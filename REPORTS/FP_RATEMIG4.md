# FP R4 — Rational EXPORT truth + audio-master sample facts (R3 folded in)

**Prereq landed (R2, HEAD `e5a9052`):** the opt-in rational build spine —
`VideoClip.duration_frames` (additive, dropped-when-None), the timeline
`rate_echo` (serialized as `edit_rate`), the compiler's cumulative-boundary
frame grid, and the native `-r num/den` render. R4 makes the **exports** tell
that rational truth, with **int-project byte-identity** as the non-negotiable
product.

## The R3-collapse honesty note

The originally-planned R3 (burned captions + audio inherit rational correctness)
**collapsed to thin remnants**: R2's boundary-exact timeline already makes burned
captions and audio ride the same cumulative whole-frame boundaries the picture
does, so there was no separate correctness work to do — those surfaces inherit
it. Its one real remnant — **audio-master sample facts** — rides here as R4 item
3. Nothing was skipped silently; the collapse is recorded so the R-track's shape
stays auditable.

## Files touched (only these)

| File | Change |
|---|---|
| `exporters/otio.py` | rational path: exact integer-frame RationalTime values at a float64 rate; int path byte-identical (threaded `_Times`, `rate=None` ⇒ today's exact code) |
| `exporters/conform.py` | `_frame_drift` rational-awareness (all-zero **by construction**); µs-carrier honest note rows; refreshed OTIO `where` line cites |
| `exporters/edl.py` | `_Placed` consumes `duration_frames` for exact record/source TC; ms fallback keeps S2 golden bytes |
| `media/masters.py` | additive `duration_samples`/`sample_basis` per artifact row — **rational-echo projects only** (int `masters.json` + `index_digest` byte-identical) |
| `tests/test_fp_ratemig4.py` | red-first suite (20 tests) |
| `REPORTS/FP_RATEMIG4.md` | this report |

R2's landed files (`compiler`/`models`/`render`/`graph`), all S1/S2 pins, FIX-B,
compat, and the ratemig1/2 suites are **untouched**. No `CONTRACTS`/`DECISIONS`/
`README`. No new deps. No commit/push.

---

## 1. OTIO — rational truth (item 1)

The dispatch is the R2 echo, read defensively (`_edit_rate_of` → `Rate | None`);
a whole-number or absent echo ⇒ `None` ⇒ the byte-identical int path.

**Rate = OTIO's float64 convention, pinned exactly:**

| Rational rate | `RationalTime.rate` (exact IEEE-754 double) |
|---|---|
| `24000/1001` | **`23.976023976023978`** (`Rate.fps_float`, `== 24000/1001`) |
| `30000/1001` | `29.97002997002997` |
| `60000/1001` | `59.94005994005994` |

**Value = INTEGER whole frames, never `ms × fps` float:**

* video **duration** = the compiler's `VideoClip.duration_frames` (the exact
  frame truth the ms grid cannot hold for the 1001 family);
* **starts telescope** the same way the compiler's cumulative boundary does —
  a virtual trim's `source_in_ms` becomes `ms_to_frames(in_ms)` whole frames
  (1001 ms → 24 frames), `available_range` widens to in-point + window (24+48=72);
* audio (no `duration_frames`) uses `ms_to_frames(duration_ms)` — still an exact
  integer via exact-Fraction rounding, **no float**;
* `global_start_time` is integer `0`.

Every emitted `value` is a genuine Python `int` (serializes `48`, not `48.0`);
every `rate` is the float64 above. The exact `{num, den}` is additionally
surfaced in `metadata.manju.edit_rate` (rational-only, additive).

**Int path proof:** `round(2000*24/1000, 6) == 48.0` at `rate: 24.0` (float),
byte-for-byte as before; `test_export_sfx_ambient` / `test_export_containment` /
`test_export_center` / `test_audio_edit` / `test_dr01_binding_pins` all green.

## 2. conform — the closing pin (item 2)

An R2-compiled + R4-exported project's `frame_drift` is **all-zero BY
CONSTRUCTION**: the exporter writes exact whole frames, so the drift walk over
the frame truth (video telescopes `duration_frames`, audio/captions
`ms_to_frames`) yields only integer frame indices — residual `0`, computed (not
fabricated). The rational block adds `grid: "rational"`, `edit_rate:
"24000/1001"`, `all_zero_by_construction: true`; the **int block is byte-identical**
(same seven keys, same integer-grid math).

