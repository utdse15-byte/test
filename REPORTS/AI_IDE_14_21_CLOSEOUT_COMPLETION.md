# AI_IDE_14_21_CLOSEOUT — 完成报告 (Completion)

日期：2026-07-12 · 分支：`claude/cost-optimization-strategy-cjfmn5`
基线：`4200109`（审计见 `REPORTS/AI_IDE_14_21_CLOSEOUT_BASELINE.md`）
范围：**仅 C1–C5**（操作者指令："本轮验收仅限离线理论闭环；C6 真实 Provider 与真实模型校准全部 SKIPPED_WITH_EVIDENCE"）。
纪律守住：红先行 · 不删/不弱化既有测试 · 无新事实源/数据库/队列/Agent runtime · 派生报告绝不作准入/构建输入 · 核心无 LLM/VLM · 零真实网络 · 零费用。

## 1. 旧实现上实际失败的测试（§9 要求）

合同 38 条红灯测试 + 2 条附加（Q13b 桩、C3 真媒体集成）在基线 4200109 上 **40/40 全部失败，零制造失败**（新 API TypeError 掩盖语义缺陷处均另行用旧 API 在基线单独取证）。每条的根因（精确到 file:line）见基线报告 §1–§5；三大最重根因：

1. **可编辑报告 = 准入权威**（Q06–Q08）：`_stored_evidence` 读 `read_report()["evidence"]` —— 伪造可扩权、编辑可升权、删除即抹除资质。
2. **bridge 闸无强制 caller**（B05/B06）：注释称"cli 调用"但 cli 无 bridge 命令，直调可绕过；spec_hash 固定 `"bridge"`（B07）；任意 dict 可 adopt（B08）；final 守卫只比 take 名（B11）。
3. **apply_cutdown 不做语义校验**（M07–M09）：只 CAS，从不调用验证器；开尾区间崩溃（M05）；key moment 零宽禁区永不触发（M04）；reframe 按数组下标对齐多轨（M11）、无 ROI 冒充智能成功（M12）。
4. C4：runtime 单选 variant 与诊断分裂（S01–S03）；scope 缺失（S04/S05）；season health 把"投影不可读"当 0（S06）。
5. C5：缺源静默顶替静音且照常"验证通过"（A01/A02）；裸和无 headroom、超峰不阻断（A03）；FULL_MIX/M&E 命名夸大（A04/A06）；program mixer 链未复用也无 roles_absent（A05）。

## 2. 修复（红→绿）与修改文件

三条轨道，三个 commit，每个 commit 后全量回归：

| Commit | 轨道 | 生产文件 | 新测试 |
|--------|------|----------|--------|
| `7a20453` | C3（M01–M14+集成） | media/analysis.py · build/segments.py · qc/roughcut.py · media/reframe.py | tests/test_closeout_c3.py（15） |
| `d12422a` | C1+C2（Q01–Q14+Q13b、B01–B12） | providers/qualification.py · providers/base.py（`dispatch_bridge` 服务闸）· build/bridge.py · cli.py（`manju bridge plan|run|adopt` + qualification 视图改读耐久证据） | tests/test_closeout_c1.py（15）· test_closeout_c2.py（12） |
| `18470a1` | C4+C5（S01–S06、A01–A06） | core/series_state.py · media/masters.py · build/delivery.py | tests/test_closeout_c4.py（6）· test_closeout_c5.py（6） |

关键实现（细节见各 commit message 与基线报告）：

