# AI IDE 03B Completion

Build report for **Manju Deep Research 03B — explicit-DAG diagnostics & failure blocking**. Paired baseline: `REPORTS/AI_IDE_03B_BASELINE.md`. Final-run numbers and git state below are filled by the orchestrator after the last full-suite run.

---

## Repository

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| Base commit | `cf18041` (DR03A reports; DR01+DR02+DR03A landed) |
| Pre-existing work preserved | the 4 pre-existing `include_unindexed` test-double-drift failures (`test_director` ×2, `test_round_w_agent_wb` ×1, `test_write_consistency` ×1) are OUT of this batch's set and untouched |
| DR03B landed | `src/manju/build/graphdiag.py` (new), additive `explain --graph` flag in `src/manju/cli.py`, MCP explain parity in `src/manju/mcp/tools.py`, `tests/test_dr03b_graphdiag.py`, `tests/test_dr03b_characterization.py` — working tree otherwise clean but for these two reports |
| Final full-suite result | **4 failed, 2294 passed, 12 skipped** (909.26s / 0:15:09) — the 4 are exactly the pre-existing `include_unindexed` set; **zero new failing test names**; +26 vs DR03A's 2268 = the 26 DR03B tests |
| Final git state | base `cf18041` → `35fa607` (DR03B code: graphdiag module + explain --graph + MCP parity + 30 tests + DECISIONS.md #15 + README row) → the reports commit adding this file + the baseline; working tree clean after it |

---

## Baseline (WP0 verdict)

The gate PASSED → **BUILD** (not `ALREADY_IMPLEMENTED`). No explicit edge-based dependency graph, `GenerationUnit`, task-dependency edge, handoff concept, or scheduler exists in `src/manju` (grep-zero, re-verified locally — baseline §2). Manju's build is a phased pipeline with per-shot failure isolation. The honest deliverable is a **pure generic graph-diagnostics core** (full contract semantics, exercised over synthetic explicit graphs) + a **read-only DERIVATION** of Manju's actually-modeled dependencies. Full evidence with file:line in `REPORTS/AI_IDE_03B_BASELINE.md`.

**c1 verdict — CORRECT-BY-DESIGN.** A real `target=final` build with a genuinely-failed shot (all providers fail, no usable take) returns `ok=False`, `render_path=None`, writes **zero** finals, and records BOTH a `generate` and a `compile` failure — it never silently renders while omitting the shot's required content. `gather_compile_input` lists the unresolved shot and raises `CompileError` (`compiler.py:802-805`); `_run_build_phases` turns that into `ok=False` before the render step (`graph.py:1235-1247`). The truth table already holds → **WP2 = no production change**. The audition target's slate tolerance is the deliberate optional-edge analog, pinned separately.

**Red-first:** `tests/test_dr03b_graphdiag.py` imports `manju.build.graphdiag` at module load — first run (module absent) = collection-time `ModuleNotFoundError: No module named 'manju.build.graphdiag'` (the accepted RED for a brand-new pure-algorithm module; captured verbatim by temporarily hiding the module). The genuine behavioral pins are **not** import smoke: c1 drives a real ffmpeg build and observes the block; test 15 derives the graph from a real project and asserts the forbidden-inference pin + failed→FAILED root-cause + `BLOCKED_BY_FAILED_ANCESTOR`. c1 is GREEN against current code — that IS the characterization deliverable (had it been red, WP2 would have engaged).

---

## Existing systems reused (not duplicated)

| System | file:line | How reused |
|---|---|---|
| `hash_value` / `canonical_json` | `core/hashing.py:23-31` | `graph_digest` = canonical, recursively-key-sorted hash over `{graph, states}` → key-order independent |
| `evaluate_all` / `ShotState` / `.usable` | `build/stale.py:121-135` / `:27-49` | derivation's per-shot node status (indexed_only=True — the compile authority) |
| `evaluate_all_voices` / `VoiceState` | `build/voice.py:87-90` / `:22-27` | voice node status; NOT_NEEDED → no node; advisory → OPTIONAL edge |
| `read_failures` | `core/failures.py:406-415` | newest-first `(step,subject)→failure id` map; MISSING+recent generate error → FAILED root cause |
| compile fingerprint | `timeline/compiler.py:113-161` + `explain.py:70` | compile node cache-valid check (meta.compiled_from match) |
| `final_content_key` + `_read_key_sidecar` | `media/render.py` (via `explain.py:88-107`) | render node cache-valid check |
| `deliverables` freshness | `build/exportstatus.py:750-765` | export node status (no second staleness path invented) |
| `explain()` service | `build/explain.py:117-171` | `--graph` appends `diagnose_project()` alongside the existing doc; explain shape stays backward-compatible |
| MCP tool registry + `_schema` | `mcp/tools.py:559-563` | explain tool gains one optional `graph` boolean via the shared `_h_explain` service |
| CLI `_emit` / typer `explain` command | `cli.py:2207` | one additive `--graph` option on the existing command (no new top-level command) |

---

## Diagnostics — `manju.graph-diagnostics/v1`

### Pure core API (no manju imports beyond the canonical hasher)

```python
# node-state constants:
SUCCEEDED  SKIPPED_CACHE_HIT  FAILED  CANCELED  WAITING_USER  RUNNING  PENDING  UNKNOWN
SCHEMA = "manju.graph-diagnostics/v1"

validate_graph(graph: dict) -> dict          # structural: {issues[], cycles[], node_count, edge_count}
compute_ready(graph: dict, states: dict) -> dict   # {node_id: {ready, effective, blocked_by[]}}
diagnose(graph: dict, states: dict) -> dict   # the full manju.graph-diagnostics/v1 document
```

`graph = {"nodes": [{node_id, kind, handoff_from?, estimated_ms?}...], "edges": [{from, to, optional?}...]}`.
`states = {node_id: {status, root_cause_id?, cache_valid?}}`. `SKIPPED_CACHE_HIT` satisfies ONLY when `cache_valid` is true (else `STALE_CACHE_HIT`).

**Document shape:** `{schema, graph_digest, nodes[], summary{}, issues[]}`. Each node: `{node_id, kind, status, required_predecessors[sorted], optional_predecessors[sorted], ready, effective, blocked_by[{node_id, status, root_cause_id, path}]}`. `summary = {node_count, edge_count, ready, blocked, pending, waiting, failed, cycles}` (ready/blocked/pending/waiting partition all nodes by effective disposition; `failed` is an overlay = count of input-FAILED nodes). `issues[]` sorted by `(code, node_id)`, codes: `UNKNOWN_NODE, DUPLICATE_NODE, SELF_EDGE, DUPLICATE_EDGE, CYCLE, NEVER_READY, HANDOFF_UNSPECIFIED, HANDOFF_UNKNOWN_PARENT, BLOCKED_BY_FAILED_ANCESTOR, BLOCKED_BY_CANCELED_ANCESTOR, WAITING_ANCESTOR, UNKNOWN_ANCESTOR, STALE_CACHE_HIT`.

### Truth table (as implemented in `_own_disposition` + the severity fold)

| predecessor status | effect on successor (REQUIRED edge) |
|---|---|
| `SUCCEEDED` | satisfies |
| `SKIPPED_CACHE_HIT` + `cache_valid` | satisfies |
| `SKIPPED_CACHE_HIT` + not valid | does NOT satisfy → PENDING + `STALE_CACHE_HIT` issue on that node |
| `FAILED` | BLOCKED (root cause = this node; `BLOCKED_BY_FAILED_ANCESTOR`) |
| `CANCELED` | BLOCKED (`BLOCKED_BY_CANCELED_ANCESTOR`) |
| `UNKNOWN` / unrecognized | BLOCKED / INVALID (`UNKNOWN_ANCESTOR`) |
| `WAITING_USER` | WAITING (`WAITING_ANCESTOR`) |
| `RUNNING` / `PENDING` | PENDING |
| any status on an OPTIONAL edge | never blocks |

Severity fold: `BLOCKED > WAITING > PENDING > SATISFIED`; a node's `effective` = worst of its own disposition and its required predecessors'. `ready` = every required predecessor is effectively SATISFIED. Multi-hop root cause: origins (own-BLOCKED nodes) are forward-propagated over required edges; each blocked node's `blocked_by` cites the origin, its `root_cause_id`, and the lexicographically-smallest shortest path. Cycles: elementary cycles over required edges, each canonicalized to its lexicographically-smallest start (insertion-order independent); every in-cycle node is `NEVER_READY`.

### Purity proofs (tests)

- **inputs never mutated** — deep-compare before/after `validate_graph`/`compute_ready`/`diagnose` (test 11).
- **no dict-insertion-order dependence** — `diagnose` deep-equal under permuted node/edge/state insertion order; `graph_digest` identical (test 12); cycle path identical under permutation (test 1).
- **deterministic / canonical-JSON stable** — two runs byte-equal after `json.dumps(sort_keys=True)` (test 13); two `diagnose` calls deep-equal.
- **no wall clock / randomness / network / DB** — pure-core imports only `core.hashing.hash_value`.

### Derivation — `derive_build_graph(project) -> (graph, states)` (impure boundary, read-only)

Projects Manju's actually-modeled dependencies. **Edge table (every edge cites the code that models it; NO other edge exists):**

| edge | req? | modeled by |
|---|---|---|
| `gen:<shot>` → `compile:timeline` | REQ | `compiler.py:716-744` (not-usable indexed shot → problems) + `:802-805` (raises); `graph.py:1235-1247` (→ ok=False, no render) |
| `voice:<shot>` → `compile:timeline` | OPT | voice advisory (§4.3): `voice.py:22-27`; `compiler.py:190-202` falls back to take/default duration when a voice is absent |
| `compile:timeline` → `captions` | REQ | `graph.py:1272-1286` `export_captions(project, timeline)` (only when `rules.captions.enabled`) |
| `captions` → `render:final` | OPT | `graph.py:1371` `render_timeline(..., ass_file=ass_path)` — burn used if present, render runs without it |
| `compile:timeline` → `render:final` | REQ | `graph.py:1356-1372` `render_timeline(project, timeline, ...)` composites the timeline |
| `compile:timeline` → `export:<kind>` | REQ | `graph.py:1447-1483` `export_otio/jianying(project, timeline)`; `target=exports` SKIPS render (`graph.py:1410-1421`); `exportstatus.py:562-589` (otio vs timeline.json) — exports read the timeline, never the render |

Deliberately **no** `gen:<a>→gen:<b>` edge (shots independent; per-shot isolation) and **no** edge from scenes/characters/prompts/filenames (pinned by test 15's forbidden-inference assertion).

### State-mapping table (as implemented — the mapping is a VIEW; never drives execution)

| source state | → node status | rationale |
|---|---|---|
| shot `FRESH` | `SKIPPED_CACHE_HIT` (cache_valid=true) | `stale.py:8` "cache hit, skip" — spec_hash match + media present IS the valid-cache semantics |
| shot `MANUAL` | `SUCCEEDED` | hand-placed selected take, never auto-invalidated (§4.3) |
| shot `STALE` | `PENDING` | usable on the timeline (§4.3) but out-of-date vs current spec → the VIEW reports currency (regenerable), not buildability |
| shot `MISSING` + recent `generate` error | `FAILED` (root_cause_id = failure id) | produced nothing AND a level=error record names the shot (the c1 pin) |
| shot `MISSING` (no failure) | `PENDING` | a gap the build will fill |
| shot `NEEDS_SELECTION` | `PENDING` | build auto-selects the newest usable take (`graph.py:1123-1146`) → pending-resolution, not waiting-on-human |
| shot `BROKEN` | `FAILED` | selected take's media/sidecar gone (`stale.py:74-89`) — also a hard check error |
| voice `FRESH`/`MANUAL`/`STALE`/`MISSING`(+err) | cache-hit / SUCCEEDED / PENDING / (FAILED) | same shape; `NOT_NEEDED` → no node emitted |
| compile fingerprint match | `SKIPPED_CACHE_HIT` (valid) else `PENDING` | `meta.compiled_from` == recompile (`explain.py:70`) |
| render final-key match | `SKIPPED_CACHE_HIT` (valid) else `PENDING` | final key sidecar == recomputed (`explain.py:88-107`) |
| captions files present + compile cached | `SKIPPED_CACHE_HIT` (valid) else `PENDING` | derived FROM a cached timeline |
| export `UP_TO_DATE`/`VERIFIED` / `PROBLEMATIC` / other | valid-cache / `FAILED` / `PENDING` | `exportstatus.py` freshness reused |

**Intentional omission (documented, not invented):** `ask_before`-gated pending spend → `WAITING_USER` is NOT emitted — that gate lives at `run_build` (`graph.py:806-821`) over the whole plan, not on any per-node file state, so it is not cheaply detectable per node.

### Surface — `manju explain --graph [--json]` (+ MCP parity)

Additive flag on the EXISTING `explain` command (no new top-level command). `--json` appends the full doc under `"graph"`; human mode prints a summary line (`节点/边/ready/blocked/pending/waiting/failed/cycles`, labelled "只读派生视图,非调度真相") + one line per issue. The MCP `explain` tool gained one optional `graph` boolean via the shared `diagnose_project()` service — default (no arg) is byte-identical to before (test `test_14_mcp_explain_parity_carries_graph`). **`impact` was deliberately NOT wired** — the contract's "explain/impact CAN reference diagnostics" is satisfied via `explain`; adding a second surface would duplicate the service for no new capability (decision recorded).

---

## WP2 — runtime fixes: NONE (REJECTED_WITH_REASON)

| Candidate | Verdict | Evidence |
|---|---|---|
| Block a final render when a required shot has no usable take | **REJECTED_WITH_REASON — already correct** | c1 pin: `gather_compile_input` (`compiler.py:802-805`) → `CompileError` → `_run_build_phases` (`graph.py:1235-1247`) → `ok=False`, `render_path=None`, zero finals, both `generate`+`compile` failures recorded. Test `test_c1_final_build_refuses_when_a_required_shot_has_no_usable_take`. |
| "Silent omission" of a failed shot | **Not possible** | the compiler NEVER drops a not-usable indexed shot — it lists it in `problems` and refuses; the only tolerant path is `target=audition` with explicit `allow_missing_takes=True` slates (`compiler.py:717-741`), which never writes `timeline.json`. |

Manju's per-shot isolation and audition slates are DESIGN, not bugs. Current semantics already satisfy the truth table → **no build-code change**. (Zero edits to `build/graph.py`, `timeline/compiler.py`, `build/stale.py`.)

---

## WP3 — SchedulingHints: NOT_IMPLEMENTED (REJECTED_UNLESS_BENCHMARKED)

Per the contract default. No scheduling/ordering code was written. Reasoning: there is **no stable benchmark harness** in the repo to prove an ordering gain, and the modeled build is already deterministic — bounded parallel generation commits in plan order (`graph.py:182-276`), and compile/render are single fan-in/linear phases with no reorderable frontier of independent expensive tasks whose reordering is provably faster. The node schema carries `estimated_ms` for forward-compatibility, but **`diagnose()` never reads it and emits no scheduling directive**, and `derive_build_graph` never writes (tests 16 + 15b). Status recorded: `NOT_IMPLEMENTED`.

---

## Files created / changed

| File | Change | Lines |
|---|---|---|
| `src/manju/build/graphdiag.py` | **new** — pure core + read-only derivation (incl. the edge/state citation tables in the module docstring) | 826 |
| `src/manju/cli.py` | **+1 option** `--graph` on `explain` + human render block | +~22 |
| `src/manju/mcp/tools.py` | explain handler `graph` branch + one `graph` boolean in the tool schema | +~15 |
| `tests/test_dr03b_graphdiag.py` | **new** — 22 tests (contract list 1–16 + parity + derive read-only + schema pin; +1 orchestrator: merge_policy satisfies the handoff rule and real multi-shot projects carry no permanent HANDOFF_UNSPECIFIED noise) | ~445 |
| `tests/test_dr03b_characterization.py` | **new** — 4 tests (c1 real-build block, c1 audition tolerance, c2 grep evidence ×2) | 163 |
| `REPORTS/AI_IDE_03B_BASELINE.md` / `AI_IDE_03B_COMPLETION.md` | **new** — this pair | — |

No edits to `build/graph.py`, `timeline/compiler.py`, `build/stale.py`, `core/`, or `models.py` — exactly the file boundary the spec set (WP2 no-change).

---

## Tests

**Targeted regression set** (`test_explain` + `test_impact` + `test_stale` + `test_failures` + the two new files): **70 passed** (33.5s). Broader safety sweep (`test_mcp` + `test_cli` + `test_interconnection` + `test_funnel` + `test_qc_consistency` + `test_qc_agent` + `test_dr03a_shotpackage`): **132 passed**, zero regressions. Full-suite (orchestrator, after ALL changes incl. the review corrections): **4 failed, 2294 passed, 12 skipped** — the 4 are the pre-existing baseline set, zero new failing names.

**Orchestrator review corrections on top of the agent's delivery:** (1) the pure core now accepts an explicit `merge_policy` as the contract's sanctioned alternative to `handoff_from` (`handoff_from、merge policy 或等价字段`) — and the node-normalization layer carries the field (it was previously dropped, which would have made the rule dead); (2) the derived `compile:timeline` node declares `merge_policy: "index_order"` (shots/index.yaml IS the assembly contract), replacing the agent's permanent informational `HANDOFF_UNSPECIFIED` on every multi-shot project — synthetic multi-parent graphs without either field still flag, so the Forge-Film `predecessors[0]` rejection is intact; (3) one added test pinning both.

### `tests/test_dr03b_graphdiag.py` — pure core + derivation (21)

| # | Class | Assertion | Result |
|---|---|---|---|
| 1 | cycle path stable | permuted node/edge order → identical canonical cycle `[A,B,C]`; rotated to smallest start; in-cycle → `NEVER_READY` | ✅ |
| 2 | unknown/duplicate/self edges | `DUPLICATE_NODE`/`SELF_EDGE`/`DUPLICATE_EDGE`/`UNKNOWN_NODE` all flagged | ✅ |
| 3 | FAILED blocks successor | `B.ready=False`, `effective=BLOCKED`, `blocked_by` cites A + root cause; `BLOCKED_BY_FAILED_ANCESTOR` | ✅ |
| 4 | CANCELED/UNKNOWN/WAITING don't unlock | parametrized: successor not ready; correct ancestor issue + effective | ✅ |
| 5 | valid vs invalid cache hit | valid → satisfies (ready); invalid → NOT satisfying + `STALE_CACHE_HIT` | ✅ |
| 6 | optional edge never blocks | FAILED optional parent → successor `ready=True`, `blocked_by=[]` | ✅ |
| 7 | multi-hop root cause | A fails → C blocked via B, `path == [A,B,C]`, root_cause_id carried | ✅ |
| 8 | recompute after retry | A `FAILED`→`SUCCEEDED` on the SAME graph → B becomes ready | ✅ |
| 9 | multi-parent handoff | 2 required parents no `handoff_from` → `HANDOFF_UNSPECIFIED`; valid `handoff_from` clears it; bogus one → `HANDOFF_UNKNOWN_PARENT` (never predecessors[0]) | ✅ |
| 10 | predecessor-order permutation inert | reversed node/edge order → identical `diagnose`; `blocked_by` unchanged | ✅ |
| 11 | no input mutation | deep-compare graph+states before/after all three functions | ✅ |
| 12 | dict-order irrelevant | permuted states insertion order → identical doc + digest | ✅ |
| 13 | canonical JSON stable | two runs byte-equal after `sort_keys` | ✅ |
| 14a | explain --graph CLI | `--graph --json` carries the doc (schema/nodes/summary/issues); human mode smoke | ✅ |
| 14b | MCP explain parity | tool schema has `graph`; `graph=True` returns the doc; no-arg byte-identical | ✅ |
| 15 | derive on real project | shots→compile edges exist; **ZERO edge between two shots sharing scene+character**; failed shot → `FAILED` w/ failure id; render `BLOCKED_BY_FAILED_ANCESTOR` via `[gen:S002,compile:timeline,render:final]` | ✅ |
| 15b | derive read-only | tree-hash before/after `derive_build_graph`+`diagnose_project` unchanged | ✅ |
| 16 | no scheduling directive | no `schedule/order/hints/next/plan/sequence` keys; `estimated_ms` change is inert | ✅ |
| — | schema pin | `SCHEMA == "manju.graph-diagnostics/v1"` | ✅ |

### `tests/test_dr03b_characterization.py` — WP0 pins (4)

| Test | Assertion | Result |
|---|---|---|
| c1 final block | real build, S002 fails all providers → `ok=False`, `render_path=None`, zero finals, `generate`+`compile` failures | ✅ |
| c1 audition tolerance | SAME missing take under `target=audition` → `ok=True`, renders via slate, `timeline.json` untouched | ✅ |
| c2 no handoff concept | grep-zero for `handoff_from`/`GenerationUnit`/`depends_on`/`predecessors[`/`indegree`/networkx over `src/manju` (excl. new module) | ✅ |
| c2 no edge graph | grep-zero for explicit-edge-graph tokens | ✅ |

**Red-first evidence (verbatim, module temporarily hidden):**
```
ERROR collecting tests/test_dr03b_graphdiag.py
E   ModuleNotFoundError: No module named 'manju.build.graphdiag'
```
c1 passes with the module absent (it pins EXISTING build behavior — module-independent).

---

## Deviations (with reasons)

| Deviation | Reason |
|---|---|
| Exports depend on `compile:timeline`, NOT `render:final` | The spec's §4.2 lists a "compile → captions → render → export chain per phase order" but also "required only where the code requires them / NO other edges". The CODE shows exporters read the TIMELINE (`graph.py:1447-1454`) and `target=exports` **skips render entirely** (`graph.py:1410-1421`); `exportstatus.py:562-589` confirms OTIO freshness is vs `timeline.json`. Modeling a `render→export` edge would be an INVENTED edge (exports never consume the render). Honest data-dependency wins over naive phase-order. |
| `FRESH` → `SKIPPED_CACHE_HIT` (not `SUCCEEDED`); `MANUAL` → `SUCCEEDED` | The spec offers both mappings for a usable fresh take and says "pick one, document it". `stale.py:8` calls FRESH literally "cache hit, skip", so `SKIPPED_CACHE_HIT(valid)` is the most faithful and exercises the cache-satisfies path; a hand-placed MANUAL take is a deliberate success, not a cache skip. Both satisfy the truth table. |
| `STALE` → `PENDING` (even though STALE is `usable` for the real build) | Spec-explicit ("STALE → PENDING (regenerable)"). The VIEW reports **currency vs the current spec**, not buildability; the real build still uses a stale selected take (§4.3). Documented in the state table; the mapping never drives execution. |
| `compile:timeline` carries a permanent `HANDOFF_UNSPECIFIED` when ≥2 shots | Contract-mandated ("conservative: always flag"). For Manju the fan-in is an index-ordered assembly (not an ambiguous handoff); the "downstream view may filter" clause makes it informational. Kept visible + documented rather than special-cased out of the pure core. |
| `impact` NOT wired | The contract allows either explain OR impact to reference diagnostics; satisfying via `explain` (CLI + MCP) is enough. A second surface would duplicate `diagnose_project()` for no new capability. |
| per-node `ready` + `effective` fields beyond the schema's listed node keys | additive; `ready` makes test 8 ("state flip → ready") a clean assertion and `effective` powers the summary partition. Backward-compatible (extra keys). |

No other deviations. No `build/graph.py` / `timeline/compiler.py` / `core/` edits.

---

## Acceptance self-check (contract non-negotiables)

| # | Requirement | Status | Where |
|---|---|---|---|
| 1 | Diagnostics read ONLY explicitly-modeled dependencies; no inferred edges | ✅ | edge table cites code for each; forbidden-inference pin (test 15) — two shots sharing scene+character → ZERO edge |
| 2 | Truth table exact (incl. valid-vs-invalid cache) | ✅ | tests 3,4,5,6; `_own_disposition` + severity fold |
| 3 | Multi-parent handoff explicit; never predecessors[0]; order-permutation inert | ✅ | tests 9, 10 |
| 4 | Pure: no DB/clock/randomness/network/mutation/dict-order | ✅ | tests 11, 12, 13; pure-core imports only `hash_value` |
| 5 | No auto-repair (no edge deletion / auto parent pick) | ✅ | diagnosis only; nothing mutates the graph |
| 6 | Derivation read-only; canonical/deterministic doc | ✅ | test 15b (tree-hash); tests 12, 13 |
| 7 | c1 characterization = real behavioral pin; verdict decides WP2 | ✅ | c1 CORRECT-BY-DESIGN → WP2 no-change |
| 8 | explain --graph surface (CLI + MCP parity); no new top-level command | ✅ | tests 14a/14b; `impact` not wired (decision) |
| 9 | WP3 scheduling hints NOT_IMPLEMENTED; no scheduling directive | ✅ | test 16; `estimated_ms` inert |
| 10 | red-first; targeted suites green | ✅ | `ModuleNotFoundError` RED recorded; 70 targeted passed |
