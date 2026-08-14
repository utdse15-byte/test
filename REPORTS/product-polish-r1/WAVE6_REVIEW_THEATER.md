# Product Polish R1 · Wave 6 — Review Theater

日期：2026-08-14
基线：`8938fa70269464399159f485b7ff4daedd496431`
功能提交：`17ba7a7 R1: focus the review theater`
分支：`codex/product-polish-r1`
范围：高频审片旅程、优先队列、当前候选评价、镜头审批与渐进证据呈现
费用边界：严格零成本；真实 Provider、外部模型 API、凭据读取和付费请求均为 0

## 1. 产品判断

Wave 5 已把所有主要页面收进同一个应用外壳，但 `/review` 仍带有早期工程页的结构：多个镜头卡纵向铺开，评价、选用、镜头审批、修复、路由、QC 抽帧和外部 IDE 交接接近同一视觉权重；队列虽然存在，却不是一个持续可见、可直接定位的工作表面。

审片是 Manju 最高频、最需要持续注意力的环节。本波不增加新的判定、选择或锁片状态，而是把现有 owner 重组为一个明确的 Review Theater：

```text
当前候选 + 一个主要判断区 + 证据/其它候选
                         + 待审优先队列
```

用户每次只需要处理一个候选，同时一直看得见后续工作。所有自动化仍只提供证据；真正的当前选择、镜头审批和 Picture Lock 继续由现有核心与人类决定。

## 2. 四个事实明确分开

本波刻意把以下四件事持续呈现在同一张镜头卡上，但不合并语义：

| 产品事实 | 现有 owner | 本波行为 |
|---|---|---|
| 当前评价 | `status.take_notes[selected_take]` | “推荐 / 不推荐 / 有备注 / 未评价”；底层继续复用既有 `好 / 弃` note 前缀，可以清除且不改变当前选择 |
| 当前选择 | `status.selected_take` | 其它候选可显式“选用”；评价不会自动换选 |
| 镜头审批 | 现有 review state / approval owner | 单独“确认镜头通过”；已经通过时按钮禁用 |
| Picture Lock | 现有人工锁片流程 | 页面只说明边界，不自动锁片，也不把镜头审批冒充锁片 |

页面主说明现在明确写出：

> 评价、当前选择、镜头审批和锁片是四件独立的事。质检只提供证据，不替你做决定。

没有新增 review database、approval flag 或 Picture Lock 状态。

## 3. 已完成

### 3.1 聚焦式双栏 Theater

桌面队列模式采用：

- 左侧：当前镜头、主要判断、播放器、质检/批注和其它候选；
- 右侧：持续可见的审片队列；
- 只有当前镜头卡进入主舞台，其余镜头仍在 DOM/队列中，可直接跳转；
- 当前 rail item 与当前镜头同步高亮。

`render_review()` 仍然使用原有媒体、QC、批注、take-note、select、approve、repair 和 redo owner。GUI 只改变编排与文案。

### 3.2 优先队列

本次打开时，队列按当前事实生成稳定快照：

```text
待选
→ 待更新
→ 当前媒体的 blocker / QC error
→ 当前候选尚未评价
→ 已评价但镜头尚未通过
→ 无候选
→ 已通过
```

队列项显示镜头号、动作摘要和人话状态。当前媒体已经变化的旧 blocker 仍保留历史证据，但不会继续冒充当前 blocker。

“稍后处理”在队列模式下按这份优先队列前进，而不是退回 YAML 文件顺序。

### 3.3 进度只统计当前选择

旧候选的历史备注不会把新选中的候选误算成已评价。

进度现在表达：

```text
当前候选已评价 2 / 5
另有 2 个镜头尚未选择候选
```

分母只包含已有当前选择的镜头；没有选择的镜头单独列出。进度条使用 `role="progressbar"`、`aria-valuemin/max/now`，服务端首屏即有正确宽度，不依赖 JavaScript 才变得诚实。

### 3.4 一个主要判断区

