# Competitive study — mature auto-video pipelines vs Manju One

Round J of continuous development. Sources studied (live, 2026-07):
[MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) (via
[feature overviews](https://www.nexusai-tech.com/ai-apps/moneyprinterturbo-open-source-ai-short-video-generator)),
[ShortGPT](https://github.com/RayVentura/ShortGPT) ([docs](https://docs.shortgpt.ai/)),
[NarratoAI](https://github.com/linyqh/NarratoAI),
[edge-tts](https://github.com/rany2/edge-tts).

## Feature matrix (them vs us)

| Feature (theirs) | MPT | ShortGPT | NarratoAI | Manju One |
| --- | --- | --- | --- | --- |
| LLM script generation in-tool | ✅ multi-provider | ✅ | ✅ | ✋ deliberate NON-feature: intelligence stays outside (§0); Claude Code writes story/shots via SKILL.md |
| Free keyless TTS (Edge) as default voice | ✅ default | ✅ | ✅ | ✅ **adopted round K** — `manju.providers.edge_tts:EdgeTtsProvider`, real live test |
| Stock footage sourcing (Pexels/Pixabay) | ✅ core | ✅ | — | 🔲 provider slot designed (capability `stock_footage` routes through the existing fallback chain); adapter pending an API key to verify honestly |
| Timed captions from voice | ✅ | ✅ | ✅ | ◐ captions follow the voice window, split by text weight; word-level timing possible later via edge-tts submaker / ASR alignment |
| Styled subtitles (font/pos/color/outline) | ✅ | ✅ | ✅ | ✅ ASS style engine + bible style overrides |
| 9:16 + 16:9 + arbitrary aspect | ✅ two | ✅ | ✅ | ✅ any WxH per project.yaml |
| BGM + ducking | ✅ | ✅ | ✅ | ✅ sidechaincompress + loudnorm |
| Batch generation (topic list → N videos) | ✅ | ✅ | ✅ | ✋ out of engine scope — the AI director loops `manju new`/`build`; noted, not adopted |
| Auto-publish (TikTok/YT upload) | ✅ | — | — | ✋ out of scope (accounts/policy surface); exports are the boundary |
| Web UI | ✅ | ✅ gradio | ✅ | ✋ deliberate: static board covers review at 1/20 the cost (§1-⑦) |
| Video parsing / keyframe extraction / scene understanding | — | — | ✅ | ✅ QC frames + qc_vision manifest slot + `manju transcribe` |
| Draft export to editors (剪映) | — | — | ◐ merge only | ✅ dual-path JianYing + pyCapCut + OTIO — **our differentiator** |
| Reproducible builds / caching / staleness | — | — | — | ✅ **our differentiator**: content-keyed idempotent finals, segment cache, hash-derived staleness |
| Human↔AI takeover semantics | — | — | — | ✅ **our differentiator**: locks, manual takes/timeline/captions, events/status |
| Cost guardrails / run ledger | — | — | — | ✅ dry-run pricing, budget breaker, per-call ledger |

## What we adopted this round

1. **Edge TTS (round K, shipped).** Every studied pipeline defaults to
   Microsoft Edge's free neural TTS. Now a dedicated adapter through the
   §8.6 `module:Class` escape hatch (it speaks WebSocket, exactly what the
   generic REST adapter is not for). Live-verified: real zh-CN neural voice
   takes, voice-driven durations, `pip install manju[edgetts]`.

## Adopted as designs, pending honest verification

2. **Stock footage provider.** MPT/ShortGPT's core visual source. Fits our
   provider model exactly: a manifest with capability `stock_footage`
   slots into shots' fallback chains (capability routing already resolves
   unknown steps). Needs a dedicated adapter (search-GET shape, not
   submit/poll) and a real PEXELS_API_KEY to verify — free tier exists.
   Scaffold shipped; verification honestly deferred until a key exists.

## Considered and deliberately NOT adopted

- **In-tool LLM script generation** — Manju's core thesis is the opposite
  (§0: intelligence outside, determinism inside). The studied tools bake one
  prompt chain in; we hand the whole creative stage to the director (human
  or Claude Code), which is strictly more capable and stays auditable.
- **Auto-publishing, batch mode, web UI** — platform-account surface,
  looped-director work, and §1-⑦ respectively.

## Where we are ahead (none of the three have)

Deterministic rebuilds (content keys), append-only media with lineage
sidecars, hard value-hash locks with a proposal channel, dual-path editor
drafts, machine-checked content QC (OCR must_show), an explainer that
justifies every build decision, and a run ledger with budget breakers.

Sources:
- https://github.com/harry0703/MoneyPrinterTurbo / https://www.nexusai-tech.com/ai-apps/moneyprinterturbo-open-source-ai-short-video-generator
- https://github.com/RayVentura/ShortGPT / https://docs.shortgpt.ai/
- https://github.com/linyqh/NarratoAI
- https://github.com/rany2/edge-tts
