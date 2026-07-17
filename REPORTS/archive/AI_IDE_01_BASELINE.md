# AI IDE 01 — Baseline Audit (基线审计)

Deliverable for **Manju Deep Research 01 — Timeline-first 运行时与 OpenClap 交换格式, AI IDE 可执行修订版 v4** (Part A = binding research contract). This file is the frozen baseline of HEAD before any WP1–WP4 change; the paired build report is `REPORTS/AI_IDE_01_COMPLETION.md`.

---

## 1. Header — branch / commit / date / environment

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` (== origin default HEAD) |
| Base commit audited | `047b6ce` |
| Date | 2026-07-10 |
| Python | 3.11.15 |
| ffmpeg | **ABSENT at session start**, installed mid-audit (`6.1.1-3ubuntu5`, apt) — both suite runs recorded below |
| Missing test deps | `httpx` (imported by `test_board_serve`) and `pillow` (imported by `test_branding`) are absent from the `[dev]` extras; installed manually as an **environment note only**, `pyproject.toml` NOT changed (out of scope) |

### Full-test baseline (both runs)

| Run | ffmpeg | Result | Wall |
|---|---|---|---|
| `python -m pytest -q` (whole suite) | absent | **17 failed, 1952 passed, 178 skipped, 1 error** | 373.55s |
| rerun of the 12 previously-failing files | present | **4 failed, 253 passed** | 88.55s |

The without-ffmpeg run is dominated by ffmpeg-missing failures (plus the same test-double drift below). With ffmpeg present, the **honest comparison baseline for this WP = 4 pre-existing failures**, everything else green.

### The 4 surviving pre-existing failures (classified, NOT fixed)

All four are **test-double drift**: the test fakes lack the `include_unindexed` kwarg that `gui/plan.py:125` now passes → `TypeError: unexpected keyword argument 'include_unindexed'`. They are unrelated to this WP.

| # | Failing test |
|---|---|
| 1 | `tests/test_director.py::test_execute_build_passes_assume_yes_from_confirmed_state` |
| 2 | `tests/test_director.py::test_mcp_driven_roundtrip_build_mocked` |
| 3 | `tests/test_round_w_agent_wb.py::test_build_gen_off_still_skips_generation` |
| 4 | `tests/test_write_consistency.py::test_execute_locked_internally_action_type_no_double_acquire` |

**Per research contract A3.1 these are explicitly NOT fixed in this batch** — unrelated baseline problems are left alone so the diff stays scoped.

---

## 2. Two hash systems (澄清 — the audit hinged on this)

The audit turned on **not conflating** the two independent hashes Manju maintains. They answer different questions and one is byte-blind by design:

| Hash | Where | What it hashes | Media bytes? |
|---|---|---|---|
| **Timeline compile fingerprint** — `meta.compiled_from` | `CompileInput.fingerprint()` in `timeline/compiler.py:113-161` | specs + take **paths** + probed **durations** + rules/packaging | **NO** — path string + probed duration only |
| **Final / segment content key** — `final_content_key()` / `_segment_cache_key()` | `media/render.py:1002-1066` / `:316-344` | normalized timeline JSON + per-segment keys (each starts with `hash_file(src)` at `render.py:331`) + ASS + audio + overlay-image + encoding + target | **YES** — via `hash_file` (`core/hashing.py:38-44`) |

Consequence pinned by the red program (§4): swapping the selected take's media bytes but keeping the same filename + probed duration leaves `compiled_from` **unchanged** yet changes the **final content key** (segment `hash_file` differs) → re-render. Selecting a different take changes **both**.

---

## 3. Capability matrix (候选能力裁决)

Statuses drawn only from `{IMPLEMENTED, PARTIAL, MISSING, REJECTED_WITH_REASON, SKIPPED_WITH_EVIDENCE}`. Evidence file:line from the `EVIDENCE_*` audit reports.

| 候选能力 (candidate capability) | 状态 | 代码证据 (file:line) | 红灯测试或现有测试 | 裁决 |
|---|---|---|---|---|
| One-shot ↔ one-clip stable identity | **IMPLEMENTED** | `VideoClip.shot` `models.py:876-908`; compiler emits exactly one clip per shot `compiler.py:416-440` | `test_compiler.py::test_same_input_yields_byte_identical_json:53` | keep — core identity IS the shot id |
| Multi-clip / split / merge identity need | **REJECTED_WITH_REASON** | no split/merge path anywhere (`clip_id`/`split`/`merge` grep → only caption/anchor, EVIDENCE_1 cap.6); one-shot↔one-clip is bijective; adapter derives `shot:<id>/video:main` | none constructible (no failing scenario) | reject core `clip_id`; derived IDs live in adapter only → **DR01-C01 SKIPPED_WITH_EVIDENCE** |
| Single resolution path for active assets | **IMPLEMENTED** | compiler bakes `selected_take`→`VideoClip.source` once `compiler.py:716-800` (esp. `:789`); all name→file via `Project.takes()`/`get_take()` `container.py:412-470` | NEW pin `test_dr01_binding_pins` (pin3) | keep; no `resolved_assets.json` → **DR01-C02 SKIPPED_WITH_EVIDENCE** |
| Final key covers media bytes + effective params | **IMPLEMENTED** | `_segment_cache_key` starts with `hash_file(src)` `render.py:316-344` (esp. `:331`); `final_content_key` covers segments+ass+audio+overlay+encoding+target `render.py:1002-1066` | NEW pins `test_dr01_binding_pins` (pin1 byte-swap, pin2 selection); `test_idempotency::test_redo_selected_then_build_adds_exactly_one:119` | keep; no binding-digest add → **DR01-C03 SKIPPED_WITH_EVIDENCE**. Renderer id/version deliberately NOT added (would stale every cached final; no contract failure reaches it) |
| Plan stable serialization + dependency explanation | **IMPLEMENTED** | plan rows shot/reason/provider/estimated_cost `graph.py:438-501,766-779`; dry-run `--json` envelope carries no timestamps/run-ids `gui/plan.py:72-168`,`cli.py:906-938`; `explain` gives spec_hash/fingerprint/content-key verdicts `explain.py:117-171`; `impact` gives `changed_fields` `impact.py:367-484` | p1–p6 GREEN at HEAD; p7 RED→fixed | **DR01-C04 IMPLEMENTED**; only fix = p7 relpath, no new schema/serializer/planner |
| Run evidence sufficient (which run / input hashes / planned-vs-executed / output bytes / verifications / failure step) | **PARTIAL** | failures+cancel YES (`failures.jsonl` step/cause/evidence/hint; `BuildCanceled`); verifications YES (`qc.json`+`qc_agent.jsonl`+`verifications.jsonl`); MISSING build-level run_id, per-input hash manifest (only combined final_key), output mp4 sha never persisted (EVIDENCE_3 cap.2) | `test_dr01_run_evidence` e1–e3 RED at HEAD | **DR01-C05 IMPLEMENT minimally** — extend `.key.json` sidecar (run_id, output_sha256, inputs breakdown) + build-event run_id; no new DB/report file |
| Deterministic offline provider / fixture | **IMPLEMENTED** (as-designed) | `caption_card` always-available terminal fallback `registry.py:33`,`caption_card.py:21-64`; kenburns deterministic; scripted HTTP transports simulate provider_fail/rate-limit/timeout/content-rejection `test_failures.py:256-292`; `MockDurationProvider` exact durations | existing tests cited above | **DR01-C06 SKIPPED_WITH_EVIDENCE** — a unified mock "pack" would duplicate covered capabilities (invariant: no parallel systems) |
| OpenClap adapter | **MISSING** | zero matches for `clap`/`openclap`/`aitube`/`.clap` across the whole repo (EVIDENCE_3 cap.5) | new tests per WP1 spec | **IMPLEMENT (WP1)** — isolated in `exporters/openclap`; raw-preserving; resource/path limits; staged import-plan |
| Second `GenerationRequest` necessity | **REJECTED_WITH_REASON** | `providers/base.py:160-214` already carries project/shot/bible/spec_hash/duration/candidates/params/estimated_cost/routing_bias/max_retries/refs/should_cancel (EVIDENCE_2 cap.4) | n/a | forbidden by contract; acceptance contract deferred to DR02 |
| Creative-stage QC in engine | **REJECTED_WITH_REASON** | `README:48-51` creation/execution boundary; `qc/agent_review.py` docstring (engine never runs vision models; agent-eyes pipe is hash-bound); `skills/visual-qc-review` holds criteria | n/a | boundary already correct; document in ADR (DECISIONS.md #12) |

---

## 4. Red-test program outcomes (first-run @ HEAD → action)

Three test files author the red program: `tests/test_dr01_plan_projection.py` (p1–p7), `tests/test_dr01_binding_pins.py` (pin1–3), `tests/test_dr01_run_evidence.py` (e1–e3).

| Test | Assertion | First run @ HEAD | Action taken |
|---|---|---|---|
| p1 | plan semantic determinism | 🟢 GREEN | none — IMPLEMENTED at baseline (contract A7.1: "已经通过,标记 IMPLEMENTED") |
| p2 | evidence-noise independence (ledger spend row between dry-runs) | 🟢 GREEN | none |
| p3 | targeted invalidation | 🟢 GREEN | none |
| p4 | explainability union (dry-run row + explain + routing.resolve give provider/why/cost/spec_hash) | 🟢 GREEN | none |
| p5 | stable node identity (shot-keyed, order-independent content) | 🟢 GREEN | none |
| p6 | plan/build provider parity | 🟢 GREEN | none |
| p7 | project-relative timeline_path in dry-run `to_dict` | 🔴 **RED** (absolute path `graph.py:790-792`, leaked through MCP build tool) | one-line `project.relpath(...)` mirroring the real-build branch `graph.py:1236`; green. Invariant: derived JSON paths project-relative. **No** schema/serializer/planner added |
| pin1 | media-byte swap (same filename) changes final key | 🟢 GREEN | NEW pinning regression added → DR01-C03 SKIPPED_WITH_EVIDENCE |
| pin2 | `selected_take` flip changes final key | 🟢 GREEN | NEW pinning regression added → DR01-C03 SKIPPED_WITH_EVIDENCE |
| pin3 | OTIO + JianYing embed exactly the compiler-baked `clip.source` (single resolution path) | 🟢 GREEN | NEW pinning regression added → DR01-C02 SKIPPED_WITH_EVIDENCE |
| e1 | final `output_sha256` recorded | 🔴 **RED** (never persisted) | minimal extension: sidecar `output_sha256 = hash_file(mp4)` |
| e2 | per-input breakdown re-hashes to the same final_key | 🔴 **RED** (only combined key) | sidecar `inputs {timeline, segment_keys, ass_sha256, audio, encoding, target[, overlay_images, look]}` |
| e3 | build-level run_id correlation | 🔴 **RED** (no build run id) | `run_id` minted per `run_build`, threaded to render sidecar + stamped on `events.jsonl` build event |

**e1–e3 fix discipline:** `render.py` refactored to a mechanical `_final_key_payload()` extraction whose hashed payload is **byte-identical** to before; external guard `test_media_durability` (asserts `sidecar.final_key == final_content_key`) stayed green → the key value is unchanged, only additive evidence fields were added. Orchestrator review correction: sidecar inputs field renamed `timeline_compiled_from` → `timeline` (its value is the normalized timeline dump, distinct from `meta.compiled_from`). Re-ran dr01 + media_durability + idempotency → **29 passed**.

---

## 5. Additional baseline facts

- Exporters have **no shared base class** — free functions + conventions (EVIDENCE_3 cap.4).
- `DECISIONS.md` is a flat `## N. <title>` log (design→built→why); next free number was **12** (now the DR01 entry).
- `events.jsonl` at project root `{ts, actor, action, detail}`; `reports/failures.jsonl` rotating, ids `F-xxxx`; `.manju/state.sqlite` rebuildable (`runs`/`jobs`/`intents`).
- Roundtrip **staged-import precedent** already exists for JianYing/OTIO (`manju roundtrip`, baselines under `exports/<kind>/.baseline/`).
