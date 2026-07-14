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


# ---------------------------------------- wave D: board/GUI/doctor/installer


def test_gc_hard_refusal_explains_and_offers_the_alternative(tmp_project, monkeypatch):
    """F6: the non-tty refusal was a bare English token while sibling unlock
    explains itself — agents (the stated collaboration surface) need the
    branchable code and the safe alternative."""
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["gc", "--hard"])
    assert res.exit_code == 1
    combined = res.output + (res.stderr or "")
    assert "interactive-only" in combined
    assert "manju gc" in combined  # the agent-safe alternative is named


def test_sync_dir_marker_covers_the_clients_the_owner_actually_uses():
    """F33: OneDrive-only, case-sensitive detection missed Dropbox and the
    China-common clients; the marker helper is pure and covers them all."""
    from manju.build.doctor import _sync_dir_marker

    assert _sync_dir_marker(r"C:\Users\o\OneDrive\项目.manju", {}) == "OneDrive"
    assert _sync_dir_marker(r"C:\Users\o\onedrive\项目.manju", {}) == "OneDrive"
    assert _sync_dir_marker(r"D:\Dropbox\项目.manju", {}) == "Dropbox"
    assert _sync_dir_marker(r"D:\坚果云\项目.manju", {}) == "坚果云"
    assert _sync_dir_marker(r"D:\Nutstore\项目.manju", {}) == "Nutstore"
    assert _sync_dir_marker(r"E:\百度网盘同步\项目.manju", {}) == "百度网盘"
    assert _sync_dir_marker(r"C:\普通目录\项目.manju", {}) is None
    # the env-var detection stays (OneDrive envs point at the sync root)
    assert _sync_dir_marker(r"C:\X\项目.manju",
                            {"OneDrive": r"C:\X"}) == "OneDrive"


def test_unknown_url_serves_html_404_for_browsers_json_for_apis(
        gui, tmp_project):
    """F21: a mistyped/stale URL dead-ended in bare JSON with no way back.
    Browser navigations (Accept: text/html) now get a small page with a link
    home; API fetches keep the exact JSON envelope."""
    base = f"http://127.0.0.1:{gui.port}"
    req = urllib.request.Request(base + "/nonexistent-page")
    req.add_header("Accept", "text/html,application/xhtml+xml")
    try:
        urllib.request.urlopen(req, timeout=15)
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
        body = exc.read().decode("utf-8")
        assert "text/html" in exc.headers.get("Content-Type", "")
        assert 'href="/"' in body and "页面不存在" in body

    req = urllib.request.Request(base + "/nonexistent-api")
    req.add_header("Accept", "application/json")
    try:
        urllib.request.urlopen(req, timeout=15)
        raise AssertionError("expected 404")
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
        assert json.loads(exc.read())["error"] == "not found"


