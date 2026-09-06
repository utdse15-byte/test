# Research / dogfood R2 — implementation record

Date: 2026-09-06. Base: `ee1e524a8640a121101603d3da9a2acb78a49c50`.

## Landed scope

- Empty and delete-all mixer / packaging forms can create their first SFX or
  information-card row. Inert templates reuse the existing Python row renderers;
  new rows never clone a previous user's populated input or selected card kind.
- Story editors send the revision of the exact displayed text. The existing
  shared quick mutex, cross-process build lock, optimistic comparison and atomic
  writer now also own story draft writes. Incomplete prose remains saveable;
  project-schema checking is explicitly omitted only for this draft caller.
  GUI conflicts preserve both canonical disk text and the unsaved browser draft.
  Legacy API clients omitting `expected_rev` retain the existing opt-in contract.
- Save completion marks only the submitted snapshot saved, not text typed while
  saving. Delayed refresh checks all stages and in-flight saves at execution time.
- Offline handoff renderer `2026-09-06.r1` adds `ASSET_MAP.md`: exact packaged
  filenames, keyframe order, logical reference roles, and explicit not-packaged
  resources. Its bytes participate in semantic identity. No URL is downloaded,
  no profile limit or provider capability is silently changed. Historical
  `2026-08-08.r1` and `.r2` bundles remain verifiable.

## Evidence recorded before broad regression

`r2-focus-first.log`: 67 passed. `r2-new-regressions-final.log`: 56 passed.
These scopes overlap and must not be summed as unique tests. Red evidence on
unchanged product reproduced the empty-row and stale-write defects; separate
handoff tests first failed for the missing map. Browser tests use actual Chromium
DOM/script execution, not source-string assertions.

The delivery evidence directory carries exact command logs and subsequent broad
regression results. This implementation record alone does not certify release.

## Unchanged release boundaries

No paid/provider generation; no credentials used; no real generated-footage or
human film approval. No Windows runtime available. Direct browser localhost
navigation is blocked by this sandbox; browser/real-service composition is not
live HTTP E2E. Available FFmpeg is 7.1.5 rather than pinned 6.1.1. Hypothesis,
pytest-xdist and Ruff are unavailable. Three Hypothesis-dependent modules cannot
be collected. `LAST_GREEN` and old project delivery records remain unchanged.
