> **历史阶段参考。** 当前交接请先读 [`FINAL_HANDOFF.md`](FINAL_HANDOFF.md)。下文中的分支、SHA、测试数和待办只描述当时状态。

# 交接：`budget.limit` 语义不一致（引擎按单次，界面按累计）

更新时间：2026-08-13
仓库：`G:\XXN\test`
当前分支：`codex/manju-zero-cost-g0`
基线提交：`071f091`（工作树干净，全量套件 6184 passed / 63 skipped / 0 failed）

本文件只覆盖**一个**未完成项。与 `IDE_HANDOFF.md`（零成本工作流）无关，互不冲突，可并行执行。

---

## 1. 一句话结论

`project.yaml` 的 `budget.limit` 在**引擎里是"单次构建上限"**，在**界面里被当成"项目一生累计上限"**显示。两边用同一个字段、同一个数字，得出互相矛盾的结论。

已确认这是**表现层缺陷**，不是引擎缺陷。**引擎行为一行都不要改。**

## 2. 证据（全部已核对，行号基于 `071f091`）

引擎侧 —— 单次口径：

| 位置 | 代码 | 口径 |
|---|---|---|
| `src/manju/build/graph.py:1515` | `if budget is not None and result.estimated_cost > budget:` | 只比**本次**计划的预估 |
| `src/manju/build/graph.py:1258` | `spent_so_far = {"total": 0.0, "currency": None}` | 每次构建**归零** |
| `src/manju/build/graph.py:1807, 1920, 1992` | `spent_so_far["total"] > float(budget)` | 只比**本次**实付 |
| `src/manju/build/graph.py:570` | `running = 0.0`（`_concurrent_generate`） | 并发路径同样归零 |

`graph.py` 全文 grep `spend_report|total_cost\(|cost_by_provider` → **无匹配**。引擎从不读账本。

界面侧 —— 累计口径（`spend_report()` / `project_status()` 的 `total` 来自 `.manju/state.sqlite` 全部历史行，账本不可用时退化读 take sidecar）：

| 位置 | 代码 | 问题 |
|---|---|---|
| `src/manju/gui/cockpit.py:357-364` | `if limit and float(total) >= 0.8 * float(limit)` → `"已超预算 (over budget):"` | 拿一生累计判"已超预算" |
| `src/manju/build/director.py:1624-1631` | `if limit and total >= 0.8 * float(limit)` → `"花费护栏提醒:已花 X / 上限 Y — 接近上限"` | 同上 |
| `src/manju/board/board.py:2176-2184` | `spend += f' / 预算 budget {budget}'` | 累计花费与单次上限并排，暗示同一标尺 |
| `src/manju/gui/page.py:1925-1932` | `"已花 " + total + " / 预算 " + limit` + 进度条 `total / limit` | 进度条把累计除以单次上限 |
| `src/manju/gui/page.py:2177-2181` | `"花费 " + b.total_cost + " / 预算 " + limitTxt` + 进度条 | 同上（数据来自 `gui/state.py:461-465` 的 `budget` 块） |

**可复现的矛盾**：某项目 `budget.limit: 100`，历史累计已花 120。驾驶舱红字"已超预算 120 / 100"，但一次新的 `manju build` 只要本次预估 ≤ 100 就照常放行并继续花钱。

## 3. 为什么不许改引擎（关键，别走错路）

直觉方案是"把引擎改成累计"。**不要这么做**，理由是它违反仓库自己的架构不变量：

- 累计额唯一来源是 `.manju/state.sqlite`，而 `CLAUDE.md` 与 README §disciplines 明确规定 `.manju`/SQLite 是**可随时删除的派生物**，且 **"derived reports are never build inputs"**。
- 让它成为付费闸门的输入 ⇒ 删一次 `.manju` 预算即满血复活（守卫看着更严、实际更不可信）。
- 账本为空时 `spend_report` 退化读 sidecar（`build/spend.py:_from_sidecars`），同一项目会出现两套数字，闸门行为随之漂移。

若将来真要做累计上限，必须先设计一个**非派生**的累计真相来源（写入文本层），并新增独立字段（如 `budget.total_limit`），那是新功能，不属于本次维护修复。

`DECISIONS.md:431` 的 `#7 budget is a soft limit` 只承认了"进行中的 N 个镜头仍会完成并计费"这一层软，**没有**记录过跨构建归零。README 与 `docs/CLI.md` 对 `budget.limit` 一字未提（已 grep 确认）。也**没有任何测试**钉住单次语义。

## 4. 要做的事

目标：让界面讲真话 —— 累计花费照常显示，但不再拿它跟 `budget.limit` 比大小判"超预算"；`budget.limit` 在文案里明确标注为"每次构建"。

### 4.1 `src/manju/gui/cockpit.py`（`_risks`，350-364 行）

把 budget 风险项的判定基准从"累计 vs 上限"改掉。累计额不再产生 `level: "error"` 的"已超预算"。建议改法：该风险项只在**本次构建**语境下有意义，而 `_risks` 没有本次构建上下文，因此**移除**这个 budget 风险项，或降级为纯信息行（`level: "info"`，文案改为"累计已花 X;单次构建上限 Y"，不含"超"字）。

