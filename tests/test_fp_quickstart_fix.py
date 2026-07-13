"""FP quickstart-fix — the audit's UX defect: unknown-shot verbs must fail
STRUCTURED, ORPHAN-FREE, and NEVER as a raw traceback.

Optimization audit 2026-07-12, defect 0a (refuter panel: CONFIRMED):
``manju select <shot> --file`` on a project where the shot YAML does not
exist used to register the take FIRST — leaving orphan
``media/gen/<shot>/take_01.*`` on disk — and THEN crash with an uncaught
ProjectError traceback; ``voice`` and ``redo`` shared the raw-traceback
path on unknown shot ids, while align/impact/prompt/routing already used
the house ``except ProjectError: _fail(...)`` pattern. This file pins the
fix: check-then-register plus the standard catch, applied to the three
verbs the first-run journey actually hits.

The teeth: exit code 1 (never an unhandled SystemExit-2/traceback), the
structured message names the shot AND a next action, ``--json`` carries
the ``{"error": ...}`` envelope, and — for select --file — media/gen
stays EMPTY (no orphan take directory).
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()


def _media_file(tmp_path: Path) -> Path:
    f = tmp_path / "human_clip.mp4"
    f.write_bytes(b"\x00\x00\x00\x18ftypmp42fakebytes")
    return f


import pytest


@pytest.fixture(autouse=True)
def _in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)


def _run(project, *args):
    return runner.invoke(app, [*map(str, args)])


# --------------------------------------------------------------- select --file


def test_select_file_unknown_shot_is_structured_and_orphan_free(tmp_project, tmp_path):
    src = _media_file(tmp_path)
    res = _run(tmp_project, "select", "S404", "--file", src)
    assert res.exit_code == 1
    out = res.output
    assert "Traceback" not in out and "ProjectError" not in out
    assert "S404" in out  # names the missing shot
    # names a next action (status or the shots discipline)
    assert ("manju status" in out) or ("shots/" in out) or ("index.yaml" in out)
    # THE orphan tooth: nothing was registered for the nonexistent shot
    gen = tmp_project.root / "media" / "gen"
    assert not (gen / "S404").exists(), "orphan take directory left behind"


def test_select_file_unknown_shot_json_envelope(tmp_project, tmp_path):
    src = _media_file(tmp_path)
    res = _run(tmp_project, "select", "S404", "--file", src, "--json")
    assert res.exit_code == 1
    payload = json.loads(res.output.strip().splitlines()[-1])
    assert "error" in payload and "S404" in payload["error"]
    assert not (tmp_project.root / "media" / "gen" / "S404").exists()


def test_select_file_known_shot_still_registers_and_selects(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    src = _media_file(tmp_path)
    res = _run(tmp_project, "select", "S001", "--file", src)
    assert res.exit_code == 0, res.output
    takes = tmp_project.takes("S001")
    assert takes and takes[0].sidecar.provider == "manual_import"


# ------------------------------------------------------------------- voice


def test_voice_unknown_shot_is_structured(tmp_project):
    res = _run(tmp_project, "voice", "S404")
    assert res.exit_code == 1
    assert "Traceback" not in res.output and "ProjectError" not in res.output
    assert "S404" in res.output


# -------------------------------------------------------------------- redo


def test_redo_unknown_shot_is_structured(tmp_project):
    res = _run(tmp_project, "redo", "S404")
    assert res.exit_code == 1
    assert "Traceback" not in res.output and "ProjectError" not in res.output
    assert "S404" in res.output


# ------------------------------------------- the guard never blocks real work


def test_known_shot_paths_unaffected(tmp_project, add_shot):
    """The three guarded verbs still reach their normal next failure/success
    for a shot that EXISTS (no over-eager refusal): select without a take
    argument reaches its own take-picker message, not the unknown-shot one."""
    add_shot(tmp_project, "S001")
    res = _run(tmp_project, "select", "S001")
    assert res.exit_code == 1
    assert "没提供 take" in res.output or "take" in res.output
