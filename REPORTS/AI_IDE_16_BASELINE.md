# AI_IDE_16 BASELINE — Preview Ladder, Previs & Storyboard Round-trip (WP0 audit)

Date: 2026-07-11
Repo: `utdse15-byte/test` (Manju), branch `claude/cost-optimization-strategy-cjfmn5`
Orient: HEAD = `8f5b250 AI_IDE_15: cloud visual reviewer …`, `8b0f008 AI_IDE_20A`, `2fb939a AI_IDE_14`.

Controlling discipline (contract §0, addendum): the ladder is DERIVED state, never
stored; approvals ride EXISTING adoption facts (no second storyboard truth); preview
products are append-only media / derived views; the core calls no LLM/VLM; red-first;
SQLite-rebuildable. Old projects with no keyframe candidates stay byte-identical.

## 1. Existing owners to REUSE (audited — NOT duplicated)

| Contract need | Existing owner | Verdict |
|---|---|---|
| build targets (audition precedent) | `build/graph.run_build` `target in (proxy/final/exports/qc/audition)`; in-memory compile + `media/audition.render_audition` | **extend**: add `--target animatic` beside `audition` (same in-memory, never-write-timeline, derived-artifact shape) |
| kenburns pan/hold | `providers/kenburns.KenburnsProvider` + `media/kenburns.kenburns` | reuse as the animatic panel renderer |
| slate fallback | `media/audition.ensure_slate` | reuse for a keyframe-less animatic panel |
| final assembly + content key | `media/render.render_timeline` / `_write_key_sidecar` / `_audio_input_hashes` / `_enc_params` | reuse (animatic is content-keyed, deletable+rebuildable) |
| takes (image vs video) | `core/container.Project.takes` + `MEDIA_EXTS` (video > image priority; `.png/.jpg/.jpeg` are takes) | reuse: image takes = keyframe candidates, distinguishable by extension |
| candidate provenance | `qc/production.candidate_families` + take sidecars (request/ref/provider/cost, 08_10_12C) | reuse — keyframe candidates already carry provenance; no candidate store |
| adoption facts | `ShotStatus.selected_take`; refs binding `{ref,controls,ignore,subject_ref}` (`providers/refs`) | reuse — "approved" = selected keyframe take OR a promoted refs binding to the exact bytes |
| refs transfer vocabulary | `providers/refs.REF_TRANSFER_VOCAB` (has `style`,`color_grade`,`motion`) | reuse as-is — canonical style frame + motion ref need **no new vocabulary** |
| ref containment guard | `providers/refs.resolve_local_ref` | reuse — motion ref absolute/escape paths refused, bytes never read |
| spend gate sites | `build/graph.spend_gate` / the `_run_build_phases` ask_before gate; DR05 `mcp/policy` profile | reuse — keyframe gate rides beside ask_before, keyed on the DR05 profile |
| DR05 unattended profile | `mcp/policy` (`collaborative`/`unattended`); `mcp/tools.call_tool(profile)` | reuse — thread the live profile to the paid-video handlers |
| production checks / `prompt --check` | `qc/prompt_checks.production_checks` / `check_all` (joins `qc/production.continuation_checks`) | **extend**: join `KEYFRAME_NOT_ADOPTED` the same way |
| board payload | `board/board._render_shot` (per-shot HTML); static board is byte-pinned | **extend serve-mode only** (ladder chips + blocking SVG); static bytes untouched |
| next-step cost estimator | `build/graph._estimate_shot_cost` / `_target_duration_ms` | reuse for the §6 predicted video-layer cost |
| ShotDraftPackage (DR03A) | `build/shotpackage.build_shot_import_plan` / `apply_shot_import_plan` (zero-write inspect → CAS apply → rollback; whitelist map; create-only) | reuse — pull-sheet NEW rows map onto it |
| checked-write CAS | `core/writes.checked_shot_write` (`expected_text_hash` + lock guard) / `shot_text_hash` | reuse — pull-sheet EDIT rows are CAS proposals; locks/selected_take/media untouched |
| exporter pattern | `exporters/srt_ass` (pure compile → `atomic_write_text`); `exports_dir` | mirror for CSV/MD pull sheet |
| timeline transition source | `TimelineRules.transition_default`/`transition_overrides` (rules.yaml) → compiler → `VideoClip.transition_out` | reuse — a transition choice is ALREADY a timeline source fact (verify + pin) |

Conclusion: every §3–§9 need already has an owner. **No parallel schema, no second
storyboard truth, no free-node runtime, no 3D engine** is introduced.

## 2. Confirmed gaps (what AI_IDE_16 adds)

1. **§3 ladder view** — no derived stage projection existed. New pure
   `qc/production.ladder_view` over EXISTING facts (text/audition/keyframe/board/
   animatic/motion-ref/video). No store.
2. **§10 keyframe spend gate** — no keyframe-adoption gate existed. New
   `keyframe_adoption`/`keyframe_gate`; consumed at the build dispatch (unattended
   refusal, transport 0) + `KEYFRAME_NOT_ADOPTED` advisory in `prompt --check`.
3. **§5 auto-select boundary** — the build auto-selected a single take even when it
   was an IMAGE (keyframe) candidate. Contract §5 forbids auto-adopting a candidate;
   fixed to skip image takes.
4. **§6 animatic** — no `--target animatic`. Added, kenburns pan/hold from
   adopted/candidate keyframes over the existing timeline audio; derived artifact.
5. **§6/§7 board fields** — board showed no ladder stage / approval / next-step cost,
   and no 2D blocking. Added serve-mode ladder chips + a pure `blocking_svg`.
6. **§9 pull sheet** — no CSV/MD export and no round-trip import existed (no `csv`
   usage anywhere). Added `build/pullsheet` (export + import onto DR03A).

## 3. Environment / scope notes → SKIPPED_WITH_EVIDENCE

* **PDF pull sheet** — no headless-Chromium / PDF-table path in this environment and
  the addendum forbids adding one this batch. CSV + Markdown + existing JSON cover export.
* **Sound bridge / J-L cut audio model** — not expressible in the timeline model today
  (no field); AI_IDE_18 extends audio. This batch verifies + pins the transition-source
  path and that generative bridges are proposal-only (transport 0, AI_IDE_19 owns exec).

## 4. Planned file budget (≤8 production)

`qc/production.py`, `qc/prompt_checks.py`, `build/graph.py`, `build/director.py`,
`mcp/tools.py`, `board/board.py`, `build/pullsheet.py` (NEW), `cli.py`. Tests:
`tests/test_c16_ladder.py`, `_spend_gate.py`, `_animatic.py`, `_board.py`,
`_pullsheet.py`, `_transitions.py`. `DECISIONS.md`/`README.md`/`tests/fixtures/`/
`tests/golden/` untouched.

## 5. Baseline suite state

`python -m pytest tests/test_dr05_unattended.py tests/test_dr03a_shotpackage.py
tests/test_boards.py tests/test_ask_before.py -q` → green starting point for the
modules being extended.
