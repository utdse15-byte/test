# Windows Wave 2 — Baseline (MANJU_WINDOWS_ONLY_LEAN_V3, W2: 安装、Portable、升级和 Doctor)

Plan: **MANJU_WINDOWS_ONLY_LEAN_V3** — "安装、Portable、升级和 Doctor".
Slice: **W2 only** — §4.1–4.3 (per-user install layout / staging + atomic
pointer switch / launcher / PATH policy), §4.4 (portable ZIP — see the
deferral), §4.5 (doctor Windows hardening + output hygiene), §4.6 (uninstall +
rollback safety). W1 (Windows 硬 CI + 文件系统/进程安全) is landed; W3 (NLE
exchange, fonts, board `--app`) is out of scope.
Date: 2026-07-13 · Repo: `/home/user/test` (Manju) · Branch
`claude/implement-ai-advice-delegation-tvt9ms` · Base = the W1 tip **b1349ec**
(code + `windows-ci.yml` hard gate) + **7962545** (W1 docs: DECISIONS #36 and the
W1 wave reports).

## Authoring environment — and the honest limit of what it can prove

Authored on the same **Linux container** (Ubuntu Noble) as W1. **No Windows host
was available at any point**, and W2 is a *Windows install + Windows doctor* wave,
so the honesty limit is sharper than W1's and is stated three ways, none of them
pretending to be a real-host run:

1. **Doctor Windows probes** (`winreg` long-path read, `GetVolumeInformationW`,
   `GetDriveTypeW`) are driven on Linux by injecting the platform flag
   (`monkeypatch.setattr(doctor, "_IS_WINDOWS", True)`) and stubbing each tiny
   probe function (`_win_long_paths_enabled`, `_win_volume_fs`,
   `_win_drive_remote`). This exercises the **dispatch, the row rendering, the
   ✓/⚠/• glyph policy and the UNKNOWN-never-PASS rule** — but the *real* winreg
   read and the two `kernel32` calls only run on the Windows gate.
2. **PowerShell cannot execute on the authoring host.** The three installer
   scripts (`scripts/windows/*.ps1`) are therefore pinned as **static text
   safety properties** (`tests/test_windows_install.py`) — strict mode, no
   `Invoke-Expression`/download-and-run, no registry writes, no elevation,
   user-scope-and-opt-in-only PATH edits, the §4.1 layout, the staging +
   self-test + atomic pointer switch, and an uninstall that can never touch a
   project. The **executable** half of that contract is the new
   `windows-ci.yml` `install-smoke` job on a real `windows-latest` host — its
   **first run is the only real-host proof**, called out again in the
   COMPLETION's Remaining-risks.
3. **Behavioral doctor pins** that go red on the missing production seam each
   docstring names (no python row, no `windows=` kwarg, no Windows probes, no
   redaction, no `find_edge`, no `--windows` CLI flag).

| tool | version (authoring host) |
|------|--------------------------|
| Python | 3.11.15 |
| ffmpeg / ffprobe | 6.1.1-3ubuntu5 |
| tesseract | installed, with `chi_sim` + WenQuanYi fonts |
| PowerShell | **not available** — installer scripts pinned as text only |
| Windows host | **none** — `winreg`/`ctypes` probes stubbed; real calls run on the CI gate |
| OS | Linux (Ubuntu Noble) |

## Baseline test state at the W1 tip (b1349ec)

W2 begins exactly where W1 finished. The full suite at that tip, with the full
toolchain present (ffmpeg + tesseract + fonts):

```
python -m pytest -n auto
4137 passed, 1 skipped, 0 failed
```

