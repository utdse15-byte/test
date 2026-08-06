# Production readiness — Phase 5

Date: 2026-08-05

Phase 5 composes existing narrative contracts, timeline compilation, assurance,
media-bound review, and the existing `reports/verifications.jsonl` log into a
pure derived readiness view. It does not add a readiness or approval truth file.

The paid-video path is staged as:

```text
AUTHORING → PROOF_SHOT_READY → PROOF_SCENE_READY → BULK_READY
```

Animatic approval binds the current path, exact bytes, SHA-256, content key,
source revision, human actor and reason. Proof-scene approval binds the ordered
selected takes, exact media hashes, trims, contracts, expectation digests,
transitions, dialogue, sound and voice/timing state. Any relevant source or
media change therefore invalidates the derived approval match.

## Verification

- Focused Phase 5, Animatic-key, tool-key and plan-projection tests: **64 passed**.
- Documentation, CLI snapshot, workflow, contract and skill tests: **280 passed**
  in the first focused run; **183 passed** after registering the readiness schema
  and approval refusal code.
- Build/CLI/caption/paid-confirmation regressions: **156 passed, 2 skipped**.
- GUI, assurance, narrative-contract and interconnection regressions:
  **189 passed** under `PYTHONUTF8=1`.
- Full repository regression with Git tools on PATH, excluding only the two
  confirmed workstation-incompatible modules below: **6067 passed, 64 skipped**.
- Static checks: `ruff check --select E9,F63,F7,F82 src` and `compileall` passed.

The local full suite reached 6082 passed and 64 skipped. Remaining failures are
environment-specific on this workstation: ffmpeg 8.1.2 cannot resolve the
Linux `/usr/share/fonts` fixture path, Windows PowerShell 5.1 fails to initialize
when the installer tests intentionally replace `PATH`, and Git `grep.exe` is
not on the default PATH. CI continues to use the pinned ffmpeg and UTF-8 setup.
