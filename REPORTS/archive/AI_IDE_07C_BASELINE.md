# AI_IDE_07C Baseline — WP0 Audit & Pre-Change Gate

Contract: `AI_IDE_07C` (Approved Baseline & Composed Release Assessment).
Repo HEAD at audit: `5a7aa28` (P0 remediation complete — full suite 0 failed /
2575 passed). Branch `claude/cost-optimization-strategy-cjfmn5`.

Scope discipline honored: production-code files changed = **3** (cap 6), new
public schemas = **0**, new command groups = **0**, new fact sources / stores =
**0**. Red-first: pins + §9 tests written before any production edit.

> Concurrency note: while this batch was in flight a PARALLEL contract
> (C08/10/12 — take continuation, prompt-checks, review routing) was editing a
> DISJOINT set of files in the same worktree (`build/graph.py`,
> `build/promptlab.py`, `core/models.py`, `providers/base.py`,
> `providers/refs.py`, `qc/agent_review.py`, `qc/prompt_checks.py`,
> `qc/production.py`, `skills/*`, `tests/test_c081012_*`). None overlap 07C's
> three files. Failures originating in those files are out of 07C scope.

---

## §4.1 — required subsystems located (all ALREADY_IMPLEMENTED)

| Subsystem | Status | Evidence (file:symbol) |
|---|---|---|
| compare engine + persisted final snapshots | ALREADY_IMPLEMENTED | `build/compare.py:compare_finals`; snapshots `final_vN.timeline.json` via `media/render.py:_write_timeline_sidecar` |
| exports status builder (delivery owner) | ALREADY_IMPLEMENTED | `build/exportstatus.py:deliverables` / `deliverables_data` |
| human verification log + append helper | ALREADY_IMPLEMENTED | `reports/verifications.jsonl`; `exportstatus.py:mark_verified` / `_latest_verification` / `_verifications_path` (`VERIFICATIONS_FILE`) |
| final path/hash/key sidecar | ALREADY_IMPLEMENTED | `media/render.py:_write_key_sidecar` / `_read_key_sidecar` — `final_vN.key.json` = `{final_key, target, created_at, run_id?, output_sha256?, inputs?}` |
| latest-final selection rule | ALREADY_IMPLEMENTED | `core/container.py:newest_final_path` (numeric max, not lexicographic) |
| QC Assurance / current evidence | ALREADY_IMPLEMENTED | `qc/assurance.py:compute_assurance` / `assurance_for_all` — tri-state `qc:{status: pass\|blocked\|unavailable}` |
| RunManifest terminal state (P0) | ALREADY_IMPLEMENTED | `build/attempts.py:build_run_manifest` — `INCOMPLETE` + `dangling_attempts` / `NOT_FOUND` / `legacy_terminal_only` |
| stage-attempt evidence integrity | ALREADY_IMPLEMENTED | `build/attempts.py:read_attempts` (`malformed_lines`); `providers/submission.py:verify_chain` |
| unresolved paid submissions + chain integrity (P0) | ALREADY_IMPLEMENTED | `runtime/state.py:unresolved_submissions`; `providers/submission.py:verify_chain` / `normalize_state` / `RECOVERY_EVIDENCE_CORRUPT`; `cli.py:_unresolved_submissions` |
| impact / explain / dry-run | ALREADY_IMPLEMENTED | `build/impact.py`, `build/explain.py` — 07C does **not** copy their node algorithm |
| Director suggest / repair / proposal | ALREADY_IMPLEMENTED | `build/director.py`; MCP tool `director_suggest` |
| MCP ToolPolicy / profile resolver | ALREADY_IMPLEMENTED | `mcp/policy.py:resolve_agent_surface` / `TOOL_DEFS`; policies `spend/network/unattended` |
| canonical JSON / project-relative path helpers | ALREADY_IMPLEMENTED | `core/hashing.py:canonical_json` / `hash_value` / `hash_file`; `container.py:relpath` / `resolve` |

## The one real gap (MISSING at HEAD → filled by this batch)

| Gap | Status | Where it now lives |
|---|---|---|
| approved-baseline event bound to exact final bytes | MISSING → IMPLEMENTED | `build/baseline.py:approve_baseline` / `current_baseline` (rides `reports/verifications.jsonl`, new `kind="release_baseline_approved"`) |
| baseline-aware compare | MISSING → IMPLEMENTED | `build/baseline.py:compare_against_baseline` (calls `compare_finals`, additive fields only) |
| composed release assessment | MISSING → IMPLEMENTED | `build/baseline.py:release_assessment` embedded additively in `exportstatus.deliverables_data` |
| next-actions from stable blocker codes | MISSING → IMPLEMENTED | `build/baseline.py:_next_actions` (safety READ from `mcp.policy`) |

## Deliberately NOT built (contract §11 hard-stops respected)

- No `RebuildImpactSet` / `RegressionComparison` store / `DeliveryReadiness`
  subsystem / `NextActionSet` store / `PreviewLadder` schema — **REJECTED_WITH_REASON**:
  §1/§11 forbid parallel objects; assessment is derived, not stored.
- compare algorithm NOT copied — **SKIPPED_WITH_EVIDENCE**: `compare_against_baseline`
  delegates to `compare.compare_finals`.
- exports status NOT re-implemented — **SKIPPED_WITH_EVIDENCE**: assessment reads
  `exportstatus.deliverables`.
- No new MCP approval tool — **REJECTED_WITH_REASON** (Fable ruling 3): approval is
  human-CLI; the unattended surface therefore has no path to it (negative test added).

---

## §4.2 — pre-change safety gate (all PASS; cited, not re-proven)

| Gate | Result | Evidence |
|---|---|---|
| 03C run terminal honesty | PASS | `tests/test_p0_run_lifecycle.py` (INCOMPLETE ≠ COMPLETED, dangling attempts) — 9 tests green |
| 06 paid submission fail-closed | PASS | `tests/test_p0_recovery.py` + `tests/test_p0_paid_safety.py` (OUTCOME_UNKNOWN, recovery_unavailable, no auto-resubmit) |
| 02 deterministic QC fails closed | PASS | `tests/test_p0_assurance.py` (unavailable never → accepted) |
| evidence chain | PASS | `providers/submission.py:verify_chain` longest-valid-prefix; corrupt sentinel |
| test baseline (0 unexplained) | PASS | pre-gate suites `test_p0_*` + `test_compare` + `test_export_center` = **67 passed**; full suite verified in COMPLETION |

Gate verdict: **all pre-conditions satisfied — proceed to implementation** (no
"audit-only stop").

## §4.3 — pins pinned before the change (expected flips: none)

The surfaces-never-disagree pins `tests/test_export_center.py:test_cli_and_engine_agree`
(`exports --json == deliverables_data`) and `:test_api_exports_matches_engine`
(`/api/exports == deliverables_data`) were kept GREEN by composing
`release_assessment` INSIDE `deliverables_data` (not only at the CLI) so all three
surfaces still agree byte-for-byte. **0 pins flipped.**
