# FP Loop W2 — FCPXML IMPORT-PLAN (the analysis half T2/V1 declined)

Roadmap §5 item 5 (depth). T2's FCPXML writer was honestly writer-only ("no
import / import-plan is claimed anywhere"). This loop adds the READ-ONLY ANALYSIS
half on the EXISTING house import-plan pattern — **plan, never apply** — built on
the openclap precedent (`exporters/openclap/import_plan.py`).

New module: `src/manju/exporters/fcpxml_import.py` (the WRITER `fcpxml.py` is
untouched). CLI: `manju fcpxml import-plan`. Tests:
`tests/test_fp_fcpxml_import.py` (28, red-first).

---

## 1. openclap-mirror audit (done FIRST, mirrored faithfully)

**CLI surface — openclap HAS one, so we expose one, mirroring the shape exactly.**
openclap's import-plan is not a top-level command; it is an `import-plan`
subcommand under a per-format Typer sub-app (`openclap_app`, registered
`app.add_typer(openclap_app, name="openclap")`), i.e.
`manju openclap import-plan FILE [--target PATH] [--json]`
(`cli.py` ~L2279). FCPXML previously had **no** sub-app — only a `--fcpxml` flag
on `manju export`. The faithful mirror is therefore a new per-format sub-app with
exactly the same subcommand shape:

    manju fcpxml import-plan FILE [--target PATH] [--json]

The writer stays a flag on `manju export --fcpxml` (untouched). The new command
reuses openclap's exact grammar: a `_fcpxml_read_or_fail` helper (mirrors
`_openclap_read_or_fail`), the same `{"error","code","diagnostics"}` `--json`
error envelope, `hash_file` for `source_sha256`, `_emit` for `--json`, a colored
summary otherwise.

**Schema id — openclap CARRIES one AND it is registered, so we mirror + hand off.**
`import_plan.py` declares `PLAN_SCHEMA = "manju.openclap-import-plan/v1"`, and
that id has a registry row in `CONTRACTS.yaml` (L400). Per the ruling, this module
mirrors with `PLAN_SCHEMA = "manju.fcpxml-import-plan/v1"` and **STOP-AND-REPORTs**
for the orchestrator to add the registry row (CONTRACTS.yaml is orchestrator-only
— see §6). Recommendation: register as **experimental** (the honest "new arrival"
state; openclap's is stable because it is a mature adapter).

---

## 2. Scope (READ + PLAN only)

`parse_fcpxml(path | XML string) -> ParsedFcpxml`
- DTD-less `ElementTree` (bytes input, no external entity ever fetched).
- Refuses a non-`<fcpxml>` root and malformed XML with a structured
  `FcpxmlImportError` (fail-closed, mirrors openclap's `ClapReadError`).
- Every time is an **exact `Fraction` of seconds — never a float**
  (`_parse_time`: `"48/24s" → Fraction(48,24)`, `"1001/24000s" → 1001/24000`).
- **Version recorded verbatim**, no allowlist gate: any version parses
  best-effort; `version_verified` is True only for the version this parser was
  validated against (1.9), and an unverified version emits an honest diagnostic.
- **FCP-native tolerance**: extra attributes, foreign attribute order, whitespace
  and unknown elements (`<gap>`, `<conform-rate>`, `<adjust-transform>`, …) are
  tolerated — the known geometry is still read; unknown element tags are
  **counted** in `unknown_elements`, never a crash.

`plan_fcpxml_import(parsed, *, source_sha256=None, target_project=None) -> dict`
DERIVED ADVISORY output (never a build input). Rows:
- **windows** — each spine `<asset-clip>` → a proposed clip window; frames via
  the document's OWN `frameDuration` (`frames = seconds / frameDuration`, exact
  rational division). The plan keeps BOTH the integer frame counts and the exact
  rational seconds strings; ms conversion is deliberately NOT performed (frames +
  exact fractions are lossless — `ROUND_HALF_UP` would only be needed for an ms
  echo, which the plan does not carry).
- **transitions** — a `Cross Dissolve` `<transition>` (effect uid
  `FFVideoTransitionCrossDissolve`) → an `xfade_fade_candidate`
  (`proposed_type: xfade_fade`); any other → an `unsupported_transition` row
  (never a faked mapping). NB the writer degrades non-dissolves to an XML
  *comment*, which `ElementTree` drops on parse — so a manju-authored document
  yields exactly its native dissolves as candidates.
- **audio_suggestions** — each connected `<asset-clip audioRole=…>` → a bus
  suggestion via **V1's role map INVERTED** (`dialogue→voice`, `music→music`,
  `effects.sfx→sfx`, `effects.ambient→ambient`); an unknown role → an honest
  `unknown_audio_role` row (role verbatim, **no fabricated bus**).
- **needs_relink** — every external `src` (absolute / remote URL / root-escaping /
  containment-failing, deduped by src) → a `needs_relink` row **pointing at the
  existing relink machinery** (`manju relink plan|apply` /
  `manju.media.relink.relink_plan` / `apply_relink`). Never copied, never fetched.
  Inside-project srcs are not relink rows; each window still carries a
  `media_status`.
- **unmapped** — assets declared but referenced by no clip (listed, not planned).
- **unknown_elements**, **conflicts** (describe-only against a `--target`),
  **diagnostics**.
- **digest** — `hash_value(facts)` over every field except `digest` itself
  (tamper-evidence, the `conform-loss` precedent); `verify_plan_digest` recomputes
  and rejects any tamper / dropped digest / wrong schema.

