"""Wave 3 (MANJU_WINDOWS_ONLY_LEAN_V3 §5.4) — caption sub-feature loss rows.

格式不支持的语义进入 conform-loss,不伪造: a caption cue's optional ``role``
and ``speaker`` are timeline truth that most exits cannot carry. This file
pins the two new conform features:

* ``KNOWN_FEATURES`` gains ``caption_roles`` / ``caption_speakers``;
* ``timeline_feature_inventory`` emits a row for each ONLY when a cue actually
  carries a role / speaker (no vacuous rows — the honesty rule);
* EVERY classifier target has an explicit, honest rule for both features
  (the classify_features hard-error discipline forces completeness):
  ttml preserves both (ttm:role/x-manju:role + ttm:agent); srt_ass carries
  roles only in the ASS Name field (SRT/VTT drop them) and drops speakers;
  jianying's text material carries the speaker natively but never the role;
  openclap dumps both into the x-manju extension; every picture exit
  (otio/edl/fcpxml/xmeml/native_draft) drops them with the caption track;
* the ``_FALLBACK_RULES`` caption-only branch keeps working — non-caption
  features still fall to the honest "unsupported" rows for srt_ass/ttml.
"""

from __future__ import annotations

import pytest

from manju.core.models import (
    CaptionLine,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
)
from manju.exporters import conform
from manju.exporters.conform import (
    KNOWN_FEATURES,
    TARGET_CLASSIFIERS,
    classify_features,
    conform_loss_report,
    timeline_feature_inventory,
)

CATEGORIES = ("preserved", "approximated", "dropped", "unsupported")


def _cue(start_ms, end_ms, text, **kw):
    return CaptionLine(start_ms=start_ms, end_ms=end_ms, text=text, **kw)


def _tl(captions, video=()):
    return Timeline(
        meta=TimelineMeta(compiled_from="w3-conform-captions"),
        fps=24, width=1080, height=1920,
        duration_ms=max((c.end_ms for c in captions), default=0),
        tracks=TimelineTracks(video=list(video), captions=list(captions)),
    )


def _features(tl):
    return {r["feature"] for r in timeline_feature_inventory(tl)}


def _got(cats):
    return {r["feature"]: c for c in CATEGORIES for r in cats[c]}


def _rows(cats, category):
    return {r["feature"]: r for r in cats[category]}


# --------------------------------------------------------------------------- #
# 1. KNOWN_FEATURES + inventory honesty (rows only when present)               #
# --------------------------------------------------------------------------- #


def test_known_features_gains_caption_subfeatures():
    assert "caption_roles" in KNOWN_FEATURES
    assert "caption_speakers" in KNOWN_FEATURES


def test_inventory_silent_when_no_roles_or_speakers():
    tl = _tl([_cue(0, 1000, "你好"), _cue(1000, 2000, "世界", shot="S001")])
    feats = _features(tl)
    assert "captions" in feats
    assert "caption_roles" not in feats, "no role on any cue ⇒ no row"
    assert "caption_speakers" not in feats, "no speaker on any cue ⇒ no row"


def test_inventory_emits_rows_when_roles_and_speakers_present():
    tl = _tl([
        _cue(0, 1000, "你好", speaker="林夏", role="sdh"),
        _cue(1000, 2000, "世界", speaker="阿明"),
        _cue(2000, 3000, "再见"),
    ])
    rows = {r["feature"]: r for r in timeline_feature_inventory(tl)}
    assert set(rows) >= {"captions", "caption_roles", "caption_speakers"}
    roles = rows["caption_roles"]
    assert set(roles) == {"feature", "detail", "where"}
    assert "1" in roles["detail"]                    # one roled cue
    assert roles["where"] == "tracks.captions[].role"
    speakers = rows["caption_speakers"]
    assert "2" in speakers["detail"]                 # two speakered cues
    assert speakers["where"] == "tracks.captions[].speaker"


