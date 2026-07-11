# AI_IDE_20B — Completion

Slice: the post-15–19 corpus expansion. **0 production files** — everything under
`tests/`. No commit/push; DECISIONS.md/README.md untouched; the 21G agent's
namespace (`tests/test_c21g*`, `REPORTS/AI_IDE_21G*`) untouched.

## (a) Reviewer calibration (§5) — runner + pinned rates

`tests/fixtures/golden/calibration.py::run_calibration(root)` feeds all 18
committed visual cases through the **REAL 15 pipeline** — one project, one shot
per case, real `qc_brief` packets, both fake profiles filing **real
`record_verdicts` v2 intakes carrying §6 `dimension_observations`** (new opt-in
`build_verdict(row, include_dimensions=True)`), metrics computed from the
**stored records** (`read_v2_records`) and the real
`reviewer_agreement`/`drift_trend`/`repair_routes` surfaces. Output = returned
dict + derived `reports/calibration/calibration.json` inside the throwaway
project (deletable; runtime never reads it — grep-asserted).

Pinned rates (denominators explicit; the twin's **7 declared conflicts** are the
known-value denominators):

| metric | primary (`qc_vision_fake`) | twin (`qc_vision_fake_twin`) |
|---|---|---|
| per-dimension precision/recall (defect dims) | identity 1.0/1.0 · wardrobe 1.0/1.0 · prop 1.0/1.0 · lighting 1.0/1.0 · style 1.0/1.0 | recall: identity 0.0 · wardrobe 0.0 · lighting 0.0 · style 0.0 · **prop 1.0** (unconflicted) |
| false-blocker rate | 0/17 = 0.0 | 0/17 = 0.0 |
| missed-blocker rate | 0/1 = 0.0 | **1/1 = 1.0** (identity blocker missed) |
| unknown rate | 3/18 (the 3 ambiguous cases, honestly) | 3/18 |
| overconfident-on-ambiguous | 0/3 | 2/3 |
| disagreement rate | — | **7/18** (= the declared conflicts, id-exact) |
| blocker-disagreement shots | — | `S008` (identity mismatch) via real `UNKNOWN_REVIEWER_DISAGREEMENT` |
| cost/latency | honest zeros + note (in-process fake) | same |
| profile digest | distinct | distinct |

Honesty framing everywhere (report `subject_note` + tests): the subjects are the
FAKE profiles — rates prove the **harness**, not any real model; the corpus is
the annotated set. Thresholds never write back (`policy_writeback: NEVER`;
`src/manju` has no reference to calibration/cards/golden corpus — tested with
load-bearing needles). Drift surface pin: `drift_dimensions == ["prop_product"]`
— the real latest-record-per-dimension rule (twin files after primary, so only
the dimension both call mismatch survives as drift); 1 route proposed from the
7-route vocabulary.

## (b) Provider regression cards (§6)

`tests/fixtures/golden/provider_cards.py::derive_card(project, provider_id,
capability)` — a pure tests-side view (the permissible production touch was NOT
needed) over 14's machinery: `declared_facts` + stored `read_report` evidence →
the same pure `qualification_state`. Card fields: `qualification{state, level,
stale, blocked_reason, reasons}` · `tested{capability, mode, transport,
fixture_version, evidence_refs}` · `known_unsupported_or_untested`
(declared-but-untested capabilities, never claimed) · `error_semantics{declared
(content_rejected_when + definite_rejection_statuses), observed_submission_states
(from evidence_refs[].to)}` · `cost_latency{estimated, observed
(evidence.cost.actual), latency_ms: None + honest note}` ·
`artifact_probe{artifact, checks}` · `digests{profile/adapter/request/
response_schema}` · `derived_from{report_present, deletable note}`.
**No aggregate score** — `assert_no_score()` walks every key against a forbidden
list (score/rank/best/…); tested red and green. Proven behaviors: scripted-canary
card (CANARY_ARTIFACT_PASSED, states PREPARED→…→TERMINAL_SUCCESS, cost 0.1),
stale flip on profile-digest move, config-floor without a report, deletable
report ⇒ honest floor.

## (c) New corpus partitions (manifest 20B.v1 — 77 cases total)

| partition | cases | kinds |
|---|---:|---|
| visual_continuity (20A) | 18 | committed_image (sha256-pinned) |
| technical_media (20A) | 13 | generated_media (probe facts) |
| **prompt_director** | 10 | 8 declared_input (live-run: multi-beat ×2, future leak, ref contamination ×2, camera/action motion, endpoint-missing, clean negative) + 1 live_intake (§9.3 one-variable via REAL record_verdicts reject) + 1 indexed_evidence (continuation-restart → honest "no such check code"; nearest = 3 CONTINUATION_* gate codes) |
| **delivery_policy** | 11 | 6 live_pure (platform checks incl. DISCLOSURE_REVIEW PENDING_HUMAN wording, credential leak, format-only, cutdown codes, reframe statuses, voice provenance) + wordlist fixture case (annotate-only) + 3 indexed_evidence (stems/M&E+textless gap, bundle TOCTOU, NLE mapping) + 1 honest_gap (no codec/loudness-vs-profile check) |
| **provider_runtime** | 9 | full §3 fault list: 2 live_injection (preflight reject → NOT_DISPATCHED with 0 transport calls; post-send timeout → OUTCOME_UNKNOWN with exactly 1 attempt) + 7 indexed_evidence (remote reject, pre-send timeout, poll transient, download corruption, idempotency/reconcile, cancel-may-continue, malformed evidence) |
| **story_skill** | 4 | skill_eval for the four first-party skills via the REAL `core.skills` loader: required 输入/输出/失败条件/纪律 sections, boundary phrases, forbidden `--yes`, review-skill vocabulary == code `DISPOSITIONS`, continue-skill names the 3 gate codes, localize data-pack carries `{text, base_hash}` |
| **cross_platform** | 12 | contract_index: 8 EXECUTED (CJK paths, rename, BuildLock incl. mid-acquire race, CLI JSON compat, core-no-SDK, imports read-only, SQLite rebuild, reports-not-inputs), 1 SKIPPED_WITH_EVIDENCE (Windows/macOS — ci.yml is ubuntu-latest only, recorded as CI-matrix future), 1 PARTIAL (ffmpeg/font pinned in CI; tz/locale unpinned), 1 HONEST_GAP (no long-path/MAX_PATH pin), 1 HUMAN_EVIDENCE_REQUIRED (NLE opens in real app) |

