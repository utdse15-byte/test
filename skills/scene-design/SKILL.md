---
name: scene-design
description: 戏剧场次设计：把故事状态变化写成可执行、可承接的 SceneContract，不把地点或镜头列表误当场次。
when_to_use: 当需要拆场、创建或修订 SceneContract，或相邻镜头缺少进入/离开状态时使用；先于 shot-design。
tags: [directing, task]
user_invocable: true
---

# Scene Design

场次是一次不可逆的戏剧状态变化，不是地点标签，也不是镜头容器。单片和剧集都使用同一 SceneContract；剧集只增加跨集索引。

## 决策顺序

1. 这场为什么存在：主角欲望、阻力和观众需要理解的变化。
2. 谁进入：每个关键角色的 `knowledge`、`intention`、`emotion_residue`、`body` 和关键 `props`。
3. 压力从哪里来：外部限制、关系压力、时间或信息不对称。
4. 什么发生后无法完全回到原状：写入 `irreversible_change`，必须是事件或决定，不写“更紧张”。
5. 谁离开时变成什么：更新 `exit_state`，包括知道什么、想做什么、关系、情绪残留和可观察身体状态。
6. 下一场必须继承什么：写入 `carry_forward`，并由下一场 entry state 或真实媒体 endpoint 接住。

## 合同边界

场次只写 `scenes/<id>.yaml`，不写 `shots` 字段。镜头成员由 `shots/index.yaml` 顺序和 `ShotSpec.scene_id` 派生；镜头的目的、endpoint 和实现方式交给 `shot-design`。

最小可解析示例（字段与 `manju.core.authoring.SceneContract` 一致）：

```yaml
format: manju.scene-contract/v1
id: SC001
title: 门外的决定
location_ref: hallway
time: night
purpose: 主角在被发现前决定带走证据
entry_state:
  linxia:
    knowledge: [知道证据在门内]
    intention: [取回证据]
    emotion_residue: [刚与同伴争执]
    body: [呼吸急促, 右手握门把]
    props: {evidence: pocket}
irreversible_change: [linxia 拿走证据并切断退路]
exit_state:
  linxia:
    knowledge: [知道同伴已经报警]
    intention: [从后门离开]
    emotion_residue: [恐惧被压成克制]
    body: [低头快走, 仍握着证据]
    props: {evidence: hand}
carry_forward: [证据在手中, 后门路线, 报警倒计时]
proof_scene: false
```

## 评审问题

- 同一地点若发生三次不同的选择、关系变化或知识变化，应是三个场次。
- 对话只有重复信息而没有选择、后果或状态变化，应指出删减或合并，而不是强行拆场。
- “悲伤/紧张”必须翻译成呼吸、视线、体态、动作速度、距离或停顿等可观察状态。

完成合同后运行 `manju check` 和 `manju production status`；只有合同有效才进入 `shot-design`。

## 什么时候不该用

已有场次合同、只是设计单镜头时不要用本技能；转到 `shot-design`。只是检查当前是否能付费或批准时用 `manju production status`。

## Eval

1. 便利店三次剧情若分别改变知识、关系、选择，输出三个 SceneContract。
2. 没有不可逆变化的对话，指出信息重复并给出合并建议。
3. 把情绪残留分别写成呼吸、视线、体态或动作速度，而不是抽象形容词。
