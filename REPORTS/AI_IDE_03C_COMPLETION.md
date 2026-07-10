# AI IDE 03C Completion

Build report for **Manju Deep Research 03C — single run-evidence stream & derived RunManifest**. Paired baseline: `REPORTS/AI_IDE_03C_BASELINE.md`. Final-run numbers and git state below are filled by the orchestrator after the last full-suite run.

---

## Repository

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| Base commit | `69af1bc` (DR03B reports; DR01+DR02+DR03A+DR03B landed) |
| Pre-existing work preserved | the 4 pre-existing `include_unindexed` test-double-drift failures (`test_director` ×2, `test_round_w_agent_wb` ×1, `test_write_consistency` ×1) are OUT of this batch's set and untouched |
| DR03C landed | `src/manju/build/attempts.py` (new), emit-around wiring in `build/graph.py` + `providers/registry.py`, one additive field in `providers/base.py`, an additive column+param in `runtime/state.py`, `tasks manifest` + `attempt_id` in `cli.py`; `tests/test_dr03c_attempts.py` + `tests/test_dr03c_lifecycle.py`; working tree otherwise clean but for these two reports |
| Production files touched | **6 of the 7-file cap** — `mcp/tools.py` NOT needed (the build tool already returns `to_dict()` → `run_id` free; no tasks MCP tool exists to gain `attempt_id`) |
| Final full-suite result | **4 failed, 2329 passed, 12 skipped** (936.89s / 0:15:36) — the 4 are exactly the pre-existing `include_unindexed` set; **zero new failing test names**; +35 vs DR03B's 2294 = the 35 DR03C tests |
| Final git state | base `69af1bc` → `ce7bfaa` (DR03C code: attempts.py + emit-around wiring + ledger cross-ref + tasks manifest + 35 tests + DECISIONS.md #16 + README rows) → the reports commit adding this file + the baseline; working tree clean after it |

---

## Baseline (WP0 verdict)

The gate PASSED → **BUILD** (not `ALREADY_IMPLEMENTED`). Grep-zero for `stage_attempt`/`attempt_id`/`append_attempt`/`RunEvidence`/`run-manifest`/`reports/runs` across `src/manju`. Attempt **identity + parentage + input/output digests = MISSING**; every neighbouring capability (a `run_id`, the runs ledger, failures.jsonl, cancel, ask_before, content keys, tasks/failures CLI) exists only **PARTIALLY**, split across four surfaces that never converge on an attempt. `RuntimeState.rebuild` is the one complete, must-preserve invariant (sidecar-derived; never reads events/manifests). Full §3 checklist with file:line in `REPORTS/AI_IDE_03C_BASELINE.md`.

**Architecture — Variant A confirmed.** `events.jsonl` IS the single attempt stream (`action="stage_attempt"`, `detail` = a `manju.stage-attempt-evidence/v1` document). The SQLite ledger, `tasks`/`failures` views and the RunManifest are all PROJECTIONS of it. No trace.json / receipts dir / second DB. §3-disposability holds: **deleting `.manju` loses nothing** — attempt HISTORY is append-only in `events.jsonl`; the succeeded-take INDEX re-derives from sidecars (pinned by test 17).

**Red-first.** With `attempts.py` hidden, both DR03C test files fail at collection:
```
ERROR collecting tests/test_dr03c_attempts.py
E   ImportError: cannot import name 'attempts' from 'manju.build'
```
The behavioral pins are genuine, not import smoke: with the registry evidence hookup toggled off (`ev = None`), `test_fallback_chain_records_abc_parentage_and_preserves_failures` fails `assert 0 == 3` (no attempts on the stream) **while the generation itself still succeeds** (`takes[0].sidecar.provider == "ok_c"` passes in RED) — proving the wrap is emit-around, zero behavior change. Restored → GREEN.

---

## Existing systems reused (not duplicated)

| System | file:line | How reused |
|---|---|---|
| `events.jsonl` append log | `core/events.py:21` | the attempt stream IS this file; `stage_attempt` lines ride the existing `tail_events`/`follow_events` readers (torn-line-skipping identical) |
| `_ledger_lock` flock pattern | `core/failures.py:55-93` | replicated as `attempts._events_lock` on a sibling `events.lock` (degrade-not-hang; Windows no-op) for the >PIPE_BUF locked write |
| `hash_value` / `canonical_json` / `hash_file` | `core/hashing.py:23-44` | `semantic_digest`, `request_digest`, `evidence_digest`, output/verify hashing — one canonical hasher |
| `SECRET_PATTERNS` | `core/check.py:31-41` | the final systematic secret-scan backstop after the explicit key/URL policy |
| DR01 `run_id` | `graph.py:662` mint / `render.py:1123-1149` sidecar | reused verbatim; additionally exposed as `BuildResult.run_id` |
| DR01 final `output_sha256` | `render.py:_write_key_sidecar:1138-1143` | the render attempt READS it from the `.key.json` sidecar — never re-hashes the mp4 |
| `record_run` ledger | `runtime/state.py:208` | additive `attempt_id` column + param (the `#47`/`estimated_cost` idempotent-ALTER precedent `state.py:149-154`) |
| `RuntimeState.rebuild` | `runtime/state.py:476-533` | left sidecar-derived; NEVER reads a manifest (test 17 pins it) |
| `atomic_write_text` | `core/yamlio.py:61-77` | the RunManifest's crash-safe atomic write |
| `_ensure_builtins`/`register_provider` | `providers/registry.py:36-50` | the sandbox fixture registers fake providers for the no-ffmpeg lifecycle tests |

---

## WP1 — `build/attempts.py` — the module that OWNS the stream

`SCHEMA = "manju.stage-attempt-evidence/v1"`, `MANIFEST_SCHEMA = "manju.run-manifest/v1"`, `ACTION = "stage_attempt"`.

**States (11, never a `done` boolean):** `PLANNED, SUBMITTED, RUNNING, SUCCEEDED, SKIPPED_CACHE_HIT, FAILED, CANCELED, WAITING_USER, REJECTED_PRECHECK, ABANDONED, UNKNOWN_LEGACY`. `PLANNED`/`RUNNING` are reserved for a future streaming emission and never written in v1 (one-event-per-attempt); `UNKNOWN_LEGACY` is what the projection reads an unrecognizable state as (never invented into a real state); `ABANDONED` is reserved for an attempt whose work unwound past it (e.g. `ProviderCanceled`) — it emits no terminal event.

### API (as implemented)

```python
append_attempt(project, payload, *, actor="engine") -> dict     # normalize→redact→stamp→ONE locked write; never raises
class RunEvidence(project, run_id, *, actor="engine")           # per-run context
    .attempt(stage, unit, action, **fields) -> AttemptHandle
    .run_succeeded()/.run_failed()/.run_canceled()/.run_waiting_user()   # single run-level terminal (idempotent)
    .add_evidence_ref(ref) / .note_take_attempt(shot, take, attempt_id) / .attempt_for_take(shot, take)
    .warnings                                                    # evidence-append-failed warnings the build surfaces
class AttemptHandle
    .succeeded(outputs, cost, decision, …) / .failed(failure, …) / .rejected_precheck(failure)
    .skipped_cache_hit(outputs) / .canceled(…) / .waiting_user(…) / .abandoned(…)   # each emits ONE event; idempotent
read_attempts(project, run_id=None) -> (records, malformed)     # projection reader, sorted (sequence, attempt_id)
build_run_manifest(project, run_id) -> dict                     # pure projection
materialize_run_manifest(project, run_id) -> Path               # reports/runs/<id>/run.json (atomic)
run_manifest_path(project, run_id) -> Path
verify_outputs(project, run_id) -> list[mismatch]               # re-hash recorded outputs; report drift (test 21)
# helpers callers use: output_ref/input_ref, provider_request_evidence, redact_params, semantic_digest
```

### The StageAttemptEvidence v1 document

`{schema, run_id, attempt_id, sequence, stage, unit, action, state, ts, started_at, ended_at, duration_ms, parent_attempt_id?, fallback_root_attempt_id?, fallback_index?, executor?, request?, inputs?, outputs?, cost?, decision?, failure?, evidence_refs?, redactions?, semantic_digest}`.

- **inputs/outputs items**: `{role, path (project-relative), sha256, bytes?, content_key?, asset_id?}`; refs/prompts carry `{role, digest}` only.
- **`semantic_digest`** = `hash_value` over the payload EXCLUDING the exact set `{ts, started_at, ended_at, duration_ms, semantic_digest, redactions, evidence_refs}` and the two nested UI-text fields (`failure.message`, `request.redacted_summary`). Stable under timestamp/duration/redaction changes; moves on any semantic change (pinned by `test_semantic_digest_excludes_incidentals`).

### Concurrency + security (as built)

- **One event per attempt, single locked write.** `append_attempt` does exactly one `write()` of the whole line behind `events.lock` (flock), then `flush`+`fsync`. `AttemptHandle` guards a second terminal call → idempotent. `test_concurrent_appends_never_lose_or_tear_a_line` (40 threads) and `test_one_event_per_attempt` pin it. **NEVER raises out** — an append failure after a media commit returns `{}`, the handle records a run warning, and the media is untouched (test 18).
- **Sequence bound (documented in the module).** Per-run `itertools.count` + `threading.Lock`; sufficient because a build is single-process (`build_lock`) with in-process `ThreadPoolExecutor` concurrency sharing one `RunEvidence`. Not cross-process; `attempt_id` (uuid) stays unique regardless; projection sorts `(sequence, attempt_id)`.
- **Systematic redaction.** Explicit policy FIRST (drop `Authorization`/api-key-ish keys → `<redacted>`; digest `prompt`/`compiled_prompt`/`text` → `{digest,len}`; strip query strings from signed `http(s)` URLs), then `SECRET_PATTERNS` as a blanket backstop scan; `redactions[]` lists every kind applied. `request_digest` is computed over the REDACTED params so no secret enters the digest input. Pinned by `test_redaction_drops_credentials_prompts_and_signed_urls` (no `sk-…`, no `X-Amz-Signature` in the log) + `test_provider_request_evidence_digests_over_redacted_params`.

### RunManifest — `manju.run-manifest/v1`

`{schema, run_id, generated_at, terminal_status, attempt_count, malformed_lines, stages{stage:{state:count}}, costs[{currency,amount}], failures[{attempt_id,stage,category,code,failure_id?}], qc_report_refs[], final_output_refs[], attempts[…sorted…], evidence_digest}`.

- **terminal_status** ∈ {`COMPLETED`, `COMPLETED_WITH_WARNINGS`, `FAILED`, `CANCELED`, `WAITING_USER`} — from the run-end (`build`/`run`) attempt's state + whether any failure attempt is present (SUCCEEDED + a failure ⇒ COMPLETED_WITH_WARNINGS).
- **costs** — per attempt, prefer `actual` else `estimated`, **never both**; summed per currency (test 16).
- **derived / atomic / deterministic / deletable / never read** — same event set ⇒ identical `evidence_digest` (generated_at excluded); `atomic_write_text`; NEVER read by build/resume/cache/rebuild-index (tests 14/15/17).
- **never guesses** — `final_output_refs` come ONLY from render-attempt outputs; a stray final with no attempt is never claimed (test 13).
- **`verify_outputs`** — the honest verifier: re-materializing never RE-claims validity; mutating a final's bytes surfaces as a sha mismatch (test 21).

---

## WP2 — lifecycle wiring (emit-around; zero behavior change when evidence is None)

`RunEvidence` is created next to the `run_id` mint (`graph.py`), `None` on a dry-run. A `_finish_run(result)` closure emits the single run-level terminal (state derived from the result), materializes the manifest (best-effort — a failure warns, never breaks the build), and stamps `result.run_id`; it is called at every non-dry-run return (14 sites, mechanically wrapped `return result → return _finish_run(result)`). CANCELED is emitted at the `_cancel_check` choke point (where `RunEvidence` lives), with `run_id` riding the `BuildCanceled` exception so `run_build`'s handler stamps the freshly-minted canceled result.

### Emission-point table

| # | Point | Location (post-change) | State(s) | Evidence carried |
|---|---|---|---|---|
| 1 | build run | `_finish_run` at every non-dry-run return; `graph.py` | SUCCEEDED / FAILED / WAITING_USER | run-level; `evidence_refs` (qc); manifest materialized |
| 1c | cancel run | `_cancel_check` before raising `BuildCanceled` | CANCELED | `outputs_adopted` count, `remote_may_continue` |
| 2 | provider try | `registry.generate_with_fallback` (`_EvidenceChain`) | SUCCEEDED / FAILED / REJECTED_PRECHECK | executor{provider_id,type,model_id?,manifest_digest?}, request{request_digest,candidate_index,seed,redacted_summary}, outputs{take path+sha256+bytes} (post-`register_take`), cost{actual,estimated,currency}, failure{category,code,retryable,provider_job_id?,remote_may_continue?}, A/B/C parentage |
| 3 | cache hit | generate phase, per skipped shot; `graph.py` | SKIPPED_CACHE_HIT | outputs{take name, spec_hash, path} — **NO media re-hash** |
| 4 | final render | after `render_timeline`; `graph.py` | SUCCEEDED / SKIPPED_CACHE_HIT | outputs{final path, sha256 READ from DR01 sidecar, content_key, bytes} |
| 5 | spend gate | ask_before refusal path; `graph.py` | WAITING_USER | no provider submission (asserted) |
| 6 | QC ref | qc phase; `graph.py` | — | run `evidence_refs += qc report paths` |

**Provider parentage (A/B/C, as implemented & pinned):** `fallback_index` = position in the resolved order; `fallback_root_attempt_id` = the FIRST try's id (itself for A); `parent_attempt_id` = the immediately-previous try's id (`None` for A). Real run captured: `nope`(not_registered→REJECTED_PRECHECK, idx0, root=self) → `ffmpeg_kenburns`(invalid→REJECTED_PRECHECK, idx1, parent=A) → `caption_card`(SUCCEEDED, idx2, parent=B, root=A); the ledger row for the produced take carries the SUCCEEDED attempt's id.

### Scoping decisions (recorded honestly)

| Decision | What & why |
|---|---|
| **Cache-hit = no media re-hash** | Hashing every fresh take on every build is unacceptable I/O. A `SKIPPED_CACHE_HIT` binds identity via take **name + spec_hash** only (no `sha256`); the content-key binding for the deliverable rides the render attempt (test 4 satisfied at the render level). `verify_outputs` therefore skips cache-hit outputs (no recorded sha) — honest. |
| **Cache-hit covers FRESH *and* MANUAL** | `result.skipped` = FRESH ∪ MANUAL. Emitting for both makes the stream account for EVERY shot (none silently absent), not just regenerated ones. MANUAL's `spec_hash` is `"manual"` — honest binding. |
| **One event per attempt** | No begin/end pairs in v1. `started_at`/`ended_at`/`duration_ms` are fields of the single terminal record — simple and loss-proof; `PLANNED`/`RUNNING` reserved for future streaming. |
| **Cloud-internal retries stay internal** | The registry loop does not itself retry a provider, so `decision.retry_index = 0` honestly. A cloud provider's own backoff retries stay inside `CloudProvider` and do not mint separate attempts in v1. |
| **`ProviderCanceled` → no provider-level terminal** | The registry's per-provider `except` clauses intentionally don't catch `ProviderCanceled` (it propagates as a cancellation); the handle is simply never terminated (honest — the try didn't finish). The run-level CANCELED captures it. `ABANDONED` is reserved for this. |
| **Ledger `attempt_id` on the LOCAL path** | Threaded via `RunEvidence.note_take_attempt` → `_record_local_runs` → `record_run(attempt_id=…)`. Cloud `_on_success` writes its own ledger row without the attempt id (deep in the provider); documented gap, not exercised by the deterministic test 19 (which uses a local caption_card fallback). |
| **`record_failure` signature unchanged** | Per the ruling — an attempt id, where a caller has one, rides the free-form `detail`. The manifest's `failures[]` cross-references by `attempt_id` (the stable ref into the single stream). |

---

## WP3 — surfaces

- `manju build --json` / MCP build tool → `run_id` present (via `BuildResult.run_id` → `to_dict()`; MCP untouched).
- `manju tasks --json` rows gain `attempt_id` (from the new column; null for legacy / rebuilt-from-sidecar rows).
- `manju tasks manifest <run_id> [--json]` — **NEW subcommand** of the existing `tasks` sub-app (not a new family): re-materializes + prints the manifest path/content — the user path for "deleted manifest → rebuild". `test_cli_tasks_manifest_rematerializes` pins it.
- `explain` untouched (plans, not attempts). `rebuild-index` untouched — pinned by a test that deletes `.manju` + all manifests and rebuilds from sidecars while the attempt history stays readable from `events.jsonl`.
- MCP — no new tools; `mcp/tools.py` not touched.

---

## Files created / changed

| File | Change | Lines |
|---|---|---|
| `src/manju/build/attempts.py` | **new** — schema/states, `_events_lock`, redaction, `RunEvidence`/`AttemptHandle`, `read_attempts`, `build_run_manifest`/`materialize_run_manifest`, `verify_outputs` | 793 |
| `src/manju/build/graph.py` | `BuildResult.run_id` + `BuildCanceled.run_id`; `RunEvidence`+`_finish_run`+`_materialize`; CANCELED in `_cancel_check`; cache-hit + render + QC-ref emission; `_emit_render_attempt`; `evidence=` on the request; `attempt_id` through `_record_local_runs`; 14 returns wrapped | +187 / −26 |
| `src/manju/providers/registry.py` | `_EvidenceChain` (parentage + classified emission) + emit-around in `generate_with_fallback` | +180 / −2 |
| `src/manju/providers/base.py` | **one** additive field `GenerationRequest.evidence: Any = None` (+ `Any` import) | +10 / −2 |
| `src/manju/runtime/state.py` | additive `runs.attempt_id` column (idempotent ALTER) + `record_run(attempt_id=None)` | +22 / −4 |
| `src/manju/cli.py` | `attempt_id` in tasks `--json` rows + `tasks manifest` subcommand | +49 |
| `tests/test_dr03c_attempts.py` | **new** — 16 module tests | 307 |
| `tests/test_dr03c_lifecycle.py` | **new** — 17 wiring tests | 421 |
| `REPORTS/AI_IDE_03C_BASELINE.md` / `AI_IDE_03C_COMPLETION.md` | **new** — this pair | — |

Within the 7-file production cap (6 used; `mcp/tools.py` not needed). No edits to `core/`, `models.py`, `media/render.py`, `timeline/`, or the compiler.

---

## Tests

**Red-first** — verbatim above (module-hidden `ImportError`; behavioral `assert 0 == 3` with the wiring toggled off, generation still green).

**Targeted regression set (test 22)** — `test_events_follow` + `test_failures` + `test_ledger_count` + `test_runtime` + `test_job_cancel` + `test_jobs_lifecycle` + `test_idempotency` + `test_cli` + `test_mcp` + `test_ask_before` + dr01×3 + dr02×3 + dr03a + dr03b×2 + the two new files: **263 passed** (168.9s), zero regressions. Broader safety sweep (`test_interconnection` + `test_experience` + `test_generic_cloud` + `test_comfyui` + `test_local_cmd` + `test_auto_agent` + `test_history_rollback` + `test_evaluate` + `test_e2e_m0` + `test_final_trust_round` + `test_spend` + `test_spend_delta` + `test_hash_versions`): **132 passed**, zero regressions. Known baseline drift (4 `include_unindexed` failures in `test_director`/`test_round_w_agent_wb`/`test_write_consistency`) is OUT of this set and untouched. Full-suite (after ALL changes incl. review corrections): **4 failed, 2329 passed, 12 skipped** — zero new failing names.

**Orchestrator review corrections on top of the agent's delivery (both pinned by new tests):** (1) **cloud-path attempt_id parity** — attempt ids are now pre-minted per provider try and stamped on the request (`GenerationRequest.evidence_attempt_id`), so cloud providers' self-recorded ledger rows (base.py `_on_success`/`_on_failure`) carry the SAME attempt_id as the stream — the agent had wired only the local path and documented the cloud gap; the gap is now closed, not deferred. (2) **invocation context** — the run-level terminal carries `run_context {command, target, gen, mode}` and the manifest projects command/target/mode top-level (contract §6.1), fixing a whitelist in `_emit` that silently dropped the field. Test deltas: 33 → 35.

### Contract tests (all 22 covered)

| # | Contract pin | Test(s) | Result |
|---|---|---|---|
| 1 | attempt identity + schema | `attempts::test_attempt_has_stable_identity_and_schema` | ✅ |
| 2 | 11 states, never a `done` boolean | `attempts::test_states_are_the_eleven…` + `test_unknown_state_normalises_to_unknown_legacy` | ✅ |
| 3 | SUCCEEDED only after media committed+hashed | `lifecycle::test_succeeded_record_lands_only_after_media_is_committed_and_hashed` (intercept + re-hash at append) | ✅ |
| 4 | cache hit → render-level content-key binding | `lifecycle::test_second_build_render_is_skipped_cache_hit` + `test_build_emits_cache_hit_render_and_run_attempts` | ✅ |
| 5 | REJECTED_PRECHECK (invalid pre-submit; kenburns no ref) | `lifecycle::test_invalid_before_submit_is_rejected_precheck` | ✅ |
| 6 | cancel → CANCELED run attempt + manifest | `lifecycle::test_cancel_emits_canceled_run_attempt_and_manifest` | ✅ |
| 7 | parentage (root + parent + index) | `lifecycle::test_fallback_chain_records_abc_parentage_and_preserves_failures` | ✅ |
| 8 | redaction (secrets/prompts/signed URLs) | `attempts::test_redaction_drops_credentials_prompts_and_signed_urls` | ✅ |
| 9 | paths project-relative; prompts by digest | `attempts::test_paths_are_project_relative_never_absolute` + `test_provider_request_evidence_digests_over_redacted_params` | ✅ |
| 10 | concurrency — N threads, no loss/tear | `attempts::test_concurrent_appends_never_lose_or_tear_a_line` (40 threads) | ✅ |
| 11 | torn tail → malformed count, projection intact | `attempts::test_torn_tail_line_reports_malformed_but_projection_survives` | ✅ |
| 12 | one event per attempt (no begin/end pairs) | `attempts::test_one_event_per_attempt_no_begin_end_pairs` | ✅ |
| 13 | manifest never guesses from file existence | `attempts::test_manifest_never_claims_a_file_without_an_attempt` | ✅ |
| 14 | manifest deterministic for same event set | `attempts::test_manifest_is_deterministic_for_the_same_event_set` | ✅ |
| 15 | manifest derived/atomic/deletable/never-read | `attempts::test_manifest_is_derived_atomic_and_deletable` + `lifecycle::test_build_still_succeeds_after_its_manifest_is_deleted` | ✅ |
| 16 | costs — actual else estimated, no double-count | `attempts::test_manifest_costs_prefer_actual_and_never_double_count` | ✅ |
| 17 | delete `.manju` → rebuild-index works; history readable | `lifecycle::test_rebuild_index_works_from_sidecars_after_manju_and_manifests_deleted` | ✅ |
| 18 | evidence-append failure after commit → warn, never delete media | `lifecycle::test_evidence_append_failure_after_commit_warns_never_deletes_media` | ✅ |
| 19 | tasks `attempt_id` == events attempt_id | `lifecycle::test_tasks_attempt_id_equals_events_attempt_id` | ✅ |
| 20 | fallback success preserves failed attempts | `lifecycle::test_fallback_chain_records_abc_parentage_and_preserves_failures` | ✅ |
| 21 | mutate final bytes → `verify_outputs` flags it | `attempts::test_verify_outputs_flags_mutated_bytes` | ✅ |
| 22 | regression suites | the targeted set + broad sweep above | ✅ |
| + | extras | evidence-None inert; dry-run no attempts; render sha reuses DR01 sidecar; WAITING_USER + no submission; reader ignores non-attempt/foreign lines; `semantic_digest` excludes incidentals; `run_id` on result/to_dict; `tasks manifest` CLI | ✅ |

---

## Deferred stages (FORBIDDEN in this batch)

| Stage | Status | Why deferred |
|---|---|---|
| voice attempts | NOT WIRED | out of the frozen emission list; the voice phase runs but mints no `stage_attempt` (the run-level attempt still covers the build) |
| export attempts | NOT WIRED | exports read the timeline, not the render; a separate evidence surface would duplicate `exportstatus` |
| repair / package wiring | NOT WIRED | mutating surfaces outside the six frozen points |
| per-segment render attempts | NOT WIRED | v1 emits one render attempt for the final deliverable; segments are the render pipeline's internal cache |
| streaming begin/end pairs | NOT WIRED | v1 is one-event-per-attempt; `PLANNED`/`RUNNING` states reserved for a future streaming emission |
| locale / audition render attempts | NOT WIRED | WP4/audition paths are outside the frozen "final render" point; the run-level attempt still fires for those builds |

---

## Deviations (with reasons)

| Deviation | Reason |
|---|---|
| WAITING_USER wired at the build's own ask_before gate (`graph.py` `result.waiting_user` path), not the `:1855-1870` region the spec text cited | `:1855-1870` is the **redo-batch** spend gate (a different command); the build run's refusal path is the inline gate that sets `result.waiting_user` before any generation. Emitting there is the correct place for the *build* run's WAITING_USER terminal — pinned by `test_spend_gate_emits_waiting_user_with_no_provider_submission` (asserts zero generate attempts). |
| Run-level terminal emitted at ALL non-dry-run returns (not only the two build-event append sites) | Guarantees exactly one run terminal + a materialized manifest on EVERY exit path (check-fail, no-shots, mode-error, budget-breaker, render-fail included), which the "manifest on success AND failure" ruling requires. `_finish_run` is idempotent (a `RunEvidence` guard), so a canceled build that already emitted CANCELED is never double-counted. Still one logical emission point. |
| `mcp/tools.py` untouched | The build tool already returns `run_build(...).to_dict()` (run_id free once `BuildResult.run_id` exists) and there is no tasks MCP tool to gain `attempt_id`. Touching it would add a file for no capability — stayed at 6 of 7. |
| Cache-hit emission covers MANUAL as well as FRESH | See the scoping table — makes the stream account for every shot; `result.skipped` is exactly FRESH ∪ MANUAL. |
