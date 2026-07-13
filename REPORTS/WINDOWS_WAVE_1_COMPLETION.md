# Windows Wave 1 — Completion (MANJU_WINDOWS_ONLY_LEAN_V3, W1: Windows 硬 CI + 文件系统/进程安全)

Plan **MANJU_WINDOWS_ONLY_LEAN_V3**, wave **W1** (§3.1–3.5). Branch
`claude/implement-ai-advice-delegation-tvt9ms`, base HEAD **dbee976**. Authored on a
Linux container (Python 3.11.15, ffmpeg 6.1.1); **no Windows host was available** —
Windows-only semantics are pinned by injected-constant/stub unit tests plus
behavioral tests that go red on the new `windows-ci.yml` gate (stated honestly here
and in the Baseline). All **22** new tests pass on the authoring host; the real-host
run is the gate's first execution.

## Baseline

Full suite at HEAD dbee976 with full tooling: **4115 passed, 1 skipped, 0 failed**
(484.95s, `pytest -n auto`). An earlier ffmpeg-less run showed 25F/16E — purely the
missing binary, documented as an environment note in
`REPORTS/WINDOWS_WAVE_1_BASELINE.md`. Four audit dossiers (atomic-write, path-rules,
locks/processes, CI/toolchain) established the HEAD status of every W1 capability
before any code changed; their statuses/owners are the Baseline's audit tables.

## Audit

Decision key: **Implement** (W1 built it) · **Kept** (already satisfied, pinned) ·
**Deferred** (with evidence, out of W1 budget) · **Rejected** (per a plan rule) ·
**N-t-d** (nothing to do / vacuous pass).

