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

import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.container import Project
from ..core.safeio import publish_text, refuse_linked_within
from ..core.yamlio import dump_yaml
from .checks import QCItem, QCReport


def write_reports(project: Project, qc: QCReport,
                  assurance: list[dict] | None = None) -> dict[str, Path]:
    """Write the three QC artifacts. ``assurance`` (DR02 WP4) is optional and
    purely ADDITIVE: when a caller passes the per-shot assurance dicts (from
    :func:`manju.qc.assurance.assurance_for_all`), qc.json gains an
    ``"assurance"`` block ({shots, proposals}) and qc.md a short 验收 section.
    Omitted, every existing key is byte-identical to before. repair_plan.yaml
    (the machine-tier plan) is UNCHANGED either way — the expectation-driven
    proposals live only in qc.json's assurance block."""
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reports_dir = project.reports_dir
    # QC-P0-001: a symlinked ``reports/`` (or any linked segment on the way to
    # it) would write qc.json/qc.md/repair_plan.yaml THROUGH the link into an
    # external directory while the returned relpaths still read as in-project.
    # Refuse the linked directory chain up front, then publish each artifact via
    # the no-follow atomic publisher (random-named sibling temp + atomic
    # replace) so nothing lands outside the project. Byte-identical to the old
    # write_json/atomic_write_text/write_yaml output for a genuine reports/.
    refuse_linked_within(reports_dir, project.root, kind="reports 目录")
    reports_dir.mkdir(parents=True, exist_ok=True)

    qc_json = reports_dir / "qc.json"
    qc_md = reports_dir / "qc.md"
    repair_yaml = reports_dir / "repair_plan.yaml"

    payload = {"ok": qc.ok, "generated_at": generated_at, **qc.to_dict()}
    if assurance is not None:
        payload["assurance"] = build_assurance_block(project, assurance)
    publish_text(qc_json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    publish_text(qc_md, _render_md(qc, generated_at, assurance))
    publish_text(repair_yaml, dump_yaml(_repair_plan(project, qc, generated_at)))

    return {"qc_json": qc_json, "qc_md": qc_md, "repair_plan": repair_yaml}


# ------------------------------------------------------ assurance (DR02 WP4)


def build_assurance_block(project: Project, assurance: list[dict] | None) -> dict:
    """The derived assurance block embedded in qc.json (and the ``manju qc
    --json`` / MCP ``qc`` envelopes so all three agree): the per-shot assurance
    dicts plus read-only repair PROPOSALS for the rejected/unknown shots. Pure
    and side-effect-free — proposals never generate, write a source, or spend
    (each carries ``do_not_execute_automatically``). A malformed shot entry is
    skipped, never fatal (degrade-gracefully)."""
    from .assurance import repair_proposal

    shots = list(assurance or [])
    proposals: list[dict] = []
    for a in shots:
        try:
            sid = (a.get("subject") or {}).get("id")
            prop = repair_proposal(project, sid, a) if sid else None
        except Exception:
            prop = None
        if prop is not None:
            proposals.append(prop)
    return {"shots": shots, "proposals": proposals}


# --------------------------------------------------------------- qc.md


_LEVELS = [
    ("error", "❌", "Errors 错误"),
    ("warn", "⚠️", "Warnings 警告"),
    ("info", "ℹ️", "Info 信息"),
]


def _render_md(qc: QCReport, generated_at: str,
               assurance: list[dict] | None = None) -> str:
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
    lines.extend(_render_assurance_md(assurance))
    if len(lines) and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _render_assurance_md(assurance: list[dict] | None) -> list[str]:
    """The short 验收 (assurance) section — one line per shot
    ``S001 · <state> — <first reason>``, plus the named stale reasons where
    present. Empty (no section) when no assurance was passed (additive)."""
    if not assurance:
        return []
    out = ["## 🔖 验收 (assurance)", ""]
    for a in assurance:
        sid = (a.get("subject") or {}).get("id", "?")
        state = a.get("assurance_state", "?")
        reasons = a.get("reasons") or []
        first = f" — {reasons[0]}" if reasons else ""
        out.append(f"- {sid} · {state}{first}")
        stale = a.get("stale_reasons") or []
        if stale:
            out.append(f"  - stale 原因: {', '.join(stale)}")
    out.append("")
    return out


# ------------------------------------------------------- repair_plan.yaml


def _repair_plan(project: Project, qc: QCReport, generated_at: str) -> dict:
    actions: list[dict] = []
    fallback_cache: dict[str, bool] = {}
    for it in qc.items:
        entry = _derive_action(project, it, fallback_cache)
        if entry is not None:
            actions.append(entry)
    return {"generated_at": generated_at, "ok": qc.ok, "actions": actions}


# QC subjects that name a whole-render / track, NOT a shot — a redo_new_seed
# repair (which redo_shot's `subject`) is inapplicable to these.
_NON_SHOT_SUBJECTS = frozenset(
    {"final", "timeline", "voice", "music", "sfx", "ambient"})


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

    # A check that declared its own item auto_safe (today: the OCR must_show
    # FAIL, content.py) promised "可由 `manju repair --auto` 自动修复" in
    # qc.md — the plan must map it to an actually-auto action, or the two
    # derived artifacts contradict each other and --auto silently never does
    # the promised repair. Shot-subject only (redo_shot needs a real shot).
    if item.auto_safe and subject not in _NON_SHOT_SUBJECTS:
        return make("redo_new_seed", True)

    # duration mismatch -> redo (§9). Gate on the subject being a real shot:
    # the "duration" word also appears on non-shot items — final-render drift
    # ("final duration ... differs from timeline ...", subject "final") and a
    # negative audio-clip duration (subject "voice"/"music"/"sfx"/"ambient").
    # Deriving an auto-safe redo_new_seed on those makes `manju repair --auto`
    # redo_shot a non-existent shot, masking the real re-render / timeline-edit
    # fix — so route them to human_review (the per-shot "shorter than the clip"
    # deficit, subject=shot, still redoes).
    if ("duration" in msg or "shorter than the clip" in msg) and \
            subject not in _NON_SHOT_SUBJECTS:
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
