# ROUND-V-REFERENCES-1 — skill libraries, creative-workflow funnels, long-form/multi-episode, visual-QC standards

Date: 2026-07-07. Round V research deliverable (agent RV1). Method: five parallel
research legs (four background web-research agents on the amateur-vs-pro lessons,
creation funnels, long-form/短剧, and visual-QC standards; plus the session lead's
own primary-source investigation of the Agent Skills ecosystem via github + the
official Anthropic/Claude Code docs). Every concrete claim carries an inline source
URL; anything not pinnable to a primary source is marked **(unverified)**. This
landscape moves monthly — generation-API numbers and product UIs are current as of
2026-07.

Companion to [ROUND-U-REFERENCES.md](ROUND-U-REFERENCES.md) (exact API field names,
reference-image limits, status vocabularies, the §10 glossary) and
[COMPETITIVE-UX-STUDY.md](COMPETITIVE-UX-STUDY.md) / [MARKET-GAP.md](MARKET-GAP.md).
Round U delivered the *workbench-surface* vocabulary; this report delivers the
*craft-and-judgment* layer Manju must encode as **skills** so an external agent
(Claude et al.) driving Manju produces professional, not amateur, video — and the
staged creation entry, long-form data structures, and visual-QC criteria that feed it.

The single strongest through-line: **Manju is agent-neutral and never calls an LLM
itself (§0), so the only way domain craft reaches a film is through the agent that
drives Manju. A skill library IS Manju's product surface for expertise.** Everything
below serves the "Consolidated skill taxonomy" at the end.

---

## Item 1 — SKILL LIBRARIES (the big one)

### 1a. The Agent Skills format & Claude Code conventions (primary-source)

**SKILL.md is a folder with one required file.** A skill is a directory whose
entrypoint is `SKILL.md`: YAML frontmatter (metadata) + a Markdown body, optionally
bundled with `scripts/`, `references/`, and `assets/` subdirectories
(https://github.com/anthropics/skills;
https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices).

**Frontmatter — exact validated fields (Anthropic best-practices doc, verified):**
- `name`: **max 64 chars**, lowercase letters/numbers/hyphens only, no XML tags,
  **may not contain the reserved words "anthropic" or "claude"**.
