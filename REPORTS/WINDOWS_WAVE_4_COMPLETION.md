# Windows Wave 4 — Completion (MANJU_WINDOWS_ONLY_LEAN_V3, W4: 色彩最小闭环 / colour minimal closed loop)

Plan **MANJU_WINDOWS_ONLY_LEAN_V3**, wave **W4** (the conditional colour ask).
Branch `claude/implement-ai-advice-delegation-tvt9ms`, base = HEAD **78aecbb**
(`docs: DECISIONS #39 — the Windows hard gate is GREEN`), itself on the run #7
green tip **d2cfff7**. Authored on a Linux container (Python 3.11.15, ffmpeg
**6.1.1** — the exact version on both CI OSes). W4 is the **first wave developed
entirely on a green Windows gate**: there is no red tail to drive down, only one
tightly-scoped honest colour loop to add without disturbing byte-identity
anywhere. All **22** new tests pass on the authoring host; the real-host run is
the gate's next execution.

## Baseline

At the base tip 78aecbb the authoring-host suite was green (W3's 4270 passed / 1
skipped) and the hard `windows-ci.yml` gate was **terminal-green** (run #7,
`d2cfff7`, actions run 29258059991: 0 failed, full suite + `install-smoke`). A
single audit established the base-tip status of every colour capability before
any code changed: tagged outputs **GAP** (all four ffprobe axes "unknown" on our
own renders; `color_known: False`), source conversion **GAP** (no zscale/
colorspace/lut3d/tonemap anywhere in `src` — the round-T look is creative
grading, not colour management), HDR awareness **PARTIAL** (`COLOR_UNKNOWN` but
no HDR warning), and the colorstats adopt-path a **BUG** (`manju repair --op
grade` does not exist). The zscale investigation — the empirical red — is in
`REPORTS/WINDOWS_WAVE_4_BASELINE.md`.

## Audit

Decision key: **Implement** (W4 built it) · **Kept** (already satisfied, pinned)
· **Deferred** (with evidence, out of W4 scope) · **Rejected** (per a plan rule)
· **N-t-d** (nothing to do / vacuous pass).

| Capability | Decision | Evidence |
|------------|----------|----------|
| `color:` block — one opt-in `ColorSpec` (`tag_outputs`, `input_transform`) | **Implement** | `core/models.py` `ColorSpec`; parsed via the `ProjectConfig.color` field; dumps only non-default keys (drop-default precedent); **`color: {}` rejected** at validation (absent is the off switch — the S4 empty-list precedent) |
| Tagged final/proxy — state what the pipeline produces in substance | **Implement** | `render._COLOR_TAG_ARGS` appended by `_enc_params(target, color)` — `-color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv`; tags yuv420p / swscale bt709-family / limited range; **converts nothing** |
| Declared source→bt709 conversion at the one normalize seam | **Implement** | `normalize._COLOR_TRANSFORM_CHAINS` (one fixed zscale chain per token, full input+output declaration), applied in `_video_filter` after pad (even geometry), before `format=yuv420p`; **declared, never probed** |
| Cache-key participation, fold-only-when-active | **Implement** | `input_transform` folds `("color_in", token)` into `_segment_cache_key` **only when set** (boundary keys inherit via the two neighbour segment keys); `tag_outputs` rides the hashed encoding list. Every project without `color:` keys byte-identically |
| HDR source advisory in the technical profile | **Implement** | `technical_profile._diagnostics` emits `HDR_SOURCE` (severity **warning**) when transfer ∈ {`smpte2084`, `arib-std-b67`} or primaries == `bt2020`, naming the offending axes; unknown axes stay `COLOR_UNKNOWN`'s business |
| colorstats adopt-path honesty | **Implement** | `qc/colorstats.py` `adopt_via` now carries `implemented: False`, `command: None` and a note describing the real manual path (grade externally → register as a NEW take, append-only → re-run QC); the `--op grade` string may never reappear |
| doctor colour preflight | **Implement** | `build/doctor.py` `color_transform` advisory row probes `ffmpeg -filters` for zscale **only when** a project declares `input_transform`; `⚠` names the fix; **never gates doctor's exit code** |
| Facts-only `profile_digest` unmoved by the advisory | **Kept** | `profile_digest` hashes the facts block only; the new diagnostic is outside it by design (test-proven deterministic + unmoved) |
| Colour-axis facts + `COLOR_UNKNOWN` sentinel (never guessed) | **Kept** | the four-axis verbatim-or-"unknown" recording is untouched; HDR never guessed, UNKNOWN never guessed to PASS |
| OCIO/LUT configuration plumbing | **Rejected** | personal single-user pipeline — no calibrated-monitor workflow, no LUT producer; dead config would fabricate correctness; the declared enum carries the same honesty at zero config surface |
| ColorTransformPlan derived document | **Rejected** | a new derived doc for two booleans is ceremony — facts in `project.yaml`, diagnostics in the technical profile, tags in the output itself; a derived report is never a build input |
| HDR tone-mapping | **Rejected** | no HDR display/QC path exists to validate a curve against — the `HDR_SOURCE` warning states the limit instead of guessing |
| `manju repair --op grade` op | **Rejected** | out of the minimal loop; the pointer was the lie, so the pointer was fixed |
| Per-clip transform overrides | **Rejected** | project-wide declaration only — a single user's sources are homogeneous per project; per-clip would multiply key/UI surface for no present need |

