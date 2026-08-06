# Second static residual closeout - offline only

Date: 2026-08-06

Audited baseline: `b8c9491904887406c25ebfef7889e5713161d4b7`

This closeout addresses R1-R3 from
`Manju_One_第二轮静态复核_3个残留Bug与离线修复指令_2026-08-06.md`.
All implementation and verification used pure functions, temporary bytes,
Fake Transport, and offline tests. No real Provider, paid request, network
generation, real media production, Phase 7B, or S2 work was performed.

## Commits

- `69fd707` - freeze the exact semantic provider payload before request
  identity and reuse it for submit/retry.
- `3942f7f` - add picture SPEC v5 and share complete authored-reference mapping
  expansion with provider resolution.
- `7918e96` - make structured opening facts schema-valid and project them by
  exact canonical subject scope.
- Documentation/verification closeout: recorded by the final Git history.

## Red-first evidence and disposition

| Residual | Pre-fix behavior | Offline closeout |
|---|---|---|
| R1 request/body TOCTOU | 2 failing tests showed that reference bytes and body-template content could change after identity was built | immutable `PreparedProviderPayload`; body rendered once, local files read once, identity and Fake Transport consume the same semantic fields/files, retries reuse them |
| R2 `shot.refs` mapping gap | `test_spec_v5_tracks_shot_refs_mapping_plural_images` failed with an empty `references` list | v5 and provider resolution share `iter_authored_reference_bindings`; singular/plural image/video and mixed refs preserve resolver order and bind controls, ignore, subject scope, and local bytes |
| R3 unreachable scoped opening | `test_shot_contract_accepts_structured_opening_fact` failed validation at `contract.opening.0` because only strings were accepted | `SubjectStateFact` is schema-valid; one helper family feeds Prompt, v5 SPEC, Expectations, director view, and Animatic digest; full scopes prevent `character:A`/`prop:A` cross-match |

## Compatibility matrix

| Recorded take version | Comparison behavior |
|---|---|
| v1 / missing | historical base formula |
| v2 | historical dialogue/keyframe formula |
| v3 | historical props/picture-contract formula; opening remains statement text |
| v4 | historical flat-reference formula, including its mapping omission |
| v5 | complete shared reference syntax and structured opening payload |

Every existing take is compared under its recorded version. Nothing migrates a
take or rewrites an old hash. Legacy string opening values round-trip unchanged,
legacy Prompt bytes stay pinned, and `prompt_override` remains verbatim.

## Verification

Pre-closeout targeted results:

- R3 intent/scoped ownership: `40 passed`;
- static audit Wave 0-2 combined: `53 passed`;
- intent, expectations, staleness, readiness, Prompt, and historical hashes:
  `151 passed`;
- Ruff on the R3 surface, `compileall -q src/manju`, and
  `git diff --check`: passed.

The final offline full-suite, lint, compile, and no-network/Fake Transport gate
results are recorded in the task closeout after the documentation commit. CI
must run Ubuntu and Windows against that exact final SHA; a commit cannot embed
its own resulting object id.

## Boundary

These results establish static request identity, reference staleness, and
opening-ownership consistency only. They do not establish Provider API
compatibility, model output quality, story/performance/continuity quality,
hands or prop fidelity, or a finished AI film. Those claims still require real
Phase 7B Dogfood and evidence-driven S2.
