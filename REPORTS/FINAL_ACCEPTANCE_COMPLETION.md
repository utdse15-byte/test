# FINAL_ACCEPTANCE Completion

Base: `f416167` (branch `claude/cost-optimization-strategy-cjfmn5`). Fix
surface: 4 production files, 2 test-fixture files, 1 new test file. No new
public schema, no ledger/DB/queue/worker/lease, no local model. Not touched:
`runtime/buildlock.py` (F4/D fixed by the orchestrator at `f416167`),
DECISIONS.md, README.md.

## Gate verdict

`FINAL_ACCEPTANCE_PASSED` — all 15 contract tests met, verified by the orchestrator against public GitHub CI (the prior sentence's pending state is resolved): —
all local gates below are green; the public-CI gate (contract test 15) is
filled by the orchestrator, never claimed locally:

- CI run: https://github.com/utdse15-byte/test/actions/runs/29151178302
- CI result: SUCCESS — 2803 passed / 0 failed / 0 skipped in 775.56s at HEAD 6d141d9 (Python 3.11.15, ubuntu-latest; CI additionally runs the 12 locally env-skipped tests) + the M0 acceptance smoke (build twice = one final, 24/1 fps, content-key reuse) passed

## Fixes (each red-first; red matrix in FINAL_ACCEPTANCE_BASELINE.md)

### F1 — malformed paid evidence fail-closed
- `runtime/state.py:123` `MalformedSubmissionEvidence`; `:739` the strict
  restore raises on ANY stream-global torn line; `:846-850`
  `ensure_submission_projection` classifies it as
  `submission_recovery_unavailable` stage `evidence_malformed`.
- `providers/base.py:1077-1087` `_guard_chain_integrity` re-checks the
  malformed count on the DB-fast-path (a torn line has no parseable
  submission_id, so the count is stream-global by construction) — covers
  fresh-submit, redispatch AND admitted-resume consults; transport and poll
  both 0.
- `build/baseline.py:576-585` release gate: any torn line ⇒ blocking
  `ATTEMPT_EVIDENCE_CORRUPT` (scope `events`), even with no ledger on disk.
- Unpaid/local work unblocked: no torn lines + no submission events + no
  ledger ⇒ zero submission blockers (pinned).
- Scope ruling as landed: **stream-global** — a torn line could belong to any
  submission, so all paid consults and the release gate block; explicit
  `rebuild()` still restores what parses (the consult re-blocks regardless).

### F2 — run proof
- `build/baseline.py:560` a final whose sidecar has no `run_id` ⇒ blocking
  `RUN_NOT_PROVEN` (reverses the POST_COMPLETION skip: absence IS the signal,
  no sidecar schema marker added). With `run_id`, only
  COMPLETED / COMPLETED_WITH_WARNINGS pass (H1 matrix, re-pinned).
- Legacy/manual escape hatch: ONLY the existing human `--accept-known-risk`
  approval — human-only, binds the exact final sha256 + blocker codes +
  reason, appended to the existing verification log (pinned by
  `test_f2_legacy_final_approval_requires_human_risk_acceptance`).
- c07 `test_23` fixture (`_clean_current_final`) upgraded to a RUN-PROVEN
  final (sidecar run_id + completed run-lifecycle events) — first-release
  ready==True still holds for a proven final; the RUN_NOT_PROVEN case sits
  beside it in `test_final_acceptance.py`. Same upgrade for c13's
  `test_editor_approved_only_after_human_nle_verification` (technical_ready
  consumes the 07C assessment verbatim).

### F3 — final byte proof
- `build/baseline.py:505-519` `_final_health`: sidecar without
  `output_sha256` ⇒ blocking `CURRENT_FINAL_UNVERIFIABLE`; unhashable final
  bytes ⇒ same; recorded≠actual ⇒ `CURRENT_FINAL_HASH_MISMATCH` (unchanged).
- `release_assessment`: `ready` additionally requires
  `candidate.sha256 is not None` — no byte proof, no release.

### F5 — the four P1s
1. Valid baseline + torn line: `current_baseline` carries `malformed_count`
   additively (`baseline.py:212/251` — display status stays what the valid
   event proves); the assessment turns it into blocking
   `BASELINE_EVIDENCE_CORRUPT` (`:808-813`) — a torn line could BE the
   superseding approval.
2. Baseline path escape: `baseline.py:255-262` — an unresolvable approved
   artifact path is `DAMAGED`, never the old `project.root / path` fallback
   (which followed `../` escapes and could read back VALID against foreign
   bytes).
3. NLE project file unverifiable: `delivery.py:405-424` — a registered
   project-file deliverable that is a regular file but unhashable, vanished,
   or path-unresolvable ⇒ blocking `NLE_PROJECT_UNVERIFIABLE`. Directory
   drafts (JianYing/CapCut native folders) keep their per-asset sha256 +
   human-verification axes — they have no single-file byte identity by
   construction.
4. Bundle TOCTOU: `delivery.py:1290-1367` `write_bundle` is now
   stream-hash-once — each member is opened exactly once, hashed in 1 MiB
   chunks WHILE streaming into the ZIP (no whole-file buffering; finals can
   be GBs); the streamed digest is CAS-checked against the manifest
   (`_expected_bindings`, structural pre-check `:1264`); SHA256SUMS is
   generated FROM the streamed digests (`_sha256sums_from` `:1253`) and the
   embedded manifest binds that same text — checksums, manifest and ZIP
   consume ONE byte snapshot. Any drift ⇒
   `DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST`, temp discarded under
   `atomic_output`, prior bundle intact. Reproducibility and
   only-manifest-files pins unchanged (c13 green).

## Tests

Contract tests 1-12 live in `tests/test_final_acceptance.py` (18 tests:
14 were RED at `f416167`, 4 are guardrail pins). Fixture flips, each with an
in-file F2 comment:
- `tests/test_c07_baseline.py::_clean_current_final` (+`_seed_completed_run`)
  and `test_24`'s fab — run-proven finals;
- `tests/test_c13_delivery.py::test_editor_approved_only_after_human_nle_verification`
  — run-proven final.

| Command | Result |
|---|---|
| `python -m pytest tests/test_final_acceptance.py -q` (pre-fix, at f416167) | 14 failed / 4 passed |
| `python -m pytest tests/test_final_acceptance.py -q` (post-fix) | 18 passed |
| targeted sweep (final_acceptance + c07 + c13 + h2 + post_completion + c0911 + export_center) | 183 passed |
| wider sweep (dr06×4, runtime, p0×4, locks, write_locks, cli, evaluate, compare, gui_finish, events_follow, ledger_count) | 224 passed |
| FULL local suite (synchronous, 3 chunks covering all 167 test files) | **2791 passed, 12 skipped, 0 failed** (916+4s / 879+8s / 996+0s; ~803s) |
| GitHub CI (contract test 15 — orchestrator-verified) | SUCCESS — 2803 passed / 0 failed / 0 skipped in 775.56s at HEAD 6d141d9 (Python 3.11.15, ubuntu-latest; CI additionally runs the 12 locally env-skipped tests) + the M0 acceptance smoke (build twice = one final, 24/1 fps, content-key reuse) passed at https://github.com/utdse15-byte/test/actions/runs/29151178302 |

## Completion conditions

- malformed evidence is never read as empty history (F1, consult + gate);
- no run proof / no byte proof ⇒ never ready (F2/F3);
- valid+torn verification log blocks; baseline path never escapes; NLE file
  bindings provable; bundle = one byte snapshot (F5);
- no new fact source, schema, ledger, DB, queue, worker or lease;
- local suite 0 failed (2791/12/0); public CI: SUCCESS — 2803 passed / 0 failed / 0 skipped in 775.56s at HEAD 6d141d9 (Python 3.11.15, ubuntu-latest; CI additionally runs the 12 locally env-skipped tests) + the M0 acceptance smoke (build twice = one final, 24/1 fps, content-key reuse) passed.