播放器之前固定出现：

- **推荐并下一条**；
- 不推荐，查看其它候选；
- 稍后处理；
- 确认镜头通过；
- 有多个候选时显示“对比候选”。

“推荐并下一条”只写当前候选评价并前进；不会自动选择、审批，也不会自动 Picture Lock。

评价已经存在时，事实 chip 与“清除当前评价”会同步；撤回不会清除当前选择。

### 3.5 证据渐进展开

默认页面保留当前判断所需信息；低频技术动作进入“更多操作”：

- 重新生成；
- 本地修复；
- 查看路由；
- 清除当前评价；
- 复制给 IDE 助手；
- 详细下一步 / 内部解释。

QC 抽帧从第二个默认播放器退为“查看质检抽帧”证据区。其它候选独立显示，并明确说明“更换当前选择，不等于锁片”。

### 3.6 中文优先与外部 IDE 交接

高频界面使用：

- 待选、待更新、有阻塞、需复核、待审、已评价、已通过、无候选；
- 当前选择、当前评价、镜头审批；
- 质检与批注、其它候选、更多操作。

英文内部值继续保留在 title、data attribute 或专业术语层，不再默认中英并排。

“复制给 IDE 助手”的结构化文本明确分开：

- 当前评价；
- 当前选择；
- 镜头审批；
- 问题、QC 和下一步。

它不再绑定某个特定模型名称，也不要求 GUI 内建聊天。

### 3.7 窄窗口顺序

在不改变桌面双栏的前提下，窄窗口采用：

```text
进度与队列控制
→ 横向审片队列
→ 当前镜头
→ 主要判断
→ 播放器与证据
```

队列使用横向 scroll-snap，并在当前镜头变化时自动把 active item 滚进视野。镜头标题在 760px 以下改为单列，避免当前选择、评价和审批 chip 把动作摘要挤成狭窄右栏。

390×844 的静态真实 renderer 验证中，页面 `scrollWidth=380`，无整体横向溢出；主要判断位于可继续向下滚动的位置。

### 3.8 动态顶部偏移与窄窗口退化

应用外壳高度会随窄窗口、项目名、视图模式和内容换行变化。本波不再依赖固定猜测：页面 JavaScript 只测量实际全局导航高度，写入 `--rv-nav-h`，供桌面队列 rail 的 sticky 位置使用。队列进度和筛选本身已经收进 rail，不再维护一条重复的 workbar，也没有第二个偏移变量。

窗口 resize 时会重新同步。窄窗口下 rail、当前卡和判断区全部回到正常文档流，避免 sticky 元素遮挡播放器、标题或键盘 focus。

## 4. 保留的性能边界

- 仍然只有当前主视频激活真实 `src`；离开当前卡会卸载上一条流；
- 其它候选预览继续 lazy；
- 跨镜一致性 board 继续只在展开后生成；
- `/review` 首屏仍不因为本波新增第二套媒体探测；
- 队列排序只使用页面已经读取的 shot/take/QC/annotation facts；
- 浏览器队列位置和模式仍是可删除、按项目身份绑定的 UI state，不是项目 truth。

`tests/test_fp_review_lazy.py` 继续通过 10 条定向性能边界。

## 5. 可访问性

本波增加或明确：

- 队列 `<aside aria-label="审片队列">`；
- 队列项具有人话 `aria-label`；
- 当前阶段和页面外壳沿用已有 `aria-current`；
- 筛选 chip 和“只看当前镜头 / 浏览全部镜头”开关使用 `aria-pressed` 并实时同步；
- 进度条具有完整 ARIA 数值；
- 快捷键帮助可用 `?` 打开；
- 当前 rail item 自动进入可见范围；
- 760px 下标题和 facts 单列；
- 200% text 静态渲染无页面横向溢出；
- 技术折叠均使用原生 `<details>`，保留键盘语义。

## 6. 视觉证据

