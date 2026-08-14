# Product Polish R1 · Wave 6 — Review Theater

日期：2026-08-14
基线：`8938fa70269464399159f485b7ff4daedd496431`
功能提交：`17ba7a79edf36a5bf2f6b1fe81e3ac025c80b719`（`R1: focus the review theater`）
分支：`codex/product-polish-r1`
范围：高频审片旅程、优先队列、当前候选评价、镜头审批和渐进证据呈现
费用边界：严格零成本；真实 Provider、外部模型 API、凭据读取、免费额度和付费请求均为 0

## 1. 产品判断

Wave 5 已经统一应用外壳，但 `/review` 仍保留早期工程页的使用方式：多个镜头卡纵向铺开，评价、选用、镜头审批、修复、路由、QC 抽帧和外部 IDE 交接接近同一视觉权重；队列虽然存在，却不是持续可见、可以直接定位的主要工作表面。

审片是 Manju 最高频、最需要持续注意力的环节。本波不增加新状态，不改变 mutation owner，而是把现有能力重组为一个聚焦式 Review Theater：

```text
当前候选 + 明确的人工判断区 + 当前媒体证据
                            + 持续可见的优先队列
```

系统继续负责显示事实和证据；当前选择、镜头审批和 Picture Lock 仍然由人类通过现有核心明确决定。

## 2. 四个事实不再混淆

| 产品事实 | 现有 owner | 本波表达 |
|---|---|---|
| 本次评价 | `status.take_notes[selected_take]` | 推荐、不推荐、有备注、未评价；可清除，不改变选择 |
| 当前选择 | `status.selected_take` | 只有明确“选用”其它候选才变化 |
| 镜头审批 | 现有 review / approval owner | 独立“确认镜头通过”；不等于锁片 |
| Picture Lock | 现有人工锁片流程 | 只在成片阶段完成；审片页不自动触发 |

页面长期说明：

```text
推荐 / 不推荐：只记录本次评价，不会换用候选。
当前选择：只有显式选用其它候选，当前版本才会改变。
镜头审批：确认镜头可以继续，但不会自动锁片。
```

持久数据仍使用兼容的 `好 / 弃 · rationale` 表达。GUI 只把它解释成“推荐 / 不推荐”产品语言，没有创建第二个评价数据库。

## 3. 已完成

### 3.1 聚焦式双栏 Theater

桌面宽度下：

- 左侧是当前镜头、三项决定状态、主要判断、播放器、当前媒体证据和其它候选；
- 右侧是常驻审片队列；
- 队列本身不可误折叠，不会留下一个占满高度的空栏；
- 当前队列项与当前镜头同步高亮；
- 队列模式只决定是否仅显示当前镜头，不改变任何项目事实。

`render_review()` 继续复用已有媒体、QC、批注、`/api/take-note`、`/api/select`、`/api/storyboard/approve`、repair 和 redo owner。

### 3.2 三项决定状态固定可见

每张当前镜头卡持续显示：

```text
当前选择
本次评价
镜头审批
```

例如：

```text
当前选择  take_01
本次评价  不推荐
镜头审批  待审
```

这些状态不再依赖多个分散 badge 推断，也不会用一个绿色对勾同时暗示“已评价、已选择、已审批或已锁片”。

### 3.3 评价动作改为“推荐 / 不推荐”

主要操作区现在是：

- **推荐并下一条**；
- **不推荐，查看其它候选**；
- 稍后处理；
- 确认镜头通过；
- 有多个候选时显示“对比候选”。

行为边界：

- “推荐并下一条”只写当前候选评价，然后沿当前队列前进；
- “不推荐”只写评价，不取消 `selected_take`，并把注意力带到其它候选；
- “确认镜头通过”只更新镜头审批，不触发 Picture Lock；
- 无当前选择时，评价和审批均禁用，用户先显式选择候选；
- `U` 只撤回最近一次评价，当前选择保持不变。

### 3.4 优先队列使用当前事实

队列排序在本次打开时生成稳定快照：

```text
待选
→ 待更新
→ 当前媒体上的 blocker / QC error
→ 当前候选尚未评价
→ 已评价但镜头尚未通过
→ 无候选
→ 已通过
```

关键诚实性：

- blocker 必须仍然绑定当前媒体字节才进入阻塞优先级；
- 媒体被替换后，旧 blocker 继续显示为历史证据，但标记“已过期”，不再冒充当前画面阻塞；
- QC error 和 blocker 即使镜头已有旧评价，仍然排在普通未评价项之前；
- “稍后处理”按优先队列移动，而不是退回 YAML 文件顺序。

### 3.5 进度只统计当前选择

旧候选的历史备注不会把新选中的候选误算成已评价。

当前表达：

```text
当前候选已评价 2 / 5
另有 2 个镜头尚未选择候选
```

分母只包含已经有 `selected_take` 的镜头；无选择和无候选单独说明。服务端首屏即输出正确进度宽度和完整 `progressbar` ARIA 数值，不等待 JavaScript 才变得诚实。

### 3.6 证据和高级动作渐进展开

默认保留当前决定需要的内容：

- 当前主播放器；
- 当前选择对应的 QC；
- 当前选择对应的媒体绑定批注；
- 其它候选及明确“选用”动作。

