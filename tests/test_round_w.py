"""Round W: concurrency and atomicity — unified selected_take write path,
BuildLock coverage, and crash-safety (§9 review round W).

Cross-cutting verification that does not belong to any one existing module's
test file:

- #24 — atomic_write_text fsyncs the PARENT DIRECTORY after os.replace, not
  just the file itself (crash-safety for the directory entry, not only the
  bytes).
- #28 — build/graph.py's auto-select ("fill a gap the human never decided")
  must not silently overturn a value-hash lock sealed on an EMPTY
  status.selected_take — it skips, loudly, with a QC-visible record.

Entrance-specific #39 (CLI/board/GUI select all refusing a locked
selected_take, and all recording "via") live next to each entrance's own
test file (test_cli.py / test_board_serve.py / test_gui.py) —
see those for the rest of the round-W coverage.
"""

from __future__ import annotations

import os

from manju.core.locks import seal_lock


# ============================================================ #24: dir fsync


def test_atomic_write_text_fsyncs_parent_directory(tmp_path, monkeypatch):
    """The parent directory must be opened and fsync'd AFTER the os.replace
    that lands the file — not just the file's own fd (round W, #24)."""
    from manju.core import yamlio

    calls: list[tuple[str, int]] = []
    real_fsync = os.fsync
    real_open = os.open

    def spy_fsync(fd):
        calls.append(("fsync", fd))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy_fsync)

    target = tmp_path / "sub" / "project.yaml"
    yamlio.atomic_write_text(target, "hello: world\n")

    assert target.read_text(encoding="utf-8") == "hello: world\n"
    # at least two fsyncs happened: the temp FILE (inside the `with
    # os.fdopen` block) and the PARENT DIRECTORY (_fsync_dir, after replace).
    if os.name == "nt":
        # Windows cannot open a directory fd — _fsync_dir degrades by design
        # (yamlio docstring); only the temp FILE fsync is guaranteed there.
        assert len(calls) >= 1, "expected at least the file fsync"
    else:
        assert len(calls) >= 2, "expected both a file fsync and a directory fsync"


def test_fsync_dir_degrades_silently_when_os_open_fails(tmp_path, monkeypatch):
    """A platform/filesystem that cannot open a directory as a file (Windows,
    some network mounts) must degrade the dir-fsync to a no-op — never break
    the write itself (round W, #24: 'best-effort... swallowed narrowly')."""
    from manju.core import yamlio

    target_dir = tmp_path
    real_open = os.open

    def selective_failing_open(path, flags, *a, **kw):
        # Only fail opening the DIRECTORY itself (what _fsync_dir does);
        # every other os.open call (mkstemp's temp file, etc.) passes
        # through untouched, so the write itself is free to succeed.
        if os.fspath(path) == os.fspath(target_dir):
            raise OSError("simulated: cannot open a directory as a file here")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(os, "open", selective_failing_open)

    target = tmp_path / "project.yaml"
    yamlio.atomic_write_text(target, "ok: true\n")  # must not raise
    assert target.read_text(encoding="utf-8") == "ok: true\n"


# ==================================================== #28: auto-select + locks


def _seal_empty_selected_take_lock(project, shot_id: str) -> None:
    """Seal status.selected_take BEFORE any take is ever chosen — a human
    sealing "nothing auto-picks this one" ahead of time."""

    def ensure_field(d):
        status = d.get("status")
        if not isinstance(status, dict):
            status = {}
        status.setdefault("selected_take", None)
        d["status"] = status

    project.update_shot_raw(shot_id, ensure_field)
    raw = project.load_shot_raw(shot_id)
    digest = seal_lock(raw, "status.selected_take")
    project.update_shot_raw(
        shot_id, lambda d: d.setdefault("locked", {}).__setitem__(
            "status.selected_take", digest)
    )


def test_build_selection_gate_respects_locked_empty_selection(
        tmp_project, add_shot, make_take):
    """A locked empty selection remains empty and build stops at review."""
    from manju.build.graph import run_build

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")  # a usable take exists...
    _seal_empty_selected_take_lock(tmp_project, "S001")  # ...but selection is locked

    result = run_build(tmp_project, target="qc", gen="off", actor="engine")

    # the lock held: selected_take is still empty
    assert tmp_project.load_shot("S001").status.selected_take is None
    assert result.selection_required == ["S001"]
    assert any("manual selection required" in e for e in result.errors)


def test_build_selection_gate_never_fills_unlocked_gaps(
        tmp_project, add_shot, make_take):
    """Unlocked candidates still require an explicit human selection."""
    from manju.build.graph import run_build

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")

    result = run_build(tmp_project, target="qc", gen="off", actor="engine")

    assert tmp_project.load_shot("S001").status.selected_take is None
    assert result.selection_required == ["S001"]
    assert take.name in {t.name for t in tmp_project.takes("S001")}


def test_build_auto_select_never_overturns_an_existing_selection(
    tmp_project, add_shot, make_take
):
    """Companion invariant (§4.3, unaffected by #28): auto-select only fills
    a GAP — a shot that already has a human selection is never touched, lock
    or no lock."""
    from manju.build.graph import run_build

    add_shot(tmp_project, "S001")
    t1 = make_take(tmp_project, "S001", "h1")
    make_take(tmp_project, "S001", "h2")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", t1.name)
    )
    run_build(tmp_project, target="qc", gen="off", actor="engine")
    assert tmp_project.load_shot("S001").status.selected_take == t1.name