## Red tests

22 new tests in one file (`tests/test_windows_color.py`), red-first, one row per
**RED CLUSTER**. Collection died `ImportError: ColorSpec` before any production
change (collection-level red); the tagging half's red is **behavioral** — all
four axes "unknown" proven on a real render, asserted as the (a) half of the
closed-loop test.

| Cluster | Old (base) | New behavior pinned |
|---------|-----------|---------------------|
| model / serialization byte-identity (4) | `ImportError: ColorSpec` | no `color:` → no `color` key in any dump (edit_rate precedent); a non-default block dumps only its non-default keys; `color: {}` **rejected**; the `input_transform` enum validated |
| enc-params literal + tag append (2) | RED | the historical final/proxy encode literals pinned byte-for-byte; `tag_outputs` appends exactly the 8 bt709/tv args as a suffix (prefix untouched); `input_transform` alone does **not** tag (two independent switches) |
| segment-key identity / distinctness (2) | RED | `color_in=None` keys byte-identically to no-arg; `srgb_to_bt709`/`p3_to_bt709`/base are three content-distinct keys, and the same token re-keys deterministically |
| chain-shape pin (1) | RED | exactly the two tokens; each chain declares the **full input side** (`pin`/`tin`/`min`/`rin`) and the bt709/limited output side; P3 uses `smpte432` primaries |
| e2e closed loop, real ffmpeg 6.1.1 (5) | behavioral RED (all-axes-unknown) | (a) untagged today honestly → all axes "unknown", `color_known: False`; (b) `tag_outputs` → all four axes bt709/tv, `color_known: True`, no `COLOR_UNKNOWN`; content-key **flip/flip-back** returns the original key byte-for-byte; the transform runs on a still **and** untagged yuv video for both tokens; no-transform is command-stable (no `zscale` node) |
| doctor probe conditionality (1) | RED | undeclared → **no** `color_transform` check even probed; declared → one advisory row, `ok: True` (never gates), naming the token and reporting zscale **available** on the pinned ffmpeg |
| HDR advisory matrix (5 param + 1 digest) | RED | PQ / HLG transfers and `bt2020` primaries each warn alone (severity warning, axes named); SDR / unknown never warn; the advisory does not move `profile_digest` (facts-only, deterministic) |
| colorstats adopt_via pin (1) | RED | the compare document carries `implemented: False`, no command; no `"command": "...--op grade..."` string may reappear |

## Implementation

### Production source files (6, within the plan's ≤10)

1. **`src/manju/core/models.py`** — `ColorSpec` (`tag_outputs: bool`,
   `input_transform: srgb_to_bt709|p3_to_bt709|None`) + the optional
   `ProjectConfig.color` field. `ColorSpec` dumps only its non-default keys;
   `color: {}` (all-default) is **rejected** at validation — absent is the off
   switch (the S4 empty-list-rejected precedent). The existing single wrap-
   serializer that already drops `edit_rate`/`cache_toolchain_keys` when default
   also drops `color` when absent — one owner, no second serializer, so every
   existing project dumps byte-identically.
2. **`src/manju/media/normalize.py`** — `_COLOR_TRANSFORM_CHAINS` (the two
   declared zscale chains) applied at the ONE normalize seam (`_video_filter`,
   after pad — even geometry — before `format=yuv420p`). Full input+output
   declaration per chain (the pinned zscale refuses untagged yuv without an input
   matrix — "no path between colorspaces"):
   `srgb_to_bt709: zscale=pin=bt709:tin=iec61966-2-1:min=bt709:rin=limited:p=bt709:t=bt709:m=bt709:r=limited`;
   `p3_to_bt709: zscale=pin=smpte432:tin=iec61966-2-1:min=bt709:rin=limited:p=bt709:t=bt709:m=bt709:r=limited`
   (Display P3 = `smpte432` primaries + the same `iec61966-2-1` transfer as
   sRGB). `color_transform=None` (every existing caller) leaves the chain
   byte-identical — no zscale node.
