"""Product Polish R1 Wave 14 — click-first Windows application lifecycle.

These tests stay local and credential-free.  They prove the pure/session parts
on every host and pin the PowerShell/shortcut boundary statically; the real
pythonw/COM/Start-menu journey remains a Windows hard-gate responsibility.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
from urllib.request import urlopen

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.gui import windows_app as wa
from manju.gui.brand import app_icon_path, app_icon_svg
from manju.gui.pages import GLOSSARY_HEAD

runner = CliRunner()


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "LOCALAPPDATA": str(tmp_path / "LocalAppData"),
        "APPDATA": str(tmp_path / "RoamingAppData"),
        "USERPROFILE": str(tmp_path),
    }


def _write_session(path: Path, *, url: str = "http://127.0.0.1:4444/") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "format": "manju-windows-app-session.1",
                "pid": 44,
                "token": "token",
                "url": url,
            }
        ),
        encoding="utf-8",
    )


def test_brand_assets_are_local_packaged_and_shared_by_browser_head():
    icon = app_icon_path()
    svg = app_icon_svg()
    assert icon.is_file() and icon.suffix == ".ico" and icon.stat().st_size > 1000
    assert svg.startswith("<svg") and "#6ea8fe" in svg
    assert '<link rel="icon" href="/favicon.ico" type="image/svg+xml">' in GLOSSARY_HEAD
    assert '<meta name="theme-color" content="#11151c">' in GLOSSARY_HEAD


def test_favicon_route_serves_the_packaged_svg():
    from manju.gui.server import create_server

    server = create_server(None, host="127.0.0.1", port=0, app_mode=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(server.url.rstrip("/") + "/favicon.ico", timeout=2.0) as response:
            body = response.read().decode("utf-8")
            assert response.headers.get_content_type() == "image/svg+xml"
            assert body == app_icon_svg()
    finally:
        server.shutdown()
        server.close()
        thread.join(timeout=2)


def test_start_menu_command_is_windowless_navigation_only_and_self_tests(monkeypatch):
    assert wa.app_gui_argv() == ["manju", "gui", "--app", "--port", "0"]
    command = " ".join(wa.app_gui_argv()).casefold()
    for forbidden in ("build", "redo", "select", "approve", "provider", "lock"):
        assert forbidden not in command
    monkeypatch.setattr(wa._LOOPBACK_OPENER, "open", lambda *_a, **_k: pytest.fail("self-test used network"))
    assert wa.self_test() == 0


def test_session_roundtrip_is_exact_token_and_loopback_only(tmp_path, monkeypatch):
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)
    path = wa.expected_session_path()
    token = wa.register_app_session("http://127.0.0.1:43123/", path=path)
    doc = wa.read_app_session(path=path)
    assert doc and doc["url"] == "http://127.0.0.1:43123/"
    assert doc["pid"] == os.getpid() and doc["token"] == token
    assert Path(doc["executable"]).resolve(strict=False) == Path(sys.executable).resolve(strict=False)
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    assert wa.clear_app_session("not-the-token", path=path) is False
    assert path.exists()
    assert wa.clear_app_session(token, path=path) is True
    assert not path.exists()

    with pytest.raises(ValueError, match="non-loopback"):
        wa.register_app_session("https://example.com/", path=path)


def test_hidden_session_env_cannot_write_an_arbitrary_path(tmp_path):
    env = _env(tmp_path)
    expected = wa.expected_session_path(env=env)
    env[wa.APP_SESSION_ENV] = str(expected)
    assert wa.session_path_from_env(env=env) == expected
    env[wa.APP_SESSION_ENV] = str(tmp_path / "outside.json")
    assert wa.session_path_from_env(env=env) is None


@pytest.mark.parametrize("state", ["open", "closing", "stuck"])
def test_session_state_accepts_only_live_loopback_lifecycles(state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            assert self.path == "/api/app/status"
            raw = json.dumps(
                {
                    "product": "manju",
                    "protocol": "manju-gui-app-status.1",
                    "pid": 1,
                    "shutdown_state": state,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        doc = {
            "format": "manju-windows-app-session.1",
            "pid": 1,
            "token": "x",
            "url": f"http://127.0.0.1:{server.server_address[1]}/",
        }
        assert wa._session_state(doc, timeout=2.0) == state
        doc["pid"] = 2
        assert wa._session_state(doc, timeout=2.0) is None
        doc["pid"] = 1
        doc["url"] = "http://example.com:80/"
        assert wa._session_state(doc, timeout=0.1) is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_session_probe_does_not_follow_redirects():
    hits = {"target": 0}

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits["target"] += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            return

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()

    location = f"http://127.0.0.1:{target.server_address[1]}/outside"

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()

        def log_message(self, *_args):
            return

    redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    redirect_thread = threading.Thread(target=redirect.serve_forever, daemon=True)
    redirect_thread.start()
    try:
        doc = {
            "format": "manju-windows-app-session.1",
            "pid": 1,
            "token": "x",
            "url": f"http://127.0.0.1:{redirect.server_address[1]}/",
        }
        assert wa._session_state(doc, timeout=2.0) is None
        assert hits["target"] == 0
    finally:
        redirect.shutdown()
        redirect.server_close()
        redirect_thread.join(timeout=2)
        target.shutdown()
        target.server_close()
        target_thread.join(timeout=2)


def test_existing_open_session_reopens_without_duplicate(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    _write_session(session)
    opened: list[tuple[str, bool]] = []
    messages: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(wa, "_session_state", lambda _doc: "open")
    monkeypatch.setattr(wa, "_show_native_message", lambda t, m, *, error: messages.append((t, m, error)))
    import manju.cli as cli

    monkeypatch.setattr(cli, "_open_gui_window", lambda url, app: opened.append((url, app)) or True)
    assert wa._reopen_existing_session(path=session) is True
    assert opened == [("http://127.0.0.1:4444/", True)]
    assert messages == [] and session.exists()


def test_browser_failure_on_live_session_never_starts_second_server(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    _write_session(session)
    messages: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(wa, "_session_state", lambda _doc: "open")
    monkeypatch.setattr(wa, "_show_native_message", lambda t, m, *, error: messages.append((t, m, error)))
    import manju.cli as cli

    monkeypatch.setattr(cli, "_open_gui_window", lambda *_a, **_k: False)
    assert wa._reopen_existing_session(path=session) is True
    assert session.exists()
    assert messages and messages[0][0] == "Manju 已在运行"
    assert "不会启动第二个服务" in messages[0][1]
    assert messages[0][2] is True


def test_closing_session_blocks_duplicate_and_stuck_session_reopens(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    _write_session(session)
    messages: list[tuple[str, str, bool]] = []
    opened: list[str] = []
    monkeypatch.setattr(wa, "_show_native_message", lambda t, m, *, error: messages.append((t, m, error)))
    import manju.cli as cli

    monkeypatch.setattr(cli, "_open_gui_window", lambda url, _app: opened.append(url) or True)
    monkeypatch.setattr(wa, "_session_state", lambda _doc: "closing")
    assert wa._reopen_existing_session(path=session) is True
    assert opened == []
    assert messages[-1][0] == "Manju 正在安全退出"

    monkeypatch.setattr(wa, "_session_state", lambda _doc: "stuck")
    assert wa._reopen_existing_session(path=session) is True
    assert opened == ["http://127.0.0.1:4444/"]


def test_stale_or_malformed_session_is_removed(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    _write_session(session)
    monkeypatch.setattr(wa, "_session_state", lambda _doc: None)
    assert wa._reopen_existing_session(path=session) is False
    assert not session.exists()

    session.write_text("not-json", encoding="utf-8")
    assert wa._reopen_existing_session(path=session) is False
    assert not session.exists()


def test_unreachable_but_live_process_blocks_duplicate_server(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    _write_session(session)
    messages: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(wa, "_session_state", lambda _doc: None)
    monkeypatch.setattr(wa, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(
        wa,
        "_show_native_message",
        lambda title, message, *, error: messages.append((title, message, error)),
    )

    assert wa._reopen_existing_session(path=session) is True
    assert session.exists(), "live-but-unreachable session evidence must not be discarded"
    assert messages and messages[0][0] == "Manju 状态暂时无法确认"
    assert "不会启动第二个服务" in messages[0][1]
    assert messages[0][2] is True


def test_known_pid_reuse_by_another_executable_discards_stale_session(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    _write_session(session)
    doc = json.loads(session.read_text(encoding="utf-8"))
    doc["executable"] = str(tmp_path / "installed" / "pythonw.exe")
    session.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(wa, "_session_state", lambda _doc: None)
    monkeypatch.setattr(wa, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wa, "_process_executable", lambda _pid: tmp_path / "other" / "python.exe")

    assert wa._reopen_existing_session(path=session) is False
    assert not session.exists(), "a proven PID-reuse mismatch must not block launch forever"


def test_unknown_live_process_identity_still_fails_closed(tmp_path, monkeypatch):
    session = tmp_path / "session.json"
    _write_session(session)
    doc = json.loads(session.read_text(encoding="utf-8"))
    doc["executable"] = str(tmp_path / "installed" / "pythonw.exe")
    session.write_text(json.dumps(doc), encoding="utf-8")
    messages: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(wa, "_session_state", lambda _doc: None)
    monkeypatch.setattr(wa, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wa, "_process_executable", lambda _pid: None)
    monkeypatch.setattr(
        wa,
        "_show_native_message",
        lambda title, message, *, error: messages.append((title, message, error)),
    )

    assert wa._reopen_existing_session(path=session) is True
    assert session.exists()
    assert messages and "不会启动第二个服务" in messages[0][1]


def test_windows_pid_probe_uses_query_only_api(monkeypatch):
    calls: list[tuple] = []

    class FakeFunction:
        def __init__(self, result):
            self.result = result
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            calls.append(args)
            return self.result

    kernel = SimpleNamespace(
        OpenProcess=FakeFunction(1234),
        CloseHandle=FakeFunction(True),
    )
    fake_ctypes = ModuleType("ctypes")
    fake_ctypes.WinDLL = lambda *_a, **_k: kernel  # type: ignore[attr-defined]
    fake_ctypes.get_last_error = lambda: 0  # type: ignore[attr-defined]
    fake_ctypes.wintypes = SimpleNamespace(  # type: ignore[attr-defined]
        DWORD=int, BOOL=int, HANDLE=int
    )

    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(wa.os, "name", "nt")
    monkeypatch.setattr(
        wa.os, "kill", lambda *_a: pytest.fail("Windows PID probe used os.kill")
    )

    assert wa._pid_alive(4242) is True
    assert calls[0] == (0x1000, False, 4242)  # PROCESS_QUERY_LIMITED_INFORMATION
    assert calls[1] == (1234,)


def test_launcher_lock_closes_the_double_click_race(tmp_path):
    first = wa._LauncherLock(tmp_path / "launch.lock")
    second = wa._LauncherLock(tmp_path / "launch.lock")
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


def test_launcher_lock_release_failure_is_disposable_not_an_app_crash(tmp_path, capsys):
    lock = wa._LauncherLock(tmp_path / "launch.lock")

    class FailingDelegate:
        def release(self):
            raise OSError("scanner still holds the runtime lock")

    lock._delegate = FailingDelegate()
    lock._held = True
    lock.release()

    assert lock._held is False
    assert "launcher lock release warning" in capsys.readouterr().out


def test_first_window_open_failure_is_native_only_for_start_menu_launch(monkeypatch, tmp_path):
    import manju.cli as cli

    monkeypatch.setattr(cli, "_open_gui_window", lambda *_a: False)
    messages: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        wa,
        "_show_native_message",
        lambda title, message, *, error: messages.append((title, message, error)),
    )
    session = tmp_path / "session.json"

    assert cli._open_gui_window_with_launcher_notice(
        "http://127.0.0.1:4567/", True, app_session_path=None
    ) is False
    assert messages == []

    assert cli._open_gui_window_with_launcher_notice(
        "http://127.0.0.1:4567/", True, app_session_path=session
    ) is False
    assert messages and messages[0][0] == "Manju 已启动，但窗口没有打开"
    assert "不会再启动第二个服务" in messages[0][1]
    assert messages[0][2] is True


def test_windowless_launch_sets_runtime_session_env_then_restores_it(tmp_path, monkeypatch):
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(wa.APP_SESSION_ENV, raising=False)
    log = tmp_path / "launch.log"
    monkeypatch.setattr(wa, "_new_log_path", lambda: log)
    monkeypatch.setattr(wa, "_reopen_existing_session", lambda **_kw: False)
    messages = []
    monkeypatch.setattr(wa, "_show_native_message", lambda *a, **k: messages.append((a, k)))

    seen = {}

    def fake_runner() -> int:
        seen["session"] = Path(wa.session_path_from_env())
        seen["argv"] = wa.app_gui_argv()
        return 0

    assert wa.launch(runner=fake_runner) == 0
    assert seen["session"] == wa.expected_session_path()
    assert seen["argv"] == ["manju", "gui", "--app", "--port", "0"]
    assert wa.APP_SESSION_ENV not in os.environ
    assert messages == []
    assert "launcher pid=" in log.read_text(encoding="utf-8")


def test_windowless_launch_failure_is_visible_and_logged(tmp_path, monkeypatch):
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)
    log = tmp_path / "failure.log"
    monkeypatch.setattr(wa, "_new_log_path", lambda: log)
    monkeypatch.setattr(wa, "_reopen_existing_session", lambda **_kw: False)
    messages = []
    monkeypatch.setattr(
        wa,
        "_show_native_message",
        lambda title, message, *, error: messages.append((title, message, error)),
    )

    def fail() -> int:
        raise RuntimeError("startup exploded")

    assert wa.launch(runner=fail) == 1
    assert messages and messages[0][0] == "Manju 未能启动"
    assert messages[0][2] is True and str(log) in messages[0][1]
    text = log.read_text(encoding="utf-8")
    assert "RuntimeError: startup exploded" in text
    assert "Traceback" in text


def test_launch_log_falls_back_and_prunes_the_actual_directory(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    fallback_root = tmp_path / "fallback"
    monkeypatch.setattr(wa.tempfile, "gettempdir", lambda: str(fallback_root))
    opened = wa._open_launch_log(blocked / "app-launch-current.log")
    assert opened is not None
    path, fh = opened
    fh.close()
    assert path.parent == fallback_root / "Manju-Logs"

    for idx in range(5):
        row = path.parent / f"app-launch-20260814-{idx}.log"
        row.write_text(str(idx), encoding="utf-8")
        os.utime(row, (idx + 1, idx + 1))
    wa._prune_logs(keep=2, directory=path.parent)
    rows = sorted(path.parent.glob("app-launch-*.log"))
    # two newest plus the current launch file whose timestamp is newer
    assert len(rows) == 2


def test_install_snapshot_is_no_network_and_honest_about_shortcut_detection(tmp_path):
    env = _env(tmp_path)
    root = wa.app_root(env=env)
    version = "0.2.0-test"
    pythonw = root / "App" / version / "venv" / "Scripts" / "pythonw.exe"
    pythonw.parent.mkdir(parents=True)
    pythonw.write_bytes(b"")
    (root / "App" / "current.txt").write_text(version, encoding="ascii")
    shortcut = Path(env["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Manju 工作台.lnk"
    shortcut.parent.mkdir(parents=True)
    shortcut.write_bytes(b"unverified-lnk")

    snap = wa.windows_install_snapshot(env=env)
    assert snap["current"] == version
    assert snap["pythonw_exists"] is True
    assert snap["icon_exists"] is True
    assert snap["shortcut_detected"] is True
    assert snap["shortcut_verified"] is None
    assert snap["session_exists"] is False
    assert snap["logs"].endswith("Manju/Logs") or snap["logs"].endswith("Manju\\Logs")


def test_gui_publishes_session_only_while_server_is_alive(tmp_path, monkeypatch):
    for key, value in _env(tmp_path).items():
        monkeypatch.setenv(key, value)
    session = wa.expected_session_path()
    monkeypatch.setenv(wa.APP_SESSION_ENV, str(session))
    monkeypatch.chdir(tmp_path)
    observed = {}

    class FakeServer:
        url = "http://127.0.0.1:45678/"

        def serve_forever(self):
            observed["during"] = wa.read_app_session(path=session)

        def close(self):
            observed["closed"] = True

    import manju.gui.server as server_mod

    monkeypatch.setattr(server_mod, "create_server", lambda *_a, **_kw: FakeServer())

    result = runner.invoke(app, ["gui", "--no-open", "--port", "0"])
    assert result.exit_code == 0, result.output
    assert observed["during"]["url"] == FakeServer.url
    assert observed["closed"] is True
    assert not session.exists()


def test_app_status_identifies_the_exact_manju_process():
    from manju.gui.server import create_server

    server = create_server(None, host="127.0.0.1", port=0, app_mode=True)
    try:
        status = server.app_status()
        assert status["product"] == "manju"
        assert status["protocol"] == "manju-gui-app-status.1"
        assert status["pid"] == os.getpid()
        assert status["shutdown_state"] == "open"
    finally:
        server.close()


def test_windows_scripts_stage_shortcut_and_self_test_before_activation():
    installer = Path("scripts/windows/install-manju.ps1").read_text(encoding="utf-8")
    helper = Path("scripts/windows/manju-shortcut.ps1").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/windows-ci.yml").read_text(encoding="utf-8")

    assert "manju.gui.windows_app --self-test" in installer
    assert installer.index("manju.gui.windows_app --self-test") < installer.index("Activating $versionId")
    assert ".Manju-Workspace-" in helper
    assert "Staged shortcut target changed unexpectedly" in helper
    assert "Staged shortcut arguments changed unexpectedly" in helper
    assert "Staged shortcut icon changed unexpectedly" in helper
    assert "Shortcut ownership changed while updating" in helper
    assert "-MaximumRedirection 0" in helper
    assert "Move-Item -LiteralPath $tempShortcut" in helper
    assert "pythonw.exe" in workflow
    assert "shortcut target did not follow rollback" in workflow
    assert "uninstall accepted a running installed process" in workflow
    assert "uninstall removed application files before refusing" in workflow
    assert "owned Start-menu shortcut survived uninstall" in workflow
