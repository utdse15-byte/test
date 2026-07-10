# DECISIONS.md — approved deviations from the v2.2 design

Reviewed and approved in review round 1 (§3). Each entry states the design
text, what was actually built, and why the deviation stands.

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

## 5. The GUI ships now, as a pure client (§1-⑦ deferral revisited)

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

## 6. ask_before becomes an engine gate, not just agent discipline (§8.3)

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
