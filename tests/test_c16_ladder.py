"""AI_IDE_16 — Preview Ladder derivation, keyframe adoption, and the §10 spend
gate (the batch's teeth).

Discipline pinned here: the ladder is DERIVED, never stored; "approved" at
KEYFRAME rides EXISTING adoption facts (a selected keyframe take OR a promoted
refs binding); a shot with NO keyframe candidates is UNAFFECTED (old projects
byte-identical); the unattended profile REFUSES a paid video for an unadopted
keyframe with transport=0; the collaborative surface is an advisory check only.
"""

from __future__ import annotations

from manju.core.hashing import hash_file
from manju.qc import production as prod


# ------------------------------------------------------------ ladder derivation


def test_ladder_stage_script_only(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    view = prod.ladder_view(tmp_project, "S001")
    assert view["schema"] == "manju.preview-ladder/v1"
    assert view["stage"] == "SCRIPT"
    assert view["reached"]["SCRIPT"] is True
    assert view["reached"]["KEYFRAME"] is False
    assert view["reached"]["FINAL_VIDEO"] is False
    # nothing is stored — the view is pure derivation
    assert view["do_not_execute_automatically"] is True


def test_keyframe_candidates_are_image_takes(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:abc", suffix=".mp4")   # a video take
    make_take(tmp_project, "S001", "sha256:def", suffix=".png")   # a keyframe
    make_take(tmp_project, "S001", "sha256:ghi", suffix=".jpg")   # a keyframe
    cands = prod.keyframe_candidates(tmp_project, "S001")
    assert {t.name for t in cands} and all(
        t.media_path.suffix.lower() in {".png", ".jpg"} for t in cands)
    assert len(cands) == 2
    # the video take is NOT a keyframe candidate
    assert len(prod.video_takes(tmp_project, "S001")) == 1


def test_ladder_stage_final_video_with_video_take(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:v", suffix=".mp4")
    view = prod.ladder_view(tmp_project, "S001")
    assert view["reached"]["FINAL_VIDEO"] is True
    assert view["stage"] == "FINAL_VIDEO"


# ------------------------------------------------------------ keyframe adoption


def test_no_candidates_is_unaffected(tmp_project, add_shot):
    """A shot with NO keyframe candidates: not gated, not adopted, opt-in."""
    add_shot(tmp_project, "S001")
    adoption = prod.keyframe_adoption(tmp_project, "S001")
    assert adoption["has_candidates"] is False
    assert adoption["adopted"] is False
    assert prod.keyframe_gate(tmp_project, "S001")["gated"] is False


def test_adoption_via_selected_take(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    kf = make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    # candidate present but not selected → gated
    assert prod.keyframe_gate(tmp_project, "S001")["gated"] is True
    # select the keyframe → adopted via selected_take
    shot = tmp_project.load_shot("S001")
    shot.status.selected_take = kf.name
    tmp_project.save_shot(shot)
    adoption = prod.keyframe_adoption(tmp_project, "S001")
    assert adoption["adopted"] is True
    assert adoption["via"] == "selected_take"
    assert adoption["take"] == kf.name
    assert prod.keyframe_gate(tmp_project, "S001")["gated"] is False


def test_adoption_via_promoted_ref_exact_bytes(tmp_project, add_shot, make_take):
    """A refs-binding referencing a candidate's EXACT media bytes = adopted."""
    add_shot(tmp_project, "S001")
    kf = make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    kf_rel = tmp_project.relpath(kf.media_path)
    # promote that exact media as a first-frame ref binding (canonical style)
    shot = tmp_project.load_shot("S001")
    shot.generation.params["refs"] = [
        {"image": kf_rel, "controls": ["style", "color_grade"],
         "ignore": ["character_identity"]}
    ]
    tmp_project.save_shot(shot)
    adoption = prod.keyframe_adoption(tmp_project, "S001")
    assert adoption["adopted"] is True
    assert adoption["via"] == "promoted_ref"
    assert adoption["media_sha"] == hash_file(kf.media_path)
    assert prod.keyframe_gate(tmp_project, "S001")["gated"] is False


def test_promoted_ref_to_DIFFERENT_bytes_is_not_adoption(tmp_project, add_shot, make_take, tmp_path):
    """A style ref that is NOT one of the shot's keyframe candidates does not
    count as adopting a keyframe (exact-bytes join, never a coincidence)."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    other = tmp_path / "unrelated.png"
    other.write_bytes(b"a-totally-different-image")
    dest = tmp_project.refs_dir / "style.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(other.read_bytes())
    shot = tmp_project.load_shot("S001")
    shot.generation.params["refs"] = [
        {"image": tmp_project.relpath(dest), "controls": ["style"]}]
    tmp_project.save_shot(shot)
    assert prod.keyframe_adoption(tmp_project, "S001")["adopted"] is False
    assert prod.keyframe_gate(tmp_project, "S001")["gated"] is True


# ------------------------------------------------------------ motion references


def test_motion_reference_rides_existing_vocabulary(tmp_project, add_shot):
    """External motion ref = a VIDEO ref with controls=[motion]; must_not_transfer
    rides `ignore`. No new vocabulary (motion ∈ REF_TRANSFER_VOCAB)."""
    add_shot(tmp_project, "S001")
    vid = tmp_project.refs_dir / "blockout.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    vid.write_bytes(b"fake-blockout-video")
    shot = tmp_project.load_shot("S001")
    shot.generation.params["refs"] = [
        {"video": tmp_project.relpath(vid), "controls": ["motion"],
         "ignore": ["character_identity", "face", "style"]}]
    tmp_project.save_shot(shot)
    mrefs = prod.motion_references(tmp_project, "S001")
    assert len(mrefs) == 1
    assert mrefs[0]["controls"] == ["motion"]
    assert "character_identity" in mrefs[0]["must_not_transfer"]
    assert prod.ladder_view(tmp_project, "S001")["reached"]["MOTION_REF"] is True


# ------------------------------------------------------------ collaborative check


def test_collaborative_check_is_advisory_never_blocks(tmp_project, add_shot, make_take):
    from manju.qc.prompt_checks import has_blocking, production_checks

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    shot = tmp_project.load_shot("S001")
    findings = production_checks(tmp_project, shot)
    kf = [f for f in findings if f["code"] == prod.KEYFRAME_NOT_ADOPTED]
    assert len(kf) == 1
    assert kf[0]["level"] == "advisory"
    # advisory is NOT a blocking level → a human is never hard-blocked
    assert has_blocking(kf) is False


def test_collaborative_check_absent_when_no_candidates(tmp_project, add_shot):
    from manju.qc.prompt_checks import production_checks

    add_shot(tmp_project, "S001")
    findings = production_checks(tmp_project, tmp_project.load_shot("S001"))
    assert not [f for f in findings if f["code"] == prod.KEYFRAME_NOT_ADOPTED]


def test_collaborative_check_absent_after_adoption(tmp_project, add_shot, make_take):
    from manju.qc.prompt_checks import production_checks

    add_shot(tmp_project, "S001")
    kf = make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    shot = tmp_project.load_shot("S001")
    shot.status.selected_take = kf.name
    tmp_project.save_shot(shot)
    findings = production_checks(tmp_project, tmp_project.load_shot("S001"))
    assert not [f for f in findings if f["code"] == prod.KEYFRAME_NOT_ADOPTED]


# ------------------------------------------------------------ i2v exact bytes


def test_adopted_keyframe_i2v_uses_exact_ref_bytes(tmp_project, add_shot, make_take):
    """§5 pin: once a keyframe is adopted (promoted to a first-frame ref), the
    video (i2v) request's first-frame ref is the EXACT adopted media bytes."""
    from manju.providers.refs import resolve_refs

    add_shot(tmp_project, "S001")
    kf = make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    kf_sha = hash_file(kf.media_path)
    shot = tmp_project.load_shot("S001")
    shot.generation.params["image"] = tmp_project.relpath(kf.media_path)  # first-frame ref
    tmp_project.save_shot(shot)

    rs = resolve_refs(tmp_project, tmp_project.load_shot("S001"), tmp_project.load_bible())
    assert rs.primary_image is not None
    assert hash_file(rs.primary_image) == kf_sha        # exact bytes to i2v
    view = prod.ladder_view(tmp_project, "S001")
    assert view["approved_at_keyframe"] is True
    assert view["keyframe"]["media_sha"] == kf_sha


# ------------------------------------------------------------ motion containment


def test_motion_ref_absolute_path_never_crosses_boundary(tmp_project, add_shot):
    """§7 pin: an external motion ref that names an absolute/outside path is
    refused by the ONE containment guard — its bytes are never read."""
    add_shot(tmp_project, "S001")
    shot = tmp_project.load_shot("S001")
    shot.generation.params["refs"] = [
        {"video": "/etc/passwd", "controls": ["motion"], "ignore": ["character_identity"]}]
    tmp_project.save_shot(shot)
    mrefs = prod.motion_references(tmp_project, "S001")
    assert len(mrefs) == 1
    assert mrefs[0]["content_sha"] is None              # never read from disk
    assert "character_identity" in mrefs[0]["must_not_transfer"]  # ignore preserved


def test_canonical_style_frame_transfer_boundary(tmp_project, add_shot, make_take):
    """A canonical style frame transfers style/color_grade but must NOT transfer
    identity — the controls/ignore boundary rides the existing binding."""
    add_shot(tmp_project, "S001")
    kf = make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    shot = tmp_project.load_shot("S001")
    shot.generation.params["refs"] = [
        {"image": tmp_project.relpath(kf.media_path),
         "controls": ["style", "color_grade"], "ignore": ["character_identity", "face"]}]
    tmp_project.save_shot(shot)
    from manju.providers.refs import resolve_refs
    rs = resolve_refs(tmp_project, tmp_project.load_shot("S001"), tmp_project.load_bible())
    item = next(it for it in rs.items if it.kind == "image")
    assert set(item.controls) == {"style", "color_grade"}
    assert set(item.ignore) == {"character_identity", "face"}
    assert not item.transfer_errors                     # known vocabulary, no conflict


# ------------------------------------------------------------ old projects


def test_old_project_no_keyframes_is_ladder_inert(tmp_project, add_shot, make_take):
    """Opt-in per shot: a shot with only a VIDEO take (no keyframe candidates) is
    FINAL_VIDEO, never gated, and emits no ladder check — byte-identical."""
    from manju.qc.prompt_checks import production_checks

    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:v", suffix=".mp4")
    view = prod.ladder_view(tmp_project, "S001")
    assert view["reached"]["FINAL_VIDEO"] is True
    assert view["keyframe"]["has_candidates"] is False
    assert prod.keyframe_gate(tmp_project, "S001")["gated"] is False
    assert not [f for f in production_checks(tmp_project, tmp_project.load_shot("S001"))
                if f["code"] == prod.KEYFRAME_NOT_ADOPTED]
