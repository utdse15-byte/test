# AI IDE 06 Baseline

WP0 audit for **Manju Deep Research 06 — persistent paid-submission identity, idempotent admission & ambiguous-outcome recovery**. Paired completion: `REPORTS/AI_IDE_06_COMPLETION.md`. Base commit: DR04 landed (catalog + preflight; `provider_profile_digest` present).

The gate **PASSED → BUILD** (not `ALREADY_IMPLEMENTED`). No `providers/submission.py`, no `submission_id` / `request_digest` / admission state machine, no `submission_state` event / `manju.provider-submission-event/v1` schema, no disposition classification, no SQLite-CAS claim, no `unresolved_submissions` surface and no `tasks attach-remote-job`/`abandon` subcommands exist anywhere in `src/manju` (grep-zero). The *facts* an identity needs exist (DR04 `provider_profile_digest`, the intents breadcrumb, the 03C flock-locked stream) but nothing binds a durable submission identity, nothing distinguishes "provably-not-sent" from "outcome-unknown", and the pre-submit breadcrumb is deliberately **advisory** — a crash mid-submit is flagged, never fail-closed.

---

## §6.4 verdict table (audited, file:line, CURRENT HEAD)

| # | Capability the contract asks for | Where it lives today | Verdict |
|---|---|---|---|
| 1 | Persistent submission identity (`submission_id`) minted+persisted before network | — (only a per-attempt uuid intent id, **state.py:437**, not durable identity) | **MISSING** |
| 2 | `request_digest` over remote-result-affecting semantics | intent `params_hash = hash_text(str(sorted(params.items())))` **base.py:530** — bare params ONLY | **PARTIAL** — a params hash, not full semantics (no spec_hash/profile/refs/prompt) |
| 3 | Reuse DR04 `provider_profile_digest(provider_id, capability)` | **catalog.py:250-267** (stable, documented "AI_IDE_06 deliverable") | **PRESENT** — input reused verbatim |
| 4 | `submission_state` events + per-submission hash chain (`manju.provider-submission-event/v1`) | 03C `stage_attempt` stream **attempts.py:66-73**; no submission action, no chain | **MISSING** |
| 5 | Flock-locked append machinery to reuse (`events.lock`) | `_events_lock` **attempts.py:134-166**; `append_attempt` **:356-400** | **PRESENT** — reused (expose a submission writer, no new lock) |
| 6 | Admission state machine (PREPARED/DISPATCHING/ADMITTED/REJECTED_PRE_DISPATCH/REMOTE_REJECTED/OUTCOME_UNKNOWN/TERMINAL_*/ABANDONED) | intent `status` ∈ open/resolved/error **state.py:446-465** | **MISSING** — 3 opaque statuses, no transitions, no validation |
| 7 | Intents table as the admission projection | `intents(id,provider,shot,params_hash,ts,remote_job_id,status)` **state.py:90-98** | **PARTIAL** — breadcrumb exists; no submission_id/request_digest/state/CAS |
| 8 | Atomic DISPATCHING claim (SQLite-CAS, multi-process safe) | `open_intent` is a plain `INSERT` **state.py:438-443**; no CAS anywhere | **MISSING** |
| 9 | §8.4 write order (PREPARED→claim→submit→ADMITTED-evidence→projection) | `open_intent→submit→resolve_intent→open_job` **base.py:412-424** | **PARTIAL** — a breadcrumb+resolve, no PREPARED/claim, resolve AFTER submit, no ADMITTED evidence |
| 10 | Disposition (NOT_DISPATCHED/DEFINITELY_REJECTED/ADMITTED/OUTCOME_UNKNOWN), orthogonal to FailureKind | `FailureKind` only **base.py:41-49**; no disposition attr | **MISSING** |
| 11 | Send-boundary marking (generic_cloud transport) | `default_transport` maps EVERY `URLError/TimeoutError/OSError` → `FailureKind.timeout` **generic_cloud.py:123-127** | **MISSING** — DNS/refused indistinguishable from a post-send timeout |
| 12 | Registry stops fallback on OUTCOME_UNKNOWN | `generate_with_fallback` `except ProviderFailure: … continue` **registry.py:266-270** — falls back on EVERY failure | **MISSING** |
| 13 | Pending correlation by `submission_id`/`request_digest` | `_resume_job_id` matches **shot+provider only** **base.py:465-480**; `pending_jobs` **state.py:410-424** | **PARTIAL** — resume exists, correlation is coarse |
| 14 | Structured unknown-outcome diagnostic `{submission_outcome_unknown, automatic_resubmit:false, …}` | — | **MISSING** |
| 15 | No TTL for unresolved states | rebuild prunes pending **jobs** >7d **state.py:538-542**; the unresolved *states* don't exist yet | **ABSENT** (states absent) — the pruning is jobs-only, never touches an admission state |
| 16 | rebuild() restores unresolved submissions from events | rebuild re-derives `runs` from sidecars + prunes jobs **state.py:490-547**; never scans events for submissions | **MISSING** |
| 17 | Recovery subcommands `tasks attach-remote-job` / `abandon` | `tasks` has `cancel`/`retry`/`manifest` **cli.py:4004-4134** | **MISSING** |
| 18 | `tasks --json` `unresolved_submissions` section | payload = `{tasks,pending,spend,shown}` **cli.py:3959-3964** | **MISSING** |
| 19 | `failures --json` carries `submission_id` in detail | `record_provider_failure` detail = `{provider,failure_kind}` **base.py:152** | **MISSING** |
| 20 | Declared-only provider-native idempotency (manifest field + header injection) | no `submission` section in `ProviderManifest` **manifest.py:260-285** | **MISSING** |
| 21 | Fail-closed if PREPARED/claim cannot be persisted | goal 27 EXPLICITLY proceeds on a write failure (`_open_intent`/`_on_submit` record a Failure then **continue**) **base.py:518-599** | **ABSENT (opposite)** — the deliberate §3 choice DR06 reverses for the paid path |
| 22 | Local/offline providers untouched | comfyui/local_cmd/kenburns/caption_card/manual are `Provider` subclasses that override `generate` **comfyui.py:86-119, local_cmd.py:71-107** | **PRESENT** — never enter `CloudProvider.generate` |
| 23 | Cache-hit short-circuit (no identity, no consumption) | FRESH/MANUAL shots skip generation in graph **graph.py:942-959** — the cloud provider is never entered | **PRESENT** — pin, don't move |
| 24 | Dangling-intent handling is advisory (not blocking) | `_flag_dangling_intents` records a Failure, does NOT block **base.py:482-516** | **PRESENT** — the advisory baseline DR06 tightens to a fail-closed correlation |

