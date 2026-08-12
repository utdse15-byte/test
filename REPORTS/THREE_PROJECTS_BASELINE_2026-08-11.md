# G0-001 Zero-Cost Baseline

Status: **PASS_LOCAL_COMPOSITE**
HEAD: `e040751f04fd0de9d587014d44ae87ec4689e61a`  
Branch: `codex/manju-zero-cost-g0`

## Verdict

The zero-cost credential gate passed: 30 common cloud-provider credential names were checked, none were set, and no values were read or recorded. G0-001 owns no product runtime; its disposition is `REUSE_ONLY`, with evidence confined to `REPORTS/`.

The local baseline is green by explicit composite evidence. G0-002 may start.

Three independent facts matter:

1. The current HEAD differs from the real dual-green commit only in `STATE.md` and two existing evidence reports. `src/`, `tests/`, dependency files and workflows are byte-identical. The real runs are Ubuntu `31289927126` (6206 passed) and Windows `31289952433` (6155 passed), both on `f076aee0482e134f1041817e07173ce10c056019`.
2. The three required plan/runbook files name an external localhost-only OpenChatCut protocol. The v5 pin now exempts exactly those three documentation files while continuing to scan all product runtime, CLI, GUI, skills and other current documents; absence of the removed Manju module remains asserted.
3. The initial media failure was a Linux-only font path in the deterministic fixture, not an FFmpeg 8.x incompatibility. The fixture now reuses Manju's cross-platform font owner. The same corpus passes 44/44 on isolated FFmpeg 6.1.1 and the user's unchanged 8.1.2.
4. The installer tests now retain only `SYSTEMROOT`, which Windows PowerShell requires to initialize; cloud credentials and the rest of the parent environment remain excluded.

## Command Summary

| Command | Exit | Result |
| --- | ---: | --- |
| `python -m compileall -q src` | 0 | Passed |
| `python -m ruff check --select E9,F63,F7,F82 src` | 0 | Passed |
| `python -m pytest tests --collect-only -q` | 0 | 6211 tests collected in 9.51s |
| `python -m pytest -q -n auto` | 1 | 6111 passed, 64 skipped, 21 failed, 15 errors in 267.863s |
| Focused PATH rerun using the existing Git `sh`/`grep` | 1 | 15 passed, 1 skipped, 1 current-surface pin failure in 40.586s |
| `python -m pytest -q tests/test_windows_install_behaviour.py --tb=short` | 1 | 9 environment failures from the PowerShell host |
| Installer + v5 boundary focused tests after fixes | 0 | 13 passed |
| Media corpus on FFmpeg 6.1.1 | 0 | 44 passed |
| Media corpus on FFmpeg 8.1.2 | 0 | 44 passed |
| Full suite after environment/fixture fixes | 1 | 6146 passed, 64 skipped, 1 invalid test assertion in 266.745s |
| Affected union after correcting the offset-aware assertion | 0 | 68 passed |

The full-run failures were classified instead of mechanically rerunning the suite:

- Twelve missing-`sh`/`grep` failures were environment-only; the focused rerun cleared them.
- Fifteen corpus setup errors traced to a Linux-only fixture font path and were fixed through the existing font owner.
- Nine installer failures traced to over-isolating the Windows PowerShell subprocess and were fixed without inheriting credentials.
- The current-document conflict was narrowed to three exact external-integration documents without restoring a Manju protocol surface.
- The final full run exposed an invalid lexical timestamp assertion; the product already sorted absolute instants. The corrected affected union is green.

## Evidence

- `REPORTS/THREE_PROJECTS_BASELINE_2026-08-11.json`
- `REPORTS/logs/G0-001-collect.log`
- `REPORTS/logs/G0-001-path-focused.log`
- Existing CI source: `REPORTS/LAST_GREEN.yaml`

No report contains credential values, usernames, or private absolute paths. Derived evidence remains deletable and is not a build/runtime input.

## Next Gate

`G0-002` is ready. Real same-HEAD Ubuntu and Windows run IDs remain reserved for the later `manual_ci` release gate and are not inferred from local results.
