"""UX-WAVE-3 — the daily-loop wave (2026-07-20).

Two owner-facing gaps from the personal-UX review, both closing loops that
already had server-side halves:

1. 草稿保护 — ``/api/ui-state/draft`` existed with ZERO client consumers, so
   a crash or mis-click discarded any typing in the shot/bible/rules editor.
   The SPA editor now mirrors the buffer into the personal store (debounced),
   offers an explicit restore on reopen, and clears the draft on a successful
   save. Truth files are NEVER written by the draft path (pinned below).

2. 全局任务条 — jobs belong to the app window, but each server-rendered page
   only polled the job it had itself submitted; navigating away made a running
   build invisible. ``/common.js`` now carries a read-only bar over the
   existing runner (poll ``/api/jobs``, cancel/retry via the existing
   endpoints, follow-up links per kind). The SPA home keeps its own queue
   panel and must NOT load common.js (no double bar).

The HTTP tests assert server behavior; the artifact tests assert on the
SERVED files (``/common.js`` / ``/app.js`` / page shells) because the served
bytes are exactly what the browser executes — the file content IS the
artifact under test (tests/CONVENTIONS.md exception), and there is no
browser in this suite.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from manju.gui.server import create_server


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


def _get_text(server, path):
    url = f"http://127.0.0.1:{server.port}{path}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8")


# ------------------------------------------------------- 1. 草稿保护 (drafts)


def test_draft_roundtrip_via_query_then_clear(tmp_project, monkeypatch, tmp_path,
                                              add_shot):
    """The exact protocol the editor client speaks: save → GET ?draft= →
    clear → GET again returns nothing. Truth file untouched throughout."""
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "gs.json"))
    add_shot(tmp_project, "S001")
    truth_before = (tmp_project.root / "shots" / "S001.yaml").read_text(
        encoding="utf-8")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, data = _post(server, "/api/ui-state/draft", {
            "relative_file_path": "shots/S001.yaml",
            "expected_rev": "rev-1",
            "draft_text": "dialogue: half-typed line",
        })
        assert st == 200 and data["ok"]

        st, got = _req(server, "/api/ui-state?draft=shots%2FS001.yaml")
        assert st == 200
        assert got["draft"] is not None
        assert got["draft"]["draft_text"] == "dialogue: half-typed line"
        assert got["draft"]["expected_rev"] == "rev-1"
        assert got["draft"]["updated_at"]  # restore banner shows the timestamp

        # the draft mirror never touches project truth
        truth_after = (tmp_project.root / "shots" / "S001.yaml").read_text(
            encoding="utf-8")
        assert truth_after == truth_before

        st, data = _post(server, "/api/ui-state/draft", {
            "relative_file_path": "shots/S001.yaml",
            "action": "clear",
        })
        assert st == 200 and data["cleared"]

        st, got = _req(server, "/api/ui-state?draft=shots%2FS001.yaml")
        assert st == 200 and got["draft"] is None
    finally:
        server.shutdown()
        server.close()


def test_spa_editor_consumes_the_draft_api(tmp_project):
    """The store existed with no consumer — pin that the served app.js now
    speaks the draft protocol end-to-end: mirror writes, restore read, and
    the explicit clear (both save-success and discard use action=clear)."""
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, js = _get_text(server, "/app.js")
        assert st == 200
        assert "/api/ui-state/draft" in js
        assert "/api/ui-state?draft=" in js
        assert '"clear"' in js or "'clear'" in js
        # restore is an explicit OFFER, not an auto-apply: the served editor
        # carries the banner class + both the restore and discard affordances
        assert "ed-draft" in js
        assert "恢复草稿" in js and "删除草稿" in js
    finally:
        server.shutdown()
        server.close()


# --------------------------------------------------- 2. 全局任务条 (task bar)


def test_common_js_carries_the_taskbar_over_existing_endpoints(tmp_project):
    """The served /common.js polls /api/jobs and drives cancel/retry through
    the EXISTING job endpoints with their real body field (job_id) — a
    read-only presentation layer, no new task machinery."""
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, js = _get_text(server, "/common.js")
        assert st == 200
        assert "mj-taskbar" in js
        assert "/api/jobs" in js
        assert "/api/jobs/cancel" in js
        assert "/api/jobs/retry" in js
        assert "job_id" in js  # the endpoints' actual request field
        # 待确认花费 must never render as a plain完成 (paid-safety honesty)
        assert "waiting_user" in js and "待确认花费" in js
    finally:
        server.shutdown()
        server.close()


def test_taskbar_kind_labels_come_from_the_registry_not_a_copy(tmp_project):
    """core.jobkinds is the ONE kind→label owner. The bar must fetch
    /api/meta/job-kinds; a hard-coded copy of the display names would trip
    this the moment it appeared. (Short labels like 导出/质检 legitimately
    occur inside ACTION texts such as 打开导出中心, so the pin checks the
    distinctive labels a copied map could not omit.)"""
    from manju.core.jobkinds import display_labels

    distinctive = {"批量重做", "批量配音", "补拍手柄", "外部回写",
                   "同步设定", "剪辑预览"}
    assert distinctive <= set(display_labels().values())  # still real labels

    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, js = _get_text(server, "/common.js")
        assert st == 200
        assert "/api/meta/job-kinds" in js
        for zh in sorted(distinctive):
            assert zh not in js, f"kind label {zh!r} hard-coded in common.js"
    finally:
        server.shutdown()
        server.close()


def test_spa_home_does_not_load_common_js_but_server_pages_do(tmp_project,
                                                              add_shot):
    """No double bar: the SPA home has its own queue panel (+ hidden-tab
    notifications) and must not load common.js; every server-rendered page
    does load it, which is what puts the bar there."""
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        st, home = _get_text(server, "/")
        assert st == 200
        assert "/common.js" not in home

        st, review = _get_text(server, "/review")
        assert st == 200
        assert "/common.js" in review
    finally:
        server.shutdown()
        server.close()
