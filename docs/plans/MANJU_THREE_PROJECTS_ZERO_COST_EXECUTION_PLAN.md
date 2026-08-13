# Manju × ViMax × OpenChatCut × Toonflow：严格零成本 IDE 执行计划

生成日期：2026-08-11  
目标仓库：`https://github.com/utdse15-byte/test`  
预算约束：**0 元；禁止所有计费或可能产生账单的模型/API 调用。**

> 本版替代此前包含真实 Provider Proof Shot 的计划。旧版付费任务不得执行，也不得通过“免费额度”“试用金”或人工 override 变相恢复。

## 1. 最终判断

没有预算并不妨碍项目继续前进。当前最合理的路线是把 **架构、工作流、证据链、IDE 协作和本地精剪** 做扎实，用本地假 Provider、FFmpeg 合成素材和用户已有媒体替代云生成。真实模型质量、真实计费和供应商稳定性必须诚实标记为未验证。

```text
外部 IDE + ViMax-style hierarchical-director Skill
                    ↓ existing Director Proposal
                 Manju Core
       ┌────────────┼──────────────┐
       ↓            ↓              ↓
localhost fake   local QC       Workbench
provider         /human review   (Toonflow-inspired)
       ↓
FFmpeg synthetic/user-owned media
       ↓ Picture Lock
OpenChatCut localhost-only finishing（不启用外部 AI）
```

## 2. 严格零成本规则

### 永久禁止

- Any metered or billing-enabled model/provider API, even when a free trial or free tier may exist.
- Any real video/image/audio generation provider submission.
- Any DashScope/Qwen cloud tool, Serper, OpenAI API, Gemini API, Replicate, fal, Runway, Kling, Veo, Seedance or similar remote inference call.
- Loading or storing cloud provider API keys for this execution plan.
- Any non-loopback provider transport from Manju tests, dogfood scripts or OpenChatCut workflows.
- Automatic purchase, subscription, credit top-up or budget increase.
- Automatic multi-gigabyte model-weight download; this requires a separate future user decision even if the model is open source.

### 允许

- Existing IDE/Codex interaction itself; Manju code must not call an additional model API.
- Localhost-only fake providers and mock MCP servers.
- FFmpeg/ffprobe, Python standard library and existing installed open-source dependencies.
- Deterministic synthetic media generated locally for tests.
- User-owned or already-present local media.
- OpenChatCut local editing features that do not call external AI services.
- Normal source-code/package retrieval when no purchase or metered inference is involved; large downloads require explicit user approval.

### 关键解释

- “免费额度”“试用金”“按量付费但这次预计为 0”仍然属于被禁止的远程计费通道，因为它们可能产生账单或改变价格。
- 本计划不要求安装本地大模型。若以后想下载多 GB 权重，先单独评估硬件、磁盘和许可证；不能由 IDE 自动决定。
- Codex/IDE 本身是当前工作界面；禁止的是 Manju 代码、脚本或 OpenChatCut 再调用一个额外的云模型/API。
- OpenChatCut 只能使用本地剪辑、时间线、字幕、音频和导出能力；任何外部生成/转录/搜索功能都不配置 Key。

## 3. 为什么仍然值得做

| 能力 | 零成本下能验证 | 暂时不能验证 |
|---|---|---|
| Manju Provider/任务 | identity、异步恢复、重复提交防护、下载、媒体验证、append-only | 真实供应商限流、计费、签名 URL |
| ViMax 方法 | 层级导演、来源引用、视觉依赖、波次、关键帧工作流 | 真实生成模型的一致性提升 |
| OpenChatCut | 本地时间线、draft session、人工批准、roundtrip | 依赖云 AI 的转录/生成 |
| Toonflow 启发 | 本地工作台、三角色、Provider 禁用态和可视化 | 其完整云短剧流水线 |

## 4. 数据所有权和不可妥协的不变量

- Text project files remain the sole canonical truth.
- Media remains append-only; no candidate, reference, take or finishing result is overwritten.
- Reports, graph projections, UI state, vector indexes and SQLite remain deletable and never become build/provider inputs.
- The LLM stays outside Manju; Manju code does not call an additional model API.
- Director Proposal remains the sole executable proposal store.
- Truth adoption uses the existing human-only atomic truth_patch_set.
- Strict zero-cost mode rejects every non-loopback provider transport before network I/O; there is no human override inside this plan.
- No cloud API key is requested, loaded, persisted or displayed.
- Reviewer observations, KEEP decisions and scores never imply selected_take or Picture Lock.
- Manju MCP is not reintroduced.
- OpenChatCut, ViMax and Toonflow remain optional; absence never blocks core workflows.
- No public-network/model-provider calls occur in tests or dogfood.
- Windows remains a hard release gate on the same commit as Ubuntu.

## 5. IDE 省 Token 执行策略

- 一次只加载：全局不变量、当前任务、直接依赖任务的 evidence。不要每个任务重读整份计划。
- 使用 `rg`、具体函数名和有限行范围，不把整个大文件粘贴到对话。
- 每个任务只跑 focused tests 和 compile/static；完整测试仅在 G0 基线和 H1 发布门运行。
- 长日志写到 `REPORTS/`，IDE 回复只给命令、退出码、失败摘要和证据路径。
- 已有功能足够时标记 `REUSE_ONLY` 或 `NOT_NEEDED`，不要为了“完成任务”而写代码。
- 不重复联网研究三个上游；只有 capability snapshot 过期且当前任务真正需要时才重新固定 commit。

## 6. 阶段顺序

### G0 — 基线、治理与第三方边界

**目标：** 冻结可复现起点，书面确定许可证、数据所有权和 clean-room 边界。

**退出门：** 同一 HEAD 基线证据存在；三方代码未进入核心；ADR 明确采用/拒绝项。

### P0 — 零成本执行闭环与网络硬禁用

**目标：** 先用 localhost 假 Provider、FFmpeg 合成媒体和已有素材验证提交恢复、下载、哈希、QC 与人工选择，同时证明任何非本地 provider transport 都被拒绝。

**退出门：** 本地 Proof Shot 完成；模拟远程任务只 submit 一次；零外部模型请求、零 API Key、零账单；STATE 明确真实 Provider 仍未验证。

### D1 — ViMax 风格层级导演规划

**目标：** 将事件链、场次、镜头和摄影机意图变成外部 IDE Agent 工作流，最终仍只采用现有 truth_patch_set。

**退出门：** 真实故事片段可产生可审查、可过期、可原子采用的 Scene/Shot 提案，无第二真相。

### V1 — 视觉依赖图与零成本前置检查

**目标：** 派生跨镜视觉依赖、当前锚点和建议波次；在 strict zero-cost mode 下所有外部 transport 一律硬阻断。

**退出门：** 依赖可解释、可重建、可识别陈旧；本地 preflight 可演练，任何非 loopback provider transport_count=0。

### K1 — 本地关键帧 Best-of-2 与视觉评审

**目标：** 复用 image takes、Preview Ladder、QC v2 和人工选择，用本地合成/已有图片验证两个关键帧候选的 lineage、评审和下游绑定。

**退出门：** 两个零成本候选 append-only、hash-bound 评审；人工采纳的 exact hash 进入本地假视频请求身份；不宣称真实模型质量。

### S1 — 本地空间锚点与引用链

**目标：** 用 FFmpeg 抽帧和已有媒体将 camera-parent/transition-anchor 思想落为 reference-only、可追溯、会过期的 refs。

**退出门：** 父镜变化使锚点过期；子镜本地请求绑定 exact anchor hash；没有云生成或账单。

### F1 — OpenChatCut 本地精剪侧车

**目标：** Picture Lock 后经中立 handoff 进入本地 OpenChatCut；仅使用非 AI、本地编辑能力，经双重人工批准返回。

**退出门：** 导出—本地精剪—零写入分析—人工采用闭环完成；OpenChatCut 未调用外部 AI/API。

### W1 — Toonflow 风格零成本本地工作台

**目标：** 在现有 localhost GUI 实现派生图视图、三角色交互与零成本 provider 状态，不引入 SQLite 真相、动态 TS Provider 或云 Key。

**退出门：** 删除 UI 状态/reports/runtime DB 不改变 build；所有修改只生成现有 Director Proposal；外部 provider 显示为禁用。

### H1 — 零成本故障注入、跨平台与发布判定

**目标：** 证明网络硬禁用、崩溃、陈旧输入、恶意包、第三方不可用和 Windows 差异下仍 fail closed。

**退出门：** 同一 commit Ubuntu/Windows 双绿；测试证明无外部模型调用、无凭据、无隐藏真相；发布声明明确真实模型质量未验证。

## 7. 建议契约与边界

优先复用现有：`manju.truth-patch-set/v1`、`manju.scene-contract/v2`、nested `ShotContract`、`manju.preview-ladder/v1`、`manju.qc.packet/v2`、`manju.qc.verdict/v2`、`manju.production-readiness/v1`、`manju.submission-identity/v2`、现有 handoff/roundtrip。

条件新增的 schema 只有：

- `manju.authoring-context/v1`：stdout-first、source-cited、可删除的外部 Agent 输入。
- `manju.visual-dependency-graph/v1`：只读 projection；preflight 从 current truth 重新计算。
- `manju.finishing-handoff/v1` / `manju.finishing-result/v1`：中立、内容寻址、默认不参与 build。
- `manju.workbench-graph/v1`：GUI 派生图，不包含布局真相。

strict zero-cost policy 的执行语义：

```yaml
network_policy: loopback_only
allow_cloud_credentials: false
allow_metered_provider: false
allow_free_tier_provider: false
allow_automatic_model_download: false
allowed_transports:
  - localhost_http
  - stdio
  - local_files
```

如果仓库已有等价 owner，必须复用；这段只是行为要求，不强制使用这些字段名。

## 8. 完整任务清单

## 阶段 G0：基线、治理与第三方边界

**阶段目标：** 冻结可复现起点，书面确定许可证、数据所有权和 clean-room 边界。

**退出门：** 同一 HEAD 基线证据存在；三方代码未进入核心；ADR 明确采用/拒绝项。

### G0-001 — 冻结零成本版基线与工具链证据

**优先级 / 规模 / 模式：** P0 / S / `automated`  
**依赖：** 无

**目的**

在任何功能修改前记录当前分支、HEAD、工作区、Python、OS、FFmpeg 和基线测试，并确认没有云凭据进入本轮执行环境。

**为什么**

历史绿色只能说明历史 SHA；本轮必须从用户当前 checkout 的准确字节开始，且绝不能自动清理用户改动。

