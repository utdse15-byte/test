# AI IDE 02 Completion

Build report for **Manju Deep Research 02 — bound acceptance evidence (expectations · packet/verdict v2 · derived assurance)** (research contract; section skeleton per §10). Paired baseline: `REPORTS/AI_IDE_02_BASELINE.md`. Final-run numbers and git state below were filled by the orchestrator after the last full-suite run.

---

## Repository

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| Base commit | `5aab90e` (DR01 baseline+completion reports; code base `1286ac7` + DR01) |
| Pre-existing work preserved | DR01 already pushed — code `1286ac7`, reports `5aab90e`; the **4 pre-existing `include_unindexed` test-double-drift failures untouched** (baseline §1) |
| DR02 core landed | `e63511c` "DR02 core: bound acceptance evidence — expectations, packet/verdict v2, derived assurance." (WP0–3) |
| DR02 surface wiring landed | `a9a190d` "DR02 WP4: assurance surfaces — qc summary, qc.json block, director advisories, MCP parity, skill contracts." — `qc/report.py`, `cli.py`, `build/director.py`, `mcp/tools.py`, `skills/visual-qc-review/SKILL.md`, `skills/repair-loop/SKILL.md`, `tests/test_dr02_surfaces.py` (WP4); working tree clean but for these two reports |
| Final git state | `5aab90e` (base) → `e63511c` (DR02 core) → `a9a190d` (DR02 WP4) → the reports commit that adds this file + the baseline; working tree clean after it |

Observed `git log --oneline -3` at report time (read-only):

```
a9a190d DR02 WP4: assurance surfaces — qc summary, qc.json block, director advisories, MCP parity, skill contracts.
e63511c DR02 core: bound acceptance evidence — expectations, packet/verdict v2, derived assurance.
5aab90e DR01: baseline audit + completion reports.
```

---

## Existing systems reused (not duplicated)

| System | File / symbol | How reused |
|---|---|---|
| provider `GenerationRequest` | `providers/base.py:160-214` | untouched; no second request type |
| spec hash | `spec.py` `compute_spec_hash:131-138` / `spec_payload:94-128` / `diff_spec_fields:141-156` | hashes still route through `spec.py`; the expectation digest is a **separate** binding axis, not a re-derivation |
| `hash_file` | `hashing.py:38-44` (`"sha256:"` prefix :19) | `media_sha256` is the existing whole-file hash |
| qc pipe | `agent_review.py` brief :236-321 / intake :604-734 / merge :785-865 | extended in place — packet issuance in briefs, v2 branch in intake; no parallel pipe |
| consistency units | `_consistency_units:374-477` | unit member-map packets reuse it; intake fully supports units |
| QC report | `qc/report.py`; `QCReport.ok` `checks.py:69-71` | assurance block added to `qc.json`/`qc.md`; `repair_plan.yaml` **unchanged** |
| `ShotStatus.review` / `approved` | `models.py:168-204` (`review_state`) | read-only as `human_review_state`; zero writes back |
| director suggest | `director.py:1128-1214` | advisory `_assurance_suggestions` folded in, `action=None`, tolerant of old `qc.json` |
| skills | `visual-qc-review/SKILL.md`, `repair-loop/SKILL.md` | v2 binding sections added; still single-file; `test_skill_content.py` pins green |
| atomic / JSONL patterns | `yamlio.py` `atomic_write_text`; `failures.py` `_ledger_lock`+rotate+fsync :55-93,343-364; `events.py:21-33`; `agent_review.py:715-721` | v2 appends + content-addressed packet writes copy these; torn-line skip-and-count on read |

---

## Scope result

| WP | Status | Files | Tests |
|---|---|---|---|
| WP0 — race red test | **done** (RED recorded at HEAD, GREEN via v2) | `tests/test_dr02_race.py` | race red→green (stored `take_hash == B` under legacy; `binding:"stale"` bound to A under v2) |
| WP1 — expectation compile | **done** | `qc/expectations.py` (159 lines) | 16 |
| WP2 — packet/verdict v2 intake | **done** | `agent_review.py` (+~570 lines) | 18 + 1 (orchestrator) |
| WP3 — derived assurance | **done** | `qc/assurance.py` (~340 lines) | 15 + 1 (orchestrator) |
| WP4 — surface wiring | **done** | `qc/report.py`, `cli.py`, `build/director.py`, `mcp/tools.py`, `visual-qc-review/SKILL.md`, `repair-loop/SKILL.md` (6 files) | 13 (`tests/test_dr02_surfaces.py`) |

**Totals:** WP1–3 = **3 production files, 51 tests incl. orchestrator's +3** (run-`run_qc`-once threading, `test_13` deterministic-QC acceptance gate + per-shot scoping, echoed-field-contradiction reject + test). WP4 = 6 production files, 13 tests. **9 production files total (≤14 budget).**

