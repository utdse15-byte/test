# AI IDE 01–06 P0 Remediation Completion

Build report for **AI_IDE_01_06_P0_REMEDIATION**. Paired baseline (RED
observations + transport counts per injection): `REPORTS/AI_IDE_01_06_P0_
REMEDIATION_BASELINE.md`.

## Repository

| Field | Value |
|---|---|
| Base | `f7a9a78` (intake) |
| Commits | `9517ce9` WP6 test doubles (tests-only) → `64c364c` WP5 assurance fail-closed → `76be954` WP1+WP2 durable admission + phase disposition → `95cbbb0` WP3+WP4 recovery/chain + run lifecycle → the reports commit |
| Final full-suite result | **0 failed / 2575 passed / 12 skipped** (802.08s / 0:13:22) — the contract's §1.11 target met; intake baseline was 4 failed / 2537 passed. One collateral surfaced only in the first full run (`test_ingest` pure-noop snapshot seeing the coordinator's 0-byte `events.lock` sibling) — flipped tests-only with rationale, then this clean full rerun recorded |
| Hard-constraint check | events.jsonl remains the ONLY evidence stream (no second ledger/file/dir; the run-lifecycle events ride it); SQLite remains deletable/rebuildable (rebuild now chain-verified); every §1 constraint mapped to a green test below |

## §10 explicit corrections of earlier claims

1. **"Terminal-only attempt evidence is loss-proof" — RETRACTED.** A crash
   mid-attempt left NO event at all; the RunManifest then claimed COMPLETED
   (baseline G: interrupted run → COMPLETED, attempt_count 0; unknown run_id →
   COMPLETED). Runs now carry `run_started`/`run_terminal` and expensive
   attempts carry `attempt_started`; an interrupted run derives **INCOMPLETE**
   with `dangling_attempts`, an unknown run **NOT_FOUND**. Legacy streams stay
   readable, stamped `legacy_terminal_only: true`, never embellished.
2. **"A disposition=None CloudProvider paid path is safe (legacy
   retry-by-kind)" — RETRACTED.** Inside the submit phase that default retried
   an unclassified timeout into up to 4 transport submits (baseline C). The
   submit choke point now defaults `disposition=None → OUTCOME_UNKNOWN`
   (`disposition_defaulted: submit_phase` marker): no retry, no fallback, no
   resubmit. Poll/download-phase `None` keeps kind-based retry and can never
   re-enter submit (pinned).
3. **"Evidence append failure ⇒ warning still satisfies durable admission" —
   RETRACTED.** A failed PREPARED/DISPATCHING append proceeded to transport
   (baseline A/B, count 1). Admission evidence is now REQUIRED_BEFORE_SIDE_
   EFFECT: any lock/write/flush/fsync failure raises
   `submission_evidence_unavailable` (disposition NOT_DISPATCHED) with
   **transport count 0** — including the REDISPATCH re-entry (review find).
   Post-spend evidence stays BEST_EFFORT by design (media kept, warning).

## Transport call counts per failure injection (§10)

| Injection | RED (before) | GREEN (after) |
|---|---|---|
| A PREPARED append fails | submit 1; stream `[DISPATCHING, ADMITTED, TERMINAL_SUCCESS]` | **submit 0**; stream `[]` |
| B DISPATCHING append fails | submit 1; stream `[PREPARED, ADMITTED, TERMINAL_SUCCESS]` | **submit 0**; stream `[PREPARED]` |
| B′ redispatch DISPATCHING append fails (review-added) | submit 2 (retry re-submitted) | **submit 1** |
| lock timeout (held events.lock) / no-fcntl platform | unlocked write; submit proceeded | **submit 0**; best-effort records DROPPED, never written unlocked |
| C submit `ProviderFailure(timeout, None)` | 4 submit attempts, then kind-retry/fallback eligible | **submit 1**, OUTCOME_UNKNOWN, fallback chain stops |
| C′ same failure from poll() | (safe already) | poll retries, **submit stays 1**, exhaustion leaves ADMITTED (pinned) |
| D `state.submissions` raises in consult | fresh submit, transport 1 | **transport 0**, `submission_recovery_unavailable` (stage `state_query`), no fallback |
| E identity computation raises in consult | fresh submit past an in-flight ADMITTED job, transport 1 | **transport 0**, stage `identity` |
| F corrupt chain + deleted SQLite | 0 rows restored (latest-event-wins trusted a forged/torn terminal) → re-submit, transport 1 | DISPATCHING restored from the longest valid prefix, fresh submit **blocked (transport 0)**; no-valid-prefix → `RECOVERY_EVIDENCE_CORRUPT` sentinel; valid-ADMITTED rebuild → poll-only (submit 0) |
| G interrupted run / unknown run id | manifest COMPLETED | **INCOMPLETE + dangling_attempts** / **NOT_FOUND** |
| H `run_qc` raises + PASS observations | assurance `accepted` | `unknown` + `qc: {status: unavailable, reason}` (single + bulk) |
| I duplicate expectation_id in one verdict | accepted, last-one-wins | whole batch rejected, **zero writes** (ledger+packets byte-identical) |

