"""FP Loop E — manju.delivery-conformance/v1 declarative delivery conformance.

Red-first (roadmap §7.1/§7.2, §15.7 delivery-fixture subset). Everything is
ffmpeg-free, exactly like tests/test_c13_delivery.py and tests/test_fp_profile.py:

* delivery artifacts (final/srt/…) are fabricated with hand-written ``.key.json``
  sidecars over a manual-mode ``timeline.json`` so the exportstatus content-key
  recompute is deterministic;
* each artifact's technical facts come from a hand-written ffprobe dict passed
  through :func:`manju.media.technical_profile.normalize_probe_document` and
  stored content-addressed — no media, no probe;
* loudness/true-peak facts come from a hand-written ``exports/masters/masters.json``
  index (``load_index`` just reads that JSON).

The conformance document is a PURE, honest REPORT: per §7.2 checklist rows each
carry PASS / FAIL / UNKNOWN / NOT_APPLICABLE (never a guess, never an aggregate
score), it is deterministic, it is a deletable derived projection, and no
release/readiness path imports it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from manju.build import conformance as C
from manju.build import delivery as D
from manju.core.hashing import hash_file
from manju.core.models import (
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import read_yaml, write_yaml
from manju.media.technical_profile import normalize_probe_document as N
from manju.media.technical_profile import write_profile

STATUSES = {"PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"}


# ------------------------------------------------------------- fabricators (c13 patterns)


def _set_profiles(project, profiles: dict) -> None:
    data = read_yaml(project.root / "project.yaml") or {}
    data["delivery_profiles"] = profiles
    write_yaml(project.root / "project.yaml", data)


def _manual_timeline(project, video=None) -> Timeline:
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


def _final_key(project, tl: Timeline) -> str:
    from manju.media.render import final_content_key

    ass = project.captions_dir / "captions.ass"
    return final_content_key(project, tl, ass_file=ass if ass.exists() else None, target="final")


def _fab_final(project, key: str, *, data: bytes = b"final-bytes") -> Path:
    project.final_dir.mkdir(parents=True, exist_ok=True)
    p = project.final_dir / "final_v1.mp4"
    p.write_bytes(data)
    sc = {"final_key": key, "target": "final", "created_at": "2026-07-06T10:00:00+00:00",
          "output_sha256": hash_file(p)}
    (project.final_dir / "final_v1.key.json").write_text(json.dumps(sc), encoding="utf-8")
    return p


def _clean_final(project):
    tl = _manual_timeline(project)
    return _fab_final(project, _final_key(project, tl)), tl


def _write_srt(project) -> None:
    project.captions_dir.mkdir(parents=True, exist_ok=True)
    (project.captions_dir / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n这不可能。\n", encoding="utf-8")


# ------------------------------------------------------------- ffprobe dict → stored profile


def _vid(**over) -> dict:
    base = {
        "index": 0, "codec_type": "video", "codec_name": "h264",
        "profile": "High", "level": 40,
        "width": 1080, "height": 1920, "coded_width": 1080, "coded_height": 1920,
        "r_frame_rate": "24/1", "avg_frame_rate": "24/1",
        "pix_fmt": "yuv420p", "sample_aspect_ratio": "1:1",
        "display_aspect_ratio": "9:16", "field_order": "progressive",
        "color_primaries": "bt709", "color_transfer": "bt709",
        "color_space": "bt709", "color_range": "tv",
        "nb_frames": "48", "duration": "2.0",
    }
    base.update(over)
    return base


def _aud(**over) -> dict:
    base = {
        "index": 1, "codec_type": "audio", "codec_name": "aac",
        "sample_rate": "48000", "sample_fmt": "fltp", "channels": 2,
        "channel_layout": "stereo",
    }
    base.update(over)
    return base


def _probe(*streams, **fmt) -> dict:
    f = {"format_name": "mov,mp4,m4a,3gp", "bit_rate": "2000000", "nb_streams": len(streams)}
    f.update(fmt)
    return {"streams": list(streams), "format": f}


def _store_profile_for(project, sha256: str, probe: dict) -> None:
    """Store a technical profile content-addressed to an artifact's exact bytes."""
    doc = N(probe, source_media_hash=sha256, source_ref="media/final/final_v1.mp4")
    write_profile(project, doc)