**先读/先审计**

- README.md
- STATE.md
- DECISIONS.md
- CONTRACTS.yaml
- CLAUDE.md
- pyproject.toml
- .github/workflows/ci.yml
- .github/workflows/windows-ci.yml
- tests/CONVENTIONS.md

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/THREE_PROJECTS_BASELINE_<date>.json
- REPORTS/THREE_PROJECTS_BASELINE_<date>.md

**执行步骤**

1. 运行 git status/rev-parse/log，记录实际分支和未提交文件；不自动清理用户改动。
2. 记录 Python、OS、ffmpeg/ffprobe；检查本轮 shell 中常见 provider key 变量是否存在，只记录“已设置/未设置”，绝不记录值。
3. 若发现任何云模型/provider key 已设置，停止并要求在本轮专用 shell 中清除后再继续。
4. 运行 compile/static 和一次基线 full suite；后续普通任务只跑 focused tests，减少 token 和计算浪费。
5. 把命令、退出码、耗时、失败摘要和 HEAD 写入 REPORTS；verbose log 留文件，不粘贴到 IDE 对话。

**Focused tests**

- 基线命令和退出码被记录。
- 证据文件不被 build/runtime 读取。
- 报告不含 secret 值、用户名或私有绝对路径。

**验收标准**

- 工作区和 HEAD 明确。
- 本轮 shell 无云 API Key。
- 基线红时不开始功能开发。

**必须产出的证据**

- 基线 JSON 与 Markdown
- 完整命令日志摘要

**回滚**

- 仅删除本任务新增报告；不改用户工作区。

**立即停止条件**

- 发现用户未提交改动与目标文件重叠。
- 检测到云 API Key 仍处于本轮环境。
- 基础测试红且与本轮无关。

### G0-002 — 建立第三方边界、零成本政策与架构 ADR

**优先级 / 规模 / 模式：** P0 / M / `automated`  
**依赖：** G0-001

**目的**

固定三个上游的 commit/LICENSE/关键能力快照，并把进程、数据、代码复用和失败边界写成治理文件。

**为什么**

OpenChatCut 是 AGPL，Toonflow LICENSE 含附加商业/品牌条件，ViMax 虽为 MIT 也不应把其弱执行层直接移植。

**先读/先审计**

- DECISIONS.md append-only 规则
- docs/SOURCE_LEDGER.yaml
- OpenChatCut README/LICENSE/MCP session
- ViMax README/LICENSE/paper/pipeline
- Toonflow README/LICENSE/VM/provider/task record

**候选修改文件**

- docs/SOURCE_LEDGER.yaml
- DECISIONS.md

**候选新文件**

- docs/architecture/ADR-THREE-PROJECT-INTEGRATION-ZERO-COST.md
- docs/architecture/THIRD_PARTY_BOUNDARIES.md
- docs/runbooks/ZERO_COST_MODE.md

**执行步骤**

1. 记录三个上游仓库当前完整 commit、许可证文件 SHA256 和本轮实际阅读的关键文件。
2. 写清四层架构和 clean-room 规则；OpenChatCut/ViMax/Toonflow 都不得成为 Manju core runtime 依赖。
3. 写入 strict zero-cost 决策：禁止 metered/free-tier cloud inference、禁止 provider key、禁止真实 provider submit、禁止自动大模型权重下载。
4. 允许 localhost fake provider、FFmpeg/Python 合成媒体、已有本地素材和 OpenChatCut 非 AI 本地编辑。
5. 所有第三方能力 optional；缺失不得阻断 Manju check/build/qc/export。
6. DECISIONS.md 只追加新条目，不修改历史。

**Focused tests**

- YAML 可安全加载。
- 核心 runtime dependencies 不含三个上游。
- 静态边界测试覆盖 src/manju、skills 和 package metadata。

**验收标准**

- adopt/adapt/reject 与 strict zero-cost 条款明确。
- 后续 IDE 不需要猜测是否可以调用 free tier：答案始终是不可以。
- 任何付费研究必须另建未来计划。

**必须产出的证据**

- 上游 snapshot YAML
- ADR 与边界测试输出

**回滚**

- 回退本任务独立提交；运行时零影响。

**立即停止条件**

- 无法确定当前 LICENSE 文本。
- 实现目标要求复制受限制源码或素材。

## 阶段 P0：零成本执行闭环与网络硬禁用

**阶段目标：** 先用 localhost 假 Provider、FFmpeg 合成媒体和已有素材验证提交恢复、下载、哈希、QC 与人工选择，同时证明任何非本地 provider transport 都被拒绝。

**退出门：** 本地 Proof Shot 完成；模拟远程任务只 submit 一次；零外部模型请求、零 API Key、零账单；STATE 明确真实 Provider 仍未验证。

### P0-001 — 建立零成本运行策略、外部模型出口硬禁用与本地 Proof 配置

**优先级 / 规模 / 模式：** P0 / M / `automated`  
**依赖：** G0-002

**目的**

审计 ProviderManifest、preflight、submission identity、attempt ledger 和 transport owner，并为本轮建立 localhost-only 的执行策略。

**为什么**

用户明确零预算。最重要的不是挑一个“免费额度”供应商，而是从代码和流程上保证不会误发任何计费请求。

**先读/先审计**

- src/manju/providers/manifest.py
- src/manju/providers/catalog.py
- src/manju/providers/preflight.py
- src/manju/providers/submission.py
- src/manju/providers/generic_cloud.py
- src/manju/providers/base.py
- src/manju/build/attempts.py
- src/manju/build/spend.py
- src/manju/build/readiness.py
- 相关 CLI docs/tests

**候选修改文件**

- 仅在审计证明表达缺口时修改 provider owner；默认不改公共 schema。

**候选新文件**

- docs/runbooks/ZERO_COST_LOCAL_PROOF_SHOT.md
- scripts/dogfood/local_proof_preflight.py
- tests/test_zero_cost_provider_policy.py

**执行步骤**

1. 审计是否已有 offline/network/provider policy owner；优先复用。
2. 定义本轮执行环境：仅 loopback HTTP/stdio/local files；非 loopback provider endpoint 在 transport 前失败。
3. 不要请求或配置任何 provider key；若 manifest 含 secret_ref，只允许 schema/dry-run 展示“未配置”，不得解析值。
4. 选择 localhost fake async provider profile；固定 3–6 秒、720p、候选数 1、cost=0。
5. 运行 catalog/check/prompt check/build dry-run，保存 secret-free request、execution-profile digest、request digest 和 zero-cost policy 状态。
6. 若核心缺少可验证的 network policy，按唯一 owner 做最小 additive 实现；旧项目默认行为不能被意外改变。

**Focused tests**

- strict zero-cost 下非 loopback transport_count=0。
- localhost fake provider 可通过 preflight。
- 无需任何凭据。
- 同输入 digest 稳定。

**验收标准**

- 所有后续任务有可机器验证的零成本/localhost-only 边界。
- 没有真实 provider profile 或 API Key。
- 本任务零网络模型调用、零费用。

**必须产出的证据**

- zero-cost policy snapshot
- localhost provider profile
- preflight/dry-run JSON
- egress-block test

**回滚**

- 删除专用 profile/docs；若新增 policy code，独立回退且旧行为测试仍绿。

**立即停止条件**

- 实现只能靠“提醒 IDE 小心”而不能在 transport 前验证。
- 任何步骤要求 cloud key、free tier 或真实 endpoint。

### P0-002 — 用 localhost 假 Provider 验证请求、时长与零成本身份（条件任务）

**优先级 / 规模 / 模式：** P0 / L / `conditional`  
**依赖：** P0-001

**目的**

在纯本地环境验证整数秒、最小时长、量化步长、分辨率和模拟成本事实是否在 request/preflight/submission identity 中一致。

**为什么**

这项工作可提前发现真实 provider 适配缺口，但不需要连接真实供应商。若现有抽象已满足，则标记 NOT_NEEDED。

**先读/先审计**

- src/manju/providers/manifest.py CostConfig/LimitsConfig
- src/manju/providers/preflight.py
- src/manju/providers/submission.py
- src/manju/providers/generic_cloud.py render values
- src/manju/build/spend.py
- provider execution-profile contracts

**候选修改文件**

- src/manju/providers/manifest.py
- src/manju/providers/preflight.py
- src/manju/providers/submission.py
- src/manju/providers/generic_cloud.py
- src/manju/build/spend.py
- CONTRACTS.yaml（仅有新 schema literal 时）

**候选新文件**

- tests/test_provider_billable_request.py

**执行步骤**

1. 先用 fake manifest 证明 duration/resolution/candidate semantics；不需要则零代码结束。
2. 若需要，定义纯函数 effective_request_facts，输出 requested/submitted/billable duration、resolution、candidate count 和 simulated cost components。
3. fake provider 的实际费用固定为 0；可另用不执行的 synthetic price fixture 测量计费公式，但不得连接外部 endpoint。
4. request body、preflight、identity、attempt evidence 共用同一冻结结果。
5. 旧 manifest 无新字段时 byte-compatible。

**Focused tests**

- 4.2 秒量化到 5 秒的纯函数测试。
- fake provider actual cost=0。
- 任何非 loopback URL 在 transport 前失败。
- 旧 manifest 兼容。

**验收标准**

- 请求与身份事实一致。
- 无真实价格、账号或供应商依赖。
- NOT_NEEDED 时代码零修改。

**必须产出的证据**

- local request truth table
- compatibility snapshot
- zero-network test

**回滚**

- 回退 additive 字段；兼容测试证明旧行为恢复。

**立即停止条件**

- 需要真实供应商文档或请求才能完成。
- 需要破坏 stable provider API。

### P0-003 — 建立零成本异步假 Provider 故障注入测试台

**优先级 / 规模 / 模式：** P0 / L / `automated`  
**依赖：** P0-001, P0-002

**目的**

用 localhost fake provider 复现 submit、ambiguous response、poll、重启恢复、下载和重复执行危险时序。

**为什么**

在零预算下仍可完整验证任务账本、恢复和 append-only 媒体语义。

**先读/先审计**

- 现有 provider/submission/attempt fixtures
- src/manju/providers/base.py
- src/manju/providers/generic_cloud.py
- src/manju/providers/submission.py
- src/manju/build/attempts.py
- src/manju/core/events.py

**候选修改文件**

- 仅在测试暴露真实 bug 时改对应 owner；禁止测试后门。

**候选新文件**

- tests/fixtures/fake_async_video_provider.py
- tests/test_paid_provider_fault_matrix.py
- scripts/dogfood/simulate_paid_provider.py

