---
name: direct-shot-source-patch
description: 导演源 patch：根据 SceneContract、ShotContract、Bible、参考所有权和 preflight，把一个导演判断落到最窄的真实源字段。
when_to_use: 当用户要改一镜的 beat、opening、endpoint、参考职责、实现方式或风险时使用；不直接生成媒体。
tags: [directing, task]
user_invocable: true
---

# 导演判断落地

所有创作判断必须落在真实 source：SceneContract、ShotContract、Bible asset、reference ownership、production method 或 provider params。不要写 reports 里的派生 JSON 期待引擎读取，也不要把一场的复杂意图塞进旧的 `action.main`、`quality.must_show`、`continuity.locks` 三个容器。

## 输入

- 当前 SceneContract、ShotContract、ShotSpec 和 Bible；
- `manju prompt <shot> --json` 的 spec hash、production checks 和 compiler trace；
- `manju refs <shot> --json` 的 reference lineage 与当前 ProviderManifest preflight；
- 用户要求和当前媒体观察（若已有 selected take）。

## 归属判断

1. 影响场次为何存在、进入/离开状态或不可逆变化：改 SceneContract。
2. 影响本镜目的、观众感知、opening、visible change、endpoint、表演、声音、风险或验收：改 ShotContract。
3. 影响角色/场景/道具稳定事实：改 Bible asset，并检查所有引用。
4. 影响参考的控制变量：改 reference binding 的 `controls`/`ignore`/lineage，交给 `prompt-craft` 重新编译。
5. 影响实现路径：改 control.production_method/motion_source；能力由 ProviderManifest preflight 决定。
6. 影响厂商参数：只写当前 manifest 允许的 generation.params，未知就停。

现有 ShotSpec 的兼容字段仍有明确职责：`action.main` 只镜像当前主要可见动作，`quality.must_show` 只放可观察验收项，`continuity.locks` 只放连续性事实，`camera.*` 承担机位/运动，`generation.params` 只放 manifest 允许的参数。丰富导演判断优先写入新合同，不发明模型不存在的旁路字段。

## 输出

输出一个窄 diff 或 `manju director propose` action，附三行：当前可见 beat、endpoint 落点、控制来源变化。纯建议可以写报告，但必须声明不影响 build；引擎永不读取 `reports/directing` 或其他 reports/ 文件来编译 Prompt。

## 失败条件

- 字段锁定或 proposal 已 stale：停，走 human proposal 流程，不 unlock；
- 意图不能压缩成一个可见变化：停，提出拆镜或 Edit Beat；
- ProviderManifest preflight 不支持：停，改 production method 或 provider 参数 proposal；
- 用户未授权新场次/镜头：不扩大 source 范围。

## 纪律

patch 后运行 `manju check` 和 `manju prompt <shot> --json`，确认 contract digest、production checks 与 refs lineage 更新。本技能不花钱、不 build、不 select、不直接调用 Provider。

## 什么时候不该用

只是承接 accepted media 真实结尾时用 `continue-from-accepted-take`；已经有 take 需要判断 disposition 时用 `review-take-and-route-repair`；只是执行 QC 修复 op 时用 `repair-loop`。

## Eval

1. 用户说“更紧张”时，先要求可观察 beat/endpoint，不能直接加形容词。
2. 角色认知变化应落在 SceneContract/ShotContract 的状态字段，不应伪装成 camera 参数。
3. locked source patch 必须转 proposal，不执行 unlock。
