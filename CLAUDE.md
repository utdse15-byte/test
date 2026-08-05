# For AI sessions working on this repository

**This is PERSONAL, single-user software, and Windows 11 is the primary
platform.** 这是店主个人使用的软件,不是多用户产品;Windows 11 x64 是第一平台。

Standing facts every session should honor:

- The owner is the only user. No SaaS, no multi-user, no real paid providers
  by default — see the wave plan MANJU_WINDOWS_ONLY_LEAN_V3 in
  DECISIONS.md #36-#41 and REPORTS/WINDOWS_WAVE_*.md.
- **MCP surface FROZEN by owner decision (2026-07-31)** — 店主原话:
  「感觉没有必要,有 CLI 和 GUI 就可以了」,并要求把这话传给后续接手的
  AI 会话(就是这一条)。`manju serve-mcp`、它的工具与全部 MCP 测试
  **原样保留并保持绿**(已付费的守护面;机械性保绿 — 比如既有 parity 钉
  因 CLI 长了新格式而红时机械补齐 — 属维护,仍在范围内),但**不再有任何
  新投入**:不加新工具、不扩 schema、不再做 MCP 专项测试/加固波,除非
  店主亲口重开。(DECISIONS TRISURFACE-FIX #25)
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

Where the rest lives (a cold session should read these before touching code):

- **Operating protocol → `skills/manju/SKILL.md`** — how to drive the product
  (take selection, stale semantics, ask_before gates, the takeover ritual).
  Task playbooks live beside it in `skills/*/SKILL.md`; `manju skills` lists them.
  Every skill states 「什么时候不该用」 — read it before loading one, over-triggering
  is the expensive failure. Depth the core protocol pushed out of its <300-line
  budget lives in `skills/manju/references/` (progressive disclosure, layer 3).
- **Every command → `docs/CLI.md`** (moved out of README 2026-08-01; the README
  keeps the owner's daily path). Task-first navigation: `manju help-workflow`.
- **Repo state, open risks, work in flight → `STATE.md`** (short by policy).
- **Why anything is the way it is → `DECISIONS.md`** — start at its two index
  tables. Citation rule: a bare `#N` means the TOP-LEVEL `## N.` entry; entries
  inside a named section must carry it (`UX-REAL-USE #33`, `TRISURFACE-FIX #25`).
- **What was measured → `REPORTS/INDEX.md`** (one row per landed wave).

Dev loop (never bare `pytest` — src layout needs the editable install):

- Full suite: `python -m pytest -q -n auto` (~6-9 min on 4 cores; needs
  `pip install -e ".[dev]" -c constraints.txt` + ffmpeg/ffprobe on PATH).
- **Use ffmpeg 6.1.1 — the version windows-ci.yml pins.** On Ubuntu 24.04 that
  is just `apt install ffmpeg`. An off-pin 7.x build makes
  `tests/test_transitions_looks.py` fail intermittently (~1 run in 6) with
  `[aost#0:1/aac] Could not open encoder before EOF` — that is an ffmpeg
  regression in the `acrossfade` leg, NOT a Manju bug and NOT your change:
  measured 9 failures in 1600 runs on 7.1 versus 0 in 1600 on 6.1.1, with
  byte-identical inputs (DECISIONS `UX-REAL-USE #33` — the bare `#33` used to
  land on an unrelated top-level entry). A previous session lost most of a day
  to it. If you see that signature, check `ffmpeg -version` first.
- Fast loop: `python -m pytest -q -n auto -m "not ffmpeg"` — deselects the
  wholly ffmpeg-gated render-heavy modules (honest under-selection: mixed
  modules keep their pure halves). Full suite before every commit.
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
- GUI project identity (stale-tab guard) → `gui/state.project_identity`;
  the `manju-project` meta is stamped ONLY by `gui/server._send_text` —
  never hand-embed it in a page shell (DECISIONS #45)
- CLI shot/take shorthand (s14/14 → S014, 3 → take_03) →
  `cli._resolve_shot_arg` / `cli._resolve_take_arg` — existing entities
  only, ambiguity fails bad_args, echo on stderr (DECISIONS #47)