**执行步骤**

1. 实现 localhost HTTP fake：submit 返回 job_id；poll 可 queued/running/succeeded/failed；download 返回本地生成视频或错误页。
2. 用 FFmpeg 在测试启动时生成确定性短 MP4，不下载模型或素材。
3. 加入提交后响应丢失、persist 后退出、poll 429/503、URL 过期、下载中断、JSON/HTML 伪装 MP4。
4. 同 submission identity 重跑不得再次 submit；ambiguous 状态只能 attach/abandon/reconcile。
5. 测试并发 CAS、媒体验证、hash 和 append-only take。
6. 测试网络策略：只有 loopback fake endpoint 被允许。

**Focused tests**

- 每个故障场景断言 submit_count/poll_count/download_count。
- 新进程恢复原 job。
- 错误媒体不注册。
- CI 不访问公网。

**验收标准**

- 危险时序有自动化回归。
- 零外部请求。
- 无需凭据或账单。

**必须产出的证据**

- fault matrix
- 调用计数和测试输出

**回滚**

- 删除测试台；生产 bug 修复需单独评估。

**立即停止条件**

- 测试必须联网。
- 模拟要求绕过 submission identity。

### P0-004 — 执行一次零成本本地 Proof Shot

**优先级 / 规模 / 模式：** P0 / M / `manual_review`  
**依赖：** P0-003

**目的**

用 localhost 假 Provider 和 FFmpeg 合成媒体验证合同、提交恢复、下载、hash-bound QC 与人工选择完整闭环。

**为什么**

它不能替代真实模型质量验证，但能在零费用下证明 Manju 的执行和证据架构。

**先读/先审计**

- docs/runbooks/ZERO_COST_LOCAL_PROOF_SHOT.md
- manju production status --json
- providers check/catalog
- prompt --check
- build --dry-run
- tasks
- qc brief/verdict/select

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/ZERO_COST_LOCAL_PROOF_SHOT_<date>.json
- REPORTS/ZERO_COST_LOCAL_PROOF_SHOT_<date>.md

**执行步骤**

1. 确认 strict zero-cost policy active，provider endpoint 为 loopback，环境中无云 key。
2. 生成一条带可验证时长/分辨率/帧标记的确定性本地 MP4；不调用生成模型。
3. 通过 localhost fake async provider 提交；记录本地 submission id 和 fake remote job id。
4. 在 queued/running 时故意结束进程，再启动并确认只 poll 原 job、submit_count=1。
5. 成功后下载 localhost 结果，验证 ffprobe facts、媒体类型和 SHA256，注册 append-only take。
6. 生成 QC packet；优先用确定性/人工 observation fixture，不调用云 VLM；用户可做一次人工视觉确认。
7. 用户明确 KEEP/拒绝/select；记录整个零成本证据链。

**Focused tests**

- strict zero-cost/loopback checks。
- submit_count=1。
- manju check/provider/task/QC focused tests。
- 非 loopback sentinel transport_count=0。

**验收标准**

- 零费用、零外部模型请求、零 API Key。
- 中断后不重复 submit。
- 本地媒体被 hash-bound 评审并由人选择或拒绝。

**必须产出的证据**

- zero-cost policy state
- localhost request/identity/attempt
- submit_count
- media SHA/ffprobe
- QC binding
- manual decision

**回滚**

- 媒体 append-only；若不采用，只取消选择或保留为 rejected candidate。

**立即停止条件**

- endpoint 不是 loopback。
- 发现任何 provider key。
- 脚本尝试下载模型/访问公网。

### P0-005 — 冻结零成本 Proof 证据并明确真实 Provider 继续未验证

**优先级 / 规模 / 模式：** P0 / S / `manual_review`  
**依赖：** P0-004

**目的**

把本地模拟闭环写回 STATE/DECISIONS/SOURCE_LEDGER，同时明确真实 Provider、真实付费和真实生成质量因零预算被有意延期。

**为什么**

不能把 localhost fake provider 成功包装成真实供应商验证；诚实的未知状态是架构资产。

**先读/先审计**

- STATE.md
- DECISIONS.md
- docs/SOURCE_LEDGER.yaml
- ZERO_COST_LOCAL_PROOF_SHOT reports

**候选修改文件**

- STATE.md
- DECISIONS.md
- docs/SOURCE_LEDGER.yaml

**候选新文件**

- 无。

**执行步骤**

1. 核对 commit、fake profile digest、media hash、submit_count 和测试证据一致。
2. STATE 记录：本地异步恢复/下载/hash/QC/select 已验证；真实 Provider/付费/模型质量仍未验证且因零预算延期。
3. 明确未验证：真实取消计费、签名 URL、供应商限流、长视频、多候选质量、真实 Windows 云子进程。
4. 将任何旧的“下一步先做真实付费 Proof”改为“未来有独立预算计划后再研究”，不删除历史。

**Focused tests**

- 文档引用文件/SHA 存在。
- STATE 保持 active-only。
- 没有“真实 provider 已验证”的错误表述。

**验收标准**

- 状态声明精确。
- 后续阶段只依赖本地媒体/模拟证据。

**必须产出的证据**

- STATE/DECISIONS/SOURCE_LEDGER diff
- 明确 deferred paid track

**回滚**

- 证据错误时追加更正；不改写历史报告。

**立即停止条件**

- 任何文档试图把 fake provider 称为真实供应商。

## 阶段 D1：ViMax 风格层级导演规划

**阶段目标：** 将事件链、场次、镜头和摄影机意图变成外部 IDE Agent 工作流，最终仍只采用现有 truth_patch_set。

**退出门：** 真实故事片段可产生可审查、可过期、可原子采用的 Scene/Shot 提案，无第二真相。

### D1-001 — 审计现有导演、场次、镜头与外部 Agent owner

**优先级 / 规模 / 模式：** P1 / M / `automated`  
**依赖：** P0-005

**目的**

逐项映射 ViMax 的 event/scene/shot/camera/reference/candidate/checkpoint 到 Manju 现有唯一 owner。

**为什么**

Manju 已有 creation-funnel、SceneContractV2、ShotContract、Director Proposal 和 authoring patch；不应造第二 authoring plan。

**先读/先审计**

- src/manju/core/authoring.py
- src/manju/core/creative.py
- src/manju/core/source_spans.py
- src/manju/story/context.py
- src/manju/story/coverage.py
- src/manju/story/trajectory.py
- src/manju/build/director.py
- src/manju/build/authoring_patch.py
- skills/creation-funnel/SKILL.md
- skills/scene-design/SKILL.md
- skills/shot-design/SKILL.md
- CONTRACTS.yaml

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/VIMAX_CONCEPT_OWNER_AUDIT_<date>.md

**执行步骤**

1. 建立 ViMax 概念→Manju owner 表并标 REUSE/EXTEND/REJECT。
2. 确认 director propose --from-file 可承载唯一 truth_patch_set action。
3. 确认 basis/content-bound confirmation/human-only patch/locks/atomic rollback。
4. 确认 Canonical Video Authoring Plan 只是派生输出，不反向成为可编辑真相。
5. 找不到 owner 证据前禁止新 schema。

**Focused tests**

- 运行 director/authoring/scene/shot owner 现有测试。

**验收标准**

- 不提出 director-proposal/v2 或 editable event graph。
- 每个新接口都有唯一 owner 证据。

**必须产出的证据**

- owner audit report

**回滚**

- 删除审计报告。

**立即停止条件**

- 当前分支已有等价实现但任务仍想复制。

### D1-002 — 增加只读层级导演上下文包（条件任务）

**优先级 / 规模 / 模式：** P1 / L / `conditional`  
**依赖：** D1-001

**目的**

给 IDE Agent 一个稳定、来源可引用、无隐藏 memory 的外部输入；若现有 JSON 命令已足够则零代码。

**为什么**

ViMax 依靠分层上下文/RAG，但 Manju 不能让向量库或摘要成为真相。

**先读/先审计**

- 现有 story context/production status/prompt JSON/GUI API
- src/manju/story/context.py
- src/manju/core/source_spans.py
- src/manju/build/control_view.py
- src/manju/build/readiness.py

**候选修改文件**

- src/manju/story/context.py 或审计后的单一 owner
- src/manju/cli.py 薄适配器
- CONTRACTS.yaml（条件）

**候选新文件**

- src/manju/story/authoring_context.py（条件）
- tests/test_authoring_context_packet.py

**执行步骤**

1. 先组合现有命令验证等价；足够则标 REUSE_ONLY。
2. 若不足，定义实验性 authoring-context/v1：basis hashes、source spans、creative、scenes、shots、threads、trajectory、locks、readiness、allowed roots。
3. 每段文本携带 source_path/source_sha/span；禁止无出处摘要。
4. stdout-first；显式写 reports 仍是可删除派生物。
5. 删除 .manju/reports 后重新输出语义一致。

**Focused tests**

- 输出顺序/digest 稳定。
- source/scene/shot 修改使 basis 变化。
- 无 secret/私密绝对路径。
- legacy 项目诚实输出 unknown。
- CONTRACTS registry。

**验收标准**

- Agent 不靠隐藏向量 memory 获得当前事实。
- 上下文包不是 build input。

**必须产出的证据**

- 示例 JSON
- 删除重建等价测试

**回滚**

- 删除模块/CLI/schema；Agent 退回现有命令。

**立即停止条件**

- 现有接口已足够。
- 实现要求持久化隐藏摘要。

### D1-003 — 实现 ViMax 风格 hierarchical-director Skill 与 eval

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** D1-001, D1-002

**目的**

让外部 IDE Agent 按来源→事件→场次→镜头→视觉依赖建议顺序工作，最终只输出一个现有 truth_patch_set。

**为什么**

要吸收 ViMax 的导演方法，而非其 Provider、文件缓存或 Agent runtime。

**先读/先审计**

- skills 目录规范/eval 约定
- skills/creation-funnel/SKILL.md
- skills/scene-design/SKILL.md
- skills/shot-design/SKILL.md
- skills/direct-shot-source-patch/SKILL.md
- src/manju/build/authoring_patch.py payload

**候选修改文件**

- skills/creation-funnel/SKILL.md（仅路由引用）
- docs/SOURCE_LEDGER.yaml

**候选新文件**

