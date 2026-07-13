# Windows Wave 4 — Baseline (MANJU_WINDOWS_ONLY_LEAN_V3, W4: 色彩最小闭环 / colour minimal closed loop)

Plan: **MANJU_WINDOWS_ONLY_LEAN_V3** — the W4 slice, "色彩最小闭环".
Slice: **W4 only** — the plan's conditional colour ask (OCIO/LUT configuration,
a ColorTransformPlan document, HDR handling, tagged outputs), scoped down to
*only what carries its weight for a single-user pipeline*. W1 (Windows 硬 CI +
文件系统/进程安全), W2 (安装 / Portable / 升级 / Doctor) and W3 (NLE 交换、字幕、
字体、Board 审片) are landed; the Windows hard gate is **green** (run #7). Date:
2026-07-13 · Repo: `/home/user/test` (Manju) · Branch
`claude/implement-ai-advice-delegation-tvt9ms` · Base = HEAD **78aecbb** (`docs:
DECISIONS #39 — the Windows hard gate is GREEN`), which sits on the round-4 tip
**d2cfff7** (the run #7 green commit) and the W3 line before it.

## Authoring environment — and the honest limit of what it can prove

Authored on the same **Linux container** (Ubuntu Noble) as W1–W3, but W4's
honesty gap is narrower than W3's and the reasons are the point:

1. **The e2e colour evidence is real, not stubbed.** Unlike the W3 NLE/font/
   browser surfaces, colour is exercised against the *same* ffmpeg the gate runs
   — the pinned **6.1.1** is byte-for-byte the version on both CI OSes (choco on
   windows-latest, apt on ubuntu). Every tagging and transform claim below was
   reproduced on it; the e2e tests skip cleanly (`shutil.which`) where ffmpeg is
   absent, so they are green-or-skipped, never green-by-omission.
2. **No calibrated display, no colorimeter, no HDR monitor exists on any host.**
   The transform's *correctness* is asserted by ffprobe axes, byte-level pixel
   comparison and one measured sRGB→BT.709 sample — **not** by a human eye on a
   calibrated panel. This is exactly why OCIO/LUT plumbing and HDR tone-mapping
   are rejected below: there is no workflow that could validate them, so shipping
   them would fabricate correctness. Called out again in the COMPLETION's risks.
3. **No NLE / player observes the tags resolving.** `tag_outputs` writes the
   bt709/tv metadata a downstream tool is *meant* to read; that a real Premiere/
   Resolve/QuickTime then honours it (rather than re-guessing) is unverified on
   this host — the W3 NLE gap, inherited.
4. **The gate is green going in.** W4 is the **first wave developed entirely on
   a green Windows gate** (run #7, `d2cfff7`, DECISIONS #39). There is no
   inherited red tail to drive down this wave; the honest expectation is that the
   push holds the gate green (§ below), not that it shrinks a residue.

| tool | version (authoring host) |
|------|--------------------------|
| Python | 3.11.15 |
| ffmpeg / ffprobe | 6.1.1-3ubuntu5 (**identical to both CI OSes** — the pin the colour evidence rides) |
| zscale / libzimg | present in this ffmpeg (`ffmpeg -filters` lists `zscale` **and** `colorspace`) |
| calibrated display / colorimeter / HDR monitor | **none** — colour asserted by ffprobe + byte-level pixels + one measured sample, never by eye |
| NLE / player honouring the tags | **none** — the tags are written, their downstream honouring is unobserved |
| OS | Linux (Ubuntu Noble) |

## Baseline test state at the base tip (78aecbb)

W4 begins on the green tip. The authoring-host suite was green at 78aecbb
(W3's 4270 passed / 1 skipped), and — the decisive fact this wave inherits — the
hard `windows-ci.yml` gate is **terminal-green**:

| `windows-ci.yml` run | verdict | note |
|----------------------|---------|------|
| Run #1 (W1 push) | **76 F / 4016 P / 35 E** | first real execution |
| Run #7 (round-4 tip `d2cfff7`, actions run 29258059991) | **GREEN — 0 failed** | full suite + `install-smoke`, choco ffmpeg 6.1.1, PYTHONUTF8=1, `-n auto`, no skip-as-green (DECISIONS #39) |

There is no gate residue for W4 to attack. Before any code changed, a single
focused audit established the base-tip status of every colour capability the
plan asks for; its rows are this Baseline's audit table, and its empirical
findings (the zscale investigation) are the red evidence below.

## Audit summary — the colour capabilities at 78aecbb

`ALREADY_IMPLEMENTED` = the invariant already holds; `PARTIAL` = present but
incomplete; `GAP` = does not exist anywhere; `BUG` = present and *wrong*. Owner =
the single module an implementer must extend, never a parallel one.

| Capability | Status | Owner |
|------------|--------|-------|
| Tagged outputs — final/proxy carry the colour metadata they produce in substance | **GAP** — ffprobe shows `color_range`/`color_space`/`color_transfer`/`color_primaries` **all "unknown"** on our own final/proxy (reproduced fresh on the pinned ffmpeg 6.1.1); the technical-profile module honestly reports `color_known: False` about Manju's own renders. Untagged bt709-ish output is exactly what players/NLEs then guess at | `media/render._enc_params` (the final/proxy encode tail) |
| Source-colour conversion (a real colour-managed transform) | **GAP** — **no** conversion filter (`zscale`/`colorspace`/`lut3d`/`tonemap`) exists anywhere in `src`. The round-T "look" pipeline is *creative grading* (eq/curves-style, deterministic) — **not** colour management | `media/normalize._video_filter` (the one normalize seam) |
| Colour-axis facts + honest UNKNOWN | **PARTIAL** — `media/technical_profile.py` records the four axes verbatim-or-"unknown" behind a `COLOR_UNKNOWN` advisory (never guessed), but has **NO HDR awareness**: a `smpte2084`/HLG/`bt2020` source produced **no** warning although the SDR pipeline cannot tone-map it — a silent lie of omission | `media/technical_profile._diagnostics` |
| colorstats adopt-path pointer | **BUG** — `qc/colorstats.py` `compare_to_reference` advertised `adopt_via.command = "manju repair --op grade --from <ref> --dry-run"`, an op that **does not exist** (`manju repair` accepts `retime\|extend\|trim\|inout\|croppad\|voice`); a reader typing it gets an error | `qc/colorstats.py` `compare_to_reference` |
| Plan W4 ask — OCIO/LUT config, ColorTransformPlan doc, HDR handling, tagged outputs | **CONDITIONAL** — the plan marks W4 conditional: build *only* what carries its weight for a personal pipeline. Two of the four (tags, HDR advisory) are honest wins; two (OCIO/LUT, the plan doc) would be dead ceremony (see Wave scope decisions) | — |

## Red / empirical evidence — gathered before any production change

W4's red is two-layered: a **behavioral red** for the tagging half (proven on a
real render), and an **empirical investigation** that had to precede the code
because the transform chain's exact shape is not guessable — the pinned zscale's
behaviour dictates it. All of it was run against ffmpeg **6.1.1**, the exact CI
version. `tests/test_windows_color.py` was written and run first: collection
died `ImportError: cannot import name 'ColorSpec'` (the model did not exist —
the collection-level red), and the tagging half's assertion (a) is the behavioral
red below, asserted against a real render.

| # | Empirical finding (ffmpeg 6.1.1) | Consequence for the design |
|---|----------------------------------|----------------------------|
| 1 | A fresh proxy render, probed → all four colour axes **"unknown"** | the behavioral red for the tagging half; also the (a) assertion of the closed-loop e2e test |
| 2 | `zscale` **refuses** untagged yuv input without a full input-side declaration — **"code 3074: no path between colorspaces"** | each transform chain must declare the **input** matrix too (`pin`/`tin`/`min`/`rin`), not just the output side — a partial declaration is a probe-and-guess that errors |
| 3 | zimg **ignores** the yuv input declarations (`min`/`rin`) for RGB frames | the *same* fully-declared chain is safe for **RGB stills AND untagged yuv video** — verified byte-level: identical output pixels with `rin=limited`, `rin=full`, and no `rin` on a PNG. One chain covers both source classes |
| 4 | Alpha PNGs and full-range mjpeg (`yuvj`) sources pass the chain without error | the one chain survives the source classes the normalize seam actually sees |
| 5 | The conversion is **real, not a relabel**: an sRGB `(200,30,30)` still comes back `(202,40,41)` after srgb→bt709 | the transform moves pixels (the piecewise-sRGB→BT.1886 transfer difference plus yuv420 rounding) — it is colour management, not a metadata swap |
| 6 | `ffmpeg -filters` on 6.1.1 lists **both** `zscale` and `colorspace` | **zscale chosen** — full input/output declaration, one fixed chain per token → deterministic bytes for the same source bytes |

## Wave scope decisions

- **One opt-in `color:` block, no new public schema.** The wave adds a single
  optional `project.yaml` field (`ColorSpec`: `tag_outputs`, `input_transform`),
  not a contract id — the plan's one schema budget was spent in W3 on
  `manju.review.annotation/v1`. Absent = today, byte-identical everywhere.
- **`color: {}` (all-default) is REJECTED at validation.** Absent is the off
  switch; an all-default block can only mislead (the S4 empty-list-rejected
  precedent). Only non-default keys serialize (the `edit_rate`/`cache_toolchain_
  keys` drop-default precedent).
- **Tag what IS; never convert silently.** `tag_outputs` states the profile the
  pipeline already produces (yuv420p, swscale bt709-family defaults, limited
  range) — it converts nothing. `input_transform` is the *only* pixel-moving path
  and it is a **declared** truth (the user states the source colour), never a
  probe-and-guess.
- **Fold into cache keys only when active.** Both facts re-key only when set (the
  look/S4 fold-only-when-active precedent), so every existing project without
  `color:` keys byte-identically.
- **OCIO/LUT config plumbing — REJECTED_WITH_REASON.** No LUT producer, no
  calibrated-monitor workflow exists for a single user; dead config would
  fabricate correctness. The declared enum carries the same honesty at zero
  config surface.
- **ColorTransformPlan document — REJECTED_WITH_REASON.** A new derived doc for
  two booleans is ceremony — the facts live in `project.yaml`, the diagnostics in
  the technical profile, the tags in the output itself; a derived report is never
  a build input (the README discipline).
- **HDR tone-mapping — REJECTED (advisory instead).** No HDR display/QC path
  exists to validate a tone-map against; the honest move is an `HDR_SOURCE`
  **warning** that states the limit, never a guessed curve. Unknown axes stay
  `COLOR_UNKNOWN`'s business (never guessed into HDR).
- **Fix the colorstats pointer, don't build the op.** `manju repair --op grade`
  is out of the minimal loop; the pointer was the lie, so the *pointer* is fixed
  (`implemented: False`, no command, the real manual path described).

## REPORTS paths

- `REPORTS/WINDOWS_WAVE_4_BASELINE.md` (this file)
- `REPORTS/WINDOWS_WAVE_4_COMPLETION.md`
