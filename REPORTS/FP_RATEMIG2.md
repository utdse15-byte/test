# FP Rational Edit Rate — Stage 2 (R2): the opt-in rational build spine

**Deliverable:** the OPT-IN rational path made real through **compiler → render →
cache keys**, with **INT-PROJECT BYTE-IDENTITY** (timelines, keys, ffmpeg command
lines) as the loop's product. Builds on R1 (`ProjectConfig.edit_rate` +
`Project.edit_rate` → `timebase.Rate`), which stays intact (d03fbba is an
ancestor of HEAD; `test_fp_ratemig1.py` byte-identity pins unchanged).

**Files touched (only these):** `timeline/compiler.py`, `core/models.py`,
`media/render.py`, `build/graph.py`, `tests/test_fp_ratemig1.py` (grep-pin
update — see §G), `tests/test_fp_ratemig2.py` (new), this report.
`media/ffmpeg.py` **untouched** (`-r` does not live there — see §A);
`media/normalize.py`, `core/container.py`, `cli.py`, `exporters/` **untouched**.

**Encapsulation discipline:** the R2 build spine reads the exact rate through the
typed resolvers `ProjectConfig.frame_rate` / `Timeline.frame_rate` (→ `Rate`) and
**never** names the raw `edit_rate` token — so the field stays encapsulated in
`core/models` + `core/container`, exactly as R1's surface pin requires. The token
appears in `src/manju/` only in `core/models.py`, `core/container.py`, and the
sanctioned exporters surface (`exporters/edl.py`, committed by the parallel EDL
loop).

---

## A. `-r` / fps injection points (the audit)

The engine never used a bare `-r` in the render path — it forced the grid with
the **`fps=` video filter**. R2 feeds the native rational token (`24000/1001`) at
every point the frame rate enters ffmpeg, and adds an explicit `-r` on the final
encode. `str(Rate)` yields the canonical `num/den`, which ffmpeg's `fps=` filter,
`-framerate` and `-r` all accept.

| # | Site | File · function | Pre-R2 | R2 rational | R2 int |
|---|---|---|---|---|---|
| 1 | segment normalize `fps=` (vf) | `media/normalize.py::_video_filter` (via the `fps=` arg render passes) | `fps={fps}` | `fps=24000/1001` | `fps=24` (identical) |
| 2 | still-image input rate | `media/normalize.py` `-framerate str(fps)` (via the arg render passes) | `-framerate 24` | `-framerate 24000/1001` | `-framerate 24` (identical) |
| 3 | xfade boundary `fps=` (vf) | `media/render.py::_build_boundary_segment` | `fps={fps}` | `fps=24000/1001` | `fps=24` (identical) |
| 4 | handle-trim `fps=` (vf) | `media/render.py::_trim_segment` | `fps={fps}` | `fps=24000/1001` | `fps=24` (identical) |
| 5 | final composition `fps=` node | `media/render.py::render_timeline` (both the image-overlay and plain branch) | `fps={fps}` | `fps=24000/1001` | `fps=24` (identical) |
| 6 | **final encode `-r`** (NEW) | `media/render.py::render_timeline` | *(none)* | `-r 24000/1001` | *(none — byte-identical)* |

`normalize.py` is off-limits; R2 reaches sites 1–2 by threading the ffmpeg
frame-rate **token** (`rate_arg`) through the `fps=` argument render already
passes to `normalize_segment` (which only ever string-interpolates it — no int
math), so no edit to `normalize.py` is needed. Int projects pass the int `fps`
there exactly as before → byte-identical command lines.

`media/ffmpeg.py` carries **no** `-r`/fps (verified). The only `-r` elsewhere is
`media/repair_ops.py` (a repair surface, out of R2 scope).

**Cache-key fps sites** (fps as a KEY input, not an ffmpeg arg — kept int nominal;
rational adds a distinct component, see §B/§C): `_segment_cache_key`,
`_boundary_cache_key`, `_final_key_payload` (`media/render.py`); the animatic
preview key (`build/graph.py::_render_animatic`).

## B. Byte-identity evidence — int project (the loop's product)

