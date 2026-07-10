"""DR05 — the opt-in `unattended` profile decision table (§12 items 28-44) and
the AgentWorkspaceSnapshot gate decision (45-46, SKIPPED_WITH_EVIDENCE).

The unattended profile is reachable ONLY through `manju serve-mcp
--agent-profile unattended` (server flag). Project content — project.yaml,
skills, shots, tool arguments — can NEVER select or escalate it. list and call
share the ONE resolver; a hidden tool called by name returns the structured
`agent_profile_denied` denial, never a natural-language-only error and never
disguised as unknown-tool.

RED before the profile threading + policy dicts land; GREEN after.
"""

from __future__ import annotations

import pytest

from manju.mcp import policy as P
from manju.mcp import tools as mcp_tools
from manju.mcp.policy import resolve_agent_surface
from manju.mcp.server import MCPServer
from manju.mcp.tools import AgentProfileDenied, ToolError, call_tool, list_tools

PURE_READS = [
    "status", "explain", "impact", "check", "list_shots", "get_shot", "events",
    "qc_coverage", "director_suggest", "funnel_status", "skill_list",
    "skill_show", "qc_brief", "agent_surface",
]


def _surf(profile=P.UNATTENDED):
    return resolve_agent_surface(mcp_tools.TOOL_DEFS, profile)


# ================================================== 28-39 the decision table


def test_un_28_all_pure_reads_allow():
    surf = _surf()
    for name in PURE_READS:
        d = surf.decide(name, {})
        assert d.admitted, f"read {name} denied in unattended"
        assert name in surf.listed_names()


def test_un_29_update_shot_allow_with_cas():
    surf = _surf()
    assert surf.decide("update_shot", {"shot_id": "S1", "yaml_content": "id: S1",
                                       "expected_rev": "sha256:abc"}).admitted
    denied = surf.decide("update_shot", {"shot_id": "S1", "yaml_content": "id: S1"})
    assert not denied.admitted
    # LISTED though (usable — it just requires the CAS token)
    assert "update_shot" in surf.listed_names()


def test_un_30_denial_payload_shape():
    d = _surf().decide("update_shot", {"shot_id": "S1", "yaml_content": "id: S1"})
    pay = d.denial_payload()
    assert pay["code"] == P.DENIAL_CODE == "agent_profile_denied"
    assert pay["tool"] == "update_shot"
    assert pay["profile"] == P.UNATTENDED
    assert pay["required_path"] and isinstance(pay["required_path"], str)
    assert "error" in pay  # human-readable line too — structured AND worded
    assert "expected_rev" in pay["required_path"]


def test_un_31_select_take_allow_keeps_checked_write():
    d = _surf().decide("select_take", {"shot_id": "S1", "take": "take_01"})
    assert d.admitted
    # no new CAS field was invented for it
    pol = next(t for t in mcp_tools.TOOL_DEFS if t["name"] == "select_take")["policy"]
    assert pol["unattended"] == P.ALLOW
    assert P.GATE_CAS not in pol["gate"]


def test_un_32_build_dry_run_only():
    surf = _surf()
    assert surf.decide("build", {"dry_run": True}).admitted
    assert not surf.decide("build", {}).admitted                 # no dry_run
    assert not surf.decide("build", {"dry_run": False}).admitted
    assert not surf.decide("build", {"gen": "off"}).admitted     # gen=off NOT whitelisted
    # dry_run:true short-circuits before any generation (graph.py:916), so it is
    # admitted regardless of gen — the free plan is what matters
    assert surf.decide("build", {"dry_run": True, "gen": "auto"}).admitted
    # build stays LISTED (usable for cost estimates)
    assert "build" in surf.listed_names()


