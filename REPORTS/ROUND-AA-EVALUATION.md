# ROUND-AA-EVALUATION — grounding the honest skill/workflow evaluation layer

Date: 2026-07-08. Round AA research deliverable (agent AA5), goal item 8. Method:
three spot-check web searches against the specific claims below (Frame.io review/
approval workflow, Kitsu/ShotGrid shot-status tracking, editorial revision-count
metrics, OSS creative-tool reuse signals) plus the session's own reasoning about
what `events.jsonl` + `reports/qc_agent.jsonl` can and cannot support. Every
concrete claim below carries an inline source URL; anything not pinned to a
primary/secondary source is marked **(reasoned, not sourced)**. This is a much
smaller research pass than ROUND-U/ROUND-V — the goal here is to justify FOUR
shipped signals and their honesty caveats, not survey a market.

Companion to `src/manju/core/evaluate.py` (the shipped module) and
`src/manju/qc/agent_review.py` (whose docstring already cites "Netflix severity
tiers (§6a)" for the blocker/issue/fyi vocabulary this report's QC signal reuses).

---

## 0. The trap this report is designed to avoid

Every "does our tooling help?" question tempts a team into inventing a number
that *sounds* rigorous but isn't: "skills saved 3.2 hours/video", "AI QC caught
40% of issues before a human would have". Manju has no way to know either of
those things — it has no baseline (a video made *without* a skill to compare
against), no wall-clock instrumentation, and no ground truth for "would a human
have caught this anyway". Fabricating such a number would be worse than shipping
nothing, because it would look authoritative while being unfalsifiable. So
`core/evaluate.py` deliberately ships **usage counts and event tallies only** —
things a log file can prove — and a REQUIRED `honesty` section that says, in
Chinese (the report's primary audience), exactly what those counts cannot mean.

The four shipped signal families (skill usage, rework hotspots, QC verdict
tally, funnel scaffold coverage) were each chosen because a real, load-bearing
part of the professional video/creative-tooling industry already tracks the
same *kind* of signal — for the same reason we do: it's cheap, honest, and
directionally useful without claiming causality.

---

## 1. Signal → real-world practice → what would falsify it

| Shipped metric (`core/evaluate.py`) | Real-world practice it mirrors | What would falsify "this metric is useful" |
|---|---|---|
| **Skill usage count + `via` breakdown** (`skills.usage[].count/by_via`, `never_used`) | Plugin/extension **install & reuse counts** as an adoption proxy — Blender's own extension ecosystem moved from ad-hoc addon lists to a searchable **Extensions Platform** (extensions.blender.org) precisely so reuse could be seen; BlenderMarket ranks its top add-ons by all-time "sales" count as the closest thing to a popularity signal a marketplace without telemetry can produce ([Blender Extensions Platform](https://code.blender.org/2022/10/blender-extensions-platform/); [Top Ten Most Popular Blender Add-Ons](https://brandon3d.com/top-ten-blender-add-ons-by-popularity/)). Cursor's own community guidance for `.cursor/rules` — already cited in ROUND-V-REFERENCES-1 §1b — makes the same move explicitly: *"when a rule hasn't fired in weeks, delete it."* | If `never_used` skills turn out to be used anyway through a channel evaluate() can't see (a human copy-pasting SKILL.md content by hand, an agent reading the file directly off disk without going through `skills show`/MCP/GUI), the count undercounts real usage and the "never used" label is a false negative — this is explicitly called out in the honesty section ("只统计被记录的操作"). |
| **Redo / repair rework hotspots** (`workflow.redo`, `workflow.repair`, per-shot top-N) | **Revision-count / iteration-count** as a post-production efficiency metric: editorial workflow guidance treats "number of revisions" as a standard signal, explicitly warning that a high count "could indicate various issues like challenging topics, indecisive teams, or weak ideas" rather than a single root cause ([Editorial Workflow Optimization: Key Metrics](https://www.ndevr.io/blog/editorial-workflow-optimization-metrics-strategies/)). The same source-agnostic-cause caveat is exactly why Manju's hotspot rows carry `low_n` flags instead of a confident ranking. | If a shot with a high redo/repair count turns out to be high for a reason external to the tooling — e.g. a director changing their mind three times, or a single noisy actor re-running the same take for a demo — the "hotspot" signal is describing a person's indecision, not a defect in Manju's skill/funnel guidance. This is exactly the ambiguity the cited editorial-metrics source names, and exactly why `evaluate()` never labels a hotspot "a problem" — only "N events happened here". |
| **QC verdict blocker/issue/fyi tally** (`qc.by_level`, `qc.verdicts_total`) | **Review/approval-cycle tracking** in professional review tools: Frame.io's review-and-approval workflow is built around "reusable approval templates" with reviewers who comment and approvers who "must make a decision to move the approval process forward" — i.e. review activity is counted and staged, not scored ([Frame.io Workflow Management](https://frame.io/features/workflow-management); [Frame.io V4](https://blog.adobe.com/en/publish/2024/10/14/frameio-v4-the-fully-reimagined-platform-is-now-available-for-all)). Manju's own severity vocabulary (blocker/issue/fyi) is already modeled on Netflix's content-QC severity tiers per `qc/agent_review.py`'s own docstring — this report just adds the *tally*, not a new taxonomy. | If two projects with identical `by_level` tallies ship at very different actual quality (one team is stricter about calling something a "blocker" than another), the tally is measuring reviewer calibration, not footage quality — which is precisely why the honesty section says "不能跨项目比较优劣". |
| **Funnel scaffold coverage** (`workflow.funnel.scaffold_events`, `never_scaffolded`) | **Shot/task status-flow tracking** in production pipelines: Kitsu and ShotGrid (Flow Production Tracking) exist specifically to show *where* a shot/task sits in a pipeline — not to prove the pipeline is efficient — via a shared task-status vocabulary all departments read the same way ([CGWire — Kitsu vs ShotGrid](https://www.cg-wire.com/shotgrid-alternative/); [CGWire — Kitsu](https://www.cg-wire.com/kitsu/)). Manju's `funnel_scaffold` events are the same idea at a much smaller scale: which of the three template-backed stages (brief/synopsis/beats) a human actually used the assisted scaffold for. | If most real projects hand-write `story/brief.md` without ever running `manju create brief` (a human just opens the file in an editor), `never_scaffolded` will read "never applied" for stages that are, in truth, done — this is exactly why `_funnel_section`'s `note` field says completion for script/storyboard/plan/produce is **not derivable from the log at all**, and even for the three scaffoldable stages, the signal only proves "the assisted path wasn't used", not "the stage wasn't done". |

**Explicitly not modeled**: time-to-completion, cost-per-video, human labor
hours, or any before/after comparison. The post-editing/machine-translation
productivity-measurement literature — the closest academic analogue to "does
assistive tooling save time" — treats this as a genuinely hard, actively
researched problem even with controlled experiments and paid annotators
(e.g. the comparison-of-post-editing-effort-metrics literature surveyed in
["What Do You Say? Comparison of Metrics for Post-editing Effort"](https://www.researchgate.net/publication/353505123_What_Do_You_Say_Comparison_of_Metrics_for_Post-editing_Effort)
and ["Towards Predicting Post-Editing Productivity"](https://www.researchgate.net/publication/220418997_Towards_predicting_post-editing_productivity)).
If that field — with A/B cohorts and keystroke logging — still argues about
which time-based metric is trustworthy, an append-only event log with no
control group has no business inventing one. **(reasoned from sourced
literature, not a direct claim about video production specifically)**

---

## 2. Why "review-cycle counts" and not "approval speed"

Frame.io's own pitch is telling: it replaced "vague emails, inaccurate
timecodes, scattered approvals, and confusing version control" with countable,
structured events (a comment, a version, an approval decision) — but the
product markets itself on visibility and organization, not on a productivity
percentage ([Frame.io Workflow Management](https://frame.io/features/workflow-management)).
Manju's QC tally follows the same restraint: `qc.by_level` says "this many
blocker/issue/fyi verdicts exist", never "QC made the video N% better".

## 3. Why shot-status tracking (Kitsu/ShotGrid) informs the funnel section but isn't copied wholesale

Kitsu/ShotGrid both track *task status* per shot per department (animation,
lighting, comp, ...) as the backbone of a whole team's coordination — genuinely
different from Manju's single-track, human-or-agent-driven funnel. What *does*
transfer is the underlying discipline: a status field only means what it was
observed to mean, nothing more. `_funnel_section`'s explicit `note` ("完整漏斗
进度用 `manju create`" — full funnel progress needs the funnel's own live
artifact walk, not the log) is Manju's version of the same discipline applied
to a much smaller surface.

---

## 4. Cut-list — features `manju evaluate` should flag if usage stays at zero

This instrumentation just landed (round AA); there is no real historical data
yet — every count in a `manju evaluate` run today is either zero or seeded only
by this round's own tests. So this is a **prediction**, framed as a set of
concrete falsifiable checks a future maintainer (or a scheduled `manju
evaluate` run) can act on once N real projects have accumulated enough events
to clear the `low_n` threshold (currently 3). Framing each as a testable
condition, not a verdict:

- **Bundled skills that stay in `skills.never_used` across many real projects.**
  14 skills ship today (`audio-finishing`, `character-consistency`,
  `cover-and-title`, `creation-funnel`, `manju`, `narrative-pacing`,
  `prompt-craft`, `repair-loop`, `series-bible`, `series-breakdown`,
  `shot-design`, `skill-authoring`, `subtitle-standards`, `visual-qc-review`).
  ROUND-V-REFERENCES-1 §1b already flagged Cursor's community rule of thumb —
  "5–8 rules is the sweet spot" — as a lesson for Manju's own skill count. The
  narrowest-audience candidates most likely to sit at zero: `series-bible` /
  `series-breakdown` (only relevant to multi-episode projects, a minority of
  runs) and `skill-authoring` (a meta-skill for writing new skills — used by
  skill authors, not by ordinary video-making sessions). If `manju evaluate`
  shows these at zero after real usage accrues, fold their content into a
  section of a broader skill (e.g. `creation-funnel`) rather than keeping them
  as standalone ids agents have to discover.
- **`manju create <stage>` scaffolding as a distinct command surface.** If
  `workflow.funnel.never_scaffolded` stays `["brief", "synopsis", "beats"]`
  (i.e. real users hand-write these files directly instead of running the
  scaffold command), the scaffold TEMPLATES are still valuable documentation
  but the separate `manju create <stage>` verb may not be pulling its weight
  as a command — consider folding it into `manju new`'s existing scaffolding
  instead of keeping a second entry point.
- **Consistency-mode QC (`qc brief --mode consistency`, round X).** Its
  three comparison-unit kinds (character / pair / scene) are a real answer to
  a real pain point (cross-shot consistency), but they are also the newest,
  narrowest surface in the QC pipe. If `qc_coverage().summary.units_never`
  stays high relative to `shots_never` across projects with multiple
  characters/scenes (i.e. people keep skipping consistency review even when
  it's available), that is a signal the UX for triggering it — not the
  judgment criteria — needs work, or that per-shot review already covers most
  of what teams check.
- **The director propose/confirm/execute loop as a separate surface from
  direct `manju build`.** `workflow` does not currently break out
  `director_propose`/`director_confirm`/`director_execute_*` counts (out of
  this round's scope), but the same log already carries them — a follow-up
  round should add them to `evaluate()` and watch whether the approve-before-
  spend proposal flow gets used at all outside of `ask_before`-gated expensive
  generations, or whether most spend still happens through direct
  confirmation prompts.

None of these are decisions this report makes — they are the specific,
falsifiable questions `manju evaluate`'s own counts are built to answer once
real usage exists. That is the whole point of building the evaluation layer
before touching any feature: cut decisions should follow the log, not precede
it.
