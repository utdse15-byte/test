# AI_IDE_21G Completion

Two INDEPENDENT anti-overbuild gates, run honestly and judged separately (§5:
neither justifies the other). Both came back GREEN on the real code / a
reproducible benchmark — **final path: SKIPPED_WITH_EVIDENCE for both,
production files changed: 0**. Evidence: `tests/test_c21g_gates.py` (7 always-on
characterization tests + 1 opt-in 5-run timing benchmark). Like 09_11G, no red
reproduced anywhere.

```text
VISUAL_WORKSPACE: SKIPPED_WITH_EVIDENCE
THROUGHPUT_HINTS: SKIPPED_WITH_EVIDENCE
```

Baseline audit, full surface inventory, transcript and benchmark numbers:
`REPORTS/AI_IDE_21G_BASELINE.md`.

---

## Gate A — VISUAL_WORKSPACE: SKIPPED_WITH_EVIDENCE

**Finding.** The serve-mode Board already carries the entire visual
review-and-fix core in ONE surface: shot wall with inline seekable `<video>` +
poster stage, side-by-side take COMPARE with one synchronized play button and
per-take metadata, per-take verdict chips (好/弃) + selected★ + sel-reject
warning, the 16 preview-LADDER chips, 2D BLOCKING SVG, the tabbed inspector
(project/subs/bible/log/assets/QC), render-verdict header chips, and the 8 safe
actions (select/redo/rollback/build/qc/package/export). Pinned by
`test_a_board_expresses_the_visual_review_core`.

**The honest gap** (`test_a_board_does_not_render_the_specialized_views`): the
Board does NOT render candidate-FAMILY grouping or KEEPER badges (08_10_12C
§12.3 already SKIPPED that — family data rides the qc-brief JSON), 15 continuity
DRIFT / reviewer-agreement, 17 WORLD-STATE diff / season-health dashboard, a 19
CROP-keyframe editor / color scopes, 18 WORD/VISEME timing overlays / waveform,
or a read-only LINEAGE graph. These are exactly the contract's *allowed* minimal
extensions — none is built.

**Why SKIPPED, not IMPLEMENTED.** §2 requires proving, SIMULTANEOUSLY, a real
>4-surface round-trip that the Board *cannot express* AND that the user needs
*visually*. The recorded review-and-fix transcript (inspect bad take → compare
candidates → check continuity → read routed repair → route repair → adopt
keyframe) touches ~5 surfaces, but the Board is one of them and owns the visual
work (inspect/compare/redo/select); the drops (continuity drift, world-state,
disposition, reframe/adopt) are **structured-data best read as CLI JSON** or
**professional editing 19 explicitly routes to the NLE** (`build/roundtrip.py`).
Each delegated view has a working, tested owner
(`test_a_delegated_views_have_working_cli_service_owners`:
`qc.production.reviewer_agreement`/`candidate_families`,
`series_state.world_state_at`/`season_health`, `media/timing.py`,
`media.reframe.compile_crop_keyframes`). No dispersal causing real errors or
unreasonable round-trips reproduced — the contract's actual threshold (not
">1 surface"). No speculative panel built.

Forbidden list respected absolutely: no node-graph runtime truth, no 3D scene
engine, no canvas coordinates in build, no chat Session DB, no multi-user/CRDT/
RBAC.

## Gate B — THROUGHPUT_HINTS: SKIPPED_WITH_EVIDENCE

**Finding.** The current generation scheduler is `build.graph._concurrent_generate`
— a bounded `ThreadPoolExecutor` that dispatches READY nodes greedily in **plan
(shot-index) order** and commits **strictly in plan order** regardless of
completion (`test_b_current_scheduler_dispatches_in_plan_order`). Reordering the
same READY nodes changes ONLY wall-clock, never the committed outcome / trip /
cancel / cost (`test_b_reordering_ready_nodes_changes_only_wallclock_not_outcome`
— the invariant §3 forbids the optimization from breaking). Parallel slots (≥2)
exist ONLY under an opt-in build mode; the default path is SERIAL
(`test_b_default_build_is_serial_so_order_is_irrelevant`:
`knobs_for(None) is None`; modes = 2/4/8).

