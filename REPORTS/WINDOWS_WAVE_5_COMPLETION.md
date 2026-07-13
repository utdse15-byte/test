# Windows Wave 5 — Completion (MANJU_WINDOWS_ONLY_LEAN_V3, W5: 条件性收尾 — 归档加固 / 硬件编码资格 / 基准 / 溯源 · conditional close-out)

Plan **MANJU_WINDOWS_ONLY_LEAN_V3**, wave **W5** (the conditional close-out).
Base = HEAD **b20c321** (the W4 docs-only commit — DECISIONS #40 + the W4
reports), itself atop the W4 colour code commit **103be30** (the run #9 green
tip). Authored on a Linux container (Python 3.11.15, ffmpeg **6.1.1** — the exact
version on both CI OSes). W5 is **CONDITIONAL**: of the four sub-slices, **two
are honest wins** (W5.2 archive hardening, W5.4 hw-encode eligibility facts),
**one is an opt-in bench** (W5.3), and **one is rejected-with-reason** (W5.1
C2PA — recording the rejection is its deliverable). Plus a **gate-hygiene flake
fix** that kills run #10's `WinError 10053`. All new tests pass on the authoring
host; the real-host run is the gate's next execution.

## Baseline

At the base tip `b20c321` the hard `windows-ci.yml` gate was **green** on the W4
code (run #9, `103be30`: 0 failed, full suite + `install-smoke`). The only red
since was run #10 (`b20c321`, a **docs-only** commit): exactly one failure,
`test_shot_lab::test_token_guard_on_mutations`, `ConnectionAbortedError`
**WinError 10053** — an OS-level loopback abort under `xdist` load (**1 failed /
4275 passed** on a diff that changed no code). A single audit established the
base-tip status of every W5 capability before any code changed: pack smuggles a
linked **directory** (`is_symlink()` False for a junction, `rglob` traverses it;
a symlinked dir traversed on **any OS** — a **BUG**); pack is a silent **trap
for its own unpack** (`CON.wav`/trailing-space names packs, then `unpack`
refuses — a **GAP**); **casefold collisions** detected nowhere (`A.wav`/`a.wav` →
last-writer-wins data loss on restore — a **GAP**); **no encoder facts** anywhere
(this ffmpeg lists nvenc/qsv with no GPU — a **GAP**); **no benchmark**; **no
C2PA**. The empirical reds (the real symlinked-dir smuggle, the LISTED-≠-VERIFIED
proof) are in `REPORTS/WINDOWS_WAVE_5_BASELINE.md`.

## Audit

Decision key: **Implement** (W5 built it) · **Kept** (already satisfied, pinned)
· **Deferred** (with evidence, out of W5 scope) · **Rejected** (per a plan rule)
· **N-t-d** (nothing to do / vacuous pass).

| Capability | Decision | Evidence |
|------------|----------|----------|
| pack prunes linked **directories** (junction + symlinked dir) | **Implement** | `cli._dir_is_reparse_point()` — `nt`-only `lstat` `st_file_attributes & 0x400` (`FILE_ATTRIBUTE_REPARSE_POINT`), POSIX always `False` (byte-identical walk); a module-level, **stub-driven** helper (the W1 pattern). The pack walk prunes linked dirs **wholesale** with a named warning; `sorted(Path)` yields a parent before its children, so an ancestor check shields everything beneath a pruned root from `rglob`'s traversal |
| pack warns on Windows-unportable members | **Implement** | via the W1 owner `core.idents.windows_relpath_problems` — **WARN-only**, naming each member our own `unpack` would refuse; a **backup is never blocked** (refusing to back up risks data loss exactly when the user needs the backup) |
| pack warns on casefold-collision groups | **Implement** | via `core.idents.windows_collision_key` — WARN-only, naming both paths in a colliding group; the member is still packed (backup completeness beats portability) |
| pack `--json` `portability` block | **Implement** | additive, **drop-when-empty**: `unportable_members` + `casefold_collisions`; a clean tree emits **no** block and no warning text (the warning must never become ambient noise) |
| unpack **refuses** casefold-colliding members | **Implement** | up front, on **every OS** (deterministic cross-platform), the same class as the W1 reserved-name gate; the dir-entry trailing `/` is normalized before keying; identical repeated names (dir entries) are **not** collisions |
| cross-destination restore | **Kept** (pinned) | a deep nested Chinese-named `--dest` restores and opens as a real project carrying the packed payload byte-for-byte; a different drive on Windows is **this same code path** by construction (absolute pathlib paths, no cwd/common-root assumption) — was correct, now **pinned** |
| `HW_ENCODER_CANDIDATES` + `encoder_inventory()` + `hw_encode_eligibility()` | **Implement** | `media/ffmpeg.py` — the h264 family only (`h264_amf`/`h264_nvenc`/`h264_qsv`); one `functools.lru_cache` parse of `ffmpeg -hide_banner -encoders`; a **pure predicate** → `NONE_LISTED` / `LISTED` (candidates sorted); the note states **`LISTED ≠ VERIFIED`** |
| toolchain manifest `encoders` block | **Implement** | `core/toolchain._encoder_facts()` **delegates** to `media/ffmpeg` (the `_font_inventory`→`media/card` precedent); **record-only**, drift-visible, never a build input; S4 cache keys still read the `-version` line only |
| doctor `hw_encoders` row | **Implement** | `build/doctor.py` — informational (listed candidates or none); **never gates** the exit code |
| render/segment/normalize stay `libx264` | **Kept** (pinned) | `render._SEG_ENC`/`_enc_params` literal `libx264` pins, **and** a source grep that **neither** `render` **nor** `normalize` contains `"encoder_inventory"` — eligibility is inert evidence, never an auto-enable |
| W5.3 opt-in benchmark | **Implement** | `tests/test_windows_bench.py` — `MANJU_BENCH=1` env gate (the `MANJU_C21G_BENCH` precedent); prints cold/warm/final wall times for a 3×2s 640×360 project; asserts only sanity floors (outputs exist; warm ≤ 1.5×cold) |
| §8.7 `runperf` perf-view pins | **Kept** | untouched — nothing under `build/` references `runperf`, `runperf` never reads the wall clock; the bench touches neither |
| `test_shot_lab._req` transient-abort retry | **Implement** | bounded retry (3 attempts, 0.3s backoff) on `ConnectionAborted/Reset/BrokenPipe/RemoteDisconnected` + the `URLError`-wrapped forms; addresses run #10's `WinError 10053`. HTTP statuses (even 5xx) are **real results**, never retried — no assertion weakened; three aborts still fail |
| C2PA / provenance | **Rejected** | provenance proves authorship to third parties; a personal archive has no distribution chain, no verifier; `c2pa` adds a signing-key lifecycle + a heavy dependency with no consumer; integrity is already carried by `MANJU_FIXITY.json` + the BagIt manifests. **Recording the rejection IS the W5.1 deliverable** |
| a `video_encoder` project switch | **Rejected** | consuming eligibility needs the full lockstep (render `_SEG_ENC` + `_enc_params` + `normalize._encode_args` + every cache key + concat profile) and a VERIFIED canary; eligibility without a consumer is deliberately **inert evidence**, not dead config |
| a clock-based perf **REPORT** beside `runperf` | **Rejected** | exactly the parallel system the plan forbids; the opt-in bench prints for the human and asserts sanity floors instead |

## Red tests

Two new files red-first, one env-gated bench. `tests/test_windows_archive.py`
ran **before any production change**: **6 red / 3 already-green pins** (the
behavioral red). `tests/test_windows_hwencode.py` died at **collection**
(`ImportError: HW_ENCODER_CANDIDATES` — the collection-level red). One row per
**RED CLUSTER**.

| Cluster | Old (base) | New behavior pinned |
|---------|-----------|---------------------|
| pack portability warnings (3) | no warning / vacuous | `CON.wav` → a named **便携/unportable** warning, member still packed; `Take.wav`+`take.wav` → a named **大小写/casefold** warning, both still packed; a clean scaffolded tree emits **no** warning text |
| pack `--json` portability block (1) | RED (no block) | absent when clean (**drop-when-empty**); when present, `portability.unportable_members` names `CON.wav` and `portability.casefold_collisions` groups `Take.wav`/`take.wav` |
| junction / reparse-dir prune (2 stub) | RED — `AttributeError` (`_dir_is_reparse_point` missing) | a dir the stubbed probe flags is pruned **wholesale** and **named** in the output; no `smuggled` member reaches the archive; the POSIX probe answers `False` for a normal dir and the walk is byte-identical (`normal_dir/kept.bin` still packed) |
| symlinked-directory prune, **POSIX-real** (1, added during impl) | a **real** red class | a real symlink to an outside tree is pruned wholesale and named; no `secret` member reaches the archive — `rglob` follows dir symlinks on 3.11 and the inner files are not symlinks, so per-file skip never fires |
| unpack casefold refusal (2) | RED — accepted-instead-of-refused | `media/Take.wav`+`media/take.wav` → **non-zero exit**, both names surfaced, **no** dest tree created; case-**varied but non-colliding** names (`Alpha.wav`+`beta.wav`) are **not** refused (the gate is for collisions, not uppercase) |
| cross-destination restore (1) | already green (by construction) | pack → restore into a deep nested Chinese-named `--dest`; the project opens and `media/probe.bin` hashes byte-for-byte; `restored.root == dest.resolve()` |
| hw candidate set + inventory (4) | `ImportError: HW_ENCODER_CANDIDATES` (collection) | `HW_ENCODER_CANDIDATES` is exactly the h264 family; `encoder_inventory()` answers every candidate as a `bool` (real ffmpeg), `libx264` `True`; **missing ffmpeg → all-absent facts** (no crash); the inventory is **process-cached** (one subprocess) |
| manifest encoder block (1) | RED | `toolchain_manifest()["facts"]["encoders"]` carries `libx264: True` and a `bool` per candidate — additive, drift-visible, never a build input |
| eligibility pure predicate (2) | RED | no candidates → `eligible: False`, `candidates: []`, `level: NONE_LISTED`; with candidates → `eligible: True`, candidates **sorted** (`["h264_amf","h264_nvenc"]`), `level: LISTED`, the note names the **driver** caveat |
| never-auto-enables (1) | RED | `render._SEG_ENC[1]`/`_enc_params("final"|"proxy")[1]` == `libx264`; the `render` **and** `normalize` sources contain **no** `"encoder_inventory"` string |
| doctor `hw_encoders` row (1) | RED | one informational row, `ok: True` (never gating); detail names the listed candidates or says `none listed` — this container **LISTS** nvenc/qsv with no GPU, the live `LISTED ≠ VERIFIED` proof |

## Implementation

### Production source files (4, within the plan's ≤10)

1. **`src/manju/cli.py`** — the archive hardening. `_dir_is_reparse_point()`
   (`nt`-only `lstat` `st_file_attributes & 0x400`; POSIX always `False`) is a
   module-level, stub-driven helper (the W1 pattern). The **pack walk** prunes
   linked directories **wholesale** — symlinked dirs (any OS) and junctions
   (`nt`) — with a named warning; because `sorted(Path)` yields a parent before
   its children, an ancestor check shields the whole subtree from `rglob`'s
   traversal. **Portability facts** for every member actually packed come from
   the W1 `core.idents` owners (`windows_relpath_problems` +
   `windows_collision_key`) and are **WARN-only** — a backup is never blocked;
   named warnings fire for members our own `unpack` would refuse and for
   casefold-collision groups. `--json` gains a **drop-when-empty** `portability`
   block; a clean tree emits **no** warning text. **unpack** now **REFUSES**
   casefold-colliding members up front, on **every OS** (deterministic
   cross-platform, the W1 reserved-name-gate class); the dir-entry trailing `/`
   is normalized before keying, and identical repeated names (dir entries) are
   not collisions. Cross-destination restore is pinned (a deep nested
   Chinese-named `--dest`; a different drive is the same code path).
2. **`src/manju/media/ffmpeg.py`** — the hw-encode facts. `HW_ENCODER_CANDIDATES
   = (h264_amf, h264_nvenc, h264_qsv)` (h264 only — the pipeline's profile).
   `encoder_inventory()` parses `ffmpeg -hide_banner -encoders` **once**
   (`functools.lru_cache` process-cached; a missing/broken ffmpeg → all-absent
   **facts**, never an error). `hw_encode_eligibility()` is a **pure predicate**
   over an inventory → `NONE_LISTED` or `LISTED` with candidates **sorted**; the
   note states **`LISTED ≠ VERIFIED`** — a listed encoder still fails at runtime
   without its driver (this very container lists nvenc/qsv with no GPU).
3. **`src/manju/core/toolchain.py`** — the manifest gains an **additive**
   `encoders` fact block via `_encoder_facts()` **delegation** to `media/ffmpeg`
   (the `_font_inventory`→`media/card` precedent): **record-only**,
   drift-visible, **never a build input**; S4 cache keys still read the
   `-version` line only.
4. **`src/manju/build/doctor.py`** — an informational `hw_encoders` row (listed
   candidates or none); it **never gates** doctor's exit code.

Plus, outside the production budget:

- **`tests/test_shot_lab.py`** — the `_req` helper gains a **bounded**
  transient-abort retry (3 attempts, 0.3s backoff) on
  `ConnectionAbortedError`/`ConnectionResetError`/`BrokenPipeError`/
  `RemoteDisconnected` + the `URLError`-wrapped forms. HTTP statuses — even
  5xx — are **real results** and are **never** retried, so no assertion is
  weakened; three aborts in a row still fail. This kills run #10's WinError
  10053.
- **`CONTRACTS.yaml`** — the `manjupkg` and toolchain-manifest entries' **notes**
  are extended (pack portability warnings + the `encoders` fact block). **No new
  contract id, no new schema, no new CLI surface.**

### The boundary lesson (honest)

The first draft placed the inventory in `core/toolchain` and had `doctor` import
it. `tests/test_fp_toolchain.py`'s grep pin — **nothing under `build/`, `core/`,
`providers/`, `runtime/` may reference the toolchain-manifest module; the
manifest is evidence, never an input** — **fired**, and it fired **even on a
comment literal** naming the module (the W3 comment-literal lesson repeating).
Relocated to `media/ffmpeg` — `doctor` already imports `media` (`media.card`),
and `core/toolchain` **lazily delegates** to it. The manifest boundary holds:
`build/` may import `media`, never `core.toolchain`.

### Tests

- `tests/test_windows_archive.py` (**10**) — pack portability warnings (3),
  `--json` portability block (1), junction/reparse-dir prune (2 stub) + the
  POSIX-real symlinked-directory prune (1, added during implementation) + the
  POSIX-inert probe (folded in), unpack casefold refusal (2), cross-destination
  restore (1). Red-first: **6 red / 3 already-green pins** before any production
  change.
- `tests/test_windows_hwencode.py` (**9**) — candidate set + inventory (real,
  missing-ffmpeg, process-cached) (4), manifest encoder block (1), eligibility
  pure predicate (2), never-auto-enables source grep (1), doctor informational
  row (1). Red-first: collection died `ImportError: HW_ENCODER_CANDIDATES`.
- `tests/test_windows_bench.py` (**1**, env-gated → **skipped** in normal runs)
  — `MANJU_BENCH=1` opt-in; prints cold/warm/final wall times for a 3×2s
  640×360 project; asserts only sanity floors.

### CLI / contracts

- **No new schema, no new contract id.** `CONTRACTS.yaml`'s `manjupkg` and
  toolchain-manifest entries' **notes** are extended (pack portability warnings +
  the additive `encoders` fact block); `test_fp_contracts.py` stays green both
  directions.
- **No new command surface.** The frozen CLI surface
  (`tests/fixtures/cli_surface.json`) is unchanged: `pack` gains warnings and a
  drop-when-empty `--json` block (no new flag), `unpack` gains a refusal path,
  `doctor` gains one informational row — none is a command.

### Design deviations (honest)

- **C2PA / provenance REJECTED_WITH_REASON** — no distribution chain, no
  verifier, no key lifecycle for a single user; a heavy `c2pa` dependency with no
  consumer; integrity is already carried by `MANJU_FIXITY.json` + the BagIt
  manifests. Recording the rejection is the W5.1 deliverable.
- **`video_encoder` project switch REJECTED** — consuming eligibility needs the
  full encode lockstep + a VERIFIED canary; eligibility without a consumer is
  deliberately inert evidence, not dead config.
- **Clock-based perf REPORT beside `runperf` REJECTED** — the forbidden parallel
  system; the opt-in bench prints for the human and asserts sanity floors.
- **The reparse `nt` branch is stub-driven** — no real junction exists on any
  authoring host; the POSIX tests drive it via `monkeypatch` and the
  symlinked-dir case is real (the W1 stub-driven honesty; a real-host first run,
  in the risks).

## Compatibility

- **The POSIX walk is byte-identical.** `_dir_is_reparse_point()` answers
  `False` for every directory on POSIX; the `nt` `lstat` branch never runs
  there — archive membership is unchanged for every existing tree
  (`normal_dir/kept.bin` still packed, test-proven).
- **pack warns but never blocks.** A clean tree emits **no** warning and **no**
  `--json` portability block; a hazardous tree still packs **everything** — a
  backup is never refused.
- **unpack's casefold refusal is OS-independent and deterministic.** A
  collision-free archive extracts unchanged; case-varied-but-distinct names are
  accepted; identical repeated dir entries are not collisions.
- **The manifest `encoders` block is additive and record-only.** S4 cache keys
  read the `-version` line only, so no rebuild is triggered; the manifest is
  never a build input (`test_fp_toolchain` boundary intact).
- **Every encode path stays `libx264` byte-for-byte.** Eligibility never flips a
  default — `render._SEG_ENC`/`_enc_params` are literal-pinned and a source grep
  proves `render`/`normalize` never consult the inventory.
- **The benchmark is env-gated → skipped in every normal run.** `qc/runperf` and
  the §8.7 pins are untouched.
- **The `_req` retry weakens no assertion.** Only transient loopback aborts are
  retried (bounded, 3×); HTTP statuses (even 5xx) are real results and never
  retried; three aborts still fail loudly.

## Verification

Targeted suites on the authoring host (Linux, full tooling):

- The new files — **`tests/test_windows_archive.py` 10 passed**,
  **`tests/test_windows_hwencode.py` 9 passed**,
  **`tests/test_windows_bench.py` 1 skipped** (env-gated; `MANJU_BENCH=1`
  prints its numbers with `-s`).
- **Guard suites green after the change:** `fixity` + `bagit` + `security` +
  `archive_meta` + `cli_snapshot` (**68**, incl. the archive tests);
  `fp_toolchain` + `fp_toolkeys` + `windows_doctor` + `windows_color` +
  `hwencode` (**91**); `shot_lab` (**26**) — the fixity/BagIt/security,
  manifest-boundary, doctor, colour and shot-lab neighbours all hold.

Full suite (`pytest -n auto`, authoring host): **4311 passed, 2 skipped, 0 failed (511.64s) — the 4292 W4 tip plus the 19 new W5 tests (the 20th is the env-gated bench, the second skip), zero regressions**.

Windows CI (`windows-ci.yml` full suite + `install-smoke`): **run #11 triggered by the 85cdb21 push (run #9 green on the W4 code; run #10's docs-only flake is fixed by the _req retry in this very commit — verdict recorded in DECISIONS when it lands)**.
Honest expectation: **green**. The junction probe is exercised for real **only**
on the Windows host (the POSIX tests drive it stubbed + the symlink-dir case
real); the casefold gate and the pack warnings are **OS-independent**; the
hw-encode facts are read-only and the bench is env-gated (skipped). The
`_req` retry removes the run-#10 loopback flake without weakening any assertion.

Method: `python -m pytest` only; red-first pins for every new behavior (both new
files ran red — behavioral for archive, `ImportError` for hwencode — before any
production change); no existing test deleted or weakened.

## Skipped / Rejected

| Item | Reason |
|------|--------|
| C2PA / provenance (W5.1) | **REJECTED_WITH_REASON** — a personal single-user archive has no distribution chain and no verifier; `c2pa` adds a signing-key lifecycle + a heavy dependency with no consumer; integrity is already carried by `MANJU_FIXITY.json` + the BagIt manifests. Recording the rejection is the deliverable |
| `video_encoder` project switch | **REJECTED** — consuming eligibility needs the full encode lockstep (render `_SEG_ENC` + `_enc_params` + `normalize._encode_args` + every cache key + concat profile) and a VERIFIED canary; eligibility without a consumer is deliberately inert evidence, not dead config |
| a clock-based perf REPORT beside `runperf` | **REJECTED** — exactly the parallel system the plan forbids; the opt-in bench prints for the human and asserts sanity floors instead. The §8.7 pins (no `build/` reference, no wall clock in `runperf`) stay untouched |

## Final

**Commit SHA:** `85cdb21` (W5 archive hardening + hw-encode facts + the
opt-in bench + the flake fix, with tests; this report and the DECISIONS #41
follow-up land in the docs commit).

**Remaining risks:**

1. **No REAL junction has been packed on any host yet.** The `nt` branch of
   `_dir_is_reparse_point` (`lstat` `st_file_attributes & 0x400`) runs for the
   **first time on the gate/user machine** — the POSIX tests drive it stubbed
   (the W1 stub-driven honesty) and the symlinked-directory case is real; the
   junction path itself is real-host-first.
2. **LISTED hw encoders are unverified by construction.** `hw_encode_eligibility`
   reports `LISTED`, never `VERIFIED` — a listed encoder still fails at runtime
   without its driver (this container lists nvenc/qsv with no GPU). Anyone
   enabling hardware encoding in the future **must build the VERIFIED canary
   first**; the facts are inert evidence until then.
3. **The pack warnings are advisory.** A user who ignores them still produces an
   archive their own `unpack` refuses (unportable members) or that loses a file
   on an NTFS restore (casefold collisions). This is **deliberate**: backup
   completeness beats portability — a backup is never blocked.
4. **The transient-abort retry masks a genuinely sick server for up to 2
   retries.** It is **bounded** (3 attempts, 0.3s backoff) and three aborts in a
   row still fail loudly; HTTP statuses — even 5xx — are real results and are
   never retried, so no assertion is weakened.
