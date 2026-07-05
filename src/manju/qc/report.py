"""QC report writers (§9).

Three artifacts, machine + human + actionable:
    reports/qc.json          machine-readable {ok, generated_at, items}
    reports/qc.md            human-readable, grouped by level, UTF-8/中文-friendly
    reports/repair_plan.yaml one suggested action per error/warn

Repair actions are derived mechanically (§9), never by an LLM. Action vocabulary:
    redo_new_seed | switch_provider | degrade_fallback | human_review
``manju repair --auto`` executes only ``auto_safe`` entries; the rest are human
to-dos. There is no separate repair subsystem — every action loops back to
editing the shot's generation params and rebuilding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..core.container import Project
from ..core.yamlio import atomic_write_text, write_json, write_yaml
from .checks import QCItem, QCReport


def write_reports(project: Project, qc: QCReport) -> dict[str, Path]:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reports_dir = project.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)

    qc_json = reports_dir / "qc.json"
    qc_md = reports_dir / "qc.md"
    repair_yaml = reports_dir / "repair_plan.yaml"

    write_json(qc_json, {"ok": qc.ok, "generated_at": generated_at, **qc.to_dict()})
    atomic_write_text(qc_md, _render_md(qc, generated_at))
    write_yaml(repair_yaml, _repair_plan(project, qc, generated_at))

    return {"qc_json": qc_json, "qc_md": qc_md, "repair_plan": repair_yaml}


# --------------------------------------------------------------- qc.md


_LEVELS = [
    ("error", "❌", "Errors 错误"),
    ("warn", "⚠️", "Warnings 警告"),
    ("info", "ℹ️", "Info 信息"),
]


def _render_md(qc: QCReport, generated_at: str) -> str:
    status = "✅ ok" if qc.ok else "❌ has errors"
    lines: list[str] = [
        "# QC Report / 质检报告",
        "",
        f"- 结果 result: {status}",
        f"- 生成时间 generated_at: {generated_at}",
        f"- 条目 items: {len(qc.items)}",
        "",
    ]
    for level, emoji, title in _LEVELS:
        items = [it for it in qc.items if it.level == level]
        if not items:
            continue
        lines.append(f"## {emoji} {title} ({len(items)})")
        lines.append("")
        for it in items:
            lines.append(f"- **[{it.area}] {it.subject}** — {it.message}")
            if it.suggestion:
                lines.append(f"  - 建议 suggestion: {it.suggestion}")
            if it.auto_safe:
                lines.append("  - auto-safe: 可由 `manju repair --auto` 自动修复")
        lines.append("")
    if len(lines) and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# ------------------------------------------------------- repair_plan.yaml


def _repair_plan(project: Project, qc: QCReport, generated_at: str) -> dict:
    actions: list[dict] = []
    fallback_cache: dict[str, bool] = {}
    for it in qc.items:
        entry = _derive_action(project, it, fallback_cache)
        if entry is not None:
            actions.append(entry)
    return {"generated_at": generated_at, "ok": qc.ok, "actions": actions}


def _derive_action(project: Project, item: QCItem, fallback_cache: dict[str, bool]):
    """Map an error/warn item to a concrete repair action (§9), mechanically."""
    if item.level not in ("error", "warn"):
        return None  # info items are not actionable

    subject = item.subject
    msg = item.message.lower()

    def make(action: str, auto_safe: bool) -> dict:
        return {
            "shot": subject,
            "problem": item.message,
            "action": action,
            "auto_safe": auto_safe,
        }

    # caption issues -> human_review (§9)
    if "caption" in msg:
        return make("human_review", False)

    # duration mismatch -> redo (§9)
    if "duration" in msg or "shorter than the clip" in msg:
        return make("redo_new_seed", True)

    # missing / unreadable media -> degrade_fallback if the shot has a fallback,
    # else human_review (§9). auto_safe only for fallback / redo actions.
    if item.area == "existence" and (
        "missing" in msg or "not readable" in msg or "no media" in msg
    ):
        if _has_fallback(project, subject, fallback_cache):
            return make("degrade_fallback", True)
        return make("human_review", False)

    # everything else (final resolution, black/silence, invalid source) -> human_review
    return make("human_review", False)


def _has_fallback(project: Project, shot_id: str, cache: dict[str, bool]) -> bool:
    if shot_id in cache:
        return cache[shot_id]
    result = False
    if shot_id not in ("timeline", "final"):
        try:
            result = bool(project.load_shot(shot_id).generation.fallback)
        except Exception:
            result = False
    cache[shot_id] = result
    return result
