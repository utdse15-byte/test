# FP Loop Y4 — CMX3600 EDL IMPORT-PLAN (the analysis half S2 declined)

Roadmap §5 item 5 (depth). S2's CMX3600 EDL adapter was honestly writer-only
("there is no EDL import path this loop"). This loop adds the READ-ONLY ANALYSIS
half on the EXISTING house import-plan pattern — **plan, never apply** — built on
the openclap (`exporters/openclap/import_plan.py`) and fcpxml
(`exporters/fcpxml_import.py`, W2) precedents, whose honesty grammar it mirrors.

New module: `src/manju/exporters/edl_import.py` (the WRITER `edl.py` is never
touched — a parallel loop owns it this wave). CLI: `manju edl import-plan`.
Tests: `tests/test_fp_edl_import.py` (23, red-first).

---

## 1. W2-mirror audit (resolved from W2's landed results, commit 353bfbc)

**CLI surface — a per-format Typer sub-app, mirroring fcpxml exactly.**
W2 shipped `manju fcpxml import-plan FILE [--target PATH] [--json]` as a new
per-format sub-app (`fcpxml_app`, `app.add_typer(fcpxml_app, name="fcpxml")`),
itself mirroring openclap's grammar. EDL previously had **no** sub-app — only a
`--edl` flag on `manju export`. The faithful mirror is a new per-format sub-app
with exactly the same subcommand shape:

    manju edl import-plan FILE [--target PATH] [--json]

The writer stays a flag on `manju export --edl` (untouched). The command reuses
the exact grammar: an `_edl_read_or_fail` helper (mirrors `_fcpxml_read_or_fail`),
the same `{"error","code","diagnostics"}` `--json` error envelope, `hash_file`
for `source_sha256`, `_emit` for `--json`, a colored summary otherwise. The frozen
CLI surface was regenerated (`python -m tests.test_fp_cli_snapshot`), the
sanctioned (b)-path — the only delta vs HEAD is the single new key
`edl import-plan` (126 → 127; floor 112 holds).

**Schema id — carried in code, STOP-AND-REPORT for the registry row.** W2's
ruling: mirror openclap/fcpxml by declaring `PLAN_SCHEMA = "manju.edl-import-plan/v1"`
in code and hand the CONTRACTS.yaml row to the orchestrator (CONTRACTS.yaml is
orchestrator-only). Recommendation: register as **experimental** — the registry's
honest arrival state (fcpxml's is experimental; openclap's is stable only because
it is a mature adapter). See §6.

**Digest idiom — the same tamper-evidence helper.** `hash_value(facts)` over
every field except `digest` itself (the `conform-loss` / fcpxml precedent);
`verify_plan_digest` recomputes and rejects any tamper / dropped digest / wrong
schema.

---

## 2. Scope (READ + PLAN only)

`parse_edl(path | EDL text) -> ParsedEdl` — structural, **rate-independent**.
- `TITLE:` / `FCM:` headers; event rows tokenized generically (the 4 trailing
  tokens are always the timecodes; a single optional token between the edit type
  and the timecodes is the transition frame count — so a `C` cut and a `D    012`
  dissolve both parse).
- **Generic channel tokens**: `V`, `A`, `A1`, `A2`, `AA`, `B` and combined forms
  classify via `_channel_kind` (has_video / has_audio) with no table — a V-only
  document AND the Y1 audio extension (A1/A2 events) landing in parallel both
  parse the same way.
- `* FROM/TO CLIP NAME:` comments bind to the most-recent event row (the incoming
  clip of a dissolve); `* MANJU:` notes are collected **verbatim**; every other
  non-CMX line is **counted** in `unknown_rows`, never a crash.

