"""DR03B — the explicit-DAG diagnostics core + Manju's read-only derivation.

Red-first: this file imports ``manju.build.graphdiag`` at module load, so its
first run (before the module existed) was a collection-time ``ModuleNotFoundError``
(the accepted RED for a brand-new pure-algorithm module). The behavioral pins
below are genuine assertions over deterministic graph inputs, including synthetic
multi-parent graphs the contract's semantics require but Manju itself never
builds.

Numbering follows the DR03B contract test list (1–16); the real-build
characterization c1 lives in tests/test_dr03b_characterization.py.
"""

from __future__ import annotations

import copy
import json

import pytest
from typer.testing import CliRunner

from manju.build.graphdiag import (  # noqa: E402 — import IS the WP1 gate (red-first)
    SCHEMA,
    compute_ready,
    derive_build_graph,
    diagnose,
    diagnose_project,
    validate_graph,
)

runner = CliRunner()


def _node(n):
    """Terse (id[, kind[, handoff_from]]) → node dict."""
    d = {"node_id": n[0], "kind": (n[1] if len(n) > 1 else "task")}
    if len(n) > 2:
        d["handoff_from"] = n[2]
    return d


def _edge(e):
    """Terse (from, to[, optional]) → edge dict."""
    d = {"from": e[0], "to": e[1]}
    if len(e) > 2 and e[2]:
        d["optional"] = True
    return d


def _g(nodes, edges):
    """Build a graph from terse node/edge tuples."""
    return {"nodes": [_node(n) for n in nodes], "edges": [_edge(e) for e in edges]}


def _by_id(doc):
    return {n["node_id"]: n for n in doc["nodes"]}


# ---------------------------------------------------------------- 1: cycles


def test_01_cycle_path_stable_regardless_of_insertion_order():
    g1 = _g([("A",), ("B",), ("C",)], [("A", "B"), ("B", "C"), ("C", "A")])
    # a wholly different insertion order for both nodes and edges
    g2 = _g([("C",), ("A",), ("B",)], [("C", "A"), ("A", "B"), ("B", "C")])
    c1 = validate_graph(g1)["cycles"]
    c2 = validate_graph(g2)["cycles"]
    assert c1 == c2 == [["A", "B", "C"]], (c1, c2)
    # the CYCLE issue rotates to the lexicographically-smallest start
    cyc_issue = next(i for i in validate_graph(g1)["issues"] if i["code"] == "CYCLE")
    assert cyc_issue["node_id"] == "A"
    assert cyc_issue["detail"]["cycle"] == ["A", "B", "C"]
    # every in-cycle node is NEVER_READY
    nr = {i["node_id"] for i in validate_graph(g1)["issues"] if i["code"] == "NEVER_READY"}
    assert nr == {"A", "B", "C"}


# ------------------------------------------------ 2: unknown/duplicate/self


def test_02_unknown_duplicate_and_self_edges_flagged():
    g = {
        "nodes": [{"node_id": "A"}, {"node_id": "A"}, {"node_id": "B"}],  # dup A
        "edges": [
            {"from": "A", "to": "A"},          # self
            {"from": "A", "to": "B"},
            {"from": "A", "to": "B"},          # duplicate
            {"from": "A", "to": "ghost"},      # dangling / unknown node
        ],
    }
    codes = {(i["code"], i["node_id"]) for i in validate_graph(g)["issues"]}
    assert ("DUPLICATE_NODE", "A") in codes
    assert ("SELF_EDGE", "A") in codes
    assert ("DUPLICATE_EDGE", "A") in codes
    assert ("UNKNOWN_NODE", "ghost") in codes
    # a self-edge makes its node never ready
    assert ("NEVER_READY", "A") in codes


# --------------------------------------------- 3: FAILED blocks successor


def test_03_failed_required_predecessor_blocks_successor():
    g = _g([("A",), ("B",)], [("A", "B")])
    states = {"A": {"status": "FAILED", "root_cause_id": "F-1"}, "B": {"status": "PENDING"}}
    ready = compute_ready(g, states)
    assert ready["B"]["ready"] is False
    assert ready["B"]["effective"] == "BLOCKED"
    assert ready["B"]["blocked_by"][0]["node_id"] == "A"
    assert ready["B"]["blocked_by"][0]["root_cause_id"] == "F-1"
    doc = diagnose(g, states)
    assert ("BLOCKED_BY_FAILED_ANCESTOR", "B") in {(i["code"], i["node_id"]) for i in doc["issues"]}
    assert doc["summary"]["failed"] == 1
    assert doc["summary"]["blocked"] == 2  # A (own failure) + B (downstream)


# ------------------------------- 4: CANCELED / WAITING / UNKNOWN don't unlock


