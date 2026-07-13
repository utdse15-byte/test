# FP Test-Speed Batch (F2 + F3)

Implementation of `FP_OPT_TESTS_RESULTS.md` findings **F2** (build-once /
read-many shared fixtures) and **F3** (throwaway fixture-clip preset), the
test-suite speed batch. F1 (pytest-xdist) landed a prior wave. TESTS-ONLY —
**no `src/manju/**` changed**, nothing committed. `python -m pytest` throughout.
**No test, assertion, skip, or pin was deleted or weakened.**

**Result: ~27.5 s off the serial wall of the touched set (F3 ≈ 24.1 s / 15
files, F2 ≈ 3.4 s / qc_consistency), 284 → 284 passed, and the set is
`-n auto`-clean (parallel-safe).**

## Changes
| File | Finding | Change |
|---|---|---|
| `tests/fixtures/make_sample.py` | F3 | `_make_clip` source encode `medium` → **`-preset ultrafast`** (the only generation site the report counts; imported by 13 test files) |
| `tests/test_qc_consistency.py` | F2 | new `scope="module"` `consistency_project` (+ `consistency_gui`) fixture; **7 read-only tests share it**, **9 mutating/cold-cache tests keep their own build**; every assertion unchanged |

## F3 is byte-safe (verified, not assumed)
- Takes from these clips carry the literal `spec_hash="manual"` + a hand-set
  probe (`make_sample.py:159-173`) → clip bytes feed **no pinned hash**.
- The real byte-identity/golden pins (`test_fp_ratemig*`, `test_hash_versions`)
  build their **own** sources (`ratemig._gen_real_source`) — grep-confirmed they
  never import make_sample; the tree's 64-hex golden literals are **disjoint**
  from the 13 make_sample consumers.
- Every make_sample consumer asserts "byte-identical" **relatively** (idempotency
  / content-key-skip / `sha256:`-prefix), never against a baked literal → a
  same-project two-build comparison is preset-invariant. Zero baked render
  goldens found in any consumer.
- No test probes source-clip codec/preset/bitrate; ultrafast preserves
  duration/fps/resolution/audio. `_make_bgm` (PCM `.wav`, no x264) left as-is.

Microbench (real ffmpeg): `make_sample_project(2)` 1.479 → 0.386 s (−74 %);
`(12)` 3.462 → 1.700 s (−51 %).

## F2 split (why safe)
Read-only sharers only READ the brief/coverage/board/`/review` and never write a
verdict or regenerate a take; the sole shared side effect is the
**content-addressed, unit-salted board cache**, populated idempotently → every
sharer sees byte-identical truth regardless of order. Mutating tests (verdict
writes, member regeneration) and the two **cold-cache-dependent** board tests
(`..board_composed_labeled_and_cached` asserts one `make_board` call per unit on
a cold pass; `..board_degrades..` asserts `image is None`) keep fresh builds.
Full per-test classification table in `scratchpad/FP_M3_RESULTS.md`.

Note: the report's F2 sketch mislabeled `..board_composed_labeled_and_cached` and
`..verdict_endpoint_roundtrip_as_human` as read-only; they are cold-cache- and
verdict-writing respectively and are kept fresh — sharing would have required
weakening an assertion. dr03c needed no change: `test_dr03c_lifecycle.py` already
shares a `scope="module"` `built` fixture; its remaining fresh builds are all
destructive/unique, and `test_dr03c_attempts.py` uses no ffmpeg.

## Respected rule-outs
F4 render preset (byte-pin frozen), F6 sleeps (load-bearing), F7 collection, F8
`tmp_project` scope — untouched. F5 (CI M0 smoke) deferred: `ci.yml` is
orchestrator-owned, outside TESTS-ONLY. Inline clip gens in `test_overlay.py:125`
/ `test_dr01_binding_pins.py:48` left alone (outside F3 scope; determinism /
binding-pin sensitive).

## Measurements (pytest-internal seconds)
| Set | before | after | Δ | passed |
|---|---:|---:|---:|---:|
| 15 make_sample consumers (F3) | 224.91 | 200.77 | −24.14 | 258 → 258 |
| test_qc_consistency.py (F2) | 24.01 | 20.64 | −3.37 | 26 → 26 |
| **touched-set per-file sum** | **248.92** | **221.41** | **−27.51** | **284 → 284** |

All 16 touched files together: **serial 222.13 s / 284 passed**;
**`-n auto` (4 cores) 138.10 s / 284 passed** — parallel-safe, count preserved.
Largest single win: `test_packaging.py` −13.3 s (multiple sample builds).
Full-suite serial after the change: **4092 passed / 13 skipped, 0 failed**
(969 s) — count rose from the 4056 baseline (concurrent loops' new pins),
skips unchanged. Full run tails in `scratchpad/FP_M3_RESULTS.md`.
