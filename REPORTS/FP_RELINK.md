# FP Loop G — Missing-media report + hash-verified relink inspect/apply (§6.3)

`manju relink`: the recovery workflow for the one failure §3's directory-as-project
design makes *survivable but previously unreported*: truth text (sidecars, shots,
timeline, bible) is in git, media bytes are not — so a fresh clone, a pruned disk,
or a moved external drive leaves sidecars whose media is gone. The engine already
*models* that state; this loop makes it visible and reversible without ever
touching truth.

- Library: `src/manju/media/relink.py`
- CLI: `manju relink report|plan|apply` (one command, mode argument)
- Tests: `tests/test_fp_relink.py` (red-first; 22 tests)
- Entry points:
  - `missing_media_report(project) -> dict` — zero-write
  - `relink_plan(project, search_roots, *, max_files=20_000, max_bytes_per_file=2 GiB) -> dict` — zero-write
  - `apply_relink(project, plan, *, allow_unverified=False, actor="engine") -> dict`

## (a) Detection coverage — what counts as missing (seams cited)

Exactly the media the truth model actually tracks — no invented categories:

| Row kind | Truth seam | Missing when |
|---|---|---|
| `take` | `core/container.py:97` — `TakeInfo.media_path: Path \| None` ("None if sidecar exists but media is missing"; resolution at `Project.takes()` container.py:459-460) | a `media/gen/<shot>/take_NN.yaml` sidecar has no matching media file for any `MEDIA_EXTS` extension |
| `timeline_source` | `core/models.py` — `VideoClip.source` / `OverlayClip.source` / `AudioClip.source` (project-relative paths on the video/overlay/voice/music/sfx/ambient tracks of `timeline/timeline.json`) | the source does not resolve to an existing file. `__slate__/**` virtual placeholder sources (compiler convention, `timeline/compiler.py:733-736`) are **by design not files — never reported**; sources under `media/generated/**` are reported but flagged `derived: true` (rebuilt by `manju build`) |
| `ref` | `core/refs.py` bible pins — `ref_image`/`ref_images`/`ref_video`/`ref_videos` across all five bible files; the report reuses `refs_report(project)["missing"]` so it can never disagree with `manju refs` / `manju check` | the pin names a file that does not exist |

**`expected_hash` is lineage, never fabrication.** The only durable content-hash
record for media bytes is the attempt-evidence stream (`build/attempts.py` —
`outputs: [{path, sha256, bytes, take}]`, emitted by `providers/registry.py:409-412`
for every generated take). The report joins takes by `(shot, take)` and paths by
recorded relpath. Where no hash was recorded (manual imports, voice takes, refs,
music), the row carries `expected_hash: null` + an explicit note that
hash-verified relink is unavailable and candidates are name-only ADVISORY.

## (b) The verified-relink flow — CAS + refusal semantics

`plan` (zero-write): **hash-first, never name-first when a hash exists** — a
recorded-hash item matches candidates by content only (found under any filename);
hashless items get `verification: "name_only_advisory"` name matches. One plan row
per missing item (`action: relink|skip` + machine-token `reason`), rows and
candidate lists sorted (deterministic; byte-identical across runs).

`apply` (per-row atomic, no partial surprises — every row lands in the result):

1. plan gate: wrong/absent `schema` (`manju.relink-plan/v1`) ⇒ `RelinkError`, nothing runs;
2. target guards (apply **re-checks; never trusts the plan**): recorded relpath
   required (`no_recorded_target`), must resolve inside the project
   (`target_escapes_project`), must be under `media/gen/**` or `media/refs/**`
   (`target_not_restorable`), never `media/imports/**` (`target_in_imports` —
   ingest-only; candidates may be *read* from anywhere, restores never target it);
3. append-only: an existing target refuses (`target_already_present`) — bytes on
   disk are never overwritten;
4. **CAS**: the candidate is re-hashed at apply time — vanished ⇒
   `candidate_vanished`; hash ≠ recorded ⇒ `candidate_hash_mismatch` (tampered);
5. restore = copy bytes to a temp file beside the target, hash the **written**
   bytes (`written_bytes_mismatch` refusal on any divergence), then rename into
   the recorded path. Truth files are **never rewritten** — the media returns to
   where sidecar/timeline/bible already point (pinned by byte-identity asserts);
6. unverified (name-only) rows refuse with `unverified_requires_opt_in` unless
   `allow_unverified=True`; even then they are re-hashed and land as
   `status: "restored_unverified"` — no silent lies;
7. one `relink_apply` event line (append-only evidence; best-effort —
   `event_appended` in the result).

`report`/`plan` zero-write is pinned by a whole-project `{relpath: sha256}`
snapshot before/after (file set + bytes identical).

## (c) Caps

- `max_files` (default **20 000**): total files *considered* across all roots;
  exceeding it stops the walk with `scan.complete: false` + a structured
  `partial scan` note — never a hang.
- `max_bytes_per_file` (default **2 GiB**): files larger are never hashed —
  counted in `scan.skipped_oversize` (an oversize candidate for a hashed item is
  *not* a match; it is never downgraded to a name match).
- symlinks are never followed (dir or file — the `pack` discipline).

## (d) Files

- `src/manju/media/relink.py` (new — library)
- `src/manju/cli.py` (one command block: `relink`, anchored by pack/unpack/support-bundle)
- `tests/test_fp_relink.py` (new — red-first)
- `tests/fixtures/cli_surface.json` (regenerated — surface grew by the new command)
- `REPORTS/FP_RELINK.md` (this file)

## (e) Registry row (orchestrator applies to CONTRACTS.yaml)

```yaml
  - id: manju.relink-plan/v1
    kind: schema
    owner: manju.media.relink
    status: experimental
    latest_version: 1
    read_older: true
    write_older: false
    notes: >-
      Hash-verified relink plan (manju relink plan → apply). Zero-write
      inspect; CAS apply restores bytes to recorded media/gen|refs paths
      only; truth files never rewritten. Internal/experimental (§13).
```

Status: the orchestrator applied this row (equivalent notes wording) during the
loop — `tests/test_fp_contracts.py` is green (17/17) with the
`manju.relink-plan/v1` literal ↔ registry row bidirectionally matched, and
`test_every_owner_resolves_to_real_code` confirms `manju.media.relink` imports.
