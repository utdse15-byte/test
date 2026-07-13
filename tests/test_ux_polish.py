"""UX polish round 1 — the top verified defects from the 2026-07-13 three-axis
usability audit (owner / AI-session / project; 46 findings, lens-ranked; the
backlog and provenance live in REPORTS/UX_AUDIT_2026-07-13.md).

Every test here pins a REPRODUCED defect, red-first:

* F1  — ``manju status`` (the takeover entry point) crashed with a raw
        traceback on any hand-edited-broken YAML, and under ``--json`` emitted
        NOTHING on stdout — breaking the repo's own structured-error contract
        (--json always gets ``{error, code}``). Text-is-truth means the owner
        hand-edits YAML constantly; status is what they run right after.
* F2  — ``manju new`` on an existing project died with a raw traceback.
* F3  — ``manju unpack``/``manju fixity`` on a corrupt/truncated .manjupkg
        (the realistic half-copied-backup shape) crashed with BadZipFile —
        on the disaster-recovery commands, the worst place for a traceback.
* F30 — CLI stdout was not UTF-8-hardened: ``manju doctor > log.txt`` on a
        GBK-codepage Windows shell raises UnicodeEncodeError on ✓/✗/⚠/•
        (none encode in cp936/cp1252). The MCP server already hardens its
        streams; the CLI now does the same, nt-only.
* F34 — the "reproducible command line" in ffmpeg logs was POSIX-quoted —
        unpasteable into cmd.exe/PowerShell on the primary platform.
* F14 — /review: data-rev (the CAS token) was never refreshed after a
        successful save, so the owner's SECOND action on the same card was
        always refused 409 with a message blaming "其他入口" — their own
        click of two seconds earlier.
* F19 — /review's keyboard hint mislabelled g/x with the vocabulary of a
        DIFFERENT state machine (通过 = the approve chip, not the take
        verdict the keys actually fire).
"""

from __future__ import annotations

import json
import threading
import urllib.request
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()


# ------------------------------------------------------------ F1: status


def test_status_on_broken_yaml_is_a_clean_error_not_a_traceback(tmp_project, monkeypatch):
    (tmp_project.root / "project.yaml").write_text(
        "name: x\n  bad: [unclosed\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["status"])
    assert res.exit_code == 1
    combined = res.output + (res.stderr or "")
    assert "Traceback" not in combined
    assert "manju check" in combined  # points at the fixer, not a stack


def test_status_json_on_broken_yaml_keeps_the_error_contract(tmp_project, monkeypatch):
    """--json ALWAYS gets a structured {error, code} object on stdout — the
    documented contract every agent relies on (cli._fail; test_experience
    pins the no_project case; this pins the truth-parse case)."""
    shot_dir = tmp_project.root / "shots"
    shot_dir.mkdir(exist_ok=True)
    (shot_dir / "S001.yaml").write_text("id: S001\n  broken: [\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["status", "--json"])
    assert res.exit_code == 1
    doc = json.loads(res.output)
    assert doc["code"] == "truth_parse_error"
    assert doc["error"]


# ------------------------------------------------------------ F2: new twice


def test_new_on_existing_project_is_a_clean_one_liner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["new", "重复"]).exit_code == 0
    res = runner.invoke(app, ["new", "重复"])
    assert res.exit_code == 1
    combined = res.output + (res.stderr or "")
    assert "Traceback" not in combined
    assert "already exists" in combined or "已存在" in combined


# ------------------------------------------------------------ F3: bad archives


def test_unpack_and_fixity_on_corrupt_archive_fail_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bogus = tmp_path / "半拷贝.manjupkg"
    bogus.write_bytes(b"this is not a zip file, it is a truncated copy")
    for cmd in (["unpack", str(bogus)], ["fixity", str(bogus)]):
        res = runner.invoke(app, cmd)
        assert res.exit_code == 1, cmd
        combined = res.output + (res.stderr or "")
        assert "Traceback" not in combined, cmd
        assert "BadZipFile" not in combined, cmd
        assert ".manjupkg" in combined  # names the problem in prose


def test_unpack_not_found_message_matches_the_house_bilingual_pattern(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["unpack", str(tmp_path / "不存在.manjupkg")])
    assert res.exit_code == 1
    combined = res.output + (res.stderr or "")
    assert "not found" in combined
    assert "核对" in combined or "路径" in combined  # the cli import-style hint


# ------------------------------------------------- F4/F7/F12/F5/F10: signposts


def test_new_prints_the_next_step_bridge(tmp_path, monkeypatch):
    """F7/F12: the very first state was the only unsignposted one — `new`
    now bridges to cd + status + the creation funnel."""
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["new", "签路"])
    assert res.exit_code == 0
    assert "下一步" in res.output and "manju status" in res.output
    assert "help-workflow" in res.output


