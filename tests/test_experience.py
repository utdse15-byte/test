"""Round V agent VG — experience-polish invariants (goal item 7).

Pins the highest-value fixes from the three-column audit
(``docs/EXPERIENCE-AUDIT.md``): the beginner error triples (WHAT/WHY/HOW-to-fix),
the ``--json`` structured-error shape (RFC 7807-ish ``{error, code}``), and the
semantic exit-code contracts (a check that finds errors must exit non-zero).

Driven through the real Typer app via ``CliRunner`` against the conftest
fixtures, plus one ffmpeg-free unit test for the missing-ffmpeg message. Fast.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """cwd-anchor a fresh project so ``_project()`` discovers it from cwd."""
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


# --------------------------------------------- error triples (what/why/how-fix)


def test_not_in_project_error_names_the_two_ways_out(tmp_path, monkeypatch):
    """The #1 first-run error must say WHAT (not a project) and HOW (cd or new)."""
    monkeypatch.chdir(tmp_path)  # an empty, non-project directory
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "manju new" in result.output  # one way out
    assert ".manju" in result.output     # the other (cd into a project dir)


def test_select_missing_take_names_available_takes(in_project, add_shot, make_take):
    """Picking a take that does not exist should list the takes that DO — the
    concrete how-to-fix, not just 'no such take'."""
    add_shot(in_project, "S001")
    take = make_take(in_project, "S001", "h1")
    result = runner.invoke(app, ["select", "S001", "does-not-exist"])
    assert result.exit_code == 1
    assert "现有 takes" in result.output
    assert take.name in result.output


def test_voice_without_dialogue_points_at_the_field(in_project, add_shot):
    """No dialogue → the error names the exact field to write (dialogue.text)."""
    add_shot(in_project, "S001", dialogue={"speaker": "linxia", "text": ""})
    result = runner.invoke(app, ["voice", "S001"])
    assert result.exit_code == 1
    assert "dialogue.text" in result.output


def test_missing_ffmpeg_message_is_a_triple(monkeypatch):
    """A missing ffmpeg binary is rewritten from a raw OSError traceback into the
    what/why/how-to-fix triple (install command + a pointer to `manju doctor`)."""
    from manju.media import ffmpeg

    monkeypatch.setattr(ffmpeg, "FFMPEG", "definitely-not-a-real-binary-xyz-9000")
    with pytest.raises(ffmpeg.MediaError) as ei:
        ffmpeg.run_ffmpeg(["-i", "in.mp4", "out.mp4"])
    msg = str(ei.value)
    assert "ffmpeg" in msg
    assert ("brew install ffmpeg" in msg) or ("apt install ffmpeg" in msg)
    assert "doctor" in msg  # the recovery check


# ------------------------------------------- structured --json error (RFC 7807)


def test_fail_under_json_emits_error_object(in_project, add_shot):
    """Under --json, a failure is a parseable {error, code} object on stdout —
    not colored prose — so an agent branches on it whether the command won or lost."""
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["select", "S001", "nope", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert set(payload) >= {"error", "code"}
    assert isinstance(payload["error"], str) and payload["error"].strip()
    assert isinstance(payload["code"], str) and payload["code"]


def test_not_in_project_json_has_stable_machine_code(tmp_path, monkeypatch):
    """The #1 error carries a STABLE code an agent can branch on, not just a
    human string that may be reworded later."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == "no_project"


def test_json_success_still_parses(in_project):
    """Sanity: --json success is still clean JSON on stdout (the error path did
    not leak prose into the machine channel)."""
    result = runner.invoke(app, ["check", "--json"])
    assert result.exit_code == 0
    json.loads(result.output)  # must not raise


# ---------------------------------------------------- semantic exit-code contract


def test_check_exits_zero_when_clean(in_project, add_shot):
    add_shot(in_project, "S001")
    assert runner.invoke(app, ["check"]).exit_code == 0


def test_check_exits_nonzero_on_error(in_project, add_shot):
    """A check that finds a hard error must exit non-zero (agents gate on it)."""
    add_shot(in_project, "S001")
    (in_project.root / "shots" / "S001.yaml").write_text(
        "this: is: not: valid: yaml: [broken\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 1
    # and the same non-zero holds under --json (the machine channel agrees)
    jresult = runner.invoke(app, ["check", "--json"])
    assert jresult.exit_code == 1
    assert json.loads(jresult.output)["ok"] is False


def test_prompt_check_clean_project_exits_zero(in_project, add_shot):
    """prompt --check is a lint gate: clean shots → exit 0 (the success direction
    of the same contract check/qc enforce on the failing side)."""
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["prompt", "--check"])
    assert result.exit_code == 0
