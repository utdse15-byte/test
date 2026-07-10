# AI IDE 03A — Baseline Audit (基线审计)

Deliverable for **Manju Deep Research 03A — external ShotDraftPackage & controlled shot import**. This file is the frozen WP0 baseline of HEAD before any WP1/WP2 change; the paired build report is `REPORTS/AI_IDE_03A_COMPLETION.md`. WP0 discipline: if any gate had FAILED (an equivalent capability already existed), the batch would STOP with a characterization test and report `ALREADY_IMPLEMENTED` — no build. **All gates PASSED → BUILD.**

---

## 1. Header — branch / commit / date / environment

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` (`.git/HEAD`, read-only) |
| Base commit audited | `548227f` (DR01 + DR02 already landed per `REPORTS/AI_IDE_02_COMPLETION.md`) |
| Date | 2026-07-10 |
| Python | 3.11.15 |
| ffmpeg | `6.1.1-3ubuntu5` present |
| Process rules honored | no git commands; `python -m pytest` only |

### Targeted-suite baseline (the honest comparison set for this batch)

`python -m pytest tests/test_funnel.py tests/test_director.py tests/test_check.py tests/test_write_consistency.py` at WP0 HEAD:

| Run | Result |
|---|---|
| the four targeted suites (WP0 HEAD, before the new file) | **3 failed / 93 passed** |
| the four suites + `tests/test_dr03a_shotpackage.py` (after build) | **3 failed / 112 passed** (the +19 is this batch's file) |

The **3 surviving pre-existing failures** (test-double drift — the test fakes lack the `include_unindexed` kwarg that `gui/plan.py:125` now passes → `TypeError`), classified and **NOT fixed** (out of scope; the spec names exactly these to ignore):

| # | Failing test | Cause |
|---|---|---|
| 1 | `tests/test_director.py::test_execute_build_passes_assume_yes_from_confirmed_state` | `fake_run_build()` lacks `include_unindexed` |
| 2 | `tests/test_director.py::test_mcp_driven_roundtrip_build_mocked` | same test-double drift |
| 3 | `tests/test_write_consistency.py::test_execute_locked_internally_action_type_no_double_acquire` | same test-double drift (`gui/plan.py:125`) |

Anything else must be green. The full-suite number (after all changes): **4 failed, 2268 passed, 12 skipped** — the 4 are the pre-existing `include_unindexed` set, zero new failing names.

---

## 2. WP0 gate audit — five gate proofs (file:line)

### G1 — No unified external shot-package schema exists ✅ PASS

`grep -rn` over `src/` for `shot-draft` / `shot_package` / `shot-package` / `ShotDraftPackage` / `draft_id` → **zero matches** (verbatim: "No matches found"). No schema, model, reader, or writer for an external multi-shot creative package exists anywhere in the tree.

### G2 — No unified validate / diff / apply for a structured shot proposal exists ✅ PASS

Each existing external-input surface was read and confirmed to cover something else; **none imports a structured multi-shot creative package** (source_facts + soft creative_suggestions with a mapped-vs-forbidden split, bible-ref resolution, CAS-guarded create). Evidence in the existing-surfaces table (§3).

### G3 — The reusable controlled-write machinery already exists ✅ PASS (reuse, do not build)

| Machinery | file:line | Role reused |
|---|---|---|
| `Project.save_shot` | `core/container.py:338` | atomic shot YAML write (via `write_yaml`) |
| `Project.load_index` / `save_index` | `core/container.py:286` / `:290` | index read / atomic write |
| append-to-index precedent | `core/container.py:656-661` (`scaffold_shots`) | append new ids to `index.order`, preserve order, never duplicate |
| shot-id safety validator (goal item 11) | `core/container.py:310-317` → `core/idents.py:39` `validate_safe_segment` (`SAFE_SEGMENT_PATTERN` `:26`) | reject `..`/absolute/backslash/NUL ids — reused for `draft_id` + `proposed_shot_id` |
| checked-write + CAS + revert-on-regression | `core/writes.py` (read in full): `checked_shot_write:109-170`, CAS `:151-154`, revert `:164-167` | the exact guard PATTERN mirrored by apply (CAS re-verify → write → post-write check → rollback) |
| `run_check` entry + `CheckReport` | `core/check.py:87` / `:52-62` | post-apply check; new-error diff drives rollback |
| `SECRET_PATTERNS` | `core/check.py:31-41` | reused verbatim over raw package text + serialized body |
| lock verify | `core/locks.py:36` `verify_locks`; `seal_lock:28` | existing-shot locks stay a conflict; never rewritten |
| `append_event` | `core/events.py:21` | one `shot_package_apply` event, ids + digest only |
| atomic writers | `core/yamlio.py:61` `atomic_write_text` / `:97` `write_yaml` / `:85` `dump_yaml` | shot + index writes and the rollback restore |
| hashing | `core/hashing.py:28` `hash_value` / `:34` `hash_text` / `:23` `canonical_json` | semantic_digest, project_revision, proposed_text/index hashes (`sha256:` §) |
| `snap_to_frame_grid` | `timeline/compiler.py:164` | duration-vs-frame-grid WARNING only (never mutates the stored duration) |
| providers fallback map | `providers/registry.py:27` `_FALLBACK_MAP` (keys `caption_card`,`still_frame_motion`) | the ONLY capabilities `execution_constraints.capability` may map into `generation.fallback` |
| build mutex | `runtime/buildlock.py:266` `build_lock` | one lock around apply (like `roundtrip`) |

### G4 — No LLM needed anywhere in this loop ✅ PASS

Every step is deterministic: field copy, bible-membership set lookups (`Project.load_bible` `container.py:356`), id allocation, `hash_value`/`hash_text`, `run_check`. No model call, no prompt compilation, no embedding — inspect and apply are pure functions of (project state, package bytes).

### G5 — STOP-vs-BUILD verdict ✅ BUILD

G1–G4 all pass → **no equivalent capability exists** → the WP0 rule to STOP-with-a-characterization-test does **not** trigger. The closest analog, `manju openclap import-plan` (`exporters/openclap/import_plan.py:158` `build_import_plan`), is a plan-only importer for **OpenClap timeline documents / media locators** — it maps VIDEO/STORYBOARD segments to shots + `register_media` ops; it has **no apply**, carries **no creative_suggestions** and **no soft/forbidden split**, does **no bible-ref resolution**, and is **not CAS-guarded**. It informed the plan-dict shape (reused as a style precedent) but is not the capability. Build proceeds.

---

## 3. Existing-surfaces table — why none is equivalent (G2 detail)

| Surface | Entry (file:line) | What it actually covers | Why NOT equivalent |
|---|---|---|---|
| `manju create` / `new` | `cli.py:270` `create` / `cli.py:169` `new` → `scaffold_shots` `container.py:608` | scaffolds EMPTY `S###` skeletons ("引擎从不代写内容 §2", `container.py:614`) | writes no content; imports no package; no source_facts, no suggestions |
| `manju director` | `build/director.py:98` `ACTION_TYPES` (`build/redo/voice/repair/mixer/captions/packaging/snapshot/rollback`) | proposals from a FIXED whitelist, each 1:1 to an existing engine entry (`director.py:96-108`) | no `create_shot`/`import` action in the vocabulary; cannot ingest an external creative package |
| `manju ingest` | `build/ingest.py:245` `plan_ingest` / `:143` `IngestError` | maps MEDIA files (takes/voice/refs) by filename to shots; imports to `media/imports` | round-trips **media bytes**, not a structured shot spec; attaches to shots, does not CREATE specs from a proposal |
| `manju roundtrip` | `build/roundtrip.py:436` `plan_roundtrip` / `:670` `apply_roundtrip` | flows JianYing/OTIO **timeline edits** back (reorder / caption / trim) on EXISTING shots | carrier is a timeline format; no shot creation from a creative package; no source_facts/suggestions |
| `manju mentions --apply` | `core/mentions.py:229` `apply_to_shot` (via `checked_shot_write`) | writes resolved character/scene mentions into EXISTING shot fields | single field, existing shots only; never creates a shot, never imports a package |
| `manju openclap import-plan` | `exporters/openclap/import_plan.py:158` | plan-only import of a `.clap` timeline; VIDEO/STORYBOARD → shots + media locators | plan-only (no apply); no creative_suggestions / soft-vs-forbidden split / bible resolution / CAS |

