---
name: character-consistency
description: 跨镜头/跨集的人设一致性工艺——角色参考库(4–6 张 front/¾/profile/back + 表情)、seed 锁定、按相似度批量生成、各厂牌角色 ID 映射(Kling/Runway/Sora/Midjourney/LoRA)、50–70% 可用率的现实预期、漂移遮盖技巧。把 AI 角色一致性变成可执行流程,落到 Manju 的资产矩阵与参考图预算。触发词:一致性、人设、角色、character、consistency、漂移、drift、参考图、seed、LoRA、角色 ID。
when_to_use: 建/锁一个角色、要角色跨镜跨集保持一致、或人脸/服装出现漂移时。
tags: [craft, reference]
auto: true
---

# 人设一致性(character-consistency)

AI 最擅长把主角在第 3 镜变成另一个人。你的活:把角色标准化成一份**参考库 + ID**,让每一次生成都引用它,并接受「不是每条都能用」的现实,靠批量与挑选补齐。

## 决策树:按漂移风险选打法

- **主角、反复出镜、要跨集** → 建**角色资产库**:4–6 张多角度参考(front/¾/profile/back)+ 4–6 张表情,每张连 **prompt + seed** 一起存;条件够(~15–30 张、剧集固定 ~20 集)再训 **LoRA**(0.7–0.9 权重)。
- **配角、只出几镜** → 参考图 + seed 锁即可,别上 LoRA。
- **一批相关镜头** → **锁 seed**;**按相似度而非时间顺序批量**(近景→¾→远景→配角→建立镜),减少切换重解读。
- **人脸顽固漂移** → 短片 4–5s + cutaway 打断 + 单一 LUT 统一肤色/光;实在不行后期换脸。
- **跨集** → 外观只在 series bible 定义一次,分集只存**状态增量**(换装/受伤/情绪),见 `series-bible`。

现实预期:一支 15 分钟片 = **40–80 个镜头** @5–10s,每镜 2–3 个变体,**可用率约 50–70%**——所以要多生 + 会挑,别指望一次成。

## 参考库原语(每个角色一份)

- **四视图 + 表情**:front / ¾ / profile / back(+ full-body / action)+ 4–6 表情,合成一张 sheet,「成为每次生成的输入」。
- **每张存 prompt + seed**:可复现、可追。
- **结构化元数据(CHAR-ID)**:UUID + 头/脸/发型/服装 + 配色(主/辅/点缀)。原则 = **角色资产标准化**。

## 各厂牌角色 ID 映射(把参考库接到具体模型)

| 厂牌 | 角色一致机制 |
| --- | --- |
| Midjourney | `--cref` / `--cw`(v7 → Omni Reference) |
| Runway Gen-4 | References:单张高清正脸,「identity encoding」 |
| Kling 3.0 | 上传 3–5 张参考 → identity embedding(Elements ≤4) |
| Sora 2 | 「创建角色」API → character ID → prompt 里 `@角色ID` |
| LoRA | 训 15–30 张,0.7–0.9 权重,剧集固定 ~20 集最稳 |

## BEFORE / AFTER(业余 → 专业,附 WHY)

**例 1 — 每镜重描外观**
- BEFORE:每个镜头 prompt 都写「长黑发、红裙、圆脸的女孩」。
- AFTER:外观进参考库/角色 ID,镜头 prompt 只写动作 + `@角色ID`(或挂参考图)。
- WHY:反复文字描述会被模型每次重新解读 → 漂移;固定参考 + ID 才锁得住(呼应 `prompt-craft`)。

**例 2 — 一张参考图想通吃**
- BEFORE:只有一张正脸,侧身/背身镜全靠模型脑补。
- AFTER:补齐 front/¾/profile/back + 表情共 4–6 张,存进资产库。
- WHY:单视图撑不住转身/侧脸,多角度参考才让不同机位一致。

**例 3 — 按剧情顺序硬生**
- BEFORE:按 S001→S050 顺序逐镜生成,seed 随机。
- AFTER:锁 seed,按相似度分组批量(所有近景一批、所有远景一批),再回填顺序。
- WHY:相似镜同 seed 一起生,身份最稳;按时间顺序 + 随机 seed 最容易花。

**例 4 — 跨集换装写死在分集**
- BEFORE:第 5 集脚本里重新完整定义角色长相 + 新衣服。
- AFTER:series bible 定外观,第 5 集只写 `character_id + wardrobe: 军装(delta)`。
- WHY:本地重定义外观 = Showrunner 式「每集重置角色」的经典翻车;只存增量才跨 60 集稳定(见 `series-bible`)。

## 拷贝进工作笔记的清单

```
人设一致性自检 · 逐条勾
[ ] 每个主角有 4–6 张多角度参考(front/¾/profile/back)+ 表情?
[ ] 每张参考存了 prompt + seed?
[ ] 角色有稳定 character_id,映射到目标厂牌的 ID 机制(Kling/Runway/Sora/MJ/LoRA)?
[ ] 相关镜头锁了 seed、按相似度批量而非时间顺序?
[ ] 镜头 prompt 只写动作、不反复重描外观/服装?
[ ] 接受 50–70% 可用率:每镜多生 2–3 变体再挑?
[ ] 跨集:外观在 bible 定义一次,分集只存状态增量?
[ ] 顽固漂移:短片 + cutaway + 单一 LUT 已上?
```

## 失败目录(smells + 在 Manju 里长什么样)

| 漂移 smell | 在 Manju 的表现 / 抓法 |
| --- | --- |
| 主角跨镜变脸 | `visual-qc-review` A2(跨镜身份)/ A1(镜内变脸);参考图不足或 seed 未锁 |
| 服装/配色跳变 | `visual-qc-review` B1/B2;`manju refs` 清洁度 + 参考图预算不够 |
| 每镜重描外观致漂移 | promptlab `manju prompt <shot>` 看 prompt;改为动作 + 角色 ID |
| 参考图超厂牌上限被截 | `manju refs <shot>` 显示 selected/省略 + 影响(`limits.max_ref_images`) |
| 跨集人设重置 | series bible 未做单一外观源;见 `series-bible` 三层模型 |

## Manju 落地(把角色钉进资产真相)

- **资产矩阵**:`manju assets [show <id>]`——bible 上的 角色/场景/道具/配音/风格 读模型(别名/关系/appearances);角色记录即「角色库」条目,给它稳定 `character_id`、参考集(每图 prompt+seed;take 已存 spec_snapshot+seed)、各厂牌 ID 槽。
- **@提及**:`manju mentions [--check|--apply]` 把镜头文本里的 @角色/@场景 注册进 spec(尊重锁)。
- **参考图预算/清洁度**:`manju refs <shot> [--json]`——按厂牌分配 + 清洁度 QC + needs_vision。
- **镜头引用**:`shots/SNNN.yaml` 的 `characters: [引用 bible/characters]`、`continuity.locks: [character:linxia, …]`。
- **锁外观**:人钉死的外观字段走值哈希锁;你要改走 `proposals/`,永不 `unlock`。
- **跨集**:角色外观的单一真相在 series bible,分集存增量——见 `series-bible`、`series-breakdown`。

## 验收 eval

1. 给一个只有正脸参考的角色,是否补齐四视图 + 表情并存 prompt+seed?
2. 给一批 50 个乱序镜头,是否改成锁 seed + 按相似度批量?
3. 给一个跨集换装需求,是否用 bible 定义 + 分集状态增量而非本地重定义?
