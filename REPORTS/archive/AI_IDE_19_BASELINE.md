# AI_IDE_19 — 基线报告 (Baseline): 素材理解、语义剪辑、Cutdown 与 Smart Reframe

日期：2026-07-11 · 分支：`claude/cost-optimization-strategy-cjfmn5`
契约：`Manju_AI_IDE_19_...Compact_v2.md` · 编排裁定：`C19_ADDENDUM.md`
纪律：文本事实源 · 分析是派生证据(绝非剪辑事实) · 核心不调 LLM/VLM ·
LLM/VLM 不直接写 Timeline · fixture 分析先于云 · 未 commit/push ·
未触碰 DECISIONS.md / README.md / tests/fixtures/golden/

## WP0 现有能力审计（§3 逐项 —— 全部复用，不重建）

| §3 能力 | 既有 owner（复用点） | 事实 |
|---|---|---|
| media import/hash | `build/ingest.py` (`hash_file`)、`core/container.register_take`、`MANUAL_HASH` | 精确 sha256 内容寻址；take 追加式 |
| transcription/captions | `media/align.py`(`align_shot`)、`media/timing.py`(18 对齐证据 sidecar `<take>.align.json`：word/speaker/confidence)、`CaptionLine`、`exporters/srt_ass` | 词/说话人级证据已在 18 落地；rough-cut **消费它**，不建新转写库 |
| Timeline clips/source_in/duration | `timeline/compiler.py`(`VideoClip.source_in_ms`/`start_ms`/`duration_ms`)、`snap_to_frame_grid` | 帧栅格；选择归 `evaluate_all`/compiler |
| roundtrip/OTIO/JianYing | `build/roundtrip.py`(`plan_roundtrip`/`apply_roundtrip`)、`exporters/{otio,jianying,openclap(capcut),native_draft}` | 外部编辑回流为 reviewable source patch；不升级 NLE 文件为主真相 |
| cutdown/localization profiles | `build/delivery.py`(13C `manju.delivery-manifest/v1`：`variant_kind ∈ MASTER/FORMAT_ONLY/EDITORIAL_CUTDOWN/LOCALIZED/PLATFORM_PACKAGE`、`timeline_semantic_digest`、`check_format_only_invariant`、`cutdown_source.ref` 门、`external_framing` 采纳规则)、`core/locale.py` | 13C 只**打包既有 cut**；EDITORIAL_CUTDOWN 需显式 `cutdown_source.ref`（“approved proposal” 是合法引用类型） |
| FFmpeg crop/scale/overlay | `media/repair_ops.py`(`crop_pad_take`：center_crop/pad_blur、`set_inout_take`、`retime`/`trim`/`extend`)、`media/render.py`(drawtext overlay/scale/pad)、`media/ffmpeg.py`(`run_ffmpeg`/`atomic_output`) | repair 自成小 filtergraph 词表，**不碰 render.py**（reframe 沿此先例） |
| board/preview | `board/board.py`、`media/preview.py`、`qc/production.ladder_view` | 派生视图 |

**编排/证据既有骨架（复用点）：**
- **派生报告存储**：`providers/qualification.py` 的 `reports/providers/qualification/<id>__<cap>.json`（deletable、非 build 输入）——analysis 报告镜像此形，落 `reports/analysis/`。
- **资质闸**：`qualification_state(provider_id, capability, *, evidence, declared)`（纯函数 8 级梯）+ `reviewer_admission_from_state`(`REVIEWER_NOT_QUALIFIED`)——analyzer 闸镜像之，`ANALYZER_NOT_QUALIFIED`。
- **能力 token**：`providers/manifest.capabilities: list[str]`（自由表，追加无 schema 改动；`LIP_SYNC_CAPABILITY`/`FIRST_LAST_CAPABILITY` 先例）。
- **提案信封**：`build/director.py`(`Proposal`/`ProposalAction`，`reports/proposals/<id>.yaml`；固定白名单 `ACTION_TYPES`；`confirm()` 独立；`_PRICED_TYPES=build/redo/voice`)。cutdown = **提案 payload**，非新 schema。
- **付费生成路径**：`providers/base.py`(`GenerationRequest`→`CloudProvider.generate`→`register_take` 追加式；`redo_of` lineage；`should_cancel`/预算/admission)。bridge 走**此标准路径**。
- **付费恢复演练**：14 `RECOVERY_PASSED`（删 SQLite→`runtime.state.rebuild`→poll-only resume，resubmit 0）。
- **当前绑定 review**：15 `qc/agent_review.py`(`manju.qc.verdict/v2`、`record_verdicts`、`compute_assurance`)。
- **CAS 写**：`core/writes.py`(`checked_shot_write`/`shot_text_hash`，`expected_text_hash`)。
- **对齐 STALE 先例**：`media/timing.py`(header `source_media_hash` + 读时比对 → STALE；`UNALIGNED` 绝不伪造均匀时间)。
- **16 的 bridge 定桩**：`test_c16_transitions` 钉 `generative_bridge` 转场此前是 **transport 0 / proposal-only**——本批**执行**它。

