# AI IDE 06 Completion

Build report for **Manju Deep Research 06 — persistent paid-submission identity, idempotent admission & ambiguous-outcome recovery**. Paired baseline: `REPORTS/AI_IDE_06_BASELINE.md`. Final-run numbers and git state are filled by the orchestrator after the last full-suite run.

Core principle held throughout: **an ambiguous submit outcome must never be automatically treated as not-happened.** We never claim exactly-once — we make the ambiguity durable, corruption-detectable and recoverable, and we fail closed rather than risk a blind resubmit of an already-billed job.

---

## Repository

| Field | Value |
|---|---|
| Base commit | DR04 landed (catalog + preflight; `provider_profile_digest` present) |
| DR06 landed | `src/manju/providers/submission.py` (new, pure); admission write-order + fail-closed + disposition-driven recovery in `providers/base.py`; send-boundary + disposition + declared-idempotency header in `providers/generic_cloud.py`; stop-fallback-on-UNKNOWN in `providers/registry.py`; additive intents columns + SQLite-CAS claim + submission projection + rebuild-from-events in `runtime/state.py`; flock-locked submission writer/reader in `build/attempts.py`; additive `submission.idempotency` field in `providers/manifest.py`; `tasks --json` `unresolved_submissions` + `attach-remote-job`/`abandon` subcommands in `cli.py`; `tests/test_dr06_identity.py` + `test_dr06_admission.py` + `test_dr06_recovery.py` + `test_dr06_characterization.py`; DECISIONS.md #18 + README rows; these two reports |
| Production files touched | **8 of the 10-file cap** (spec expected 7-8): `submission.py` (new), `base.py`, `generic_cloud.py`, `registry.py`, `runtime/state.py`, `build/attempts.py`, `manifest.py` (the 8th, additive idempotency field only), `cli.py`. No 9th/10th needed |
| Line deltas (production) | `submission.py` **+413** (new) · `base.py` **+523/-26** · `runtime/state.py` **+193/-5** · `cli.py` **+173** · `build/attempts.py` **+119** · `generic_cloud.py` **+68/-15** · `registry.py` **+24** · `manifest.py` **+22** |
| Tests | `test_dr06_identity.py` (45 tests) · `test_dr06_admission.py` (21 + 1 orchestrator) · `test_dr06_recovery.py` (15) · `test_dr06_characterization.py` (9) — 91 DR06 tests · `test_runtime.py` (+11/-5, one characterization flip) |
| Final full-suite result | **4 failed, 2473 passed, 12 skipped** (919.07s / 0:15:19) — the 4 are exactly the pre-existing `include_unindexed` set; zero new failing test names; +91 vs DR04's 2382 = the 91 DR06 tests (one earlier full run surfaced the DR04 within-limit characterization pin, flipped to the new contract in `6ca8db2`) |
| Final git state | base `b83b09b` → `38c9672` (DR06 code) → `6ca8db2` (characterization flip) → the reports commit adding this file + its pair; working tree clean after it |

---

## Baseline (WP0 verdict)

Gate PASSED → **BUILD**. The identity primitives exist (DR04 `provider_profile_digest` catalog.py:250; the 03C flock-locked stream attempts.py:134; the intents breadcrumb state.py:90; shot+provider pending resume base.py:465) but nothing binds a *durable submission identity*, nothing *classifies* a submit outcome (every transport error → `timeout`, generic_cloud.py:123), and the pre-submit window is *advisory by design* (goal 27 proceeds on a write failure). Full §6.4 table (24 rows, file:line) + the §6.3 crash map (C1-C4) + the 9 characterization pins in `REPORTS/AI_IDE_06_BASELINE.md`.

---

## Red-first (per WP)

**WP0 characterization first (9 pins, GREEN before any production change).** `tests/test_dr06_characterization.py` captured HEAD's exact behavior: dangling-intent advisory, pending-lookup shot+provider-only, a submit-timeout retried 4× (scripted transport), `params_hash` = bare params, a broken DB proceeds, cache-hit never submits, no submission columns, an unparseable-2xx receipt raises `provider_error`-with-no-disposition, and the clean-success anchor. All 9 GREEN on the untouched tree.

