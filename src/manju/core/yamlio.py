"""YAML/JSON file IO with crash safety.

All truth files are written via temp-file + atomic rename, always UTF-8,
always pathlib — Windows and Chinese paths are first-class citizens (§14).
"""

from __future__ import annotations

import json
import os
import tempfile
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


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)  # #24: the rename's directory entry, not just the bytes
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
