---
name: repair-loop
description: QC 后的修复闭环程序(validator→fix→repeat)——把每条 QC 发现映射到对应的 Manju 修复 op(retime/extend/trim/inout/croppad/voice/redo),只增新 take 并带血缘,再复核直到通过。含花钱前的 dry-run/审批闸与「auto 只做安全项」纪律。触发词:修复、修、repair、fix、qc 修复、repair_plan、定点重做、redo、闭环。
when_to_use: 一轮 QC 出了 findings 之后、要按发现逐项修复、或收到「修/fix/repair」请求时。
tags: [qc, task]
user_invocable: true
---

# 修复闭环(repair-loop)

修复是一个**反馈环**:QC(validator)→ 逐项修(fix)→ 复核(repeat),只在通过时收工。你的活:读懂每条发现、选对修复 op、只增 take、再跑 QC 闭环。花钱的重做先 dry-run + 问。

## 决策树:按发现类型选修复手段

- **时长/时序类**(镜头太短放不下对白、时长不匹配)→ `manju repair --op extend`(freeze/pad_black)或 `--op retime`(变速 `--factor`)或 `--op trim`(裁短)。
- **画幅/缩放类**(比例错、劣质缩放,QC I6)→ `manju repair --op croppad`(center_crop / pad_blur)。
- **裁时间范围**(掐头去尾)→ `manju repair --op inout --in-ms A --out-ms B`(virtual 保留手柄供真交叉溶解 / reencode 实裁)。
- **配音类**(改台词后配音 stale、音画不同步 J1)→ `manju repair --op voice`(留画面、重生 TTS、按新时长比例对齐 cue;手动 cue 不动)。
- **画面缺陷类**(A–J 视觉:变脸/多指/漂移/穿帮)→ **不是 ffmpeg 能修的**,走**定点重做** `manju redo SNNN [--seed N] [--provider X]`(换 seed 重编 prompt / 换 provider / 走降级链),再 `manju select` 选新 take。
- **内容审核拒 content_rejected** → **别无脑重试**:`manju tasks` 看拒绝原因,改写 prompt(见 `prompt-craft`)或走降级链(§8)。

## QC 发现 → 修复 op 对照(核心)

| QC 发现(层/码) | 修复 op |
| --- | --- |
| 对白放不下 / 镜头偏短 | `repair --op extend --shot S001 --ms 500 --mode freeze` |
| 节奏太拖 / 偏长 | `repair --op trim --shot S001 --ms 300` 或 `--op retime --factor 0.9` |
| 画幅/比例错(I6) | `repair --op croppad --shot S001 --mode pad_blur` |
| 掐头去尾 | `repair --op inout --shot S001 --in-ms A --out-ms B` |
| 配音 stale / 音画不同步(J1) | `repair --op voice --shot S001 [--dry-run]` |
| 削波/静音(J4) | 音频修:见 `audio-finishing`(rules ducking/gain);或重配音 |
| 视觉缺陷(A–J·V:变脸/多指/穿帮) | `manju redo S001 --seed N`(不是 ffmpeg 能修) |
| BGM 未闪避 | 改 `rules.yaml` `music.ducking: true`(`audio-finishing`) |

## 修复程序(逐步,闭环)

1. `manju build --target qc` 或 `manju qc [--deep]` → `reports/qc.json`/`qc.md`/`repair_plan.yaml`。
2. 读 `reports/qc.md`:分层看 存在 / 技术 / 内容(`must_show` 违背等)的 error/warn;严重度判读接 `visual-qc-review`。
3. **auto 只做安全项**:`manju repair --auto` 只执行 auto-safe(`redo_new_seed`/`degrade_fallback`),其余留给人/你判断——**绝不让 auto 触发花钱动作**。
4. 逐项按上表选 op 修;花钱/长耗时的 redo **先 `--dry-run` + 命中 ask_before 就问人**。
5. 重做 → `manju select SNNN take_NN` 选新 take(原选择仍有效直到你选新的)。
6. `manju check` → `manju build` → **再 `manju qc` 复核闭环**;不过就回步骤 4。
7. 阶段 `git commit`。

## 从 assurance 出发的修复回路(DR02)

除了 `repair_plan.yaml`(机检层),`reports/qc.json` 现在还带一个 `assurance` 块:每个镜头的**派生验收态**(accepted / rejected / unknown / stale / …)+ 针对 rejected/unknown 镜头的**只读修复提案** `assurance.proposals`。据此修复:

