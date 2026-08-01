---
name: visual-qc-review
description: AI 视频质检判读程序(逐帧/逐镜)——把 Manju 的 needs_vision 帧槽变成结构化裁决。带专业级 A–J 严重度分级判据(角色/服装/场景/光照/解剖手脸/文字/水印/运动物理/技术编码/音画)、Netflix Blocker/Issue/FYI 三档 + 主体/前景/时长的语境升降级、穿帮/连续性失败目录、便宜检查先行再上视觉、PSE 硬闸,以及 agent 直接产出的 JSON 裁决契约。触发词:质检、QC、穿帮、判读、looks AI、一致性初筛、needs_vision、verdict、severity。
when_to_use: 复查 QC 抽帧/某条 take、有视觉能力的 agent 终审画面、或判断「像不像 AI/有没有穿帮」时。
tags: [qc, task]
user_invocable: true
---

# 质检判读(visual-qc-review)

Manju 引擎自己不看画面(§0 不含 LLM)——它跑得动的便宜检查先跑,视觉判断留给**你**。本技能给你专业场记级判据 + 严重度,让你把「看着像 AI 吗」变成**逐条可核、可下钩子的裁决**。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 当前 agent 没有视觉能力 | 先跑 `manju qc` 的机检层(A–J 里的 C 类判据),别猜画面 |
| 已经判完了,现在要执行修复 | `repair-loop` |
| 要给出 disposition 与修复路由(KEEP/REROLL…) | `review-take-and-route-repair` |

## 裁决 JSON 契约(agent 产出这个;`manju qc brief` 供帧+判据,`manju qc verdict` 收裁决)

对**每一条发现**(不是每条判据都写,只写命中的)产出一个对象;一次判读产出一个数组:

```json
{
  "shot": "S003",
  "criterion": "E1",
  "level": "blocker",
  "message": "右手第4、5指粘连成一根,前景特写,主体手部",
  "evidence": "frame@1200ms; reports/qc/frames/S003_1200.png"
}
```

- `shot`:镜头 id(或 `S003#f12` 精确到帧)。
- `criterion`:下方 A–J 判据码(如 `A2`/`E1`/`I8`)。
- `level`:**`blocker` | `issue` | `fyi`**(见下,Netflix 三档)。
- `message`:**中文**,一句话说清什么问题 + 在哪 + 主体还是背景。
- `evidence`:帧时间/帧文件路径/OCR 结果等可核证据。
无发现的镜头产出 `[]`。整体可另附 `{shot, verdict: pass|blocked, counts:{blocker,issue,fyi}}` 汇总。

## v2 绑定判读(packet + 逐条 expectation · DR02)

`manju qc brief` 的每条镜头行现在多带一组 v2 字段(`packet_id` / `expectations` / `media` / `spec_hash` / `expectation_digest`)。带这些字段时,走**绑定判读**契约,回填更严谨、可验收:

- **回显 `packet_id`**:裁决里原样带回该行的 `packet_id`(如 `pkt_0123456789ab`)——它把你的判读钉死在你所看的那份**字节 + spec + expectation 摘要**上。
- **逐条 expectation 给观察**:对 `packet.expectations` 里的**每一条** id,给一个 `observed ∈ present | absent | uncertain | not_evaluated`。
  - **`uncertain` 是被允许且诚实的**:看不清 / 拿不准就写 `uncertain`,**绝不猜 `present`**。宁可 UNKNOWN,也不要假通过。
  - `confidence` 仅是可选元数据,**绝不参与** PASS/FAIL 计算。
- **`findings` 只写 expectation 列表之外**的判断(A–J 里那些镜头没显式承诺、但你眼睛抓到的问题,如背景招牌畸变);承诺内的判定一律走 `observations`。
- **最终 PASS/FAIL/UNKNOWN 由引擎的纯 diff 算出**(`present+present→PASS` / `present+absent→FAIL` / 其余→`UNKNOWN`),**判读者永不自己写结论**——你只报观察。
- **回退**:若某 brief 行**没有** v2 字段(老项目 / 未编译 expectation),就退回上面的 legacy 契约(逐 finding 的 `shot/criterion/level/message`)。

最小 v2 JSON 示例(形状取自 `verdict_contract()['v2']`):

```json
{
  "schema": "manju.qc.verdict/v2",
  "packet_id": "pkt_0123456789ab",
  "subject": {"kind": "shot", "id": "S003"},
  "observations": [
    {"expectation_id": "exp:S003:must_show:1a2b3c4d", "observed": "present",
     "confidence": "high", "evidence_refs": ["frame:1200"]},
    {"expectation_id": "exp:S003:avoid:9f8e7d6c", "observed": "uncertain"}
  ],
  "findings": [
    {"level": "issue", "message": "背景招牌轻微畸变(不在 expectation 列表内)"}
  ],
  "reviewer": {"kind": "model_visual", "name": "driving-agent"}
}
```

