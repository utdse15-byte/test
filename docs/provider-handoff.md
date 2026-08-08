# Video Authoring and External Handoff

Manju derives one in-memory Canonical Video Authoring Plan from existing shot,
Bible, keyframe, dialogue, contract, and reference truth. The plan is never
written back as another editable project file. A static authoring profile then
projects that plan into a manual prompt dialect:

```text
portable_video  provider-neutral natural-language handoff
minimax_h3      strict unofficial MiniMax H3 authoring dialect
```

Neither profile is a runtime Provider. Both have `network: forbidden` and
`execution: unavailable`; they cannot route, estimate cost, submit, poll,
download, infer, register a take, or qualify media for Picture Lock.

## Commands

```text
manju handoff profiles
manju handoff create S001 --profile portable_video
manju handoff create S001 --profile minimax_h3
manju handoff inspect <bundle-directory>
manju handoff verify <bundle-directory>
```

The existing command remains an alias:

```text
manju prompt S001 --target minimax_h3 --bundle
manju prompt S001 --target portable_video --bundle
```

## Versioned bundle

New exports use `manju.provider-handoff/v2` and a sibling v2 manifest. The
directory includes the exact prompt bytes, frozen logical bindings and unique
physical assets, profile-authored instructions, copied local assets, the
manifest, and exact `SHA256SUMS`. `portable_video` adds `UPLOAD_ORDER.md` and
`CONSTRAINTS.md`.

`semantic_digest` binds the source plan, profile revision, renderer revision,
and bundle-format revision. `manifest_digest` binds the sorted final payload
member path/hash/size rows. The manifest and checksum envelope avoid self-hash
cycles. Existing v1 H3 bundles remain fully readable and fully verified; new
writers never emit v1.

Publication is directory-atomic and immutable. Manju builds all bytes in a
sibling temporary directory, flushes them, verifies the complete temporary
bundle, and atomically renames it to the digest directory. A valid byte-equal
directory is reused. Any existing mismatch fails closed and is never repaired
in place.

## Verification boundary

`verify_handoff_bundle()` is the only trust entry point. It rejects unsafe
relative paths, symlinks and Windows reparse points, casefold collisions,
missing or non-regular members, size/hash drift, a non-exact checksum file,
unknown files, schema/profile mismatches, and handoff/manifest identity drift.
`ingest --handoff` completes this verification before registering any take.

Remote URLs are not downloaded; query and fragment data are removed. Absolute
host paths, runtime state, credentials, signed queries, and authorization data
are excluded from the package.

## Manual return

```text
manju ingest returned/S001_external_v1.mp4 --shot S001
manju ingest returned/S001_external_v1.mp4 --shot S001 --apply \
  --no-auto-select --handoff exports/provider_handoff/portable_video/S001/<digest12>
manju select S001 <take>
```

The first command is a dry run. Handoff-aware apply records
`external_manual_roundtrip`, the handoff identity, returned-media SHA-256, and
`claimed_generator: unverified`. It never auto-selects. A filename or manual
return does not prove generator identity, execution capability, output quality,
official certification, or Picture Lock eligibility.
