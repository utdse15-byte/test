"""POST_COMPLETION_HARDENING_V1 — H2 track (WP4 accepted-take views + WP5 delivery).

Red-first over confirmed claims 7-16. Each test asserts the HARDENED behavior,
so the pre-fix run is the red proof:

  WP4 (qc/production + qc/prompt_checks)
    7  keeper survived a spec/expectation change (same media sha)      → 6.1
    8  continuation derivation exception was silently swallowed        → 6.2
    +  redo_of cycle split families / unstable representative          → 6.3
  WP5 (build/delivery)
    9  artifacts[] + nle.project_file same path → bundle duplicate err → 7.1
    10 bytes changed after manifest → bundle embeds stale hashes       → 7.2
    11 materialized master manifest was READ as variant base identity  → 7.3
    12 audio/subtitle/overlay changes did not move the semantic digest → 7.4
    13 absolute metadata path leaked into the manifest                 → 7.5
    14 metadata bytes were excluded from manifest_digest               → 7.5
    15 explicitly unknown profile/variant silently became MASTER       → 7.6
    16 final_ref accepted but ignored                                  → 7.7 (B)

Nothing here persists new truth: every fix stays inside the derived views.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from manju.build import delivery as D
from manju.core.container import Project
from manju.core.hashing import hash_file
from manju.core.models import (
    AudioClip,
    CaptionLine,
    OverlayClip,
    TakeSidecar,
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import read_yaml, write_yaml

# ------------------------------------------------------------- fabricators
# (self-contained on purpose — same shapes as tests/test_c13_delivery.py)


def _set_profiles(project: Project, profiles: dict) -> None:
    data = read_yaml(project.root / "project.yaml") or {}
    data["delivery_profiles"] = profiles
    write_yaml(project.root / "project.yaml", data)


def _manual_timeline(project: Project, video=None, *, music=None, captions=None,
                     overlay=None) -> Timeline:
    write_yaml(project.rules_path, TimelineRules(mode="manual").model_dump())
    video = video or [VideoClip(shot="S001", take="take_01",
                                source="media/gen/S001/take_01.mp4",
                                start_ms=0, duration_ms=2000)]
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920,
        duration_ms=sum(c.duration_ms for c in video),
        tracks=TimelineTracks(video=video, music=music or [],
                              captions=captions or [], overlay=overlay or []),
    )
    project.save_timeline(tl)
    return tl


def _final_key(project: Project, tl: Timeline) -> str:
    from manju.media.render import final_content_key

    ass = project.captions_dir / "captions.ass"
    return final_content_key(project, tl, ass_file=ass if ass.exists() else None, target="final")


def _fab_final(project: Project, key: str, *, version=1, data=b"final-bytes"):
    project.final_dir.mkdir(parents=True, exist_ok=True)
    p = project.final_dir / f"final_v{version}.mp4"
    p.write_bytes(data)
    sc = {"final_key": key, "target": "final", "output_sha256": hash_file(p),
          "created_at": "2026-07-06T10:00:00+00:00"}
    (project.final_dir / f"final_v{version}.key.json").write_text(json.dumps(sc),
                                                                  encoding="utf-8")
    return p


def _clean_final(project: Project):
    tl = _manual_timeline(project)
    return _fab_final(project, _final_key(project, tl)), tl


def _write_otio(project: Project) -> "object":
    name = project.load_config().name
    d = project.exports_dir / "otio"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.otio"
    p.write_text('{"OTIODOC": 1}', encoding="utf-8")
    return p


def _register(project: Project, shot: str, data: bytes, *, spec_hash="sha256:rev1",
              redo_of=None):
    tmp = project.root / f"_src_{shot}_{len(data)}.mp4"
    tmp.write_bytes(data)
    kwargs = {"provider": "test", "spec_hash": spec_hash}
    if redo_of:
        kwargs["redo_of"] = redo_of
    info = project.register_take(shot, tmp, TakeSidecar(**kwargs))
    tmp.unlink()
    return info


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name))


def _keep_current(project, shot_id):
    """Record a v2 KEEP verdict bound to the CURRENT selected media/spec/
    expectations (the exact bindings qc_brief hands out)."""
    from manju.qc.agent_review import qc_brief, record_verdicts

    row = next(r for r in qc_brief(project, [shot_id])["shots"] if r["shot"] == shot_id)
    record_verdicts(project, {
        "schema": "manju.qc.verdict/v2", "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": shot_id},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [{"expectation_id": e["id"], "observed": "present"}
                         for e in row["expectations"]],
        "findings": [], "reviewer": {"kind": "model_visual", "name": "t"},
        "decision": {"disposition": "KEEP"},
    })
    return row


def _member(view, take_name):
    return next(m for f in view["families"] for m in f["takes"] if m["take"] == take_name)


# =====================================================================
# WP4 — claim 7 (6.1 keeper), claim 8 (6.2 continuation), 6.3 cycles
# =====================================================================


def test_18_stale_keep_after_spec_change_is_not_keeper(tmp_project, add_shot):
    """Claim 7 RED: keeper stayed true after the shot SPEC moved under the
    same media bytes. Hardened: keeper only from a fully current-bound KEEP;
    history stays visible as historical_disposition + binding_status."""
    from manju.qc.production import candidate_families

    add_shot(tmp_project, "S001", quality={"must_show": ["红伞出现"]})
    take = _register(tmp_project, "S001", b"same-bytes")
    _select(tmp_project, "S001", take.name)
    _keep_current(tmp_project, "S001")

    fresh = _member(candidate_families(tmp_project, "S001"), take.name)
    assert fresh["keeper"] is True                     # current-bound KEEP is keeper
    assert fresh["binding_status"] == "current"

    # the creative source moves (spec change) — same media sha on disk
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("action", {}).__setitem__("main", "完全不同的动作"))

    stale = _member(candidate_families(tmp_project, "S001"), take.name)
    assert stale["media_sha256"] == fresh["media_sha256"]   # bytes did NOT move
    assert stale["keeper"] is False                    # RED pre-fix: was True
    assert stale["binding_status"] == "stale"
    assert stale["historical_disposition"] == "KEEP"   # history never deleted


def test_19_changed_expectation_is_not_keeper(tmp_project, add_shot):
    """Claim 7 RED (expectation axis): must_show edits invalidate keeper."""
    from manju.qc.production import candidate_families

    add_shot(tmp_project, "S001", quality={"must_show": ["红伞出现"]})
    take = _register(tmp_project, "S001", b"same-bytes-2")
    _select(tmp_project, "S001", take.name)
    _keep_current(tmp_project, "S001")

    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("quality", {}).__setitem__(
            "must_show", ["红伞出现", "并且下雨"]))

    m = _member(candidate_families(tmp_project, "S001"), take.name)
    assert m["keeper"] is False                        # RED pre-fix: was True
    assert m["binding_status"] == "stale"
    assert m["historical_disposition"] == "KEEP"


def test_20_continuation_derivation_exception_emits_blocking_diagnostic(
        tmp_project, add_shot, monkeypatch):
    """Claim 8 RED: an exception inside the continuation derivation was
    swallowed (prompt_checks `except: pass`) → NO finding at all. Hardened: a
    shot WITH continuity.prev gets CONTINUATION_CHECK_UNAVAILABLE with
    blocking semantics; a shot WITHOUT continuation stays finding-free."""
    from manju.qc import production as P
    from manju.qc.prompt_checks import has_blocking, production_checks

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002", continuity={"prev": "S001"})
    add_shot(tmp_project, "S003")                       # no continuation

    def boom(project, shot_id):
        raise RuntimeError("v2 records unreadable")

    monkeypatch.setattr(P, "accepted_observed_state", boom)

    findings = production_checks(tmp_project, tmp_project.load_shot("S002"))
    codes = {f["code"] for f in findings}
    assert "CONTINUATION_CHECK_UNAVAILABLE" in codes    # RED pre-fix: absent
    unavailable = [f for f in findings if f["code"] == "CONTINUATION_CHECK_UNAVAILABLE"]
    assert has_blocking(unavailable)                    # enters has_blocking semantics

    free = production_checks(tmp_project, tmp_project.load_shot("S003"))
    assert "CONTINUATION_CHECK_UNAVAILABLE" not in {f["code"] for f in free}


def test_21_redo_cycle_is_one_family_with_stable_representative(tmp_project, add_shot):
    """WP4 6.3 RED: a redo_of cycle across a source edit split the cycle into
    DIFFERENT families (each member rooted at the other). Hardened: the whole
    cycle lands in ONE family under a stable canonical representative (min
    take name), a REDO_LINEAGE_CYCLE diagnostic is emitted, sidecars untouched."""
    from manju.qc.production import candidate_families

    add_shot(tmp_project, "S001")
    a = _register(tmp_project, "S001", b"cycle-A", spec_hash="sha256:rev1")
    b = _register(tmp_project, "S001", b"cycle-B", spec_hash="sha256:rev2", redo_of=a.name)
    # close the cycle by hand-editing A's sidecar (a corrupted/looped lineage)
    a_sidecar = a.media_path.with_suffix(".yaml")
    sc = read_yaml(a_sidecar) or {}
    sc["redo_of"] = b.name
    write_yaml(a_sidecar, sc)
    before = {p: p.read_bytes() for p in (a_sidecar, b.media_path.with_suffix(".yaml"))}

    view = candidate_families(tmp_project, "S001")
    by_take = {m["take"]: f for f in view["families"] for m in f["takes"]}
    assert by_take[a.name]["family_id"] == by_take[b.name]["family_id"]  # RED: differed
    # canonical representative = min take name → family keyed by A's revision
    assert by_take[a.name]["source_spec_hash"] == "sha256:rev1"
    diags = view.get("diagnostics") or []
    assert any(d["code"] == "REDO_LINEAGE_CYCLE" for d in diags)          # RED: absent
    cyc = next(d for d in diags if d["code"] == "REDO_LINEAGE_CYCLE")
    assert set(cyc["members"]) == {a.name, b.name}
    # derived view only — no sidecar was rewritten
    assert {p: p.read_bytes() for p in before} == before


# =====================================================================
# WP5 — claims 9/10 (bundle), 11 (base identity), 12 (digest axes)
# =====================================================================


def test_22_nle_project_file_packed_once_not_duplicate_error(tmp_project, add_shot):
    """Claim 9 RED: a manifest with a REAL NLE artifact made the NORMAL bundle
    path raise `duplicate bundle entry` (artifacts[] row + nle.project_file
    name the same path). Hardened: same path + same hash ⇒ packed ONCE."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    otio = _write_otio(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    assert man["nle"] is not None and man["nle"]["project_file"]["path"]

    out, man2 = D.write_bundle(tmp_project, man)        # RED pre-fix: raised
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    rel = tmp_project.relpath(otio)
    assert names.count(rel) == 1                        # exactly once
    sums = next(n for n in names if n == "SHA256SUMS")
    with zipfile.ZipFile(out) as zf:
        sums_text = zf.read(sums).decode()
    assert sums_text.count(rel) == 1


