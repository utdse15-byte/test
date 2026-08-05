---
name: character-consistency
description: 管理角色跨镜、跨场和跨集的 identity、presentation、behavior、cognition 与 residue 连续性。用于建立角色、检查外观或行为漂移、继承知识/意图/情绪/身体状态，以及规划参考素材和运动压力测试；具体 provider、seed、参考数量与 LoRA 行为必须由当前证据决定。
when_to_use: 建/锁一个角色、要角色跨镜跨集保持一致、或人脸/服装出现漂移时。
tags: [craft, reference]
auto: true
---

# 角色连续性(character-consistency)

角色连续性不只是一张脸。每次检查 identity、presentation、behavior、cognition 与 residue 五层，并让下一场继承人物真实状态。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 单镜一次性的人物且没有连续性职责 | `shot-design` |
| 角色来自实拍素材(长相不由生成决定) | `manju import` + `manju select --file` 登记为 manual take |
| 要管的是跨集的人设档案与世界观 | `series-bible` |

## 五层连续性

1. **identity**：脸、身体比例、年龄感、声音身份。
2. **presentation**：服装、发型、伤势、污渍、道具。
3. **behavior**：体态、步态、视线、手部习惯、社交距离、情绪外显。
4. **cognition**：知道什么、不知道什么、相信什么、当前意图。
5. **residue**：情绪、疲劳、疼痛、呼吸和动作速度的残留。

## 决策树

- **反复出镜角色**：建立 identity、服装/道具、运动与情绪反应测试素材。
- **存在动作或道具风险**：先做 turn、walk、sit、reach、hold-prop 等压力测试。
- **只有外观一致但行为不属于角色**：仍判连续性失败，回到行为 Bible 或镜头表演要求。
- **真实 endpoint 偏离计划**：下一镜继承 observed state，或明确 reroll / rewrite source。
- **具体 provider 控制**：读取 ProviderManifest 与带日期证据；未知时不猜 seed、参考数量、权重或型号机制。

## 参考库原语(每个角色一份)

- **四视图 + 表情**:front / ¾ / profile / back(+ full-body / action)+ 4–6 表情,合成一张 sheet,「成为每次生成的输入」。
- **每张存 prompt + seed**:可复现、可追。
- **结构化元数据(CHAR-ID)**:UUID + 头/脸/发型/服装 + 配色(主/辅/点缀)。原则 = **角色资产标准化**。

Provider 型号映射、固定图数、seed 行为、LoRA 权重和可用率已从核心正文撤销。它们只有在当前 manifest 或可追溯 owner evidence 支持时才可使用。

## BEFORE / AFTER(业余 → 专业,附 WHY)

**例 1 — 每镜重描外观**
- BEFORE:每个镜头 prompt 都写「长黑发、红裙、圆脸的女孩」。
- AFTER:外观进参考库/角色 ID,镜头 prompt 只写动作 + `@角色ID`(或挂参考图)。
- WHY:反复文字描述会被模型每次重新解读 → 漂移;固定参考 + ID 才锁得住(呼应 `prompt-craft`)。

**例 2 — 一张参考图想通吃**
- BEFORE:只有一张正脸,侧身/背身镜全靠模型脑补。
- AFTER:根据实际镜头覆盖所需角度、全身动作、道具与表情测试，补齐足够的参考素材。
- WHY:参考集合由镜头职责决定，不由固定张数决定。

**例 3 — 按剧情顺序硬生**
- BEFORE:只看脸相似就通过，角色体态、视线和反应方式已经改变。
- AFTER:同时检查 behavior、cognition 和 residue；不属于角色的动作判为失败。
- WHY:外观相同不代表角色连续。

**例 4 — 跨集换装写死在分集**
- BEFORE:第 5 集脚本里重新完整定义角色长相 + 新衣服。
- AFTER:series bible 定外观,第 5 集只写 `character_id + wardrobe: 军装(delta)`。
- WHY:本地重定义外观 = Showrunner 式「每集重置角色」的经典翻车;只存增量才跨 60 集稳定(见 `series-bible`)。

## 拷贝进工作笔记的清单

```
人设一致性自检 · 逐条勾
[ ] identity / presentation / behavior / cognition / residue 五层都检查?
[ ] 参考素材覆盖实际镜头角度、动作、道具与情绪压力?
[ ] provider 策略来自当前证据，未知没有被猜成支持?
[ ] 镜头 prompt 只写动作、不反复重描外观/服装?
[ ] 失败 take 保留并记录真实观察?
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

1. 给一个只有正脸参考的角色,是否根据计划镜头补齐动作、角度、道具和情绪测试?
2. 给一个脸相同但行为突变的镜头,是否判为角色连续性失败?
3. 给一个跨集换装需求,是否用 bible 定义 + 分集状态增量而非本地重定义?
