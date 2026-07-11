# AI_IDE_21G Baseline — Audit & Gate Inputs

Contract: `AI_IDE_21G`（条件式视觉工作区与单机吞吐优化门禁 — audit-first, two
INDEPENDENT gates, default outcome **SKIPPED_WITH_EVIDENCE** for each, §5:
neither gate justifies the other). Repo HEAD at audit: `7edda2b` (AI_IDE_19
landed; 14/15/16/17/18/19 + 08_10_12C all present, full suite green). Branch
`claude/cost-optimization-strategy-cjfmn5`.

Gate evidence file: `tests/test_c21g_gates.py` — 7 always-on characterization
tests (Gate A inventory + Gate B invariants) all GREEN at HEAD, plus 1 opt-in
5-run timing benchmark (`MANJU_C21G_BENCH=1`). No red reproduced anywhere →
**0 production files changed** (like 09_11G).

A parallel agent is concurrently expanding the golden corpus
(`tests/fixtures/golden/`, `tests/test_c20b*`, `REPORTS/AI_IDE_20B*`) — those
files are theirs; this cycle did not touch them.

---

## Gate A — 派生视觉工作区

### §2 audit — what the Board (`board/board.py` + `board/server.py`) actually renders

| Candidate surface | Verdict | Evidence (file:symbol) |
|---|---|---|
| shot wall + inline `<video>` + poster stage | ALREADY_IMPLEMENTED | `board.py:_render_take` (video + `reports/frames/<shot>.jpg` poster), `server.py:_serve_media` (Range/206 seek, allowlist) |
| side-by-side take COMPARE, one synchronized play | ALREADY_IMPLEMENTED | `board.py:_render_compare` (`compare-grid`/`cmp-cell`/`cmp-meta`), `_SERVE_JS:syncPlay/playAll` |
| per-take director VERDICT chips (好/弃) + selected★ + sel-reject warning | ALREADY_IMPLEMENTED | `board.py:_render_take_note` (`tv-ok`/`tv-no`), `_render_shot` sel-reject |
| 16 preview-LADDER chips (stage + keyframe-approval + next-step cost) | ALREADY_IMPLEMENTED | `board.py:_ladder_chips` → `qc.production.ladder_view` (read-only, serve-only) |
| 2D BLOCKING diagram (derived SVG, no canvas truth) | ALREADY_IMPLEMENTED | `board.py:blocking_svg` (pure, from Shot camera fields; §16 §7 WP3) |
| tabbed inspector: project / subs / bible / log / assets / QC | ALREADY_IMPLEMENTED | `board.py:_render_panels` + `_PANEL_TABS`; SRT timing table, Bible 🔒, events log |
| render verdicts (final/proxy skip-or-rebuild) header chips | ALREADY_IMPLEMENTED | `board.py:_render_header` → `build.explain.explain` |
| actionable veneer: select · redo · rollback · build · qc · package · export | ALREADY_IMPLEMENTED | `server.py:API_ACTIONS` (exactly 8; dangerous unlock/gc/pack deliberately ABSENT) |
| candidate-FAMILY grouping / KEEPER badges | **NOT ON BOARD (by design)** | 08_10_12C §12.3 SKIPPED_WITH_EVIDENCE (`REPORTS/AI_IDE_08_10_12C_COMPLETION.md` rows 23/95c); family data lives in the qc-brief JSON (`qc.production.candidate_families`) |
| 15 continuity DRIFT / reviewer-agreement view | NOT ON BOARD (CLI JSON owner) | `qc.production.reviewer_agreement`, 15 `drift_trend` — `manju continuity`/`series` JSON |
| 17 WORLD-STATE diff / season-health dashboard | NOT ON BOARD (CLI JSON owner) | `core.series_state.world_state_at`/`season_health`; `series status --json --health` (17: 无新命令组, no board) |
| 18 WORD/VISEME timing overlay / waveform / J-L cut | NOT ON BOARD (sidecar owner) | `media/timing.py` `<take>.align.json` (compiler-consumed); no board overlay |
| 19 CROP-keyframe editor / color scopes | NOT ON BOARD (CLI+NLE owner) | `media.reframe.compile_crop_keyframes` → framing artifact; adopt via CLI / `build/roundtrip.py` → NLE (19 explicitly routes to the NLE) |
| read-only LINEAGE graph | NOT ON BOARD (derivation owner) | `qc.production.candidate_families` join (redo_of chain) — derivation-only, not a board graph |

