# AI_IDE_13C Baseline — DeliveryManifest, NLE handoff & variant boundaries

Document: `AI_IDE_13C` (replaces `AI_IDE_13R v2`). Branch
`claude/cost-optimization-strategy-cjfmn5`, HEAD after 07C (`bff287f`) +
08_10_12C (`e000ea1`). Audit-first, red-first. One new public schema:
`manju.delivery-manifest/v1`. Production-code budget: 8 files.

Non-negotiable stance confirmed before writing a line: the manifest is a
**derived delivery statement**, never an editorial decision and never an export
input. Creative cut / order / duration / caption text / voice content stay owned
by the existing source/timeline/roundtrip.

---

## WP0 audit — every subsystem the manifest must reuse (§5.1)

| Subsystem | Where | Status | Evidence / how it is reused |
|---|---|---|---|
| Export status roles / staleness / human verification | `build/exportstatus.py` `deliverables()` / `deliverables_data()` / `mark_verified()` | ALREADY_IMPLEMENTED | The ONE status owner. Nine rows (final/proxy/srt/ass/otio/jianying/capcut/cover/teaser), `Freshness` vocab, verifications.jsonl. Manifest maps rows→artifacts; **no second staleness path**. |
| 07C release assessment | `build/baseline.py` `release_assessment(project, rows=)` | ALREADY_IMPLEMENTED | Consumed VERBATIM (§12). Real vocab = a `ready` bool + blocker codes (`RUN_INCOMPLETE`, `CURRENT_FINAL_STALE`, `SUBMISSION_OUTCOME_UNKNOWN`, `REQUIRED_EXPORT_*`, `QC_*`…), `baseline.status ∈ VALID/NO_BASELINE/DAMAGED/CORRUPT`, `regression_review.status`. Manifest folds these into its `release` block. |
| Approved baseline events | `build/baseline.py` `current_baseline()` (`reports/verifications.jsonl`) | ALREADY_IMPLEMENTED | Referenced through the assessment's `baseline` status only (§12: baseline info by ref/digest, not copied). |
| final/proxy + key sidecars | `media/render.py` `final_content_key`, `_read_key_sidecar` | ALREADY_IMPLEMENTED | Manifest `content_key` reads the SAME `.key.json`; `sha256`/`bytes` hash the real file. |
| Timeline compiler + digests | `timeline/compiler.py` `compile_timeline`, `Timeline`/`VideoClip` | PARTIAL | `Timeline.meta.compiled_from` and `final_content_key` exist, but **no digest limited to {segment id, order, in/out, duration}** and both fold in width/height/encoding. → the manifest computes `timeline_semantic_digest` over exactly the format-only-invariant fields (excludes w/h/fps). |
| OTIO / JianYing / CapCut exporters + media refs | `exporters/otio.py` `export_otio`; `exporters/jianying.py` `export_jianying`,`lint_draft`; `exporters/native_draft.py` `export_capcut_native` | PARTIAL | All read the shared compiled `VideoClip` (scan-free via `Project.resolve(clip.source)`) and carry frame in/out (µs / fractional frames). **None binds a per-clip sha256** (audit fact). → the manifest's `nle` section ADDS the exact `asset_sha256` + integer frame mapping the adapters lack, without rewriting them. |
| Shared source resolver / selected take | `timeline/compiler.py` `gather_compile_input` → `VideoClip.source`; `build/stale.py` `evaluate_all` | ALREADY_IMPLEMENTED | Selection happens once upstream; exporters/manifest never re-scan. `Project.get_take` DOES glob → **not used** for binding. |
| Roundtrip import/apply | `build/roundtrip.py` `plan_roundtrip` (zero-write) / `apply_roundtrip` (only writer, explicit rows, build_lock) | ALREADY_IMPLEMENTED | Confirms §8.4: a cutdown/source edit only changes source through explicit `--apply`. Manifest records a cutdown's source ref; never mutates. |
| Subtitle SRT/ASS + source IDs | `exporters/srt_ass.py` `compile_srt`/`compile_ass`; `CaptionLine.shot` | PARTIAL | No compound `line:S010:dialogue:0` id exists — only `CaptionLine.shot` + position. Localization `source_ids` therefore use the locale system's shot-based ids. |
| Locale base_hash / lines / status | `core/locale.py` `locale_status`, `line_status`, `base_text_hash`, `load_lines` | ALREADY_IMPLEMENTED | `line_status` → `{state ∈ missing/翻译过期/ok, base_hash(current), stored_base_hash}`. Manifest `localization` consumes verbatim; `翻译过期` ⇒ STALE ⇒ block. |
| Voice / BGM / SFX / full mix / stems | `build/voice.py`, `build/mixer.py` | SKIPPED_WITH_EVIDENCE | No standalone stem / full-mix / textless / M&E artifact files are produced (grep: only doc mentions). Roles `DIALOGUE_STEM/MUSIC_STEM/SFX_STEM/FULL_MIX/TEXTLESS_MASTER/M_AND_E_MASTER` are declared-but-unpopulated (§6.3 "只列真实支持/生成的角色"). |
| VTT captions | — | SKIPPED_WITH_EVIDENCE | No VTT exporter exists (grep `vtt` in `src/` = 0). `CAPTIONS_VTT` role recognised but never emitted. |
| Package cover/teaser | `media/packaging.py` `make_package`, `cover_cache_key`, `teaser_cache_key`, `_read_key` | ALREADY_IMPLEMENTED | Cover→POSTER, teaser→TEASER; freshness/key via the exportstatus rows (same formula). |
| pack/unpack + atomic archive | `manju pack`/`unpack` **inline in `cli.py`** (zipfile only in cli.py) | PARTIAL / REJECTED_WITH_REASON (reuse) | The zip-safety guards are inline command bodies, **not an importable module**; pack is not atomic and has no dup guard. → the bundle reuses the importable PRIMITIVES that back them (`Project.resolve` escape guard, `media.ffmpeg.atomic_output`) and enforces §11.3 in `delivery.py`. Rewriting cli.pack was rejected (frozen surface, budget). |
| RunManifest / attempt / final outputs | `build/attempts.py` `build_run_manifest` (`terminal_status ∈ COMPLETED/…/INCOMPLETE/NOT_FOUND`) | ALREADY_IMPLEMENTED | Not re-derived — the run-incomplete / evidence-corrupt / unknown-submission gates arrive through `release_assessment.blockers` (§12, addendum P0). |
| Secret / signed-URL scan | `core/check.py` `SECRET_PATTERNS`; `providers/submission.py` `_SECRET_KEY_RE`,`strip_url_query` | ALREADY_IMPLEMENTED | `credentials_present` is a REAL scan over metadata reusing the ONE canonical token list + the signed-URL query detector. |
| path / hash / canonical JSON | `core/hashing.py` `hash_value`,`hash_file`,`canonical_json`,`HASH_PREFIX`; `core/yamlio.py` `atomic_write_text`; `core/container.py` `Project.resolve/relpath` | ALREADY_IMPLEMENTED | Digest determinism, SHA256SUMS, atomic writes, project-relative paths all from existing primitives. |
| export_profiles shape | `core/models.py` `ProjectConfig.export_profiles: list[str]` (export KINDS); `ManjuModel` `extra="allow"` | ALREADY_IMPLEMENTED | Existing field is a list of export kinds, NOT rich profiles. `extra="allow"` (pydantic 2.13) lets an additive `delivery_profiles` map ride project.yaml with **zero models.py change**; absent → default MASTER (old-project compat pinned by test). |