**Pure module RED→GREEN.** With `providers/submission.py` absent, `test_dr06_identity.py` failed at import (`ImportError`). The 45 pure tests (identity/digest/state-machine/disposition/chain/idempotency/redaction) went GREEN against the module with zero wiring — the pure logic is pinned independently of the persistence.

**Admission + recovery RED→GREEN.** `test_dr06_admission.py` (20) and `test_dr06_recovery.py` (15) were RED until the base/generic_cloud/registry/state/cli wiring landed. The crash-recovery tests inject a crash at each §8.5 boundary (after PREPARED, after the claim, after the ADMITTED event before the projection) via monkeypatch, then a FRESH `RuntimeState`/second `generate()` simulates the restart; the multi-process claim races two `RuntimeState` instances on one db (threads — SQLite locking is the guarantee).

**The 4 flipped characterizations (the documented deltas).** After wiring, exactly 4 of the 9 pins turned RED — each a documented change, updated in place to its post-DR06 form with the flip recorded:

| Pin | HEAD (pinned) | DR06 (flipped) |
|---|---|---|
| submit-timeout retry | retried 4× then raised `timeout` | **1 transport attempt**, OUTCOME_UNKNOWN, no retry |
| broken runtime DB | cloud `generate()` SUCCEEDED (§3) | **fail-closed** before the paid submit (submit_calls==0) |
| unparseable-2xx receipt | `provider_error`, no disposition, fallback-eligible | disposition **OUTCOME_UNKNOWN**, stops the chain |
| intents table shape | no submission columns | `submission_id`(UNIQUE)/`request_digest`/`state`/`updated_ts` + the CAS index |

`test_runtime::test_generate_survives_broken_runtime_state` was the one regression-set test whose behavior genuinely changed (§3-disposability reversal for the paid path) — updated in place to `test_generate_broken_runtime_state_fails_closed_before_paid_submit` and documented below. **Zero other regression-set test names changed.**

---

## Existing systems reused (not duplicated)

| System | file:line | How reused |
|---|---|---|
| DR04 `provider_profile_digest(provider_id, capability)` | `catalog.py:250-267` | the identity's provider input — a profile change moves `request_digest` (pinned) |
| 03C flock (`_events_lock`) + redaction backstop | `attempts.py:134-166, 227-249` | the SAME lock/file/`events.jsonl` carries `submission_state`; no new lock/file/ledger |
| `hash_value` / `hash_file` / `hash_text` | `core/hashing.py:28-44` | one canonical hasher for the identity digest, ref-file hashes, event-chain digest, idempotency key |
| intents breadcrumb + `open_intent`/`resolve_intent`/`dangling_intents` | `state.py:428-486` | KEPT (goal 27 advisory intact); `open_intent` gains submission columns additively |
| `pending_jobs` resume (§8.1) | `state.py:410-424`, `base.py:465-480` | the legacy jobs-row resume is the fallback when no DR06 submission exists |
| `#47` additive migration precedent | `state.py:154-160` | the same "ALTER ADD COLUMN + duplicate-swallow" grows the intents table |
| `record_provider_failure` / `_on_failure` free-form detail | `base.py:106-157, 633` | carries `submission_id`+`disposition` for UNKNOWN — no signature change |
| registry `_EvidenceChain.provider_failure` | `registry.py:431-454` | the attempt stays FAILED; the disposition rides its failure payload (no new attempt state) |
| `ShotSpec.keyframes` / `refset()` | models + `refs.py` | first/last flags + the delivery-order ref facts for the identity |

---

## WP1 — `providers/submission.py` — the pure identity / admission / disposition module

`SCHEMA_IDENTITY = "manju.submission-identity/v1"`, `SCHEMA_EVENT = "manju.provider-submission-event/v1"`, `EVENT_ACTION = "submission_state"`. Pure — no persistence, no network.

