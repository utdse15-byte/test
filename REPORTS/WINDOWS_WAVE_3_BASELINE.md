# Windows Wave 3 — Baseline (MANJU_WINDOWS_ONLY_LEAN_V3, W3: Windows NLE、字幕、字体和 Board 审片)

Plan: **MANJU_WINDOWS_ONLY_LEAN_V3** — "Windows NLE、字幕、字体和 Board 审片".
Slice: **W3 only** — §5.1 (conform-loss fed by every produced export target),
§5.2 (xmeml / FCP7 XML interchange writer for Premiere/Resolve), §5.3 (Windows
font enumeration for CJK burn-in), §5.4 (per-cue subtitle semantics + their
conform-loss honesty), §5.5 (structured media-bound board annotations + the ONE
budgeted `manju.review.annotation/v1` contract), §5.6 (Windows app-mode board
launch, `msedge --app=`). W1 (Windows 硬 CI + 文件系统/进程安全) and W2 (安装 /
Portable / 升级 / Doctor) are landed. **Interleaved with W3**: the Windows-gate
fix rounds that the W1/W2 hard gate keeps surfacing (round 2 lands in this wave).
Date: 2026-07-13 · Repo: `/home/user/test` (Manju) · Branch
`claude/implement-ai-advice-delegation-tvt9ms` · Base = the round-1 push tip
**4d1668a** (`133a235` "Windows gate round 1" + `4d1668a` static-board byte-pin
flake fix), which sits on the W2 tip **72dfa91** (code) + **2064636** (W2 docs,
DECISIONS #37).

## Authoring environment — and the honest limit of what it can prove

Authored on the same **Linux container** (Ubuntu Noble) as W1/W2. W3 is a
*Windows NLE-interchange + Windows-font + browser-launch* wave, so the honesty
gap is the sharpest of the three and is stated four ways, none pretending to be
a real-host run:

1. **No NLE exists on any host.** The xmeml writer's output is never opened by a
   real Premiere Pro / DaVinci Resolve. Its evidence is *structural*, not a live
   import: XML well-formedness (`ElementTree` round-parse), rational-exactness
   (`frame_drift` = 0 **by construction** on both the int and the 1001-family
   path), byte-determinism, the drive-colon-literal `file://localhost` URI unit
   tests, and a machine-readable conform-loss document. The first import into a
   real NLE is unverified — called out again in the COMPLETION's Remaining-risks.
2. **No Windows host.** The Windows font locator (`%WINDIR%\Fonts`,
   `%LOCALAPPDATA%\...\Fonts`, the `winreg` HKLM/HKCU Fonts fallback) is driven
   on Linux by injecting `card._IS_WINDOWS = True` and redirecting the stores via
   env vars into `tmp_path`; the real `winreg` read runs only on the gate.
3. **No human browser session.** The board annotation UI (severity chips,
   click-to-seek, STALE badge, add-form) is exercised through the served HTML
   bytes + the HTTP `annotate` action, not a rendered browser.
4. **The Windows gate is not yet green.** W1's hard `windows-ci.yml` has now run
   three times; W3 lands **round 2** of the fixes. The gate is *expected to
   shrink, not necessarily to go green*, on run #4 — the remaining tail is the
   next round's red evidence, per the plan's iterative discipline.

| tool | version (authoring host) |
|------|--------------------------|
| Python | 3.11.15 |
| ffmpeg / ffprobe | 6.1.1-3ubuntu5 |
| tesseract | installed, with `chi_sim` + WenQuanYi fonts |
| NLE (Premiere / Resolve) | **none** — xmeml validated structurally, never imported |
| Windows host | **none** — `winreg`/font-store probes stubbed via env + `_IS_WINDOWS` |
| PowerShell | **not available** — installer round-2 edits pinned as text; the gate is the executable half |
| OS | Linux (Ubuntu Noble) |

## Baseline test state at the base tip (4d1668a)

W3 begins where the round-1 push finished. The authoring-host suite was green at
that tip — W2's verified **4158 passed** plus round-1's 11 new gate pins
(`tests/test_windows_gate_round1.py`) — and Ubuntu CI was green on the round-1
push. The base's authoritative **Windows** evidence is the hard gate itself:

| `windows-ci.yml` run | verdict | note |
|----------------------|---------|------|
| Run #1 (W1 push) | **76 F / 4016 P / 35 E** | first real execution; four product-bug classes surfaced |
| Run #3 (round-1 push `4d1668a`, the W3 base) | **41 F / 4105 P / 9 E** | 111→50 catalogued entries; install-smoke's first run found two real bugs |

The remaining 41/9 at the base is what W3's round-2 fixes drive down; run #4 is
the wave's own gate execution. Three audit dossiers established the base-tip
status of every W3 capability before any code changed; their statuses/owners are
this Baseline's audit tables.

## Audit summary — Dossiers 05 (NLE) · 06 (Fonts + subtitles) · 07 (Board)

`ALREADY_IMPLEMENTED` = the invariant already holds; `PARTIAL` = present but
incomplete or not wired; `GAP` = does not exist anywhere. Owner = the single
module an implementer must extend, never a parallel one.

### Dossier 05 — NLE exchange (§5.1–5.2)

| Capability | Status | Owner |
|------------|--------|-------|
| §5.1 conform-loss report fed by **every produced export target** | **PARTIAL** — the full model exists (`conform.py:75` SCHEMA, 4 categories, all 8 targets classified, frame-drift math, write/read) but **every caller is a test**; `manju export` (`cli.py:2041`) never materializes one — "library + tests only" (`conform.py:43-44`) | `exporters/conform.py` + `cli.py` export body |
| §5.2 xmeml (`<xmeml>` Premiere/Resolve interchange) writer | **GAP** — `xmeml` appears only as the *negative* case in `test_fp_fcpxml_import.py` (parse refuses non-`<fcpxml>`); no writer/parser anywhere. `fcpxml.py` emits `<fcpxml>` 1.9, an unrelated schema | none (greenfield; mirror `fcpxml.py`) |
| §5 import safety (fcpxml/edl **plan-only**, never writes truth) | **ALREADY_IMPLEMENTED** | `exporters/fcpxml_import.py`, `edl_import.py` (parse → plan → digest → verify) |
| Windows `file://` URIs in exporters (drive/space/CJK) | **GAP** — zero `pathname2url`; no exporter emits `file://` at all (all write project-relative strings) | containment guard `Project.resolve` / `fcpxml._require_contained_source` |
| Rational frame-rate math (24000/1001) in exporters | **ALREADY_IMPLEMENTED** — Fractions only, no float in a conversion path | `core/timebase.py` (Rate/`ms_to_frames`/Timecode) |
| Exporter test coverage + CLI export/import exposure | **ALREADY_IMPLEMENTED** — but no CLI materializes conform, no Windows-path exporter fixtures | `cli.py:2041` |

### Dossier 06 — Fonts + subtitles (§5.3–5.4)

| Capability | Status | Owner |
|------------|--------|-------|
| §5.3 Windows font enumeration (registry / `%WINDIR%\Fonts`) | **PARTIAL** — a font inventory exists but is single-font and Linux-only (`fc-match` + `/usr/share/fonts` glob); on Windows `find_font()` returns `None`, inventory records `'unknown'`; zero `winreg`/Windows-Fonts usage | `media/card.py:87` `find_font` / `FONT_ROOTS` |
| §5.3 font hash in the burn-in content key | **PARTIAL** — the opt-in mechanism exists (`cache_toolchain_keys`) but the hashed font is whatever `find_font()` resolves — constant `'unknown'` on Windows today | `core/toolchain.py:183`/`:227`, `media/render.py` |
| §5.4 per-cue subtitle semantics (role/speaker/region/RTL/vertical/ruby/SDH) | **PARTIAL** — cue model carries only `start/end/text/speaker/shot/role`; TTML rich, ASS Name-field, SRT/VTT none; region/vertical/ruby/sound-cue absent | `core/models.py:1191` `CaptionLine` |
| §5.4 conform-loss for unsupported subtitle semantics | **PARTIAL** — framework exists but loss granularity stops at the monolithic `"captions"` feature; per-semantic (role/speaker) downgrades live in docstrings, **not** machine-readable loss rows | `exporters/conform.py:86` `KNOWN_FEATURES` |
| §5.3 UNKNOWN handling for glyph coverage | **GAP** — no font-parsing lib imported; the only charset probe is a font-*selection* heuristic; no text-vs-cmap coverage check anywhere | none (reuse the UNKNOWN sentinel; `qc/captions_access.py`) |
| Existing caption tests | **ALREADY_IMPLEMENTED** — rich caption suites; **no** Windows-specific font/caption test | `test_fp_captions.py` / `test_fp_ttml*.py` |

### Dossier 07 — Board review + security (§5.5–5.6)

| Capability | Status | Owner |
|------------|--------|-------|
| Board security: token on mutating POSTs · CSP+framing headers · HTML-escape · 1 MiB body cap · no arbitrary write | **ALREADY_IMPLEMENTED** (1b/1d/1e/1f/1g) | `board/server.py`, `board/board.py:888` `_esc` |
| Loopback-only bind · Origin-header check | **PARTIAL** / **GAP** — loopback is the default not an invariant; only a Host-header allowlist exists, no `Origin` read | `board/server.py` |
| §5.5 structured annotations (frame/range, severity, list, click-to-seek, overlay) | **PARTIAL** — one free-text director note per take (`take_notes: dict[str,str]`); no structured/frame-level/severity/list/seek in the board | `core/models.py:371` `ShotStatus`, `board/board.py` |
| §5.5 annotation **binding** (media hash + frame rate + frame/range + subject + actor; staleness) | **GAP** — notes keyed only by take name; no hash/rate/frame/subject/actor in stored truth; no staleness | `core/models.py:371` |
| §5.5 `manju.review.annotation` contract in CONTRACTS.yaml | **GAP** — no `manju.review.*` id exists (49 schema rows, none review) | `CONTRACTS.yaml` |
| §5.6 Windows app-mode launch (`msedge --app=`) | **GAP** — board opens via `webbrowser.open`; zero `msedge`/`--app` | `cli.py:3801` board callback (+ `html_card.find_edge` from W2) |
| Repair-Proposal machinery annotations could route to | **ALREADY_IMPLEMENTED** — full repair-op vocabulary + `_VARIABLE_ROUTE`; what's absent is any annotation→repair *connection* | `media/repair_ops.py`, `qc/production.py:478` |

**Dossier 08 (contract budget, §8–9)** framed the one hard constraint: the
registry holds 49 schema rows + 12 documents, `test_fp_contracts.py` enforces
**bidirectional** literal↔registry equality, and a newcomer arrives
`experimental`. W3 spends **exactly one** schema budget slot —
`manju.review.annotation/v1` (§5.5) — and the xmeml import plan is rejected
partly to keep that budget (below).

## Red-test inventory — 100 new tests, red-first per slice

Five new files. Every slice was built red-first on disjoint files by parallel
implementers; the root cause is the missing production seam each test names.

| Slice / file | New tests | Red root cause at the base |
|--------------|-----------|-----------------------------|
| **NLE** — `test_windows_xmeml.py` | 36 | `ModuleNotFoundError` — `exporters/xmeml.py` did not exist; every writer/URI/transition/link/determinism/containment test imports it |
| **NLE** — `test_windows_conform_captions.py` | 22 | **19 F** — `caption_roles`/`caption_speakers` were not `KNOWN_FEATURES` and the `xmeml` target had no rule table, so `classify_features` hard-errored / classified the wrong category across the 9 targets |
| **Fonts** — `test_windows_fonts.py` | 9 | `AttributeError` — `card._find_font_windows`/`_win_font_dirs`/`_win_registry_font_files`/`_IS_WINDOWS` absent; `find_font()` had no Windows branch |
| **Board** — `test_windows_annotations.py` | 30 | staged: `ImportError` on `core.models.Annotation`; the board had no `annotate` action (404) and `ShotStatus.annotations` did not exist; the contract test went red the moment the schema literal was expected in the registry |
| **Orchestrator** — `test_windows_export_conform.py` | 3 | `manju export` emitted no `conform` JSON key and wrote no conform-loss document for any produced target |

Root-cause discipline mirrors W1/W2: injected-constant/env-stub unit tests for
the Windows-only surfaces (fonts, the URI drive-colon), behavioral HTTP tests
for the board, structural XML/exactness tests for xmeml, and grep/byte pins for
the invariants (static board bytes, old-shot byte-identity, the ratemig1
`edit_rate` leak, the frozen CLI surface).

## Wave scope decisions

- **Spend exactly ONE schema budget slot.** The single new public contract is
  `manju.review.annotation/v1` (§5.5, `experimental`, owner `manju.core.models`),
  registered in `CONTRACTS.yaml` in the same change as the `ANNOTATION_SCHEMA`
  src literal so `test_fp_contracts.py` stays green both directions.
- **xmeml IMPORT plan — REJECTED_WITH_REASON.** The schema budget went to
  `review.annotation`; an xmeml *import* plan would need its own registry row.
  A writer with a machine-readable conform-loss doc is the W3 deliverable; the
  P2 AAF exit is skipped per the plan's default.
- **Subtitle model expansion (ruby / vertical / sound-cue fields) —
  DEFERRED_WITH_EVIDENCE.** No upstream producer exists in the dialogue/ASR
  pipeline, so the fields would be dead — fabrication. The honesty is carried
  instead by the new machine-readable `caption_roles`/`caption_speakers` loss
  rows (§5.4).
- **Drawing UI beyond the geometry field — DEFERRED.** The schema *stores*
  loose rect/arrow geometry so a future wave adds freehand + a canvas overlay UI
  **without a schema bump**; W3 ships list + click-to-seek + add-form only.
- **Glyph-coverage stays the honest UNKNOWN sentinel.** No font-parsing
  dependency is added; coverage is never guessed to PASS.
- **Annotation → Repair Proposal — carried, never auto-routed.** The annotation
  stores `repair_variable` verbatim (the `qc/production._VARIABLE_ROUTE` seam);
  an explicit human action, not the annotation, remains the trigger.
- **Extend the ONE owner, never fork.** conform grows `KNOWN_FEATURES` + the
  `xmeml` `_RULES` table + the frame-native drift branch (`conform.py`); fonts
  grow `find_font` (never a parallel scanner); the board grows `API_ACTIONS` +
  the serve-mode renderer with **static bytes frozen**; xmeml mirrors the
  `fcpxml.py` compile/export split and its containment guard.
- **Two deliberate pin evolutions (reviewed).** `test_fp_ttml.py` — the caption
  sub-features join `"captions"` on the *preserved* side (the TTML writer
  factually preserves both via `ttm:agent` + verbatim `x-manju:role`); every
  other feature keeps its unsupported teeth. `test_fp_ratemig1`'s `edit_rate`
  grep-pin needed **no change** — the leak was *fixed* (the board's rate now
  reads the sanctioned `ProjectConfig.frame_rate` resolver), not sanctioned.

## REPORTS paths

- `REPORTS/WINDOWS_WAVE_3_BASELINE.md` (this file)
- `REPORTS/WINDOWS_WAVE_3_COMPLETION.md`
