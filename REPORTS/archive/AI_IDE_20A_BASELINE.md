# AI_IDE_20A — Baseline

Contract: `Manju_AI_IDE_20_Golden_Corpus_Reviewer_Calibration_and_Fault_Harness_Compact_v2.md`
Slice: **20A only** — the minimal pre-15 base (synthetic visual calibration set +
deterministic bad-media generator + fake `qc_vision` reviewer & disagreeing twin +
corpus self-tests). 20B (calibration reports, provider cards, story corpus,
cross-platform) is explicitly out of scope.
Date: 2026-07-11 · Repo: `utdse15-byte/test` (Manju), branch
`claude/cost-optimization-strategy-cjfmn5`.

## Discipline / boundaries

- **0 production files.** Everything lands under `tests/`. No change to
  `DECISIONS.md` / `README.md`. No commit/push.
- A **parallel agent (C14)** owns `src/manju/providers/` and `src/manju/cli.py`
  and is editing them concurrently in this shared working tree — I never touch
  them. At baseline its in-flight changes are already present as
  uncommitted edits (`cli.py`, `providers/manifest.py`, new
  `providers/qualification.py`, `tests/fixtures/canary/`, `test_c14_qualification.py`).

## Environment (all present — offline-capable)

| tool | version |
|------|---------|
| ffmpeg / ffprobe | 6.1.1 (libx264, aac, pcm_s16le; drawtext, blackdetect, freezedetect, concat, adelay, sine, testsrc2) |
| Pillow (PIL) | 12.3.0 |
| Python | 3.11.15 |
| pytest | 9.1.1 |
| font | `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` |

## Existing plumbing 20A builds on (read, not modified)

The DR02 bound-evidence **packet / verdict / assurance** stack already exists and
is the "real plumbing" the fake reviewer must exercise:

- `src/manju/qc/agent_review.py` — `qc_brief()` issues content-addressed **v2
  packets** (`manju.qc.packet/v2`, `pkt_<12hex>`) per shot carrying `expectations`
  + `media.sha256` + `spec_hash` + `expectation_digest`; `record_verdicts()` →
  `_record_verdicts_v2()` intakes **v2 verdicts** (`manju.qc.verdict/v2`);
  `read_v2_records()` reads them back. Observation enum
  `OBSERVED_STATES = (present, absent, uncertain, not_evaluated)`; finding levels
  `LEVELS = (blocker, issue, fyi)`. This **is** the AI_IDE_15 §6 observation shape.
- `src/manju/qc/expectations.py` — `compile_expectations()` derives
  `{id, polarity(present|absent), check, statement}` from a shot's
  `quality.must_show` / `quality.avoid` / `continuity.locks`.
- `src/manju/qc/assurance.py` — `diff(expectations, observations)` is the pure
  verdict truth table (present+present→PASS, present+absent→FAIL, present+uncertain/
  missing→UNKNOWN, …); `compute_assurance()` yields `accepted / rejected / unknown / …`.
- `src/manju/qc/content.py` — `vision_provider_id()` returns the first
  `type: "vision"` provider manifest (the **qc_vision slot** the fake reviewer
  fills); `black_detected()` / `freeze_detected()` are the production ffmpeg
  detector gates.
- `src/manju/providers/manifest.py` — `load_manifests()` scans
  `providers_dir()` (overridable via **`MANJU_PROVIDERS_DIR`**);
  `ProviderManifest` accepts `type: vision` and extra fields (`ManjuModel`
  `extra="allow"`); a non-REST `adapter` needs no submit/poll and yields zero
  `validate_for_generic()` problems.
- `src/manju/media/probe.py` — `probe()` → `ProbeInfo(duration_ms, width, height,
  fps, has_audio)`; the probe-fact oracle for generated media.
- Fixture convention: `from tests.fixtures.<pkg> import …` (root on `sys.path`
  via `tests/__init__.py`); shared `tmp_project` / `add_shot` fixtures in
  `tests/conftest.py`.

## Baseline test state (pre-20A)

- Full collection: **2803 tests collected** cleanly.
- Plumbing subset I integrate with — green:
  `test_dr02_intake / test_dr02_assurance / test_dr02_expectations /
  test_content_qc / test_promptlab` → **86 passed, 4 skipped** (skips are
  network/live-probe guarded, unrelated to 20A).

## Design decisions taken into implementation

1. **Committed vs generated.** Visual set = tiny committed PNGs, **sha256-pinned**
   in the manifest (deterministic, cross-version stable because the bytes are
   frozen in git). Bad media = **generated into a session tmp dir** and validated
   by **probe facts** (ffprobe duration/fps/streams/size), never an output byte
   hash — ffmpeg's encoded bytes are not cross-version stable, so pinning one
   would be a false regression (contract §8). Only the *generator* is committed.
2. **Fake reviewer table keyed by media sha256**, falling back to a declared
   default → the qc_vision slot with zero network; emits the live
   `manju.qc.verdict/v2` observation shape so 15 consumes it unchanged.
3. **Manifest is the single source of truth** for pinned hashes + reviewer
   stances; `fake_reviewer.py` hard-codes no hash. Manifest lives entirely under
   `tests/fixtures/golden/`; runtime code must never read it (asserted).