3. **`src/manju/media/render.py`** — `_COLOR_TAG_ARGS` + `_enc_params(target,
   color)` append the bt709/tv tags when `tag_outputs`. Because `_final_key_
   payload` hashes the encoding list **verbatim**, flipping the switch honestly
   re-keys (re-renders) the final/proxy, and flipping it back returns the
   original key byte-for-byte. `render_timeline` reads the config once
   (`_color_spec`) and threads `color_in` through `_build_segment` /
   `_build_boundary_segment` (both xfade handle layers) / `_assemble_video_
   pieces`; `input_transform` folds into `_segment_cache_key` only when set, and
   `_boundary_cache_key` inherits it via the two neighbour segment keys.
   `_trim_segment` is deliberately untouched (it operates on already-converted
   segments). The transform deliberately does **not** touch title cards/kenburns
   (synthesized content, not camera sources), the animatic sketch path, or
   normalize callers outside the build (none exist).
4. **`src/manju/media/technical_profile.py`** — `_diagnostics` gains
   `HDR_SOURCE` (severity warning) when transfer ∈ {`smpte2084`, `arib-std-b67`}
   or primaries == `bt2020`, naming the offending axes. Unknown axes stay
   `COLOR_UNKNOWN`'s business — HDR is never guessed. `profile_digest` is
   facts-only by design, so the new diagnostic cannot move any existing digest.
5. **`src/manju/qc/colorstats.py`** — `compare_to_reference`'s `adopt_via` now
   carries `implemented: False`, `command: None` and a note describing the real
   manual path (grade externally → register as a NEW take, append-only → re-run
   QC). The nonexistent `manju repair --op grade` string is gone and pinned out.
6. **`src/manju/build/doctor.py`** — a `color_transform` advisory row probes
   `ffmpeg -filters` for zscale **only when** a project declares
   `input_transform` (zero cost for every other project); `⚠` names the fix (a
   libzimg-less ffmpeg build fails at render otherwise); it **never gates**
   doctor's exit code.

### Tests

- `tests/test_windows_color.py` (**22**) — model/serialization byte-identity
  (4), enc-params literal pin + tag append (2), segment-key identity/distinctness
  (2), chain-shape pin (1), the e2e closed loop with real ffmpeg (5: untagged-
  today-honestly + tagged-after-opt-in + `color_known: True`; content-key flip/
  flip-back; transform on still + untagged video for both tokens; no-transform
  command stability), doctor probe conditionality (1), the HDR advisory matrix (5
  parametrized + 1 digest determinism), colorstats adopt_via pin (1). Red-first:
  the file was written and run before any production change (`ImportError:
  ColorSpec` — collection-level red); the tagging half's red is behavioral
  (all-axes-unknown on a real render).

### CLI / contracts

- **No new schema.** The plan's one schema budget was spent in W3 on
  `manju.review.annotation/v1`; W4 adds a `project.yaml` *field*, not a contract
  id. `CONTRACTS.yaml`'s `project.yaml` document entry's **notes** are extended
  with the W4 `color` field semantics — no new contract id, so
  `test_fp_contracts.py` stays green both directions.
- **No new command surface.** `color:` is a config field; the frozen CLI surface
  (`tests/fixtures/cli_surface.json`) is unchanged. `doctor` gains one
  conditional advisory row, not a command.

### Design deviations (honest)

- **OCIO/LUT plumbing REJECTED_WITH_REASON** — no producer, no calibrated-monitor
  workflow for one user; dead config would fabricate correctness; the declared
  enum is the same honesty at zero config surface.
- **ColorTransformPlan doc REJECTED_WITH_REASON** — ceremony for two booleans;
  the facts already live in three honest places (config / profile / the output
  tags); a derived report is never a build input.
- **HDR tone-mapping REJECTED** — no HDR display/QC path to validate a curve; the
  `HDR_SOURCE` warning states the limit instead of guessing.
- **`manju repair --op grade` REJECTED** — out of the minimal loop; the pointer
  was the lie, so the pointer was fixed.
- **Per-clip overrides REJECTED** — project-wide declaration only; a single user's
  sources are homogeneous per project.

## Compatibility

- **Every project without `color:` keys byte-identically.** The wrap-serializer
  drops `color` when absent (the `edit_rate`/`cache_toolchain_keys` precedent);
  `_enc_params` returns the historical literals with `color=None`;
  `_segment_cache_key` returns the base key with `color_in=None` — all
  test-proven.
- **The tags convert nothing.** They state what the pipeline already produces in
  substance (yuv420p, swscale bt709-family defaults, limited range); the substance
  is unchanged whether or not the switch is on.
