# Optimization assessment — convenience, practicality, and what bites in the real world

## 1. Scope & method

SYSTEM-ASSESSMENT.md answered "does the system meet its own goals" and the answer
stands. This report asks a different question: **how does it hold up as a daily
tool, operated by a real human + real agent on real projects**, and where do
convenience/practicality gaps exist that the v2.2 design either deferred
(§1-⑦ GUI, ASR 后置) or never considered (process concurrency, schema
migration, browser codec reality). Method: every claim below was verified by
reading the cited file in this tree; where the design *names* a mechanism the
code does not implement, that is stated explicitly. Findings marked **[cheap]**
are localized fixes; **[structural]** need a design decision first.

## 2. Convenience/practicality gaps the design deferred or missed

### 2.1 The GUI (§1-⑦ deferred it; `manju gui` is now in flight)

§1-⑦ deferred the GUI indefinitely; the static board is read-only — every
action on it is copy-a-CLI-command (`board/board.py` renders literal
`manju select S002 take_03` snippets with a copy button). Fine for a
Claude-Code session; genuinely painful for the first-class human-manual mode —
pick-a-take, retry-a-shot, approve-a-build are all terminal round-trips. A
local web GUI as a **pure client of the engine** closes this without violating §0.

What `manju gui` MUST do:
- Call the **same core functions** the CLI/MCP call (`build.status.project_status`,
  `core.check.run_check`, `build.graph.run_build/redo_shot`, `build.explain.explain`,
  select via `update_shot_raw`) — no logic of its own, truth stays in files.
  The MCP registry (`mcp/tools.py: TOOL_DEFS`) already defines the safe write
  surface: status/explain/check/list_shots/get_shot/update_shot/select_take/
  build/redo/qc/export/events/board/propose. The GUI's POST surface should be
  exactly this set — one already-reviewed safety boundary, not a second one.
- Serve on 127.0.0.1 with stdlib only (`http.server`) — pyproject.toml has three
  runtime deps and the GUI must not add a fourth.
- Stay stateless per request: re-read files each time so an external edit
  (human in an editor, agent over MCP) is visible on refresh. No in-memory
  truth, no daemon that must be "synced".

What it must NOT do (matching the MCP decision, `mcp/tools.py` docstring:
unlock/gc/pack/import "intentionally ABSENT"; `cli.py:_interactive()` gates
unlock and `gc --hard` to a tty):
- No unlock, no `gc --hard`, no lock add/remove, no writes under
  `media/imports/`. A browser click is not the "human at a terminal" §5 demands
  — dangerous ops stay terminal-only.
- No direct file writes: every shot mutation goes through the same
  parse→schema→locks→atomic-write pipeline `update_shot` uses.
