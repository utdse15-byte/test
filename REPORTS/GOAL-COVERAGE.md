# GOAL-COVERAGE — the 19-item goal mapped to evidence

Date: 2026-07-06 (round Q). Each item: what already stood before this round
(with where to verify), and what round Q adds. "CLI" means run it; "tests"
means the named suite pins it.

## 1. Project basics — stood, one gap closed in Q
`manju new` (blank projects), `manju import` (media incl. images/video/audio
into sacred media/imports; refs via media/refs), `status`, `doctor` (health),
`history` (events+git merged), `snapshot`, `rollback shot|file`,
`pack/unpack` (.manjupkg with identity, FIX-E). **Q adds:** text/script/novel
import — .txt/.md route to story/imports/ for agent adaptation.

## 2. Defaults — stood
Vertical 1080×1920 default (`manju new --vertical` is the default), fps 24
default / 30 via config or preset, proxy renders + content-key skip, final
renders (append-only final_vN), SRT/ASS, MP4, JianYing dual-path + pycapcut
drafts, subtitle safe area (ASS margin_v §7④), never-overwrite guarantees
(imports sacred, takes append-only, selected takes only re-pointed, manual
timelines refused by the compiler), §8.4 fallback chain. Tests: e2e_m0,
idempotency, native_draft, overlay, check.

## 3. Shots — stood (fields incl. must_show/avoid, continuity, locks,
stale/manual states via build/stale.py; redo/select --file/manual takeover).
"approved" is expressed as a value-hash lock on the shot (lock = approve).

## 4. Takes — stood
Multiple takes, board preview/compare, select, rollback (round O), stale
marks, sidecars record provider/prompt/params/cost/QC lineage; manual takes
first-class (§8.4 manual_import); locks protect selection.

## 5. Providers — stood + Q
Stood: generic_cloud (custom HTTP by config, §8.6), generic_tts, Edge TTS,
Pexels stock, kenburns (static pan/zoom), caption/text cards, manual import;
capability declarations + priority + fallback chain; doctor config checks;
engine-side retry/throttle. **Q adds:** ComfyUI adapter (workflow-JSON
submit/poll/download) and local-command adapter (run any binary → take).

## 6. Timeline — stood
Pure compiler + manual takeover (timeline.json human truth), video/voice/
music/sfx/ambient/overlay/caption tracks, order via shots/index.yaml,
transitions, fingerprint stale detection, recompile, segment-level
incremental re-render, OTIO/draft export.

## 7. Render/post — stood
Proxy/final/segment/incremental + content-key cache, normalization to
project res/fps (FIX-B frame grid), concat stitching, burned ASS + external
SRT, full audio mix, BGM/ambient sidechain ducking (tunable), loudnorm
(final), kenburns, text/title/info cards, cover extraction.

## 8. Subtitles — stood
SRT/ASS, CJK punctuation-aware segmentation + line breaks at the declared
budget (round O), safe area, outline (ASS style), position (margin), TTS
word-boundary alignment (round M), manual takeover (captions.srt = truth),
import (`transcribe --from-srt/--text`), export. **Q adds:** garbled-text
(乱码) QC check. Overlap/bounds checks: stood in QC; Q adds same-window
overlap tightening.

## 9. Audio — stood + Q
TTS (per-character voice_id = multi-role), VO import/replace (manual voice
takes, newest-wins), BGM gain/trim/fade/ducking, SFX/ambience/transition
sounds with anchor grammar (round N), voice gain. **Q adds:** all-silent
voice detection and clipping detection in QC.

## 10. Export — stood
final/proxy MP4, SRT, ASS, OTIO, JianYing dual-path + CapCut drafts (sfx/
ambient tracks carried, round O), cover + teaser clips (`manju package`),
export report (`manju export --json` returns produced paths; draft lint
report embedded).

## 11. Bible — stood + Q
characters/scenes/style bibles with value-hash locks, refs + voice binding,
lock machinery on any entry (props/voices are plain bible files in the same
shape). **Q adds:** proof/wiring that props.yaml + voices.yaml enumerate
everywhere plus `manju appearances` (per-character/scene/prop shot map,
orphans, continuity aid).

## 12. AI creation — stood in shape, Q deepens
BY DESIGN there is no in-tool LLM (§0): creation is the external agent
following the playbook over the same CLI/MCP surface. **Q adds:** SKILL.md
concrete workflows — idea expansion, outline/script, novel-to-script,
script-to-shot, storyboard planning, order optimization, hook/ending
strengthening, dialogue polish under locks, pacing pass, repair loop.

## 13. GUI/Board — stood (round P) + Q
`board --serve`: live actionable workspace (select/redo/rollback/build/qc/
package/snapshot, Range-seekable video, event-logged, dangerous surface
unreachable). **Q adds:** take-comparison view, project/subtitle/bible/log/
asset/QC panels, export button, playback controls.

## 14. AI collaboration — stood
MCP server (no unlock/gc), full CLI --json, shot editing via MCP update_shot
with lock rejection+rollback, QC/build/select/propose, actor-attributed
events, `manju auto` for ANY one-shot agent CLI (round P), assisted + manual
takeover everywhere.

## 15. QC — stood + Q
Stood: missing/unreadable, duration vs spec, resolution, fps (r_frame_rate
assert), black/freeze probes (provenance-aware), missing audio, subtitle
overlap/safe-area, missing shots, draft lint, must_show OCR machine checks,
image-quality gate (mcp-video slot), character/scene consistency slots
(qc_vision manifest — needs a real vendor), JSON + Markdown reports, frame
extraction. **Q adds:** garbled subtitles, silent-voice, clipping, timeline
conflicts (overlap/zero-duration).

## 16. Repair — stood + Q
Stood: repair_plan.yaml auto/manual split, `repair --auto` (redo/degrade),
targeted `redo --provider/--seed/--candidates`, replace via select --file,
fallback chain, re-run QC. **Q adds:** first-class repair ops — retime,
extend (freeze/black), trim, crop/blur-pad — each minting a NEW take with
lineage, exposed as `manju repair --op …`.

## 17. Presets — REVERSED in Q per owner decision
Round O's 13 content kits are replaced by exactly three: blank /
vertical_ai_video / horizontal_ai_video. Content-type inference is the
agent's job from the input (playbook), not a preset's. DECISIONS #7.

## 18. Visual packaging — stood + Q
Stood: intro/outro cards as real segments, title/chapter/character/info
cards (subkind bands), subtitle styling, cover, teaser. **Q adds:** logo
overlay, watermark (text/image), corner badge (角标), CTA closing chip,
trailer-title via intro kicker/subtext.

## 19. Cost/tasks — stood + Q
Stood: dry-run pricing + budget breaker, SQLite ledger with async submit +
resume-polling (restart re-polls, never resubmits), failure + moderation-
rejection records with reason text, engine-side rate/concurrency throttles,
per-take cost in sidecars, totals in status, ask_before high-cost gates.
**Q adds:** `manju tasks` — the human/JSON view over the ledger with
per-provider aggregates.

## Honest boundary (unchanged)
A live PAID text-to-video vendor run and desktop JianYing/CapCut draft
opening still require the owner's API key / desktop apps. The qc_vision
consistency slot needs a real vision vendor the same way.
