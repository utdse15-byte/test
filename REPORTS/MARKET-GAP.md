# MARKET-GAP — manju vs commercial products and strong OSS (2025–2026)

Date: 2026-07-07. Method: deep-research fan-out (5 search angles → 15 sources
fetched → falsifiable claims → 3-vote adversarial verification, 105 agents);
10 findings survived, each cited below. Caveat up front: this landscape moves
monthly — several facts are already near-stale (Runway Gen-4.5 Dec 2025,
Runway Agent 2.0/Skills Jun–Jul 2026, ViMax Jun 2026), and vendor-reported
benchmark numbers (LTX "~95% facial consistency", FLORA "50+ models") were
cross-checked for existence but not independently measured.

## The one-paragraph verdict

manju occupies a genuinely distinctive niche — local-first, deterministic,
auditable, human-takeover-first, editor-draft-handoff — but on most
INDIVIDUAL dimensions at least one commercial product or OSS project already
matches or exceeds it. The moat is the BUNDLE, not any single feature: no
verified competitor combines deterministic idempotent renders, append-only
take lineage with cost provenance, git-backed rollback, machine-checked
QC+repair, and open model-agnostic adapters. The biggest deficit
(generation-quality access) is keys-and-config away; the biggest real
engineering gap is perceptual consistency QC; the widest gaps (hosted GUI,
collaboration, community) are out of scope by our own design.

## Landscape map

| Category | Best-of examples | Relationship to manju |
| --- | --- | --- |
| Frontier model providers | Runway Gen-4.5 (AA leaderboard top, Dec 2025), Veo 3.x, Kling 可灵, Seedance, Sora 2 | What manju's adapters CALL — suppliers, not competitors |
| Hosted creative suites | LTX Studio (script→scenes/storyboards, Elements consistency), Runway (Agent 2.0, Workflows), FLORA (50+ model canvas) | Overlap manju's orchestration territory, hosted/cloud |
| One-click generators | MoneyPrinterTurbo, ShortGPT; 剪映图文成片, 度加, 快影 | Far shallower than manju: no shot state, takes, QC, or rollback |
| Editors with AI | CapCut/剪映, Descript, OpusClip | manju hands off TO them (draft export), doesn't compete |
| OSS orchestrators | **ViMax** (HKU, MIT license — the direct competitor), NarratoAI, VideoLingo | Same category; each beats manju on 1–2 dimensions, none on the bundle |
| Local generation stacks | ComfyUI + Wan 2.2 open weights (TI2V-5B on ~8GB VRAM) | manju's zero-key generation path — an ally, wired in round Q |

## Dimension scores (manju vs best-in-category)

| Dimension | manju vs the bar | Verdict |
| --- | --- | --- |
| Generation-quality access | Runway Gen-4.5 / Veo 3.1 / Kling via LTX/FLORA one-subscription | **Behind — biggest gap, closable by keys** (adapters exist; no paid vendor verified) |
| Story→shot orchestration depth | LTX script→storyboard; Runway Agent 2.0/Workflows; ViMax idea/novel/script→shots | **Par, not unique** — our depth is real but hosted suites now do this too |
| Consistency (character/scene) | Runway References/Act-Two, LTX Elements, ViMax's VLM candidate-judging | **Behind — real engineering** (we have structural bibles/locks, zero perceptual checking) |
| Editing/draft handoff | NarratoAI also ships 剪映草稿 export | **Par among Chinese-first OSS** (our dual-path + OTIO is broader, not unique) |
| Subtitles/audio pipeline | VideoLingo (WhisperX word-level, CJK-aware splitting, dubbing) | **Par-ish** — we win on safe-area/burn/draft integration, lose on ASR-side alignment breadth |
| QC/repair | Nobody verified ships machine-checked QC + derived repair plans + repair ops | **Ahead** |
| Human-AI collaboration & auditability | Runway Agent is agentic but opaque; ViMax has no locks/rollback | **Ahead** (locks, actor-attributed events, git rollback, guarded MCP) |
| Cost/task management | Hosted suites hide cost in subscriptions | **Ahead for power users** (ledger, dry-run, budget breaker, per-take cost) |
| UX/GUI | LTX/FLORA canvas UIs, per-shot directorial controls | **Behind by design and by a wide margin** — board --serve is a workspace, not a product |
| Ecosystem/community | ComfyUI's ecosystem; MoneyPrinterTurbo's stars | **Behind, out of scope** (single-user tool) |

