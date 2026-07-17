# AI IDE 04 Completion

Build report for **Manju Deep Research 04 — provider capability projection & pre-paid preflight**. Paired baseline: `REPORTS/AI_IDE_04_BASELINE.md`. Final-run numbers and git state are filled by the orchestrator after the last full-suite run.

---

## Repository

| Field | Value |
|---|---|
| Base commit | `cb7305d` (DR03C reports; DR01→DR03C landed) |
| DR04 landed | `src/manju/providers/catalog.py` (new), `src/manju/providers/preflight.py` (new); routing extraction + compatibility skip-reasons in `providers/routing.py`; explicit-pin fail-earlier in `providers/registry.py`; duration-guard delegation in `providers/generic_cloud.py`; `providers catalog` subcommand + structured `providers check --json` sections in `cli.py`; `tests/test_dr04_characterization.py` + `tests/test_dr04_catalog.py` + `tests/test_dr04_preflight.py`; these two reports |
| Production files touched | **6 of the 10-file cap** (spec expected ~5) — `catalog.py`, `preflight.py`, `routing.py`, `registry.py`, `generic_cloud.py`, `cli.py`. **`manifest.py` UNTOUCHED** (no fixture proves a field addition — items 9/10 SKIPPED_WITH_EVIDENCE) |
| Final full-suite result | `**4 failed, 2382 passed, 12 skipped** (904.44s / 0:15:04) — the 4 are exactly the pre-existing `include_unindexed` set; zero new failing test names; +53 vs DR03C's 2329 = the 53 DR04 tests` |
| Final git state | `base `cb7305d` → `75e37f7` (DR04 code: catalog + preflight + routing extraction + CLI + 53 tests + DECISIONS.md #17 + README row) → the reports commit adding this file + its pair; working tree clean after it` |

---

## Baseline (WP0 verdict)

Gate PASSED → **BUILD**. The provider FACTS all exist but live PRIVATE to routing (`_catalog()`/`_ProviderView`, routing.py:250-281); the duration cap is inlined in the submit path only (generic_cloud.py:697-704); `max_resolution` has grep-zero semantics (opaque); structured limits + `capability_profiles` are grep-zero (absent). No projection, no spend-free preflight, no profile digest. Full §6.3 table with file:line in `REPORTS/AI_IDE_04_BASELINE.md`.

---

## Red-first

**Characterization first (12 pins, GREEN before any production change).** `tests/test_dr04_characterization.py` captured the exact surface the extraction must not move — `_catalog` facts, §8.4 default-order byte-identity, the cheapest real-price order, the duration guard's zero-transport-submit property, the placeholder vocabulary, `render_body`'s unknown-placeholder refusal, and the refbudget byte-identity path. All 12 pass on the untouched tree.

**New capability RED.** With `catalog.py`/`preflight.py` absent, both new suites fail at collection:
```
ImportError: cannot import name 'preflight' from 'manju.providers'
```
Then, with the modules present but the consumers un-wired, exactly the 7 integration tests were RED (`test_36/36b/37` CLI, `test_33/34/35/38` routing) while the 33 pure catalog/preflight tests were already GREEN — proving the pure logic and the wiring are independently pinned. Wiring the consumers turned all 7 GREEN with the 12 characterization pins still GREEN (extraction byte-identical).

---

## Existing systems reused (not duplicated)

| System | file:line | How reused |
|---|---|---|
| `load_manifests` (errors, duplicate id) | `manifest.py:415-435` | the descriptor scan's manifest source; broken → `errors`, never hidden |
| `available_providers` / built-ins | `registry.py:36-44,116-122` | the descriptor scan's live-kind refinement + built-in entries |
| `hash_value` / `canonical_json` | `core/hashing.py:23-31` | `projection_digest`, `source.semantic_digest`, `provider_profile_digest` — one canonical hasher |
| `resolve_refs` / `RefSet` | `refs.py:157-180` | the ONE refs resolver; preflight reads resolved counts, never re-resolves |
| `allocate` / `BudgetReport` | `refbudget.py:272-296` | the ONE allocator; preflight's effective-count clamp mirrors `budget.selected[:refs.max_*]` |
| `reachability_probe` | `manifest.py:679-713` | `free_probe_available` = comfyui(`/system_stats`) or `ping_url` set |
| `estimate_cost` | `manifest.py:438-441` | the projection's per-capability `cost` block |
| `_mask_secrets` / key-env-name-only (§8.2) | `cli.py:4883-4899` | the projection carries credential NAMES only; the CLI reuses the masking discipline |
| `Resolution.skipped` / `explain` | `routing.py:373-387,526-543` | extended with compatibility skips — they flow through `explain` with a ≤0-line change (ruling 7) |