* **Source-rate mismatch** now compares against the *exact* rational fraction, so
  a `24000/1001` source on a `24000/1001` edit grid is correctly the same clock
  (no invented drift row).
* **µs carriers** (`jianying`, `native_draft`) get **one honest note** per
  rational report: their `ms×1000` stream approximates the 1001 grid within
  `≤½ms` per boundary (timebase's cumulative-boundary bound) — recorded, never
  silent. The frame-truth drift walk itself stays all-zero-by-construction.
* **ms-native captions** (`srt_ass`, `ttml`) stay silent — no frame grid; the
  existing `_NOT_TIME_BEARING` reason IS the "why no row" statement (unchanged).

## 3. masters — sample facts, the R3 remnant (item 3 + the RULING)

`masters.json`'s `index_digest = hash_value({"a": artifacts, "t": tdigest})`
covers the **artifact rows**. Adding row keys shifts the digest for *every*
project — **not acceptable** for int byte-identity. Therefore the RULING:

> `duration_samples` / `sample_basis` are emitted **ONLY for rational-echo
> projects**. An int/whole-number project's rows gain nothing → its
> `masters.json` (and `index_digest`) is **byte-identical**, pinned.

A rational project's row carries:

* `sample_basis: "frames"` + exact `duration_samples =
  frames_to_samples(frames, rate, sr)` when the render length maps to a whole
  frame — **2002 samples/frame** at `24000/1001` & 48 kHz (48 frames = **96096**
  samples, verified against a real render);
* else `sample_basis: "ms"` + `round(ms × sr / 1000)` — honest provenance, the
  two bases are **never mixed silently**.

Index-level `sample_rate` (48000) already existed — audited, unchanged. Facts
only: nothing about the rendered WAV bytes changed. `test_closeout_c5` (A01–A06)
+ `test_c18_dialogue_masters` stay green.

## 4. EDL — consuming `duration_frames` (item 4)

S2 left `duration_frames` unconsumed. `_Placed` now takes the exact whole-frame
length from `duration_frames` when present (record/source spans frame-exact);
absent (every int project → the field is `None`) it falls back to the ms→frame
telescoping difference — **byte-identical to S2's golden pins**. Record-IN still
telescopes via `ms_to_frames(start_ms)`, which on an R2-compiled timeline
recovers the exact cumulative frame (`start_ms == frames_to_ms(cum)` and
`ms_to_frames∘frames_to_ms` is the identity), so continuity (event N out ==
event N+1 in) stays exact on both paths. Proven by a constructed disagreement
(`duration_ms=1000` ⇒ ms-fallback 24 frames, but `duration_frames=48` ⇒ EDL uses
48). S2's whole `test_fp_edl` golden suite green.

## 5. Tests (item 5) — `tests/test_fp_ratemig4.py`, 20 red-first

golden int OTIO byte pin · rational OTIO integer-values + float64 rate pins ·
conform all-zero-by-construction on a real R2 compile→export · ms-carrier note
rows (+ int has none, + ms-native captions silent) · masters int byte-identity +
rational 2002/frame facts (pure helper + ffmpeg render) · EDL rational record-TC
exactness + int ms-fallback unchanged · `plan_roundtrip` coherence (no-op + a
frame trim) on a rational export.

## Targeted test evidence (no full suite)

| Suite(s) | Result |
|---|---|
| `test_fp_ratemig4` | **20 passed** |
| `test_fp_ratemig1` + `ratemig2` + `fp_compat` | passed (inviolable — untouched) |
| `test_fp_conform` + `fp_edl` + `fp_conformance` + `fp_ttml` | passed |
| `test_export_containment/_sfx_ambient/_center` + `audio_edit` + `native_draft` + `openclap_export` + `transitions_looks` | passed |
| `test_closeout_c5` + `c18_dialogue_masters` (ffmpeg) | passed |
| `test_c20b_corpus` + `dr01_binding_pins` | passed |

Consolidated de-duplicated run of all suites above: **352 passed, 7 skipped, 0
failed** (skips are ffmpeg/optional-dep conditionals).

## Deviations

None material. The rational conform `frame_drift` block gains three additive keys
(`grid`, `edit_rate`, `all_zero_by_construction`) present only on the rational
path — the int block is byte-identical. The OTIO `where` line citations in
`conform.py` were refreshed to the post-edit line ranges (the module contract
requires accurate cites); classifications/categories are unchanged.
