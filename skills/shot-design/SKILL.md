---
name: shot-design
description: 镜头目的与实现方式设计，先确定观众必须感知的变化、opening、可见动作、endpoint、表演、物理、声音、风险和替代调度，再选择景别、运镜与剪辑接口。用于分镜、shot list、镜头复查和实现策略；不使用固定平均镜长或变化频率。
when_to_use: 拆镜头、写 shot list、规划景别/运镜节奏或做分镜复查时。
tags: [craft, reference]
auto: true
---

# 分镜设计 · 镜头语言(shot-design)

镜头设计先解决“为什么存在、从哪里开始、发生什么、在哪里结束”，再决定相机。景别与运镜服务已经明确的导演目的。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 镜头已经生成 / 拍好了,现在要评判画面 | `visual-qc-review` |
| 要写的是投给厂牌的提示词措辞 | `prompt-craft` |
| 要调的是整片的节奏与钩子分布 | `narrative-pacing` |

## 决策顺序

1. 镜头的叙事目的。
2. 观众必须感知的事实。
3. opening state。
4. 一个主要可见变化。
5. endpoint 与下一镜接口。
6. 表演和物理要求。
7. 声音事件和时机。
8. production method 与控制来源。
9. 主要风险与替代调度。
10. 相机、构图和验收条件。

## 相机选择

- **情绪/台词重镜** → `close_up`/`extreme_close_up` + 极简运镜(轻推或固定);别用甩镜抢戏。
- **交代环境/转场** → `wide`/`extreme_wide` 建立镜(establishing),配缓慢 `摇/移`。
- **动作/追逐** → `medium` + `跟/甩`,用**动接动**藏切点。
- **单人口播** → 根据论点、反应和视觉证据决定是否切换景别，不按固定频率机械切镜。
- **跨镜连续动作** → 守 **180° 轴线**(别翻轴),保持视线方向一致;道具/人物位置连续。

镜长由表演、对白与呼吸、动作完成、观众读取、前后剪辑接口和 Animatic 共同决定。没有通用平均镜长。

## 景别 / 运镜词汇(和 shots 的枚举对齐)

- **景别 shot_size**(`shots.camera.shot_size` 枚举):`extreme_wide` 远 · `wide` 全 · `medium` 中 · `close_up` 近 · `extreme_close_up` 特写。
- **运镜 movement**:推 · 拉 · 摇 · 移 · 跟 · 升 · 降 · 甩。
- **机位 angle**:平/俯/仰/过肩/主观。
- 相机字段只是镜头计划的一部分；不能替代目的、opening、endpoint、表演、声音、风险与替代调度。

## BEFORE / AFTER(业余 → 专业,附 WHY)

**例 1 — 一镜到底的口播**
- BEFORE:`S001 wide, 无运镜, 30s` 一个全景把整段说完。
- AFTER:按“论点建立 → 证据 → 人物反应”拆镜，并在 Animatic 中由对白和读取时间确定每镜时长。
- WHY:切镜必须承担信息或情绪，不以固定秒数制造变化。

**例 2 — 翻轴(经典新手错)**
- BEFORE:`S004` 人物看向画右,`S005` 反打后人物却看向画右(轴线翻了,像两人同向)。
- AFTER:`S005` 反打时视线转画左;`continuity.prev: S004` 标好,守 180° 轴线。
- WHY:翻轴让空间关系错乱,观众瞬间出戏;对话/对峙镜尤其致命。

**例 3 — 无意义乱推镜**
- BEFORE:`S006 movement: 推` 平白无故一路推近,情绪没到。
- AFTER:固定或轻微 `移`;把「推」留给情绪升点(台词爆点那一镜)。
- WHY:运镜要服务情绪;漫无目的的推拉/晃镜是「AI slop / 业余」的明显 tell。

**例 4 — 硬切跨对冲运动**
- BEFORE:`S007`(人向左跑)硬切 `S008`(人向右跑),动作方向对冲、跳切。
- AFTER:用**动接动**在同方向动作的遮挡点切,或加一个中性过渡镜。
- WHY:cut-on-action 藏切点;方向对冲的硬切制造跳切感,连续性断裂。

## 拷贝进工作笔记的清单

```
分镜设计自检 · 逐条勾
[ ] 每个镜头写清 shot_size + movement + angle?
[ ] 一场戏里景别有变化,没有一镜到底的口播?
[ ] 每镜目的、opening、主要变化和 endpoint 清楚?
[ ] 时长由表演、对白、读取、剪辑接口和 Animatic 决定?
[ ] 对话/连续动作守住 180° 轴线、视线方向一致?
[ ] 运镜服务情绪,没有无意义的推拉/晃镜?
[ ] 切点用动接动,没有方向对冲的硬跳切?
[ ] 跨镜道具/人物位置连续?(continuity.prev / locks 标好)
```

## 失败目录(smells + 在 Manju 里长什么样)

| 业余通病 | 在 Manju 的表现 / 抓法 |
| --- | --- |
| 一镜到底、景别单一 | `/storyboard` 分镜表景别列全是同一值;`manju status` 看单镜超长 |
| 翻轴 / 视线错乱 | 跨镜 `angle`/视线不一致;`continuity.locks` 未标 scene/轴线;`visual-qc-review` D/H 类初筛 |
| 无意义晃镜/乱推 | `shots.camera.movement` 与 `action.emotion` 不匹配;分镜复查(工作流 D) |
| 硬切跳切、方向对冲 | 相邻 shot 运动方向相反;`/edit` 时间线相邻镜缩略图审 |
| 道具/位置跨镜漂移 | `continuity.locks` 缺 `prop:`;`manju appearances` 查道具引用,`visual-qc-review` C 类 |

## Manju 落地(把镜头语言写进真相)

- **每镜镜头字段**:`shots/SNNN.yaml` 的 `camera.shot_size`(枚举)/ `camera.movement` / `camera.angle`;`action.main` / `action.emotion`。
- **连续性**:`continuity.prev`(上一镜 id)、`continuity.locks: [character:…, scene, prop:…]`——道具/轴线连续性钉在这里。
- **分镜工作台**:`/storyboard`(round U)REPORTS-§2 分镜表,角色 chips 来自资产矩阵、`来源=`routing、`状态/审批`两态;可批量审批/锁。
- **分镜图/关键帧**:`manju board scene <id> --grid 4|9`(4/9 宫格分镜图)、`manju board keyframes <shot> --n N [--scaffold]`(动作→关键帧节拍,加 `--scaffold` 才写入)。
- **顺序**:`shots/index.yaml` 的 `order`;改序写 `manju propose`(工作流 D)。
- 与 `narrative-pacing`(镜长/打断)、`prompt-craft`(把景别/运镜翻成 vendor 提示词)配合。

## 验收 eval

1. 给一个单景别口播,是否先按论点、证据和反应判断是否拆镜，而非固定频率切镜?
2. 给一组对打镜,产出是否守住 180° 轴线、视线方向一致?
3. 给一个「推镜」滥用的分镜,是否把运镜收敛到只服务情绪升点?
