# FP Loop E — manju.delivery-conformance/v1 (declared targets, honest verdicts)

Declarative delivery TECHNICAL targets (roadmap §7.1) + a per-check delivery
conformance REPORT (§7.2): each §7.2 checklist row carries one of
`PASS / FAIL / UNKNOWN / NOT_APPLICABLE`. UNKNOWN is honest — a declared target
the media cannot prove (no stored technical profile, colour genuinely unknown,
no measured loudness) is UNKNOWN, **never guessed into PASS**. There is **no
aggregate score** (§7.2 明确 不生成单一总分); the summary is counts by status
only. The report is deterministic (same inputs → byte-identical doc + digest,
no wall-clock), a deletable derived projection under `reports/conformance/`, and
**never a build or release input** (grep-pinned).

Conformance EXTENDS the existing delivery owner — it reads
`build.delivery.build_manifest` (the ONE delivery-list owner), the
`media.technical_profile` facts, and the `media.masters` loudness index. No
second system, no new fact source, no new dependency.

## Files
- `src/manju/build/conformance.py` (new) — `check_delivery_conformance(project,
  manifest, profile) -> doc`, `write_conformance_report` / `read_conformance_report`
  (tamper-rejected on read), `builtin_technical_presets()`. Holds the ONE schema
  literal `SCHEMA = "manju.delivery-conformance/v1"`.
- `src/manju/cli.py` — one additive command `manju qc conformance` under the
  existing `qc` sub-app (same placement precedent as `qc tech`). Local imports
  only; the schema literal does not leak into cli scope.
- `tests/test_fp_conformance.py` (new) — 17 tests, red-first, §15.7 subset.
- `tests/fixtures/cli_surface.json` — regenerated via the sanctioned
  `python -m tests.test_fp_cli_snapshot` (single additive `qc conformance` row,
  114 → 115).
- `build/delivery.py` — **untouched**. The additive `technical:` block rides on
  the existing `delivery_profiles` entry for free (project.yaml `extra=allow`;
  `_delivery_profiles` returns the value dict as-is), so no delivery.py edit was
  needed (Discipline: ADDITIVE ONLY / release code untouched — 0 edits).

## (a) Check coverage vs §7.2 (implemented / UNKNOWN-honest / deferred)

Row = `{check, artifact, status, expected, observed, detail}`. Every technical
target field is OPTIONAL → an absent field is `NOT_APPLICABLE` (never a silent
PASS). Video/container/audio facts come from the stored technical profile keyed
by the artifact's exact sha256 (`read_profile`); absent/tampered profile →
`UNKNOWN`.

| §7.2 check | implemented as | UNKNOWN when | N/A when |
|---|---|---|---|
| container | `container` vs `facts.container.format_name` (brand-list membership) | no stored profile / format unknown | `technical.container` absent |
| codec (video) | `video_codec` vs `facts.picture.codec_name` | no profile / codec `"unknown"` | `technical.video.codec` absent |
| resolution | `resolution` — `max_width/height` ceiling **or** `width/height` exact vs `facts.picture.coded_*` | no profile / coded dims unknown | none declared |
| PAR | `pixel_aspect_ratio` vs `facts.picture.sample_aspect_ratio.normalized` (exact) | no profile / SAR unknown | absent |
| fps / timebase | `frame_rate` vs `facts.time.rate` via **`core.timebase.Rate`** exact rational compare (24000/1001 ≠ 24) | no profile / rate `"unknown"` | absent |
| color + range | `color` 4-tuple (primaries/transfer/matrix/range) vs `facts.color.*` | `color_known` false (colour genuinely unknown — detail says so) | `technical.video.color` absent |
| pix_fmt / bit-depth | `pix_fmt` vs `facts.picture.pix_fmt` | no profile / unknown | absent |
| audio codec | `audio_codec` vs `facts.audio.codec_name` (FAIL if no audio stream) | no profile | absent |
| sample rate | `audio_sample_rate` vs `facts.audio.sample_rate` | no profile / unknown | absent |
| channel layout | `audio_channel_layout` vs `facts.audio.channel_layout` | no profile / unknown | absent |
| loudness | `loudness` — masters index `integrated_lufs` vs min/max **or** target±tol | no masters measurement | no `audio.loudness` integrated target |
| true peak | `true_peak` — masters index `true_peak_dbtp` vs `true_peak_max_dbtp` ceiling | no masters measurement | ceiling absent |
| caption files | `captions` — each required fmt has a non-MISSING `CAPTIONS_<FMT>` row on disk | (unrecognised fmt) | `technical.captions.required` absent |
| checksum | `checksum` — per on-disk artifact, recompute `sha256` vs manifest | bytes unreadable | no on-disk artifacts with hashes |
| file naming / package structure | `package_structure` — every non-MISSING manifest row is a real file on disk | — | no delivered files |
| rights / disclosure | `disclosure` — **REUSES the platform hand-off disclosure verdict** (PENDING_HUMAN → UNKNOWN; credential-leak → FAIL) | hand-off has no disclosure verdict | no platform hand-off |
| unresolved issues | `unresolved_issues` — any manifest diagnostic `severity==blocking` ⇒ FAIL (lists codes) | — | always evaluated |

