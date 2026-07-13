# Manju One

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
files, a `--json` CLI, and an MCP server.

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
Collaboration surface               text files (truth) · CLI (--json) · MCP · events.jsonl
        │
Manju engine (deterministic)        build graph & cache · timeline compiler ·
                                    render pipeline · QC · exporters
        │
Provider / adapter layer            generation: cloud video/image/TTS, kenburns,
(all replaceable, out-of-process)   caption cards, manual import (also a provider)
                                    output: FFmpeg · pyJianYingDraft · OTIO · SRT/ASS
```

The creation/execution boundary is drawn deliberately: brief → outline → script
→ Bible → shot list is *creation* (the director's job); everything after the
shot list (generate, assemble, render, QC, export) is *execution* (the engine's
job). The build graph starts from "shots + media" and never invents shots.

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

## Quickstart

```bash
pip install -e .                      # Python 3.11+, pulls pydantic v2 / typer / PyYAML

manju new 雨夜便利店 --vertical         # scaffold a 1080×1920 project (auto git init)
cd 雨夜便利店.manju

# drop your own clips in — imports are read-only and always usable
manju import ~/clips/*.mp4

# write shots (shots/S001.yaml …) referencing Bible entries AND list them in
# shots/index.yaml (index order is the one order authority) — the guided
# version of this step is `manju help-workflow new-project`. THEN point a
# shot at an imported clip (registers it as a manual take; an unknown shot
# id fails with a clean pointer, never a stray take):
manju select S001 --file media/imports/opening.mp4

manju check                           # schema + references + locks + secret scan
manju build                           # compile timeline → render → QC → export
```

Every edit should be followed by `manju check`; a failing check blocks `build`.

## CLI reference

`manju` is Typer-based; every command supports `--json`. The engine core is
frozen and unit-tested; the surface below lands per-milestone.

| Command | Status | Purpose |
| --- | --- | --- |
| `manju new [--preset <kit>]` | M0/P3 | scaffold a project (auto `git init`; a preset pre-fills the skeleton) |
| `manju presets [--json]` | P3 | list the 3 neutral preset kits (blank / vertical_ai_video / horizontal_ai_video) |
| `manju status [--json]` | M0 | takeover entry: stage, gaps, next step, spend |
| `manju check` | M0 | schema + referential + lock + secret validation |
| `manju import <files…>` | M0 | register into `imports/` (proxy/thumb/waveform) |
| `manju build [--target proxy\|final\|exports\|qc\|audition] [--gen …] [--regen-stale] [--dry-run]` | M0/WP2 | the one-command build; `audition` = audio-first (no video gen) |
| `manju redo S002 [--candidates N] [--provider X] [--seed N]` | M0 | explicitly remake a shot |
| `manju select S002 take_03 [--file …]` | M0 | pick a take (or a manual clip) |
| `manju lock / unlock <shot> <field>` | M0 | value-hash locks (unlock is interactive-only) |
| `manju qc / repair [--auto] [--op retime\|extend\|trim\|croppad]` | M0/Q | quality checks / repairs — auto-safe plan or a targeted clip op (new take, append-only) |
| `manju appearances [--json]` | Q | who/where map: characters/scenes/props → shots, plus orphans |
| `manju tasks [--json]` | Q/DR03C/DR06 | run-ledger view: jobs, statuses, failures/rejections, spend by provider; rows carry the `attempt_id` of the stage_attempt evidence they cross-reference; `--json` adds `unresolved_submissions` — in-flight/ambiguous paid submissions with `automatic_resubmit: false`, disposition, evidence-chain integrity and the attach/abandon recovery actions |
| `manju tasks attach-remote-job <submission_id> <remote_job_id> [--expected-state]` | DR06 | HONEST recovery for an UNKNOWN/DISPATCHING submission once you have confirmed the remote job ran: appends an ADMITTED evidence event under the SAME submission_id, updates the projection, then poll-only on the next build — never resubmits, never claims verified ownership (you asserted it) |
| `manju tasks abandon <submission_id> --reason … [--expected-state]` | DR06 | abandon an unresolved submission → ABANDONED_BY_USER, accepting duplicate-risk EXPLICITLY (records the reason + risk acceptance on the evidence chain); history preserved, a new submission_id is only minted by the next explicit redo/build |
| `manju tasks manifest <run_id> [--json]` | DR03C | re-materialize + print `reports/runs/<run_id>/run.json` — the derived RunManifest projected from the run's stage_attempt events (command/target/mode, stages, costs, failures, final outputs); deletable, never a build/resume/cache input |
| `manju export --jianying --srt --otio` | M0/M1 | JianYing draft / captions / OTIO |
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
| `manju skills [show <id>] [--json]` | V | the 14-skill expertise library: index cheap, full text on demand (project > user > bundled) |
| `manju create [brief\|synopsis\|beats] [--force]` | V | the creation funnel: 立意→梗概→节拍→剧本→分镜→计划→生成 checklist + guided scaffolds |
| `manju series new/new-episode/status/sync-bible/characters/split-script` | V | multi-episode umbrella: episodes are normal projects; global bible with conservative explicit sync |
| `manju qc brief / qc verdict --from-file` | V | agent-eyes visual QC: frames+context out, hash-bound [AI判读] findings back into run_qc |
| `manju ingest <dir> [--shot S001] [--role] [--apply]` | X | batch external round-trips: dry-run plan → takes/voice/refs by naming convention, deduped |
| `manju qc brief --mode consistency / qc coverage` | X | cross-shot consistency briefs (contact sheets, pair boards) + review coverage tracking |
| `manju tasks cancel/retry <id>` | X | retry failed ledger runs; cancel is GUI-native (running jobs cancel cooperatively, ffmpeg killed) |
| `manju import --on-duplicate skip\|import\|link` | X | import dedup against the private library with reuse suggestions |
| `manju pack / unpack` | M0 | single-file archive round-trip (`.manjupkg`); `pack --bagit` writes an RFC 8493 serialized bag instead (`data/` payload + BagIt manifests as the ONE fixity authority in that mode — deterministic bag-info, no Bagging-Date, stated in the restore note), auto-detected by `unpack`/`fixity` with the same remove-on-mismatch discipline |
| `manju events` | M0 | collaboration log |
| `manju doctor` | M0 | environment probes (ffmpeg/fonts/disk/project) |
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
| `manju export --fcpxml` | T | FCPXML 1.9 video spine — the one NLE exit where rational time rides natively (`frameDuration="1001/24000s"` exact, times never reduced, zero drift for int AND 1001-family projects); native cross-dissolves with FCP overlap geometry, everything else a cut + in-band note; captions ride `--srt`/`--ttml`, audio honestly deferred; conform-loss-classified |
| `manju fixity PACK [--info]` | S | archive verify without extracting (streamed, header-blind); packs now self-describe with fixity-covered `MANJU_RESTORE.txt` + engine-registry & toolchain snapshots (omitted honestly when unavailable — never fabricated), all dropped from verified restores |

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

## Milestones

Sliced by closed loop, not by calendar (see [DESIGN_v2.1.md](docs/DESIGN_v2.1.md) §13).

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
- **M2 — AI collaboration layer.** ✅ Done: `skills/manju/SKILL.md` playbook,
  `events.jsonl`, full `--json` coverage, the stdio MCP server (`manju
  serve-mcp`, with `unlock`/`gc` absent by design and lock-violating edits
  rejected with rollback), `manju propose`, and the `manju auto` shell.
- **M3 — cloud generation + content QC.** ✅ Engine-side done: the
  config-driven **generic cloud adapter** (§8.6 — onboarding a REST API =
  filling the ★ fields of a `provider.yaml`, no code; dedicated adapter
  classes remain the escape hatch), provider manifests with engine-side
  throttling and dry-run pricing, resume-polling via the SQLite ledger (a
  restart re-polls, never resubmits), content-review rejection as a
  first-class failure, and the **machine-checked content QC chain** (§9):
  must_show assertion-ization via frame-sampled OCR, provenance-aware
  black/freeze probes, the mcp-video quality gate, and a `qc_vision`
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

The full design rationale — why directory-as-project beats a ZIP container, why
git *is* the patch engine, why the AI is fully external, the provider protocol,
cost guardrails, and the FFmpeg pipeline — is in
[docs/DESIGN_v2.2.md](docs/DESIGN_v2.2.md) (current; v2.1 kept for history).

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
- the dangerous surface (`unlock`, `gc`, `pack`) is NOT reachable from the
  browser, exactly like the MCP server; media paths are traversal-guarded;
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
is substituted as a single argument, never word-split. Agents that speak
MCP should use `manju serve-mcp` instead; `auto` exists for pure
prompt-in/work-out CLIs.

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
pip install -e ".[mcpvideo]"  # mcp-video — QC gate / media analysis / agent toolbelt
pip install -e ".[jianying,capcut,mcpvideo]"  # everything
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
