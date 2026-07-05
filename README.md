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

# write shots (shots/S001.yaml …), reference Bible entries, then point a shot
# at an imported clip (registers it as a manual take):
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
| `manju new` | M0 | scaffold a project (auto `git init`) |
| `manju status [--json]` | M0 | takeover entry: stage, gaps, next step, spend |
| `manju check` | M0 | schema + referential + lock + secret validation |
| `manju import <files…>` | M0 | register into `imports/` (proxy/thumb/waveform) |
| `manju build [--target …] [--gen …] [--regen-stale] [--dry-run]` | M0 | the one-command build |
| `manju redo S002 [--candidates N] [--provider X] [--seed N]` | M0 | explicitly remake a shot |
| `manju select S002 take_03 [--file …]` | M0 | pick a take (or a manual clip) |
| `manju lock / unlock <shot> <field>` | M0 | value-hash locks (unlock is interactive-only) |
| `manju qc / repair [--auto]` | M0 | quality checks / repair (`--auto` = auto-safe only) |
| `manju export --jianying --srt --otio` | M0/M1 | JianYing draft / captions / OTIO |
| `manju board` | M0 | static HTML review board |
| `manju pack / unpack` | M0 | single-file archive round-trip (`.manjupkg`) |
| `manju events` | M0 | collaboration log |
| `manju doctor` | M0 | environment probes (ffmpeg/fonts/disk/project) |
| `manju gc [--hard]` | M0 | tiered cleanup (never touches imports/final; `--hard` is interactive-only) |
| `manju rebuild-index` | M0/M2 | rebuild `.manju/` runtime (incl. the run ledger) from text + media |
| `manju propose <title> --body …` | M2 | file a proposal — the agent's channel for locked-content changes |
| `manju transcribe <media> [--from-srt / --text]` | M4 | ASR slot: cloud manifest or manual input → SRT |
| `manju voice <shot>` | M3 | synthesize a new voice take (append-only; newest wins) |
| `manju explain [--json]` | M4 | why will the next build do what it will do (read-only) |
| `manju serve-mcp` | M2 | stdio MCP server for structured IO (no `unlock`/`gc` on this surface) |
| `manju auto "一句话"` | M2 | thin wrapper over `claude -p` (autopilot shell) |

Dangerous commands (`unlock`, `gc --hard`) are **not** exposed over MCP.

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
  `repair --auto`, title cards, `gc`, `doctor` (incl. provider-manifest and
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
