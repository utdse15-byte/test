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
| `manju auto "一句话"` | planned | thin wrapper over `claude -p` (autopilot) |
| `manju serve-mcp` | planned | MCP server for structured IO |
| `manju doctor` | planned | environment probes (ffmpeg/fonts/disk/providers) |
| `manju gc` | planned | tiered cleanup (never touches imports/final) |
| `manju rebuild-index` | planned | rebuild `.manju/` runtime from text + media |

Dangerous commands (`unlock`, `gc --hard`) are **not** exposed over MCP.

## Milestones

Sliced by closed loop, not by calendar (see [DESIGN_v2.1.md](docs/DESIGN_v2.1.md) §13).

- **M0 — engine skeleton, no AI, no models.** ✅ **Core done & frozen.**
  Directory-as-project container, canonical hashing, value-hash locks, spec_hash
  staleness, the pure-function timeline compiler, `check`, events, and the
  12-shot regression fixture (`tests/fixtures/make_sample.py`) all implemented
  and unit-tested.
- **M1 — JianYing export.** 🚧 Partial: SRT/ASS style engine, OTIO, and a
  pyJianYingDraft exporter skeleton.
- **M2 — AI collaboration layer.** 🚧 Partial: `skills/manju/SKILL.md` playbook
  and `events.jsonl` are in place; full `--json` coverage and the MCP server
  follow.
- **M3 — cloud generation providers.** 🧱 Skeleton: the async provider base
  (submit/poll/download, resume-polling) is stubbed; cost guardrails, cloud TTS,
  the first cloud video adapter, and the local fallback providers
  (kenburns/caption_card) come next.
- **M4 — experience & cruise.** 📋 Planned: review board, `repair --auto`, title
  cards, `gc`, cloud ASR (on demand), full `doctor`.

## Design

The full design rationale — why directory-as-project beats a ZIP container, why
git *is* the patch engine, why the AI is fully external, the provider protocol,
cost guardrails, and the FFmpeg pipeline — is in
[docs/DESIGN_v2.1.md](docs/DESIGN_v2.1.md).
