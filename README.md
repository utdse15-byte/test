# Manju One

> **Personal software, Windows-first.** 本仓库是店主个人使用的单用户软件;
> Windows 11 x64 是第一平台(硬门禁 `windows-ci.yml`),详见 `CLAUDE.md` 与
> DECISIONS #36-#38。

## 目录

- [中文速览(店主日常)](#中文速览店主日常)
- [Four goals, one model](#four-goals-one-model)
- [Architecture (layers)](#architecture-layers)
- [Project layout](#project-layout)
- [The three disciplines](#the-three-disciplines)
- [Windows 快速开始](#windows-快速开始)
- [系统体检样片:两条命令,零花费(`manju new --demo`)](#系统体检样片两条命令零花费manju-new---demo)
- [Quickstart](#quickstart)
- [CLI reference](#cli-reference)
- [Milestones](#milestones)
- [Design](#design)
- [Frame-grid rule and idempotent finals](#frame-grid-rule-and-idempotent-finals)
- [Audio policy (`rules.yaml → audio`)](#audio-policy-rulesyaml--audio)
- [Packaging kit (`timeline/packaging.yaml` + `manju package`)](#packaging-kit-timelinepackagingyaml--manju-package)
- [Preset kits (`manju new --preset`)](#preset-kits-manju-new---preset)
- [The actionable board (`manju board --serve`)](#the-actionable-board-manju-board---serve)
- [Autopilot for any agent (`manju auto`)](#autopilot-for-any-agent-manju-auto)
- [History, snapshots and rollback (P2)](#history-snapshots-and-rollback-p2)
- [Toolbelt (§2.5)](#toolbelt-25)

> 命令全表在 [docs/CLI.md](docs/CLI.md);GUI 见 [docs/GUI.md](docs/GUI.md) 与 [docs/WORKBENCH.md](docs/WORKBENCH.md);仓库当前状态见 `STATE.md`。

## 中文速览(店主日常)

店主日常六个命令,顺着走即可:

- `manju new` —— 新建项目(自动 git init)
- `manju import` —— 登记自己的素材(只读,永不改原件)
- `manju check` —— 校验 schema / 引用 / 锁 / 密钥,过不了不给 build
- `manju build` —— 一条命令:编译时间线 → 渲染 → 质检 → 导出
- `manju select` —— 给镜头定 take(或一段手动素材);支持简写:
  `manju select s1 2` = `manju select S001 take_02`(只匹配已存在的,歧义必报)
- `manju exports` —— 导出中心:九种交付物的状态一览

拿不准下一步就 `manju status`(当前阶段 + 每个镜头的待办,一句话一个动作),或 `manju help-workflow`(任务式工作流导航);偏好点击操作用 `manju gui`,审片用 `manju board --serve`,环境体检用 `manju doctor`。选错了不用慌:`manju rollback shot <镜头>` 撤回上一次选择(历史只增不减,永不覆盖)。详细工作流已内建 —— 例如 `manju help-workflow new-project`。

**隔了一阵子回来?** 先 `manju doctor`(尤其是 Windows 更新或动过 ffmpeg 之后),
再 `manju new 我的样片 --demo && cd 我的样片.manju && manju build --yes` —— 内置
系统体检样片零花费跑通执行链,确认环境没坏再动真项目(见下面「系统体检样片」)。手上项目
的进度看 `manju status`;仓库自身的状态与在办风险看 `STATE.md`。

**A deterministic build system for video.** Not an "editor with AI bolted on" — a
compiler. Think `make` / `ninja`: source files compile into derived artifacts,
staleness is decided by content hashes, unchanged work is cached, and every
build is reproducible.

```text
source files (text edited by humans and AI + imported media)
    ↓  build rules (deterministic, cacheable, incremental)
derived artifacts (generated assets, compiled timeline, rendered cuts,
                   exported drafts, QC reports)
```

Intelligence lives *outside* the system; determinism lives *inside* it. The
Manju engine calls no LLM — it only executes, renders, and validates. Claude
Code (or a human) is the director, collaborating with the engine through text
files, a `--json` CLI, and the local GUI.

## Four goals, one model

The build-system model solves all four core goals at once:

- **One-command build** — `manju build`: stale shots are recomputed, unchanged
  ones are skipped, failed ones fall back down a degradation chain.
- **Human takeover** — edit any source file or drop any clip into a directory;
  the next `manju build` picks it up. No special mode required.
- **AI takeover** — Claude Code edits the *same* files and runs the *same*
  commands. The build system does not care who the author is.
- **Graceful degradation** — the fallback chain is part of the build rules, so a
  single failed shot never sinks the whole cut.

## Architecture (layers)

```text
Intelligence (outside the system)   Claude Code = AI director / human = same identity
        │
Collaboration surface               text files (truth) · CLI (--json) · CLI/GUI · events.jsonl
        │
Manju engine (deterministic)        build graph & cache · timeline compiler ·
                                    render pipeline · QC · exporters
        │
Provider / adapter layer            generation: cloud video/image/TTS, kenburns,
(all replaceable, out-of-process)   caption cards, manual import (also a provider)
                                    output: FFmpeg · pyJianYingDraft · OTIO · SRT/ASS
```

The creation/execution boundary is drawn deliberately. Story and ending,
`SceneContract`, embedded `ShotContract`, asset/control choices, media review,
human approval and Picture Lock are *director decisions*. Prompt projection,
approved provider transport, append-only take registration, timeline assembly,
render, technical QC and export are *deterministic execution*. 引擎不替导演决定
场次为何存在、镜头如何结束、哪个 take 可接受或何时锁片；build graph 只从合同
与已登记媒体执行，不从 Prompt 或渲染结果反向发明作者事实。

Selected media uses one eligibility vocabulary everywhere:

- `proxy-only`: caption card/comic proxy，可做系统体检和预览，永远不能证明 Picture Lock。
- `candidate`: 已登记的真实候选，但当前性、人工批准或 assurance 仍未全部满足。
- `final-eligible`: 当前 selected video、非 proxy、人工批准且 assurance accepted，才有资格进入 Picture Lock 评审。

`build ok` 只说明本次执行成功；即使产生 `renders/final/final_vN.mp4`，也不会自动把
`proxy-only` 或 `candidate` 升级成 `final-eligible`，更不会自动完成 Picture Lock。

## Project layout

A project is a directory with a `.manju` suffix — mentally "one project, one
thing", physically an ordinary folder (native performance, crash-safe, git-friendly).

```text
雨夜便利店.manju/
├─ project.yaml          # frame size, fps, mode, ask_before, export profiles
├─ story/                # brief.md, outline.md, script.md
├─ bible/                # characters / scenes / props / style .yaml (consistency)
├─ shots/                # index.yaml (order + defaults) + one S001.yaml per shot
├─ media/
│  ├─ imports/           # human imports — read-only, engine never touches
│  ├─ refs/              # reference images, voice samples
│  └─ gen/S002/          # generated takes: take_01.mp4 + take_01.yaml (sidecar)…
├─ timeline/             # rules.yaml (assembly rules) + timeline.json (compiled)
├─ captions/             # captions.srt / .ass
├─ renders/              # segments/ (content-addressed cache) · proxy/ · final/
├─ exports/              # jianying/ · capcut/ · otio/
├─ reports/              # qc.json / qc.md · repair_plan.yaml · frames/
├─ proposals/            # where AI writes change requests for locked content
├─ events.jsonl          # collaboration log: who did what, when
├─ .manju/               # runtime: state.sqlite, queue, cache index (disposable)
└─ .git/                 # manju new inits it; all truth text is versioned
```

## The three disciplines

Everything else rests on these three invariants:

1. **Truth is text; media is append-only.** Every decision (including
   `selected_take: take_03`) is a line of YAML — a one-line, reviewable,
   revertible git diff. Media files are never overwritten: a redo produces
   `take_04`. Rolling back a shot = reverting a line of text.
2. **`imports/` is sacred.** There is *no code path* anywhere in the engine that
   deletes or rewrites a file under `media/imports/`. Not even `manju gc`. This
   is "AI can't delete originals" enforced at the hardware level, not by AI
   discipline.
3. **SQLite is disposable.** `.manju/` holds only rebuildable state (queue, run
   log, cache index). Delete the whole directory and `manju rebuild-index`
   reconstructs it from text + media. Backing up a project = copying the folder
   (or `manju pack`). There is no hidden state.

## Windows 快速开始

Windows 11 x64 是第一平台;全程 per-user、无需管理员。

1. **装 Python 3.11+** —— 从 python.org 下载 per-user 安装包,勾选 **Add Python
   to PATH**。
2. **拉仓库** —— `git clone` 本仓库到任意目录。
3. **装 Manju** —— `powershell -ExecutionPolicy Bypass -File scripts\windows\install-manju.ps1 -AddToPath`。
   per-user、免管理员、**先自检后原子切换**,失败绝不影响现有版本;装进
   `%LOCALAPPDATA%\Manju`,`-AddToPath` 只改**用户** PATH。更新用
   `update-manju.ps1`,回滚上一版用 `update-manju.ps1 -Rollback`,卸载用
   `uninstall-manju.ps1`(只删 App,绝不碰任何 `*.manju` 项目与 `~/.manju` 配置)。
4. **装 ffmpeg** —— `choco install ffmpeg --version=6.1.1`,必须与验证套件**完全同版**:
   ffmpeg.org 的 8.x 会偏移 ffprobe 的色彩标签,已被硬门禁实测。
5. **体检环境** —— `manju doctor --windows` 验证 ffmpeg/字体/磁盘/项目 + Windows 环境行
   (长路径策略、NTFS、网络盘、OneDrive、配置可写性、Edge/Chrome、安装模式)。
6. **偏好点击操作** —— `manju gui`,浏览器工作台(与 CLI 同一引擎核)。

## 系统体检样片:两条命令,零花费(`manju new --demo`)

**第一次用、或者隔了半年回来想确认环境还好** —— 别从空项目开始,先让内置
pipeline regression sample 跑通一遍。`--demo` 造一个 12 张文字卡组成的系统体检
样片(雨夜便利店),画面走本地 `caption_card`,**不联网、不花一分钱**,只需要 ffmpeg。

```bash
manju new 我的样片 --demo
cd 我的样片.manju
manju build --yes
```

关键输出会明确标记它的资格:

```text
created …/我的样片.manju  [system-check sample: 雨夜便利店 · 12 张 caption-card · proxy-only · 零花费]
  regression pipeline: cd 我的样片.manju && manju build --yes(…build ok 不等于 Picture Lock)
  下一步: cd 我的样片.manju && manju status(随时告诉你下一步)
…
QC: 通过
build ok
  查看构建产物 look: manju gui(成片页)· 抽帧 manju frames renders/final/final_v1.mp4
  媒体资格: proxy-only=12, candidate=0, final-eligible=0 · Picture Lock not eligible
```

体检渲染在 `renders/final/final_v1.mp4`(四核机器上约 2–3 分钟)。到这一步,
**确定性执行链已经在你的机器上跑通了**:编译时间线 → 渲染 → 技术质检 → 导出。
它的每镜仍是 `proxy-only`；这不证明 AI 叙事生产、真实表演、镜头连续性、人工内容
批准、`final-eligible` 或 Picture Lock。caption-card Demo 只证明系统 plumbing。

接着可以拿这个样片练手,每一步都不花钱:

- `manju status` —— 看当前阶段和每个镜头的待办(一句话一个动作)
- `manju gui` —— 浏览器工作台,成片页能直接看片
- 改 `shots/S003.yaml` 里的 `action.main`,再 `manju build` —— 观察它**只重做那一镜**
- `manju qc brief` —— 看质检怎么出题
- `manju exports` —— 九种交付物的状态一览

练完了删掉整个目录即可,不留痕迹。要开始真正的项目,往下看 Quickstart。

## Quickstart

```bash
pip install -e .                      # Python 3.11+, pulls pydantic v2 / typer / PyYAML

manju new 雨夜便利店 --vertical         # scaffold a 1080×1920 project (auto git init)
cd 雨夜便利店.manju

# drop your own clips in — imports are read-only and always usable.
# native exe 不做 glob/~ 展开:直接给具体路径(Windows 亦然)。
manju import clips/开场.mp4 clips/雨夜.mp4
# PowerShell 通配符:manju import (Get-Item ~\clips\*.mp4)

# write shots (shots/S001.yaml …) referencing Bible entries AND list them in
# shots/index.yaml (index order is the one order authority) — the guided
# version of this step is `manju help-workflow new-project`. THEN point a
# shot at an imported clip (registers it as a manual take; an unknown shot
# id fails with a clean pointer, never a stray take). `manju import` prints
# this exact line for you, with the real path:
manju select S001 --file media/imports/开场.mp4

manju check                           # schema + references + locks + secret scan
manju build                           # compile timeline → render → QC → export
```

Every edit should be followed by `manju check`; a failing check blocks `build`.

## CLI reference

`manju` is Typer-based; every command supports `--json`(失败时 stdout 仍是
JSON:`{"error": "人话,几乎总带补救命令", "code": "unknown_shot"}`)。

**完整命令表 → [docs/CLI.md](docs/CLI.md)**(90+ 条,含契约治理与有理帧率说明)。
日常只需要三个入口:

| 想干什么 | 敲什么 |
| --- | --- |
| 日常六步 | 见上面「[中文速览](#中文速览店主日常)」 |
| 某条命令怎么用 | `manju <命令> -h` |
| 按任务找命令(建项目→交付、抢救、体检…) | `manju help-workflow` |

危险命令(`unlock`、`gc --hard`)只在交互式终端里存在,GUI 上没有。

## Milestones

Sliced by closed loop, not by calendar (see [archived design](docs/archive/DESIGN_v2.2.md) §13).

- **M0 — engine skeleton, no AI, no models.** ✅ **Core done & frozen.**
  Directory-as-project container, canonical hashing, value-hash locks, spec_hash
  staleness, the pure-function timeline compiler, `check`, events, and the
  12-shot regression fixture (`tests/fixtures/make_sample.py`) all implemented
  and unit-tested.
- **M1 — draft exports (JianYing dual-path + international).** ✅ Structural:
  SRT/ASS style engine, OTIO, and the dual-path JianYing export (decision 8) —
  a native pyJianYingDraft draft as the primary, the diff-stable skeleton +
  lint as the secondary, capcut-cli lint behind an adapter wall, pycapcut for
  international CapCut. Opening in the pinned desktop apps is the remaining
  human verification step (§14).
- **M2 — AI collaboration layer.** ✅ Done: `skills/manju/SKILL.md` playbook, `events.jsonl`, full `--json` coverage, `manju propose`, and the `manju auto` one-shot shell. The current collaboration surface is files + CLI + local GUI.

- **M3 — cloud generation + content QC.** ✅ Engine-side done: the
  config-driven **generic cloud adapter** (§8.6 — onboarding a REST API =
  filling the ★ fields of a `provider.yaml`, no code; dedicated adapter
  classes remain the escape hatch), provider manifests with engine-side
  throttling and dry-run pricing, resume-polling via the SQLite ledger (a
  restart re-polls, never resubmits), content-review rejection as a
  first-class failure, and the **machine-checked content QC chain** (§9):
  must_show assertion-ization via frame-sampled OCR, provenance-aware
  black/freeze probes, black/freeze media probes, and a `qc_vision`
  manifest slot — all engine-driven, no agent required. What remains is
  literally a config file: fill `~/.manju/providers/<id>/provider.yaml`
  for a real vendor and set its key env var.
- **M4 — experience & cruise.** ✅ Engine-side done: review board,
  `repair --auto`, title cards, the intro/outro packaging kit + cover/teaser
  (`manju package`), `gc`, `doctor` (incl. provider-manifest and
  toolbelt probes), and the cloud ASR plugin slot (`manju transcribe`):
  an `asr`-type manifest on the same §8.6 config shape (`adapter:
  generic_asr`, sync and async forms) plus two manual on-ramps usable today
  — `--from-srt` (normalize your own subtitles) and `--text` (auto-timed
  transcript). A real ASR vendor is one manifest + one key env var away.

## Design

### Zero-budget video authoring handoff

`portable_video` and `minimax_h3` are offline authoring profiles over one
derived Canonical Video Authoring Plan. They do not appear in the Provider
catalog and cannot route, price, qualify, submit, poll, download, infer locally,
or generate media. Use `manju handoff profiles` and
`manju handoff create S001 --profile portable_video`; the existing
`manju prompt S001 --target minimax_h3 --bundle` command remains an alias.
Every returned file uses the existing verified manual `ingest`, QC, and
explicit `select` flow; see [docs/provider-handoff.md](docs/provider-handoff.md).

The full design rationale — why directory-as-project beats a ZIP container, why
git *is* the patch engine, why the AI is fully external, the provider protocol,
cost guardrails, and the FFmpeg pipeline — is in
[docs/archive/DESIGN_v2.2.md](docs/archive/DESIGN_v2.2.md)(历史设计,当前架构以 README/CLI/GUI 为准);
被它取代的 v2.1 只留历史,已归档到 [docs/archive/DESIGN_v2.1.md](docs/archive/DESIGN_v2.1.md)。

- [docs/PINS.md](docs/PINS.md) — index of the source-scanning pin tests (grep-style assertions a stray comment/docstring token trips) and how to evolve one honestly.
- [docs/PROJECT_YAML.md](docs/PROJECT_YAML.md) — 面向店主的 `project.yaml` 逐字段参考:每个 `ProjectConfig` 字段的类型 / 默认值 / 作用 / 后果。

## Frame-grid rule and idempotent finals

Every clip duration is snapped to the frame grid by the timeline compiler
(FIX-B): `frames = max(1, round(duration_ms * fps / 1000))`, then
`snapped_ms = max(1, round(frames * 1000 / fps))` — e.g. 1200ms @ 24fps is
28.8 frames → 29 frames → 1208ms. Non-frame-aligned durations cannot render
faithfully: encoders round each segment independently and the drift
accumulates across the concat. The final compositing chain also forces
`fps=<project fps>`, and QC asserts both `r_frame_rate == fps` and
`|final − timeline| ≤ 1 frame`.

Finals are idempotent (FIX-A): each `final_vN.mp4` carries a
`final_vN.key.json` sidecar recording its content key —
`hash(timeline JSON + ordered segment keys + ASS hash + audio input hashes +
encoding params + target)`. `manju build` skips the render when the key
matches the latest final; `manju build --force` re-renders regardless.

## Audio policy (`rules.yaml → audio`)

The project's default audio mix, compiled deterministically onto the timeline:

- **`voice_gain_db`** — level applied to every voice clip.
- **`sfx`** — one-shot effects. Each has a `source` (a human asset under
  `media/imports/`, never AI-touched), a `gain_db`, and an anchor
  `at` + `offset_ms`: `""` (absolute from t=0), `"shot:<id>"` /
  `"shot:<id>:start"`, or `"shot:<id>:end"`. An anchor naming an unknown
  shot is skipped deterministically and flagged by `manju qc`.
- **`transition_sound`** (+ `transition_gain_db`) — one hit at every interior
  cut (N segments → N−1 hits, packaging cards included).
- **`ambient`** — a looped room-tone bed under the whole film (`gain_db`,
  `fade_out_ms`, and `ducking` to sit under speech like BGM).

SFX play flat; music and ambient duck under the voice bus when
`ducking: true` — the sidechain is tunable per bed (`duck_threshold`,
`duck_ratio`, `duck_attack_ms`, `duck_release_ms`; defaults match the
long-standing constants). Every audio file's bytes are part of the final
content key — editing one re-renders, unchanged ones skip. `manju qc` adds
rule-based audio advisories (no BGM configured, ducking off under speech,
ambient bed suggestion for multi-shot films) as info items — suggestions,
never gates.

## Packaging kit (`timeline/packaging.yaml` + `manju package`)

`packaging.yaml` (scaffolded all-disabled by `manju new`) wraps the film:

- **intro / outro** — set `enabled: true` + `text`; each becomes a real
  leading/trailing timeline segment, so voice, captions, music and total
  duration shift naturally. The card MP4 is content-addressed at
  `media/generated/_packaging/{intro|outro}_<hash10>.mp4`: the same spec
  reuses the file, edited text mints a new one (append-only, like takes).
  `manju build` renders any missing card — HTML card when Chromium is
  present, drawtext floor otherwise (§8.4).
- **info_cards** — chapter/role/info overlays on the overlay track, placed
  with the same `at:` anchor grammar as SFX; unknown shots are skipped and
  QC warns.
- **cover / teaser** — `manju package` cuts them from the current final into
  `exports/packaging/`: `cover.png` (a frame at `frame_ms`, or a rendered
  card) at project resolution, and `teaser.mp4` sliced
  `[from_ms, +duration_ms]`. Both idempotent via `.key.json` sidecars;
  `--force` recuts. Needs a final — run `manju build` first. The window is
  validated against the final's real length (a past-the-end `from_ms` is a
  clean failure, an overrun is clamped with a warning), and `package` warns
  when the newest final is stale relative to the current specs, or when a
  frame-mode cover / teaser start lands inside an enabled intro card.

## Preset kits (`manju new --preset`)

A preset is *just a pre-filled project skeleton* that never binds the
project afterwards — everything it writes is plain, hand-editable
YAML/markdown. Exactly three kits ship (DECISIONS #7): `blank` (an explicit
"start from nothing"), `vertical_ai_video` 竖屏AI成片 (1080×1920@30) and
`horizontal_ai_video` 横屏AI成片 (1920×1080@24). A kit fixes only the FRAME
and generic technical defaults (safe-area captions, ducking, srt) — content
type (漫剧/短剧/广告/…) is deliberately NOT a preset: the AI infers it from
your input at creation time, following the playbook. A neutrality-guard test
keeps genre flavor from creeping back into kit data. `manju presets
[--json]` lists them; a landscape kit overrides the `--vertical` flag.

## The actionable board (`manju board --serve`)

The static `board.html` stays the zero-dependency default. `manju board
--serve` turns the same board into a live, clickable workspace on
localhost — a thin veneer over the same core functions the CLI calls:

- the page regenerates on every load (always-current state); takes play
  inline with real HTTP Range support (seekable video);
- click 选用 on any take (= `manju select`), 重做 a shot (= `manju redo`),
  回滚 a shot's selection (= `manju rollback shot`), and run 构建 / 质检 /
  打包 / 快照 from the header, with a busy overlay while a build runs;
- every click records the same event the CLI would (actor from
  `MANJU_ACTOR`, default human);
- per-take 评审批注:severity chips、click-to-seek(点批注跳到该时间码)、
  媒体一换就自动标 STALE;
- the dangerous surface (`unlock`, `gc`, `pack`) is NOT reachable from the
  browser, backed by the same local engine; media paths are traversal-guarded;
  binds 127.0.0.1 by default — a personal workspace, not a hosted product.

Round Q grows it into a workspace: a **take-comparison view** (side-by-side
takes with synced playback per shot), tabbed panels — 项目 project status,
字幕 subtitles (with manual-mode banner), 圣经 bible (locked fields marked),
日志 events log, 资产 imports, QC with suggestions — an **export button**
(`/api/export`, same core as `manju export`), and keyboard playback (space
toggles the focused video). Still stdlib-only, still localhost, still no
`unlock`/`gc`/`pack` from the browser.

```bash
manju board --serve            # http://127.0.0.1:8787, opens your browser
manju board --serve --port 9000 --no-open
```

## Autopilot for any agent (`manju auto`)

`manju auto "把这支片子做完"` hands the Manju playbook (SKILL.md) plus your
task to a one-shot agent CLI and logs everything it does as `actor=ai`.
The agent is resolved as: `--agent` flag → `MANJU_AGENT` env →
`project.yaml: agent:` → first of claude / codex / gemini / qwen / aider
found on PATH. A bare name uses that tool's documented one-shot form; a
template like `"myagent --task {prompt}"` runs anything else — the prompt
is substituted as a single argument, never word-split. Agents work through project files and ordinary CLI commands; `auto` remains an optional one-shot prompt-in/work-out shell.

## History, snapshots and rollback (P2)

Git is the patch engine (§3) — these commands give the records that already
exist a user-facing surface, and never invent a second version store:

- **`manju history`** — one merged feed: every `events.jsonl` action (who did
  what: human/ai/engine) interleaved with the project's git log (what changed
  on disk), oldest→newest.
- **`manju snapshot [label]`** — a labeled git checkpoint of the truth text; a
  clean tree is a no-op, not an error.
- **`manju rollback shot S002`** — re-select the previously selected take from
  the event record. Append-only: the newer take stays on disk for compare.
- **`manju rollback file timeline/rules.yaml [--to <sha>]`** — restore ONE
  truth-text file from git, guarded: media, renders and exports are refused
  outright; `check` runs afterwards; the rollback is itself an event, so
  history only ever grows.

## Toolbelt (§2.5)

P0 tools are pinned via three independent extras — install only what you use:

```bash
pip install -e ".[jianying]"   # pyJianYingDraft — JianYing draft primary path
pip install -e ".[capcut]"    # pycapcut — international CapCut drafts
pip install -e ".[jianying,capcut]"  # optional NLE adapters
```
Every tool sits behind an adapter wall — `manju doctor` probes each one, and
absence degrades to an alternative path instead of breaking a milestone.
Toolbelt products must be written back via `manju select <shot> --file` —
`manju check` flags unregistered media under `media/gen/` (write-back rule).

### Providers that work today, no vendor account

- **Edge TTS** (`pip install -e ".[edgetts]"`) — free keyless neural voices
  (dozens of zh-CN speakers). Manifest: `adapter:
  manju.providers.edge_tts:EdgeTtsProvider`, voice per character via bible
  `voice_id`. Streams word boundaries into `<take>.timing.json`, which the
  compiler uses for **word-timed captions** (punctuation re-aligned from the
  original dialogue).
- **Stock footage** — `manju.providers.stock:PexelsStockProvider` searches
  royalty-free footage per shot (query: params > scene `stock_query` > action
  line), orientation-matched to the project; needs a free `PEXELS_API_KEY`.
  Add capability `stock_footage` to a shot's fallback list to route it.
- **ComfyUI** (round Q) — `manju.providers.comfyui:ComfyUIProvider` drives a
  LOCAL ComfyUI: point `workflow_file` at your API-format workflow JSON, map
  shot fields into node inputs via `input_map` ("node_id.input_name" →
  `{prompt}/{width}/{seed}/…` templates), and manju submits `/prompt`, polls
  `/history`, downloads the first video/gif/image output as a take. Cost 0,
  full lineage, one-line "is ComfyUI running?" on connection failure.
- **Local command** (round Q) —
  `manju.providers.local_cmd:LocalCommandProvider` runs ANY binary/script as
  a generator: a shlex template with `{out}` (+ optional `{prompt}/{width}/
  {duration_s}/{seed}/{image}/…`), timeout-guarded, stderr surfaced on
  failure, output registered as a take with the argv in its lineage.

The P1 `html_render` slot (HyperFrames idea: HTML+CSS → deterministic MP4) is
implemented against headless Chromium (`CHROME_BIN` / PATH / Playwright
browsers dir): the caption-card provider prefers the styled HTML renderer and
falls back to zero-dependency drawtext; the renderer that ran is recorded in
the take's lineage. `manju import` generates thumbnails/waveforms into the
disposable `.manju/thumbs/` (§11); the normalized segment cache is the lazy
proxy. Captions support manual takeover like the timeline: set
`rules.captions.mode: manual` and `captions.srt` becomes human truth — the
burned ASS is recompiled from your cues and the compiler's version lands in
`captions.generated.srt` (§3).

### Writing your own provider — the frozen v1 plugin API

The provider surface is a **declared, frozen contract**
(`manju.provider-plugin-api/v1` in `CONTRACTS.yaml`, stable/additive-only;
teeth in `tests/test_fp_plugin_api.py`). A plugin written today keeps
working: the `Provider`/`CloudProvider` ABCs (`generate`;
`submit`/`poll`/`download`), `GenerationRequest`'s constructor shape,
`ProviderFailure(kind, message, detail=, disposition=)`, the registry entry
points (`register_provider`/`get_provider`/`fallback_chain`/
`generate_with_fallback`) and the `provider.yaml` adapter escape hatch
(`adapter: generic_cloud` or `module:Class`) are all pinned — renames,
removals or new abstract members fail the freeze tests. A broken or
builtin-shadowing manifest surfaces in `manju doctor`'s manifest errors and
never breaks the registry; the fallback chain always terminates at the
network-independent `caption_card`. There is deliberately **no plugin
marketplace or remote discovery** — plugins are local manifests or explicit
`register_provider()` calls, nothing else.

undefined
undefined
undefined
undefined
undefined
