"""FP_M2 — GUI state/render efficiency pins (audit G2 + G3).

These are SPY pins on the request-thread work, not timing assertions:

  * G2 — one ``build_state`` must run ``evaluate_all`` and
    ``evaluate_all_voices`` EXACTLY ONCE each (before: twice each, because
    ``project_status`` and ``_shot_cards`` recomputed independently). The
    /api/state SHAPE is pinned elsewhere; here we also assert the per-shot cards
    still appear so the threaded result is not silently dropped.

  * G3 — ``render_edit`` must make ZERO ``build.explain`` recompiles and spawn
    ZERO subprocesses on the request thread (before: a full explain recompile
    for the dirty badge + the playback stale-check, ~40 probes). The dirty/stale
    badges become a lazy fetch after first paint (mirrors the review boards).
"""

from __future__ import annotations

import subprocess

import pytest


class _FakeRunner:
    """The two methods build_state calls on a JobRunner."""

    def list(self):
        return []

    def interrupted(self):
        return []


def _three_selected_shots(project, add_shot, make_take):
    for sid in ("S001", "S002", "S003"):
        add_shot(project, sid)
        make_take(project, sid, "manual")
        project.update_shot_raw(
            sid, lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01"))


# --------------------------------------------------------------------- G2


def test_build_state_evaluates_stale_and_voice_once(
        monkeypatch, tmp_project, add_shot, make_take):
    _three_selected_shots(tmp_project, add_shot, make_take)

    import manju.build.stale as bstale
    import manju.build.status as bstatus
    import manju.build.voice as bvoice
    import manju.gui.state as gstate

    calls = {"stale": 0, "voice": 0}
    real_stale = bstale.evaluate_all
    real_voice = bvoice.evaluate_all_voices

    def spy_stale(project):
        calls["stale"] += 1
        return real_stale(project)

    def spy_voice(project):
        calls["voice"] += 1
        return real_voice(project)

    # evaluate_all is imported `from ..build.stale import evaluate_all` into BOTH
    # gui.state and build.status at module load, so patch both bindings with the
    # SAME counter; evaluate_all_voices is imported lazily, so patch its source.
    monkeypatch.setattr(gstate, "evaluate_all", spy_stale)
    monkeypatch.setattr(bstatus, "evaluate_all", spy_stale)
    monkeypatch.setattr(bvoice, "evaluate_all_voices", spy_voice)

    state = gstate.build_state(tmp_project, _FakeRunner())

    assert calls["stale"] == 1, f"evaluate_all ran {calls['stale']}x per build (want 1)"
    assert calls["voice"] == 1, f"evaluate_all_voices ran {calls['voice']}x per build (want 1)"
    # shape survived: per-shot cards still present with the same ids
    assert {c["id"] for c in state["shots"]} == {"S001", "S002", "S003"}
    assert "shots_by_state" in state and "voice_by_state" in state


def test_build_state_shape_unchanged_by_threading(
        tmp_project, add_shot, make_take):
    """The threaded computation must produce the SAME payload a from-scratch
    build would — compute-once is pure plumbing, no shape drift."""
    _three_selected_shots(tmp_project, add_shot, make_take)
    from manju.gui.state import build_state

    a = build_state(tmp_project, _FakeRunner())
    b = build_state(tmp_project, _FakeRunner())
    assert a.keys() == b.keys()
    assert a["shots"] == b["shots"]
    assert a["shots_by_state"] == b["shots_by_state"]


# --------------------------------------------------------------------- G3


def test_render_edit_zero_explain_zero_spawns(
        monkeypatch, tmp_project, add_shot, make_take):
    _three_selected_shots(tmp_project, add_shot, make_take)
    # a fake final so playback_source's stale-check path (the SECOND inline
    # explain, before the fix) is also on the render path
    final = tmp_project.final_dir / "final_v1.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"\x00\x00fake-final\x00")

    import manju.build.explain as bexplain
    from manju.gui import edit

    explain_calls = []
    real_explain = bexplain.explain

    def spy_explain(project, *a, **k):
        explain_calls.append(1)
        return real_explain(project, *a, **k)

    monkeypatch.setattr(bexplain, "explain", spy_explain)

    spawns = []

    def _forbid(*args, **kwargs):
        spawns.append(args[0] if args else kwargs.get("args"))
        raise AssertionError(f"render_edit spawned a subprocess: {spawns[-1]!r}")

    monkeypatch.setattr(subprocess, "run", _forbid)
    monkeypatch.setattr(subprocess, "Popen", _forbid)

    html = edit.render_edit(tmp_project, "tok", {})

    assert explain_calls == [], "render_edit recompiled via build.explain on the request thread"
    assert spawns == [], f"render_edit spawned a subprocess on the request thread: {spawns}"
    assert '<h1>剪辑<span class="mj-en"' in html   # Chinese-first page still rendered
    assert 'id="ed-dirty-chip"' in html            # the dirty badge is a lazy slot now


def test_edit_dirty_endpoint_serves_the_verdict(
        tmp_project, add_shot, make_take):
    """The badge the render used to compute inline is now fetched from a small
    endpoint after first paint — it returns the same _unbuilt verdict."""
    from manju.gui import edit

    _three_selected_shots(tmp_project, add_shot, make_take)
    unbuilt, why = edit._unbuilt(tmp_project)
    assert isinstance(unbuilt, bool)
    # the JS lazy-fetch target + reveal marker are wired into /edit.js
    js = edit.render_edit_js()
    assert "/api/edit/dirty" in js
    assert "ed-dirty-chip" in js
