---
name: creation-funnel
description: 从一句话创意到 picture lock 的分阶段创作漏斗，覆盖结尾、场次变化、剧本、镜头计划、资产、临时声音、Animatic、proof、生成评审与修源循环。用于新建项目或把想法做成完整视频；不会把故事节拍等同于镜头，也不会从 Storyboard 直接跳到批量生成。
when_to_use: 用户新建项目、说「帮我把这个想法做成视频」、或要一条从创意到分镜的完整路径时。
tags: [funnel, task]
user_invocable: true
---

# 创作漏斗(creation-funnel)

这是一条**逐步程序**:把一句话创意推进到 picture lock。生成、看片、续接和修源仍属于创作循环；只有 selected media 之后的装配是确定性执行。任何花钱动作都在闸后。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 项目已有 script/shots,只是要改其中一处 | 直接改文本 + `manju check`;单镜的导演判断走 `direct-shot-source-patch` |
| 手上是一个长剧本 / 小说,要先拆集 | `series-breakdown` |
| 只是想查看生成计划 | `manju build --dry-run` |

## 决策树:先判入口

- **有想法、要从零创作** → 走下面的完整漏斗(默认)。
- **想套现成结构(模板式)** → `manju new --preset <kit>` 选预设；仍需验证场次变化、Animatic 与 proof。
- **有小说/长稿** → 先走 `series-breakdown`(取框架压缩)再回本漏斗的剧本阶段。

## 当前过渡流程

对齐 Manju 已铺好的骨架(`manju new` 生成 `story/brief.md`/`outline.md`/`script.md`):

1. **立意 / Concept → `story/brief.md`**
   - 开工三步(核心手册 §1):`manju status --json`、读 events 尾部、看 `mode/ask_before`。
   - 若 brief 空:先与人确认一句话创意——谁、在哪、发生什么、为什么抓人。别自己拍板方向。
2. **梗概 / Synopsis → 扩进 `story/brief.md` 末尾**
   - 扩成 3–5 句 premise:核心冲突、一个转折、一个结局钩子。人确认。
3. **结尾与场次变化 → `story/outline.md`**
   - 先明确结尾和不可逆变化，再按戏剧场次组织人物进入、变化和离开状态。故事节拍可由多个镜头完成；只有明确的平台格式才加载 `narrative-pacing`。
4. **剧本 / Script → `story/script.md`**
   - 分场 + 对白,对白逐字写清(将原样成为 `shots/*.yaml` 的 `dialogue.text`,决定 TTS 时长)。
5. **镜头计划 / Storyboard → `shots/*.yaml` + `shots/index.yaml`**
   - 每镜先写目的、开始状态、一个主要可见变化、结束接口、表演、声音、风险和替代调度，再决定景别与运镜。不要机械地一拍一镜。
   - **cast/Elements 确认闸(第 1 道 approve-before)**:任何镜头生成前,先把人物/场景/道具/配音落进 `bible/` 并让人确认——这是一致性锁,也是花钱前的 checkpoint。
6. **临时声音与 Animatic**
   - 先用现有 `build --target animatic` 连续播放镜头顺序、对白、停顿与环境声；未通过时回到故事或镜头计划。
7. **Proof → 生成 / 评审 / 修源循环**
   - 先验证最高风险镜头和完整场次，再逐步扩大生成范围。每次生成只验证一个主要不确定性；真实媒体 endpoint 优先于原计划。
8. **Picture lock → 确定性装配**
   - selected media 锁定后再进入 timeline assembly、最终声音、render 与 delivery。

## 三道 approve-before 闸(市场只有 3 个产品做,Manju 引擎强制)

| 闸 | 何时 | Manju 落地 |
| --- | --- | --- |
| cast/Elements 确认 | 任何镜头生成前 | `bible/` 落人物/场景/道具 + 人确认;`manju assets` 核对 |
| 计划(plan) | build 前 | `manju build --dry-run` 出清单 + 成本 |
| 逐镜/花钱 | 命中 ask_before | 停→贴估算→问人→`--yes` 才执行 |

## BEFORE / AFTER(业余流程 → 专业流程)

**例 1 — 把节拍机械映射成镜头**
- BEFORE:拿到一句话就写 30 个 `shots/*.yaml`,结构散、钩子弱。
- AFTER:先写结尾和场次变化，再让一个故事节拍按表演与剪辑需要使用一个或多个镜头。
- WHY:故事变化、生成实验和剪辑落点不是同一种 beat。

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
[ ] outline.md 先明确结尾和场次不可逆变化，没有一拍≈一镜?
[ ] script.md 分场 + 逐字对白?
[ ] 临时声音与完整 Animatic 已连续播放?
[ ] 先做最高风险 proof，再扩大生成?
[ ] 生成后记录真实 endpoint，并在需要时修源?
[ ] 生成前:bible 落 cast/Elements 且人确认?
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
