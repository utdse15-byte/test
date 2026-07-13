# Windows Wave 1 — Baseline (MANJU_WINDOWS_ONLY_LEAN_V3, W1: Windows 硬 CI + 文件系统/进程安全)

Plan: **MANJU_WINDOWS_ONLY_LEAN_V3** — "Windows 硬 CI + 文件系统/进程安全".
Slice: **W1 only** — §3.1 (hard Windows CI + toolchain pin), §3.2 (Windows path
rules), §3.3 (atomic-write sharing-violation retry), §3.4 (BuildLock/events lock
Windows-correctness), §3.5 (external-process tree-kill). W2 (install story /
doctor) and W3 (NLE exchange, fonts, board) are out of scope.
Date: 2026-07-13 · Repo: `/home/user/test` (Manju) · Branch
`claude/implement-ai-advice-delegation-tvt9ms` · Base HEAD **dbee976**
("docs: DECISIONS #35 + report index — OPT wave 2 closes the optimization program").

## Authoring environment — and the honest limit of what it can prove

This wave was authored on a **Linux container** (Ubuntu Noble). **No Windows host
was available at any point.** Windows-only semantics are therefore pinned two ways,
both stated plainly and neither pretending to be a real-host run:

1. **Branch-level unit tests** drive the Windows code paths on Linux by injecting
   the platform constant (`monkeypatch.setattr(buildlock, "_IS_WINDOWS", True)`,
   the `local_cmd._IS_WINDOWS` flag) and stubbing the OS surface the branch calls
   (`_StubKernel32` for OpenProcess/GetExitCodeProcess, `_StubMsvcrt` for
   `msvcrt.locking`) — so the dispatch, the argv, the err-safe outcomes and the
   never-write-unlocked policy are all exercised without a Windows kernel.
2. **Behavioral tests** that go **red on the new `windows-ci.yml` hard gate** —
   the gate is what finally runs the real `os.kill`/`OpenProcess`/`msvcrt` paths on
   `windows-latest`. Until that gate executes, the real-host behavior is asserted,
   not observed. This is called out again in the COMPLETION's Final/remaining-risks.

| tool | version (authoring host) |
|------|--------------------------|
| Python | 3.11.15 |
| ffmpeg / ffprobe | 6.1.1-3ubuntu5 |
| tesseract | installed, with `chi_sim` + WenQuanYi fonts |
| OS | Linux (Ubuntu Noble) |

## Baseline test state at HEAD dbee976

With the **full toolchain present** (ffmpeg + tesseract + fonts), the whole suite
is green at HEAD:

```
python -m pytest -n auto
4115 passed, 1 skipped, 0 failed   (484.95s)
```

**Environment note (not a code fault).** An earlier baseline run on a host where
ffmpeg was *not yet installed* showed **25 failures / 16 errors**. Every one traced
to the missing binary, not to repository code: Manju's ~63 ffmpeg-gated test files
use per-file `shutil.which("ffmpeg")` skip guards (audit 03 §2), but a handful of
call sites raise `MediaError` rather than skip when the binary vanishes mid-test.
Installing ffmpeg 6.1.1 restored the clean 4115/1/0. This is recorded here precisely
because it is the failure mode the new Windows gate is built to *prevent* silently:
a Windows job with no ffmpeg would instead go green by skipping all 63 files (audit
03 §2 NOTES) — hence the gate's version-and-presence asserts (see §3.1 scope below).

## Audit summary — the four W1 dossiers

Four audit dossiers were produced before any code changed. Each capability carries
its HEAD status and the owner an implementer must extend (never a parallel module).
`ALREADY_IMPLEMENTED` = the desired invariant already holds; `PARTIAL` = present but
incomplete or bypassed; `GAP` = does not exist anywhere at HEAD.

### Dossier 00 — Atomic write semantics (§3.3)

