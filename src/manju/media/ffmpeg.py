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
import functools
import os
import re
import shlex
import shutil
import signal
import tempfile
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.yamlio import replace_with_retry

# Gate round 3: os.name is process-constant; a module flag keeps the Windows
# branch below patchable in tests without touching the global ``os`` module.
_IS_WINDOWS = os.name == "nt"


def _taskkill_tree(proc: "subprocess.Popen") -> bool:
    """Windows cancel/timeout kill must take the whole TREE, and must do it
    while the direct child is still alive: Chocolatey installs ffmpeg as a
    SHIM that spawns the real encoder as a child — TerminateProcess on the
    shim alone leaves the encoder running with the pipes open (gate run #5:
    every cancel took the encode's FULL duration). ``taskkill /T`` walks the
    tree from the live shim; killing the shim first would orphan the encoder
    beyond /T's reach, so call this BEFORE terminate()."""
    try:
        result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=10, check=False)
        return result.returncode == 0
    except Exception:
        return False  # caller still reaps the direct process and reports uncertainty

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
    """Local work stopped. Completed registered takes survive cancellation.

    This exception makes no assertion about a remote task or its billing.
    Unconfirmed local teardown raises MediaCleanupError instead.
    """

    def __init__(self, message: str, *, completed_takes=()):
        super().__init__(message)
        self.completed_takes = tuple(completed_takes)


class MediaCleanupError(MediaError):
    """Owned process teardown could not be confirmed; never a successful cancel."""


def check_canceled(check: Callable[[], bool] | None = None) -> None:
    check = check if check is not None else _CANCEL_CHECK.get()
    if check is not None and check():
        raise MediaCanceled("已取消:尚未开始下一项本地操作")


@contextmanager
def cancel_scope(should_cancel: "Callable[[], bool] | None") -> Iterator[None]:
    """Make ``should_cancel`` the ambient cancel check for every
    :func:`run_ffmpeg` call made (directly or transitively) within this
    ``with`` block. See the ``_CANCEL_CHECK`` module note for why this exists.
    ``should_cancel=None`` is a genuine no-op (the byte-identical default
    ``subprocess.run`` path stays in effect for every call inside)."""
    if should_cancel is None:
        yield
        return
    token = _CANCEL_CHECK.set(should_cancel)
    try:
        yield
    finally:
        _CANCEL_CHECK.reset(token)


def _quote(cmd: list[str]) -> str:
    """The reproducible command line for logs and failure evidence — this
    module's docstring PROMISES every command re-runs from the logs, and that
    only holds if the quoting matches the OWNER'S shell. POSIX: shlex.quote,
    byte-identical to the historical output. Windows (UX audit F34):
    ``subprocess.list2cmdline`` — the exact string CreateProcess receives,
    paste-able into both cmd.exe and PowerShell (a single-quoted C:\\ path is
    a literal in cmd.exe and misfires in PowerShell)."""
    if _IS_WINDOWS:
        return subprocess.list2cmdline(cmd)
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
    # UX audit F10: the Windows line used to send the owner to ffmpeg.org —
    # today that is an 8.x build, the EXACT skew class the gate pins 6.1.1
    # against (run #1: ffprobe-8 colour-tag drift vs the verified suite).
    # Steer to the same pinned version CI verifies.
    msg = (
        "ffmpeg 没找到 (ffmpeg not on PATH) — Manju 靠 ffmpeg 合成与转码,"
        "缺了它无法出片。安装后重试:macOS `brew install ffmpeg`;"
        "Debian/Ubuntu `apt install ffmpeg`;Windows `choco install ffmpeg "
        "--version=6.1.1`(与验证套件同版 — ffmpeg.org 的 8.x 会偏移 ffprobe "
        "色彩标签)。装好用 `manju doctor` 复检。"
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
                hint="已注册候选与已完成缓存保留;未完成的临时输出不会发布",
                level="info", actor="engine"))
        except Exception:
            pass
    return MediaCanceled(msg)


