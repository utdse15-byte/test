# FP Loop Z1 — the `qc/colorstats.py` PIL adapter wall

The optimization audit found a **confirmed packaging defect, verified at every
site**: `qc/colorstats.py:82` did a bare `from PIL import Image`; Pillow was
declared **NOWHERE** in `pyproject.toml`; `build/doctor.py` carried **no PIL
probe**; and `core/toolchain.py:93` already tracked `("PIL", "Pillow")` in
`_KEY_DEPS`. On a clean `pip install manju` (no toolbelt extras), the color-stats
path crashed with a **raw `ModuleNotFoundError`** while `manju doctor` reported
green — a silent, undeclared hidden dependency.

This loop converts that hidden transitive into a **declared, versioned optional
extra behind the house adapter wall**, mirroring the closest precedents in the
tree. Branch `claude/cost-optimization-strategy-cjfmn5`. Red-first, targeted
pytest only. No base deps added; no network; no git ops.

## The wall pattern mirrored

The house has a consistent `*Unavailable(RuntimeError)` family for a missing
**optional** dependency (absence degrades structurally, never a raw traceback):

| Precedent | Where | Message names |
|---|---|---|
| `ExporterUnavailable(RuntimeError)` | `exporters/native_draft.py:27` | `pip install <lib>` |
| `TtsUnavailable(RuntimeError)` | `providers/tts.py:44` | — |
| `AsrUnavailable(RuntimeError)` | `providers/asr.py:37` | — |
| `PreviewUnavailable(RuntimeError)` | `media/ttspreview.py:23` | — |
| **edge_tts wall** (closest *message* precedent) | `providers/edge_tts.py:66-71` | `pip install 'manju[edgetts]'` |

`qc/colorstats.py` had **no** error class and **no** live callers (grep across
`src/`: only doc-comments in `board/board.py`; the sole exercisers are
`tests/test_c15_color.py`). So there was no established "what callers catch" —
the guidance defaults to *mirror the closest precedent*. The **edge_tts** wall
is the closest by message shape (it names a `manju[<extra>]` install), and the
`*Unavailable(RuntimeError)` classes are the closest by type. This loop adds the
qc-domain member **`ColorStatsUnavailable(RuntimeError)`**, whose message is
modelled on edge_tts but **bilingual** (house style, e.g. the doctor rows and
`cli._fail` lines). As a `RuntimeError` subclass it is rendered as one clean line
by the CLI's existing `RuntimeError`/`_fail` handlers — never a traceback.

### The exact bilingual message

```
颜色统计需要 Pillow(PIL)做精确整数直方图,但 Pillow 未安装 —— 运行 pip install "manju[colorstats]" 安装该可选依赖后重试(其余 QC 检查与交付出口不受影响)。 Pillow is not installed: colorstats needs it for exact integer channel histograms — install the optional extra with pip install "manju[colorstats]" (the rest of QC and every delivery exit are unaffected).
```

Chained via `raise ColorStatsUnavailable(...) from exc` so the original
`ImportError` survives as `__cause__` (never swallowed).

## What shipped (all additive)

| File | Change |
|---|---|
| `src/manju/qc/colorstats.py` | New `class ColorStatsUnavailable(RuntimeError)` (house `*Unavailable` family). `color_stats()` guards `from PIL import Image` in a `try/except ImportError` → raises `ColorStatsUnavailable` with the bilingual message above, `from exc`. `compare_to_reference()` routes through `color_stats`, so it walls identically (pinned). Everything downstream of the import is byte-identical. |
| `pyproject.toml` | New `colorstats = ["Pillow>=9.3"]` extra. **NOT** added to base `dependencies`. **NOT** added to `dev` (dev does not aggregate the toolbelt extras — see below). |
| `src/manju/build/doctor.py` | One `Pillow` probe row in the toolbelt block, in the existing `add()` idiom: `add("Pillow", True, …)` — `ok` hard-wired `True`, glyph `✓`/`•`, so it is **informational and never gates** the aggregate `ok` (matches `pyJianYingDraft`/`tesseract`/`chromium`). Uses the already-imported `_ilu.find_spec("PIL")`, guarded against `ValueError`/`ImportError` for poisoned entries. |
| `tests/test_fp_colorstats_wall.py` | 8 new pins (below). |
| `REPORTS/FP_COLORSTATS_WALL.md` | This report. |

## Pillow floor-version evidence (`>=9.3`)

The API surface `color_stats` uses is `Image.open` / `im.convert("RGB")` /
`rgb.size` / `rgb.histogram()` — all present since Pillow 1.x, so **the API is
not the binding constraint**. The binding constraint is the project's
`requires-python = ">=3.11"`: the *first* Pillow release shipping CPython-3.11
wheels is **9.3.0** (2022-10-29; 9.2.0 predates the 3.11 final release and ships
no `cp311` wheel). The task hypothesised `>=9`, but `>=9` would resolve to
9.0/9.1/9.2 on a 3.11 floor with no matching wheel (sdist-build only), so
**`>=9.3` is the honest, evidence-based floor**. Verified in-env: Pillow 12.3.0
on Python 3.11.15 exercises all four calls (`open/convert/size/histogram` →
`(2, 2)`, 768-bin histogram). `core/toolchain.py:93` already names the pair
`("PIL", "Pillow")`, consistent with this extra.

