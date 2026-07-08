"""P2 versioning surface: `manju history` / `snapshot` / `rollback` (§3, §10).

- rollback shot re-selects the previously selected take from the event record
  (append-only: nothing is deleted, the newer take stays for compare);
- snapshot is a labeled git checkpoint; a clean tree is a no-op, not an error;
- rollback file is a guarded, single-file `git checkout` scoped to truth text
  (media/renders/exports are refused outright);
- history merges events.jsonl with the git log into one feed;
- every rollback is itself an event — history only ever grows.
"""

from __future__ import annotations

import shutil

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.container import Project
from manju.core.events import append_event, tail_events
from manju.core.history import (
    HistoryError,
    history,
    rollback_file,
    rollback_shot,
    snapshot,
)

runner = CliRunner()

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git required")


# --------------------------------------------------------------- shot rollback


def _select(project: Project, shot_id: str, take: str) -> None:
    """The select command's two effects, without the CLI shell."""
    project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take)
    )
    append_event(project.root, "human", "select", {"shot": shot_id, "take": take})


def test_rollback_shot_returns_to_previous_take(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "hash1")  # take_01
    make_take(tmp_project, "S001", "hash1")  # take_02
    takes = [t.name for t in tmp_project.takes("S001")]
    _select(tmp_project, "S001", takes[0])
    _select(tmp_project, "S001", takes[1])

    result = rollback_shot(tmp_project, "S001")
    assert result == {"shot": "S001", "take": takes[0], "was": takes[1]}
    assert tmp_project.load_shot("S001").status.selected_take == takes[0]
    # the newer take is still on disk — append-only, nothing deleted
    assert [t.name for t in tmp_project.takes("S001")] == takes
    # the rollback itself is an event
    assert tail_events(tmp_project.root, 1)[0]["action"] == "rollback_shot"


def test_rollback_shot_twice_toggles(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    make_take(tmp_project, "S001", "h")
    a, b = [t.name for t in tmp_project.takes("S001")]
    _select(tmp_project, "S001", a)
    _select(tmp_project, "S001", b)
    assert rollback_shot(tmp_project, "S001")["take"] == a
    assert rollback_shot(tmp_project, "S001")["take"] == b  # back forward again


def test_rollback_shot_without_history_fails_cleanly(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    with pytest.raises(HistoryError, match="manju select"):
        rollback_shot(tmp_project, "S001")


def test_rollback_shot_unknown_shot(tmp_project):
    with pytest.raises(HistoryError, match="unknown shot"):
        rollback_shot(tmp_project, "S404")


# ------------------------------------------------------- snapshot + file (git)


@needs_git
def test_snapshot_and_rollback_file(tmp_path):
    project = Project.create(tmp_path / "回滚测试", git_init=True)
    rules = project.rules_path
    original = rules.read_text(encoding="utf-8")

    first = snapshot(project, "基线")
    assert first["clean"] is False and first["sha"]
    # clean tree → no-op with the same checkpoint, not an error
    again = snapshot(project)
    assert again["clean"] is True and again["sha"] == first["sha"]

    rules.write_text(original + "\n# 手滑改坏了\n", encoding="utf-8")
    result = rollback_file(project, "timeline/rules.yaml")
    assert result["ref"] == "HEAD"
    assert rules.read_text(encoding="utf-8") == original
    assert tail_events(project.root, 1)[0]["action"] == "rollback_file"


@needs_git
def test_rollback_file_refuses_media_and_escapes(tmp_path):
    project = Project.create(tmp_path / "守卫测试", git_init=True)
    snapshot(project, "基线")
    with pytest.raises(HistoryError, match="truth"):
        rollback_file(project, "media/imports/human.wav")
    with pytest.raises(HistoryError, match="truth|surface"):
        rollback_file(project, "renders/final/final_v1.mp4")
    with pytest.raises(HistoryError, match="escapes"):
        rollback_file(project, "story/../../outside.yaml")
    with pytest.raises(HistoryError, match="surface"):
        rollback_file(project, "notes.txt")


@needs_git
def test_rollback_file_refuses_compiled_timeline_artifacts(tmp_path):
    """review #74: timeline/timeline.json and timeline/timeline.generated.json
    are COMPILED ARTIFACTS (the build's output), not truth text — even though
    they sit under timeline/ and end in .json (which the generic
    prefix/suffix rule would otherwise allow). rollback must refuse both and
    point at rebuilding instead of git-checkout-restoring a stale artifact."""
    project = Project.create(tmp_path / "产物测试", git_init=True)
    snapshot(project, "基线")
    for rel in ("timeline/timeline.json", "timeline/timeline.generated.json"):
        with pytest.raises(HistoryError, match="编译产物|manju build"):
            rollback_file(project, rel)
    # the actual truth file living in the SAME directory is still restorable
    result = rollback_file(project, "timeline/rules.yaml")
    assert result["path"] == "timeline/rules.yaml"


def test_rollback_file_requires_git(tmp_project):
    with pytest.raises(HistoryError, match="patch engine"):
        rollback_file(tmp_project, "timeline/rules.yaml")


def test_snapshot_requires_git(tmp_project):
    with pytest.raises(HistoryError, match="patch engine"):
        snapshot(tmp_project, "x")


# -------------------------------------------------------------------- history


@needs_git
def test_history_merges_events_and_git(tmp_path, monkeypatch):
    project = Project.create(tmp_path / "历史测试", git_init=True)
    snapshot(project, "第一版")
    append_event(project.root, "ai", "select", {"shot": "S001", "take": "take_02"})

    rows = history(project, n=50)
    sources = {r["source"] for r in rows}
    assert {"event", "git"} <= sources
    assert [r for r in rows if r["actor"] == "ai" and "select" in r["text"]]
    assert [r for r in rows if r["source"] == "git" and "snapshot" in r["text"]]
    # oldest→newest ordering
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)

    # CLI smoke: human output carries both kinds of rows
    monkeypatch.chdir(project.root)
    out = runner.invoke(app, ["history"])
    assert out.exit_code == 0, out.output
    assert "[git" in out.output and "[ai]" in out.output


def test_history_without_git_is_events_only(tmp_project):
    append_event(tmp_project.root, "human", "lock", {"shot": "S001"})
    rows = history(tmp_project, n=10)
    assert rows and all(r["source"] == "event" for r in rows)