- No remote binding, ever (see failure mode #6 for the localhost threat model).

### 2.2 Long-running builds are synchronous and opaque

`build/graph.py: run_build` runs check → generate → voice → compile → render →
QC → export **inline in one process**; `cli.py: build` blocks until it returns.
No progress signal, no "which phase am I in", no cancel other than Ctrl-C
(which kills ffmpeg mid-write — see #2 below). Real renders take minutes;
cloud generation tens of minutes. Who hits it: everyone past toy size.
Partial mitigation today: phases append to `events.jsonl` as they complete,
so `manju events` in a second terminal is a crude tail. Fix: a phase/step
callback in `run_build` (stderr progress lines + `--json` event stream),
consumed by CLI and GUI alike; a cooperative cancel flag checked between
segments (`media/render.py` builds segments in a loop — a natural
checkpoint); and parallel segment normalization (segments are independent
ffmpeg runs — a thread pool here is a **[cheap]** multi-x speedup).

### 2.3 No process-level mutual exclusion (build lock is in flight)

Two `manju build` — the human-terminal + AI-session dual-actor scenario the
design celebrates (§10) — can run concurrently today. Nothing prevents it:
no flock/pid file anywhere in `src/` (grep: flock|lockfile|fcntl|msvcrt — zero
hits). Notably, design §3's directory listing *names* `.manju/` as holding
"任务队列、缓存索引、**进程锁**" — the process lock was in the layout comment
and never implemented. Value-hash locks (§5) protect content decisions, not
processes. Blast radius is detailed in failure mode #1. The planned
`.manju/build.lock` with pid + staleness detection is the right shape: pid
liveness check, atomic O_EXCL creation, lock only around mutating commands
(build/redo/select/voice/repair), read commands never blocked.

### 2.4 Browser/codec reality: the board shows black boxes

`core/container.py: MEDIA_EXTS` accepts `.mkv .webm .m4v .flac .m4a` etc. as
take media; `board/board.py: _render_take` embeds whatever it finds as
`<video src="media/gen/...">` unconditionally. A `.mkv` or `.flac` take renders
as a dead black rectangle with no error — the browser can't decode it and the
board says nothing. Who hits it: anyone registering toolbelt output or manual
takes not in H.264/MP4. Fix **[cheap]**: the normalized segment cache is
already "the lazy proxy" (§11) — prefer the segment (always H.264/yuv420p/AAC,
`media/normalize.py`) as the playable src when it exists, fall back to the QC
poster frame plus an honest "unplayable in browser: .mkv" label otherwise.

### 2.5 Error-at-a-distance: YAML typos surface only at `manju check`

The discipline is edit → `manju check` (README, SKILL.md §2). Agents follow it;
humans don't — a typo made in an editor at 14:00 surfaces as a build refusal at
14:40 with FIX-D's one-line finding. `run_check` is already fast, pure, and
side-effect-free (`core/check.py`), so check-on-save in the GUI (re-check on
every mutation POST and on a filesystem mtime poll) closes the loop at zero
engine cost. No file-watcher dependency needed: a 1–2s mtime poll over
`shots/ bible/ timeline/ project.yaml` is enough. **[cheap]** once the GUI
exists; the CLI keeps the explicit-check contract.

### 2.6 No undo surface for non-git users

Git IS the history (§1-②) and the scaffolded repo is real
(`container.py: Project.create` runs `git init`), but nothing exposes "what
changed since the last build / revert this shot" to someone who doesn't speak
git. `manju events` says who did what, not what to type to undo it. Fix: a
read-only `manju diff [shot]` (subprocess `git diff -- shots/S002.yaml`) and a
`revert-shot` that shells to `git checkout <rev> -- shots/S002.yaml` +
`manju check` — thin wrappers, no version engine of our own (§1-② stays
respected). The GUI surfaces these as "changes" and "restore" per shot.
**[structural]** only in UX wording — the mechanism is two subprocess calls.

### 2.7 `ask_before` is a convention, not a gate

`ProjectConfig.ask_before` exists (`core/models.py`) and status shows spend, but
grep shows the engine never reads it: the only consumers are SKILL.md and the
`cli.py` mini-playbook — i.e. agent discipline. The one hard gate is the budget
breaker in `run_build`, and it compares **this build's estimate** against
`budget.limit` (`build/graph.py`), not cumulative ledger spend + estimate —
weaker than §8.3's 事中"累计花费超限"熔断. Who hits it: anyone whose agent
forgets, and every GUI user (a click has no SKILL.md). Fix: (a) **[cheap]** add
ledger total to the breaker comparison; (b) engine-side enforcement for
non-interactive surfaces — when `ask_before` matches and the caller is MCP/GUI,
return `waiting_user` with the dry-run plan instead of proceeding; the GUI
renders it as an approval dialog. Terminal humans keep the current behavior.

### 2.8 Onboarding: hand-written manifests, reactive doctor

First contact with cloud generation is "hand-write
`~/.manju/providers/<id>/provider.yaml`, set a key env var" (§8.6,
`providers/manifest.py: providers_dir`). `manju doctor` validates a bad fill
before first spend (`validate_for_generic` — good) but is reactive: you learn
by failing it. Who hits it: every new user, on day one. Fix **[cheap]**:
`manju provider add <id> --type video` scaffolds a manifest with the ★ fields
as commented TODOs and runs the doctor probe immediately; `manju new` prints a
"no providers configured — offline fallbacks only (kenburns/caption_card)"
notice, which is currently only discoverable in doctor output.

### 2.9 Multi-project workflow: everything assumes cwd

`Project.find` walks cwd upward (`core/container.py`); there is no picker, no
recents, no "which projects exist". A CLI user lives with `cd`; a GUI cannot.
Fix: a client-side recents list (`~/.manju/recents.json`, appended by
`manju new`/`gui`) and a GUI project picker. Keep it out of the engine — the
engine's cwd contract is correct and testable. **[cheap]**.

### 2.10 Findings from reading the code (own findings)

1. **[cheap] `tail_events` reads the whole file per call** and `append_event`
   fsyncs per event (`core/events.py`). `project_status` calls it too, so every
   status/board render scans the full log. Fix: read backwards from EOF in
   blocks; batch fsync. Pairs with rotation (failure mode #9).
2. **[cheap] `manju import` never dedups by content** (`cli.py: import_`):
   re-importing the same clip mints `name_2.mp4`, a full copy — no
   content-hash check against existing imports, though `hash_file` exists
   (`core/hashing.py`). Real projects re-drop the same rushes constantly.
3. **[cheap] The board lies when stale, and pollutes git.** Nothing regenerates
   `board.html` except explicit `manju board`/MCP `board` (grep:
   `generate_board` call sites); after a build it silently shows the old
   state, with only a footer timestamp as a clue. And `board.html` is not in
   the scaffolded `.gitignore` (`container.py: GITIGNORE`) while its footer
   embeds a per-render timestamp — a guaranteed-noise derived file committed
   by default. Fix: regenerate at end of `run_build`; add to gitignore.
4. **[cheap] `manju pack` archives the rebuildable cache**: `PACK_EXCLUDE` is
   only `.manju/` and `.git/` (`cli.py`), so `.manjupkg` includes
   `renders/segments/` and `renders/proxy/` — GBs of content-addressed cache
   that `manju build` regenerates. Exclude both (finals stay in).
5. **[cheap] `status` counts ledger runs by fetching up to 100k rows**
   (`build/status.py`: `len(state.run_log(100000))`, commented "no COUNT
   API") instead of `SELECT COUNT(*)`. One-line fix in `runtime/state.py`.
6. **[structural] No media-hash cache.** `final_content_key`
   (`media/render.py`) calls `hash_file` on every selected take + every audio
   input; `hash_file` streams the entire file every time (`core/hashing.py`) —
   no (path, mtime, size)→sha256 index, although design §3 names a 缓存索引
   in `.manju/`. Every `explain`, every `board` header (it calls explain),
   and every build re-reads all selected media in full. See failure mode #4.
7. **[structural] No i18n strategy.** CLI human output is zh-only
   (`cli.py: status/build` print 项目/镜头/时间线/花费/下一步; SKILL.md is zh)
   while README and board labels are en. Deliberate for the current user, but
   there is no locale layer at all — the day a non-zh collaborator arrives,
   every human string needs touching. Decide: either declare zh-first policy
   in docs, or route human strings through one table now while it's ~50 strings.
8. **[cheap] `status`/board trust `finals[-1]` blindly**: `_latest_final`
   (`build/status.py`) globs `final_v*.mp4` and takes the last, without the
   `.key.json` sidecar check that `render.py: _latest_final_with_key` applies —
   a crash-truncated final (see #2 below) is reported as the project's 成片.

## 3. Real-world failure modes

1. **Concurrent writers.** Trigger: human runs `manju build` while an AI session
   builds/selects (the §10 scenario). Blast radius: `update_shot_raw`
   (`core/container.py`) is read-modify-write with no inter-process mutex —
   lost updates on shot YAML; `next_take_name`/`next_final_path` both compute
   max+1 then write, so two processes can mint the same `take_NN`/`final_vN`
   and `run_ffmpeg` passes `-y` (`media/ffmpeg.py`) — the "append-only"
   invariant is violated by overwrite; two compiles race on `timeline.json`.
   Atomic rename (`core/yamlio.py`) keeps individual files un-torn, but
   whole-file-swap is exactly the lost-update mechanism. Current mitigation:
   none (SQLite WAL covers only the ledger; events O_APPEND lines are ~atomic).
   Fix: the in-flight `.manju/build.lock` (pid + staleness) around mutating
   commands, plus O_EXCL reservation of take/final names. **P0**.
2. **Crash mid-build.** Trigger: Ctrl-C, OOM, power loss during ffmpeg. Text
   truth is safe (temp+rename discipline, `yamlio.py`), but **media is not**:
   `_build_segment` has ffmpeg encode **directly into the content-addressed
   cache path** (`media/render.py` → `normalize_segment(src, seg_path)`), and
   the next run's `if seg_path.exists(): return seg_path` treats the truncated
   file as a permanent cache hit — the corrupt segment is concatenated into
   every future final until someone deletes `renders/segments/`. A killed
   final render similarly leaves a partial `final_vN.mp4`; reuse is protected
   (no `.key.json` sidecar → no key match) but status/board surface it as
   latest (finding 2.10-8). With the build lock added, a crash also leaves a
   stale lock — pid-liveness detection must handle it. Fix: encode to
   `<dest>.tmp` + `os.replace` for segments AND finals; write the key sidecar
   only after rename. **P0** (silent corrupt output), fix is small.
3. **Disk-full during render.** Trigger: segments+proxy+finals on a small
   drive. Blast radius: ffmpeg exits nonzero mid-file → same partial-file
   poisoning as #2; `doctor` warns at <2 GB free (`cli.py: doctor`) but only
   when run, and `build` never pre-checks. Mitigation today: `manju gc`
   reclaims segments/proxy. Fix: atomic media writes (#2's fix covers
   correctness), plus a cheap pre-flight in `run_build` (duration × bitrate
   estimate vs free space → warning). **P1**.
4. **Huge projects (100+ shots).** Trigger: scale. `evaluate_all`
   (`build/stale.py`) re-parses every shot YAML + every take sidecar and stats
   every extension per take; `project_status` then calls `project.takes()`
   again per shot for cost; `board` runs `evaluate_all` AND `explain` (which
   runs it again) AND `load_shot` twice per card (`board/board.py`); and
   content keys full-read all selected media (finding 2.10-6). At 100 shots ×
   3 takes × 50 MB this is thousands of YAML parses and ~15 GB of hashing IO
   per board render. Mitigation: none. Fix: (path, mtime, size)→hash index in
   `.manju/state.sqlite` (it is exactly the rebuildable state §3 allows) +
   pass `statuses` down instead of re-evaluating. **P1** — it converts
   "instant" commands into minutes silently.
5. **CJK/Windows paths.** Largely engineered-for: pathlib + explicit UTF-8
   everywhere (`yamlio.py`), argv-list subprocess (`ffmpeg.py`), filtergraph
   escaping handles `C:` drive colons (`render.py: _escape_filter_path`), zh
   fixtures in tests (`tests/fixtures/make_sample.py` 雨夜便利店). Honest
   residuals: CI runs `ubuntu-latest` only (`.github/workflows/ci.yml`) so
   Windows is asserted, never exercised; CLI output uses ✓/⚠/★ which
   raise UnicodeEncodeError on a GBK-codepage console when piped
   (cp936 redirection); `os.replace` fails on Windows if the destination is
   open in an app that takes a write lock. **P2**, but add one Windows CI job
   before claiming §13 M0's "Windows 路径通过" in the README.
6. **Localhost GUI security.** Trigger: `manju gui` running while the user
   browses the web. A malicious page can hit 127.0.0.1 via DNS rebinding
   (Host header points at attacker domain) or blind CSRF POSTs — "select a
   take" is mild, "build" spends money via cloud providers. Mitigations the
   GUI must ship on day one: strict Host allowlist (`127.0.0.1:port` /
   `localhost:port`), a per-run random token required on every POST (embedded
   in the served page, checked server-side), path access only through
   `Project.resolve()` which already rejects root escapes
   (`core/container.py`), a restrictive CSP (`default-src 'self'`), and
   binding to 127.0.0.1 explicitly. No token → no mutation. **P1**
   (prospective — must land with R1, not after).
7. **Provider API drift / vendor death.** §14 already treats this as the top
   external risk and the adapter wall + dual-path drafts + manifest-driven
   generic adapter are real mitigations (DECISIONS.md 1–3). What rots quietly:
   pinned versions (`pyproject.toml`: `pyJianYingDraft==0.2.7`,
   `mcp-video==1.5.1`, `edge-tts==7.2.8`) — PROGRESS.md round M shows Edge TTS
   7.x already needed a live-found API adjustment. `doctor` probes presence,
   not behavioral compat. Fix: a tiny contract test per adapter behind
   `doctor --deep` (offline: import + one scripted-transport call), and record
   the verified app/library version in the export notes. **P2**, chronic.
8. **Schema migration across manju versions.** Reported honestly: there is
   **no `schema_version` field anywhere** — not in `ProjectConfig`, `ShotSpec`,
   `TakeSidecar`, or `Timeline` (`core/models.py`), and nothing stamps the
   writing manju version into `project.yaml`. `extra="allow"` means an older
   engine silently *ignores* fields a newer one wrote (e.g. a future shot
   field that affects rendering) — it will happily produce a wrong build
   rather than refuse. Trigger: any truth-file evolution + a user with two
   manju installs (laptop/desktop, human/agent environments). Fix: stamp
   `manju_version` (and a truth-format integer) at `Project.create`, have
   `check` warn on newer-format projects, and grow `manju migrate` when the
   first real format change lands. Cheap now, expensive after v0.2 ships.
   **P1**.
9. **events.jsonl unbounded growth.** Every command and every engine phase
   appends (`core/events.py`); nothing rotates — `gc` touches only
   segments/proxy/takes (`cli.py: gc`). Combined with whole-file tails
   (2.10-1) a months-old project pays a full-log scan per status. Fix: `gc`
   rotates to `events/archive-YYYYMM.jsonl` past a size threshold; tail reads
   backwards. **P2**.
10. **Git repos ballooning on accidental media commits.** The scaffolded
    `.gitignore` (`container.py: GITIGNORE`) covers `media/imports/`,
    `renders/`, `exports/`, `.manju/` and gen media for
    `.mp4/.mov/.png/.jpg/.wav/.mp3` — but `MEDIA_EXTS` also accepts
    `.mkv .webm .m4v .jpeg .m4a .flac`, which ARE committed if a take lands in
    one; `reports/frames/` (QC JPEG frames, regenerated every deep QC) is not
    ignored at all; `board.html` churns (2.10-3). `media/refs/` is also
    unignored — defensible (refs are small and deliberate truth-adjacent
    inputs) but a 50 MB `.flac` voice sample there rides in every clone
    forever. Fix **[cheap]**: add `reports/frames/`, `board.html`, and the
    missing gen extensions; have `check` warn when a tracked file under
    `media/` exceeds ~5 MB.

## 4. Prioritized roadmap

| Round | What | Effort | Payoff |
| --- | --- | --- | --- |
| R1 | `manju gui` core (in flight): pure client, MCP-equivalent surface, token+Host+CSP from day one (§2.1, #6) | M | Human-manual mode becomes usable without a terminal; review→decide loop in one place |
| R2 | `.manju/build.lock` (pid+staleness) + job visibility: phase callbacks, `--json` progress events, cooperative cancel (§2.3, §2.2, #1) | M | Dual-actor safety — the design's core scenario stops being a race; builds stop being opaque |
| R3 | Media write durability: tmp+rename for segments/finals, key-sidecar-gated "latest", stale-lock recovery (#2, #3, 2.10-8) | S | Kills the only silent-corruption path; crash/disk-full become clean retries |
| R4 | Board/GUI playability + freshness: segment-as-preview for non-MP4 takes, regenerate board post-build, check-on-save + mtime poll in GUI (§2.4, §2.5, 2.10-3) | S | Review surface stops lying and stops showing black boxes |
| R5 | Scale pass: media-hash index in state.sqlite, single `evaluate_all` per command, COUNT(*), backwards tail (#4, 2.10-1/-5/-6) | S | 100-shot projects keep sub-second status/board; unblocks GUI polling |
| R6 | Truth hygiene: `manju_version` stamp + format check (#8), .gitignore/pack fixes (#10, 2.10-4), import dedup (2.10-2) | S | Future-proofs truth files while the format is young; repos and archives stop bloating |
| R7 | Cost gate: cumulative-spend breaker, engine-enforced `ask_before` → `waiting_user` on MCP/GUI, approval dialog (§2.7) | M | Spend safety stops depending on agent discipline — required before real paid vendors |
| R8 | Onboarding + multi-project: `provider add` wizard, doctor-on-fill, recents/picker in GUI (§2.8, §2.9) | M | Day-one experience; second-project experience |
| R9 | Undo surface (`diff`/`revert-shot` git wrappers), events rotation, i18n decision, Windows CI job (§2.6, #9, 2.10-7, #5) | M/L | Long-tail polish; each is independently shippable |

Ordering rationale: R2/R3 before everything else that writes — a GUI
multiplies concurrent access, so the lock and durable media writes are its
prerequisites, not follow-ups. R5 before R7/R8 because the GUI polls status.
R6 is time-sensitive: migration stamps only help if added before formats diverge.

## 5. What NOT to build

- **In-engine LLM / prompt chains** — §0's thesis (智能在系统之外) is the
  system's identity and its testability. A convenience push tempts "one-click
  fix this shot" buttons that call a model; the correct shape is the GUI
  handing the task to the external director (copy a `manju auto`/`claude -p`
  invocation), never the engine calling one. COMPETITIVE-STUDY.md already
  rejected this deliberately; every studied tool that baked one in is welded
  to a single prompt chain.
- **Auto-publishing** — platform accounts, ToS, and takedown policy are a
  liability surface with zero build-system leverage; exports are the boundary
  (COMPETITIVE-STUDY.md). Still true when a GUI makes "upload" a tempting
  button.
- **Batch farm / queue of topics → N videos** — looped-director work; the AI
  director can loop `manju new`/`build` today. An in-engine farm imports
  scheduling, tenancy, and partial-failure semantics the build model doesn't
  need (COMPETITIVE-STUDY.md "batch mode" rejection stands).
- **A heavyweight editor-style GUI** — §1-⑦'s cost argument was against a
  desktop workbench, not against any UI. The line to hold: the GUI is a pure
  client of existing core calls; the moment it wants its own state, timeline
  scrubbing, or edit semantics, the answer is "export to JianYing/CapCut/OTIO
  — that's what the exporters are for."
- **A self-developed patch/versioning engine** for the undo surface — §1-②
  killed this once; `manju diff`/`revert-shot` must remain git subprocess
  wrappers, not a version subsystem.
- **A template marketplace/system** — decision 6 (§15) cut it; `bible/` +
  `rules.yaml` + a scaffold already carry the load, and "template" can return
  later as a pre-filled skeleton with no architectural debt.

---

Honest qualifiers: (1) `manju gui` and the build lock are in-flight in this
working tree (`src/manju/gui/server.py`, `src/manju/runtime/buildlock.py`) —
a read of both confirms they match the constraints in §2.1/§2.3 (token +
Host allowlist + CSP + dangerous-ops-absent; O_EXCL + pid staleness), but
they are assessed, not reviewed; (2) severity calls for #5/#7 assume the
current single-user, zh-locale, mostly-Linux reality — a Windows-first or
team deployment promotes both; (3) the O(n)-scan figures in #4 are
read-from-code IO accounting, not profiled wall-clock numbers.