def test_funnel_hint_names_the_stage_not_itself(tmp_path, monkeypatch):
    """F4: the synopsis hint was circular ('manju create 可生成模板' — the
    command that printed the checklist); it must name `manju create synopsis`."""
    from manju.build.funnel import _brief_done, _synopsis_done
    from manju.core.container import Project

    project = Project.create(tmp_path / "漏斗", git_init=False)
    (project.root / "story" / "synopsis.md").unlink(missing_ok=True)
    (project.root / "story" / "brief.md").unlink(missing_ok=True)
    done, evidence = _synopsis_done(project)
    assert done is False and "manju create synopsis" in evidence
    done, evidence = _brief_done(project)
    assert done is False and "manju create brief" in evidence


def test_export_help_documents_srt_and_jianying():
    """F5: --srt's hidden side-effect (ASS/WebVTT ride along — the thing
    doctor's WebVTT hint relies on) must be visible in --help."""
    res = runner.invoke(app, ["export", "--help"])
    assert "WebVTT" in res.output
    assert "剪映" in res.output


def test_missing_ffmpeg_hint_names_the_pinned_version(monkeypatch, tmp_path):
    """F10: the old hint steered Windows users to ffmpeg.org — today an 8.x
    build, the exact colour-tag skew class the gate pins 6.1.1 against."""
    import manju.media.ffmpeg as ff

    err = ff._not_found_error(None, "render", None, ["ffmpeg", "-i", "x"])
    assert "6.1.1" in str(err)
    assert "ffmpeg.org 的 8.x" in str(err)


# ------------------------------------------------------------ F30: UTF-8 stdio


def test_utf8_stdio_hardening_reconfigures_both_streams(monkeypatch):
    """The nt-only import-time guard, driven directly (the W1 stub pattern):
    a stream whose encoding is a legacy codepage gets reconfigure(utf-8,
    errors=replace); a stream without reconfigure (a test capture shim)
    must be left alone, never crashed on."""
    import manju.cli as cli

    class _Stream:
        def __init__(self, encoding="gbk"):
            self.encoding = encoding
            self.calls: list[dict] = []

        def reconfigure(self, **kw):
            self.calls.append(kw)

    out, err = _Stream(), _Stream("cp936")
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)
    cli._utf8_harden_stdio()
    assert out.calls and out.calls[0].get("encoding") == "utf-8"
    assert err.calls and err.calls[0].get("errors") == "replace"

    class _Bare:  # no reconfigure attribute at all (pytest capture shims)
        encoding = "gbk"

    monkeypatch.setattr(cli.sys, "stdout", _Bare())
    monkeypatch.setattr(cli.sys, "stderr", _Bare())
    cli._utf8_harden_stdio()  # must not raise


def test_utf8_stdio_hardening_leaves_utf8_streams_untouched(monkeypatch):
    import manju.cli as cli

    class _Stream:
        encoding = "utf-8"

        def __init__(self):
            self.calls = []

        def reconfigure(self, **kw):
            self.calls.append(kw)

    out = _Stream()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", out)
    cli._utf8_harden_stdio()
    assert out.calls == []  # already UTF-8 → byte-identical behaviour