---

## WP1 — `providers/catalog.py` — the single source of provider facts

`SCHEMA = "manju.provider-capability-projection/v1"`. `ProviderDescriptor` (frozen dataclass) carries every fact a consumer needs; the routing-facing subset mirrors the old `_ProviderView` plus `max_duration_ms`.

### API (as implemented)

```python
@dataclass(frozen=True)
class ProviderDescriptor:              # provider_id, provider_type, adapter, kind, enabled,
    ...                                # exists, source_kind, capabilities, per_second, per_call,
                                       # currency, max_duration_ms, max_resolution(verbatim),
                                       # max_concurrent, rate_limit_per_min, max_ref_images,
                                       # max_ref_videos, refs_image_mode, refs_video_mode,
                                       # refs_max_images, refs_max_videos, first_last_supported,
                                       # credential_env(name only), free_probe_available, manifest

iter_provider_descriptors() -> tuple[list[ProviderDescriptor], list[str]]   # (sorted, errors)
descriptor_for_manifest(manifest) -> ProviderDescriptor                     # single-manifest, no disk scan
project_provider_capabilities() -> dict                                     # the …/v1 document
provider_profile_digest(provider_id, capability, *, descriptor=None) -> str # AI_IDE_06 deliverable
provider_source_digest(descriptor) -> str                                   # provider-level identity
profile_id(provider_id, capability) -> str                                  # "provider:<id>#<cap>"
```

- **`iter_provider_descriptors` mirrors `_catalog()` EXACTLY** — manifests first (kind by adapter), then the live registry refines `kind` for any provider it constructed and adds built-ins with empty caps / zero cost / `source_kind="builtin"`. Broken manifests never enter the list — collected as `errors` (test 07).
- **The projection document**: `{schema, providers[…sorted…], projection_digest, errors}`. Each provider = `{provider_id, provider_type, adapter, enabled, source{kind, semantic_digest}, capabilities[{profile_id, limits, refs, cost, profile_digest}], credential_requirements(names only), free_probe_available, limits}`.
- **Deterministic / derived / secret-free**: providers sorted, no timestamps, `projection_digest = hash_value(providers)`. The digest EXCLUDES paths, mtime, credential values AND credential presence (only the env-var NAME appears). Errors are scrubbed of the absolute providers root. Pure function of built-ins + on-disk manifests — delete/rebuild safe, never a build/resume/cache input.
- **`max_resolution`** surfaced VERBATIM in every `limits` block (opaque); `max_width`/`max_height`/`allowed_frame_sizes` are NEVER invented (test 13/14).

### The profile digest (AI_IDE_06 deliverable)

`provider_profile_digest(provider_id, capability)` = `hash_value` over `{profile_id, provider_id, provider_type, adapter, capability, limits, refs, cost}`. **Stable** (test 16) and **moves only on a semantic change** (test 17 — a `max_duration_ms` edit shifts it). `source.semantic_digest` is the provider-level counterpart (all facts, credential NAME only, no presence). The next batch derives submission identity from these; the digest inputs are documented in `catalog._profile_facts` / `_provider_semantic_facts`.

---

## WP2 — `providers/preflight.py` — spend-free request compatibility

`SCHEMA = "manju.request-compatibility/v1"`. Pure leaf (imports only stdlib + typing) so both routing and generic_cloud can depend on it.

