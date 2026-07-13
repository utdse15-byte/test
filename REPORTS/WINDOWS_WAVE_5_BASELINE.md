# Windows Wave 5 — Baseline (MANJU_WINDOWS_ONLY_LEAN_V3, W5: 条件性收尾 — 归档加固 / 硬件编码资格 / 基准 / 溯源 · conditional close-out)

Plan: **MANJU_WINDOWS_ONLY_LEAN_V3** — the W5 slice, the plan's **conditional**
close-out ask. Four sub-slices: **W5.1** C2PA provenance, **W5.2** archive/
restore hardening, **W5.3** benchmark, **W5.4** hw-encode qualification — each
scoped down to *only what carries its weight for a single-user pipeline*. W1
(Windows 硬 CI + 文件系统/进程安全), W2 (安装 / Portable / 升级 / Doctor), W3
(NLE 交换、字幕、字体、Board 审片) and W4 (色彩最小闭环) are landed; the Windows
hard gate is **green** (run #9). Date: 2026-07-13 · Repo: `/home/user/test`
(Manju) · Base = HEAD **b20c321** (the W4 docs-only commit — DECISIONS #40 + the
W4 reports), itself atop the W4 colour code commit **103be30** (the run #9 green
tip) and the W1–W3 line before it.

## Authoring environment — and the honest limit of what it can prove

Authored on the same **Linux container** (Ubuntu Noble) as W1–W4, Python
**3.11.15**, ffmpeg **6.1.1** (the exact version on both CI OSes). W5's honesty
gap is entirely about **filesystem and hardware primitives the authoring host
cannot manufacture**, and each gap is the reason a slice took the shape it did:

1. **The directory-smuggle is proven where a real primitive exists, stubbed
   where one cannot.** The symlinked-**directory** smuggle is **REAL on this
   host**: a genuine POSIX symlink, and `rglob` **traverses** it on 3.11 while
   the files inside are not themselves symlinks — so the per-file symlink skip
   never fires and an entire outside tree lands in the archive. The **junction**
   branch (`nt`-only `lstat` `st_file_attributes & 0x400`,
   `FILE_ATTRIBUTE_REPARSE_POINT`) has **no real junction on any host** — POSIX
   has none — so it is **stub-driven** in tests (`monkeypatch`, the W1 pattern
   for Windows-only primitives) and runs for real for the first time on the
   gate/user machine. `Path.is_junction()` would be the obvious probe but it is
   **3.12+** and the repo floor is **3.11**, so the manual `lstat` bit-test is
   the only portable-at-the-floor option.
2. **No GPU, no hardware encoder exists on any host — yet this ffmpeg LISTS
   two.** The authoring container's ffmpeg 6.1.1 **lists `h264_nvenc` and
   `h264_qsv`** with no GPU present. This is the **live proof** that "listed by
   ffmpeg" is a *build* fact, not a *runtime* verification: a listed encoder
   still fails at render time without its driver. It is exactly why hw-encode is
   delivered as **ELIGIBILITY facts** (`LISTED`, never `VERIFIED`) that
   **never auto-enable** an encode — see the scope decisions, and the risks in
   the COMPLETION.
3. **No distribution chain, no third-party verifier, no signing key exists for a
   personal archive.** C2PA/provenance chains prove authorship *to third
   parties*; a single-user archive has no third party, no verifier, and no key
   lifecycle to justify a heavy `c2pa` dependency with no consumer. This is why
   **W5.1 is rejected-with-reason**: the integrity need a personal user actually
   has is already carried by `MANJU_FIXITY.json` + the BagIt manifests.
4. **The gate is green going in — the only red in the interval was a pure
   environment flake.** Run #9 (`103be30`, the W4 colour code) is **GREEN** — W4
   held the gate. The only failure since was run #10 (`b20c321`, a **docs-only**
   commit that changed no source): exactly **one** failure,
   `tests/test_shot_lab.py::test_token_guard_on_mutations`, dying
   `ConnectionAbortedError` **WinError 10053** — an OS-level loopback abort under
   `xdist` load (**1 failed / 4275 passed** on a diff that touched no code). Not
   a regression; **fixed this wave as gate hygiene** (COMPLETION).

| tool | version / state (authoring host) |
|------|----------------------------------|
| Python | 3.11.15 (**floor 3.11** — `Path.is_junction()` is 3.12+, unavailable; the reparse probe uses `lstat` `st_file_attributes & 0x400` instead) |
| ffmpeg / ffprobe | 6.1.1-3ubuntu5 (**identical to both CI OSes**) |
| NTFS junction / directory reparse point | **none on any host** — the `nt` `lstat` branch is stub-driven in tests (W1 pattern), real only on the gate/user machine; the symlinked-**directory** smuggle is **REAL** here |
| GPU / hardware h264 encoder | **none** — yet this ffmpeg **LISTS** `h264_nvenc` + `h264_qsv` (the live proof **LISTED ≠ VERIFIED**) |
| C2PA signing key / `c2pa` tooling / distribution chain | **none** — no third-party verifier exists for a personal archive (why W5.1 is rejected) |
| OS | Linux (Ubuntu Noble) |

