"""Why-stale field diffs (UX-STUDY #2, Nx pattern).

A hash can only say THAT the spec moved; the take's spec_snapshot (captured
at generation time by Provider._register) lets staleness name WHERE. Takes
made before snapshots existed degrade to the old generic note.
"""

from __future__ import annotations

from manju.build.stale import ShotState, evaluate_shot
from manju.core.models import TakeSidecar
from manju.core.spec import compute_spec_hash, diff_spec_fields, spec_payload
from manju.core.yamlio import write_yaml


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name)
    )


def test_diff_spec_fields_paths():
    old = {"camera": {"shot_size": "medium", "movement": "still"}, "duration": 3}
    new = {"camera": {"shot_size": "close", "movement": "still"}, "duration": 3,
           "quality": {"grade": "high"}}
    assert diff_spec_fields(old, new) == ["camera.shot_size", "quality"]


def test_stale_note_names_the_fields(tmp_project, add_shot, tmp_path):
    shot = add_shot(tmp_project, "S001")
    media = tmp_path / "t.mp4"
    media.write_bytes(b"x")
    tmp_project.register_take(
        "S001", media,
        TakeSidecar(provider="test", spec_hash=compute_spec_hash(shot, tmp_project.load_bible()),
                    spec_snapshot=spec_payload(shot, tmp_project.load_bible())))
    _select(tmp_project, "S001", "take_01")

    # move two picture-side fields: one on the shot, one in the bible excerpt
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("camera", {}).__setitem__("shot_size", "close_up"))
    scenes = tmp_project.root / "bible" / "scenes.yaml"
    write_yaml(scenes, {"convenience_store": {"name": "便利店", "lighting": "warm"}})

    st = evaluate_shot(tmp_project, tmp_project.load_shot("S001"))
    assert st.state == ShotState.STALE
    assert "camera.shot_size" in st.note
    assert "scene_bible" in st.note


def test_snapshotless_take_degrades_to_generic_note(tmp_project, add_shot, tmp_path):
    shot = add_shot(tmp_project, "S001")
    media = tmp_path / "t.mp4"
    media.write_bytes(b"x")
    tmp_project.register_take(
        "S001", media,
        TakeSidecar(provider="test",
                    spec_hash=compute_spec_hash(shot, tmp_project.load_bible())))
    _select(tmp_project, "S001", "take_01")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("camera", {}).__setitem__("shot_size", "close_up"))
    st = evaluate_shot(tmp_project, tmp_project.load_shot("S001"))
    assert st.state == ShotState.STALE
    assert st.note == "shot spec changed after this take was generated"


def test_generated_takes_carry_snapshots(tmp_project, add_shot):
    """The real provider path (redo -> fallback chain -> caption_card)
    records the snapshot without any caller involvement."""
    from manju.build.graph import redo_shot

    add_shot(tmp_project, "S001")
    takes = redo_shot(tmp_project, "S001", actor="human")
    assert takes
    info = tmp_project.get_take("S001", takes[0])
    assert info.sidecar.spec_snapshot
    assert info.sidecar.spec_snapshot.get("scene") == "convenience_store"


def test_status_carries_stale_notes(tmp_project, add_shot, tmp_path):
    """Acceptance finding #1: the takeover surface (`manju status --json`)
    must carry the why-stale evidence, not just the state buckets."""
    from manju.build.status import project_status

    shot = add_shot(tmp_project, "S001")
    media = tmp_path / "t.mp4"
    media.write_bytes(b"x")
    tmp_project.register_take(
        "S001", media,
        TakeSidecar(provider="test",
                    spec_hash=compute_spec_hash(shot, tmp_project.load_bible()),
                    spec_snapshot=spec_payload(shot, tmp_project.load_bible())))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01"))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("camera", {}).__setitem__("shot_size", "close_up"))
    info = project_status(tmp_project)
    assert "camera.shot_size" in info["shot_notes"]["S001"]


def test_snapshot_bloat_guard(tmp_project, add_shot, monkeypatch, tmp_path):
    """R25: a pathological (>32KB) spec payload is not embedded per take."""
    from manju.build.graph import redo_shot
    from manju.core.yamlio import write_yaml

    write_yaml(tmp_project.root / "bible" / "scenes.yaml",
               {"convenience_store": {"name": "便利店", "description": "巨" * 40000}})
    add_shot(tmp_project, "S001")
    takes = redo_shot(tmp_project, "S001", actor="human")
    info = tmp_project.get_take("S001", takes[0])
    assert info.sidecar.spec_snapshot is None  # dropped, staleness degrades