所有 after 截图来自项目真实 server HTML renderer 与仓库实际 CSS，通过本地 Chromium `page.set_content` 渲染；不是重新画的 mock UI。页面脚本不用于声称 live-server journey。

- `screenshots/wave6/review-theater-before-desktop.png`：Wave 5 基线；
- `screenshots/wave6/review-theater-after-desktop.png`：1440×900；
- `screenshots/wave6/review-theater-after-1280x720.png`：1280×720；
- `screenshots/wave6/review-theater-after-narrow.png`：390×844 首屏；
- `screenshots/wave6/review-theater-after-narrow-active.png`：窄窗口滚动到当前镜头；
- `screenshots/wave6/review-theater-after-200pct.png`：200% text；
- `screenshots/wave6/review-theater-after-layout.json`：尺寸和溢出事实；
- `screenshots/wave6/review-theater-demo-facts.json`：Demo 状态事实。

布局事实：

| 视口 | 页面横向溢出 | 当前镜头 | 队列项 | 主判断 |
|---|---:|---|---:|---|
| 1440×900 | 无 | S004 | 7 | 首屏可见 |
| 1280×720 | 无 | S004 | 7 | 首屏可见 |
| 390×844 | 无 | S004 | 7 | 向下滚动可达 |
| 1280×720 + 200% text | 无 | S004 | 7 | 向下滚动可达 |

## 7. 测试证据

focused pytest 共 **262 passed**：

| 测试组 | 通过 | 用时 |
|---|---:|---:|
| Review Theater + UX | 109 | 18.67s |
| GUI pages + review queue | 35 | 30.96s |
| App chrome + modes + home | 37 | 9.64s |
| Selection / review core regression | 69 | 16.79s |
| Review lazy / performance | 10 | 4.74s |
| QC consistency review | 2 | 2.25s |

日志与机器可读摘要：`REPORTS/product-polish-r1/wave6-tests/`。

### 离线交互证据

使用项目真实 server-rendered HTML 与实际 Review JavaScript，在不连接 localhost、
不触发真实 API 的情况下执行了一次离线交互：

- 默认优先队列进入 S006；
- 跳到 S002 后执行“推荐”，真实 POST payload 只写 `take-note`；
- 当前选择保持 `take_01`，没有调用 select；
- 撤回评价后 verdict 回到 pending；
- 快捷键帮助、队列 `aria-pressed`、active rail 和下一条切换同步；
- 页面没有横向溢出。

证据：`wave6-tests/review-theater-offline-interaction.json`。这证明实际前端状态机与
mutation 边界可以在离线 DOM 中工作，但仍不冒充 live HTTP / Windows App 旅程。

静态检查：

```text
python -m compileall -q src tests            PASS
rendered pages.js | node --check             PASS
rendered app.js   | node --check             PASS
git diff --check                              PASS
ruff                                           NOT RUN（环境未安装）
```

## 8. 零成本和隐私证据

```text
真实 Provider 调用：0
真实外部模型 API：0
凭据读取：0
付费调用：0
免费额度调用：0
```

本波只使用本地 fixture、项目 renderer、系统 Chromium 和 focused tests。

## 9. 未冒充完成的验证

本波没有声称完成：

- 完整 pytest；
- Windows hard gate；
- 同一 SHA Ubuntu / Windows 发布门；
- live localhost Playwright 完整旅程；
- 真实 Windows App 模式键鼠审片；
- 真实 Provider 或 AI 视频质量。

当前沙箱的 localhost Chromium 访问仍受管理员策略限制，因此视觉证据是实际 HTML/CSS 的离线渲染，不是 live HTTP journey。

## 10. 下一优先级

下一波应打磨**镜头制作阶段**，而不是增加新模型：

```text
分镜 → 镜头实验室 → 批量入库 → 回到审片
```

重点应是：保持当前 shot context、明确“计划 / 实验 / 导入”的边界、一个主操作、候选资格和进入审片的下一步；继续不新增第二真相、不自动选择、不自动锁片、不做真实付费。