**Conclusion:** the capability — *validate + inspect + controlled-apply of an external structured multi-shot creative PROPOSAL, mapping only ordinary editable truth into created shots and refusing existing-shot overwrites* — has no home in the repo. Built as `src/manju/build/shotpackage.py` + one `cli.py` command, reusing G3's machinery.

---

## 4. Non-negotiables carried into the build (recorded for the report)

| # | Non-negotiable | How WP1/WP2 honors it |
|---|---|---|
| 1 | Package is a PROPOSAL, never truth | inspect derives a plan; only `apply_shot_import_plan` writes, via `save_shot`/`save_index` |
| 2 | creative_suggestions are SOFT | forbidden table (`visual_prompt`/`negative_prompt`/`style_tags`/`continuity_notes`/`confidence`/`duration_budget_ms`/`provenance`/`review_notes`/`source_spans`) ALWAYS omitted → `omitted_suggestions` |
| 3 | fragment ids never mint Shot identity | `draft_id` is provenance; empty `proposed_shot_id` → next free `S###` |
| 4 | apply reuses ONLY existing controlled-write machinery | G3 table; no model call, no `selected_take`/media write, no new merge engine/DB |
| 5 | existing-shot op = CONFLICT | `target_id in existing` → `kind:conflict`; `safe_to_apply=False`; points at `manju propose` |
| 6 | inspect ZERO-WRITE; apply needs `--apply` | `build_shot_import_plan` reads only; CLI default = inspect |
| 7 | red-first | `tests/test_dr03a_shotpackage.py` written first — first run `ModuleNotFoundError` (recorded); race-class tests are genuine behavioral assertions |
