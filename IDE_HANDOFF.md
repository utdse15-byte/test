# Manju One 零成本功能实现交接

更新时间：2026-08-12
仓库：`G:\XXN\test`
当前分支：`codex/manju-zero-cost-g0`
已提交起点：`517b764c969dbbe6d9079114c449e4b056a3e81f`

## 1. 给接手 IDE 的授权

你的目标是把 Manju One 的零成本工作流做成真正可用、可测试、可恢复的功能，不是机械完成任务清单。

你可以并且应该独立判断：挑战旧设计、重排任务、合并或删除低价值步骤、复用现有能力、修正错误假设，并为更好的功能闭环调整实现。`docs/plans/manju_zero_cost_execution_tasks.yaml` 和长篇 Markdown 计划是需求地图、风险清单和历史思路，不是必须逐项照抄的命令。发现任务已由现有代码满足时，直接记录 `REUSE_ONLY` 或 `NOT_NEEDED`；发现计划妨碍正确实现时，以代码证据、测试和真实用户体验为准。

不要为了流程制造代码、报告、schema 或 commit。优先修复真正的问题。完成的标准是功能可实际使用、关键失败模式受控、测试能够证明行为，而不是任务 ID 全部被打勾。

## 2. 不可协商的边界

以下是用户预算、数据所有权和人工权限边界，不属于可自由修改的实现偏好：

- 总预算是 **0 元**。禁止任何可能产生账单的调用，包括免费额度、试用金、按量 API、云 OCR/ASR/VLM、云生成和外部搜索服务。
- 不请求、读取、打印、配置或试探任何云 API Key；不得以“只是检查能否调用”为理由接触真实 provider。
- 不自动下载大型模型权重。只可使用已有本地依赖、已有本地媒体、Python、FFmpeg/ffprobe、确定性合成素材及 localhost fake provider。
- provider、测试、dogfood 和 OpenChatCut bridge 发起 HTTP(S) 时，目标 host 只能精确为 `localhost`、`127.0.0.1` 或 `::1`；不得接受子域、局域网地址或其他回环地址作为等价物。
- 不得寻找替代的免费云服务。某功能若只能依赖 API Key、billing account、免费额度或试用金，保留清楚的 unavailable 状态，不要调用。
- 文本项目文件始终是唯一 canonical truth。report、graph、GUI state、SQLite、缓存、向量索引和 reviewer summary 都是可删除投影，不得成为 build/provider 的执行输入。
- 不恢复 Manju MCP；不复制 OpenChatCut/Toonflow 源码、资产或品牌；不执行 ViMax 的默认生成 pipeline。第三方只可按已记录的 clean-room 或进程边界使用。
- 模型、评审分数或自动规则不得替人选择 candidate、写入 `selected_take` 或完成 Picture Lock。
- truth confirmation、候选选择、OpenChatCut 内批准、finishing result 采用必须停在人工门前。CI 结论必须引用真实 run ID，不得伪造。
- 不弱化既有 spend、Windows、locking、path/archive、CSRF、content binding 和 provider identity gate。

如果某条功能要求突破上述边界，停止该功能并报告真实缺口。不要绕道到另一个付费或云端方案。

## 3. 当前真实状态

已提交：

- `ee254b9 G0-001: record zero-cost baseline evidence`
- `517b764 G0-002: establish strict zero-cost third-party boundaries`

`G0-002` 已固定 ViMax、OpenChatCut、Toonflow 的来源、许可证摘要和 clean-room 边界。相关材料位于：

- `docs/architecture/ADR-THREE-PROJECT-INTEGRATION-ZERO-COST.md`
- `docs/architecture/THIRD_PARTY_BOUNDARIES.md`
- `docs/runbooks/ZERO_COST_MODE.md`
- `REPORTS/G0-002_THIRD_PARTY_BOUNDARY_2026-08-11.md`

工作树包含尚未提交的 `P0-001` 实现；当前 `HEAD` 仍是 `517b764`，接手时不得 reset、stash 或覆盖这些工作。主要内容：

- 新增 `src/manju/providers/zero_cost.py`，以 `MANJU_EXECUTION_MODE=strict_zero_cost` 激活机器可执行策略。
- generic video、ASR、TTS、ComfyUI、stock、Edge TTS、manifest/registry 入口接入 transport 前检查。
- strict 模式拒绝非 loopback URL、任何 credential reference 以及未审计的 `module:Class` adapter；旧模式保持既有行为。
- CLI/GUI provider 状态在 strict 模式下不读取 credential 环境变量。
- 新增 localhost fake video profile、离线 proof 脚本、runbook、测试和 `REPORTS/P0-001_LOCAL_PROOF.json`。

未提交文件以 `git status --short` 为准，当前涉及：

```text
src/manju/cli.py
src/manju/gui/pages.py
src/manju/providers/{asr,comfyui,edge_tts,generic_cloud,manifest,registry,stock,tts,zero_cost}.py
tests/test_zero_cost_provider_policy.py
scripts/dogfood/
docs/runbooks/ZERO_COST_LOCAL_PROOF_SHOT.md
REPORTS/P0-001_LOCAL_PROOF.json
```

2026-08-12 对该工作树做过本地聚焦验证：