## Baseline test state at the base tip (b20c321)

W5 begins on a green gate. The decisive gate facts this wave inherits:

| `windows-ci.yml` run | verdict | note |
|----------------------|---------|------|
| Run #9 (W4 code `103be30`) | **GREEN — 0 failed** | W4 held the gate; full suite + `install-smoke`, choco ffmpeg 6.1.1, PYTHONUTF8=1, `-n auto`, no skip-as-green |
| Run #10 (docs-only `b20c321`) | **1 F / 4275 P** | the single **WinError 10053** loopback abort in `test_shot_lab::test_token_guard_on_mutations` — an OS-level flake on a diff that changed **no code**; not a regression |

There is no gate residue of *code* origin for W5 to attack — only the run-#10
flake, which is a test-infra hazard, not a product bug. Before any production
change, a focused audit established the base-tip status of every capability the
four W5 slices ask for; its rows are this Baseline's audit table.

## Audit summary — the W5 capabilities at b20c321

`ALREADY_IMPLEMENTED` = the invariant already holds; `PARTIAL` = present but
incomplete; `GAP` = does not exist anywhere; `BUG` = present and *wrong*. Owner =
the single module an implementer must extend, never a parallel one.

| Capability | Status | Owner |
|------------|--------|-------|
| pack refuses to smuggle a linked **directory** | **BUG** — `pack` skips symlink *files* (goal item 14) but `Path.is_symlink()` answers **False** for an NTFS junction and `rglob` **traverses** it: a junction inside the project smuggles an entire outside tree in (the directory edition of the same smuggle). Worse, a symlinked **directory** is traversed on **ANY OS** — the inner files are not themselves symlinks, so the per-file skip never fires (proven with a **REAL** symlink on this host, no Windows needed) | `manju.cli` pack walk (+ the `_dir_is_reparse_point` helper it lacks) |
| pack warns before it traps its own unpack | **GAP** — a tree containing `CON.wav` or a trailing-space name packs **silently** on POSIX, and the resulting archive is then **REFUSED** by `manju unpack` (the W1 member gate refuses Windows-unportable names). No pack-side warning existed | `manju.cli` pack (+ `core.idents` `windows_relpath_problems`) |
| casefold collisions anywhere in the archive path | **GAP** — `A.wav` vs `a.wav` are two files on ext4, **one** on NTFS; detected **NOWHERE**: pack silent, unpack extracts both and whichever lands last **silently wins** — data loss on a Windows restore | `manju.cli` pack (warn) + unpack (refuse) + `core.idents` `windows_collision_key` |
| unpack member gate (reserved/trailing/colon/ADS/traversal/absolute/symlink) | **ALREADY_IMPLEMENTED** — the W1 gate refuses these members up front; **solid**. Cross-drive restore is correct **by construction** (absolute pathlib paths, no cwd assumption) but was **UNPINNED** | `manju.cli` unpack |
| encoder facts / hw-encode awareness | **GAP** — the toolchain manifest records ffmpeg's `-version` line but **not which encoders it carries**; no hw-encode awareness at all. Empirical: this ffmpeg 6.1.1 **lists `h264_nvenc` + `h264_qsv` with NO GPU** — "listed" is a build fact, never a runtime verification | `media/ffmpeg.py` (inventory) + `core/toolchain.py` (record-only manifest) |
| a benchmark | **GAP** — none exists. `qc/runperf.py` is the §8.7 perf-view owner (derived from build evidence, deterministic, **never wall-clock**; pins: nothing under `build/` references `runperf`, `runperf` never reads the wall clock) — a second clock-based artifact beside it would be a forbidden parallel system | a new **opt-in** test, **never** `runperf` |
| C2PA / provenance | **GAP** — absent everywhere; no signing keys, no `c2pa` dependency, no distribution chain, no verifier | — (rejected; see below) |
| Plan W5 ask — the four sub-slices | **CONDITIONAL** — the plan marks W5 conditional: build **only** what carries its weight for a personal pipeline. Two are honest wins (archive hardening, hw-encode facts), one is an opt-in bench, one is dead ceremony (C2PA) — see Wave scope decisions | — |

## Red / empirical evidence — gathered before any production change

Both new test files were written and run **before any production change**. The
archive file's red is **behavioral**; the hwencode file's red is
**collection-level**; and two empirical facts (one hardware, one filesystem)
dictated the shape of the code that answers them. All run against ffmpeg
**6.1.1**, the exact CI version.

