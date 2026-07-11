# AI_IDE_09_11G Baseline — Audit & Gate Inputs

Contract: `AI_IDE_09_11G` (Resume 与单机执行的反过度建设门禁 — audit-first,
default outcome SKIPPED_WITH_EVIDENCE). Repo HEAD at audit: `891fce2`
(07C + 08_10_12C + 13C landed, full suite green). Branch
`claude/cost-optimization-strategy-cjfmn5`.

Gate evidence file: `tests/test_c0911_gates.py` (26 tests, all GREEN at HEAD —
no red reproduced anywhere, so **0 production files changed**). POST_COMPLETION
WP6 later added b14/b15 (28 total) for the two proof-scope corrections — the
per-process semaphore framing, the same-pre-existing-submission_id CAS scope,
and the explicit-vs-automatic rebuild distinction (see the completion report).

---

## §2 audit — every candidate surface located and verdicted

| Candidate | Verdict | Evidence (file:symbol) |
|---|---|---|
| `status --json` takeover surface | ALREADY_IMPLEMENTED | `build/status.py:project_status` — phase/shots/timeline/`latest_final` (+ crash-truncation note), qc summary, spend (per currency), `run_log` (+`pending_jobs`), `build_lock` holder peek (read-only, never steals), `next_step`, `failures_since_build` (`cli.py:322`) |
| `director suggest` / proposal currency | ALREADY_IMPLEMENTED | `build/director.py:suggest_next`; staleness gate `_is_current` (fingerprint) enforced at `confirm` (:636 → `expired` + refuse) and `execute` (:694); paid proposals human-confirm-only (:631) |
| history / snapshot / rollback | ALREADY_IMPLEMENTED | `core/history.py`; CLI `history/snapshot/rollback` (`cli.py:4697/4713/4734`) |
| AgentSurface / ToolPolicy / skills | ALREADY_IMPLEMENTED | `mcp/policy.py:resolve_agent_surface` / `Surface.digest()` (pure, stable); skills listed via `skill_list`/`skill_show`; **skill files deliberately outside the digest** (tool-policy digest only) |
| RunManifest / tasks / failures | ALREADY_IMPLEMENTED | `build/attempts.py:build_run_manifest` (P0: INCOMPLETE + `dangling_attempts` / NOT_FOUND / `legacy_terminal_only`); `cli.py:tasks` + `tasks manifest`; `core/failures.py` + `failure_id` cross-ref on ledger rows |
| build lock & process ownership | ALREADY_IMPLEMENTED | `runtime/buildlock.py:BuildLock` — atomic `O_CREAT\|O_EXCL`, holder JSON (pid/actor/host/started), dead-pid + age staleness, heartbeat, release-only-own; held around `run_build` (`build/graph.py:651`), MCP mutating tools (`mcp/tools.py:267,318,355`), CLI `_write_lock` (`cli.py:134`) |
| provider max_concurrent / rate limit | ALREADY_IMPLEMENTED | manifest `limits.max_concurrent` (validated ≥1, `providers/manifest.py:110`); **per-process / per-build cap** via a shared per-provider in-process `threading.Semaphore` under one build's worker pool (`build/graph.py:_provider_semaphore`/`_gated`, exception-safe `with`) — a thread-level bound, NOT a cross-process host quota; `rate_limit_per_min` per-process soft throttle, **honestly documented** as such (`generic_cloud.py:_throttle` docstring); remote 429 → `rate_limited` backoff is the cross-process backstop |
| 06 submission claim / recovery | ALREADY_IMPLEMENTED | `providers/base.py:_prepare_submission` — durable PREPARED evidence → SQLite-CAS `claim_dispatching` (`runtime/state.py:492`, WAL, cross-process row lock) → submit; `_resolve_resume` strict consult (ADMITTED→poll-only, DISPATCHING/UNKNOWN→fail closed, corrupt chain→fail closed); P0 `submission_recovery_unavailable` |
| tasks cancel / retry / remote_may_continue | ALREADY_IMPLEMENTED | `cli.py:tasks_cancel` refuses to pretend (exit ≠0, points at Ctrl-C / GUI); GUI `gui/jobs.py:JobRunner` cooperative cancel; `providers/base.py:ProviderCanceled` ("远程任务可能仍在进行并计费", job id persisted, resume-not-resubmit); `build/graph.py:876` `run_canceled(decision={remote_may_continue})`; P0 `run_terminal canceled` |
| provider REMOTE cancel hook | MISSING (by design) | no `cancel` method exists on `CloudProvider`/`GenericCloudProvider` (audited; pinned by test_b11) — hence `REMOTE_CANCEL_CONFIRMED` is unrecordable truth → REJECTED_WITH_REASON, not built |
| ResumeCapsule / SkillLock / status `resume` section | REJECTED_WITH_REASON | §4 gate conditions 2/4 fail (transcript below): the read set decides the next safe action; no real mis-action reproduced. SkillLock additionally forbidden by §5.2 absent a reproduced skill-drift recovery error (none found; digest split pinned by test_a4) |
| ExecutionAdmission / BatchView / TaskReconciliation / CancellationDisposition / Lease-Fencing | REJECTED_WITH_REASON | owners already exist (06 claim / tasks projection / evidence / cancel semantics / build lock); G11-1..5 all GREEN (below) — no red to justify any of them |