| Capability | Status | Owner |
|------------|--------|-------|
| Unified write helper (same-dir temp, flush+fsync, replace) | PARTIAL — helper exists, widely adopted, but no read-back verify and several bypassers/parallel writers | `core/yamlio.py:61` `atomic_write_text` |
| Bounded retry on Windows sharing/lock violation (winerror 32/33), none on access-denied/disk-full/invalid-path | **GAP** — no winerror classification exists anywhere; every writer does a bare single `os.replace` | `core/yamlio.py:61` + `media/ffmpeg.py:313` |
| On failure the old file survives (temp cleaned, target untouched) | ALREADY_IMPLEMENTED | `core/yamlio.py:70-77`, `media/ffmpeg.py:345-350` |
| fsync of the directory after replace | ALREADY_IMPLEMENTED for text; **absent in `media/ffmpeg.atomic_output`** (no file *or* dir fsync) | `core/yamlio.py:31` `_fsync_dir` |
| Test coverage for atomicity/retry | PARTIAL — media crash-sim covered; zero coverage of sharing-violation retry or `atomic_write_text`'s own failure path | `tests/test_round_w.py`, `tests/test_media_durability.py` |

### Dossier 01 — Windows path rules (§3.2)

| Capability | Status | Owner |
|------------|--------|-------|
| POSIX-relative storage + a read-side validator that rejects backslash/absolute | PARTIAL — writers normalize via `Project.relpath`; no rejecting validator | `core/container.py:274`, `core/check.py:132` |
| Reject `..`/absolute/UNC/ADS-colon/reserved-names/trailing-dot-space | PARTIAL — traversal+absolute solid; **UNC-explicit, ADS colon, reserved device names, trailing dot/space exist NOWHERE** | `core/idents.py:26`, `build/shotpackage.py:184` |
| Case-insensitive collision detection | **GAP** — zero `casefold` in `src/`; `manju check` has no scan | `core/check.py:132` |
| Directory boundary NOT via string `startswith` | PARTIAL — one real offender: `media/align.py:377` & `:464` (`str(media).startswith(str(root))`) | `media/align.py:377` |
| Junction/symlink escape defense (resolve stays under root) | ALREADY_IMPLEMENTED (`resolve()`+`is_relative_to`) | `core/container.py:267` |
| No `\\?\` long-path prefix persisted | **GAP (vacuous)** — grep proves the prefix is never generated, stripped, or tested | none |
| Test coverage for CON/ADS/reserved/case/symlink | PARTIAL — symlink/traversal well covered; reserved/ADS/case-collision/trailing all zero | — |

### Dossier 02 — BuildLock + external process handling (§3.4–3.5)

| Capability | Status | Owner |
|------------|--------|-------|
| Lock primitive + Windows-correct PID liveness | PARTIAL — the lock *file* mechanism is Windows-portable, but `_pid_alive` uses `os.kill(pid, 0)`, which on Windows is **TerminateProcess** — probing a live holder KILLS it | `runtime/buildlock.py:81` `_pid_alive` |
| Real dual-process lock test (two OS processes) | ALREADY_IMPLEMENTED | `tests/test_c0911_gates.py:495` `test_b4` |
| ONE shared external-process runner (argv/timeout/size-caps/cancel/tree-kill/redaction) | **GAP** — no single runner; two partial wrappers + ~30 direct sites; tree-kill is POSIX-only | `media/ffmpeg.py:231`, `providers/local_cmd.py:137` |
| `shell=True` / string-command sites | ALREADY_IMPLEMENTED (zero — every site passes an argv list) | — |
| Orphan-child (grandchild) reaping test | PARTIAL — one test exists but is `skipif not hasattr(os,'killpg')` + `#!/bin/sh` scripts → never runs on Windows | `tests/test_local_cmd.py:148` |

### Dossier 03 — CI + toolchain pinning (§3.1)

