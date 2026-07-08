"""The AI-director loop (goal item 17): build/director.py + CLI + MCP + GUI.

The six-step collaboration contract as one auditable object — propose → cost/
impact → confirm → execute → diff → suggest next. These tests drive the core
lifecycle (incl. expiry-on-change), assert the cost figures come from the SAME
estimator the GUI plan modal uses (never a parallel one), pin the execute
ordering + first-failure stop + auto-snapshot + rollback path, cover the
suggest_next heuristics, and exercise the MCP tools + the GUI page/guards over
real HTTP. No ffmpeg is needed anywhere — every action exercised is a text/git
operation or a mocked engine call.
"""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from manju.build import director as d
from manju.core.container import Project
from manju.core.models import ShotSpec
from manju.core.spec import compute_spec_hash
from manju.core.yamlio import write_yaml

PROJECT_NAME = "雨夜便利店"


# --------------------------------------------------------------- fixtures


def _bible(project: Project) -> None:
    write_yaml(project.root / "bible" / "scenes.yaml",
               {"store": {"name": "便利店", "description": "雨夜街角的便利店。"}})
    write_yaml(project.root / "bible" / "characters.yaml",
               {"lin": {"name": "林夏", "voice": "冷静、克制", "appearance": "短发"}})


def _shot(project: Project, shot_id: str = "S001", **over) -> ShotSpec:
    data = {"id": shot_id, "scene": "store", "characters": ["lin"],
            "dialogue": {"speaker": "lin", "text": "这不可能。"}, "duration": "auto"}
    data.update(over)
    shot = ShotSpec.model_validate(data)
    project.save_shot(shot)
    idx = project.load_index()
    if shot_id not in idx.order:
        idx.order.append(shot_id)
        project.save_index(idx)
    return shot


@pytest.fixture
def project(tmp_path: Path) -> Project:
    p = Project.create(tmp_path / PROJECT_NAME, name=PROJECT_NAME, git_init=False)
    _bible(p)
    _shot(p, "S001")
    return p


@pytest.fixture
def git_project(tmp_path: Path) -> Project:
    """A git-backed project (snapshots/rollback need a repo) with rules.yaml
    materialized and everything committed."""
    p = Project.create(tmp_path / PROJECT_NAME, name=PROJECT_NAME, git_init=True)
    _bible(p)
    _shot(p, "S001")
    p.save_rules(p.load_rules())  # materialize timeline/rules.yaml (mode: compiled)
    subprocess.run(["git", "-C", str(p.root), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(p.root), "-c", "user.email=a@b.c",
                    "-c", "user.name=t", "commit", "-qm", "init"], capture_output=True)
    return p


# --------------------------------------------------------------- lifecycle


def test_propose_persists_annotated_proposal(project):
    prop = d.propose(project, [{"type": "snapshot", "label": "cp"}],
                     why="初始存档", actor="ai")
    assert prop.state == "proposed"
    assert prop.why == "初始存档" and prop.actor == "ai"
    assert prop.fingerprint  # a state fingerprint was stamped
    assert len(prop.actions) == 1
    # persisted as human-editable YAML under reports/proposals/
    path = d.proposals_dir(project) / f"{prop.id}.yaml"
    assert path.exists()
    reloaded = d.load_proposal(project, prop.id)
    assert reloaded.id == prop.id and reloaded.state == "proposed"


def test_propose_rejects_unknown_action_type(project):
    with pytest.raises(d.DirectorError):
        d.propose(project, [{"type": "launch_missiles"}])


def test_propose_rejects_bad_shape(project):
    with pytest.raises(d.DirectorError):  # redo needs a real shot
        d.propose(project, [{"type": "redo", "shot": "NOPE"}])
    with pytest.raises(d.DirectorError):  # repair needs a valid op
        d.propose(project, [{"type": "repair", "op": "explode", "shot": "S001"}])
    with pytest.raises(d.DirectorError):  # empty action list
        d.propose(project, [])


def test_confirm_is_a_separate_explicit_step(project):
    prop = d.propose(project, [{"type": "snapshot"}])
    # execute must refuse a merely-proposed (unconfirmed) plan
    with pytest.raises(d.DirectorError, match="only a confirmed"):
        d.execute(project, prop.id)
    confirmed = d.confirm(project, prop.id, actor="human")
    assert confirmed.state == "confirmed" and confirmed.confirmed_by == "human"


