# POST_COMPLETION_HARDENING Completion

## Baseline
- branch `claude/cost-optimization-strategy-cjfmn5`, base `b34f186`, clean.
- Baseline full suite: 2727 passed / 12 skipped / 0 failed.
- Landed as two commits: `cbb4a0d` (H2: WP4+WP5) and `fc43451` (H1: WP1–WP3, WP6).
- Final full-suite result: **0 failed / 2768 passed / 12 skipped** (831.01s, clean committed tree at fc43451) — +41 over the intake baseline = the 23 H1 tests (21 hardening + 2 gate) + the 18 H2 tests; zero unexplained failures

## Proven gaps → fixes (claim-by-claim table in the baseline report)

## Paid recovery (WP1)
- `RuntimeState.ensure_submission_projection(project, shot=, provider=)`:
  intents-first (cheap), else folds evidence via the SAME P0 `project_chain`
  restore (terminal chains never resurrected; scope-filtered; sentinel for
  no-valid-prefix); any read/verify failure → `recovery_unavailable` dict —
  never an empty set. Consumed by the paid consult BEFORE the submissions
  query and by the 07C gate. No second ledger, no second algorithm.
- Transport-count proof: delete `.manju` → direct paid generate → **0**
  transport calls, structured fail-closed (auto-guard, no explicit rebuild —
  `test_c0911_gates.py::b15`); explicit rebuild and auto-guard yield the same
  classification; unrelated shot/provider unblocked; §3.3 forbidden shortcuts
  all pinned absent.

## Release gate (WP2)
- `_submission_blockers`: evidence probe first (a never-ran-paid project stays
  ledger-free and ungated); evidence unreadable / consult failure →
  `SUBMISSION_RECOVERY_UNAVAILABLE` blocking; unresolved →
  `SUBMISSION_OUTCOME_UNKNOWN` / `ATTEMPT_EVIDENCE_CORRUPT` per chain verify.
- Baseline log tri-state ABSENT/READABLE/UNREADABLE; unreadable →
  `BASELINE_EVIDENCE_UNAVAILABLE` (blocking); malformed-only → CORRUPT; never
  conflated with NO_BASELINE.
- Every baseline event self-verified on read (`recomputed event_id ==
  stored`); mismatch → CORRUPT.
- Append lock: `verifications.jsonl` writes (baseline approval AND
  `mark_verified`) ride the ONE coordinator (`core/events.py
  append_jsonl_line(file_name=, lock_name=)`) under `verifications.lock`;
  the failure rollback truncates to an fstat-under-lock anchor, so it can
  never clobber a concurrent writer's committed line (interleaved-writer
  test: every line intact).
- Run gate matrix: COMPLETED / COMPLETED_WITH_WARNINGS pass; INCOMPLETE /
  NOT_FOUND / FAILED / CANCELED / WAITING_USER / not-proven-success block.
  No-run_id sub-item: SKIPPED_WITH_EVIDENCE (no new-style sidecar marker
  exists; adding one is an out-of-budget schema change; run_id-less finals
  remain the legacy/manual first-release path, human risk-acceptance already
  required whenever any blocker stands).

## Identity and legacy jobs (WP3)
- Cloud submits: prompt-compile / ref-resolution / ref-hash failures →
  `submission_identity_unavailable`, NOT_DISPATCHED, transport 0 — an
  identity is never silently weaker than declared. Local providers keep
  best-effort (per-kind, no global flag).
- Legacy pending rows: digest correlation → poll-only resume; no digest →
  `LEGACY_PENDING_CORRELATION_UNKNOWN` (attach/abandon, automatic_resubmit
  false). Deliberate DR06-compat flip, pinned twice with in-file rationale.

## Accepted-take loop (WP4)
- keeper=true ⇔ v2 KEEP decision whose media/spec/expectation binding is
  CURRENT (single `_live_failures` checker); stale →
  `historical_disposition` + `binding_status:"stale"` + keeper:false;
  history never deleted.
- Continuation derivation failure → `CONTINUATION_CHECK_UNAVAILABLE`
  (blocking semantics when `continuity.prev` exists; continuation-free shots
  unchanged).