| Surface | How proven | Pin (`test_fp_ratemig2.py`) |
|---|---|---|
| compiled timeline JSON | no `edit_rate`/`rate_echo`/`duration_frames`; durations = historical snap (S001 4000, S002 2500); deterministic | `test_int_compile_is_byte_identical_no_rational_keys` |
| `snap_to_frame_grid` / `_resolve_duration_ms` | FIX-B literals verbatim (1208/2500/42/1000; 1208/10000) — the int path is a SIBLING, never a refactor | `test_int_snap_and_resolve_are_untouched` |
| `_segment_cache_key` | `== cache_key(hash, w, h, 24, dur, "final", 0, 0)` (rate component absent) | `test_int_segment_cache_key_is_byte_identical` |
| rational walker never entered | spy on `_RationalFrameGrid.__init__` during an int compile → 0 constructions | `test_rational_walker_never_entered_for_int_project` |
| `final_content_key` | int deterministic; stray hand-edited `duration_frames` on an int timeline is IGNORED (identical key) | `test_final_content_key_*`, `test_stray_duration_frames_on_int_timeline_is_ignored_in_key` |
| built ffmpeg command lines | real render: **no `-r`, no `/1001`**, `fps=24` present; output `r_frame_rate` whole-number | `test_int_ffmpeg_command_lines_carry_no_rational_rate`, `test_int_render_output_is_unchanged_whole_number_rate` |
| compat corpus `timeline.json` | loads with no `rate_echo`/`duration_frames`; bytes unchanged | `test_compat_timeline_fixture_still_loads_without_a_rate_echo` + `test_fp_compat` (11 green) |

The whole existing render/compile suite (compiler/idempotency/round_t/
transitions/virtual_trim/media_durability/final_trust — 168 tests) is the pre-R2
**golden**: all stay green, so int segment keys, content keys, concat and final
encodes are unchanged.

## C. Cumulative-boundary snapping + drift pins (the numbers)

Design: walk the clip sequence tracking the EXACT rational boundary
(`cum_frames × 1000·den/num` ms, an exact `Fraction` inside `timebase`); each
clip's emitted ms = `round(exact_end) − round(exact_start)`. The emitted
durations are differences of rounded boundaries, so their running sum telescopes
to `round(exact_total)` at every prefix — cumulative error **≤ ½ ms forever**.

`duration_frames` (auto path, default clamp [1200, 10000] ms → [29, 240] frames)
at 24000/1001, hand-computed and pinned:

| raw ms | 100 | 500 | 1200 | 2000 | 2600 | 4000 | 5005 | 10000 | 20000 | explicit 2.5 s |
|---|---|---|---|---|---|---|---|---|---|---|
| frames | 29 | 29 | 29 | 48 | 62 | 96 | 120 | 240 | 240 | 60 |

**Cumulative-drift pins** (`test_rational_two_hour_drift_bounded_vs_per_clip_independent_contrast`):
2 h of 23.976 clips = **N=14401 clips × 12 frames** (~0.5 s each; the pathological
½-ms-per-clip case), total 172812 frames, true end = 7 207 700.5 ms.

| method | end error vs exact | in frames (1 frame = 1001/24 ≈ 41.708 ms) |
|---|---|---|
| **cumulative-boundary (compiler)** | **+0.5 ms** (worst prefix also ≤ ½ ms) | 0.012 frame |
| per-clip independent rounding (naive) | **+7200.5 ms** | **≈ 172.6 frames** |

The contrast is the reason the walker exists. A 400-clip varied sequence pins the
≤ ½ ms bound at every prefix (`test_snap_to_frame_grid_rational_cumulative_error_within_half_ms_every_prefix`),
and the compiled `total_ms` telescopes to `frames_to_ms(Σ frames)`
(`test_rational_timeline_total_telescopes_from_frame_boundaries`).

## D. Clamp-in-frame-space rules

The same duration-resolution rules as the int path, resolved in FRAME space
(`_resolve_duration_frames`, the twin of `_resolve_duration_ms`):

- **explicit** numeric duration wins, UNCLAMPED: `frames = max(1, ms_to_frames(round(dur·1000), rate))`.
- **auto** (audio-drives-picture): `raw = voice+pad_before+pad_after` else `take_dur` else `default_shot_ms`; then clamp **in frames**:
  `frames = max(1, max(ms_to_frames(min_shot_ms, rate), min(ms_to_frames(max_shot_ms, rate), ms_to_frames(raw, rate))))`.
- clamp bounds are the frame images of the ms bounds: `ms_to_frames(1200)=29`, `ms_to_frames(10000)=240` (ROUND_HALF_UP).

Edge pins: raw 100/1200 → 29 (min), raw 10000/50000 → 240 (max)
(`test_rational_min_max_clamp_is_in_frame_space`). Int clamp semantics are
unchanged (delegates to `_resolve_duration_ms`).

## E. Model shape (additive; `core/models.py`)

- `VideoClip.duration_frames: int | None = None` — the exact whole-frame count;
  written ONLY on the rational path; drop-None serializer (wave-4b precedent) →
  int clips byte-identical.
- `Timeline.rate_echo: EditRate | None` (validation alias `edit_rate`) + a wrap
  serializer that emits it under the exporter-facing key `edit_rate` and drops it
  when None → int timelines byte-identical; a rational timeline round-trips its
  `{num, den}`. `Timeline.fps` stays the int hand-editable mirror.
