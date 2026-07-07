"""Thin, logged FFmpeg subprocess wrapper (§7).

Every command is spawned as an argv list (never ``shell=True``) so Chinese and
Windows paths survive untouched (§14). Every command can be reproduced from the
logs: ``run_ffmpeg`` shell-quotes and logs the full command line before running
it, and re-raises FFmpeg's own stderr tail on failure.

This module also owns the media durability primitive: :func:`atomic_output`
gives ffmpeg outputs the same temp+``os.replace`` discipline that text truth
gets from ``core/yamlio.py: atomic_write_text`` — a durable destination only
ever receives a complete file.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

FFMPEG = "ffmpeg"
_STDERR_TAIL = 15


class MediaError(RuntimeError):
    """Any failure in the media pipeline (bad ffmpeg exit, unreadable probe...)."""


def _quote(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def run_ffmpeg(args: list[str], *, log: Callable[[str], None] | None = None) -> None:
    """Run ``ffmpeg -y -hide_banner -loglevel error <args>``.

    ``args`` may contain Path objects; every element is coerced with ``str()``
    so callers never have to remember to stringify paths themselves. On a
    nonzero exit a :class:`MediaError` is raised carrying the last ~15 lines of
    stderr plus the reproducible command line.
    """
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *[str(a) for a in args]]
    if log is not None:
        log(_quote(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-_STDERR_TAIL:])
        raise MediaError(
            f"ffmpeg failed (exit {proc.returncode}):\n{tail}\n--- command ---\n{_quote(cmd)}"
        )


@contextmanager
def atomic_output(dest: Path, *, must_not_exist: bool = False) -> Iterator[Path]:
    """Encode-to-temp + ``os.replace`` for durable ffmpeg outputs.

    Yields a sibling temp path for ffmpeg (or any writer) to produce, and moves
    it onto ``dest`` only if the block completes; on any failure the temp is
    unlinked (best-effort) and ``dest`` is left exactly as it was — a killed
    ffmpeg / disk-full / Ctrl-C can never leave a truncated file at a path the
    pipeline trusts. The temp name is built for the job:

    - same directory as ``dest`` → same filesystem, so ``os.replace`` is atomic;
    - the real extension stays LAST (``.final_v3.mp4.tmp-<pid>-<rand>.mp4``)
      so ffmpeg's extension-based muxer inference keeps working;
    - a leading dot keeps in-flight/leaked temps invisible to every
      name-anchored lookup (``final_v*.mp4`` globs, ``next_final_path``'s
      regex, the exact ``proxy.mp4`` / segment-key paths).

    ``must_not_exist=True`` preserves the append-only invariant for finals: if
    something occupies ``dest`` by swap time (e.g. two concurrent builds minted
    the same ``final_vN``), raise :class:`MediaError` instead of clobbering —
    the same paranoia as ``container.py: register_take``.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}{dest.suffix}")
    try:
        yield tmp
        if must_not_exist and dest.exists():
            raise MediaError(
                f"refusing to overwrite existing {dest} (append-only invariant); "
                "a concurrent build likely minted the same name"
            )
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def default_log(project_root: Path, name: str = "render") -> Callable[[str], None]:
    """A logger that appends timestamped UTF-8 lines to
    ``<project_root>/.manju/logs/<name>.log`` (directories created on demand)."""
    logs_dir = Path(project_root) / ".manju" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = logs_dir / f"{name}.log"

    def _log(line: str) -> None:
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(logfile, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {line}\n")

    return _log
