# Product Polish R1 — Wave 8：创作 → 提案 → 分镜

日期：2026-08-14
分支：`codex/product-polish-r1`
基线：`d3413f6928f536d20da929eed6ac566ca8ef6bf7`
功能提交：`2307612 R1: connect the authoring journey`
范围：`/create`、`/director`、`/storyboard` 的产品旅程；不改变创作真相、Director Proposal 或分镜合同 owner。

## 1. 本轮判断

此前三个页面能力都已经存在，但用户仍需要自己推断三件事：

1. 是直接编辑项目文本，还是必须先走导演提案；
2. 外部 IDE 的建议是否已经进入项目；
3. 提案待处理时，当前分镜究竟依据哪一版内容。

本轮没有把创作漏斗再扩成更多阶段，也没有新增 `authoring plan`、事件图或数据库状态。正确产品模型是：

```text
直接写项目真相 ───────────────┐
                               ├─ 人工确认后的 canonical text ─→ 建立分镜
外部 IDE → Director Proposal ─┘
```

Director Proposal 是**可选审阅路径**，不是每次写作的强制步骤；但一份待处理的 `truth_patch_set` 会优先提醒，因为继续编辑同一批文件会让提案过期。

## 2. 唯一只读创作旅程

新增 `src/manju/gui/authoring_journey.py`，只消费现有 owner：

- `build.funnel.funnel_status()`：四份故事材料与分镜校验；
- `build.director.list_proposals()` / `_is_current()`：提案状态和当前性；
- `Project.shot_ids()`：现有镜头数量。

它只派生：

```text
1 创作真相
2 审阅提案（可选）
3 建立分镜
```

不会：

- 写项目文件；
- 保存第二份创作进度；
- 自动创建、确认或执行提案；
- 自动生成分镜；
- 调用 Provider；
- 读取凭据；
- 参与 build、cache 或 Picture Lock。

正常分镜页面不重复展示整条创作旅程。只有以下异常才出现紧凑交接提示：

- 创作 `truth_patch_set` 尚待人工决定；
- 提案存储暂时不可读；
- 已有镜头，但四份故事材料尚未完成。

提示明确说明：**当前分镜仍然基于已经落盘的项目真相。**

## 3. `/create`：从七段工程漏斗收敛为日常写作台

### 3.1 四份 canonical story material 成为主表面

首层只显示：

- 故事与结尾；
- 场次概览；
- 变化节拍；
- 剧本与声音。

每张卡片显示人话状态：

```text
已完成
正在写
尚未开始
稍后
需要检查
```

原始路径、字符阈值、SceneContract / ShotContract、media eligibility 和完整七阶段 funnel 全部保留在“生产资格与技术状态”中，不再长期占据写作空间。

### 3.2 写作安全

新增并实际验证：

- 文本变化后立即显示“未保存”；
- `Ctrl+S` 保存当前材料；
- 连续按两次 `Ctrl+S` 只产生一个保存请求；
- 离开有未保存内容的页面会触发浏览器保护；
- 切换到另一份材料前明确询问；
- 拒绝切换后草稿和焦点上下文保留；
- 接受切换后原草稿仍留在 DOM，不被覆盖；
- 编辑器已有未保存文本时，“生成模板”零网络、零写入；
- 保存或模板请求进行中时防止重复提交；
- 本地服务失败时按钮恢复，文本继续留在编辑器中。

### 3.3 外部 IDE 继续是可选协作者

“让 IDE 助手起草”进入渐进展开：

- Manju 不内置或调用 LLM；
- 命令仍可复制；
- 外部生成的草稿只有经用户检查并保存才成为真相。

## 4. `/director`：从动作表格变成真正可审阅的提案

### 4.1 待决定内容优先

页面顺序改为：

1. 创作路径；
2. “提案不会替你做决定”边界；
3. 待我决定；
4. 当前建议（仅在有价值时）；
5. 历史提案；
6. 高级 JSON 入口。

有待处理提案时，不再用“当前没有建议”面板把真正决策向下推。

### 4.2 `truth_patch_set` 直接展示修改前后

创作提案现在显示：

- 会修改多少个文件；
- 每个相对路径；
- 当前内容；
- 提案内容；
- 字符级删除 / 新增高亮；
- 折叠的统一 diff；
- 超长文件的有界预览；
- 原始动作、提案 ID、基线和成本放在技术详情。

所有内容均经过 HTML escape。超过 20,000 字符的正文不会在主界面完整渲染，完整提案仍保存在既有 Proposal store。

### 4.3 决定边界保持严格分离

