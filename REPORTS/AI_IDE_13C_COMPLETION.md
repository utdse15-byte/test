# AI_IDE_13C Completion — manju.delivery-manifest/v1

One derived, deletable delivery manifest over the artifacts Manju already
produces. Pure derivation, red-first, 2 production files touched (cap 8), exactly
one new public schema.

## Existing systems reused
- **exports:** `build/exportstatus.deliverables()` / `deliverables_data()` — the
  ONE status owner. Every artifact row is a `DeliverableRow` mapped to a role;
  no second staleness path is created (test: manifest artifact count == nine
  deliverables; blocker parity with `release_assessment`).
- **timeline/source resolver:** compiled `Timeline.tracks.video` via
  `exportstatus._gather` (the single recompile). NLE media bound scan-free with
  `Project.resolve(clip.source)`; selection stays owned by
  `gather_compile_input`/`evaluate_all`.
- **NLE adapters:** `exporters/otio|jianying|native_draft` outputs are described,
  never rewritten. The manifest only ADDS the `asset_sha256` + integer frame
  mapping the adapters lack.
- **locale:** `core/locale.locale_status` / `line_status` / `load_lines`
  consumed verbatim; `翻译过期` ⇒ STALE.
- **package/pack:** cover/teaser via the exportstatus rows; bundle reuses
  `media.ffmpeg.atomic_output` + `Project.resolve` (pack's guards are inline in
  cli.py, not importable — see deviation).
- **verification/run evidence:** `mark_verified` (human draft axis) and 07C
  `release_assessment` (run/QC/submission honesty) — never re-derived.

## DeliveryManifest
- **schema and materializer:** `build/delivery.py` — `SCHEMA =
  "manju.delivery-manifest/v1"`, `build_manifest()` (instant, read-only,
  no wall-clock field), `materialize_manifest()` (atomic, into `reports/delivery/`,
  optional). `manifest_id = "derived:" + manifest_digest`.
- **artifact roles actually supported:** MASTER_VIDEO, PROXY_VIDEO, CAPTIONS_SRT,
  CAPTIONS_ASS, NLE_OTIO, NLE_JIANYING, NLE_CAPCUT, POSTER, TEASER — one per real
  deliverable. Recognised-but-unpopulated (no real artifact exists):
  CAPTIONS_VTT, DIALOGUE/MUSIC/SFX/FULL_MIX stems, TEXTLESS/M_AND_E masters,
  LOCALIZATION_SOURCE, PLATFORM_METADATA, OTHER_DECLARED (§6.3 — no empty files
  pre-generated).
- **status layering:** per-artifact `state ∈ GENERATED / TECHNICALLY_VERIFIED /
  HUMAN_VERIFIED / STALE / MISSING / INVALID / BLOCKED` (from the row's
  `Freshness`); per-delivery `delivery_state = {technical_ready, editor_approved,
  publish_handoff_ready, published:"unknown_not_owned"}`. `file-exists→ready`,
  `technical→human`, `handoff→published` are all provably separated (tests).
- **proof it is not an input:** deleting or hand-editing the materialized file
  leaves `deliverables_data` byte-identical AND the manifest rebuilds to the same
  `manifest_digest` (test `test_manifest_delete_and_hand_edit_are_inert`). build/
  export/cache never read it.

## Variants
- **profile source:** additive `delivery_profiles` map on project.yaml (pydantic
  `extra="allow"`, zero models.py change). Absent → default MASTER (old-project
  compat pinned). `variant_kind ∈ MASTER/FORMAT_ONLY/EDITORIAL_CUTDOWN/LOCALIZED/
  PLATFORM_PACKAGE`.
- **format-only invariant:** `timeline_semantic_digest` over ONLY
  {shot, take, source, start_ms, duration_ms, source_in_ms} (excludes w/h/fps) —
  so 16:9 and 9:16 share it, a dropped/reordered/retimed segment moves it.
  `check_format_only_invariant` emits `VARIANT_KIND_MISMATCH` (blocks the
  format-only labelling; a manifest diagnostic, never a build failure).
- **cutdown source boundary:** an EDITORIAL_CUTDOWN without an explicit
  `cutdown_source.ref` (timeline revision / approved roundtrip / approved
  proposal / sibling source) is rejected (`CUTDOWN_SOURCE_REQUIRED`). 13C only
  packages an existing cut; no `SegmentDecisionList`, no auto semantic cutdown.
- **framing implementation or skipped:** SKIPPED_WITH_EVIDENCE (execution). The
  current renderer exposes no per-profile crop/pad capability, so framing is
  **recorded** as a variant fact (`variant.frame.strategy`, `applied_by:
  "declared_only"`) and never drives render (§8.3). An external smart-framing
  artifact is recorded inert (`drives_render:false`, `EXTERNAL_FRAMING_NOT_ADOPTED`
  until adopted into source). Extending the renderer was rejected as
  editorial-engine work beyond this batch.

## NLE
- **exact media/hash/frame binding:** for each compiled `VideoClip`, the `nle`
  section binds `asset_sha256 = hash_file(Project.resolve(clip.source))`,
  integer `source_in/out_frames` + `timeline_in/out_frames` (round(ms·fps/1000)),
  `timebase{fps_num,fps_den,drop_frame}`, `handles{0,0}` (never guessed). Media
  comes from the compiled timeline, never a directory scan (tests 11/14/16).
