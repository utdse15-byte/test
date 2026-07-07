---
name: audio-finishing
description: 短视频声音收尾工艺——整体响度 −14 LUFS / −1 dBTP、BGM 在人声下闪避(duck)≥15–20 dB(剪映 ~30% + 2s 淡出)、SFX ≤ 音乐 30%、卡点踩鼓点。把响度与混音目标钉进 Manju 的 music/ducking 旋钮与混音台,并作 −14 LUFS 建议。触发词:音量、响度、LUFS、BGM、配乐、混音、闪避、ducking、音效、SFX、卡点、淡入淡出。
when_to_use: 配 BGM/音效、调混音、担心响度或人声被压过、或做音频 QC 时。
tags: [craft, reference]
auto: true
---

# 声音收尾(audio-finishing)

人声听不清、BGM 盖过说话、整片忽大忽小——都是业余音频的 tell。你的活:把响度与闪避目标钉进 `rules.yaml` 的 music/ambient 旋钮,让人声永远在前。

## 决策树:按素材配置声音

- **有对白** → BGM **必须开 ducking**,在人声出现时下压 **≥15–20 dB**(剪映口径:音乐压到人声的 ~30% + 2s 淡出);整体响度对到 **−14 LUFS / −1 dBTP**。
- **纯音乐/无对白** → 不需要 duck;整体仍对 −14 LUFS;卡点踩鼓点,选无人声 ~30s 曲。
- **有环境音 ambient** → 作床音铺底,gain 远低于人声;别和 BGM 抢。
- **SFX/转场音** → SFX **≤ 音乐轨的 ~30%**;whoosh 只在关键节拍用,别每切必响。

响度目标随投放 profile:流媒体 **−14 LUFS / −1 dBTP**(TikTok 有说更响 ~−10~−9);Netflix **−27 LKFS 对白门控 / −2 dBTP**;广播 EBU R128 **−23 LUFS / −1 dBTP**。短视频默认 **−14 LUFS**。

## BEFORE / AFTER(业余 → 专业,附 WHY)

**例 1 — BGM 盖过人声**
- BEFORE:`music.ducking: false`,音乐和人声同响度。
- AFTER:`music.ducking: true`,duck 深度让音乐在说话时降 15–20 dB(≈人声的 30%)。
- WHY:不闪避,观众听不清台词 → 划走;闪避是「人声永远在前」的基本功。

**例 2 — 整片忽大忽小**
- BEFORE:各镜配音/素材响度不一,没有统一响度目标。
- AFTER:整片归一到 −14 LUFS integrated、真峰 ≤ −1 dBTP。
- WHY:平台会二次归一;不对目标会被压得发闷或忽大忽小,体验差。

**例 3 — 转场音效满天飞**
- BEFORE:每次切镜都来一记 whoosh,SFX 和音乐一样响。
- AFTER:whoosh 只留关键节拍;SFX 压到音乐的 ≤30%。
- WHY:滥用转场音是廉价感来源;SFX 要点缀不要抢。

**例 4 — 卡点没踩鼓点**
- BEFORE:切镜时间随意,和音乐节拍错开。
- AFTER:切点对齐鼓点(卡点),选无歌词 ~30s 曲。
- WHY:踩点让快剪有律动;错拍显得松散。

## 拷贝进工作笔记的清单

```
声音收尾自检 · 逐条勾
[ ] 有对白 → BGM 开了 ducking,人声出现时压 ≥15–20 dB(≈30%)+ 尾部淡出?
[ ] 整片响度对到 −14 LUFS integrated、真峰 ≤ −1 dBTP?
[ ] SFX ≤ 音乐的 ~30%,转场音只在关键节拍?
[ ] 卡点踩鼓点、BGM 无歌词干扰人声?
[ ] 无削波(peak ≥ −0.1 dBFS)、无异常静音(≤ −50 dB RMS)?
[ ] ambient 床音 gain 远低于人声、不和 BGM 抢?
```

## 失败目录(smells + 在 Manju 里长什么样)

| smell | 在 Manju 的表现 / 抓法 |
| --- | --- |
| BGM 未闪避、盖人声 | `manju qc` 内容层:配了 BGM 却 `music.ducking: false` → 告警(qc/checks.py `set rules.yaml → music.ducking: true`) |
| 响度未对标 | 目前 QC 查削波/静音,尚无 LUFS 目标 → 本技能作 −14 LUFS **建议**(loudnorm) |
| 削波/爆音 | `manju qc` 峰值 ≥ −0.1 dBFS 报错(round Q) |
| 异常静音 | `manju qc` ≤ −50 dB RMS 静音检查 |
| 音画不同步/唇音错位 | `visual-qc-review` J1(A/V sync)、J2/J3(loudness/真峰) |

## Manju 落地(把声音钉进 rules 与混音台)

- **闪避旋钮**(`timeline/rules.yaml` 的 `music` / `audio.ambient`,core/models.py):`ducking: true|false`,以及闪避形状 `duck_threshold`(默认 0.05)/ `duck_ratio`(默认 8.0)/ `duck_attack_ms`(5)/ `duck_release_ms`(250)——render.py 用 ffmpeg `sidechaincompress` 实现(键控人声)。要更深的闪避提高 ratio / 降 threshold。
- **BGM 源与淡入**:`music.source`(项目相对路径)、in-point + fade-in(round T);ambient 同族旋钮。
- **混音台**:`/mixer`(round T)——BGM 选择(从 imports+library,可试听)、in-point、淡入淡出、SFX 列表编辑、闪避参数(新手模式隐藏,专业模式 `mj-pro-only` 展开);读/写 `rules.yaml` 并报重渲染 verdict。
- **响度建议**:QC 尚无 LUFS 目标——本技能建议对 −14 LUFS/−1 dBTP(ffmpeg `ebur128`/`loudnorm`),作为 advisory,不擅自改人的手工混音。
- **配音**:`manju voice <shot>` 只增新 voice_take;与 `subtitle-standards`(配音时长决定字幕)、`narrative-pacing`(卡点即节奏)配合。

## 验收 eval

1. 给一个配了 BGM 但未开 ducking 的项目,是否建议开 ducking 并给出 15–20 dB 深度?
2. 给一段响度杂乱的片,是否给出 −14 LUFS / −1 dBTP 归一建议?
3. 给一个每切必 whoosh 的时间线,是否把 SFX 收敛到关键节拍且 ≤ 音乐 30%?