def test_inventory_role_only_and_speaker_only_are_independent():
    role_only = _tl([_cue(0, 1000, "x", role="forced")])
    feats = _features(role_only)
    assert "caption_roles" in feats and "caption_speakers" not in feats

    speaker_only = _tl([_cue(0, 1000, "x", speaker="林夏")])
    feats = _features(speaker_only)
    assert "caption_speakers" in feats and "caption_roles" not in feats


# --------------------------------------------------------------------------- #
# 2. every target classifies both features — no hard error, honest category    #
# --------------------------------------------------------------------------- #

# (caption_roles category, caption_speakers category) per target — the audited
# truth of each writer, see the rule tables' `where` cites.
EXPECTED = {
    "otio": ("dropped", "dropped"),            # captions never written at all
    "jianying": ("dropped", "preserved"),      # text material carries speaker
    "native_draft": ("dropped", "dropped"),    # TextSegment: text+timerange only
    "srt_ass": ("approximated", "dropped"),    # ASS Name carries role; no speaker
    "ttml": ("preserved", "preserved"),        # ttm:role/x-manju:role + ttm:agent
    "edl": ("dropped", "dropped"),             # picture cut list, no captions
    "fcpxml": ("dropped", "dropped"),          # captions ride SRT/TTML exits
    "openclap": ("approximated", "approximated"),  # x-manju.captions dumps
    "xmeml": ("dropped", "dropped"),           # captions ride SRT/TTML exits
}


def test_expected_table_covers_every_classifier_target():
    assert set(EXPECTED) == set(TARGET_CLASSIFIERS), (
        "a classifier target is missing an expectation here — audit it")


@pytest.mark.parametrize("target", sorted(EXPECTED))
def test_target_classifies_caption_subfeatures_without_hard_error(target):
    tl = _tl([_cue(0, 1000, "你好", speaker="林夏", role="sdh")])
    cats = classify_features(target, timeline_feature_inventory(tl))  # no raise
    got = _got(cats)
    want_roles, want_speakers = EXPECTED[target]
    assert got["caption_roles"] == want_roles, (
        f"{target}: caption_roles must be {want_roles}, got {got['caption_roles']}")
    assert got["caption_speakers"] == want_speakers, (
        f"{target}: caption_speakers must be {want_speakers}, "
        f"got {got['caption_speakers']}")
    # every row is a real audit row: non-empty detail + a where cite
    for cat in CATEGORIES:
        for row in cats[cat]:
            if row["feature"] in ("caption_roles", "caption_speakers"):
                assert row["detail"].strip() and row["where"].strip()


def test_ttml_preserves_both_with_the_binding_vocabulary():
    tl = _tl([_cue(0, 1000, "你好", speaker="林夏", role="sdh")])
    cats = classify_features("ttml", timeline_feature_inventory(tl))
    preserved = _rows(cats, "preserved")
    assert "x-manju:role" in preserved["caption_roles"]["detail"]
    assert "ttm:agent" in preserved["caption_speakers"]["detail"]
    assert "ttml.py" in preserved["caption_roles"]["where"]
    assert "ttml.py" in preserved["caption_speakers"]["where"]


def test_jianying_speaker_native_role_dropped():
    tl = _tl([_cue(0, 1000, "你好", speaker="林夏", role="sdh")])
    cats = classify_features("jianying", timeline_feature_inventory(tl))
    assert "speaker" in _rows(cats, "preserved")["caption_speakers"]["detail"].lower()
    assert "jianying.py" in _rows(cats, "dropped")["caption_roles"]["where"]


def test_openclap_both_ride_the_x_manju_extension():
    tl = _tl([_cue(0, 1000, "你好", speaker="林夏", role="sdh")])
    cats = classify_features("openclap", timeline_feature_inventory(tl))
    approx = _rows(cats, "approximated")
    assert "x-manju" in approx["caption_roles"]["detail"]
    assert "x-manju" in approx["caption_speakers"]["detail"]


