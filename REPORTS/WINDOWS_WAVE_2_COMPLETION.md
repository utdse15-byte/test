# Windows Wave 2 — Completion (MANJU_WINDOWS_ONLY_LEAN_V3, W2: 安装、Portable、升级和 Doctor)

Plan **MANJU_WINDOWS_ONLY_LEAN_V3**, wave **W2** (§4.1–4.6). Branch
`claude/implement-ai-advice-delegation-tvt9ms`, base = the W1 tip **b1349ec**
(code + hard gate) + **7962545** (W1 docs). Authored on a Linux container
(Python 3.11.15, ffmpeg 6.1.1); **no Windows host, and PowerShell cannot run
here** — the Windows doctor probes are stubbed and the installer scripts are
pinned as text, with the `windows-ci.yml` `install-smoke` job on `windows-latest`
as the executable half (stated honestly here and in the Baseline). All **21** new
tests pass on the authoring host; the real-host run is the gate's first execution.

## Baseline

Full suite at the W1 tip b1349ec with full tooling: **4137 passed, 1 skipped,
0 failed** (`pytest -n auto`) — W1's verified end state (the 4115-passed
pre-Windows baseline plus W1's 22 tests; see
`REPORTS/WINDOWS_WAVE_1_COMPLETION.md`). Ubuntu CI green on the W1 push; the first
`windows-ci.yml` execution was in progress when W2 landed. One audit dossier
(Install story + Doctor) established the W1-tip status of every W2 capability
before any code changed; its statuses/owners are the Baseline's audit table.

## Audit

Decision key: **Implement** (W2 built it) · **Kept** (already satisfied, pinned) ·
**Deferred** (with evidence, out of W2 scope) · **Rejected** (per a plan rule) ·
**N-t-d** (nothing to do / vacuous pass).

| Capability | Decision | Evidence |
|------------|----------|----------|
| §4.5 doctor states the Python actually running it | **Implement** | `build/doctor.py:120` `python` row — version/machine/`sys.executable`, informational |
| §4.5 Windows env rows: OS build, long-path policy, NTFS, network drive, OneDrive, config writability, Edge/Chrome, install mode + rollback | **Implement** | `build/doctor.py:280` `_add_windows_rows` (auto on `_IS_WINDOWS`); probes `_win_long_paths_enabled:40` (winreg READ-ONLY), `_win_volume_fs:56` (`GetVolumeInformationW`), `_win_drive_remote:72` (`GetDriveTypeW`) |
| §4.5 UNKNOWN 不得猜成 PASS | **Implement** | every probe returns `None` for undecidable → row renders `•`/`未知`, never `✓`; pinned by `test_doctor_long_paths_unknown_is_reported_unknown` |
| §4.5 doctor output hygiene — no username / private absolute path leak | **Implement** | `supportbundle.py:175` `redact_private_text` + `:170` `_PRIVATE_PATH_RE`, wired at the ONE `add()` choke point (`doctor.py:113`), degrading to identity if the import fails (`doctor.py:108-111`) |
| §4.5 reuse THE redactor, never a parallel one | **Kept** | `redact_private_text` is the same owner and reuses `redact_text`'s building blocks (`_AUTH_RE`/`_BEARER_RE`/`_SIGNED_URL_RE`/`SECRET_PATTERNS`/`_basename_sub`) |
| §4.5 `manju doctor --windows` honest off-Windows | **Implement** | `doctor.py:212` "非 Windows 主机 — Windows 探测已跳过" row; the flag at `cli.py:6893`, passed as `windows=True if windows else None` (`cli.py:6909`) |
| §4.5/§5.6 Edge/Chrome discovery | **Implement** | `media/html_card.py:41` `find_edge` (PATH names, then ProgramFiles/ProgramFiles(x86)/LOCALAPPDATA roots, `_IS_WINDOWS`-guarded); consumed by doctor's `edge_chrome` row, by `board --app` in W3 |
| §4.1–4.3 per-user installer (layout, staging, self-test, atomic pointer switch, run-time launcher, no admin, no IEX, no registry, PATH opt-in) | **Implement** | `scripts/windows/install-manju.ps1` |
| §4.3 update keeps previous + rollback | **Implement** | `scripts/windows/update-manju.ps1` (`previous.txt`, `-Rollback`, delegates to the installer) |
| §4.6 uninstall never touches projects or `~/.manju` | **Implement** | `scripts/windows/uninstall-manju.ps1` (only `%LOCALAPPDATA%\Manju` subtrees; `-RemoveUserPathEntry` opt-in) |
| §4.6 the installer contract on a REAL host | **Implement** | `windows-ci.yml` `install-smoke` job — install → launcher `--version` → update keeps `previous.txt` → rollback → uninstall preserves a space+CJK project; no `continue-on-error` |
| §4.1 config under `%APPDATA%\Manju` | **Rejected** | the six-module `~/.manju` owner is consistent and Windows-valid; migrating risks silent data loss for zero functional gain — doctor reports the config dir + writability instead |
| §4.4 portable embeddable-Python ZIP | **Deferred** | the plan ranks the per-user install first; the pinned-wheel bundle needs artifact hosting this repo lacks; doctor + installer close the W2 goal |
| six-site `~/.manju` consolidation into one owner module | **Deferred** | pure churn without a Windows deliverable; every site is already env-overridable and consistent |
| frozen-CLI-surface snapshot regen | **N-t-d** | `--windows` is optional; the snapshot pins required params only — verified by running `test_fp_cli_snapshot` green |

