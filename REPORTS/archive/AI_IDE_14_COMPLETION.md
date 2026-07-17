# AI_IDE_14 — Completion: Real-Provider Qualification & Canary

> Formed: 2026-07-11 · Branch: `claude/cost-optimization-strategy-cjfmn5`
> Contract: `Manju_AI_IDE_14_Real_Provider_Qualification_and_Canary_Compact_v2`
> Baseline: `REPORTS/AI_IDE_14_BASELINE.md`

## 1. What landed

A **derived-only** qualification layer + a **fake-transport-first, operator-
gated** canary, on top of the eleven landed batches. No second registry, ledger
or spend system (§1/§11).

### 1.1 Qualification state machine + bindings (`providers/qualification.py`)

`qualification_state(provider_id, capability, *, evidence, declared)` — a PURE,
I/O-free, clock-free derivation of the 8-rung ladder with STALE/BLOCKED modelled
as overlays `(level, stale, blocked_reason)`:

```
UNTESTED → CONFIG_VALID → DRY_RUN_VALID → CANARY_SUBMIT_PASSED
        → CANARY_ARTIFACT_PASSED → RECOVERY_PASSED → PRODUCTION_READY
STALE   = a staleness anchor moved (recorded evidence drops to the config floor)
BLOCKED = provider absent / manifest broken
```

- **CONFIG_VALID** is the live floor (manifest exists + `validate_for_generic`
  passes) — derivable with zero network.
- **DRY_RUN_VALID** adds the fixture + estimate, still no network.
- **CANARY_SUBMIT/ARTIFACT/RECOVERY** come only from non-stale canary evidence.
- **PRODUCTION_READY** is minted ONLY when evidence records `transport ==
  "real"`; a scripted run is capped at RECOVERY_PASSED **in the pure function
  itself**, so the honesty invariant holds regardless of who wrote the evidence.

**Bindings (§3):** provider id · capability · `provider_profile_digest` (REUSED
from DR04 `providers.catalog`) · `adapter_semantic_digest` = `hash_text` of the
adapter class source (moves on a real adapter edit) · `fixture_version` · 
`request_digest` (REUSED from `providers.submission`) · `response_schema_digest`
· evidence refs · artifact probe facts · cost. **Staleness anchors** = profile /
adapter / fixture / request / response-schema digests. **The check DATE is audit
metadata only — never in bindings/identity** (pinned by a test).

### 1.2 Canary project + report layout (addendum ruling 2)

- The canary runs inside a **disposable canary project** auto-created under the
  user project's runtime area: `.manju/canary/<provider>__<capability>.manju/`.
  Deleting it loses qualification HISTORY only, never truth.
- The canary generate goes through the **STANDARD** `GenerationRequest` →
  admission → evidence path (`CloudProvider.generate`) — **zero new ledger**.
- The report is a **deletable derived JSON**:
  `reports/providers/qualification/<provider>__<capability>.json`. A test proves
  no build/resume/cache/render module reads it, and that deleting it changes no
  projection digest.

### 1.3 Canary fixtures (addendum ruling 3) — `tests/fixtures/canary/`

