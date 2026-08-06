# Static bug audit closeout - offline only

Date: 2026-08-06

Audited baseline: `87cb387fa9c743efcc17d3aa1276555189db1ba1`

This closeout addresses the 12 findings in
`Manju_One_静态Bug审计与无真实Provider修复指令_2026-08-06.txt`. All implementation
and verification used pure functions, temporary bytes, fake transports, and
offline tests. No real Provider, paid request, external generation, or real
media production was performed.

## Commits

- `d8aa262` - seal engine-owned request fields, plan provider references before
  Prompt/identity/body, and isolate fallback attempt state.
- `92adafb` - scope logical ownership, complete prop and motion/pose projection,
  preserve logical bindings across physical dedup, and align readiness.
- `a9abfd8` - add reference-aware picture SPEC v4 and whitelist redo recipes.
- `72f1f2c` - derive assurance, observed states, continuation, and Proof Scene
  binding from one current-bound verdict.
- `99fa518` - keep absolute physical paths out of request identity blob ids.
- Wave 4 documentation/verification commit: recorded by the final Git history.

## Finding disposition

| Finding | Offline regression evidence | Result |
|---|---|---|
| BUG-P0-01 | reserved prompt/duration/dimensions/shot id tests, preflight/submit parity, explicit seed owner | collisions fail before transport |
| BUG-P0-02 | mode-none and budget omission blockers, selected-only Prompt/identity, rendered-body digest | one provider-specific plan precedes Prompt, identity, admission, and body |
| BUG-P1-03 | distinct/same/global scoped owner tests and alias normalization coverage | ownership key is role plus canonical subject scope |
| BUG-P1-04 | scoped motion/camera and scoped opening preservation tests | subject motion/pose no longer erases unrelated facts |
| BUG-P1-05 | prop Bible reference and Prompt ownership test | prop references traverse Bible, RefSet, Prompt, delivery, and SPEC |
| BUG-P1-06 | binding/control/subject/local-byte SPEC tests | new takes use reference-aware SPEC v4 |
| BUG-P1-07 | same-blob multi-binding, invalid duplicate, upload-once tests | logical binding validation precedes physical dedup |
| BUG-P1-08 | mutating provider A / capturing provider B test | every fallback attempt receives independent request state |
| BUG-P1-09 | strict replay-key and forced-resume regression tests | runtime and unknown legacy sidecar keys never replay |
| BUG-P1-10 | opening/endpoint approval invalidation, equivalent re-review, stale/rejected exclusion tests | Proof Scene binds stable accepted observation and assurance truth |
| BUG-P1-11 | old rejected endpoint/new accepted-without-state mismatch test | assurance and observed state consume the same verdict and packet |
| BUG-P2-12 | shared readiness/generation ownership conflict test | readiness uses the generation validator and stable conflict code |

## Transport and compatibility evidence

The pretransport tests assert empty fake-transport request lists for reserved
placeholder collisions, omitted closed-role owners, readiness conflicts, and
unattended refusal paths. Fallback uses an in-memory failing provider and capture
provider. No default HTTP transport is invoked. The final identity regression
also builds identity without submit and confirms local absolute paths are absent.

`prompt_override` remains byte-preserving. Contract-free legacy Prompt behavior
remains covered by the existing Prompt/intent suites. Manual imports remain
downstream-equal and append-only. Existing take SPEC versions are never rewritten
or compared with a newer formula:

| Recorded take version | Comparison rule |
|---|---|
| v1 / missing | historical v1 formula |
| v2 | historical v2 formula |
| v3 | historical v3 formula; ordinary references do not retroactively stale it |
| v4 | current formula including all authored logical reference bindings and readable local bytes |

Provider delivery budgets and omitted subsets remain outside the
provider-independent picture spec. They are bound by the provider request
identity instead.

## Verification

The complete local Windows run reached `6140 passed, 63 skipped, 29 failed, 15
errors`. Two product regressions exposed by that run were fixed afterward and
their affected suites were rerun green: Experiment Memory no longer changes
assurance/proof truth, and the historical SPEC pin now expects v4. A
`--last-failed` rerun then contained only host/toolchain failures:

- C20A media corpus setup under local FFmpeg 8.1.2 with missing Windows
  fontconfig and a Linux-only DejaVu fixture path;
- command/source-pin tests requiring unavailable `sh` or `grep` executables;
- Windows installer tests blocked by the host's managed PowerShell load error
  `8009001d`.

Final targeted results on the resulting worktree were:

- static audit Wave 0-3: `39 passed`;
- Experiment Memory, intent projection, and Phase 7A rehearsal: `37 passed`;
- legacy spec/staleness/Prompt and audit compatibility: `90 passed`;
- characterization, unattended/no-network, closeout, and identity: `127 passed`;
- documentation and Skill contracts: `246 passed`.

Ruff passed across `src` and `tests`; `compileall -q src` and `git diff --check`
also passed. Ubuntu/Windows CI is run against the final documentation commit.
The exact final SHA and CI run ids are recorded in the task closeout because a
commit cannot embed its own resulting object id.

## Boundary

This closes static request/state consistency defects only. It does not validate
Provider API compatibility, model output quality, story quality, performance,
character continuity, hands/props, or a finished AI film. Phase 7B, evidence-
driven S2, and real Dogfood remain explicitly unstarted.
