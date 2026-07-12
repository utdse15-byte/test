"""FP Loop D — manju.conform-loss/v1: honest per-export loss reports + exact
one-frame drift detection for the NLE/exchange exporters.

Red-first (§20): this file is written BEFORE ``src/manju/exporters/conform.py``
exists — every test errors at import until the module lands.

What is pinned here:

* completeness — every feature the compiled timeline actually contains is
  classified into exactly one of preserved/approximated/dropped/unsupported
  (inventory − union == ∅ AND the categories are pairwise disjoint);
* honesty — a feature ABSENT from the timeline yields NO row (no vacuous
  "preserved"); srt_ass says loudly that a caption export is caption-only;
  jianying says its transitions ride manju stamps (approximated, round-trippable);
* exact drift math — residuals are Fractions from ``manju.core.timebase``,
  never floats: 41 ms at 24 fps ⇒ 123/125 frames exact, residual 2/125;
  23.976 material on a 24 grid ⇒ grid_drift_ms(2000) == 2 ms exactly and
  one_frame_drift_at == 125000/3 ms exactly;
* derived-output discipline — reports/conform/* is content-addressed,
  deletable, tamper-evident, byte-deterministic, and NEVER a build input
  (grep-pin);
* no silent degradation (§6.2) — every exporter module under
  src/manju/exporters/ is either classified or listed in UNSUPPORTED_TARGETS
  with a reason; exporter #6 fails this suite until classified.

Exporter artifact bytes are asserted UNCHANGED by report derivation — conform
only reads.
"""

from __future__ import annotations

import json
import shutil
from fractions import Fraction
from pathlib import Path

import pytest

from manju.core.container import ProjectError
from manju.core.models import (
    AudioClip,
    CaptionLine,
    OverlayClip,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)
from manju.core.spec import compute_spec_hash
from manju.core.timebase import Rate
from manju.exporters import conform
from manju.exporters.conform import (
    SCHEMA,
    TARGET_CLASSIFIERS,
    UNSUPPORTED_TARGETS,
    classify_features,
    conform_loss_report,
    read_conform_report,
    reimport_changes,
    timeline_feature_inventory,
    write_conform_report,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "manju"

ALL_CATEGORIES = ("preserved", "approximated", "dropped", "unsupported")


# --------------------------------------------------------------------------- #
# fixture: a small compiled timeline exercising every representable feature    #
# --------------------------------------------------------------------------- #


def _register(project, add_shot, make_take, shot_id: str):
    """One shot + one registered take whose media file exists in-project."""
    shot = add_shot(project, shot_id)
    take = make_take(project, shot_id, compute_spec_hash(shot, project.load_bible()))
    media = project.root / "media" / "gen" / shot_id / f"{take.name}.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    if not media.exists():
        media.write_bytes(b"fake-" + shot_id.encode())
    return take.name, f"media/gen/{shot_id}/{take.name}.mp4"


def _aux_media(project, rel: str) -> str:
    p = project.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"fake-audio")
    return rel


def _full_timeline(project, add_shot, make_take) -> tuple[Timeline, dict[str, str]]:
    """2 video clips, 1 transition, 1 overlay, 2 captions, volume automation
    (video gain + audio gain + fades + ducking), in-points, a looped ambient
    bed. Every boundary is a multiple of 125 ms — exactly on the 24 fps grid —
    so the honest drift expectation is ALL-ZERO."""
    t1, src1 = _register(project, add_shot, make_take, "S001")
    t2, src2 = _register(project, add_shot, make_take, "S002")
    voice = _aux_media(project, "media/voice/v1.wav")
    music = _aux_media(project, "media/music/bed.mp3")
    rain = _aux_media(project, "media/music/rain.wav")
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp-conform-1"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take=t1, source=src1, start_ms=0,
                          duration_ms=2000, source_in_ms=125,
                          source_gain_db=-3.0,
                          transition_out=TransitionSpec(type="fade", duration_ms=250)),
                VideoClip(shot="S002", take=t2, source=src2, start_ms=2000,
                          duration_ms=2000),
            ],
            overlay=[OverlayClip(kind="title_card", text="章节一",
                                 start_ms=0, duration_ms=1500)],
            voice=[AudioClip(source=voice, start_ms=0, duration_ms=2000)],
            music=[AudioClip(source=music, start_ms=0, duration_ms=4000,
                             gain_db=-6.0, ducking=True, fade_in_ms=250,
                             fade_out_ms=500, start_offset_ms=250)],
            ambient=[AudioClip(source=rain, start_ms=0, duration_ms=4000, loop=True)],
            captions=[
                CaptionLine(start_ms=0, end_ms=1000, text="你好", shot="S001"),
                CaptionLine(start_ms=1000, end_ms=2000, text="世界", shot="S001"),
            ],
        ),
    )
    return tl, {"S001": src1, "S002": src2}


