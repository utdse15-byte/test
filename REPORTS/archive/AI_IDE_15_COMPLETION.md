# AI_IDE_15 COMPLETION — Cloud Visual Reviewer, Perceptual Continuity, Drift & Repair Routing

Date: 2026-07-11
Branch: `claude/cost-optimization-strategy-cjfmn5` (no commit/push — working tree only)
Discipline held: reviewers report Observations only (never acceptance); core imports no vendor
SDK / calls no model; red-first; append-only; SQLite-rebuildable.

## (a) WP0 audit — existed vs extended

Every §4 owner already existed and was REUSED, not duplicated (full table in the BASELINE):
ExpectationSet, Review Packet v2, Verdict/Observation v2 (with the 08_10_12C `decision` /
`observed_states` additive precedent), Assurance (`compute_assurance` + pure `diff`),
consistency brief/coverage, `accepted_observed_state`, `candidate_families` keeper currency,
the 5-disposition `_REPAIR_ROUTES`, the `qc_vision` `type: vision` slot, attempt/submission
evidence + `build.spend` estimators, and AI_IDE_14's qualification ladder. The current schema
already carries reviewer/model/rubric/ref-set bindings, so **no parallel schema was added**.
Extended (not duplicated): verdict v2 (+§6 dimension observations), the repair mapping (+7-route
vocabulary), the frame cache (+pure plan), the qualification ladder (+reviewer-admission gate).

## (b) Observation contract fields landed + intake validation

New OPTIONAL verdict-v2 field `dimension_observations[]` (Path A additive, byte-compatible with
every legacy payload), each item: `dimension` (∈ the 11 §3 reviewer dimensions
`REVIEWER_DIMENSIONS`), `subject_ref`, `observed` (∈ `DIMENSION_OBSERVED` =
`match|mismatch|uncertain|not_visible`), `severity` (∈ `DIMENSION_SEVERITY` =
`info|warning|blocker` or absent), `confidence` (0..1), `evidence_refs`, `explanation`,
`suggested_repair_variable` (∈ the existing `REPAIR_VARIABLES`). Validated in the SAME
`_prepare_v2_record` zero-write path as `decision`/`observed_states`: any bad enum / out-of-range
confidence / unsafe evidence_ref (traversal, absolute, NUL) / embedded secret ⇒ **whole-batch
reject, zero writes**. Stored verbatim on the record. The reviewer still never writes accepted —
proven from the reviewer direction: a verdict carrying only `dimension_observations` (even all
`match`, `confidence=1.0`) leaves assurance `unknown`, never `accepted`; `qc/assurance.py` is
untouched and stays the pure acceptance authority.

## (c) Frame plan semantics (§5 WP1)

`media.frames.frame_plan(duration_ms, fps, media_hash)` — a PURE first/25/50/75/last plan.
Positions are a function of duration alone; `fps` only sets the last EXTRACTABLE frame
(`duration - one frame`) so `last` is a real frame matching `extract_frame`'s clamp. `plan_digest`
folds in `media_hash` (a regenerated take ⇒ distinct plan identity). Unknown/zero duration
degrades to a single `first@0` (never an invented mid/last). Extraction stays the EXISTING
content-addressed `.manju/frames` cache (`extract_frame`) — a plan, not a second cache; proven by
a content-addressed re-extract hitting the same file. Wired additively into `qc_brief` as
`row["frame_plan"]` (legacy `frames` first/mid/last untouched). **Scene-change slots:
SKIPPED_WITH_EVIDENCE** — core has only black/freeze detectors, no scene-change detector, and this
batch does not add scdet (addendum ruling 2); recorded in the plan, never faked.

## (d) Multi-reviewer / disagreement design (§7)

`qc.production.reviewer_agreement(project, shot, *, current_reviewer_digest=None)` — a derived
view over each reviewer's current-bound §6 dimension observations (`reviewer{name,profile_digest}`
already rides every record). Per-dimension agreement is computed; **disagreement on any
blocker-severity dimension ⇒ state `UNKNOWN_REVIEWER_DISAGREEMENT`** — a blocker is never
majority-voted away. Human adjudication is an APPENDED verdict (`actor="human"` /
`reviewer.kind="human"`), listed under `adjudications`; the original reviewer records are always
kept (log is append-only). Primary-vs-twin on the identity-blocker corpus case yields
`UNKNOWN_REVIEWER_DISAGREEMENT`; both-agree yields `AGREEMENT`.

## (e) Drift / route view (§8)

`drift_trend(project, shots=None)` — a dimension × shot-order matrix over **current-bound** §6
dimension observations (`_live_failures` drops any record whose media/spec/expectations moved), so
a swapped take's stale drift never surfaces. `repair_routes(...)` — minimal executable proposals
in the **7-route vocabulary** `SEVEN_ROUTES`; each declares `primary_variable` (the reviewer's
§9.3 one variable), `estimated_cost` (`class: generation|local` — generation routes price through
the existing `build.spend` estimator at the human-gated spend step; nothing here spends),
`affected_shots`, `requires_confirmation: true`, `do_not_execute_automatically: true`. **Routes
EXTEND the 5-disposition mapping**: `DISPOSITION_TO_ROUTE` maps KEEP→ACCEPT_DEVIATION + the other
four 1:1; `ROUTE_LEVEL_ADDITIONS = (REGENERATE_REFERENCE, RESHOOT)` are route-level ONLY — asserted
NOT present in `agent_review.DISPOSITIONS` (a reviewer can never emit them as a disposition).

## (f) Color module facts (§10 WP6)