- `redo_of` cycles: one family under the stable min-take-name canonical
  representative + `REDO_LINEAGE_CYCLE` diagnostic; sidecars untouched.

## Delivery (WP5)
- NLE dedupe: same path + same hash packed once; disagreement → blocking
  `NLE_ARTIFACT_BINDING_MISMATCH` (build-time diagnostic + bundle guard).
- Bundle CAS: every member re-verified (sha256+bytes) against the manifest
  before any zip byte; drift → `DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST`,
  old ZIP intact. SHA256SUMS ≡ embedded manifest ≡ zip bytes.
- Report-inert proof: the materialized-manifest disk-read in
  `_resolve_base_master_digest` is DELETED; base identity = explicit
  in-memory param or in-process recursive `build_manifest(base_profile)`
  with a `_base_chain` cycle/depth guard (`BASE_PROFILE_CYCLE` blocking,
  never a RecursionError); hand-edit/delete of materialized reports proven
  inert for variants.
- Semantic digest axes: video segments + voice/music/sfx/ambient audio
  clips + caption source text/speaker/timing + overlay identity/timing;
  excludes w/h/fps, encoding, caption layout, wall clock. An audio or
  subtitle change now moves the digest (breaks FORMAT_ONLY labeling).
- Metadata: first-class artifact row (sha/bytes/mime) whose bytes join
  `manifest_digest` — RECORDED REVERSAL of 13C's exclusion; resolve
  failure/escape blocks without echoing the absolute path; credential
  content never emitted (boolean + diagnostics only).
- Strictness: explicit unknown profile_id / variant_kind → error;
  absent-config MASTER compat pinned; FORMAT_ONLY/LOCALIZED without a
  derivable base → blocking `BASE_IDENTITY_MISSING`. `final_ref` on the
  delivery surface REMOVED (Option B — manifest targets the current/newest
  final only; 07C's approval keeps honoring explicit finals, pinned).

## 09/11 evidence claims (WP6)
- Report wording corrected in both `REPORTS/AI_IDE_09_11G_*.md`: the limiter
  proof is a per-process/per-build cap; B1 is CAS on the SAME pre-existing
  submission_id; rebuild-based classifications say "after explicit rebuild".
- New gates: b14 (two FULL subprocess paid generates → exactly one transport
  submit across both) and b15 (delete-`.manju`, no rebuild → auto-guard
  fail-closed). Lease/fencing remains REJECTED_WITH_REASON, now stronger.

## Tests
| Suite | Result |
|---|---|
| `test_post_completion_hardening.py` (H1, red-first 19F→) | 21 passed |
| `test_h2_hardening.py` (H2, red-first 18F→) | 18 passed |
| `test_c0911_gates.py` (+b14/b15) | 28 passed |
| Targeted rings (both tracks) | 143 + 199 + 218 + 126 passed (orchestrator + agent reruns) |
| Full suite | 0 failed / 2768 passed / 12 skipped |

Pins flipped (5 total, each with in-file rationale): legacy-pending
characterization ×2 (H1); materialized-base fixture, unknown-profile compat
half, format-only-unverified severity (H2).

## Architecture proof
- No parallel stores/schemas/runtimes: 0 new public schemas; the WP1 helper
  is a method on the EXISTING RuntimeState reusing the EXISTING restore; the
  locked-append generalization is two additive kwargs on the EXISTING
  coordinator; every new string is an additive status/blocker/diagnostic code
  on an existing envelope.
- No local model / ComfyUI / queue / lease / new database (§10 forbidden list
  grep-clean).

## Remaining risks
- The evidence probe in the release gate reads the full submission-event
  stream on projects WITH paid history; acceptable at single-operator scale
  (correctness-first per contract §3.2), revisit only with a proven perf red.
- Zip byte-determinism depends on the fixed-timestamp writer; if a future
  Python zipfile change breaks it, the checksums/entry-order stability tests
  are the tripwire.
- `_submission_blockers` may create the disposable `state.sqlite` when
  projecting recovery for evidence-bearing projects (never for clean ones) —
  documented; the ledger remains deletable/rebuildable.
