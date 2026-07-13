"""FP Loop L4 — the process-scoped ``hash_file`` memo (audit Tier-1 #3).

Audit finding (refuter CONFIRMED): real builds hash each source at least twice —
``media/render.py`` ``_final_key_payload`` computes every segment key (each
``_segment_cache_key`` calls ``hash_file(src)``), then ``_build_segment``
recomputes the identical key (``hash_file(src)`` again); ``build/graph.py`` hashes
each animatic still twice (``hash_file(still)`` at :217 and again at :231).
SHA-256 is CPU-bound (~370 MB/s measured) and ``hash_file`` was unmemoized.

The chosen fix is a process-scoped memo INSIDE ``core.hashing.hash_file`` keyed on
``(resolved absolute path, st_size, st_mtime_ns)`` — digests stay byte-identical,
every double-hash site collapses at once, and no cache-key discipline changes.

This suite is RED-FIRST against HEAD: the core behavioural tests
(:func:`test_second_call_on_unchanged_file_does_not_reread`,
:func:`test_render_segment_double_hash_collapses`) prove the double-read at HEAD
and the single-read after the memo. It also pins correctness (parity to a fresh
no-cache process incl. CJK names), the kill-switch, LRU eviction, thread-safety,
the *documented* staleness window, and that the signature / error behaviour of
``hash_file`` is untouched.
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from manju.core import hashing
from manju.core.hashing import HASH_PREFIX, hash_file

# ---------------------------------------------------------------- test seam


@pytest.fixture(autouse=True)
def _isolate_hash_cache(monkeypatch):
    """Every test starts with the kill-switch OFF and a clean cache.

    The reset hook is resolved with ``getattr`` so the two behavioural tests
    that need no new symbols still RUN (and go red) against un-memoized HEAD.
    """
    monkeypatch.delenv("MANJU_NO_HASH_CACHE", raising=False)
    reset = getattr(hashing, "_reset_hash_cache", None)
    if reset is not None:
        reset()
    yield
    if reset is not None:
        reset()


@contextlib.contextmanager
def count_reads(*targets: Path):
    """Count content reads (``builtins.open``) of specific files.

    This is an *observable*, not a timing signal: the memo's ``os.stat`` never
    goes through ``builtins.open``, so an ``open`` of a target path == a genuine
    content read. A cache hit stats only and never opens.
    """
    real_open = builtins.open
    wanted = {os.path.realpath(os.fspath(t)) for t in targets}
    counts: dict[str, int] = {os.path.realpath(os.fspath(t)): 0 for t in targets}

    def spy(file, *a, **k):
        try:
            rp = os.path.realpath(os.fspath(file))
            if rp in wanted:
                counts[rp] += 1
        except (TypeError, ValueError, OSError):
            pass
        return real_open(file, *a, **k)

    builtins.open = spy
    try:
        yield counts
    finally:
        builtins.open = real_open


def _reads(counts: dict[str, int], path: Path) -> int:
    return counts[os.path.realpath(os.fspath(path))]


def _hashlib_truth(path: Path) -> str:
    """Ground-truth digest computed with no manju code in the path at all."""
    return HASH_PREFIX + hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------- (1) the collapse itself


def test_second_call_on_unchanged_file_does_not_reread(tmp_path):
    """RED at HEAD: an unmemoized ``hash_file`` opens the file on EVERY call, so
    two calls read twice. GREEN with the memo: the second call is a stat-only
    cache hit — the content is read exactly ONCE — and returns the same digest."""
    f = tmp_path / "clip.bin"
    f.write_bytes(b"\x00\x01video-bytes-" + b"Z" * 4096)

    with count_reads(f) as counts:
        first = hash_file(f)
        second = hash_file(f)

    assert first == second == _hashlib_truth(f)
    assert _reads(counts, f) == 1, "second call must not re-read content"


def test_render_segment_double_hash_collapses(tmp_project):
    """Integration seam on the REAL render key function. ``_segment_cache_key`` is
    the exact routine both render double-hash sites call: ``_final_key_payload``
    computes it for every segment, then ``_build_segment`` recomputes it. Calling
    it twice on one clip reproduces the audit's double-hash. RED at HEAD: the
    source is read twice. GREEN with the memo: read ONCE, keys byte-identical.

    ``build/graph.py``'s :217/:231 ``hash_file(still)`` pair collapses by the
    identical mechanism (same file path, same memo) — proven at the unit seam in
    :func:`test_second_call_on_unchanged_file_does_not_reread`; the graph path
    itself invokes ffmpeg (``kenburns``) and is deliberately not driven here."""
    from manju.core.models import VideoClip
    from manju.media.render import _segment_cache_key

    project = tmp_project
    src = project.root / "seg_src.bin"
    src.write_bytes(b"deterministic-segment-source-bytes-" + b"S" * 2048)
    clip = VideoClip(shot="S001", take="take_01", source="seg_src.bin",
                     start_ms=0, duration_ms=4000)
    kw = dict(width=1080, height=1920, fps=24, target="final",
              fade_in_ms=0, fade_out_ms=0)

    with count_reads(src) as counts:
        key_from_payload = _segment_cache_key(project, clip, **kw)   # _final_key_payload
        key_from_build = _segment_cache_key(project, clip, **kw)     # _build_segment

    assert key_from_payload == key_from_build
    assert _reads(counts, src) == 1, "render double-hash did not collapse to one read"


def test_render_segment_double_hash_re_reads_when_cache_disabled(tmp_project, monkeypatch):
    """Control for the collapse: with the kill-switch the SAME render seam reads
    the source twice — proving the single read above is the memo, not an artifact
    of the seam."""
    from manju.core.models import VideoClip
    from manju.media.render import _segment_cache_key

    monkeypatch.setenv("MANJU_NO_HASH_CACHE", "1")
    project = tmp_project
    src = project.root / "seg_src.bin"
    src.write_bytes(b"deterministic-segment-source-bytes-" + b"S" * 2048)
    clip = VideoClip(shot="S001", take="take_01", source="seg_src.bin",
                     start_ms=0, duration_ms=4000)
    kw = dict(width=1080, height=1920, fps=24, target="final",
              fade_in_ms=0, fade_out_ms=0)

    with count_reads(src) as counts:
        k1 = _segment_cache_key(project, clip, **kw)
        k2 = _segment_cache_key(project, clip, **kw)

    assert k1 == k2
    assert _reads(counts, src) == 2, "kill-switch must not collapse the double-hash"


# ----------------------------------------------------------- (2) correctness


def test_same_size_content_change_with_touched_mtime_recomputes(tmp_path):
    """A same-SIZE rewrite that touches mtime_ns MUST recompute (memo miss on
    mtime), and the new digest must be correct."""
    f = tmp_path / "same_size.bin"
    f.write_bytes(b"A" * 64)
    first = hash_file(f)
    assert first == _hashlib_truth(f)

    # Rewrite the SAME number of bytes, then touch mtime to an explicit, distinct
    # value so the assertion is deterministic on any filesystem granularity.
    f.write_bytes(b"B" * 64)
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    second = hash_file(f)
    assert second != first
    assert second == _hashlib_truth(f)


def test_different_path_same_content_gets_correct_digest_and_own_entry(tmp_path):
    """Two distinct files with byte-identical content each hash correctly (the
    digest IS the same value — content-addressed) and each occupies its own memo
    entry keyed by its own resolved path (both remain cached, no cross-talk)."""
    payload = b"identical-content-" + b"Q" * 1024
    a = tmp_path / "a.bin"
    b = tmp_path / "sub" / "b.bin"
    b.parent.mkdir()
    a.write_bytes(payload)
    b.write_bytes(payload)

    da = hash_file(a)
    db = hash_file(b)
    assert da == db == _hashlib_truth(a)          # content-addressed: same digest

    # both are independently cached: re-hashing neither re-reads.
    with count_reads(a, b) as counts:
        assert hash_file(a) == da
        assert hash_file(b) == db
    assert _reads(counts, a) == 0 and _reads(counts, b) == 0


def test_parity_with_fresh_no_cache_process_including_cjk_names(tmp_path):
    """Every digest is byte-identical to a FRESH no-cache process (subprocess with
    MANJU_NO_HASH_CACHE=1) and to raw hashlib — including CJK filenames."""
    files = [
        tmp_path / "ascii.bin",
        tmp_path / "雨夜便利店.mp4",       # CJK basename
        tmp_path / "林夏" / "素材_01.mov",  # CJK dir + basename
    ]
    files[2].parent.mkdir()
    for i, f in enumerate(files):
        f.write_bytes(f"content-{i}-".encode("utf-8") + bytes(range(256)) * (i + 1))

    for f in files:
        cached = hash_file(f)
        assert cached == _hashlib_truth(f)

        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; from manju.core.hashing import hash_file;"
             "sys.stdout.write(hash_file(sys.argv[1]))",
             str(f)],
            env={**os.environ, "MANJU_NO_HASH_CACHE": "1"},
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == cached, f"parity mismatch for {f.name}"


# ----------------------------------------------------------- (3) kill-switch


def test_kill_switch_bypasses_memo_and_re_reads(tmp_path, monkeypatch):
    """MANJU_NO_HASH_CACHE=1 bypasses the memo entirely (checked per call): every
    call re-reads content. Correctness is preserved (digest still right)."""
    f = tmp_path / "clip.bin"
    f.write_bytes(b"kill-switch-bytes-" + b"K" * 2048)

    monkeypatch.setenv("MANJU_NO_HASH_CACHE", "1")
    with count_reads(f) as counts:
        d1 = hash_file(f)
        d2 = hash_file(f)
    assert d1 == d2 == _hashlib_truth(f)
    assert _reads(counts, f) == 2, "kill-switch must re-read on every call"


def test_kill_switch_is_per_call_not_cached_at_import(tmp_path, monkeypatch):
    """The switch is read per call: flipping it mid-process takes effect, and a
    value written under the switch is NOT admitted to the memo."""
    f = tmp_path / "clip.bin"
    f.write_bytes(b"per-call-switch-" + b"P" * 1024)

    monkeypatch.setenv("MANJU_NO_HASH_CACHE", "1")
    with count_reads(f) as counts:
        hash_file(f)                      # bypassed: nothing cached
        hash_file(f)
    assert _reads(counts, f) == 2

    monkeypatch.delenv("MANJU_NO_HASH_CACHE", raising=False)
    with count_reads(f) as counts:
        first = hash_file(f)              # miss (switch-era call cached nothing)
        second = hash_file(f)             # hit
    assert _reads(counts, f) == 1
    assert first == second == _hashlib_truth(f)


# ------------------------------------------------------------- (4) LRU bound


def test_lru_evicts_oldest_and_next_call_rereads(tmp_path, monkeypatch):
    """With the cap exceeded, the LEAST-recently-used entry is evicted (its next
    hash re-reads) while a recently-used entry stays cached (no re-read)."""
    monkeypatch.setattr(hashing, "_HASH_CACHE_MAXSIZE", 2, raising=True)

    a = tmp_path / "a.bin"; a.write_bytes(b"a" * 100)
    b = tmp_path / "b.bin"; b.write_bytes(b"b" * 100)
    c = tmp_path / "c.bin"; c.write_bytes(b"c" * 100)

    hash_file(a)                 # cache: [a]
    hash_file(b)                 # cache: [a, b]
    hash_file(a)                 # touch a -> MRU; cache order: [b, a]
    hash_file(c)                 # insert c, cap=2 -> evict LRU 'b'; cache: [a, c]

    with count_reads(a, b, c) as counts:
        hash_file(a)             # still cached -> no read
        hash_file(c)             # still cached -> no read
        hash_file(b)             # evicted -> must re-read
    assert _reads(counts, a) == 0
    assert _reads(counts, c) == 0
    assert _reads(counts, b) == 1


def test_cache_never_exceeds_cap(tmp_path, monkeypatch):
    """The memo is strictly bounded: hashing far more files than the cap leaves
    the live entry count at or below the cap (a long GUI session cannot grow it
    without bound)."""
    monkeypatch.setattr(hashing, "_HASH_CACHE_MAXSIZE", 8, raising=True)
    for i in range(50):
        f = tmp_path / f"f{i}.bin"
        f.write_bytes(f"file-{i}".encode("ascii"))
        hash_file(f)
    assert len(hashing._hash_cache) <= 8


# --------------------------------------------------------- (5) thread safety


def test_thread_hammer_all_digests_correct_no_exception(tmp_path, monkeypatch):
    """N threads hashing M files concurrently — under a SMALL cap so get/set and
    eviction race — produce correct digests and never raise."""
    monkeypatch.setattr(hashing, "_HASH_CACHE_MAXSIZE", 16, raising=True)
    m = 40
    files = []
    truth = {}
    for i in range(m):
        f = tmp_path / f"m{i}.bin"
        f.write_bytes((f"payload-{i}-").encode("utf-8") + bytes([i % 256]) * (512 + i))
        files.append(f)
        truth[f] = _hashlib_truth(f)

    n_threads = 12
    errors: list[BaseException] = []
    results: list[tuple[Path, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        try:
            barrier.wait()
            for _ in range(6):
                for f in files:
                    d = hash_file(f)
                    with lock:
                        results.append((f, d))
        except BaseException as exc:  # noqa: BLE001 - surface any thread failure
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"thread(s) raised: {errors[:3]}"
    assert results, "no work recorded"
    for f, d in results:
        assert d == truth[f]


# ------------------------------------------- (6) documented staleness window


def test_documented_staleness_window_same_size_same_mtime_returns_stale(tmp_path):
    """HONEST characterization of the KNOWN, accepted limitation the docstring
    states: a same-SIZE rewrite that lands within one mtime tick (forced here by
    pinning mtime_ns back) is a memo HIT and returns the STALE digest.

    This is why the fix is safe in manju: ns-resolution filesystems make a real
    same-tick collision vanishingly unlikely, manju media is append-only, and the
    truth files are small (re-hash is cheap). The kill-switch exists for the rare
    caller who cannot tolerate even the theoretical window."""
    f = tmp_path / "twin.bin"
    f.write_bytes(b"A" * 32)
    st = f.stat()
    stale = hash_file(f)
    assert stale == _hashlib_truth(f)

    # Same size, different bytes, mtime_ns pinned back to the exact prior tick.
    f.write_bytes(b"B" * 32)
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert f.stat().st_mtime_ns == st.st_mtime_ns  # collision forced

    got = hash_file(f)
    assert got == stale                    # documented: returns the STALE digest
    assert got != _hashlib_truth(f)        # ...which no longer matches the bytes

    # The kill-switch is the escape hatch and always sees the truth.
    os.environ["MANJU_NO_HASH_CACHE"] = "1"
    try:
        assert hash_file(f) == _hashlib_truth(f)
    finally:
        del os.environ["MANJU_NO_HASH_CACHE"]


# ------------------------------------------- (7) signature / error behaviour


def test_signature_and_error_behaviour_unchanged(tmp_path, monkeypatch):
    """The memo must not change ``hash_file``'s signature or error behaviour: a
    missing file raises FileNotFoundError exactly as before — with the memo ON,
    with the kill-switch, and via a failed stat that falls through to open()."""
    missing = tmp_path / "does_not_exist.bin"

    with pytest.raises(FileNotFoundError):
        hash_file(missing)                       # memo ON: stat fails -> open() raises

    monkeypatch.setenv("MANJU_NO_HASH_CACHE", "1")
    with pytest.raises(FileNotFoundError):
        hash_file(missing)                       # kill-switch: original path raises
    monkeypatch.delenv("MANJU_NO_HASH_CACHE", raising=False)

    # chunk_size keyword still honoured (streaming across multiple reads).
    f = tmp_path / "chunked.bin"
    f.write_bytes(b"Z" * 4096)
    assert hash_file(f, chunk_size=16) == _hashlib_truth(f)
    assert hash_file(f, 16) == _hashlib_truth(f)  # positional too
