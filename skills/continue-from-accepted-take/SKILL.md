---
name: continue-from-accepted-take
description: 续接程序——下一镜必须从"已验收媒体的真实结尾"续写,不是从原 Prompt 的预期结尾。读 continuation view(accepted media hash + observed endpoint),产出下一镜现有 source 的 proposal/patch;绝不直接调 Provider。触发词:续接、下一镜、continuation、continue from、接着拍。
when_to_use: 要写/改"承接上一镜"的镜头,或 prompt --check 报 CONTINUATION_* 时。
tags: [directing, task]
user_invocable: true
---

# 从已验收的真实结尾续写(continue-from-accepted-take)

反例(禁止):

```text
上一镜 Prompt 预期结尾 = 她已经抬头
真实 accepted take 的结尾 = 她仍低头
→ 下一镜绝不能写「她抬头后……」
→ 必须从「她仍低头」提出下一镜 patch
```

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 上一镜还没有被验收的 take(没有真实结尾可接) | 先 `manju select` 定稿,或走 `review-take-and-route-repair` |
| 要重新决定这一镜表现什么 | `direct-shot-source-patch` |
| 只是改台词 / 时长这类窄字段 | 直接改 `shots/SNNN.yaml` + `manju check` |

## 输入

- `manju prompt <next_shot> --json` 的 `compiler_trace.continuation_source`
  (源镜头 accepted media 的 sha256、observed_endpoint、completed/reserved beats、
  re-anchor 建议)——这是派生 view,build 不读它,只有你读;
- 源镜头的 canonical refs 与下一镜当前 source;
- surface/provider capability(04 preflight 事实)。

## 续接闸(全过才动笔,§10.5)

1. 源 take 是当前明确 accepted/selected 的媒体(`CONTINUATION_SOURCE_NOT_ACCEPTED` 必须为空);
2. media SHA-256 可验证(锚定则须一致:`CONTINUATION_SOURCE_HASH_MISMATCH`);
3. endpoint 有 bound 观察(`CONTINUATION_ENDPOINT_UNOBSERVED` 为空——没有就先走
   review-take-and-route-repair 回填 observed_states);
4. 源未 stale;
5. capability 支持所需的 reference/edit/extend 方式;
6. 无未决 submission 冲突(`manju tasks`)。

闸不过 → 停,输出把闸修好的下一步(出题回填 / 重锚定 / 改 continuity.prev),
不硬续。

## 输出

1. 对**下一镜现有 source** 的 proposal/patch:
   - `action.main` 的开场状态 = 源镜头 observed_endpoint 的原话事实;
   - `continuity.prev` = 源镜头 id;可选锚定 `continuity.source_media_sha256` =
     view 里的 hash(源被替换时 checker 会报 mismatch);
   - 需要画面级承接时,refs 用 dict 绑定声明迁移:
     `{ref: media/gen/<shot>/<take>.mp4 的帧或 ref 图, controls: [character_identity], ignore: [background, pose]}`;
2. continuation source citation(sha256 + assurance digest,抄自 view);
3. completed / reserved / do-not-show 边界(view 的 completed_beats /
   excluded_future_beats,别把保留 beat 写进本镜);
4. re-anchor 建议:view 说链太深(canonical_ref_reanchor_recommended)就提
   「回锚 canonical reference」的 proposal——只是建议,不自动换 ref。

## 失败条件

- 源镜头无 accepted take / endpoint 未观察 → 停(见闸);
- 下一镜 source 被锁定 → 走 `manju director propose`;
- 用户要求"按剧本预期结尾续" → 指出与真实结尾的冲突,让人裁决(改剧本 or 重拍源镜头)。

## 纪律

- patch 落地后必由现有 workbench 重编译(`manju prompt <next_shot> --json`),
  确认 CONTINUATION_* 检查为空;
- 本 skill 不调 Provider、不 build、不花钱;continuation view 只进你的输入,
  永不直接进 Provider request。
