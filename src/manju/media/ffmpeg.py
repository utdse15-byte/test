"""Thin, logged FFmpeg subprocess wrapper (§7).

Every command is spawned as an argv list (never ``shell=True``) so Chinese and
Windows paths survive untouched (§14). Every command can be reproduced from the
logs: ``run_ffmpeg`` shell-quotes and logs the full command line before running
it, and re-raises FFmpeg's own stderr tail on failure.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
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
