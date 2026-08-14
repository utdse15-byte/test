"""Shared presentation for the shot-production journey.

The shot surfaces are deliberately not a false four-step pipeline.  A candidate
can be obtained in either of two ways — prepare/generate it in the shot lab or
import an externally-produced file — and both paths converge on the existing
human review surface::

    plan shots -> (shot lab OR batch ingest) -> human review

This module owns only a small, read-only projection and the Chinese product
copy around that path.  It does not persist progress, select takes, approve
shots, call providers, or become a build input.  All counts come from the
existing shot files, append-only take directories and current per-shot status.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

__all__ = [
    "shot_journey_html",
    "shot_production_summary",
]


_ACTIVE_COPY: dict[str, tuple[str, str]] = {
    "/storyboard": ("规划镜头", "把每个镜头的意图、画面和约束先说清楚。"),
    "/lab": ("在实验室准备候选", "调整参考、提示词和候选，不会自动选用。"),
    "/ingest": ("导入外部候选", "先预演匹配，确认后只追加、不覆盖。"),
    "/review": ("人工审片", "评价、选择和镜头审批是三项独立决定。"),
}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _shot_href(path: str, shot_id: str | None, *, role: str | None = None) -> str:
    if not shot_id:
        return path
    suffix = "?shot=" + quote(shot_id, safe="")
    if role:
        suffix += "&role=" + quote(role, safe="")
    return path + suffix


def shot_production_summary(project: Any) -> dict[str, Any]:
    """Return a best-effort, deterministic summary over current shot facts.

    This is intentionally cheaper and narrower than production readiness.  It
    answers only the questions needed by the shot-journey chrome: whether each
    shot has any candidate media, a current selection, a review note for that
    *current* selection, and shot-level approval.  Broken shot files are kept in
    the total and surfaced as the first repair target; they never make the page
    fail to render.
    """

    try:
        shot_ids = list(project.shot_ids())
    except Exception as exc:
        return {
            "total": 0,
            "with_candidates": 0,
            "selected": 0,
            "reviewed": 0,
            "approved": 0,
            "broken": [],
            "shots": [],
            "error": " ".join(str(exc).split())[:180],
            "next": {
                "href": "/storyboard",
                "label": "检查镜头列表",
                "kind": "repair",
                "shot": None,
            },
        }

    rows: list[dict[str, Any]] = []
    for shot_id in shot_ids:
        row: dict[str, Any] = {
            "shot": shot_id,
            "candidate_count": 0,
            "selected_take": None,
            "reviewed": False,
            "approved": False,
            "broken": False,
            "action": "",
        }
        try:
            shot = project.load_shot(shot_id)
            row["action"] = str(getattr(getattr(shot, "action", None), "main", "") or "")
            selected = getattr(getattr(shot, "status", None), "selected_take", None)
            row["selected_take"] = selected
            notes = dict(getattr(getattr(shot, "status", None), "take_notes", {}) or {})
            row["reviewed"] = bool(selected and str(notes.get(selected) or "").strip())
            row["approved"] = (
                str(getattr(getattr(shot, "status", None), "review_state", "needs_review"))
                == "approved"
            )
        except Exception as exc:
            row["broken"] = True
            row["error"] = " ".join(str(exc).split())[:180]
        try:
            row["candidate_count"] = len(project.takes(shot_id, skip_ghosts=True))
        except Exception:
            row["candidate_count"] = 0
        rows.append(row)

    broken = [r["shot"] for r in rows if r["broken"]]
    with_candidates = sum(1 for r in rows if r["candidate_count"] > 0)
    selected = sum(1 for r in rows if r["selected_take"])
    reviewed = sum(1 for r in rows if r["reviewed"])
    approved = sum(1 for r in rows if r["approved"])

    next_action: dict[str, Any]
    if broken:
        shot_id = broken[0]
        next_action = {
            "href": "/storyboard",
            "label": f"修复 {shot_id} 的镜头文件",
            "kind": "repair",
            "shot": shot_id,
        }
    elif not rows:
        next_action = {
            "href": "/create",
            "label": "先建立第一个镜头",
            "kind": "plan",
            "shot": None,
        }
    else:
        missing_candidate = next((r for r in rows if r["candidate_count"] <= 0), None)
        missing_selection = next(
            (r for r in rows if r["candidate_count"] > 0 and not r["selected_take"]),
            None,
        )
        missing_review = next(
            (r for r in rows if r["selected_take"] and not r["reviewed"]),
            None,
        )
        missing_approval = next(
            (r for r in rows if r["selected_take"] and not r["approved"]),
            None,
        )
        if missing_candidate is not None:
            shot_id = str(missing_candidate["shot"])
            next_action = {
                "href": _shot_href("/lab", shot_id),
                "label": f"为 {shot_id} 准备候选",
                "kind": "candidate",
                "shot": shot_id,
            }
        elif missing_selection is not None:
            shot_id = str(missing_selection["shot"])
            next_action = {
                "href": _shot_href("/review", shot_id),
                "label": f"为 {shot_id} 选择候选",
                "kind": "select",
                "shot": shot_id,
            }
        elif missing_review is not None:
            shot_id = str(missing_review["shot"])
            next_action = {
                "href": _shot_href("/review", shot_id),
                "label": f"评价 {shot_id} 的当前候选",
                "kind": "review",
                "shot": shot_id,
            }
        elif missing_approval is not None:
            shot_id = str(missing_approval["shot"])
            next_action = {
                "href": _shot_href("/review", shot_id),
                "label": f"确认 {shot_id} 是否通过",
                "kind": "approve",
                "shot": shot_id,
            }
        else:
            next_action = {
                "href": "/edit",
                "label": "进入剪辑",
                "kind": "edit",
                "shot": None,
            }

    return {
        "total": len(rows),
        "with_candidates": with_candidates,
        "selected": selected,
        "reviewed": reviewed,
        "approved": approved,
        "broken": broken,
        "shots": rows,
        "error": None,
        "next": next_action,
    }


def _current_shot_next(row: dict[str, Any], active: str) -> dict[str, Any] | None:
    """Return the honest next action for an explicitly focused shot.

    Project-wide progress still supplies the fallback.  This prevents a user
    working on S001 in the lab from seeing an unrelated S004 call to action
    merely because S004 is the first incomplete shot in project order.
    """

    shot_id = str(row.get("shot") or "")
    if not shot_id:
        return None
    if row.get("broken"):
        return {
            "href": "/storyboard",
            "label": f"修复 {shot_id} 的镜头文件",
            "kind": "repair",
            "shot": shot_id,
        }
    if int(row.get("candidate_count") or 0) <= 0:
        target = "/ingest" if active == "/ingest" else "/lab"
        return {
            "href": _shot_href(target, shot_id, role="take" if target == "/ingest" else None),
            "label": f"为 {shot_id} 准备第一个候选",
            "kind": "candidate",
            "shot": shot_id,
        }
    if not row.get("selected_take"):
        return {
            "href": _shot_href("/review", shot_id),
            "label": f"为 {shot_id} 选择候选",
            "kind": "select",
            "shot": shot_id,
        }
    if not row.get("reviewed"):
        return {
            "href": _shot_href("/review", shot_id),
            "label": f"评价 {shot_id} 的当前候选",
            "kind": "review",
            "shot": shot_id,
        }
    if not row.get("approved"):
        return {
            "href": _shot_href("/review", shot_id),
            "label": f"确认 {shot_id} 是否通过",
            "kind": "approve",
            "shot": shot_id,
        }
    return None

def shot_journey_html(
    active: str,
    project: Any,
    *,
    shot_id: str | None = None,
) -> str:
    """Render the compact branching shot-production path.

    ``shot_id`` is navigation context only.  It is never persisted and only
    narrows links so moving from lab/import to review keeps the same shot in
    view.  Project-wide progress is derived fresh from current facts.
    """

    summary = shot_production_summary(project)
    active_label, active_desc = _ACTIVE_COPY.get(active, _ACTIVE_COPY["/storyboard"])

    rows = summary.get("shots") or []
    known_shots = {str(row.get("shot")) for row in rows}
    target_shot = shot_id if shot_id in known_shots else summary.get("next", {}).get("shot")
    if not target_shot:
        target_shot = str(rows[0]["shot"]) if rows else None

    lab_href = _shot_href("/lab", target_shot)
    ingest_href = _shot_href("/ingest", target_shot, role="take")
    review_href = _shot_href("/review", target_shot)

    planning_active = active == "/storyboard"
    candidate_active = active in {"/lab", "/ingest"}
    review_active = active == "/review"

    def step_link(path: str, number: str, label: str, is_active: bool) -> str:
        cls = "mj-shot-step active" if is_active else "mj-shot-step"
        current = ' aria-current="step"' if is_active else ""
        return (
            f'<a class="{cls}" href="{_e(path)}"{current}>'
            f'<span class="mj-shot-num">{_e(number)}</span>'
            f'<span class="mj-shot-label">{_e(label)}</span>'
            "</a>"
        )

    branch_cls = (
        "mj-shot-step mj-shot-candidate active"
        if candidate_active
        else "mj-shot-step mj-shot-candidate"
    )
    branch_current = ' aria-current="step"' if candidate_active else ""
    lab_cls = "active" if active == "/lab" else ""
    ingest_cls = "active" if active == "/ingest" else ""

    stats = (
        f'<span><b>{int(summary.get("total") or 0)}</b> 镜头</span>'
        f'<span><b>{int(summary.get("with_candidates") or 0)}</b> 有候选</span>'
        f'<span><b>{int(summary.get("selected") or 0)}</b> 已选择</span>'
        f'<span><b>{int(summary.get("reviewed") or 0)}</b> 已评价</span>'
    )
    context = ""
    if target_shot:
        current = next(
            (row for row in (summary.get("shots") or []) if row.get("shot") == target_shot),
            None,
        )
        action = str((current or {}).get("action") or "").strip()
        context = (
            f'<span class="mj-shot-context"><b>{_e(target_shot)}</b>'
            + (f'<span>{_e(action)}</span>' if action else "")
            + "</span>"
        )

    current_row = next(
        (row for row in rows if str(row.get("shot")) == str(target_shot)),
        None,
    )
    contextual_next = (
        _current_shot_next(current_row, active) if shot_id and current_row else None
    )
    next_action = contextual_next or summary.get("next") or {}
    next_scope = "项目下一步" if shot_id and current_row and contextual_next is None else "下一步"
    next_href = str(next_action.get("href") or "/storyboard")
    next_label = str(next_action.get("label") or "查看镜头计划")
    error_note = ""
    if summary.get("error"):
        error_note = '<span class="mj-shot-warning">镜头进度暂不可用，不影响当前页面。</span>'
    elif summary.get("broken"):
        error_note = (
            '<span class="mj-shot-warning">'
            + _e(f'有 {len(summary["broken"])} 个镜头文件需要修复。')
            + "</span>"
        )

    return (
        f'<section class="mj-shot-journey" data-shot-journey data-active="{_e(active)}" '
        'aria-label="镜头制作路径">'
        '<div class="mj-shot-head">'
        '<div class="mj-shot-current">'
        '<span class="mj-shot-eyebrow">镜头制作</span>'
        f'<strong>当前：{_e(active_label)}</strong>'
        f'<span class="muted">{_e(active_desc)}</span>'
        f'{context}'
        "</div>"
        f'<div class="mj-shot-stats" aria-label="镜头制作进度">{stats}</div>'
        "</div>"
        '<nav class="mj-shot-track" aria-label="镜头制作步骤">'
        + step_link("/storyboard", "1", "规划镜头", planning_active)
        + f'<div class="{branch_cls}"{branch_current}>'
        '<span class="mj-shot-step-title"><span class="mj-shot-num">2</span>'
        '<span class="mj-shot-label">获得候选</span></span>'
        '<span class="mj-shot-branches" aria-label="候选来源">'
        f'<a class="{lab_cls}" href="{_e(lab_href)}">实验室</a>'
        '<span>或</span>'
        f'<a class="{ingest_cls}" href="{_e(ingest_href)}">批量入库</a>'
        "</span></div>"
        + step_link(review_href, "3", "人工审片", review_active)
        + "</nav>"
        '<div class="mj-shot-foot">'
        '<p class="muted">实验室和批量入库是两条替代路径；候选不会自动选用，评价不会自动锁片。'
        f'{error_note}</p>'
        f'<a class="btn ghost mini mj-shot-next" href="{_e(next_href)}">'
        f'{_e(next_scope)}：{_e(next_label)} →</a>'
        "</div>"
        "</section>"
    )