```python
# identity (§7)
build_submission_identity(*, shot_id, spec_hash, provider_id, provider_profile_digest,
    capability, duration_ms, candidates, seed, params, compiled_prompt,
    ref_refs=None, first_frame=False, last_frame=False, project_root=None) -> dict
request_digest(identity) -> "sha256:…"          # stable; moves on any included fact
canonical_params(params, *, project_root=None)  # sorted keys, bool≠int, float repr-stable,
                                                 # Path→project-relative, signed-URL query stripped,
                                                 # secret-named keys DROPPED WHOLESALE
ref_fact(role, logical_id, content_sha256) -> dict   # delivery-order ref, signed-url stripped
mint_submission_id() -> "sub_"+uuid4().hex[:12]      # minted+persisted BEFORE any network

# state machine
STATES, LEGAL_TRANSITIONS, normalize_state, is_legal_transition, assert_transition/IllegalTransition
UNRESOLVED_STATES = {DISPATCHING, ADMITTED, OUTCOME_UNKNOWN}
SIDE_EFFECT_AMBIGUOUS = {DISPATCHING, OUTCOME_UNKNOWN}; TERMINAL_STATES = {…}

# disposition (orthogonal to FailureKind)
NOT_DISPATCHED / DEFINITELY_REJECTED / ADMITTED / OUTCOME_UNKNOWN
disposition_for_status(status)                  # tested-4xx → DEFINITE, else >=400 → UNKNOWN
disposition_for_transport_error(exc)            # gaierror/ConnectionRefused → NOT_DISPATCHED, else UNKNOWN
disposition_to_state(disposition)

# event hash chain (per-submission corruption detection)
submission_event_digest(event) -> "sha256:…"    # over the §8.6 fields only (stable)
verify_chain(events) -> (ok, broken_at)

# idempotency + redaction
idempotency_key(provider_id, submission_id)     # sha256(("manju-provider-submit/v1", pid, sid)); no secret
redact_reason(text) -> str
```

- **`request_digest` includes** shot id + spec_hash; provider id + the DR04 profile digest; capability; duration; candidates; seed; canonical params; the compiled-prompt **digest** (text never stored, test asserts the plaintext appears nowhere); the final selected refs as `(role, logical_id, content_sha256)` in delivery order; first/last flags. **Excludes** keys/auth/signed-URLs/absolute-paths/mtime/now/PID/poll-intervals/UI-names — none are accepted as inputs. Secret-named params are dropped wholesale so a key-bearing request hashes IDENTICALLY to one without (test 09), and a signed-URL query is stripped so it never affects (nor rides) the identity.
- **`submission_id ≠ request_digest`** (test 02): an explicit redo mints a NEW id for the same digest; recovery KEEPS the id; a fallback provider is a NEW submission (registry mints per-`generate` call).

## WP2 — `runtime/state.py` — additive intents projection + SQLite-CAS

Additive columns `submission_id (UNIQUE index) / request_digest / state / updated_ts` via the #47 precedent (duplicate-column swallow); legacy rows read `state NULL → UNKNOWN_LEGACY`.

```python
open_intent(*, provider, shot, params_hash=None, submission_id=None, request_digest=None, state=None)
claim_dispatching(submission_id) -> bool        # UPDATE … SET state='DISPATCHING'
                                                 # WHERE submission_id=? AND state='PREPARED'; rowcount==1
set_submission_state(submission_id, new_state, *, expected_state=None, remote_job_id=None) -> bool
get_submission(submission_id) -> dict|None
submissions(*, shot=None, provider=None, states=None, request_digest=None) -> list[dict]
unresolved_submissions() -> list[dict]          # state in UNRESOLVED_STATES
rebuild(project) -> {runs, pending_jobs, submissions_restored}   # scans submission_state events
```

`claim_dispatching` is the **SQLite-level CAS** — multi-process safe (row locking is the guarantee, NOT an in-memory lock); the racing-threads test asserts exactly one `True`. `rebuild` restores UNRESOLVED submissions from the event stream (events win on reconcile — the write order emits the event before/around the SQLite write), so a `.manju/` loss never silently forgets an in-flight or ambiguous paid submission.

## WP3 — `build/attempts.py` — the flock-locked submission writer/reader

```python
append_submission_event(project, *, submission_id, request_digest, from_state, to_state,
    provider_id, shot=None, remote_job_id=None, reason_code=None,
    prev_event_digest=None, detail=None, actor="engine") -> dict   # returns the written event
read_submission_events(project, submission_id=None) -> (records, malformed)  # FILE order = emission order
submission_chains(project) -> {submission_id: [events…]}
```

