# AI_IDE_08_10_12C Completion

Contract: Accepted Take Production Loop (replaces 08R/10R/12R v2). Base HEAD
5a7aa28 on `claude/cost-optimization-strategy-cjfmn5`. Baseline audit:
`REPORTS/AI_IDE_08_10_12C_BASELINE.md`. All work red-first; nothing committed.

## Baseline and existing owners

| Capability | Decision | Code/test evidence |
|---|---|---|
| ShotSpec/Bible/quality/continuity sources | ALREADY_IMPLEMENTED (reused) | baseline table 1-4; §13.1 proof tests |
| Prompt workbench (4 prompts, one projection) | ALREADY_IMPLEMENTED (extended additively) | `providers/prompt.py`, `build/promptlab.py`; bundle keys unchanged, `test_c081012_prompt_checks.py::test_bundle_carries_production_checks_and_trace_additively` |
| Deterministic prompt checks | PARTIAL → closed | `qc/prompt_checks.py` (was 3 codes; +11 production codes) |
| Refs resolution/lineage | PARTIAL → closed (transfer fields) | `providers/refs.py` dict-form binding; red: dict entry degraded to stringified garbage path pre-change |
| GenerationRequest / preflight / request digest | ALREADY_IMPLEMENTED (called, not duplicated) | `_capability_checks` CALLS `providers/preflight.check_request_compatibility`; PROVIDER_CAPABILITY_MISMATCH emitted from its verdict only |
| Take sidecar provenance | PARTIAL → closed (`redo_of` only) | red: `test_c081012_candidates.py::test_redo_records_lineage_on_sidecar` failed pre-fix (redo emits no attempt evidence — `providers/registry.py:234-241` — and no sidecar lineage existed) |
| Attempt/submission evidence | ALREADY_IMPLEMENTED (joined, not extended) | family join reads `build/attempts.read_attempts` outputs sha256 + request_digest |
| Verdict v2 / assurance / packets | ALREADY_IMPLEMENTED (extended additively, Path A) | `qc/agent_review.py` `_prepare_v2_record` |
| Consistency brief / coverage | ALREADY_IMPLEMENTED (untouched) | `qc/agent_review.py` |
| Ingest/import external candidates | ALREADY_IMPLEMENTED — reused as-is | `build/ingest.py` dry-run/confirm/dedupe; no promotion engine built (§8.5); external-candidate flow covered by existing ingest suites (`tests/test_ingest*.py`) — SKIPPED_WITH_EVIDENCE for new code |
| select/rollback/repair/redo | ALREADY_IMPLEMENTED — mapped to, never executed | `qc/production.repair_route`; spend gate red-tested in `tests/test_ask_before.py::test_redo_is_gated_too` |
| Director proposal path | ALREADY_IMPLEMENTED — the only creative-write path | locked/stale rejection already red-tested: `tests/test_director.py:126-146`, `tests/test_locks.py`, `tests/test_write_locks.py` |
| Board comparison UI additions | SKIPPED_WITH_EVIDENCE | contract 12.3 trigger is "board lacks a real comparison entry"; board HAS one (`board/board.py:254-264` side-by-side compare + per-take verdict badges). Family/disposition data is exposed via qc brief JSON instead |
| Local models/ComfyUI DAG/face-rec/embeddings | REJECTED_WITH_REASON | contract §4.3/§15 hard stops |

## Source authority proof
- Directing output path: Skill (`direct-shot-source-patch`) → narrow patch/proposal on existing Shot/Bible/refs; forbidden path (`reports/directing/*.json` as engine input) named as forbidden in the skill and proven inert.
- Accepted source patch path: `update_shot_raw`/director propose→confirm→execute (existing, untouched).
- Proof derived reports do not affect build: `tests/test_c081012_source_authority.py` — mutating `reports/directing/S010.json` and a continuation capsule under reports/, and even deleting reports/ entirely, leaves spec_hash, all four prompts, prompt_bundle_digest and the provider request digest byte-identical; applying the same decision as a source patch moves all of them.

