# FP Loop S2 — CMX3600 EDL writer (§5 / user item 5: real timecode + conform-loss)

Branch `claude/cost-optimization-strategy-cjfmn5`. Scope per the binding addendum:
**WRITER only** (no EDL import this loop). A classic CMX3600 video cut list compiled from
the SAME `Timeline.tracks.video` truth the render and OTIO export read, expressing exactly
what the timeline carries and loud, in-band, about everything CMX cannot hold.

**No new `manju.*/vN` schema**: an `.edl` is an external-format artifact (like `.srt`,
`.otio`, `.ttml`), not a truth document — the conform-loss report (`manju.conform-loss/v1`)
carries the honesty statement for it.

## CMX capability audit — what our timeline can ride, and what it cannot

| Timeline feature | CMX3600 expression | Verdict |
|---|---|---|
| `tracks.video` clip windows | one V-track `C` event per clip; record TC from the cumulative timeline position (ms→frames ROUND_HALF_UP at the edit rate), offset by `start_timecode` | **preserved** |
| `VideoClip.source_in_ms` (virtual trim) | source-side in-point, `00:00:00:00`-based; source length reuses the record frame count | **preserved** (honest zero-base) |
| `transition_out` = `xfade_fade` (dur>0) | standard two-line dissolve on the incoming event: a zero-duration `C` "from" line + a `D` line over the transition's frame count | **preserved** (native `D`) |
| `transition_out` = `fade` / `xfade_*` wipes+slides / unknown | hard cut + an in-band `* MANJU:` note stating the loss | **approximated** (never a wrong dissolve) |
| `tracks.overlay` (titles/branding) | — not a cut-list primitive | **dropped** |
| `tracks.captions` | — a cut list is picture-only (SRT/ASS/VTT/TTML are the caption exits) | **dropped** |
| audio buses (voice/music/sfx/ambient), clip own-audio, gains/fades/ducking/loops | — Manju's four-bus reality cannot ride CMX's flat A-channel model faithfully | **unsupported** (V-only by design) |

**FCM / drop-frame decision.** Integer rates (the default this loop) → `FCM: NON-DROP FRAME`,
all-colon `HH:MM:SS:FF`. A drop-frame-legal NTSC rate (30000/1001, 60000/1001) →
`FCM: DROP FRAME` and `HH:MM:SS;FF` (semicolon before frames — the CMX-legal DF convention
that `timebase.Timecode` already renders). 23.976 and 48000/1001 stay NON-DROP though NTSC
(the timebase enforces DF legality; we mirror it via `Rate.is_ntsc && nominal_int ∈ {30,60}`).
The mode is auto-derived from the rate, resolved (defensively) as: the R2 rational `edit_rate`
echo on the timeline (`getattr`) → a project-declared rational `edit_rate` mirroring
`timeline.fps` → the legacy integer `fps`. R2 has NOT yet added an `edit_rate` echo to
`Timeline`; the `getattr` fallback path is what runs today, so an int project is exact and
the DF path is reachable via a project-declared 29.97/59.94.

## Source-timecode honesty

`TakeSidecar` carries **no** recorded reel or source timecode (audited — the field does not
exist). Generated media's own timebase is zero-based, so its honest source TC **is**
`00:00:00:00`-based: a clip's source-in is `ms_to_frames(source_in_ms)` and its source-out is
that plus the **record** duration. Durations are taken from the record side (absolute
positions) and reused on the source side, so (a) a clip's source length always equals its
record length — the cut-list invariant — and (b) record continuity is exact when clips abut
(`event N out == event N+1 in`), which the compiler guarantees (single advancing `cursor`).
This is stated in the conform-loss report, never dressed up as real tape TC.

## Reel names (8-char CMX limit)

Deterministic 8-char ASCII fold of the take name: uppercased `[A-Z0-9]`, truncated to 8; an
all-non-ASCII name (e.g. CJK) folds to a **hash-derived** ASCII reel so the column is always
legal. Distinct takes folding to the same stem are disambiguated by a numeric suffix in
timeline order (`TAKEALPH`, then `TAKEALP2`); the **same** take always maps to the same reel.
The full, untruncated take name (UTF-8, CJK and all) rides the `* FROM CLIP NAME:` comment —
**columns stay ASCII, identity is never lost**. The `TITLE:` header is ASCII-folded (the
editorial identity of media lives in the comments, not the header).

## Golden sample (first 10 lines of the 3-clip fixture, 24 fps NDF)

