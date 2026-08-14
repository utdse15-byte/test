# Product Polish R1 · Wave 11 — Chinese-first Language, Durable Feedback and Accessible Reflow

Date: 2026-08-14  
Branch: `codex/product-polish-r1`  
Baseline: `7b5b8f2d727653342a4832921fbbb3b9d5fc3cae`  
Feature commit: `77eefcbf4b198a98fafa97f22224e12115de55fe`

## Decision

Waves 3–10 connected the major product journeys and made home, review, shot
production, authoring, finishing and task lifecycle coherent. The next maturity
limit was no longer workflow structure. It was the small shared behavior a user
touches everywhere:

- some document shells still declared only generic `zh` and each carried its own
  no-JavaScript fallback;
- keyboard users had no consistent way to bypass the permanent application and
  stage bars;
- first-run project errors were short-lived technical messages instead of a
  durable recovery surface;
- the glossary `?` affordance imitated a button with `tabindex` rather than being
  a native control;
- success and error toasts used the same rack semantics even though errors must
  remain and be announced assertively;
- the stale-project protection overlay looked protective but did not expose
  dialog semantics or a complete focus boundary;
- 200%–400% text and narrow reflow were not pinned across every shell;
- browser 404 navigation still felt like an engineering fallback rather than a
  product screen.

Wave 11 therefore makes language, focus, feedback and high-zoom reflow shared
product contracts. It adds no workflow, project state, database, Provider path or
business decision.

## Product outcome

### One document-level accessibility owner

A new tiny, stateless helper owns only four fragments:

```text
HTML language: zh-CN
skip link: 跳到主要内容
focusable main landmark: main#main-content, tabindex=-1
no-JavaScript protection message
```

Every primary GUI document now renders exactly one skip link and exactly one main
landmark:

```text
home
shared pages
subtitles / mixer / packaging
create
director
storyboard
shot lab
ingest
edit
exports
series
workspace
browser 404
```

The helper does not introduce a page framework, runtime state or new navigation.
It prevents independent shells from drifting on the most basic document
contract.

### Keyboard users can reach the work immediately

The first Tab stop is now:

```text
跳到主要内容
```

Activating it moves focus to `main-content` without putting the main landmark in
the normal tab order. The permanent app bar, six production stages and stage
subnavigation remain fully available, but no longer have to be traversed before
every page's work.

The link is visually hidden until focused, has a clear focus treatment, and
honours reduced-motion preferences.

### Chinese-first no-JavaScript and browser fallback

All primary pages now say:

```text
需要启用 JavaScript 才能使用工作台。
项目文件不会因此被修改；启用后重新加载即可。
```

The fallback explains both the requirement and the safety boundary. It no longer
mixes a terse English phrase into the default product surface.

A browser navigation to an unknown route now receives a real Manju page:

```text
这里没有这个页面
链接可能已经过期，或者地址输入有误。项目文件没有被修改。
[返回工作台]
```

API callers still receive the existing structured error envelope; only browser
HTML navigation uses this product fallback.

### First-run workspace is a product screen

The workspace picker now presents one clear first-run decision:

```text
打开或新建项目
```

It explains that Manju will restore the previous page and work position. The
screen is organized as:

```text
current project
recent projects
open existing project
create project
```

High-frequency copy is Chinese-first. Existing project paths remain visible for
a local single-user tool, but technical status chips use product language such
as `可打开` and `路径失效`.

The opening and creation forms now use the full available card width and reflow
to one column at narrow widths.

### A real layout defect was fixed

The first-run main container previously reused `.ws-wrap`, a class already owned
by the home page's compact project switcher. `app.css` therefore applied:

```css
display: inline-block
```

to the entire workspace page, shrinking what should have been a centered desktop
screen to a narrow content strip.

Wave 11 gives the page its own `.workspace-page` owner. The desktop workspace now
uses a stable 960 px maximum content width and the compact switcher retains its
existing behavior.

This was not cosmetic preference; it was a real cross-surface CSS ownership
collision.

### Failed project open/create attempts do not lose context

