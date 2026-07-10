# AI IDE 02 — Baseline Audit (基线审计)

Deliverable for **Manju Deep Research 02 — bound acceptance evidence (expectations · packet/verdict v2 · derived assurance)**. This file is the frozen baseline of HEAD before any WP0–WP4 change; the paired build report is `REPORTS/AI_IDE_02_COMPLETION.md`.

---

## 1. Header — branch / commit / date / environment

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` (== origin default HEAD) |
| Base commit audited | `5aab90e` (DR01 baseline+completion reports; code base `1286ac7` + DR01) |
| Date | 2026-07-10 |
| Python | 3.11.15 |
| ffmpeg | `6.1.1` present (installed in the DR01 session, carried over) |
| Test deps | `httpx` + `pillow` present (installed in DR01 as environment note; `pyproject.toml` NOT changed) |

### Full-test baseline

| Run | Result |
|---|---|
| `python -m pytest -q` (whole suite at HEAD `5aab90e`, ffmpeg + httpx + pillow present) | **4 failed / 2183 passed / 12 skipped** |

This is the DR01 final regression carried unchanged to this HEAD — no code moved between `1286ac7` and the audit. The **honest comparison baseline for this batch = 4 pre-existing failures**, everything else green.

### The 4 surviving pre-existing failures (classified, NOT fixed)

All four are **test-double drift**: the test fakes lack the `include_unindexed` kwarg that `gui/plan.py:125` now passes → `TypeError: unexpected keyword argument 'include_unindexed'`. Unrelated to this batch's scope; left alone so the diff stays scoped (per contract, unrelated baseline problems are not fixed).

| # | Failing test |
|---|---|
| 1 | `tests/test_director.py::test_execute_build_passes_assume_yes_from_confirmed_state` |
| 2 | `tests/test_director.py::test_mcp_driven_roundtrip_build_mocked` |
| 3 | `tests/test_round_w_agent_wb.py::test_build_gen_off_still_skips_generation` |
| 4 | `tests/test_write_consistency.py::test_execute_locked_internally_action_type_no_double_acquire` |

---

## 2. Capability matrix (候选能力裁决)

Statuses: `ALREADY_IMPLEMENTED / PARTIAL / MISSING / REJECTED_WITH_REASON / BLOCKED`. Evidence file:line from the `DR02_EVIDENCE_*` audits.

| Capability (contract §3) | Status | Evidence (file:line) |
|---|---|---|
| provider `GenerationRequest` | ALREADY_IMPLEMENTED | `providers/base.py:160-214`; reused, NOT duplicated |
| `Quality.must_show` / `avoid` | ALREADY_IMPLEMENTED | `models.py:139-142`; in `spec_payload` (`spec.py:119`) so edits restage takes |
| `Continuity.locks` | ALREADY_IMPLEMENTED | `models.py:134-136`; NOT in spec payload; only `prop:` refs validated (`check.py:242`); grammar `prop:`/`character:`/`scene` (`skills/manju/SKILL.md:188`) |
| `compute_spec_hash` | ALREADY_IMPLEMENTED | `spec.py:131-138` (`SPEC_VERSION=2` :39); `spec_payload` :94-128; `diff_spec_fields` :141-156 |
| `hash_file` | ALREADY_IMPLEMENTED | `hashing.py:38-44` (`"sha256:"` prefix :19) |
| qc brief / verdict / `qc_agent.jsonl` | PARTIAL | pipe exists end-to-end (`agent_review.py`: brief :236-321, intake :604-734, merge :785-865) BUT: brief never persisted (no issuance), verdict binds by hashing CURRENT file at intake (`_resolve_take` :599-600), no spec/expectation binding → the same-name-replacement race misbinds. THE core gap of this batch |
| consistency units / contact sheets | ALREADY_IMPLEMENTED | `_consistency_units` :374-477; member-map binding; unit staleness :892-893 |
| QC report | ALREADY_IMPLEMENTED | `qc/report.py` qc.json/qc.md/repair_plan.yaml; `QCReport.ok` = no error items (`checks.py:69-71`) |
| `ShotStatus.review` / `approved` | ALREADY_IMPLEMENTED | `models.py:168-204` three-state `review_state`; ZERO coupling to `qc/` today (grep-verified) — maps to contract's `human_review_state` |
| Director suggestion / repair / redo | ALREADY_IMPLEMENTED | `suggest_next` `director.py:1128-1214` (reads `reports/qc.json` read-only); `repair --auto` `cli.py:1704-1739`; `_derive_action` `report.py:92-126` |
| skills `visual-qc-review` / `repair-loop` | ALREADY_IMPLEMENTED | single-file `SKILL.md` each; verdict contract `SKILL.md:13-32`; pinned by `test_skill_content.py` (<500 lines, frontmatter, no LLM vendoring) |
| atomic writer / JSONL / events / failures | ALREADY_IMPLEMENTED | `yamlio.py` `atomic_write_text`; `failures.py` `_ledger_lock`+rotate+fsync :55-93,343-364; `events.py` :21-33 |
| Media-bound verdict against VIEWED bytes (race defense) | MISSING | intake-time rehash = misbind; no test anywhere submits a verdict after same-name byte swap (audit: "None … pins any content-hash binding [at intake]") — WP0 red test proves |
| `ExpectationSet` compile | MISSING | no expectation concept in repo |
| Packet v2 / Verdict v2 | MISSING | no packet persistence, no v2 schema |
| Deterministic diff + assurance states | MISSING | `QCReport.ok` exists but no expectation diff, no accepted/rejected/unknown/stale derivation |
| Read-only repair proposal (expectation-driven) | PARTIAL | `repair_plan.yaml` + `suggest_next` exist (mechanic, not expectation-driven) → extend, no new command |

---

## 3. The confirmed race (the hinge of this batch)

The audit turned on **how the verdict binds to media bytes**. It does not bind to what the reviewer *viewed* — it re-hashes the current file at intake.

**Mechanism (legacy intake, at audit HEAD):**

| Step | Behavior | Evidence |
|---|---|---|
| Brief | `qc_brief` *computes* a `take_hash` and returns it, but **nothing about the brief is persisted** — no brief id, no brief file, no brief event, no spec_hash | `agent_review.py:236-321,296`; EVIDENCE_1 §1 |
| Verdict contract | reviewer is **never asked to echo the hash they judged** — no hash field in the payload shape | `agent_review.py:115-165`; EVIDENCE_1 §1 |
| Intake bind | `_resolve_take` resolves the take by name and **hashes the CURRENT bytes on disk at intake time** (`_safe_hash(take.media_path)`), written into the record — answer (b): recomputed at intake, not carried from brief, not read from payload | `_resolve_take` `agent_review.py:582-601` (hash at **:599-600**), stored at :702; EVIDENCE_1 §2 |

**Scenario:** reviewer views media **A**; the take file is swapped out-of-band to **B** (same shot, same take name, same path); the A-formed verdict is submitted late. Legacy intake resolves the take by name and hashes the current file — now **B** — and silently records the verdict as if it judged B. There is **no submitted-hash comparison, no brief-issuance hash to diff, no mtime guard** (`agent_review.py:588,599-600`). (The normal build path never overwrites a take in place — `register_take` mints fresh names and refuses to clobber, `container.py:486-489` — so this requires out-of-band replacement; but the intake itself has zero defense if it occurs.)

**WP0 red test (`tests/test_dr02_race.py`) — verbatim outcome:** RED against legacy intake. The recorded failing assertion is that the **stored `take_hash` equalled replacement B's hash** — `sha256:5299...136b == itself` — proving `_resolve_take` rebinds at intake. It goes **GREEN via the v2 path** (the record is stored `binding:"stale"`, still bound to A).

---

## 4. Design rulings (裁决, recorded for the report)

| # | Ruling |
|---|---|
| 1 | **One evidence log.** v2 records append to `qc_agent.jsonl` with a `schema` field; the legacy reader skips schema-bearing lines; the v2 reader skips legacy lines. No second ledger. |
| 2 | **Packets.** `reports/qc_packets/<pkt_id>.json`, content-derived verifiable id (`pkt_` + sha12 of the canonical payload minus id), immutable, needed only brief→intake (verdict records carry all binding fields; assurance never needs packet files). |
| 3 | **Expectation scope.** ONLY `must_show` (present) / `avoid` (absent) / `continuity.locks` (external_consistency); existing deterministic machine checks stay in `run_qc` and gate acceptance via the separate "no policy-blocking error" condition — **not duplicated as expectations**. |
| 4 | **Binding split at intake.** payload-invalid (packet missing/forged, subject mismatch, unknown expectation id, malformed) ⇒ reject batch, zero writes; world-moved (take path/media sha/spec hash/expectation digest changed) ⇒ store record marked `binding:"stale"` with `binding_failures` — reviewer work preserved as history, never current evidence. Intake re-hashes the current file ONLY for comparison; the stored `media_sha256` always comes from the packet the reviewer actually reviewed. |
| 5 | **Expectation ids content-derived** (`exp:<shot>:<kind>:<sha8 of polarity+NFC-normalized statement>`) → reorder-stable; `source_path` keeps the authored index as provenance; exact duplicates collapse. |
| 6 | **Assurance precedence.** `not_reviewable > no_explicit_expectations > unreviewed/legacy_reviewed > stale > rejected > unknown > accepted`; `accepted` additionally requires `QCReport` has no policy-blocking error + human_review read-only from `ShotStatus.review_state`. |
| 7 | **Known asymmetry encoded in tests.** quality edits move `spec_hash` AND expectation digest; locks edits move ONLY the expectation digest (continuity not in spec payload) — steps 5/6 independently tested. |
| 8 | **Repair proposal = pure data** from assurance (rejected/unknown only), `do_not_execute_automatically:true`, surfaces via the `qc.json` assurance block → `suggest_next` picks it up (no new command family). |
