# Budget semantics presentation fix (2026-08-13)

## Decision

`project.yaml` `budget.limit` is a per-build ceiling. The engine compares the
current build's estimate and running actual spend with it; cumulative ledger or
take-sidecar history is not a build input. Presentation surfaces now label the
two facts separately and do not derive a cumulative/limit progress ratio.

## Changes

- Removed the cockpit budget risk row and director budget suggestion, because
  neither surface has current-build context.
- Labelled board, CLI and GUI values as cumulative spend versus per-build limit.
- Removed misleading cumulative-spend progress bars from both GUI spend panels.
- Added a behavioral regression for historical ledger spend above the limit and
  documented the decision in README, CLI.md and DECISIONS.md.

## Verification

- `python -m pytest -q -n auto tests/test_cockpit.py tests/test_director.py tests/test_spend.py tests/test_c0911_gates.py tests/test_ledger_p1_nan_state.py tests/test_ledger_p0_wiring_graph.py tests/test_c21g_gates.py tests/test_ask_before.py`
  → 130 passed, 1 skipped.
- `python -m pytest -q tests/test_docs_reachability.py tests/test_fp_docs.py` → 13 passed.
- `python -m compileall -q src` → 0.
- `python -m pytest -q -n auto` → 6175 passed, 63 skipped, 10 failed. The
  remaining failures are pre-existing Windows environment gaps: three tests
  invoke unavailable `grep`, and seven local-command tests invoke unavailable
  `sh`; none is related to this change.
- `git diff --check` → 0.
- `ffmpeg -version` → 8.1.2-full_build; no environment downgrade was performed.
- No provider key was configured or read; no network/provider/model call was made.
