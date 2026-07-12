# FP_BOARD_COMPARE2 — onion skin + scopes on the EXISTING compare board (Loop V2)

User item 6 named six surfaces. Loop T1 shipped the first four (A/B sync, wipe,
difference, frame-lock) plus the boundary view. This loop lands the item's last
two words — **onion skin** and **scopes** — as CLIENT-SIDE, VIEW-ONLY extensions
of the SAME serve-mode compare surface T1 shipped, never a rebuild. Red-first:
`tests/test_fp_board_compare2.py` was written and run RED (7 discriminating
failures; 5 guard tests green pre-change) before `board.py` was touched.

## What existed vs what was added (extend, never rebuild)

Existed (untouched in shape, every T1 marker still pinned green):

- the `.compare-wrap[data-mode]` wrap with T1's three modes (`sbs`/`wipe`/
  `diff`), the `.cmp-ab` A/B stack (`.ab-a`/`.ab-b`/`.ab-canvas`), the wipe
  slider, the difference canvas + amplified-gain label, and the ±1-frame
  stepper (`setCmpMode`/`frameStep`/`wipeMove`/`gainMove`/`diffDraw`/
  `startDiffLoop` and the delegated click/input/`loadedmetadata` listeners);
- the `.bnd-row` boundary rows (two `.bnd-img` stills + `.bnd-canvas` diff);
- the frames cache (`.manju/frames`) and its server allowlist entry — reused,
  NOT re-added;
- the static board — byte-for-byte identical (its pins in `test_board_serve`
  and T1's `test_static_board_carries_none_of_it` stay green; re-pinned here
  with the V2 markers asserted ABSENT).

Added, serve-mode only, one coherent CSS/JS region continuing T1's:

1. **Onion skin — a fourth A/B mode** (`data-cmpmode="onion"`): B rendered over
   A at CSS `opacity` on the SAME `.ab-b` element (slider `data-onion`, 0-100%,
   default 50%), no canvas for the video pair. The wipe (`clip-path`) and onion
   (`opacity`) inline overrides are each cleared by `setCmpMode` on every mode
   switch, so a mode always starts from its own CSS baseline (and a stale onion
   opacity can never leak into diff mode's `opacity:0` rule — inline beats the
   sheet). `activeCmpVideos`/`frameStep`/`syncPlay` already act on the A/B pair
   in any non-`sbs` mode, so onion inherits sync + stepping for free.
2. **Onion skin — per boundary row** (`data-bndonion` toggle +
   `data-bndonion-op` slider): the next shot's FIRST still stacked over this
   shot's LAST still at CSS opacity ("does the cut register?"). A pure CSS
   overlay (`.bnd-onion`) built from the SAME cached stills, with distinct
   classes so the existing diff canvas is unaffected — both toggle on the same
   row without conflict. Only emitted when both stills resolve (same gate as the
   diff tools); a failed extraction renders T1's honest slots with no onion.
3. **Scopes panel** (`data-scopes` toggle, collapsed by default): from video
   A's CURRENT PARKED frame, sampled client-side to an offscreen canvas, it
   draws (a) a **Rec.709 luma histogram** and (b) a **per-column luma
   waveform**. Luma is `Y = 0.2126·R + 0.7152·G + 0.0722·B` in BOTH the label
   and the drawing code (`rec709()`), never a naive average. **Redraw is
   event-driven only** — `pause`, `seeked` (the frame-step lands here),
   slider `input`, and panel-open — there is NO rAF loop for scopes (contrast
   the diff view's `startDiffLoop`); scopes are for parked frames. Degradations
   are labelled, never silent: no A/B stack → no panel; no decoded frame yet →
   "park a frame first"; canvas 2D or `getImageData` unavailable/blocked → a
   note in the panel's `data-scope-note` slot.

### The permanent scopes honesty label (BINDING)

Rendered verbatim from `_SCOPES_LABEL` at the top of the panel:

> 示波器 scopes:读数取自浏览器解码的 RGB（browser-decoded RGB），来自视频 A 的当前暂停帧（current paused frame of video A only）—— 仅供查看 view-only。亮度用 Rec.709 luma：Y = 0.2126·R + 0.7152·G + 0.0722·B。这不是引擎的颜色事实（not the engine's color facts）：QC colorstats remain the measurement authority。仅在暂停 / 逐帧步进 / 滑块时重绘，播放时绝不连续刷新（redrawn on pause / frame-step / slider only, never looped while playing — scopes are for parked frames）。

It states, in one breath: browser-decoded RGB · current paused frame of **video
A only** · **view-only** · the exact Rec.709 formula · NOT the engine's color
facts, **QC colorstats remain the measurement authority** · redraw only for
**parked frames** (never a play-time loop). The canvas is a VIEW, never a fact
source; nothing is written anywhere.

## No server change

Scopes read the already-served A/B `/media/<take>` videos; boundary-onion
stills reuse the already-allowlisted `/media/.manju/frames/...` stills (T1's
one server entry). No new asset class, no new route — `board/server.py` is
untouched, as the addendum predicted.

## Read-only / no new facts

Onion is pure CSS opacity; scopes are a client-side canvas view. Neither writes
anything. `test_onion_and_scopes_write_nothing_outside_runtime` re-pins T1's
tree-hash proof: rendering the serve board changes NO byte outside `.manju/`.

## Tests (`tests/test_fp_board_compare2.py`, 13 — red-first)

- onion is a fourth A/B mode with an opacity slider + CSS baseline + handler;
- extend-not-rebuild pin: every T1 A/B marker still present next to onion;
- onion honestly unavailable without two media takes (T1's note reused);
- boundary row gains onion toggle + slider + a stacked stage (diff canvas
  still present, untouched) when both stills resolve; absent on failed
  extraction (honest slots remain);
- scopes panel with BOTH canvases + toggle; the exact honesty label (all seven
  bound phrases); Rec.709 coefficients present in the drawing code too;
  collapsed by default; absent without the A/B stack;
- static board byte-identical and carrying NONE of the V2 markers (extends
  T1's pin);
- read-only proof: serve render writes nothing outside `.manju/`;
- serve smoke: a live BoardServer serves onion + scopes + the honesty label
  over HTTP (the existing serve-test pattern; no new server route).

Targeted runs, all green: V2 suite 13/13; T1's `test_fp_board_compare` 15/15
(untouched); `test_board_serve` 33/33 (static byte-identical pin); the
edit-rate grep pin `test_fp_ratemig1` 22/22 (board/ never introduces the
`edit_rate` token); board-adjacent `test_c16_board`+`test_boards`+
`test_storyboard`+`test_board_round_e` 62/62.

## Discipline

Files touched: `src/manju/board/board.py` (serve-mode CSS/JS + serve render
functions only — static `_CSS`/`_JS` and both entrypoints untouched),
`tests/test_fp_board_compare2.py` (new), this report. No `board/server.py`
change, no new dependency, no CLI/snapshot/schema change, no
CONTRACTS/DECISIONS/README edit. T1's tests were neither edited nor weakened.
The `exporters/` package (parallel loop V1) was never touched. The `edit_rate`
token was never introduced into `board/` (the standing R1 grep pin holds).
