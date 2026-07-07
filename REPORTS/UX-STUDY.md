# UX-STUDY.md — what mature products teach the manju workbench

> **Superseded for planning purposes** by
> [COMPETITIVE-UX-STUDY.md](COMPETITIVE-UX-STUDY.md) — the complete 8-product
> run (Figma and Obsidian legs recovered, machine synthesis with repo-verified
> landing spots). This file remains the per-product raw findings for the six
> studies completed first plus the session lead's interim synthesis; rounds
> R10–R12 shipped from its shortlist.

> Method: six parallel research agents studied one product family each
> (live docs/changelogs/help centers, 2026-07-05), each returning patterns
> scored for fit against manju's doctrine (§0 intelligence outside, §1-② git
> is the patch engine, §3 truth-is-text, §5 locks, §8.3 spend guardrails).
> Two studies (Figma multiplayer, Obsidian local-first) and the machine
> synthesis were lost to an API session limit; the synthesis below is by the
> session lead from the six completed studies. Adopt/reject calls follow the
> COMPETITIVE-STUDY.md convention: every adoption must survive the "does it
> move truth out of text?" test.

## DaVinci Resolve (Blackmagic Design) — Cut page, Media Pool proxy workflow, Deliver page render queue

Resolve is the architectural opposite of Manju One — a monolithic GUI NLE with an opaque project database and in-engine AI — but its Cut page is the industry's best worked example of "review throughput as a first-class feature." Its strongest ideas (alternates scoped to a slot with exactly one active, duration-normalized review playback, always-visible rendition provenance badges, named/inspectable render jobs with pre-flight warnings, one-key edit verbs with a where-will-this-land indicator) are about attention, decision speed, and trust rather than about the engine, so they port cleanly onto a text-truth, hash-stale build system. Research was done against live web sources (Blackmagic feature pages, the Resolve 18.6 manual mirror, practitioner guides and reviews); all patterns below are evidenced from those, not training memory.

### Take Selector: alternates live inside the slot, exactly one active
- **what**: In Resolve, right-clicking a timeline clip creates a Take Selector — a container on the timeline slot holding N alternate takes. You click any take to preview it in place; whichever was last clicked becomes the active one when you close the selector. A stacked-filmstrip badge marks slots that contain alternates. 'Ripple Take' absorbs duration differences between takes so the surrounding edit stays in sync. 'Finalize Take' collapses the container to a plain clip, making the pick permanent. A grade applied to the container applies to every take, so comparisons are apples-to-apples.
- **why_it_works**: Alternates are modeled as a property of the SLOT, not as timeline clutter — so the decision state ('which take is live here?') is always singular, local, and glanceable, and comparison happens in context (same position, same grade) rather than in an abstract bin. Explicit finalize separates 'still deciding' from 'decided'.
- **effort**: S

### Fast Review: duration-normalized playback
- **what**: The Cut page's Fast Review button plays clips at a speed derived from their length: long clips play faster, short clips near real time, so every clip takes roughly the same wall-clock time to review and a whole bin can be watched in a bounded, predictable sitting.
- **why_it_works**: It budgets the scarce resource — director attention — instead of letting footage duration dictate review time. Predictable per-item cost makes batch review something you actually start ('this will take 4 minutes') rather than defer.
- **effort**: M

### Source Tape: the whole bin as one scrubbable strip
- **what**: One button turns the entire media pool bin into a single continuous 'tape' in the viewer — no opening files one by one. You scrub or fast-play across everything, and hardware/keyboard sort keys reorder the tape by timecode, camera, duration, or clip name instantly.
- **why_it_works**: It deletes per-item navigation cost (open, watch, close, find next), turning 'browse N files' into 'scan one surface.' Sorting the same surface different ways answers different questions (what's newest? what's from shot 3?) without changing tools.
- **effort**: M

### One-key edit verbs plus a where-will-this-land indicator
- **what**: The Cut page and Speed Editor bind each editorial intent to a dedicated key: SMART INSERT, APPEND, RIPPLE OVERWRITE, SOURCE OVERWRITE, PLACE ON TOP — you never build the operation from primitives. Crucially, the Smart Indicator (an animated arrow in the timeline ruler) continuously shows where the next edit will land before you press anything, independent of the playhead. Reviewers note the two-handed verb-key + dial design is what makes rough cutting fast, and that trust in the Smart Indicator is mandatory.
- **why_it_works**: Named verbs make actions predictable and learnable (recognition over composition), and pre-commit effect preview means the user never fires blind — trust comes from seeing consequences before the keystroke, not from undo after it.
- **effort**: S

### Proxy tri-state policy with per-clip provenance badges
- **what**: Resolve separates rendition POLICY from rendition FACT. Policy: Playback > Proxy Handling is a single global tri-state (Prefer Proxies / Disable All Proxies / Prefer Camera Originals). Fact: every clip in the Media Pool and timeline carries a corner badge — purple PXY = proxy in use, PXY-only = original missing, HQ = original in use — plus an optional Media Pool 'Proxy' column reading None / Offline / <resolution>. Proxies are ordinary, recognizably-named files stored in a Proxy folder alongside originals, auto-linked with an explicit Relink command; the contrast case, Optimized Media, is an internal .dvcc cache with cryptic names that guides steer professionals away from. Export has an explicit 'use proxy media' checkbox.
- **why_it_works**: The user never has to wonder which rendition they are judging — provenance is glanceable at the item level while the policy is one global switch, and 'previews are plain portable files next to originals' beats an opaque cache for trust and shareability. Judging color/detail on a proxy you thought was the master is the classic silent failure this design eliminates.
- **effort**: M

