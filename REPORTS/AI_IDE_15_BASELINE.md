# AI_IDE_15 BASELINE — Cloud Visual Reviewer, Perceptual Continuity, Drift & Repair Routing

Date: 2026-07-11
Repo: `utdse15-byte/test` (Manju), branch `claude/cost-optimization-strategy-cjfmn5`
Orient: `git log --oneline` HEAD = `8b0f008 AI_IDE_20A: golden corpus`, `2fb939a AI_IDE_14: qualification ladder`.

This is the WP0 owner audit (contract §4). The controlling discipline: **reviewers only
report Observations, never acceptance; core never imports a vendor SDK / calls a model;
red-first; SQLite-rebuildable; media append-only.** Per the orchestrator addendum every
reviewer lands against the 20A fake reviewer + twin (real cloud VLM is unreachable and is
gated behind AI_IDE_14 qualification with a `REVIEWER_NOT_QUALIFIED` refusal).

## 1. Existing owners to REUSE (audited, will NOT duplicate)

| Contract need (§4) | Existing owner | Verdict |
|---|---|---|
| ExpectationSet | `qc/expectations.py::compile_expectations` (polarity present/absent, spec_hash, digest) | reuse as-is |
| Review Packet v2 | `qc/agent_review.py::_issue_shot_packet` / `_issue_unit_packet` (`PACKET_SCHEMA`) | reuse as-is |
| Verdict/Observation v2 | `qc/agent_review.py` verdict `manju.qc.verdict/v2`: `observations[]` (`present\|absent\|uncertain\|not_evaluated`), `findings[]`, additive `decision`/`observed_states` (08_10_12C Path A) | **extend additively** (§6 dimension observations) |
| Assurance (acceptance authority) | `qc/assurance.py::compute_assurance` + pure `diff()` | reuse, **pin from reviewer direction** |
| consistency brief/coverage | `qc/agent_review.py::qc_brief(mode="consistency")`, `qc_coverage` | reuse as-is |
| accepted observed state | `qc/production.py::accepted_observed_state` | reuse as-is |
| candidate family / keeper currency | `qc/production.py::candidate_families` (`_live_failures` binding recheck) | reuse as-is |
| repair route (dispositions) | `qc/production.py::_REPAIR_ROUTES` (5 dispositions KEEP/FIX_IN_POST/EDIT_DONT_REGENERATE/REROLL/REWRITE_SOURCE) | **extend** to the §8 7-route vocabulary |
| qc_vision Provider manifest | `qc/content.py::vision_provider_id` (`type: vision` slot via `MANJU_PROVIDERS_DIR`) | reuse as the gate input |
| Attempt/Submission evidence | `build/attempts.py`, `providers/submission.py`, cost estimators `build/spend.py` | reuse as-is |
| Qualification ladder (14) | `providers/qualification.py::qualification_state` / `declared_facts` / `qualification_matrix` (UNTESTED→…→PRODUCTION_READY) | reuse as the `REVIEWER_NOT_QUALIFIED` gate input |
| Frame extraction cache | `media/frames.py::extract_frame` (content-addressed `.manju/frames`, clamp-before-key) | reuse, **extend** with a pure plan |
| Repair-op path (color adoption) | `media/repair_ops.py` (append-only new-take ffmpeg ops) | reuse as the color-adoption pointer |
| Golden corpus + fake reviewer | `tests/fixtures/golden/` (31 cases, `FakeVisionReviewer` primary + twin) | reuse as fixtures (never modified) |

Conclusion: the current schema already carries reviewer/model/rubric/ref-set bindings
(packet media sha + spec_hash + expectation_digest + reviewer.profile_digest; qualification
staleness anchors). **No parallel schema is introduced** (§4 requirement met).

## 2. Confirmed gaps (what AI_IDE_15 adds)

