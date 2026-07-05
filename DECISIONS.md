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