One event line, `action="submission_state"`, reusing the SAME `_events_lock` flock and secret backstop. `read_submission_events` returns records in **file order** (which IS emission order under the append flock) — deliberately NOT re-sorted, so it is exactly the order the hash chain assumes and `verify_chain` validates. Only the free-form `detail` is redacted; the chain fields are controlled so the digest is stable.

## WP4 — `providers/base.py` — the §8.4 write order (wraps the existing flow)

`ProviderFailure` gains an orthogonal `disposition` attr (default `None` = legacy). `_SubmissionContext` owns the per-submission bookkeeping; every `transition()` emits the event FIRST then updates SQLite (evidence-before-projection).

### State machine (as implemented)

```
PREPARED ─claim─► DISPATCHING ─submit ok──────────► ADMITTED ─downloaded─► TERMINAL_SUCCESS
   │                  │  ├─ NOT_DISPATCHED ─► REJECTED_PRE_DISPATCH ─retry─► DISPATCHING
   │                  │  ├─ DEFINITELY_REJECTED ─► REMOTE_REJECTED ─(429)retry─► DISPATCHING
   │                  │  └─ OUTCOME_UNKNOWN ─► OUTCOME_UNKNOWN ─attach─► ADMITTED
   │                  │                                         └─abandon─► ABANDONED_BY_USER
   └─abandon──────────┴──────────────────────────────────────────────────► ABANDONED_BY_USER
ADMITTED ─remote failed/content_rejected─► TERMINAL_FAILURE          (legacy rows: UNKNOWN_LEGACY, read-only)
```

### Write order (as implemented)

1. **cache hit** → the cloud provider is never entered (graph.py skips FRESH/MANUAL) — no identity, no consumption (pinned).
2. **PREPARED** — `open_intent(state=PREPARED)` (intent row) + `submission_state` event with `{estimated_cost, gate: cleared_upstream}`. **Fail-closed:** if the row cannot be persisted (or state is `None`), raise BEFORE the network.
3. **DISPATCHING** — the atomic CAS claim (+event). **Fail-closed:** a lost CAS (`rowcount != 1`, another process owns it) raises BEFORE the network.
4. **submit()** → classify by disposition:
   - **ADMITTED**: emit the ADMITTED event **FIRST**, THEN `resolve_intent`/`open_job` (a crash between recovers from evidence, §8.5 row 5) → poll (existing).
   - **NOT_DISPATCHED** → REJECTED_PRE_DISPATCH (existing retry semantics allowed).
   - **DEFINITELY_REJECTED** → REMOTE_REJECTED (fallback allowed per existing policy).
   - **OUTCOME_UNKNOWN** → OUTCOME_UNKNOWN event → raise carrying the disposition; **NO local retry, NO fallback, NO resubmit**.
5. **TERMINAL_SUCCESS** — only AFTER the existing commit points (`register_take` + `_on_success` ledger row) have run.

Strict correlation (ruling 8) runs before a fresh mint: an ADMITTED submission with a matching digest resumes polling under the SAME submission_id; a DISPATCHING/OUTCOME_UNKNOWN one fails closed (`submission_outcome_unknown`); a digest-mismatched ADMITTED one is a conflict; a corrupt chain fails closed (`RECOVERY_EVIDENCE_CORRUPT`) — each scoped to that ONE shot+provider.

## WP5 — `providers/generic_cloud.py` — send boundary + disposition + declared idempotency

- `default_transport` marks the send boundary: `disposition_for_transport_error` — only `socket.gaierror` / `ConnectionRefusedError` → NOT_DISPATCHED; everything else → OUTCOME_UNKNOWN.
- `submit()`: pre-transport raises (duration/render/refs/missing-key) → NOT_DISPATCHED; a tested-4xx → DEFINITELY_REJECTED; any other ≥400 (5xx…) → OUTCOME_UNKNOWN; a 2xx-but-unparseable-job-id → OUTCOME_UNKNOWN; a 2xx with a job id → ADMITTED.
- `_with_idempotency_header`: injects the derived key into the declared header ONLY when the manifest opts in (`submission.idempotency.mode == header`) AND a key was derived — old manifests / direct tests are byte-identical.

