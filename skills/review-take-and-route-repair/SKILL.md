---
name: review-take-and-route-repair
description: 基于真实媒体和合同的评审路由：观察 opening、可见变化、endpoint、连续性与声音，输出 disposition 和单变量 Experiment Memory。
when_to_use: 当已有生成/导入 take 需要看片、验收、决定保留/后期修/剪辑/重拍/改源时使用；不根据 Prompt 猜结果。
tags: [qc, task]
user_invocable: true
---

# Review Take and Route Repair

评审输入必须能确认媒体身份：shot id、take id、文件路径、字节 SHA-256、spec/contract digest、source revision、观察者和观察时间。媒体被替换或合同变化时，旧 verdict 只作历史，不能作为当前 assurance。

## 输入

- `manju qc brief` 生成的当前、未修改 packet；
- packet 绑定的 take 路径、media SHA-256、ShotContract、expectations 和 evidence frames；
- 当前 SceneContract/Bible 只用于判断角色和连续性，不替代媒体观察。

## 评审顺序

1. 核对媒体身份与哈希，确认是当前 selected/current take。
2. 观察可用区间和开场实际状态。
3. 对照 ShotContract 的 `purpose`、`viewer_must_perceive`、`opening`、`endpoint`、performance、physics、sound、acceptance。
4. 逐条写 visible beat 是否发生；观察不到写 `UNCERTAIN` 或 `NOT_EVALUATED`，附 evidence_refs。
5. 记录结尾瞬态：position=END、value、visibility、confidence。它是续接锚点，不是永久 Bible 事实。
6. 再判断角色五层、物理连续性、声音和剪辑接口。
7. 输出 disposition 与下一步路由。

## 允许的 disposition

`KEEP`、`FIX_IN_POST`、`EDIT_DONT_REGENERATE`、`REROLL`、`REWRITE_SOURCE`。非 KEEP 必须指定一个 primary repair variable，例如 `source_action`、`camera`、`motion`、`endpoint`、`reference_role`、`audio`、`post_trim` 或 `provider_surface`。

完整 repair variable 词表与代码一致：`clip_scope source_action camera motion endpoint reference_role reference_asset framing lighting text_overlay audio seed provider_surface safety_wording post_trim post_mask post_grade`。

observed state visibility 词表与代码一致：`VISIBLE NOT_VISIBLE UNCERTAIN NOT_EVALUATED NOT_APPLICABLE`。

```yaml
observations:
  - expectation_id: opening-state
    status: present
    evidence_refs: [frame:000, frame:012]
  - expectation_id: endpoint
    status: uncertain
    evidence_refs: [frame:end]
decision:
  disposition: REROLL
  primary_repair_variable: motion
  diagnostic_isolation: true
  reason: 动作末段手与门把脱离
  experiment:
    hypothesis: 缩小手部动作幅度可保持接触
    tested_variable: motion
    held_constant: [camera, identity, lighting, duration]
    expected_result: 末段仍能看见连续接触
    observed_result: 前半段通过，末段失败
    next_test: 用相同 source 只改 motion
observed_states:
  - dimension: POSE_ACTION_PHASE
    position: END
    value: 右手仍搭在门把上
    visibility: VISIBLE
    evidence_refs: [frame:end]
    confidence: 0.9
```

Experiment Memory 绑定 take/media hash/spec/expectation；测试变量不能同时出现在 held_constant。不要删除失败 take，不要直接执行 `select`、`redo`、`repair` 或付费动作；只交证据和路由建议。

## 输出

一次 `manju qc verdict` 所需的 observations、observed_states 和可选 decision/experiment；每条观察含 expectation id、status 和 evidence refs。输出只提供证据与路由建议，实际 select/redo/repair 由现有 gate 约束。

## 失败条件

- packet 缺失、被改或与当前媒体不符：重新生成 brief；
- 媒体/源发生 binding drift：可以存历史，但不得算当前 assurance；
- opening、endpoint 或声音无法观察：写 UNCERTAIN/NOT_EVALUATED，不猜；
- 用户要求一次改变多个变量：标 `diagnostic_isolation: false`，不得归因到单一变量。

## 纪律

只评审，不执行 select、redo、repair、source patch 或 Provider 调用，不预先授权花费。媒体事实优先于 Prompt 预期，失败 take 保持 append-only。

## 什么时候不该用

还没有真实 take 时先走 `shot-design` 或 `prompt-craft`；没有 QC finding 不要启用 `repair-loop`；只需从已验收结尾设计下一镜时转到 `continue-from-accepted-take`。

## Eval

1. Prompt 与画面冲突时，以当前媒体观察为准，不判 Prompt 成功。
2. 画面漂亮但 endpoint 未观察时，输出 uncertain，不输出 KEEP 的确定性理由。
3. 变脸问题路由到 REROLL 或 REWRITE_SOURCE，不把“更电影感”当变量。
