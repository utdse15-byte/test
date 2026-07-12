# FP Loop K — Archive fixity for `.manjupkg` pack/unpack (roadmap §7.8, narrow)

Function-Perfection loop K: content-fixity for the EXISTING `.manjupkg`
pack/unpack path. `manju pack` now records an in-zip fixity manifest so a
restore can PROVE its bytes are exactly what was packed; `manju unpack`
verifies after extraction and refuses to leave a corrupt restore looking like a
good one; a new `manju fixity` verifies a pack WITHOUT extracting it. Additive
by construction — packs made before this loop keep unpacking exactly as before.

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| HEAD at intake | `eed5d5c`, working tree clean |
| CLI touched | `manju pack` / `manju unpack` bodies + one new `manju fixity` command (all in the pack/unpack region of `src/manju/cli.py`) |
| New tests | `tests/test_fp_fixity.py` — 10 targeted, red-first, all green |
| Guard held | `tests/test_fp_security.py` — 9/9 still green (extended, not rewritten) |
| Snapshot | `tests/fixtures/cli_surface.json` regenerated 119 → 121 (fold-in: this loop's `fixity` + parallel Loop M's `perf`) |
| Deps | none added; reuses `core.hashing.hash_file` + `core.toolchain._manju_version` |
| CONTRACTS.yaml | not edited (the manifest is a document member, not a schema id) — a suggested `manjupkg` document row is reported below |

## Scope ruling (narrow)

In scope: an in-zip `MANJU_FIXITY.json` written on pack; verify-after-extract on
unpack with guarded destination removal on failure; a `manju fixity`
verify-without-extract command; old packs (no manifest) unchanged.

Explicitly OUT of scope (recorded, not built): BagIt layout, split/multi-part
archives, a periodic revalidation daemon, and any per-file signing/PKI. These
are larger surfaces than §7.8's narrow fixity ask.

## Pack-writer audit (facts grounding the design)

The writer (`manju pack`) was audited empirically (built a real pack, inspected
the members) before any edit:

- Container: `zipfile.ZipFile(out, "w", ZIP_DEFLATED)`; the original project dir
  name rides in the **zip comment** (`{"manjupkg":1,"name":…}`), never trusted
  for paths on unpack (goal-20).
- Member order is already deterministic — `sorted(project.root.rglob("*"))`.
- Excludes: `PACK_EXCLUDE = (".manju/", ".git/")`; rebuildable render caches
  (`renders/segments/`, `renders/proxy/`) excluded unless `--full`.
- Symlinks are **skipped** (never followed) — a symlink's target bytes must
  never be smuggled in under a safe-looking name (goal-14).
