"""Explicit-DAG diagnostics (DR03B) — a pure graph-diagnostics core plus a
read-only derivation of Manju's *actually-modeled* build dependencies.

Two layers live here, kept strictly apart:

**A. Pure core** (no manju imports beyond the canonical hasher). It reasons over
an EXPLICIT graph — ``{"nodes": [...], "edges": [...]}`` — and a per-node
``states`` map, implementing the contract's truth table, multi-parent handoff
rules, cycle detection and root-cause blocking. It never touches a DB, clock,
network or randomness; it never mutates its inputs; it never depends on dict
insertion order (every list is sorted by a stable key); its output is
canonical-JSON stable. It is exercised over synthetic multi-parent graphs in
the tests — the semantics do NOT assume Manju's own shapes.

**B. Derivation** (impure boundary, read-only). :func:`derive_build_graph`
projects Manju's *actually-modeled* dependencies into that explicit form. Manju
has **no explicit edge-based dependency graph** — ``build/graph.py`` is a PHASED
pipeline with deliberate per-shot failure isolation, no inter-shot edges, no
task-dependency edges, no scheduler. The derived graph is therefore a diagnostic
VIEW, never a second build graph and never a scheduling truth source. It invents
NO edges: nothing is ever derived from scene order, shared characters, prompt
similarity, an LLM/VLM, filesystem proximity, or creative-text causality.

Node states (module constants, the contract's vocabulary)::

    SUCCEEDED  SKIPPED_CACHE_HIT  FAILED  CANCELED
    WAITING_USER  RUNNING  PENDING  UNKNOWN

Truth table (which states satisfy a REQUIRED predecessor edge):

    ================  =========================================================
    state             effect on the successor
    ================  =========================================================
    SUCCEEDED         satisfies
    SKIPPED_CACHE_HIT satisfies ONLY when cache_valid is true; otherwise it is a
                      STALE_CACHE_HIT (does NOT satisfy — treated as PENDING +
                      an issue: a cache hit must be key/output-verified)
    FAILED            successor BLOCKED (root cause = this node)
    CANCELED          successor BLOCKED
    UNKNOWN           successor BLOCKED / INVALID
    WAITING_USER      successor WAITING
    RUNNING / PENDING successor PENDING
    ================  =========================================================

An OPTIONAL edge never blocks, whatever the predecessor's state.

Multi-parent handoff must be explicit: a node with >1 REQUIRED predecessors and
no ``handoff_from`` is flagged ``HANDOFF_UNSPECIFIED`` (conservative — always
flag; downstream views may filter). ``predecessors[0]`` is never assumed, so a
predecessor-order permutation changes no output.

--------------------------------------------------------------------------------
Derived edge table — every edge cites the code that MODELS it (§4.2 allow-list).
NO edge exists that is not on this table.
--------------------------------------------------------------------------------

    edge                                     req?   modeled by (file:line)
    ---------------------------------------  -----  ---------------------------
    gen:<shot>  -> compile:timeline          REQ    timeline/compiler.py
                                                    gather_compile_input:716-744
                                                    (a not-usable indexed shot ->
                                                    problems) + :802-805 (raises
                                                    CompileError) — the final
                                                    compile needs EVERY indexed
                                                    shot's picture. build/graph.py
                                                    :1235-1247 turns that into
                                                    ok=False (no render).
    voice:<shot> -> compile:timeline         OPT    voice is advisory (§4.3):
                                                    build/voice.py:22-27 (stale/
                                                    manual voices flag-only);
                                                    compiler _resolve_duration_ms
                                                    :190-202 falls back to take/
                                                    default duration when a voice
                                                    is absent — a missing voice
                                                    never fails the compile.
    compile:timeline -> captions             REQ    build/graph.py:1272-1286
                                                    export_captions(project,
                                                    timeline) — captions are
                                                    derived FROM the timeline.
                                                    (emitted only when
                                                    rules.captions.enabled)
    captions -> render:final                 OPT    build/graph.py:1371 /1296
                                                    render_timeline(..., ass_file=
                                                    ass_path): the burn is used
                                                    when present, but render runs
                                                    without captions too.
    compile:timeline -> render:final         REQ    build/graph.py:1356-1372
                                                    render_timeline(project,
                                                    timeline, ...) composites the
                                                    compiled timeline.
    compile:timeline -> export:<kind>        REQ    build/graph.py:1443-1483
                                                    export_otio/jianying(project,
                                                    timeline) read the TIMELINE,
                                                    not the render — confirmed by
                                                    target=exports SKIPPING render
                                                    (graph.py:1410-1421 comment)
                                                    and exportstatus.py:562-589
                                                    (otio freshness vs
                                                    timeline.json). So exports
                                                    hang off compile, never render.

There is deliberately NO gen:<a> -> gen:<b> edge (shots are independent, per-shot
failure isolation) and NO edge from scenes/characters/prompts/filenames.

--------------------------------------------------------------------------------
State-mapping table — evaluate_all/evaluate_voice + reports/failures.jsonl into
the node vocabulary. The mapping is a VIEW; it never drives execution.
--------------------------------------------------------------------------------

    subject / source state         -> node status         rationale
    -----------------------------  ---------------------  ---------------------
    shot FRESH                     SKIPPED_CACHE_HIT      stale.py:8 "cache hit,
                                   (cache_valid=true)     skip" — spec_hash match
                                                          + media present IS the
                                                          valid-cache semantics.
    shot MANUAL                    SUCCEEDED              hand-placed selected
                                                          take, never auto-
                                                          invalidated (§4.3).
    shot STALE                     PENDING                usable on the timeline
                                                          (§4.3, selection stands)
                                                          but out-of-date vs the
                                                          current spec -> the VIEW
                                                          reports currency, not
                                                          buildability (regenerable)
    shot MISSING + recent          FAILED                 generation produced
      generate error in            (root_cause_id =       nothing AND a level=error
      failures.jsonl               that failure id)       generate record names the
                                                          shot (c1 pin).
    shot MISSING (no failure)      PENDING                a gap the build will fill.
    shot NEEDS_SELECTION           PENDING                build auto-selects the
                                                          newest usable take
                                                          (graph.py:1123-1146), so
                                                          it is pending-resolution,
                                                          not waiting-on-human.
    shot BROKEN                    FAILED                 selected take's media is
                                                          gone / sidecar illegal
                                                          (stale.py:74-89) — also a
                                                          hard check error.
    voice FRESH                    SKIPPED_CACHE_HIT      same cache semantics.
    voice MANUAL                   SUCCEEDED              hand-dropped, §4.3.
    voice STALE                    PENDING                advisory, regenerable.
    voice MISSING (+failure)       PENDING (FAILED)       will synthesize on build;
                                                          FAILED only with a recent
                                                          voice error record.
    voice NOT_NEEDED               (no node emitted)      shot has no dialogue.
    compile fingerprint match      SKIPPED_CACHE_HIT      meta.compiled_from ==
                                   (cache_valid=true)     recompiled fingerprint
                                                          (explain.py:70). else
                                                          PENDING (regenerable).
    render final key match         SKIPPED_CACHE_HIT      final key sidecar ==
                                   (cache_valid=true)     final_content_key
                                                          (explain.py:88-107). else
                                                          PENDING.
    captions files present +       SKIPPED_CACHE_HIT      derived FROM a cached
      compile cached               (cache_valid=true)     timeline. else PENDING.
    export UP_TO_DATE/VERIFIED     SKIPPED_CACHE_HIT      exportstatus.py freshness
    export PROBLEMATIC             FAILED                 (reused — no second
    export other                   PENDING                staleness path invented).

ask_before-gated pending spend -> WAITING_USER is NOT emitted: the gate lives at
run_build (graph.py:806-821), not on any per-node file state, so it is not
cheaply detectable per node — omitted by design (documented, not invented).

WP3 SchedulingHints: NOT IMPLEMENTED (REJECTED_UNLESS_BENCHMARKED). ``estimated_ms``
is carried on nodes for schema-completeness but is never read by any diagnostic —
:func:`diagnose` emits NO scheduling directive and :func:`derive_build_graph`
never writes.
"""

