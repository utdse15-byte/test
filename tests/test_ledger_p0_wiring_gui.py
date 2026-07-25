"""Ledger P0 regressions — GUI library reads + RunManifest writes (group:
wiring_gui).

Two escapes the external audit demonstrated, driven end to end and then pinned:

- LIBRARY-P0-002 (GUI face): every GUI route that serves or copies library
  bytes (``/lib-blob/<hash8>``, ``/api/lib/use``, the mixer's ``lib:<hash8>``
  source token) must read through ``Library.open_verified_blob`` — a forged
  index row aimed at an external secret, or a blob whose bytes no longer hash
  to the recorded index hash, must refuse through the route's own JSON error
  protocol (never a traceback, never a half response, never the bytes) and
  must not leak the verified descriptor.
- TASKS-P0-001 (service face): ``materialize_run_manifest`` must validate the
  run id as ONE safe path leaf and refuse a linked ``reports/runs/<id>`` chain
  BEFORE it writes — the CLI's own pre-check is not the security boundary.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import manju.build.attempts as A
from manju.core.library import Library, LibraryError, _hex, library_root
from manju.core.safeio import SafeOutError
from manju.gui.server import _Handler, create_server

# --------------------------------------------------------------- gui fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    """Point the user-level shelves at tmp dirs so no test touches ~/.manju."""
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "_library"))
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers"))
    monkeypatch.setenv("MANJU_ROUTING", str(tmp_path / "_user_routing.yaml"))


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), (
                payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _post(server, path, body):
    return _req(server, path, method="POST", body=body,
                headers={"X-Manju-Token": server.token})


def _seed(name="asset.wav", content=b"RIFF" + b"0" * 60 + b"WAVE"):
    """Add one honest asset to the isolated shelf; return (hash8, entry)."""
    src = Path(os.environ["MANJU_LIBRARY"]).parent / ("_src_" + name)
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(content)
    entry = Library().add(src, tags=["bgm"], note="seed")["entry"]
    return _hex(entry["hash"])[:8], entry


def _forge_index(assets):
    """Write index.json directly — the audit's threat model is a corrupted or
    hostile machine-local catalogue, so the safe writers are bypassed on
    purpose."""
    root = library_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(
        json.dumps({"version": 1, "assets": assets}, ensure_ascii=False),
        encoding="utf-8")


def _external_row(secret: Path, *, name="secret.wav"):
    return {"hash": "sha256:" + "c" * 64, "blob": str(secret), "name": name,
            "kind": "audio", "size": secret.stat().st_size, "tags": [],
            "note": "", "added": "t", "thumb": None}


SECRET = b"TOP-SECRET-OUTSIDE"


# =========================================== LIBRARY-P0-002 — /lib-blob serve


def test_lib_blob_route_serves_a_good_asset(gui):
    hash8, _ = _seed(content=b"good-library-bytes")
    status, headers, body = _req(gui, "/lib-blob/" + hash8, raw=True)
    assert status == 200
    assert body == b"good-library-bytes"
    assert headers.get("Accept-Ranges") == "bytes"


def test_lib_blob_route_honours_range_requests(gui):
    hash8, _ = _seed(content=b"0123456789")
    status, headers, body = _req(gui, "/lib-blob/" + hash8, raw=True,
                                 headers={"Range": "bytes=2-5"})
    assert status == 206
    assert body == b"2345"
    assert headers.get("Content-Range") == "bytes 2-5/10"


def test_lib_blob_route_refuses_a_forged_external_blob(gui, tmp_path):
    """A rewritten index row pointing at an external secret must not turn the
    GUI into an arbitrary-file reader."""
    secret = tmp_path / "outside-secret.wav"
    secret.write_bytes(SECRET)
    _forge_index([_external_row(secret)])

    status, _, body = _req(gui, "/lib-blob/" + "c" * 8, raw=True)
    assert status in (403, 404)
    assert SECRET not in body
    assert json.loads(body)["error"]  # the route's JSON protocol, not a traceback


def test_lib_blob_route_refuses_byte_substituted_blob(gui):
    """Path stays contained, bytes were swapped after the index was written —
    the verified open must refuse rather than serve the substitution."""
    hash8, entry = _seed(content=b"real-bytes")
    (library_root() / entry["blob"]).write_bytes(b"SUBSTITUTED-BYTES")

    status, _, body = _req(gui, "/lib-blob/" + hash8, raw=True)
    assert status in (403, 404)
    assert b"SUBSTITUTED-BYTES" not in body
    assert json.loads(body)["error"]


def test_lib_blob_route_closes_the_verified_handle(gui, monkeypatch):
    """The GUI is a long session: the verified descriptor must be closed
    deterministically on the serve path (no fd leak per request)."""
    hash8, _ = _seed(content=b"handle-bytes")
    opened: list = []
    real = Library.open_verified_blob

    def _spy(self, entry):
        fh = real(self, entry)
        opened.append(fh)
        return fh

    monkeypatch.setattr(Library, "open_verified_blob", _spy)
    for _ in range(3):
        status, _h, body = _req(gui, "/lib-blob/" + hash8, raw=True)
        assert status == 200 and body == b"handle-bytes"
    assert len(opened) == 3
    # the client returns as soon as Content-Length bytes arrive, so give the
    # handler thread its (deterministic) finally-block a moment to run.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not all(fh.closed for fh in opened):
        time.sleep(0.02)
    assert all(fh.closed for fh in opened)


# ============================================ LIBRARY-P0-002 — /api/lib/use


def test_lib_use_copies_a_good_asset(gui, tmp_project):
    hash8, _ = _seed(content=b"good-library-bytes")
    status, _, data = _post(gui, "/api/lib/use", {"hash": hash8, "as": "refs"})
    assert status == 200 and data["ok"] is True
    assert tmp_project.resolve(data["dest"]).read_bytes() == b"good-library-bytes"


def test_lib_use_refuses_a_forged_external_blob(gui, tmp_project):
    secret = tmp_project.root.parent / "outside-secret.wav"
    secret.write_bytes(SECRET)
    _forge_index([_external_row(secret)])

    status, _, data = _post(gui, "/api/lib/use",
                            {"hash": "c" * 8, "as": "refs"})
    assert status == 400 and data["error"]
    landed = list(tmp_project.refs_dir.glob("*")) if tmp_project.refs_dir.exists() else []
    assert all(p.read_bytes() != SECRET for p in landed if p.is_file())
    assert secret.read_bytes() == SECRET  # untouched


def test_lib_use_refuses_byte_substituted_blob(gui, tmp_project):
    hash8, entry = _seed(content=b"real-bytes")
    (library_root() / entry["blob"]).write_bytes(b"SUBSTITUTED-BYTES")

    status, _, data = _post(gui, "/api/lib/use", {"hash": hash8, "as": "refs"})
    assert status == 400 and data["error"]
    landed = list(tmp_project.refs_dir.glob("*")) if tmp_project.refs_dir.exists() else []
    assert all(p.read_bytes() != b"SUBSTITUTED-BYTES" for p in landed if p.is_file())


# ====================================== LIBRARY-P0-002 — mixer lib: source


def test_mixer_lib_source_refuses_a_forged_external_blob(gui, tmp_project):
    secret = tmp_project.root.parent / "outside-secret.wav"
    secret.write_bytes(SECRET)
    _forge_index([_external_row(secret)])

    status, _, data = _post(gui, "/api/mixer/apply",
                            {"changes": {"music": {"source": "lib:" + "c" * 8}}})
    assert status == 400 and data["error"]
    imports = tmp_project.imports_dir
    landed = list(imports.glob("*")) if imports.exists() else []
    assert all(p.read_bytes() != SECRET for p in landed if p.is_file())
    assert tmp_project.load_rules().music.source in (None, "")


def test_mixer_lib_source_refuses_byte_substituted_blob(gui, tmp_project):
    hash8, entry = _seed(content=b"real-bytes")
    (library_root() / entry["blob"]).write_bytes(b"SUBSTITUTED-BYTES")

    status, _, data = _post(gui, "/api/mixer/apply",
                            {"changes": {"music": {"source": "lib:" + hash8}}})
    assert status == 400 and data["error"]
    imports = tmp_project.imports_dir
    landed = list(imports.glob("*")) if imports.exists() else []
    assert all(p.read_bytes() != b"SUBSTITUTED-BYTES" for p in landed if p.is_file())


def test_mixer_lib_source_still_resolves_a_good_asset(gui, tmp_project):
    hash8, _ = _seed(content=b"good-library-bytes")
    status, _, data = _post(gui, "/api/mixer/apply",
                            {"changes": {"music": {"source": "lib:" + hash8}}})
    assert status == 200 and data["ok"]
    src = tmp_project.load_rules().music.source
    assert src and src.startswith("media/imports/")
    assert tmp_project.resolve(src).read_bytes() == b"good-library-bytes"


# ==================================================== TASKS-P0-001 — manifest


def _seed_run(root: Path, run_id: str) -> None:
    ev = A.RunEvidence(root, run_id)
    ev.attempt("render", {"kind": "render", "target": "final"}, "render").succeeded()
    ev.run_succeeded()


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "c:evil",
                                 "nul\x00id", "x" * 129])
def test_materialize_run_manifest_refuses_unsafe_run_ids(tmp_path, bad):
    """The run id is joined straight into ``reports/runs/<id>/run.json``, so
    every leaf-escaping shape must fail closed in the SERVICE layer."""
    with pytest.raises(SafeOutError):
        A.materialize_run_manifest(tmp_path, bad)


def test_materialize_run_manifest_refusal_is_a_structured_error(tmp_path):
    with pytest.raises(SafeOutError) as exc:
        A.materialize_run_manifest(tmp_path, "x" * 5000)
    assert getattr(exc.value, "reason", None)  # stable machine token, not ENAMETOOLONG


def test_materialize_run_manifest_refuses_absolute_run_id(tmp_path):
    """``pathlib`` DROPS the project root when the right operand is absolute —
    an absolute run id used to overwrite any external ``run.json``."""
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "run.json"
    victim.write_text("KEEP ME", encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()

    with pytest.raises(SafeOutError):
        A.materialize_run_manifest(project, str(outside))
    assert victim.read_text(encoding="utf-8") == "KEEP ME"


def test_materialize_run_manifest_refuses_traversal_run_id(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "run.json"
    victim.write_text("KEEP ME", encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()

    with pytest.raises(SafeOutError):
        A.materialize_run_manifest(project, "../../../outside")
    assert victim.read_text(encoding="utf-8") == "KEEP ME"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink support")
def test_materialize_run_manifest_refuses_a_symlinked_runs_chain(tmp_path):
    """A directory symlink at ``reports/runs/<id>`` redirects the atomic write
    outside the project — the leaf-only replace still lands wherever the linked
    PARENT resolves."""
    project = tmp_path / "proj"
    _seed_run(project, "run_link")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "run.json").write_text("KEEP ME", encoding="utf-8")
    runs = project / "reports" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, runs / "run_link", target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover — no privilege
        pytest.skip("symlink creation not permitted")

    with pytest.raises(SafeOutError):
        A.materialize_run_manifest(project, "run_link")
    assert (outside / "run.json").read_text(encoding="utf-8") == "KEEP ME"


def test_materialize_run_manifest_still_writes_a_normal_run(tmp_path):
    _seed_run(tmp_path, "run_ok")
    path = A.materialize_run_manifest(tmp_path, "run_ok")
    assert path == A.run_manifest_path(tmp_path, "run_ok")
    assert path.is_file()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["run_id"] == "run_ok" and doc["schema"] == A.MANIFEST_SCHEMA


def test_materialize_run_manifest_keeps_accepting_the_recorded_id_corpus(tmp_path):
    """The refusal is a TRAVERSAL guard, not a charset narrowing: run ids the
    stream already records (``run_$`` in tests/test_dr03c_attempts.py) stay
    materializable."""
    _seed_run(tmp_path, "run_$")
    assert A.materialize_run_manifest(tmp_path, "run_$").is_file()


def test_library_error_is_never_raised_out_of_the_gui_lib_routes(gui, tmp_path):
    """Every library refusal reaches the client as this server's JSON error
    envelope — a LibraryError escaping the handler would be a 500/torn body."""
    secret = tmp_path / "outside-secret.wav"
    secret.write_bytes(SECRET)
    _forge_index([_external_row(secret)])
    for path, body in (("/api/lib/use", {"hash": "c" * 8, "as": "refs"}),
                       ("/api/mixer/apply",
                        {"changes": {"music": {"source": "lib:" + "c" * 8}}})):
        status, _, data = _post(gui, path, body)
        assert status == 400, (path, status, data)
        assert isinstance(data, dict) and data.get("error")
    # …and the core owner really does refuse this row (the routes are wired to
    # that refusal, they do not re-implement it).
    with pytest.raises(LibraryError):
        Library().open_verified_blob(Library().get("c" * 8))


# ============================================ GUI-HTTP-P1-004 — drain restores

def _drain(sock, payload: bytes, declared: int, **kw):
    """Drive the real ``_drain_request_body`` over a REAL socket, unbound from
    a stub carrying only the three attributes it touches."""
    stub = types.SimpleNamespace(
        headers={"Content-Length": str(declared)},
        connection=sock,
        rfile=io.BytesIO(payload),
    )
    _Handler._drain_request_body(stub, **kw)
    return stub


@pytest.fixture
def blocking_sock():
    left, right = socket.socketpair()
    try:
        yield left
    finally:
        left.close()
        right.close()


def test_drain_restores_a_blocking_socket_to_blocking(blocking_sock):
    """The regression itself. A handler socket is in BLOCKING mode, so
    ``gettimeout()`` is ``None`` — the old ``if prev_timeout is not None``
    restore then never ran, and every drained refusal (host guard / readonly
    POST / stale token) welded a 2s deadline onto a keep-alive connection the
    browser goes on reusing for later reads AND for sending big responses."""
    assert blocking_sock.gettimeout() is None  # the ordinary case, not exotic
    _drain(blocking_sock, b"x" * 32, 32)
    assert blocking_sock.gettimeout() is None


def test_drain_restores_a_socket_that_already_had_a_timeout(blocking_sock):
    blocking_sock.settimeout(37.5)
    _drain(blocking_sock, b"x" * 32, 32)
    assert blocking_sock.gettimeout() == pytest.approx(37.5)


def test_drain_restores_even_when_the_body_is_short(blocking_sock):
    """A truncated body leaves the loop through ``not chunk``; the restore is
    in a ``finally`` and must still fire."""
    _drain(blocking_sock, b"x" * 4, 4096)
    assert blocking_sock.gettimeout() is None


def test_drain_restores_when_the_read_raises(blocking_sock):
    class _Boom(io.BytesIO):
        def read(self, _n):  # noqa: D401 - stub
            raise OSError("socket went away")

    stub = types.SimpleNamespace(
        headers={"Content-Length": "64"}, connection=blocking_sock, rfile=_Boom())
    _Handler._drain_request_body(stub)
    assert blocking_sock.gettimeout() is None


def test_drain_still_consumes_the_declared_bytes_and_no_more(blocking_sock):
    stub = _drain(blocking_sock, b"a" * 10 + b"NEXT-REQUEST", 10)
    assert stub.rfile.read() == b"NEXT-REQUEST"


def test_drain_is_bounded_by_max_bytes(blocking_sock):
    stub = _drain(blocking_sock, b"a" * 100, 100, max_bytes=10)
    assert len(stub.rfile.read()) == 90
