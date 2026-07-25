"""Usability papercuts found by actually driving the CLI end to end (2026-07-25).

None of these is a correctness defect — the engine did the right thing every
time. They are the five places where the tool made the OWNER do avoidable work
or told them something that read wrong at a glance:

1. ``manju frames S001`` — the one shot-facing command whose argument was a
   media PATH while every sibling takes a shot id (with the s14/14 shorthand),
   and whose failure named the input without saying a path was wanted.
2. ``bible/*.yaml`` scaffolded as a bare ``{}`` — ``shots/*.yaml`` points the
   author here by id, but nothing showed the shape, and there is no
   ``manju bible add``.
3. ``qc.md`` opening with a flat ``✅ ok`` for a film whose every shot declared
   nothing to check — "nothing to verify" rendered as "verified good".
4. The creation funnel marking a stage ``○`` while its own evidence line said
   已落地 (state is positional; the predicate can already be satisfied).
5. ``manju exports`` printing "READY 可发布" directly under "missing:6".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.container import BIBLE_FILES, Project
from manju.core.yamlio import read_yaml

runner = CliRunner()


# ---------------------------------------------------------------- 1. frames


def test_frames_accepts_a_shot_id_and_resolves_the_selected_take(
        tmp_project: Project, add_shot, make_take) -> None:
    """A shot id is the natural thing to type here, so it must work."""
    from manju.cli import _resolve_frame_source

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:spec")

    resolved = _resolve_frame_source(tmp_project, "S001")
    assert resolved.replace("\\", "/").endswith("S001/take_01.mp4")
    # ...and the s14/14 shorthand every sibling command already forgives
    assert _resolve_frame_source(tmp_project, "1") == resolved


def test_frames_still_takes_a_real_media_path_unchanged(
        tmp_project: Project, add_shot) -> None:
    """The path form wins: resolution only runs when the arg is not a file, so
    no existing invocation changes meaning."""
    from manju.cli import _resolve_frame_source

    add_shot(tmp_project, "S001")
    rel = "renders/final/final_v1.mp4"
    p = Path(tmp_project.root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    assert _resolve_frame_source(tmp_project, rel) == rel


def test_frames_error_names_what_the_argument_wants(tmp_project: Project) -> None:
    """The old message was a bare "frame source not found: S001", which reads as
    "no such shot" when the real issue is the argument's TYPE."""
    from manju.media.ffmpeg import MediaError
    from manju.media.frames import _resolve_source

    with pytest.raises(MediaError) as exc:
        _resolve_source(tmp_project, "media/gen/nope.mp4")
    msg = str(exc.value)
    assert "媒体路径" in msg and "镜头 id" in msg


def test_frames_shot_without_any_take_fails_structured(
        tmp_project: Project, add_shot, monkeypatch) -> None:
    """UNKNOWN is never guessed into a preview: a shot with nothing rendered yet
    gets a bad_args refusal naming the fix, not a path-not-found. Driven through
    --json so the machine contract is what gets pinned, not the prose."""
    import json as _json

    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["frames", "S001", "--json"])
    assert res.exit_code != 0
    payload = _json.loads(res.stdout.strip().splitlines()[-1])
    assert payload["code"] == "bad_args"
    assert "还没有可预览的 take" in payload["error"]


# ------------------------------------------------------------ 2. bible scaffold


def test_bible_scaffold_shows_the_shape_and_still_parses_empty(tmp_path: Path) -> None:
    root = tmp_path / "demo.manju"
    Project.create(root, name="demo")
    for fname in BIBLE_FILES:
        path = root / "bible" / f"{fname}.yaml"
        text = path.read_text(encoding="utf-8")
        # the author can see BOTH the field names and a concrete example
        assert "name:" in text and "desc" in text, fname
        assert "示例" in text, fname
        # …and a fresh project is still an EMPTY bible: nothing here is content
        assert read_yaml(path) == {}, fname


def test_fresh_project_still_passes_check_with_the_scaffolded_bible(
        tmp_path: Path) -> None:
    from manju.core.check import run_check

    root = tmp_path / "demo2.manju"
    Project.create(root, name="demo2")
    assert run_check(Project(root)).errors == []


# ------------------------------------------------------------------- 3. qc.md


def _md(ok: bool, assurance):
    from manju.qc.checks import QCReport
    from manju.qc.report import _render_md

    qc = QCReport()
    if not ok:
        qc.add("error", "technical", "S001", "boom")
    return _render_md(qc, "2026-07-25T00:00:00+00:00", assurance)


def test_qc_headline_says_nothing_was_promised_rather_than_plain_ok() -> None:
    md = _md(True, [{"assurance_state": "no_explicit_expectations"},
                    {"assurance_state": "no_explicit_expectations"}])
    head = md.splitlines()[2]
    assert "✅ ok" in head            # the machine tier really did pass
    assert "2 镜未声明任何预期" in head  # …and it says what it could NOT judge


def test_qc_headline_is_unchanged_when_shots_declared_expectations() -> None:
    """No noise for a project doing it properly."""
    head = _md(True, [{"assurance_state": "accepted"}]).splitlines()[2]
    assert head == "- 结果 result: ✅ ok"
    assert _md(True, []).splitlines()[2] == "- 结果 result: ✅ ok"


def test_qc_headline_error_case_untouched() -> None:
    head = _md(False, [{"assurance_state": "no_explicit_expectations"}]).splitlines()[2]
    assert head == "- 结果 result: ❌ has errors"


# ------------------------------------------------------------------ 4. funnel


def test_funnel_marks_a_satisfied_out_of_order_stage_as_satisfied(
        tmp_project: Project, add_shot) -> None:
    """`state` stays POSITIONAL (the funnel keeps its order), but a later stage
    whose predicate already holds must not render as ○ next to evidence saying
    已落地 — the renderer keys off this flag."""
    from manju.build.funnel import funnel_status

    add_shot(tmp_project, "S001")  # storyboard holds; the story stages do not
    info = funnel_status(tmp_project)
    by_id = {s["id"]: s for s in info["stages"]}
    assert all("satisfied" in s for s in info["stages"])
    board = by_id["storyboard"]
    # it is not its turn (brief comes first) yet its own predicate passes
    assert board["state"] == "todo" and board["satisfied"] is True
    # the tally is unchanged — satisfied-but-out-of-order is not "done"
    assert info["done"] == sum(1 for s in info["stages"] if s["state"] == "done")


def test_funnel_first_unfinished_stage_is_still_current(tmp_project: Project) -> None:
    from manju.build.funnel import funnel_status

    info = funnel_status(tmp_project)
    assert info["current"] == "brief"
    assert [s for s in info["stages"] if s["state"] == "current"][0]["id"] == "brief"
    assert info["stages"][0]["satisfied"] is False
