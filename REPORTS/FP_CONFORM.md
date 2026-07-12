# FP_CONFORM — manju.conform-loss/v1 (Loop D completion note)

Honest per-export loss reports + exact one-frame drift detection for the
NLE/exchange exporters (roadmap §6.1/§6.2, §15.6). Red-first: the fixture
matrix in `tests/test_fp_conform.py` was written and run RED (import error)
before `src/manju/exporters/conform.py` existed.

## What shipped

- `src/manju/exporters/conform.py` (new, library-only — no CLI this loop):
  - `conform_loss_report(project, target, timeline, exported, *, source_rates=None)`
    → the `manju.conform-loss/v1` doc: `{schema, target, preserved,
    approximated, dropped, unsupported, frame_drift, reimport, notes}` with
    STRUCTURED rows `{feature, detail, where}` (exporter code line-cited).
  - `timeline_feature_inventory(timeline)` — feature inventory from the
    TIMELINE side; an absent feature yields NO row. Kenburns/motion,
    speed/retime and markers are not representable in today's `Timeline`
    model, so they can never appear (honest absence, documented).
  - `write_conform_report(project, doc)` → `reports/conform/<target>_<digest>.json`
    — content-addressed (sha256 over doc facts, no wall-clock), deletable,
    tamper-evident (`read_conform_report` rejects any fact/digest edit),
    byte-deterministic, grep-pinned as never a build input.
  - `reimport_changes(project, edited_path)` — thin summary over the existing
    `build/roundtrip.plan_roundtrip` (zero new diff logic) →
    `{kind, baseline, truth_moved, changed, unchanged, unknown}` for the doc's
    `reimport` block.
- `tests/test_fp_conform.py` — 26 tests, all green; exporter artifact bytes
  asserted unchanged by report derivation (conform only READS).

## Per-target loss tables (as implemented — audited, line-cited)

Features are fine-grained so each maps to exactly ONE category per target;
the suite pins inventory == preserved∪approximated∪dropped∪unsupported and
pairwise disjointness. A feature absent from the timeline emits no row.

### otio (exporters/otio.py)

| feature | category | where / why |
|---|---|---|
| video_clips | preserved | Clip.1 + source_range/available_range (otio.py:88-115,158) |
| video_in_points | preserved | source_in_ms → source_range.start_time (otio.py:100-114) |
| audio_in_points | preserved | start_offset_ms → audio source_range start (otio.py:127-137) |
| transitions | approximated | metadata.manju.transition_out only, no OTIO Transition; round-trippable (otio.py:97-99) |
| clip_volume | approximated | mute/gain in metadata.manju only; round-trippable (otio.py:90-96) |
| audio_fade_in | approximated | metadata.manju only (otio.py:128-132) |
| audio_loops | approximated | no OTIO loop semantics; metadata flag (otio.py:121-126) |
| audio_tracks | approximated | 4 buses flatten onto ONE Audio track; bus id in metadata.manju.track (otio.py:124,158-171) |
| captions | dropped | never reads tracks.captions — no text track (otio.py:158-172) |
| overlays | dropped | never reads tracks.overlay (otio.py:153-202) |
| audio_gain | dropped | AudioClip.gain_db never written, not even metadata (otio.py:118-140) |
| audio_fade_out | dropped | never written (otio.py:118-140) |
| ducking | dropped | never written (otio.py:118-140) |

### jianying (M1 skeleton, exporters/jianying.py)

| feature | category | where / why |
|---|---|---|
| video_clips | preserved | material+segment, µs timeranges (jianying.py:110-160) |
| video_in_points | preserved | source_timerange.start (jianying.py:119-149) |
| audio_in_points | preserved | source_timerange.start (jianying.py:193-195) |
| captions | preserved | text materials + text-track segments + manju stamp (jianying.py:214-244,262) |
| audio_tracks | preserved | one draft track per bus (jianying.py:246-262) |
| transitions | approximated | manju_v stamp only, no native transition segment; round-trippable via `manju roundtrip` (jianying.py:124-132,150 + roundtrip.py:604-646) |
| clip_volume | approximated | native linear volume/muted + exact dB in manju_v; round-trippable (jianying.py:127-130,152-159) |
| audio_gain | approximated | linear volume field only; NOT round-tripped (jianying.py:197-201) |
| overlays | dropped | never read (jianying.py:98-280) |
| audio_fade_in/out | dropped | "fades live in the render, not the draft schema" (jianying.py:197-199) |
| ducking | dropped | never written (jianying.py:175-211) |
| audio_loops | dropped | no loop primitive; flag not stamped (jianying.py:163-169) |

### native_draft (pyJianYingDraft/pycapcut, exporters/native_draft.py)

| feature | category | where / why |
|---|---|---|
| video_clips | preserved | VideoSegment per clip; NOTE short-take padding becomes implicit slow-down (native_draft.py:47-75) |
| video_in_points | preserved | source_timerange seek (native_draft.py:50-61) |
| audio_in_points | preserved | guarded kwarg — old libs silently fall back to 0 (native_draft.py:85-104) |
| captions | preserved | TextSegment per cue; speaker not carried (native_draft.py:147-155) |
| audio_tracks | preserved | per-bus lanes; SFX spread across sfx_N lanes (native_draft.py:106-145) |
| clip_volume | approximated | dB→linear kwarg, guarded, no stamps ⇒ NOT round-trippable (native_draft.py:62-74) |
| audio_gain | approximated | dB→linear volume (native_draft.py:90-104) |
| transitions | dropped | adapter never reads transition_out (native_draft.py:35-156) |
| overlays | dropped | never read (native_draft.py:35-156) |
| audio_fade_in/out, ducking | dropped | render-only, never passed to the library (native_draft.py:77-104) |
| audio_loops | dropped | bed trimmed to source, plays ONCE (native_draft.py:77-104) |

