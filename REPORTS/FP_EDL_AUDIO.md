# FP Loop Y1 — CMX3600 EDL declared A1/A2 audio subset

Roadmap §5 item 5 depth. S2 shipped a V-only CMX3600 cut list with Manju's four
audio buses recorded as the format's TOTALITY limit (audio `unsupported` in the
conform audit). This loop narrows that loss with a DECLARED, honest two-channel
subset — it contradicts nothing S2 said, it just stops throwing away what CMX
*can* carry.

## What shipped

`src/manju/exporters/edl.py` — the V block is now followed by a declared A1/A2
audio region (`_emit_audio` + helpers), appended only when a bus is populated:

- **VOICE bus → A1 events** (CMX channel token `A`); **MUSIC bus → A2 events**
  (token `A2`). The channel column is the same fixed column the V events use;
  `_event_line` gained a `channel` parameter (V events pass `V`, byte-identically).
- **Source TC is zero-based from `start_offset_ms`** — generated media's own
  timebase is zero-based, so its honest source in-point is `ms_to_frames(
  start_offset_ms)`, never dressed up as real tape TC. This is the exact S2
  precedent for video `source_in_ms`.
- **Record TC rides the SAME timebase machinery** as the picture:
  `start_frame + ms_to_frames(start_ms)`, `ms_to_frames` ROUND_HALF_UP at the
  edit rate, DROP/NON-DROP inherited from the same header logic. Source length
  reuses the record frame count (the cut-list handles-match invariant).
- **Single monotone event numbering across V → A1 → A2** (blocked-by-track).
- **sfx + ambient are NOT squeezed into channels** — the classic CMX3600 form
  has no third/fourth stereo pair, so every sfx/ambient clip is an in-band
  `* MANJU:` omission note naming its bus/source/window.
- **gain / fades → per-clip `* MANJU:` note when nonzero** (never silent); not a
  cut-list primitive, the level/ramp rides the mix.
- **loop=True / `duration_ms is None` → omission note, NEVER a fabricated event**
  (a looped bed is materialized by repetition at render time and has no honest
  single-event source window; an open clip has no frame-exact out-point).
- **ducking** has no CMX primitive at all → stays out of scope (conform audit,
  no per-clip note).

`tests/test_fp_edl_audio.py` (NEW; S2's `test_fp_edl.py` untouched) — 17 tests,
red-first, golden derived from first principles.

## Event-ordering ruling: BLOCKED-BY-TRACK, single monotone numbering

Audio events follow the **entire** V block (V events 1..Nv, then A1, then A2),
one monotonically increasing event number across all three, omitted clips
consuming no number. Chosen over interleave-by-record-TC because:

1. **Real CMX3600 practice** organizes machine-generated lists in track-grouped
   blocks; blocked audio is a widely-parseable, unambiguous form when the audio
   channel token differs from the picture track.
2. **Byte-stability with S2**: appending audio *after* the V block keeps every V
   event's number and bytes exactly as S2 pinned them. Interleaving by record TC
   would renumber V events whenever an audio event sorts before a video cut,
   breaking S2's frozen golden pins.
3. **Determinism / auditability**: a fixed bus order (voice→A1, then music→A2)
   with a monotone counter needs no cross-track tie-break for coincident record
   TCs and diffs cleanly.
4. **Single sequence, not per-track restart**: CMX event numbers are a
   document-wide reference; one ascending run keeps every number unique.

## Golden head (V + A1 + A2)

```
TITLE:   AV DEMO
FCM: NON-DROP FRAME
001  TAKEALPH V    C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00
* FROM CLIP NAME: TAKEALPHA0001
002  TAKEBETA V    C        00:00:00:00 00:00:02:00 01:00:02:00 01:00:04:00
* FROM CLIP NAME: TAKEBETA0002
003  LINE1    A    C        00:00:00:12 00:00:02:00 01:00:00:00 01:00:01:12
* FROM CLIP NAME: media/vo/line1.wav
004  LINE2    A    C        00:00:00:00 00:00:01:00 01:00:02:00 01:00:03:00
* FROM CLIP NAME: media/vo/line2.wav
* MANJU: voice clip 'media/vo/line2.wav' gain -3dB not expressible in bare CMX3600 (a mix level, not a cut-list primitive)
005  BED      A2   C        00:00:00:00 00:00:04:00 01:00:00:00 01:00:04:00
* FROM CLIP NAME: media/music/bed.mp3
* MANJU: music clip 'media/music/bed.mp3' fades (in 250ms / out 500ms) not expressible in bare CMX3600
* MANJU: music clip 'media/music/loopbed.mp3' [0..8000ms) loop=True omitted - CMX3600 has no loop primitive; the render materializes the bed by repetition (no honest single-event source window)
* MANJU: sfx clip 'media/sfx/whoosh.wav' [1000..1300ms) omitted - CMX3600 classic form carries A1/A2 only (voice->A1, music->A2); sfx/ambient have no channel
* MANJU: ambient clip 'media/amb/rain.wav' [0..4000ms) omitted - CMX3600 classic form carries A1/A2 only (voice->A1, music->A2); sfx/ambient have no channel
```

## Conform rows — HANDOFF (orchestrator applies; I do not touch conform.py)

A parallel loop owns `exporters/conform.py`, so the edl audio classification is
handed off as row tuples for the orchestrator to apply (registry pattern). See
`scratchpad/FP_Y1_RESULTS.md` for the exact `(category, detail, where)` tuples.

Summary: `audio_tracks` unsupported→**approximated** (declared A1/A2 subset;
sfx/ambient omitted with notes); `audio_in_points` unsupported→**approximated**
(A1/A2 carry zero-based source TC; sfx/ambient omitted); `audio_gain`,
`audio_fade_in`, `audio_fade_out` unsupported→**approximated** (notes-only);
`audio_loops` stays **unsupported** (omission note, no materialization here);
`ducking` stays **unsupported** (no primitive); `clip_volume` unchanged
(**unsupported** — video own-audio is not a bus).

**Pin reconciliation the orchestrator must own:** applying `audio_tracks →
approximated` collides with S2's frozen assertion
`test_edl_conform_audio_is_unsupported_video_is_preserved` (`assert
"audio_tracks" in unsupported`). The completeness meta-pin
(`test_edl_conform_every_feature_classified_exactly_once`) stays green (union
unchanged, only re-bucketed). In THIS loop's delivery conform.py is untouched,
so all 28 S2 tests stay green; the one assertion above must be reconciled by
whoever applies the conform rows.

## Verification

- `tests/test_fp_edl_audio.py` — 17 passed (red-first: 14 failed before the
  emission existed).
- `tests/test_fp_edl.py` — 28 passed, UNEDITED.
- `tests/test_fp_ratemig1.py` — 22 passed.
- Also confirmed green (no regressions from the edl.py change):
  `test_fp_conform.py` (26), `test_fp_ratemig4.py` (20).

(`tests/test_fp_edl_import.py` fails at collection on a missing `edl_import`
module — a *different* in-flight loop's red-first, not this loop's and not in
this loop's file set.)