### Render queue as named, inspectable, pre-flighted jobs
- **what**: Resolve's Deliver page queue turns each render into a durable object: default names (Job 1...) that are click-to-rename, per-job metadata on display (project, timeline, output path, and optionally frame size, format, fps, audio channels, duration), presets for settings, serial top-down execution, an overall progress bar plus OS-taskbar progress. Pre-flight guards fire BEFORE spend: a warning when the timeline contains offline/missing media, an overwrite warning for existing files, and a Yes/No/Cancel prompt before re-rendering already-completed jobs. Housekeeping is explicit: per-job X, 'Clear Rendered', 'Clear All'. Quick Export offers the same presets one-click from any page for the common case.
- **why_it_works**: When work is slow and costly, the unit of work must be a named artifact you can read, re-run, and audit — and every predictable waste case (missing inputs, accidental overwrite, redundant re-render) is caught by a cheap check before resources burn, which is exactly what builds user trust in pressing Start.
- **effort**: S

### Boring Detector: deterministic lint that paints the timeline
- **what**: A Cut page analyzer with two user-set thresholds: shots longer than a maximum get a grey highlight ('boring'), cuts shorter than a minimum frame count get red ('jump cut'). It is pure rule-based analysis rendered as passive color on the timeline — no suggestions, no automation, just flags telling the editor where to look.
- **why_it_works**: Review attention is the bottleneck, and cheap deterministic rules can rank where attention should go without any intelligence in the loop; passive highlighting informs without seizing control, so there is nothing to mistrust or undo.
- **effort**: S

### In-engine AI edit assists (Close Up auto-reframe, Smooth Cut)
- **what**: Cut page edit modes where a neural network acts inside the edit operation itself: 'Close Up' runs face detection and auto-zooms/reframes as part of placing the clip; Smooth Cut morphs across a cut. The intelligence executes inline in the engine at edit time, with a dedicated hardware key.
- **why_it_works**: For Resolve it collapses a multi-step creative judgment into one keystroke inside a monolithic app that already owns the media and the ML runtime.
- **effort**: L

Sources: https://www.blackmagicdesign.com/products/davinciresolve/cut; https://jayaretv.com/edit/davinci-resolve-take-selector-explained/; https://www.provideocoalition.com/review-davinci-resolve-speed-editor-part-1/; https://www.steakunderwater.com/VFXPedia/__man/Resolve18-6/DaVinciResolve18_Manual_files/part3956.htm; https://www.steakunderwater.com/VFXPedia/__man/Resolve18-6/DaVinciResolve18_Manual_files/part154.htm; https://elements.tv/blog/everything-you-need-to-know-about-the-proxy-workflow-in-davinci-resolve/

## Frame.io (Adobe)

Frame.io (Adobe) is the industry-standard cloud review-and-approval platform for video post; V4 (GA since late 2024) rebuilt it around a keyboard-first player, time/range-anchored comments, version stacks, status-as-metadata with saved-search Collections, and highly configurable share links for zero-account client review. It is the strongest reference for manju gui's review-keyboard mode, shot-card take handling, and board.html — with the caveat that its link-revocation/analytics/anonymous-write machinery is exactly the cloud-service surface manju's doctrine excludes, so patterns were split into adoptable review mechanics vs rejected service mechanics. Web research succeeded; all patterns below are evidenced by Frame.io's live V4 Knowledge Center except where noted.

### Comment-at-playhead with auto-pause and range brackets
- **what**: Typing in the comment box auto-pauses the player and pins the comment to the current timecode — the act of typing is what captures the frame, no manual timecode entry. The posted comment renders twice: a card in the comment panel and a bubble marker on the scrubber; clicking the card seeks the player to that exact frame. For ranges, bracket handles appear under the play bar (or press I/O) to extend the comment across an in/out span, and preview playback restricts to that range.
- **why_it_works**: It removes the transcription step that makes video feedback lossy ('around the 4-second mark...'). Feedback becomes frame-addressable data instead of prose, so it is directly actionable — and the auto-pause means capturing the anchor costs zero extra gestures.
- **effort**: M

### Version stack: one card, latest-on-top, badge-opens-lineage
- **what**: A new upload dragged onto an existing asset creates a stack; the browser shows ONE card displaying the newest version with a 'v3' badge. Clicking the badge at the top of the player opens the full version list; a manage-stack menu reorders or removes versions. Share links carry a 'view all versions vs latest only' toggle.
- **why_it_works**: It matches the reviewer's mental model ('the shot', not 'the file'): default attention goes to the newest cut while full lineage stays one click away, so iteration never creates browser clutter or ambiguity about which version is current.
- **effort**: S

### Locked-scrub comparison viewer for two versions
- **what**: Select two assets (or 'Compare Versions' on a stack) to open side-by-side players with linked zoom/playback controls; controls can be unlinked. For stills, a draggable slider overlays the two, and 'Show/Hide Differences' renders the right side desaturated with changed pixels highlighted. Comments can still be made in compare mode, targeted at either asset by clicking its header.
- **why_it_works**: Verifying a revision ('did the new take actually fix what I asked?') becomes a single perceptual act instead of tab-flipping plus memory. That directly builds trust in each iteration and speeds the approve/reject decision.
- **effort**: M