def test_23_nle_hash_conflict_is_binding_mismatch_not_duplicate(tmp_project, add_shot):
    """Claim 9 (conflict arm): nle.project_file hash disagreeing with the
    registered artifact for the same path is NLE_ARTIFACT_BINDING_MISMATCH."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _write_otio(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    man = json.loads(json.dumps(man))                   # deep copy
    man["nle"]["project_file"]["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(D.DeliveryManifestError) as exc:
        D.write_bundle(tmp_project, man)
    assert "NLE_ARTIFACT_BINDING_MISMATCH" in str(exc.value)
    assert "duplicate" not in str(exc.value)


def test_24_artifact_changed_after_manifest_blocks_bundle(tmp_project, add_shot):
    """Claim 10 RED: bytes changed after build_manifest → the bundle happily
    embedded a manifest whose hashes no longer matched the packed bytes.
    Hardened: CAS revalidation refuses with DELIVERY_ARTIFACT_CHANGED_AFTER_
    MANIFEST and writes nothing."""
    add_shot(tmp_project, "S001")
    final, _tl = _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    final.write_bytes(b"tampered-after-manifest")       # drift
    dest = tmp_project.root / "out.zip"
    with pytest.raises(D.DeliveryManifestError) as exc:
        D.write_bundle(tmp_project, man, output=dest)   # RED pre-fix: succeeded
    assert "DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST" in str(exc.value)
    assert not dest.exists()                            # no new ZIP


def test_25_old_zip_remains_after_cas_refusal(tmp_project, add_shot):
    """Claim 10 (old output survives): a prior good bundle is never deleted."""
    add_shot(tmp_project, "S001")
    final, _tl = _clean_final(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    dest = tmp_project.root / "keep.zip"
    out, _ = D.write_bundle(tmp_project, man, output=dest)
    original = out.read_bytes()
    final.write_bytes(b"tampered-later")
    with pytest.raises(D.DeliveryManifestError):
        D.write_bundle(tmp_project, man, output=dest)
    assert out.read_bytes() == original                 # old ZIP intact


def test_26_materialized_manifest_edit_is_inert_for_variant(tmp_project, add_shot):
    """Claim 11 RED (CONFIRMED): _resolve_base_master_digest read the
    materialized reports/delivery manifest as the variant's base identity —
    hand-editing that file SHIFTED the variant's base digest. Hardened: the
    disk fallback is deleted; base identity is re-derived in-process."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {
        "master": {"variant_kind": "master"},
        "fmt": {"variant_kind": "format_only", "base_profile": "master"},
    })
    master = D.build_manifest(tmp_project, "master")
    path = D.materialize_manifest(tmp_project, master)

    # hand-tamper the materialized report's digest
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["variant"]["source_timeline_digest"] = "sha256:tampered-by-hand"
    path.write_text(json.dumps(doc), encoding="utf-8")

    fmt = D.build_manifest(tmp_project, "fmt")
    assert fmt["variant"]["base_master_manifest_digest"] != "sha256:tampered-by-hand"
    assert (fmt["variant"]["base_master_manifest_digest"]
            == master["variant"]["source_timeline_digest"])   # in-process derivation
    assert "VARIANT_KIND_MISMATCH" not in {d["code"] for d in fmt["diagnostics"]}
    # deleting the report is equally inert
    path.unlink()
    fmt2 = D.build_manifest(tmp_project, "fmt")
    assert fmt2["variant"]["base_master_manifest_digest"] == \
        fmt["variant"]["base_master_manifest_digest"]


