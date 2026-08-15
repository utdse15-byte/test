"""The project cockpit read model (round V, goal item 4).

`cockpit_data(project)` aggregates — READ-ONLY, no mutation, no new state
machinery — every number the one-glance home needs, drawn straight from the
existing engine surfaces the CLI already exposes:

    identity / state  ← build.status.project_status + build.stale.evaluate_all
    final freshness    ← build.exportstatus.deliverables (the final row's verdict)
    next best action   ← build.director.suggest_next
    risks (exception)  ← project_status (qc / build_lock / crashed-render)
    deliverables       ← build.exportstatus.deliverables
    spend              ← build.spend.spend_report
    queue              ← project_status.run_log.pending_jobs (the resume queue)
    activity           ← core.events.tail_events
    approvals          ← ShotStatus.review_state (the storyboard 审批 chip's source)
    onboarding         ← gui.onboarding.build_onboarding (fresh-project checklist)

The cockpit is PRESENTATION, not engine: it never computes a build verdict
itself, it only re-frames what these functions already report — exactly the
stance :mod:`manju.gui.state` takes for the SPA poll. This mirrors the block
layout of REPORTS/ROUND-V-REFERENCES-2.md §"Cockpit block layout" (state → one
action → activity → risk-by-exception).

Every block degrades INDEPENDENTLY: each is computed inside :func:`_guard`, so a
single poisoned source (an unreadable qc.json, an uncompilable timeline, a torn
ledger) collapses to ``{"error": "一句话"}`` for that block alone — the cockpit
never 500s and its neighbours still render. The three expensive shared reads
(``project_status``, ``deliverables``, ``spend_report``, ``suggest_next``) are
taken ONCE and threaded into the blocks that need them.

``series`` (core/series.py) and ``funnel`` (build/funnel.py) may land in a
parallel round-V branch; both are consulted behind an import guard and degrade
silently to ``None`` when absent, so this module builds against the current base
and lights up automatically if they arrive.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..build.director import suggest_next
from ..build.exportstatus import deliverables
from ..build.spend import spend_report
from ..build.stale import evaluate_all
from ..build.status import project_status
from ..core.container import Project
from ..core.events import tail_events
from .onboarding import build_onboarding
from .authoring_journey import authoring_journey_payload
from .shot_journey import shot_production_summary
from .userstate import is_onboarding_dismissed

__all__ = ["cockpit_data"]

# The picture-side shot states, in the order the state strip renders them
# (blockers first, 就绪 last) — pre-attentive: an exception colour leads.
STATE_ORDER = ("missing", "needs_selection", "broken", "stale", "manual", "fresh")
STATE_ZH = {
    "missing": "缺失", "needs_selection": "待挑选", "broken": "损坏",
    "stale": "待更新", "manual": "人工", "fresh": "就绪",
}


# --------------------------------------------------------------- degradation


def _safe(fn):
    """Run a shared read, degrading to ``None`` on any failure (§3 disposable
    reads must never raise into the cockpit)."""
    try:
        return fn()
    except Exception:
        return None


def _guard(fn) -> Any:
    """Run one block, degrading to ``{"error": 一句话}`` on any failure — a
    poisoned source collapses ONE block, never the whole cockpit."""
    try:
        return fn()
    except Exception as exc:
        msg = " ".join(str(exc).split())[:200]
        return {"error": msg or "读取失败 (unavailable)"}


# ------------------------------------------------------------------- helpers


def _num(v: Any) -> str:
    try:
        return format(float(v), "g")
    except (TypeError, ValueError):
        return str(v)


def _join(ids: Any, n: int = 5) -> str:
    ids = list(ids or [])
    return "、".join(str(x) for x in ids[:n]) + ("…" if len(ids) > n else "")


def _event_summary(detail: Any) -> str:
    """A one-token summary of an event's detail dict for the activity feed."""
    if not isinstance(detail, dict):
        return ""
    for k in ("shot", "id", "take", "op", "target", "kind", "count", "actions", "path"):
        v = detail.get(k)
        if v not in (None, "", [], {}):
            return f"{k}={v}"
    return ""


def _series_membership(project: Project) -> Any:
    """Series membership, guarded — core/series.py may land in parallel (§V).
    Absent → ``None`` (degrade silently); present but unexpected shape → ``None``."""
    try:
        from ..core import series  # type: ignore
    except Exception:
        return None
    for name in ("cockpit_membership", "membership", "series_of", "project_series"):
        fn = getattr(series, name, None)
        if callable(fn):
            try:
                res = fn(project)
            except Exception:
                return None
            return res if isinstance(res, (dict, list)) else None
    return None


