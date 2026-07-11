# AI_IDE_20A — Completion

Slice: the minimal pre-15 golden-corpus base. **0 production files** — everything
under `tests/fixtures/golden/` + one self-test file. No commit/push; no
`DECISIONS.md` / `README.md` change; `src/manju/providers/` and `src/manju/cli.py`
untouched (parallel C14 agent's territory).

## (a) Corpus inventory

| partition | cases | committed bytes | how validated |
|-----------|------:|----------------:|---------------|
| visual_continuity | 18 committed PNGs | 8,114 B | sha256-pinned (byte-exact) |
| technical_media | 13 generated cases | 0 (generated to tmp) | ffprobe **probe facts** + detectors |
| **committed golden tree total** | | **80,489 B** (< 300 KB budget) | |

Visual dimensions (AI_IDE_15 visual-continuity axes): identity match/mismatch
(3: ref/match/mismatch), wardrobe drift (2), prop drift (2), scene+lighting
change (2), axis/screen-direction flip (2), action phase (2), style/palette shift
(2), not-visible/occluded (1), ambiguous/low-signal (2). Each PNG 96×96 (≤128px),
a distinct sha256 (a per-case deterministic corner "slate" makes the shared
canonical charA pose a genuinely distinct capture).

Technical fault family (deterministic ffmpeg lavfi, fixed args, no wall-clock):
good / short / black-head+tail / freeze / bad-aspect / bad-fps /
corrupt-container / silent / clipped(0dBFS) / audio-drift / subtitle-overlap(.ass)
/ text-mutation(2 frames). Media is **generated into a session pytest tmp dir**;
only the generator (`make_bad_media.py`) is committed.

## (b) Manifest schema (`tests/fixtures/golden/manifest.json`, `manju.golden_corpus/20A`)

Top level: `schema, version, rubric_version, annotator, generated, license_note,
design_notes[], partitions{visual_continuity,technical_media}, fake_reviewer_defaults{primary,twin}, cases[]`.

Every case carries: `id, partition, dimension, kind, license(="self-made-synthetic"),
rubric_version, annotator(="orchestrated-synthetic-v1"), expected_observations[],
blocking_policy(blocking|advisory|non_blocking), repair_route, rationale`.

- **expected_observations** = the §6 shape `{dimension, observed ∈ OBSERVED_STATES,
  severity ∈ LEVELS|null, allow_unknown:bool, note}` (the real live enums).
- **committed_image** cases add `path`(relative POSIX), `sha256`(bare hex),
  `bytes`, and `reviewer:{primary,twin}` stances (`{present, absent, severity,
  message}` — the observed state emitted per expectation polarity).
- **generated_media** cases add `generator, generator_case, validation
  (ffprobe | ffprobe+detector | ffprobe+audio_start | probe_fail | ass_overlap |
  distinct_frame), probe{…probe-fact expectations…}` — never a byte hash. A
  `design_notes` entry states this honestly.

## (c) Fake reviewer + twin (`tests/fixtures/golden/fake_reviewer.py`)

`FakeVisionReviewer(profile)` — a deterministic, offline TEST DOUBLE keyed by
input media **sha256** (prefix-normalized), falling back to the manifest's
`fake_reviewer_defaults` for unregistered media (UNKNOWN, never a silent pass):

- `observe(sha256)` → §6 observation list `[{dimension, observed, severity,
  allow_unknown, message}]` for calibration scoring vs `expected_observations`.
- `build_verdict(brief_row)` → a live `manju.qc.verdict/v2` payload: per-packet-
  expectation `observations[{expectation_id, observed}]` chosen by the
  expectation's polarity, `findings[{level,message}]` from severity, and a
  `reviewer{kind, name, profile_digest}` block. Echoes the packet's binding
  fields exactly, so DR02 intake **binds** (never re-stamps).
- `verdicts_for_brief(brief)` → whole-brief batch payload.
- `install_manifests(dir)` → copies the two committed vision `provider.yaml`s
  (`qc_vision_fake`, `qc_vision_fake_twin`, `type: vision`, non-REST test adapter)
  into a `MANJU_PROVIDERS_DIR` tree; they load through the **real registry** and
  `vision_provider_id()` returns `qc_vision_fake`.

**Twin** (`qc_vision_fake_twin`): distinct `profile_digest`, a table with **7
declared conflicts** (identity match, identity mismatch, wardrobe drift, scene
lighting, style gradient, ambiguous ×2) → false passes / a **missed blocker**
against ground truth, for 15's disagreement-rate / multi-reviewer tests.

**How AI_IDE_15 invokes them** (proven in the self-tests):
`install_manifests(dir)` + `MANJU_PROVIDERS_DIR` → build a project, author a shot
`quality.must_show`/`avoid`, `register_take(golden PNG)` + select →
`qc_brief(project)` → `reviewer.build_verdict(row)` → `record_verdicts(project,…)`
→ `read_v2_records` / `compute_assurance` (or `diff`). For calibration: compare
`reviewer.observe(sha)` to the case's `expected_observations`.

## (d) Self-test results (`tests/test_c20a_corpus.py`)

**44 passed** (5.0s). Coverage:
- manifest schema/enums valid; no duplicate ids; licenses present; **no absolute
  paths** (real drive-letter regex, not a naive `s[1]==':'`); partition counts
  match; blocking/repair tokens known.
- every committed image **sha256 matches** + byte-count matches; images distinct,
  tiny (≤128px) PNGs; visual generation deterministic; **committed tree < 300 KB**.
- generator produces **probe-conformant** media (parametrized ffprobe facts);
  **corrupt-container fails ffprobe**; black/freeze trip the *production*
  detectors; audio-drift carries a real stream offset; subtitle cues overlap;
  text-mutation frames differ; generation facts deterministic.
- reviewer determinism; **twin disagrees exactly where declared**; default
  fallback → UNKNOWN; twin misses the blocker the primary catches.
- **real plumbing end-to-end**: reviewer verdict → `record_verdicts` (bound) →
  `compute_assurance` = **accepted** (match) / **rejected** (mismatch, blocker) /
  **unknown** (ambiguous); avoid-polarity mapping; default fallback on generated
  media; packet binds the reviewed golden bytes; reviewer emits the live
  `manju.qc.verdict/v2` schema.
- ruling: `src/manju` **never references** the golden corpus (grep-asserted).

Integration (my file + the plumbing I build on — `dr02_intake/assurance/
expectations`, `content_qc`, `promptlab`, `dr06_admission`, `providers_routing`):
**200 passed, 4 skipped** (skips are network/live-probe guarded, pre-existing).

## (e) Full-suite result

`python -m pytest` (whole repo): **2861 passed, 12 skipped, 0 failed** in 926s
(15:26), exit code 0 — the +44 C20A tests plus the pre-existing suite and the
parallel C14 agent's tests, all green. The 12 skips are network/live-probe
guarded (pre-existing, unrelated to 20A).

## (f) Deviations / notes

- **Hash prefix reconciliation.** `core.hashing.hash_file` (and every packet's
  `media.sha256`) carries a `sha256:` prefix; the manifest stores the
  conventional bare `hashlib` hexdigest (the form the self-test verifies against
  committed bytes). The reviewer normalizes the `algo:` prefix on lookup. No
  contract impact.
- **Detector-threshold calibration** (empirical, not arbitrary): `freeze` is 3s
  and `black_head_tail` uses 1s black head/tail so the *production* gates
  (`freezedetect d=2`, `blackdetect d=1.0`) actually fire — the fault harness
  exercises the real detectors.
- **No production gap found.** 20A needed zero `src/` change; the qc_vision slot,
  packet/verdict/intake, and `MANJU_PROVIDERS_DIR` registry all accept the double
  unchanged.
- **Shared working tree.** The parallel C14 agent's uncommitted edits
  (`src/manju/cli.py`, `src/manju/providers/manifest.py`,
  `src/manju/providers/qualification.py`, `tests/fixtures/canary/`,
  `test_c14_qualification.py`) coexist in this tree; not mine, not touched. Its
  `manifest.py` additions do not affect `type:vision` / extra-field loading, so
  the fake-reviewer manifests are unaffected.

## (g) REPORTS paths

- `REPORTS/AI_IDE_20A_BASELINE.md`
- `REPORTS/AI_IDE_20A_COMPLETION.md`

## Deliverables (all under `tests/`)

```
tests/fixtures/golden/__init__.py            package marker + dir/manifest locator
tests/fixtures/golden/make_visual.py         PIL synthetic visual set generator (recipe)
tests/fixtures/golden/make_bad_media.py      deterministic ffmpeg bad-media generator
tests/fixtures/golden/fake_reviewer.py       fake qc_vision reviewer + disagreeing twin
tests/fixtures/golden/manifest.json          versioned corpus manifest (31 cases)
tests/fixtures/golden/visual/*.png           18 committed sha256-pinned PNGs
tests/fixtures/golden/providers/qc_vision_fake/provider.yaml       primary vision manifest
tests/fixtures/golden/providers/qc_vision_fake_twin/provider.yaml  twin vision manifest
tests/test_c20a_corpus.py                    the corpus self-tests (44)
```