## WP6 — `providers/registry.py` + `providers/manifest.py` + `cli.py`

- **registry**: on `disposition == OUTCOME_UNKNOWN`, the fallback chain re-raises immediately (falling back would be new spend on an unresolved outcome). The 03C attempt stays FAILED with the disposition + `automatic_resubmit:false`/`remote_may_continue` in its failure payload (no new attempt state).
- **manifest**: additive `submission.idempotency.{mode: none|header, field}` — absent on every existing manifest, default no-op.
- **cli**: `tasks --json` gains `unresolved_submissions` (submission_id, shot, provider, request_digest, state, remote_job_id, updated, disposition, `automatic_resubmit:false`, `possible_remote_side_effect`, `evidence_chain_ok`, actions). Two new subcommands: `tasks attach-remote-job <sid> <remote_job_id> [--expected-state]` (provider from the row; UNKNOWN/DISPATCHING only; appends ADMITTED evidence; poll-only; never claims verified ownership) and `tasks abandon <sid> --reason … [--expected-state]` (ABANDONED_BY_USER + explicit duplicate-risk; history preserved). NO "retry unknown" anywhere. `failures --json` UNKNOWN records carry `submission_id` in detail.

---

## Disposition classification table (as implemented)

| Submit outcome | Disposition | Submission state | Retry (submit)? | Fallback? |
|---|---|---|---|---|
| DNS failure (`socket.gaierror`) / `ConnectionRefusedError` before any byte | **NOT_DISPATCHED** | REJECTED_PRE_DISPATCH | yes (retryable kind) | yes |
| pre-transport `invalid` (missing key / duration cap / bad ref) | **NOT_DISPATCHED** | REJECTED_PRE_DISPATCH | no (`invalid`) | yes |
| tested 4xx: 400/401/403/404/409/422 | **DEFINITELY_REJECTED** | REMOTE_REJECTED | no | yes |
| 429 rate limit | **DEFINITELY_REJECTED** | REMOTE_REJECTED | yes (`rate_limited`) | yes |
| 2xx with a readable job id | **ADMITTED** | ADMITTED | — | — |
| timeout / reset / EOF **after** the send boundary | **OUTCOME_UNKNOWN** | OUTCOME_UNKNOWN | **NO** | **NO** |
| 5xx (500/502/503/…), 408, any other ≥400 | **OUTCOME_UNKNOWN** | OUTCOME_UNKNOWN | **NO** | **NO** |
| 2xx but the job-id path is unreadable | **OUTCOME_UNKNOWN** | OUTCOME_UNKNOWN | **NO** | **NO** |
| unclassified (`disposition=None`, e.g. a test double / non-generic provider) | legacy | inferred (REJECTED_PRE_DISPATCH / REMOTE_REJECTED) | pre-DR06 kind-based | pre-DR06 |

`FailureKind` is untouched — disposition is the orthogonal field. A poll/download failure carries `disposition=None` (it is not a submit-outcome), so poll/download retries stay byte-identical and never resubmit.

---

## Conditional-item decisions (with evidence)

| Item | Decision | Evidence |
|---|---|---|
| Provider-native idempotency | **IMPLEMENTED (declared-only, minimal)** — additive `submission.idempotency.{mode:header,field}`; generic_cloud injects the derived key ONLY when declared; a declared provider MAY re-dispatch the SAME submission_id on recovery (remote dedupes) | fake-provider tests prove a stable key, the same key across dispatch attempts, and one remote task on the idempotent recovery (tests 48/49) |
| `SpendAuthorizationRef` schema | **SKIPPED_WITH_EVIDENCE** — the existing ask_before/budget gate results are referenced inline on the PREPARED event (`{estimated_cost, gate: cleared_upstream}`); no new schema | the gate lives upstream in `build.graph`; reaching a paid submit means it cleared; `assume_yes` is not re-plumbed to the provider layer (that needs a graph.py touch beyond the file cap) |
| reconcile / status-by-key recovery hook | **SKIPPED_WITH_EVIDENCE** — provider manifests model no query-by-key endpoint; attach/abandon is the honest recovery, so the tasks diagnostic lists **attach/abandon only** | `ProviderManifest` has submit+poll(-by-job-id) only; no by-key status route exists to call |
| multi-process keyed-flight optimization | **SKIPPED** — the SQLite CAS is the multi-process guarantee; `build_lock` already serializes builds | `claim_dispatching` rowcount==1 + the racing-threads test |
| `manifest.py` field additions | **ONE** (`submission.idempotency`, the 8th production file) — additive, default no-op; old `provider.yaml` files load unchanged | required by the idempotency conditional's IMPLEMENT decision |

