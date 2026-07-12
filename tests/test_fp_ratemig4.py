"""FP rational-edit-rate migration, STAGE 4 (R4): rational EXPORT truth + the
folded R3 remnant (audio-master sample facts), with INT BYTE-IDENTITY as the
non-negotiable product.

R2 landed the opt-in rational build spine (cumulative-boundary compile,
``VideoClip.duration_frames``, the timeline ``rate_echo``, native ``-r num/den``
render). R4 makes the EXPORTS tell the rational truth:

* ``exporters/otio.py`` — a rational timeline exports RationalTime values as EXACT
  WHOLE-FRAME INTEGERS (video from ``duration_frames``, telescoped starts; audio
  via ``ms_to_frames``) at OTIO's own ``float64`` fps rate (24000/1001 →
  23.976023976023978). Int timelines are BYTE-IDENTICAL to today's float
  ``ms×fps/1000`` export.
* ``exporters/conform.py`` — the drift checker measures a rational export against
  the exact rational grid and reports ``all_zero: true`` / ``all_zero_by_construction``
  for an R2-compiled + R4-exported project (the closing pin of the R-track); the
  µs carriers (jianying/native_draft) get one honest ≤½ms approximation note;
  int behaviour is unchanged.
* ``exporters/edl.py`` — the CMX3600 writer CONSUMES ``duration_frames`` for exact
  record/source TC on a rational clip; the ms fallback (every int project) keeps
  S2's golden bytes byte-identical.
* ``media/masters.py`` — each artifact row gains ``duration_samples`` /
  ``sample_basis`` ONLY for a rational-echo project (2002 samples/frame at
  24000/1001 & 48 kHz); an int project's ``masters.json`` (and its
  ``index_digest``, which covers the artifact rows) stays byte-identical.

Red-first (§20): written to pin the SPEC (frame math spelled out inline via
:mod:`manju.core.timebase`), not a copy of the output.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from manju.core.models import (
    AudioClip,
    CaptionLine,
    Dialogue,
    EditRate,
    OverlayClip,
    ProjectConfig,
    ShotSpec,
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)
from manju.core.timebase import (
    Rate,
    Rounding,
    Timecode,
    frames_to_ms,
    frames_to_samples,
    ms_to_frames,
)
from manju.exporters import conform
from manju.exporters.conform import conform_loss_report
from manju.exporters.edl import compile_edl, export_edl
from manju.exporters.otio import export_otio
from manju.timeline.compiler import CompileInput, ShotInput, compile_timeline

# --------------------------------------------------------------------------- #
# constants + fixtures                                                         #
# --------------------------------------------------------------------------- #

NTSC = Rate.from_fraction(24000, 1001)          # 23.976
R24 = Rate.from_fraction(24, 1)
# The EXACT IEEE-754 double for 24000/1001 — OTIO's own float64 rate convention.
# Pinned so a future refactor that reaches for a rounded/quantised rate is caught.
FLOAT64_NTSC = 23.976023976023978
assert 24000 / 1001 == FLOAT64_NTSC              # sanity: this IS the double


def _shot(sid: str, *, duration="auto", take: int | None = 4000,
          voice: int | None = None, source_in_ms: int = 0) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=sid, duration=duration,
                      dialogue=Dialogue(speaker="linxia", text="雨夜")),
        take_name="take_01", take_source=f"gen/{sid}/take_01.mp4",
        take_duration_ms=take, voice_source=None, voice_duration_ms=voice,
        source_in_ms=source_in_ms,
    )


def _compile(config: ProjectConfig, shots: list[ShotInput]) -> Timeline:
    return compile_timeline(CompileInput(config=config, rules=TimelineRules(), shots=shots))


def _rational_config() -> ProjectConfig:
    return ProjectConfig(name="ratemig4", fps=24, edit_rate={"num": 24000, "den": 1001})


def _rat_video_timeline() -> Timeline:
    """A hand-built rational timeline: two 48-frame clips (2002ms each on the
    cumulative-boundary grid) + a virtual trim on the second, plus a voice clip.
    Frame truth is stamped exactly as the R2 compiler would."""
    return Timeline(
        meta=TimelineMeta(compiled_from="fp-r4"),
        fps=24, width=1080, height=1920, duration_ms=4004,
        rate_echo=EditRate(num=24000, den=1001),
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                          start_ms=0, duration_ms=2002, duration_frames=48),
                VideoClip(shot="S002", take="take_01", source="media/gen/S002/take_01.mp4",
                          start_ms=2002, duration_ms=2002, duration_frames=48,
                          source_in_ms=1001),  # 1001ms in-point == 24 frames
            ],
            voice=[AudioClip(source="media/voice/v.wav", start_ms=0, duration_ms=2002)],
        ),
    )


def _rt_values(doc: dict) -> list:
    """Every RationalTime.value in the document (depth-first)."""
    out: list = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("OTIO_SCHEMA") == "RationalTime.1":
                out.append(o["value"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return out


def _rt_rates(doc: dict) -> list:
    out: list = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("OTIO_SCHEMA") == "RationalTime.1":
                out.append(o["rate"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return out


# =====================================================================
# 1. OTIO — golden int byte pin (existing-style export unchanged)
# =====================================================================


def test_otio_int_export_is_byte_identical_float_ms_grid(tmp_project):
    """Int timeline: RationalTime is today's float ``round(ms*fps/1000, 6)`` at a
    float rate — byte-identical to every pre-migration export. No rational surface
    leaks (no integer-only values, no ``edit_rate`` in metadata)."""
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp-r4-int"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="take_01",
                      source="media/gen/S001/take_01.mp4",
                      start_ms=0, duration_ms=2000),
        ]),
    )
    out = export_otio(tmp_project, tl)
    text = out.read_text(encoding="utf-8")
    doc = json.loads(text)

    clip = doc["tracks"]["children"][0]["children"][0]
    dur = clip["source_range"]["duration"]
    # derived from spec: round(2000 * 24 / 1000, 6) == 48.0 at a float rate
    assert dur == {"OTIO_SCHEMA": "RationalTime.1", "rate": 24.0, "value": 48.0}
    assert isinstance(dur["value"], float) and isinstance(dur["rate"], float)
    start = clip["source_range"]["start_time"]
    assert start["value"] == 0.0 and isinstance(start["value"], float)
    # byte-level: floats render with a decimal point; no rational tokens anywhere
    assert '"value": 48.0' in text and '"rate": 24.0' in text
    assert "edit_rate" not in text and "duration_frames" not in text
    assert "23.976" not in text
    # determinism: a re-export is byte-for-byte identical
    assert export_otio(tmp_project, tl).read_bytes() == out.read_bytes()


# =====================================================================
# 2. OTIO — rational integer-values + float64 rate pins
# =====================================================================


def test_otio_rational_export_is_integer_frames_at_float64_rate(tmp_project):
    tl = _rat_video_timeline()
    out = export_otio(tmp_project, tl)
    doc = json.loads(out.read_text(encoding="utf-8"))

    # (a) EVERY RationalTime.rate is the exact float64 of 24000/1001
    rates = _rt_rates(doc)
    assert rates and all(r == FLOAT64_NTSC for r in rates), rates
    # (b) EVERY RationalTime.value is a genuine Python int — no fractional frames,
    #     no ms×fps float math survived onto the rational path
    vals = _rt_values(doc)
    assert vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals), vals

    v0 = doc["tracks"]["children"][0]["children"][0]
    v1 = doc["tracks"]["children"][0]["children"][1]
    a0 = doc["tracks"]["children"][1]["children"][0]
    # (c) video duration is the compiler's EXACT frame truth (duration_frames)
    assert v0["source_range"]["duration"]["value"] == 48
    assert v0["source_range"]["start_time"]["value"] == 0
    assert v0["media_reference"]["available_range"]["duration"]["value"] == 48
    # (d) a virtual trim: 1001ms in-point telescopes to 24 whole frames; the
    #     available_range widens to in-point + window (24 + 48 == 72)
    assert v1["source_range"]["start_time"]["value"] == ms_to_frames(1001, NTSC) == 24
    assert v1["source_range"]["duration"]["value"] == 48
    assert v1["media_reference"]["available_range"]["duration"]["value"] == 72
    # (e) audio (no duration_frames) uses ms_to_frames — still an exact integer
    assert a0["source_range"]["duration"]["value"] == ms_to_frames(2002, NTSC) == 48
    # (f) the exact rational truth is surfaced in metadata for the rational project
    assert doc["metadata"]["manju"]["edit_rate"] == {"num": 24000, "den": 1001}
    # (g) global start is integer 0, not 0.0
    assert doc["global_start_time"]["value"] == 0
    assert isinstance(doc["global_start_time"]["value"], int)


def test_otio_rational_from_a_real_r2_compile(tmp_project):
    """End-to-end: a genuinely R2-compiled 1001-family timeline exports integer
    frames whose durations equal the stamped duration_frames."""
    tl = _compile(_rational_config(),
                  [_shot("S001", duration=2.5), _shot("S002", duration=2.0)])
    assert tl.rate_echo == EditRate(num=24000, den=1001)
    frames = [v.duration_frames for v in tl.tracks.video]
    assert frames == [60, 48]  # 2.5s -> 60, 2.0s -> 48 whole frames @24000/1001

    out = export_otio(tmp_project, tl)
    doc = json.loads(out.read_text(encoding="utf-8"))
    got = [c["source_range"]["duration"]["value"]
           for c in doc["tracks"]["children"][0]["children"]]
    assert got == frames
    assert all(r == FLOAT64_NTSC for r in _rt_rates(doc))


# =====================================================================
# 3. conform — all_zero BY CONSTRUCTION on a rational compile→export
#    (the closing pin of the whole R-track)
# =====================================================================


def test_conform_rational_frame_drift_all_zero_by_construction(tmp_project):
    tl = _rat_video_timeline()
    doc = conform_loss_report(tmp_project, "otio", tl, {})
    fd = doc["frame_drift"]
    assert fd["checked"] is True
    assert fd["grid"] == "rational"
    assert fd["edit_rate"] == "24000/1001"
    assert fd["edit_fps"] == 24                 # nominal label
    assert fd["off_grid"] == []
    assert fd["all_zero"] is True
    assert fd["all_zero_by_construction"] is True
    assert fd["cumulative_max_residual"] == "0"
    # otio drift tracks = video(2×2) + voice(1×2) = 6 boundaries
    assert fd["boundaries_checked"] == 6
    assert any("BY CONSTRUCTION" in n for n in doc["notes"])


def test_conform_closing_pin_on_a_real_r2_compile(tmp_project):
    """The R-track's closing pin: a project COMPILED by R2 (cumulative whole-frame
    boundaries) and EXPORTED by R4 (integer frames) has ZERO frame drift by
    construction — over a long, varied 1001-family sequence."""
    shots = [_shot(f"S{i:03d}", take=t)
             for i, t in enumerate([2000, 4000, 2600, 5005, 3300, 4200], 1)]
    tl = _compile(_rational_config(), shots)
    assert tl.rate_echo is not None
    assert all(v.duration_frames is not None for v in tl.tracks.video)

    doc = conform_loss_report(tmp_project, "otio", tl, {})
    fd = doc["frame_drift"]
    assert fd["all_zero"] is True and fd["all_zero_by_construction"] is True
    assert fd["off_grid"] == []
    assert fd["boundaries_checked"] == 2 * len(tl.tracks.video)
    # and the residual is exactly zero, computed (not fabricated)
    assert Fraction(fd["cumulative_max_residual"]) == Fraction(0)


def test_conform_int_frame_drift_is_unchanged(tmp_project):
    """Guard: an INT timeline keeps today's integer-grid block verbatim — no
    ``grid``/``edit_rate``/``all_zero_by_construction`` keys leak in."""
    tl = Timeline(
        fps=24, duration_ms=4000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="t", source="media/gen/S001/take_01.mp4",
                      start_ms=0, duration_ms=2000),
            VideoClip(shot="S002", take="t", source="media/gen/S002/take_01.mp4",
                      start_ms=2000, duration_ms=2000),
        ]),
    )
    fd = conform_loss_report(tmp_project, "otio", tl, {})["frame_drift"]
    assert set(fd) == {"checked", "edit_fps", "boundaries_checked", "off_grid",
                       "cumulative_max_residual", "all_zero", "rate_mismatch"}
    assert fd["edit_fps"] == 24 and fd["all_zero"] is True


def test_conform_rational_same_clock_source_yields_no_mismatch(tmp_project):
    """A 24000/1001 SOURCE on a 24000/1001 rational edit grid is the same clock —
    no drift row invented (the equality check uses the exact rational fraction,
    not the integer nominal)."""
    tl = _rat_video_timeline()
    doc = conform_loss_report(tmp_project, "otio", tl, {},
                              source_rates={"media/gen/S001/take_01.mp4": "24000/1001"})
    assert doc["frame_drift"]["rate_mismatch"] == []


# =====================================================================
# 4. conform — ms/µs-carrier honest note rows
# =====================================================================


@pytest.mark.parametrize("target", ["jianying", "native_draft"])
def test_conform_rational_ms_carrier_note(tmp_project, target):
    """jianying/native_draft store µs (ms×1000): a rational project gets ONE honest
    note that the µs stream approximates the 1001 grid at ≤½ms/boundary."""
    tl = _rat_video_timeline()
    doc = conform_loss_report(tmp_project, target, tl, {})
    joined = " ".join(doc["notes"])
    assert "MICROSECOND" in joined
    assert "½ms" in joined or "1/2" in joined or "half" in joined.lower()
    assert "APPROXIMATES" in joined
    # the drift block itself is still all-zero-by-construction (frame-truth walk)
    assert doc["frame_drift"]["all_zero_by_construction"] is True


def test_conform_int_carrier_has_no_rational_note(tmp_project):
    """An INT jianying export carries NO µs-approximation note (nothing to approximate)."""
    tl = Timeline(
        fps=24, duration_ms=2000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="t", source="media/gen/S001/take_01.mp4",
                      start_ms=0, duration_ms=2000)]),
    )
    doc = conform_loss_report(tmp_project, "jianying", tl, {})
    assert not any("MICROSECOND" in n for n in doc["notes"])


@pytest.mark.parametrize("target", ["srt_ass", "ttml"])
def test_conform_rational_ms_native_captions_stay_silent(tmp_project, target):
    """srt/vtt/ttml ARE ms formats: no frame grid, no carrier note — the reason
    already states why (they are ms-native), even for a rational project."""
    tl = _rat_video_timeline()
    doc = conform_loss_report(tmp_project, target, tl, {})
    fd = doc["frame_drift"]
    assert fd["checked"] is False
    assert "ms" in fd["reason"].lower()
    assert not any("MICROSECOND" in n for n in doc["notes"])


# =====================================================================
# 5. masters — int byte-identity + rational sample facts (2002/frame)
# =====================================================================


def test_masters_sample_facts_helper_rational_and_int():
    """Pure (no-ffmpeg) pin of the sample-facts rule: rational-echo project gets
    exact frame-based samples (2002/frame at 24000/1001 & 48 kHz); an int project
    gets NOTHING (byte-identity); an off-frame rational duration is honestly ms."""
    from manju.media.masters import _sample_facts

    # int timeline → {} (no keys → masters.json + index_digest byte-identical)
    int_tl = Timeline(fps=24, duration_ms=2000)
    assert _sample_facts(int_tl, 2000, 48_000) == {}

    # rational, duration lands on a whole frame (2002ms == 48 frames): EXACT
    rat_tl = Timeline(fps=24, duration_ms=2002, rate_echo=EditRate(num=24000, den=1001))
    facts = _sample_facts(rat_tl, 2002, 48_000)
    assert facts == {"duration_samples": 96096, "sample_basis": "frames"}
    assert facts["duration_samples"] == 48 * 2002               # 2002 samples/frame
    assert facts["duration_samples"] == frames_to_samples(48, NTSC, 48_000)

    # a longer whole-frame duration stays exact and multiplicative
    assert _sample_facts(rat_tl, frames_to_ms(240, NTSC), 48_000) == {
        "duration_samples": 240 * 2002, "sample_basis": "frames"}

    # rational but OFF a whole frame → honest ms basis (never a fake frame count)
    off = frames_to_ms(48, NTSC) + 1  # 2003ms — not a frame boundary
    assert ms_to_frames(off, NTSC) != 0
    facts_ms = _sample_facts(rat_tl, off, 48_000)
    assert facts_ms["sample_basis"] == "ms"
    assert facts_ms["duration_samples"] == round(off * 48_000 / 1000)


_HAS_FFMPEG = shutil.which("ffmpeg") is not None
ffmpeg_only = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required for masters")


def _sine(path: Path, freq: int, dur: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", "48000",
         str(path)], check=True)


@ffmpeg_only
def test_masters_int_masters_json_byte_identical_and_no_facts(tmp_project):
    """An INT project's masters.json artifact rows carry NO sample-fact keys, so
    the index_digest (which covers the rows) is untouched — and two renders are
    byte-identical (deterministic)."""
    from manju.media import masters as M

    _sine(tmp_project.root / "media" / "v.wav", 300, 1.5)
    tmp_project.save_rules(TimelineRules(mode="manual"))
    tl = Timeline(fps=24, duration_ms=1500, tracks=TimelineTracks(
        voice=[AudioClip(source="media/v.wav", start_ms=0, duration_ms=1500)]))

    idx = M.render_masters(tmp_project, tl)
    for a in idx["artifacts"]:
        assert "duration_samples" not in a and "sample_basis" not in a
    # digest is derived from the (fact-free) rows + tdigest, and is deterministic
    from manju.core.hashing import hash_value
    assert idx["index_digest"] == hash_value(
        {"a": idx["artifacts"], "t": idx["timeline_digest"]})
    idx2 = M.render_masters(tmp_project, tl)
    assert idx2["index_digest"] == idx["index_digest"]


@ffmpeg_only
def test_masters_rational_rows_carry_frame_exact_sample_facts(tmp_project):
    """A rational-echo project's rows gain duration_samples/sample_basis; a
    whole-frame render length is EXACT frames (2002/frame at 24000/1001 & 48 kHz)."""
    from manju.media import masters as M

    _sine(tmp_project.root / "media" / "v.wav", 300, 2.1)
    tmp_project.save_rules(TimelineRules(mode="manual"))
    # duration_ms 2002 == frames_to_ms(48, 24000/1001) → lands on a whole frame
    tl = Timeline(fps=24, duration_ms=2002, rate_echo=EditRate(num=24000, den=1001),
                  tracks=TimelineTracks(
                      voice=[AudioClip(source="media/v.wav", start_ms=0, duration_ms=2002)]))
    idx = M.render_masters(tmp_project, tl)
    assert idx["artifacts"], "expected at least the dialogue stem"
    for a in idx["artifacts"]:
        assert a["sample_basis"] == "frames"
        assert a["duration_samples"] == 48 * 2002 == 96096
    # index-level sample_rate already existed (audited, unchanged)
    assert idx["sample_rate"] == 48_000


# =====================================================================
# 6. EDL — rational record TC consumes duration_frames; int goldens green
# =====================================================================


def _tc_to_frames(tc: str, rate: Rate) -> int:
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return Timecode(h, m, s, f, rate, ";" in tc).to_frames()


def test_edl_rational_consumes_duration_frames_over_ms(tmp_project):
    """A rational clip's record/source span is its EXACT duration_frames, even when
    that DISAGREES with the ms→frame telescoping — proof the frame truth is
    consumed (S2 left it unconsumed)."""
    # duration_ms=1000 would telescope to ms_to_frames(1000, NTSC) == 24 frames,
    # but duration_frames says 48 — the exporter must honour 48.
    assert ms_to_frames(1000, NTSC) == 24
    tl = Timeline(
        fps=24, duration_ms=1000, rate_echo=EditRate(num=24000, den=1001),
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="TA", source="media/gen/S001/TA.mp4",
                      start_ms=0, duration_ms=1000, duration_frames=48)]),
    )
    got = compile_edl(tl, rate=NTSC, start_timecode="00:00:00:00", title="X")
    row = [ln for ln in got.splitlines() if ln.startswith("001")][0]
    src_in, src_out, rec_in, rec_out = row.split()[-4:]
    # 48 frames at 24 nominal == 00:00:02:00, NOT the ms-fallback 00:00:01:00
    assert src_out == "00:00:02:00"
    assert _tc_to_frames(src_out, NTSC) - _tc_to_frames(src_in, NTSC) == 48
    assert _tc_to_frames(rec_out, NTSC) - _tc_to_frames(rec_in, NTSC) == 48


def test_edl_rational_record_tc_exact_and_continuous(tmp_project):
    """A real R2 compile → EDL: every event's record span equals its
    duration_frames, and record-out(N) == record-in(N+1) exactly."""
    tl = _compile(_rational_config(),
                  [_shot("S001", duration=2.5), _shot("S002", duration=2.0),
                   _shot("S003", duration=3.0)])
    frames = [v.duration_frames for v in tl.tracks.video]
    assert frames == [60, 48, 72]

    out = export_edl(tmp_project, tl)  # rate resolution picks the 24000/1001 echo
    text = out.read_text(encoding="utf-8")
    assert "FCM: NON-DROP FRAME" in text        # 23.976 is NTSC but not DF-legal
    rows = [ln for ln in text.splitlines() if ln[:3].isdigit()]
    spans, recs = [], []
    for ln, fcount in zip(rows, frames):
        si, so, ri, ro = ln.split()[-4:]
        assert _tc_to_frames(so, NTSC) - _tc_to_frames(si, NTSC) == fcount
        spans.append(_tc_to_frames(ro, NTSC) - _tc_to_frames(ri, NTSC))
        recs.append((_tc_to_frames(ri, NTSC), _tc_to_frames(ro, NTSC)))
    assert spans == frames
    for (_, out_n), (in_n1, _) in zip(recs, recs[1:]):
        assert out_n == in_n1, "record TC discontinuity on the rational grid"


def test_edl_int_clip_uses_ms_fallback_unchanged(tmp_project):
    """An INT clip (no duration_frames) keeps the ms→frame span verbatim — S2's
    golden behaviour is byte-identical (the fallback branch)."""
    tl = Timeline(
        fps=24, duration_ms=1000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="TA", source="media/gen/S001/TA.mp4",
                      start_ms=0, duration_ms=1000)]),
    )
    got = compile_edl(tl, rate=R24, start_timecode="00:00:00:00", title="X")
    row = [ln for ln in got.splitlines() if ln.startswith("001")][0]
    src_in, src_out = row.split()[-4:-2]
    # ms_to_frames(1000, 24) == 24 frames == 00:00:01:00 (the historical value)
    assert src_out == "00:00:01:00"
    assert _tc_to_frames(src_out, R24) - _tc_to_frames(src_in, R24) == 24


# =====================================================================
# 7. roundtrip — plan_roundtrip coherence on a rational export
# =====================================================================


def test_rational_otio_roundtrips_with_no_spurious_changes(tmp_project):
    """A rational OTIO export (integer frames) round-trips against its own baseline
    with NO changes — the frame representation is self-consistent through
    plan_roundtrip (the same _ms conversion is applied to both sides)."""
    tl = _rat_video_timeline()
    tmp_project.save_timeline(tl)
    out = export_otio(tmp_project, tl)          # writes the baseline too

    block = conform.reimport_changes(tmp_project, out)
    assert block["kind"] == "otio"
    assert block["changed"] == []
    assert [r["class"] for r in block["unchanged"]] == ["no_changes"]
    assert block["unknown"] == []


def test_rational_otio_reimport_detects_a_frame_trim(tmp_project, tmp_path):
    """Coherence the other way: editing the rational OTIO in-point to a new whole
    frame is seen as a trim by plan_roundtrip (integer frames read back cleanly)."""
    tl = _rat_video_timeline()
    tmp_project.save_timeline(tl)
    out = export_otio(tmp_project, tl)

    edited = tmp_path / out.name  # same stem so find_baseline locates the baseline
    shutil.copy2(out, edited)
    data = json.loads(edited.read_text(encoding="utf-8"))
    clip0 = data["tracks"]["children"][0]["children"][0]
    clip0["source_range"]["start_time"]["value"] = 24   # trim in by 24 whole frames
    edited.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    block = conform.reimport_changes(tmp_project, edited)
    assert any(r["class"] == "trim" for r in block["changed"])
