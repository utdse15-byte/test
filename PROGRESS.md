# PROGRESS.md — append-only log

> Append entries at the bottom. Never rewrite history; corrections get a new
> entry referencing the old one.

---

## 2026-07-05 — rounds 1–5: v2.2 implementation

**Done:** M0 engine core (container/hashing/locks/stale/compiler/check),
media pipeline + providers + QC + exporters + board, build graph + full CLI,
MCP server, SQLite run ledger with resume-polling, prompt compilation,
title cards, generic_cloud config adapter (§8.6) + manifests + dry-run
pricing, machine-checked content QC (OCR must_show), dual-path drafts
(pyJianYingDraft/pycapcut, capcut-cli lint wall), ASR slot
(`manju transcribe` + manual on-ramps), html_render via Chromium, import
previews, captions manual mode, schema export. 172 tests green at round end.

**Next:** review response (tickets 1–4).

**Open issues:** real cloud vendor manifests unverified against live APIs
(need keys); desktop JianYing/CapCut draft opening unverified (no desktop
apps in environment); xfade seam transitions deliberately not implemented
(handles problem — see render.py design note + DECISIONS.md context).

---

## 2026-07-05 — review round 1 response (tickets 1–4)

**Done:**
- Ticket 1 [FIX-B 93b302b, FIX-A e1031b0]: frame-grid snapping in the
  compiler (rule documented in code + README), `fps=` forced in the final
  chain, QC asserts `r_frame_rate == fps` and `|final − timeline| ≤ 1 frame`;
  finals are idempotent via a content key + `final_vN.key.json` sidecar +
  `manju build --force`. Red-proofs: pre-fix r_frame_rate=143/6; build-twice
  produced 2 finals.
- Ticket 2 [FIX-C/D/E 756a69e]: secret scan catches unquoted
  `api_key=sk-proj-…`, ghp_/gho_, xox?-, long Bearer, with false-positive
  guards; YAML/schema/Media errors are one-line findings (props.yaml and
  manual-timeline repros are tests); pack embeds the project name (zip
  comment), unpack restores it, `--dest` overrides; make_sample uses argparse
  (`--help` side-effect-free).
- Ticket 3 [FIX-F 7bbbbbf]: pinned dialogue.text ∉ video spec_hash;
  `compute_voice_hash` added (M3 TTS staleness anchor, not yet wired);
  `tools` extra split into jianying/capcut/mcpvideo.
- Ticket 4: DECISIONS.md, PROGRESS.md, REPORTS/REVIEW-01-RESPONSE.md.

**Next:** wire `compute_voice_hash` into the TTS provider when a real cloud
TTS manifest lands (M3); fill a real video-API manifest and run a paid
end-to-end.

**Open issues:** see REPORTS/REVIEW-01-RESPONSE.md "newly discovered issues".

---

## 2026-07-05 — round A: review open-issue closure

**Done:** proxy renders now carry proxy.key.json and skip re-encodes on a
content-key match (--force bypasses) — open issue #3; QC names the frame
grid when a hand-authored timeline carries off-grid durations, with the
FIX-B rule in the suggestion — open issue #2; captions-manual build
idempotency pinned by test (deterministic ASS recompile keeps the key
stable; edits to the human SRT change it by exactly one new final) — open
issue #1 audit closed. 213 tests green.

**Next:** M3 voice/TTS pipeline wiring compute_voice_hash; build explainer.

**Open issues:** baseline count reconciliation (#4) still with the reviewer.

---

## 2026-07-05 — round B: M3 voice/TTS pipeline

**Done:** generic_tts manifest adapter (sync URL/base64 §8.4 degenerate form
+ async poll), VoiceTakeSidecar carrying voice_hash (FIX-F anchor now WIRED),
§4.3-conservative voice staleness (missing→synthesize, stale→flag only,
hand-dropped→manual/never invalidated), build-graph voice phase before the
timeline compile (fresh voices drive durations §6 in the same build),
dry-run per_call pricing feeding the budget breaker, `manju voice` command,
status voice_by_state summary. Fixed: compiler picked the OLDEST voice take
(sorted-first) — now newest-wins per append-only semantics. 220 tests green.

**Next:** build explainer (`manju explain`), property-based tests.

**Open issues:** voice cost estimate is per_call only — speech duration is
unknown pre-synthesis so per_second pricing cannot be estimated honestly;
the ledger records the real figure afterwards.

---

## 2026-07-05 — round C: manju explain

**Done:** `manju explain [--json]` + MCP `explain` tool — the build system
justifies itself, read-only: per-shot picture/voice states with short hash
evidence, timeline fingerprint comparison (unchanged / recompile / manual-
truth), final & proxy content-key verdicts (skip / render / re-render), with
an honest note that keys are computed against the current captions.ass.
Degrades cleanly on empty projects. 226 tests green.

**Next:** property-based tests (hypothesis) over the pure cores.

---

## 2026-07-05 — round D: property-based tests

**Done:** hypothesis invariants over the pure cores: canonical hashing is
key-order blind and JSON round-trips; jsonpath list indexing; frame snapping
is idempotent, monotone, lands on whole frames, and honors the one-frame
floor (hypothesis found the ms=1 → 42ms floor case on its first run — the
naive half-frame bound was wrong, the floor is the point); the caption
splitter loses no characters and respects the line budget; SRT emit→parse is
the identity on valid segments. hypothesis added to the dev extra.
236 tests green.

---

## 2026-07-05 — round E: board catches up with the engine

**Done:** review board upgrades — per-shot voice chip (M3 states), render
verdicts from the explainer in the header (final/proxy skip-or-re-render at
a glance), QC mid-point frames as posters on selected takes; QC now surfaces
voice staleness as info items with the `manju voice` suggestion (§4.3:
advisory, never auto-redone). 240 tests green.

---

## 2026-07-05 — round F: MCP copilot e2e + fresh-clone verification

**Done:** the M2 copilot acceptance shape driven over the real MCP wire in
one server session: status → update_shot (the only MCP write path) → check →
build --target qc (offline self-fill via caption_card) → explain →
select_take → propose → events all recorded as actor=ai; dangerous surface
(unlock/gc/pack/import) re-asserted absent. Fresh-clone suite verification
follows the push. 242 tests green.

---

## 2026-07-05 — round G: CI gate + status voice line

**Done:** GitHub Actions workflow (.github/workflows/ci.yml): full suite on
push/PR with the same system deps as local (ffmpeg, tesseract+chi_sim,
WenQuanYi; Chromium deliberately absent — html tests skip and the drawtext
fallback is itself the tested path), plus the M0 acceptance smoke (build
twice → one final, r_frame_rate=24/1). Human `manju status` now prints the
voice-state line. Fresh-clone verification of the full tree: 242/242.

---

## 2026-07-05 — round H: showcase film《深夜信号》

**Done:** a real 10s vertical short built purely through the CLI:
HTML-rendered caption cards (Chromium html_render), Ken Burns from a
rendered still, title card overlay, burned CJK captions, ambient BGM,
1080×1920@24/1, QC passed; second build reused the final via content key;
`manju redo --provider` + `manju select` + incremental re-render exercised
live, with `manju explain` correctly predicting the re-render beforehand.

**Finding (recorded, not churned):** with any image in media/refs/, the
§8.4 fallback chain routes EVERY missing shot to ffmpeg_kenburns with that
same generic image (still_frame_motion precedes caption_card by design).
Correct per spec but a UX footgun for dialogue-only shots — candidate
improvement: advisory when kenburns used the generic refs_dir fallback
rather than a shot-specific reference. Open issue for the next review.

---

## 2026-07-05 — round I: generic-reference advisory (showcase finding closed)

**Done:** the kenburns provider records which resolution tier chose the
reference image (params / bible / refs_dir_fallback) in the take's lineage
(§4.2), and the build now advises when a shot fell back to a generic
media/refs image — with the three shot-specific alternatives named.
Explicit params.image stays silent. 243 tests green.

---

## 2026-07-05 — rounds J+K: competitive study + Edge TTS + stock footage

**Done (J):** studied MoneyPrinterTurbo, ShortGPT, NarratoAI, edge-tts live;
REPORTS/COMPETITIVE-STUDY.md maps their features against ours: adopted the
free keyless Edge TTS default and the stock-footage provider pattern;
deliberately rejected in-tool LLM scripting (against §0), auto-publishing,
batch mode, web UI (§1-⑦); identified our differentiators (deterministic
rebuilds, locks/takeover, editor drafts, ledger) that none of them have.

**Done (K):** EdgeTtsProvider — a REAL keyless neural cloud TTS through the
§8.6 module:Class escape hatch (Edge speaks WebSocket, precisely not the
generic REST shape), live-verified: `manju build` synthesized 4 zh-CN
neural voice lines for《深夜信号》, the timeline recompiled with
voice-driven durations (§6), voiced final delivered. `manju[edgetts]`
extra pinned. PexelsStockProvider scaffolded with offline scripted-transport
verification (search→orientation-matched rendition→download→lineage);
live verification honestly pending a free PEXELS_API_KEY. Query resolution:
params.query > scene stock_query > action line > scene name. 251 tests green.

---

## 2026-07-05 — round L: system assessment + idea-stage scaffolds

**Done:** REPORTS/SYSTEM-ASSESSMENT.md answers the goal question claim by
claim with executable evidence — the idea→film chain per stage, the three
modes (AI-auto via MCP wire e2e + live showcase; human-manual via the M0
acceptance; handoff via locks/manual-truth states), and two honest
qualifiers (paid text-to-video unexercised without a vendor account;
desktop draft opening needs the apps). `manju new` now scaffolds
story/brief.md, outline.md, script.md so every takeover finds the same
idea-stage shape. 252 tests green.

---

## 2026-07-05 — round M: word-timed captions

**Done:** Edge TTS now streams word boundaries (edge-tts 7.x needs explicit
boundary="WordBoundary" — found live) into <voice_take>.timing.json; the
compiler prefers real speech timing over the weighted split when a timing
sidecar exists (fingerprint includes it — purity holds), grouping words into
cues by line budget + sentence enders. Edge's zh boundaries drop punctuation,
so an alignment pass glues it back from the original dialogue (mismatch falls
back to raw). Live-verified in the showcase: cues start at word onset (300ms)
and end when speech ends; definitive cut delivered. 259 tests green.

---

## 2026-07-06 — round N: audio policy + packaging kit + preset kits

**Done (three parallel builds off one schema contract, merged in order):**
- **Contract first:** AudioMixRules / PackagingSpec / ProjectConfig.preset and
  the shared `resolve_anchor()` ("shot:<id>[:start|:end]") committed as
  models-only so all three features built to one shape.
- **Audio policy (P2):** `rules.audio` compiles deterministically onto new
  sfx/ambient timeline tracks — per-voice gain, anchored one-shot SFX,
  transition sound at every interior cut, looped ambient bed
  (`-stream_loop -1` + atrim) with optional voice-keyed sidechain ducking
  (ducking generalized: one key per ducked clip across music AND ambient);
  `_audio_input_hashes` covers the new tracks so edited audio re-renders and
  unchanged skips; QC reports missing sources and unresolvable anchors.
- **Packaging kit (P2, closes M4 片头尾/包装):** `timeline/packaging.yaml`
  (scaffolded disabled) — intro/outro rendered as content-addressed card MP4s
  (`_packaging/{intro|outro}_<hash10>.mp4`, html_card ∥ drawtext) inserted as
  REAL leading/trailing segments inside the clip accumulation, so every
  downstream timing shifts naturally; info cards ride the overlay track via
  the shared anchor grammar; `manju package` cuts cover.png + teaser.mp4 from
  the current final with .key.json idempotency; an all-disabled spec folds to
  None in the fingerprint (existing projects byte-identical).
- **Preset kits (P3, decision 6 returns):** 8 editable kits (comic/short_drama/
  explainer/novel vertical; trailer/mv 16:9; ad/talking_head) as pure data —
  `manju new --preset` pre-fills project/rules/packaging/story scaffolds and
  never binds; `manju presets [--json]`; qc_focus advisories recorded in
  project.yaml and surfaced by status.

**Verified live:** M0 smoke (build twice → one final, 24/1) + a combined
exercise on the 6-shot sample: intro shifted all shots by exactly its
duration, 7 transition dings on 7 boundaries, ambient spans the full film,
bad SFX anchor skipped + QC-warned with the grammar hint, fixing it minted
final_v3 append-only with the ding at shot start + offset; `manju package`
delivered 1080×1920 cover + 3.0s teaser and skipped both on rerun.
323 tests green (259 + 17 audio + 18 packaging + 29 presets).

**Review (same round): 5-lens adversarial workflow, 65 agents — 18 confirmed
findings, 2 refuted; all engine-material ones fixed:**
- newest-final resolution unified on one NUMERIC resolver
  (Project.newest_final_path) — the render skip and `manju explain` used a
  lexicographic sorted()[-1] that picks final_v9 over final_v10;
- timeline/rules.yaml + timeline/packaging.yaml joined the `manju check`
  safety net (malformed → one-line finding, never a build traceback, FIX-D);
- teaser window validated against the final's real length (past-the-end →
  clean failure, never key-cached; overrun → clamped + warning), cover frame
  clamp made frame-accurate + output verified before key write;
