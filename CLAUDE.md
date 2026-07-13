# For AI sessions working on this repository

**This is PERSONAL, single-user software, and Windows 11 is the primary
platform.** 这是店主个人使用的软件,不是多用户产品;Windows 11 x64 是第一平台。

Standing facts every session should honor:

- The owner is the only user. No SaaS, no multi-user, no real paid providers
  by default — see the wave plan MANJU_WINDOWS_ONLY_LEAN_V3 in
  DECISIONS.md #36-#41 and REPORTS/WINDOWS_WAVE_*.md.
- `.github/workflows/windows-ci.yml` is the HARD gate (windows-latest, full
  suite, pinned ffmpeg 6.1.1, no continue-on-error). The ubuntu `ci.yml`
  stays green as the dev-loop gate; macOS is informational only (xplat.yml).
- Architecture invariants (README §disciplines): text is truth, media is
  append-only, `.manju/`/SQLite is disposable, derived reports are never
  build inputs, security checks live in service/core (not just CLI),
  UNKNOWN is never guessed into PASS.
- Change control: CONTRACTS.yaml + tests/test_fp_contracts.py (schema ids),
  tests/fixtures/cli_surface.json (frozen CLI; regenerate via
  `python -m tests.test_fp_cli_snapshot`), red-first tests, DECISIONS.md
  entry + REPORTS/INDEX.md row per landed wave. Don't weaken existing tests.
  PROGRESS.md is CLOSED (frozen 2026-07-09) — never append there.
- Windows specifics already paid for (don't regress): two-level filtergraph
  path escaping (`media/render._escape_filter_path` is the ONE owner),
  msvcrt lock branches (events/recents/library/failures), non-POSIX command
  splitting (`providers/local_cmd._split_command`), `sorted(..., key=as_posix)`
  for cross-platform determinism, explicit UTF-8 on every file read/write
  AND on every `subprocess` decode (`encoding="utf-8", errors="replace"`).
- Maintenance mode after W1-W5: accept Windows-real-usage bugs, data-loss/
  security/paid-safety issues, NLE compatibility changes, and provably
  beneficial improvements — nothing speculative. (The plan document itself
  is not in the repo; this list IS the acceptance gate.)

Dev loop (never bare `pytest` — src layout needs the editable install):

- Full suite: `python -m pytest -q -n auto` (~6-9 min on 4 cores; needs
  `pip install -e ".[dev]" -c constraints.txt` + ffmpeg/ffprobe on PATH).
- Collection sanity: `python -m pytest tests/ --collect-only -q` (~3 s).

Standing grep-pins — several tests scan RAW SOURCE TEXT, so a token in a
COMMENT or docstring trips them (this has burned four recorded sessions;
see DECISIONS #38, #41 and the ratemig/c21g entries):

- `edit_rate` may be referenced only from its owner modules — consumers use
  `ProjectConfig.frame_rate` / `Timeline.frame_rate`
  (tests/test_fp_ratemig1.py::test_edit_rate_only_referenced_in_models_and_container).
- The toolchain-manifest module may never be referenced from `build/`,
  `core/`, `providers/`, `runtime/` — machine facts live in `media/ffmpeg`
  and core/toolchain delegates
  (tests/test_fp_toolchain.py::test_grep_pin_never_read_by_build_core_providers_runtime).
- The Board view must not render specialized-view tokens
  (tests/test_c21g_gates.py::test_a_board_does_not_render_the_specialized_views).
- Batch rule: a loop touching surface X re-runs the UNION of the previous
  X-loop's verification batch — per-loop batches kept dropping the pin that
  then went red on the definitive run.

Owners (one owner per concern — extend it, never fork a parallel one):

- filtergraph escaping → `media/render._escape_filter_path`
- command-template splitting → `providers/local_cmd._split_command`
- content hashing → `core.hashing.hash_file`
- encoder/toolchain machine FACTS → `media/ffmpeg` (record-only; never a
  build input; `core/toolchain` lazily delegates)
- CJK font resolution → `media/card.find_font`
- field seals → `core/locks.py`; advisory FILE locks → the msvcrt quartet
  in events/recents/library/failures (change all four together)
- optional `ProjectConfig` fields → JOIN the one wrap serializer
  `core/models._drop_default_edit_rate` (pydantic allows ONE
  model_serializer per model; the compat corpus pins byte-identity for
  projects that never set the field)
