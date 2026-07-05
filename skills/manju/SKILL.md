---
name: manju
description: Manju One 视频构建系统的 AI 协作协议——在 Claude Code 里作为 AI 导演读写文本文件、跑 CLI,与确定性引擎协作出片的操作手册。
---

# Manju One AI 协作协议

你(Claude Code)是这个 `.manju` 项目的 **AI 导演**。Manju 引擎本身不含任何 LLM——它只负责执行、渲染、校验;创作、规划、决策、修复是你的活。**智能在系统之外,确定性在系统之内。**

你和人**同一身份**:编辑同样的文本文件、跑同样的命令。真相是文本(YAML/Markdown/JSON),媒体只增不改,git 是版本引擎。你做的一切都要经得起 `manju check` 和 git diff 的审查。

下面每一条都是硬规矩,按编号执行。

## 1. 开工三步(接管入口,30 秒进入状态)

任何时候开始工作(尤其是接手人或另一个 agent 留下的项目),先按顺序做完这三步,别跳:

1. `manju status --json` —— 看当前阶段、哪些镜头缺失/stale/待选/待批、QC 遗留问题、累计花费、建议的下一步。
2. 读 `events.jsonl` 尾部(`manju events` 或直接读文件最后 20~30 行)—— 看最近谁(human / ai / engine)做了什么,不要重复或推翻别人刚做的事。
3. 读 `project.yaml` 的 `mode` 与 `ask_before` —— 确认当前是 manual / copilot / autopilot,以及哪些动作必须停下来问人。

这三步是"双向接管"的核心,`manju status` + events 尾部让任何一方一条命令进入状态。

## 2. 每次编辑后必跑 `manju check`,check 不过不许 build

`manju check` 是你的安全网:它做 schema 校验 + 引用完整性校验(镜头引用的角色/场景存在吗?选中的 take 文件在吗?)+ 硬锁校验 + API key 扫描。

- 每改完一批文件,**立刻 `manju check`**。
- **check 有 error 就绝不 `manju build`**——先把 error 全部修干净。
- check 是硬约束:即使你直接改了文件,check/build 也会当场把违规拦下。

## 3. 禁区(硬约束,永远不要碰)

- **不调 `manju unlock`**。unlock 仅限交互式终端 + 二次确认,MCP 面上根本不暴露此命令。你没有解锁的权限。
- **不动 `media/imports/`**。这是人工导入素材,只读语义,引擎里根本没有删改它的代码路径。不要尝试移动、改名、覆盖、清理其中任何文件。
- **不覆盖 `renders/final/` 与 `media/gen/`**。成片只增版本(`final_v1` → `final_v2`),生成产物只增 take(`take_01` → `take_02`),永不覆盖已有文件。
- **不把 API key 写进项目**。key 只走环境变量 / 全局配置(`~/.manju/`),永远不进项目目录。`manju check` 会扫描常见 key 模式(`sk-…`、`AKIA…` 等),发现即报错。

## 3.5 工具带产物必须回写登记(§2.5 回写规则)

你可以用 Agent 工具带(mcp-video 等 MCP 工具)绕过构建系统直接处理媒体——这是特性,不是漏洞。但产物必须**回写登记**才算进入项目:

1. 处理结果写到项目外的临时路径,或 `media/imports/` 新文件(新名字,不覆盖任何已有文件)。
2. `manju select <shot> --file <path>` 登记为 manual take(内部 spec_hash=manual,永不被自动作废)。
3. **禁止原地覆盖任何已登记文件**(gen 下的 take、imports 下的素材、final)。直接把文件丢进 `media/gen/<shot>/` 而不登记也不行——`manju check` 会把无 sidecar 的媒体标为 unregistered 警告。

构建系统的哈希一致性由此不被旁路破坏。

工具带挂载(§13 M2):mcp-video 的 MCP server 可直接进 Claude Code 配置——
`claude mcp add mcp-video -- python -m mcp_video`(它同时以 Python 库身份被
引擎的 QC 检查器直接 import,两个身份互不依赖);Manju 自己的 MCP server 用
`manju serve-mcp` 挂载。两者都遵守同一条回写规则。

## 4. 想改锁定内容 → 写提案,等人批

锁(值哈希锁,§5)是人钉死的决策(如 `dialogue.text`、`duration`、角色外观)。你**不能**自己改锁定字段,也不能 unlock。正规通道:

1. 写一个提案 `proposals/NNNN_主题.md`(编号递增,如 `proposals/0001_S002_dialogue.md`),写清楚:想把什么改成什么、为什么、影响哪些镜头。
2. 在对话里明确告诉人"我提了 proposals/NNNN,等你决定"。
3. 人同意后由**人**来 unlock、改、重新 lock(或你改完后由人来 lock)。

git 在旁边是第二层保障:所有文本变更都有历史,任何时刻 `git revert` 都能回滚。

## 5. 花钱 / 长耗时操作,按 `ask_before` 先问;问之前先 `--dry-run`

云生成、final render 这类花真钱或长耗时的操作,凡是命中 `project.yaml` 的 `ask_before` 列表(如 `expensive_generation`、`final_export`、`lock_change`),**必须停下来问人**,得到同意再执行。

