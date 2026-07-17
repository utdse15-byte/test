# AI_IDE_07C Completion

Approved release baseline + composed release assessment. One real gap filled; no
parallel objects, no new schema/store/event-log/command-group, no local model.

## Current owners reused
- compare: `build/compare.py:compare_finals` — the ONLY final-regression engine;
  `baseline.compare_against_baseline` calls it (baseline = `a`, candidate = `b`),
  never re-derives the diff.
- exports: `build/exportstatus.py:deliverables` / `deliverables_data` — the ONLY
  delivery-status owner; the assessment reads its rows for final + required-export
  freshness and embeds `release_assessment` additively in the same payload.
- verification log: `reports/verifications.jsonl` via `exportstatus`
  (`VERIFICATIONS_FILE`, `mark_verified`, `_latest_verification`). The baseline
  event is a new `kind` INSIDE this envelope — no second log.
- QC / run / tasks: `qc/assurance.py:assurance_for_all` (tri-state qc),
  `build/attempts.py:build_run_manifest` (INCOMPLETE honesty),
  `runtime/state.py:unresolved_submissions` + `providers/submission.py:verify_chain`.
- ToolPolicy: `mcp/policy.py` (`TOOL_DEFS` policies) drives every next-action's
  `fix_owner / safe_to_auto_run / requires_confirmation / may_network / may_spend`.

## Baseline event
- existing envelope: `reports/verifications.jsonl` (append-only human verification
  log). New record `kind="release_baseline_approved"`, `target="final"`.
- exact byte binding: `artifact = {path, sha256, bytes, final_key}` +
  `source_revision`, `run_id`, `assurance_digest`, `actor:{kind:human}`, `reason`,
  `supersedes_event_id`. `event_id` = canonical hash of the payload (self-verifying).
- approval surface: human CLI `manju exports --approve-baseline [--final F] --reason
  "…" [--accept-known-risk]`. Pre-checks run the SAME technical gate as the
  assessment; blockers refuse by default; `--accept-known-risk` (human-only) records
  the blockers + reason into the event. Durable append (write→flush→fsync, rollback
  on failure) — a failed durable write is NEVER reported as success.
- unattended denial: `approve_baseline(unattended=True)` raises; and there is NO
  MCP approval tool at all, so the unattended surface structurally cannot approve.
  A planted project-source "approved" flag is inert (only the log is read).
- corruption/damage behavior: latest event with missing artifact binding →
  `CORRUPT`; recorded bytes absent or hash-mismatched → `DAMAGED` (never re-points
  at a newer final); malformed JSON line skipped, never swallows valid events.

## Release assessment
- composed services: exports status + baseline resolver + compare-vs-baseline + QC
  assurance + run manifest + unresolved-submission query + final key/hash check.
  Instant, read-only, deterministic (no wall-clock fields), materializes nothing.
- blocker codes (all §7.3, implemented + evidence source):
  `CURRENT_FINAL_MISSING / STALE / HASH_MISMATCH` (exportstatus key + sidecar
  `output_sha256`); `RUN_INCOMPLETE` (build_run_manifest `terminal_status`);
  `ATTEMPT_EVIDENCE_CORRUPT` (verify_chain false / RECOVERY_EVIDENCE_CORRUPT);
  `SUBMISSION_OUTCOME_UNKNOWN` (unresolved rows);
  `SUBMISSION_RECOVERY_UNAVAILABLE` (manifest failure code
  `submission_recovery_unavailable`); `QC_UNAVAILABLE / STALE / REJECTED`
  (assurance `qc.status` + `assurance_state`); `REQUIRED_EXPORT_MISSING / STALE /
  PROBLEM` (deliverables rows); `BASELINE_DAMAGED / EVIDENCE_CORRUPT` (resolver);
  `REGRESSION_REVIEW_REQUIRED` (candidate sha ≠ baseline sha).
- first-release / no-baseline behavior: `NO_BASELINE` is NOT a blocker — a clean
  first release is `ready:true` (test 23).
- regression review behavior: VALID baseline + changed candidate bytes →
  `regression_review.status = CHANGED_REQUIRES_REVIEW` + a non-hard
  `requires_human_review` blocker → `ready:false`; identical bytes → `UNCHANGED`.
- next_actions: one per actionable blocker, mapped to an existing command; safety
  READ from ToolPolicy; CLI-only actions (approve/review/tasks) → `fix_owner:human,
  safe_to_auto_run:false`. Never executes; proposal/confirm reference only.

## No-duplication proof
- no RebuildImpactSet: impact/explain untouched; assessment references, never copies.
- no RegressionComparison store: `compare_against_baseline` returns the live
  `compare_finals` dict + additive keys; nothing persisted.
- no Readiness subsystem: `ready` is a pure boolean over the composed blockers.
- no NextActionSet store: `next_actions` derived on read from blocker codes.
- no PreviewLadder schema: not built (the ladder stays documentation/skill flow).

## Verification
| command | result |
|---|---|
| `pytest tests/test_c07_baseline.py` | 30 passed (red-first → green) |
| `pytest test_p0_* test_compare test_export_center` | 67 passed (pre-gate + composed subsystems, no regression) |
| full suite `python -m pytest` | **2652 passed, 12 skipped, 0 failed** (839s, exit 0) |

FULL-SUITE: 0 unexplained failures. The run included the concurrent C08/10/12
work in-tree and still went fully green; 07C's 30 tests are part of the 2652.

## Public schema and file budget
- new public schemas: **0** (baseline event is a `kind` inside the existing
  verification envelope; assessment is an additive section in the exports JSON).
- production files changed: **3** —
  `src/manju/build/baseline.py` (new; baseline event + resolver + assessment +
  against-baseline compare + next-actions),
  `src/manju/build/exportstatus.py` (+14/−5: additive `release_assessment` in
  `deliverables_data`),
  `src/manju/cli.py` (+125: `exports --baseline/--approve-baseline/--final/
  --reason/--accept-known-risk`; `compare --against-baseline/--candidate`).
- tests (uncapped): `tests/test_c07_baseline.py` (30 tests, §9 1–29 + CLI + the
  unattended/planted-flag negatives).
- NOT touched: `DECISIONS.md`, `README.md` (orchestrator-owned). No commit/push.