def _master_sha(manifest) -> str:
    for a in manifest["artifacts"]:
        if a["role"] == "MASTER_VIDEO":
            return a["sha256"]
    raise AssertionError("no MASTER_VIDEO artifact in manifest")


def _write_masters(project, *, integrated_lufs=-14.0, true_peak_dbtp=-1.5) -> None:
    d = project.exports_dir / "masters"
    d.mkdir(parents=True, exist_ok=True)
    (d / "masters.json").write_text(json.dumps({
        "schema": "manju.audio-masters/v1",
        "artifacts": [{
            "role": "RAW_STEM_SUM",
            "loudness": {"integrated_lufs": integrated_lufs,
                         "true_peak_dbtp": true_peak_dbtp},
        }],
    }), encoding="utf-8")


def _rows(doc, check: str) -> list[dict]:
    return [r for r in doc["checks"] if r["check"] == check]


def _statuses(doc, check: str) -> set[str]:
    return {r["status"] for r in _rows(doc, check)}


# a full technical profile whose targets MATCH the fabricated master facts.
_MATCHING_TECHNICAL = {
    "container": "mp4",
    "video": {
        "codec": "h264",
        "width": 1080, "height": 1920,
        "frame_rate": {"num": 24, "den": 1},
        "pix_fmt": "yuv420p",
        "pixel_aspect_ratio": {"num": 1, "den": 1},
        "color": {"primaries": "bt709", "transfer": "bt709",
                  "matrix": "bt709", "range": "tv"},
    },
    "audio": {
        "codec": "aac", "sample_rate": 48000, "channel_layout": "stereo",
        "loudness": {"integrated_lufs_min": -15.0, "integrated_lufs_max": -13.0,
                     "true_peak_max_dbtp": -1.0},
    },
    "captions": {"required": ["srt"]},
    "checksums": {"required": True},
}


# ============================================================ §15.7 valid package


def test_valid_package_all_pass_or_not_applicable(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _write_srt(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(), _aud()))
    _write_masters(tmp_project)

    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})

    assert doc["schema"] == C.SCHEMA
    statuses = {r["status"] for r in doc["checks"]}
    assert statuses <= STATUSES
    assert "FAIL" not in statuses, [r for r in doc["checks"] if r["status"] == "FAIL"]
    assert "UNKNOWN" not in statuses, [r for r in doc["checks"] if r["status"] == "UNKNOWN"]
    # the core technical rows are genuinely PASS (not merely NOT_APPLICABLE)
    for chk in ("container", "video_codec", "frame_rate", "color",
                "audio_codec", "loudness", "true_peak", "captions", "checksum"):
        assert _statuses(doc, chk) == {"PASS"}, (chk, _rows(doc, chk))


# ============================================================ §15.7 wrong codec


def test_wrong_codec_is_fail_row(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    # the real bytes decode as hevc, not the required h264
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(codec_name="hevc"), _aud()))
    _write_masters(tmp_project)

    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    codec = _rows(doc, "video_codec")
    assert len(codec) == 1 and codec[0]["status"] == "FAIL"
    assert codec[0]["expected"] == "h264" and codec[0]["observed"] == "hevc"


# ============================================================ §15.7 missing caption file


def test_missing_caption_file_is_fail(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    # NB: no _write_srt → the required srt caption is absent
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(), _aud()))

    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    caps = _rows(doc, "captions")
    assert any(r["status"] == "FAIL" for r in caps), caps
    assert any("srt" in str(r["expected"]).lower() for r in caps)


# ============================================================ §15.7 checksum mismatch


def test_checksum_mismatch_after_tamper_is_fail(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    final, _ = _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(), _aud()))
    # tamper ONE byte on disk AFTER the manifest recorded the sha256
    final.write_bytes(b"final-bytez")  # same length, different byte

    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    chk = [r for r in _rows(doc, "checksum") if r["artifact"] == "master:main"]
    assert len(chk) == 1 and chk[0]["status"] == "FAIL", _rows(doc, "checksum")


# ============================================================ §15.7 absent profile → UNKNOWN never PASS


