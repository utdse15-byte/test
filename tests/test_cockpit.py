"""The project cockpit (round V, goal item 4) — engine read model + GUI.

Two surfaces, one truth: :func:`manju.gui.cockpit.cockpit_data` aggregates the
existing engine reads into the block layout of REPORTS/ROUND-V-REFERENCES-2.md
(state → one action → activity → risk-by-exception), and the SPA home renders it
as a hero + block grid served over the same strict-client HTTP server. These
tests pin the block shapes against fixtures (fresh / stale / budget / healthy),
prove per-block degradation (one poisoned source never 500s the cockpit),
exercise ``GET /api/cockpit`` over real HTTP, and re-assert the CSP/mode-aware
render invariants the whole GUI is built on.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from manju.core.models import RemoteJobInfo, TakeSidecar
from manju.core.spec import compute_spec_hash
from manju.gui import cockpit as ck
from manju.gui.cockpit import cockpit_data
from manju.gui.server import create_server


# --------------------------------------------------------------- fixtures / helpers


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "gui_state.json"))
    monkeypatch.setenv("MANJU_ROUTING", str(tmp_path / "no_user_routing.yaml"))


def _request(server, path, *, method="GET", headers=None, host=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, dict(resp.headers), (body if raw else json.loads(body or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        return exc.code, dict(exc.headers), (payload if raw else json.loads(payload or b"{}"))


def _fresh_take(project, make_take, shot_id):
    """Register a take whose spec_hash matches the shot NOW → FRESH, and select
    it (so the shot reads 就绪, not 待挑选)."""
    shot = project.load_shot(shot_id)
    h = compute_spec_hash(shot, project.load_bible())
    take = make_take(project, shot_id, h)
    shot = project.load_shot(shot_id)
    shot.status.selected_take = take.name
    project.save_shot(shot)
    return take


def _stale_take(project, make_take, shot_id):
    """A selected take whose spec_hash no longer matches → STALE."""
    take = make_take(project, shot_id, "sha256:stale-old")
    shot = project.load_shot(shot_id)
    shot.status.selected_take = take.name
    project.save_shot(shot)
    return take


ALL_BLOCKS = {"identity", "state", "next_action", "suggestions", "risks",
              "deliverables", "spend", "queue", "activity", "approvals", "onboarding"}


# --------------------------------------------------------------- fresh project


def test_cockpit_fresh_project_leads_with_onboarding(tmp_project):
    c = cockpit_data(tmp_project)
    assert set(c) == ALL_BLOCKS  # every block present
    # brand-new: the brief stage, the hero points at the storyboard
    assert c["state"]["phase"] == "brief"
    assert c["state"]["shots_total"] == 0
    assert c["next_action"]["verb"] == "story"
    assert c["next_action"]["action"] is None
    # onboarding leads a fresh project (the empty-state pattern)
    assert c["onboarding"]["should_show"] is True
    # a calm project shows NO risks
    assert c["risks"]["items"] == []


# --------------------------------------------------------------- stale project


def test_cockpit_stale_shot_is_a_risk_with_redo(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    _stale_take(tmp_project, make_take, "S001")

    c = cockpit_data(tmp_project)
    assert c["state"]["shots_by_state"] == {"stale": 1}
    # the risk block flags the stale shot (Linear "at risk")
    kinds = [r["kind"] for r in c["risks"]["items"]]
    assert "stale" in kinds
    assert all(r.get("level") for r in c["risks"]["items"])
    # the ONE next action is a redo of that shot (director.suggest_next)
    assert c["next_action"]["verb"] == "redo"
    assert c["next_action"]["shot"] == "S001"
    assert c["next_action"]["action"] == {"type": "redo", "shot": "S001"}


# --------------------------------------------------------------- budget risk


def test_cockpit_budget_near_limit_is_a_risk(tmp_project, add_shot, make_take):
    from manju.core.yamlio import write_yaml

    add_shot(tmp_project, "S001")
    take = _fresh_take(tmp_project, make_take, "S001")
    # book a real cost onto the take's sidecar (the §3 sidecar spend source)
    sidecar = take.sidecar
    sidecar.remote = RemoteJobInfo(cost=0.9, currency="CNY")
    write_yaml(take.sidecar_path, sidecar.model_dump(exclude_none=True))
    # a tight budget → ≥80% trips the guard
    config = tmp_project.load_config()
    config.budget.limit = 1.0
    tmp_project.save_config(config)

    c = cockpit_data(tmp_project)
    budget = [r for r in c["risks"]["items"] if r["kind"] == "budget"]
    assert budget, "budget-near-limit should surface as a risk"
    assert budget[0]["level"] == "warn"  # 0.9/1.0 = 90% (not yet over)
    assert c["spend"]["budget_limit"] == 1.0
    assert c["spend"]["total"] == pytest.approx(0.9)


# --------------------------------------------------------------- healthy project


def test_cockpit_healthy_project_has_no_risks(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    _fresh_take(tmp_project, make_take, "S001")
    # approve the shot so nothing is pending review either
    shot = tmp_project.load_shot("S001")
    shot.status.review = "approved"
    tmp_project.save_shot(shot)

    c = cockpit_data(tmp_project)
    assert c["state"]["shots_by_state"] == {"fresh": 1}
    assert c["risks"]["items"] == []              # the calm baseline
    assert c["approvals"]["pending"] == 0
    # deliverables still enumerate the nine ship outputs (all missing yet)
    assert isinstance(c["deliverables"]["rows"], list)
    assert len(c["deliverables"]["rows"]) == 9


# --------------------------------------------------------------- approvals


def test_cockpit_approvals_pending_counts_unapproved_selected(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    _fresh_take(tmp_project, make_take, "S001")  # selected, default review = needs_review
    c = cockpit_data(tmp_project)
    assert c["approvals"]["pending"] == 1
    assert c["approvals"]["shots"] == [{"shot": "S001", "review": "needs_review"}]


# --------------------------------------------------------------- degradation


def test_cockpit_degrades_per_block(tmp_project, add_shot, make_take, monkeypatch):
    add_shot(tmp_project, "S001")
    _fresh_take(tmp_project, make_take, "S001")

    def boom(_project):
        raise RuntimeError("poisoned spend source")

    monkeypatch.setattr(ck, "spend_report", boom)
    c = cockpit_data(tmp_project)
    # the poisoned block degrades to a one-line error string…
    assert "error" in c["spend"]
    assert isinstance(c["spend"]["error"], str) and c["spend"]["error"]
    # …and every neighbour still renders (no cascade, no 500)
    for block in ("state", "next_action", "activity", "deliverables", "approvals"):
        assert "error" not in c[block], block
    # risks still renders — it just omits the (unavailable) budget item
    assert "error" not in c["risks"]
    assert not any(r["kind"] == "budget" for r in c["risks"]["items"])


def test_cockpit_never_raises_on_broken_project(tmp_project):
    # scribble garbage into project.yaml — cockpit_data must still return a
    # fully-shaped payload (every block degrades, none raises)
    (tmp_project.root / "project.yaml").write_text("{{{ not yaml", encoding="utf-8")
    c = cockpit_data(tmp_project)
    assert set(c) == ALL_BLOCKS
    assert isinstance(c, dict)


# --------------------------------------------------------------- HTTP surface


def test_api_cockpit_over_http(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    _stale_take(tmp_project, make_take, "S001")
    status, headers, data = _request(gui, "/api/cockpit")
    assert status == 200
    assert "application/json" in headers.get("Content-Type", "")
    assert set(data) == ALL_BLOCKS
    assert data["next_action"]["verb"] == "redo"
    assert any(r["kind"] == "stale" for r in data["risks"]["items"])


def test_api_cockpit_is_a_read_no_token_needed(gui):
    # GET reads never require the X-Manju-Token (same as /api/state)
    status, _, data = _request(gui, "/api/cockpit")
    assert status == 200 and "state" in data


def test_api_cockpit_host_guarded(gui):
    status, _, _ = _request(gui, "/api/cockpit", host="evil.example.com")
    assert status == 403


# --------------------------------------------------------------- home render


def test_home_renders_cockpit_hero_and_blocks(gui):
    status, headers, body = _request(gui, "/", raw=True)
    assert status == 200
    html = body.decode("utf-8")
    # the cockpit region replaces the old top loading header
    assert 'id="cockpit"' in html
    # its JS + CSS shipped in the app bundle
    _, _, js = _request(gui, "/app.js", raw=True)
    js = js.decode("utf-8")
    for needle in ("renderCockpit", "maybeCockpit", "heroBuild", "/api/cockpit",
                   "ck-hero", "ck-risks", "renderCockGrid"):
        assert needle in js, needle
    _, _, css = _request(gui, "/app.css", raw=True)
    css = css.decode("utf-8")
    assert ".cockpit" in css and ".ck-hero" in css and ".ck-risks" in css


def test_home_cockpit_is_csp_and_xss_safe(gui):
    status, headers, body = _request(gui, "/", raw=True)
    assert status == 200
    html = body.decode("utf-8")
    assert "script-src 'self'" in headers.get("Content-Security-Policy", "")
    # no inline handlers / inline styles / inline scripts in the served page
    for bad in ("onclick", "onload", "onerror", " style=", "<script>"):
        assert bad not in html, bad
    # the app script never assigns innerHTML (textContent / CSSOM only)
    _, _, js = _request(gui, "/app.js", raw=True)
    js = js.decode("utf-8")
    assert "innerHTML =" not in js and "innerHTML=" not in js


def test_home_cockpit_is_mode_aware(gui):
    from manju.gui import userstate

    # conftest points MANJU_GUI_STATE at a fresh tmp file → round-U default 新手
    _, _, body = _request(gui, "/", raw=True)
    assert 'class="mj-mode-beginner' in body.decode("utf-8")
    # switching to 专业 flips the body class the cockpit inherits
    userstate.set_mode("pro")
    _, _, body = _request(gui, "/", raw=True)
    assert "mj-mode-pro" in body.decode("utf-8")
