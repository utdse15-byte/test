# FP Loop U2 — locale-aware TTML

Roadmap item 4 remnant: the TTML writer had a `lang: str = ""` slot (S1) that
no caller ever fed. The one caller that *has* a declared language — the locale
overlay build — now writes a `captions.ttml` sibling with `xml:lang == <locale
id>`, plus an OPTIONAL human-declared text `direction`. Branch
`claude/cost-optimization-strategy-cjfmn5`. Red-first, targeted pytest only.

## What shipped (all additive)

| File | Change |
|---|---|
| `src/manju/exporters/ttml.py` | `compile_ttml(..., direction: str \| None = None)` keyword-only param + `_div_open` helper. `"rtl"` → `tts:direction="rtl"` + `tts:unicodeBidi="embed"` on the content `<div>`; `"ltr"` → `tts:direction="ltr"` only; `None`/absent → **byte-identical** bare `<div>`. Honesty note in the module docstring updated (RTL is now a *declared* overlay, never inferred). |
| `src/manju/core/locale.py` | `load_locale_meta(project, lang)` reads OPTIONAL `locales/<lang>/meta.yaml`. Missing/empty → `{}`. Closed field set `{direction}`; `direction ∈ {rtl, ltr}`. Unknown key / bad direction / non-mapping → **structured `ProjectError`** (S4 `cache_toolchain_keys` precedent: names allowed set + offending value + why). Direction is EXPLICIT human declaration — never a CLDR/lang-code guess. |
| `src/manju/build/locale_build.py` | `export_locale_captions` writes the third sibling `captions.ttml` via `compile_ttml(timeline, lang=lang, max_chars_per_line=…, direction=load_locale_meta(...).get("direction"))`, same determinism + line-budget discipline as its srt/ass siblings, and returns an extra `"ttml"` key. |
| `tests/test_fp_ttml_locale.py` | 21 new pins (below). |

## graph.py consumption-site verification (read-only; graph.py NOT modified)

`build/graph.py:1726-1732` is the only engine caller:

```python
paths = export_locale_captions(project, timeline, lang)
result.captions = {k: project.relpath(v) for k, v in paths.items()}
ass_path = paths.get("ass")
```

- `result.captions` (`dict[str, str]`, `graph.py:532`) is rebuilt by iterating
  **all** items — an extra `"ttml"` key just adds one more `project.relpath`
  entry; no fixed key-set is assumed.
- `ass_path = paths.get("ass")` uses `.get` — indifferent to extra keys.
- The ttml path lives under `captions_dir/locales/<lang>/`, so
  `project.relpath` resolves it cleanly (pinned:
  `test_export_locale_captions_paths_are_relpath_safe` →
  `captions/locales/ar/captions.ttml`).

The other consumer, `tests/test_locale_roundtrip_gui.py`, reads `paths["srt"]`
by key — also unaffected (re-run green). **No strict assumptions anywhere; the
extra key is safe.**

## None-path byte identity (proven three ways)

1. In-test golden captured from the **pre-change** writer: SHA-256
   `ae3c4e62cc45424717dc5373d3ae272692b660c4cd899c895f4d2f784ccf940f` +
   full-document equality (`test_none_direction_is_byte_identical_to_pre_change_golden`).
2. `cmp` of the post-change output (default **and** explicit `direction=None`)
   against golden files captured before `ttml.py` was touched → IDENTICAL.
3. Existing `tests/test_fp_ttml.py` (25 pins on the exact bytes) stays green.

The only new bytes on any path are the `<div …>` attributes, emitted **solely**
when a non-empty direction is passed.

## Ruby — OUT OF SCOPE (restated, nothing built)

`CaptionLine` carries `start_ms, end_ms, text, speaker, shot, role` — **no ruby
structure**. Emitting TTML `<ruby>` containers would mean fabricating
cue-model data that does not exist. S1's recorded reason stands; this loop
builds nothing for ruby.

## Base (non-locale) export UNTOUCHED

`export_ttml` / `manju export --ttml` are unchanged: they call `compile_ttml`
with `direction` defaulting to `None`, so `xml:lang` stays `""` and the bytes
are identical. No language is declared on the base path.

## CONTRACTS registry — documents-row question → NO edit needed

The `documents:` section lists engine-owned document kinds (`project.yaml`,
`bible`, `shot.yaml`, `timeline.json`, `take-sidecar`, …), each with an `owner`
that resolves to real code. The sibling project-side locale declared files
`lines.yaml` and `voices.yaml` (readers `load_lines`/`load_voices`) are **NOT**
listed. `test_fp_contracts.py` enforces the `schemas:` (`manju.*/vN`) section
and the §4.7 ownership map — it does **not** require a `documents:` row for
project-side declared files. `meta.yaml` is a sibling of `lines.yaml`, carries
no `manju.*/vN` schema id, and follows their established pattern.

**Recommendation:** do not add a `documents:` row for `meta.yaml`; the registry
pattern does not require one and `CONTRACTS.yaml` is orchestrator-only.
`test_fp_contracts.py` re-run green (17 passed), confirming no stray schema
literal was introduced.

## Coordination note (not actioned — parallel-loop-owned file)

`exporters/conform.py`'s `ttml` row states "RTL/vertical/ruby are NOT expressed
(the cue model carries no layout semantics)". That core claim stays **true** —
RTL is now expressible only via an *external human declaration*
(`meta.yaml`), never derived from the cue model, and the conform report is
computed from a timeline with `direction=None`. conform.py's owner may wish to
refine the wording ("RTL only via explicit locale declaration; vertical/ruby
unexpressed"). Left untouched per scope.

## Tests (red → green)

Red-first: 21 fail for exactly the intended causes — 7 `TypeError`
(`compile_ttml` has no `direction`), 8 `ImportError` (`load_locale_meta`
absent), 6 `KeyError: 'ttml'` (sibling not written). After implementation:

| Suite | Result |
|---|---|
| `tests/test_fp_ttml_locale.py` (new) | **21 passed** |
| `tests/test_fp_ttml.py` (existing) | **25 passed** (unchanged) |
| `tests/test_locale_roundtrip_gui.py` (consumer) | **5 passed** |
| `tests/test_fp_contracts.py` (registry) | **17 passed** |

Coverage: locale ttml `xml:lang == locale id`; `direction=rtl` → both attrs
exact; `ltr` → direction only; no meta → attrs absent + None-golden identity;
bad direction / unknown key / non-mapping → structured rejection; direction
never inferred from lang; CJK + RTL (`深夜 مرحبا`) roundtrip; determinism
(two runs byte-equal); relpath safety of the returned dict.