## How CI currently gets PIL (item c — verified, unchanged by this loop)

- **`.github/workflows/ci.yml`** (the one acceptance gate) installs
  `pip install -c constraints.txt -e ".[dev,jianying,capcut,mcpvideo,edgetts]"`
  then runs the **full** suite (`pytest -q`), which includes `test_c15_color.py`
  (imports PIL). Pillow is therefore obtained **purely transitively** through the
  media-handling toolbelt libs (`jianying`/`capcut`/`mcpvideo`) — it is declared
  in **no** extra and pinned in **no** `constraints.txt` line (constraints only
  pins the core runtime deps: pydantic/typer/PyYAML + their transitives). A
  silent, uncontrolled transitive — exactly the defect.
- **`.github/workflows/xplat.yml`** (informational, `continue-on-error`) installs
  only `.[dev]` and deliberately runs a **media-free subset** that excludes the
  colorstats suite, so it never imports PIL — which is why its thinner install
  works today.
- **`dev` does NOT aggregate the other extras** (`dev = ["pytest>=7.4",
  "hypothesis>=6.100"]`), so per the audit rule the `colorstats` extra was
  **left out of `dev`**. CI's `.github/workflows/*` are outside this loop's file
  cap and were **not modified**; the gating job keeps its transitive Pillow, so
  `test_c15_color.py` stays green there. The new extra's value is for **clean
  end-user installs** (`pip install "manju[colorstats]"`) and as the audit-
  sanctioned *declaration* of the previously hidden dependency.

## Doctor row does not gate (verified)

`test_doctor.py::expected_ok` / `test_fp_doctor_locale.py::gating_ok` both gate
`ok` on **only** `ffmpeg`/`ffprobe`/`project_check`/`provider_manifest`/`provider:*`.
The new `Pillow` row (`ok=True` always, `✓`/`•` glyph, 4 canonical keys, line
starts with a valid glyph) satisfies the shape assertions and is invisible to
the aggregation. No suite asserts an exact row count or name-set, so the extra
row is safe. Pinned by `test_doctor_pillow_row_is_informational_never_gates`.

## Tests (red → green)

Red-first: the pre-implementation run is a **collection-time `ImportError`**
(`cannot import name 'ColorStatsUnavailable'`) — the documented house red-first
convention (cf. `test_dr02_*` / `test_dr03b_*` docstrings). Captured in
`scratchpad/RED_z1.txt`.

| Test | Pins |
|---|---|
| `test_missing_pillow_raises_structured_unavailable` | poisoned PIL → `ColorStatsUnavailable`, `__cause__` is the chained `ImportError` |
| `test_wall_message_is_bilingual_and_names_the_extra` | message contains `pip install "manju[colorstats]"`, "Pillow", and CJK chars |
| `test_compare_path_also_walls` | `compare_to_reference` walls too (routes through `color_stats`) |
| `test_unavailable_is_a_runtimeerror_family_member` | `issubclass(ColorStatsUnavailable, RuntimeError)` — CLI renders it cleanly |
| `test_doctor_pillow_row_present_when_installed` | `✓ Pillow` row, `ok True` (present-when-installed) |
| `test_doctor_pillow_row_is_informational_never_gates` | poisoned PIL → `• Pillow`, `ok True`, aggregate `ok` unaffected |
| `test_doctor_cli_reports_pillow_as_structured_data` | `manju doctor --json` (CliRunner) → structured Pillow check, no traceback, exit 0 |
| `test_doctor_cli_no_traceback_when_pillow_absent` | poisoned PIL via CLI → no `Traceback`/`ModuleNotFoundError`, structural degrade, exit 0 |

Absence is simulated with the house `sys.modules` poisoning idiom
(`monkeypatch.setitem(sys.modules, "PIL", None)` — the same mechanism
`tests/test_native_draft.py:222` uses for `pyJianYingDraft`). Pillow IS installed
here, so `test_c15_color.py` stays green.

**Verification (all green):** `test_fp_colorstats_wall.py` (8) + `test_c15_color.py`
(11) + `test_fp_doctor_locale.py` + `test_doctor.py` + `test_fp_cli_snapshot.py`
+ `test_fp_ratemig1.py` → **59 passed**. Broader sweep (`test_cli.py`,
`test_fp_toolkeys.py`) → 66 passed. No existing test deleted or weakened.

## Deviation

The audit's illustrative `manju qc colorstats` **command does not exist** — grep
confirms colorstats has **no CLI command and no live caller** (only
`test_c15_color.py`), and `src/manju/cli.py` is **outside this loop's file cap**,
so no command could be wired to exercise "the CLI path returns the structured
error" for colorstats directly. The requirement is instead satisfied on the CLI
surface this loop *does* touch — `manju doctor` (CliRunner): it now reports PIL
state as **structured JSON with no traceback** even when PIL is absent
(`test_doctor_cli_*`), and the structured-error-not-a-traceback guarantee for the
color-stats function itself is proven at the unit boundary the CLI would hit
(`ColorStatsUnavailable`, a `RuntimeError` the existing `_fail`/RuntimeError
handlers render as one line). This is the faithful, in-cap realization of the
requirement.