## Red tests

20 red at the W1 tip, 1 deliberate green pin; **all 21 pass** after
implementation. Root cause = the missing production seam each test names.
(15 doctor + 6 installer; the planning note's "16 / two green pins" folded in a
W1-resident pin — see the Baseline's count reconciliation.)

| Test | Old (W1 tip) | New | Root cause |
|------|--------------|-----|------------|
| `test_doctor_has_python_row` | no `python` row | states `platform.python_version()` | interpreter never reported |
| `test_doctor_windows_flag_is_honest_off_windows` | `TypeError` (no kwarg) | `windows` row says probes skipped | `run_doctor` had no `windows=` param |
| `test_doctor_windows_rows_present_on_nt` | `AttributeError` on `_IS_WINDOWS` | `windows_version`/`long_paths`/`app_install` present | no flag, no probes, no rows |
| `test_doctor_long_paths_disabled_is_an_advisory` | RED | `⚠` line, `LongPathsEnabled` in detail, still `ok=True` | no `long_paths` row |
| `test_doctor_long_paths_unknown_is_reported_unknown` | RED | `None` → detail says unknown, line not `✓` | no UNKNOWN-never-PASS policy |
| `test_doctor_ntfs_and_network_rows` | RED | `FAT32`→`⚠`, network drive→`⚠` | no `filesystem`/`project_drive` rows |
| `test_doctor_onedrive_advisory` | RED | env/path match → `⚠ OneDrive` | no `onedrive` row |
| `test_doctor_config_dir_writable_row` | RED | `config_writable` present | no `~/.manju` probe |
| `test_doctor_reports_installed_versions_and_rollback` | RED | `app_install` shows current **and** previous | `current.txt`/`previous.txt` never read |
| `test_doctor_redacts_private_paths` | `C:\Users\bob\…` survived | `bob`/`C:\Users` gone, `ffmpeg.exe` basename kept | no redaction at `add()` |
| `test_doctor_system_paths_stay_readable` | **green pin** | `/usr/bin/ffmpeg` still in detail | narrow redaction must not nuke system paths |
| `test_redact_private_text_composition` | `ImportError` | private roots collapse, secrets/signed-URL mask, `/usr` + CJK survive | the composition did not exist |
| `test_cli_doctor_windows_flag` | Typer exit 2 | `--windows` runs, exit 0 | no `--windows` option |
| `test_find_edge_discovers_windows_install_paths` | `AttributeError` | finds `msedge.exe` under a redirected ProgramFiles | `find_edge` absent |
| `test_find_edge_absent_is_none` | `AttributeError` | returns `None` | `find_edge` absent |
| `test_scripts_exist_with_strict_mode_and_stop` | RED (no scripts) | all three: strict mode + `Stop` | scripts did not exist |
| `test_no_forbidden_constructs` | RED (no scripts) | no IEX/download-run/registry/elevation strings | scripts did not exist |
| `test_path_edits_are_user_scope_and_opt_in_only` | RED (no scripts) | no `"Machine"`; PATH edits behind explicit switches | scripts did not exist |
| `test_install_layout_matches_plan` | RED (no scripts) | §4.1 layout + `Move-Item` pointer switch | scripts did not exist |
| `test_update_keeps_previous_version_for_rollback` | RED (no scripts) | `previous.txt` + `-Rollback` + delegation | scripts did not exist |
| `test_uninstall_never_touches_projects_or_user_config` | RED (no scripts) | `*.manju`/`~/.manju` named untouchable; scoped `Remove-Item` | scripts did not exist |

## Implementation

### Production source files (4)

1. **`src/manju/core/supportbundle.py`** — the NARROW composition, same owner as
   the existing redactor. New `redact_private_text` (`:175`) masks secrets, auth
   headers and signed-URL queries **exactly** as `redact_text` (reusing
   `_AUTH_RE`/`_BEARER_RE`/`_SIGNED_URL_RE`/`SECRET_PATTERNS`/`_basename_sub`/
   `_bump`), but collapses **only** private-rooted paths via the new
   `_PRIVATE_PATH_RE` (`:170`) — `/home` `/root` `/Users` and drive-letter roots →
   basename, while `/usr` `/opt` `/etc` `/tmp` stay readable. `_PRIVATE_PATH_RE` is
   deliberately narrower than `_SENSITIVE_PATH_RE`. **No parallel redactor.**
2. **`src/manju/build/doctor.py`** — the one engine, extended in place.
   - `_IS_WINDOWS` module flag (`:30`) so the Windows dispatch is patchable in
     tests without touching global `os`.
   - **Redaction at the ONE `add()` choke point** (`:113`): every row's `detail`
     and `line` pass `redact_private_text`; imported at `:108` and degrading to an
     identity `_redact` (`:110`) if the seam cannot import — doctor must never
     crash on hygiene.
   - **`python` row** (`:120`) — version/machine/`sys.executable`, informational.
   - **`run_doctor(project, *, windows=None)`** (`:85`): Windows rows fire
     automatically on `_IS_WINDOWS`; `--windows` forces them; forced on a
     non-Windows host emits the honest "非 Windows 主机 — Windows 探测已跳过"
     row (`:212`) — never a fabricated Windows fact.
   - **Three probes**, each tiny, individually guarded, individually stubbable,
     each `None` for UNKNOWN: `_win_long_paths_enabled` (`:40`, `winreg` HKLM
     FileSystem\LongPathsEnabled, **READ-ONLY** — doctor never writes the
     registry), `_win_volume_fs` (`:56`, `GetVolumeInformationW`), `_win_drive_remote`
     (`:72`, `GetDriveTypeW == DRIVE_REMOTE`).
   - **`_add_windows_rows`** (`:280`), all INFORMATIONAL: `windows_version`;
     `long_paths` (True `✓` / False `⚠` advisory / `None` → `•` 未知, never `✓`);
     `filesystem` (NTFS `✓` / other `⚠` / unknown `•`); `project_drive`
     (network `⚠`); `onedrive` advisory (env prefix or path match);
     `config_writable` (`~/.manju` write-probe, `:340`); `edge_chrome`
     (`find_edge`/`find_chromium`, `:352`); `app_install` (`:375`) — version +
     install mode + launcher `current.txt`/`previous.txt` pointers, i.e. the
     rollback visibility the §4.5 checklist asks for. **The gating set is
     unchanged.**
3. **`src/manju/media/html_card.py`** — `find_edge` (`:41`): PATH names
   (`_EDGE_PATH_CANDIDATES`, `:34`) via `shutil.which`, then the standard Windows
   roots (`ProgramFiles`/`ProgramFiles(x86)`/`LOCALAPPDATA` + `_EDGE_WINDOWS_SUFFIX`,
   `:38`) behind the `_IS_WINDOWS` guard (`:32`). `None` degrades honestly (caller
   falls back to the default browser). Consumed by doctor now, by `board --app` in W3.
4. **`src/manju/cli.py`** — the `doctor` command gains the **optional** `--windows`
   flag (`:6893`), passed as `run_doctor(project, windows=True if windows else None)`
   (`:6909`). The frozen CLI surface pins required params only, so **no snapshot
   regen** was needed — verified by running `test_fp_cli_snapshot` green.

Plus one **updated existing test**:

5. **`tests/test_doctor.py`** — the `which()`-verbatim assertion now expects
   `redact_private_text(found or "NOT FOUND")`: identity on a POSIX `/usr` path,
   basename-collapse on a Windows `C:\Users` path. The old assertion was
   Windows-broken by design (it asserted a raw `C:\Users` path would survive an
   unredacted doctor). Recorded as an **intentional test evolution**, not a
   weakening (see Compatibility).

### Scripts (`scripts/windows/`, greenfield §4.1–4.3)

- **`install-manju.ps1`** — per-user `%LOCALAPPDATA%\Manju\{App\<version>\venv,
  bin, Logs}` with a `current.txt` pointer. Flow: stage into `<version>.staging`
  → self-test (`manju --version` + `doctor` run) → atomic `Move-Item` pointer
  switch (a rename, not an in-place write); a failed install never disturbs the
  active version. The launcher `bin\manju.cmd` resolves the pointer **at run
  time**. PATH is untouched unless `-AddToPath`, and then USER scope only.
  `Set-StrictMode -Version Latest` + `$ErrorActionPreference = "Stop"`; no
  `Invoke-Expression`, no downloads, no registry, no admin.
- **`update-manju.ps1`** — a thin wrapper: it **delegates to the installer** (one
  flow), records `previous.txt` on success, and `-Rollback` re-points the pointer
  to the previous version.
- **`uninstall-manju.ps1`** — removes only `%LOCALAPPDATA%\Manju` subtrees
  (App/bin/Cache, Logs with `-Logs`); `*.manju` projects and `~/.manju` config are
  **never** touched; `-RemoveUserPathEntry` is opt-in.

### Workflow

- **`.github/workflows/windows-ci.yml`** gains the `install-smoke` job — a real
  `windows-latest` E2E: clean install → launcher `--version` → update keeps
  `previous.txt` → rollback → uninstall while a space+CJK project under
  `%USERPROFILE%\作品 集\雨夜 便利店.manju` **survives**. **No `continue-on-error`.**

### Tests

- `tests/test_windows_doctor.py` (15) — the python row, the honest off-Windows
  `--windows`, the four Windows probe outcomes (present/advisory/unknown/NTFS+
  network), OneDrive, config writability, install-mode + rollback visibility, the
  private-path redaction + system-path readability + the composition unit, the CLI
  flag, and `find_edge` discovery/absence.
- `tests/test_windows_install.py` (6) — the static text safety pins for the three
  scripts.

### CLI / contracts

- **No required-param CLI change** — `--windows` is optional; the frozen surface
  snapshot is unchanged (`test_fp_cli_snapshot` green).
- **One doctor behavior change visible to old consumers**: private-rooted paths in
  row output are now redacted (POSIX `/usr` paths identity; Windows `C:\Users`
  basename). The single `test_doctor.py` assertion was updated to track it —
  recorded here as intentional, not a silent weakening.

### Design deviations (honest)

- **Config NOT migrated to `%APPDATA%\Manju`** (REJECTED_WITH_REASON). The
  existing `~/.manju` owner spans six modules
  (providers/routing/recents/skills/library/GUI state), is consistent and
  Windows-valid; migrating locations risks silent data loss for zero functional
  gain. Doctor reports the config dir + writability instead; revisit only on real
  Windows-user evidence.
- **Portable ZIP (§4.4) DEFERRED_WITH_EVIDENCE.** The plan ranks the PowerShell
  per-user install first; the embeddable-Python + pinned-wheel bundle needs
  artifact hosting this repo does not have. Doctor + installer close the W2 goal.
- **Six-site `~/.manju` consolidation DEFERRED.** Pure churn without a Windows
  deliverable; every site is already env-overridable and consistent.
- **Test count is 15 doctor + 6 install = 21, not the planning note's 16 + 6.**
  The 16th "green pin" is `test_events_append_refuses_when_no_lock_primitive_exists`,
  which lives in **W1's** `tests/test_windows_procs.py` and is not re-added here.

## Compatibility

- **Doctor's gating set is unchanged** — every new row is informational, so no old
  consumer's `ok`/exit-code moves.
- **The only visible doctor behavior change is redaction** of private-rooted paths
  (POSIX `/usr` identity; Windows `C:\Users` → basename). One test assertion
  updated to match; no test deleted or weakened.
- **CLI surface unchanged for required params** — `--windows` is optional; snapshot
  intact.
- **Config owner unchanged** — `~/.manju`, no data migration, no seventh
  `Path.home()` computation added.
- **`find_chromium` untouched** — `find_edge` is purely additive; the browser row
  prefers Edge then Chromium, else honestly reports "not found → default browser".

## Verification

Targeted suites on the authoring host (Linux, full tooling):

- `tests/test_windows_doctor.py` + `tests/test_windows_install.py` — **21 passed,
  0 skipped** (15 doctor + 6 install).
- Neighbor suites `tests/test_doctor.py`, `tests/test_fp_doctor_locale.py`,
  `tests/test_fp_colorstats_wall.py`, `tests/test_fp_cli_snapshot.py`,
  `tests/test_fp_supportbundle.py` — **39 passed** (redaction, doctor locale rows,
  colorstats wall, the frozen CLI snapshot, and the supportbundle tripwire all
  still green).

Full suite (`pytest -n auto`, authoring host): **4158 passed, 1 skipped, 0 failed (508.71s) — the 4137 W1 tip plus the 21 new tests; the failing set is empty before AND after. One pre-existing xdist ordering flake (test_m0_incremental_rerender depended on test_m0_full_build's worker) was made self-sufficient in the same change**.

Windows CI (`windows-ci.yml` full suite + the new `install-smoke` job): **the FIRST windows-ci.yml execution (W1 push, run #1) completed RED as anticipated: 76 failed / 4016 passed / 11 skipped / 35 errors in 9m30s — the toolchain steps all worked (choco ffmpeg 8.1.2 verified, full extras install, full pytest executed); the failures are REAL Windows product bugs (ass= filter drive-colon escaping breaking every subtitled final render; fcntl-only recents/library/failures locks dropping concurrent updates; local_cmd shlex eating backslashes; ffprobe-8 color-tag parse skew; plus test-side POSIX assumptions). Catalogued and driven down in the Windows-gate fix rounds recorded in DECISIONS**.

Method: `python -m pytest` only; red-first pins for every new behavior; no existing
test deleted or weakened (the one `test_doctor.py` assertion was updated to track
the intentional redaction change, recorded above).

## Skipped / Rejected

| Item | Reason |
|------|--------|
| Portable embeddable-Python ZIP (§4.4) | **DEFERRED_WITH_EVIDENCE** — the plan ranks the per-user PowerShell install first; the pinned-wheel bundle needs artifact hosting this repo lacks; doctor + installer already close the W2 goal |
| Config migration to `%APPDATA%\Manju` (§4.1) | **REJECTED_WITH_REASON** — the six-module `~/.manju` owner is consistent and Windows-valid; migrating risks silent data loss for zero functional gain; doctor reports the config dir + writability instead |
| Six-site `~/.manju` consolidation into one owner module | **DEFERRED** — pure churn without a Windows deliverable; every site is already env-overridable and consistent |
| Doctor auto-*fixing* long-path policy / OneDrive exclusion | **REJECTED** — doctor is READ-ONLY (never writes the registry, never moves the user's files); it advises, the user acts |

## Final

**Commit SHA:** `72dfa91 (code + scripts + install-smoke job; this report and DECISIONS #37 land in the docs follow-up commit)` (code + scripts + tests + the `install-smoke`
job; this report and the DECISIONS follow-up land in the docs commit).

**Remaining risks:**

1. **`install-smoke` has never run on a real host** until the W2 push. Its first
   execution is the only real-host proof that the clean install → launcher →
   update → rollback → uninstall chain (and the space+CJK-project preservation)
   actually holds on `windows-latest`.
2. **The `winreg`/`ctypes` probes are stub-tested only** until the gate runs.
   `_win_long_paths_enabled`, `_win_volume_fs` and `_win_drive_remote` are
   exercised on Linux through injected stubs; the real HKLM read and the two
   `kernel32` calls run for the first time on the Windows leg.
3. **`redact_private_text`'s drive-letter regex collapses ANY drive-letter path**
   (even `D:\media\clip.mp4`), not just `C:\Users\…`. Accepted: a drive letter is a
   user-machine fact worth hiding in a shared paste; noted here so it is not
   mistaken for over-redaction.
4. **The `app_install` install-mode label is a heuristic** — it reads
   `site-packages` in `manju.__file__` to distinguish a pip install from an
   editable checkout. Informational only; it never gates and never blocks a launch.