Verified by grep: `board.py` contains **no** `family`/`keeper`/`crop`/`viseme`/
`waveform`/`drift`/`world_state`/`season`/`scope`/`lineage` render markers
(pinned by `test_a_board_does_not_render_the_specialized_views`).

### The review-and-fix transcript (09_11G style)

A realistic interrupted review-and-fix session on a flagged shot S007 (a bad
take: continuity drift on a jacket variant + framing off for the vertical
target). The honest command/surface sequence:

```text
1. inspect the bad take     → manju board --serve, play S007 take inline   [BOARD]
2. compare candidates       → Board ⧉ 对比 compare (synced side-by-side)    [BOARD]
3. check continuity drift    → manju continuity --json / 15 drift_trend      [CLI JSON]
4. check world-state/variant → manju series status --json --health (17)      [CLI JSON]
5. read the routed repair    → qc brief JSON: disposition + primary variable  [CLI JSON]
6. route the repair          → Board 重做 redo / select  (or manju redo)      [BOARD]
7. reframe / adopt keyframe   → manju reframe → adopt / NLE roundtrip (19)     [CLI + NLE]
```

§2 触发条件 scored (must hold SIMULTANEOUSLY):

| condition | held? |
|---|---|
| 1. 真实项目需在 4+ 命令/报告间反复跳转 | partial — a full fix touches ~5 surfaces, but the Board is ONE of them and carries the visual core (1,2,6); the drops (3,4,5,7) are structured-data / NLE tasks |
| 2. 现有 Board 不能表达 ladder/lineage/continuity/crop/audio-timing | **no (as a whole)** — the Board DOES render the 16 preview-ladder; it does not render lineage/continuity-drift/crop-editor/audio-timing, but those are non-visual JSON or NLE-delegated |
| 3. 扩展 Board 能复用同一 core service | yes — the services exist (would be a read-only view join) |
| 4. 不需要新事实源/节点 Runtime | yes |
| **decisive: dispersal causes real errors / unreasonable round-trips?** | **no** — the visual work stays on the Board; dropping to `continuity`/`reframe`/the NLE for a specialized task is the honest home, not error-causing dispersal |

The narrow gaps the addendum flagged (19 crop-keyframe, 18 word-timing) have **no
board surface** — but Gate A also requires proving the user needs them
**visually** rather than via the existing CLI/NLE roundtrip. 19 explicitly routes
crop editing to the NLE; 18 alignment is compiler-consumed evidence. The
"needs-it-visually" prong fails. → **VISUAL_WORKSPACE: SKIPPED_WITH_EVIDENCE**
(no speculative panels built).

---

## Gate B — 单机吞吐与排序提示

### §3 audit — the current scheduler

