# FP Loop U1 — `manju pack --bagit` serialized BagIt bag (roadmap item 8)

Opt-in RFC 8493 serialized bag written INSIDE the same `.manjupkg` zip. Default
(`manju pack` with no flag) output is **byte-identical to before this change** —
proven, not asserted (see §1). One coherent extension of the existing pack/
unpack/fixity region in `src/manju/cli.py`; no new dependencies (stdlib
`zipfile`/`hashlib` only).

## 0. What `--bagit` writes

```
bagit.txt                 "BagIt-Version: 1.0\n" + "Tag-File-Character-Encoding: UTF-8\n"
manifest-sha256.txt       payload fixity — one line "<hex>  data/<path>" per payload file
bag-info.txt              External-Identifier, Bag-Software-Agent, Payload-Oxum (NO Bagging-Date)
tagmanifest-sha256.txt    tag-file fixity — digests of every tag file except itself
data/<project tree>       the payload
MANJU_RESTORE.txt         \
MANJU_CONTRACTS.yaml       } ride as TAG FILES at bag root, tagmanifest-covered
MANJU_TOOLCHAIN.json      /
```

**One fixity truth per format:** in `--bagit` mode `MANJU_FIXITY.json` is
OMITTED — the BagIt manifests are the single fixity authority. `unpack` and
`manju fixity` AUTO-DETECT the layout by the `bagit.txt` member and verify
payload against `manifest-sha256.txt` AND tag files against
`tagmanifest-sha256.txt`, with the SAME remove-on-mismatch discipline as the
default path. Restore strips the `data/` prefix and drops every tag file, so the
restored directory equals the original project tree.

## 1. Default-mode byte-identity (evidence)

Captured a golden by packing a persistent fixture (CJK project name + a CJK
payload file `笔记.txt`, fixed mtimes) in default mode on the UNEDITED `cli.py`,
then re-packed the SAME on-disk fixture after the change:

| | sha256 | bytes | members |
|---|---|---|---|
| pre-edit golden | `9f99eed8…397a4` | 13807 | 20, `MANJU_FIXITY.json` last |
| post-edit        | `9f99eed8…397a4` | 13807 | 20, `MANJU_FIXITY.json` last |

`PASS: post-edit default pack is BYTE-IDENTICAL to the pre-edit golden.` All
new default-vs-bag behaviour is gated behind `if bagit:`; `--bagit` is an
optional flag (default `False`), so the default archive bytes, `--json` shape,
and human output are untouched. The committed pin
(`test_default_mode_deterministic_and_bag_absent`) enforces default determinism
+ absence of every bag member + `MANJU_FIXITY.json` as last member going
forward.

## 2. Bag member table + one manifest line

Real `manju pack --bagit` of the fixture (16 payload files) → 23 members:

| member | role | fixity by |
|---|---|---|
| `data/…` × 16 (incl. `data/笔记.txt`) | payload | `manifest-sha256.txt` |
| `manifest-sha256.txt` | payload manifest | `tagmanifest-sha256.txt` |
| `bag-info.txt` | metadata | `tagmanifest-sha256.txt` |
| `bagit.txt` | bag declaration | `tagmanifest-sha256.txt` |
| `MANJU_CONTRACTS.yaml` | transport (tag) | `tagmanifest-sha256.txt` |
| `MANJU_RESTORE.txt` | transport (tag) | `tagmanifest-sha256.txt` |
| `MANJU_TOOLCHAIN.json` | transport (tag) | `tagmanifest-sha256.txt` |
| `tagmanifest-sha256.txt` | tag manifest | root of trust (unsigned) |

`MANJU_FIXITY.json`: **absent** (bag mode). Sample payload manifest line
(two-space separator, path verbatim UTF-8):

```
ecd145dd16671ee243e5114dc2d4291280e373001cce458c4d9f07fa68b7e874  data/.gitignore
```

`bag-info.txt` (whole file — deterministic, no Bagging-Date):

```
External-Identifier: 雨夜便利店.manjupkg
Bag-Software-Agent: manju 0.1.0
Payload-Oxum: 3819.16
```

Payload-Oxum `3819.16` = 3819 octets over 16 streams — cross-checked against the
zip in `test_bagit_payload_oxum_math`. `tagmanifest-sha256.txt` lists exactly
the 6 tag files (sorted; MANJU_* + `bag-info.txt` + `bagit.txt` +
`manifest-sha256.txt`), never itself, never any `data/` entry. Round-trip
restore reproduces all 16 payload files (incl. `笔记.txt`) and drops every tag
file; `fixity --info --json` reports `format=bagit-1.0`, `checked=22`
(16 payload + 6 tag), `info.bag={format, payload_oxum}`.

## 3. Bagging-Date ruling

**Omitted deliberately; the bag stays RFC 8493-valid.** A wall-clock
`Bagging-Date` would break byte-for-byte determinism (the explicit goal: two
`--bagit` packs of one tree must be identical). Basis:

- **RFC 8493 §2.2.2 (bag-info.txt):** `bag-info.txt` is itself OPTIONAL, and its
  metadata elements — the reserved set including `Bagging-Date`, `Source-
  Organization`, `Payload-Oxum`, … — are all optional; none are REQUIRED for a
  valid bag. `Payload-Oxum` is *recommended* (fast completeness check), which is
  why it IS written. This is stated from knowledge of the RFC text; per loop
  discipline (no network) I could not re-fetch the RFC to quote it verbatim, but
  the "all bag-info elements optional / bag-info.txt optional" structure is a
  load-bearing, well-established property of the format.
- **Validators:** the Library of Congress reference implementation
  `bagit-python`'s `Bag.validate()` does not require `Bagging-Date` — it checks
  the payload/tag manifests, the `bagit.txt` declaration, and (when present)
  `Payload-Oxum`. `make_bag()` *writes* `Bagging-Date` as a producer default,
  but that is not a validation requirement. `bagit-python` is NOT installed in
  this environment and I did not add it (no new deps), so this validator claim
  is from knowledge of its source, not a live run here.

Honesty caveat surfaced in-product: `MANJU_RESTORE.txt` (bag variant) states
both deliberate omissions — no `MANJU_FIXITY.json`, no `Bagging-Date` (with the
determinism reason) — so a restorer is never misled.

## 4. CLI surface snapshot — UNMOVED

`tests/fixtures/cli_surface.json` records, per command, only its REQUIRED params
+ existence. `--bagit` is an optional flag on the EXISTING `pack` command (whose
required-param list is `[]`), so it adds no command and no required param.
Regenerating (`python -m tests.test_fp_cli_snapshot`) rewrote the file
**byte-identically** (sha256 `5a5bbd0c…` before == after; still 125 rows;
`git status` clean for the fixture). Floor ≥112 holds. No snapshot change is
part of this loop.

## 5. README / docs resolver

No README edit is required and none was made. `tests/test_fp_docs.py` enforces
the README→app direction (every command in a README command-table row must
resolve to a live command); it does NOT require the app→README direction, so a
new optional flag needs no row. `test_fp_docs.py` + `test_fp_workflows.py` stay
green (13 passed) with `--bagit` added.

## 6. Tests (red → green)

`tests/test_fp_bagit.py` written red-first (19 tests): default-mode determinism
+ bag-absent golden pin; exact bag member set; `bagit.txt` exact bytes;
Payload-Oxum math; manifest line format + digests; tagmanifest coverage incl.
transport members; deterministic bag-info without Bagging-Date; two-packs byte-
identity; CJK payload path; round-trip restore == source; tamper a payload byte
→ fail + dir removed; tamper a tag file → fail + dir removed; fixity verify-
without-extract; fixity detects payload tamper; fixity detects tag tamper;
`fixity --info` reports `bagit-1.0`; `MANJU_FIXITY.json` present-default/absent-
bag; pack `--json` bag shape; restore-note states the omissions.

- RED (pre-implementation): **18 failed, 1 passed** (the 1 pass = default-mode
  pin; all `--bagit` tests failed `No such option: --bagit`).
- GREEN (post-implementation): **19 passed**.

Regression suites kept green (targeted, no full runs):
`test_fp_fixity.py`, `test_fp_archive_meta.py`, `test_fp_cli_snapshot.py`
(29 passed); `test_fp_security.py`, `test_round_y.py`, `test_cli.py`,
`test_ticket2.py` (62 passed); `test_e2e_m0.py` (6 passed);
`test_fp_docs.py`, `test_fp_workflows.py` (13 passed).

## 7. Design notes / seams reused

- Single hash per file: the payload walk computes `hash_file(path)` ONCE and
  feeds the bag manifest (default mode still feeds `MANJU_FIXITY.json`) — no
  file is hashed twice in a pack run.
- Determinism: tag files carry the fixed 1980 stamp (`_synth_zipinfo`); manifest
  + tagmanifest lines are sorted; `bag-info.txt` has fixed field order and no
  timestamp; the restore note names the CANONICAL pack name (not `--out`).
- Restore reshaping uses a staging dir + atomic `os.replace(stage/data, dest)`,
  which strips `data/` and drops tag files in one move and correctly handles a
  project that itself contains a top-level `data/` entry; on any verify failure
  the stage is removed and `dest` is never created (remove-on-mismatch is then
  structural).
- Payload paths percent-encode only CR/LF/`%` per RFC 8493 §2.1.3
  (`_bagit_encode_path`/`_bagit_decode_path`); UTF-8/CJK passes through verbatim,
  so a normal `as_posix()` path is unchanged.
- Trust model is identical to the JSON-fixity path: `tagmanifest-sha256.txt` is
  the unsigned root of trust (covers `manifest-sha256.txt`, which covers the
  payload); a consistent rewrite of all manifests passes, exactly as a
  consistent rewrite of `MANJU_FIXITY.json` did — stated in the restore note.

## 8. Files

- `src/manju/cli.py` — pack/unpack/fixity region + `--bagit` flag (in place).
- `tests/test_fp_bagit.py` — new, red-first.
- `tests/fixtures/cli_surface.json` — regenerated, UNMOVED (no net change).
- `REPORTS/FP_BAGIT.md` — this report.

No CONTRACTS/DECISIONS/README edits. No files outside the U1 scope touched.
