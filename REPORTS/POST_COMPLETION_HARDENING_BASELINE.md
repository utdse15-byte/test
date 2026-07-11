# POST_COMPLETION_HARDENING Baseline

WP0 for **POST_COMPLETION_HARDENING_V1** — an EXTERNAL review of the completed
01–06 P0 / 07C / 08_10_12C / 13C / 09_11G work, accepted after orchestrator
source-verification. Paired completion:
`REPORTS/POST_COMPLETION_HARDENING_COMPLETION.md`.

## Recorded first (§2)

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| HEAD at intake | `b34f186`, working tree CLEAN |
| Full-suite baseline | **2727 passed / 12 skipped / 0 failed** (clean-tree run at this HEAD) |
| Environment | Python 3.11, ffmpeg 6.1.1, pillow/httpx pip-installed (session env notes) |

## Claim-by-claim verdicts (16 WP0 reds)

Four claims were source-confirmed by the orchestrator BEFORE any agent ran
(file:line evidence); all sixteen were then subjected to red-first
reproduction. **Fifteen reproduced red; one was split** (claim 6's no-run_id
sub-item and claim 16's release half are honest skips — see completion).

| # | Claim | RED? | Evidence |
|---|---|---|---|
| 1 | unresolved evidence + deleted `.manju` → transport fired | **YES** | consult read only the (fresh, empty) SQLite projection; P0's restore ran only on explicit rebuild |
| 2 | DB missing + unresolved evidence → release not blocked | **YES** | `_submission_blockers`: `if not state.sqlite exists: return []` |
| 3 | state query raise → release not blocked | **YES** (source-confirmed) | `except Exception: return []` with an in-code rationalization ("a tasks concern, not a gate") |
| 4 | verifications.jsonl unreadable → NO_BASELINE | **YES** (source-confirmed) | `_read_baseline_events` swallowed OSError → ([], 0) |
| 5 | tampered baseline payload, same event_id → VALID | **YES** | event_id never recomputed on read |
| 6 | run NOT_FOUND/FAILED/CANCELED/WAITING_USER → not blocked | **YES** (matrix ×4) | `_run_blockers` mapped only INCOMPLETE. Sub-item "new-style sidecar without run_id": NO RED constructible — the key sidecar has no new-style marker (locale/audition finals legitimately omit run_id); would require an out-of-budget schema field → SKIPPED_WITH_EVIDENCE |
| 7 | keeper=true survives spec/expectation change | **YES** | family view checked media sha only |
| 8 | continuation derivation raise → silent | **YES** | `except: pass` at the check join |
| 9 | NLE artifact + nle.project_file same path → duplicate error on the NORMAL path | **YES** | literal `duplicate bundle entry: exports/otio/….otio` |
| 10 | bytes changed after manifest → inconsistent bundle | **YES** | no pre-zip CAS |
| 11 | hand-edited materialized master manifest drives the variant base | **YES** (source-confirmed) | `_resolve_base_master_digest` read `reports/delivery/*.delivery-manifest.json` from disk — the exact "manifest becomes an input" violation |
| 12 | audio/subtitle change → semantic digest unchanged | **YES** (source-confirmed) | `timeline_semantic_digest` was video-segments-only by docstring |
| 13 | absolute metadata path leaked on resolve failure | **YES** | path echoed verbatim |
| 14 | metadata bytes changed → manifest_digest unchanged | **YES** | 13C had deliberately excluded metadata bytes — the review argues (correctly) that metadata content is a delivery fact; recorded reversal |
| 15 | explicit unknown profile/variant → silent MASTER | **YES** | `_delivery_profiles`/`_norm_kind` fell back |
| 16 | final_ref accepted but ignored | **YES (delivery half)** — `build_manifest(final_ref=)` ignored → parameter REMOVED (Option B). Release half: NO RED — `approve_baseline` honors final_ref via `resolve_final` (07C tests 3/11/12 pin exact-byte binding) → SKIPPED_WITH_EVIDENCE |

RED proof runs: H1's `tests/test_post_completion_hardening.py` first ran
**19 failed / 2 passed** at HEAD (the 2 green = §3.4 non-overblocking
guardrails, green by design); H2's `tests/test_h2_hardening.py` first ran
**18/18 failed** at HEAD, each for the claimed reason.

## Orchestrator verdict on the external analysis

Accepted. The review's core insight is correct twice over: (a) the P0 batch
made the EVIDENCE durable but left two consumers trusting the DISPOSABLE
projection (the consult and the release gate) — a fresh SQLite legitimately
returns "nothing", which is not the same as "verified nothing"; (b) 13C's
manifest was derived-only in intent but its base-identity fallback quietly
re-read its own materialization. Both are the exact class of failure the
original contracts prohibit, sitting one seam deeper than the tests reached.
