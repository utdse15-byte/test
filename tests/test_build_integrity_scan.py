"""build/ integrity — regressions found by the hourly build/ scan (adversarially
reproduced before fixing). Behavioral pins, never source-text greps.

1. attempts.py's three readers (read_attempts / read_submission_events /
   read_run_lifecycle) opened events.jsonl in strict text mode and iterated
   lines, so a torn write ending mid-multibyte-char raised UnicodeDecodeError
   out of the reader instead of counting the line as malformed — bricking the
   projection (and crashing `manju run manifest`, which calls read_attempts
   unwrapped), contrary to the "torn tail counted as malformed" contract.
2. ingest._register_manual_voice_take copied straight to the append-only name
   with no cleanup on a failed copy, leaving a truncated, sidecar-less voice
   take that build treats as MANUAL truth (never auto-invalidated).
3. segments._ranges' except tuple omitted KeyError, so a dict-shaped range
   entry ({"start_ms": 0, ...}) made r[0] raise KeyError uncaught instead of the
   intended CutdownError.
4. segments._hashable converted only a TOP-LEVEL list, so a dict or nested-list
   reorder entry stayed unhashable and set(keys) raised a raw TypeError out of
   validate_cutdown.
5. graph._provider_semaphore populated its cache with an unsynchronized
   check-then-set spanning a get_manifest() disk read, so racing workers could
   build separate semaphores and exceed max_concurrent.
"""

from __future__ import annotations

import json
import threading
import time

import pytest


# --------------------------------------------------------------------------- #
# (1) attempt-stream readers survive a torn multibyte write                     #
# --------------------------------------------------------------------------- #


def _write_torn_events(tmp_path):
    ev = tmp_path / "events.jsonl"
    valid = {"action": "stage_attempt", "ts": "2026-07-18T00:00:00Z",
             "detail": {"run_id": "run_x", "state": "SUCCEEDED",
                        "sequence": 1, "attempt_id": "att_1"}}
    ev.write_bytes(json.dumps(valid, ensure_ascii=False).encode("utf-8") + b"\n")
    with open(ev, "ab") as f:  # torn: first 2 bytes of the 3-byte char 中
        f.write('{"action":"stage_attempt","detail":{"run_id":"run_x","p":"中'
                .encode("utf-8")[:-1])
    return tmp_path


def test_read_attempts_counts_torn_multibyte_not_crash(tmp_path):
    from manju.build import attempts

    d = _write_torn_events(tmp_path)
    records, malformed = attempts.read_attempts(d, "run_x")  # must not raise
    assert len(records) == 1 and malformed == 1


def test_other_attempt_readers_survive_torn_multibyte(tmp_path):
    from manju.build import attempts

    d = _write_torn_events(tmp_path)
    # neither sibling reader may raise on the same torn tail
    attempts.read_submission_events(d)
    attempts.read_run_lifecycle(d, "run_x")


# --------------------------------------------------------------------------- #
# (2) ingest voice registration cleans up a partial copy                        #
# --------------------------------------------------------------------------- #


def test_register_manual_voice_take_unlinks_partial_on_failure(tmp_path, monkeypatch):
    import manju.build.ingest as ingest

    class _StubProject:
        def takes_dir(self, shot_id):
            p = tmp_path / "gen" / shot_id
            p.mkdir(parents=True, exist_ok=True)
            return p

        def next_voice_take_name(self, shot_id):
            return "voice_take_01"

    def _failing_copy(src, dst):
        __import__("pathlib").Path(dst).write_bytes(b"PARTIAL")  # truncated file
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ingest.shutil, "copy2", _failing_copy)
    src = tmp_path / "v.wav"
    src.write_bytes(b"x")

    with pytest.raises(OSError):
        ingest._register_manual_voice_take(_StubProject(), "S001", src)
    # the truncated, sidecar-less voice take must NOT survive (it would be MANUAL
    # truth build never auto-invalidates)
    dest = tmp_path / "gen" / "S001" / "voice_take_01.wav"
    assert not dest.exists()


# --------------------------------------------------------------------------- #
# (3, 4) validate_cutdown never leaks a raw KeyError/TypeError                   #
# --------------------------------------------------------------------------- #


def test_ranges_dict_entry_is_cutdown_error_not_keyerror():
    from manju.build import segments

    with pytest.raises(segments.CutdownError):
        segments.validate_cutdown(
            {"keep": [{"start_ms": 0, "end_ms": 100}], "remove": [],
             "source_analysis_digest": "x"},
            media_duration_ms=100)


@pytest.mark.parametrize("reorder", [
    [{"seg": 0}, {"seg": 0}],          # dict entries -> was TypeError (unhashable dict)
    [[[0, 50], [50, 100]]],            # nested list -> was TypeError (unhashable list)
])
def test_validate_cutdown_unhashable_reorder_no_typeerror(reorder):
    from manju.build import segments

    # must return diagnostics (a list), never raise a raw TypeError
    diags = segments.validate_cutdown(
        {"keep": [[0, 100]], "remove": [], "reorder": reorder,
         "source_analysis_digest": "x"},
        media_duration_ms=100)
    assert isinstance(diags, list)


def test_validate_cutdown_duplicate_dict_reorder_flags_identity():
    from manju.build import segments

    diags = segments.validate_cutdown(
        {"keep": [[0, 100]], "remove": [], "reorder": [{"seg": 0}, {"seg": 0}],
         "source_analysis_digest": "x"},
        media_duration_ms=100)
    assert any(d.get("code") == "REORDER_IDENTITY" for d in diags)


# --------------------------------------------------------------------------- #
# (5) provider semaphore is populated exactly once under concurrency            #
# --------------------------------------------------------------------------- #


def test_provider_semaphore_populated_once_under_concurrency(monkeypatch):
    from manju.build import graph
    import manju.providers.registry as registry

    calls = {"n": 0}

    class _Limits:
        max_concurrent = 2

    class _Manifest:
        limits = _Limits()

    def _slow_get_manifest(name):
        calls["n"] += 1
        time.sleep(0.05)  # simulate the GIL-releasing disk read the race spans
        return _Manifest()

    monkeypatch.setattr(registry, "get_manifest", _slow_get_manifest)

    cache: dict = {}
    results: list = []
    n = 8
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()  # all start together, maximizing the race window
        results.append(graph._provider_semaphore("capped", cache))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # exactly one populate (pre-fix: each racing worker missed the cache and
    # called get_manifest, building separate semaphores)...
    assert calls["n"] == 1
    # ...and every worker shares the SAME semaphore object (the real cap).
    assert len({id(r) for r in results}) == 1
