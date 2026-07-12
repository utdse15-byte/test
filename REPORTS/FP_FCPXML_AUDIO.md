# FP Loop V1 — FCPXML connected-audio lanes (the T2-deferred increment)

Branch `claude/cost-optimization-strategy-cjfmn5`. This loop EXTENDS T2's
`compile_fcpxml` (roadmap §5 item 5) with the audio increment T2 explicitly
deferred (see `REPORTS/FP_FCPXML.md` — "The audio decision"). T2 took the honest
`unsupported` branch because "faithful placement requires connected-clip nesting
with parent-relative offsets that couple to each clip's source in-point *and* to
the transition-shifted spine offsets". This loop implements exactly that placement
— reusing T2's `offsets` / `in_frames` / `frames` arrays and `_secs`, never a
second geometry.

## What is now written

Every `AudioClip` on the four buses (voice / music / sfx / ambient) that is
**non-loop with a resolvable `duration_ms`** becomes a CONNECTED `<asset-clip>`
nested INSIDE its owning spine clip:

| aspect | expression |
|---|---|
| host | the spine clip `i` whose PULLED-BACK interval `[offsets[i], offsets[i]+frames[i])` contains the clip's absolute frame `F` (ties → the earlier clip; `F` past the last clip end → the last clip) |
| placement | `child.offset = _secs(in_frames[i] + F − offsets[i], rate)` — parent-relative, so `parent_offset + (child_offset − parent_start) == F` |
| source in-point | `child.start = _secs(ms_to_frames(start_offset_ms), rate)` (0 when untrimmed) |
| duration | `child.duration = _secs(ms_to_frames(duration_ms), rate)` — a connected clip MAY extend past its parent's end (legal FCPXML; never split) |
| lane | fixed: voice −1, music −2, sfx −3, ambient −4 |
| audioRole | fixed: `dialogue` / `music` / `effects.sfx` / `effects.ambient` (standard base role + verbatim bus subrole) |
| gain | `<adjust-volume amount="{gain_db:g}dB">` child, ONLY when `gain_db != 0` |
| asset | one `hasAudio="1"` `<asset>` per distinct source (deduped, available range widened to `in-point + window`), declared after the video assets, before the effect |

Frame counts use the SAME `ms_to_frames(ROUND_HALF_UP)` discipline as T2. Emit
order is deterministic: bus order voice/music/sfx/ambient, then clip-list order.

## Honest rows (never faked)

- **loop=True** → fill-to-duration semantics; a single pass would be wrong audio.
  NOT written — an in-band `<!-- MANJU: … loop bed … omitted … -->` note records
  the deferral (a future loop may materialize repeats).
- **duration_ms is None** → the render's `_build_audio_graph`
  (`media/render.py`) resolves this to the source's natural length and performs
  **no static probe**; the writer performs no probe either (parity), so the clip
  is OMITTED with an honest note rather than a fabricated frame count. In a
  compiled timeline this is exactly the SFX bus.
- **ducking=True** → the clip STILL plays (written at its static gain); only the
  render-time sidechain RELATIONSHIP is unexpressible, so it rides an in-band
  "approximated" note. The mix is never silently claimed.
- **fades** → gain-only branch (see below); fade values ride an honest note.
- **source_audio on video clips / overlays** → out of scope (unchanged).

## The placement math — hand-computed golden (the hard case)

