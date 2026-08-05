---
name: error-codes
description: Manju CLI `--json` 失败信封的 code 词表与分类——按「你该做什么」把带专门 code 的失败分成改输入 / 修真相 / 停下来问人 / 等一下再试四类,并说明默认的 error 码是「未分类」而非可分支类别。触发词:报错、失败、error、code、退出码、非 0、exit code、命令失败、json 错误、怎么处理这个错。
when_to_use: 一条 manju 命令失败了、要决定重试还是改输入还是问人时;或在写自动化循环、需要按 code 分支时。
tags: [reference, core]
user_invocable: true
---

# 错误码词表(error-codes)

任何带 `--json` 的命令**失败**时,stdout 上是一个信封,退出码非 0:

```json
{"error": "S099: 镜头不存在 shot not found (…) — 用 `manju status` 看现有镜头;…", "code": "unknown_shot"}
```

成功时是正常结果对象。**`--json` 的输出永远是 JSON**,你不需要为失败准备一条解析
ANSI 彩色文本的分支。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 命令成功了但结果不对(退出码 0) | 不是错误码的事,`manju explain`、`manju status` 看它到底做了什么 |
| 渲染失败要看 ffmpeg 到底被喂了什么 | `manju failures` 里的 info 行 + `.manju/render-debug/` 的中间输入 |
| 内容审核拒了要改写 | `prompt-craft`(别无脑重试,§8 降级链) |

## 先记住这一条:`error` 是「未分类」

CLI 里约 275 个失败点,只有 25 个带专门 code,其余全部落到默认的 `"error"`。

看到 `"code": "error"`,**读 `error` 文本**——它是中文人话,几乎总带补救命令——
**不要在 `error` 上做分支逻辑**,它不是一个类别,它是「还没分类」。

## 同一个事实 = 同一个 code

镜头不存在,不管你跑的是 `select` / `redo` / `voice` / `impact`,都必须是
`unknown_shot`。发现某个命令对同一事实给了不同的 code(尤其是命令名形状的 code,
如 `xxx_error`),那是 bug,值得报。

## 带专门 code 的失败:按「你该做什么」分四类

### 1. 改你的输入再试(参数/id 写错,项目没问题)

`bad_args` `bad_input` `bad_name` `bad_lang` `bad_mode` `bad_out` `bad_rate`
`unknown_shot` `unknown_provider` `not_found` `no_media` `plan_not_found`
`batch_not_found` `no_fixture` `missing_root` `exists` `out_of_project`
`ingest_review_no_items` `no_baseline`

典型:`unknown_shot` → 先 `manju status` 看现有镜头再重试;`exists` → 换个名字;
`out_of_project` → `--out` 必须落在项目内(引擎拒绝项目外写入)。

`no_baseline`(`manju roundtrip --apply`)是**安全拒绝**:载体不在 `exports/<kind>/` 原目录时找不到对账边车,计划会**漏掉真实改动、并多出没做过的**行,所以拒绝写入。把载体放回原目录,或重新 `manju export` 后再改。

### 2. 先修项目真相 / 先跑前置命令(不是你参数的问题)

`truth_parse_error` `no_timeline` `missing_plan` `no_project` `bad_plan`
`bad_align` `bad_archive` `bad_report` `bad_fixture` `ingest_invalid`
`pull_sheet_invalid` `shot_package_invalid` `refs_assign_invalid`
`batch_review_invalid` `unreadable_media` `plan_unreadable`
`toolchain_diff_unreadable` `impact_error`

这三个最常见,补救是固定的:

| code | 先做什么 |
| --- | --- |
| `truth_parse_error` | `manju check` 定位坏掉的 YAML 并修 |
| `no_timeline` | `manju build` 先编译时间线 |
| `missing_plan` | `manju qc` 先质检才有修复计划 |

### 3. 停下来问人,绝不自动重试(引擎在保护真相或钱)