def _rows_by_feature(doc: dict, category: str) -> dict[str, dict]:
    return {r["feature"]: r for r in doc[category]}


def _all_classified(doc: dict) -> dict[str, str]:
    """feature -> category across all four category lists."""
    out: dict[str, str] = {}
    for cat in ALL_CATEGORIES:
        for row in doc[cat]:
            out[row["feature"]] = cat
    return out


# --------------------------------------------------------------------------- #
# 1. inventory honesty — only present features, structured rows                #
# --------------------------------------------------------------------------- #


def test_inventory_lists_only_present_features(tmp_project, add_shot, make_take):
    t1, src1 = _register(tmp_project, add_shot, make_take, "S001")
    bare = Timeline(
        fps=24, duration_ms=1000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take=t1, source=src1, start_ms=0, duration_ms=1000),
        ]),
    )
    feats = {r["feature"] for r in timeline_feature_inventory(bare)}
    assert feats == {"video_clips"}, (
        "a bare video-only timeline must yield exactly one inventory row — "
        f"no vacuous features, got {sorted(feats)}"
    )


def test_inventory_rows_are_structured(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    rows = timeline_feature_inventory(tl)
    assert rows, "full fixture must produce inventory rows"
    for row in rows:
        assert set(row) == {"feature", "detail", "where"}
        assert row["feature"] and row["detail"] and row["where"]


def test_inventory_full_fixture_features(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    feats = {r["feature"] for r in timeline_feature_inventory(tl)}
    assert feats == {
        "video_clips", "video_in_points", "audio_in_points", "transitions",
        "overlays", "captions", "clip_volume", "audio_gain", "audio_fade_in",
        "audio_fade_out", "ducking", "audio_loops", "audio_tracks",
    }


# --------------------------------------------------------------------------- #
# 2. otio — completeness pin + audited classification                          #
# --------------------------------------------------------------------------- #


def test_otio_every_feature_classified_exactly_once(tmp_project, add_shot, make_take):
    from manju.exporters.otio import export_otio

    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    out = export_otio(tmp_project, tl)
    doc = conform_loss_report(tmp_project, "otio", tl, out)

    assert doc["schema"] == SCHEMA
    assert doc["target"] == "otio"
    inventory = {r["feature"] for r in timeline_feature_inventory(tl)}
    union: set[str] = set()
    for cat in ALL_CATEGORIES:
        cat_feats = {r["feature"] for r in doc[cat]}
        assert len(cat_feats) == len(doc[cat]), f"duplicate feature rows in {cat}"
        assert not (union & cat_feats), (
            f"feature classified in more than one category: {union & cat_feats}")
        union |= cat_feats
    assert inventory - union == set(), (
        f"unclassified timeline features: {sorted(inventory - union)}")
    assert union - inventory == set(), (
        f"fabricated rows for absent features: {sorted(union - inventory)}")


def test_otio_classification_is_the_audited_truth(tmp_project, add_shot, make_take):
    from manju.exporters.otio import export_otio

    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    out = export_otio(tmp_project, tl)
    doc = conform_loss_report(tmp_project, "otio", tl, out)
    got = _all_classified(doc)

    # audited against exporters/otio.py — clips/ranges land in the doc:
    assert got["video_clips"] == "preserved"
    assert got["video_in_points"] == "preserved"
    assert got["audio_in_points"] == "preserved"
    # metadata-only ⇒ approximated (addendum: state where):
    assert got["transitions"] == "approximated"
    assert got["clip_volume"] == "approximated"
    assert got["audio_loops"] == "approximated"
    assert got["audio_fade_in"] == "approximated"
    assert got["audio_tracks"] == "approximated"  # 4 buses flatten onto 1 track
    # absent from the OTIO doc entirely ⇒ dropped:
    assert got["captions"] == "dropped"
    assert got["overlays"] == "dropped"
    assert got["audio_gain"] == "dropped"
    assert got["audio_fade_out"] == "dropped"
    assert got["ducking"] == "dropped"

    approx = _rows_by_feature(doc, "approximated")
    assert "metadata" in approx["transitions"]["detail"].lower()
    assert "otio.py" in approx["transitions"]["where"]
    dropped = _rows_by_feature(doc, "dropped")
    assert "otio.py" in dropped["captions"]["where"]


def test_otio_export_bytes_unchanged_by_report(tmp_project, add_shot, make_take):
    from manju.exporters.otio import export_otio

    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    out = export_otio(tmp_project, tl)
    before = out.read_bytes()
    doc = conform_loss_report(tmp_project, "otio", tl, out)
    write_conform_report(tmp_project, doc)
    assert out.read_bytes() == before, "conform must only READ exporter output"


# --------------------------------------------------------------------------- #
# 3. frame drift — exact, zero fabrication                                     #
# --------------------------------------------------------------------------- #


def test_frame_drift_all_zero_on_snapped_timeline(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    doc = conform_loss_report(tmp_project, "otio", tl, {})
    fd = doc["frame_drift"]
    assert fd["checked"] is True
    assert fd["edit_fps"] == 24
    # video 2×2 + voice 2 + music 2 + ambient 2 = 10 (otio carries no captions)
    assert fd["boundaries_checked"] == 10
    assert fd["off_grid"] == []
    assert fd["all_zero"] is True
    assert fd["cumulative_max_residual"] == "0"


def test_frame_drift_detects_constructed_off_grid_boundary(
        tmp_project, add_shot, make_take):
    t1, src1 = _register(tmp_project, add_shot, make_take, "S001")
    tl = Timeline(
        fps=24, duration_ms=41,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take=t1, source=src1, start_ms=0, duration_ms=41),
        ]),
    )
    doc = conform_loss_report(tmp_project, "otio", tl, {})
    fd = doc["frame_drift"]
    assert fd["all_zero"] is False
    assert len(fd["off_grid"]) == 1
    row = fd["off_grid"][0]
    assert row["ms"] == 41
    assert row["clip"] == f"video:S001/{t1}.end"
    # 41 ms · 24 fps / 1000 = 123/125 frames (0.984); residual 2/125 (0.016)
    assert Fraction(row["frames_exact"]) == Fraction(123, 125)
    assert Fraction(row["residual_frames"]) == Fraction(2, 125)
    assert Fraction(fd["cumulative_max_residual"]) == Fraction(2, 125)


