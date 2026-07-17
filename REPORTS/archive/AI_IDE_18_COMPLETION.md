# AI_IDE_18 — 完成报告 (Completion)

日期：2026-07-11 · 契约：对白表演、云对齐、可选口型与专业音频母版
纪律：文本事实源 · 媒体追加 · 核心不调用 LLM/VLM · 无本地声学模型 · 未 commit/push
未触碰：DECISIONS.md / README.md / tests/fixtures/ / golden/

## 交付概览（10 生产文件，skill 不计入）

| # | 文件 | WP | 性质 |
|---|---|---|---|
| 1 | `media/masters.py` | WP7 母版（本批的牙齿） | 新增 · 真 ffmpeg |
| 2 | `media/timing.py` | WP2 对齐证据 sidecar | 新增 |
| 3 | `build/voiceid.py` | WP1 语音身份 + 授权闸 | 新增 |
| 4 | `qc/lipsync.py` | WP4/5/6 时长顾问 · lip_sync · drift | 新增 |
| 5 | `build/delivery.py` | WP7 角色映射 + loudness 事实折入 | 改 |
| 6 | `build/exportstatus.py` | WP7 母版/VTT 交付行（条件性） | 改 |
| 7 | `exporters/srt_ass.py` | WP7 WebVTT 导出 | 改 |
| 8 | `media/align.py` | WP2 盖证据头 + UNALIGNED | 改 |
| 9 | `providers/manifest.py` | WP5 lip_sync capability token | 改 |
| 10 | `cli.py` | `manju masters` 命令 | 改 |
| — | `skills/localize-dialogue/` | WP8 SKILL.md + 数据包 | 新增（不计入预算） |

测试：`tests/test_c18_dialogue_masters.py`（31 用例，§12 二十行 ~1:1）。

## WP7 音频母版 —— 全部真实 ffmpeg（EBU R128 实测）

`render_masters(project, timeline)` 从既有四总线（voice/music/sfx/ambient）真渲染：

- **DIALOGUE_STEM**=voice；**MUSIC_STEM**=music；**SFX_STEM**=sfx+ambient；**FULL_MIX**=四总线相加；**M_AND_E_MASTER**=FULL_MIX−对白（music+sfx+ambient）。48kHz/立体声/pcm_s16le。
- **stems 相加 = full mix**：full mix 由各 bus stem `amix normalize=0` 构造，关系精确、时长一致（§12 通过）。
- **M&E 证明不含对白**：`excludes_dialogue=True` 且 `buses` 不含 voice；`volumedetect` 实测 M&E max_volume < full mix（去掉最响的对白总线），对白 stem 本身非静音（§12 通过）。
- **loudness 实测为事实**：`loudnorm print_format=json` 取 integrated LUFS / true-peak dBTP / LRA + `ebur128` 短时；写入 index，绝非目标值。
- **loudness 目标来自 profile**：`delivery_profiles.<id>.loudness_target_lufs` 驱动额外的 `full_mix.loudnorm.wav`；实测命中（-14 目标 → 实测 ~-14，误差<1.5）。目标非硬编码（pin 通过）。
- **manifest 角色填充**：母版落 `exports/masters/`，`exportstatus.deliverables()` 追加条件行，`delivery.build_manifest` 经既有行扫描将 5 个角色以 **TECHNICALLY_VERIFIED**（human 仍 PENDING）填入，并折入 source/audio-input hashes + 实测 loudness。时间线语义位移 → 全部 STALE。
- **实测样例**（1.5s，四总线）：DIALOGUE I=-21.85/TP=-21.07 · FULL_MIX I=-19.39/TP=-12.90 · M_AND_E I=-23.01/TP=-16.96；full max -12.9 vs M&E max -17.0（对白已除）。
- **textless**：render.py 的 `ass_file=None` 已具烧字幕开关能力——textless 母版无需改 render.py（审计事实，见 BASELINE）。

## WP7 WebVTT（13C CAPTIONS_VTT 角色终于变真）