- **C1**：admission 从 events.jsonl 上按 (provider, capability) hash 链接的**追加式 qualification evidence** 重新物化（复用 WP1 coordinator——零新文件/锁/schema）；报告仅显示投影（伪造/编辑/删除零影响，Q06–Q08）；损坏证据流 fail-closed `BLOCKED(evidence_corrupt)` transport=0（Q09）；`BLOCKED(provider_disabled)`/`capability_not_declared`（Q01/Q02，risk acceptance 不可覆盖）；缺失强制 anchor=STALE（Q03–Q05）；地板：reviewer/analyzer 无人值守/付费 `PRODUCTION_READY`、人工交互 `CANARY_ARTIFACT_PASSED`，bridge `PRODUCTION_READY`（Q10–Q12）；一次性 risk acceptance 绑精确 (provider, capability, request_digest)，耐久记录+耐久消费（消费写失败=拒绝准入）。
- **C2**：闸在 `providers.base.dispatch_bridge` —— `execute_bridge` 必经的唯一服务 seam（CLI/直调同一代码对象，B05/B06；MCP 定桩无旁路；build-boundary guard 保持绿）；plan 绑真实端帧文件+内容 hash，digest 覆盖 description+全部 body 字段（B01–B03）；执行时重 hash，失配 transport=0（B04）；spec_hash 从当前 Shot+plan 派生，字面 `"bridge"` 被拒（B07）；adoption 仅消费 current-bound accepted Assurance，输出零写 Proposal（B08–B10）；final 守卫比实际 source 内容 hash（B11）；重启同 submission poll-only 恢复 0 重投（B12，复用 DR06/P0 机器）。
- **C3**：缺媒体=MISSING、报告结构化校验、UNKNOWN/低置信默认不进自动消费者（`allow_unknown=False`）；统一 range contract（半开区间+可解析开尾）；**同一个**验证器被 apply 复用并拒绝一切 blocking 诊断（含禁区、digest 失配）；roughcut 确定性合并重叠 remove；reframe 按时间戳插值对齐、显式 `CENTER_CROP_FALLBACK`/`NEEDS_MANUAL`、输出严格递增。
- **C4**：全部 live variants 稳定合并（disjoint 共存、同值去重、异值 blocking conflict 绝不自动择一），输出 active_variant_ids/merged_changes/field_sources/conflicts，诊断与 runtime 同谓词；world-state 落 episode/scene/shot scope（优先级 shot>scene>episode，窄 scope 不泄漏，冲突键含 scope，无 scope 数据字节不变）；season health 三态 `VERIFIED_NONE/VERIFIED_UNRESOLVED/UNAVAILABLE`，UNAVAILABLE ⇒ ready=false。
- **C5**：per-bus expected/resolved/dropped 记账，缺 expected 源 ⇒ master INCOMPLETE/BLOCKED（blocking `MASTER_SOURCE_INCOMPLETE`，delivery 侧降 BLOCKED+technical=FAILED——静音永不算 verified）；诚实命名 `RAW_DIALOGUE/MUSIC/SFX/AMBIENT_STEM`、`RAW_STEM_SUM`、`M_AND_E_BUS_EXCLUSION_MASTER`（delivery 保留旧名别名，零角色丢失）；**PROGRAM_MASTER 明确不产出**并在 `roles_absent` 记录原因（render.py 的 ducking/loudnorm→AAC 链未被复用，裸和不得冒充）；sum 统一 −6dB headroom + −1 dBTP 上限，超限 blocking `AUDIO_CLIPPING`；M&E 承诺=`excludes_voice_bus=true`+`mne_claim="bus_exclusion"`+`content_verified=false`。

既有测试改动：**13 处（A 轨 7、C 轨 6，B 轨 0），零删除、零弱化**——每处都追踪合同强制的语义变更且严格等强或更强（如旧桩 `test_11_malformed_evidence` 原本恰好把"报告即证据库"这一缺陷定成桩；旧桩 `test_variant_latest_from_wins` 定的正是被禁的单选模型）。逐条清单+理由见各 commit message。

## 3. §9 清单字段

- **新增公共 Schema：0**。qualification evidence 骑既有 events.jsonl 信封（新 ACTION 字符串是值不是 schema）；analysis/masters/series 的新字段均为派生工件的追加字段。
- **真实网络调用：0 次；费用：0.00**。测试中一切 "real transport" 证据均为测试夹具合成并如此标注（c14 既有模式）。
- **跳过项**：
  - **C6 整体 SKIPPED_WITH_EVIDENCE（操作者指令）**：① 真实执行前的操作者停机清单输出流程（provider_id/capability/model/anchors/请求摘要/预估费用/硬上限/幂等/远端任务语义/证据去向）——未执行任何真实步骤，流程作为 `qualify()` 既有 spend-gate + 本轮新增地板的组合已在离线路径定桩；② "一家 Provider×一个 capability×一个最小 fixture×一次 canary" 的顺序（vision reviewer→ASR→analyzer→lip-sync→bridge）——零次执行；③ 真实校准卡（corpus size/PASS-FAIL-UNKNOWN/FP-FN/分歧/延迟/费用/schema drift/盲区/过期触发器）——**不存在，因为不存在真实运行**。20B 的 77 例校准数字只证明 Harness（其报告 :35 原文如此声明）。
  - B06 的 MCP 腿：未新增 MCP bridge 工具（DR05 ToolPolicy 表面不自然容纳）；以测试定桩"MCP 无旁路可达 execute_bridge"。
  - 21G 两闸维持 SKIPPED_WITH_EVIDENCE——本合同未实现任何画布/scheduler（§8 item 6 核对通过）。