**Conclusion → BUILD.** The identity primitives (`provider_profile_digest`, the flock-locked stream, the intents breadcrumb, the pending-job resume) all exist but nothing binds a *durable submission identity*, nothing *classifies* a submit outcome as sent-vs-unknown, and the pre-submit window is *advisory by design* (goal 27). The 5 headline gaps: identity/digest (1-2), the admission state machine + CAS (6-8), disposition + send boundary (10-11), the fail-closed reversal (21), and the whole recovery surface (14-19).

---

## §6.3 crash-point sequence (drawn from code, HEAD)

The paid path today (`CloudProvider.generate`, base.py:387-444):

```
open_intent('open')  ──►  submit()  ──►  resolve_intent(job_id)  ──►  open_job('pending')  ──►  poll ─► download ─► _on_success(close_job+record_run)
     C1                       C2                 C3                          C4
```

| Point | Crash here at HEAD | HEAD behavior | The DR06 gap |
|---|---|---|---|
| **C1** after `open_intent`, before `submit` | intent stays `open` | next run FLAGS it (advisory) then **proceeds** — could resubmit | nothing was sent; DR06 records PREPARED, no claim yet → safe to proceed |
| **C2** during `submit` (bytes sent, no response) | intent stays `open` | next run FLAGS + **proceeds** — the remote MAY have received it → **double-submit / double-charge window** | THE window: DR06 leaves DISPATCHING → next run FAILS CLOSED (`submission_outcome_unknown`) |
| **C3** after `submit` returns id, before `resolve_intent`/`open_job` | intent `open`, NO jobs row | job id LOST → next run FLAGS + could resubmit an already-billed job | DR06 writes the ADMITTED **evidence first** → rebuild-from-events restores it (§8.5 row 5) |
| **C4** after `open_job`, before terminal | jobs `pending` | resume POLLS the same id — no resubmit (§8.1) | already correct; DR06 keeps it (ADMITTED-without-terminal is resume-safe) |

