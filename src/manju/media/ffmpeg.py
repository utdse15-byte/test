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

import contextvars
import os
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.yamlio import replace_with_retry

FFMPEG = "ffmpeg"
_STDERR_TAIL = 15
_ARGV_HEAD = 12  # how many argv tokens to keep as evidence (the -i/-vf head)
_CANCEL_POLL_S = 0.2  # how often the cancel-aware Popen loop checks in
_CANCEL_GRACE_S = 5.0  # SIGTERM -> SIGKILL grace period on a cancel trip

# Cooperative cancellation (goal: honest job cancellation). ``run_ffmpeg``
# checks an explicit ``cancel_event`` kwarg first; failing that, this ambient
# contextvar — set via :func:`cancel_scope` — so a caller several layers up
# (build/graph.py's render phase) can make EVERY run_ffmpeg call made
# transitively within a ``with`` block cancel-aware WITHOUT media/render.py
# needing a single line changed (it stays a read-only surface this round —
# its whole segment/boundary/final pipeline funnels through this one
# function). A contextvar is correct here because the call chain for one
# build's render step runs on ONE thread (the GUI JobRunner's single worker
# thread, or a CLI's main thread) — no cross-thread propagation is needed.
# Default ``None`` everywhere keeps the byte-identical ``subprocess.run`` path.
_CANCEL_CHECK: "contextvars.ContextVar[Callable[[], bool] | None]" = contextvars.ContextVar(
    "manju_ffmpeg_cancel_check", default=None
)

# Timeouts (goal W, #55): every ffmpeg call gets SOME timeout — a corrupt
# input, a network-mounted path or a stuck filter must never hang check/
# render/package forever. Renders can legitimately run long (many shots, high
# resolution, complex filter graphs), so the default here is deliberately
# generous rather than short. Override per call (``timeout=``) or globally via
# MANJU_FFMPEG_TIMEOUT_S (seconds; <=0 disables the cap, for the rare
# legitimately-unbounded job).
DEFAULT_FFMPEG_TIMEOUT_S = 7200.0  # 2h
_UNSET = object()  # sentinel: "caller did not override" vs. an explicit None (no cap)


class MediaError(RuntimeError):
    """Any failure in the media pipeline (bad ffmpeg exit, unreadable probe...)."""


class MediaCanceled(MediaError):
    """``run_ffmpeg`` killed its own subprocess because a cancel check tripped
    (goal: honest job cancellation) — distinguishable from every other
    :class:`MediaError` (bad exit, timeout, missing binary) so a caller can
    tell "the user canceled" from "ffmpeg actually failed". The process is
    confirmed dead (SIGTERM, then SIGKILL after a grace period) before this
    is raised — nothing is left running."""


@contextmanager
def cancel_scope(should_cancel: "Callable[[], bool] | None") -> Iterator[None]:
    """Make ``should_cancel`` the ambient cancel check for every
    :func:`run_ffmpeg` call made (directly or transitively) within this
    ``with`` block. See the ``_CANCEL_CHECK`` module note for why this exists.
    ``should_cancel=None`` is a genuine no-op (the byte-identical default
    ``subprocess.run`` path stays in effect for every call inside)."""
    token = _CANCEL_CHECK.set(should_cancel)
    try:
        yield
    finally:
        _CANCEL_CHECK.reset(token)


