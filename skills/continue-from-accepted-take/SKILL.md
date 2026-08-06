---
name: continue-from-accepted-take
description: 真实结尾续接：读取当前 accepted media 的哈希与 observed END，把它变成下一 ShotContract 的 opening；绝不按预期结尾假装续接。
when_to_use: 当要写/改承接上一镜的下一镜，或 prompt check 报 continuation 问题时使用。
tags: [directing, task]
user_invocable: true
---

# 从已验收媒体续接

续接源是当前 selected/accepted、非 stale 的媒体，不是上一镜 Prompt 的预期。先读取 `manju prompt <next> --json` 的 continuation view、`manju qc` 的 observed states、source media SHA-256 和 assurance digest。

## 输入

- continuation view 的 accepted media SHA-256、`observed_endpoint`、completed/reserved beats；
- 源镜头 assurance digest、下一镜当前 SceneContract/ShotContract 和 canonical refs；
- 当前 ProviderManifest 的 reference/edit/extend 能力与未决任务状态。

## 续接闸

以下全部满足才可提出 patch：

1. 源 take 当前 accepted/selected；否则 `CONTINUATION_SOURCE_NOT_ACCEPTED`。
2. SHA-256 可验证且与 view 一致；否则 `CONTINUATION_SOURCE_HASH_MISMATCH`。
3. 有 bound END 观察，visibility 不是 `UNCERTAIN`、`NOT_EVALUATED` 或缺失；否则 `CONTINUATION_ENDPOINT_UNOBSERVED`。
4. 源未 stale，且没有未决 submission 冲突。
5. 当前 ProviderManifest 能力支持所需参考/延展方式。

闸不过就停：先让人验收、重新出题回填观察、修 continuity.prev 或改 source；不要硬续。

## 输出

对下一镜现有 source 提出窄 patch/proposal：

- `contract.opening` 以真实 END 原话为起点；
- `continuity.prev` 指向源镜头，必要时绑定 `source_media_sha256`；
- 引用声明 `controls` 与 `ignore`，不要把源媒体自动当身份参考；
- 列出已完成、保留和不得提前展示的 beats；
- 引用 source hash、assurance digest 和 observation evidence。

若作者不接受真实结尾，应明确路由为 `REROLL` 或 `REWRITE_SOURCE`，而不是静默写回原计划。patch 后重跑 `manju prompt <next> --json`，确认 continuation checks 清零；本技能不调用 Provider、不 build、不花钱。

## 失败条件

- 任一 continuation gate code 存在：停，先补验收、哈希或 END 观察；
- 源媒体 stale/被替换：重新评审并绑定当前字节；
- 下一镜合同被锁：走 `manju director propose`；
- 作者拒绝真实 endpoint：显式选择 REROLL 或 REWRITE_SOURCE。

## 纪律

accepted media observed endpoint 必须逐字映射到下一 ShotContract.opening。本技能不调 Provider、不 build、不花钱；continuation view 只供 agent 阅读，绝不直接写入 provider request。

## 什么时候不该用

上一镜没有 accepted take 或只是评审已有媒体时转到 `review-take-and-route-repair`；要重新决定当前镜头表现什么时转到 `direct-shot-source-patch`；只改台词/时长时直接定点编辑并跑 `manju check`。

## Eval

1. 预期结尾是“抬头”而真实 END 仍低头时，下一镜 opening 必须从低头开始。
2. endpoint 缺失或 stale 时拒绝续接并给出补证步骤。
3. 真实结尾与剧本冲突时，输出 REROLL/REWRITE_SOURCE 选择，不伪造连续性。
