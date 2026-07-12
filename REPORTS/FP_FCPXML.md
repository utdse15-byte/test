# FP Loop T2 — FCPXML writer (§5 / user item 5: the rational-native NLE exit)

Branch `claude/cost-optimization-strategy-cjfmn5`, HEAD `9757104`. Scope per the binding
addendum: **WRITER only** (no FCPXML import / import-plan this loop). A minimal-valid
FCPXML 1.9 subset — `library > event > project > sequence > spine` — compiled from the SAME
`Timeline.tracks.video` truth the render, OTIO and EDL exits read, expressing exactly what
the timeline carries and loud, in-band, about everything it does not.

**No new `manju.*/vN` schema**: an `.fcpxml` is an external-format artifact (like `.otio`,
`.edl`, `.ttml`), not a truth document — the conform-loss report (`manju.conform-loss/v1`,
target `"fcpxml"`) carries the honesty statement for it.

## Why FCPXML is special — rational time rides NATIVELY

FCPXML expresses every time as an exact rational number of seconds (`"N/Ds"`), and the
sequence `<format>` carries a `frameDuration` that IS the reciprocal of the edit rate. This
is the **one** NLE exit where our rational truth rides natively, with **zero drift for int
AND 1001-family projects**:

| edit rate | `frameDuration` | one clip of `F` frames |
|---|---|---|
| `24` (int) | `1/24s` | `"{F}/24s"` |
| `24000/1001` (23.976) | `1001/24000s` | `"{F·1001}/24000s"` (e.g. 48 → `48048/24000s`) |

Times are written as `"{F·den}/{num}s"` on the rate's own timescale (denominator = the
frame-rate numerator), **never reduced** — so frame-alignment is verifiable by inspection
(the numerator is always an integer multiple of the `frameDuration` numerator) and the bytes
are deterministic. Frame counts come from `VideoClip.duration_frames` (R2/R4 rational stamp)
when present, else telescope `ms_to_frames` differences (every int project — byte-identical
to the ms grid it always used). The edit rate resolves through the sanctioned
`Timeline.frame_rate` accessor (the rational echo when present, else the int `fps` promoted
to a whole-number `Rate`) — the writer never names the raw `edit_rate` field.

**The rational-native advantage, pinned.** Unlike OTIO — whose int path writes a
*fractional-frame* `RationalTime` value (`round(ms·fps/1000, 6)`) — FCPXML **snaps every
boundary to a whole frame and carries it EXACTLY** as `N/D` seconds. So the conform
`frame_drift` block reports `all_zero_by_construction: true` for **both** paths. This is
demonstrated by a test where an int project with an OFF-grid millisecond boundary (1234 ms
@ 24 fps = 29.616 frames) still yields residual `0` — the drift that OTIO's int path would
carry is quantized away into the exact frame FCPXML holds.

## FCPXML capability audit — 13-feature classification (conform target `"fcpxml"`)

| Timeline feature | FCPXML expression | Verdict |
|---|---|---|
| `tracks.video` clip windows | one `<asset-clip>` per clip on the spine; offset/start/duration as exact rational-seconds strings on the `frameDuration` timescale | **preserved** |
| `VideoClip.source_in_ms` (virtual trim) | the asset-clip `start` (source in-point, exact whole frame); the shared `<asset>` available duration widens to cover in-point + window | **preserved** |
| `transition_out` = `xfade_fade` (0 < dur < both adjacent clips) | a NATIVE `<transition>` (Cross Dissolve effect, FCP overlap geometry — the incoming clip and every later element pull back by the transition frames) | **approximated** (native cross-dissolve) |
| `transition_out` = `fade` / `xfade_*` wipes+slides / unknown / too-long dissolve / dissolve on the last clip | hard cut + an in-band `<!-- MANJU: … -->` note | **approximated** (never a wrong dissolve) |
| `tracks.overlay` (titles/branding) | — not written to the spine | **dropped** |
| `tracks.captions` | — captions ride the SRT/TTML exits (honest boundary; FCPXML titles NOT emitted) | **dropped** |
| `VideoClip.source_gain_db` / `source_mute` | — no `<adjust-volume>` on the video asset-clip this loop | **dropped** |
| audio in-points / gain / fades / ducking / loops / the four buses | — video spine only this loop (see the audio decision) | **unsupported** |

