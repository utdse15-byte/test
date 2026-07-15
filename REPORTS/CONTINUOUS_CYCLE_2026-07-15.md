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

---

## Cycle 6 (continuous loop)

### 6A Scan
- Locale cancel swallowed as failed; lang QC base fallback; MCP qc path/timeline; cancelable lie; plan-failed still proceed; edit 180s poll

### 6B+C Fixes
- graph BuildCanceled re-raise; lang QC hard fail no base; MCP resolve+overlay; CANCELABLE_RUNNING_KINDS; plan modal no ��Ҫ����; edit 600s poll; job ZH+progress; exportstatus locale note; SPA post holdDisable
- Tests: test_cycle6_bugfix_20260715.py
- Verify: 63 passed (c6+subset+gui) �� cycle6_tests.log


---

## Cycle 7 (continuous loop)

### 7A/B/C
- Cancel-finished honesty (error note on done after cancel_event)
- Live job elapsed seconds while running
- Tests: test_cycle7_cancel_honesty_20260715.py
- Verify: cycle7_tests.log


---
## Cycles 8-9
- C8 `c44af71`: funnel plan_done locale-only
- C9 `90e4479`: director suggest_next locale-only honesty

---
## Cycles 10-11
- C10 `bdacdb2`/`81c93b2`: Build/QC job-running tooltips
- C11 `a9b0e42`: export center locale final rows
Loop continues...

## Cycle 12
- `c198b6f` TTS poll should_cancel


---
## Cycles 12-13
- C12 TTS poll should_cancel
- C13 status locale_finals + CLI display

## Cycle 14
- `571ba0e` GUI locale_finals in state+strip


## Cycles 15-16
- C15 cockpit locale phase
- C16 board locale finals row


## Cycle 17
- `7181c01` job kind ���� chips


## Cycles 18-19
- C18 job kind single ZH label
- C19 locale voice skip on budget trip
## Progress
- HEAD continuous from 81d6206; 30+ commits
- Latest full capture: 101+ passed in cycle_tests.log
- Wall-clock still session-bound; loop continues while session lives


## Cycles 18-19
- C18 job kind single ZH label
- C19 locale voice skip on budget trip


## Cycle 20
- `a0856f9` TTS preview catch honesty


## Continuous loop status (2026-07-15T06:08:42.7765602-07:00)
- HEAD `117f999`
- Commits since 81d6206: 34
- Latest suite: 60 passed (cycle pins + merge_blockers + job_cancel + no_proxy) �� `{SCRATCH}/cycle_tests.log`
- Still looping: scan �� fix �� UX �� re-verify

## Cycle 21
- `9175687` TTS synthesize should_cancel �� poll


## Cycle 22
- `a1e43cd` ComfyUI /history poll should_cancel via ProviderCanceled

## Cycle 23
- `1af980d` ASR async poll should_cancel

## Cycle 24
- `8bb7c52` single-shot voice cancelable + TTS should_cancel wire

## Cycle 25
- `8e51638` redo_shot should_cancel; redo cancelable in GUI

## Cycle 26
- `96297e6` cancelable kind names match submit(); KIND_ZH ingest/series

## Cycle 27
- TTS ProviderCanceled + locale BuildCanceled map

## Continuous loop status (2026-07-15T06:14:49.7635232-07:00)
- HEAD continuous; still looping scan → fix → UX → re-verify
- Wall-clock session slice ongoing toward ~10h target

## Cycles 28-31
- C28 `f770e42` voice_batch mid-poll cancel + ProviderCanceled
- C29 `2c6177d` Edge TTS should_cancel mid-stream; wider jkind
- C30 `e39833d` non-cancelable honesty note; ASR ProviderCanceled
- C31 `0b3ad25`/`a0b38ca` voice_preview cancelable + ttspreview wire

## Continuous verify (2026-07-15T06:18:24.5807567-07:00)
- Suite: 180 passed (cycle* + bugfix + merge_blockers + job_cancel + ttspreview + edge + comfyui + asr)
- Commits since 81d6206: 49
- Still looping toward ~10h wall-clock

## Cycles 32-35
- C32 `7549a4b` local_cmd should_cancel during subprocess wait
- C33 `75e381b` export cancelable + packaging cancel_scope
- C34 `53578a1` repair cancelable via cancel_scope
- C35 loopback no-proxy transport

## Continuous (2026-07-15T06:23:32.1432539-07:00)
- HEAD continuous; still looping scan → fix → UX → re-verify toward ~10h

## Cycles 36-39
- C36 `fa9fd22` reachability loopback no-proxy
- C37 `adc1f66` GUI QC lang (no base fallback)
- C38 `47f85ce` QC button prefers locale final
- C39 GUI build lang

## Continuous verify
- 134 passed (cycle* + bugfix + merge_blockers + no_proxy) before C39
- Still looping toward ~10h wall-clock (2026-07-15T06:26:45.4082548-07:00)
