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
