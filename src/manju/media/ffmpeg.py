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
from typing import Any

FFMPEG = "ffmpeg"
_STDERR_TAIL = 15
_ARGV_HEAD = 12  # how many argv tokens to keep as evidence (the -i/-vf head)


class MediaError(RuntimeError):
    """Any failure in the media pipeline (bad ffmpeg exit, unreadable probe...)."""


def _quote(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def run_ffmpeg(
    args: list[str],
    *,
    log: Callable[[str], None] | None = None,
    project: Any = None,
    subject: str | None = None,
    step: str = "render",
    log_name: str = "render",
) -> None:
    """Run ``ffmpeg -y -hide_banner -loglevel error <args>``.

    ``args`` may contain Path objects; every element is coerced with ``str()``
    so callers never have to remember to stringify paths themselves. On a
    nonzero exit a :class:`MediaError` is raised carrying the last ~15 lines of
    stderr plus the reproducible command line.

    Debuggability (goal 10): when ``project`` (a Project or a bare root path) is
    supplied, a failure is ALSO recorded as a structured :class:`Failure` before
    the SAME :class:`MediaError` is raised — evidence is the ffmpeg stderr tail
    plus the argv head (the ``-i/-vf`` shape that usually explains the exit), and
    ``log_path`` points at the fuller ``default_log`` file. Recording is
    best-effort and never changes the exception type or its one-line message
    (existing callers and their tests are untouched).
    """
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *[str(a) for a in args]]
    if log is not None:
        log(_quote(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-_STDERR_TAIL:])
        if project is not None:
            _record_ffmpeg_failure(project, cmd, proc.returncode, tail,
                                   subject=subject, step=step, log_name=log_name)
        raise MediaError(
            f"ffmpeg failed (exit {proc.returncode}):\n{tail}\n--- command ---\n{_quote(cmd)}"
        )


def _record_ffmpeg_failure(project: Any, cmd: list[str], returncode: int, tail: str,
                           *, subject: str | None, step: str, log_name: str) -> None:
    """Turn a nonzero ffmpeg exit into a structured Failure. Best-effort: a
    recording hiccup must never mask the real MediaError the caller expects."""
    try:
        from ..core.failures import Failure, record_failure

        argv_head = _quote(cmd[:_ARGV_HEAD])
        if len(cmd) > _ARGV_HEAD:
            argv_head += " …"
        evidence = f"$ {argv_head}\n{tail}" if tail else f"$ {argv_head}"
        record_failure(
            project,
            Failure(
                step=step,
                subject=subject or "final",
                cause=f"ffmpeg exited {returncode}",
                evidence=evidence,
                hint=f"复现单条命令见 .manju/logs/{log_name}.log;核对滤镜/输入路径",
                log_path=f".manju/logs/{log_name}.log",
                detail={"returncode": returncode},
            ),
        )
    except Exception:
        pass


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