def test_board_serve_404_html_for_browsers(tmp_project):
    from manju.board.server import make_server

    server = make_server(tmp_project, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/stale-bookmark")
        req.add_header("Accept", "text/html")
        try:
            urllib.request.urlopen(req, timeout=15)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            assert "页面不存在" in exc.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_error_toasts_are_sticky_and_success_toasts_still_autodismiss():
    """F20: 3.6-second auto-dismiss made long English engine errors
    unreadable; error toasts now stay until clicked (✕), successes keep the
    quick dismiss. Source-level pin on the one shared toast()."""
    from manju.gui.common_js import COMMON_JS

    assert "ok === false" in COMMON_JS
    # the error branch must NOT ride the same unconditional 3600ms removal
    body = COMMON_JS.split("function toast(")[1]
    assert "click" in body  # click-to-dismiss exists
    assert "3600" in body   # the success path keeps the quick dismiss


def test_board_banner_is_viewport_fixed():
    """F15: the serve-mode banner sat in normal flow at the top of the page —
    a failed action while scrolled deep played out as 'nothing happened'."""
    from manju.board.board import _SERVE_CSS

    banner_css = _SERVE_CSS.split(".mj-banner {")[1].split("}")[0]
    assert "fixed" in banner_css or "sticky" in banner_css
    assert "z-index" in banner_css


def test_installer_names_the_failing_step():
    """F31: the three likeliest install failures (Store-alias python, venv,
    pip) died pointing away from the cause; the script now checks each step
    and the catch names the log. Property pins, no execution."""
    src = Path("scripts/windows/install-manju.ps1").read_text(encoding="utf-8")
    assert "Store" in src              # the App-Execution-Alias trap is named
    assert "venv creation failed" in src
    assert "pip install failed" in src
    assert src.count("$LASTEXITCODE") >= 4  # version probe + venv + pip + self-test


def test_every_text_subprocess_decode_declares_utf8():
    """F29 (repo-wide pin): on Windows, ``text=True`` without ``encoding=``
    decodes child output with the ANSI codepage (cp936) — tesseract's UTF-8
    stdout mojibakes and `token in text` QC checks falsely FAIL for text that
    IS on screen; ffmpeg stderr tails ride garbled into error messages. The
    house pattern is ``encoding="utf-8", errors="replace"`` (gitops/ffmpeg/
    probe already do it); this scan keeps the class extinct."""
    import re

    src_root = Path(__file__).resolve().parent.parent / "src" / "manju"
    offenders: list[str] = []
    for py in sorted(src_root.rglob("*.py")):
        src = py.read_text(encoding="utf-8")
        for m in re.finditer(
                r"(?:subprocess\.(?:run|Popen|check_output|check_call|call))\s*\(", src):
            start, depth = m.end() - 1, 0
            for i in range(start, min(len(src), start + 2500)):
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                    if depth == 0:
                        span = src[start:i + 1]
                        break
            else:
                continue
            if (("text=True" in span or "universal_newlines=True" in span)
                    and "encoding=" not in span):
                offenders.append(f"{py.relative_to(src_root)}:{src[:m.start()].count(chr(10)) + 1}")
    assert offenders == [], (
        "text=True without encoding= decodes with the ANSI codepage on "
        f"Windows — add encoding='utf-8', errors='replace': {offenders}")


# ------------------------------------------- F16: board annotate flow honesty


def test_board_annotate_response_carries_the_new_rev(tmp_project, add_shot, make_take):
    """F16 half 1: without the post-write hash in the response, the page
    cannot refresh its CAS tokens client-side — every follow-up annotate on
    the same shot would 409 against the owner's own previous annotation
    (the F14 class, board edition)."""
    from manju.board.server import API_ACTIONS

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    result = API_ACTIONS["annotate"](tmp_project, {
        "shot": "S001", "take": take.name, "text": "第一条", "severity": "note"})
    assert result["id"].startswith("ann_")
    assert result.get("rev", "").startswith("sha256:")

    # the returned rev IS the current file hash — a follow-up CAS write passes
    result2 = API_ACTIONS["annotate"](tmp_project, {
        "shot": "S001", "take": take.name, "text": "第二条", "severity": "issue",
        "expected_rev": result["rev"]})
    assert result2["id"].startswith("ann_")


def test_board_js_annotates_from_the_compare_frame_and_never_reloads():
    """F16 halves 2+3, source pins on the served JS: the '当前帧' capture must
    consult the OPEN compare stack (where frame-accurate parking happens —
    K/L/J and ±1帧 live there) before falling back to the card video; a
    successful annotate must NOT ride the global location.reload (which
    dropped every parked player/tab); the active tab survives in the URL
    hash."""
    from manju.board.board import _SERVE_JS

    ann = _SERVE_JS.split("function annSubmit(")[1].split("\n  }")[0]
    assert "compare-wrap" in ann          # the open compare stack is consulted
    assert "activeCmpVideos" in ann
    post_fn = _SERVE_JS.split("function post(")[1].split("\n  }")[0]
    assert "onOk" in post_fn              # caller-managed success path exists
    assert "mjtab" in _SERVE_JS           # tab persistence via location.hash


def test_board_states_read_chinese_with_the_enum_on_title(tmp_project, add_shot, make_take):
    """F18: the board showed raw English enums ('manual', 'needs_selection')
    while the GUI translates the SAME vocabulary — one state, two names. The
    badge now carries the GUI's Chinese with the enum in the title attribute;
    empty states point at the next step instead of dead-ending."""
    from manju.board.board import _STATE_ZH, render_board

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    html = render_board(tmp_project, serve=True)
    # a state badge renders the ZH text and keeps the enum greppable in title
    assert 'title="manual"' in html or 'title="needs_selection"' in html \
        or 'title="fresh"' in html or 'title="stale"' in html
    assert any(zh in html for zh in _STATE_ZH.values())
    # the annotation severity options carry the bilingual labels
    assert "note 备注" in html and "blocker 阻断" in html


def test_board_empty_state_points_at_the_next_step(tmp_project):
    from manju.board.board import render_board

    html = render_board(tmp_project, serve=True)
    assert "还没有镜头" in html
    assert "shots/" in html  # names where shots come from


def test_import_expands_wildcards_and_tilde_when_the_shell_did_not(
        tmp_project, tmp_path, monkeypatch):
    """F11 (code half): cmd.exe/PowerShell pass ~ and wildcards to native
    executables LITERALLY — the README's flagship import line failed on the
    primary platform. import now expands a NON-EXISTING arg that carries ~ or
    glob chars; a wildcard matching nothing keeps the clean not-found error,
    and existing weird-named files are never re-interpreted."""
    clips = tmp_path / "素材"
    clips.mkdir()
    (clips / "开场.mp4").write_bytes(b"clip-a")
    (clips / "雨夜.mp4").write_bytes(b"clip-b")
    monkeypatch.chdir(tmp_project.root)

    res = runner.invoke(app, ["import", str(clips / "*.mp4")])
    assert res.exit_code == 0, res.output
    got = sorted(p.name for p in tmp_project.imports_dir.glob("*.mp4"))
    assert got == ["开场.mp4", "雨夜.mp4"]

    res = runner.invoke(app, ["import", str(clips / "*.mov")])
    assert res.exit_code == 1
    assert "not found" in res.output + (res.stderr or "")


# ------------------------------------------ F17: annotations visible in gui


def test_review_page_shows_board_annotations_with_stale_honesty(
        gui, tmp_project, add_shot, make_take):
    """F17: annotations filed on the board (severity/frame/media binding)
    vanished on the richer /review page — the owner opened 审片 to act on a
    blocker and saw 'QC 无此镜发现'. The page now mirrors the board's list
    read-only: same severity vocabulary, the ONE staleness rule
    (Annotation.matches_media), and a click-to-seek frame chip."""
    from manju.board.server import API_ACTIONS

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    status, data = _post(gui, "/api/select", {"shot": "S001", "take": take.name})
    assert status == 200, data

    API_ACTIONS["annotate"](tmp_project, {
        "shot": "S001", "take": take.name, "text": "第8帧左手穿帮",
        "severity": "blocker", "frame": 8})

    html = urllib.request.urlopen(
        f"http://127.0.0.1:{gui.port}/review", timeout=15).read().decode("utf-8")
    assert "第8帧左手穿帮" in html
    assert "blocker" in html
    assert "data-seek=" in html  # the frame chip carries the seek target

    # STALE honesty: replace the take media bytes → the binding no longer
    # matches → the page must SAY so, never silently point at new pixels.
    info = tmp_project.get_take("S001", take.name)
    info.media_path.write_bytes(b"totally-different-bytes")
    html = urllib.request.urlopen(
        f"http://127.0.0.1:{gui.port}/review", timeout=15).read().decode("utf-8")
    assert "STALE" in html


def test_annotation_list_container_renders_even_when_empty(
        tmp_project, add_shot, make_take):
    """Browser-verified F16 follow-up: the client-side insert after a take's
    FIRST annotation targets .ann-list — when the server only rendered the
    container for non-empty lists, that first insert was a silent no-op (the
    row appeared only after a manual reload). Found by the real headless-
    Chromium pass; unreachable by HTTP-level tests."""
    from manju.board.board import render_board

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    html = render_board(tmp_project, serve=True)
    assert 'class="ann-list"' in html  # present with zero annotations


def test_token_refused_post_with_large_body_gets_a_clean_403(gui, tmp_project):
    """Windows gate run #20: a POST refused for a missing token, carrying an
    UNREAD request body, left bytes on the socket — Windows RSTs and the
    client saw ConnectionAbortedError (WinError 10053) instead of the 403
    (two token-guard tests died exactly this way). The gui server now drains
    before every early refusal, matching the board server's own discipline.
    A 1 MB body makes the undreained race maximally likely; the clean 403
    must come back on every platform."""
    body = json.dumps({"pad": "x" * (1024 * 1024)}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{gui.port}/api/select", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Manju-Token", "wrong-token")
    try:
        urllib.request.urlopen(req, timeout=15)
        raise AssertionError("expected 403")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403
        assert "X-Manju-Token" in json.loads(exc.read())["error"]

    src = Path("src/manju/gui/server.py").read_text(encoding="utf-8")
    gate = src.split("def do_POST")[1].split("url = urlsplit")[0]
    assert gate.count("_drain_request_body()") == 3  # host/readonly/token


def test_python_dash_m_manju_works():
    """Round-2 journey finding: `python -m manju` said 'No module named
    manju.__main__' — on Windows that is THE fallback when the console-script
    shim breaks (unactivated venv, stale PATH after a rollback)."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "manju", "--version"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "manju" in proc.stdout


# --------------------------------- round-2: error-contract consistency (F-E*)


def test_waiting_user_is_code_branchable_on_redo(tmp_project, add_shot, monkeypatch):
    """F-E2: the §8.3 spend-gate stop carried code:"error" on redo/voice —
    agents had to string-match the waiting_user: prefix while lock/export
    already shipped the token. Driven deterministically: the engine raises
    the REAL WaitingUser; the pin is the CLI's envelope mapping."""
    import manju.cli as cli
    from manju.build.graph import WaitingUser

    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)

    def _stop(*a, **kw):
        raise WaitingUser("waiting_user: 预估花费 3.0 CNY 命中 ask_before="
                          "expensive_generation — 确认后重试:manju redo S001 --yes",
                          3.0, "CNY")

    monkeypatch.setattr("manju.build.graph.redo_shot", _stop)
    res = runner.invoke(app, ["redo", "S001", "--json"])
    assert res.exit_code == 1
    doc = json.loads(res.output)
    assert doc["code"] == "waiting_user"
    assert "waiting_user:" in doc["error"]


