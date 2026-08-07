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

v5.0 AV0–AV8 are implemented as an additive, opt-in offline chain. Ownership
is recorded in `docs/SOURCE_LEDGER.yaml`; the rehearsal evidence is
`REPORTS/UNIFIED_FILM_REHEARSAL_2026-08-07.md`. No real Provider, paid request
or real-media Dogfood has been run.
- Narrative closed-loop refactor — S0, Phases 1–7A and S1 are complete locally: stale
  provider formulas quarantined; SceneContract + nested ShotContract landed;
  one pure intent projection now drives current prompt, picture SPEC v5,
  expectations v2/v3, and director view while legacy versions remain readable.
  Typed opening facts support exact canonical-subject pruning while legacy
  strings and prompt overrides remain unchanged. Authored reference bindings
  now include complete list/mapping syntax, scoped ownership, prop references
  and local-byte staleness; inferred Bible scopes now survive runtime resolution,
  budget classification and logical dedup while physical uploads remain unique;
  v1-v4 formulas remain permanent historical reads.
  Provider-specific delivery is rendered/read once into an immutable payload
  consumed by identity, admission, submit and retry. Generic-cloud submission
  identity v2 also binds a persisted, secret-free execution profile; unresolved
  rows fail closed before poll/redispatch when the original profile is absent or
  differs from the live manifest. Closed-role omissions remain blocked before
  transport and physical upload dedup stays separate. ProviderManifest
  authoring evidence is traceable and advisory-only. Experiment Memory now
  extends the existing exact-media-bound verdict, remains historical after
  binding drift, and stays outside assurance/spec/attempt evidence. Production
  readiness now derives Scene/Shot, exact-byte Animatic approval, Proof Shot,
  ordered Proof Scene digest approval, and staged paid-video admission without a
  second truth store. Assurance, observed states and continuation now consume one
  current-bound verdict, and Proof Scene approvals bind its stable accepted
  evidence rather than timestamps or older endpoints. `redo --from-take` replays
  only explicit creative recipe keys. Phase S1 rewrites the external AI-director Skills,
  adds scene-design, aligns review/continuation/repair/audio to observed media,
  and adds ten structured eval cases. Phase 6 now gives docs, workflow help,
  status, skill/auto surfaces, GUI and the system-check Demo one derived
  `proxy-only` / `candidate` / `final-eligible` vocabulary; build success never
  implies Picture Lock, and parser-valid `.yaml.example` contracts do not opt
  legacy projects into narrative gates. Phase 7A now rehearses the no-paid
  contract -> exact-byte Animatic -> failed endpoint/one-variable experiment ->
  REWRITE_SOURCE -> replacement candidate -> Proof Scene -> BULK_READY path,
  while retaining an empty provider-attempt ledger and an ungated legacy path.
  Scoped opening commitments use Expectations v3 so identical text for distinct
  subjects stays distinct; unscoped contracts remain byte-identical v2 and old
  v1/v2 verdicts remain readable.
  This is code/path evidence only. Real provider Dogfood (7B), evidence-driven
  S2 and cross-platform Phase 8 remain open.
- Consolidate job metadata into one registry — DONE: `core/jobkinds.py`
  (`JobKindSpec`); GUI/frontend consume it via `GET /api/meta/job-kinds`.
- Shared operation semantics — DONE: `core/outcomes.py` (`OperationOutcome`,
  `classify_exception`, `CancelRecord`); CLI and GUI adapters use it.
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

### v5.0 closeout and protocol removal (2026-08-07)

The current branch completes C0-C5 of the owner closeout plan. The supported
product surface is project files + the JSON-capable CLI + the existing local GUI
over shared core services. The removed protocol has no runtime, dependency,
contract, test, snapshot, or current-document compatibility surface. C6 local
checks and package audit are recorded in the final certification report; remote
Ubuntu/Windows CI evidence for the final commit remains pending until this
branch is pushed. No real provider, paid request, network generation, or media
Dogfood was run.

## Do not regress
- Text is the source of truth.
- Media is append-only.
- Derived reports (REPORTS/, LAST_GREEN.yaml) are never build inputs.
- Windows-first, local-first, single-user personal software.
