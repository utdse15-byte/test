# Active State

Small, active-only. Closed work lives in REPORTS/ and DECISIONS.md — do not
accumulate history here. (No test inspects this file's text; it is a human
note, not a contract.)

Default branch: `claude/fable-opus-task-division-wv97i6`
Last full-green Windows SHA: `f076aee` — windows-ci run 31289952433 (2026-08-09)
Last full-green Ubuntu SHA: `f076aee` — ci run 31289927126 (same commit, same day)

Both greens are on the SAME commit, so REPORTS/LAST_GREEN.yaml is stamped for
the video-authoring acceptance closeout tree. The Ubuntu run recorded 6206
passed / 5 skipped; the Windows hard release gate recorded 6155 passed / 56
skipped. It also passed the pinned-FFmpeg and
install/update/rollback/uninstall checks. No workflow calls the generator; a
maintainer stamps the measured result by hand.

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

The current branch completes C0-C6 of the owner closeout plan. The supported
product surface is project files + the JSON-capable CLI + the existing local GUI
over shared core services. The removed protocol has no runtime, dependency,
contract, test, snapshot, or current-document compatibility surface. C6 local
checks, package audit, and same-commit Ubuntu/Windows evidence are recorded in
the final certification report. No real provider, paid request, network
generation, or media Dogfood was run.

### MiniMax H3 zero-budget authoring handoff (2026-08-08)

The H3 track is implemented strictly as an offline `authoring_only` prompt
profile plus deterministic external handoff. It is not a Provider and does not
participate in network execution, routing, cost, qualification, paid admission,
generation or Picture Lock. The bundle reuses the existing prompt/reference,
animatic and manual ingest/select/QC paths; returned media stays unverified and
is never auto-selected. Implementation and local evidence are recorded in
`REPORTS/MINIMAX_H3_ZERO_BUDGET_IMPLEMENTATION_2026-08-08.md`. No MiniMax API,
weights, local H3 inference, paid request, real H3 output or quality validation
was used.

### Video authoring and external handoff generalization (2026-08-08)

The H3-only implementation now sits behind a static offline profile registry
with `portable_video` and `minimax_h3`. Both consume one frozen, in-memory
Canonical Video Authoring Plan and many-to-many ReferenceGraph derived from
existing project truth. New bundles are v2, directory-atomic, immutable, and
fully verified down to paths, regular-file status, exact inventory, bytes,
hashes, checksums, schema, profile, and identity before manual ingest writes.
Legacy v1 H3 bundles remain readable. No runtime Provider, network, paid API,
model download, local inference, GPU path, editable truth file, automatic
selection, or removed-protocol surface was added. The v2 acceptance closeout
recomputes semantic identity from actual bundle content, preserves verified
manifest/profile lineage through manual ingest, and reads known historical
profile revisions from static descriptors. Same-SHA Ubuntu and Windows evidence
for `f076aee` is recorded in `REPORTS/LAST_GREEN.yaml`.

## Do not regress
- Text is the source of truth.
- Media is append-only.
- Derived reports (REPORTS/, LAST_GREEN.yaml) are never build inputs.
- Windows-first, local-first, single-user personal software.