---

## Binding proof — media A → replace with B → old verdict stored `binding:"stale"` bound to A

Live e2e smoke on a real 4-shot sample project:

| Step | Action | Result |
|---|---|---|
| 1 | Set `quality.must_show` on S001 (2 items) → `manju qc brief --shots S001` | issued packet **`pkt_f482be2a4cbe`** with 2 content-derived expectation ids |
| 2 | v2 verdict all-present → intake | `{bound: 1}` → `manju qc`: "验收 assurance: **accepted 1, no_explicit_expectations 3**"; exit ok |
| 3 | Second verdict with one absent | "rejected — required expectation(s) failed: **`exp:S001:must_show:dadba8f2`**" named in `manju qc`; exit code STILL ok (separate axis); `manju director suggest` surfaced an advisory repair suggestion citing the exact id, `action=None` |
| 4 | **THE RACE, live:** fresh brief on media A (packet `pkt_f482be2a4cbe`, sha `27d8…`) → take file replaced out-of-band with different bytes B (same name, ffmpeg blue clip) → A-formed verdict submitted late | intake **`{stale: 1}`**, record bound to **A's sha from the packet**, assurance = **stale** with `stale_reasons ['media']`. The old verdict **NEVER bound to B**. |

**WP0 red-test verbatim assertion:** against legacy intake the stored `take_hash` **equalled replacement B's hash** — `sha256:5299...136b == itself` — proving `_resolve_take` rebinds by re-hashing the current file at intake (`agent_review.py:599-600`). The v2 path stores the record `binding:"stale"`, still bound to A — matching the live `{stale: 1}` / `['media']` above.

---

## Schemas

| Schema | Identity | Binding semantics |
|---|---|---|
| `manju.qc.expectations/v1` | one `ExpectationSet` per shot; each id `exp:<shot>:<kind>:<sha8 of polarity+NFC-normalized statement>` (kinds `must_show`/`avoid`/`continuity.locks`→`external_consistency`) | set digest = hash over sorted expectation identities **excluding `source_path` and `spec_hash`** → reorder-stable, an axis independent of `spec_hash`; exact duplicates collapse |
| `manju.qc.packet/v2` | `pkt_<sha12 of canonical payload minus id>` — content-addressed, verifiable, idempotent; one file `reports/qc_packets/<pkt_id>.json`, immutable | carries the subject (shot / unit member-map) + `media_sha256` + `spec_hash` + `expectation_digest` the reviewer actually saw; needed only brief→intake (verdict records carry all binding fields, so assurance never reads packets) |
| `manju.qc.verdict/v2` | appended line in the **same** `reports/qc_agent.jsonl`, tagged with a `schema` field (legacy reader skips schema-bearing lines; v2 reader skips legacy) | stored `media_sha256`/`spec_hash`/`expectation_digest` come from the **packet**, never re-hashed at intake; world-moved ⇒ `binding:"stale"` + `binding_failures`; payload-invalid ⇒ zero-write reject |
| `manju.qc.assurance/v1` | derived per shot at read time, **never persisted as truth** (test 11: `.manju` deleted, still computes) | pure diff of current expectations vs bound verdict evidence → 8 states by precedence (`not_reviewable > no_explicit_expectations > unreviewed/legacy_reviewed > stale > rejected > unknown > accepted`); `accepted` additionally requires `QCReport` has no policy-blocking error + `human_review` read-only from `ShotStatus.review_state` |

---

## Authority proof — no source of truth written

| Claim | Proof |
|---|---|
| Nothing writes source truth | expectations are **derived** from `must_show`/`avoid`/`continuity.locks`; packets are content-addressed evidence; verdicts are **appended** evidence lines; assurance is **derived at read time** (never persisted); proposals are pure data — none mutates a shot spec, take, or selection |
| `ShotStatus` read-only | `human_review_state` reads `ShotStatus.review_state` (`models.py:168-204`); no write-back to `.review`/`.approved` |
| No model call in core | acceptance is `PASS`/`FAIL`/`UNKNOWN` from the **pure diff** — the engine never runs a vision model; reviewer judgment enters only as v2 verdict evidence |
| No second QC / router / ledger | v2 records land in the **same** `qc_agent.jsonl`; packets under `reports/qc_packets/`; assurance surfaces via the existing `qc.json` block and `suggest_next` — **no new command family** |

---

## Compatibility

