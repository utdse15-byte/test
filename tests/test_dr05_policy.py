"""DR05 — ToolPolicy registry integrity (§12 items 1-10), AgentSurfaceManifestV1
projection + stable digest (11-20), and default collaborative compatibility
(21-27).

RED before `manju.mcp.policy` exists (ImportError) and before TOOL_DEFS carry
`policy` dicts / the server threads a profile. GREEN once WP1-WP3 land.

The one resolver `resolve_agent_surface` is the SINGLE source: tools/list, the
call gate, and the agent_surface manifest all consume it — pinned here so no
parallel allowlist can drift in.
"""

from __future__ import annotations

import copy

import pytest

from manju.mcp import policy as P
from manju.mcp.policy import (
    PolicyError,
    resolve_agent_surface,
    validate_policy,
    validate_registry,
)
from manju.mcp.tools import TOOL_DEFS, call_tool, list_tools

BASELINE_TOOL_NAMES = [
    "status", "explain", "impact", "check", "list_shots", "get_shot",
    "update_shot", "select_take", "build", "redo", "qc", "qc_brief",
    "qc_coverage", "qc_verdict", "export", "events", "board", "propose",
    "director_propose", "director_confirm", "director_execute",
    "director_suggest", "funnel_status", "skill_list", "skill_show",
]


def _defs_copy():
    # deep copy WITHOUT the handler callables (copy.deepcopy chokes on some
    # closures/bound refs) — keep handler identity by shallow-copying each entry.
    out = []
    for t in TOOL_DEFS:
        e = dict(t)
        e["policy"] = copy.deepcopy(t.get("policy"))
        e["inputSchema"] = copy.deepcopy(t["inputSchema"])
        out.append(e)
    return out


# =============================================================== 1-10 integrity


def test_int_01_registry_validates_clean():
    validate_registry(TOOL_DEFS)  # must not raise


def test_int_02_every_tool_has_a_policy_dict():
    for t in TOOL_DEFS:
        assert isinstance(t.get("policy"), dict), f"{t['name']} has no policy dict"


def test_int_03_all_enum_values_legal():
    for t in TOOL_DEFS:
        pol = t["policy"]
        assert set(pol["effects"]) <= set(P.EFFECTS), t["name"]
        assert pol["network"] in P.NETWORK_LEVELS
        assert pol["spend"] in P.SPEND_LEVELS
        assert set(pol["gate"]) <= set(P.GATES), t["name"]
        assert pol["concurrency"] in P.CONCURRENCY
        assert pol["unattended"] in P.UNATTENDED_RULES


def test_int_04_spend_effect_iff_spend_not_never():
    for t in TOOL_DEFS:
        pol = t["policy"]
        assert (P.SPEND in pol["effects"]) == (pol["spend"] != P.NEVER), t["name"]


def test_int_05_network_effect_iff_network_not_never():
    for t in TOOL_DEFS:
        pol = t["policy"]
        assert (P.NETWORK in pol["effects"]) == (pol["network"] != P.NEVER), t["name"]


def test_int_06_write_truth_never_gate_none():
    for t in TOOL_DEFS:
        pol = t["policy"]
        if P.WRITE_TRUTH in pol["effects"]:
            assert pol["gate"] and P.GATE_NONE not in pol["gate"], t["name"]


def test_int_07_allow_with_cas_declares_cas_gate():
    for t in TOOL_DEFS:
        pol = t["policy"]
        if pol["unattended"] == P.ALLOW_WITH_CAS:
            assert P.GATE_CAS in pol["gate"], t["name"]
    # update_shot is the ONE tool that carries it
    us = next(t for t in TOOL_DEFS if t["name"] == "update_shot")
    assert us["policy"]["unattended"] == P.ALLOW_WITH_CAS
    assert P.GATE_CAS in us["policy"]["gate"]


def test_int_08_confirmed_proposal_only_iff_gate_confirmed():
    for t in TOOL_DEFS:
        pol = t["policy"]
        left = pol["unattended"] == P.CONFIRMED_PROPOSAL_ONLY
        right = P.GATE_CONFIRMED_PROPOSAL in pol["gate"]
        assert left == right, t["name"]
    de = next(t for t in TOOL_DEFS if t["name"] == "director_execute")
    assert de["policy"]["unattended"] == P.CONFIRMED_PROPOSAL_ONLY


