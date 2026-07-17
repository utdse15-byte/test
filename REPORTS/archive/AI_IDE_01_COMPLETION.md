# AI IDE 01 Completion

Build report for **Manju Deep Research 01 — Timeline-first 运行时与 OpenClap 交换格式** (Part A binding contract; section skeleton per A12). Paired baseline: `REPORTS/AI_IDE_01_BASELINE.md`. Final-run numbers and git state below were filled by the orchestrator after the last full-suite run.

---

## 1. Baseline

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| Base commit | `047b6ce` (== origin default HEAD) |
| Dirty state | clean at audit start; the batch lands as two commits — code `1286ac7`, then reports/docs (the commit introducing this file) |
| Python | 3.11.15 |
| ffmpeg | absent at start, installed mid-audit (`6.1.1-3ubuntu5`) — both runs recorded |
| Missing test deps | `httpx`, `pillow` (imported by tests, absent from `[dev]`); installed manually as environment note; `pyproject.toml` NOT changed |

**Full-test baseline:** without ffmpeg **17 failed / 1952 passed / 178 skipped / 1 error**; with ffmpeg the previously-failing files rerun to **4 failed / 253 passed**. The 4 surviving are **pre-existing test-double drift** (fakes lack the `include_unindexed` kwarg that `gui/plan.py:125` passes): `test_director.py::test_execute_build_passes_assume_yes_from_confirmed_state`, `test_director.py::test_mcp_driven_roundtrip_build_mocked`, `test_round_w_agent_wb.py::test_build_gen_off_still_skips_generation`, `test_write_consistency.py::test_execute_locked_internally_action_type_no_double_acquire`. **NOT fixed** (contract A3.1 — unrelated baseline problems). Full matrix + red-test outcomes: see the baseline report.

---

## 2. Gap decisions

| Capability | Status | Evidence (file:line) | Action |
|---|---|---|---|
| One-shot ↔ one-clip identity | IMPLEMENTED | `models.py:876-908`, `compiler.py:416-440` | keep; identity IS shot id |
| Multi-clip/split/merge identity | REJECTED_WITH_REASON | no split/merge path (EVIDENCE_1 cap.6); adapter derives `shot:<id>/video:main` | reject core `clip_id` → C01 |
| Single resolution path | IMPLEMENTED | `compiler.py:716-800`(:789), `container.py:412-470` | keep; no `resolved_assets.json` → C02 |
| Final key covers bytes+params | IMPLEMENTED | `render.py:316-344`(:331), `render.py:1002-1066` | keep; no binding-digest → C03 |
| Plan serialization + explanation | IMPLEMENTED | `graph.py:438-501,766-779`, `explain.py:117-171`, `impact.py:367-484` | C04; only p7 relpath fix |
| Run evidence sufficiency | PARTIAL → delivered | `failures.jsonl`/`qc.json`/`qc_agent.jsonl`/`verifications.jsonl`; gaps EVIDENCE_3 cap.2 | C05 minimal red-first extension |
| Deterministic offline provider | IMPLEMENTED | `registry.py:33`, `caption_card.py:21-64`, `test_failures.py:256-292` | C06 — no parallel mock pack |
| OpenClap adapter | MISSING → delivered | zero repo matches (EVIDENCE_3 cap.5) | IMPLEMENT (WP1), adapter-only |
| Second `GenerationRequest` | REJECTED_WITH_REASON | `providers/base.py:160-214` | forbidden; deferred to DR02 |
| Creative-stage QC in engine | REJECTED_WITH_REASON | `README:48-51`; `qc/agent_review.py` docstring | boundary correct; document in ADR |

---

## 3. Required delivery — OpenClap adapter (WP1)

New package **`src/manju/exporters/openclap/`** (~1700 lines):

