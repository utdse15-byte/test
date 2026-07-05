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

import hashlib
import json
from pathlib import Path
from typing import Any

HASH_PREFIX = "sha256:"
MANUAL_HASH = "manual"


def canonical_json(value: Any) -> str:
    """Deterministic JSON serialization: sorted keys, compact, non-ASCII kept."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def hash_value(value: Any) -> str:
    """Hash any JSON-serializable value (used by value-hash locks and spec_hash)."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return HASH_PREFIX + digest


def hash_text(text: str) -> str:
    return HASH_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Content hash of a file (used for media cache keys). Streams in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return HASH_PREFIX + h.hexdigest()


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
