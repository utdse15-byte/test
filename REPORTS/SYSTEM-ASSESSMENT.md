# System assessment — is the AI video production OS implemented?

Question asked (goal): *"from idea to finished film; AI fully automatic;
human fully manual; hand control back and forth most of the time; connecting
ideas, assets, shots, timelines, drafts, post-processing, QC, and fixes."*

Answer: **yes, with two honest qualifiers** (listed at the end). Every claim
below cites its executable evidence — a test, a command, or a delivered
artifact.

## 1. Idea → finished film (the full chain)

| Stage | Surface | Evidence |
| --- | --- | --- |
| Ideas | `story/brief.md → outline.md → script.md` scaffolded by `manju new`; creation belongs to the director (§2), the engine never invents shots | container scaffolds + `run_build` "no shots" refusal (test_container / graph) |
| Assets | `manju import` (sacred imports + previews), providers: cloud video (generic §8.6), stock footage, kenburns, HTML cards, drawtext; manual import as a provider | test_round5 (previews), test_stock, test_generic_cloud, e2e M0 |
| Voice | Edge TTS (free, keyless, live-verified) / generic_tts manifests / hand-dropped WAVs; voice_hash staleness | test_edge_tts (live), test_voice |
| Shots | ShotSpec YAML + spec_hash staleness + locks + candidate takes with lineage sidecars | test_stale, test_locks, test_idempotency |
| Timeline | pure-function compiler, audio-driven durations, frame-grid snapping, manual takeover | test_compiler, test_round_a |
| Drafts | JianYing dual-path (native + skeleton + lint), pyCapCut, OTIO, SRT/ASS | test_native_draft |
| Post | segment-cached incremental renders, transitions, title cards, BGM ducking, loudnorm, content-keyed idempotent finals | test_idempotency, e2e M0 incremental |
| QC | existence/technical/content tiers, OCR must_show with NO agent present, fps/duration contracts, frames for agent eyes | test_content_qc, test_idempotency |
| Fixes | repair_plan.yaml + `manju repair --auto`, `manju redo/voice`, everything returns to the build loop (§9) | test_cli (repair), test_voice |

Delivered artifacts: 《深夜信号》 (silent v2 and **voiced v3** — 4 real neural
lines synthesized in one `manju build`, speech driving picture durations).

## 2. The three modes

- **AI fully automatic** — the copilot/autopilot surface is proven two ways:
  the MCP wire e2e (status → update_shot → check → build → explain → select →
  propose, all `actor=ai`, test_mcp_copilot_e2e) and the live showcase built
  by the AI director through the public CLI only. `manju auto` shells to
  `claude -p` with SKILL.md prepended.
- **Human fully manual** — the M0 acceptance is exactly this flow: hand-write
  shots, `select --file` imported clips, build, QC, pack (test_e2e_m0); no AI
  anywhere in the loop.
- **Handoff, both directions** — hash-guarded: value locks block AI edits
  hard (tampering fails check/build — test_ticket2, test_mcp lock rejection);
  human takeovers are first-class states the engine never overturns: manual
  takes (spec_hash=manual), manual voice (no sidecar), manual timeline
  (mode=manual, generated-only writes), manual captions (SRT is truth, ASS
  recompiled from it); `manju status`+`events`+`explain` are the 30-second
  re-entry surface for whoever picks the project up.

## 3. Competitive posture

See REPORTS/COMPETITIVE-STUDY.md: feature parity with the mature auto-video
pipelines on voice/captions/BGM/aspect (Edge TTS adopted, stock footage
scaffolded), plus differentiators none of them have — deterministic rebuilds,
locks/takeover semantics, editor drafts, cost ledger, build explainer.

## Honest qualifiers

1. **Paid cloud video generation is config-complete but unexercised** — the
   §8.6 generic adapter is fully tested against scripted transports and the
   free Edge TTS proves the live path, but no paid text-to-video API has been
   called (no vendor account exists here). Onboarding one is a provider.yaml
   + key env var; the first live call is the remaining risk.
2. **Desktop editor verification** — JianYing/CapCut drafts are structurally
   verified against the real libraries; opening them in the pinned desktop
   apps needs a human with the apps installed (§14).