## Delivered per WP

- **WP1 (one coordinator, two policies).** `core/events.py` now owns
  `events_lock` / `append_jsonl_line` / `EvidenceWriteError` (reasons
  `lock_timeout` / `no_reliable_lock` / `io_error`; `str()` secret/path-free) —
  the SINGLE flock + durable write behind attempt events, submission events AND
  plain `append_event`. Lock timeout NEVER falls through to an unlocked write:
  best-effort drops + warns, REQUIRED raises. No-fcntl platforms refuse paid
  appends (`no_reliable_lock`) and drop best-effort ones. PREPARED/DISPATCHING
  (incl. redispatch) are `required=True`; ADMITTED/terminal/classify stay
  best-effort (money already spent — the flow is never orphaned). All
  redaction stayed in `attempts.py`, byte-identical (pinned).
- **WP2 (phase-aware disposition).** One choke point in
  `CloudProvider.generate`: submit-phase `ProviderFailure(disposition=None)` →
  OUTCOME_UNKNOWN; raw exceptions already were (DR06). Every pre-transport
  raise site in `generic_cloud.submit` (limits, render, refs, missing key,
  throttle — 7 sites, one boundary) is explicitly NOT_DISPATCHED. The global
  tested-4xx/429 → DEFINITELY_REJECTED table is DELETED; a post-send status is
  a definite rejection ONLY when the manifest declares it
  (`submit.definite_rejection_statuses`, additive, validated 400–599);
  undeclared post-send errors (429 included) are OUTCOME_UNKNOWN.
- **WP3 (fail-closed recovery + chain projection).** Consult failures
  (identity / state query / evidence read / chain verify) raise structured
  `submission_recovery_unavailable` `{automatic_resubmit: false,
  possible_remote_side_effect: true, stage}` — never "fresh". Rebuild projects
  each submission as the LONGEST VALID PREFIX (`project_chain`: digest links
  recomputed, transitions legality-checked, first event must open fresh);
  corrupt tails are ignored, never trusted; no valid prefix restores the new
  `RECOVERY_EVIDENCE_CORRUPT` sentinel (unresolved + side-effect-ambiguous BY
  CONSTRUCTION; exits only attach→ADMITTED / abandon→ABANDONED_BY_USER; never
  idempotent redispatch — corrupt evidence cannot prove the prior key).
- **WP4 (run lifecycle + honest manifest).** `manju.run-lifecycle/v1` events on
  the SAME stream, all best-effort, one lock acquisition each: `run_started`
  (run_id mint), `run_terminal` (every `run_build` exit incl. cancel checkpoint,
  BaseException guard, and the review-added BuildCanceled-handler emit),
  `attempt_started` before exactly the two expensive families (provider
  generation, final render) on the pre-minted attempt_id the existing terminal
  rides (no rename, no dual-write). Manifest: NOT_FOUND / INCOMPLETE +
  `dangling_attempts` / verbatim terminal status + warnings promotion /
  `legacy_terminal_only`. No file-existence success inference exists (pinned).
- **WP5 (assurance fail-closed).** Deterministic QC is tri-state
  (pass/blocked/unavailable): UNAVAILABLE derives `unknown` with a visible
  `qc: {status, reason}` — never accepted (single + `assurance_for_all` via the
  QC_UNAVAILABLE sentinel). Expectation compile failure surfaces
  `expectation_compile_error` in `unknown` — never a vacuous
  `no_explicit_expectations`. Duplicate `expectation_id` inside one verdict
  rejects the WHOLE batch, zero writes (extends the existing payload-invalid
  path; no second intake).
