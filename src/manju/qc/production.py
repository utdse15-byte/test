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
    EXACT bytes, the latest Experiment Memory for those bytes, and the
    keeper/selected separation (§8.4: a KEEP conclusion never implies
    selection).  A stale experiment remains under ``historical_experiment``
    but is never projected as the current ``experiment``.

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
    experiment_by_sha = _experiments_by_sha(project, shot_id)

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
        experiment_record = experiment_by_sha.get(sha) if sha else None
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
        historical_experiment = (
            (experiment_record.get("decision") or {}).get("experiment")
            if experiment_record else None
        )
        experiment_current = False
        if experiment_record is not None and historical_experiment is not None:
            try:
                experiment_current = not _live_failures(
                    project, shot_id, experiment_record
                )
            except Exception:
                experiment_current = False
        fam["takes"].append({
            "take": take.name,
            "media_sha256": sha,
            "candidate_index": cand_idx,
            "request_digest": digest,
            "redo_of": getattr(take.sidecar, "redo_of", None),
            "review_disposition": dispo,
            "historical_disposition": dispo,
            "binding_status": binding_status,
            "experiment": historical_experiment if experiment_current else None,
            "historical_experiment": historical_experiment,
            "experiment_binding_status": (
                "current" if experiment_current else "stale"
            ) if historical_experiment is not None else None,
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


def _experiments_by_sha(project: "Project", shot_id: str) -> dict[str, dict]:
    """Exact media sha256 -> latest v2 record carrying Experiment Memory.

    This index is deliberately independent from the latest disposition index:
    a later plain review does not erase an earlier experiment.  Currency is
    checked by the caller through the one existing binding verifier.
    """
    try:
        from .agent_review import read_v2_records

        records, _ = read_v2_records(project)
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for rec in records:
        subject = rec.get("subject") or {}
        if subject.get("kind") != "shot" or subject.get("id") != shot_id:
            continue
        experiment = (rec.get("decision") or {}).get("experiment")
        media_sha256 = rec.get("media_sha256")
        if experiment is not None and media_sha256:
            out[media_sha256] = rec
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


# ================================================= AI_IDE_15 §7/§8 derived views
#
# The multi-reviewer agreement view, the drift trend, and the 7-route repair
# proposal — all READ-ONLY projections over current-bound v2 review records
# (their §6 dimension observations). Nothing here writes, spends, or executes;
# every route is human-gated (``requires_confirmation``).

DRIFT_SCHEMA = "manju.qc.drift/v1"
ROUTES_SCHEMA = "manju.qc.repair_routes/v1"
AGREEMENT_SCHEMA = "manju.qc.reviewer_agreement/v1"

# §8: the 7-route vocabulary EXTENDS the 5-disposition repair mapping above.
# KEEP→ACCEPT_DEVIATION and the four other dispositions map 1:1; REGENERATE_
# REFERENCE and RESHOOT are ROUTE-LEVEL additions — they are NOT verdict
# dispositions (a reviewer can never emit them as decision.disposition; see
# agent_review.DISPOSITIONS). This distinction is the whole point of ruling 5.
DISPOSITION_TO_ROUTE: dict[str, str] = {
    "KEEP": "ACCEPT_DEVIATION",
    "FIX_IN_POST": "FIX_IN_POST",
    "EDIT_DONT_REGENERATE": "EDIT_DONT_REGENERATE",
    "REROLL": "REROLL",
    "REWRITE_SOURCE": "REWRITE_SOURCE",
}
ROUTE_LEVEL_ADDITIONS = ("REGENERATE_REFERENCE", "RESHOOT")
SEVEN_ROUTES = tuple(DISPOSITION_TO_ROUTE.values()) + ROUTE_LEVEL_ADDITIONS

# the §9.3 one-variable → route map. The reviewer's suggested_repair_variable is
# the primary signal (one variable per repair, §6); routes never invent a second.
_VARIABLE_ROUTE: dict[str, str] = {
    "reference_asset": "REGENERATE_REFERENCE",
    "reference_role": "REGENERATE_REFERENCE",
    "source_action": "RESHOOT",
    "camera": "RESHOOT",
    "motion": "RESHOOT",
    "endpoint": "RESHOOT",
    "framing": "EDIT_DONT_REGENERATE",
    "clip_scope": "EDIT_DONT_REGENERATE",
    "post_trim": "FIX_IN_POST",
    "post_mask": "FIX_IN_POST",
    "post_grade": "FIX_IN_POST",
    "text_overlay": "FIX_IN_POST",
    "audio": "FIX_IN_POST",
    "lighting": "FIX_IN_POST",
    "seed": "REROLL",
    "provider_surface": "REROLL",
    "safety_wording": "REWRITE_SOURCE",
}
_ROUTE_DEFAULT_VARIABLE = {
    "REGENERATE_REFERENCE": "reference_asset", "RESHOOT": "camera",
    "FIX_IN_POST": "post_grade", "EDIT_DONT_REGENERATE": "clip_scope",
    "REROLL": "seed", "REWRITE_SOURCE": "safety_wording",
    "ACCEPT_DEVIATION": "clip_scope",
}
# routes that spend on a generation provider price through the EXISTING estimator
# (build.spend) at the human-gated spend step; local routes are deterministic
# ffmpeg (repair_op) or a plain accept — no generation spend.
_GENERATION_ROUTES = {"REROLL", "REGENERATE_REFERENCE", "RESHOOT", "REWRITE_SOURCE"}
_DIM_SEV_ORDER = {None: 0, "info": 1, "warning": 2, "blocker": 3}


def _max_dim_severity(sevs) -> str | None:
    best: str | None = None
    for s in sevs:
        if _DIM_SEV_ORDER.get(s, 0) > _DIM_SEV_ORDER.get(best, 0):
            best = s
    return best


def _bound_dimension_records(project: "Project", shot_id: str) -> list[dict]:
    """Every CURRENT-bound v2 shot record for ``shot_id`` that carries §6
    dimension observations (file order). Uses the ONE binding checker
    (:func:`assurance._live_failures`) so a record whose media/spec/expectations
    moved is dropped as history — the drift trend is derived from current-bound
    evidence ONLY (§12: 漂移趋势只从 current-bound evidence 派生)."""
    from .agent_review import read_v2_records
    from .assurance import _live_failures

    out: list[dict] = []
    try:
        records, _ = read_v2_records(project)
    except Exception:
        return out
    for rec in records:
        subj = rec.get("subject") or {}
        if subj.get("kind") != "shot" or subj.get("id") != shot_id:
            continue
        if not rec.get("dimension_observations"):
            continue
        try:
            if _live_failures(project, shot_id, rec):
                continue
        except Exception:
            continue
        out.append(rec)
    return out


def verdict_reviewer_current(record: dict, current_reviewer_digest: str | None) -> bool:
    """§11 cache currency extended to the reviewer PROFILE digest: a stored
    verdict is current for the configured reviewer iff its ``reviewer.profile_
    digest`` equals the current one. A changed reviewer profile/model makes old
    verdicts historical — the review cache never reuses a result across models
    (§12: 缓存不跨模型错误复用). ``None`` disables the check (profile-agnostic)."""
    if current_reviewer_digest is None:
        return True
    return (record.get("reviewer") or {}).get("profile_digest") == current_reviewer_digest


def drift_trend(project: "Project", shots: list[str] | None = None) -> dict[str, Any]:
    """The §8 drift trend: a dimension × shot-order matrix over current-bound §6
    dimension observations. Surfaces DRIFT (``observed == "mismatch"``) — a
    per-dimension row in shot order, latest current-bound record winning per
    dimension. Pure, read-only, current-bound only."""
    order = list(shots) if shots is not None else list(project.shot_ids())
    dimensions: dict[str, list[dict]] = {}
    for sid in order:
        latest_by_dim: dict[str, tuple[dict, dict]] = {}
        for rec in _bound_dimension_records(project, sid):  # file order
            for o in rec.get("dimension_observations") or []:
                latest_by_dim[o["dimension"]] = (o, rec)
        for dim, (o, rec) in latest_by_dim.items():
            if o.get("observed") != "mismatch":
                continue  # a match is continuity, not drift
            dimensions.setdefault(dim, []).append({
                "shot": sid,
                "observed": o["observed"],
                "severity": o.get("severity"),
                "subject_ref": o.get("subject_ref"),
                "suggested_repair_variable": o.get("suggested_repair_variable"),
                "reviewer": (rec.get("reviewer") or {}).get("name"),
            })
    return {
        "schema": DRIFT_SCHEMA,
        "shot_order": order,
        "dimensions": dimensions,
        "bound_only": True,
        "do_not_execute_automatically": True,
    }


def _route_for(variable: str | None) -> str:
    """One route from the reviewer's suggested repair variable (§9.3 one-variable
    rule). No variable named ⇒ the minimal same-intent retry (REROLL)."""
    return _VARIABLE_ROUTE.get(variable or "", "REROLL")


def repair_routes(project: "Project", shots: list[str] | None = None) -> dict[str, Any]:
    """The §8 minimal, executable repair-route proposals derived from the drift
    trend. One proposal per (route, dimension) with drift; each declares its
    primary variable, an estimated-cost class (generation routes price through
    the existing build.spend estimator at the human-gated spend step), the
    affected shots, and ``requires_confirmation: true``. NOTHING auto-executes."""
    trend = drift_trend(project, shots)
    proposals: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for dim, entries in trend["dimensions"].items():
        if not entries:
            continue
        affected = [e["shot"] for e in entries]
        sev = _max_dim_severity(e.get("severity") for e in entries)
        variable = next((e["suggested_repair_variable"] for e in entries
                         if e.get("suggested_repair_variable")), None)
        route = _route_for(variable)
        key = (route, dim)
        if key in seen:
            continue
        seen.add(key)
        gen = route in _GENERATION_ROUTES
        proposals.append({
            "route": route,
            "dimension": dim,
            "primary_variable": variable or _ROUTE_DEFAULT_VARIABLE.get(route, "seed"),
            "estimated_cost": {
                "class": "generation" if gen else "local",
                "note": ("prices through build.spend at the human-gated spend step; "
                         "nothing here spends or executes") if gen
                        else ("deterministic post op (media.repair_ops) or accept — "
                              "no generation spend"),
            },
            "affected_shots": affected,
            "severity": sev,
            "is_route_level_addition": route in ROUTE_LEVEL_ADDITIONS,
            "requires_confirmation": True,
            "do_not_execute_automatically": True,
        })
    proposals.sort(key=lambda p: (p["route"], p["dimension"]))
    return {
        "schema": ROUTES_SCHEMA,
        "routes": proposals,
        "route_vocabulary": list(SEVEN_ROUTES),
        "route_level_additions": list(ROUTE_LEVEL_ADDITIONS),
        "do_not_execute_automatically": True,
    }


def reviewer_agreement(project: "Project", shot_id: str, *,
                       current_reviewer_digest: str | None = None) -> dict[str, Any]:
    """§7 multi-reviewer / blind-review agreement view for one shot. Reads every
    v2 record's §6 dimension observations per reviewer (current-bound), computes
    per-dimension agreement, and — crucially — sets state
    ``UNKNOWN_REVIEWER_DISAGREEMENT`` when reviewers disagree on ANY
    blocker-severity dimension (never majority-vote a blocker away). Human
    adjudication (a verdict filed by ``actor="human"`` / ``reviewer.kind ==
    "human"``) is listed separately as an APPENDED opinion; the original reviewer
    records are always kept. ``current_reviewer_digest`` (optional) marks records
    from a superseded reviewer profile as historical (§11 cache currency)."""
    from .agent_review import read_v2_records
    from .assurance import _live_failures

    try:
        records, _ = read_v2_records(project)
    except Exception:
        records = []

    reviewers: dict[str, dict] = {}
    per_dim: dict[str, dict[str, dict]] = {}
    adjudications: list[dict] = []
    for rec in records:
        subj = rec.get("subject") or {}
        if subj.get("kind") != "shot" or subj.get("id") != shot_id:
            continue
        reviewer = rec.get("reviewer") or {}
        if reviewer.get("kind") == "human" or rec.get("actor") == "human":
            adjudications.append({
                "actor": rec.get("actor"),
                "reviewer": reviewer,
                "ts": rec.get("ts"),
                "dimension_observations": rec.get("dimension_observations") or [],
            })
            continue
        try:
            bound = not _live_failures(project, shot_id, rec)
        except Exception:
            bound = False
        name = reviewer.get("name") or "?"
        pdig = reviewer.get("profile_digest")
        reviewers[name] = {
            "name": name, "profile_digest": pdig,
            "current": bound and verdict_reviewer_current(rec, current_reviewer_digest),
        }
        if not bound:
            continue
        for o in rec.get("dimension_observations") or []:
            per_dim.setdefault(o["dimension"], {})[name] = {
                "observed": o.get("observed"), "severity": o.get("severity")}

    per_dimension: dict[str, dict] = {}
    blocker_disagreement = False
    for dim, by_rev in per_dim.items():
        observeds = {v["observed"] for v in by_rev.values()}
        agree = len(observeds) <= 1
        max_sev = _max_dim_severity(v.get("severity") for v in by_rev.values())
        if not agree and max_sev == "blocker":
            blocker_disagreement = True
        per_dimension[dim] = {
            "observed_by_reviewer": by_rev, "agree": agree, "max_severity": max_sev}

    return {
        "schema": AGREEMENT_SCHEMA,
        "subject": shot_id,
        "reviewers": sorted(reviewers.values(), key=lambda r: r["name"]),
        "per_dimension": per_dimension,
        "blocker_disagreement": blocker_disagreement,
        "state": ("UNKNOWN_REVIEWER_DISAGREEMENT" if blocker_disagreement
                  else "AGREEMENT"),
        "adjudications": adjudications,
        "do_not_execute_automatically": True,
    }


# ============================================================================
# AI_IDE_16 — Preview Ladder (DERIVED state) + keyframe adoption + spend gate
#
# The ladder is DERIVED, never stored (Fable ruling 1): a pure projection over
# EXISTING truth. "Approved" at KEYFRAME rides the EXISTING adoption facts — a
# selected keyframe (image) take OR a refs-binding referencing that keyframe's
# exact media (a promoted ref). NO new approval store/flag is introduced, and a
# shot with no keyframe candidates is UNAFFECTED (the ladder is opt-in per shot,
# so old projects are byte-identical).
# ============================================================================

LADDER_SCHEMA = "manju.preview-ladder/v1"

# §3 rungs, ascending cost order.
LADDER_STAGES = (
    "SCRIPT", "AUDITION", "KEYFRAME", "BOARD", "ANIMATIC", "MOTION_REF",
    "FINAL_VIDEO",
)

# Keyframe candidates are IMAGE takes; final video takes are VIDEO takes. The
# take's own media kind is the ONLY grouping signal (never filename/mtime).
_LADDER_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg"})
_LADDER_VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v"})

KEYFRAME_NOT_ADOPTED = "KEYFRAME_NOT_ADOPTED"


def _take_media_ext(take) -> str:
    mp = getattr(take, "media_path", None)
    return mp.suffix.lower() if mp is not None else ""


def _ladder_media_sha(path) -> str | None:
    from ..core.hashing import hash_file

    try:
        return hash_file(path) if path is not None and path.exists() else None
    except Exception:
        return None


def keyframe_candidates(project: "Project", shot_id: str) -> list:
    """A shot's KEYFRAME candidates = its IMAGE takes (Fable ruling 3).

    Image takes produced through the EXISTING generate path (image-capability
    providers / a human image import); their candidate sidecars already carry
    request/ref/provider/cost provenance (08_10_12C). This is a derivation over
    ``project.takes`` — no candidate store."""
    return [t for t in project.takes(shot_id)
            if _take_media_ext(t) in _LADDER_IMAGE_EXTS]


def video_takes(project: "Project", shot_id: str) -> list:
    """A shot's VIDEO takes (the FINAL_VIDEO rung evidence)."""
    return [t for t in project.takes(shot_id)
            if _take_media_ext(t) in _LADDER_VIDEO_EXTS]


def keyframe_adoption(project: "Project", shot_id: str) -> dict[str, Any]:
    """The EXISTING adoption facts at the KEYFRAME rung (Fable ruling 1).

    A keyframe is ADOPTED iff an image candidate is EITHER the shot's selected
    take OR referenced by EXACT media bytes from a refs-source binding (a
    promoted ref / canonical style frame / first-frame ref). Read-only: this
    reads ``status.selected_take`` and the resolved refs and joins by content
    hash — it never writes a flag."""
    candidates = keyframe_candidates(project, shot_id)
    out: dict[str, Any] = {
        "has_candidates": bool(candidates),
        "candidate_count": len(candidates),
        "candidates": [t.name for t in candidates],
        "adopted": False,
        "via": None,
        "take": None,
        "media_sha": None,
    }
    if not candidates:
        return out

    cand_by_sha: dict[str, str] = {}
    for t in candidates:
        sha = _ladder_media_sha(getattr(t, "media_path", None))
        if sha:
            cand_by_sha.setdefault(sha, t.name)

    # (a) a selected keyframe (image) take
    try:
        selected = project.load_shot(shot_id).status.selected_take
    except Exception:
        selected = None
    sel = next((t for t in candidates if t.name == selected), None)
    if sel is not None:
        out.update(adopted=True, via="selected_take", take=sel.name,
                   media_sha=_ladder_media_sha(getattr(sel, "media_path", None)))
        return out

    # (b) a refs-binding referencing a candidate's EXACT media (promoted ref)
    try:
        from ..providers.refs import resolve_refs

        shot = project.load_shot(shot_id)
        refset = resolve_refs(project, shot, project.load_bible())
    except Exception:
        refset = None
    if refset is not None:
        for it in refset.items:
            p = getattr(it, "path", None)
            if p is None or not getattr(it, "exists", False):
                continue
            sha = _ladder_media_sha(p)
            if sha and sha in cand_by_sha:
                out.update(adopted=True, via="promoted_ref",
                           take=cand_by_sha[sha], media_sha=sha, ref=it.ref)
                return out
    return out


def motion_references(project: "Project", shot_id: str) -> list[dict[str, Any]]:
    """External MOTION references (§7 WP3 / Fable ruling 5).

    A motion reference is a VIDEO ref whose transfer contract declares
    ``controls`` including ``motion``; its ``must_not_transfer`` set rides the
    EXISTING ``ignore`` field. The closed ``REF_TRANSFER_VOCAB`` already carries
    ``motion`` — no new vocabulary. Bytes/URL + controls/ignore ride the
    resolved binding, so this is pure derivation."""
    try:
        from ..providers.refs import resolve_refs

        shot = project.load_shot(shot_id)
        refset = resolve_refs(project, shot, project.load_bible())
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for it in refset.video_items():
        if "motion" in (getattr(it, "controls", ()) or ()):
            out.append({
                "ref": it.ref,
                "controls": list(it.controls),
                "must_not_transfer": list(it.ignore),
                "content_sha": _ladder_media_sha(getattr(it, "path", None)),
                "is_url": bool(getattr(it, "is_url", False)),
            })
    return out


def _audition_exists(project: "Project") -> bool:
    d = project.root / "renders" / "audition"
    return d.is_dir() and any(d.glob("audition_v*.mp4"))


def _animatic_exists(project: "Project") -> bool:
    d = project.root / "renders" / "animatic"
    return d.is_dir() and any(d.glob("animatic_v*.mp4"))


def _board_exists(project: "Project") -> bool:
    if (project.root / "board.html").exists():
        return True
    frames = project.root / "reports" / "frames"
    return frames.is_dir() and any(frames.glob("board_*.jpg"))


def _next_step_estimate(project: "Project", shot_id: str) -> dict[str, Any]:
    """The §6 predicted incremental cost from the current preview layer to the
    paid VIDEO layer — priced through the EXISTING per-shot estimator (the same
    routing the build would use). ``None`` cost when unpriceable (never coerced
    to 0)."""
    try:
        from ..build.graph import _estimate_shot_cost, _target_duration_ms

        shot = project.load_shot(shot_id)
        cost, currency = _estimate_shot_cost(
            shot, _target_duration_ms(project, shot, project.load_rules()))
        return {"stage": "FINAL_VIDEO", "estimated_cost": cost, "currency": currency}
    except Exception:
        return {"stage": "FINAL_VIDEO", "estimated_cost": None, "currency": None}


def ladder_view(project: "Project", shot_id: str) -> dict[str, Any]:
    """The DERIVED §3 preview-ladder view for one shot — pure, read-only,
    rebuildable, never stored. Each rung's ``reached`` is a fact over EXISTING
    truth; ``stage`` is the highest reached rung. ``approved_at_keyframe`` rides
    the existing adoption facts (no new flag)."""
    reached = {s: False for s in LADDER_STAGES}

    try:
        project.load_shot(shot_id)
        reached["SCRIPT"] = True
    except Exception:
        pass

    reached["AUDITION"] = _audition_exists(project)

    adoption = keyframe_adoption(project, shot_id)
    reached["KEYFRAME"] = adoption["has_candidates"]

    reached["BOARD"] = _board_exists(project)
    reached["ANIMATIC"] = _animatic_exists(project)

    mrefs = motion_references(project, shot_id)
    reached["MOTION_REF"] = bool(mrefs)

    vts = video_takes(project, shot_id)
    reached["FINAL_VIDEO"] = bool(vts)

    stage = "SCRIPT"
    for s in LADDER_STAGES:
        if reached[s]:
            stage = s

    return {
        "schema": LADDER_SCHEMA,
        "shot": shot_id,
        "stage": stage,
        "reached": reached,
        "keyframe": adoption,
        "approved_at_keyframe": adoption["adopted"],
        "motion_references": mrefs,
        "final_video_takes": [t.name for t in vts],
        "next_step": (None if reached["FINAL_VIDEO"]
                      else _next_step_estimate(project, shot_id)),
        "do_not_execute_automatically": True,
    }


def keyframe_gate(project: "Project", shot_id: str) -> dict[str, Any]:
    """The §10 spend-gate condition for ONE shot (Fable ruling 2).

    A shot that HAS keyframe candidates but NO adopted one is GATED for paid
    video. A shot with NO keyframe candidates is UNAFFECTED (opt-in per shot —
    old projects byte-identical)."""
    adoption = keyframe_adoption(project, shot_id)
    return {
        "shot": shot_id,
        "gated": adoption["has_candidates"] and not adoption["adopted"],
        "adoption": adoption,
    }


def keyframe_gated_shots(project: "Project", shot_ids=None) -> list[str]:
    """Every shot whose paid video is gated by an unadopted keyframe."""
    ids = list(shot_ids) if shot_ids is not None else list(project.shot_ids())
    return [sid for sid in ids if keyframe_gate(project, sid)["gated"]]


def keyframe_ladder_checks(project: "Project", shot_id: str) -> list[dict[str, Any]]:
    """The collaborative surface of the spend gate (Fable ruling 2): a
    ``KEYFRAME_NOT_ADOPTED`` production check for ``manju prompt --check``.

    ADVISORY level (NOT in ``FAIL_LEVELS``) — never a hard block for a human,
    who may legitimately skip the ladder. Fires only for a shot with pending
    (unadopted) keyframe candidates."""
    gate = keyframe_gate(project, shot_id)
    if not gate["gated"]:
        return []
    n = gate["adoption"]["candidate_count"]
    return [{
        "code": KEYFRAME_NOT_ADOPTED,
        "severity": "advisory",
        "level": "advisory",  # qc/prompt_checks printer + has_blocking compat
        "message": (
            f"{shot_id}: {n} 个关键帧候选尚未采纳 — 付费视频前先采纳一个关键帧"
            "(select 该 take,或把它提升为 refs first-frame 绑定);"
            "unattended 档会在派发时拒绝未采纳关键帧的付费视频(transport=0)"
        ),
        "suggestion": (
            f"manju select {shot_id} <take> 采纳关键帧,或在 generation.params.refs "
            "写入指向该关键帧字节的 first-frame 绑定"
        ),
        "source_paths": [f"shots/{shot_id}.yaml#/status/selected_take"],
        "proposal": None,
        "auto_apply": False,
    }]