@pytest.mark.parametrize(
    "status,code,eff",
    [
        ("CANCELED", "BLOCKED_BY_CANCELED_ANCESTOR", "BLOCKED"),
        ("UNKNOWN", "UNKNOWN_ANCESTOR", "BLOCKED"),
        ("WAITING_USER", "WAITING_ANCESTOR", "WAITING"),
    ],
)
def test_04_canceled_waiting_unknown_do_not_unlock(status, code, eff):
    g = _g([("A",), ("B",)], [("A", "B")])
    states = {"A": {"status": status}, "B": {"status": "PENDING"}}
    ready = compute_ready(g, states)
    assert ready["B"]["ready"] is False
    assert ready["B"]["effective"] == eff
    doc = diagnose(g, states)
    assert (code, "B") in {(i["code"], i["node_id"]) for i in doc["issues"]}


# ------------------------------------- 5: valid vs invalid cache hit


def test_05_valid_cache_hit_satisfies_invalid_does_not():
    g = _g([("A",), ("B",)], [("A", "B")])
    ok = compute_ready(g, {"A": {"status": "SKIPPED_CACHE_HIT", "cache_valid": True},
                           "B": {"status": "PENDING"}})
    assert ok["B"]["ready"] is True and ok["B"]["effective"] == "PENDING"

    bad_states = {"A": {"status": "SKIPPED_CACHE_HIT", "cache_valid": False},
                  "B": {"status": "PENDING"}}
    bad = compute_ready(g, bad_states)
    assert bad["B"]["ready"] is False, "an unverified cache hit must NOT satisfy"
    doc = diagnose(g, bad_states)
    assert ("STALE_CACHE_HIT", "A") in {(i["code"], i["node_id"]) for i in doc["issues"]}


# --------------------------------------------- 6: optional edge never blocks


def test_06_optional_edge_does_not_block():
    g = _g([("A",), ("B",)], [("A", "B", True)])  # optional edge
    states = {"A": {"status": "FAILED"}, "B": {"status": "PENDING"}}
    ready = compute_ready(g, states)
    assert ready["B"]["ready"] is True, "an optional predecessor's failure must not block"
    assert ready["B"]["blocked_by"] == []
    doc = _by_id(diagnose(g, states))
    assert doc["B"]["optional_predecessors"] == ["A"]
    assert doc["B"]["required_predecessors"] == []


# -------------------------------------- 7: multi-hop root-cause path


def test_07_blocked_ancestry_root_cause_path_multi_hop():
    # A fails → C blocked via B, path [A, B, C]
    g = _g([("A",), ("B",), ("C",)], [("A", "B"), ("B", "C")])
    states = {"A": {"status": "FAILED", "root_cause_id": "F-42"},
              "B": {"status": "PENDING"}, "C": {"status": "PENDING"}}
    ready = compute_ready(g, states)
    entry = ready["C"]["blocked_by"][0]
    assert entry["node_id"] == "A"
    assert entry["root_cause_id"] == "F-42"
    assert entry["path"] == ["A", "B", "C"]


# ------------------------------------------ 8: recompute after retry


def test_08_state_flip_to_succeeded_makes_successor_ready():
    g = _g([("A",), ("B",)], [("A", "B")])
    blocked = compute_ready(g, {"A": {"status": "FAILED"}, "B": {"status": "PENDING"}})
    assert blocked["B"]["ready"] is False
    # retry succeeds → the SAME graph, new states → B becomes ready
    retried = compute_ready(g, {"A": {"status": "SUCCEEDED"}, "B": {"status": "PENDING"}})
    assert retried["B"]["ready"] is True
    assert retried["B"]["blocked_by"] == []


# ---------------------------------- 9: multi-parent handoff


def test_09_multiparent_without_handoff_flagged_and_unknown_parent():
    # C has two required parents and no handoff_from → HANDOFF_UNSPECIFIED
    g = _g([("A",), ("B",), ("C",)], [("A", "C"), ("B", "C")])
    codes = {(i["code"], i["node_id"]) for i in validate_graph(g)["issues"]}
    assert ("HANDOFF_UNSPECIFIED", "C") in codes

    # with an explicit handoff_from that names a real parent → no HANDOFF issue
    g2 = _g([("A",), ("B",), ("C", "task", "A")], [("A", "C"), ("B", "C")])
    codes2 = {i["code"] for i in validate_graph(g2)["issues"]}
    assert "HANDOFF_UNSPECIFIED" not in codes2 and "HANDOFF_UNKNOWN_PARENT" not in codes2

    # a handoff_from that names a NON-parent → HANDOFF_UNKNOWN_PARENT. The join
    # never silently assumes predecessors[0]=A: the bogus handoff is flagged
    # loudly instead. (HANDOFF_UNSPECIFIED does NOT also fire — a handoff WAS
    # specified, it just names a stranger.)
    g3 = _g([("A",), ("B",), ("C", "task", "Z")], [("A", "C"), ("B", "C")])
    codes3 = {(i["code"], i["node_id"]) for i in validate_graph(g3)["issues"]}
    assert ("HANDOFF_UNKNOWN_PARENT", "C") in codes3
    assert ("HANDOFF_UNSPECIFIED", "C") not in codes3