# --------------------------------------------------------------------------- #
# 3. the srt_ass conform DOC — speakers dropped with an explanatory detail     #
# --------------------------------------------------------------------------- #


def test_srt_ass_doc_lists_speakers_dropped_with_detail(tmp_project):
    from manju.exporters.srt_ass import compile_srt

    tl = _tl([
        _cue(0, 1000, "你好", speaker="林夏", role="sdh"),
        _cue(1000, 2000, "世界", speaker="阿明"),
    ])
    srt = tmp_project.captions_dir / "captions.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text(compile_srt(tl), encoding="utf-8")

    doc = conform_loss_report(tmp_project, "srt_ass", tl, srt)
    dropped = {r["feature"]: r for r in doc["dropped"]}
    assert "caption_speakers" in dropped, (
        "the srt_ass conform doc must list caption_speakers under dropped")
    row = dropped["caption_speakers"]
    assert "speaker" in row["detail"].lower()        # says WHAT is lost
    assert "srt_ass.py" in row["where"]

    # roles: approximated — the ASS Name field carries them; SRT/VTT drop them
    approx = {r["feature"]: r for r in doc["approximated"]}
    assert "caption_roles" in approx
    detail = approx["caption_roles"]["detail"]
    assert "Name" in detail                          # the ASS carrier is named
    assert "SRT" in detail and "VTT" in detail       # the drops are stated
    assert "drop" in detail.lower()


def test_srt_ass_doc_captions_still_preserved(tmp_project):
    from manju.exporters.srt_ass import compile_srt

    tl = _tl([_cue(0, 1000, "你好", speaker="林夏", role="sdh")])
    srt = tmp_project.captions_dir / "captions.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text(compile_srt(tl), encoding="utf-8")
    doc = conform_loss_report(tmp_project, "srt_ass", tl, srt)
    got = {r["feature"]: c for c in CATEGORIES for r in doc[c]}
    assert got["captions"] == "preserved"


# --------------------------------------------------------------------------- #
# 4. the caption-only fallback branch keeps working                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("target", ["srt_ass", "ttml"])
def test_fallback_still_marks_non_caption_features_unsupported(target):
    tl = _tl(
        [_cue(0, 1000, "你好", speaker="林夏", role="sdh")],
        video=[VideoClip(shot="S001", take="T", source="media/gen/S001/T.mp4",
                         start_ms=0, duration_ms=1000)],
    )
    cats = classify_features(target, timeline_feature_inventory(tl))
    got = _got(cats)
    assert got["video_clips"] == "unsupported"       # the fallback, intact
    row = _rows(cats, "unsupported")["video_clips"]
    assert "caption-only" in row["detail"]
    # while the caption sub-features take their EXPLICIT rows, not the fallback
    want_roles, want_speakers = EXPECTED[target]
    assert got["caption_roles"] == want_roles
    assert got["caption_speakers"] == want_speakers


def test_completeness_holds_for_every_target_with_subfeatures_present():
    """inventory == union of the four categories, pairwise disjoint, for a
    timeline carrying captions + both sub-features on EVERY target."""
    tl = _tl(
        [_cue(0, 1000, "你好", speaker="林夏", role="sdh")],
        video=[VideoClip(shot="S001", take="T", source="media/gen/S001/T.mp4",
                         start_ms=0, duration_ms=1000)],
    )
    inventory = _features(tl)
    for target in sorted(TARGET_CLASSIFIERS):
        cats = classify_features(target, timeline_feature_inventory(tl))
        union: set[str] = set()
        for cat in CATEGORIES:
            feats = {r["feature"] for r in cats[cat]}
            assert not (union & feats), (
                f"{target}: feature in >1 category: {union & feats}")
            union |= feats
        assert union == inventory, (
            f"{target}: classified {sorted(union)} != inventory {sorted(inventory)}")
