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

## Acceptance closeout

The conditional-acceptance follow-up keeps the accepted architecture and closes
the protocol gaps without adding Provider, runtime, MCP, network, API, model, or
GPU surfaces:

- The v2 writer and verifier share one renderer-versioned semantic document.
  The verifier reconstructs it from the exact prompt bytes, parsed refs,
  manifest asset rows, and handoff fields, then recomputes both
  `semantic_digest` and `handoff_id`.
- Renderer r1 retains its original read formula. Current writers use r2, which
  also binds the complete canonical block. Unknown revisions fail closed.
- Cross-document shot, reference-plan, reference-graph, mode, quality, and
  prompt-origin copies must agree.
- Static profile descriptor history keeps known old profile revisions readable
  without retaining old executable profile implementations.
- Verified profile, semantic, manifest, reference-plan, format, and renderer
  lineage now reaches the manual take sidecar through a frozen path-free value.
- The reference-graph builder no longer swallows unexpected `relpath()`
  invariant failures.

Focused closeout coverage includes fully resealed stale prompt, refs, asset,
canonical, and handoff-ID cases; historical and unknown revisions; descriptor
drift; sidecar lineage; no stored handoff absolute path; and unexpected relpath
errors. Current local results are:

```text
authoring/H3 closeout: 43 passed
ingest/CLI/Windows-semantics regression: 212 passed / 2 skipped
repository non-ffmpeg attempt: 6006 passed / 62 skipped / 26 failed / 15 errors
```

The broad-run failures are outside this change's surfaces and reproduce as
host limitations: absent `sh`/`grep`, GBK default decoding, a Linux-only font
fixture plus missing fontconfig data, and Windows PowerShell startup error
`8009001d`. This remains local evidence only, not a release-green claim.
`LAST_GREEN` must not advance until the final fix SHA is green on both Ubuntu
and Windows CI.