A failed first-run action now produces a persistent inline recovery surface:

```text
无法打开这个项目
请确认这是一个可访问的 .manju 项目文件夹，然后重试。
项目没有被修改，刚才填写的内容仍然保留。
[技术详情]
```

The behavior is explicit:

- the user-entered path/name remains in the form;
- the error receives programmatic focus;
- the message is an assertive live region;
- the next step is separate from the technical detail;
- the project is not modified;
- the error remains until the user acts again.

This avoids the common failure mode where a transient toast disappears and the
user has to reconstruct both the input and the cause.

### Glossary help is a native control

The existing Chinese-first glossary remains. Its `?` help affordance is now a
native button instead of a styled span with simulated button semantics.

It supports:

```text
hover
keyboard focus
click to pin open
click outside to close
Escape to close and restore focus
aria-expanded
24 × 24 px minimum target
```

The explanation is present in the button's accessible name and the visible
`role=tooltip` surface. User-supplied labels and glossary copy remain HTML-escaped.

### Toasts now reflect the consequence of the message

Both the SPA and server-rendered pages use the same product semantics:

#### Success

- `role=status`;
- polite announcement;
- 4.5 second lifetime;
- pauses on hover or focus;
- explicit close button.

#### Warning

- `role=status`;
- polite announcement;
- 8 second lifetime;
- explicit close button.

#### Error

- `role=alert`;
- assertive announcement;
- no automatic dismissal;
- explicit close button;
- still supports the historical click-to-dismiss convenience.

Identical messages arriving within 1.5 seconds coalesce instead of producing a
stack of duplicate notifications. Transient chatter is capped so it cannot cover
the workbench. Persistent errors are not silently evicted.

### Project-switch protection is a real safety dialog

When the server has switched to another project, an old page now announces:

```text
这个页面已经停止读写
服务器已经切换到项目「…」。这个页面仍属于先前项目，为避免误写已停止操作。
项目文件没有被这个旧页面修改。刷新后即可跟随当前项目。
[刷新并跟随当前项目]
```

The overlay is now an `alertdialog` with:

- `aria-modal=true`;
- labelled and described content;
- initial focus on the sole safe action;
- Tab constrained to that action;
- Escape deliberately unable to dismiss the protection;
- no mutation from the stale page.

### High zoom and narrow layouts are pinned

The shared shell and first-run workspace were rendered at:

```text
320 CSS px (1280 px desktop at 400% equivalent)
200% text scaling
400% text scaling
```

The product keeps:

- all six production stages;
- current execution mode;
- task entry;
- view setting;
- project identity;
- safe exit;
- stage subnavigation;
- primary content.

No tested surface produced page-level horizontal overflow. At very narrow widths
the app bar wraps, project controls receive their own line, stage navigation
remains a complete 3 × 2 grid, and subnavigation wraps instead of becoming an
unreachable horizontal strip.

### Shared shell noise was reduced

A small set of high-frequency labels now defaults to Chinese product language:

```text
正在加载
当前
创建
取消
确认花费并继续
确认花费并构建
通过 / 未通过
已启用 / 未启用
当前生效
```

The professional terminology mechanism still exists. This wave does not attempt
a risky whole-repository translation or remove raw diagnostic facts.

### One duplicate script load was removed

The series page loaded `/webclient.js` directly even though the shared glossary
head already loaded it. Duplicate loading could redeclare top-level client
bindings and produce avoidable browser console errors. The page now uses the
single shared script owner, and a rendered-document test pins that no primary
shell loads any script twice.

## Architecture boundaries

Wave 11 adds no persistent product state.

The new shared helper and product behavior do not:

```text
write project files
write GUI state beyond existing preferences
change selected_take
change shot approval
change Picture Lock
call a Provider
read credentials
call a model or external API
become a build input
become a cache identity
restore Manju MCP
introduce React or Electron
```

The skip link, toast observation state, tooltip open state and inline error state
exist only in the current browser document.

## Browser evidence

Evidence uses:

- actual Manju server renderers;
- actual application, page, workspace and glossary CSS;
- actual workspace, glossary and common JavaScript;
- Playwright Chromium with `page.set_content`;
- one loopback HTTP request to capture the real browser 404 body;
- no external network, Provider, credential or model call.

Machine-readable facts:

```text
wave11-tests/browser-accessibility.json
```

### First-run desktop

Screenshot:

```text
screenshots/wave11/workspace-desktop.png
```

The repaired `.workspace-page` uses the intended desktop width and presents
recent/open/create choices without an engineering console feel.

### Durable project error

Screenshot:

```text
screenshots/wave11/workspace-error.png
```

Verified:

- input value preserved;
- `ws-feedback` receives focus;
- live region is assertive;
- project protection copy remains visible;
- technical detail remains available;
- no project write occurred.

### 400%-equivalent reflow

Screenshots:

```text
screenshots/wave11/workspace-400-percent-equivalent.png
screenshots/wave11/shared-shell-400-percent-equivalent.png
screenshots/wave11/shared-shell-200-percent-text.png
```

Measured facts:

```text
workspace 320 CSS px: documentWidth 310, overflow false
shared shell 320 CSS px: documentWidth 310, overflow false
shared shell 200% text: overflow false
shared shell 400% text: overflow false
```

### Glossary help

Screenshot:

```text
screenshots/wave11/glossary-help.png
```

Click opens the visible help, `aria-expanded` becomes true, Escape closes it and
returns focus to the button.

### Durable feedback

Screenshot:

```text
screenshots/wave11/toast-feedback.png
```

Two identical success calls produce one success item. The success disappears
after its lifetime while the error remains until the explicit close action.

### Stale-project protection

Screenshot:

```text
screenshots/wave11/project-switch-protection.png
```

The sole safe action receives focus; Tab stays inside the dialog and Escape does
not expose a stale page for mutation.

### Browser 404

Screenshot:

```text
screenshots/wave11/not-found.png
```

The page remains navigable and explicitly states the project was not modified.

## Test evidence

Non-overlapping focused test files report:

```text
347 passed
```

Breakdown:

```text
shared accessibility / workspace / modes / shell / app chrome / pages  94
shared UX contract                                                    92
protocol and core GUI regression                                      67
existing product journeys                                             94
```

Full details:

```text
WAVE11_TEST_SUMMARY.json
wave11-tests/pytest-summary.txt
wave11-tests/pytest-files/
```

Static checks:

```text
python -m compileall -q src tests                 PASS
rendered app.js         | node --check            PASS
rendered common.js      | node --check            PASS
rendered glossary.js    | node --check            PASS
rendered pages.js       | node --check            PASS
rendered task-center.js | node --check            PASS
rendered workspace.js   | node --check            PASS
git diff --check                                  PASS
```

`ruff` is not installed in the current sandbox and was not fetched; it is not
reported as passing.

## Honest limits

Wave 11 does not claim:

- full repository pytest;
- full `tests/test_gui.py`;
- live localhost Playwright end-to-end navigation;
- Windows App-mode keyboard/mouse validation;
- Windows hard gate;
- same-SHA Ubuntu / Windows release evidence;
- real Provider, billing, cancellation or AI-video quality.

One broad combined server-heavy pytest command left sandbox orchestration waiting
although its component files complete independently. It is not used as evidence.
Every counted test file was rerun independently or in a bounded group and
produced a complete pytest summary.

## Zero-cost boundary

```text
real Provider calls:       0
external model/API calls:  0
credential reads:          0
paid calls:                0
free-tier calls:           0
```

## Next product priority

The product now has coherent journeys, home, review, task lifecycle, shared
language and high-zoom accessibility. The next highest-value wave is not another
workflow. It is measured performance and visual-regression hardening:

- establish one canonical set of browser journeys and screenshots;
- measure 12 / 100 / 300-shot interaction latency in the real shell;
- remove only proven rendering or polling bottlenecks;
- run the Windows App-mode keyboard and 200% text journey;
- then close the same-SHA Ubuntu / Windows release gate.