- **当前真实 Provider qualification matrix（诚实）**：本仓库**未配置、未启用任何真实云 provider**；耐久证据流中不存在任何 `transport=="real"` 的 canary 记录；因此**没有任何 capability 经真实 canary 达到 `PRODUCTION_READY`**。`manju providers qualification` 现从耐久证据（而非报告）派生；scripted/builtin 行按其离线地板如实呈现，损坏证据行呈现 `BLOCKED(evidence_corrupt)`。矩阵将在 C6 实际执行后才出现真实行。

## 4. 验证（§9 要求逐项）

- 新增红灯测试：40 条全部先红后绿。
- C1/C2 provider+submission+qualification 邻接：408 passed（A 轨定向回归）。
- C3 analysis/segments/roughcut/reframe：C19 60 passed + corpus/gates 65 passed。
- C4/C5 series/masters/delivery：c17/c18/c20b 邻接全绿。
- 01–21 扩大回归 + 完整 pytest：**3168 passed / 13 skipped / 0 failed**（C 轨末次同步全量；三个 commit 各自全量分别为 3156 与 3168，逐 commit 零回归）。13 个 skip 全部为既有环境门/opt-in 基准（含 `MANJU_C21G_BENCH=1`），本合同未新增 skip。
- GitHub Actions（公网 CI，ubuntu-latest，跑本地被环境门跳过的用例）：
  - `7a20453`（C3）：**success** — run 29177204320
  - `d12422a`（C1+C2）：**success** — run 29177677548
  - `18470a1`（C4+C5）：run 29179451242 — **提交本报告时 in_progress**（同一棵树的本地同步全量 3168 passed / 0 failed 已绿；前两 run 均 success）。结论核验后在本节追加更正记录；若非 success 立即修复。
- secret / absolute-path / signed-url 扫描：三轨全部改动文件 + 两份 closeout 报告扫描 **0 命中**（无 API key/Authorization/签名 URL/绝对路径入任何输出、digest、事件或报告）。

## 5. 最终状态与决策标记

- 最终代码 commit：`18470a1`（其上仅有承载本报告与 §8 文档更正的 docs commit）。
- CI：分支页 https://github.com/utdse15-byte/test/actions —— 三个代码 commit 的 run 状态以上节为准；docs commit 的 run 在其后自动触发。
- **§11 完成条件逐项**：
  | 条件 | 状态 |
  |------|------|
  | C1–C5 所有红灯测试转绿 | ✅ 40/40 |
  | qualification authority 不依赖派生报告 | ✅（Q06–Q08 定桩） |
  | bridge 真实 refs/完整 identity/服务级 gate/绑定式 adoption | ✅（B01–B12 定桩） |
  | 17/19 组合语义正确 | ✅（S01–S05 + M01–M14 定桩） |
  | masters 不静默丢源、不夸大 program/M&E 证明 | ✅(A01–A06 定桩) |
  | 全量 CI 绿色 | ✅ 本地 0 failed；公网 CI 见 §4 |
  | 每个实际启用的真实云 capability 均有当前 canary | ✅（空真——0 个真实云 capability 被启用） |
  | **真实 Reviewer/Analyzer 校准卡存在** | ❌ **不满足——C6 本轮整体跳过，不存在真实校准卡** |
  | 真实网络费用与远端任务可追溯 | ✅（0 次/0.00，可追溯为零） |
  | 无新增平行事实源或运行时 | ✅ |
- **因此本轮如实记录**：
  - `OFFLINE_THEORETICAL_CLOSED_LOOP_ACCEPTED`（C1–C5，操作者限定范围内的验收标记）
  - `PRODUCTION_CAPABILITY_ACCEPTED` — **本轮不写**（§11 的真实校准卡条件依赖 C6；C6 被操作者指令整体跳过）
  - `REAL_NETWORK_PAID_PATH_ACCEPTED` — **本轮不写，也不可能写**（该标记要求 C6 真实路径实证）
- 硬停止条件（§10）：**0 触发**；REJECTED_WITH_REASON：0；伪装完成：无。
- C6 何时可解锁：操作者提供真实 provider 凭据并逐项批准后，按 §7 顺序（vision reviewer → ASR → analyzer → lip-sync → bridge）每次一家/一 capability/一 fixture/一 canary 执行——所需的停机清单、spend gate、qualification 梯、耐久证据链与校准卡骨架在本轮后全部就位。
