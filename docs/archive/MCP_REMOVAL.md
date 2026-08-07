# Historical MCP Removal Record

Date: 2026-08-07

Manju previously exposed a Model Context Protocol runtime, CLI entry point,
tool policy/profile schemas, active contract rows, protocol-specific tests, and
an optional `mcp-video` dependency. The v5.0 closeout removed that entire
surface from the current product and advanced the package version to 0.2.0.

The supported product boundary after this removal is project files, the
JSON-capable CLI, and the existing local GUI over shared domain/application
services. No reader, writer, migration, deprecated command, feature flag, stub,
or compatibility layer is retained for the removed protocol.

This file is historical evidence only. It is not a current capability,
contract, runtime input, or build input. The detailed inventory and measured
closeout evidence live in `REPORTS/MCP_REMOVAL_INVENTORY_2026-08-07.md` and
`REPORTS/MCP_REMOVAL_AND_V5_CLOSEOUT_2026-08-07.md`.
