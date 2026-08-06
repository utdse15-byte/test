---
name: series-bible
description: 单片与剧集共用的连续性账本：维护角色、关系、知识、时间线、情绪残留和资产职责的状态增量。
when_to_use: 当跨场、跨镜或跨集需要维护人物/世界状态、伏笔和关系变化时使用；单片也可使用，不要求先变成系列项目。
tags: [directing, task]
user_invocable: true
---

# Series Bible

Bible 不是静态人物介绍，而是可追踪的状态账本。SceneContract 与 ShotContract 是单片和剧集共同的创作原语；系列层只增加规模、索引和跨集汇总。

## 五类状态

- identity：稳定身份和声音锚点。
- presentation：服装、发型、伤势、污渍、道具和位置。
- behavior：体态、节奏、眼神、手部习惯、距离和情绪外显。
- cognition：知道/不知道、相信、误解和当前意图。
- residue：情绪、疲劳、疼痛、呼吸和动作速度的残留。

关系、地点、道具、线索和时间线也用同样的“当前状态 → 本场变化 → 下一场要求”记录。每次只写真实变化，不重抄整个人物定义。

## 更新规则

1. 先读上一场 exit state、accepted media 的 bound observed endpoint 和当前 Bible。
2. 只把跨镜/跨场稳定的事实写进 Bible；瞬态姿势、镜头内表演和未验收生成结果留在 ShotContract 或观察记录。
3. 对每个变化写来源：SceneContract、approved decision、human observation 或已登记资产。
4. 下一场/下一集读取状态增量，检查是否与 opening、continuity 和角色五层一致。
5. 发生冲突时暂停编译，报告哪个源拥有权；不要用新一段描述静默覆盖旧事实。

## 系列索引

剧集项目可以额外维护：跨集时间线、伏笔/回收、关系图、资产目录和每集 state delta。单片不应因为没有“系列”标签而省略知识状态、关系状态或情绪残留。

## 什么时候不该用

只需创建一场的 entry/exit 合同时转到 `scene-design`；只需设计当前单镜头时转到 `shot-design`；只需确认生成媒体是否符合合同则用 `review-take-and-route-repair`。

## Eval

1. 下一集角色服装变了但 identity 未变，输出 presentation delta，不重定义角色。
2. 角色在上一集已经知道线索时，下一场不得把它写回 unknown；指出 cognition 冲突。
3. accepted take 的“手搭在门把上”是 transient endpoint，不把它升级成 Bible 永久属性。