```python
duration_exceeds_limit(max_duration_ms, duration_ms) -> bool   # the ONE duration rule
iter_body_placeholders(template) -> set[str]                   # {name} roots a body references
check_request_compatibility(entry, *, capability, duration_ms, width=None, height=None,
    ref_image_count=0, ref_video_count=0, first_last=False, params=None,
    body_placeholders=None) -> dict
BASE_PLACEHOLDER_KEYS = frozenset({prompt,duration_s,duration_ms,width,height,fps,seed,shot_id})
```

- **Status** ∈ `COMPATIBLE` / `COMPATIBLE_WITH_OMISSIONS` / `INCOMPATIBLE` / `UNKNOWN_LEGACY`. Output = `{schema, provider_id, capability, status, effective{…}, omissions[], reasons[], warnings[]}`.
- **Validation scope = ONLY explicit executable facts (§8.3):** exists / enabled (INCOMPATIBLE), capability match (INCOMPATIBLE), duration vs `max_duration_ms` via the shared rule (INCOMPATIBLE), body-template placeholder completeness — an unknown placeholder is `INCOMPATIBLE` before submit (test 25/26). Reference counts vs caps/budget and first/last support are **OMISSIONS, never a block** (tests 27/31). `max_resolution` + a width/height → a `LEGACY_UNINTERPRETABLE_LIMIT` warning, never a status change (test 30).
- **Never infers** from story / prompt / shot-size — a giant prompt / a `shot_size` param leaves the verdict identical (test 32).
- **UNKNOWN_LEGACY** for a built-in / no-facts provider (test 29) so the fallback safety net is NEVER declared incompatible.
- **Effective refs = the SAME clamp submit applies** (`min(requested, field cap, budget cap)` = `budget.selected[:refs.max_*]`). Test 28 resolves refs once (the one resolver), allocates once (the one allocator), and asserts `preflight.effective.ref_images == len(budget.selected[:max]) == len(delivered by the fake adapter)`.

---

## WP3 — extraction + fail-earlier wiring (byte-identical where required)

| File | Change | Δ lines |
|---|---|---|
| `providers/catalog.py` | **new** — descriptors, projection, digests | +341 |
| `providers/preflight.py` | **new** — duration rule, placeholder scan, compatibility check | +326 |
| `providers/routing.py` | `_catalog()` → thin consumer of `iter_provider_descriptors`; `_ProviderView.max_duration_ms`; `_incompatible_skip` + `explicit_pin_incompatibility` (share `duration_exceeds_limit`); `_capable_sorted` + `add()` record compatibility skips | +86 |
| `providers/registry.py` | explicit-pin incompatibility raises `invalid` BEFORE submit (next to the disabled guard) | +12 |
| `providers/generic_cloud.py` | `_enforce_limits` delegates to `preflight.duration_exceeds_limit` (byte-identical); `_placeholder_map` cross-references `BASE_PLACEHOLDER_KEYS` | +13 |
| `cli.py` | `providers catalog [--json] [--output]` (atomic derived report); `providers check --json` structured sections `{manifest_errors, credential_presence, projection_consistency, routing_references, warnings}` | +101 |
| `providers/manifest.py` | **UNTOUCHED** | 0 |

### Single-source proof (who consumes the shared modules)

- **Provider FACTS** come from `catalog.iter_provider_descriptors` and nowhere else. Consumers: **routing** (`_catalog()` builds `_ProviderView` from descriptors), the **CLI** (`providers catalog` + `providers check` projection consistency), and **preflight** (via `descriptor_for_manifest`). One scan, three readers — pinned byte-identical by `test_providers_routing` + the 12 characterization tests.
- **Duration rule** is `preflight.duration_exceeds_limit`, consumed by **generic_cloud** (`_enforce_limits`, the submit-time final defense), **routing** (`_incompatible_skip` + `explicit_pin_incompatibility`), and **preflight** (`check_request_compatibility`). Submit and preflight can never disagree (test 23/24).
- **Placeholder vocabulary** is `preflight.BASE_PLACEHOLDER_KEYS`; `generic_cloud._placeholder_map` fills exactly these keys — equality pinned by `test_placeholder_vocabulary_is_single_source`.
- **Refs** = `resolve_refs` + `refbudget.allocate` (the one resolver + one allocator); preflight consumes their results, never a second allocator (test 28).

