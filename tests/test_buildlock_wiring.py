"""Process build-lock wiring into the engine surfaces.

The lock itself is covered by test_buildlock.py; here we assert the engine
actually honors it: a held lock turns `run_build` into a one-line ok=False
result, makes `redo_shot` raise, and never blocks the read-only dry-run.
"""

from __future__ import annotations

import pytest

from manju.build.graph import redo_shot, run_build
from manju.runtime.buildlock import BuildLock, BuildLocked


def test_run_build_fails_cleanly_when_locked(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        result = run_build(tmp_project, target="qc", gen="off", actor="ai")
        assert result.ok is False
        assert any("build already running" in e for e in result.errors)
        assert any("actor=human" in e for e in result.errors)
    finally:
        lock.release()


def test_dry_run_is_lockless(tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # missing -> plan entry
    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        result = run_build(tmp_project, dry_run=True, actor="ai")
        assert result.ok is True
        assert any(p["shot"] == "S001" for p in result.plan)
    finally:
        lock.release()


def test_redo_raises_when_locked(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        with pytest.raises(BuildLocked):
            redo_shot(tmp_project, "S001", actor="ai")
    finally:
        lock.release()


def test_build_works_after_release(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    lock = BuildLock(tmp_project.root, actor="human").acquire()
    lock.release()
    result = run_build(tmp_project, target="qc", gen="off", actor="ai")
    assert result.ok is True
    # and the lock file is gone afterwards (released by the build itself)
    assert not (tmp_project.runtime_dir / "build.lock").exists()


def test_status_surfaces_active_lock(tmp_project):
    from manju.build.status import project_status

    assert project_status(tmp_project)["build_lock"] is None
    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        info = project_status(tmp_project)["build_lock"]
        assert info and info["actor"] == "human" and info["pid"]
    finally:
        lock.release()