from __future__ import annotations

from typing import Any

from ..core.hashing import hash_value  # canonical, key-order-independent hasher

SCHEMA = "manju.graph-diagnostics/v1"

# ---------------------------------------------------------------- node states
SUCCEEDED = "SUCCEEDED"
SKIPPED_CACHE_HIT = "SKIPPED_CACHE_HIT"
FAILED = "FAILED"
CANCELED = "CANCELED"
WAITING_USER = "WAITING_USER"
RUNNING = "RUNNING"
PENDING = "PENDING"
UNKNOWN = "UNKNOWN"

NODE_STATES = frozenset(
    {SUCCEEDED, SKIPPED_CACHE_HIT, FAILED, CANCELED, WAITING_USER, RUNNING, PENDING, UNKNOWN}
)

# Effective per-node dispositions (the propagated readiness classes).
_SATISFIED = "SATISFIED"
_D_PENDING = "PENDING"
_D_WAITING = "WAITING"
_BLOCKED = "BLOCKED"
_SEVERITY = {_SATISFIED: 0, _D_PENDING: 1, _D_WAITING: 2, _BLOCKED: 3}
_BY_SEVERITY = {v: k for k, v in _SEVERITY.items()}


# ============================================================== normalization


def _node_id(node: Any) -> str:
    return str(node.get("node_id")) if isinstance(node, dict) else str(node)


