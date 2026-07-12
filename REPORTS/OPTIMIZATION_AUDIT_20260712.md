# Manju optimization audit — 2026-07-12 four-hour deep-research program

Nine read-only research dimensions in parallel (performance, code quality,
test-suite efficiency, robustness, architecture/coupling, features/UX,
dependencies + AI surface, GUI deep audit, provider adapters), followed by a
28-agent ADVERSARIAL REFUTER PANEL over every finding (each refuter
instructed to disprove its claim at the cited lines). Panel outcome:
17 CONFIRMED / 11 ADJUSTED / 0 REFUTED — adjustments are folded in below.
Orchestrator separately hand-verified the headline mechanisms before the
panel ran. Per-dimension raw reports and the full verdict JSON live in the
session records; each recommendation below carries its constraining pins —
this repo's aggressive golden/contract pinning is the primary refactor
hazard, and the do-not-touch list is as load-bearing as the fixes.

The two DEFECTS (not optimizations) found: the Quickstart select-crash
(fixed considerations pending as a follow-up loop) and the undeclared
Pillow dependency (FIXED in this window: loop Z1 — adapter wall
ColorStatsUnavailable + colorstats extra + doctor probe).


Method detail: the dimensions ran as read-only agents (parallel agents) + orchestrator
measurements and adversarial verification of every headline claim at its
cited line. Measured numbers are labeled; estimates are labeled; negative
results are RECORDED to protect against wrong refactors. This audit is the
benchmark-evidence foundation the item-11 discipline requires — no
performance change ships in the window that produced it. Cross-reference:
the legacy REPORTS/OPTIMIZATION-ASSESSMENT.md predates the FP program and
the R/S/T/U/V/W/X waves; this audit supersedes its performance sections.

## Tier 1 — measured, high-impact, low-risk (orchestrator-verified)

1. PERF/tail_events (core/events.py:187-201, verified): the last-N reader
   parses the ENTIRE unbounded events.jsonl. Measured 195ms at 9.8MB vs
   0.095ms backward-read (2042x); hot on `manju status` and every GUI
   refresh; events.jsonl never rotates. Fix: seek-from-EOF block read.
   Constraint: torn-line tolerance must be preserved.
2. PERF/voice probe per compile (timeline/compiler.py:943, verified): live
   ffprobe per shot per compile (44.8ms measured each) while the adjacent
   video path reads the cached sidecar probe; voice synthesis never fills
   its own sidecar probe field. ~25s per 500-shot compile (estimate). Fix:
   fill at synthesis + read the cache; voice takes are append-only so the
   cache cannot go stale.
3. PERF/double hashing (media/render.py:1128-1132 + :438, verified
   mechanism): final-key payload computes every segment key, then
   _build_segment recomputes identical keys; animatic hashes each still
   twice (graph.py:216/:231). SHA-256 is CPU-bound at 370MB/s measured.
   Fix: process-scoped hash_file memo keyed (abspath, size, mtime_ns) —
   digests byte-identical, cache-key discipline intact.
4. PERF/CLI import cost (measured twice independently — agent + my own
   -X importtime run agree): manju.core.models eagerly builds 46 pydantic
   schemas (58ms self) inside a 202ms manju.cli import. defer_build=True
   measured 59->15ms class-def; a lazy check/container chain (~145ms off).
   Constraint: 27 `except ProjectError` sites need the PEP 562 path.
5. TESTS/make_sample preset (measured): sample clips encode with ffmpeg
   default `medium`; `ultrafast` saves ~10-15s across 32 files; clip bytes
   feed no pinned hash (spec_hash is literal "manual").

0a. DEFECT (UX, refuter-verification pending): the README Quickstart
   crashes on its own step 3 — `manju select <shot> --file` on a fresh
   project registers the take BEFORE checking the shot exists, then dumps
   a raw ProjectError traceback and leaves orphan media/gen/<shot>/
   take_01.* that `manju check` never flags; `voice` and `redo` share the
   uncaught-traceback path while align/impact/prompt/routing catch it
   cleanly (the house pattern exists — apply it to the three verbs).
   Fix candidate: check-then-register + the standard catch. S/L-effort,
   HIGH user impact (it is the first command a new user runs wrong).