- **WP6 (green baseline).** Tests-only: three doubles mirror the real
  `run_build` signature; one stale assertion moved to the CLI's actual `rows`
  envelope (git-history-verified as test drift, not a production regression).

## Fail-earlier behaviors introduced (complete list, §10)

1. PREPARED/DISPATCHING/redispatch evidence append failure → refuse before
   transport (was: warn + spend).
2. Events-lock timeout → REQUIRED refuses / best-effort drops (was: unlocked
   write).
3. No-fcntl platform → paid appends refuse (was: lockless write).
4. Submit-phase unclassified ProviderFailure → OUTCOME_UNKNOWN, no retry, no
   fallback (was: kind-based retry, up to 4 submits).
5. Undeclared post-send HTTP error (incl. 429) → OUTCOME_UNKNOWN, chain stops
   (was: global DEFINITELY_REJECTED table → fallback).
6. Consult identity/state-query/evidence-read/chain-verify failure → structured
   refusal (was: silent fresh submit).
7. Consult on a RECOVERY_EVIDENCE_CORRUPT row → structured refusal (state
   named; attach/abandon are the only exits).
8. QC unavailable / expectation compile error → non-accepting `unknown` (was:
   accepted / vacuous no-expectations).
9. Duplicate expectation_id → whole-batch intake rejection, zero writes (was:
   accepted, last-one-wins).
10. `append_event` no longer crashes callers on an unwritable events.jsonl
    (best-effort drop) — fail-QUIETER, recorded for symmetry.

## Deviations (with reasons)

| Deviation | Reason |
|---|---|
| RED probes for A/B/D/E/F/G run then deleted (not left in-tree) | the permanent tests assert the CONTRACT (green); the RED observations live in the baseline report — keeping both forms would pin the unsafe behavior twice |
| `_chain_tail_digest` keeps its exception swallow | `_guard_chain_integrity` reads+verifies the SAME events fail-closed immediately before it on every consult path — a raise there is unreachable; changing it is churn without a reachable gap |
| No `chain_corrupt` SQLite column | spec-permitted skip: the consult re-verifies every chain from evidence on each fresh submit; the row needs no marker |
| Render `attempt_started` emitted before cache-hit is knowable | reuse is only decidable after `render_timeline`; a cache hit's SKIPPED_CACHE_HIT terminal rides the SAME attempt_id, so nothing dangles |
| Direct-TTS voice + audition/locale renders not wrapped in `attempt_started` | they emit no terminal stage_attempt today — there is no attempt_id to correlate; wrapping would create a NEW attempt family beyond WP4's additive mandate (recorded, not hidden) |
| `run_build` uses an `except BaseException` guard + idempotence flag instead of a literal try/finally | equivalent coverage; lets BuildCanceled keep its dedicated `canceled` terminal from the cancel checkpoint; KeyboardInterrupt → `failed`; a hard KILL emits nothing → INCOMPLETE (by design) |
| `tasks attach/abandon` suite named `test_tasks*` in the plan does not exist | nearest real coverage runs green instead: `test_dr06_recovery` + new sentinel attach/abandon CLI tests |
| Windows lock strategy = refuse paid calls (not an msvcrt lock) | the contract offers exactly this alternative ("必须有可靠锁实现或明确拒绝付费调用"); a reliable cross-process msvcrt implementation cannot be verified on this POSIX-only CI |

## Tests

- **New (32):** `test_p0_paid_safety.py` (9 — incl. the review-added redispatch
  gate), `test_p0_recovery.py` (6), `test_p0_run_lifecycle.py` (7),
  `test_p0_assurance.py` (5), + 5 flipped/companion pins in the DR06/generic_
  cloud families. §9 acceptance items 1–13 map 1:1 onto them; item 14
  (secret/path scan) rides the pinned redaction tests + `manju check`'s
  existing scan; items 15–16 are the suite results below.
- **Pins flipped (4, all WP2):** submit-timeout-retry characterization →
  1-attempt OUTCOME_UNKNOWN; `disposition_for_status` global-table pin →
  undeclared-UNKNOWN + declared companion; two fixtures (4xx-fallback, 429-
  retry) now DECLARE their statuses instead of weakening assertions. B and A2
  flipped ZERO pins — those gaps were silent-unsafe, untested before.
- **Targeted suites (Fable reruns at commit time):** P0 set 27 green; provider
  family 197 green; coordinator consumers 122 green; A2 targeted set 204 green;
  dr02+qc consumers 103 green.
