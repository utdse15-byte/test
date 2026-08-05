---
name: direct-shot-source-patch
description: 导演判断落地程序——读 Shot+Bible+prompt/refs/preflight 诊断,把"这一镜要表现什么"压缩成一个可见 beat,输出对现有 Shot/Bible/refs source 的窄 patch 或 proposal;绝不直接生成、绝不写派生报告当输入。触发词:导演、分镜、改镜头、beat、运镜、导演判断、direct shot。
when_to_use: 用户要"导"一镜(定 beat/机位/结尾/参考),或 prompt --check 报了 CLIP_SCOPE_* 问题需要重新收窄镜头时。
tags: [directing, task]
user_invocable: true
---

# 导演判断 → source patch(direct-shot-source-patch)

创作决定只有写进现有事实源(Shot/Bible/refs)才存在。你可以自由地做导演判断
(scene function / felt intent / POV / power shift / single visible beat /
opening state / endpoint / future beats to reserve / camera & blocking),
但**交付物只有两种**:

```text
A. 对现有 Shot/Bible/refs 的窄 patch(经 manju director propose 或直接受控写)
B. 纯建议报告 —— 开头必须写明「不影响 build,尚未采纳」
```

禁止:写 `reports/directing/*.json` 之类的派生文件并指望引擎读它(引擎**从不**读
reports/ 来编译 Prompt/请求——这是 08_10_12C 的 source-authority 铁律)。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 这一镜的要求就是「接住上一镜的结尾」 | `continue-from-accepted-take` |
| 要评的是已经生成出来的 take | `review-take-and-route-repair` |
| 要改的字段已锁 | `manju propose` 写提案,永不 `unlock` |

## 输入

- 当前 Shot source(`manju prompt <shot> --json` 的 `shot_spec` + `spec_hash`);
- Bible(角色/场景锁定字段优先);
- `production_checks` + `compiler_trace`(同一 JSON 里,含 surface freshness);
- `manju refs <shot> --json` 的 per-ref lineage;
- 用户的创作诉求(原话)。

## 映射表:导演决定 → 写入位置(只用现有字段)

| 导演决定 | 写入 |
|---|---|
| 当前一个可见 beat | `action.main`(一镜一个 beat;结尾写明落点,如「最后定格在…」) |
| 相机与运动 | `camera.shot_size / movement / angle` |
| 必须可见 / 不得出现 | `quality.must_show` / `quality.avoid`(只写可观察验收项) |
| 连续性事实 | `continuity.locks` / `continuity.prev` |
| 参考选择与迁移 | 现有 refs 绑定的 dict 形式:`{ref: …, controls: […], ignore: […], subject_ref: …}` |
| Provider 参数 | `generation.params`(经 04 preflight 检查) |
| 未来剧情 / 保留 beat | 下一镜或故事 source —— **绝不**塞进本镜 must_show/avoid |

## 输出

1. 一个窄 patch(diff 或 `manju director propose` 的 action 列表);
2. 三行解释:当前可见 beat / endpoint 落点 / camera & ref 变化;
3. 不执行任何生成命令。

## 失败条件(直接说,不硬做)

- 诉求需要发明新镜头而用户未授权 → 停,提议新增 shot 的 proposal;
- source 被锁定(CAS/locks)或 proposal 已 stale → 停,走 `manju director` 流程;
- 意图无法压缩成单 clip 的一个 beat → 停,输出拆镜建议(引用 CLIP_SCOPE 检查的 split 提议);
- Provider capability 不支持(preflight INCOMPATIBLE)→ 停,改参数或换 provider 的 proposal。

## 纪律

- 改完 source 后由现有 workbench 重新编译:`manju prompt <shot> --json`,确认
  `spec_hash`/`prompt_bundle_digest` 移动、`production_checks` 变干净;
- 一次 patch 解决一个诊断;不顺手重写无关字段;
- 本 skill 不花钱、不 build、不 select。
