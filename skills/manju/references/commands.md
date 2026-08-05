# 命令速查表(核心协议 §11 的第三层)

> 从 `skills/manju/SKILL.md` 挪来(2026-08-01):正文只放**规矩**,查表按需读。
> 确切参数以 `manju <命令> -h` 为准;按任务找命令用 `manju help-workflow`;
> 全部 90+ 条命令见仓库的 `docs/CLI.md`。

| 命令 | 作用 |
| --- | --- |
| `manju new 名字 --vertical` | 新建竖屏项目(自动 git init) |
| `manju status [--json]` | 接管入口:阶段、缺口、下一步、累计花费 |
| `manju check` | schema + 引用 + 锁 校验(编辑后必跑) |
| `manju import <files…>` | 登记素材:真实影音进 `media/imports/`(转码代理/缩略图/波形);文本 `.txt/.md` 改道进 `story/imports/<名>.md`(可改编的原稿),两者同样只增不覆盖 |
| `manju appearances [--json]` | 出场表(只读):每个角色/场景/道具被哪些镜头引用(按序)+ 未引用的孤儿 + 镜头引用但 bible 缺失的条目 |
| `manju tasks [--json] [-n 20]` | 运行账本(只读):最近生成任务的 provider/镜头/状态(succeeded/failed/moderation-rejected)/花费/失败原因 + 在飞任务 + 按 provider 与项目合计的花费 |
| `manju build [--target proxy\|final\|exports\|qc] [--gen missing\|auto\|off] [--regen-stale] [--dry-run]` | 一键出片;`--dry-run` 先看清单和成本 |
| `manju redo S002 [--candidates N] [--provider X] [--seed N]` | 显式重做某镜头 |
| `manju select S002 take_03` | 选中某 take(或 `--file` 指人工素材) |
| `manju lock / unlock <shot> <field>` | 上锁 / 解锁(**unlock 你不能调**) |
| `manju qc / repair [--auto]` | 质检 / 修复(`--auto` 只做 auto-safe 项) |
| `manju export --jianying/--capcut/--srt/--ttml/--otio/--edl/--fcpxml/--xmeml/--pullsheet` | 从编译时间线导出:剪映/CapCut 草稿、SRT/TTML 字幕、OTIO、EDL、FCPXML、XMEML(Premiere/Resolve)、拉片表(§14 兜底出口 final.mp4/SRT/OTIO 永在) |
| `manju openclap/fcpxml/edl import-plan <file>` | 互换格式只读导入规划(plan-only,从不拷贝媒体、从不写项目、从不自动落轨);openclap 另有 `inspect`/`export`(.clap) |
| `manju locale add <lang> / status` | 多语言本地化叠层(WP4):本地化文本不进画面哈希,视频段共享,只有配音/字幕随语言变 |
| `manju board` | 生成静态 HTML 评审板 |
| `manju transcribe <media> [--from-srt/--text]` | 导入真人素材转录:云 ASR manifest 或人工输入 → SRT(M4 插件位) |
| `manju voice <shot>` | 为镜头重新配音(只增;最新的 voice_take 生效;stale 配音 build 只提示不重做;免费的 Edge TTS manifest 开箱即用,词级字幕自动跟随) |
| `manju pack [--bagit] / unpack` | 单文件归档往返(`.manjupkg`);`--bagit` 写 RFC 8493 序列化 BagIt 包(data/ 载荷 + sha256 清单,unpack/fixity 自动识别) |
| `manju migrate inspect/plan/apply/downgrade` | 有理数编辑帧率迁移(edit-rate migration):体检 / 计划 / 应用 / 降级 |
| `manju explain [--json]` | 只读解释:下次 build 会做什么、为什么(哈希证据) |
| `manju events` | 看协作日志 |
