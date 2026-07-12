# AI_IDE_14_21_CLOSEOUT — 基线审计报告 (Baseline)

日期：2026-07-12 · 分支：`claude/cost-optimization-strategy-cjfmn5`
基线 HEAD：`4200109fe9e19cc56e73f0536664289eba53cf0d`（与合同审计 HEAD 逐字节一致；其 CI run 29167162344 = success，全量 3114 passed / 13 skipped / 0 failed）。
方法：合同 §1 入口指令——第一步不是编码。先以当前 HEAD 重新审计合同每条指控（编排者定向源码核对 + 三条轨道各自把全部红灯测试**先写先跑**在基线上取证），后修复。
范围：仅 C1–C5。**C6（真实 Provider/真实模型校准）本轮由操作者明确指令整体 SKIPPED_WITH_EVIDENCE**（"本轮验收仅限离线理论闭环"）——基线审计不含任何真实网络动作。

## 0. 审计结论总览

38 条合同红灯测试（Q01–Q14、B01–B12、M01–M14 之 14、S01–S06、A01–A06）在基线 **38/38 全部失败**（另加 1 条 Q13b 附加桩与 1 条 C3 集成测试也失败）——合同的每一条指控都在 4200109 源码上得到证实，无一条是凭空的，也无一条失败是制造出来的（新 API TypeError 掩盖语义缺陷处，均另行用旧 API 在基线单独取证）。

最严重的三项（按风险排序）：

1. **可编辑派生报告实为网络准入权威（C1/Q06–Q08）**：`providers/qualification.py::_stored_evidence`（基线 :444-449）直接 `read_report(...)["evidence"]`——伪造一份合法 JSON 报告即可**扩大 admission**，手改 `evidence.level` 即可升权，删除报告文件即可**抹除已挣得的资质记忆**（matrix 跌回 CONFIG_VALID）。这正是合同 §2 第 1/9 条禁止的形态。
2. **bridge 资质闸没有任何强制 caller（C2/B05/B06）**：`build/bridge.py:31-33` 注释声称闸"called from cli.py"，但 cli.py **不存在任何 bridge 命令**（仅 :6673 一行注释）——直接 Python `execute_bridge()` 可不经 bridge 地板闸抵达 provider。AI_IDE_19 完成报告的 CLI 声称与源码不符。
3. **apply_cutdown 完全不做语义校验（C3/M07–M09）**：基线 `build/segments.py:248-269` 只做 CAS hash 比对，从不调用 `validate_cutdown`——倒置区间、穿禁区、陈旧 analysis 绑定全部可 apply。

## 1. C1 基线证据（Q01–Q14：14/14 FAIL）

| ID | 基线行为 | 根因（4200109 行号） |
|----|----------|----------------------|
| Q01 | disabled provider 照常 admit | `qualification_state` 从不查 `declared["enabled"]`（:141-149 仅 provider_absent/manifest_error 阻断） |
| Q02 | 未声明 capability 照常 admit | `declared_facts`（:326-384）不记录 capability 声明事实，state 不检查 |
| Q03/Q04 | 缺 profile/adapter anchor 仍读 CANARY_ARTIFACT_PASSED | `staleness_drift`（:89-96）只比较**双侧同时存在**的 anchor——"缺失=无漂移" |
| Q05 | canary 级证据缺 fixture/request/response digest 仍算 current | 同上；canary 级无强制 anchor 集合 |
| Q06 | **伪造合法 JSON 报告 → ADMITTED** | `_stored_evidence` 读报告即准入权威（:444-449，:432-441） |
| Q07 | 手改报告 `evidence.level` → 升权 admit | 同 Q06 |
| Q08 | 删除报告 → 资质记忆被抹除（matrix 跌 CONFIG_VALID） | `write_report`（:422-429）是唯一证据 sink，无耐久存储 |
| Q09 | 撕裂/损坏证据被静默当"无证据"，以地板 admit | `read_report` 对 malformed 返回 None（:438-441）→ corrupt 恒等于 UNTESTED-OK，无结构化 corrupt 态 |
| Q10 | DRY_RUN_VALID admit 真 reviewer | `REVIEWER_MIN_QUALIFICATION = DRY_RUN_VALID`（:462） |
| Q11 | DRY_RUN_VALID admit 真 analyzer | `ANALYZER_MIN_QUALIFICATION = DRY_RUN_VALID`（:535） |
| Q12 | DRY_RUN_VALID admit 付费 bridge | `BRIDGE_MIN_QUALIFICATION = DRY_RUN_VALID`（:603） |
| Q13(+13b) | 无耐久证据 API（AttributeError）；anchor 变更不撤销 | 唯一存储即可编辑报告 |
| Q14 | capability 隔离仅存在于报告**文件名**上 | 同 Q13——文件名隔离在可编辑存储上毫无强制力 |