**PLAN ONLY.** No apply path, no truth writes. `test_module_exposes_no_apply_or_write_surface`
pins that no public name contains apply/write/export.

---

## 3. Round-trip floor (the star pin)

A timeline compiled with **T2 + V1 features** (`compile_fcpxml`) → `parse_fcpxml`
→ `plan_fcpxml_import` reproduces every window / transition / audio suggestion
**exactly**, frames derived from the document's own frameDuration, exact
fractions preserved.

Three-clip T2 timeline (`clip1 --xfade_fade 500ms--> clip2 --fade 300ms(degraded)--> clip3`):

| window | offset_f | start_f | dur_f | offset_seconds |
|--------|---------:|--------:|------:|----------------|
| S001   | 0        | 0       | 48    | `0`            |
| S002   | 36       | 3       | 48    | `3/2`          |
| S003   | 84       | 0       | 24    | `7/2`          |

- transitions: 1 × `xfade_fade_candidate` at S002, offset 36f, duration 12f
  (500ms→12), uid `FFVideoTransitionCrossDissolve` (the degraded `fade` became an
  XML comment → absent from the parse, correctly).

V1 audio timeline (2 clips across a dissolve + voice/music/sfx):

| src              | bus   | role         | lane | parent | off_f | start_f | dur_f | gain   |
|------------------|-------|--------------|-----:|--------|------:|--------:|------:|--------|
| voice/line.wav   | voice | dialogue     | −1   | S001   | 12    | 0       | 24    | —      |
| music/bed.mp3    | music | music        | −2   | S002   | 30    | 0       | 24    | `-6dB` |
| sfx/hit.wav      | sfx   | effects.sfx  | −3   | S002   | 42    | 3       | 24    | —      |

Rational-native (1001 family): `frameDuration="1001/24000s"` parses to the EXACT
`Fraction(1001, 24000)`; 48-frame clips round-trip to whole-frame counts with
`duration_seconds == "1001/500"` (48048/24000 reduced) — zero drift, no float.

---

## 4. Honesty grammar (mirrored from openclap)

- Nothing silently dropped: unknown element → counted row; unknown audio role →
  honest verbatim row (no fabricated bus); unreferenced asset → `unmapped`.
- External media never resolved/fetched — recorded as `needs_relink` pointing at
  the existing machinery, exactly as openclap records locators without resolving.
- Zero filesystem mutations (see §7). The plan is tamper-evident (digest).

---

## 5. Verification

`python -m pytest` (targeted). New suite red-first: `ModuleNotFoundError` →
after implementation **28 passed**.

Verification set (my suite + the standing 6): **130 passed, 3 failed**. The three
reds are NOT regressions from this loop:

- `test_fp_contracts.py::test_every_src_schema_literal_is_registered` and
  `::test_registry_schema_ids_match_src_exactly` — the schema-id STOP-AND-REPORT
  handoff (§6). Proven to flip green with the proposed CONTRACTS.yaml row (loaded
  through the real `contracts._load`, real file untouched).
- `test_fp_fcpxml.py::test_fcpxml_conform_video_preserved_captions_dropped_audio_classified`
  — caused by the **parallel W1 loop's concurrent edit to `conform.py`** (it owns
  `fcpxml.py` + `conform.py`), which changed the `fcpxml` `audio_loops`
  classification from `unsupported` → `approximated` (loop materialization). This
  loop authored **zero lines** of `conform.py`/`fcpxml.py`; `git show HEAD` proves
  the rule was `unsupported` at HEAD and `approximated` only in W1's unstaged
  edit. W1 owns reconciling that assertion. `test_fp_fcpxml_audio.py` (V1, 22) is
  green.

`test_fp_cli_snapshot.py` stays green: the frozen CLI surface was regenerated
(`python -m tests.test_fp_cli_snapshot`), the sanctioned (b)-path — the only delta
vs HEAD is the single new key `fcpxml import-plan` (125 → 126; floor 112 holds).

---

## 6. STOP-AND-REPORT — CONTRACTS.yaml registry row (orchestrator to add)

The code carries `manju.fcpxml-import-plan/v1`; CONTRACTS.yaml is orchestrator-only
and was NOT touched. Add under `schemas:` (verified against `contracts._load`):

```yaml
  - id: manju.fcpxml-import-plan/v1
    kind: schema
    owner: manju.exporters.fcpxml_import
    status: experimental
    latest_version: 1
    read_older: true
    write_older: false
    notes: >-
      Staged FCPXML (.fcpxml) import plan — read-only analysis of a foreign
      FCPXML document into proposed windows / transition candidates / audio-bus
      suggestions (V1 role map inverted) + needs_relink rows; writes nothing,
      fetches nothing, never auto-applies.
```

That single row flips both contracts assertions green (schema-id count 40 → 41,
floor holds).

---

## 7. NO-writes proof

- Loop-level: authored files are `exporters/fcpxml_import.py` (new), `cli.py`
  (+93/−0, purely additive — the audited import-plan surface only), the
  regenerated `tests/fixtures/cli_surface.json` (+1 key), this report, and the
  test file. Zero edits to `conform.py`/`fcpxml.py`/`board.py` (concurrent loops).
- Runtime: a temp tree hashed before/after 3× `parse → plan → verify` is
  byte-and-mtime identical (`test_import_plan_never_writes_against_a_real_project`
  proves the same against a populated `--target` project, mtime included).
