# Product Polish R1 · Wave 7 — 镜头生产旅程

日期：2026-08-14  
基线：`ca2c48b0e27d65e6876188a98aa2751e596423a7`  
功能提交：`703a3861e36585f605748075aa3e6829baa8c0cd`（`R1: connect the shot production journey`）  
分支：`codex/product-polish-r1`  
范围：分镜、镜头实验室、批量入库、审片深链和候选来源的产品连续性  
费用边界：严格零成本；真实 Provider、外部模型 API、凭据读取、免费额度和付费请求均为 0

## 1. 产品判断

Wave 6 已把审片收敛为稳定的 Review Theater，但镜头进入审片之前仍像三个彼此独立的工程页：

```text
分镜工作台
镜头实验室
批量入库
```

用户需要自己推断：应该先去哪里、实验室和入库谁在前、候选何时已经进入审片、历史评价是否属于当前选择。

本波没有制造错误的四步流水线。真实语义是：

```text
规划镜头
    ↓
获得候选 ── 镜头实验室：整理参考、提示词、试算和生成
    │
    └──────── 批量入库：加入外部生成、拍摄或配音素材
    ↓
人工审片
```

实验室和批量入库是两条**替代路径**，不是前后依赖。两者都只产生或追加候选；候选不会自动选用，评价不会自动锁片。

## 2. 唯一只读旅程 owner

新增：

```text
src/manju/gui/shot_journey.py
```

它只从现有事实派生：

- 镜头数量；
- 是否有 append-only 候选；
- 当前 `selected_take`；
- 当前选择是否有评价；
- 镜头是否已审批；
- 镜头文件是否损坏；
- 当前最值得处理的下一项。

它不会：

- 保存流程进度；
- 选择 take；
- 审批镜头；
- Picture Lock；
- 调用 Provider；
- 读取凭据；
- 写入项目；
- 成为 build input。

现有 shot YAML、take 目录、status 和 Review Theater 继续是唯一 owner。

## 3. 已完成

### 3.1 三页共享分支式镜头路径

`/storyboard`、`/lab`、`/ingest` 现在共享：

```text
1 规划镜头
2 获得候选：实验室 或 批量入库
3 人工审片
```

共享卡片同时显示：

- 当前阶段；
- 当前镜头和一句动作；
- 镜头 / 候选 / 已选择 / 已评价统计；
- 诚实的下一步；
- “候选不会自动选用，评价不会自动锁片”的边界说明。

当用户显式聚焦某一镜头时，下一步优先针对该镜头；该镜头已经完成时，才显示“**项目下一步**”，避免把其它镜头的动作误认为当前镜头的下一动作。

### 3.2 深链保持当前镜头

页面之间使用稳定 shot id，而不是数组位置：

```text
/lab?shot=S001
/ingest?shot=S001&role=take
/review?shot=S001
```

Review Theater 现在优先读取显式 `?shot=`，再退回 per-project localStorage 位置。由实验室或入库进入审片时，会落到用户刚刚处理的镜头，而不是被上次浏览位置覆盖。

shot id 和素材角色在入库页均经过现有项目列表 / whitelist 校验；未知值不会进入页面字段或路径。

### 3.3 分镜页从表格入口变成生产计划

分镜工作台保留现有表格、批量动作、锁和审批 owner，但增加了连续入口：

- 镜头编号直接进入该镜头实验室；
- 展开详情可“导入本地候选”；
- 展开详情可直接“去审片”；
- 空项目给出“去创作”，CLI 命令只在专业术语层显示；
- 狭窄窗口明确提示表格可横向查看；
- 状态、审批和锁定说明进入渐进展开。

### 3.4 镜头实验室从诊断墙收敛为一次只处理一镜

默认首层只保留当前决策需要的信息：

- 当前镜头；
- 本镜头专属参考；
- 当前视频提示词；
- Provider / 成本试算摘要；
- 动作可生成性；
- 生成候选；
- 当前和其它候选；
- 导入本地候选 / 去审片。

低频工程信息进入 `<details>`：

- 参考来源、缺失和质量检查；
- 其它提示词与路由 trace；
- 关键帧脚手架；
- `generation.prompt_override`；
- 详细 budget omission。

当前已选候选显示禁用的“当前选择”，不会再提供一个可重复触发 mutation 的“选用”按钮。

生成按钮改为：

```text
先试算，再确认生成…
```

它没有改变既有 dry-run、成本确认或未来 Provider 路径。

布局测量：

| 页面 | 基线高度 | Wave 7 高度 | 变化 |
|---|---:|---:|---:|
| 实验室 1440×900 | 1414px | 983px | -431px / -30.5% |
| 实验室 390×844 | 2521px | 2152px | -369px / -14.6% |

也就是说，即使新增了镜头旅程，实验室仍明显更短、更聚焦。

### 3.5 批量入库采用诚实的空、成功和失败状态

批量入库首层收敛为：

```text
选择文件
生成入库计划
开始新一批
```

“素材类型”和“目标镜头”进入匹配设置；从镜头深链进入时自动展开并预填：

