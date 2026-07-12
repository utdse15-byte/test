# FP Loop I — Caption roles + accessibility advisories (§5.5: record + advise, never block)

Branch `claude/cost-optimization-strategy-cjfmn5`. Scope per the binding addendum:
OPTIONAL role vocabulary on caption cues with byte-identical default behavior, plus a
derived CJK-aware readability/accessibility advisory report. **Nothing here blocks a
build, a check, or an export; nothing rewrites a cue.** No TTML/IMSC writer this loop
(that is a conditional §11-class exporter — explicitly out of scope, recorded here).

## Where captions actually live (the audit)

| Concern | Owner |
|---|---|
| Cue model | `src/manju/core/models.py` — `CaptionLine` (start_ms/end_ms/text/speaker/shot) on `Timeline.tracks.captions`; `CaptionRules` on `TimelineRules.captions` |
| Cue creation | `src/manju/timeline/compiler.py::_timed_captions` (TTS word grouping); manual takeover re-parses `captions/captions.srt` via `providers/asr.py::parse_srt` |
| Export | `src/manju/exporters/srt_ass.py` — `compile_srt` / `compile_ass` / `compile_vtt` / `export_captions` (manual mode: human SRT is truth, ASS/VTT re-burned from it) |
| Caption QC (check-time) | `src/manju/qc/checks.py::_technical_captions` (empty text, non-positive duration, per-cue char budget, ≤120 ms overlap warn) + `_technical_garbled_captions`; hard overlaps → `_technical_timeline_conflicts` |
| Delivery rows | `src/manju/build/exportstatus.py::_caption_row`/`_vtt_row` → `build/delivery.py::_artifact_from_row` (roles `CAPTIONS_SRT`/`CAPTIONS_ASS`/`CAPTIONS_VTT`) |
| Locale overlay | `src/manju/core/locale.py` (locale caption freshness keys hash `CaptionRules` + an explicit cue projection — both untouched by this loop) |

No reading-speed/CPS logic existed anywhere in `src/` before this loop (verified by
grep); the only adjacent facts were the per-cue char budget and the overlap warn in
`_technical_captions`, which stay exactly as they were.

## 1. `CaptionLine.role` (optional, truth-side)

- Vocabulary `CAPTION_ROLES = ("translation", "sdh", "forced", "lyrics", "speaker_label")`
  in `core/models.py`.
- `role: str | None = None`, **lenient at the model** (hand-edited truth — same stance
  as `TRANSITION_TYPES`): an unknown string parses fine and is diagnosed later, never
  a crash.
- **Byte-identity pin**: a wrap `model_serializer` drops the key entirely when the
  role is `None`. `save_timeline` uses plain `model_dump()`, so without this the field
  would have landed as `"role": null` in every recompiled `timeline.json`. With it,
  role-less projects serialize byte-identically — stricter than the round-O `shot`
  precedent (no one-time fingerprint move at all).
- Unknown role ⇒ **structured warn** in `qc/checks.py::_technical_captions`
  (level `warn`, never `error` — `QCReport.ok` is unchanged; never silent).

## 2. Role flow: export + delivery

