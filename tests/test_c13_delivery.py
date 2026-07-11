"""AI_IDE_13C — manju.delivery-manifest/v1, NLE handoff & variant boundaries.

Red-first (contract §5.2's twelve fixtures + §13 test contract). Everything is
ffmpeg-free: finals/captions/otio/cover are fabricated with hand-written
``.key.json`` sidecars and a manual-mode ``timeline.json`` makes the content-key
recompute deterministic — exactly the stance of tests/test_c07_baseline.py.

The manifest is a PURE DERIVATION: these tests prove it (a) reuses the same
exports engine + 07C release assessment, (b) never becomes an export input
(delete / hand-edit inert), (c) binds NLE media by exact sha256 + frame mapping,
(d) keeps technical / human / published separated, and (e) writes a safe,
reproducible bundle of only its own registered files.
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest
from typer.testing import CliRunner

from manju.build import delivery as D
from manju.build.exportstatus import deliverables_data, mark_verified
from manju.cli import app
from manju.core.container import Project
from manju.core.hashing import hash_file
from manju.core.models import (
    CaptionLine,
    TakeSidecar,
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import read_yaml, write_yaml

runner = CliRunner()


# ------------------------------------------------------------- fabricators


def _set_profiles(project: Project, profiles: dict) -> None:
    """Inject additive ``delivery_profiles`` into project.yaml (extra=allow)."""
    data = read_yaml(project.root / "project.yaml") or {}
    data["delivery_profiles"] = profiles
    write_yaml(project.root / "project.yaml", data)


def _register(project: Project, shot: str, provider: str = "test") -> tuple[str, str]:
    tmp = project.root / f"_src_{shot}_{provider}.mp4"
    tmp.write_bytes(b"take-" + f"{shot}{provider}".encode())
    info = project.register_take(shot, tmp, TakeSidecar(provider=provider, spec_hash="manual"))
    return info.name, project.relpath(info.media_path)


def _manual_timeline(project: Project, video=None) -> Timeline:
    write_yaml(project.rules_path, TimelineRules(mode="manual").model_dump())
    video = video or [VideoClip(shot="S001", take="take_01",
                                source="media/gen/S001/take_01.mp4",
                                start_ms=0, duration_ms=2000)]
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920,
        duration_ms=sum(c.duration_ms for c in video),
        tracks=TimelineTracks(video=video),
    )
    project.save_timeline(tl)
    return tl


def _final_key(project: Project, tl: Timeline) -> str:
    from manju.media.render import final_content_key

    ass = project.captions_dir / "captions.ass"
    return final_content_key(project, tl, ass_file=ass if ass.exists() else None, target="final")


def _fab_final(project: Project, key: str, *, version: int = 1, data: bytes = b"final-bytes",
               run_id=None, output_sha256="auto") -> "os.PathLike":
    project.final_dir.mkdir(parents=True, exist_ok=True)
    p = project.final_dir / f"final_v{version}.mp4"
    p.write_bytes(data)
    sc = {"final_key": key, "target": "final", "created_at": "2026-07-06T10:00:00+00:00"}
    if run_id:
        sc["run_id"] = run_id
    if output_sha256 == "auto":
        sc["output_sha256"] = hash_file(p)
    elif output_sha256:
        sc["output_sha256"] = output_sha256
    (project.final_dir / f"final_v{version}.key.json").write_text(json.dumps(sc), encoding="utf-8")
    return p


def _clean_final(project: Project):
    """A deterministic up-to-date current final (manual timeline + matching key)."""
    tl = _manual_timeline(project)
    return _fab_final(project, _final_key(project, tl), output_sha256="auto"), tl


def _write_srt(project: Project) -> None:
    project.captions_dir.mkdir(parents=True, exist_ok=True)
    (project.captions_dir / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n这不可能。\n", encoding="utf-8")


def _write_otio(project: Project, name: str, *, stale_vs_timeline: bool = False) -> "os.PathLike":
    d = project.exports_dir / "otio"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.otio"
    p.write_text('{"OTIODOC": 1}', encoding="utf-8")
    if stale_vs_timeline:
        # make the otio OLDER than timeline.json so exportstatus judges it stale
        old = 1_000_000_000
        os.utime(p, (old, old))
    return p


def _translate(project: Project, lang: str, sid: str, text: str) -> None:
    """Give a locale line a real translation (add_locale seeds empty text), keeping
    the stored base_hash so staleness is driven only by the SOURCE text moving."""
    path = project.root / "locales" / lang / "lines.yaml"
    data = read_yaml(path) or {}
    entry = dict(data.get(sid) or {})
    entry["text"] = text
    data[sid] = entry
    write_yaml(path, data)


def _cover_with_key(project: Project, key: str) -> None:
    d = project.exports_dir / "packaging"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cover.png").write_bytes(b"PNGDATA")
    (d / "cover.key.json").write_text(json.dumps({"key": key, "kind": "cover"}), encoding="utf-8")


# ============================================================ §5.2 fixtures


def test_f1_master_and_format_only_share_timeline_semantic_digest(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {
        "master": {"variant_kind": "master"},
        "yt_vertical": {"variant_kind": "format_only", "base_profile": "master",
                        "frame": {"width": 1080, "height": 1920, "strategy": "center_crop"}},
    })
    master = D.build_manifest(tmp_project, "master")
    D.materialize_manifest(tmp_project, master)          # so format-only can look it up
    fmt = D.build_manifest(tmp_project, "yt_vertical")
    # frame size differs, but the SEGMENT identity/order/duration digest is shared
    assert (master["variant"]["source_timeline_digest"]
            == fmt["variant"]["source_timeline_digest"])
    codes = {d["code"] for d in fmt["diagnostics"]}
    assert "VARIANT_KIND_MISMATCH" not in codes          # a genuine format-only


def test_f2_format_only_dropped_segment_is_variant_kind_mismatch(tmp_project, add_shot):
    # PIN FLIPPED (POST_COMPLETION_HARDENING WP5 7.3, claim 11): this test used
    # to pin the MATERIALIZED master manifest as the variant's base identity —
    # the exact manifest-becomes-an-input violation (hand-editing the report
    # shifted the verdict; see tests/test_h2_hardening.py::test_26). The base
    # is now the EXPLICIT in-memory derivation passed as ``base_master`` (or an
    # in-process re-derivation); the invariant SEMANTICS are unchanged: a
    # dropped segment against the pinned base is still VARIANT_KIND_MISMATCH.
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    two = [VideoClip(shot="S001", take="t", source="media/gen/S001/take_01.mp4",
                     start_ms=0, duration_ms=1000),
           VideoClip(shot="S002", take="t", source="media/gen/S002/take_01.mp4",
                     start_ms=1000, duration_ms=1000)]
    _manual_timeline(tmp_project, two)
    _set_profiles(tmp_project, {"yt": {"variant_kind": "format_only", "base_profile": "master"}})
    master = D.build_manifest(tmp_project, "master")   # the explicit base derivation
    # a segment silently disappears from the cut the format-only is built from
    _manual_timeline(tmp_project, two[:1])
    fmt = D.build_manifest(tmp_project, "yt", base_master=master)
    codes = {d["code"] for d in fmt["diagnostics"]}
    assert "VARIANT_KIND_MISMATCH" in codes
    assert fmt["release"]["delivery_state"]["technical_ready"] is False


def test_f3_cutdown_requires_explicit_source_revision(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    # (a) cutdown WITHOUT a source ref → rejected
    _set_profiles(tmp_project, {"short": {"variant_kind": "editorial_cutdown"}})
    bad = D.build_manifest(tmp_project, "short")
    assert "CUTDOWN_SOURCE_REQUIRED" in {d["code"] for d in bad["diagnostics"]}
    assert bad["release"]["delivery_state"]["technical_ready"] is False
    # (b) cutdown WITH an explicit adopted source revision → recorded, no reject
    _set_profiles(tmp_project, {"short": {
        "variant_kind": "editorial_cutdown",
        "cutdown_source": {"kind": "timeline_revision", "ref": "sha256:accepted-rev"}}})
    good = D.build_manifest(tmp_project, "short")
    assert "CUTDOWN_SOURCE_REQUIRED" not in {d["code"] for d in good["diagnostics"]}
    assert good["variant"]["cutdown_source"]["ref"] == "sha256:accepted-rev"
    assert good["variant"]["source_timeline_digest"]                 # digest recorded


def test_f4_localized_stale_base_hash_blocks(tmp_project, add_shot):
    from manju.core.locale import add_locale

    shot = add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    add_locale(tmp_project, "en")                     # stores base_hash of the current text
    _translate(tmp_project, "en", "S001", "This can't be.")   # a real translation
    # the source dialogue changes AFTER translation → stored base hash goes stale
    shot.dialogue.text = "完全不同的台词"
    tmp_project.save_shot(shot)
    _set_profiles(tmp_project, {"en_ver": {"variant_kind": "localized", "locale": "en"}})
    man = D.build_manifest(tmp_project, "en_ver")
    assert man["localization"]["status"] == "STALE"
    assert "LOCALE_BASE_HASH_STALE" in {d["code"] for d in man["diagnostics"]}
    assert man["release"]["delivery_state"]["technical_ready"] is False


def test_f5_nle_references_old_selected_take_is_stale(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tname, src = _register(tmp_project, "S001")
    tl = _manual_timeline(tmp_project, [VideoClip(shot="S001", take=tname, source=src,
                                                  start_ms=0, duration_ms=2000)])
    _fab_final(tmp_project, _final_key(tmp_project, tl))
    _write_otio(tmp_project, tmp_project.load_config().name, stale_vs_timeline=True)
    man = D.build_manifest(tmp_project, "master")
    assert man["nle"] is not None and man["nle"]["format"] == "OTIO"
    assert "NLE_STALE" in {d["code"] for d in man["diagnostics"]}


def test_f6_nle_missing_media_bytes_errors(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tname, src = _register(tmp_project, "S001")
    tl = _manual_timeline(tmp_project, [VideoClip(shot="S001", take=tname, source=src,
                                                  start_ms=0, duration_ms=2000)])
    _fab_final(tmp_project, _final_key(tmp_project, tl))
    _write_otio(tmp_project, tmp_project.load_config().name)
    # the referenced media disappears
    tmp_project.resolve(src).unlink()
    man = D.build_manifest(tmp_project, "master")
    assert "NLE_MEDIA_UNRESOLVED" in {d["code"] for d in man["diagnostics"]}
    m0 = man["nle"]["media"][0]
    assert m0["media_state"] == "MISSING" and m0["asset_sha256"] is None


def test_f7_cover_stale(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _cover_with_key(tmp_project, "sha256:deliberately-wrong-cover-key")
    man = D.build_manifest(tmp_project, "master")
    cover = next(a for a in man["artifacts"] if a["role"] == "POSTER")
    assert cover["state"] == "STALE"


def test_f8_required_captions_missing_blocks(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)                                     # no captions written
    _set_profiles(tmp_project, {"yt": {"variant_kind": "master",
                                       "required_roles": ["MASTER_VIDEO", "CAPTIONS_SRT"]}})
    man = D.build_manifest(tmp_project, "yt")
    srt = next(a for a in man["artifacts"] if a["role"] == "CAPTIONS_SRT")
    assert srt["state"] == "MISSING"
    assert man["release"]["required_roles_satisfied"] is False
    assert man["release"]["delivery_state"]["technical_ready"] is False


def test_f9_final_exists_but_run_incomplete_is_draft_not_ready(tmp_project, add_shot):
    from manju.build import attempts as A

    add_shot(tmp_project, "S001")
    tl = _manual_timeline(tmp_project)
    _fab_final(tmp_project, _final_key(tmp_project, tl), run_id="run_x")
    A.append_run_started(tmp_project, "run_x", target="final", gen="missing")
    A.append_attempt_started(tmp_project, "run_x", "att_lost", stage="render")
    man = D.build_manifest(tmp_project, "master")
    assert "RUN_INCOMPLETE" in man["release"]["blocker_codes"]     # consumed from 07C verbatim
    assert man["release"]["delivery_state"]["technical_ready"] is False


def test_f10_metadata_with_token_sets_credentials_present(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    meta = tmp_project.exports_dir / "meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"title": "x", "note": "token sk-abcdefghijklmnopqrstuvwxyz012345"}),
                    encoding="utf-8")
    _set_profiles(tmp_project, {"yt": {"variant_kind": "platform_package", "platform": "youtube",
                                       "metadata_file": "exports/meta.json"}})
    man = D.build_manifest(tmp_project, "yt")
    assert man["platform_handoff"]["credentials_present"] is True
    assert "PLATFORM_CREDENTIAL_LEAK" in {d["code"] for d in man["diagnostics"]}
    # the secret NEVER enters the manifest — only the project-relative path
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in json.dumps(man, ensure_ascii=False)


def test_f11_bundle_rejects_traversal_and_duplicate_entries(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    # a traversal-named artifact must be refused by the bundle guard
    bad = dict(man)
    bad["artifacts"] = list(man["artifacts"]) + [
        {"artifact_id": "x", "role": "OTHER_DECLARED", "path": "../escape.mp4",
         "state": "TECHNICALLY_VERIFIED"}]
    with pytest.raises(D.DeliveryManifestError):
        D._bundle_members(tmp_project, bad)
    # a duplicate arcname must be refused
    dup = dict(man)
    a0 = dict(man["artifacts"][0]); a0 = {**a0, "path": man["artifacts"][0]["path"]}
    real = next(a for a in man["artifacts"] if a["path"] and a["state"] != "MISSING")
    dup["artifacts"] = list(man["artifacts"]) + [
        {"artifact_id": "dupe", "role": "OTHER_DECLARED", "path": real["path"],
         "state": "TECHNICALLY_VERIFIED"}]
    with pytest.raises(D.DeliveryManifestError):
        D._bundle_members(tmp_project, dup)


def test_f12_nle_opened_pending_editor_not_approved(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tname, src = _register(tmp_project, "S001")
    tl = _manual_timeline(tmp_project, [VideoClip(shot="S001", take=tname, source=src,
                                                  start_ms=0, duration_ms=2000)])
    _fab_final(tmp_project, _final_key(tmp_project, tl))
    _write_otio(tmp_project, tmp_project.load_config().name)
    man = D.build_manifest(tmp_project, "master")
    assert man["nle"]["verification"]["opened_in_target_app"] == "PENDING"
    assert man["release"]["delivery_state"]["editor_approved"] is False


# ============================================================ §13 contract


def test_manifest_deterministic_and_rematerialize_stable(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    a = D.build_manifest(tmp_project, "master")
    b = D.build_manifest(tmp_project, "master")
    assert a == b                                          # no wall-clock field
    p1 = D.materialize_manifest(tmp_project, a)
    t1 = p1.read_text(encoding="utf-8")
    p2 = D.materialize_manifest(tmp_project, D.build_manifest(tmp_project, "master"))
    assert p2.read_text(encoding="utf-8") == t1           # same inputs → same bytes


def test_artifact_path_hash_bytes_content_key_accurate(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    final, tl = _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    master = next(a for a in man["artifacts"] if a["role"] == "MASTER_VIDEO")
    assert master["path"] == tmp_project.relpath(final)
    assert master["sha256"] == hash_file(final)
    assert master["bytes"] == final.stat().st_size
    assert master["content_key"] == _final_key(tmp_project, tl)   # the SAME key sidecar
    assert master["state"] == "TECHNICALLY_VERIFIED"


def test_manifest_delete_and_hand_edit_are_inert(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    path = D.materialize_manifest(tmp_project, man)
    # exports payload BEFORE touching the manifest
    before = deliverables_data(tmp_project)
    path.write_text('{"schema":"tampered","artifacts":"garbage"}', encoding="utf-8")
    after_edit = deliverables_data(tmp_project)
    path.unlink()
    after_delete = deliverables_data(tmp_project)
    assert before == after_edit == after_delete           # build/export never read it
    # and the manifest can be rebuilt identically from source (it owns no truth)
    assert D.build_manifest(tmp_project, "master")["manifest_digest"] == man["manifest_digest"]


def test_manifest_is_relative_and_secret_free(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    blob = json.dumps(man, ensure_ascii=False)
    assert "Authorization" not in blob and "http://" not in blob and "https://" not in blob
    for a in man["artifacts"]:
        if a["path"]:
            assert not a["path"].startswith("/")          # project-relative only


def test_old_project_without_delivery_profiles_defaults_to_master(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    # no delivery_profiles in project.yaml at all — implicit master compat pinned
    man = D.build_manifest(tmp_project, "master")
    assert man["variant"]["kind"] == "MASTER"
    # PIN FLIPPED (POST_COMPLETION_HARDENING WP5 7.6, claim 15): an EXPLICITLY
    # unknown profile id used to silently degrade to MASTER — it now errors
    # (see tests/test_h2_hardening.py::test_31). Only the implicit "master"
    # default stays permissive for old projects.
    with pytest.raises(D.DeliveryManifestError):
        D.build_manifest(tmp_project, "does_not_exist")


def test_unknown_artifact_role_policy_is_explicit(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    for a in man["artifacts"]:
        assert a["role"] in D.KNOWN_ROLES                 # every emitted role is recognised


def test_manifest_uses_same_service_as_exports(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    data = deliverables_data(tmp_project)
    # the release block is the 07C assessment folded into the SAME exports payload
    assert man["release"]["blocker_codes"] == sorted(
        {b["code"] for b in data["release_assessment"]["blockers"]})
    # every manifest artifact maps 1:1 onto a deliverables row (same nine kinds)
    assert len(man["artifacts"]) == len(data["deliverables"])


def test_nle_media_hash_matches_selected_take(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tname, src = _register(tmp_project, "S001")
    tl = _manual_timeline(tmp_project, [VideoClip(shot="S001", take=tname, source=src,
                                                  start_ms=0, duration_ms=2000)])
    _fab_final(tmp_project, _final_key(tmp_project, tl))
    _write_otio(tmp_project, tmp_project.load_config().name)
    man = D.build_manifest(tmp_project, "master")
    m0 = man["nle"]["media"][0]
    assert m0["asset_sha256"] == hash_file(tmp_project.resolve(src))   # exact bytes
    assert m0["media_state"] == "OK"


def test_nle_frame_timebase_precise_and_handles_zero(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tname, src = _register(tmp_project, "S001")
    # 24fps, a clip from source_in 1000ms for 2000ms placed at timeline 500ms
    tl = _manual_timeline(tmp_project, [VideoClip(shot="S001", take=tname, source=src,
                                                  start_ms=500, duration_ms=2000, source_in_ms=1000)])
    _fab_final(tmp_project, _final_key(tmp_project, tl))
    _write_otio(tmp_project, tmp_project.load_config().name)
    man = D.build_manifest(tmp_project, "master")
    m0 = man["nle"]["media"][0]
    assert man["nle"]["timebase"] == {"fps_num": 24, "fps_den": 1, "drop_frame": False}
    assert m0["source_in_frames"] == 24 and m0["source_out_frames"] == 72   # 1000ms,3000ms @24
    assert m0["timeline_in_frames"] == 12 and m0["timeline_out_frames"] == 60
    assert m0["handles"] == {"head_frames": 0, "tail_frames": 0}            # never guessed


def test_format_only_invariant_pure_function():
    assert D.check_format_only_invariant("sha256:a", "sha256:a") == []
    mism = D.check_format_only_invariant("sha256:a", "sha256:b")
    assert mism and mism[0]["code"] == "VARIANT_KIND_MISMATCH"
    # PIN FLIPPED (POST_COMPLETION_HARDENING WP5 7.6, claim 33): a missing base
    # identity used to be a WARNING (FORMAT_ONLY_UNVERIFIED) — an unprovable
    # format-only claim now BLOCKS (see test_h2_hardening.py::test_33).
    unv = D.check_format_only_invariant(None, "sha256:b")
    assert unv and unv[0]["code"] == "BASE_IDENTITY_MISSING"
    assert unv[0]["severity"] == "blocking"


def test_platform_package_does_not_change_narrative_cut(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    master = D.build_manifest(tmp_project, "master")
    _set_profiles(tmp_project, {"yt": {"variant_kind": "platform_package", "platform": "youtube"}})
    pkg = D.build_manifest(tmp_project, "yt")
    # a platform package is packaging-only: the timeline semantic digest is unchanged
    assert pkg["variant"]["source_timeline_digest"] == master["variant"]["source_timeline_digest"]


def test_localization_base_hash_current_is_not_stale(tmp_project, add_shot):
    from manju.core.locale import add_locale

    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    add_locale(tmp_project, "en")                          # translated against current text
    _translate(tmp_project, "en", "S001", "This can't be.")
    _set_profiles(tmp_project, {"en_ver": {"variant_kind": "localized", "locale": "en"}})
    man = D.build_manifest(tmp_project, "en_ver")
    assert man["localization"]["status"] == "CURRENT"
    assert "LOCALE_BASE_HASH_STALE" not in {d["code"] for d in man["diagnostics"]}


def test_building_manifest_creates_no_translation_or_voice(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"en_ver": {"variant_kind": "localized", "locale": "en"}})
    before = {str(p) for p in tmp_project.root.rglob("*")}
    D.build_manifest(tmp_project, "en_ver")               # read-only derivation
    after = {str(p) for p in tmp_project.root.rglob("*")}
    assert before == after                                # no auto locale/TTS materialization


def test_platform_handoff_no_credentials_and_no_upload(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"yt": {"variant_kind": "platform_package", "platform": "youtube"}})
    man = D.build_manifest(tmp_project, "yt")
    ph = man["platform_handoff"]
    assert ph["credentials_present"] is False
    assert ph["upload_supported"] is False
    assert any(c["code"] == "DISCLOSURE_REVIEW" and c["status"] == "PENDING_HUMAN"
               for c in ph["checks"])


def test_unknown_platform_profile_freshness_is_visible(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"yt": {"variant_kind": "platform_package", "platform": "tiktok"}})
    man = D.build_manifest(tmp_project, "yt")
    assert man["platform_handoff"]["rules_freshness"] == "UNKNOWN"   # not silently trusted


def test_handoff_ready_is_not_published(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"yt": {"variant_kind": "platform_package", "platform": "youtube"}})
    man = D.build_manifest(tmp_project, "yt")
    ds = man["release"]["delivery_state"]
    # a pending disclosure keeps handoff not-ready; published is never claimed
    assert ds["publish_handoff_ready"] is False
    assert ds["published"] == "unknown_not_owned"


def test_bundle_contains_only_manifest_files_with_correct_sha256sums(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    final, _ = _clean_final(tmp_project)
    _write_srt(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    out, man2 = D.write_bundle(tmp_project, man)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert "SHA256SUMS" in names and "delivery-manifest.json" in names
        assert tmp_project.relpath(final) in names        # the master IS included
        # nothing outside the manifest's registered files (no project.yaml, no bible)
        assert not any(n.startswith("bible/") or n == "project.yaml" for n in names)
        sums = zf.read("SHA256SUMS").decode()
    # SHA256SUMS lists exact bytes
    line = next(l for l in sums.splitlines() if l.endswith(tmp_project.relpath(final)))
    assert line.split()[0] == hash_file(final)[len("sha256:"):]
    assert man2["checksums"]["path"] == "SHA256SUMS"


def test_bundle_is_reproducible(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _write_srt(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    o1, _ = D.write_bundle(tmp_project, man, output=tmp_project.root / "a.zip")
    o2, _ = D.write_bundle(tmp_project, man, output=tmp_project.root / "b.zip")
    assert o1.read_bytes() == o2.read_bytes()             # byte-identical zip


def test_atomic_bundle_failure_keeps_old_output(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    out, _ = D.write_bundle(tmp_project, man, output=tmp_project.root / "keep.zip")
    original = out.read_bytes()
    # a later write blows up mid-zip; the existing bundle must survive intact
    import manju.build.delivery as _d

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(_d.zipfile, "ZipFile", boom)
    with pytest.raises(Exception):
        D.write_bundle(tmp_project, man, output=tmp_project.root / "keep.zip")
    assert out.read_bytes() == original                   # untouched


def test_delivery_bundle_is_not_project_pack(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    out, _ = D.write_bundle(tmp_project, man)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    # unlike `manju pack`, a delivery bundle never carries source/config/runtime
    assert not any(n.startswith(("bible/", "shots/", ".manju/")) for n in names)


def test_cli_and_core_service_agree(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["exports", "--manifest", "--json"])
    assert res.exit_code == 0, res.output
    got = json.loads(res.output)
    assert got["manifest_digest"] == D.build_manifest(tmp_project, "master")["manifest_digest"]


def test_editor_approved_only_after_human_nle_verification(tmp_project, add_shot):
    """technical_ready ≠ editor_approved: a verified JianYing draft is required.
    FINAL_ACCEPTANCE F2: the final must be RUN-PROVEN for technical_ready (and
    hence editor_approved) to be reachable — a bare sidecar no longer passes."""
    from manju.build import attempts as A

    add_shot(tmp_project, "S001")
    tname, src = _register(tmp_project, "S001")
    tl = _manual_timeline(tmp_project, [VideoClip(shot="S001", take=tname, source=src,
                                                  start_ms=0, duration_ms=2000)])
    A.append_run_started(tmp_project, "run_jy", target="final", gen="missing")
    A.append_run_terminal(tmp_project, "run_jy", status="completed")
    _fab_final(tmp_project, _final_key(tmp_project, tl), run_id="run_jy")
    # a JianYing skeleton draft on disk, then a human marks it verified
    d = tmp_project.exports_dir / "jianying" / tmp_project.load_config().name
    d.mkdir(parents=True, exist_ok=True)
    (d / "draft_content.json").write_text(json.dumps({"materials": {}, "tracks": []}), encoding="utf-8")
    before = D.build_manifest(tmp_project, "master")
    assert before["release"]["delivery_state"]["editor_approved"] is False
    mark_verified(tmp_project, "jianying", actor="me", note="opens fine")
    after = D.build_manifest(tmp_project, "master")
    jy = next(a for a in after["artifacts"] if a["role"] == "NLE_JIANYING")
    assert jy["state"] == "HUMAN_VERIFIED"
    assert after["release"]["delivery_state"]["editor_approved"] is True


def test_only_one_new_public_schema_no_parallel_objects():
    """No NLEHandoff / VariantPlan / SegmentDecisionList / LocalizationPackage /
    PublishReceipt parallel schema — the batch ships exactly one."""
    import pathlib
    import re

    src = pathlib.Path(D.__file__).read_text(encoding="utf-8")
    # exactly ONE public schema id is DEFINED in this module (docstring prose that
    # names the rejected parallel schemas is compliance text, not a definition)
    schema_ids = set(re.findall(r'"(manju\.[a-z0-9-]+/v\d+)"', src))
    assert schema_ids == {"manju.delivery-manifest/v1"}
    assert D.SCHEMA == "manju.delivery-manifest/v1"
    # no parallel public OBJECT is defined
    for forbidden in ("class NLEHandoff", "class VariantPlan", "class SegmentDecisionList",
                      "class LocalizationPackage", "class PublishReceipt",
                      "class PublishHandoff", "class FramingPlan"):
        assert forbidden not in src
