# Manju — Interconnection & Trust: Implementation Guide

> Audience: an AI coding agent working in an IDE on this repository.
> This document is self-contained. Follow it top to bottom. Every claim about
> the existing code has been verified against the source at the commit this
> guide was written (post round AA, 2090 tests green). File references are
> `path:line` anchors — verify each anchor with a targeted read before editing,
> line numbers may have drifted slightly.

---

## 0. What you are building and why

Manju is a **deterministic build system for video** (think `make`/`ninja`):
truth is text files, staleness is decided by content hashes, media is
append-only, and the engine calls no LLM. Read `README.md` and
`docs/DESIGN_v2.2.md` §0–§6 before anything else.

Two product outcomes drive everything in this guide:

1. **One interconnected system.** Script text, voiceover, subtitles, and
   the visual timeline must not feel like four isolated subsystems. When a
   user changes one line of dialogue, the system must tell them exactly
   which voice take goes stale, which subtitle cues move, which shots are
   affected, whether the timeline recompiles and the final re-renders, and
   what it would cost to catch everything up. This includes imported real
   human voiceover (align it to the script) and multilingual versions
   (subtitles and voiceover keep the same structure across languages).

2. **Trust before spend.** AI video makes users anxious because clicking a
   button has unclear consequences. Before every generation the user must
   see: roughly how much it costs, which shots are affected, why
   regeneration is needed, what will be reused from cache, and — after a
   failure — what their options are. Manju's hash-based architecture
   already computes most of this; the work is surfacing it as one uniform
   contract, plus one missing entry point: **preview the audio first, then
   generate the full video.**

A third strategic item: Manju exports OTIO / JianYing / CapCut drafts, but
the high-value capability is not export — it is letting **diffs from
external editing flow back** into the system as reviewable truth-text
changes.

The work is organized as seven work packages (WP1–WP7, §4). WP1 is the
spine; do it first.

---

## 1. Non-negotiable ground rules

These are standing invariants of this codebase. Violating any of them is a
defect regardless of whether tests pass. They come from `DESIGN_v2.2.md`
§3–§5, `DECISIONS.md`, and round-W/AA conventions.

1. **Truth is text; media is append-only.** Every decision is a line of
   YAML. Never overwrite a media file; a redo produces a new take. There is
   no code path that deletes or rewrites anything under `media/imports/` —
   do not create one.
2. **The engine calls no LLM.** All new engine logic must be deterministic.
   Anything needing judgment is packaged as briefs for an external agent
   (see `qc/agent_review.py` for the pattern).
3. **§4.3 conservatism ("人机互不践踏").** Spec changes mark things STALE
   and *advise*; they never auto-regenerate or auto-invalidate a human's
   selection. Manual takes (`spec_hash == "manual"`, sidecar-less voice
   drops) are never invalidated. New staleness you introduce must follow
   the same rule: flag, never act.
4. **No mass restage.** Changing what goes into `spec_hash`/`voice_hash`
   requires the versioned-formula pattern: bump `SPEC_VERSION`/
   `VOICE_VERSION` in `src/manju/core/spec.py:39-40` and keep old takes
   judged by the version recorded in their sidecar, forever.
5. **Byte-identity when off.** Every new feature that touches the timeline
   fingerprint, content keys, or scaffolds must leave existing projects
   byte-identical when the feature is unused (precedent: `refbudget.py:21`,
   transition overrides). If a new model field unavoidably moves the
   timeline fingerprint once, document it in `PROGRESS.md` exactly like the
   round-O note ("changes timeline fingerprints once on first recompile").
6. **Spend gates.** Any new code path that can spend money must go through
   the §8.3 pattern: dry-run estimate available before, `spend_gate`
   (`src/manju/build/graph.py:67`) / `WaitingUser` unless `--yes`/
   `assume_yes`, per-run ledger row after (`runtime/state.py:record_run`).
7. **Dangerous surface containment.** `unlock`, `gc --hard`, `pack`,
   arbitrary-path import are absent from MCP and the GUI/board HTTP
   surfaces. Keep them absent. New GUI POST handlers follow round-Z lock
   ordering: in-process mutex outer → `build_lock` inner; mutations go
   through the checked write paths in `core/writes.py`.
8. **Structured errors and events.** User-facing failures are one-line
   findings with 中文 what/why/how-fix (see `core/failures.py` —
   step/subject/cause/evidence/hint), never tracebacks. Every mutation
   appends to `events.jsonl` (`core/events.py`). `--json` errors emit
   `{"error","code"}` via the `_fail` convention in `cli.py`.
9. **SQLite is disposable.** Nothing you build may make `.manju/` a source
   of truth. Every read from the ledger must degrade gracefully (pattern:
   `build/spend.py` sidecar fallback).
10. **Git discipline for this repo itself:** one commit per work package
    (or coherent slice), descriptive message, append an entry to
    `PROGRESS.md` (never rewrite history), and add a `DECISIONS.md` entry
    for any data-model or hash-formula change.

---

## 2. How to work (execution & token policy)

You are expected to control your own analysis cost. Rules:

- **Never read the mega-files end-to-end.** `src/manju/cli.py` (~217 KB),
  `src/manju/gui/server.py` (~255 KB), `src/manju/gui/page.py` (~200 KB),
  `src/manju/build/graph.py` (~94 KB), `src/manju/gui/edit.py` (~130 KB).
  Grep for the symbol named in this guide, then read ±80 lines around it.
- **Trust this guide's current-state map (§3) instead of re-exploring.**
  Verify only the specific anchors you are about to edit.
- **Effort calibration.** Mechanical work (adding a pydantic field, CLI
  plumbing, JSON passthrough) needs minimal deliberation — follow the
  nearest existing pattern. Design-sensitive work (WP6 diff engine, WP4
  staleness semantics, anything touching hash formulas) deserves slow,
  careful reasoning and a written-down plan in the commit message.
- **Tests:** during development run only the test files you touch
  (`python -m pytest tests/test_<area>.py -q`). Run the full suite
  (`python -m pytest -q`, baseline **2090 passing**) once per work package
  before committing. `OMP_THREAD_LIMIT=1` if tesseract-related tests
  thrash. CI installs with `-c constraints.txt`.
