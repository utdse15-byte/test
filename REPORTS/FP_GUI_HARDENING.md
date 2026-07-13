# FP M2 — GUI/board hardening + efficiency batch (audit 13-17 + G2/G3/G5/G6)

**Deliverable (OPT-M2):** the GUI/board slice of `REPORTS/OPTIMIZATION_AUDIT_20260712.md`
— three defense-in-depth hardenings on the local board serve surface, three
request-thread efficiency fixes on the GUI, one JS-dedup, one input-cap, and one
i18n pairing. Every change is either an HTTP **header** on a served response, an
internal **plumbing** refactor that keeps a pinned SHAPE/bytes identical, or an
**unpinned** render/template dedup — the aggressive golden/byte pins this repo
carries were the binding constraint and NONE were moved.

**Binding constraint honoured:** the board's EXPORTED static HTML is byte-pinned
(`test_board_serve.py::test_static_board_is_byte_identical`,
`test_fp_board_transport.py`). The CSP fix (item 15) is a **response header** with
a script hash computed from the served bytes, so **zero HTML bytes changed** — the
static byte-pin never had to move. `/api/state` SHAPE is pinned; the G2 fix is
pure plumbing that reproduces the payload identically.

**Files touched (only these):**
`board/server.py` (items 15/17/14), `core/container.py` (item 14 — one new method
`Project.safe_served_path`), `gui/server.py` (items 14 + G3 endpoint + G5 route),
`gui/state.py` (G2), `build/status.py` (G2 — `project_status` gained keyword-only
`statuses=`/`voices=`, every existing caller unchanged), `gui/edit.py` (G3 + G5),
`gui/jobs.py` (G6), `gui/common_js.py` (**new** — G5 shared helper), the ten
server-rendered page modules `gui/{pages,pages_t,lab_page,storyboard,create_page,
director_page,exports_page,ingest_page,series_page}.py` + `edit.py` (G5),
`qc/agent_review.py` (item 13 — VerdictError message strings only),
`exporters/fcpxml_import.py` (item 16). New/extended tests:
`tests/test_fp_board_hardening.py`, `tests/test_fp_state_perf.py`,
`tests/test_fp_gui_endpoints.py`, `tests/test_fp_fcpxml_import.py` (+5). This
report. **Untouched:** `providers/` (live parallel loop), `timeline/`,
`core/events.py`, `core/hashing.py`, `tests/conftest.py`, CONTRACTS/DECISIONS/.github.

---

## Item 15 — board CSP gap (served responses only; bytes frozen)

`board/server.py::_serve_board` now emits the GUI's security header set:
`Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options:
nosniff`, `Referrer-Policy: no-referrer`. The board is a SINGLE-FILE
inline-everything document (unlike the GUI, whose CSS/JS are external `/app.css` /
`/app.js`), so `script-src 'self'` is impossible. Instead `script-src` carries the
**sha256 of the ACTUAL inline `<script>`** the served bytes ship — computed at
serve time via `_board_security_headers(html)`, because that block includes the
per-run token (a static baked-in hash would break on every run). This lets the
inline script run **without changing one HTML byte**, so the static byte-pin stays
frozen. `style-src 'unsafe-inline'` (the board inlines its `<style>` block + a few
`style=""` attrs; style injection is not a script-execution vector, so `script-src`
carries the real XSS protection). The one `onclick="mjCopy(this)"` is **static-mode
only** — the served board has no inline handlers, so `'unsafe-hashes'` is omitted
(the helper self-heals if a serve-mode handler is ever added). All image/media
sources are `/media/...` (verified: no `data:`/external), so `img-src 'self'` +
`media-src 'self'` are sufficient; no `<form>`/`<base>`/`<iframe>`.

## Item 17 — oversize-POST keep-alive desync

`board/server.py::_read_body_raw` sets `self.close_connection = True` when a body
exceeds `_MAX_BODY_BYTES` (1 MiB), and `_send_json` emits `Connection: close` when
that flag is set. The remaining declared body is left undrained (as before, to
avoid pulling a hostile body into memory) but the keep-alive connection now closes
after the response, so those leftover bytes can never desync into the next request.
A NORMAL-size POST is unaffected (no `Connection: close`). Pinned with two
back-to-back requests on ONE raw socket.

## Item 14 — one shared media-serving gate

Extracted `Project.safe_served_path(rel, prefixes)` into `core/container.py` — the
resolve-then-recheck containment gate both surfaces implemented independently. The
GUI's `_resolve_served` and the board's `_safe_media_path` now both delegate to it,
preserving the EXACT semantics each had (allowlist BEFORE resolve, symlink-aware
`resolve()`, allowlist AGAIN on the resolved relpath; the board still `unquote`s at
its call site, the GUI router already had). OPT-ROBUST confirmed both gates were
already correct — this closes the "one-sided future fix" risk, pinned by a spy
asserting the board `/media` route calls `Project.safe_served_path`. Both surfaces'
serving tests stay green unmodified.

## G2 — build_state double evaluation

`build_state` ran `evaluate_all` + `evaluate_all_voices` TWICE each per state build
(`project_status` and `_shot_cards` recomputed independently; ~438 ms of a measured
705 ms @40 shots was the duplicate pass). Now `build_state` runs each ONCE and
threads the raw results into both `project_status(project, statuses=, voices=)` and
`_shot_cards(project, statuses=, voices=)` (both keyword-only, default `None` →
compute standalone, so `manju status`/MCP/board callers are unchanged). Spy-pinned:
`evaluate_all` and `evaluate_all_voices` each run exactly once per build; the
`/api/state` payload is proven shape-identical.