## Prompt/refs
- Existing workbench reused: `manju prompt --json` remains the ONE projection; `shot_prompt_bundle` gained two ADDITIVE keys (`production_checks`, `compiler_trace`); every legacy key byte-compatible.
- New checks (all in `qc/prompt_checks.py`, pure/deterministic/proposal-only, `auto_apply: false`, each with `source_paths`):
  CLIP_SCOPE_MULTIPLE_COMPLETED_ACTIONS, CLIP_SCOPE_FUTURE_BEAT_LEAK,
  CLIP_SCOPE_ENDPOINT_MISSING_FOR_CONTINUATION, REFERENCE_ROLE_AMBIGUOUS (advisory),
  REFERENCE_TRANSFER_UNDECLARED (advisory — old refs stay compatible),
  REFERENCE_CONTROL_CONFLICT (warning; unknown enums never silently accepted),
  REFERENCE_SOURCE_STALE, SURFACE_PROFILE_STALE, SURFACE_PROFILE_UNKNOWN,
  PROMPT_BUDGET_EXCEEDED, PROVIDER_CAPABILITY_MISMATCH (by CALLING DR04 preflight).
  CONTINUATION_* (3 codes) derive in `qc/production.py` and join `manju prompt --check` through `check_all`.
- Ref additive fields: dict-form binding entry `{ref, controls[], ignore[], subject_ref}` on the EXISTING params/shot refs lists (RefItem +5 fields, closed vocabulary `REF_TRANSFER_VOCAB`). In-digest proof: fields ride `generation.params` → spec_hash moves on any edit (`test_transfer_fields_enter_spec_hash_and_trace`). No ReferenceTransferContract store.
- Surface profile data: ONE conservative family (`generic-video-v1` current / `generic-video-v0` superseded) + UNKNOWN fallback, py-literal source-dated table in `qc/prompt_checks.py`; explicit opt-in only (`generation.params.surface_profile`) — no brand guessing, no network; digest + evidence_date + freshness in `compiler_trace.surface_profile`; profile-pin change moves spec_hash (test). Undeclared → honest UNKNOWN freshness in trace, non-blocking.

## Candidate/provenance
- Family derivation (`qc/production.candidate_families`): creative source revision (root `spec_hash` after cycle-safe `redo_of` chain walk) ⇒ family; attempt evidence join (output sha256 → request_digest + candidate_index) ⇒ per-request grouping inside; manual takes stay honest singletons. Never filename/shot_id/mtime grouping. View is pure/rebuildable/zero-write (tested).
- Sidecar/evidence fields added: `TakeSidecar.redo_of` ONLY (red-proven; request_digest/submission_id/attempt_id/candidate_index/seed already joinable from existing attempt+submission evidence — audited, not duplicated). Plumb: `GenerationRequest.redo_of` → `Provider._register` → stamped by `redo_shot --from-take`.
- keeper/selected/ref separation: `review_disposition`/`keeper` (bound evidence) vs `selected` (Shot source) carried side-by-side in the view; KEEP never auto-selects (`test_keep_does_not_auto_select`, `test_keeper_selected_and_disposition_separation`).
- Ingest reuse: unchanged existing ingest/import path; no new external-candidate code (see baseline).

## Bound review
- Existing Verdict additive (PATH A — no new public schema): optional `decision {disposition, primary_repair_variable, diagnostic_isolation, reason}` + `observed_states [{dimension, position START|END, value, visibility, evidence_refs, confidence}]` on `manju.qc.verdict/v2`. Validated in the SAME `_prepare_v2_record` errs path: malformed ⇒ whole-batch reject, ZERO writes. Legacy payloads without them intake byte-identically.
- Five dispositions: KEEP / FIX_IN_POST / EDIT_DONT_REGENERATE / REROLL / REWRITE_SOURCE (parametrized test).
- One-variable rule: non-KEEP requires exactly one `primary_repair_variable` from the 17-token vocabulary; multi-variable tries carry `diagnostic_isolation: false` (stored, never inferred).
- Media replacement stale proof: `test_media_replacement_stales_decision_record` (decision record stored binding-stale, bound to bytes A); reviewer cannot write assurance: KEEP on a FAILED expectation still derives `rejected` (`test_decision_never_writes_assurance_acceptance`) — acceptance remains 02's pure function; QC-unavailable still cannot accept (P0 pins remain green).

