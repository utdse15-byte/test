---
name: prompt-craft
description: AI 视频生成提示词工艺——分厂牌(Runway/Kling/Sora/即梦等)的提示词公式、一动作原则、参考图数量上限、颜色锚点、拆短片建议。把「写一段能出片的 prompt」变成决策树与 before/after,喂给 Manju 的提示词工作台(promptlab)与降级链。触发词:提示词、prompt、Runway、Kling、可灵、Sora、即梦、生成、运镜关键词、参考图、negative。
when_to_use: 为某个镜头写或改生成提示词、要把提示词投给具体厂牌、或 promptlab 报了单动作/时长问题时。
tags: [craft, reference]
auto: true
---

# 提示词工艺(prompt-craft)

同一个镜头,提示词写法决定能不能一次出片。你的活:按目标厂牌选公式、守住「一动作」、锁住外观与颜色,把结果写进镜头的生成配置,让 Manju 的 promptlab 校验通过。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 这一镜用的是手动素材 / 实拍 | prompt 根本不参与,`manju select --file` 登记即可 |
| 要决定的是「这一镜要表现什么」而不是「怎么写」 | `direct-shot-source-patch` |
| 下一镜必须接住上一镜的真实结尾 | `continue-from-accepted-take` |

## 跨模型铁律(所有厂牌通用)

- **所有模型都在同一处翻车**:手、快速运动、脸的连续性、错位音频。写 prompt 时主动规避:少露手、单一主动作、别让脸做剧烈运动。
- **一主 + 一次**:一个主运动 + 一个次运动;一个镜头只讲**一个动作**。多动作堆叠 = 糊。
- **拆短更稳**:超过 5 秒、单镜一镜到底、且有脸在画面 → 建议拆成 **4–5s** 短片再拼接(切点重置漂移)。首尾帧锁定 + 一致 seed/palette。
- **颜色锚点**:点名 **3–5 个颜色锚点**稳住调色板,防「发烧梦境」式中途变色。

## 决策树:投给哪个厂牌就用哪套公式

- **Runway Gen-4** → **描述运动,不描述外观**(外观由参考图/图片定):图设定场景,prompt 只写动作。**只用正向措辞**(不支持负向 negative)。一主 + 一次运动。
- **Kling / 可灵** → **Subject + Action + Context(3–5 元素)+ Style**;3.0「以镜头而非关键词思考」,**镜头语言前置**。`++元素++` 加权。**Elements 参考图 2–4 张(>4 会混淆)**。给运动端点(「settles back」收势)。相机参数是**风格化**表述,非光学精确。
- **Sora 2** → **shot-list 结构**:一个相机运动 + 一个主体动作,时间用节拍(beats)描述;**点名 3–5 个颜色锚点**稳调色板;**把两段 4s 拼成一个 8s**,别硬生成 8s。
- **图生视频 / 首尾帧**(降级链中段)→ prompt 写**动作**,不重述角色外观/服装(重述会触发重新解读、导致漂移);锁首/末帧。
- **人脸顽固漂移** → 短片(4–5s)+ cutaway + 后期换脸。

## BEFORE / AFTER(业余 → 专业,附 WHY)

**例 1 — Runway 写成了外观堆砌**
- BEFORE:`a beautiful woman, long black hair, red dress, detailed face, 4k, cinematic`
- AFTER:`she turns from the window and walks toward camera, slow dolly-in`(外观交给参考图)
- WHY:Gen-4 靠图定外观、靠 prompt 定运动;堆外观词只会和参考图打架、加剧漂移。

**例 2 — 到处写 negative(Runway 不吃)**
- BEFORE:`..., no extra people, no blur, no distortion`(Runway 无视负向)
- AFTER:正向改写目标画面:`a single person walking down an empty street`
- WHY:Runway 不支持 negative;把「不要什么」翻成「要什么」的正向描述。

