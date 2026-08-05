---
name: narrative-pacing
description: 为明确的发行格式设计叙事节奏与注意力策略。只有用户明确要求平台留存、营销、带货、知识短视频、竖屏短剧或指定格式 profile 时才使用；普通叙事片不自动套黄金三秒、CTA、固定镜长或 pattern interrupt。
when_to_use: 用户明确指定平台留存、营销、知识短视频、竖屏短剧或其他格式特定节奏目标时。
tags: [craft, reference]
auto: false
---

# 叙事节奏与格式特定注意力策略

先判断作品的格式目标，再选择节奏工具。没有明确 profile 时，按人物状态变化、信息释放、动作与反应、停顿、声音和 Animatic 的实际播放判断节奏。

## 什么时候不该用

| 情形 | 去哪 |
| --- | --- |
| 普通叙事片只需要故事与镜头设计 | `creation-funnel` / `shot-design` |
| 单镜画面坏了或 QC 有 findings | `repair-loop` |
| 对白已锁 | `manju propose`，不要直接改作者真相 |

## 决策树

- **Narrative film**：以场次张力、人物和观众何时得知信息、动作-反应、沉默、声音先行/滞后和 Animatic 为准。
- **Vertical drama**：在故事成立的前提下检查冲突进入速度与场尾承接；不要把每拍等同于一镜。
- **Knowledge video**：明确观众问题、证据顺序和信息负担，必要时前置价值。
- **Marketing / commerce**：只有用户明确选定该 profile，才讨论 hook、CTA 或注意力重置。
- **Owner-defined**：记录用户给定的节奏标准，不用未经验证的行业百分比替代。

固定秒数、平台完播百分比和通用 pattern-interrupt 频率已撤销。时长必须通过对白、呼吸、动作完成、信息读取、剪辑接口和 Animatic 决定。

## BEFORE / AFTER

**普通叙事片**

- BEFORE：自动要求黄金三秒、CTA 和每几秒切镜。
- AFTER：先找场次不可逆变化，再用动作、反应、停顿和声音安排观看时间。
- WHY：营销留存公式不是通用电影语法。

**知识视频**

- BEFORE：长时间寒暄后才回答观众问题。
- AFTER：在明确 profile 后前置问题与可验证价值，再按理解负担组织证据。
- WHY：注意力策略必须服务格式和信息。

**镜头过长**

- BEFORE：只因超过某个固定秒数就拆镜。
- AFTER：在 Animatic 中检查表演、对白、读取和剪辑接口；只有节奏或控制风险确实需要时才拆。
- WHY：固定镜长无法替代导演判断。

## 自检

```text
[ ] 用户明确了 format profile？
[ ] 普通叙事项目没有自动套 hook/CTA？
[ ] 节奏依据是状态变化、信息、动作-反应和声音？
[ ] 没有无来源的百分比和固定秒数？
[ ] 决定已在完整 Animatic 中播放验证？
```

## Manju 落地

- `shots/index.yaml`：作者镜头顺序。
- `shots/*.yaml` 与 `timeline/rules.yaml`：镜头时长和全局时序。
- `manju build --target animatic`：实际播放临时画面与声音。
- 锁定内容通过 `manju propose` 提交修改理由。

## 验收 eval

1. 普通叙事短片不得自动出现黄金三秒或 CTA。
2. 明确的营销任务可以使用格式特定注意力策略，但不伪造指标。
3. 镜长调整必须能解释为表演、信息、声音或剪辑需要。
