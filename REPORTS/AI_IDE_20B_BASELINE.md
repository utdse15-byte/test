# AI_IDE_20B — Baseline

Contract: `Manju_AI_IDE_20_Golden_Corpus_Reviewer_Calibration_and_Fault_Harness_Compact_v2.md`
Slice: **20B** — the post-15–19 expansion of the 20A golden corpus (contract §2:
"20B 在 15–19 后扩充"). Date: 2026-07-11 · Branch
`claude/cost-optimization-strategy-cjfmn5` (clean at start).

## State at 20B start

- **20A landed** (commit 8b0f008): `tests/fixtures/golden/` — 18 sha256-pinned
  visual PNGs + 13 probe-fact technical cases, `manifest.json` (20A.v1, 31
  cases), fake reviewer + twin (7 declared conflicts), 44 self-tests.
- **All five owners landed on top of it:**
  - AI_IDE_14 (2fb939a) — `providers/qualification.py` ladder (UNTESTED →
    CONFIG_VALID → DRY_RUN_VALID → CANARY_* → RECOVERY_PASSED →
    PRODUCTION_READY[real-only]), canary fixtures `tests/fixtures/canary/`,
    scripted-transport canary (`Canned`) in `test_c14_qualification.py`.
  - AI_IDE_15 (8f5b250) — §6 `dimension_observations` on verdict v2
    (`REVIEWER_DIMENSIONS` ×11, `DIMENSION_OBSERVED` match/mismatch/uncertain/
    not_visible, `DIMENSION_SEVERITY` info/warning/blocker),
    `qc.production.reviewer_agreement` (UNKNOWN_REVIEWER_DISAGREEMENT on blocker
    splits), `drift_trend` + `repair_routes` (SEVEN_ROUTES), reviewer admission
    gate (`REVIEWER_NOT_QUALIFIED` below DRY_RUN_VALID). `test_c15_reviewer.py`
    already builds ON the 20A fake reviewer.
  - AI_IDE_16 — preview ladder; AI_IDE_17 — series state/packs (incl. voice
    provenance gate consumer); AI_IDE_18 — masters (stems/M&E, loudness FACTS),
    voiceid provenance gate, localize-dialogue skill; AI_IDE_19 — analysis/
    segments (cutdown validation), reframe (crop/safe-area), roughcut
    (sensitive_terms advisory), bridge admissions.
- **Baseline test state:** 20A + c15 + c14 = 98 passed on entry; full
  collection ~3080 tests, green.

## Integration research (verified, exact)

- **Prompt/Director checks** (`qc/prompt_checks.py`): entry
  `check_shot(project, shot)` + `production_checks(project, shot)`; codes
  `too_many_actions`, `too_many_motion_paths`,
  `CLIP_SCOPE_MULTIPLE_COMPLETED_ACTIONS`, `CLIP_SCOPE_FUTURE_BEAT_LEAK`,
  `CLIP_SCOPE_ENDPOINT_MISSING_FOR_CONTINUATION`, `REFERENCE_CONTROL_CONFLICT`,
  `REFERENCE_TRANSFER_UNDECLARED`, `REFERENCE_SOURCE_STALE` (+surface/budget).
  **No check named "continuation restart"** (nearest: the 3 `CONTINUATION_*`
  gate codes, ffmpeg-gated) and **§9.3 one-variable is verdict-intake
  validation**, not a shot check — both recorded honestly in the corpus.
  Hermetic pitfalls: `MANJU_PROVIDERS_DIR` + `registry._manifest_cache = None`;
  refs need real files; `prompt_override` wins over `action.main`.
- **Delivery/policy**: platform checks = `ASPECT_RATIO`/`DURATION_LIMIT`(always
  PENDING_HUMAN)/`DISCLOSURE_REVIEW`(always PENDING_HUMAN)/`CREDENTIALS_PRESENT`
  + `PLATFORM_CREDENTIAL_LEAK` in `build/delivery.py::_platform_handoff`;
  **codec / loudness-vs-profile checks do NOT exist** (loudness is facts-only in
  `media/masters.py`); format-only = `check_format_only_invariant`
  (`VARIANT_KIND_MISMATCH`); cutdown = `segments.validate_cutdown`
  (`CUT_CROSSES_NO_CUT_ZONE`, `RANGE_INVERTED`, …); crop/safe-area =
  `media/reframe.compile_crop_keyframes` (ok/blanking/needs_manual, rate-limit);
  bundle TOCTOU pinned in `test_final_acceptance.py::test_f5_*` +
  `test_h2_hardening.py::test_24/25`
  (`DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST`); **no brand/claims wordlist
  exists in src** — but `qc/roughcut.rough_cut_proposal(sensitive_terms=…)` is a
  real deterministic annotate-only path a corpus wordlist fixture can drive;
  voice/asset license-provenance = `build/voiceid.template_export_gate` +
  seriespack `rights_missing`. `TEXTLESS_MASTER` is a KNOWN_ROLES member the
  masters renderer does not produce (honest gap).
- **Skills**: four first-party SKILL.md under `skills/`; loader
  `core/skills.py` (`load_skill`/`skill_text`); existing content pins in
  `test_c081012_skills.py` (3 skills) + `test_c18_dialogue_masters.py`
  (localize). Required sections `## 输入/## 输出/## 失败条件/## 纪律`.
- **Cross-platform pins** (existing test ids captured for indexing): CJK paths
  (test_container/test_gitops), BuildLock incl. mid-acquire steal race
  (test_buildlock::test_fresh_corrupt_lock_is_not_stolen), CLI JSON
  backward-compat (test_c0911_gates::test_a10_…), no-LLM-in-core
  (test_c19_orchestration), imports read-only (test_import_dedup), SQLite
  rebuild (test_runtime, test_dr06_recovery), reports-never-build-inputs
  (test_c081012_source_authority, c14 test_15). **CI = ubuntu-latest only, no
  matrix** (.github/workflows/ci.yml) → Windows/macOS rows are
  SKIPPED_WITH_EVIDENCE. **No long-path/MAX_PATH pin exists** (honest gap).
- **Cards inputs** (`providers/qualification.py`): report shape
  (state/level/stale/bindings incl. 4 digests + artifact + cost), evidence
  stores the submission-state sequence in `evidence_refs[].to` and cost under
  `evidence.cost.actual`; `declared_facts` + pure `qualification_state` re-derive
  staleness live.

## Boundaries

- Parallel agent owns the 21G gate audits — `tests/test_c21g*` and
  `REPORTS/AI_IDE_21G*` untouched (none present in the tree at baseline).
- 0 production files expected; the permissible qualification.py view was NOT
  needed (tests express the cards cleanly).
- No commit/push; DECISIONS.md/README.md untouched.