## G3 — /edit inline explain recompile

`render_edit` ran a FULL `build.explain` recompile (+ ~40 probes) inline just to
compute the dirty-badge string, and again via `playback_source`'s stale-check — the
exact op `state.py`'s docstring keeps behind an on-demand endpoint. Now the dirty +
stale badges are **lazy slots** (`id="ed-dirty-chip"` / `id="ed-play-stale"`,
hidden), revealed by `/edit.js` after first paint via a new small `GET
/api/edit/dirty` (mirrors the review-board lazy idiom). `playback_source` gained
`include_stale=False` for the render path (the async `/api/edit/playback-source`
keeps the default). `build.explain` semantics unchanged; CLI explain untouched.
Spy-pinned: `render_edit` makes ZERO explain recompiles and spawns ZERO subprocesses
on the request thread.

## G5 — duplicated per-page JS helpers

The token-reader + `post` + `toast` were **byte-identical** across ten
server-rendered page modules (post ×10 identical; toast ×10 identical modulo a
cosmetic 3400/3600/4000 ms delay, unified to 3600; token ×10). Defined ONCE now in
`gui/common_js.py`, served as `/common.js` (`defer`, injected before each page's own
script in that page's shell). Each page **dropped its local copies** and reads the
globals through the scope chain — no call site changed. **754 source bytes removed
per page × 10 = 7 540 bytes** of duplication; `/common.js` is 994 bytes served once
and cached. NOT deduped (documented residual): `pollJob` (its body genuinely
diverges across lab/exports/ingest/series and edit uses a `(id, done)` signature)
and the glossary/workspace `post` (a different return contract — raw fetch / throw
on error). The GUI has no byte-golden on any rendered JS (audit negative result);
behaviour is pinned by the full GUI test set + a node `--check` syntax pass.

## G6 — jobs.jsonl fsync + untested endpoints

`gui/jobs.py::_append_records` dropped `os.fsync` (kept `flush`) — `jobs.jsonl` is
self-declared disposable operational history, and `submit()` persisted on the
POST/click thread BEFORE the 202, so an fsync put disk-sync on the click path. The
write itself is kept (pinned: the queued line is on disk, no fsync on the submit
thread, none across the lifecycle). Added HTTP-layer smoke tests for the three
GUI-untested endpoints the research named: `POST /api/impact` (token gate +
missing-shot 400 + report shape), `GET /api/director/suggest`, `GET /api/git/diff`
(the research labelled director/suggest a POST; the route is a GET — pinned as it is).

## Item 13 — VerdictError zh-only messages

`qc/agent_review.py`'s zh-only `VerdictError` message literals gained English
pairings in the house `中文 (english)` style. Appended (never replaced), so every
existing substring/`match=` assertion across `test_qc_agent`, `test_dr02_intake`,
`test_c081012_review`, `test_qc_consistency`, `test_c20b_corpus`, `test_p0_assurance`,
`test_c15_reviewer`, `test_fp_compat` stays green. Nothing else in that file touched
(the v2 batch-reject wrapper, whose content is the joined per-verdict sub-errors from
`_prepare_v2_record`, was left alone — out of the "VerdictError message block" scope).

## Item 16 — fcpxml_import input caps

`exporters/fcpxml_import.py::_load_text` now enforces a **64 MiB byte cap** (checked
on a file via `stat()` BEFORE the read, so a multi-GB document is never materialized
for the DTD-less parser) and **refuses a DOCTYPE/ENTITY prolog** with a clean
structured `FcpxmlImportError` (`code: doctype_forbidden`) — closing the
platform-libexpat-dependent "billion laughs" residual with a code-level refusal
rather than a parser-version dependency. Both are local, plan-only, no-write
residuals (defense-in-depth). Normal-file import-plan behaviour is byte-identical
(`tests/test_fp_fcpxml_import.py`, +5 pins).

---

## G4 — DEFERRED (GUI↔board transport substrate consolidation)

G4 (extract the ~180-line Range/206 + path-jail + Host-guard transport substrate the
GUI and board reimplement on the same stdlib base) is **deferred**, exactly as the
audit frames it. The audit's own finding #4 names the blocker: `test_board_serve.py`
+ `test_boards.py` byte-pin the board's static HTML **and** its range/206 header
bytes, and the two surfaces carry **divergent allowlists** (`MEDIA_PREFIXES` vs
`_MEDIA_PREFIXES`, overlapping only on `media/`), so a shared `MediaFileServer`
extraction would have to reproduce both surfaces' response bytes exactly — a
byte-pin decision that belongs to the orchestrator, not this loop (HOUSE RULE:
stop-and-report on a byte-pin change). The ~180-line duplication is therefore
**accepted risk for now**. The mitigation actually taken is item 14's
`Project.safe_served_path`: the single most security-sensitive slice of that
duplication — the path-traversal containment gate — is now single-sourced and
spy-pinned on both surfaces, so the highest-consequence divergence (a one-sided
traversal fix) can no longer happen even while the range/host/token plumbing stays
duplicated. Full transport extraction should ride a deliberate re-pin of the board
range/206 goldens against the shared implementation.

## Verification

`python -m pytest` only; red-first pins for every new behaviour; no existing test
weakened or deleted; no commit/push. See `scratchpad/FP_M2_RESULTS.md` for the
per-item table, before/after measurements, and verbatim run tails.
