# External Provider Handoff

`manju prompt <shot> --target minimax_h3 --bundle` writes a derived directory:

```text
exports/provider_handoff/minimax_h3/<shot>/<digest12>/
  handoff.json
  prompt.txt
  refs.json
  MANIFEST.json
  README.md
  RETURN_FILES.md
  assets/
  SHA256SUMS
```

The same frozen reference resolution feeds prompt labels, `handoff.json`,
`refs.json`, copied assets, the manifest, and checksums. The
`reference_plan_digest` must match in every structured projection. Logical
bindings survive physical deduplication, so one Picture may support multiple
Subjects and one Subject may use multiple Pictures without duplicate copies.

Remote URLs are not downloaded; only scheme/host/path identity is retained and
query/fragment data is removed. Bundle members reject traversal and
case-insensitive collisions. Originals are read and copied, never moved or
modified. Runtime SQLite, keys, authorization data, signed queries and absolute
home paths are excluded.

The bundle contains no generated media. It is `handoff-only`,
`picture_lock: not_eligible`, and does not claim official H3 provenance or
validated output quality. Animatic facts are read-only context; export remains
available before approval with `h3_handoff_before_animatic_approval`. The
context carries the current animatic content key plus shot-timing and keyframe
digests; none of these values changes project truth.

Returned media stays on the existing manual path:

```text
manju ingest returned/S001_h3_v1.mp4 --shot S001
manju ingest returned/S001_h3_v1.mp4 --shot S001 --apply \
  --no-auto-select --handoff exports/provider_handoff/minimax_h3/S001/<digest12>
manju select S001 <take>
```

The first command is a dry-run. Handoff-aware apply records source
`external_manual_roundtrip`, handoff ID, bundle digest, returned media SHA256,
and `claimed_generator: unverified`; it never auto-selects. Normal review and QC
decide whether a human later selects the take.
