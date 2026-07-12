# FP Loop X1 — professional review TRANSPORT on the serve-mode compare board

Roadmap item 6 polish (pro review transport grammar). Serve-mode only; the
static board stays byte-for-byte identical. This loop EXTENDS the ONE delegated
`keydown` listener that T1 shipped Space-only — it never adds a second listener
and never rebuilds the compare surface.

## What shipped (serve-only, `src/manju/board/board.py`)

The pro review transport, active ONLY when the keydown event target sits inside
an **open** `.compare-wrap` and NEVER when the target is a form field:

| Key            | Action                                                                 |
|----------------|------------------------------------------------------------------------|
| `K`            | pause the mode's active compare videos                                  |
| `L`            | play; each press cycles the **native** `playbackRate` 1→2→4→1, labelled |
| `J`            | **honest** single −1-frame step-back (see ruling below)                 |
| `←` / `→`      | ±1 frame via the EXISTING exact den/num period (shared helper)          |
| `Shift`+`←`/`→`| ±1 second (exact)                                                        |

Plus a bilingual discoverability **hint line** and a live **transport label**
(`data-transport`) in the compare head, and a serve-only CSS rule for the label.

## (a) The J ruling as implemented + exact label text

Browsers cannot play a `<video>` backwards natively. Rather than fake a smooth
reverse with an interval/timeout shuttle (dishonest — it would drop frames and
lie about direction), **J performs a single −1-frame step per press**, using the
SAME exact frame period as `ArrowLeft` (`framePeriod(wrap)` → `cmpSeek(wrap,
-per)`), and the transport label states the limitation verbatim:

```
J: 逐帧回退 step-back (浏览器不支持倒放 no native reverse)
```

No `setInterval`/`setTimeout` exists anywhere in the served JS (pinned absent).
The honesty phrase "no native reverse" appears in BOTH the runtime label and the
discoverable hint line (count ≥ 2 pinned).

## (b) Extend-not-rebuild proof (vs T1 / V2 markers)

- **ONE keydown listener.** `_SERVE_JS.count('addEventListener("keydown"') == 1`
  (pinned). The pre-existing Space→focused-`<video>` PlayPause branch is
  preserved verbatim (`e.code === "Space"`, `a.tagName === "VIDEO"`); the
  transport is dispatched from the same listener via `cmpTransport(e, wrap)`.
- **Shared stepping math, not duplicated.** T1's `frameStep(btn)` was refactored
  to delegate to two new tiny helpers — `framePeriod(wrap)` (the exact `den/num`
  read, now living once) and `cmpSeek(wrap, dt)` (the pause+seek+redraw path).
  The `±1-frame BUTTONS` and the keyboard `←`/`→`/`J` all call the SAME
  `framePeriod`; the old inline `dir * den / num` duplication is gone (pinned
  absent).
- **V2's `seeked` hook reused, not rewired.** `addEventListener("seeked",
  scopesFromVideoEvent, true)` is untouched; `cmpSeek` ends by calling
  `drawScopes(wrap)` exactly as T1/V2's stepping did, so parked-frame scopes
  follow every transport step through the existing redraw path.
- **All T1 pins (15) and V2 pins (13) stay green untouched** — the mode toggles
  (`sbs`/`wipe`/`diff`/`onion`), `data-framestep`, `data-fps-num/den`,
  `compare-wrap`, `data-syncplay`, `onionMove`, `data-scopes`, `drawScopes`, the
  Rec.709 coefficients and the scopes honesty label are all still present.

## (c) Files

- `src/manju/board/board.py` — serve-only: `framePeriod`/`cmpSeek` extraction +
  `cmpRate`/`setTransport`/`cmpTransport` + extended keydown listener
  (`_SERVE_JS`); the transport hint line + `data-transport` label
  (`_render_compare`); the `.cmp-transport` CSS (`_SERVE_CSS`). (+100 / −11)
- `tests/test_fp_board_transport.py` — NEW, 15 source-level pins on the served
  JS + hint/label + guards + `node --check`.
- `REPORTS/FP_BOARD_TRANSPORT.md` — this file.

Parallel-loop-owned files (`exporters/fcpxml*.py`, `exporters/conform.py`,
`cli.py`) were NEVER touched by this loop.

## (d) Red → green counts

- **My suite** (`test_fp_board_transport.py`): RED **10 failed / 5 passed** →
  GREEN **15 passed**.
- **Union batch** (my suite + `test_fp_board_compare` + `test_fp_board_compare2`
  + `test_board_serve` + `test_c21g_gates` + `test_fp_ratemig1`): baseline
  **90 passed / 1 skipped** (my file absent) → **105 passed / 1 skipped** (my 15
  added, zero regressions). The 1 skip is the ffmpeg-gated boundary e2e.
- `node --check` on the served IIFE: clean parse.

## Static-board byte-identity

`render_board(serve=False)` is byte-for-byte identical HEAD vs working tree —
sha256 `5091a0f0e9cb194017854f5abe8f5365464a8c4e1bea6a7174426620f94daee0`
(7172 bytes), proven by a git-stash A/B render. All edits live in serve-only
regions (`_SERVE_JS`, `_SERVE_CSS`, and the `serve`-gated `_render_compare`);
`_CSS`, `_JS`, and the `serve` gate are untouched.

## Discipline

- No `edit_rate` token in `board/` (ratemig R1 grep pin holds — the stepper reads
  the derived `data-fps-num`/`data-fps-den` echo, never the raw field).
- No c21g-forbidden specialized-view marker introduced (vectorscope /
  waveform-monitor / beat-grid / color-scope / … all still absent).
- No new dependency, no network, no server route, no schema, no CLI change, no
  git ops. Targeted `python -m pytest` only.

## (e) Deviations

None material. Notes:
- Rate-cycle semantics: the first `L` plays at ×1 (normal), and "L again" cycles
  ×2 → ×4 → ×1 — matching "L = play; L again cycles 1→2→4→1". Rate state is held
  on the wrap element (`wrap._mjRate`, mirroring the existing `wrap._mjRaf`
  idiom) rather than a DOM attribute, so no new `data-*` attribute is added.
- Transport scoping uses the keydown **event target** (`e.target.closest(
  ".compare-wrap.open")`) exactly as the addendum's ruling specifies, so the keys
  act on the wrap the reviewer is focused inside and never cross-fire between
  shots; a `<video>`/button inside the open wrap must hold focus (documented in
  the hint line).
