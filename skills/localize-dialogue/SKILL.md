---
name: localize-dialogue
description: 把一集的对白本地化成另一种语言的 locale source patch(locales/<lang>/lines.yaml)。吸收 VideoLingo 的方法但放在 Skill 层:术语表 → Translate → Reflect → Adaptation → 时长贴合复审(terminology→translate→reflect→adaptation→timing fit）。产出的是「源补丁」交人确认,核心只负责 base_hash 陈旧、字幕规范与音频验证;技能自己绝不改核心真相、绝不直接调用云翻译/TTS。触发词:本地化、localize、翻译、locale、字幕翻译、lines.yaml、多语言、配音翻译、i18n。
when_to_use: 需要把对白翻译/改写成某语言、写或修 locales/<lang>/lines.yaml、或为配音做时长贴合复审时。
tags: [task, localization, craft]
user_invocable: true
---

# 对白本地化(localize-dialogue)

Manju 的本地化真相是 `locales/<lang>/lines.yaml`——每个镜头一行 `{text, base_hash}`,`base_hash` 钉住翻译当时的基准中文 `dialogue.text`。基准一改,这个 hash 对不上,该行就 STALE(核心自动判定)。你的活:按下面五步流水产出**一份 locale source patch**(即 `lines.yaml` 的补丁),交人确认。核心只做 base_hash、timing、字幕规范和音频验证——**翻译内容永远是人/Agent 的源提案,不是核心自动生成的**。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 只是同一种语言里换个说法 | 直接改 `shots/SNNN.yaml` 的 `dialogue.text` |
| 翻译已定,现在要配音 / 对齐 | `manju voice`、`manju align` |
| 要定的是字幕每行几个字、安全区多大 | `subtitle-standards` |

## 五步流水(VideoLingo 方法,Skill 层)

1. **terminology(术语表)**:先抽出专有名词、人名、口头禅、世界观词,定一版**术语对照表**并全集一致。人名遵循字幕规范(名字之间用全角间隔号 ·)。
2. **Translate(直译)**:逐行忠实直译,保留说话人语气标签(`speaker`),不合并/不拆分镜头行。
3. **Reflect(反思)**:对照原文自检——漏译、误译、术语不一致、语气跑偏、机翻腔;标出要改的行。
4. **Adaptation(在地化改写)**:把直译改写成目标语言自然的台词(俚语、称谓、文化梗替换),**意思对齐但允许句式改**;这是「源提案」,不是核心自动改写。
5. **timing fit review(时长贴合复审)**:估算每行朗读时长对齐镜头槽位;超时的行走 `duration-adaptation` 建议(re-TTS 变速 → 源提案改写 → 阈值内 time-stretch → 改剪辑 → 人工接受),**绝不为了塞进时长而在核心里删字改意**。

## 与核心字段的映射(只碰真实存在的字段)

- 产出/修改:`locales/<lang>/lines.yaml`,每行 `text`(译文)+ `base_hash`(基准中文 `dialogue.text` 的 sha)。
- 陈旧判定:核心用 `base_hash` 判 STALE(基准变→重译);技能不自己算 hash 塞进真相。
- 字幕规范:交给 `subtitle-standards`(每行字数、安全区、CPS)。
- 音频/配音验证:交给核心的 align/timing 与 audio masters;技能只提议,不出片。

## 输入

- 目标语言 `lang`;本集镜头的基准 `dialogue.text` 与 `speaker`;可选已有 `lines.yaml`、术语表、平台字幕规范(画幅/每行字数)。

## 输出

- 一份 **locale source patch**:`locales/<lang>/lines.yaml` 的新增/修改行(`{text, base_hash}`)+ 一版术语表 + timing 复审备注(哪些行超时、建议走哪条 duration 路线)。**只是源提案,等人确认后由核心走既有 locale 写入路径落盘。**

## 失败条件

- 基准 `dialogue.text` 与手上原文对不上(`base_hash` 会 STALE)→ 停,先对齐基准,别在旧基准上翻。
- 术语表不一致 / 人名跨集漂移 → 标为待修,不静默放行。
- 为了贴合时长而删字、改意、改角色台词 → **禁止**;只能提 duration 建议由人取舍。
- 想直接调云翻译/TTS 出结果 → 越权;技能只产源补丁,spend-bearing 命令是人后续在既有 gate 后自己跑。

## 纪律

- 技能产**源补丁 + 证据**,绝不代核心写真相、绝不直接触发付费 Provider。
- 翻译是**人/Agent 的源提案**;核心只做 base_hash / timing / 字幕规范 / 音频验证。
- 五步顺序不可跳:没有 Reflect 的直译不算完成;没有 timing 复审的译文不能进配音。
- 遵循 append-only 与文本事实源:不覆盖历史 locale,不为适配时长而改动原语义。
