# Third static residual closeout - offline only

Date: 2026-08-06

Audited baseline: `702362128104305ba6fb915061f8d3f504612a78`

Final implementation SHA before documentation closeout:
`5bab4c360c27e5b2c92bab0e98ee0ffc76cc3bec`

The repository's final closeout HEAD is the commit containing this report and
is obtained with `git rev-parse HEAD`. A content-addressed Git commit cannot
embed its own object id without changing that id; the exact fifth-commit SHA is
therefore recorded in the task closeout and CI run, not fabricated here.

This report closes T3-P0-01 and T3-P1-02 through T3-P1-04 from
`Manju_One_第三轮静态复核_付费恢复与引用QC残留修复指令_2026-08-06.md`.
All implementation and verification used pure functions, temporary bytes/files,
injected Fake Transport, and offline tests.

## Commits

1. `08f9a413336419237e7c32c570dc7e47a69fa646` -
   `feat(submission): bind execution profile to recovery identity`
2. `ca2ce1210696026b4b8fe84460dd76bfe5770c3a` -
   `fix(refs): preserve inferred bible subject scopes`
3. `57607f8345bfa0e72a72c7fa73b7c76963d45c56` -
   `fix(refbudget): classify logical bible bindings by scope`
4. `5bab4c360c27e5b2c92bab0e98ee0ffc76cc3bec` -
   `feat(expectations): add scoped commitment schema v3`
5. Final documentation/test closeout -
   `docs(audit): close third-pass static residuals` (the commit containing this
   report; exact SHA is emitted after Git creates it).

## Red-first evidence

The initial six-test slice produced `5 failed, 1 passed`. The passing v2 pin
proved that the compatibility branch already existed; the five failures were
the minimum behavioral evidence for the four residual findings:

| Finding | Pre-fix failing evidence | Observed defect |
|---|---|---|
| T3-P0-01 | `test_submit_url_change_moves_submission_identity`; `test_execution_profile_mismatch_blocks_redispatch_before_transport` | submit URL did not move identity, and changed execution semantics reached Fake Transport |
| T3-P1-02 | `test_character_bible_ref_infers_subject_scope` | runtime resolver discarded the parser's Bible scope |
| T3-P1-03 | `test_budget_uses_canonical_scene_and_prop_scopes` | scene/prop logical bindings fell back to character budget roles |
| T3-P1-04 | `test_scoped_opening_facts_compile_as_distinct_v3_expectations` | scoped openings emitted v2 and identical text for two subjects collapsed |

The passing pin was `test_unscoped_contract_remains_expectations_v2`.

## Repaired behavior

- A frozen, secret-free `ProviderExecutionProfile` binds submit, poll,
  rejection, failure-marker, and original idempotency semantics to Generic
  Cloud submission identity v2. PREPARED evidence and SQLite persist the
  snapshot/digest. Drift and missing legacy profile evidence fail closed before
  poll or redispatch.
- Bible inferred character/scene/prop scopes survive runtime resolution. An
  explicit scope wins, inferred scope does not impersonate an explicit
  transfer, logical bindings survive dedup, and a shared physical blob uploads
  once.
- Reference budget roles prefer canonical runtime scope. Dict-form Bible refs
  use the shared parser rather than `str(dict)`, and lineage retains every
  logical role sharing one blob.
- Scoped opening commitments emit Expectations v3. Canonical scope participates
  in row ID and set digest; review packets preserve the row. Unscoped contracts
  retain the historical v2 implementation and bytes.

## Transport and no-network proof

The new regression module contains 27 offline tests and injects
`_RecordingTransport`; it never invokes the default HTTP transport. Profile
drift, a legacy ambiguous row, and an originally non-idempotent request under a
later idempotent manifest each assert `transport.requests == []`. Thus those
unsafe recovery paths have `transport_count == 0`. The one persistence test
that crosses a simulated submit boundary receives an injected in-memory 503;
it performs no network request and verifies that rotated secret values are
absent from SQLite and `events.jsonl`.

