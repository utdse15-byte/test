# Manju One v5.0 Closeout Certification

Date: 2026-08-07  
Branch: `claude/fable-opus-task-division-wv97i6`  
C0 checkpoint: `pre-mcp-removal-fb3a782` (`fb3a782090580187cc4767fb806077be463c7363`)  
Release change: `0.1.0` -> `0.2.0` (breaking removal of the unsupported protocol surface)

## Scope completed

- C0 inventory and checkpoint recorded in `REPORTS/MCP_REMOVAL_INVENTORY_2026-08-07.md`.
- C1 shared proposal slugging and atomic numbering moved to `core/proposal_paths.py`.
- C2 runtime package, module entry point, CLI command, and agent profile option removed.
- C3 active contracts, policy/profile schemas, optional dependency, QC adapter, and
  packaging references removed.
- C4 protocol-only tests and snapshots removed; core behavior tests retained or
  migrated; current docs and skills now describe files + CLI + GUI only.
- C5 offline closeout gates added and rehearsed with no provider or network activity.

## Local evidence on the closeout tree

| Gate | Result |
| --- | --- |
| `tests/test_closeout_c1.py` through `tests/test_closeout_c5.py` | 337 passed |
| `tests/test_v5_closeout.py` | 4 passed after final edits |
| Impacted CLI/docs/package regression set | 72 passed |
| `python -m compileall -q src tests` | passed |
| `python -m ruff check src tests` | passed |
| `git diff --check` | passed |
| Full local suite (`PYTHONPATH=G:\\nxm\\src`) | 6058 passed, 63 skipped, 31 failed, 15 errors |

The full-suite non-green results are environment or pre-existing independent
module issues, not protocol-removal regressions: the corpus fixture cannot use
FFmpeg drawtext without Fontconfig; Windows install tests hit local PowerShell
error `8009001d`; local-command tests expect a POSIX `sh`; several tests decode
UTF-8 files through the host GBK locale; and existing static/help assertions are
outside C0-C5. These are preserved as explicit residual risk rather than hidden.

## No-protocol audit

The audit scans current `src/`, `tests/`, `skills/`, `README.md`, `CLAUDE.md`,
`docs/`, `pyproject.toml`, CI, and root operating files. It excludes only this
single boundary document because it explains the removal. All runtime, dependency,
contract, test, snapshot, and package-surface scans are otherwise zero.

Historical inventory and archived design records retain their original
terminology and are labeled historical evidence; they are not imported or read
by the current product.

## Package audit

The release artifacts were built locally as `manju-0.2.0-py3-none-any.whl` and
`manju-0.2.0.tar.gz`. The wheel contained 226 entries and the sdist contained 696;
both checks found no `manju/mcp` path, protocol module, or `mcp-video` dependency
metadata. This is an offline package inspection only.

## Platform certification status

The last measured same-commit Ubuntu + Windows green evidence remains the prior
SHA recorded in `REPORTS/LAST_GREEN.yaml`. The current closeout commit has not yet
run on those remote gates at the time this report is authored. Pushing this branch
is therefore the next release action; this report does not claim a green result
for a SHA that has not been measured.

## Explicit stop boundary

No real Provider, remote generation, paid request, network Dogfood, or real-media
Dogfood is authorized by this closeout. After remote release gates are measured,
the owner may separately create the real-Dogfood handoff; this task stops here.
