# AI_IDE_08_10_12C — WP0 Baseline Audit (HEAD 5a7aa28)

Branch `claude/cost-optimization-strategy-cjfmn5`. Audit of the accepted-take
production loop's existing owners, per the contract §5.1 list. Statuses:
ALREADY_IMPLEMENTED / PARTIAL / MISSING / REJECTED_WITH_REASON / SKIPPED_WITH_EVIDENCE.

## Audit table

| # | Capability | Status | Evidence (file:line) |
|---|---|---|---|
| 1 | ShotSpec fields + checked writes | ALREADY_IMPLEMENTED | `core/models.py:226-252` (ShotSpec, `extra="allow"` at :34); checked writes `core/writes.py`; CAS/locks `core/locks.py` |
| 2 | Bible identity/style/location/prop | ALREADY_IMPLEMENTED | `core/models.py:419` (BibleEntry), bible/*.yaml, `_bible_excerpt` in `core/spec.py` |
| 3 | quality.must_show / avoid | ALREADY_IMPLEMENTED | `core/models.py:139-141`; compiled to ExpectationSet by `qc/expectations.py:74` |
| 4 | continuity locks / prev / transitions | PARTIAL | `core/models.py:134-136` (`prev`, `locks`); rules-level transitions `TransitionSpec:455`. No allowed-change field — not required by this batch (no red fixture needs it) |
| 5 | prompt workbench four prompts | ALREADY_IMPLEMENTED | `providers/prompt.py:179-270` (video/image/negative/director); bundle `build/promptlab.py:146` |
| 6 | prompt deterministic checks | PARTIAL | `qc/prompt_checks.py` (3 codes: too_many_actions, too_many_motion_paths, excessive_duration). Contract production codes (CLIP_SCOPE_*, REFERENCE_*, CONTINUATION_*, SURFACE_PROFILE_*, PROMPT_BUDGET_EXCEEDED) MISSING → WP2 |
| 7 | refs declaration/resolution/lineage/budget | PARTIAL | 4-tier resolver `providers/refs.py:157-254` + lineage; budget `providers/refbudget.py`. NO transfer fields (`controls`/`ignore`/`subject_ref`) — dict-form binding entries are not parseable today (red fixture 2) → WP2 §7.4 additive |
| 8 | GenerationRequest / effective request | ALREADY_IMPLEMENTED | `providers/base.py:176` (GenerationRequest); effective submit via manifests/generic_cloud |
| 9 | Provider profile/preflight/request digest | ALREADY_IMPLEMENTED | `providers/catalog.py:232-290` (profile_id/digests); `providers/preflight.py:182` (check_request_compatibility, tri-state+UNKNOWN_LEGACY); `providers/submission.py:294-372` (identity+request_digest) |
| 10 | selected_take + take sidecars | ALREADY_IMPLEMENTED | `core/models.py:172` (ShotStatus.selected_take), `:324-382` (TakeSidecar: provider/spec_hash/spec_snapshot/params/remote/compiled_prompt/probe/source_in_out/spec_version) |
| 11 | candidate_index/seed/request/submission correlation | PARTIAL | Attempt evidence carries request_digest+seed+candidate count+outputs sha256 (`build/attempts.py:289-303`, registry `:369-373`); joinable take→attempt via output sha. BUT `manju redo` emits NO attempt evidence (`providers/registry.py:234-241` — evidence None outside a run) and NO lineage field exists on the sidecar (red fixture 4) → WP3 additive `redo_of` |
| 12 | 03C attempts + 06 submission evidence | ALREADY_IMPLEMENTED | `build/attempts.py:339` (append_attempt), `:813` (read_attempts), `:398` (submission events, hash chain); P0 A1/A2 durable admission + run lifecycle |
| 13 | 02 packet/verdict/assurance/repair proposal | ALREADY_IMPLEMENTED | `qc/agent_review.py:945-1176` (v2 intake, payload-invalid⇒zero-write, dup-id reject), `qc/assurance.py` (8 states, tri-state QC, fail-closed — P0 64c364c), repair_proposal `:244` |
| 14 | consistency brief/contact sheet/coverage | ALREADY_IMPLEMENTED | `qc/agent_review.py:311` (qc_brief), `_qc_brief_consistency:757`, qc_coverage `:1547` |
| 15 | ingest/import/private library | ALREADY_IMPLEMENTED | `build/ingest.py` (dry-run/review/confirm/flag/discard, dedupe by content hash), `core/library.py`; cli `import_:402`, `ingest:597` |
| 16 | select/rollback/repair/redo | ALREADY_IMPLEMENTED | cli `select:1083`, `redo:992`, `repair:1703`; `build/graph.py:1738` (redo_shot, spend-gated, append-only) |
| 17 | Director suggest/propose/confirm/run | ALREADY_IMPLEMENTED | `build/director.py:564` (propose), `:611` (confirm), `:666` (execute), suggest + `_assurance_suggestions` (DR02 WP4) |
| 18 | Skill front matter/examples/evals | ALREADY_IMPLEMENTED | `core/skills.py:15-158` (anthropics/skills front-matter convention, bundled+user scan); 14 skills under `skills/` |
| 19 | board take comparison | ALREADY_IMPLEMENTED | `board/board.py:254-264` (side-by-side compare grid, synced playback), per-take verdict badges `:57` |

## Red fixtures constructed (contract §5.2 → test files)

1. Multi-action + future-reveal shot → `tests/test_c081012_prompt_checks.py` (CLIP_SCOPE_*)
2. Identity ref with undeclarable ignores → same file (REFERENCE_*; dict binding red)
3. One request → N candidates → `tests/test_c081012_candidates.py` (family via attempt evidence)
4. Explicit redo, same creative family → same file (RED: no lineage anywhere → sidecar `redo_of`)
5. Take needing post-only fix; 6. reroll vs rewrite → `tests/test_c081012_review.py` (five dispositions)
7. Same-name byte replacement → stale (existing DR02 proof reused + decision-stale test)
8. Continuation from authored ending ≠ real ending → `tests/test_c081012_continuation.py`
9. External candidates via ingest → covered by existing ingest suite (reuse, cited in completion)
10. Surface rules unknown/stale → `tests/test_c081012_prompt_checks.py` (SURFACE_PROFILE_*)

## Public-schema admission (§5.3)

| Candidate | Decision |
|---|---|
| Directing brief | REJECTED as runtime schema — Skill temp product; enforcement: reports/ is never read by prompt/request compilation (proof tests §13.1) |
| Clip contract | REJECTED — effective decisions land in Shot source via existing patch/proposal |
| Surface profile | internal source-dated py-literal data in the prompt-checks module; digest in compiler_trace |
| Reference transfer | additive fields on the EXISTING ref binding (params/shot refs entries), in-digest via generation.params |
| Candidate set | derived view (sidecar+attempt-evidence join), rebuildable, never build input |
| Recipe snapshot | already existing sidecar (params/compiled_prompt/spec_snapshot) + attempt evidence — no new store |
| Accepted state | derived view over assurance + bound endpoint observations |
| Continuation capsule | derived view for Skill input; never build input |
| Production decision | **Path A** — verdict v2 optional additive fields (`decision`, `observed_states`); 0 new public schemas |

## Hard-constraint check
- No second ShotSpec/GenerationRequest/prompt store/QC/registry/DB/queue: PASS (all extensions additive on existing owners).
- Board candidate-family UI: contract 12.3 conditional trigger is "board lacks a real comparison entry" — board HAS one (`board.py:254`), so the UI addition is NOT triggered; family data is exposed through the qc/status JSON views instead. SKIPPED_WITH_EVIDENCE.
- Exports status/compare/verification modules: owned by a parallel agent this cycle — untouched by this work package.
