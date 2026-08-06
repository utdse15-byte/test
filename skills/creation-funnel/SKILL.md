---
name: creation-funnel
description: 从故事意图到可批准生产的创作漏斗：先建立场次和镜头合同，再用临时音频、Animatic、Proof Shot 和 Proof Scene 逐级验证。
when_to_use: 当从 brief/剧本开始规划新片、重建生产顺序或判断能否扩大花费时使用；不替代单镜头设计。
tags: [directing, task]
user_invocable: true
---

# Creation Funnel

漏斗的目标是把创作判断逐步变成可审查合同，而不是把 storyboard 完成当成可付费生产。默认流程：

```text
brief → ending → scene map → script → SceneContract → ShotContract
→ assets/motion tests → TEMP_AUDIO_FOR_ANIMATIC → Animatic
→ Proof Shot → Proof Scene → generation/review/experiment/source rewrite
→ picture lock → deterministic assembly → FINAL_AUDIO_FINISHING → delivery
```

## 规划顺序

1. `brief` 写主角、欲望、阻碍、选择和后果。
2. 先写结尾和不可逆变化，避免中段堆事件却没有方向。
3. 用场次地图拆状态变化，不按“一节拍一个镜头”拆分；一个 Narrative Beat 可以由多个镜头完成。
4. `script` 写动作、对白、声音事件和沉默。
5. 用 `scene-design` 把进入/变化/离开状态落成 SceneContract。
6. 用 `shot-design` 把每镜的目的、opening、一个主要可见变化、endpoint、表演、声音、风险和实现方式落成 ShotContract。
7. 建立角色、场景、道具、声音资产，先做必要的 costume、动作或运动压力测试。
8. `TEMP_AUDIO_FOR_ANIMATIC` 只验证完整播放、对白时长、停顿和声音落点；不能当最终音频。
9. Animatic 通过人类观看后，先做 `proof_shot`，再做按顺序绑定媒体的 `proof_scene`。用 `manju production status` 检查阶段。
10. 生成后的每轮都走 review → KEEP/FIX_IN_POST/EDIT_DONT_REGENERATE/REROLL/REWRITE_SOURCE；非 KEEP 只改一个主要变量并记录 Experiment Memory。
11. 只有满足 readiness 才 picture lock；最后执行 `FINAL_AUDIO_FINISHING` 与确定性装配。

## 三种 beat

- **Narrative Beat**：故事状态变化，可能跨多个镜头。
- **Generation Beat**：一次生成只承担一个主要可见动作或不确定性。
- **Edit Beat**：剪辑中一次信息、情绪或声音落点，可以由 cutaway、反应或声音先行完成。

不要把三个概念混为“镜头数量”或固定镜长。时长由表演、对白与呼吸、动作完成、观众读取信息、剪辑接口和 Animatic 共同决定。

## 花费与证据

- `manju build --dry-run` 先给任务数和估算；命中 `ask_before` 必须等 human。
- `AUTHORING` 只能写合同和本地预演；`PROOF_SHOT_READY` 只能扩到明确的 proof shot；`PROOF_SCENE_READY` 只能在 proof scene 顺序和媒体摘要批准后进入有限 paid video；`BULK_READY` 才允许全片批量。
- `--yes` 只确认花费，不替代 Animatic、Proof Shot、Proof Scene 或内容批准。
- 没有真实 provider、真实素材或 human approval，只能报告“未验证”，不能伪造生产证据。

## 交付检查

```text
manju check
manju production status --json
manju build --dry-run
manju qc
git diff
```

## 什么时候不该用

只是修改一个镜头的 opening、endpoint 或控制来源时，转到 `shot-design` 或 `direct-shot-source-patch`；只是处理 QC finding 时转到 `repair-loop`；只检查闸门时用 `manju production status`。

## Eval

1. 给一个有欲望、阻碍和后果的 brief，先产结尾和 SceneContract，再产 ShotContract，而不是直接写 Prompt。
2. 对含临时音频的 Animatic，允许节奏判断但拒绝宣称 final audio 或 bulk ready。
3. proof shot 通过而 proof scene 未批准时，拒绝扩大到全片付费。