def test_int_08b_spend_possible_declares_spend_gate():
    # §7.4: policy must describe the tool's REAL protections. Every tool that
    # can spend money reaches the engine's ask_before spend gate (§8.3) —
    # build/redo via run_build/redo_shot, director_execute via the confirmed
    # plan's engine run — so spend=POSSIBLE without GATE_SPEND_GATE is policy
    # under-describing the engine (metadata drift, not a missing guard).
    for t in TOOL_DEFS:
        pol = t["policy"]
        if pol["spend"] == P.POSSIBLE:
            assert P.GATE_SPEND_GATE in pol["gate"], t["name"]


def test_int_09_writes_are_logical_project_relative():
    for t in TOOL_DEFS:
        for w in t["policy"].get("writes", []):
            assert isinstance(w, str) and w
            assert not w.startswith("/"), f"{t['name']}: absolute write {w!r}"
            assert ".." not in w.split("/"), f"{t['name']}: traversal in {w!r}"


def test_int_10_no_callables_paths_or_time_in_policy_values():
    from pathlib import Path

    def _leaf_ok(v):
        return isinstance(v, (str, bool))

    for t in TOOL_DEFS:
        for key, val in t["policy"].items():
            if isinstance(val, list):
                assert all(_leaf_ok(x) for x in val), (t["name"], key)
            else:
                assert _leaf_ok(val), (t["name"], key)
            assert not callable(val)
            assert not isinstance(val, Path)


# ---- validate_policy rejects each malformed shape (the load-time gate bites)


def test_int_validate_rejects_bad_policies():
    good = {
        "effects": [P.READ], "network": P.NEVER, "spend": P.NEVER,
        "gate": [P.GATE_NONE], "concurrency": P.CONC_NONE,
        "unattended": P.ALLOW, "writes": [],
    }
    validate_policy("t", good)  # sanity: the good one passes

    def bad(**over):
        d = copy.deepcopy(good)
        d.update(over)
        return d

    cases = [
        {"effects": ["NONSENSE"]},                       # illegal effect
        {"network": "MAYBE"},                            # illegal network level
        {"effects": [P.SPEND], "spend": P.NEVER},        # SPEND effect w/o spend
        {"effects": [P.NETWORK], "network": P.NEVER},    # NETWORK effect w/o network
        {"effects": [P.WRITE_TRUTH], "gate": [P.GATE_NONE]},  # WRITE_TRUTH gate NONE
        {"unattended": P.ALLOW_WITH_CAS, "gate": [P.GATE_NONE]},  # ALLOW_WITH_CAS no CAS
        {"unattended": P.CONFIRMED_PROPOSAL_ONLY, "gate": [P.GATE_NONE]},  # mismatch
        {"writes": ["/etc/passwd"]},                     # absolute write path
        {"writes": ["../escape"]},                       # traversal
    ]
    for over in cases:
        with pytest.raises(PolicyError):
            validate_policy("t", bad(**over))

    # a Path / callable / number value is rejected (no impure values)
    with pytest.raises(PolicyError):
        validate_policy("t", bad(network=lambda: "x"))
    with pytest.raises(PolicyError):
        validate_policy("t", {**good, "extra_time": 12345})


def test_int_registry_rejects_a_tool_without_policy():
    defs = _defs_copy()
    defs[0].pop("policy")
    with pytest.raises(PolicyError):
        validate_registry(defs)


# ================================================= 11-20 surface manifest/digest


def test_surf_11_manifest_schema_and_resolver_single_source():
    surf = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE)
    man = surf.manifest()
    assert man["schema"] == "manju.agent-surface/v1"
    assert man["profile"] == P.COLLABORATIVE
    # the listed names come from the SAME surface the manifest does
    listed = surf.listed_names()
    assert [x["name"] for x in man["tools"] if x["listed"]] == listed


def test_surf_12_honesty_flags_in_every_profile():
    for prof in (P.COLLABORATIVE, P.UNATTENDED):
        man = resolve_agent_surface(TOOL_DEFS, prof).manifest()
        assert man["raw_filesystem_enforced"] is False
        assert man["project_can_override"] is False


def test_surf_13_manifest_projects_policy_fields():
    man = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).manifest()
    by = {t["name"]: t for t in man["tools"]}
    for key in ("effects", "network", "spend", "gate", "concurrency",
                "unattended", "listed"):
        assert key in by["update_shot"], key
    assert by["update_shot"]["unattended"] == P.ALLOW_WITH_CAS
    # description text / handler names are NOT projected into the manifest
    assert "description" not in by["status"]
    assert "handler" not in by["status"]


def test_surf_14_digest_is_stable():
    a = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest()
    b = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest()
    assert a == b and a.startswith("sha256:")


