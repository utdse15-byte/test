"""PRIVATE ASSET LIBRARY (goal 8, round-S): core/library.py + `manju lib`.

The library is local, user-level and content-addressed. These tests pin its
disciplines: dedup by content hash (tags merge, bytes stored once), the source
is never consumed, kind classification, tag/kind filters, the MANJU_LIBRARY
override, `lib use` copying into a project (with no-overwrite suffixing and an
event) while the library stays independent, and the --yes gate on `lib rm`.

Every test points MANJU_LIBRARY at a tmp dir, so the real ~/.manju is untouched.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.events import tail_events
from manju.core.library import Library, LibraryError, kind_of, library_root

runner = CliRunner()


@pytest.fixture
def lib_root(tmp_path, monkeypatch):
    root = tmp_path / "user_lib"
    monkeypatch.setenv("MANJU_LIBRARY", str(root))
    return root


@pytest.fixture
def src_file(tmp_path):
    def _make(name: str, content: bytes = b"asset-bytes") -> "object":
        p = tmp_path / name
        p.write_bytes(content)
        return p
    return _make


# ------------------------------------------------------------------- basics


def test_env_override_and_kind_classification(lib_root, monkeypatch):
    assert library_root() == lib_root
    monkeypatch.delenv("MANJU_LIBRARY", raising=False)
    assert library_root().name == "library"  # ~/.manju/library
    assert kind_of(".MP4") == "video"
    assert kind_of(".png") == "image"
    assert kind_of(".wav") == "audio"
    assert kind_of(".txt") == "other"


def test_add_copies_in_and_never_consumes_source(lib_root, src_file):
    lib = Library()
    f = src_file("clip.mp4", b"the-real-footage")
    res = lib.add(f, tags=["broll", "night"], note="城市夜景")
    assert res["deduped"] is False
    entry = res["entry"]
    assert entry["kind"] == "video" and entry["name"] == "clip.mp4"
    assert entry["tags"] == ["broll", "night"] and entry["note"] == "城市夜景"
    # source survives; a content-addressed blob now exists in the library
    assert f.exists(), "add must never move/delete the source"
    blob = lib.blob_path(entry)
    assert blob.exists() and blob.read_bytes() == b"the-real-footage"
    assert blob.name.endswith(".mp4")


def test_dedup_merges_tags_and_stores_bytes_once(lib_root, src_file):
    lib = Library()
    a = src_file("a.mp4", b"identical")
    b = src_file("b.mp4", b"identical")  # same content, different name
    first = lib.add(a, tags=["one"])
    second = lib.add(b, tags=["two"])
    assert second["deduped"] is True
    assert first["entry"]["hash"] == second["entry"]["hash"]
    # tags merged, name of the first add preserved, exactly one asset + one blob
    assert second["entry"]["tags"] == ["one", "two"]
    assert len(lib.assets()) == 1
    blobs = [p for p in lib.root.iterdir() if p.suffix == ".mp4"]
    assert len(blobs) == 1


def test_list_filters_by_tag_and_kind(lib_root, src_file):
    lib = Library()
    lib.add(src_file("v.mp4", b"v"), tags=["keep"])
    lib.add(src_file("p.png", b"p"), tags=["keep"])
    lib.add(src_file("a.wav", b"a"), tags=["drop"])
    assert {e["name"] for e in lib.list_assets(tag="keep")} == {"v.mp4", "p.png"}
    assert {e["name"] for e in lib.list_assets(kind="audio")} == {"a.wav"}
    assert lib.list_assets(tag="keep", kind="image")[0]["name"] == "p.png"


def test_find_get_and_ambiguity(lib_root, src_file, monkeypatch):
    lib = Library()
    r = lib.add(src_file("x.mp4", b"unique-bytes"))
    from manju.core.library import _hex

    h8 = _hex(r["entry"]["hash"])[:8]
    assert lib.get(h8)["name"] == "x.mp4"
    with pytest.raises(LibraryError, match="no asset"):
        lib.get("ffffffff")
    # force an ambiguous prefix by hand-editing the index to two same-prefix hashes
    idx = lib.load_index()
    twin = dict(idx["assets"][0])
    twin["hash"] = idx["assets"][0]["hash"][:20] + "0" * 44  # shares the >8 prefix
    idx["assets"].append(twin)
    lib._save_index(idx)
    with pytest.raises(LibraryError, match="ambiguous"):
        lib.get(idx["assets"][0]["hash"].removeprefix("sha256:")[:8])


def test_remove_is_library_side_only(lib_root, src_file):
    lib = Library()
    r = lib.add(src_file("gone.mp4", b"remove-me"))
    from manju.core.library import _hex

    h8 = _hex(r["entry"]["hash"])[:8]
    blob = lib.blob_path(r["entry"])
    assert blob.exists()
    lib.remove(h8)
    assert not blob.exists()
    assert lib.assets() == []


# --------------------------------------------------------------------- CLI


def test_cli_add_dedup_and_list_json(lib_root, src_file):
    f = src_file("footage.mov", b"cli-bytes")
    first = runner.invoke(app, ["lib", "add", str(f), "--tag", "a,b", "--json"])
    assert first.exit_code == 0, first.output
    added = json.loads(first.output)["added"][0]
    assert added["deduped"] is False and len(added["hash8"]) == 8

    dup = runner.invoke(app, ["lib", "add", str(f), "--tag", "c", "--json"])
    assert json.loads(dup.output)["added"][0]["deduped"] is True

    listing = runner.invoke(app, ["lib", "list", "--json"])
    assets = json.loads(listing.output)["assets"]
    assert len(assets) == 1 and set(assets[0]["tags"]) == {"a", "b", "c"}
    assert assets[0]["hash8"] == added["hash8"]


def test_cli_use_copies_into_project_with_no_overwrite(lib_root, src_file, tmp_project, monkeypatch):
    f = src_file("logo.png", b"brand-logo")
    add = runner.invoke(app, ["lib", "add", str(f), "--json"])
    h8 = json.loads(add.output)["added"][0]["hash8"]

    monkeypatch.chdir(tmp_project.root)
    first = runner.invoke(app, ["lib", "use", h8, "--json"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["dest"] == "media/refs/logo.png"
    # a second use suffixes, honoring the project's no-overwrite rule (§3)
    second = runner.invoke(app, ["lib", "use", h8, "--json"])
    assert json.loads(second.output)["dest"] == "media/refs/logo_2.png"
    # --as imports routes to media/imports, --name overrides the filename
    imp = runner.invoke(app, ["lib", "use", h8, "--as", "imports", "--name", "sting.png", "--json"])
    assert json.loads(imp.output)["dest"] == "media/imports/sting.png"

    assert (tmp_project.refs_dir / "logo.png").exists()
    assert (tmp_project.imports_dir / "sting.png").exists()
    # the library blob is independent — still present after using it into a project
    assert len(Library().assets()) == 1
    # every use is an import-style event
    actions = [e["action"] for e in tail_events(tmp_project.root, 50)]
    assert actions.count("lib_use") == 3


def test_cli_rm_requires_yes(lib_root, src_file):
    f = src_file("temp.mp4", b"delete-me")
    add = runner.invoke(app, ["lib", "add", str(f), "--json"])
    h8 = json.loads(add.output)["added"][0]["hash8"]

    refused = runner.invoke(app, ["lib", "rm", h8])
    assert refused.exit_code == 1
    assert "without --yes" in (refused.stdout + refused.stderr)
    assert len(Library().assets()) == 1

    ok = runner.invoke(app, ["lib", "rm", h8, "--yes", "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["name"] == "temp.mp4"
    assert Library().assets() == []


# --------------------------------------------------------- #42: cross-process lock


def _mp_add_worker(root: str, src: str, tag: str) -> None:
    """Top-level (picklable) worker for the multiprocessing race test below:
    each OS process adds ONE distinct-content file to the SAME library root,
    all timed to fire together (round W, #42)."""
    import manju.core.library as library_mod

    library_mod.Library(root).add(src, tags=[tag])


def test_add_concurrent_processes_no_lost_update(lib_root, tmp_path):
    """Real OS processes (not threads) racing `Library.add` on the SAME
    index.json must not lose an entry to a last-writer-wins read-modify-write
    (round W, #42 — library index lost update). Six distinct-content files
    (so every add is a genuinely NEW row, never a dedup no-op) are added by
    six separate processes launched together; without the fcntl.flock guard
    around load→mutate→save, at least one process's snapshot of the index
    predates another's write and its row is silently dropped on save.
    With the lock, every add is serialized and all six rows must survive."""
    import multiprocessing

    n = 6
    files = []
    for i in range(n):
        p = tmp_path / f"clip_{i}.bin"
        p.write_bytes(f"library-race-content-{i}-".encode() * 200)
        files.append(p)

    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_mp_add_worker, args=(str(lib_root), str(f), f"tag{i}"))
        for i, f in enumerate(files)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, f"worker process failed (exitcode={p.exitcode})"

    from manju.core.hashing import hash_file

    lib = Library(lib_root)
    assets = lib.assets()
    assert len(assets) == n, (
        f"expected {n} distinct library rows, got {len(assets)} — "
        "a lost update dropped a concurrent add()"
    )
    expected_hashes = {hash_file(f) for f in files}
    assert {e["hash"] for e in assets} == expected_hashes