- **`tag_outputs` re-keys honestly and reversibly.** Flipping it on re-keys the
  final/proxy (the encoding list is hashed verbatim — the output bytes DO change);
  flipping it back returns the ORIGINAL key byte-for-byte (proven).
- **The transform never touches synthesized content.** Title cards, kenburns and
  the animatic sketch path are excluded — only camera-sourced segments are
  converted; `_trim_segment` is untouched (post-conversion).
- **The facts digest is unmoved.** `HDR_SOURCE` is a diagnostic, outside
  `profile_digest` by design — no existing profile document re-digests.
- **doctor is zero-cost when undeclared.** The `color_transform` probe runs only
  for a project that declares `input_transform`, and never gates the exit code.

## Verification

Targeted suites on the authoring host (Linux, full tooling):

- The new file — **22 passed** (`tests/test_windows_color.py`).
- **Guard suites green after the change:** `test_fp_profile`, `test_fp_contracts`,
  `test_fp_ratemig1`, `test_transitions_looks`, `test_fp_cli_snapshot`
  (**105**), `test_c15_color` + `test_fp_colorstats_wall` (**17**),
  `test_windows_doctor` (**15**) — the frozen-surface, contract, profile, look,
  colorstats and doctor neighbours all hold.

Full suite (`pytest -n auto`, authoring host): **4292 passed, 1 skipped, 0 failed (504.84s) — the 4270 gate-green tip plus the 22 new W4 tests, zero regressions**.

Windows CI (`windows-ci.yml` full suite + `install-smoke`): **run #9 triggered by the 103be30 push (runs #7/#8 both green; in progress at report time — verdict recorded in DECISIONS when it lands)**.
Honest expectation: **green**. W4 is the first wave developed entirely on a green
gate (run #7); this push triggers the next run. The wave adds **no OS-conditional
code** — the colour path is identical on both platforms and rides the identically
pinned ffmpeg 6.1.1 — and every e2e test skips cleanly (`shutil.which`) where
ffmpeg is absent, so nothing here is green-by-omission. The expectation is that
the gate stays green, not that it shrinks a residue.

Method: `python -m pytest` only; red-first pins for every new behavior (the file
ran red — `ImportError: ColorSpec` — before any production change); no existing
test deleted or weakened.

## Skipped / Rejected

| Item | Reason |
|------|--------|
| OCIO/LUT configuration plumbing | **REJECTED_WITH_REASON** — personal single-user pipeline; no calibrated-monitor workflow, no LUT producer; dead config fabricates correctness; the declared enum carries the same honesty at zero config surface |
| ColorTransformPlan derived document | **REJECTED_WITH_REASON** — ceremony for two booleans; the facts live in `project.yaml`, the diagnostics in the technical profile, the tags in the output itself; a derived report is never a build input |
| HDR tone-mapping | **REJECTED** — no HDR display/QC path exists to validate a curve; the `HDR_SOURCE` warning states the limit instead of guessing |
| `manju repair --op grade` op | **REJECTED** — out of the minimal loop; the pointer was the lie, so the pointer was fixed (`implemented: False`, no command) |
| Per-clip transform overrides | **REJECTED** — project-wide declaration only; a single user's sources are homogeneous per project; per-clip would multiply key/UI surface for no present need |

## Final

**Commit SHA:** `103be30` (W4 colour loop + tests; this report and the
DECISIONS follow-up land in the docs commit).

**Remaining risks:**

1. **No calibrated display / colorimeter / HDR monitor exists on any host.** The
   transform's correctness is asserted by ffprobe axes, byte-level pixel identity
   and one measured sRGB→BT.709 sample `(200,30,30)`→`(202,40,41)` — **not** by a
   human eye on a calibrated panel. This is precisely why OCIO/LUT and HDR
   tone-mapping were rejected: there is no workflow that could validate them.
2. **No NLE / player has been observed honouring the tags.** `tag_outputs` writes
   the bt709/tv metadata a downstream tool is *meant* to read instead of guessing;
   that a real Premiere/Resolve/QuickTime then honours it is unverified on this
   host (the inherited W3 NLE gap).
3. **`input_transform` is a DECLARED truth, not a probe.** If the user's sources
   are not actually sRGB / Display P3, the conversion is wrong-by-declaration —
   by design (nothing guesses the source colour). The doctor row and the enum are
   the only guard rails; a mis-declaration produces a clean-but-wrong render.
4. **The zscale/libzimg dependency is new at render time for `input_transform`.**
   A libzimg-less ffmpeg build fails the transform render; doctor's advisory names
   this before the first failed render, but it does not gate, so a user who
   ignores `⚠` meets the failure at render. The authoring host and the pinned CI
   ffmpeg 6.1.1 both carry zscale.
