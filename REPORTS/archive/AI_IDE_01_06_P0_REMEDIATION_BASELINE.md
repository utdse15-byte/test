# AI IDE 01–06 P0 Remediation Baseline

WP0 failure-injection baseline for **AI_IDE_01_06_P0_REMEDIATION** (P0 修复闭环:
03C/06 崩溃与付费安全、02 assurance 收紧、全量测试清零). Paired completion:
`REPORTS/AI_IDE_01_06_P0_REMEDIATION_COMPLETION.md`.

Per the entry instruction, the current completion reports were NOT taken as
correctness proof — every claim below is grounded in source reads, failure
injection with transport call counts, and rebuild results.

## Recorded first (§1)

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| HEAD at intake | `f7a9a78`, working tree CLEAN |
| Full-suite baseline | **4 failed / 2537 passed / 12 skipped** (896.22s, run at this exact HEAD ~30 min before intake) — the 4 = exactly the WP6/J set |
| After WP6 (tests-only, `9517ce9`) | those 4 pass; production untouched |

## §2 claim-by-claim verdicts (audit-first: source read at intake HEAD, then injection)

| # | Claim (current unsafe behavior) | Verdict | Evidence |
|---|---|---|---|
| A | PREPARED event append failure → transport still called | **CONFIRMED** (source + injection) | `base.py` `emit` turned an empty append into a warning and continued; `attempts.py` `append_submission_event` "NEVER raises out", returns `{}`. RED injection: **transport submit count 1**, stream `[DISPATCHING, ADMITTED, TERMINAL_SUCCESS]` — the failed PREPARED silently missing. (The SQLite intent row was already fail-closed; the EVENT evidence was the gap.) |
| B | DISPATCHING event append failure → transport still called | **CONFIRMED** | RED injection: **transport 1**, stream `[PREPARED, ADMITTED, TERMINAL_SUCCESS]` |
| C | `ProviderFailure(timeout, disposition=None)` in submit → retry/fallback | **CONFIRMED** | `_classify_submit_failure` docstring said it outright: disposition-None is "legacy inference for the projection only (never fail-closed here)"; the kind-based retry gate re-entered submit. RED injection: **4 transport submit attempts** (pinned at HEAD by `test_char_submit_timeout_is_retried_then_raises`) |
| D | `state.submissions` raises → fresh submit possible | **CONFIRMED** | `_resolve_resume`: `except Exception: subs = []` → consult sees nothing → `("fresh")`. RED injection: fresh paid submit proceeded, **transport 1**, take produced. (`state=None` was separately safe: `_prepare_submission` refuses with transport 0 — pinned, not changed.) |
| E | identity computation raises → fresh submit possible | **CONFIRMED** | `_resolve_resume`: `except Exception: digest = None` → ADMITTED match AND conflict check silently skipped. RED injection: fresh submit **past an in-flight ADMITTED job** (transport 1 — the double-charge case) |
| F | valid DISPATCHING + corrupt terminal + deleted SQLite → block not restored | **CONFIRMED** | rebuild was **latest-event-wins**: a corrupt/torn TERMINAL tail meant the whole submission restored **0 rows** (a forged resolution was trusted) → fresh generate re-submitted (**transport 1**). A first-line-corrupt chain also restored 0 rows (no sentinel existed) |
| G | run started / long stage interrupted, no terminal → manifest may claim COMPLETED | **CONFIRMED** | DR03C evidence was terminal-only (a crash mid-attempt left NO event) and there were no run-level lifecycle events. RED injections: interrupted run → **COMPLETED, attempt_count 0**; attempt-terminal-then-crash → **COMPLETED**; unknown run_id → **COMPLETED, attempt_count 0** |
| H | `run_qc` raises + all observations PASS → assurance accepted | **CONFIRMED** | RED injection: `compute_assurance` returned `accepted` on both the single-shot and the `assurance_for_all` bulk path (the shared-QC failure was a silent `None` = "no blocks") |
| I | duplicate `expectation_id` in one verdict → last-one-wins | **CONFIRMED** | RED injection: `record_verdicts` accepted the payload and wrote; `assurance.diff()` maps observations by id, so the later (conflicting) item silently replaced the earlier |
| J | four `include_unindexed` tests failing | **CONFIRMED + FIXED first** (`9517ce9`, tests-only) | Three doubles lacked `include_unindexed`/`should_cancel`/`lang` vs the real `run_build` signature. The fourth was NOT a double: a stale assertion (`payload["plan"]` vs the CLI dry-run envelope's actual `rows` key — `git log -S` shows the test predates the envelope change in pre-session commit `23269e9`); semantic assert (gen off ⇒ nothing planned) preserved via `rows == []` |

## WP1 lock-layer facts at intake (why the coordinator was mandatory)

- `attempts.py` flock: **timeout → fell through to an UNLOCKED write** ("never
  lose the record … proceed"); **no fcntl → the lock no-oped entirely**
  (Windows wrote lockless).
- `core/events.py` `append_event`: no lock at all (fsync only) — and an
  unwritable events.jsonl **crashed its callers** (no try/except).
- Redaction (secret keys dropped, prompts digested, URL queries stripped) lived
  correctly in `attempts.py` and had to survive the refactor byte-identically.

## Conclusion → BUILD (three tracks)

All ten claims confirmed; none was ALREADY_IMPLEMENTED. Remediation proceeded
red-first in three tracks — A1 (WP1 coordinator + WP2 disposition), B (WP5
assurance), A2 (WP3 recovery/chain + WP4 lifecycle/manifest) — each gap's RED
observation recorded above, each fix + flipped pin + suite result in the
completion report.