New committed fixture: `tests/fixtures/golden/policy/brand_claims_wordlist.v1.yaml`
(self-made synthetic claim/brand terms; `disposition: advisory_only`,
`final_judgment: human`). It drives `qc.roughcut.rough_cut_proposal
(sensitive_terms=…)` — every hit is `action: "annotate"`, `default_action:
annotate`, `reversible: true`, deterministic; the wordlist never becomes a core
hard rule (contract §7/§9: 法律判断归人 — wording asserted).

## (d) Cross-platform contract subset: executed vs skipped

Executed here (evidence-indexed, freshness-tested): CJK paths · rename ·
BuildLock (incl. `test_fresh_corrupt_lock_is_not_stolen` mid-acquire race) · CLI
JSON schema compat · core-no-SDK/LLM (indexed pins + a NEW whole-`src/manju`
grep test in c20b) · imports read-only · SQLite rebuild · reports-not-inputs.
Skipped with evidence: Windows/macOS rows (CI has no OS matrix — quoted from
`.github/workflows/ci.yml`, asserted in a test). Honest gaps recorded: long-path
limits unpinned; tz/locale variance unpinned. Human evidence: NLE-opens-in-app.

The corpus INDEXES existing tests instead of duplicating them, and
`test_every_evidence_ref_in_the_whole_corpus_is_fresh` verifies every one of the
40+ `tests/file.py::test_fn` refs still resolves to a real `def test_fn(` — the
index cannot rot silently.

## (e) Files + test counts (0 production files)

```
M tests/fixtures/golden/fake_reviewer.py    (+§6 dimension_observation, opt-in emit; 20A behavior byte-identical by default)
M tests/fixtures/golden/manifest.json       (20A.v1 → 20B.v1: +46 cases, +5 partitions, +4 design notes)
M tests/test_c20a_corpus.py                 (2 pins re-scoped for the evolving manifest)
A tests/fixtures/golden/calibration.py      (§5 runner)
A tests/fixtures/golden/provider_cards.py   (§6 cards)
A tests/fixtures/golden/policy/brand_claims_wordlist.v1.yaml
A tests/test_c20b_corpus.py                 (49 tests)
```
Committed golden tree: 28 files, 145,294 B (< 1 MB budget). Generated media
still test-time-only.

## (f) Suite results

- `tests/test_c20b_corpus.py`: **49 passed**.
- Neighbors (c20a + c20b + c15 ×2 + c14 + c081012 ×3 + promptlab + dr06 + dr02):
  **269 passed**.
- **Full suite: 3114 passed, 13 skipped, 0 failed** (= 3127 collected), run
  synchronously in three foreground chunks (a–e: 1471p/6s in 426s · f–o:
  653p/7s in 242s · p–z: 990p in 293s). Skips are pre-existing
  network/live-probe/ffmpeg guards.

## (g) Deviations / notes

- **Two 20A pins re-scoped** (not weakened): manifest version pin now accepts
  the contract's own 20A→20B evolution; the every-case-has-observations rule now
  applies where it is meaningful (media partitions) while 20B kinds carry their
  own expectation fields — enforced in test_c20b.
- **Honest non-implementations recorded instead of invented checks**:
  continuation-restart (no such check code), codec/loudness-vs-profile platform
  checks (don't exist), TEXTLESS_MASTER (role recognised, never produced),
  brand/claims wordlist (no src implementation — the corpus fixture drives the
  real annotate-only roughcut path instead), long-path pin (absent). Each is a
  corpus row with `honest_gap`/note, per contract §1 (明确已知限制) and §9 stop
  rules (no fake compliance conclusions).
- **Calibration drift pin captures real 15 semantics**: latest-record-per-
  dimension means the twin's later "match" verdicts supersede primary mismatches
  except prop (both agree) — pinned as `drift_dimensions == ["prop_product"]`.
- **§8 two paths (documented, both already real, cited not rebuilt)**: default =
  fully offline deterministic fakes (scripted `Canned` transport for canaries,
  fake reviewer/twin for reviews, lavfi bad media); optional real canary =
  operator-run `manju providers qualify --run` via AI_IDE_14 (cost-bounded,
  confirm-gated; PRODUCTION_READY reachable only with `transport == "real"`).
  Both share the same admission/evidence path (dr06), so fake-path green is
  meaningful for the real path.
- **§9 stop conditions**: no model downloads; no annotation→training loop; core
  CI offline (all fakes); no copyrighted media/prompt corpora (everything
  self-made synthetic, licensed in-manifest); no single aggregate score anywhere
  (cards assert it, calibration reports per-dimension only); legal/platform
  wording stays advisory + human-final (asserted); CI needs no real cloud
  account or desktop NLE (scripted transports; NLE row = human evidence).

## (h) REPORTS paths

- `REPORTS/AI_IDE_20B_BASELINE.md`
- `REPORTS/AI_IDE_20B_COMPLETION.md`