0b. DEFECT (orchestrator-confirmed at all cited sites): undeclared
   Pillow — qc/colorstats.py:82 bare `from PIL import Image`, declared
   NOWHERE in pyproject (base or extras), doctor has zero PIL probes,
   yet core/toolchain.py:93 _KEY_DEPS tracks it. Clean pip install →
   any color_stats() caller crashes ModuleNotFoundError while doctor
   reports green (Z1 correction: no CLI command exists today — the
   import path itself is the crash surface for library/future callers).
   Fix (queued as loop Z1): adapter-wall the import (house
   ExporterUnavailable pattern) + doctor probe row + a colorstats extra.
   The only actual DEFECT the audit found — everything else is
   optimization.

## Tier 2 — measured/verified, medium effort or with governance weight

6. TESTS/xdist feasibility (measured worklist): the suite is nearly
   parallel-ready (port=0 servers, env isolation, per-tmp sqlite); 11 raw
   os.chdir sites (2 confirmed leaking: tests/test_providers_routing.py:
   447,612) are the blockers. Projected 17 -> ~6-8 min. Requires the
   pytest-xdist dep decision (operator).
7. TESTS/fixture sharing: qc_consistency + dr03c rebuild a real-ffmpeg
   project per test; module-scoped read-only builds save ~15-20s each
   with zero pin risk (mutating tests keep fresh builds).
8. CODE/cli.py decomposition (verified structure): 8847 lines, 66
   commands, but ALREADY 15 sub-apps composed via add_typer + a lazy
   extraction precedent (cli_workflows.py). The archive region
   (~3766-5010) is ~pure helpers + 4 commands -> core/archive.py as a
   VERBATIM move (member order + 1980 stamps byte-pinned). Start with the
   migrate slice.
9. CODE/atomic-write unification: no core atomic_write_bytes; three
   implementations + 12 inline os.replace sites with INCONSISTENT fsync
   (absent in gui/userstate, webpreview, supportbundle, generic_cloud —
   some write non-rebuildable state). Add the core helper; no golden risk.
10. CODE/dual ToolError (verified: build/toolmap.py:60 vs mcp/tools.py:47):
    two same-named exceptions with different meanings; rename one. Plus:
    no common ManjuError root across 45 domain errors (catch-all
    ergonomics); 7 zero-caller public functions (list in the CODE report);
    presets/__init__.py carries 240 lines of models (move to spec.py).
10a. ARCH/the one hard coupling (qc/runperf.py:61, module-level reach
    into build.attempts privates _cost_of/_root — the only edge closing
    the module-level build<->qc package cycle): move the shared
    constants to a leaf module (on-disk ledger vocabulary — values stay
    byte-identical). S/M.
10b. ARCH/acyclicity guard test: the module-level import graph is fully
    acyclic ONLY by the lazy-import convention (682 of 1144 internal
    edges lazy — measured); pin it with an SCC test like every other
    house invariant. S/M.
10c. ARCH/de-facto-public privates: _target_duration_ms (10 cross-pkg
    sites), _hex (9), _estimate_shot_cost (7) — extract the pricing/
    timing kernel from build/graph (2583 lines) into a public leaf;
    spend/cache pins constrain to a verbatim move. M/H.
