# FP Loop M — Run Performance Report (§8.6, a derived view)

Function-Perfection loop M: the run performance report — a **pure derived view**
over the existing DR03C run/attempt evidence. NO new instrumentation, NO engine
edits, NO scheduling input (§8.7 原则: 性能提示不是调度事实). A §8.6 metric with no
recorded source is listed honestly under `unavailable[]`, never estimated.

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| HEAD at intake | `eed5d5c`, working tree clean |
| Deliverable module | `src/manju/qc/runperf.py` (`run_performance(project, run_id=None) -> dict`) |
| CLI | `manju perf [RUN_ID] [--json]` — latest run by default |
| Tests | `tests/test_fp_runperf.py` — 22 targeted, all green |
| Snapshot | `tests/fixtures/cli_surface.json` regenerated (fold-ins: `perf` + parallel Loop K's `fixity`) |

## Scope ruling

The report reads ONLY evidence that already exists on the single `events.jsonl`
attempt stream (`build.attempts`, DR03C, Variant A: the events stream IS the
attempt stream). It adds no timers, no counters, no event kinds. Its output is
byte-deterministic for a given `events.jsonl` (no wall-clock read; the
RunManifest's `generated_at` is dropped; wall duration is parsed from the
recorded `started_at`/`ended_at` stamps, never `ts`, never `now()`).

## Evidence-source audit — what §8.6 wants vs what is recorded

The complete emission surface was audited: exactly **three stages** emit terminal
`stage_attempt` events — `generate` (provider generation in
`providers/registry.py`; per-shot cache/manual skips in `build/graph.py`),
`render` (fresh/reused final in `build/graph.py`), and the run-level `build`/`run`
envelope (`build/attempts.py`). No other module emits attempts. Cost/trace: the
module docstring of `build/attempts.py` is explicit — "there is no trace.json, no
receipts directory, no second database"; the only run-scoped, deterministic cost
evidence is the per-attempt `cost` block (the disposable SQLite ledger that
`manju spend` reads is the cross-run MONEY view, not per-run performance).

| §8.6 metric | Recorded source | Verdict |
|---|---|---|
| structured logs | `events.jsonl` itself (the substrate) | available (is the source) |
| run correlation | `run_id` on every attempt/lifecycle line | **available** |
| stage duration | `duration_ms` (monotonic wall time, stamped at `_emit`) | **available** |
| cache hit rate | `SKIPPED_CACHE_HIT` — a first-class recorded state, emitted per stage by `build/graph.py` | **available** (audit FOUND a real source) |
| Provider time | `duration_ms` where `executor.kind == "provider"` (set by `registry.py`) | **available** |
| render time | `duration_ms` where `stage == "render"` | **available** |
| QC time | — no `stage="qc"` attempt is ever emitted; QC is referenced only via `evidence_refs` (report paths) on the run-level attempt, never timed | **UNAVAILABLE** |
| cost | attempt `cost` block (`actual`/`estimated`, `_cost_of` rule) | **available** |
| disk usage | — only per-output `bytes` recorded; no filesystem measurement | **UNAVAILABLE** (proxy surfaced) |
| slowest node | `duration_ms` sorted (stages + attempts) | **available** |
| error categories | `failure.category` / `failure.code` | **available** |
| performance report | this deliverable | delivered |

### The honest gap list (`unavailable[]`)

Two §8.6 metrics have no recorded source on today's stream and are listed with
the missing-source name — never estimated:

1. **`qc_time`** — QC runs never emit a timed `stage="qc"` attempt; they are
   referenced only as `evidence_refs` on the run-level attempt, so there is no
   recorded QC duration to sum.
2. **`disk_usage`** — only per-output `bytes` are recorded. A true disk-usage
   figure (project size, proxy/cache/intermediate footprint, dedup-aware) needs a
   live filesystem stat, which a deterministic derived view must not read. The
   honest recorded proxy `recorded_output_bytes` (Σ `outputs[].bytes`) is surfaced
   separately and is explicitly NOT conflated with disk usage.

The **cache** row is deliberately NOT in `unavailable[]`: the audit found the
real recorded source (`SKIPPED_CACHE_HIT`), so cache hit rate is genuinely
derived per stage (hits / cache-decidable population = `SUCCEEDED` +
`SKIPPED_CACHE_HIT`; a `FAILED` attempt is not cache-decidable). A test pins this
exact reasoning.

## Metrics implemented

`run_performance` returns (every key always present; empty/`None` when a run has
no such evidence):

- `wall` — `{ms, started_at, ended_at, source}` from the run envelope's span
  (fallback: min/max of stage stamps → `source: "event_span"`).
- `stages[]` — per stage: `total_ms/min_ms/max_ms/mean_ms`, `attempts`,
  `status_counts`, `missing_duration`; the run envelope is **excluded** (its
  span is the wall, not a stage — never double-counted).
- `slowest_stages[]` / `slowest_attempts[]` — top 5 by recorded `duration_ms`.
- `time_split` — `provider_ms` (executor.kind==provider) vs `local_ms` (every
  other non-envelope attempt) vs `render_ms`, `by_provider[]`,
  `attempts_missing_duration`.
- `cache` — overall + per-stage `hits/eligible/rate` from `SKIPPED_CACHE_HIT`.
- `cost` — `totals` (reuses the RunManifest verbatim, single money source),
  `by_provider[]`, `by_shot[]` (same `_cost_of` rule — no drift, no double count).
- `by_shot[]` — per-shot `total_ms`, `cost_amount`, state histogram.
- `errors` — grouped by `failure.category` then `failure.code`.
- `recorded_output_bytes` — the honest proxy for the unavailable disk-usage.
- `unavailable[]` — the two structural gaps above (emitted even for an empty report).

## Design decisions

- **Honest owner = `qc/` (observability), not `build/`.** `runperf` is a
  read-side reporting view; it imports FROM `build.attempts` (a clean, acyclic
  `qc → build → core` edge — `build.attempts` imports no qc), and `build/` never
  imports it. Putting it in `build/` would place a perf view inside the engine,
  exactly the smell the §8.7 pin guards against. Enforced by a test: no file under
  `src/manju/build/` may contain `runperf` (verified: clean).
- **CLI-JSON only, no schema id.** The report is a derived view surfaced via the
  CLI (`--json` is plain CLI JSON), NOT written to `reports/` as a
  content-addressed evidence doc, so it carries **no `manju.*/vN` schema id** —
  the RunManifest (`manju.run-manifest/v1`) remains the one schema'd projection of
  this evidence.
- **Reuse over reimplement.** `run_performance` derives from
  `build_run_manifest` (the canonical projection) for `terminal_status`, `costs`,
  `failures`, and the attempt records, then adds performance aggregates. Cost math
  reuses `attempts._cost_of`, so the numbers can never drift from the manifest.
- **Latest-run resolution** (`run_id=None`) = the last `run_id` written on the
  stream (file order = append order under the events flock = run order, since
  builds are serialized by `build_lock`). Format-independent, deterministic.

## Tests (`tests/test_fp_runperf.py`, 22, red-first)

Synthetic attempt-evidence fixtures (the dr03c idiom: `append_attempt` with
explicit `duration_ms`/`started_at`/`ended_at`, which are preserved verbatim —
never re-timed) pin exact values: stage durations (3000+500+1 = 3501ms),
wall (11000ms), slowest ordering, provider(3500)/local(8001)/render(8000) split,
cache 1/3 = 0.3333 (generate 1/2, render 0/1), cost prefers actual (0.5, the 9.0
estimate never added), per-provider/per-shot cost, error grouping,
`recorded_output_bytes` = 51000. Plus: structured-empty on missing events / unknown
run_id / torn tail (never crashes); the `unavailable[]` honesty pins (qc_time +
disk_usage present with missing-source; cache NOT present because the audit found
a source); latest-run resolution; determinism (two calls equal, JSON-stable);
the no-build-import §8.7 pin; a no-wall-clock guard; and 3 CLI tests (`--json`
equals the report verbatim, human summary, graceful no-run).

## Deviations

- The addendum flagged the cache row as the honesty test ("assert cache row IS in
  unavailable unless the audit found a real source"). The audit **found a real
  source** (`SKIPPED_CACHE_HIT` is recorded per-stage), so the honest outcome is
  the inverse: cache is derived and populated, and a test asserts it is NOT in
  `unavailable[]`. This is a resolved-branch, not a deviation from intent.
- Snapshot regeneration folded in parallel Loop K's `fixity` command alongside
  `perf` (surface 119 → 121). Expected per "fold-ins normal"; `perf` is in
  `cli.py` on disk, so any later regeneration by either loop preserves it. Loop K's
  regions (pack/unpack/fixity, `tests/test_fp_fixity*`) were not touched.
- No commit/push (per addendum); working tree left modified for integration.