## Continuity and continuation
- Authored state source: Bible/Shot continuity locks only (carried as `authored_locks`, separate from transient observations; nothing inferred from pixels).
- Observed state projection: `accepted_observed_state` — current only when assurance=accepted for the CURRENT selected bytes AND the observation record is still current-bound; any binding move empties it (never fail-open) (`test_observed_state_goes_stale_on_media_replacement`); never written back (tree-hash test).
- Source gate: `continuation_view` fires CONTINUATION_SOURCE_NOT_ACCEPTED / CONTINUATION_SOURCE_HASH_MISMATCH (optional authored pin `continuity.source_media_sha256`) / CONTINUATION_ENDPOINT_UNOBSERVED; all three surface in `manju prompt --check` (blocking) and in `compiler_trace.continuation_source`.
- Proof continuation only creates source proposal: view carries `do_not_execute_automatically: true`; build/prompt digests unaffected by its existence (§13.1 test 4); real reviewed ending ("她仍低头看着硬币") carried instead of the authored prompt ending (`test_continuation_view_carries_real_ending_not_prompt_ending`). Chain re-anchor is an advisory boolean (depth ≥3), never an automatic ref change.

## Skills

| Skill | Inputs | Output | Eval |
|---|---|---|---|
| direct-shot-source-patch | Shot/Bible source, prompt bundle diagnostics, user intent | narrow source patch/proposal + beat/endpoint/camera explanation; never generates | front-matter/discovery/section/field-mapping evals (`test_c081012_skills.py`) |
| review-take-and-route-repair | qc brief packet, family view, deterministic QC, cost evidence | bound observations + disposition + one primary variable + endpoint observations; never select/redo/patch | vocabulary drift-catchers: dispositions/variables/visibility enums must equal the code's |
| continue-from-accepted-take | accepted media binding, endpoint observation, next-shot source, capabilities | next-shot source proposal + continuation citation + boundaries + re-anchor advice; never calls Provider | gate-code naming eval + no `--yes` spend pre-authorization eval |

Data packs (one-variable map, disposition table, transfer-binding phrasing) are embedded, source-dated by the skill file, and pinned to the code vocabularies by tests.

## No-duplication proof
- no DirectingBrief runtime schema: reports/ inert (proof tests); skill outputs patches.
- no ClipContract runtime schema: decisions land in Shot source; nothing else compiles requests.
- no CandidateSet store: `candidate_families` is derivation-only (zero-write test).
- no RecipeSnapshot store: sidecar (params/compiled_prompt/spec_snapshot/redo_of) + attempt evidence remain the only recipe carriers.
- no ContinuityState store: `accepted_observed_state` recomputed per call, stales with bindings.
- no ContinuationCapsule build input: `compiler_trace.continuation_source` is display/Skill data; digest proofs show build independence.
- no second compiler/QC/registry/queue: extensions live inside `prompt_checks`/`promptlab`/`agent_review`/`refs` owners; preflight called, never re-derived.

## Verification

| command | result |
|---|---|
| `python -m pytest tests/test_c081012_*.py` (6 files) | 61 passed |
| WP4 red (pre-fix) | 13 failed: decision block silently dropped (KeyError), invalid disposition/variable/visibility DID NOT RAISE |
| WP3/WP5 red (pre-fix) | 14 failed: `qc.production` ModuleNotFoundError; redo sidecar had no `redo_of` |
| WP2 red (verified by stashing WP2 diffs) | collection error: `production_checks` ImportError; bundle keys absent |
| targeted regression (prompt/refs/batch/DR02/P0/QC suites) | 259 passed |
| full suite (`python -m pytest`, two halves) | 1002+1664 = 2666 passed, 12 skipped (pre-existing env skips), 0 failed |

## Budget
- new public schemas: **0** (Path A additive on verdict v2; production-decision/v1 not needed — no compatibility break encountered).
- production files changed: **8 / 10** — `qc/prompt_checks.py` (+358/−2), `qc/agent_review.py` (+112/−0), `qc/production.py` (new, 377), `build/promptlab.py` (+63/−13), `providers/refs.py` (+85/−5), `providers/base.py` (+9/−0), `build/graph.py` (+7/−1), `core/models.py` (+10/−0). Uncapped: 3 skills, 6 test files, 2 REPORTS.
- remaining risks / left unwired: (a) surface-profile CONTENT changes move only the trace digest, not the submission request identity (an authored `surface_profile` pin does move spec_hash/request digest via params) — deeper coupling would touch `providers/submission.py` identity semantics, deliberately not spent; (b) exports status/compare/verification modules are owned by a parallel agent this cycle — no wiring of candidate/continuation views into export status was attempted; if wanted later it is a read-only view join; (c) board UI family grouping skipped per the contract's own conditional (evidence in baseline); (d) contract §13.2 items 13/14 covered by architecture reuse (single resolver / preflight call), cited not re-tested.
