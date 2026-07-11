# AI_IDE_18 — WP0 存量审计基线 (Baseline)

日期：2026-07-11 · 分支：`claude/cost-optimization-strategy-cjfmn5`
契约：对白表演、云对齐、可选口型与专业音频母版
方法：只读审计（`git log --oneline -8` 定位 + 逐面源码审读），未改动任何文件。

## 落地上下文（已存在，本批复用）

| 面 | 位置 | 现状 |
|---|---|---|
| voice take / voice_id / staleness | `core/models.py:VoiceTakeSidecar`、`core/spec.py:voice_payload`（`VOICE_BIBLE_KEYS = voice/voice_ref/voice_sample/voice_id/tone`，v2 哈希） | `voice_hash` 已把 `voice_id` 纳入陈旧锚；`series.py:_voice_divergence` 已单独标语音字段跨集漂移 |
| transcribe / align / timing.json | `media/align.py`、`providers/asr.py` | `align_shot` 写 `<take>.timing.json`（**裸数组** `[{start_ms,end_ms,text}]`，Edge schema），三入口：ASR / from-srt / 文本锚定；失败仅加 advisory，未有 UNALIGNED 状态、未绑定 source-audio hash |
| captions word timing | `timeline/compiler.py:_load_voice_timing`（读裸数组）、`exporters/srt_ass.py` | 逐字时间来自 timing.json；`CaptionLine` 只有 text/start/end/speaker/shot，**无词级时间** |
| locale lines / base_hash | `core/locale.py` | `locales/<lang>/lines.yaml` 每行 `{text, base_hash}`，base_hash 判 STALE |
| audio buses / ducking / loudnorm | `media/render.py:_build_audio_graph`（voice/music/sfx/ambient + sidechaincompress 侧链 + `amix` + `loudnorm=I=-14:TP=-1.5:LRA=11`）、`build/mixer.py`、`core/models.py:AudioClip` | 四总线混音图已存在；`AudioClip` 携 gain/fade/offset/loop/duck 旋钮 |
| caption burn 开关 | `media/render.py:1310` `if ass_file is not None: vchain += ",ass=..."` | **textless 能力已存在**：`ass_file=None` 即不烧字幕——无需改 render.py |
| DeliveryManifest 音频角色 | `build/delivery.py`（13C `manju.delivery-manifest/v1`） | `KNOWN_ROLES` **已声明** DIALOGUE_STEM/MUSIC_STEM/SFX_STEM/FULL_MIX/M_AND_E_MASTER/TEXTLESS_MASTER/CAPTIONS_VTT，但 `_ROLE_BY_KIND` 未映射 → 诚实地「声明但未填充」；manifest 由 `exportstatus.deliverables()` 的行 `kind→role` 扫描组装，非文件系统扫描 |
| Provider capability / preflight | `providers/manifest.py`（`capabilities: list[str]`，已有 `first_last_frame` token）、`providers/base.py` GenerationRequest/admission/evidence | capability 是自由字符串表，**加 token 无需改 schema**；lip_sync 可走既有 admission 拿到 UNKNOWN/reconcile |
| take lineage | `core/models.py:TakeSidecar.redo_of` | provenance-only、default-absent、不入任何 content key——适合做 lip_sync 输出 take 的父系指针 |
| 14 qualification / 15 reviewer / 16 ladder | `providers/qualification.py`、`qc/agent_review.py`、`qc/production.py:SEVEN_ROUTES` | lip_sync 的资质档在 14 矩阵中应为 UNTESTED；drift 路由映射到既有 7-route 词表（ACCEPT_DEVIATION/FIX_IN_POST/EDIT_DONT_REGENERATE/REROLL/REWRITE_SOURCE/REGENERATE_REFERENCE/RESHOOT） |

## 识别出的缺口（本批要补，红先）

1. **无真实音频母版**：13C 的 stems/M&E/textless/VTT 角色被诚实跳过，无对应字节。→ WP7 用 ffmpeg 真出。
2. **timing.json 无对齐证据层**：无 source_media_hash 绑定、无 aligner 身份、无 phoneme/viseme/speaker/confidence、无 UNALIGNED、无同名替换 STALE。→ WP2 加证据 sidecar。
3. **无 voice provenance / 模板导出闸**：voice_sample 未记来源/授权、无 template-pack 导出阻断。→ WP1。
4. **无 lip_sync capability / lineage / 多脸 selector 拒绝 / UNKNOWN**。→ WP5。
5. **无 measured drift 计算 + 路由**。→ WP6。
6. **无时长适配顾问**（有序 §7 建议、不改文本）。→ WP4。
7. **无本地化 Skill**（Translate→Reflect→Adaptation）。→ WP8。

## 关键约束（审计确认，指导实现）

- timing.json 的裸数组被 `compiler._load_voice_timing` 与 `locale_build` 直读 → 证据层用**追加式 companion sidecar `<take>.align.json`**，保 timing.json 字节不变（否则须动 compiler/locale_build，超预算且高风险）。
- textless 无需改 render.py（`ass_file=None` 已在）。
- 母版 manifest 填充走**既有行扫描**：给 `deliverables()` 追加条件性行 + 在 `_ROLE_BY_KIND` 映射新 kind，不新增第二引擎。
- loudness 目标来自 delivery profile 附加字段，**不硬编码**。
- 环境无真实云 ASR/TTS/lip-sync 账户 → provider-facing 路径走 scripted transport；ffmpeg 是真的，stems/mix/loudness/VTT 是环境内完全真实的交付物。
