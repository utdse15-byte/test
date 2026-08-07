"""Honest usage/workflow evaluation (round AA, goal item 8).

The project has grown a skill library (§core/skills.py), a creation funnel
(§build/funnel.py), an agent visual-QC pipe (§qc/agent_review.py), and many
CLI/GUI workflows — but nothing ever asked "does any of this actually get
used, and does it correlate with fewer redo/repair cycles?" This module is
that read-only lens.

Deliberate scope: :func:`evaluate` reads ONLY two append-only logs that
already exist —

    events.jsonl          skill_used / funnel_scaffold / redo / repair /
                           select / auto_select / build / ingest actions,
                           each carrying its ``actor`` (human|ai|engine)
    reports/qc_agent.jsonl the agent visual-QC verdict records (§qc/agent_review.py)

plus the installed skill INDEX (:func:`..core.skills.list_skills` — a
directory scan, not a log) so a skill that was never touched can be named as
such. It never re-derives "truth" by re-walking shot files, re-running
`manju check`, or calling the funnel's own live status — that would blur
"what got logged" with "what is true right now", and this report's only
honest claim is about the FORMER (see the ``honesty`` section below, which is
a REQUIRED, always-populated part of the report, not an afterthought).

Every count here is a correlation-only signal a human still has to interpret
— goal item 8 explicitly asked to avoid inventing metrics the data cannot
support, so there is no productivity number, no quality score, and no
before/after comparison anywhere in this module. What IS honest: how often a
skill's content was actually served, which shots got redone or repaired the
most (rework hotspots — a signal editorial/post houses already track via
revision counts), and the AI QC verdict tally (blocker/issue/fyi — the same
severity-tier idea Netflix's own QC framework uses, already adopted by
qc/agent_review.py).

Degrades gracefully everywhere: a missing/empty events.jsonl or
qc_agent.jsonl, or a project with zero installed skills, produces an
all-zeros report — never a crash, never a KeyError bubbling to the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .events import EVENTS_FILE

__all__ = ["evaluate", "SMALL_N_THRESHOLD", "TOP_N_HOTSPOTS"]

# Below this many observations, a count is flagged ``low_n`` — a ranking or
# "hotspot" built from 1-2 events is noise, not signal (§honesty).
SMALL_N_THRESHOLD = 3

# How many rework hotspots to surface per category — enough to see a pattern,
# short enough to stay a glance, not a wall of shot ids.
TOP_N_HOTSPOTS = 10

_QC_LEVELS = ("blocker", "issue", "fyi")


# ------------------------------------------------------------------ readers


def _detail(event: dict) -> dict:
    """An event's ``detail`` as a dict, whatever shape it actually is on
    disk — a handful of legacy call sites pass a bare list/str; those degrade
    to ``{}`` here rather than raising."""
    d = event.get("detail")
    return d if isinstance(d, dict) else {}


def _read_events(project: Any) -> list[dict]:
    """Every parsed record in ``events.jsonl``, file order. Same tolerant
    parse as :func:`core.events.tail_events` (a torn line is skipped, never
    fatal) — but the WHOLE file, not a tail, because usage counts across a
    project's history must not silently drop everything before the last N
    lines (that would make ``never_used`` and hotspot counts dishonestly low)."""
    path = Path(project.root) / EVENTS_FILE
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


def _read_qc_records(project: Any) -> tuple[list[dict], int]:
    """(records, malformed-line-count) from ``reports/qc_agent.jsonl``. A
    local tolerant reader — mirrors qc/agent_review.py's own
    ``_read_records`` shape (never a parallel VALIDATOR, this is read-only
    counting) rather than importing its private helper across packages."""
    from ..qc.agent_review import agent_log_path

    path = agent_log_path(project)
    if not path.exists():
        return [], 0
    records: list[dict] = []
    malformed = 0
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(rec, dict):
            records.append(rec)
        else:
            malformed += 1
    return records, malformed


def _project_name(project: Any) -> str | None:
    try:
        return project.load_config().name
    except Exception:
        return None


# ------------------------------------------------------------------ skills


def _skills_section(project: Any, events: list[dict]) -> dict[str, Any]:
    from .skills import list_skills

    try:
        installed = list_skills(project)
    except Exception:
        installed = []
    installed_ids = sorted(s.id for s in installed)

    usage: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("action") != "skill_used":
            continue
        d = _detail(e)
        sid = d.get("skill")
        if not sid:
            continue
        row = usage.setdefault(str(sid), {"count": 0, "last_used": None, "by_via": {}})
        row["count"] += 1
        via = str(d.get("via") or "unknown")
        row["by_via"][via] = row["by_via"].get(via, 0) + 1
        ts = e.get("ts")
        if ts and (row["last_used"] is None or ts > row["last_used"]):
            row["last_used"] = ts

    all_ids = sorted(set(installed_ids) | set(usage))
    rows = []
    for sid in all_ids:
        u = usage.get(sid)
        count = u["count"] if u else 0
        rows.append({
            "id": sid,
            "installed": sid in installed_ids,
            "count": count,
            "last_used": u["last_used"] if u else None,
            "by_via": dict(sorted(u["by_via"].items())) if u else {},
            "low_n": bool(u) and count < SMALL_N_THRESHOLD,
        })
    rows.sort(key=lambda r: (-r["count"], r["id"]))

    never_used = sorted(sid for sid in installed_ids if usage.get(sid) is None)
    return {
        "installed_total": len(installed_ids),
        "used_total": sum(1 for sid in installed_ids if usage.get(sid)),
        "never_used": never_used,
        "usage": rows,
    }


# ---------------------------------------------------------------- workflow


def _shot_hotspots(events: list[dict], actions: tuple[str, ...]) -> tuple[int, list[dict]]:
    """(total matching events, top-N shot hotspots) for the given action
    name(s) — reused by redo and repair, the two rework signals the field
    already tracks as revision/redo counts (§REPORTS/ROUND-AA-EVALUATION.md)."""
    total = 0
    counts: dict[str, int] = {}
    last_used: dict[str, str] = {}
    for e in events:
        if e.get("action") not in actions:
            continue
        total += 1
        d = _detail(e)
        shot = d.get("shot")
        if not shot:
            continue
        shot = str(shot)
        counts[shot] = counts.get(shot, 0) + 1
        ts = e.get("ts")
        if ts and (shot not in last_used or ts > last_used[shot]):
            last_used[shot] = ts
    rows = [
        {"shot": sid, "count": n, "last_used": last_used.get(sid), "low_n": n < SMALL_N_THRESHOLD}
        for sid, n in counts.items()
    ]
    rows.sort(key=lambda r: (-r["count"], r["shot"]))
    return total, rows[:TOP_N_HOTSPOTS]


def _select_section(events: list[dict]) -> dict[str, int]:
    manual = sum(1 for e in events if e.get("action") == "select")
    auto = sum(1 for e in events if e.get("action") == "auto_select")
    return {"manual": manual, "auto": auto, "total": manual + auto}


def _build_section(events: list[dict]) -> dict[str, int]:
    total = ok = failed = canceled = 0
    for e in events:
        if e.get("action") != "build":
            continue
        total += 1
        d = _detail(e)
        if d.get("canceled"):
            canceled += 1
        elif d.get("ok"):
            ok += 1
        else:
            failed += 1
    return {"total": total, "ok": ok, "failed": failed, "canceled": canceled}


def _ingest_section(events: list[dict]) -> dict[str, int]:
    runs = 0
    landed = 0
    for e in events:
        if e.get("action") != "ingest":
            continue
        runs += 1
        d = _detail(e)
        try:
            landed += int(d.get("landed") or 0)
        except (TypeError, ValueError):
            pass
    return {"runs": runs, "files_landed": landed}


def _funnel_section(events: list[dict]) -> dict[str, Any]:
    """What's DERIVABLE from the log about the creation funnel: how often
    each scaffoldable stage's template was written (``funnel_scaffold``,
    build/funnel.py's existing event — reused verbatim, not renamed). Full
    stage completion/abandonment needs the funnel's own live artifact walk
    (`manju create`), which this log-only evaluator deliberately does not
    call (§module docstring) — so this section only claims what an event
    actually recorded."""
    from ..build.funnel import SCAFFOLDS

    scaffold: dict[str, dict[str, Any]] = {}
    for e in events:
        if e.get("action") != "funnel_scaffold":
            continue
        d = _detail(e)
        stage = d.get("stage")
        if not stage:
            continue
        stage = str(stage)
        row = scaffold.setdefault(stage, {"count": 0, "last_used": None})
        row["count"] += 1
        ts = e.get("ts")
        if ts and (row["last_used"] is None or ts > row["last_used"]):
            row["last_used"] = ts

    scaffoldable = sorted(SCAFFOLDS.keys())
    never = sorted(s for s in scaffoldable if s not in scaffold)
    return {
        "scaffoldable_stages": scaffoldable,
        "scaffold_events": {sid: scaffold[sid] for sid in sorted(scaffold)},
        "never_scaffolded": never,
        "note": (
            "只有 brief/synopsis/beats 三个阶段有 `manju create <stage>` 的 "
            "funnel_scaffold 事件;剧本/分镜/生成计划/生成四个阶段没有独立的每阶段"
            "日志事件,本报告不会、也不能仅凭日志判断它们是完成还是放弃——"
            "完整漏斗进度用 `manju create`(读当前项目文件)才能看到。"
        ),
    }


def _workflow_section(events: list[dict]) -> dict[str, Any]:
    redo_total, redo_hotspots = _shot_hotspots(events, ("redo",))
    repair_total, repair_hotspots = _shot_hotspots(events, ("repair",))
    return {
        "redo": {"total": redo_total, "hotspots": redo_hotspots},
        "repair": {"total": repair_total, "hotspots": repair_hotspots},
        "select": _select_section(events),
        "build": _build_section(events),
        "ingest": _ingest_section(events),
        "funnel": _funnel_section(events),
    }


# --------------------------------------------------------------------- qc


def _qc_section(records: list[dict], malformed: int) -> dict[str, Any]:
    by_level = {lv: 0 for lv in _QC_LEVELS}
    by_actor: dict[str, int] = {}
    for r in records:
        lv = str(r.get("level") or "")
        if lv in by_level:
            by_level[lv] += 1
        actor = str(r.get("actor") or "unknown")
        by_actor[actor] = by_actor.get(actor, 0) + 1
    return {
        "verdicts_total": len(records),
        "by_level": by_level,
        "by_actor": dict(sorted(by_actor.items())),
        "malformed_lines": malformed,
    }


# ------------------------------------------------------------------ actors


def _actor_totals(events: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for e in events:
        actor = str(e.get("actor") or "unknown")
        totals[actor] = totals.get(actor, 0) + 1
    return dict(sorted(totals.items()))


# ---------------------------------------------------------------- honesty


def _honesty_section() -> dict[str, Any]:
    """REQUIRED section — what this report cannot claim. Always populated,
    always rendered by the CLI (goal item 8): a usage/rework report is easy
    to over-read as a productivity or quality verdict, and this project's
    stance (§0, §10) is to never fabricate a metric the data cannot support."""
    return {
        "summary": (
            "本报告只统计 events.jsonl / reports/qc_agent.jsonl 里已经发生的操作"
            "次数与判读结果——它是一面镜子,不是一个分数。"
        ),
        "cannot_claim": [
            "不衡量生产率:events.jsonl 只记录『发生了什么、谁做的』,不记录墙钟时间"
            "或人力成本,不存在『省了多少时间』这类数字。",
            "不是因果关系,只是相关:某技能用得多不代表它让质量变好;某镜头 redo/"
            "repair 次数多,也可能是镜头本身难拍或需求反复变化,不一定是工具或"
            "流程的问题——相关不是因果。",
            "QC 判读分布不是质量分数:blocker/issue/fyi 计数反映『AI 判读出过多少"
            "问题』,不能跨项目比较优劣,也不是最终成片质量的度量。",
            f"小样本不可靠:计数低于 {SMALL_N_THRESHOLD} 的条目标记为 low_n=true——"
            "排名和『热点』在小样本下极易被单次操作左右,不要当排行榜看。",
            "只统计被记录的操作:手工绕开 CLI/GUI 直接改文件、或事件文件被清空/"
            "轮转丢失的历史,都不会出现在这里——『从未使用』可能只是『从未被记录』。",
        ],
        "small_n_threshold": SMALL_N_THRESHOLD,
    }


# ------------------------------------------------------------------- main


def evaluate(project: Any) -> dict[str, Any]:
    """The whole evaluation report — see the module docstring for scope and
    the ``honesty`` section for what it deliberately does NOT claim.

    Deterministic: every list is sorted by an explicit key (count desc then
    id asc, or plain id asc), so two runs over the same logs always produce
    byte-identical JSON. Degrades to an all-zeros report when events.jsonl
    and/or reports/qc_agent.jsonl are missing or empty — never raises for a
    fresh project.
    """
    events = _read_events(project)
    qc_records, qc_malformed = _read_qc_records(project)

    return {
        "project": _project_name(project),
        "events_total": len(events),
        "skills": _skills_section(project, events),
        "workflow": _workflow_section(events),
        "qc": _qc_section(qc_records, qc_malformed),
        "actors": _actor_totals(events),
        "honesty": _honesty_section(),
    }