- **Determinism finding (a correction to the addendum's parenthetical):** the
  writer does NOT use fixed 1980 zip stamps — `zf.write(path, rel)` stores each
  file's REAL mtime, so the CONTAINER zip is not byte-reproducible across time.
  What this loop makes deterministic is the **manifest CONTENT** (which is what
  the addendum's determinism test actually scopes: "same tree ⇒ same manifest
  bytes"). The one member this loop synthesizes — `MANJU_FIXITY.json` — is
  written with a FIXED `1980-01-01` stamp (zip's minimum), because it has no
  source mtime and a wall-clock stamp (`writestr`'s default) would inject
  needless nondeterminism.

## The manifest (`MANJU_FIXITY.json`)

Written as the **last** zip member. A self-describing document — a PLAIN
`"format": "manju-fixity.1"` string, deliberately NOT a `manju.*/vN` CONTRACTS
schema id (it never leaves the zip; same convention support-bundle already uses
for its `support-bundle-manifest.1`):

```json
{
  "format": "manju-fixity.1",
  "manju_version": "0.1.0",
  "files": { "<relpath>": { "sha256": "<64-hex>", "bytes": 123 }, ... },
  "totals": { "count": 15, "bytes": 8797 }
}
```

- `files` records every OTHER member and **excludes itself** — the manifest is
  the fixity of the payload, not of the manifest.
- `sha256` is the **bare 64-hex** digest (the field name already names the
  algorithm; no `sha256:` prefix). Computed via the shared
  `core.hashing.hash_file` (1 MiB streaming) with the prefix stripped.
- Serialized `json.dumps(sort_keys=True, ensure_ascii=False, indent=2)` — no
  wall-clock content, so an unchanged tree yields byte-identical manifest bytes
  (pinned by a determinism test).
- Defensive: any stray on-disk `MANJU_FIXITY.json` in the project tree is
  skipped from the payload loop, so it can never collide with the synthesized
  manifest or corrupt self-exclusion.

## Fixity flow + failure semantics

**Unpack (verify-after-extract).** All existing guards run FIRST, untouched —
member-NAME/`..`/absolute + symlink-member refusals, the member-count cap, and
the declared-total-vs-free-disk preflight (the FP-security guards). Only then,
after `extractall`:

- **No manifest** ⇒ today's behavior byte-for-byte, exit 0, plus a one-line
  advisory note ("旧包/非 manju pack — 未做完整性校验").
- **Manifest present + verifies** ⇒ exit 0, a "✓ 完整性校验通过" line, and the
  transport-only `MANJU_FIXITY.json` is **dropped** from the restored tree so it
  equals the original project (and a re-pack can't double-write it).
- **Manifest present + FAILS** (any mismatched / missing / extra row) ⇒
  structured rows, **the just-created destination is REMOVED**, nonzero exit. A
  failed-fixity restore must never masquerade as a good one.
- **Manifest present but unparseable/foreign format** ⇒ advisory, restore kept
  (an unreadable metadata sidecar is not itself evidence the payload is bad, and
  a future `manju-fixity.2` must not make an older binary refuse a good restore).
  No weaker than the absent-manifest path, which an attacker could reach anyway
  by deleting the manifest.

**Guarded destination removal.** `unpack` already refuses when `dest`
pre-exists, so the tree removed on failure is *always* the one THIS invocation
just extracted — the `rmtree` is reached only on a present-manifest verification
failure, is scoped to that `dest`, is best-effort, and reports whether the
directory is actually gone (`dest_removed` in `--json`).

**`manju fixity <pack.manjupkg> [--json]`** — verify WITHOUT extracting. Streams
every member through the hasher (bounded memory — never inflates a whole member;
the header-lie concern from the FP-security loop), applies the same member-count
cap as unpack (the free-disk cap is irrelevant with nothing extracted), and
emits the same mismatched/missing/extra rows + `ok` + matching exit code. A pack
with no manifest is `present:false` with a nonzero exit — an unverifiable pack
is not a verified one (this differs deliberately from unpack's exit-0 advisory,
because verification is `fixity`'s primary job, not a restore's byproduct).

## Determinism

`tests/test_fp_fixity.py::test_pack_manifest_deterministic_same_tree_same_bytes`
packs an unchanged tree twice and asserts byte-identical `MANJU_FIXITY.json`
content. `_manju_version()` (importlib.metadata → git-describe → "unknown") is
the only non-tree input and is stable within a release.

## Files

| File | Change |
|---|---|
| `src/manju/cli.py` | fixity toolkit (constants + manifest builder/zipinfo + parser + shared verifier + dir/zip present-scanners + guarded-remove + row printer) in the pack/unpack region; `pack` records the manifest as the last member; `unpack` verifies-after-extract with guarded removal + manifest drop + advisory; new `manju fixity` command; `from .core.hashing import HASH_PREFIX, hash_file` added to top imports |
| `tests/test_fp_fixity.py` | new — 10 red-first tests |
| `tests/fixtures/cli_surface.json` | regenerated 119 → 121 (fold-in: `fixity` + Loop M's `perf`) |

## Targeted test results

- `test_fp_fixity` — 10 passed (pack manifest present/accurate/last; determinism;
  clean roundtrip verifies + drops manifest; tamper ⇒ fail + dest removed;
  delete ⇒ missing row; stray ⇒ extra row; old pack ⇒ advisory exit 0; `fixity`
  verifies without writing; `fixity` detects tamper; `fixity` no-manifest).
- `test_fp_security` — **9 passed** (the untrusted-archive guards held; extended,
  not rewritten).
- `test_fp_cli_snapshot` — 6 passed (snapshot at 121, ≥ the 112 floor).
- Regression sweep — `test_cli`, `test_ticket2`, `test_round_y`, `test_e2e_m0`,
  `test_mcp`, `test_fp_contracts`, `test_fp_runperf` all green (142 passed across
  the consolidated targeted set).

## Suggested CONTRACTS document row (audit result — NOT applied)

Audit: the `documents:` registry has **no** `manjupkg` / pack-format row (its
nearest analogue, `support-bundle`, registers a zip + plain-`format:` manifest).
Per the addendum I did not edit CONTRACTS.yaml; the `manju fixity --json` output
is already covered by the existing `cli-json-surface` document row. Suggested
row for the orchestrator, modeled on `support-bundle`:

```yaml
  - id: manjupkg
    kind: document
    owner: manju.cli:pack
    status: stable
    latest_version: 1
    read_older: true          # packs without MANJU_FIXITY.json still unpack
    write_older: false
    notes: >-
      The project archive format (.manjupkg): a ZIP_DEFLATED zip whose comment
      carries {manjupkg:1, name} (never trusted for restore paths) and whose
      LAST member is a fixity manifest MANJU_FIXITY.json with a plain
      `format: manju-fixity.1` field (deliberately NOT a manju.*/vN schema):
      per-member {sha256 bare-hex, bytes} excluding itself, plus totals +
      manju_version. unpack verifies after extract (mismatch/missing/extra ⇒
      restore removed, nonzero exit); `manju fixity` verifies without
      extracting. Older packs (no manifest) unpack unchanged (advisory).
```

## Deviations / decisions

1. **1980-stamp correction.** The addendum's "the zip's fixed 1980 stamps" does
   not describe the current writer (it stores real mtimes). Scoped determinism
   to the manifest CONTENT (the test's actual requirement) and gave only the
   synthesized manifest member a fixed 1980 stamp. Recorded here.
2. **Restore drops the manifest.** After a passing verify, `MANJU_FIXITY.json`
   is removed from the restored tree (transport metadata, not project content;
   also prevents a re-pack double-write). Not spelled out in the addendum; the
   alternative (leaving it) creates a duplicate-member footgun on re-pack.
3. **Unparseable manifest ⇒ advisory, not failure** (forward-compat; no weaker
   than absent).
4. **`manju fixity` no-manifest ⇒ nonzero exit** (verification is its job),
   whereas unpack's no-manifest is exit-0 advisory (restore is its job). Easy to
   flip to exit-0 if the orchestrator prefers symmetry.