def test_reject_is_terminal(project):
    prop = d.propose(project, [{"type": "snapshot"}])
    r = d.reject(project, prop.id)
    assert r.state == "rejected"
    with pytest.raises(d.DirectorError):
        d.confirm(project, prop.id)


def test_expiry_on_change_at_confirm(project):
    prop = d.propose(project, [{"type": "snapshot"}])
    # move the project underneath the proposal
    shot = project.load_shot("S001")
    shot.action.text = "she turns"
    project.save_shot(shot)
    with pytest.raises(d.DirectorError, match="待更新"):
        d.confirm(project, prop.id)
    assert d.load_proposal(project, prop.id).state == "expired"


def test_expiry_on_change_between_confirm_and_execute(project):
    prop = d.propose(project, [{"type": "snapshot"}])
    d.confirm(project, prop.id)
    # a rules edit moves the fingerprint after confirm
    rules = project.load_rules()
    rules.captions.max_chars_per_line = 99
    project.save_rules(rules)
    with pytest.raises(d.DirectorError, match="待更新"):
        d.execute(project, prop.id)
    assert d.load_proposal(project, prop.id).state == "expired"


def test_list_marks_current_flag(project):
    prop = d.propose(project, [{"type": "snapshot"}])
    rows = d.list_proposals(project)
    assert rows[0].id == prop.id
    assert d._is_current(project, prop) is True
    shot = project.load_shot("S001")
    shot.action.text = "moved"
    project.save_shot(shot)
    assert d._is_current(project, d.load_proposal(project, prop.id)) is False


# ------------------------------------------------------------- cost parity


def test_cost_figures_equal_action_plan(project, monkeypatch):
    """The proposal's est cost must be the SAME number the GUI plan modal /
    build --dry-run produces for the same action — never a parallel estimator."""
    import manju.build.graph as graph
    from manju.gui.plan import action_plan

    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (2.0, "CNY"))

    plan_env = action_plan(project, "redo", {"shot": "S001"})
    prop = d.propose(project, [{"type": "redo", "shot": "S001"}])

    assert prop.estimated_cost == plan_env["estimated_cost"] == 2.0
    assert prop.currency == plan_env["currency"] == "CNY"
    # the priced action carries the plan rows in its impact (shots annotated)
    assert prop.actions[0].impact["shots"] == ["S001"]


def test_local_ops_price_zero(project):
    prop = d.propose(project, [
        {"type": "snapshot"},
        {"type": "captions", "op": "revert"},
        {"type": "rollback", "op": "shot", "shot": "S001"},
    ])
    assert prop.estimated_cost == 0.0
    assert all(pa.estimated_cost == 0.0 for pa in prop.actions)


# ------------------------------------------------------- execute + snapshot


def test_execute_snapshot_before_mutating_enables_one_step_rollback(git_project):
    """The auto-snapshot BEFORE mutating makes 还原 one step: a captions-save
    action flips rules.captions.mode → manual, and rollback_file restores the
    pre-execution compiled truth from the snapshot commit."""
    from manju.core.history import rollback_file

    prop = d.propose(git_project, [{
        "type": "captions", "op": "save",
        "cues": [{"start_ms": 0, "end_ms": 1000, "text": "你好"}],
    }])
    d.confirm(git_project, prop.id)
    out = d.execute(git_project, prop.id)

    assert out.ok is True and out.state == "done"
    assert out.snapshot and out.snapshot.get("sha")  # a real checkpoint was taken
    assert git_project.load_rules().captions.mode == "manual"  # the mutation landed

    # 还原 in one step: restore rules.yaml from the pre-mutation snapshot
    rollback_file(git_project, "timeline/rules.yaml")
    assert git_project.load_rules().captions.mode == "compiled"