Completeness is meta-pinned: for a full-inventory timeline every one of the 13 `KNOWN_FEATURES`
lands in exactly one category, union == inventory, pairwise disjoint (no vacuous or fabricated
rows). The exporter-module coverage pin in `test_fp_conform.py` (every module under
`exporters/` is classified) is now **satisfied** by adding `fcpxml.py` together with its
classifier.

## The audio decision — honest rows, not faked lanes

The addendum's audit: "four buses → lane-based asset-clips with role attributes is
FCPXML-native — implement if clean, **else honest unsupported rows**." I took the **honest
rows** branch. FCPXML's connected-clip role/lane model genuinely *can* carry the four buses
(a real capability CMX EDL's flat A-channel lacks), but faithful placement requires
connected-clip nesting with parent-relative offsets that couple to each clip's source
in-point *and* to the transition-shifted spine offsets — that is **not "trivially faithful"**;
getting it subtly wrong yields wrong-position audio. So audio is honestly **omitted** this
loop and classified `unsupported`, with the `audio_tracks` row explicit that this is a
**writer-scope boundary — a deferred increment — not a format limit** (unlike EDL, where the
format itself cannot hold four buses). Nothing is faked. The video spine + rational-native
time is the faithful, low-risk star of this loop.

## Transitions — never a wrong dissolve (S2 precedent)

A clean cross-dissolve becomes a native `<transition>` referencing the `Cross Dissolve`
effect (`uid="FFVideoTransitionCrossDissolve"`), laid with FCP's overlap geometry: the
incoming clip and every later element pull back by the dissolve's frame count so the two
clips overlap for exactly the dissolve (the correct editorial overlap FCP itself writes). A
guard — `0 < dt < both adjacent clip lengths`, and a next clip must exist — keeps the overlap
valid; a dissolve that would meet/exceed either clip, or one on the last clip, **degrades to
a cut + note** rather than emit an invalid (wrong) dissolve. Every non-cross-dissolve kind
likewise degrades to a cut + an in-band `<!-- MANJU: … -->` note.

## Honesty boundaries (recorded, not silently claimed)

- **Captions** ride SRT/TTML — deliberately not emitted as FCPXML titles.
- **Audio** is a deferred writer increment (see above) — omitted, not faked.
- **Import is OUT OF SCOPE** — the module exposes exactly `compile_fcpxml` / `export_fcpxml`;
  no import / import-plan surface is claimed anywhere (pinned by a test).
- `tcFormat="NDF"` universally (`tcStart="0s"`): the honest subset makes no drop-frame
  timecode-display claim; the exact rational *time* is carried regardless via `frameDuration`.
- Sources are containment-checked exactly like OTIO's `target_url` (an out-of-project `src`
  is refused); the original project-relative `src` string is kept byte-identically.

## Determinism

Fixed resource order (format `r1`, then assets by first appearance `r2..`, then the effect
`r`last), fixed attribute order, exact rational strings (no float, no wall-clock), 2-space
`ElementTree` indentation, LF endings, trailing newline. XML validity + escaping are
`ElementTree`'s (CJK names ride natively; `&`/`<`/`>`/`"` escaped and round-trip verbatim).
The same timeline renders byte-identical FCPXML forever.

## CLI

`manju export --fcpxml` — an **optional** flag added to the existing `export` command
(`exports/fcpxml/<name>.fcpxml`). The frozen CLI-surface snapshot is **unchanged**: an
optional flag adds no required parameter and no new command, so `test_fp_cli_snapshot.py`
stays green with no regeneration.

## Files

- `src/manju/exporters/fcpxml.py` (new) — `compile_fcpxml` (pure, deterministic) + `export_fcpxml`.
- `src/manju/exporters/conform.py` — `"fcpxml"` rows (13-feature classification), `_DRIFT_TRACKS`,
  `_SCOPE_NOTES`, a dedicated rational-native `_fcpxml_frame_drift` (+ `_fcpxml_rate_mismatch`),
  and the `.fcpxml` exported-artifact cross-check. Existing S1/S2/R4 rows untouched.
- `src/manju/exporters/__init__.py` — re-export `compile_fcpxml` / `export_fcpxml`.
- `src/manju/cli.py` — the optional `--fcpxml` flag + handler.
- `tests/test_fp_fcpxml.py` (new) — 33 red-first tests.