# ------------------------------- 10: predecessor-order permutation is inert


def test_10_predecessor_order_permutation_changes_nothing():
    nodes = [("A",), ("B",), ("C",), ("D",)]
    g1 = _g(nodes, [("A", "D"), ("B", "D"), ("C", "D")])
    g2 = _g(list(reversed(nodes)), [("C", "D"), ("A", "D"), ("B", "D")])
    states = {"A": {"status": "SUCCEEDED"}, "B": {"status": "FAILED", "root_cause_id": "F-9"},
              "C": {"status": "SUCCEEDED"}, "D": {"status": "PENDING"}}
    assert diagnose(g1, states) == diagnose(g2, states)
    # D is blocked by B irrespective of B's position among the parents
    d = _by_id(diagnose(g1, states))["D"]
    assert [b["node_id"] for b in d["blocked_by"]] == ["B"]


# ---------------------------------------- 11: inputs never mutated


def test_11_functions_do_not_mutate_inputs():
    g = _g([("A",), ("B",), ("C",)], [("A", "B"), ("B", "C"), ("C", "A")])
    states = {"A": {"status": "FAILED"}, "B": {"status": "PENDING"}, "C": {"status": "PENDING"}}
    g_before, s_before = copy.deepcopy(g), copy.deepcopy(states)
    validate_graph(g)
    compute_ready(g, states)
    diagnose(g, states)
    assert g == g_before, "graph mutated"
    assert states == s_before, "states mutated"


# --------------------------------------- 12: dict insertion order irrelevant


def test_12_dict_insertion_order_irrelevant():
    g = _g([("A",), ("B",), ("C",)], [("A", "C"), ("B", "C")])
    s1 = {"A": {"status": "SUCCEEDED"}, "B": {"status": "SUCCEEDED"}, "C": {"status": "PENDING"}}
    s2 = {"C": {"status": "PENDING"}, "B": {"status": "SUCCEEDED"}, "A": {"status": "SUCCEEDED"}}
    assert diagnose(g, s1) == diagnose(g, s2)
    assert diagnose(g, s1)["graph_digest"] == diagnose(g, s2)["graph_digest"]


# ------------------------------------------- 13: canonical JSON stable


def test_13_canonical_json_byte_stable_across_runs():
    g = _g([("A",), ("B",), ("C",)], [("A", "B"), ("B", "C"), ("A", "C")])
    states = {"A": {"status": "SUCCEEDED"}, "B": {"status": "RUNNING"},
              "C": {"status": "PENDING"}}
    a = json.dumps(diagnose(g, states), sort_keys=True, ensure_ascii=False)
    b = json.dumps(diagnose(g, states), sort_keys=True, ensure_ascii=False)
    assert a == b


# ------------------------------ 14: explain --graph carries the doc


