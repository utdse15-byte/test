"""Windowless, recoverable launcher for the Windows Start-menu entry.

The ordinary CLI remains ``manju gui``.  This module exists only for the
per-user Windows shortcut, whose target is the active version's ``pythonw.exe``.
It adds the pieces a console-less launch otherwise loses:

* UTF-8 startup logs under ``%LOCALAPPDATA%\\Manju\\Logs``;
* a native error message that names the log instead of failing invisibly;
* a small runtime session file so a second double-click reopens the existing
  local server instead of starting a duplicate one;
* a per-user launcher lock that closes the double-click race before the session
  file has been written.

The session and lock are disposable runtime state.  They are never project
truth, never build/provider inputs and never contain credentials.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import traceback
from typing import Any, Callable, TextIO
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from manju import __version__
from manju.runtime.buildlock import BuildLock, BuildLocked

from .brand import app_icon_path, app_icon_svg

__all__ = [
    "APP_SESSION_ENV",
    "app_gui_argv",
    "app_root",
    "clear_app_session",
    "expected_session_path",
    "installed_icon_path",
    "launch",
    "notify_window_open_failure",
    "read_app_session",
    "register_app_session",
    "session_path_from_env",
    "self_test",
    "windows_install_snapshot",
]

APP_SESSION_ENV = "MANJU_APP_SESSION_FILE"
_SESSION_FORMAT = "manju-windows-app-session.1"
_LOG_KEEP = 12


class _NoRedirect(HTTPRedirectHandler):
    """The lifecycle probe must never leave the exact loopback endpoint."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


_LOOPBACK_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


def app_root(*, env: dict[str, str] | None = None) -> Path:
    """Return the per-user installed-app root without touching project state."""

    source = os.environ if env is None else env
    local = source.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Manju"
    # This fallback makes the launcher testable on non-Windows hosts and gives
    # a readable failure log if somebody invokes the module manually elsewhere.
    return Path.home() / ".manju" / "windows-app"


def expected_session_path(*, env: dict[str, str] | None = None) -> Path:
    return app_root(env=env) / "App" / "workspace-session.json"


def installed_icon_path() -> str:
    """PowerShell-friendly path to the versioned shortcut icon."""

    return str(app_icon_path())


def app_gui_argv() -> list[str]:
    """The one click-first command; intentionally has no project mutation."""

    return ["manju", "gui", "--app", "--port", "0"]