Small YAML contracts per §5 (`fixture_id`, `max_estimated_cost`,
`expected_capability`, `expected_duration_range_ms`, `expected_frame_size`,
`required_receipt_fields`, `forbidden_secret_fields`) + tiny **deterministic,
non-private** media: a hand-built 64×64 solid-teal PNG, a 1s 64×64 clip, a 0.3s
tone WAV. Five capabilities: video i2v, image t2i, tts, asr, vlm-review. The
**fixture file hash (yaml + bundled media) is the `fixture_version`** binding, so
editing the contract OR the input moves it (staleness). Lip-sync: absent in this
repo → N/A. **`tests/fixtures/golden/` (the parallel agent's) was never touched.**

### 1.4 CLI surface (contract §9) — extends the existing `providers` group

```
manju providers qualify <id> --capability C --dry-run [--json]
manju providers qualify <id> --capability C --run --max-cost X [--yes] [--json]
manju providers qualification [<id>] [--capability C] [--json]
```

`--run` is refused (spend 0) when `--max-cost` is absent, when the estimate
exceeds `--max-cost` (or the fixture's own cap), and passes the existing
`ask_before` spend gate unless `--yes`. Fallback to a second provider is
**hard-disabled** — the canary builds `GenericCloudProvider` directly (never
`generate_with_fallback`): a single `submission_id`, a chain of one.

## 2. WP-by-WP

- **WP1 canary contract** — fixtures per §5; `--dry-run → human confirm (ask_
  before) → single submission_id → no fallback` enforced.
- **WP2 run semantics** — REUSED, not re-implemented (P0/FA): preflight-fail =
  transport 0; DEFINITELY_REJECTED vs OUTCOME_UNKNOWN; poll-retry never
  resubmits; corrupt/HTML download fails closed; RECOVERY_PASSED = re-run the
  existing drill (delete SQLite → `runtime.state.rebuild` → poll-only resume,
  resubmit count 0) against the canary project.
- **WP3 empirical vs declared** — the report renders Declared / Observed /
  Not-tested lines (§7); the manifest is never written back; a 1s canary never
  claims the declared 15s boundary.
- **WP4 health/quota + schema drift + data-handling** — a GET-only health-probe
  SLOT that never triggers submit (real free endpoints SKIPPED_WITH_EVIDENCE —
  none documented in-repo); response-schema fingerprint → STALE on drift;
  additive optional manifest `data_handling` fields (region/retention/training_
  opt_out/deletion_url/source_ref) surfaced VERBATIM as `declared`, `verified:
  false` — never an observed/verified fact.

## 3. WP0 matrix — the honest ceiling (no real account here)

| State | Providers |
|---|---|
| CONFIG_VALID | built-in local: `caption_card`, `ffmpeg_kenburns`, `manual_import` |
| DRY_RUN_VALID | any configured cloud provider after `qualify --dry-run` (ceiling without a real account) |
| CANARY_* / RECOVERY_PASSED | reached **only** via the scripted canary in the test suite (transport≠real) |
| PRODUCTION_READY | **unreachable here** — needs a real operator canary |

There are no cloud manifests in the shipped repo, so cloud capabilities are
UNTESTED until configured; a representative `generic_cloud` provider tops out at
**DRY_RUN_VALID**. This is the CORRECT outcome per the environment fact, not a gap.

## 4. The 19 contract checks (§10) — all covered in `tests/test_c14_qualification.py`

1 profile-digest→STALE · 2 fixture→STALE · 3 dry-run touches no transport · 4
budget/confirm refusals = transport 0 (3 codes) · 5 canary = chain of one, no
fallback · 6 DEFINITELY_REJECTED vs OUTCOME_UNKNOWN on the canary · 7 poll
retries never resubmit · 8 artifact **hashed + ffprobed** (real 64×64 / 1000ms
validated) · 9 corrupt/HTML download fails closed · 10 delete SQLite → poll-only
resume (resubmit 0) · 11 malformed evidence → no false pass + torn-line fail-
closed · 12 no secret / absolute-path leak · 13 report binds exact profile /
adapter / fixture / request · 14 untested boundary not claimed · 15 report is not
a build input · 16 response-schema drift → STALE · 17 health probe never triggers
submit · 18 canary inputs deterministic + non-private (solid-color proof) · 19
data-handling declared, never verified. Plus pure-derivation, CLI-surface, and
matrix-ceiling tests.

## 5. Files (production ≤6: **3 used**)

| File | Change |
|---|---|
| `src/manju/providers/qualification.py` | **new** — pure derivation + orchestration + report projection + matrix |
| `src/manju/providers/manifest.py` | **additive** — optional `DataHandlingConfig` + `data_handling` field (byte-identical for existing manifests) |
| `src/manju/cli.py` | **additive** — `providers qualify` + `providers qualification` |
| `tests/test_c14_qualification.py` | new tests |
| `tests/fixtures/canary/*` | new fixtures + deterministic media |

No `providers/catalog.py` hook was needed (digest reused directly). No
`DECISIONS.md` / `README.md` touch. No commit/push.

## 6. Test results

- **`tests/test_c14_qualification.py`: 26 passed** (the 19 contract checks +
  pure-derivation, CLI-surface, and matrix-ceiling tests).
- **Full local suite: 2817 passed, 12 skipped, exit 0** (`python -m pytest`,
  15m32s). Baseline before this batch was 2768 passed; the increase is this
  batch (26) plus the parallel 20A corpus agent's tests — **no regressions**.

## 7. Honest environment limitations recorded

- **No real provider account/API key** → the whole real-canary path is exercised
  against scripted transports; PRODUCTION_READY is unreachable here by design.
- **No free, documented health/quota endpoint** in any in-repo manifest → the
  live probe is a tested SLOT, real probes SKIPPED_WITH_EVIDENCE.
- **ffprobe-backed artifact facts** are real (ffmpeg present) — the scripted
  transport serves the committed deterministic clip, so `content_sha256` and the
  64×64 / 1000 ms probe are genuine, not stubbed.
- Response-schema staleness has a real per-run fingerprint; a LIVE re-probe to
  auto-flip STALE needs a real account (the anchor + its firing are proven, the
  live feed is operator-run).

---

## 14_21 CLOSEOUT 更正块（2026-07-12）

1. **报告曾兼任 admission 记忆（缺陷）**：§1.2 称报告是「deletable derived JSON」——但 14 的 `_stored_evidence` 恰恰从该报告 JSON 重读 evidence 喂 admission，即这份可编辑投影实为准入权威：伪造/编辑报告可扩权，删除报告即抹除资质（closeout Q06–Q08 红灯证实）。closeout 起 admission 从 events.jsonl 上按 (provider, capability) hash 链接的追加式 qualification evidence 重新物化（`record/read_qualification_evidence`，_finish 先写 durable evidence 再写投影），报告仅为显示投影——伪造/编辑/删除零影响；证据流损坏 fail-closed（`BLOCKED(evidence_corrupt)`，transport 0，Q09）。**派生报告绝不作为 authorization input**（contract §8 item 5）。
2. **其余更正**：`enabled==false` ⇒ `BLOCKED(provider_disabled)`（Q01）；未声明 capability ⇒ 阻断（Q02）；缺失强制 staleness anchor = STALE 而非「无漂移」（Q03–Q05）；reviewer/analyzer/bridge 地板改为——无人值守/付费 `PRODUCTION_READY`、人工交互 `CANARY_ARTIFACT_PASSED`、bridge `PRODUCTION_READY` + 一次性 digest 绑定 risk acceptance（Q10–Q12）；capability 精确隔离定桩（Q13/Q14）。