def test_index_lock_serializes_two_threads(lib_root, tmp_path, monkeypatch):
    """A lower-level, same-process check of the lock primitive itself: force
    two threads to interleave load→save around `Library.add` (a delay
    injected into `_save_index`) and confirm both rows still land — the
    thread-local proof that `_index_lock` actually blocks a second acquirer
    rather than being a no-op (round W, #42)."""
    import threading
    import time as _time

    import manju.core.library as library_mod

    lib = Library(lib_root)
    orig_save = lib._save_index
    barrier_hits = []

    def slow_save(index):
        barrier_hits.append(1)
        _time.sleep(0.05)  # widen the race window a lock must close
        orig_save(index)

    monkeypatch.setattr(lib, "_save_index", slow_save)

    f1 = tmp_path / "a.bin"
    f1.write_bytes(b"thread-race-a")
    f2 = tmp_path / "b.bin"
    f2.write_bytes(b"thread-race-b")

    results: dict[str, Exception | None] = {}

    def run(name, f, tag):
        try:
            lib.add(f, tags=[tag])
            results[name] = None
        except Exception as exc:  # pragma: no cover - surfaced via assert below
            results[name] = exc

    t1 = threading.Thread(target=run, args=("t1", f1, "x"))
    t2 = threading.Thread(target=run, args=("t2", f2, "y"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert results == {"t1": None, "t2": None}
    assert len(barrier_hits) == 2
    assert len(Library(lib_root).assets()) == 2
