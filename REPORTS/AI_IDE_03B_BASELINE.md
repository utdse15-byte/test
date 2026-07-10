# AI IDE 03B — Baseline Audit (基线审计)

Deliverable for **Manju Deep Research 03B — explicit-DAG diagnostics & failure blocking**. This file is the frozen WP0 baseline of HEAD before any WP1 change; the paired build report is `REPORTS/AI_IDE_03B_COMPLETION.md`. WP0 discipline: if an equivalent `GraphDiagnostics` already existed, the batch would STOP with a characterization test and report `ALREADY_IMPLEMENTED`. **The gate PASSED → BUILD a pure diagnostics core + a read-only derivation.**

---

## 1. Header — branch / commit / date / environment

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` (`.git/HEAD`, read-only) |
| Base commit audited | `cf18041` (DR03A baseline + completion reports landed) |
| Date | 2026-07-10 |
| Python | 3.11.15 |
| ffmpeg | `6.1.1-3ubuntu5` present (real-build c1 pin exercised) |
| Process rules honored | no git commands; `python -m pytest` only |

---

## 2. Calibration re-verified — Manju has NO explicit edge-based dependency graph

The orchestrator's calibration was re-verified locally with my own greps (verbatim). `graphdiag.py` (the new module) is excluded — it deliberately INTRODUCES the vocabulary.

**G-CAL1 — zero explicit-edge-graph tokens ✅**

```
$ grep -rnE "depends_on|predecessor|topolog|networkx|in_degree|indegree|\bDAG\b" \
      src/manju --include=*.py | grep -v graphdiag.py
