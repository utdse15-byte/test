# FP Loop L4 — process-scoped `hash_file` memo (optimization audit Tier-1 #3)

## The finding (refuter CONFIRMED)

Real builds hash each source **at least twice**:

- `media/render.py` — `_final_key_payload` (`:1129-1138`) computes every segment
  key (each `_segment_cache_key` calls `hash_file(src)` at `:379`), then
  `_build_segment` (`:438-442`) recomputes the identical key → the same source
  is streamed and SHA-256'd a second time.
- `build/graph.py` — each animatic still is hashed twice: `hash_file(still)` at
  `:217` (Ken-Burns key) and again at `:231` (`seg_keys` entry).

SHA-256 is CPU-bound (~370 MB/s measured) and `hash_file` (`core/hashing.py:38-44`)
was unmemoized, so every double-hash re-streamed the whole file.

## The fix (the audit's chosen option)

A **process-scoped memo INSIDE `core.hashing.hash_file`**, keyed on
`(resolved absolute path, st_size, st_mtime_ns)`. Digests stay **byte-identical**,
so **every** double-hash site collapses at one seam and **no cache-key discipline
changes** — the render/graph key code is untouched (the narrow-hoist alternative
was therefore unnecessary and is not taken). Files changed:

| File | Change |
|------|--------|
| `src/manju/core/hashing.py` | `hash_file` wraps the historical body (`_hash_file_stream`) with the memo; adds `_hash_cache` (LRU `OrderedDict`), `_hash_cache_lock`, `_HASH_CACHE_MAXSIZE=4096`, `_reset_hash_cache()` (test hygiene). Signature and error behaviour unchanged. |
| `tests/test_fp_hash_memo.py` | NEW — 13 red-first pins (see inventory). |
| `REPORTS/FP_HASH_MEMO.md` | this report. |

`render.py` / `graph.py` call sites were **NOT** edited — the memo alone collapses them.

## Binding design constraints — how each is met

1. **Correctness first.** Every call `os.stat`s the file. A hit requires **both**
   `st_size` **and** `st_mtime_ns` to match the cached stat. The one theoretical
   staleness window (a same-size rewrite within a single mtime tick) is stated in
   the docstring and pinned as an honest characterization test — see below.
2. **Kill-switch.** `MANJU_NO_HASH_CACHE=1` is checked **per call** (one cheap
   `os.environ.get`) at the very top and bypasses the memo entirely (no stat, no
   dict) — the call is then byte-for-byte the original streaming hash.
3. **Bounded memory.** LRU cap of 4096 entries via `OrderedDict` +
   `move_to_end` (promote on hit/insert) + `popitem(last=False)` (evict oldest).
   A long GUI session cannot grow the memo without bound (~1 MB at the cap).
4. **Thread-safety.** A `threading.Lock` guards **every** dict read/promote and
   write/evict (LRU `move_to_end` + `popitem` + `len` is a read-modify-write, so
   plain CPython dict atomicity is insufficient — the lock is the explicit,
   correct choice). The SHA-256 itself runs **OUTSIDE** the lock so concurrent
   misses on large files never serialize on the CPU; two threads may redundantly
   hash the same brand-new file (a benign, idempotent double-compute) but the
   dict is never mutated unlocked.
5. **Signature / error behaviour unchanged.** `hash_file(path, chunk_size=1<<20)`.
   A missing file raises `FileNotFoundError` exactly as before — the memo catches
   `OSError` from `os.stat` and **falls through** to `_hash_file_stream`, whose
   `open()` raises the original error. Verified with the memo on, under the
   kill-switch, and via the stat-fall-through path.

**Key uses `os.path.realpath`** (strictly better than `abspath`): a file reached
via a symlink or a `..`-laden path shares one cache entry, and two genuinely
distinct files never collide. Cost is one `realpath` (µs) vs. re-hashing (ms+).

## The documented staleness window (verbatim from the docstring)

