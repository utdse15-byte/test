# AI_IDE_16 COMPLETION — Preview Ladder, Previs & Storyboard Round-trip

Date: 2026-07-11
Branch: `claude/cost-optimization-strategy-cjfmn5` (no commit/push — working tree only)
Discipline held: the ladder is DERIVED, never stored; approvals ride EXISTING adoption
facts (no second storyboard truth); previews are append-only media / derived views;
core calls no LLM/VLM; red-first; SQLite-rebuildable; old projects byte-identical.

## (a) WP0 audit — existed vs extended

Every §3–§9 need already had an owner and was REUSED, not duplicated (full table in the
BASELINE). Reused as-is: `render_audition`/`ensure_slate`/`kenburns`/`render_timeline`
(animatic), `Project.takes`+`MEDIA_EXTS` (image vs video takes), `candidate_families` +
take sidecars (candidate provenance), `REF_TRANSFER_VOCAB` (`style`/`color_grade`/`motion`
— no new tokens), `resolve_local_ref` (motion-ref containment), `spend_gate`/ask_before
site + DR05 `mcp/policy` profile (the gate), `build_shot_import_plan`/`apply_shot_import_plan`
(pull-sheet creates), `checked_shot_write`+`shot_text_hash` (pull-sheet edits),
`TimelineRules.transition_overrides`→compiler→`transition_out` (transition source),
`_estimate_shot_cost` (next-step cost). No parallel schema, no second storyboard truth,
no free-node runtime, no 3D engine.

## (b) Ladder derivation + adoption semantics

`qc/production.ladder_view(project, shot)` — a pure `manju.preview-ladder/v1` projection
(never stored, `do_not_execute_automatically: true`). Each §3 rung's `reached` is a fact
over EXISTING truth: SCRIPT (shot loads), AUDITION (`renders/audition/*`), KEYFRAME (image
takes exist), BOARD (`board.html`/contact sheet), ANIMATIC (`renders/animatic/*`),
MOTION_REF (a video ref with `controls=[motion]`), FINAL_VIDEO (a video take); `stage` =
highest reached. **Keyframe candidates = IMAGE takes** (`.png/.jpg/.jpeg`, via
`Project.takes` — video>image priority) through the existing generate path; their sidecars
already carry request/ref/provider/cost. **Adoption** (`keyframe_adoption`) rides the
EXISTING facts, no new flag: (a) the shot's `selected_take` is a keyframe image take, OR
(b) a resolved refs binding references a candidate's EXACT media bytes (content-hash join
→ a promoted ref / canonical style frame). A style ref to DIFFERENT bytes is not adoption
(exact-bytes, never coincidence). `motion_references` derives external motion refs from the
existing binding (`controls=[motion]`, `must_not_transfer` = `ignore`) — no new vocabulary.

## (c) Spend gate exact mechanics + transport-0 proof