def test_absent_technical_profile_is_unknown_never_pass(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    # deliberately DO NOT store a technical profile for the master bytes
    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    for chk in ("container", "video_codec", "resolution", "frame_rate", "color",
                "audio_codec", "audio_sample_rate"):
        st = _statuses(doc, chk)
        assert st == {"UNKNOWN"}, (chk, _rows(doc, chk))
        assert "PASS" not in st


# ============================================================ §15.7 color-unknown media vs color-required


def test_color_unknown_media_is_unknown_with_detail(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    # a master with NO colour tags — color_known is False; the profile requires colour
    untagged = _vid()
    for k in ("color_primaries", "color_transfer", "color_space", "color_range"):
        untagged.pop(k, None)
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(untagged, _aud()))

    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    color = _rows(doc, "color")
    assert len(color) == 1 and color[0]["status"] == "UNKNOWN"
    assert color[0]["detail"] and "color" in color[0]["detail"].lower()


# ============================================================ §15.7 loudness over true-peak ceiling


def test_true_peak_over_ceiling_is_fail(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(), _aud()))
    # measured true peak -0.3 dBTP exceeds the -1.0 ceiling
    _write_masters(tmp_project, true_peak_dbtp=-0.3)

    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    tp = _rows(doc, "true_peak")
    assert len(tp) == 1 and tp[0]["status"] == "FAIL"
    assert "-1.0" in str(tp[0]["expected"]) or "-1" in str(tp[0]["expected"])


def test_loudness_and_true_peak_unknown_without_masters(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(), _aud()))
    # no masters index at all → loudness/true-peak are honestly UNKNOWN, never PASS/FAIL
    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    assert _statuses(doc, "loudness") == {"UNKNOWN"}
    assert _statuses(doc, "true_peak") == {"UNKNOWN"}


# ============================================================ frame-rate PASS/FAIL (timebase)


def test_frame_rate_match_and_mismatch(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    sha = _master_sha(manifest)

    # match: 24/1 vs {24,1}
    _store_profile_for(tmp_project, sha, _probe(_vid(r_frame_rate="24/1", avg_frame_rate="24/1"), _aud()))
    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    assert _statuses(doc, "frame_rate") == {"PASS"}

    # mismatch: 30000/1001 vs required 24/1
    _store_profile_for(tmp_project, sha,
                       _probe(_vid(r_frame_rate="30000/1001", avg_frame_rate="30000/1001"), _aud()))
    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    assert _statuses(doc, "frame_rate") == {"FAIL"}


# ============================================================ NOT_APPLICABLE when target absent


def test_absent_technical_block_is_not_applicable_but_integrity_still_runs(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"bare": {}})  # NO technical block at all
    manifest = D.build_manifest(tmp_project, "bare")
    doc = C.check_delivery_conformance(tmp_project, manifest, {})

    # every declarative technical row is NOT_APPLICABLE (nothing declared to check)
    for chk in ("container", "video_codec", "frame_rate", "color", "audio_codec",
                "loudness", "true_peak", "captions"):
        assert _statuses(doc, chk) <= {"NOT_APPLICABLE"}, (chk, _rows(doc, chk))
    # but manifest-integrity rows always run
    assert _statuses(doc, "package_structure") == {"PASS"}
    assert _statuses(doc, "unresolved_issues") == {"PASS"}


# ============================================================ unresolved issues → FAIL


def test_blocking_diagnostic_makes_unresolved_issues_fail(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    two = [VideoClip(shot="S001", take="t", source="media/gen/S001/take_01.mp4",
                     start_ms=0, duration_ms=1000),
           VideoClip(shot="S002", take="t", source="media/gen/S002/take_01.mp4",
                     start_ms=1000, duration_ms=1000)]
    _manual_timeline(tmp_project, two)
    _set_profiles(tmp_project, {"yt": {"variant_kind": "format_only", "base_profile": "master"}})
    master = D.build_manifest(tmp_project, "master")
    _manual_timeline(tmp_project, two[:1])  # drop a segment → VARIANT_KIND_MISMATCH (blocking)
    manifest = D.build_manifest(tmp_project, "yt", base_master=master)
    assert any(d.get("severity") == "blocking" for d in manifest["diagnostics"])

    doc = C.check_delivery_conformance(tmp_project, manifest, {})
    ur = _rows(doc, "unresolved_issues")
    assert len(ur) == 1 and ur[0]["status"] == "FAIL"
    assert "VARIANT_KIND_MISMATCH" in str(ur[0]["detail"])


# ============================================================ no single score


def test_no_single_aggregate_score(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(), _aud()))
    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})

    blob = json.dumps(doc).lower()
    for banned in ("score", "total", "overall", "grade", "pass_rate", "percent", "pass_pct"):
        assert banned not in doc, f"conformance doc must carry no aggregate {banned!r}"
    # summary is COUNTS BY STATUS only — nothing else
    assert set(doc["summary"]) == STATUSES
    assert all(isinstance(v, int) for v in doc["summary"].values())
    assert "score" not in blob and "总分" not in json.dumps(doc, ensure_ascii=False)