> Correctness / staleness: a hit requires BOTH `st_size` AND `st_mtime_ns` to
> match the cached stat. The one theoretical staleness window is a same-size
> rewrite that lands within a single mtime tick (identical `st_mtime_ns`): the
> memo would then return the prior (now stale) digest. This is acceptable here
> because manju runs on ns-resolution filesystems (distinct mtime_ns per write in
> practice), manju media is append-only (new bytes take a new path / grow the
> size), and truth files are small and cheap to re-hash anyway — the window is
> not engineered away, it is bounded and documented. `MANJU_NO_HASH_CACHE=1`
> bypasses the memo entirely for any caller that cannot tolerate even the
> theoretical window.

Measured on this env (ext2/ext3, Linux 6.18.5): five consecutive same-size
rewrites yielded **five distinct** `st_mtime_ns` — the window does not occur in
practice here. The `..._returns_stale` pin forces the window with `os.utime`
(pinning mtime_ns back) to prove the behaviour is exactly as documented and that
the kill-switch always sees the truth.

## Pin inventory (`tests/test_fp_hash_memo.py`, 13 pins)

| Pin | Proves |
|-----|--------|
| `test_second_call_on_unchanged_file_does_not_reread` | 2nd call is a stat-only hit → content read **once** (RED at HEAD: 2). |
| `test_render_segment_double_hash_collapses` | real `_segment_cache_key` twice → source read **once** (RED at HEAD: 2). |
| `test_render_segment_double_hash_re_reads_when_cache_disabled` | control: kill-switch → same seam reads **twice**. |
| `test_same_size_content_change_with_touched_mtime_recomputes` | same size + touched mtime → miss → correct new digest. |
| `test_different_path_same_content_gets_correct_digest_and_own_entry` | two files, identical content → same digest, independent entries. |
| `test_parity_with_fresh_no_cache_process_including_cjk_names` | digest == fresh `MANJU_NO_HASH_CACHE=1` subprocess == hashlib, incl. CJK names. |
| `test_kill_switch_bypasses_memo_and_re_reads` | kill-switch re-reads every call, digest still correct. |
| `test_kill_switch_is_per_call_not_cached_at_import` | switch read per call; switch-era calls admit nothing to the memo. |
| `test_lru_evicts_oldest_and_next_call_rereads` | cap exceeded → LRU evicted (re-read) while MRU stays cached. |
| `test_cache_never_exceeds_cap` | live entry count ≤ cap after hashing far more files. |
| `test_thread_hammer_all_digests_correct_no_exception` | 12 threads × 40 files under a small cap (get/set + eviction race) → all correct, no raise. |
| `test_documented_staleness_window_same_size_same_mtime_returns_stale` | honest: forced same-tick collision → stale hit; kill-switch sees truth. |
| `test_signature_and_error_behaviour_unchanged` | missing file → `FileNotFoundError` (memo on / kill-switch); `chunk_size` honoured. |

## Verification (targeted `pytest`; no full suites, no network, no git ops)

- New suite + canonical hashing: `test_fp_hash_memo.py` + `test_hashing.py` → **25 passed**.
- Full verification set (fixity/pack goldens are the heaviest hash consumers;
  their digests are untouched): `test_fp_hash_memo test_fp_fixity test_fp_bagit
  test_c17_packs test_fp_ratemig1 test_fp_toolkeys test_fp_ratemig2
  test_transitions_looks test_media_durability test_audio_edit` → **169 passed**.
- Global-regression safety net (the only open-counting test + hash-after-mutation):
  `test_final_acceptance test_dr02_intake` → **37 passed**. `write_bundle` streams
  members with a direct `open()` (not `hash_file`), so the memo cannot change its
  read count; content mutations bump `mtime_ns`, so the memo correctly misses.

### Note on the pack-determinism flake

While verifying, two `test_fp_bagit.py` determinism pins flaked under one batch
ordering. Root cause (confirmed by the orchestrator, independently reproduced from
CI, and pre-dating this work): `_seed` refreshed `probe.bin` / `场记/第一场.txt`
mtimes before every `_pack`, and ZIP members quantize mtimes to 2-second DOS
ticks — when two seeds straddled a tick the archives differed in exactly the two
DOS mod-time bytes (hashes and CRCs identical, which is why paranoid stale-hit
detection found **no** digest mismatch). **Not caused by the memo.** Fixed on the
branch in commit `ba68585` (`_seed` made idempotent); the full verification set is
green with that fix in tree.