NEW pure `qc/colorstats.py` (**PIL-only — numpy is absent in this environment**; integer channel
histograms, no float-accumulation drift):
* `color_stats(image)` — histogram / channel means / Rec.709 luma / white-balance, bound to
  `input_sha256`; deterministic; a warm relight vs a cool base separate measurably.
* `color_metadata(clip)` — ffprobe `space/transfer/matrix/range` (`matrix` ← ffprobe `color_space`,
  the YCbCr coefficients). Unidentifiable ⇒ `UNKNOWN`, never guessed; a still-image frame carries
  no video color-continuity metadata so every field is `UNKNOWN` (probe the real clip); a tagged
  bt709/tv clip reads back its tags; an unreadable file ⇒ all `UNKNOWN`.
* `compare_to_reference(cur, ref)` — bound to BOTH content hashes; READ-ONLY single-variable
  suggestions from `COLOR_VARIABLES` (`lut/exposure/white_balance/curve/shot_match`); identical
  frames ⇒ `match: true, suggestions: []`. `adopt_via` points at the EXISTING append-only
  repair-op path (`append_only: true, overwrites_source: false`, both hashes bound), and reminds
  to re-run character/text/flicker/delivery QC after a grade.
* `histogram_match_preview(src, ref)` — proves the pin: `is_preview/mutates_source=false/
  mutates_downstream_request=false`; it reads bytes only, so the source hash is provably unchanged
  before/after.

## (g) Files + deltas vs ≤8 budget

Production files touched: **5 of 8**.
1. `src/manju/media/frames.py` — pure `frame_plan` + `FRAME_PLAN_LABELS` (WP1).
2. `src/manju/qc/agent_review.py` — §6 `dimension_observations` intake (additive) + `_shot_frame_plan`
   brief wiring + the 11-dimension / §6 enum constants.
3. `src/manju/qc/production.py` — `drift_trend`, `repair_routes`, `reviewer_agreement`,
   `verdict_reviewer_current`, the 7-route vocabulary extending the disposition mapping.
4. `src/manju/qc/colorstats.py` — NEW pure color module.
5. `src/manju/providers/qualification.py` — `reviewer_admission` / `reviewer_admission_from_state`
   gate (moved here from qc, see (j): the qc build-boundary guard forbids qc↔qualification coupling;
   admission is a provider-layer concern reusing the local ladder).

`qc/assurance.py`, `cli.py`, `build/promptlab.py`, `media/repair_ops.py` untouched.
`DECISIONS.md` / `README.md` / `tests/fixtures/golden/` untouched. Tests added:
`tests/test_c15_reviewer.py`, `tests/test_c15_color.py` (corpus cases are the fixtures).

## (h) Pins flipped (re-proven this batch)

* Reviewer NEVER writes accepted — pinned from the reviewer direction (dimension-observation /
  high-confidence match ⇒ still `unknown`; assurance schema untouched).
* Histogram preview / color analysis changes NO source or downstream request digest (append-only).
* Color adoption is append-only, binds input + reference hash, never overwrites the take.
* Drift derives from current-bound evidence ONLY (a moved binding drops the observation).
* `REGENERATE_REFERENCE` / `RESHOOT` are route-level, NOT verdict dispositions.
* Admission opens no transport / imports no vendor SDK (a scripted `default_transport` that raises
  is never reached).

## (i) Suite results

New tests: `tests/test_c15_reviewer.py` (28) + `tests/test_c15_color.py` (9) = **37 passed**.
Touched-module regression set (`test_c20a_corpus`, `test_qc_agent`, `test_c14_qualification`,
`test_round_t`) — green, including the AI_IDE_14 build-boundary guard.
Full run (`python -m pytest -q`): **2898 passed, 12 skipped in 890.9s** (exit 0). The 12
skips are pre-existing environment skips (ffmpeg-gated / optional-tool rows), none introduced
by this batch; zero failures, zero regressions.

## (j) Deviations

1. **Admission gate home.** The addendum's file budget pencilled the gate into `qc/agent_review.py`,
   but AI_IDE_14's `test_15_qualification_report_is_not_a_build_input` guard forbids the `qc`
   package from importing qualification (it is a deletable evidence projection, not a build input).
   Admission is genuinely a provider-layer concern, so `reviewer_admission` lives in
   `providers/qualification.py` (reusing the local ladder, zero new imports). qc stays decoupled;
   the §6 verdict constants remain in `agent_review.py`. Net effect on the budget: still 5 ≤ 8 files.
2. **numpy absent** ⇒ `colorstats.py` is PIL-only (the addendum said "PIL/numpy"). PIL integer
   histograms are exact and deterministic; no capability lost.
3. **WP5 first-frame candidate ranking → SKIPPED_WITH_EVIDENCE.** Conditional in the addendum
   (ruling 6); 16 owns keyframes and no first-frame keyframe-candidate machinery exists to bind
   candidate hashes (candidate_families groups takes, not first-frame candidates). Not implemented;
   recorded, not faked.
4. **Real cloud VLM cost/latency/round-trip → SKIPPED_WITH_EVIDENCE** (environment-impossible); the
   `REVIEWER_NOT_QUALIFIED` refusal (fabricated unqualified vision manifest + the pure predicate
   over both branches) is its stand-in (addendum ruling 10).
5. **Scene-change frame slots → SKIPPED_WITH_EVIDENCE** (no scene detector in core; scdet out of
   scope this batch).

## (k) REPORTS paths

* `REPORTS/AI_IDE_15_BASELINE.md`
* `REPORTS/AI_IDE_15_COMPLETION.md`