The double-charge exposure is entirely at **C2/C3**: HEAD narrows it (goal 27 flags), DR06 closes it (an ambiguous outcome is never auto-treated as not-happened).

---

## §6.5 characterization program (`tests/test_dr06_characterization.py`, 9 pins, GREEN on the untouched tree)

The five load-bearing pins the contract names, plus four anchors — all captured BEFORE any production change, so each DR06 fail-closed/fail-earlier change is a provable delta:

1. **dangling-intent = advisory** — an OPEN intent from an earlier crash is flagged as a Failure and the next `generate()` still **succeeds** (not blocked). *(pinned)*
2. **pending lookup = shot+provider only** — a pending job with unrelated params is resumed for a differently-seeded request; the params/semantics are never consulted. *(pinned)*
3. **submit-timeout retry count** — a persistent submit-phase timeout is RETRIED `max_retries` times (**4 transport attempts** via a scripted transport) before it raises. *(pinned; this is the ambiguity DR06 fail-closes)*
4. **params-hash completeness** — `intents.params_hash == hash_text(str(sorted(params.items())))` — the bare params dict, NOT spec_hash/profile/refs/prompt. *(pinned)*
5. **state-write-failure proceeds** — a broken/absent runtime DB is swallowed and a cloud `generate()` still SUCCEEDS (§3 disposability). *(pinned)*
6. **cache-hit** — a FRESH shot never enters the cloud provider's `submit()` (spy asserts 0 calls). *(pinned)*
7. **intents table shape** — no `submission_id`/`request_digest`/`state` columns at HEAD. *(pinned)*
8. **unparseable-2xx receipt** — a 200-but-unreadable-job-id submit raises `provider_error` with NO disposition, fallback-eligible. *(pinned)*
9. **clean-success anchor** — a clean submit→poll→download records exactly one succeeded run with the cost. *(pinned)*

Pins **3, 5, 7, 8** are the four that DR06 deliberately FLIPS (submit-timeout no longer retried → OUTCOME_UNKNOWN; broken state now fail-closes; the columns now exist; the unparseable receipt is now OUTCOME_UNKNOWN). The COMPLETION report records the flip for each.

---

## Must-preserve invariants (pinned)

- **Clean cloud success byte-identity** — `test_generic_cloud::test_happy_path_registers_take` + `test_runtime` resume/success tests: the take sidecar, the ledger row, the cost and the job-close are unchanged; the only additions are the submission intent columns + `submission_state` events.
- **Cache-hit / local / offline untouched** — FRESH/MANUAL shots skip generation (graph.py:942); comfyui/local_cmd/kenburns/caption_card/manual override `generate` and never enter the wrapped path. `test_dr03c_lifecycle` cache-hit + `test_comfyui`/`test_local_cmd` pin this.
- **03C stream discipline** — one flock (`events.lock`), one file (`events.jsonl`); the submission writer reuses `_events_lock`, never a second lock/file. `test_dr03c_attempts` pins the attempt stream is unchanged.
- **#47 migration precedent** — additive `ALTER TABLE … ADD COLUMN` with "duplicate column" swallow; `test_runtime::test_legacy_single_column_jobs_table_migrates_cleanly` pins the migration discipline.
- **Registry fallback on ordinary failures** — only OUTCOME_UNKNOWN stops the chain; DEFINITELY_REJECTED/content_rejected/no-takes still fall back (`test_dr03c_lifecycle` parentage + safety net).

Full red-first outcomes, the delivered module API, the state machine + write order as implemented, the disposition table and the conditional decisions are in `REPORTS/AI_IDE_06_COMPLETION.md`.
