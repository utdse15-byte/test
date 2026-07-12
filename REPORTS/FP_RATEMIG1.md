# FP Rational Edit Rate — Stage 1 (R1): the field + the accessor

**Deliverable:** `ProjectConfig.edit_rate` (optional rational truth field) +
`core/container.Project.edit_rate` (the single accessor) +
`tests/test_fp_ratemig1.py`.
**Registry anchor:** `CONTRACTS.yaml` `planned_migrations[0]`
`project.fps-int-to-rational-edit-rate` — **still `implemented: false`** (R1 is
the staged foundation, NOT the migration; `ProjectConfig.fps` is untouched
`int`, and `test_fp_contracts.py::test_declared_future_major_is_recorded_not_implemented`
stays green).
**Boundary honoured:** additive + inert. Touched `core/models.py` +
`core/container.py` only. No consumer, compiler, render, graph, caption, audio,
exporter, keys, or `CONTRACTS.yaml` change. Zero behavior change: every existing
(edit_rate-less) project is **BYTE-IDENTICAL** — that is the loop's product.

---

## 1. The model shape

```python
class EditRate(ManjuModel):          # core/models.py
    num: int
    den: int
    # num/den positive ints (bool rejected); num/den ∈ [1, 1000] fps after
    # normalization (reuses timebase.Rate.from_fraction for exact rationals).
    @property
    def rate(self) -> Rate: ...       # -> timebase.Rate(num/den)

class ProjectConfig(ManjuModel):
    fps: int = 24                      # UNCHANGED — the legacy int mirror
    edit_rate: EditRate | None = None  # NEW, optional, default-absent
    # _edit_rate_mirrors_fps  (model_validator, after): when present, fps MUST
    #                          equal edit_rate.rate.nominal_int, else a structured
    #                          error naming both. Absent => strict no-op.
    # _drop_default_edit_rate (model_serializer, wrap): a None edit_rate is
    #                          dropped from EVERY dump (wave-4b CaptionLine.role
    #                          precedent) so an edit_rate-less project emits no key.
```

**Validation rules (structured errors, never silent):**

| Input | Result |
|---|---|
| `edit_rate` absent | exactly today's model; **no key** emitted on save |
| `{24000,1001}` + `fps: 24` | loads; accessor → exact `Rate(24000/1001)`, `is_ntsc` |
| `{24000,1001}` + `fps: 25` | `ValidationError` naming fps 25 and nominal 24 |
| `{0,1}` / `{24,0}` / negative | `ValidationError` (num/den must be positive ints) |
| `{2000,1}` (2000 fps) / `{1,2}` (0.5 fps) | `ValidationError` (outside 1..1000 fps) |
| `{True,1}` | `ValidationError` (bool is not a frame-rate term) |

An invalid pair also surfaces through **`manju check`** for free: `core/check.py`
already wraps `load_config()` in `except ValidationError` →
`project.yaml: schema invalid — …` (round-W config-validation path, unchanged).

## 2. The accessor

`Project.edit_rate(config: ProjectConfig | None = None) -> timebase.Rate`

- rational `edit_rate` field when the project declares one, **else**
  `Rate.from_fraction(config.fps)` (the legacy int promoted to an exact
  whole-number rate);
- the result's `nominal_int` always equals `fps`, so it is a safe drop-in while
  every existing consumer keeps reading the plain `fps` int;
- pass an already-loaded `config` to avoid re-reading `project.yaml`; omitted, it
  loads fresh — mirrors how `fps` is read via `load_config().fps` today (Project
  caches no config, so neither does this).
- **Nothing in the engine consumes it this loop** (enforced by the grep pin).

## 3. Byte-identity evidence (pinned in `tests/test_fp_ratemig1.py`)

| Surface | How proven | Pin |
|---|---|---|
| `ProjectConfig` serialization | literal dicts for `model_dump()` **and** `model_dump(exclude_none=True)` — no `edit_rate` key either way (the drop-None serializer, required because presets/supportbundle/status dump plain) | `test_no_edit_rate_config_dumps_are_byte_identical_to_pre_r1` |
| `save_config` bytes | written YAML has no `edit_rate` line; re-save is sha256-stable | `test_save_config_writes_no_edit_rate_line` |
| compat corpus `project.yaml` | old int-fps fixture loads read-only, `edit_rate is None`, sha256 unchanged (mirrors, never touches `test_fp_compat`) | `test_compat_corpus_project_loads_hash_identical` |
| `spec_hash` (v1 + v2) | literal sha256 of a fixed shot; `fps`/`edit_rate` absent from `spec_payload` | `test_spec_hash_is_unchanged_and_rate_free` |
| `snap_to_frame_grid` | literal ints (1200@24→1208, 1000@24→1000, 3000@25→3000, 4000@24→4000) | `test_snap_to_frame_grid_is_unchanged` |
| `_segment_cache_key` | re-derived from primitives = `cache_key(hash_file(src), w, h, INT fps, dur, target, fades)` — nothing rate-rational folded in | `test_segment_cache_key_folds_int_fps_and_nothing_rational` |
| compiled timeline | `compile_timeline` output: no `edit_rate`, `fps==24`, deterministic bytes | `test_compiled_timeline_has_no_edit_rate_and_pinned_fps` |
| only-two-files | `edit_rate` token appears in exactly `core/models.py` + `core/container.py` under `src/manju/` | `test_edit_rate_only_referenced_in_models_and_container` |

The picture/frame/cache surfaces are **structurally** immune: `spec_payload`,
`snap_to_frame_grid` and `_segment_cache_key` take an int `fps` / a `ShotSpec` —
none reads `ProjectConfig`. The literals are pre/post-R1 invariants (canonical
sorted-key JSON → sha256 is environment-stable). The one surface R1 truly
touches — config serialization — is proven by the drop-None serializer, whose
necessity is red-first: without it a plain `model_dump()` leaks `edit_rate: null`.

## 4. Stage map (declared migration)

| Stage | Scope | Status |
|---|---|---|
| **R1** | `ProjectConfig.edit_rate` truth field + `Project.edit_rate` accessor; zero consumer change; byte-identity | **LANDED** (this loop) |
| R2 | opt-in rational frame grid (`snap_to_frame_grid` / compiler read the accessor behind a flag) | declared, not done |
| R3 | captions + audio on the rational grid | declared, not done |
| R4 | OTIO / interchange carry the exact rate | declared, not done |
| R5 | `manju migrate` tool: write `edit_rate` into existing projects; eventually retire the `fps` mirror | declared, not done |

`CONTRACTS.yaml` `planned_migrations` `implemented` flag flips only when the
consumers migrate (R2+). R1 keeps it `false`.