def windows_install_snapshot(*, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return a credential-free, no-network view of the Windows app install.

    A ``.lnk`` is a COM-owned binary format, so this cross-platform snapshot
    reports only that a file was detected.  Ownership/target verification stays
    with ``manju-shortcut.ps1`` on Windows and is never guessed here.
    """

    source = os.environ if env is None else env
    root = app_root(env=source)
    app_dir = root / "App"
    pointer = app_dir / "current.txt"
    try:
        current = pointer.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        current = ""
    version_dir = app_dir / current if current else None
    pythonw = version_dir / "venv" / "Scripts" / "pythonw.exe" if version_dir else None
    appdata = source.get("APPDATA")
    shortcut = (
        Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Manju 工作台.lnk"
        if appdata else None
    )
    try:
        icon = app_icon_path()
        icon_exists = icon.is_file()
    except (OSError, RuntimeError):
        icon = Path("<unavailable>")
        icon_exists = False
    session = expected_session_path(env=source)
    return {
        "app_root": str(root),
        "current": current or None,
        "pythonw": str(pythonw) if pythonw else None,
        "pythonw_exists": bool(pythonw and pythonw.is_file()),
        "icon": str(icon),
        "icon_exists": icon_exists,
        "shortcut": str(shortcut) if shortcut else None,
        "shortcut_detected": bool(shortcut and shortcut.is_file()),
        # Backward-compatible display key.  It intentionally means only
        # "a file exists"; Windows PowerShell owns authoritative verification.
        "shortcut_exists": bool(shortcut and shortcut.is_file()),
        "shortcut_verified": None,
        "session": str(session),
        "session_exists": session.is_file(),
        "logs": str(root / "Logs"),
    }


def _is_loopback_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and port is not None
        and 0 < port < 65536
        and not parsed.username
        and not parsed.password
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def register_app_session(url: str, *, path: Path | None = None) -> str:
    """Publish the bound local URL after the GUI server is listening."""

    if not _is_loopback_http_url(url):
        raise ValueError(f"refusing non-loopback app session URL: {url!r}")
    target = expected_session_path() if path is None else Path(path)
    token = secrets.token_urlsafe(18)
    _atomic_write_json(
        target,
        {
            "format": _SESSION_FORMAT,
            "pid": os.getpid(),
            "url": url,
            "token": token,
            "version": __version__,
            "executable": str(Path(sys.executable).resolve(strict=False)),
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return token


def read_app_session(*, path: Path | None = None) -> dict[str, Any] | None:
    target = expected_session_path() if path is None else Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("format") != _SESSION_FORMAT:
        return None
    url = doc.get("url")
    pid = doc.get("pid")
    token = doc.get("token")
    if not isinstance(url, str) or not _is_loopback_http_url(url):
        return None
    if not isinstance(pid, int) or pid <= 0:
        return None
    if not isinstance(token, str) or not token:
        return None
    return doc


def clear_app_session(token: str, *, path: Path | None = None) -> bool:
    """Remove only the session record created by this exact server process."""

    target = expected_session_path() if path is None else Path(path)
    doc = read_app_session(path=target)
    if not doc or doc.get("token") != token or doc.get("pid") != os.getpid():
        return False
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def session_path_from_env(*, env: dict[str, str] | None = None) -> Path | None:
    """Return the launcher's one allowed session path, or refuse an override.

    The hidden environment handoff must never become an arbitrary file-write
    primitive.  A manually supplied different path is ignored by the GUI.
    """

    source = os.environ if env is None else env
    raw = source.get(APP_SESSION_ENV)
    if not raw:
        return None
    expected = expected_session_path(env=source)
    try:
        candidate = Path(raw).expanduser().resolve(strict=False)
        wanted = expected.expanduser().resolve(strict=False)
    except OSError:
        return None
    return expected if candidate == wanted else None


def _session_state(doc: dict[str, Any], *, timeout: float = 0.65) -> str | None:
    """Return the authoritative local server lifecycle, or ``None`` if stale.

    ``closing`` and ``stuck`` are still live servers.  Treating either as a
    dead session would let a rapid double-click create a second process while
    the first may still be writing the project or waiting on a remote task.
    """

    url = str(doc.get("url", ""))
    if not _is_loopback_http_url(url):
        return None
    endpoint = url.rstrip("/") + "/api/app/status"
    try:
        req = Request(endpoint, headers={"Accept": "application/json"})
        with _LOOPBACK_OPENER.open(req, timeout=timeout) as response:  # noqa: S310 - exact loopback guard above
            if response.status != 200:
                return None
            payload = json.loads(response.read(131072).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("product") != "manju":
        return None
    if payload.get("protocol") != "manju-gui-app-status.1":
        return None
    if payload.get("pid") != doc.get("pid"):
        return None
    state = payload.get("shutdown_state")
    return state if state in {"open", "closing", "stuck"} else None


def _session_healthy(doc: dict[str, Any], *, timeout: float = 0.65) -> bool:
    """Compatibility helper used by older tests/callers."""

    return _session_state(doc, timeout=timeout) == "open"


def _discard_stale_session(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Conservatively report whether a recorded launcher process may live.

    A false positive merely asks the user to inspect a stale local session;
    a false negative could start a second server while the first still writes.
    Permission errors therefore count as alive.
    """

    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        # Never use os.kill(pid, 0) on Windows: unlike POSIX, unsupported
        # signals are implemented with TerminateProcess and a zero signal can
        # therefore kill the very process we are trying to inspect.
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                close_handle(handle)
                return True
            return ctypes.get_last_error() == 5  # ACCESS_DENIED => exists, fail closed
        except Exception:
            return True  # unknown is safer than launching a duplicate writer
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _process_executable(pid: int) -> Path | None:
    """Best-effort executable identity for stale-PID reuse protection.

    ``None`` means the process path could not be inspected.  Callers must fail
    closed in that case; a known mismatch is the only safe reason to discard a
    live PID's session record.
    """

    if pid == os.getpid():
        try:
            return Path(sys.executable).resolve(strict=False)
        except OSError:
            return Path(sys.executable)
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
            query_name = kernel32.QueryFullProcessImageNameW
            query_name.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            query_name.restype = wintypes.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            handle = open_process(0x1000, False, pid)
            if not handle:
                return None
            try:
                capacity = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(capacity.value)
                if not query_name(handle, 0, buffer, ctypes.byref(capacity)):
                    return None
                return Path(buffer.value).resolve(strict=False)
            finally:
                close_handle(handle)
        except Exception:
            return None

    proc_link = Path("/proc") / str(pid) / "exe"
    try:
        return proc_link.resolve(strict=True)
    except OSError:
        return None


def _session_process_may_be_owner(doc: dict[str, Any]) -> bool:
    """Return false only when a live PID is proven to be a different process."""

    pid = doc.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return False
    expected_raw = doc.get("executable")
    if not isinstance(expected_raw, str) or not expected_raw:
        # Older additive session records had no executable identity.  Preserve
        # their fail-closed behavior until that process exits.
        return True
    actual = _process_executable(pid)
    if actual is None:
        return True
    try:
        expected = Path(expected_raw).resolve(strict=False)
    except OSError:
        return True
    return os.path.normcase(str(actual)) == os.path.normcase(str(expected))


def _reopen_existing_session(*, path: Path | None = None) -> bool:
    """Handle a live prior session and prevent a duplicate server.

    The return value means "this launch request has been handled", not merely
    "the browser opened".  Once a loopback server proves it is alive, failure
    to open a browser must surface an error instead of starting a second server.
    """

    target = expected_session_path() if path is None else Path(path)
    doc = read_app_session(path=target)
    if not doc:
        if target.exists():
            _discard_stale_session(target)
        return False
    state = _session_state(doc)
    if state is None:
        if _session_process_may_be_owner(doc):
            _show_native_message(
                "Manju 状态暂时无法确认",
                "已有 Manju 进程仍在运行，但本地状态接口暂时没有响应。"
                "为避免重复任务或重复写入，本次不会启动第二个服务。\n\n"
                "请稍后再试；若窗口已经关闭，可先从任务管理器确认旧进程，"
                "或运行“manju doctor --windows”查看安装状态。",
                error=True,
            )
            return True
        _discard_stale_session(target)
        return False

    url = str(doc["url"])
    if state == "closing":
        _show_native_message(
            "Manju 正在安全退出",
            "已有 Manju 工作台正在处理退出。请等待它完成；为避免重复写入，"
            "本次不会启动第二个本地服务。",
            error=False,
        )
        return True

    from manju.cli import _open_gui_window

    if _open_gui_window(url, True):
        return True
    state_note = "；原会话需要人工检查" if state == "stuck" else ""
    _show_native_message(
        "Manju 已在运行",
        "现有本地工作台仍在运行，但系统未能打开应用窗口。"
        f"{state_note}\n\n请在浏览器中打开：\n{url}\n\n"
        "为避免重复任务，本次不会启动第二个服务。",
        error=True,
    )
    return True


class _LauncherLock:
    """Delegate the per-user launcher mutex to Manju's portable lock owner.

    The Windows app must not grow a fifth advisory-file-lock implementation.
    :class:`~manju.runtime.buildlock.BuildLock` already owns the hard-link /
    ``O_EXCL`` acquisition, holder identity, Windows-safe PID probe, stale-lock
    stealing and heartbeat semantics.  Here it is pointed at disposable app
    runtime state rather than a project, so no project truth or build input is
    introduced.
    """

    def __init__(self, path: Path):
        requested = Path(path)
        self._delegate = BuildLock(
            requested.parent,
            name=requested.stem,
            actor="windows-app-launcher",
            stale_after_s=45.0,
            heartbeat_interval_s=5.0,
        )
        self.path = self._delegate.path
        self._held = False

    def acquire(self) -> bool:
        if self._held:
            return False
        try:
            self._delegate.acquire()
        except BuildLocked:
            return False
        self._held = True
        return True

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            self._delegate.release()
        except OSError as exc:
            # This lock is disposable runtime state.  A scanner or backup tool
            # may briefly hold the file on Windows; leaving holder evidence for
            # the existing stale-lock owner is safer than crashing an otherwise
            # clean application shutdown.
            print(f"launcher lock release warning: {exc}")

    def __enter__(self) -> "_LauncherLock":
        if not self.acquire():
            raise BlockingIOError("Manju launcher is already active")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def _logs_dir() -> Path:
    return app_root() / "Logs"


def _new_log_path() -> Path:
    # Directory creation and fallback belong to _open_launch_log so even a
    # completely unwritable LOCALAPPDATA can produce one native, visible error.
    root = _logs_dir()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return root / f"app-launch-{stamp}-{os.getpid()}.log"


def _prune_logs(*, keep: int = _LOG_KEEP, directory: Path | None = None) -> None:
    root = _logs_dir() if directory is None else Path(directory)
    try:
        rows = sorted(
            root.glob("app-launch-*.log"),
            key=lambda item: item.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError:
        return
    for old in rows[max(1, keep):]:
        try:
            old.unlink()
        except OSError:
            pass


def _show_native_message(title: str, message: str, *, error: bool) -> None:
    if os.name == "nt":
        try:
            import ctypes

            icon = 0x10 if error else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
            flags = icon | 0x00010000 | 0x00040000  # SETFOREGROUND | TOPMOST
            ctypes.windll.user32.MessageBoxW(None, message, title, flags)
            return
        except Exception:
            pass
    try:
        sys.__stderr__.write(f"{title}: {message}\n")
    except Exception:
        pass


def notify_window_open_failure(url: str) -> None:
    """Make a first-launch browser failure visible from ``pythonw``.

    The one healthy loopback server remains authoritative.  Starting another
    server merely because the shell handoff failed could duplicate queued work
    or project writers, so the user gets the exact local URL and log directory
    instead.
    """

    if not _is_loopback_http_url(url):
        return
    _show_native_message(
        "Manju 已启动，但窗口没有打开",
        "本地工作台仍在运行，没有修改任何项目。\n\n"
        f"请在浏览器中打开：\n{url}\n\n"
        f"启动日志位于：\n{_logs_dir()}\n\n"
        "为避免重复任务，本次不会再启动第二个服务。",
        error=True,
    )


def _run_typer_app() -> int:
    from manju.cli import app

    old_argv = sys.argv[:]
    sys.argv = app_gui_argv()
    try:
        app(prog_name="manju")
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else (0 if code is None else 1)
    finally:
        sys.argv = old_argv
    return 0


def _open_launch_log(path: Path) -> tuple[Path, TextIO] | None:
    """Open the UTF-8 log, falling back once before showing native failure."""

    candidates = [path]
    fallback = Path(tempfile.gettempdir()) / "Manju-Logs" / path.name
    if fallback != path:
        candidates.append(fallback)
    for candidate in candidates:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            return candidate, candidate.open("a", encoding="utf-8", buffering=1)
        except OSError:
            continue
    _show_native_message(
        "Manju 未能启动",
        "无法创建启动日志，因此没有继续启动。请确认本地用户目录可写，"
        "然后从终端运行“manju doctor --windows”。",
        error=True,
    )
    return None


def self_test() -> int:
    """Zero-network installer probe for the packaged click-first entry."""

    try:
        icon = app_icon_path()
        if not icon.is_file() or icon.suffix.casefold() != ".ico":
            raise RuntimeError(f"packaged Windows icon is missing: {icon}")
        if icon.stat().st_size < 1024 or icon.read_bytes()[:4] != b"\x00\x00\x01\x00":
            raise RuntimeError(f"packaged Windows icon is invalid: {icon}")
        svg = app_icon_svg()
        if "<svg" not in svg or "viewBox=\"0 0 256 256\"" not in svg:
            raise RuntimeError("packaged browser icon is invalid")
        if app_gui_argv() != ["manju", "gui", "--app", "--port", "0"]:
            raise RuntimeError("Windows app command contract changed")
        # Importing the Typer root proves the installed wheel contains the GUI
        # command without binding a socket, opening a browser or reading keys.
        from manju.cli import app as _app

        commands = _app.registered_commands
        if not any(
            getattr(row, "name", None) == "gui"
            or getattr(getattr(row, "callback", None), "__name__", None) == "gui"
            for row in commands
        ):
            raise RuntimeError("installed CLI has no gui command")
    except Exception as exc:
        try:
            sys.__stderr__.write(f"Manju Windows app self-test failed: {exc}\n")
        except Exception:
            pass
        return 1
    return 0


def launch(*, runner: Callable[[], int] | None = None) -> int:
    """Launch or reopen the personal workbench from a windowless shortcut."""

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    session = expected_session_path()
    requested_log_path = _new_log_path()
    opened = _open_launch_log(requested_log_path)
    if opened is None:
        return 1
    log_path, log = opened
    lock = _LauncherLock(app_root() / "App" / "workspace-launch.lock")
    previous_session_env = os.environ.get(APP_SESSION_ENV)

    with log, redirect_stdout(log), redirect_stderr(log):
        print(
            f"[{datetime.now(timezone.utc).isoformat()}] Manju {__version__} "
            f"launcher pid={os.getpid()} python={sys.executable}"
        )
        try:
            if _reopen_existing_session(path=session):
                print("handled existing local GUI session")
                return 0

            if not lock.acquire():
                # A rapid second double-click can arrive before the first server
                # publishes its URL.  Wait briefly for that exact session rather
                # than launching a duplicate process.
                for _ in range(40):
                    time.sleep(0.125)
                    if _reopen_existing_session(path=session):
                        print("handled session after waiting for concurrent startup")
                        return 0
                _show_native_message(
                    "Manju 正在启动",
                    "另一个 Manju 启动过程仍在进行。请稍等片刻后再试；"
                    f"如果长时间没有窗口，请查看日志：\n{log_path}",
                    error=False,
                )
                return 0

            # Close the narrow race in which another launcher published a live
            # session immediately before we acquired the lock.
            if _reopen_existing_session(path=session):
                print("handled existing local GUI session after lock acquisition")
                return 0

            os.environ[APP_SESSION_ENV] = str(session)
            result = (runner or _run_typer_app)()
            if result:
                raise RuntimeError(f"Manju exited with status {result}")
            return 0
        except KeyboardInterrupt:
            return 0
        except BaseException as exc:
            traceback.print_exc()
            _show_native_message(
                "Manju 未能启动",
                "没有修改任何项目。请从终端运行“manju doctor --windows”，"
                f"并查看启动日志：\n{log_path}\n\n{type(exc).__name__}: {exc}",
                error=True,
            )
            return 1
        finally:
            lock.release()
            if previous_session_env is None:
                os.environ.pop(APP_SESSION_ENV, None)
            else:
                os.environ[APP_SESSION_ENV] = previous_session_env
            _prune_logs(directory=log_path.parent)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(self_test())
    raise SystemExit(launch())
