"""`manju status` — the handover entry point (§10).

Either party takes over with one command: current phase, which shots are
missing/stale/awaiting approval, QC leftovers, cumulative spend, suggested
next step. 30 seconds to context.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.container import Project
from ..core.events import tail_events
from .stale import ShotState, evaluate_all


def _latest_final(project: Project):
    finals = sorted(project.final_dir.glob("final_v*.mp4"))
    return finals[-1] if finals else None


def project_status(project: Project) -> dict[str, Any]:
    config = project.load_config()
    statuses = evaluate_all(project)
    by_state: dict[str, list[str]] = {}
    for st in statuses:
        by_state.setdefault(st.state.value, []).append(st.shot_id)

    total_cost = 0.0
    currency = config.budget.currency
    for st in statuses:
        for take in project.takes(st.shot_id):
            if take.sidecar.remote and take.sidecar.remote.cost:
                total_cost += take.sidecar.remote.cost
                currency = take.sidecar.remote.currency

    timeline = project.load_timeline()
    final = _latest_final(project)

    qc_summary = None
    qc_path = project.reports_dir / "qc.json"
    if qc_path.exists():
        try:
            qc_data = json.loads(qc_path.read_text(encoding="utf-8"))
            items = qc_data.get("items", [])
            qc_summary = {
                "ok": qc_data.get("ok"),
                "errors": sum(1 for i in items if i.get("level") == "error"),
                "warnings": sum(1 for i in items if i.get("level") == "warn"),
            }
        except (json.JSONDecodeError, OSError):
            qc_summary = {"ok": None, "note": "qc.json unreadable"}

    # suggested next step, in build order
    if not statuses:
        next_step = "创作阶段:先写 shots/(引擎不编故事,§2)"
    elif by_state.get("missing"):
        next_step = f"manju build(补齐缺失镜头:{', '.join(by_state['missing'][:5])}…)" \
            if len(by_state.get("missing", [])) > 5 else \
            f"manju build(补齐缺失镜头:{', '.join(by_state['missing'])})"
    elif by_state.get("needs_selection"):
        next_step = f"manju select(待挑选:{', '.join(by_state['needs_selection'])})"
    elif by_state.get("broken"):
        next_step = f"修复 broken 镜头:{', '.join(by_state['broken'])}"
    elif timeline is None:
        next_step = "manju build(编译时间线并渲染)"
    elif final is None:
        next_step = "manju build --target final"
    elif qc_summary and qc_summary.get("errors"):
        next_step = "manju repair / 处理 reports/qc.md 中的错误"
    else:
        next_step = "已可出片;stale 镜头可用 manju redo 重做" if by_state.get("stale") else "完成 ✅"

    return {
        "project": config.name,
        "mode": config.mode,
        "resolution": f"{config.width}x{config.height}@{config.fps}",
        "shots_total": len(statuses),
        "shots_by_state": by_state,
        "timeline": {
            "exists": timeline is not None,
            "duration_ms": timeline.duration_ms if timeline else None,
            "mode": timeline.meta.mode if timeline else None,
        },
        "latest_final": project.relpath(final) if final else None,
        "qc": qc_summary,
        "total_cost": total_cost,
        "currency": currency,
        "budget_limit": config.budget.limit,
        "recent_events": tail_events(project.root, 5),
        "next_step": next_step,
    }