Golden timeline (`tests/test_fp_fcpxml_audio.py::test_golden_audio_lanes_exact_bytes`),
@24fps: clip0 (0–2000ms) `--xfade_fade 500ms-->` clip1 (2000–4000ms, in-point
250ms). Video geometry (T2's arrays): `frames=[48,48]`, `in_frames=[0,6]`,
dissolve `12`, `offsets=[0,36]`, seq `84`.

| audio clip | `F` | host | `child.offset` = in_frames[i]+F−offsets[i] | recovered abs = off+(child−start) |
|---|---|---|---|---|
| voice @500ms | 12 | clip0 | `0+12−0` = **12** | `0+(12−0)` = 12 ✓ |
| music @2500ms, −6dB | 60 | **clip1 (pulled back)** | `6+60−36` = **30** | `36+(30−6)` = 60 ✓ |
| sfx @3000ms, in 125ms | 72 | **clip1** | `6+72−36` = **42** | `36+(42−6)` = 72 ✓ |

The music/sfx offsets (30, 42) are neither `F` nor `F−pullback` — they couple to
BOTH the dissolve pullback (offsets[1]=36) and clip1's 6-frame source in-point,
exactly the coupling T2 flagged as the hard part. The sfx clip's absolute span
`[72, 96)` extends 12 frames PAST clip1's end (84) and is emitted as a single
connected clip (asserted, not split). A tie test
(`test_tie_in_dissolve_overlap_attaches_to_earlier_clip`) pins that `F=38` in the
overlap `[36,48)` — inside both clips — resolves to the earlier clip0.

## Fades — the confidence branch taken: GAIN-ONLY

Confidence in the exact FCPXML 1.9 fade-handle element/attribute shape is NOT
high (candidate encodings differ: a `<fade-in>`/`<fade-out>` element, keyframed
`<adjust-volume>` params, or clip `fadeIn`/`fadeOut` attributes), and a wrong one
risks FCP rejecting the whole document — worse than an honest omission. So fades
are expressed **gain-only**: `fade_in_ms`/`fade_out_ms` emit NO fade element;
instead an in-band note names the omitted values. Gain itself (`<adjust-volume>`)
still lands. This mirrors T2's own conservative stance.

## Duration-None resolution — parity with the render path

Audited `media/render.py::_build_audio_graph`: it performs **no media probe**.
Per bus it resolves length as — voice: `atrim=0:duration_ms` when set, natural
media length when None; sfx: natural media length; music/ambient:
`apad,atrim=0:total_s` (filled to the timeline length). The compiler
(`timeline/compiler.py`) always SETS `duration_ms` on voice (=voice length) and
music/ambient (=`total_ms`), so the writer's emitted frame count equals the
render's effective length for every written clip (voice's `atrim` == duration_ms;
a bed's `duration_ms == total_ms == total_s`). The ONLY compiler-produced
`None` is SFX, which the render plays at natural length WITHOUT probing — so the
writer honestly omits it (a note records the render's exact behaviour) rather than
inventing a length.

## Determinism, CJK, empty-buses byte-identity

Deterministic bytes (fixed resource/lane/attribute order, exact rational strings,
2-space indent, LF, trailing newline — pinned). CJK audio source basenames ride
natively. **Empty buses are byte-identical to T2's frozen video-only golden**
(`test_empty_buses_are_byte_identical_to_t2_video_only_golden` recompiles T2's
three-clip timeline and asserts equality with T2's exact golden string, plus that
no `lane=` / `hasAudio` / `audioRole` / `adjust-volume` leaks) — because the audio
path activates only when a bus has a written clip, and audio assets/ids append
strictly AFTER the video ones (the effect id shifts only when audio exists).

## Scope / deviations

- **No CLI change, no new schema id** — the existing `manju export --fcpxml`
  flag now simply carries audio.
- **`conform.py` was left UNTOUCHED (documented deviation).** The addendum's
  deliverable #2 (move the 7 audio features out of `unsupported`) is
  irreconcilable this loop with T2's frozen conform test
  `test_fcpxml_conform_video_preserved_captions_dropped_audio_unsupported`, which
  asserts `audio_tracks` and `audio_gain` are `unsupported` for its inventory
  timeline — whose only audio clip is a **loop bed**. `classify_features` is
  per-feature (it sees inventory rows, not the clips), so any static category move
  breaks that frozen test, which the binding rules forbid editing. Crucially, for
  that loop-only timeline the honest per-export answer genuinely IS
  `unsupported` (a loop bed is not written), so the existing table is already
  correct for it; a truthful reclassification would have to be per-clip
  (loop/None → unsupported, otherwise preserved/approximated), which needs a
  timeline-aware classifier outside this loop's file scope. The per-clip audio
  honesty is instead carried by the WRITER in-band (ducking/fade approximation
  notes + loop/None omission notes) — nothing is silently claimed.
- Audio with **no video spine host** draws no connected clips (they need a
  parent) — an unusual edge, left as an honest no-op.

## Files

- `src/manju/exporters/fcpxml.py` — audio constants + `_audio_parent_index` /
  `_audio_name` / `_audio_approx_notes` / `_AudioPlanItem` / `_plan_audio`
  helpers; `compile_fcpxml` extended (audio assets + nested connected clips +
  honest notes). Video-only output byte-identical.
- `tests/test_fp_fcpxml_audio.py` (NEW) — 22 red-first tests. T2's
  `tests/test_fp_fcpxml.py` untouched (33 green).
