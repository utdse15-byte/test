"""TRISURFACE fix wave — the mechanical/guidance batch (F-03/04/05/12/14/16/
17/21/23/24, R2-2/R2-3), each red-first against the behavior the field test
recorded in TRISURFACE_TEST_2026-07-29.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.core.models import TakeSidecar
from manju.core.spec import compute_spec_hash


# ---------------------------------------------------------- F-03 None LUFS


def test_masters_row_never_prints_none_lufs():
    """A silent stem records no loudness; the shared exportstatus row said
    ``实测 integrated None LUFS / TP None dBTP`` — "measured: None" is a
    contradiction (the CLI `manju masters` learned 静音 in the prior wave;
    this is the SAME fact's other formatter)."""
    from manju.build.exportstatus import _loudness_clause

    silent = _loudness_clause({})
    assert "None" not in silent
    assert "静音" in silent or "silent" in silent

    measured = _loudness_clause({"integrated_lufs": -19.68, "true_peak_dbtp": -7.75})
    assert "-19.68" in measured and "-7.75" in measured
    assert "None" not in measured


# ------------------------------------------------- F-04 /create double include


def test_create_page_loads_webclient_exactly_once(tmp_project):
    """GLOSSARY_HEAD already carries /webclient.js; a second include made every
    /create load throw "Identifier 'ManjuApiError' has already been declared"
    — the exact bug pages_t.py documents and fixed for its three pages."""
    from manju.gui.create_page import render_create

    html = render_create(tmp_project, "tok")
    assert html.count('src="/webclient.js"') == 1


# ---------------------------------------------------- F-14 board 0.0 None


def test_board_spend_line_never_renders_none_currency(tmp_project, add_shot):
    """An empty ledger yields currency=None; ``.get("currency", "")`` does not
    catch an explicit None, so the served board's project tab printed
    ``花费 spend: 0.0 None``."""
    from manju.board.board import _render_project_panel
    from manju.runtime.state import RuntimeState

    add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as state:
        state.record_run(shot="S001", provider="caption_card",
                         status="succeeded", cost=0.0, currency=None)
    html = _render_project_panel(tmp_project)
    assert "None" not in html, html[:400]


# ------------------------------------- F-05 stale loop: point at the candidate


def _fake_take(project, shot_id, spec_hash, name_hint=b"x"):
    src = project.root / "_t.mp4"
    src.write_bytes(b"take-bytes-" + name_hint)
    take = project.register_take(
        shot_id, src, TakeSidecar(provider="test", spec_hash=spec_hash))
    src.unlink()
    return take


def test_stale_action_points_at_a_current_spec_candidate(tmp_project, add_shot):
    """`status` said "spec 已变:manju redo S001 重做" forever — even right
    after that redo had produced a take from the CURRENT spec. Following the
    printed advice looped; the actual next action was `manju select`.

    The candidate's sidecar is fabricated EXACTLY the way the pipeline stamps
    it (SPEC_VERSION + project_root — the field re-verification caught a first
    version of this fix that hashed with defaults and never matched a real
    take)."""
    from manju.build.status import shot_next_action
    from manju.core.spec import SPEC_VERSION

    shot = add_shot(tmp_project, "S001")
    old = _fake_take(tmp_project, "S001", "sha256:old-spec", b"old")
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", old.name))
    current = compute_spec_hash(
        tmp_project.load_shot("S001"), tmp_project.load_bible(),
        version=SPEC_VERSION, project_root=tmp_project.root)
    src = tmp_project.root / "_t.mp4"
    src.write_bytes(b"take-bytes-new")
    tmp_project.register_take(
        "S001", src, TakeSidecar(provider="test", spec_hash=current,
                                 spec_version=SPEC_VERSION))
    src.unlink()

    act = shot_next_action(tmp_project, "S001", state="stale",
                           selected_take=old.name)
    assert act["key"] == "stale"  # agents keep their branch
    assert "manju select S001" in act["action"]
    assert "take_02" in act["action"] or " 2" in act["action"]


def test_stale_action_without_candidate_still_says_redo(tmp_project, add_shot):
    from manju.build.status import shot_next_action

    add_shot(tmp_project, "S001")
    old = _fake_take(tmp_project, "S001", "sha256:old-spec")
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", old.name))
    act = shot_next_action(tmp_project, "S001", state="stale",
                           selected_take=old.name)
    assert act["key"] == "stale"
    assert f"manju redo S001" in act["action"]


# ------------------------------------ F-12 approval refusal names offenders


def test_approval_blocked_message_names_the_offending_scopes():
    from manju.build.baseline import _approval_blocked_message

    msg = _approval_blocked_message([
        {"code": "REQUIRED_EXPORT_STALE", "scope": "export:jianying"},
        {"code": "REQUIRED_EXPORT_STALE", "scope": "export:cover"},
        {"code": "REQUIRED_EXPORT_PROBLEM", "scope": "export:otio"},
    ])
    # every offender is named — "resolve them" without a list sent the owner
    # off to scan the whole table by hand.
    assert "export:jianying" in msg and "export:cover" in msg and "export:otio" in msg
    assert "--accept-known-risk" in msg


# ---------------------------------------------- F-16 TTS dead-end message


def test_tts_unconfigured_message_is_walkable(monkeypatch):
    """The old text pointed at §8.6 (a doc not in the repo) and named a class
    with no command to type. The message must hand the owner the actual door:
    ``manju providers add``."""
    from manju.providers import tts as tts_mod

    monkeypatch.setattr(tts_mod, "tts_providers", lambda: {})
    with pytest.raises(tts_mod.TtsUnavailable) as exc:
        tts_mod.get_tts_provider(None)
    msg = str(exc.value)
    assert "manju providers add" in msg
    assert "§8.6" not in msg
    # the free keyless path stays named — round 3 (F-09) upgraded it from
    # "hand-edit the adapter line" to the direct `--adapter edge` scaffold.
    assert "--adapter edge" in msg


# ------------------------------------------------- F-17 honest pullsheet note


def test_pullsheet_note_does_not_blame_the_environment(tmp_project, add_shot,
                                                       monkeypatch):
    """The note claimed "no headless-Chromium … in this environment" without
    ever probing — doctor proved Chromium present on the same box. PDF is
    simply not implemented; the note must say that."""
    from typer.testing import CliRunner

    from manju.cli import app

    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(app, ["export", "--pullsheet", "--yes"])
    assert res.exit_code == 0, res.output
    assert "in this environment" not in res.output
    assert "未实现" in res.output


# ---------------------------------------------------- F-21 series cd hint


def test_series_new_next_step_includes_the_cd(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_path)
    res = CliRunner().invoke(app, ["series", "new", "深夜食堂系列"])
    assert res.exit_code == 0, res.output
    assert "cd 深夜食堂系列" in res.output


# ------------------------------------------- F-24a qc brief double command


def test_qc_brief_header_names_the_skill_command_once(tmp_project, add_shot,
                                                      monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    add_shot(tmp_project, "S001")
    _fake_take(tmp_project, "S001", "h")
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01"))
    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(app, ["qc", "brief"])
    assert res.exit_code == 0, res.output
    header = next(ln for ln in res.output.splitlines() if "判读标准" in ln)
    assert header.count("manju skills show visual-qc-review") == 1


# --------------------------------------------- F-24b release scope spacing


def test_release_scope_clause_reads_as_a_count_not_a_decimal():
    from manju.cli import _release_scope_clause

    clause = _release_scope_clause(5)
    assert "0,5" not in ("blockers=0" + clause)
    assert clause.startswith(", ") or clause.startswith(",") is False


# ------------------------------------------ R2-2 bundle outside-path display


def test_display_path_handles_inside_and_outside(tmp_project):
    """`exports --bundle --output <项目外>` WROTE the zip then stacktraced on
    project.relpath() for the success line. Display must degrade to the
    absolute path, never raise."""
    from manju.cli import _display_path

    inside = tmp_project.root / "reports" / "d.zip"
    assert _display_path(tmp_project, inside) == "reports/d.zip"
    outside = Path("/tmp") / "trisurface-outside.zip"
    assert _display_path(tmp_project, outside) == str(outside)


# ------------------------------------------------- R2-3 stale-tab 403 text


def test_token_403_message_tells_the_human_what_to_do():
    from manju.gui.server import TOKEN_403_MESSAGE

    assert "X-Manju-Token" in TOKEN_403_MESSAGE  # the mechanism stays named
    assert "刷新" in TOKEN_403_MESSAGE  # …and the human gets a next step


# ------------------------------------------- F-08 the new-shot button's door


def test_funnel_storyboard_advice_points_at_the_page_that_has_the_button(tmp_project):
    """The advice said 分镜页 (the /storyboard PAGE, by the nav's own name) —
    but the 新建镜头 button lives on the workbench home's shots panel, and
    /storyboard has no create affordance at all. Point at the right door."""
    from manju.build.funnel import funnel_status

    st = funnel_status(tmp_project)
    stage = next(s for s in st["stages"] if s["id"] == "storyboard")
    advice = stage["next_action"]
    assert "manju gui" in advice and "新建镜头" in advice
    assert "分镜页" not in advice  # that page has no such button
    assert "工作台" in advice or "首页" in advice


# ------------------------------------------- F-06 schema errors show shapes


def test_shot_schema_error_shows_the_expected_shape(tmp_project):
    """`action: 一句话` (the natural first guess — bible fields are all free
    text) failed with the raw pydantic "Input should be a valid dictionary or
    instance of Action": what's wrong, never what right looks like. The error
    must show the shape and point at `manju schema`."""
    from manju.core.check import run_check
    from manju.core.yamlio import write_yaml

    write_yaml(tmp_project.shots_dir / "S001.yaml",
               {"id": "S001", "scene": "convenience_store",
                "action": "夜。大雨。", "dialogue": "这不可能。"})
    idx = tmp_project.load_index()
    idx.order.append("S001")
    tmp_project.save_index(idx)

    report = run_check(tmp_project)
    err = next(e for e in report.errors if "S001" in e and "action" in e)
    assert "main" in err and "emotion" in err  # the Action shape, spelled out
    assert "speaker" in err and "text" in err  # …and Dialogue's
    assert "manju schema" in err


# --------------------------------------- F-07 near-miss unknown-key advisory


def test_near_miss_extra_key_gets_an_advisory_warning(tmp_project, add_shot):
    """``extra="allow"`` is deliberate (humans and agents both edit truth
    files) — but it made `duration_ms:` a silent no-op: the shot quietly kept
    duration=auto and nothing anywhere said so. A near-miss of a REAL field
    name now draws a warning (advisory — check stays ok)."""
    add_shot(tmp_project, "S001", duration_ms=5000)

    from manju.core.check import run_check

    report = run_check(tmp_project)
    assert report.ok  # forgiving extras stay forgiving — a warning, not a gate
    hits = [w for w in report.warnings
            if "duration_ms" in w and "duration" in w and "S001" in w]
    assert hits, report.warnings


def test_unrelated_extra_key_stays_silent(tmp_project, add_shot):
    """A key nothing like any real field is the documented free-note use of
    forgiving extras — no nagging."""
    add_shot(tmp_project, "S001", my_private_note="随手记")

    from manju.core.check import run_check

    report = run_check(tmp_project)
    assert not [w for w in report.warnings if "my_private_note" in w]


# ---------------------------------------------------- F-23 dev extra httpx


def test_dev_extra_declares_httpx():
    """Seven test modules import httpx directly; CLAUDE.md's documented dev
    install (`pip install -e ".[dev]"`) collected 7 errors because no extra
    declared it — it arrived only as another extra's transitive dependency."""
    import tomllib

    data = tomllib.loads(Path(__file__).resolve().parents[1]
                         .joinpath("pyproject.toml").read_text(encoding="utf-8"))
    dev = data["project"]["optional-dependencies"]["dev"]
    assert any(d.split(">=")[0].strip() == "httpx" for d in dev), dev