```text
pytest P0/provider/contract matrix: 93 passed, 1 skipped, exit 0
python compileall: exit 0
ruff check: exit 0
git diff --check: exit 0
```

proof evidence 显示 `transport_count=0`、`network_calls=0`、模拟成本 `0.0`、4 秒、720p、单候选，preflight 为 `COMPATIBLE`，并明确 `arbitrary_subprocess=forbidden`。这证明离线配置和阻断测试，不代表真实云 provider 已验证。

当前 Windows shell 下原有 `tests/test_local_cmd.py` 的 7 个 fixture 使用 `sh`，因系统无 `sh` 而失败；这是独立的跨平台测试/fixture 修复项，不是 P0 egress 策略失败。不要用它替代或弱化 strict gate。

## 4. 接手后的第一判断

先审查并收口当前 `P0-001`，不要立刻展开整个 DAG：

1. 运行 `git status --short --branch`、`git rev-parse HEAD`，阅读当前 diff；保留所有现有工作。
2. 复跑 `tests/test_zero_cost_provider_policy.py` 及受影响 provider 测试，确认 credential reference 和自定义 adapter 在首次 transport/secret resolution 前被拒绝，并确认 redirect 目标在后续请求发出前重新经过 loopback 检查。
3. 保留 `local_cmd` 的信任边界：普通模式继续支持它，strict 模式必须拒绝。任意子进程可自行联网，而 Manju 没有操作系统级网络沙箱，不能把它算作已证明的零外连执行路径。
4. 审查通过后再提交当前实现，建议提交信息：`P0-001: enforce strict zero-cost provider egress`。
5. 修复 Windows `sh` fixture 的跨平台问题，优先复用 Python/`sys.executable`，并保持普通模式 local_cmd 行为不变。
6. 再用一个最短的端到端本地样片验证：prompt/check -> fake submit/resume -> local media/hash/QC -> 人工选择门。不要只验证 JSON 形状。

当前最值得优先完成的是 P0 功能闭环。其后再按用户可感知价值选择 D1/V1/K1/S1/F1/W1 中的工作，不必遵循旧文件的细粒度顺序。

## 5. FFmpeg 判断

不要把“固定 6.1.1”误解为遇到新版问题就要求用户降级。

6.1.1 是已有 CI 与历史证据的参考工具链，用来复现和比较；它不是逃避兼容性修复的理由。对 FFmpeg 7/8 或更高版本出现的可复现问题，应先定位参数、filter graph、codec、metadata 或 ffprobe 输出差异，优先写回归测试并修复兼容层。用户机器上的新版本不应被自动降级。

合理策略是：

- CI 保留一个已知参考版本，同时逐步增加新版矩阵或聚焦兼容测试。
- 只依赖项目实际需要且被支持的 FFmpeg 能力，避免按完整版本号硬编码行为。
- 无法跨版本稳定的字节输出，应比较语义事实而非假定二进制完全相同。
- 只有确认属于上游缺陷、无可靠 workaround 且会破坏正确性时，才把某版本标为已知不兼容，并给出证据和非破坏性的提示。

## 6. 实现原则与验收

- 从 owner 和真实调用链开始，优先修改核心 owner；CLI/GUI 只做薄 adapter。
- 对行为缺口先写能失败的聚焦测试，再做最小但完整的实现；不为追求“小 diff”留下已知旁路。
- focused tests 是日常反馈；修改共享执行核心、高风险安全边界或准备发布时，扩大到相关 integration/full suite。不要把“节省流程”变成漏测。
- 允许重构，但重构必须直接减少功能复杂度或消除真实重复，不做无关风格清扫。
- verbose evidence 放入 `REPORTS/`；报告不是 runtime input。对用户只给结论、失败原因、证据路径和仍需人工处理的事项。
- 任何“成功”声明必须区分：静态/schema 测试、localhost fake 验证、真实本地媒体端到端验证、真实跨平台 CI。不得互相替代。

最终验收至少包括：严格模式无凭据解析且非 loopback transport 在 I/O 前被拒绝；零成本本地样片能够恢复、落盘、校验、QC 并停在人工选择门；删除 reports/UI cache 不改变执行结果；Windows 与 Ubuntu 的受影响测试在同一 commit 上有真实证据；项目文档诚实说明已实现与尚未实现的能力。

## 7. 必读但按需加载

先读 `README.md`、`STATE.md`、`DECISIONS.md` 末尾相关条目、`CONTRACTS.yaml` 相关 schema、`CLAUDE.md`、`pyproject.toml`、`tests/CONVENTIONS.md` 和本文件。随后只按当前问题读取 focused ranges。

需求与历史方案索引：

- `docs/plans/manju_zero_cost_execution_tasks.yaml`
- `docs/plans/MANJU_THREE_PROJECTS_ZERO_COST_EXECUTION_PLAN.md`
- `docs/runbooks/MANJU_ZERO_COST_LOCAL_RUNBOOK.md`

接手 IDE 可以不同意这些文件中的实现选择，但不能悄悄改变用户的零预算、canonical truth 和人工决策权。若要改变重要产品方向，先把证据、取舍和建议讲清楚，再与用户讨论。
