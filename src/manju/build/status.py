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


def project_status(project: Project, *, statuses: Any = None,
                   voices: Any = None) -> dict[str, Any]:
    """``statuses`` / ``voices`` (G2): a caller that already ran
    :func:`evaluate_all` / :func:`~manju.build.voice.evaluate_all_voices` (the
    GUI's ``build_state`` does, for the per-shot cards) may pass them so this
    does not recompute the same linear pass. Both default to ``None`` →
    computed here, so every standalone caller (``manju status``) is unchanged."""
    config = project.load_config()
    if statuses is None:
        statuses = evaluate_all(project)
    by_state: dict[str, list[str]] = {}
    notes: dict[str, str] = {}
    for st in statuses:
        by_state.setdefault(st.state.value, []).append(st.shot_id)
        if st.note:  # why-stale field evidence rides to the takeover surface
            notes[st.shot_id] = st.note

    # voice states (M3): mirror of the picture-side summary, keyed by the
    # voice_hash staleness anchor; not_needed shots are omitted for signal.
    voice_by_state: dict[str, list[str]] = {}
    try:
        from .voice import VoiceState, evaluate_all_voices

        voice_list = voices if voices is not None else evaluate_all_voices(project)
        for vs in voice_list:
            if vs.state != VoiceState.NOT_NEEDED:
                voice_by_state.setdefault(vs.state.value, []).append(vs.shot_id)
    except Exception:
        pass  # voice summary is advisory

    # goal 79: NEVER sum across currencies into one silently-mislabeled number
    # — group per currency first (sidecar_by_currency), then derive the
    # single-number total_cost/currency fields ONLY when there is exactly one
    # currency in play (kept for backward-compat callers); a genuinely mixed
    # ledger sets currency=None rather than whatever the LAST take happened
    # to carry (the actual bug: the old loop overwrote `currency` on every
    # iteration regardless of whether it matched the running total).
    sidecar_totals: dict[str | None, float] = {}
    for st in statuses:
        for take in project.takes(st.shot_id):
            remote = take.sidecar.remote
            if remote and remote.cost:
                sidecar_totals[remote.currency] = (
                    sidecar_totals.get(remote.currency, 0.0) + float(remote.cost))
    sidecar_by_currency = [
        {"currency": cur, "cost": round(cost, 6)}
        for cur, cost in sorted(sidecar_totals.items(), key=lambda kv: kv[1], reverse=True)
    ]
    if len(sidecar_by_currency) == 1:
        total_cost = sidecar_by_currency[0]["cost"]
        currency = sidecar_by_currency[0]["currency"]
    elif sidecar_by_currency:  # genuinely mixed -> a raw sum is not a real number
        total_cost = sum(c["cost"] for c in sidecar_by_currency)
        currency = None
    else:
        total_cost = 0.0
        currency = config.budget.currency

    # Run ledger snapshot (§8.3), best-effort — the SQLite state is disposable
    # (§3), so any failure degrades to an "unavailable" marker, never an error.
    run_log_info: dict[str, Any] = {
        "runs": 0, "total_cost": 0.0, "currency": None, "by_currency": [],
        "note": "state.sqlite unavailable",
    }
    try:
        from ..runtime.state import RuntimeState

        with RuntimeState(project.root) as state:
            total, cur = state.total_cost()
            run_log_info = {
                "runs": state.count_runs(),  # COUNT(*), not len() over fetched rows
                "total_cost": float(total),
                "currency": cur,
                # goal 79: the honest per-currency breakdown — never merges
                # 10 CNY + 2 USD into one meaningless "12".
                "by_currency": state.total_cost_by_currency(),
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
        currency = run_log_info["currency"]
        sidecar_by_currency = run_log_info.get("by_currency") or sidecar_by_currency

    # Process build lock (R2): the takeover entry point must say when the OTHER
    # party (or a crashed run) holds the mutating build right now. Read-only
    # peek at the holder JSON — never acquires.
    build_lock_info: dict[str, Any] | None = None
    lock_path = project.runtime_dir / "build.lock"
    if lock_path.exists():
        try:
            holder = json.loads(lock_path.read_text(encoding="utf-8"))
            build_lock_info = holder if isinstance(holder, dict) else {}
        except (json.JSONDecodeError, OSError):
            build_lock_info = {"note": "lock file unreadable"}

    timeline = project.load_timeline()
    # Round W (issue #70): use the ONE numeric final resolver (final_v10 beats
    # final_v9) instead of a lexicographic sorted-glob, which used to pick
    # final_v9 as "latest" once a project passed 9 renders.
    final = project.newest_final_path()

    # FIX-A writes the .key.json sidecar only at render completion, so a latest
    # final lacking one is likely crash-truncated — say so instead of presenting
    # it as the project's finished 成片 (assessment 2.10-8, §10 takeover honesty).
    latest_final_note = None
    if final is not None and not final.with_suffix(".key.json").exists():
        latest_final_note = "final may be incomplete (no content-key sidecar; crashed render?)"

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
        "shot_notes": notes,
        "voice_by_state": voice_by_state,
        "timeline": {
            "exists": timeline is not None,
            "duration_ms": timeline.duration_ms if timeline else None,
            "mode": timeline.meta.mode if timeline else None,
        },
        "latest_final": project.relpath(final) if final else None,
        "latest_final_note": latest_final_note,
        "qc": qc_summary,
        "total_cost": total_cost,
        "currency": currency,
        # goal 79: the honest breakdown — display ALL currencies present
        # (e.g. "12 CNY + 2 USD") instead of ever merging them into one number.
        "spend_by_currency": sidecar_by_currency,
        "run_log": run_log_info,
        "budget_limit": config.budget.limit,
        "recent_events": tail_events(project.root, 5),
        "next_step": next_step,
        "build_lock": build_lock_info,
    }
