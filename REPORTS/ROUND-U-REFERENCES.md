# ROUND-U-REFERENCES — product & OSS reference patterns for the wave-2 GUI

Date: 2026-07-07. Round U research deliverable (agent UR). Method: six parallel
web-research agents (primary-source-first: official API references, help centers,
product docs) plus the session lead's own cross-verification of the two most
load-bearing fact sets (reference-image limits, first/last-frame API fields).
Every claim carries an inline source URL; claims that could not be pinned to a
primary source are marked **(unverified)**. This landscape moves monthly — the
generation-API numbers are current as of 2026-07 and are version-gated where noted.

Companion to [MARKET-GAP.md](MARKET-GAP.md) (where Manju stands vs the field) and
[COMPETITIVE-UX-STUDY.md](COMPETITIVE-UX-STUDY.md) (how the workbench should feel).
This report is the concrete-pattern layer those two defer: exact columns, exact
status words, exact API field names, exact Chinese UI vocabulary.

---

## Headline findings (the exact-limit facts, up top)

**Reference-image limits — documented max per multi-subject feature:**

| Provider | Feature | Max ref images | Field name | Verified |
|---|---|---|---|---|
| **MiniMax / Hailuo** | Subject reference (S2V-01) | **1** (single human face) | `subject_reference:[{type:"character", image:[…]}]` | Official |
| **Runway Gen-4** | References | **3** (1–3) | `referenceImages:[{uri, tag}]` | Official |
| **Kling** | Multi-image / Elements / 多图参考 | **4** (2–4) | `image_list` (`minItems:2,maxItems:4`) / Elements `input_image_urls` | Aggregator mirror of official schema |
| **Vidu** | Reference-to-video (multi-subject) | **7** (1–7; 1–4 if a video is also supplied) | `images` (array) | Official |
| **PixVerse** | Fusion (reference-to-video) | **3** (v4.5/v5) → **7** (v5.5/v5.6/v6/c1) | `image_references:[{img_id, type, ref_name}]` | Official |
| **Pika** | ingredients (Pikascenes) | **no documented cap** (secondary: ~5) | `ingredients` (array) | Format only |

Vidu is the multi-subject leader (7). MiniMax S2V is the outlier — exactly one
face. Sources cited in full under item 4.

**First/last-frame video API — exact field names (three incompatible styles):**

| Provider | First frame | Last frame | Style |
|---|---|---|---|
| **Kling** | `image` | `image_tail` | two named fields (URL or base64; `image_tail` optional; mutually exclusive with `dynamic_masks`/`static_mask`/`camera_control`) |
| **Runway** | `promptImage:[{uri, position:"first"}]` | `…position:"last"` | positioned objects, each `position` unique |
| **Vidu** | `images[0]` | `images[1]` | positional array of exactly 2 |
| **PixVerse** | `first_frame_img` | `last_frame_img` | named **integer img_id** (pre-upload required — not URL/base64) |

Cited in full under item 5. The cross-vendor trap: aggregators (fal.ai, AI/ML API)
rename Kling's `image`/`image_tail` to `image_url`/`tail_image_url`, and KIE's
Kling 2.6 wrapper drops the tail field entirely.

---

## 1. Native cut depth — what a minimal multi-track web editing view needs

Studied: 剪映/CapCut desktop, DaVinci Resolve.