`build/graph.py` is, by its own doc, a PHASED pipeline with **no scheduler and no
inter-shot edges** (`graphdiag.py` header: the derived graph "is a diagnostic
VIEW, never a second build graph and never a scheduling truth source" — the
contract's forbidden GraphDiagnostics-as-second-truth is already respected).

- **Dispatch policy**: `_concurrent_generate(items, gen_one, *, max_workers, …)`
  (graph.py:400) fills a bounded `ThreadPoolExecutor` greedily from
  `items[next_i]` in the order given — **plan / shot-index order**
  (`_plan_generation` iterates `statuses` in index order). No estimate-based
  reordering exists today (`test_b_current_scheduler_dispatches_in_plan_order`).
- **Commit is strictly plan order** regardless of completion (graph.py:1438 loops
  `video_plan` in order, applying `_commit_one(done.get(shot))`) — so dispatch
  order can affect ONLY wall-clock, never outcome/identity
  (`test_b_reordering_ready_nodes_changes_only_wallclock_not_outcome`).
- **Parallel slots exist ONLY under a build mode**: `max_workers = mode_knobs
  .max_workers if mode_knobs else 1` (graph.py:1267). No mode → **serial**
  (`knobs_for(None) is None`). Modes: quality=2, balanced=4, speed=8
  (`build/modes.py:BUILD_MODES`).
- **Per-provider governor**: a shared `threading.Semaphore(max_concurrent)`
  nested under the pool (`_provider_semaphore`) — the real throughput cap when
  shots share one cloud provider.

### The benchmark (deterministic sleep-calibrated fake provider, 5 runs)

Drives the REAL scheduler `_concurrent_generate` with a fake `gen_one` that
sleeps a calibrated per-shot duration; measures makespan of the current
(plan/index) order vs an oracle longest-first (LPT) ordering. N=12 nodes, ≥2
slots, 5 reps. Recorded numbers (`MANJU_C21G_BENCH=1 python -m pytest
tests/test_c21g_gates.py -k benchmark -s`):

| duration spread | W (slots) | current(plan) | oracle(LPT) | gap | 2σ noise |
|---|---|---|---|---|---|
| same-length ~1.2x (realistic clips) | 2 | 2366.3 ms | 2356.4 ms | **+0.4%** | 6.7 ms |
| same-length ~1.2x | 4 | 1215.8 ms | 1179.4 ms | **+3.1%** | 0.4 ms |
| moderate ~2x | 2 | 2632.2 ms | 2526.1 ms | +4.2% | 261 ms |
| moderate ~2x | 4 | 1402.8 ms | 1269.4 ms | +10.5% | 0.4 ms |
| hetero ~4x | 2 | 2048.0 ms | 1995.0 ms | +2.7% | 18.8 ms |
| hetero ~4x | 4 | 1185.2 ms | 1004.6 ms | +18.0% | 0.3 ms |

Supplementary sensitivity runs (scratch, same scheduler):
- **Noisy estimate**: the optimization must sort by an ESTIMATE, not the true
  (unknown) duration. Sorting by `true·exp(N(0,σ))` still captured 89%/77%/57% of
  the ideal gain at σ=0.2/0.4/0.6 — LPT is forgiving (rough order suffices).
- **Binding provider cap**: all 12 shots on one provider, `max_concurrent=2`,
  pool W=8 → gap collapses to **+3.3%** (the semaphore, not the pool order, is
  the governor); C=3 → +8.9%.

### §3 触发条件 scored

| condition | held? |
|---|---|
| 1. 12+ 待生成节点 | yes (N=12) |
| 2. 2+ 并行 Provider/worker slot | yes — **but only under an opt-in build mode**; default is serial (gap ≡ 0) |
| 3. 重复 5 次,当前策略壁钟时间**稳定明显**落后 | **stable yes** (sub-ms σ), **"明显"（clearly） no** for realistic input — same-length clips gap +0.4%/+3.1% is within noise; the "current order already ~matches" the oracle |
| 4. 优化不改变提交/预算/fallback/commit-order/hash | yes — a pure READY-node reorder changes none (pinned) |

**Decisive facts against a real bottleneck**: (a) the default path is SERIAL —
ordering is irrelevant unless a mode is opted into; (b) a FIRST build has no
attempt history, so estimates come from the manifest (∝ duration_ms →
near-uniform for same-length clips) exactly when the biggest build runs; (c) the
gap is a monotone function of duration heterogeneity that only becomes "clear"
(>10%) at 2-4x spread AND ≥4 slots — a favorable corner; (d) **no real
project/timing corpus exhibiting the gap exists** — the heterogeneity is a
synthetic assumption. The reproduced gap is the textbook LPT-vs-FIFO makespan
delta ((4/3−1/3W) vs (2−1/W)), not a Manju-specific bottleneck on a real
project. → **THROUGHPUT_HINTS: SKIPPED_WITH_EVIDENCE** (no ordering hint built).

---

## §4 研究来源 (consulted, not copied)

- Visual workspace: Nomi, Inline-Studio, SPITE, Loomic, Toonflow-app — free
  node-graph / canvas workspaces; the contract's forbidden list (no node runtime
  truth, no 3D, no canvas-in-build, no chat DB, no CRDT) is exactly what
  separates Manju's derived read-only Board from these. No pattern here overrode
  the SKIP evidence.
- Throughput: forge-film, ViMax, ai-short-drama, openstory — most add a
  queue/worker daemon (Redis/Celery/BullMQ), which the contract forbids and the
  benchmark shows is unwarranted for single-host, mode-gated parallelism.