## Where manju is genuinely ahead (verified: no single competitor bundles these)

The architectural layer: deterministic pure-function compiler with
content-key idempotent renders and incremental re-render; append-only takes
with full provider/prompt/params/cost lineage; value-hash locks + manual
takeover at every layer; git-backed history/snapshot/rollback with
actor-attributed events; machine-checked QC feeding mechanical repair plans;
config-driven model-agnostic adapters with a keyless fallback chain (down to
Wan 2.2 local via ComfyUI — a real zero-cloud path hosted suites cannot
offer). ViMax — the closest OSS competitor — has none of the
state-model/auditability machinery; ShortGPT/MoneyPrinterTurbo are one-shot
pipelines with no takes, no QC, no rollback.

## Where manju is behind, and what closing costs

1. **Generation quality access — config/keys, days.** Fill provider.yaml
   manifests for 1–2 paid vendors (Runway API / Kling / a Chinese vendor) and
   run one live verified end-to-end. The engine (submit/poll/ledger/budget)
   is already built and mock-verified.
2. **Perceptual consistency — real engineering, the one big build.** ViMax
   proves the recipe in OSS: a vision model judges candidate takes against
   character/scene refs and picks/flags. Our `qc_vision` manifest slot is the
   socket; the work is the judging loop (frames → VLM → per-take consistency
   verdicts → QC findings → repair suggestions) and reference-frame
   management on the bible.
3. **Generation-side consistency (References/Elements-class) — partly
   vendor-dependent.** When a vendor exposes reference-image conditioning,
   our adapters can pass bible refs through (`{image}` already plumbs);
   parity with Runway References requires their models, not our code.
4. **Subtitle alignment breadth — moderate.** WhisperX-class forced
   alignment as an ASR manifest (the slot exists) would match VideoLingo's
   word-level precision for imported/human VO, complementing our TTS-boundary
   path.
5. **UX/GUI polish, hosting, collaboration, community — out of scope by
   design.** The board is a personal workspace; competing with LTX/FLORA
   canvases is a different product.

## What to build next (one power user, priority order)

1. Live-verify one paid video vendor end-to-end (keys + manifest, then a
   paid showcase build) — converts the biggest deficit into a config note.
2. The perceptual consistency loop on the qc_vision slot (VLM judging takes
   against bible refs; ViMax-style, engine-side, vendor-pluggable).
3. WhisperX-class forced alignment behind the ASR manifest for imported VO.
4. Reference-image passthrough for vendors with References-class endpoints.
5. Desktop verification of JianYing/CapCut drafts (double-click on a real
   desktop; fix whatever lint missed).

## Sources (survived 3-vote verification)

- https://runwayml.com/changelog (Gen-4.5 Dec 1 2025; References; Act-One/Two; Workflows Oct 2025; Agent 2.0 Jun 2026; Agent Skills Jul 2026)
- https://ltx.io/studio (script→scenes/storyboards; Elements consistency; LTX-2.3 + partner models)
- https://www.florafauna.ai/ (50+ model canvas incl. Veo 3, Gen-4 Turbo, Ray 2, Kling, Seedance)
- https://github.com/hkuds/vimax (ViMax, HKU, MIT, arXiv 2606.07649 — idea/novel/script→video with VLM consistency)
- https://github.com/linyqh/NarratoAI (剪映草稿 export; vision-model footage understanding)
- https://github.com/Huanshere/VideoLingo (WhisperX word-level alignment, CJK splitting, dubbing)
- https://github.com/harry0703/MoneyPrinterTurbo, https://github.com/RayVentura/ShortGPT (one-shot pipelines, no state model)
- https://docs.comfy.org/tutorials/video/wan/wan2_2 (Wan 2.2 open weights; TI2V-5B ~8GB VRAM)

Known citation correction from verification: Runway Gen-4.5 launched Dec 1,
2025 (not Dec 11 as one source stated).
