"""Core data-integrity / concurrency / robustness — regressions found by the
hourly deep scan of ``core/`` (adversarially reproduced before fixing).

Six genuine defects, each pinned red-first:

1. ``failures.read_failures`` crashed with an uncaught ``UnicodeDecodeError`` on
   a torn multibyte write in failures.jsonl (text-mode line iteration decoded
   OUTSIDE the try) — `manju failures` and the GUI failures view bricked. Now
   reads bytes and skips the corrupt line, mirroring events.py.
2. ``recents._lock``'s Windows/msvcrt branch omitted the per-name
   ``threading.Lock`` the other three quartet members pair with the byte lock,
   so it failed to serialize same-process threads → lost recents.json updates.
3. ``recents.touch_recent``'s cap loop evicted the just-opened (newest) entry
   when the store was already full of pinned entries.
4. ``hashing.canonical_json``/``hash_value`` raised ``TypeError`` on a
   ``datetime.date`` (which ``yaml.safe_load`` produces from an unquoted YAML
   date), crashing every value-hash lock / build over such a field.
5. ``container.next_take_name`` numbered only from ``*.yaml`` sidecars, so an
   orphan media file (crash between the media write and sidecar write) made
   ``register_take`` collide forever and raise "could not allocate exclusive
   take name".
6. ``refs.refs_report`` raised ``ValueError`` (crashing ``manju refs``) when a
   file under media/refs was a symlink resolving OUTSIDE the project root.

Behavioral pins — they exercise the functions, never grep source text.
"""

from __future__ import annotations

import threading
import time

import pytest
import yaml


# --------------------------------------------------------------------------- #
# (1) failures reader survives a torn multibyte write                           #
# --------------------------------------------------------------------------- #


def test_read_failures_survives_torn_multibyte_line(tmp_path):
    from manju.core import failures

    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    fp = tmp_path / "reports" / "failures.jsonl"
    with open(fp, "wb") as f:
        f.write(b'{"cause": "before", "level": "error"}\n')
        f.write(b'{"cause": "' + "好".encode()[:2] + b' torn"}\n')  # 2 of 3 bytes
        f.write(b'{"cause": "after", "level": "error"}\n')

    recs = failures.read_failures(tmp_path, 10)  # must not raise
    causes = {r.get("cause") for r in recs}
    assert "before" in causes and "after" in causes  # good records survive


# --------------------------------------------------------------------------- #
# (2) recents lock serializes same-process threads on the Windows model         #
# --------------------------------------------------------------------------- #


class _NoopMsvcrt:
    """Faithful model of the codebase's proven Windows fact (DECISIONS #38): the
    CRT byte lock does NOT exclude same-process threads — each fd's
    ``msvcrt.locking`` succeeds. If the byte lock were the only guard, all
    threads would enter the critical section together."""

    LK_NBLCK = 2
    LK_UNLCK = 0

    def locking(self, fd, mode, n):  # noqa: D401 - no-op by design
        return None


def test_recents_lock_serializes_same_process_threads(tmp_path, monkeypatch):
    import manju.core.recents as recents

    monkeypatch.setattr(recents, "fcntl", None)
    monkeypatch.setattr(recents, "msvcrt", _NoopMsvcrt(), raising=False)
    monkeypatch.setenv("MANJU_RECENTS", str(tmp_path / "recents.json"))

    n = 12
    state = {"cur": 0, "max": 0}
    guard = threading.Lock()
    barrier = threading.Barrier(n)

    def worker() -> None:
        barrier.wait()
        with recents._lock(recents.recents_path()):
            with guard:
                state["cur"] += 1
                state["max"] = max(state["max"], state["cur"])
            time.sleep(0.005)
            with guard:
                state["cur"] -= 1

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # exactly one holder at a time; pre-fix (byte lock only, no thread lock)
    # this reaches n because _NoopMsvcrt lets every thread in.
    assert state["max"] == 1


# --------------------------------------------------------------------------- #
# (3) touch_recent never evicts the just-opened project                         #
# --------------------------------------------------------------------------- #


