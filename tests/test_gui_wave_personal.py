"""GUI-WAVE-PERSONAL-01 — scale, ui_rev, personal state, static scans."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.gui.server import create_server
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


# ---------------------------------------------------- ui_rev + perf


def test_shot_cards_include_stable_ui_rev(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h1")
    from manju.gui.jobs import JobRunner

    r = JobRunner(tmp_project.runtime_dir, project_id=project_identity(tmp_project))
    try:
        state = build_state(tmp_project, r, include_timing=True)
        assert state["shots"]
        s0 = state["shots"][0]
        assert "ui_rev" in s0 and len(s0["ui_rev"]) >= 8
        # stable across rebuilds with no change
        state2 = build_state(tmp_project, r, include_timing=True)
        assert state2["shots"][0]["ui_rev"] == s0["ui_rev"]
        assert "perf" in state
        assert state["perf"]["shot_count"] >= 1
    finally:
        r.shutdown(timeout=2.0)


def test_ui_rev_changes_when_note_changes(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h1")
    from manju.gui.jobs import JobRunner

    r = JobRunner(tmp_project.runtime_dir, project_id=project_identity(tmp_project))
    try:
        before = build_state(tmp_project, r)["shots"][0]["ui_rev"]

        def mutate(d):
            st = d.setdefault("status", {})
            notes = st.setdefault("take_notes", {})
            notes[take.name] = "00:01 changed"

        tmp_project.update_shot_raw("S001", mutate)
        after = build_state(tmp_project, r)["shots"][0]["ui_rev"]
        assert after != before
    finally:
        r.shutdown(timeout=2.0)


def test_state_perf_query_adds_payload_bytes(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, data = _req(server, "/api/state?perf=1")
        assert st == 200
        assert "perf" in data
        assert data["perf"]["state_payload_bytes"] > 0
        assert data["perf"]["shot_count"] >= 1
    finally:
        server.shutdown()
        server.close()


# ---------------------------------------------------- personal UI state


def test_ui_state_isolated_by_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "gui_state.json"))
    from manju.gui import userstate as us

    a = us.patch_workspace_ui("projA", {"last_shot_id": "S001", "volume": 0.5})
    b = us.patch_workspace_ui("projB", {"last_shot_id": "S099", "volume": 0.2})
    assert us.get_workspace_ui("projA")["last_shot_id"] == "S001"
    assert us.get_workspace_ui("projB")["last_shot_id"] == "S099"
    assert a["volume"] == 0.5
    assert b["volume"] == 0.2


def test_ui_state_http_roundtrip(tmp_project, monkeypatch, tmp_path):
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "gs.json"))
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, data = _post(server, "/api/ui-state", {
            "ui": {"last_shot_id": "S010", "shot_status_filter": "needs_selection"},
        })
        assert st == 200 and data["ok"] is True
        assert data["ui"]["last_shot_id"] == "S010"
        st, got = _req(server, "/api/ui-state")
        assert st == 200
        assert got["ui"]["last_shot_id"] == "S010"
        assert got["workspace_id"] == server.session.project_id
    finally:
        server.shutdown()
        server.close()


def test_editor_draft_not_auto_write(tmp_project, monkeypatch, tmp_path, add_shot):
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "gs2.json"))
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, data = _post(server, "/api/ui-state/draft", {
            "relative_file_path": "shots/S001.yaml",
            "expected_rev": "abc",
            "draft_text": "draft body",
        })
        assert st == 200 and data["ok"]
        # truth file unchanged
        text = (tmp_project.root / "shots" / "S001.yaml").read_text(encoding="utf-8")
        assert "draft body" not in text
    finally:
        server.shutdown()
        server.close()


# ---------------------------------------------------- already_open


def test_open_same_project_returns_already_open(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, data = _post(server, "/api/workspace/open",
                         {"path": str(tmp_project.root)})
        assert st == 200
        assert data["code"] == "already_open"
        assert data["next_action"]["kind"] == "already_open"
        assert server.session.project.root == tmp_project.root
    finally:
        server.shutdown()
        server.close()


# ---------------------------------------------------- scale fixture + soak


def test_make_gui_scale_project_small(tmp_path):
    from tests.fixtures.make_gui_scale_project import build

    root = build(tmp_path / "scale", shots=5, takes_per_shot=2, name="tiny")
    p = Project(root)
    assert len(p.shot_ids()) == 5
    assert len(p.takes("S001")) == 2


def test_scale_state_timing_10(tmp_path):
    from tests.fixtures.make_gui_scale_project import build
    from manju.gui.jobs import JobRunner

    root = build(tmp_path / "s10", shots=10, takes_per_shot=3, name="s10")
    p = Project(root)
    r = JobRunner(p.runtime_dir, project_id=project_identity(p))
    try:
        t0 = time.perf_counter()
        state = build_state(p, r, include_timing=True)
        ms = (time.perf_counter() - t0) * 1000
        assert state["perf"]["shot_count"] == 10
        assert state["perf"]["take_count"] == 30
        # Soft budget: 10-shot fixture should build state under 15s on CI.
        assert ms < 15000
    finally:
        r.shutdown(timeout=2.0)


def test_gui_soak_smoke(tmp_path):
    from tests.fixtures.make_gui_scale_project import build
    from scripts.gui_soak import main as soak_main

    root = build(tmp_path / "soak", shots=4, takes_per_shot=1, name="soak")
    rc = soak_main([
        "--project", str(root),
        "--polls", "5",
        "--actions", "5",
        "--restarts", "1",
        "--seed", "1",
    ])
    assert rc == 0


# ---------------------------------------------------- static source pins


def test_app_js_has_keyed_patch_and_no_full_clear_for_shots():
    from manju.gui.page import render_js

    js = render_js()
    assert "ui_rev" in js or "uiRev" in js or "dataset.uiRev" in js
    assert "shots_reused" in js
    assert "IntersectionObserver" in js
    # Must not still clear entire shots root then rebuild unconditionally only
    # (keyed path must exist).
    assert "function renderShots" in js
    assert "shots_created" in js


def test_no_window_alert_in_project_flows():
    from manju.gui.glossary import render_glossary_js
    from manju.gui.workspace import render_workspace_js

    for src in (render_glossary_js(), render_workspace_js()):
        assert "window.alert" not in src


def test_webclient_is_single_json_owner_for_common_paths():
    """High-risk modules must not invent bare Error from body without data."""
    from manju.gui.webclient import render_webclient_js

    js = render_webclient_js()
    assert "class ManjuApiError" in js
    assert "requestJson" in js


def test_frontend_source_scan_fetch_allowlist():
    """JSON API pages should prefer requestJson; allowlist remaining raw fetch."""
    gui = Path("src/manju/gui")
    # Modules still using raw fetch intentionally (upload/stream/poll) —
    # keep the list explicit so new call sites are reviewed.
    allow = {
        "webclient.py",  # the owner
        "common_js.py",  # fallback + project-id poll
        "page.py",       # apiRaw fallback, watch long-poll, media
        "glossary.py",   # fallback + recents GET
        "workspace.py",  # fallback
        "ingest_page.py", "lab_page.py", "create_page.py",
        "exports_page.py", "series_page.py", "pages_t.py",
        "pages.py", "edit.py", "storyboard.py", "director_page.py",
        "command_palette.py",  # requestJson-first; standalone renderer fallback
        "help_center.py",      # requestJson-first; standalone renderer fallback
    }
    offenders = []
    for p in gui.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "fetch(" not in text and "fetch (" not in text:
            continue
        if p.name not in allow:
            offenders.append(p.name)
    assert offenders == [], f"new raw fetch sites need review: {offenders}"
