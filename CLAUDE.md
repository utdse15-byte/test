# For AI sessions working on this repository

**This is PERSONAL, single-user software, and Windows 11 is the primary
platform.** 这是店主个人使用的软件,不是多用户产品;Windows 11 x64 是第一平台。

Standing facts every session should honor:

- The owner is the only user. No SaaS, no multi-user, no real paid providers
  by default — see the wave plan MANJU_WINDOWS_ONLY_LEAN_V3 in
  DECISIONS.md #36-#38 and REPORTS/WINDOWS_WAVE_*.md.
- `.github/workflows/windows-ci.yml` is the HARD gate (windows-latest, full
  suite, pinned ffmpeg 6.1.1, no continue-on-error). The ubuntu `ci.yml`
  stays green as the dev-loop gate; macOS is informational only (xplat.yml).
- Architecture invariants (README §disciplines): text is truth, media is
  append-only, `.manju/`/SQLite is disposable, derived reports are never
  build inputs, security checks live in service/core (not just CLI),
  UNKNOWN is never guessed into PASS.
- Change control: CONTRACTS.yaml + tests/test_fp_contracts.py (schema ids),
  tests/fixtures/cli_surface.json (frozen CLI), red-first tests, DECISIONS.md
  entry + REPORTS row per landed wave. Don't weaken existing tests.
- Windows specifics already paid for (don't regress): two-level filtergraph
  path escaping (`media/render._escape_filter_path` is the ONE owner),
  msvcrt lock branches (events/recents/library/failures), non-POSIX command
  splitting (`providers/local_cmd._split_command`), `sorted(..., key=as_posix)`
  for cross-platform determinism, explicit UTF-8 on every file read/write.
- Maintenance mode after W1-W3: accept Windows-real-usage bugs, data-loss/
  security/paid-safety issues, NLE compatibility changes, and provably
  beneficial improvements — nothing speculative (plan §11).