@pytest.fixture
def two_shot_project(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    return tmp_project


def test_14_explain_graph_json_and_human(two_shot_project, monkeypatch):
    monkeypatch.chdir(two_shot_project.root)
    from manju.cli import app

    result = runner.invoke(app, ["explain", "--graph", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "graph" in payload, "explain --graph --json must append the diagnostics doc"
    graphdoc = payload["graph"]
    assert graphdoc["schema"] == SCHEMA
    assert {"nodes", "summary", "issues", "graph_digest"} <= set(graphdoc)
    assert graphdoc["summary"]["node_count"] >= 3  # 2 gens + compile + …

    # human mode smoke: it prints a graph section, never crashes
    human = runner.invoke(app, ["explain", "--graph"])
    assert human.exit_code == 0, human.output
    assert "graph" in human.output.lower() or "依赖" in human.output



# --------------------- 15: derive_build_graph on a real project


def test_15_derive_build_graph_models_only_real_dependencies(tmp_project, add_shot):
    """Shots→compile edges exist; NO inter-shot edges; NOTHING derived from a
    shared scene/character. A failed-generation shot maps to FAILED with the
    failure id as root cause, and render shows BLOCKED_BY_FAILED_ANCESTOR."""
    from manju.core.failures import Failure, record_failure

    # two shots that SHARE scene (convenience_store) and character (linxia)
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    rec = record_failure(tmp_project, Failure(
        step="generate", subject="S002", cause="所有供应商都失败,该镜头没有可用镜头",
        level="error"))

    graph, states = derive_build_graph(tmp_project)
    ids = {n["node_id"] for n in graph["nodes"]}
    assert {"gen:S001", "gen:S002", "compile:timeline", "render:final"} <= ids

    # every indexed shot's gen → compile (required)
    req_edges = {(e["from"], e["to"]) for e in graph["edges"] if not e.get("optional")}
    assert ("gen:S001", "compile:timeline") in req_edges
    assert ("gen:S002", "compile:timeline") in req_edges

    # FORBIDDEN-INFERENCE PIN: two shots sharing scene+character → ZERO edge
    # between them (never from scenes/characters/prompts/filenames)
    inter = [e for e in graph["edges"]
             if {e["from"], e["to"]} == {"gen:S001", "gen:S002"}]
    assert inter == [], "a shared scene/character must NEVER create an edge"

    # the failed shot maps to FAILED with the failure id as root cause
    assert states["gen:S002"]["status"] == "FAILED"
    assert states["gen:S002"]["root_cause_id"] == rec["id"]
    # a healthy sibling is NOT dragged into FAILED
    assert states["gen:S001"]["status"] != "FAILED"

    doc = diagnose(graph, states)
    render = _by_id(doc)["render:final"]
    assert render["effective"] == "BLOCKED"
    blk = {(i["code"], i["node_id"]) for i in doc["issues"]}
    assert ("BLOCKED_BY_FAILED_ANCESTOR", "render:final") in blk
    # the root cause path threads gen:S002 → compile → render
    rc = render["blocked_by"][0]
    assert rc["node_id"] == "gen:S002"
    assert rc["path"] == ["gen:S002", "compile:timeline", "render:final"]


def test_15b_derive_is_read_only(tmp_project, add_shot):
    """derive_build_graph writes NOTHING (it is a diagnostic view)."""
    import hashlib

    def tree_hash(root):
        h = hashlib.sha256()
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if rel.startswith(".manju/") or rel.startswith(".git/"):
                continue
            h.update(rel.encode()); h.update(b"\0"); h.update(p.read_bytes()); h.update(b"\0")
        return h.hexdigest()

    add_shot(tmp_project, "S001")
    before = tree_hash(tmp_project.root)
    derive_build_graph(tmp_project)
    diagnose_project(tmp_project)
    assert tree_hash(tmp_project.root) == before, "derivation must not write"


# ----------------- 16: scheduling hints N/A (NOT_IMPLEMENTED)


def test_16_diagnose_carries_no_scheduling_directive(tmp_project, add_shot):
    """WP3 SchedulingHints is NOT_IMPLEMENTED: the diagnostics document must
    carry no ordering/scheduling directive, and estimated_ms (schema-only) must
    never influence the output."""
    add_shot(tmp_project, "S001")
    graph, states = derive_build_graph(tmp_project)
    doc = diagnose(graph, states)
    # no scheduling vocabulary anywhere at the top level
    for k in ("schedule", "order", "hints", "next", "plan", "sequence"):
        assert k not in doc
    # setting estimated_ms on every node cannot change any diagnostic
    doc_no_est = diagnose(graph, states)
    g2 = copy.deepcopy(graph)
    for n in g2["nodes"]:
        n["estimated_ms"] = 12345
    doc_with_est = diagnose(g2, states)
    for d in (doc_no_est, doc_with_est):
        d_cmp = json.loads(json.dumps(d))
    # compare everything except that est is echoed nowhere (it isn't carried)
    assert doc_no_est["summary"] == doc_with_est["summary"]
    assert doc_no_est["issues"] == doc_with_est["issues"]
    assert [n["node_id"] for n in doc_no_est["nodes"]] == \
        [n["node_id"] for n in doc_with_est["nodes"]]


def test_schema_constant_is_v1():
    assert SCHEMA == "manju.graph-diagnostics/v1"


def test_merge_policy_satisfies_handoff_rule_and_real_projects_are_clean(tmp_project, add_shot):
    """The contract accepts `handoff_from、merge policy 或等价字段`: a
    multi-required-parent node declaring an explicit merge_policy is NOT
    unspecified. Manju's own compile node declares merge_policy=index_order
    (shots/index.yaml IS the assembly contract), so a normal multi-shot
    project must never carry permanent HANDOFF_UNSPECIFIED noise."""
    graph = {
        "nodes": [{"node_id": "A"}, {"node_id": "B"},
                  {"node_id": "C", "merge_policy": "index_order"}],
        "edges": [{"from": "A", "to": "C"}, {"from": "B", "to": "C"}],
    }
    doc = diagnose(graph, {"A": {"status": "SUCCEEDED"}, "B": {"status": "SUCCEEDED"},
                           "C": {"status": "PENDING"}})
    codes = {(i["code"], i["node_id"]) for i in doc["issues"]}
    assert ("HANDOFF_UNSPECIFIED", "C") not in codes

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    project_doc = diagnose_project(tmp_project)
    assert not any(i["code"] == "HANDOFF_UNSPECIFIED" for i in project_doc["issues"]), \
        "a normal multi-shot project must not carry permanent handoff noise"
