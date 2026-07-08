# DECISIONS.md — approved deviations from the v2.2 design

Reviewed and approved in review round 1 (§3). Each entry states the design
text, what was actually built, and why the deviation stands.

## 1. JianYing dual path = self-developed skeleton ∥ pyJianYingDraft

Design (v2.2 §13 M1, decision 8) named pyJianYingDraft primary and
capcut-cli secondary. As built, the dual path is: a **self-developed,
diff-stable draft skeleton** (deterministic uuid5 ids, own lint,
`exports/jianying/<name>/draft_content.json`) **in parallel with** the
**pyJianYingDraft native draft** (`exports/jianying/<name>_native/`). Both are
emitted on every JianYing export; either can be opened/validated
independently, and final.mp4 + SRT + OTIO remain the fallback exits (§14).
Rationale: the skeleton is version-drift-immune and diffable in git; the
native draft is app-faithful. Two implementations that cross-check beat one.

## 2. capcut-cli is demoted to lint

Design listed capcut-cli as the secondary draft *path*. As built, capcut-cli
is a **lint-only adapter** behind the adapter wall
(`exporters/native_draft.py: capcut_cli_lint`): when the binary exists it
lints the exported draft; when absent (as in the build environment — it is a
Node tool) the export falls back to our own `lint_draft` and reports the
degradation as a note, never a failure. Rationale: draft *generation* is
already dual-pathed (deviation 1); a third generator adds risk without
coverage, while its existing lint capability is the part worth keeping (v2.2
decision 8: lint 用现成能力,不自研).

## 3. HyperFrames follows the idea but is self-developed with Chromium

Design (§2.5 P1) named HyperFrames as the html_render provider. As built, the
HTML→deterministic-MP4 idea is implemented **in-repo against headless
Chromium** (`media/html_card.py`: styled HTML → screenshot at exact project
resolution → looped MP4), with zero new Python dependencies; the caption-card
provider prefers it and records the renderer in the take's lineage; drawtext
remains the zero-dependency floor (§8.4). Rationale: P1 semantics are "有现成
实现就装,没有就走自研保底" — HyperFrames itself was not installable in the
target environment, Chromium was; the adapter wall keeps a future swap to the
real HyperFrames a provider-level change.

## 4. `auto` and `board` were delivered early

Design scheduled `manju auto` as a later thin wrapper (v2.2 §10 "后续再加")
and `manju board` under M4. Both shipped early: `board` because it is the
review surface every other milestone's acceptance leaned on; `auto` because
the M2 CLI pass made the thin `claude -p` shell (with `MANJU_ACTOR=ai` and
the SKILL.md playbook prepended) a few dozen lines. No architectural debt:
both sit strictly on the public CLI/core surface.

## 5. `board --serve`: an actionable localhost board (owner-directed, 2026-07-06)

