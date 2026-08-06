---
name: review-take-and-route-repair
description: bound take 评审程序——对着 qc brief 的 packet 看片,回填 bound observations + 五类 disposition(KEEP/FIX_IN_POST/EDIT_DONT_REGENERATE/REROLL/REWRITE_SOURCE)+ 一个 primary repair variable + 媒体绑定 Experiment Memory + endpoint observations;只交证据,绝不执行 select/redo/patch。触发词:评审、看片、review take、triage、disposition、修复路由。
when_to_use: qc brief 出题后要判读 take、或用户问"这个 take 该留该改还是该重拍"时。
tags: [qc, task]
user_invocable: true
---

# 看片 → 生产决策(review-take-and-route-repair)

你评审的是 packet 绑定的**那份字节**(media_sha256),不是"这个镜头"。评审产出
是**证据**(verdict v2 追加),裁决(assurance)由引擎的纯函数算——你不写结论,
只写观察 + 决策建议。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 还没有 qc brief 的 packet(没有绑定的字节可评) | 先 `manju qc brief` |
| 已经判完了,现在要真的动手修 | `repair-loop` |
| 要评的是整片而不是某一条 take | `manju exports`、`manju compare` |

## 输入

- `manju qc brief --json` 的 packet(packet_id/media sha/spec_hash/expectation_digest/frames);
- candidate family view(同一创作 family 的兄弟 takes,便于对比);
- 确定性 QC(`manju qc --json`)与成本/attempt 证据(`manju tasks`);
- 判读标准:`manju skills show visual-qc-review`(A–J)。

## 输出:一次 `manju qc verdict` 回填,含

1. **observations** —— 每个 expectation_id 一条(present/absent/uncertain/not_evaluated),附 evidence_refs;
2. **decision**(additive,可选):

```json
{
  "disposition": "REROLL",
  "primary_repair_variable": "motion",
  "diagnostic_isolation": true,
  "reason": "动作末段变形,其余条件可保留",
  "experiment": {
    "hypothesis": "缩小右手动作幅度可以避免硬币融入手掌",
    "tested_variable": "motion",
    "held_constant": ["camera", "character_reference", "lighting", "duration"],
    "expected_result": "硬币全程保持形状并与手指真实接触",
    "observed_result": "0-800ms 可用,末段仍轻微变形",
    "usable_ranges_ms": [{"start_ms": 0, "end_ms": 800}],
    "next_test": "不再生成完整放入口袋动作,改用遮挡切镜"
  }
}
```

3. **observed_states** —— 开头/结尾的瞬态观察(下一镜续接的锚点):

```json
{"dimension": "POSE_ACTION_PHASE", "position": "END",
 "value": "左手仍搭在门把上", "visibility": "VISIBLE",
 "evidence_refs": ["frame:end"], "confidence": 0.9}
```

visibility 只有:VISIBLE / NOT_VISIBLE / UNCERTAIN / NOT_EVALUATED / NOT_APPLICABLE。
瞬态姿态**永不**升级为 Bible 身份事实。

## 五类 disposition(不许用含糊的 PASS/FAIL 代替)

| disposition | 判据 | 默认后续(人执行,引擎有闸) |
|---|---|---|
| KEEP | 可保留;选不选仍由人定 | `manju select`(人) |
| FIX_IN_POST | 画面可用,确定性后期可修 | `manju repair --op … --dry-run` |
| EDIT_DONT_REGENERATE | 该剪辑/遮挡/字幕/声音解决 | timeline/NLE roundtrip proposal |
| REROLL | source/Prompt 基本正确,只缺随机性运气 | `manju redo`(同 source,spend gate) |
| REWRITE_SOURCE | 问题在镜头意图/Prompt source/refs/结构 | 先 `direct-shot-source-patch` |

## one-variable 纪律(§9.3)

非 KEEP 必填 **一个** primary_repair_variable,词表(与引擎校验一致):

```text
clip_scope source_action camera motion endpoint reference_role
reference_asset framing lighting text_overlay audio seed
provider_surface safety_wording post_trim post_mask post_grade
```

secondary 观察可以列,但下一次诊断式尝试只改这一个变量。用户执意一次改多个:
可以,但 `diagnostic_isolation: false`,并且不得声称知道是哪项起了效。

## Experiment Memory 纪律

- 它只写在**同一个** verdict v2 的 `decision.experiment`,不另建 generation log;
- `tested_variable` 复用上面的词表,且不得同时列入 `held_constant`;
- `hypothesis` 写可证伪假设,`expected_result` 与 `observed_result` 分开;
- `usable_ranges_ms` 只标实际可用区间,必须满足 `0 <= start_ms < end_ms`,不得越过已知媒体时长;
- `next_test` 说明下一轮唯一变量或明确的剪辑/改源动作;
- experiment 继承 packet 的 take/media_sha256/spec/expectation 绑定。媒体或源变化后它只作历史,不得当成当前 assurance 证据;
- 失败 take 不删除。下一轮先读 candidate family 的当前/历史 experiment,再决定唯一变量。

## 失败条件

- packet 缺失/被改(intake 会拒)→ 重新 `manju qc brief` 出题;
- 媒体已被同名替换 → 照常提交,intake 存为 binding-stale(你的工作是历史,不作数于当前);
- 拿不准 → observed 用 uncertain,不猜。

## 纪律

绝不执行 select / redo / repair / source patch / 花钱——只交证据。路由建议指到
现有命令即可(全部人工确认执行)。
