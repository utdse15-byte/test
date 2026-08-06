---
name: narrative-pacing
description: 叙事节奏与格式特定注意力策略：根据明确选择的格式 profile 设计信息、动作、反应、停顿、声音和剪辑节奏。
when_to_use: 当需要做节奏 pass、安排信息揭示、沉默、声音先行或平台格式策略时使用；普通叙事默认不加载营销结构。
tags: [directing, task]
user_invocable: true
---

# 叙事节奏与格式特定注意力策略

先声明 profile，再选择节奏策略：

```text
NARRATIVE_FILM
VERTICAL_DRAMA
KNOWLEDGE_VIDEO
MARKETING_OR_COMMERCE
DOCUMENTARY
ARCHIVE_OR_FOUND_FOOTAGE
OWNER_DEFINED
```

## NARRATIVE_FILM 默认

- 场次压力和不可逆变化优先于“每秒刺激”。
- 让人物和观众何时知道信息成为节奏选择；必要时使用信息差，但不伪造事实。
- 用动作—反应、停顿、沉默、声音先行/滞后和视觉模式变化组织注意力。
- 用 Animatic 的真实播放测试阅读时间、呼吸、对白、声音事件和剪辑接口。
- Narrative Beat、Generation Beat、Edit Beat 分开；不要把每个节拍强行压成一镜。

## 其他 profile

只有用户或项目明确选择 `VERTICAL_DRAMA`、`KNOWLEDGE_VIDEO` 或 `MARKETING_OR_COMMERCE` 时，才考虑 hook、pattern interrupt、CTA、价值摘要等格式工具。它们是可撤销的上下文，不是普通叙事的硬门禁；不要写未经当前项目、平台或实验验证的百分比和固定时长。

## 节奏 pass

1. 读 SceneContract 的 entry/exit 和 ShotContract 的 opening/endpoint。
2. 标出信息首次可见、角色首次反应、动作完成、声音落点和剪辑切点。
3. 在 Animatic 中验证实际播放；若过短，先调整表演、对白、剪辑或 staging，再考虑数值时长。
4. 对问题只改一个主要变量，记录假设和观察结果；不要用“更快/更电影感”作为诊断。
5. `manju check` 后再 `manju production status`，节奏通过不等于 paid-video ready。

## 什么时候不该用

还没有场次状态或镜头 endpoint 时转到 `scene-design` 或 `shot-design`；只是音频响度和混音时转到 `audio-finishing`；只是 QC 发现后的具体修复时转到 `repair-loop`。

## Eval

1. 给普通电影叙事，选择 NARRATIVE_FILM，并拒绝自动添加 CTA 或营销 hook。
2. 给明确带货需求，才加载 MARKETING_OR_COMMERCE，并把 CTA 标成格式策略而非人物动机。
3. Animatic 显示对白能听清但动作未完成时，优先提出 staging/剪辑调整，而不是固定加长每镜。
