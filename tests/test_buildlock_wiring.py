"""Process build-lock wiring into the engine surfaces.

The lock itself is covered by test_buildlock.py; here we assert the engine
actually honors it: a held lock turns `run_build` into a one-line ok=False
result, makes `redo_shot` raise, and never blocks the read-only dry-run.

Round W (#9): coverage widened to the CLI mutating commands that did NOT
route through build/graph.py's own lock (select, import, gc, lock/unlock,
rollback, snapshot, repair ops) — each must now refuse fast with a clear
busy message naming the holder, exactly like build/redo/qc already did.
"""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from manju.build.graph import redo_shot, run_build
from manju.cli import app
from manju.runtime.buildlock import BuildLock, BuildLocked

runner = CliRunner()


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


# ============================================================ round W (#9)
# CLI commands that mutate the project but previously did NOT take the
# process build lock: select, import, gc, lock/unlock, rollback, snapshot,
# the explicit repair ops. Each must refuse fast (never hang — BuildLock
# never blocks/waits) with a one-line busy message, and must NOT have
# mutated anything.


def _held(tmp_project) -> BuildLock:
    return BuildLock(tmp_project.root, actor="human").acquire()


def _busy(output: str) -> bool:
    return "占用" in output or "busy" in output.lower() or "build_locked" in output.lower()


def test_cli_select_refuses_when_build_locked(tmp_project, add_shot, make_take, monkeypatch):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    monkeypatch.chdir(tmp_project.root)
    lock = _held(tmp_project)
    try:
        result = runner.invoke(app, ["select", "S001", take.name])
        assert result.exit_code != 0
        assert _busy(result.output)
    finally:
        lock.release()
    assert tmp_project.load_shot("S001").status.selected_take is None


def test_cli_import_refuses_when_build_locked(tmp_project, tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake-media-bytes")
    monkeypatch.chdir(tmp_project.root)
    lock = _held(tmp_project)
    try:
        result = runner.invoke(app, ["import", str(src)])
        assert result.exit_code != 0
        assert _busy(result.output)
    finally:
        lock.release()
    assert not (tmp_project.imports_dir / "clip.mp4").exists()


def test_cli_gc_refuses_when_build_locked(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    (tmp_project.segments_dir).mkdir(parents=True, exist_ok=True)
    marker = tmp_project.segments_dir / "seg.mp4"
    marker.write_bytes(b"x")
    lock = _held(tmp_project)
    try:
        result = runner.invoke(app, ["gc", "--json"])
        assert result.exit_code != 0
        assert _busy(result.output)
    finally:
        lock.release()
    assert marker.exists()  # nothing was reclaimed while locked


def test_cli_lock_refuses_when_build_locked(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    lock = _held(tmp_project)
    try:
        # --yes clears WP5 lock_change ask_before so the refusal we assert
        # is the build_lock contention, not the honesty gate.
        result = runner.invoke(app, ["lock", "S001", "duration", "--yes"])
        assert result.exit_code != 0
        assert _busy(result.output)
    finally:
        lock.release()
    assert not (tmp_project.load_shot_raw("S001").get("locked") or {})


def test_cli_unlock_refuses_when_build_locked(tmp_project, add_shot, monkeypatch):
    """unlock is interactive-only (§5) — monkeypatch past that gate so the
    LOCK-specific refusal (not the tty gate) is what's under test."""
    import manju.cli as cli_mod

    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda *a, **kw: True)
    lock = _held(tmp_project)
    try:
        result = runner.invoke(app, ["unlock", "S001", "duration"])
        assert result.exit_code != 0
        assert _busy(result.output)
    finally:
        lock.release()


def test_cli_snapshot_refuses_when_build_locked(tmp_project, monkeypatch):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_project.root, check=False,
                   capture_output=True)
    monkeypatch.chdir(tmp_project.root)
    lock = _held(tmp_project)
    try:
        result = runner.invoke(app, ["snapshot", "test-label"])
        assert result.exit_code != 0
        assert _busy(result.output)
    finally:
        lock.release()


def test_cli_rollback_shot_refuses_when_build_locked(tmp_project, add_shot, make_take, monkeypatch):
    add_shot(tmp_project, "S001")
    t1 = make_take(tmp_project, "S001", "h1")
    t2 = make_take(tmp_project, "S001", "h2")
    monkeypatch.chdir(tmp_project.root)
    assert runner.invoke(app, ["select", "S001", t1.name]).exit_code == 0
    assert runner.invoke(app, ["select", "S001", t2.name]).exit_code == 0
    lock = _held(tmp_project)
    try:
        result = runner.invoke(app, ["rollback", "shot", "S001"])
        assert result.exit_code != 0
        assert _busy(result.output)
    finally:
        lock.release()
    assert tmp_project.load_shot("S001").status.selected_take == t2.name  # unchanged


def test_cli_repair_op_refuses_when_build_locked(tmp_project, add_shot, make_take, monkeypatch):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    monkeypatch.chdir(tmp_project.root)
    assert runner.invoke(app, ["select", "S001", take.name]).exit_code == 0
    lock = _held(tmp_project)
    try:
        # the lock check runs BEFORE the ffmpeg op, so this refuses fast
        # with no ffmpeg dependency either way.
        result = runner.invoke(
            app, ["repair", "--op", "trim", "--shot", "S001", "--ms", "100"]
        )
        assert result.exit_code != 0
        assert _busy(result.output)
    finally:
        lock.release()
    # no new take was minted while locked
    assert {t.name for t in tmp_project.takes("S001")} == {take.name}


def test_cli_read_commands_stay_lockless(tmp_project, add_shot, monkeypatch):
    """READ commands (status/check/explain) must stay lock-free (#9) — a
    held build lock must never block them."""
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    lock = _held(tmp_project)
    try:
        for argv in (["status", "--json"], ["check", "--json"], ["explain", "--json"]):
            result = runner.invoke(app, argv)
            assert result.exit_code == 0, (argv, result.output)
    finally:
        lock.release()