def test_launcher_sets_pythonutf8():
    """The generated manju.cmd must export PYTHONUTF8=1 so redirected output
    from the installed launcher is UTF-8 regardless of the console codepage
    (the gate itself already runs with it — this extends the same guarantee
    to the owner's real terminal)."""
    src = Path("scripts/windows/install-manju.ps1").read_text(encoding="utf-8")
    assert "PYTHONUTF8" in src


# ------------------------------------------------------------ F34: repro line


def test_repro_command_line_is_windows_pasteable(monkeypatch):
    import manju.media.ffmpeg as ff

    cmd = ["ffmpeg", "-i", r"C:\雨夜 便利店\clip 01.mp4", "-vf", "scale=1080:1920", "out.mp4"]
    monkeypatch.setattr(ff, "_IS_WINDOWS", True)
    line = ff._quote(cmd)
    assert "'" not in line  # cmd.exe treats single quotes as literals
    assert '"C:\\雨夜 便利店\\clip 01.mp4"' in line  # list2cmdline double-quotes

    monkeypatch.setattr(ff, "_IS_WINDOWS", False)
    posix = ff._quote(cmd)
    assert "'" in posix  # POSIX form byte-identical to the historical shlex.quote


# ------------------------------------------------------------ F14 + F19: /review


@pytest.fixture
def gui(tmp_project):
    from manju.gui.server import create_server

    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _post(server, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}",
        data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    if getattr(server, "token", None):
        req.add_header("X-Manju-Token", server.token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_take_note_returns_the_new_rev_so_a_second_action_succeeds(
        gui, tmp_project, add_shot, make_take):
    """F14, the real click sequence: verdict then note (or 好 then 弃) on ONE
    page load. The first save must return the NEW rev; re-sending it as
    expected_rev must succeed — never the misleading 乐观锁 409."""
    from manju.core.writes import shot_text_hash

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    rev0 = shot_text_hash(tmp_project, "S001")

    status, data = _post(gui, "/api/take-note",
                         {"shot": "S001", "take": take.name, "text": "好",
                          "expected_rev": rev0})
    assert status == 200 and data["ok"] is True
    assert data.get("rev"), "the response must carry the post-write rev"
    assert data["rev"] != rev0

    # the second action with the refreshed rev — the previously dead flow
    status, data2 = _post(gui, "/api/take-note",
                          {"shot": "S001", "take": take.name, "text": "弃 · 手抖了",
                           "expected_rev": data["rev"]})
    assert status == 200 and data2["ok"] is True

    # a genuinely stale rev is STILL refused — the lock itself is untouched
    status, _ = _post(gui, "/api/take-note",
                      {"shot": "S001", "take": take.name, "text": "x",
                       "expected_rev": rev0})
    assert status == 409


def test_sb_approve_returns_per_shot_revs(gui, tmp_project, add_shot, make_take):
    """F14, the qapprove half: 通过 rewrites the shot file and used to stale
    the card's rev silently; the response now carries the new rev per shot."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    status, data = _post(gui, "/api/storyboard/approve",
                         {"shot": "S001", "review": "approved"})
    assert status == 200 and data["ok"] is True
    assert data.get("revs", {}).get("S001", "").startswith("sha256:")


def test_review_page_rev_refresh_and_honest_key_hint(gui, tmp_project, add_shot, make_take):
    """F19: the header hint must describe what g/x actually DO (the per-take
    好/弃 verdict), not the separate 审批通过 state machine; F14: the page JS
    must update data-rev from successful responses."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    html = urllib.request.urlopen(
        f"http://127.0.0.1:{gui.port}/review", timeout=15).read().decode("utf-8")
    assert "g 好" in html and "x 弃" in html
    assert "g 通过" not in html
    js = urllib.request.urlopen(
        f"http://127.0.0.1:{gui.port}/pages.js", timeout=15).read().decode("utf-8")
    assert 'setAttribute("data-rev"' in js  # the JS refresh exists
