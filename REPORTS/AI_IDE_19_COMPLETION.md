# AI_IDE_19 — 完成报告 (Completion): 素材理解、语义剪辑、Cutdown 与 Smart Reframe

日期：2026-07-11 · 分支：`claude/cost-optimization-strategy-cjfmn5`（仅工作树，未 commit/push）
契约：`Manju_AI_IDE_19_...Compact_v2.md` · 编排裁定：`C19_ADDENDUM.md`
纪律守住：分析=派生证据(绝非剪辑事实) · LLM/VLM 不直接写 Timeline · 核心无 LLM planner ·
fixture 分析先于云 · 红先行 · 老项目字节不变 · 未触碰 DECISIONS.md / README.md / tests/fixtures/golden/

## (a) WP0 审计（§3 全部复用，零重建）
media import/hash=`ingest.hash_file`+`register_take`+`MANUAL_HASH`；transcription/word-timing=18 `media/timing.py`(`<take>.align.json`：start/end/text/speaker/confidence)；Timeline=`compiler.VideoClip`+`snap_to_frame_grid`；roundtrip=`build/roundtrip.py`(`plan/apply_roundtrip`，OTIO trim→`set_inout` 行)+`exporters/{otio,jianying,openclap,native_draft}`；cutdown/variant=13C `build/delivery.py`(`variant_kind`、`timeline_semantic_digest`、`check_format_only_invariant`、`cutdown_source.ref`、`external_framing` 采纳)；FFmpeg crop=`repair_ops.crop_pad_take`/`set_inout_take`+`media/render.py`(overlay/scale/pad)；资质梯=14 `qualification_state`+`reviewer_admission`；付费路径=`providers/base.GenerationRequest`→`CloudProvider.generate`→`register_take`。全部作为复用点，未复制。

## (b) 分析证据 shape + fixture 分析器 + 资质闸（WP1，`media/analysis.py`）
- 一个新派生 schema **`manju.media-analysis/v1`**（与资质报告同类）。header 绑 **exact `source_media_hash`**（内容寻址落 `reports/analysis/<hash>.json`）+ `source_ref` + `analyzer{provider,profile_digest,rubric_digest}`。八轴**全 OPTIONAL**：transcription_ref/shot_boundaries/scene_boundaries/ocr/roi_tracks/audio_regions/key_moments/quality_flags。
- **诚实 UNKNOWN 透传**：`analyze_with_fixture` 对显式 `"UNKNOWN"` 或 `confidence < min_confidence`(默认0.25) 的轴**保留其值**并列入 `unknown_axes`——绝不丢弃、绝不升格为确信事实（§4）。
- **fixture 先行**：`analyze_with_fixture(source_hash, fixture_json)` 确定性离线、读提交 JSON（20A 语料模式），先验证数据合同。
- **STALE**：`analysis_status(evidence, media_path)` 读时用 header hash 比对磁盘字节——**同名替换即 STALE**（镜像 18 timing）。删报告=inert projection，源不变、可重分析（pin）。
- **云闸在 PROVIDER 层**：`providers.qualification.analyzer_admission`（镜像 15 reviewer 闸）→ 未达 `DRY_RUN_VALID`/BLOCKED/STALE 返回结构化 **`ANALYZER_NOT_QUALIFIED`**；由 cli.py 调用，`media/` **不 import qualification**（build-boundary guard §15，即 c14 定桩）。

## (c) segments / cutdown 校验语义（WP2/3，`build/segments.py`）
- **WP2** `derive_segments(evidence, word_timing)` 纯派生：每段 start/end、scene、shot_index、`dialogue_complete`（18 词时间跨边界=断词=incomplete）、visual/audio continuity、`cut_risk`（边界邻接：断对白/关键动作→**high**；场景证据缺失→medium；对齐 shot 边界→low；无边界证据→**unknown**）、key_subjects、evidence_refs。人/Agent 才做最终 source proposal。
- **WP3** cutdown = `keep/remove/reorder/transition/reason/source_analysis_digest` **提案 payload（无新 schema，断言）**，骑既有 Director `ProposalAction` 信封（`cutdown_proposal_action` 返回 `{"type":"cutdown",...}`）。`validate_cutdown` **零写**：时间边界 sanity + **CUT_CROSSES_NO_CUT_ZONE**（remove 边界落在对白/关键时刻禁区=blocking 诊断，禁止静默切断，是**提案诊断非引擎硬阻**）+ MEDIA_HASH_MISMATCH。`apply_cutdown` **CAS**：源自提案后位移(hash≠) → `StaleCutdownError`，否则产 `cutdown_source{ref:"proposal:<id>",kind:"approved_proposal"}` → 喂 13C **EDITORIAL_CUTDOWN**（其 `_build_variant` 不再报 CUTDOWN_SOURCE_REQUIRED，实测）。