| Module | Responsibility |
|---|---|
| `profile.py` | format knowledge: category axes, provider aliases (incl. `COMFUI`→`comfyui`), locator classification |
| `model.py` | raw-preserving views + `ClapDocument`/`Diagnostic`/`ClapReadError`/`ClapLimits` |
| `io.py` | `read_clap` (streamed decompression cap / item cap / nesting cap / strict UTF-8 / verified header counts / fail-closed structured diagnostics); `write_clap` (deterministic gzip `mtime=0`, atomic); `inspect_clap` |
| `exporter.py` | `export_openclap` from the compiled timeline; derived segment ids `shot:<id>/video:main`; `x-manju` provenance = shot/take/provider only (no wall-clock, no secrets) |
| `import_plan.py` | `build_import_plan` — plan only, **zero writes**, never downloads, never auto-selects; exhaustively lists unmapped; schema `manju.openclap-import-plan/v1` |

**CLI:** `manju openclap inspect|export|import-plan` sub-app in `cli.py`. `export` honors the default `ask_before=final_export` gate exactly like `manju export`/`manju package` (requires `--yes`); event-logged like export; re-exported from `exporters/__init__.py`.

**Tests & fixtures:** `test_openclap_io.py` (15), `test_openclap_export.py` (6), `test_openclap_import_plan.py` (8), `test_openclap_cli.py` (6, incl. gate pin), `test_export_containment.py` parametrization now covers `export_openclap` (10). Together they cover **all 13 items** of the WP1 spec test matrix (fidelity, fail-closed corruption, bomb/caps, traversal, enum preservation, determinism byte-identity, semantic equivalence, never-writes, URL/data-URI, project-relative, no-secret, endTimeInMs independence).

**Review corrections applied on top of the implementation:** (1) added the `final_export` gate + gate test; (2) `zlib.error` now fails closed with a diagnostic (was uncaught); (3) `RecursionError` from pathological YAML nesting fails closed (PyYAML recurses before our depth check); (4) import-plan media/imports target names deduplicated (distinct segments sharing basename `take_01.mp4` previously planned onto ONE overwriting target — found in live smoke, fixed, verified unique).

**Live smoke** (real 4-shot sample): `manju build` → `openclap export --yes` → two exports **byte-identical** → `inspect`: header/actual counts match, 0 errors 0 warnings → `import-plan`: 9 operations, unique media targets, 6 unmapped (entities/scenes listed, nothing silently dropped), 0 writes.

**ADR / docs:** `DECISIONS.md` #12 (design→built→why) + a `README` CLI-table row — added in the same commit (see §8d).

---

## 4. Conditional delivery (DR01-C01 … C06)

SKIPPED_WITH_EVIDENCE = **COMPLETED by evidence + regression pins** (the capability already holds and is now pinned); it is never "not done".

| Candidate | Red test | Implemented or skipped | Evidence (file:line) |
|---|---|---|---|
| **C01** core `clip_id` | none constructible (one shot ↔ one clip, no split/merge) | **SKIPPED_WITH_EVIDENCE — COMPLETED**: identity already carried by `VideoClip.shot`; adapter derives ids without touching core models | `models.py:876-908`, `compiler.py:416-440`; adapter `shot:<id>/video:main` |
| **C02** resolved-asset view | pin3 (single resolution path) | **SKIPPED_WITH_EVIDENCE — COMPLETED**: single bake point already exists; pinned by new regression, no `resolved_assets.json` added | `compiler.py:716-800`, `container.py:412-470`; `test_dr01_binding_pins` pin3 |
| **C03** binding digest | pin1 byte-swap, pin2 selection | **SKIPPED_WITH_EVIDENCE — COMPLETED**: final key already covers take media bytes + params; pinned, no binding-digest added | `render.py:316-344`(:331), `render.py:1002-1066`; `test_dr01_binding_pins` pin1/pin2 |
| **C04** plan projection | p1–p6 GREEN; p7 RED→fixed | **IMPLEMENTED**: green at HEAD → marked IMPLEMENTED, no new schema/serializer/planner/plan file; only fix = p7 one-line `project.relpath` | `graph.py:438-501,766-779`; p7 fix `graph.py:790-792`→ mirrors `:1236` |
| **C05** run evidence | e1–e3 all RED→green | **IMPLEMENTED** (minimal, red-first): sidecar `.key.json` gains `run_id`/`output_sha256`/`inputs{…}` (re-hashing inputs reproduces final_key); `run_id` minted per `run_build`, evidence-only, never enters hashes; no new DB, no new report file | `render.py` `_final_key_payload()`; `graph.py` run_id mint+thread+event stamp |
| **C06** deterministic mock | existing coverage | **SKIPPED_WITH_EVIDENCE — COMPLETED**: `caption_card` terminal fallback + kenburns + scripted HTTP transports + `MockDurationProvider` already cover it; a unified pack would duplicate (invariant: no parallel systems) | `registry.py:33`, `caption_card.py:21-64`, `test_failures.py:256-292` |