def test_execute_ordering_stops_at_first_failure(git_project):
    """Actions run strictly in order; the FIRST failure stops the rest and is
    recorded as a structured failure."""
    from manju.core.failures import read_failures

    prop = d.propose(git_project, [
        {"type": "snapshot", "label": "ok-first"},           # #0 succeeds
        {"type": "rollback", "op": "file", "path": "shots/NOPE.yaml"},  # #1 fails
        {"type": "snapshot", "label": "never"},              # #2 never runs
    ])
    d.confirm(git_project, prop.id)
    out = d.execute(git_project, prop.id)

    assert out.ok is False and out.state == "failed"
    assert [r["index"] for r in out.results] == [0, 1]  # #2 never reached
    assert out.results[0]["ok"] is True
    assert out.results[1]["ok"] is False
    assert out.failure and out.failure.get("id")
    # the failure is in the structured failures log
    recs = read_failures(git_project, 5)
    assert any(r.get("id") == out.failure["id"] for r in recs)


def test_execute_build_passes_assume_yes_from_confirmed_state(git_project, monkeypatch):
    """Defense in depth: the assume_yes that lets a build spend comes ONLY from
    the proposal's confirmed state — execute passes assume_yes=True, and the
    engine's spend gate still applies."""
    import manju.build.graph as graph

    calls: list[dict] = []

    def fake_run_build(project, *, target="final", gen="missing", regen_stale=False,
                       dry_run=False, force=False, actor="engine",
                       assume_yes=False, on_phase=None, mode=None):
        calls.append({"dry_run": dry_run, "assume_yes": assume_yes})
        r = graph.BuildResult()
        r.ok = True
        r.plan = []
        r.generated = [] if dry_run else ["S001/take_01"]
        return r

    monkeypatch.setattr(graph, "run_build", fake_run_build)

    prop = d.propose(git_project, [{"type": "build", "target": "final"}])
    d.confirm(git_project, prop.id)
    out = d.execute(git_project, prop.id)

    assert out.ok is True
    # the dry-run (pricing) call and the real execute call both happened
    assert any(c["dry_run"] for c in calls)  # propose priced via dry-run
    real = [c for c in calls if not c["dry_run"]]
    assert real and real[-1]["assume_yes"] is True  # confirmed → assume_yes


def test_execute_diff_and_outcome_persisted(git_project):
    prop = d.propose(git_project, [{"type": "snapshot", "label": "x"}])
    d.confirm(git_project, prop.id)
    out = d.execute(git_project, prop.id)
    assert "spec" in out.diff and "outputs" in out.diff
    # the outcome is persisted onto the proposal file (auditable truth)
    reloaded = d.load_proposal(git_project, prop.id)
    assert reloaded.state == "done"
    assert reloaded.outcome and reloaded.outcome["ok"] is True
    assert isinstance(out.suggestions, list)


def test_execute_failure_in_nongit_snapshot_is_clean(project):
    """A snapshot action in a non-git project fails cleanly (first-failure stop),
    never a traceback — the auto-snapshot itself degrades to a note."""
    prop = d.propose(project, [{"type": "snapshot"}])
    d.confirm(project, prop.id)
    out = d.execute(project, prop.id)
    assert out.ok is False
    assert out.snapshot and out.snapshot.get("sha") is None  # no repo → degraded
    assert "not a git repository" in out.results[0]["error"]


# ------------------------------------------------------------ suggest_next


def test_suggest_missing_shot_generates(project):
    sugg = d.suggest_next(project)
    kinds = {s.kind for s in sugg}
    assert "generate" in kinds
    gen = next(s for s in sugg if s.kind == "generate")
    assert gen.action == {"type": "build", "target": "final", "gen": "missing"}


def test_suggest_stale_shot_redo(project, make_take):
    shot = project.load_shot("S001")
    take = make_take(project, "S001", compute_spec_hash(shot, project.load_bible()))
    project.update_shot_raw(
        "S001", lambda dd: dd.setdefault("status", {}).__setitem__("selected_take", take.name))
    # now move the spec so the selected take is STALE
    shot = project.load_shot("S001")
    shot.action.text = "she turns to the window"
    project.save_shot(shot)

    sugg = d.suggest_next(project)
    redo = [s for s in sugg if s.kind == "redo"]
    assert redo and redo[0].action == {"type": "redo", "shot": "S001"}