**例 3 — Kling 一句话糊成一团**
- BEFORE:`一个女孩在雨里跑,然后停下来哭,镜头旋转,很唯美`
- AFTER:`Subject: 穿校服的少女;Action: 在雨中奔跑后停步喘息(settles back);Context: 深夜空巷、霓虹倒影(3–5 元素);Style: 电影感冷调,低机位跟拍`,`++雨++` 加权。
- WHY:Kling 要 Subject+Action+Context+Style 分层、镜头前置;多动作(跑+停+哭+旋转)堆一句必糊,拆成一主动作 + 收势端点。

**例 4 — Sora 硬生成 8s、调色乱**
- BEFORE:`8 秒长镜,女孩走过整条街,四季变化`(时长过长 + 多变化)
- AFTER:两段 4s 拼接;每段 shot-list:`camera: slow pan left; subject: walks 3 steps and pauses; palette: teal, amber, charcoal(3 锚点); 2 beats`
- WHY:Sora 拼两段 4s 比硬 8s 稳;颜色锚点防中途变色;一相机运动 + 一动作。

## 拷贝进工作笔记的清单

```
提示词自检 · 逐条勾(先确认目标 provider)
[ ] 目标厂牌?用了它的公式(Runway 运动-not-外观/正向;Kling S+A+C+Style;Sora shot-list)?
[ ] 一个镜头只有一个主动作 + 至多一个次动作?
[ ] Runway:无 negative,全正向?Kling:Elements ≤4?Sora:≤8s(超了拆 4s×2)?
[ ] 点了 3–5 个颜色锚点?
[ ] 有脸 + 单镜 >5s → 已建议拆 4–5s + 首尾帧/seed 锁?
[ ] 没堆无用外观词、没重述会触发漂移的服装描述?
[ ] promptlab(manju prompt <shot> --check)单动作/CJK/时长检查全绿?
```

## 失败目录(smells + 在 Manju 里长什么样)

| smell | 在 Manju 的表现 / 抓法 |
| --- | --- |
| 一句塞多动作 | `manju prompt <shot> --check` 报单动作/motion 启发式;拆句 |
| Elements/参考图超限 | `manju refs <shot>` 显示按厂牌预算的 selected/省略 + 影响;超 `limits.max_ref_images` 被截 |
| 写了 negative 给不吃的厂牌 | promptlab 提示;改正向 |
| 中途变色/发烧感 | `visual-qc-review` D 类(时序/光照);prompt 加颜色锚点重生成 |
| 内容审核拒 content_rejected | `manju tasks` 看拒绝原因 tail;**别无脑重试**,改写 prompt 或走降级链(§8) |

## Manju 落地(把提示词钉进真相与工作台)

- **提示词工作台 promptlab**:`manju prompt <shot> [--json] / --check`——4 段 prompt + 参考图 lineage + provider trace + 成本 + 单动作/CJK 从句/时长的启发式检查与确定性拆分建议(round U)。
- **参考图预算**:`manju refs <shot> [--json]`——参考解析 + 各厂牌预算分配(selected/省略 + 中文影响)+ 清洁度 QC(繁忙背景/光照冲突/缩放的本地 ffmpeg 启发式 + 诚实 needs_vision)。厂牌上限见 provider manifest 的 `limits.max_ref_images`(Kling 4、Runway 3、Vidu 7、PixVerse 3→7…)。
- **写进镜头**:`shots/SNNN.yaml` 的 `generation`(`candidates`/`fallback`/`provider`);关键帧 `keyframes`(首尾帧,`manju board keyframes`)。
- **路由与降级**:`manju route explain [<shot>]`/`manju routing explain` 看该镜派给谁、为什么;失败按降级链走(换 provider → 换 seed 重编 prompt → 图生视频 → 首尾帧 → 静帧推拉 → 文字卡)。
- **一致性**:角色/外观锁与参考图见 `character-consistency`;镜头语言翻译见 `shot-design`。

## 验收 eval

1. 给一段外观堆砌的 Runway prompt,是否被改成「运动 + 参考图定外观、全正向」?
2. 给一个塞了 4 个动作的 Kling 句,是否拆成 S+A+C+Style、一主动作 + 收势?
3. 给一个 8s Sora 需求,是否拆成 4s×2 且给了 3–5 颜色锚点?
