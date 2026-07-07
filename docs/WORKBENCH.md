# WORKBENCH.md — what "complete workbench-level capability" means (round S §4)

The rule: **every project capability a human needs day-to-day is reachable in
`manju gui` with one obvious affordance; everything the CLI can do that the
GUI deliberately cannot is listed here with its reason.** The GUI is a veneer
over the same core the CLI calls — no capability exists only in the GUI.

## Deliberately CLI-only (containment, §5 — the GUI must NEVER grow these)

| Capability | Why not in the GUI |
| --- | --- |
| `unlock` | interactive-tty-only by design; a browser click is too cheap for breaking a seal |
| `gc --hard` | destructive; tty-only |
| `pack` / `unpack` | filesystem-level import/export of whole projects; shell territory |
| raw `git` beyond gitops panel (rebase, push) | git is the patch engine; plumbing stays in the shell |
| `serve-mcp`, `auto`, `watch` | process-management; started from a terminal next to the GUI |

## The capability matrix (CLI ↔ GUI affordance)

| Capability | CLI | GUI affordance (round; ✱ = round S) |
| --- | --- | --- |
| Project status / next step | `status` | header + 项目 panel (Q) / state poll (R) |
| Shots: view, states, why-stale | `status`, `explain` | shot cards + why-stale chips (R) |
| Shot fields editing | edit YAML | keystroke-validated editor (R17) |
| Shot order | edit index.yaml | ✱ reorder controls (S8a) |
| Takes: preview/compare/select/rollback | `select`, `rollback shot` | cards + A/B compare (Q/R) |
| Director notes / verdicts | edit YAML | 好/弃 chips + notes (R) |
| Generate: build/redo/voice | `build`, `redo`, `voice` | job buttons (R) |
| **Pre-generation explanation** | `build --dry-run`, `explain` | ✱ plan modal before every priced run: shots, providers, why, cost (S8a) |
| Spend gate | `--yes` | confirm dialog (R, live) |
| Batch operations | ✱ `redo --shots/--all-stale`, `voice --all` (S-E) | ✱ multi-select + bulk bar (S8a) |
| QC run + findings | `qc` | QC panel (Q/R) |
| **Human visual review** | `board` | ✱ review page: sequential shot review, frames + takes, 好/弃/redo/note, keyboard (S8b) |
| Repair ops | `repair --op …` | ✱ per-finding repair buttons on review page (S8b) |
| Providers: list/add/check | ✱ `providers …` (S-A) | ✱ providers page: status, doctor probes, add-from-template (S8b) |
| Routing strategies | ✱ `route …` (S-A) | ✱ read-only routing view + strategy picker (S8b) |
| Asset library | ✱ `lib …` (S-D) | ✱ library page: browse/preview/use-into-project (S8b) |
| Version compare | ✱ `compare` (S-D) | ✱ finals diff page: side-by-side + per-shot change map (S8b) |
| History / snapshot / rollback file | `history`, `snapshot`, `rollback` | git panel (R) + ✱ snapshot/rollback buttons (S8a) |
| Exports / package | `export`, `package` | export action (Q) + ✱ packaging editor lite (S8a) |
| Spend / tasks | `spend`, `tasks` | spend view (R) + ✱ tasks view (S8a) |
| Doctor | `doctor` | /api/doctor (R) + ✱ doctor page w/ fix hints (S8b) |
| **Failure clarity** | ✱ structured failures everywhere (S-C) | ✱ failure cards: step, cause, hint, log link, retry (S8a) |
| **First-run guidance** | — (README) | ✱ onboarding checklist: 建项目→写剧本→生成→审片→导出, dismissable, per-user state (S8a) |
| Bible view/edit | edit YAML | bible panel read-only (Q) → ✱ guarded edit w/ lock awareness (S8a, locked fields read-only) |
| Rules / packaging YAML | edit files | ✱ raw-YAML editors with validate-on-save (S8a) |
| New project / presets | `new --preset` | ✱ new-project dialog in workspace switcher (S8a) |

## Interaction invariants (every ✱ feature obeys)

1. Same core functions as the CLI; every mutation records the same event.
2. Locks respected: locked fields render read-only with the 🔒 and a
   propose-instead hint; the GUI never writes through a lock.
3. Mutations hold the in-process lock; builds/redos the cross-process lock.
4. Priced actions surface the plan first (§8.3 + item 5): dry-run → modal →
   explicit confirm; never a silent spend.
5. Failures render as structured cards (step/cause/hint/log), never toasts
   with bare exception text (item 10).
6. Human assets and human decisions are never overwritten silently — batch
   ops skip locked/manual shots and say so.
