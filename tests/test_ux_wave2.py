"""UX wave 2 (2026-07-17) behavioral tests.

Items covered here: job-completion notifications (1 — the API-anchored wiring),
n/m generation progress (2), CLI did-you-mean (3), shell-completion opt-in (4),
and the Show-in-Folder endpoint (6). Server behavior is driven for real; the
few frontend checks are anchored to Web-API names (Notification, visibility)
that no behavior-preserving refactor can rename — per tests/CONVENTIONS.md.
"""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest
from typer.testing import CliRunner

import manju.build.graph as graph
import manju.providers.registry as registry_mod
from manju.build.graph import run_build
from manju.cli import app
from manju.gui.server import _reveal_argv, create_server


# --------------------------------------------------------------------------- #
# Item 3: did-you-mean on a mistyped command
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("typo,want", [("bulid", "build"), ("vioce", "voice"),
                                       ("qcc", "qc")])
def test_mistyped_command_suggests_close_match(typo: str, want: str) -> None:
    r = CliRunner().invoke(app, [typo])
    assert r.exit_code != 0
    assert f"manju {want}" in r.output
    assert "你是想输入" in r.output


def test_hopeless_typo_still_errors_cleanly() -> None:
    r = CliRunner().invoke(app, ["zzzzqqqq"])
    assert r.exit_code != 0
    assert "No such command" in r.output


def test_valid_commands_are_unaffected() -> None:
    r = CliRunner().invoke(app, ["--help"])
    assert r.exit_code == 0


# --------------------------------------------------------------------------- #
# Item 4: shell completion is available as an explicit opt-in
# --------------------------------------------------------------------------- #

def test_completion_options_exist() -> None:
    # Pin the REGISTERED option surface, not the rendered --help text (DECISIONS
    # #51a precedent): CI runners render --help in an 80-column ANSI box that
    # wraps/truncates long option names, so scanning r.output for the literal
    # token is terminal-dependent and flakes. The click command's params are
    # terminal-independent and are the real contract.
    from typer.main import get_command

    opts: set[str] = set()
    for p in get_command(app).params:
        opts.update(getattr(p, "opts", []) or [])
    assert "--install-completion" in opts
    assert "--show-completion" in opts


# --------------------------------------------------------------------------- #
# Item 2: generation progress reports shot n/m via on_phase
# --------------------------------------------------------------------------- #

def test_generation_progress_reports_n_of_m(tmp_project, add_shot, monkeypatch) -> None:
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    monkeypatch.setattr(registry_mod, "generate_with_fallback", lambda req, chain: [])

    phases: list[str] = []
    run_build(tmp_project, target="qc", actor="t", assume_yes=True,
              on_phase=phases.append)
    assert "gen:S001 (1/2)" in phases
    assert "gen:S002 (2/2)" in phases


def test_on_phase_coarse_sequence_still_intact(tmp_project, add_shot, make_take) -> None:
    # the pre-existing coarse phases survive the finer-grained additions
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))
    phases: list[str] = []
    result = run_build(tmp_project, target="qc", gen="off", on_phase=phases.append)
    assert result.ok is True
    assert phases[0] == "check"


# --------------------------------------------------------------------------- #
# Item 6: Show in Folder — argv per platform + the endpoint's safety
# --------------------------------------------------------------------------- #

def test_reveal_argv_windows_uses_explorer_select(tmp_path) -> None:
    f = tmp_path / "final_v1.mp4"
    argv = _reveal_argv(f, platform="nt")
    assert argv[0] == "explorer"
    assert argv[1].startswith("/select,") and "final_v1.mp4" in argv[1]


def test_reveal_argv_mac_and_linux(tmp_path) -> None:
    f = tmp_path / "final_v1.mp4"
    f.write_bytes(b"x")
    assert _reveal_argv(f, platform="darwin") == ["open", "-R", str(f)]
    linux = _reveal_argv(f, platform="posix")
    assert linux[0] == "xdg-open" and linux[1] == str(f.parent)


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.close()


def _post(server, path, body):
    url = f"http://127.0.0.1:{server.port}{path}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(body).encode("utf-8"),
        headers={"X-Manju-Token": server.token, "Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_reveal_launches_file_manager_for_project_file(gui, tmp_project, monkeypatch) -> None:
    target = tmp_project.root / "renders" / "final" / "final_v1.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fake")
    launched: list[list[str]] = []

    import subprocess

    class _P:
        def __init__(self, argv, **kw):
            launched.append(list(argv))

    monkeypatch.setattr(subprocess, "Popen", _P)
    status, data = _post(gui, "/api/reveal", {"path": "renders/final/final_v1.mp4"})
    assert status == 200 and data["ok"] is True
    assert len(launched) == 1
    # platform argv differs (explorer selects the FILE, xdg-open opens the
    # DIR) — the contract is: the launch points INSIDE the project at the
    # final's location.
    assert any(str(target.parent) in a for a in launched[0])