- skills/hierarchical-director/SKILL.md
- skills/hierarchical-director/references/OUTPUT_CONTRACT.md
- tests/evals/hierarchical_director/*

**执行步骤**

1. Skill 明确六步：读取 basis→建立 source-cited event chain→聚合 SceneContractV2→设计 ShotContract→提出视觉依赖建议→输出一个 truth_patch_set action。
2. 事件不是新 truth 文件；编译到现有 scene/shot owner。
3. unknown/assumption/conflict 单列，不允许补全想象覆盖原文空白。
4. 每个 patch 带当前 base_sha256/payload_sha256；不直接写文件。
5. 禁止调用任何 provider、修改 selected_take、写 Picture Lock 或把 reports 当 build input。
6. 写至少 10 个 eval；使用静态 fixture，不调用外部模型 API。

**Focused tests**

- Skill/frontmatter 发现测试。
- eval 输出唯一 truth_patch_set、无 provider action、无锁字段变化。
- 示例 payload 通过零执行 propose 验证。

**验收标准**

- Codex/Claude IDE 可直接调用。
- 产物是 Manju 原生提案。
- 来源引用完整。

**必须产出的证据**

- Skill eval 结果
- 完整 proposal 示例

**回滚**

- 删除 Skill；核心无依赖。

**立即停止条件**

- 需要复制 ViMax prompt/code 原文。

### D1-004 — 补强 truth_patch_set 的 diff、影响与陈旧反馈

**优先级 / 规模 / 模式：** P1 / M / `automated`  
**依赖：** D1-003

**目的**

让多文件导演提案在人工确认前清晰展示来源、锁冲突、过期影响和将失效的证据。

**为什么**

现有 patch/confirmation/CAS 已正确，应增强可用性而非新增执行通道。

**先读/先审计**

- src/manju/build/director.py
- src/manju/build/authoring_patch.py
- src/manju/build/authoring_impact.py
- director CLI/GUI proposal pages

**候选修改文件**

- src/manju/build/director.py（最小 additive diagnostics）
- src/manju/build/authoring_impact.py
- src/manju/cli.py/GUI 薄展示

**候选新文件**

- tests/test_hierarchical_truth_patch_proposal.py

**执行步骤**

1. 保持 action whitelist 和 sole proposal store。
2. impact 增加 scene/shot/source spans、lock conflict、story lint/screen coverage/animatic staleness。
3. show/diff 显示 path/base hash/payload hash/create-or-modify/affected ids，不在日志复制敏感全文。
4. stale 告诉 IDE 哪些 basis 变化，禁止自动 rebase。
5. 若现有行为已足够，只补测试/文档，不重构大型 director.py。

**Focused tests**

- 篡改 action/content/estimate 后确认拒绝。
- 并发 run 只有一个 CAS 成功。
- base 变化零写入。
- paid+truth 混合与锁字段变化拒绝。

**验收标准**

- 人在 confirm 前能理解计划与风险。
- AI 无法伪造 human confirmation。
- 旧 proposal 可读。

**必须产出的证据**

- CLI/GUI JSON snapshot
- stale/CAS tests

**回滚**

- 回退展示增强；核心语义不变。

**立即停止条件**

- 需要拆分 director.py 大文件；该重构不属本计划。

### D1-005 — 导演规划 Proof：真实故事到原子 Scene/Shot 提案

**优先级 / 规模 / 模式：** P1 / M / `manual_review`  
**依赖：** D1-004

**目的**

用 3–5 场、8–15 镜真实/授权内容验证来源保持、状态变化、镜头合同和人类 diff 审核。

**为什么**

合成 eval 不足以证明导演工作流。

**先读/先审计**

- 真实或授权故事片段
- authoring context
- director propose/show/confirm/run
- check/story lint/coverage/production status

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/HIERARCHICAL_DIRECTOR_PROOF_<date>.md

**执行步骤**

1. IDE Agent 使用 Skill，只生成 proposal 文件。
2. 运行 director propose，保存零费用 impact。
3. 人审 source citations、entry/exit state、shot purpose/opening/endpoint、风险。
4. 故意改一个 basis 文件，证明旧 proposal 过期零写入；重新生成。
5. 人类 confirm/run；运行 check/lint/coverage/status/git diff。
6. 记录人工修订比例和误差，不冒充 benchmark。

**Focused tests**

- 采用前后 manju check。
- stale test。
- paid attempt/event 为零。
- 删除 reports 后真相完整。

**验收标准**

- 真实 Scene/Shot 进入既有 owner。
- 无 event graph/hidden memory。
- 最终只有原子采用文本为 truth。

**必须产出的证据**

- proposal YAML
- human notes
- git diff
- zero-paid proof

**回滚**

- 用现有 snapshot/rollback 恢复；保留 proposal 历史。

**立即停止条件**

- 内容授权不清。
- Agent 绕过 proposal 直接写 truth。

## 阶段 V1：视觉依赖图与零成本前置检查

**阶段目标：** 派生跨镜视觉依赖、当前锚点和建议波次；在 strict zero-cost mode 下所有外部 transport 一律硬阻断。

**退出门：** 依赖可解释、可重建、可识别陈旧；本地 preflight 可演练，任何非 loopback provider transport_count=0。

### V1-001 — 派生视觉依赖图，不改现有 build graph

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** D1-005

**目的**

从 continuity、Scene/Shot state、角色/道具、refs 和 exact-media evidence 计算跨镜依赖，只用于解释和检查。

**为什么**

ViMax 的视觉 DAG 有价值；但 Manju graphdiag 明确诊断不是调度真相，核心 graph 当前无跨镜边。

**先读/先审计**

- src/manju/build/graphdiag.py ruling
- src/manju/core/models.py Continuity/characters/props/generation
- src/manju/core/authoring.py
- src/manju/providers/refs.py
- src/manju/qc/production.py continuation/keyframe
- src/manju/build/readiness.py

**候选修改文件**

- CONTRACTS.yaml
- src/manju/cli.py 或现有 explain 薄层

**候选新文件**

- src/manju/build/visual_dependencies.py
- tests/test_visual_dependency_view.py

**执行步骤**

1. 定义实验性 visual-dependency-graph/v1；纯函数结果，不读取 materialized 报告。
2. 边类型至少 continuation、character_identity、appearance、location_state、prop_state、spatial_anchor、eyeline/motion、lighting_weather、reference_binding。
3. 每边包含 from/to/subjects/source_paths/strength/reason；共享角色默认 advisory。
4. hard 只来自显式 truth：continuity.prev、exact refs、明确 carry-forward state。
5. 节点显示 current selected media hash、keyframe adoption、accepted endpoint、ref digest 的可用性。
6. deterministic sort/digest/cycle/unknown diagnostics；CLI 复用 explain/production 命名。

**Focused tests**

- 同 truth 多次字节稳定。
- 删除报告重建一致。
- continuity.prev=>hard；共享角色=>advisory。
- cycle/unknown/self-edge 明确。
- build graph 既有无跨镜测试不变。

**验收标准**

- 图能解释等待/复核原因。
- 不修改 build/graph.py 调度。
- 报告不是 build input。

**必须产出的证据**

- 示例 graph JSON
- cycle/unknown snapshots

**回滚**

- 删除模块/命令/schema；build 行为零变化。

**立即停止条件**

- materialized graph 被用作执行输入。
- 用隐藏 VLM 记忆推断 hard edge。

### V1-002 — 实现 exact-anchor readiness 与陈旧诊断

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** V1-001

**目的**

判断 hard dependency 的父镜 exact media/endpoint/ref 当前是否可用，并给出零付费修复路径。

**为什么**

依赖关系本身不等于锚点可用；必须区分 authored expectation 与 current observed evidence。

**先读/先审计**

- src/manju/qc/production.py accepted_observed_state/continuation_view
- src/manju/qc/assurance.py
- src/manju/providers/refs.py
- src/manju/build/readiness.py
- src/manju/qc/prompt_checks.py

**候选修改文件**

- src/manju/build/visual_dependencies.py
- src/manju/build/readiness.py（只读 detail）
- src/manju/qc/prompt_checks.py（若 owner 合适）

**候选新文件**

- tests/test_visual_dependency_readiness.py

**执行步骤**

1. 状态枚举：ready、missing_source_media、source_not_accepted、endpoint_unobserved、hash_mismatch、ref_missing、ref_stale、cycle、unknown。
2. hash 比较基于当前字节；不得用 authored endpoint 代替 observed endpoint。
3. 诊断附 source/evidence paths、建议现有命令、do_not_execute_automatically。
4. 第一版只进 explain/prompt-check/readiness detail，不硬阻塞。
5. 长 continuation 链 re-anchor 建议复用现有 continuation_view。

**Focused tests**

- 父镜重选=>旧 source hash mismatch。
- QC binding 漂移=>stale。
- 无 observed END 不 ready。
- 修复后 deterministic ready。

**验收标准**

- 每个阻塞有证据和下一步。
- 不自动写 refs/select/redo。

**必须产出的证据**

- readiness truth table
- CLI JSON examples

**回滚**

- 回退 diagnostics 接入；保留基础 view。

**立即停止条件**

- 需要读取保存的 graph 报告。

### V1-003 — 加入 strict zero-cost 外部 transport 硬阻断，并保留视觉依赖本地门控

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** V1-002, P0-005

**目的**

在本轮 strict zero-cost mode 下，任何非 loopback provider 请求都在 transport 前拒绝；视觉依赖 readiness 只对本地 fake/imported workflow 进行演练。

**为什么**

用户不允许付费，因此本计划不存在 human override。未来若预算改变，必须另建计划而不是关闭一个复选框。

**先读/先审计**

- 现有 unattended/tool policy/keyframe gate/production readiness owner
- src/manju/providers/preflight.py
- src/manju/build/readiness.py
- src/manju/qc/production.py
- project/creative policy fields

**候选修改文件**

- 审计后的单一策略 owner
- src/manju/providers/preflight.py
- src/manju/build/readiness.py
- CONTRACTS.yaml（条件）

**候选新文件**

- tests/test_visual_dependency_paid_gate.py

**执行步骤**

1. 复用 P0 的 network policy owner；不要再创建第二策略字段。
2. strict zero-cost active 时，非 loopback endpoint 永远 transport_count=0，无 human override。
3. local fake provider 仍从 current truth/evidence 重算 visual dependency readiness；不读保存图。
4. 门控失败只给零成本修复命令：补 current local media、更新 QC、重绑 ref、修复 cycle。
5. 旧项目在未启用 strict zero-cost execution profile 时保持原有行为；本计划的 IDE/runbook 永远启用它。

**Focused tests**

- non-loopback=>transport_count=0。
- loopback+missing anchor=>preflight fail。
- loopback+current anchor=>本地执行。
- 不存在 human network override。
- 篡改图报告无效。

**验收标准**

- 本轮不可能误触真实 provider。
- 视觉门控来自 current facts。
- 无跨镜 scheduler。

**必须产出的证据**

- egress-block tests
- local visual-gate examples

**回滚**

- 移除本轮 profile/测试接入；不要弱化生产既有 spend gate。

**立即停止条件**

- 实现依赖保存图。
- 存在可绕过的外部 transport override。

### V1-004 — 生成 advisory 依赖波次计划，而非核心 scheduler

**优先级 / 规模 / 模式：** P2 / M / `automated`  
**依赖：** V1-002

**目的**

展示哪些镜头可并行、哪些等待父镜，但绝不自动批量提交。

**为什么**

ViMax 研究支持图感知并行；Manju 现阶段应先给人/IDE 计划，不改变 executor。

**先读/先审计**

- src/manju/build/visual_dependencies.py
- src/manju/build/director.py
- src/manju/build/graphdiag.py
- 现有 batch/spend gate

**候选修改文件**

- src/manju/build/visual_dependencies.py
- src/manju/cli.py 只读输出

**候选新文件**

- tests/test_visual_dependency_waves.py

**执行步骤**

1. 对 hard-edge DAG 做 deterministic topological layers；advisory 不改变波次。
2. 每波输出 ready/blocked、依赖证据、估价来源、建议现有命令。
3. 不调用 redo/build，不成为 resume/cache input。
4. 可输出 Director action 草稿但不 persist/confirm/run。
5. cycle 时 fail closed，不给伪计划。

**Focused tests**

- 同图波次稳定。
- 独立分支同波，hard child 后移。
- cycle 无计划。
- 删除输出零行为变化。

**验收标准**

- 人可据此安排生成。
- 核心 graph/executor 未改。

**必须产出的证据**

- proof scene waves snapshot

**回滚**

- 删除波次输出。

**立即停止条件**

- 实现试图自动确认或提交整波。

### V1-005 — 零成本视觉依赖 Proof Scene 演练

**优先级 / 规模 / 模式：** P1 / M / `manual_review`  
**依赖：** V1-003, V1-004

**目的**

在 3–5 个相互依赖镜头上验证 hard/advisory、陈旧失效、波次和人类决策。

**为什么**

图算法单测不能证明真实 refs、QC、选择协作。

**先读/先审计**

- 一组 proof_scene 镜头
- visual dependency explain/waves
- production status/qc/refs/selected takes

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/VISUAL_DEPENDENCY_PROOF_SCENE_<date>.md

**执行步骤**

1. 选择 3–5 镜的 synthetic/user-owned proof scene，包含同角色、同场景、continuity.prev 和 camera change。
2. 使用本地导入/FFmpeg 媒体完成父镜 current-bound END observation。
3. 观察子镜 blocked→ready；重选父镜后旧 anchor 立即 stale。
4. 确认 advisory shared-character edge 不硬阻塞。
5. 记录误报/漏报；不调用 VLM 或外部 provider。

**Focused tests**

- 演练前后 manju check/status/QC。

**验收标准**

- hard 依赖不因旧证据 fail open。
- 零外部请求。
- 图不会阻断无关镜头。

**必须产出的证据**

- 前后 graph snapshots
- media hashes
- human notes

**回滚**

- 关闭 opt-in gate；保留证据。

**立即停止条件**

- 父镜无 current-bound evidence。

## 阶段 K1：本地关键帧 Best-of-2 与视觉评审

**阶段目标：** 复用 image takes、Preview Ladder、QC v2 和人工选择，用本地合成/已有图片验证两个关键帧候选的 lineage、评审和下游绑定。

**退出门：** 两个零成本候选 append-only、hash-bound 评审；人工采纳的 exact hash 进入本地假视频请求身份；不宣称真实模型质量。

### K1-001 — 审计并固定现有 Preview Ladder 关键帧路径

**优先级 / 规模 / 模式：** P1 / M / `automated`  
**依赖：** V1-005

**目的**

证明 Best-of-2 应复用 image takes、keyframe adoption、promoted refs、QC 和 spend gate。

**为什么**

Manju 已有 preview-ladder/v1 和 exact-byte adoption，不应新增 CandidateSet/approval 表。

**先读/先审计**

- src/manju/qc/production.py Preview Ladder/keyframe
- image provider capability
- src/manju/providers/refs.py
- src/manju/core/container.py takes
- take sidecar/attempt evidence
- GUI/CLI keyframe view

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/KEYFRAME_OWNER_AUDIT_<date>.md

**执行步骤**

1. 确认 image 结果注册 take/sidecar 的 request/ref/provider/cost facts。
2. 确认 generation.candidates 或 adapter 多结果如何形成两个独立 candidate identity。
3. 确认 select/promote ref 和 hash 绑定现有命令。
4. 确认 QC packet 对 image take 支持；不足归属 agent_review。
5. 写 REUSE/EXTEND 清单。

**Focused tests**

- 运行 preview ladder/keyframe/provider candidate 现有测试。

**验收标准**

- 明确最小扩展点。
- 无新 approval store。

**必须产出的证据**

- keyframe owner audit

**回滚**

- 删除报告。

**立即停止条件**

- image take 无法 append-only 表达。

### K1-002 — 用本地/已有素材建立两个 append-only 关键帧候选

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** K1-001, P0-005

**目的**

在同一镜头意图和 refs 下，用 Pillow/FFmpeg 合成图或用户已有本地图片建立两个可追踪 image candidates。

**为什么**

本阶段验证 candidate、lineage、QC 和采纳机制，不验证云模型生成质量。

**先读/先审计**

- image provider route
- src/manju/build/graph.py/provider candidate handling
- src/manju/build/attempts.py
- src/manju/core/container.py
- src/manju/qc/production.py

**候选修改文件**

- 现有 image generation/candidate owner
- CLI/GUI keyframe 展示薄层

**候选新文件**

- tests/test_keyframe_best_of_two.py

**执行步骤**

1. 使用 generation.candidates: 2 的现有语义，或通过本地 import/fixture 形成 candidate_index 0/1；不新增第二 candidates 字段。
2. 每候选独立 media bytes/take/candidate_index/request digest/local profile digest/refs hash，cost=0。
3. 默认用确定性图：不同构图、角色占位或空间标记，便于人工/QC 区分；不下载模型权重。
4. 写入 append-only media 路径，禁止固定 first_frame.png 覆盖。
5. Preview Ladder 显示两个 image takes但不自动 adopted。

**Focused tests**

- 两个候选 hash/sidecar 独立。
- 重跑不覆盖。
- cost=0且无 transport。
- 部分失败证据保留。
- 旧项目行为不变。

**验收标准**

- 两个本地候选可独立查看/评审/保留。
- 无 file-exists/mtime cache。
- 不宣称 AI 质量。

**必须产出的证据**

- 两个 sidecars/attempt snapshots
- zero-cost dry-run

**回滚**

- 候选数退回 1；历史媒体保留。

**立即停止条件**

- 实现要求远程图片 API或自动大模型下载。

### K1-003 — 用现有 QC v2 做本地 hash-bound 关键帧评审

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** K1-002

**目的**

外部视觉模型只报告观察和证据，Manju 保存当前字节绑定，人类保留选择权。

**为什么**

应借鉴 ViMax VLM 评估而拒绝黑箱 BestImageSelector 自动决策。

**先读/先审计**

- src/manju/qc/agent_review.py
- src/manju/qc/assurance.py
- src/manju/qc/production.py candidate/keyframe
- qc brief/verdict CLI
- skills/visual-qc-review/SKILL.md

**候选修改文件**

- src/manju/qc/agent_review.py（条件）
- src/manju/qc/production.py 派生比较
- skills/visual-qc-review/SKILL.md
- CONTRACTS.yaml（仅必要）

**候选新文件**

- tests/test_keyframe_qc_binding.py

**执行步骤**

1. 优先复用 manju.qc.packet/v2 和 verdict/v2。
2. 评审可由人工填写或确定性 fixture 产生；本轮不调用云 VLM。
3. 维度仍是 observations：identity/appearance/location/prop/composition/screen_intent/style/technical_artifact。
4. evidence 绑定候选 media path/hash；换字节后旧记录 historical/stale。
5. comparison view 展示差异/uncertain/blocker，不自动排序或选择。

**Focused tests**

- 媒体变化=>stale。
- malformed/unsafe=>zero writes。
- 两个 reviewer 冲突=>disagreement。
- KEEP 不改变 selected_take。

**验收标准**

- 评审可追溯 exact bytes。
- 无需外部模型。
- 人类能看到差异。

**必须产出的证据**

- 两个 QC packets/verdicts
- comparison JSON

**回滚**

- 停止生成新 verdict；历史可读。

**立即停止条件**

- 流程要求模型自动选择才能继续。

### K1-004 — 人工采纳关键帧并绑定本地视频请求身份

**优先级 / 规模 / 模式：** P1 / M / `automated`  
**依赖：** K1-003, V1-002

**目的**

把人类采纳的 exact image bytes 通过现有 image take/ref 进入 localhost fake video request identity。

**为什么**

关键帧只有真正约束下游付费请求才有价值。

**先读/先审计**

- src/manju/qc/production.py adoption/gate
- src/manju/providers/refs.py
- src/manju/providers/submission.py identity
- select/refs/prompt/build dry-run

**候选修改文件**

- 仅当 identity 未绑定 ref bytes 时修对应 owner
- GUI/CLI adoption/binding digest 展示

**候选新文件**

- tests/test_keyframe_downstream_binding.py

**执行步骤**

1. 人类选择一张 image take或提升为 first-frame/canonical ref；动作走现有命令/truth patch。
2. 本地 dry-run 显示 adopted take、media_sha、ref role、request digest。
3. localhost fake video submission identity 包含 exact ref bytes hash；改选产生新 identity。
4. strict zero-cost policy 同时证明同一请求无法被改成外部 endpoint。
5. 图片 selected_take 不让 FINAL_VIDEO rung 成立。

**Focused tests**

- 采纳前后 gate。
- 重选 request digest 改变。
- ref bytes变化=>stale。
- 图片不等于 final video。
- non-loopback transport=0。

**验收标准**

- 本地请求可证明使用哪张图。
- 采纳是人类 truth change。
- 零费用。

**必须产出的证据**

- adoption before/after JSON
- identity diff

**回滚**

- 人类重选/移除 ref；候选历史保留。

**立即停止条件**

- identity 未包含实际参考字节。

### K1-005 — 零成本 Best-of-2 工作流 Proof（不评估云模型质量）

**优先级 / 规模 / 模式：** P1 / M / `manual_review`  
**依赖：** K1-004

**目的**

用三个本地/已有素材镜头验证 Best-of-2 的候选管理、评审、采纳、身份变化和用户操作成本。

**为什么**

零预算下可以验证系统设计，但不能决定真实模型是否因 Best-of-2 提升质量。

**先读/先审计**

- 三个 local proof shots：单主体/双主体/复杂场景占位
- QC v2
- Preview Ladder
- localhost fake video provider

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/KEYFRAME_BEST_OF_TWO_PROOF_<date>.md

**执行步骤**

1. 固定 truth/local profile；每镜建立两张本地候选和一条 fake video result。
2. 记录人工完成比较所需步骤、候选 hash、QC observation、采纳和 request identity 变化。
3. 不调用任何远程图片/视频/VLM 服务。
4. 结论只针对 workflow：是否易懂、是否保持 lineage、是否能防止自动选择。
5. 真实质量策略保持 experimental/off，等待未来独立预算计划。

**Focused tests**

- provider/task/QC/check focused tests。
- zero egress sentinel。

**验收标准**

- 候选工作流有 Manju 自身零成本证据。
- 没有质量夸大。
- 自动选择仍无权威。

**必须产出的证据**

- media hashes
- workflow step count
- human notes
- zero-network proof

**回滚**

- 保持 feature opt-in；本地媒体可保留。

**立即停止条件**

- 任何步骤要求远程 VLM/图像/视频 API。

## 阶段 S1：本地空间锚点与引用链

**阶段目标：** 用 FFmpeg 抽帧和已有媒体将 camera-parent/transition-anchor 思想落为 reference-only、可追溯、会过期的 refs。

**退出门：** 父镜变化使锚点过期；子镜本地请求绑定 exact anchor hash；没有云生成或账单。

### S1-001 — 定义并生成本地 reference-only 空间锚点

**优先级 / 规模 / 模式：** P2 / L / `automated`  
**依赖：** K1-004, V1-002

**目的**

从父镜当前本地媒体用 FFmpeg 抽取帧/裁剪视图，保存为 append-only reference artifact。

**为什么**

先验证 lineage、stale 和 refs 语义；不需要云 transition-video 生成。

**先读/先审计**

- src/manju/providers/refs.py
- src/manju/media/frames.py
- media/ref path rules
- external authoring handoff/reference graph
- take/ref sidecar
- continuation current evidence

**候选修改文件**

- 现有 refs/ingest owner
- CONTRACTS.yaml（条件）

**候选新文件**

- src/manju/media/spatial_anchor.py（条件）
- tests/test_spatial_anchor_artifact.py

**执行步骤**

1. 优先用现有 frame extraction + manual ingest + refs transfer；足够则不新增 schema。
2. 输入绑定父镜 exact media_sha、帧/时间、目标 shot/camera intent和本地 extraction profile。
3. 用 FFmpeg 抽帧、crop/scale 等确定性操作生成 anchor；禁止调用生成模型。
4. 输出写现有 append-only refs 路径，content-addressed/不覆盖。
5. sidecar 标记 reference_only、source shot/take/hash、target shot、frame/time、method/profile/output hash。

**Focused tests**

- 相同输入 identity稳定。
- 父媒体变化=>stale。
- path/symlink/overwrite拒绝。
- anchor 不进入 final eligibility。
- 无公网。

**验收标准**

- anchor 可追溯父镜真实字节。
- 纯本地、零费用、reference-only。

**必须产出的证据**

- anchor sidecar
- stale test

**回滚**

- 解除绑定；artifact 依 append-only/GC 规则保留。

**立即停止条件**

- 只能固定文件名覆盖。

### S1-002 — 将当前空间锚点提升为子镜现有 refs binding

**优先级 / 规模 / 模式：** P2 / M / `automated`  
**依赖：** S1-001

**目的**

通过 provider-neutral ref transfer contract 让子镜真实使用锚点。

**为什么**

单独生成图片无价值，必须进入现有控制 owner 与 submission identity。

**先读/先审计**

- src/manju/providers/refs.py vocabulary/controls/ignore
- Shot generation.params.refs
- src/manju/providers/submission.py
- src/manju/core/intent.py

**候选修改文件**

- 仅在 vocabulary 无法表达 spatial/background/framing 时 additive 扩展
- skills/hierarchical-director/SKILL.md

**候选新文件**

- tests/test_spatial_anchor_ref_binding.py

**执行步骤**

1. 优先现有 controls 表达 background/framing，排除错误 identity transfer。
2. 通过 truth_patch_set 提议子镜 refs，等待人类确认。
3. preflight 显示 anchor/controls/must_not_transfer/ignore/exact hash。
4. anchor stale 时 transport 前拒绝或 advisory 警告。
5. 长链定期回到 canonical character/location refs re-anchor。

**Focused tests**

- binding 后 identity 含 anchor hash。
- controls/ignore 进入 payload。
- stale 不复用旧 identity。

**验收标准**

- 子镜可证明使用锚点。
- 不修改 Bible 身份。

**必须产出的证据**

- truth patch
- request identity diff

**回滚**

- 新 truth patch 移除 binding；历史保留。

**立即停止条件**

- 只能把锚点塞 prompt override 且无 lineage。

### S1-003 — 零成本空间锚点 Proof：本地同场景机位变化

**优先级 / 规模 / 模式：** P2 / M / `manual_review`  
**依赖：** S1-002

**目的**

用 synthetic/user-owned 两镜场景验证 anchor lineage、refs binding、stale 和人工理解，不评估云模型空间保持质量。

**为什么**

在没有预算时，能诚实验证的是系统契约和操作路径，而非生成模型性能。

**先读/先审计**

- 含明确空间标记的本地两镜 proof scene
- QC spatial/composition observations
- strict zero-cost policy

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/SPATIAL_ANCHOR_PROOF_<date>.md

**执行步骤**

1. 父镜 current-bound accepted 且有 END observation。
2. 从父镜本地抽取 anchor，绑定到子镜 localhost fake request。
3. 评审 lineage、位置标记、identity pollution 风险和 stale 行为。
4. 重选/修改父镜，确认旧 anchor失效。
5. 功能保持 opt-in；真实质量等待未来独立预算计划。

**Focused tests**

- 媒体/QC/hash 完整。
- zero external transport。

**验收标准**

- 契约和 stale 行为有证据。
- 不声称真实模型增益。

**必须产出的证据**

- anchor/local media hashes
- QC/lineage comparison
- zero-network proof

**回滚**

- 保持 opt-in。

**立即停止条件**

- 任何步骤试图调用 transition/video model。

## 阶段 F1：OpenChatCut 本地精剪侧车

**阶段目标：** Picture Lock 后经中立 handoff 进入本地 OpenChatCut；仅使用非 AI、本地编辑能力，经双重人工批准返回。

**退出门：** 导出—本地精剪—零写入分析—人工采用闭环完成；OpenChatCut 未调用外部 AI/API。

### F1-001 — OpenChatCut 本地能力、协议、网络与许可证探针

**优先级 / 规模 / 模式：** P1 / M / `automated`  
**依赖：** S1-003, G0-002

**目的**

记录当前 OpenChatCut 实际 MCP tools、项目导入/导出格式、session 限制，并证明本轮只使用 localhost 和非 AI 编辑能力。

**为什么**

项目活跃变化；README 明确 draft session 不提供 generation/export/delete 等即时副作用，不能凭想象自动化。

**先读/先审计**

- OpenChatCut exact commit/README/LICENSE/package.json
- .mcp.json
- external MCP tool registry/session/revision/store
- project import/export/FCPXML/complete project data

**候选修改文件**

- 无。

**候选新文件**

- docs/integrations/openchatcut/CAPABILITY_SNAPSHOT_<commit>.yaml
- docs/integrations/openchatcut/README.md
- scripts/probes/openchatcut_capabilities.py
- tests/test_openchatcut_probe.py

**执行步骤**

1. 记录 exact commit/版本、工具 schema、draft-safe、side-effect、导入/导出方向。
2. 只连接 localhost；Bearer token可用环境变量，绝不写报告。
3. 检查并禁用/不配置所有外部 AI provider、转录、生成或搜索功能。
4. 验证 begin/read/draft edit/review/status/stale/browser takeover。
5. OpenChatCut 未安装时输出 structured unavailable；可继续完成 handoff contract，bridge/task保持 optional。

**Focused tests**

- 未运行=>structured unavailable。
- token/key 不出现在报告。
- 不执行生成/删除/外部 AI。
- 非 localhost URL拒绝。

**验收标准**

- 后续只用实测本地能力。
- 无云 Key、无外部 AI 请求。

**必须产出的证据**

- capability snapshot
- probe output

**回滚**

- 删除 probe/docs；核心零依赖。

**立即停止条件**

- endpoint 非 localhost 或网络不可信。

### F1-002 — 建立中立、不可变、内容寻址的 finishing handoff

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** F1-001

**目的**

把 Picture Lock 的 clips、timeline、audio、captions、constraints 交给任意外部精剪器。

**为什么**

Manju 已有 verified external handoff 和 OTIO/FCPXML/XMEML，应复用安全打包器而不是建立 OpenChatCut 专属真相。

**先读/先审计**

- 现有 manju handoff profile/writer/verifier/ingest
- external authoring handoff v2
- src/manju/exporters/otio.py/fcpxml.py/xmeml.py
- roundtrip/import-plan owners
- Picture Lock/readiness

**候选修改文件**

- 现有 handoff/profile owner 或单一 finishing owner
- src/manju/cli.py 薄层
- CONTRACTS.yaml

**候选新文件**

- tests/test_finishing_handoff.py
- docs/integrations/openchatcut/FINISHING_HANDOFF.md

**执行步骤**

1. 优先新增 finishing_portable profile；语义不兼容才定义 finishing-handoff/v1，并复用同一 archive/inventory/path guard。
2. 默认要求 current Picture Lock；rough-cut 显式 non_authoritative，不能回写锁片。
3. manifest 含 project identity、lock/timeline digest、rational edit rate、clip media hashes/in-out、audio/caption hashes、exporter versions、constraints、inventory。
4. 打包实际支持且通过 conform-loss 的 OTIO/FCPXML/XMEML，并带 neutral timeline JSON。
5. directory-atomic、regular-file-only、relative paths、exact inventory/checksum。

**Focused tests**

- 无 Picture Lock 默认拒绝。
- tamper/missing/extra/symlink/traversal/casefold 拒绝。
- 删除 bundle 不影响 build。
- 相同输入 identity 稳定。
- conform loss 明确。

**验收标准**

- bundle 与 OpenChatCut 解耦。
- 外部编辑器不能改 truth。

**必须产出的证据**

- verified sample bundle
- conform-loss report

**回滚**

- 删除 profile/schema；现有 exporters 不受影响。

**立即停止条件**

- 外部 project file 被当 canonical timeline。

### F1-003 — 实现可选 localhost-only OpenChatCut sidecar bridge

**优先级 / 规模 / 模式：** P2 / L / `optional`  
**依赖：** F1-001, F1-002

**目的**

让 IDE 从 verified finishing bundle 驱动 OpenChatCut draft-safe MCP，同时保持进程和许可证隔离。

**为什么**

OpenChatCut MCP 适合外部控制；Manju 自身 MCP 已移除，不能倒退。

**先读/先审计**

- F1-001 capability snapshot
- Streamable HTTP MCP
- Manju JSON CLI

**候选修改文件**

- pyproject.toml 仅 optional dev extra（若需要）

**候选新文件**

- tools/openchatcut_bridge/README.md
- tools/openchatcut_bridge/bridge.py 或 bridge.ts
- tools/openchatcut_bridge/tests/*

**执行步骤**

1. bridge 只读 verified bundle和 capability snapshot；不复制 OpenChatCut 源码。
2. 只允许 loopback Streamable HTTP MCP；host 不是 localhost/127.0.0.1/::1 时拒绝。
3. 实现 health/capability、project target、begin/read/draft edit/review/poll/discard。
4. 默认 approvalMode=manual；stale/browser takeover/revision mismatch fail closed。
5. v1 不做 AI generation/transcription/search、delete、credential management 或自动 export。
6. OpenChatCut 未安装时 Manju 全部核心测试仍绿。

**Focused tests**

- mock happy/stale/rejected/takeover/unavailable。
- non-loopback拒绝。
- token redaction。
- 无第三方时核心全绿。

**验收标准**

- optional sidecar、localhost-only、无云 AI。
- 没有 Manju MCP server。

**必须产出的证据**

- mock transcript
- license boundary test

**回滚**

- 删除 tools bridge。

**立即停止条件**

- 需要复制 AGPL 实现。
- 必须让 Manju 持有 OpenChatCut truth。

### F1-004 — 完成一次 OpenChatCut 本地人工精剪与零写入 roundtrip 分析

**优先级 / 规模 / 模式：** P1 / M / `manual_review`  
**依赖：** F1-002, F1-003

**目的**

证明 Picture Lock 后能进行 trim/transition/caption/audio 精修，返回后仍由 Manju 分析和人类采用。

**为什么**

OpenChatCut 的价值是最后 10% finishing，不是替代 Manju 制作系统。

**先读/先审计**

- 已 Picture Lock 短场景
- verified finishing bundle
- OpenChatCut MCP/editor
- 现有 FCPXML/OTIO/roundtrip CLI

**候选修改文件**

- 无。

**候选新文件**

- REPORTS/OPENCHATCUT_FINISHING_PROOF_<date>.md

**执行步骤**

1. 导出 verified bundle；按 capability snapshot 自动或人工导入本地 OpenChatCut。
2. 确认所有 AI provider/转录/生成配置为空；manual edit session 只做 trim 与 transition/caption/audio 等本地编辑。
3. 用户在编辑器内预览批准。
4. 导出实测支持的 project data/exchange file/review render，记录 hash/version。
5. Manju 运行 zero-write roundtrip/import-plan；不得直接 apply。
6. 第二次人审后才采用可表达变化；其余 finishing-only。

**Focused tests**

- source digest匹配。
- 分析默认 zero writes。
- stale Lock拒绝。
- unsupported effects列 loss。
- 无外部网络请求。

**验收标准**

- 双方各自一份真相。
- 两次人工批准。
- 本地精剪零服务费用。

**必须产出的证据**

- bundle/result hashes
- editor version/session
- roundtrip plan
- approvals

**回滚**

- 不采用 plan；外部 artifact 保留。

**立即停止条件**

- OpenChatCut source 与 handoff digest 不一致。
- 要求覆盖 Manju 媒体。

### F1-005 — 登记 append-only finishing result 与安全加固

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** F1-004

**目的**

将外部 project export/render 作为 lineage 完整的交付 artifact 登记，默认不参与 canonical build。

**为什么**

外部结果要可审计，也要防恶意/损坏包和隐式 auto-apply。

**先读/先审计**

- existing handoff ingest/bundle/path security
- exports/package/delivery manifest owners
- roundtrip import plans

**候选修改文件**

- 现有 finishing/handoff owner
- src/manju/build/delivery.py（若 owner 合适）
- CONTRACTS.yaml

**候选新文件**

- tests/test_finishing_result_security.py

**执行步骤**

1. 定义/复用 finishing-result/v1：source handoff digest、editor/version、project/render hashes、change summary、losses、human approval。
2. 复制到 append-only exports/finishing/<id> 或现有 owner 路径，禁止覆盖 final.mp4。
3. 默认只登记；可表达变化仍走 roundtrip plan。
4. 加入 zip slip/symlink/absolute/casefold/decompression bomb/extra inventory/JSON injection/oversize tests。
5. delivery manifest 可引用 artifact，但 source cuts 仍由 truth 决定。

**Focused tests**

- tamper/mismatch 拒绝。
- 重复相同 bytes idempotent，不同 bytes 不覆盖。
- 删除 result 不改变 canonical build。

**验收标准**

- lineage 完整。
- 无 hidden auto-apply。

**必须产出的证据**

- result manifest
- security matrix

**回滚**

- 移除登记；truth 不变。

**立即停止条件**

- result 成为下次 build 隐式输入。

## 阶段 W1：Toonflow 风格零成本本地工作台

**阶段目标：** 在现有 localhost GUI 实现派生图视图、三角色交互与零成本 provider 状态，不引入 SQLite 真相、动态 TS Provider 或云 Key。

**退出门：** 删除 UI 状态/reports/runtime DB 不改变 build；所有修改只生成现有 Director Proposal；外部 provider 显示为禁用。

### W1-001 — 派生工作台图模型与现有 GUI API

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** V1-002, K1-004, F1-002

**目的**

在一个图上连接 source、scene、shot、refs、candidates、QC、selection、Lock 和 finishing。

**为什么**

采用 Toonflow 的可视化价值，但所有节点必须来自现有 truth/evidence。

**先读/先审计**

- src/manju/gui/server.py
- src/manju/gui/state.py
- src/manju/gui/page.py/pages.py
- 现有 /api/state/schema/meta
- 所有节点 owner

**候选修改文件**

- src/manju/gui/server.py 薄路由
- CONTRACTS.yaml（若公共 schema）

**候选新文件**

- src/manju/gui/workbench_graph.py
- tests/test_workbench_graph.py

**执行步骤**

1. 定义 workbench-graph/v1 或内部稳定 shape；公共则登记。
2. 节点：source_span/event-or-change/scene/shot/ref/image take/video take/qc/selection/lock/finishing。
3. 边：cites/contains/continues/depends_on/references/generated_from/reviewed_by/selected_as/locked_in/finished_as。
4. 每边带 source path/hash/evidence；推测不入图。
5. 每次从当前 truth/evidence 派生，不读布局。
6. 大项目支持 filter/pagination/scene subgraph，不返回媒体正文。

**Focused tests**

- deterministic。
- 删除 projection 后 build 不变。
- 恶意文本安全编码。
- 保留 localhost/Host/CSRF。
- 大 fixture 性能。

**验收标准**

- 图是导航/解释层。
- 无 secret/私密绝对路径。

**必须产出的证据**

- graph snapshot
- performance facts

**回滚**

- 删除 endpoint/module。

**立即停止条件**

- 要求新增 SQLite canonical tables。

### W1-002 — 在现有 localhost GUI 实现只读图形工作台

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** W1-001

**目的**

提供无限画布式浏览、过滤、聚焦、详情和跳转，不引入 Electron/React/Vue。

**为什么**

Manju GUI 已是安全薄客户端；clean-room 增量比移植 Toonflow 前端更稳。

**先读/先审计**

- src/manju/gui/page.py
- src/manju/gui/pages.py
- 现有 JS/CSS
- reduced-motion/XSS/project identity/stale-tab protections

**候选修改文件**

- src/manju/gui/page.py
- src/manju/gui/pages.py
- 现有静态资源 owner

**候选新文件**

- tests/test_workbench_gui.py

**执行步骤**

1. 新增 Workbench 页，v1 只读；用原生 SVG/Canvas/HTML。
2. 支持 scene/shot/status/edge filter、search、focus、details、跳现有 review/edit/qc。
3. layout/zoom/fold 只存 localStorage 或 .manju/ui，key 绑定 project identity。
4. 删除 UI state 后重建默认布局且不改 graph/build digest。
5. 节点显示 exact source/hash/current-or-stale/next existing command。
6. 支持 reduced motion、键盘、ARIA、转义和列表降级。

**Focused tests**

- XSS 不执行。
- project switch/stale tab 409。
- 删除布局 build key 不变。
- 无 JS 有列表。

**验收标准**

- 一页理解故事到交付。
- 页面零直接写入。
- 无 Toonflow 代码/品牌。

**必须产出的证据**

- GUI screenshots
- a11y/security tests

**回滚**

- 移除页面/资源。

**立即停止条件**

- 需要替换 GUI 技术栈。

### W1-003 — 实现三角色、proposal-only 工作台交互

**优先级 / 规模 / 模式：** P1 / L / `automated`  
**依赖：** W1-002, D1-004

**目的**

将 Toonflow 决策/执行/监督映射为 Director Proposal、现有 engine commands 和 QC evidence。

**为什么**

增强体验但不能绕过 human confirm、spend gate 和唯一真相。

**先读/先审计**

- src/manju/build/director.py
- GUI proposals/review/jobs API
- QC brief/verdict API
- operation semantics/job registry

**候选修改文件**

- src/manju/gui/server.py
- src/manju/gui/pages.py/page.py
- 薄 API adapters

**候选新文件**

- tests/test_workbench_proposal_interactions.py

**执行步骤**

1. Director 只生成/import truth_patch_set proposal。
2. Executor 只调用现有 check/build/redo/export，paid 仍过 human/spend gate。
3. Reviewer 只创建 packet/接 verdict/显示 assurance，不 select/Lock。
4. 拖拽 v1 只生成 proposal draft。
5. 所有返回用现有 OperationOutcome/structured code。

**Focused tests**

- AI actor 不能 confirm paid/truth。
- KEEP 不 select。
- drag 只 proposal。
- stale identity 零写入。

**验收标准**

- 三层边界在 UI/API 可验证。
- mutation 走 shared core。

**必须产出的证据**

- interaction matrix
- proposal flow

**回滚**

- 移除交互，保留只读页。

**立即停止条件**

- GUI 维护独立业务状态。

### W1-004 — 声明式 Provider 配置与零成本状态 UI

**优先级 / 规模 / 模式：** P2 / L / `automated`  
**依赖：** W1-003, P0-005

**目的**

借鉴 Toonflow 配置体验，但在本轮只展示本地/fake provider；外部 provider 明确禁用，不接受云 Key。

**为什么**

零成本版的 UI 首要责任是防止用户误以为 free tier 可以安全执行，而不是让配置更容易触发云调用。

**先读/先审计**

- src/manju/providers/manifest.py/catalog.py/preflight.py
- providers add/show/check/catalog CLI
- GUI settings patterns
- secret owner

**候选修改文件**

- src/manju/gui/server.py/pages.py/page.py
- 现有 provider manifest editor owner（若有）

**候选新文件**

- tests/test_provider_config_gui.py

**执行步骤**

1. 从 ProviderManifest schema 生成只读/编辑字段；未知 additive 字段 roundtrip 保留。
2. strict zero-cost active 时隐藏或禁用 secret 输入，显示“外部 provider 禁用；需未来独立预算计划”。
3. localhost fake provider 可展示 diff/profile digest/capabilities/limits/cost=0/request preview。
4. 外部 URL 不做连通性探测；保存含非 loopback endpoint 的 profile时标记 disabled，不能执行。
5. 禁止脚本/eval/VM；profile变化不篡改历史 execution profile。

**Focused tests**

- secret输入/值不进 HTML/API/log。
- 非 loopback profile不可执行。
- 恶意 script/URL不执行。
- 历史任务绑定旧 digest。

**验收标准**

- 普通用户能看懂零成本状态。
- 无动态 TS runtime。
- UI不能解除零成本硬门。

**必须产出的证据**

- GUI disabled-state/preflight snapshots
- redaction/egress tests

**回滚**

- 移除 UI editor；CLI 仍可用。

**立即停止条件**

- UI提供“忽略零成本限制并继续”按钮。

### W1-005 — 证明工作台与 UI 状态可删除、无隐藏真相

**优先级 / 规模 / 模式：** P1 / M / `automated`  
**依赖：** W1-004

**目的**

系统验证画布、layout、reports 和 runtime DB 不参与 build/request/selection。

**为什么**

这是 Toonflow 风格工作台能否与 Manju 共存的核心验收。

**先读/先审计**

- .manju runtime dirs
- localStorage keys
- reports projections
- GUI state
- build/spec/cache key owners

**候选修改文件**

- 无。

**候选新文件**

- tests/test_workbench_no_hidden_truth.py

**执行步骤**

1. 记录 fixture 的 check/build dry-run/spec/timeline/request digests。
2. 删除 .manju/ui、workbench reports、布局和可重建 SQLite，再开 GUI。
3. 比较 truth/build plan/identity/selection/Lock/exports。
4. 移动节点/折叠布局，确认无 events/truth writes。
5. 加入 Windows/Ubuntu 矩阵。

**Focused tests**

- 上述 deletion/rebuild 跨平台测试。

**验收标准**

- 核心输出一致，仅布局重置。

**必须产出的证据**

- before/after digest comparison

**回滚**

- 移除产生 hidden truth 的 UI feature。

**立即停止条件**

- 发现 UI state 参与 build/cache/provider。

## 阶段 H1：零成本故障注入、跨平台与发布判定

**阶段目标：** 证明网络硬禁用、崩溃、陈旧输入、恶意包、第三方不可用和 Windows 差异下仍 fail closed。

**退出门：** 同一 commit Ubuntu/Windows 双绿；测试证明无外部模型调用、无凭据、无隐藏真相；发布声明明确真实模型质量未验证。

### H1-001 — 执行零成本跨模块故障、安全与不变量矩阵

**优先级 / 规模 / 模式：** P0 / L / `automated`  
**依赖：** P0-005, D1-005, V1-005, K1-005, S1-003, F1-005, W1-005

**目的**

把网络出口、导演、依赖、关键帧、外部编辑和 GUI 的组合失效放入统一回归门。

**为什么**

最大风险在边界组合，不在单模块 happy path。

**先读/先审计**

- 所有新增 tests
- tests/CONVENTIONS.md
- 现有 path/archive/locking/security fixtures

**候选修改文件**

- 无。

**候选新文件**

- tests/test_three_project_integration_invariants.py
- REPORTS/THREE_PROJECTS_FAULT_MATRIX_<date>.md

**执行步骤**

1. provider：非 loopback egress、ambiguous local submit、crash、duplicate、bad media。
2. director：stale/tamper/locks/mixed provider action/CAS。
3. visual/keyframe：cycle/missing/stale/disagreement/no auto-select。
4. finishing：OpenChatCut unavailable/stale/version/tamper/zip slip/source drift/non-loopback MCP。
5. GUI：XSS/CSRF/Host/stale tab/hidden truth/large graph/disabled external provider。
6. environment：云 key presence检测、secret redaction、no public-network sentinel。
7. license：核心 imports/dependencies/source 无上游混入。

**Focused tests**

- compileall
- CI static checks
- integration invariants
- full pytest at this release gate only

**验收标准**

- 所有危险场景 fail closed或人工恢复。
- 零公网模型请求、零凭据、零账单。
- 无 derived state成为 truth。

**必须产出的证据**

- zero-cost fault matrix
- full test output
- network sentinel results

**回滚**

- 按独立 feature commit 回退，不降低既有 gate。

**立即停止条件**

- 为过测试而弱化 Windows/spend/path/security gate。

### H1-002 — 同一 commit 的 Ubuntu 与 Windows 全量发布门

**优先级 / 规模 / 模式：** P0 / M / `manual_ci`  
**依赖：** H1-001

**目的**

在项目首要平台上证明同一字节集通过现有硬门。

**为什么**

Windows 是 Manju 明确的硬 release gate；不能用本地或不同 SHA 代替。

**先读/先审计**

- .github/workflows/ci.yml
- .github/workflows/windows-ci.yml
- .github/workflows/xplat.yml
- REPORTS/LAST_GREEN.yaml

**候选修改文件**

- 仅修真实跨平台 bug；不得弱化 workflow。

**候选新文件**

- REPORTS/THREE_PROJECTS_XPLAT_GATE_<date>.md

**执行步骤**

1. 干净 worktree 运行 compile/static/full tests。
2. 推送单一候选 commit，运行 Ubuntu full 和 Windows hard gate。
3. optional OpenChatCut bridge 在未安装时 mock/skip，不联网。
4. 核对 FFmpeg、case/path、locks、subprocess、archive。
5. 只有同一 SHA 双绿才更新 LAST_GREEN/STATE；失败修新 commit 重跑。

**Focused tests**

- 现有 CI 全量，不删减。

**验收标准**

- Ubuntu/Windows 同一 commit 双绿。
- test/skip counts 诚实。

**必须产出的证据**

- CI run ids
- commit SHA
- counts

**回滚**

- 回退问题 commit；不伪造 LAST_GREEN。

**立即停止条件**

- workflow 被 allowed_failure/临时弱化。

### H1-003 — 零成本版发布判定、文档与未来预算边界

**优先级 / 规模 / 模式：** P0 / M / `manual_review`  
**依赖：** H1-002

**目的**

对每项能力给出 SHIP/OPT-IN/DEFER/REJECT，明确哪些只验证了架构，哪些因零预算没有真实模型证据。

**为什么**

三个上游快速演化，必须保留可撤销边界和重新审计触发条件。

**先读/先审计**

- 所有 REPORTS
- STATE.md
- DECISIONS.md
- CONTRACTS.yaml
- docs/SOURCE_LEDGER.yaml
- 真实费用/UX evidence

**候选修改文件**

- STATE.md
- DECISIONS.md
- docs/SOURCE_LEDGER.yaml
- README.md/CLI/GUI/Skills docs

**候选新文件**

- REPORTS/THREE_PROJECTS_FINAL_DECISION_<date>.md

**执行步骤**

1. 逐能力判定：local proof、hierarchical director、visual graph/gate、local Best-of-2、local spatial anchor、OpenChatCut、Workbench。
2. 真实 Provider、真实模型质量、真实计费、云 OCR/ASR/VLM 全部保持 DEFERRED/UNVERIFIED。
3. 记录上游 commit snapshots 和重新审计触发条件。
4. 更新零成本用户工作流：authoring→local candidates→local fake video→QC→Picture Lock→local finishing。
5. 写清未来重新启用云能力必须生成新计划，不得改一个 flag 后直接执行旧任务。

**Focused tests**

- docs command smoke。
- CONTRACTS registry。
- sample project full check。

**验收标准**

- 状态声明与证据一致。
- 没有三方/云配置时默认路径完整。
- 所有新文件/行为有 owner。
- 不宣称 AI 视频生成质量已验证。

**必须产出的证据**

- final decision report
- docs command logs

**回滚**

- 按 feature commits 回退；追加更正历史。

**立即停止条件**

- H1-002 未同 SHA 双绿。

## 9. 暂停且禁止执行的未来付费轨道

- Real cloud Provider Proof Shot
- Paid keyframe Best-of-2 quality bake-off
- Cloud-generated spatial transition-anchor comparison
- Cloud OCR/ASR/VLM review
- Any AI feature inside OpenChatCut that needs a remote API

恢复这些工作必须同时满足：

- The user explicitly states a non-zero maximum budget in a future conversation.
- A separate plan is generated; do not silently reactivate tasks from the old paid edition.
- Provider/model/pricing/privacy are re-researched at that future date.

## 10. 完成定义

- 所有任务证据来自当前 HEAD。
- 零外部模型/API请求、零 API Key、零账单。
- localhost fake provider 的 submit/resume/download/hash/QC/select 闭环通过。
- ViMax 风格能力只产生现有 Director Proposal。
- 视觉图和 Workbench 均可删除且不改变 build。
- Best-of-2 与空间锚点只证明 workflow/lineage，不宣称真实模型质量。
- OpenChatCut 只做本地非 AI finishing，并有两次人工批准。
- 同一 commit 的 Ubuntu/Windows 双绿。
- STATE/DECISIONS 明确写着真实 Provider 和真实生成质量仍未验证。