- **Environment:** Python 3.11+, `pip install -e ".[edgetts]"`, ffmpeg
  required. The 12-shot fixture generator is `tests/fixtures/make_sample.py`.
- **Match the house style.** Bilingual output (中文 labels for user-facing
  strings, English identifiers), pydantic v2 models in `core/models.py`,
  Typer commands with `--json`, stdlib-only GUI (no new dependencies
  anywhere without strong justification), atomic writes (tmp + `os.replace`).
- **Scope discipline.** Each WP lists explicit non-goals. Do not expand
  scope; record follow-up ideas as a line in `PROGRESS.md` instead.

---

## 3. Current-state map (verified)

### 3.1 The chain today: script → voice → captions → timeline

- **Dialogue is one object per shot.** `Dialogue` (`core/models.py:129`) has
  only `speaker` and `text`; `ShotSpec.dialogue` (`models.py:241`). There is
  **no per-line list, no line id** — the shot id IS the dialogue-line
  identity. A shot with two spoken lines is modeled as two shots.
- **Voice takes are append-only, newest-wins.** No `selected_voice` exists.
  `register_voice_take` (`core/container.py:535`) writes
  `media/gen/<shot>/voice_take_NN.<ext>` + `voice_take_NN.sidecar.yaml`;
  the compiler picks the newest (`timeline/compiler.py:_find_voice`, ~:635).
  A hand-dropped audio file with no sidecar = MANUAL, never invalidated.
- **Edge TTS** (`providers/edge_tts.py`) streams word boundaries into
  `<take>.timing.json` — a JSON array of `{"start_ms": int, "end_ms": int,
  "text": str}` (written at `edge_tts.py:124-128`). The generic REST TTS
  (`providers/tts.py`) does NOT emit timing.
- **Word-timed captions.** The compiler loads
  `voice.with_suffix(".timing.json")` (`compiler.py:643`), and when timing
  exists uses `_timed_captions` (`compiler.py:260`) with punctuation
  re-glued from the original dialogue by `_align_words_to_text`
  (`compiler.py:236`); otherwise a weighted CJK split (`_split_caption`).
- **Caption cues carry no identity.** `CaptionLine` (`models.py:958`) =
  `start_ms/end_ms/text/speaker`. The ONLY cue↔shot mapping in the codebase
  is temporal reconstruction inside the voice-repair loop
  (`media/voicefix.py:_shot_cues`, ~:150): a cue belongs to the shot whose
  compiled video-clip window contains its `start_ms`.
- **Manual captions mode:** `rules.captions.mode: manual` +
  `captions/captions.srt` present → human SRT is truth, compiler output goes
  to `captions.generated.srt`, ASS recompiled from the human cues verbatim
  (`exporters/srt_ass.py:276-324`). Human cues are never re-broken or moved.
- **Voice repair** (`manju repair --op voice`, `media/voicefix.py`): keeps
  footage, regenerates TTS via the build's own resolution, proportionally
  retimes this shot's cues (manual cues left verbatim + advisory), marks
  `repaired_from`/`audio_repaired` on the new sidecar, and lets content
  keys drive the remix on next build.
- **Staleness anchors:** `spec_payload`/`compute_spec_hash` (picture,
  `core/spec.py:94/:131`; v2 folds dialogue + keyframes) and
  `voice_payload`/`compute_voice_hash` (voice, `spec.py:168/:206`; folds
  text + speaker + the speaker's `VOICE_BIBLE_KEYS` (`spec.py:165`) +
  provider descriptor). `build/voice.py:evaluate_voice` yields
  NOT_NEEDED/MISSING/FRESH/STALE/MANUAL; **STALE voice is advisory-only**
  — build plans only MISSING (`build/graph.py:_plan_voice`, ~:438).
- **Why-stale names fields:** video take sidecars carry `spec_snapshot`
  (`models.py:333`); `diff_spec_fields` (`spec.py:141`) produces dotted
  paths. NOTE: `VoiceTakeSidecar` has **no** spec_snapshot — voice
  staleness reasons are generic strings today.

### 3.2 Staleness, caching, cost, confirmation (mostly already built)

- `ShotState` machine: `build/stale.py` (missing/fresh/stale/manual/
  needs_selection/broken). `--regen-stale` is the only auto-regen path.
- Segment cache key: `media/render.py:_segment_cache_key` (~:316) — source
  hash + geometry + fps + duration + fades + gain/mute + in-point.
  Final content key: `render.py:final_content_key` (~:1002) = timeline JSON
  + ordered segment keys + ASS hash + audio input hashes + encoding params;
  `.key.json` sidecars make finals idempotent. `final_vN.timeline.json`
  snapshot enables `manju compare` (`build/compare.py`).
- Pricing: manifest `CostConfig` (`providers/manifest.py:110`,
  per_call/per_second/currency), `estimate_cost` (`manifest.py:428`),
  routed-provider pricing (`graph.py:_estimate_shot_cost` ~:406). Voice
  estimates are per_call only (speech duration unknown pre-synthesis).
- Gates: `spend_gate` (`graph.py:67`) + inline gates in `run_build`
  (~:784), `redo_batch` (~:1675, ONE aggregated gate with per-shot
  breakdown), `voice_batch` (~:1838). Only the `expensive_generation`
  token of `ask_before` is enforced; `final_export` and `lock_change` are
  **inert** (declared in `models.py:75`, consumed nowhere).
- Explanation: `manju explain` (`build/explain.py`) gives per-shot
  video/voice states, timeline verdict, final/proxy content-key verdicts —
  **but no cost figures at all**.
- GUI plan modal: `gui/plan.py:action_plan` (~:91) — same estimators as the
  CLI, `{action, rows[], skipped[], estimated_cost, saved_cost, currency,
  zero_cost, routing}`; every generative job confirms through it.
- Director contract: `build/director.py` — Proposal YAML in
  `reports/proposals/`, whitelisted actions, state fingerprint expiry,
  **human-only confirm for priced actions** (`director.py:631`).
- Failures: `core/failures.py` structured records (step/subject/cause/
  evidence/hint/log_path) + `manju failures`; cloud resume-polling never
  resubmits (`providers/base.py:372`); `content_rejected` is first-class
  and never auto-retried; GUI jobs have honest interrupted state
  (`gui/jobs.py`).