---

## Fail-earlier / fail-closed behavior changes (every one, deliberate + documented)

1. **Broken/absent runtime DB → fail-closed cloud submit.** If the PREPARED intent cannot be persisted (state unavailable / write error), the paid submit does NOT start (was: §3-disposable "proceed on best-effort state"). Direct call raises; through the registry it degrades to the local safety net. *(flipped char pin; `test_runtime` updated)*
2. **Lost DISPATCHING claim → fail-closed.** A CAS that does not win (`rowcount != 1`, another process owns it) refuses the submit (new — the multi-process double-submit guard).
3. **Submit-phase timeout/reset/EOF/5xx/unparseable-2xx → OUTCOME_UNKNOWN.** No local retry, no fallback, no resubmit (was: retried `max_retries` as `timeout`, or fell back as `provider_error`). *(flipped char pin)*
4. **Registry stops the fallback chain on OUTCOME_UNKNOWN** (was: fell back on every `ProviderFailure`).
5. **Fresh submit blocked by an unresolved DISPATCHING/OUTCOME_UNKNOWN submission** for the same shot+provider → `submission_outcome_unknown` diagnostic, `automatic_resubmit:false` (was: resumed by shot+provider, or a fresh resubmit).
6. **Digest-mismatched ADMITTED pending job → conflict** (`submission_spec_conflict`), never a silent poll of the old task (was: shot+provider resume would poll it).
7. **Corrupt per-submission evidence chain → fail-closed** (`RECOVERY_EVIDENCE_CORRUPT`), scoped to that ONE shot+provider (new).

Everything else is byte-identical: cache hits, local/offline providers (comfyui/local_cmd/kenburns/caption_card/manual never enter the wrapped path), and cleanly-succeeding cloud flows (only additive submission columns + `submission_state` events). Poll/download retries never resubmit (unchanged; disposition is submit-only).

---

## Tests

