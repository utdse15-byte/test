# Active State — Research / Dogfood R2

Session takeover: read the newest `PROJECT_STATE_*.md`, then its task index.
Current snapshot: `PROJECT_STATE_2026-09-06_09-44-32.md`. Delivery identity belongs to the package
manifest and actual Git, not a self-referential SHA in this note.

## Current work

Active local branch: `improvement/research-dogfood-r2`.
Input baseline: `ee1e524a8640a121101603d3da9a2acb78a49c50`. Original ZIP preserved unchanged.
Runtime acceptance commit: `62ccbb96894ee7a9c22c1329b2d4f0cbd5524515`.
This is a focused research/repair candidate, not a certified Windows release.

Landed: first-row SFX and info-card creation; shared story revision/atomic write
protection; preservation of edits made during saves and hidden-stage drafts;
immutable exact-filename asset map; Unicode/long filename media suffix repair.
Historical renderer R1/R2 bundles remain readable without rewriting old bytes.
No provider/profile capability limits, routing, paid admission or approval
semantics were silently changed.

## Measured this round

Final available scope: 6366 passed / 15 failed /
22 skipped, 0 errors. Three Hypothesis modules
could not be collected. Related focused scope: 174 passed (overlaps, not additive).
24 browser/viewports and real-service composed action journeys passed; direct
browser localhost transport is sandbox-blocked, not E2E-certified.
Synthetic handoff/ingest and six-second edit/render/export rehearsals passed.
Four actual historical-source handoffs verified unchanged by current code.
Final wheel installed/imported in an independent target and matched source.
See `REPORTS/research-dogfood-r2/VALIDATION_2026-09-06.md` for exact boundaries.

## Release marker — deliberately unchanged

Last measured dual-platform green: `f076aee0482e134f1041817e07173ce10c056019`.
Ubuntu run 31289927126 and Windows run 31289952433, 2026-08-09.
`REPORTS/LAST_GREEN.yaml` is informational, manually stamped from actual runs,
never a build input. Local repair evidence does not update this marker.

## Open risks / next decisive evidence

- Local card/HTML generation cancellation can remain in a screenshot subprocess
  past the GUI cancellation deadline; remote-task cancellation exceptions must
  not be misused to fabricate a local/remote pending state.
- Browser screenshot failures and two legacy content-QC/selection assumptions
  remain unclosed in this sandbox. A timing-sensitive SIGINT failure was also
  observed in the interim run; paired isolated baseline/new runs passed.
- Actual Windows App lifecycle and child-process cancellation; same-final-SHA
  Ubuntu full suite + Windows hard release gate with pinned FFmpeg 6.1.1.
- Real provider/media Proof Shot and remote cancellation/billing semantics.

## Effective constraints

Windows-first, local-first, single-user. Text/YAML is truth; media append-only.
Current operational mode: `strict_zero_cost`; no user credentials/quotas or real
provider generation used this round. External AI may direct existing CLI/files;
no internal LLM, restored protocol, remote UI, or platform migration was added.
No automatic human selection, approval or Picture Lock. Reports, GUI state and
snapshots never become build inputs. No large core-module split before gates.

The complete previous state and handoff are retained in
`REPORTS/research-dogfood-r2/BASE_STATE_2026-08-15.md` and
`BASE_HANDOFF_2026-08-15.md`; original architecture and closed-work details are
still available there and in Git/DECISIONS, not discarded.