That is W1's verified end state (the 4115-passed pre-Windows baseline plus W1's
22 new tests; see `REPORTS/WINDOWS_WAVE_1_COMPLETION.md`). Ubuntu CI was **green**
on the W1 push; the **first** `windows-ci.yml` execution (W1's hard gate) was
**in progress** when W2 landed — its result, and any Windows-only red evidence,
is recorded in the DECISIONS follow-up for this wave, not invented here.

## Audit summary — Dossier 04 (Install story + Doctor)

One dossier was produced before any code changed:
`audit/04_Install_story_+_Doctor_plan_W2`. It enumerates six capability areas;
each carries its status at the W1 tip and the **single owner** an implementer
must extend (never a parallel module). `ALREADY_IMPLEMENTED` = the invariant
already holds; `PARTIAL` = present but incomplete or not wired; `GAP` = does not
exist anywhere.

| # | Capability | Status | Owner |
|---|------------|--------|-------|
| §1 | The existing doctor checks (gating: ffmpeg/ffprobe/git + provider manifests + project check; the rest informational/advisory) | **ALREADY_IMPLEMENTED** | `build/doctor.py:27` `run_doctor` — pure `{checks:[…], ok}` function; three surfaces (CLI + 2 GUI) consume it |
| §2 | Windows-specific probes (OS build, `LongPathsEnabled`, NTFS, OneDrive/network-drive, Edge/Chrome, install mode, rollback) | **GAP** | none — grep found zero `winreg`/`LongPathsEnabled` in `src/`; `_CHROMIUM_CANDIDATES` has no `msedge`; add inside `run_doctor` |
| §3 | Secret/privacy hygiene of doctor output (no username, no private absolute path) | **PARTIAL** — no key values ever printed, **but** `shutil.which`/font/chromium rows embed verbatim absolute paths (on Windows `C:\Users\<name>\…` = a username leak); doctor does not route through the redactor | `core/supportbundle.py:134` `redact_text` — THE redactor, already handles `C:\`/`/Users`, but doctor never calls it |
| §4 | install / update / uninstall scripts, PATH handling | **GAP** — no `scripts/`, no `*.ps1`, no Makefile; the only install surface is `[project.scripts] manju` + `pip install -e .` | greenfield |
| §5 | config / cache / log locations + a config-dir owner module | **PARTIAL** — `~/.manju` is consistent and Windows-valid, but computed **independently in six modules**; there is **no** central owner | the six `~/.manju` location functions (providers/routing/recents/skills/library/gui-state) |
| §6 | version / rollback machinery (launcher) | **GAP** — `__version__` is a static string; `manju rollback` is **project-content** rollback (a name collision to avoid); no versioned install dirs, no launcher | greenfield |

## Red-test inventory — 21 new tests, 20 red at the W1 tip

Two new files: `tests/test_windows_doctor.py` (**15 tests**) and
`tests/test_windows_install.py` (**6 static pins**). Red-first discipline:
**20 go red** at the W1 tip, **1 is a deliberate green pin** (a POSIX-path
identity property that already held and must not regress). Every docstring names
its own gap.

> **Count reconciliation (honest).** The W2 planning note said "16 doctor tests,
> two green pins." The tree has **15** doctor tests; the second "green pin" it
> counted — the no-lock-primitive refusal — actually lives in **W1's**
> `tests/test_windows_procs.py`
> (`test_events_append_refuses_when_no_lock_primitive_exists`) and is **not**
> re-added here. Verified counts are used throughout: 15 doctor + 6 install = 21.

### `tests/test_windows_doctor.py` (§4.5)

| Test | HEAD | Failing reason at the W1 tip |
|------|------|------------------------------|
| `test_doctor_has_python_row` | RED | `run_doctor` had no `python` row — the interpreter running doctor was never stated |
| `test_doctor_windows_flag_is_honest_off_windows` | RED | `run_doctor(project)` had no `windows=` kwarg → `TypeError` |
| `test_doctor_windows_rows_present_on_nt` | RED | `doctor._IS_WINDOWS` absent (the `monkeypatch.setattr` has no target); `windows_version`/`long_paths`/`app_install` rows + the `_win_*` probes existed nowhere |
| `test_doctor_long_paths_disabled_is_an_advisory` | RED | no `long_paths` row / `_win_long_paths_enabled` |
| `test_doctor_long_paths_unknown_is_reported_unknown` | RED | same — and no UNKNOWN-renders-as-`•` policy to pin |
| `test_doctor_ntfs_and_network_rows` | RED | no `filesystem`/`project_drive` rows / `_win_volume_fs` / `_win_drive_remote` |
| `test_doctor_onedrive_advisory` | RED | no `onedrive` advisory row |
| `test_doctor_config_dir_writable_row` | RED | `_stub_probes` hits the absent `_IS_WINDOWS`; no `config_writable` row |
| `test_doctor_reports_installed_versions_and_rollback` | RED | no `app_install` row — the installer layout's `current.txt`/`previous.txt` were never read |
| `test_doctor_redacts_private_paths` | RED | no redaction at the `add()` choke point — `C:\Users\bob\…\ffmpeg.exe` (username and all) survived verbatim |
| `test_doctor_system_paths_stay_readable` | **GREEN pin** | at HEAD the raw `/usr/bin/ffmpeg` path already sits in the `ffmpeg` detail (identity); this pins that the *new* narrow redaction must NOT nuke diagnostic system paths — skips if no `/usr` ffmpeg |
| `test_redact_private_text_composition` | RED | `from …supportbundle import redact_private_text` → `ImportError` (the narrow composition did not exist) |
| `test_cli_doctor_windows_flag` | RED | the `doctor` command had no `--windows` option → Typer exit code 2 |
| `test_find_edge_discovers_windows_install_paths` | RED (AttributeError) | `html_card.find_edge` absent |
| `test_find_edge_absent_is_none` | RED (AttributeError) | `html_card.find_edge` absent |

### `tests/test_windows_install.py` (§4.2–4.6) — NEW-ARTIFACT pins

The three scripts **did not exist** at the W1 tip, so all six pins were red by
absence: the `_text()` helper asserts `p.exists()` first. They freeze the safety
*properties* of `scripts/windows/*.ps1` as text.

| Test | HEAD | What it now pins |
|------|------|------------------|
| `test_scripts_exist_with_strict_mode_and_stop` | RED (no scripts) | all three carry `Set-StrictMode -Version Latest` + `$ErrorActionPreference = "Stop"` |
| `test_no_forbidden_constructs` | RED (no scripts) | none of `Invoke-Expression`, `iex `, `DownloadString`, `Invoke-WebRequest`, `Set-ItemProperty`, `New-ItemProperty`, `reg.exe`, `reg add`, `-Verb RunAs`, `Start-BitsTransfer` ever appear |
| `test_path_edits_are_user_scope_and_opt_in_only` | RED (no scripts) | no `"Machine"` scope anywhere; the install PATH edit is behind `if ($AddToPath)`, the uninstall removal behind `if ($RemoveUserPathEntry)` |
| `test_install_layout_matches_plan` | RED (no scripts) | the §4.1 layout strings (`…\Manju`, `App`, `bin`, `Logs`, `current.txt`, `.staging`, `--version`, `doctor`, `manju.cmd`) + a `Move-Item` pointer switch (rename, not in-place write) |
| `test_update_keeps_previous_version_for_rollback` | RED (no scripts) | `previous.txt` + `-Rollback` + delegation to `install-manju.ps1` (one flow) |
| `test_uninstall_never_touches_projects_or_user_config` | RED (no scripts) | `*.manju`/`.manju project` named untouchable; every `Remove-Item` scoped under `$AppRoot`; base is `Join-Path $env:LOCALAPPDATA "Manju"` |

**Authoring note (the pin working as intended).** `test_no_forbidden_constructs`
first failed on the installer's **own header comment**, which literally read "no
`Invoke-Expression`". The comment was reworded to "no dynamic evaluation of
downloaded text" — the string pin caught a would-be false-negative in the very
document that promises the property, exactly as designed.

## Wave scope decisions

- **Extend the ONE redactor, never fork it.** `redact_private_text` lands *beside*
  `redact_text` in `core/supportbundle.py`, reusing the same building blocks
  (`_AUTH_RE`, `_BEARER_RE`, `_SIGNED_URL_RE`, `SECRET_PATTERNS`, `_basename_sub`,
  `_bump`). Its only new piece, `_PRIVATE_PATH_RE`, is **deliberately narrower**
  than `_SENSITIVE_PATH_RE`: only `/home` `/root` `/Users` and drive-letter roots
  collapse; `/usr` `/opt` `/etc` `/tmp` system paths stay readable — telling the
  user *where a tool lives* is doctor's whole job (audit 04 §3 OWNER).
- **All Windows rows are INFORMATIONAL.** They are added inside the one
  `run_doctor` engine (audit 04 §1/§2 OWNER); doctor's **gating set** (env tools +
  provider manifests + project check) is unchanged, so no old consumer's exit-code
  policy moves.
- **UNKNOWN 不得猜成 PASS.** Every probe returns `None` when it cannot decide; a
  `None` renders as `•`/`未知`, **never** a `✓`. This is pinned directly
  (`test_doctor_long_paths_unknown_is_reported_unknown`).
- **Config stays at `~/.manju` — NOT migrated to `%APPDATA%\Manju`**
  (REJECTED_WITH_REASON). The six-module owner is consistent and Windows-valid
  (`C:\Users\<x>\.manju`); relocating risks silent data loss for zero functional
  gain. Doctor **reports** the config dir + its writability instead of moving it.
- **Portable ZIP (§4.4) is DEFERRED_WITH_EVIDENCE.** The plan itself ranks the
  PowerShell per-user install *first* ("优先") and the ZIP second; the
  embeddable-Python + pinned-wheel bundle needs artifact hosting this repo does
  not have. Doctor + the installer already close the W2 goal (no more
  editable-install-only story).
- **Per-user, no-admin installer.** `%LOCALAPPDATA%\Manju`, its own venv per
  version, `current.txt` pointer, staging → self-test → **atomic** pointer switch,
  a launcher that resolves the pointer at **run** time, PATH untouched unless
  `-AddToPath` (then USER scope only).
- **The installer contract has a real-host gate.** `windows-ci.yml` gains an
  `install-smoke` job (clean install → launcher `--version` → update keeps
  `previous.txt` → rollback → uninstall preserves a space+CJK project) with **no
  `continue-on-error`** — the executable complement to the static text pins.

## REPORTS paths

- `REPORTS/WINDOWS_WAVE_2_BASELINE.md` (this file)
- `REPORTS/WINDOWS_WAVE_2_COMPLETION.md`