def _raise_on_bad_exit(project: Any, cmd: list[str], returncode: int, stderr: str | None, *,
                       subject: str | None, step: str, log_name: str) -> None:
    tail = "\n".join((stderr or "").strip().splitlines()[-_STDERR_TAIL:])
    try:
        note = known_failure_advisory(tail)
    except Exception:  # the steer may never mask the real error
        note = None
    if project is not None:
        _record_ffmpeg_failure(project, cmd, returncode, tail,
                               subject=subject, step=step, log_name=log_name,
                               known_note=note)
    raise MediaError(
        f"ffmpeg failed (exit {returncode}):\n{tail}\n--- command ---\n{_quote(cmd)}"
        + (f"\n{note}" if note else "")
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


def _signal_local_group(proc: subprocess.Popen, sig: int) -> None:
    # Only for children we create with start_new_session=True, never arbitrary PIDs.
    if not _IS_WINDOWS:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass


def _stop_local_process(proc: subprocess.Popen) -> None:
    """Bounded tree signalling and direct-child reaping, preserving failures."""
    try:
        tree_confirmed = True
        if _IS_WINDOWS:
            tree_confirmed = _taskkill_tree(proc)  # before killing a Windows shim
        else:
            _signal_local_group(proc, signal.SIGTERM)
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=_CANCEL_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=_CANCEL_GRACE_S)
        finally:
            # The group may outlive its leader. Stop remaining group members too.
            if not _IS_WINDOWS:
                _signal_local_group(proc, getattr(signal, "SIGKILL", signal.SIGTERM))
        if tree_confirmed is False or proc.returncode is None:
            raise MediaCleanupError("本地进程退出状态未确认;不能报告取消成功")
    except MediaCleanupError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaCleanupError("本地进程清理未确认;请检查仍在运行的任务") from exc


def _output_tail(stream, limit: int = 65536) -> str:
    stream.seek(0, os.SEEK_END)
    stream.seek(max(0, stream.tell() - limit))
    return stream.read().decode("utf-8", errors="replace")


def _run_local_process(cmd: list[str], *, timeout: float | None = None,
                       check: Callable[[], bool] | None = None,
                       output_limit: int = 32 * 1024 * 1024) -> subprocess.CompletedProcess:
    """Own one local process. No pipes, shell, unbounded waits or reader threads.

    Log capture uses temporary files with a polled size limit. Bursts can exceed
    the limit between polls; this is not a hard disk quota. POSIX children have
    a private session. Windows uses the existing taskkill tree mechanism.
    """
    check = check if check is not None else _CANCEL_CHECK.get()
    check_canceled(check)
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(
            cmd, stdout=stdout, stderr=stderr,
            start_new_session=not _IS_WINDOWS,
        )
        start = time.monotonic()
        try:
            while True:
                check_canceled(check)
                if timeout is not None and time.monotonic() - start > timeout:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if any(os.fstat(f.fileno()).st_size > output_limit for f in (stdout, stderr)):
                    raise MediaError("本地进程输出超过限制,已终止操作")
                try:
                    proc.wait(timeout=_CANCEL_POLL_S)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if any(os.fstat(f.fileno()).st_size > output_limit for f in (stdout, stderr)):
                raise MediaError("本地进程输出超过限制,已终止操作")
            return subprocess.CompletedProcess(
                cmd, proc.returncode, _output_tail(stdout), _output_tail(stderr))
        except BaseException:
            _stop_local_process(proc)
            raise