The teeth live at the build dispatch, beside the ask_before gate in `_run_build_phases`
(`build/graph.py`). `_keyframe_gated_video_shots(project, gen)` is a SHOT-level predicate:
a shot is gated iff it has keyframe candidates but NO adopted one, has no video take yet,
and its video generation is PRICED > 0 (a free local plan is not a §10 paid request; a shot
with no candidates never appears — opt-in per shot). Then:
* **UNATTENDED** (`agent_profile="unattended"`): a STRUCTURED `KEYFRAME_NOT_ADOPTED` refusal
  returned BEFORE the generate phase — independent of `assume_yes`, so a confirmed director
  proposal still cannot let unattended automation skip the ladder. **Transport 0**, pinned
  by a `registry.generate_with_fallback` spy that never moves (it blocks the WHOLE paid
  build, so a plain sibling shot's spend is stopped too).
* **COLLABORATIVE** (default): an advisory warning in `result.warnings` + the dry-run plan
  row flag, and a `KEYFRAME_NOT_ADOPTED` **advisory** production check in `manju prompt
  --check` (level `advisory`, NOT in `FAIL_LEVELS`) — never a hard block for a human.
The live DR05 profile is threaded server-flag→`call_tool`→(`_h_build`/`_h_director_execute`)
→`director.execute`→`run_build(agent_profile=…)` — never read from agent `args`. Collaborative
threading is byte-identical to every existing caller (the profile kwarg is passed only when
non-default). **§5 pin**: a keyframe (image) candidate is NEVER auto-adopted — the build
auto-select now skips image takes; adoption is explicit (select or promote-to-ref). **i2v
pin**: once adopted (first-frame ref), the video request's `primary_image` is the EXACT
adopted media bytes (hash-equal).

## (d) Animatic / board / SVG facts

**Animatic** — `manju build --target animatic` (validated in CLI + `run_build`), a voice-only
plan (never plans/prices paid video). It compiles the timeline IN MEMORY with
`allow_missing_takes` (never writes `timeline.json`), then `_render_animatic` builds each
panel from the shot's best keyframe still (adopted keyframe > first candidate image take >
authored `keyframes[].image` > slate) via `kenburns` pan/hold, reusing `render_timeline` +
audio/captions, to `renders/animatic/animatic_vN.mp4`. It is a DERIVED artifact —
content-keyed (idempotent), **never a video take, never selected** (pinned), deletable +
rebuildable. **Board** (serve-mode only, static `board.html` bytes untouched — its pin holds):
`_ladder_chips` renders the derived ladder stage, keyframe-approval status, and predicted
next→video cost (existing estimator). `blocking_svg(shot)` is a PURE, deterministic 2D
blocking diagram (camera frame + subject box sized by `shot_size` + angle shift + movement
arrow + action line) — no 3D, no canvas truth; same shot → same bytes.

## (e) Pull-sheet round-trip mapping onto ShotDraft

`build/pullsheet.py`. **EXPORT** — `export_pull_sheet` writes `exports/pullsheet/<name>.csv`
+ `.md` (pure `compile_pull_sheet_csv`/`_md`, stdlib `csv` + hand-built MD table, escaped
pipes) derived from the compiled timeline + shots: shot id, duration, frame refs, camera,
action, dialogue, transition, refs, quality, status. Wired as `manju export --pullsheet`
(loads-or-compiles, works pre-build; PDF SKIPPED_WITH_EVIDENCE). **IMPORT** — `manju
pull-sheet <file> [--apply]`. `plan_pull_sheet_import` is ZERO-WRITE inspect: it diffs the
edited sheet and produces a shot-package-style proposal — a NEW shot row → a
`manju.shot-draft-package/v1` create op routed through the DR03A `build_shot_import_plan`;
an EDITED existing shot → a `checked_shot_write` CAS proposal carrying the reviewed values +
`expected_text_hash`. `apply_pull_sheet_import` is the CAS apply: creates via
`apply_shot_import_plan`, edits via `checked_shot_write`. A STALE source (moved since
inspect) fails the CAS; a locked field blocks; `selected_take`, media, transition, refs and
status are NEVER written back (transition/refs/frame_refs/status are export-only display).

## (f) Files + deltas vs ≤8

Production files touched: **8 / 8**.
1. `src/manju/qc/production.py` — `ladder_view`, `keyframe_candidates`/`video_takes`,
   `keyframe_adoption`, `motion_references`, `keyframe_gate`/`keyframe_gated_shots`,
   `keyframe_ladder_checks` (+ the `manju.preview-ladder/v1` constants).
2. `src/manju/qc/prompt_checks.py` — join `keyframe_ladder_checks` into `production_checks`
   (isolated; the continuation gate above is unchanged).
3. `src/manju/build/graph.py` — the keyframe spend gate (`_keyframe_gated_video_shots` +
   the collaborative warning + the unattended refusal), `agent_profile` param, the
   `--target animatic` branches + `_render_animatic`/`_animatic_shot_still`, and the §5
   auto-select image-skip.
4. `src/manju/build/director.py` — thread `agent_profile` through `execute`→`_run_action`→
   `_do_build`→`run_build` (non-default only; collaborative byte-identical).
5. `src/manju/mcp/tools.py` — thread the live profile to `_h_build`/`_h_director_execute`.
6. `src/manju/board/board.py` — `blocking_svg` + `_ladder_chips` (serve-mode wiring).
7. `src/manju/build/pullsheet.py` — NEW (export + import).
8. `src/manju/cli.py` — `--target animatic`, `export --pullsheet`, `pull-sheet` command.

