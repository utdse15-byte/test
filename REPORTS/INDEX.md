# REPORTS index

A navigation map only — every claim lives in the linked report, its
DECISIONS entry, and its commit. Nothing here is a fact source.

## Active reports

| Report | Record |
|---|---|
| `PRODUCTION_READINESS_2026-08-05.md` | NARRATIVE-CLOSED-LOOP #10 |
| `SKILLS_S1_2026-08-05.md` | NARRATIVE-CLOSED-LOOP #11 |
| `WORKFLOW_HONESTY_2026-08-06.md` | NARRATIVE-CLOSED-LOOP #12 |
| `NARRATIVE_REHEARSAL_2026-08-06.md` | NARRATIVE-CLOSED-LOOP #13 |
| `STATIC_BUG_CLOSEOUT_2026-08-06.md` | NARRATIVE-CLOSED-LOOP #14 |

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
| `UX_REAL_USE_2026-07-25.md` | 真实使用波次(十九轮)— 用工具而非读代码;20 处白忙/看不懂/悄悄丢东西的修复(含 roundtrip 未知≠False、@显示名解析、三处 CJK 列宽、events 摘要、驾驶舱竖梯行)+ 换 ffmpeg 关掉 drawtext 环境缺口(22 条转绿)并因此挖出 color.tag_outputs 在 ffmpeg≥7.1 上少打两个轴的真 bug + 拆开 windows-ci.yml 逐步验证(装齐 extras 让 7 条从未跑过的测试转绿;真 pwsh 跑出闸门 ffmpeg 版本断言的前缀匹配洞)+ 用 PureWindowsPath / list2cmdline 在 Linux 上真跑「只有 Windows 能验」的两条不变量(并纠正自己写错的那句断言)+ pwsh 真跑 uninstall/rollback(证明纯文本检查挡不住删用户配置的脚本)+ Wine 证伪自己编造的 msvcrt 理由 + 体验波次:错误码契约文档化、漏斗三处「建议照敲即被拒」、CJK 列宽与中文命令表、roundtrip 无边车拒绝写入、出片后交付下一步、status 锚点报实事 + 渲染失败保留中间输入(_render_scratch 一个 owner,.manju/render-debug/<环节>/,嵌套不互相覆盖、取消不留、双上限且小文件优先、被丢掉的逐条具名;真 ffmpeg 实测)+ 靠保留下来的现场把 xfade 偶发失败定性(层字节 160 轮同一哈希、同样 argv 159 成 1 败 → ffmpeg 并发下的非确定性;未验 6.1.1 故「会不会打到店主」记 UNKNOWN,未加重试)+ 硬闸门抓出自己写的 guard 在量终端而不是量界面(Linux 绿≠Windows 绿)+ 把偶发失败的 UNKNOWN 变成数字(7.1 上 1600 轮 9 次 / 6.1.1 上 1600 轮 0 次,换回钉的版本后连跑三轮全量零失败 → 是环境不对版,不加重试,改的是 CLAUDE.md 那一行)+ 自身流程错误留档 | UX-REAL-USE |
| `TRISURFACE_TEST_2026-07-29.md` | 三面实测(CLI·MCP·GUI)— 只测不修的发现账:`final_export` 闸只拦 CLI(MCP/GUI 直通)、配音/手动登记不进 run ledger(rebuild-index 后账本 7→13 行)、「I=None LUFS」在共享导出中心引擎里未修、/create 页 webclient.js 双引 pageerror(pages_t 修过的同款)、redo 后 status 仍叫 redo 的死循环、镜头 schema 报错无形状提示、extra=allow 下错字段名零提示、「分镜页新建镜头」指错门、Edge TTS 无脚手架路径、--readonly 导出中心按钮未禁用、locale 给无台词镜头铸行、板面 `0.0 None` 等 24 条;MCP 面整体最扎实(锁/CAS/金钱闸/unattended 拒绝全对)+ 第二轮续测 6 条:**v2 视觉判读闭环每个消费端都断**(CLI 人类输出写后崩 KeyError 'levels';coverage/[AI判读] 走 legacy 读取器跳过 v2 → 判读完显示 never、findings 永不汇入)、交付 bundle 出界拒绝漏 pathlib 内脏、陈旧标签页守卫拦住但报 X-Manju-Token 行话、support-bundle 脱敏摘要 repr 直出、ingest 评审只回显序号、空计划弹窗无强制门;字幕接管/还原・repair・audition・roundtrip(带边车 apply)・损坏归档等 20+ 条路扫过干净 + 自身测具错误留档(管道吃 rc、git checkout 自毁锁前提、Playwright 默认驳回原生 confirm)。修复波 TRISURFACE-FIX 同日落地(v2 判读闭环三消费端接通、final_export 一个 owner 三面同契约、配音/手动登记进账本且 live≡rebuild 钉为行为、stale 建议指向已重做候选、None/双引/死路信息批量清理、check 教形状+近似键 advisory、httpx 入 dev);第三轮把七项遗留清到两项(事件摘要器一个 owner 供 events/history/support-bundle、ingest 参考图登记点名被过期镜头、Edge TTS 一条命令铸完整 keyless 清单、analyze/segments 拒绝语说人话、readonly 导出中心渲染即禁用、MCP 四个可分支专码+错误码技能 MCP 小词表、MCP export 九格式追平 CLI);第四轮清零:F-11 定案「无台词≠欠账」(not_needed 状态、零迁移、后补台词即回 missing、删台词遗译仍报过期)+ F-20 最小护栏(永久核验前原生 confirm,撤回语义仍留档);加练轮(店主注资):视觉 QC 闭环首次真跑(真读 21 帧、15 条 v2 判读、覆盖 7/7+8/8、两占位镜头被 assurance 如实拒收、卡片两字台词折行一条真发现)+ 三部完整片(剧集 bible 分歧诚实/双语 en+ja 全环/实拍 ingest+xfade+修复+teaser,零新缺陷)+ 崩溃安全战役(有理帧率/摘要行/locale 状态机属性测试 + 真 kill -9 注入逐条验 §3 纪律;首枪抓到 detail 换行破单行摘要,已修唯一属主);门禁揭示轮:Windows 硬门在两轮合并上抓出唯一红灯——`test_display_path` 对项目外路径断言了字面值而非 `resolve()` 形态(Windows 无盘符路径锚定当前盘),产品代码对、测试期望错,改断平台解析形态;同轮把 #15「无机制」的 wire 偶发超时钉死为载荷敏感(6 个 CPU 自旋确定性复现 3 红、撤掉全绿),两处 10s 死线提到 120s(检出延迟非断言,内容断言未动);卡片渲染波:视觉闭环那条「谢谢。折两行」正式收账——`.card` 的 max-width 百分比困在收缩包裹里(循环百分比:内在尺寸阶段被忽略、布局阶段再收紧),所有单行卡都断尾,上限移到 flex item 即愈;复现时逐行测量又抓出底部 87px 白带(该版 headless Chromium 布局视口比窗口矮 87px、视口外只有纯色 canvas 基色可达、渐变回退纯白),canvas 改铺数据声明的纯色 bg_edge、真渐变画在 body::before 全视口;7 条像素断言红-先行,preset 字节恒等钉原样通过,三消费口一属主同愈;门禁第二轮:PR #29 延迟 Windows 全量再抓 3 失败全数收账——`signal.SIGKILL` Windows 不存在(注入改 `Popen.kill()`,TerminateProcess 恰是任务管理器杀)、hypothesis 掷出 NEL 戳破状态机模型(YAML 1.1 行折叠改写 truth,夹具改从落盘重读铸 hash、模型补 #13 孤儿行规则,性质反而更强)、board e2e 的 httpx 默认 5s 死线包着服务端真 ffmpeg 抽帧(放宽 120s);Windows 卡渲染波:find_chromium 只认 Linux 命名——第一平台上 M3 首选的 HTML 渲染器从未跑过、每张卡静默落 drawtext 地板,而 doctor 隔一行报着 Edge ✓;唯一属主补 Windows 段(PATH chrome → Chrome 规范安装根 → find_edge 兜底,Edge 就是 Chromium)+ playwright chrome-win glob,F13 适配墙保证失败仍安全降级,4 条红-先行、POSIX 解析零扰动;门禁第三轮:探测器太诚实戳穿不完整模拟——「清空 PATH=无 Chromium」的伪造没清安装根,runner 真 Chrome 被如实找到,缺席世界补全三根即愈(断言未动);同一 run 里 chromium 测试道在 Windows 首次打开(skip 55→53,卡片像素断言 Windows 实跑全绿);卡片渲染波·二:四 preset 首次并排全渲暴露「涂满≠画对」——z-index:-1 的 ::before 渐变被 body 自身不透明 bg_edge 按绘制顺序埋掉、所有渐变卡实为平色(无白行测试与肉眼深色平检双双漏过,own-error 留档),bg_edge 改只挂 html 即浮出,新像素测试钉三家族色调行程;white_big flex-end 文字顶死右缘 14px,.stack/.wrap 加 4% 边距(居中 preset 不动,≥3% 内距入钉);预览缓存升级陷阱:cardprev 键只含模板/preset **名**、缓存不随升级失效——修复后老预览永远端旧观感,键改折入观感数据本身(模板 CSS 串+双侧 preset 行)自动重键;缓存键全族清点:cover_cache_key 同枪命中已修(card 模式折入模板 CSS、frame 模式键不变、exportstatus 共用公式自动跟随),waveform/kenburns/audition 同类无火不动,boards 早有 board_v1 令牌,intro/outro 资产地址被编译器消费明确不改(涟漪=重写全部既有时间线);门禁第四轮:chromium 道自己造出的新死线成员——#31 之后 `/api/card-preview` 在 windows-latest 请求内真启 Chrome,最慢 runner 击穿 10s socket 死线,`_req` 助手加 timeout 形参(默认不动)仅卡预览三处传 120,姐妹站点按同端点证据一并修;店主定界 2026-07-31:MCP 面冻结(「有 CLI 和 GUI 就可以了」,落 CLAUDE.md 常备事实 + #25,代码测试保留保绿、零新投入)、写满演习否决、四个真实使用战役核准(ffmpeg 误升级演习/供应商中途真死/规模现实性/中文路径+git 时间旅行);战役①落地(#26):7.1 上 PATH 时 doctor 曾满绿零警告 — 现在版本是必报事实(钉版✓/记录区间⚠/其余•,永不改退出码),7.x 劝告带实测数字,渲染失败现场对已知 acrossfade 签名当场点名回归并指路回钉(failures hint 同句,GUI 失败页同步受益),未知版本诚实不判;战役②落地(#28):真 HTTP 供应商中途暴毙全套编排 — 付费安全核心教科书级全过(OUTCOME_UNKNOWN 拦截/abandon 流/未决提交幸存 rebuild/账目分毫不差),修掉五处外壳(脚手架 {prompt} 教错占位符、全败批量 rc=0、配音失败不进 failures 存档(唯一编排器 record_voice_failure,DR06 拦截结构化排除)、单发 143 行栈墙→一行 provider_error 新码、check 把无效说成不存在),stale 混因留档带路径;战役③落地(#29):80 镜真长片逐环计时 — 健康面留数(status 9 行/redo 2.8s/首建 11m57s 诚实),三处剖面铁证病灶全修(read_yaml 3044 次→字节比较缓存于唯一属主、QC 抽帧 80 次→身份标记并关死自述的陈旧帧隐患、probe/astats 跨进程→ambient 作用域缓存仅 run_qc 启用),no-op build 35s→1.9s、/exports 页 10.8s→0.65s、警告墙 27→6 行带指路;战役④落地(#30):中文+空格路径全链零缺陷(xfade/中文手动 take/导出/pack 中文包名/异地中文目录 unpack/fixity),git 时间旅行纪律兑现(回滚即自洽、build 铸新版不覆写、逐 clip 身份 v4==提交世界为真、钉成 test_time_travel_discipline),钉出真 bug:短+纯静音片 loudnorm NaN 必败(2 秒 hello-world 建不出)— astats 实测 -inf 才旁路、疑问保留 loudnorm、长片零开销;自留过错:空对空比对的空洞 True 被钉的非空断言揭穿 | TRISURFACE-FIX |
| `SOFT_CAPABILITY_2026-07-31.md` | 软能力审计(文档/协议/CLI 惯例)—— 店主问「文档够好吗?吸收了市面经验吗?」。普查结论:域内标准吸收极深(OTIO 336、EBU R128 270、POSIX 117、无障碍 49、RFC 7807 移植成 CLI 错误信封),工程惯例几乎为零(SemVer/CHANGELOG/conventional commits 判定合理跳过;Diátaxis/NO_COLOR 该补;XDG 留档不动);成因同一:文档都写给已经知道答案的人。三条亲手复核的事实:①`manju new --demo` 是做好却零提及的教程(实测 2m19s 零花费出片)②README Quickstart 复制粘贴必失败(select 了从未 import 的文件,产品自己印的才是对的)③当天冻结的 MCP 没扫进技能面,`SKILL.md:57` 把一个 CLI 根本没有的步骤标为强制(本会话欠账)。另:决定索引只覆盖顶层 1–51,最近三个月 92 条不在册,且七段各自从 1 重编号 —— CLAUDE.md 的裸 #33(ffmpeg 钉的依据)指向了无关条目。三波落地:README 教程节+回归路径、Quickstart 修正、92 行命名段索引+编号域规则、技能面 MCP 清扫(CLI 原生接管仪式,附字段锁实测)、CLAUDE.md 四条指路、`-h`、NO_COLOR、构建进度(stderr 且仅交互,--json/管道字节不变)。22 条红-先行测试;自留过错四条(错误的 NO_COLOR 断言、无效锁实测、管道又吃退出码、自我满足的钉)| SOFT-CAP |
| `REWORK_FREEDOM_2026-07-31.md` | 返工自由度审计 —— 店主目标「实际使用时 CLI/GUI 要有极大自由,因为会不断调整返工」。真项目(--demo 12 镜)打完 A–F 六组反悔动作:**大部分自由已经在**(take 来回切、rollback 说清退回了什么、手动接管后能换回生成 take、删镜头 check 给两条出路、中间插镜头只重生成那一个且播放序按 index 不按编号、fps 24→30 与竖转横实测生效且旧素材加黑边不拉伸、compare 连画幅变化都点名、Ctrl-C 实测 rc=130 无栈锁已释放 —— 此前审计说的抛栈三点位均未复现,按实测纠正)。两处真缺口已修:①可行动建议在所有面上不可见(改画幅后 QC 逐镜给了确切修复命令,却因是 info 级被 errors/warnings 两个数字盖住;实测 29 条 info 里 16 条带命令全部静默 → 新增唯一计数器 actionable_notes,判据 info+非空 suggestion,qc/status 同步显示)②Ctrl-C 后零输出(店主不知道已产出的还在不在 → 唯一属主 _interrupted_message,build/redo/voice 三处接住,走 stderr、rc 仍 130);顺带 --approve-baseline 补一句「再审批即以最新为准」。一处留档待拍板:GUI 无法把镜头移出成片(permute_index 的排列不变量正是两标签页安全守卫,放宽等于弱化保护,应另起子集写入器且语义须店主定)—— 已如实写进 WORKBENCH 矩阵。自留过错两条(GUI 探测方法错、缺陷经查证后缩小);**2026-08-01 补齐移出本刀**(店主授权定语义):移出≠删除(镜头文件永留盘、index order 就是这一刀、随时放回,没有单向门),新写入器 set_cut_order 独立守卫、permute_index 排列不变量原样保留、GUI 子集必须带 CAS 令牌;CLI 得 `manju cut` / `cut drop` / `cut restore`(简写、点名拒绝、重复移出是 no-op),剪辑台得 ✕移出 + 「不在本刀里」放回托盘;连带修掉真 bug:剪辑台画 shot_ids() 导致点一下 ▲ 就把刚移出的镜头静默塞回成片;真浏览器验收(点通放回闭环、零 pageerror、按钮折行 38px→21px);15 条红-先行 + 一条既有钉从查措辞改为查行为(加强)| REWORK-FREEDOM · CUT-MEMBERSHIP |
| `DOCS_CLOSEOUT_2026-08-01.md` | 文档收口波 —— 把《软能力审计》§六「明知而未做」清单关掉。①README 的读者被两拨人抢:90 行命令表(真实读者是接手的 AI)挡在店主进门第一页前 —— 表整体搬进 `docs/CLI.md`,README 加目录(每个 `##` 都在、锚点与测试共用同一 slug 函数按构造一致、置于第 7 行),正文 48.3k→27.4k 字符;`test_fp_docs` 的扫描面跟着内容走(README ∪ docs/CLI.md,断言未动、输入变宽,搬家中间态实测红两条证明钉是活的)②DESIGN_v2.1 与 v2.2 82% 重复且无取代标记(按文件名先读到的是过时那份)→ 归档到`docs/archive/` 并写明别拿它改代码,活文档不得再指旧路径 ③**唯一一条文档在骗人**:`LAST_GREEN.yaml` 自称 written by CI,而 `.github/workflows/` 里 `update_last_green` 出现 0 次 —— 两个 pending 不是「还没绿」是「没人盖章」,读者会误以为门禁从没双绿过;修法是把话说对(维护者手动跑,个人软件不为一份信息性文件接跨 workflow 回写)+ 用真数据首次盖章:efd7783 在 windows run 30704812522 与 ubuntu run 30704812542 双绿、ffmpeg 钉由同 run 的 anti-silent-skip 步骤作证、合并后 c7889b8 树字节相同(aa87368),test_count 没读到就留 null(看起来合理的数字比空白更坏);新钉双向 —— success 必须带得出 40 位 SHA + run id,文件若再自称 CI 盖章 workflow 里就必须真有那一步,另补「派生报告永不是构建输入」④技能面:内容契约作用域从 taxonomy(13)扩到盘上全部 19 个技能(那 5 个此前只被「frontmatter 能解析吗」两条覆盖,实测本来就合规——但在此之前没人知道);19 个技能 0 个写「什么时候不该用」→ 18 个可选技能各补一节且**必须指出去处**(另一技能 id 或真命令),核心协议因 auto 全量注入豁免、豁免由正面钉守着;渐进式披露第三层全仓零实现而核心协议正卡在298/300 行 → 两张查表进 `skills/manju/references/`(正文 298→282 行,走的是「查表」留的是「规矩」;搬家代价被既有钉当场抓住 —— 只读注入面的 agent 必须知道 --xmeml/--ttml/--bagit/import-plan 等出口**存在**,没有放宽它去扫 references(那会让钉子变空),而是把「存在性」留在注入面、「用法」沉到第三层),4 个孤儿技能进 §0 索引,新钉检查被指向的 reference 文件真的存在。16 条红-先行(首轮 6 红 + 技能轮 37 红);仍不做的一条:XDG/%APPDATA% 迁移(风险大于收益,维持 SOFT-CAP 原判);自留过错:抖动猎杀被我自己在同一棵树上的编辑污染(第 1 轮干净 5961 passed 有效,第 2 轮起作废)—— 冻结的树才是它的前提,改由本波合并后重开 | DOCS-CLOSEOUT |