- **adapter tests:** the chosen NLE project file (otio→jianying→capcut, whichever
  exists) is hashed; missing/changed media ⇒ `NLE_MEDIA_UNRESOLVED`; an older
  draft than the current selected take ⇒ `NLE_STALE`.
- **human verification:** `opened_in_target_app` is a SEPARATE axis from
  `adapter_roundtrip`; only a `mark_verified` draft (`HUMAN_VERIFIED`) yields
  `editor_approved` — `technical_ready` alone never does (test
  `test_editor_approved_only_after_human_nle_verification`).

## Localization
- **base hash/source IDs:** `localization` mirrors `locale_status` —
  `base_text_hash` (stored), `status ∈ CURRENT/STALE/INCOMPLETE/UNAVAILABLE`,
  `source_ids` (shot-based, the only ids that exist), caption/voice artifact ids.
  A stale base hash blocks a LOCALIZED variant. No auto-translate / no TTS
  (test asserts building the manifest creates no files).
- **picture change semantics:** `picture_timeline_changed` is explicit; a
  localized editorial variant is never disguised as pure localization.

## Platform handoff
- **no-credential proof:** `credentials_present` is a REAL scan
  (`core.check.SECRET_PATTERNS` + `providers.submission` signed-URL detector)
  over the user-provided metadata; a hit ⇒ `CREDENTIALS_PRESENT` FAIL +
  `PLATFORM_CREDENTIAL_LEAK` and the token never enters the manifest (only the
  project-relative path). No credential/token/cookie is ever stored.
- **upload status:** NOT_IMPLEMENTED (`upload_supported:false` always; no login,
  no upload, no schedule; `DISCLOSURE_REVIEW` stays `PENDING_HUMAN`; unknown
  platform rules surface `rules_freshness:UNKNOWN`).

## Bundle
- **entries/checksums/path safety:** `write_bundle` packs ONLY manifest-registered
  files + `SHA256SUMS` + the manifest JSON; rejects `..`/absolute/backslash names,
  symlinks, and duplicate entries; stable (sorted) entry order; SHA256SUMS over
  exact bytes (coreutils format, the first such writer in the repo). Atomic via
  `atomic_output` (a failure leaves any prior bundle intact — test). Distinct
  from `manju pack` (never carries bible/shots/.manju/config).
- **reproducibility:** entry set, order, checksums and zip bytes are deterministic
  (fixed 1980 archive timestamp, fixed perms). Byte-identical on re-pack (test
  `test_bundle_is_reproducible`) — full determinism achieved, no honesty caveat
  needed.

## No-duplication proof
- no NLEHandoff schema: `nle` is a manifest SECTION; no `NLEHandoffV*` class/id.
- no VariantPlan schema: variants are additive project.yaml fields.
- no SegmentDecisionList schema: cutdowns reference an existing source revision.
- no LocalizationPackage schema: `localization` is a manifest SECTION over locale.
- no PublishReceipt schema: `platform_handoff` is credential-free, upload NOT_IMPLEMENTED.
- Enforced by `test_only_one_new_public_schema_no_parallel_objects` (exactly one
  `manju.*/v*` id defined; no parallel class).

## Verification
| command | result |
|---|---|
| `python -m pytest tests/test_c13_delivery.py -q` | 35 passed |
| `python -m pytest tests/test_c07_baseline.py tests/test_export_center.py tests/test_cli.py tests/test_container.py -q` | 117 passed (no regression) |
| `python -m pytest -q` (full suite) | **2701 passed, 12 skipped, 0 failed** (779s) |

The 12 skips are pre-existing, environment-gated (Chromium / pycapcut / network),
none introduced by this batch. Zero unexplained failures (§13.5.46, §15.11).

## Budget
- new public schemas: **1** (`manju.delivery-manifest/v1`).
- production files changed: **2 / 8** — `src/manju/build/delivery.py` (new),
  `src/manju/cli.py` (extend `exports` with `--manifest`/`--bundle`/`--profile`/
  `--metadata`/`--output`). No models.py / exporters / packaging / locale / check
  touch (all consumed by import). Tests + fixtures + these reports are not
  production code.

## Deviations (honest)
- **Framing execution SKIPPED_WITH_EVIDENCE** — recorded as a variant fact only;
  the renderer is not extended (editorial-engine scope). §4.2/§8.2 format-only is
  fully verifiable via the semantic digest without it.
- **Stem / textless / M&E / VTT roles SKIPPED_WITH_EVIDENCE** — no such artifacts
  are produced today; roles are recognised but never populated (§6.3).
- **pack/unpack zip guards not imported** — they are inline in `cli.py`, not an
  importable module (audit fact), and pack is not atomic / has no dup guard. The
  bundle instead reuses the importable primitives (`Project.resolve`,
  `atomic_output`) and enforces §11.3 itself (stricter than pack). Lifting
  cli.pack into a shared helper was rejected (frozen surface + budget).
- **No MCP tool added** — CLI-first per addendum ruling 11; the existing export
  tool's DR05 policy is untouched.