# ============================================================ determinism


def test_deterministic_same_inputs_byte_identical(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _write_srt(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(), _aud()))
    _write_masters(tmp_project)

    a = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    b = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})
    assert json.dumps(a) == json.dumps(b)
    assert a["conformance_digest"] == b["conformance_digest"]


# ============================================================ write / read / delete inert + tamper


def test_write_read_delete_and_tamper(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    final, _ = _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    _store_profile_for(tmp_project, _master_sha(manifest), _probe(_vid(), _aud()))
    doc = C.check_delivery_conformance(tmp_project, manifest, {"technical": _MATCHING_TECHNICAL})

    path = C.write_conformance_report(tmp_project, doc)
    assert path.exists()
    got = C.read_conformance_report(path)
    assert got is not None and got["conformance_digest"] == doc["conformance_digest"]

    # tamper a check verdict → digest no longer matches → rejected on read
    tampered = json.loads(path.read_text(encoding="utf-8"))
    for r in tampered["checks"]:
        if r["status"] == "FAIL":
            r["status"] = "PASS"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    assert C.read_conformance_report(path) is None

    # deleting the derived report is inert — the delivered bytes are untouched
    before = final.read_bytes()
    path.unlink()
    assert final.read_bytes() == before
    assert C.read_conformance_report(path) is None


# ============================================================ built-in presets


def test_builtin_presets_are_copyable_technical_dicts():
    presets = C.builtin_technical_presets()
    assert set(presets) >= {"web-1080p", "social-vertical", "archive-mezzanine"}
    for name, prof in presets.items():
        assert isinstance(prof, dict) and isinstance(prof.get("technical"), dict)
        tech = prof["technical"]
        assert "video" in tech
    # the helper hands out COPIES — mutating one must not poison the next call
    presets["web-1080p"]["technical"]["container"] = "MUTATED"
    assert C.builtin_technical_presets()["web-1080p"]["technical"]["container"] != "MUTATED"


# ============================================================ release-path isolation grep-pin


def test_conformance_is_never_a_build_or_release_input():
    """No build/core/providers/runtime module (other than the conformance owner
    itself) may import conformance or read reports/conformance — a delivery
    conformance verdict never gates a build or a release (roadmap §7.2 boundary)."""
    root = Path(C.__file__).resolve().parents[1]  # src/manju
    owner = Path(C.__file__).resolve()
    hits: list[str] = []
    for sub in ("build", "core", "providers", "runtime"):
        for py in (root / sub).rglob("*.py"):
            if py.resolve() == owner or "__pycache__" in py.parts:
                continue
            text = py.read_text(encoding="utf-8")
            if "reports/conformance" in text:
                hits.append(f"{py}: reads reports/conformance")
            if "import conformance" in text or "from .conformance" in text \
                    or "build.conformance" in text:
                hits.append(f"{py}: imports conformance")
    assert hits == [], f"a build/release path depends on conformance: {hits}"


def test_release_assessment_verdict_is_independent_of_conformance(tmp_project, add_shot):
    """The delivery manifest's own release_state is computed WITHOUT conformance —
    proving conformance is not silently wired into readiness this loop."""
    add_shot(tmp_project, "S001")
    _clean_final(tmp_project)
    _set_profiles(tmp_project, {"web": {"technical": _MATCHING_TECHNICAL}})
    manifest = D.build_manifest(tmp_project, "web")
    # a FAIL-heavy conformance run must not exist as a field on the manifest
    assert "conformance" not in manifest
    assert "delivery-conformance" not in json.dumps(manifest)