## 2. C2 基线证据（B01–B12：12/12 FAIL）

| ID | 基线行为 | 根因 |
|----|----------|------|
| B01 | 仅 description 不同的两个 plan **digest 相等**（实测取证） | digest 只含 p/n/duration/direction/params（bridge.py:65-67） |
| B02 | endpoint 字节无法进入 plan（只收 hash 字符串） | `plan_bridge` 仅接受裸 hash（:44-47） |
| B03 | provider 收不到任何 endpoint 媒体；hash-only 可执行 | plan 只作为 lineage 骑 `params["bridge"]`（:80-87） |
| B04 | 执行时不重新 hash endpoint 文件 | `execute_bridge`（:74-90）无任何执行时校验 |
| B05 | 直接 `execute_bridge()` 不经 bridge 闸抵达 provider | 闸无强制 caller（cli.py 无 bridge 命令；见总览 2） |
| B06 | CLI/MCP/直调不存在共享闸 | 同 B05 |
| B07 | `spec_hash: str = "bridge"` 固定占位默认（:74-75，实测取证） | 固定 spec 身份 |
| B08 | **任意 `{passed:true, bound_hash:...}` dict 即可 adopt**（实测取证） | `adopt_bridge`（:131-142）只查 passed+bound_hash |
| B09 | 无 state/spec/packet 绑定检查 | 同 B08——只比 bound_hash |
| B10 | 无 Proposal 输出（adopt 只回 lineage+review） | :131-142 |
| B11 | **改名复制 bridge 媒体不被 final 守卫捕获**（实测取证） | `assert_not_in_final`（:148-163）只比 take 名 |
| B12 | 闭环恢复链路不存在（底层 DR06 机器在，bridge 无法确定性走到） | plan/spec 身份基线不可确定性派生 |

## 3. C3 基线证据（M01–M14 + 集成：15/15 FAIL）

| ID | 基线行为 | 根因（4200109） |
|----|----------|------------------|
| M01 | 媒体文件消失后仍读 **ANALYZED** | `analysis.py:188` 只在 `pinned and media_path.exists()` 时查 staleness，缺文件穿透到 ANALYZED |
| M02 | 被篡改报告原样返回被消费 | `read_report`（:166-174）只验"是 JSON dict" |
| M03 | 低置信/UNKNOWN 轨道全量流入自动消费者 | `roi_tracks`（:204-212）无 unknown_axes/confidence 闸 |
| M04 | 切穿 key moment **不产生阻断** | key moment 存成零宽 `[t,t]`（:233-235）+ 验证器用严格 `zs<b<ze`（segments.py:216-225）永不触发 |
| M05 | 开放尾区间直接 `int(None)` 崩溃 | `_ranges`（:174-182）无开尾支持，也无解析 |
| M06 | 重叠 remove 区间原样透传 | `to_cutdown`（roughcut.py:117）不合并 |
| M07 | 倒置区间照常 apply | `apply_cutdown`（:248-269）只 CAS，不调验证器 |
| M08 | apply 无禁区参数/检查 | 同 M07 |
| M09 | apply 不比对 analysis digest | 同 M07 |
| M10 | 乱序 keyframes 原样消费 | `reframe.py:167` 按原始顺序读 times |
| M11 | 多轨按**数组下标**对齐（不同采样率错位） | :157-159, :170-172 |
| M12 | 无 ROI → 静默居中裁剪报 `ok`（冒充智能成功） | :149-151 |
| M13 | 低置信 ROI 报 `ok` | compile 从不读 confidence |
| M14 | 输出时间轴可含重复/非递增 | `_rate_limit:103` 的 `max(1,...)` 掩盖非正 dt |