| Capability | Status | Owner |
|------------|--------|-------|
| A hard Windows job covering locks/process/fs, board security, media, corpus | PARTIAL — only a 12-file **informational** subset runs (`xplat.yml`, `continue-on-error: true`); ubuntu `ci.yml` is the sole gate | `.github/workflows/xplat.yml`, `ci.yml` |
| Suite degrades cleanly when ffmpeg is absent | ALREADY_IMPLEMENTED — decentralized per-file `shutil.which` skipif (~63 files); **but a Windows job that forgets ffmpeg passes while covering nothing media** | `media/ffmpeg.py:102`, `tests/conftest.py` |
| Python version declared vs CI | ALREADY_IMPLEMENTED — `requires-python >=3.11`, all workflows pin `3.11` | `pyproject.toml:9`, `ci.yml:17` |
| FFmpeg version pinning / SHA-256 verification | **GAP** — ubuntu installs "whatever apt ships"; no version spec, no checksum, no Windows install step at all | `.github/workflows/*` |
| Tests using paths with spaces / CJK | PARTIAL — CJK is first-class and pervasive (`tmp_project` scaffolds under `雨夜便利店.manju`); **spaces appear only in one `test_gitops.py` rename** | `tests/conftest.py:24` |
| `conftest.py` fixtures to reuse (autouse home-dir isolation) | ALREADY_IMPLEMENTED | `tests/conftest.py:28` |

## Red-test inventory — 22 new tests, 18 red at HEAD

Two new files: `tests/test_windows_fs.py` (13 tests) and `tests/test_windows_procs.py`
(9 tests). Red-first discipline: **18 go red at HEAD**, **4 are deliberate green
pins** (behavior that already holds and must not regress — they pin POSIX
byte-identity and the platform-neutral space+CJK lifecycle). Every docstring names
its own gap.

### `tests/test_windows_fs.py`

| Test | HEAD | Failing reason at HEAD |
|------|------|------------------------|
| `test_reserved_device_name_segments_are_flagged` | RED | `manju.core.idents.windows_relpath_problems` does not exist; reserved-name rejection (CON/PRN/AUX/NUL/COM1-9/LPT1-9) exists nowhere (audit 01 §2 GAP) |
| `test_ads_colon_and_trailing_dot_space_are_flagged` | RED | same predicate absent; ADS-colon and trailing dot/space checks exist nowhere |
| `test_backslash_unc_absolute_and_traversal_are_flagged` | RED | `windows_relpath_problems` absent — no single lexical relpath validator |
| `test_windows_collision_key_folds_case_only` | RED | `windows_collision_key` absent; zero `casefold` in `src/` (audit 01 §3 GAP) |
| `test_shotpackage_rejects_windows_unsafe_path_hints` | RED | `_is_unsafe_path` rejected only absolute/drive/NUL/`..`; `CON.wav`, `x.wav:stream`, `take./f.wav`, `nul.txt` passed straight through (`build/shotpackage.py:184` at HEAD) |
| `test_check_warns_on_casefold_collision` | RED | `run_check` had no case-fold collision scan — no warning emitted for `Ambience.wav`+`ambience.WAV` |
| `test_check_warns_on_reserved_name_on_disk` | RED | `run_check` had no reserved-name scan — `nul.txt` on disk produced no warning |
| `test_atomic_write_retries_transient_sharing_violation` | RED | `atomic_write_text` did a bare `os.replace`; a winerror-32 `PermissionError` propagated and the write failed (no retry seam; `yamlio` imported no `time`) |
| `test_atomic_write_never_retries_access_denied` | RED | no winerror classification and no `yamlio.time` seam — the test's `monkeypatch.setattr(yamlio.time, "sleep", …)` has no target at HEAD |
| `test_atomic_write_retry_is_bounded` | RED | no bounded retry loop; `yamlio.time` seam absent |
| `test_posix_permission_error_is_not_retried` | RED | same — the retry/`time` seam being pinned does not exist at HEAD |
| `test_align_apply_survives_sibling_prefix_dir` | RED | `align.py:377/:464` used `str(media).startswith(str(root))`; sibling `<root>_evil/` counted as "inside", the foreign file was never import-copied, and `project.relpath(media)` raised `ValueError` writing the batch record |
| `test_project_lifecycle_in_space_cjk_directory` | **GREEN pin** | scaffold/check/atomic-write/BuildLock already work under `含 空格/雨夜 便利店.manju` on Linux — pins §14 space+CJK viability and that the wave does not regress it |

### `tests/test_windows_procs.py`