### Fail-earlier changes (each a deliberate, recorded behavior change)

1. **cheapest / local compare only compatible candidates** — `_capable_sorted` drops a duration-incompatible candidate with a VISIBLE `skipped` reason, so the strategy head is always runnable (tests 33/35). Was: all capable providers ranked, incl. those that would fail at submit.
2. **Rule / tier / quality-priority candidate skip** — `resolve.add()` skips a duration-incompatible non-explicit candidate with a recorded reason (test 38), surfaced through `explain` unchanged (ruling 7 satisfied additively).
3. **Explicit pin fails BEFORE submit** — `registry.generate_with_fallback` raises `ProviderFailure(invalid)` for an explicit pin whose duration exceeds the provider's cap (via `routing.explicit_pin_incompatibility`), next to the existing disabled-pin guard — never silently degraded to fallback (test 34). Was: caught at submit, then degraded down the chain.
4. **§8.4 fallback chain UNCHANGED** (deliberate NON-change) — "fallback ORDER unchanged" is a hard rule: the exhaustive safety net is never trimmed (a shot must never dead-end; the promptlab excessive-duration linter reads the whole chain to find a ceiling — `qc/prompt_checks.py:176`). An incompatible provider only the safety net reaches is still stopped by generic_cloud's submit-time guard. Verified: `test_promptlab::test_excessive_duration_for_provider_warns_and_splits` stays GREEN.

---

## WP4 — CLI surfaces

- **`manju providers catalog [--json] [--output <path>]`** — ONE new subcommand on the existing `providers_app`. Prints the projection (human table or `--json`); `--output` writes an atomic derived JSON report (`atomic_write_text`, never read by a build). Human output carries no credential values and surfaces `max_resolution` verbatim with a `LEGACY` note (§10.2). Smoke-tested end-to-end: no secret / signed-URL token leaks; the digest is identical whether the API key is set or unset.
- **`manju providers check <id> --json`** — gains `{manifest_errors, credential_presence, projection_consistency, routing_references, warnings}` additively (existing `id/ok/enabled/problems/live` keys unchanged, so `test_check_reports_problem_and_fix_hint` stays GREEN). `credential_presence` names the env var and its set/unset state — presence is allowed in the doctor, NOT in the projection/digest. `projection_consistency` recomputes the projection twice and confirms the provider is present. Human output adds a `⚠` line per warning.
- **`route explain` / `routing explain`** — untouched; the compatibility skips flow through the existing `Resolution.skipped` → `explain` path with no explain-side change (ruling 7 → SKIPPED_WITH_EVIDENCE for a dedicated explain edit).

---

## Conditional-item decisions (with evidence)

| Item | Decision | Evidence |
|---|---|---|
| `max_resolution` structured parse (`max_width`/`max_height`/`allowed_frame_sizes`) | **SKIPPED_WITH_EVIDENCE** — surfaced verbatim + `LEGACY_UNINTERPRETABLE_LIMIT` warning; no manifest field added | grep-zero for the fields; `max_resolution` has exactly one hit (the def, manifest.py:68) — no semantics to preserve |
| `capability_profiles` overlay + param type/enum/range contracts | **SKIPPED_WITH_EVIDENCE** — every capability echoes the provider-level limits; no manifest field added; no failing fixture written | grep-zero; no shipped manifest has ≥2 capabilities with genuinely conflicting top-level limits (§9.4 gate unmet) |
| Dedicated `explain`/dry-run compatibility wiring | **SKIPPED_WITH_EVIDENCE (satisfied naturally)** — the ruling-5 skip reasons already flow through `Resolution.skipped` → `explain` with no explain-side change (≤10-line additive intent met at 0 lines) | test 38 asserts a rule's incompatible provider appears in `explain(...)["skipped"]` |
| `manifest.py` field additions | **NONE** | no failing fixture demanded one; old `provider.yaml` files load unchanged |

