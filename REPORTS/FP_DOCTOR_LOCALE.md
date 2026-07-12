# FP Loop Y2 — `manju doctor`: locale-meta + interchange-exit advisory rows

Roadmap item 4/8 polish. The declared locale meta (U2) and the interchange
exits (OTIO/EDL/FCPXML/TTML/WebVTT) now get `manju doctor` probes, just like the
provider manifests and toolbelt did. **Advisory only — zero new validation
logic, exit-code policy unchanged.**

## Where doctor lives (launch-gate audit)

The probe engine is the pure function `run_doctor(project)` in
**`src/manju/build/doctor.py`** — it returns `{"checks": [...], "ok": bool}` and
does no printing/exiting. `src/manju/cli.py:doctor` (line ~6748) is a thin
client: `from .build.doctor import run_doctor; info = run_doctor(project)`, then
`raise typer.Exit(0 if info["ok"] else 1)`.

**The launch gate did NOT bind**: the probe logic lives fully in
`build/doctor.py`, not in `cli.py`. All work landed in `build/doctor.py`;
`cli.py` was never touched (a parallel loop owns it).

## Row grammar matched (the existing probe idiom)

Every check is `{"name": str, "ok": bool, "detail": str, "line": str}`, appended
by the in-function `add(name, ok, detail, line)` helper; `line` always starts
with one of the four glyphs `✓ ✗ • ⚠`. The **aggregate `ok`** is gated ONLY by
env tools (ffmpeg/ffprobe), provider manifests, and `project_check` — every
other probe (toolbelt/disk/build-lock) is informational and never gates. The new
rows follow that informational idiom exactly.

## New rows (all inside the `if project is not None:` block; none gate `ok`)

### `locale:<lang>` — one per `locales/<lang>/` dir (via `core.locale.list_locales`)
Facets, all sourced from `core.locale` (**consult, never re-implement**):
- `lines.yaml 解析正常(N 行)` via `load_lines` (it raises on malformed YAML) — ⚠ on failure.
- `meta.yaml` via `load_locale_meta` **in a try** — on failure the row carries
  `load_locale_meta`'s EXACT structured `ProjectError` message **verbatim** (the
  S4/bridge "consult, don't copy" precedent); ⚠, row `ok=False`.
- `direction` echoed only when the human declared one (`direction: rtl|ltr`);
  never inferred.

### `export:<fmt>` — one per interchange exit
Present + **stdlib well-formedness** probe only (no semantic re-validation —
`exporters.conform` owns semantics/drift, and the row says so):

| fmt    | canonical path                | well-formed probe (stdlib) |
|--------|-------------------------------|----------------------------|
| otio   | `exports/otio/<name>.otio`    | `json.loads` + top-level `OTIO_SCHEMA` present |
| edl    | `exports/edl/<name>.edl`      | first line starts `TITLE:` |
| fcpxml | `exports/fcpxml/<name>.fcpxml`| `ET.fromstring(bytes)` |
| ttml   | `captions/captions.ttml`      | `ET.fromstring(bytes)` |
| vtt    | `captions/captions.vtt`       | first line starts `WEBVTT` |

- **Absent ⇒ `•`** ("absence is a fact, not a failure" — the toolbelt precedent), `ok=True`.
- **Present + well-formed ⇒ `✓`**, `ok=True`, with a "语义/丢帧以 conform 为准" pointer.
- **Present + malformed/unreadable ⇒ `⚠`**, `ok=False` (flagged on its own row only).

> XML gotcha (pinned in the code + a test): FCPXML/TTML emit an `<?xml
> encoding="utf-8"?>` declaration, so the probe feeds `ET.fromstring` **bytes**
> (`read_bytes()`). A decoded `str` with an encoding decl raises `ValueError`
> in ElementTree.

## Exit-code policy — audited & pinned UNCHANGED

`doctor` exits `0` iff `run_doctor()["ok"]` is True, and `ok` is gated only by
the documented set (env tools + manifests + `project_check`). Every new row is
informational: it sets its OWN `ok` field honestly (⚠/`False` for a
corrupt/broken artifact) but **never touches the aggregate `ok`**. Test
`test_exit_code_policy_unchanged_even_with_broken_advisory` pins this — with a
broken `meta.yaml` AND a corrupt `.otio` present, the aggregate `ok` still equals
the gating-only verdict.

## Tests (`tests/test_fp_doctor_locale.py`, NEW — red-first)

Red-first captured (5 failed on missing rows) → green (6 passed). Real exporters
(`export_otio/edl/fcpxml/ttml/captions`) produce the healthy artifacts so the
probes are validated against genuine bytes, incl. a CJK project name
(`雨夜便利店.otio`).

1. new rows obey the frozen `{name, ok, detail, line}` + glyph grammar;
2. healthy: ✓ per locale (direction echoed only when declared) + ✓ per exit;
3. broken `meta.yaml` → row carries `load_locale_meta`'s message **verbatim**
   (captured via `pytest.raises`, proving delegation not duplication);
4. corrupt `.fcpxml` → ⚠ flagged, points at conform, aggregate `ok` unchanged;
5. absent exports → `•` absent-not-error;
6. exit-code policy unchanged with broken advisory rows present.

## Verification

- `tests/test_fp_doctor_locale.py` (NEW, 6) + `tests/test_doctor.py` (existing, 8,
  green — no existing test weakened) + `test_fp_cli_snapshot.py` (surface
  unmoved — no new command) + `test_fp_ratemig1.py` → **42 passed**.
- No new deps, no network, no git ops, no new schema id.

## Note (shared worktree)

The worktree is shared with concurrent sibling FP loops (in-flight edits to
`cli.py`, `conform.py`, `edl.py`, `fcpxml.py`; new untracked `edl_import.py`,
`test_fp_edl_import.py`, ...). One sibling's `edl_import.py` is not yet
classified in `conform.py`, so `tests/test_fp_conform.py` currently fails —
**sibling-caused, unrelated to this change** (this loop added no exporter
module; it is not in this loop's verification set).