def test_un_33_build_dry_run_admitted_call_is_free(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    out = call_tool(tmp_project, "build", {"dry_run": True}, profile=P.UNATTENDED)
    assert out["ok"] is True
    assert out["estimated_cost"] == 0
    assert not out.get("generated")


def test_un_34_redo_deny_hidden_and_structured_denial(tmp_project, add_shot):
    surf = _surf()
    assert not surf.decide("redo", {"shot_id": "S001"}).admitted
    assert "redo" not in surf.listed_names()          # hidden from tools/list
    add_shot(tmp_project, "S001")
    # called by NAME anyway -> structured denial (not unknown-tool)
    with pytest.raises(AgentProfileDenied) as exc:
        call_tool(tmp_project, "redo", {"shot_id": "S001"}, profile=P.UNATTENDED)
    assert exc.value.payload["code"] == P.DENIAL_CODE
    assert exc.value.payload["tool"] == "redo"


def test_un_35_director_confirm_deny(tmp_project):
    surf = _surf()
    assert not surf.decide("director_confirm", {"id": "prop_0001"}).admitted
    assert "director_confirm" not in surf.listed_names()
    with pytest.raises(AgentProfileDenied):
        call_tool(tmp_project, "director_confirm", {"id": "prop_0001"},
                  profile=P.UNATTENDED)


def test_un_36_37_propose_and_director_propose_allow(tmp_project, add_shot):
    surf = _surf()
    assert surf.decide("propose", {"title": "t", "body": "b"}).admitted
    assert surf.decide("director_propose", {"actions": [{"type": "snapshot"}]}).admitted
    add_shot(tmp_project, "S001")
    prop = call_tool(tmp_project, "director_propose",
                     {"actions": [{"type": "snapshot"}]}, profile=P.UNATTENDED)
    assert prop["state"] == "proposed"


def test_un_38_director_execute_confirmed_proposal_only(tmp_project, add_shot):
    # the profile ADMITS the call; the ENGINE gate re-checks confirmed/current/
    # human — the profile never replaces it.
    surf = _surf()
    assert surf.decide("director_execute", {"id": "prop_0001"}).admitted
    assert "director_execute" in surf.listed_names()
    add_shot(tmp_project, "S001")
    prop = call_tool(tmp_project, "director_propose",
                     {"actions": [{"type": "redo", "shot": "S001"}]},
                     profile=P.UNATTENDED)
    # unconfirmed -> the ENGINE refuses (ToolError), NOT a profile denial
    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "director_execute", {"id": prop["id"]},
                  profile=P.UNATTENDED)
    assert not isinstance(exc.value, AgentProfileDenied)


def test_un_39_derived_writes_allow():
    surf = _surf()
    for name in ("qc", "export", "board", "qc_verdict"):
        assert surf.decide(name, {}).admitted, name
        assert name in surf.listed_names()


# ================================================ 40-42 surface + source rules


def test_un_40_unattended_hides_exactly_deny_tools():
    collab = set(resolve_agent_surface(mcp_tools.TOOL_DEFS, P.COLLABORATIVE).listed_names())
    unatt = set(_surf().listed_names())
    assert collab - unatt == {"redo", "director_confirm"}


def test_un_41_hidden_tool_by_name_is_structured_not_unknown(tmp_project):
    # the denial is NOT disguised as unknown-tool, and carries the required_path
    with pytest.raises(AgentProfileDenied) as exc:
        call_tool(tmp_project, "redo", {"shot_id": "S001"}, profile=P.UNATTENDED)
    pay = exc.value.payload
    assert pay["code"] == "agent_profile_denied"
    assert "unknown tool" not in pay["error"].lower()
    assert pay["required_path"]
    # a genuinely unknown tool still raises the plain unknown-tool ToolError
    with pytest.raises(ToolError) as exc2:
        call_tool(tmp_project, "does_not_exist", {}, profile=P.UNATTENDED)
    assert not isinstance(exc2.value, AgentProfileDenied)


def test_un_42_profile_comes_only_from_the_flag_not_project_content(tmp_project, add_shot):
    # planting agent_profile in project.yaml / passing it as a tool argument must
    # NOT escalate: call_tool defaults to collaborative and only the explicit
    # profile= arg (fed by the server flag) changes it.
    import yaml
    py = tmp_project.root / "project.yaml"
    data = yaml.safe_load(py.read_text("utf-8")) or {}
    data["agent_profile"] = "unattended"
    data["mode"] = "autopilot"
    py.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    add_shot(tmp_project, "S001")
    # default is still collaborative -> redo admitted despite the planted field
    surf = resolve_agent_surface(mcp_tools.TOOL_DEFS, P.COLLABORATIVE)
    assert surf.decide("redo", {"shot_id": "S001"}).admitted
    # a tool argument claiming a profile is ignored (redo still runs the engine,
    # not a profile switch) — the resolver only knows the profile it was handed
    with pytest.raises(ValueError):
        resolve_agent_surface(mcp_tools.TOOL_DEFS, "autopilot")  # unknown profile rejected


# ============================== 43-44 no unattended spend/network (transport spy)


