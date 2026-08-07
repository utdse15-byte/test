# Architecture Boundaries

This is the current product boundary for Manju One v5.0.

## Supported surfaces

- Project files are the source of truth and are read and written directly.
- The JSON-capable CLI is the automation and inspection surface.
- The existing local GUI uses the same domain and application services as the CLI.
- Core modules own deterministic domain behavior, evidence, locks, readiness, and QC.
- Provider adapters are optional boundaries; this closeout does not invoke a real provider.

## Removed surface

The Model Context Protocol (MCP) runtime, server, client, tools, policy/profile,
transport, schemas, compatibility layer, command entry point, tests, and
`mcp-video` dependency are removed and unsupported. There is no replacement
protocol adapter and no feature flag or stub reserved for future restoration.

Historical audit and archived design records may mention the removed protocol.
They live under `docs/archive/` or in the reports ledger and are evidence only,
never current capability or build input.

## Offline closeout rule

The v5 rehearsal uses local fixtures, deterministic core services, CLI JSON, and
GUI state projection. It does not call a network service, a paid operation, a
real provider, or real-media Dogfood. Those activities require an explicit owner
handoff after the release gates pass.