```text
确认这份提案
    只冻结当前提案内容

执行已确认提案
    才会写项目或运行操作
```

已过期提案：

- 明确解释项目内容已经变化；
- 不显示确认或执行动作；
- 允许否决并保留历史；
- 要求按当前项目重新起草。

本地安全模式中的付费提案：

- 可以保留已确认状态；
- 执行按钮禁用；
- 不读取 Key；
- 不调用 Provider。

实际前端状态机验证：连续点击确认只产生一次 `/api/director/confirm`，没有 `/api/director/run`。

## 5. `/storyboard`：只在创作真相需要注意时交接

分镜页继续由 Wave 7 的镜头生产旅程拥有正常主表面。

本轮只增加异常提醒：

- 待处理创作提案；
- 提案状态读取失败；
- 已有分镜但故事材料尚未完成。

健康路径完全不增加额外 banner。提案 ID、hash 或内部状态不会泄漏到紧凑提示。

## 6. 响应式与视觉事实

截图使用项目真实 server renderer、实际 CSS 与本地 Chromium `page.set_content`；没有访问网络或 localhost 服务。

### 创建页

| 视口 | Wave 7 高度 | Wave 8 高度 | 变化 | 横向溢出 |
|---|---:|---:|---:|---:|
| 1440×900 | 1212px | 1117px | -95px | 无 |
| 390×844 | 1588px | 1540px | -48px | 无 |

虽然增加了三段创作路径，日常创建页反而更短，因为完整七阶段 funnel 和生产资格被渐进披露。

### 导演页

导演页高度增加是有意的：旧工程动作行被真实 before / after 审阅取代。窄窗口从 10px 横向溢出修正为无页面横向溢出，比较区在窄屏下改为单列。

证据：

- `REPORTS/product-polish-r1/screenshots/wave8-before/`
- `REPORTS/product-polish-r1/screenshots/wave8/`
- `REPORTS/product-polish-r1/WAVE8_LAYOUT_FACTS.json`

## 7. 测试结果

### Focused pytest

共 **176 passed**：

| 组 | 通过 |
|---|---:|
| Wave 8 创作旅程 | 18 |
| 创建页 | 17 |
| Director | 27 |
| 分镜 | 26 |
| 模式 / 应用外壳 / 首页 | 37 |
| GUI 页面 | 25 |
| Shell / 项目动作 | 20 |
| UX 定向子集 | 6 |

### 浏览器状态机

`REPORTS/product-polish-r1/wave8-tests/browser-interaction.json` 证明：

- dirty label 与 `beforeunload`；
- 模板不会覆盖未保存草稿；
- 拒绝 / 接受材料切换；
- 草稿跨切换保留；
- 双 `Ctrl+S` 仅一次保存；
- 双击 Director 确认仅一次确认请求；
- 确认从未触发执行。

### 静态

- `python -m compileall -q src tests`：PASS；
- 实际生成的 create/director JavaScript `node --check`：PASS；
- `git diff --check`：PASS。

### 明确未完成

仓库的 broader fast suite 在收集时发现当前环境未安装 `hypothesis`：

- `tests/test_crash_safety_campaign.py`
- `tests/test_fp_timebase.py`
- `tests/test_properties.py`

排除这三个文件后的重试在沙箱 20 分钟执行上限内没有形成 pytest summary，因此不冒充通过或失败。当前环境也没有 `ruff`。

本轮未运行：

- 全量 pytest；
- FFmpeg 全量门；
- Windows hard gate；
- 同一 SHA Ubuntu / Windows 双绿；
- live localhost Playwright E2E；
- Windows App 模式真实键鼠旅程。

## 8. 零成本与架构边界

```text
真实 Provider 调用：0
外部模型 / API：0
凭据读取：0
付费调用：0
免费额度调用：0
```

没有新增：

- 项目 schema；
- authoring plan truth；
- event truth；
- Proposal lifecycle；
- 数据库表；
- 内置 LLM；
- Manju MCP；
- React / Electron；
- 自动确认、自动执行、自动选片或自动锁片。

## 9. 下一步建议

前半程与后半程现在已经形成三条连续旅程：

```text
创作 → 提案 → 分镜
分镜 → 实验室 / 入库 → 审片
剪辑 → 字幕 → 混音 → 包装 → 导出
```

下一轮不应再增加流程层。最高价值方向是**首页 Cockpit 收口**：让首页把这三条旅程压缩成一个可靠的“现在最值得做”，并统一展示需要人工决定、正在运行和最近成果；同时避免把完整状态墙重新搬回首页。