## (d) reframe 编译事实 + FORMAT_ONLY 证明（WP4，`media/reframe.py`）
- `compile_crop_keyframes(tracks, source_wh, target_wh, max_px_per_s, safe_areas)` 纯函数：
  - **跳变限速**：相邻关键帧 crop 位移 ≤ `max_px_per_s·dt`（实测 0.1s 内 raw 1536px 被限到 ≤101px）；
  - **安全区**：字幕/logo/关键物体框保持在 crop 窗口内（右缘框 → 窗口右推包含之）；
  - **多主体冲突**：主体中心跨度 > 窗宽 → `status=needs_manual`（UNKNOWN，不猜）；
  - **不可行 → blanking**：安全框宽于任何 crop 窗 / 目标比例不可裁 → `status=blanking`,`strategy∈{pillarbox,letterbox}`。
- **FORMAT_ONLY**：`changes={duration,selection,audio,subtitle:False}`（几何唯一变量）；`reframe_artifact` 绑 base `timeline_semantic_digest`，`reframe_preserves_semantics` 复用 13C `check_format_only_invariant` → `[]`（9:16 裁剪不移语义 digest，因 w/h 被排除）。
- **可执行（13C 此前 declared_only → 本批 EXECUTES）**：`ffmpeg_crop_expr` 产确定性 `crop=w:h:x(t):y(t)` 分段线性表达式；`execute_reframe` 走 repair-ops 先例（自含 filtergraph，不碰 render.py）产**追加式**新 take。**真 ffmpeg 实测**：320×180→180×320 裁剪，时长保持（±80ms 内），sidecar `op="reframe"`。
- **人可编辑 + 采纳**：keyframes 落显式 framing 工件，`adopted=False/drives_render=False`（inert）→ `adopt_reframe_artifact` 显式采纳才 drive render（复用 13C external-framing 采纳路径）。

## (e) rough-cut 提案 shape（WP5a，`qc/roughcut.py`）
`rough_cut_proposal(align_evidence)` 消费 18 `<take>.align.json`（无新转写库）→ `{kind:"speech_rough_cut", default_action:"annotate", reversible:True, source_transcript_ref:<audio hash>, annotations[]}`。检测 filler(口头禅)/long_pause(长停顿)/repetition(重复句)/mistake/sensitive；每条带**原始 range+reason+cue_index**（可恢复）。**默认只标注不删**；反转=不 apply（不 apply 即零写，实测）。`to_cutdown(select)` 才把选中项转 WP3 cutdown 提案 payload——**rough cut 本身绝不改 Timeline**（源码断言无 save_timeline/register_take/checked_shot_write）。

## (f) bridge 执行路径 + never-in-final 证明（WP5b，`build/bridge.py`）
- 16 曾定桩 bridge=proposal-only/transport-0；**本批执行**：`plan_bridge` 绑**前镜尾帧 hash + 后镜首帧 hash + duration + direction**，缺任一端帧/非正时长→`BridgeError`；`request_digest` 确定性（poll-only resume 幂等键→付费恢复不重投）。
- `execute_bridge` 走**标准** `GenerationRequest`→`provider.generate`→`register_take`（**追加式**；`params.bridge` 载 plan，`redo_of` 记父）——继承 14 admission/预算/付费恢复。无真账户→scripted provider stand-in。
- `bridge_lineage` 绑 input 双帧 hash + **output_media_hash**，`is_transition_candidate=True/adopted=False`。`bridge_review_requirement` → 需**当前绑定** review（15，绑 output 字节）；`adopt_bridge` 拒绝无 review / review 绑旧字节（不得掩盖 continuity 失败）。
- **未批准不进 final**：`assert_not_in_final(lineage, compiled_sources, adopted=False)` → 候选不在 final=`[]`；若混入 final=**UNAPPROVED_BRIDGE_IN_FINAL** blocking；显式 adopted 后合法（补 16 gate + 13C manifest 检查）。
- 真 bridge 资质闸在 provider 层 `qualification.bridge_admission`（**`BRIDGE_NOT_QUALIFIED`**），cli 调用；build/ 不 import qualification。