- Resolvers: `ProjectConfig.frame_rate` / `Timeline.frame_rate` → exact `Rate`
  (rational field when present, else promoted int fps). These are what the R2
  spine reads (never the raw field), and `Timeline.edit_rate` (read-only property)
  gives the exporters surface the echo without re-loading project.yaml.

Dispatch: `compile_timeline` asks `config.frame_rate`; `rate.exact_int is not
None` (every int project) → the untouched `snap_to_frame_grid`/`_resolve_duration_ms`
path; else the `_RationalFrameGrid` cumulative-boundary walker. The fingerprint
folds the exact rate ONLY when rational (drop-when-int), so an int compile keeps
its `compiled_from` hash while two projects differing only in edit rate never
collide.

## F. Targeted test counts (no full suite)

New: **`tests/test_fp_ratemig2.py` — 17 passed** (2 real-render ffmpeg pins + 1
e2e smoke run: rational output `r_frame_rate == 24000/1001`; ffmpeg present so
none skipped).

Guardian set — **214 passed, 0 failed**:

| suite | count | role |
|---|---|---|
| `test_fp_ratemig2` | 17 | R2 (new) |
| `test_fp_ratemig1` | 22 | R1 byte-identity + updated grep pin (§G) |
| `test_compiler` | 16 | **FIX-B** snap/duration pins |
| `test_fp_compat` | 11 | **compat corpus** (timeline/project hash-identity) |
| `test_fp_contracts` | 17 | `planned_migrations` still `implemented:false` (R2 is opt-in, not the migration — that's R5) |
| `test_container` | 36 | R1 accessor + config IO |
| `test_check` | 30 | config/timeline validation |
| `test_idempotency`, `test_round_t`, `test_transitions_looks`, `test_virtual_trim`, `test_media_durability`, `test_final_trust_round` | 65 | render/keys/xfade byte-identity golden |

## G. Deviations

1. **Grep pin updated (`test_fp_ratemig1.py::test_edit_rate_only_referenced_in_models_and_container`).**
   R1's pin asserted the `edit_rate` token lives in EXACTLY `core/container.py` +
   `core/models.py`, and its own docstring scoped it to fail "the moment a
   consumer starts plumbing it in ahead of its staged loop **(R2+)**." That loop
   has landed. Critically, the pin is **already red at committed HEAD** independent
   of R2: the parallel EDL loop committed `exporters/edl.py` (which reads the
   `edit_rate` echo) without updating the pin. I cannot touch `exporters/`, so the
   only way to green the suite is to update the pin. The update is **teeth-
   preserving, not a weakening**: it still requires the R1 surface to declare the
   field, permits ONLY the sanctioned R2+ consumers (`exporters/` + `cli.py`), and
   **fails on any leak into the R2 build spine** (`timeline/compiler.py`,
   `media/render.py`, `build/graph.py`, `media/normalize.py`, `media/ffmpeg.py`)
   or any other engine area (gui/qc/providers/board/runtime). My spine is
   verified token-free, so this update actually adds a guarantee. Every other pin
   in `test_fp_ratemig1.py` (the byte-identity pins) is **untouched and green**.
2. **`normalize.py` reached via its argument, not edited.** It is off-limits and
   its `fps=` / `-framerate` string-interpolate whatever token render passes, so
   the rational rate flows through with no edit (the int path passes the int fps
   → byte-identical).
3. **`duration_frames` "structured check advisory" (addendum §2) landed as a
   render-time structured advisory + key-strip, not a `manju check`/qc surface** —
   `core/check.py` and `qc/` are outside R2's file set. In scope, R2 guarantees
   the stray field is IGNORED (stripped from the int content key, `test_stray_
   duration_frames_on_int_timeline_is_ignored_in_key`) and never silent
   (`render_timeline` logs a structured advisory naming the clips). Wiring it into
   `manju check` output is left to a follow-up (check/qc surface).
4. **Preview/masters surfaces:** the animatic (`build/graph.py::_render_animatic`)
   is a de-scoped preview; its OUTER key already differs for a rational project
   via the timeline's `edit_rate` echo (folded through `tl.model_dump()`), and its
   inner per-still key gains a drop-when-int rate component for consistency. Its
   clips still ENCODE at the int nominal fps (kenburns/slate untouched) — a
   rational animatic preview is nominal-rate, noted here per the addendum.
5. **`CONTRACTS.yaml` untouched.** R2 is the opt-in spine, not the completed
   migration; `planned_migrations[0].implemented` stays `false` until R5, and
   `test_fp_contracts` stays green.