`DECISIONS.md` / `README.md` / `tests/fixtures/` / `tests/golden/` untouched. Tests added:
`tests/test_c16_{ladder,spend_gate,animatic,board,pullsheet,transitions}.py` (51 tests).

## (g) Pins flipped (re-proven this batch)

* Ladder is DERIVED, never stored (`ladder_view` pure; `do_not_execute_automatically`).
* Approval rides EXISTING facts only — a selected keyframe OR a promoted refs binding to the
  EXACT bytes; a different-bytes style ref is NOT adoption.
* Unattended paid video for an unadopted keyframe → structured refusal, **transport 0**
  (spy pinned), even under `assume_yes`; collaborative is advisory-only, never a hard block.
* A keyframe (image) candidate is NEVER auto-adopted as `selected_take` (§5).
* Adopted keyframe → i2v request carries the SAME exact media bytes.
* Animatic is a derived artifact — never a video take, never selected, deletable+rebuildable.
* Blocking SVG is a pure derivation (no 3D / canvas truth); static board bytes unchanged.
* Pull-sheet import is proposal-only (zero-write inspect); stale source fails CAS; locks and
  `selected_take`/media never overwritten.
* A transition choice is a TIMELINE SOURCE fact (rules.yaml → compiler → `transition_out`);
  a generative bridge is proposal-only, transport 0 this batch.
* No keyframe candidates ⇒ ladder inert, no warning, no check (old projects byte-identical).

## (h) Suite results

New: `tests/test_c16_ladder.py` (15) + `_spend_gate.py` (10) + `_animatic.py` (5) +
`_board.py` (7) + `_pullsheet.py` (11) + `_transitions.py` (3) = **51 passed**.
Touched-adjacent regression (dr03a/dr03c/dr05/export/compiler/boards/board_serve/c07/c13/c14/
c15/c20a/c081012/locks/write_locks/write_consistency/ask_before/director/batch/voice/
evaluate/mcp/prompt/promptlab) — green.
Full run in two synchronous halves (`python -m pytest -q -p no:cacheprovider`):
**1474 passed, 5 skipped** + **1475 passed, 7 skipped** = **2949 passed, 12 skipped, 0
failed** (exit 0). The 12 skips are the pre-existing environment skips (ffmpeg/optional-tool
gated), none introduced here; zero failures, zero regressions.

## (i) Deviations

1. **Spend gate is SHOT-level, not plan-level.** An unadopted keyframe leaves the shot
   `NEEDS_SELECTION` (an image take is present but is not a video deliverable), so it is not
   in the auto-generate plan. The gate therefore prices the pending video directly and, under
   unattended, blocks the whole paid build — the faithful "must not submit video while a
   keyframe is unapproved". Collaborative stays advisory (the eventual compile failure for a
   shot with no video is a separate, legitimate "needs video" failure, not the gate).
2. **§5 auto-select image-skip.** Required by the contract ("不得自动把候选设为 selected") but a
   behavior change surfaced by the tests: the build auto-select now skips IMAGE takes, so a
   keyframe candidate is never auto-adopted. Full suite green.
3. **Animatic render inlined in `build/graph.py`** (not a new `media/` module) to hold the
   ≤8 production-file budget — it reuses the kenburns + `render_timeline` helpers; the
   `--target animatic` plumbing is the natural home (audition is the precedent).
4. **PDF pull sheet → SKIPPED_WITH_EVIDENCE** (no headless-Chromium / PDF-table path this
   environment; addendum forbids adding one). CSV + Markdown + existing JSON cover export.
5. **Sound bridge / J-L cut audio model → SKIPPED_WITH_EVIDENCE** (not expressible in the
   timeline model today; AI_IDE_18 extends audio). This batch verifies + pins the
   transition-source path and generative-bridge-is-proposal-only (transport 0).

## (j) REPORTS paths

* `REPORTS/AI_IDE_16_BASELINE.md`
* `REPORTS/AI_IDE_16_COMPLETION.md`