---

## Tests

- **Red-first** — verbatim above (characterization GREEN pre-change; new suites `ImportError` RED; 7 integration tests RED until wired, then GREEN with the 12 pins still GREEN).
- **New suites (53 tests):** `test_dr04_characterization.py` (12), `test_dr04_catalog.py` (22, contract §11 items 1-18 + CLI 36-37), `test_dr04_preflight.py` (19, items 19-35 + 38 + the placeholder single-source cross-check). All 53 GREEN.
- **Targeted regression set (306 passed):** `test_providers_routing` + `test_generic_cloud` + `test_refs` + `test_refbudget` + `test_cloud_estimate` + `test_quality_modes` + `test_promptlab` + `test_shot_lab` + `test_cli` + the three new files — zero regressions.
- **Broad safety sweep (215 passed):** `test_doctor` + `test_comfyui` + `test_local_cmd` + `test_dr03c_lifecycle` + `test_dr03b_characterization` + `test_batch` + `test_job_cancel` + `test_evaluate` + `test_e2e_m0` + `test_savings` + `test_spend_delta` + `test_compiler` + `test_check` + `test_final_trust_round`, plus `test_mcp`/`test_hash_versions`/`test_dr03a`/`test_openclap_cli`/`test_create_page`/`test_interconnection`/`test_experience`/`test_spend`/`test_gui_pages`/`test_round_w` (132 more passed). The only failures anywhere in the sweep are the **4 pre-existing `include_unindexed` test-double-drift** failures (`test_director` ×2, `test_round_w_agent_wb` ×1, `test_write_consistency` ×1) — all the identical `fake_run_build() got an unexpected keyword argument 'include_unindexed'` in `gui/plan.py:125`, a file this batch never touched. They are OUT of this batch's set and untouched. **Zero new failing test names.**

### Contract §11 coverage (38 items)

| Items | Area | Suite | Result |
|---|---|---|---|
| 1-10 | projection determinism (schema, digest, cred-presence/mtime exclusion, no secrets/paths, names-only, broken→errors, sorted, profile_id, delete/rebuild safe) | `test_dr04_catalog::test_01…test_10` | ✅ |
| 11-18 | manifest/profile (built-ins local, disabled, max_resolution verbatim, structured-limits not invented, no capability_profiles overlay, profile digest stable + moves, source digest + free_probe) | `test_dr04_catalog::test_11…test_18b` | ✅ |
| 19-32 | request compatibility (compatible/not-registered/disabled/capability/duration/zero-submit/placeholder/omissions/same-effective-refs/unknown-legacy/legacy-resolution-warn/first-last/never-infers) | `test_dr04_preflight::test_19…test_32` | ✅ |
| 33-38 | routing/CLI/regression (skip visible reason, explicit-pin fail-before-submit, cheapest compatible-only, catalog CLI + --output, check sections, default-order byte-identity + explain skip) | `test_dr04_preflight::test_33…test_38` + `test_dr04_catalog::test_36/37` | ✅ |

---

## Deviations (with reasons)

| Deviation | Reason |
|---|---|
| `registry.py` touched (a 6th file, not in the spec's ~5 list) | The explicit-pin fail-before-submit belongs next to the existing disabled-pin guard (`generate_with_fallback`), which lives in the registry — the correct enforcement point. Within the 10-file cap. |
| §8.4 fallback chain deliberately NOT compatibility-filtered | "fallback ORDER unchanged" is a hard contract rule; the promptlab ceiling linter (`qc/prompt_checks.py:176`) reads the whole chain, so trimming it regresses a real feature. Fail-earlier applies to strategy selectors + the explicit-pin guard; the submit-time guard defends the safety-net path. |
| No dedicated `explain` edit | The ruling-5 skip reasons flow through the existing `Resolution.skipped` → `explain` with zero explain-side change — the ≤10-line additive intent is met at 0 lines (test 38 pins it). |
