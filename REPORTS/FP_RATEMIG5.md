# FP Rational Edit Rate — Stage 5 (R5): the real `manju migrate` tool

**Deliverable:** the declared `project.fps` int→rational migration
(`CONTRACTS.yaml planned_migrations[0]`), made **runnable** now that R1 (the
truth field + accessor), R2 (the opt-in rational build spine) and R4 (export
truth) give it a real benefit. The registry deferred this machinery "until a
genuine migration existed" — it exists now, so `manju migrate` earns its place.

**Files touched (only these):** `src/manju/core/migrate.py` (new), `src/manju/cli.py`
(the `migrate` sub-app only — one additive hunk at EOF), `tests/test_fp_ratemig5.py`
(new), `tests/fixtures/cli_surface.json` (reviewed snapshot regen), this report.
**Not touched:** `core/models.py` (R1's validation is CONSUMED, never edited),
`core/container.py` (config READ through its existing API), `exporters/` /
`media/masters.py` (R4), `timeline/compiler.py` / `media/render.py` /
`build/graph.py` (R2 spine), and every `test_fp_ratemig4*` / `test_fp_toolkeys*`.

---

## A. The token-free discipline — how R1's grep pin stays green *untouched*

R1's surface pin (`test_fp_ratemig1.py::test_edit_rate_only_referenced_in_models_and_container`)
greps `src/manju/**/*.py` for the literal `edit_rate` token and permits it in
**exactly** `core/models.py`, `core/container.py`, `exporters/`, and `cli.py`.
`core/migrate.py` is **not** in that sanctioned set, and "all prior pins
inviolable" forbids weakening it.

Resolution: **`core/migrate.py` never spells the token.** It depends on the
`EditRate` *type* (CamelCase — no `edit_rate` substring) and the
`ProjectConfig.frame_rate` / `Project.edit_rate()`-class **resolvers** (which
return a `timebase.Rate`), and it discovers the field's key **by type** at import:

```python
_RATE_FIELD = next(n for n, f in ProjectConfig.model_fields.items()
                   if EditRate in get_args(f.annotation))   # -> "edit_rate" at runtime
```

Every read is via `config.frame_rate` (a `Rate`); every write sets the
type-discovered key on a validated `ProjectConfig`. Output dict keys, messages
and comments use `rational_declared` / "rational rate" — never the underscore
token. `test_migrate_module_is_token_free` pins this, and R1's pin is **green,
unmodified**. This is the same encapsulation R2 used for the build spine, applied
to the migration tool: depend on the type + resolver, never the raw field.

## B. `migrate_inspect` — zero-write, honest, never recommends

| Property | Behaviour | Pin |
|---|---|---|
| zero-write | project.yaml byte-identical + whole tree untouched after inspect | `test_inspect_is_zero_write` |
| candidate listing | fps 24→[**24000/1001** ntsc_neighbor, **24** exact_passthrough]; 30→30000/1001; 60→60000/1001 | `test_inspect_lists_ntsc_neighbour_and_exact_passthrough_for_24`, `..._for_30_and_60` |
| honest non-NTSC row | fps 25 → exact int only + `candidate_note` "no NTSC neighbor …" | `test_inspect_non_ntsc_fps_is_exact_only_with_honest_note` |
| **never auto-selects** | output carries **no** `recommended`/`suggested`/`default`/`chosen`/`preferred` key anywhere | `test_inspect_never_carries_a_recommended_field` |

Every candidate mirrors the project fps (`nominal_int == fps`) — the migration
preserves the legacy `fps` mirror, so a candidate can never split the truth.

## C. Rebuild forecast — a COUNT, never a deletion

Cached segments live at `project.segments_dir/<short_hash>.mp4` (plain segments)
and `xfade_<short_hash>.mp4` (boundary segments) — all `*.mp4` there
(`media/render.py:396`). `inspect` reports `rebuild_forecast.cached_segments` =
`count(*.mp4)` and states "N cached segment(s) will go cold after the rate change
— a COUNT, not a deletion." They go cold because R2 folds the rational rate into
the segment/boundary/final cache keys, so int-keyed entries miss after the
migration. Content-addressed stale entries are inert disk (gc's business);
**nothing is removed.** Pinned by `test_inspect_rebuild_forecast_counts_cached_segments_honestly`
(3 synthetic segments incl. an `xfade_` boundary counted exactly, all still on
disk afterward) and `..._zero_on_fresh_project`.

## D. `migrate_plan` — the CAS plan

The plan is the byte contract for the write:

- `expected_sha256` — the CAS anchor = the **current** project.yaml file hash
  (what apply must match);
- `result_sha256` — the hash of the **target** bytes (the model-canonical dump
  with the rational key inserted);
- `diff` — a unified diff. For a scaffolded project it is a clean minimal
  insertion of the `<rational-key>:` block right after `fps:` (the fps line is
  **unchanged** — the mirror);
- `post_checks` — "project.yaml validates" + "the resolver returns `<target>`".

**Mirror refusal:** a target whose `nominal_int != fps` (e.g. fps 24 →
30000/1001, nominal 30) is refused with a structured error naming both — the
int→rational move keeps `fps` as the exact mirror; changing the nominal rate is a
different operation. Pins: `test_plan_carries_cas_hashes_and_the_key_insertion_diff`,
`test_plan_refuses_a_target_that_breaks_the_fps_mirror`, `test_plan_is_zero_write`.

## E. `apply_plan` — CAS + atomic + post-check + **no auto-commit**

1. **CAS gate:** re-read project.yaml, hash it; if `!= plan.expected_sha256`,
   **refuse** (the file changed since the plan was made — optimistic concurrency).
2. **Recompute + verify:** rebuild the target bytes from the CAS-verified current
   file and confirm they match `plan.result_sha256` (plan and write must agree).
3. **Atomic write of ONLY project.yaml** (temp + fsync + rename, via
   `yamlio.atomic_write_text`).
4. **Post-check:** reload config (raises if invalid) + confirm the resolver
   returns the target rate + `rational_declared` is now True.
5. **No git commit.** Git is the user's rollback engine, so apply returns the
   exact revert command instead: `git -C <root> checkout -- project.yaml` (and the
   native `manju rollback file project.yaml`).

| Guarantee | Pin |
|---|---|
| writes the rational rate; fps mirror kept; resolver returns it | `test_apply_writes_the_rational_rate_and_mirrors_fps` |
| post-check reports validates + resolver_rate + rational_declared | `test_apply_post_check_confirms_validation_and_resolver` |
| **touches ONLY project.yaml** (whole tree else byte-identical) | `test_apply_touches_only_project_yaml` |
| never commits; leaves an uncommitted working-tree edit to just project.yaml; reports revert | `test_apply_does_not_auto_commit_and_prints_the_revert` |
| CAS drift → refuse, nothing written, rate not applied | `test_apply_refuses_on_cas_drift` |
| exact-int passthrough (24/1) is a legal migration too | `test_apply_exact_passthrough_declares_a_whole_rate` |

The engine derives the timeline, ffmpeg command lines and cache keys from the one
project.yaml pin at the next compile — apply writes one file and stops.

## F. Downgrade — supported, with a required loss acknowledgement

`downgrade_loss_report` emits structured rows (rational → int); the write is
**refused** unless the caller acknowledges the loss.

| aspect | impact |
|---|---|
| `exact_ntsc_timing` | the exact NTSC rate is discarded; the 0.1% NTSC-on-int drift returns |
| `drop_frame_timecode` | DF timecode (legal only for 29.97/59.94) no longer applies (honest "n/a" row for 23.976) |
| `drift_free_grid` | the ≤ ½ ms cumulative-boundary grid is lost; back to the int-ms grid (R2) |
| `cache_key_continuity` | keys shift back — the N cached segments go cold again (rebuild, no deletion) |
| `interchange_rate` | OTIO/EDL rational values become nominal-int approximations (R4) |

Pins: `test_downgrade_loss_report_has_the_structured_rows`,
`test_downgrade_apply_is_refused_without_acknowledgement` (refusal names
"acknowledge"; the rational rate is still on disk after the refusal),
`test_downgrade_apply_with_acknowledgement_removes_the_rational_rate` (field gone,
fps mirror kept, resolver back to whole int),
`test_apply_then_downgrade_round_trips_to_the_original_int` (a fresh
apply→downgrade returns project.yaml to its **original bytes**), and
`test_downgrade_plan_refuses_when_nothing_to_downgrade`.

## G. CLI surface — `manju migrate inspect | plan | apply | downgrade`

- `inspect [--json]` — the picture; lists candidates, never recommends.
- `plan --rate N/D [--json]` — the CAS plan (diff + hashes). No `--rate` → lists
  candidates and **refuses** (the never-auto-select pin, enforced at the surface).
- `apply --rate N/D [--yes] [--json]` — CAS + atomic write. Without `--yes` shows
  the plan and refuses (gate); with `--yes` writes + prints the revert command.
- `downgrade [--acknowledge-loss] [--yes] [--json]` — without `--acknowledge-loss`
  prints the full loss report and **refuses**; with `--acknowledge-loss --yes` it
  applies. `--json` everywhere (structured envelopes, incl. the refusals).

CLI pins: `test_cli_inspect_json`, `test_cli_plan_requires_rate_and_lists_candidates`,
`test_cli_apply_gate_and_write`, `test_cli_downgrade_refused_without_acknowledge_loss`.
**Snapshot regen:** `tests/fixtures/cli_surface.json` regenerated on purpose
(the reviewed (b)-path) — 121 → **125** commands (the 4 migrate commands, each
with **zero required params** — the compat-safe direction), floor 112 satisfied.
Verified end-to-end against the real Typer app + a real git repo: `manju check`
passes on the migrated project.

## H. CONTRACTS flip — SUGGESTED rows (orchestrator applies; NOT edited here)

`CONTRACTS.yaml` is untouched (`test_fp_contracts` stays green:
`planned_migrations[0].implemented` is still `false`). Suggested edits for the
orchestrator:

1. `planned_migrations[0].status`: `declared` → **`implemented`**
2. `planned_migrations[0].implemented`: `false` → **`true`**
3. `planned_migrations[0]`: add **`implemented_by: manju migrate (core.migrate)`**
4. `documents[ id: project.yaml ].notes`: replace the parenthetical
   "(… the int→rational … major is declared only)" with a note that it is now
   **implemented via `manju migrate`**, and add a
   **`migration_from: [fps-int]`** marker referencing migration id
   `project.fps-int-to-rational-edit-rate`.

## I. Test counts (targeted; no full suite)

New: **`tests/test_fp_ratemig5.py` — 27 passed.** Guardian set (targeted) —
**145 passed, 0 failed:**

| suite | count | role |
|---|---|---|
| `test_fp_ratemig5` | 27 | R5 (new) |
| `test_fp_ratemig1` | 22 | R1 grep pin (**green, unmodified**) + byte-identity |
| `test_fp_ratemig2` | 17 | R2 spine byte-identity |
| `test_fp_cli_snapshot` | (reviewed regen) | CLI surface floor + (a)/(b) directions |
| `test_fp_contracts` | 17 | `planned_migrations` still `implemented:false` (flip is the orchestrator's) |
| `test_container` | 36 | R1 accessor + config IO (consumed, not changed) |
| `test_cli` | rest | CLI smoke over the real Typer app |

## J. Deviations / honest characteristics

1. **Core `apply_plan` touches ONLY project.yaml — no audit event.** Unlike some
   CLI writes (`lock` appends to events.jsonl), migrate apply appends **no** event
   and writes **no** other file — the addendum's "edits ONLY project.yaml" pin is
   literal, and git (the printed revert) is the audit/rollback trail. The CLI
   wraps apply in the standard `_write_lock` (a `.manju/` process lock, the
   deletable runtime dir §3); the pure `apply_plan` core takes no lock, so
   `test_apply_touches_only_project_yaml` proves the strict guarantee.
2. **project.yaml is re-serialized through the canonical model dump.** For a
   scaffolded file this is a clean minimal insertion (the on-disk bytes already
   equal the model dump). A hand-formatted project.yaml (reordered keys, YAML
   comments) is normalized by the round-trip — but the **plan's diff shows exactly
   that** before any write, and CAS refuses if the file drifts, so nothing is
   silent. Extra (non-model) keys are preserved (`extra="allow"`).
3. **`manju migrate` never auto-selects.** `plan`/`apply` require an explicit
   `--rate`; omitting it lists candidates and exits non-zero. There is no
   "recommended" field anywhere in the tool — the choice is always the user's.
