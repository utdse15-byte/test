# 术语速查(核心协议 §10 的第三层)

> 从 `skills/manju/SKILL.md` 挪来(2026-08-01)。跟人沟通用左列白话,
> 跟引擎打交道用右列内部词/命令。和 GUI 的术语表同源。

跟人沟通时用左列白话;跟引擎打交道时用右列内部词/命令。

| 白话 | 内部 / 命令 | 含义 |
| --- | --- | --- |
| 生成来源 | provider / routing | 这条画面/配音由哪个 AI 模型或服务做出来 |
| 版本 / 这一条 | take_NN / final_vN | 同一镜头反复生成的候选、只增不改的成片版本 |
| 待更新 / 需重做 | stale | 上游改过、这条还是旧的;默认不动,选择仍生效(§7) |
| 兜底 / 备用方案 | fallback / 降级链 | 首选失败时逐级退到断网也能出的本地能力(§8) |
| 智能派单 | routing.yaml / `manju route explain` | 按 draft/review/key_shot 分层自动派 provider |
| 制作台账 | `manju tasks` | 每笔生成的 provider/状态/花费/失败原因 |
| 配套信息 | sidecar / packaging | 素材旁的参数说明;封面/预告/片头尾/信息卡 |
| 成片清单 / 配方单 | manifest / `manju exports` | 9 项交付物 × 上新/待更新/缺失/有问题/待人工确认 |
| 质量检查 / 质检 | `manju qc` | 存在/技术/内容三层校验 → `reports/qc.*` |
| 修复方案 | repair_plan.yaml / `manju repair` | QC 发现 → 逐项修复 op(retime/extend/trim/inout/croppad/voice) |

看不懂某个内部词就查这张表或 `manju skills show <相关技能>`;别自己发明术语。
