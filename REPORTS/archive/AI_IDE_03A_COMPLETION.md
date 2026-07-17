# AI IDE 03A Completion

Build report for **Manju Deep Research 03A — external ShotDraftPackage & controlled shot import**. Paired baseline: `REPORTS/AI_IDE_03A_BASELINE.md`. Final-run numbers and git state below are filled by the orchestrator after the last full-suite run.

---

## Repository

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| Base commit | `548227f` (DR02 reports; DR01+DR02 fully landed) |
| Pre-existing work preserved | DR01 + DR02 landed; the **4 pre-existing `include_unindexed` test-double-drift failures untouched** (full-suite baseline) — `test_director` ×2, `test_round_w_agent_wb` ×1, `test_write_consistency` ×1 (the targeted run in §Tests sees 3 of them — `test_round_w_agent_wb` is outside that set) |
| DR03A landed | `src/manju/build/shotpackage.py` (new), one `src/manju/cli.py` command block, `tests/test_dr03a_shotpackage.py`, `tests/fixtures/shot_draft_package.yaml` — working tree otherwise clean but for these two reports |
| Final git state | `db0db1e` (DR03A code: module + CLI + 20 tests + fixture + DECISIONS.md #14 + README row) → the reports commit adding this file + the baseline; working tree clean after it |

---

## Baseline (WP0 verdict)

All four gates PASSED → **BUILD** (not `ALREADY_IMPLEMENTED`). No external shot-package schema exists (grep-zero); no existing surface (`create`/`director`/`ingest`/`roundtrip`/`mentions`, nor the plan-only `openclap import-plan`) imports a structured multi-shot creative package; the controlled-write machinery already exists and was reused; no LLM is needed. Full evidence with file:line in `REPORTS/AI_IDE_03A_BASELINE.md` §2–§3.

**Red-first:** `tests/test_dr03a_shotpackage.py` was written and run BEFORE the module existed — first run = collection-time `ModuleNotFoundError: No module named 'manju.build.shotpackage'` (the accepted RED for a brand-new module). The RACE-class tests (9 zero-write, 10 CAS, 12 half-state rollback, 13 check-failure rollback) carry genuine behavioral assertions (tree-hash before/after, structured refusal, `code`, retained check evidence), not import smoke. After the module + CLI landed: **19 passed**; the orchestrator's review added test 14b (empty-proposed-id re-apply refusal as a first-class cause) → **20 passed**.

---

## Existing systems reused (not duplicated)

| System | file:line | How reused |
|---|---|---|
| `Project.save_shot` | `core/container.py:338` | the ONLY shot writer apply uses (atomic via `write_yaml`) |
| index read/append/save | `core/container.py:286`/`:290`; append precedent `:656-661` | apply appends created ids to `index.order`, preserves order, never duplicates |
| shot-id validator | `core/idents.py:39` `validate_safe_segment` | reused for `draft_id` + `proposed_shot_id` (no new regex) |
| checked-write PATTERN | `core/writes.py:109-170` (CAS `:151`, revert `:164`) | apply mirrors CAS re-verify → write → post-write check → revert-on-regression |
| `run_check` / `CheckReport` | `core/check.py:87`/`:52` | baseline vs post-apply diff drives rollback; evidence returned |
| `SECRET_PATTERNS` | `core/check.py:31-41` | reused verbatim over raw text AND serialized body |
| `verify_locks`/`seal_lock` | `core/locks.py:36`/`:28` | existing-shot locks stay a conflict; never rewritten |
| `append_event` | `core/events.py:21` | one `shot_package_apply` event (ids + digest only) |
| atomic writers | `core/yamlio.py:61`/`:97`/`:85` | shot + index writes and the rollback restore |
| hashing | `core/hashing.py:28`/`:34`/`:23` | `semantic_digest`, `project_revision`, proposed_text/index hashes (`sha256:`) |
| `snap_to_frame_grid` | `timeline/compiler.py:164` | duration-vs-grid WARNING only; never mutates stored duration |
| `_FALLBACK_MAP` | `providers/registry.py:27` | the known-capability set for `execution_constraints.capability` |
| `build_lock` | `runtime/buildlock.py:266` | one lock around apply (like `roundtrip`); NOT wrapped in `_write_lock` |
| CLI envelope | `cli.py` `_emit:67`/`_fail:72`; `roundtrip:2318`/`openclap import-plan:2056` | `--json`/error style + flat plan-or-apply command shape |

---

## Package contract — `manju.shot-draft-package/v1`

YAML, safe-loaded, **size-capped at 2 MiB before parse**, raw text + serialized body scanned with `SECRET_PATTERNS`.

- **Required:** `schema`, `package_id`, `producer{kind,name}`, `source.source_revision`, `shots[]` each with `draft_id`. Everything else optional.
- **Per shot:** `draft_id` (required), `proposed_shot_id` (optional — empty → next free `S###` allocated at inspect), `source_facts{source_spans[], scene_ref, character_refs[]}`, `creative_suggestions{duration_ms, camera{}, action, visual_prompt, negative_prompt, style_tags, confidence, continuity_notes}`, `execution_constraints{capability, duration_budget_ms}`, `review_notes{warnings[]}`, `provenance{node_ids[], prompt_digest}`.
- **Normalization / validation (all raise `ShotPackageError` unless noted):** ids validated via `validate_safe_segment`; absolute / `..` path in any `*path*` field rejected (never resolved → no symlink escape); `SECRET_PATTERNS` hit rejected; unknown schema **MAJOR** rejected (`/v2` → reject); unknown **minor fields** preserved + WARN; duplicate `draft_id`/`proposed_shot_id` → invalid; missing required field → invalid.
- **`semantic_digest(package)`** = `hash_value` over a canonical payload **EXCLUDING `created_at` and `package_id`** → independent of YAML key order and of those envelope fields (tests 1, 2 pin this). Two packages with the same creative content share a digest → the basis of re-apply idempotency.

### Mapping table — fields that MAP into a created ShotSpec (ordinary editable truth)

| Package field | → Shot field | Rule (as implemented) |
|---|---|---|
| `source_facts.scene_ref` | `shot.scene` | mapped; must exist in bible (`load_bible`) else **UNRESOLVED REF** — never silently created; op not `safe_to_apply` until resolved |
| `source_facts.character_refs[]` | `shot.characters[]` | mapped; each checked against bible; a miss is an unresolved ref |
| `creative_suggestions.action` | `shot.action.main` | mapped as the action's main text |
| `creative_suggestions.camera.{shot_size,movement,angle}` | `shot.camera.*` | only keys in `core/models.py` `Camera.model_fields`; unknown keys → `omitted_suggestions`; a `shot_size` outside `SHOT_SIZES` → omitted (invalid value) |
| `creative_suggestions.duration_ms` | `shot.duration` (seconds) | `duration_ms/1000` as a PLAIN editable spec duration (NOT a lock); a value that would frame-snap emits a WARNING only (reuses `snap_to_frame_grid`) |
| `execution_constraints.capability` | `shot.generation.fallback` | mapped to `[capability]` ONLY if `capability` is a key in `providers/registry.py` `_FALLBACK_MAP` (`caption_card`,`still_frame_motion`); else omitted + warn (routing owns capability choice) |

`shot.id` is the allocated/proposed `S###`, never the `draft_id`.

### Rejected mappings — the forbidden table (ALWAYS omitted; reason + where the value goes)

Every row below is emitted into the op's `omitted_suggestions` when present in the shot:

| Package field | Reason it is never mapped | Where the value goes instead |
|---|---|---|
| `creative_suggestions.visual_prompt` | would shadow prompt compilation | recorded nowhere in the shot; the author may **hand-copy it into `generation.prompt_override`** later — never auto |
| `creative_suggestions.negative_prompt` | would shadow prompt compilation | same — manual `generation.prompt_override` only |
| `creative_suggestions.style_tags` | soft styling; never an auto quality/continuity lock, never a bible write | dropped (listed in `omitted_suggestions` for the author) |
| `creative_suggestions.continuity_notes` | never an auto continuity-lock, never a bible write | dropped (listed in `omitted_suggestions`) |
| `creative_suggestions.confidence` | never auto-approves or auto-rejects a shot | dropped (listed in `omitted_suggestions`) |
| `execution_constraints.duration_budget_ms` | no matching spec field; budgeting is routing's job | dropped (listed in `omitted_suggestions`) |
| `provenance` (`node_ids`, `prompt_digest`) | never in shot YAML | recorded in the **plan** + the **apply event** (digest/op_ids), not the shot |
| `review_notes` (`warnings[]`) | never in shot YAML | recorded in the **plan**, not the shot |
| `source_facts.source_spans` | provenance only; shot YAML schema unchanged | kept in the **plan/events**, never in the shot |

Structural guarantee (asserted by test 7): a created shot has empty/absent `quality.must_show`, `quality.avoid`, `continuity.locks`, no `tier`, no `generation.provider`, no `generation.prompt_override`, `locked == {}`, and `status.selected_take is None` — creative suggestions never become hard constraints, and ops never carry `selected_take`/media.

---

## Inspect / apply — `manju.shot-import-plan/v1`

**`build_shot_import_plan(project, package) -> dict`** (ZERO writes). Plan fields: `schema`, `package_id`, `package_digest` (= `semantic_digest`), `project_revision` (= `hash_value` over `shot_ids` + `index.yaml` text — documented current-state token), `operations[]`, `summary{create, update:0, conflicts, unresolved_refs}`, `safe_to_apply`, `warnings[]`.

Each operation: `op_id` (sequential), `kind` ∈ `create_shot | update_index | conflict`, `draft_id`, `target_path` (project-relative, e.g. `shots/S001.yaml`), `expected_current_hash` (null for creates; the CURRENT `index.yaml` hash for the `update_index` op), `proposed_text_hash` (hash of the EXACT YAML text apply will write), `fields` (mapped dict), `omitted_suggestions[{field,reason}]`, `unresolved_refs[{field,ref}]`, `warnings[]`. `safe_to_apply` is true iff no conflicts, no unresolved refs, and the package was not already applied.

Inspect validates the whole contract list: schema/digest, dup ids, path safety, secret scan, bible refs, duration-vs-frame-grid (warn), existing-id conflicts (checked against `shot_ids()`), current index hash, stale `source_revision` vs `project_revision` (warn), and asserts structurally that ops never carry `selected_take`/media.

**`apply_shot_import_plan(project, package, *, actor, plan=None) -> dict`** — the ONLY writer, under one `build_lock`:

1. **Recompute** the plan NOW; refuse `not_safe_to_apply` (reasons listed).
2. **CAS** re-verify against the reviewed `plan` (when given): `index.yaml` hash unchanged since it, the write signature (`create_signature`) identical, every target shot file still absent → any mismatch = `code:"cas_mismatch"`, **zero writes**.
3. **Staged write**: `save_shot` each new spec (byte-exactness re-checked against `proposed_text_hash`), then `save_index` — remembering original index bytes + created paths; **any** failure → `_rollback` (delete created files reverse, restore index verbatim) → `code:"apply_failed"`, no half-state.
4. **Post-apply `run_check`**: NEW errors (after − baseline) → `_rollback` → `code:"check_failed"` with `check` evidence retained.
5. **`append_event`** `shot_package_apply` `{package_id, package_digest, op_ids, created}` — no prompts, no secrets, no package body.
6. Never touches git, media, `selected_take`, or locks.

The `plan=` keyword is an additive, keyword-only extension (see Deviations) that anchors the CAS check to the plan the human reviewed.

### MCP parity decision — NOT exposed (recorded)

The external-file apply command class is **not on MCP today**: `mcp/tools.py` `list_tools()` exposes 25 tools (`status`…`skill_show`) and includes **no** `roundtrip`, `ingest`, or `import` (grep-verified — zero matches). Per the parity rule, `shot_package` is **not** added to MCP either. CLI and the (absent) MCP path would share the same two service functions regardless; test 17 asserts `shot_package`, `roundtrip`, `ingest` are all absent from `list_tools()`. The CLI command `manju shot-package FILE [--apply] [--json]` calls `load_package` + `build_shot_import_plan` (inspect) / `apply_shot_import_plan` (apply) directly, so CLI `--json` output equals direct service output byte-for-byte (test 17).

---

## Files created / changed

| File | Change | Lines |
|---|---|---|
| `src/manju/build/shotpackage.py` | **new** — the whole WP1+WP2 module | 773 |
| `src/manju/cli.py` | **+1 command block** (`shot_package` + `_print_shot_package_plan`), registered next to `roundtrip` | +~90 |
| `tests/test_dr03a_shotpackage.py` | **new** — 19 tests (18 behavior classes + schema-constant pin) | 386 |
| `tests/fixtures/shot_draft_package.yaml` | **new** — a real two-shot CJK package (scene `convenience_store`, character `linxia`) | 68 |
| `REPORTS/AI_IDE_03A_BASELINE.md` / `AI_IDE_03A_COMPLETION.md` | **new** — this pair | — |

No edits to `core/`, `models.py`, `check.py`, or `mcp/tools.py` — exactly the file boundary the spec set.

### Service API signatures

```python
load_package(path) -> dict                                              # size-cap + secret scan + safe-load + schema-major
semantic_digest(package: dict) -> str                                  # sha256: over payload minus created_at/package_id
project_revision(project) -> str                                       # sha256: over shot_ids + index.yaml text
build_shot_import_plan(project, package: dict) -> dict                 # ZERO-WRITE inspect → shot-import-plan/v1
apply_shot_import_plan(project, package: dict, *, actor="human",
                       plan: dict | None = None) -> dict                # controlled apply under build_lock
class ShotPackageError(ValueError)                                     # structural/safety rejection
```

---

## Tests (`tests/test_dr03a_shotpackage.py`)

Red-first: first run = `ModuleNotFoundError` (collection error, all classes RED). After build: **19 passed in ~1.8s** (20 after the orchestrator's review addition, test 14b).

| # | Class | Test fn | Behavioral assertion | Result |
|---|---|---|---|---|
| 1 | key-order digest stability | `test_01_…` | reordered top-level + shot keys → identical `semantic_digest` | ✅ |
| 2 | created_at (and package_id) excluded | `test_02_…` | changing either does not move the digest; a real content change does | ✅ |
| 3 | dup draft/proposed ids rejected | `test_03_…` | both raise `ShotPackageError` | ✅ |
| 4 | path traversal + absolute rejected | `test_04_…` | `../…`, `/etc/…`, `..\\…` in `path_hint` all raise | ✅ |
| 5 | unknown schema MAJOR rejected | `test_05_…` | `/v2` raises (dict + file paths); unknown minor field → warns, not reject | ✅ |
| 6 | missing bible ref → unresolved, never created | `test_06_…` | `summary.unresolved_refs≥1`, `safe_to_apply=False`, bible untouched, tree-hash unchanged before/after inspect AND refused apply | ✅ |
| 7 | suggestions → NO hard constraints | `test_07_…` | created shot has empty must_show/avoid/locks, no tier/provider/prompt_override, `locked={}`, no selected_take; prompt/style/notes text absent from YAML | ✅ |
| 8 | draft ids never mint identity | `test_08_…` | `draft_001` + empty proposed → `shots/S001.yaml`, never `shots/draft_001.yaml` | ✅ |
| 9 | inspect ZERO-WRITE | `test_09_…` | tree-hash identical before/after `build_shot_import_plan` | ✅ |
| 10 | CAS: index mutated between plan & apply | `test_10_…` | reviewed plan anchored; `add_shot` moves index; apply → `code:"cas_mismatch"`, targets absent, tree-hash unchanged | ✅ |
| 11 | locked fields never rewritten | `test_11_…` | op on locked existing S001 stays `conflict` (refused, byte-identical); unrelated S002 create applies, S001 still byte-identical, `check.ok` | ✅ |
| 12 | half-state impossible | `test_12_…` | monkeypatch `save_index`→raise after shots written → created files rolled back, index restored, tree-hash unchanged | ✅ |
| 13 | check-failure rollback | `test_13_…` | monkeypatch `run_check` to inject a post-write error → `code:"check_failed"`, evidence in `check.errors`, batch rolled back | ✅ |
| 14 | re-apply idempotent | `test_14_…` | 2nd inspect → conflicts, `safe_to_apply=False`; 2nd apply refused; `index.order` unchanged, no dup | ✅ |
| 15 | stale source_revision warns | `test_15_…` | fixture rev warns "stale"; a rev == `project_revision` does not | ✅ |
| 16 | JSON stable + project-relative paths | `test_16_…` | two inspects → identical JSON; every `target_path` relative, `..`-free, `shots/`-prefixed | ✅ |
| 17 | CLI ⇄ service share one plan; MCP parity | `test_17_…` | CLI `--json` == direct `build_shot_import_plan`; `shot_package`/`roundtrip`/`ingest` absent from MCP `list_tools()` | ✅ |
| 18 | events carry no secrets/prompt text | `test_18_…` | fake `sk-proj-…` package rejected (file + dict paths), no event; applied event detail ⊆ {package_id,package_digest,op_ids,created}, no action/prompt/secret text | ✅ |
| 19 | targeted-suite regression | (run, not a fn) | `test_funnel` + `test_director` + `test_check` + `test_write_consistency` + this file | see below |

**Test 19 — targeted regression run:** `python -m pytest tests/test_funnel.py tests/test_director.py tests/test_check.py tests/test_write_consistency.py tests/test_dr03a_shotpackage.py` → **3 failed, 112 passed**. The 3 failures are **exactly** the pre-existing baseline set (`test_director` ×2, `test_write_consistency` ×1 — `include_unindexed` test-double drift, `gui/plan.py:125`); zero new failing names; +19 passed = this batch (before the orchestrator's 20th test). `test_check.py` and `test_funnel.py` fully green.

Full-suite (orchestrator, after ALL changes incl. review fixes): **4 failed, 2268 passed, 12 skipped** (886.22s / 0:14:46) — the 4 failures are exactly the pre-existing `include_unindexed` set; **zero new failing test names**; +20 passed vs DR02's 2248 = the 20 DR03A tests.

---

## Deviations (with reasons)

| Deviation | Reason |
|---|---|
| `apply_shot_import_plan(..., plan=None)` adds a keyword-only `plan=` beyond the spec's `(project, package, *, actor)` | additive and backward-compatible (default `None` = recompute-and-apply). It anchors the CAS "index bytes unchanged since **plan**" to the plan the human actually reviewed — the correct optimistic-concurrency semantics ("apply what I inspected") and what makes test 10's CAS a genuine behavioral assertion. The CLI passes the freshly-inspected plan; a direct caller may omit it. |
| Re-apply idempotency also keyed on `semantic_digest` in `events.jsonl` (not only id-collision) | id-collision alone makes explicit-`proposed_shot_id` packages idempotent, but an empty-id package would re-allocate fresh `S###` and duplicate content on re-apply. Scanning prior `shot_package_apply` events for the same digest closes that footgun (read-only; inspect stays zero-write). Strengthens test 14. |
| `execution_constraints.capability` maps to `generation.fallback = [capability]` | the spec says map to "the shot's generation fallback/capability field"; `Generation.fallback` (`models.py:147`) is that field. Set to the single proposed capability (editable truth the author owns), gated on `_FALLBACK_MAP` membership per the ruling. |
| `camera.angle` mapped in addition to `shot_size`/`movement` | the ruling says "shot.camera fields that exist in the model"; `angle` is a `Camera` field (`models.py:113`), so it maps by the same rule. Unknown camera keys and invalid `shot_size` values still go to `omitted_suggestions`. |

No other deviations. No `core/`, `models.py`, `check.py`, or `mcp/tools.py` edits.

---

## Acceptance self-check (contract non-negotiables)

| # | Requirement | Status | Where |
|---|---|---|---|
| 1 | Package is a proposal, never truth | ✅ | inspect derives; only `apply` writes, via `save_shot`/`save_index` |
| 2 | creative_suggestions SOFT (no hard-constraint promotion) | ✅ | forbidden table → `omitted_suggestions`; test 7 |
| 3 | fragment ids never mint Shot identity | ✅ | `draft_001` → `S001`; test 8 |
| 4 | apply reuses ONLY existing machinery; no model/media/selected_take/DB | ✅ | reuse table; grep-clean of media/selected_take in ops; test 7/18 |
| 5 | existing-shot op = CONFLICT | ✅ | `kind:conflict`, `safe_to_apply=False`; tests 11, 14 |
| 6 | inspect ZERO-WRITE; apply needs `--apply` | ✅ | tests 9, 6, 10, 12, 13; CLI default = inspect |
| 7 | CAS-guarded, compensating rollback, post-apply check | ✅ | apply steps 2–4; tests 10, 12, 13 |
| 8 | events carry no secrets/prompt bodies | ✅ | detail = ids + digest only; test 18 |
| 9 | one flat CLI command (roundtrip precedent); MCP parity | ✅ | `manju shot-package`; not on MCP (parity); test 17 |
| 10 | red-first; targeted suites green but the 3 baseline drifts | ✅ | RED recorded; test 19 = 3 failed (baseline) / 112 passed |