1. 读 `reports/qc.json → assurance.proposals`(或看 `manju qc` 摘要 / `manju director suggest` 的验收建议)。
2. 对每个 `failed_expectation_id`(该镜头承诺却没满足的一条)→ **修源**:改镜头 YAML(走正常编辑流 / 锁定字段走 `manju propose`)或定点 `manju redo <shot>`(换 seed/provider 重生)再 `manju select`。
3. `unknown` 的 id(uncertain / 未观察)→ **不是修复,是再判读**:重跑 `manju qc brief --shots <id>` 出题、按 `visual-qc-review` 重新逐条给 observed。
4. `stale`(判读过期,媒体/spec/expectation 已变)→ 同样重跑 `manju qc brief` 再判。

纪律:**永不直接编辑 expectations / packets / verdicts**——它们都是**派生**产物(源是镜头 YAML 的 `must_show`/`avoid`/`continuity.locks` + 所选 take 的字节);改源,派生自然重算。修复提案带 `do_not_execute_automatically`,**永不自动执行**——由人/你显式把它变成 `manju redo` 或补丁,验收态本身不花钱、不写源。

## 修复纪律(只增、可回滚)

- 所有修复**只增新 take / 新 final_vN**,带血缘(`_repaired` sidecar / failure 记录);**永不原地覆盖**(gen take、imports、final)。
- 每个 op 都可 `--dry-run` 先看计划再落。
- 想改的是**锁定字段** → 走 `manju propose`,永不 `unlock`。

## BEFORE / AFTER(业余 → 专业)

**例 1 — 看到 stale 就全量重生**
- BEFORE:QC 报几条,直接 `manju build --regen-stale` 一把梭,烧钱又推翻人的选择。
- AFTER:逐条按 repair_plan 定点修;`--auto` 只做安全项,花钱 redo 先 dry-run + 问。
- WHY:stale 仍可用、人的选择有效(§7);定点修才省钱、不误伤。

**例 2 — 用 ffmpeg 修变脸**
- BEFORE:对「主角变脸」跑 `--op croppad`,毫无作用。
- AFTER:变脸是生成缺陷 → `manju redo --seed N` 换 seed/provider 重生 + 选新 take。
- WHY:croppad/extend 只改时空,改不了画面内容;分清「时空修复」vs「重生」。

**例 3 — content_rejected 无脑重试**
- BEFORE:审核拒了就原样重试三次,继续被拒、继续烧钱。
- AFTER:`manju tasks` 看拒绝原因 → 改写 prompt 或走降级链。
- WHY:同一 prompt 重试还会被拒;要改因,或退到断网能出的本地能力。

## 拷贝进工作笔记的清单

```
修复闭环自检 · 逐条勾
[ ] 从 reports/qc.md + repair_plan.yaml 出发,逐项对照而非全量重生?
[ ] 每条发现选对 op(时空→extend/trim/retime/inout/croppad;配音→voice;画面缺陷→redo)?
[ ] auto 只做安全项,没让 auto 触发花钱?
[ ] 花钱/长耗时 redo 先 --dry-run + 命中 ask_before 问人?
[ ] 重做后 manju select 选新 take,原选择未被覆盖?
[ ] 修完 manju check → build → 再 qc 复核,通过才收工?
[ ] content_rejected 看了原因、改因而非无脑重试?
[ ] 没覆盖 final/imports/gen,没 unlock?
```

## 失败目录

| smell | 抓法 |
| --- | --- |
| 全量重生烧钱 | `--regen-stale` 滥用;改定点 `redo`/`repair --op` |
| op 选错(拿 ffmpeg 修画面) | 画面缺陷走 `redo`,不是 croppad/extend |
| auto 触发花钱 | `--auto` 只做 auto-safe;花钱必闸 |
| 覆盖已选 take/final | 只增语义;`manju select` 选新版,history 只增 |
| 审核拒无脑重试 | `manju tasks` 看原因 → 改 prompt / 降级 |

## Manju 落地

- **QC/修复命令**:`manju qc / repair [--auto] [--op retime|extend|trim|inout|croppad]`;`manju repair --op voice --shot <id> [--dry-run]`;`manju repair --op inout --in-ms A --out-ms B [--mode virtual|reencode]`;`manju redo S002 [--candidates N] [--provider X] [--seed N]`;`manju select S002 take_03`。
- **报告**:`reports/qc.json` / `qc.md` / `repair_plan.yaml`;`manju failures [-n]`(step/cause/evidence/hint/log)、`manju tasks`(拒绝原因)。
- **降级链**(§8):换 provider → 换 seed 重编 prompt → 图生视频 → 首尾帧 → 静帧推拉 → 文字卡。
- 判读严重度接 `visual-qc-review`;预防接 `prompt-craft`/`character-consistency`;音频修接 `audio-finishing`。

## 验收 eval

1. 给一份 repair_plan,是否逐项选对 op 且花钱项先 dry-run + 问?
2. 对一个「变脸」发现,是否走 redo 换 seed 而非 ffmpeg op?
3. 修完是否回跑 qc 复核、通过才收工?
