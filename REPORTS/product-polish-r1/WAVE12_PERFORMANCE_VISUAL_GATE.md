# Product Polish R1 · Wave 12 — Measurable Performance and Visual Acceptance

Date: 2026-08-14

Branch: `codex/product-polish-r1`

Baseline: `cb75092a0a95078c4e3e7aa941f662f72e1e4ee7`

Performance feature commit: `38a76159ec18a8398dad1b0066247151b1af0908`

Visual-fixture hardening: `00436af7862401510eb6de780ef4ce6a56027e31`

## Decision

Waves 3–11 connected the product journeys, unified the shell, clarified human decisions,
made task cancellation honest, and established Chinese-first accessible surfaces. The next
maturity limit was no longer another page or another workflow. It was whether the product
could prove two things repeatedly:

1. a large personal project still opens quickly enough to feel like a desktop application;
2. the six most important surfaces keep their document, reflow and visual contracts as the
   product continues to change.

A 12/100/300-shot baseline found that the home Cockpit took about 0.69 / 5.56 / 15.94 seconds
on the restored Wave 11 tree. The 100- and 300-shot results were incompatible with the
product goal of opening into an immediately useful next action. The cause was not media
rendering or a missing cache. It was repeated deterministic work inside one read:

- export status compiled the entire effective timeline and both final content keys before it
  knew whether any export artifact existed;
- one Cockpit request recomputed the creation funnel three times;
- picture staleness was evaluated once for `project_status` and again for `suggest_next`.

Wave 12 removes that duplicated work and adds explicit local release aids. It does not add a
cache, database, project field, scheduler, Provider path or persisted recommendation.

## Product outcome

### Missing exports are now cheap and honest

`build.exportstatus` now gathers only cheap project facts up front. Timeline compilation and
content-key derivation are lazy and happen only when an existing artifact genuinely needs a
freshness comparison.

For an empty or early project, the export center can still report nine honest `missing` rows
without probing every shot or compiling the timeline. When final and proxy artifacts do
exist, one timeline compile and one pair of content-key calculations are shared by both rows.

This preserves the existing freshness owner and exact semantics. It only avoids work whose
result could not affect a missing row.

### One Cockpit request now shares one current read

`cockpit_data()` now evaluates the creation funnel once and threads that immutable result
through:

```text
engine suggestions
hero next action
authoring journey
```

It likewise evaluates picture staleness once and shares it between `project_status` and
`suggest_next`.

The public functions remain backward-compatible: standalone callers that do not pass a
precomputed view still perform their own existing read. There is no process cache, TTL,
materialized report, hidden state or new invalidation rule.

### The improvement is measured, not inferred

The same zero-cost benchmark tool was copied into the restored Wave 11 worktree for the
before observation and then run on the feature commit. One cold sample per size was used for
the direct comparison because the old 300-shot Cockpit took roughly 16 seconds per read.
The feature tree was additionally measured with one warm-up and five samples under explicit,
local release budgets.

| Shots | Wave 11 cold | Wave 12 cold | Reduction | Speed-up | Wave 12 warm median / p95 |
|---:|---:|---:|---:|---:|---:|
| 12 | 692.262 ms | 48.521 ms | 93.0% | 14.3× | 40.402 / 42.448 ms |
| 100 | 5561.350 ms | 284.854 ms | 94.9% | 19.5× | 290.869 / 301.931 ms |
| 300 | 15939.263 ms | 814.209 ms | 94.9% | 19.6× | 826.736 / 848.276 ms |

The direct cold comparison shows roughly a 93–95% Cockpit reduction. The repeatable warm gate
passed the explicitly supplied local budgets:

```text
12 shots   median <= 150 ms
100 shots  median <= 600 ms
300 shots  median <= 1400 ms
```

These are observations and an opt-in release check for the measured environment, not a hidden
runtime guarantee or a universal Windows performance claim.

Evidence:

- `REPORTS/product-polish-r1/wave12-tests/performance-before.json`
- `REPORTS/product-polish-r1/wave12-tests/performance-after-cold.json`
- `REPORTS/product-polish-r1/wave12-tests/performance-after.json`

## Repeatable local performance tool

A new developer command creates disposable 12/100/300-shot projects with tiny local PNG takes
and measures:

```text
build-state projection
home Cockpit
Review Theater HTML
Storyboard HTML
```

```bash
python scripts/dev/product_polish_benchmark.py \
  --output REPORTS/product-polish-performance.json
```

By default it reports only. Release developers may explicitly opt into Cockpit median budgets
with `--enforce-cockpit`. The tool uses no network, Provider, credential, FFmpeg process or
paid service, and its output is never a project or build input.

Behavioural tests pin work avoided instead of wall-clock milliseconds:

