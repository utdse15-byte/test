---
name: repair-loop
description: 从 QC finding 到复核的修复闭环：按保留成果优先的阶梯选择操作，保留媒体血缘并用单变量实验隔离原因。
when_to_use: 当已有 QC findings、assurance proposal 或 repair_plan 需要逐项处理时使用；没有 finding 先跑 manju qc。
tags: [qc, task]
user_invocable: true
---

# Repair Loop

闭环是 `validator → route → fix → check/build → qc → repeat`。先读媒体观察、ShotContract 和 assurance，再决定修 source、剪辑、声音还是重拍；不要看到 stale 就全片重生。

## 修复阶梯

从最保留已有成果到最昂贵：

```text
trim / retime
→ crop / reframe
→ sound or reaction insert
→ cutaway / occlusion
→ local image or video patch
→ composite
→ alternate staging
→ motion-reference / V2V
→ reroll one variable
→ rewrite shot source
→ rewrite scene source
```

时空问题使用现有 `manju repair --op trim|retime|extend|inout|croppad|voice`；画面内容缺陷通常走 `manju redo`，改 source 后重新编译；故事状态错误回到 `scene-design` 或 `direct-shot-source-patch`。所有 paid/长耗时 redo 先 dry-run 并等待 human。

## 证据与单变量

每个非 KEEP 路由必须写一个 `primary_repair_variable`、可证伪 hypothesis、held constants、expected/observed result 和 next test。变量从 `source_action/camera/motion/endpoint/reference_role/reference_asset/framing/lighting/text_overlay/audio/seed/provider_surface/safety_wording/post_trim/post_mask/post_grade` 选择；测试变量不能出现在 held constants。旧 verdict 绑定的媒体或 spec 变化后只是历史。

## 程序

1. `manju qc` 或 `manju build --target qc`，读 `qc.json`、`repair_plan.yaml` 和 assurance proposals。
2. 每条 finding 选择最窄的 op；`auto` 只执行安全、无花费项。
3. 必要时只增新 take/final，登记血缘，再由人 `manju select`；不覆盖已有媒体。
4. `manju check` → build → `manju qc`，直至观察和合同都通过。
5. 记录 `git diff` 与阶段 commit。

## 什么时候不该用

没有 QC finding 时先用 `manju qc`；要判断留/改/重拍的证据时用 `review-take-and-route-repair`；坏的是场次目的或状态时用 `scene-design`，不要直接做媒体修复。

## Eval

1. 变脸 finding 走 reroll/source rewrite，不选 croppad。
2. 对白放不下可先 extend/retime 或 edit，不自动重生成画面。
3. auto 模式遇到 paid redo 必须停在 dry-run/approval gate。