回填仍走 `manju qc verdict --from-file <路径>`(MCP 用 `qc_verdict`);验收状态见 `reports/qc.json` 的 `assurance` 块与 `manju qc` 摘要。若当前媒体/spec/expectation 与 packet 绑定不符,该判读只作历史保留(`binding=stale`),不计入当前验收。

## 严重度三档(Netflix 口径,按「会员体验影响」而非「是否存在」)

- **blocker** — 内容不可消费 / 不能上线(必须修:如内容审核类、PSE、主体断裂)。
- **issue** — 明显降低体验、上线前该修(报告里的 Major,以及落在主体的严重 Minor)。
- **fyi** — 存在但不可操作 / 观众基本不察觉(Info,及背景/一闪而过的 Minor)。

**语境升降级(关键,别用平表)**:缺陷落在**主体/前景/持续出现** → 升级;**背景/次要/亚秒一闪** → 降级。单一「AI tell」(E3–E5 皮肤/眼睛、F、H3)证据弱、且新模型在退化——**要 ≥2 个信号或命中主体才升过 fyi**;人类判这些 tell 准确率仅 ~60–75%。

## A–J 判据(码 · 默认档 · C=便宜可测 / V=需视觉)

**A 角色身份** A1 镜内不变脸 `blocker·V` · A2 跨镜身份一致 `issue·V` · A3 疤/纹身/发等固定标记不迁移(穿帮)`issue·V` · A4 人数稳定、无物→人 `issue·V`。
**B 服装** B1 镜内服装型/色/态不变 `issue·V` · B2 跨切一致(纽扣/袖/配饰)`issue·V` · B3 配饰不忽有忽无 `fyi→issue·V` · B4 衣物物理(不融进皮肤)`fyi·V`。
**C 场景/背景** C1 背景物保形保位 `issue·V` · C2 道具留位、食水位/烟长一致 `issue·V` · C3 无穿越/出戏物件 `issue·V`(依设定) · C4 无平铺 AI 纹理/不可能建筑 `fyi·V` · C5 无幻觉工作人员/器材/游离文字 `fyi→issue·混合`。
**D 光照逻辑** D1 阴影方向与光源一致 `issue·V` · D2 面部光/反射匹配环境 `fyi→issue·V` · D3 场内时段/曝光一致 `issue·混合`(ffmpeg 亮度统计报跳变,视觉确认) · D4 无无端亮度闪烁 `fyi·C`。
**E 解剖/手/脸** E1 手指数正确、无融合/多指、弯曲自然 `blocker→issue·V`(手在主体/前景则 blocker) · E2 面部解剖合理(五官不融、耳齿正常)`issue·V` · E3 眼睛自然(眨眼/视线/眼神光)`fyi·V` · E4 牙不融合/嘴腔在说话时存在 `fyi·V` · E5 皮肤不蜡塑感 `fyi·V`(弱、常为风格) · E6 肢体不穿物、比例合理 `issue·V`。
**F 文字/字体** F1 屏内文字可读、真语言、非乱码 `issue·先C后V`(OCR+语言检测,视觉确认) · F2 文字跨帧稳定(字母不变异)`fyi→issue·混合`(逐帧 OCR diff) · F3 无镜像翻转/畸形字形,尤其 CJK(穿帮)`fyi·V`。
**G 水印/logo 残影** G1 无生成器残留水印(Sora/Runway 等)`blocker·混合`(已知标模板匹配便宜,淡影靠视觉;法务/品牌 + 暴露 AI 来源) · G2 无误复制 logo/台标 `issue·混合` · G3 EXIF/元数据 AI 标签 `fyi·C`。
**H 运动/物理** H1 无物/肢穿实体 `issue·V` · H2 重力/动量合理(不漂浮/无摩擦滑)`issue·V` · H3 快动有动态模糊、无频闪/异常稳定 `fyi·混合` · H4 因果正确(手握住物、叉到嘴)`issue·V` · H5 相机运动连贯、无生成器扭曲/抖 `fyi→issue·V`。
**I 技术/编码(全便宜 · 预筛闸)** I1 无黑帧/丢帧/冻帧 `blocker→issue·C`(ffmpeg blackdetect/freezedetect) · I2 无宏块/像素化 `issue·C` · I3 无带状 banding `fyi·C` · I4 无锯齿/摩尔纹 `fyi·C` · I5 无坏点/雪花/过噪 `fyi·C` · I6 分辨率/画幅正确、无劣质缩放 `issue·C`(ffprobe) · I7 合法视频电平/在色域内 `fyi·C`(signalstats) · **I8 PSE/有害闪烁——最高优先自动闸 `blocker·C`**(ITU-R BT.1702:任意 1s 内 ≤3 闪、亮度反向变化 ≥20 cd/m²、饱和红闪一律高危、图样占屏 >~25%)。
**J 音画/唇音** J1 音画同步、唇形匹配、辅音闭合、漂移 <~100ms `blocker→issue·V+C` · J2 整体响度对 profile(−14 LUFS 短视频)`issue·C`(ffmpeg ebur128/loudnorm) · J3 真峰 ≤ 上限 `fyi→issue·C` · J4 无丢音/削波/交流声 `issue·C` · J5 声道映射正确 `issue·C`。

