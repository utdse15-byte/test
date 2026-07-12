# FP Loop S1 — TTML/IMSC1 caption writer (§5.5 / user item 4: honest scope + conform-loss)

Branch `claude/cost-optimization-strategy-cjfmn5`. Scope per the binding addendum:
**WRITER only** (no TTML import this loop). The document is an IMSC1-Text-Profile-shaped
TTML1 sidecar compiled from the SAME `Timeline.tracks.captions` truth as SRT/ASS/VTT,
expressing exactly what the cue model actually carries — nothing invented. This closes
the deferral recorded in `REPORTS/FP_CAPTIONS.md` ("No TTML/IMSC writer this loop").

**No new `manju.*/vN` schema**: a TTML file is an external-format artifact (like `.srt`,
`.otio`, `.clap`), not a truth document — the conform-loss report (`manju.conform-loss/v1`)
already carries the honesty statement for it.

## The cue-model audit (what CaptionLine really has → what the TTML says)

| `CaptionLine` field | TTML expression | Honesty note |
|---|---|---|
| `start_ms` / `end_ms` | `begin`/`end` as media-time `HH:MM:SS.mmm` | ms is the model's native precision (§4.4); `ttp:timeBase="media"` explicit; fps never enters (the rational R-track upgrades timing later — recorded) |
| `text` | escaped UTF-8 `<p>` content; author/budget breaks → `<br/>` | same `break_lines` budget as SRT/VTT (imported, not re-derived); CJK passes through un-entity-escaped |
| `speaker` | `<ttm:agent type="character">` per distinct speaker in head metadata (sorted → `agent.1`, `agent.2`, …) + `ttm:agent` ref on the cue | real model data expressed natively — NOT the guessed "speaker prefix in text" (the audit found a dedicated field) |
| `role` (wave 4b, optional) | standard hint where mapped (table below) + **verbatim `x-manju:role` on every roled cue** | never dropped silently; role-less cues are plain `<p>` rows |
| `shot` | *not emitted* | internal impact-spine id — SRT/ASS ignore it too (models.py stance) |

Not carried by the model → honestly NOT in the document: positioning/regions per cue,
RTL / vertical writing / ruby (they need layout semantics the cue model lacks — **out of
scope, recorded** in the conform rows), per-word timing (contract §5 UNALIGNED: no
fabricated karaoke tokens — same stance as `compile_vtt`).

## Role → TTML mapping (the loop's binding table)

| `cue.role` | Standard attribute(s) | Plus verbatim |
|---|---|---|
| `sdh` | `ttm:role="captions"` | `x-manju:role="sdh"` |
| `translation` | `ttm:role="subtitles"` | `x-manju:role="translation"` |
| `forced` | `itts:forcedDisplay="true"` (no `ttm:role`) | `x-manju:role="forced"` |
| `speaker_label` | `ttm:agent` → the cue's speaker agent, else the shared stub `<ttm:agent type="other" xml:id="agent.unknown"/>` | `x-manju:role="speaker_label"` |
| `lyrics` | none | `x-manju:role="lyrics"` |
| anything else | none — never a fabricated standard field | `x-manju:role="<verbatim>"` (hostile characters survive a parse round-trip — pinned) |

Honest nuance, recorded: TTML1's *registered* `ttm:role` tokens are singular
(`caption`) and include no `subtitles`; the two hint values above follow this loop's
binding mapping table. Nothing depends on the hint token — the authoritative, lossless
value is always `x-manju:role` (namespace `urn:x-manju:ttml`, prefix `x-manju` —
mirroring the openclap `x-manju` extension-key stance). Flipping the hint tokens later
is a one-line change with zero truth loss.

## Honest-scope decisions

- **One default region** `r.bottom` (origin `10% 80%`, extent `80% 15%`,
  `displayAlign="after"`, `textAlign="center"` — §7④ spirit, percentages only), one
  default style; referenced once on `<body>`, inherited by every `<p>`.