def test_select_and_build_errors_carry_stable_codes(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    doc = json.loads(runner.invoke(app, ["select", "S001", "--json"]).output)
    assert doc["code"] == "bad_args"
    doc = json.loads(runner.invoke(app, ["select", "S001", "take_99", "--json"]).output)
    assert doc["code"] == "not_found"
    doc = json.loads(runner.invoke(app, ["build", "--target", "bogus", "--json"]).output)
    assert doc["code"] == "bad_args"
    assert "proxy|final" in doc["error"]  # F-E3: names the valid set now


def test_export_and_qc_survive_malformed_timeline(tmp_project, monkeypatch):
    """F-E4/F-E5: a malformed timeline/timeline.json died as a raw
    JSONDecodeError traceback through export and qc — and --json emitted
    NOTHING. Now: the truth_parse_error contract, naming the rebuild."""
    tl_dir = tmp_project.root / "timeline"
    tl_dir.mkdir(exist_ok=True)
    (tl_dir / "timeline.json").write_text("{bad json", encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)
    for cmd in (["export", "--srt", "--yes", "--json"], ["qc", "--json"]):
        res = runner.invoke(app, cmd)
        assert res.exit_code == 1, cmd
        combined = res.output + (res.stderr or "")
        assert "Traceback" not in combined, cmd
        doc = json.loads(res.output)
        assert doc["code"] == "truth_parse_error", cmd
        assert "manju build" in doc["error"], cmd


def test_migrate_survives_corrupt_project_yaml(tmp_project, monkeypatch):
    """F-E6: migrate inspect crashed raw on a hand-edited-broken project.yaml
    while its sibling status had already been fixed — same clean line now."""
    (tmp_project.root / "project.yaml").write_text(
        "name: x\n  broken: [\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["migrate", "inspect", "--json"])
    assert res.exit_code == 1
    assert "Traceback" not in res.output + (res.stderr or "")
    doc = json.loads(res.output)
    assert doc["code"] == "truth_parse_error"


def test_select_survives_malformed_shot_yaml(tmp_project, add_shot, monkeypatch):
    """F-E7: _require_shot caught only ProjectError — a broken shots/<id>.yaml
    escaped as a raw traceback through select/redo/voice/prompt."""
    add_shot(tmp_project, "S001")
    (tmp_project.root / "shots" / "S001.yaml").write_text(
        "id: S001\n  broken: [\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["select", "S001", "take_01", "--json"])
    assert res.exit_code == 1
    assert "Traceback" not in res.output + (res.stderr or "")
    doc = json.loads(res.output)
    assert doc["code"] == "truth_parse_error"


# ------------------------- intuitiveness wave: the ONE per-shot next action


def _st(project, sid):
    from manju.build.stale import evaluate_all

    return {s.shot_id: s for s in evaluate_all(project)}[sid]


def test_next_action_ladder_missing_select_ok(tmp_project, add_shot, make_take):
    """The owner's real question — 这个镜头现在需要我做什么 — answered by ONE
    resolver folding the per-shot state machines, most-blocking first, always
    naming the exact command (and the undo, where a choice can be wrong)."""
    from manju.build.status import shot_next_action

    add_shot(tmp_project, "S001")
    st = _st(tmp_project, "S001")
    current_spec = st.spec_hash  # MISSING reports the CURRENT spec hash
    act = shot_next_action(tmp_project, "S001", state=st.state.value,
                           selected_take=st.selected_take)
    assert act["key"] == "missing"
    assert "manju build" in act["action"]

    t1 = make_take(tmp_project, "S001", "h1")
    make_take(tmp_project, "S001", "h2")
    st = _st(tmp_project, "S001")
    act = shot_next_action(tmp_project, "S001", state=st.state.value,
                           selected_take=st.selected_take)
    assert act["key"] == "select"
    assert f"manju select S001" in act["action"]
    assert "rollback shot" in act["action"]  # the undo is named where it applies

    from manju.core.writes import select_take_checked
    from manju.runtime.buildlock import build_lock

    with build_lock(tmp_project.root, actor="human"):
        select_take_checked(tmp_project, "S001", t1.name, actor="human", via="cli")
    st = _st(tmp_project, "S001")
    act = shot_next_action(tmp_project, "S001", state=st.state.value,
                           selected_take=st.selected_take)
    # fake takes carry an arbitrary spec_hash → honestly STALE, and the rung
    # says so while noting the selection still stands (§4.3)
    assert act["key"] == "stale"
    assert "manju redo S001" in act["action"]
    assert "现选依然可用" in act["action"]

    # the fallthrough: a fresh shot with nothing pending → 无需动作 (the
    # state parameter is a plain string by design — every surface passes its
    # own already-computed value; drive the rung directly)
    del current_spec  # the sidecar-vs-computed comparison form is generation's
    act = shot_next_action(tmp_project, "S001", state="fresh",
                           selected_take=t1.name)
    assert act["key"] == "ok"
    assert "无需动作" in act["action"]


def test_next_action_blocker_annotation_outranks_review(tmp_project, add_shot, make_take):
    """A blocker annotation bound to the CURRENT selected media is the most
    actionable fact after the build states — and a STALE one (media since
    replaced) must NOT nag."""
    from manju.board.server import API_ACTIONS
    from manju.build.status import shot_next_action
    from manju.core.writes import select_take_checked
    from manju.runtime.buildlock import build_lock

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    with build_lock(tmp_project.root, actor="human"):
        select_take_checked(tmp_project, "S001", take.name, actor="human", via="cli")
    API_ACTIONS["annotate"](tmp_project, {
        "shot": "S001", "take": take.name, "text": "第2帧穿帮", "severity": "blocker",
        "frame": 2})
    st = _st(tmp_project, "S001")
    act = shot_next_action(tmp_project, "S001", state=st.state.value,
                           selected_take=st.selected_take)
    assert act["key"] == "blocker"
    assert "blocker" in act["action"]

    # replace the media bytes → the binding is stale → no nag from it
    info = tmp_project.get_take("S001", take.name)
    info.media_path.write_bytes(b"replaced-bytes")
    act = shot_next_action(tmp_project, "S001", state=st.state.value,
                           selected_take=st.selected_take)
    assert act["key"] != "blocker"


def test_next_action_qc_and_voice_rungs(tmp_project, add_shot, make_take):
    from manju.build.status import shot_next_action
    from manju.core.writes import select_take_checked
    from manju.runtime.buildlock import build_lock

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    with build_lock(tmp_project.root, actor="human"):
        select_take_checked(tmp_project, "S001", take.name, actor="human", via="cli")
    st = _st(tmp_project, "S001")
    act = shot_next_action(tmp_project, "S001", state=st.state.value,
                           selected_take=st.selected_take,
                           qc_error_shots=frozenset({"S001"}))
    assert act["key"] == "qc"
    assert "manju repair" in act["action"]

    act = shot_next_action(tmp_project, "S001", state=st.state.value,
                           selected_take=st.selected_take, voice_state="missing")
    assert act["key"] == "voice"
    assert "manju voice S001" in act["action"]


def test_status_cli_lists_the_todo_lines(tmp_project, add_shot, make_take, monkeypatch):
    """`manju status` — the takeover surface — now answers per shot, not only
    per project: a 待办 section naming each shot's ONE action (and the JSON
    envelope gains an additive `todo` list for agents)."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "a")
    make_take(tmp_project, "S001", "b")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0
    assert "待办" in res.output
    assert "manju select S001" in res.output

    doc = json.loads(runner.invoke(app, ["status", "--json"]).output)
    todo = {t["shot"]: t for t in doc["todo"]}
    assert todo["S001"]["key"] == "select"


def test_review_and_board_cards_show_the_next_action(gui, tmp_project, add_shot, make_take):
    from manju.board.board import render_board

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "a")
    make_take(tmp_project, "S001", "b")

    html = urllib.request.urlopen(
        f"http://127.0.0.1:{gui.port}/review", timeout=15).read().decode("utf-8")
    assert "下一步" in html and "manju select S001" in html

    served = render_board(tmp_project, serve=True)
    assert "下一步" in served and "manju select S001" in served
    # static board (the shareable handoff) intentionally does NOT carry the
    # owner's workbench todo line
    static = render_board(tmp_project, serve=False)
    assert "下一步" not in static


def test_installer_offers_the_click_first_entry():
    """Intuitiveness wave: a click-first owner needs a Start-Menu entry —
    opt-in (-CreateShortcut), a per-user .lnk FILE (no registry, §4.2),
    targeting the launcher's gui mode (workspace picker outside a project)."""
    src = Path("scripts/windows/install-manju.ps1").read_text(encoding="utf-8")
    assert "$CreateShortcut" in src
    assert "Start Menu" in src
    assert '"gui"' in src  # the shortcut opens the workbench, not a bare shell
    block = src.split("if ($CreateShortcut)")[1].split("# ---")[0]
    assert "reg" not in block.lower().replace("programs", "")  # no registry path


# ---------------------------------------------- GPT-analysis wave (2026-07-14)
# Two kernels distilled from the owner-supplied external analysis, everything
# else in it was already landed or rejected (DECISIONS #45):
#   * `next_step_key` — the project-level 下一步 gets a STABLE machine token
#     next to its Chinese sentence, completing the key+text contract the
#     per-shot `todo` entries already follow (agents never parse prose).
#   * stale-tab guard — the workspace server binds ONE switchable project, so
#     a tab rendered for project 甲 could silently READ and WRITE project 乙
#     after any other tab switched. Pages now carry their project identity
#     (`manju-project` meta, stamped by the ONE owner in _send_text), the
#     shared JS echoes it as X-Manju-Project, and do_POST refuses a mismatch
#     with 409 project_switched instead of mutating the wrong project.


def test_next_step_key_is_the_stable_machine_token(tmp_project, add_shot, make_take):
    from manju.build.status import project_status

    assert project_status(tmp_project)["next_step_key"] == "create_shots"

    add_shot(tmp_project, "S001")
    assert project_status(tmp_project)["next_step_key"] == "build_missing"

    make_take(tmp_project, "S001", "h1")
    make_take(tmp_project, "S001", "h2")
    st = project_status(tmp_project)
    assert st["next_step_key"] == "select"
    # the key and the sentence describe the SAME rung
    assert "manju select" in st["next_step"]


def test_state_payload_carries_next_step_key(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    data = json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{gui.port}/api/state", timeout=15).read())
    assert data["next_step_key"] == "build_missing"


def _get_raw(server, path):
    with urllib.request.urlopen(
            f"http://127.0.0.1:{server.port}{path}", timeout=15) as resp:
        return resp.status, resp.read().decode("utf-8")


def test_project_identity_is_stamped_into_every_html_page(gui, tmp_project, add_shot):
    from manju.gui.state import project_identity

    tok = project_identity(tmp_project)
    assert tok and len(tok) == 12
    add_shot(tmp_project, "S001")
    for path in ("/", "/review"):
        _, html = _get_raw(gui, path)
        assert f'<meta name="manju-project" content="{tok}">' in html, path

    _, body = _get_raw(gui, "/api/project-id")
    data = json.loads(body)
    assert data["token"] == tok and data["name"]

    _, body = _get_raw(gui, "/api/state")
    assert json.loads(body)["project_token"] == tok


def _post_h(server, path, body, headers):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}",
        data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Manju-Token", server.token)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_stale_tab_write_is_refused_after_project_switch(tmp_path, add_shot, make_take):
    """The trust question the external analysis put best: 我正在审核 A 项目,
    屏幕上这次点击到底落进了哪个项目?A stale tab's write must be REFUSED,
    never silently applied to whatever project is bound now."""
    from manju.core.container import Project
    from manju.gui.server import create_server, discover_workspace
    from manju.gui.state import project_identity

    ws = tmp_path / "studio"
    ws.mkdir()
    a = Project.create(ws / "甲", git_init=False)
    b = Project.create(ws / "乙", git_init=False)
    server = create_server(a, host="127.0.0.1", port=0,
                           workspace=discover_workspace(ws))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        tok_a, tok_b = project_identity(a), project_identity(b)
        assert tok_a != tok_b

        # the switch response hands the SPA its new identity to adopt
        status, data = _post_h(server, "/api/switch", {"slug": "乙"}, {})
        assert status == 200 and data["project_token"] == tok_b

        # a tab still holding 甲's identity: its write is refused, named, coded
        add_shot(b, "S001")
        take = make_take(b, "S001", "h")
        status, data = _post_h(server, "/api/select",
                               {"shot": "S001", "take": take.name},
                               {"X-Manju-Project": tok_a})
        assert status == 409 and data["code"] == "project_switched"
        assert "乙" in data["error"]  # names where the server actually is
        assert b.load_shot("S001").status.selected_take is None  # nothing landed

        # the same write with the CURRENT identity goes through
        status, data = _post_h(server, "/api/select",
                               {"shot": "S001", "take": take.name},
                               {"X-Manju-Project": tok_b})
        assert status == 200

        # switching is exempt — a stale tab may always ask to switch/open
        status, _ = _post_h(server, "/api/switch", {"slug": "甲"},
                            {"X-Manju-Project": tok_b})
        assert status == 200

        # header-less clients (tests, curl, older pages) keep the old behaviour
        status, _ = _post_h(server, "/api/lock",
                            {"shot": "S001", "field": "dialogue.text"}, {})
        assert status != 409
    finally:
        server.shutdown()
        server.close()


def test_page_js_echoes_the_project_identity():
    """Source pins: all three JS owners (shared page helpers, the SPA, the
    glossary/mode script) read the manju-project meta, echo it on mutating
    POSTs and block the page once the server's project no longer matches."""
    from manju.gui.common_js import render_common_js
    from manju.gui import glossary as _glossary
    from manju.gui.page import render_js

    common = render_common_js()
    assert 'manju-project' in common
    assert '"X-Manju-Project"' in common
    assert 'project_switched' in common
    assert '/api/project-id' in common  # the read-side watchdog poll

    spa = render_js()
    assert 'manju-project' in spa
    assert '"X-Manju-Project"' in spa
    assert 'project_switched' in spa

    glos = _glossary._GLOSSARY_JS
    assert 'manju-project' in glos and '"X-Manju-Project"' in glos


# ------------------------------------------------ Continuity wave (2026-07-14)
# The owner re-issued the intuitiveness mandate. This round's finding: the
# surfaces answer "现在做什么" (#44) and "这是哪个项目" (#45), but not the two
# questions a RETURNING owner asks first — "我上次做到哪了?" and "我的东西
# 备份了没?". Both land as one-line facts on the surfaces that already own
# them: status (the takeover entry point) and doctor (the health surface).


def test_humanize_age_phrases():
    from datetime import datetime, timezone

    from manju.core.events import humanize_age

    now = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
    assert humanize_age("2026-07-14T11:59:40+00:00", now) == "刚刚"
    assert humanize_age("2026-07-14T11:30:00+00:00", now) == "30 分钟前"
    assert humanize_age("2026-07-14T04:00:00+00:00", now) == "8 小时前"
    assert humanize_age("2026-07-11T12:00:00+00:00", now) == "3 天前"
    assert humanize_age("2026-07-11T12:00:00", now) == "3 天前"  # naive → UTC
    assert humanize_age("garbage", now) == ""     # hand-edited log line
    assert humanize_age("2027-01-01T00:00:00+00:00", now) == ""  # 未来 = 不猜


def test_status_shows_the_last_activity_anchor(tmp_project, monkeypatch):
    from manju.core.events import append_event

    append_event(tmp_project.root, "human", "select", {"shot": "S007"})
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0, res.output
    assert "上次动作" in res.output
    assert "select S007" in res.output and "(human)" in res.output

    # no history (events.jsonl gone) → no anchor line, no crash
    (tmp_project.root / "events.jsonl").unlink()
    res = runner.invoke(app, ["status"])
    assert res.exit_code == 0, res.output
    assert "上次动作" not in res.output


def test_pack_records_the_marker_and_doctor_reads_backup_age(
        tmp_project, tmp_path, monkeypatch):
    """The marker lives in `.manju/` (PACK_EXCLUDE) ON PURPOSE: pack must
    stay READ-ONLY on the tree it archives — the W5 pins hold two packs of
    one tree byte-identical, so the record may never touch events.jsonl."""
    from manju.build.doctor import run_doctor

    monkeypatch.chdir(tmp_project.root)

    # before any pack: the advisory row points at the command, never gates
    row = next(c for c in run_doctor(tmp_project)["checks"]
               if c["name"] == "backup")
    assert row["ok"] is True and "manju pack" in row["line"]

    events_before = (tmp_project.root / "events.jsonl").read_bytes() \
        if (tmp_project.root / "events.jsonl").exists() else b""
    out = tmp_path / "备份.manjupkg"
    res = runner.invoke(app, ["pack", "--out", str(out)])
    assert res.exit_code == 0, res.output

    marker = json.loads(
        (tmp_project.runtime_dir / "last_pack.json").read_text(encoding="utf-8"))
    assert marker["name"] == "备份.manjupkg" and marker["ts"]
    # read-only principle: the packed TREE is untouched by the pack itself
    events_after = (tmp_project.root / "events.jsonl").read_bytes() \
        if (tmp_project.root / "events.jsonl").exists() else b""
    assert events_after == events_before

    row = next(c for c in run_doctor(tmp_project)["checks"]
               if c["name"] == "backup")
    assert row["ok"] is True
    assert "上次整包备份" in row["line"] and "刚刚" in row["line"]


def test_doctor_backup_row_warns_past_two_weeks(tmp_project):
    from datetime import datetime, timedelta, timezone

    from manju.build.doctor import run_doctor

    old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(timespec="seconds")
    tmp_project.runtime_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.runtime_dir / "last_pack.json").write_text(
        json.dumps({"ts": old, "name": "old.manjupkg"}, ensure_ascii=False),
        encoding="utf-8")
    row = next(c for c in run_doctor(tmp_project)["checks"]
               if c["name"] == "backup")
    assert row["ok"] is True          # advisory: NEVER gates doctor's ok
    assert "⚠" in row["line"] and "manju pack" in row["line"]
    assert "15 天前" in row["line"]


# ---------------------------------------------- Convenience wave (2026-07-14)
# The 7-hour convenience mandate, wave 1: the owner types shot/take ids
# dozens of times a day and the CLI accepted exactly one spelling. The
# forgiving resolvers (cli._resolve_shot_arg / _resolve_take_arg — one owner
# each) match shorthand against EXISTING entities only: s14/S14/14 → S014,
# 3 → take_03. Ambiguity fails structured (never guess — the UNKNOWN
# discipline applied to intent), no match passes through unchanged, and the
# resolution echo rides STDERR so --json stdout stays machine-pure.


def test_shot_shorthand_resolves_case_and_number(tmp_project, add_shot, make_take, monkeypatch):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h1")
    make_take(tmp_project, "S001", "h2")
    monkeypatch.chdir(tmp_project.root)

    res = runner.invoke(app, ["select", "s1", "2"])
    assert res.exit_code == 0, res.output
    assert tmp_project.load_shot("S001").status.selected_take == "take_02"


def test_shot_shorthand_json_stdout_stays_pure(tmp_project, add_shot, make_take, monkeypatch):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h1")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["select", "1", "1", "--json"])
    assert res.exit_code == 0, res.output
    json.loads(res.stdout)                # echo must NOT pollute stdout
    assert "S001" in res.stderr and "take_01" in res.stderr  # taught canonically


def test_shot_shorthand_ambiguity_fails_structured_never_guesses(
        tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    # a second id with the same numeric part → "1" is genuinely ambiguous
    src = (tmp_project.root / "shots" / "S001.yaml").read_text(encoding="utf-8")
    (tmp_project.root / "shots" / "SC001.yaml").write_text(
        src.replace("S001", "SC001"), encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)

    res = runner.invoke(app, ["select", "1", "take_01", "--json"])
    assert res.exit_code == 1
    doc = json.loads(res.output)
    assert doc["code"] == "bad_args"
    assert "S001" in doc["error"] and "SC001" in doc["error"]


def test_shot_shorthand_no_match_passes_through_unchanged(
        tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["redo", "S999", "--json"])
    assert res.exit_code == 1
    doc = json.loads(res.output)
    assert "S999" in doc["error"]        # the structured not-found, untouched


def test_resolver_units_cover_the_documented_rules(tmp_project, add_shot, make_take):
    from manju.cli import _resolve_shot_arg, _resolve_take_arg

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S014")
    make_take(tmp_project, "S014", "h")

    assert _resolve_shot_arg(tmp_project, "S014") == "S014"   # exact: untouched
    assert _resolve_shot_arg(tmp_project, "s14") == "S014"    # casefold+pad
    assert _resolve_shot_arg(tmp_project, "14") == "S014"     # bare number
    assert _resolve_shot_arg(tmp_project, "S05") == "S05"     # no match: as-is
    assert _resolve_take_arg(tmp_project, "S014", "1") == "take_01"
    assert _resolve_take_arg(tmp_project, "S014", "TAKE_01") == "take_01"
    assert _resolve_take_arg(tmp_project, "S014", "9") == "9"  # no match: as-is


def test_select_without_take_lists_the_candidates(tmp_project, add_shot, make_take, monkeypatch):
    """The next keystroke must be obvious: no-take now shows the same listing
    the bad-take branch always had, plus the numeric shorthand."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h1")
    make_take(tmp_project, "S001", "h2")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["select", "S001", "--json"])
    assert res.exit_code == 1
    doc = json.loads(res.stdout)
    assert doc["code"] == "bad_args"
    assert "take_01" in doc["error"] and "take_02" in doc["error"]
    assert "select S001 1" in doc["error"]   # teaches the shorthand


# ------------------------------------------- Convenience wave 2 (2026-07-14)
# The friction auditor's cross-cutting finding, personally re-verified at
# every cited site: the GENERATE/EDIT verbs (redo/select/voice/align/
# repair) and the recovery/deliver surfaces (tasks/exports/transcribe) each
# terminated at a fact with no next keystroke — while status/new/review
# already speak the next-command idiom. One clause each, same idiom.


def test_select_success_names_the_build_step(tmp_project, add_shot, make_take, monkeypatch):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h1")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["select", "S001", "take_01"])
    assert res.exit_code == 0, res.output
    assert "manju build" in res.output      # the change must reach the film


def test_redo_batch_summary_names_the_pick_step(capsys):
    from manju.cli import _print_batch_result

    class _R:
        ran = ["S001"]
        skipped: list = []
        failed: list = []
        takes = {"S001": ["take_03"]}
        estimated_cost = 0.0
        currency = None

    _print_batch_result(_R(), "redo")
    assert "manju select" in capsys.readouterr().out
    _print_batch_result(_R(), "voice")
    assert "manju build" in capsys.readouterr().out
    _R.ran = []
    _print_batch_result(_R(), "redo")   # nothing ran → no hint noise
    assert "manju select" not in capsys.readouterr().out


def test_tasks_failed_row_prints_the_verbatim_retry(tmp_project, add_shot, monkeypatch):
    from manju.runtime.state import RuntimeState

    add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        rid = st.record_run(shot="S001", provider="cloud_test",
                            status="failed", error="boom")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["tasks"])
    assert res.exit_code == 0, res.output
    assert f"manju tasks retry {rid}" in res.output


def test_exports_missing_rows_name_their_command(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["exports"])
    assert res.exit_code == 0, res.output
    assert "→ manju build --target final" in res.output
    assert "→ manju export --srt" in res.output


def test_repair_auto_zero_work_stays_noise_free(tmp_project, monkeypatch):
    tmp_project.reports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.reports_dir / "repair_plan.yaml").write_text(
        "actions: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["repair", "--auto"])
    assert res.exit_code == 0, res.output
    assert "repaired 0" in res.output
    assert "manju build" not in res.output   # hints only when something happened


def test_transcribe_names_the_align_chain_with_actual_values(tmp_project, monkeypatch):
    (tmp_project.root / "media" / "imports").mkdir(parents=True, exist_ok=True)
    (tmp_project.root / "media" / "imports" / "素材.mp4").write_bytes(b"x")
    srt = tmp_project.root / "手打.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["transcribe", "media/imports/素材.mp4",
                              "--from-srt", str(srt)])
    assert res.exit_code == 0, res.output
    assert "manju align --media media/imports/素材.mp4" in res.output
    assert "--from-srt captions/transcripts/素材.srt" in res.output


# ------------------------------------------- Convenience wave 3 (2026-07-14)


def test_review_keyboard_gains_approve_but_never_spend(tmp_project, add_shot, make_take):
    """`a` fires the card's own 通过 button (one click path — CAS refresh and
    queue advance stay owned there); 重做 deliberately has NO key: a spend
    action never hides behind a single keystroke."""
    from manju.gui.pages import render_pages_js, render_review

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    html = render_review(tmp_project, "tok")
    assert "a 通过" in html                       # the legend teaches it
    js = render_pages_js()
    assert 'e.key === "a"' in js and 'qapprove' in js
    # no keydown branch fires the redo (spend) action
    keyblock = js.split('addEventListener("keydown"')[1]
    key_branches = [seg.split(")", 1)[0] for seg in keyblock.split("e.key === ")[1:]]
    assert not any("redo" in b for b in key_branches)


def test_package_and_masters_point_at_exports():
    src = Path("src/manju/cli.py").read_text(encoding="utf-8")
    assert src.count("交付状态一览: manju exports") == 2


# ------------------------------------------- Convenience wave 4 (2026-07-14)
# The GUI click-flow audit (second auditor, second lens), each site
# re-verified: bulk actions the engine already served but pages never wired,
# a spend click without the house confirm, state lost to a full reload, and
# two cross-page round-trips that dropped their context.


def test_review_queue_bar_offers_batch_redo_stale(tmp_project, add_shot, make_take):
    from manju.gui.pages import render_pages_js, render_review

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    html = render_review(tmp_project, "tok")
    assert 'id="rv-redo-stale"' in html
    js = render_pages_js()
    assert '/api/redo-batch' in js
    seg = js.split('rv-redo-stale')[1].split("rv-queue-toggle")[0]
    # §8.3 honesty: the posted body is shots-only — the page never
    # self-approves spend (assume_yes stays a workbench plan-modal decision)
    assert "{ shots: stale }" in seg
    assert "window.confirm" in seg


def test_review_shot_id_links_into_its_lab(tmp_project, add_shot, make_take):
    from manju.gui.pages import render_review

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    html = render_review(tmp_project, "tok")
    assert 'href="/lab?shot=S001"' in html


def test_board_redo_confirms_and_select_updates_in_place(tmp_project, add_shot, make_take):
    from manju.board.board import render_board
    from manju.core.writes import select_take_checked
    from manju.runtime.buildlock import build_lock

    add_shot(tmp_project, "S001")
    t1 = make_take(tmp_project, "S001", "h1")
    make_take(tmp_project, "S001", "h2")
    with build_lock(tmp_project.root, actor="human"):
        select_take_checked(tmp_project, "S001", t1.name, actor="human", via="cli")
    served = render_board(tmp_project, serve=True)
    # the spend click carries the house confirm; select flips in place
    assert "将产生新的生成花费" in served
    assert 'act === "select"' in served and "location.reload(); return;" in served
    # the ★ button carries its identity so the flip is lossless
    assert 'class="btn btn-sel" disabled data-shot="S001" data-take="take_01"' \
        in served.replace("\n", "")


def test_caption_clip_deep_links_to_its_cue_row():
    from manju.gui.edit import _caption_block
    from manju.gui.pages_t import _PAGES_T_JS, _cue_row

    class _C:
        start_ms, end_ms, text = 0, 1000, "你好"

    assert 'href="/subtitles#cue-3"' in _caption_block(_C(), 3, 10)
    assert 'id="cue-7"' in _cue_row({"index": 7, "start_ms": 0, "end_ms": 1,
                                     "text": "x"})
    assert "landOnCue" in _PAGES_T_JS and "#cue-" in _PAGES_T_JS


def test_storyboard_batch_bar_wires_redo_and_voice(tmp_project, add_shot):
    from manju.gui.storyboard import render, render_storyboard_js

    add_shot(tmp_project, "S001")
    html = render(tmp_project, "tok")
    assert 'id="sb-redo-all"' in html and 'id="sb-voice-all"' in html
    js = render_storyboard_js()
    seg = js.split('sb-redo-all" || ev.target.id === "sb-voice-all"')[1]
    seg = seg.split("sb-clear")[0]
    assert '"/api/" + kind + "-batch"' in seg
    assert "window.confirm" in seg
    assert "{ shots: selB }" in seg   # shots-only body — no self-approved spend


# ------------------------------------------------ GUI polish wave (2026-07-14)
# Usability/feel/visual pass over the workbench + server pages. The load-
# bearing fixes: the hidden-vs-display CSS conflict class (the /create skill
# modal shipped permanently covering the page — `.cw-modal{display:flex}`
# beats the UA [hidden] rule), the two toast systems' diverging look, native
# bright-grey Windows scrollbars on the dark palette, pollJob's serialized-
# runner misreport, and the #48b-deferred /exports bulk refresh.


def test_hidden_always_hides_and_create_stage_swap_clears_the_class():
    from manju.gui.create_page import render_create_js
    from manju.gui.page import render_css

    css = render_css()
    # THE one owner: both spellings of "hidden" must beat any later
    # display: rule — per-selector `.foo.hidden` patches are retired.
    assert ".hidden { display: none !important; }" in css
    assert "[hidden] { display: none !important; }" in css
    # the stage swap must clear the server-rendered hidden CLASS on the
    # target editor (the attribute alone never unhid anything).
    js = render_create_js()
    assert 'classList.toggle("hidden", !match)' in js


def test_native_widgets_and_motion_follow_the_os():
    from manju.gui.page import render_css

    css = render_css()
    # Windows renders bright-grey UA scrollbars/controls without this.
    assert "color-scheme: dark" in css
    # every animation/transition is decorative — the OS preference wins.
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_toast_systems_share_one_visual_voice():
    from manju.gui.page import render_css
    from manju.gui.pages import render_pages_css

    app_css = render_css()
    pages_css = render_pages_css()
    assert "@keyframes mj-rise" in app_css
    assert "animation: mj-rise" in app_css.split(".toast {")[1].split("}")[0]
    assert "animation: mj-rise" in pages_css.split(".toast-item {")[1].split("}")[0]
    assert "border-left-color: var(--ok)" in pages_css
    assert "border-left-color: var(--err)" in pages_css


def test_exports_bulk_updates_all_stale_free_kinds(tmp_project):
    from manju.core.models import ShotSpec, TakeSidecar
    from manju.core.spec import compute_spec_hash
    from manju.exporters.srt_ass import export_captions
    from manju.gui.exports_page import render, render_exports_js
    from manju.media.probe import probe_duration_ms
    from manju.timeline.compiler import compile_timeline, gather_compile_input

    # a compiled project with exported captions, then a dialogue edit →
    # srt/ass read 待更新 (the same derivation the engine test suite pins)
    shot = ShotSpec.model_validate({
        "id": "S001", "scene": "convenience_store", "characters": ["linxia"],
        "dialogue": {"speaker": "linxia", "text": "这不可能。"}, "duration": 3})
    tmp_project.save_shot(shot)
    idx = tmp_project.load_index()
    idx.order.append("S001")
    tmp_project.save_index(idx)
    h = compute_spec_hash(shot, tmp_project.load_bible())
    src = tmp_project.root / "_src.mp4"
    src.write_bytes(b"fakevideo")
    info = tmp_project.register_take("S001", src, TakeSidecar(provider="test", spec_hash=h))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", info.name))
    tl = compile_timeline(gather_compile_input(tmp_project, probe_duration_ms))
    tmp_project.save_timeline(tl)
    export_captions(tmp_project, tl)
    tmp_project.update_shot_raw(
        "S001", lambda d: d["dialogue"].__setitem__("text", "完全不同的一句台词"))

    html = render(tmp_project, "tok")
    assert 'id="xc-gen-stale"' in html
    kinds = html.split('data-kinds="')[1].split('"')[0].split(",")
    assert "srt" in kinds and "ass" in kinds
    # priced deliverables never ride a bulk refresh (§8.3: build-only)
    assert "final" not in kinds and "proxy" not in kinds

    js = render_exports_js()
    seg = js.split("function doGenerateAll")[1]
    # sequential, one in flight; reload only on FULL success (partial
    # failure keeps the sticky error toasts readable)
    assert "chain = chain.then" in seg
    assert "okCount === kinds.length" in seg


def test_exports_render_without_stale_has_no_bulk_button(tmp_project):
    from manju.gui.exports_page import render

    assert 'id="xc-gen-stale"' not in render(tmp_project, "tok")


def test_polljob_outlives_the_serialized_queue_on_every_job_page():
    # the runner is SERIALIZED: a generate queued behind a long build takes
    # minutes; the old ~60s cap made every page misreport it as a failure.
    from manju.gui import exports_page, ingest_page, lab_page, series_page

    for mod, js in (
        (exports_page, exports_page._EXPORTS_JS),
        (ingest_page, ingest_page._INGEST_JS),
        (lab_page, lab_page._LAB_JS),
        (series_page, series_page._SERIES_JS),
    ):
        name = mod.__name__
        assert "tries > 1215" in js, name          # ~10min, not ~60s
        assert "tries < 20 ? 100 : 500" in js, name  # adaptive cadence
        assert "仍在排队/运行" in js, name          # null job ≠ failure


# ------------------------------------- GUI polish wave, round 2 (2026-07-14)
# Disposition of an owner-supplied external AI review (the #45 pattern:
# verify every claim, land the bounded kernels, record the rejections).
# Landed kernels: the SPA missed F20's sticky-error discipline; the workbench
# card/filters spoke raw enums (F18 landed on the board only); localStorage
# was keyed by the COLLIDING display name instead of #45's identity token;
# the collapsible panel heads / dropzone were mouse-only divs; take notes
# went through the page-freezing window.prompt.


def test_spa_error_toast_sticky_and_toasts_announced():
    from manju.gui.common_js import render_common_js
    from manju.gui.page import render_js

    js = render_js()
    seg = js.split("const toast = ")[1].split("};")[0]
    # F20 parity: an error stays until clicked; success keeps auto-dismiss
    assert 'if (kind === "err")' in seg and "setTimeout" in seg
    # both toast systems announce politely to assistive tech
    assert 'setAttribute("aria-live", "polite")' in js
    assert 'setAttribute("aria-live", "polite")' in render_common_js()


def test_workbench_speaks_the_state_vocabulary():
    from manju.gui.page import render_js

    js = render_js()
    # F18 discipline on the workbench card: Chinese word, enum on the title
    assert "CK_STATE_ZH[shot.state] || shot.state" in js
    assert "stBadge.title = shot.state" in js
    # filters: urgency ladder (never the alphabet) + toggle semantics
    assert "STATE_FILTER_ORDER" in js
    seg = js.split("function filterChips")[1].split("return bar;")[0]
    assert "aria-pressed" in seg and "CK_STATE_ZH[st] || st" in seg


def test_client_state_is_keyed_by_project_identity():
    from manju.gui.page import render_js
    from manju.gui.pages import render_pages_js

    js = render_js()
    # the stable #45 token, not the colliding display name
    assert '"manju-ui-" + (PROJECT || "unbound")' in js
    assert '"manju-reviewed-" + (PROJECT || lastProjectName)' in js
    # /review continues from the last position, same key discipline
    pjs = render_pages_js()
    assert '"manju-rv-pos-" + (typeof PROJECT === "string" ? PROJECT : "")' in pjs


def test_workbench_keyboard_semantics():
    from manju.gui.page import render_js
    from manju.gui.pages import nav_html

    js = render_js()
    # panel heads and the dropzone act as buttons for the keyboard
    assert "const actAsButton = " in js
    assert js.count("actAsButton(") >= 4  # git/tasks/proposals heads + dropzone
    assert 'setAttribute("aria-expanded"' in js
    # the page-freezing window.prompt is retired from the workbench
    assert "window.prompt" not in js
    # the one nav owner marks the current page for assistive tech
    assert 'aria-current="page"' in nav_html("/review")


# --------------------------------------------- Direction program (#50, 2026-07-14)
# The owner-endorsed direction document, dispositioned: the GUI converges on
# a personal production console — open-and-continue, the review queue as the
# strongest surface, clean AI handoff, use-frequency nav. Presentation and
# client memory only; the engine keeps no UI state.


def test_review_queue_walks_most_blocking_first_and_supports_undo():
    from manju.gui.pages import render_pages_js

    js = render_pages_js()
    seg = js.split("function qPriority")[1].split("var qOrder")[0]
    # a take-less shot cannot take a verdict — it trails everything reviewable
    assert 'if (!s.getAttribute("data-take")) return 4;' in seg
    assert '"needs_selection"' in seg and '"stale"' in seg
    # 好 advances the queue exactly like 通过 always did; u restores what the
    # verdict overwrote (the input's defaultValue = the server-rendered note)
    assert "else if (queueMode)" in js
    assert 'e.key === "u"' in js
    assert "defaultValue" in js and "prevReviewed" in js
    # unreviewed work opens straight into the queue; only the explicit toggle
    # persists as a preference (a default must never silently become one)
    assert "manju-rv-queue-" in js
    assert 'savedQ === "1" || (savedQ === null && unreviewed > 0)' in js


def test_review_legend_teaches_undo(tmp_project, add_shot, make_take):
    from manju.gui.pages import render_review

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    assert "u 撤回" in render_review(tmp_project, "tok")


def test_home_opens_with_continue_and_clickable_counts():
    from manju.gui.common_js import render_common_js
    from manju.gui.page import render_js

    js = render_js()
    # the chip reads the per-project trail…
    assert '"manju-last-" + PROJECT' in js
    assert "ck-continue-btn" in js
    # …that every server page writes; the home page never clobbers it
    cjs = render_common_js()
    assert '"manju-last-" + PROJECT' in cjs
    assert 'location.pathname !== "/"' in cjs
    # the cockpit state counts are clickable queues driving the shots filter
    assert 'el("button", "ck-scount"' in js
    assert "stateFilter = k" in js


def test_ai_handoff_copies_structured_context(tmp_project, add_shot, make_take):
    from manju.gui.page import render_js
    from manju.gui.pages import render_pages_js, render_review

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    html = render_review(tmp_project, "tok")
    assert 'data-act="ai-ctx"' in html and "复制给 Claude" in html
    js = render_pages_js()
    seg = js.split('act === "ai-ctx"')[1].split('act === "route"')[0]
    assert '"- shots/" + shot + ".yaml"' in seg
    assert "copyForAI" in seg
    # the workbench failure cards hand over a diagnostic block the same way
    ajs = render_js()
    assert "复制诊断上下文" in ajs
    assert 'shots/" + f.subject + ".yaml"' in ajs


def test_nav_groups_by_frequency_and_keeps_every_link(tmp_project):
    from manju.gui.pages import _NAV, _NAV_GROUPS, nav_html

    # every page lives in exactly one group — _NAV stays the one label owner
    grouped = [h for _, hs in _NAV_GROUPS for h in hs]
    assert sorted(grouped) == sorted(h for h, _ in _NAV)
    pro = nav_html("/review", mode="pro")
    for href, _ in _NAV:  # presentation nests; the DOM keeps every link
        assert f'href="{href}"' in pro
    assert pro.count('class="pnav-group"') == 5  # 工作台 stays a plain pill
    assert pro.count('aria-current="page"') == 1
    beginner = nav_html("/review", mode="beginner")
    assert 'href="/providers"' not in beginner
    # 工具箱 collapses to its one visible page in 新手 mode
    assert ">素材库</a>" in beginner and "工具箱" not in beginner
