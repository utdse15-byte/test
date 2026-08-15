"""W2 doctor hardening (MANJU_WINDOWS_ONLY_LEAN_V3 §4.5).

Red-first evidence: at HEAD the doctor had NO python row, NO Windows probes
(winreg/long-path policy/NTFS/OneDrive/network-drive/install-mode/rollback —
grep found zero `winreg`/`LongPathsEnabled` anywhere in src/), and leaked
user-identifying absolute paths (`shutil.which` results verbatim → on Windows
that is `C:\\Users\\<name>\\...`, a username in every shared doctor paste).

Design honoured: probes are tiny module functions returning ``None`` for
UNKNOWN (§ UNKNOWN 不得猜成 PASS — an unknown is REPORTED as unknown, never
upgraded to ✓); every new row is informational (never gates ``ok``); privacy
routes through the ONE redaction owner (core/supportbundle) via the narrow
``redact_private_text`` composition — secrets, signed URLs and PRIVATE-rooted
paths (/home /Users /root C:\\) collapse, while diagnostic system paths
(/usr/bin/ffmpeg) stay readable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import manju.build.doctor as doctor


def _by_name(result: dict) -> dict[str, dict]:
    return {c["name"]: c for c in result["checks"]}


# --------------------------------------------------------------------------
# environment rows every platform gets
# --------------------------------------------------------------------------


def test_doctor_has_python_row():
    """§4.5: doctor must state the Python actually running it."""
    import platform

    checks = _by_name(doctor.run_doctor())
    assert "python" in checks
    assert platform.python_version() in checks["python"]["detail"]
    assert checks["python"]["ok"] is True  # informational, never gates


def test_doctor_windows_flag_is_honest_off_windows():
    """`manju doctor --windows` on a non-Windows host must say plainly that
    the Windows probes were skipped — never fabricate Windows facts."""
    if os.name == "nt":
        pytest.skip("this pin is for non-Windows hosts")
    checks = _by_name(doctor.run_doctor(windows=True))
    assert "windows" in checks
    assert checks["windows"]["ok"] is True
    assert "非 Windows" in checks["windows"]["detail"] or "not Windows" in checks["windows"]["detail"]


# --------------------------------------------------------------------------
# Windows probe rows (probe functions stubbed — the dispatch is under test)
# --------------------------------------------------------------------------


def _stub_probes(monkeypatch, *, long_paths=True, fs="NTFS", remote=False):
    monkeypatch.setattr(doctor, "_IS_WINDOWS", True)
    monkeypatch.setattr(doctor, "_win_long_paths_enabled", lambda: long_paths)
    monkeypatch.setattr(doctor, "_win_volume_fs", lambda path: fs)
    monkeypatch.setattr(doctor, "_win_drive_remote", lambda path: remote)


def test_doctor_windows_rows_present_on_nt(monkeypatch):
    _stub_probes(monkeypatch)
    checks = _by_name(doctor.run_doctor())
    assert "windows_version" in checks
    assert "long_paths" in checks
    assert checks["long_paths"]["ok"] is True
    assert "✓" in checks["long_paths"]["line"]
    assert "app_install" in checks  # install mode + version (informational)


def test_doctor_long_paths_disabled_is_an_advisory(monkeypatch):
    _stub_probes(monkeypatch, long_paths=False)
    checks = _by_name(doctor.run_doctor())
    row = checks["long_paths"]
    assert row["ok"] is True, "informational — must never gate doctor ok"
    assert row["line"].startswith("⚠")
    assert "LongPathsEnabled" in row["detail"]


def test_doctor_long_paths_unknown_is_reported_unknown(monkeypatch):
    """UNKNOWN 不得猜成 PASS: a probe that cannot decide must SAY unknown."""
    _stub_probes(monkeypatch, long_paths=None)
    checks = _by_name(doctor.run_doctor())
    row = checks["long_paths"]
    assert row["ok"] is True
    assert "unknown" in row["detail"].lower() or "未知" in row["detail"]
    assert not row["line"].startswith("✓"), "unknown must never render as a pass"


def test_doctor_ntfs_and_network_rows(tmp_project, monkeypatch):
    _stub_probes(monkeypatch, fs="FAT32", remote=True)
    checks = _by_name(doctor.run_doctor(tmp_project))
    assert checks["filesystem"]["line"].startswith("⚠"), "non-NTFS is an advisory"
    assert "FAT32" in checks["filesystem"]["detail"]
    assert checks["project_drive"]["line"].startswith("⚠"), "network drive is an advisory"


def test_doctor_onedrive_advisory(tmp_project, monkeypatch):
    _stub_probes(monkeypatch)
    monkeypatch.setenv("OneDrive", str(tmp_project.root.parent))
    checks = _by_name(doctor.run_doctor(tmp_project))
    assert "onedrive" in checks
    assert checks["onedrive"]["line"].startswith("⚠")


def test_doctor_config_dir_writable_row(monkeypatch):
    _stub_probes(monkeypatch)
    checks = _by_name(doctor.run_doctor())
    assert "config_writable" in checks


def test_doctor_reports_installed_versions_and_rollback(monkeypatch, tmp_path):
    """§4.5 当前版本和 rollback: when the W2 installer layout exists under
    %LOCALAPPDATA%\\Manju\\App, doctor reports the active + previous version."""
    _stub_probes(monkeypatch)
    app = tmp_path / "Manju" / "App"
    app.mkdir(parents=True)
    (app / "current.txt").write_text("0.1.0-20260713T000000", encoding="utf-8")
    (app / "previous.txt").write_text("0.1.0-20260701T000000", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    checks = _by_name(doctor.run_doctor())
    row = checks["app_install"]
    assert "0.1.0-20260713T000000" in row["detail"]
    assert "0.1.0-20260701T000000" in row["detail"], "rollback target must be visible"


def test_doctor_reports_click_first_install_without_claiming_lnk_verification(monkeypatch, tmp_path):
    _stub_probes(monkeypatch)
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "RoamingAppData"
    version = "0.2.0-20260814T000000"
    app = local / "Manju" / "App"
    pythonw = app / version / "venv" / "Scripts" / "pythonw.exe"
    pythonw.parent.mkdir(parents=True)
    pythonw.write_bytes(b"")
    (app / "current.txt").write_text(version, encoding="ascii")
    shortcut = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Manju 工作台.lnk"
    shortcut.parent.mkdir(parents=True)
    shortcut.write_bytes(b"not-a-real-lnk; detection-only")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))

    checks = _by_name(doctor.run_doctor())
    row = checks["windows_app"]
    assert row["ok"] is True
    assert "shortcut_detected=True" in row["detail"]
    assert "shortcut_verified=windows-only" in row["detail"]
    assert "检测到开始菜单入口" in row["line"]
    assert "已就绪" not in row["line"], "file existence alone must not claim COM ownership"


# --------------------------------------------------------------------------
# §4.5 output hygiene — no usernames / private paths / keys / signed URLs
# --------------------------------------------------------------------------


def test_doctor_redacts_private_paths(monkeypatch):
    """A doctor paste must never leak C:\\Users\\<name> (or /home/<name>) —
    the exact leak class the audit found in the which()/font/chromium rows."""
    monkeypatch.setattr(
        doctor.shutil, "which",
        lambda tool: "C:\\Users\\bob\\tools\\" + tool + ".exe",
    )
    result = doctor.run_doctor()
    joined = "\n".join(c["line"] + "\n" + c["detail"] for c in result["checks"])
    assert "bob" not in joined
    assert "C:\\Users" not in joined
    assert "ffmpeg.exe" in joined, "the basename must survive — diagnosis stays possible"


def test_doctor_system_paths_stay_readable():
    """The narrow composition must NOT nuke /usr/bin-style system paths —
    doctor's whole point is telling the user where things are."""
    import shutil as real_shutil

    found = real_shutil.which("ffmpeg")
    if not found or not found.startswith("/usr/"):
        pytest.skip("needs a system ffmpeg under /usr")
    checks = _by_name(doctor.run_doctor())
    assert found in checks["ffmpeg"]["detail"]


