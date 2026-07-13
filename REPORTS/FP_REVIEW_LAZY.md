# FP Review Lazy — audit finding G1: the /review render is cheap again

**Deliverable:** the `GET /review` render drops from ~16.6s / 474 subprocess
spawns (at 40 shots, measured by OPT-GUI) to **~0 subprocess spawns on the
request thread**, plus a `_member_frame` probe fix that removes a per-member
`ffprobe` the content-addressed frame cache had already made moot.
**Files touched (this loop owns qc/ + gui/ only):** `qc/agent_review.py`,
`gui/pages.py`, `gui/server.py`, `tests/test_fp_review_lazy.py` (new),
`REPORTS/FP_REVIEW_LAZY.md`.
**Boundary honoured:** no change to `timeline/`, `providers/`, `core/events.py`,
`core/hashing.py`, `cli.py`, or any `tests/` chdir site. Extracted frames stay
**BYTE-IDENTICAL**; existing GUI + consistency tests stay green.

---

## The defect (OPT-GUI G1)

`pages.render_review` → `_consistency_section` (gui/pages.py) ran
`qc_brief(mode="consistency")` INLINE on the HTTP request thread. That call
composes one contact-sheet board per comparison unit — `_member_frame`
(qc/agent_review.py) subprocess-probes each member, then `extract_frame` +
`make_board` spawn again — so a 40-shot project paid 316 `ffprobe` + 158
`ffmpeg` spawns before the page could paint. Worse, `_member_frame` re-probed a
midpoint the take's sidecar probe already carried (the SAME redundant-probe
pattern Tier-1 #2 fixes for voice in the compiler).

## Two halves

### 1. `_member_frame` reads the sidecar probe first (byte-identical)

New leaf `_member_duration_ms(take)` mirrors the video compiler's read at
`timeline/compiler.py:898`:

```python
probe_info = take.sidecar.probe if take.sidecar is not None else None
if probe_info is not None and probe_info.duration_ms is not None:
    return int(probe_info.duration_ms or 0)          # SIDECAR first
try:
    from ..media.probe import probe as _probe
    return int(_probe(take.media_path).duration_ms or 0)   # live fallback only
except Exception:
    return 0
```

The midpoint arithmetic (`at_ms = dur // 2`) is **unchanged**, so a
cached-duration path and a live-probe path that see the same duration request
the byte-identical frame timestamp to `extract_frame` — the extracted JPEG is
the same bytes. Generated takes fill `sidecar.probe.duration_ms` at synthesis
and takes are append-only, so the cache can never go stale.

### 2. The consistency section is lazy (structure inline, boards fetched)

`qc_brief` / `_qc_brief_consistency` gained a keyword-only `compose_boards: bool
= True` (default keeps every CLI/MCP caller byte-identical). `_consistency_section`
now calls `qc_brief(mode="consistency", compose_boards=False)` — the unit
STRUCTURE (members, criteria, coverage chip, verdict FORM) renders inline and
subprocess-free, exactly the house pattern the alt-take previews above it
already use ("the review page's initial GET never triggers ffmpeg"). Each
unit's board is a placeholder `<div class="cs-board-slot">`; `/pages.js`
`initReviewConsistencyBoards()` fetches the composed boards once, after first
paint, from a NEW endpoint and fills each slot by DOM-building (`createElement`
+ `textContent` + `img.src` — never `innerHTML`). A fetch failure leaves a
labelled note in every slot — never a blank.

**New endpoint** `POST /api/review/consistency` (`_act_review_consistency`),
registered in `_pages_post` right next to `/api/qc/verdict` and mirroring its
idiom exactly: token-gated by `do_POST` (403 without `X-Manju-Token`), JSON
envelope via `_send_json` (`{ok, units:[{unit, kind, label, image}]}`), same
synchronous read threading as `_route_explain`. It is a pure read (composes only
the content-addressed `.manju/frames` board cache, writes no truth file) so it
joins `/api/validate` + `/api/impact` in `do_POST`'s readonly allow-list — the
boards still load on a readonly workbench, as they did when the section rendered
inline before G1.

## Measured (8-shot scratch project, real ffmpeg, cold cache)

```
[zero-spawn] render_review spawns              = 0   (hard-forbidding spy)
[zero-spawn] consistency units rendered inline = 10
[timing]     NEW render_review (lazy boards)   =   231.0 ms
[timing]     board compose (moved off render)  =  3178.6 ms, 160 spawns
[timing]     OLD render_review (≈ new+boards)  =  3409.5 ms
[probe-win]  board compose spawns WITHOUT probe = 224   (probe-less sidecars)
[probe-win]  board compose spawns WITH   probe = 160   (sidecar.probe first)
[probe-win]  ffprobe spawns eliminated          = 64 across 8 shots
```

`render_review` is **~15× faster and spawns zero subprocesses**; the board
work is moved off the request thread onto an on-demand fetch, and half 1 strips
64 redundant `ffprobe` spawns from that fetch (one per member frame).

## Tests — `tests/test_fp_review_lazy.py` (9, red-first, no ffmpeg needed)

- `test_render_review_spawns_zero_subprocesses` — `subprocess.run`/`Popen` made
  hard failures after the project is built; `render_review` spawns ZERO.
- `test_consistency_section_structure_renders_inline_without_boards` — the
  `data-unit` cards, verdict form, coverage chip still render inline; the board
  is a `cs-board-slot`, no composed `<img class="cs-board" src="/media/…">`.
- `test_consistency_section_empty_state_still_inline` — the honest empty-state
  line still renders on a single-shot project.
- `test_consistency_endpoint_returns_units_and_token_gated` +
  `_wrong_token_is_403` + `_readonly_allowed` — the endpoint returns the units,
  refuses a missing/ wrong token 403, and is permitted under readonly.
- `test_page_js_lazy_fetch_and_degradation_marker` — the fetch target + slot
  fill + the labelled fetch-failure note are present in `/pages.js`.
- `test_member_frame_prefers_sidecar_probe_and_is_byte_identical` — a
  probe-carrying sidecar makes ZERO probe calls and requests the IDENTICAL
  `at_ms` the live-probe path computes from the same duration (parity).
- `test_member_frame_falls_back_when_sidecar_probe_absent` — a probe-less
  sidecar makes exactly one live probe.

**Verification set (targeted, no network):** `test_fp_review_lazy` (9) +
`test_gui_pages` + `test_qc_consistency` + `test_gui` + `test_gui_core` +
`test_fp_ratemig1` all green (152 in the core set), plus a broad
GUI/review/qc/mcp sweep (229) green.