| Legacy case | Proof |
|---|---|
| Legacy verdicts still readable | the merge/staleness reader consumes pre-v2 lines unchanged; they surface as `unreviewed`/`legacy_reviewed`, **never `accepted`** (v2-only gate) |
| Legacy reader skips v2 lines byte-identically | schema-tagged lines are skipped by the legacy reader; the legacy record path is **byte-identical** to pre-batch |
| Old `qc.json` without an assurance block | `manju director suggest` (`_assurance_suggestions`) is **tolerant of old `qc.json`** → still works, no crash |
| Malformed lines | **skipped and counted**, never fatal (torn-line discipline preserved) |
| Skill pins | `test_skill_content.py` **green** after v2 skill edits (still single-file, <500 lines, frontmatter intact, no LLM vendoring) |

---

## Tests

| Command | Result |
|---|---|
| `pytest tests/test_dr02_race.py` (WP0) | RED at HEAD (legacy intake), GREEN via v2 path |
| `pytest tests/test_dr02_* tests/test_qc_agent.py tests/test_qc_consistency.py tests/test_content_qc.py` (after WP0–3 core, before surfaces) | **99 passed, 4 skipped** (pre-existing tesseract-gated) |
| `pytest tests/test_dr02_surfaces.py` (WP4) | **13 passed** (agent surface run: 214 passed / 4 skipped / 2 failed = the 2 known pre-existing `test_director` drift cases) |
| `pytest tests/test_skill_content.py` (skill pins) | green after v2 skill edits |
| `python -m pytest -q` (final full suite, after ALL changes, ffmpeg + httpx + pillow present) | **4 failed, 2248 passed, 12 skipped** (894.10s / 0:14:54) — the 4 failures are exactly the pre-existing `include_unindexed` test-double-drift set from the baseline; **zero new failing test names**; +65 passed vs the 2183-passed baseline = the 65 DR02 tests |

---

## Rejected or deferred work

| Item | Reason |
|---|---|
| unit-level `compute_assurance` | intake **fully supports units** (member-map packets); per-unit assurance **deferred** — `compute_assurance` is shot-scoped, matching the contract's 12 tests |
| deterministic technical checks as expectations | **not duplicated** (ruling #3) — machine checks stay in `run_qc` and gate acceptance via the separate "no policy-blocking error" condition |
| new mock provider | **not added** — would duplicate covered capabilities (invariant: no parallel systems) |
| `execution_state` field | **not emitted** — execution "done" already lives in `build/stale`; assurance output carries `assurance_state` + `human_review_state`; the three axes exist without inventing a new execution field |
| `repair_plan.yaml` | **untouched** — assurance surfaces via the `qc.json` assurance block + `suggest_next`, not by rewriting `repair_plan.yaml` |

---

## Acceptance self-check — DR02 global acceptance (contract §10)

| # | Requirement | Status | Where |
|---|---|---|---|
| 1 | No second `GenerationRequest` / QC runner / router / ledger / runtime DB | ✅ | `providers/base.py:160-214` reused; v2 in the same `qc_agent.jsonl`; `run_qc`/`write_reports` unchanged; assurance file-only |
| 2 | No free-text inference of hard requirements | ✅ | expectations ONLY from `must_show`/`avoid`/`continuity.locks` (ruling #3); core diff is `PASS`/`FAIL`/`UNKNOWN`, no model call |
| 3 | v2 evidence binds packet / media / spec / expectations | ✅ | stored `media_sha256`/`spec_hash`/`expectation_digest` come from the packet; `binding_failures` on world-move (ruling #4) |
| 4 | Same-name race test passes | ✅ | `tests/test_dr02_race.py` GREEN via v2 (`binding:"stale"`, bound to A); live smoke intake `{stale: 1}` |
| 5 | Legacy readable, never fake-accepted | ✅ | legacy reader skips schema lines byte-identically; legacy verdicts surface as `unreviewed`/`legacy_reviewed`, never `accepted` |
| 6 | `UNKNOWN` preserved | ✅ | reviewer "uncertain" → `unknown` state, not coerced to pass/fail; precedence keeps `unknown` above `accepted` |
| 7 | done / assurance / human review separated | ✅ | execution done = `build/stale`; `assurance_state` = derived diff; `human_review_state` = `ShotStatus.review_state` read-only — three independent axes |
| 8 | Proposals never execute | ✅ | `repair_proposal` pure data, `do_not_execute_automatically:true`, `action=None` via `suggest_next` (ruling #8) |
| 9 | Reports deletable + rebuildable | ✅ | `qc.json`/`qc.md`/assurance block regenerated each `manju qc`; packets content-addressed idempotent (rebuilt by re-brief) |
| 10 | SQLite deletable (assurance file-only) | ✅ | test 11: `.manju` deleted, assurance still computes from `reports/` files only |
| 11 | Full suite — no new failures | ✅ 4 failed / 2248 passed / 12 skipped; the 4 = pre-existing baseline set, zero new failing names | `python -m pytest -q` after all changes |