| # | Finding (before any production change) | Consequence for the design |
|---|----------------------------------------|----------------------------|
| 1 | `tests/test_windows_archive.py` ran first: **6 red** — the three pack warnings (unportable member, casefold collision, reparse-dir prune) absent, the `--json` **portability block** absent, the junction-prune stub raising **AttributeError** on the missing `_dir_is_reparse_point` helper, and `unpack` **accepting** casefold-colliding members instead of refusing — over **3 already-green pins** (clean-tree silence, case-distinct acceptance, cross-destination restore) | the archive-hardening shape: warn at pack (never block a backup), refuse at unpack (the W1 member-gate class), prune linked dirs wholesale, and pin the two behaviours already correct by construction |
| 2 | `tests/test_windows_hwencode.py` died at **collection**: `ImportError: HW_ENCODER_CANDIDATES` from `manju.media.ffmpeg` | the collection-level red — the inventory/eligibility API did not exist; `media/ffmpeg` is the owner of "what can this ffmpeg do" |
| 3 | The authoring container's ffmpeg 6.1.1 **LISTS `h264_nvenc` + `h264_qsv`** with **no GPU present** | `hw_encode_eligibility()` must report **`LISTED`, never `VERIFIED`** — nothing may treat a listed encoder as production-ready; a real canary encode is the only thing that proves runtime capability |
| 4 | The **POSIX-real symlinked-directory smuggle** (a real red class discovered while writing the prune, then pinned as a real test — no Windows needed): `rglob` **traverses** a symlinked directory on 3.11, and the inner files are not themselves symlinks, so the per-file symlink skip **never fires** — one link dir smuggles an entire outside tree in | the prune must operate on **DIRECTORIES wholesale**, not per file; `sorted(Path)` yields a parent before its children, so an ancestor check shields everything beneath a pruned root |
| 5 | `Path.is_junction()` is **3.12+**; the repo floor is **3.11** | the `nt` reparse probe must be the manual `lstat` `st_file_attributes & 0x400` (`FILE_ATTRIBUTE_REPARSE_POINT`), not the stdlib convenience; POSIX always answers `False` (byte-identical walk) |

## Wave scope decisions

- **W5.2 archive hardening — Implement (four items).** (a) A `nt`-only
  `_dir_is_reparse_point()` (`lstat` `st_file_attributes & 0x400`; POSIX always
  `False`) that prunes linked **directories** wholesale — symlinked dirs (any
  OS, real) and junctions (`nt`, stubbed) — with a **named warning**. (b)
  **WARN-only** portability facts at pack (unportable members + casefold groups,
  via the `core.idents` W1 owners) — **a backup is never blocked**: refusing to
  back up an existing tree risks data loss exactly when the user needs the
  backup. (c) `unpack` **REFUSES** casefold-colliding members up front, on
  **every OS** (the same class as the W1 reserved-name gate — a hazard that
  corrupts the restored tree). (d) **Pin** cross-destination restore (a
  different drive is the same code path by construction). `--json` gains a
  drop-when-empty `portability` block; a clean tree emits **no** warning text
  (the warning must never become ambient noise).
- **W5.4 hw-encode — Implement as ELIGIBILITY facts, never auto-enable.** Owner
  `media/ffmpeg.py` (the module that owns "what can this ffmpeg do"):
  `HW_ENCODER_CANDIDATES` (h264 family only — the pipeline's profile),
  `encoder_inventory()` (process-cached parse of `ffmpeg -encoders`; missing/
  broken ffmpeg → all-absent facts, never an error), `hw_encode_eligibility()`
  (a pure predicate → `NONE_LISTED` / `LISTED`, `LISTED ≠ VERIFIED`). The facts
  ride the **record-only** toolchain manifest (additive block, drift-visible,
  **never a build input**); doctor shows one **informational** row that never
  gates. The `nt`-vs-manifest boundary pin (`test_fp_toolchain`) dictates the
  home: `build/` may import `media`, never `core.toolchain` — see the boundary
  lesson in the COMPLETION.
- **W5.4 — REJECTED (recorded): an actual `video_encoder` project switch.**
  Consuming eligibility to change an encode needs the full lockstep (render
  `_SEG_ENC` + `_enc_params` + `normalize._encode_args` + every cache key +
  concat profile compatibility) **and a VERIFIED canary**. Eligibility without a
  consumer is deliberately **inert evidence** (doctor/status read it), not dead
  config.
- **W5.3 benchmark — Implement as an opt-in, env-gated test only.**
  `MANJU_BENCH=1` (the `MANJU_C21G_BENCH` precedent): prints cold/warm/final
  wall times for a 3×2s 640×360 project, asserts only sanity floors (outputs
  exist; warm ≤ 1.5×cold). **REJECTED**: a clock-based perf **REPORT** beside
  `qc/runperf` — that is exactly the parallel system the plan forbids; the §8.7
  pins (no `build/` reference, no wall clock in `runperf`) stay untouched.
- **W5.1 C2PA — REJECTED_WITH_REASON.** Provenance chains prove authorship to
  third parties; a personal single-user archive has no distribution chain and no
  verifier; `c2pa` tooling adds a signing-key lifecycle + a heavy dependency
  with **no consumer**; the integrity the user actually needs is already carried
  by `MANJU_FIXITY.json` + the BagIt manifests. **Recording the rejection IS the
  W5.1 deliverable.**
- **Gate hygiene — fix run #10's flake.** `test_shot_lab.py`'s `_req` helper
  gains a bounded transient-abort retry so a loopback `WinError 10053` no longer
  reds the gate on a no-code diff (COMPLETION); HTTP statuses stay real results,
  never retried.

## REPORTS paths

- `REPORTS/WINDOWS_WAVE_5_BASELINE.md` (this file)
- `REPORTS/WINDOWS_WAVE_5_COMPLETION.md`