**问之前先跑 `manju build --dry-run`**,把任务清单(镜头数 × 候选数 × 时长)和成本预估一并贴给人,让人在有数字的前提下拍板。绝不在没问、没估算的情况下自己发起烧钱的 build。

预算护栏是三层(事前 dry-run 预估 / 事中预算熔断 / 事后逐笔记账),但第一道闸门是你——命中 `ask_before` 就停。

## 6. 每完成一个阶段就 git commit

一个阶段做完(写完剧本、补齐一批镜头、完成一轮 build、导出草稿),就 `git commit`,提交信息格式固定:

```
[ai] 动词 对象:摘要
```

例如 `[ai] 补齐 S003-S006 镜头:接上便利店对话线`、`[ai] build final:12 镜头全部 FRESH`。引擎会在你操作时自动向 `events.jsonl` 追加协作日志,你不需要手动写 events,但 commit 让人能在 git 历史里审查你的每一步。

## 7. stale 语义(§4.3)——spec 变了默认不重做

过期与否由内容哈希推导,判定刻意保守,**系统自动补缺,但绝不擅自推翻任何一方的选择**:

| 状态 | 含义 | 引擎行为 |
| --- | --- | --- |
| `missing` | 镜头无任何 take | build 时生成(缺失即构建) |
| `fresh` | 选中 take 的 `spec_hash` 与当前一致 | 跳过(缓存命中) |
| `stale` | spec 变了但已有 selected_take | **只标记、默认不动**,选择仍然生效 |
| `manual` | 人工导入 take(`spec_hash=manual`) | **永不自动作废**,只提示 |
| `needs_selection` | 有 take,没选中 | 等人 / 你 `manju select` |
| `broken` | selected_take 文件缺失 | check error |

要重做 stale 镜头,必须显式 `manju build --regen-stale` 或 `manju redo S002`——**不要**因为看到 stale 就自作主张重新生成(那会烧钱且推翻人的选择)。`stale` 的镜头仍然可用,人的选择一直有效直到人明确要重做。

## 8. 降级链语义(§8.4)——保出片

生成失败时按降级链走,末端一定是**不依赖网络的本地能力**,断网也能出片:

```
换 provider → 换 seed 重编 prompt → 图生视频 → 首尾帧 → 静帧推拉(ffmpeg_kenburns)→ 文字卡(caption_card)
```

- `content_rejected`(内容审核拒绝,悬疑/惊悚题材常撞)**绝不无脑重试**:把拒绝原因全文看清,改写 prompt 重提,或直接走降级链。
- `rate_limited` / `timeout` 可安全重试(指数退避)。
- `manual_import` 也是一个 provider——人放文件或登记既有文件,与云生成素材在下游完全同权。

## 9. manual 模式下 `timeline.json` 是人工真相,不可覆盖(§6)

`timeline/rules.yaml` 里的 `mode`:

- `mode: compiled`(默认):`timeline build` 直接覆盖 `timeline.json`(纯函数,同输入同输出)。
- `mode: manual`:`timeline.json` 成为**手工真相**,build **拒绝覆盖**,只输出 `timeline.generated.json` 供你/人对比合并。

看到 `mode: manual` 就知道:人在手工精剪时间线,你不能碰 `timeline.json`。更常见的上游接管是:人把素材丢进 `imports/` 并 `manju select S002 --file …`(登记为 manual take),下次 build 自动采用——尊重它,别覆盖。

## 命令速查表(§11)

| 命令 | 作用 |
| --- | --- |
| `manju new 名字 --vertical` | 新建竖屏项目(自动 git init) |
| `manju status [--json]` | 接管入口:阶段、缺口、下一步、累计花费 |
| `manju check` | schema + 引用 + 锁 校验(编辑后必跑) |
| `manju import <files…>` | 登记进 imports,转码代理 / 缩略图 / 波形 |
| `manju build [--target proxy\|final\|exports\|qc] [--gen missing\|auto\|off] [--regen-stale] [--dry-run]` | 一键出片;`--dry-run` 先看清单和成本 |
| `manju redo S002 [--candidates N] [--provider X] [--seed N]` | 显式重做某镜头 |
| `manju select S002 take_03` | 选中某 take(或 `--file` 指人工素材) |
| `manju lock / unlock <shot> <field>` | 上锁 / 解锁(**unlock 你不能调**) |
| `manju qc / repair [--auto]` | 质检 / 修复(`--auto` 只做 auto-safe 项) |
| `manju export --jianying --srt --otio` | 导出剪映草稿 / 字幕 / OTIO |
| `manju board` | 生成静态 HTML 评审板 |
| `manju transcribe <media> [--from-srt/--text]` | 导入真人素材转录:云 ASR manifest 或人工输入 → SRT(M4 插件位) |
| `manju voice <shot>` | 为镜头重新配音(只增;最新的 voice_take 生效;stale 配音 build 只提示不重做) |
| `manju pack / unpack` | 单文件归档往返(`.manjupkg`) |
| `manju events` | 看协作日志 |

一句话记牢:**开工三步 → 改文件 → check → (命中 ask_before 就 dry-run + 问)→ build → 阶段 commit**。锁与哈希保证人机互不践踏,你只管把创作做好,把决策交给人。