def _funnel_status(project: Project) -> Any:
    """Funnel status, guarded — build/funnel.py may land in parallel (§V)."""
    try:
        from ..build import funnel  # type: ignore
    except Exception:
        return None
    for name in ("funnel_status", "cockpit_status", "status", "funnel"):
        fn = getattr(funnel, name, None)
        if callable(fn):
            try:
                res = fn(project)
            except Exception:
                return None
            return res if isinstance(res, (dict, list)) else None
    return None


# ------------------------------------------------------------ block builders


def _identity(project: Project, status: Any) -> dict[str, Any]:
    if status is None:
        raise RuntimeError("项目状态不可用 (status unavailable)")
    cfg = project.load_config()
    return {
        "name": status.get("project"),
        "preset": status.get("preset"),
        "mode": status.get("mode"),
        "resolution": status.get("resolution"),
        "width": cfg.width,
        "height": cfg.height,
        "fps": cfg.fps,
    }


def _phase(status: dict[str, Any]) -> tuple[str, str]:
    """A coarse project phase (key, 中文) derived from the SAME signals
    ``project_status`` already computed — never a new state machine."""
    by = status.get("shots_by_state") or {}
    total = status.get("shots_total") or 0
    tl = status.get("timeline") or {}
    qc = status.get("qc") or {}
    if total == 0:
        return ("brief", "创作")
    if by.get("missing"):
        return ("generate", "生成")
    if by.get("needs_selection"):
        return ("select", "挑选")
    if by.get("broken"):
        return ("repair", "修复")
    if not tl.get("exists"):
        return ("compile", "合成")
    if not status.get("latest_final"):
        # C15: locale-only deliverables already count as "has film" for phase.
        if status.get("locale_finals"):
            pass  # fall through toward done/qc/stale
        else:
            return ("render", "出片")
    if qc.get("errors"):
        return ("qc", "质检")
    if by.get("stale"):
        return ("update", "更新")
    # A build holding the lock RIGHT NOW is not "完成". The headline said so in
    # large type while the risk banner directly beneath it said 构建进行中, and
    # two adjacent lines contradicting each other is worse than either alone —
    # the reader stops trusting the big one. Same signal the banner already
    # uses (status["build_lock"]), so the two can no longer disagree.
    if status.get("build_lock"):
        return ("building", "构建中")
    return ("done", "完成")


def _final_summary(status: dict[str, Any], deliv: Any) -> dict[str, Any] | None:
    lf = status.get("latest_final")
    if not lf:
        # C15: show a locale path when base is empty.
        locs = status.get("locale_finals") or {}
        if locs:
            lang = sorted(locs)[0]
            return {
                "path": locs[lang], "version": None, "freshness": None,
                "freshness_zh": None, "basis": None,
                "note": status.get("latest_final_note") or f"locale {lang}",
            }
        return None
    out = {
        "path": lf, "version": None, "freshness": None,
        "freshness_zh": None, "basis": None, "note": status.get("latest_final_note"),
    }
    if deliv:
        row = next((r for r in deliv if r.kind == "final"), None)
        if row is not None:
            out["version"] = row.version
            out["freshness"] = row.freshness.value
            out["freshness_zh"] = row.freshness_zh
            out["basis"] = row.basis
    return out


def _state(project: Project, status: Any, deliv: Any) -> dict[str, Any]:
    if status is None:
        raise RuntimeError("项目状态不可用 (status unavailable)")
    by = status.get("shots_by_state") or {}
    counts = {k: len(v) for k, v in by.items()}
    total = status.get("shots_total") or 0
    phase, phase_zh = _phase(status)
    voice = {k: len(v) for k, v in (status.get("voice_by_state") or {}).items()}

    if total == 0:
        sentence = "还没有镜头 — 先完成创作和分镜"
    else:
        parts = [f"{counts[s]} {STATE_ZH[s]}" for s in STATE_ORDER if counts.get(s)]
        sentence = f"{phase_zh} · 共 {total} 镜:" + " · ".join(parts)

    return {
        "phase": phase,
        "phase_zh": phase_zh,
        "sentence": sentence,
        "shots_total": total,
        "shots_by_state": {s: counts[s] for s in STATE_ORDER if s in counts},
        "voice_by_state": voice,
        "final": _final_summary(status, deliv),
        "series": _series_membership(project),
    }


