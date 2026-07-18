"""Canonical hashing — the single source of truth for spec_hash, value locks,
and cache keys.

Rules:
- Canonical form = JSON with recursively sorted keys, compact separators,
  ensure_ascii=False, encoded as UTF-8.
- Hashes are rendered as "sha256:<hex>" everywhere they appear in text files.
- MANUAL_HASH is the sentinel spec_hash for human-imported takes: they are
  never automatically invalidated (§4.3).
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

HASH_PREFIX = "sha256:"
MANUAL_HASH = "manual"

# --- process-scoped hash_file memo (audit Tier-1 #3) --------------------------
# Real builds hash each source at least twice: media/render.py computes every
# segment key in _final_key_payload (each _segment_cache_key hashes the source),
# then _build_segment recomputes the identical key; build/graph.py hashes each
# animatic still twice. SHA-256 is CPU-bound (~370 MB/s measured), so the memo
# below collapses EVERY such double-hash at one seam. Keyed on
# (resolved absolute path, st_size, st_mtime_ns) — see hash_file for the exact
# correctness / staleness / thread-safety contract.
_HASH_CACHE_MAXSIZE = 4096
_hash_cache: "OrderedDict[tuple[str, int, int], str]" = OrderedDict()
_hash_cache_lock = threading.Lock()
# Env kill-switch: MANJU_NO_HASH_CACHE=1 bypasses the memo entirely (per call).
_HASH_CACHE_KILL_ENV = "MANJU_NO_HASH_CACHE"


def _reset_hash_cache() -> None:
    """Drop every memo entry (test hygiene only; not part of the public API)."""
    with _hash_cache_lock:
        _hash_cache.clear()


def _json_default(o: Any) -> str:
    """Serialize the non-JSON scalars that ``yaml.safe_load`` routinely produces
    from truth files. An unquoted YAML date/time (``aired: 2026-07-18``) parses
    to a ``datetime.date``/``datetime`` which ``json.dumps`` cannot serialize —
    a value-hash lock over such a field used to raise an uncaught ``TypeError``
    and crash ``run_check`` (and thus every build). ISO-8601 is deterministic
    and stable, so the lock now hashes reproducibly. Only reached for otherwise
    unserializable values, so every currently-hashable value is byte-identical."""
    if isinstance(o, (_datetime.date, _datetime.time)):  # date covers datetime
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def canonical_json(value: Any) -> str:
    """Deterministic JSON serialization: sorted keys, compact, non-ASCII kept."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=_json_default)


def hash_value(value: Any) -> str:
    """Hash any JSON-serializable value (used by value-hash locks and spec_hash)."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return HASH_PREFIX + digest


def hash_text(text: str) -> str:
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_file_stream(path: Path, chunk_size: int) -> str:
    """The uncached content hash — streams the whole file in chunks.

    This is verbatim the historical ``hash_file`` body, so the digest is
    byte-identical and a missing file raises ``FileNotFoundError`` from ``open``
    exactly as before. Every not-memoized code path (kill-switch, failed stat)
    routes here."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return HASH_PREFIX + h.hexdigest()


def hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Content hash of a file (used for media cache keys). Streams in chunks.

    A process-scoped memo (audit Tier-1 #3) keyed on
    ``(resolved absolute path, st_size, st_mtime_ns)`` returns the cached digest
    when a file is hashed again unchanged, collapsing the render/graph
    double-hash (~370 MB/s SHA-256 is CPU-bound). The returned digest is
    byte-identical to the uncached hash, and this function's signature and error
    behaviour are unchanged: on every call the file is stat-ed; a failed stat
    (e.g. a missing file) falls through to the streaming path, which raises
    exactly as before.

    Correctness / staleness: a hit requires BOTH ``st_size`` AND ``st_mtime_ns``
    to match the cached stat. The one theoretical staleness window is a
    same-size rewrite that lands within a single mtime tick (identical
    ``st_mtime_ns``): the memo would then return the prior (now stale) digest.
    This is acceptable here because manju runs on ns-resolution filesystems
    (distinct mtime_ns per write in practice), manju media is append-only (new
    bytes take a new path / grow the size), and truth files are small and cheap
    to re-hash anyway — the window is not engineered away, it is bounded and
    documented. ``MANJU_NO_HASH_CACHE=1`` bypasses the memo entirely for any
    caller that cannot tolerate even the theoretical window.

    Memory is bounded by an LRU cap (``_HASH_CACHE_MAXSIZE``, 4096 entries) so a
    long GUI session cannot grow the memo without bound. Thread-safety: gui/jobs
    and servers hash on threads, so the ``OrderedDict`` is guarded by a lock for
    every read/promote and write/evict; the SHA-256 itself is computed OUTSIDE
    the lock, so concurrent misses on large files never serialize on the CPU
    (two threads may redundantly hash the same new file — a benign, idempotent
    double-compute — but the dict is never mutated unlocked)."""
    # Kill-switch checked per call (cheap dict lookup) — full bypass, no stat.
    if os.environ.get(_HASH_CACHE_KILL_ENV) == "1":
        return _hash_file_stream(path, chunk_size)

    try:
        st = os.stat(path)
    except OSError:
        # A failed stat (missing/unstattable) falls through to the streaming
        # path, which reproduces the original error exactly (open raises).
        return _hash_file_stream(path, chunk_size)

    key = (os.path.realpath(os.fspath(path)), st.st_size, st.st_mtime_ns)
    with _hash_cache_lock:
        cached = _hash_cache.get(key)
        if cached is not None:
            _hash_cache.move_to_end(key)  # promote: most-recently used
            return cached

    # Miss: hash OUTSIDE the lock (CPU-bound; must not serialize other threads).
    digest = _hash_file_stream(path, chunk_size)

    with _hash_cache_lock:
        _hash_cache[key] = digest
        _hash_cache.move_to_end(key)
        while len(_hash_cache) > _HASH_CACHE_MAXSIZE:
            _hash_cache.popitem(last=False)  # evict least-recently used
    return digest


def cache_key(*parts: Any) -> str:
    """Combine several hashable parts into one cache key (segment cache, etc.)."""
    return hash_value(list(parts))


def short_hash(full: str, n: int = 16) -> str:
    """Filesystem-friendly shortened hash for cache file names."""
    return full.removeprefix(HASH_PREFIX)[:n]


def get_by_path(data: dict[str, Any], dotted: str) -> Any:
    """Resolve a dotted path like 'dialogue.text' inside nested dicts.

    Raises KeyError when the path does not exist — a locked field that
    disappeared must be treated as a violation, not as a silent pass.
    """
    node: Any = data
    for part in dotted.split("."):
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                raise KeyError(f"path '{dotted}' not found (missing '{part}')")
            node = node[part]
        else:
            raise KeyError(f"path '{dotted}' not found (hit non-container at '{part}')")
    return node
