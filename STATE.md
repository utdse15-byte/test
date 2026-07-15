# Active State

Small, active-only. Closed work lives in REPORTS/ and DECISIONS.md — do not
accumulate history here. (No test inspects this file's text; it is a human
note, not a contract.)

Current head: `claude/ci-validation-improvements-rsmjpf`
Last full-green Windows SHA: pending — see REPORTS/LAST_GREEN.yaml (CI-stamped)
Last full-green Ubuntu SHA: pending — see REPORTS/LAST_GREEN.yaml (CI-stamped)

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
- Consolidate job metadata into one registry — DONE: `core/jobkinds.py`
  (`JobKindSpec`); GUI/frontend consume it via `GET /api/meta/job-kinds`.
- Shared operation semantics — `core/outcomes.py` (`OperationOutcome`).
- Shared execution context — `core/execctx.py` (`ExecutionContext`).
- Replace source-text tests with behavioral tests (in progress; see
  tests/CONVENTIONS.md — new tests must not scan source or report strings).

## Do not regress
- Text is the source of truth.
- Media is append-only.
- Derived reports (REPORTS/, LAST_GREEN.yaml) are never build inputs.
- Windows-first, local-first, single-user personal software.
