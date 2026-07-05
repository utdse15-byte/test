# REVIEW-01 Response

Review round 1: six defects (A–F) across four tickets. All acceptance
commands pass verbatim; red-first TDD throughout (each defect's failing state
was reproduced before fixing, exceptions noted honestly below).

Baseline before any change: `172 passed, 0 skipped` (this tree has 172 tests;
the review's predicted counts of 169+3/174 did not match this tree — reported
before starting, no changes were stacked to chase the number).

## Defects → fixes

| Defect | Description | Fixing commit | Red-proof |
| --- | --- | --- | --- |
| A | every `build` minted a new `final_vN` for identical inputs | `e1031b0` `[FIX-A]` | build-twice produced 2 finals (test + live repro) |
| B | non-frame-aligned durations; final `r_frame_rate` drifted | `93b302b` `[FIX-B]` | pre-fix final probed `r_frame_rate=143/6`; 1200ms clips became 29-frame/1216ms segments |
| C | secret scan missed unquoted `api_key=sk-proj-…`, ghp_/gho_, xox?-, Bearer | `756a69e` `[FIX-C]` | 7 red-team samples missed pre-fix (parametrized tests) |
| D | YAML/schema/Media errors leaked tracebacks | `756a69e` `[FIX-D]` | props.yaml repro raised ScannerError through the CLI; manual-mode invalid timeline.json raised JSONDecodeError |
| E | unpack lost the original project name | `756a69e` `[FIX-E]` | 雨夜便利店 → arbitrary.manjupkg → unpack produced `arbitrary.manju` |
| F | spec_hash intent unpinned; no voice-input hash for M3 | `7bbbbbf` `[FIX-F]` | voice-hash tests red (ImportError); **the two intent-pin tests were green from the start** — dialogue.text was already excluded from the video spec_hash; they pin the invariant rather than fix a live bug |

Also in ticket 2: `tests/fixtures/make_sample.py` converted to argparse
(`--help` is side-effect-free); ticket 3 split the `tools` extra into
`jianying` / `capcut` / `mcpvideo`.

## Acceptance commands and outputs

```
$ pytest -q
208 passed in 133.21s

$ python tests/fixtures/make_sample.py /tmp/e2e
/tmp/e2e/雨夜便利店.manju
$ cd /tmp/e2e/雨夜便利店.manju && manju build && manju build
$ ls renders/final/*.mp4 | wc -l
1

$ ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate \
    -of default=nw=1 renders/final/final_v1.mp4
r_frame_rate=24/1
```

No mocks, skipped tests, or loosened assertions were used to make acceptance
pass. Four pre-existing unit tests of the internal `_resolve_duration_ms`
were updated for its new `(inp, rules, fps)` signature; their millisecond
expectations changed only where the values were genuinely not frame-aligned
(1200 → 1208 @ 24fps), which is the FIX-B behavior itself, and a new test
pins the rounding rule.

## Honesty notes (not fully verified)

- The QC **duration** assertion (`|final − timeline| ≤ 1 frame`) was NOT
  independently red pre-fix: the final render's `-t` clamp masked the
  accumulated drift in the container duration (16.8ms on a 6-shot sample).
  The drift was real but surfaced as truncated content, not as total
  duration; the red-proof for defect B came from the `r_frame_rate`
  assertion (143/6) and the per-segment probes (1216ms for 1200ms clips).
- The FIX-F intent-pin tests were green on first run, as noted above.
- Ticket 2's C and D changes share hunks in `core/check.py`, so tickets 2's
  three fixes are one commit (`756a69e`) with all three tags rather than
  three commits.

## Newly discovered issues (recorded only — scope not expanded)

1. `final_content_key` hashes the ASS file, but in captions **manual** mode
   the ASS is recompiled from the human SRT during every build *before* the
   key check — if a human edits `captions.srt` between builds the key changes
   correctly, but a byte-identical recompile also rewrites the ASS mtime;
   correctness holds, though the key computation reads the freshly rewritten
   file. No misbehavior observed; noted for audit.
2. `snap_to_frame_grid` is applied to clip durations only; a hand-authored
   **manual-mode** `timeline.json` with non-frame-aligned durations bypasses
   the snap (compiled-mode-only guarantee). QC's fps/duration assertions
   still catch the drift on the final, but a manual timeline can fail QC
   without a hint that the grid is the cause.
3. `proxy` renders have no content-key skip (idempotency was scoped to
   finals per the ticket); repeated `--target proxy` builds re-encode the
   proxy each time.
4. The review's predicted baseline counts (169+3 / 174) did not match this
   tree (172); worth reconciling which tree the review counted.
