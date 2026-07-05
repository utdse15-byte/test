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