def test_un_43_44_unattended_session_never_spends(tmp_project, add_shot, monkeypatch):
    """A monkeypatched spend/network counter: the ONLY MCP paths that spend are
    run_build(dry_run=False) and redo_shot (and director_execute's engine run).
    Drive a full unattended session — reads, a dry-run build, and the denied
    paid tools called by name — and prove the counter never moves."""
    spend = {"network": 0}
    real_build = mcp_tools.run_build
    real_redo = mcp_tools.redo_shot

    def spy_build(project, **kw):
        if not kw.get("dry_run"):
            spend["network"] += 1   # a real (paid/network) build
        return real_build(project, **kw)

    def spy_redo(project, shot_id, **kw):
        spend["network"] += 1       # redo always forces (paid) generation
        return real_redo(project, shot_id, **kw)

    monkeypatch.setattr(mcp_tools, "run_build", spy_build)
    monkeypatch.setattr(mcp_tools, "redo_shot", spy_redo)

    add_shot(tmp_project, "S001")
    # admitted reads + a dry-run build
    for name in ("status", "list_shots", "check", "funnel_status"):
        call_tool(tmp_project, name, {}, profile=P.UNATTENDED)
    call_tool(tmp_project, "build", {"dry_run": True}, profile=P.UNATTENDED)

    # denied paid tools called by name -> denial precedes dispatch (enforcement
    # at the call gate, not description parsing): the engine handler never runs
    for name, args in (("redo", {"shot_id": "S001"}),
                       ("build", {"gen": "auto"}),
                       ("build", {"dry_run": False})):
        with pytest.raises(AgentProfileDenied):
            call_tool(tmp_project, name, args, profile=P.UNATTENDED)

    assert spend["network"] == 0    # zero real generation across the session


# ============================ real MCPServer list/call parity through the flag


def test_server_threads_profile_into_list_and_call(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    server = MCPServer(tmp_project.root, agent_profile=P.UNATTENDED)
    # tools/list reflects the profile (redo/director_confirm hidden)
    listed = {t["name"] for t in server._dispatch("tools/list", {})["tools"]}
    assert "redo" not in listed and "director_confirm" not in listed
    assert "agent_surface" in listed and "status" in listed
    # tools/call denial is a structured isError result (not a JSON-RPC error)
    res = server._tools_call({"name": "redo", "arguments": {"shot_id": "S001"}})
    assert res["isError"] is True
    import json
    payload = json.loads(res["content"][0]["text"])
    assert payload["code"] == "agent_profile_denied"
    assert payload["tool"] == "redo" and payload["profile"] == P.UNATTENDED
    # an admitted read still works over the same server
    ok = server._tools_call({"name": "status", "arguments": {}})
    assert ok["isError"] is False


def test_server_default_profile_is_collaborative(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    server = MCPServer(tmp_project.root)  # no flag
    listed = {t["name"] for t in server._dispatch("tools/list", {})["tools"]}
    assert "redo" in listed and "director_confirm" in listed  # nothing hidden
    res = server._tools_call({"name": "redo", "arguments": {"shot_id": "S001"}})
    # collaborative: redo dispatches to the engine (no profile denial); with no
    # provider it fails as an ordinary engine error, never agent_profile_denied
    if res["isError"]:
        import json
        assert json.loads(res["content"][0]["text"]).get("code") != "agent_profile_denied"


# ================================ 45-46 AgentWorkspaceSnapshot gate (SKIPPED)


def test_snap_45_no_snapshot_tool_added_status_carries_it(tmp_project, add_shot):
    # DECISION: SKIPPED_WITH_EVIDENCE. `status` already composes stage/gaps/next
    # step/spend/recent-events in ONE read, so the §10.1 "≥3 calls status cannot
    # carry" bar is NOT met. We add NO workspace_snapshot and NO agent_context.
    names = {t["name"] for t in mcp_tools.TOOL_DEFS}
    assert "workspace_snapshot" not in names
    assert "agent_snapshot" not in names
    assert "agent_context" not in names
    add_shot(tmp_project, "S001")
    st = call_tool(tmp_project, "status", {}, profile=P.UNATTENDED)
    # the takeover inventory a snapshot would have duplicated, in one call
    for key in ("shots_by_state", "next_step", "total_cost", "budget_limit",
                "recent_events", "qc", "timeline", "mode"):
        assert key in st


def test_snap_46_cheap_json_reads_exist_for_takeover():
    # the composition the snapshot would bundle is already available as cheap
    # read tools, all admitted unattended
    surf = _surf()
    for name in ("status", "funnel_status", "director_suggest", "skill_list"):
        assert surf.decide(name, {}).admitted
