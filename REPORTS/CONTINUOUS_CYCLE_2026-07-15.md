# Continuous bug-fix → UX → bug-fix cycle ledger

| | |
|---|---|
| **Goal start** | 2026-07-15T05:27:48-07:00 |
| **Start HEAD** | `81d6206` |
| **End HEAD** | `4c55fc1` |
| **Branch** | `claude/fable-opus-task-division-wv97i6` |
| **Scratch** | `C:\Users\ADMINI~1\AppData\Local\Temp\grok-goal-bd93d317b808\implementer` |

## Residual risks after R2 (baseline)

- exports/`--lang` pollute base → **C1 refused**
- empty locale plan → 母语 film → **C1 hard-fail**
- funnel/exportstatus base-only final → **C2 honesty message**
- voice spend not in ledger rebuild → **C2 fixed**
- locale TTS cancel → **C2 should_cancel**
- MCP qc/build lang → **C3/C5**

---

## Cycle 1

### 1A Scan
- Engine: empty locale plan hard-fail gap; exports pollution; voice spend; cancel; funnel base final; MCP qc base
- GUI: review/storyboard redo without assume_yes dead-end; edit cancel→success; TTS false 不可用; skill half-open; series sticky status

### 1B Fixes — SHA `0ee1e7e`
- `graph.py`: hard-fail 有译文无配音; early refuse lang+proxy|exports; skip base NLE exports on locale final
- `pages.py` / `storyboard.py`: assume_yes after confirm
- `edit.py`: canceled/interrupted not success
- Tests: `tests/test_cycle1_bugfix_20260715.py`

### 1C UX — same SHA
- Storyboard approve toast; kbd hint **n**; job badge **待确认花费**; TTS timeout honest copy

### 1D Re-verify
- pytest cycle1 + bugfix* + merge_blockers + job_cancel → green (69+)

---

## Cycle 2

### 2A Scan
- Voice spend missing from rebuild/sidecars; locale cancel gap; funnel silent on locale-only; create skill / series sticky UX

### 2B Fixes — SHA `60d72e6`
- `RuntimeState.rebuild` + spend `_from_sidecars` include voice takes
- `synthesize_locale_voices(should_cancel=...)`
- Funnel locale-only honesty

### 2C UX — same SHA
- create skill `.catch`; series timeout clears sticky “新建中/同步中”

### 2D Re-verify
- `tests/test_cycle2_bugfix_20260715.py` + cycle1 → 41+ passed

---

## Cycle 3

### 3A Scan
- MCP qc base-only; lab scaffold no reload; locale auto ignores stale; lab generate strict 202; edit modal closes on fail

### 3B Fixes — SHA `4c01f00`
- MCP `qc` lang/final_path; `plan_locale_voice` gen=auto stale; lab scaffold reloadSoon; generate job envelope

### 3C UX — same SHA
- Edit rebuild keeps modal on queue failure

### 3D Re-verify
- cycle3 + mcp subset green (73–81)

---

## Cycle 4 (extra continuous)

### 4A/B/C — SHA `9cfbeaf`
- Director run/confirm `.catch`; job row **取消中…**; exports job envelope
- Tests: `tests/test_cycle4_ux_20260715.py`

---

## Cycle 5 (extra continuous)

### 5A/B/C — SHA after c5 commit
- MCP `build` `lang` argument
- ingest/series accept 200+job
- Tests: `tests/test_cycle5_mcp_lang_20260715.py`

---

## Final summary

| Item | Value |
|------|--------|
| Full A→B→C→D cycles | **≥3** (C1–C3) + C4–C5 continuous |
| Fix SHAs (post-start) | `0ee1e7e` `60d72e6` `4c01f00` `9cfbeaf` `4c55fc1` |
| Related pytest (final, single capture) | **115 passed** in one transcript (see below) |
| Wall-clock | Start 05:27:48-07:00; multi-cycle continuous work; **not 10h wall-clock** if harness caps session. Software bar: ≥3 A→B→C→D + green suite + ledger. |

### Test command (canonical — one capture)
```
py -3 -m pytest tests/test_http_no_proxy_fixture.py tests/test_gui_merge_blockers.py tests/test_job_cancel.py tests/test_mcp.py tests/test_cycle1_bugfix_20260715.py tests/test_cycle2_bugfix_20260715.py tests/test_cycle3_bugfix_20260715.py tests/test_cycle4_ux_20260715.py tests/test_cycle5_mcp_lang_20260715.py tests/test_project_bugfix_20260715.py tests/test_project_bugfix_r2_20260715.py -q
```
**Result (captured `{SCRATCH}/cycle_tests.log`):** `115 passed in 24.04s` with system `HTTP_PROXY=http://127.0.0.1:10090` still set.

### Skeptic gap fix (proxy 502)
System proxy was hijacking `urllib` to localhost → false 502. Fixed via:
- `tests/conftest.py` autouse `_isolate_http_proxy` (delenv + `ProxyHandler({})`)
- `test_gui_merge_blockers._req` / `test_job_cancel._request` use no-proxy opener
- pin `tests/test_http_no_proxy_fixture.py`

Evidence: `{SCRATCH}/cycle_tests.log`, `{SCRATCH}/cycle_ledger_audit.txt`