## (g) 白名单编排 map（WP6，`build/toolmap.py`）
`TOOL_WHITELIST` = 10 意图 op → **既有** deterministic executor：trim/split→`repair_ops.set_inout_take`、crop→`crop_pad_take`、speed→`retime_take`、gain/ducking→`build.mixer.apply_mixer`、caption→`gui.captions_edit`、transition→`TimelineRules.transition_overrides`、overlay→`OverlayClip`、reorder→`Project.save_index`。`resolve_tool` 校验+成形 payload；`dry_run_tool` 复用校验、local op **priced 0**；**off-whitelist→`ToolError`**（拒绝不臆造）。**核心无 LLM planner**：断言 `src/manju/**` 无 `planner.py`、toolmap 无 LLM import/`def plan(`。是纯数据+派发，未新建引擎。

## (h) 文件 + Δvs≤10（生产 9/10）
新增6：`media/analysis.py`·`build/segments.py`·`media/reframe.py`·`qc/roughcut.py`·`build/bridge.py`·`build/toolmap.py`。改3(追加)：`providers/manifest.py`(media_analysis+generative_bridge capability token，镜像 lip_sync)·`providers/qualification.py`(analyzer_admission+bridge_admission 闸，镜像 reviewer 闸)·`cli.py`(`analyze/segments/reframe/rough-cut/tool` 命令)。**WP7 零新增生产文件**（验证+定桩既有 roundtrip/13C）。测试7：`tests/test_c19_{analysis,segments,reframe,roughcut,bridge,orchestration,cli}.py`（51 用例）。

## (i) Pins（红→绿）
analysis 绑 exact hash · 同名替换 STALE · 低置信/未知 UNKNOWN 透传 · 报告 deletable/非 build 输入(c14 grep 定桩仍绿) · 仅一新派生 schema · cut 不穿禁区(blocking 提案诊断) · cutdown=提案 payload 非 schema · EDL 零写 · apply CAS · crop 限速 · 安全区 · 多主体→needs_manual · blanking/pillarbox · FORMAT_ONLY(13C 语义 digest) · reframe 可执行且保时长 · rough cut 只标注/可逆/不改 Timeline · bridge 绑双帧+output hash · bridge 走标准付费路径(付费恢复继承) · bridge 当前绑定 review · bridge 未批准不进 final · 白名单派发既有 executor · 无 LLM planner(无 planner.py) · LLM/VLM 不直接写 Timeline(派生模块无 timeline writer) · 人工 trim/split marker→reviewable set_inout patch(零写) · NLE 整数帧映射。

## (j) 套件结果（同步全量，两半）
- C19：**51 passed**（analysis 11 · segments 9 · reframe 9〔含真 ffmpeg 裁剪〕· roughcut 5 · bridge 6 · orchestration 9 · cli 2）。
- 邻接回归（qualification/manifest/delivery/cli/reviewer/lipsync/roundtrip/pullsheet/transitions）：154 passed。
- **全量**（`python -m pytest -p no:cacheprovider`，两同步半）：**1478 passed,5 skipped** + **1580 passed,7 skipped** = **3058 passed, 12 skipped, 0 failed**（6:33 + 8:12）。12 skips 为既有环境门(网络/可选工具)，本批未引入；零回归。

## (k) 偏差 / 跳过（诚实）
1. **无真云账户**：analyzer / bridge 的真云路径 SKIPPED_WITH_EVIDENCE——资质闸拒 `ANALYZER_NOT_QUALIFIED`/`BRIDGE_NOT_QUALIFIED` 是其 stand-in；bridge 执行用 scripted provider 证明标准路径（reframe crop 是真 ffmpeg）。
2. **WP7 AAF/FCPXML SKIPPED_WITH_EVIDENCE**（无真实编辑器 fixture）；WP7 人工 trim/split marker→`set_inout` reviewable patch(零写) + 13C 整数帧映射为**既有能力，验证+定桩**，未新增生产文件（守预算）。
3. **cutdown apply 边界**：本批产 `approved_proposal` ref 喂 13C EDITORIAL_CUTDOWN（13C 只打包既有 cut）；CAS 拒陈旧源。真正的时间线重切由 13C 既有变体路径承担，未在本批重造切割引擎（与 13C 边界一致）。
4. **闸在 provider 层**：analyzer/bridge admission 落 `qualification.py` 并由 cli 调用，`media/`·`build/` 不 import qualification（build-boundary guard），故这两个 gate 的 C19 测试直接调 `providers.qualification`。

## (l) REPORTS 路径
- `REPORTS/AI_IDE_19_BASELINE.md`
- `REPORTS/AI_IDE_19_COMPLETION.md`