def _next_action(
    project: Project, status: Any, sugg: Any, funnel: Any = None
) -> dict[str, Any]:
    """The ONE primary next step (block 3). ``director.suggest_next``'s first
    ACTIONABLE item, mapped to a GUI verb the hero button wires to; the human
    label rides from ``project_status.next_step``."""
    total = status.get("shots_total") if status else len(_safe(project.shot_ids) or [])
    human = status.get("next_step") if status else None
    if not total:  # a brand-new project → point at the storyboard / onboarding
        return {"verb": "story", "text": "先写分镜 · 建镜头 (write the storyboard)",
                "action": None, "shot": None, "kind": "story",
                "human_label": human, "funnel": funnel}

    # C50: status next_step_key build_locale wins when base is done but
    # locale lines still lack finals (see build/status.py C49).
    if isinstance(status, dict) and status.get("next_step_key") == "build_locale":
        # C55: extract first lang from next_step text for hero build params.
        lang_hint = None
        label = human or ""
        if "--lang " in label:
            try:
                lang_hint = label.split("--lang ", 1)[1].split()[0].strip()
            except Exception:
                lang_hint = None
        action = {"type": "build", "target": "final"}
        if lang_hint:
            action["lang"] = lang_hint
        return {
            "verb": "build",
            "text": human or "构建 locale 成片 (build locale final)",
            "action": action,
            "shot": None,
            "kind": "build_locale",
            "human_label": human,
            "funnel": funnel,
        }

    items = list(sugg or [])
    primary = next((s for s in items if s.action is not None), None)
    if primary is None and items:
        primary = items[0]
    if primary is None:  # nothing suggested but shots exist → everything current
        return {"verb": "done", "text": "全部就绪 · 可导出 (all set — export)",
                "action": None, "shot": None, "kind": "done",
                "human_label": human, "funnel": funnel}

    action = primary.action
    verb = "none"
    if action is not None:
        verb = {
            "build": "build", "redo": "redo", "voice": "redo", "repair": "repair",
            "packaging": "package", "captions": "package", "mixer": "package",
        }.get(action.get("type"), "none")
    elif primary.kind == "select":
        verb = "select"

    return {"verb": verb, "text": primary.text, "action": action,
            "shot": primary.shot, "kind": primary.kind,
            "human_label": human, "funnel": funnel}


def _suggestions(sugg: Any, next_action: Any) -> dict[str, Any]:
    """The REST of ``suggest_next`` (block 8) — everything except the one shown
    as the hero, so a nudge never appears twice."""
    if sugg is None:
        raise RuntimeError("建议不可用 (suggestions unavailable)")
    hero_text = next_action.get("text") if isinstance(next_action, dict) else None
    out = [s.to_dict() for s in sugg if s.text != hero_text]
    return {"items": out[:7]}


def _risks(project: Project, status: Any, spend: Any) -> dict[str, Any]:
    """Risks by EXCEPTION only (Linear model: an empty list is a calm project).
    Every item carries a ``level`` (error|warn|info) so the render colours by
    severity — pre-attentive red for the real blockers."""
    if status is None:
        raise RuntimeError("项目状态不可用 (status unavailable)")
    items: list[dict[str, Any]] = []
    by = status.get("shots_by_state") or {}

    broken = by.get("broken") or []
    if broken:
        items.append({"kind": "broken", "level": "error", "count": len(broken),
                      "shots": broken,
                      "text": f"{len(broken)} 个镜头媒体损坏：{_join(broken)}"})

    qc = status.get("qc") or {}
    if qc.get("errors"):
        items.append({"kind": "qc", "level": "error", "count": qc["errors"],
                      "text": f"质检发现 {qc['errors']} 处错误"})

    stale = by.get("stale") or []
    if stale:
        items.append({"kind": "stale", "level": "warn", "count": len(stale),
                      "shots": stale,
                      "text": f"{len(stale)} 个镜头内容已经变化：{_join(stale)}"})

    if status.get("latest_final_note"):
        items.append({"kind": "final", "level": "warn",
                      "detail": status["latest_final_note"],
                      "text": "当前成片可能不完整"})

    bl = status.get("build_lock")
    if bl:
        who = bl.get("actor") if isinstance(bl, dict) else None
        items.append({"kind": "lock", "level": "info",
                      "text": "构建正在进行"
                              + (f" · {who}" if who else "")})

    return {"items": items}