注意 `_spend()`（393-404 行）继续原样透出 `budget_limit`，**不要动** —— `tests/test_cockpit.py:146` 钉住 `c["spend"]["budget_limit"] == 1.0`。

### 4.2 `src/manju/build/director.py`（`_budget_suggestion`，1617-1632 行）

同样问题。文案 `"花费护栏提醒:已花 X / 上限 Y — 接近上限"` 把累计和单次上限并置。改为明确两者不同标尺，例如 `"累计已花 X Y;每次构建上限 Z(不累计)"`，或移除该建议。保持 `action=None`（纯建议）。

### 4.3 `src/manju/board/board.py`（2176-2184 行）

`' / 预算 budget {budget}'` 改为标明单次，例如 `' · 单次构建上限 per-build limit {budget}'`。

### 4.4 `src/manju/gui/page.py`（1925-1932、2177-2181 行）

两处"已花 X / 预算 Y"+进度条。进度条 `total / limit` 是最误导的元素（累计÷单次上限），应移除进度条，或把两个数字拆成两行且不做除法。文案标注"每次构建上限"。

### 4.5 文档与记录（必做，这是本次的主要价值）

- README：`budget.limit` 补一句 —— 它是**每次构建**的上限，不跨构建累计；累计花费看 `manju spend`。
- `docs/CLI.md`：同上（当前完全没写）。
- `src/manju/build/spend.py:7-8` 的 docstring 现在写的是 `"cumulative spend trips the queue to waiting_user *while* running"` —— "cumulative"在这里指**本次构建内**逐笔累加，容易被读成跨构建。改写成无歧义措辞。
- `DECISIONS.md`：新增一条，记录"`budget.limit` 是单次上限"这个裁决 + 为什么不让可丢弃账本成为付费闸门输入（§3）。
- `STATE.md`：如仍列为开放风险，更新或移除。

### 4.6 测试（红先行）

现有需同步的测试：

- `tests/test_cockpit.py:143-147`（`test_...` 断言存在 `kind == "budget"` 且 `level == "warn"`）—— 按 4.1 的改法调整。
- `tests/test_cockpit.py:201`（断言 spend 块坏掉时**不出现** budget 风险项）—— 大概率仍成立，跑一遍确认。
- `tests/test_director.py:338-346`（`test_suggest_budget_near_limit`）—— 按 4.2 调整。

新增测试建议：一个钉住"引擎是单次语义"的回归测试 —— 账本里已有超过 `budget.limit` 的历史花费时，一次预估在限内的 `build` 仍然放行（把当前行为**显式**钉住，防止未来有人"顺手改成累计"而不走 §3 讨论）。

**不要放宽**任何现有测试。

## 5. 验证批次

按 `CLAUDE.md` 的 batch rule，改完至少跑：

```bash
python -m pytest -q -n auto tests/test_cockpit.py tests/test_director.py \
  tests/test_spend.py tests/test_c0911_gates.py tests/test_ledger_p1_nan_state.py \
  tests/test_ledger_p0_wiring_graph.py tests/test_c21g_gates.py
```

提交前跑全量：`python -m pytest -q -n auto`（4 核约 6-9 分钟）。

注意 `tests/test_c0911_gates.py:320-329` 钉住 `status --json` 的键集合含 `budget_limit`、`total_cost`、`currency` —— **键名不能改**，只改文案与判定逻辑。GUI 侧 `gui/state.py:461-465` 的 `budget` 块键名（`total_cost`/`currency`/`limit`）同理，`gui/server.py:141` 的空账本回退也保持 `budget_limit` 键。

**ffmpeg 必须是 6.1.1**（`ffmpeg -version` 先确认）。7.x 会让 `tests/test_transitions_looks.py` 间歇失败，那是 ffmpeg 回归不是本次改动，详见 `DECISIONS.md` `UX-REAL-USE #33`。

## 6. 边界

- 引擎付费闸门（`graph.py` 的 1515 / 1807 / 1920 / 1992 / 570 / 1258）**一行都不改**。
- 不让 `.manju/state.sqlite` 或任何派生报表成为 build/provider 的执行输入。
- 不新增公共 schema 字段（`budget.total_limit` 属于新功能，需先决策）。
- 不弱化既有 spend / SPEND-P0-001 fail-closed 行为（`build/spend.py` 的 `checked_cost` / `book_actual_cost` 不动）。
- 本次是纯表现层 + 文档改动，不动钱的行为。

## 7. 附：已完成部分（无需重做）

同一轮工作里已落地并全量验证通过，勿回退：

- `cc4234f` 测试编码修复 + secret 扫描器扩到 `tests/`
- `027e51e` GUI provider 开关走已验证的 `provider_manifest_dir`
- `bf9ccaf` P0-001 零成本 provider 出口策略
- `2e32fb1` FCPXML 成为第三种回环载体 / `e3becaa` 修其 format 解析
- `071f091` GUI 可达 `explain --graph`

另：roundtrip 缺失 baseline 语义已复核，**结论是现有实现正确**（"absent baseline 是合法模式、损坏 baseline 是硬拒绝"，`roundtrip.py:38` 的 `BASELINE_CORRUPT` + 各 diff 路径的 `if baseline_doc` 守卫），无需改动。