`plan_edl_import(parsed, *, rate=None, source_sha256=None, target_project=None) -> dict`
DERIVED ADVISORY output (never a build input). Rows:
- **windows** — each picture event → a proposed clip window; timecodes decoded to
  **exact integer frame indices** through `core.timebase.Timecode` at the
  document's FCM drop mode. The plan keeps both the frame counts and the verbatim
  TC strings; `duration_frames` = rec_out − rec_in, `source_duration_frames` =
  src_out − src_in (the cut-list handle invariant). The zero-record-duration `C`
  "from" line of a dissolve is the CMX freeze mechanic — **not** its own window.
- **transitions** — a `D`-family edit → an `xfade_fade_candidate`
  (`proposed_type: xfade_fade`, duration = the transition frame column); any
  other non-cut edit (`W` wipe, `K` key) → an `unsupported_transition` row (never
  a faked cross-dissolve).
- **audio_events** — a CMX flat audio channel (`A`/`A1`/`A2`/`AA`) → a
  `cmx_audio_channel` row with the channel **verbatim** and an honest note that
  Manju's four buses (voice/music/sfx/ambient) cannot ride the flat A-channel
  model faithfully — **no bus fabricated** (exactly the conform "edl" stance).
- **needs_relink** — a CMX3600 EDL names tape reels / clip names, **not file
  paths**, so a source can NEVER be resolved to project media by the EDL alone:
  every distinct source (deduped) → a `needs_relink` row **pointing at the
  existing relink machinery** (`manju relink plan|apply` /
  `manju.media.relink.relink_plan` / `apply_relink`). Never copied, never fetched.
- **frame rate honesty** — an EDL carries no explicit rate. When the caller gives
  none it is inferred and `rate_assumed` is flagged: DROP FRAME → 30000/1001 (or
  60000/1001 if a frame label ≥ 30 is seen — the only DF-legal rates); NON-DROP →
  24 with a diagnostic (an EDL cannot distinguish 24/25/30 from its TCs). Pass
  `rate=` to decode against the true edit rate.
- **conflicts** (describe-only against a `--target`), **manju_notes**,
  **comments**, **unknown_rows** (count + first-5 samples), **diagnostics**.
- **digest** — tamper-evidence as in §1.

**PLAN ONLY.** No apply path, no truth writes.
`test_module_exposes_no_apply_or_write_surface` pins that no public name contains
apply/write/export.

---

## 3. Round-trip floor (the star pin) vs the committed S2 goldens

The exact bytes S2 pinned in `tests/test_fp_edl.py` (HEAD) are parsed back and
every window / dissolve / TC is recovered exactly, frames derived through the
document's FCM. Three-clip NDF timeline
(`clip1 --xfade_fade 500ms--> clip2 --fade 300ms(degraded)--> clip3`), @24 NDF,
start `01:00:00:00` (frame 86400, from `Timecode(1,0,0,0,R24,False)`):

| window        | rec_in_f | rec_out_f | src_in_f | src_out_f | dur_f |
|---------------|---------:|----------:|---------:|----------:|------:|
| TAKEALPHA0001 |    86400 |     86448 |        0 |        48 |    48 |
| TAKEBETA0002  |    86448 |     86496 |        3 |        51 |    48 |
| TAKEGAMMA0003 |    86496 |     86520 |        0 |        24 |    24 |

- windows = **3** (the zero-record-dur `C` freeze row of event 002 is correctly
  NOT a window); record continuity holds (out_N == in_{N+1}); source span ==
  record span on every row.
- transitions: **1 × xfade_fade_candidate** at event 2, 12 frames (500ms→12),
  from `TAKEALPHA0001` → to `TAKEBETA0002`.
- MANJU note surfaced verbatim: the degraded `fade` note rides through.
- needs_relink: **3** (one per distinct clip source), each pointing at
  `manju relink plan|apply`.

Drop-frame golden (single `TA` clip @29.97 DF, `FCM: DROP FRAME`, `;` frames
separator): record `01:00:00;00` decodes to frame **107892** at 30000/1001 DF
(from `Timecode(1,0,0,0,R30DF,True)`), 2000ms → **60** frame record duration,
source zero-based — rate inferred to 30000/1001 with `rate_assumed: true`, and an
explicitly-supplied `rate=R30DF` agrees frame-for-frame.

