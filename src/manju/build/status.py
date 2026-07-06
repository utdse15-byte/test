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
from .stale import evaluate_all


def _latest_final(project: Project):
    finals = sorted(project.final_dir.glob("final_v*.mp4"))
    return finals[-1] if finals else None


def project_status(project: Project) -> dict[str, Any]:
    config = project.load_config()
    statuses = evaluate_all(project)
    by_state: dict[str, list[str]] = {}
    for st in statuses:
        by_state.setdefault(st.state.value, []).append(st.shot_id)

    # voice states (M3): mirror of the picture-side summary, keyed by the
    # voice_hash staleness anchor; not_needed shots are omitted for signal.
    voice_by_state: dict[str, list[str]] = {}
    try:
        from .voice import VoiceState, evaluate_all_voices

        for vs in evaluate_all_voices(project):
            if vs.state != VoiceState.NOT_NEEDED:
                voice_by_state.setdefault(vs.state.value, []).append(vs.shot_id)
    except Exception:
        pass  # voice summary is advisory

    total_cost = 0.0
    currency = config.budget.currency
    for st in statuses:
        for take in project.takes(st.shot_id):
            if take.sidecar.remote and take.sidecar.remote.cost:
                total_cost += take.sidecar.remote.cost
                currency = take.sidecar.remote.currency

    # Run ledger snapshot (§8.3), best-effort — the SQLite state is disposable
    # (§3), so any failure degrades to an "unavailable" marker, never an error.
    run_log_info: dict[str, Any] = {
        "runs": 0, "total_cost": 0.0, "currency": None,
        "note": "state.sqlite unavailable",
    }
    try:
        from ..runtime.state import RuntimeState

        with RuntimeState(project.root) as state:
            total, cur = state.total_cost()
            run_log_info = {
                "runs": len(state.run_log(100000)),  # no COUNT API; count the log
                "total_cost": float(total),
                "currency": cur,
                # in-flight cloud jobs: the resume-polling queue (§8.1) — the
                # one runtime-only state, so surface it at the takeover entry
                "pending_jobs": len(state.pending_jobs()),
            }
    except Exception:
        pass  # keep the "unavailable" marker above

    # The ledger is authoritative once populated; the sidecar-derived sum is the
    # §3 rebuild source and the fallback when the ledger has no runs yet.
    if run_log_info.get("runs"):
        total_cost = run_log_info["total_cost"]
        if run_log_info.get("currency"):
            currency = run_log_info["currency"]

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

    # Preset label + advisory qc_focus (P3): purely a record the preset wrote
    # at `manju new` time; surfaced here so a takeover sees what to watch for.
    qc_focus = config.model_dump().get("qc_focus") or []

    return {
        "project": config.name,
        "preset": config.preset,
        "qc_focus": qc_focus,
        "mode": config.mode,
        "resolution": f"{config.width}x{config.height}@{config.fps}",
        "shots_total": len(statuses),
        "shots_by_state": by_state,
        "voice_by_state": voice_by_state,
        "timeline": {
            "exists": timeline is not None,
            "duration_ms": timeline.duration_ms if timeline else None,
            "mode": timeline.meta.mode if timeline else None,
        },
        "latest_final": project.relpath(final) if final else None,
        "qc": qc_summary,
        "total_cost": total_cost,
        "currency": currency,
        "run_log": run_log_info,
        "budget_limit": config.budget.limit,
        "recent_events": tail_events(project.root, 5),
        "next_step": next_step,
    }