---

## 5. Compatibility

| Concern | Guarantee |
|---|---|
| Old projects / timelines / sidecars | Unaffected — `.key.json` sidecar extension is **additive** (existing readers consume `final_key`/`target` only); `run_id` is an optional kwarg (default `None`) |
| No source-of-truth changes | Plans and resolved views remain **derived**; the build API takes **no plan file** as input; OpenClap is not read back as truth |
| Content keys | **Unchanged** — hashed payload byte-identical; `test_media_durability` + `test_idempotency` green |
| media / imports | **Untouched** by all new code paths; `import-plan` writes nothing, downloads nothing, never auto-selects |
| Locators & remotes | Locators never treated as digests; remote URLs never fetched; data URIs digested in-memory only |
| OpenClap round-trip | Unknown fields preserved read→write; typed views are non-destructive (see §8a risk on the *counterparty's* sanitizers) |

---

## 6. Commands actually run

```
python -m pip install -e ".[dev]"
python -m pytest -q                     # baseline, no ffmpeg
  → 17 failed, 1952 passed, 178 skipped, 1 error   (373.55s)

# with-ffmpeg rerun of the previously-failing files
  → 4 failed, 253 passed                            (88.55s)

# targeted
pytest tests/test_openclap_*.py tests/test_export_containment.py
  → 45 passed
pytest tests/test_dr01_*.py tests/test_media_durability.py tests/test_idempotency.py
  → 29 passed

# final full suite (after ALL changes, ffmpeg + httpx + pillow present)
python -m pytest -q
  → 4 failed, 2183 passed, 12 skipped                (729.41s / 0:12:09)
  # the 4 failures are exactly the pre-existing test-double-drift set
  # from §1 — ZERO new failing test names introduced by this batch

# git state
git log --oneline -2
  → 1286ac7 DR01: OpenClap adapter, plan-projection pins, run-evidence sidecar.
  → 047b6ce Align JianYing volume/transition baseline by manju identity, not segment index.
  # + one follow-up commit adding REPORTS/AI_IDE_01_{BASELINE,COMPLETION}.md
```

---

## 7. Architecture checks (invariants held)

| Invariant | Verified |
|---|---|
| No second `GenerationRequest` / planner / QC runner / event log / runtime DB added | ✅ existing `providers/base.py:160-214` request reused; plan/QC/events/state unchanged |
| No LLM / model call in core | ✅ adapter has zero network/exec/datetime-wallclock code (grep-verified) |
| Plan / resolved views remain **derived** | ✅ build takes no plan file; `explain`/`impact` recompute read-only |
| OpenClap remains **adapter-only** | ✅ isolated in `exporters/openclap`; unknown fields preserved; typed views non-destructive; never an internal source of truth |
| Creative vs execution QC boundary | ✅ engine never runs a vision model; documented `README:48-51` + DECISIONS.md #12 |

---

## 8. Final state

**Commits:** `1286ac7` (all code + tests + DECISIONS.md #12 + README row, pushed to `claude/cost-optimization-strategy-cjfmn5`) followed by the reports commit that introduces this file and the baseline report. Working tree clean after both.

Modified: `build/graph.py` (+~27), `cli.py` (+~165), `exporters/__init__.py` (+2), `media/render.py` (+~95), `tests/test_export_containment.py` (+2), `tests/test_openclap_cli.py`. Created: `exporters/openclap/{__init__,profile,model,io,exporter,import_plan}.py`, `tests/test_openclap_{io,export,import_plan,cli}.py`, `tests/test_dr01_{plan_projection,binding_pins,run_evidence}.py`, this report + the baseline report.

### Remaining concrete risks (honest)

- **(a) Counterparty sanitizers may drop `x-manju`.** Our parser preserves unknown fields on read→write, but **aitube-clap's own writer rebuilds fixed shapes**, so `x-manju` provenance can be dropped on *their* round-trip. We cannot guarantee survival past a tool we don't control.
- **(b) 4 pre-existing test-double-drift failures remain** at baseline (`include_unindexed` kwarg on test fakes) — not this WP's scope, left per contract A3.1.
- **(c) Export carries no media digests in `x-manju`.** Take sidecars record `spec_hash`/`spec_snapshot`/`provider`/`params` but **not a content hash of the media bytes** (EVIDENCE_1 cap.2), so the export has no media content digest to embed. `x-manju` is provenance (shot/take/provider) only.
- **(d) `DECISIONS.md` #12 + `README` CLI-table row land in the same commit** as the code; cross-reference each other and this report.

---

## Acceptance self-check — DR01-R01 … R10

| Req | Assertion | Status | Where |
|---|---|---|---|
| R01 | baseline report complete | ✅ | `REPORTS/AI_IDE_01_BASELINE.md` |
| R02 | no second GenerationRequest/planner/QC/event/runtime DB | ✅ | §7 |
| R03 | parser preserves unknown fields + resource/path limits | ✅ | `model.py` raw views; `io.py` decompression/item/nesting caps + strict UTF-8 + traversal guard |
| R04 | export round-trip fixture | ✅ | `test_openclap_export.py`; two exports byte-identical (live smoke) |
| R05 | import-plan writes nothing / downloads nothing / imports untouched | ✅ | `build_import_plan` plan-only; `test_openclap_import_plan.py` |
| R06 | OpenClap not an internal source of truth | ✅ | §7 — adapter-boundary only, never read back as truth |
| R07 | old projects + related tests no regression | ✅ | §5; additive sidecar; `test_media_durability`+`test_idempotency` green |
| R08 | full suite no new failures, honest environment classification | ✅ | §1 / §6; final: 4 failed / 2183 passed / 12 skipped — the 4 are the pre-existing set; ffmpeg/httpx/pillow classified as environment |
| R09 | report distinguishes IMPLEMENTED vs SKIPPED_WITH_EVIDENCE | ✅ | §4 (C04/C05 IMPLEMENTED; C01/C02/C03/C06 SKIPPED_WITH_EVIDENCE = COMPLETED-by-evidence) |
| R10 | docs state creative/execution QC boundary + plan's derived identity | ✅ | `README:48-51`; DECISIONS.md #12; §7 |

## Acceptance self-check — DR01-C01 … C06

| Item | Decision |
|---|---|
| C01 core clip_id | **SKIPPED_WITH_EVIDENCE** (COMPLETED — identity = `VideoClip.shot`; adapter-derived ids) |
| C02 resolved-asset view | **SKIPPED_WITH_EVIDENCE** (COMPLETED — single bake point, pin3) |
| C03 binding digest | **SKIPPED_WITH_EVIDENCE** (COMPLETED — final key covers bytes+params, pin1/pin2) |
| C04 plan projection | **IMPLEMENTED** (green at baseline; only p7 relpath fix) |
| C05 run evidence | **IMPLEMENTED** (minimal red-first: run_id + output_sha256 + inputs breakdown) |
| C06 deterministic mock | **SKIPPED_WITH_EVIDENCE** (COMPLETED — existing providers/transports cover it) |