def _deliverables(deliv: Any) -> dict[str, Any]:
    if deliv is None:
        raise RuntimeError("导出状态不可用 (deliverables unavailable)")
    rows = [{"kind": r.kind, "label": r.label, "freshness": r.freshness.value,
             "freshness_zh": r.freshness_zh, "version": r.version,
             "path": r.path, "basis": r.basis} for r in deliv]
    counts: dict[str, int] = {}
    for r in deliv:
        counts[r.freshness.value] = counts.get(r.freshness.value, 0) + 1
    return {"rows": rows, "counts": counts}


def _spend(spend: Any) -> dict[str, Any]:
    if spend is None:
        raise RuntimeError("花费报告不可用 (spend unavailable)")
    return {
        "total": spend.get("total"),
        "currency": spend.get("currency"),
        "budget_limit": spend.get("budget_limit"),
        "estimated_total": spend.get("estimated_total"),
        "delta": spend.get("delta"),
        "by_provider": (spend.get("by_provider") or [])[:5],
        "source": spend.get("source"),
    }


def _queue(status: Any) -> dict[str, Any]:
    if status is None:
        raise RuntimeError("项目状态不可用 (status unavailable)")
    rl = status.get("run_log") or {}
    return {"pending_cloud": int(rl.get("pending_jobs") or 0)}


def _activity(project: Project) -> dict[str, Any]:
    events = tail_events(project.root, 8)
    events = list(reversed(events))  # newest first (the F-pattern top)
    return {"events": [
        {"ts": e.get("ts"), "actor": e.get("actor"), "action": e.get("action"),
         "summary": _event_summary(e.get("detail"))}
        for e in events if isinstance(e, dict)]}


def _approvals(project: Project) -> dict[str, Any]:
    """Storyboard review (审批) state — the Frame.io three-state review pending
    count, straight off :attr:`ShotStatus.review_state`. Only shots with a
    selected take (something to actually review) count as pending."""
    pending: list[dict[str, str]] = []
    by_state: dict[str, int] = {}
    for sid in (_safe(project.shot_ids) or []):
        try:
            shot = project.load_shot(sid)
        except Exception:
            continue
        rs = shot.status.review_state
        by_state[rs] = by_state.get(rs, 0) + 1
        if shot.status.selected_take and rs != "approved":
            pending.append({"shot": sid, "review": rs})
    return {"pending": len(pending), "shots": pending[:12], "by_state": by_state}


def _onboarding(project: Project) -> dict[str, Any]:
    ob = build_onboarding(project)
    ob["dismissed"] = is_onboarding_dismissed(project.root)
    ob["should_show"] = bool(ob.get("empty_ish")) and not ob["dismissed"]
    return ob


