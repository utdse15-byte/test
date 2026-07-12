# FP Loop C — manju.media-technical-profile/v1 (honest technical facts)

Deterministic, unknown-never-guessed per-file technical facts with edit-grid
drift diagnostics. Pure core (`normalize_probe_document`) drives the whole
fixture matrix from hand-written ffprobe JSON; a thin ffprobe shell
(`technical_profile`) + content-addressed derived storage
(`write_profile`/`read_profile`) mirror `media/analysis.py`. `probe.py` /
`ProbeInfo` untouched; no new dependencies; `fractions.Fraction` for all ratio
math (float only in raw verbatim strings + labelled `*_approx` display fields).

## Files

- `src/manju/media/technical_profile.py` (new) — pure normalizer + ffprobe shell
  + derived storage.
- `src/manju/cli.py` — one additive command `manju qc tech` under the existing
  `qc` sub-app.
- `tests/test_fp_profile.py` (new) — 40 unit + 4 real-ffmpeg integration.
- `tests/fixtures/cli_surface.json` — regenerated (one additive `qc tech` row).

## Document field coverage vs §5.2–§5.6

Every fact is **verbatim** from ffprobe or the literal string `"unknown"` —
never a guessed default. `profile_digest` hashes the `facts` block ONLY (no
timestamps / tool paths / source hash) so the same input dict is byte-identical.

### §5.1 time (recorded in `facts.time`; timebase owns the math)
| field | status |
|---|---|
| r_frame_rate / avg_frame_rate (raw) | recorded verbatim |
| rational `rate {num,den}` | recorded via `classify_rate` (odd decimals → `"unknown"`, never snapped) |
| rate_mode cfr/vfr_suspected/unknown | recorded via `timebase.rate_mode` |
| duration_ms / nb_frames / start_time | recorded (int/verbatim or `"unknown"`) |
| range_semantics | recorded (`timebase.RANGE_SEMANTICS`) |
| start_timecode / drop_frame | out of scope (owned by `timebase.Timecode`; not duplicated) |

### §5.2 picture
| field | status |
|---|---|
| coded width/height | recorded (`coded_*`, fallback `width`/`height`) |
| sample/display aspect ratio | recorded (raw + normalized Fraction or `"unknown"`) |
| rotation | recorded (side-data display matrix / `rotate` tag; `0` when absent = displayed as-is) |
| field order (scan type) | recorded (progressive/tt/bb/tb/bt, else `"unknown"`) |
| pixel format | recorded verbatim |
| bit depth | recorded (`bits_per_raw_sample` → COMMON pix_fmt table → `"unknown"`) |
| chroma subsampling | recorded (COMMON table, else `"unknown"`) |
| alpha | recorded (`has_alpha` for known formats, else `"unknown"`) |
| codec profile / level | recorded verbatim / int |
| GOP / keyframe interval / bitrate mode / mastering (HDR) metadata | out of scope (need `-show_frames` / SEI; container `bit_rate` is recorded) |

### §5.3 color
| field | status |
|---|---|
| primaries / transfer / matrix / range | recorded, each verbatim or `"unknown"` |
| color_known gate (all four present) | recorded; drives COLOR_UNKNOWN |
| working/input/display space, OCIO digest, transforms | out of scope (roadmap "可做"; this loop records SOURCE tags only, never invents a pipeline space) |

### §5.4 audio (or `null` when no audio stream)
| field | status |
|---|---|
| codec / sample_rate / sample_fmt / channels | recorded |
| channel_layout | recorded verbatim or `"unknown"` |
| bit depth | recorded (`bits_per_raw_sample`/`bits_per_sample` → sample_fmt table → `"unknown"`) |
| LUFS / true-peak / phase / silence / clipping / delay / stem role | out of scope (measurement, not a probe fact — masters/loudness territory) |

### §5.5 subtitles
Out of scope this loop. Subtitle streams surface honestly in
`container.streams` by `codec_type` + disposition + language; roles / CPS /
glyph coverage deferred.

