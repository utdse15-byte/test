# Product Polish R1 · Wave 4 — 连续成片旅程

日期：2026-08-13
分支：`codex/product-polish-r1`
功能提交：`2bc0df1`、`2097fa0`
范围：剪辑、字幕、混音、包装、导出五个既有页面的产品统一；零网络、零凭据、零付费。

## 1. 为什么做这一波

Wave 3 已把交换文件和回程基线的信任语义分开，但五个成片页面仍然像五个独立工具：
用户进入任一页面后，需要自己记住前后阶段，也不能在同一位置看到当前成片是否有效、
镜头是否具备进入锁片评审的资格。

本波没有增加新的成片状态、锁片状态或工作流真相。目标只是把既有 owner 以同一条只读
产品表面呈现出来：

```text
剪辑 → 字幕 → 混音 → 包装 → 导出
```

## 2. 唯一属主与边界

共享流程条只消费现有事实：

- `manju.build.exportstatus.deliverables`：只读取当前 final 的 missing / current / stale / problematic；
- `manju.build.readiness.media_eligibility`：镜头媒体资格和派生的 Picture Lock eligibility；
- 各页面自己的 edit/subtitle/mixer/packaging/export owner：页面内容与 mutation；
- GUI：中文文案、导航、状态降级和渐进披露。

明确没有做：

- 没有新增 schema；
- 没有持久化流程进度；
- 没有把 GUI 状态读回 build；
- 没有自动 Picture Lock；
- 没有把 `picture_lock_eligible` 文案写成“已经锁片”；
- 没有新增轮询；每次页面只在首屏后读取一次；
- 没有 Provider、外部 API、凭据或费用。

## 3. 已完成

### 3.1 五页共享成片流程条

新增 `src/manju/gui/finishing_journey.py`，定义唯一的五阶段顺序、中文标签、前后导航和
只读状态投影。以下页面现在都显示同一个流程条：

- `/edit` 剪辑；
- `/subtitles` 字幕；
- `/mixer` 混音；
- `/packaging` 包装；
- `/exports` 导出。

流程条明确写着“这里只展示，不会替你锁片或改项目”。

### 3.2 当前阶段与窄窗口

- 当前阶段使用 `aria-current="step"`；
- 桌面为五等分轨道；
- 窄窗口使用 3+2 的紧凑网格，五个阶段无需横向滚动即可同时看见；
- 上一步 / 下一步始终提供显式入口，因此阶段带不是唯一导航方式。

### 3.3 成片和锁片资格同屏

新增只读 `GET /api/finishing/status`。页面先诚实显示“正在检查”，首屏后读取一次：

- 成片：尚无成片 / 当前有效 / 待更新 / 有问题 / 待人工确认；
- 锁片：尚无镜头可锁片 / 镜头尚未完成 / 含仅预览镜头 / 待人工确认 /
  可进入锁片评审。

“可进入锁片评审”刻意不等于“已锁片”。真正的选择与锁片仍属于人类和现有核心。

两个 owner 独立降级：成片状态读取失败不会遮住媒体资格，媒体资格读取失败也不会遮住
成片 freshness。页面本身始终可编辑。

### 3.4 首屏性能边界

`/edit` 的普通 GET 继续不进行 finishing status 计算、不重新编译、不启动子进程。
流程条先画静态占位，`common.js` 在首屏后发一次只读请求。

### 3.5 五页共享同一应用外壳

导出页此前绕过了部分共享 chrome。现在五页统一拥有：

- 项目切换；
- 新手 / 专业模式；
- 专业术语开关；
- series banner；
- `common.js` 的项目切换和任务状态保护。

### 3.6 中文优先

默认标题和高频编辑控件改为中文：

- 剪辑；
- 字幕；
- 混音；
- 包装；
- 导出中心。

英文 `Edit / Subtitles / Mixer / Packaging` 以及 `Trim / Timeline / Lanes / SFX`
等工程术语仍在 DOM 中，但只由现有“显示专业术语”开关显示。导航中的历史“打包”
同步收敛为“包装”。

## 4. 测试证据

逐组测试均单独报告通过，共 **172 passed**：

```text
journey                  10 passed
shared shell              6 passed
edit page                19 passed
native-cut lanes         19 passed
edit-v3 affected paths    5 passed
export center            34 passed
finishing pages core     17 passed, 5 deselected
teaser warning            1 passed
roundtrip regression     38 passed
mode + first-paint perf  23 passed
```

测试日志：`REPORTS/product-polish-r1/wave4-tests/`

覆盖重点：

- 五页只有一份共享流程条；
- 每页 active stage 正确；
- 五页共享项目 / 模式 chrome；
- 空项目状态诚实；
- 两个 owner 独立失败；
- status endpoint 零项目写入；
- `/edit` 首屏不计算 status；
- common JS 懒读取与窄屏五阶段完整可见；
- FCPXML / OTIO / baseline / export center 无回归；
- 新手 / 专业模式无回归。

`tests/test_gui_finish.py` 的核心集合先报告 17 passed / 5 deselected；其中一条 teaser
warning 随后单独报告通过，所以最终只有四项既有 FFmpeg 像素类测试未运行：
`card_preview`、`frame_and_strip`、`packaging_cover_frame` 相关路径。完整
`tests/test_edit_v3.py` 也未在沙箱时限内结束；本波直接影响的五条 edit-v3 路径已单独
通过。本报告不把其余慢测试描述成通过，最终 Windows / FFmpeg hard gate 仍需在阶段
收口时运行。

静态检查：

```text
python -m compileall              PASS
rendered common/pages/edit/exports JS  node --check PASS
git diff --check                  PASS
```

当前环境没有可用 `ruff`，未声称执行。

## 5. 视觉证据

使用项目实际 HTML renderer、实际已发布 CSS 和实际 Demo project 状态离线渲染：

- `screenshots/wave4/finishing-journey-mixer-desktop.png` — 1440×900；
- `screenshots/wave4/finishing-journey-mixer-narrow.png` — 390×844；
- `screenshots/wave4/finishing-journey-exports-narrow.png` — 390×844；
- `screenshots/wave4/facts.json` — 视口、页面宽度与流程条尺寸。

窄窗口页面没有整体横向溢出，五个阶段采用 3+2 网格完整可见。

当前沙箱 Chromium 仍被管理员策略禁止访问 localhost，因此没有宣称真实 live-server
Playwright journey 已通过。HTTP endpoint 和 server-rendered 页面由自动测试覆盖，
像素证据来自真实 renderer + CSS，而不是另造 mock UI。

## 6. 零成本与隐私

```text
真实 Provider：0
真实外部 API：0
凭据读取：0
付费请求：0
```

## 7. 后续优先级

下一波应继续打磨同一条成片旅程，而不是扩 AI：

1. 打磨窄窗口下的全局应用导航与新手提示密度；当前成片流程本身已完整，但顶部 chrome 仍偏拥挤；
2. 将导出后的外部精剪返回状态接入成片阶段，但仍然只读、append-only、人工采用；
3. 在 Windows App 模式走一遍真实键鼠旅程并补 live screenshot；
4. 阶段收口时运行同 SHA Ubuntu / Windows / pinned FFmpeg 门禁。

这一波的完成定义不是“成片功能全部结束”，而是五个已有工具第一次表现为一条连续、
可解释且不越权的产品旅程。