| Test | HEAD | Failing reason at HEAD |
|------|------|------------------------|
| `test_pid_probe_leaves_live_child_alive` | **GREEN pin** | `os.kill(pid, 0)` is a real probe on POSIX, so it passes on the Linux host — this is the regression tripwire that goes **red on the Windows CI leg** at HEAD (where the same call TerminateProcess-es the child) |
| `test_pid_probe_windows_dispatch_never_calls_os_kill` | RED | `buildlock._IS_WINDOWS` absent (the `monkeypatch(..., raising=True)` has no target); `_pid_alive` had no Windows dispatch |
| `test_pid_alive_windows_probe_semantics` | RED | `buildlock._pid_alive_windows` / `_win_kernel32` did not exist |
| `test_pid_probe_windows_errors_count_as_alive` | RED | `_pid_alive_windows` / `_win_kernel32` absent |
| `test_local_cmd_kill_tree_uses_taskkill_on_windows` | RED | `local_cmd._IS_WINDOWS` absent; `_kill_process_group` had no Windows/taskkill branch (POSIX `killpg` only, degrading to a direct child kill) |
| `test_events_append_uses_msvcrt_lock_when_fcntl_missing` | RED (2nd batch) | with `fcntl=None` and no `msvcrt` branch, `events_lock` raised `EvidenceWriteError("no_reliable_lock")` for a required append — the record never landed (DECISIONS #20 refuse-on-Windows) |
| `test_events_append_contention_still_never_writes_unlocked` | RED (2nd batch) | no `msvcrt` branch: `fcntl=None` + `required` raised `no_reliable_lock`, not the `lock_timeout` the test pins |
| `test_events_append_refuses_when_no_lock_primitive_exists` | **GREEN pin** | with **neither** primitive, the pre-W1 refuse/drop contract already held — pinned so the msvcrt addition does not weaken the exotic-platform path |
| `test_local_cmd_posix_group_kill_unchanged` | **GREEN pin** | POSIX `killpg` path is byte-identical; a dead fake pid falls through to the direct `proc.kill()` exactly as at HEAD |

**Red count:** 12 (fs) + 4 procs (buildlock/local_cmd dispatch) + 2 procs
(events-lock, added in the second batch once the audit-level fact surfaced that
*all* event appends refuse/drop on Windows) = **18 red**. **4 green pins.** Total 22.

## Wave scope decisions

- **Extend the one owner, never fork a validator.** The Windows-lexical predicate
  lands in `core/idents.py` (the single safe-segment owner) and is *consulted* by
  every intake gate + `manju check` — no parallel path-rules module (audit 01
  OWNERS).
- **No-evidence-no-rewrite on the lock file.** The BuildLock *file* mechanism
  (hardlink + O_EXCL fallback, JSON holder, mtime heartbeat) is Windows-portable and
  is **not** touched; only the broken liveness *probe* grows a Windows branch (audit
  02 §1; plan §3.4 不要无证据重写现有锁). The real dual-process test
  (`test_c0911_gates.py::test_b4`) is already Windows-compatible.
- **No single unified subprocess runner in W1.** The load-bearing paths
  (`run_ffmpeg`, `local_cmd`, `gitops._run`) already own argv/timeout/cancel; full
  unification of ~30 sites exceeds the wave file budget and is a regression-risk
  refactor. W1 fixes only the *tree-kill owner* (`local_cmd`) for Windows (audit 02
  §3 → DEFERRED_WITH_EVIDENCE).
- **Old projects gain WARNINGS, never new errors.** The Windows portability scan in
  `manju check` surfaces pre-existing hazards (reserved names, case-fold collisions)
  as warnings only, so every legacy project keeps building (plan W1 completion
  condition).
- **The Windows CI job is a HARD gate.** `windows-ci.yml` runs the full suite on
  `windows-latest` with **no `continue-on-error`**, superseding `xplat.yml`'s
  informational 12-file subset; a pinned, presence-asserted ffmpeg closes the
  "green by silently skipping media" hole (audit 03 §2/§4).
- **File budget honesty.** The plan budgets ≤10 production files; W1 touched **11**
  — `core/events.py` was added mid-wave when the audit fact surfaced that *every*
  event append refuses/drops on Windows, a hard blocker for a green Windows gate.
  Recorded as a deliberate, evidenced overrun (see COMPLETION §Implementation).

## REPORTS paths

- `REPORTS/WINDOWS_WAVE_1_BASELINE.md` (this file)
- `REPORTS/WINDOWS_WAVE_1_COMPLETION.md`