- **ASS**: the role lands **verbatim** in the Dialogue **Name** field
  (`Dialogue: 0,start,end,Default,<role>,0,0,0,,text`). Role-less cues keep the
  historical empty field — byte-identical output (pinned). Because Name sits mid
  field-grid, comma → fullwidth comma, newline → space, and `{`/`}`/`\` are
  neutralized exactly like cue text (round-W #31 stance) so a hostile role can never
  shift the field grid or inject override tags (pinned).
- **SRT**: the format has **no role field** — bytes are identical with or without
  roles (pinned). The role stays truth-side in `timeline.json`. Consequence: the
  manual-SRT takeover path cannot carry roles either (parse_srt round-trip loses
  them by format honesty) — role coverage reads "empty" for manual projects and the
  report header says so.
- **VTT**: left untouched this loop (no standardized cue-role slot for this
  vocabulary; bytes identical with or without roles — pinned).
- **Delivery manifest**: caption artifact rows (`CAPTIONS_SRT/ASS/VTT`) gain an
  **additive** `caption_roles` count map (verbatim role → cue count), only when at
  least one cue carries a role. Role-less manifests have no new key and an unchanged
  `manifest_digest` (same stance as the additive `audio` block: the digest core is an
  explicit projection and the file sha256 already binds delivered bytes). The cut
  fingerprint (`timeline_semantic_digest`) is untouched.

## 3. The advisory report — `manju.caption-accessibility/v1` (experimental)

`src/manju/qc/captions_access.py`, a pure derivation + deletable artifact
(`media/technical_profile.py` stance). Every row is `severity: "advisory"`; the
module never touches `QCReport`, `manju check`, build, or export outcomes (pinned:
a caption set drowning in advisories leaves QC's verdict exactly as before).

**CJK counting rule (documented + pinned with exact numbers):** character weight is
Unicode East Asian Width (UAX #11): `W` and `F` → **2**; newline → **0**; everything
else (spaces, `H`, `Na`, `N`, and ambiguous `A`) → **1**.
`CPS = weighted_chars × 1000 / duration_ms` (2 dp). Pins: `hello`/1s = 5.0;
`你好世界`/2s = 4.0; `你好ab`/1.2s = 5.0; non-positive duration ⇒ `cps: null`, never
a crash. Line weights measure what actually renders: compiled mode applies the same
`break_lines` budget the SRT/ASS writers use; manual cues are measured verbatim.

**Advisory catalogue** (defaults are knobs, not policy — a human/platform profile
decides; all overridable per call):

| Code | Fires when | Default |
|---|---|---|
| `CPS_HIGH` | cue cps > ceiling | 20.0 weighted cps (≈10 full-width/s) |
| `LINE_TOO_LONG` | any rendered line weight > ceiling | 42 weighted (≈21 full-width) |
| `TOO_MANY_LINES` | rendered lines > ceiling | 2 |
| `DURATION_UNDER_FLOOR` | duration < floor | 833 ms (~5/6 s) |
| `DURATION_OVER_CEILING` | duration > ceiling | 7000 ms |
| `GAP_TOO_SHORT` | 0 < gap < minimum (butt-joined 0 ms is deliberate, not flagged) | 83 ms (2 frames @24) |
| `CUE_OVERLAP` | adjacent sorted windows overlap (> 0 ms) | — |
| `FORCED_OVERLAPS_NONFORCED` | a `forced` cue shares screen time with a non-forced cue | — |
| `ROLE_UNKNOWN` | role outside `CAPTION_ROLES` (recorded verbatim) | — |

**Cue rows** carry the measured (post-break) text alongside the numbers, so an
advisory row is self-explanatory and `report_digest` binds to actual content
(identical geometry over different words still differs).

**Summary block**: reading-speed distribution (min/max/mean/median, over-ceiling
count, fixed buckets ≤5 / 5-10 / 10-15 / 15-20 / 20-25 / >25), role coverage
(counts per verbatim role, unroled count, unknown-role list), totals by code.

**Determinism**: no wall-clock field anywhere; `report_digest = hash_value(body)`;
`write_accessibility` stores `reports/captions/<digest-hex>.caption-accessibility.json`
(atomic, deletable, never read back by any build/check path).

## 4. CLI

`manju qc captions [--json] [--write]` (appended beside `qc tech` / `qc conformance`,
the sibling derived-report commands). **Always exits 0** — advisories never block
(pinned). Snapshot regenerated: 118 → 119 commands (118 already included the
wave-4a `help-workflow` fold-in committed mid-loop; this loop's delta is exactly
`qc captions`).

## 5. Registry row (for the orchestrator to apply — CONTRACTS.yaml deliberately not edited by this loop)

```yaml
  - id: manju.caption-accessibility/v1
    kind: schema
    owner: manju.qc.captions_access
    status: experimental
    latest_version: 1
    read_older: false
    write_older: false
    notes: >-
      Derived caption readability/accessibility advisory report (§5.5): CJK-aware
      CPS/line geometry (UAX#11 W/F=2), duration/gap/overlap/role-coverage rows, all
      severity "advisory" — never a build/check input. Content-addressed under
      reports/captions/. Record + advise, never block.
```

Until that row lands, `tests/test_fp_contracts.py::test_every_src_schema_literal_is_registered`
and `::test_registry_schema_ids_match_src_exactly` are red by design (the governance
tests doing exactly their job on the handoff).

## Out of scope (recorded)

- TTML/IMSC writer — conditional §11-class exporter, not this loop.
- `timeline/cuemap.py`'s raw-dict normalization fallback rebuilds cues field-by-field
  and would drop a role on that path (voice-repair realignment of legacy dict cues);
  model-typed cues pass through intact. Recorded as a known boundary, not touched.
- GUI caption editors and locale `lines.yaml` overlays: roles survive load→save via
  the typed model; no UI affordance added this loop.