def test_surf_15_digest_excludes_description_text():
    base = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest()
    defs = _defs_copy()
    defs[0]["description"] = defs[0]["description"] + " (reworded)"
    assert resolve_agent_surface(defs, P.COLLABORATIVE).digest() == base


def test_surf_16_digest_excludes_handler_identity():
    base = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest()
    defs = _defs_copy()
    defs[0]["handler"] = lambda project, args: {"x": 1}  # different callable
    assert resolve_agent_surface(defs, P.COLLABORATIVE).digest() == base


def test_surf_17_digest_changes_with_profile():
    collab = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest()
    unatt = resolve_agent_surface(TOOL_DEFS, P.UNATTENDED).digest()
    assert collab != unatt


def test_surf_18_digest_changes_when_policy_semantics_change():
    base = resolve_agent_surface(TOOL_DEFS, P.UNATTENDED).digest()
    defs = _defs_copy()
    # flip redo from DENY to ALLOW — a real semantic change the digest must catch
    for e in defs:
        if e["name"] == "redo":
            e["policy"]["unattended"] = P.ALLOW
    assert resolve_agent_surface(defs, P.UNATTENDED).digest() != base


def test_surf_19_manifest_has_no_time_or_persistence():
    man = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).manifest()
    for forbidden in ("generated_at", "timestamp", "time", "now", "path", "cwd"):
        assert forbidden not in man


def test_surf_20_agent_surface_tool_is_read_runtime(tmp_project):
    a = next((t for t in TOOL_DEFS if t["name"] == "agent_surface"), None)
    assert a is not None, "agent_surface tool missing from the registry"
    pol = a["policy"]
    assert pol["effects"] == [P.READ_RUNTIME]
    assert pol["network"] == P.NEVER and pol["spend"] == P.NEVER
    assert pol["unattended"] == P.ALLOW
    # calling it returns THIS profile's manifest
    out = call_tool(tmp_project, "agent_surface", {}, profile=P.COLLABORATIVE)
    assert out["schema"] == "manju.agent-surface/v1"
    assert out["profile"] == P.COLLABORATIVE
    unatt = call_tool(tmp_project, "agent_surface", {}, profile=P.UNATTENDED)
    assert unatt["profile"] == P.UNATTENDED


# =============================================== 21-27 default collaborative compat


def test_compat_21_collaborative_lists_all_baseline_plus_agent_surface():
    names = [t["name"] for t in list_tools(P.COLLABORATIVE)]
    # every baseline tool present, order preserved
    assert [n for n in names if n in BASELINE_TOOL_NAMES] == BASELINE_TOOL_NAMES
    # the ONE additive delta
    assert "agent_surface" in names
    assert set(names) == set(BASELINE_TOOL_NAMES) | {"agent_surface"}


def test_compat_22_list_payload_shape_unchanged():
    for entry in list_tools(P.COLLABORATIVE):
        assert set(entry.keys()) == {"name", "description", "inputSchema"}


def test_compat_23_every_baseline_tool_admitted_in_collaborative():
    surf = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE)
    for name in BASELINE_TOOL_NAMES:
        d = surf.decide(name, {})
        assert d.admitted, f"{name} unexpectedly denied in collaborative"


def test_compat_24_collaborative_results_byte_identical(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # default (no profile) and explicit collaborative agree, and match direct
    default = call_tool(tmp_project, "list_shots", {})
    collab = call_tool(tmp_project, "list_shots", {}, profile=P.COLLABORATIVE)
    assert default == collab
    st = call_tool(tmp_project, "status", {}, profile=P.COLLABORATIVE)
    assert st["shots_total"] == 1


def test_compat_25_list_tools_defaults_to_collaborative():
    assert list_tools() == list_tools(P.COLLABORATIVE)


def test_compat_26_collaborative_admits_redo_and_confirm(tmp_project, add_shot):
    # tools DENY-hidden in unattended are fully available in collaborative
    surf = resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE)
    assert surf.decide("redo", {"shot_id": "S001"}).admitted
    assert surf.decide("director_confirm", {"id": "prop_0001"}).admitted
    assert "redo" in surf.listed_names()
    assert "director_confirm" in surf.listed_names()


def test_compat_27_baseline_descriptions_and_schemas_preserved():
    # the collaborative surface never rewrote any baseline tool's public shape
    by = {t["name"]: t for t in list_tools(P.COLLABORATIVE)}
    reg = {t["name"]: t for t in TOOL_DEFS}
    for name in BASELINE_TOOL_NAMES:
        assert by[name]["description"] == reg[name]["description"]
        assert by[name]["inputSchema"] == reg[name]["inputSchema"]