**Pattern 1a — magnetic main track + typed, stacked overlay tracks.** CapCut/剪映
build the timeline around one **main track (主轨道)** plus **overlay / picture-in-
picture (画中画)** tracks; adding a text, sticker, or effect element auto-creates a
*dedicated lane of that type*. CapCut names the overlay categories literally:
"Video overlays, Image overlays, Text overlays, Sticker overlays, Effects & filters,
and Transition overlays."
(https://www.capcut.com/resource/how-to-add-capcut-overlays;
https://www.capcut.com/resource/overlay-picture-on-video). The renderer-relevant
rule: upper track covers lower ("上层轨道画面默认覆盖下层轨道画面"), and the main
track is **magnetic** (主轨吸附 — moving a clip snaps forward to close gaps)
(https://zhuanlan.zhihu.com/p/636375314 — secondary, Chinese tutorial).

**Pattern 1b — waveform rendered on the clip body; volume as on-clip keyframes.**
Waveforms draw directly inside the audio (and audio-bearing video) clip; detaching
audio moves the waveform to its own lane below. Volume is a keyframable envelope —
a **diamond keyframe (关键帧)** next to animatable params (Position, Scale, Opacity,
**Volume**) (https://www.capcut.com/resource/how-to-add-keyframes-in-capcut).

**Pattern 1c — transitions are a clickable seam marker, not a free drop.** Between
two adjacent clips CapCut shows "a white square with a black vertical line — that's
the transition point"; click it → the transition menu. Desktop also allows dragging
a transition onto the seam. Applied transitions expose a **Duration** slider/field
and draggable edges (https://vediting.home.blog/2025/08/09/how-to-add-transitions-in-capcut/ — secondary;
https://www.createthat.ai/blog/how-to-add-transitions-in-capcut — secondary). 剪映
enforces a rule worth copying: **max transition length = half the shorter adjacent
clip** ("转场的最长显示时间为相邻素材中显示时长最短的时长的一半")
(https://zhuanlan.zhihu.com/p/721082465 — secondary).

**Pattern 1d — captions arrive pre-timed from ASR; each cue is a draggable block.**
CapCut desktop: "Text → Auto Captions, choose language and audio track, then click
Recognize" returns timestamped cues bound to the timeline
(https://www.capcut.com/help/auto-captions-in-capcut — primary). 剪映's equivalents:
**智能字幕** (recognize speech→captions), **文稿匹配** (align an existing script to the
voice), **文本朗读** (TTS, the reverse) (https://zhuanlan.zhihu.com/p/687694980 —
secondary). Each cue is retimed by dragging its edges/body and split at the playhead
with `B` (https://www.capcut.com/help/auto-captions-in-capcut — primary).

### 打法建议 (item 1)
- **Minimal credible multi-track web view =** (1) a magnetic **main video lane** + a
  small fixed set of typed overlay lanes (Video/PiP, Audio, **Caption/字幕**, optional
  Sticker/Effect), upper-covers-lower; (2) one **global playhead** with click-scrub;
  (3) **per-clip waveforms**; (4) **clip trim handles** with snap; (5) **seam-marker
  transitions** (click the cut, cap length at ½ the shorter neighbour); (6) a
  **caption lane** of independently draggable cues fed by ASR; (7) **timeline zoom**.
- **Reuse CapCut's exact vocabulary** — 主轨道 / 画中画(Overlay) / 字幕 — Chinese
  short-video creators already have the muscle memory; don't invent terms.
- **Make transitions a seam interaction, not free drag**, and enforce the ½-clip cap
  as a validity rule so the compiler never sees an impossible transition.

---

## 2. Storyboard table workspaces + Frame.io approval model

Studied: LTX Studio, Filmustage, StudioBinder, Boords, Katalist; Frame.io V4.

**Pattern 2a — LTX Studio: script → scenes → shots, with reusable "Elements".** LTX
auto-extracts **characters, objects, and locations as Elements** you tag and reuse
"to ensure consistency across every shot"
(https://ltx.io/studio/platform/ai-storyboard-generator). Its shot-list columns:
**Scene & Shot Number, Shot Description, Shot Type/Size, Camera Angle**, optional
**equipment/movement/notes**; storyboard panels carry **shot number, scene
description, dialogue, technical notes** (https://ltx.io/blog/shot-list-template).

**Pattern 2b — Filmustage frame card + StudioBinder columns (industry labels).**
Filmustage's storyboard frame card exposes "**Camera, shot size, lens, lighting,
frame rate, VFX — all in one frame card**," auto-populated with **location,
characters, actions** (https://filmustage.com/storyboards-and-shot-lists/ — verified
by direct fetch). StudioBinder's exact, toggleable shot-list columns: **Scene #,
Shot #, Subject, Description, Camera, Shot Size, Shot Type, Movement, Equipment,
Lens, Special Equipment**, plus color-coding, image upload, notes, print checkboxes
(https://www.studiobinder.com/blog/shot-lists-complete-customization-intuitive-interface/
— verified). Boords fields (customizable): **Label, Action, Lens, Shot Type**
(https://boords.com/shot-list-template).

**Pattern 2c — Frame.io review/approval status model (the key ask).** Exact per-clip
status names in V4: **Approved, In Progress, Needs Review**. "If Approvals are
enabled in Link Settings, reviewers will have the ability to mark a clip as Approved,
In Progress, or Needs Review"
(https://help.frame.io/en/articles/414306-sharing-your-files-and-folders-for-review-legacy
— verified). The status control sits **top-right of the open asset**
(https://support.frame.io/en/articles/1161479-review-links-explained-for-clients-legacy).
**Who flips them:** reviewers on a share link — but only when the link/account owner
enables the **Approvals** toggle; a change fires a **"Media Status Updated"**
notification (https://support.frame.io/en/articles/16799-project-settings). The
Adobe Workfront ↔ Frame.io integration extends the vocabulary for approvers to
**Approve / Approved with changes / Needs work**
(https://experienceleague.adobe.com/en/docs/workfront/using/review-and-approve-work/document-reviews-and-approvals/review-and-approve-documents/review-with-frame).

### 打法建议 (item 2) — proposed storyboard shot-table columns for the wave-2 GUI
Borrow the two strongest citable primitives — LTX **Elements** + Frame.io's
three-state **Approved / In Progress / Needs Review**:

| Manju column | Maps to | Borrowed label / source |
|---|---|---|
| **Shot #** | shot id | "Shot #" (StudioBinder), "Scene & Shot Number" (LTX) |
| **场景 Scene** | scene | "Scene #" (StudioBinder) |
| **角色 Character(s)** | character | LTX/Filmustage/Katalist "characters" — bind as reusable tagged **Elements** |
| **动作/描述 Action** | action | "Action" (Boords), "Description" (StudioBinder/LTX) |
| **台词 Dialogue** | dialogue | "dialogue" (LTX panel field) |
| **镜头 Shot size/camera** | camera hint | "Shot Size / Shot Type / Movement / Lens" (StudioBinder); Filmustage frame card |
| **来源 Provider** | provider | per-shot model selector (Filmustage "AI Models" precedent) |
| **状态 Take-state** | take-state | generation freshness chip (see item 3) |
| **锁 Lock** | lock | Manju-specific (StudioBinder color-code/checkbox as lightweight precedent) |
| **审批 Approval** | approval | **Needs Review / In Progress / Approved** (adopt Frame.io's three literal words) |

Keep freshness (take-state) and approval as **two orthogonal chips** — Frame.io keeps
version-freshness separate from approval, and so should Manju.

---

## 3. Export / deliverable status centers

Studied: Frame.io version stacks, Adobe Media Encoder / Premiere render queue,
DaVinci Resolve Deliver, GitHub Actions, Nx.

**Pattern 3a — Adobe Media Encoder status enum (authoritative literal list).** The
AME scripting API enumerates job status: **`0 Waiting, 1 Done, 2 Failed, 3 Skipped,
4 Encoding, 5 Paused, 6 Stopped, 7 Any, 8 AutoStart, 9 Done Warning, 10 Watch Folder
Waiting`** (https://ame-scripting.docsforadobe.dev/reference/index.html — verified).
The UI Status column shows **Ready / Done / Failed / Stopped / Skip** with a
completed-vs-total job tracker
(https://helpx.adobe.com/media-encoder/using/encode-export-video-audio.html —
verified). Note **"Done Warning"** — a distinct "finished but check it" state, the
exact analog of Manju's *needs-manual-check*.

**Pattern 3b — GitHub Actions two-field split (state vs outcome).** `status`:
`queued, in_progress, completed, waiting, requested, pending`. `conclusion`:
`success, failure, neutral, cancelled, skipped, timed_out, action_required,`
**`stale`** (https://docs.github.com/en/rest/actions/workflow-runs — verified). Two
first-class words to steal: **`stale`** (a real conclusion) and **`action_required`**
(≈ needs-manual-check). The clean model is separating the *run state* from the
*result*.

**Pattern 3c — Resolve/Nx freshness = rendered-vs-unrendered / cache-hit.** Resolve's
Deliver queue lets you **"Clear Render Status"** to make a job "appear unrendered
again" — an explicit fresh/stale toggle
(https://www.steakunderwater.com/VFXPedia/__man/Resolve18-6/DaVinciResolve18_Manual_files/part3957.htm),
and warns on **offline (missing source) material**. Nx prints **"existing outputs
match the cache, left as is"** for a fresh target
(https://nx.dev/docs/features/cache-task-results). Core idea for Manju: a deliverable
is **up-to-date** iff its input hash matches the cached hash; otherwise **stale**.

**Pattern 3d — Frame.io version stacks: newest = current.** Versions stack rather
than proliferate as files; "the newest version will be ready to view," older reached
by clicking the version number (V1/V2…)
(https://help.frame.io/en/articles/9101068-version-stacking). (Explicit "current
version" text label is **unverified** — the mechanism is "newest = default".)

### 打法建议 (item 3) — export-center status vocabulary for Manju deliverables
Adopt GitHub's **two-axis** model (state vs outcome), per deliverable
(**final / draft / captions / cover / teaser**):

- **Freshness axis** (input-hash driven): **上新/Up-to-date** ("outputs match the
  cache", Nx / `success`) · **待更新/Stale** (GitHub literal `stale`; Resolve "Clear
  Render Status") · **缺失/Missing** (never generated; Resolve offline / AME "Ready")
  · **待人工确认/Needs-manual-check** (AME literal **"Done Warning"**; GitHub
  `action_required`; Frame.io "Needs Review").
- **Job axis** (at export time): **Ready/Queued** · **Rendering** (AME "Encoding") ·
  **Done** · **Failed** · **Paused/Stopped**.
- **Version badge** `v1/v2…` (Frame.io) + **approval chip** (Needs Review / In
  Progress / Approved) sit on top, orthogonal to both.

Manju already computes every one of these numbers (content keys, spec snapshots,
staleness diff, key.json sidecars) — this is pure vocabulary/presentation. Wire the
existing `manju compare` / staleness signals to these exact words.

---

## 4. Asset/character consistency — reference-image limits (EXACT, cited)

Full detail behind the headline table. Distinction observed throughout: **product
feature claims** vs **documented API parameters**.

- **MiniMax / Hailuo (S2V-01):** `subject_reference` = array of
  `{type:"character", image:[…]}`; **"only a single subject reference (human face) is
  supported"** — effectively **1** reference image
  (https://platform.minimax.io/docs/guides/video-generation;
  https://fal.ai/models/fal-ai/minimax/image-01/subject-reference/api). Clean-image
  guidance is **not in the official API doc**; best secondary (Segmind): "clear front
  view of the face without shadows or angles," "simple background," "≥512×512px,"
  "good lighting" (https://blog.segmind.com/minimax-subject-reference-everything-you-need-to-know/
  — **unverified** against MiniMax primary).
- **Runway Gen-4 References:** `referenceImages` array, **1–3**, each `{uri, tag}`
  referenced via `@tag` in `promptText`. "The model supports up to three reference
  images… max 720×720 for 1:1, 1280×720 for 16:9"; label them `image_1/2/3`
  (https://help.scenario.com/articles/6803483730-runway-gen-4-references — quoting
  Runway; https://docs.dev.runwayml.com/guides/using-the-api/). Inputs: JPEG/PNG/WebP
  (no GIF); URL ≤16MB / data-URI ≤5MB; ideally 640×640 to 4K
  (https://docs.dev.runwayml.com/assets/inputs).
- **Kling multi-image / Elements / 多图参考:** `image_list` with **minItems 2,
  maxItems 4**; Elements uses `input_image_urls`, "2–4 image URLs (JPG/PNG, max 10MB
  each)," referenced via `@element_name`
  (https://docs.aimlapi.com/api-references/video-models/kling-ai/v1.6-standard-multi-image-to-video;
  https://fal.ai/models/fal-ai/kling-video/v1.6/standard/elements/api). Kuaishou's
  own launch: upload "one or more images of the same subject… up to 4"
  (https://ir.kuaishou.com/news-releases/news-release-details/kuaishou-kling-ai-unveils-multi-image-reference-feature-further/).
  Independently confirmed: multi-image up to 4 for the subject, up to 7 elements when
  no video is supplied (search cross-check, 2026-07). The API reference specifies only
  format/aspect/size — **no official "remove background / single person" rule
  (unverified in API docs)**.
- **Vidu (reference-to-video):** `images` array, **1–7** (viduq1/q2/q3/2.0/mix);
  viduq2-pro = 1–7 without a video, **1–4 with a video**. PNG/JPEG/JPG/WebP; **min
  128×128px**; **aspect < 1:4 or 4:1**; ≤50MB/image; POST body ≤20MB
  (https://platform.vidu.com/docs/reference-to-video — verified by direct fetch). The
  documented multi-subject leader.
- **PixVerse Fusion (reference-to-video):** `image_references` array of
  `{img_id, type:"subject"|"background", ref_name}` (ref via `@ref_name`); **1–3 for
  v4.5/v5, up to 7 for v5.5/v5.6/v6/c1** — version-gated
  (https://docs.platform.pixverse.ai/fusionreference-to-video-generation-19884194e0).
  Upload: png/webp/jpeg/jpg, max dim 10000px, <20MB
  (https://docs.platform.pixverse.ai/upload-image-13016631e0). Best-practice note:
  "Use a high-resolution image with clear subjects and balanced lighting. Avoid heavy
  compression and cluttered backgrounds…"
  (https://docs.magnific.com/api-reference/image-to-video/pixverse-v6/overview).
- **Pika:** `ingredients` (Pikascenes multi-subject) — array, `.png/.jpeg/.webp`,
  **no documented max**; `pikaframes` — "at least 2 frames," no documented upper
  bound (secondary claims "up to 5") (https://www.pikapikapika.io/docs/web). Weakest-
  documented of the set — treat any "5" as **unverified**.

### 打法建议 (item 4)
- **Default cap = 4 clean reference images per character**, with an optional "extended
  set" up to **7** for shots routed to Vidu / PixVerse-v6. 4 satisfies Kling exactly,
  over-collects for Runway (send its best 3), and is a healthy subset of the 7-image
  providers. **Never expose more than 7** — no surveyed provider documents more.
- **Trim per-provider at send time**, ranked by resolver confidence: ≤3 → Runway,
  ≤4 → Kling, ≤7 → Vidu/PixVerse-v6, and **exactly 1 (best front-facing portrait) →
  MiniMax S2V**. Preserve a stable ranked order (Kling Elements is order-sensitive;
  Runway/PixVerse want per-image tags/`ref_name`).
- **"Clean reference image" checklist to surface in the UI** (each item cited):
  (1) one subject per image; (2) simple/uncluttered background (PixVerse "avoid
  cluttered backgrounds"; MiniMax "simple background"); (3) front-facing, even
  lighting, no harsh shadows; (4) high-res, low compression (≥640×640, target ≥720p);
  (5) moderate aspect (Kling 1:2.5–2.5:1; Vidu/PixVerse not beyond 1:4/4:1);
  (6) JPG/PNG/WebP, ≤10MB (Kling's cap is strictest). Flag that Kling/MiniMax publish
  these only as best-practice, not hard API rules.

---

## 5. First/last-frame video generation — exact API fields

- **Kling:** `image` (first/start), `image_tail` (tail/last, **optional**; at least
  one of the two required). URL or base64 (base64 **without** a `data:` prefix on the
  native API); `image_tail` **mutually exclusive** with `dynamic_masks`/`static_mask`
  and `camera_control`. Image constraints: JPG/JPEG/PNG, **≤10MB, min 300px/side,
  aspect 1:2.5–2.5:1** (https://kling.ai/document-api/apiReference/model/imageToVideo;
  https://www.segmind.com/models/kling-image2video/api — field names verified via
  mirror; kling.ai official page is a JS SPA WebFetch can't render, so exclusivity is
  search-surfaced, high-confidence). **Rename trap:** fal.ai / AI-ML API expose
  `image_url` / `tail_image_url`; KIE's Kling 2.6 wrapper uses a single `image_urls`
  and **drops the tail field**
  (https://fal.ai/models/fal-ai/kling-video/v1/standard/image-to-video/api;
  https://docs.kie.ai/market/kling/image-to-video).
- **Runway:** `promptImage` accepts an **array of `{uri, position}`** with
  `position: "first" | "last"`; each `position` must be unique. "If you set position
  to `last`, the generated video will end with the image instead of starting with it."
  `uri` = HTTPS URL, data URI, or `runway://`
  (https://docs.dev.runwayml.com/api-details/versions/2024-11-06/ — verified).
- **Vidu (start-end to video):** `images` = array of **exactly 2** strings, `[0]`
  start / `[1]` end, both required; **first/last aspect ratio must be 0.8–1.25** (near
  equal). PNG/JPEG/JPG/WebP, URL or base64, ≤50MB/image
  (https://platform.vidu.com/docs/start-end-to-video — verified).
- **PixVerse (transition / first-last):** `first_frame_img` + `last_frame_img`, both
  **required integer `img_id`s** returned by the Upload-Image endpoint — **not URL or
  base64** (upload first, then reference the id). `POST …/openapi/v2/video/transition/
  generate` (https://docs.platform.pixverse.ai/transitionfirst-last-frame-generation-15123014e0).
  A separate multi-keyframe mode supports **2–7 keyframes**.
- **Pika (Pikaframes):** first + last still images; up to **5 keyframes** A→B→C→D→E
  (secondary — https://www.pikapikapika.io/docs/web; count **unverified** against
  primary).

### 打法建议 (item 5)
- **Store keyframes internally as `{role:"first"|"last", asset:<url|base64|handle>}`
  — role, not array index** (only Vidu is positional). Translate to each vendor's
  representation at compile time:

  | Manju internal | Kling | Runway | Vidu | PixVerse |
  |---|---|---|---|---|
  | first | `image` | `promptImage[{…,position:"first"}]` | `images[0]` | `first_frame_img` (int id) |
  | last | `image_tail` | `promptImage[{…,position:"last"}]` | `images[1]` | `last_frame_img` (int id) |

- **Gate the UI by vendor capability:** Kling/Runway allow first-only or last-only;
  Vidu and PixVerse-transition require **both**.
- **Insert a PixVerse pre-upload step** (upload → integer `img_id`) since it accepts
  neither URL nor base64 inline; **remap Kling field names per route** (native
  `image`/`image_tail` vs fal/AI-ML `image_url`/`tail_image_url`), and warn that KIE's
  2.6 wrapper has no tail slot.
- **Enforce the strictest common frame constraint** so a shot stays portable: matched
  aspect for first/last (Vidu's 0.8–1.25 is binding), ≥300px/side, ≤10MB, JPG/PNG.

---

## 6. Prompt-engineering guidance for video models

- **Kling** (official T2V guide): prompt formula **"Subject (description) + Subject
  Movement + Scene (description) + (Camera language + Lighting + Atmosphere)"** —
  主体+运动+场景+(镜头+光影+氛围). Motion should be "straightforward and suitable for a
  5-second video"; "keep the visual content as simple as possible, aiming for
  completion within 5 to 10 seconds"; "simple words and sentence structures"; models
  "are not sensitive to numbers"
  (https://kling.ai/quickstart/text-to-video-prompt-guide).
- **Runway** (Gen-4 prompting guide): "Gen-4 thrives on prompt simplicity"; "keep one
  primary motion and one secondary motion" — the cat→dragon example ("transforms into
  a dragon while jumping through a forest that changes seasons… camera spins 360°…")
  should reduce to "a cat transforms into a dragon while running through a forest."
  In image-to-video, let the image set the scene and use text to **describe what
  moves**. **Avoid negatives** ("the camera doesn't move" → state the move directly)
  (https://help.runwayml.com/hc/en-us/articles/39789879462419-Gen-4-Video-Prompting-Guide
  — help.runwayml.com 403s to the fetcher; rules are search-surfaced from the official
  article, paraphrase-accurate).
- **OpenAI Sora 2** (official cookbook guide): "**Each shot should have one clear
  camera move and one clear subject action.**" Actions "described in beats or counts…
  so they feel grounded in time" ("takes four steps to the window, pauses, and pulls
  the curtain in the final second"). "The model generally follows instructions more
  reliably in shorter clips" — prefer two 4s clips over one 8s. Split complex
  sequences into distinct shot blocks, "each with one camera setup, one subject
  action, and one lighting recipe"
  (https://developers.openai.com/cookbook/examples/sora/sora2_prompting_guide —
  verified by fetch).
- **Pika:** "JUST a subject and the description of an action"; no instructional
  prompts ("make it move"); `-motion` 0–4 (default 1); `-camera` = one of
  zoom/pan/rotate (not stackable) (https://pikalabs.org/perfecting-pika-labs-prompting/
  — community site, **unverified as official**).

### 打法建议 (item 6) — rules for Manju's prompt-compiler advisories
1. **One primary action + at most one secondary per shot** (Runway "one primary/one
   secondary motion"; Sora "one camera move + one subject action"). Bake as a linter.
2. **Motion in beats/counts, timed to the clip** ("jogs three steps and stops" not
   "moves quickly"); budget it to ~5s / the chosen duration (Sora + Kling).
3. **Compile structure Subject + Movement + Scene (+ camera/lighting/mood)** (Kling
   formula); in keyframe mode, let the frames define the scene, text only the motion.
4. **Concise, no keyword-stuffing, no negative phrasing** — state camera moves
   positively (Runway + Kling).
5. **Split-shot auto-advisory:** if the prompt has >1 major action, a location/season
   change, OR a camera-transform + subject-transform, recommend splitting (Runway
   cat→dragon; Sora "distinct shot blocks").
6. **Prefer shorter clips** for instruction-following; nudge 2× short over 1× long
   (Sora + Kling "within 5–10s").

---

## 7. Quality / speed modes (draft vs final)

- **Runway** encodes the fast/quality axis in the **model name**, not a "draft"
  toggle: `Gen-4 Turbo` (5 API credits/sec) vs `Gen-4.5` (12 API credits/sec); Gen-4
  Image `Turbo` = 2 credits/image (https://docs.dev.runwayml.com/guides/pricing/ —
  verified; consumer plan credits differ). Official framing: Gen-3 Alpha Turbo "7x
  faster for half the price… still matching performance across many use cases,"
  image-to-video only (https://x.com/runwayml/status/1824070782768529629 — official
  account).
- **Kling — Standard vs Professional (Pro)**: Standard = faster/cheaper/lower quality,
  Pro = "sharper textures, smoother motion, better coherence" at ~3.5× credits;
  explicit guidance "Use Standard for testing and Professional for final output"
  (https://magichour.ai/blog/kling-ai-pricing — secondary).
- **Pika — Turbo:** "the model to use when you feel the need for speed… up to 3x
  faster and 7x cheaper – all with high quality outputs" (https://pika.art/faq —
  primary). **Sora — `sora-2` vs `sora-2-pro`:** base is "designed for speed… ideal
  for the exploration phase," pro is "higher quality… best for high-resolution
  cinematic footage"
  (https://developers.openai.com/api/docs/guides/video-generation — primary).
- **NLE playback vs export separation:** Resolve **Proxy Media** (½/¼/⅛/1/16),
  **Optimized Media**, **Timeline Proxy Mode (Off/Half/Quarter)**, **Draft Quality**
  — all lower *editing viewport* quality and "will not affect the final product"
  (https://videowithjens.com/davinci-resolve-proxy-optimized-media-get-smooth-playback/
  — secondary). Premiere: proxies for editing, **export default ignores proxies**;
  an explicit **"Use Proxies" checkbox** opts into fast delivery
  (https://helpx.adobe.com/premiere/desktop/organize-media/ingest-proxy-workflow/ingest-and-proxy-workflow.html
  — primary).
- **ComfyUI queue:** FIFO **Queue Prompt** (Ctrl+Enter, appends) vs **Queue Front**
  (Ctrl+Shift+Enter, priority jump) vs **Batch** (fan-out N); "the queue captures
  state at queue time" (params frozen at enqueue)
  (https://comfyui-wiki.com/en/interface/basic — community-official).

### 打法建议 (item 7)
- **Ship a single two-position toggle: `草稿 Draft` / `成片 Final`**, mapping under the
  hood to a **turbo route vs quality route** (mirrors Runway/Pika `Turbo`, Sora
  base/`-pro`, Kling Standard/Pro). Every surveyed tool ships a binary — don't invent
  a third label.
- **Decouple preview from export** (Resolve/Premiere): a Timeline-Proxy-style playback
  control (Off/Half/Quarter) that never touches final render, plus a "use proxies on
  export" opt-in. Manju already has proxy renders — surface this as the preview axis.
- **Show the tradeoff numerically at the toggle** via the existing dry-run gate:
  "Draft ≈ ½ credits, ~3× faster" next to "Final = full quality," echoing Kling's
  "Standard for testing, Pro for final."
- **Add `Queue Front` priority + `Batch ×N`** to the render queue, FIFO default,
  params frozen at enqueue (ComfyUI).

---

## 8. Beginner / pro mode splits

- **DaVinci Resolve Cut vs Edit page** — the split is **two top-level pages**, not a
  settings toggle, over one shared project. Cut page philosophy = strip the systematic
  machinery: **dual timeline** ("upper shows the entire program… lower a zoomed-in
  area… you never have to zoom again" — you *can't* control zoom), **Source Tape** (the
  whole bin as one scrubbable strip), and opinionated one-click verbs (**Smart Insert,
  Append at End, Place on Top, Ripple Overwrite, Close Up, Boring Detector**). Cut
  **subtracts capability** (manual zoom, arbitrary insert points, deep track/mixer
  management); Edit restores the full V1/V2/A1 timeline
  (https://www.blackmagicdesign.com/products/davinciresolve/cut — primary).
- **CapCut mobile vs desktop** — split by **device/edition**, capability *subtraction*
  on the smaller surface: mobile = "trimming, sliding, dropping clips with your
  fingers… fast and natural," AI tools often ship mobile-first; desktop = "the full
  package: exact alignment, markers, track levels, complex timelines," batch ops,
  advanced color/keyframe curves. **Cloud sync lets a project start on mobile and
  finish on desktop** (https://www.capcut.com/resource/capcut-ipad-vs-desktop —
  primary).
- **Blender workspaces** — a mode is a **saved full-screen layout preset behind a top
  tab**: "Workspaces are essentially predefined window layouts… click the tabs to
  switch." They carry **no hidden capabilities — only which panels are visible**;
  saved in the blend-file (https://docs.blender.org/manual/en/latest/interface/window_system/workspaces.html
  — manual verbatim via domain-restricted search; docs.blender.org WAF-blocks the
  fetcher). So a mode can either *subtract capability* (Resolve) or *only re-arrange*
  (Blender) — decide deliberately.

### 打法建议 (item 8)
- **Implement 新手 / 专业 as a top-level view switch (Resolve page-tab model), not a
  buried checkbox** — two named surfaces over one shared project, switchable anytime
  with **no data loss**. Matches how Chinese users already read 剪映 vs 剪映专业版.
- **Make 新手 mostly Blender-style "same power, fewer panels"** (hide the transition-
  duration panel, keyframe editor, extra overlay lanes — re-arrange, don't cripple),
  and Resolve-Cut-style "opinionated automation" only for genuinely hard tasks (auto-
  caption, smart trim, the **"never zoom" dual timeline** as the beginner default).
- **Preserve fidelity across the toggle** (CapCut cloud-sync lesson): switching
  新手→专业 must hide, never delete, clips/keyframes/transitions the other mode can't
  show, so users graduate without redoing work.

---

## 9. AI director / copilot loops (propose → cost → confirm → execute → show → next)

Scored against six steps: (a) propose plan, (b) show cost/impact, (c) explicit
approve-before, (d) execute, (e) show result/diff, (f) suggest next.

- **Cursor / Claude Code** nail (a),(c),(e): Cursor **Plan Mode** writes an editable
  Markdown plan "with file paths and code references," you edit it, then "build the
  plan"; changes return as a **color-coded add/delete diff** you selectively apply
  (https://cursor.com/docs/agent/plan-mode; https://cursor.com/docs/agent/review).
  Claude Code **Plan mode**: "file edits are never auto-approved in plan mode, even
  when an allow rule matches" — the strict approve-before boundary via `canUseTool`
  (https://code.claude.com/docs/en/agent-sdk/permissions). Neither shows cost.
- **Runway Chat Mode** — conversational partner that "brainstorms ideas, refines
  prompts, provides feedback on your shots," then generates; implements (a),(d),(e),(f
  conversational); an explicit pre-generation **cost estimate in chat is unverified**
  (https://help.runwayml.com/hc/en-us/articles/42290974553875-Gen-4-Chat-Mode-FAQs —
  403 on fetch).
- **LTX Studio** — strongest on (a)+(b-as-scope): "automating the structure,
  extracting Elements, and giving you control over models **before you generate a
  single frame**… a complete view before generation"
  (https://ltx.studio/blog/ltx-storyboard-generator-update).
- **Descript Underlord** — strongest on (a)+(f): "reads your script, watches your
  video, decides what to do next… makes suggestions and takes feedback"; "notices
  poor eye contact or bad audio and offers fixes"; user "retains final creative
  control" — confirm is **review-after**, not approve-before
  (https://www.descript.com/underlord).

**The gap:** coding tools nail approve-before + diff but skip cost; generative video
tools nail plan + scope preview + proactive-next but skip a hard approve-before-spend
boundary. **No surveyed product implements all six as a formal contract** — Manju's
opportunity.

### 打法建议 (item 9) — the 6-step contract for Manju's MCP/CLI director loop
1. **PROPOSE** — an editable plan artifact (shot/edit list, "complete view before
   generation" — LTX; inline-editable like Cursor's plan Markdown).
2. **COST/IMPACT** — run the existing **dry-run cost gate here**: credits + est.
   wall-clock + affected shots per action, Draft-vs-Final side by side. *This is the
   step every competitor underplays — Manju's differentiator.*
3. **CONFIRM** — enforce an **approve-before-execute boundary** on all paid/write
   actions (Claude Code `canUseTool` / plan mode: never auto-approve a paid render).
   Offer an Accept-Edits-style power-user mode, but never auto-run spend.
4. **EXECUTE** — dispatch via routing to the chosen route, FIFO with optional
   `Queue Front` priority.
5. **SHOW RESULT/DIFF** — before/after timeline diff (Cursor color-coded) + proxy
   preview; accept/reject which changes land.
6. **SUGGEST NEXT** — proactively surface the next best action (Descript Underlord:
   "audio is quiet on shot 3 — normalize?"), looping back to step 1.

Manju already has the pieces (proposals channel, dry-run gate, ask_before engine
gate, spec-snapshot diffs, proxy previews) — this is sequencing them into one named
contract. **Ship the cost step + the strict approve-before boundary** as the two
moves no creative tool completes.

---

## 10. Plain-language glossary — 剪映 wording + proposed Manju 中文用户词

**剪映 actual on-screen wording (confirmed):** 轨道 / 主轨道(主轨) / 素材 / 素材库 /
草稿 / 草稿箱 / 导出 / 字幕 · 文本 / 识别字幕 · 智能字幕 / 转场 / 滤镜 / 特效 / 贴纸 /
蒙版 / 关键帧 / 变速 / 画中画(Overlay) / 音频 / 提取音乐 / 一键成片 · 剪同款 / 模板 /
封面 — CapCut EN equivalents: Track / Main track / Material·Media / Draft·Project /
Export / Captions·Text / Auto captions / Transitions / Filters / Effects / Stickers /
Mask / Keyframe / Speed / Overlay(PiP) / Audio / Extract audio / Auto Cut·Smart edit /
Templates / Cover
(https://www.capcut.com/resource/how-to-add-capcut-overlays;
https://www.capcut.com/help/auto-captions-in-capcut;
https://zhuanlan.zhihu.com/p/417182761; https://zhuanlan.zhihu.com/p/640368221 —
剪映 terms partly from Chinese tutorials, **directionally verified**). Note 剪映 files
"字幕" and "文本" under one bottom **文本** entry: manual typing = 新建文本, speech-to-
caption = 智能字幕/识别字幕.

**Proposed Manju engineering-term → 中文用户词 → tooltip** (the key deliverable —
naming principle: 剪映-style plain, short, 望文生义; hover shows the English original
for pro users):

| Engineering term | 中文用户词 | 一句话说明 (tooltip) |
|---|---|---|
| provider | **生成来源** | 这条画面/配音是用哪个 AI 模型或服务做出来的。 |
| take | **版本 / 这一条** | 同一个镜头反复生成的不同版本，可并排对比、挑一条留用。 |
| stale | **待更新 / 需重做** | 上游改过之后这一条还是旧的，得重新生成才跟得上。 |
| fallback | **备用方案 / 兜底** | 首选来源失败时自动改用的替代方案，保证片子出得来。 |
| routing | **智能派单 / 线路选择** | 系统自动决定每条任务交给哪个模型来做，你不用手动指定。 |
| ledger | **制作台账 / 制作记录** | 完整记下每一步生成了什么、花了多少，随时可回查。 |
| sidecar | **配套信息** | 跟素材一起存的说明文件（参数、来源、时间），不占画面。 |
| manifest | **成片清单 / 配方单** | 记录整片由哪些镜头、素材、参数拼成，照它能一模一样再做一遍。 |
| QC | **质量检查 / 质检** | 自动帮你查画面、字幕、音量有没有明显问题。 |
| repair plan | **修复方案** | 针对质检查出的问题，系统给出的一键修补步骤。 |
| lock | **锁定** | 锁住这一条，避免被重新生成或不小心改动。 |
| snapshot | **存档点** | 把当前状态存下来，随时可以回到这一刻。 |
| rollback | **还原 / 回到上一版** | 放弃这次改动，退回之前的存档点。 |
| proxy | **预览版 / 低清代理** | 用小体积低清文件流畅预览，导出时自动换回高清。 |
| render | **合成导出 / 渲染** | 把时间线上所有内容合成为最终成片。 |
| timeline | **时间线** | 按时间先后排素材的编辑区（沿用剪映同名词）。 |
| shot | **镜头** | 一段连续画面，分镜表里的一格。 |
| storyboard | **分镜 / 分镜脚本** | 把整片拆成一个个镜头的计划表，先定好再生成。 |
| budget breaker | **花费护栏 / 预算上限** | 花费到上限就自动暂停，避免不知不觉超支。 |
| dry-run | **试跑 / 预演** | 只算不真正生成，先看计划和预估花费再决定要不要开工。 |

### 打法建议 (item 10)
- **Adopt this table as the product-copy single source of truth.** Surface each term
  as a **`?` tooltip at its first appearance** in the GUI (the third column is the
  tooltip text verbatim).
- **Add a "显示专业术语" toggle** — when on, grey the English original (take /
  provider / stale) beside the Chinese word, so beginners see 版本/生成来源/待更新
  while pros can map to docs.
- **Standardize on the four base words 镜头 / 分镜 / 时间线 / 版本** (all aligned to
  剪映 or film common-sense); hang every other engineering concept off them.

---

## 11. OSS reference patterns Manju can still learn from

(Extends [COMPETITIVE-STUDY.md](COMPETITIVE-STUDY.md), which covered the features
layer; here, the workflow/UI patterns for round-U's gaps.)

- **ViMax (HKUDS/ViMax)** — the consistency loop worth copying: "Generate multiple
  images in parallel and select the best consistent image as the first frame through
  MLLM/VLM," and "intelligently select the reference image required for the first
  frame of the current video, **including the storyboards that occurred in the
  previous timeline**." Plus a shot-level storyboard system "through cinematography
  language" and an Agents Loop TUI (director/writer/producer agents)
  (https://github.com/HKUDS/ViMax/blob/main/readme.md). **Learn:** make character
  consistency a visible "generate N → VLM picks the most-alike → save as this shot's
  reference, reused across shots" loop — directly feeds items 4 and 9.
- **ComfyUI (comfyanonymous/ComfyUI)** — models first/last-frame explicitly via
  `WanFirstLastFrameToVideo` (two keyframe inputs → interpolated motion), and expresses
  "quality mode" as a set of *explicit params* (resolution/frames/fps/interpolation)
  rather than a black-box switch
  (https://comfy.org/workflows/video_wan2_2_14B_flf2v-7016f027bcf1/). **Learn:** give
  Pro mode lockable first/last keyframe slots (item 5) and make the Draft/Final tier a
  visible parameter set (item 7).
- **MoneyPrinterTurbo (harry0703/MoneyPrinterTurbo)** — the beginner WebUI compresses
  choices to a visual five: 主题→文案→画幅(9:16/16:9)→素材源(Pexels/Pixabay/本地)→
  字幕/配音, with **real-time voice 试听** and **batch-generate-N-then-pick-one**
  (https://github.com/harry0703/MoneyPrinterTurbo/blob/main/README-en.md). **Learn:**
  a 新手 five-step flow (item 8) and "generate several takes, keep one" as the default —
  which is exactly Manju's take model.
- **ShortGPT (RayVentura/ShortGPT)** — an Editing Markup Language + JSON EditingEngine:
  edits as an LLM-readable, human-editable structured intermediate
  (https://github.com/RayVentura/ShortGPT/blob/stable/README.md). **Learn:** keep the
  shot/edit plan a structured layer both the director agent and the user can read/write
  — Manju's manifest/成片清单 already is this; keep the loop (item 9) reading/writing it.

### 打法建议 (item 11)
1. **Adopt ViMax's VLM consistency loop** on the `qc_vision` slot: N candidates → VLM
   picks the most-consistent → save as the shot's reference, reusable across shots
   (also the biggest open engineering gap named in MARKET-GAP.md).
2. **Adopt MoneyPrinterTurbo's five-step 新手 flow + generate-N-pick-one** as the
   beginner default — it maps 1:1 onto Manju's takes.
3. **Adopt ComfyUI's explicit-parameter quality tier + lockable first/last slots** for
   Pro mode instead of an opaque toggle.

---

## Consolidated wave-2 GUI deliverables (the four the task asked for)

1. **Storyboard shot-table columns** — item 2 打法建议 table: Shot# · 场景 · 角色
   (Elements) · 动作/描述 · 台词 · 镜头 · 来源(provider) · 状态(take-state) · 锁 ·
   审批(**Needs Review/In Progress/Approved**).
2. **Export-center status vocabulary** — item 3 打法建议: freshness **上新/待更新/
   缺失/待人工确认** (Up-to-date / Stale / Missing / Needs-manual-check, borrowing AME
   "Done Warning" + GitHub `stale`/`action_required`) × job **Ready/Rendering/Done/
   Failed** × version badge `v1/v2…` × approval chip.
3. **Shot-lab layout** — Pro-mode reference workbench: up-to-4 (extended-7) **ranked,
   tagged clean reference images per character** with the 6-point clean-image checklist
   (item 4); **lockable first/last keyframe slots** with role-tagged, per-vendor field
   mapping (item 5); a **Draft/Final quality toggle** showing the dry-run cost delta
   (item 7); a **prompt box with the 6 compiler advisories** incl. the split-shot
   trigger (item 6).
4. **Glossary table** — item 10, wired as first-appearance tooltips + a 显示专业术语
   toggle.

---

## Sources & verification notes

**Verified by direct primary fetch:** Vidu reference-to-video & start-end docs
(platform.vidu.com); MiniMax video-generation guide (platform.minimax.io); Runway API
version doc & pricing (docs.dev.runwayml.com); PixVerse Fusion/transition/upload
(docs.platform.pixverse.ai); Sora 2 prompting guide (developers.openai.com); Adobe
Media Encoder status enum (ame-scripting.docsforadobe.dev) & Premiere proxy
(helpx.adobe.com); GitHub Actions status/conclusion (docs.github.com); Nx cache
(nx.dev); Frame.io statuses/version-stacking/roles (help.frame.io); Resolve Cut page
(blackmagicdesign.com); Filmustage & StudioBinder columns; CapCut auto-captions &
overlays (capcut.com); ViMax/MoneyPrinterTurbo/ShortGPT/ComfyUI READMEs (github.com).

**Kling** field names (`image`/`image_tail`, multi-image `image_list` 2–4) verified
via aggregator mirrors of the official schema (segmind.com, docs.aimlapi.com, fal.ai)
+ image-constraint cross-check (≤10MB / 300px / 1:2.5–2.5:1) — the kling.ai official
API page is a JS SPA WebFetch cannot render, so `image_tail` mutual-exclusivity and
the "first↔tail must match aspect" rule are **search-surfaced (high-confidence, not
first-party-rendered)**.

**Search-surfaced (help.runwayml.com / help.descript.com 403 the fetcher):** Runway
Gen-4/Gen-3 prompting-guide wording; Runway Chat Mode; Descript Underlord article —
paraphrase-accurate, exact quotes reconstructed.

**Marked unverified:** Pika reference/keyframe counts (~5) — no primary cap; Kling &
MiniMax "remove background / single person / lighting" as *hard API rules* (they are
best-practice, not in the API reference); Resolve exact per-job label strings; Frame.io
explicit "current version" text label; 剪映 in-app term wording partly from Chinese
tutorials rather than a first-party manual page; Blender default-workspace list
(WAF-blocked, manual's own strings via search); Runway Chat Mode pre-generation cost
estimate; several secondary-only credit-split figures (Gen-3 std 10 vs Turbo 5;
"80–90% quality"; ComfyUI API `front` param).