1. **§6 dimension observations** — the verdict carries per-*expectation* observations
   (`present/absent`) but not the §6 per-*dimension* observation
   (`dimension` ∈ 11 reviewer dims, `subject_ref`, `observed` ∈ `match|mismatch|uncertain|not_visible`,
   `severity` ∈ `info|warning|blocker`, `confidence`, `suggested_repair_variable`). Additive on
   verdict v2 (Path A), validated in the SAME zero-write intake path.
2. **Reviewer qualification gate** — no `REVIEWER_NOT_QUALIFIED` refusal exists; a review
   dispatched to a vision provider below `DRY_RUN_VALID` must be refused (structured). The
   offline fake double drives `record_verdicts` directly and never consults the gate.
3. **Deterministic frame plan (§5 WP1)** — `_shot_frames` extracts first/mid/last only; the
   contract wants a repeatable first/25/50/75/last plan keyed by duration/fps/hash. Pure
   `frame_plan()` added; extraction stays the existing `.manju/frames` cache (no 2nd cache).
   Scene-change slots: **SKIPPED_WITH_EVIDENCE** — core has only black/freeze detectors, no
   scene-detection tooling, and the addendum forbids adding scdet in this batch.
4. **Multi-reviewer disagreement view (§7)** — records already carry `reviewer{name,profile_digest}`,
   but no derived agreement view exists; disagreement on any blocker-severity dimension ⇒
   `UNKNOWN_REVIEWER_DISAGREEMENT` (never majority-vote a blocker away); human adjudication
   appended, originals kept.
5. **Drift trend + repair routes (§8)** — no dimension × shot-order matrix or 7-route proposal
   view. Routes **extend** the 5-disposition mapping; `REGENERATE_REFERENCE` + `RESHOOT` are
   route-level additions, NOT new verdict dispositions.
6. **Deterministic color analysis (§10 WP6)** — no color module. New pure `qc/colorstats.py`:
   PIL histogram/luma/white-balance + ffprobe color metadata (space/transfer/matrix/range;
   unknown ⇒ `UNKNOWN`), hash-bound reference compare, read-only single-variable suggestions;
   adopted corrections route to the existing append-only repair-op path. **numpy is absent in
   this environment — the module uses PIL only** (histograms via `Image.histogram()`).
7. **First-frame candidate ranking (§9 WP5)** — a candidate-hash-bound ranking view. Assessed
   as **conditional**; 16 owns keyframes, and the current keyframe-candidate machinery is not
   present, so this is **SKIPPED_WITH_EVIDENCE** (candidate_families groups takes but is not a
   first-frame keyframe candidate set).

## 3. Environment-impossible rows (§12 / §13) → SKIPPED_WITH_EVIDENCE

* Real cloud VLM cost / latency / round-trip, PRODUCTION_READY reviewer — no real account is
  reachable here. Stand-in: the `REVIEWER_NOT_QUALIFIED` qualification-gate refusal test
  (addendum ruling 10). Core never imports a vendor SDK (§2/§13 stop condition honored).
* Scene-change frame slots — no scene detector in core (only black/freeze). Recorded, not faked.

## 4. Planned file budget (≤8 production files)

1. `media/frames.py` — pure `frame_plan` (WP1).
2. `qc/agent_review.py` — §6 dimension observations intake (additive) + `reviewer_admission` gate + brief wiring.
3. `qc/production.py` — reviewer-agreement view, drift trend, 7-route proposal (extends repair mapping).
4. `qc/colorstats.py` — NEW pure color module (WP6).

`assurance.py` is pinned from the reviewer direction by TEST only (no code change needed —
it stays the untouched acceptance authority). `cli.py`, `build/promptlab.py` untouched.
Tests: `tests/test_c15_reviewer.py`, `tests/test_c15_color.py` (corpus cases are the fixtures;
`DECISIONS.md` / `README.md` / `tests/fixtures/golden/` untouched).

## 5. Baseline suite state

`python -m pytest tests/test_c20a_corpus.py tests/test_qc_agent.py -q` → **59 passed** (green
starting point for the modules being extended).