## 判读程序(便宜先行,视觉后判)

1. **先跑硬闸 I8(PSE)**——一票否决,先于一切。
2. **跑便宜 C 行**:整个 I 行 + J2–J5 + F1(OCR)+ G1/G3——Manju 能用 ffmpeg/OCR/元数据自查(round Q 已有削波/静音、I1/I2 类;PSE/banding/freeze/loudness 为补强项)。命中即写裁决。
3. **再判 V 行**:对 `manju qc brief` 给的每个 `needs_vision` 帧槽,按 A/B/C/D/E/F/H 逐条看,应用语境升降级 + 弱信号 ≥2 才升级的规则。
4. **产出 verdict 数组**(上面的 JSON),`manju qc verdict` 收;有 blocker 即整体 blocked。
5. 修复接 `repair-loop`;预防(同一份失败目录)接 `prompt-craft` / `character-consistency`。

## 拷贝进工作笔记的清单

```
质检判读自检 · 逐条勾
[ ] I8 PSE 硬闸先跑,通过?
[ ] 便宜 C 行(I/J2-5/F1/G1)先跑,命中都写了裁决?
[ ] 每个 needs_vision 帧槽按 A/B/C/D/E/F/H 逐条看过?
[ ] 每条裁决:shot+criterion+level+中文 message+evidence 齐?
[ ] 语境升降级用了(主体/前景/时长),没用平表?
[ ] 弱 tell(E3-5/F/H3)有 ≥2 信号或命中主体才升过 fyi?
[ ] 有 blocker → 整体 blocked,交 repair-loop?
```

## 失败目录(穿帮/连续性,和 prompt-craft 共用)

脸:morph/身份漂移、死眼、塑料皮、齿发闪。手:4/6/7 指、融合、帧间变数。运动:漂浮无重量、过顺无微抖、穿模、交互落空(叉没到嘴)。物件:漂移/瞬移/消失、握物变形、平铺纹理、不可能反射。时序/光:中途「发烧梦境」变风格、曝光闪、跨切背景不一致、过饱和合成色。文字:场景招牌乱码、logo 变形、水印残影。音:双唇音唇音错位、无呼吸、TTS 平。穿帮码对照:伤口左右迁移=A3、镜像汉字=F3、工作人员入镜=C5/G2。

## Manju 落地

- **QC 报告**:`manju qc [--deep]` → `reports/qc.json`/`qc.md`/`repair_plan.yaml`;三层(存在/技术/内容)。视觉判据缺视觉厂牌时发 `needs_vision` advisory(点名跳过了哪条,及是否配了 `qc_vision` provider)。
- **裁决管道**:`manju qc brief`(供帧 + 逐镜 A/B/C/D/E/F/H 判据)→ 你产出上面的 JSON 数组 → `manju qc verdict`(收裁决)。契约形状即本页 JSON。
- **一致性判读(round X)**:一致性是跨镜/镜头对参考图的属性,不是单镜属性——`manju qc brief --mode consistency` 出的不是逐镜帧,而是 COMPARISON UNIT:每个出场 >1 镜的角色一张对照看板(bible 参考图 + 每个出场镜头一帧,判 A/B 身份服装)、每对共享场景的相邻镜头一张并排图(判 C/D 场景光照)、每个场景一张整体看板(同判 C/D)。裁决 JSON 把 `shot` 换成 `unit`(取自 brief 的 `unit` 字段,如 `character:linxia`),一条裁决对该组合内**所有**成员镜头的当前字节生效——任一成员重生成,裁决整体过期。`manju qc coverage` 看哪些镜头/组合还没判读过。
- **两级 QC(守 §0)**:Manju 跑便宜 C 行;V 行由驱动 agent 的视觉填。
- **修复闭环**:裁决 → `repair-loop`(map 到 repair op)→ 重做 → 复核。
- **PSE 是法务安全必加项**,不依赖视觉、便宜、且是唯一能伤到观众的缺陷 → `manju qc` 硬闸。

## 验收 eval

1. 给一组帧,是否先跑 PSE/便宜检查再上视觉、每条裁决字段齐全?
2. 一个背景一闪的塑料皮 tell,是否被正确降到 fyi 而非误报 blocker?
3. 一个主体特写的六指,是否升到 blocker 并交 repair-loop?