def _journeys(
    project: Project, state: Any, *, funnel: Any = None
) -> dict[str, Any]:
    """Summarise the three product journeys already owned elsewhere.

    This is deliberately a presentation projection, not a new workflow state
    machine.  Authoring and shot-production reuse their existing read models;
    finishing is framed from the cockpit's already-computed final evidence plus
    the shot journey's human approval counts.  Nothing here is persisted or
    read back by build/provider/QC code.
    """

    authoring_error = ""
    try:
        authoring_raw = authoring_journey_payload(
            project, funnel_status_data=funnel
        )
    except Exception as exc:
        authoring_error = " ".join(str(exc).split())[:180] or exc.__class__.__name__
        authoring_raw = {
            "writing": {"done": 0, "total": 0, "complete": False},
            "proposal": {"truth_pending": 0, "truth_stale": 0, "error": authoring_error},
            "storyboard": {"ready": False, "count": 0},
            "next": {
                "kind": "writing",
                "href": "/create",
                "label": "检查创作材料",
                "detail": "创作进度暂时无法读取；项目文件没有被修改。",
            },
        }
    writing = authoring_raw.get("writing") or {}
    proposal = authoring_raw.get("proposal") or {}
    storyboard = authoring_raw.get("storyboard") or {}
    authoring_next = authoring_raw.get("next") or {}
    if authoring_error:
        authoring_state = "unavailable"
    elif (
        proposal.get("error")
        or proposal.get("truth_pending")
        or proposal.get("truth_stale")
        or proposal.get("other_pending")
    ):
        authoring_state = "attention"
    elif not writing.get("complete") or not storyboard.get("ready"):
        authoring_state = "current"
    else:
        authoring_state = "done"
    authoring_summary = (
        f"故事材料 {int(writing.get('done') or 0)}/{int(writing.get('total') or 0)}"
        f" · 分镜 {int(storyboard.get('count') or 0)} 镜"
    )
    if proposal.get("truth_pending"):
        authoring_summary += f" · {int(proposal['truth_pending'])} 份创作提案待决定"
    elif proposal.get("other_pending"):
        authoring_summary += f" · {int(proposal['other_pending'])} 份操作提案待决定"

    try:
        shots_raw = shot_production_summary(project)
    except Exception as exc:
        shot_error = " ".join(str(exc).split())[:180] or exc.__class__.__name__
        shots_raw = {
            "total": 0, "with_candidates": 0, "selected": 0,
            "reviewed": 0, "approved": 0, "broken": [],
            "error": shot_error,
            "next": {
                "kind": "plan", "href": "/storyboard",
                "label": "检查镜头计划",
            },
        }
    total = int(shots_raw.get("total") or 0)
    with_candidates = int(shots_raw.get("with_candidates") or 0)
    selected = int(shots_raw.get("selected") or 0)
    reviewed = int(shots_raw.get("reviewed") or 0)
    approved = int(shots_raw.get("approved") or 0)
    if shots_raw.get("error") or shots_raw.get("broken"):
        shot_state = "attention"
    elif total <= 0:
        shot_state = "todo"
    elif approved >= total:
        shot_state = "done"
    else:
        shot_state = "current"
    shot_next = shots_raw.get("next") or {}
    shot_summary = (
        "尚无镜头"
        if total <= 0
        else (
            f"{with_candidates}/{total} 有候选 · "
            f"{selected}/{total} 已选择 · {approved}/{total} 已通过"
        )
    )

    final = None if not isinstance(state, dict) or state.get("error") else state.get("final")
    if shots_raw.get("error"):
        finish_state = "unavailable"
        finish_summary = "成片准备状态暂不可用"
        finish_detail = "镜头进度暂时无法读取；不会因此修改项目。"
        finish_href = "/edit"
        finish_next = "检查成片工作区"
    elif total <= 0:
        finish_state = "todo"
        finish_summary = "等待镜头计划"
        finish_detail = "先完成故事与分镜，再进入剪辑和交付。"
        finish_href = "/create"
        finish_next = "先完成创作与分镜"
    elif approved < total:
        finish_state = "todo"
        finish_summary = f"等待镜头通过 · {approved}/{total}"
        finish_detail = "先完成候选选择、评价和镜头审批。"
        finish_href = str(shot_next.get("href") or "/review")
        finish_next = str(shot_next.get("label") or "继续镜头制作")
    elif not final:
        finish_state = "current"
        finish_summary = "镜头已通过 · 尚无成片"
        finish_detail = "进入剪辑并生成第一版成片。"
        finish_href = "/edit"
        finish_next = "进入剪辑"
    else:
        freshness = str(final.get("freshness") or "")
        version = str(final.get("version") or "").strip()
        suffix = f" {version}" if version else ""
        if freshness in {"up_to_date", "verified"}:
            finish_state = "done"
            finish_summary = f"成片{suffix} 当前有效"
            finish_detail = "可以继续精剪、导出或交付。"
            finish_href = "/exports"
            finish_next = "查看成片与导出"
        elif freshness == "stale":
            finish_state = "attention"
            finish_summary = f"成片{suffix} 待更新"
            finish_detail = "项目内容已经变化，需要重新生成当前成片。"
            finish_href = "/edit"
            finish_next = "更新成片"
        elif freshness == "problematic":
            finish_state = "attention"
            finish_summary = f"成片{suffix} 有问题"
            finish_detail = "先检查成片问题，再继续导出或交付。"
            finish_href = "/exports"
            finish_next = "检查成片问题"
        elif freshness == "needs_manual":
            finish_state = "attention"
            finish_summary = f"成片{suffix} 待人工确认"
            finish_detail = "当前文件存在，但仍需要你检查后再交付。"
            finish_href = "/exports"
            finish_next = "检查当前成片"
        else:
            finish_state = "current"
            finish_summary = f"成片{suffix} 状态待确认"
            finish_detail = str(final.get("note") or "进入成片工作区检查当前结果。")
            finish_href = "/exports"
            finish_next = "查看成片状态"

    return {
        "authoring": {
            "state": authoring_state,
            "label": "创作",
            "summary": authoring_summary,
            "detail": str(authoring_next.get("detail") or ""),
            "href": str(authoring_next.get("href") or "/create"),
            "next_label": str(authoring_next.get("label") or "继续创作"),
            "next_kind": str(authoring_next.get("kind") or "writing"),
            "progress": {
                "done": int(writing.get("done") or 0),
                "total": int(writing.get("total") or 0),
            },
            "attention": {
                "truth_pending": int(proposal.get("truth_pending") or 0),
                "truth_stale": int(proposal.get("truth_stale") or 0),
                "other_pending": int(proposal.get("other_pending") or 0),
                "error": authoring_error or str(proposal.get("error") or ""),
            },
        },
        "shots": {
            "state": shot_state,
            "label": "镜头",
            "summary": shot_summary,
            "detail": (
                "候选、选择、评价和镜头审批始终是独立决定。"
                if total else "先把故事拆成可执行镜头。"
            ),
            "href": str(shot_next.get("href") or "/storyboard"),
            "next_label": str(shot_next.get("label") or "查看镜头计划"),
            "next_kind": str(shot_next.get("kind") or "plan"),
            "shot": shot_next.get("shot"),
            "progress": {
                "total": total,
                "with_candidates": with_candidates,
                "selected": selected,
                "reviewed": reviewed,
                "approved": approved,
            },
            "broken": list(shots_raw.get("broken") or []),
            "error": str(shots_raw.get("error") or ""),
        },
        "finishing": {
            "state": finish_state,
            "label": "成片",
            "summary": finish_summary,
            "detail": finish_detail,
            "href": finish_href,
            "next_label": finish_next,
            "next_kind": "exports" if finish_state == "done" else "finishing",
        },
    }


