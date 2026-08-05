---
name: creation-funnel
description: 从一句话创意到可执行分镜的分阶段创作漏斗(逐步程序,含花钱前审批闸)——立意/Concept(brief.md) → 梗概/Synopsis → 节拍/Beat-sheet → 剧本/Script(script.md) → 分镜/Storyboard → 生成。借 LTX 阶段命名与三道 approve-before 闸(cast/Elements 确认、计划、逐镜)。决策树:idea-first 生成式 vs preset/模板式。触发词:创作、漏斗、从头开始、把想法做成视频、立意、梗概、节拍、storyboard、开新项目。
when_to_use: 用户新建项目、说「帮我把这个想法做成视频」、或要一条从创意到分镜的完整路径时。
tags: [funnel, task]
user_invocable: true
---

# 创作漏斗(creation-funnel)

这是一条**逐步程序**:把一句话创意稳稳推到「可以按生成键的分镜」。每一步产出一个真相文件、经人确认再走下一步;**任何花钱动作都在闸后,先 dry-run 再问**。你只在文本上创作,引擎不编故事。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 项目已有 script/shots,只是要改其中一处 | 直接改文本 + `manju check`;单镜的导演判断走 `direct-shot-source-patch` |
| 手上是一个长剧本 / 小说,要先拆集 | `series-breakdown` |
| 只是想跑一次生成 | `manju build --dry-run` 看清单再 `manju build` |

## 决策树:先判入口

- **有想法、要从零生成(生成式)** → 走下面的六阶段漏斗(默认)。
- **想套现成结构(模板式)** → `manju new --preset <kit>` 选一套预设(vertical_ai_video / horizontal_ai_video / blank),「挑结构 → 往槽里填素材 → 编辑」;仍回到漏斗的「分镜→生成」两步。
- **有小说/长稿** → 先走 `series-breakdown`(取框架压缩)再回本漏斗的剧本阶段。

## 六阶段(每阶段 = 一个真相文件 + 一次确认)

对齐 Manju 已铺好的骨架(`manju new` 生成 `story/brief.md`/`outline.md`/`script.md`):

1. **立意 / Concept → `story/brief.md`**
   - 开工三步(核心手册 §1):`manju status --json`、读 events 尾部、看 `mode/ask_before`。
   - 若 brief 空:先与人确认一句话创意——谁、在哪、发生什么、为什么抓人。别自己拍板方向。
2. **梗概 / Synopsis → 扩进 `story/brief.md` 末尾**
   - 扩成 3–5 句 premise:核心冲突、一个转折、一个结局钩子。人确认。
3. **节拍 / Beat-sheet → `story/outline.md`**(市场空白,Manju 的差异点)
   - 短片按 **Hook→Value/Build→Payoff→CTA** 写 4 拍;**每行一个节拍,一节拍≈一镜头**,保留节拍编号(下一步一一映射成镜头)。钩子/节奏取 `narrative-pacing`。人确认节拍。
4. **剧本 / Script → `story/script.md`**
   - 分场 + 对白,对白逐字写清(将原样成为 `shots/*.yaml` 的 `dialogue.text`,决定 TTS 时长)。
5. **分镜 / Storyboard → `shots/*.yaml` + `shots/index.yaml`**
   - 每节拍一个 `shots/SNNN.yaml`(景别/运镜取 `shot-design`;提示词取 `prompt-craft`)。更新 `order`。
   - **cast/Elements 确认闸(第 1 道 approve-before)**:任何镜头生成前,先把人物/场景/道具/配音落进 `bible/` 并让人确认——这是一致性锁,也是花钱前的 checkpoint。
6. **生成 / Generate → `manju build`**
   - **第 2 道闸(计划)**:先 `manju build --dry-run`,把镜头数 × 候选 × 时长 + 成本预估贴给人。
   - **第 3 道闸(逐镜/花钱)**:命中 `project.yaml` 的 `ask_before`(如 `expensive_generation`)就停下问人,得同意再 `manju build`。绝不在没问、没估算下发起烧钱 build。