## 4. C4 基线证据（S01–S06：6/6 FAIL）

- **S01–S03**：`active_variant`（series_state.py:172）单选模型——"最新 valid_from 胜出，平手取字典序"；`variant_diagnostics`（:86）却允许 disjoint 重叠共存。**诊断允许的，runtime 只选一个**——恰是合同 §5 明令禁止的分裂：同集两个不相交 variant（如 wardrobe+voice）只有一个生效；同字段矛盾不阻断而是静默择一。
- **S04/S05**：world-state 冲突键 `(subject, field, episode)` 无 scope（:288+）；`world_state_at(series, eid)`（:303）无 scene/shot 参数——scene 专属状态跨 scene 误报冲突，shot 专属状态泄漏整集。
- **S06**：`_episode_health`（:405+）对缺失 SQLite 记 `unresolved_submissions=None`+note；`season_health`（:465+）用 truthiness `if row.get("unresolved_submissions")` ——**None 被当 0 处理，投影不可读时季仍可 ready**。

## 5. C5 基线证据（A01–A06：6/6 FAIL）

- **A01/A02**：`_resolve_source` 返回 None 的 expected 源被**静默丢弃**（`_render_bus` 无记账），母带以数字静音顶替且照常"验证通过"；index 无 expected/resolved/dropped。
- **A03**：`_mix_files` `amix normalize=0` 裸相加，无 headroom/limiter；true peak 虽测量（`measure_loudness:218`）但超限不产生任何阻断。
- **A04**：`FULL_MIX`（:354-357）= 裸四总线和，未复用 program mixer 链，命名暗示成品混音；ambient 藏进 SFX_STEM。
- **A05**：审计确认 `media/render.py::_build_audio_graph` 才是真 program mixer（sidechaincompress ducking + loudnorm I=-14:TP=-1.5 → AAC 入 final.mp4）——masters 与其无任何复用/可校验关系，也无 roles_absent 记录。
- **A06**：`M_AND_E_MASTER` 命名 + `excludes_dialogue` 字段暗示内容级"无对白证明"，实际仅为总线排除。

## 6. §8 文档失实项（基线核对）

1. 19 完成报告 (f)/(k)(4) 称 bridge 闸"cli 调用"——cli.py 无任何 bridge 命令，**失实**。
2. toolmap 在 19 报告 (g) 中称"派发"——`resolve_tool` 只解析/校验/报价，从不调用 executor，应称 whitelist resolver。
3. 18 报告 (b) "M&E 证明不含对白"——超出总线排除所能证明的范围。
4. 14 报告 §1.2 "报告可删除"——但删除即抹除 admission 记忆（见 Q08），描述与实现互相矛盾。
5. 20B 报告已如实标注"rates prove the harness, not any real model"（:35）——**无需更正**。
6. 21G 保持 SKIPPED_WITH_EVIDENCE——基线无画布/scheduler 实现，本合同也不得实现。

## 7. 基线全局状态

- 全量测试：3114 passed / 13 skipped / 0 failed（本地）；CI success（run 29167162344）。
- 以上缺陷全部处于**离线可证**范围——基线取证零真实网络调用、零费用。
- 修复轨道划分：A=C1+C2（qualification.py/bridge.py/base.py/cli.py），B=C3（analysis/segments/roughcut/reframe），C=C4+C5（series_state/masters/delivery）——文件两两不相交，B12 复用 DR06/P0 既有恢复机器，未建任何新 ledger。