| Capability | Decision | Evidence |
|------------|----------|----------|
| §3.3 winerror-32/33 bounded replace-retry; no retry on access-denied/disk-full/invalid-path | **Implement** | `core/yamlio.py:72` `replace_with_retry` (winerror ∈ {32,33} only, 8 attempts, capped backoff); wired at `yamlio.py:103` + `media/ffmpeg.py:347`. Tests: `test_windows_fs.py` retry/access-denied/bounded/posix |
| §3.3 media temp fsynced before swap (`atomic_output` had no fsync at all) | **Implement** | `media/ffmpeg.py:356` `_fsync_file`, called `ffmpeg.py:346` before `replace_with_retry` |
| §3.3 old file survives on failure; §3.3 dir-fsync after replace | **Kept** | `yamlio.py:70-77`, `_fsync_dir` at `yamlio.py:31` (already implemented; unchanged) |
| §3.3 read-back verify step in the write helper | **N-t-d** | not added — no correctness evidence it is needed; media content is already hash-verified on its own path and content keys are untouched (see Compatibility) |
| §3.2 reserved device names / ADS colon / trailing dot-space / backslash-UNC-absolute-`..` lexical rejection | **Implement** | `core/idents.py:86` `windows_segment_problems`, `:112` `windows_relpath_problems`, `:79` `WINDOWS_RESERVED_NAMES` — the ONE lexical owner |
| §3.2 case-insensitive collision detection (display case preserved) | **Implement** | `core/idents.py:145` `windows_collision_key` (`casefold`); surfaced as a `manju check` warning at `core/check.py:442` |
| §3.2 no string-`startswith` directory boundary check | **Implement** | `media/align.py:35` `_inside_project` (resolve + `is_relative_to`) replaces the two `startswith` sites (`align.py:390`, `:477`) |
| §3.2 junction/symlink escape defense | **Kept** | `Project.resolve` (`core/container.py:267`) — unchanged |
| §3.2 no `\\?\` long-path prefix persisted | **N-t-d (vacuous)** | grep proves the prefix is never generated or persisted anywhere in `src/` |
| §3.4 Windows-correct PID liveness (never kill the probed process) | **Implement** | `runtime/buildlock.py:100` `_pid_alive_windows` (OpenProcess `PROCESS_QUERY_LIMITED_INFORMATION` + GetExitCodeProcess); dispatched at `_pid_alive` `buildlock.py:142` |
| §3.4 real dual-process lock test | **Kept** | `tests/test_c0911_gates.py::test_b4` — already Windows-compatible |
| §3.4 events ledger append lock works on Windows (not dark) | **Implement** | `core/events.py:24` `msvcrt` import + `events_lock` byte-0 branch — reverses DECISIONS #20 |
| §3.5 external-process **tree** kill on Windows | **Implement** | `providers/local_cmd.py:326` `_kill_process_group` Windows branch: `taskkill /PID <pid> /T /F` then direct kill |
| §3.5 ONE unified runner for all ~30 subprocess sites | **Deferred** | load-bearing paths already own argv/timeout/cancel; unification exceeds budget + is regression-risk (Skipped/Rejected) |
| §3.5 Job Objects for orphan reaping | **Rejected** | plan rule 只有真实 orphan 测试失败时才实现 — no failing Windows orphan test exists; `taskkill /T` is the documented order |
| §3.4 LockFileEx / Named-Mutex BuildLock rewrite | **Rejected** | plan rule 不要无证据重写现有锁 — only the liveness probe was broken |
| §3.1 hard Windows CI gate, no continue-on-error, full suite | **Implement** | `.github/workflows/windows-ci.yml` (new) |
| §3.1 FFmpeg version pin + checksum on Windows | **Implement (choco variant)** | `windows-ci.yml:44` `choco install ffmpeg --version=8.1.2` + version-assert + presence-assert; hardcoded-SHA-256 variant Deferred (network policy) |
| §3.1 Python 3.11 parity; ffmpeg-absent skip mechanism | **Kept** | `windows-ci.yml:41` `3.11`; per-file `shutil.which` guards unchanged |
| §14 a project directory with spaces + CJK exercises the stack | **Implement (test)** | `test_windows_fs.py::test_project_lifecycle_in_space_cjk_directory` |

## Red tests

18 red at HEAD, 4 deliberate green pins; **all 22 pass** after implementation.
Root cause = the missing production seam each test names in its docstring.

| Test | Old (HEAD) | New | Root cause |
|------|-----------|-----|------------|
| `test_reserved_device_name_segments_are_flagged` | ImportError — no predicate | flags CON/con.txt/NUL/COM7/LPT1/AUX-dir, passes CONSOLE/COM10/LPT0/CJK | reserved-name rejection existed nowhere |
| `test_ads_colon_and_trailing_dot_space_are_flagged` | RED | flags `:stream`/`:$DATA`/trailing `.`/trailing ` `; passes interior space & multi-dot | ADS/trailing checks existed nowhere |
| `test_backslash_unc_absolute_and_traversal_are_flagged` | RED | flags `\`, `//server`, `\\server`, `/abs`, `C:/`, `..`, `a/../b`, `""` | no lexical relpath validator |
| `test_windows_collision_key_folds_case_only` | RED | `A.wav`≡`a.WAV`, `a`≠`b`, spelling untouched | no `casefold` key existed |
| `test_shotpackage_rejects_windows_unsafe_path_hints` | admitted `CON.wav`/`x.wav:stream`/`take./`/`nul.txt` | rejects them; pre-existing abs/drive/`..` still rejected | `_is_unsafe_path` was abs/drive/NUL/`..` only |
| `test_check_warns_on_casefold_collision` | no warning | warns naming both spellings; not an error | `run_check` had no scan |
| `test_check_warns_on_reserved_name_on_disk` | no warning | warns on `nul.txt`; not an error | `run_check` had no scan |
| `test_atomic_write_retries_transient_sharing_violation` | bare `os.replace` → propagates | rides out 2× winerror-32, lands "new", temp cleaned | no retry seam / no `yamlio.time` |
| `test_atomic_write_never_retries_access_denied` | no seam to pin | 1 attempt, old file preserved, temp cleaned | winerror-5 must never retry |
| `test_atomic_write_retry_is_bounded` | no seam | fails after `1 < n ≤ 16`, old file preserved | held-forever must not loop |
| `test_posix_permission_error_is_not_retried` | no seam | 1 attempt (winerror `None`) | retry is a Windows-only ride-out |
| `test_align_apply_survives_sibling_prefix_dir` | `<root>_evil/` counted inside → `project.relpath` ValueError | foreign source import-copied to `media/imports/`, batch record `media/imports/src.wav` | `str().startswith()` boundary check |
| `test_project_lifecycle_in_space_cjk_directory` | **green pin** | scaffold/check/atomic-write/BuildLock under `含 空格/雨夜 便利店.manju` | pins §14 space+CJK, no regression |
| `test_pid_probe_leaves_live_child_alive` | **green pin (POSIX)** / red on Windows leg | live child stays alive after probe | `os.kill(pid,0)` = TerminateProcess on Windows |
| `test_pid_probe_windows_dispatch_never_calls_os_kill` | RED | Windows branch never touches `os.kill` | no `_IS_WINDOWS` dispatch |
| `test_pid_alive_windows_probe_semantics` | RED | STILL_ACTIVE→alive, exit→dead, err5→alive, err87→dead; handle closed | `_pid_alive_windows`/`_win_kernel32` absent |
| `test_pid_probe_windows_errors_count_as_alive` | RED | kernel32 unavailable → alive (err-safe) | probe path absent |
| `test_local_cmd_kill_tree_uses_taskkill_on_windows` | RED | emits `["taskkill","/PID","43210","/T","/F"]` | no Windows tree-kill branch |
| `test_events_append_uses_msvcrt_lock_when_fcntl_missing` | required → `no_reliable_lock` (dropped) | takes/releases msvcrt byte-0 lock, record lands | DECISIONS #20 refuse-on-Windows |
| `test_events_append_contention_still_never_writes_unlocked` | required → `no_reliable_lock` | contention → `lock_timeout`; best-effort drops; never unlocked | no msvcrt poll loop |
| `test_events_append_refuses_when_no_lock_primitive_exists` | **green pin** | neither primitive → refuse/drop unchanged | exotic-platform contract preserved |
| `test_local_cmd_posix_group_kill_unchanged` | **green pin** | dead pid → falls through to direct kill | POSIX `killpg` byte-identical |