10d. ARCH/server transport substrate: gui and board independently
    reimplement Range/206 + path-jail + Host guard on the same stdlib
    base (board's comments admit the mirroring); extract the transport
    layer, keep both byte-pinned bodies. M/H-maint.
10e. ARCH/core layering: 18 upward imports from core/ (all lazy) are
    orchestration modules misfiled in the foundation (evaluate,
    series_state, locale) — relocate to make "core imports nothing
    upward" checkable. M/L.
10f. ARCH/storage-authority guard: the §3 ladder is actively enforced
    (a past violation was found DELETED with its record at
    delivery.py:855); remaining derived-report reads are display-only.
    Add the guard test that keeps derived files out of build/cache/
    spend inputs. S/M.
11. CI/M0 smoke redundancy: ci.yml re-runs an M0 smoke after the full
    suite already ran test_e2e_m0 (~30-60s CI time; the gate file is
    orchestrator-owned — change as its own reviewed commit).

## Tier 3 — product/discipline decisions (not mechanical)

11a. DEPS-AI/MCP token cost (measured): 26 tools, tools/list ≈ 3,383
    tokens per agent session; the biggest descriptions carry changelog
    cruft (round names, reviewer references) that is history, not
    call-time schema. The agent-surface digest EXCLUDES description
    text — trimming reclaims hundreds of recurring tokens with zero
    contract churn. Consolidating tool clusters would move the digest —
    do not. S/High-recurring.
11b. DEPS-AI/SKILL.md staleness: the always-injected cheat sheet omits
    migrate / locale / pack --bagit / both import-plans, and the MCP
    export enum under-advertises formats (enum change touches the
    stable mcp-tool-surface; SKILL.md rows are free). S.
11c. DEPS-AI/bare-pytest footgun diagnosed: /root/.local/bin/pytest is
    a uv tool-venv shim without manju; ci.yml uses the script form while
    xplat.yml uses python -m pytest — align ci.yml to module form
    (orchestrator-owned file). XS.
11d. DEPS-AI/phantom dep: _KEY_DEPS tracks OpenTimelineIO which is
    imported nowhere (otio.py is deliberately schema-lite) — every
    toolchain manifest records a meaningless "absent". Drop the row
    (record-only contract). XS.
11e. DEPS-AI/--json success envelopes: errors are uniform ({error,code},
    exit 1) but successes split between ok-carrying and bare domain
    dicts — agents can only trust the exit code. Normalization is gated
    by the STABLE cli-json-surface snapshot: document exit-code-is-the-
    signal now; normalize as a reviewed surface change if ever. S-doc.
11f. DEPS-AI/wheel data honesty: CONTRACTS.yaml is not packaged; harmless
    today because contracts.py is test-only governance (verified no
    runtime importer) — record the constraint so a future runtime
    consumer does not assume it ships. XS-doc.
11g. UX/flat --help: 65 top-level commands render as one wall;
    rich_help_panel used zero times — a one-file grouping into ~7 panels
    is the highest-leverage discoverability fix. S/L.
11h. UX/Quickstart staleness: README's Quickstart diverges from the
    test-pinned `manju help-workflow new-project` (which inserts create
    and avoids the crashing call); also README's `manju refs <shot>` is
    wrong (real form `refs shot <shot>`) and the docs validator cannot
    catch argument-form drift — README fix + a validator-depth note. S/M.
11i. UX/status --json next-step is localized prose, not a machine token —
    agents cannot branch on it; add a stable `next_action` token field
    (additive, snapshot-safe). S/M.
11j. UX/no shot-scaffolding verb: the creation funnel points at hand-
    written YAML; `ingest` only maps onto existing shots. Product gap —
    a `manju shots add` scaffolder is the biggest new->build friction
    remover. M/M, product decision.
11k. UX/misc confirmed: `manju new` lacks --json; --vertical (24fps) vs
    --preset vertical_ai_video (30fps) inconsistency; route vs routing
    near-duplicate groups; board pro controls have zero title= tooltips.
    Error-message quality is bimodal — the excellent house style exists
    but three common paths skipped it (shot-file-not-found, unknown
    target, providers show).
12. events.jsonl rotation policy: unbounded today (only failures.jsonl
    rotates); rotation interacts with the evidence discipline — needs a
    design ruling (age/size-based archive that PRESERVES the audit trail,
    e.g. rotate to events-YYYYMM.jsonl, never delete).
13. zh/en message pairing: mostly disciplined "中文 (english)" but
    qc/agent_review.py VerdictError block is zh-only — i18n consistency
    decision.
14. gui/board media-gate duplication (security-adjacent): the same
    resolve-then-recheck idiom implemented twice (gui/server.py:742,
    board/server.py:448) — extract one Project.safe_served_path to
    prevent one-sided hardening. OPT-ROBUST confirmed both instances are
    CORRECT today (gate before AND after resolve, pre-resolved root) —
    the risk is future one-sided fixes, not a live gap.
15. ROBUST/board CSP gap (board/server.py:417-430): the GUI ships a
    strict CSP + X-Frame-Options; the board ships none. Escaping is
    complete (_esc everywhere, textContent-only JS) so this is layer-2
    hardening — and NOT a one-liner: the board inlines its JS and one
    onclick, so the fix externalizes JS first. Med/Med.
16. ROBUST/fcpxml_import input caps: XXE/SSRF confirmed unreachable
    (DTD-less stdlib ET); residuals are a missing byte cap (multi-GB
    OOM) and billion-laughs depending on libexpat >= 2.4.1. Low/Low:
    cap bytes in _load_text, optionally refuse DOCTYPE/ENTITY prologs.
17. ROBUST/board oversize-POST keep-alive desync (server.py:582-596):
    oversize bodies are not drained; localhost + token-gated so
    robustness-only. Fix: Connection: close on oversize. Low/Low.

## Do-NOT-touch list (negative results, measured — protect these)

- Timeline compile is O(n) and fast (500 shots = 12ms measured); the cost
  is gather I/O, not the algorithm.
- hash chunk size is irrelevant (CPU-bound); don't tune it.
- The float round(ms*fps/1000) idiom at render.py:594/otio.py:99 is
  BYTE-IDENTITY-PINNED legacy behavior, deliberately coexisting with
  exact timebase math — consolidation would break goldens.
- state.sqlite usage is clean (WAL, indexed lookups); no action.
- GUI/board string building already uses joins; no O(n^2) accumulation.
- The render's veryfast/crf18 pipeline settings are byte-pin-frozen.
- test sleeps total 3.66s and are mostly load-bearing race-window holds.
- ARCH negative result: do NOT add an exporter writer base class — the
  8 formats already share the right seam (compile/export split, yamlio,
  timebase, hashing, conform registry); residual code is irreducibly
  format-specific and independence is a deliberate risk-isolation choice.
- DEPS-AI negative results: all three base deps genuinely used; the
  four toolbelt extras are ==-pinned behind honest adapter walls;
  constraints.txt + toolchain-manifest are the reproducibility controls
  (pyproject looseness does NOT undermine byte-drift explanations);
  events.jsonl CONTENT is lean (169 append sites sampled — the cost is
  reader-side, already Tier 1 #1).
- ROBUST confirmations (do not "fix" what is sound): file-serving gates
  correct on both surfaces; unpack zip-entry trust REFUTED (absolute/../
  symlink members rejected, decompression preflight, staged atomic
  restore); zero shell=True anywhere; yaml.safe_load exclusively (12
  sites); events/failures writers leak no secrets (argv capped, IDs
  only); zero mktemp; _esc/quote=True coverage complete; no
  pickle/eval/exec.

## GUI dimension (OPT-GUI, measured on scratch projects, landed last)

G1. CRITICAL /review render: 16.6s at 40 shots — qc_brief(consistency)
    runs inline on the request thread with 316 ffprobe + 158 ffmpeg
    spawns (cProfile); _member_frame subprocess-probes what the sidecar
    probe already carries (the SAME pattern as Tier-1 #2's voice probe).
    Fix: read sidecar.probe first (10 lines), then make the section a
    lazy /api fetch. UNPINNED (substring tests only).
G2. HIGH build_state doubles the work: evaluate_all AND
    evaluate_all_voices each run TWICE per state build (status.py +
    state.py independently); per shot load_shot x5, takes x4. Measured
    705ms@40 / 1.76s@100 shots on EVERY state change. Fix: compute once,
    thread through. /api/state SHAPE pinned, plumbing free.
G3. HIGH /edit render: 3.4s — full build.explain recompile inline for a
    dirty-badge string (the exact op state.py's own docstring says to
    keep on-demand) + 40 inline probe spawns.
G4. MED the GUI<->board transport substrate duplication measured at
    ~180 security-sensitive lines with DIVERGED allowlists — confirms
    ARCH F4 from the GUI side; board static HTML byte-pin is the
    binding constraint.
G5. MED ~8.3KB byte-identical JS helpers copied across 10 page modules
    (post x10, toast x10, token x12, pollJob x5) — fully unpinned, the
    safest dedup win.
G6. LOW jobs.jsonl fsync x4/job incl. on the POST thread for a
    self-declared disposable log; 3 HTTP-layer-untested endpoints.
GUI negative results: NO byte-identity golden exists on any GUI render
(the real byte pin is board-side) — findings G1-G3/G5 are less
constrained than assumed; project_fingerprint already optimal (1.1ms);
no dead feature flags; static css/js render cost ~0.

## Adversarial verification (28-refuter panel, complete)
17 CONFIRMED / 11 ADJUSTED / 0 REFUTED — no finding died; adjustments
are precision corrections, folded into the tiers above at synthesis:
- defer_build saves ~20ms not ~40 (class construction dominates; an
  overlooked import-time ShotSpec.model_rebuild at models.py:1448 is
  empirically safe under defer_build — the conclusion survives for a
  different reason than claimed);
- a SECOND hard qc->build module edge exists (qc/checks.py:23 via
  qc/__init__ re-export) beyond runperf's — the decoupling fix must take
  both;
- the CI M0-smoke redundancy is pinned by tests/test_idempotency.py,
  not test_e2e_m0 (redundancy stands, citation corrected);
- tail_events hot callers are WORSE than claimed: gui/edit.py and
  build/compare.py call it with n=100_000, history with 10_000;
- lazy-edge share 56.5% of 1099 edges (not 60% of 1144); zero-SCC holds;
- keyframe_gated_shots lives in qc/production.py; ToolError paths need
  the src/manju prefix; cli.py is 8940 lines/65 commands at HEAD;
  8765 never appears in tests (9999 is the mock, unbound); check DOES
  carry ok while tasks does not (envelope inconsistency stands with
  corrected examples); import-plan surfaces number three, not two;
  delivery.py's removed-input record cites Hardening WP5 not §3 (the
  discipline holds, the label differs).

## Pending sections (agents in flight)
- GUI deep audit (OPT-GUI); provider adapters (OPT-PROVIDERS) landed —
  fold at synthesis: local_cmd orphaned-grandchildren timeout (the one
  true leak), transient-5xx-kills-paid-job, FailureKind drift map,
  unbounded generic_cloud job dicts, fallback-floor test gap; negative
  results: determinism clean, routing/preflight/catalog single-sourced,
  idempotency discipline sound, refs caching sound.


## Full-suite durations profile (top 30 of the close run's --durations=60)

Measured on the quiescent close run (3928 passed / 13 skipped, 1048s):

```
26.16s call     tests/test_e2e_m0.py::test_m0_full_build
16.75s setup    tests/test_idempotency.py::test_final_frame_rate_and_duration_qc
14.38s call     tests/test_compare.py::test_e2e_snapshot_persisted_idempotent_and_compare
13.27s call     tests/test_e2e_m0.py::test_m0_incremental_rerender
11.65s call     tests/test_c20b_corpus.py::test_calibration_is_deterministic
10.44s call     tests/test_round_a.py::test_captions_manual_mode_build_stays_idempotent
9.33s call     tests/test_audio_policy.py::test_real_build_with_audio_policy_is_idempotent_and_key_sensitive
9.25s call     tests/test_idempotency.py::test_redo_selected_then_build_adds_exactly_one
8.81s call     tests/test_c16_animatic.py::test_animatic_is_deletable_and_rebuildable
8.73s setup    tests/test_packaging.py::test_intro_is_a_real_rendered_segment
7.93s call     tests/test_round_a.py::test_kenburns_generic_ref_advisory
7.73s call     tests/test_transitions_looks.py::test_applied_xfade_boundary_cache_reused_and_type_change_rerenders_only_boundary
7.46s call     tests/test_c16_animatic.py::test_animatic_render_is_derived_never_a_take_or_selected
7.03s call     tests/test_audio_edit.py::test_muted_shot_segment_is_silent_and_reencodes_exactly_one
6.67s call     tests/test_idempotency.py::test_force_rerenders
6.55s call     tests/test_board_serve.py::test_export_endpoint_produces_files
6.21s call     tests/test_round_a.py::test_proxy_reencodes_when_content_changes
6.14s call     tests/test_c20b_corpus.py::test_calibration_report_is_written_and_honest
5.98s setup    tests/test_packaging.py::test_package_no_stale_warning_right_after_build
5.77s setup    tests/test_dr01_run_evidence.py::test_dr01_e1_sidecar_records_output_sha256
5.77s call     tests/test_board_serve.py::test_build_endpoint_produces_a_final
5.62s setup    tests/test_c20b_corpus.py::test_calibration_primary_is_perfectly_calibrated
5.57s setup    tests/test_branding.py::test_logo_burns_onto_final_without_changing_duration
5.55s call     tests/test_packaging.py::test_editing_intro_text_rerenders
5.37s setup    tests/test_dr03c_lifecycle.py::test_build_run_id_on_result_and_to_dict
5.34s call     tests/test_dr03b_characterization.py::test_c1_audition_target_tolerates_missing_take_via_slate_by_design
5.33s call     tests/test_round_a.py::test_proxy_build_is_idempotent
5.28s call     tests/test_job_cancel.py::test_run_build_checkpoint_stops_between_shots_honest_partial_result
4.97s call     tests/test_presets.py::test_vertical_preset_project_builds_to_final
4.78s call     tests/test_post_completion_hardening.py::test_wp2_concurrent_verification_and_baseline_append_no_torn_or_truncate
```

Consistent with the OPT-TESTS finding: every top entry is a real ffmpeg
encode; the levers are xdist, shared read-only build fixtures, and the
sample-preset fix — never weakening a pin.
