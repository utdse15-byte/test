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