## Implementation

### Production files (11 — one over the ≤10 budget; deviation recorded below)

1. **`src/manju/core/idents.py`** — the ONE lexical owner (§3.2). New
   `windows_segment_problems` (`:86`), `windows_relpath_problems` (`:112`),
   `windows_collision_key` (`:145`), `WINDOWS_RESERVED_NAMES` (`:79`). Segment rules:
   reserved device name on the pre-dot stem (`CON`, `con.txt`, `nul.tar.gz` all
   refuse; `CONSOLE`/`COM10`/`LPT0` do not), `:` (ADS/drive), trailing dot/space,
   NUL byte. Relpath rules add backslash / UNC (`//`, `\\`) / absolute / drive-letter
   / `..`. Messages preserve the original spelling — only the *comparison*
   (`casefold`) folds. Lexical only; containment stays owned by `Project.resolve`.
2. **`src/manju/core/yamlio.py`** — `replace_with_retry` (`:72`): `os.replace`
   with a bounded (`_REPLACE_ATTEMPTS = 8`), winerror-32/33-only retry and capped
   0.02→0.15s backoff; any other `OSError` and the final attempt re-raise, old file
   intact. On POSIX `winerror` is `None` → single attempt, byte-identical to a bare
   `os.replace`. Wired into `atomic_write_text` (`:103`); `import time` added.
3. **`src/manju/media/ffmpeg.py`** — `atomic_output` now fsyncs the finished temp
   via new `_fsync_file` (`:356`, silent-degrade) and swaps via `replace_with_retry`
   (`:346-347`), so the media and text write halves share Windows semantics and the
   ffmpeg producer's output is finally fsynced before the swap.
4. **`src/manju/runtime/buildlock.py`** — CRITICAL FIX. `_pid_alive` dispatches to
   `_pid_alive_windows` on `_IS_WINDOWS` (`:142`). Rationale: `os.kill(pid, 0)` on
   Windows routes through **TerminateProcess** — the old probe would kill the live
   lock holder it was "checking". New `_pid_alive_windows` (`:100`): OpenProcess
   (`PROCESS_QUERY_LIMITED_INFORMATION`) + GetExitCodeProcess — STILL_ACTIVE(259)→
   alive, real exit code→dead, access-denied→alive (exists, not ours),
   invalid-parameter(87)→dead, every undecidable outcome→alive (err-safe philosophy
   unchanged). `_win_kernel32` (`:91`) is a function so tests stub it and non-Windows
   never loads the DLL. **POSIX path byte-identical.** The lock *file* mechanism was
   NOT rewritten (§3.4 no-evidence-no-rewrite).