- `description`: **non-empty, max 1024 chars**, third-person, "what it does AND when
  to use it." This is *the* triggering mechanism.
  (https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Claude Code adds optional fields: `when_to_use`, `allowed-tools`,
  `disable-model-invocation`, `user-invocable`, `disallowed-tools`, `model`,
  `effort`, `context: fork`, `agent`, `paths` (globs that gate auto-activation),
  `argument-hint`, `arguments`. The combined `description`+`when_to_use` is
  **truncated at 1,536 chars in the skill listing** to save context
  (https://code.claude.com/docs/en/skills).

**Progressive disclosure = three loading levels (the core idea).** (1) *Metadata*
(name+description, ~100 words) is preloaded into the system prompt for every installed
skill so the agent knows it exists. (2) *SKILL.md body* loads only when the skill
triggers (keep it **under 500 lines**). (3) *Bundled files* (`references/*.md`,
`scripts/*.py`, `assets/`) load only when the body points to them — and scripts are
**executed, not read into context** (only their output costs tokens)
(https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills;
https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices).
"Agents with a filesystem and code execution don't need to read the entirety of a
skill into their context window" (Anthropic engineering, verbatim).

**Invocation control (Claude Code, verified table):**
| Frontmatter | User invokes | Agent auto-invokes | Description in context |
|---|---|---|---|
| (default) | ✓ | ✓ | always |
| `disable-model-invocation: true` | ✓ | ✗ | not preloaded |
| `user-invocable: false` | ✗ | ✓ | always |

Use `disable-model-invocation` for side-effecting actions you want to time yourself
(`/commit`, `/deploy`); use `user-invocable: false` for pure background knowledge
that "isn't a meaningful action for a user to take"
(https://code.claude.com/docs/en/skills).

**Discovery & precedence:** skills live at enterprise / personal (`~/.claude/skills/`)
/ project (`.claude/skills/`) / plugin scope; enterprise > personal > project, and any
of them overrides a bundled skill of the same name. Nested `.claude/skills/` under a
monorepo subdir load on demand and disambiguate as `apps/web:deploy`
(https://code.claude.com/docs/en/skills). **Skill descriptions share a context budget
(~1% of the window); when it overflows the least-used skills' descriptions are dropped
first** — so a large library must keep descriptions tight and distinct
(https://code.claude.com/docs/en/skills).

**Dynamic context injection (Claude Code-specific, powerful for Manju):** a `` !`cmd` ``
line in the body runs *before* the agent sees the skill and inlines the output. Example
in the docs pulls a live `git diff HEAD` into the prompt
(https://code.claude.com/docs/en/skills). Manju's analog: a skill body could inline
`` !`manju status --json` `` or `` !`manju explain` `` so the agent's craft advice
arrives already grounded in the current project state.

### 1b. How OTHER agent products package domain expertise

- **Cursor rules (`.cursor/rules/*.mdc`).** Four activation modes mirror Claude's
  triggering: **Always Apply / Auto Attached (globs) / Agent Requested (description) /
  Manual (@rule)**. Community best practice: keep always-apply rules **under ~200
  words** ("token tax"), **5–8 rules is the sweet spot** (1 always-on base + 3–4 glob-
  scoped + 1–2 manual), and "when you re-explain the same thing three times, it belongs
  in a rule; when a rule hasn't fired in weeks, delete it"
  (https://cursor.com/docs/rules; https://www.morphllm.com/cursor-rules-best-practices).
  *Lesson for Manju:* scope skills, don't dump everything always-on.
- **Custom GPTs = instructions vs knowledge split.** Instructions (~**8,000 char**
  soft cap) hold *rules/tone/workflow behavior*; **Knowledge files** hold *reference
  material* the GPT draws on. "Use knowledge for reference material, not rules; put
  rules, tone, workflow in instructions; NEVER repeat the same content in both"
  (https://help.openai.com/en/articles/9358033-key-guidelines-for-writing-instructions-for-custom-gpts;
  https://community.openai.com/t/instructions-versus-knowledge-files-in-customgpts/1289220).
  This is *exactly* the SKILL.md-body (behavior) vs `references/` (depth) split — and
  the pitch is identical: "turn your personal expertise into codified best practices"
  (https://mdynotes.com/everything-ive-learned-about-making-custom-gpts-so-far/).
- **CrewAI = role/goal/backstory + Knowledge sources + Tools.** An agent carries a
  *role* ("Senior Research Analyst"), *goal*, *backstory* (persona that "greatly
  reduces hallucinations"), a *tool* set, and separate *knowledge sources* (RAG over
  documents) (https://docs.crewai.com/v1.14.7/en/concepts/knowledge; https://www.ibm.com/think/topics/crew-ai).
  *Lesson:* persona/role framing + retrievable domain knowledge are treated as distinct
  layers — Manju's skills should carry a clear "you are acting as a short-video director"
  framing plus deep reference material, not blur them.
- **LangChain/RAG:** domain expertise is retrieval over a vector store, injected per
  query. Heavier machinery than a filesystem skill; the takeaway is *retrieve only what
  the current task needs* — the same instinct progressive disclosure encodes without a
  vector DB (https://dev.to/pulkitgovrani/hermes-agent-vs-langchain-vs-crewai-when-to-reach-for-each-3ea5).

**Marketplace/ecosystem reality:** Claude Code skills follow the open **Agent Skills**
standard (agentskills.io) and ship via **plugins** (a bundle of skills+MCP+commands+
hooks+agents) distributed through **marketplaces** (a GitHub repo registry). Community
marketplaces already list thousands of skills (e.g. tonsofskills.com,
travisvn/awesome-claude-skills), organized Creative&Design / Development / Enterprise /
Document (https://github.com/travisvn/awesome-claude-skills; https://claudemarketplaces.com/).
Anthropic's own creative skills (algorithmic-art, canvas-design, slack-gif-creator)
prove the format handles *aesthetic* domains, not just document plumbing.
**打法建议:** Manju should ship its skills as a real **plugin/marketplace bundle** (a
`skills/` dir any agent can install), not bury them in one CLAUDE.md — so a Claude/Codex/
Gemini user driving Manju loads exactly the craft skill the current stage needs.

### 1c. What makes a skill CAPABILITY-improving vs a mere protocol doc

Distilled from the Anthropic best-practices doc and skill-creator (all verified):

1. **Judgment/degrees-of-freedom calibration.** Match specificity to task fragility:
   *high freedom* (prose heuristics) when many approaches are valid; *low freedom*
   (an exact script, "do not modify the command") when the operation is fragile.
   The "robot on a narrow bridge vs open field" analogy
   (https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices).
   A protocol doc gives rules; a *skill* tells the agent **when to bend them.**
2. **Worked examples (input→output pairs) beat description.** "Examples help Claude
   understand the desired style more clearly than descriptions alone" — the doc's
   commit-message skill ships three concrete Input/Output pairs (ibid.). For a
   creative skill this is the single biggest lever: show a *bad* prompt and its
   *fixed* form, a *dead* opening and its *hooked* rewrite.
3. **Checklists the agent copies into its working response** and checks off as it
   goes — the doc's canonical pattern for multi-step tasks (ibid.). This is precisely
   what a QC pass or a hook-writing pass needs.
4. **Decision trees / conditional workflows** ("Creating new content? → workflow A;
   Editing? → workflow B") route the agent to the right sub-procedure without loading
   all of them (ibid.).
5. **Feedback loops** ("run validator → fix → repeat", "only proceed when validation
   passes") — the pattern that "greatly improves output quality" (ibid.). Manju's
   `check`/`qc`/`repair` loop is exactly this shape and a skill should name it.
6. **Failure catalogs as anti-patterns.** The doc's "avoid" sections (too-many-options,
   Windows paths, magic constants, time-sensitive info) are a *failure catalog* — the
   most transferable device for encoding "what amateur output looks like" (item 1d).
7. **Concise, imperative, "why" not just "what", consistent terminology, one level of
   reference nesting, ToC on any reference file >100 lines** (ibid.). A skill that
   restates what the model already knows is dead weight.
8. **It must be measurable.** skill-creator's whole loop is *baseline vs with-skill*
   evals: build ≥3 eval scenarios FIRST, measure pass-rate lift against token cost,
   A/B two versions before committing, tune the description for trigger accuracy
   (https://code.claude.com/docs/en/skills; https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md).
   **The line between a doc and a skill is: a skill has an eval that proves it lifts
   output.** Manju should ship eval prompts with each craft skill (e.g. "given this
   flat 3-shot outline, does the hook skill produce a first-3-seconds hook?").

**打法建议 (item 1, format & authoring):**
- **Ship Manju skills as a plugin bundle** (`manju-skills/skills/<name>/SKILL.md`) that
  installs into any agent's `.claude/skills/` (or the vendor equivalent), plus keep a
  copy in the repo's `.claude/skills/` so an agent working *in* a Manju project auto-
  loads them. Follow the exact format: ≤64-char kebab `name` (no "claude"/"anthropic"),
  ≤1024-char third-person `description` with concrete trigger phrases, body **<500
  lines**, depth pushed to `references/`, deterministic bits to `scripts/` that call the
  **Manju CLI** (the skill's scripts should BE `manju ...` invocations — that keeps the
  engine the single source of truth and the skill purely advisory craft).
- **Two skill archetypes, matching the GPTs split:** *reference skills* (always-listed
  background craft the agent applies inline — pacing, subtitle rules, prompt formulas)
  vs *task skills* (`disable-model-invocation`, user-timed procedures — "storyboard a
  script", "run the pro-QC pass"). Do NOT auto-trigger anything that spends money.
- **Write descriptions "pushy" against under-triggering** (skill-creator's word): name
  the Chinese trigger terms a creator actually types (完播率, 分镜, 字幕, 封面, 短剧,
  穿帮) so the skill fires on real requests.
- **Every craft skill carries: a decision tree, ≥3 worked before/after examples, a
  copy-in checklist, a failure catalog, and an eval file.** That is the doc-vs-skill line.

### 1d. The LESSONS a skill library must encode — amateur vs professional AI-video

This is the substance the skills must carry. Organized as the seven craft domains, each
with the hard numbers a skill can turn into a checklist/decision-tree.

**(i) 叙事节奏 / pacing — the hook and 完播率 (the single biggest lever).**
- **Hook in the first 3 seconds is quantified:** TikTok clips holding **70–85%
  retention at 3s get ~2.2× more views**; **>65% at 3s → 4–7× more impressions**;
  **<60% → minimal promotion**; **84.3% of 2025 viral clips deployed a hook trigger
  within 3s** (https://insights.ttsvibes.com/tiktok-first-3-seconds-hook-retention-rate/).
  YouTube Shorts: past-3s retention **>70% good, <60% rework the hook**
  (https://www.opus.pro/blog/youtube-shorts-hook-formulas).
- **Completion falls off a cliff with length:** **<15s → 92%; 16–30s → 84%; 31–60s →
  68%; 1–3min → 42%** avg completion
  (https://insights.ttsvibes.com/tiktok-first-3-seconds-hook-retention-rate/). Platform
  completion baselines: **Shorts ~73%, TikTok ~78%, Reels ~65%**
  (https://virvid.ai/blog/ai-shorts-increase-retention-watch-time).
- **[CN] 黄金3秒 / 完播率 doctrine:** the algorithm weights **2秒跳出率 and 5秒完播率**,
  comparing you against same-category clips in the 流量池; high 完播 → bigger pool.
  Douyin's disclosed 2025 model scores **100+ behaviors** (完播/点赞/评论/收藏/复访/分享)
  (https://zhuanlan.zhihu.com/p/1897014736534631373; https://www.zhihu.com/question/464416465).
- **Structure = Hook (0–3s) → Value drop (4–15s) → Payoff (16–45s) → CTA (last 5s)**;
  save the most surprising beat for last; **pattern interrupt every 2–3s**, a deliberate
  reset (angle/music/SFX) around the **25–35s** drift point; "a tight 20s cut beats a
  slow 45s one" (https://www.socialync.io/blog/short-form-video-structure-guide-2026;
  https://www.opus.pro/blog/youtube-shorts-hook-formulas).
- **[CN] 10 encodable hook archetypes:** 好奇/痛点/制造焦虑/价值展示/利益前置/数字冲击/
  避坑/权威/内幕/反转 — with 信息前置·爆点前置·悬念·视觉冲击; openings to BAN:
  "大家好我是…", over-filtered face close-ups, empty "震惊!/美炸了!"
  (https://www.ixunke.com/article/562).

**(ii) 分镜设计 / shot design.** 180° rule (don't cross the axis — the classic beginner
flip), cut-on-action to hide the cut, avoid jump cuts in continuity, vary shot sizes
(a single unbroken talking head loses viewers). **Baseline avg shot length 4–6s; a
meaningful change every 3–5s** (viewers need ~3s to absorb, attention fades past ~5s)
(https://www.studiobinder.com/blog/how-does-an-editor-control-the-rhythm-of-a-film/;
https://www.editorskeys.com/blogs/news/5-editing-mistakes-that-make-your-videos-look-amateur-and-how-to-avoid-them).
**[CN] 景别** 远/全/中/近/特写; **运镜** 推拉摇移跟升降甩; 分镜头脚本 = 景别·内容·台词·
时长·运镜·道具 (https://zhuanlan.zhihu.com/p/127208419). Amateur smells: aimless push-in,
shaky follow, hard transitions across opposing motion, flat emotion
(https://blog.csdn.net/2301_79425796/article/details/141367861). *[the ~0.5–1.5s/move,
rhythm-change-every-30s cadences are creator-blog heuristics, **(unverified)** as
official].*

**(iii) Per-vendor prompt craft (feeds Manju's existing prompt-compiler advisories,
ROUND-U item 6).** Cross-model truth: **all models fail in the same places — hands,
fast motion, face continuity, mismatched audio**
(https://www.dualview.ai/blog/ai-tools/best-ai-video-models.html). Model specifics:
*Runway Gen-4* — prompt the MOTION not the appearance (image sets the scene), positive
phrasing only (negatives unsupported), one primary + one secondary motion
(https://help.runwayml.com/hc/en-us/articles/39789879462419-Gen-4-Video-Prompting-Guide).
*Kling* — Subject+Action+Context(3–5 elems)+Style; 3.0 "thinks in shots not keywords",
lead with camera language; `++element++` weighting; **Elements max 2–4 refs (>4
confuses)**; add motion endpoints ("settles back"); camera specs are stylistic not
optical (https://blog.fal.ai/kling-3-0-prompting-guide/; https://fal.ai/learn/devs/kling-2-6-pro-prompt-guide).
*Sora 2* — shot-list structure, one camera move + one subject action, timing in beats,
name **3–5 color anchors** to stabilize palette, **stitch two 4s clips over one 8s**
(https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide).
*Consistency workflow* — 3–5 reference angles (front/profile/¾/back), short 4–5s clips
stitched (cuts reset drift), prompt actions not the character's outfit (re-describing
triggers reinterpretation), first/last-frame locking, face-swap in post for stubborn
drift (https://www.kittl.com/blogs/ai-video-character-consistency-workflow/). *These
corroborate ROUND-U item 6 and item 4/5 — the skill is the natural home for them.*

**(iv) 字幕规范 / subtitles (Manju already burns CJK captions — these are the numbers
its caption skill should enforce).**
- **CJK chars/line:** Netflix Simplified-Chinese **16/line** (18 SDH); **portrait 9:16 ≈
  60% of landscape → plan ~9–10 CJK chars/line** — which matches Manju's round-N fix
  that made `max_chars_per_line` real
  (https://partnerhelp.netflixstudios.com/hc/en-us/articles/215986007-Chinese-Simplified-Timed-Text-Style-Guide;
  https://subhero.io/blog/subtitle-standards-guide).
- **Max 2 lines. Reading speed [CN] CJK ~9 CPS adult** (7 children, 11 SDH); Latin
  17–20 CPS. **Cue min ~833ms / max 7s; min gap 2 frames (~83ms). Align cue changes to
  camera cuts.** (same Netflix/Subhero sources).
- **[CN] vertical safe area 1080×1920:** keep content in central ~70%; **top ~150px**
  (status bar), **bottom ~300px** (like/comment UI), sides ~50px — caption band must sit
  above the bottom-300 UI zone
  (https://www.secaiyun.com/docs/douyin-kuaishou-video-size-specification-guide-2026-06-02.html).
- **[CN] styling** (Netflix zh): white, sans-serif, **no italics**, full-width interpunct
  · in names, **no periods/commas — spaces instead**. Karaoke-caption amateur tells:
  cap **4–6 words/line**, 50–100ms between word transitions (no gap = strobe), strong
  highlight contrast, strip filler words, **test on a phone**
  (https://vidno.ai/blog/karaoke-style-word-highlight-captions).

**(v) BGM / 音效 / sound.**
- **Loudness:** short-form consensus **−14 LUFS integrated, −1.0 dBTP** ceiling across
  Shorts/Reels/TikTok (some sources put TikTok louder ~−10 to −9)
  (https://clickyapps.com/creator/video/guides/lufs-targets-2025;
  https://www.criticallisteninglab.com/en/learn/loudness/social-media). *Manju's round-Q
  QC checks clipping at peak ≥ −0.1 dBFS and silence at ≤ −50 dB RMS but has no LUFS
  target — a loudness advisory to −14 LUFS is a clean skill+QC add.*
- **Ducking ≥15–20 dB below voice** ([CN] 剪映: music ~30% under voice + 2s fade-out)
  (https://filmora.wondershare.com/audio-ducking.html; https://diantuoyi.com/article/7816.html).
  *Manju already ducks via sidechain (round N) — the skill states the target depth.*
- **SFX ≤ ~30% of the music track; transition whoosh only at key beats**; **[CN] 卡点:**
  cut on the 鼓点, lyric-free ~30s tracks
  (https://www.huishenghuiying.com.cn/rumen/hshy-jjskd.html).

**(vi) 封面 / 标题.** Faces on covers → **~35% higher CTR**; **cover text 3–5 words**,
bold 700+ sans, ~20–30% of frame, contrasting outline; minimalist covers **~18% higher
CTR** than cluttered; good CTR 5–7% (https://www.pictiny.dev/blog/thumbnail-best-practices;
https://vidiq.com/blog/post/youtube-custom-thumbnails-ctr/). **[CN] 小红书封面 3:4**;
drivers 与我相关/对我有用/让我好奇 (https://www.ixunke.com/article/411). Titles: front-load
keyword in first 3–5 words, **numbers +20–30% CTR (odd numbers beat even ~20%)**,
curiosity gap that the video MUST close; **[CN] 疑问句 > 感叹句**; **ban absolutes 最/第一/
唯一 and guarantees (regulatory)** (https://humbleandbrag.com/blog/youtube-title-best-practices;
https://www.check51.com/news/info/48578.html).

**(vii) AI-video failure smells — the catalog (the QC/repair skill's spine, ties to Item
6 below).** Faces: morph/identity drift, dead eyes, plastic skin, teeth/hair flicker.
Hands: 4/6/7 fingers, merging, changing count between frames. Motion: floaty weightless
glide, too-smooth (no micro-jitter), physics pass-through, interaction misses (fork
misses mouth). Objects: drift/teleport/vanish, held-item morph, tiling textures,
impossible reflections. Temporal/lighting: "fever-dream" style shift mid-clip, exposure
flicker, cross-cut background inconsistency, over-saturated synthetic palette. Text:
garbled in-scene signage, logo morph, watermark ghosts. Audio: lip-sync desync on
bilabials, no breath, flat TTS cadence. **The consensus mitigation is the same across
every source: short 3–5s clips + stitch, reference locking, hide hands, add text/logos
in post, consistent seeds/palette**
(https://genra.ai/blog/why-ai-videos-look-fake-how-to-fix;
https://caniphish.com/blog/how-to-spot-ai-videos; https://www.opus.pro/blog/ai-slop-aesthetic-12-tells;
https://www.kittl.com/blogs/ai-video-character-consistency-workflow/).

**打法建议 (item 1d → skills):** turn each domain into a skill with (1) the numeric
thresholds above as a **copy-in checklist**, (2) **before/after worked examples**
(a flat opener → a 好奇-hook opener; a "no extra people" negative → "a single person
walking"; an 18-CJK-char caption line → a 10-char split), (3) a **decision tree** (e.g.
"clip > 5s AND single take AND a face on screen → recommend splitting to 4–5s + ref-lock,
because drift/flicker compound past 5s"), and (4) a **failure catalog** the QC skill
scans against. Critically, **wire these to Manju's existing engine**: the pacing skill
reads `manju status`/storyboard shot durations; the subtitle skill sets
`rules.captions.max_chars_per_line` (already enforced) and warns when a cue > 7s or a
line > 10 CJK chars; the sound skill sets duck depth + a −14 LUFS advisory; the failure
catalog feeds the `qc_vision` `needs_vision` slots. The skill never generates — it
advises the agent which `manju` truth-edits and which provider prompts produce pro output.

---

## Item 2 — CREATIVE WORKFLOW AS PRODUCT (the staged entry funnel)

The design question: what is Manju's *creation entry* — the staged funnel from a bare
idea to a storyboard the director loop can execute? The field gives a clear answer.

### 2a. The archetype — LTX Studio's semantic funnel (most granular, verified)

LTX exposes the richest idea→storyboard funnel and is the model to borrow from:
**Concept/Prompt → Synopsis (+title+cast+style) → Cast/Elements → Style/Aspect →
Storyboard → Scenes → Shots (still frame each) → Timeline → Export**
(https://www.cined.com/ltx-studio-deep-dive-the-first-ai-based-full-editing-suite/;
https://uraiguide.com/ltx-studio-guide/; https://ltx.io/studio/platform/ai-storyboard-generator).
Stage-by-stage artifacts and the AI-vs-human split:
- **Concept** — user types a few lines + genre; AI proposes a **synopsis + title + cast +
  style options**. *(User need not write a full script.)*
- **Cast** — per-character fields **Name / Age / Essence / Appearance / Clothing / Voice**;
  the human locks each; saved as reusable **Elements**. This is the consistency lock.
- **Style/Aspect** — aspect ratio + visual style (cinematic/anime/comic/3D…).
- **Storyboard (pivotal)** — AI expands synopsis/script into **scenes → shots → one still
  per shot**, auto-extracting characters/objects/locations as **Elements** tagged across
  shots. Script stays attached so dialogue lines up with panels.
- **Scene edit** — Location / Lighting / Weather per scene. **Shot refine** — shot type,
  angle, camera motion+scale, keyframes, duration (3/6/9/12s), negative prompt, fps.
- **Timeline/export** — send shots to a timeline; export MP4 / pitch deck / Premiere-DaVinci.

The newer **Storyboard Generator** (script-first) inserts an explicit **"review Elements
before Generate"** gate: upload script → pick model → aspect → **edit auto-extracted
Elements** → Generate (https://ltx.io/blog/ltx-storyboard-generator-update).

### 2b. The canonical funnel and the exact nouns the field uses

The dominant real-world funnel collapses to five moves:
**idea/prompt → script → scene list → per-scene visual generate → edit loop → export.**
Which stages products actually name (verified across the survey):

| Canonical stage | The noun products ship | Who exposes it |
|---|---|---|
| Idea/Concept | "concept" / "prompt" / "idea" | LTX, InVideo, HeyGen, Sora, Fliki, Runway |
| **Synopsis** | "synopsis"; "preset storylines" | **Only LTX** names it |
| Treatment / **Plan** | **"Video Plan"** (HeyGen); implicit (LTX/InVideo) | HeyGen (explicit), LTX, InVideo |
| **Script** | "script" / **脚本** / **文案** | near-universal — the true common artifact |
| Beat sheet | — | **nobody** (whitespace) |
| Scene list | "scenes"/"Scene Settings"/**分段·分镜**/scene cards | everyone |
| Shot list | "shots"/"Master Storyboard"/**分镜脚本** | LTX, Runway, InVideo(pro), 即创 |
| **Storyboard** | "storyboard" / **分镜脚本** / 分段素材 | LTX, Pictory, Runway, Sora, 即创, 智影 |
| Generation | "Generate"/**成片·合成视频** | all |

Exact vendor cheat-sheet: **Pictory** Script→**Scene Settings**→Choose Layout→**Storyboard**→
Editor (scenes split on "sentence breaks, line breaks, or both")
(https://pictory.ai/academy/how-to-turn-script-into-video-pictory-ai). **InVideo** Prompt
(Choose a workflow)→Generate→**"Magic Box"** natural-language edit (re-renders only affected
scenes <15s)→Publish (https://invideo.io/make/ai-video-generator/). **HeyGen** Prompt→
Configure→**Video Plan** (approvable pre-generation outline of structure+pacing)→Generate→
Edit (https://www.heygen.com/academy/video-agent). **Sora 2 "Storyboard"** = a timeline of
scene cards, each a text description or an image/video keyframe, per-scene 5/10/15/20s
(https://openai.com/index/sora/). **Runway 2026 Agent** takes a script → suggests a
**Master Storyboard** → generates clips → edits a rough cut
(https://resource.digen.ai/runway-agent-video-editing-features-2026/). **Fliki** idea→
scene-by-scene script→per-scene visual→captions/music→export
(https://fliki.ai/features/idea-to-video).

### 2c. Two architecturally different funnel FAMILIES (the key design signal)

1. **Generative content funnel** (LTX, InVideo, Pictory, Fliki, HeyGen, Steve, 即创, 智影):
   AI *proposes structure from an idea* — concept→synopsis/script→scenes→shots→storyboard→
   generate. The human's decisions cluster at three gates: **(a) style/format, (b) cast/
   Elements approval (the consistency lock), (c) per-scene text & asset swaps.**
2. **Template-first funnel** (剪映 一键成片 / 剪同款 / 模板): the storyboard is a
   *pre-authored reusable product asset*; the human's funnel inverts to **"pick structure →
   drop material into slots → edit,"** and AI's job is *matching* (template/asset/duration/
   angle fit), not authoring. 剪映 externalizes the storyboard as a shareable community
   **template** — a direct "creative workflow as product" precedent
   (https://www.jb51.net/softjc/841101.html; https://zhuanlan.zhihu.com/p/685627370).

**即创 (抖音/巨量) is the Chinese-first generative archetype worth mirroring:** 商品信息/关键词
→ **AI视频脚本** (口播/直播/**分镜脚本** "含对话、画面动作") → **智能成片** (auto 剪辑+配乐+
字幕+特效, "10秒生成", multiple broadcast-ready cuts + 一键过审) → 成片
(https://aixzd.com/oceanengine; https://blog.csdn.net/weixin_40774379/article/details/135763363).
Its storyboard is a **text 分镜脚本** (shot list with dialogue+action), not still frames —
exactly Manju's storyboard shape. **(Note: 腾讯智影 discontinued 2025-06-30 — documented
historically only; its 文本→文案→分段(分镜)→数字人+配音→合成 pattern still instructive:
https://finance.sina.com.cn/roll/2025-04-26/doc-ineupaav8472953.shtml.)**

### 2d. The three named "approve-before-spend" gates the market ships

Only three products name an explicit human-approval checkpoint between plan and generation:
**HeyGen "Video Plan"**, **InVideo "Always Ask mode"** (per-shot approval), and **LTX
"review Elements before Generate."** This is precisely Manju's differentiator from
ROUND-U item 9 (the six-step director contract nobody completes) — the market validates
the *gate*, Manju already *enforces* it engine-side (ask_before + dry-run).

**打法建议 (item 2):**
- **Adopt LTX's stage nouns onto Manju's existing scaffolds and director loop.** Manju's
  `manju new` already scaffolds story/brief.md, outline.md, script.md; formalize the funnel
  as named stages the director loop walks: **立意/Concept (brief.md) → 梗概/Synopsis →
  剧本/Script (script.md) → 分镜/Storyboard (the shot table, already the /storyboard page)
  → 生成/Generate.** Each stage = one truth artifact the agent proposes and the human
  confirms — mapping 1:1 onto propose→confirm→execute.
- **Fill the whitespace nobody ships: an explicit 梗概/Synopsis and 节拍/Beat-sheet stage**
  between script and storyboard. No surveyed product exposes a beat sheet; for a short (hook
  →build→payoff→CTA, item 1d) a 4-beat sheet is the natural artifact and a genuine
  differentiator. Make it a *skill* (「梗概/节拍」skill) that the director loop invokes to
  propose the beats, human edits, then the storyboard skill expands beats→shots.
- **Make "cast/Elements approval" a first-class gate.** LTX's per-character Name/Age/
  Appearance/Clothing/Voice lock IS Manju's asset matrix + voices.yaml (ROUND-U UA). The
  funnel should force a **character-sheet confirm before any shot generation** — this is
  both the consistency lock (item 3) and the ask_before-before-spend moment.
- **Support BOTH funnel families.** Generative (idea→storyboard) via the director loop is
  the default; also honor template-first — a Manju project/preset kit (rounds N/O) IS a
  reusable "template," and `manju new --preset` is the "pick structure → fill slots" entry.
  Position preset kits explicitly as 剪同款-style reusable storyboards.
- **Keep the "Magic Box" re-render-only-affected discipline** — Manju already does this
  (content keys re-render exactly the changed segment). Surface it as the natural-language
  edit affordance in the director loop: "delete shot 3 / change the voiceover on shot 5"
  → one truth edit → incremental rebuild.

---

## Item 3 — LONG-FORM / MULTI-EPISODE (短剧 & AI series)

The design question: what data structure lets Manju hold a 60+ episode series and keep
人设/世界观 coherent across it? The Chinese 短剧 industry has hardened conventions worth
copying wholesale, and the answer is a **three-layer data model**.

### 3a. 短剧 production conventions (the format Manju's presets should encode)

- **Regulatory definition (NRTA/广电):** 微短剧 = networked drama, single episode "从几十秒
  到15分钟左右," with a clear主线 and continuous plot
  (http://www.nrta.gov.cn/art/2022/12/26/art_113_63041.html;
  https://baike.baidu.com/item/网络微短剧/63680830). Three tiers: 长视频横屏剧 / 短视频
  竖屏剧 (抖音/快手) / 小程序微短剧.
- **Episode counts & duration (the hard numbers):** **竖屏/小程序: 1–3 min/集, 80–100 集/部**;
  **横屏"缩水版": 7–10 min/集, 通常 ≤30集**; a deliberate premium **12集横屏** middle
  ground (https://zhuanlan.zhihu.com/p/1941534366003079041; https://www.jiemian.com/article/6538986.html).
  AI 短剧 examples ~15 min/episode at ~100–400 RMB/episode
  (https://china.cnr.cn/gdgg/20260429/t20260429_527605015.shtml).
- **卡点/付费点 (paywalls) drive the whole structure** — writers decide "在第几集让用户掏钱"
  *before* writing. For an 80-集 series: **卡一 ep 8–12 (点燃时刻), 卡二 ep 26–30 (再起波澜),
  卡三 ~ep 60 (高潮前的高潮)**; first 3 free = **黄金三集**
  (https://lmtw.com/mzw/content/detail/id/242101). Every episode ends on a **钩子** that
  must stop the viewer in **3s** and provoke curiosity within **60s**; 编剧三点论 = 卡点+
  冲突点+反转点, reversals "快、狠、合理" (https://lmtw.com/mzw/content/detail/id/241607).
- **备案 (filing) regime — a compliance gate a Chinese-first tool must know:** since
  **2024-06-01** no 微短剧 broadcasts without a 备案号; 分类分层审核 by investment (2026
  thresholds raised: 重点 ≥300万, 普通 ≥100万; "upload-first-file-later" ended 2026-04-01;
  AI dramas required 备案 by 2026-03-31)
  (https://news.bjd.com.cn/2024/05/21/10780102.shtml;
  https://www.byerisk.com/blog/short-drama-2026-compliance-5-takedown-traps). 快手 星芒短剧
  funds AI short-drama via its 可灵/Kling model (https://www.chinaventure.com.cn/news/78-20240725-382175.html).

### 3b. 长剧本 → 分集 splitting conventions

- **红果 "编剧第一课" 三层次拆解法 (authoritative platform guide):** 宏观 (one sentence for
  the series) → 中层 four-stage arc **前10集 / 10–30集 / 30–60集 / 60集–结局** (each a 核心任务
  + 情绪递进) → 微观 per-episode frame **开场3秒设钩 → 冲突建立 → 反转设计 → 结尾卡点**; ban
  水台词 (https://news.qq.com/rain/a/20251031A04KVO00). Craft consensus: **每集结尾留悬念,
  每3–5集一个小高潮, 每10集一个大反转**; single-episode = 钩子–发展–反转–收尾, **500–1200字**
  (https://zhuanlan.zhihu.com/p/708951818). *[These per-episode cadences are practitioner
  consensus, **(unverified)** as a single official standard.]*
- **Pitch document structure:** 一句话简介 (≤100字) · 题材 · 故事梗概 (300–400字) · 故事大纲
  (800–1000字, 每10集标一卡点) · 人物小传 · 分集梗概(集纲) · 人物关系图
  (https://zhuanlan.zhihu.com/p/1941534366003079041).
- **Novel→短剧 (网文改编):** a 几十万字 novel compressed to ~5万字 in three moves — 取框架
  (keep 爽点/核心CP/情感节点) → 世界观 → 爽点节奏+情绪弧线; 番茄小说 pushed 6,700+ works into
  adaptation in 2025 (https://zhuanlan.zhihu.com/p/17691500866). *Directly feeds Manju's
  existing novel→script import (ROUND-Q item 1).*
- **Western writers-room contrast (best practice):** bible/premise → season arc → **break each
  episode (act breaks FIRST)** → **beat sheet** → **outline (9–14 pp)** → script; **A/B/C
  storylines** (A = backbone, B/C orbit, "blend" A→C→A→B)
  (https://scriptmag.com/features/writers-room-101-beats-breaking-blending;
  https://fiveable.me/tv-writing/unit-2/a-b-storylines/study-guide/waDPbIa19i9duUlP). The
  beat-sheet layer is exactly the whitespace item 2 identified.

### 3c. The data structure — SERIES BIBLE vs EPISODE SCRIPT vs CONTINUITY (the definitive answer)

Every source converges on a **three-layer model**: an immutable/slow global layer, a
per-episode layer, and a continuity layer that reconciles them. The cleanest shipped
exemplar is the `oh-story` Claude Code novel skill's file tree
(https://github.com/worldwonderer/oh-story-claudecode):
```
{series}/
├── 设定/     (SERIES BIBLE)  世界观/ · 角色/(one file per character) · 势力/ · 关系.md · 题材定位.md
├── 大纲/     大纲.md(series) · 卷纲_第N卷.md · 细纲_第NNN集.md(beats, characters present, hooks)
├── 正文/     (episode scripts / manuscript)
├── 追踪/     (CONTINUITY)  伏笔.md · 时间线.md · 角色状态.md
```

**(A) SERIES BIBLE — global, shared across all 60+ episodes.** *Series meta* (title, logline,
题材定位, tone + 2–3 comparables, format 竖屏/横屏 + 集数 + duration, platform + 备案 tier).
*World/世界观* (setting, backstory/mythology, **world rules** — power system / 金手指, costs,
limits; 势力 one record each). *Characters/人设 — one record per character = the "character
bank" entry* — identity (`character_id` UUID, name, aliases, age), 小传 (backstory, family,
personality external-vs-subconscious, wants/needs, arc), and the **visual-identity payload
(face/hair/body, default outfit + color scheme primary/secondary/accent, voice, a reference
set of 4–6+ imgs front/¾/profile/back + expressions each stored with its prompt+seed, plus a
LoRA/embedding pointer and per-tool IDs)**. *Relationships* (关系图). *Global visual/audio
style* + reusable **location & prop Elements** (each with its own reference set + id). *Series
arc* (the 红果 four stages + paywall strategy 卡一/卡二/卡三 + themes). Western show-bible
sources concur: logline, world+rules, **4–8 series regulars** at ½–1 pp each, relationships,
tone, visual style (https://industrialscripts.com/tv-show-bible/;
https://www.finaldraft.com/blog/building-your-tv-series-bible-in-final-draft). Chinese 人物
小传 fields: 姓名/性别/年龄/性格优缺点/服装发型造型/长相/家庭/感情前史/目标/童年/外在-vs-潜意识
性格 (https://www.sohu.com/a/435322734_257537).

**(B) EPISODE SCRIPT / 分集剧本 — per-episode.** `episode_number`, title, duration, free/paid +
卡N marker; **集纲** (logline, A/B/C threads, beat sheet, act breaks, 3-sec hook, mid conflict,
reversal, ending 钩子); **scene→shot breakdown** (per scene: location Element ref, time/light;
per shot: shot#, duration 5–10s, camera angle, **character IDs present → pulls their bank refs**,
action, 台词, prop refs, seed+prompt, model). **Characters reference the bible by ID and store
ONLY per-episode state deltas** (wardrobe change, injury, emotion) — never redefine appearance
locally.

**(C) CONTINUITY / TRACKING — reconciles A↔B (the piece most AI tools omit).** 伏笔.md
(foreshadowing planted-→-paid register), 时间线.md (internal chronology), 角色状态.md
(per-character state snapshot per episode: location, wardrobe, injuries, knowledge, relationship).
ViMax's "dependency-aware visual-consistency mechanism" tracks character/environment state across
shots and picks the first-frame reference from prior-timeline storyboards
(https://github.com/HKUDS/ViMax; https://arxiv.org/abs/2606.07649). **Showrunner (Fable) explicitly
"resets characters between episodes"** — the exact failure a continuity layer prevents; multi-
episode arcs remain the hard problem (https://dramatica.com/blog/ai-and-storytelling-why-multi-episode-arcs-challenge-generative-models).

### 3d. Character banks (AI consistency, the 人设-across-60-episodes answer)

- **Reference sheet primitive:** 4–6 imgs/character (front/¾/full-body/action) + 4–6 expressions,
  compiled to one sheet that "becomes the input for every generation," each saved with **prompt +
  seed** (https://www.aividpipeline.com/blog/character-consistency-ai-video).
- **LoRA per character:** train on **15–30 imgs**, apply at **0.7–0.9 weight**; "most robust when
  the drama is fixed at ~20 episodes"
  (https://www.aimagicx.com/blog/long-form-ai-video-character-consistency-guide-2026).
- **Seed locking** for batches of related shots; **batch shots by similarity not chronology**
  (close-ups → ¾ → wide → supporting → establishing); a 15-min video = **40–80 shots** at 5–10s,
  2–3 variations/shot, expect **50–70% usable** (same source).
- **Per-tool character-ID systems** (ties to ROUND-U items 4/5): Midjourney `--cref`/`--cw`
  (v7 → **Omni Reference**) (https://updates.midjourney.com/character-refs/); Runway Gen-4
  References (single hi-res front portrait, "identity encoding"); **Kling 3.0 uploads 3–5 refs →
  identity embedding**; **Sora 2 "创建角色" API → character ID → `@角色ID`**
  (https://blog.lusyoe.com/article/ai-anim-character-consistency.html;
  https://www.aimagicx.com/blog/long-form-ai-video-character-consistency-guide-2026). Chinese
  **CHAR-ID 资产库 schema**: UUID + four-view+expression sheet + structured metadata (head/face/
  hair/outfit + color scheme) — principle = **角色资产标准化**
  (https://blog.lusyoe.com/article/ai-anim-character-consistency.html).
- **LTX Elements** = the shipped archetype: a saved reusable **character/location/prop/style/brand**
  asset tagged by name in any prompt, applied consistently across every scene ("protagonist in
  minute 1 = minute 80") over a **Project → Scenes → Shots** hierarchy
  (https://help.ltx.io/hc/en-us/articles/33578393195922-Introduction-to-Elements). *LTX's
  feature-length/60-episode claim is vendor positioning — no shipped cross-episode series-container
  workflow was found* **(unverified)**.

**打法建议 (item 3):**
- **Introduce a SERIES layer above Manju's project.** Manju's current bible is project-scoped
  (characters/scenes/props/voices.yaml — the ROUND-U UA asset matrix). For 60+ episodes, add a
  **series bible** (a shared parent) + **per-episode projects that reference character IDs into
  it** + a **continuity layer** (`追踪/`: 伏笔/时间线/角色状态). Manju's asset matrix already IS
  the character-bank; promote character records to have a stable `character_id`, a reference set
  (with prompt+seed per image — Manju already stores spec_snapshot+seed on takes), and per-tool ID
  slots (Kling refs / Runway refs / Sora character-ID / LoRA pointer) — reusing ROUND-U items 4/5's
  per-vendor ref mapping.
- **Episode scripts store only state deltas.** Appearance lives once in the series bible; an
  episode's shot names `character_id` + a per-episode wardrobe/injury/emotion delta. This is the
  single rule that keeps 人设 stable across 60 episodes and is exactly what Showrunner-class tools
  get wrong.
- **Ship a 短剧 preset + a 分集 skill.** A `short_drama_series` preset kit encodes the format
  (竖屏 1–3min, 80–100集, 黄金三集, 卡一/卡二/卡三 targets, 每集钩子). A **「分集拆解」skill**
  encodes the 红果 三层次拆解法 (宏观→四阶段→per-episode 3秒钩→冲突→反转→卡点) and the
  novel→短剧 取框架 compression, so the driving agent splits a 长剧本/novel into episode scripts
  with hooks and paywalls landing on-convention. A **「人设一致性」skill** encodes the character-
  bank build (4–6 refs, seed lock, batch-by-similarity, per-tool ID mapping, 50–70% usable
  expectation).
- **Add a continuity-tracking surface** (`manju continuity` or a bible file trio) that the QC/
  director loop reads — foreshadowing register, timeline, per-episode character-state — so a
  cross-episode inconsistency (wardrobe/knowledge/timeline) is a *detectable* finding, not a
  surprise. This is the differentiator no AI-series tool ships.
- **备案/compliance advisory** for the Chinese path: a preset/skill note that a 微短剧 needs a
  备案号 before broadcast and flags 标题/内容 compliance (ties to item 1d's ban on absolutes) —
  advisory only, Manju never publishes.

---

## Item 6 — VISUAL QC JUDGMENT STANDARDS (professional review → AI video)

Feeds Manju's `qc_vision` `needs_vision` slots (still gated on a real vision vendor).
The deliverable: a ready-to-adapt, severity-tagged criteria list grounded in professional
review practice.

### 6a. The professional grounding (four citable standard bodies)

- **Continuity supervision (场记 / script supervisor)** tracks, shot-to-shot: wardrobe
  state, hair/makeup (sweat/blood/injuries), prop position & set dressing (photograph
  frame start & end of every setup), **screen direction / 180° axis**, **eyelines**,
  actor blocking & "hand business", match-on-action, **food/drink levels, cigarette
  length**, lighting/shadow & time-of-day, weather, dialogue beats
  (https://www.studiobinder.com/blog/script-supervisor-forms-template/;
  https://blog.kinolime.com/articles/what-is-a-script-supervisor;
  https://en.wikipedia.org/wiki/Script_supervisor). Oxford VGG even maintains an academic
  visual continuity-errors dataset (https://www.robots.ox.ac.uk/~vgg/research/continuity/index.html).
- **穿帮 catalog + IMDb Goofs taxonomy** (the ready-made label space): 穿帮 categories =
  现代物品穿越 (anachronistic objects), 工作人员/器材露出 (crew/equipment visible),
  镜头前后矛盾 (shot-to-shot contradiction — e.g. **wound migrating left→right cheek**,
  mirror-flipped 汉字), 历史细节错误 (https://zh.wikipedia.org/zh-hans/%E7%A9%BF%E5%B9%AB).
  IMDb's official set: **Continuity / Factual error / Character error / Anachronism /
  Revealing mistake / Crew or equipment visible / Boom visible / Audio-visual unsynchronised
  / Errors in geography / Plot hole**
  (https://help.imdb.com/article/contribution/titles/goofs/GFDUF27RBTQGS8UZ).
- **Netflix Production QC Glossary** — the richest professional defect taxonomy, coded
  `PREFIX-###` by department (Camera / **Image** / Lighting / Grips / Audio / **Misc** /
  Virtual-Production / Film). Directly reusable image-level codes: aliasing/moiré (I-303),
  **banding (I-304)**, compression artefacts (I-502), dead/hot pixel (I-503/504),
  pixelation (I-505), **black frame (I-702), dropped frame (I-703), freeze frame (I-704)**;
  lighting flicker (L-100); and the "revealing mistakes" class **people/crew/equipment/
  boom/camera in shot (M-300..403)** — the same class as 穿帮 & IMDb
  (https://partnerhelp.netflixstudios.com/hc/en-us/articles/21890106462611-The-Production-QC-Glossary).
  **Netflix's severity tiers are the model to adopt: Blocker (content inconsumable /
  cannot launch) / Issue (degrades experience, wouldn't block) / FYI (present but
  non-actionable)** — severity is about member-experience impact, not mere presence
  (https://partnerhelp.netflixstudios.com/hc/en-us/articles/115000353211-Introduction-to-Netflix-Quality-Control-QC).
- **PSE / harmful-flashing (legal-safety gate, ITU-R BT.1702 / Harding):** **≤3 flashes in
  any 1s**, a flash = opposing luminance change **≥20 cd/m²** with darker state <160 cd/m²,
  **any saturated-red flash is potentially harmful regardless of luminance**, applies over
  **>~25% of screen**, and regular stripe/grid patterns count the same
  (https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1702-3-202311-I!!PDF-E.pdf;
  https://en.wikipedia.org/wiki/Harding_test). **Loudness targets** (delivery-profile
  dependent): streaming **−14 LUFS / −1 dBTP**; Netflix **−27 LKFS dialog-gated / −2 dBTP**;
  broadcast EBU R128 **−23 LUFS / −1 dBTP**
  (https://partnerhelp.netflixstudios.com/hc/en-us/articles/360050414014-Loudness-and-True-Peaks-How-to-Measure-and-When-to-Flag;
  https://tech.ebu.ch/docs/r/r128.pdf).
- **Automated file-based QC tools** (Interra Baton, Tektronix Cerify, Telestream Vidchecker,
  Venera) define the *cheap signal-processing* side: blockiness, blur, moiré, mosquito
  noise, pixelation, freeze/black/blank frames, banding, video dropout, flicker, **PSE**,
  plus loudness/gamut and **burnt-in-text / QR detection**
  (https://www.interrasystems.com/file-based-qc.php).

### 6b. The consolidated AI-video QC checklist (severity + cheap-vs-vision)

Severity: **Blocker** (must not ship) / **Major** (viewer notices, fix before ship) /
**Minor** (close inspection) / **Info** (FYI). `V` = needs a vision model; `C` = cheap
(ffmpeg/OCR/analyzer, no vision). Context rule: **promote a Minor→Major/Blocker when the
defect lands on the hero subject / foreground / lingers; demote when incidental/background/
sub-second** (Netflix's member-impact logic).

**A. Character identity** — A1 face/identity stable within a shot (no morph into another
person) **Blocker·V**; A2 identity matches across shots in a scene **Major·V**; A3 stable
marks (scars/tattoos/hair) don't fade/migrate (穿帮) **Major·V**; A4 face count stable, no
object→person morph **Major·V**.

**B. Outfit / wardrobe** — B1 costume type/color/state stable within shot (no clear→blue
cup flip) **Major·V**; B2 wardrobe matches across cuts (buttons/sleeves/accessories)
**Major·V**; B3 accessories not disappearing/reappearing **Minor→Major·V**; B4 clothing
physics (no melt into skin) **Minor·V**.

**C. Scene / background** — C1 background objects keep shape/position (no cup→blob)
**Major·V**; C2 props stay put/same state, food/drink levels, cigarette length **Major·V**;
C3 no anachronisms / out-of-world objects **Major·V** (context-dependent on intended
setting); C4 no tiled AI patterns / impossible architecture **Minor·V**; C5 no hallucinated
crew/equipment/stray text in set **Minor→Major· mixed** (OCR flags burnt text, vision for
objects).

**D. Lighting logic** — D1 shadow direction consistent & matches light source **Major·V**;
D2 face lighting/reflections match environment **Minor→Major·V**; D3 time-of-day/exposure
consistent within scene **Major· mixed** (ffmpeg luminance stats flag jumps, vision
confirms); D4 no unmotivated luminance flicker **Minor·C** (frame-mean-luminance delta).

**E. Anatomy / hands / faces** — E1 correct finger count, no fused/extra digits, natural
bends **Blocker→Major·V** (Blocker if hands are hero/foreground); E2 plausible face anatomy
(no melted features, normal ears/teeth) **Major·V**; E3 natural eye behavior (blink cadence,
gaze, catchlight) **Minor·V**; E4 teeth not fused/painted-on, mouth cavity when speaking
**Minor·V**; E5 skin not waxy/plastic **Info→Minor·V** (weak, model-dependent — often
stylistic); E6 limbs don't phase through objects, plausible proportions **Major·V**.

**F. Text / typography** — F1 on-screen text legible, real language, not gibberish
**Major· C-then-V** (OCR + lang-detect, vision confirms); F2 text stable across frames (no
letter mutation) **Minor→Major· mixed** (per-frame OCR diff); F3 no mirror-flipped/malformed
glyphs, esp. CJK (穿帮) **Minor·V**.

**G. Watermark / logo ghosts** — G1 no residual generator watermark (Sora/Runway/etc.)
**Blocker· mixed** (template-match cheap for known marks, vision for faint ghosts — legal/
brand + reveals synthetic origin); G2 no unintended duplicated logos/station bugs
**Major· mixed**; G3 EXIF/metadata AI-tool tags **Info·C**.

**H. Motion / physics** — H1 no objects/limbs through solids **Major·V**; H2 gravity/
momentum plausible (no floating/frictionless slide) **Major·V**; H3 motion blur present on
fast motion, no strobing/unnatural steadiness **Minor· mixed**; H4 cause-effect correct
(hand grips object, fork reaches mouth) **Major·V**; H5 camera motion coherent, no generator
warp/judder **Minor→Major·V**.

**I. Technical / encode (all cheap — the pre-filter gate)** — I1 no black/dropped/freeze
frames **Blocker→Major·C** (ffmpeg blackdetect/freezedetect); I2 no macroblocking/pixelation
**Major·C**; I3 no banding **Minor·C**; I4 no aliasing/moiré **Minor·C**; I5 no dead pixels/
digital snow/excess noise **Minor·C**; I6 correct resolution/aspect, no bad scaling **Major·C**
(ffprobe); I7 legal video levels / in-gamut **Minor·C** (signalstats); **I8 PSE / harmful
flashing** — the highest-priority automated gate **Blocker·C** (Harding-style, ITU-R BT.1702).

**J. Audio / lip-sync** — J1 A/V sync, lips match dialogue, consonant closures, drift <~100ms
**Blocker→Major· V+C**; J2 integrated loudness on target for profile **Major·C** (ffmpeg
ebur128/loudnorm); J3 True Peak ≤ profile limit **Minor→Major·C**; J4 no dropouts/clipping/
hum **Major·C**; J5 correct channel mapping **Major·C**.

**Cross-cutting agent guidance:** (1) **Run cheap checks first (gate), vision second
(judge)** — the whole I-row + J2–J5 + F1(OCR) + G1/G3 are analyzer/OCR/metadata-catchable;
**PSE (I8) is a hard gate before anything else.** (2) **Corroboration rule:** any single
anatomy/skin/text tell (E3–E5, F, H3) is weak and several are degrading with newer models
— require ≥2 signals or hero-shot placement before escalating above Minor; human accuracy on
these tells is only ~60–75% (https://www.aivideodetector.org/blog/detect-ai-videos-manual-techniques;
https://caniphish.com/blog/how-to-spot-ai-videos). **(unverified):** Netflix True-Peak varies
−1 vs −2 dBTP by print-master-vs-streaming context — confirm against the current Sound Mix
Spec for your target profile.

**打法建议 (item 6):**
- **Adopt Netflix's three-tier severity (Blocker/Issue/FYI) as Manju QC's severity model**,
  mapping to Manju's existing finding levels (error/advisory/info) — and make severity
  **context-sensitive on the hero-subject/foreground/duration axis**, not a flat table.
- **Two-stage QC pipeline that respects §0 (Manju never calls an LLM):** stage 1 = the
  **cheap C-row**, which Manju runs itself with ffmpeg/OCR (Manju already does I1/I2-ish,
  silence/clipping from round Q; ADD PSE/Harding I8, banding I3, freeze/black I1, loudness
  J2/J3 as a −14 LUFS advisory). Stage 2 = the **V-row**, emitted as structured
  `needs_vision` slots with the exact per-shot criteria (A/B/C/D/E/F/H) for the *driving
  agent's* vision to fill — Manju supplies the checklist + frames, the agent (Claude) judges.
- **Ship this checklist as a skill (「质检判读」/visual-QC-review) with the severity table as
  a copy-in checklist and the 穿帮/continuity catalog as a failure catalog**, so a
  vision-capable agent reviewing Manju's QC frames has the professional criteria in hand.
  The skill turns Manju's `needs_vision` frame slots into a rigorous per-criterion verdict
  rather than a vague "does it look AI?" The failure catalog is the same one item 1d encodes
  — QC (detect) and prompt-craft (prevent) share it.
- **PSE is a legal-safety must-add regardless of vision availability** — it's cheap and it's
  the one defect that can harm a viewer; make it a hard gate in `manju qc`.

---

## Consolidated skill taxonomy — the skills Manju should ship

The definitive list. Each skill follows the verified Agent Skills format (§1a): a directory
`manju-skills/skills/<name>/SKILL.md` with ≤64-char kebab `name` (no "claude"/"anthropic"),
≤1024-char third-person `description` with concrete Chinese+English trigger terms, body **<500
lines**, depth in `references/`, and `scripts/` that are **`manju` CLI calls** (the engine stays
the single source of truth — skills advise, never generate). Shipped as an installable **plugin/
marketplace bundle** so any agent (Claude/Codex/Gemini) driving Manju loads exactly the stage's
craft; also mirrored in the repo `.claude/skills/` for agents working inside a project. Each craft
skill carries a **decision tree + ≥3 before/after worked examples + a copy-in checklist + a failure
catalog + an eval file** (the doc-vs-skill line, §1c).

**Loading discipline:** *reference skills* (background craft, always-listed, agent auto-loads
inline) vs *task skills* (`disable-model-invocation`, user-timed, may spend — never auto-trigger a
paid action). `paths`/`when_to_use` scope auto-activation so the ~1% skill-description budget isn't
blown (§1a, Cursor "5–8 rules" lesson §1b).

| # | name | 中文名 | Type | Contains | When the agent loads it |
|---|---|---|---|---|---|
| 1 | `manju-orientation` | 曼菊上手 | reference (`user-invocable:false`) | The agent-neutral contract (§0: Manju never calls an LLM — the agent supplies craft), the director loop (propose/confirm/execute/diff/suggest), the playbook protocol for `manju auto`, spend gate (dry-run→ask_before→`--yes`/assume_yes), truth-file model, the §10 glossary (生成来源/版本/待更新/兜底…). Injects `` !`manju status --json` `` for live grounding. | Always — first thing any agent driving Manju should read. |
| 2 | `creation-funnel` | 创作漏斗 | task | The staged entry (item 2): 立意/Concept(brief.md) → 梗概/Synopsis → 节拍/Beat-sheet → 剧本/Script(script.md) → 分镜/Storyboard → 生成. LTX stage nouns; the three approve-before gates (cast/Elements confirm, plan, per-shot). Decision tree: idea-first (generative) vs preset/template-first. | User starts a new project or asks "help me turn this idea into a video." |
| 3 | `narrative-pacing` | 叙事节奏 | reference | item 1d(i): hook-in-3s (>65%@3s → 4–7× reach), 完播率 by length (<15s 92%…), Hook→Value→Payoff→CTA, pattern interrupt every 2–3s, the 10 [CN] hook archetypes, banned openers. Reads storyboard shot durations. | Any script/storyboard/hook/完播率/retention request. |
| 4 | `shot-design` | 分镜设计 | reference | item 1d(ii): 景别/运镜 vocab, 180° rule, cut-on-action, shot-length 4–6s / change every 3–5s, shot-size variety, amateur smells. Maps to the /storyboard shot table + camera hints. | Storyboarding, shot lists, "分镜/镜头/camera" requests. |
| 5 | `prompt-craft` | 提示词工艺 | reference | item 1d(iii) + ROUND-U item 6: per-vendor formulas (Runway motion-not-appearance/positive-only; Kling Subject+Action+Context+Style, Elements 2–4; Sora one-move+one-action, 3–5 color anchors, split-to-4s), one-primary+one-secondary, split-shot advisory. Feeds Manju's prompt-compiler advisories & promptlab. | Writing/refining any generation prompt; routing to a specific vendor. |
| 6 | `character-consistency` | 人设一致性 | reference | item 3d: character-bank build (4–6 refs front/¾/profile/back+expressions, prompt+seed), seed lock, batch-by-similarity, per-tool ID mapping (Kling/Runway/Sora/Midjourney/LoRA), 50–70%-usable expectation, drift-mask tricks (short clips, cutaways, single-LUT). Ties to asset matrix + ROUND-U items 4/5. | Building/locking a character; any 一致性/角色/character-drift request. |
| 7 | `subtitle-standards` | 字幕规范 | reference | item 1d(iv): CJK ≤16/line (portrait ~10), ≤2 lines, ~9 CPS, cue 833ms–7s, align to cuts, vertical safe-area (top 150 / bottom 300px), [CN] styling (white, no italics, spaces-not-punctuation), karaoke tells. Sets `rules.captions.max_chars_per_line`; warns cue>7s / line>10 CJK. | Captions/字幕 authoring or QC. |
| 8 | `audio-finishing` | 声音收尾 | reference | item 1d(v): −14 LUFS/−1 dBTP target, duck ≥15–20 dB (剪映 ~30% + 2s fade), SFX ≤30% of music, 卡点 on 鼓点. Sets duck depth + a LUFS advisory; ties to round-N audio rules + round-T mixer. | BGM/音效/mixing or audio QC. |
| 9 | `cover-and-title` | 封面标题 | reference | item 1d(vi): face on cover (+35% CTR), 3–5-word bold cover text, minimalist (+18%), [CN] 小红书 3:4, titles front-load keyword, numbers +20–30%, curiosity-gap-that-closes, [CN] 疑问句>感叹句, ban absolutes 最/第一/唯一. Feeds round-Q branding + package cover. | Making a cover/封面/标题/thumbnail or CTA. |
| 10 | `visual-qc-review` | 质检判读 | task | item 6: the severity-tagged criteria list (A–J), Netflix Blocker/Issue/FYI tiers, context-sensitive promotion, 穿帮/continuity failure catalog, cheap-first-then-vision discipline, PSE hard gate. Turns Manju's `needs_vision` frame slots into per-criterion verdicts. | Reviewing QC frames / a vision-capable agent judging takes; "质检/穿帮/looks AI." |
| 11 | `series-breakdown` | 分集拆解 | task | item 3: 红果 三层次拆解法 (宏观→四阶段前10/10-30/30-60/60-结局→per-ep 3秒钩→冲突→反转→卡点), 卡一/卡二/卡三 paywall targets, 黄金三集, novel→短剧 取框架 compression, A/B/C threads. The series-bible↔episode-script↔continuity three-layer model. | Splitting a 长剧本/novel into episodes; any 短剧/多集/series request. |
| 12 | `series-bible` | 剧集设定集 | reference | item 3c: the three-layer data structure — global bible (世界观/世界规则/金手指/人设 records with character_id + visual payload), per-episode state deltas only, continuity layer (伏笔/时间线/角色状态). Contrasts with the Showrunner "reset" failure. | Managing characters/世界观 across multiple episodes; setting up a series project. |
| 13 | `repair-loop` | 修复闭环 | task | Manju's `check`→`qc`→`repair --op` feedback loop as a validator→fix→repeat procedure (§1c pattern): map each QC finding to the matching repair op (retime/extend/trim/croppad/voice/set_inout), mint new takes with lineage, re-verify. | After a QC pass with findings; "fix/修/repair" requests. |
| 14 | `skill-authoring` | 技能编写 | task (`disable-model-invocation`) | How to write/extend a Manju skill (this taxonomy's format): frontmatter limits, progressive disclosure, decision-tree+examples+checklist+failure-catalog+eval, baseline-vs-with-skill measurement. The meta-skill that keeps the library healthy (Cursor "delete rules that haven't fired" hygiene). | The maintainer adds or tunes a Manju skill. |

**Rollout order (highest leverage first):** 1 `manju-orientation` (the contract) → 3
`narrative-pacing` + 5 `prompt-craft` + 10 `visual-qc-review` (the three that most separate
amateur from pro output) → 2 `creation-funnel` + 4 `shot-design` + 7 `subtitle-standards` +
8 `audio-finishing` (per-stage craft) → 6 `character-consistency` + 11 `series-breakdown` +
12 `series-bible` (the long-form/consistency frontier, Manju's differentiator) → 9, 13, 14.

---

## Sources & verification notes

**Verified by direct primary fetch:** the Agent Skills format & best-practices
(github.com/anthropics/skills, platform.claude.com, code.claude.com/docs — §1a/1c all first-party);
Claude Code skills frontmatter table & invocation control (code.claude.com/docs/en/skills); LTX
Studio funnel (cined.com, uraiguide.com, ltx.io) and Elements (help.ltx.io); Pictory/InVideo/
HeyGen/Sora/Runway funnels (vendor pages + reviews); Netflix Production QC Glossary & severity
tiers & loudness (partnerhelp.netflixstudios.com — the highest-confidence QC taxonomy); IMDb Goofs
(help.imdb.com); EBU R128 (tech.ebu.ch); ITU-R BT.1702 PSE (itu.int); NRTA 微短剧 definition
(nrta.gov.cn); Netflix CJK subtitle style guide; 红果 编剧第一课 (news.qq.com); oh-story file tree,
ViMax, Showrunner (github.com, arxiv.org).

**Search-surfaced (help.runwayml.com / help.descript.com / some 知乎 & ltx.io pages 403 or
header-overflow the fetcher):** Runway/Kling prompt-guide wording; several 短剧 practitioner pages;
LTX stage nouns partly from reviews mirroring vendor copy.

**Marked (unverified):** the [CN] per-episode pacing cadences (3秒设钩 / 每3–5集小高潮 / 每10集
大反转 / 0.5–1.5s per camera move) are practitioner/creator-blog consensus, not a single official
standard; LTX feature-length/60-episode support is vendor positioning with no shipped cross-episode
container documented; TikTok exact LUFS (−10 vs −14) and Netflix True-Peak (−1 vs −2 dBTP) vary by
source/profile; Pika/some reference-count figures (ROUND-U) remain best-practice not hard API rules;
AI-tell detection accuracy (~60–75% human) and "degrading tells" caveats apply — treat any single
smell as weak evidence.
