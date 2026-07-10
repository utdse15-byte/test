# AI IDE 03C — Baseline Audit (基线审计)

Deliverable for **Manju Deep Research 03C — single run-evidence stream & derived RunManifest**. This file is the frozen WP0 baseline of HEAD before any WP1 change; the paired build report is `REPORTS/AI_IDE_03C_COMPLETION.md`. WP0 discipline: if an equivalent single attempt-evidence stream (attempt identity + parentage + input/output digests) already existed, the batch would STOP with a characterization test and report `ALREADY_IMPLEMENTED`. **The gate PASSED → BUILD** (attempt identity/parentage/digests are MISSING; every neighbouring capability exists only PARTIALLY, split across surfaces).

---

## 1. Header — branch / commit / date / environment

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` (read-only) |
| Base commit audited | `69af1bc` (DR03B baseline + completion reports landed) |
| Date | 2026-07-10 |
| Python | 3.11.15 |
| ffmpeg | `6.1.1-3ubuntu5` present (real-build render/QC pins exercised) |
| Process rules honored | no git commands; `python -m pytest` only |

---

## 2. Calibration re-verified — Manju has NO single attempt-evidence stream

The orchestrator's Variant-A ruling was re-verified locally with my own greps.

**C-1 — zero attempt-evidence tokens (grep-zero) ✅**

```
$ grep -rnE "stage_attempt|attempt_id|append_attempt|RunEvidence|StageAttempt|run-manifest|reports/runs" src/manju --include=*.py
(no matches)
```

There is **no** attempt identity, no `stage_attempt` action, no per-run evidence context, no RunManifest, and no `reports/runs/` store anywhere in the pre-existing tree.

**C-2 — the run identity that DOES exist is DR01's, and it is partial ✅**

`run_id` is minted once per build and correlates a produced final to its run, but nothing downstream of it records an *attempt* (a single provider try / cache-hit / render as a first-class, parented, digest-bearing event). Evidence today is SPLIT across four disjoint surfaces that never cross-reference an attempt:

- `events.jsonl` — coarse `action` records (`build`/`generate`/`render`/`failure`), no attempt granularity, no parentage, no digests.
- `reports/failures.jsonl` — structured failures (goal 10), but keyed to a shot, not an attempt.
- `.manju/state.sqlite` `runs` — the cost ledger, one row per generation, no attempt id.
- take `.key.json` / take sidecars — DR01 gave the FINAL an `output_sha256` + re-hashable `inputs`; individual takes carry `spec_hash` (the picture spec), never a media-content hash tied to the attempt that produced them.

**Verdict:** no single stream, no attempt identity, no derived manifest. The honest deliverable is (A) a new `build/attempts.py` that OWNS the stream (schema + states + locked emission + redaction + run context + projection reader + RunManifest materializer), and (B) SURGICAL emit-around wiring at the frozen lifecycle points — never a second store, never a behavior change when the evidence context is absent.

---

## 3. §3 checklist — capability verdicts (file:line, pre-change)

Verdict scale: **complete** / **partial** (exists but split across surfaces / not attempt-aware) / **missing**.

| # | Capability | Where it lives today (file:line) | Verdict |
|---|---|---|---|
| 1 | **Attempt identity** (`attempt_id`) | — (grep-zero) | **MISSING** |
| 2 | **Attempt parentage** (`parent_attempt_id`, `fallback_root_attempt_id`, `fallback_index`) | — (grep-zero) | **MISSING** |
| 3 | **Per-attempt input/output digests** | take sidecar has `spec_hash` (picture spec, not media bytes) `models.py:324-360`; only the FINAL is content-hashed (`render.py:_write_key_sidecar:1123`, DR01 `output_sha256`) | **MISSING** (no per-attempt output sha; final-only) |
| 4 | **Single evidence stream** | `events.jsonl` exists (`core/events.py:21` `append_event`) but carries coarse actions only | **PARTIAL** |
| 5 | `run_id` | minted `_run_build_phases` `graph.py:662`; on build event `graph.py:1487`; threaded to final sidecar via `render_timeline(run_id=…)` `graph.py:1372` / `render.py:1185,1138` | **PARTIAL** (DR01: build event + final sidecar; not on an attempt stream, not on `BuildResult`) |
| 6 | runs ledger fields | `state.py:_SCHEMA:63-79` (ts/shot/provider/params/status/failure_kind/cost/currency/remote_job_id/take/error/estimated_cost/failure_id); `record_run` `state.py:208` | **PARTIAL** (cost ledger; no `attempt_id`) |
| 7 | take sidecars (media hash) | `TakeSidecar` `models.py:324`; `Provider._register` `base.py:250-296` | **PARTIAL** (spec_hash + remote cost; **no media-content sha**) |
| 8 | failures.jsonl | `core/failures.py`; `record_failure:304`; `_ledger_lock:55-93` (the flock pattern to reuse) | **PARTIAL** (shot-keyed; free-form `detail` can hold an attempt id but none is written) |
| 9 | cancel | `BuildCanceled` `graph.py:29`; `_cancel_check:670`; `run_build` handler `graph.py:597-612` (appends a canceled build event) | **PARTIAL** (build-level; no `CANCELED` attempt) |
| 10 | ask_before gate | build gate `graph.py:806-821` (sets `result.waiting_user`); `spend_gate:67-83`; redo-batch gate `~1863` | **PARTIAL** (result flag + event; no `WAITING_USER` attempt) |
| 11 | content keys | `final_content_key` `render.py:1069`; `_final_key_payload:1002`; DR01 `_write_key_sidecar:1123` writes `final_key`+`output_sha256`+`inputs`+`run_id` | **PARTIAL** (final content key + output sha via DR01; not surfaced as an attempt output) |
| 12 | tasks / failures CLI | `tasks` sub-app `cli.py:3887`; rows built `:3929-3945`; `failures` command | **PARTIAL** (views exist; no `attempt_id`, no `manifest` subcommand) |
| 13 | rebuild-index | `RuntimeState.rebuild` `state.py:476-533` — wipes `runs`, re-derives one row per take **from sidecars only**; prunes stale pending jobs | **complete & MUST-PRESERVE** (never reads events/manifests — the invariant DR03C pins) |
| 14 | MCP build/tasks parity | build tool `mcp/tools.py:312-320` returns `run_build(...).to_dict()`; **no** tasks tool | **PARTIAL** (run_id rides to_dict for free once `BuildResult.run_id` exists; no tasks tool to touch) |

**Aggregate verdict — matches the expected:** attempt identity + parentage + input/output digests = **MISSING**; everything else **PARTIAL**, split across four surfaces that never converge on an attempt. Row 13 (`rebuild` is sidecar-derived) is the one thing that is complete and is a hard invariant to preserve. → **BUILD**, not `ALREADY_IMPLEMENTED`.

---

## 4. Chosen architecture — Variant A (events.jsonl IS the stream) + reasons

The orchestrator's binding ruling, re-confirmed against the code:

1. **`events.jsonl` is the single attempt stream.** An attempt is one versioned event line: `action="stage_attempt"`, `detail` = a `manju.stage-attempt-evidence/v1` document. The SQLite `runs` ledger, the `tasks`/`failures` views and the RunManifest are all **projections / cross-references** of it. No `trace.json`, no receipts dir, no second DB, no parallel file store.
   *Reason:* the codebase already treats `events.jsonl` as the append-only collaboration log and the handover surface (§3/§10); adding a second truth store would drift. A versioned `detail` payload rides the existing reader (`tail_events`/`follow_events` skip torn lines identically) without a schema break, and `rebuild` stays sidecar-derived so **deleting `.manju` loses nothing** — the attempt HISTORY is append-only in `events.jsonl`, the succeeded-take INDEX re-derives from sidecars.

2. **Reuse the DR01 `run_id` verbatim** and additionally expose it as `BuildResult.run_id` (additive, default None) so `build --json` / MCP return it for free via `to_dict()`.

3. **`attempt_id`** = `"att_" + uuid4().hex[:12]` (globally unique, no coordination). **`sequence`** = per-run monotonic int from a process-local `itertools.count` + `threading.Lock`. *Bound (documented):* sufficient because a build is single-process (`build_lock` serializes builds) and its generation concurrency is an in-process `ThreadPoolExecutor` (`graph.py:182-276`) sharing one `RunEvidence`; it is NOT a cross-process sequence (attempt_id stays unique regardless; the projection sorts by `(sequence, attempt_id)`).

4. **`build/attempts.py` owns everything;** every other module only CALLS it.

5. **Locked emission** — one `stage_attempt` write behind an `events.lock` flock (the exact `core/failures.py::_ledger_lock:55-93` pattern) because attempt payloads (inputs/outputs/request) exceed `PIPE_BUF`; plain small `append_event` writes stay unlocked-atomic and can only land wholly before/after a locked line.

6. **SQLite cross-ref** — an additive `runs.attempt_id` column (the `state.py:149-154` `#47`/`estimated_cost` idempotent `ALTER` precedent); `record_run(attempt_id=None)`. `record_failure`'s signature is UNCHANGED — an attempt id, when a caller has one, rides the free-form `detail`.

