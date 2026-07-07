"""The project cockpit read model (round V, goal item 4).

`cockpit_data(project)` aggregates — READ-ONLY, no mutation, no new state
machinery — every number the one-glance home needs, drawn straight from the
existing engine surfaces the CLI already exposes:

    identity / state  ← build.status.project_status + build.stale.evaluate_all
    final freshness    ← build.exportstatus.deliverables (the final row's verdict)
    next best action   ← build.director.suggest_next
    risks (exception)  ← project_status (qc / build_lock / crashed-render) + spend
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

from ..build.director import suggest_next
from ..build.exportstatus import deliverables
from ..build.spend import spend_report
from ..build.status import project_status
from ..core.container import Project
from ..core.events import tail_events
from .onboarding import build_onboarding
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
        return ("render", "出片")
    if qc.get("errors"):
        return ("qc", "质检")
    if by.get("stale"):
        return ("update", "更新")
    return ("done", "完成")


def _final_summary(status: dict[str, Any], deliv: Any) -> dict[str, Any] | None:
    lf = status.get("latest_final")
    if not lf:
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
        sentence = "还没有镜头 — 先写分镜再生成 (no shots yet)"
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


def _next_action(project: Project, status: Any, sugg: Any) -> dict[str, Any]:
    """The ONE primary next step (block 3). ``director.suggest_next``'s first
    ACTIONABLE item, mapped to a GUI verb the hero button wires to; the human
    label rides from ``project_status.next_step``."""
    total = status.get("shots_total") if status else len(_safe(project.shot_ids) or [])
    human = status.get("next_step") if status else None
    funnel = _funnel_status(project)

    if not total:  # a brand-new project → point at the storyboard / onboarding
        return {"verb": "story", "text": "先写分镜 · 建镜头 (write the storyboard)",
                "action": None, "shot": None, "kind": "story",
                "human_label": human, "funnel": funnel}

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
                      "text": f"{len(broken)} 个镜头损坏 (broken):{_join(broken)}"})

    qc = status.get("qc") or {}
    if qc.get("errors"):
        items.append({"kind": "qc", "level": "error", "count": qc["errors"],
                      "text": f"质检 {qc['errors']} 处错误 (QC errors)"})

    stale = by.get("stale") or []
    if stale:
        items.append({"kind": "stale", "level": "warn", "count": len(stale),
                      "shots": stale,
                      "text": f"{len(stale)} 个镜头待更新 (stale):{_join(stale)}"})

    if isinstance(spend, dict):
        limit = spend.get("budget_limit")
        total = spend.get("total") or 0.0
        if limit and float(total) >= 0.8 * float(limit):
            over = float(total) >= float(limit)
            cur = spend.get("currency") or ""
            items.append({
                "kind": "budget", "level": "error" if over else "warn",
                "ratio": (float(total) / float(limit)) if limit else None,
                "text": ("已超预算 (over budget):" if over else "接近预算上限 (budget):")
                        + f" {_num(total)} / {_num(limit)} {cur}".rstrip()})

    if status.get("latest_final_note"):
        items.append({"kind": "final", "level": "warn",
                      "detail": status["latest_final_note"],
                      "text": "成片可能不完整 (final may be incomplete)"})

    bl = status.get("build_lock")
    if bl:
        who = bl.get("actor") if isinstance(bl, dict) else None
        items.append({"kind": "lock", "level": "info",
                      "text": "构建进行中 (build in progress)"
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


# --------------------------------------------------------------------- api


def cockpit_data(project: Project) -> dict[str, Any]:
    """The whole cockpit payload — one glance → one action → activity → risk.

    Pure and read-only: never spends, never mutates, never 500s. The four
    expensive shared reads are taken ONCE (each best-effort) and threaded into
    the blocks; every block is independently :func:`_guard`-ed so one poisoned
    source degrades a single block to ``{"error": …}``.
    """
    status = _safe(lambda: project_status(project))
    deliv = _safe(lambda: deliverables(project))
    spend = _safe(lambda: spend_report(project))
    sugg = _safe(lambda: suggest_next(project))

    data: dict[str, Any] = {}
    data["identity"] = _guard(lambda: _identity(project, status))
    data["state"] = _guard(lambda: _state(project, status, deliv))
    data["next_action"] = _guard(lambda: _next_action(project, status, sugg))
    data["suggestions"] = _guard(lambda: _suggestions(sugg, data["next_action"]))
    data["risks"] = _guard(lambda: _risks(project, status, spend))
    data["deliverables"] = _guard(lambda: _deliverables(deliv))
    data["spend"] = _guard(lambda: _spend(spend))
    data["queue"] = _guard(lambda: _queue(status))
    data["activity"] = _guard(lambda: _activity(project))
    data["approvals"] = _guard(lambda: _approvals(project))
    data["onboarding"] = _guard(lambda: _onboarding(project))
    return data
