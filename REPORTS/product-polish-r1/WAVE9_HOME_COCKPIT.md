# Product Polish R1 · Wave 9 — Home Cockpit

Date: 2026-08-14  
Branch: `codex/product-polish-r1`  
Baseline: `458f8dec01ecc2d3912bcc17f912557880f08f66`

## Decision

Wave 8 completed three coherent journeys:

```text
创作 → 可选提案 → 分镜
分镜 → 实验室 / 批量入库 → 审片
剪辑 → 字幕 → 混音 → 包装 → 导出
```

The home page already had the underlying facts, but presented the legacy status,
onboarding, build controls, shot grid, QC, event stream, proposals, ledger and Git
surface at nearly equal weight. The product problem was therefore not another
workflow. It was ordering.

Wave 9 makes home answer one question first:

> 现在最值得做什么？

It then shows the three existing journeys, at most five human-attention items,
active work and recent results. The complete engineering workbench remains
available under one explicit disclosure.

## Product outcome

### One priority

The priority projection is deterministic and read-only:

1. broken current media;
2. current-byte QC blockers;
3. unfinished authoring or pending proposal decisions;
4. stale shots;
5. candidate / selection / current-take review / shot approval;
6. existing engine build, redo, repair or package action;
7. finishing and delivery.

An engine action still enters the existing plan and cost-confirmation modal. The
cockpit cannot execute a provider request, select a take, approve a shot or create
Picture Lock.

### Three journey cards

Home reuses the existing authoring and shot-production projections and frames
finishing from current final evidence plus human shot approval. It does not save
another progress file or database row.

```text
创作  故事材料 4/4 · 分镜 6 镜
镜头  6/6 有候选 · 6/6 已选择 · 5/6 已通过
成片  等待镜头通过 · 5/6
```

Authoring and shot projections degrade independently. A broken authoring read
cannot hide shot/finishing status, and a broken shot projection cannot erase the
canonical authoring facts.

### Human attention, running work and recent result

The compact second row shows only context that changes a decision:

- failed/interrupted jobs;
- unread takes;
- pending or stale proposals;
- shot approvals;
- current risks;
- active local/remote work;
- current final or newest completed job.

A build lock is shown as context but is not double-counted as an additional job.

### Progressive disclosure

The former full workbench is preserved under:

```text
项目详情与高级操作
```

It contains the original project header, upload drop zone, build controls, jobs,
timeline, shot cards, QC, events, proposals, ledger and Git controls.

The section defaults closed in both beginner and professional view. Its state is
only per-user UI memory. When closed:

- shot cards are not constructed;
- the timeline is not requested;
- proposals are not requested;
- diagnostic evaluation is not requested;
- the hidden build cost estimate is not requested;
- the detailed cockpit DOM is not constructed.

Opening it renders from the already-fetched state and then performs the existing
lazy reads. It never becomes a project or build input.

### Safe App exit remains permanent

Collapsing the legacy header exposed a real lifecycle defect: App mode's safe
`退出` button was hosted inside that header and therefore disappeared. It now
belongs to the permanent `.pnav-tools` application bar, with the old header/body
only as fallbacks.

## Browser evidence

The evidence uses the real server renderer, real application CSS and real
`app.js`, with local deterministic API responses. No localhost server or external
network was required.

Demo state:

- 6 story-complete shots;
- 6 local append-only candidates;
- all 6 selected;
- 5 current candidates reviewed and approved;
- S003 awaiting current-candidate review;
- one local running job and one completed local result;
- `strict_zero_cost`.

### Desktop 1440×900

- focus: `评价 S003 的当前候选`;
- 3 journey cards;
- `待我处理 / 正在运行 / 最近成果`;
- workbench closed;
- initial shot-card DOM: 0;
- initial header detail DOM: 0;
- initial total elements: 200;
- expanded total elements: 441;
- initial API calls: only UI state, App status, state and cockpit;
- no initial `/api/build`, `/api/timeline`, `/api/proposals`;
- no horizontal overflow.

Screenshot: `screenshots/wave9/home-cockpit-desktop.png`.

### Narrow 390×844

- all six application stages remain visible;
- focus and one primary action remain above the journey cards;
- journey and priority cards stack in reading order;
- workbench remains reachable but collapsed;
- no horizontal overflow.

Screenshot: `screenshots/wave9/home-cockpit-narrow.png`.

Machine-readable evidence:

- `screenshots/wave9/home-cockpit-facts.json`
- `wave9-tests/browser-home.json`

## Local performance observation

A synthetic read-only benchmark ran `cockpit_data()` five times after warm-up.
This is local evidence, not a release threshold:

| Shots | Median | Max |
|---:|---:|---:|
| 12 | 32.83 ms | 35.46 ms |
| 100 | 177.92 ms | 181.53 ms |
| 300 | 503.48 ms | 528.25 ms |

The browser evidence also proves that the collapsed first paint avoids building
241 additional DOM elements in the six-shot sample. The larger saving in a real
100-shot project comes from deferring shot-card rendering and hidden auxiliary
reads.

Evidence: `wave9-tests/cockpit-benchmark.json`.

## Verification

Focused pytest, non-overlapping groups:

| Scope | Result |
|---|---:|
| Cockpit, home focus and Wave 9 product gates | 32 passed |
| Shared application chrome | 8 passed |
| Shared GUI pages | 25 passed |
| Workspace | 27 passed |
| GUI modes | 22 passed |
| Relevant UX subset | 6 passed / 86 deselected |
| **Total passed** | **120** |

Additional checks:

```text
python -m compileall -q src tests    PASS
rendered app.js | node --check       PASS
git diff --check                     PASS
```

`ruff` was not installed and was not fetched from the network.

## Zero-cost and architecture boundaries

```text
real provider calls:        0
external model/API calls:   0
credential reads:           0
paid calls:                 0
free-tier calls:            0
```

No new project schema, database, workflow state, provider route, MCP, embedded
LLM, React or Electron surface was introduced.

The following remain separate:

```text
recommendation ≠ selected take
selected take ≠ current-candidate review
review ≠ shot approval
shot approval ≠ Picture Lock
home priority ≠ execution
```

## Not claimed

This wave does not claim:

- full pytest;
- live-localhost Playwright E2E;
- Windows App-mode keyboard/mouse acceptance;
- Windows hard gate;
- same-SHA Ubuntu/Windows green evidence;
- real provider reliability or AI-video quality.

## Next product seam

The three journeys and home cockpit are now coherent. The next highest-value
polish is no longer another workflow rail. It is the global task/notification
experience:

```text
queued → running → waiting for human → downloading → validating → done
```

The owner should see one consistent task drawer, human-language phases, honest
cancel/interrupted semantics, de-duplicated notifications and a predictable App
exit experience without opening the engineering workbench.