def test_base_profile_cycle_is_blocked_not_recursion(tmp_project, add_shot):
    """Claim 11 (guard): a profile whose base chain names itself or cycles
    produces a blocking BASE_PROFILE_CYCLE diagnostic, never a RecursionError."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {
        "selfy": {"variant_kind": "format_only", "base_profile": "selfy"},
        "a": {"variant_kind": "format_only", "base_profile": "b"},
        "b": {"variant_kind": "format_only", "base_profile": "a"},
    })
    for pid in ("selfy", "a"):
        man = D.build_manifest(tmp_project, pid)
        codes = {d["code"] for d in man["diagnostics"]}
        assert "BASE_PROFILE_CYCLE" in codes
        assert man["release"]["delivery_state"]["technical_ready"] is False


def test_27_audio_change_moves_semantic_digest(tmp_project):
    """Claim 12 RED (CONFIRMED): the digest was video-segments-only — adding/
    changing a music cue on the SAME picture cut left it unchanged."""
    video = [VideoClip(shot="S001", take="t", source="media/gen/S001/take_01.mp4",
                       start_ms=0, duration_ms=2000)]
    d_silent = D.timeline_semantic_digest(_manual_timeline(tmp_project, video))
    d_music = D.timeline_semantic_digest(_manual_timeline(
        tmp_project, video,
        music=[AudioClip(source="media/music/bgm.mp3", start_ms=0, duration_ms=2000)]))
    assert d_music != d_silent                          # RED pre-fix: equal
    # timing move on the same cue also moves it
    d_music2 = D.timeline_semantic_digest(_manual_timeline(
        tmp_project, video,
        music=[AudioClip(source="media/music/bgm.mp3", start_ms=500, duration_ms=1500)]))
    assert d_music2 != d_music


def test_28_subtitle_text_and_timing_change_moves_digest(tmp_project):
    """Claim 12 RED: caption source text/timing were invisible to the digest."""
    video = [VideoClip(shot="S001", take="t", source="media/gen/S001/take_01.mp4",
                       start_ms=0, duration_ms=2000)]
    base = D.timeline_semantic_digest(_manual_timeline(
        tmp_project, video,
        captions=[CaptionLine(start_ms=0, end_ms=1000, text="这不可能。", shot="S001")]))
    retext = D.timeline_semantic_digest(_manual_timeline(
        tmp_project, video,
        captions=[CaptionLine(start_ms=0, end_ms=1000, text="改过的台词。", shot="S001")]))
    retime = D.timeline_semantic_digest(_manual_timeline(
        tmp_project, video,
        captions=[CaptionLine(start_ms=200, end_ms=1200, text="这不可能。", shot="S001")]))
    assert retext != base                               # RED pre-fix: equal
    assert retime != base and retime != retext
    # overlay identity/timing is narrative too; pure geometry is not included
    with_overlay = D.timeline_semantic_digest(_manual_timeline(
        tmp_project, video,
        overlay=[OverlayClip(kind="title_card", text="第一章", start_ms=0, duration_ms=1500)]))
    assert with_overlay != base


# =====================================================================
# WP5 — claims 13/14 (metadata), 15 (strict), 16 (final_ref)
# =====================================================================


def test_29_absolute_metadata_path_is_not_emitted(tmp_project, add_shot):
    """Claim 13 RED: a metadata path that fails to resolve (absolute /
    out-of-project) was echoed verbatim into the manifest. Hardened: blocking
    PLATFORM_METADATA_PATH_INVALID, no path leak."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"yt": {"variant_kind": "platform_package",
                                       "platform": "youtube"}})
    leak = "/etc/definitely-outside-the-project/metadata.json"
    man = D.build_manifest(tmp_project, "yt", metadata_file=leak)
    blob = json.dumps(man, ensure_ascii=False)
    assert leak not in blob                             # RED pre-fix: leaked
    assert man["platform_handoff"]["metadata_file"] is None
    codes = {d["code"] for d in man["diagnostics"]}
    assert "PLATFORM_METADATA_PATH_INVALID" in codes
    assert man["release"]["delivery_state"]["technical_ready"] is False


