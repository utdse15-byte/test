"""Round X agent XE — workspace: recents, the workspace picker, the in-chrome
project switcher (user pain #6 workspace/multi-project rough edges + the
recents half of pain #8: `manju gui` used to hard-fail outside a project).

Three layers, tested at the level that actually exercises them:

  * `manju.core.recents` — the ``~/.manju/recents.json`` store: touch/dedup/
    cap/missing-path-drop + cross-process-safe concurrent touches. Unit-level,
    no HTTP.
  * the CLI's ``_project()`` recents touch — the "once per invocation" guard.
  * the GUI: the picker page (served when NO project is bound), its open-by-
    path/new-project actions (both REBIND the running server — see
    ``GuiServer.bind_project``), and the in-chrome switcher that appears once
    a project IS bound. Real HTTP against an in-process server, following
    ``tests/test_gui_core.py``/``tests/test_gui_modes.py``'s own conventions.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from typer.testing import CliRunner

import manju.cli as cli_mod
from manju.cli import app
from manju.core.container import Project
from manju.core.recents import MAX_ENTRIES, load_recents, recents_path, touch_recent
from manju.core.series import Series, new_episode
from manju.gui.server import create_server

runner = CliRunner()

# `_isolate_gui_state` + `_isolate_recents` (tests/conftest.py, autouse) already
# redirect MANJU_GUI_STATE / MANJU_RECENTS to hermetic tmp paths for every test.


# =========================================================================
# core/recents.py — unit level
# =========================================================================


def _mk_project(tmp_path: Path, name: str) -> Project:
    return Project.create(tmp_path / name, git_init=False)


def test_touch_recent_creates_entry(tmp_path):
    p = _mk_project(tmp_path, "alpha")
    touch_recent(p)
    result = load_recents()
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry["path"] == str(p.root)
    assert entry["name"] == p.load_config().name
    assert "last_opened" in entry


def test_touch_recent_dedups_by_resolved_path(tmp_path):
    p = _mk_project(tmp_path, "alpha")
    touch_recent(p)
    assert load_recents().entries[0]["last_opened"]
    time.sleep(0.01)
    touch_recent(p)  # opening the SAME project again must not duplicate it
    result = load_recents()
    assert len(result.entries) == 1
    assert result.entries[0]["path"] == str(p.root)


def test_touch_recent_orders_most_recent_first(tmp_path):
    a = _mk_project(tmp_path, "alpha")
    b = _mk_project(tmp_path, "beta")
    touch_recent(a)
    touch_recent(b)
    entries = load_recents().entries
    assert [e["path"] for e in entries] == [str(b.root), str(a.root)]
    touch_recent(a)  # re-opening a moves it back to the front
    entries = load_recents().entries
    assert [e["path"] for e in entries] == [str(a.root), str(b.root)]


def test_recents_cap_evicts_oldest(tmp_path):
    projects = [_mk_project(tmp_path, f"p{i:02d}") for i in range(MAX_ENTRIES + 5)]
    for p in projects:
        touch_recent(p)
    entries = load_recents().entries
    assert len(entries) == MAX_ENTRIES
    kept_paths = {e["path"] for e in entries}
    # the 5 OLDEST touches (touched first, never re-touched) are the ones evicted
    for p in projects[:5]:
        assert str(p.root) not in kept_paths
    for p in projects[5:]:
        assert str(p.root) in kept_paths


def test_missing_path_dropped_on_read_and_reported_once(tmp_path):
    import shutil

    p = _mk_project(tmp_path, "gone")
    touch_recent(p)
    assert len(load_recents().entries) == 1

    shutil.rmtree(p.root)

    result = load_recents()
    assert result.entries == []
    assert result.dropped == [str(p.root)]

    # the store already reflects the removal — a second read never re-reports it
    result2 = load_recents()
    assert result2.entries == []
    assert result2.dropped == []


def test_concurrent_touch_is_safe(tmp_path):
    """Cross-process-safe (fcntl.flock, mirrors core/library.py): many threads
    touching DIFFERENT projects concurrently must never corrupt the store —
    the file stays valid JSON and every project ends up recorded exactly once."""
    projects = [_mk_project(tmp_path, f"c{i:02d}") for i in range(12)]

    def worker(p: Project) -> None:
        # touch twice per project to also exercise the dedup path under contention
        touch_recent(p)
        touch_recent(p)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, projects))

    # the file must be valid JSON — a torn concurrent write would fail this
    result = load_recents()
    paths = [e["path"] for e in result.entries]
    assert len(paths) == len(set(paths)) == len(projects)
    assert set(paths) == {str(p.root) for p in projects}


def test_recents_path_honours_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom_recents.json"
    monkeypatch.setenv("MANJU_RECENTS", str(custom))
    assert recents_path() == custom


def test_load_recents_tolerates_corrupt_file(tmp_path, monkeypatch):
    custom = tmp_path / "corrupt.json"
    custom.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv("MANJU_RECENTS", str(custom))
    result = load_recents()
    assert result.entries == [] and result.dropped == []


def test_touch_recent_never_raises_on_bad_config(tmp_path):
    """A project whose config fails to parse must still get recorded (falls
    back to the directory stem for its name) — recents is best-effort."""
    p = _mk_project(tmp_path, "badcfg")
    (p.root / "project.yaml").write_text("not: [valid, yaml", encoding="utf-8")
    touch_recent(p)  # must not raise
    entries = load_recents(drop_missing=False).entries
    assert entries and entries[0]["path"] == str(p.root)


# =========================================================================
# CLI — _project() touches recents once per process
# =========================================================================


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


def test_cli_project_resolution_touches_recents(in_project, monkeypatch):
    monkeypatch.setattr(cli_mod, "_RECENTS_TOUCHED", False)
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    entries = load_recents().entries
    assert any(Path(e["path"]) == in_project.root for e in entries)


def test_cli_touches_recents_at_most_once_per_process(in_project, monkeypatch):
    calls: list[Path] = []

    def fake_touch(project):
        calls.append(project.root)

    monkeypatch.setattr(cli_mod, "_RECENTS_TOUCHED", False)
    monkeypatch.setattr("manju.core.recents.touch_recent", fake_touch)
    assert runner.invoke(app, ["status", "--json"]).exit_code == 0
    assert runner.invoke(app, ["check", "--json"]).exit_code == 0
    # two commands, two separate `_project()` resolutions — the module-level
    # guard means only the FIRST one actually touches recents
    assert len(calls) == 1


def test_cli_no_project_never_touches_recents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # not a manju project
    monkeypatch.setattr(cli_mod, "_RECENTS_TOUCHED", False)
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code != 0
    assert load_recents().entries == []


# =========================================================================
# GUI — workspace picker + switcher (real HTTP)
# =========================================================================


def _request(server, path, *, method="GET", body=None, headers=None, host=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            body_out = payload if raw else json.loads(payload or b"{}")
            return resp.status, dict(resp.headers), body_out
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _post(server, path, body, token="__default__", **kw):
    headers = {} if token is None else {"X-Manju-Token": server.token if token == "__default__" else token}
    return _request(server, path, method="POST", body=body, headers=headers, **kw)


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------- picker (no project)


def test_picker_serves_outside_project(tmp_path):
    """`manju gui` with no bound project (the round-X fix for user pain #8:
    it used to hard-fail here) serves the picker instead of erroring."""
    other = _mk_project(tmp_path, "recent_one")
    touch_recent(other)

    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, headers, html_body = _request(server, "/", raw=True)
        text = html_body.decode("utf-8")
        assert status == 200
        assert "Content-Security-Policy" in headers
        assert "工作区" in text
        assert 'id="ws-open-form"' in text
        assert 'id="ws-new-form"' in text
        assert str(other.root) in text  # the recent project is listed
    finally:
        server.shutdown()
        server.close()


def test_picker_project_dependent_routes_404_when_unbound(tmp_path):
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, _ = _request(server, "/api/state")
        assert status == 404
        status, _, _ = _request(server, "/api/check")
        assert status == 404
    finally:
        server.shutdown()
        server.close()


def test_recents_endpoint_works_unbound(tmp_path):
    other = _mk_project(tmp_path, "recent_two")
    touch_recent(other)
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _request(server, "/api/workspace/recents")
        assert status == 200
        assert data["current"] is None
        assert any(r["path"] == str(other.root) for r in data["recents"])
    finally:
        server.shutdown()
        server.close()


def test_workspace_open_rebinds_unbound_server(tmp_path):
    target = _mk_project(tmp_path, "to_open")
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _post(server, "/api/workspace/open", {"path": str(target.root)})
        assert status == 200 and data["ok"] is True
        assert data["root"] == str(target.root)

        # now bound: /api/state works, and / serves the SPA (not the picker)
        status, _, state = _request(server, "/api/state")
        assert status == 200
        assert state["project"]["name"] == target.load_config().name

        status, _, html_body = _request(server, "/", raw=True)
        text = html_body.decode("utf-8")
        assert 'id="cockpit"' in text  # SPA marker
        assert 'id="ws-open-form"' not in text  # not the picker anymore

        # and it landed in recents
        assert any(Path(e["path"]) == target.root for e in load_recents().entries)
    finally:
        server.shutdown()
        server.close()


def test_workspace_open_unknown_path_404(tmp_path):
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _post(server, "/api/workspace/open", {"path": str(tmp_path / "nope")})
        assert status == 404
        assert "error" in data
    finally:
        server.shutdown()
        server.close()


def test_workspace_new_creates_lands_and_appears_in_recents(tmp_path):
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _post(server, "/api/workspace/new", {
            "name": "新片场", "vertical": True, "path": str(tmp_path),
        })
        assert status == 201 and data["ok"] is True
        newp = Project(tmp_path / "新片场.manju")
        assert newp.load_config().name == "新片场"

        # server rebound to it
        status, _, state = _request(server, "/api/state")
        assert status == 200 and state["project"]["name"] == "新片场"

        assert any(Path(e["path"]) == newp.root for e in load_recents().entries)
    finally:
        server.shutdown()
        server.close()


def test_workspace_new_invalid_name_rejected(tmp_path):
    server = create_server(None, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _post(server, "/api/workspace/new", {
            "name": "../evil", "path": str(tmp_path),
        })
        assert status == 400
        assert not (tmp_path.parent / "evil.manju").exists()
    finally:
        server.shutdown()
        server.close()


# ------------------------------------------------------------- bound-mode guards


def test_workspace_open_requires_token(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _post(server, "/api/workspace/open", {"path": str(tmp_project.root)}, token="wrong")
        assert status == 403
        assert "token" in data["error"].lower()
    finally:
        server.shutdown()
        server.close()


def test_workspace_post_blocked_in_readonly(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    _serve(server)
    try:
        status, _, data = _post(server, "/api/workspace/open", {"path": str(tmp_project.root)})
        assert status == 403 and "readonly" in data["error"].lower()
        status, _, data = _post(server, "/api/workspace/new", {"name": "x"})
        assert status == 403
    finally:
        server.shutdown()
        server.close()


def test_workspace_open_rebinds_already_bound_server(tmp_project, tmp_path):
    other = _mk_project(tmp_path, "sibling_project")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _post(server, "/api/workspace/open", {"path": str(other.root)})
        assert status == 200
        status, _, state = _request(server, "/api/state")
        assert state["project"]["name"] == other.load_config().name

        status, _, recents = _request(server, "/api/workspace/recents")
        paths = {r["path"]: r for r in recents["recents"]}
        assert paths[str(other.root)]["current"] is True
        assert str(tmp_project.root) in paths  # the previously-bound project is remembered too
    finally:
        server.shutdown()
        server.close()


def test_workspace_does_not_trigger_legacy_workspace_chip(tmp_project, tmp_path):
    """Round X's recents-based rebind must stay independent of the OLDER
    --workspace directory-scan feature (`self.server.workspace`) — otherwise
    the SPA's pre-existing workspace chip would pop up a second, redundant
    switcher any time a recents-based open happens outside --workspace mode."""
    other = _mk_project(tmp_path, "sibling_two")
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        _post(server, "/api/workspace/open", {"path": str(other.root)})
        status, _, state = _request(server, "/api/state")
        assert status == 200
        assert state["workspace"] is None
    finally:
        server.shutdown()
        server.close()


# ------------------------------------------------------------- switcher (bound chrome)


def test_switcher_renders_on_spa_when_bound(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, html_body = _request(server, "/", raw=True)
        text = html_body.decode("utf-8")
        assert status == 200
        assert 'id="mj-ws-btn"' in text
        assert 'id="mj-ws-menu"' in text
    finally:
        server.shutdown()
        server.close()


def test_switcher_renders_on_server_rendered_pages(tmp_project):
    """The switcher lives in the SHARED chrome (gui/pages.py's nav_html), not
    just the SPA — it must show up on the server-rendered pages too."""
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, html_body = _request(server, "/library", raw=True)
        text = html_body.decode("utf-8")
        assert status == 200
        assert 'id="mj-ws-btn"' in text
    finally:
        server.shutdown()
        server.close()


def test_workspace_query_param_reaches_picker_while_bound(tmp_project):
    """The switcher's "管理工作区" link (`/?workspace=1`) reaches the full
    picker (forms + recents) even while a project IS bound."""
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, html_body = _request(server, "/?workspace=1", raw=True)
        text = html_body.decode("utf-8")
        assert status == 200
        assert 'id="ws-new-form"' in text
        assert "返回当前项目" in text
    finally:
        server.shutdown()
        server.close()


def test_recents_endpoint_marks_current_and_series_grouping(tmp_path):
    series = Series.create(tmp_path / "myshow", name="我的剧集", git_init=False)
    ep = new_episode(series, "E01", title="第一集")
    touch_recent(ep)

    server = create_server(ep, host="127.0.0.1", port=0)
    _serve(server)
    try:
        status, _, data = _request(server, "/api/workspace/recents")
        assert status == 200
        row = next(r for r in data["recents"] if r["path"] == str(ep.root))
        assert row["current"] is True
        assert row["series"] is not None
        assert row["series"]["name"] == "我的剧集"
    finally:
        server.shutdown()
        server.close()