def _focus(journeys: Any, next_action: Any, risks: Any) -> dict[str, Any]:
    """Choose one presentation priority without becoming an execution owner."""

    def _link(source: str, row: dict[str, Any]) -> dict[str, Any]:
        summary = str(row.get("summary") or "").strip()
        detail = str(row.get("detail") or "").strip()
        if summary and detail and summary not in detail:
            focus_detail = summary + "。" + detail
        else:
            focus_detail = detail or summary
        return {
            "source": source,
            "phase": row.get("label") or "项目",
            "kind": row.get("next_kind") or source,
            "label": row.get("next_label") or "继续",
            "detail": focus_detail,
            "href": row.get("href"),
            "verb": "link",
            "action": None,
            "shot": row.get("shot"),
        }

    risk_items = [] if not isinstance(risks, dict) else list(risks.get("items") or [])
    broken = next((row for row in risk_items if row.get("kind") == "broken"), None)
    if broken:
        shots = list(broken.get("shots") or [])
        first = str(shots[0]) if shots else None
        return {
            "source": "risk",
            "phase": "修复",
            "kind": "broken",
            "label": f"修复 {int(broken.get('count') or len(shots) or 1)} 个损坏镜头",
            "detail": "先恢复当前镜头媒体，再继续生成、审片或成片。",
            "href": "/storyboard" + (f"?shot={quote(first, safe='')}" if first else ""),
            "verb": "link",
            "action": None,
            "shot": first,
        }
    qc = next((row for row in risk_items if row.get("kind") == "qc"), None)
    if qc:
        return {
            "source": "risk",
            "phase": "质检",
            "kind": "qc",
            "label": f"处理 {int(qc.get('count') or 1)} 处质检问题",
            "detail": "先处理阻塞问题，再继续审批和交付。",
            "href": "/review",
            "verb": "link",
            "action": None,
            "shot": None,
        }

    if not isinstance(journeys, dict):
        journeys = {}
    authoring = journeys.get("authoring") or {}
    if authoring.get("state") in {"attention", "current"}:
        return _link("authoring", authoring)

    stale = next((row for row in risk_items if row.get("kind") == "stale"), None)
    if stale:
        shots = list(stale.get("shots") or [])
        first = str(shots[0]) if shots else None
        return {
            "source": "risk",
            "phase": "镜头",
            "kind": "stale",
            "label": f"更新 {first} 的候选" if first else "更新过期镜头",
            "detail": "镜头内容已经变化；重新准备候选，或明确保留当前选择。",
            "href": "/lab" + (f"?shot={quote(first, safe='')}" if first else ""),
            "verb": "link",
            "action": None,
            "shot": first,
        }

    shots = journeys.get("shots") or {}
    if shots.get("state") in {"attention", "current", "todo"}:
        return _link("shots", shots)

    # Once authoring and human shot decisions are settled, preserve the engine's
    # exact build/package action so the existing dry-run and spend-confirmation
    # modal remains the sole execution path.
    if isinstance(next_action, dict) and next_action.get("verb") in {
        "build", "redo", "repair", "package",
    }:
        verb = str(next_action.get("verb"))
        shot = next_action.get("shot")
        labels = {
            "build": "生成或更新当前成片",
            "redo": f"更新 {shot} 的候选" if shot else "更新镜头候选",
            "repair": f"修复 {shot}" if shot else "修复当前问题",
            "package": "更新成片包装",
        }
        details = {
            "build": "先查看构建计划和费用试算，再决定是否执行。",
            "redo": "先查看将重做的镜头、参考和费用试算，再决定是否执行。",
            "repair": "先检查本地媒体和修复计划；现有原件不会被覆盖。",
            "package": "先查看会更新的包装与交付物，再决定是否执行。",
        }
        return {
            "source": "engine",
            "phase": "成片" if verb in {"build", "package"} else "镜头",
            "kind": str(next_action.get("kind") or verb),
            "label": labels[verb],
            "detail": details[verb],
            "href": None,
            "verb": verb,
            "action": next_action.get("action"),
            "shot": shot,
        }

    finishing = journeys.get("finishing") or {}
    if finishing:
        return _link("finishing", finishing)

    return {
        "source": "fallback",
        "phase": "项目",
        "kind": "details",
        "label": "查看项目详情",
        "detail": "当前没有明确的下一项；展开项目详情检查状态。",
        "href": "#workbench-details",
        "verb": "link",
        "action": None,
        "shot": None,
    }


