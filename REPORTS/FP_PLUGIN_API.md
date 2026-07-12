# FP loop — provider plugin API freeze (`manju.provider-plugin-api/v1`)

Roadmap item 10: "stable plugin interfaces (no online marketplace)".
Executed by the orchestrator directly (registry edits are orchestrator-only).

## What was true before

Third parties already extend Manju two ways — a `provider.yaml` manifest
(`generic_cloud` or the `module:Class` escape hatch) or a `Provider`
subclass via `register_provider()`. Both worked; neither was **promised**.
Nothing stopped an additive refactor from renaming `submit`, adding a new
required `GenerationRequest` field, or growing a new abstract member — each
of which silently breaks every shipped plugin.

## What this loop adds (freeze, not features)

1. `providers/base.py`: `PLUGIN_API_CONTRACT = "manju.provider-plugin-api/v1"`
   — the in-code half of the contract identity (a pure constant; zero
   behavior change).
2. `CONTRACTS.yaml`: the schemas row (stable, `owner: manju.providers.base`,
   `read_older: true`, `write_older: false`). The existing F0 tests bind the
   literal and the row bidirectionally from this commit on.
3. `tests/test_fp_plugin_api.py` — 13 surface pins:
   - contract identity in code + registry (red-first: both failed before
     the constant/row landed);
   - `manju.providers.__all__` floor (11 plugin-facing names);
   - `FailureKind` member floor + str-enum value stability (values are on
     disk in `reports/failures.jsonl`);
   - `GenerationRequest` head-field law (5 required, exact order) + the
     additive law: **every later field must carry a default** — the rule the
     codebase has followed by comment ("additive and default-absent") is now
     enforced for all future fields;
   - `ProviderFailure` signature + attribute quartet;
   - `CloudProvider.__init__`: every parameter keyword-only WITH a default
     (present and future — checked generically);
   - registry entry-point signatures;
   - the plugin-author proofs: a minimal `id`+`generate` subclass registers
     and resolves (a new abstract member fails this exactly when it should);
     the cloud trio instantiates, a partial trio refuses;
   - manifest surface floor (`GENERIC_ADAPTER` name + 10 manifest fields);
   - the two protective manifest rules, sandbox-verified before pinning:
     broken adapter string → `manifest_errors()`, registry survives;
     builtin-shadowing id → skipped with error, builtin instance retained;
   - `fallback_chain` never dead-ends (terminates at `caption_card`).
4. README: "Writing your own provider — the frozen v1 plugin API" subsection.

## What was deliberately NOT built

No marketplace, no remote discovery/index, no dynamic download of adapters
(the roadmap item's own exclusion). No new runtime code path at all — the
only src change is the contract constant. Provider *behavior* stays owned by
the existing behavior suites; this file pins *surface*.

## Verification

- Red-first: contract-identity tests failed before the constant + row.
- `python -m pytest tests/test_fp_plugin_api.py tests/test_fp_contracts.py`
  → 30 passed (freeze + full registry-consistency suite).
- Escape-hatch and shadowing behaviors verified in a sandbox against the
  real registry code before being pinned.
