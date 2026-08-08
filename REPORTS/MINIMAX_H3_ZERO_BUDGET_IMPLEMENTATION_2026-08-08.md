# MiniMax H3 Zero-Budget Implementation Report

Date: 2026-08-08
Status: implementation complete; offline scope only

## Boundary

`minimax_h3` is an unofficial `authoring_only` prompt profile and external
handoff format. It is deliberately absent from the Provider catalog, routing,
cost estimation, qualification, paid admission, submission, polling,
downloading, take generation and Picture Lock.

No MiniMax API call, API key, paid request, network generation, H3 weight
download, local H3 inference, GPU use, fake Provider, fake generated video,
real-media Dogfood or H3 quality validation occurred.

## Implemented

- Separate offline prompt-profile registry with dated advisory H3 limits.
- Pure H3 mode projection for T2VA/I2VA/L2VA/FL2VA/REF2VA.
- Static conflict, missing input, duration and reference-count lint.
- Character-for-character `generation.prompt_override` preservation.
- Deterministic fallback prompt marked `needs_director_review` without invented
  action, emotion, music, dialogue, lyrics or visible text.
- One frozen logical reference plan and digest shared by prompt labels,
  `handoff.json`, `refs.json`, `MANIFEST.json`, asset bytes and checksums.
- Physical asset dedup without collapsing logical Subject/control bindings.
- Safe asset freezing through the existing reference read guard, with
  change-during-export refusal.
- Query/fragment-redacted remote identities; remote references are not
  downloaded.
- Read-only current animatic linkage with content, shot-timing and keyframe
  digests plus human approval state.
- Existing ingest/manual-take extension for handoff ID, bundle digest, returned
  SHA256 and `claimed_generator: unverified`; H3 returns never auto-select.
- Existing ratio/duration comparison recorded in the take sidecar and surfaced
  by ingest before human selection.
- Three recipe references and five structured offline eval fixtures.

## Commands

```text
manju prompt S001 --target minimax_h3
manju prompt S001 --target minimax_h3 --check
manju prompt S001 --target minimax_h3 --json
manju prompt S001 --target minimax_h3 --bundle --output exports/provider_handoff
manju build --target animatic
manju ingest returned/S001_h3_v1.mp4 --shot S001
manju ingest returned/S001_h3_v1.mp4 --shot S001 --apply \
  --no-auto-select --handoff exports/provider_handoff/minimax_h3/S001/<digest12>
manju select S001 <take>
```

## Offline Dogfood

The integration test exercised three shots through existing project services:

- S001: T2VA, no generated media, existing animatic dry-run only.
- S002: I2VA with a project-contained start keyframe.
- S003: REF2VA with three logical bindings over two physical Pictures; one
  Picture supports two Subjects and one Subject uses two Pictures.

The animatic plan remained the existing zero-paid path. With no human animatic
approval, all handoffs reported `h3_handoff_before_animatic_approval` and still
exported as handoff-only, Picture-Lock-ineligible bundles.

## Verification

- Final H3/CLI/contract/conform/snapshot/skill gate: 49 passed.
- Earlier prompt/ref/ingest/animatic/routing compatibility subset: 282 passed;
  one pre-existing Windows `sh` lookup failure in `local_cmd`.
- Full local non-FFmpeg sweep before environment normalization: 5970 passed,
  62 skipped, 102 deselected. The observed failures/errors were caused by local
  environment gaps (missing `sh`/`grep`/`sleep`, GBK default decoding, FFmpeg
  Fontconfig using a Linux font path, and Windows PowerShell managed-runtime
  error 8009001d) plus two new governance registrations; both governance issues
  were fixed and their focused gates rerun green.
- With UTF-8 mode and bundled Git `sh`/`grep`, the focused environment-sensitive
  set passed 39 tests; only the existing timeout test remained blocked because
  that Git runtime lacks `sleep`.
- `compileall`, Ruff configured gate and `git diff --check`: passed.
- Boundary scan confirmed no H3 entry in Provider registry/routing/
  qualification/cost paths and no network client/API authorization code in the
  H3 modules.

This report does not stamp a new full-suite or CI green. The last official
same-SHA Ubuntu/Windows evidence remains in `REPORTS/LAST_GREEN.yaml`.