5. **`src/manju/core/events.py`** — `events_lock` gains an `msvcrt.locking` byte-0
   branch (import `:24`) when `fcntl` is absent: same non-blocking poll loop, same
   deadline, same never-write-unlocked policy. This **deliberately reverses DECISIONS
   #20**'s "Windows appends refuse/drop" — without it the evidence ledger is dark on
   the primary platform and the Windows gate can never go green. `fcntl` present →
   byte-identical to the pre-W1 flock path; **neither** primitive → the old
   refuse/drop contract, unchanged.
6. **`src/manju/providers/local_cmd.py`** — `_kill_process_group` (`:326`) grows a
   `_IS_WINDOWS` branch: `taskkill /PID <pid> /T /F` (tree + force) then a belt-and-
   suspenders `proc.kill()`. The old direct-child-only degrade left grandchildren
   (the F1 GPU-worker leak) alive. **POSIX `killpg` path byte-identical.**
7. **`src/manju/media/align.py`** — the last string-prefix boundary check. `_inside_project`
   (`:35`, resolve + `is_relative_to`) replaces `str(media).startswith(str(root))` at
   both sites (`:390`, `:477`). The old code admitted sibling `<root>_evil/` and then
   crashed `project.relpath` while writing the batch record.
8. **`src/manju/core/check.py`** — Windows portability scan in `run_check` (`:420-449`):
   leaf-name lexical hazards + case-fold sibling collisions as **WARNINGS only**
   (old projects never gain errors); skips `.git/`, `.manju/`, `renders/`.
9. **`src/manju/build/shotpackage.py`** — `_is_unsafe_path` (`:184`) delegates to
   `windows_relpath_problems` (`:194`): now rejects `CON.wav`, ADS colons, trailing
   dot/space in path_hints — strictly wider than before, no regression.
10. **`src/manju/gui/server.py`** — shared `_fs_name_problems` classmethod (`:1677`)
    wired into 2 project-name gates (`:1699`, `:1804`) and 3 upload-name gates
    (`:2178`, `:2780`, `:5344`).
11. **`src/manju/cli.py`** — pack extraction (`unpack`) member gate (`:5029-5045`):
    `windows_relpath_problems` on every zip member (trailing `/` stripped for dir
    members) before extraction.

Plus **`.github/workflows/windows-ci.yml`** (new hard gate) and the two test files.

### Tests

- `tests/test_windows_fs.py` (13) — §3.2 lexical rules, §3.3 retry/no-retry/bounded,
  the align sibling-prefix crash, the §14 space+CJK lifecycle.
- `tests/test_windows_procs.py` (9) — §3.4 liveness probe (live-child tripwire,
  Windows dispatch, four probe outcomes, err-safe), §3.4 events msvcrt lock (lands /
  contention-drops / exotic-refuses), §3.5 taskkill tree-kill + POSIX byte-identity.

### CLI / contracts

- **DECISIONS #20 reversed**, deliberately and minimally, for the events-lock
  Windows branch (documented in-code at `events.py`; record in DECISIONS).
- No CLI surface change — `manju check` gains warnings only (no new error codes, no
  frozen-typer-surface change); `unpack` gains a stricter member refusal reusing the
  existing `_fail` path and message style. GUI gates return the existing 400 shape.

### Design deviations (honest)

- **11 production files vs a ≤10 budget.** `core/events.py` was added mid-wave when
  the audit-level fact surfaced that *all* event appends drop/raise on Windows — a
  hard blocker for a green Windows gate. Recorded as a **deliberate, evidenced
  overrun**, not split into a separate wave.
- **FFmpeg pin via Chocolatey, not a hardcoded SHA-256.** A direct gyan.dev/BtbN URL
  + hardcoded SHA-256 was **impossible from the authoring environment** (gyan.dev
  packages 404 through the proxy; github.com release downloads 403 by network
  policy). Hashing an artifact never held would be an invented fact. `choco install
  ffmpeg --version=8.1.2` (Chocolatey verifies the package's embedded SHA-256 at
  install) + a version-assert + a presence-assert is the shipped pin; the hardcoded-
  SHA-256 URL is documented in the workflow as the stricter upgrade path at the next
  bump.