# --------------------------------------------------------------------- api


def cockpit_data(project: Project) -> dict[str, Any]:
    """Build one internally consistent cockpit snapshot from current truth."""
    from ..core.yamlio import yaml_read_snapshot

    with yaml_read_snapshot():
        return _cockpit_data(project)


def _cockpit_data(project: Project) -> dict[str, Any]:
    """The whole cockpit payload — one glance → one action → activity → risk.

    Pure and read-only: never spends, never mutates, never 500s. The four
    expensive shared reads are taken ONCE (each best-effort) and threaded into
    the blocks; every block is independently :func:`_guard`-ed so one poisoned
    source degrades a single block to ``{"error": …}``.
    """
    # The creation funnel runs `manju check` and is the most expensive shared
    # authoring read on large projects.  The cockpit needs it in three places
    # (engine suggestions, the hero action, and the authoring journey), so take
    # it once and thread the same current facts through all three consumers.
    funnel = _safe(lambda: _funnel_status(project))
    statuses = _safe(lambda: evaluate_all(project))
    status = _safe(lambda: project_status(project, statuses=statuses))
    deliv = _safe(lambda: deliverables(project))
    spend = _safe(lambda: spend_report(project))
    sugg = _safe(
        lambda: suggest_next(
            project, funnel_status_data=funnel, statuses=statuses
        )
    )

    data: dict[str, Any] = {}
    data["identity"] = _guard(lambda: _identity(project, status))
    data["state"] = _guard(lambda: _state(project, status, deliv))
    data["next_action"] = _guard(
        lambda: _next_action(project, status, sugg, funnel)
    )
    data["suggestions"] = _guard(lambda: _suggestions(sugg, data["next_action"]))
    data["risks"] = _guard(lambda: _risks(project, status, spend))
    data["deliverables"] = _guard(lambda: _deliverables(deliv))
    data["spend"] = _guard(lambda: _spend(spend))
    data["queue"] = _guard(lambda: _queue(status))
    data["activity"] = _guard(lambda: _activity(project))
    data["approvals"] = _guard(lambda: _approvals(project))
    data["onboarding"] = _guard(lambda: _onboarding(project))
    data["journeys"] = _guard(
        lambda: _journeys(project, data["state"], funnel=funnel)
    )
    data["focus"] = _guard(
        lambda: _focus(data["journeys"], data["next_action"], data["risks"])
    )
    return data