def _normalize_graph(graph: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Return (nodes, edges) as fresh, sorted, defensively-copied lists — never
    mutating the caller's graph and never depending on insertion order.

    Nodes sort by node_id; edges sort by (from, to, optional). Duplicate node
    entries are preserved here (so :func:`validate_graph` can report them) but
    de-duplicated at adjacency time."""
    raw_nodes = list(graph.get("nodes") or [])
    raw_edges = list(graph.get("edges") or [])
    nodes = sorted(
        (
            {
                "node_id": _node_id(n),
                "kind": (n.get("kind") if isinstance(n, dict) else None),
                "handoff_from": (n.get("handoff_from") if isinstance(n, dict) else None),
                "merge_policy": (n.get("merge_policy") if isinstance(n, dict) else None),
                "estimated_ms": (n.get("estimated_ms") if isinstance(n, dict) else None),
            }
            for n in raw_nodes
        ),
        key=lambda n: n["node_id"],
    )
    edges = sorted(
        (
            {
                "from": str(e.get("from")),
                "to": str(e.get("to")),
                "optional": bool(e.get("optional", False)),
            }
            for e in raw_edges
            if isinstance(e, dict)
        ),
        key=lambda e: (e["from"], e["to"], e["optional"]),
    )
    return nodes, edges


def _adjacency(nodes: list[dict], edges: list[dict]) -> dict[str, Any]:
    """Build the working adjacency, dropping structurally-invalid edges (self,
    duplicate, dangling) from the *readiness* computation while leaving the raw
    edges intact for issue reporting. Neighbor lists are sorted for determinism.
    """
    node_ids = []
    seen: set[str] = set()
    for n in nodes:
        nid = n["node_id"]
        if nid not in seen:
            seen.add(nid)
            node_ids.append(nid)
    handoff = {n["node_id"]: n.get("handoff_from") for n in nodes}

    req_pred: dict[str, set[str]] = {nid: set() for nid in node_ids}
    opt_pred: dict[str, set[str]] = {nid: set() for nid in node_ids}
    req_succ: dict[str, set[str]] = {nid: set() for nid in node_ids}
    seen_edge: set[tuple[str, str, bool]] = set()
    for e in edges:
        f, t, opt = e["from"], e["to"], e["optional"]
        if f == t:
            continue  # self-edge: reported, never a real dependency
        if f not in seen or t not in seen:
            continue  # dangling: reported as UNKNOWN_NODE, dropped here
        key = (f, t, opt)
        if key in seen_edge:
            continue  # duplicate: reported, collapsed here
        seen_edge.add(key)
        if opt:
            opt_pred[t].add(f)
        else:
            req_pred[t].add(f)
            req_succ[f].add(t)
    return {
        "node_ids": node_ids,
        "handoff": handoff,
        "req_pred": {k: sorted(v) for k, v in req_pred.items()},
        "opt_pred": {k: sorted(v) for k, v in opt_pred.items()},
        "req_succ": {k: sorted(v) for k, v in req_succ.items()},
    }


# ==================================================================== cycles


def _find_cycles(node_ids: list[str], req_succ: dict[str, list[str]]) -> tuple[list[list[str]], set[str]]:
    """All elementary cycles over REQUIRED edges, each canonicalized to start at
    its lexicographically-smallest node, plus the set of in-cycle nodes.

    Deterministic and insertion-order-independent: neighbor lists are sorted, we
    start a DFS from every node in sorted order, and prune to neighbors strictly
    greater than the start node so each cycle is discovered exactly once from its
    minimum node (never as a rotation)."""
    cycles: set[tuple[str, ...]] = set()

    def dfs(start: str, current: str, path: list[str], visited: set[str]) -> None:
        for nxt in req_succ.get(current, []):
            if nxt == start:
                cycles.add(tuple(path))  # path already begins at `start` (the min)
            elif nxt > start and nxt not in visited:
                dfs(start, nxt, path + [nxt], visited | {nxt})

    for s in sorted(node_ids):
        dfs(s, s, [s], {s})

    canon = sorted([list(c) for c in cycles])
    in_cycle: set[str] = set()
    for c in canon:
        in_cycle.update(c)
    return canon, in_cycle


def _shortest_path(adj_succ: dict[str, list[str]], src: str, dst: str) -> list[str] | None:
    """Lexicographically-smallest shortest path src->dst over ``adj_succ`` (a BFS
    that keeps the best (length, path) per node). Small graphs — exhaustive is
    fine and keeps the path stable under insertion-order permutation."""
    from collections import deque

    best: dict[str, list[str]] = {src: [src]}
    q: deque[str] = deque([src])
    while q:
        cur = q.popleft()
        for nxt in adj_succ.get(cur, []):
            cand = best[cur] + [nxt]
            prev = best.get(nxt)
            if prev is None or (len(cand), cand) < (len(prev), prev):
                best[nxt] = cand
                q.append(nxt)
    return best.get(dst)


# ================================================================= truth table


def _own_disposition(state: dict[str, Any] | None) -> str:
    """A single node's disposition from its OWN state, per the truth table."""
    status = (state or {}).get("status", UNKNOWN)
    if status == SUCCEEDED:
        return _SATISFIED
    if status == SKIPPED_CACHE_HIT:
        return _SATISFIED if bool((state or {}).get("cache_valid")) else _D_PENDING
    if status in (FAILED, CANCELED, UNKNOWN):
        return _BLOCKED
    if status == WAITING_USER:
        return _D_WAITING
    if status in (RUNNING, PENDING):
        return _D_PENDING
    return _BLOCKED  # unrecognized status is conservatively invalid


def compute_ready(graph: dict[str, Any], states: dict[str, Any]) -> dict[str, Any]:
    """Per-node readiness / blocking per the truth table. Pure; inputs untouched.

    Returns ``{node_id: {"ready": bool, "effective": <disposition>,
    "blocked_by": [{node_id, status, root_cause_id, path}]}}``. ``ready`` means
    every REQUIRED predecessor is effectively SATISFIED (so the node may run);
    ``effective`` folds the node's own state with its required ancestors;
    ``blocked_by`` lists the BLOCKED origins (FAILED/CANCELED/UNKNOWN) reachable
    via required edges, each with a stable root-cause path."""
    nodes, edges = _normalize_graph(graph)
    adj = _adjacency(nodes, edges)
    node_ids = adj["node_ids"]
    req_pred = adj["req_pred"]
    req_succ = adj["req_succ"]
    _, in_cycle = _find_cycles(node_ids, req_succ)

    own = {nid: _own_disposition(states.get(nid)) for nid in node_ids}

    # Effective disposition, memoized over the DAG (in-cycle nodes are BLOCKED
    # sinks so the recursion always terminates).
    eff_cache: dict[str, str] = {}

    def effective(nid: str, stack: frozenset[str]) -> str:
        if nid in eff_cache:
            return eff_cache[nid]
        if nid in in_cycle or nid in stack:
            eff_cache[nid] = _BLOCKED
            return _BLOCKED
        sev = _SEVERITY[own[nid]]
        for p in req_pred.get(nid, []):
            sev = max(sev, _SEVERITY[effective(p, stack | {nid})])
        disp = _BY_SEVERITY[sev]
        eff_cache[nid] = disp
        return disp

    for nid in node_ids:
        effective(nid, frozenset())

    # Blocking origins = nodes whose OWN disposition is BLOCKED. Forward-propagate
    # each over required edges to attribute a root cause + a stable path.
    origins = [nid for nid in node_ids if own[nid] == _BLOCKED and nid not in in_cycle]
    blocked_by: dict[str, list[dict]] = {nid: [] for nid in node_ids}
    for origin in origins:
        ostate = states.get(origin) or {}
        entry_status = ostate.get("status", UNKNOWN)
        rc = ostate.get("root_cause_id")
        # every node reachable from `origin` over required edges is blocked by it
        stack = [origin]
        reached: set[str] = set()
        while stack:
            cur = stack.pop()
            for nxt in req_succ.get(cur, []):
                if nxt not in reached:
                    reached.add(nxt)
                    stack.append(nxt)
        for nid in sorted(reached):
            path = _shortest_path(req_succ, origin, nid)
            blocked_by[nid].append(
                {
                    "node_id": origin,
                    "status": entry_status,
                    "root_cause_id": rc,
                    "path": path or [origin, nid],
                }
            )

    out: dict[str, Any] = {}
    for nid in node_ids:
        preds = req_pred.get(nid, [])
        ready = (nid not in in_cycle) and all(
            effective(p, frozenset()) == _SATISFIED for p in preds
        )
        out[nid] = {
            "ready": ready,
            "effective": eff_cache[nid],
            "blocked_by": sorted(blocked_by[nid], key=lambda b: b["node_id"]),
        }
    return out


# =============================================================== structural


def validate_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Structural diagnostics over the graph ALONE (no states). Pure; inputs
    untouched. Returns ``{"issues": [...sorted...], "cycles": [...canonical...],
    "node_count": int, "edge_count": int}``.

    Issue codes: UNKNOWN_NODE, DUPLICATE_NODE, SELF_EDGE, DUPLICATE_EDGE, CYCLE
    (deterministic minimal path, rotated to the lexicographically-smallest
    start), NEVER_READY (in-cycle), HANDOFF_UNSPECIFIED (>1 required parents and
    no handoff_from), HANDOFF_UNKNOWN_PARENT (handoff_from names a non-parent)."""
    nodes, edges = _normalize_graph(graph)
    issues: list[dict] = []

    # DUPLICATE_NODE
    counts: dict[str, int] = {}
    for n in nodes:
        counts[n["node_id"]] = counts.get(n["node_id"], 0) + 1
    for nid, c in counts.items():
        if c > 1:
            issues.append({"code": "DUPLICATE_NODE", "node_id": nid, "detail": {"count": c}})
    known = set(counts)

    # edge-level structural issues
    seen_edge: set[tuple[str, str, bool]] = set()
    for e in edges:
        f, t, opt = e["from"], e["to"], e["optional"]
        if f == t:
            issues.append({"code": "SELF_EDGE", "node_id": f, "detail": {}})
        for endpoint in (f, t):
            if endpoint not in known:
                issues.append({"code": "UNKNOWN_NODE", "node_id": endpoint,
                               "detail": {"edge": [f, t]}})
        key = (f, t, opt)
        if key in seen_edge:
            issues.append({"code": "DUPLICATE_EDGE", "node_id": f, "detail": {"edge": [f, t]}})
        seen_edge.add(key)

    adj = _adjacency(nodes, edges)
    node_ids = adj["node_ids"]
    req_pred = adj["req_pred"]
    opt_pred = adj["opt_pred"]
    req_succ = adj["req_succ"]

    # cycles + NEVER_READY. A self-edge is a length-1 cycle: its node is in-cycle.
    canon, in_cycle = _find_cycles(node_ids, req_succ)
    for e in edges:
        if e["from"] == e["to"] and e["from"] in known:
            in_cycle.add(e["from"])
    for cyc in canon:
        issues.append({"code": "CYCLE", "node_id": cyc[0], "detail": {"cycle": cyc}})
    for nid in sorted(in_cycle):
        issues.append({"code": "NEVER_READY", "node_id": nid,
                       "detail": {"reason": "in_cycle"}})

    # handoff rules — a multi-required-parent node must declare EITHER a single
    # ``handoff_from`` (which parent's content flows through) OR an explicit
    # ``merge_policy`` (how ALL parents combine, e.g. "index_order" for Manju's
    # timeline assembly). The contract accepts "handoff_from、merge policy 或
    # 等价字段"; only a node declaring NEITHER is unspecified. predecessors[0]
    # is never assumed either way.
    for n in nodes:
        nid = n["node_id"]
        preds_req = req_pred.get(nid, [])
        handoff = n.get("handoff_from")
        merge_policy = n.get("merge_policy")
        if len(preds_req) > 1 and not handoff and not merge_policy:
            issues.append({"code": "HANDOFF_UNSPECIFIED", "node_id": nid,
                           "detail": {"required_predecessors": preds_req}})
        if handoff:
            all_preds = set(preds_req) | set(opt_pred.get(nid, []))
            if handoff not in all_preds:
                issues.append({"code": "HANDOFF_UNKNOWN_PARENT", "node_id": nid,
                               "detail": {"handoff_from": handoff}})

    issues = _dedupe_sorted(issues)
    return {
        "issues": issues,
        "cycles": canon,
        "node_count": len(node_ids),
        "edge_count": len(_distinct_edges(edges)),
    }


def _distinct_edges(edges: list[dict]) -> set[tuple[str, str, bool]]:
    """The distinct (from, to, optional) edges — duplicates collapsed (edge_count
    reflects the real dependency count, not repeated declarations)."""
    return {(e["from"], e["to"], e["optional"]) for e in edges}


def _dedupe_sorted(issues: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for iss in sorted(issues, key=lambda i: (i["code"], i["node_id"],
                                             hash_value(i.get("detail") or {}))):
        key = iss["code"] + "\0" + iss["node_id"] + "\0" + hash_value(iss.get("detail") or {})
        if key not in seen:
            seen.add(key)
            unique.append(iss)
    return unique


# ================================================================= diagnose


def diagnose(graph: dict[str, Any], states: dict[str, Any]) -> dict[str, Any]:
    """The full ``manju.graph-diagnostics/v1`` document. Pure and deterministic:
    inputs are never mutated, no dict-insertion-order sensitivity, two calls are
    deep-equal, and ``json.dumps(..., sort_keys=True)`` is byte-stable.

    Carries NO scheduling directive of any kind (WP3 SchedulingHints is not
    implemented)."""
    nodes, edges = _normalize_graph(graph)
    adj = _adjacency(nodes, edges)
    node_ids = adj["node_ids"]
    req_pred = adj["req_pred"]
    opt_pred = adj["opt_pred"]

    structural = validate_graph(graph)
    ready = compute_ready(graph, states)

    node_docs: list[dict] = []
    counts = {"ready": 0, _BLOCKED.lower(): 0, "pending": 0, "waiting": 0}
    failed = 0
    kind_by_id = {n["node_id"]: n.get("kind") for n in nodes}
    for nid in node_ids:
        st = states.get(nid) or {}
        status = st.get("status", UNKNOWN)
        r = ready[nid]
        node_docs.append(
            {
                "node_id": nid,
                "kind": kind_by_id.get(nid),
                "status": status,
                "required_predecessors": sorted(req_pred.get(nid, [])),
                "optional_predecessors": sorted(opt_pred.get(nid, [])),
                "ready": r["ready"],
                "effective": r["effective"],
                "blocked_by": r["blocked_by"],
            }
        )
        if status == FAILED:
            failed += 1
        eff = r["effective"]
        if eff == _SATISFIED:
            counts["ready"] += 1
        elif eff == _BLOCKED:
            counts[_BLOCKED.lower()] += 1
        elif eff == _D_WAITING:
            counts["waiting"] += 1
        else:
            counts["pending"] += 1

    # state-dependent issues layered on top of the structural ones
    issues = list(structural["issues"])
    for nid in node_ids:
        st = states.get(nid) or {}
        status = st.get("status", UNKNOWN)
        bb = ready[nid]["blocked_by"]
        origin_statuses = {b["status"] for b in bb}
        if FAILED in origin_statuses:
            issues.append({"code": "BLOCKED_BY_FAILED_ANCESTOR", "node_id": nid,
                           "detail": {"blocked_by": [b for b in bb if b["status"] == FAILED]}})
        if CANCELED in origin_statuses:
            issues.append({"code": "BLOCKED_BY_CANCELED_ANCESTOR", "node_id": nid,
                           "detail": {"blocked_by": [b for b in bb if b["status"] == CANCELED]}})
        if UNKNOWN in origin_statuses:
            issues.append({"code": "UNKNOWN_ANCESTOR", "node_id": nid,
                           "detail": {"blocked_by": [b for b in bb if b["status"] == UNKNOWN]}})
        # WAITING_ANCESTOR: an unblocked node whose effective is WAITING because a
        # WAITING_USER required ancestor gates it (never itself the origin).
        if ready[nid]["effective"] == _D_WAITING and status != WAITING_USER:
            issues.append({"code": "WAITING_ANCESTOR", "node_id": nid, "detail": {}})
        # STALE_CACHE_HIT: claimed a cache hit that is not key/output-verified.
        if status == SKIPPED_CACHE_HIT and not bool(st.get("cache_valid")):
            issues.append({"code": "STALE_CACHE_HIT", "node_id": nid, "detail": {}})

    issues = _dedupe_sorted(issues)

    summary = {
        "node_count": structural["node_count"],
        "edge_count": structural["edge_count"],
        "ready": counts["ready"],
        "blocked": counts[_BLOCKED.lower()],
        "pending": counts["pending"],
        "waiting": counts["waiting"],
        "failed": failed,
        "cycles": len(structural["cycles"]),
    }

    return {
        "schema": SCHEMA,
        "graph_digest": hash_value({"graph": {"nodes": nodes, "edges": edges}, "states": states}),
        "nodes": node_docs,
        "summary": summary,
        "issues": issues,
    }


# =================================================================== derivation


def _recent_failure_ids(project: Any) -> dict[tuple[str, str], str]:
    """``{(step, subject): newest-error-failure-id}`` from reports/failures.jsonl.

    Read-only, best-effort. Only level=="error" records for the generate/voice
    steps are indexed; read_failures is newest-first, so the FIRST id seen per
    (step, subject) is the most recent."""
    out: dict[tuple[str, str], str] = {}
    try:
        from ..core.failures import read_failures

        for rec in read_failures(project, n=500, level="error"):
            step = rec.get("step")
            subject = rec.get("subject")
            fid = rec.get("id")
            if step in ("generate", "voice") and subject and fid:
                out.setdefault((step, subject), fid)
    except Exception:
        pass
    return out


def _map_shot_state(state_value: str, shot_id: str,
                    failures: dict[tuple[str, str], str]) -> dict[str, Any]:
    """ShotState.value -> node status (see the module state-mapping table)."""
    if state_value == "fresh":
        return {"status": SKIPPED_CACHE_HIT, "cache_valid": True}
    if state_value == "manual":
        return {"status": SUCCEEDED}
    if state_value == "stale":
        return {"status": PENDING}
    if state_value == "needs_selection":
        return {"status": PENDING}
    if state_value == "broken":
        fid = failures.get(("generate", shot_id))
        return {"status": FAILED, "root_cause_id": fid}
    # missing
    fid = failures.get(("generate", shot_id))
    if fid is not None:
        return {"status": FAILED, "root_cause_id": fid}
    return {"status": PENDING}


def _map_voice_state(state_value: str, shot_id: str,
                     failures: dict[tuple[str, str], str]) -> dict[str, Any]:
    if state_value == "fresh":
        return {"status": SKIPPED_CACHE_HIT, "cache_valid": True}
    if state_value == "manual":
        return {"status": SUCCEEDED}
    if state_value == "stale":
        return {"status": PENDING}
    # missing
    fid = failures.get(("voice", shot_id))
    if fid is not None:
        return {"status": FAILED, "root_cause_id": fid}
    return {"status": PENDING}


def _compile_cache_valid(project: Any) -> bool:
    """True iff the on-disk timeline's fingerprint equals a fresh recompile
    (explain.py's verdict basis). Best-effort; any failure -> False (PENDING)."""
    try:
        from ..media.probe import probe_duration_ms
        from ..timeline.compiler import compile_timeline, gather_compile_input

        current = project.load_timeline()
        if current is None:
            return False
        would = compile_timeline(gather_compile_input(project, probe_duration_ms))
        return bool(current.meta.compiled_from == would.meta.compiled_from)
    except Exception:
        return False


def _render_cache_valid(project: Any) -> bool:
    """True iff the newest final's content-key sidecar equals the recomputed
    final key (explain.py render verdict). Best-effort; failure -> False."""
    try:
        from ..media.render import _read_key_sidecar, final_content_key

        timeline = project.load_timeline()
        if timeline is None:
            return False
        ass = project.captions_dir / "captions.ass"
        key = final_content_key(project, timeline, ass_file=ass if ass.exists() else None,
                                target="final")
        newest = project.newest_final_path()
        return newest is not None and _read_key_sidecar(newest) == key
    except Exception:
        return False


def _export_states(project: Any, kinds: list[str]) -> dict[str, dict[str, Any]]:
    """Per-export-kind node status, reusing exportstatus freshness (no second
    staleness path). UP_TO_DATE/VERIFIED -> valid cache; PROBLEMATIC -> FAILED;
    anything else (stale/missing/needs_manual) -> PENDING."""
    out: dict[str, dict[str, Any]] = {}
    fresh_by_kind: dict[str, str] = {}
    try:
        from .exportstatus import deliverables

        for row in deliverables(project):
            fresh_by_kind[row.kind] = row.freshness.value
    except Exception:
        pass
    for kind in kinds:
        f = fresh_by_kind.get(kind)
        if f in ("up_to_date", "verified"):
            out[f"export:{kind}"] = {"status": SKIPPED_CACHE_HIT, "cache_valid": True}
        elif f == "problematic":
            out[f"export:{kind}"] = {"status": FAILED, "root_cause_id": None}
        else:
            out[f"export:{kind}"] = {"status": PENDING}
    return out


# exporters that run in build/graph.py's export phase (step 7) — srt/ass are
# produced by the captions phase, not here.
_EXPORT_KINDS = ("otio", "jianying", "capcut")


def derive_build_graph(project: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project Manju's actually-modeled build dependencies into an explicit
    (graph, states) pair — read-only, writes NOTHING. See the module edge/state
    tables for exactly which edges exist and how each node's status is mapped.

    Only INDEXED shots are modeled (index order is the compile authority —
    stale.py:121-135 / graph.py:718-721). No inter-shot edges; nothing derived
    from scenes/characters/prompts/filenames."""
    from .stale import evaluate_all
    from .voice import VoiceState, evaluate_all_voices

    config = project.load_config()
    rules = project.load_rules()
    captions_enabled = bool(getattr(rules.captions, "enabled", True))
    export_kinds = sorted(k for k in config.export_profiles if k in _EXPORT_KINDS)

    shot_states = evaluate_all(project, indexed_only=True)
    voice_states = {v.shot_id: v for v in evaluate_all_voices(project)}
    failures = _recent_failure_ids(project)

    nodes: list[dict] = []
    edges: list[dict] = []
    states: dict[str, Any] = {}

    for st in shot_states:
        sid = st.shot_id
        gen_id = f"gen:{sid}"
        nodes.append({"node_id": gen_id, "kind": "generation", "estimated_ms": None})
        states[gen_id] = _map_shot_state(st.state.value, sid, failures)
        edges.append({"from": gen_id, "to": "compile:timeline"})  # REQUIRED

        vs = voice_states.get(sid)
        if vs is not None and vs.state != VoiceState.NOT_NEEDED:
            voice_id = f"voice:{sid}"
            nodes.append({"node_id": voice_id, "kind": "voice", "estimated_ms": None})
            states[voice_id] = _map_voice_state(vs.state.value, sid, failures)
            edges.append({"from": voice_id, "to": "compile:timeline", "optional": True})

    # merge_policy: the compile's multi-parent combination IS explicitly
    # specified — shots/index.yaml order is the assembly contract (container.py
    # shot_ids: index.yaml IS the order), so HANDOFF_UNSPECIFIED must not fire
    # as permanent noise on every multi-shot project. A graph whose
    # multi-parent node declares neither handoff_from nor merge_policy still
    # gets flagged (the Forge-Film predecessors[0] rejection stays intact).
    nodes.append({"node_id": "compile:timeline", "kind": "compile",
                  "estimated_ms": None, "merge_policy": "index_order"})
    states["compile:timeline"] = (
        {"status": SKIPPED_CACHE_HIT, "cache_valid": True}
        if _compile_cache_valid(project) else {"status": PENDING}
    )

    if captions_enabled:
        nodes.append({"node_id": "captions", "kind": "captions", "estimated_ms": None})
        edges.append({"from": "compile:timeline", "to": "captions"})  # REQUIRED
        edges.append({"from": "captions", "to": "render:final", "optional": True})
        srt = project.captions_dir / "captions.srt"
        cap_valid = srt.exists() and states["compile:timeline"].get("cache_valid") is True
        states["captions"] = (
            {"status": SKIPPED_CACHE_HIT, "cache_valid": True} if cap_valid
            else {"status": PENDING}
        )

    nodes.append({"node_id": "render:final", "kind": "render", "estimated_ms": None})
    edges.append({"from": "compile:timeline", "to": "render:final"})  # REQUIRED
    states["render:final"] = (
        {"status": SKIPPED_CACHE_HIT, "cache_valid": True}
        if _render_cache_valid(project) else {"status": PENDING}
    )

    for kind in export_kinds:
        exp_id = f"export:{kind}"
        nodes.append({"node_id": exp_id, "kind": "export", "estimated_ms": None})
        edges.append({"from": "compile:timeline", "to": exp_id})  # REQUIRED
    states.update(_export_states(project, export_kinds))

    return {"nodes": nodes, "edges": edges}, states


def diagnose_project(project: Any) -> dict[str, Any]:
    """Convenience: derive Manju's build graph and diagnose it. Read-only — the
    shared service the CLI/MCP ``explain --graph`` surface calls."""
    graph, states = derive_build_graph(project)
    return diagnose(graph, states)