## 三道 approve-before 闸(市场只有 3 个产品做,Manju 引擎强制)

| 闸 | 何时 | Manju 落地 |
| --- | --- | --- |
| cast/Elements 确认 | 任何镜头生成前 | `bible/` 落人物/场景/道具 + 人确认;`manju assets` 核对 |
| 计划(plan) | build 前 | `manju build --dry-run` 出清单 + 成本 |
| 逐镜/花钱 | 命中 ask_before | 停→贴估算→问人→`--yes` 才执行 |

## BEFORE / AFTER(业余流程 → 专业流程)

**例 1 — 跳过节拍直接拆镜**
- BEFORE:拿到一句话就写 30 个 `shots/*.yaml`,结构散、钩子弱。
- AFTER:先写 4 拍 beat-sheet(Hook/Value/Payoff/CTA),再逐拍映射成镜头。
- WHY:beat-sheet 是市场空白也是留存地基;先定节拍再拆镜,顺序/钩子才立得住。

**例 2 — 没确认 cast 就烧钱生成**
- BEFORE:bible 还没定角色就 `manju build` 生成 20 镜,人物每镜不一样。
- AFTER:先 `bible/` 定角色 + 人确认(cast 闸),再 dry-run + 问,再 build。
- WHY:cast 确认既是一致性锁又是花钱前 checkpoint;跳过 = 漂移 + 白烧钱。

**例 3 — 直接 build 不 dry-run**
- BEFORE:`manju build` 一把梭,事后才发现花超预算。
- AFTER:`manju build --dry-run` 贴数字给人 → 命中 ask_before 就问 → 才 build。
- WHY:预算护栏第一道闸是你;有数字才让人拍板。

## 拷贝进工作笔记的清单

```
创作漏斗自检 · 逐阶段勾
[ ] 开工三步做完(status/events/mode)?
[ ] brief.md 有立意 + 3–5 句梗概,方向经人确认(空 brief 没自己拍板)?
[ ] outline.md 是编号节拍、一拍≈一镜,走 Hook→Value→Payoff→CTA?
[ ] script.md 分场 + 逐字对白?
[ ] 生成前:bible 落 cast/Elements 且人确认(第 1 闸)?
[ ] build 前:manju build --dry-run 出清单 + 成本(第 2 闸)?
[ ] 命中 ask_before 停下问人,拿到同意才 build(第 3 闸)?
[ ] 每阶段 manju check + git commit?
```

## 失败目录

| smell | 抓法 |
| --- | --- |
| 跳阶段(无 beat-sheet/无 cast 确认) | 本清单;`manju status` 看阶段缺口 |
| 空 brief 自己编方向 | 硬规矩:人已写方向要尊重,别推翻 |
| 未 dry-run 直接烧钱 | 命中 ask_before 必停;核心手册 §5 |
| 想改锁定内容 | 走 `manju propose`,永不 `unlock` |

## Manju 落地

- **脚手架**:`manju new [--preset <kit>]`(自动 git init;preset 预填骨架);`manju presets` 列预设。
- **阶段文件**:`story/brief.md` → `story/outline.md` → `story/script.md` → `shots/*.yaml`;`bible/`(cast/Elements)。
- **确认/花钱**:`manju build --dry-run`、`ask_before`、`manju director propose/confirm/run/suggest`(六步契约作持久对象)。
- **增量重渲**:改一处只重渲对应段(内容键);自然语言编辑「删第 3 镜/改第 5 镜配音」→ 一次真相编辑 → 增量 build。
- 承接技能:`narrative-pacing`(节拍/钩子)、`shot-design`(拆镜)、`prompt-craft`(提示词)、`character-consistency`(cast 一致性)。

## 验收 eval

1. 给一句话创意,是否产出 brief→梗概→beat-sheet→script→shots 的有序文件而非直接堆镜头?
2. 生成前是否强制了 cast/Elements 确认闸?
3. build 前是否 dry-run 出成本并在命中 ask_before 时停下问人?
