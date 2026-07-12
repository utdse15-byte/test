# FP Loop B — Rational Time / SMPTE Timecode / Drift Foundation

**Deliverable:** `src/manju/core/timebase.py` (pure, stdlib-only) +
`tests/test_fp_timebase.py` (§15.1 fixture matrix).
**Roadmap anchor:** §5.1 时间与帧 · §15.1 Fixture 矩阵 (时间) · P0 "Rational
time/timecode" (§17).
**Boundary honoured:** foundation only. Zero engine changes. `ProjectConfig.fps`
(int), `media/probe.py` (float fps), and `timeline/compiler.snap_to_frame_grid`
(ms × int-fps grid) are **untouched**. Nothing in the engine imports this module
this loop; `timebase` depends on nothing but the standard library
(`fractions`, `math`, `re`, `dataclasses`, `enum`). The int→rational edit-rate
change remains a governed FUTURE migration.

All conversion math is exact integer arithmetic via `fractions.Fraction`. No
`float` appears on any conversion path; floats are confined to two labelled
display/parse surfaces — `Rate.fps_float` and the *input* of `classify_rate` /
`rate_mode`.

---

## 1. API summary

### `Rate` — exact rational frames/second
- `Rate.from_fraction(num, den=1)` — normalised to lowest terms; non-positive /
  zero-denominator → `ValueError`.
- `Rate.parse(text)` — STRICT. Accepts `"24000/1001"`, `"24"`, exact-integer
  decimals (`"24.0"`), and the fixed NTSC decimal-alias table
  (`23.976`/`23.98`→24000/1001, `29.97`→30000/1001, `47.952`→48000/1001,
  `59.94`→60000/1001, `119.88`→120000/1001). Any other decimal is **refused**
  (`"23.976023976"`, `"23.5"` → `ValueError`). No float guessing.
- Properties: `.fraction`, `.numerator`, `.denominator`, `.fps_float`
  (display only), `.is_ntsc` (denominator a multiple of 1001), `.exact_int`
  (int or `None`), `.nominal_int` (fps rounded → timecode label rate).
- Value semantics: `__eq__`/`__hash__` canonical (`24000/1001 == 48000/2002`).

### Conversions — explicit `Rounding` (FLOOR / ROUND_HALF_UP / CEIL)
- `frames_to_ms(frames, rate, rounding=ROUND_HALF_UP)`
- `ms_to_frames(ms, rate, rounding=ROUND_HALF_UP)`
- `frames_to_samples(frames, rate, sample_rate, rounding=ROUND_HALF_UP)`
- `samples_to_frames(samples, rate, sample_rate, rounding=ROUND_HALF_UP)`
- `samples_per_frame(rate, sample_rate) -> Fraction` (exact)
- `sample_alignment_period(rate, sample_rate) -> int` (frames until whole samples)
- `ROUND_HALF_UP` = `floor(x + 1/2)` — ties toward +∞ (away from zero for the
  non-negative durations here); **not** Python banker's rounding.

### `Timecode` — SMPTE value object (frozen dataclass, bound to `Rate` + drop flag)
- `Timecode.from_frames(frame, rate, drop_frame=False)` — 0-based, wraps at 24 h.
- `.to_frames()` — inverse; validates field ranges, DF-legality, and rejects
  dropped-number labels.
- `parse_timecode(text, rate)` — `';'` (or `'.'`) before frames ⇒ drop-frame,
  `':'` ⇒ non-drop.
- `str()` renders `HH:MM:SS:FF` (NDF) / `HH:MM:SS;FF` (DF).
- Drop-frame legal **only** for 30000/1001 and 60000/1001; requested elsewhere →
  `ValueError`. Standard rule: drop 2 (30) / 4 (60) frame numbers at each minute
  except every 10th.

### Drift analysis — honest bridge to today's int-fps ms grid
- `grid_drift_ms(duration_ms, edit_fps_int, true_rate) -> Fraction` — signed,
  exact = `duration_ms * (edit_fps_int − true_rate) / true_rate`.
- `one_frame_drift_at(edit_fps_int, true_rate) -> Fraction` — grid duration (ms)
  at which drift first reaches one edit-grid frame period.
- `classify_rate(value) -> Rate | UNKNOWN` — exact inputs (int, `"num/den"`,
  integer string) parse straight through; decimals/floats classify only within
  a tight ±0.005 tolerance of one known standard, else `UNKNOWN`. Never snaps an
  unknown decimal onto a nearby standard.
- `UNKNOWN` — falsy sentinel.

### VFR honesty + convention
- `rate_mode(r_frame_rate, avg_frame_rate) -> "cfr" | "vfr_suspected" | "unknown"`
  — exact fraction compare; equal ⇒ cfr, differ ⇒ vfr_suspected, missing/`0/0`/
  `N/A`/unparseable ⇒ unknown. Never probes.
- `RANGE_SEMANTICS = "start_inclusive_end_exclusive"` — module-wide convention.

---

## 2. Pinned drift numbers (exact)

### 23.976 (24000/1001) material on a 24 fps grid
- **Drift rate:** exactly **1 ms per 1000 ms** (0.1 %). `grid_drift_ms` positive
  — the integer grid runs *ahead* of the slower material.