低频能力进入“更多操作”：

- 重新生成；
- 本地修复；
- 查看路由；
- 清除当前评价；
- 复制给 IDE 助手；
- 系统建议的技术命令。

QC 抽帧从默认第二画面退为“查看质检抽帧”。其它候选继续 lazy preview，并明确“更换当前选择，不等于锁片”。

### 3.7 窄窗口顺序

980px 以下：

```text
决定边界说明
→ 进度、筛选和横向优先队列
→ 当前镜头与主要判断
→ 播放器与证据
```

- 队列改为横向 scroll-snap；
- active item 变化时进入可见范围；
- 760px 以下主要动作重新排版；
- 480px 以下三项决定状态改成单列；
- 390×844 和 200% text 均无整页横向溢出。

### 3.8 保留性能边界

- 仍然只有当前主视频激活真实 `src`；
- 离开当前卡会卸载上一条媒体流；
- 其它候选继续 lazy；
- 跨镜一致性 board 继续在展开后生成；
- `/review` 首屏不新增 ffprobe、FFmpeg 或第二套媒体探测；
- 队列只消费页面已经读取的 shot / take / QC / annotation facts；
- 队列位置和筛选仍是可删除的浏览器状态，不是项目 truth。

## 4. 视觉证据

所有 after 截图来自项目真实 server HTML renderer 和仓库实际 CSS，经本地 Chromium `page.set_content` 渲染；不是重画的 mock UI。

- `screenshots/wave6/review-theater-before-desktop.png`：Wave 5 基线；
- `screenshots/wave6/review-theater-after-desktop.png`：1440×900；
- `screenshots/wave6/review-theater-after-1280x720.png`：1280×720；
- `screenshots/wave6/review-theater-after-narrow.png`：390×844 首屏；
- `screenshots/wave6/review-theater-after-narrow-active.png`：窄窗口滚动到主要判断；
- `screenshots/wave6/review-theater-after-200pct.png`：200% text；
- `screenshots/wave6/review-theater-after-layout.json`：尺寸和溢出事实；
- `screenshots/wave6/review-theater-demo-facts.json`：本地样片状态。

布局测量：

| 视口 | 整页横向溢出 | 当前镜头 | 队列项 | 主要判断 |
|---|---:|---|---:|---|
| 1440×900 | 无 | S004 | 7 | 首屏可见 |
| 1280×720 | 无 | S004 | 7 | 首屏可见 |
| 390×844 | 无 | S004 | 7 | 滚动后可见 |
| 1280×720 + 200% text | 无 | S004 | 7 | 滚动后可见 |

Demo 同时覆盖：待选、待更新、当前 QC error、当前 blocker、旧候选备注、新选择未评价、无候选和已通过。

## 5. 验证证据

focused pytest 共 **262 passed**：

| 测试组 | 通过 |
|---|---:|
| Review Theater + UX | 109 |
| GUI pages + review queue | 35 |
| App chrome + modes + home | 37 |
| Selection / review core regression | 69 |
| Review lazy / performance | 10 |
| QC consistency review | 2 |

日志和机器可读摘要位于：

```text
REPORTS/product-polish-r1/wave6-tests/
```

静态检查：

```text
python -m compileall -q src tests      PASS
rendered pages.js | node --check       PASS
rendered app.js   | node --check       PASS
git diff --check                       PASS
ruff                                    NOT RUN（环境未安装）
```

### 离线真实前端状态机

使用真实 server-rendered HTML 和实际 Review JavaScript，在不连接 localhost、不调用真实 API 的情况下，以 mock `post()` 执行：

1. 新会话默认进入优先队列，队首为 S006（待选）；
2. 下一条进入 S003（待更新）；
3. 点击 S002 并执行“推荐”，唯一 mutation 是 `/api/take-note`；
4. `selected_take` 始终保持 `take_01`，没有调用 `/api/select`；
5. `U` 把评价恢复为 pending；
6. `?` 打开快捷键帮助；
7. 队列 `aria-pressed`、active item 和进度同步；
8. 页面没有横向溢出。

证据：

```text
wave6-tests/review-theater-offline-interaction.json
wave6-tests/review-theater-offline-interaction.log
```

这证明实际 DOM 状态机与 mutation 边界，但不冒充 live HTTP 或 Windows App 旅程。

## 6. 零成本和隐私

```text
真实 Provider 调用：0
真实外部模型 API：0
凭据读取：0
付费调用：0
免费额度调用：0
```

只使用本地 fixture、项目 renderer、系统 Chromium 和 focused tests。

## 7. 未宣称完成

本波没有宣称：

- 完整 pytest；
- Windows hard gate；
- 同一 SHA Ubuntu / Windows 发布门；
- live localhost Playwright 完整旅程；
- 真实 Windows App 模式键鼠审片；
- 真实 Provider 或 AI 视频质量。

## 8. 后续建议

下一轮应统一：

```text
分镜 → 镜头实验室 → 批量入库 → 审片
```

让镜头从计划、试验、导入到人工判断成为连续旅程。继续不扩模型、不自动选择、不自动 Picture Lock，也不进行真实付费测试。
