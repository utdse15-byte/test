---
name: artifact-review
description: 作品评审：产生绑定来源或 exact media 的文字/银幕评审 evidence，不允许自我批准。
when_to_use: 当需要用文字 Lens 或 Screen Translation Lens 评审剧本、Animatic 或媒体并记录 evidence 时使用。
tags: [review, task]
user_invocable: true
---

# Artifact Review

Use the existing verification log. Text and screen findings must include
evidence, audience impact, production impact, requested change and fix owner.
AI visual rhythm observations are provisional; final Animatic Experience Review
requires the exact media binding and a human actor. Review evidence never edits
brief, script, contracts or shots directly.

## 什么时候不该用

需要设计场次或镜头时分别使用 `scene-design`、`shot-design`；需要修复已观察到的媒体失败时转到 `review-take-and-route-repair`。本技能不运行 `manju build`，不改正文，也不代替人类批准 Animatic。

## Eval

1. self_check 的 approved 被拒绝或降为 provisional。
2. 视觉节奏的最终批准必须同时绑定 exact media 与 human actor。
3. finding 写明 evidence、audience impact、production impact、requested change 与 fix owner。