**The benchmark** (opt-in `MANJU_C21G_BENCH=1`; deterministic sleep-calibrated
fake provider driving the REAL scheduler; N=12, ≥2 slots, 5 runs each; current
plan order vs oracle longest-first/LPT):

| duration spread | W=2 gap | W=4 gap |
|---|---|---|
| same-length ~1.2x (realistic short-drama clips) | **+0.4%** | **+3.1%** |
| moderate ~2x | +4.2% | +10.5% |
| hetero ~4x | +2.7% | +18.0% |

The measurement is STABLE (sub-ms σ — the gap is real signal, not noise), but it
is a **monotone function of duration heterogeneity**: for realistic same-length
clips the current order **already ~matches the oracle** (within noise), and the
gap only becomes "明显/clear" (>10%) at 2-4x spread AND ≥4 slots. Decisive
against a real bottleneck: (a) default is SERIAL — no ordering effect unless a
mode is opted into; (b) a FIRST build has no attempt history, so estimates come
from the manifest (∝ duration_ms → near-uniform for same-length clips) exactly
on the largest build; (c) a binding provider `max_concurrent` semaphore collapses
the gap to ~3%; (d) **no real project/timing corpus exhibiting the gap exists** —
the heterogeneity is a synthetic assumption, and the reproduced gap is the
textbook LPT-vs-FIFO makespan delta, not a Manju-specific bottleneck on a real
project. §3 requires "在真实项目上稳定明显落后" — stable yes, but not clear on
realistic input, and not on a real project. → SKIPPED_WITH_EVIDENCE; no ordering
hint built.

Forbidden list respected: GraphDiagnostics stays a read-only VIEW (never a second
scheduling truth — already so per `graphdiag.py`); no Redis/Celery/BullMQ, no
worker lease/fencing, no cross-host, no edges inferred from prompt/story, no
extra paid submit.

## No-duplication / no-overbuild proof
- no new visual node runtime, 3D engine, canvas-truth, chat DB, CRDT (Gate A).
- no new queue/scheduler/worker daemon, no lease store, no second scheduling
  truth, no reordering code path (Gate B).
- 0 production files changed; 0 new public schemas; the only new file is the
  evidence test `tests/test_c21g_gates.py`.

## Verification

| command | result |
|---|---|
| `python -m pytest tests/test_c21g_gates.py` | 7 passed, 1 skipped (opt-in benchmark) |
| `MANJU_C21G_BENCH=1 python -m pytest tests/test_c21g_gates.py -k benchmark -s` | 1 passed (numbers recorded above) |
| `python -m pytest tests/test_board_serve.py tests/test_boards.py tests/test_c16_board.py tests/test_dr03b_graphdiag.py tests/test_c21g_gates.py` | 94 passed, 1 skipped |
| `python -m pytest` (full suite, excl. parallel agent's in-flight c20 corpus) | **3021 passed, 13 skipped** in 945.42s (0:15:45), exit 0 |

The 13 skipped = this contract's 1 opt-in benchmark + ~12 pre-existing env skips.
The c20a/c20b corpus + golden fixtures were `--ignore`d only because the parallel
agent is mid-edit on them this cycle (they are that agent's deliverable, not
ours); every other test file, including `tests/test_c21g_gates.py`, ran green.

## Final path
- **VISUAL_WORKSPACE: SKIPPED_WITH_EVIDENCE**
- **THROUGHPUT_HINTS: SKIPPED_WITH_EVIDENCE**
- production files changed: **0**; new public schemas: **0**
- tests added: `tests/test_c21g_gates.py` (7 always-on + 1 opt-in benchmark)
- NOT touched: DECISIONS.md, README.md, and the parallel agent's corpus
  (`tests/fixtures/golden/`, `tests/test_c20b*`, `REPORTS/AI_IDE_20B*`).
  No commit/push.
