"""Accepted-take production-loop DERIVED views (AI_IDE_08_10_12C WP3/WP5).

Everything in this module is a READ-ONLY, deletable-and-rebuildable projection
over EXISTING truth — take sidecars, attempt evidence (events.jsonl), v2 review
records and the pure assurance derivation. Nothing here persists a file, nothing
participates in build/cache/render keys, and nothing auto-executes: every output
is either display data or a proposal pointer at an existing safe path.

Three views + one routing table:

* :func:`candidate_families` — groups a shot's takes into CREATIVE families
  from evidence only: same creative source revision (sidecar ``spec_hash``),
  explicit-redo lineage (sidecar ``redo_of``), and the attempt-evidence join
  (``request_digest`` + output hashes → per-candidate index). Never grouped by
  filename, shot id or mtime. No CandidateSet store exists — this IS the view.
* :func:`accepted_observed_state` — the shot's current accepted media binding
  plus the reviewer's TRANSIENT opening/endpoint observations, valid only while
  media/spec/expectations are unchanged and assurance still derives accepted;
  any binding move stales it (never fail-open to an old observation). Authored
  hard facts (continuity locks) are carried separately and are never inferred
  from pixels; observations are never written back to Bible/Shot/Timeline.
* :func:`continuation_view` — the WP5 continuation-source gate for a shot that
  declares ``continuity.prev``: the REAL reviewed ending of the accepted source
  media (never the authored prompt's expected ending), with blocking check
  codes when the source is not accepted, its bytes moved, or its endpoint was
  never observed. Consumed by Skills to write the NEXT shot's source patch —
  never by prompt compilation or GenerationRequest assembly.
* :func:`repair_route` — the §10.4 disposition → existing-safe-path map (pure
  data; every route is human-gated, nothing spends or executes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.hashing import hash_value

if TYPE_CHECKING:
    from ..core.container import Project

# production-check severities reuse qc/prompt_checks' two tiers.
_WARNING = "warning"


# ------------------------------------------------------- candidate families


def candidate_families(project: "Project", shot_id: str) -> dict[str, Any]:
    """The derived candidate-family view for one shot (contract §8.3 shape).

    Family key = the creative source revision: the ``spec_hash`` of the take at
    the ROOT of its explicit-redo lineage chain (``redo_of`` hops, cycle-safe),
    so a recipe-replay redo joins its parent's family even across a source
    edit. Takes with no derivable revision (manual imports) stay honest
    singletons keyed by their own name — never merged by coincidence.

    Members carry, when the attempt evidence makes them joinable, the shared
    ``request_digest`` and the take's ``candidate_index`` within that request's
    returned outputs; plus the latest bound review disposition for the take's
    EXACT bytes, and the keeper/selected separation (§8.4: a KEEP conclusion
    never implies selection).

    Hardening WP4 6.1 (claim 7): ``keeper=true`` requires the KEEP record to be
    FULLY current-bound — re-verified through the one existing binding checker
    (:func:`assurance._live_failures`: media/spec/expectations + intake-stale
    marks). A KEEP whose bindings drifted stays visible as
    ``historical_disposition`` with ``binding_status="stale"`` and
    ``keeper=false`` — history is never deleted.

    Hardening WP4 6.3: a ``redo_of`` CYCLE (corrupted/looped lineage) is
    detected, the whole cycle lands in ONE family under a stable canonical
    representative (the min take name), and a ``REDO_LINEAGE_CYCLE`` diagnostic
    is emitted at the view level. Sidecars are never rewritten (derived view)."""
    from ..core.hashing import hash_file
    from .assurance import _live_failures

    takes = list(project.takes(shot_id))
    by_name = {t.name: t for t in takes}

    try:
        selected = project.load_shot(shot_id).status.selected_take
    except Exception:
        selected = None

    # evidence join: media sha -> (request_digest, candidate_index)
    sha_to_request = _attempt_output_index(project, shot_id)
    # review join: media sha -> latest FULL v2 record (file order, later wins)
    verdict_by_sha = _verdicts_by_sha(project, shot_id)

    families: dict[str, dict[str, Any]] = {}
    cycles: dict[frozenset, list[str]] = {}
    for take in sorted(takes, key=lambda t: t.name):
        root, cycle = _lineage_root(take, by_name)
        if cycle:
            cycles.setdefault(frozenset(cycle), sorted(cycle))
        root_rev = (root.sidecar.spec_hash or "").strip()
        if not root_rev or root_rev == "manual":
            key = {"shot": shot_id, "root_take": root.name}
        else:
            key = {"shot": shot_id, "source_spec_hash": root_rev}
        family_id = "derived:" + hash_value(key)
        fam = families.setdefault(family_id, {
            "family_id": family_id,
            "source_spec_hash": root_rev if root_rev and root_rev != "manual" else None,
            "takes": [],
        })

        sha = None
        if take.media_path is not None:
            try:
                sha = hash_file(take.media_path)
            except OSError:
                sha = None
        digest, cand_idx = sha_to_request.get(sha, (None, None)) if sha else (None, None)
        record = verdict_by_sha.get(sha) if sha else None
        dispo = (record.get("decision") or {}).get("disposition") if record else None
        binding_status = None
        keeper = False
        if record is not None and dispo:
            try:
                current = not _live_failures(project, shot_id, record)
            except Exception:
                current = False  # unverifiable binding is never current (fail closed)
            binding_status = "current" if current else "stale"
            keeper = dispo == "KEEP" and current
        fam["takes"].append({
            "take": take.name,
            "media_sha256": sha,
            "candidate_index": cand_idx,
            "request_digest": digest,
            "redo_of": getattr(take.sidecar, "redo_of", None),
            "review_disposition": dispo,
            "historical_disposition": dispo,
            "binding_status": binding_status,
            "keeper": keeper,
            "selected": take.name == selected,
        })

    out: dict[str, Any] = {
        "shot": shot_id,
        "families": sorted(families.values(), key=lambda f: f["family_id"]),
    }
    if cycles:
        out["diagnostics"] = [
            {
                "code": "REDO_LINEAGE_CYCLE",
                "severity": _WARNING,
                "members": members,
                "canonical_representative": members[0],  # min name (sorted)
                "message": "redo_of lineage forms a cycle — grouped under the "
                           "canonical representative; fix the sidecars by hand "
                           "if the lineage is wrong (never auto-rewritten)",
            }
            for members in sorted(cycles.values())
        ]
    return out


def _lineage_root(take, by_name: dict, *, max_hops: int = 32):
    """Follow ``redo_of`` to the chain root. Returns ``(root, cycle_members)``
    — ``cycle_members`` is the list of take names forming a cycle when one is
    hit (empty otherwise). On a cycle the root is the STABLE canonical
    representative (min take name among the cycle's members), so every take
    walking into the same cycle lands in the SAME family regardless of entry
    point (WP4 6.3; pre-hardening each member rooted at its predecessor,
    splitting the cycle across families)."""
    order = [take.name]
    seen = {take.name}
    cur = take
    for _ in range(max_hops):
        parent = getattr(cur.sidecar, "redo_of", None)
        if not parent or parent not in by_name:
            return cur, []
        if parent in seen:
            cycle = order[order.index(parent):]
            return by_name[min(cycle)], cycle
        seen.add(parent)
        order.append(parent)
        cur = by_name[parent]
    return cur, []


def _attempt_output_index(project: "Project", shot_id: str) -> dict[str, tuple]:
    """media sha256 -> (request_digest, candidate_index) from the EXISTING
    attempt evidence stream. First mapping wins (the attempt that produced the
    bytes); index = position among that attempt's hashed outputs."""
    try:
        from ..build.attempts import read_attempts

        records, _malformed = read_attempts(project)
    except Exception:
        return {}
    out: dict[str, tuple] = {}
    for rec in records:
        unit = rec.get("unit") or {}
        if unit.get("shot") != shot_id:
            continue
        digest = (rec.get("request") or {}).get("request_digest")
        if not digest:
            continue
        idx = 0
        for o in rec.get("outputs") or []:
            sha = o.get("sha256") if isinstance(o, dict) else None
            if not sha:
                continue
            out.setdefault(sha, (digest, idx))
            idx += 1
    return out


def _verdicts_by_sha(project: "Project", shot_id: str) -> dict[str, dict]:
    """media sha256 → the latest v2 record carrying a decision for those exact
    bytes (file order — later verdicts win). The FULL record is kept so keeper
    can be re-verified against CURRENT bindings via ``_live_failures`` (WP4
    6.1) instead of trusting the disposition string alone."""
    try:
        from .agent_review import read_v2_records

        records, _ = read_v2_records(project)
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for rec in records:  # file order — later verdicts win
        subj = rec.get("subject") or {}
        if subj.get("kind") != "shot" or subj.get("id") != shot_id:
            continue
        dispo = (rec.get("decision") or {}).get("disposition")
        sha = rec.get("media_sha256")
        if dispo and sha:
            out[sha] = rec
    return out


# --------------------------------------------------- accepted observed state


def accepted_observed_state(project: "Project", shot_id: str) -> dict[str, Any]:
    """§10.2 — the derived accepted-state projection for one shot.

    ``status``:
        "current"       assurance derives accepted for the CURRENT selected
                        bytes; transient observations below are live evidence
        "not_accepted"  anything else (unreviewed/rejected/stale/unknown/...)
                        — observations are withheld (a stale or unaccepted
                        observation is NOT evidence; never fail-open)

    Authored hard facts (continuity locks) ride separately from the transient
    opening/endpoint observations; neither is ever written back anywhere."""
    from .agent_review import _current_selected_media, read_v2_records
    from .assurance import _live_failures, compute_assurance

    assurance = compute_assurance(project, shot_id)
    _path, cur_sha = _current_selected_media(project, shot_id)
    try:
        locks = list(project.load_shot(shot_id).continuity.locks)
    except Exception:
        locks = []

    state: dict[str, Any] = {
        "subject": shot_id,
        "assurance_state": assurance["assurance_state"],
        "assurance_digest": hash_value(assurance),
        "media_sha256": cur_sha,
        "authored_locks": locks,
        "observed_opening": [],
        "observed_endpoint": [],
        "status": "not_accepted",
        "reasons": list(assurance.get("reasons") or []),
    }
    if assurance["assurance_state"] != "accepted" or cur_sha is None:
        return state

    state["status"] = "current"
    state["reasons"] = []
    # the transient observations: the LAST current-bound v2 record for these
    # exact bytes that carries observed_states (file order, later wins).
    try:
        records, _ = read_v2_records(project)
    except Exception:
        records = []
    chosen = None
    for rec in records:
        subj = rec.get("subject") or {}
        if subj.get("kind") != "shot" or subj.get("id") != shot_id:
            continue
        if rec.get("media_sha256") != cur_sha:
            continue
        if rec.get("observed_states") is None:
            continue
        if _live_failures(project, shot_id, rec):
            continue  # binding moved -> that observation is history, not evidence
        chosen = rec
    if chosen is not None:
        for obs in chosen.get("observed_states") or []:
            bucket = ("observed_endpoint" if obs.get("position") == "END"
                      else "observed_opening")
            state[bucket].append(dict(obs))
    return state


# ------------------------------------------------------- continuation gate


def continuation_view(project: "Project", shot_id: str) -> dict[str, Any] | None:
    """§10.5/10.6 — the continuation-source gate + Skill-input view for a shot
    declaring ``continuity.prev``. ``None`` when the shot declares no
    continuation. The view cites the REAL reviewed endpoint of the accepted
    source media — never the authored prompt's expected ending — and carries
    blocking production checks when the gate is not satisfied. It is Skill
    input only: build/prompt compilation never reads it."""
    try:
        shot = project.load_shot(shot_id)
    except Exception:
        return None
    prev_id = (shot.continuity.prev or "").strip() if shot.continuity else ""
    if not prev_id:
        return None
    pinned = getattr(shot.continuity, "source_media_sha256", None)

    # Hardening WP4 6.2 (claim 8): a shot that DECLARES continuity.prev must
    # never lose its gate to a raising derivation — pre-hardening the exception
    # propagated into prompt_checks' `except: pass` and the gate silently
    # vanished. Unavailable evidence is a blocking finding, not silence.
    try:
        src = accepted_observed_state(project, prev_id)
    except Exception as exc:
        return {
            "subject": shot_id,
            "source_shot": prev_id,
            "source_status": "unavailable",
            "checks": [_check(
                "CONTINUATION_CHECK_UNAVAILABLE",
                f"续接源 {prev_id} 的验收/观察证据当前无法推导"
                f"({type(exc).__name__})— 证据不可用时不得静默放行续写",
                [f"shots/{shot_id}.yaml#/continuity/prev"],
                f"修复 {prev_id} 的评审证据(reports/ 下 v2 记录/媒体可读性)"
                "后重试;或显式改写 continuity.prev",
            )],
            "do_not_execute_automatically": True,
        }
    checks: list[dict[str, Any]] = []
    src_path = f"shots/{shot_id}.yaml#/continuity/prev"

    if src["status"] != "current":
        checks.append(_check(
            "CONTINUATION_SOURCE_NOT_ACCEPTED",
            f"续接源 {prev_id} 当前不是 accepted(assurance="
            f"{src['assurance_state']})— 不能从未验收的媒体续写下一镜",
            [src_path],
            f"先完成 {prev_id} 的 bound review 直到 assurance=accepted,"
            "或改写 continuity.prev",
        ))
    if pinned and src.get("media_sha256") and pinned != src["media_sha256"]:
        checks.append(_check(
            "CONTINUATION_SOURCE_HASH_MISMATCH",
            f"续接锚定的 source_media_sha256 与 {prev_id} 当前选中媒体不符"
            "(源已被替换/重选)",
            [f"shots/{shot_id}.yaml#/continuity/source_media_sha256"],
            "重看当前媒体的真实结尾后更新锚定 hash(source patch),"
            "或回滚源镜头的选择",
        ))
    if src["status"] == "current" and not src["observed_endpoint"]:
        checks.append(_check(
            "CONTINUATION_ENDPOINT_UNOBSERVED",
            f"续接源 {prev_id} 的真实结尾从未被 bound review 观察过 — "
            "不得从原 Prompt 的预期结尾直接续写",
            [src_path],
            f"manju qc brief 出题 {prev_id},reviewer 回填 observed_states"
            "(position=END)后再续写",
        ))

    completed, reserved = _beats(project, prev_id)
    return {
        "subject": shot_id,
        "source_shot": prev_id,
        "source_media_sha256": src.get("media_sha256"),
        "source_assurance_digest": src.get("assurance_digest"),
        "source_status": src["status"],
        "observed_endpoint": list(src["observed_endpoint"]),
        "completed_beats": completed,
        "excluded_future_beats": reserved,
        "canonical_ref_reanchor_recommended": _chain_depth(project, shot_id) >= 3,
        "checks": checks,
        "do_not_execute_automatically": True,
    }


def continuation_checks(project: "Project", shot_id: str) -> list[dict[str, Any]]:
    """Just the gate's blocking checks (for ``manju prompt --check`` wiring).
    Empty when the shot declares no continuation or the gate is satisfied."""
    view = continuation_view(project, shot_id)
    return list(view["checks"]) if view else []


def _check(code: str, message: str, source_paths: list[str],
           proposal: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": _WARNING,
        "level": _WARNING,  # qc/prompt_checks printer + has_blocking compat
        "message": message,
        "suggestion": proposal,
        "source_paths": source_paths,
        "proposal": proposal,
        "auto_apply": False,
    }


def _beats(project: "Project", shot_id: str) -> tuple[list[str], list[str]]:
    """(completed beats, reserved/future beats) of the SOURCE shot's authored
    action — clause-level, deterministic (reuses qc/prompt_checks splitting).
    Advisory Skill context only."""
    from .prompt_checks import _FUTURE_RE, split_clauses

    try:
        text = (project.load_shot(shot_id).action.main or "").strip()
    except Exception:
        return [], []
    clauses = split_clauses(text)
    completed = [c for c in clauses if not _FUTURE_RE.search(c)]
    reserved = [c for c in clauses if _FUTURE_RE.search(c)]
    return completed, reserved


def _chain_depth(project: "Project", shot_id: str, *, max_hops: int = 8) -> int:
    """Length of the continuity.prev chain above this shot (cycle-safe) — the
    conditional re-anchor advisory input (identity drift over long chains)."""
    depth = 0
    seen = {shot_id}
    cur = shot_id
    for _ in range(max_hops):
        try:
            prev = (project.load_shot(cur).continuity.prev or "").strip()
        except Exception:
            return depth
        if not prev or prev in seen:
            return depth
        depth += 1
        seen.add(prev)
        cur = prev
    return depth


# ------------------------------------------------------------ repair routing


# §10.4 — disposition → the EXISTING safe path. Pure data: nothing here (or
# anywhere) auto-selects, auto-redoes, auto-patches or auto-spends.
_REPAIR_ROUTES: dict[str, dict[str, str]] = {
    "KEEP": {
        "path": "human_select",
        "command": "manju select <shot> <take>",
        "note": "KEEP 是评审结论;是否选用仍由人决定(不自动 select)",
    },
    "FIX_IN_POST": {
        "path": "repair_op",
        "command": "manju repair --op <extend|trim|retime|inout|croppad|voice> --dry-run",
        "note": "确定性后期可修;先 dry-run,auto 只做安全项",
    },
    "EDIT_DONT_REGENERATE": {
        "path": "timeline_roundtrip_proposal",
        "command": "manju director propose(timeline/NLE roundtrip 修改)",
        "note": "剪辑/遮挡/字幕/声音处理;不重生成",
    },
    "REROLL": {
        "path": "explicit_redo",
        "command": "manju redo <shot> [--seed N|--from-take X](经 §8.3 spend gate)",
        "note": "source/Prompt 基本正确;同一创作意图,只换随机性,一次一个变量",
    },
    "REWRITE_SOURCE": {
        "path": "director_proposal",
        "command": "manju director propose(先 source patch,后重生成)",
        "note": "问题出在镜头意图/Prompt source/refs/结构;patch 前不提交任何生成",
    },
}


def repair_route(disposition: str) -> dict[str, Any] | None:
    """The mapped existing safe path for one disposition, or ``None`` for an
    unknown value (never invented)."""
    route = _REPAIR_ROUTES.get(disposition)
    if route is None:
        return None
    return {**route, "disposition": disposition,
            "do_not_execute_automatically": True}
