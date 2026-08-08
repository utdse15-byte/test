# Video Authoring and External Handoff Generalization

Date: 2026-08-08
Branch: `claude/fable-opus-task-division-wv97i6`
Base: `b2afbbc` (`feat(h3): add offline authoring handoff`)

## Delivered

- Added a frozen, in-memory `VideoAuthoringPlan` and `ReferenceGraph` derived
  from existing Shot, Bible, keyframe, dialogue, and reference truth.
- Separated unique physical assets, logical bindings, and explicitly normalized
  Subjects; H3 advisory file counts now use physical assets.
- Added a static authoring profile protocol/registry containing only
  `portable_video` and `minimax_h3`. Neither profile is a runtime Provider.
- Added the provider-neutral portable prompt dialect, upload order, constraints,
  profile docs, and five offline eval fixtures.
- Preserved H3 dialect modes, finding codes, prompt shape, exact
  `prompt_override`, reference labels, manual lineage, and old prompt CLI alias.
- Added generic `manju handoff profiles/create/inspect/verify` commands.
- Added v2 handoff and manifest schemas with explicit bundle, renderer, and
  profile revisions; v1 remains fully readable.
- Replaced per-file final-directory writes with sibling temporary-directory
  construction, full verification, atomic rename, immutable byte-equal reuse,
  and concurrent-winner verification.
- Added the sole full verifier for safe paths, no links/reparse points, casefold
  collisions, exact inventory, regular files, sizes, hashes, exact checksums,
  schema/profile validity, manifest digest, and identity agreement.
- Routed `ingest --handoff` through full verification before any take write.

## Local evidence

The focused implementation suite covered existing H3 compatibility, v1 legacy
read, canonical many-to-many graph behavior, physical count semantics, portable
prompt/override behavior, v2 atomic/concurrent reuse, seven tampering classes,
generic CLI dispatch, and verification-before-ingest:

```text
27 passed
```

Additional local regressions at implementation time:

```text
CLI snapshot + contracts: 23 passed
prompt/promptlab/prompt checks: 50 passed
ingest/batches/library ingest: 125 passed
```

The repository's parallel non-ffmpeg gate reached completion with:

```text
5949 passed / 61 skipped / 7 failed
```

Three failures were introduced governance pins (decision index, Chinese CLI
lead, current-surface protocol wording); all three were fixed and their 11
focused tests passed. The final combined authoring, integrity, CLI/contracts,
skills, documentation, help, and current-surface gate passed 75 tests. The
remaining failures were host-tool limitations outside
this change: no `grep.exe`, no `sh`, a Linux-only font path in the C20a ffmpeg
fixture, and Windows PowerShell failing before script execution with system
error `8009001d`. Consequently this report does not claim a full local release
gate or cross-platform CI green.

## Evidence boundary

No paid API, network generation, model download, local inference, GPU work,
real provider output, or film-quality validation was performed. Manual returns
remain unverified and never auto-select. `REPORTS/LAST_GREEN.yaml` was not
updated because there is no same-SHA Ubuntu and Windows evidence for this tree.
