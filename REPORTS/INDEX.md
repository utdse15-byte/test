# REPORTS index

A navigation map only — every claim lives in the linked report, its
DECISIONS entry, and its commit. Nothing here is a fact source.

Closed-era reports (the whole `AI_IDE_*` family below) live under
`REPORTS/archive/` — moved 2026-07-17 (UX wave 2 item 13) so the active
reporting window stays visible; the era table keeps their record intact.

## Program eras

| Era | Reports | Record |
|---|---|---|
| AI_IDE_01–06 + P0 remediation | `AI_IDE_01..06_*`, `AI_IDE_01_06_P0_*` | DECISIONS #1–#20 |
| Production batch 07C–13C, 09/11G | `AI_IDE_07C/08_10_12C/09_11G/13C_*` | DECISIONS #21–#24 |
| Post-completion hardening | `POST_COMPLETION_HARDENING_*` | DECISIONS #25 |
| Final acceptance + agent track | `FINAL_ACCEPTANCE_*` | DECISIONS #26 |
| Capability rounds 14–21G | `AI_IDE_14..21G_*` | DECISIONS #27–#28 |
| Offline closeout (C1–C5; C6 skipped with evidence) | `AI_IDE_14_21_CLOSEOUT_*` | DECISIONS #29 |
| Studies / references | `COMPETITIVE*`, `UX-STUDY`, `MARKET-GAP`, `GOAL-COVERAGE`, `SYSTEM-ASSESSMENT`, `OPTIMIZATION-ASSESSMENT`, `REVIEW-01-RESPONSE`, `ROUND-*` | advisory only |

## FP function-perfection program (the FP_* family)