def test_redact_private_text_composition():
    """The doctor redaction reuses supportbundle's pieces: private-rooted
    paths collapse to basenames; secrets/signed URLs mask; system paths and
    CJK survive."""
    from manju.core.supportbundle import redact_private_text

    s = redact_private_text("font C:\\Users\\alice\\字体\\simhei.ttf ok")
    assert "alice" not in s and "simhei.ttf" in s
    s = redact_private_text("/home/bob/.manju/providers ok")
    assert "bob" not in s and "providers" in s
    s = redact_private_text("ffmpeg at /usr/bin/ffmpeg")
    assert "/usr/bin/ffmpeg" in s
    s = redact_private_text("url https://x/y?sig=abc123def456")
    assert "abc123def456" not in s


# --------------------------------------------------------------------------
# CLI surface: manju doctor --windows
# --------------------------------------------------------------------------


def test_cli_doctor_windows_flag(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_path)  # no project — environment checks only
    result = CliRunner().invoke(app, ["doctor", "--windows"])
    assert result.exit_code == 0, result.output
    assert "windows" in result.output.lower()


# --------------------------------------------------------------------------
# Edge/Chrome discovery (consumed by doctor now, board --app in W3)
# --------------------------------------------------------------------------


def test_find_edge_discovers_windows_install_paths(monkeypatch, tmp_path):
    from manju.media import html_card

    fake = tmp_path / "Microsoft" / "Edge" / "Application"
    fake.mkdir(parents=True)
    exe = fake / "msedge.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(html_card.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nope"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nope2"))
    monkeypatch.setattr(html_card, "_IS_WINDOWS", True, raising=False)
    assert html_card.find_edge() == exe