### Hard-stop conditions (§15) — none triggered
No manifest-as-export-input, no 2nd take resolver, no auto semantic cutdown, no
core vision model, no auto translation/TTS, no stored credential, no real
upload, no parallel schema, ≤1 new schema, ≤8 production files. All held.

---

## Red fixtures (§5.2's twelve) + test contract (§13)

Written in `tests/test_c13_delivery.py` (ffmpeg-free, 07C fabricator style).
Before `build/delivery.py` existed, `from manju.build import delivery` raised
ImportError → the whole file was red. Each fixture pins one derived fact:

1. master 16:9 + format-only 9:16 → **shared** `source_timeline_digest`, no mismatch.
2. format-only that dropped a segment → `VARIANT_KIND_MISMATCH`, not technical_ready.
3. explicit 15s cutdown → rejected without a source revision; recorded with one.
4. zh-CN master + en-US locale, stale base hash → `localization.status=STALE`, blocks.
5. OTIO/JianYing referencing an old take → `NLE_STALE`.
6. NLE project referencing missing media bytes → `NLE_MEDIA_UNRESOLVED`, `asset_sha256=None`.
7. cover/teaser stale → artifact `state=STALE`.
8. required captions missing → `CAPTIONS_SRT` MISSING, `required_roles_satisfied=False`.
9. final exists but run incomplete → `RUN_INCOMPLETE` blocker (07C), DRAFT not ready.
10. metadata with token/signed URL → `credentials_present=True`, secret never in manifest.
11. bundle with `..` traversal / duplicate entry → refused.
12. NLE opened but unverified → `opened_in_target_app=PENDING`, `editor_approved=False`.

### Pre-change behaviour observations (§5.3)
- `exports --json` already emits `deliverables` + `counts` + `release_assessment`
  (07C). The manifest reuses that exact payload (test asserts blocker parity).
- `Project.resolve` already rejects `..`/absolute escapes (bundle guard reuse).
- `locale_status` base-hash staleness already representable as `翻译过期`.
- `mark_verified` already flips a draft row to `verified` (human axis).
- `final_content_key` recompute is deterministic under a manual timeline.
- `ProjectConfig` (extra=allow) round-trips an unknown `delivery_profiles` key.