`waiting_user` `interactive_only` `write_rejected` `tool_refused`
`migrate_refused` `adopt_refused` `bridge_refused` `bundle_refused`
`nothing_to_downgrade` `abandoned_by_user` `bad_capability`

这一类**不是错误,是闸门**。`waiting_user` 表示流程卡在人的确认上(§5 approve-
before-spend);`interactive_only` 表示该命令只在交互式终端可用(如 `unlock`),
MCP 面上根本不暴露。重试不会让它们变成成功,只会浪费一轮。

### 4. 等一下再试(瞬时/外部)

`build_locked` `tts_unavailable` `canary_submit_failed` `ingest_partial_failure`
`attach_remote_job` `provider_error`

`build_locked` 表示另一个 build 正在跑——等它结束,别强行并发。
`provider_error` 表示单发配音时供应商调用本身失败(网络断/HTTP 错/中途死):
失败已进 failures 存档;网络或服务恢复后重试即可,若提示未决提交先按
`manju tasks` 的恢复命令处理(绝不自动重提,§8/DR06)。

## 别和降级链的失败原因搞混

`content_rejected` / `rate_limited` / `timeout` 是 **provider 生成失败的原因**,
出现在任务账本(`manju tasks`)和降级链语义里(核心协议 §8),**不是** CLI 命令
本身的 code。两者的补救完全不同:

| | 出现在哪 | 例子 | 补救 |
| --- | --- | --- | --- |
| CLI code | `--json` 失败信封 | `unknown_shot` | 改输入 / 修真相 / 问人 / 等 |
| provider 失败原因 | `manju tasks`、run ledger | `content_rejected` | 改 prompt 重提 或 走降级链 |

## 写自动化循环时

1. 先判退出码:0 就按成功解析,非 0 才读 `code`。
2. `code == "error"` → 读 `error` 文本给人看,别自己猜分类。
3. 第 3 类(问人)出现时**立刻停**,把 `error` 原文交给人,不要重试。
4. 第 4 类可以退避重试,但要有次数上限,并且**花钱的操作即使可重试也要先看
   `ask_before`**(核心协议 §5)。

## MCP 工具面的 code(**面已冻结** — 存档参考,不是推荐路径)

> 店主 2026-07-31 冻结 MCP 面(「有 CLI 和 GUI 就可以了」,DECISIONS
> `TRISURFACE-FIX #25`)。`manju serve-mcp` 与本表原样保留并保持绿,但**日常
> 走 CLI**,上面那张 CLI 词表才是你要用的。本节留档给仍挂着 MCP 的场合。

MCP 工具失败时返回 `isError: true` + `{"error": …, "code": …}` — 同一信封,
但词表是工具面自己的(不要拿去和上面 CLI 的表混对):

| code | 什么事实 | 该做什么 |
| --- | --- | --- |
| `invalid_argument` | 缺参数/参数形状不对 | 读 tools/list 的 inputSchema 改调用 |
| `unknown_tool` | 工具名不存在 | 重读 tools/list,别重试同名 |
| `waiting_user` | ask_before 闸(金钱或外向制品) | 停下问人;人同意后带 `assume_yes: true` 重调 |
| `agent_profile_denied` | 当前 agent profile 不许该工具 | 走 payload 里给出的协作路径(通常是 propose) |
| `locked_field` | 改动碰到 §5 锁 | 写 proposal,永远别绕锁 |
| `rev_conflict` | 乐观锁:加载后文件已被改 | 重新 get_shot 拿新 rev 再保存 |
| `unknown_proposal` | 提案 id 不存在 | 重新 propose / 列出现有提案 |
| `canceled` | 构建/轮询被协作取消 | 按人意图处理,不自动重试 |

(此表由 tests/test_trisurface_round3.py 的 MCP code 测试钉住;CLI 表的
反捏造校验只扫上面的分类块,两张词表各自为政。**再说一遍:MCP 面已冻结**
—— 表里的 `get_shot` 等工具名只在仍挂着 MCP 的场合有意义,CLI 会话请回到
上面那张 CLI 词表。)