def test_reveal_refuses_path_escape(gui, monkeypatch) -> None:
    import subprocess

    launched: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: launched.append(list(argv)))
    status, data = _post(gui, "/api/reveal", {"path": "../../etc"})
    assert status == 400
    assert launched == []  # never launches outside the project


def test_reveal_missing_file_is_404(gui, monkeypatch) -> None:
    import subprocess

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: None)
    status, _data = _post(gui, "/api/reveal", {"path": "renders/nope.mp4"})
    assert status == 404


# --------------------------------------------------------------------------- #
# Item 7: voice preview comparison set (--voices a,b,c)
# --------------------------------------------------------------------------- #

def test_voices_requires_preview(tmp_project, add_shot, monkeypatch) -> None:
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    r = CliRunner().invoke(app, ["voice", "S001", "--voices", "a,b"])
    assert r.exit_code != 0
    assert "--preview" in r.output


def test_voices_previews_each_candidate(tmp_project, add_shot, monkeypatch) -> None:
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    import manju.media.ttspreview as tp

    seen: list[str | None] = []

    def fake_preview(project, shot_id, *, text=None, voice=None, provider=None,
                     assume_yes=False, should_cancel=None):
        seen.append(voice)
        return {"preview": f".manju/webpreview/tts/{voice or 'own'}.mp3",
                "cached": False, "path": None, "provider": "edge"}

    monkeypatch.setattr(tp, "preview_voice", fake_preview)
    r = CliRunner().invoke(app, ["voice", "S001", "--preview",
                                 "--voices", "xiaoxiao,yunjian,yunxi", "--json"])
    assert r.exit_code == 0, r.output
    assert seen == ["xiaoxiao", "yunjian", "yunxi"]  # same line, one per voice
    data = json.loads(r.output)  # --json emits one pretty-printed object
    assert [p["voice"] for p in data["previews"]] == ["xiaoxiao", "yunjian", "yunxi"]


def test_single_preview_json_shape_unchanged(tmp_project, add_shot, monkeypatch) -> None:
    # the pre-existing --preview (no --voices) JSON envelope stays intact
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    import manju.media.ttspreview as tp

    monkeypatch.setattr(tp, "preview_voice",
                        lambda *a, **k: {"preview": "x.mp3", "cached": True,
                                         "path": None, "provider": "edge"})
    r = CliRunner().invoke(app, ["voice", "S001", "--preview", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data == {"preview": "x.mp3", "cached": True, "provider": "edge"}


# --------------------------------------------------------------------------- #
# Item 8: the built-in zero-cost demo project (manju new --demo)
# --------------------------------------------------------------------------- #

@pytest.fixture
def demo_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["new", "雨夜便利店", "--demo"])
    assert r.exit_code == 0, r.output
    from manju.core.container import Project

    return Project(tmp_path / "雨夜便利店.manju")


def test_demo_scaffolds_a_complete_12_shot_story(demo_project) -> None:
    index = demo_project.load_index()
    assert index.order == [f"S{i:03d}" for i in range(1, 13)]
    for sid in index.order:
        shot = demo_project.load_shot(sid)
        assert shot.dialogue.text  # every beat has its line
        assert shot.generation.provider == "caption_card"  # zero-cost pin
    brief = (demo_project.root / "story" / "brief.md").read_text(encoding="utf-8")
    assert "便利店" in brief


def test_demo_passes_check_out_of_the_box(demo_project) -> None:
    from manju.core.check import run_check

    report = run_check(demo_project)
    assert report.ok, report.errors


def test_demo_build_plan_costs_nothing(demo_project) -> None:
    # THE demo promise: a full build is free. The dry-run estimate over all
    # 12 caption_card shots must be exactly zero — a demo must never be one
    # `build --yes` away from real spend.
    result = run_build(demo_project, dry_run=True)
    assert result.ok is True
    assert float(result.estimated_cost or 0.0) == 0.0
    assert len(result.plan) == 12


def test_demo_is_mutually_exclusive_with_preset_and_shots(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["new", "d1", "--demo", "--shots", "3"])
    assert r.exit_code != 0 and "互斥" in r.output
    assert not (tmp_path / "d1.manju").exists()  # refused before creating


# --------------------------------------------------------------------------- #
# Item 1: completion notification wiring (Web-API-anchored presence)
# --------------------------------------------------------------------------- #

def test_notification_wiring_is_present_and_api_anchored() -> None:
    # These names are Web platform APIs (Notification.requestPermission,
    # visibilitychange) — a behavior-preserving refactor cannot rename them,
    # so their presence pins the wiring without pinning implementation text.
    from manju.gui.page import render_page

    # render_page needs a project; the JS is embedded in the page module's
    # source constants — assemble via the page module's script for a project-
    # free check is not exposed, so use the module's file only for the JS.
    import manju.gui.page as page_mod
    from pathlib import Path

    js = Path(page_mod.__file__).read_text(encoding="utf-8")
    assert "Notification.requestPermission" in js
    assert "visibilitychange" in js
    assert "mj-notify-sound" in js  # the opt-in sound is persisted, not default-on