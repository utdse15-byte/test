"""Without its baseline, a roundtrip plan is not unverified — it is WRONG.

Measured by walking the workflow: export OTIO, "edit" it (one clip trimmed
72 → 36 frames), bring it back. The SAME file plans two different ways
depending only on where it sits:

    beside its baseline   ->  1 row,  set_inout                (the real trim)
    copied elsewhere      ->  3 rows, set_transition_override x3

So with no baseline the genuine edit VANISHES from the plan and three changes
the editor never made appear instead. A pristine, untouched export produces the
same three rows — they are artifacts of having nothing to diff against.

That is the ordinary case, not an exotic one: a draft saved from JianYing or
Resolve lands in the editor's own folder, not back in `exports/<kind>/` where
the sidecar lives.

An earlier round of this session added a warning that the rows were "未与导出点
对账" — true, but it understates the consequence, and `--apply` went ahead
anyway. Applying such a plan writes fiction into truth AND silently drops the
owner's actual cut. `--apply` is now refused; planning still works, so the
carrier can be inspected before being moved back.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.models import (
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import write_yaml

runner = CliRunner()


@pytest.fixture
def exported(tmp_project, monkeypatch) -> Path:
    write_yaml(tmp_project.rules_path, TimelineRules(mode="manual").model_dump())
    clips = []
    for i, sid in enumerate(("S001", "S002")):
        clips.append(VideoClip(shot=sid, take="take_01",
                               source=f"media/gen/{sid}/take_01.mp4",
                               start_ms=i * 2000, duration_ms=2000))
        src = tmp_project.root / "media" / "gen" / sid / "take_01.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"fakevideo")
    tmp_project.save_timeline(Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        tracks=TimelineTracks(video=clips)))
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["export", "--otio", "--yes"])
    assert res.exit_code == 0, res.stdout
    otio = list((tmp_project.root / "exports" / "otio").glob("*.otio"))
    assert otio, res.stdout
    return otio[0]


def _strayed(exported: Path, tmp_path: Path) -> Path:
    """What an editor actually does: save the draft in its own folder."""
    stray = tmp_path / "back from the editor.otio"
    shutil.copy(exported, stray)
    return stray


# ------------------------------------------------------------ apply is gated


def test_apply_is_refused_without_a_baseline(exported, tmp_path) -> None:
    stray = _strayed(exported, tmp_path)
    res = runner.invoke(app, ["roundtrip", str(stray), "--apply"])
    assert res.exit_code != 0, res.output
    assert "baseline" in res.output


def test_the_refusal_carries_a_machine_code(exported, tmp_path) -> None:
    """An agent must be able to branch on this rather than match prose."""
    stray = _strayed(exported, tmp_path)
    res = runner.invoke(app, ["roundtrip", str(stray), "--apply", "--json"])
    assert res.exit_code != 0
    assert json.loads(res.output)["code"] == "no_baseline"


def test_the_refusal_says_how_to_recover(exported, tmp_path) -> None:
    stray = _strayed(exported, tmp_path)
    out = runner.invoke(app, ["roundtrip", str(stray), "--apply"]).output
    assert "exports/" in out, "does not say where to put the carrier back"
    assert "manju export" in out, "does not offer the re-export route"


def test_planning_still_works_without_a_baseline(exported, tmp_path) -> None:
    """Refusing to WRITE is right; refusing to look would strand the owner."""
    stray = _strayed(exported, tmp_path)
    res = runner.invoke(app, ["roundtrip", str(stray)])
    assert res.exit_code == 0, res.output
    assert "roundtrip plan" in res.output


def test_apply_still_works_with_the_baseline(exported) -> None:
    """The gate must not break the supported path."""
    res = runner.invoke(app, ["roundtrip", str(exported), "--apply"])
    assert res.exit_code == 0, res.output


# --------------------------------------------------------- the warning is honest


def test_the_warning_names_the_real_consequence(exported, tmp_path) -> None:
    """"unverified rows" and "the wrong rows" call for different reactions."""
    stray = _strayed(exported, tmp_path)
    out = runner.invoke(app, ["roundtrip", str(stray)]).output
    assert "缺失" in out, "does not say real edits can be missing"
    assert "多出" in out, "does not say spurious rows can appear"


def test_the_warning_is_absent_with_a_baseline(exported) -> None:
    out = runner.invoke(app, ["roundtrip", str(exported)]).output
    assert "不可靠" not in out


# ------------------------------------------------- the measurement itself holds


def _trim_first_clip(path: Path) -> bool:
    """Halve the first clip's duration — the commonest editor action."""
    data = json.loads(path.read_text(encoding="utf-8"))

    def walk(node) -> bool:
        if isinstance(node, dict):
            sr = node.get("source_range")
            if (str(node.get("OTIO_SCHEMA", "")).startswith("Clip")
                    and isinstance(sr, dict) and "duration" in sr):
                sr["duration"]["value"] = max(1, int(sr["duration"]["value"] * 0.5))
                return True
            return any(walk(v) for v in node.values())
        if isinstance(node, list):
            return any(walk(v) for v in node)
        return False

    hit = walk(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return hit


def test_the_plan_really_does_differ_by_location(exported, tmp_path) -> None:
    """Guard the guard, and the measurement this whole file rests on.

    The FIRST version of this test compared two plans of an UNEDITED export, so
    both were trivially empty and it "passed" without measuring anything — the
    guard caught it. The divergence only exists once there is a real edit to
    lose, so the trim has to be applied to both copies."""
    stray = _strayed(exported, tmp_path)
    assert _trim_first_clip(stray), "no clip with a source_range to trim"
    assert _trim_first_clip(exported), "no clip with a source_range to trim"

    here = json.loads(runner.invoke(
        app, ["roundtrip", str(exported), "--json"]).output)
    there = json.loads(runner.invoke(
        app, ["roundtrip", str(stray), "--json"]).output)
    assert here.get("baseline") and not there.get("baseline")

    here_actions = [r.get("action") for r in here["rows"]]
    there_actions = [r.get("action") for r in there["rows"]]
    assert "set_inout" in here_actions, (
        f"the trim is no longer detected even WITH a baseline: {here_actions}")
    assert "set_inout" not in there_actions, (
        f"the no-baseline hazard is gone — this gate may be obsolete: "
        f"{there_actions}")
