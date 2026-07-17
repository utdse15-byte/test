# AI_IDE_09_11G Completion

Anti-overbuild gates run honestly. Every gate came back GREEN on real
fixtures — **final path: SKIPPED_WITH_EVIDENCE, production files changed: 0**.
Evidence: `tests/test_c0911_gates.py` (28 characterization tests, Part A + B;
b14/b15 added by POST_COMPLETION WP6 for the two proof-scope corrections).

## Baseline
- current resume surfaces: `status --json` (phase/final+crash-note/spend/
  run_log+pending/build_lock holder/next_step), `tasks --json` (+`tasks
  manifest <run_id>`: INCOMPLETE/dangling), `events --json` (run_id
  discovery), `exports --json` `release_assessment` (blockers + ToolPolicy-
  stamped next actions, 07C), `director suggest`/proposal fingerprint gate,
  `history/snapshot/rollback`, `agent_surface` digest.
- current lock/admission/concurrency/cancel surfaces: `runtime/buildlock.py`
  (O_EXCL + staleness + heartbeat, held by every mutating entrypoint), 06
  admission (`_prepare_submission` durable evidence → SQLite-CAS
  `claim_dispatching` → submit; `_resolve_resume` strict consult),
  `graph.py:_provider_semaphore` hard `max_concurrent` cap +
  `generic_cloud._throttle` (per-process soft rate, documented),
  `ProviderCanceled` / `run_canceled(decision={remote_may_continue})` /
  `tasks cancel` honest refusal.

## Gate results
| Gate | Red test | Result | Decision |
|---|---|---|---|
| 09 §4 resume trigger | test_a0 transcript + a1–a10 | GREEN — 5 read-only calls decide everything; engine enforces safety regardless | SKIPPED_WITH_EVIDENCE |
| 09 §5.1 status `resume` section | (would need §4 red) | no red | not built |
| 09 §5.2 SkillLock / skill evidence | test_a4 (digest split pinned) | no reproduced skill-drift recovery error | REJECTED_WITH_REASON |
| G11-1 duplicate paid submit | test_b1 (2 OS processes race the CAS on the **same pre-existing submission_id**), test_b2 (2nd process resumes ADMITTED poll-only), test_b3 (consult fail-closed on foreign DISPATCHING), **b14 (2 full subprocess generates → build-lock serializes to exactly 1 transport)** | GREEN — transport ≤1 for a given submission / build | SKIPPED_WITH_EVIDENCE |
| G11-2 concurrency limiter | test_b5 (per-process/per-build cap 2 held under 8 threads), b5b (no permit leak on exception), b5c (manifest is the one authority), b6 (cache hit never enters plan) | GREEN — **per-process (thread) cap, not a cross-process quota** | SKIPPED_WITH_EVIDENCE |
| G11-3 crash classification | test_b7 (process gone ≠ success; truncated final flagged), b8 (classification survives `.manju` loss **after an explicit `rebuild()`**), **b15 (same classification via the automatic consult guard, NO explicit rebuild)**, a1 (INCOMPLETE/dangling/NOT_FOUND) | GREEN — explainable + recoverable, never mislabeled | SKIPPED_WITH_EVIDENCE |
| G11-4 cancel honesty | test_b9 (before-dispatch: zero submission events, remote_may_continue=false), b10 (stop-polling stays ADMITTED, resume-poll-only, message says remote may bill), b11 (CLI refuses to fake; NO provider cancel hook exists) | GREEN | SKIPPED_WITH_EVIDENCE; `REMOTE_CANCEL_CONFIRMED` reason code REJECTED_WITH_REASON (no remote cancel API exists to confirm through) |
| G11-5 lease/fencing | test_b4 (2 OS processes race BuildLock — exactly one owner) | GREEN — no dual ownership reproducible; conditions 1–3 of §8 G11-5 all fail | REJECTED_WITH_REASON |

## 09 Resume
- round trips required: 5 read-only calls (status, tasks, events,
  tasks manifest <run_id>, exports) + the pure agent_surface digest. Full
  transcript in REPORTS/AI_IDE_09_11G_BASELINE.md.
- missing safety information: none found. Mis-resubmit is engine-blocked
  (transport 0, test_a2); mis-completion impossible (INCOMPLETE, test_a1);
  stale proposal expires at confirm (test_a3); surface drift visible via the
  policy digest (test_a4); source revision exact (test_a5); corrupt evidence
  fail-closed (test_a8); derived projections deletable (test_a9).
- status additive fields, if any: **none added** (backward-compat pinned by
  test_a10).
- skill evidence, if any: **none added** — no recovery error attributable to
  skill drift was reproduced; skills are readable content (skill_list/show)
  and deliberately outside the tool-policy digest (pinned test_a4).
