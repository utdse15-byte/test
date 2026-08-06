---
name: audio-finishing
description: 两阶段声音流程：先用 TEMP_AUDIO_FOR_ANIMATIC 验证节奏和声音事件，picture lock 后再做 FINAL_AUDIO_FINISHING 与交付检查。
when_to_use: 当需要临时对白/音效支撑 Animatic，或在 picture lock 后处理最终配音、混音、响度、字幕和交付时使用。
tags: [audio, task]
user_invocable: true
---

# Audio Finishing

音频是场次和镜头设计的一部分，但必须区分两个阶段。

## TEMP_AUDIO_FOR_ANIMATIC

目的：验证完整播放、对白时长、呼吸和停顿、声音事件、music cue、声音先行/滞后以及剪辑节奏。它可以是临时 TTS、scratch dialogue、临时音效或占位音乐。

- 读取 SceneContract 的压力/状态变化和 ShotContract.sound。
- 在 Animatic 中确认对白能说完、动作有时间完成、声音落点与 endpoint 一致。
- 若画外动作仅靠声音事件和画内反应就能完成信息，优先把它设计成 ShotContract.sound + Edit Beat，不强迫生成高风险的手/物接触镜头。
- 标记为 temporary；不得宣称 voice lock、final mix、final loudness 或 delivery ready。
- Animatic 字节或源变化后，原批准不再匹配当前版本。

## FINAL_AUDIO_FINISHING

只有 picture lock 后执行：

1. 重新确认最终对白、语言、speaker、voice reference 和 voice hash。
2. 生成/导入 final voice，检查发音、情绪、呼吸和角色 identity。
3. 对齐 cue，处理 source audio、对白、环境、音效和音乐层级。
4. 校验 ducking、峰值、响度、静音、削波、声道、字幕时间和交付格式。
5. `manju check`、build、`manju qc`，输出新 final 版本，不覆盖旧文件。

Provider 的 voice、格式、语言、成本和能力只从当前 ProviderManifest 与 authoring evidence 读取；不在技能中写固定厂牌参数。若 picture 或对白改变，相关 voice/mix 变 stale，应重新验证而不是沿用旧批准。

## 媒体观察

看片/听音时把声音观察绑定到当前媒体 SHA-256 和合同：cue 是否发生、对白是否完整、环境声是否支持空间、末尾声音是否能接下一镜。无法判断写 `UNCERTAIN`，不要根据 script 或 Prompt 假定实际听见。

## 什么时候不该用

只是设计镜头里的声音事件时转到 `shot-design`；已有音频 finding 需要选择修复 op 时转到 `repair-loop`；picture 尚未锁定却要求最终交付时先用 `manju production status` 检查 gate。

## Eval

1. 临时 TTS 的 Animatic 可以批准节奏，但必须拒绝标为 final audio。
2. picture lock 后对白变化导致 voice hash stale，应重新做 final voice 和 mix。
3. 没有当前 ProviderManifest 证据时，不猜某厂牌的固定格式或负向语法。
