# DECISIONS.md — approved deviations from the v2.2 design

Reviewed and approved in review round 1 (§3). Each entry states the design
text, what was actually built, and why the deviation stands.

索引 (index) — added 2026-07-13 so a session can find what governs a file without a full read.
Entries are append-only below.

| # | date | summary | governs (modules / files / surfaces) |
|---|------|---------|--------------------------------------|
| 1 | — | JianYing export emits both self-dev skeleton and native draft | exporters/native_draft, exports/jianying/ (skeleton + pyJianYingDraft native), final.mp4/SRT/OTIO fallback |
| 2 | — | capcut-cli demoted to a lint-only adapter behind the wall | exporters/native_draft.py (capcut_cli_lint), lint_draft |
| 3 | — | HTML caption-card self-developed on headless Chromium, drawtext floor | media/html_card.py, caption-card provider |
| 4 | — | `auto` and `board` delivered early on the public surface | CLI: manju auto, manju board (MANJU_ACTOR=ai, SKILL.md) |
| 5 | 2026-07-06 | `board --serve`: bounded actionable localhost board | board/ serve server (select/redo/build/qc/package/snapshot/rollback_shot) |
| 6 | 2026-07-06 | `manju auto` drives any one-shot agent CLI | manju auto agent-resolver (MANJU_AGENT, project.yaml:agent, PATH probe) |
| 7 | 2026-07-06 | Preset kits reversed to three neutral frame-only kits | presets/kits (blank/vertical_ai_video/horizontal_ai_video), SKILL.md |
| 5b | — | `manju gui` ships as a pure third engine client | gui/ (manju gui) |
| 6b | — | `ask_before` becomes an engine spend gate, not just discipline | engine spend gate (§8.3); assume_yes (--yes CLI, MCP/GUI) |
| 8 | — | Round W meta calls: known-agent-only, constraints, _fail JSON, refactor deferred | project.yaml:agent, constraints.txt + CI, core `_fail`; deferred cli.py / build/graph.py / media/render.py |
| 9 | — | Round Y closed the round-W accounting gap: four issues repaid | core MEDIA_EXTS / Project.takes, board/server.py, director confirm/execute/state_fingerprint, manju unpack |
| 10 | 2026-07-08 | Review states stored; reference ownership derived, not registered | reports/ingest_batches/, core/refs.py (refs assign), checked_shot_write CAS, gui/jobs, manju evaluate |
| 11 | 2026-07-09 | Interconnection: caption↔shot, timing sidecar, locale overlay, roundtrip | timeline/cuemap.py (CaptionLine.shot), media/align.py timing, locales/<lang>/, roundtrip (JianYing skeleton, OTIO) |
| 12 | 2026-07-10 | DR01: timeline stays compiled truth; OpenClap only at boundary | exporters/openclap/, compiler (VideoClip.source), BuildResult.plan/--dry-run, .key.json + events.jsonl run_id |
| 13 | 2026-07-10 | DR02: bound acceptance evidence; assurance is pure derived state | qc/expectations.py, qc/assurance.py, reports/qc_packets, qc_agent.jsonl (v2 intake) |
| 14 | 2026-07-10 | DR03A: external shot package is a proposal, create-only, CAS apply | manju shot-package (shot-import-plan/v1), Project.save_shot |
| 15 | 2026-07-10 | DR03B: graph diagnostics stay a read-only derived view | build/graphdiag.py, derive_build_graph, manju explain --graph (+MCP) |
| 16 | 2026-07-10 | DR03C: one attempt stream, derived RunManifest, no new ledger | events.jsonl (stage_attempt), build/attempts, reports/runs/ RunManifest, manju tasks manifest |
| 17 | 2026-07-10 | DR04: one provider-fact source, spend-free preflight | providers/catalog.py, providers/preflight.py, routing, manju providers catalog/check |
| 18 | 2026-07-10 | DR06: persistent submission identity; ambiguous outcome recoverable | providers/submission.py, build/attempts.py, events.jsonl submission_state, intents table, manju tasks attach-remote-job/abandon |
| 19 | 2026-07-10 | DR05: declared tool policy, one agent surface, opt-in unattended | mcp/policy.py (ToolPolicy), tools/list + call gate, manju serve-mcp --agent-profile, agent_surface tool |
| 20 | 2026-07-11 | P0: paid-path evidence is a precondition before money moves | core/events.py coordinator, providers/submission (disposition, project_chain), qc/assurance, RunManifest |
| 21 | 2026-07-11 | 07C: release baseline is evidence about exact approved bytes | reports/verifications.jsonl (release_baseline_approved), manju compare --against-baseline, exports release_assessment |
| 22 | 2026-07-11 | 08_10_12C: creative decisions become source diffs, else derived | manju prompt --json workbench, refs transfer (params), verdict v2 decision, TakeSidecar.redo_of, first-party skills |
| 23 | 2026-07-11 | 13C: delivery manifest states facts; sources decide cuts | build/delivery.py (delivery-manifest/v1), NLE / platform handoff, bundle |
| 24 | 2026-07-11 | 09_11G: resume/single-host already owned — deliverable is proof | record (0 production files) — status/tasks/build-lock, tests/test_c0911_gates.py |
| 25 | 2026-07-11 | Post-completion hardening: sixteen external gaps reproduced and repaid | ensure_submission_projection (resume consult + release gate), build/delivery base-identity resolver, submission strict identity |
| 26 | 2026-07-11 | Final acceptance: red CI exposed a real BuildLock race | BuildLock (write-then-hardlink), submission evidence torn-line, release gate (run_id/output_sha256), write_bundle |
| 27 | 2026-07-11 | AI_IDE 14–21G capability program: qualification through semantics | qualification ladder, qc reviewer observations, spend ladder, audio masters, series/world state, media analysis (cutdown/reframe/bridge/toolmap) |
| 28 | 2026-07-12 | 14–21G closeout: admission truth moved off the editable report | providers.base.dispatch_bridge (manju bridge), network admission evidence, cutdown validator, variants/world state, audio masters naming |
| 29 | 2026-07-12 | Function-perfection: governance registry first, honest media semantics | CONTRACTS.yaml + core/contracts.py, core/timebase.py, qc tech/conform/relink, toolchain-manifest, help-workflow, caption roles, unpack zip guards |
| 30 | 2026-07-12 | Rational edit-rate migration executed; interchange continued | core/models edit_rate + wrap serializer, compiler rational spine, otio export, manju migrate, TTML/EDL writers, S4 keys, xplat.yml |
| 31 | 2026-07-12 | T/U wave: plugin freeze, FCPXML, board compare, locale TTML, BagIt | provider-plugin-api freeze, board/ compare (.manju/frames), exporters/fcpxml.py, locales/<lang>/meta.yaml, pack --bagit, test_fp_ratemig1 rule |
| 32 | 2026-07-12 | V-wave: FCPXML connected-audio lanes; board onion skin, scopes | exporters/fcpxml.py (audio lanes), conform doc, board/ (onion + scopes; see 32a c21g tooth) |
| 33 | 2026-07-12 | W/X/Y/Z waves plus the four-hour optimization research program | board transport, exporters/fcpxml (import-plan/loops/fades), EDL audio/import, doctor locale probes, qc/colorstats (Pillow), REPORTS/OPTIMIZATION_AUDIT |
| 34 | 2026-07-13 | OPT wave: six audit items implemented; CI flake killed | tail_events reader, voice probe cache (compiler), gui /review, core hash_file memo, pytest-xdist + ci.yml, help panels / SKILL.md / MCP descriptions |
| 35 | 2026-07-13 | OPT wave 2: providers robustness, GUI/board hardening, test speed | providers (local_cmd/generic_cloud/comfyui), gui/board (safe_served_path, common.js), test fixtures, ci.yml smoke |
| 36 | 2026-07-13 | Windows wave 1: hard gate plus file/process/lock semantics | .github/workflows/windows-ci.yml, runtime/buildlock, core/idents, core/events (msvcrt), core/yamlio, media/ffmpeg, providers/local_cmd, media/align |
| 37 | 2026-07-13 | Windows wave 2: per-user installer, doctor --windows, first verdict | scripts/windows/*.ps1, build/doctor --windows, media/html_card.find_edge, core/supportbundle.redact_private_text, windows-ci install-smoke |
| 38 | 2026-07-13 | Windows wave 3 + gate rounds: xmeml, filtergraph escaping, annotations | exporters/xmeml.py, media/render._escape_filter_path, core locks (recents/library/failures), providers/local_cmd split, media/card find_font, board annotations (review.annotation/v1), ffmpeg 6.1.1 |
| 39 | 2026-07-13 | Windows hard gate GREEN: runs #5–#7, terminal verdict | qc/agent_review.py lock, media/ffmpeg _taskkill_tree, auto-agent harness, windows-ci (green), CLAUDE.md Windows lessons |
| 40 | 2026-07-13 | Windows wave 4: opt-in colour minimal closed loop | core/models ColorSpec, media/render, media/normalize (zscale), technical_profile, qc/colorstats, build/doctor |
| 41 | 2026-07-13 | Windows wave 5: archive hardening, hw-encode facts; C2PA rejected | manju cli pack/unpack, core.idents, media/ffmpeg (HW_ENCODER_CANDIDATES / eligibility), core/toolchain manifest, build/doctor hw row |
| 41a | 2026-07-13 | W5 gate verdict addendum: run #11's three failures fixed | record (gate verdict) — test_mcp self-contained, pack casefold capability probe, CI green both platforms |
| 42 | 2026-07-13 | UX program waves A-E: 46-finding audit, top defects fixed | cli (_fail codes, status/new/unpack guards), gui/server (drain-before-refuse), gui/pages review, board banner/anns, installer probes, REPORTS/UX_AUDIT_2026-07-13.md |
| 43 | 2026-07-13 | UX round 2: AI-collaboration surfaces + loop-until-dry close | mcp/tools + server (ToolError code/payload, arg prechecks), docs/PINS.md, CLAUDE.md, conftest _isolate_providers |
| 44 | 2026-07-13 | Intuitiveness wave: ONE per-shot next action, everywhere | build/status (shot_next_action, todo), cli status 待办, gui/pages review cards, board serve cards, installer -CreateShortcut |
| 45 | 2026-07-14 | GPT-analysis wave: next_step_key + stale-tab project guard | build/status next_step_key, gui/state project_identity, gui/server (_send_text stamp, do_POST 409 project_switched, /api/project-id), common_js/page/glossary JS echo + overlay |
| 46 | 2026-07-14 | Continuity wave: 上次动作 anchor + backup-age doctor row | core/events humanize_age, cli status 上次动作 line, cli pack .manju/last_pack.json marker (pack stays read-only on the tree), build/doctor backup row |
| 47 | 2026-07-14 | Convenience wave 1: shot/take shorthand resolvers, 18 commands | cli._resolve_shot_arg/_resolve_take_arg (existing-only, ambiguity=bad_args, stderr echo), select no-take candidate listing; startup-latency item measured-and-dismissed |
| 48 | 2026-07-14 | Convenience wave 2: generate/recover/deliver verbs never dead-end | cli redo/select/voice/align/repair/tasks/exports/transcribe next-keystroke clauses; _print_batch_result tail hints; exports per-row kind→command map |

## 1. JianYing dual path = self-developed skeleton ∥ pyJianYingDraft

Design (v2.2 §13 M1, decision 8) named pyJianYingDraft primary and
capcut-cli secondary. As built, the dual path is: a **self-developed,
diff-stable draft skeleton** (deterministic uuid5 ids, own lint,
`exports/jianying/<name>/draft_content.json`) **in parallel with** the
**pyJianYingDraft native draft** (`exports/jianying/<name>_native/`). Both are
emitted on every JianYing export; either can be opened/validated
independently, and final.mp4 + SRT + OTIO remain the fallback exits (§14).
Rationale: the skeleton is version-drift-immune and diffable in git; the
native draft is app-faithful. Two implementations that cross-check beat one.

## 2. capcut-cli is demoted to lint

Design listed capcut-cli as the secondary draft *path*. As built, capcut-cli
is a **lint-only adapter** behind the adapter wall
(`exporters/native_draft.py: capcut_cli_lint`): when the binary exists it
lints the exported draft; when absent (as in the build environment — it is a
Node tool) the export falls back to our own `lint_draft` and reports the
degradation as a note, never a failure. Rationale: draft *generation* is
already dual-pathed (deviation 1); a third generator adds risk without
coverage, while its existing lint capability is the part worth keeping (v2.2
decision 8: lint 用现成能力,不自研).

## 3. HyperFrames follows the idea but is self-developed with Chromium

Design (§2.5 P1) named HyperFrames as the html_render provider. As built, the
HTML→deterministic-MP4 idea is implemented **in-repo against headless
Chromium** (`media/html_card.py`: styled HTML → screenshot at exact project
resolution → looped MP4), with zero new Python dependencies; the caption-card
provider prefers it and records the renderer in the take's lineage; drawtext
remains the zero-dependency floor (§8.4). Rationale: P1 semantics are "有现成
实现就装,没有就走自研保底" — HyperFrames itself was not installable in the
target environment, Chromium was; the adapter wall keeps a future swap to the
real HyperFrames a provider-level change.

## 4. `auto` and `board` were delivered early

Design scheduled `manju auto` as a later thin wrapper (v2.2 §10 "后续再加")
and `manju board` under M4. Both shipped early: `board` because it is the
review surface every other milestone's acceptance leaned on; `auto` because
the M2 CLI pass made the thin `claude -p` shell (with `MANJU_ACTOR=ai` and
the SKILL.md playbook prepended) a few dozen lines. No architectural debt:
both sit strictly on the public CLI/core surface.

## 5. `board --serve`: an actionable localhost board (owner-directed, 2026-07-06)

Design §1-⑦ deliberately rejected a web UI ("static board covers ~80% of a
GUI's value at ~1/20th the cost"). The project owner explicitly requested an
actionable GUI / visual workspace for personal use. As built, the deviation
is bounded: the static board stays the default and byte-identical; `--serve`
starts a stdlib-only localhost server whose seven actions are a THIN veneer
over the exact core functions the CLI calls (select/redo/build/qc/package/
snapshot/rollback_shot), every mutation records the same event, and the
dangerous surface (unlock/gc/pack) is unreachable from the browser — the
same containment rule as the MCP server. No new dependencies, no auth
(127.0.0.1 binding; personal use per the owner), Range-guarded media
streaming with path-traversal rejection.

## 6. `manju auto` drives any one-shot agent CLI (2026-07-06)

v2.2 §10 described `auto` as a thin shell over `claude -p`. The owner asked
for the productization flow to be agent-neutral. As built: an agent-command
resolver (flag → MANJU_AGENT → project.yaml:agent → PATH probe over
claude/codex/gemini/qwen/aider) with a `{prompt}` template grammar; the
playbook prepend, actor=ai logging and exit-code propagation are unchanged.
Claude Code remains merely the first PATH-probe default. MCP stays the
recommended structured integration.

## 7. Preset kits reversed to three neutral frames (owner-directed, 2026-07-06)

Round O shipped 13 content-type kits (comic/short_drama/…) under the P3
"presets" goal. The owner reversed this: content-type inference belongs to
the agent reading the input, not to a preset — kits now are exactly `blank`,
`vertical_ai_video` (1080×1920@30) and `horizontal_ai_video` (1920×1080@24),
fixing only the frame and generic technical defaults (safe-area captions,
ducking, srt). The preset MACHINERY is unchanged; a neutrality guard test
bans genre tokens from kit data so flavor cannot creep back; `--preset
blank` is pinned byte-for-byte to the generic scaffold. This supersedes the
round-O kit list; the playbook (SKILL.md) carries the genre workflows
instead.

<!-- From the parallel R-line (merged in round R): -->

## 5b. The GUI ships now, as a pure client (§1-⑦ deferral revisited)

(Renumbered from a duplicate '## 5.' — short citations were ambiguous; nothing cited this entry by bare number.)

Design (§1-⑦) postponed a workbench GUI indefinitely: the static review board
covered ~80% of the value at ~1/20 of the cost. As built (user-directed
reversal of the *timing*, not the *architecture*): `manju gui` is a local web
workbench implemented as the **third client of the unchanged engine core** —
the same functions the CLI and MCP call, zero new dependencies (stdlib
`http.server`), truth still in text files, jobs in-memory only. The original
rationale for deferral was cost and the risk of a second source of truth; the
first is paid deliberately (the user asked), the second is structurally
avoided. The MCP dangerous-surface rule carries over verbatim: no `unlock`,
no `gc --hard`, no `pack`/`unpack`, no arbitrary-path `import` over HTTP (§5,
§11). The static board remains for offline/share use.

## 6b. ask_before becomes an engine gate, not just agent discipline (§8.3)

(Renumbered from a duplicate '## 6.' — same reason as 5b.)

Design (§8.3, §10) made the agent's discipline the first spending gate:
SKILL.md orders a stop-and-ask when a plan hits `ask_before`, and the engine
only enforced the budget ceiling (事中熔断). As built, the engine now also
enforces the first gate itself: a non-dry-run whose plan estimates cost > 0
while `expensive_generation` is in `ask_before` returns
`ok=false, waiting_user=true` unless the caller passed an explicit
`assume_yes` (`--yes` on the CLI, `assume_yes` on MCP/GUI). Uniform across
actors: the human's yes is one flag away; the agent's yes must still come
from the human (SKILL.md §5 unchanged — the engine just stops trusting that
discipline alone). Dry-run estimates are never gated, so "问前先 dry-run"
stays frictionless. Rationale: a discipline-only gate silently degrades as
agents and surfaces multiply; the cheapest honest place to stop money is the
single code path every surface already funnels through.

## 8. Round W (external 10-round review): meta-tier decisions

- **#83 project-supplied agent commands refused**: `project.yaml:agent` now
  accepts only KNOWN agent names (claude/codex/gemini/qwen/aider); free-form
  command templates remain available via `--agent`/`MANJU_AGENT` (machine
  trust). A downloaded project must not name an arbitrary local command.
  `local_cmd` providers were already machine-level (~/.manju/providers).
- **#84 dependency constraints**: constraints.txt pins the core runtime deps;
  CI installs with `-c constraints.txt`. Refresh deliberately (bump + full
  suite), not by drift.
- **#82 `_fail` JSON detection**: the frame-walk stays (call-site-free), now
  backed by an argv `--json` fallback so the structured-error contract cannot
  silently regress under refactoring.
- **#85 large-file refactor deferred**: cli.py / build/graph.py /
  media/render.py sizes are real maintenance debt; splitting them is
  deliberately NOT bundled into a correctness round (too much churn alongside
  85 behavioral fixes). Recorded here as standing debt for a dedicated round.

## 9. Round Y — closing the round-W accounting gap (external review)

An external review re-checked the round-W "85 issues" work and correctly found
that my round-W claim of "84 fixed / 1 design call / 0 not-real" was WRONG.
Reconciling the agent packages against the full 1–85 list, FIVE issues were
never assigned to any agent: #4, #12, #15, #19, #45. Of those, #19 (MCP
select_take) was incidentally fixed by WE's #39 unified checked write. The
other four were genuinely unfixed. Root cause: I tracked agent REPORTS instead
of reconciling coverage against the source list back-to-front. Fixed in round Y:

- **#4 media-ext determinism** — `MEDIA_EXTS` is now an ORDERED tuple (video >
  image > audio); `Project.takes()` picks by priority (deterministic across
  processes) and records a conflict on `TakeInfo.error` when a stem has >1
  media file, surfaced via staleness/check.
- **#12 board CSRF/token** — `board/server.py` now carries the same control
  plane as the GUI: per-run token (X-Manju-Token) on every mutating POST, Host
  allowlist (DNS-rebinding), Content-Type gate, body-size cap. The token is
  embedded into the served page's own JS; a foreign page cannot read it. Static
  board stays byte-identical (serve-only JS).
- **#15 MCP director hard human-gate** — `director.confirm` REFUSES an
  `actor != "human"` confirmation of any proposal containing a priced action
  (build/redo/voice); `execute` re-checks `confirmed_by == "human"` for paid
  proposals (catches a tampered proposal file). Free/local proposals stay
  AI-confirmable. This is now a code-level boundary, not a prompt-level hope.
- **#45 director fingerprint** — `state_fingerprint` now folds project.yaml
  (budget/mode) and timeline/routing.yaml (which provider, at what price), so a
  confirmed plan expires when the cost/routing picture moves. Provider manifests
  live in ~/.manju (machine config, not project truth) — out of the project
  fingerprint; the execute-time ask_before/spend gate is the remaining backstop.
- **#20 unpack hardening** — `manju unpack` validates every zip member before
  extraction: rejects symlink members and absolute/`..` names (stdlib
  extractall already neutralises name traversal, but not symlink-through-write).

### Standing design debt (documented, NOT claimed fixed)

- **#5 cross-process locking on GUI/MCP mutating handlers** — CLOSED in round Z
  (agent ZA audit): all ~76 GUI POST handlers, 24 MCP tools and 8 board actions
  classified (already-locked / pure-read / light-writer); 27 light-writers now
  acquire the fail-fast build_lock (11 at the shared `_gated_save` choke point,
  16 individually; board rollback/qc/package/export and MCP update_shot/export
  had zero coverage before). Lock order: in-process mutex outer → build_lock
  inner, everywhere. BuildLocked surfaces as the same 409 busy shape on all
  three servers. The two engine-level residuals flagged here were CLOSED in
  round AA (item 5): `sync_bible(apply=True)` now wraps each episode's whole
  write section in THAT episode's own build_lock (a busy episode stops the run
  fail-fast, earlier episodes stay synced, `stopped_at` names it) and
  `new_episode` locks only the series.yaml register step; `director execute`
  now carries an explicit per-action-type table at the dispatch — build/redo/
  voice/mixer lock internally (skipped, would self-deadlock), repair/captions/
  packaging/snapshot/rollback are wrapped individually at the dispatch site.
- **#6 rate_limit_per_min** — best-effort interval throttle in generic_cloud,
  NOT a precise cross-process token bucket. `max_concurrent` IS hard-enforced.
- **#7 budget is a soft limit** — accepted design; the trip message is honest
  ("不再提交新任务;进行中的 N 个镜头仍会完成并计费"). Not advertised as a hard cap.
- **#8 large-file refactor** (cli.py / build/graph.py / media/render.py) —
  maintenance debt for a dedicated refactor round, not a correctness bug.

## 10. Round AA — review states are stored, ownership is derived (2026-07-08)

Two data-model calls that shaped the whole round:

- **Ingest batch records** live in `reports/ingest_batches/<batch_id>.yaml`
  (the `reports/qc_agent.jsonl` precedent: human review decisions are project
  truth, git-tracked, human-readable). Only the REVIEW DECISIONS
  (confirm/flag/discard + notes) are stored state — everything else on a batch
  item (what landed where, match confidence, candidates) is written once at
  apply time from facts the engine derived. `discard` undoes an auto-staged
  selection ONLY if it is still the staged take; it never deletes media
  (append-only, §3).
- **Reference ownership is DERIVED, never registered.** `core/refs.py` computes
  the refs report from existing truth (the `{id}_ref` naming convention, bible
  ref fields, shot YAML ref params) on every call; `refs assign` makes a
  relationship real by RENAMING the file / setting the bible field — there is
  deliberately no refs.yaml index that could drift from the files.

Consistency/honesty calls: CAS (`expected_text_hash` on `checked_shot_write`)
is opt-in and applied ONLY where a client demonstrably holds stale rendered
state (GUI shot editor / storyboard cell / take note; MCP update_shot via
`expected_rev`) — fire-and-forget single-field actions and the single-process
CLI were deliberately skipped (spurious 409s, no staleness window). Interrupted
GUI jobs (found in `.manju/jobs.jsonl` on restart) are surfaced but NOT
retryable — `params_summary` is lossy by design, so a faithful resubmit is
impossible; the UI says so instead of pretending. Cross-process job control
(GUI seeing/canceling CLI/MCP runs) is documented out of scope in
`gui/jobs.py`. `manju evaluate` reports usage/rework/QC correlations ONLY and
renders its honesty section (what the data cannot claim) as part of the
output, not as fine print.


## 11. Interconnection & Trust (GUIDEINTERCONNECTION, 2026-07-09)

### 11a. CaptionLine.shot (WP1)
`CaptionLine` gains optional `shot: str = ""` (default empty = legacy).
Compiler stamps it on every generated cue. SRT/ASS exporters ignore it
(internal only). First recompile moves the timeline fingerprint once —
same byte-identity exception class as round-O transition overrides.
Cue↔shot resolution lives in `timeline/cuemap.py` (stamped preferred,
temporal window fallback).

### 11b. Timing-sidecar regenerability (WP3)
`<voice_take>.timing.json` is derived metadata, not a take. Re-running
`manju align` overwrites it (mirrors `.key.json` / webpreview stance).
Documented in `media/align.py` module docstring. Append-only still holds
for media takes themselves; MANUAL (sidecar-less) voice is never
invalidated by alignment.

### 11c. Locale overlay model (WP4)
A locale is `locales/<lang>/{lines,voices}.yaml`, never a project fork.
Locale text is excluded from `spec_payload` (picture pipeline shared);
only voice_payload consumers / caption compilation see the overlay.
`base_hash` tracks base dialogue.text; drift → 翻译过期 advisory only
(§4.3 / engine calls no LLM — no auto-translate).

### 11d. Roundtrip carrier scope (WP6)
v1 parses only our deterministic carriers: JianYing diff-stable skeleton
and OTIO. Native pyJianYingDraft / pycapcut / CapCut cloud drafts are
explicitly not parsed back (format drift risk). Export report / docs tell
users to edit the skeleton/OTIO copy for round-trip. Baseline written to
`exports/<kind>/.baseline/`.

## 12. Deep Research 01 — OpenClap adapter + derived-plan/run-evidence stance (2026-07-10)

External research (DR01, Timeline-first runtime & OpenClap format) was
audited against HEAD before any code: most of its candidate systems already
exist here, so the batch shipped one new boundary and two small proofs
instead of parallel infrastructure.

- **Timeline stays compiled truth, not a creation source.** The compiler
  remains the single place `selected_take` becomes a media path
  (`VideoClip.source`); renderer and every exporter consume the baked path
  (pinned by `tests/test_dr01_binding_pins.py`).
- **The existing provider `GenerationRequest` stays the only one.** It
  already carries spec hash, refs lineage, routing bias, cost and
  cancellation; a future acceptance contract must be a *compiled projection*
  of ShotSpec + technical rules (Deep Research 02 scope), never a second
  hand-filled truth.
- **`BuildResult.plan` / `--dry-run` / `explain` / `impact` are the plan
  surface — derived, never an input.** Six red tests (determinism,
  evidence-noise independence, targeted invalidation, explainability union,
  stable node identity, plan/build parity) passed unmodified at HEAD, so no
  schema/planner/plan-file was added (`tests/test_dr01_plan_projection.py`).
  One fix: dry-run `timeline_path` is now project-relative like the real
  build's (machine outputs never leak absolute paths).
- **OpenClap lives only at the adapter boundary**
  (`src/manju/exporters/openclap/`): raw-preserving parser (unknown fields,
  unknown enums and item order survive round-trip; `COMFUI` accepted and
  normalized in the typed view only), hard resource limits (compressed +
  streamed-decompressed byte caps, item/nesting caps, strict UTF-8), header
  counts *verified* fail-closed. `manju openclap export` is a deterministic
  snapshot (gzip mtime=0) gated by `ask_before=final_export` like every
  outward-facing export; `import-plan` writes nothing, downloads nothing,
  never auto-selects takes — the JianYing/OTIO `roundtrip` stance applied to
  a foreign format. `.clap` never becomes an internal source of truth;
  locators are never content identity.
- **Run evidence extends existing artifacts, no new store.** The final's
  `.key.json` sidecar now also records `run_id`, `output_sha256`, and the
  exact `inputs` breakdown that hashes to `final_key` (re-hashable, not a
  parallel account); the `events.jsonl` build record carries the same
  `run_id`. Content keys are byte-identical to before — the payload was
  extracted mechanically (`_final_key_payload`), guarded by
  `test_media_durability`.
- **Creative QC stays outside the engine.** brief→outline→script→Bible→shot
  list judgment belongs to Skill/Director (README boundary); the engine only
  runs deterministic preflight and artifact QC from the shot list on.
  Conditional candidates whose gap could not be proven by a failing test
  (core clip_id, resolved-asset view, binding digest, unified mock pack)
  were skipped with recorded evidence — see REPORTS/AI_IDE_01_BASELINE.md.

## 13. Deep Research 02 — bound acceptance evidence & derived assurance (2026-07-10)

External research (DR02, ARIS/aivideo-production-skills) audited against HEAD
first: the agent-review pipe, spec hashes, QC report, director suggestions and
both QC skills already existed, so the batch extends that pipe instead of
building a second QC/runtime. The one confirmed defect: verdict intake bound
evidence by hashing the CURRENT take file (`_resolve_take`), so a verdict
formed while viewing media A silently rebound to a same-name replacement B
(red test recorded; `register_take` never overwrites, so the race needs an
out-of-band replacement — but intake must still refuse the misbind).

- **Expectations are compiled promises, never inferences.** `qc/expectations.py`
  compiles ONLY explicit `quality.must_show` (present) / `quality.avoid`
  (absent) / `continuity.locks` (consistency) into
  `manju.qc.expectations/v1`; action/dialogue/prompts/model output are
  forbidden sources. Empty set = "nothing was promised", a valid state.
  Expectation ids are content-derived (reorder-stable); `source_path` keeps
  the authored index as provenance. Deterministic machine checks stay in
  `run_qc` — they gate acceptance separately, not duplicated as expectations.
- **Packets bind what the reviewer actually saw.** Briefs now issue
  content-addressed packets (`reports/qc_packets/<pkt_id>.json`,
  `manju.qc.packet/v2`) binding media sha256 + spec_hash + expectation digest
  (units: the member map). `pkt_id` re-hashes from content, so a forged or
  edited packet no longer matches its own id.
- **v2 intake: payload-invalid rejects, world-moved stores stale.** Missing/
  forged packet, subject mismatch, unknown expectation id, bad enums,
  oversized payload, unsafe evidence paths, secret-bearing payloads, or an
  echo contradicting the packet ⇒ whole batch rejected, zero writes. Media/
  spec/expectation drift since the packet ⇒ the record is stored as history
  with `binding: "stale"` + exact `binding_failures`, never current evidence.
  Stored binding fields always come from the PACKET (the bytes reviewed) —
  intake re-hashes the current file only to compare, never to stamp. v2
  records live in the same `reports/qc_agent.jsonl`, schema-tagged; the
  legacy reader skips them, legacy lines stay byte-identical, legacy verdicts
  never satisfy v2 acceptance.
- **Assurance is a pure derived state, separate from done and from human
  review.** `qc/assurance.py` computes PASS/FAIL/UNKNOWN per expectation from
  a fixed truth table (a model never writes the result) and derives
  not_reviewable / no_explicit_expectations / unreviewed / legacy_reviewed /
  stale / rejected / unknown / accepted — file-only (works with `.manju/`
  deleted), reading `ShotStatus.review_state` without ever writing it.
  `accepted` additionally requires no shot-scoped error in deterministic QC.
- **Repair proposals are advice, never actions.** Rejected/unknown shots get a
  read-only proposal citing exact expectation ids
  (`do_not_execute_automatically: true`); surfaces show it, humans/agents
  turn it into `manju redo`/patches explicitly.

## 14. Deep Research 03A — external ShotDraftPackage, controlled import (2026-07-10)

External research (03A, PenShot) audited first: no existing surface imports a
structured multi-shot creative package (`create` scaffolds, `director`
whitelists actions, `ingest` moves media, `roundtrip` edits the timeline,
`mentions` registers refs) — so one boundary was added, reusing the existing
controlled-write machinery end to end.

- **A package is a proposal, never truth.** `manju shot-package FILE` is a
  zero-write inspect producing `manju.shot-import-plan/v1` (precise diff,
  omitted suggestions, unresolved refs, warnings); only `--apply` writes,
  under `build_lock`, via `Project.save_shot` + index append, with CAS
  anchored to the reviewed plan, proposed-text drift guards, compensating
  rollback, and a post-apply `check` that rolls the batch back on any NEW
  error.
- **Soft suggestions never promote.** scene/characters/action/camera/duration
  map into ordinary editable shot fields (duration frame-snaps at build — a
  warning, never a lock); visual/negative prompts, style_tags,
  continuity_notes, confidence and budgets are ALWAYS omitted and listed in
  `omitted_suggestions` with reasons. `must_show`/`avoid`/`continuity.locks`
  are authored by humans, not imported.
- **Create-only.** An op targeting an existing shot id is a conflict pointing
  at `manju propose`; fragment/draft ids never mint shot identity (empty
  proposed ids get the next free S###).
- **Idempotency is enforced, not advisory.** A package's semantic digest
  (excluding created_at/package_id, key-order independent) is recorded on the
  apply event; re-applying the same digest is refused as a first-class cause
  — without this, empty-proposed-id packages would silently duplicate shots.
- **Not exposed over MCP** — mirroring the existing decision for the
  roundtrip/ingest command class (none are MCP tools today); CLI and any
  future MCP path share the same two service functions.

## 15. Deep Research 03B — graph diagnostics stay a derived view (2026-07-10)

External research (03B, Forge Film) audited first: Manju has NO explicit
edge-based dependency graph (grep-zero for depends_on/predecessor/topological/
DAG) — its "graph" is the phased pipeline with deliberate per-shot isolation.
The batch therefore ships diagnostics, not a scheduler.

- **A pure core with the strict truth table** (`build/graphdiag.py`): only
  SUCCEEDED or a key-verified cache hit satisfies a required edge; FAILED/
  CANCELED/WAITING/UNKNOWN never unlock successors; cycles get deterministic
  minimal paths; multi-parent nodes must declare `handoff_from` OR an explicit
  `merge_policy` — `predecessors[0]` is never assumed (the Forge-Film bugs are
  rejected by construction). Pure: no DB/clock/randomness/mutation/dict-order.
- **`derive_build_graph` is a read-only projection** of the actually-modeled
  dependencies (every edge cites the code that models it; gen→compile REQ,
  voice→compile OPT per §4.3 advisory voices, compile→captions/render/export
  per the phase order; exports depend on compile, not render — the code says
  so). No inter-shot edges; nothing inferred from scenes/characters/prompts
  (pinned by test). Manju's compile node declares `merge_policy: index_order` —
  shots/index.yaml IS the assembly contract, so no permanent handoff noise.
- **Failure propagation was already correct-by-design** (characterized, not
  fixed): a final-target build with an unresolved shot refuses at compile
  (`CompileError` → ok=False, no render); audition slates are the explicit
  optional-edge analog. No execution code changed.
- **SchedulingHints: NOT_IMPLEMENTED** (contract default REJECTED_UNLESS_
  BENCHMARKED — no stable benchmark harness; diagnostics emit no scheduling
  directives and never become an execution input).
- Surface: `manju explain --graph [--json]` (+ MCP explain parity) — the
  diagnostics doc rides the existing command; no new command family.

## 16. Deep Research 03C — one attempt stream, derived RunManifest (2026-07-10)

External research (03C, ClipForge) audited first: run evidence was split
across four surfaces (SQLite runs ledger, failures.jsonl, take sidecars,
coarse events) with no attempt identity, no parentage, no per-attempt
digests. The batch converges them on ONE stream instead of adding a ledger.

- **events.jsonl is the single attempt stream** (variant A). One terminal
  `stage_attempt` event per attempt (`manju.stage-attempt-evidence/v1`,
  11 states — never a `done` bool), appended under a flock because attempt
  payloads exceed the small-write atomicity that protects legacy events.
  SQLite gains only an additive `runs.attempt_id` cross-ref and stays
  sidecar-rebuildable; attempt history is never lost with it.
- **Emit-around only.** The registry's fallback chain emits one attempt per
  provider try with A/B/C parentage (fallback_root + parent); failed
  attempts are never overwritten. The attempt id is pre-minted and stamped
  on the request so cloud providers' self-recorded ledger rows carry the
  SAME id (tasks --json parity on the expensive path). With no evidence
  context, behavior is byte-identical (proven by a toggle-off red test).
- **SUCCEEDED only after the existing commit points** (media atomically on
  disk + hashed); evidence-append failure warns and never deletes media.
  Cache hits bind take identity without re-hashing media; the content-key
  binding rides the render attempt where hashing is already paid.
- **RunManifest is a derived report** (`reports/runs/<run_id>/run.json`):
  projects the run's attempts (command/target/mode from the run terminal's
  run_context), never guesses success from file existence, is atomic,
  deletable, re-materializable (`manju tasks manifest <run_id>`), and is
  never read by build/resume/cache/rebuild-index (pinned).
- **Systematic redaction**: SECRET_PATTERNS scan + explicit policy (auth/
  api-key params dropped, signed-URL queries stripped, prompts by digest).
- Deferred stages recorded honestly: voice/export/repair/package attempts
  and per-segment render evidence are later wirings, not silent gaps.

## 17. Deep Research 04 — one provider-fact source, spend-free preflight (2026-07-10)

External research (04, LumenX) audited first: provider facts lived in
routing's private `_catalog()`, the duration cap was enforced only inside
generic_cloud's submit, and `max_resolution` was a declared-but-never-read
opaque string. The batch extracts one source instead of adding a catalog.

- **providers/catalog.py is the single provider-fact source** (built-ins +
  manifests; broken manifests → errors without hiding healthy providers).
  Routing's `_catalog()`, the CLI (`providers catalog`/`check`) and
  preflight all consume the same descriptors; routing behavior is
  byte-identical (pinned). The projection
  (manju.provider-capability-projection/v1) is derived, deterministic, and
  excludes paths/mtime/credential values AND credential presence;
  `provider_profile_digest(provider_id, capability)` is the stable identity
  downstream batches derive submission identity from.
- **providers/preflight.py: spend-free request compatibility**
  (manju.request-compatibility/v1) validating ONLY explicit executable
  facts — provider exists/enabled, capability, duration (the SAME rule
  generic_cloud enforces at submit, now shared), refs counts via the ONE
  existing budget allocator (preflight and submit see identical effective
  refs), first/last frame, body-placeholder completeness. Never infers
  from story/prompt/shot language.
- **max_resolution is never guessed**: surfaced verbatim with a
  LEGACY_UNINTERPRETABLE_LIMIT warning. Structured resolution limits,
  capability_profiles overlays and param type/enum/range contracts are
  SKIPPED_WITH_EVIDENCE (grep-zero readers; no real manifest with
  conflicting per-capability limits) — no manifest fields were added
  without a failing fixture.
- **Deliberate fail-earlier changes** (each visible as a skip reason):
  cheapest/rule/tier candidates skip duration-incompatible providers;
  an EXPLICIT provider pin that cannot satisfy the request fails BEFORE
  submit instead of silently degrading. The §8.4 no-routing fallback chain
  order is deliberately unchanged (safety-net semantics; the submit guard
  defends it).

## 18. Deep Research 06 — persistent submission identity, idempotent admission, ambiguous-outcome recovery (2026-07-10)

The paid-submission safety batch. Core principle: **an ambiguous submit
outcome must never be automatically treated as not-happened** — we make the
ambiguity durable and recoverable, and never claim exactly-once. Cache hits,
local/offline providers and cleanly-succeeding cloud flows are byte-identical
(characterization-pinned); the only behavior changes are documented
fail-earlier/fail-closed ones.

- **Evidence rides 03C's stream, no new ledger.** Submission lifecycle
  transitions are `submission_state` events in the SAME flock-locked
  events.jsonl (schema `manju.provider-submission-event/v1`), each carrying a
  per-submission hash chain (`prev_event_digest`) for corruption detection.
  `build/attempts.py` gained the locked writer/reader; no second lock, no
  second file.
- **`providers/submission.py` is a pure module**: identity + `request_digest`
  (§7, reusing DR04's `provider_profile_digest`), submission_id mint
  (`sub_`+uuid12, minted+persisted BEFORE any network), the state machine
  (9 states + legal transitions), disposition classification, the event-chain
  digest/verify, idempotency-key derivation, redaction. No persistence.
- **Intents table extended additively** (submission_id UNIQUE / request_digest /
  state / updated_ts — the #47 migration precedent). The DISPATCHING claim is a
  SQLite-CAS `UPDATE ... WHERE submission_id=? AND state='PREPARED'` with a
  rowcount==1 check (multi-process safe, not an in-memory lock). rebuild()
  restores unresolved submissions from the event stream — SQLite stays a
  rebuildable projection.
- **§8.4 write order** in CloudProvider.generate (wraps the existing flow):
  cache hit → untouched; PREPARED (row+event) → atomic DISPATCHING claim
  (+event) → submit → classify. **Evidence ADMITTED lands BEFORE the SQLite
  job projection** (a crash between recovers from evidence). If PREPARED or the
  claim cannot be persisted, the paid submit does NOT start (fail-closed).
- **Conservative disposition** (generic_cloud): provably-not-sent =
  DNS/connection-refused ONLY (→ NOT_DISPATCHED, retry allowed); tested 4xx
  (400/401/403/404/409/422/429) = DEFINITELY_REJECTED (fallback allowed);
  EVERYTHING else after the send boundary — timeout, reset, EOF, 5xx,
  unparseable 2xx receipt — = OUTCOME_UNKNOWN. On OUTCOME_UNKNOWN there is NO
  local retry, NO fallback (the registry stops the chain), NO resubmit.
  FailureKind is untouched; disposition is an orthogonal additive attr on
  ProviderFailure (default None = legacy/conservative, so non-classifying
  providers keep pre-DR06 retry-by-kind behavior).
- **Strict correlation** (ruling 8): a fresh submit consults unresolved
  submissions for the shot+provider — digest match on an ADMITTED one resumes
  polling under the SAME submission_id; a DISPATCHING/OUTCOME_UNKNOWN one fails
  closed with `{code: submission_outcome_unknown, automatic_resubmit: false,
  possible_remote_side_effect: true, actions: [attach_remote_job,
  abandon_with_duplicate_risk]}`; a digest-mismatched ADMITTED one is a
  conflict (never a silent poll). **No TTL, ever** for unresolved states.
- **Recovery is honest**: `manju tasks attach-remote-job` (appends ADMITTED
  evidence, poll-only, never claims verified ownership) and `manju tasks
  abandon` (ABANDONED_BY_USER with explicit duplicate-risk acceptance, history
  preserved). NO "retry unknown" anywhere. A new submission_id is only minted by
  the next explicit redo/build.
- **Minimal declared-only idempotency** (ruling 10): additive manifest
  `submission.idempotency.{mode: header, field: <Name>}`; generic_cloud injects
  the derived key ONLY when declared; a declared provider MAY re-dispatch the
  SAME submission_id on recovery (remote dedupes). **SKIPPED_WITH_EVIDENCE**:
  SpendAuthorizationRef (gate results referenced inline on the PREPARED event),
  the reconcile/status-by-key hook (manifests model no query-by-key endpoint —
  attach/abandon is the honest recovery), and the multi-process keyed-flight
  optimization (SQLite CAS + build_lock are the guarantee).
- **Documented behavior changes** (each pinned-then-flipped in the
  characterization file): a submit-phase timeout/5xx/unparseable-receipt is now
  OUTCOME_UNKNOWN (was retried/fallback-eligible); a broken/absent runtime DB
  now fail-closes a cloud paid submit (was: proceeded on best-effort state, §3);
  the registry stops the fallback chain on OUTCOME_UNKNOWN.

## 19. Deep Research 05 — declared tool policy, one agent surface, opt-in unattended profile (2026-07-10)

The MCP-collaboration governance batch. Core principle: **the collaborative
surface changes by not one byte; "unattended" is a stricter opt-in projection
of the SAME 26 tools, never a second server or a second allowlist.** Policy
metadata DESCRIBES the engine's real protections (§7.4) — it is honest
paperwork, not a security boundary; enforcement stays where it always was
(the tools/call dispatch gate + the engine's own CAS/checked-write/build-lock/
ask_before guards).

- **`mcp/policy.py` is the one vocabulary + one resolver.** ToolPolicy fields:
  `effects` (READ/READ_RUNTIME/WRITE_TRUTH/WRITE_PROPOSAL/WRITE_DERIVED/
  NETWORK/SPEND), `network`/`spend` levels, `gate`, `concurrency`,
  `unattended` rule, logical `writes` prefixes. `validate_policy` runs at
  import (load-time gate: unknown values, absolute/traversal write paths,
  effect/level mismatches all refuse to load). `resolve_agent_surface(defs,
  profile)` is the SINGLE resolver consumed by tools/list, the tools/call
  gate and the `agent_surface` manifest — no parallel allowlist to drift.
- **Two deliberate vocabulary deviations from the contract** (recorded, not
  hidden): network/spend levels are two-valued `NEVER|POSSIBLE` (the
  contract's `CONDITIONAL|ALWAYS` split encodes runtime knowledge — regen
  flags, cache state — that a static declaration cannot honestly claim), and
  `gate` is a LIST (real tools stack guards: update_shot = CAS+checked-write+
  build-lock; a single-valued field would force a lie by omission).
- **Profiles**: `collaborative` (default) admits all 26 tools — byte-identical
  surface, pinned by characterization. `unattended` (opt-in via `manju
  serve-mcp --agent-profile unattended`, operator flag ONLY — never project
  content, never a tool argument) hides exactly `redo` + `director_confirm`
  from tools/list and refuses their calls with a structured, machine-readable
  denial `{code: agent_profile_denied, tool, profile, required_path}` that
  names the collaborative path back. director_propose/director_execute stay
  admitted: the confirmed-proposal chain IS the unattended spend path.
- **`agent_surface` tool (the 26th)**: returns AgentSurfaceManifestV1 — every
  tool with its full policy block, `listed` true/false (denied tools stay IN
  the manifest with the denial reason — agents can see what exists and why
  it's closed), plus a stable `digest` over the normalized surface so a
  policy/profile change is detectable. `raw_filesystem_enforced: false` is
  stated honestly: MCP-level policy cannot stop an agent's OWN filesystem
  access; that boundary belongs to the agent harness.
- **Review fix (Fable)**: `redo`'s declared gate list omitted `SPEND_GATE`
  even though redo_shot runs the same §8.3 ask_before spend gate as build —
  metadata under-described a real guard. Fixed + a new invariant test pins
  it structurally: every `spend: POSSIBLE` tool must declare `SPEND_GATE`.
- **What did NOT happen** (§2 no-parallel-systems): no second server, no
  per-project policy file, no runtime capability negotiation, no policy
  enforcement inside engine functions (they keep their own guards), no
  hiding of read-only tools in unattended mode.

## 20. P0 remediation 01–06 — paid-path evidence is a precondition, not telemetry (2026-07-11)

The cross-batch hardening pass (AI_IDE_01_06_P0_REMEDIATION). Core principle:
**before money moves, evidence is a precondition; after money moved, evidence
is telemetry.** The same events.jsonl, the same single flock — but two honest
policies instead of one optimistic one. Ten claimed gaps audited claim-by-claim
(all ten CONFIRMED by source + failure injection with transport call counts —
none was already handled); every fix red-first. Full evidence:
REPORTS/AI_IDE_01_06_P0_REMEDIATION_{BASELINE,COMPLETION}.md.

- **One append coordinator, two policies** (core/events.py, the layering-true
  home): REQUIRED_BEFORE_SIDE_EFFECT for PREPARED/DISPATCHING (incl. the
  redispatch re-entry — a review find: the retry's DISPATCHING event was still
  best-effort) raises EvidenceWriteError and the paid submit never starts;
  BEST_EFFORT for everything post-spend keeps media and warns. A lock timeout
  or a no-fcntl platform now DROPS a best-effort record and REFUSES a paid one
  — the old "never lose a record: write unlocked" fallthrough is gone (a torn
  line in the evidence stream is worse than a dropped telemetry line).
- **Disposition is phase-aware** at the one submit choke point: inside submit,
  an unclassified ProviderFailure is OUTCOME_UNKNOWN (the DR06 "legacy
  retry-by-kind for non-classifying adapters" default was a double-charge
  vector — retried an unclassified timeout into 4 transport submits);
  poll/download keep kind-based retry and can never re-enter submit. The
  global tested-4xx/429 ⇒ DEFINITELY_REJECTED table is DELETED: definite
  rejection is now a PER-PROVIDER DECLARED fact
  (submit.definite_rejection_statuses, additive) — a gateway 4xx after the
  backend accepted is real, so an undeclared post-send error is UNKNOWN.
- **The consult never degrades to "fresh"**: identity/state-query/evidence-
  read/chain-verify failures raise structured submission_recovery_unavailable
  (automatic_resubmit false, possible_remote_side_effect true) instead of the
  old silent skip that fresh-submitted past in-flight ADMITTED/DISPATCHING
  rows.
- **Chain rebuild is longest-valid-prefix, never last-event-wins**
  (submission.project_chain): a corrupt/torn tail can no longer forge a
  resolution and unblock a resubmit; a chain with no valid prefix restores the
  new RECOVERY_EVIDENCE_CORRUPT sentinel — side-effect-ambiguous by
  construction, resolved ONLY by tasks attach-remote-job / abandon, never by
  redispatch (corrupt evidence cannot prove the prior idempotency key).
- **Runs and expensive attempts have real lifecycles** (manju.run-lifecycle/v1
  on the SAME stream, best-effort): run_started/run_terminal on every
  run_build exit, attempt_started before provider generation and the final
  render on the SAME attempt_id their existing terminals ride. The RunManifest
  stops lying: INCOMPLETE + dangling_attempts for interrupted runs, NOT_FOUND
  for unknown ids, legacy streams stamped legacy_terminal_only. The DR03C
  claim "terminal-only attempt evidence is loss-proof" is formally RETRACTED.
- **Assurance fails closed** (qc/): deterministic QC is tri-state — 
  UNAVAILABLE derives `unknown` with a visible qc block, never accepted;
  expectation compile errors surface instead of reading as "nothing promised";
  a duplicate expectation_id rejects the whole verdict batch with zero writes.
- **The 4 include_unindexed baseline failures are fixed tests-only** — three
  stale doubles now mirror run_build's real signature; one stale assertion
  (payload["plan"]) moved to the CLI's actual "rows" envelope
  (git-history-verified test drift). Suite target: 0 failed.
- **What did NOT happen**: no second ledger/lock/stream/table (the sentinel is
  a STATE, not a store); no TTL on unresolved paid states; no auto-resubmit
  anywhere; no msvcrt lock (Windows paid appends refuse instead — the
  contract's explicit alternative); no wrapping of attempt families that have
  no terminal events to correlate (direct TTS, audition/locale renders —
  recorded openly).

## 21. 07C — the release baseline is evidence about exact bytes, never a rebuildable pointer (2026-07-11)

The approved-baseline batch (AI_IDE_07C). Core principle: **what a human
approved is a specific file's bytes — if those bytes or their evidence are
gone, the baseline is DAMAGED/CORRUPT and stays that way until a human
approves again; nothing ever re-derives an "equivalent" approval.**

- **No new store**: the baseline is a `release_baseline_approved` event kind
  inside the existing reports/verifications.jsonl human-verification envelope,
  binding {path, sha256, bytes, final_key} + source_revision/run_id/assurance
  digest, with an append-only supersedes_event_id chain. Durable append
  (flush+fsync, partial-line rollback) gates the success report.
- **Human-only**: unattended/agent callers are refused at the engine; there is
  deliberately NO MCP tool for approval (CLI is the surface); a planted
  "approved" flag in project source changes nothing (tested).
- **Approval runs the assessment's own technical gate** — blockers refuse by
  default; `--accept-known-risk` (human CLI) approves anyway but records the
  accepted blocker codes + reason INTO the event (auditable risk acceptance).
- **One compare engine**: `compare --against-baseline` calls the same
  compare_finals service; a sha difference is CHANGED_REQUIRES_REVIEW — a
  human judgment prompt, never an auto-verdict of better/worse.
- **exports composes, subsystems own**: the additive `release_assessment` in
  exports' deliverables_data is a pure combination of existing services —
  exports rows, baseline resolver, compare, QC assurance tri-state, P0
  RunManifest terminal honesty, unresolved-submission/chain state — with the
  16 contract blocker codes mapping 1:1 onto those signals. NO_BASELINE on a
  virgin project is not a blocker (first release stays possible).
  next_actions safety metadata is READ from the DR05 ToolPolicy registry,
  never a hand-written allowlist; nothing auto-executes.
- Budget: 3 production files (cap 6), 0 new public schemas, 30 red-first
  tests; zero existing pins flipped (the CLI/GUI byte-agreement pins held
  because the assessment is composed inside the shared payload builder).

## 22. 08_10_12C — the production loop runs through existing sources, not new nouns (2026-07-11)

The accepted-take production-loop batch (AI_IDE_08_10_12C, replacing the
08R/10R/12R object families). Core principle: **every creative decision that
can change a paid request must first become a diff on an existing text
source; everything else is a deletable derived view.** The seven planned
schemas (DirectingBrief, ClipContract, CandidateSet, RecipeSnapshot,
AcceptedObservedState, ContinuityDiff, ContinuationCapsule) all resolved to
existing owners — 0 new public schemas (Path A held: verdict v2 carries the
production decision additively).

- **Source authority is now pinned by tests**: mutating a derived directing
  report moves NO prompt/request digest; applying the equivalent proposal
  moves spec_hash + prompt digest; locked sources still demand proposals.
- **Prompt workbench stays the one projection**: `manju prompt --json` gained
  additive production_checks (clip-scope, reference-transfer, surface
  freshness, budget, capability-via-preflight — pure functions naming source
  paths, auto_apply always false) and a compiler_trace (source revision,
  bundle digest, surface-profile digest/freshness, continuation_source).
  Surface profiles are ONE conservative source-dated internal data family +
  an UNKNOWN fallback — never a capability authority (DR04 owns that).
- **Ref transfer rides the existing binding**: dict-form refs entries with a
  closed controls/ignore vocabulary + subject_ref; conflicts and unknown
  enums are recorded and refused, legacy string refs byte-identical (flagged
  advisory REFERENCE_TRANSFER_UNDECLARED). Authored in params ⇒ in spec_hash.
- **Candidate families are a derived join** over sidecars + attempt/
  submission evidence — same request_digest groups; an explicit
  `redo --from-take X` now stamps additive TakeSidecar.redo_of (red-proven
  the only durable lineage carrier since redo emits no attempt evidence
  outside a run). keeper (QC evidence) / selected_take (shot source) /
  promoted reference (refs source) remain three separate states.
- **Bound review**: verdict v2 optionally carries decision{disposition
  KEEP|FIX_IN_POST|EDIT_DONT_REGENERATE|REROLL|REWRITE_SOURCE,
  primary_repair_variable (17-value closed set, mandatory for non-KEEP),
  diagnostic_isolation, reason} + observed_states[] (START/END transient
  observations, 5-value visibility, bounded confidence) — validated in the
  DR02 zero-write intake; reviewers still cannot write assurance; observed
  transients never become Bible facts.
- **Continuation gate**: next-shot continuation requires the accepted media's
  verified hash + a bound endpoint observation; the observed ending beats the
  prompt's predicted ending; the output is only ever a next-shot source
  proposal, recompiled by the same workbench.
- **Three first-party skills** (direct-shot-source-patch,
  review-take-and-route-repair, continue-from-accepted-take) with
  source-dated data packs; eval tests pin their vocabularies to the code's
  enums so drift fails loudly.
- Budget: 8 of 10 production files; board additions SKIPPED_WITH_EVIDENCE
  (a comparison entry already exists); zero pins flipped.

## 23. 13C — the delivery manifest states facts; sources decide cuts (2026-07-11)

The delivery-boundary batch (AI_IDE_13C, replacing seven planned objects with
ONE schema). Core principle: **creative editing decisions belong to source;
manju.delivery-manifest/v1 only states what was already generated, hashed and
verified — deleting it changes nothing.**

- **One schema, pure derivation** (build/delivery.py): composed from
  exportstatus rows (the one status owner), 07C's release_assessment
  (verbatim), the compiled timeline, locale_status, key sidecars. Optional
  atomic materialization; delete/hand-edit inert; rebuild-identical (no
  wall-clock field exists anywhere in the manifest).
- **Variant boundaries are verifiable**: FORMAT_ONLY is proven by a
  segment-only timeline semantic digest (VARIANT_KIND_MISMATCH blocks the
  label); EDITORIAL_CUTDOWN requires an explicit adopted source revision
  (never auto-cut); LOCALIZED binds the locale base hash (stale ⇒ block);
  PLATFORM_PACKAGE cannot silently change the narrative cut. Framing is
  recorded declared-only — the renderer has no per-profile crop today, so
  execution is SKIPPED_WITH_EVIDENCE, not faked.
- **NLE handoff is exact-bytes**: per-clip asset_sha256 + integer frame
  in/out + timebase read from the compiled timeline through the source
  resolver — no directory scanning, no take guessing; human
  opened-in-target-app verification stays separate from adapter roundtrip.
- **Platform handoff is credential-free by construction**: the manifest's
  credentials_present is a real scan (core.check SECRET_PATTERNS + the
  submission signed-URL detector) and a detected token REFUSES the manifest;
  upload_supported is always false — publishing is not owned by this batch.
- **The bundle is reproducible and safe**: only manifest-registered files +
  SHA256SUMS; `..`/absolute/symlink/duplicate entries rejected; atomic
  replace keeps the old bundle on failure; fixed 1980 zip timestamps make
  the archive byte-deterministic. Deliberately distinct from `manju pack`.
- **Honest rejection**: pack's inline zip guards were NOT lifted into a
  shared helper (frozen surface + budget); delivery re-enforces stricter
  guards locally and the completion report says so.
- Budget: 2 of 8 production files, exactly 1 new public schema, 35
  red-first tests, old profiles derive MASTER (compat pinned).

## 24. 09_11G — the discipline held: nothing to build, and we can prove it (2026-07-11)

The anti-overbuild gate batch (AI_IDE_09_11G, replacing 09R/11R). Core
principle: **for a single-operator local workspace, resume and single-host
safety are already owned — by status/tasks/exports/agent_surface on the read
side and by build-lock/06-admission/limiter on the write side; the deliverable
is PROOF, not features.** Outcome: Path A — SKIPPED_WITH_EVIDENCE, 0
production files, 0 new schemas, 26 characterization tests
(tests/test_c0911_gates.py) as the standing evidence.

- **Resume**: a real interrupted-session transcript (killed run, dead-pid
  lock holder, unknown paid submission) is fully recoverable read-only; the
  §4 trigger chain failed decisively (no missing safety information, next
  safe action decided, every mis-action engine-blocked regardless). No
  status resume section, no ResumeCapsule, no SkillLock (REJECTED — the
  surface digest covers the TOOL surface by design; skill files are sources
  under git like everything else).
- **Single host**: two real OS processes cannot duplicate a paid submit
  (SQLite CAS admits exactly one) nor dual-own the build lock; the limiter
  holds its manifest cap without leaks and cache hits never consume vendor
  permits; classifications survive a full .manju wipe; cancel stays honest —
  and REMOTE_CANCEL_CONFIRMED is REJECTED_WITH_REASON because no provider
  cancel API exists to confirm through (recording a confirmation state we
  cannot obtain would be a lie).
- **Lease/fencing REJECTED_WITH_REASON**: no reproducible dual ownership
  exists to justify one.

## 25. Post-completion hardening — an external review, accepted and repaid in full (2026-07-11)

An external AI review (POST_COMPLETION_HARDENING_V1) of the completed
01–13 work claimed sixteen gaps. Verdict after source-verification: the
review was RIGHT — fifteen of sixteen reproduced red (the sixteenth split
into honest halves). Its two structural insights are worth recording:

1. **Durable evidence is not enough if consumers trust the projection.**
   P0 made paid-submission evidence durable, but the resume consult and the
   07C release gate still read the DISPOSABLE SQLite projection — so a
   deleted/fresh `.manju` read as "nothing in flight" while events.jsonl
   held an unresolved chain. Fix: `ensure_submission_projection` — the one
   helper both consumers now call, folding evidence into the projection
   (same project_chain machinery) before anything is trusted, fail-closed
   on any read failure. "Empty" and "verified empty" are different facts.
2. **A derived artifact can become an input through its own convenience
   fallback.** 13C's manifest was derived-only by contract, but its base-
   identity resolver quietly re-read the materialized master manifest from
   disk — hand-editing a report drove the format-only invariant. Fix: the
   disk-read is deleted; base identity is explicit or re-derived in-process
   under a cycle guard. The same review also caught the semantic digest
   covering only video (audio/subtitle changes now break FORMAT_ONLY), the
   fail-open unreadable baseline log, the unverified baseline event_id, the
   run-gate matrix gap, keeper currency, the NLE duplicate-entry crash on
   the NORMAL path, missing bundle CAS, metadata-outside-the-digest
   (13C's deliberate exclusion, now REVERSED on the review's better
   argument), silent-MASTER fallbacks, and the accepted-but-ignored
   final_ref (removed, Option B).
- Strict identity (cloud submits refuse to proceed on prompt/ref/hash
  failures — NOT_DISPATCHED, transport 0) and honest legacy correlation
  (digest match ⇒ poll-only; else human attach/abandon — a deliberate,
  pinned DR06-compat flip) close WP3.
- The 09_11G report language was corrected to claim exactly what its tests
  prove (per-process cap; same-submission-id CAS; explicit-vs-automatic
  rebuild) and two stronger gates were added (two full subprocess generates
  ⇒ one transport submit; delete-`.manju` auto-guard).
- Budget: 8 of 10 production files, 0 new schemas, 41+ new red-first tests,
  5 pins flipped with rationale. Two honest SKIPPED_WITH_EVIDENCE sub-items
  (no-run_id sidecar marker; release-side final_ref already correct).

## 26. Final acceptance — the red CI was telling the truth (2026-07-11)

The final-acceptance contract (FINAL_ACCEPTANCE, external review #2) closed
four P0s and four P1s. Verdict: agreed in full — and its CI claim found the
best bug of the whole program.

- **The BuildLock dual-ownership race was real** (F4/D). GitHub CI had been
  red for four commits with `['ACQUIRED', 'ACQUIRED']` from the G11-5
  two-process probe — not a flake: `_create()` opened O_EXCL then wrote the
  holder JSON, and `_is_stale()` treated an empty lock as stale OUTRIGHT, so
  a sibling landing in the open→write window stole the winner's lock. Our
  fast local container never lost that race; CI's loaded runners were the
  fault injection we lacked — 09_11G's "no reproducible dual ownership" was
  an environment-limited claim. Fix (still no lease/fencing — the remedy
  hierarchy held): write-then-hardlink creation (the lock is never visible
  empty) + corrupt-needs-age staleness (10s grace) + the race test now
  asserts the true invariant (hold intervals never overlap; sequential
  re-acquire under runner starvation is legal). Deterministic red tests b16/
  b17 pin the window shut. Lesson recorded: single-host claims proven only
  in one environment are claims about that environment.
- **F1**: a torn line in the submission evidence stream now taints every
  paid consult AND the release gate (stream-global: a torn line has no
  parseable submission_id, so no scope can be exonerated); previously the
  malformed count from read_submission_events was silently discarded at
  every consumer.
- **F2**: RUN_NOT_PROVEN — a final without run_id in its key sidecar cannot
  be ready (reverses hardening's SKIPPED sub-item: absence IS the signal, no
  schema marker was ever needed); the only escape is the existing human
  --accept-known-risk, exact-SHA-bound.
- **F3**: CURRENT_FINAL_UNVERIFIABLE — missing output_sha256 / unreadable
  bytes / a None candidate hash all block readiness.
- **F5**: a torn verification-log line blocks release even beside a VALID
  baseline event (the torn line could BE the superseding approval); the
  baseline path-escape fallback is deleted (unresolvable ⇒ DAMAGED, never
  root-relative guessing); an unverifiable NLE project file blocks
  (directory drafts keep per-asset hashes — no single-file identity exists);
  write_bundle is stream-hash-once, so SHA256SUMS, the embedded manifest and
  the ZIP provably consume ONE byte snapshot (the validate→re-read pipeline
  had a second TOCTOU window).
- Acceptance was verified against PUBLIC CI, per the contract's own rule
  that local 0-failed reports do not substitute: run 29151178302 at HEAD
  6d141d9 — 2803 passed / 0 failed (CI also runs the 12 locally env-skipped
  tests) + the M0 build-twice smoke. FINAL_ACCEPTANCE_PASSED.

## 27. AI_IDE 14–21G — the capability program: qualification, perception, ladder, sound, series, semantics, gates (2026-07-11)

Eight contracts executed in the operator's order (14 → 20A → 15 → 16 → 18 →
17 → 19 → 20B → 21G), pivoting from safety hardening to production
capability. Core principle held throughout: **every new capability is either
derived evidence over existing owners or an additive source field — and
anything a model reports remains an observation that only a pure function or
a human can turn into acceptance.**

- **14**: the qualification ladder (UNTESTED→PRODUCTION_READY, STALE/BLOCKED
  overlays) as a pure derivation bound to profile/adapter/fixture/request
  digests; cost-bounded canaries through the standard admission path in a
  disposable canary project; PRODUCTION_READY requires transport=="real"
  evidence — a scripted canary can prove the machinery but never production
  readiness. This environment has no real account: real providers honestly
  cap at DRY_RUN_VALID.
- **20A/20B**: the golden corpus (77 synthetic cases, ~145 KB), the fake
  reviewer + disagreeing twin loading through the real registry, the
  deterministic bad-media generator (probe-fact validated — ffmpeg bytes are
  not cross-version stable), calibration rates with known-value denominators
  proving the HARNESS (subjects are the fakes; asserted in-test), provider
  regression cards with a tested no-aggregate-score walker, and a
  cannot-rot evidence index (every file::test ref verified fresh).
- **15**: reviewers only observe — dimension_observations ride verdict v2
  additively through the same zero-write intake; observations alone can
  never derive accepted; blocker disagreement is UNKNOWN_REVIEWER_
  DISAGREEMENT, never majority-voted; drift trends and the 7-route repair
  vocabulary are route-level (not dispositions); colorstats is pure PIL +
  honest UNKNOWN color metadata; real VLMs wait behind REVIEWER_NOT_
  QUALIFIED.
- **16**: the spend ladder is derived state over existing adoption facts;
  unattended video spend on unadopted keyframes refuses pre-generate
  (transport 0) while humans get advisories; auto-select now skips image
  takes (contract flip, pinned); animatic rides kenburns as a derived
  artifact; the pull sheet round-trips through ShotDraft/CAS discipline.
- **18**: real audio masters off the existing four-bus mix (stems/M&E/
  loudness measured by EBU-R128, M&E provably dialogue-free) fill the 13C
  roles that were honestly skipped; alignment evidence is a companion
  sidecar (UNALIGNED never fabricated); voice profiles carry provenance and
  missing rights BLOCK template export; lip_sync is a standard-admission
  capability whose result API cannot carry a model self-score.
- **17**: canonical/variant/transient identity strictly separated by
  storage; variants resolve deterministically per episode; world state is
  engine-validated text; season health aggregates owners' current results
  verbatim (a stale episode is never masked); template packs ride the 13C
  bundle discipline + the 18 voice-rights gate, import is copy-on-import.
- **19**: media analysis is hash-bound derived evidence (fixture-first,
  ANALYZER_NOT_QUALIFIED for real); cutdowns are proposal payloads with
  no-cut-zone diagnostics and CAS apply; smart reframe compiles ROI tracks
  to rate-limited, safe-area-constrained crop keyframes that preserve the
  FORMAT_ONLY semantic digest — 13C's declared framing becomes executable;
  bridges execute through the standard paid path and provably never reach a
  final unapproved; the tool map dispatches a 10-op whitelist onto existing
  executors with no LLM planner in core.
- **21G**: both gates SKIPPED_WITH_EVIDENCE on real evidence — the Board
  already owns the visual core, and the scheduler benchmark shows the
  ordering gap is textbook LPT-vs-FIFO that vanishes on realistic clips.
  0 production files, like 09_11G before it.
- Program discipline: three agent spawns collapsed to the recurring
  infrastructure fault and were replaced by resuming context-bearing
  agents (18→17, 20A→20B); two agents independently applied the
  provider-layer gate correction that 14's build-boundary guard enforces;
  every batch was CI-gated after the BuildLock lesson.

## 28. AI_IDE 14–21G production closeout — the offline theoretical closed loop (2026-07-12)

**Scope ruling (operator)**: this acceptance round is limited to the offline
theoretical closed loop — C1–C5 only; ALL of C6 (real providers, real model
calibration) is SKIPPED_WITH_EVIDENCE. Consequently
`REAL_NETWORK_PAID_PATH_ACCEPTED` is not written, and
`PRODUCTION_CAPABILITY_ACCEPTED` is not written either (its §11 row "real
reviewer/analyzer calibration cards exist" is C6-dependent); the round's
verdict is recorded as `OFFLINE_THEORETICAL_CLOSED_LOOP_ACCEPTED` with the
full §11 row-by-row table in the completion report.

- **Admission truth moved out of the editable report** (the round's gravest
  baseline finding, Q06–Q08: forging/editing/deleting
  reports/providers/qualification/*.json changed real admission). Network
  admission now re-materializes from hash-chained append-only qualification
  evidence on events.jsonl (the existing WP1 coordinator — no new ledger);
  the report is a display projection with zero authority; corrupt/torn
  evidence fails closed (BLOCKED(evidence_corrupt), transport 0). Floors:
  unattended/paid PRODUCTION_READY, human-interactive
  CANARY_ARTIFACT_PASSED, bridge PRODUCTION_READY with a single-use,
  digest-exact, durably-consumed operator risk acceptance that can never
  cover provider_disabled / capability_not_declared.
- **The bridge gate got a mandatory caller**: baseline had NO enforced
  caller (the claimed "called from cli.py" was false — no bridge CLI
  existed; direct calls bypassed). The gate now lives in
  providers.base.dispatch_bridge — the one service seam execute_bridge
  funnels through — with real `manju bridge plan|run|adopt` wrappers,
  real endpoint frame refs+hashes in the request digest, Shot-derived
  spec_hash (literal "bridge" rejected), Assurance-only adoption emitting a
  zero-write Proposal, and a content-hash finality guard (rename/copy
  cannot launder).
- **One validator, honestly shared**: apply_cutdown re-runs the SAME
  validate_cutdown (baseline: CAS only — inverted ranges applied cleanly);
  the range contract (half-open, single resolvable open tail) is documented
  and shared with roughcut's _complement; key moments carry versioned
  tolerance windows that enter the analysis digest.
- **Variants merge, never single-pick**: all live variants merge in stable
  order (disjoint coexist, same-value dedup, contradictions block
  unresolved); diagnostics and runtime share one predicate. World state
  gained episode/scene/shot scope (shot>scene>episode, no narrow-scope
  leakage). Season health is tri-state; an unreadable runtime projection is
  UNAVAILABLE and forces not-ready (never "None means zero").
- **Masters tell the truth**: RAW_* naming with ambient split out;
  RAW_STEM_SUM instead of a FULL_MIX that implied a program mix;
  PROGRAM_MASTER deliberately absent with the reason recorded (render.py's
  ducking/loudnorm chain is not reused); dropped expected sources block
  verification (silence never verifies); uniform sum headroom with a true
  peak ceiling and blocking AUDIO_CLIPPING; M&E claims bus exclusion
  (excludes_voice_bus), never content-level proof.
- 40/40 closeout red tests failed at baseline 4200109 and are green now;
  suite 3114 → 3168 passed / 13 skipped / 0 failed; 13 existing pins
  updated equal-or-stronger (several had pinned the defects themselves),
  none deleted or weakened; 0 new public schemas; 0 network calls, 0.00
  spent. §8 documentation corrections applied (19's bridge-CLI claim, the
  toolmap "resolver" wording, 18's M&E wording, 14's report-authority
  contradiction; 20B was already honest; 21G stays skipped).

## 29. Function-perfection program — governance first, honest media semantics second (2026-07-12)

Operator directive: five hours of continuous improvement on the external
"Function Perfection Audit / All-Possible Roadmap" doc, own analysis first,
better ideas allowed without approval. Own audit findings that set the scope
(each verified at source before any loop launched): 40 public schema ids +
112 CLI commands with zero governance; Project.fps is int / ProbeInfo.fps is
float / the frame grid is ms x int-fps (NTSC edit rates unrepresentable);
otio.py wrote fractional frame values rounded to 6dp at float rates; the
untrusted-pack inspect path inflated zip members with no caps; `manju unpack`
extractall'd with no size preflight.

Structural rulings (deviations from the external doc, taken deliberately):
- The engine's int edit rate was NOT converted under a deadline. The pure
  rational foundation (core/timebase.py) landed instead, and the int→rational
  edit-rate major is DECLARED in CONTRACTS.yaml planned_migrations — the
  registry's first governed future migration.
- `manju migrate` machinery deferred: zero real pending migrations exist
  (doc §16 #20 real-benefit rule). Registry + compat fixture corpus + declared
  plan are the honest minimum.
- Every shipped schema defaults to STABLE in the registry; newcomers arrive
  EXPERIMENTAL (stable is a promotion, not a default). Growth-hostile exact
  pins (==112 commands, ==40 schemas, experimental-set equality) were rejected
  in review and replaced with floors/membership — the floor is what catches a
  truncated snapshot regeneration, which live-vs-snapshot tests cannot.
- Unpack zip-bomb guard is declared-total-vs-free-disk, NOT absolute caps or
  compression-ratio heuristics: real restores are legitimately huge and
  digital-silence WAVs compress ~1000:1 legally. Template packs (small by
  contract) get hard caps + header-blind stream-capped reads.
- PROGRAM/loudness facts have ONE owner each; conformance consumes the
  masters index and the technical profile, never re-measures, and UNKNOWN is
  never guessed into PASS. Conformance is grep-pinned OUT of release paths —
  promotion to a release gate is a future explicit decision.
- Relink restores BYTES to the paths truth already records — truth files are
  never rewritten; expected hashes come only from the attempt-evidence
  stream (recorded lineage); "verified without a hash" refuses as a lie.

Landed (each loop = audit → red fixtures → minimal contract → implement →
targeted regression → report; definitive suites + CI orchestrator-owned):
- Wave 1 `5334590`: CONTRACTS.yaml registry (50 entries; ownership map;
  planned migration) + core/contracts.py loader + CLI compat snapshot +
  8-fixture compat corpus + doc validation; core/timebase.py (exact NTSC
  rates, SMPTE NDF/DF incl. 17982 + 108/216 pins, grid-drift math:
  23.976-on-24 = one frame per 125000/3 ms, 7.2 s per 2 h); archive
  decompression limits (orchestrator-built). 88 tests.
- Wave 2 `05f85b2`: manju.media-technical-profile/v1 (verbatim-or-unknown
  facts + drift diagnostics, manju qc tech), manju.conform-loss/v1
  (line-cited per-exporter loss tables — otio drops overlays/captions/
  audio-gain/ducking today, recorded honestly; exact fractional-frame drift
  rows), redacted support bundle with a self-scan tripwire that refuses to
  write leaks (manju support-bundle). 81 tests.
- Wave 3 `cdee36e`+`c32f934`: manju.delivery-conformance/v1 (17 checks,
  PASS/FAIL/UNKNOWN/NOT_APPLICABLE, no aggregate score, manju qc
  conformance), manju.relink-plan/v1 (missing-media report joined to
  attempt-evidence hashes; per-row CAS apply; manju relink),
  manju.toolchain-manifest/v1 (record-only reproducibility evidence,
  manju toolchain). 62 tests (E 17 + G 23 + H 22).
- Wave 4 `156e724`+`b072b53`: manju help-workflow (10 task-oriented
  workflows, 91 command strings test-resolved against the live typer
  registry, paid-safety wording preserved verbatim in the recover flow);
  caption roles + CJK-aware accessibility advisories (record + advise,
  never block; role-less projects byte-identical everywhere; 39 tests). README gained the FP rows + governance
  section; snapshot 112 → 118+ via the reviewed regeneration flow (each
  addition an explicit regen; the ≥112 floor held throughout).
- Wave 5 `ff9b067`: .manjupkg fixity (in-zip MANJU_FIXITY.json; unpack
  verifies and REMOVES a failed restore, old packs byte-identical; manju
  fixity verifies without extracting, streaming/header-lie-safe) + the run
  performance view (qc/runperf over existing attempt evidence; cache rate
  derived from the recorded SKIPPED_CACHE_HIT state — the audit inverted
  the addendum's unavailable-default honestly; qc_time/disk_usage named as
  the true unavailable[] gaps; never a scheduling input, grep-pinned).
  32 tests. manjupkg registered as a stable document contract.
- The close-gate integration suite caught exactly ONE cross-loop defect in
  the whole program: an over-broad grep pin ("reports/conform" prefix-
  matched the new, unrelated reports/conformance store) — tightened to the
  precise directory with the rationale in-line. Every loop's own targeted
  verification had been green; only the integrated run could see it.

Suite: 3168 → 3446 (wave-4 close gate) → 3478 passed / 13 skipped / 0 failed (wave-5 final gate); CI green per
commit. 0 existing tests modified beyond sanctioned snapshot regens; 0 new
runtime deps; 0 network. Six of the seven agent loops ran on Opus 4.8 with
binding addenda + orchestrator review; five mid-flight corrections were
issued from early review (all growth-hostility or process); the recurring
agent-spawn collapse struck three times and was recovered by resume each
time.

## 30. The rational migration executed + the interchange continuation (2026-07-12)

Seven-hour continuation. Own analysis first: the fps surface spans 20+
files but spec_hash carries no fps while segment/render keys carry it
explicitly — so a STAGED migration with per-stage legacy byte-identity
pins was feasible, and "upgrade everything at once" was not. The external
list's item 1 executed in stages; items 3/4/5/7/8 landed as side loops;
color/AAF/Board/plugins/benchmarks deliberately untouched (each needs its
own narrow loop or violates the no-new-deps discipline this session).

R-track (the registry's declared migration, closed end to end):
- R1 `d03fbba`: edit_rate truth field + the one accessor; the wrap
  serializer proven load-bearing red-first (a plain dump leaks
  edit_rate:null into every config); byte-identity pinned at every
  surface; a grep pin holds the token to models/container until consumers
  earn it.
- R2 `e5a9052`: the opt-in rational build spine. Telescoping cumulative-
  boundary walker: 2h of 23.976 = +0.5ms cumulative vs +7200.5ms (~172.6
  frames) per-clip-independent — computed in-test as the design's own
  justification. Native -r 24000/1001 verified on real output. FIX-B
  literals verbatim; walker-spy zero on int compiles; keys isolated
  drop-when-int. The R1 pin had gone red at HEAD from S2's committed echo
  reader — an honest orchestration gap (S2's verification set missed
  ratemig1), corrected teeth-preserving.
- R3 honestly COLLAPSED into R4: boundary-exact timelines made burned
  captions and audio inherit correctness; forcing a loop would have built
  for the list, not the need.
- R4 `730fb45`: export truth — OTIO integer frame values at the float64
  rational rate; conform drift all_zero BY CONSTRUCTION (the track's
  closing pin); masters sample facts rational-only so int index digests
  never move; EDL consumes duration_frames.
- R5 `362623a`: the real migrate tool the registry deferred until a real
  migration existed. inspect (no recommended field ever; honest cached-
  segment rebuild forecast) / CAS plan / apply touching ONLY project.yaml
  with no auto-commit (Git is the rollback engine; the revert command is
  printed) / downgrade refused without --acknowledge-loss. migrate.py is
  TOKEN-FREE (field discovered by type) so R1's pin stands unmodified.
  planned_migrations[0]: declared → IMPLEMENTED (this close).

S-track: S1 TTML/IMSC writer `e122404` (x-manju verbatim authority over
registry-token nuance); S2 CMX3600 EDL `4952435` (FCM DF/NDF via
timebase; zero-based source TC stated honestly; never a wrong dissolve);
S3 archive self-description `3496a03` (fixity-covered restore notes +
registry/toolchain snapshots; "no snapshot was fabricated" fallback);
S4 toolchain→keys `d773933` (tokens EXACTLY ffmpeg|fonts, irrelevant
facts REJECTED at validation per the roadmap's own warning; single-probe
spy; kenburns key coverage catch); S5 xplat informational CI `1d393c5`
(orchestrator-executed; allow-fail; the ubuntu gate untouched).

Spawn collapses #10–#12 recovered by resume. Suite: 3478 → 3653 passed / 13 skipped / 0 failed (definitive quiescent run); snapshot 121 → 125.

## 31. T/U wave: pro compare, FCPXML, plugin freeze, locale TTML, BagIt (2026-07-12)

The 7-hour window's second half, straight after #30's close. Parallel agent
loops (T1/T2 then U1/U2); the plugin freeze and every registry/README edit
orchestrator-executed. Items 2/9/11 recorded OUT with reasons; with them,
all twelve roadmap items are dispositioned.

- Plugin API freeze (item 10) `6369034`+`4eef882`:
  manju.provider-plugin-api/v1 registered stable; the PLUGIN_API_CONTRACT
  constant is the ONLY src change (zero behavior). 14 surface pins:
  __all__ floor; FailureKind str-enum value stability; GenerationRequest
  head-field law + the ENFORCED additive law (every post-head field must
  carry a default — was comment discipline, now teeth); ProviderFailure /
  CloudProvider / registry entry-point signatures (knobs keyword-only-with-
  default, checked generically for future knobs); minimal-subclass and
  cloud-trio instantiability proofs; the two protective manifest rules
  (broken adapter + builtin shadowing — sandbox-verified BEFORE pinning);
  chain-terminal pin; and the follow-up pin of the cls(manifest) adapter
  convention + the module:Class happy path end-to-end (a real out-of-tree
  module slots into fallback chains via declared capability). Deliberately
  NO marketplace/discovery — the item's own exclusion.
- T1 board professional compare (item 6) `2225e15`: wipe / difference /
  frame-lock stepping / cut-boundary view as read-only EXTENSIONS of the
  existing compare (7 legacy markers pinned; static board byte-identical
  with the new markers asserted absent; tree-hash read-only proof). The
  difference canvas is a VIEW with the always-honest "amplified ×N — not
  raw pixel deltas" label (stated ×1 fallback); stepping carries the EXACT
  den/num period from the typed rate resolvers, honestly disabled when
  unresolvable; boundary stills ride the EXISTING .manju/frames
  content-addressed cache — the past-EOF sentinel exploits the round-W
  clamp-before-key (verified at frames.py:151-155); one server allowlist
  entry (.manju/frames/), truth files re-pinned 403.
- The T1 pin violation + fix `2d4716e`: T1's fallback named the rate
  accessor literally; the R1 grep pin (board/ is named-forbidden) went red
  at HEAD, and BOTH T1's suite set and the orchestrator's T1 review batch
  lacked ratemig1 — the #30 S2 gap class, second occurrence, caught by
  T2's clean-HEAD stash isolation proof. The pin stayed UNTOUCHED; board
  switched to the typed resolver (load_config().frame_rate), exactly the
  pin's own prescription. STANDING RULE adopted: test_fp_ratemig1.py joins
  the orchestrator's landing-verification batch for every loop that
  consumes rate machinery.
- T2 FCPXML writer (item 5) `40d0bd1` (+README row `1aca907`): the one NLE
  exit where rational time rides NATIVELY — frameDuration is the exact
  rate reciprocal; times "{F·den}/{num}s" never reduced (frame-alignment
  verifiable by inspection); zero drift for int AND 1001-family (conform
  all_zero_by_construction on both paths, pinned via an off-grid-ms case
  OTIO's int path would carry); R2/R4 duration_frames consumed with ms
  telescoping fallback; clean cross-dissolves become native transitions
  with FCP's overlap geometry (pull-back telescoping independently
  verified), everything else a cut + in-band note; captions ride the
  SRT/TTML exits; audio = honest unsupported rows (writer-scope deferred
  increment, NOT a format limit — the addendum's "else" branch, reasoned:
  connected-clip offset coupling is not trivially faithful). DEVIATION
  recorded: red-first ORDER unproven (impl predated tests on disk); the
  orchestrator ran a three-mutation discriminating-power check instead
  (fraction-reduction → 11 red; overlap-guard <= → its guard test red;
  tcFormat flip → golden bytes red; restored 33/33) — substance verified,
  order lapse noted.
- U2 locale-aware TTML (item 4 remnant) `9f0c97c`: the S1 lang slot's one
  legitimate caller wired (locale build writes captions.ttml with
  xml:lang == the validated locale id); NEW declared file
  locales/<lang>/meta.yaml with the closed set {direction: rtl|ltr} —
  explicit human declaration ONLY, never inferred from the language code;
  rtl → tts:direction + unicodeBidi=embed; None path byte-identical
  (proven three ways). Ruby stays out — the cue model has no ruby
  structure; emitting <ruby> would fabricate data. Registry ruling: NO
  documents row for meta.yaml (the lines.yaml/voices.yaml project-side
  pattern) — agent recommended, orchestrator concurred. conform's ttml
  row wording refined accordingly (RTL only via explicit declaration).
  Exemplary red-first: 21 failures with exact causes captured.
- U1 BagIt serialized bag (item 8 remnant) `8b766cb`: pack --bagit writes
  an RFC 8493 bag inside the .manjupkg zip; ONE FIXITY TRUTH PER FORMAT —
  MANJU_FIXITY.json omitted in bagit mode, the BagIt manifests are the
  authority; tagmanifest covers every tag file (incl. the MANJU_*
  transport members) except itself; bag-info is deterministic (no
  Bagging-Date — RFC-optional per a sourced ruling carrying its honest
  no-live-validator caveat; omission stated in the restore note);
  unpack/fixity auto-detect with strict two-manifest verify where extras
  FAIL, and the staging + os.replace(stage/data, dest) restore
  structurally guarantees no dest on verify failure. Default-mode pack
  bytes golden-pinned identical; CLI snapshot regen byte-identical
  (125 rows). Red-first proven: 18 failed / 1 passed (the pre-change
  golden) → 19/19.
- Item 8 "resolver maps" — MOOT for packs, grounded: the pack walk
  includes the full truth+imports payload; only §3-rebuildable caches are
  excluded by default (--full keeps them) and symlinks are skipped, never
  followed. Nothing non-rebuildable is external to a .manjupkg, so there
  is nothing for a resolver map to resolve; hash-based relinking for LIVE
  trees shipped in wave 3 (media/relink.py). No second mechanism built.
- Orchestrator's independent verification beyond per-loop review:
  (a) 4000-case seeded adversarial sweep of the R2 walker — telescoping
  sum exactness, ≤1ms per-clip deviation, ≤0.5ms cumulative drift at
  EVERY prefix: 0 failures; a mis-specified 4th sweep property was
  corrected against compiler.py:324/776 (int rates never reach the
  walker — no echo emitted; legacy byte-identity is structural).
  (b) Cross-loop integration dry-run on a real scratch project via the
  real CLI: new → migrate inspect/plan/apply (CAS, dry-run default,
  printed rollback, no auto-commit) → build → the rendered final probes
  r_frame_rate=24000/1001 → the interchange matrix closed on the SAME
  project: EDL FCM NON-DROP with the honest approximation note, OTIO
  exactly 23.976023976023978 (the R4 float64 pin), FCPXML 1001/24000s —
  four exits, one truth, zero disagreement.
- Color/HDR OCIO/ACES (item 2) RECORDED OUT: honest implementation needs
  OpenColorIO (heavyweight new dep) or pseudo-ACES hand math; both
  refused. Colorstats projections stay experimental (registry-pinned).
  Revisit only as its own loop behind an operator dependency decision.
- Benchmark-driven performance (item 11) RECORDED OUT as work, honored as
  discipline: no benchmarks → no perf work (the item's own rule); the
  opt-in env-gated benchmark pattern already exists (MANJU_C21G_BENCH).
- C2PA / licensing (item 9) RECORDED OUT: honest C2PA needs the real
  toolchain + signing-key custody; a hand-rolled manifest is exactly the
  fake provenance the item warns against. If ever built: delivery-layer
  only, never internal truth (the item's own constraint).

Orchestration: spawn collapse #13 (T1) recovered by resume; T2/U1/U2
spawned clean; U1 additionally recovered from a ~10-minute wedged tool
round via a queued nudge. Agent tokens ≈ 744k across four loops (T1 189k,
T2 247k, U1 179k, U2 129k); the freeze, sweeps, integration dry-run,
registry/README/conform-wording edits, deep reviews, mutation checks and
git/CI stayed with the orchestrator. CI: T-batch `1aca907` run
29195075680 SUCCESS; the S5 xplat informational matrix (allow-fail) is
GENUINELY GREEN on windows AND macos for both `1aca907` and `bc5d97a` —
with test_fp_plugin_api aboard — so the pure layers are demonstrably
OS-portable, not merely unblocking; tip ci.yml on `bc5d97a` (U2+xplat)
run 29195379665 SUCCESS; the close sha's run verified in the window
wrap. Suite: 3653 → 3755 passed / 13 skipped /
0 failed — CONFIRMED by the definitive quiescent full run (1006s,
EXIT 0), which concluded exactly at the arithmetic expectation (the 102
new red-first-governed tests 14+15+33+21+19); the in-flight marker the
previous commit carried is hereby resolved, no correction needed. Snapshot: 125 rows,
unmoved (both new CLI additions are optional flags).

## 32. V-wave: FCPXML connected-audio lanes + board onion skin & scopes (2026-07-12)

The user extended the window three hours after the T/U close (#31, all
twelve items dispositioned). The V-wave deepened the two calibrated
continuations. Both loops agent-executed (Opus), orchestrator-reviewed;
the conform truth-update orchestrator-executed.

- V1 FCPXML audio lanes `1ba26d0` (item 5, the increment T2's #31 entry
  deferred with reasons): non-loop resolvable-duration clips on the four
  buses become CONNECTED role/lane asset-clips at exact frame placement
  on the pulled-back spine geometry — offset = in_frames[i] + F −
  offsets[i], REUSING T2's arrays (no second geometry). The hard case
  (audio under a dissolve-pulled parent with a source in-point) was
  hand-computed by the agent AND re-derived independently by the
  orchestrator (36+(30−6)=60 recovers F exactly). Honest boundaries:
  fades GAIN-ONLY (low confidence in the exact fade element shape — a
  wrong guess risks whole-document rejection; values ride in-band
  notes); loop beds not written (one pass of fill-to-duration is wrong
  audio); duration-None omitted with AUDITED parity to the render path
  (_build_audio_graph never probes; the only compiler-produced None is
  sfx); ducking clips written at static gain, the sidechain relationship
  noted. Empty buses byte-identical to T2's frozen video-only golden.
- The conform truth-update (orchestrator, same commit): V1 CORRECTLY
  refused to touch T2's frozen conform pin and shipped with its static
  audio rows stale — the conform document (the honesty artifact) would
  have understated real exports. Test evolution on a legitimate contract
  change is an orchestrator prerogative (migration-pin / R1-pin
  precedents): six audio rows moved unsupported → approximated with the
  loop/None boundary STATED in the detail; audio_loops honestly STAYS
  unsupported; T2's pin renamed + evolved teeth-preserving ("not a
  format limit" carried forward, plus a new tooth: the OMITTED boundary
  must be stated).
- V2 board onion skin + scopes `f271981` (item 6's last two words):
  onion as a FOURTH A/B mode (CSS opacity on the same stack; per-mode
  inline-override reset so stale opacity never leaks between modes) +
  the boundary-row register-check overlay from the SAME cached stills;
  a scopes panel (Rec.709 luma histogram + per-column waveform of video
  A's parked frame, event-driven redraw ONLY — no play-time loop)
  carrying the permanent honesty label verbatim: browser-decoded RGB,
  video A only, view-only, the exact formula, NOT the engine's color
  facts — QC colorstats remain the measurement authority. No server.py
  change (T1's allowlist suffices). Static board byte-identical; the
  read-only tree-hash proof re-pinned. V2 self-caught two over-broad
  guard tests at first green and tightened them (recorded in its
  report) — the seven discriminating reds were valid.
- Orchestrator hands-on dry-runs of the U surfaces (real CLI, the
  migrated 24000/1001 scratch project, during the V-wave): pack --bagit
  (49 members, exact declaration bytes, no MANJU_FIXITY, deterministic
  bag-info, fixity --info bag summary, verified restore files-equal —
  the only deltas being .git by design and two empty dirs, a zip-format
  property predating U1) and the locale flow (locale add ar +
  direction:rtl + build --lang ar → xml:lang/tts:direction/unicodeBidi
  all correct with Arabic text and a localized final render).
- Suite: 3755 → 3790 passed / 13 skipped / 0 failed — CONFIRMED by the
  post-32a definitive quiescent rerun (1053s, EXIT 0; run 1's single
  c21g failure and its resolution are #32a's record). CI: `a196bcb`
  failed on exactly the known c21g gate (log-verified singular), then
  `348da51` ci+xplat BOTH success — the fix verified remotely on the
  full gate before this flip. Protocol tightened after this episode:
  commits carrying CODE ride only pushes whose definitive suite has
  CONCLUDED; the in-flight marker remains legitimate for prose-only
  closes.
- Orchestration: V1/V2 spawned clean, zero collapses this wave; agent
  tokens V1 233k + V2 182k. Both loops ran the standing ratemig1 rule
  themselves AND the orchestrator re-ran it at both landings.

### 32a. The V2 scopes ⇄ 21G gate conflict (amendment, same day)

The V definitive run surfaced ONE failure the per-loop batches missed:
`test_c21g_gates::test_a_board_does_not_render_the_specialized_views`
forbade the "waveform" token on the Board — V2's scopes panel tripped it.
A genuine CONTRACT CONFLICT, not a bug: the 21G delegation gate (Board =
lean review core; specialized analysis views live with delegated owners)
vs the roadmap's item 6, which EXPLICITLY ordered scopes into the review
interface. Renaming tokens to slip the gate was rejected as evasion. The
gate's own docstring had always called these "the contract's ALLOWED
minimal extensions — none is built, by design": the resolution is the
anticipated arrival, orchestrator-executed teeth-preserving evolution —
"waveform" leaves the forbidden list; a COMPENSATING tooth pins the
discipline that made it acceptable (a Board carrying data-scopes MUST
also carry "view-only" + "colorstats" — the delegation stays intact: the
Board views, it never measures); vectorscope/color-scope and every other
specialized view remain forbidden.

Process finding (the gap class's THIRD instance): T1's landing batch
included test_c21g_gates; V2's batch (and the orchestrator's mirror of
it) dropped it, so the gate was only hit by the definitive run. The
ratemig1 standing rule generalizes: a loop touching surface X runs the
UNION of the previous X-loop's verification batch, not its own
selection. The definitive-suite-before-push discipline caught it exactly
as designed — the failure never reached origin.

Also recorded: the W1 candidate (WebVTT writer) DISSOLVED on pre-launch
audit — compile_vtt has shipped since 13C/18 (captions.vtt + the
CAPTIONS_VTT deliverable + conform coverage); no loop was spawned for
already-existing work. Loop materialization for FCPXML audio_loops is
RECORDED OUT: laying N repeats requires the source's natural duration —
a live media probe the pure writer must not perform (the same fact-source
discipline that omitted duration-None clips); the render's aloop remains
the owner of fill semantics. V1's placement math got a three-mutation
discriminating-power sweep (formula sign → 4 red; lane map → 6 red;
tie-break flip → exactly the tie test red; restored 55/55).

## 33. W/X/Y/Z waves + the four-hour optimization research program (2026-07-12)

The window's final arc: three more implementation waves (item-5/6 depth,
then the audit's own defect) INTERLEAVED with a nine-dimension research
program the user commissioned ("find all the areas that can be
optimised"), closed by a 28-agent adversarial refuter panel. Peak
concurrency: 28 refuters + 6 loose agents.

Implementation:
- X1 board pro transport 2cb8765: K/L/J/arrows on the ONE delegated
  handler; the honest J ruling (single step-back, label states browsers
  have no native reverse, fake shuttles pinned absent); T1's period math
  extracted once into framePeriod/cmpSeek.
- W2 FCPXML import-plan 353bfbc + W1 loop materialization 8872eb0 (render
  parity -stream_loop verified FIRST; cumulative-boundary passes; W1
  wedged 51min post-delivery — artifacts verified independently). W2's
  registry row (manju.fcpxml-import-plan/v1, EXPERIMENTAL) applied by the
  orchestrator per stop-and-report.
- Y-wave, all four in parallel: Y1 EDL A1/A2 2001bfe (blocked-by-track
  ordering keeps S2 goldens byte-identical; sfx/ambient never squeezed —
  per-clip notes; conform rows handed off per amendment); Y4 EDL
  import-plan ea07b14 (rate_assumed honesty — EDL carries no rate; every
  reel a needs_relink by nature; registry row applied, 48→49); Y2 doctor
  probes d6f80c2 (locale rows surface load_locale_meta's message
  VERBATIM — consult-don't-copy pinned; exit-code policy pinned
  unchanged); Y3 DTD fades 954388d — the fades uncertainty T2 and V1
  twice recorded DISSOLVED by orchestrator research against Apple's
  archived FCPXML v1.7 DTD (adjust-volume → param name="amount"
  [convention, not DTD-mandated — documented] → fadeIn/fadeOut
  type="linear" = afade parity), with the JOINT CONFORM TRANSACTION:
  V1's pin evolved, Y1's rows applied, S2's pin evolved per the surfaced
  collision, edl_import coverage declared, both stale scope notes
  updated. FCPXML's writer story is now complete: placement + roles +
  lanes + gain + materialized loops + real fades; remaining honest
  boundaries: ducking (no primitive), loop start_offset_ms combo (W1's
  noted follow-up).
- Z1 e09fb95: the audit's confirmed packaging defect FIXED same-window —
  ColorStatsUnavailable adapter wall (house family), colorstats extra
  with the REASONED Pillow>=9.3 floor (first cp311 wheel; the audit's
  own >=9 hypothesis corrected by evidence), doctor probe row; the
  audit's "qc colorstats command" phrasing corrected (no CLI command
  exists — the import path is the crash surface).

Research (REPORTS/OPTIMIZATION_AUDIT_20260712.md is the deliverable):
- Nine dimensions, all READ-ONLY agents, headline mechanisms hand-
  verified by the orchestrator before the panel: performance (tail_events
  full-file parse with n=100_000 callers; the voice-probe-per-compile
  asymmetry; double hashing; import cost), code quality (cli.py
  decomposition map; atomic-write inconsistency; dual ToolError; dead
  code), tests (40% runtime in 15% of files; xdist feasibility w/
  worklist), robustness (7 of 8 areas clean CONFIRMATIONS; board CSP
  gap; fcpxml input caps), architecture (60%-lazy import discipline
  holds the only acyclicity; TWO hard qc→build edges; §3 authority
  actively enforced — a past violation found deleted), UX (the
  Quickstart select-crash DEFECT; flat 65-command help; localized
  next-step token), deps+AI (Pillow defect; OTIO phantom dep; MCP
  surface ≈3.4k tokens/session with digest-free trim path; SKILL.md
  stale), GUI (316 ffprobe spawns on the /review request thread;
  build_state doubles both evals; ~8.3KB JS copied ×10), providers
  (local_cmd orphans grandchildren on timeout; transient-5xx kills paid
  jobs; FailureKind drift; the fallback floor untested directly).
- The 28-refuter panel: 17 CONFIRMED / 11 ADJUSTED / 0 REFUTED — no
  finding died; adjustments (folded) include defer_build savings halved,
  a second qc→build hard edge, the CI-smoke redundancy's real pinning
  test (test_idempotency), and count/path corrections. The audit's
  do-not-touch list (negative results) is recorded as load-bearing.
- Discipline note: this report IS the benchmark evidence the item-11
  rule requires; NO performance change shipped in the window that
  produced it (Z1 fixed a packaging defect, not perf).

Orchestration: W1 and Z1 wedged post-delivery (51min/13min) — both
recovered by nudge with artifacts verified independently first; the
OPT-security spawn was killed by an API-filter false positive and
relaunched reworded (OPT-ROBUST). Two candidate loops DISSOLVED on
pre-launch audit (WebVTT existed since 13C/18; OTIO audio since R4) —
recorded as the pattern that the remaining-work list underestimates
existing coverage. Suite: 3790 → 3928 passed / 13 skipped / 0 failed
(definitive quiescent run with --durations profiling folded in). CI:
the batch push's verdict in the window wrap.

## 34. OPT wave: the audit's six items implemented + the CI flake killed (2026-07-13)

The user approved the optimization backlog wholesale ("1-6 all"); six
parallel loops ran on disjoint file sets, each reviewed against
orchestrator bars written from independent reads BEFORE the diffs landed,
with the orchestrator's own adversarial harnesses as the acceptance gate.

- L1 tail_events backward-read (91e5ce6): seek-from-EOF 64KiB blocks,
  1385x-5900x for small-n tails on MB-scale logs, n=100000 still faster.
  Byte-exact parity pinned adversarially (83 cases). The orchestrator's
  independent harness caught ONE divergence the loop had classified
  theoretical — lone-\r separators (old universal newlines split them;
  the first cut lost events on hand-edited logs) — fixed by splitting on
  both newline bytes (\r can't sit inside a multibyte sequence either)
  and pinned red-first. One deliberate improvement kept: an undecodable
  torn line skips (like follow_events) where the old strict text reader
  crashed the whole tail. str.splitlines() deliberately avoided —
  U+2028/9/85 are legal inside ensure_ascii=False JSON lines.
- L2 voice probe cache (2d74a22): the three synthesis sites now stamp
  VoiceTakeSidecar.probe via the SAME media.probe reading the live path
  uses; the compiler reads cache-first at both consumption sites
  (slate + WP4 locale base-voice) with probe_fn fallback. Zero live
  voice probes on cached compiles (spy-pinned), exactly-one-probe legacy
  fallback (§4.3), cache-vs-live timeline JSON identity.
- L3 /review lazy consistency boards (1c71196): request thread 3409.5ms
  -> 231.0ms at 8 shots, spawns 474 -> 0 (spy FORBIDS subprocess on
  render); unit structure + verdict forms stay inline (existing
  inline pins kept green); boards compose on demand via token-gated
  POST /api/review/consistency — readonly-allowed as capability-
  PRESERVING (boards already loaded inline on readonly workbenches;
  the endpoint writes only the rebuildable frames cache). _member_frame
  reads the sidecar probe first: 64 redundant ffprobe spawns stripped
  from board composition itself, frame keys byte-identical.
- L4 hash_file memo (5cf08bc): process-scoped, (realpath, size,
  mtime_ns)-keyed, LRU 4096, per-call MANJU_NO_HASH_CACHE kill-switch,
  SHA-256 outside the lock, failures never cached; the render/graph
  double-hash collapses at the one seam with render.py/graph.py
  untouched. The staleness window (same-size rewrite inside one
  mtime_ns tick) is documented honestly, not engineered away. Open-spy
  pins prove the collapse at the REAL _segment_cache_key seam with a
  kill-switch control.
- L5 pytest-xdist (c067715; ci flip 167dc8b): all 11 raw os.chdir sites converted to
  monkeypatch.chdir / MonkeyPatch.context() (FIVE were truly
  unguarded — the audit's two in test_providers_routing plus all three
  in test_quality_modes, no try/finally at all); lint pin forbids raw chdir
  under tests/ with no allowlist; pytest-xdist==3.8.0 (+execnet==2.1.2)
  pinned in constraints + dev extra. Full-suite -n auto proof:
  4056 passed / 13 skipped / 0 failed in 337.34s (0:05:37). ci.yml flipped to `python -m pytest -q -n auto` by the
  orchestrator ONLY on that proof (also killing the bare-pytest footgun
  in CI); fallback documented: revert the one line to serial module
  form, keep the isolation fixes.
- L6 polish trio (4c067f9): 8 bilingual rich_help_panel groups over
  the ~65-command help wall (cli_surface.json pins commands+params, not
  panels — zero drift); SKILL.md gains the missing rows (migrate,
  locale, pack --bagit, export formats, and the THREE import-plans —
  openclap/fcpxml/edl); MCP tool descriptions compressed —
  the agent-surface digest excludes description text (policy.py:359,
  verified before editing); names/params/enums untouched, mcp-tool-surface
  digest pins green. Honest yield: ~196 tokens/session — the cut was
  changelog provenance (round/goal/finding numbers), everything
  agent-facing kept; most of the audited ~3.4k surface is load-bearing.
  A new SKILL cheat-sheet drift-guard tooth was added (red on the
  unpatched tree with exactly the 9 missing surface tokens) since no
  existing pin covered rows — approved scope addition. SKILL.md now
  291/300 against its hard line ceiling (future growth needs the same
  discipline).

Also in this wave — the CI red on cfe6c8a (run 29220786644) root-caused
and killed (ba68585): test_fp_bagit's _seed rewrote two identical-content
files before EVERY pack, refreshing mtimes between the double packs of
the byte-identity pins; zip members quantize mtimes into 2-second DOS
ticks, so a straddled boundary differed the archives by exactly two
mod-time bytes (~2-3% odds per run — latent since U1, first fired on the
slower CI runner). _seed is now idempotent with a red-first
no-mtime-churn tooth; the byte-identity assertions are unchanged. L4's
loop independently converged on the same exoneration mid-wave (its
bisection found hashes/CRCs identical before being unblocked) — two
independent derivations, one root cause.

Orchestration: the six loops shared one working tree under a disjoint
file-ownership map; the orchestrator hand-verified each mechanism
against independent reference reads (probe-equivalence, digest scope,
realpath keying, readonly-allowlist capability argument), ran its own
adversarial harnesses (tail-parity, hash-cache stress), and re-ran every
loop's union batch before committing per loop. L5's first -n auto proof
collected L1's pin file mid-edit (its before/after tree fingerprint
caught exactly this) — rerun clean after the tree settled; the flagged
failure was the mid-edit artifact, not an xdist issue. Suite: 3934 ->
4056 passed / 13 skipped / 0 failed in 936.10s (0:15:36) (definitive quiescent serial run) and 4056 passed / 13 skipped / 0 failed in 337.34s (0:05:37) under
-n auto. CI: the batch push's verdict in the window wrap.

## 35. OPT wave 2: providers robustness, GUI/board hardening, test speed — the audit executed to the floor (2026-07-13)

The user approved the remainder wholesale ("Do everything, then wrap up
afterwards"). Three loops on disjoint files; same bars-before-diffs
orchestration as #34.

- M1 providers (feabff4): F1 local_cmd process-group kill on timeout
  (grandchild GPU workers no longer orphaned; identical
  ProviderFailure(timeout) surface); F6 transient poll-5xx no longer
  kills paid jobs (generic_cloud + comfyui keep polling within the
  EXISTING budget; 4xx stays terminal; submit path + DR06 idempotency
  BYTE-untouched — the audit verified them sound and this wave kept its
  hands off); F4 shared status_to_kind ends the 429 drift (tts poll +
  asr now record rate_limited); F5 comfyui 400 bad-graph → invalid;
  F2 stock records failures in the house shape; F3 generic_cloud job
  dicts evicted at the download terminal; F11 stock HTML-error-page
  guard; F9 the degradation-chain floor (kenburns/caption_card/manual)
  gains direct offline tests. Deferred with reasons: F7 manifest knobs
  (additive plugin-API design), F10 poll-loop consolidation (no live
  defect), F12 comfyui cancel (behavior change, own charter).
- M2 GUI/board (f96bf00): board served responses gain the GUI's
  security headers (script-src pinned to the sha256 of the actual served inline
  script computed at serve time; exported static bytes untouched — the
  byte-pin held); oversize-POST Connection: close;
  ONE Project.safe_served_path now feeds BOTH serving gates (audit 14 —
  one-sided-hardening risk retired); G2 build_state evaluates once (was 2x each pass; spy-pinned
  exactly-once); G3 /edit renders with zero explain recompiles and zero spawns
  (lazy GET /api/edit/dirty); G5 754B x 10 pages of byte-identical JS helpers deduped into
  /common.js (divergent glossary/workspace/pollJob left as documented
  residuals);
  G6 disposable jobs.jsonl stops fsyncing on the POST thread + the
  three uncovered endpoints (/api/impact, /api/director/suggest,
  /api/git/diff) gain HTTP-layer tests; VerdictError bilingual;
  fcpxml_import byte cap + DOCTYPE/ENTITY refusal. Deferred: G4
  transport-substrate consolidation (board byte-pin binding; the
  safe_served_path extraction is the mitigation actually taken).
- M3 test speed (b4af70d): build-once-read-many fixtures
  (qc_consistency, dr03c siblings), ultrafast fixture clips, per-file
  worklist — 27.5s serial saved on the touched set measured honestly (two of
  the research report's read-only classifications CORRECTED to fresh —
  a cold-cache call-count pin and a verdict writer); zero assertions
  weakened, touched-set count 284 before AND after; rule-outs respected (pipeline preset
  frozen, sleeps load-bearing, tmp_project function-scoped).
- Orchestrator ci.yml: the M0 smoke's redundant double build dropped in
  favor of a minimal console-script entry-point check — the suite's
  test_idempotency pins the same double-build behavior (refuter-
  corrected citation); the script-wiring coverage the smoke uniquely
  held is preserved.

Suite: 4056 -> 4103 passed / 13 skipped / 0 failed — serial 948.04s
(0:15:48) and -n auto 311.68s (0:05:11) in perfect count agreement.
CI: the batch push's verdict in the window wrap. This closes the optimization program: 4h research audit
(#33) → wave 1 (#34) → wave 2 (#35); everything actionable in the audit
is implemented, consciously deferred with reasons, or on the
do-not-touch list.

## 36. WINDOWS wave 1: the hard Windows gate + file/process semantics (2026-07-13)

The user handed over MANJU_WINDOWS_ONLY_LEAN_V3 ("implement as much of this
advice as possible") — Windows 11 becomes the primary platform, one wave at
a time, audit-before-touch, red tests before code. W1 = §3.1–3.5 (hard CI,
path rules, atomic writes, locks, process trees). Reports:
REPORTS/WINDOWS_WAVE_1_BASELINE.md / WINDOWS_WAVE_1_COMPLETION.md.

- W1 (b1349ec): the Windows-lethal find of the audit — BuildLock's
  _pid_alive used os.kill(pid, 0), which CPython maps to TerminateProcess
  on Windows: the staleness probe KILLED the live lock holder it was
  checking. Fixed with an OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)
  + GetExitCodeProcess probe behind an _IS_WINDOWS dispatch; POSIX path
  and the lock-file mechanism byte-identical (the plan's
  不要无证据重写现有锁 honoured — test_b4's real dual-process race is
  already Windows-compatible and now actually runs on a Windows gate).
- DECISIONS #20 partially REVERSED, deliberately: "no msvcrt lock —
  Windows appends refuse/drop" was decided when Windows was informational.
  Under a Windows-primary plan an evidence ledger that drops every
  best-effort record and raises on every required one is dark exactly
  where truth matters most. events_lock now takes an msvcrt.locking
  byte-0 lock on the SAME sibling lock file (same poll loop, deadline and
  never-write-unlocked policy); platforms with NEITHER primitive keep the
  #20 refuse contract, and the flock path is byte-identical.
- New ONE lexical owner core/idents.windows_relpath_problems (+ segment
  variant + casefold collision key): reserved device names / ADS colons /
  trailing dot-space / backslash / UNC / absolute / traversal. Wired as
  REJECTION at every intake boundary (shotpackage path_hints, 5 GUI
  name gates, unpack members) and as WARNINGS-ONLY in manju check (old
  projects never gain errors — W1 completion condition).
- yamlio.replace_with_retry: bounded winerror-32/33-only ride-out (8
  attempts, capped backoff) around os.replace; access-denied/disk-full
  stay one-attempt; old file survives every failure path.
  media/ffmpeg.atomic_output now fsyncs the finished temp and shares the
  same swap semantics (its bytes were never fsynced before).
- local_cmd timeout reaping: taskkill /PID <pid> /T /F on Windows (the F1
  grandchild leak was unreapable there); killpg path untouched.
- media/align.py: the last str().startswith() directory-boundary check
  replaced with resolve+is_relative_to (sibling <root>_evil/ admitted +
  relpath crash).
- .github/workflows/windows-ci.yml: NEW hard gate — windows-latest, no
  continue-on-error, full extras, FULL pytest (everything xplat.yml
  deliberately skips), pinned FFmpeg via choco 8.1.2 (checksum-verified
  package; version + presence asserts so silent skipping can never fake
  green). Direct-URL + hardcoded SHA-256 recorded as the upgrade at next
  bump (unreachable from the authoring environment: gyan.dev 404,
  github release downloads 403 by network policy). ubuntu ci.yml stays
  (the dev loop lives there); macOS remains informational in xplat.yml.
- Deviation recorded: 11 production files vs the plan's ≤10 — events.py
  joined mid-wave when the ledger-dark-on-Windows fact surfaced (a green
  Windows gate is impossible without it). Deferred with evidence: single
  unified subprocess runner (~30 sites), Job Objects (no failing orphan
  test), LockFileEx rewrite (no red evidence), casefold-aware
  copy_collision_safe suffixing.
- Suite: 4115 → 4137 passed / 1 skipped / 0 failed (18 red at HEAD → all
  22 new green; zero regressions). Authored with NO Windows host — the
  Windows branches are pinned by stub/injected-constant unit tests, and
  the first windows-ci.yml execution is the real-host verdict (recorded
  here once it runs).

## 37. WINDOWS wave 2: per-user installer + doctor --windows + the gate's first verdict (2026-07-13)

W2 of MANJU_WINDOWS_ONLY_LEAN_V3 (§4). Reports:
REPORTS/WINDOWS_WAVE_2_BASELINE.md / WINDOWS_WAVE_2_COMPLETION.md.

- W2 (72dfa91): scripts/windows/{install,update,uninstall}-manju.ps1 —
  per-user %LOCALAPPDATA%\Manju\App\<version>\venv with a run-time
  pointer launcher, staging → self-test → atomic switch, previous.txt
  rollback, uninstall that can never touch *.manju projects or ~/.manju.
  Static safety pins (no dynamic eval / no downloads / no registry / no
  admin / USER-scope opt-in PATH only) + a real windows-latest
  install-smoke job in windows-ci.yml. Doctor: python row, --windows,
  Windows probes (long-path policy READ-ONLY, NTFS/network advisories,
  OneDrive, config writability, Edge/Chrome via new html_card.find_edge,
  install mode + rollback visibility), all informational, UNKNOWN never
  rendered as pass. Output hygiene: NEW supportbundle.redact_private_text
  at doctor's one add() choke point — usernames/private roots collapse,
  /usr system paths stay readable.
- REJECTED_WITH_REASON — §4.1 config under %APPDATA%\Manju: the config
  owner is ~/.manju across six modules, consistent and Windows-valid;
  relocation risks silent data loss for zero functional gain. Doctor
  reports the dir + writability instead. DEFERRED — Portable ZIP (§4.4,
  plan ranks it second to the PS installer; no artifact hosting exists);
  six-site ~/.manju consolidation (churn without a Windows deliverable).
- The gate's FIRST verdict (run #1, W1 push): RED with real Windows
  product bugs — 76F/4016P/35E in 9m30s. Catalogue: ass= filter
  drive-colon escaping breaks EVERY subtitled final render (the ~60-test
  cascade); fcntl-only recents/library/failures locks lose concurrent
  updates; local_cmd shlex(posix) eats backslashes; ffprobe-8 color-tag
  skew; html_card leading-backslash file URL; animatic path/None; plus
  POSIX-assuming tests (mode bits, dir-fsync, grep-pin separators).
  Driven down root-cause-first in the Windows-gate fix rounds (#38+).
- Suite: 4137 → 4158 passed / 1 skipped / 0 failed. The m0 incremental
  test was made xdist-order-safe (primes its own cache) after one
  observed ordering flake — recorded, not silenced.

## 38. WINDOWS wave 3 + gate rounds 1-2: NLE exchange, annotations, and driving the gate down (2026-07-13)

W3 of MANJU_WINDOWS_ONLY_LEAN_V3 (§5) interleaved with the hard gate's
first real verdicts. Reports: REPORTS/WINDOWS_WAVE_3_BASELINE.md /
WINDOWS_WAVE_3_COMPLETION.md. Orchestration: three parallel red-first
implementation agents on disjoint owners (xmeml+conform / fonts / board)
+ orchestrator CLI wiring and integration review.

- W3 (597656a): NEW exporters/xmeml.py (XMEML v4: timebase+ntsc exact
  rational rate, whole-frame spine, file://localhost pathurls, linked
  A/V, clean dissolves only, in-band omission comments); conform gains
  the xmeml table + caption_roles/caption_speakers loss rows across ALL
  9 targets (§5.4 不伪造 made machine-readable); manju export now ships
  a conform-loss doc per produced target (the audit's biggest W3 gap:
  the machinery was library+tests only); find_font Windows branch
  (curated CJK list + winreg fallback, POSIX byte-identical, glyph
  coverage stays UNKNOWN); manju.review.annotation/v1 — THE one budgeted
  schema — with media-hash-bound, staleness-aware board annotations
  (annotate action: strict validation + build_lock + checked_shot_write
  + CAS; serve-only UI with click-to-seek and STALE badges; static board
  bytes pin-frozen); board --app (Edge app window, honest fallback);
  manju --version. Pin evolutions (reviewed): test_fp_ttml caption
  sub-features preserved; board switched to the sanctioned
  ProjectConfig.frame_rate resolver when ratemig1's grep-pin caught the
  edit_rate token.
- REJECTED/DEFERRED with reasons: xmeml IMPORT plan (schema budget spent
  on review.annotation; AAF stays skipped per plan); subtitle model
  expansion ruby/vertical/sound-cue (no upstream producer — dead fields
  would fabricate; conform rows carry the honesty); freehand/canvas
  drawing UI (schema carries geometry; UI is a future increment);
  auto-routing annotation→repair (repair_variable carried; the
  qc/production._VARIABLE_ROUTE seam stays human-triggered).
- Gate round 1 (133a235, after run #1's 76F/4016P/35E): TWO-LEVEL
  filtergraph escaping (the graph parser unescapes C\: and the filter's
  OWN option parser splits at the drive colon — every subtitled render
  on Windows died); msvcrt byte locks for recents/library/failures;
  agent_review append under the events coordinator; local_cmd non-POSIX
  split; ffmpeg pin 8.1.2→6.1.1 (choco carries the EXACT ubuntu apt
  version — the ffprobe-8 color-tag skew class died with the repin).
  Run #3 verdict: 41F/4105P/9E (111→50 entries).
- Gate round 2 (597656a): card/boards/waveform filter paths delegate to
  the ONE escaping owner — the caption-card death explains the whole
  'missing' cluster (offline fallback fell through to kenburns, which
  demands ref images); events_lock Windows branch pairs the byte lock
  with a per-name threading.Lock (real-host proof: CRT byte locks do NOT
  exclude same-process threads — 12 verdict threads → 6 lines);
  installer self-test now gates the pointer switch via LASTEXITCODE
  (run #3's install-smoke switched on a FAILED self-test) and pointers
  write ASCII (PS5.1 UTF8 BOM breaks the launcher's set /p); gate jobs
  run PYTHONUTF8=1 (cp1252 vs CJK sidecars). Remaining tail (expected to
  shrink on run #4, recorded honestly): ticket2 argparse, round_w_meta
  argv fallback, round_q repair JSON, ingest dedupe counts, auto_agent
  event counts, mcp_copilot e2e, job_cancel kill latencies, gui_finish
  preview format, edit_v3 caption preview, local_cmd video_ref compare —
  next-round evidence per the plan's iterative discipline.
- Suite: 4169 → 4269 passed / 1 skipped / 0 failed (100 new tests, zero
  regressions). ubuntu gate green throughout (one pre-existing
  render-clock byte-pin flake frozen in 4d1668a).

## 39. WINDOWS gate GREEN: runs #5-#7, rounds 3b-4, terminal verdict (2026-07-13)

- **The Windows hard gate is green.** Run #7 (commit `d2cfff7`, actions run
  29258059991, 2026-07-13T14:39Z): full suite on windows-latest — **0 failed**,
  plus `install-smoke` green (install → launcher `--version` → update →
  rollback → uninstall, CJK-named project preserved). Toolchain exactly as
  pinned in W1: choco ffmpeg 6.1.1 (the ubuntu apt version), PYTHONUTF8=1,
  full extras, `pytest -n auto`, no skip-as-green.
- Trajectory (each run's residue became the next round's red evidence, per
  the plan's iterative discipline): #1 76F/35E → #3 41F/9E → #4 7F → #5 5F →
  #6 2F → #7 0F.
- Round 3b (`6f8c5f6`, from run #5's evidence): the dr02 lost-verdict mystery
  was NOT the msvcrt lock (the 12-thread probe PASSED on the real host) but a
  SECOND unserialized appender in `qc/agent_review.py` (the v2 batch path) —
  now under the same events-coordinator lock, fail-closed. Full-duration
  cancels: a Chocolatey shim spawns the real ffmpeg as a CHILD, so
  `taskkill /T` must run BEFORE terminate() — killing the shim first orphans
  the grandchild beyond /T's reach (`_taskkill_tree` in media/ffmpeg.py,
  cancel AND timeout branches).
- Round 4 (`d2cfff7`, from run #6's evidence): the auto-agent fake `.cmd`
  died on cmd.exe's 8191-character command-line cap (the composed playbook
  prompt exceeds it by design); the fake agent is now a python script invoked
  as `{sys.executable} fake.py {prompt}` — CreateProcess runs it directly
  (32K argv cap), byte-identical on both platforms, zero skips.
- Windows-only lessons now encoded as pins/comments (also in CLAUDE.md):
  CRT byte locks don't exclude same-process threads (per-name threading.Lock
  pairs them); choco shims parent the real exe (tree-kill first); cmd.exe
  8191 cap (don't route long argv through .cmd); PS5.1 UTF-8 BOM corrupts
  `set /p` pointer reads (ASCII pointers); NTFS casefold moves `sorted(Path)`
  (sort key=as_posix).
- Suite at green: 4270 passed / 1 skipped / 0 failed on the authoring host;
  ubuntu gate green throughout.

## 40. WINDOWS wave 4: the colour minimal closed loop (2026-07-13)

- Scope (plan W4, conditional): ONE opt-in project.yaml block `color:`
  (ColorSpec — tag_outputs, input_transform srgb_to_bt709|p3_to_bt709), no
  new public schema (the W3 annotation schema spent the budget). Absent =
  byte-identical everywhere: serialization (the edit_rate/cache_toolchain_keys
  wrap-serializer join), cache keys (fold-only-when-active, the look/S4
  precedent; boundary keys inherit via the neighbour segment keys), command
  lines (_enc_params literals pinned). `color: {}` rejected — absent is the
  off switch.
- tag_outputs stamps finals/proxies bt709/tv — states what the pipeline
  already produces in substance, converts NOTHING; rides the hashed encoding
  list so flipping it re-keys honestly and flipping back restores the
  original key byte-for-byte (test-proven).
- input_transform is a DECLARED source colour (the user states it; nothing
  probes or guesses), converted at the one normalize seam with one fixed
  zscale chain per token. Empirical chain design against the pinned ffmpeg
  6.1.1: zscale REFUSES untagged yuv without a full input-side declaration
  ("no path between colorspaces"), zimg ignores the yuv declarations for RGB
  stills, alpha PNG + full-range mjpeg pass, and the conversion measurably
  moves pixels ((200,30,30)→(202,40,41) — sRGB→BT.1886, not a relabel).
- HDR_SOURCE warning in the technical profile (smpte2084/HLG transfer or
  bt2020 primaries, axes named; facts-only profile_digest unmoved). colorstats
  adopt_via honesty fix: the advertised `manju repair --op grade` DOES NOT
  EXIST — now implemented:False + the real manual path; pinned. doctor gains
  a zscale preflight probed only when a project declares a transform.
- REJECTED with reasons: OCIO/LUT plumbing (no calibrated-monitor workflow
  for one user — dead config fabricates correctness); ColorTransformPlan doc
  (ceremony for two booleans); HDR tone-mapping (no HDR display/QC path to
  validate a curve — the warning states the limit); the grade repair op (the
  pointer was the lie); per-clip overrides (homogeneous personal sources).
- 22 new tests (tests/test_windows_color.py), red-first (collection
  ImportError + the behavioral all-axes-unknown red on a real render); guard
  suites green (fp_profile/contracts/ratemig1/transitions_looks/cli_snapshot
  105, c15_color+colorstats_wall 17, windows_doctor 15). Reports:
  WINDOWS_WAVE_4_{BASELINE,COMPLETION}.md.

## 41. WINDOWS wave 5: conditional close-out — archive hardening, hw-encode facts, bench; C2PA rejected (2026-07-13)

- Gate interval facts: run #9 (103be30, the W4 colour code) GREEN — W4 held
  the gate. Run #10 (b20c321, a DOCS-ONLY diff) failed 1/4276 with WinError
  10053 (loopback abort under xdist — pure environment flake): fixed as gate
  hygiene by a bounded transient-abort retry in test_shot_lab._req (HTTP
  statuses, even 5xx, are real results and are never retried; three aborts
  in a row still fail loudly).
- W5.2 archive hardening (owner manju.cli + the W1 core.idents rules):
  pack prunes linked DIRECTORIES wholesale — Path.is_symlink() is False for
  an NTFS junction and rglob TRAVERSES it, and a symlinked dir is equally
  traversed on ANY OS (real-symlink proof; the inner files are not links so
  the per-file skip never fired): one link = an entire outside tree smuggled
  into the archive. nt probe = lstat st_file_attributes & 0x400
  (Path.is_junction is 3.12+; floor 3.11). Pack WARNS — never blocks a
  backup — on members our own unpack would refuse and on casefold-collision
  groups; --json portability block, drop-when-empty. Unpack REFUSES
  casefold-colliding members on every OS (one path after an NTFS extraction
  — silent data loss; the W1 member-gate class). Cross-destination restore
  pinned (a different drive is the same code path by construction).
- W5.4 hw-encode delivered as ELIGIBILITY FACTS, never auto-enable:
  media/ffmpeg owns HW_ENCODER_CANDIDATES (h264 amf/nvenc/qsv) +
  encoder_inventory (one process-cached '-encoders' parse; missing ffmpeg =
  all-absent facts) + hw_encode_eligibility (pure predicate,
  NONE_LISTED/LISTED). LISTED ≠ VERIFIED — the authoring container itself
  lists nvenc/qsv with NO GPU. Manifest gains the additive record-only
  encoders block via delegation (the font-inventory precedent; a first
  draft in the fenced module tripped the fp_toolchain boundary pin — even
  on a comment literal, the W3 lesson repeating). doctor row informational.
  Encode paths stay libx264, literal- and grep-pinned. REJECTED: a
  video_encoder switch (needs the full encode lockstep + a VERIFIED canary;
  eligibility without a consumer is deliberately inert evidence).
- W5.3: MANJU_BENCH=1 opt-in env-gated bench (prints cold/warm/final wall
  times, sanity floors only). REJECTED: a clock-based perf report beside
  qc/runperf — the forbidden parallel system; §8.7 pins untouched.
- W5.1 C2PA: REJECTED_WITH_REASON — provenance proves authorship to third
  parties; a personal single-user archive has no distribution chain, no
  verifier, no key lifecycle to justify a heavy c2pa dependency with no
  consumer; integrity is already carried by MANJU_FIXITY.json + the BagIt
  manifests. Recording the rejection is the W5.1 deliverable.
- 20 new tests (test_windows_archive 10, red-first 6R/3pin;
  test_windows_hwencode 9, collection-red; test_windows_bench 1 env-gated).
  Suite: 4311 passed / 2 skipped / 0 failed. Reports:
  WINDOWS_WAVE_5_{BASELINE,COMPLETION}.md. With W5 the plan
  MANJU_WINDOWS_ONLY_LEAN_V3 is COMPLETE: every wave landed or
  rejected-with-reason, on a green hard gate.

## 41a. W5 gate verdict addendum (2026-07-13)

- Run #11 (d49845c, the W5 tip): 3 F — (1) the test_mcp order-dependence
  flake (a module-scoped test asserting on events "earlier tests" wrote;
  xdist load distribution hands single tests to fresh workers — made
  self-contained, ccc98b3); (2)+(3) the two pack-side casefold-warning tests
  assumed a case-colliding tree (Take.wav + take.wav) can exist — on NTFS
  they are ONE file (the W1 colon-directory lesson, casefold edition; the
  Windows-side protection is the unpack REFUSAL, which ran green). Fixed
  with a real filesystem capability probe, never an os.name assumption
  (a5c2fa3); docstring escape-sequence DeprecationWarnings killed (597886c).
- Verdict on a5c2fa3: **ubuntu CI green + Windows CI green** — the W5 wave
  and the plan close-out stand verified on both platforms.

## 42. UX program: the three-axis usability audit + waves A-E (2026-07-13)

- Mandate: the owner asked for a sustained usability/experience optimization
  pass across three axes — the owner's daily experience, a future AI
  session's effectiveness, and the project's own health. Method: 7 read-only
  auditors over disjoint dimensions (CLI, cold-start, GUI/board, AI-DX,
  Windows real-usage, docs truth, test health), every finding evidence-cited
  (file:line or captured live output); 3 lens rankers (owner-value / ai-dx /
  risk-maintenance) scored all 46 findings. Ranked backlog + disposition:
  REPORTS/UX_AUDIT_2026-07-13.md (raw JSON preserved in the session).
- Wave A (verified owner-facing defects, all red-first): status traceback on
  hand-edited YAML + the broken --json error contract (truth_parse_error);
  new-on-existing traceback; unpack/fixity BadZipFile traceback at the
  disaster-recovery moment (bad_archive); nt stdout/stderr UTF-8 hardening
  (doctor's ✓/✗/⚠/• died on GBK redirects; launcher now sets PYTHONUTF8=1);
  Windows-pasteable repro command lines (list2cmdline on nt, POSIX
  byte-identical); /review CAS-token refresh (the owner's second action on a
  card was always refused 409 by their own save); the g/x keyboard hint
  named the wrong state machine.
- Wave B (AI-DX): CLAUDE.md gains the dev-loop commands, the STANDING
  GREP-PINS block (comment literals trip raw-source scans — four recorded
  burns — with exact test ids + the union-batch rule), an OWNERS map, and a
  self-contained maintenance gate. DECISIONS gains a 44-row index and the
  duplicate '## 5.'/'## 6.' became 5b/6b; PROGRESS.md tombstoned (frozen
  2026-07-09 — its header still ordered sessions to fork the record).
- Wave C (docs truth + signposts): README gains 中文速览, a Windows 快速开始
  (installer scripts + choco ffmpeg 6.1.1 provenance + doctor --windows),
  a Windows-real import example, 19 missing command rows (gui/watch/spend/
  perf/evaluate/schema/unpack/... — drift was structurally invisible), the
  --xmeml/--capcut export mentions, three W2/W3/W5 feature one-liners, and
  the stale '14-skill' count replaced by a countless phrase. manju new now
  bridges to cd/status/funnel; the funnel hint names `manju create <stage>`
  (was circular); export --help documents --srt's ride-alongs; the missing-
  ffmpeg hint steers to the PINNED 6.1.1 (ffmpeg.org is 8.x — the exact skew
  class the gate pins); SKILL.md export row + cheat-sheet token pin gain
  --xmeml.
- Wave D (GUI/board/installer): board banner viewport-fixed (failures while
  scrolled deep looked like nothing happened); error toasts sticky with
  click-dismiss (3.6s couldn't be read; successes keep auto-dismiss); HTML
  404 with a way home for browser navigations on both servers (Accept-header
  scoped — API JSON envelopes byte-identical); gc --hard refusal now
  self-explains + names the agent-safe alternative (interactive_only);
  doctor's sync advisory covers Dropbox/坚果云/Nutstore/百度网盘, casefolded
  (was OneDrive-only, case-sensitive); install-manju.ps1 names the failing
  step (Store-alias python probe, venv, pip — each $LASTEXITCODE-checked)
  and the catch prints the log path.
- Wave E (Windows correctness, repo-wide): all 21 subprocess text=True call
  sites missing encoding= now decode UTF-8 with errors=replace (ANSI-codepage
  decode mojibaked tesseract's Chinese OCR → false FAILs on text that IS on
  screen, and garbled ffmpeg stderr tails in errors); a repo-wide source-scan
  pin keeps the class extinct. test_c20b_corpus stops re-running the ~8s
  calibration three times (module fixture reused; ~20s saved per run).
- Also this interval: the ubuntu #226 / Windows #11 verdicts — the test_mcp
  order-dependence flake (made self-contained), the two pack-side casefold
  tests whose colliding tree cannot exist on NTFS (real capability probe),
  the docstring escape-sequence DeprecationWarnings (raw-string). All landed
  before this entry; gates green on a5c2fa3 (#41a).
- Tests: tests/test_ux_polish.py (24 pins, red-first per behavior). Explicitly
  NOT done (recorded): recents in the no-project error and the local_cmd
  8191 preflight (both judged improvement-not-defect / speculative under the
  maintenance gate by the risk lens); wholesale message translation (pinned
  strings, low value against the already-Chinese-first runtime surfaces).

### 42a. UX program close-out: waves F-G + real-browser verification (2026-07-13)

- Wave F: board annotate flow honesty (F16 — '当前帧' reads the OPEN compare
  stack first; annotate updates IN PLACE with the post-write rev refreshed
  across the shot's forms; the active tab survives in the URL hash); the
  board renders the GUI's OWN Chinese state vocabulary with the enum on the
  title attribute (F18 — one state, one name across both review surfaces;
  one pin evolved with recorded intent); `manju import` expands ~/wildcards
  exactly and only when the literal path does not exist (F11 code half — the
  README's flagship line failed on the primary platform); pytest marker
  `ffmpeg` on the 10 WHOLLY-gated render modules — `-m "not ffmpeg"` is the
  honest fast loop (F43, documented in CLAUDE.md).
- Wave G: docs/PINS.md (20 VERIFIED source-scanning pins across 15 test
  files — invariant / test / what-trips-it / sanctioned alternative, with
  the union-batch rule and an honest-evolution footer) + docs/PROJECT_YAML.md
  (Chinese owner reference for all 14 ProjectConfig fields with the
  cache-cold / fps-mirror / declared-colour consequences) + README links
  (F23/F38); the GUI /review side column mirrors the board's media-bound
  annotations read-only — same severity vocabulary, the ONE staleness rule
  (Annotation.matches_media), click-to-seek onto the card player (F17 — a
  blocker pinned to an exact frame used to vanish on the richer page).
- REAL-BROWSER verification (headless Chromium via Playwright, driving live
  gui + board servers on a scaffolded project with real mp4 takes): 15/15
  checks — the F14 two-verdicts-one-card flow, sticky error toasts, HTML
  404s, the fixed banner, in-place annotate with rev refresh, tab
  persistence across reload, and the F17 mirror. The pass CAUGHT one real
  defect HTTP tests could not: the client-side row insert after a take's
  FIRST annotation was a silent no-op (.ann-list only rendered when
  non-empty) — fixed and pinned. This also closes the W3 completion
  report's risk item 2 (the annotate UI had never run in a real browser).
- Disposition at close: all 46 audit findings dispositioned — landed except
  F8/F35 (rejected under the maintenance gate, recorded in #42) and F45
  (no-op bookkeeping). REPORTS/UX_AUDIT_2026-07-13.md is the ranked record.

### 42b. Windows run #20: the undreained-refusal RST class (2026-07-13)

- Run #20 (32e6831): 2 F — two token-guard tests died with WinError 10053.
  Cause: gui/server's do_POST early refusals (host/readonly/token) answered
  WITHOUT reading the request body; Windows RSTs the connection with unread
  bytes and the CLIENT sees ConnectionAbortedError instead of the clean 403
  (Linux lets the buffered response through — 19 green runs hid the race).
  The board server has ALWAYS drained before refusing (_read_body_raw,
  "drain so keep-alive stays sane"); the gui server now applies the same
  discipline via a bounded, best-effort _drain_request_body() at all three
  gates. Pinned with a 1 MB-body refusal test + a gate-structure pin.

## 43. UX round 2: the AI-collaboration surfaces + loop-until-dry close (2026-07-13)

- Round-2 sweep over three FRESH dimensions (MCP server as an agent's
  surface; CLI error-contract consistency; guidance-surface truthfulness),
  every finding live-driven, plus an owner day-one journey (CLI new→import→
  check→build→export→browser over every GUI page collecting JS console
  errors: clean) and a second real-browser pass.
- MCP (7 landed): required arrays now ENFORCED at dispatch; update_shot's
  rollback raises (it returned its error dict and the server stamped
  isError:false — an agent believed a rolled-back write landed); build
  refuses unknown targets (PAID-SAFETY: a typo spent on generation then
  silently skipped the requested phase — the CLI always had this guard, MCP
  was the one open entrance; validated against the tool's own byte-pinned
  enum); events n clamped; ToolError carries a stable code + structured
  payloads; skill_show un-double-quoted; build/redo descriptions state they
  can spend real money.
- CLI error contract (7 landed): waiting_user (the §8.3 spend stop) is
  code-branchable on redo/voice/transcribe/tasks-retry (was code:"error" —
  agents string-matched the prefix); export/qc survive malformed
  timeline.json and migrate a corrupt project.yaml (raw JSONDecodeError/
  ValidationError tracebacks, empty --json stdout — three more editions of
  the status class); _require_shot catches broken shot YAML (select/redo/
  voice/prompt crashed raw); stable codes on the top user-hit refusals;
  build's unknown-target names the valid set; the new-project workflow
  finally writes down the cd step (folded into step 1 — the step-shape pin
  requires manju-only commands, honored).
- Also: python -m manju works (__main__.py); scripts/dev/browser_verify.py
  (15 checks, re-runnable); tests gain the MANJU_PROVIDERS_DIR autouse
  isolation after a real incident (an audit subprocess's stray manifest in
  ~/.manju/providers turned doctor's gating check red suite-wide — the
  suite had only been green because that dir happened to be empty).
- Guidance-surface auditor verdict (loop-until-dry evidence): workflows
  execute as written, status 下一步 correct across all driven states (QC
  errors→repair, no final→build, empty→create), content-key idempotent
  skip holds on real re-builds — the dimension came back essentially DRY
  beyond the cd step; round 3 is not warranted. The UX program's finder
  rounds are closed; the babysit loop (gate watch + fix-on-red) continues.
- Suites at close: 4356 passed / 2 skipped / 0 failed local; both gates
  green through 2cc258a (wave H) and the conftest hardening (3634148)
  verdict pending on the gate at entry-writing time.

## 44. Intuitiveness wave: one question, one answer per shot (2026-07-13)

- The owner's mandate ("make it feel more intuitive") after two dry finder
  rounds → the remaining value was CONCEPTUAL: a shot carries four-plus
  parallel state machines (build state, per-take verdicts, review
  annotations, QC findings, voice) and the owner's real daily task was
  diffing them in their head.
- Landed: `build/status.shot_next_action` — the ONE resolver folding all
  machines into a single actionable sentence per shot (most-blocking first:
  broken→missing→select→blocker→qc→voice→stale→review→ok), always naming
  the exact command and the UNDO (`manju rollback shot`) wherever a choice
  can be wrong; blocker annotations count only when bound to the CURRENT
  selected media (a stale binding never nags). Pure derivation, no new
  state. Surfaced: `manju status` 待办 lines (capped 6; full list in the
  additive `todo` JSON key), gui /review cards, board SERVE cards (the
  static board is the shareable handoff — workbench todo stays off it).
- Also: installer `-CreateShortcut` opt-in (per-user Start-Menu .lnk FILE →
  `manju gui` workspace picker — the click-first daily entry; no registry,
  §4.2 honoured); README 中文速览 names the undo.
- Measured-and-dismissed: /review per-load media hashing (hash_file is
  process-cached — 40MB first hash 38ms, repeat 0ms); cross-server deep
  links between gui and board (two ad-hoc ports — brittle; the resolver's
  hints NAME the other surface instead).
- Rejected-with-reason: converging the two review surfaces into one (a
  structural refactor far past the maintenance gate; vocabulary + mirrors +
  next-action already carry the coherence); redesigning bare `manju` output
  (help panels already group 82 commands sanely).
- tests/test_ux_polish.py grows to 44 pins; full suite 4362/2/0.

## 45. GPT-analysis wave: the two real kernels of an external review (2026-07-14)

- The owner supplied a second AI's product review ("Manju Continuity": six
  P0/P1 programs — ActionSpec registry, project-scoped routing, JobSpec
  resume, Agent Inbox, six-domain IA, change-driven review). Dispositioned
  against the maintenance gate: most of it is framework-scale product
  engineering for a multi-user tool this is not, or already landed in
  #42-#44 in bounded form (its "next_action stable id" ask IS the todo-key
  contract; its "failure center" IS the status 待办 ladder; its "Director
  loop as protocol" stays a page, not a framework). Two kernels were real
  and bounded; both landed here.
- `next_step_key` (from its P0-2): `project_status` now carries a STABLE
  machine token beside the Chinese `next_step` sentence
  (create_shots/build_missing/select/fix_broken/build_timeline/build_final/
  fix_qc/redo_stale/done) — completing the key+text contract the per-shot
  `todo` entries set in #44. Agents and GUIs branch on keys; prose can be
  reworded freely. Additive, mirrored into /api/state.
- Stale-tab project guard (the data-confusion bug inside its P0-3, minus
  the routing rewrite): the workspace server binds ONE switchable project,
  so a tab rendered for 甲 kept resolving /media/ and landing POSTs in
  whatever project any OTHER tab switched to. Now: `gui/state
  .project_identity` (root-derived 12-char token, the one owner — NOT a
  nonce, so same-project tabs never conflict and switching back
  re-validates old tabs); `_send_text` stamps it into every served HTML
  document as a `manju-project` meta anchored on the token meta all ten
  shells already embed (zero per-shell threading, future shells covered);
  common.js / app.js / glossary.js echo it as `X-Manju-Project` on mutating
  POSTs; do_POST refuses a mismatch 409 `{code: project_switched}` naming
  the server's current project (switch/open/new stay exempt — that intent
  is project-independent; header-less clients keep old behaviour). Read
  side: a 15 s constant-time `/api/project-id` watchdog + the SPA's
  /api/state compare overlay the page ("已停止读写 — 刷新跟随当前项目")
  instead of silently repainting as the other project. The SPA adopts the
  new identity ONLY from its own intentional /api/switch response.
- Explicitly rejected from the same document, with reasons: project-scoped
  URL routing rewrite (server is single-owner localhost; the guard closes
  the trust gap at ~1% of the surface churn), ActionSpec code-generation
  layer (three surfaces already share one core; a registry adds a fourth
  representation to keep honest), JobSpec lossless resume/reconciliation
  (paid providers are off by default on this personal setup; jobs.jsonl
  already records interruptions honestly rather than guessing), six-domain
  nav rewrite + command palette, Agent Task Inbox, review baseline
  fingerprints, journey-test framework + local metrics (browser_verify.py
  is the journey harness this repo actually runs).
- 5 new pins in tests/test_ux_polish.py (49 total), red-first proven by
  stash; the stale-tab pin asserts the refused write NEVER lands (selected
  take stays None in the switched-to project).

## 46. Continuity wave: the returning owner's first two questions (2026-07-14)

- The owner re-issued the intuitiveness mandate after #44/#45. The remaining
  gap was TEMPORAL, not spatial: the surfaces answer 现在做什么 (#44) and
  这是哪个项目 (#45), but a returning-after-days owner asks 我上次做到哪了
  and 我的东西备份了没 first — and neither had a surface.
- 上次动作 anchor: `manju status` (the takeover entry point) opens with ONE
  line — "上次动作 3 天前 · select S007 (human)" — from the events tail the
  status payload already carried but never printed. `core/events.humanize_age`
  is the one owner of the phrase (刚刚/N 分钟前/N 小时前/N 天前; unparseable
  or future timestamps → "" — a hand-edited log line never crashes status,
  and time is never guessed). No events → no line, no noise.
- Backup age: `manju pack` records `.manju/last_pack.json`; doctor (the
  health surface, not daily status — no nagging) renders the advisory row:
  ✓ 上次整包备份 X 前 / ⚠ over two weeks → 建议 manju pack / • no record →
  names the command. Never gates doctor's ok.
- The design correction this wave PAID for and pins: the first cut appended
  a "pack" event to events.jsonl — and three W5 pins went red because two
  packs of one tree must stay byte-identical. The deeper contract surfaced:
  **a backup operation must be read-only on the tree it archives.** The
  marker therefore lives in `.manju/` (PACK_EXCLUDE) — disposable by design,
  so a wiped marker degrades toward 建议备份, never toward false confidence
  — and the new pin asserts events.jsonl is byte-untouched across a pack.
- Also verified-then-dropped this round: browser auto-open (already ships in
  gui + board), empty-state sweep (every page already renders a pointered
  empty state from earlier rounds), doctor fix-naming (rows already name
  commands).
- 4 new pins (test_ux_polish.py, 53 total), red-first proven by stash; the
  determinism trio (fp_fixity + fp_bagit ×2) re-verified green.

## 47. Convenience wave 1: type less, never guess (2026-07-14)

- New 7-hour mandate: "more convenient, easier to use". Wave 1 target: the
  single highest-frequency typing surface — shot/take ids, entered dozens
  of times daily in exactly one accepted spelling.
- `cli._resolve_shot_arg` / `cli._resolve_take_arg` (one owner each):
  ``s14``/``S14``/``14`` → S014, ``3`` → take_03 — resolved by matching
  against EXISTING entities only. Exact id → untouched; no match → passes
  through unchanged (creation paths and structured not-founds keep byte-
  level behaviour); MORE than one match → structured bad_args naming every
  candidate (the UNKNOWN-never-guessed discipline applied to intent).
  Resolution echoes on STDERR (`镜头 s1 → S001`) so --json stdout stays
  machine-pure and the canonical form is taught in passing. Wired into 18
  commands (select/redo incl. --shots batch, lock/unlock, voice, prompt,
  impact, repair --shot/--take, align single-shot, board keyframes,
  mentions, refs shot/assign, route/routing explain, bridge run, ingest
  --shot). Batch RANGE specs (align/qc --shots) deliberately untouched.
- `manju select S001` with no take now lists the pickable takes (same
  listing the bad-take branch always had) and teaches `select S001 1`.
- Measured-and-dismissed: CLI startup latency. The observed 4 s --help was
  cold FS cache; warm is 0.84 s (--help) / ~0.4 s (status). The heavy edge
  is pydantic model construction reached through core.container at import
  — needed by nearly every command, so only an invasive lazy-import rewrite
  of the 9k-line cli would shave the remainder. Fails the maintenance gate.
- 6 new pins (test_ux_polish.py, 59 total); the no-match passthrough pin is
  green-by-design pre-implementation (a no-regression pin); the rest proven
  red-first by stash. Full suite 4377/2/0.

## 48. Convenience wave 2: no verb dead-ends (2026-07-14)

- A dedicated friction auditor swept five daily loops with one lens (typing/
  clicks/dead-end outputs); every cited site was personally re-verified
  before landing. The cross-cutting defect: status/new/review already speak
  the next-command idiom, but the GENERATE/EDIT verbs terminated at facts.
- Landed, one clause each: redo (single + batch tail) → `manju select <镜头>
  <数字>`; select → `manju build`; voice (single + batch) → `manju build`;
  align single-shot → `manju build`; align plan-only mode → names `--apply`
  (and `--rows`); transcribe → the verbatim `manju align --media … --from-srt
  …` chain with ACTUAL paths; repair --auto → rerender + `manju failures`
  pointers (suppressed at zero work — no noise); `manju tasks` failed rows →
  verbatim `manju tasks retry <id>` (the unresolved-submission block always
  had its recovery commands; real failures never did); `manju exports`
  缺失/待更新 rows → the ONE command for that kind (final/proxy→build,
  captions/NLE→export flags, cover/teaser→package) — release-level
  next_actions stay the blockers' owner.
- Rejected from the same audit, with reasons: build-failure "re-run build"
  echo (the diagnosis pointer `manju failures` is already printed; the verb
  is self-evident); "build ok" next-step line (状态阶梯的 owner 是 status —
  duplicating it forks the one owner); review 跳过-button tooltip "快捷键 j"
  (the auditor misread — the skip handler IS setActive(+1), exactly what j
  does; the hint is correct); board-serve subtitle panel terminal strings
  (the bounded-board design, not a defect).
- 6 new pins (test_ux_polish.py, 65 total), red-first by stash (the
  zero-work noise guard is green-by-design). Full suite green.
