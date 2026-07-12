# FP Loop J — `manju help-workflow` (task-oriented navigation, roadmap §8.3)

Roadmap §8.3. Branch `claude/cost-optimization-strategy-cjfmn5`, on top of c32f934.

## Scope ruling honoured

**NAVIGATION ONLY.** One data-driven command over a declared WORKFLOWS table.
No behaviour change to any existing command; no aliases, no deprecations, no
shell completion (all explicitly out of scope this loop). The table is CODE —
a cli-side dict in `src/manju/cli_workflows.py` — **not a new fact source**:
it never enters builds, caching, events, or any engine path, and nothing reads
it back except the one command that renders it.

**No schema id.** The command's `--json` output is plain CLI JSON, covered by
the existing **cli-json-surface document row** in `CONTRACTS.yaml`. No
`manju.*/vN` registration was added and `CONTRACTS.yaml` was not touched —
deliberately, per the scope ruling.

## The table (`src/manju/cli_workflows.py`)

10 task-oriented workflows, each `{title, when, steps: [(command, why)],
next, see_also}` — the real main flows, verified command-by-command against
the live registry and each command's own `--help`/docstring before writing:

| workflow | steps | covers |
|---|---|---|
| `new-project` | 6 | scaffold → creation funnel → import → check → dry-run → first build |
| `refine-shot` | 7 | status → mentions/impact preflight → director propose/confirm/run → check |
| `generate-takes` | 6 | dry-run cost → build → redo candidates → tasks ledger → board → select |
| `qc-repair` | 5 | qc → repair --auto → targeted --op → rebuild final → re-verify |
| `preview-final` | 6 | audition → animatic → proxy ladder → explain --cost → final → package |
| `captions-localization` | 7 | transcribe → align → voice preview/batch → locale add/status → build --lang |
| `deliver` | 6 | exports center → NLE exports → --manifest → --bundle → qc conformance → baseline |
| `series-episode` | 5 | series status → new-episode → sync-bible → continuity → characters |
| `recover` | 7 | status → tasks (unresolved) → attach-remote-job → abandon → resume build → rebuild-index → unlock guidance |
| `diagnose` | 6 | check → doctor → failures → events → toolchain --diff → support-bundle |

`next` is a pointer to another workflow key (or none) forming the natural
production ladder; `see_also` rows are command strings and are held to the
same honesty bar as steps. Placeholders use `<…>` so the resolver strips them.

## The command (`cli.py`, anchored after `toolchain`)

`manju help-workflow [name] [--json]` — read-only and **project-free** (it
never calls `_project()`, so it works before `manju new`).

- no name → list: every workflow's name, title and one-line 何时 when;
- with a name → the steps table: numbered commands, one-line why each, the
  `next` pointer and see-also rows;
- `--json` → machine-readable (`list_payload()` / `detail_payload()` — the
  exact dicts the tests compare against);
- unknown name → structured error, exit 1: human form lists every valid name;
  `--json` form is `{"error", "code": "unknown_workflow", "valid_workflows"}`
  (the stable `{"error","code"}` shape plus one additive field).

`cli.py`'s own diff is one ~50-line command block; all data and rendering
live in `cli_workflows.py`.

## Honesty enforcement (tests/test_fp_workflows.py, red-first)

The registry-resolution mechanism is **imported from
`tests/test_fp_docs.py`** (`_registry_view` + `_strip_placeholders` +
`_clean_tokens` + `_resolve_segment`) — the same machinery that keeps the
README command table honest, reused rather than duplicated (its README
parsing is not imported). Tests:

1. every command string in the table — **91 across steps + see_also** —
   resolves in the live typer registry (an aspirational command fails RED);
2. floor + shape: ≥ 8 workflows, every entry ≥ 2 steps, every why non-empty,
   every step starts with `manju `;
3. `next` pointers stay inside the table and never self-point;
4. list mode names every workflow and its when line, from a non-project cwd;
5. detail mode renders commands, whys, next, see_also;
6. `--json` list/detail round-trip equal to the payload functions;
7. unknown name → non-zero exit, human output lists all valid names, JSON
   output carries `code == "unknown_workflow"` + the sorted valid list.

Red-first evidence: the test file was written and run before the module
existed (`ModuleNotFoundError: No module named 'manju.cli_workflows'`), then
went 8/8 green after the implementation.

## Surface governance

`tests/fixtures/cli_surface.json` regenerated via
`python -m tests.test_fp_cli_snapshot`: 117 → 118 entries, exactly one added
row (`help-workflow`, no required params). Not exposed over MCP (this loop
adds no MCP tool); dangerous-command policy untouched.

## Targeted test runs

- `tests/test_fp_workflows.py` — 8 passed (after the recorded red)
- `tests/test_fp_cli_snapshot.py` + `tests/test_fp_docs.py` — 11 passed
- `tests/test_cli.py` — 20 passed

No existing test was edited or weakened. README/DECISIONS/CONTRACTS untouched
(orchestrator-owned); parallel loop I's qc/caption files untouched.
