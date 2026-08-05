---
name: subtitle-standards
description: CJK 字幕规范工艺——竖屏每行 CJK 字数(横屏 ≤16、竖屏 ~9–10)、最多 2 行、阅读速度 ~9 CPS、单条时长 833ms–7s、切换对齐镜头切点、竖屏安全区(顶 150 / 底 300px)、中文样式(白色无斜体、名字用间隔号、以空格代标点)、卡拉 OK 字幕通病。设 rules.captions 并在越界时告警。触发词:字幕、subtitle、caption、每行字数、安全区、CPS、SRT、烧字。
when_to_use: 写或校对字幕、设置每行字数/安全区、或做字幕 QC 时。
tags: [craft, reference]
auto: true
---

# 字幕规范(subtitle-standards)

Manju 会把 CJK 字幕烧进画面——这些数字就是它该守的规范。你的活:把每行字数、时长、安全区、样式钉进 `rules.captions`,并在越界时告警。

## 什么时候不该用

这个技能**只**管上面 frontmatter `when_to_use` 说的那件事。误触发比漏触发贵——被拉进相邻场景后,agent 会照着这里的决策树一路走完。以下情形请转走:

| 情形 | 去哪 |
| --- | --- |
| 要把台词翻译 / 改写成另一种语言 | `localize-dialogue` |
| 字幕不准是因为配音换了、时间轴要重对 | `manju align`、`manju repair --op voice` |
| 要做的是封面文案 / 标题 | `cover-and-title` |

## 决策树:按画幅定每行字数

- **竖屏 9:16(短视频/短剧)** → 竖屏可用宽约横屏 60% → **每行规划 ~9–10 个 CJK 字**;字幕带**必须在底部 UI 区之上**。
- **横屏 16:9** → Netflix 简中 **16 字/行**(SDH 18)。
- **一律**:**最多 2 行**;阅读速度 **CJK ~9 CPS**(儿童 7、SDH 11;拉丁 17–20 CPS);单条 **最短 ~833ms、最长 7s**;条间最小间隔 **2 帧(~83ms)**;**字幕切换对齐镜头切点**。

## 竖屏安全区(1080×1920)

内容保持在中央 ~70%:**顶部 ~150px**(状态栏)、**底部 ~300px**(点赞/评论 UI)、两侧 ~50px。字幕带要坐在「底部 300px UI 区」之上。

## 中文样式(Netflix 简中)

白色、无衬线、**不用斜体**;人名之间用**全角间隔号 ·**;**不用句号/逗号,以空格代替**。卡拉 OK 逐字高亮的业余 tell:每行封顶 **4–6 词**、字间过渡 50–100ms(无间隔会频闪),高亮对比要强,剔掉语气词,**一定在手机上试看**。

## BEFORE / AFTER(业余 → 专业,附 WHY)

**例 1 — 竖屏一行 18 字**
- BEFORE:`他说那天晚上便利店的灯一直亮着我没敢进去`(一行,竖屏)
- AFTER:拆两行、每行 ~9 字:`他说那天晚上 / 便利店的灯一直亮着`,余下进下一条。
- WHY:竖屏每行 ~9–10 CJK 才读得完;18 字一行在手机上要么溢出要么字太小,直接掉完播。

**例 2 — 满是标点**
- BEFORE:`林夏,你到底,在怕什么?`
- AFTER:`林夏 你到底在怕什么`(以空格代逗号,去句读)
- WHY:中文字幕规范以空格代标点、无句号逗号;标点挤占本就紧的每行字数、也不合规范。

**例 3 — 一条挂 9 秒 / 不对齐切点**
- BEFORE:一条字幕横跨 S003–S005(9s),中间还切了两次镜。
- AFTER:按镜头切点拆成 3 条,每条 ≤7s、对齐 cut。
- WHY:单条最长 7s;字幕不随切点走会「串台」,观众对不上说话人。

**例 4 — 卡拉 OK 频闪**
- BEFORE:逐字高亮、一行 10 个词、字间 0 间隔。
- AFTER:每行 4–6 词、字间 50–100ms、高对比高亮,手机试看。
- WHY:词太多 + 无间隔 = 频闪(strobe),是廉价字幕的明显 tell。

## 拷贝进工作笔记的清单

```
字幕规范自检 · 逐条勾
[ ] 竖屏每行 ≤~10 CJK(横屏 ≤16)、最多 2 行?
[ ] 阅读速度 ~9 CPS 内(不是一闪而过)?
[ ] 每条 833ms–7s、条间 ≥2 帧、切换对齐镜头切点?
[ ] 字幕带在竖屏底部 300px UI 区之上、顶部避开 150px?
[ ] 白色无斜体、名字用 ·、以空格代标点?
[ ] (卡拉 OK)每行 4–6 词、字间 50–100ms、无频闪?
[ ] 已在手机比例上试看?
```

## 失败目录(smells + 在 Manju 里长什么样)

| smell | 在 Manju 的表现 / 抓法 |
| --- | --- |
| 每行超字数 | 超 `rules.captions.max_chars_per_line × max_lines` 预算 → `manju qc` 技术层 captions 报错(qc/checks.py `_technical_captions`) |
| 单条 >7s / 时长异常 | `manju qc` 字幕时长告警;`/subtitles` 编辑器改 |
| 字幕撞底部 UI | 安全区预览(round T `/subtitles` 真帧上叠)看是否压住底 300px |
| 乱码/翻转字 | `visual-qc-review` F 类(F1 OCR + lang-detect;F3 镜像翻转 CJK 穿帮) |
| 不对齐切点 | `/edit` 多轨(字幕轨)看 cue-over-silence / speech-without-cue 同步提示 |

## Manju 落地(把规范钉进 rules 与编辑器)

- **每行字数/行数**:`timeline/rules.yaml` 的 `captions.max_chars_per_line` / `max_lines`(已被 QC 强制执行:预算 = `max_chars_per_line × max_lines`,超了 `manju qc` 技术层报错)。竖屏建议 `max_chars_per_line: 10`。
- **字幕编辑器**:`/subtitles`(round T)——inline 改文本/时序、增/拆/合/删、首次编辑要显式确认手动接管(保留 `generated.srt` 对比,「还原自动字幕」可回退)、安全区预览叠在真帧上。
- **来源**:字幕来自对白 + TTS 对齐(词级字幕自动跟随,Edge TTS 开箱即用);改台词 → 配音 stale → `manju voice <shot>`。
- **导出**:`manju export --srt`(从编译时间线导 SRT/ASS)。
- **告警**:让越界(行 >10 CJK、条 >7s)在 `manju qc` 报出来;与 `audio-finishing`(配音时长)、`narrative-pacing`(台词字数即节奏)配合。

## 验收 eval

1. 给一条竖屏 18 字字幕,是否拆成每行 ~9 字、≤2 行?
2. 给一条挂 9s 且跨切点的字幕,是否按切点拆成 ≤7s 多条?
3. 给一句带逗号句号的中文字幕,是否改成以空格代标点、白色无斜体?