```text
素材类型 = 视频候选
目标镜头 = S001
```

入库规则进入渐进披露。批次评审明确说明：

> 确认匹配不会自动选择镜头候选。

控件状态现在诚实：

| 状态 | 批次选择 | 全部确认 | 主信息 |
|---|---|---|---|
| 无批次 | 禁用 | 禁用 | 还没有可评审的入库批次 |
| 空批次 | 可用 | 可用 | 这个批次没有可评审条目 |
| API 失败 | 禁用 | 禁用 | 暂时无法读取入库批次，请检查本地服务后重试 |

入库完成后优先提供：

- 检查本批次；
- 若确实加入视频 take，则提供“去审片”并保持第一条镜头上下文。

任务栏的 ingest 后续动作也从含糊的“查看分镜”改为“检查本批次”。

### 3.6 历史选择不再冒充本次自动行为

旧批次中可能保留历史 staged selection。界面现在使用：

```text
历史选择
历史自动选择
```

并明确：

```text
当前入库不会自动选择候选
```

这保留历史证据，但不会让用户误以为当前入库会替他选片。

## 4. 视觉与浏览器证据

截图来自：

- 项目真实 server-side HTML renderer；
- 项目实际 CSS；
- 本地 Chromium `page.set_content`；
- `strict_zero_cost`；
- 同一份本地 Demo 项目。

不是重新绘制的效果图。

### 前后截图

```text
REPORTS/product-polish-r1/screenshots/wave7-before/
REPORTS/product-polish-r1/screenshots/wave7/
```

每组包含：

- storyboard desktop / narrow；
- lab desktop / narrow；
- ingest desktop / narrow；
- 对应 JSON viewport / scroll facts。

所有 Wave 7 页面：

```text
390px viewport
380px document scrollWidth
整页横向溢出：0px
```

详细比较：

```text
REPORTS/product-polish-r1/WAVE7_LAYOUT_FACTS.json
```

分镜和入库的窄页高度增加来自新增的方向说明与诚实状态，不是隐藏内容膨胀；实验室通过渐进披露抵消了流程卡并显著变短。

### 入库真实 DOM 状态机

使用项目实际生成的 ingest JavaScript 和 mock 本地 API 验证：

```text
REPORTS/product-polish-r1/WAVE7_BROWSER_FACTS.json
```

覆盖：

- 无批次；
- 一个空批次；
- API 失败；
- 控件禁用 / 启用；
- 信息文案；
- 390px 无横向溢出。

它不是 live localhost E2E，因此不冒充完整 HTTP / App 模式旅程。

## 5. 验证

focused pytest 共 **252 passed，80 deselected**：

| 测试组 | 通过 | 未选择 |
|---|---:|---:|
| Shot journey | 14 | 0 |
| Storyboard | 26 | 0 |
| Ingest core / GUI | 71 | 0 |
| Ingest batches | 39 | 0 |
| Shot lab | 25 | 1 |
| Review Theater | 17 | 0 |
| Review queue | 10 | 0 |
| App chrome / modes / home | 37 | 0 |
| UX navigation subset | 13 | 79 |

`test_generate_runs_through_jobs_runner` 在当前沙箱会长期等待，因此本组明确 deselect 1；没有把它包装成通过。

日志和机器可读摘要：

```text
REPORTS/product-polish-r1/wave7-tests/
```

静态验证：

```text
python -m compileall -q src tests                  PASS
rendered common.js       | node --check            PASS
rendered pages.js        | node --check            PASS
rendered storyboard.js   | node --check            PASS
rendered lab.js          | node --check            PASS
rendered ingest.js       | node --check            PASS
git diff --check                                  PASS
```

当前环境未安装 `ruff`，没有声称它已经运行。

## 6. 不变量与费用边界

本波没有：

- 自动选择 take；
- 自动镜头审批；
- 自动 Picture Lock；
- 新项目 schema；
- 第二套镜头进度；
- 第二套候选库；
- 内置 LLM；
- Manju MCP；
- React / Electron；
- 真实 Provider；
- 真实外部 API；
- 凭据读取；
- 付费或免费额度调用。

仍然保持：

```text
候选 ≠ 当前选择
当前选择 ≠ 评价
评价 ≠ 镜头审批
镜头审批 ≠ Picture Lock
```

## 7. 尚未冒充完成

本波没有宣称完成：

- 完整 pytest；
- 未 deselect 的完整 shot-lab 文件；
- live localhost Playwright E2E；
- Windows App 模式真实键鼠旅程；
- Windows hard gate；
- 同一 SHA Ubuntu / Windows 双绿；
- 真实 Provider 或 AI 视频质量验证。

## 8. 下一批判断

当前从镜头计划到人工审片的方向已经清楚。下一批最高价值不是增加生成能力，而是收敛**创作 → 导演提案 → 分镜**的前半程：

- 来源、场次和镜头计划形成连续入口；
- Proposal 默认展示来源、影响和 before / after；
- 原始 truth patch 和 hash 退到技术详情；
- 未确认不写 truth；
- 不建立第二套 authoring plan 或 event truth。
