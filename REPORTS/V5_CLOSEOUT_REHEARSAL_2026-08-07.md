# v5.0 Closeout Rehearsal (2026-08-07)

This rehearsal is intentionally offline. It does not invoke a real provider,
remote generation, paid request, network media service, or real Dogfood.

## Gates

`tests/test_v5_closeout.py` verifies:

- the current source, tests, skills, docs, CI workflow, packaging metadata, and
  root operating files contain no removed protocol/dependency surface;
- `manju` CLI and GUI state derive the same project status core;
- an unexpected external socket attempt fails the rehearsal immediately while
  a zero-cost `build --dry-run --json` remains usable.

## Evidence

| check | result |
| --- | --- |
| v5 closeout gates | `3 passed` |
| C4 migrated/deleted surface tests | `337 passed` |
| compileall | passed |
| ruff | passed |
| network/provider/paid execution | not invoked by design |

The complete release certification records the final commit, package audit,
and platform evidence in `REPORTS/MCP_REMOVAL_AND_V5_CLOSEOUT_2026-08-07.md`.
