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
  `2026-08-08.r1` and `.r2` bundles remain verifiable. Safe upload names now
  preserve media suffixes for Unicode-only and long stems; historical renderer
  naming remains unchanged. The pure guide is explicitly classified outside
  timeline/NLE conform coverage.
- The legacy download-cap test now intercepts the actual credential-safe
  opener dispatch instead of the obsolete `urlopen` seam. The cap assertion
  and production redirect policy are retained; the test no longer attempts a
  real DNS request to its placeholder URL.

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
be collected. `LAST_GREEN` remains unchanged. Previous active state and handoff were archived in full before updating their current entry points.

## Final runtime acceptance

Runtime commit `62ccbb96894ee7a9c22c1329b2d4f0cbd5524515` completed the available
full scope: 6366 passed / 15 failed / 22 skipped (6403 cases). All 6373 original
case identities were retained; 30 new cases passed. Final failures were all
observed in the original baseline; the sole resolved baseline failure was the
obsolete transport test seam, not a newly fixed production transport.
Related focused scope: 174 passed (overlapping).

Final browser/real-service acceptance is `EVIDENCE/composed-delivery/`;
`edit-export-delivery/` retains the six-second synthetic edit/render/export
rehearsal; `historical-handoffs/` verifies four bundles made by actual historical
R1/R2 source. Final wheel source/independent-target comparison matched 240 files.
The final active notes and verification report describe remaining local
cancellation, browser screenshot, legacy selection and release-gate risks.
No production or test source was changed after this full-scope runtime freeze.
