# Product Polish R1 · Wave 10 — Task Center, Honest Cancel and Safe Exit

Date: 2026-08-14  
Branch: `codex/product-polish-r1`  
Baseline: `9fb911a62aff164f3c2c9f84149bb1c3a88c2192`  
Feature commit: `4988541a2e901797a8ae50c6745bc5d6a2ada509`

## Decision

Wave 9 made home answer one question first, but task lifecycle still had several
product surfaces interpreting the same facts independently:

- the home jobs panel;
- the compact cross-page task bar;
- system notifications;
- waiting-user cost banners;
- page-specific cancel buttons;
- the App-mode quit dialog.

The underlying engine model was already strong: `JobRunner` owns the serialized
queue, `jobkinds` owns capability/cancel/retry metadata, jobs carry durable
transition evidence, and paid cancellation can retain an uncertain billing
state. The problem was not another queue. It was one coherent presentation.

Wave 10 therefore introduces a permanent, read-only Task Center over the existing
facts and removes duplicated notification/cancel/exit interpretations from the
normal shell. It creates no project state, task database, retry protocol or
scheduler.

## Product outcome

### One global task entry

Every bound GUI page now carries one permanent application-bar entry:

```text
任务  [count]
```

The count means:

- work that needs a human decision;
- running work;
- queued work;
- or, when no work remains, unseen terminal transitions.

The task entry and the permanent safe `退出` action remain visible on desktop and
390 px narrow layouts. The narrow application bar has no horizontal overflow and
keeps `退出` on one readable line.

### One truthful grouping

The drawer groups current `JobRunner` facts as:

```text
需要你处理
正在进行
接下来
最近结束
```

`waiting_user` is human attention, not a successful task. A positive structured
estimate renders as, for example:

```text
预估费用 2.4 CNY；尚未执行付费步骤。
```

A non-monetary confirmation instead says the operation has not executed and
links back to the owning surface; it is not mislabeled as a fee plan.

Critical attention and active rows are never truncated. Large queued and recent
groups disclose the remainder while preserving the real group total and keeping
every task reachable.

### Human phases without losing audit facts

Raw phases remain in technical details, while the primary surface translates:

```text
gen:S003 (3/12)  →  生成 S003 · 第 3 / 12 个
render:final      →  渲染成片
qc                →  检查质量
```

Completed tasks keep the operation visible:

```text
质检 · 已完成
检查质量 · 已完成
```

instead of the previous visually redundant `已完成 / 已完成`.

### Immediate visibility after job acceptance

The shared `requestJson` owner emits a presentation-only
`manju:jobs-changed` event when a 202 response contains accepted background
work. The Task Center refreshes immediately and deduplicates overlapping reads.
It no longer waits for the idle polling interval before showing a newly queued
job.

This event is not task state. `JobRunner` remains authoritative and polling is the
fallback for older/custom hosts.

### Cross-page completion continuity

Task state observations live only in project-scoped `sessionStorage`. When a user
navigates while a job is running and it finishes on the next page, the first read
on that page can surface exactly one unseen transition. Notification keys are
deduplicated and pruned as jobs leave the current runner window.

No notification permission prompt appears during generation or page load.
Permission is requested only from the explicit Task Center setting.

### Honest cancellation

Queued work says:

```text
移出队列
```

Running local work says it will stop at a supported safe checkpoint and that
completed local artifacts remain.

A potentially paid remote task uses a focus-safe Manju `alertdialog` with the safe
default:

```text
继续等待
```

and explains that a cancel request does not prove the provider stopped or stopped
billing. The dangerous action is visually distinct.

A canceled task with uncertain remote disposition stays in `需要你处理`, retains
its provider job identity, links to Provider status and never offers an immediate
retry. This prevents the most dangerous duplicate paid submission state from
looking recoverable by one click.

In `strict_zero_cost`, the same paid-capability job remains auditable as a paid
kind, but the UI does not falsely warn that cloud billing is active because the
execution policy already blocks external Provider transport and credential
reads.

### One safe exit explanation

The permanent App exit now uses the same human phase and billing-risk facts as the
Task Center. It explains:

- the current task, current phase and shot context;
- whether the task can cooperatively cancel;
- whether remote billing can remain uncertain;
- that queued jobs have not started and will be removed for either exit choice.

If only queued work exists, the primary action is:

```text
移出队列并退出
```

and no meaningless “cancel running task” choice is rendered.

The dialog traps focus, restores focus, closes with Escape or backdrop, and keeps
`继续使用` as the initial focus. It does not force-kill the engine.

### Progressive notifications

The drawer owns two browser-only preferences:

- system completion reminders;
- an optional short completion sound.

They are local browser preferences, never project truth. Audio contexts close
after the tone. Critical errors and waiting-user states remain visible in the
Task Center and do not rely on a disappearing toast.

## Architecture boundaries

The new projection consumes:

```text
JobRunner.list()
JobRunner.interrupted()
jobkinds.spec()
GuiServer.app_status()
execution_policy_snapshot()
```

It does not:

```text
write project files
write a task database
create a scheduler
infer provider completion
release uncertain billing exposure
auto-retry ambiguous work
read credentials
call a Provider
become a build/cache input
```

