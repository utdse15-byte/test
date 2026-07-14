# GUI 打磨波审计合卷 (2026-07-14) — usability / responsiveness / feel / visual polish

Mandate: "彻底优化 GUI——可用性、响应性、交互手感、视觉打磨" (owner-directed,
same-day follow-on to the convenience program #47-#48b). Method: architecture
+ test-pin mapping first (two read-only mappers), then a LIVE audit — every
page screenshotted at 1440px in headless Chromium on a scaffolded project
with real mp4 takes, console collected (clean on all 17 pages), suspicious
findings re-verified against computed styles, never markup alone. One
finding class was confirmed live before any fix landed.

## The load-bearing find: hidden-vs-display, a CSS bug CLASS

The HTML `hidden` attribute only hides via the UA rule `[hidden]{display:
none}`, which ANY author `display:` rule on the same element overrides —
and the class spelling `.hidden` loses the same way to any LATER author
`display:` rule (equal specificity, later cascade). The repo had hand-
patched the class variant six times (`.ed-modal.hidden`, `.ws-menu.hidden`,
`.ns-form.hidden`, `#kbdhint.hidden`, `.batchbar.hidden`, `.rv-alt-video
.hidden`) but the attribute variant had no owner. A systematic sweep of all
14 attribute-toggle sites found 7 BROKEN across 6 elements:

| element | page | live effect |
|---|---|---|
| `#cw-modal` (`.cw-modal{display:flex}`) | /create | **page dead**: the skill modal rendered OPEN on load, covered the viewport (`position:fixed;inset:0;z-index:300`), blocked every click, and could not be dismissed (close sets `hidden` — a no-op). Confirmed live: computed `display:flex` with `hidden` attr true; save-button click times out. |
| `.cw-editor` (`display:block`) | /create | ✎编辑 stage swap dead — doubly: the attribute never hid the active editor, and the target's server-rendered `hidden` CLASS was never removed. |
| `#sa-frame`, `.pk-prev-img`, `#cover-card-img`, `#cover-fine-img` (`display:block`) | /mixer, /packaging | preview `<img hidden>` placeholders rendered as visible empty boxes before any preview existed. |

Why nothing caught it: every /create test asserts over raw served HTML
(markup is correct); the #43 whole-GUI browser journey collected console
errors only (none fire); the real-browser harness never drove /create.

Fix, one owner: app.css now pins `.hidden{display:none!important}` +
`[hidden]{display:none!important}` (the board's own CSS carries the same
`[hidden]` guard) — the six per-selector patches are obsolete and the class
is extinct by construction. `showStage` additionally clears the hidden CLASS
on the target editor. Verified live: modal starts hidden (computed), opens
from the chip, closes on Escape, and the page underneath accepts clicks —
now permanent harness checks.

## Disposition ledger

| # | finding (short) | verdict |
|---|-----------------|---------|
| P-1 | /create dead under the always-open skill modal | LANDED — [hidden] owner + showStage class fix; 4 new harness checks |
| P-2 | /mixer /packaging preview imgs visible-empty before first preview | LANDED — same owner (better first paint) |
| P-3 | Windows renders bright-grey UA scrollbars/form controls into the dark theme | LANDED — `color-scheme: dark` on both surfaces' `:root` + thin dark scrollbar styling (gui) |
| P-4 | zero transitions anywhere: hovers snap, presses give no feedback | LANDED — eased hover transitions (.btn/.chip/.pnav a/.ws-item/.tl-clip/.dropzone), `:active` press dip |
| P-5 | no `prefers-reduced-motion` guard on pulse/flash/new animations | LANDED — global reduce block (all animations are decorative) |
| P-6 | two toast systems, two visual voices (SPA colored edge vs server-page plain border) | LANDED — shared `mj-rise` entrance + accent-edge language on `.toast-item`; systems stay separate code by design (common_js #G5) |
| P-7 | `加载中…` placeholders are static text | LANDED — `.loading` breathe animation (CSS-only) |
| P-8 | pollJob (exports/ingest/lab/series) polls at 10 req/s then MISREPORTS a job queued behind a long build as failure at ~60-90s (runner is serialized) | LANDED — adaptive 100ms→500ms cadence, ~10min cap, fetch-error retry; null job now toasts 仍在排队/运行(轮询超时), never 失败 |
| P-9 | /exports generate-all-stale (#48b deferred item A3-7) | LANDED — `#xc-gen-stale` bulk button over STALE free kinds only (final/proxy stay build-only §8.3; missing stays per-card); sequential, button carries live progress, reload only on full success so sticky error toasts survive |
| P-10 | palette hexes hand-copied across page CSS (7× `#6ea8fe`, 7× `#202b40`) | LANDED — `var(--accent)` + new `--accent-bg` token; favicon SVG literals stay (no var() in data-URI SVG) |
| P-11 | keyboard focus ring only on .btn/inputs — links, chips-as-buttons, summary unstyled | LANDED — `a/button/summary:focus-visible` joined the one outline rule |
| P-12 | scrollbar-appearance layout shift between short/long pages | LANDED — `scrollbar-gutter: stable` |
| P-13 | full-reload-after-action on server pages | REJECTED — the server-rendered stance is a recorded design (#5b, #45-rejections); reload jank is mitigated by browser scroll restoration; converging on the SPA model is structural churn past the maintenance gate |
| P-14 | nav grouping / command palette | REJECTED — #45 already rejected the six-domain rewrite + palette with reasons; nothing new |
| P-15 | mobile breakpoints for server pages | REJECTED — Windows 11 desktop is the platform (CLAUDE.md); flex-wrap + tablewrap already degrade |
| P-16 | light theme | REJECTED — dark-only is the recorded visual identity (board §11); no owner ask |

## Verification at close

- 6 new pins in tests/test_ux_polish.py (78 total): the [hidden]/.hidden
  owner + showStage class clear, color-scheme + reduced-motion, one toast
  voice, exports bulk (stale-only, priced kinds excluded, sequential,
  full-success reload) + its absent-state, pollJob adaptive/honest across
  all four job pages. Red-first proven by stash (5 red; the absent-state
  pin green-by-design as a no-regression pin).
- Real-browser harness grew 17→21 checks (the four /create liveness checks);
  21/21 passed. All 17 pages re-screenshotted after the wave: consoles clean.
- Full suite green (see DECISIONS #49 for the count) including the board
  gates (test_c21g_gates, test_board_serve) over the board's added
  `color-scheme`/`[hidden]` lines.
- Delegation note: the three mechanical pollJob mirrors were executed by a
  cheaper model against a byte-exact reference implementation and reviewed
  line-by-line here; everything judgment-bearing (audit, fixes, tests,
  docs) stayed with the session.