src/manju/cli.py:2215:        help="DR03B: append explicit-DAG diagnostics — ...   (← my new flag help)
src/manju/mcp/tools.py:581/586:  "...explicit-DAG diagnostics VIEW."            (← my new tool desc)
```

The only hits are the three DR03B strings I added. There is **no** `depends_on`, `predecessor`, `topological*`, `networkx`, `in_degree`/`indegree`, nor an edge-graph `DAG` anywhere in the pre-existing tree.

**G-CAL2 — no GenerationUnit / task-dependency edge / handoff concept ✅**

```
$ grep -rnE "GenerationUnit|task_depend|dependency_edge|edge_graph" src/manju --include=*.py | grep -v graphdiag.py
(no matches)
```

The `handoff` / `fragment` tokens that DO exist are unrelated: `qc/prompt_checks.py` (clause *fragments*), `media/packaging.py` (torn-JSON *fragment*), `exporters/jianying.py` (BGM trim *handoff*), and `build/shotpackage.py:17` which explicitly states **"fragment / generation-unit ids never mint Shot identity"** — i.e. Manju rejects the concept.

**G-CAL3 — no scheduler / worker pool beyond bounded parallel generation ✅**

```
$ grep -rniE "scheduler|worker_pool|workerpool" src/manju --include=*.py | grep -v graphdiag.py
(no matches — no scheduler)
```

The only concurrency is `build/graph.py` `_concurrent_generate` (goal 14): a **bounded `ThreadPoolExecutor` over per-shot generation** whose commit is applied strictly in plan order (deterministic), with **no inter-task edges** — runtime ledger rows are independent (`_record_local_runs`).

**Verdict:** the calibration holds. Manju's build is a PHASED pipeline with deliberate per-shot failure isolation, not an explicit DAG. The honest deliverable is therefore (A) a pure generic graph-diagnostics core implementing the full contract semantics, exercised over synthetic explicit graphs, and (B) a read-only DERIVATION of Manju's actually-modeled dependencies into that explicit form. No inferred edges; no second build graph; never a scheduling truth source.

---

## 3. The modeled build — phase structure, fan-out, states (file:line)

### 3.1 Phase order — `_run_build_phases` (`build/graph.py`)

```
$ grep -nE '_phase\("' src/manju/build/graph.py
705  _phase("check")        # 0. hard gate (run_check); not-ok → return, no generation
823  _phase("generate")     # 2. per-shot video (+voice plan), per-shot failure isolation
1060 _phase("voice")        # 2v. TTS for MISSING voices
1169 _phase("compile")      # 3. compile timeline (pure fn) — the fan-IN join
1276 _phase("captions")     # 4. export captions FROM the compiled timeline
1290 _phase("render:audition") / 1313 render:final:locale / 1356 render:{proxy,final}  # 5. render
1411 _phase("qc")           # 6. QC (gate, not a dependency)
1445 _phase("exports")      # 7. otio/jianying/capcut FROM the timeline
```

Ordered pipeline: **check → generate → voice → compile → captions → render → qc → exports**. Every phase is skippable by target/gen; a `CompileError`/`MediaError` sets `ok=False` and returns (no later phase runs).

### 3.2 Per-shot fan-out (bounded parallel generation)

`_concurrent_generate` (`build/graph.py:182-276`) drives `gen_one` over the video plan in a bounded pool (`max_workers`, `mode_knobs`); **serial when no build mode** (byte-identical). The commit (`_commit_one`, `:919-988`) is applied **in plan order** regardless of finish order — deterministic. **No inter-shot edges**; each shot writes its own take dir.

### 3.3 Failure isolation — a shot that fails all providers

`_gen_one` (`:859-917`) catches `ProviderFailure`/`NeedsHumanInput` → `takes=None`; `_commit_one` (`:931-947`) records a `generate` failure (`所有供应商都失败,该镜头没有可用镜头`) and returns — **the build does NOT abort at generation** (per-shot isolation, §8.4). The block happens later, at compile (§4.1 below).

### 3.4 States — `build/stale.py` `ShotState` + `build/voice.py` `VoiceState`

| ShotState (`stale.py:27-33`) | meaning | `usable` (`:45-49`) |
|---|---|---|
| `MISSING` | no takes at all | ✗ |
| `FRESH` | selected take, spec_hash equal (cache hit, skip) | ✓ |
| `STALE` | selected take, spec changed (flag only, §4.3) | ✓ |
| `MANUAL` | hand-imported take, never auto-invalidated | ✓ |
| `NEEDS_SELECTION` | takes exist, none selected | ✗ |
| `BROKEN` | selected take's media/sidecar gone | ✗ |

`usable == state ∈ {FRESH, STALE, MANUAL}`. `VoiceState` (`voice.py:22-27`): `NOT_NEEDED/MISSING/FRESH/STALE/MANUAL`; **stale/manual voices are advisory-only (§4.3)** — the compiler falls back to take/default duration when a voice is absent (`compiler.py:190-202`), so **voice never blocks the compile** (→ the OPTIONAL edge in the derivation).

### 3.5 Render/export dependencies, cache semantics, failure records

- **compile fingerprint** = `Timeline.meta.compiled_from` (`compiler.py:113-161 fingerprint()`); `explain.py:70` calls a fingerprint match "unchanged".
- **final content key** = `media.render.final_content_key` + `.key.json` sidecar; `explain.py:88-107` verdicts "skip (content key matches)" — the render's own cache-hit.
- **exports read the TIMELINE, not the render**: `export_otio/jianying(project, timeline)` (`graph.py:1447-1454`); `target=exports` **skips render** (`graph.py:1410-1421` comment: "its exporters read the timeline, not the composited video"); `exportstatus.py:562-589` compares OTIO mtime vs `timeline.json`. → exports hang off **compile**, never render.
- **failures**: `core/failures.py` `read_failures(project, n, *, level)` (`:406-415`), newest-first, over append-only `reports/failures.jsonl`; a generation failure's `subject` is the shot id, `step="generate"`, `level="error"`, `id="F-…"` (`:127-130`).
- **ask_before gate** (WAITING_USER analog): at `run_build` (`graph.py:806-821`), gated on the WHOLE plan's spend, not a per-node file state → not cheaply detectable per node (documented in the derivation as an intentional omission).

---

## 4. WP0 characterization — the four contract questions answered

Characterization tests live in `tests/test_dr03b_characterization.py` (real behavioral pins, ffmpeg-backed where a build runs).

### 4.1 c1 — a genuinely-failed shot at a final build → **CORRECT-BY-DESIGN**

**Question:** a shot whose generation FAILS on every provider (no usable take) — what does a `target=final` build do? Does it block honestly, or silently render while omitting the shot's required content (a truth-table violation)?

**Observed (test `test_c1_final_build_refuses_when_a_required_shot_has_no_usable_take`, real build, S001 usable + S002 all-providers-fail):**

```
ok:           False
render_path:  None
finals after: []                      (== finals before; nothing written)
errors:       ['cannot compile timeline, unresolved shots:\n  S002: missing']
failures:     ('generate','S002',error)  AND  ('compile','timeline',error)
```

**Mechanism (file:line):** `gather_compile_input` (`compiler.py:716-744`) appends every `not status.usable` indexed shot to `problems`; `:802-805` raises `CompileError`; `_run_build_phases` (`graph.py:1235-1247`) catches it, sets `ok=False`, records the `compile` failure, and **returns before the render step**. So a FAILED required predecessor BLOCKS the successor — exactly the truth table. **No silent omission is possible.**

**Verdict: CORRECT-BY-DESIGN.** The truth table already holds by construction. → **WP2 = no production change** (see completion report; `REJECTED_WITH_REASON`).

**The optional-edge analog (pinned too):** `test_c1_audition_target_tolerates_missing_take_via_slate_by_design` — the SAME missing take, under `target=audition`, is TOLERATED: `gather_compile_input(allow_missing_takes=True)` (`compiler.py:717-741`) inserts a `__slate__` placeholder and the audition renders (`ok=True`, `render_path=renders/audition/…`, `timeline.json` untouched). This is EXPLICIT DESIGN (an optional-edge analog: audition treats picture as optional), **not** a violation.

### 4.2 c2 — multi-parent handoff → **NOT-APPLICABLE to Manju's own graph**

Manju models no explicit multi-parent content-handoff (grep-zero, §2 G-CAL2). c2 is therefore `REJECTED_WITH_REASON/not-applicable` for Manju's own pipeline; the handoff semantics (`HANDOFF_UNSPECIFIED` / `HANDOFF_UNKNOWN_PARENT`, never `predecessors[0]`) live entirely in the **new pure module** and its synthetic-graph tests (`test_dr03b_graphdiag.py` test 9). Pinned by `test_c2_no_multiparent_handoff_concept_in_manju` + `test_c2_no_explicit_dependency_edge_graph`.

Note: the DERIVED graph's `compile:timeline` node legitimately fans IN from every indexed shot's `gen` node (>1 required parent) and so is conservatively flagged `HANDOFF_UNSPECIFIED` — but for Manju that fan-in is an **index-ordered assembly** (`shots/index.yaml` is the explicit order authority, `stale.py:121-135`), not an ambiguous handoff. The flag is informational; the "downstream view may filter" clause applies.

### 4.3 c3 / c4 — input mutation / dict-order sensitivity → the NEW module

These apply to the pure core, not Manju's build. Pinned as property-style tests: no input mutation (`test_dr03b_graphdiag.py` test 11, deep-compare), dict-insertion-order irrelevance (test 12), canonical-JSON stability (test 13).

---

## 5. STOP-vs-BUILD verdict ✅ BUILD

No equivalent `GraphDiagnostics` / explicit-edge dependency graph / topological analyzer exists (§2). The WP0 rule to STOP-with-`ALREADY_IMPLEMENTED` does not trigger. Build proceeds: `src/manju/build/graphdiag.py` (pure core + read-only derivation) + an additive `explain --graph` flag (CLI + MCP parity). Runtime fixes (WP2) and scheduling hints (WP3): see §4.1 (no-change) and the completion report (`NOT_IMPLEMENTED`).

---

## 6. Non-negotiables carried into the build (recorded)

| # | Non-negotiable | How WP1 honors it |
|---|---|---|
| 1 | Diagnostics read ONLY explicitly-modeled dependencies | derivation edge table cites the code for every edge; NO edge from scene order / shared characters / prompt similarity / LLM / filesystem / creative-text causality |
| 2 | Truth table exact | `_own_disposition` + severity fold; SUCCEEDED/valid-cache satisfy, FAILED/CANCELED/UNKNOWN block, WAITING_USER waits, RUNNING/PENDING pend, invalid cache = STALE_CACHE_HIT (not satisfying) |
| 3 | Multi-parent handoff explicit; never `predecessors[0]` | `HANDOFF_UNSPECIFIED` (>1 required parent, no `handoff_from`) always flagged; order permutation inert (test 10) |
| 4 | Pure functions | no DB/clock/randomness/network; inputs never mutated (test 11); dict-order independent (test 12); canonical-JSON stable (test 13) |
| 5 | No auto-repair | diagnostics never delete an edge or auto-pick a parent — read-only diagnosis only |
| 6 | Derivation read-only | `derive_build_graph` writes nothing (test 15b tree-hash before/after) |
| 7 | red-first | `test_dr03b_graphdiag.py` import-gate red recorded (`ModuleNotFoundError`); c1/test-15 are genuine behavioral assertions |