The old compact bar, home notification surface and home-only quit injection remain
fallbacks only when a custom/legacy shell omits the shared Task Center.

## Browser evidence

Evidence uses the real Manju server shell, real application CSS, actual Task
Center / project-action JavaScript and Playwright `page.set_content`, with local
deterministic API stubs. It performs no external network or Provider call.

### Desktop 1440×900

Screenshot: `screenshots/wave10/task-center-desktop.png`

Evidence:

- 2 attention tasks, 1 active and 1 queued produce badge `4`;
- task button title is `2 项需要处理 · 2 项进行中`;
- drawer is an ARIA modal dialog;
- structured cost is visible;
- remote-cancel uncertainty has a Provider-status action and no retry;
- running progress and raw phase remain auditable;
- no notification permission request occurs.

### Narrow 390×844

Screenshots:

- `screenshots/wave10/task-center-appbar-narrow.png`
- `screenshots/wave10/task-center-narrow.png`

Evidence:

- application bar width and scroll width are both 380 px;
- bar height is 46 px;
- no page or app-bar horizontal overflow;
- the task text label collapses but the count remains;
- project, mode and safe exit remain readable;
- drawer width is 369 px and all actions remain reachable.

### Interaction evidence

Screenshots:

- `screenshots/wave10/task-cancel-confirm-desktop.png`
- `screenshots/wave10/safe-exit-dialog-desktop.png`

Machine-readable evidence:

- `screenshots/wave10/task-center-facts.json`
- `wave10-tests/browser-task-center.log`

Verified interactions:

- cancel endpoint is not called before explicit paid-risk confirmation;
- `继续等待` receives initial focus;
- one explicit dangerous confirmation produces one cancel call;
- a cross-page active→done transition produces one unseen item;
- notification permission is not requested implicitly;
- one accepted 202 job triggers exactly one immediate extra Task Center read;
- an 11-task queued group shows 8 rows, `显示其余 3 项`, then all 11;
- safe exit defaults to `继续使用` and explains queued/no-start and remote-billing
  uncertainty.

## Local projection performance

`task_center_snapshot()` was measured after warm-up for 800 iterations per case.
This is a local synthetic projection benchmark, not a cross-platform release
threshold:

| Current jobs | Median | p95 | p99 | Max |
|---:|---:|---:|---:|---:|
| 0 | 0.0043 ms | 0.0049 ms | 0.0102 ms | 0.2147 ms |
| 10 | 0.0955 ms | 0.1680 ms | 0.3189 ms | 0.4791 ms |
| 50 | 0.4052 ms | 0.7043 ms | 1.0865 ms | 2.4982 ms |

Evidence: `wave10-tests/task-center-benchmark.json`.

## Verification

Non-overlapping, logged focused groups:

| Scope | Result |
|---|---:|
| Task Center, application chrome, daily-loop pins, JobKind registry | 73 passed |
| GUI project actions and quit API | 14 passed |
| Cancel billing disposition | 8 passed |
| Personal GUI request-owner and notification pins | 14 passed |
| Selected shutdown/owner/read-only quit safety | 8 passed / 11 deselected |
| Home cockpit regression | 12 passed |
| Shared GUI page regression | 25 passed |
| Webclient/protocol/shell-script regression | 13 passed |
| **Logged passed total** | **167 passed** |

Additional checks:

```text
python -m compileall -q src tests                PASS
rendered common.js       | node --check           PASS
rendered app.js          | node --check           PASS
rendered project-action  | node --check           PASS
rendered task-center.js  | node --check           PASS
rendered webclient.js    | node --check           PASS
git diff --check                                  PASS
```

`ruff` is not installed in this environment and was not fetched from the
network.

A few broader/repeated runner-lifecycle commands did not produce a stable summary
within the sandbox timeout. One direct `test_gui_wave_complete.py` run completed
9/9, while a later repeat stalled after partial progress; that result is not
included in the logged total and no full-suite claim is made. The full ledger and
ask-before suites were likewise not counted after timeout.

## Zero-cost evidence

```text
real Provider calls:       0
external model/API calls:  0
credential reads:          0
paid calls:                0
free-tier calls:           0
```

The standard/paid UI evidence is a local deterministic simulation only. It proves
copy, focus, action count and fail-safe interaction; it does not claim a real
provider cancel or billing result.

## Not claimed

Wave 10 does not claim:

- full pytest;
- live-localhost Playwright E2E;
- Windows App-mode mouse/keyboard acceptance;
- Windows hard gate;
- same-SHA Ubuntu/Windows green;
- real Provider cancellation behavior;
- real billing accuracy;
- AI video quality.

## Next highest-value direction

The main daily journeys and global task lifecycle are now coherent. The next
polish wave should not add another workflow. It should unify product language and
feedback across the existing surfaces:

```text
Chinese-first copy inventory
→ shared status and error vocabulary
→ inline protection / next-action wording
→ accessibility and 200–400% zoom
→ canonical visual regression journeys
```

The work should start from actual screenshots and browser behavior, not a CSS
rewrite, and should preserve all existing truth, media, cost and human-decision
boundaries.