def test_30_metadata_bytes_join_manifest_digest_as_artifact(tmp_project, add_shot):
    """Claim 14 RED: 13C deliberately excluded metadata bytes from the digest
    — this contract reverses that. Metadata becomes a first-class artifact
    row (sha256/bytes/mime) and its hash moves manifest_digest."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    meta = tmp_project.exports_dir / "meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text('{"title": "one"}', encoding="utf-8")
    _set_profiles(tmp_project, {"yt": {"variant_kind": "platform_package",
                                       "platform": "youtube",
                                       "metadata_file": "exports/meta.json"}})
    m1 = D.build_manifest(tmp_project, "yt")
    row = next(a for a in m1["artifacts"] if a["role"] == "PLATFORM_METADATA")
    assert row["sha256"] == hash_file(meta) and row["bytes"] == meta.stat().st_size
    assert row["mime"] == "application/json"

    meta.write_text('{"title": "two"}', encoding="utf-8")
    m2 = D.build_manifest(tmp_project, "yt")
    assert m2["manifest_digest"] != m1["manifest_digest"]   # RED pre-fix: equal
    # credential CONTENT still never enters output — boolean + diagnostics only
    meta.write_text('{"note": "sk-abcdefghijklmnopqrstuvwxyz012345"}', encoding="utf-8")
    m3 = D.build_manifest(tmp_project, "yt")
    assert m3["platform_handoff"]["credentials_present"] is True
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in json.dumps(m3, ensure_ascii=False)


def test_31_explicit_unknown_profile_is_blocked(tmp_project, add_shot):
    """Claim 15 RED: an explicitly unknown profile_id silently produced a
    MASTER manifest. Hardened: error. Compat pinned: absent delivery_profiles
    + the implicit 'master' id still derives MASTER."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    with pytest.raises(D.DeliveryManifestError):
        D.build_manifest(tmp_project, "does_not_exist")     # RED pre-fix: MASTER
    # old-project compat unchanged
    assert D.build_manifest(tmp_project, "master")["variant"]["kind"] == "MASTER"
    # and a DECLARED profile id still works
    _set_profiles(tmp_project, {"yt": {"variant_kind": "master"}})
    assert D.build_manifest(tmp_project, "yt")["variant"]["kind"] == "MASTER"