Deferred (declared NOT in the addendum's `technical:` schema, so surfaced as
NOT_APPLICABLE rather than invented): codec **profile/level**, **bitrate**,
explicit **bit-depth** target row (bit-depth facts exist on the profile but no
target field is in the schema this loop). These need target fields the schema
does not yet carry; adding them is additive future work, not guessed here.

## (b) Built-in presets (`builtin_technical_presets()` → deep copies)
Versioned DATA the user COPIES into a `delivery_profiles` entry — never silently
applied; platform-volatile limits are not hard-coded (§7.1 profiles stay
updatable + versioned):
- **web-1080p** — mp4/h264, ≤1920×1080, yuv420p, PAR 1:1, bt709/tv, aac 48k
  stereo, loudness −14 LUFS ±1 / TP −1.0 dBTP, captions [srt], checksums.
- **social-vertical** — mp4/h264, 1080×1920 exact, 30/1, yuv420p, bt709/tv, aac
  48k stereo, loudness −14 ±1 / TP −1.0, captions [srt], checksums.
- **archive-mezzanine** — mov/prores, yuv422p10le, bt709/tv, pcm_s24le 48k
  stereo, checksums (no captions required).

## (c) CLI + snapshot regen
`manju qc conformance [--profile <id>=master] [--metadata <file>] [--json]
[--write]`. Builds the manifest for `--profile`, reads that same profile's
`technical:` block, runs conformance; `--write` materialises
`reports/conformance/<profile>.delivery-conformance.json` and appends a
`delivery_conformance` event. Human output prints the status-count summary
("(no single score)") + colour-coded rows; `--json` emits the full doc.
Snapshot regenerated with `python -m tests.test_fp_cli_snapshot`; diff is one
additive block (no required params — `--profile` defaults to master):

```
"qc conformance": { "exists": true, "params": [] }
```

## (d) Deliverables map
1. Declarative technical targets — additive `technical:` on `delivery_profiles`
   (no delivery.py change) + 3 built-in presets via `builtin_technical_presets()`.
2. `build/conformance.py::check_delivery_conformance` — the
   `manju.delivery-conformance/v1` document (per-check rows, counts-only summary).
3. `write_conformance_report`/`read_conformance_report` — deletable, tamper ⇒
   rejected on read, never a build/release input.
4. CLI `qc conformance` + regenerated snapshot.
5. `tests/test_fp_conformance.py` (17, red-first).
6. This report + the registry row below.

## (e) Full-suite counts
Targeted runs only (definitive suite is orchestrator-owned):
`tests/test_fp_conformance.py` **17 passed**; with
`tests/test_fp_cli_snapshot.py` + `tests/test_c13_delivery.py` **58 passed**.
No existing test edited (as expected: **0**). No existing test deleted/weakened.

NOTE for the orchestrator: adding my schema literal makes
`test_fp_contracts.py::test_every_src_schema_literal_is_registered` /
`::test_registry_schema_ids_match_src_exactly` RED until the row below is
applied — the intended registration flow. (A sibling loop's
`manju.relink-plan/v1` is also currently unregistered; that is not mine.)

## (f) Deviations + registry row
Deviations: **none of substance.** CLI placed at `qc conformance` (addendum's
"under qc app" option) rather than the non-existent `export` group — matches the
sibling `qc tech` precedent; `delivery.py` needed **0 edits** (pass-through was
free). Loudness/true-peak read the `RAW_STEM_SUM` role from the masters index
(then a loudnorm master, then any measured role) — deterministic selection.

Registry row (paste under `schemas:` — I do NOT edit CONTRACTS.yaml):

```yaml
  - id: manju.delivery-conformance/v1
    kind: schema
    owner: manju.build.conformance
    status: experimental
    latest_version: 1
    read_older: true
    write_older: false
    notes: >-
      Per-check delivery conformance report (PASS/FAIL/UNKNOWN/NOT_APPLICABLE)
      of a delivery manifest against a declarative, additive delivery technical
      profile (container/codec/resolution/PAR/fps/color/audio/loudness/true-peak/
      captions/checksum/package/disclosure/unresolved). No aggregate score;
      deterministic; deletable projection under reports/conformance/; never a
      build or release input (release paths do not import it — grep-pinned).
```
