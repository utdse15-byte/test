"""AI_IDE_08_10_12C WP5 — accepted observed state, continuation source gate,
repair routing. All DERIVED views over existing evidence (assurance + bound
endpoint observations); nothing persisted, nothing auto-executed, never a
build input.

Red-first: `manju.qc.production` did not exist; a continuation shot whose
source was NOT accepted (or whose real ending was never observed) sailed
through `manju prompt --check` with zero findings.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess

import pytest

from manju.core.hashing import hash_file
from manju.core.models import TakeSidecar
from manju.qc.agent_review import qc_brief, record_verdicts

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg + ffprobe required (accepted state needs a real, probe-able take)",
)

V2 = "manju.qc.verdict/v2"

END_OBS_STILL_DOWN = {
    "dimension": "POSE_ACTION_PHASE", "position": "END",
    "value": "她仍低头看着硬币", "visibility": "VISIBLE",
    "evidence_refs": ["frame:end"], "confidence": 0.9,
}


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _real_take(project, shot_id, *, color="red", select=True):
    tmp = project.root / f"_src_{shot_id}_{color}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=160x120:d=1:r=24", "-pix_fmt", "yuv420p", str(tmp)],
        check=True, capture_output=True,
    )
    take = project.register_take(shot_id, tmp, TakeSidecar(provider="test", spec_hash="h"))
    tmp.unlink()
    if select:
        _select(project, shot_id, take.name)
    return take


def _row(project, shot_id):
    return next(r for r in qc_brief(project, [shot_id])["shots"] if r["shot"] == shot_id)


def _verdict(row, *, observed="present", observed_states=None, decision=None):
    v = {
        "schema": V2, "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [{"expectation_id": e["id"], "observed": observed}
                         for e in row["expectations"]],
        "findings": [], "reviewer": {"kind": "model_visual", "name": "t"},
    }
    if observed_states is not None:
        v["observed_states"] = observed_states
    if decision is not None:
        v["decision"] = decision
    return v


def _accepted_with_endpoint(project, add_shot, shot_id="S001", *,
                            observed_states=None):
    """An ACCEPTED shot (all promises PASS) whose ending was — per the authored
    prompt — '她已经抬头', but whose REAL reviewed ending observation says
    otherwise (fixture 8)."""
    add_shot(project, shot_id,
             quality={"must_show": ["红色雨伞出现"]},
             action={"main": "她低头看硬币,最后抬起头"})
    _real_take(project, shot_id)
    row = _row(project, shot_id)
    record_verdicts(project, _verdict(
        row, observed_states=observed_states))
    return row


def _tree_hashes(root):
    # The §10.1/10.2 proposition is about TRUTH (Bible/Shot/Timeline — see
    # the never-writes-back docstring). `.manju` is contractually DISPOSABLE
    # runtime (README §disciplines) and 战役③'s probe-fact cache legitimately
    # writes there during QC; including it here would pin "observing may not
    # warm a disposable cache", which is not the claim.
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".manju" not in p.relative_to(root).parts
    }


# ------------------------------------------------ accepted observed state


@needs_ffmpeg
def test_accepted_observed_state_binds_current_media(tmp_project, add_shot):
    from manju.qc.production import accepted_observed_state

    _accepted_with_endpoint(tmp_project, add_shot,
                            observed_states=[END_OBS_STILL_DOWN])
    take = tmp_project.get_take("S001", tmp_project.load_shot("S001").status.selected_take)

    state = accepted_observed_state(tmp_project, "S001")
    assert state["status"] == "current"
    assert state["assurance_state"] == "accepted"
    assert state["media_sha256"] == hash_file(take.media_path)
    assert [o["value"] for o in state["observed_endpoint"]] == ["她仍低头看着硬币"]


@needs_ffmpeg
def test_observed_state_goes_stale_on_media_replacement(tmp_project, add_shot):
    """§10.2: any binding change stales the observed state — same-name byte
    replacement included. Never fail-open to the old observation."""
    from manju.qc.production import accepted_observed_state

    _accepted_with_endpoint(tmp_project, add_shot,
                            observed_states=[END_OBS_STILL_DOWN])
    take = tmp_project.get_take("S001", tmp_project.load_shot("S001").status.selected_take)
    take.media_path.write_bytes(b"REPLACED-same-name-different-bytes")

    state = accepted_observed_state(tmp_project, "S001")
    assert state["status"] != "current"
    assert state["observed_endpoint"] == []  # a stale observation is not evidence


@needs_ffmpeg
def test_observed_state_never_writes_back(tmp_project, add_shot):
    """§10.1/10.2: transient observations never touch Bible/Shot/Timeline."""
    from manju.qc.production import accepted_observed_state

    _accepted_with_endpoint(tmp_project, add_shot,
                            observed_states=[END_OBS_STILL_DOWN])
    before = _tree_hashes(tmp_project.root)
    accepted_observed_state(tmp_project, "S001")
    assert _tree_hashes(tmp_project.root) == before


# ------------------------------------------------ continuation source gate


@needs_ffmpeg
def test_continuation_gate_blocks_unaccepted_source(tmp_project, add_shot):
    """RED pre-fix: nothing checked the continuation source at all."""
    from manju.qc.production import continuation_view

    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞出现"]})
    _real_take(tmp_project, "S001")  # unreviewed -> NOT accepted
    add_shot(tmp_project, "S002", continuity={"prev": "S001"})
    _real_take(tmp_project, "S002")

    view = continuation_view(tmp_project, "S002")
    assert view is not None
    codes = {c["code"] for c in view["checks"]}
    assert "CONTINUATION_SOURCE_NOT_ACCEPTED" in codes


@needs_ffmpeg
def test_continuation_gate_blocks_unobserved_endpoint(tmp_project, add_shot):
    from manju.qc.production import continuation_view

    _accepted_with_endpoint(tmp_project, add_shot)  # accepted, NO endpoint obs
    add_shot(tmp_project, "S002", continuity={"prev": "S001"})
    _real_take(tmp_project, "S002")

    view = continuation_view(tmp_project, "S002")
    codes = {c["code"] for c in view["checks"]}
    assert "CONTINUATION_SOURCE_NOT_ACCEPTED" not in codes
    assert "CONTINUATION_ENDPOINT_UNOBSERVED" in codes


@needs_ffmpeg
def test_continuation_gate_detects_pinned_hash_mismatch(tmp_project, add_shot):
    from manju.qc.production import continuation_view

    _accepted_with_endpoint(tmp_project, add_shot,
                            observed_states=[END_OBS_STILL_DOWN])
    add_shot(tmp_project, "S002", continuity={
        "prev": "S001",
        "source_media_sha256": "sha256:" + "0" * 64,  # authored against other bytes
    })
    _real_take(tmp_project, "S002")

    view = continuation_view(tmp_project, "S002")
    codes = {c["code"] for c in view["checks"]}
    assert "CONTINUATION_SOURCE_HASH_MISMATCH" in codes


@needs_ffmpeg
def test_continuation_view_carries_real_ending_not_prompt_ending(tmp_project, add_shot):
    """Fixture 8: authored prompt expected '她已经抬头'; the reviewed REAL
    ending is '她仍低头看着硬币'. The view must carry the observation, and the
    checks must be clean when source is accepted+observed+hash-verifiable."""
    from manju.qc.production import continuation_view

    _accepted_with_endpoint(tmp_project, add_shot,
                            observed_states=[END_OBS_STILL_DOWN])
    take = tmp_project.get_take("S001", tmp_project.load_shot("S001").status.selected_take)
    add_shot(tmp_project, "S002", continuity={"prev": "S001"})
    _real_take(tmp_project, "S002")

    view = continuation_view(tmp_project, "S002")
    assert view["source_shot"] == "S001"
    assert view["source_media_sha256"] == hash_file(take.media_path)
    assert view["observed_endpoint"][0]["value"] == "她仍低头看着硬币"
    assert view["checks"] == []
    # advisory boundaries for the Skill: what the source visibly completed
    assert view["completed_beats"]
    # and it is only ever a proposal input, never auto-applied
    assert view["do_not_execute_automatically"] is True


def test_no_continuation_declared_returns_none(tmp_project, add_shot):
    from manju.qc.production import continuation_view

    add_shot(tmp_project, "S001")
    assert continuation_view(tmp_project, "S001") is None


# ------------------------------------------------ repair routing (10.4)


def test_repair_routes_map_to_existing_safe_paths_only():
    from manju.qc.production import repair_route

    for dispo, path in {
        "KEEP": "human_select",
        "FIX_IN_POST": "repair_op",
        "EDIT_DONT_REGENERATE": "timeline_roundtrip_proposal",
        "REROLL": "explicit_redo",
        "REWRITE_SOURCE": "director_proposal",
    }.items():
        route = repair_route(dispo)
        assert route["path"] == path
        assert route["do_not_execute_automatically"] is True
        assert "manju" in route["command"]  # points at an EXISTING command
    assert repair_route("NOT_A_DISPOSITION") is None
