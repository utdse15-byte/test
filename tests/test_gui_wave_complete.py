"""Hard completion coverage for GUI-WAVE-PERSONAL-01 remaining criteria."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.gui.jobs import JobRunner, RunnerClosed, RunnerState
from manju.gui.server import create_server
from manju.gui.shutdown import AppShutdownCoordinator
from manju.gui.state import build_state, project_identity


def _serve(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def _req(server, path, *, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _post(server, path, body):
    return _req(server, path, method="POST", body=body,
                headers={"X-Manju-Token": server.token})


def test_app_shutdown_coordinator_class_exists():
    assert AppShutdownCoordinator is not None
    src = Path("src/manju/gui/shutdown.py").read_text(encoding="utf-8")
    assert "class AppShutdownCoordinator" in src
    assert "quit_already_in_progress" in src or "idempotent" in src.lower()


def test_a_b_isolation_ports_tokens_jobs(tmp_path):
    a = Project.create(tmp_path / "A", name="A", git_init=False)
    b = Project.create(tmp_path / "B", name="B", git_init=False)
    sa = create_server(a, host="127.0.0.1", port=0)
    sb = create_server(b, host="127.0.0.1", port=0)
    _serve(sa)
    _serve(sb)
    try:
        assert sa.port != sb.port
        assert sa.session.project_id != sb.session.project_id
        assert sa.runner is not sb.runner
        assert sa.runner._jobs_log_path != sb.runner._jobs_log_path
        release = threading.Event()
        started = threading.Event()

        def fn(job):
            started.set()
            release.wait(5)
            return {"ok": True}

        ja = sa.runner.submit("build", {}, fn)
        assert started.wait(3)
        _, jobs_a = _req(sa, "/api/jobs")
        _, jobs_b = _req(sb, "/api/jobs")
        ids_a = {j["id"] for j in jobs_a["jobs"]}
        ids_b = {j["id"] for j in jobs_b["jobs"]}
        assert ja.id in ids_a
        assert ja.id not in ids_b
        release.set()
    finally:
        sa.shutdown(); sa.close()
        sb.shutdown(); sb.close()


def test_closing_rejects_write_keeps_status_readable(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        release = threading.Event()

        def hold(job):
            release.wait(8)
            return {"ok": True}

        server.runner.submit("hold", {}, hold)
        time.sleep(0.05)
        st, _ = _post(server, "/api/app/quit", {"mode": "after_current"})
        assert st == 200
        st, body = _post(server, "/api/lock",
                         {"shot": "S001", "field": "dialogue.text"})
        assert st == 503 and body.get("code") == "server_closing"
        st, status = _req(server, "/api/app/status")
        assert st == 200
        assert status["closing"] is True
        st, jobs = _req(server, "/api/jobs")
        assert st == 200
        release.set()
    finally:
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass


def test_ui_rev_only_one_card_changes(tmp_path):
    from tests.fixtures.make_gui_scale_project import build

    root = build(tmp_path / "rev", shots=12, takes_per_shot=2, name="rev")
    p = Project(root)
    r = JobRunner(p.runtime_dir, project_id=project_identity(p))
    try:
        s1 = build_state(p, r)
        revs1 = {s["id"]: s["ui_rev"] for s in s1["shots"]}

        def mut(d):
            st = d.setdefault("status", {})
            notes = st.setdefault("take_notes", {})
            notes["take_01"] = "only S006"

        # find actual take name
        takes = p.takes("S006")
        tname = takes[0].name if takes else "take_01"

        def mut2(d):
            st = d.setdefault("status", {})
            notes = st.setdefault("take_notes", {})
            notes[tname] = "only S006 changed"

        p.update_shot_raw("S006", mut2)
        s2 = build_state(p, r)
        revs2 = {s["id"]: s["ui_rev"] for s in s2["shots"]}
        changed = [sid for sid in revs1 if revs1[sid] != revs2.get(sid)]
        assert changed == ["S006"], changed
    finally:
        r.shutdown(timeout=2)


def test_scale_200_state_builds(tmp_path):
    from tests.fixtures.make_gui_scale_project import build

    root = build(tmp_path / "s200", shots=200, takes_per_shot=5, name="s200")
    p = Project(root)
    r = JobRunner(p.runtime_dir, project_id=project_identity(p))
    try:
        t0 = time.perf_counter()
        st = build_state(p, r, include_timing=True)
        ms = (time.perf_counter() - t0) * 1000
        assert st["perf"]["shot_count"] == 200
        assert st["perf"]["take_count"] == 1000
        assert all("ui_rev" in s for s in st["shots"])
        # Soft budget for CI machine: under 2 minutes for pure offline PNG takes
        assert ms < 120_000, ms
    finally:
        r.shutdown(timeout=2)


def test_soak_larger(tmp_path):
    from tests.fixtures.make_gui_scale_project import build
    from scripts.gui_soak import main as soak_main

    root = build(tmp_path / "soak2", shots=20, takes_per_shot=2, name="soak2")
    rc = soak_main([
        "--project", str(root),
        "--polls", "100",
        "--actions", "50",
        "--restarts", "5",
        "--seed", "42",
    ])
    assert rc == 0


def test_app_js_review_focus_and_a11y_pins():
    from manju.gui.page import render_js, render_css

    js = render_js()
    css = render_css()
    assert "reviewFocusMode" in js or "review-focus" in js
    assert "aria-label" in js
    assert "prefers-reduced-motion" in css
    assert "AppShutdownCoordinator" in Path("src/manju/gui/shutdown.py").read_text(
        encoding="utf-8")


def test_json_get_jobs_pages_prefer_requestjson():
    for rel in (
        "src/manju/gui/exports_page.py",
        "src/manju/gui/series_page.py",
        "src/manju/gui/lab_page.py",
        "src/manju/gui/ingest_page.py",
        "src/manju/gui/edit.py",
    ):
        text = Path(rel).read_text(encoding="utf-8")
        # pollJob path must call requestJson for /api/jobs
        assert 'requestJson("GET", "/api/jobs"' in text or \
               'requestJson("GET","/api/jobs"' in text, rel


def test_imports_hash_stable_across_gui_ops(tmp_project, add_shot, make_take, tmp_path):
    """media/imports must not change during GUI select/ui-state ops."""
    import hashlib

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    imp = tmp_project.imports_dir
    imp.mkdir(parents=True, exist_ok=True)
    sample = imp / "canary.bin"
    sample.write_bytes(b"import-canary-bytes")

    def tree_hash():
        h = hashlib.sha256()
        for p in sorted(imp.rglob("*")):
            if p.is_file():
                h.update(p.name.encode())
                h.update(p.read_bytes())
        return h.hexdigest()

    before = tree_hash()
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        _post(server, "/api/select", {"shot": "S001", "take": take.name})
        _post(server, "/api/ui-state", {"ui": {"last_shot_id": "S001"}})
        _req(server, "/api/state")
    finally:
        server.shutdown()
        server.close()
    assert tree_hash() == before