The qualification recovery drill records the same profile evidence as a real
PREPARED chain, deletes disposable SQLite, rebuilds from append-only events,
and resumes poll-only with `resubmit_calls == 0`. A legacy unresolved chain
without that evidence is separately pinned to fail closed with zero transport.

## Submission identity compatibility

| Schema | Permanent behavior |
|---|---|
| `manju.submission-identity/v1` | historical/pure legacy identity construction remains unchanged; it has no execution-profile digest |
| legacy unresolved v1-era row | absence of a recoverable execution profile fails closed before poll/redispatch; a live manifest cannot retroactively authorize it |
| `manju.submission-identity/v2` | current Generic Cloud identity binds rendered request, provider profile, and secret-free execution-profile digest; valid recovery must match the stored profile |

## Picture SPEC compatibility

| Recorded take version | Permanent comparison rule |
|---|---|
| v1 / missing | historical base picture formula |
| v2 | historical dialogue/keyframe formula |
| v3 | historical props/picture-contract formula; opening remains statement text |
| v4 | historical flat-reference formula, including its mapping omission |
| v5 | complete shared reference syntax, canonical logical scopes, local bytes, and structured opening payload |

Every take is compared under its recorded version. There is no migration or
bulk stale rewrite.

## Expectations compatibility

| Schema | Permanent behavior |
|---|---|
| `manju.qc.expectations/v1` | historical contract-free quality/continuity compiler and verdict reads remain unchanged |
| `manju.qc.expectations/v2` | historical unscoped ShotContract compiler, ID formula, digest, and verdict reads remain byte-identical |
| `manju.qc.expectations/v3` | scoped authored commitments include canonical `subject_scope` in each row, ID, and set digest |

Verdict/review/proof consumers continue to use expectation IDs and digests as
authority; they do not match commitments by statement text alone.

## Verification

Post-fix local Windows results:

- third-round residual regression: `27 passed`;
- static audit Wave 0-3: `60 passed`;
- provider submission/recovery/idempotency/unattended: `150 passed`;
- reference resolver/ownership/budget/SPEC and history: `137 passed, 2 skipped,
  1 deselected` (the deselected local-command test requires unavailable `sh`);
- intent/Expectations/review/proof/readiness: `129 passed`;
- legacy Prompt/SPEC/Expectations pins: `41 passed`;
- qualification recovery plus full contract registry: `18 passed`;
- third-round plus DR06 identity/recovery after the qualification repair:
  `94 passed`;
- final third-round, Wave 0-3, qualification, and contract-registry closeout:
  `105 passed`;
- documentation reachability/closeout contracts: `29 passed`;
- final Expectations/intent/assurance/readiness core recheck: `82 passed`;
- Ruff, `python -m compileall -q src/manju`, and `git diff --check`: passed.

The required full `pytest` run completed rather than stopping at first failure:
`6199 passed, 63 skipped, 24 failed, 15 errors`. It exposed three closeout
regressions (the recovery drill's old event shape and two registry assertions),
which were fixed and rerun green in the `18 passed` gate above. The remaining
host/toolchain failures are outside this change:

- 15 C20A setup errors: local FFmpeg cannot load Fontconfig and the fixture uses
  a Linux-only DejaVu path;
- 8 local-command/reference tests: this Windows host has no usable `sh`;
- 9 installer tests: managed Windows PowerShell fails to load with `8009001d`;
- 3 source-pin tests: this host has no `grep` executable;
- 1 local Git history fixture was transient and passed on immediate isolated
  rerun. None occurs in the third-round changed surface or its focused gates.

Ubuntu and Windows CI must run against the exact final documentation commit.
Their exact SHA/run ids belong in the post-commit task closeout.

## Boundary

No real Provider, paid request, network generation, real media production,
Phase 7B, or S2 work occurred. This proves static/offline request recovery
safety, reference-scope consistency, and scoped QC identity only. It does not
prove Provider API compatibility, model output quality, story/performance/
continuity quality, hands or prop fidelity, or a finished AI film. Those claims
still require Phase 7B real Dogfood and evidence-driven S2.
