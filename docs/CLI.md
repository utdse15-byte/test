# CLI 命令参考(全表)

> **这张表 2026-08-01 从 README 搬到这里。** 它有 90 行、读者主要是接手的 AI
> 会话,而 README 是店主进门的第一页——两者混在一起,谁都读不痛快。
>
> 店主日常:六个命令看 [README 的「中文速览」](../README.md#中文速览店主日常);
> 某条命令怎么用 `manju <命令> -h`;按任务找命令 `manju help-workflow`。
> **本文件回答的是"到底有哪些命令"。**
>
> 本表由 `tests/test_fp_docs.py` 守着:每一行里的 `manju …` 都必须解析到活的
> typer 注册表——命令改了名而这里没跟,测试当场红。

`manju` is Typer-based; every command supports `--json`. The engine core is
frozen and unit-tested; the surface below lands per-milestone.

| Command | Status | Purpose |
| --- | --- | --- |
| `manju new [--preset <kit>]` | M0/P3 | scaffold a project (auto `git init`; a preset pre-fills the skeleton) |
| `manju presets [--json]` | P3 | list the 3 neutral preset kits (blank / vertical_ai_video / horizontal_ai_video) |
| `manju status [--json]` | M0/P6 | takeover entry: gaps, next step, spend, narrative stage, proxy-only/candidate/final-eligible counts and Picture Lock eligibility |
| `manju check` | M0 | schema + referential + lock + secret validation |
| `manju import <files…>` | M0 | register into `imports/` (proxy/thumb/waveform) |
| `manju build [--target proxy\|final\|exports\|qc\|audition\|animatic] [--gen …] [--regen-stale] [--dry-run]` | M0/WP2/P5 | the one-command build; `audition` = audio-first and `animatic` = deterministic keyframe/temp-audio preview (neither performs paid video generation); dry-run always reports production readiness |
| `manju production status [--json]` | P5/P6 | derive SceneContract/ShotContract, current+approved Animatic, Proof Shot, Proof Scene and bulk gates plus the one selected-media eligibility view (proxy-only/candidate/final-eligible); reports Picture Lock eligibility without saving a readiness truth file |
| `manju production approve-animatic PATH --reason …` | P5 | human-only exact-path/exact-byte Animatic approval in the existing verification log; `--yes` and unattended callers cannot approve |
| `manju production approve-proof-scene SCENE --reason …` | P5 | human-only approval of the current ordered proof-scene digest (takes, media hashes, trims, contracts, expectations and audio timing) |
| `manju redo S002 [--candidates N] [--provider X] [--seed N]` | M0 | explicitly remake a shot |
| `manju select S002 take_03 [--file …]` | M0 | pick a take (or a manual clip) |
| `manju lock / unlock <shot> <field>` | M0 | value-hash locks (unlock is interactive-only) |
| `manju qc / repair [--auto] [--op retime\|extend\|trim\|croppad]` | M0/Q | quality checks / repairs — auto-safe plan or a targeted clip op (new take, append-only) |
| `manju appearances [--json]` | Q | who/where map: characters/scenes/props → shots, plus orphans |
| `manju tasks [--json]` | Q/DR03C/DR06 | run-ledger view: jobs, statuses, failures/rejections, spend by provider; rows carry the `attempt_id` of the stage_attempt evidence they cross-reference; `--json` adds `unresolved_submissions` — in-flight/ambiguous paid submissions with `automatic_resubmit: false`, disposition, evidence-chain integrity and the attach/abandon recovery actions |
| `manju tasks attach-remote-job <submission_id> <remote_job_id> [--expected-state]` | DR06 | HONEST recovery for an UNKNOWN/DISPATCHING submission once you have confirmed the remote job ran: appends an ADMITTED evidence event under the SAME submission_id, updates the projection, then poll-only on the next build — never resubmits, never claims verified ownership (you asserted it) |
| `manju tasks abandon <submission_id> --reason … [--expected-state]` | DR06 | abandon an unresolved submission → ABANDONED_BY_USER, accepting duplicate-risk EXPLICITLY (records the reason + risk acceptance on the evidence chain); history preserved, a new submission_id is only minted by the next explicit redo/build |
| `manju tasks manifest <run_id> [--json]` | DR03C | re-materialize + print `reports/runs/<run_id>/run.json` — the derived RunManifest projected from the run's stage_attempt events (command/target/mode, stages, costs, failures, final outputs); deletable, never a build/resume/cache input |
| `manju export --jianying --capcut --srt --otio` | M0/M1 | JianYing / CapCut draft · captions · OTIO |
| `manju board [--serve] [--port]` | M0/P2 | review board: static HTML, or a live actionable workspace with --serve |
| `manju package [--json] [--force]` | M4 | cover + teaser cut from the current final (`exports/packaging/`) |
| `manju history [-n] [--json]` | P2 | merged change feed: events + git log, actor-attributed |
| `manju snapshot [label]` | P2 | labeled git checkpoint of the truth text |
| `manju rollback shot <id> \| file <path> [--to <ref>]` | P2 | scoped rollback; history only grows |
| `manju providers list/add/check/enable/disable/show` | S | provider manifests: scaffold, offline probes, toggles; secrets never printed |
| `manju providers catalog [--json] [--output …]` | DR04 | the shared capability projection (manju.provider-capability-projection/v1): every provider's capabilities/limits/refs/cost + stable profile digests — the same facts routing and the spend-free preflight consume; deterministic, no secrets, no credential presence, `--output` writes a derived report builds never read |
| `manju route list/explain [--json]` | S | routing strategies (timeline/routing.yaml): which provider fires for a shot and why |
| `manju redo --shots/--all-stale/--all-missing/--all [--yes]` | S | batch redo: one lock, one aggregated spend gate, per-shot isolation, loud skip reasons |
| `manju voice --missing/--all/--shots` | S | batch voice for missing dialogue lines (stale voices stay advisory §4.3) |
| `manju compare [a b] [--json] [--against-baseline [--candidate F]]` | S/07C | per-shot diff between finals: takes/captions/audio/packaging + why; `--against-baseline` diffs the current candidate against the approved release baseline through the SAME engine (additive comparison_mode/baseline/review_status fields) |
| `manju lib add/list/show/use/rm` | S | private cross-project asset library (~/.manju/library, content-addressed, tags) |
| `manju failures [-n] [--json]` | S | recent structured failures: step, cause, evidence, hint, log path |
| `manju repair --op inout --in-ms A --out-ms B [--mode virtual\|reencode]` | T | crop a clip's time range (virtual = keeps handles for real crossfades) |
| `manju frames <source> [--at ms \| --strip N]` | T | cached frame / scrub-strip previews (cover picker, GUI trimmer) |
| `manju assets [show <id>]` | U | asset matrix over the bible: 角色/场景/道具/配音/风格 with aliases, relations, appearances |
| `manju mentions [--check\|--apply]` | U | @角色/@场景 mentions in shot text: report or register into the spec (lock-respecting) |
| `manju prompt <shot> [--json] / --check` | U | prompt workbench bundle: 4 prompts + refs lineage + provider trace + cost + single-action checks |
| `manju refs shot <shot> [--json]` | U | reference resolution + per-provider budget allocation (selected/省略+impact) + cleanliness QC |
| `manju repair --op voice --shot <id> [--dry-run]` | U | voice repair loop: keep footage, regen TTS, realign cues (manual cues untouched), remix via keys |
| `manju board scene <id> [--grid 4\|9] / keyframes <shot> --n N [--scaffold]` | U | 4/9-panel storyboards; action → keyframe beats (writes only with --scaffold) |
| `manju build --mode quality\|balanced\|speed` | U | quality modes: provider bias + retries + bounded parallel generation (gates preserved) |
| `manju routing explain [<shot>] [--mode]` | U | per-shot: chosen provider, why (explicit/rule/tier/fallback), full order, est cost |
| `manju exports [--json] [--baseline] [--approve-baseline …] [--profile ID --manifest\|--bundle [--metadata F] [--output …]]` | U/07C/13C | 导出中心 status: 9 deliverables × 上新/待更新/缺失/有问题/待人工确认 + human verification log; `--json` now carries the additive `release_assessment` (blockers/ready/regression review/next safe actions from ToolPolicy); baseline approval is a human-only append-only event binding the final's exact bytes; `--manifest` derives the manju.delivery-manifest/v1 (variant/NLE/localization/credential-free platform-handoff facts — never a build input), `--bundle` packs exactly the manifest's files + SHA256SUMS into a byte-deterministic, path-safe, atomic zip (distinct from `manju pack`) |
| `manju director propose/confirm/run/suggest [--json]` | U | the six-step AI-director contract: propose → cost → confirm → execute → diff → next |
| `manju skills [show <id>] [--json]` | V | the bundled expertise library (index via `manju skills`): index cheap, full text on demand (project > user > bundled) |
| `manju create [brief\|synopsis\|beats] [--force]` | V/P5/P6 | narrative-neutral staged checklist: story and ending → SceneContract/ShotContract → Animatic/Proof → candidate build; build success never implies final-eligible or Picture Lock |
| `manju series new/new-episode/status/sync-bible/characters/split-script` | V | multi-episode umbrella: episodes are normal projects; global bible with conservative explicit sync |
| `manju qc brief / qc verdict --from-file` | V | agent-eyes visual QC: frames+context out, hash-bound [AI判读] findings back into run_qc |
| `manju ingest <dir> [--shot S001] [--role] [--apply]` | X | batch external round-trips: dry-run plan → takes/voice/refs by naming convention, deduped |
| `manju qc brief --mode consistency / qc coverage` | X | cross-shot consistency briefs (contact sheets, pair boards) + review coverage tracking |
| `manju tasks cancel/retry <id>` | X | retry failed ledger runs; cancel is GUI-native (running jobs cancel cooperatively, ffmpeg killed) |
| `manju import --on-duplicate skip\|import\|link` | X | import dedup against the private library with reuse suggestions |
| `manju pack / unpack` | M0 | single-file archive round-trip (`.manjupkg`); `pack --bagit` writes an RFC 8493 serialized bag instead (`data/` payload + BagIt manifests as the ONE fixity authority in that mode — deterministic bag-info, no Bagging-Date, stated in the restore note), auto-detected by `unpack`/`fixity` with the same remove-on-mismatch discipline; warns (never blocks) on Windows-unportable names and casefold collisions |
| `manju events` | M0 | collaboration log |
| `manju doctor` | M0 | environment probes (ffmpeg/fonts/disk/project)(Windows 行在 Windows 主机自动开启,或 --windows 强制) |
| `manju gc [--hard]` | M0 | tiered cleanup (never touches imports/final; `--hard` is interactive-only) |
| `manju rebuild-index` | M0/M2 | rebuild `.manju/` runtime (incl. the run ledger) from text + media |
| `manju propose <title> --body …` | M2 | file a proposal — the agent's channel for locked-content changes |
| `manju transcribe <media> [--from-srt / --text]` | M4 | ASR slot: cloud manifest or manual input → SRT |
| `manju voice <shot>` | M3 | synthesize a new voice take (append-only; newest wins) |
| `manju voice <shot> --preview [--text …]` | WP2 | 试听: disposable TTS sample under `.manju/webpreview/tts/` (never a take; cache-keyed) |
| `manju build --target audition` | WP2 | 先听后看: voice+captions+music on slate video; no picture generation; missing takes allowed |
| `manju explain [--json] [--cost] [--graph]` | M4/WP5/DR03B | why will the next build do what it will do (read-only); `--cost` adds est_cost totals; `--graph` appends the derived dependency diagnostics (manju.graph-diagnostics/v1: ready/blocked/failed ancestry, cycles, issues — a read-only view, never a scheduling truth source) |
| `manju impact <shot> [--field … --value …] [--json]` | WP1 | interconnection spine: what a dialogue/field edit stales, recompiles, costs |
| `manju align <shot> [--from-srt\|--asr]` | WP3 | align imported VO → `<take>.timing.json` (regenerable; free text-anchor default) |
| `manju align --media VO --shots S001-S012 [--apply]` | WP3 | multi-shot VO split plan/apply (manual takes + batch record) |
| `manju locale add\|status <lang>` | WP4 | multilingual overlay (lines.yaml + base_hash; picture pipeline shared) |
| `manju roundtrip <edited.json> [--apply]` | WP6 | flow JianYing skeleton / OTIO edits back as reviewable truth changes |
| `manju openclap inspect/export/import-plan` | DR01 | OpenClap (.clap) 互换适配器: read-only inspect (counts/diagnostics/locators), deterministic export from the compiled timeline (`--yes` honors the final_export gate), and a staged import-plan that writes nothing, downloads nothing and never auto-selects takes |
| `manju shot-package FILE [--apply]` | DR03A | controlled import of an external ShotDraftPackage (镜头提案包): default = zero-write inspect with a precise plan (mapped fields, omitted soft suggestions, unresolved bible refs, conflicts); `--apply` creates NEW shots + index entries only, CAS-guarded with rollback and a post-apply `check` — soft suggestions never become must_show/avoid/locks, existing shots are conflicts (`manju propose`) |
| `manju providers qualify/qualification`, `manju masters`, `manju analyze/segments/reframe/rough-cut/tool`, `manju series status --health`, `manju series outline`, `manju export --pullsheet`, `manju pull-sheet`, `build --target animatic` | 14–21G | provider qualification ladder + cost-bounded canary; real audio masters (raw stems / raw stem sum / M&E **bus-exclusion** master, loudness + true-peak measured) filling the delivery-manifest roles; hash-bound media analysis, cutdown proposals, smart reframe and the whitelist tool **resolver** (`manju tool` resolves + validates intent ops onto existing executors — it never invokes them); series world state, variants, season health and voice-rights-gated template packs; the preview spend ladder with keyframe gates and storyboard round-trip |
| `manju serve-mcp [--agent-profile collaborative\|unattended]` | M2/DR05 | stdio MCP server for structured IO (no `unlock`/`gc` on this surface); every tool carries a declared ToolPolicy and the `agent_surface` tool returns the machine-readable manifest+digest; `unattended` (opt-in, operator flag only) hides `redo`/`director_confirm` and answers them with a structured denial naming the collaborative path back — the default surface is byte-identical to before |
| `manju auto "一句话" [--agent …]` | M2 | autopilot shell over ANY one-shot agent CLI (claude/codex/gemini/qwen/aider or a custom template) |
| `manju qc tech MEDIA [--edit-fps N] [--write]` | FP | honest per-file technical profile (`manju.media-technical-profile/v1`): time/picture/color/audio/container facts **verbatim-or-`unknown`, never guessed**, with exact edit-grid drift diagnostics (rational NTSC rates via `core/timebase`) — a deletable derived projection, never a build input |
| `manju support-bundle [--out F] [--events-tail N]` | FP | redacted diagnostic bundle: default-deny collectors (env versions, project shape counts, redacted events/failures tails, provider-manifest byte digests — auth never read), a pre-write self-scan **refuses** to produce any bundle containing secret/path markers; deterministic zip |
| `manju qc conformance [--profile ID] [--write]` | FP | delivery conformance vs a declarative technical target riding `delivery_profiles`: 17 checks, each `PASS/FAIL/UNKNOWN/NOT_APPLICABLE` (UNKNOWN is honest — no profile/color/measurement means UNKNOWN, never a guessed PASS), **no aggregate score**, never a release input |
| `manju relink report\|plan\|apply [--root DIR]` | FP | missing-media recovery: zero-write report (takes/timeline sources/ref pins; hashes from recorded attempt evidence only), hash-first candidate plan under bounded roots, per-row CAS apply that restores bytes to the **recorded** path — truth files never rewritten, imports/ never a target, unverified rows opt-in only |
| `manju toolchain [--write] [--diff OLD]` | FP | record-only reproducibility evidence (`manju.toolchain-manifest/v1`): manju/python/OS/ffmpeg/dep versions + font content digests, facts-only digest, structured drift compare; no absolute paths/hostname/username ever; content-key wiring is a declared future step |
| `manju help-workflow [NAME]` | FP | task-oriented navigation: 10 real workflows (new-project → deliver, recover, diagnose …) whose every step is a command that exists (test-enforced against the live CLI registry) with an honest one-line why |
| `manju qc captions [--json\|--write]` | FP | caption accessibility advisories (`manju.caption-accessibility/v1`): CJK-aware CPS (UAX#11 wide=2), line length/count, duration floors, gaps/overlaps, forced-vs-nonforced, role coverage — **advisory only**, never blocks, always exits 0; optional cue `role` vocabulary flows into ASS Name + delivery rows, role-less projects stay byte-identical |
| `manju migrate inspect/plan/apply/downgrade` | R | the registry-declared `fps int → rational edit_rate` migration, now REAL: zero-write inspect (NTSC-neighbor candidates, never auto-chosen; honest cached-segment rebuild forecast), CAS plan/apply touching ONLY project.yaml (no auto-commit — the revert command is printed), downgrade refused without `--acknowledge-loss` (structured loss rows) |
| `manju export --ttml` / `--edl` | S | IMSC1 caption writer (roles via `ttm:*` + verbatim `x-manju:role`; positioning/RTL/vertical honestly out of scope) and CMX3600 EDL writer (FCM DROP/NON-DROP via `core/timebase`, zero-based source TC for generated media stated honestly, dissolve-family only — never a wrong dissolve); both conform-loss-classified |
| `manju export --fcpxml` | T | FCPXML 1.9 video spine — the one NLE exit where rational time rides natively (`frameDuration="1001/24000s"` exact, times never reduced, zero drift for int AND 1001-family projects); native cross-dissolves with FCP overlap geometry, everything else a cut + in-band note; captions ride `--srt`/`--ttml`, audio honestly deferred; conform-loss-classified. Sibling `manju export --xmeml`:W3 §5.2 的 Premiere/Resolve XMEML v4 时间线(timebase+ntsc 有理帧率、linked A/V、cross dissolve;语义损失见 conform-loss) |
| `manju fixity PACK [--info]` | S | archive verify without extracting (streamed, header-blind); packs now self-describe with fixity-covered `MANJU_RESTORE.txt` + engine-registry & toolchain snapshots (omitted honestly when unavailable — never fabricated), all dropped from verified restores |
| `manju gui [--workspace DIR] [--readonly] [--port] [--app]` | X | 浏览器工作台:与 CLI/MCP 同一引擎核,truth 仍在文本,危险操作(unlock/gc --hard)同样缺席;项目外自动给出工作区选择器;`--app` 以 Edge/Chrome 独立窗口打开(#50a)。GUI 能做什么、刻意不做什么见 `docs/WORKBENCH.md`(上手另见 `docs/GUI.md`) |
| `manju watch [--once] [--interval S]` | — | dev loop:truth 一变就重跑 `manju check`,只读、只 stat 文件,绝不与 build / `manju board --serve` 抢锁 |
| `manju spend [--json]` | — | 花费视图(§8.3 事后逐笔记账):按供应商/镜头/最近运行拆分 + 估算vs实际差额;读同一份可丢弃 run ledger,ledger 没了退回 take sidecar |
| `manju perf [RUN_ID] [--json]` | — | 运行性能报告(只读派生视图):每阶段耗时、缓存命中率、provider vs 本地耗时、成本合计、最慢阶段;无源数据的指标诚实标 unavailable,从不估算 |
| `manju evaluate [--json]` | AA | 技能/工作流的诚实用量评估:技能实际被读取次数、redo/repair 的镜头级返工热点、AI QC 判读分布;只读,从不编造生产率数字 |
| `manju schema [--out DIR]` | M0 | 导出每个 truth-file 模型的 JSON Schema(§4/§12) |
| `manju unpack ARCHIVE [--dest DIR]` | M0 | 还原一个 .manjupkg:默认恢复目录取归档文件名(经消毒),从不信任 zip 注释里的路径;损坏/半拷贝的包给一行干净提示 |
| `manju unlock SHOT FIELD` | M0 | 解锁一个字段:仅交互式终端 + 二次确认,永不经 MCP 暴露(§5);AI 请改写 proposal |
| `manju segments REPORT [--json]` | WP2 | 从媒体分析证据派生连贯的 A/V 段落(只读) |
| `manju rough-cut ALIGN [--json]` | WP5a | 只加注的口播粗剪提案:从不删除、可逆、从不碰 Timeline;基于词/说话人对齐证据 |
| `manju reframe REPORT --source WxH --target WxH` | WP4 | 把 ROI 轨编译成裁剪关键帧(只读):限速、安全区感知,多主体→needs_manual,不可行→留黑 |
| `manju ingest-batches [--json]` | AA | 列出已入库批次(新→旧),每批附评审状态计数 |
| `manju ingest-review BATCH [--json]` | AA | 查看一个入库批次的逐项评审状态(index/name/action/target/match/review/note) |
| `manju ingest-confirm BATCH [--item N] [--all-matched]` | AA | 确认一个/多个批次条目 —— 标记为人工已核实无误 |
| `manju ingest-flag BATCH [--item N]` | AA | 标记一个/多个批次条目为需人工再看(不改动任何已落地文件) |
| `manju ingest-discard BATCH [--item N]` | AA | 撤销一个/多个批次条目的评审状态;若曾自动选用 take 且此后未再选择,一并撤销该次自动选用(不删素材,§3) |
| `manju bridge plan/run/adopt` | 14–21 | 生成式转场 bridge(同一服务 + Provider 层准入门):plan 绑定两端帧+时长,run 经门执行成 CANDIDATE take,adopt 消费已评审 Assurance 产出零写入采纳提案 |
| `manju edl import-plan FILE [--target DIR]` | FP | CMX3600 EDL 互换适配器:只读 import-plan,只规划不落盘(从不拷贝媒体、从不写项目);写出口仍是 `manju export --edl` |
| `manju fcpxml import-plan FILE [--target DIR]` | FP | FCPXML 互换适配器:只读 import-plan,只规划不落盘;外部素材记为 needs_relink;写出口仍是 `manju export --fcpxml` |

**Rational edit rate (R-track)**: a project may declare `edit_rate: {num: 24000, den: 1001}` (fps stays the legacy int mirror; one truth, validated). Rational projects compile on an exact cumulative-boundary frame grid (total drift ≤½ms at any length — vs ~172 frames per 2h under naive per-clip rounding), render at native `-r 24000/1001` (verified on real output), carry distinct cache keys, and export integer frame values to OTIO/EDL (conform-loss drift reports `all_zero` **by construction**). Every int-fps project stays byte-identical everywhere — timelines, keys, renders, exports (pinned). Opt-in toolchain cache keys: `cache_toolchain_keys: [ffmpeg, fonts]` (only byte-affecting facts allowed; anything else is rejected at validation so irrelevant drift can never cause meaningless rebuilds). A Windows/macOS **informational** CI matrix (`xplat.yml`, allow-fail) watches the OS-portable layers; the ubuntu gate is unchanged.

Dangerous commands (`unlock`, `gc --hard`) are **not** exposed over MCP.

**Contract governance (FP)**: every public contract is registered in
`CONTRACTS.yaml` (40+ `manju.*/vN` schema rows + document rows, each with
owner/status/`read_older`/`write_older`) and enforced by tests — registry⇄code
consistency both directions, a frozen CLI-surface snapshot
(`tests/fixtures/cli_surface.json`, regenerate via
`python -m tests.test_fp_cli_snapshot`), an old-project compat corpus
(`tests/fixtures/compat/` — old shapes load byte-identically, torn files reject
structurally, unknown future majors refuse), and README command validation. The
first declared (not yet implemented) major migration is `project.fps int →
rational edit_rate`; `core/timebase.py` (exact rational rates, SMPTE NDF/DF
timecode, grid-drift analysis) is its landed foundation. Export honesty lives in
`manju.conform-loss/v1` (`exporters/conform.py`): per-target
preserved/approximated/dropped/unsupported tables (line-cited) + exact
one-frame-drift detection — no exporter degrades silently.