- **One frame of drift** (edit-grid frame = 1000/24 ms): at exactly
  **`125000/3` ms ≈ 41 666.67 ms ≈ 41.667 s** — i.e. **1000 grid frames**.
- **2 h** (7 200 000 ms): drift = **7200 ms = 7.2 s** (= 172.8 frames).
- **8 h** (28 800 000 ms): drift = **28 800 ms = 28.8 s**.
- **1 h**: 3600 ms = 3.6 s.

### 29.97 (30000/1001) on 30 fps grid · 59.94 (60000/1001) on 60 fps grid
- Same **0.1 %** drift → **2 h = 7200 ms = 7.2 s** for both.
- One frame of drift: **29.97-on-30 → `1000000/30` ms ≈ 33 333.33 ms ≈ 33.333 s**;
  **59.94-on-60 → `1000000/60` ms ≈ 16 666.67 ms ≈ 16.667 s** (each = 1000 grid
  frames).

### 29.97 drop-frame timecode pins
- Classic: frame **17982 ↔ `00:10:00;00`**.
- One hour: DF `01:00:00;00` ↔ frame **107 892**; NDF `01:00:00:00` ↔ frame
  **108 000**; difference **108 frames/hour = 3.6 s** label drift.
- Minute boundary: frame 1799 = `00:00:59;29`, frame 1800 = `00:01:00;02`
  (labels ;00/;01 dropped); 10th-minute label `;00` is kept.

### 59.94 drop-frame timecode pin
- DF `01:00:00;00` ↔ frame **215 784**; NDF `01:00:00:00` ↔ **216 000**;
  difference **216 frames/hour = 3.6 s**.

### Audio 48 kHz sample alignment (1001-periodic)
- 24000/1001 @ 48 kHz → **exactly 2002 samples/frame** (aligns *every* frame,
  period 1).
- 30000/1001 @ 48 kHz → **8008/5 samples/frame** (period **5** frames = 8008
  samples).
- 60000/1001 @ 48 kHz → **4004/5 samples/frame** (period **5** frames = 4004
  samples).
- Integer rates @ 48 kHz exact: 24→2000, 25→1920, 30→1600, 50→960, 60→800.

---

## 3. Invariants proven + the one bound that cannot hold

**Proven identity (ROUND_HALF_UP):** `ms_to_frames(frames_to_ms(n)) == n` for
every supported rate (24, 24000/1001, 25, 30000/1001, 30, 50, 60, 60000/1001),
across frames 0..10 000, a set of huge values (up to 1e12), and a Hypothesis
sweep to 1e15. Holds because every supported rate ≤ 60 fps keeps the per-frame
rounding residual well under ½ frame.

**Timecode identity:** `from_frames(n).to_frames() == n` across day-length
ranges — every minute boundary of the first hour, every hour boundary, the last
frame of the day, and a dense full-day sweep (NDF); every-minute round-trip for
the first hour at 29.97 DF and 59.94 DF.

**Sample-alignment exactness:** `samples_to_frames(frames_to_samples(n)) == n`
whenever `n` is a multiple of `sample_alignment_period`.

**The bound that CANNOT hold (stated, not pretended):** `frames_to_ms` is exact
only when `1000/fps ∈ ℤ` — i.e. **25 fps (40 ms) and 50 fps (20 ms)**. For 24,
30, 60 and the *entire* NTSC 1001 family a frame boundary never lands on a whole
millisecond, so `frames_to_ms` carries **up to ½ ms of rounding error per call**
(> 0 for some frames; tested `≤ 1/2` and non-zero). The frame index still
round-trips, but the millisecond *timestamps* drift — exactly the error
`grid_drift_ms` quantifies. Correspondingly the reverse trip
`frames_to_ms(ms_to_frames(ms))` is **deliberately not** the identity (a frame
index cannot carry sub-frame ms); tested residual ≤ half a frame period.

---

## 4. Test count & regression

- `tests/test_fp_timebase.py`: **40 tests, all green** (1 Hypothesis property +
  39 example/matrix tests). Written red-first (absent-module import error) before
  implementation.
- Full suite: baseline 3168 passed / 13 skipped / 0 failed → after this loop
  **3208 passed / 13 skipped / 0 failed** (baseline + 40; zero cross-impact —
  the module is import-isolated from the engine).

## 5. Deviations from the addendum

- None material. Judgement calls, all documented in-module:
  - `one_frame_drift_at` measures against the **edit-grid** frame period
    (1000/edit_fps) rather than the true-rate period; both differ by 0.1 % and
    the edit-grid choice is consistent with "drift vs the grid".
  - `classify_rate` uses a ±0.005 absolute tolerance (the closest standard pair,
    23.976 vs 24.0, is 0.024 apart, so this is unambiguous) and refuses to
    `UNKNOWN` on any ambiguous or unrecognised decimal.
  - Added two helpers beyond the named API — `samples_per_frame` and
    `sample_alignment_period` — to express the 1001-periodic audio fact exactly;
    `Rate.nominal_int` exposes the timecode label rate. All additive.
  - `parse_timecode` also accepts `'.'` as a drop-frame separator (some tools
    emit it); `';'` remains the primary.