### §5.6 container / metadata
| field | status |
|---|---|
| ffprobe normalization | recorded (the whole document) |
| format_name / bit_rate / nb_streams | recorded |
| per-stream disposition.default/.forced | recorded |
| per-stream language tag | recorded verbatim or `"unknown"` |
| rotation / display matrix | recorded (picture.rotation) |
| container/stream mismatch diagnostic | partial (DAR/SAR geometry mismatch; broader mismatch deferred) |
| chapters / title/artist/copyright / creation_time / track ordering | out of scope — `creation_time` deliberately NOT recorded (reproducibility + secret/PII discipline) |

## Diagnostics implemented (additive, structured, none blocking)

1. **RATE_UNREPRESENTABLE_ON_EDIT_GRID** — when `--edit-fps` is given and the
   true rate ≠ the integer edit grid (NTSC and any mismatch). Carries the exact
   `grid_drift_ms_over_clip` and `one_frame_drift_at_ms` from `timebase`.
2. **DAR_SAR_GEOMETRY_MISMATCH** — DAR ≠ (coded_w/coded_h)·SAR, **exact Fraction
   compare, tolerance 0**; carries the expected DAR.
3. **COLOR_UNKNOWN** — advisory (never blocking); lists the missing colour axes.
4. **VFR_SUSPECTED** — `r_frame_rate` ≠ `avg_frame_rate`.

### Pinned NTSC drift numbers (surfaced in the diagnostic, from `timebase`)
30000/1001 (29.97) on an integer **edit_fps=30** grid:

- `one_frame_drift_at_ms` = **100000/3 ms ≈ 33 333.33 ms (≈ 33.33 s)** — exactly
  1000 grid frames of accumulated error.
- over a **10.01 s (10 010 ms)** clip: `grid_drift_ms_over_clip` = **1001/100 ms
  = 10.01 ms** — exactly 0.1 % (1 ms per 1000 ms; 3.6 s/hour). Pinned exactly in
  `test_ntsc_rate_exact_rational_and_edit_grid_drift`.

## CLI

`manju qc tech <media> [--edit-fps N] [--json] [--write]` (collision-free under
the existing `qc` sub-app). Honest JSON on `--json`; `--write` materialises the
deletable `reports/technical/<hash>.json`; `source_ref` is **project-relative**
(never an absolute path). Snapshot regenerated via the sanctioned
`python -m tests.test_fp_cli_snapshot`; the diff is a single additive block:

```
"qc tech": { "exists": true, "params": ["media"] }
```

(surface floor 112 → 113; `media` is the only required param).

## Skips / deviations (evidence, never faked)

- **Rotation integration SKIPPED_WITH_EVIDENCE**: env ffmpeg 6.1 cannot stamp a
  ffprobe-READABLE display-matrix rotation — both `-display_rotation 90` and
  `-metadata:s:v:0 rotate=90` round-trip to `side_data_list: null` / no `rotate`
  tag. Rotation is pinned at the **unit** level (display matrix `rotation:90` →
  90; `rotate` tag `"270"` → 270; absent → 0). Integration result never faked.
- **Interlaced / alpha integration**: lavfi `testsrc` is progressive and
  mp4/h264 has no alpha, so `field_order` (tt/bb) and `has_alpha` (rgba→true)
  are pinned at the unit level only.
- **Owner path spelling**: the addendum names the owner `media/technical_profile.py`
  (the file). The registry row below uses the importable dotted form
  `manju.media.technical_profile` so `test_every_owner_resolves_to_real_code`
  passes — same module, registry convention.

## CONTRACTS.yaml row (for the orchestrator — I do NOT edit CONTRACTS.yaml)

Adding the `manju.media-technical-profile/v1` schema **literal** to src makes
`tests/test_fp_contracts.py::test_every_src_schema_literal_is_registered` and
`::test_registry_schema_ids_match_src_exactly` go RED until this row is applied
— the intended registration flow (the test's own docstring names
`media-technical-profile` as the expected path). Paste under `schemas:`:

```yaml
  - id: manju.media-technical-profile/v1
    kind: schema
    owner: manju.media.technical_profile
    status: experimental
    latest_version: 1
    read_older: true
    write_older: false
    notes: >-
      Honest per-file technical facts (time/picture/color/audio/container),
      each verbatim-or-"unknown" with edit-grid drift diagnostics. Derived +
      deletable (reports/technical/<hash>.json); never a build/authorization
      input.
```