- **`xml:lang=""`** — the audit found NO recorded base-project locale (locales are
  per-language *overlays*, `core/locale.py`); `compile_ttml(lang=…)` exists for callers
  that know (a locale build), the exporter never guesses.
- **No `ttp:profile` conformance claim**: no external validator runs (no new deps), so
  the document does not claim what it has not proven.
- **XML-1.0-illegal C0 controls** are substituted with U+FFFD (they cannot exist in any
  XML document, escaped or not — documented loss, module docstring).
- **Manual takeover (§3)**: `rules.captions.mode == "manual"` + existing `captions.srt`
  → the TTML recompiles FROM the human cues (mirrors `export_captions`' ASS/VTT
  re-burn) so every caption exit says the same thing; SRT carries no roles/speakers, so
  the manual TTML honestly has none (format honesty, FP loop I). `captions.generated.srt`
  stays the SRT exporter's job.
- **Determinism**: fixed skeleton, per-element attributes sorted by name, sorted agent
  ids, LF endings, no wall-clock — byte-identical forever (pinned two-run tests at
  compile and export level).

## Conform-loss: the "ttml" classification (exporter-#6 meta-pin)

`exporters/conform.py` gains the `ttml` target: `captions` **preserved** with the
subset stated in the row itself (roles/agents + the not-expressed RTL/vertical/ruby
boundary, line-cited to `exporters/ttml.py`); **every** non-caption feature falls to
the caption-only **unsupported** fallback — the exact `srt_ass` honesty stance. Also:
`_NOT_TIME_BEARING["ttml"]` (ms-native, no frame grid → `frame_drift.checked=False`),
a `_SCOPE_NOTES["ttml"]` row, and a read-only `.ttml` cross-check note (ElementTree
`<p>` count vs timeline cues, MISMATCH-flagged when stale). No other target's rows were
touched. `tests/test_fp_conform.py::test_every_exporter_module_is_classified_or_declared_unsupported`
passes with the new module classified — honestly, not via `UNSUPPORTED_TARGETS`.

## CLI exposure (smallest honest surface)

`manju export --ttml` writes `captions/captions.ttml` next to the existing `--srt`
path (same try/except + failure-record flow; `--ttml` alone does NOT trigger the
srt+otio default pair). The frozen CLI snapshot is UNCHANGED — `--ttml` is an optional
parameter and the snapshot pins commands + required params only — so no regeneration
was needed (verified: `tests/test_fp_cli_snapshot.py` green as-is). Deliberately NOT
wired this loop (deferred, unchanged surfaces): build-graph/exportstatus/delivery rows,
GUI/board/MCP export paths, locale caption builds.

## Files

- `src/manju/exporters/ttml.py` — new: `ms_to_ttml` (= `ms_to_vtt`, identical grammar,
  reuse over drift), `compile_ttml`, `export_ttml`.
- `src/manju/exporters/conform.py` — `ttml` rules row + fallback + scope note +
  not-time-bearing reason + `.ttml` cross-check branch.
- `src/manju/cli.py` — `export --ttml`.
- `src/manju/exporters/__init__.py` — re-export `compile_ttml`/`export_ttml`.
- `tests/test_fp_ttml.py` — 25 red-first pins (document shape, timing pins
  `0→00:00:00.000` / `3661234→01:01:01.234`, the full role table incl. forcedDisplay +
  verbatim hostile fallback, agents, CJK, escaping, `<br/>`, line budget, determinism,
  role-less + empty stability, export glue + dest override, manual takeover, conform
  rows / not-time-bearing / stale-artifact MISMATCH / meta-pin mirror, CLI flag).

## Test evidence (targeted)

- `tests/test_fp_ttml.py` — 25 passed (written first; 25 red before implementation).
- `tests/test_fp_conform.py` + `tests/test_fp_cli_snapshot.py` + `tests/test_fp_captions.py`
  + `tests/test_caption_breaks.py` — 86 passed.
- `tests/test_cli.py` + `tests/test_export_center.py` — 51 passed.