### Status as plain metadata + saved-search review queue (Collections)
- **what**: Approval state in V4 is just a metadata Field with colored label values (defaults like Needs Review/In Progress/Approved, options fully customizable), shown as chips on asset cards. Collections are saved searches over fields that auto-populate — e.g. a 'Needs Review' Collection is a living queue used to run multi-stage approvals, with no workflow engine.
- **why_it_works**: Approval is data, not ceremony: queues emerge from filters, are always current, and cost zero process overhead. Colored chips make the 'what still needs my decision' scan instant.
- **effort**: S

### NLE-standard keyboard vocabulary for review
- **what**: The exact keymap: Space/K play-pause, J/L shuttle (2x/4x/8x, Shift+J/L incremental), Left/Right = 1 frame, Shift+arrows = 10 frames, C = new comment, Shift+R reply, I/O = in/out points, [ and ] = previous/next asset, F fullscreen, Ctrl+L loop — discoverable via an in-player keyboard icon overlay.
- **why_it_works**: It borrows muscle memory every editor and director already has from NLEs; the entire review pass happens without touching the mouse, which is where review throughput actually comes from. Inventing a novel keymap would squander free training.
- **effort**: S

### Export-time share scoping (adopt) vs live link management (reject)
- **what**: A Frame.io share is a configured object: at creation you pick layout (Grid/List/Reel sequential player), toggles for comment/download/view-all-versions, passphrase, expiration; after sending you can flip visibility off, watch view counters and last-viewed, and audit an activity feed (Opened, Viewed, Commented, Downloaded).
- **why_it_works**: The sender decides per-share exactly what recipients see and can do; revocation and analytics close the trust loop after the link leaves their hands.
- **effort**: S

### Internal vs share-visible comments (lock icon)
- **what**: Before sending, any comment can be toggled Public/Internal; internal comments display a lock icon and never render on client-facing share links, while public ones do. One comment stream serves both audiences.
- **why_it_works**: Teams keep candid process notes adjacent to client-visible feedback without maintaining two systems — the redaction decision is made once, per comment, at write time, not at export time under pressure.
- **effort**: S

### Zero-account client review page (adopt read-only half, reject anonymous write)
- **what**: A share-link recipient watches immediately with no account; name+email are asked only on first comment. When commenting is disabled the page degrades gracefully: media plus all existing comments remain visible read-only. 'Open in Viewer' skips the landing page for single-asset shares.
- **why_it_works**: Every ounce of client-side friction costs feedback; and read-only degradation keeps everyone looking at the same source of truth even when they can't write to it.
- **effort**: S

Sources: https://help.frame.io/en/articles/9105251-commenting-on-your-media; https://help.frame.io/en/articles/9105337-keyboard-shortcuts; https://help.frame.io/en/articles/9101068-version-stacking; https://help.frame.io/en/articles/9952618-comparison-viewer; https://help.frame.io/en/articles/9105232-shares-in-frame-io; https://help.frame.io/en/articles/9105242-share-links-explained-for-clients

## Descript