def test_frame_drift_jianying_includes_caption_boundaries(
        tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    doc = conform_loss_report(tmp_project, "jianying", tl, {})
    fd = doc["frame_drift"]
    # 10 clip boundaries + 2 captions × 2 = 14
    assert fd["boundaries_checked"] == 14
    assert fd["all_zero"] is True


def test_ntsc_source_rate_grid_drift_and_one_frame_point(
        tmp_project, add_shot, make_take):
    """23.976 material on a 24 int grid: 0.1% — 1 ms per second, one edit frame
    (1000/24 ms) reached at exactly 125000/3 ms of grid time."""
    tl, sources = _full_timeline(tmp_project, add_shot, make_take)
    # stretch S002 to 60 s (on-grid: 60000 and 62000 are multiples of 125)
    tl.tracks.video[1].duration_ms = 60000
    tl.duration_ms = 62000
    rates = {sources["S001"]: Rate.from_fraction(24000, 1001),
             sources["S002"]: "23.976"}
    doc = conform_loss_report(tmp_project, "otio", tl, {}, source_rates=rates)
    rows = {r["clip"]: r for r in doc["frame_drift"]["rate_mismatch"]}

    r1 = rows["video:S001/" + tl.tracks.video[0].take]
    assert r1["source_rate"] == "24000/1001"
    assert Fraction(r1["grid_drift_ms_over_clip"]) == Fraction(2)      # 2000 ms → 2 ms
    assert Fraction(r1["one_frame_drift_at_ms"]) == Fraction(125000, 3)
    assert r1["exceeds_one_frame"] is False                            # 2 < 125/3

    r2 = rows["video:S002/" + tl.tracks.video[1].take]
    assert r2["source_rate"] == "24000/1001"  # "23.976" alias classifies exactly
    assert Fraction(r2["grid_drift_ms_over_clip"]) == Fraction(60)     # 60 s → 60 ms
    assert r2["exceeds_one_frame"] is True                             # 60 > 125/3


def test_source_rate_equal_to_grid_yields_no_row(tmp_project, add_shot, make_take):
    tl, sources = _full_timeline(tmp_project, add_shot, make_take)
    doc = conform_loss_report(tmp_project, "otio", tl, {},
                              source_rates={sources["S001"]: 24})
    assert doc["frame_drift"]["rate_mismatch"] == []


def test_source_rate_unknown_is_reported_not_guessed(tmp_project, add_shot, make_take):
    tl, sources = _full_timeline(tmp_project, add_shot, make_take)
    doc = conform_loss_report(tmp_project, "otio", tl, {},
                              source_rates={sources["S001"]: 23.5})
    rows = doc["frame_drift"]["rate_mismatch"]
    assert len(rows) == 1
    assert rows[0]["source_rate"] == "unknown"
    assert "grid_drift_ms_over_clip" not in rows[0], "no fabricated drift numbers"


def test_source_rate_unmatched_key_is_noted(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    doc = conform_loss_report(tmp_project, "otio", tl, {},
                              source_rates={"media/gen/NOPE/x.mp4": "23.976"})
    assert any("NOPE" in n for n in doc["notes"])
    assert doc["frame_drift"]["rate_mismatch"] == []


def test_srt_ass_frame_drift_not_checked(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    from manju.exporters.srt_ass import compile_srt
    srt = tmp_project.captions_dir / "captions.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text(compile_srt(tl), encoding="utf-8")
    doc = conform_loss_report(tmp_project, "srt_ass", tl, srt)
    fd = doc["frame_drift"]
    assert fd["checked"] is False
    assert "ms" in fd["reason"].lower() or "fps" in fd["reason"].lower()


# --------------------------------------------------------------------------- #
# 4. jianying / srt_ass / native_draft / openclap honesty                      #
# --------------------------------------------------------------------------- #


def test_jianying_transition_approximated_as_metadata(tmp_project, add_shot, make_take):
    from manju.exporters.jianying import export_jianying

    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    draft = export_jianying(tmp_project, tl)
    before = draft.read_bytes()
    doc = conform_loss_report(tmp_project, "jianying", tl, draft)
    assert draft.read_bytes() == before

    got = _all_classified(doc)
    assert got["transitions"] == "approximated"
    row = _rows_by_feature(doc, "approximated")["transitions"]
    assert "manju" in row["detail"].lower()          # rides the manju_v stamp
    assert "round-trip" in row["detail"].lower()     # and says it round-trips
    assert "jianying.py" in row["where"]
    # captions land as real text materials + segments:
    assert got["captions"] == "preserved"
    assert got["audio_tracks"] == "preserved"
    assert got["overlays"] == "dropped"
    assert got["audio_fade_in"] == "dropped"
    assert got["ducking"] == "dropped"
    assert got["audio_loops"] == "dropped"


def test_srt_ass_is_caption_only_and_says_so(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    from manju.exporters.srt_ass import compile_srt
    srt = tmp_project.captions_dir / "captions.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text(compile_srt(tl), encoding="utf-8")

    doc = conform_loss_report(tmp_project, "srt_ass", tl, srt)
    got = _all_classified(doc)
    assert got["captions"] == "preserved"
    # EVERY other present feature is honestly unsupported — not silently "fine":
    others = {f for f in got if f != "captions"}
    assert others, "fixture must carry non-caption features"
    for feat in others:
        assert got[feat] == "unsupported", f"{feat} must be unsupported for srt_ass"
    assert any("caption-only" in n.lower() for n in doc["notes"])


def test_native_draft_classification(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    doc = conform_loss_report(tmp_project, "native_draft", tl, {})
    got = _all_classified(doc)
    assert got["transitions"] == "dropped"      # adapter never reads transition_out
    assert got["captions"] == "preserved"       # TextSegment per caption
    assert got["clip_volume"] == "approximated"  # dB → linear volume kwarg, guarded
    assert got["audio_loops"] == "dropped"
    assert got["overlays"] == "dropped"


def test_openclap_classification(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    doc = conform_loss_report(tmp_project, "openclap", tl, {})
    got = _all_classified(doc)
    # x-manju extension = namespaced metadata ⇒ approximated:
    assert got["overlays"] == "approximated"
    assert got["captions"] == "approximated"
    assert got["clip_volume"] == "approximated"
    assert got["video_in_points"] == "approximated"
    assert got["audio_gain"] == "approximated"   # outputGain + exact dB in x-manju
    assert got["audio_in_points"] == "dropped"   # start_offset_ms never read
    assert got["transitions"] == "dropped"
    assert got["audio_tracks"] == "preserved"


def test_unknown_target_is_refused(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    with pytest.raises(ProjectError) as exc:
        conform_loss_report(tmp_project, "premiere", tl, {})
    assert "otio" in str(exc.value)


def test_unknown_feature_hard_fails_classification():
    with pytest.raises(ProjectError) as exc:
        classify_features("otio", [
            {"feature": "martian_track", "detail": "x", "where": "y"}])
    assert "martian_track" in str(exc.value)


# --------------------------------------------------------------------------- #
# 5. write / read / delete inert + tamper + determinism                        #
# --------------------------------------------------------------------------- #


def test_write_read_delete_and_tamper(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    doc = conform_loss_report(tmp_project, "otio", tl, {})
    path = write_conform_report(tmp_project, doc)

    assert path.parent == tmp_project.reports_dir / "conform"
    assert path.name.startswith("otio_")
    loaded = read_conform_report(path)
    assert loaded == doc  # digest stripped on read; facts identical

    # reading is inert
    before = path.read_bytes()
    read_conform_report(path)
    assert path.read_bytes() == before

    # tamper with a fact ⇒ rejected
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["target"] = "jianying"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    with pytest.raises(ProjectError):
        read_conform_report(path)

    # missing digest ⇒ rejected
    raw = {k: v for k, v in raw.items() if k != "digest"}
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProjectError):
        read_conform_report(path)

    # deletable derived output: removing it breaks nothing, rewrite is identical
    path.unlink()
    doc2 = conform_loss_report(tmp_project, "otio", tl, {})
    path2 = write_conform_report(tmp_project, doc2)
    assert path2 == path
    assert read_conform_report(path2) == doc


def test_report_is_byte_deterministic(tmp_project, add_shot, make_take):
    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    doc_a = conform_loss_report(tmp_project, "jianying", tl, {})
    doc_b = conform_loss_report(tmp_project, "jianying", tl, {})
    assert doc_a == doc_b
    p1 = write_conform_report(tmp_project, doc_a)
    first = p1.read_bytes()
    p2 = write_conform_report(tmp_project, doc_b)
    assert p2 == p1
    assert p2.read_bytes() == first


def test_reports_conform_is_never_a_build_input():
    """Grep-pin: no src module besides exporters/conform.py mentions the
    reports/conform directory — it stays a deletable derived output."""
    offenders = []
    anchor_seen = False
    for py in SRC_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # Match the conform DIRECTORY precisely: "reports/conform/" with its
        # trailing slash, or the exact reports_dir / "conform" join. The bare
        # prefix "reports/conform" also matches "reports/conformance" — the
        # unrelated, separately-pinned delivery-conformance store — which made
        # this pin false-positive the moment build/conformance.py landed.
        if "reports/conform/" in text or 'reports_dir / "conform"' in text:
            if py.name == "conform.py" and py.parent.name == "exporters":
                anchor_seen = True
            else:
                offenders.append(str(py))
    assert anchor_seen, "conform.py must own the reports/conform path"
    assert offenders == [], f"reports/conform must never be a build input: {offenders}"


# --------------------------------------------------------------------------- #
# 6. reimport delta — REUSE of build/roundtrip (zero new diff logic)           #
# --------------------------------------------------------------------------- #


def test_reimport_changes_unedited_export_is_unchanged(
        tmp_project, add_shot, make_take):
    from manju.exporters.otio import export_otio

    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    tmp_project.save_timeline(tl)
    out = export_otio(tmp_project, tl)
    block = reimport_changes(tmp_project, out)
    assert block["kind"] == "otio"
    assert block["changed"] == []
    assert [r["class"] for r in block["unchanged"]] == ["no_changes"]
    assert block["unknown"] == []


def test_reimport_changes_detects_trim_via_plan_roundtrip(
        tmp_project, add_shot, make_take, tmp_path):
    from manju.exporters.otio import export_otio

    tl, _ = _full_timeline(tmp_project, add_shot, make_take)
    tmp_project.save_timeline(tl)
    out = export_otio(tmp_project, tl)

    edited = tmp_path / out.name  # same stem so find_baseline locates it
    shutil.copy2(out, edited)
    data = json.loads(edited.read_text(encoding="utf-8"))
    clip0 = data["tracks"]["children"][0]["children"][0]
    clip0["source_range"]["start_time"]["value"] = 27.0  # 125 ms → 1125 ms in-point
    edited.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    block = reimport_changes(tmp_project, edited)
    assert any(r["class"] == "trim" for r in block["changed"])
    assert block["unchanged"] == []

    # the block rides the conform-loss doc and survives write/read+digest
    doc = conform_loss_report(tmp_project, "otio", tl, out)
    doc["reimport"] = block
    path = write_conform_report(tmp_project, doc)
    assert read_conform_report(path)["reimport"] == block


# --------------------------------------------------------------------------- #
# 7. meta-pin — no exporter escapes classification (§6.2)                      #
# --------------------------------------------------------------------------- #


def test_every_exporter_module_is_classified_or_declared_unsupported():
    exp_dir = SRC_ROOT / "exporters"
    modules = {p.stem for p in exp_dir.glob("*.py")
               if p.stem not in ("__init__", "conform")}
    modules |= {p.name for p in exp_dir.iterdir()
                if p.is_dir() and p.name != "__pycache__"
                and (p / "__init__.py").exists()}
    assert modules, "exporter scan came up empty — wrong directory?"
    for mod in sorted(modules):
        assert mod in TARGET_CLASSIFIERS or mod in UNSUPPORTED_TARGETS, (
            f"exporter module {mod!r} has no conform classifier and is not in "
            "UNSUPPORTED_TARGETS — classify it (doc §6.2: no silent degradation)")
    for mod, reason in UNSUPPORTED_TARGETS.items():
        assert isinstance(reason, str) and reason.strip(), (
            f"UNSUPPORTED_TARGETS[{mod!r}] needs a real reason string")
    # and no ghost classifiers for modules that do not exist
    assert set(TARGET_CLASSIFIERS) <= modules, (
        f"classifiers without an exporter module: "
        f"{sorted(set(TARGET_CLASSIFIERS) - modules)}")
