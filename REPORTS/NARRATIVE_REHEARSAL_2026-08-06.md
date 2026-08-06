# No-paid narrative loop rehearsal - Phase 7A

Date: 2026-08-06

Phase 7A rehearses the landed narrative code paths with synthetic fixture
bytes, manual take registration and explicit fixture QC. It performs no paid
provider call and makes no claim about visual quality, directing quality or a
real finished film.

```text
CODE/PATH REHEARSAL COMPLETE
REAL NARRATIVE PRODUCTION NOT YET PROVEN
```

## Rehearsed path

1. A real `SceneContract` and embedded `ShotContract` opt the fixture project
   into narrative readiness.
2. A content-keyed Animatic fixture is approved by the human-only exact-path,
   exact-byte approval path. Readiness advances to `PROOF_SHOT_READY`.
3. The first registered manual proof candidate fails its authored endpoint.
   The media-bound v2 verdict records the actual observed endpoint, a
   one-variable `endpoint` experiment, and `REWRITE_SOURCE`.
4. The explicit source rewrite changes `action.main` and the authored endpoint.
   The prior experiment remains historical, while its current binding and the
   original Animatic approval become stale. Readiness returns to `AUTHORING`.
5. A new content-keyed Animatic is explicitly approved. A second append-only
   manual candidate receives current-bound observations and derived accepted
   assurance; its actual endpoint becomes the continuation observation.
6. The proof shot passes, readiness advances to `PROOF_SCENE_READY`, and a
   human-only approval binds the current ordered proof-scene digest. The code
   path then derives `BULK_READY`.
7. The attempt ledger remains empty throughout. `BULK_READY` means later paid
   expansion is permitted by the proof gate; it does not mean generation was
   called or production completed.
8. A separate legacy fixture remains `LEGACY` and retains its prior paid-plan
   compatibility. Its caption-card selected media is still `proxy-only` and
   never Picture Lock eligible.

## Verification

- Phase 7A integration rehearsal: **2 passed**.
- Narrative contracts, intent projection, reference control, experiment
  memory, v2 review and production readiness regression: **115 passed**.
- Legacy compatibility, byte-characterization, expectation compatibility and
  neutral preset model tests: **52 passed, 8 deselected**. The deselected cases
  invoke the workstation's known Python 3.14 child-process path; Phase 6 records
  its `WinError 6` evidence.
- `ruff check` on `src` and the Phase 7A test: passed.
- `python -m compileall -q src tests/test_phase7a_narrative_rehearsal.py`: passed.
- `git diff --check`: passed at closeout.

## Boundary

The fixture `.mp4` bytes are deliberately not presented as viewable footage,
and frame extraction plus deterministic media QC are explicit synthetic
fixtures. This proves bindings, invalidation, routing, approvals and readiness
transitions, not story quality or media quality.

Phase 7B still requires a real video provider, real human media, or media
explicitly supplied by the owner, plus actual human review and any required
spend approval. Those prerequisites are not present in this rehearsal. Phase
S2 therefore also remains closed, because it may only be calibrated from real
Dogfood evidence.
