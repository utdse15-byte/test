---
name: prompt-craft
description: 将已确定的镜头意图编译成 provider 执行指令，并检查参考控制冲突、未决变量与实验假设。只在镜头目的和可见动作已经确定后使用；涉及提示词、prompt、参考图、negative、provider 约束或生成实验时触发。具体型号能力与限制必须读取 ProviderManifest，没有当前证据时保持 unknown。
when_to_use: 镜头意图已确定，需要分配控制来源、编译执行提示词或检查 provider 未知能力时。
tags: [craft, reference]
auto: true
---

# 提示词编译与控制分配

Prompt 是镜头决定的派生执行物，不是第二份导演真相。先确认镜头要让观众看到什么，再把仍未被参考素材、预演、实拍或合成承担的变量编译成指令。

## 什么时候不该用

| 情形 | 去哪 |
| --- | --- |
| 镜头为什么存在、开始和结束状态尚未确定 | `shot-design` |
| 下一镜必须接住上一镜真实媒体的结尾 | `continue-from-accepted-take` |
| 已有媒体需要看片和修复路由 | `review-take-and-route-repair` |
| 使用手动素材或实拍，不需要生成指令 | `manju select --file` |

## 当前隔离纪律

1. 读取镜头作者事实、Bible 与当前媒体观察。
2. 用 `manju refs <shot> --json` 和 provider manifest 确认参考输入、能力、限制与成本。
3. 逐项分配 identity、costume、location、framing、lighting、pose、motion、camera 和环境响应的控制来源。
4. 只有显式声明的 reference transfer 才能接管变量；不要从文件名或画面猜 ownership。
5. Prompt 只描述未被其他来源控制的变量，并保留可见动作意图与 endpoint。
6. 每次生成写明一个主要不确定性和可观察的预期结果。
7. ProviderManifest 没有声明的能力保持 unknown。不要猜 negative 支持、参考数量、时长、seed 或型号语法。
8. `generation.prompt_override` 是人工逐字指令；不要自动拼接或改写。

旧型号公式已移到 `references/archive/legacy-provider-guidance-2026-08-05.md`，该文件标记为历史记录，不得自动加载或作为当前事实。

## BEFORE / AFTER

**控制冲突**

- BEFORE：参考图已经负责服装，Prompt 又重新指定另一套服装。
- AFTER：保留角色 ID 和当前状态，只编译动作、未被控制的镜头变化与 endpoint。
- WHY：两个来源争夺同一变量会让结果不可诊断。

**未知能力**

- BEFORE：凭经验断言某型号支持 negative、固定参考数量或固定时长。
- AFTER：读取 ProviderManifest；未声明时输出 `unknown` 和验证建议。
- WHY：易变型号事实不属于核心 Skill。

**不可诊断实验**

- BEFORE：一次同时改镜头、动作、参考图和时长。
- AFTER：保持其他条件不变，只改变一个主要变量并写出假设。
- WHY：失败才会形成可继承知识。

## 自检

```text
[ ] 镜头目的、可见动作和 endpoint 已确定？
[ ] 每个控制变量都有显式 owner？
[ ] 没有 reference 争夺同一 closed role？
[ ] Prompt 只包含未被控制的变量？
[ ] Provider 能力来自 manifest；未知仍是 unknown？
[ ] 本次实验只有一个主要变量？
[ ] prompt_override 未被改写？
```

## Manju 落地

- `manju prompt <shot> --json`：检查编译结果与启发式问题。
- `manju refs <shot> --json`：查看解析后的 reference、transfer 与 provider 预算。
- `manju providers show <id>`：读取机器能力与限制。
- `manju build --dry-run`：在 transport 前查看请求计划和成本。

## 验收 eval

1. Reference 已控制服装时，输出不得重复争夺服装。
2. Provider 未验证时，不得猜 negative、参考数量或时长。
3. 一次失败后的下一轮必须只改变一个主要变量。