### 3.3 Export, ingest, round-trip

- Exporters (`src/manju/exporters/`): OTIO hand-written JSON
  (`otio.py:export_otio` :150), JianYing dual path — deterministic
  **diff-stable skeleton** with uuid5 ids (`jianying.py:_build_draft` :98,
  `export_jianying` :371, self-lint :268) plus pyJianYingDraft native
  (`native_draft.py:177`) — pycapcut for CapCut (:187), SRT/ASS
  (`srt_ass.py`). All strictly **one-way**: no code parses an edited
  draft/OTIO back. The only round-trip is `manju ingest` (media files by
  naming convention, `build/ingest.py`, two-phase plan/apply, match
  confidence, batch records under `reports/ingest_batches/`).
- Export status center: `build/exportstatus.py` — exactly 9 deliverables ×
  freshness (上新/待更新/缺失/有问题/待人工确认/已人工确认), staleness
  recomputed from existing keys, drafts capped at 待人工确认 until a human
  `mark_verified` (hash-bound in `reports/verifications.jsonl`).

### 3.4 Transcribe / alignment

- `manju transcribe` (`providers/asr.py`): three on-ramps — generic ASR
  manifest (`AsrConfig`, `manifest.py:140`: segments_path/text_key/
  start_key/end_key/time_unit), `--from-srt` (`parse_srt` :55), `--text`
  (`distribute_text` :80, length-weighted spread). Output:
  `captions/transcripts/<name>.srt`. **There is no forced alignment** of
  script text to imported audio, and no mapping of imported human
  voiceover to script lines beyond the ingest filename convention.

### 3.5 Bible / consistency