- missing deliverables do not gather or compile a timeline;
- existing final and proxy rows share one lazy timeline/key pass;
- one Cockpit request performs one funnel read;
- one Cockpit request performs one picture-staleness pass.

## Core visual acceptance gate

A second local tool renders six core product surfaces from their real server-side renderers
and the repository's actual CSS:

```text
workspace
create
storyboard
review
edit
exports
```

It runs at:

```text
1440 × 900 desktop
390 × 844 narrow
320 × 800 (about the usable width of 1280px at 400% zoom)
```

For every page and viewport it asserts:

- `lang=zh-CN`;
- exactly one skip link;
- exactly one focusable `main#main-content`;
- no duplicate IDs;
- no visible literal `undefined`;
- no page-level horizontal overflow;
- no browser page error.

All 18 page/viewport combinations passed. The tool supports filtered pages/viewports and a
`--no-screenshots` smoke mode so its own executable contract can be tested quickly. It also
isolates the recent-project store and replaces only the disposable fixture path with a stable
representative path, so screenshot diffs do not change merely because `tempfile` chose a new
directory.

Evidence:

- `REPORTS/product-polish-r1/wave12-tests/visual-acceptance.json`
- `REPORTS/product-polish-r1/screenshots/wave12/`

Representative screenshots:

- `screenshots/wave12/workspace-desktop.png`
- `screenshots/wave12/create-desktop.png`
- `screenshots/wave12/storyboard-desktop.png`
- `screenshots/wave12/review-narrow.png`
- `screenshots/wave12/edit-narrow.png`
- `screenshots/wave12/exports-desktop.png`

Playwright is now a development extra only. Ordinary Manju runtime dependencies and the
vanilla/stdlib product architecture are unchanged; the tool can point at system Chromium or
Edge and does not require an ordinary install to download a browser.

## Honest live-browser boundary

The existing live localhost Playwright verifier was attempted first. Chromium in this sandbox
is managed by a policy that rejects loopback navigation:

```text
net::ERR_BLOCKED_BY_ADMINISTRATOR at http://127.0.0.1:<port>/review
```

That failure is preserved in:

- `REPORTS/product-polish-r1/wave12-tests/live-http-browser-blocked.log`

The offline acceptance gate therefore uses `page.set_content` with real Manju renderers and
CSS. It proves structural and responsive product contracts, but it is deliberately labelled
`live_http_e2e: false`. Wave 12 does not claim a completed live-server journey.

## Documentation and reproducibility

`docs/GUI.md` now documents both local tools, their zero-cost boundary, the distinction between
offline real-renderer acceptance and live HTTP E2E, and the rule that a managed-browser
localhost block must be recorded rather than hidden.

`.[dev]` now declares Playwright as a developer-only dependency. Runtime remains free of a
browser framework, React, Electron or remote assets.

## Verification

Recorded, non-overlapping focused tests:

```text
255 passed
31 deselected
```

Groups:

```text
performance and dev gates               7
export status and finishing            91  (31 deselected)
Cockpit                                 13
home Cockpit                            12
authoring journey                      18
creation funnel                        18
Director                               27
Director surfaces                       8
shared GUI pages                       25
GUI modes                              22
application chrome                      8
accessibility contract                  6
```

Static checks:

```text
python -m compileall -q src tests scripts/dev     PASS
git diff --check                                  PASS
```

`ruff` is not installed in this environment and was not claimed as run.

## Zero-cost boundary

```text
real Provider calls:       0
external model/API calls:  0
credential reads:          0
paid calls:                0
free-tier calls:           0
```

The synthetic projects and screenshots use only local text and tiny local PNG media.

## Unchanged product boundaries

Wave 12 adds no:

- project schema or database table;
- persisted Cockpit recommendation;
- build input or cache identity;
- scheduler or automatic retry;
- automatic take selection, shot approval or Picture Lock;
- embedded LLM or Manju MCP;
- React, Electron or remote UI asset;
- real Provider or billing test.

The rules remain:

```text
performance view != scheduling fact
Cockpit suggestion != execution
review verdict != selected_take
selected_take != shot approval
shot approval != Picture Lock
```

## Not claimed

Wave 12 does not claim completion of:

- the full pytest suite;
- live localhost Playwright E2E;
- real Windows App-mode keyboard/mouse use;
- Windows hard gate;
- same-SHA Ubuntu and Windows green evidence;
- real Provider, cancellation, billing or AI-video quality.

## Next highest-value work

The product no longer needs another journey or another global surface. The next release step
should use these new gates to harden a real Windows App-mode session and then run the same SHA
through Ubuntu and Windows release gates. Any additional refactor should happen only after the
browser/visual baseline is stable and should be mechanical rather than mixed with redesign.
