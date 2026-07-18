# AI_IDE_14 — Baseline (WP0 audit): Real-Provider Qualification & Canary

> Formed: 2026-07-11 · Branch: `claude/cost-optimization-strategy-cjfmn5`
> Contract: `Manju_AI_IDE_14_Real_Provider_Qualification_and_Canary_Compact_v2`
> Discipline: audit-first · red-first · derived-only · fake-transport-first,
> operator-gated real calls.

## 1. Scope confirmed (what this batch adds, and does NOT add)

AI_IDE_14 upgrades "manifest loads + preflight passes" into an **evidence-bound
qualification level** earned by a fixed, low-cost, private-material-free
**canary** that runs through the STANDARD admission/evidence path. Per contract
§1/§11 it builds **no** second provider registry, task ledger or spend system —
it is a derived projection on top of the eleven landed batches.

**Environment fact (baked in honestly).** This environment has **no real
provider account / API key**. The entire real-canary path is therefore landed
and tested against **scripted transports** (the DR06 admission-sandbox pattern);
the qualification matrix tops real cloud providers out at **CONFIG_VALID /
DRY_RUN_VALID** — the correct outcome, not a gap. `PRODUCTION_READY` is
reachable only by a real operator running a real canary later (evidence must
record `transport == "real"`).

## 2. Existing machinery located for REUSE (WP0 §4)

Everything the canary needs already exists on the branch and is reused verbatim
— the canary is a thin driver, not a re-implementation:

| Contract asks for | Reused from | How the canary uses it |
|---|---|---|
| ProviderCapabilityProjection | `providers/catalog.py` | `provider_profile_digest()` = the profile-digest binding (§3) |
| Request/SubmissionIdentity | `providers/submission.py` | `request_digest`, the 8-state admission machine, event chain |
| Attempt / RunManifest evidence | `build/attempts.py` | `append_submission_event` / `read_submission_events` = evidence refs |
| Admission write order + CAS | `providers/base.py::CloudProvider.generate` | canary submit→poll→download→register, unchanged |
| Config-driven cloud adapter | `providers/generic_cloud.py` | injectable `transport` = the fake/real seam; artifact download |
| cost / ask_before / budget | `build/graph.py::spend_gate` | the operator spend gate on `--run` |
| providers check/doctor | `providers/manifest.py::validate_for_generic` | the CONFIG_VALID floor |
| secret redaction + HTML-page guard | `submission.py`, `generic_cloud.py` | no secret/abs-path leak; corrupt-download fail-closed |
| fail-closed recovery / rebuild | `runtime/state.py::rebuild`, P0/FA drills | RECOVERY_PASSED = re-run the drill on the canary project |
| ffprobe → ProbeInfo | `media/probe.py` | artifact frame-size / duration facts (test 8) |

## 3. WP0 qualification matrix (capability × current level, with evidence)

Derived live by `qualification_matrix()`. **(A)** is the repository as shipped
(no cloud manifests configured); **(B)** adds a representative cloud video
provider to show the honest ceiling.

**(A) Built-ins only — the shipped state**

| Provider | Capability | Kind | Level | Evidence |
|---|---|---|---|---|
| `caption_card` | video | local | CONFIG_VALID | built-in; no manifest fill; not a paid-canary target |
| `ffmpeg_kenburns` | video | local | CONFIG_VALID | built-in local generator |
| `manual_import` | manual | local | CONFIG_VALID | built-in; human import path |

There are **no cloud provider manifests** in this repo, so every CLOUD capability
(video/image/TTS/ASR/VLM-review) is **UNTESTED** until a provider is configured —
the honest baseline.

**(B) + a representative cloud video provider (`generic_cloud`, key present, no real account)**

| Provider | Capability | Kind | Level | Why |
|---|---|---|---|---|
| `kling_like` | image_to_video | cloud | **DRY_RUN_VALID** | config valid + dry-run estimate passed, NO network |
| `kling_like` | text_to_video | cloud | CONFIG_VALID | config valid; that capability not yet dry-run |

**Ceiling without a real account: DRY_RUN_VALID.** A scripted canary run in the
test suite provably reaches CANARY_SUBMIT_PASSED → CANARY_ARTIFACT_PASSED →
RECOVERY_PASSED against the fake transport; it is capped BELOW PRODUCTION_READY
because `transport != "real"`. Lip-sync: **absent** in this repo (no adapter/
capability) → recorded N/A, not a gap (contract §5 "若存在").

## 4. Qualification state machine + bindings (as designed)

8-rung ladder with STALE/BLOCKED as overlays `(level, stale, blocked_reason)`:

```
UNTESTED → CONFIG_VALID → DRY_RUN_VALID → CANARY_SUBMIT_PASSED
        → CANARY_ARTIFACT_PASSED → RECOVERY_PASSED → PRODUCTION_READY
overlays: STALE (a staleness anchor moved) · BLOCKED (absent/broken manifest)
```

Bindings (§3): provider id, capability, `provider_profile_digest` (REUSED from
DR04), `adapter_semantic_digest` (source hash of the adapter class),
`fixture_version`, `request_digest`, `response_schema_digest`, evidence refs,
artifact probe facts, cost. **Staleness anchors** = profile / adapter / fixture
/ request / response-schema digests. **The check DATE is audit metadata only —
never in the identity.**

## 5. Red-first: the 19 contract checks (§10) and where each binds

Each has a test in `tests/test_c14_qualification.py`. Items whose runtime
behaviour the P0/FA batches already proved (6 disposition, 7 poll-retry, 9
corrupt download, 10/11 recovery) are exercised THROUGH the canary path so the
qualification BINDING gets its own proof, citing the existing guards rather than
re-implementing them. Items that cannot bind in this environment
(PRODUCTION_READY; a live free health/quota endpoint — none is documented in
this repo) are **SKIPPED_WITH_EVIDENCE** with the reason recorded in the report.

## 6. Files (planned, ≤6 production)

- `providers/qualification.py` (new) — pure derivation + canary orchestration + report projection
- `providers/manifest.py` (additive) — optional `data_handling` declaration fields (WP4)
- `cli.py` — `providers qualify` + `providers qualification`
- Fixtures (own dir, parallel agent untouched): `tests/fixtures/canary/`
- Tests: `tests/test_c14_qualification.py`

## 7. Stop conditions honoured (§11)

No second registry; no online model discovery; no API-key storage; the report is
never a build input; no user-config auto-edit; the real canary is hard cost-
bounded (`--max-cost` required, estimate-vs-cap enforced); UNKNOWN is never
resubmitted (the canary is a chain of one and reuses the fail-closed recovery).