| Report | Loop | Landed |
|---|---|---|
| `FP_F0_CONTRACTS.md` | contract registry + loader + teeth | wave 1 |
| `FP_TIMEBASE.md` | rational timebase core (`core/timebase.py`) | wave 1 |
| `FP_PROFILE.md` | media-technical-profile/v1 | wave 2 |
| `FP_CONFORM.md` | conform-loss/v1 report | wave 2 |
| `FP_CONFORMANCE.md` | delivery conformance | wave 3 |
| `FP_RELINK.md` | hash-based relink | wave 3 |
| `FP_TOOLCHAIN.md` | toolchain manifest (record-only) | wave 3 |
| `FP_CAPTIONS.md` | caption accessibility roles | wave 4 |
| `FP_WORKFLOWS.md` | workflow help | wave 4 |
| `FP_FIXITY.md` | archive fixity manifest | wave 5 |
| `FP_RUNPERF.md` | run performance view | wave 5 |
| `FP_SECURITY.md`, `FP_SUPPORTBUNDLE.md` | earlier hardening loops | — |
| `FP_RATEMIG1/2/4/5.md` | R-track: edit_rate field → rational build spine → export truth → migrate tool (R3 honestly collapsed into R4) | R/S window, DECISIONS #30 |
| `FP_TTML.md` | S1 IMSC1/TTML writer | #30 |
| `FP_EDL.md` | S2 CMX3600 EDL writer | #30 |
| `FP_ARCHIVE_META.md` | S3 pack self-description | #30 |
| `FP_TOOLKEYS.md` | S4 toolchain→cache-keys opt-in | #30 |
| `FP_PLUGIN_API.md` | provider plugin API freeze (v1) | T/U window, DECISIONS #31 |
| `FP_BOARD_COMPARE.md` | T1 wipe/difference/frame-lock/boundary view | #31 |
| `FP_FCPXML.md` | T2 rational-native FCPXML writer | #31 |
| `FP_TTML_LOCALE.md` | U2 locale lang + declared direction | #31 |
| `FP_BAGIT.md` | U1 RFC 8493 serialized-bag pack | #31 |
| `FP_FCPXML_AUDIO.md` | V1 connected-audio lanes (+ conform truth-update) | V window, DECISIONS #32/#32a |
| `FP_BOARD_COMPARE2.md` | V2 onion skin + scopes (21G gate evolved, #32a) | #32/#32a |
| `FP_BOARD_TRANSPORT.md` | X1 K/L/J/arrows transport | W/X window, DECISIONS #33 |
| `FP_FCPXML_LOOPS.md` | W1 loop-bed materialization (render parity) | #33 |
| `FP_FCPXML_IMPORT.md` | W2 FCPXML import-plan | #33 |
| `FP_EDL_AUDIO.md` | Y1 declared A1/A2 subset | #33 |
| `FP_EDL_IMPORT.md` | Y4 EDL import-plan | #33 |
| `FP_DOCTOR_LOCALE.md` | Y2 locale + interchange probes | #33 |
| `FP_FCPXML_FADES.md` | Y3 DTD-sourced fades | #33 |
| `FP_COLORSTATS_WALL.md` | Z1 Pillow defect fix | #33 |
| `OPTIMIZATION_AUDIT_20260712.md` | the four-hour nine-dimension audit + 28-refuter panel | #33 |
| `FP_HASH_MEMO.md` | L4 process-scoped hash_file memo (double-hash collapse) | #34 |
| `FP_REVIEW_LAZY.md` | L3 /review subprocess-free render + lazy consistency boards | #34 |
| `FP_PROVIDER_ROBUST.md` | M1 provider robustness (orphan kill, poll-5xx, kinds, floor) | #35 |
| `FP_GUI_HARDENING.md` | M2 board CSP/one gate/single-eval state/lazy edit/JS dedup | #35 |
| `FP_TEST_SPEED.md` | M3 build-once fixtures + ultrafast fixture clips | #35 |
| `WINDOWS_WAVE_1_BASELINE.md` | W1 Windows hardening — audit + red evidence | #36 |
| `WINDOWS_WAVE_1_COMPLETION.md` | W1 Windows hardening — hard gate + file/process semantics | #36 |
| `WINDOWS_WAVE_2_BASELINE.md` | W2 installer + doctor — audit + red evidence | #37 |
| `WINDOWS_WAVE_2_COMPLETION.md` | W2 installer + doctor + the gate's first verdict | #37 |
| `WINDOWS_WAVE_3_BASELINE.md` | W3 NLE/fonts/board — audit + red evidence | #38 |
| `WINDOWS_WAVE_3_COMPLETION.md` | W3 + gate rounds — xmeml, conform losses, annotations; gate-green addendum | #38/#39 |
| `WINDOWS_WAVE_4_BASELINE.md` | W4 colour — audit + zscale empirical red | #40 |
| `WINDOWS_WAVE_4_COMPLETION.md` | W4 colour loop — tags, declared transform, HDR advisory | #40 |
| `WINDOWS_WAVE_5_BASELINE.md` | W5 close-out — audit + smuggle/LISTED≠VERIFIED evidence | #41 |
| `WINDOWS_WAVE_5_COMPLETION.md` | W5 archive hardening, hw-encode facts, bench; C2PA rejected | #41 |
| `UX_AUDIT_2026-07-13.md` | three-axis usability audit — ranked backlog + disposition | #42 |
| `CONV_AUDIT_2026-07-14.md` | 便利性计划合卷 — 两位审计员 + 逐条处置账 | #47-#48b |
| `GUI_POLISH_2026-07-14.md` | GUI 打磨波 — hidden-vs-display 缺陷类 + 手感/视觉/轮询处置账;round 2 外部评审处置 | #49/#49a |
| `GUI_DIRECTION_2026-07-14.md` | GUI 方向计划 — 个人生产操作台:审片队列/继续工作/交给 Claude/六组导航 处置账 | #50-#51 |
| `UX_WAVE_2_2026-07-17.md` | UX wave 2 — 通知/进度/did-you-mean/补全/文件夹/对比试听/--demo 样片/归档;deferred 项留档 | UX-WAVE-2 |
| `UX_WAVE_3_2026-07-20.md` | UX wave 3 日常闭环 — 外部方案逐条对账;草稿保护闭环 + 全局任务条;拒/留档处置 | UX-WAVE-3 |
| `AUDIT_LEDGER_WAVE_2026-07-24.md` | 外部审计总账 908 条逐条/抽样处置 — 真 bug 判据、十二种误报模式、P0 全修 + 付费安全闭环 | AUDIT-LEDGER-WAVE |
| `UX_REAL_USE_2026-07-25.md` | 真实使用波次(十九轮)— 用工具而非读代码;20 处白忙/看不懂/悄悄丢东西的修复(含 roundtrip 未知≠False、@显示名解析、三处 CJK 列宽、events 摘要、驾驶舱竖梯行)+ 换 ffmpeg 关掉 drawtext 环境缺口(22 条转绿)并因此挖出 color.tag_outputs 在 ffmpeg≥7.1 上少打两个轴的真 bug + 拆开 windows-ci.yml 逐步验证(装齐 extras 让 7 条从未跑过的测试转绿;真 pwsh 跑出闸门 ffmpeg 版本断言的前缀匹配洞)+ 用 PureWindowsPath / list2cmdline 在 Linux 上真跑「只有 Windows 能验」的两条不变量(并纠正自己写错的那句断言)+ pwsh 真跑 uninstall/rollback(证明纯文本检查挡不住删用户配置的脚本)+ Wine 证伪自己编造的 msvcrt 理由 + 体验波次:错误码契约文档化、漏斗三处「建议照敲即被拒」、CJK 列宽与中文命令表、roundtrip 无边车拒绝写入、出片后交付下一步、status 锚点报实事 + 渲染失败保留中间输入(_render_scratch 一个 owner,.manju/render-debug/<环节>/,嵌套不互相覆盖、取消不留、双上限且小文件优先、被丢掉的逐条具名;真 ffmpeg 实测)+ 自身流程错误留档 | UX-REAL-USE |
