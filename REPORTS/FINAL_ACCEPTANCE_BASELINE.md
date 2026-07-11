# FINAL_ACCEPTANCE Baseline — audit & red reproduction

Contract: `Manju_Final_Acceptance_Blockers_Compact_v1` (F1-F5; red-first per
claim). Base: branch `claude/cost-optimization-strategy-cjfmn5`, HEAD
`f416167` ("Final acceptance F4/D: close the BuildLock mid-acquire steal race
CI reproduced"), working tree clean at audit. Python 3.11.15, pytest 9.1.1,
ffmpeg 6.1.1 (tests are ffmpeg-free).

## F4/D status (pre-resolved — not part of this track)

GitHub CI red at `b892529` (pytest exit 1, `test_b4` — a REAL BuildLock
mid-acquire steal race) was fixed by the orchestrator at commit `f416167`
(write-then-hardlink create + corrupt-needs-age grace; `runtime/buildlock.py`
untouched by this track). CI verification for the current HEAD is the
orchestrator's gate:

- CI run: <<CI_RUN_URL>>
- CI result: <<CI_RESULT>>

## Red reproduction at f416167

Evidence file: `tests/test_final_acceptance.py` (18 tests: 14 RED at HEAD +
4 guardrail pins green by design). First run at HEAD: **14 failed, 4 passed**.

| # | Contract claim | RED test (all in tests/test_final_acceptance.py) | HEAD behaviour reproduced |
|---|---|---|---|
| 1 | F1 malformed-only evidence + fresh SQLite | `test_f1_malformed_only_evidence_fresh_sqlite_blocks_transport` | torn line dropped by the consult → fresh paid submit fired (transport 1) |
| 2 | F1 valid terminal prefix + malformed tail | `test_f1_valid_terminal_prefix_plus_malformed_tail_blocks` | terminal chain restored nothing, torn tail ignored → transport 1 |
| 2b | F1 admitted-resume with torn stream | `test_f1_admitted_resume_with_torn_stream_fail_closes_not_polls` | consult trusted a chain read from a stream that provably lost a line → resumed |
| 4 | F1 release gate | `test_f1_release_gate_blocks_on_malformed_evidence` | malformed count discarded → gate read clean, ready not blocked by corruption |
| 5 | F2 run_id-less final | `test_f2_final_without_run_id_is_run_not_proven` | `_run_blockers` returned [] ("nothing to prove") → ready True |
| 5b | F2 legacy risk acceptance | `test_f2_legacy_final_approval_requires_human_risk_acceptance` | approval succeeded silently with no risk record |
| 6 | F3 missing output_sha256 | `test_f3_missing_output_sha256_blocks` | `if recorded:` skipped the whole byte check → no blocker |
| 7 | F3 unhashable final | `test_f3_unreadable_final_bytes_block` | `actual is None` skipped the check → no blocker |
| 8 | F3 candidate sha None | `test_f3_candidate_sha_none_is_never_ready` | ready stayed True with a None candidate sha |
| 9 | F5 valid baseline + torn line | `test_f5_valid_baseline_plus_torn_line_release_blocks` | malformed count invisible beside a valid event → no blocker |
| 10 | F5 baseline path escape | `test_f5_baseline_path_escape_is_damaged_never_fallback` | `except: root / path` followed a `../` escape; matching outside bytes read back **VALID** |
| 11 | F5 NLE hash missing | `test_f5_nle_project_file_without_hash_blocks` | `sha256: None` with no diagnostic at all |
| 12a | F5 bundle single-read | `test_f5_bundle_members_are_read_exactly_once` | each member read 3× (CAS revalidate → SHA256SUMS → zip) — two TOCTOU windows |
| 12b | F5 racing writer | `test_f5_racing_writer_never_yields_internally_inconsistent_bundle` | a write landing after CAS validation produced a bundle whose SHA256SUMS disagreed with the embedded manifest (internally inconsistent package shipped) |

Guardrail pins green at HEAD (and required to stay green): unpaid local
project unblocked (`test_f1_unpaid_local_project_stays_unblocked`), run-proven
final ready (`test_f2_run_proven_final_stays_ready`), hash-mismatch blocks
(`test_f3_hash_mismatch_still_blocks_pin`), pre-bundle mutation refused
cleanly (`test_f5_mutation_between_manifest_and_bundle_refused_cleanly`).

## Targeted baseline (pre-change, at f416167)

| Command | Result |
|---|---|
| `python -m pytest tests/test_final_acceptance.py -q` | 14 failed / 4 passed (the red matrix above) |
| `python -m pytest tests/test_c07_baseline.py -q` (with the F2 run-proven fixture upgrade) | 30 passed |

Full local suite at the pre-change HEAD: last recorded full run is
`b892529`'s completion report (2768 passed / 12 skipped / 0 failed); `f416167`
then rewrote `test_b4` and added `b16`/`b17` in `tests/test_c0911_gates.py`.
The post-change full count is in `REPORTS/FINAL_ACCEPTANCE_COMPLETION.md`.

## Scope note

Fix surface: `build/baseline.py`, `runtime/state.py`, `providers/base.py`,
`build/delivery.py` + test fixtures. Not touched: `runtime/buildlock.py`
(F4/D owner: orchestrator), DECISIONS.md, README.md. No new public schema, no
ledger/DB/worker/lease.