## 交付计划（生产文件 ≤10；红先行；§11 十六行 1:1）

| # | 文件 | WP | 性质 |
|---|---|---|---|
| 1 | `media/analysis.py` | WP1 派生证据文档 `manju.media-analysis/v1` + fixture 分析器 + ROI 提取 | 新增 |
| 2 | `build/segments.py` | WP2 coherent segments 派生 + WP3 cutdown 校验(零写/CAS/禁区) | 新增 |
| 3 | `media/reframe.py` | WP4 ROI→crop keyframes 编译 + FORMAT_ONLY 证明 + 可执行 crop | 新增 |
| 4 | `qc/roughcut.py` | WP5a speech rough cut（只标注、可逆、不改 Timeline） | 新增 |
| 5 | `build/bridge.py` | WP5b 生成式转场 bridge：标准付费路径执行 + never-in-final | 新增 |
| 6 | `build/toolmap.py` | WP6 白名单意图→既有 executor 派发（无 LLM planner） | 新增 |
| 7 | `providers/manifest.py` | media_analysis / generative_bridge 能力 token | 改（追加） |
| 8 | `providers/qualification.py` | `analyzer_admission`(`ANALYZER_NOT_QUALIFIED`) 镜像 reviewer 闸 | 改（追加） |
| 9 | `cli.py` | `manju analyze/segments/cutdown/reframe/rough-cut/bridge/tool` | 改（追加） |

WP7 NLE roundtrip：验证并定桩既有 `build/roundtrip.py` 的人工 trim/crop/split marker → proposal 路径（人工 split 优先、帧映射）；AAF/FCPXML **SKIPPED_WITH_EVIDENCE**（无真实编辑器 fixture）。目标不新增生产文件。

## §11 十六行 → 测试映射（红先行）

1 analysis 绑定 exact media · 2 同名替换 stale · 3 低置信度 UNKNOWN → `test_c19_analysis.py`
4 cut 不穿禁区 · 5 EDL proposal 零写入 · 6 apply CAS → `test_c19_segments.py`
7 crop keyframe 稳定/平滑 · 8 safe area · 9 blanking fallback · 10 format-only invariant → `test_c19_reframe.py`
14 speech rough-cut 可逆且不直接改 Timeline → `test_c19_roughcut.py`
15 bridge 输入/输出 hash+付费恢复+review · 16 bridge 未批准不进 final → `test_c19_bridge.py`
11 人工 split marker 优先 · 12 LLM/VLM 不直接写 Timeline · 13 NLE frame mapping → `test_c19_orchestration.py`

## Pins（本批翻红→绿）
analysis 绑 exact source hash · 同名替换 STALE · 低置信/未知 UNKNOWN 透传 · analysis 是 deletable projection(非 build 输入) · 仅一个新派生 schema(`manju.media-analysis/v1`) · cut 不穿显式禁区(proposal 阻断诊断) · cutdown 是提案 payload(非新 schema) · EDL proposal 零写入 · apply CAS · crop 跳变限速 · 安全区 · 多主体→UNKNOWN/needs_manual · blanking/pillarbox fallback · FORMAT_ONLY 不变式(13C 语义 digest) · rough cut 只标注/可逆/不改 Timeline · bridge 绑前尾/后首帧 hash + 付费恢复 + 当前绑定 review · bridge 未批准不进 final · 白名单意图派发到既有 executor · 核心无 LLM planner(断言无新 planner 模块) · LLM/VLM 不直接写 Timeline · 人工 split marker 优先 · NLE 帧映射。