def test_find_edge_absent_is_none(monkeypatch, tmp_path):
    from manju.media import html_card

    monkeypatch.setattr(html_card.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert html_card.find_edge() is None

def test_find_chromium_discovers_chrome_on_windows(monkeypatch, tmp_path):
    """W-real-usage: the CARD renderer's probe must find the Chromium the
    owner's platform actually ships (chrome.exe install roots) — before this,
    find_chromium knew only Linux names, so the M3-preferred HTML renderer
    silently never ran on Windows and every card fell to drawtext."""
    from manju.media import html_card

    fake = tmp_path / "Google" / "Chrome" / "Application"
    fake.mkdir(parents=True)
    exe = fake / "chrome.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.setattr(html_card.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nope"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nope2"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-pw"))
    monkeypatch.setattr(html_card, "_IS_WINDOWS", True, raising=False)
    assert html_card.find_chromium() == exe


def test_find_chromium_falls_back_to_edge_on_windows(monkeypatch, tmp_path):
    """No Chrome anywhere: Edge (always present on Windows 11, and Chromium
    underneath) carries the card renderer instead of dropping to drawtext."""
    from manju.media import html_card

    fake = tmp_path / "Microsoft" / "Edge" / "Application"
    fake.mkdir(parents=True)
    exe = fake / "msedge.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.setattr(html_card.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nope"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nope2"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-pw"))
    monkeypatch.setattr(html_card, "_IS_WINDOWS", True, raising=False)
    assert html_card.find_chromium() == exe


def test_find_chromium_windows_roots_stay_off_on_posix(monkeypatch, tmp_path):
    """The Windows block is gated on _IS_WINDOWS: identical env on POSIX
    still answers None (no cross-platform ghost hits)."""
    from manju.media import html_card

    fake = tmp_path / "Google" / "Chrome" / "Application"
    fake.mkdir(parents=True)
    (fake / "chrome.exe").write_bytes(b"MZ")
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.setattr(html_card.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-pw"))
    monkeypatch.setattr(html_card, "_IS_WINDOWS", False, raising=False)
    assert html_card.find_chromium() is None


def test_find_chromium_playwright_windows_layout(monkeypatch, tmp_path):
    """A playwright-managed browsers dir on Windows lays chromium out as
    chrome-win/chrome.exe — the glob must match it like chrome-linux/chrome."""
    from manju.media import html_card

    pw = tmp_path / "pw" / "chromium-1234" / "chrome-win"
    pw.mkdir(parents=True)
    exe = pw / "chrome.exe"
    exe.write_bytes(b"MZ")
    exe.chmod(0o755)
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.setattr(html_card.shutil, "which", lambda name: None)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nope"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nope"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nope"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "pw"))
    monkeypatch.setattr(html_card, "_IS_WINDOWS", True, raising=False)
    assert html_card.find_chromium() == exe