`srt_ass.compile_vtt` 输出真 WebVTT（`WEBVTT` 头 + `HH:MM:SS.mmm` 点分毫秒），与 SRT/ASS 同源逐条一致；`export_captions` 现产 `captions.vtt`（auto/manual 双模）。CaptionLine 无词级时间 → **不伪造** 逐词 karaoke `<timestamp>` token（§5 纪律；karaoke 条件性 SKIPPED_WITH_EVIDENCE）。

## WP2 对齐证据 sidecar

追加式 companion `<take>.align.json`（保 timing.json 字节不变，compiler/locale 直读不受影响）：

- header 绑定 **source_media_hash**（精确音频）+ **aligner{provider, profile_digest}** + status。
- cue 可携 phoneme[]/viseme[]/speaker/confidence（Provider 有证据时；本地路径诚实缺省）。
- **UNALIGNED**：`write_unaligned` 记 status+reason 且 cues=[]，**绝不伪造均匀时间**（pin）。
- **同名替换 STALE**：读时用 header 的 hash 对当前字节，换了内容同名文件即 STALE（pin）。
- `align_shot` 现同时盖证据头（provider=asr/from_srt/text_anchor），ASR/SRT 空结果 → UNALIGNED。

## WP1 语音身份 + provenance + 模板导出闸

`build/voiceid.py`（bible 角色为自由 dict，纯追加，无 models.py 改动）：

- 附加字段：`voice_provider/voice_model/voice_ref_hash` + `voice_provenance{source, license_or_consent, note}` + `voice_locked`。
- **template_export_gate**：缺权利信息 → `blocked=True/shareable=False`（本地仍可用，仅阻断模板包导出——17 消费此面，pin）。齐全 → shareable。
- **lock** = 显式 `voice_locked` 源字段；audition 是一次性预览常量 `AUDITION_IS_PREVIEW`（绝非 take）。
- **profile 变更 → 下游陈旧**：`profile_digest` 覆盖身份字段（voice_id/provider/model/ref），一个 note 不动它（pin）。

## WP5 lip_sync capability + lineage

- `providers/manifest.LIP_SYNC_CAPABILITY = "lip_sync"`（capabilities 自由表，加 token 无 schema 改动）。资质档在 14 矩阵中诚实标 UNTESTED（无云账户）。
- `plan_lipsync`：绑定**精确 video+audio hash** + 显式 subject selector；**多脸无 selector → 拒绝**（LipSyncError）；空 hash 拒绝。
- `lipsync_lineage`：输出为**新 append-only take**，`redo_of=parent` 指向父系，原视频不动。
- `lipsync_result`：超时/不可识别/遮挡 → **UNKNOWN**（不冒充 PASS）；**函数签名不收 score**——模型自报分不算对齐（pin）。走既有 GenerationRequest/admission 拿 UNKNOWN/reconcile。

## WP6 measured drift（纯计算，无模型自报分）

`measure_drift(audio_onsets, mouth_events, face_visible, thresholds)`：overall offset（中位）/ max deviation / local drift / alignability → PASS/FAIL/UNKNOWN，路由映射到既有 7-route（offset fix→FIX_IN_POST、time-stretch→EDIT_DONT_REGENERATE、re-TTS/re-lipsync→REROLL、换镜→RESHOOT）。无嘴动观测或脸不可见 → UNKNOWN（不凭音频独造 drift）。阈值为声明参数，非硬标准。

## WP4 时长适配顾问（绝不改文本）

`duration_proposals(voice_ms, slot_ms, thresholds)`：按 §7 顺序发建议（RETTS_SPEED → SOURCE_PROPOSAL → 阈值内 TIME_STRETCH → PICTURE_EDIT → ACCEPT_DEVIATION）。`text_mutation=False`，pin 断言前后 dialogue 文本 digest 不变；超 `max_stretch_pct` → TIME_STRETCH_BLOCKED 不提供。

## WP8 本地化 Skill

