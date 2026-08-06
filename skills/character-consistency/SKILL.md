---
name: character-consistency
description: 角色连续性：同时维护 identity、presentation、behavior、cognition 和 residue，并把每一层转成可观察、可验收的状态。
when_to_use: 当建立角色、跨镜保持角色、处理表演漂移或检查下一镜是否继承人物状态时使用；不把外观参考当作全部人设。
tags: [directing, task]
user_invocable: true
---

# 角色连续性

连续性不是“每镜长得一样”，而是人物在故事和媒体中仍是同一个人。五层必须分开记录：

1. **identity**：脸、身体比例、年龄感、声音身份。
2. **presentation**：服装、发型、伤势、污渍、随身道具。
3. **behavior**：体态、行走节奏、眼神、手部习惯、社交距离、情绪外显。
4. **cognition**：知道什么、不知道什么、相信什么、当前意图。
5. **residue**：情绪、疲劳、疼痛、呼吸和动作速度的残留。

## 建立与使用

```text
静态 identity refs
→ costume / prop tests
→ turn / walk / sit / reach / hold-prop motion tests
→ emotional reaction tests
→ voice and pronunciation rules
→ proof shot
```

在 SceneContract 的 entry/exit state 中写 cognition、residue、body 和 props 的变化；在 ShotContract 的 opening、performance、physics、endpoint 中写本镜可观察表现。只把跨镜稳定的事实放进 Bible，把镜头瞬态放进合同或媒体观察；accepted take 的 endpoint 不得静默升级为角色永久事实。

## 参考职责

每个 reference 必须说明它控制哪一层和哪一个变量，例如 `controls: [identity]`、`controls: [presentation]`，以及 `ignore` 的范围。identity reference 不自动拥有动作、服装或场景；动作 reference 不自动拥有脸和知识状态。冲突交给 `prompt-craft` 的 ownership resolution，不能用更多形容词掩盖。

## 验收

看片时分别问：脸/声音身份是否稳定？服装和伤势是否继承？动作与手部习惯是否属于这个人？她是否只使用当前知道的信息？上一镜的恐惧、疲劳、疼痛是否在呼吸、速度、视线或距离中留下可观察 residue？记录证据引用，不以“感觉像”结论代替观察。

## 什么时候不该用

只是编排场次进入/离开状态时转到 `scene-design`；只是分配每镜控制来源时转到 `prompt-craft`；只是评审生成媒体时转到 `review-take-and-route-repair`。

## Eval

1. identity 稳定但服装、姿势和知识状态改变时，分别标 presentation、behavior、cognition，而不是判为同一性失败。
2. 上一镜结尾的疲惫应在下一镜 opening 中体现为呼吸或动作速度，不能只写“她很累”。
3. 两个 reference 都声明控制脸部 identity 时，报告 ownership conflict 并要求选择一个来源。
