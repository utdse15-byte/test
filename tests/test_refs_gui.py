"""GUI surfacing of core/refs.py (round AA goal item 3, GUI half).

The engine layer (core/refs.py: refs_report/assign_ref) is frozen — this
suite only pins the two new HTTP entrances the /library page's new refs
section and its 关联 (assign) picker call:

    GET  /api/refs         refs_report(project) verbatim, read-only, no lock
    POST /api/refs/assign  wraps assign_ref in _optional_build_lock — assign_ref
                            itself does NOT take the cross-process build lock
                            (its own module docstring's lock contract: the
                            caller holds it), mirroring every other light
                            writer covered by tests/test_write_locks.py.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from manju.core.refs import refs_report
from manju.gui.server import create_server
from manju.runtime.buildlock import BuildLock


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = {}
        return exc.code, parsed


def _post(server, path, body):
    return _req(server, path, method="POST", body=body,
                headers={"X-Manju-Token": server.token})


def _touch(project, rel, data=b"img"):
    p = project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# =================================================================== GET


def test_api_refs_roundtrips_report(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/S001_ref.png")
    _touch(tmp_project, "media/refs/nobody_wants_me.png")

    status, data = _req(gui, "/api/refs")
    assert status == 200
    assert data == refs_report(tmp_project)
    assert data["total"] == 2
    assert data["orphan_count"] == 1
    by_file = {r["file"]: r for r in data["files"]}
    assert by_file["media/refs/S001_ref.png"]["role"] == "shot_ref"
    assert by_file["media/refs/nobody_wants_me.png"]["orphan"] is True


def test_api_refs_empty_project_roundtrips(gui, tmp_project):
    status, data = _req(gui, "/api/refs")
    assert status == 200
    assert data == refs_report(tmp_project)
    assert data["files"] == [] and data["total"] == 0 and data["orphan_count"] == 0


# ================================================================= assign


def test_api_refs_assign_happy_path_renames_and_report_reflects_it(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/loose.png")

    status, data = _post(gui, "/api/refs/assign",
                         {"relpath": "media/refs/loose.png", "shot": "S001"})
    assert status == 200
    assert data["ok"] is True
    assert data["old"] == "media/refs/loose.png"
    assert data["new"] == "media/refs/S001_ref.png"
    assert (tmp_project.root / "media/refs/S001_ref.png").exists()
    assert not (tmp_project.root / "media/refs/loose.png").exists()

    report = refs_report(tmp_project)
    row = next(r for r in report["files"] if r["file"] == "media/refs/S001_ref.png")
    assert row["owners"] == ["S001"]
    assert row["orphan"] is False
    assert report["orphan_count"] == 0


def test_api_refs_assign_to_bible_asset_sets_ref_image(gui, tmp_project):
    _touch(tmp_project, "media/refs/loose2.png")

    status, data = _post(gui, "/api/refs/assign",
                         {"relpath": "media/refs/loose2.png", "character": "linxia"})
    assert status == 200 and data["ok"] is True
    assert data["new"] == "media/refs/linxia_ref.png"

    from manju.core.yamlio import read_yaml

    chars = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    assert chars["linxia"]["ref_image"] == "media/refs/linxia_ref.png"


def test_api_refs_assign_bad_request_is_a_clean_400(gui, tmp_project):
    _touch(tmp_project, "media/refs/loose3.png")
    # neither shot/character/scene/prop given
    status, data = _post(gui, "/api/refs/assign", {"relpath": "media/refs/loose3.png"})
    assert status == 400
    assert "error" in data


def test_api_refs_assign_busy_under_build_lock_no_mutation(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _touch(tmp_project, "media/refs/loose4.png")

    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        status, data = _post(gui, "/api/refs/assign",
                             {"relpath": "media/refs/loose4.png", "shot": "S001"})
    finally:
        lock.release()
    assert status == 409
    assert "error" in data
    assert (tmp_project.root / "media/refs/loose4.png").exists()  # untouched

    # released -> the SAME request now succeeds normally
    status, data = _post(gui, "/api/refs/assign",
                         {"relpath": "media/refs/loose4.png", "shot": "S001"})
    assert status == 200 and data["ok"] is True
    assert (tmp_project.root / "media/refs/S001_ref.png").exists()
