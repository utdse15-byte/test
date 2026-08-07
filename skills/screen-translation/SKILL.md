---
name: screen-translation
description: 银幕转译：把已采用的戏剧事实转成 Scene Experience 与 Screen Intent 提案，不生成 Provider Prompt。
when_to_use: 当剧本需要转成画面、身体、空间、声音、画外与并置承担的银幕体验时使用；只产提案。
tags: [directing, task]
user_invocable: true
---

# Screen Translation

Use the existing SceneContract and nested ShotContract as the only authoring
truth. Look for what image, body, space, sound, offscreen action and juxtaposition
can carry before proposing a shot. Experience Beats are not shots. Keep
ambiguity, avoid fixed shot counts and fixed duration formulas, and emit a
`truth_patch_set` Proposal instead of editing truth or compiling a Provider
Prompt. Screen Intent is never a Prompt, picture SPEC or Expectations input.

## 什么时候不该用

已有 Screen Intent、只是编译可执行控制时转到 `prompt-craft`；需要评审已有文字或媒体时使用 `artifact-review`。本技能不执行 `manju build`，也不确认提案。

## Eval

1. 余波镜头即使没有新剧情信息，也因 experience beat 保留。
2. 没有 source、beat 或 justification 的漂亮空镜标为 unanchored。
3. 不把一拍强制变成一镜，不使用固定镜长或黄金三秒。