---

## 5. Emission points (frozen) + the FORBIDDEN list

Exactly six emission points, wired emit-around (never reorder / restructure):

1. **build run** — one run-level terminal attempt (`stage="build"`, `action="run"`, `unit={"kind":"project"}`) at every non-dry-run return: SUCCEEDED / FAILED / CANCELED / WAITING_USER. Dry-run emits nothing (a plan).
2. **provider attempts** — one per provider TRY in `generate_with_fallback`, with A/B/C parentage; SUCCEEDED only AFTER `register_take` committed media + sidecar.
3. **cache hits** — one `SKIPPED_CACHE_HIT` per skipped shot, bound by take name + spec_hash, **NO media re-hash**.
4. **final render** — SUCCEEDED (fresh) / SKIPPED_CACHE_HIT (content-key reuse), output sha READ from the DR01 sidecar (no re-hash).
5. **cancel / waiting** — CANCELED at the `_cancel_check` choke point; WAITING_USER at the spend gate (no provider submission).
6. **QC ref** — the run's `evidence_refs` gain the qc report paths.

**FORBIDDEN in this batch** (recorded as Deferred stages in the completion report): voice attempts, export attempts, repair/package wiring, per-segment render attempts, streaming begin/end pairs.

---

## 6. Red-first (WP0)

`tests/test_dr03c_attempts.py` + `tests/test_dr03c_lifecycle.py` import `manju.build.attempts` at module load — with the module hidden the accepted RED is a collection-time `ImportError` (captured verbatim in the completion report). The genuine behavioral pins are NOT import smoke: e.g. the fallback-parentage test drives a real 3-provider chain and asserts three parented attempts on the stream — with the registry wiring toggled off it fails `assert 0 == 3` while generation itself still succeeds (proving the wrap is emit-around). Every targeted regression suite was green at baseline before any wiring.