Descript is the reference prosumer text-based video/podcast editor: media is transcribed, then the transcript becomes the editing surface — delete/rearrange words to cut/rearrange media — with a synced timeline, checkpointed AI co-editor (Underlord), version history, and an explicit stable-URL publish flow. It is architecturally opposite to Manju One (transcript is DERIVED from recorded media and edited in a cloud-backed GUI with an embedded LLM; manju's text is AUTHORED truth and media is generated, intelligence external), so its value to manju is not architecture but interaction mechanics: how it makes text↔media correlation instant, deletions visibly reversible, agent actions individually revertible, and publishing deliberately manual. Evidence note: help.descript.com blocks direct fetching (Cloudflare), so help-center mechanics were gathered via search-result extracts of the official articles, corroborated by two fully-fetched third-party reviews; all patterns below are current-product behavior, not marketing claims.

### Mode-scoped writes: 'correct text' vs 'edit media' on one surface
- **what**: Descript's transcript accepts two write semantics: normal typing/deleting edits the underlying media, while a toggled Correct-text mode (Opt/Alt+C, plus quick shortcuts: C corrects a word, Z+click cycles capitalization, X+click cycles punctuation) changes only the transcript text and never touches media. The active semantic is unmissable — the editor chrome tints green while in correct mode.
- **why_it_works**: Edits to the same artifact can have wildly different blast radii (fix a typo vs cut media). Binding blast radius to an explicit, visually loud mode prevents accidental expensive operations and lets users move fast when in the cheap mode.
- **effort**: M

### Visible, reversible deletion (strikethrough + 'Restore removed media')
- **what**: Deleting transcript words cuts the media non-destructively: cut text renders struck-through, timeline gaps show as lighter sections with their duration in seconds, and right-click → 'Restore removed media' on any script selection restores everything cut inside it — script-track media, layers, and sequences.
- **why_it_works**: When cuts stay visible and trivially restorable in-context, deletion becomes a cheap experiment rather than a commitment, and reviewing 'what was cut' requires no diff archaeology.
- **effort**: S

### Per-agent-action checkpoints with inline Revert
- **what**: Underlord records a checkpoint immediately before applying any batch of edits; the checkpoint appears inline in the chat at that point, and every agent response carries an action bar with a Revert button that rolls the project back to that checkpoint — independent of the human's global undo stack (formerly 'rollback', renamed and moved into the action bar to make undoability obvious).
- **why_it_works**: Agent edits arrive in semantic batches, not keystrokes. Scoping undo to agent-action granularity makes delegation safe: any single agent step is reversible without unwinding the director's own interleaved work, which is the core trust primitive for human+agent editing.
- **effort**: M

### Itemized batch review: detect all, decide per-item or apply-all
- **what**: The filler-words tool scans the whole composition and opens a sidebar listing every detected instance ('um', 'uh', 'like', 'you know'…) with timestamp and in-place audio preview; the user either removes all in one click or steps through instances choosing keep/remove per item.
- **why_it_works**: Converts an unbounded editing chore into a bounded checklist; in-place preview drives per-item decision cost to near zero, while 'apply all' preserves the fast path when trust is high. Decision speed comes from itemization, not automation.
- **effort**: L

### Stable target, explicit Update: publishing is a deliberate re-export
- **what**: Export → 'Descript (web link)' creates a share page at a stable URL; later project edits never propagate automatically. When the published copy is behind, the Export control relabels to 'Update', and the page changes only when the user clicks it. The relabeled button is itself the staleness indicator.
- **why_it_works**: A hard wall between working state and published state means viewers never see half-finished work, and encoding staleness into the primary button's label makes the 'you have unpublished changes' state impossible to miss without any extra notification machinery.
- **effort**: S

### Read-only time travel with an escape banner
- **what**: File → Version history lists autosaved/named versions; clicking one previews the entire project at that version in read-only form with a persistent top banner offering 'return to latest revision'; Restore is a separate explicit button distinct from previewing, so browsing never mutates.
- **why_it_works**: Separating 'look at the past' from 'change the present' makes history exploration risk-free, and the always-visible banner prevents the classic failure of editing — or believing you've lost — the latest state while parked on an old version.
- **effort**: M

### Bidirectional anchoring between text and rendered media
- **what**: One playhead exists simultaneously as a blue cursor in the script and a blue vertical line in the timeline; during playback the current word highlights and both views auto-scroll; clicking a word seeks the media; Esc / 'jump to playhead' re-centers; scene thumbnails in the script are clickable, and dragging a timeline scene boundary live-highlights the script word where it will land.
- **why_it_works**: Constant two-way anchoring collapses the 'where is this text in the video / where is this frame in the text' search loop to one click — that loop is most of review latency in any text↔media tool.
- **effort**: S

### Embedded in-app AI co-editor (Underlord panel)
- **what**: A chat panel inside the editor where the AI first produces a multi-step project brief / plan, waits for user approval, then directly executes multi-step edits on the open project, with inference running on Descript's cloud.
- **why_it_works**: Co-locating agent and editor in one process/cloud gives the agent implicit full context and tool access with zero integration friction — the user never wires anything up.
- **effort**: L

Sources: https://help.descript.com/hc/en-us/articles/15726742913933-Edit-like-a-doc; https://help.descript.com/hc/en-us/articles/10119613609229-Correct-your-transcript; https://help.descript.com/hc/en-us/articles/10164106619405-Version-history; https://help.descript.com/hc/en-us/articles/10476343201933-Restore-removed-media; https://help.descript.com/hc/en-us/articles/36958274409357-Revert-or-rollback-changes-made-by-Underlord-beta; https://help.descript.com/hc/en-us/articles/36803785502221-Underlord-beta-Your-AI-co-editor-in-Descript

## 剪映 / CapCut Desktop (ByteDance)

ByteDance's desktop editor pairs a pro NLE layout (media panel top-left, preview center, contextual inspector right, timeline bottom) with aggressively beginner-first flows: a draft shelf home screen with zero save buttons, template slots you just fill, and one-click AI verbs (智能字幕 auto-captions, 文稿匹配 script matching, TTS with voice preview, 一键成片 multi-candidate generation, 智能剪口播 transcript-based cutting). Its monetization UX — Pro badges on assets and per-feature AI credit prices quoted before use, plus a live file-size estimate in the export dialog — is a masterclass in pre-commit cost clarity. Web research was reachable; all patterns below are grounded in current help-center/tutorial evidence except where noted.

### Draft shelf: resume-first home cards with zero save ritual
- **what**: CapCut's home screen is a grid of draft cards (thumbnail, name, duration, last-edited), with a grid/list toggle and a '...' context menu limited to safe ops (rename, duplicate, backup, publish-as-template, delete). There is no Save button anywhere — projects continuously autosave into a plain folder (project JSON + media stored separately), and the editor's Details panel displays the project folder's literal filesystem Path field.
- **why_it_works**: Recognition over recall: a thumbnail + recency is enough context to resume work, and killing the save ritual removes the single biggest data-loss anxiety for beginners. Showing the real file path converts 'app magic' into inspectable truth, which builds trust with power users.
- **effort**: S

### Script matching (文稿匹配): captions from authored text, not ASR
- **what**: Alongside ASR captions, 剪映 offers 文稿匹配: Text → 智能字幕 → 文稿匹配 → paste your prepared script (≤5000 chars, one sentence per line recommended) → 开始匹配. The engine force-aligns the script to the spoken audio and emits perfectly-spelled, pre-punctuated caption clips with timing — documented as more accurate and faster to QA than 识别字幕 (ASR), which requires proofreading passes.
- **why_it_works**: When the user already owns a ground-truth script, alignment beats recognition: zero typos, zero re-review of text content, only timing to check. It inverts the workflow from 'transcribe then fix' to 'author then align'.
- **effort**: M

### Voice preview before paid generation (TTS panel)
- **what**: CapCut's TTS lives in the selected text clip's right panel: a voice browser (200+ voices filtered by language/gender/tone) where every voice has a ~5-second preview button that is accurate to final output, played BEFORE you hit 'Generate speech'. Only after audition do you commit the full-length generation; speed/pitch/volume sliders adjust after.
- **why_it_works**: Auditioning a cheap sample kills the most expensive failure loop in generative workflows: full-length generation followed by 'wrong voice, redo'. It moves the decision to the cheapest possible artifact.
- **effort**: S

### One-click fan-out: generate N candidates, review by switching (一键成片)
- **what**: 剪映's 一键成片: pick raw materials → the system auto-generates ~5 complete candidate videos from different templates → user flips between candidates in a preview strip, can swap the template on the spot, then exports the winner or drops into full editing. Review is 'which one?', never 'build it from scratch'.
- **why_it_works**: Humans judge comparatively far faster and more confidently than they judge a single artifact in isolation. Fanning out N variants converts open-ended creative review into a fast forced-choice, and the reject cost is zero because candidates are disposable.
- **effort**: M

### Cost badges before, live totals at commit (Pro crown + AI credits + export estimate)
- **what**: Every paid asset in CapCut's browse panels carries a Pro/crown badge before you touch it; AI features quote per-use credit prices (voice cloning 10–25 credits/min, avatars 20–40/video) and free-tier quotas (auto-captions 10 min/video, AI auto-edit 5/month). The export dialog shows an estimated file size that updates live as you change resolution/bitrate/codec — you see the consequence before you commit to the render.
- **why_it_works**: Price tags at the point of temptation, plus a live 'consequence meter' at the point of commitment, eliminate surprise — the single biggest trust killer in metered tools. Users spend more willingly when spend is never ambush.
- **effort**: S

### Contextual inspector: selection drives what you see
- **what**: CapCut desktop shows a main toolbar when nothing is selected; selecting a video clip, text, or audio swaps the right-side Details panel and sub-toolbar to only that object's properties (text clip → font/style/TTS section; audio → volume/fade/noise-reduction). Advanced controls live one level deeper behind section expanders. Beginners literally cannot see irrelevant tools.
- **why_it_works**: Progressive disclosure by selection keeps working memory on the current object and makes the valid-actions set self-evident — the interface teaches itself. Pros lose nothing because depth is one click away, not removed.
- **effort**: M

### Template placeholder slots + batch replace
- **what**: CapCut templates are timelines with designated replaceable slots: creators mark clips replaceable; consumers tap 'Use template', see slots as placeholders, fill them one-by-one via 'Replace' or all at once via 'Batch replace' (multi-select media mapped onto placeholders in order), tweak text in the right panel, export. Structure is fixed; the user only supplies content.
- **why_it_works**: Separating structure (expert-authored) from content (user-supplied) collapses the blank-canvas problem into a fill-in-the-blanks task; explicit empty slots make remaining work visible and finite.
- **effort**: M

### Transcript-marked cut candidates (智能剪口播)
- **what**: Desktop: right-click a talking-head clip → 智能剪口播. The audio is transcribed into a transcript view where the system pre-marks three classes of cut candidates: silent pauses (annotated with their duration), filler words (嗯/啊/然后…), and repeated retakes (detected re-said sentences). The user rubber-band selects marked text and hits Delete; the corresponding timeline segments are removed. Editing video becomes editing text.
- **why_it_works**: Reading marked text is an order of magnitude faster than scrubbing audio, and pre-classified candidates (with durations) turn an open judgment task into rapid accept/reject decisions while the human keeps final authority over every cut.
- **effort**: L

Sources: https://www.capcut.com/help/use-and-export-templates-in-capcut; https://www.capcut.com/help/how-to-set-replaceable-material-clips; https://www.videoproc.com/video-editor/how-to-use-capcut.htm; https://www.minitool.com/news/capcut-project-file-location.html; https://zhuanlan.zhihu.com/p/639816219; https://zhuanlan.zhihu.com/p/692339161

## Nx Cloud / Bazel BEP viewers (BuildBuddy) / GitHub Actions UI

All three are viewers over a deterministic-ish task engine, which is exactly manju's shape. Nx Cloud's core trust move is making cache behavior inspectable: filter tasks by cache status, then "Compare to similar tasks" shows a side-by-side diff of input hashes so "why did this rebuild" has a mechanical answer; flakiness is defined hash-mechanically (same input hash fails then passes). BuildBuddy is a pure viewer over Bazel's Build Event Protocol — the engine emits a structured event stream/file and every UI (invocation page, cache tab with AC-miss-filterable request table, action explorer, invocation diffing, timing profile) is built outside the engine, which is a direct blueprint for manju's "intelligence and presentation stay outside" doctrine. GitHub Actions contributes the triage ergonomics: auto-expand only the failed step, permalink any log line, "Re-run failed jobs" scoped retries with prior attempts kept navigable, an Enable-debug-logging checkbox at retry time, and $GITHUB_STEP_SUMMARY where a step writes Markdown that renders at the top of the run page. Web research succeeded; all patterns below are grounded in current docs/blogs consulted July 2026. The main filter applied: these are fleet-scale multi-tenant cloud products, so their analytics/org layers are rejected, while their single-invocation inspection patterns transfer almost verbatim to a localhost single-director tool where each rebuild costs real money.

### Input-hash diff: 'why is this stale / why did this rebuild'
- **what**: Nx Cloud: open a task, click 'Compare to similar tasks', pick a reference run; UI shows side-by-side rows of hashed inputs with changed rows highlighted (it diffs recorded hashes, not source). BuildBuddy equivalently has whole-invocation diffing and an Action details page that compares inputs, args, env vars across two builds with color-coded differences. The mechanism: persist the full input manifest (name -> content hash) with every execution, then render a two-column diff of manifests, changed rows first.
- **why_it_works**: Cache distrust is the #1 killer of deterministic build tools. Turning 'it rebuilt, I don't know why' into a mechanical, per-input answer converts the hash system from a black box into an auditable ledger — users stop suspecting the cache and start fixing over-broad inputs.
- **effort**: M

### Cache-status filter chips on the task/shot list
- **what**: Nx Cloud run details lets you filter the task list by cache status (hit/miss/etc) and click through to details; BuildBuddy's Cache tab has a 'Cache requests' table filterable by 'AC Misses'. Mechanism: every list row carries a status facet, and one-click chips with counts (e.g. 'miss (7)') narrow the list.
- **why_it_works**: Triage is a filtering problem: the reviewer's question is never 'show me everything' but 'show me only what will cost money / only what failed'. Counts on the chips double as a zero-click summary of run health.
- **effort**: S

### Cache hits replay the original output, labeled with provenance and savings
- **what**: Nx restores both the terminal output and the produced files on a cache hit — a hit looks like a real run, replayed instantly and flagged as from cache. Nx Cloud aggregates this into headline 'time saved' numbers. Mechanism: store logs alongside outputs at execution time keyed by input hash; on a hit, replay them with a provenance banner instead of printing nothing.
- **why_it_works**: Silent no-ops feel like the tool did nothing or hid something. Replaying the original evidence with 'from cache, built <when>' makes hit and miss indistinguishable in informational value, and quantifying what the hit avoided (time, and for manju: dollars) makes the cache emotionally credible.
- **effort**: S

### Auto-expand only the failure; permalink any log line
- **what**: GitHub Actions run pages render each step as a collapsed section with a duration badge; 'any failed steps are automatically expanded to display the results'. Clicking a log line number copies a permalink to that exact line; log search only covers expanded steps; a gear menu downloads the full archive.
- **why_it_works**: The failed step is the answer to 'what do I look at first' — pre-answering it removes the most repeated navigation act in CI triage. Line permalinks make 'look at this' a URL instead of a description, collapsing communication loops.
- **effort**: S

### Step-authored Markdown summary rendered atop the run page ($GITHUB_STEP_SUMMARY)
- **what**: In GitHub Actions, any step appends GitHub-flavored Markdown to the file at $GITHUB_STEP_SUMMARY; the accumulated Markdown renders on the run summary page above the logs — used for test tables, build reports, structured results. The engine defines only the contract (a file path); content intelligence lives in the steps.
- **why_it_works**: It cleanly separates 'engine executes' from 'something smarter narrates'. Reviewers get a human-oriented digest without scrolling logs, and because the channel is just a file, any tool — including an external AI — can author it without the engine knowing.
- **effort**: S

### Scoped retry: 'rebuild failed only', with attempts kept side by side
- **what**: GitHub Actions offers 'Re-run failed jobs' (only failures + dependents re-execute; successful jobs' outputs are reused), a per-job re-run icon, an 'Enable debug logging' checkbox at re-run time, and a 'Latest' attempts dropdown to navigate prior attempts. Nx shows a '1 retry' badge with 'Attempt 1 / Attempt 2' tabs to compare logs, and defines flaky mechanically: same input hash both failed and succeeded.
- **why_it_works**: Failure recovery is the highest-frequency decision in build UX; scoping the retry to exactly the failed subgraph makes the safe choice the easy choice, and keeping attempts side by side turns 'it works now?' into evidence. The hash-based flaky definition separates 'your inputs are wrong' from 'the executor is unreliable' without any heuristics.
- **effort**: M

### Build events as a text protocol; every UI is an external consumer (BEP)
- **what**: Bazel emits the Build Event Protocol — a structured event stream also writable as a local JSON file (--build_event_json_file) — and BuildBuddy's entire product (invocation page, live-updating status, cache tab, raw events view) is a separate program consuming those events. The engine contains zero presentation; viewers subscribe or read the file.
- **why_it_works**: One canonical event log means CLI, GUI, dashboards, and bots can never disagree, and new consumers need no engine changes. It also makes 'live' trivial: tail the file. This is the architectural reason BuildBuddy could exist as a third party at all.
- **effort**: M

### Fleet analytics: trends, drilldowns, impact-ranked flaky scatter plots
- **what**: Nx Cloud Enterprise Task Analytics plots flaky tasks by impact score (flake_rate x sample_size) with red/yellow/gray priority tiers, metrics cards, and time-wasted tables; BuildBuddy's Trends page graphs build-time percentiles and cache-hit ratios with a drilldown tab that clusters slow builds and highlights their common dimensions.
- **why_it_works**: At thousands of runs/day across hundreds of engineers, aggregate statistics are the only way to find systemic problems; ranking by impact directs limited platform-team attention.
- **effort**: L

Sources: https://nx.dev/docs/features/ci-features/flaky-tasks; https://nx.dev/docs/troubleshooting/troubleshoot-cache-misses; https://nx.dev/docs/features/cache-task-results; https://www.buildbuddy.io/ui/; https://www.buildbuddy.io/blog/debugging-slow-bazel-builds/; https://docs.github.com/en/actions/how-tos/monitor-workflows/use-workflow-run-logs

## Runway ML (Gen-4 / Gen-4.5 era web app, help center, API docs, changelog — researched live 2026-07-05)

Runway is manju's architectural opposite on nearly every doctrinal axis — cloud-hosted, credit-metered, database-truth, embedded in-app agent (Chat Mode) — but because every generation costs real money at consumer scale, its spend-disclosure, provenance, and verdict-capture UX is unusually evolved and battle-tested. The transferable core: cost is printed on the commit button itself; every output carries its full reproducible recipe (prompt/settings/seed) plus one-click reuse; verdicts (favorites/tags) captured during review are respected by every downstream picker; queue states come with published expected durations and an explicit errors-never-bill rule. Research caveat: help.runwayml.com returns 403 to direct fetches, so help-center facts were extracted via search-result summaries of the specific articles cited below; docs.dev.runwayml.com/guides/pricing and runwayml.com/changelog were fetched directly and confirmed the credit-rate table, cost calculator, spend-history table, favorites, tags, and presets features.

### Price printed on the trigger
- **what**: The Generate button itself carries the exact credit cost of the configured generation before you click, recomputed as model/duration/resolution change; hovering a disabled Generate button explains exactly why it is disabled; the remaining credit balance is pinned to the top-right corner of every generative session; a published per-model per-second rate table (e.g. Gen-4 Turbo 5 cr/s, Gen-4.5 12 cr/s) and an interactive cost calculator in the API docs back the numbers.
- **why_it_works**: Cost disclosure happens at the exact moment and pixel of commitment — the spend decision and the action are one gesture, requiring zero mental arithmetic or trips to a pricing page; explained-disabled states eliminate dead-end confusion and support tickets.
- **effort**: S

### Recipe travels with the output (See full prompt + Reuse Settings + copyable seed)
- **what**: Every generation in a session exposes its complete recipe from the output itself: a 'See full prompt' control above the generation reveals prompt and settings; the seed is visible and copyable; 'Reuse Settings' reloads every parameter of a past generation into the composer for a rerun; a fixed-seed toggle accepts a pasted seed to reproduce similar style/motion; named presets save and reapply whole parameter bundles (changelog Apr 2024).
- **why_it_works**: Provenance doubles as the iteration interface: reruns cost zero re-entry, 'what made this good' is always answerable, and seeds make similarity reproducible — the rerun loop collapses from minutes to one click.
- **effort**: S

### One-key verdicts that every downstream picker respects
- **what**: Any output can be favorited in one click and tagged in-session (via 'See full prompt' → 'Add Tag'); Assets pages sort/filter by favorite status, tag, type, duration, creator, date; crucially, the asset pickers inside the generative tools themselves can filter to favorites — verdicts captured while viewing are reused at selection time. All generations auto-land in an 'All Generations' folder, and deleting a session never deletes its assets.
- **why_it_works**: It separates judging from organizing: a single low-friction verdict recorded at the moment of viewing compounds into faster selection later because every chooser respects it; the append-only asset store means review actions are never destructive.
- **effort**: S

### Vary-from-winner branching
- **what**: Gen-4 Image's 'Vary' action takes one chosen output and spawns a fresh set of N derived variants (same recipe, new sampling) — any candidate becomes the parent of the next comparison round, with the session grid as the side-by-side surface.
- **why_it_works**: Directed search beats serial prompt tweaking: keeping N>1 candidates per round and branching from the best converges on intent faster, and the mental model is just 'more like this one' — no parameter literacy required.
- **effort**: M

### Queue states with honest expectations; errors never bill
- **what**: Every generation shows an explicit lifecycle state (pending/running/succeeded/failed; the API exposes PENDING/RUNNING/SUCCEEDED); help docs publish expected waits ('10–15 minutes at peak') and per-plan concurrency limits; credits are automatically returned on generation error, while user cancellation explicitly does not refund — the billing rule per failure mode is documented, not discovered.
- **why_it_works**: Trust in an async metered system comes from calibrated expectations: knowing where a job is, how long that state normally lasts, and the exact money consequence of each outcome prevents both anxious polling and rage-cancels.
- **effort**: M

### Itemized spend ledger with estimate-vs-actual
- **what**: A credit spend history table in Settings > Plans & Billing itemizes every deduction (which tool/model, when, how many credits); a dedicated 'How to troubleshoot a credit discrepancy' doc treats billing as an auditable claim users can check against published rates.
- **why_it_works**: Itemization turns metering from a black box into a verifiable record; in a tool that also predicts costs, the ledger is what lets users learn whether to trust the predictions.
- **effort**: S

### Two-lane economy: explore lane vs production lane
- **what**: Unlimited-plan users flip a toggle between Explore Mode (zero credits, relaxed/lower-priority queue, capped concurrency, supported models only) and Credits Mode (metered but fast with high limits); the documented workflow is iterate freely in the cheap lane, then re-run the winner in the paid lane (e.g. Turbo to explore, quality model to finish).
- **why_it_works**: Separating exploration economics from production economics removes spend anxiety during the phase where 90% of generations happen, and the explicit toggle makes the money-vs-latency tradeoff a conscious choice instead of a hidden policy.
- **effort**: M

### In-app conversational agent (Chat Mode)
- **what**: Runway embeds an LLM copilot inside the product: a chat pane that plans and fires generations on your behalf at standard credit rates, and even cycles its own context (asks to 'refresh' after 10 consecutive text-only messages); separately, Runway ships an MCP endpoint where external agents spend the same credits under the same rules as humans.
- **why_it_works**: For a broad consumer audience, embedded intelligence lowers the floor — no parameter literacy needed; the agent operates the tool for you inside one pane.
- **effort**: S

Sources: https://help.runwayml.com/hc/en-us/articles/15124877443219-How-do-credits-work; https://docs.dev.runwayml.com/guides/pricing/; https://help.runwayml.com/hc/en-us/articles/33545310653203-Generating-with-Sessions; https://help.runwayml.com/hc/en-us/articles/24298206897043-Navigating-Runway; https://help.runwayml.com/hc/en-us/articles/18053095835795-Unlimited-plan-details; https://help.runwayml.com/hc/en-us/articles/37309724921747-Why-does-the-Unlimited-plan-have-credits


---

## Synthesis (session lead) — adoption shortlist

The six studies converge on one meta-lesson: the products users trust most
make the ENGINE'S REASONING VISIBLE at the exact moment of decision — price
on the trigger (Runway, CapCut), input-hash diffs behind "why did this
rebuild" (Nx/BuildBuddy), provenance badges on cache hits, verdicts that
downstream pickers respect. Manju's engine already computes all of these
truths (spec_hash evidence, content keys, dry-run pricing, sidecar lineage);
the gap is purely presentational — which is exactly the gap a GUI round can
close without touching doctrine.

### Adopt next (engine-true, small-to-medium)

| # | Pattern (source) | manju shape | Effort |
| --- | --- | --- | --- |
| 1 | Price on the trigger (Runway/CapCut) | auto-refresh the dry-run estimate onto the 构建 button itself; waiting_user confirm shows the same figure | S (page) |
| 2 | Why-stale input-hash diff (Nx) | `explain` already carries spec_hash vs take_spec_hash; add per-field diff (which FIELD moved) to explain + shot card tooltip | M |
| 3 | State filter chips (Nx/GHA) | shots_by_state → clickable chips filtering the shot grid | S (page) |
| 4 | One-key verdicts (Runway/Frame.io) | take_notes shipped; add 好/弃 one-key rating convention + auto-select-respects-弃 | S |
| 5 | Failure auto-expand (GHA) | failed job row auto-opens its <details>; done rows stay collapsed | S (page) |
| 6 | Version stack (Frame.io) | final_v* cards newest-on-top with content-key lineage from .key.json sidecars | S-M |
| 7 | Recipe travels with the output (Runway) | takes already carry provider/params/seed lineage — 「用此参数重做 Reuse settings」 on every take → prefilled redo | S |
| 8 | Voice preview before paid TTS (CapCut) | Edge TTS is keyless: 试听 button per character in the bible editor | M |
| 9 | Duration-normalized take review (Resolve Fast Review) | cycling takes plays each at a rate that fits a fixed review window | S (page) |
| 10 | Comment-at-timecode (Frame.io) | convention only: "01:23 太暗" in take_notes; player seeks on click | S (page) |

### Already shipped this session (validated by the studies)

Draft-shelf/resume-first home (CapCut) → workspace mode project cards;
review keyboard vocabulary (Frame.io/Resolve) → v3 review mode (J/K/L,
space, 1..9, S); status-as-metadata review queue (Frame.io Collections) →
shots_by_state + next_step; render queue as inspectable jobs (Resolve
Deliver) → the FIFO job strip with phase progress; cache-hit provenance
(BuildBuddy) → content-key skip verdicts in explain + build warnings;
checkpointed reversibility (Descript) → git panel + check-gated editors
with auto-revert; itemized batch review (Descript) → QC items + repair
--auto split.

### Rejected, with the doctrine that rejects them

In-engine AI assists (Resolve Close Up, Descript Underlord, CapCut 一键成片's
in-app LLM) — §0: intelligence stays outside; Claude Code IS our Underlord,
on the other side of the wall. Cloud/database truth and live share-link
management (Frame.io, Descript, Runway) — §3 truth-is-text; the adopted
halves are --readonly LAN sharing and export-time scoping. Anonymous
write-back review — events.jsonl requires an actor. Template marketplaces
and fleet analytics — single-project, single-director tool (§1-⑦ scope).
Credit-wallet gamification (Runway) — budget.limit + ledger is the honest
version; spend UX yes, scarcity theater no.