def test_suggest_qc_errors_to_repair_action(project):
    """A QC item whose suggestion names a repair op becomes a ready-made repair
    action (修复方案)."""
    qc = {"ok": False, "items": [
        {"level": "warn", "area": "technical", "subject": "S001",
         "message": "source shorter than clip",
         "suggestion": "manju repair --op extend --shot S001 --ms 500 --mode freeze"},
    ]}
    (project.reports_dir).mkdir(parents=True, exist_ok=True)
    (project.reports_dir / "qc.json").write_text(json.dumps(qc), encoding="utf-8")

    sugg = d.suggest_next(project)
    repair = [s for s in sugg if s.kind == "repair"]
    assert repair
    act = repair[0].action
    assert act["type"] == "repair" and act["op"] == "extend"
    assert act["shot"] == "S001" and act["ms"] == 500 and act["mode"] == "freeze"


def test_suggest_budget_near_limit(project, monkeypatch):
    import manju.build.spend as spend

    monkeypatch.setattr(spend, "spend_report",
                        lambda p: {"budget_limit": 10.0, "total": 9.0, "currency": "CNY"})
    sugg = d.suggest_next(project)
    assert any(s.kind == "budget" for s in sugg)
    budget = next(s for s in sugg if s.kind == "budget")
    assert budget.action is None  # advisory, no proposal action


def test_suggest_action_payloads_round_trip_into_propose(project):
    """Every suggestion carrying an action must be a valid propose() input —
    closing the loop in one call."""
    for s in d.suggest_next(project):
        if s.action is not None:
            prop = d.propose(project, [s.action], why="from suggestion")
            assert prop.state == "proposed"


# ----------------------------------------------------------------- MCP


def test_mcp_tool_schemas_present():
    from manju.mcp.tools import TOOLS, list_tools

    names = {t["name"] for t in list_tools()}
    for tool in ("director_propose", "director_confirm", "director_execute",
                 "director_suggest"):
        assert tool in names
        schema = TOOLS[tool]["inputSchema"]
        assert schema["type"] == "object"
    # the confirm/execute tools require an id
    assert "id" in TOOLS["director_confirm"]["inputSchema"]["required"]
    assert "id" in TOOLS["director_execute"]["inputSchema"]["required"]
    assert "actions" in TOOLS["director_propose"]["inputSchema"]["required"]


def test_mcp_driven_roundtrip_snapshot(git_project):
    from manju.mcp.tools import call_tool

    proposed = call_tool(git_project, "director_propose",
                         {"actions": [{"type": "snapshot", "label": "x"}], "why": "cp"})
    pid = proposed["id"]
    assert proposed["state"] == "proposed"

    confirmed = call_tool(git_project, "director_confirm", {"id": pid})
    assert confirmed["state"] == "confirmed"

    outcome = call_tool(git_project, "director_execute", {"id": pid})
    assert outcome["proposal_id"] == pid and outcome["ok"] is True

    sugg = call_tool(git_project, "director_suggest", {})
    assert "suggestions" in sugg


def test_mcp_driven_roundtrip_build_mocked(git_project, monkeypatch):
    """A driven propose→confirm→execute round-trip over the MCP wire with a
    MOCKED engine function (run_build) — no ffmpeg, exercises the paid path."""
    import manju.build.graph as graph

    from manju.mcp.tools import call_tool

    def fake_run_build(project, *, target="final", gen="missing", regen_stale=False,
                       dry_run=False, force=False, actor="engine",
                       assume_yes=False, on_phase=None, mode=None):
        r = graph.BuildResult()
        r.ok = True
        r.render_path = None if dry_run else "renders/final/final_v1.mp4"
        return r

    monkeypatch.setattr(graph, "run_build", fake_run_build)

    from manju.build.director import confirm as director_confirm

    proposed = call_tool(git_project, "director_propose",
                         {"actions": [{"type": "build", "target": "final"}]})
    pid = proposed["id"]
    # Round Y (#15): MCP (actor="ai") may NOT confirm a paid proposal — a human
    # must. Confirm through the human path (what the CLI/GUI do), then MCP can
    # mechanically execute the human-approved plan.
    director_confirm(git_project, pid, actor="human")
    outcome = call_tool(git_project, "director_execute", {"id": pid})
    assert outcome["ok"] is True
    assert outcome["results"][0]["type"] == "build"