def _run_ffmpeg_cancelable(cmd: list[str], *, project: Any, subject: str | None, step: str,
                           log_name: str, timeout: float | None,
                           check: Callable[[], bool]) -> None:
    try:
        proc = _run_local_process(cmd, timeout=timeout, check=check)
    except FileNotFoundError:
        raise _not_found_error(project, step, subject, cmd) from None
    except MediaCanceled:
        raise _canceled_error(project, step, subject, cmd) from None
    except subprocess.TimeoutExpired:
        raise _timeout_error(project, step, subject, cmd, timeout) from None
    except OSError as exc:
        raise MediaError(f"本地媒体进程或日志读写失败: {exc}") from exc
    if proc.returncode != 0:
        _raise_on_bad_exit(project, cmd, proc.returncode, proc.stderr,
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
    (or anything with ``.is_set()``) checked before dispatch and every ~0.2s while ffmpeg runs —
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
                           *, subject: str | None, step: str, log_name: str,
                           known_note: str | None = None) -> None:
    """Turn a nonzero ffmpeg exit into a structured Failure. Best-effort: a
    recording hiccup must never mask the real MediaError the caller expects."""
    try:
        from ..core.failures import Failure, record_failure

        argv_head = _quote(cmd[:_ARGV_HEAD])
        if len(cmd) > _ARGV_HEAD:
            argv_head += " …"
        evidence = f"$ {argv_head}\n{tail}" if tail else f"$ {argv_head}"
        hint = f"复现单条命令见 .manju/logs/{log_name}.log;核对滤镜/输入路径"
        if known_note:
            hint = known_note  # the recorded-regression steer outranks generic advice
        record_failure(
            project,
            Failure(
                step=step,
                subject=subject or "final",
                cause=f"ffmpeg exited {returncode}",
                evidence=evidence,
                hint=hint,
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

# ===================================================== W5.4 encoder inventory
# "What can THIS ffmpeg encode with" is this module's business (it owns every
# ffmpeg invocation). core/toolchain delegates its manifest fact block here
# (the _font_inventory→media/card precedent), and doctor reads the same owner
# — never a second parser of `-encoders` anywhere.

# The h264 HARDWARE encoder candidates on Windows (nvenc = NVIDIA, qsv = Intel
# QuickSync, amf = AMD). h264 only — the pipeline's segment/final profile is
# h264; other codecs are out of scope. Sorted, deterministic.
HW_ENCODER_CANDIDATES = ("h264_amf", "h264_nvenc", "h264_qsv")

# 死线家族第五员 (2026-07-31): the fixed 15s wrapped a REAL `ffmpeg
# -encoders` subprocess and fired once in a full `-n auto` run with extra
# field-drill load beside it (isolation-green; 12 pure-CPU spinners slowed
# the probe 3× but did not breach — the field mix of 12 render-heavy
# workers was heavier). Same fingerprint as the four recorded firings:
# failure-detection latency, not an assertion — raised, nothing else moves.
_ENCODERS_TIMEOUT_S = 120.0


def _encoder_inventory_uncached() -> dict[str, bool]:
    """Presence FACTS for the encode profile's encoder family: parse
    ``ffmpeg -encoders`` into ``{name: listed}`` over libx264 (the software
    floor) + :data:`HW_ENCODER_CANDIDATES`. Absence is a fact, never an
    error; a missing/broken ffmpeg answers all-absent. NOTE: "listed" is a
    build fact only — a listed hw encoder can still fail at runtime without
    its driver (that is why eligibility below never says VERIFIED)."""
    import shutil

    names = ("libx264", *HW_ENCODER_CANDIDATES)
    exe = shutil.which("ffmpeg")
    if not exe:
        return {n: False for n in names}
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-encoders"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_ENCODERS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return {n: False for n in names}
    listed: set[str] = set()
    for line in (out.stdout or "").splitlines():
        parts = line.split()
        # encoder rows: " V....D libx264  H.264 ..." — a flags column then the
        # name; the legend's "V..... = Video" yields name "=" which can never
        # match a real candidate, so no special-casing is needed.
        if len(parts) >= 2 and parts[0] and parts[0][0] in "VAS":
            listed.add(parts[1])
    return {n: n in listed for n in names}


@functools.lru_cache(maxsize=None)
def encoder_inventory() -> dict[str, bool]:
    """Process-cached :func:`_encoder_inventory_uncached` — one subprocess per
    process, like the S4 toolchain collectors (doctor/status can both read)."""
    return _encoder_inventory_uncached()


def hw_encode_eligibility(inventory: dict[str, bool] | None = None) -> dict[str, Any]:
    """PURE eligibility predicate over an encoder inventory (W5.4).

    ELIGIBILITY ONLY — this never enables anything: the render/segment/
    normalize encode profiles stay libx264 regardless of the answer (pinned in
    tests). The honest ladder: ``NONE_LISTED`` (no hw encoder in this ffmpeg)
    or ``LISTED`` (ffmpeg lists one — which is NOT verification: a listed
    encoder still fails at runtime without its driver; VERIFIED would require
    a real canary encode, deferred until something would consume it)."""
    inv = encoder_inventory() if inventory is None else inventory
    candidates = sorted(n for n in HW_ENCODER_CANDIDATES if inv.get(n))
    if not candidates:
        return {
            "eligible": False,
            "candidates": [],
            "level": "NONE_LISTED",
            "note": "此 ffmpeg 未编入任何 h264 硬件编码器 — 软件路径 libx264 是唯一路径",
        }
    return {
        "eligible": True,
        "candidates": candidates,
        "level": "LISTED",
        "note": "ffmpeg 列出了硬件编码器,但 LISTED ≠ VERIFIED:没有对应驱动/硬件时"
                "运行必败;真正启用前需要真实 canary 编码验证,且本版永不自动启用"
                "(编码路径仍是 libx264)",
    }


# --------------------------------------------------------- version FACTS
# 战役① (2026-07-31): the owner who accidentally upgrades ffmpeg must not
# see a clean bill of health while their transition renders die 1-in-6.
# Version is a machine FACT (this module's charge, record-only, never a
# gate); advisories exist ONLY for measured/recorded ranges — an unmeasured
# version gets stated, never judged.

# The version the verified suite runs (windows-ci.yml pins it; the
# not-found steer above names the same number).
PINNED_FFMPEG_VERSION = "6.1.1"

_ACROSSFADE_SIGNATURE = "Could not open encoder before EOF"


def _ffmpeg_version_line_uncached() -> str:
    # Delegates to the ONE -version-line owner (the S4 toolchain collector;
    # media/ sits outside that module's no-import grep-pin, and
    # media/render's cache-key seam already consumes it).
    try:
        from ..core.toolchain import cached_tool_version_line

        return cached_tool_version_line("ffmpeg")
    except Exception:
        return "missing"


@functools.lru_cache(maxsize=None)
def ffmpeg_version_line() -> str:
    """FIRST line of ``ffmpeg -version`` verbatim, or ``"missing"`` —
    process-cached like :func:`encoder_inventory`."""
    return _ffmpeg_version_line_uncached()


def parse_ffmpeg_major(line: str) -> int | None:
    """Leading release major from a verbatim ``-version`` first line, or
    ``None`` for anything else (git snapshots, ``missing``, garbage) —
    unknown stays unknown, never guessed."""
    m = re.match(r"ffmpeg version [nN]?(\d+)[.\d]", (line or "").strip())
    return int(m.group(1)) if m else None


def ffmpeg_version_advisory(line: str | None = None) -> str | None:
    """One human line when the DETECTED ffmpeg falls in a RECORDED-issue
    range, else ``None``. Recorded only — never speculative: 7.x carries the
    measured acrossfade encoder-EOF regression (9 failures in 1600 runs vs
    0/1600 on 6.1.1, byte-identical inputs); ≥8 carries the ffprobe
    colour-tag drift the install steer above records. Informational always:
    callers may print it, nothing may gate on it."""
    text = ffmpeg_version_line() if line is None else line
    major = parse_ffmpeg_major(text)
    if major == 7:
        return (
            f"已知回归:ffmpeg 7.x 的 acrossfade 转场腿会间歇性报 "
            f"“{_ACROSSFADE_SIGNATURE}”(同输入实测 1600 跑 9 败,"
            f"{PINNED_FFMPEG_VERSION} 为 0)。建议换回与验证套件同版的 "
            f"{PINNED_FFMPEG_VERSION}"
        )
    if major is not None and major >= 8:
        return (
            f"注意:{major}.x 相对验证套件({PINNED_FFMPEG_VERSION})有 "
            f"ffprobe 色彩标签偏移记录 — 能出片,但探针/QC 口径可能不一致;"
            f"建议 {PINNED_FFMPEG_VERSION}"
        )
    return None


def known_failure_advisory(stderr_tail: str, *,
                           version_line: str | None = None) -> str | None:
    """The steer a failing render appends when its stderr matches a RECORDED
    regression signature — the line a prior session needed when this exact
    failure cost most of a day. ``None`` for every other stderr."""
    if _ACROSSFADE_SIGNATURE not in (stderr_tail or ""):
        return None
    line = ffmpeg_version_line() if version_line is None else version_line
    if parse_ffmpeg_major(line) == 7:
        return (
            f"已知签名:与 ffmpeg 7.x 的 acrossfade 回归一致(间歇性 — 同输入"
            f"实测 1600 跑 9 败,{PINNED_FFMPEG_VERSION} 为 0)。本机:{line}。"
            f"先换回 {PINNED_FFMPEG_VERSION} 再判断是不是项目问题"
        )
    return (
        f"已知签名:此错误形态与 ffmpeg 7.x 的 acrossfade 回归记录一致;"
        f"本机:{line}。先 `ffmpeg -version` 核对 — 与验证套件同版的是 "
        f"{PINNED_FFMPEG_VERSION}"
    )