def _quote(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def _default_ffmpeg_timeout() -> float | None:
    override = os.environ.get("MANJU_FFMPEG_TIMEOUT_S")
    if override:
        try:
            value = float(override)
        except ValueError:
            return DEFAULT_FFMPEG_TIMEOUT_S
        return value if value > 0 else None
    return DEFAULT_FFMPEG_TIMEOUT_S


def _not_found_error(project: Any, step: str, subject: str | None, cmd: list[str]) -> MediaError:
    # ffmpeg not on PATH — the #1 first-run blocker. Rewrite the raw
    # OSError traceback into the what/why/how-to-fix triple (clig.dev:
    # "catch errors and rewrite them for humans"), and record it too.
    msg = (
        "ffmpeg 没找到 (ffmpeg not on PATH) — Manju 靠 ffmpeg 合成与转码,"
        "缺了它无法出片。安装后重试:macOS `brew install ffmpeg`;"
        "Debian/Ubuntu `apt install ffmpeg`;Windows 从 ffmpeg.org 下载并加入 PATH。"
        "装好用 `manju doctor` 复检。"
    )
    if project is not None:
        try:
            from ..core.failures import Failure, record_failure

            record_failure(project, Failure(
                step=step, subject=subject or "ffmpeg", cause="ffmpeg 未安装或不在 PATH 上",
                evidence=_quote(cmd[:1]), hint="安装 ffmpeg 后 `manju doctor` 复检"))
        except Exception:
            pass
    return MediaError(msg)


def _timeout_error(project: Any, step: str, subject: str | None, cmd: list[str],
                   timeout: float | None) -> MediaError:
    # #55: a stuck filter / corrupt input / network-mounted path must not
    # hang forever — kill it and surface a structured, actionable error.
    msg = (
        f"ffmpeg 超时(>{timeout}s)— 可能是损坏媒体、网络挂载路径或卡住的 filter "
        f"(goal W/#55)。命令: {_quote(cmd)}"
    )
    if project is not None:
        try:
            from ..core.failures import Failure, record_failure

            record_failure(project, Failure(
                step=step, subject=subject or "ffmpeg",
                cause=f"ffmpeg 超时(>{timeout}s)",
                evidence=_quote(cmd[:_ARGV_HEAD]),
                hint="检查输入媒体是否损坏/挂载是否可达;必要时调大 "
                     "MANJU_FFMPEG_TIMEOUT_S 或传 run_ffmpeg(timeout=...)"))
        except Exception:
            pass
    return MediaError(msg)


def _canceled_error(project: Any, step: str, subject: str | None, cmd: list[str]) -> MediaCanceled:
    msg = f"已取消:ffmpeg 进程已终止(canceled — subprocess killed)。命令: {_quote(cmd)}"
    if project is not None:
        try:
            from ..core.failures import Failure, record_failure

            record_failure(project, Failure(
                step=step, subject=subject or "ffmpeg",
                cause="用户取消,ffmpeg 子进程已终止",
                evidence=_quote(cmd[:_ARGV_HEAD]),
                hint="部分产物已作为内容寻址缓存保留,可复用(§3);重新构建会跳过已完成部分",
                level="info", actor="engine"))
        except Exception:
            pass
    return MediaCanceled(msg)


def _raise_on_bad_exit(project: Any, cmd: list[str], returncode: int, stderr: str | None, *,
                       subject: str | None, step: str, log_name: str) -> None:
    tail = "\n".join((stderr or "").strip().splitlines()[-_STDERR_TAIL:])
    if project is not None:
        _record_ffmpeg_failure(project, cmd, returncode, tail,
                               subject=subject, step=step, log_name=log_name)
    raise MediaError(
        f"ffmpeg failed (exit {returncode}):\n{tail}\n--- command ---\n{_quote(cmd)}"
    )


def _run_ffmpeg_default(cmd: list[str], *, project: Any, subject: str | None, step: str,
                        log_name: str, timeout: float | None) -> None:
    """The original, byte-identical blocking path: ``subprocess.run`` end to
    end. Used whenever no cancel check (explicit or ambient) is active."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise _not_found_error(project, step, subject, cmd) from None
    except subprocess.TimeoutExpired as exc:
        raise _timeout_error(project, step, subject, cmd, timeout) from exc
    if proc.returncode != 0:
        _raise_on_bad_exit(project, cmd, proc.returncode, proc.stderr,
                           subject=subject, step=step, log_name=log_name)


def _run_ffmpeg_cancelable(cmd: list[str], *, project: Any, subject: str | None, step: str,
                           log_name: str, timeout: float | None,
                           check: Callable[[], bool]) -> None:
    """Popen + short-interval poll loop (goal: honest job cancellation) — used
    ONLY when a cancel check is active (explicit ``cancel_event`` or the
    ambient :func:`cancel_scope`). A trip terminates the subprocess (SIGTERM,
    then SIGKILL after a grace period) and raises :class:`MediaCanceled`
    instead of blocking until ffmpeg exits on its own."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise _not_found_error(project, step, subject, cmd) from None
    start = time.monotonic()
    stderr = ""
    while True:
        try:
            # Gate round 3: poll wait() — a bare WaitForSingleObject/waitpid
            # with no pipe machinery. The old communicate(timeout=…) poll
            # never yielded on the real Windows host (both cancel tests took
            # the encode's FULL duration, twice, deterministically — the
            # reader-thread machinery swallowed the poll interval), so the
            # cancel check only ran when ffmpeg finished by itself. Pipes are
            # drained ONCE after death; -loglevel error keeps stderr far
            # below the pipe buffer while alive, and a pathologically spewing
            # ffmpeg stalls itself until the overall timeout kill fires.
            proc.wait(timeout=_CANCEL_POLL_S)
            _stdout, stderr = proc.communicate()
            break
        except subprocess.TimeoutExpired:
            if check():
                proc.terminate()
                try:
                    proc.communicate(timeout=_CANCEL_GRACE_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                raise _canceled_error(project, step, subject, cmd) from None
            if timeout is not None and (time.monotonic() - start) > timeout:
                proc.kill()
                proc.communicate()
                raise _timeout_error(project, step, subject, cmd, timeout) from None
    if proc.returncode != 0:
        _raise_on_bad_exit(project, cmd, proc.returncode, stderr,
                           subject=subject, step=step, log_name=log_name)


def run_ffmpeg(
    args: list[str],
    *,
    log: Callable[[str], None] | None = None,
    project: Any = None,
    subject: str | None = None,
    step: str = "render",
    log_name: str = "render",
    timeout: float | None = _UNSET,  # type: ignore[assignment]
    cancel_event: "Any | None" = None,  # a threading.Event (duck-typed: .is_set())
) -> None:
    """Run ``ffmpeg -y -hide_banner -loglevel error <args>``.

    ``args`` may contain Path objects; every element is coerced with ``str()``
    so callers never have to remember to stringify paths themselves. On a
    nonzero exit a :class:`MediaError` is raised carrying the last ~15 lines of
    stderr plus the reproducible command line.

    ``timeout`` (goal W, #55): seconds before the subprocess is killed and a
    structured :class:`MediaError` raised — defaults to
    :data:`DEFAULT_FFMPEG_TIMEOUT_S` (generous; renders can legitimately run
    long). Pass an explicit smaller value for short-lived calls (frame
    extraction, thumbnails) or ``None`` to disable the cap outright.

    ``cancel_event`` (goal: honest job cancellation): a ``threading.Event``
    (or anything with ``.is_set()``) checked every ~0.2s while ffmpeg runs —
    a trip terminates the subprocess and raises :class:`MediaCanceled`
    instead of blocking until it exits. When omitted, the AMBIENT check set
    by :func:`cancel_scope` (if any) is used instead. With NEITHER present —
    every call site that does not opt in, which is most of them — this is the
    ORIGINAL blocking ``subprocess.run`` path, byte-identical to before.

    Debuggability (goal 10): when ``project`` (a Project or a bare root path) is
    supplied, a failure is ALSO recorded as a structured :class:`Failure` before
    the SAME :class:`MediaError` is raised — evidence is the ffmpeg stderr tail
    plus the argv head (the ``-i/-vf`` shape that usually explains the exit), and
    ``log_path`` points at the fuller ``default_log`` file. Recording is
    best-effort and never changes the exception type or its one-line message
    (existing callers and their tests are untouched).
    """
    if timeout is _UNSET:
        timeout = _default_ffmpeg_timeout()
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *[str(a) for a in args]]
    if log is not None:
        log(_quote(cmd))
    check = cancel_event.is_set if cancel_event is not None else _CANCEL_CHECK.get()
    if check is None:
        _run_ffmpeg_default(cmd, project=project, subject=subject, step=step,
                            log_name=log_name, timeout=timeout)
    else:
        _run_ffmpeg_cancelable(cmd, project=project, subject=subject, step=step,
                               log_name=log_name, timeout=timeout, check=check)


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
        _fsync_file(tmp)  # W1 §3.3: the producer (ffmpeg) never fsyncs its output
        replace_with_retry(tmp, dest)  # W1 §3.3: bounded Windows sharing-violation ride-out
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _fsync_file(path: Path) -> None:
    """Best-effort fsync of a finished media temp before the swap — the media
    twin of ``yamlio.atomic_write_text``'s file fsync. Silent degrade on
    filesystems that refuse (the replace still lands; only the power-loss
    durability guarantee narrows, matching ``yamlio._fsync_dir``'s stance)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


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
