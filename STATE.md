# Active State

Small, active-only. Closed work lives in REPORTS/ and DECISIONS.md — do not
accumulate history here. (No test inspects this file's text; it is a human
note, not a contract.)

Default branch: `claude/fable-opus-task-division-wv97i6`
Last full-green Windows SHA: `efd7783` — windows-ci run 30704812522 (2026-08-01)
Last full-green Ubuntu SHA: `efd7783` — ci run 30704812542 (same commit, same day)

Both greens are on the SAME commit, so REPORTS/LAST_GREEN.yaml is stamped for
the first time (it sat at `pending` since the seed, because nothing actually
ran the generator — no workflow calls it; a maintainer does, by hand). `efd7783`
is the PR #52 head; the merge commit `c7889b8` has a byte-identical tree
(`aa87368`), so the evidence covers the default branch as it stands.
Test counts stay `null` there: nobody read them off the runs, and a plausible
number is worse than an honest blank.

## Validation model (which gate runs when)
- Every commit / PR head: compile + lint (ci.yml `static`) and the full
  **Ubuntu** suite (ci.yml `test`) — the dev-loop gate.
- Merge candidates (PRs) and release candidates (push to `main` + tags): the
  full **Windows** suite (windows-ci.yml) — the HARD release gate, never weakened.
- macOS / exploratory: scheduled + manual only (xplat.yml, informational).

## Open risks
- Multi-locale QC coverage (QC must check every declared locale, not `sorted()[0]`).
- Remote cancellation billing uncertainty (a local cancel does not prove the
  provider stopped before billing).
- Windows ffmpeg cancellation integration (real child-process kill on Windows).

## Current structural work
- Narrative closed-loop refactor — S0 and Phases 1–5 complete locally: stale
  provider formulas quarantined; SceneContract + nested ShotContract landed;
  one pure intent projection now drives current prompt, picture spec v3,
  expectations v2, and director view while legacy versions remain readable.
  Declared reference ownership now prunes only unresolved default Prompt
  variables and blocks closed-role conflicts before transport; ProviderManifest
  authoring evidence is traceable and advisory-only. Experiment Memory now
  extends the existing exact-media-bound verdict, remains historical after
  binding drift, and stays outside assurance/spec/attempt evidence. Production
  readiness now derives Scene/Shot, exact-byte Animatic approval, Proof Shot,
  ordered Proof Scene digest approval, and staged paid-video admission without a
  second truth store. Phase S1 (rewrite soft capabilities against the landed
  contracts) is next.
- Consolidate job metadata into one registry — DONE: `core/jobkinds.py`
  (`JobKindSpec`); GUI/frontend consume it via `GET /api/meta/job-kinds`.
- Shared operation semantics — DONE: `core/outcomes.py` (`OperationOutcome`,
  `classify_exception`, `CancelRecord`); MCP `_h_build` adapts via it.
- Shared execution context — DONE: `core/execctx.py`
  (`ExecutionContext`, `CancelToken`). Migrate call sites incrementally.
- Multi-locale QC — DONE: `qc/multilocale.py` + `manju qc --lang/--all-locales`.
- Replace source-text tests with behavioral tests — new tests follow
  tests/CONVENTIONS.md (no source/report scanning); several legacy
  `inspect.getsource` / page.py pins already converted, more remain.
- DEFERRED (needs Windows+Ubuntu green on the same commit FIRST, per the
  "refactor only after behavior is covered" rule): splitting oversized modules
  (gui/server.py → routes/, build/graph.py → phases/). New models are already
  extracted; the big file split is intentionally not started until the full
  gates pass on one commit.

## Do not regress
- Text is the source of truth.
- Media is append-only.
- Derived reports (REPORTS/, LAST_GREEN.yaml) are never build inputs.
- Windows-first, local-first, single-user personal software.
