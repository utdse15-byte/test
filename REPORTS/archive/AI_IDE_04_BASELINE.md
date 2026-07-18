# AI IDE 04 Baseline

WP0 audit for **Manju Deep Research 04 — provider capability projection & pre-paid preflight**. Paired completion: `REPORTS/AI_IDE_04_COMPLETION.md`. Base commit `cb7305d` (DR03C reports landed).

The gate **PASSED → BUILD** (not `ALREADY_IMPLEMENTED`). No shared `providers/catalog.py`, no `providers/preflight.py`, no `manju.provider-capability-projection/v1` / `manju.request-compatibility/v1` schema, no `providers catalog` subcommand, and no profile digest exist anywhere in `src/manju` (grep-zero). The *facts* a projection needs are all present but split across four surfaces that never converge; the duration rule is inlined in the submit path only; nothing is spend-free-preflighted before a POST.

---

## §6.3 verdict table (audited, file:line)

| # | Capability the contract asks for | Where it lives today | Verdict |
|---|---|---|---|
| 1 | Provider FACT gathering (built-ins + manifests, disabled included, broken → errors) | routing `_catalog()` **routing.py:250-281** (private) + `load_manifests()` **manifest.py:415-435** | **PARTIAL** — exists but PRIVATE to routing; no shared module, no projection |
| 2 | `_ProviderView` fields (kind/caps/per_second/per_call/disabled/exists/adapter) | **routing.py:238-248** | **PARTIAL** — routing-internal; lacks `max_duration_ms`, no digest |
| 3 | Broken manifest never hides healthy | `load_manifests` errors list **manifest.py:428-434**; `_manifest_state` **registry.py:97-98** | **PRESENT** — reused verbatim |
| 4 | Duplicate manifest id handling | **manifest.py:431-433** | **PRESENT** |
| 5 | Capability projection schema `…/v1` (profile_id, limits, refs, cost, credential names, free_probe, digest) | — | **MISSING** |
| 6 | `projection_digest` / `provider_profile_digest` (stable, excludes paths/mtime/cred presence) | `hash_value`/`canonical_json` **core/hashing.py:23-31** exist; no projection uses them | **MISSING** (hasher PRESENT) |
| 7 | Duration cap rule (`max_duration_ms`) | inlined in `generic_cloud._enforce_limits` **generic_cloud.py:697-704** (`if limit and duration_ms > limit`), called from `submit()` **:362** | **PARTIAL** — one call site, not extracted/pure/shared |
| 8 | `max_resolution` semantics | field def ONLY **manifest.py:68**; grep-zero readers | **MISSING semantics** → confirms opaque, surface-verbatim ruling |
| 9 | Structured limits (`max_width`/`max_height`/`allowed_frame_sizes`) | grep-zero (no field, no reader) | **ABSENT** → conditional item, SKIPPED_WITH_EVIDENCE |
| 10 | `capability_profiles` overlay | grep-zero | **ABSENT** → conditional item, SKIPPED_WITH_EVIDENCE |
| 11 | body_template placeholder vocabulary + completeness | `_placeholder_map` **generic_cloud.py:168-185**; `render_body` refuses unknowns **:238-267** | **PARTIAL** — refusal happens at submit only, never pre-flighted |
| 12 | Refs resolve + budget (the effective refs) | `resolve_refs`/`RefSet` **refs.py:157-180**; `allocate`/`BudgetReport` **refbudget.py:272-296**; consumed in `_deliver_refs` **generic_cloud.py:497-569** | **PRESENT** — the ONE allocator; must be reused, never duplicated |
| 13 | first/last-frame support signal | `FIRST_LAST_CAPABILITY` + `refs.first_last_mode` **manifest.py:193-243**; delivery **generic_cloud.py:571-638** | **PRESENT** — capability + mode readable |
| 14 | Cost estimation | `estimate_cost` **manifest.py:438-441**; `_estimate_shot_cost` **graph.py:463-492** (candidates-clamped #29) | **PRESENT** — reused for the cost block |
| 15 | Routing skip ledger | `Resolution.skipped` **routing.py:373-387**; `resolve` add()/skip **:434-507**; `explain` **:526-543** | **PARTIAL** — records disabled/not-registered/no-match; NO compatibility skip |
| 16 | Explicit-pin hard fail before submit | disabled pin raises **registry.py:192-199**; incompatible pin **degrades** (caught at submit → fallback) | **PARTIAL** — disabled only; incompatible silently degraded |
| 17 | `cheapest` candidate comparison | `_capable_sorted(by_cost)` **routing.py:390-410** | **PARTIAL** — compares ALL capable, incl. incompatible |
| 18 | providers CLI (`list/add/check/enable/disable/show`) | **cli.py:4915-5142**; `check` **:5020-5068** | **PRESENT** — extend, do not replace; no `catalog`, no structured `check` sections |
| 19 | Secret masking / no-value discipline | `_mask_secrets`/`_scrub_url_query` **cli.py:4883-4899**; §8.2 key-env-name-only | **PRESENT** — the projection reuses the same discipline (names only) |
| 20 | doctor probe / `--live` reachability | `validate_for_generic` **manifest.py:287-354**; `reachability_probe` **manifest.py:679-713** | **PRESENT** — `free_probe_available` derives from it (comfyui/system_stats or ping_url) |

**Conclusion → BUILD.** Every FACT exists; the *projection*, the *spend-free preflight*, the *shared duration rule*, the *profile digest*, and the *compatibility-aware routing/CLI* are MISSING. `manifest.py` is authoritative and complete — no field additions are warranted (items 9/10 have no fixture).

---

## Must-preserve invariants (pinned)

- **Routing byte-identity** — `test_providers_routing` pins order (explicit > rule > tier > else > §8.4 fallback), the cheapest real-price ranking (#30), user↔project precedence, unknown-match-key errors. The extraction must leave `_catalog()`'s facts and `resolve()`'s order identical for any in-cap shot.
- **generic_cloud submit-time guard** — `test_generic_cloud::test_duration_over_limit_rejected` + the throttle/refs/first-last/network-hygiene tests. The duration guard must remain the final defense and fire before any POST (`submit()` calls `_enforce_limits` at **:362** before the transport at **:376**).
- **Refs single-allocator** — `test_refs`/`test_refbudget` pin the byte-identity path (no budget ⇒ all refs, zero omissions). Preflight must REUSE `resolve_refs`+`allocate`, never a second allocator.
- **`load_manifests` contract** — broken manifest → error, healthy retained; duplicate id → error. Old `provider.yaml` files load unchanged (additive only).
- **`_estimate_shot_cost` / `_target_duration_ms`** — the SAME estimators dry-run/plan use (graph.py:396/463); the projection's cost block reuses `estimate_cost`.

---

## §6.4 characterization list (the 10, written BEFORE any change)

`tests/test_dr04_characterization.py` — 12 pins (10 required + 2 boundary): (1) `_catalog` manifest facts; (2) disabled flag; (3) built-ins present as `local`; (4) broken-manifest-excluded/healthy-retained; (5) duplicate-id load error; (6) §8.4 default-order byte-identity; (7) cheapest real-price order; (8) duration guard raises `invalid` with **zero transport submits**; (8b) boundary `duration==limit` passes the guard; (9) `render_body` refuses unknown placeholder; (10) placeholder vocabulary; (11) refbudget byte-identity (no budget ⇒ all refs, no omissions). All 12 GREEN on the untouched tree — they pin the surface the extraction must not move.

## Conditional-item evidence (verified in WP0)

- **`max_resolution` opaque** — grep across `src/`+`tests/` finds exactly ONE hit: the field definition `manifest.py:68`. No reader, no test, no semantics. → surface VERBATIM + `LEGACY_UNINTERPRETABLE_LIMIT` warning, never block. (contract §8.6 option 2)
- **Structured limits** (`max_width`/`max_height`/`allowed_frame_sizes`) — grep-zero. No manifest field, no failing fixture. → **SKIPPED_WITH_EVIDENCE** (no field added).
- **`capability_profiles` overlay + param type/enum/range contracts** — grep-zero; no real manifest ships ≥2 capabilities with genuinely conflicting top-level limits. → **SKIPPED_WITH_EVIDENCE** (no manifest field added; each capability echoes the provider-level limits).

Full red-first outcomes and the delivered API are in `REPORTS/AI_IDE_04_COMPLETION.md`.
