# MCP Removal Inventory 2026-08-07

Audit baseline: `fb3a782090580187cc4767fb806077be463c7363`.
The annotated local checkpoint is `pre-mcp-removal-fb3a782`.

This report is an inventory only. No runtime code was deleted during C0.
The owner-provided closeout plan supersedes the earlier frozen-surface state:
MCP is now unsupported and will have no compatibility surface after C2-C4.

## Runtime Protocol

- `src/manju/mcp/` contains five runtime files: `__init__.py`, `__main__.py`,
  `policy.py`, `server.py`, and `tools.py`.
- `manju serve-mcp` and `python -m manju.mcp` are current entry points.
- `src/manju/cli.py` imports proposal path helpers from `manju.mcp.tools`.
  This is shared logic trapped in the adapter and is the C1 extraction target.
- `CONTRACTS.yaml` has active `manju.agent-surface/v1`,
  `manju.agent-tool-policy/v1`, and `mcp-tool-surface` rows.

## Tests and Current Product Text

- Ten MCP-named test files are present, plus MCP assertions in shared CLI,
  director, QC, docs, snapshot, and outcome tests.
- `tests/fixtures/cli_surface.json` contains `serve-mcp`.
- Current README, CLAUDE, CLI/Workbench/Design docs, skills, and source
  docstrings describe MCP as a supported surface.
- Historical reports and archived design/decision records are retained as
  evidence and are not deletion targets.

## mcp-video

- `pyproject.toml` declares the optional `mcpvideo` extra.
- `src/manju/qc/content.py` imports `mcp_video` through `mcp_video_gate` and
  reports the adapter in deep QC.
- `src/manju/build/doctor.py` probes `mcp_video` as an optional toolbelt item.
- `tests/test_content_qc.py` and Windows toolbelt expectations cover it.
- No v5 core module requires this package; replacement is existing local
  media/QC behavior, with no server/client/schema migration.

## Classification

| Class | Scope | Planned handling |
|---|---|---|
| `RUNTIME_PROTOCOL` | `src/manju/mcp/**`, serve-mcp, active MCP contracts | Remove in C2-C3 |
| `SHARED_LOGIC_TRAPPED_IN_MCP` | proposal slug/number/O_EXCL claim helpers | Move to `core/proposal_paths.py` in C1 |
| `MCP_VIDEO_DEPENDENCY` | optional extra, QC adapter, doctor probe, tests | Remove in C3-C4; retain local QC paths |
| `CURRENT_DOC` | README/CLAUDE/current docs/skills/help | Rewrite in C2-C4 |
| `CURRENT_TEST` | MCP-only tests, MCP assertions, CLI snapshot | Delete or migrate in C4 |
| `HISTORICAL` | `DECISIONS.md`, `REPORTS/**`, `docs/archive/**` | Retain; label as historical/removed |
| `FALSE_POSITIVE` | ordinary words containing `mcp` outside protocol usage | Review individually |

## C0 Exit

No Provider, network generation, paid request, real-media Dogfood, or history
rewrite was performed. The next phase is C1 shared proposal-path extraction.