```
TITLE:   DEMO EDL
FCM: NON-DROP FRAME
001  TAKEALPH V    C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00
* FROM CLIP NAME: TAKEALPHA0001
002  TAKEALPH V    C        00:00:02:00 00:00:02:00 01:00:02:00 01:00:02:00
002  TAKEBETA V    D    012 00:00:00:03 00:00:02:03 01:00:02:00 01:00:04:00
* FROM CLIP NAME: TAKEALPHA0001
* TO CLIP NAME: TAKEBETA0002
* MANJU: transition 'fade' (300ms) at S002 approximated as hard cut (no clean CMX3600 dissolve mapping)
003  TAKEGAMM V    C        00:00:00:00 00:00:01:00 01:00:04:00 01:00:05:00
```

Event 002 is the standard CMX dissolve: the `C` "from" line freezes the outgoing frame
(zero record + source duration), the `D    012` line dissolves into clip 2 over 12 frames
(500 ms @24). Clip 2's own `fade` transition has no clean mapping → clip 3 is a hard cut and
the `* MANJU:` note records the loss. Column layout: `NNN␣␣REEL(8)␣CHAN(4)␣OP(4)␣TDUR(3)␣SRC_IN␣SRC_OUT␣REC_IN␣REC_OUT`.

## Conform-loss: the "edl" classification (exporter meta-pin)

`exporters/conform.py` gains the `edl` target with **all 13 known features classified
explicitly** (no fallback needed): `video_clips`/`video_in_points` **preserved**,
`transitions` **approximated** (the dissolve-vs-degraded split stated in the row),
`overlays`/`captions` **dropped**, and the whole audio family (`audio_tracks`,
`audio_in_points`, `clip_volume`, `audio_gain`, `audio_fade_in`/`out`, `ducking`,
`audio_loops`) **unsupported** — audio out of scope by design, honestly, not silently.
Also: `_DRIFT_TRACKS["edl"] = ("video",)` (only the V track lands on the frame grid — exact
1001-family grid drift surfaces here just as it does for OTIO), a `_SCOPE_NOTES["edl"]` row,
and a read-only `.edl` cross-check (distinct event numbers vs video-clip count,
MISMATCH-flagged when stale). No other target's rows were touched.
`test_every_exporter_module_is_classified_or_declared_unsupported` passes with `edl.py`
classified — honestly, not via `UNSUPPORTED_TARGETS`.

## CLI exposure (smallest honest surface)

`manju export --edl` writes `exports/edl/<name>.edl` next to the existing `--otio` path
(same try/except + failure-record flow; `--edl` alone does NOT trigger the srt+otio default
pair). The frozen CLI snapshot is **UNCHANGED** — `--edl` is an optional parameter and the
snapshot pins commands + required params only — so no regeneration was needed. Deliberately
NOT wired this loop (deferred, unchanged surfaces): build-graph/exportstatus/delivery rows,
GUI/board/MCP export paths.

## Out of scope (recorded, honest)

EDL **import**; audio (A-channel) events; wipe (`W`) events and dip-to-black BL-reel
dissolves (would need a black-reel construction — degraded to cut + note instead of a wrong
dissolve); speed ramps / nested sequences / multicam; per-clip R2 `duration_frames` (landed
mid-loop, `None` for every int project — a future refinement could prefer the exact frame
count over `ms→frames` on the rational path; the ms→frames spec + absolute-position
continuity is kept this loop per the addendum).

## Files

- `src/manju/exporters/edl.py` — new: `compile_edl` (pure, deterministic — takes a `Rate` +
  DF flag) and `export_edl` (resolves rate/DF from project+timeline, writes atomically).
- `src/manju/exporters/conform.py` — `edl` rules table (13 features) + drift-track + scope
  note + `.edl` cross-check branch.
- `src/manju/cli.py` — `export --edl`.
- `src/manju/exporters/__init__.py` — re-export `compile_edl`/`export_edl`.
- `tests/test_fp_edl.py` — 28 red-first pins.

## Test evidence (targeted)

- `tests/test_fp_edl.py` — 28 passed (golden bytes, continuity, source honesty, reel
  truncation + collision + CJK, dissolve vs degraded fallback, DF header/separator + refusal,
  determinism, empty timeline, export glue + dest override + project-declared NTSC DF, conform
  completeness meta-pin + audio-unsupported + drift + cross-check + 1001-family source drift).
- `tests/test_fp_conform.py` + `tests/test_fp_cli_snapshot.py` — 32 passed (meta-pin green
  with `edl` classified; snapshot unchanged).
- `tests/test_fp_ttml.py` + `tests/test_cli.py` + `tests/test_c19_cli.py` — 47 passed
  (shared `exporters/__init__` + CLI surface intact).
- End-to-end `manju export --edl --yes` on a scaffolded project → byte-identical to the golden.
