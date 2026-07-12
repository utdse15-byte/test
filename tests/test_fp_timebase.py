"""FP Loop B — red-first fixture matrix for the rational-time / SMPTE-timecode /
drift-analysis foundation module (``manju.core.timebase``).

These tests are written BEFORE the implementation (§20 of the roadmap: 现状审计
→ 红灯 Fixture → 最小合同 → 实施 → 全量回归 → 完成报告). Until ``timebase.py``
exists every test in this file errors at import (absent API = red).

The matrix mirrors roadmap §15.1 时间:
    24 · 24000/1001 · 25 · 30000/1001 NDF · 30000/1001 DF · 50 · 60 ·
    60000/1001 · VFR suspected · unknown decimal refused · one-frame boundary
    (frame 0 / last frame of day / minute & 10-minute DF boundaries) ·
    long-duration cumulative drift (2h / 8h pinned exactly) ·
    audio 48k sample alignment (frames↔samples exact at NTSC rates, 1001-periodic).

Every number here is EXACT — computed with :class:`fractions.Fraction`, never a
float. Floats appear only where the API itself is display/parse (``.fps_float``,
``classify_rate`` input).
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from manju.core import timebase as tb
from manju.core.timebase import (
    RANGE_SEMANTICS,
    UNKNOWN,
    Rate,
    Rounding,
    Timecode,
    classify_rate,
    frames_to_ms,
    frames_to_samples,
    grid_drift_ms,
    ms_to_frames,
    one_frame_drift_at,
    parse_timecode,
    rate_mode,
    sample_alignment_period,
    samples_per_frame,
    samples_to_frames,
)

# The supported rate matrix, as (label, Rate) pairs.
FILM = Rate.from_fraction(24, 1)
NTSC24 = Rate.from_fraction(24000, 1001)
PAL = Rate.from_fraction(25, 1)
NTSC30 = Rate.from_fraction(30000, 1001)
NTSC30_INT = Rate.from_fraction(30, 1)
P50 = Rate.from_fraction(50, 1)
P60 = Rate.from_fraction(60, 1)
NTSC60 = Rate.from_fraction(60000, 1001)

ALL_RATES = [FILM, NTSC24, PAL, NTSC30, NTSC30_INT, P50, P60, NTSC60]


# --------------------------------------------------------------------------- #
# 1. Rate — exact rational frames/second                                       #
# --------------------------------------------------------------------------- #

def test_rate_parse_integer_is_exact():
    r = Rate.parse("24")
    assert r.fraction == Fraction(24, 1)
    assert r.exact_int == 24
    assert r.is_ntsc is False
    assert r.fps_float == 24.0


def test_rate_parse_fraction_ntsc_family_exact():
    assert Rate.parse("24000/1001").fraction == Fraction(24000, 1001)
    assert Rate.parse("30000/1001").fraction == Fraction(30000, 1001)
    assert Rate.parse("60000/1001").fraction == Fraction(60000, 1001)
    assert Rate.parse("24000/1001").exact_int is None
    assert Rate.parse("24000/1001").is_ntsc is True


def test_rate_parse_decimal_aliases_map_to_exact_ntsc():
    # The well-known decimal aliases resolve to their EXACT NTSC fraction —
    # NOT a float near 23.976.
    assert Rate.parse("23.976").fraction == Fraction(24000, 1001)
    assert Rate.parse("29.97").fraction == Fraction(30000, 1001)
    assert Rate.parse("59.94").fraction == Fraction(60000, 1001)
    # exact-integer decimals are honoured (no guessing needed).
    assert Rate.parse("24.0").fraction == Fraction(24, 1)


def test_rate_parse_unknown_decimal_refused():
    # No float guessing: an arbitrary decimal is refused, even one that is the
    # raw float rendering of an NTSC rate.
    for bad in ["23.5", "23.976023976", "12.345", "not-a-rate", "", "24/0"]:
        with pytest.raises(ValueError):
            Rate.parse(bad)


def test_rate_from_fraction_normalizes():
    assert Rate.from_fraction(48000, 2002).fraction == Fraction(24000, 1001)
    assert Rate.from_fraction(48, 2).fraction == Fraction(24, 1)
    assert Rate.from_fraction(48, 2).exact_int == 24
    with pytest.raises(ValueError):
        Rate.from_fraction(24, 0)
    with pytest.raises(ValueError):
        Rate.from_fraction(0, 1)
    with pytest.raises(ValueError):
        Rate.from_fraction(-24, 1)


def test_rate_properties_fps_float_is_display_only():
    assert NTSC24.fps_float == pytest.approx(23.976023976, abs=1e-9)
    assert NTSC30.fps_float == pytest.approx(29.97002997, abs=1e-9)
    assert PAL.exact_int == 25
    assert NTSC30.exact_int is None


def test_rate_equality_and_hash():
    assert Rate.from_fraction(24, 1) == Rate.parse("24")
    assert Rate.from_fraction(48000, 2002) == NTSC24
    assert hash(Rate.from_fraction(48000, 2002)) == hash(NTSC24)
    assert NTSC24 != FILM
    assert len({NTSC24, Rate.from_fraction(48000, 2002), FILM}) == 2


# --------------------------------------------------------------------------- #
# 2. Conversions with explicit rounding policy                                 #
# --------------------------------------------------------------------------- #

def test_rounding_modes_on_known_fraction():
    # frames_to_ms(1, 24fps) exact = 1000/24 = 41.666... ms
    assert frames_to_ms(1, FILM, Rounding.FLOOR) == 41
    assert frames_to_ms(1, FILM, Rounding.CEIL) == 42
    assert frames_to_ms(1, FILM, Rounding.ROUND_HALF_UP) == 42
    # a clean .5 tie rounds up (toward +inf)
    # ms_to_frames(ms) at 2fps: 1 frame = 500ms; 250ms -> 0.5 frame -> tie
    assert ms_to_frames(250, Rate.from_fraction(2, 1), Rounding.ROUND_HALF_UP) == 1
    assert ms_to_frames(250, Rate.from_fraction(2, 1), Rounding.FLOOR) == 0


def test_frames_to_ms_is_exact_for_pal_rates():
    # PROVABLE exactness: 25 and 50 fps have integer ms per frame (40 / 20 ms),
    # so frames_to_ms carries ZERO rounding error at every frame.
    for n in range(0, 2000):
        assert frames_to_ms(n, PAL, Rounding.ROUND_HALF_UP) == n * 40
        assert frames_to_ms(n, P50, Rounding.ROUND_HALF_UP) == n * 20


def test_frames_to_ms_ntsc_error_is_bounded_not_zero():
    # STATED BOUND (the invariant that CANNOT hold): for 1001 rates the exact
    # frame time is never a whole number of ms, so frames_to_ms is inexact.
    # The error is bounded by 1/2 ms per conversion and is > 0 for some frames.
    saw_nonzero = False
    for rate in (NTSC24, NTSC30, NTSC60, FILM, NTSC30_INT, P60):
        for n in range(0, 3000):
            exact = Fraction(n * 1000) / rate.fraction  # true ms, a Fraction
            got = frames_to_ms(n, rate, Rounding.ROUND_HALF_UP)
            err = abs(Fraction(got) - exact)
            assert err <= Fraction(1, 2)
            if err > 0:
                saw_nonzero = True
    assert saw_nonzero, "expected sub-ms frame times to force nonzero error"


def test_roundtrip_frames_ms_frames_identity_matrix():
    # PROVEN INVARIANT: frames -> ms -> frames with ROUND_HALF_UP is identity
    # for every supported rate, across a wide explicit range.
    for rate in ALL_RATES:
        for n in list(range(0, 10_001)):
            ms = frames_to_ms(n, rate, Rounding.ROUND_HALF_UP)
            assert ms_to_frames(ms, rate, Rounding.ROUND_HALF_UP) == n, (rate, n)


def test_roundtrip_frames_ms_frames_identity_huge_values():
    huge = [10_000, 86_400, 2_073_600, 10**6, 10**9, 10**9 + 7, 10**12]
    for rate in ALL_RATES:
        for n in huge:
            ms = frames_to_ms(n, rate, Rounding.ROUND_HALF_UP)
            assert ms_to_frames(ms, rate, Rounding.ROUND_HALF_UP) == n, (rate, n)


@settings(max_examples=400, deadline=None)
@given(n=st.integers(min_value=0, max_value=10**15),
       idx=st.integers(min_value=0, max_value=len(ALL_RATES) - 1))
def test_roundtrip_property_all_rates(n, idx):
    rate = ALL_RATES[idx]
    ms = frames_to_ms(n, rate, Rounding.ROUND_HALF_UP)
    assert ms_to_frames(ms, rate, Rounding.ROUND_HALF_UP) == n


def test_ms_to_frames_to_ms_is_not_identity_but_bounded():
    # The reverse trip CANNOT be identity — ms carries sub-frame precision a
    # frame index cannot. Concrete witness at 24fps, then a bounded sweep.
    assert ms_to_frames(41, FILM, Rounding.ROUND_HALF_UP) == 1
    assert frames_to_ms(1, FILM, Rounding.ROUND_HALF_UP) == 42  # 41 -> 42, != id
    for rate in ALL_RATES:
        half_frame_ms = int(1000 / (2 * rate.fps_float)) + 2
        for ms in range(0, 5000, 7):
            f = ms_to_frames(ms, rate, Rounding.ROUND_HALF_UP)
            back = frames_to_ms(f, rate, Rounding.ROUND_HALF_UP)
            assert abs(back - ms) <= half_frame_ms


def test_conversion_results_are_plain_ints():
    # No float ever leaks into a conversion result.
    for rate in ALL_RATES:
        assert type(frames_to_ms(123, rate)) is int
        assert type(ms_to_frames(5000, rate)) is int
        assert type(frames_to_samples(123, rate, 48000)) is int
        assert type(samples_to_frames(48000, rate, 48000)) is int


# --------------------------------------------------------------------------- #
# audio: frames <-> samples, 48k alignment (1001-periodic)                     #
# --------------------------------------------------------------------------- #

def test_samples_per_frame_exact_integer_rates_48k():
    assert samples_per_frame(FILM, 48000) == Fraction(2000)
    assert samples_per_frame(PAL, 48000) == Fraction(1920)
    assert samples_per_frame(NTSC30_INT, 48000) == Fraction(1600)
    assert samples_per_frame(P50, 48000) == Fraction(960)
    assert samples_per_frame(P60, 48000) == Fraction(800)


def test_samples_per_frame_ntsc_48k_is_1001_periodic():
    # 24000/1001 @ 48k is exactly 2002 samples/frame — integer EVERY frame.
    assert samples_per_frame(NTSC24, 48000) == Fraction(2002)
    assert sample_alignment_period(NTSC24, 48000) == 1
    assert frames_to_samples(1, NTSC24, 48000) == 2002
    # 30000/1001 @ 48k is 8008/5 — integer every 5 frames (8008 samples / 5f).
    assert samples_per_frame(NTSC30, 48000) == Fraction(8008, 5)
    assert sample_alignment_period(NTSC30, 48000) == 5
    assert frames_to_samples(5, NTSC30, 48000) == 8008
    # 60000/1001 @ 48k is 4004/5 — integer every 5 frames.
    assert samples_per_frame(NTSC60, 48000) == Fraction(4004, 5)
    assert sample_alignment_period(NTSC60, 48000) == 5
    assert frames_to_samples(5, NTSC60, 48000) == 4004


def test_samples_frames_roundtrip_on_alignment_period():
    for rate in ALL_RATES:
        period = sample_alignment_period(rate, 48000)
        for k in range(0, 50):
            n = k * period
            s = frames_to_samples(n, rate, 48000)
            assert samples_to_frames(s, rate, 48000) == n


# --------------------------------------------------------------------------- #
# 3. SMPTE timecode — NDF                                                       #
# --------------------------------------------------------------------------- #

def test_timecode_ndf_from_frames_basic_24():
    tc = Timecode.from_frames(0, FILM, drop_frame=False)
    assert (tc.hours, tc.minutes, tc.seconds, tc.frames) == (0, 0, 0, 0)
    assert str(tc) == "00:00:00:00"
    # frame 25 at 24fps -> 1 second + 1 frame
    tc = Timecode.from_frames(25, FILM, drop_frame=False)
    assert (tc.hours, tc.minutes, tc.seconds, tc.frames) == (0, 0, 1, 1)
    assert str(tc) == "00:00:01:01"
    # one hour of 24fps = 86400 frames -> 01:00:00:00
    assert str(Timecode.from_frames(86_400, FILM)) == "01:00:00:00"


def test_timecode_ndf_roundtrip_first_hour_boundaries():
    # every minute boundary of the first hour + every hour boundary of a day
    for rate in (FILM, PAL, NTSC30_INT, P50, P60):
        nominal = round(rate.fps_float)
        for minute in range(0, 60):
            n = minute * 60 * nominal
            tc = Timecode.from_frames(n, rate)
            assert tc.to_frames() == n
            assert (tc.minutes, tc.seconds, tc.frames) == (minute, 0, 0)
        for hour in range(0, 24):
            n = hour * 3600 * nominal
            tc = Timecode.from_frames(n, rate)
            assert tc.to_frames() == n
            assert (tc.hours, tc.minutes, tc.seconds, tc.frames) == (hour, 0, 0, 0)


def test_timecode_ndf_last_frame_of_day_and_wrap():
    for rate in (FILM, PAL, NTSC30_INT, P50, P60):
        nominal = round(rate.fps_float)
        frames_per_day = nominal * 3600 * 24
        last = frames_per_day - 1
        tc = Timecode.from_frames(last, rate)
        assert (tc.hours, tc.minutes, tc.seconds, tc.frames) == (23, 59, 59, nominal - 1)
        assert tc.to_frames() == last
        # one past the last frame wraps back to 00:00:00:00
        assert str(Timecode.from_frames(frames_per_day, rate)) == "00:00:00:00"


def test_timecode_ndf_full_roundtrip_sweep():
    # dense round-trip across the whole day (proves frames->tc->frames identity)
    rate = FILM
    frames_per_day = 24 * 3600 * 24
    for n in range(0, frames_per_day, 4999):
        assert Timecode.from_frames(n, rate).to_frames() == n
    assert Timecode.from_frames(frames_per_day - 1, rate).to_frames() == frames_per_day - 1


# --------------------------------------------------------------------------- #
# 3b. SMPTE timecode — drop-frame                                              #
# --------------------------------------------------------------------------- #

def test_drop_frame_illegal_on_non_ntsc_rates():
    for rate in (FILM, PAL, NTSC30_INT, P50, P60, NTSC24):
        with pytest.raises(ValueError):
            Timecode.from_frames(0, rate, drop_frame=True)
    # legal only for 30000/1001 and 60000/1001
    Timecode.from_frames(0, NTSC30, drop_frame=True)
    Timecode.from_frames(0, NTSC60, drop_frame=True)


def test_drop_frame_2997_classic_ten_minute_pin():
    # THE classic check: frame 17982 <-> 00:10:00;00 at 29.97 DF.
    tc = Timecode.from_frames(17_982, NTSC30, drop_frame=True)
    assert (tc.hours, tc.minutes, tc.seconds, tc.frames) == (0, 10, 0, 0)
    assert str(tc) == "00:10:00;00"
    assert tc.to_frames() == 17_982
    assert parse_timecode("00:10:00;00", NTSC30).to_frames() == 17_982


def test_drop_frame_2997_one_hour_vs_ndf_is_108_frames():
    # DF 01:00:00;00 sits at frame 107892; the NDF label 01:00:00:00 sits at
    # frame 108000. Difference = 108 frames/hour = 3.6s of label drift.
    df_hour = Timecode.from_frames(107_892, NTSC30, drop_frame=True)
    assert str(df_hour) == "01:00:00;00"
    assert df_hour.to_frames() == 107_892
    ndf_hour = Timecode.from_frames(108_000, NTSC30, drop_frame=False)
    assert str(ndf_hour) == "01:00:00:00"
    diff = ndf_hour.to_frames() - df_hour.to_frames()
    assert diff == 108
    # 108 frames at 30fps nominal = 3.6 seconds
    assert Fraction(diff, 30) == Fraction(36, 10)


def test_drop_frame_2997_minute_boundary_behaviour():
    # the nominal 1800th frame (index 1800) opens minute 1; labels ;00 and ;01
    # are dropped, so it is renumbered ...;02, while frame 1799 is ...;59;29.
    assert str(Timecode.from_frames(1_799, NTSC30, drop_frame=True)) == "00:00:59;29"
    tc = Timecode.from_frames(1_800, NTSC30, drop_frame=True)  # 30*60
    assert str(tc) == "00:01:00;02"
    assert tc.to_frames() == 1_800
    # minute 10 is a "kept" minute — no drop, so its :00 exists
    tc10 = Timecode.from_frames(17_982, NTSC30, drop_frame=True)
    assert str(tc10) == "00:10:00;00"


def test_drop_frame_dropped_labels_are_rejected_on_parse():
    # 00:01:00;00 and ;01 do not exist (dropped) — parsing must refuse them.
    for bad in ["00:01:00;00", "00:01:00;01", "00:02:00;01"]:
        with pytest.raises(ValueError):
            parse_timecode(bad, NTSC30)
    # but the kept-minute label ;00 at minute 10/20/... is valid
    assert parse_timecode("00:20:00;00", NTSC30).to_frames() == 2 * 17_982


def test_drop_frame_roundtrip_every_minute_first_hour():
    for drop_rate, nominal in ((NTSC30, 30), (NTSC60, 60)):
        for minute in range(0, 60):
            # pick a frame safely inside each minute, then round-trip
            base = minute * 60 * nominal
            for off in (5, nominal + 7, 2 * nominal + 1):
                n = base + off
                tc = Timecode.from_frames(n, drop_rate, drop_frame=True)
                assert tc.to_frames() == n, (minute, off)


def test_drop_frame_5994_hour_offset_is_216():
    # 59.94 DF drops 4/min except every 10th -> 216 frames/hour.
    df = Timecode.from_frames(215_784, NTSC60, drop_frame=True)
    assert str(df) == "01:00:00;00"
    ndf = Timecode.from_frames(216_000, NTSC60, drop_frame=False)
    assert str(ndf) == "01:00:00:00"
    assert ndf.to_frames() - df.to_frames() == 216


def test_timecode_parse_separator_selects_df():
    ndf = parse_timecode("01:00:00:00", FILM)
    assert ndf.drop_frame is False
    assert ndf.to_frames() == 86_400
    df = parse_timecode("01:00:00;02", NTSC30)
    assert df.drop_frame is True
    # ';' drop-frame syntax on a non-DF-legal rate is refused
    with pytest.raises(ValueError):
        parse_timecode("01:00:00;02", FILM)


def test_timecode_str_roundtrips_through_parse():
    for rate, df in ((FILM, False), (PAL, False), (NTSC30, True), (NTSC60, True)):
        for n in (0, 1, 999, 50_000, 123_456):
            tc = Timecode.from_frames(n, rate, drop_frame=df)
            assert parse_timecode(str(tc), rate).to_frames() == n


# --------------------------------------------------------------------------- #
# 4. Drift analysis — the honest bridge to today's int-fps ms grid             #
# --------------------------------------------------------------------------- #

def test_grid_drift_2397_material_on_24_grid():
    # 23.976 material laid on a 24fps grid drifts exactly 1 ms per 1000 ms.
    assert grid_drift_ms(1000, 24, NTSC24) == Fraction(1)
    # 1 hour -> 3.6s ; 2 hours -> 7.2s ; 8 hours -> 28.8s (all EXACT)
    assert grid_drift_ms(3_600_000, 24, NTSC24) == Fraction(3600)
    assert grid_drift_ms(7_200_000, 24, NTSC24) == Fraction(7200)
    assert grid_drift_ms(28_800_000, 24, NTSC24) == Fraction(28800)
    # sign is positive: the grid runs ahead of the slower material
    assert grid_drift_ms(7_200_000, 24, NTSC24) > 0


def test_grid_drift_2997_on_30_and_5994_on_60():
    # every NTSC-on-integer grid drifts the same 0.1% (1 ms / 1000 ms).
    assert grid_drift_ms(7_200_000, 30, NTSC30) == Fraction(7200)
    assert grid_drift_ms(7_200_000, 60, NTSC60) == Fraction(7200)
    assert grid_drift_ms(3_600_000, 30, NTSC30) == Fraction(3600)


def test_one_frame_drift_at_2397_on_24():
    # cumulative drift first reaches one (edit-grid) frame period at exactly
    # 1000 grid frames = 125000/3 ms ~= 41.667 s.
    d = one_frame_drift_at(24, NTSC24)
    assert d == Fraction(125_000, 3)
    assert d == pytest.approx(41_666.667, abs=1e-3)
    # sanity: the drift at that duration equals one 24fps frame period (1000/24)
    assert grid_drift_ms(d, 24, NTSC24) == Fraction(1000, 24)
    # and that is exactly 1000 frames on the grid
    assert d * 24 / 1000 == Fraction(1000)


def test_one_frame_drift_at_2997_on_30_and_5994_on_60():
    assert one_frame_drift_at(30, NTSC30) == Fraction(1_000_000, 30)
    assert one_frame_drift_at(60, NTSC60) == Fraction(1_000_000, 60)
    # ~33.333 s and ~16.667 s respectively
    assert one_frame_drift_at(30, NTSC30) == pytest.approx(33_333.333, abs=1e-3)
    assert one_frame_drift_at(60, NTSC60) == pytest.approx(16_666.667, abs=1e-3)


# --------------------------------------------------------------------------- #
# classify_rate — honest mapping of messy ffprobe values                       #
# --------------------------------------------------------------------------- #

def test_classify_rate_exact_inputs():
    assert classify_rate("24000/1001") == NTSC24
    assert classify_rate("30000/1001") == NTSC30
    assert classify_rate("24") == FILM
    assert classify_rate(25) == PAL
    assert classify_rate("25/1") == PAL


def test_classify_rate_float_and_decimal_ntsc():
    # the raw ffprobe float of an NTSC rate classifies to the exact fraction
    assert classify_rate(23.976023976023978) == NTSC24
    assert classify_rate("23.976023976023978") == NTSC24
    assert classify_rate(29.97) == NTSC30
    assert classify_rate(59.94) == NTSC60
    assert classify_rate(24.0) == FILM
    assert classify_rate(24.0).exact_int == 24


def test_classify_rate_unknown_is_refused_not_snapped():
    # honest: never force an unknown decimal onto a nearby standard.
    for bad in [23.5, 33.333, 12.0 + 0.345, "23.5", "weird", None, "0/0"]:
        assert classify_rate(bad) is UNKNOWN
    assert bool(UNKNOWN) is False


# --------------------------------------------------------------------------- #
# 5. VFR honesty helper                                                         #
# --------------------------------------------------------------------------- #

def test_rate_mode_cfr_vfr_unknown():
    assert rate_mode("24/1", "24/1") == "cfr"
    assert rate_mode("30000/1001", "30000/1001") == "cfr"
    # equal value, different spelling -> still cfr (exact fraction compare)
    assert rate_mode("24/1", "48000/2000") == "cfr"
    # differing rates -> VFR suspected
    assert rate_mode("30000/1001", "2997/100") == "vfr_suspected"
    assert rate_mode("24/1", "25/1") == "vfr_suspected"
    # missing / unparseable -> unknown
    assert rate_mode(None, "24/1") == "unknown"
    assert rate_mode("24/1", None) == "unknown"
    assert rate_mode("", "24/1") == "unknown"
    assert rate_mode("0/0", "24/1") == "unknown"


# --------------------------------------------------------------------------- #
# 6. RANGE_SEMANTICS module convention                                          #
# --------------------------------------------------------------------------- #

def test_range_semantics_constant():
    assert RANGE_SEMANTICS == "start_inclusive_end_exclusive"
    assert isinstance(tb.RANGE_SEMANTICS, str)