CLI end-to-end on the NDF golden (`manju edl import-plan demo.edl`) prints
`windows: 3 · transitions: 1 · audio: 0 · needs_relink: 3 · unknown_rows: 0`, the
xfade candidate, the three needs_relink rows, and the verbatim MANJU note; `--json`
is valid JSON whose `digest` re-verifies through `verify_plan_digest`.

---

## 4. Honesty grammar (mirrored from openclap/fcpxml)

- Nothing silently dropped: unknown line → counted; CMX audio channel → honest
  verbatim row (no fabricated bus); wipe/key → `unsupported_transition` (never a
  faked xfade); MANJU notes verbatim.
- External media never resolved/fetched — every CMX reel/clip source is a
  `needs_relink` row pointing at the existing machinery.
- Zero filesystem mutations (see §7). The plan is tamper-evident (digest).

---

## 5. Verification

`python -m pytest` (targeted). New suite red-first: `ModuleNotFoundError` →
after implementation **23 passed**.

Verification set (my suite + test_fp_edl + test_fp_cli_snapshot + test_fp_docs +
test_fp_contracts + test_fp_ratemig1): **127 passed, 2 failed**. The two reds are
the sanctioned schema-id STOP-AND-REPORT handoff, NOT regressions:

- `test_fp_contracts.py::test_every_src_schema_literal_is_registered` and
  `::test_registry_schema_ids_match_src_exactly` — the code carries
  `manju.edl-import-plan/v1`; CONTRACTS.yaml (orchestrator-only) does not yet.
  Proven to flip green with the §6 row (loaded through the real `contracts._load`,
  real file untouched: unregistered → `[]`, registry set == src literal set).

`test_fp_edl.py` (S2 writer, unchanged), `test_fp_cli_snapshot.py`,
`test_fp_docs.py`, `test_fp_ratemig1.py` stay green. `test_fp_fcpxml_import.py`
(W2) stays green (I edited only the additive `edl` block of cli.py). NB the shared
working tree also carries **parallel loops'** unstaged edits (edl.py, conform.py,
fcpxml.py, doctor.py + new untracked suites incl. Y1's `test_fp_edl_audio.py`,
which passes 17/17 alongside this loop); this loop authored **zero** lines of any
of those files.

---

## 6. STOP-AND-REPORT — CONTRACTS.yaml registry row (orchestrator to add)

The code carries `manju.edl-import-plan/v1`; CONTRACTS.yaml is orchestrator-only
and was NOT touched. Add under `schemas:` (verified against `contracts._load`):

```yaml
  - id: manju.edl-import-plan/v1
    kind: schema
    owner: manju.exporters.edl_import
    status: experimental
    latest_version: 1
    read_older: true
    write_older: false
    notes: >-
      Staged CMX3600 EDL (.edl) import plan (Y4, openclap/fcpxml precedent) —
      read-only analysis of a foreign edit decision list into proposed clip
      windows / dissolve (D) -> xfade_fade candidates / CMX flat audio-channel
      rows (no Manju bus fabricated) + needs_relink rows for every tape reel /
      clip source; writes nothing, fetches nothing, never auto-applies.
      Experimental = the registry's arrival state.
```

That single row flips both contracts assertions green (schema-id count 48 → 49,
floor 40 holds). Until then, expect exactly the two reds named in §5.

---

## 7. NO-writes proof

- Loop-level: authored files are `exporters/edl_import.py` (new), `cli.py`
  (+98/−0, purely additive — the audited `edl` import-plan sub-app only), the
  regenerated `tests/fixtures/cli_surface.json` (+1 key), this report, and the
  test file. Zero edits to `edl.py`/`conform.py`/`fcpxml.py`/`doctor.py`
  (concurrent loops).
- Runtime: a temp tree hashed (path + mtime_ns + size + bytes) before/after 3×
  `parse → plan → verify` is byte-and-mtime identical
  (`test_import_plan_never_writes`).