def test_touch_recent_keeps_newest_over_full_pinned_store(tmp_path, monkeypatch):
    import manju.core.recents as recents
    from manju.core.container import PROJECT_FILE, Project

    monkeypatch.setenv("MANJU_RECENTS", str(tmp_path / "recents.json"))
    # fill the store with MAX_ENTRIES pinned entries
    recents._write_raw(recents.recents_path(), [
        {"path": f"/PIN_{i:02d}", "name": "p", "last_opened": "t", "pinned": True}
        for i in range(recents.MAX_ENTRIES)
    ])

    proj_dir = tmp_path / "myproj"
    proj_dir.mkdir()
    (proj_dir / PROJECT_FILE).write_text("name: myproj\n", encoding="utf-8")

    recents.touch_recent(Project(proj_dir))

    entries = recents.load_recents(drop_missing=False).entries
    paths = [e["path"] for e in entries]
    # the just-opened project is present AND at the head (newest), not evicted
    assert str(proj_dir.resolve()) in paths
    assert paths[0] == str(proj_dir.resolve())


# --------------------------------------------------------------------------- #
# (4) hashing tolerates date/datetime values from yaml.safe_load                #
# --------------------------------------------------------------------------- #


def test_hash_value_handles_yaml_date(tmp_path):
    from manju.core.hashing import hash_value

    raw = yaml.safe_load("meta:\n  aired: 2026-07-18\n")  # -> datetime.date
    h1 = hash_value(raw)  # must not raise TypeError
    h2 = hash_value(yaml.safe_load("meta:\n  aired: 2026-07-18\n"))
    assert h1.startswith("sha256:")
    assert h1 == h2  # deterministic / stable across calls


def test_verify_locks_over_date_field_does_not_crash():
    from manju.core.locks import verify_locks

    raw = yaml.safe_load("meta:\n  aired: 2026-07-18\n")
    # a value-hash lock over a date field must produce a verdict, never a crash
    verify_locks(raw, {"meta.aired": "sha256:deadbeef"}, "shots/S001.yaml")


# --------------------------------------------------------------------------- #
# (5) register_take advances past an orphan media file                          #
# --------------------------------------------------------------------------- #


def test_register_take_advances_past_orphan_media(tmp_path):
    from manju.core.container import MEDIA_EXTS, Project
    from manju.core.models import TakeSidecar

    proj = Project.create(tmp_path / "demo", git_init=False)
    sid = "S001"
    src = proj.root / "in.mp4"
    src.write_bytes(b"video-bytes")

    proj.register_take(sid, src, TakeSidecar(provider="test", spec_hash="sha256:a"))
    proj.register_take(sid, src, TakeSidecar(provider="test", spec_hash="sha256:b"))

    # simulate a crash between the media write and the sidecar write: a media
    # file occupies the next slot with NO sidecar.
    ext = ".mp4" if ".mp4" in MEDIA_EXTS else MEDIA_EXTS[0]
    (proj.takes_dir(sid) / f"take_03{ext}").write_bytes(b"orphan")

    assert proj.next_take_name(sid) == "take_04"  # steps past the orphan slot
    info = proj.register_take(sid, src, TakeSidecar(provider="test", spec_hash="sha256:c"))
    assert info.name == "take_04"  # succeeds instead of raising ProjectError


# --------------------------------------------------------------------------- #
# (6) refs_report survives a symlink that escapes the project root              #
# --------------------------------------------------------------------------- #


def _symlink_ok(tmp_path) -> bool:
    try:
        t = tmp_path / "_lnkchk"
        (tmp_path / "_tgt").write_bytes(b"x")
        t.symlink_to(tmp_path / "_tgt")
        t.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def test_refs_report_survives_symlink_escaping_root(tmp_path):
    import os

    from manju.core.container import Project
    from manju.core.refs import refs_report

    if not _symlink_ok(tmp_path):
        pytest.skip("symlinks not creatable on this platform/privilege level")

    proj = Project.create(tmp_path / "demo", git_init=False)
    outside = tmp_path / "outside" / "secret.png"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"x")
    os.symlink(outside, proj.refs_dir / "sneaky.png")  # escapes the root

    report = refs_report(proj)  # must not raise ValueError
    assert report is not None
