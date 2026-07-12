# FP Loop W1 — FCPXML loop-bed materialization (render parity, pure/impure split)

## Goal

Materialize FCPXML audio loop beds with **render parity**. `media/render.py:1013-1016`
loops a bed with ffmpeg `-stream_loop -1 -i <src>` + `atrim=0:total_s` — REAL
whole-source repeats trimmed at the duration (verified in-tree). The FCPXML writer
now reproduces that structure: **N full whole-source passes + a trimmed tail**,
instead of the V1 honest omission.

## The pure/impure split (binding)

`compile_fcpxml` **stays pure**. It gains an optional
`loop_lengths: dict[str, int] | None = None` (source → probed natural ms):

* `None` (default) — today's behaviour **byte-identical**: loop beds are omitted
  with the verbatim in-band note.
* Provided — a `loop=True` clip whose source has a **positive** entry (and a
  resolvable positive `duration_ms`) materializes.

`export_fcpxml` (the IO layer, where containment checks already live) is the one
IO seam: `_probe_loop_lengths` probes every distinct loop source via the
**existing** `media.probe.probe_duration_ms` (the same prober render/compile use —
never a second one), builds the dict, and passes it in. A source that cannot be
resolved (out of project), fails to probe, or reports a non-positive/None duration
is **left out of the dict** → the pure omission path + note (never a fabricated
length). `probe_duration_ms` never raises.

## The placement math (cumulative boundaries — R2 discipline)

For a loop clip at absolute frame `F`, duration `D_ms`, probed natural `N_ms`:

```
K   = ceil(D_ms / N_ms)                                    # (D+N-1)//N, exact int
b_k = ms_to_frames(min(k·N_ms, D_ms), ROUND_HALF_UP)       # k = 0..K
pass k  ->  frames [F+b_k, F+b_{k+1}),  source start 0,  duration b_{k+1}-b_k
```

Because the boundaries are **cumulative** (each `b_k` is rounded from the exact
`k·N_ms`, not accumulated per-pass), the pass durations telescope to the clip's
exact whole-frame total with **zero per-pass rounding drift** — the same argument
R2 uses for the record spine. Each pass is a connected `<asset-clip>` on the bus
lane/role, placed through the SAME V1 helpers: parent chosen per **pass start
frame** via `_audio_parent_index`, `child.offset = in_frames[parent] + (F+b_k) −
offsets[parent]`. `start` is always `0s` (whole-source repeat). Gain rides EVERY
pass. The **first pass only** carries the materialization note; ducking/fade
approximation notes (if any) ride the first pass once.

### Hand-computed golden: D=5000ms, N=1900ms @24fps

```
K   = ceil(5000/1900) = 3
b_0 = ms_to_frames(min(0,5000))    = ms_to_frames(0)    = 0
b_1 = ms_to_frames(min(1900,5000)) = ms_to_frames(1900) = 45.6 -> HALF_UP -> 46
b_2 = ms_to_frames(min(3800,5000)) = ms_to_frames(3800) = 91.2 -> HALF_UP -> 91
b_3 = ms_to_frames(min(5700,5000)) = ms_to_frames(5000) = 120.0          -> 120
boundaries 0 / 46 / 91 / 120  ->  passes 46 + 45 + 29 = 120  (== clip frames EXACTLY)
```

Emitted (nested inside the 120-frame spine clip, `room.wav` natural 1900ms):

```xml
<asset-clip ref="r3" lane="-4" offset="0s"     name="room.wav" start="0s" duration="46/24s" audioRole="effects.ambient" />
<!-- MANJU: audio 'room.wav' (ambient) materialized loop (3 passes, natural 1900ms, render parity: -stream_loop) -->
<asset-clip ref="r3" lane="-4" offset="46/24s" name="room.wav" start="0s" duration="45/24s" audioRole="effects.ambient" />
<asset-clip ref="r3" lane="-4" offset="91/24s" name="room.wav" start="0s" duration="29/24s" audioRole="effects.ambient" />
```

The shared `<asset>` available range widens to the longest pass = the natural
source length in frames (`ms_to_frames(1900) = 46` → `duration="46/24s"`).

## Conform truth

`exporters/conform.py` — the `fcpxml` `audio_loops` row flips
**unsupported → approximated**: "materialized at export time as whole passes +
a trimmed tail from the PROBED natural length (render parity: -stream_loop);
... compile without probed lengths (the pure default) keeps the honest omission".
Only that one row changed.

## The orchestrator evolution point (STOP-AND-REPORT)

`tests/test_fp_fcpxml.py::test_fcpxml_conform_video_preserved_captions_dropped_audio_classified`
carries the 32a conform pin:

```python
assert "audio_loops" in unsupported          # line 477
```

With the row flipped to `approximated`, this reds:
`AssertionError: assert 'audio_loops' in set()`. Per the addendum this assertion
is the **orchestrator's tooth** — only the orchestrator may evolve it. It was NOT
edited by this loop. Everything else in the verification set is green.

## Files touched (hard cap respected)

* `src/manju/exporters/fcpxml.py` — `loop_lengths` param on `compile_fcpxml`;
  materialization in `_plan_audio` (reusing V1 parent/offset helpers); new `"loop"`
  emit branch; `_probe_loop_lengths` + `export_fcpxml` probe wiring; docstrings.
* `src/manju/exporters/conform.py` — the `fcpxml` `audio_loops` row only.
* `tests/test_fp_fcpxml_loops.py` — NEW (23 tests, red-first).

NOT touched: `exporters/fcpxml_import.py`, `cli.py` (parallel loop W2);
`tests/test_fp_fcpxml.py`, `tests/test_fp_fcpxml_audio.py` (T2 33 + V1 22, unedited).

## Verification

Red-first: the new suite ran **21 failed, 2 passed** against the unmodified
exporter (the 2 green were the None/omit-path tests, which pre-existed). After
implementation:

* `test_fp_fcpxml_loops.py` — **23 passed** (incl. the real-ffprobe e2e:
  a generated 1.9s wav probes to exactly 1900ms → K=3, 46/45/29 → 120 frames).
* `test_fp_fcpxml.py` — 1 expected red (line 477, above), all others green.
* `test_fp_fcpxml_audio.py`, `test_fp_conform.py`, `test_fp_ratemig1.py` — all green.
* Full set: **125 passed, 1 failed** (the expected orchestrator pin).
* Safety net (out of set): `test_fp_conformance.py` + `test_export_center.py` — 48 passed.

No new deps, no network, no git ops.