- **New suites (91 DR06 tests):** `test_dr06_identity.py` (45), `test_dr06_admission.py` (22, incl. the orchestrator's test 28b), `test_dr06_recovery.py` (15), `test_dr06_characterization.py` (9). All GREEN.

**Orchestrator review correction (pinned by test 28b):** the conservative OUTCOME_UNKNOWN default originally lived only in generic_cloud's transport classification — a RAW/unclassified exception from an escape-hatch adapter's `submit()` (e.g. a bare `TimeoutError`) escaped the admission machinery entirely (no UNKNOWN transition, no evidence, a crash instead of a fail-closed state). The default now applies at the ONE choke point (`CloudProvider.generate`'s submit call): any unclassified submit exception past the send boundary becomes a `ProviderFailure` with disposition OUTCOME_UNKNOWN, driving the same fail-closed path. Verified live: ambiguous submit → UNKNOWN + fallback stopped → `tasks --json` recovery actions → blocked re-attempt → `abandon` → new submission succeeds.
- **Targeted regression set:** `test_runtime` + `test_generic_cloud` + `test_jobs_lifecycle` + `test_job_cancel` + `test_ask_before` + `test_idempotency` + `test_failures` + `test_ledger_count` + `test_dr03c_attempts`/`_lifecycle` + `test_dr04_catalog`/`_preflight`/`_characterization` + `test_cloud_estimate` + `test_quality_modes` + `test_providers_routing` + `test_comfyui` + `test_local_cmd` + `test_refs` + `test_refbudget` — GREEN (one intended flip in `test_runtime`, documented above; zero other test names changed).
- **Broad safety sweep:** `test_cli` + `test_spend`/`_delta` + `test_savings` + `test_dr03b_characterization` + `test_dr01_run_evidence` + `test_events_follow` + `test_doctor` + `test_mcp` + `test_interconnection` + `test_final_trust_round` + `test_batch` + `test_asr` + `test_edge_tts` — GREEN.
- **Known baseline drift:** the 4 pre-existing `include_unindexed` failures (`test_director` ×2, `test_round_w_agent_wb` ×1, `test_write_consistency` ×1 — `gui/plan.py` test-double drift) live OUTSIDE this batch's set and are untouched.

### Contract §12 coverage (the 52 items)

| Area | Suite | Result |
|---|---|---|
| Identity/digest §7 (schema, included/excluded fields, prompt-digest-not-text, profile-digest moves, canonical params bool≠int/path/order, ref delivery-order, secret/signed-url exclusion, id≠digest, redo/recovery/fallback identity) | `test_dr06_identity` (18) | ✅ |
| State machine (states complete, happy/recovery legal, illegal rejected, UNKNOWN_LEGACY never invented) | `test_dr06_identity` (5) | ✅ |
| Disposition (status table 400-503, transport gaierror/refused/timeout/reset, to-state map incl. None) | `test_dr06_identity` (3) | ✅ |
| Event chain + idempotency + redaction (intact/tampered/dropped, stable/secret-free key, reason strip) | `test_dr06_identity` (5) | ✅ |
| Write order + CAS (PREPARED/DISPATCHING/ADMITTED/SUCCESS order, persist-before-submit, ADMITTED-before-projection, rowcount CAS, racing claim) | `test_dr06_admission` (7) | ✅ |
| Fail-closed + crash recovery (PREPARED/claim persist fail, crash after PREPARED/after claim/between ADMITTED-event-and-projection, rebuild-from-events) | `test_dr06_admission` (5) | ✅ |
| Disposition end-to-end + registry stop (DNS/4xx/timeout-no-retry/unparseable/5xx/pre-transport-invalid, stop-on-UNKNOWN, still-fallback-on-definite, definite-poll-fail→TERMINAL_FAILURE, local untouched, never-overwrite) | `test_dr06_admission` (9) | ✅ |
| Correlation + no-TTL (digest-match resume, unknown fail-closed, conflict, no-TTL) | `test_dr06_recovery` (4) | ✅ |
| Rebuild + corruption scoping | `test_dr06_recovery` (2) | ✅ |
| Recovery CLI (unresolved list, abandon, attach, wrong-expected-state, no-retry-unknown) | `test_dr06_recovery` (5) | ✅ |
| Declared idempotency (inject/none/idempotent-recovery) + legacy rows | `test_dr06_recovery` (4) | ✅ |

---

## Deviations (with reasons)

| Deviation | Reason |
|---|---|
| `manifest.py` touched (the 8th production file) | required by the IMPLEMENT decision for provider-native idempotency (an additive, default-no-op `submission.idempotency` field); within the 10-file cap, matches the spec's "manifest ONLY for the additive idempotency field → 8" |
| `assume_yes` not on the PREPARED event | the ask_before gate lives upstream in `build.graph`; threading `assume_yes` to the provider layer needs a graph.py touch beyond the file cap. The gate correlation is recorded inline as `{estimated_cost, gate: cleared_upstream}` — satisfying "existing gate results referencable inline" (SpendAuthorizationRef SKIPPED_WITH_EVIDENCE) |
| `disposition=None` keeps legacy retry-by-kind (not fail-closed) | ruling 6 "default None = legacy/conservative": the ONLY real `CloudProvider` on the paid path is `GenericCloudProvider`, which classifies explicitly; test doubles / hypothetical non-classifying providers keep pre-DR06 behavior, preserving byte-identity for the existing suite while the paid path is fully safe |
| `test_runtime::test_generate_survives_broken_runtime_state` updated | its behavior genuinely changed (the §3-disposability reversal for the paid path) — renamed to `…_fails_closed_before_paid_submit`, the one intended regression-set flip |