### srt_ass (exporters/srt_ass.py)

| feature | category | where / why |
|---|---|---|
| captions | preserved | cue text/times verbatim, ms-native; ASS styled+injection-neutralized (srt_ass.py:142-177,216-282) |
| EVERY other present feature | unsupported | caption-only exit — the report says a caption export is caption-only, never silently "fine" (srt_ass.py:1-12) |

### openclap (exporters/openclap/exporter.py)

| feature | category | where / why |
|---|---|---|
| video_clips | preserved | standard VIDEO segments + provenance (exporter.py:122-153) |
| audio_tracks | preserved | deterministic track numbers 0–4 per bus (exporter.py:47-52,280-298) |
| video_in_points | approximated | sourceInMs in x-manju only (exporter.py:136-137) |
| overlays | approximated | meta x-manju.overlays only (exporter.py:251-252) |
| captions | approximated | meta x-manju.captions only (exporter.py:249-250) |
| clip_volume | approximated | sourceMute/sourceGainDb in x-manju (exporter.py:132-135) |
| audio_gain | approximated | standard outputGain (linear) + exact dB in x-manju (exporter.py:167-168,184-185) |
| audio_loops | approximated | x-manju.loop (exporter.py:169-170) |
| audio_in_points | dropped | start_offset_ms never read (exporter.py:156-187) |
| transitions | dropped | never read; no TRANSITION segments emitted (exporter.py:122-153) |
| audio_fade_in/out, ducking | dropped | never read (exporter.py:156-187) |

## frame_drift pins (exact — core/timebase Fractions, no float)

The hazard is real: `exporters/otio.py:39-44` writes RationalTime values as
`round(ms*fps/1000, 6)` at a float rate — off-grid boundaries become silently
rounded FRACTIONAL frames.

- Off-grid detector: 41 ms boundary at 24 fps ⇒ `frames_exact = 123/125`
  (0.984), `residual_frames = 2/125` (0.016), `cumulative_max_residual = 2/125`.
- Grid-snapped fixture (all boundaries multiples of 125 ms at 24 fps) ⇒
  `all_zero: true`, `off_grid: []` — the current honest expectation, stated,
  never fabricated.
- NTSC source on int grid (source_rates param): 23.976 (24000/1001) material
  on a 24 grid ⇒ `grid_drift_ms(2000 ms) = 2` ms exactly (the 0.1 % law),
  `one_frame_drift_at = 125000/3` ms (≈41.667 s) exactly; a 60 s clip drifts
  60 ms ⇒ `exceeds_one_frame: true`. Unclassifiable probe values (e.g. 23.5)
  yield an honest `"unknown"` row with NO fabricated numbers.
- srt_ass: `checked: false` — ms-native caption documents, fps never enters
  (srt_ass.py:45-70); there is no frame grid to drift against.
- Boundary sets per target: otio/openclap = video+audio buses; jianying/
  native_draft additionally include caption segments (pinned: 10 vs 14
  boundaries on the shared fixture).

## Reimport block (reuse)

`reimport_changes` calls `build/roundtrip.plan_roundtrip` verbatim and only
summarizes its rows: diffed rows → `changed`, the explicit `no_changes` row →
`unchanged`, unmatched/actionless rows → `unknown`. Pinned: an unedited OTIO
export reports `unchanged=[no_changes]`; an in-point edit (125 ms → 1125 ms)
reports a `trim` row in `changed`. Zero new diff logic.

## Meta-pins (no silent degradation, §6.2)

- Every exporter module under `src/manju/exporters/` (+`openclap/`) is either
  a key of `TARGET_CLASSIFIERS` or listed in `UNSUPPORTED_TARGETS` with a
  reason — exporter #6 fails `tests/test_fp_conform.py` until classified.
- `reports/conform` is referenced by no src module except
  `exporters/conform.py` (grep-pin) — a deletable derived output, never a
  build input.

## Registry row (for the orchestrator to apply — CONTRACTS.yaml not touched)

```yaml
  - id: manju.conform-loss/v1
    kind: schema
    owner: manju.exporters.conform
    status: experimental
    latest_version: 1
    read_older: true
    write_older: false
    notes: >-
      Per-export conform-loss report: preserved/approximated/dropped/
      unsupported feature rows + exact frame-drift block for the NLE/exchange
      exits. Deletable derived output under reports/conform/ — content-
      addressed, tamper-evident, never a build input.
```

Status: the orchestrator applied this row (and Loop E's
`manju.media-technical-profile/v1`) mid-loop — `tests/test_fp_contracts.py`
is 17/17 green at completion (the interim 2 reds for the unregistered
literals resolved as designed by the handoff).

## Deviations

- CLI exposure deliberately omitted this loop (addendum: library + tests
  only; `manju conform` surfacing is a later explicit decision).
- Full-suite run superseded by orchestrator directive (central suite);
  acceptance evidence is the targeted run: `tests/test_fp_conform.py` +
  timebase/interconnection/native_draft/openclap/export_*/edit_v3/
  caption_breaks/word_timed_captions/transition_overrides/virtual_trim —
  221 passed, 7 skipped (pre-existing environment skips), 0 failed.