Design §1-⑦ deliberately rejected a web UI ("static board covers ~80% of a
GUI's value at ~1/20th the cost"). The project owner explicitly requested an
actionable GUI / visual workspace for personal use. As built, the deviation
is bounded: the static board stays the default and byte-identical; `--serve`
starts a stdlib-only localhost server whose seven actions are a THIN veneer
over the exact core functions the CLI calls (select/redo/build/qc/package/
snapshot/rollback_shot), every mutation records the same event, and the
dangerous surface (unlock/gc/pack) is unreachable from the browser — the
same containment rule as the MCP server. No new dependencies, no auth
(127.0.0.1 binding; personal use per the owner), Range-guarded media
streaming with path-traversal rejection.

## 6. `manju auto` drives any one-shot agent CLI (2026-07-06)

v2.2 §10 described `auto` as a thin shell over `claude -p`. The owner asked
for the productization flow to be agent-neutral. As built: an agent-command
resolver (flag → MANJU_AGENT → project.yaml:agent → PATH probe over
claude/codex/gemini/qwen/aider) with a `{prompt}` template grammar; the
playbook prepend, actor=ai logging and exit-code propagation are unchanged.
Claude Code remains merely the first PATH-probe default. MCP stays the
recommended structured integration.

## 7. Preset kits reversed to three neutral frames (owner-directed, 2026-07-06)

Round O shipped 13 content-type kits (comic/short_drama/…) under the P3
"presets" goal. The owner reversed this: content-type inference belongs to
the agent reading the input, not to a preset — kits now are exactly `blank`,
`vertical_ai_video` (1080×1920@30) and `horizontal_ai_video` (1920×1080@24),
fixing only the frame and generic technical defaults (safe-area captions,
ducking, srt). The preset MACHINERY is unchanged; a neutrality guard test
bans genre tokens from kit data so flavor cannot creep back; `--preset
blank` is pinned byte-for-byte to the generic scaffold. This supersedes the
round-O kit list; the playbook (SKILL.md) carries the genre workflows
instead.

<!-- From the parallel R-line (merged in round R): -->

## 5. The GUI ships now, as a pure client (§1-⑦ deferral revisited)

Design (§1-⑦) postponed a workbench GUI indefinitely: the static review board
covered ~80% of the value at ~1/20 of the cost. As built (user-directed
reversal of the *timing*, not the *architecture*): `manju gui` is a local web
workbench implemented as the **third client of the unchanged engine core** —
the same functions the CLI and MCP call, zero new dependencies (stdlib
`http.server`), truth still in text files, jobs in-memory only. The original
rationale for deferral was cost and the risk of a second source of truth; the
first is paid deliberately (the user asked), the second is structurally
avoided. The MCP dangerous-surface rule carries over verbatim: no `unlock`,
no `gc --hard`, no `pack`/`unpack`, no arbitrary-path `import` over HTTP (§5,
§11). The static board remains for offline/share use.

## 6. ask_before becomes an engine gate, not just agent discipline (§8.3)

Design (§8.3, §10) made the agent's discipline the first spending gate:
SKILL.md orders a stop-and-ask when a plan hits `ask_before`, and the engine
only enforced the budget ceiling (事中熔断). As built, the engine now also
enforces the first gate itself: a non-dry-run whose plan estimates cost > 0
while `expensive_generation` is in `ask_before` returns
`ok=false, waiting_user=true` unless the caller passed an explicit
`assume_yes` (`--yes` on the CLI, `assume_yes` on MCP/GUI). Uniform across
actors: the human's yes is one flag away; the agent's yes must still come
from the human (SKILL.md §5 unchanged — the engine just stops trusting that
discipline alone). Dry-run estimates are never gated, so "问前先 dry-run"
stays frictionless. Rationale: a discipline-only gate silently degrades as
agents and surfaces multiply; the cheapest honest place to stop money is the
single code path every surface already funnels through.

## 8. Round W (external 10-round review): meta-tier decisions

- **#83 project-supplied agent commands refused**: `project.yaml:agent` now
  accepts only KNOWN agent names (claude/codex/gemini/qwen/aider); free-form
  command templates remain available via `--agent`/`MANJU_AGENT` (machine
  trust). A downloaded project must not name an arbitrary local command.
  `local_cmd` providers were already machine-level (~/.manju/providers).
- **#84 dependency constraints**: constraints.txt pins the core runtime deps;
  CI installs with `-c constraints.txt`. Refresh deliberately (bump + full
  suite), not by drift.
- **#82 `_fail` JSON detection**: the frame-walk stays (call-site-free), now
  backed by an argv `--json` fallback so the structured-error contract cannot
  silently regress under refactoring.
- **#85 large-file refactor deferred**: cli.py / build/graph.py /
  media/render.py sizes are real maintenance debt; splitting them is
  deliberately NOT bundled into a correctness round (too much churn alongside
  85 behavioral fixes). Recorded here as standing debt for a dedicated round.