## Compatibility

- **Old projects gain at most WARNINGS** from `manju check` (never errors) — every
  legacy project keeps building.
- **Content keys untouched** — no hashing change; no read-back verify was added to
  the write helper.
- **POSIX behavior byte-identical for every touched module**: events flock loop,
  `killpg`, the `os.kill(pid, 0)` probe, `os.replace` single-attempt when `winerror`
  is `None`. Neither-primitive platforms keep the old events refuse contract.
- **board / pack / fixity untouched.** Intake gates only *widen* rejection
  (shotpackage path_hints, GUI names, pack members) — inputs already accepted stay
  accepted; only genuinely Windows-unopenable names are refused.

## Verification

Targeted suites on the authoring host (Linux, full tooling):

- `tests/test_windows_fs.py` + `tests/test_windows_procs.py` — **22 passed**.
- `tests/test_dr03a_shotpackage.py`, `test_locks.py`, `test_buildlock.py`,
  `test_buildlock_wiring.py`, `test_write_locks.py`, `test_local_cmd.py`,
  `test_media_durability.py`, `test_round_w.py`, `test_check.py`,
  `test_c0911_gates.py` — **137 passed, 9 skipped** (skips are POSIX-only /
  live-probe guarded, pre-existing).

Full suite (`pytest -n auto`, authoring host): **4137 passed, 1 skipped, 0 failed
(491.13s)** — exactly the 4115-passed baseline plus the 22 new tests; the failing
set is empty before AND after (zero regressions).

Windows CI (`windows-ci.yml`, first execution on `windows-latest`): **triggered by
this branch's push — result and any Windows-only red evidence recorded in the
DECISIONS follow-up for this wave.**

Method: `python -m pytest` only; red-first pins for every new behavior; no existing
test weakened or deleted.

## Skipped / Rejected

| Item | Reason |
|------|--------|
| Single unified runner for all ~30 subprocess sites | **DEFERRED_WITH_EVIDENCE** — load-bearing paths (`run_ffmpeg`, `local_cmd`, `gitops._run`) already own argv/timeout/cancel; full unification exceeds the wave file budget and is a regression-risk refactor; the tree-kill owner (`local_cmd`) is now Windows-correct |
| Job Objects for orphan reaping | **REJECTED** per plan rule (只有真实 orphan 测试失败时才实现) — `taskkill /T` is the documented order; no failing Windows orphan test exists yet |
| LockFileEx / Named-Mutex BuildLock rewrite | **REJECTED** per plan rule (不要无证据重写现有锁) — the real dual-process test (`test_c0911_gates.py::test_b4`) exists and is Windows-compatible; only the liveness probe was broken |
| `refs.copy_collision_safe` casefold-aware suffixing | **DEFERRED** — the check-side collision warning covers detection; intake-side auto-suffixing is an enhancement |
| `\\?\` long-path prefix handling | **NOTHING TO DO (vacuous pass)** — grep proves the prefix is never generated or persisted anywhere |
| Hardcoded FFmpeg SHA-256 in the workflow | **DEFERRED (network policy)** — choco checksum verification + version assert in place; the direct-URL + hardcoded-hash path was unreachable from the authoring environment |

## Final

**Commit SHA:** `b1349ec` (code + tests + gate; this report and DECISIONS #36 land
in the docs follow-up commit).

**Remaining risks:**

1. **`windows-ci.yml` has never executed.** Its first run may surface real Windows
   failures in the ffmpeg-gated suites — *that is the point of the gate*; those
   failures become the next wave's red evidence.
2. **FFmpeg version skew.** choco ffmpeg **8.1.2** on Windows vs ubuntu apt **6.1.1**
   may expose version-sensitive media assertions on the Windows leg.
3. **`casefold()` approximates NTFS upcase folding** (documented in `idents.py`) —
   close enough for collision *detection*, not a byte-exact model of NTFS.
4. **The msvcrt byte-0 lock is unproven on a real Windows host** until the gate runs;
   on the authoring host it is exercised only through the `_StubMsvcrt` double.
