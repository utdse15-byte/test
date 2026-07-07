# ROUND-V-REFERENCES-2 — native-cut UX, project cockpit & experience-polish patterns

Date: 2026-07-07. Round V research deliverable (agent RV2). Method: five parallel
web-research passes (OSS-editor keyboard/snapping/undo; web-editor preview + drag +
complaints; project-cockpit/dashboard; experience-polish + agent-facing UX) plus the
session lead's own primary-source cross-verification of the load-bearing facts
(Shotcut's canonical keymap, LosslessCut's keyboard-first model, OpenCut's browser
architecture, the Remotion Player's real-time model). Every factual claim carries an
inline source URL; claims not pinned to a primary/authoritative source are marked
**(unverified)**. The generation-tool landscape moves monthly — editor keymaps and
API shapes are current as of 2026-07.

Companion to [ROUND-U-REFERENCES.md](ROUND-U-REFERENCES.md) (whose §1 "Native cut
depth" this report deepens toward a buildable spec), [COMPETITIVE-UX-STUDY.md](COMPETITIVE-UX-STUDY.md)
and [MARKET-GAP.md](MARKET-GAP.md). Where ROUND-U gave the *storyboard/export/asset*
vocabulary, this report gives the *editor keyboard model*, the *home cockpit*, and the
*polish checklist* — the three still-thin surfaces of goal items 5, 4 and 7.

**Manju invariants this report is written against** (do not violate — they gate every
打法建议 below): server-rendered pages, a stdlib-only CSP-safe HTTP server (no build
step, no external JS, no WebAssembly bundle, no canvas-compositing engine), vanilla JS
that only writes `textContent` and sets geometry via the CSSOM, every mutation through
the SAME engine core the CLI calls, and git-backed history/rollback as the truth store.
The current `/edit` page (round T+U, see `src/manju/gui/edit.py`) already ships:
multi-track lanes (主轨道/字幕/音频) off the compiled timeline, a global playhead ruler
with **click-scrub → still-frame preview** (`/edit/frame`), zoom, per-clip waveforms,
seam-marker transition picker (writes `rules.transition_overrides`), sync hints, a
click-first trim scrubber (1st click = in, 2nd = out), up/down reorder, and a look
picker — **no drag-and-drop, no keyboard shortcuts on `/edit`, no undo UI, no motion
playback**. That last clause is the gap this report closes.

---

## Headline findings (up top)

- **There is a real universal editor keymap, and it is small.** Across Shotcut,
  Kdenlive, Olive and (a subset in) LosslessCut the muscle-memory core is:
  **Space** play/pause · **J/K/L** shuttle · **←/→** frame-step · **I/O** in/out ·
  **S** split at playhead · **Ctrl+Z / Ctrl+Shift+Z** undo/redo · **+/−** zoom ·
  **Home/End** and **↑/↓ (or Alt+←/→)** jump edit points. Shotcut's own reference is
  the cleanest primary list (https://www.shotcut.org/howtos/keyboard-shortcuts/,
  verified by direct fetch). A *light* editor ships the first six and drops shuttle
  nuance, ripple verbs, and multi-track routing.
- **LosslessCut proves click-first + keyboard-first is a complete, loved model** with
  **zero timeline drag-editing** — you set in/out with `I`/`O`, jump segments, and
  export; it is "keyboard-first to maximize efficiency"
  (https://losslesscut.net/; https://github.com/mifi/lossless-cut). This is Manju's
  north star: buttons + numeric entry + a small keymap, no drag.
- **The browser-native editors Manju is measured against are built on exactly the
  stack Manju forbids.** OpenCut renders its preview with **HTML5 Canvas + an LRU
  frame cache + `requestAnimationFrame`**, stores media in **OPFS/IndexedDB**, and
  runs a **command-pattern undo state machine with explicit preview/commit phases**
  (https://tsjohnnychan.medium.com/opencut-building-a-privacy-first-video-editor-that-runs-entirely-in-your-browser-4065fa1bf8fa,
  verified). Manju cannot and should not copy this. Its honest preview is
  **still-frame scrub now → proxy `<video>` playback next**, never canvas compositing.
- **Undo, honestly, is git.** OpenCut/kdenlive keep an in-memory command stack that
  dies on close; Manju's truth is text + git with append-only takes. The honest web
  "undo" is not a volatile stack — it is **snapshot / rollback surfaced as first-class
  buttons** on top of the history feed Manju already has (`manju snapshot` /
  `manju rollback` / `manju history`), plus per-edit inline revert on the gated
  editors. Don't fake a Ctrl+Z that can't be trusted across a process boundary.
- **The cockpit's job is one glance → one action.** Every studied tool (Frame.io,
  GitHub Actions, Linear, Unity Hub, 剪映草稿箱) leads with *state*, elevates the
  *failed/at-risk* item, and offers exactly one *primary next action*. Manju already
  computes every number a cockpit needs (`status.project_status`, `spend.spend_report`,
  `exportstatus`, `director.suggest_next`, the QC json, the jobs queue) — the cockpit
  is presentation, not new engine.

---

## 1. OSS editor UX (goal item 5)

Studied: OpenCut (github.com/OpenCut-app/OpenCut), Shotcut, Kdenlive, Olive-editor,
LosslessCut, and the web-player layer (Remotion Player, twick).

### 1(a) Keyboard model — the universal editing shortcuts

**Shotcut (canonical primary list — verified by direct fetch of
https://www.shotcut.org/howtos/keyboard-shortcuts/):**

| Verb | Key |
|---|---|
| Play / pause | **Space** (also `L`; `K` pauses) |
| Shuttle | **J** rewind · **K** pause · **L** fast-forward (tap to accelerate) |
| Frame step | **←/→** (also `K+J` / `K+L`) |
| Set in / out | **I** / **O** |
| Split at playhead | **S** (`Shift+S` = split all tracks) |
| Ripple delete | **X** (`Shift+Del`/`Shift+Backspace`) |
| Ripple trim in/out | **Shift+I** / **Shift+O** |
| Zoom timeline | **=** in · **−** out · **0** fit |
| Undo / redo | **Ctrl+Z** / **Ctrl+Y** (or `Ctrl+Shift+Z`) |
| Append / overwrite | **A** / **B** |
| Seek prev/next edit | **Alt+←** / **Alt+→** |
| Toggle snapping | **Ctrl+P** (per the reference page) |

Shotcut 22.09 added an **Action Search + fully editable shortcuts** dialog reachable by
`?` — the "search every command by name" affordance (https://www.shotcut.org/blog/new-release-220923/).

**LosslessCut** is deliberately keyboard-first and minimal: **left/right = frame step**,
**Shift+←/→ or Ctrl+←/→ = jump to previous/next keyframe**, and the full map opens with
**Shift+/** (i.e. `?`) inside the app; all shortcuts are user-customisable in Settings
(https://andreaazzola.com/post/losslesscut-macos-cheat-sheet/;
https://github.com/mifi/lossless-cut/discussions/1646). Its verbs are **I** set-in /
**O** set-out / **B** split-segment-at-playhead / **+** add-segment / export — a
*non-destructive* split (it marks segment boundaries, never re-encodes) plus **manual
numeric timecode entry** of cutpoints, and drag exists only as an optional accelerator
(hold Shift to move/resize a segment)
(https://github.com/mifi/lossless-cut/blob/master/docs/index.md; https://losslesscut.net/).

**Kdenlive** follows the same spine with one divergence: **Space** play, **J-K-L**
shuttle, "navigate the clip by the JKL keys or by the left/right arrows and set the IN
and the OUT point by the I and O keys," but its razor is **Shift+R** (Timeline ‣ Current
Clip ‣ Cut Clip), not `S`
(https://docs.kdenlive.org/en/cutting_and_assembling/editing.html; full default table at
https://docs.kdenlive.org/en/user_interface/shortcuts.html). **Olive** is the cautionary
tale: it has **no stable documented default keymap** — keyboard-interaction design is
literally an open design issue on the repo
(https://github.com/olive-editor/olive/issues/1097), and custom shortcuts historically
failed to persist (https://github.com/olive-editor/olive/issues/1412). An alpha editor
without a settled keymap is itself evidence: the keymap is product surface, not chrome.

**The minimal universal core (what a light editor MUST ship):**

1. **Space** — play/pause (universal, non-negotiable; even the Remotion Player binds it,
   https://www.remotion.dev/docs/player/player).
2. **←/→** — frame-step.
3. **I / O** — mark in / out.
4. **S** — split at playhead.
5. **Ctrl+Z / Ctrl+Shift+Z** — undo / redo.
6. **+/− (or =/-)** — zoom.

Shuttle (`J/K/L`), ripple verbs (`Q/W`, `X`), and track-routing (`A/B`) are *pro-tier*
— skip them in a light editor until motion playback and real ripple exist.

**打法建议 1(a) — Manju's minimal keymap (fits the CSP-safe stdlib server):**
- The current `/edit` page has **no keyboard at all**; the main workbench (`page.py`)
  already owns a small, well-behaved handler (`j/k` shots, `e` edit, `1-9` select,
  `Space` play/pause, `r` refresh, `?` hints, modifier keys deliberately ignored) and
  the `/review` page owns `j/k/g/x/Space`. **Reuse that exact handler discipline** on
  `/edit`: bail on `input/select/textarea/contentEditable`, ignore `meta/ctrl/alt`
  except for the two undo chords, and expose a `?` hint bar (Shotcut/LosslessCut both
  train users to press `?`).
- **Ship these on `/edit`, and no more (v2):** `Space` = play/pause the scrub preview ·
  `←/→` = step one frame (uses the project fps Manju already snaps to) · `I`/`O` = set
  trim in/out at the playhead (writes the SAME `set_inout_take` the scrubber writes) ·
  `[`/`]` or `+`/`-` = zoom · `S` = **split** is the one genuinely new verb — see the
  spec sketch for why it maps to "insert a cut boundary," not a destructive razor.
- **Undo/redo chords must be honest** — bind `Ctrl+Z` to *inline revert of the last
  `/edit` mutation* (a git-backed rollback), not to a fake in-memory stack. See 1(c).
  If a chord can't be honored truthfully, don't bind it.
- Keep every keyboard verb a **thin alias for an existing button** — the button stays
  the source of truth (discoverable, accessible, testable), the key is the accelerator.
  This is exactly how the workbench's `1-9`/`e`/`Space` already work.

### 1(b) Snapping — magnetic timeline & the toggle convention

Two distinct concepts travel under "snapping":

- **Magnetic timeline** — clips auto-close gaps and stick to their neighbours. CapCut/
  剪映 build around a **magnetic main track (主轨吸附)** where moving a clip snaps forward
  to close the gap (ROUND-U §1, https://zhuanlan.zhihu.com/p/636375314 — secondary),
  and Final Cut Pro's Magnetic Timeline is the canonical original. The point: the main
  track is *always gapless*.
- **Snap-to** — while dragging/placing, the edit point sticks to nearby **playhead /
  clip edges / markers**. This is the near-universal editor behaviour.

**The toggle convention.** Every timeline editor makes snapping a toggle, because
precise placement sometimes needs it off: Shotcut binds **Ctrl+P** ("Toggle Snapping")
(https://www.shotcut.org/howtos/keyboard-shortcuts/, verified); Premiere/Resolve expose a
**magnet icon button** on the timeline; the cross-tool convention is also to **hold a
modifier while dragging to temporarily bypass snap**. Kdenlive documents the behaviour —
"dragging the beginning of one clip near to the end of another will result [in] the end
of the first clip snapping into place to be perfectly aligned"
(https://docs.kdenlive.org/en/cutting_and_assembling/editing.html) — with the toggle as a
timeline-toolbar magnet control; its default toggle *key* is **(unverified)**. Olive is
alpha with no documented snap-toggle default **(unverified)**.

**打法建议 1(b) — Manju: snapping is a compile-time truth, not a drag nicety.** Manju's
main track is **already magnetic and gapless** — the timeline is *compiled* from ordered
clips, so there is no gap to close and no drag to snap. And every duration is **already
frame-grid-snapped by the compiler** (FIX-B; property-tested round D). So Manju does not
need CapCut's magnetic-drag machinery. What the `/edit` page should add is **UI snapping
of the playhead and the trim in/out handles** to: the frame grid (round to `1000/fps`
ms — click-scrub already seeks whole frames), **clip boundaries**, **markers / sync-hint
positions**, and the covered clip's own handles. Toggle it with a **magnet button + `N`**
(default on), **hold Alt to bypass** while clicking the ruler. Keep the compile-time
frame snap as the real guarantee (QC still catches hand-authored off-grid values). Full
detail in the Native Cut v2 spec §B.

### 1(c) Undo — command-stack patterns vs git-backed truth

**How OSS editors do undo.** The classic implementation is the **Command pattern** — each
edit is a reversible command object with `execute()`/`undo()`, pushed onto an undo stack
(the canonical GoF "encapsulate a request as an object … support undoable operations").
OpenCut is explicit about this: its timeline is "a **mutable, undoable command-pattern
state machine with explicit preview and commit phases**"
(https://tsjohnnychan.medium.com/opencut-building-a-privacy-first-video-editor-that-runs-entirely-in-your-browser-4065fa1bf8fa,
secondary/medium-confidence). Desktop NLEs additionally surface the stack as a panel:
Kdenlive's **View ‣ Undo History** opens "a dockable window which lists all the changes
made to your project in the order they were made," letting you jump back multiple steps
at once (https://userbase.kde.org/Kdenlive/Manual/View_Menu/Undo_History;
https://docs.kdenlive.org/en/user_interface/menu/view_menu.html) — kept **in memory for
the current project session**, with no default depth cap documented (the "unlimited by
default" phrasing is from docs-adjacent summaries, **(unverified)** against the manual
text itself).

**The critical property for Manju: an in-memory command stack dies on close and cannot
cross a process boundary.** OSS editors keep the stack in RAM tied to the open project
file; close the app (or crash) and the undo history is gone — and Shotcut's very-real
**"undo executes two commands instead of one" bug** (§1f,
https://www.capterra.com/p/173467/Shotcut/reviews/) shows how fragile a hand-rolled stack
is as a *trust* primitive. Manju's editing arrives from **three actors** (human terminal,
AI, GUI) against **text + git** truth with **append-only takes** — a volatile per-tab JS
stack would be a lie the moment a second actor edits.

**打法建议 1(c) — Manju's honest "undo" is git, surfaced as first-class buttons.** Do NOT
fake a Ctrl+Z volatile stack. Manju already has every ingredient:
- **`manju snapshot`** = a labeled git checkpoint (`core/history.snapshot`) — offer
  "开始编辑前先快照" so a whole `/edit` session is one rollback away.
- **`manju rollback`** = `rollback_shot` (re-select a prior take, append-only) and
  `rollback_file` (guarded single-file `git restore`, refuses media/renders).
- **`manju history`** = events merged with the git log, actor-attributed.
Bind the **撤销 button / `Ctrl+Z`** to **revert the last `/edit` mutation** via
`rollback_file` (or re-write the prior value the event recorded) — a durable, auditable
undo — and show a compact **"最近改动 · 一键撤销"** list (last ~5 edit events) as the
honest analogue of an Undo-History panel. The tooltip/glossary must say it is a git
rollback, not a volatile stack (Manju's existing honesty stance). Full detail in the
Native Cut v2 spec §C.

### 1(d) Preview playback — how web editors do it without server rendering

**The Remotion split is the mental model.** Remotion's `<Player>` "can be rendered in a
regular React App … to display a Remotion video" — it renders the composition **in the
DOM/React tree, not on a server** (https://www.remotion.dev/docs/player/player), and is
explicitly a **preview, not a file producer**: "embed videos that are written in React,
and change them at runtime. Connect it to server-side rendering to turn them into real
MP4 videos" (https://www.remotion.dev/player). The Player exposes `seekTo()` frame-seek
(it "will pause for a brief moment, then start playing again after the seek is completed")
and `renderPoster()` still frames for unplayed/paused/ended/buffering states
(https://www.remotion.dev/docs/player/player). **Manju's situation is the inverse of
Remotion's:** Remotion previews *because it has no render yet*; Manju *has* the rendered
`final.mp4` — so its cheapest real preview is to **play the render**.

**The hard browser limit that shapes every web editor.** Chromium enforces **75
`WebMediaPlayer`s per frame** (each `<video>`/`<audio>` = one player); the Chromium
maintainer's rationale is that "each WebMediaPlayer brings to life a bunch of 'heavy'
objects … many 1000s of media players in memory" caused OOMs
(https://groups.google.com/a/chromium.org/g/media-dev/c/wEUYR7BvdZI/m/R-8X1EdiBAAJ). This
broke real apps (BigBlueButton after Chrome 92, https://github.com/bigbluebutton/bigbluebutton/issues/12806).
**Consequence: you cannot mount one live `<video>` per clip** — you recycle a small pool
and swap `src` / `preload` the current + next clip. The seek primitive is
`HTMLMediaElement.currentTime`, and seeking far from a keyframe "requires heavy decoding"
(https://html.spec.whatwg.org/multipage/media.html) — which is exactly why editing
against a **proxy / preview-conformed** clip matters.

**Proxy editing = "edit light, export heavy."** Shotcut edits on low-res proxies and
"when the final video is rendered it is rendered based on the full resolution versions"
(https://forum.shotcut.org/t/proxy-editing/18517). Manju already has this: per-clip proxy
renders + the `webpreview` transcode cache that makes any take browser-safe.

**Filmstrips** are extracted with one ffmpeg pass tiling sampled frames
(`select=not(mod(n,N)),scale,tile=…`) and **cached once at import**, then re-served
(https://medium.com/better-programming/how-video-editors-implement-timeline-filmstrips-using-ffmpeg-and-javascript-a4683ddaeb3c)
— which is exactly Manju's `.manju/frames` scrub-strip cache.

**OpenCut** (for contrast) composites on **HTML5 Canvas** driven by **MediaBunny**
(a WebCodecs wrapper, https://github.com/Vanilagy/mediabunny) for hardware decode + LRU
frame cache + `requestAnimationFrame`, with **FFmpeg.wasm** for conversion
(https://tsjohnnychan.medium.com/opencut-building-a-privacy-first-video-editor-that-runs-entirely-in-your-browser-4065fa1bf8fa
— secondary, the live source 404s mid-rewrite, **medium-confidence**). **twick** is the
same shape (canvas timeline + live-player, https://github.com/ncounterspecialist/twick).
**Every one of these requires the JS-bundle/WASM stack Manju forbids** — canvas
compositing is off the table.

**打法建议 1(d) — feasible preview for Manju, ranked cheapest→most (matches the spec
sketch's Tiers):**
1. **Filmstrip scrub + poster still (near-free, exists today):** the playhead indexes the
   cached frame strip / grabs `/edit/frame` — the Netflix seek-preview pattern
   (https://msujaws.wordpress.com/2012/09/04/seek-previews-for-html5-video/). This IS
   Manju's current `/edit` behavior; keep it as the paused/seek preview.
2. **Play `final.mp4` (low cost, real motion, exact):** one `<video>` over the render,
   `Space` toggles, playhead driven from `currentTime`. Best fidelity-per-line when a
   fresh final exists.
3. **Single/dual pooled `<video>` over webpreview/proxy (low-moderate, previews edits):**
   one element, swap `src` at clip boundaries, `preload` the next; a second hidden element
   pre-seeked to the next clip hides the boundary stall. Honors the 75-player cap
   directly; needs only native `<video>`/`<input type=range>`, CSP-trivial, no WASM.
4. **Positioned-DOM multi-element compositing (medium):** only if live overlay preview is
   ever required — still respects the cap by mounting only active clips.
5. **Canvas/WebCodecs or a Remotion-style Player (disqualified):** needs the JS/WASM
   bundle Manju rules out.
Ship 1 (have it) → 2 (biggest win) → 3 (previews un-rendered edits). Never 5.

### 1(e) Drag vs click-first design — stay click-first

**LosslessCut is the proof a click/keyboard-first editor is complete.** "This tool is
almost ridiculously simple: there is no complex timeline, no effects, and no transitions.
Just a player, a timeline, a few buttons, and some very straightforward keyboard
shortcuts" (https://mundobytes.com/en/Losslesscut-tutorial:-a-complete-guide-to-cutting-video-without-loss/).
Its verbs are keyboard/click: **I** in, **O** out, **B** split segment at playhead,
**+** add segment, **Space** play, **←/→** or **,/.** frame-step, plus **manual numeric
timecode entry** of cutpoints; **drag exists only as an optional accelerator** ("hold
SHIFT while dragging a segment … to move or resize it")
(https://github.com/mifi/lossless-cut/blob/master/docs/index.md). Users praise it for
being immediate.

**Drag now needs a non-drag alternative by standard.** WCAG 2.2 **SC 2.5.7 Dragging
Movements (AA)**: "All functionality that uses a dragging movement … can be achieved by a
single pointer without dragging, unless … essential"
(https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html). It protects
trackball/head-pointer/eye-gaze/speech users; a drag-only UI is also a **2.1.1 keyboard
failure** (https://www.accessibility.chat/articles/the-drag-and-drop-accessibility-crisis-when-basic-interactions-lock-out-users).
Crucially, keyboard access **alone does not satisfy 2.5.7** — the alternative must be
single-pointer/clickable (https://testparty.ai/blog/wcag-dragging-movements-guide). W3C's
own recommended alternatives are exactly Manju's model: "adjacent controls for moving the
element up or down … by tapping or clicking," and "click/tap anywhere on the slider
track" (https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html).

**Drag is also less precise.** On dense timelines "dragging can only move multiple frames
at a time, making it difficult to achieve frame-level precision," whereas numeric entry
("-2:00") is exact (https://community.adobe.com/t5/premiere-pro/is-there-a-way-to-do-quot-precision-dragging-quot-a-la-fcp/td-p/4337934).
Fair counterpoint: some editors find drag faster for *flow* — so offer both, but a
numeric/button path is the precise, accessible *baseline*.

**打法建议 1(e):** Manju's click-first trim (click-in/click-out), **up/down reorder
buttons**, and **seam-click transitions** already satisfy SC 2.5.7 by construction — this
is a compliance *feature*, not a missing-drag *gap*. Frame the report/UI that way. Add the
keyboard accelerators (§1a) and **numeric in/out entry** (type an exact ms/frame) as the
precise baseline; do **not** add timeline drag-editing. If drag is ever added it must stay
an optional accelerator over the existing click path.

### 1(f) What OSS editors get WRONG — 10 lessons to heed

Mined from real reviews/forums:
- **Kdenlive** — "keeps randomly crashing … I'm losing my progress"
  (https://discuss.kde.org/t/kdenlive-keeps-crashing/42467); the project maintains
  dedicated crash-troubleshooting docs (https://docs.kdenlive.org/en/troubleshooting/windows_issues.html)
  — stability is a standing pain point.
- **Shotcut** — laggy preview ("skipping one frame out of every three"), a timeline op
  that "COMPLETELY REARRANGES YOUR TIMELINE," and an **undo bug that "executes two undo
  commands instead of one"** (https://www.capterra.com/p/173467/Shotcut/reviews/);
  "entire UI lags … to the point of Not Responding" (https://forum.shotcut.org/t/entire-shotcut-ui-lags-like-crazy-while-playing-any-video-often-to-the-point-of-not-responding/50776).
- **OpenShot** — instability so routine it ships an official "How Do I Fix OpenShot
  Crashing or Freezing?" 7-step guide (https://support.openshot.org/support/solutions/articles/43000625543-how-do-i-fix-openshot-crashing-or-freezing-).
- **DaVinci Resolve (free)** — steep learning curve
  (https://forum.blackmagicdesign.com/viewtopic.php?t=138073), GPU-hungry, free tier
  single-GPU + CPU-only H.264/H.265 → slow, and HEVC "media offline"/artifacts push
  beginners to transcode first (https://skorppio.com/blog/davinci-resolve-hardware-requirements-complete-guide-for-2026;
  https://www.videoconverterfactory.com/tips/davinci-resolve-h265.html).
- **Olive** — README: "alpha software … highly unstable … use at your own risk"
  (https://github.com/olive-editor/olive); perpetual-alpha, single-developer
  (https://www.debugpoint.com/olive-0-2-video-editor-review/).

**10 lessons a light editor must heed** (each maps to a Manju strength or an audit):
1. **Never lose the user's work** — the #1 cited failure. *Manju already wins:* truth is
   text + git, append-only takes, atomic tmp+replace media, snapshots — a crash costs
   seconds. Keep it.
2. **Undo must be bulletproof — one action, one undo.** Shotcut's double-undo is a trust
   killer. *Manju:* make the §1c git-backed undo exact and test it as an invariant.
3. **Keep preview smooth even at lower fidelity — use proxies.** *Manju:* proxy +
   webpreview already exist; play those, not raw takes.
4. **Respect the browser's limits** — never one `<video>` per clip (75-player cap). Pool
   + preload (§1d).
5. **Consumer codecs must "just work" on import.** *Manju:* webpreview auto-transcode of
   non-browser-safe takes already does this for the GUI; keep it.
6. **Stay light on hardware** — no big-GPU requirement for basic edits. *Manju:* the
   engine is ffmpeg/CPU, drawtext/html fallbacks — already light.
7. **Resist feature bloat** — LosslessCut thrives by being "a player, a timeline, a few
   buttons." *Manju:* the cockpit must not become six equal buttons (§2/§cockpit).
8. **Offer precise, non-drag input as the accessible baseline** (WCAG 2.5.7) — numeric
   in/out + click verbs; drag only as optional accelerator.
9. **Don't over-face beginners** — dense UIs are a repeated barrier. *Manju:* the 新手/
   专业 mode switch + glossary (round U) is exactly the mitigation; keep the cockpit and
   `/edit` calm in 新手 mode.
10. **Signal maturity honestly, invest in stability over features.** *Manju:* the honesty
    stance (待更新/兜底/crashed-render notes, needs-manual-check) is this lesson lived —
    don't regress it for a flashy feature.

---

## 2. Project cockpit / dashboard (goal item 4)

Studied: Frame.io V4, GitHub Actions / CircleCI / Jenkins, Linear & Height, Unity Hub &
Unreal, 剪映首页/草稿箱, plus NN/g on dashboards & empty states. The concrete cockpit
block layout with Manju data sources is the dedicated section at the end of this report;
this section is the *evidence and the priority order* behind it.

### 2(a) Frame.io V4 — state as a version stack + status pill; a review queue as the "next action"

Frame.io's project home is an **asset browser**, not a metrics dashboard: Grid/List views
where the **media thumbnail dominates** and metadata layers on top via a **Fields**
dropdown (~33 prebuilt fields incl. Status, Rating, Resolution, Duration, Comment Count)
(http://help.frame.io/en/articles/9101037-project-layout-overview). The state-at-a-glance
primitive is the **version stack** — revisions collapse into one unit, newest on top
("the newest version will be ready to view"), switch by clicking the version number
(https://help.frame.io/en/articles/9101068-version-stacking). The **Status** field values
are **approved / in progress / needs review**
(https://blog.frame.io/2024/04/23/frame-io-v4-beta-metadata-collections/). The
"what needs me" surface is a **Collection** — a live saved view ("assets meeting the
criteria get added immediately"), so a "Needs Review" collection is a live to-do queue —
plus the **notification bell** (red-dot unread count) as an update-feed/to-do list
(https://help.frame.io/en/articles/9105374-in-app-notifications) and the **Inbox** review
queue with Opened/Unopened filters (https://help.frame.io/en/articles/12832120-track-your-share-links-with-frame-io-inbox-beta).
No dedicated "at-risk" health signal — risk is inferred from status + stalled activity.

### 2(b) CI dashboards — the state/outcome split, and the failed run surfaced first

GitHub Actions leads with a **status icon per run** (green check / red cross / yellow
in-progress) (https://graphite.com/guides/github-actions-status); CircleCI leads with
"pipeline number, when triggered, status, duration" — **what → when → who → logs one
click away** (https://circleci.com/blog/check-pipeline-status-from-ide/). The authoritative
**status-vs-conclusion split**: `status` = lifecycle (`queued/in_progress/completed/
waiting/requested/pending`), `conclusion` = result (`success/failure/neutral/cancelled/
timed_out/skipped/action_required/stale`) (https://docs.github.com/en/rest/actions/workflow-runs).
**Design lesson: keep "is it running" distinct from "did it succeed"** so an in-flight
item never reads as a failure. The **failed run is surfaced by pre-attentive color**
("failed jobs automatically highlighted in red … identify which to review first",
https://www.datadoghq.com/blog/circleci-monitoring-datadog/) and paired with a **next
action** ("get the build failure logs", CircleCI). Jenkins Blue Ocean separates **current
run status** from a **weather health trend** (Sunny >80% passing … Storm <21%)
(https://www.jenkins.io/doc/book/blueocean/dashboard/) — a *trend* signal distinct from
the *last result*.

### 2(c) Linear & Height — explicit project health + progress-at-a-glance

**Linear's Project Overview** leads with a summary, then editable **properties** (Status,
Lead, Team, Milestone, Start/Target date), with **the most recent project update shown
directly on the overview** (https://linear.app/docs/project-overview;
https://linear.app/docs/initiative-and-project-updates). Health is three explicit tokens —
**On track / At risk / Off track** (green/yellow/red) — "a high-level signal of the current
state" with status/challenges/next-steps text (https://linear.app/docs/initiative-and-project-updates).
Risk gets a **visual decay signal**: "a dashed outline indicates a project update is
slightly overdue before the health icon turns grey," and updates surface **% of milestones
completed** to "spot when a project might be at risk"
(https://linear.app/changelog/2023-08-16-project-progress-reports); the Project Graph gives
a **live predicted completion date**. **Height** defaults to a spreadsheet with a per-list
**progress status bar** (completed / incomplete / % complete) for "immediate sprint
overview" and an **inline auto-logged activity feed** per task
(https://freshvanroot.com/blog/height-app-review/). Both quantify progress via length/%
(the NN/g pre-attentive channel).

### 2(d) Game-engine hubs — quick-resume above the fold, "new" is a template-first branch

**Unity Hub**'s default home is the **Projects table** (Name, **Modified**, Editor
version, Favorites, source control, **Size**), sorted by recency/favorites — quick-resume
is the primary job; it "displays warnings for missing Editor versions"
(https://docs.unity.com/en-us/hub/projects). **New Project** is a deliberate, **template-
first** branch (Core/Sample/Learning) that pre-answers configuration
(https://docs.unity.com/en-us/hub/project-create). **Unreal**'s Project Browser "launches
automatically," leads with **Recent Projects**, then template categories + a Project
Defaults panel (https://dev.epicgames.com/documentation/unreal-engine/creating-a-new-project-in-unreal-engine).
Lesson: **resume is above the fold; "new" is a separate, config-pre-answered path.**

### 2(e) 剪映 / CapCut 首页 & 草稿箱 — the draft is the unit; recency + backup are the two per-card signals

The mobile home defaults to the **草稿箱 (drafts box)** — "all un-exported projects live
here" as draft cards, tap to re-enter the editor — with **开始创作 ("Start Creating")** as
the single dominant CTA (https://zhuanlan.zhihu.com/p/1897697347716248985 — search
snippet). Desktop 专业版: a left rail (首页 / 模板 / 我的云空间) + 开始创作 top-center +
draft thumbnails with **修改时间 (modified time)**. Per-draft actions include **备份至云空间
(back up to cloud)**, rename, duplicate, delete; members get **auto 云同步** with advice to
**标星 (star)** important drafts and **查同步进度** before switching devices
(https://diantuoyi.com/article/18198.html). **Lesson: draft = the project unit; last-edited
recency and cloud-backup state are the two status signals surfaced per card.**

### 2(f) NN/g — empty states, "one obvious next action", and pre-attentive dashboards

**Empty states (3 rules)** (https://www.nngroup.com/articles/empty-state-interface-design/):
(1) **communicate status** — say *why* empty; (2) **provide learning cues** in-context
("Star your favorites to list them here"); (3) **provide a direct pathway** — a Create
button + a path to demo data. The overarching principle: **"reduce choices to exactly one
obvious next action"**, reveal complexity gradually. **Dashboards** are "collections of
data visualizations … that impart at-a-glance information on which users can act quickly";
use **pre-attentive channels — length and 2D position** for quantity, and **"color should
not be used to communicate quantitative values"** (color/shape = category, not amount)
(https://www.nngroup.com/articles/dashboards-preattentive/). Above-the-fold: users scan in
an **F-pattern**, so critical metrics belong **top-left**
(https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/ — well-known NN/g
finding, cited via secondary summaries, **(exact page not fetched)**).

### 2(g) Synthesis — the creative-tool cockpit priority order

A single hierarchy recurs across all six tools. Above the fold, in the F-pattern's
high-attention zone:

1. **STATE FIRST** — one dominant, pre-attentive status token, encoded as **color +
   position** (GitHub icon, Jenkins weather, Linear traffic-light, Frame.io status pill +
   newest-version-on-top, Height progress bar). And, per GitHub/Jenkins, **keep
   "in-progress" distinct from "succeeded/healthy"** so a running job never reads as a
   failure.
2. **NEXT ACTION** — exactly **one** primary affordance, **derived from state** (CapCut
   开始创作; Unity/Unreal resume-or-new; Frame.io Needs-Review queue; CircleCI "view
   failure logs"). NN/g: "exactly one obvious next action."
3. **RECENT ACTIVITY** — a scannable reverse-chronological feed (Frame.io bell + share
   activity, Height per-task log, Linear latest update, CapCut recency-sorted drafts) —
   answers "what changed / where do I resume?"
4. **RISKS / COSTS** — flagged **by exception, not always-on** (red for failed, sorted
   first; Linear's dashed-outline decay + % complete; Unity's missing-version warning +
   Size as a cost proxy; CapCut cloud-sync progress). Surface risk **only when present**,
   breaking an otherwise calm baseline.

**Above the fold:** the dominant **state** token, the **one primary action**, the top few
**activity** items, and any **active risk** flags — with quantitative progress encoded via
length/position. Deeper detail (all versions, full history, cost breakdown) goes below or
one click away. **For a brand-new/empty project, collapse the whole cockpit to a single
first-run empty state**: say there's nothing yet, teach what belongs, offer the one action.

---

## 3. Experience-polish catalog (goal item 7)

Manju is unusual in having **three consumers of every surface** — a human at a terminal,
a human in the local web GUI, and an AI agent driving the CLI/MCP. Each heuristic below
is read against all three, and the section ends with the three-column audit checklist
the brief asks for.

### 3(a) Error-message quality — the what / why / how-to-fix triple

**NN/g's canonical rules** (https://www.nngroup.com/articles/error-message-guidelines/):
use **human-readable language** (no raw codes/jargon as the primary content), **precisely
describe** the issue, **offer constructive advice** (problem *and* remedy), take a
**non-blaming tone** ("place accountability on the system, not the user" —
https://www.nngroup.com/articles/error-messages-scoring-rubric/), show the error **close
to its source**, use **redundant non-color-only indicators**, **match severity** (inline
vs modal), **don't fire prematurely** while the user is still typing, and **preserve user
input** so they correct in place. NN/g even ships a 12-item **scoring rubric** (1–4,
A–D) you can audit each message against — auditable bars include "≥3 error indicators,"
"7th–8th-grade reading level," "describe a solution sufficient to fix," "original input
preserved" (https://www.nngroup.com/articles/error-messages-scoring-rubric/).

**rustc / Elm — the gold standard.** Elm's thesis is **"compilers as assistants, not
adversaries"** (https://elm-lang.org/news/compilers-as-assistants); Rust's RFC 1644
"focus[es] more on the source the programmer wrote" — **point at the code span**, primary
label = *what*, secondary label = *why* ("narrative flow through the code"), **minimal by
default, rich on demand** (`--explain CODE` expands using the user's actual code), and
stays **legible without color** (https://rust-lang.github.io/rfcs/1644-default-and-expanded-rustc-errors.html).
The transferable move: show the offending input, mark the exact location, state
expected-vs-found, suggest the fix, keep a terse default with an opt-in deep explanation.

**clig.dev for CLIs** (https://clig.dev/#errors): **"catch errors and rewrite them for
humans"** (a guiding conversation); protect **signal-to-noise** (irrelevant output slows
diagnosis); **"did you mean X"** on a near-miss (but don't silently auto-correct);
**group repeated errors** under one header; **most important info at the end**, red used
sparingly; and **provide an escape hatch** — for unexpected errors emit debug/traceback +
how-to-report, but keep the traceback out of the default stream (behind `--verbose`/a log).

**The auditable triple** (fusing all three): every Manju error answers **WHAT** (precise,
plain, near-source), **WHY** (which input/field/span, expected-vs-found), **HOW to fix**
(a concrete next step or "did you mean X", input preserved) — in a non-blaming tone, with
a pointer to richer help that doesn't bloat the default.

**打法建议 3(a):** Manju's engine already emits **one-line findings** for YAML/schema/
media errors (FIX-D) and has a structured `core/failures.py` record
(step/subject/cause/evidence/hint/log_path) surfaced by `manju failures` "rustc-style"
(round S item 10) — this is exactly the what/why/how-to-fix triple already. **Audit every
user-facing string against NN/g's rubric** and confirm each `Failure` carries a `hint`
(the "how-to-fix") and the `evidence` names the span/field (the "why"). The GUI editors'
409/validate strips must show the errored **field in text** with a suggestion, not just a
red box (§3d). One gap to check: do CLI tracebacks stay behind `--verbose`? clig.dev says
they must.

### 3(b) Empty states & first-run

**NN/g's three empty-state rules** (https://www.nngroup.com/articles/empty-state-interface-design/):
(1) **communicate status** — say *why* empty (loading vs error vs genuinely empty), e.g.
"no records for the selected range"; (2) **provide learning cues** in-context ("Star your
favorites to list them here" — more memorable than an up-front tutorial); (3) **offer a
direct pathway** — a button that populates it, ideally plus a path to **sample/demo data**.
Design the three types distinctly: **first-use** (teach + CTA), **user-cleared**, and
**no-results** (confirm the query ran, suggest broadening). **First-run/onboarding:** show
value fast, **a single clear next step** (not a wall of options), progressive disclosure,
intelligent defaults (https://www.userflow.com/blog/onboarding-user-experience-the-ultimate-guide-to-creating-exceptional-first-impressions).
CLI analogues: a bare invocation prints concise help with a **runnable example**
(https://clig.dev/#help); "no results" is stated in words, never silent.

**打法建议 3(b):** the GUI already has an **onboarding checklist** (`gui.onboarding`, 6
steps, live done-detection) and `manju new` **scaffolds commented shots that pass check
out of the box** (round R27) — that is textbook "sample content + single next step." The
cockpit empty-state (see the layout section) should collapse to that checklist when
`shots_total == 0`. Audit: does `manju status` on an empty project say the *why* and the
*one next command* (it does: `next_step = "创作阶段:先写 shots/…"`)? Does every GUI list
(events/proposals/failures/deliverables) render a "why empty + what to do" line rather
than a blank panel?

### 3(c) Latency masking — skeletons vs spinners vs progress

**Nielsen's three response-time limits** (https://www.nngroup.com/articles/response-times-3-important-limits/):
**0.1s** = feels instantaneous (no feedback); **1.0s** = flow-of-thought limit (no special
feedback needed, but direct-manipulation feeling is lost); **10s** = attention limit —
beyond it show a **percent-done indicator** and a **clearly signposted cancel**. Mapping:
<0.1s none · 0.1–1s optional subtle · 1–10s busy/indeterminate · >10s determinate +
cancel. **Which indicator** (https://www.nngroup.com/articles/skeleton-screens/): use a
**skeleton only for full-page loads** and one that **mimics the real layout** (a bare
header/footer frame reads as "broken" on long waits); use a **progress bar** for anything
measurable (download/upload/**convert a file**); use a **spinner** for short indeterminate
waits. Below ~500ms a skeleton is imperceptible — don't bother. The oft-quoted "skeletons
feel ~20% faster" figure is a **(unverified)** practitioner claim, not an NN/g finding.
CLI parallel (https://clig.dev/#output): **when stdout is not a TTY, show no animations
at all** — the terminal analogue of a cancel-able percent-done bar.

**打法建议 3(c):** Manju builds are the >10s case, and the engine already publishes
**coarse phases** (`run_build(on_phase=…)`: check/generate/voice/compile/captions/
render/qc/exports — round R8) and the GUI job carries `progress`. That is the
percent-done indicator — make sure the GUI renders it as a **determinate phase bar** with
a **cancel** affordance (currently a build is a single mutation lock; at minimum show the
active phase + holder). For the cockpit/lists, use a **layout-mimicking skeleton** on the
first `/api/state` load, not a spinner. For frame/waveform/strip `<img>` lazy-loads
(sub-second), no indicator. Audit the CLI: does it suppress spinners/animations under a
pipe (agent use)? It should — this is the same rule as `--json` auto-degrade.

### 3(d) Keyboard-accessible web forms (WCAG / WAI-ARIA)

Exact success criteria to audit the GUI editors against:
- **2.1.1 Keyboard (A):** all functionality operable by keyboard, no traps
  (https://www.w3.org/WAI/WCAG21/Understanding/keyboard.html).
- **2.4.3 Focus Order (A):** focus order preserves meaning
  (https://www.w3.org/WAI/WCAG21/Understanding/focus-order.html).
- **2.4.7 Focus Visible (AA):** a visible focus indicator on every focusable element
  (https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html).
- **3.3.1 Error Identification (A):** the errored item is identified and the error
  **described in text** (https://www.w3.org/WAI/WCAG22/Understanding/error-identification.html).
- **3.3.2 Labels or Instructions (A):** persistent associated `<label>`, **not
  placeholder-as-label** (https://access-proof.com/wcag-2-1-checklist).
- **3.3.3 Error Suggestion (AA):** when a correction is known, **suggest it**
  (https://www.w3.org/WAI/WCAG22/Understanding/error-suggestion.html). Note the split:
  3.3.1 = identify+describe, 3.3.3 = suggest the fix.
- Implementation: link errors programmatically (`aria-describedby`, `aria-invalid`) and
  manage focus to them.

**打法建议 3(d):** Manju's editors already do the hard part — **keystroke-time
`/api/validate`** with pydantic findings and a debounced ✓/red strip (round R17), plus the
409 conflict diff (round R15). Audit against the SCs: (1) is the validate strip's error
**programmatically associated** with the textarea (`aria-describedby`) and is focus moved
to it on save-failure? (2) does every form control have a real `<label>` (the CSP-safe,
no-inline-handler constraint makes this easy — server-rendered `<label for>`)? (3) is the
**focus indicator visible** given the custom CSS? (4) tab order logical in the dialogs?
The keyboard model (§1a) must not trap focus in the `<dialog>` editors (the existing Esc
handling is a good start).

### 3(e) AI-agent-facing UX — machine-readable everywhere

- **`--json` / `--plain` everywhere; respect the TTY** (https://clig.dev/#output):
  JSON for structure, plain tabular for `grep`/`awk`, and **auto-degrade** (no color, no
  animation) when stdout isn't a terminal; honor `NO_COLOR`/`TERM=dumb`/`--no-color`
  (https://no-color.org).
- **Stable, meaningful exit codes** (https://clig.dev/#robustness): 0 on success, distinct
  documented non-zero per failure class. The **BSD `sysexits.h`** vocabulary gives a
  portable set (`EX_USAGE` 64 bad args, `EX_DATAERR` 65 bad input, `EX_NOINPUT` 66 missing
  file, `EX_TEMPFAIL` 75 **retryable**, `EX_CONFIG` 78, …)
  (https://www.man7.org/linux//man-pages/man3/sysexits.h.3head.html). The agent-facing
  property: codes must be **stable and documented** so an agent branches on them, and one
  code must mean "safe to retry."
- **Idempotency / dry-run / safe-to-retry** (https://clig.dev/#robustness): `--dry-run`
  previews; **confirm before danger but accept `--force`/`--yes`** so scripts aren't
  blocked; **crash-only** design (defer cleanup so a re-run after interruption is safe).
- **Structured errors** — the closest formal convention is **RFC 7807 problem+json**
  (`type` = stable class URI, `title` = stable summary, `status`, `detail` = this
  occurrence, `instance`), superseded by **RFC 9457**, extensible
  (https://datatracker.ietf.org/doc/html/rfc7807 ; https://www.rfc-editor.org/rfc/rfc9457.html).
  Under `--json`, errors are objects with a **stable machine `code`** an agent branches on
  (not prose-matched) + a human `message` + a docs URL + a retry hint.
- **Determinism** (https://clig.dev/#future-proofing): human output may drift, but machine
  output is a **contract** — pin it via `--json`/`--plain`, keep changes **additive**,
  **warn before non-additive changes**, and keep ordering **deterministic/diff-stable**.

**打法建议 3(e) — Manju is already unusually strong here; audit the edges:** `--json`
exists broadly (status/explain/spend/tasks/failures/compare/exports/…); MCP is the
structured surface; **idempotency is a core value** (content-key skips, `--force` bypass,
append-only takes); the **spend gate** already implements confirm-before-cost with
`--yes`/`assume_yes`/GUI-flag and dry-run is lockless. Concrete audits: (1) do CLI
commands return **distinct exit codes** per failure class, or mostly 1? Map at least
`BuildLocked`, budget-breaker, `WaitingUser`, validation/schema, and missing-input to
**stable documented codes** (sysexits-aligned; a `TEMPFAIL`-style code for the retryable
build-lock contention). (2) Are `--json` **error** payloads structured with a stable
`code` (RFC 7807-style), or is the error only a human string? `core/failures.py` is the
natural home for a machine `code`. (3) Is machine output ordering **deterministic** (it
mostly is — canonical hashing, sorted dotted-path diffs)? (4) Is stdout animation
suppressed under a pipe?

### 3(f) Three-column audit checklist — HUMAN | AI-AGENT | SYSTEM

| Area | HUMAN (terminal + web) | AI-AGENT | SYSTEM / robustness |
|---|---|---|---|
| **Errors** | what/why/how-to-fix, non-blaming, near-source, input preserved (nngroup error-guidelines; scoring-rubric) | same error as `--json` object with stable `code`+`message`+docs URL (RFC 7807) | every error → documented non-zero exit code by class (clig #robustness) |
| **Errors** | web: errored field named in text, non-color indicator, focus moved to it (WCAG 3.3.1) | `code` is an enum, branched on — not prose-matched (clig #future-proofing) | sysexits vocabulary; one code = "retryable" (EX_TEMPFAIL) (man sysexits) |
| **Errors** | terminal: point at span/field, terse default, `--explain`/`failures` for depth (Rust RFC 1644; `manju failures`) | terse default; traceback behind `--verbose`/log, never default stdout (clig #errors) | message wording may change; `code`/exit code stay stable (clig #future-proofing) |
| **Latency** | <1s none · 1–10s busy · >10s determinate + cancel (nngroup response-times) | no spinner/animation/color when stdout ≠ TTY (clig #output) | honors NO_COLOR / TERM=dumb / --no-color (no-color.org) |
| **Latency** | web: layout-mimicking skeleton for full-page; progress bar for renders; spinner for short waits (nngroup skeleton-screens) | build phases exposed as structured progress (`on_phase`) | phase progress is exception-proof/advisory (round R8) |
| **Empty/first-run** | say why empty + one clear CTA + path to sample content (nngroup empty-state) | "no results" = empty structured array, exit 0, never silent | first run works from uninitialized state; scaffold passes `check` (clig #robustness; round R27) |
| **Empty/first-run** | first-run: value fast, single next step, smart defaults (userflow) | bare/`--help` prints a runnable example (clig #help) | onboarding done-detection is live, per-project dismissal |
| **Safety** | destructive ops confirm; severe require typing the name (clig #robustness) | `--force`/`--yes` bypass; `--dry-run` previews (clig #arguments-and-flags) | commands idempotent / crash-only safe to re-run (clig #robustness) |
| **Forms** | keyboard-operable, no trap, visible focus, logical order (WCAG 2.1.1/2.4.7/2.4.3) | validation via `/api/validate`; errors `aria-describedby`-linked | exit 0 only on true success; partial success a distinct code |
| **Forms** | persistent `<label>`, not placeholder-as-label (WCAG 3.3.2) | structured error carries retry-safe hint (RFC 7807) | deferred cleanup: start where prior cleanup didn't run (clig #robustness) |
| **Output** | human output may iterate freely | `--json`/`--plain` on every data command; scripts pinned to them (clig #output/#future-proofing) | machine output ordering deterministic / diff-stable; changes additive (clig #future-proofing) |

*Source-reliability note:* NN/g, W3C/WAI Understanding docs, clig.dev, Rust RFC 1644,
sysexits man page, and RFC 7807/9457 are primary and directly quoted. `elm-lang.org`
compiler-errors page would not render body text (SPA shell) — Elm's intent is corroborated
via the Rust RFC that explicitly borrows from it. The "~20% faster skeletons" figure is
**(unverified)** (secondary practitioner source). clig.dev uses single-page section
anchors (`#errors`, `#output`, `#robustness`, `#future-proofing`, `#help`,
`#arguments-and-flags`) — section names are reliable if a fragment drifts.

---

## Native Cut v2 — spec sketch (fits a CSP-safe stdlib server)

Design rule: **every native-cut affordance is a thin accelerator over an existing
engine mutation** (`set_inout_take`, `rules.transition_overrides`, reorder index,
`apply_mixer`, the look). The lanes VISUALIZE the compiled timeline; keyboard + snapping
+ undo + playback are added strictly on top of what `/edit` already renders. No canvas
compositing, no WebAssembly, no drag — this is the whole point of not being OpenCut.

### A. Minimal keyboard map (add to `/edit.js`; mirror the `page.py` handler discipline)

| Key | Verb | Wires to |
|---|---|---|
| **Space** | play/pause the scrub preview | §D playback |
| **←/→** | frame-step the playhead (±1 frame at project fps) | `seekTo(ms ± 1000/fps)` |
| **Home / End** | jump to timeline start / end | `seekTo(0)` / `seekTo(total)` |
| **↑/↓** | jump to previous / next **clip boundary** (edit point) | seam positions already in the DOM |
| **I / O** | set trim **in / out** at the playhead on the covered clip | `set_inout_take` (same as the click scrubber) |
| **S** | **split** = mark the covered clip's boundary as a hard cut / insert a reorder split point | reorder + `transition_overrides` (see C) |
| **N** | toggle snapping (magnet) | §B |
| **[ / ]** or **= / −** | zoom out / in | the existing `ed-zoom` range |
| **Ctrl+Z / Ctrl+Shift+Z** | undo / redo the last `/edit` mutation | §C (git-backed) |
| **?** | toggle the bilingual hint bar | static, like `page.py` |

Guardrails (copy verbatim from `page.py`'s handler): return early on
`input/select/textarea/isContentEditable`; ignore `meta/alt` and ignore `ctrl` except
the two undo chords; every key is a no-op in `--readonly`. `S` is the only NEW verb —
all others alias existing buttons.

### B. Snapping (a validity rule, not a drag nicety)

Manju's timeline is **already gapless and frame-locked** — the compiler snaps every
duration to the frame grid (PROGRESS FIX-B; property-tested in round D), and the main
track has no gaps to magnetically close (it is compiled from ordered clips). So Manju
does **not** need CapCut's magnetic-gap-closing. What it needs is **playhead + trim
snapping** in the `/edit` UI:

- **Snap targets:** the frame grid (round to `1000/fps` ms — the click-scrub already
  seeks to whole frames), **clip boundaries** (seam positions), **marker/sync-hint
  positions** (the sync-hint strip is already placed on the ruler), and the **in/out
  handles** of the covered clip.
- **Toggle:** a magnet button on the zoom row + the `N` key (Shotcut convention is
  `Ctrl+P`; the friendlier single-key `N` matches Kdenlive-family editors — pick `N`).
  Default **on**. Hold **Alt while clicking the ruler** to temporarily bypass snap
  (the universal "hold-a-modifier to disable snap" convention).
- Snapping stays **advisory in the UI**; the compile-time frame-grid snap remains the
  real guarantee, so a hand-authored off-grid value is still caught by QC (FIX-B).

### C. Undo — honest, git-backed (never a volatile stack)

The gold-standard OSS editors keep an in-memory command stack (§1c) that is **lost on
close** and cannot cross a process boundary — precisely the wrong model for a tool whose
truth is text + git and whose edits arrive from three actors (human terminal, AI, GUI).
Manju's honest undo has three tiers, all of which already exist in the engine:

1. **Per-edit inline undo (the `Ctrl+Z` binding).** Every `/edit` mutation is (a) a
   single-file text write to `rules.yaml` / a shot YAML / the take sidecar and (b) an
   appended event. Bind `Ctrl+Z` / the 撤销 button to **revert the last `/edit` event's
   file change** — reuse `core/history.rollback_file` (already a guarded single-file
   `git restore`, refuses media/renders) or re-write the prior value the event recorded.
   Show *what* it will undo ("撤销:S3 转场 → 默认") before doing it. Redo re-applies.
   This is a real, durable, auditable undo — not a fake stack.
2. **Session snapshot (`Ctrl+Shift+S` / 快照 button).** `manju snapshot` = a labeled git
   checkpoint (already built, `core/history.snapshot`). Offer "开始编辑前先快照" so a
   whole `/edit` session is one rollback away.
3. **History feed + rollback.** `manju history` merges events with the git log
   (actor-attributed); `manju rollback shot` re-selects a prior take (append-only).
   Surface a compact "最近改动 · 撤销" list on `/edit` (last ~5 edit events, each with a
   一键撤销). This is the *honest* analogue of an "undo history panel."

**打法:** label the button **撤销 (undo)**, but the tooltip/glossary must say it is a
git rollback, not a volatile stack — matching Manju's existing honesty stance (glossary
`兜底/待更新` etc.). Never bind a chord you can't honor truthfully.

### D. Preview playback — ranked by cost, none require a server render

Manju already has three assets that make in-browser playback possible with **zero new
server rendering**: (1) a rendered **`final.mp4`**, (2) per-clip **proxy renders** +
the **webpreview** transcode cache (makes any take browser-safe), (3) **frame strips**.
The current `/edit` shows only a **still frame** at the playhead — the gap. Plan, cheapest→most:

- **Tier 0 (ships today):** click-scrub → still frame (`/edit/frame`). Keep as the
  always-available fallback and as the seek-preview while paused.
- **Tier 1 — play the `final.mp4` (cheapest real motion, highest fidelity).** When a
  fresh final exists, the honest "preview of the assembled timeline" IS the final: mount
  one `<video src=/preview/final_vN.mp4>`, bind `Space`, and **drive the ruler playhead
  from `video.currentTime`** (and seek the video on ruler-click). This is the Remotion
  insight inverted — Remotion previews *because it has no render*; Manju *has* the render,
  so play it. Gate on the final's content-key freshness (show a "成片可能已过期" chip via
  `exportstatus` when stale). Cost: ~a few lines of JS; no server work.
- **Tier 2 — per-clip proxy `<video>` sequencing (works before any final / after edits).**
  One `<video>` element; when the playhead enters clip *k*, set `src` to clip *k*'s
  webpreview/proxy, `currentTime = in_ms/1000`, play; on `timeupdate` past `out_ms`,
  advance the playhead and swap `src` to clip *k+1*. This previews the *edited* order/
  trims *before* rendering — the genuinely new capability. Chromium caps ~**75
  `WebMediaPlayer`s per frame** (https://groups.google.com/a/chromium.org/g/media-dev/c/wEUYR7BvdZI/m/R-8X1EdiBAAJ),
  so **one element, swap src** (optionally a second hidden `<video>` preloading clip
  *k+1* for gapless swaps) — never one `<video>` per clip. Reuse the existing
  `/preview/<rel>` lazy-transcode + `Range`/`206` serving. Cost: moderate JS, no new
  server render.
- **Tier 3 (defer):** gapless double-buffered crossfade between the two `<video>`
  elements to smooth boundary swaps. Nice-to-have; not needed for a usable preview.

**打法:** implement Tier 1 first (biggest fidelity-per-line), Tier 2 next (previews
un-rendered edits), keep Tier 0 as the pause/seek still. **Never** attempt canvas
compositing of multiple tracks (that is OpenCut's WebAssembly/OPFS territory Manju has
correctly ruled out). Overlay/caption tracks are burned into `final.mp4` (Tier 1) or
shown as ruler marks (Tier 2) — not composited live.

### E. Drag vs click — stay click-first (it is a feature)

LosslessCut is the proof that a click-first + keyboard-first editor is complete and
loved (§1e). Manju's click-first trim (1st click = in, 2nd = out), button reorder, and
seam-click transition are **the accessible, testable, precise model** — and they already
satisfy **WCAG 2.2 SC 2.5.7 Dragging Movements** by construction
(https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html), whose own
recommended alternatives are literally "adjacent up/down controls" and "click anywhere on
the slider track." Keep them; add the keyboard accelerators above plus **numeric in/out
entry** as the precise baseline; do **not** add timeline drag-editing (imprecise,
a 2.5.7/2.1.1 risk, and it fights the compiled-truth model). If drag is ever added, it is
an optional accelerator over the existing click path — never the only way.

### F. Two-line summary of the whole spec

Add a 6-key accelerator layer (`Space ←→ I O S`, plus `N` snap, `[]` zoom, `Ctrl+Z`),
a magnet toggle that snaps the playhead/trim to frames+boundaries, a git-backed 撤销
button (+ session snapshot + a 5-item undo list), and motion playback that plays the
`final.mp4` when fresh and falls back to per-clip proxy `<video>` sequencing — all on
the existing lanes, all through existing engine mutations, no drag, no canvas, no build
step.

---

## Cockpit block layout (ordered, with Manju data sources)

The cockpit answers three questions in order, top to bottom: **What state is my film in?
→ What is wrong / at risk? → What is the one thing to do next?** — then supporting
detail (deliverables, spend, queue, activity). This mirrors the studied tools' hierarchy
(§2): Frame.io leads with version+approval state, CI leads with the failed run, Linear
leads with project health, 剪映 leads with the resumable draft. Every block below reads
from an **existing** engine function — the cockpit is a new *page*, not new *engine*.

| # | Block | Purpose (glance) | Data source (existing) |
|---|---|---|---|
| **0** | **Identity bar** | project name · `WxH@fps` · preset · mode chip | `build.status.project_status` (`project/preset/mode/resolution`) |
| **1** | **State strip** (above the fold) | shot counts by state (缺失/待挑选/待更新/就绪/broken) + voice-state mirror, as chips | `build.stale.evaluate_all` → `shots_by_state`, `voice_by_state` (`status.project_status`) |
| **2** | **Risk / blocker banner** (elevated, red — show only when non-empty) | build-lock held by another actor; QC errors; budget ≥80%; `latest_final_note` (crashed render); broken shots | `status.build_lock`, `status.qc` (`qc.json` errors), `spend.spend_report` budget context, `status.latest_final_note` |
| **3** | **Primary next action** (one big button) | the single best next step, executable | `director.suggest_next()[0]` (carries a ready `propose` action) with `status.next_step` as the human label |
| **4** | **Deliverables / freshness** | final·proxy·captions·cover·teaser·drafts with 上新/待更新/缺失/待人工确认 + version badge | `build.exportstatus` (`DeliverableRow`, `Freshness`) |
| **5** | **Spend** | total by provider, budget bar, estimate-vs-actual delta | `build.spend.spend_report` (`total/by_provider/estimated_total/budget_limit`) |
| **6** | **Queue** | in-flight cloud jobs (resume-polling) + GUI job runner state | `status.run_log.pending_jobs`; `gui.jobs` runner |
| **7** | **Recent activity** | actor-attributed feed (human/ai/gui) | `core.events.tail_events` / `core.history` merged log |
| **8** | **Suggestions** | the rest of `suggest_next` (redo stale, repair QC, package) | `director.suggest_next()[1:]` |

**Above-the-fold rule:** blocks 1–3 must be visible without scrolling — *state → risk →
one action*. Blocks 4–8 are supporting detail. Block 2 is **conditionally rendered** (a
clean project shows no red banner — the "everything's fine" state must feel calm, per
NN/g dashboard guidance folded in below).

**Empty state (brand-new project, `shots_total == 0`):** replace blocks 1–8 with the
**first-run checklist** (`gui.onboarding` already exists): 写 shots → `check` → dry-run →
`build`. This is the "show the one clear next step" empty-state pattern (§2/§3) — Unity
Hub / 剪映 "新建项目" energy, not a wall of empty widgets.

**打法建议 (cockpit):**
- Build it as an **8th server-rendered page** (sibling of the round-S/T/U pages), GET-
  renders the real numbers, `/cockpit.js` only refreshes via the existing `/api/state`
  fingerprint-poll + `/api/watch` long-poll — no new polling machinery.
- **One primary action, styled as the one primary button** (block 3). Everything else is
  a link or a chip. Resist putting six equal buttons up top (the OSS "feature bloat"
  failure, §1f).
- Reuse the **glossary tooltips** (`gui.glossary`) on every status word so 新手 mode
  reads the cockpit without a manual.