- why no capsule/lock store: §4 requires ALL six conditions; only #1 (≥4
  calls) held. A persisted ResumeCapsule would duplicate five live surfaces
  into a second, stale-able truth — exactly the anti-pattern §1 names.

## 11 Single host
- duplicate submit proof: the strong cross-process guarantee is a CAS on the
  **same pre-existing submission_id** — two OS processes race
  `claim_dispatching("sub_race")` and SQLite admits exactly one DISPATCHING
  owner (test_b1). It is NOT a claim that two independently-minted fresh
  submits dedupe at the submission layer; two FULL subprocess generates are
  serialized instead by the BUILD LOCK (O_EXCL) — exactly one wins and its
  transport fires once, the loser fail-closes BuildLocked, transport across
  both = 1 (test_b14, POST_COMPLETION WP6 §1). A second process facing an
  ADMITTED job polls it, submit count 0 (test_b2); a foreign DISPATCHING row
  fail-closes a concurrent generate (test_b3). Layered this way, transport 2
  for a given submission/build is not reachable; no ExecutionAdmission needed.
- concurrency proof: manifest `max_concurrent` is a **per-process / per-build
  cap** — an in-process `threading.Semaphore` bounding one build's worker pool
  (peak 2 under 8 threads, test_b5), NOT a cross-process host-wide quota;
  permits release on exception (b5b); the limiter reads only the 04 manifest
  (b5c); cached shots never enter the dispatch plan, so cache hits consume no
  vendor permit (b6). The per-minute rate stays an honestly-documented
  per-process throttle with the remote 429 backstop — within contract §9.3.
- crash reconciliation: process-gone is INCOMPLETE with dangling attempts,
  never success (b7, a1); a crash-truncated final is flagged at the takeover
  surface (b7); classification is re-derived identically after total ledger
  loss **once an explicit `rebuild()` is run** (b8) AND, equivalently, by the
  AUTOMATIC consult guard on the very next paid generate with NO rebuild
  command run (b15 / POST_COMPLETION WP1 — a fresh/empty state.sqlite projects
  the unresolved submission straight from evidence); known remote jobs resume
  poll-only (b2); unknown outcomes stay fail-closed (a2). The §9.1
  classification vocabulary is answerable from existing fields
  (state/disposition/evidence_chain_ok/possible_remote_side_effect/
  automatic_resubmit + INCOMPLETE/dangling) — no new derived classification
  layer was needed because no unexplainable dangling state reproduced.
- cancellation honesty: before-dispatch cancel leaves zero submission
  evidence and records remote_may_continue=false (b9); stop-polling leaves
  the submission ADMITTED with its job id and the message says the remote may
  continue billing; the next build resumes rather than resubmits (b10);
  `manju tasks cancel` refuses to pretend (b11). No surface ever claims a
  remote cancel was confirmed.
- lease/fencing decision: REJECTED_WITH_REASON — §8 G11-5 requires (1) build
  lock cannot cover the work, (2) 06 claim cannot solve it, (3) stable
  two-process dual-ownership repro. b4 + b1 show (1)-(3) all fail: O_EXCL
  yields exactly one lock owner and the CAS exactly one dispatch owner.

## No-duplication proof
- no Session Runtime: nothing added; resume rides existing read surfaces.
- no ResumeCapsule: no persisted resume file anywhere; test_a6 proves the
  read set writes nothing.
- no ExecutionAdmission: 06 claim + consult remain the only paid gate.
- no BatchView: tasks/exports projections unchanged.
- no reconciliation ledger: classification is derived per read (b8 proves
  rebuild-from-evidence equivalence); nothing persisted twice.
- no queue/scheduler: no daemon, no worker registry, no Redis/Celery/BullMQ.

## Verification
| command | result |
|---|---|
| `python -m pytest tests/test_c0911_gates.py` | 28 passed (26 original + b14/b15 from POST_COMPLETION WP6) |
| `python -m pytest tests/test_c0911_gates.py::test_b1… ::test_b4… ::test_b14…` | race tests stable |
| `python -m pytest` (full suite) | 2727 passed / 0 failed at the 09_11G HEAD; POST_COMPLETION adds tests — see the POST_COMPLETION completion report for the current full-suite count |

## Final path
- **SKIPPED_WITH_EVIDENCE** (both parts; SkillLock, REMOTE_CANCEL_CONFIRMED
  and lease/fencing REJECTED_WITH_REASON as itemized above)
- production files changed: **0**
- new public schemas: 0
- tests added: `tests/test_c0911_gates.py` (26; uncapped)
- NOT touched: DECISIONS.md, README.md. No commit/push.