- Bible = free-form dict-of-files (`characters/scenes/props/style/voices`,
  `core/container.py:55`), fields by convention (`ref_image(s)`,
  `ref_video(s)`, `VOICE_BIBLE_KEYS`). Read models: `manju assets`,
  `appearances`, `mentions`. Single ref resolver with 4-tier lineage
  (`providers/refs.py:resolve_refs` :157: params > shot_refs > bible >
  refs_dir_fallback), per-provider ref budget with 中文 impact clauses
  (`providers/refbudget.py`), cleanliness heuristics + `needs_vision`
  advisories (`qc/ref_checks.py`). Ownership is DERIVED, never registered
  (`core/refs.py`; DECISIONS #10).
- Consistency QC: `qc brief --mode consistency` builds character contact
  sheets / adjacent-pair boards / scene sheets (`qc/agent_review.py:542`),
  verdicts hash-bound to takes, `qc coverage` tracks reviewed/stale/never.
  Series layer: umbrella over normal episode projects, conservative
  `sync-bible`, continuity dashboard (`core/series.py:series_continuity`).

### 3.6 The gaps (what this guide exists to close)

| # | Gap | WP |
|---|-----|----|
| 1 | No cue↔shot identity; impact of a dialogue edit is not queryable anywhere | WP1 |
| 2 | No audio preview (试听) entry point; no audio-first build target (R19 deferred it with the endpoint design recorded in PROGRESS.md) | WP2 |
| 3 | Imported human voiceover cannot be aligned to the script (no timing for manual voice takes, no multi-shot VO splitting) | WP3 |
| 4 | No multilingual capability at all | WP4 |
| 5 | Transparency exists but is fragmented: explain has no cost, `final_export`/`lock_change` gates inert, failure "what now" options not unified | WP5 |
| 6 | Export is one-way; external edits cannot flow back as diffs | WP6 |
| 7 | Consistency machinery is post-hoc; not wired into the pre-spend plan | WP7 |

---

## 4. Work packages

Execute in this order: **WP1 → WP2 → WP5 → WP3 → WP6 → WP4 → WP7.**
WP2/WP5 are independent of each other and may be swapped. WP4 depends on
WP1's cue attribution and WP3's alignment helpers. Each WP = one commit
(or a short series), full suite green, PROGRESS.md entry.

---

### WP1 — Impact tracing: `manju impact` (the interconnection spine)

**User story.** "If I change this line of dialogue, what happens?" One
command (and one GUI strip) answers: which voice take goes stale, which
subtitle cues are affected, whether the picture spec moves, whether the
timeline recompiles, whether the final re-renders, which export
deliverables flip to 待更新, and what catching up would cost.

**Design decisions (made — do not relitigate):**

- **Do NOT restructure `Dialogue` into a list of lines.** The shot is the
  line unit (one dialogue per shot is the modeled reality; multi-line =
  multi-shot). Changing the model shape would churn `spec_payload` v2 and
  violate rule §1.4 for no user-visible gain.
- **Give caption cues a shot identity.** Add `shot: str = ""` to
  `CaptionLine` (`core/models.py:958`). The compiler stamps it in BOTH
  caption paths (`_timed_captions` and the `_split_caption` fallback — the
  cue's owning shot is already in scope at `compiler.py:455-485`).
  This moves the timeline fingerprint once on first recompile — document
  it (rule §1.5, round-O precedent). SRT/ASS output is unchanged (the
  field is internal); the JianYing skeleton may carry it as extra
  material metadata (harmless, deterministic).
- **Temporal fallback for old timelines.** Extract the cue↔shot temporal
  mapping out of `media/voicefix.py:_shot_cues` into a shared helper
  (suggested home: `src/manju/timeline/cuemap.py`, function
  `cues_for_shot(timeline, shot_id) -> list[tuple[index, CaptionLine]]`
  that prefers the stamped `shot` field and falls back to the time-window
  method). Refactor voicefix to call it. Behavior must be pinned by the
  existing voicefix tests.

**New module: `src/manju/build/impact.py`.**

```
impact_report(project, shot_id, field: str | None = None,
              new_value: str | None = None) -> dict
```

Two modes:
- *Hypothetical* (`field` + `new_value` given): deep-copy the shot, apply
  the edit (reuse the dotted-path machinery in `core/spec.py` /
  `core/locks.py`), recompute `compute_spec_hash` and
  `compute_voice_hash` against the copy, and compare with the takes'
  recorded hashes. Nothing is written.
- *Current* (no field): report the impact of edits already made — i.e.
  what is stale right now and what it touches (reuses
  `build/stale.py:evaluate_shot` and `build/voice.py:evaluate_voice`).

Report shape (all keys always present; `--json` emits it verbatim):

```json
{
  "shot": "S002",
  "field": "dialogue.text",
  "video":    {"state": "fresh", "would_become": "stale",
               "changed_fields": ["dialogue.text"], "selected_take": "take_03"},
  "voice":    {"state": "fresh", "would_become": "stale",
               "newest_take": "voice_take_02", "manual": false},
  "captions": {"cues": [{"index": 4, "start_ms": 8120, "end_ms": 9300,
                          "text": "这不可能。"}],
               "mode": "compiled", "manual_note": null},
  "timeline": {"verdict": "recompile (inputs changed)"},
  "renders":  {"final": "re-render (content key differs)", "proxy": "..."},
  "exports":  {"stale_after": ["srt","ass","otio","jianying","capcut",
                                "final","proxy","cover","teaser"]},
  "cost":     {"regen_video": 0.32, "regen_voice": 0.0, "currency": "CNY",
               "note": "voice per_call only — speech duration unknown"}
}
```

Implementation notes:
- `timeline`/`renders` verdicts: reuse the logic in `build/explain.py`
  (`_timeline_explanation`, `_render_explanation`) — refactor into shared
  helpers rather than duplicating.
- `exports.stale_after` is a *deterministic mapping*, not a recomputation:
  timeline recompiles → srt/ass/otio/drafts stale; final re-renders →
  final/proxy/cover/teaser stale. Do not call `exportstatus` on
  hypothetical state.
- `cost`: reuse `graph._estimate_shot_cost` (routed provider) and the
  voice per_call estimate. Read-only; no gate (nothing is spent).
- Manual-mode captions: when `rules.captions.mode == manual`, list the
  affected cues from the human SRT via the shared cuemap and set
  `manual_note` to the same 中文 advisory voicefix uses (human cues are
  never auto-moved).

**Surfaces:**
- CLI: `manju impact <shot> [--field dialogue.text --value "新台词"]
  [--json]` in `cli.py` (Typer, `_fail` convention, read-only, no lock).
- MCP: read-only `impact` tool in `mcp/tools.py` mirroring the CLI.
- GUI: `POST /api/impact` (pure read, exempt from readonly gate like
  `/api/validate` — see round R22 precedent) + an impact strip in the shot
  editor and the /subtitles editor: on dialogue/caption edit, debounce
  600 ms (copy the `/api/validate` pattern in `gui/server.py` /
  `gui/page.py`) and render: 「此修改将影响:配音 1 条将过期 · 字幕 2 条 ·
  时间线将重编 · 成片将重渲 · 追赶成本 ≈0.32 CNY」.

**Acceptance criteria:**
1. On the sample project with a voiced shot: `manju impact S002 --field
   dialogue.text --value X --json` reports voice would-become stale,
   video would-become stale (v2 folds dialogue), ≥1 caption cue listed
   with correct times, final verdict "re-render".
2. `--field camera.shot_size` reports video stale but voice `fresh`
   (voice_hash ignores camera) — this asymmetry is the core proof.
3. A MANUAL voice take reports `"manual": true` and would-become stays
   `manual` (never invalidated).
4. Nothing on disk changes; running it twice is byte-identical.
5. Existing voicefix tests still pass after the cuemap extraction.

**Non-goals:** editing anything; per-word cue granularity; cross-shot
ripple beyond the compiled timeline (duration ripple is already implied by
"recompile").

---

### WP2 — Audio-first: 试听 preview + audition build

**User story.** Hear the voice before spending on video. Two levels:
(a) per-line 试听 — synthesize one dialogue line to a throwaway file;
(b) whole-film audition — voice + captions + music, no video generation,
so narration and subtitle pacing are approved before any paid picture.

R19 already recorded the endpoint design (see PROGRESS.md "R19 note"):
short sample into `.manju/webpreview`, **never a take**, job-borne,
graceful "TTS 不可用". Implement exactly that.

**Part A — 试听 preview:**
- New helper (suggested: `src/manju/media/ttspreview.py`):
  `preview_voice(project, shot_id, text=None, voice=None) -> Path`.
  Resolve the TTS provider exactly like the build does (see
  `media/voicefix.py:_resolve_tts` :103 — reuse/extract it, do not fork).
  Synthesize into `.manju/webpreview/tts/<key>.mp3` where
  `key = short_hash(cache_key(text, resolved voice descriptor))` — replay
  is a cache hit, zero cost, zero re-synthesis. No `register_voice_take`,
  no sidecar, no event needed (it is disposable runtime, §1.9).
- Spend: Edge TTS is free (zero_cost). A priced TTS manifest goes through
  `spend_gate` with its per_call estimate.
- CLI: `manju voice <shot> --preview [--text "…"]` prints the output path
  (`--json`: `{"preview": path, "cached": bool}`).
- GUI: `POST /api/voice/preview` (job-borne through `gui/jobs.py`
  JobRunner, like other media jobs) + a 试听 ▶ button next to dialogue in
  the shot editor, /storyboard drawer, and /subtitles page; audio plays
  via the existing webpreview media route. Provider missing/offline →
  the recorded graceful message 「TTS 不可用」, never a traceback.

**Part B — audition build (`manju build --target audition`):**
- Pipeline: voice phase (MISSING only, same gates) → timeline compile →
  captions → render an **audition file**: full audio mix (voice + music +
  ambient + sfx + ducking, reusing the existing audio graph in
  `media/render.py`) under a cheap deterministic slate video (lavfi
  `color` background + drawtext shot id per segment, or the caption-card
  floor — pick the simplest that burns the real ASS captions), at project
  resolution/fps.
- Output: `renders/audition/audition_vN.mp4` + `.key.json` sidecar.
  Content key = same recipe as `final_content_key` but with slate params
  in place of segment keys — build twice → one file (idempotent).
- Critical property: shots with **zero video takes must not block** the
  audition. The compiler derives durations from voice + padding; where a
  video take is missing, the audition uses the slate for that span. If the
  current compiler refuses to compile without takes, add an
  `allow_missing_takes` compile flag used ONLY by the audition target
  (keep `timeline.json` untouched — compile to an in-memory timeline for
  the audition render; the real timeline stays gated as today).
- Surfaces: CLI flag; GUI: an 「先听后看」 card on the cockpit/create page
  once ≥1 dialogue line exists, running the audition job with the plan
  modal (it may spend on priced TTS).

**Acceptance criteria:**
1. Fresh project, 3 dialogue shots, zero video takes, Edge TTS available:
   `manju build --target audition` produces a playable mp4 with correct
   voices, word-timed burned captions, and BGM ducking; **zero video
   generation attempted; zero cost recorded** beyond TTS (Edge = 0).
2. Second run skips via content key. Editing one dialogue line then
   `manju voice <shot> && manju build --target audition` re-renders.
3. `manju voice S001 --preview` twice: second call returns `cached: true`
   and does not hit the network.
4. No file under `media/gen/` is created by previews.

**Non-goals:** video preview; auto-selecting voices; persisting previews.

---

### WP3 — Align imported human voiceover to the script

**User story.** A user records real narration (one file per shot, or one
long file for the whole script) and imports it. The system aligns the
original script text to the audio so imported voice gets the same
word/segment-timed captions as TTS voice, and multi-shot recordings are
split into per-shot voice takes.

**Foundation already present:** manual voice drops via ingest naming
(`S001.wav` → sidecar-less MANUAL voice take); ASR on-ramps
(`providers/asr.py`: manifest / `--from-srt` / `--text`); the compiler
already prefers any `<voice>.timing.json` — so alignment only needs to
*produce timing sidecars*; it plugs into the existing chain with zero
compiler changes.

**Part A — per-take alignment: `manju align <shot> [options]`**
- New module `src/manju/media/align.py`.
- Targets the shot's newest voice take (or `--take voice_take_02`).
- Three on-ramps, mirroring transcribe exactly:
  1. `--asr <provider>`: run ASR on the take, get `TranscriptSegment`s.
     Extend `AsrConfig` (`providers/manifest.py:140`) with optional
     word-level keys (`words_path`, `word_text_key`, `word_start_key`,
     `word_end_key`) — used when the vendor returns word timestamps;
     segments otherwise. Spend-gated like transcribe.
  2. `--from-srt <file>`: the user supplies cue timings.
  3. Default (no flags, zero deps): **deterministic text anchoring** —
     take the shot's `dialogue.text`, split with the same CJK
     sentence-ender logic as `distribute_text` (`asr.py:80`), and spread
     over the take's real duration weighted by piece length. This is the
     honest floor: coarse but deterministic and free.
- Anchoring step (for on-ramps 1–2): match transcript segments against
  `dialogue.text` after CJK normalization (strip punctuation/whitespace);
  score with a simple character-level ratio (difflib is stdlib and
  deterministic — acceptable). Below a match threshold (suggest 0.6):
  keep the raw segment timings, add a 中文 advisory ("识别文本与剧本差异较大,
  已按识别结果对齐"), never fail.
- Output: `<take>.timing.json` in EXACTLY the Edge schema — array of
  `{"start_ms","end_ms","text"}` — beside the take file (for a manual
  drop named `voice.wav` that is `voice.timing.json`; verify against
  `compiler.py:643` `_load_voice_timing`). The sidecar is **derived and
  regenerable**: re-running `align` overwrites it (it is not a take; it is
  timing metadata — document this exception to append-only in the module
  docstring, mirroring how `.key.json` sidecars are treated).
- The manual take stays MANUAL (§1.3). Alignment never touches staleness.

**Part B — multi-shot VO splitting: `manju align --media <file>
--shots S001-S012 [--apply]`**
- The "one long recording" case. Two-phase like ingest:
  1. **Plan (default):** transcribe the file (any of the three on-ramps;
     `--from-srt` recommended in docs), then anchor each consecutive
     shot's `dialogue.text` against the transcript **in script order**
     (order-preserving greedy matching — shots are sequential by
     `shots/index.yaml`). Produce a table: shot → `[start_ms, end_ms]` →
     matched text → confidence. Unmatched shots listed honestly.
  2. **Apply:** slice the audio per shot with ffmpeg (stream-copy where
     the container allows, re-encode floor), register each slice via the
     existing `register_voice_take` path as a manual take WITH a timing
     sidecar (segment times shifted to slice-local), and write an
     ingest-style batch record under `reports/ingest_batches/` (reuse
     `build/batches.py` so the existing /ingest review UI covers it).
     Source file: if outside the project, copy into `media/imports/`
     first (imports sacred, slices are derived media under `media/gen/`).
- Confidence gates: rows below threshold land as `pending` (round-AA
  match-state vocabulary) and are NOT applied without explicit per-row
  confirm — never land audio on a guess.

**Acceptance criteria:**
1. Drop `S001.wav` (record anything), `manju align S001` → timing sidecar
   exists; `manju build` produces timed captions for S001 (assert cue
   start > 0 aligns with the sidecar, not the weighted split).
2. `align --from-srt` with a hand-made SRT maps cue text to segments.
3. Multi-shot: a concatenated fixture wav + `--from-srt` plan assigns all
   shots windows in order; `--apply` creates N voice takes + sidecars +
   one batch record; `manju build` compiles with the human voice driving
   durations.
4. Voice state for these takes remains MANUAL; nothing marks them stale.
5. Cloud ASR path is spend-gated (unit test with a scripted transport,
   pattern: existing generic_asr tests).

**Non-goals:** phoneme-level forced alignment (no new ML deps — the
architecture reserves that for a future `aligner`-type provider manifest);
speaker diarization; editing imported audio.

---

### WP4 — Multilingual versions (locale overlays)

**User story.** Produce an English (or any language) version of a finished
Chinese film: same shots, same cut, same structure of voiceover and
subtitles — different language. Translations keep referential integrity:
when the base dialogue changes, the affected translations are flagged.

**Design decisions (made):**
- **A locale is an overlay, never a fork.** New directory:

  ```
  locales/en/
  ├─ lines.yaml     # S001: {text: "Impossible.", base_hash: <sha of base text>}
  └─ voices.yaml    # linxia: {voice_id: en-US-JennyNeural}   (optional)
  ```

- **Locale text NEVER enters `spec_payload`.** The picture pipeline (spec
  hashes, video takes, segment cache) is 100 % shared across languages —
  that is the whole economic point. Only `voice_payload` and caption
  compilation see the overlay. (v2 `voice_payload` already folds
  `language`; the overlay text + per-locale voice_ref slot in naturally.)
- **Per-locale voice takes:** `media/gen/<shot>/locales/<lang>/
  voice_take_NN.*` — extend `container.voice_takes`/`register_voice_take`
  /`next_voice_take_name` with an optional `lang` param (default None =
  base, existing paths byte-identical).
- **Per-locale captions:** `captions/locales/<lang>/captions.srt/.ass`
  (+ per-locale manual mode with the same `generated.srt` contract).
- **Per-locale finals:** `renders/final/locales/<lang>/final_vN.mp4` with
  their own `.key.json`. Video segments are reused from the shared
  segment cache (only ASS hash + audio input hashes differ in the content
  key) — a locale render is cheap by construction. State this in the
  render log line so the user sees the reuse.
- **Translation staleness:** each `lines.yaml` entry stores `base_hash` =
  sha256 of the base `dialogue.text` at translation time. Base text moved
  → that line is 翻译过期 (advisory, §4.3 — never auto-retranslate; the
  engine does not translate at all, rule §1.2: translation is the
  agent's/human's job, the engine only tracks freshness).

**Surfaces:**
- `manju locale add <lang>` — scaffold `locales/<lang>/lines.yaml` with
  every shot id + empty text + current base_hash (commented, like shot
  scaffolds).
- `manju locale status [<lang>] [--json]` — per line: missing / 翻译过期 /
  ok; per shot voice state for that lang; caption/final freshness. This
  is the "structure consistency" report: it is keyed by shot id, so
  subtitles and voiceover across languages line up by construction.
- `manju voice --lang en --missing`, `manju build --lang en` — same
  commands, one flag; all spend gates and plan modals apply unchanged
  (thread `lang` through `_plan_voice`/compile/render call chains).
- `manju impact` (WP1) gains a `locales` section: a base dialogue edit
  lists which locale lines flip to 翻译过期.
- GUI: minimal this round — a locale column on `/subtitles` page selector
  and a status card; full locale editor is a recorded non-goal.

**Acceptance criteria:**
1. `locale add en`, translate 3 lines, `manju voice --lang en --missing`
   (Edge has en voices), `manju build --lang en` → an English final whose
   **video segment cache hits 100 %** (assert: no new files in
   `renders/segments/` versus the base build) and English captions.
2. Base project untouched: base final's content key unchanged; running
   the full suite proves byte-identity for projects with no `locales/`.
3. Edit base `dialogue.text` on S002 → `locale status en` flags exactly
   S002 as 翻译过期; en voice take for S002 reports stale (voice_hash
   folds the overlay text); nothing auto-regenerates.
4. `manju check` validates locale files (unknown shot ids, malformed
   YAML → one-line findings, FIX-D pattern).

**Non-goals:** machine translation inside the engine; per-locale picture
edits; locale-specific timelines (duration stays driven by the BASE
language; a locale voice longer than its slot surfaces as the existing
sync-hint advisory — record this honestly in the docs).

---

### WP5 — One transparency contract before every spend

**User story.** Every button and command that can generate answers the
same five questions first: cost? affected shots? why? what's reused? and
after failure — what now? Most machinery exists; this WP unifies it.

**Sub-tasks:**

1. **`manju explain --cost`.** `explain` output (build/explain.py) gains an
   optional per-shot `est_cost` + total, reusing `graph._estimate_shot_cost`
   / `_plan_voice` estimators (read-only, no gate). Keep the default
   output unchanged (backward compat for tests); `--cost` adds the fields.
2. **Make `ask_before` honest.** `final_export`: when present in
   `project.yaml:ask_before`, `manju export` / `manju package` (and their
   GUI jobs) require `--yes`/`assume_yes` (same `WaitingUser` shape; they
   are free, so the gate is about *outward-facing artifacts*, not money —
   the reason string must say so). `lock_change`: `manju lock` prompts for
   confirm when the token is present (unlock is already interactive-only).
   Alternatively, if implementation reveals this gate is more disruptive
   than protective, REMOVE the two tokens from the default list
   (`models.py:75`) and document in DECISIONS.md — inert defaults are the
   one unacceptable option.
3. **Unify the plan shape.** `manju build --dry-run --json` and
   `manju redo … --dry-run --json` must emit the same
   `{rows[], skipped[], estimated_cost, saved_cost, currency, zero_cost,
   routing}` envelope as `gui/plan.py:action_plan` (today the CLI prints
   its own arrangement). One shape = the director contract, the GUI modal,
   and the CLI all show the identical plan. Refactor so `action_plan`
   (or its engine core) is the single source; the CLI renders it.
4. **Cache-reuse visibility in the plan.** Plan rows already carry
   skipped/saved_cost; additionally surface the render verdict line from
   explain ("final: skip (content key matches) / re-render") in the plan
   envelope (`renders` key) so "what will NOT be regenerated" is explicit
   pre-confirm, in the GUI modal and CLI dry-run alike.
5. **Failure options (`next_options`).** After any generation failure, the
   user must see their choices. Add a deterministic
   `next_options: list[{action, label_zh, why}]` to `Failure.detail`
   (`core/failures.py`) computed at record time for `step=generate|voice`:
   - `rate_limited/timeout` → retry (`manju tasks retry <id>` — already
     exists);
   - `content_rejected` → rewrite prompt (`manju prompt <shot>`), or next
     fallback provider (name it via `fallback_chain`), or manual import
     (`manju select <shot> --file …`);
   - `provider_error` → next provider in chain / check `manju doctor`.
   Render in `manju failures` output and the GUI failure cards (which
   already have a retry button — add the other options as links).
6. **Voice cost honesty.** Everywhere voice estimates appear, carry the
   existing caveat string ("per_call only — speech duration unknown
   pre-synthesis") into the JSON (`note` field) rather than only prose.

**Acceptance criteria:**
1. `manju build --dry-run --json | jq .rows` equals the GUI
   `/api/plan`-modal rows for the same project (write a test asserting
   envelope equality through both paths).
2. With `final_export` in ask_before: `manju export` without `--yes`
   returns the `waiting_user` shape; with `--yes` proceeds; token absent →
   unchanged behavior (pinned).
3. A scripted-transport content_rejected failure record contains ≥2
   `next_options` including the fallback provider's actual name.
4. `manju explain --cost --json` totals equal `build --dry-run` estimate
   on the same project state.

**Non-goals:** changing gate semantics for `expensive_generation`; hard
budget caps (soft-limit stance is a recorded decision, DECISIONS #9).

---

### WP6 — Round-trip: external edits flow back (`manju roundtrip`)

**User story.** The user exports to JianYing/CapCut/OTIO, makes real edits
in their editor (reorder, trim, retime subtitles, tweak volumes), and
instead of those edits dying outside the system, Manju reads the edited
document, shows a reviewable diff plan ("S003 moved before S002; S005
trimmed to 2.1s; cue 7 text changed"), and applies the accepted rows as
ordinary truth-text changes — so the next build and all future
regenerations preserve the edit.

**Scope decision (made):** v1 supports **our own two deterministic
formats** as round-trip carriers: the JianYing **diff-stable skeleton**
(`exports/jianying/<name>/draft_content.json` — uuid5 ids exist precisely
for this) and **OTIO**. Native pyJianYingDraft / pycapcut drafts and
CapCut cloud drafts are explicitly NOT parsed back (format drift risk;
the export report and docs must tell users: "edit the skeleton copy /
OTIO for round-trip"). This is honest and shippable.

**Step 1 — export-side prerequisites:**
- Verify (grep `jianying.py:_build_draft` and `otio.py:export_otio`) that
  every emitted segment/clip carries its **shot id and take name** in
  metadata. Where missing, add them (skeleton: an extra deterministic
  field on each segment/material; OTIO: `metadata.manju = {shot, take,
  kind}` on every clip including audio/caption items). Deterministic
  output must stay deterministic (uuid5 unaffected by added constant
  fields).
- At export time, write a **baseline** next to the export:
  `exports/<kind>/.baseline/<name>.json` = the exported document bytes +
  `{compiled_from: <timeline fingerprint>, exported_at}`. The baseline is
  derived state (gitignored like other exports).

**Step 2 — new module `src/manju/build/roundtrip.py`:**

```
plan_roundtrip(project, edited_path) -> RoundtripPlan
apply_roundtrip(project, plan, rows) -> BatchResult
```

- **Parse** the edited file (tolerant: unknown fields ignored; a file that
  is not recognizably ours → one-line finding naming what's missing).
- **Identify** each segment via the stamped shot/take metadata (skeleton:
  uuid5 ids double-check; OTIO: `metadata.manju`). Unidentifiable
  segments → `unmatched` rows (external media the user added — the row's
  suggested action is `manju ingest`).
- **Diff against the BASELINE** (not against current truth) to isolate
  exactly the user's edits. Then check the baseline's `compiled_from`
  against the current timeline fingerprint: if truth moved since export,
  mark conflicting rows `conflict` (round-AA vocabulary) — never merge on
  a guess.
- **Translate** each detected edit class into an existing write path
  (NEVER invent a new mutation mechanism):

  | External edit | Truth-text change | Existing path |
  |---|---|---|
  | Clip reorder | `shots/index.yaml` permutation | the `/api/index` permutation-validated writer (extract to core if GUI-bound) |
  | Trim in/out | virtual in/out window on the take | `media/repair_ops.py` `set_inout` (virtual mode) |
  | Clip deleted | proposal row only — suggest removing from index | index writer (requires explicit confirm) |
  | Caption text/timing edit | manual captions takeover: update `captions/captions.srt`, set `rules.captions.mode: manual` on first edit (explicit confirm row, same as the /subtitles editor's takeover confirm) | srt write + `_gated_save`-style check |
  | Per-clip volume/mute | shot footage gain/mute | `build/mixer.py` apply API |
  | Music/ambient gain, BGM swap to an existing import | `rules.yaml` audio | mixer apply |
  | Transition type change at a boundary | `rules.transition_overrides` | existing rules writer |
  | Anything else (effects, keyframes, speed, added media) | listed honestly as `unsupported` with a 中文 explanation | none |

- **Plan rendering:** ingest-style dry-run table (reuse the
  `_print_ingest_table` pattern) with per-row: edit class, evidence
  (old → new values), target truth file, match state, and the WP1 impact
  summary (this is where the spine pays off — each row shows what its
  application would stale/re-render).
- **Apply:** under ONE `build_lock`, rows through the checked write paths,
  per-row isolation (a failing row doesn't abort the batch), one batch
  record under `reports/roundtrip_batches/<id>.yaml` (same shape/reviewer
  flow as ingest batches — reuse `build/batches.py`), one event per
  applied row, discard/undo semantics mirroring round AA (undo only if
  still the applied value).

**Surfaces:**
- CLI: `manju roundtrip <edited-file> [--apply] [--rows 1,3-5] [--json]`.
- GUI: add roundtrip batches to the existing `/ingest` review panel
  (batch selector already exists) rather than a new page.
- Export report (`export_report.md`) and `manju exports` gain one line:
  which copy is the round-trip carrier.

**Acceptance criteria:**
1. Export skeleton → programmatically reorder two segments + change one
   caption text in the JSON (this simulates the editor; the test fixture
   IS the edited file) → `manju roundtrip` plan shows exactly 2 rows
   (reorder + caption edit), nothing else.
2. `--apply` → `shots/index.yaml` order changed via the checked path;
   captions flipped to manual mode with the edited cue; `manju build`
   honors both; `manju compare` between the finals names the reorder.
3. Trim a segment's source window in OTIO → plan maps to a virtual inout
   repair; apply mints a new take (append-only) and re-render uses it.
4. Edit the file after truth moved (touch a shot) → affected rows are
   `conflict`, apply refuses them, others still applicable.
5. An added foreign media segment → `unmatched` row suggesting ingest;
   never applied.
6. A byte-identical re-export after apply produces a new baseline; a
   second roundtrip of the same edited file reports "no changes".

**Non-goals:** parsing native/encrypted drafts; two-way live sync;
importing effects/keyframes; merging concurrent edits (conflict = refuse).

---

### WP7 — Consistency: close the loop before the spend

Most consistency machinery exists (§3.5); this WP wires it into the
pre-spend moment so drift is prevented, not just detected.

1. **Consistency preflight in the plan.** The plan envelope (WP5) gains a
   `consistency: list[warning]` per row: shots about to generate whose
   characters have no `ref_image`/primary ref (reuse
   `providers/refs.py:ref_reliability_notes` :337 and
   `core/appearances.py` missing-refs data), or whose refs failed
   cleanliness checks (`qc/ref_checks.py`). Advisory only — never blocks;
   the GUI modal shows a ⚠ per row, the CLI dry-run a warning line.
2. **Promote-frame-to-ref suggestion.** When a character has appearances
   but no bible ref, `manju qc` (rule-based advisories section) suggests:
   "从已选 take 抽帧作为 <id> 的参考图: manju frames <take> --at <mid> 然后
   manju refs assign …" — pure suggestion string; the commands exist.
3. **Coverage nudge post-generation.** After a build that generated ≥1
   take for a character with existing consistency verdicts, the build
   summary includes the existing `qc coverage` stale count ("一致性评审
   过期 N 项 — manju qc brief --mode consistency"). Data already computed
   by `qc_coverage`; this is a surfacing line only.

**Acceptance criteria:** a shot with a bible character lacking refs shows
the ⚠ in both `build --dry-run --json` and the GUI plan payload; adding a
ref via `refs assign` clears it; zero behavior change for projects where
all refs resolve.

**Non-goals:** blocking generation on consistency; automatic ref
selection; vision-model checks (agent-eyes QC is the recorded stance).

---

## 5. Integration order & definition of done

| Order | WP | Depends on | Rough size |
|---|---|---|---|
| 1 | WP1 impact spine | — | M (1 module + model field + 3 surfaces) |
| 2 | WP2 audio-first | — | M |
| 3 | WP5 transparency | WP1 (impact in plans is optional but natural) | M |
| 4 | WP3 VO alignment | — | M |
| 5 | WP6 roundtrip | WP1 (impact per row), ingest/batches patterns | L |
| 6 | WP4 locales | WP1 (cue attribution), WP3 (nothing hard) | L |
| 7 | WP7 consistency preflight | WP5 (plan envelope) | S |

Definition of done, per WP:
- Full suite green (baseline 2090 + your new tests; every new behavior
  pinned, every byte-identity claim pinned by an equality test).
- `manju check` still passes on the sample fixture; `manju build` twice
  still yields one final (M0 smoke).
- New CLI commands support `--json`; new user-facing strings are 中文-first
  with the existing glossary tone (`gui/glossary.py`).
- `PROGRESS.md` gains an appended round entry (done/verified/honest
  boundaries). `DECISIONS.md` entry for: the CaptionLine field (WP1), the
  timing-sidecar regenerability exception (WP3), the locale overlay model
  (WP4), the roundtrip carrier scope (WP6).
- README CLI table updated for new commands (round-R20 precedent: every
  claim must grep back to handler code).

Final deliverable check (the product bar, restated): a user can (1) edit a
dialogue line and be shown, before saving, exactly which voice/subtitles/
shots/exports are affected and at what cost; (2) audition the entire film's
voice + subtitles before any video money is spent; (3) import a real
narration recording and get script-aligned, word/segment-timed subtitles;
(4) produce a second-language version that reuses every video segment and
flags stale translations; (5) see cost / affected shots / cache reuse /
failure options in one consistent shape at every generation trigger; and
(6) edit the exported cut in their own editor and flow those edits back as
reviewable, hash-checked truth changes.

---

## 6. Appendix — key file index

| Area | Files |
|---|---|
| Truth models | `src/manju/core/models.py` (Dialogue :129, ShotSpec :226, CaptionLine :958, ask_before :75, sidecars :333/:405) |
| Hash formulas | `src/manju/core/spec.py` (SPEC/VOICE_VERSION :39, spec_payload :94, voice_payload :168, diff_spec_fields :141) |
| Staleness | `src/manju/build/stale.py`, `src/manju/build/voice.py` |
| Build/plan/gates | `src/manju/build/graph.py` (spend_gate :67, _plan_voice :438, _estimate_shot_cost :406, redo_batch :1573) |
| Explain | `src/manju/build/explain.py` |
| GUI plan modal | `src/manju/gui/plan.py`, `src/manju/gui/page.py` (renderPlan ~:2160) |
| Director | `src/manju/build/director.py` |
| TTS / timing | `src/manju/providers/edge_tts.py`, `src/manju/providers/tts.py` |
| Compiler / captions | `src/manju/timeline/compiler.py` (_timed_captions :260, _align_words_to_text :236, _load_voice_timing :643) |
| Caption export / manual mode | `src/manju/exporters/srt_ass.py` (:276) |
| Voice repair / cue mapping | `src/manju/media/voicefix.py` (_shot_cues :150, _resolve_tts :103) |
| Render / content keys | `src/manju/media/render.py` (_segment_cache_key :316, final_content_key :1002) |
| ASR / transcribe | `src/manju/providers/asr.py`, `AsrConfig` in `providers/manifest.py:140` |
| Ingest / batches | `src/manju/build/ingest.py`, `src/manju/build/batches.py` |
| Exporters | `src/manju/exporters/{jianying,otio,native_draft}.py` |
| Export status | `src/manju/build/exportstatus.py` |
| Compare | `src/manju/build/compare.py` |
| Failures | `src/manju/core/failures.py` |
| Refs / consistency | `src/manju/providers/refs.py`, `providers/refbudget.py`, `qc/ref_checks.py`, `qc/agent_review.py`, `core/refs.py`, `core/appearances.py` |
| Writes / locks | `src/manju/core/writes.py`, `core/locks.py`, `runtime/buildlock.py` |
| Ledger | `src/manju/runtime/state.py`, `src/manju/build/spend.py` |