def test_32_explicit_unknown_variant_kind_is_blocked(tmp_project, add_shot):
    """Claim 15 RED (kind axis): an explicitly unknown variant_kind silently
    normalized to MASTER. Hardened: error; ABSENT kind still defaults MASTER."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"x": {"variant_kind": "directors_cut"}})
    with pytest.raises(D.DeliveryManifestError):
        D.build_manifest(tmp_project, "x")                  # RED pre-fix: MASTER
    _set_profiles(tmp_project, {"x": {}})                   # kind absent → compat
    assert D.build_manifest(tmp_project, "x")["variant"]["kind"] == "MASTER"


def test_33_format_only_and_localized_without_base_identity_block(tmp_path):
    """Claim 15/7.6: FORMAT_ONLY/LOCALIZED that cannot derive a base identity
    (empty project — no compilable timeline) must block, not warn."""
    project = Project.create(tmp_path / "empty", git_init=False)
    _set_profiles(project, {
        "fmt": {"variant_kind": "format_only", "base_profile": "master"},
        "loc": {"variant_kind": "localized", "locale": "en"},
    })
    for pid in ("fmt", "loc"):
        man = D.build_manifest(project, pid)
        codes = {d["code"]: d for d in man["diagnostics"]}
        assert "BASE_IDENTITY_MISSING" in codes             # RED pre-fix: warning only
        assert codes["BASE_IDENTITY_MISSING"]["severity"] == "blocking"
        assert man["release"]["delivery_state"]["technical_ready"] is False


def test_34_final_ref_parameter_is_rejected_not_ignored(tmp_project, add_shot):
    """Claim 16 RED: build_manifest accepted final_ref and silently ignored it
    (newest final used regardless). Hardened per 7.7 option B: the parameter
    no longer exists — the manifest targets the current/newest final only."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    with pytest.raises(TypeError):
        D.build_manifest(tmp_project, "master", final_ref="final_v1")  # RED: accepted
