# FP Loop F — Redacted diagnostic support bundle (library)

`manju support-bundle` core: a zip a user can safely hand to whoever is helping
them debug — enough of the *shape* of their environment and recent activity to
diagnose a problem, with **none** of their content, media, credentials, or
private paths. Library + tests only; the CLI command is wired by the orchestrator
against the signature below.

- Library: `src/manju/core/supportbundle.py`
- Tests: `tests/test_fp_supportbundle.py`
- Entry point: `build_support_bundle(project, dest_zip, *, include_events_tail=200) -> dict`
  (a pure read; returns the summary manifest dict)

## The honesty core — redaction-by-construction

Default-deny, allowlist. The bundle **contains only what the collector explicitly
produces**; it never copies a raw project file wholesale, never reads a media
byte, never opens a provider auth block, never emits an absolute private path.
Two independent layers guarantee it:

1. **The ONE redactor** — every string that could carry project data is routed
   through `redact_record` → `redact_text` (which reuses `core.check.SECRET_PATTERNS`,
   the ONE token list) before it is written.
2. **The self-scan tripwire** — after assembly, the bundle is scanned against its
   own forbidden-marker list; any survivor ⇒ `BundleError`, **nothing written**.
   A redaction bug therefore fails loudly instead of leaking.

## (a) Collector inventory — what's included, and each redaction rule

Everything below is derived/aggregated; no source file is copied.

| Section (member) | What | Redaction rule |
|---|---|---|
| `environment` (in MANIFEST.json) | manju version (`importlib.metadata` → `git describe` → `"unknown"`), python version + implementation, platform (`system release machine` — **no** hostname/user), `ffmpeg`/`ffprobe` `-version` first line (absent ⇒ `"missing"`), optional deps otio/hypothesis/PIL/numpy/pydantic/pyyaml present/absent + version | names + versions only; each string still passes `redact_text`; host identity never collected |
| `project_shape` (in MANIFEST.json) | counts: shots, bible entries, timeline clips; **bucketed** takes-per-shot histogram (`0/1/2/3-5/6-10/11+` — exact per-shot counts never revealed); reports subdirs present; schema ids seen in reports (ids only) | counts only, NO content; safe `project.yaml` subset = `fps/width/height/mode`; `name` dropped if it looks like a path (`name_omitted:true`); budget as **presence only** (`budget_limit_set`), never an amount; provider/auth never touched |
| `events-tail.txt` (member) | last `include_events_tail` (default 200) lines of `events.jsonl`, one redacted line each | per line: key-mask values of `(key\|token\|secret\|authorization\|signature\|password\|bearer)`; mask signed-URL query values; rewrite absolute paths → basename; malformed/torn line ⇒ counted + replaced by `<malformed line skipped>` (never verbatim) |
| `failures-tail.jsonl` (member) | last 50 lines of `reports/failures.jsonl` if present | identical per-line redaction |
| `config_digests` (in MANIFEST.json) | provider manifest **digests**: `{provider, sha256}` | the file's **bytes are hashed, never parsed** (`hashing.hash_file`); the auth block is never opened, so a credential cannot leak even through a redaction bug |
| `MANIFEST.json` (member) | index: `format`, `members`, self-scan verdict, redaction counts, + the inline sections above | `format: "support-bundle-manifest.1"` — a plain string, **not** a `manju.*/vN` public schema id |

**Never in the bundle** (declared honestly in `skipped`): media bytes;
bible/shot/timeline content; provider manifest contents; any absolute private path.

## (b) Self-scan tripwire — proof

After assembly and *before* any write, `self_scan` scans every member's bytes
(the inline structured data too) for: `/home/` `/root/` `/Users/` `C:\` `Authorization:`
`Bearer ` `?signature=` and every `SECRET_PATTERNS` token. Any hit ⇒ `BundleError`,
no zip. The verdict (safe pattern *names* only — never the raw marker, which would
trip the scanner on its own report) rides the returned summary as `self_scan`.

Test `test_self_scan_tripwire_refuses_to_write_when_redactor_is_noop` monkeypatches
the ONE seam `redact_record` to the identity function. The leaky events
(`Authorization: Bearer sk-…`, `?signature=…`, `/home/…`, `C:\Users\…`,
`/Users/…`) then flow unredacted into the assembled member bytes, the tripwire
fires, `BundleError` is raised, and **`dest` does not exist**. The error message
names markers/members only — it carries neither the secret token nor the private
path.

## (c) Determinism — proof

No wall-clock anywhere; fixed `(1980,1,1,0,0,0)` timestamp and `0o644` on every
member; members written in sorted order; all JSON dumped with `sort_keys=True`.
`build_support_bundle` is a **pure read** (appends no event, mutates nothing), so
the events tail it hashes does not change between runs. Test
`test_bundle_is_byte_deterministic` builds the same project twice and asserts
byte-identical zips + the fixed member timestamp + sorted members;
`test_building_the_bundle_does_not_mutate_the_project` guards the precondition.

## (d) Files

- `src/manju/core/supportbundle.py` — library (new)
- `tests/test_fp_supportbundle.py` — 10 tests (new)
- `REPORTS/FP_SUPPORTBUNDLE.md` — this report (new)

No existing file modified. Stdlib only (zipfile/json/subprocess/importlib/hashlib/
platform/shutil/re); no network; no new dependency.

## (e) Suite counts

`tests/test_fp_supportbundle.py`: **10 passed**. Full-suite result recorded in
`FP_F_RESULTS.md` (any failures are attributed to the sibling in-flight agents'
files, which Loop F does not touch).

## (f) Deviations + registry document row

- **Deviations**: none material. Two conservative choices worth noting: (1)
  absolute-path rewriting is aggressive on the sensitive roots
  (`/home /root /Users /tmp /var …` + `C:\`) and best-effort elsewhere, with a
  URL-scheme guard so `https://host/…` is preserved while private paths collapse
  to a basename; (2) `failures-tail.jsonl` uses a fixed 50-line cap (the
  `include_events_tail` parameter governs the events tail only).
- **Registry row (report only — orchestrator applies)**: a DOCUMENT row —
  `id: support-bundle`, `kind: document`, `owner: manju.core.supportbundle`,
  `status: experimental`. The bundle is a diagnostic artifact, **not** a
  `manju.*/vN` schema; its `MANIFEST.json` declares `format: support-bundle-manifest.1`
  (a plain string field), so **no new public schema id is introduced**.

## Boundaries honored

No `cli.py` edit; no `CONTRACTS.yaml`/`DECISIONS`/`README` change; no
`build/conformance.py` touch; no `tests/test_fp_{contracts,cli_snapshot,docs,timebase,security,profile,conform}*`
touch; no new public `manju.*/vN` schema id; no commit/push.
