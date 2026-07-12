# FP_BOARD_COMPARE — professional compare on the EXISTING Board (Loop T1)

User item 6: wipe, difference, frame-lock stepping, and the accepted-ending-
vs-next-start boundary view — all as READ-ONLY extensions of the serve-mode
board that already existed. Red-first: `tests/test_fp_board_compare.py` was
written and run RED (10 discriminating failures; 5 guard tests intentionally
green pre-change) before `board.py`/`server.py` were touched.

## What existed vs what was added (extend, never rebuild)

Existed (untouched in shape, every legacy marker still pinned green):

- the serve-mode compare surface — `.compare-wrap` / `.compare-grid` /
  `.cmp-cell` synced side-by-side grid, `data-syncplay`, `data-compare`
  toggle, per-take metadata + select (board.py, the `_render_compare` grid
  body and the `/* --- compare --- */` CSS block);
- the frames machinery — `media/frames.extract_frame` with its
  content-addressed `.manju/frames` cache (§3 disposable; `manju gc` wipes
  it); NO new extractor was added;
- the static board — byte-for-byte identical (its pin
  `test_static_board_is_byte_identical` still green; re-pinned here with the
  new markers asserted ABSENT).

Added, serve-mode only, one coherent CSS/JS region each
(`board.py` CSS :271/:308, JS :413–:432ff):

1. **Mode toggle** on the existing wrap: 并排 side-by-side (the existing
   grid) | 擦除 wipe | 差异 difference. Wipe = CSS `clip-path: inset()` on a
   stacked A/B video pair driven by a range slider. Difference = a `<canvas>`
   compositing `globalCompositeOperation="difference"` per animation frame,
   with a brightness-gain slider and the ALWAYS-honest label
   "差异已放大 amplified ×N — 非原始像素差 not raw pixel deltas" (a browser
   without canvas filters degrades to "gain unavailable — raw difference
   (×1)", stated, never silent). All pixel work is client-side; the canvas is
   a view, never a fact source.
2. **Frame-lock stepping**: pause-synced ±1-frame buttons stepping every
   active compare video by the EXACT frame period `den/num` seconds, carried
   as `data-fps-num`/`data-fps-den` on the wrap. Rational-aware: the rate is
   resolved once per render by `_rate_info` — the compiled timeline's
   `Timeline.frame_rate` (which honours the R2 rational echo
   `edit_rate:{num,den}`) when a timeline exists, else the project's declared
   rate via the single `Project.edit_rate()` accessor. Display honesty: the
   label spells the exact fraction ("1帧 frame = 1001/24000 s ≈ 41.708 ms @
   24000/1001 fps (exact rational)"); an unresolvable rate disables stepping
   with a labelled reason instead of guessing.
3. **Duration honesty**: when both A/B takes carry sidecar probe durations
   (an EXISTING fact the board already renders) that differ by more than one
   frame period, a server-rendered fact label (`data-durfact`) names both
   durations; a client-side check (`data-durwarn`) covers unprobed takes once
   metadata loads. Never silent.
4. **Boundary view** (`_render_boundary`, board.py :1225): for every adjacent
   pair in index order, shot N's LAST frame vs shot N+1's FIRST frame — from
   the shots' SELECTED takes — side by side with a per-pair client-side
   difference toggle (same canvas + honest amplified label). Stills are
   extracted server-side at page build through the EXISTING
   `extract_frame` (width 480); "last" uses a past-EOF sentinel that the
   round-W #36 clamp resolves to the real last frame with exactly one cache
   entry. Missing selection / missing media / failed extraction each render
   an honest `bnd-missing` slot naming the reason; the whole section is
   failure-contained (`_boundary_section`, same stance as `_safe_panel`).

## Derivation path + cache location (boundary stills)

`shot index order → status.selected_take → take media → media/frames.
extract_frame(project, rel, at_ms, width=480)` → JPEG under
`<root>/.manju/frames/<contenthash-key>.jpg` (the pre-existing
content-addressed cache — deletable at any time, rebuilt on the next request,
NEVER a build input) → served as `/media/.manju/frames/...`.

The ONE server change (`board/server.py :445`): `.manju/frames/` joins
`_MEDIA_PREFIXES`, mirroring the existing `.manju/thumbs/` entry and the
GUI's `.manju/webpreview/` precedent. The rest of `.manju/`
(state.sqlite, locks, events) stays 403 — re-pinned by test.

## Tests (`tests/test_fp_board_compare.py`, 15 — red-first)

- mode toggles + wipe slider + diff canvas + honest amplified label present;
- extend-not-rebuild pin: every pre-existing compare marker still present;
- wipe/diff honestly unavailable without two media takes (labelled note);
- int project: `data-fps-num="<fps>" data-fps-den="1"` + `1/<fps> s` label;
- rational echo: `data-fps-num="24000" data-fps-den="1001"` + `1001/24000 s`;
- duration mismatch fact label present (1.000 vs 2.500) / absent when equal;
- boundary rows per adjacent pair with honest failure slots on fake media;
- honest slots: no selected take · no media on disk · extraction failed;
- no pairs (single shot) ⇒ no section;
- ffmpeg e2e: real 3-shot sample → stills on disk in `.manju/frames`,
  `<img src="/media/.manju/frames/...">` refs, served 200 `image/*`
  (endpoint smoke, existing serve-test pattern);
- media route: frames cache served, truth files (project.yaml, shot YAML,
  events.jsonl, state.sqlite) still 403;
- static board byte-identical and carrying NONE of the new markers;
- read-only proof: rendering the serve board changes NO byte outside
  `.manju/` (tree-hash comparison) — zero new fact sources.

Targeted runs, all green: new suite 15/15; `test_board_serve` +
`test_c16_board` + `test_board_round_e` + `test_write_locks` 50/50;
`test_c21g_gates` + `test_boards` + `test_storyboard` 58 passed / 1 skipped
(pre-existing opt-in benchmark skip).

## Discipline

Files touched: `src/manju/board/board.py`, `src/manju/board/server.py` (the
one allowlist entry), `tests/test_fp_board_compare.py`, this report. No new
dependencies (stdlib + existing), no CLI/snapshot change, no schema id, no
CONTRACTS/DECISIONS/README edits, no existing test deleted or weakened.
Annotation WRITE flows untouched (read-only visual additions only).
