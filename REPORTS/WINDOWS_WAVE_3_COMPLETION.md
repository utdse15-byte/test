# Windows Wave 3 — Completion (MANJU_WINDOWS_ONLY_LEAN_V3, W3: Windows NLE、字幕、字体和 Board 审片)

Plan **MANJU_WINDOWS_ONLY_LEAN_V3**, wave **W3** (§5.1–5.6). Branch
`claude/implement-ai-advice-delegation-tvt9ms`, base = the round-1 push tip
**4d1668a** (`133a235` gate round 1 + the static-board flake fix), itself on the
W2 tip **72dfa91**. Authored on a Linux container (Python 3.11.15, ffmpeg 6.1.1);
**no Windows host, no NLE, no PowerShell**. W3 was cut into three disjoint slices
built by parallel implementers plus the orchestrator's own `cli.py` wiring, all
red-first, and is landed together with **round 2** of the Windows-gate fixes
(the wave's second story). All **100** new tests pass on the authoring host; the
real-host run is the gate's next execution.

## Baseline

At the base tip 4d1668a the authoring-host suite was green — W2's verified
**4158 passed** plus round-1's 11 gate pins — and the hard `windows-ci.yml`
gate's last measured verdict was **run #3: 41 F / 4105 P / 9 E** (down from run
#1's 76 F / 4016 P / 35 E). Three audit dossiers (NLE, Fonts+subtitles, Board)
established the base-tip status of every W3 capability before any code changed;
their statuses/owners are the Baseline's audit tables. See
`REPORTS/WINDOWS_WAVE_3_BASELINE.md`.

## Audit

Decision key: **Implement** (W3 built it) · **Kept** (already satisfied, pinned)
· **Deferred** (with evidence, out of W3 scope) · **Rejected** (per a plan rule)
· **N-t-d** (nothing to do / vacuous pass).

| Capability | Decision | Evidence |
|------------|----------|----------|
| §5.2 xmeml v4 writer (Premiere/Resolve interchange) | **Implement** | new `exporters/xmeml.py` — `compile_xmeml:194`/`export_xmeml:352`, single `<xmeml version="4">`/`<sequence>` from the same `Timeline.tracks` truth |
| §5.2 exact rational rate via timebase+ntsc | **Implement** | `_rate_elem:159` (`timebase = Rate.nominal_int`, `ntsc TRUE` = the 1001 family) — every time a whole frame off `timebase.ms_to_frames`/`duration_frames`, never a float |
| §5.2 telescoped whole-frame spine | **Implement** | `_clip_frames:126` (R2 stamp else `ms_to_frames` diff); event N's end frame == N+1's start frame |
| §5.2 `file://localhost` pathurls, drive-colon literal, %-encoded space/CJK | **Implement** | `_file_url:106` + `_DRIVE_RE:103` — forward slashes, `quote(safe="/")`, drive colon kept literal |
| §5.2 linked A/V, clean cross-dissolves only, in-band notes for every omission | **Implement** | `<link>` pairs `:333-343`; `_is_clean_dissolve:189` + overlap guard `:225`; `<!-- MANJU -->` comments for every wipe/loop/sfx/None omission |
| §5.2 markers omitted (no marker truth to invent) | **Implement (honest omission)** | docstring `:48-52` — the `Timeline` model carries no marker truth, so none is written |
| §5.1 conform-loss doc for EVERY produced export target | **Implement** | `cli.py:2213-2234` — `_CONFORM_TARGET` map, `conform_loss_report`+`write_conform_report` per output, a new `conform` JSON key + `conform[target]` echo, derivation faults degrade to `⚠` |
| §5.1/5.4 xmeml rules table + frame-native drift + caption sub-features across all 9 targets | **Implement** | `conform.py` `"xmeml"` `_RULES:777`; `caption_roles`/`caption_speakers` in `KNOWN_FEATURES:102-103`, inventory `:147-150`, per-target rows across otio/jianying/native_draft/srt_ass/ttml/edl/fcpxml/openclap/xmeml; `_frame_native_drift:1219` |
| §5.3 Windows CJK font enumeration | **Implement** | `card.py` `_find_font_windows:` (curated YaHei-first candidates), `_win_font_dirs` (`%WINDIR%`/`%LOCALAPPDATA%`), guarded `_win_registry_font_files` (winreg HKLM/HKCU) behind `_IS_WINDOWS` |
| §5.3 POSIX font path byte-identical; glyph coverage stays UNKNOWN | **Kept** | the POSIX `find_font` chain is untouched (diff-proven, Windows sources never consulted on POSIX); no glyph tool added |
| §5.5 the ONE budgeted schema `manju.review.annotation/v1` | **Implement** | `models.py:377` `ANNOTATION_SCHEMA` + `Annotation:400`; `CONTRACTS.yaml:516` experimental row, owner `manju.core.models` |
| §5.5 media-bound annotation + render-time staleness | **Implement** | `Annotation.matches_media:527` (stored `media_sha256` vs current `hash_file`); STALE computed at render (`board.py` `_render_take_annotations:1204`) |
| §5.5 board `annotate` action (strict server-side validation, CAS, one event, HTTP 400) | **Implement** | `server.py` `_api_annotate:351`, `_BadRequest:92`, registered `:467`, 400 branch `:734` |
| §5.5 serve-only annotation UI (severity chips, click-to-seek, STALE badge, add-form) | **Implement** | `board.py` `_SERVE_CSS`/`_SERVE_JS` (`annSeek`/`annSubmit`), `_render_take_annotations:1204`; static board bytes frozen |
| §5.6 board `--app` Edge/Chrome app window | **Implement** | `cli.py:3869` `--app`, `:3903` `find_edge()`/`find_chromium()` → `msedge --app=<url>`, fallback default browser, no Electron/WebView2 |
| installer self-test probe `manju --version` | **Implement** | `cli.py:33` `_version_callback` + `:42` eager `--version` callback |
| gate round 2: two-level filtergraph escaping owner | **Implement** | `card._escape`/`boards._escape_path`/`waveform` delegate to `render._escape_filter_path` (one owner) |
| gate round 2: events lock excludes same-process THREADS on Windows | **Implement** | `events.py:` per-lock-name `threading.Lock` paired with the msvcrt byte lock (Windows branch) |
| xmeml IMPORT plan | **Rejected** | schema budget → `review.annotation`; a writer + conform-loss doc is the deliverable (P2 AAF skipped per plan default) |
| subtitle model expansion (ruby/vertical/sound-cue) | **Deferred** | no upstream producer — dead fields would be fabrication; the conform loss rows carry the honesty |
| drawing UI beyond the stored geometry field | **Deferred** | schema carries rect/arrow geometry; a future wave adds freehand + canvas overlay with no schema bump |
| annotation → Repair Proposal auto-routing | **Deferred** | `repair_variable` carried verbatim (the `_VARIABLE_ROUTE` seam); explicit human action stays the trigger |

## Red tests

100 new tests, red-first, one row per **RED CLUSTER** (not all 100). Root cause =
the missing production seam each cluster names.

| Cluster (file) | Old (base) | New behavior pinned |
|----------------|-----------|---------------------|
| xmeml writer exists (`test_windows_xmeml.py`) | `ModuleNotFoundError` | `compile_xmeml`/`export_xmeml` produce a well-formed, byte-deterministic `<xmeml v4>` from `Timeline.tracks`; empty timeline → valid zero-duration doc |
| xmeml rational frame math (same file) | RED | 24000/1001 rides `timebase 24 + ntsc TRUE`, integer rides `ntsc FALSE`; frames exact, off-grid ms snaps to whole frame, R2 stamp wins over rounded ms |
| xmeml `file://` URIs (same file) | RED | backslash/forward/mixed separators + CJK + space → `file://localhost/C:/...%E6...`, reserved chars %-encoded, **drive colon literal** |
| xmeml audio subset + linked A/V + transitions (same file) | RED | voice=track1/music=track2, shared source → `<link>` pairs; clean `xfade_fade` → native `<transitionitem>`; every other kind + loop + sfx/ambient → cut/omit **with an in-band note** |
| xmeml containment + IO (same file) | RED | out-of-project video **or** audio source refuses the export; writes under `exports/xmeml/`, honors `dest` |
| caption sub-features classified across all 9 targets (`test_windows_conform_captions.py`) | **19 F** | `caption_roles`/`caption_speakers` are `KNOWN_FEATURES`; inventory silent when absent, row-per-field when present; every target classifies both **without hard-error** (禁止静默丢失 made machine-readable) |
| xmeml target is classified + drift-free (both files) | RED | `xmeml` module classified, every feature exactly once, categories honest, `frame_drift` 0 by construction, source-rate mismatch still surfaces |
| Windows font locator (`test_windows_fonts.py`) | `AttributeError` | curated dir-scan (YaHei beats SimSun across stores) + registry fallback + honest `None`→UNKNOWN; **POSIX chain byte-identical, Windows sources never consulted on POSIX** |
| Annotation model (`test_windows_annotations.py`) | `ImportError` | validates id/take/media-hash/rate-string/frame/range/subject/text/geometry-kind; `repair_variable` carried verbatim; `matches_media` prefix-agnostic |
| board `annotate` endpoint (same file) | 404 / no action | happy-path writes the shot + one event; bad input → **400**; no token → **403** and writes nothing; stale `expected_rev` → CAS refusal; dangerous-action surface unchanged |
| serve UI + static-board pin (same file) | RED | served HTML lists the annotation with seek + form, renders a STALE badge on a media mismatch; **the static board byte-pin still holds and carries no annotation UI** |
| schema registered experimental (same file) | RED | `manju.review.annotation/v1` in the registry, `test_fp_contracts` green both directions |
| export emits conform docs (`test_windows_export_conform.py`) | no `conform` key | every produced target ships its conform-loss doc under `conform`; human output names them; docs are **derived-only, never read back** |

## Implementation

### Production source files

**Slice A — NLE (§5.1/5.2/5.4):**

1. **`src/manju/exporters/xmeml.py`** (NEW) — XMEML v4 writer. `compile_xmeml`
   (`:194`, pure) / `export_xmeml` (`:352`, atomic IO under `exports/xmeml/`).
   `timebase`+`ntsc` from `_rate_elem` (`:159`) ARE the exact rational rate;
   the record spine telescopes exact whole frames (`_clip_frames:126`, R2 stamp
   else `ms_to_frames`). `_file_url` (`:106`) + `_DRIVE_RE` (`:103`) emit
   `file://localhost/...` with the drive colon literal and everything else
   percent-encoded. `_FileTable` (`:168`) dedupes `<file>` by first appearance
   (deterministic ids). Clean cross-dissolves (`_CLEAN_DISSOLVE_TYPES:93` +
   overlap guard) become a native `<transitionitem>`; every other transition,
   loop bed, sfx/ambient clip and `duration_ms is None` clip is **omitted with
   an in-band `<!-- MANJU -->` comment** (never silent). Reciprocal `<link>`
   pairs (`:333-343`) tie a voice clip to a video clip sharing its source.
   Markers omitted (docstring `:48-52`) — no marker truth to invent. Out-of-
   project sources refuse via `_resolve_contained` (`:144`, the render/OTIO/
   FCPXML containment stance). `exporters/__init__.py` re-exports both.
2. **`src/manju/exporters/conform.py`** — the full `xmeml` rules table
   (`_RULES["xmeml"]:777`, every `KNOWN_FEATURE` classified with a line-cited
   `where`); the frame-native drift branch generalized from FCPXML-only to a
   `_FRAME_NATIVE_GRID`/`_FRAME_NATIVE_NOTE`-driven `_frame_native_drift`
   (`:1219`, dispatched `:1293`) with `_DRIFT_TRACKS["xmeml"]` (`:1041`,
   video+voice+music) and the xmeml XML cross-check (`_exported_notes:1563`);
   the two NEW caption sub-features `caption_roles`/`caption_speakers`
   (`KNOWN_FEATURES:102-103`, inventory `:147-150`) classified **explicitly
   across all 9 targets** — otio(`:228`)/jianying(`:306`)/native_draft(`:386`)/
   srt_ass(`:439`)/ttml(`:468`)/edl(`:524`)/fcpxml(`:627`)/openclap(`:734`)/
   xmeml(`:820`) — so a role/speaker never vanishes silently.

**Slice B — Fonts (§5.3):**

3. **`src/manju/media/card.py`** — `find_font()` gains the Windows branch
   (`_IS_WINDOWS` guard): `_find_font_windows` probes the curated CJK candidates
   (`_WINDOWS_CJK_CANDIDATES`, YaHei `msyh.ttc` first) across the two standard
   stores (`_win_font_dirs`: `%WINDIR%\Fonts` + `%LOCALAPPDATA%\...\Fonts`),
   then a guarded `_win_registry_font_files` (winreg HKLM+HKCU Fonts,
   failure-safe → `[]`). Curated **order wins across stores**. POSIX chain
   untouched and byte-identical (diff-proven); `None` → the §8.4 card degrades
   to drawtext's default and glyph coverage stays the honest UNKNOWN.

**Slice C — Board (§5.5):**

4. **`src/manju/core/models.py`** — `ANNOTATION_SCHEMA` (`:377`) + the
   `Annotation` model (`:400`): `media_sha256` (full-hash regex) + `frame_rate`
   (exact rational string) + `frame`/`range_frames` + `subject` + severity
   `note|issue|blocker` + `text` + loose `geometry` (only `kind` validated) +
   `repair_variable` (carried for qc routing, never routed) + `actor` +
   `created_at`; safe-segment ids; `matches_media` (`:527`) is the one
   staleness comparison. `ShotStatus.annotations` (`:558`) with an
   **exclude-when-empty** `@model_serializer` (`:561`) — a shot written before
   this landed round-trips byte-identically.
5. **`src/manju/board/server.py`** — the `annotate` action: `_api_annotate`
   (`:351`) validates strictly server-side (shot/take exist, take media on disk
   → its `hash_file` is the binding), writes under `build_lock` +
   `checked_shot_write` + `expected_rev` CAS, emits one `annotate` event; the
   new `_BadRequest` (`:92`) → **real HTTP 400** (`:734`); registered in
   `API_ACTIONS` (`:467`). `frame_rate` is `ProjectConfig.frame_rate` rendered
   by the ONE `Rate.__str__` formatter (no second formatter, no raw-rational
   leak — the ratemig1 pin).
6. **`src/manju/board/board.py`** — serve-only UI: `_render_take_annotations`
   (`:1204`) renders the list rows (severity chips, `_ann_time_chip:1181` with
   `data-seek`, STALE badge `_ANN_STALE_BADGE:1161` computed at render from the
   take file's current hash) + the add-form (at-current-frame capture, CAS rev);
   `_SERVE_CSS`/`_SERVE_JS` gain the `annSeek`/`annSubmit` handlers. Static
   board bytes pin-frozen and untouched (serve-mode only).

**Orchestrator wiring (`src/manju/cli.py`):**

7. `--xmeml` on `export` (`:2081`) wired into the target set and IO
   (`export_xmeml`). §5.1: conform-loss docs now emitted for **every produced
   export target** (`:2213-2234`, `_CONFORM_TARGET` map) — the audit's biggest
   W3 gap (conform was library+tests only) — under a new `conform` JSON key +
   `conform[target]` echo lines; derivation faults degrade to visible `⚠`
   notes (never abort the export whose artifact already landed). `board --app`
   (`:3869`/`:3903`): `find_edge`/`find_chromium` → `msedge --app=<url>` app
   window, fallback default browser. `manju --version` (`:33`/`:42`, eager
   typer callback — the installer self-test's probe). `exporters/__init__.py`
   re-exports xmeml.

**Interleaved — Windows gate round 2** (the wave's second story):

8. `card._escape`, `boards._escape_path` and `waveform.rms_levels` delegate to
   the ONE two-level owner `render._escape_filter_path` (the caption-card
   fallback death — drive-colon paths dying in drawtext's own option parser —
   explained the whole "missing"/offline-degradation cluster). `events.py`
   Windows branch pairs the msvcrt byte lock (cross-process) with a per-lock-
   name `threading.Lock` (cross-thread — the real host proved CRT byte locks do
   NOT exclude same-process threads: 12 verdict threads → 6 lines). `install-
   manju.ps1` gains a strict self-test (`$LASTEXITCODE` checks **before** the
   pointer switch) + **ASCII** pointer writes (a PS5.1 UTF8 BOM would corrupt
   the launcher's `set /p`); `windows-ci.yml` sets `PYTHONUTF8=1` on the jobs
   and asserts the `--version` output shape. `test_windows_gate_round1.py`'s
   colon-directory end-to-end test now skips on `nt` (colon dirs are
   unrepresentable on NTFS — the unit tests + real renders cover the drive-colon
   case).

### Tests

- `tests/test_windows_xmeml.py` (**36**) — rate/ntsc/zero-drift frame math,
  `file://` URI encoding, linked A/V + the voice/music subset, transitions,
  determinism/well-formedness/empty-doc, export IO + containment, and the xmeml
  conform classification + drift.
- `tests/test_windows_conform_captions.py` (**22**) — `caption_roles`/
  `caption_speakers` inventory + explicit per-target classification across the
  9 targets (禁止静默丢失 machine-readable).
- `tests/test_windows_fonts.py` (**9**) — the Windows dir-scan + registry
  fallback + preference order + failure-degradation + UNKNOWN sentinel, and the
  POSIX byte-identity / Windows-sources-never-on-POSIX pin.
- `tests/test_windows_annotations.py` (**30**) — the Annotation model, the
  `annotate` endpoint (400/403/CAS/event), the serve UI + STALE badge, the
  static-board pin, and the schema registration.
- `tests/test_windows_export_conform.py` (**3**) — export emits conform docs
  per target, names them in human output, and never reads them back.

### CLI / contracts

- **One new schema:** `manju.review.annotation/v1` (`CONTRACTS.yaml:516`,
  `experimental`, owner `manju.core.models`) with the `ANNOTATION_SCHEMA` src
  literal in the same change — `test_fp_contracts.py` green both directions.
- **CLI additions:** `manju export --xmeml`; the export `--json` gains a
  `conform` key (+ `conform[target]` human echo); `manju board --app`;
  `manju --version` (an **eager callback**, not a command — the frozen command
  surface is unchanged, `test_fp_cli_snapshot` green).
- **New board action:** `annotate` (the 8→9th, first to return a real HTTP 400).

### Design deviations (honest)

- **xmeml IMPORT plan REJECTED_WITH_REASON** — the schema budget went to
  `review.annotation`; the P2 AAF exit is skipped per the plan default.
- **Subtitle model expansion DEFERRED_WITH_EVIDENCE** — no upstream producer, so
  the fields would be fabricated; the conform loss rows carry the honesty.
- **Drawing UI DEFERRED** — the schema stores the geometry so a future wave adds
  UI without a schema bump.
- **Glyph coverage stays the UNKNOWN sentinel** — no glyph-tool dependency added.
- **Two pin evolutions (reviewed, documented).** `test_fp_ttml.py` — the caption
  sub-features join `"captions"` on the preserved side (the TTML writer factually
  preserves both); every other feature keeps its unsupported teeth.
  `test_fp_ratemig1`'s `edit_rate` grep-pin was honored by switching the board's
  rate to the sanctioned `ProjectConfig.frame_rate` resolver — **the leak was
  fixed, not the pin weakened**.

## Compatibility

- **Old shots byte-identical.** `ShotStatus.annotations` serializes only when
  non-empty (`_drop_empty_annotations:561`), so a shot written before W3
  round-trips byte-for-byte (test-proven `test_old_shot_without_annotations_
  round_trips_byte_identical`).
- **Static board frozen.** All annotation UI is serve-mode only; the static
  board byte-pin (`test_board_serve.py`) still holds and carries no annotation
  markup.
- **POSIX font path byte-identical.** `find_font` returns before the Windows
  branch on POSIX; the Windows sources are never consulted there (diff-proven +
  `test_posix_chain_unchanged_and_windows_sources_never_consulted`).
- **Content keys untouched.** xmeml is a new derived exit; the burn-in content
  key still folds the font hash only under the existing `cache_toolchain_keys`
  opt-in — no new key path.
- **Existing exporters unchanged.** The conform generalization keeps every
  non-xmeml target's drift branch and the FCPXML note byte-identical; the new
  caption sub-feature rows are additive.
- **`annotate` is additive.** The other 8 board actions and the dangerous-action
  404 surface are unchanged (`test_annotate_is_registered_and_dangerous_surface_
  unchanged`).

## Verification

Targeted suites on the authoring host (Linux, full tooling):

- The five new files — **100 passed** (36 xmeml + 22 conform-captions + 9 fonts
  + 30 annotations + 3 export-conform).
- **Fonts slice + neighbors** (`test_windows_fonts.py` + `test_fp_toolkeys.py`
  + `test_fp_toolchain.py`) — **54 passed**.
- **NLE slice** mandated verify (xmeml + conform + fcpxml + fcpxml-import + edl
  + timebase families) — **179 passed** after the two integration fixes.
- **Board slice** (annotations + board-serve + board hardening + contracts) —
  **102 passed**.
- **Orchestrator integration** (`test_export_center.py` +
  `test_export_containment.py` + `test_fp_cli_snapshot.py`) — **47 passed**.
- **Pin-evolution guard** (`test_fp_ratemig1.py` + `test_fp_ttml.py` +
  `test_fp_conform.py`) — **73 passed** (the `edit_rate` leak fixed, the ttml
  caption-side evolution reviewed, conform completeness intact).

Full suite (`pytest -n auto`, authoring host): **4269 passed, 1 skipped, 0 failed (487.98s) — the 4169 round-1 tip plus the 100 new W3 tests; the failing set is empty before AND after on the authoring host**.

Windows CI (`windows-ci.yml` full suite + `install-smoke`): **gate run #4 triggered by this push (runs #1/#3 were 76F/35E → 41F/9E; round 2 targets the caption-card escaping cascade, the same-process thread lock, and the cp1252 defaults — the remaining tail is next-round evidence, recorded in DECISIONS #38)**.
Honest expectation: the gate is much smaller after round 2 but likely **not yet
green** on run #4 — most of the remaining tail was downstream of the caption-card
death (now fixed). The known remaining Windows failures after round 2 (expected
to shrink) include: `test_ticket2` make_sample argparse, `test_round_w_meta`
argv-fallback IndexError, `test_round_q` repair_auto JSON decode, `test_ingest`
duplicate count, `test_auto_agent` event counts, `test_mcp_copilot` e2e,
`test_job_cancel` kill-latency timings, `test_gui_finish` SVG-vs-PNG preview,
`test_edit_v3` caption-preview 404, plus the cluster expected **cured** by the
card/thread-lock/UTF-8 fixes (`dr02` thread-append, `test_dr06`,
`hash_versions`, `why_stale`, `shot_lab`, `spend_delta`, cli-redo, `ask_before`,
`spend_gate`, packaging, `round5`, failures-degradation, `dr03c` charmap). Stated
plainly so the tail is next-round evidence, per the plan's iterative discipline —
not hidden.

Method: `python -m pytest` only; red-first pins for every new behavior; no
existing test deleted or weakened (the two pin evolutions above are reviewed and
documented, not silent).

## Skipped / Rejected

| Item | Reason |
|------|--------|
| xmeml IMPORT plan | **REJECTED_WITH_REASON** — the one schema budget slot went to `review.annotation`; a writer + machine-readable conform-loss doc is the W3 deliverable |
| P2 AAF exchange | **REJECTED** — skipped per the plan's default scope |
| Subtitle model expansion (ruby / vertical / sound-cue fields) | **DEFERRED_WITH_EVIDENCE** — no upstream producer in the dialogue/ASR pipeline; dead fields are fabrication; the `caption_roles`/`caption_speakers` loss rows carry the honesty |
| Drawing UI beyond the geometry field (freehand + canvas overlay) | **DEFERRED** — the schema stores rect/arrow geometry so a future wave adds UI with no schema bump |
| Glyph-coverage checks | **REJECTED (stays UNKNOWN sentinel)** — no font-parsing dependency added; coverage is never guessed to PASS |
| Annotation → Repair Proposal auto-routing | **DEFERRED** — `repair_variable` carried verbatim to the `_VARIABLE_ROUTE` seam; explicit human action stays the trigger |

## Final

**Commit SHA:** `597656a (code + tests + gate round 2; this report and DECISIONS #38 land in the docs follow-up commit)` (W3 slices + gate round 2 + tests; this report
and the DECISIONS follow-up land in the docs commit).

**Remaining risks:**

1. **The first xmeml import into a real Premiere/Resolve is unverified** — no NLE
   exists on any host. The evidence is well-formedness, rational-exactness
   (drift 0 by construction), determinism and the conform-loss doc, not a live
   import. The drive-colon-literal pathurl choice in particular is asserted by
   unit tests, not observed resolving in an NLE.
2. **The annotation UI is exercised via HTTP + served-bytes tests, not a human
   browser session** — the click-to-seek and add-form JS run for the first time
   in a real browser only when a user opens `manju board --serve`.
3. **The Windows gate is not yet green** (the honest tail above). Round 2 is
   expected to shrink run #4 substantially; the residue is the next round's red
   evidence, per the plan's iterative discipline.
4. **The Windows font `winreg` read runs for the first time on the gate** — on
   the authoring host it is exercised only through env-redirected stores and a
   stubbed registry; casefold/NTFS nuances are unchanged from W1.