## §4.2 前置 — P0/03C/05/06 landed and green

`test_p0_run_lifecycle.py`, `test_p0_paid_safety.py`, `test_p0_recovery.py`,
`test_dr06_admission.py`, `test_dr05_*` all green at HEAD (2652 passed /
0 failed on the pre-batch full run). Consumed, not re-proven.

---

## Part A — the §4 resume experiment (real interrupted-session transcript)

World constructed (test_a0): a run hard-killed mid-generate (`run_started` +
`attempt_started`, no terminals), an OUTCOME_UNKNOWN paid submission left
behind, the dead process's `build.lock` still on disk, no final yet.

The fresh agent's ENTIRE read-only command sequence (5 calls + 1 pure resolver):

```text
1. manju status  --json          → build_lock holder (pid/actor/host) visible;
                                   latest_final null; run_log/pending; next_step
2. manju tasks   --json          → unresolved_submissions: sub_unknown,
                                   automatic_resubmit=false, actions=
                                   [attach_remote_job, abandon_with_duplicate_risk]
3. manju events  --json          → run_started without run_terminal → run_id
4. manju tasks manifest <run_id> --json
                                 → terminal_status=INCOMPLETE,
                                   dangling_attempts=[att_lost generate cloud_test]
5. manju exports --json          → release_assessment: ready=false; blockers
                                   SUBMISSION_OUTCOME_UNKNOWN + CURRENT_FINAL_MISSING;
                                   next_actions each stamped with ToolPolicy
                                   fix_owner/safe_to_auto_run/may_spend/…
(+) agent_surface (pure)         → surface digest sha256:… for tool-surface drift
```

§4 conditions scored:

| condition | held? |
|---|---|
| 1. ≥4 read-only entries needed | yes (5) — the only condition met |
| 2. next safe action still undecidable after the calls | **no** — call 2 names the exact recovery actions; call 5 ranks blockers with policy-stamped actions |
| 3. missing info not fixable by 1–3 additive status fields | **n/a** — nothing safety-relevant was missing at all |
| 4. a real risk materialized (mis-resubmit / mis-complete / stale proposal / drifted surface) | **no** — engine-enforced: transport 0 on unknown submission (test_a2), INCOMPLETE never COMPLETED (test_a1), stale proposal expires at confirm (test_a3), digest moves on policy change (test_a4) |
| 5. composition read-only | yes — already true (test_a6: byte-set identical tree) |
| 6. failing pre-change test exists | **no failing test could be produced** — every candidate red is GREEN |

→ **09 verdict: SKIPPED_WITH_EVIDENCE** (all-of-6 not met; 5.1's additive
`resume` section not needed — the information already exists across the
surfaces and, decisively, the ENGINE enforces the safety regardless of what a
confused agent does).

Round trips: 5 read-only calls is the honest count. The contract's threshold
is not "more than one call" but "dispersal causing real errors or unreasonable
round trips"; five sub-second local reads with zero wrong-action risk is
neither.

## Part B — gate inputs (all reproduced with real fixtures)

- G11-1: CAS race on the **same pre-existing submission_id** + cross-process
  ADMITTED resume + consult fail-closed (tests b1/b2/b3); two FULL subprocess
  generates serialized by the build lock to exactly one transport (b14, WP6).
  The submission CAS scopes to one submission_id; the build lock scopes one
  build — together transport 2 for a given submission/build is unreachable.
- G11-2: **per-process (thread) cap** of 2 held under 8 threads; permit
  released on exception; no-manifest providers uncapped by design; cache hit
  never enters the dispatch plan (tests b5/b5b/b5c/b6).
- G11-3: killed process → INCOMPLETE + dangling + truncated-final note;
  classification identical after full `.manju` loss both via an EXPLICIT
  `rebuild()` (b8) and via the AUTOMATIC consult guard with no rebuild (b15,
  POST_COMPLETION WP1) (tests b7/b8/b15, a1).
- G11-4: cancel-before-dispatch (zero submission events, remote_may_continue
  =false), stop-polling stays ADMITTED + resume-poll-only, CLI cancel refuses
  to pretend, no remote-cancel hook exists (tests b9/b10/b11).
- G11-5: two processes race the build lock → exactly one owner (O_EXCL),
  loser told who holds it (test b4). No dual ownership reproducible.
