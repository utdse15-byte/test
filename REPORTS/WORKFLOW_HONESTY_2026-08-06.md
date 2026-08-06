# Workflow honesty - Phase 6

Date: 2026-08-06

Phase 6 aligns the owner and agent surfaces around one derived selected-media
eligibility view. It does not add a Picture Lock state file, change legacy
build admission, or claim that a successful render proves a finished film.

## Landed

- `build/readiness.py` classifies every indexed selected take as `proxy-only`,
  `candidate`, `final-eligible`, or absent. Project Picture Lock eligibility
  requires every indexed shot to be `final-eligible`.
- `manju status`, `manju production status`, post-build output, workflow help,
  the skill index, the auto playbook and GUI `/create` consume the same terms.
- A proxy-backed final can no longer report completion or Picture Lock. The
  proxy notice only replaces an otherwise completed next step; it cannot hide
  a QC failure, stale media, missing locale output or another higher-priority
  action.
- The creation funnel is narrative-neutral: story and ending -> scene change ->
  SceneContract/ShotContract -> Animatic/Proof -> candidate build. Universal
  marketing syntax and one-beat/one-shot advice are not ordinary defaults.
- `manju new --demo` is explicitly a zero-cost system-check sample. Its
  caption-card media is `proxy-only` and proves deterministic pipeline
  plumbing, not narrative production or Picture Lock.
- Fresh projects include parser-valid `SCENE_EXAMPLE.yaml.example` and
  `SHOT_EXAMPLE.yaml.example` files. Their suffix deliberately does not match
  the narrative `*.yaml` opt-in glob, so a new or legacy project is not
  migrated into narrative readiness by scaffolding alone.
- README, CLI/GUI references and `docs/AI_AUTHORING_SYSTEM.md` now draw the
  creative/execution boundary and state that `build ok` does not imply lock.

## Verification

- Focused readiness and Phase 6 honesty tests: **35 passed**.
- Changed-surface workflow, container, GUI, docs, funnel and readiness bundle:
  **130 passed**.
- Preset/status regression: **130 passed**; seven cases could not execute on
  this workstation because Python 3.14 failed while creating a child process
  with `OSError: [WinError 6] invalid handle`. Six were `manju new` cases whose
  internal check reached the same subprocess path; one directly spawned
  ffmpeg. The failure is also reproducible in unrelated existing subprocess
  tests and is not classified as a product regression.
- `ruff check` on `src` and all changed tests: passed.
- `python -m compileall -q src` plus the new tests: passed.
- `git diff --check`: passed at closeout.

Phase 6 changes only documentation, derived read models, status presentation
and non-opting examples. Real provider media, human creative approval and a
real narrative production remain unproven; those are Phase 7B concerns, not a
caption-card or build-success inference.
