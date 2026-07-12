# FP Loop S3 — Archive self-description for `.manjupkg` (roadmap §7.8 extensions)

Function-Perfection loop S3: `manju pack` now writes THREE additional
synthesized members — honest restore instructions, an engine contract-registry
snapshot, and a toolchain snapshot — all written **before** `MANJU_FIXITY.json`
so each is **fixity-covered** (tampering one fails verification exactly like a
payload member). Like the fixity manifest they are transport metadata: a
passing verify on `manju unpack` drops them, so the restored tree equals the
original project. Old packs keep restoring byte-for-byte.

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| HEAD at intake | `4952435` (Loop R2 live in compiler/models/render/graph — untouched) |
| CLI touched | `manju pack` / `manju unpack` bodies + `manju fixity --info` (all inside the pack/unpack/fixity region of `src/manju/cli.py`) |
| New tests | `tests/test_fp_archive_meta.py` — 13 targeted, red-first (10 red at baseline; 3 are behavior-preservation pins), all green |
| Guards held | `test_fp_fixity` 10/10 · `test_fp_security` 9/9 · `test_fp_cli_snapshot` 6/6 · `test_fp_toolchain` 22/22 · `test_fp_contracts` 17/17 (extended, never rewritten) |
| Snapshot | `tests/fixtures/cli_surface.json` **not regenerated** — `--info` is an optional flag on an existing command (no new command, no new required param), so the frozen surface did not change; the snapshot suite proves it green |
| Deps | none added; reuses `core.toolchain.toolchain_manifest`/`_manju_version` + `core.contracts.REGISTRY_PATH` + `core.hashing` — no parallel collectors |
| CONTRACTS.yaml | not edited (in-pack members are documents of existing conventions; the orchestrator's `manjupkg` registry-row notes update is recorded below) |

## Scope ruling

In scope: three synthesized in-pack members + fixity coverage + drop-on-restore
+ the read-only `manju fixity --info` summary. Explicitly OUT of scope
(recorded, not built): BagIt layout, multi-volume archives, a revalidation
daemon — same ruling as Loop K.

## The three members

Written in this zip order (all with the fixed `1980-01-01` stamp Loop K
established for synthesized members; `MANJU_FIXITY.json` stays the LAST member
— K's pin):

1. **`MANJU_CONTRACTS.yaml`** — a byte copy of the ENGINE's contract registry
   at pack time, located via `core.contracts.REGISTRY_PATH`. The pack is a
   PROJECT archive; the relevant snapshot is the engine registry that governed
   it, never anything synthesized. **Honest absent-fallback:** an installed
   engine that ships no `CONTRACTS.yaml` (e.g. a wheel without data files) ⇒
   the member is OMITTED and `MANJU_RESTORE.txt` carries a "NOT INCLUDED"
   line — a fabricated snapshot is never written (pinned by a monkeypatch
   test; the pack still round-trips green).
2. **`MANJU_RESTORE.txt`** (`manju-restore.1`) — restore notes: the exact
   `manju fixity <name>` / `manju unpack <name>` / `manju fixity <name> --info`
   commands, what the fixity manifest guarantees and what it does **not**
   ("the manifest is NOT signed … detects corruption and accidental edits —
   not a capable adversary"), pointers to both snapshots (or the honest
   absence line), and the packing manju version (from `_manju_version()`, the
   same source the toolchain manifest uses — pinned self-consistent).
3. **`MANJU_TOOLCHAIN.json`** — the `manju.toolchain-manifest/v1` document
   from `core.toolchain.toolchain_manifest()`, serialized exactly like
   `write_toolchain_manifest` stores it (sorted keys, indent 2, trailing
   newline). Reused collectors — no parallel fact gathering; a test pins
   `schema` + `manifest_digest(doc)` round-trip, proving it IS the real
   document (and therefore inherits its no-paths/no-hostname pins).

### Determinism

No timestamps anywhere; the restore note names the **canonical** pack filename
(`<project-dir-stem>.manjupkg`, derived from the project directory, never
`--out`) and says the file may have been renamed — so two packs of one
unchanged tree yield byte-identical bytes for all three members regardless of
where they were written (pinned). This deliberately mirrors K's scope: the
CONTAINER zip is still not byte-reproducible (payload members keep real
mtimes); the synthesized members are.

## Fixity coverage + drop-on-restore semantics

- The three members are appended to the same `fixity_files` map the payload
  loop fills, so `MANJU_FIXITY.json` records their `{sha256, bytes}` like any
  member. Tampering `MANJU_RESTORE.txt` in the zip ⇒ `manju unpack` fails,
  removes the just-created destination, nonzero exit (pinned); `manju fixity`
  reports the mismatched row without any new code — it already verifies every
  non-manifest member (pinned via a tampered `MANJU_TOOLCHAIN.json`).
- After a PASSING verify, `unpack` unlinks all four `PACK_TRANSPORT_MEMBERS`
  (fixity manifest + the three) — restore == original tree, K's precedent
  extended (pinned by a payload-set equality test). No verify (old pack) or a
  failed/unparseable manifest ⇒ nothing is dropped, exactly as K left it.
- Old packs: zero behavior change (no members ⇒ `missing_ok` no-ops; the
  restored-files count is computed against the same reserved-name set, which
  matches nothing in an old pack) — pinned.
- Reserved names: the pack payload loop now skips a stray on-disk file named
  like ANY synthesized member (extends K's `MANJU_FIXITY.json` guard) so a
  project file can never shadow or collide with the transport members.

## `manju fixity <pack> --info`

Optional, additive, read-only: surfaces restore-note presence,
contracts-snapshot presence, and a toolchain summary (manju + ffmpeg version
strings) parsed from `MANJU_TOOLCHAIN.json`. Reading follows the FP-security
loop's capped-reader discipline — streamed in chunks against a hard cap on
ACTUAL inflated bytes (`META_INFO_READ_CAP_BYTES` = 1 MiB, header-blind), and
an oversized/unparseable member is reported `present, not summarized` instead
of guessed. Without `--info` the existing JSON/human surfaces are byte-stable
(pinned: no `info` key). The verify verdict and exit code are never changed by
`--info`; on a no-manifest old pack it reports an honest all-absent summary
while K's `present:false` + nonzero exit stands (pinned).

## Files

| File | Change |
|---|---|
| `src/manju/cli.py` | pack/unpack/fixity region only: S3 constants + `_toolchain_snapshot_bytes` / `_contracts_snapshot_bytes` / `_restore_note_bytes` builders; `_synth_zipinfo` generalization (`_fixity_zipinfo` now delegates); pack writes+covers the three members (additive `meta_members` in `--json`, one new dim human line); unpack drops all transport members after a passing verify; `fixity --info` + `_pack_meta_info` + `_read_meta_member_capped` + `_print_meta_info` |
| `tests/test_fp_archive_meta.py` | new — 13 tests, red-first |
| `REPORTS/FP_ARCHIVE_META.md` | this report |

## Targeted test results

- `test_fp_archive_meta` — **13 passed** (members present/before-fixity/covered;
  restore-note honesty + self-consistency; contracts byte-copy; toolchain
  document reuse; two-pack determinism; tamper note ⇒ unpack fail + dest
  removed; tamper toolchain ⇒ fixity mismatch row; drop-on-restore == original
  tree; old pack unchanged; REGISTRY_PATH-absent honest fallback; `--info`
  read-only summary; no `info` key without the flag; `--info` on old pack).
- Guards: `test_fp_fixity` 10 · `test_fp_security` 9 · `test_fp_cli_snapshot` 6
  · `test_fp_toolchain` 22 · `test_fp_contracts` 17 — all passed, untouched.
- Regression: `test_cli -k "pack or unpack or fixity"` 2 · `test_round_y` 7 —
  passed. Targeted total: **86 passed / 0 failed**.

## For the orchestrator: `manjupkg` registry-row notes update (NOT applied)

Loop K's suggested `manjupkg` document row should gain, in `notes`: "The pack
also carries three fixity-covered self-description members written before the
manifest — MANJU_RESTORE.txt (`manju-restore.1` restore instructions),
MANJU_CONTRACTS.yaml (byte copy of the engine registry at pack time; honestly
omitted when the engine ships none), MANJU_TOOLCHAIN.json (the
manju.toolchain-manifest/v1 document). All MANJU_* members are transport-only
and are dropped from the restored tree after a passing verify."

## Deviations / decisions

1. **Canonical name in the restore note.** "The exact `manju unpack <name>`
   command" is spelled with the canonical `<project-stem>.manjupkg`, never the
   `--out` filename — an `--out`-dependent note would break the two-pack
   determinism the addendum itself pins, and the archive is rename-safe anyway;
   the note says to substitute the actual filename.
2. **Member order within the meta block** is alphabetical (CONTRACTS, RESTORE,
   TOOLCHAIN) before the fixity manifest; only "before MANJU_FIXITY.json" and
   "manifest last" are contractual.
3. **Drop applies only after a PASSING verify** (K's precedent). A pack with
   meta members but a missing/unparseable manifest keeps them in the restore —
   we never delete what we could not verify.
4. **`--info` attaches to all three fixity verdict paths** (ok / fail /
   no-manifest / unparseable) as an additive key; only the happy and old-pack
   paths are pinned, the rest is the same one code path.
