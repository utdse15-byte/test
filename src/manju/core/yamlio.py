"""YAML/JSON file IO with crash safety.

All truth files are written via temp-file + atomic rename, always UTF-8,
always pathlib — Windows and Chinese paths are first-class citizens (§14).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml


class _ManjuDumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.Dumper, value: str):  # keep multiline blocks readable
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_ManjuDumper.add_representer(str, _str_representer)


def _fsync_dir(path: Path) -> None:
    """Best-effort directory-entry fsync (round W, #24 — crash safety).

    Fsync-ing the FILE (already done below) guarantees the file's own bytes
    are durable, but the directory ENTRY ``os.replace`` just repointed is a
    separate piece of filesystem metadata — on a crash/power-loss right
    after the rename, a directory that was never fsynced can still forget
    the entry ever moved, leaving "crash-safe" a claim the write did not
    fully back up. This closes that gap.

    POSIX-only in practice and deliberately narrow: opening a directory as a
    file (to get an fd to fsync) is a POSIX idiom Windows has no equivalent
    for, and a few filesystems (some network mounts) reject it outright.
    Either failure degrades SILENTLY — the file itself is already safely on
    disk by this point; losing the directory-fsync guarantee on an
    unsupported platform must never turn a successful write into an error.
    """
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, flags)
    except OSError:
        return  # platform/filesystem cannot open a directory fd — degrade
    try:
        os.fsync(fd)
    except OSError:
        pass  # dir fsync unsupported here (e.g. some network mounts) — degrade
    finally:
        os.close(fd)


# W1 (§3.3): the ONE transient Windows failure worth riding out at the replace
# step — ERROR_SHARING_VIOLATION (32) / ERROR_LOCK_VIOLATION (33), i.e. an AV
# scanner or indexer briefly holding the TARGET open. Bounded and narrow on
# purpose: access denied (5), disk full, invalid path raise on attempt one
# (retrying those masks real, permanent misconfiguration), and after the last
# attempt the original error propagates — with the OLD file intact either way.
_RETRYABLE_WINERROR = frozenset({32, 33})
_REPLACE_ATTEMPTS = 8  # ~0.7s worst case with the capped backoff below


def replace_with_retry(src: str | Path, dst: str | Path) -> None:
    """``os.replace`` with the §3.3 bounded Windows sharing-violation retry.

    Shared by every replace-style atomic writer (this module's text helper and
    ``media/ffmpeg.atomic_output``) so the Windows semantics can never drift
    apart between the text and media halves of the write story. On POSIX the
    ``winerror`` attribute is always ``None`` → single attempt, byte-identical
    behaviour to a bare ``os.replace``.
    """
    delay = 0.02
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            transient = getattr(exc, "winerror", None) in _RETRYABLE_WINERROR
            if not transient or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.15)


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, path)
        _fsync_dir(path.parent)  # #24: the rename's directory entry, not just the bytes
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# 战役③ (2026-07-31): a no-op build on a REAL 80-shot film spent 13.6s in
# 3044 read_yaml calls — the same truth parsed ~38× per shot. This is the
# ONE yaml-reading owner, so the parse cache lives here and every caller
# benefits. Semantics are byte-honest: the file's bytes are ALWAYS re-read
# (any content change reparses immediately — no mtime heuristics, so a
# same-size same-instant rewrite can never serve stale truth), only the
# PARSE is skipped on identical bytes, and every return is a fresh deep
# copy so one caller's mutation can never leak into another's read.
_PARSE_CACHE: dict[str, tuple[bytes, Any]] = {}
_PARSE_CACHE_MAX = 4096  # bounded for long-lived GUI processes


def read_yaml(path: Path) -> Any:
    import copy

    key = os.fspath(path)
    with open(path, "rb") as f:
        raw = f.read()
    hit = _PARSE_CACHE.get(key)
    if hit is not None and hit[0] == raw:
        return copy.deepcopy(hit[1])
    data = yaml.safe_load(raw.decode("utf-8"))
    if len(_PARSE_CACHE) >= _PARSE_CACHE_MAX:
        _PARSE_CACHE.clear()
    _PARSE_CACHE[key] = (raw, data)
    return copy.deepcopy(data)


def dump_yaml(data: Any) -> str:
    """Serialize to the project's canonical YAML string (no file IO)."""
    return yaml.dump(
        data,
        Dumper=_ManjuDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )


def write_yaml(path: Path, data: Any) -> None:
    atomic_write_text(Path(path), dump_yaml(data))


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any, indent: int = 2) -> None:
    atomic_write_text(Path(path), json.dumps(data, ensure_ascii=False, indent=indent) + "\n")