def test_mcp_cannot_self_confirm_paid_proposal(git_project):
    """Round Y (#15): the hard human-only gate — an AI actor cannot confirm a
    proposal that spends (build/redo/voice). Free proposals stay AI-confirmable."""
    from manju.mcp.tools import ToolError, call_tool

    paid = call_tool(git_project, "director_propose",
                     {"actions": [{"type": "build", "target": "final"}]})
    with pytest.raises(ToolError, match="付费"):
        call_tool(git_project, "director_confirm", {"id": paid["id"]})

    # a free (local/text) proposal is still AI-confirmable
    free = call_tool(git_project, "director_propose", {"actions": [{"type": "snapshot"}]})
    confirmed = call_tool(git_project, "director_confirm", {"id": free["id"]})
    assert confirmed["state"] == "confirmed"


def test_mcp_confirm_expired_is_error(git_project):
    from manju.mcp.tools import ToolError, call_tool

    proposed = call_tool(git_project, "director_propose",
                         {"actions": [{"type": "snapshot"}]})
    shot = git_project.load_shot("S001")
    shot.action.text = "changed"
    git_project.save_shot(shot)
    with pytest.raises(ToolError, match="待更新"):
        call_tool(git_project, "director_confirm", {"id": proposed["id"]})


# ----------------------------------------------------------------- GUI


def _req(server, path, *, method="GET", body=None, token=None, host=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Manju-Token", token)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


@pytest.fixture
def gui(git_project):
    from manju.gui.server import create_server

    server = create_server(git_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def test_gui_page_and_assets_render(gui):
    st, html = _req(gui, "/director")
    assert st == 200
    assert "导演助手" in html and "director.js" in html
    # the nav carries the new 导演 link
    assert 'href="/director"' in html
    st, css = _req(gui, "/director.css")
    assert st == 200 and len(css) > 10
    st, js = _req(gui, "/director.js")
    assert st == 200 and len(js) > 10


def test_gui_state_endpoint_shape(gui):
    st, body = _req(gui, "/api/director/state")
    assert st == 200
    data = json.loads(body)
    assert "proposals" in data and "suggestions" in data


def test_gui_post_requires_token(gui):
    st, _ = _req(gui, "/api/director/propose", method="POST",
                 body={"actions": [{"type": "snapshot"}]})
    assert st == 403  # missing X-Manju-Token


def test_gui_host_guard(gui):
    st, _ = _req(gui, "/director", host="evil.example.com")
    assert st == 403  # DNS-rebinding guard


def test_gui_full_roundtrip_over_http(gui):
    tok = gui.token
    st, body = _req(gui, "/api/director/propose", method="POST", token=tok,
                    body={"actions": [{"type": "snapshot", "label": "cp"}], "why": "存档"})
    assert st == 200
    prop = json.loads(body)
    pid = prop["id"]
    assert prop["state"] == "proposed"

    # confirm and execute are SEPARATE POSTs (never one)
    st, body = _req(gui, "/api/director/confirm", method="POST", token=tok,
                    body={"id": pid})
    assert st == 200 and json.loads(body)["state"] == "confirmed"

    st, body = _req(gui, "/api/director/run", method="POST", token=tok, body={"id": pid})
    assert st == 200
    outcome = json.loads(body)
    assert outcome["ok"] is True and outcome["state"] == "done"


def test_gui_confirm_expired_is_409(gui, git_project):
    tok = gui.token
    st, body = _req(gui, "/api/director/propose", method="POST", token=tok,
                    body={"actions": [{"type": "snapshot"}]})
    pid = json.loads(body)["id"]
    # move the project underneath
    shot = git_project.load_shot("S001")
    shot.action.text = "moved"
    git_project.save_shot(shot)
    st, _ = _req(gui, "/api/director/confirm", method="POST", token=tok, body={"id": pid})
    assert st == 409


def test_gui_readonly_blocks_mutation(git_project):
    from manju.gui.server import create_server

    server = create_server(git_project, host="127.0.0.1", port=0, actor="human",
                           readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        st, _ = _req(server, "/api/director/propose", method="POST",
                     token=server.token, body={"actions": [{"type": "snapshot"}]})
        assert st == 403  # readonly workbench refuses mutations
    finally:
        server.shutdown()
        server.close()
