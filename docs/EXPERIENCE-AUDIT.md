# EXPERIENCE-AUDIT.md — the three-column ledger (round V, agent VG, goal item 7)

An AUDIT-THEN-FIX sweep against ROUND-V-REFERENCES-2 §3 (NN/g error rubric +
rustc/clig.dev what-why-fix triple; empty states; the HUMAN | AI | SYSTEM
three-column checklist). Behavior-preserving polish only — no schema changes, no
new features, no renames. Each row is either **FIXED** in this commit (with a
file:line pointer) or **RECORDED** for later (too big to fix locally/safely, or a
by-design state confirmed).

Legend: ✅ fixed this commit · 📝 recorded for later · ✔ audited, already-OK.

---

## HUMAN column — terminal + web

### A(1) Error messages → the what / why / how-to-fix triple (中文)

| # | Finding | Status | Pointer |
|---|---------|--------|---------|
| H1 | "not a manju project" gave WHAT only, no fix | ✅ | `core/container.py:93` — adds WHY (missing project.yaml) + HOW (`manju new`) |
| H2 | **The #1 first-run error** "no manju project found" gave WHAT only | ✅ | `core/container.py:107` — adds WHY + two ways out (cd into `*.manju` / `manju new`) |
| H3 | **Missing ffmpeg** surfaced as a raw `FileNotFoundError` traceback (the #1 first-run blocker) | ✅ | `media/ffmpeg.py:68-88` — catches it, raises `MediaError` with per-OS install commands + `manju doctor`, and records a structured Failure |
| H4 | `select`: "provide a take name" was bare | ✅ | `cli.py:593` — says what a take is + both ways to supply one |
| H5 | `select`: "has no take 'X'" did not say which takes exist | ✅ | `cli.py:597-600` — lists the real take names (or "no takes yet → build/redo") |
| H6 | `voice`: "no dialogue.text" did not point at the field | ✅ | `cli.py:1555` — names `shots/<id>.yaml` → `dialogue.text` |
| H7 | `import`: "not found: X" was terse | ✅ | `cli.py:334` — adds WHY (path relative to cwd) + HOW (check spelling / cwd) |
| H8 | `redo` / `voice` / `prompt` "pass a shot id" were English-only | ✅ | `cli.py:544` / `cli.py:1551` / `cli.py:1452` — 中文, keep the command examples |
| H9 | Provider API-key-unset error | ✔ | `providers/generic_cloud.py:201`, `providers/stock.py:57`, `providers/manifest.py:570` — already a triple (what/why + export hint) |
| H10 | Budget-breaker error | ✔ | `build/graph.py:453` — already a triple (预算熔断 + raise-limit/shrink-plan hint) |
| H11 | `no timeline.json` / `no repair_plan.yaml` | ✔ | `cli.py:1184` / `cli.py:1091` — already "run `manju build`/`qc` first" |
| H12 | CLI tracebacks in default stdout (clig.dev: keep behind --verbose) | 📝 | Unhandled exceptions still print a Python traceback; a global `--verbose`-gated handler is a larger cross-cutting change — recorded, not swept |

### A(2) Empty states — every list-like page says what + one next action

Audited all ten list surfaces across the GUI. **8/10 have proper 中文 empty
states** at their primary render sites; the remainder:

| # | Surface | Status | Pointer |
|---|---------|--------|---------|
| H13 | shots / takes / library / proposals / suggestions / events / QC / no-final / providers / captions | ✔ | e.g. `storyboard.py:307`, `page.py:2549`, `pages.py:690`, `director_page.py:289`, `page.py:3501`, `pages.py:810`, `pages_t.py:127` — all render a "what + next action" line |
| H14 | SPA **failures** panel hidden when empty (`page.py:4223`) | 📝 (by-design) | Correct "calm / by-exception" per RV2 §2 block-2 ("no red banner on a clean project"); no bare-blank shown — left as-is |
| H15 | SPA **jobs** panel hidden when empty (`page.py:2123`) | 📝 (by-design) | Idle = nothing running; cockpit block-6 already renders `空闲 (idle)` — no bare-blank; left as-is |
| H16 | exports **deliverables grid** has no dedicated empty branch (`exports_page.py:144`) | 📝 | Effectively never blank (fixed kind-roster always renders rows) + summary chip says `暂无产物`; low-severity, recorded |

### A(3) 新手 hint bar + onboarding copy reads with the glossary

| # | Finding | Status | Pointer |
|---|---------|--------|---------|
| H17 | First-run onboarding checklist copy | ✔ | `gui/onboarding.py:83-114` — exemplary: each step = 中文 title + 怎么做 hint + one CLI + view anchor; glossary-aligned terms (分镜/take/dialogue.text/好·弃/成片) |

---

## AI-AGENT column

| # | Finding | Status | Pointer |
|---|---------|--------|---------|
| A1 | **`_fail` under `--json` emitted colored prose, not JSON** — an agent parsing `--json` output got nothing machine-readable on failure | ✅ | `cli.py:48-73` — one central change covers all 98 `_fail` sites: under `--json` emits a stable `{"error", "code"}` object on stdout (RFC 7807-style); prose+stderr otherwise. Detects `--json` by walking caller frames for the `as_json` local (Typer pushes no Click context) |
| A2 | The #1 error path (`_project`) bypassed `_fail`, so it never got the JSON shape | ✅ | `cli.py:37-40` — routes through `_fail(..., code="no_project")`; stable machine code |
| A3 | `--json` coverage on read paths | ✔ | Every read command/subcommand has `--json` (spot-checked all groups); `schema` emits JSON natively — nothing trivially missing to add |
| A4 | Semantic exit codes — check / qc / prompt --check must exit non-zero on failure | ✔ | `cli.py:309` (check), `cli.py:856` (qc), `cli.py:1447` (prompt --check) all `raise Exit(1)` on `not ok` / blocking; verified end-to-end |
| A5 | MCP `director_suggest` desc claimed **every** suggestion carries an action payload (several are advisory `action: null`) and omitted the round-V funnel/select nudges | ✅ | `mcp/tools.py:660-666` — "MOST carry … some (funnel/select/budget) are advisory-only with `action: null`" + updated signal list |
| A6 | MCP `qc_verdict` supported an undocumented `from_file` input (absent from desc + inputSchema) | ✅ | `mcp/tools.py:547-570` — documented in description and added to `inputSchema` |
| A7 | MCP `status` desc listed a "phase" field the output does not have | ✅ | `mcp/tools.py:418` — "project/preset/mode" (the real fields) |
| A8 | MCP exposes no native-draft exports (`capcut`, `jianying_native`), no cockpit/series/native-cut/standalone-voicefix tools | 📝 | Adding MCP tools = new surface (out of scope for a polish sweep); recorded as a completeness gap |

---

## SYSTEM / robustness column

| # | Finding | Status | Pointer |
|---|---------|--------|---------|
| S1 | Actor attribution on round-U/V mutating surfaces | ✔ | funnel (`build/funnel.py:389`), series (`core/series.py:350`), qc verdict (`qc/agent_review.py:332`), voicefix (`media/voicefix.py:263`), director execute (`build/director.py:666/699`), native cut (`gui/edit_engine.py:286`) — all call `append_event` with a correct `actor` |
| S2 | **`via` tag** present only on GUI-emitted events; CLI/core surfaces (funnel, series, qc_verdict, voicefix, cli `export`, director_execute) log actor but no `via` | 📝 | `via` is a deliberate GUI-layer tag stamped at the endpoint (`gui/server.py:241`); the mutating core fns don't know their caller's surface, so a truthful `via` needs threading through many CLI call sites — a broader change than a local fix, recorded |
| S3 | **exports generate**: only the CapCut path recorded a structured Failure; srt/otio/jianying exporter faults propagated as bare tracebacks | ✅ | `cli.py:1189-1229` — wraps the srt/otio/jianying builders; records `Failure(step=export, subject, cause, evidence, hint)` and aborts cleanly, matching the capcut path |
| S4 | Failure records on voicefix + director execute carry cause/evidence/hint | ✔ | `media/voicefix.py:401`, `build/director.py:799` — confirmed complete |
| S5 | GUI export failures recorded only as `job.error`, not a structured Failure | 📝 | `gui/jobs.py:131` `JobRunner._run` sets `job.error`; adding structured-failure recording there is a cross-cutting JobRunner change — recorded |
| S6 | `.manju/logs` `default_log` writer names | ✔ | Distinct + sensible: render / frames / boards / packaging / repair (`media/render.py:1132`, `media/frames.py:92`, `media/boards.py:263`, `media/packaging.py:84`, `media/repair_ops.py:142`). Minor: `media/waveform.py:62` reuses `frames.log` (plausibly intentional) — noted |

---

## Tests

- `tests/test_experience.py` (new, 10 cases) pins the highest-value fixes:
  - error triples: not-in-project names the two ways out; `select` lists available
    takes; `voice` names `dialogue.text`; missing-ffmpeg message is a full triple.
  - `--json` structured-error shape: `{error, code}` on stdout; stable `no_project`
    code; success JSON still parses.
  - exit-code contract: `check` 0-when-clean / non-zero-on-error (both text and
    `--json`); `prompt --check` clean → 0.
- No existing test asserted on the error strings changed (verified: only
  `test_generic_cloud.py:78` pins the provider key-env message and
  `test_batch.py:396` pins the substring "no dialogue" — both preserved).

## Scorecard

- HUMAN: 8 fixed, 4 already-OK, 3 recorded.
- AI: 5 fixed (1 covering all 98 `_fail` sites), 2 already-OK, 1 recorded.
- SYSTEM: 1 fixed, 3 already-OK, 2 recorded.