`skills/localize-dialogue/`（SKILL.md + data.yaml 数据包）：terminology→Translate→Reflect→Adaptation→timing fit 五步产 locale source patch（`lines.yaml`）；核心只做 base_hash/timing/字幕规范/音频验证。eval fixtures 钉 frontmatter/必备段/五步词表/locale 字段/duration 路由——防漂移。技能不含 `--yes`、不直调 Provider。

## Pins 翻红→绿（§12 映射）

voice lock/stale · 对齐绑定 exact audio · 同名替换 stale · 无法对齐 UNALIGNED · 核心不静默改对白 · lip-sync timeout/遮挡 UNKNOWN 不重提 · output append-only · 多角色 selector · drift 阈值/UNKNOWN · stems 与 full mix 相加/时长 · M&E 不含对白 · textless 按声明 · loudness/true-peak · locale/base_hash · **无本地模型依赖**（断言 masters/timing/lipsync 源码无 whisperx/wav2lip/musetalk/torch/comfyui）· J/L cut+beat markers（见偏差）· karaoke/CJK（见偏差）· voice ref 授权受检。

## 偏差 (deviations) — 均诚实标注

1. **对齐证据用 companion sidecar `<take>.align.json`**，非原地重塑 timing.json。理由：timing.json 裸数组被 compiler/locale 直读，原地改 object 形会破字节等价并须动这两处（超预算/高风险）。语义（header source_media_hash + aligner、per-cue 富字段、UNALIGNED、STALE）完整。
2. **J/L cut + beat markers**（WP7 后期意图）与 **karaoke/CJK 折行**：未落地为生产字段。理由：预算已满 10 生产文件；beat markers 需动 rules.yaml transitions（16）编译面，karaoke 需 CaptionLine 携词级时间——现渲染器不能表达 → 诚实 **SKIPPED_WITH_EVIDENCE**（VTT 已明确不伪造逐词 token）。
3. **真云 ASR/TTS/lip-sync**：环境无账户 → provider-facing 路径走 scripted stand-in；真档在 14 资质后（lip_sync 在 14 矩阵 UNTESTED）。ffmpeg 母版/VTT/loudness 是环境内**完全真实**交付。
4. **TEXTLESS 视频母版**：能力已在（render `ass_file=None`），未额外产整段无字幕视频文件（需完整视频渲染管线+真视频 take）；作为既有能力记录，未加独立 deliverable 行。

## 套件结果

- `tests/test_c18_dialogue_masters.py`：31 passed。
- 全量套件：见最终回报（未改动 tests/fixtures/golden/；触碰的共享文件回归为零）。

---

## 14_21 CLOSEOUT 更正块（2026-07-12）

1. **命名**：DIALOGUE/MUSIC/SFX_STEM、FULL_MIX、M_AND_E_MASTER 更名为 RAW_DIALOGUE/MUSIC/SFX/AMBIENT_STEM（ambient 独立成 stem）、RAW_STEM_SUM、M_AND_E_BUS_EXCLUSION_MASTER——原 FULL_MIX 是未复用 program mixer（ducking/loudnorm）链的裸总线和，不得再暗示 program master；PROGRAM_MASTER 明确不产出并在 `index.roles_absent` 记录原因（A04/A05）。delivery 映射保留旧名别名，无角色丢失。
2. **M&E 承诺降级**：(b) 原文「M&E 证明不含对白」过强——实际保证是 BUS EXCLUSION（混音不含 voice 总线），非内容级验证。index 现携 `excludes_voice_bus=true` + `mne_claim="bus_exclusion"` + `content_verified=false`（A06）。
3. **缺源与电平**：新增 per-bus expected/resolved/dropped 记账——缺任一 expected 源 ⇒ 该 master INCOMPLETE/BLOCKED，静默替代永不算 verified（A01/A02）；两个 sum 统一 −6 dB headroom + −1 dBTP 上限，超限为 blocking `AUDIO_CLIPPING`（A03）；loudnorm 和 raw sum 为不同 kind，绝不混称。