- title_card now starts at the first CONTENT frame past an enabled intro
  (was: burned over the intro card at 0ms);
- SFX/info-card anchors resolving at/past the film's end are skipped
  deterministically + QC warns (was: inaudible/1ms-invisible, silent);
- `manju package` warns when a frame-mode cover or teaser start lands inside
  the enabled intro span; trailer kit's cover.frame_ms moved past its intro;
- auto ASS font size now fits rules.captions.max_chars_per_line into the
  usable width (the declared budget was inert — every vertical kit rendered
  ~11 CJK chars/line regardless); explicit size stays user truth;
- info cards carry their semantic kind to the burn (chapter high / info
  centre / role lower-third); board placeholder + card fonts use the
  project's real aspect (were hardcoded 9:16 / width-based);
- `manju package` catches MediaError/ValidationError as one-line failures;
- OTIO + JianYing skeleton + native drafts now export sfx/ambient tracks
  (were silently dropped); overlapping SFX spread across draft lanes via a
  first-fit allocator (pyJianYingDraft hard-rejects same-track overlap).
336 tests green after the response (323 + 13 review regression pins), +6
exporter tests on the pick.

**Next:** P2 versioning/rollback audit.

**Open issues:** sidechain params (threshold/ratio/attack/release) are fixed,
not yet knobs on MusicRules/AmbientRules; `manju presets` human table uses
str.ljust which under-pads CJK titles (cosmetic; --json exact); voice gain
exercised by unit tests only in the sample (no dialogue in make_sample);
`manju package` cuts from the newest final without a staleness signal vs
current specs (advisory wanted — needs explain plumbing); compiler still
relies on libass wrapping instead of inserting \N at line-budget boundaries
(manual break control); draft ambient beds are trim-to-source, not looped
(drafts have no loop primitive — final.mp4 keeps the real mix).

---

## 2026-07-06 — round O: the remainder — P2 rollback UX, caption breaks, 13 kits, audio polish

**Done (two parallel agent builds + two architect-built cores, merged):**
- **history/snapshot/rollback (P2 headline, closes "compare/rollback/restore"):**
  `manju history` merges events.jsonl with the project git log into one
  actor-attributed feed; `manju snapshot` = labeled git checkpoint (git IS the
  patch engine §3 — no second store); `manju rollback shot` re-selects the
  prior take from the event record (append-only, newer take kept for compare);
  `manju rollback file` = guarded single-file git restore (media/renders/
  exports refused, check runs after, rollback itself an event). Snapshot event
  written BEFORE the commit so back-to-back snapshots converge.
- **Caption line breaks:** break_lines() makes max_chars_per_line real on
  screen (lossless, punctuation-preferring cuts); human cues never re-broken
  (manual ASS re-emit passes verbatim). Closes the round-N review finding.
- **13 preset kits:** +animation 动画短片, film_storyboard 影视分镜预演 (16:9,
  captions off, previz), virtual_human 虚拟人出镜 (persona sheet scaffold),
  product 产品种草 (CTA outro + info-card slots), knowledge 硬核知识长条
  (long-form, chaptered outline). CJK-width table padding (east_asian_width).
- **Audio polish:** ducking knobs (duck_threshold/ratio/attack/release on
  MusicRules+AmbientRules+AudioClip, defaults = the old constants, pinned
  byte-identical filtergraph); `manju package` staleness advisory (recompiles
  current specs like explain, compares the final's key sidecar; degrades
  silently); three rule-based QC audio advisories as info items (no BGM /
  ducking off under speech / ambient suggestion ≥4 content shots).

**Verified:** 390 tests green (342 + 19 + 12 + 17).

**Note for existing projects:** the new AudioClip/rules fields change timeline
fingerprints once on first recompile; projects WITH a BGM/ambient clip also
get one content-key re-render (§4.3-conservative, output identical in intent).

**Still requires the user (cannot be closed from this environment):** one live
paid text-to-video vendor run (needs an API key: fill a provider.yaml + env
var); opening exported drafts in desktop JianYing/CapCut (needs the apps).
Perceptual consistency QC (character drift/scene mismatch detection) remains
gated on a real qc_vision vendor manifest for the same reason.

**Open (small):** voice cost estimates per_call only; crossfade seams remain a
recorded design decision (dip-to-black instead).

---

## 2026-07-06 — round P: CI green push + actionable board + agent-neutral auto

**CI (the blocker):** two CI-only failures fixed — (1) bare `pytest` could
not collect `from tests.…` imports (local `python -m pytest` masked it);
tests/__init__.py makes tests a real package, verified with the bare-pytest
invocation CI uses. (2) tests/test_ticket2.py had the dev container's
absolute repo path baked into two subprocess cwd args — now derived from
__file__, verified from a foreign checkout path. Awaiting the green run
before this entry may claim CI fixed.

**board --serve (owner-directed GUI, DECISIONS #5):** the static board
becomes a live localhost workspace: per-request regeneration, seekable
media (single-range 206), per-take 选用 / per-shot 重做+回滚 / header
构建·质检·打包·快照 buttons with busy overlay + error banner, one mutation
lock (concurrent click → 409), events per mutation, unlock/gc/pack
unreachable, traversal-guarded /media, stdlib-only. Static output pinned
byte-identical. Live-verified over real HTTP on the sample project.

**auto for any agent (DECISIONS #6):** resolver flag → MANJU_AGENT →
project.yaml:agent → PATH probe (claude/codex/gemini/qwen/aider);
{prompt} template grammar substitutes the prompt as ONE argument; playbook
prepend + actor=ai + exit-code propagation pinned by a fake-agent e2e that
needs no real agent installed.

**Tests:** 418 green locally under the CI-faithful bare-pytest invocation.

---

## 2026-07-06 — round Q: the 19-item goal — six parallel builds, integrated

**Map first:** REPORTS/GOAL-COVERAGE.md grounds all 19 items in evidence —
most stood from rounds 1–P; round Q closed the true deltas below. Six Opus
agents built in parallel worktrees; integrated by cherry-pick in dependency
order with two add/add resolutions; 493 tests green consolidated (was 418).

- **Presets reversed (item 17, DECISIONS #7):** exactly blank /
  vertical_ai_video (1080×1920@30) / horizontal_ai_video (1920×1080@24);
  kits fix the FRAME only; a neutrality-guard test bans genre tokens;
  `--preset blank` pinned byte-for-byte to the generic scaffold.
- **Providers (item 5):** ComfyUI adapter (API-format workflow submit →
  /history poll → /view download, node errors surfaced, offline scripted-
  transport tests; API verified against live ComfyUI source) and
  local-command adapter ({out} template, timeout, stderr surfacing);
  doctor coverage via the offline manifest checks.
- **QC + repair (items 8/9/15/16):** garbled-caption (乱码), silent-voice
  (astats RMS ≤ −50dB), clipping (peak ≥ −0.1dBFS, one astats pass per
  file), timeline-conflict checks; repair ops retime/extend(freeze|black)/
  trim/croppad(center_crop|pad_blur) minting NEW takes with lineage, wired
  as `manju repair --op …`; QC suggestions name the matching op.
  **Integration fix:** `repair --auto` read "issues" while the plan writer
  emits "actions" — auto-repair had been a silent no-op against every real
  plan; now pinned by a test that executes a real plan action.
- **Branding (item 18):** logo (corner/size/margin/opacity/window),
  watermark (text/image, translucent), 角标 badge chip, closing CTA chip —
  additive PackagingSpec fields, overlay-track kinds, final-pass burn;
  image inputs indexed AFTER audio inputs so the audio graph is untouched;
  all-off stays byte-identical; logo bytes join the content key only when
  enabled.
- **Board round 2 (item 13):** take-comparison (synced playback), tabbed
  panels (project/subtitles/bible/log/assets/QC), /api/export (8th action,
  same lock), keyboard playback; static board still byte-identical.
- **Misc (items 1/11/12/19):** `.txt/.md` imports route to story/imports/
  (novel→script source); voices.yaml joined the bible enumeration
  (BIBLE_FILES centralized; props already worked — proven by test);
  `manju appearances` (character/scene/prop → shots map + orphans);
  `manju tasks` (ledger view with per-provider spend reconciling to
  status); SKILL.md grew 8 concrete creation workflows (idea→script,
  novel-to-script, script→shots, storyboard/order, hook/ending, dialogue
  under locks, pacing, repair loop).

**Verified live:** board panels + export API over real HTTP on the sample
project; compare markup on multi-take shots.

**Notes:** agents hit heavy CPU contention running six suites concurrently —
several verified in chunks; the consolidated single run (493 passed) is the
authoritative result. OMP_THREAD_LIMIT=1 tames tesseract in constrained
environments.

---

# ——— Parallel line merged (round R) ———
# The entries below (R1–R27) come from the sibling branch
# claude/project-optimization-gui-jzr1ml, which developed in parallel from
# the round-M base (54bdd54) while rounds N–Q happened on this line. Round R
# ported its features onto this line (adapted, not cherry-picked); these
# entries are preserved verbatim as that line's history.

---

## 2026-07-05 — rounds R1+R2: `manju gui` workbench + process build lock

**Done (R1, GUI):** `manju gui` — stdlib-only local web workbench as the
THIRD client of the same engine core (§1-⑦ deferral revisited, user-directed;
DECISIONS.md #5): consolidated `/api/state` (read-only, no ffprobe, poll-safe),
strictly serialized job runner (build/redo/voice/qc FIFO — honest to the
engine's single-writer design), select/lock as the same one-line text edits
the CLI writes, `Range`+ETag media serving behind an allowlist with a
RESOLVED-path re-check (the test suite caught a live `media/../project.yaml`
bypass before it ever shipped), DNS-rebinding Host guard, per-run CSRF token,
strict CSP, vanilla-JS front-end (textContent-only DOM, adaptive 1.5s/5s
polling, paused when hidden). Browser-codec reality handled: `/preview/<rel>`
lazily transcodes non-browser-safe takes (.mkv/.flac/ProRes…) once into the
disposable `.manju/webpreview` cache (media/webpreview.py, atomic tmp+replace,
(path,mtime,size)-keyed) and degrades to raw bytes. Dangerous ops (unlock,
gc --hard, pack, arbitrary-path import) absent from the surface, mirroring
MCP (§5). Docs: docs/GUI.md (architecture, full API, threat model, zh
quickstart).

**Done (R2, process lock):** `.manju/build.lock` (runtime/buildlock.py):
O_EXCL atomic create + holder JSON (pid/actor/started/hostname) + heartbeat
thread + staleness rules (dead pid on same host, or mtime age) + steal-once —
closing the dual-actor race §5's value locks never covered (human terminal ×
AI session × GUI all mutating at once). Wired engine-side so every surface
gets it: run_build (dry-run deliberately LOCKLESS — estimates stay available
mid-build), redo_shot, and the CLI/GUI voice+qc+gc paths. Contention is a
one-line ok=False/BuildLocked finding, never a traceback; `manju status` and
`/api/state` surface the active holder.

**Assessment:** REPORTS/OPTIMIZATION-ASSESSMENT.md — the convenience/
real-world audit behind these rounds (P0s: this lock; media write durability
— fix in flight). 303 tests green at round end (was 251).

**Next:** durable media writes (tmp+replace for segments/finals, cache
integrity gate); hygiene cheap wins (gitignore scaffold, ledger COUNT,
truncated-final honesty); GUI shot editor with check-on-save.

---

## 2026-07-05 — round R3: media write durability (P0) + hygiene wins

**Done (durability):** media now follows the same temp+replace discipline as
text (§3): every ffmpeg artifact destined for a durable location (segment
cache, proxy, final) encodes into a sibling `.{name}.tmp-<pid>-<rand>.mp4`
(dot-prefixed — invisible to the `final_v*` globs; real extension last so
muxer inference holds) and lands via `os.replace` only on success
(media/ffmpeg.py `atomic_output`). Cache hits pass a cheap integrity gate
(header ffprobe) — a truncated segment is evicted and rebuilt with a warning
instead of poisoning every future final. The proxy no longer destroys its
previous good copy at encode start, and its stale `.key.json` can no longer
vouch for corrupt bytes (old sidecar removed only at swap time); final
sidecars are written strictly AFTER the mp4 is in place. RED-proofed: with
the pre-fix code restored, 5/9 of the new tests fail exactly along the
claimed failure modes (planted garbage reused; crash-partial persisted;
proxy false-reuse).

**Done (hygiene):** scaffold .gitignore covers the gen-media extensions
MEDIA_EXTS accepts (.mkv/.webm/.m4v/.jpeg/.m4a/.flac), reports/frames/ and
board.html; run ledger counts via SELECT COUNT(*); `status` flags a latest
final that lacks its content-key sidecar ("crashed render?" honesty);
`find_duplicate_import` (size fast-path + sha256) backs a duplicate-content
advisory on import — never destructive, imports stay sacred (§3).

**Next:** ask_before engine gate (§8.3); GUI v2 (shot editor with
check-on-save + revert, upload, git panel, timeline strip, doctor).

---

## 2026-07-05 — rounds R4–R6: ask_before engine gate + GUI v2 + co-presence

**Done (R4, ask_before → engine gate, DECISIONS #6):** a non-dry-run whose
plan estimates cost > 0 while `expensive_generation` ∈ ask_before now stops
engine-side as `ok=false, waiting_user=true` (uniform for every actor) until
an explicit yes: CLI `--yes`, MCP `assume_yes` (schema documents "only after
relaying the estimate to the human"), GUI body flag. Dry-run stays lockless
AND gate-less, so 问前先 dry-run keeps zero friction. SKILL.md playbook
updated to the enforced flow (dry-run → 问人 → assume_yes;构建锁行为;
via:"gui" events are human decisions; latest_final_note handling).

**Done (R5, GUI v2 surface):** check-gated text editors over the SAME truth
files the CLI edits — shots (`/api/shot/<id>` GET raw text / POST write-
verbatim + full `manju check` + auto-revert on any new error, lock violations
included), bible files and timeline rules (same `_gated_save` core); shot
reorder (`/api/index`, permutation-validated — the cut order stays one
reviewable YAML line); browser upload into imports/ (append-only, collision-
suffixed, streamed via .manju tmp; MCP still excludes import by design — the
browser can't reach arbitrary filesystem paths, so the GUI twin is safe);
git panel endpoints over new `core/gitops.py` (status/diff/log read-only +
commit as the ONLY write; no checkout/reset/revert on any manju surface;
the git_commit event is appended BEFORE committing so it rides inside the
commit); `/api/doctor` over new `build/doctor.py` (extracted from the CLI —
one probe implementation for both surfaces); `/api/timeline` passthrough;
front-end v2 (editor dialogs, dropzone, timeline strip, doctor/git panels,
build-lock chip).

**Done (R6, co-presence + honesty):** `/api/watch` long-poll on a cheap
project fingerprint (stat-walk over truth text/timeline/QC/finals/events/
take-dirs + job revision — never probes media): an AI edit appears in the
browser in ~0.5s. `/api/state` is served from a fingerprint-keyed cache, so
polling large projects costs one stat-walk until something changes.
`/api/proposals` lists the AI→human channel (§5) so requests are SEEN.
`manju gui --readonly` refuses every mutation (LAN review sharing).
Builds refresh an existing board.html (a stale board lies — §1-⑦);
gc also clears the webpreview cache and no longer leaves its own lock
behind. Pre-commit adversarial review + per-take thumbnails in flight.

---

## 2026-07-05 — round R7: workspace mode + spend-gate closure + take thumbs

**Done:** `manju gui --workspace <dir>` — one server over every project in a
directory, ONE active project switchable via `/api/switch` (state cache
reset; in-flight jobs keep the project they closed over; the stale-tab
media caveat is documented, not hidden). `/api/events` gains actor/action
filters. Per-take lazy thumbnails: `/thumb/<rel>` frame-grabs (50% seek,
sub-0.5s clips fall back to first frame — found live against ffmpeg 6.1),
≤320px, cached beside the previews; every take card now reads at a glance.
Spend-gate hole closed: a priced `manju redo` / `manju voice` raised no
gate (only build did) — both now raise the same `waiting_user:` line via a
shared `spend_gate` helper unless `--yes`/`assume_yes`; MCP redo schema
documents it; SKILL.md notes the error-form for envelope-less commands.

**Next:** front-end v3 (watch-driven refresh, spend-confirm banner,
readonly awareness, proposals panel, bible/rules editors, reorder buttons,
workspace switcher, review keyboard mode, image-take rendering fix);
pre-commit adversarial review findings.

---

## 2026-07-05 — round R8: adversarial review response + build progress

**Done (review, P1s):** the pre-commit adversarial review found and we fixed:
(1) gitops CONTAINMENT — a project without its own .git nested inside an
outer repository leaked the enclosing tree (status listed foreign files,
`/api/git/diff` served content from OUTSIDE the project over HTTP, and
commit_all staged the whole outer repo — reviewer-reproduced): `is_repo` now
requires `rev-parse --show-toplevel == project root`, every gitops function
guards on it, and user pathspecs that resolve outside the root are rejected;
(2) MCP `qc` bypassed the process build lock SKILL.md promises — now held.

**Done (review, P2s):** failed GUI commits append `git_commit_failed` (the
audit log can't claim a commit that never happened); budget-breaker message
no longer says "waiting_user" (it isn't approvable); queued→running now bumps
the job revision so the cached state can't show a running build as queued;
`/api/watch` capped at 8 held threads (saturated watchers answer
immediately); GUI.md GET table unsplit + payload/threat-model drift fixed
(assume_yes on the build row, build_lock/latest_final_note/thumb fields,
editor-vs-build race honestly named as out of lock scope).

**Done (progress):** `run_build(on_phase=…)` publishes coarse phases
(check/generate/voice/compile/captions/render:<target>/qc/exports) —
advisory, exception-proof; GUI jobs carry `progress` and the build job wires
it through, so a long render is no longer an opaque "running" badge.

**Review verdicts kept:** state-cache tearing impossible (GIL snapshot);
upload single-decode + basename + mutex-held collision loop; fingerprint
covers engine writes (atomic tmp+rename bumps the take-dir mtime); gate
ordering budget→dry-run→ask_before correct.

---

## 2026-07-06 — session close: rounds R1–R9 (GUI workbench arc)

**Shipped this session (one push-ready branch, each round committed):**
R1 `manju gui` — stdlib local web workbench as the third client of the
unchanged engine core (§1-⑦ revisited, DECISIONS #5). R2 process build lock
(.manju/build.lock, DECISIONS gap the design's own §3 comment promised).
R3 media write durability P0 (atomic tmp+replace for segments/proxy/final,
cache integrity gate; RED-proofed). R4 ask_before → engine-enforced spend
gate (DECISIONS #6) + SKILL.md playbook update. R5 GUI v2: check-gated
editors over the same truth files (shots/bible/rules, auto-revert), upload,
git panel (read-mostly, commit-only), doctor, timeline strip. R6 co-presence:
/api/watch long-poll fingerprint, fingerprint-cached /api/state, proposals
surface, --readonly. R7 workspace mode (multi-project switcher) + spend-gate
closure over redo/voice + events filtering. R8 adversarial-review response:
gitops CONTAINMENT P1 (nested-repo leak), MCP qc lock, phase progress on
jobs. R9 per-take director notes + the 6-product UX study with adoption
shortlist (REPORTS/UX-STUDY.md).

**Honest state:** front-end v3 (live-watch client, spend-confirm banner,
review keyboard, workspace switcher UI, take-note UI) was cut short by an
API session limit mid-agent; the committed page is the verified v2 plus the
workspace switcher (browser-smoked: sections render, estimate works, no JS
errors), and EVERY v3 server endpoint is live and tested — the remaining
work is page-only wiring, listed in UX-STUDY.md's shortlist alongside the
ten adoption candidates. Figma/Obsidian study legs also lost to the limit.

**Where the next session starts:** UX-STUDY.md shortlist #1–#5 (price on
the trigger, why-stale field diff, state filter chips, one-key verdicts,
failure auto-expand) — all small, all engine-true.

---

## 2026-07-06 — round R10: UX shortlist lands in the workbench

**Done:** four of the study's top five, live-verified in Chromium:
#1 price on the trigger — a silent dry-run keeps the 构建 button honest
("构建 ≈5 CNY"), refreshed on control change and every project fingerprint
move, never in readonly; #3 state filter chips over the shot grid (hidden
when only one state exists; a vanished filtered state resets itself);
#4 one-key verdicts — 👍好/👎弃 are just take_notes values (one reviewable
YAML line; clicking the active verdict clears it), plus the 📝 free-text
note editor with the Frame.io timecode convention (an "mm:ss …" note is
click-to-seek on the take's player); #5 the newest failed job auto-expands
its error (GHA pattern). Discovered en route: the "lost" front-end v3 had
in fact landed watch/spend-confirm/readonly/reorder/truth-editor chips
before the API limit killed its reporter — browser-smoked and kept.

**Open (#2):** the why-stale FIELD diff needs a spec snapshot in future
take sidecars (today only the hash is stored — there is nothing to diff
against); scoped for the next engine round.

---

## 2026-07-06 — round R11: why-stale names the fields (UX-STUDY #2 closed)

**Done:** every generated take's sidecar now carries `spec_snapshot` — the
canonical spec_payload at generation time (one enrichment point:
Provider._register; manual imports stay snapshot-free, they are never
stale). Staleness evaluation diffs the snapshot against the current payload
and the note becomes "spec changed: camera.shot_size, scene_bible.lighting"
instead of the bare hash shrug — evidence that propagates untouched through
status, the board, `manju explain`, and every GUI shot card (they all render
the note). Pre-snapshot takes degrade to the generic note. diff_spec_fields
is a pure sorted dotted-path diff in core/spec.py.

---

## 2026-07-06 — round R12: version stack + recipe-reuse redo

**Done:** UX-STUDY #6 and #7. The 成片预览 panel now stacks every
`final_v*` newest-first (append-only lineage made visible): one-click links,
size, a 当前 (current) chip, and a ⚠ 无内容键 flag on any final missing its
`.key.json` sidecar (crashed-render honesty at the exact place someone would
grab the file). Take cards carry their generation seed, and a ⟳ button
re-runs the SAME provider+seed as an append-only redo — the recipe travels
with the output (Runway pattern), selection untouched.

---

## 2026-07-06 — round R13: doctor states the operational facts

**Done:** `manju doctor` (and /api/doctor) now reports an active/crashed
build lock (holder pid/actor/since + the exact file a human may remove —
informational, never gates ok) and the reclaimable cache total
(segments/proxy/webpreview → "manju gc"). The place people look when
"nothing works" now names the two most common reasons.

---

## 2026-07-06 — round R14: top of the 8-product synthesis lands

**Done:** COMPETITIVE-UX-STUDY.md (all 8 products + machine synthesis,
recovered legs included) committed with UX-STUDY.md cross-linked; then its
P0/S recommendations: board.html now stamps the generating project
fingerprint (meta tag + footer; `board_fingerprint()` reads it back — a
stale board is DETECTABLE, Descript Export→Update pattern); BuildResult
gains `skipped` (FRESH/MANUAL cache hits, finally populated) and
`saved_cost` (what regenerating the FRESH shots would have cost — Nx
replayed-hits pattern, advisory, never fails a build); editor 409s carry
the reverted-to truth text for the conflict banner (Figma pattern), and
`/api/schema` serves the truth-file JSON Schemas (§12) as editor-validation
groundwork. Remaining from the synthesis top-5: unread-first triage and the
validation/conflict UI wiring (page-side).

---

## 2026-07-06 — round R15: conflict banner, unread triage, savings display

**Done (front-end, Opus-drafted / chief-engineer-integrated):** editor 409s
now render a 对比真相 diff (buffer vs reverted-to truth, approximate
line-diff, labeled) with one-click 以真相为底重填 (confirm-gated) — the
check-gate keeps protecting truth while typed work survives (Figma
pattern); unread-first triage via a localStorage per-project review
snapshot — 新 chips on unseen takes, 未阅 N counter, 标记已阅 button
(local preference, deliberately not readonly-gated); cache savings
surface in estimates and done-build rows (跳过 N cache hits + 缓存命中省
≈X when priced). Browser-smoked end to end: 409 → diff → reload-truth
restored the exact disk bytes; a server-side new take flipped 未阅 1.

---

## 2026-07-06 — round R16: manju spend (§8.3 事后 made visible)

**Done (Opus-drafted module / chief-engineer wiring):** `build/spend.py`
spend_report — ledger-authoritative totals by provider and by shot with the
sidecar-derived fallback (§3 disposability honored: any SQLite failure
degrades, never raises), recent runs newest-first, budget context, honest
"source" marker. Surfaced as `manju spend [--json]` and GET /api/spend.
Live-smoked on the demo project (sidecar fallback path). The third §8.3
guardrail layer (事后逐笔记账) now has a human surface to match the first
two (dry-run estimates, engine ask_before gate).

---

## 2026-07-06 — round R17: keystroke-time editor validation

**Done (chief-engineer endpoint / Opus UI):** POST /api/validate — pure
parse + pydantic model check over the same truth models (no write, no full
check, no lock scan; the gated save stays the authority) — and the editors'
debounced (600ms) live strip: ✓ 校验通过 / red error lines / 校验中 only
past 300ms (anti-flicker) / offline latch until the next keystroke;
sequence-guarded against stale replies. Browser-proven: typing
`duration: nonsense` turns the strip red with the pydantic finding, fixing
it returns the ✓. Kills the submit-time-409-only feedback loop the
synthesis called the GUI's most trust-damaging moment (VS Code
settings.json pattern; COMPETITIVE-UX-STUDY P0 #1).

---

## 2026-07-06 — round R18: A/B compare, fit-5s review, events depth + pack hygiene

**Done (Opus UI / chief-engineer engine+integration):** 「对比 (Compare)」
overlay for any shot with ≥2 video takes — side-by-side players with take
selectors (selected vs newest-other default), sync-play with drift
correction, 等长回放 fit-5s (playbackRate = duration/5 clamped [0.5,4],
Resolve Cut-page pattern), and 选用左/右 writing the one-line selection;
mounted outside #shots so polling can't destroy it (Frame.io comparison
viewer). Events feed gains 「更多」 (100-row fetch). `manju pack` skips the
rebuildable segment/proxy caches by default (--full keeps; skipped MB
reported) — backups stop dwarfing their own truth. Browser-proven with real
2s/8s clips: rates [0.5, 1.6], selection landed on disk.

---

## 2026-07-06 — R19 note: voice-preview (试听) deferred with evidence

Probed live Edge TTS from this environment for the CapCut-pattern 试听
button: blocked by the sandbox's TLS-intercepting proxy (aiohttp trusts
certifi only — SSL_CERT_FILE/REQUESTS_CA_BUNDLE ignored), i.e. an
environment limitation, not a product one (round K live-verified the same
wire in a clean env). Deferred rather than shipped unverifiable; the
endpoint design (short sample into .manju/webpreview, never a take, job-
borne, graceful "TTS 不可用") is recorded here for the next session.

---

## 2026-07-06 — round R19: static board reaches GUI parity on review notes

**Done (Opus / chief-engineer gated):** the shareable board.html now renders
per-take 👍好/👎弃 verdict chips and 📝 notes (escaped, 80-char display cap,
full text in title) and warns when the SELECTED take is 弃-marked — the
offline artifact carries the same review signal as the live workbench.
XSS-proofed by test (script tag in a note arrives escaped).

---

## 2026-07-06 — round R20: docs coherence sweep

**Done (Opus, code-verified):** GUI.md route tables now cover all 36 server
routes (spend/schema/validate/projects/switch added; events filters, job
progress field, 409 "current" contract, state payload fields documented);
README CLI table complete against all 28 commands (spend + schema rows,
pack --full); SKILL.md points agents at `manju spend --json` for 花费
review. Every claim greps back to the handler code.

---

## 2026-07-06 — round R21: spend delta — estimates persist into the ledger

**Done (Opus under chief-engineer schema spec):** the runs ledger gains a
nullable `estimated_cost` column (idempotent ALTER upgrade — §3 disposable,
degrades on failure, never coerces None to 0); builds and redos record the
planned estimate alongside the actual; `spend_report` (and thus
`manju spend` + /api/spend) carries per-run estimated_cost plus
estimated_total and delta when any estimate exists — the calibration
column for the ask_before gate. Proven end-to-end: a real gated build's
generated take carries its 2.0 estimate in the ledger row.

---

## 2026-07-06 — round R22: adversarial review response (R10–R21 range)

**Fixed (review-found):** estimate over-count — the ledger writes one row
per take but stamped EVERY row with the full per-shot plan estimate, so a
multi-take run inflated estimated_total ×N (reviewer reproduced 2×);
estimates now attribute to exactly one row per shot (unit-proven).
Sidecar-fallback currency no longer treats a None as "mixed";
/api/validate is exempt from the readonly gate (it is pure — readonly
editors keep live checks); board_fingerprint's docstring now states the
honest comparison contract (null-runner fingerprint, not the GUI's live
one, which folds the job revision in).

**Recorded, deferred:** cloud runs don't yet carry estimates into the
ledger (CloudProvider self-records at poll time without the plan figure) —
today's delta only covers local runs; spec_snapshot duplicates bible
excerpts per take (advisory bloat); a future provider overriding spec_hash
while the snapshot auto-captures could make why-stale fall back to the
generic note. Reviewer verified safe: filterChips recursion bounded,
saved_cost/estimated_cost disjoint under regen_stale, ledger migration
idempotent against a genuinely legacy table, compare overlay lifecycle.

---

## 2026-07-06 — round R23: cloud runs carry their estimates (delta gap closed)

**Done (Opus under chief-engineer spec):** GenerationRequest gains
estimated_cost (None-honest); both build and redo populate it from the plan;
CloudProvider._on_success threads it onto the cloud ledger row — the spend
delta now measures what it claimed to: cloud estimate vs cloud actual, not
just free local runs (closes the R22 review's SECONDARY finding).
Scripted-transport-proven end to end.

---

## 2026-07-06 — round R24: live acceptance《边境电台》+ findings closed

**Done:** a second full acceptance film driven purely through the CLI/GUI in
a clean scratch project: scaffold → bible/3 CJK shots → check → dry-run →
build (caption_card offline chain, final_v1 1080×1920@24, QC 通过) → every
session feature asserted live (why-stale field naming, spend ledger with
persisted estimates delta 0.0, take-note 好 chip + fingerprint stamp on the
board, pack skipping the planted segment cache, second build reusing the
final via content key with skipped/saved_cost populated, doctor
gc_reclaimable, GUI zero-pageerror with the stale note and verdict chip
visible on the cards). Fresh-clone suite verification: 438/438.

**Findings closed:** `manju status --json` (the 30-second takeover surface)
now carries `shot_notes` — the why-stale evidence rode only on
explain/MCP/GUI before (acceptance finding #1); the GUI serves a real
favicon (finding #4: the one console 404 every load). Recorded as expected
behavior: build auto-select writes into shot YAML with its warning (#2),
doctor --json strips the human glyph line by design (#3), offline fallback
terminates at caption_card without a still to animate (#5).

---

## 2026-07-06 — round R27: the dev-loop wave (watch / follow / scaffold / hook)

**Done (three parallel Opus modules under chief-engineer contracts + CLI
wiring):** `manju watch [--once]` — fingerprint-driven check loop
(watchexec/jest-watch pattern; first tick immediate, re-checks only when
truth moves, torn mid-editor writes never kill it); `manju events --follow`
— binary-mode live tail of the collaboration log (§10 co-presence from a
second terminal; torn-write buffering, truncation recovery, CJK-safe byte
offsets); `manju new --shots N` — commented skeleton shots that pass check
out of the box (scaffolds are not authorship; comments survive via raw text
write); `manju new --check-hook` — opt-in pre-commit hook running `manju
check` (husky pattern: broken truth cannot enter history; never clobbers a
foreign hook). All four live-smoked end to end in a scratch project.

---

## 2026-07-07 — round R: the two lines unified — R1–R27 strengths ported onto main

**The fork:** claude/project-optimization-gui-jzr1ml developed 38 commits
(R1–R27) in parallel from the round-M base while rounds N–Q happened here.
Round R ported its strengths onto this line as ADAPTED features (not git
merges) via four Opus port agents + integrator conflict resolution:

- **Engine safety:** cross-process build lock (.manju/build.lock; contention
  = one-line ok=False, dry-run lockless), media write durability (temp+
  os.replace for segments/proxy/finals, ffprobe-gated cache hits — truncated
  debris can no longer poison finals), gitops as THE single git wrapper
  (history.py delegates; §1-② containment everywhere), MCP qc lock.
- **Spend governance:** ask_before becomes a real engine gate (build/redo/
  voice; WaitingUser + --yes/assume_yes; the R7 redo/voice spend holes
  closed), `manju spend` (money view; `tasks` stays the queue view — shared
  query layer so numbers can't drift), estimates persist into the ledger
  (additive nullable column, legacy rows read as None) for estimate-vs-
  actual deltas, cache-savings surfaced.
- **Dev loop + provenance:** `manju watch` (read-only fingerprint check
  loop), `events --follow`, `new --check-hook` (opt-in pre-commit check),
  doctor module-ized (all probes kept + build-lock/gc-reclaimable), why-
  stale names the changed FIELDS (spec snapshots in take sidecars, ≤32KB,
  additive — manual takes stay snapshot-free), `redo --from-take` (recipe
  reuse), import content-dedup (advisory).
- **GUI workbench:** `manju gui` — async jobs, watch-driven refresh,
  spend-confirm (lights up against the real gate), per-take director notes
  (take_notes in shot YAML = human truth; 好/弃 verdict chips reach the
  static board too), A/B compare, workspace switcher, web previews, git
  panel over unified gitops; unlock/gc unreachable (403). Coexists with
  `board --serve` (lightweight review) — roles documented in docs/GUI.md.

**Integration seams fixed by hand:** run_build/redo lock-wrapper now threads
assume_yes/on_phase into the phases body; redo composes lock + spend gate +
from_take; single gitops/doctor kept where three agents ported copies.

**Verified:** 693 tests green consolidated (was 493); `manju gui` live-smoked
(state/doctor/spend=sidecars/git panel/403s). Their R-line PROGRESS/DECISIONS
/reports preserved verbatim under fork notes.

---

## 2026-07-07 — round S: the 11-item workbench goal — 7 agents, two waves

**Item map (all 11 closed):**
1. Provider management — `manju providers` group (scaffold templates per
   adapter, offline probes + fix hints, enable/disable honored by the chain,
   secret masking) + GUI providers page.
2. First-run guidance — GUI onboarding checklist (6 steps, live
   done-detection, per-user+project dismissal in ~/.manju/gui_state.json).
3. Batch ops — redo_batch/voice_batch: ONE lock hold, ONE aggregated spend
   gate, per-shot isolation, loud exclusions (manual/locked/off-state);
   GUI multi-select + bulk bar. Batch select deliberately NOT built (a
   selection is a per-take human judgment — recorded stance).
4. Workbench completeness — docs/WORKBENCH.md capability matrix (the
   deliberate CLI-only containment surface + CLI↔GUI map + six interaction
   invariants); S8a filled the matrix rows: reorder, snapshot/rollback
   buttons, tasks view, YAML editors w/ validate-on-save, new-project
   dialog.
5. Explanation before generation — GUI plan modal on EVERY generative job
   (dry-run first: per-shot why/provider/est, routing-aware, 免费/本地 badge
   on zero-cost; confirm passes assume_yes).
6. Human visual QC — /review page: sequential queue, selected take large,
   QC findings + frames, 好/弃/换用/重做/修(repair ops)/note, j/k/g/x/space
   keyboard, progress meter; verdicts persist as take_notes (human truth).
7. Reference reliability — providers/refs.py single resolver (tier lineage),
   manifest refs: schema (base64/url/multipart; Runway data-URI vs Kling raw
   base64 verified), real ComfyUI /upload/image, {video_ref}, pre-submit
   validation (no paid call with a broken ref), ref_delivery in every
   sidecar, tier-mismatch warnings.
8. Asset library — core/library.py (~/.manju/library, content-addressed,
   dedup, tags, thumbs) + `manju lib` + GUI library page (use-into-project
   honors no-overwrite).
9. Routing strategies — providers/routing.py: routing.yaml match rules +
   4 built-in strategies + user strategies, explicit > rules > fallback
   chain (absent file = byte-identical behavior, pinned), `route explain`,
   GUI routing view + strategy picker.
10. Failure clarity — core/failures.py structured records (step/subject/
    cause/evidence/hint/log_path) wired through ffmpeg/providers/build/
    voice/export + degradations as info; `manju failures` rustc-style,
    status line, BuildResult.failures, ledger failure_id cross-ref; GUI
    failure cards with retry.
11. Version compare — build/compare.py per-shot diff (take/captions/audio/
    packaging + why from events), final_vN.timeline.json persisted at
    render (idempotency untouched), degraded pre-S path honest; `manju
    compare` + GUI compare page w/ synced players.

**Verified:** 867 tests green consolidated (was 693 pre-S; +174); all six
GUI pages + plan/onboarding/failures live-smoked over real HTTP (POST guards
verified working: token + DNS-rebinding 403s).

---

## 2026-07-07 — round T: finish WITHOUT leaving Manju

**The corrected goal (owner clarification):** backend tools are capability
sources, never the problem; the problem is having to OPEN JianYing/CapCut to
finish a normal video. Round T closed those moments (6 agents, 2 waves; the
pre-clarification "minimize binaries" agents were cancelled/kept only where
still consistent — the T1 binaries agent was user-cancelled and dropped):

- **音量/BGM (audio finishing):** BGM in-point + fade-in (music/ambient),
  per-shot footage gain/mute folded into the segment cache (one clip's change
  re-encodes exactly one segment), mixer read/apply API — surfaced as the
  /mixer page (sliders, BGM picker from imports+library with 试听, in-point,
  fades, sfx list editor) and per-clip audio in the /edit inspector.
- **裁剪 (cropping):** frame-accurate set_inout repair op; VIRTUAL mode
  (default) hardlinks the source with a sidecar window — spare head/tail
  handles retained; frame/scrub-strip service (.manju/frames, cached).
- **转场/调色 (polish):** handle-aware REAL crossfades (xfade family) as
  content-addressed boundary segments — total duration exact to the frame,
  applied only with real handles (virtual trims provide them — proven e2e
  applied vs dip-to-black control), degrade recorded per boundary in
  final_vN.transitions.json; color looks (warm/cool/bw/film/vivid ×
  intensity) in the final pass, content-keyed, intensity-0 byte-identical.
- **字幕 (subtitles):** /subtitles editor — inline cue text/timing edit,
  add/split/merge/delete, explicit manual-takeover confirm on first edit
  (generated.srt kept for comparison; 还原自动字幕 reverts), safe-area
  preview over a real frame.
- **排序/卡片/封面 (order/cards/cover):** /edit timeline strip (thumbs,
  reorder, trim scrubber, duration, transition default picker with
  per-boundary applied/degraded markers, look picker with before/after
  frames); /packaging v2 forms with live card previews; cover picked off a
  frame strip writing cover.frame_ms; teaser two-thumb range.

**Honest boundaries recorded in-page:** per-boundary transition OVERRIDES are
not a data path yet (global default + per-boundary state display);
generative-take handle regeneration deferred (virtual-trimmed footage is the
handle source today).

**Verified:** 972 tests green consolidated (was 867; +105); /edit /subtitles
/mixer /packaging live-smoked over real HTTP incl. a mixer apply that wrote
rules.yaml and reported its re-render verdict; applied-xfade e2e in-suite.

## 2026-07-07 — round U: productization — 分镜/资产/提示词/实验室/导出中心/导演/新手模式

Twelve parallel Opus agents (6 engine + research + 5 GUI) + inline architect
work, integrated one cherry-pick at a time, CI green at every pushed tip.
REPORTS/ROUND-U-REFERENCES.md (verified vendor limits: Kling 4 refs /
image+image_tail, Runway 3 / position first|last, Vidu 7 / positional pair,
PixVerse 3→7 / integer img_ids; Frame.io/GitHub/AME status vocabulary; the
§10 glossary) drove the GUI specs.

- **Per-boundary transitions (inline):** rules.transition_overrides — out-edge
  keyed, null/cut = hard cut, compiler places verbatim, fingerprint folds only
  when non-empty (byte-identity pinned), QC advisories on unknown keys/types;
  /edit seam popover writes it (½-clip validity, 恢复默认, event).
- **Native cut depth (UJ):** /edit multi-track lanes (主轨道/字幕/音频) off the
  compiled timeline, playhead + zoom, per-clip waveforms (media/waveform.py:
  showwavespic PNG + astats RMS buckets, frames-style cache), subtitle/voice
  sync hints (cue-over-silence / speech-without-cue / cue-early; honest
  degrade), 补拍手柄 handle rebuild = extended-duration redo + virtual-trim
  centering behind cost confirm (advisory when provider lacks duration).
- **分镜工作台 (UG):** /storyboard REPORTS-§2 table (角色 chips via asset
  matrix, 来源=routing resolution, 状态 vs 审批 as orthogonal chips), additive
  three-state review synced to legacy approved (spec-hash pinned), inline
  edits lock-respecting 409, lock-only GUI (unlock stays CLI), batch
  approve/lock.
- **资产矩阵 + @提及 (UA):** core/assets.py read model over the bible (aliases/
  relations/default_position/locked_fields/appearances), mentions.py
  registration aid (--check/--apply, locks respected), QC advisories.
- **提示词工作台 (UB):** promptlab bundle == the build's own assembly (4
  prompts, refs lineage, provider trace, cost) + single-action checks (CJK
  clause/motion/duration heuristics, deterministic split suggestions).
- **参考图预算/清洁度 (UC):** manifest limits.max_ref_images; deterministic
  allocation (selected/省略 + 中文 impact), delivery + lineage integration,
  byte-identical without limits; local ffmpeg heuristics (busy background /
  lighting conflict / scale) + honest needs_vision slots.
- **语音修复 (UD):** repair --op voice — keep footage, regen via build's TTS
  resolution, proportional cue realign (manual cues untouched + advisory),
  content-key remix, audio_repaired sidecar + failure records + dry-run.
- **质量模式/可读路由 (UE):** build --mode quality|balanced|speed (strategy
  bias + retries + bounded parallel submission; ask_before/budget/lock
  semantics preserved, deterministic commit order); routing.yaml tiers
  (draft/review/key_shot) + ShotSpec.tier (outside spec_payload — tagging
  never restages); manju routing explain.
- **分镜图/关键帧 (UF):** 4/9-panel xstack boards (cached), ShotSpec.keyframes
  (byte-identical default), first_last_frame capability + configurable field
  mapping (Kling/Runway/multipart styles) in generic_cloud with delivery
  lineage, breakdown_action beats + --scaffold gating.
- **导出中心 (UH):** build/exportstatus 9 deliverables × 上新/待更新/缺失/
  有问题/待人工确认 with honest basis lines; desktop drafts capped at
  待人工确认 until a human marks verified (reports/verifications.jsonl,
  hash-bound — regenerate flips it back); /exports page + manju exports off
  one engine.
- **导演助手 (UL):** the six-step contract as a persistent object
  (reports/proposals/*.yaml): propose (whitelisted actions, plan.py-identical
  costs, state fingerprint expiry) → confirm (separate, never implied) →
  execute (auto-snapshot, first-failure stop, diff) → suggest next; CLI +
  4 MCP tools + /director page.
- **新手/专业 + 白话 (UK):** top-level mode switch (fresh→新手, existing→专业;
  hide-never-delete, every page URL-reachable), gui/glossary.py as the §10
  single source (生成来源/版本/待更新/兜底/智能派单/制作台账/配套信息/成片清单/
  质量检查/修复方案…), first-appearance tooltips + 显示专业术语 toggle.

**Honest boundaries:** external real-world validation stays user-side — a paid
video vendor run (needs an API key), desktop 剪映/CapCut draft opening (export
center tracks both as 待人工确认 with a mark-verified flow), and visual-
consistency QC (the qc_vision slot needs a vision vendor; ref cleanliness
person/outfit checks emit needs_vision advisories until then).

**Verified:** 1263 tests green consolidated (was 972; +291); /storyboard /lab
/exports /director /edit live-smoked over real HTTP — including a per-boundary
override POST that landed in rules.yaml, a storyboard approval that persisted
three-state review, and mode/glossary toggles; CI green at every integration
push.

## 2026-07-07 — round V: from tool to studio — 技能库/创作漏斗/多集/驾驶舱/剪辑v2/agent质检

Two research agents (REPORTS/ROUND-V-REFERENCES-1.md: verified SKILL.md
conventions, the amateur-vs-pro lesson corpus with hard numbers, LTX/即创
funnels, 短剧 conventions incl. 卡一/卡二/卡三 + 红果三层次拆解法, Netflix-
grounded QC criteria; -2.md: the 6-key universal editor keymap, honest-undo
for git-backed truth, play-the-final playback, cockpit block layout, the
HUMAN|AI|SYSTEM polish audit) then eight builders, integrated one cherry-pick
at a time, CI green at every push.

- **技能库 (items 1+6):** core/skills.py — three-tier resolution (project >
  ~/.manju/skills > bundled), tolerant frontmatter, progressive disclosure:
  `manju auto` injects the core protocol + an INDEX; agents pull full bodies
  via `manju skills show` / MCP skill_show. Fourteen skills authored from the
  research corpus (叙事节奏/分镜设计/提示词工艺/人设一致性/字幕规范/声音收尾/
  封面标题/质检判读/分集拆解/剧集设定集/修复闭环/创作漏斗/技能编写 + the
  evolved core manju orientation), each with decision tree + ≥3 before/after
  examples + checklist + failure catalog + Manju 落地 mapping.
- **Agent-eyes 视觉质检 (item 6):** qc/agent_review.py — `manju qc brief`
  packages review frames + shot context + the visual-qc-review criteria
  pointer; `manju qc verdict` folds hash-bound [AI判读] findings into run_qc
  (regenerated take → verdicts honestly 过期); the vision-vendor slot
  messaging retired — the driving agent's own image understanding is the
  reviewer, the skill carries the A–J standards.
- **创作漏斗 (item 2):** build/funnel.py — seven stages as data with honest
  done-predicates (scaffold ≠ done; plan stage = the approve-before-spend
  gate); `manju create` checklist + guided 中文 scaffolds; director
  suggest_next leads with the funnel pre-storyboard; /create (创作) renders
  the rail + current-stage workbench (textarea over story/*.md, template
  parity with the CLI byte-verified, 让AI起草 vs 自己写 on every card).
- **多集 (item 3):** core/series.py — series = umbrella over NORMAL episode
  projects (zero engine edits, pinned); global bible seeds episodes at
  creation; `sync-bible` conservative (missing→add on --apply, diverged→
  report-only, --force still refuses hash-sealed locks); cross-episode
  character view; deterministic `split-script` at explicit # E01 markers.
- **驾驶舱 (item 4):** gui/cockpit.py + the SPA home hero — state → ONE next
  action → activity → risk-by-exception (calm when healthy), eleven blocks
  all read from existing engine calls, per-block degradation, never 500s.
- **剪辑 v2 (item 5):** the 6-key layer (Space/←→/I/O/Z/N + ? keymap overlay,
  input-focus guard, click-first WCAG 2.5.7), boundary/cue/second snapping
  (persisted), honest git-backed 3-tier 撤销 (revert = a new logged event;
  素材不回滚,只选择版本), Tier-1 playback of the newest final/proxy over the
  Range-capable media endpoint with bidirectional playhead sync.
- **体验清扫 (item 7):** docs/EXPERIENCE-AUDIT.md three-column ledger —
  error triples (进不了项目/缺 ffmpeg/没选 take 等 8 个最高频报错改为
  what/why/how-fix 中文), `_fail --json` now emits {"error","code"}
  structured JSON across all 98 call sites, 3 stale MCP descriptions fixed,
  export CLI failures now write structured failure records; the not-fixed
  findings recorded honestly with pointers.

**Honest boundaries:** skill efficacy is designed-for but unmeasured until a
real agent drives real generations (the skill-authoring skill carries the
baseline-vs-with-skill eval procedure); pooled per-clip playback (Tier 2)
not shipped — Tier-1 plays the rendered final; series-layer surfaces are
CLI-first this round (no series GUI page yet); the round-U user-side
validations (paid vendor key, desktop draft opening, vision-free QC now
covered by agent judgment) still stand where they stood.

**Verified:** 1456 tests green consolidated (was 1263; +193); live smoke:
skills index (14) + visual-qc-review resolution, series new/new-episode/
status, funnel walk + /create rail + stage save landing on disk, cockpit
/api blocks, /edit keymap + snap attr + synchints; CI green at every
integration push.

## 2026-07-08 — round W: the 85-issue external review — verify-and-fix

Six Sonnet-5 fix packages (per the user's model directive) + inline meta
fixes, every issue VERIFIED against the code before touching it, integrated
one cherry-pick at a time. Verdict: **84 of 85 fixed; 1 explicit design call;
zero not-real** — the review was accurate throughout.

- **WA core truth/check/spec (#1,5,18,26,34,57,74 + #16/37/60):** bible
  cross-file duplicate ids = check error; unindexed shots EXCLUDED from
  build/timeline (--include-unindexed escape); timeline/packaging asset refs
  + routing.yaml + style look + prop: locks enter check; rollback_file
  refuses compiled timeline artifacts. The hash-coverage trio landed as the
  VERSIONED formula design: SPEC_VERSION/VOICE_VERSION 2 fold dialogue,
  keyframes, and the resolved TTS provider; sidecars record their version and
  old takes stay judged by v1 forever (no mass restage) — plus a real
  inconsistency bug found in redo's hash stamping.
- **WB validation bounds (#2,3,6,22,23,25,48,69,70,81):** fps/width/height/
  duration/candidates/caption budgets/manifest cost-rate-concurrency all
  bounded with 中文 errors; --gen strictly missing|auto|off (a typo now
  STOPS the build instead of maybe spending); reversed trim windows surface
  instead of clamping; pack/unpack --json; status/GUI newest-final numeric;
  #81 resolved as transparency (qc names the artifact it checked — consistent
  with exports' render-free semantics).
- **WC path/file boundaries (#11,13,14,17,20,21,38,40,46,49,72,78):** one
  canonical safe-segment validator at every id→path choke point; refs AND
  keyframes share one containment guard (absolute/outside-project refused
  before any upload); pack skips symlinks; unpack ignores the zip comment;
  secret scan covers media/ text; board /media allowlisted; the core protocol
  skill can no longer be shadowed by a project; auto logs prompt hash not text.
- **WD spend/provider/network (#7,8,27,29,30,43,44,52,53,54,55,61,63,67,79,80):**
  estimates price the ROUTED provider; per-provider max_concurrent semaphores;
  pre-submit intent records close the double-charge window; candidates priced
  as produced; cheapest = per_call + per_second×duration; streamed downloads
  with scheme/size/content-type caps + multipart filename sanitization across
  generic_cloud/TTS/ASR/ComfyUI; ffmpeg/ffprobe timeouts; transcribe + GUI
  voice ride the spend gate; handle-rebuild pins the priced provider;
  currency never mixed; budget trip message states in-flight tasks continue.
- **WE concurrency/atomicity (#9,24,28,39,41,42,47,50,51,64,76):** ONE
  checked selected_take write path for CLI/MCP/board/GUI/build-auto-select
  (lock guard + post-check + revert + event); mutating CLI commands take the
  project write lock; parent-dir fsync; library/proposal/failure-ledger races
  fixed with real multi-process tests; jobs keyed (provider, job_id); mixer
  apply transactional; cover/teaser keys written only after verified output.
  Honest audit note: most GUI/MCP mutating handlers still rely on in-process
  mutexes only — recorded as follow-up debt.
- **WF export/QC/GUI (#10,31,32,33,35,36,56,58,59,62,65,66,68,71,73,75,77):**
  exporters honor source_in_ms (OTIO/剪映/CapCut match the render); ASS
  override-tag injection neutralized; deep-QC "detector didn't run" visible;
  verdicts require criterion+message; gc --hard removes ghost sidecars;
  stale finals BLOCK packaging by default; watch fingerprints cover captions/
  refs/keyframes/take files; manual-mode ASS staleness real; draft verify
  binds referenced media; funnel plan/produce staleness-aware; caches keyed
  by content; prompt-check checks prompt_override; masking covers
  Authorization/Cookie/URL tokens; split-script refuses to clobber edits.
- **Inline meta (#82,83,84,85):** project.yaml:agent restricted to known
  names (templates = machine-level only); constraints.txt + CI -c install;
  --json argv fallback under _fail; big-file refactor recorded in DECISIONS
  as deliberate standing debt.

**Verified:** 1716 tests green consolidated (was 1456; +260, including real
multi-process racing tests and attack-shaped path-escape repros); CI green at
every integration push.

## 2026-07-08 — round X: workflow smoothness — 入库/一致性/取消/剧集GUI/剪辑v3/工作区/素材库

Seven Sonnet packages under the new cost policy (targeted tests in worktrees,
ONE consolidated suite at integration; agents ran ~2× cheaper than round W).
All eight user pains addressed:

- **批量入库 (pain 1):** manju ingest + /ingest — a folder of externally-
  processed assets classified by naming convention (S001*.mp4→take,
  S001*.wav→voice, S001_ref→shot ref, <bible-id>_ref→bible ref) into a
  dry-run plan with content-hash dedup; apply lands through the existing
  append-only registration paths; GUI per-row action dropdowns before commit.
- **一致性质检 v2 (pain 2):** qc brief --mode consistency — per-character
  contact sheets (bible refs × every appearance frame), adjacent-pair boards,
  scene sheets; verdicts bind to whole units and stale when ANY member
  regenerates; qc coverage (reviewed/stale/never) in CLI/MCP/run_qc + the
  /review consistency section where humans file verdicts too.
- **任务管理 (pain 3):** cooperative cancel — checkpoints between phases/
  submissions, cancel_scope makes every ffmpeg call killable (SIGTERM→KILL),
  provider-poll cancel stops waiting honestly (remote may still bill; job id
  already persisted for resume); retry with lineage for build/redo/voice
  kinds; queue 取消/重试 buttons; CLI tasks retry (cancel honestly refuses —
  the ledger is not a live registry).
- **剧集 GUI (pain 4):** /series 剧集工作台 — episodes table, 新建集,
  sync-bible report+diff with SAFE apply only (--force stays CLI, like
  unlock), global characters, split-script preview; series banner in chrome.
- **剪辑 v3 (pain 5):** Tier-2 timeline preview — pooled <video> sequencing
  of per-take proxies plays the COMPILED TIMELINE before any final exists
  (honest 无混音 note; Tier-1 default the moment a render exists); 字幕样式
  panel (font/size/colour/outline/margin/alignment wired into the real ASS
  writer, defaults byte-identical); intro/outro card style presets.
- **工作区 (pain 6):** ~/.manju/recents (flock-safe, capped, dead-path
  pruning) touched from CLI/GUI/MCP; manju gui outside a project = workspace
  picker (recents + status chips + open/new) with true server REBIND; 项目 ▾
  switcher in every page's chrome, series-grouped.
- **素材库/预览/批量审 (pains 7+8):** import/ingest dedup vs the private
  library (--on-duplicate skip|import|link); deterministic 素材库建议 in
  /lab and /library; lazy per-take playable previews in storyboard drawer +
  review cards; /review queue mode — state filter chips (待选/待审/已通过/
  待更新), one-at-a-time flow with j/k, approve/换一条/redo/note through the
  existing checked paths.

**Verified:** 1931 tests green consolidated (was 1716; +215); CI green at
every integration push.

## 2026-07-08 — Round AA: confirmation flows, traceability, consistency, honesty (8-item goal)

Two waves (5 engine + 3 GUI Sonnet agents, worktree isolation, hand-resolved
integration), all eight items landed:

- **确认导入 (items 1+2):** every IngestRow carries a match state
  (matched/pending/unmatched/conflict/manual) + candidates; >1 plausible owner
  NEVER lands on a guess (downgrades to plain import naming every candidate);
  every apply persists reports/ingest_batches/<batch_id>.yaml; a take landing
  on an EMPTY shot is auto-selected via the checked write path (via="ingest",
  never past a lock, never over an existing pick) and the batch review's
  discard undoes exactly that — only if still selected.
- **批次评审 (item 4):** /ingest review panel — batch selector with per-state
  counts, match/review filter chips, thumb/preview/target-link table,
  确认/标记/丢弃(真实 undo 反馈)/查看 per row, 全部确认已匹配 under one lock
  hold; CLI ingest-batches/-review/-confirm/-flag/-discard.
- **引用关系 (item 3):** core/refs.py DERIVES ownership (naming convention +
  bible pins + shot ref params — no second registry to drift); manju refs
  (+--orphans/--json), refs assign renames/pins under build_lock; check warns
  on orphans + dangling bible pointers; /library 参考素材归属 section with
  assign picker; 参考 vs 成品 labeled.
- **写入一致性 (item 5):** CAS — checked_shot_write(expected_text_hash) +
  shot_text_hash; GUI shot editor/storyboard cell/take-note send their loaded
  rev, stale → 409 (中文); MCP get_shot returns rev, update_shot takes
  expected_rev; round-Z residuals CLOSED: sync_bible wraps each episode's
  writes in that episode's own build_lock (busy episode stops the run honestly,
  stopped_at names it), director execute carries an explicit per-action-type
  lock table (repair/captions/packaging/snapshot/rollback wrapped; build/redo/
  voice/mixer lock internally).
- **任务诚实 (item 6):** .manju/jobs.jsonl lifecycle records (capped 500);
  jobs interrupted by a GUI restart surface as 已中断 (retryable=false + 中文
  note — params_summary is lossy, no fake resubmit; retry POST → clean 409);
  series new-episode/sync-bible-apply/ingest-plan routed through the runner;
  cancel checkpoints added to redo_batch/voice_batch/ingest apply+plan/
  sync_bible/edit_preview_batch — partial progress always reported truthfully.
- **剧集连续性 (item 7):** series_continuity() — per-episode verdicts
  (有问题>缺素材>待同步>完整, documented precedence), characters+scenes+props
  continuity matrices (generalized from series_characters), voice-field
  divergences, packaging mode-vs-outlier 参考性提示; manju series continuity
  + /api/series/continuity + series-page matrix with sync links.
- **技能评估 (item 8):** skill_used/funnel events at real call sites; core/
  evaluate.py + manju evaluate + cockpit 评估 block — usage counts, never-used
  skills, redo/repair hotspots, QC tallies, funnel completion — with a
  REQUIRED honesty section (correlation only, no fabricated productivity
  claims) rendered as part of the output; REPORTS/ROUND-AA-EVALUATION.md maps
  each metric to the real-world practice it mirrors + a falsifiable cut list.

**Verified:** 2090 tests green consolidated (was 1931; +159); wave-1 CI green
at 66cf442; every mutation path stayed on the checked-write/lock conventions.


---

## 2026-07-09 — Interconnection & Trust (GUIDEINTERCONNECTION WP1–WP7)

**Done:** Landed GUIDEINTERCONNECTION.md WP1–WP7 machinery so script ↔ voice
↔ captions ↔ timeline share one impact spine and spend answers one plan shape.
Not every guide acceptance line is green end-to-end yet — see honest
boundaries below (WP4/WP6/GUI chrome remain partial).

- **WP1 impact spine.** `CaptionLine.shot` stamped by the compiler (both
  timed and weighted-split paths); shared `timeline/cuemap.py` (stamped
  field preferred, temporal fallback for legacy); `build/impact.py` with
  hypothetical + current modes; CLI `manju impact`, MCP `impact` tool,
  GUI `POST /api/impact` (readonly-exempt) + debounced shot-editor strip.
  First recompile with the new field moves the timeline fingerprint once
  (documented; round-O precedent).
- **WP2 audio-first.** `media/ttspreview.py` 试听 into `.manju/webpreview/tts/`
  (cache-keyed, never a take); `manju voice --preview`; GUI
  `/api/voice/preview` job. `manju build --target audition` with
  `allow_missing_takes` compile flag (in-memory timeline only), slate
  placeholders, `renders/audition/audition_vN.mp4` + content-key sidecar.
  Video generation is never planned for audition.
- **WP3 VO align.** `media/align.py` + `manju align`: free text-anchor
  default, `--from-srt`, `--asr` (spend-gated); multi-shot plan/apply with
  MANUAL takes + timing sidecars (regenerable exception to append-only) +
  ingest-style batch records. `AsrConfig` optional word-level keys.
- **WP4 locales.** Overlay model under `locales/<lang>/lines.yaml` with
  `base_hash` → 翻译过期 advisory; `manju locale add|status`; check
  validates unknown shot ids. Locale text never enters spec_payload.
- **WP5 transparency.** `explain --cost`; honest `final_export` /
  `lock_change` ask_before gates; dry-run `--json` emits the GUI plan
  envelope (+ `renders` cache-reuse + voice cost note); failure
  `next_options` at record time for generate/voice.
- **WP6 roundtrip.** `build/roundtrip.py` plan/apply for JianYing skeleton
  + OTIO only; baseline writer; reorder + caption_edit paths; batch under
  `reports/roundtrip_batches/`.
- **WP7 consistency preflight.** Plan rows gain advisory `consistency`
  warnings when bible characters lack refs (never blocks).

**Verified:** new tests in `tests/test_impact.py`, `test_ttspreview.py`,
`test_interconnection.py` green; voicefix/compiler regressions green.
Full suite not re-baselined in this environment (ffmpeg PATH absent on
this host for some generation tests).

**Honest boundaries:**
- Audition render reuses final pipeline with slates; needs ffmpeg.
- Locale voice/build `--lang` flag threading is scaffolded via
  `core/locale.py` overlay helpers; full per-locale voice take paths
  (`media/gen/<shot>/locales/<lang>/`) remain a follow-up for the voice
  batch/CLI flag surface.
- Roundtrip v1 covers reorder + caption edit on skeleton/OTIO; trim/volume
  /transition apply paths are planned in the module table but only
  reorder + caption_edit are wired to write paths.
- GUI 试听 button / 先听后看 card / full locale editor / roundtrip ingest
  panel wiring are partial (API endpoints exist; richer chrome is
  follow-up).

**Open follow-ups (recorded, not expanded this round):** per-locale
`register_voice_take(lang=)`, full `--lang` build/voice threading,
roundtrip trim→virtual inout + mixer apply, phoneme aligner provider
manifest slot.
﻿
---

## 2026-07-09 — Interconnection completion round (post self-check)

**Done:** Closed the honest-boundary gaps left after the first GUIDEINTERCONNECTION
landing and the self-check FAIL→PASS fixes.

- **WP4 build --lang:** `run_build(..., lang=)`, CLI `manju build --lang en`,
  `manju voice --missing --lang en`; `locale_build.py` plan/synthesize overlay,
  captions under `captions/locales/<lang>/`, finals under
  `renders/final/locales/<lang>/` with content keys that share video segment
  cache; base `timeline.json` never written by locale builds.
- **WP6 apply depth:** OTIO Stack-shaped tracks normalized; trim rows →
  `set_inout` virtual apply; unmatched foreign clips reported; export baselines
  already on jianying/otio from prior fix.
- **GUI chrome:** 试听 ▶ in shot editor (`/api/voice/preview`); 「先听后看」
  button on home (build target=audition); server allowlist includes audition.

**Verified:** `test_locale_roundtrip_gui.py` + interconnection suite green.

**Still partial (honest):** full ffmpeg e2e locale final with live Edge TTS;
volume/transition mixer apply classes; full 2090 suite on hosts without ffmpeg.
﻿
---

## 2026-07-09 — Full close of ai.txt review findings (P0–P2)

**Done:** Implemented every actionable item from the external analysis:

### P0 (misleading / data loss)
1. **Roundtrip captions:** full cue list patched by selected indices; never
   rebuild SRT from only changed rows.
2. **`voice --preview`:** `WaitingUser` → `code=waiting_user` (not tts_unavailable).
3. **GUI media allowlist:** `.manju/webpreview/` read-only (试听 playable).
4. **Plan parity:** `action_plan` / dry-run `--json` threads `lang` +
   `include_unindexed` into `run_build`.

### P1 (acceptance gaps)
5. **JianYing text materials/segments** stamp `manju:{kind:caption,shot,cue_index}`;
   plan_roundtrip diffs JY captions (not OTIO-only).
6. **`locale status`:** per-shot voice state + captions/final freshness.
7. **Multi-shot ASR:** honest CLI help + exception (transcribe → --from-srt).
8. **Roundtrip GUI:** `POST /api/roundtrip/plan|apply`; ingest batches list
   merges `reports/roundtrip_batches/`.

### P2 (product close)
9. **voices.yaml:** `overlay_shot_for_voice` injects `locale_voice_id`;
   Edge TTS honors it.
10. **Checked index writer:** `core.writes.permute_index` shared by GUI
    `/api/index` and roundtrip reorder apply.

**Verified:** `tests/test_ai_review_fixes.py` + interconnection suite green
(62 related tests).
﻿
---

## 2026-07-09 — Second-pass review: 6 remaining trust bugs

Addressed the follow-up analysis after the ai.txt full close:

1. **JY reorder:** `_shot_order_from_jianying` now prefers **video track
   segments** (edit order), materials only as fallback. Test swaps segments
   only and asserts reorder + caption_edit rows.
2. **voices.yaml override:** Edge `_voice_for` + `voice_payload` give locale
   `voice_id` priority over bible; effective id folds into voice_hash.
3. **locale caption/final freshness:** content-key based (captions.key.json +
   final key recompute), not mere file presence.
4. **locale voice status:** uses provider descriptor for v2 hash compare.
5. **multi-shot ASR test:** actually calls `plan_multi_shot(..., asr=...)`.
6. **caption baseline conflict:** export baseline stores `captions_hash`;
   roundtrip marks caption_edit conflict when human SRT moved since export.
