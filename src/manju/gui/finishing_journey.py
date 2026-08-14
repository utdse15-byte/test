"""Shared presentation for the finishing journey.

The five finishing surfaces (edit, subtitles, mixer, packaging, exports) used to
be individually capable but visually isolated.  This module adds one small,
read-only projection over existing owners:

* :mod:`manju.build.exportstatus` owns whether the newest final is missing,
  current, stale, or problematic;
* :mod:`manju.build.readiness` owns selected-media eligibility and the derived
  Picture Lock eligibility;
* the GUI owns only Chinese labels, links, and progressive disclosure.

Nothing here is persisted and no value is read back by build/provider/QC code.
The status is loaded after first paint through ``/api/finishing/status`` so the
/edit page keeps its established rule: a normal GET must not recompile the
project merely to paint chrome.
"""

from __future__ import annotations

import html
from typing import Any

__all__ = [
    "FINISHING_STAGES",
    "finishing_journey_html",
    "finishing_status",
]


FINISHING_STAGES: tuple[tuple[str, str, str], ...] = (
    ("/edit", "剪辑", "调整顺序、裁剪、转场与画面"),
    ("/subtitles", "字幕", "校对文字、时间和烧录效果"),
    ("/mixer", "混音", "平衡对白、音乐、环境与音效"),
    ("/packaging", "包装", "设置片头、片尾、封面和信息卡"),
    ("/exports", "导出", "观看、继续精剪或交付"),
)

_FINAL_PRESENTATION: dict[str, tuple[str, str]] = {
    "up_to_date": ("st-fresh", "成片当前有效"),
    "stale": ("st-stale", "成片待更新"),
    "missing": ("st-missing", "尚无成片"),
    "problematic": ("st-broken", "成片有问题"),
    "needs_manual": ("st-needs", "成片待人工确认"),
    "verified": ("st-manual", "成片已人工确认"),
}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _picture_lock_presentation(
    eligibility: dict[str, Any],
) -> tuple[str, str, str]:
    """Return product class, short label and plain-language explanation."""
    counts = eligibility.get("counts") or {}
    proxy = int(counts.get("proxy-only", 0) or 0)
    candidate = int(counts.get("candidate", 0) or 0)
    none = int(counts.get("none", 0) or 0)
    eligible = int(counts.get("final-eligible", 0) or 0)
    total = proxy + candidate + none + eligible
    if bool(eligibility.get("picture_lock_eligible")):
        return (
            "st-fresh",
            "可进入锁片评审",
            "所有镜头都使用当前、人工确认并有质量证据的视频版本。",
        )
    if total <= 0:
        return (
            "st-missing",
            "尚无镜头可锁片",
            "还没有镜头；完成分镜和候选选择后再检查锁片资格。",
        )
    if proxy:
        return (
            "st-needs",
            "含仅预览镜头",
            f"有 {proxy} 个镜头只有预览素材，需要换成可交付的视频版本。",
        )
    if candidate:
        return (
            "st-needs",
            "待人工确认",
            f"有 {candidate} 个候选镜头仍需人工审片和质量确认。",
        )
    if none:
        return (
            "st-missing",
            "镜头尚未完成",
            f"有 {none} 个镜头还没有当前视频版本。",
        )
    return (
        "st-needs",
        "暂不具备锁片资格",
        "当前证据还不足以确认所有镜头都能进入锁片。",
    )


def finishing_status(project: Any) -> dict[str, Any]:
    """Return a small, best-effort status for the finishing chrome.

    The function deliberately degrades each owner independently.  A broken
    export-status projection must not hide media eligibility, and vice versa.
    The payload is presentation-only and intentionally has no ``manju.*``
    schema: it is an internal local-GUI endpoint, not a durable artifact.
    """

    final: dict[str, Any] = {
        "state": "unknown",
        "class": "st-missing",
        "label": "成片状态未知",
        "basis": "暂时无法读取成片状态",
        "path": None,
    }
    try:
        # Only the final row is needed here.  Use the public deliverables owner,
        # not ``deliverables_data``: the latter also builds round-trip and
        # release-assessment projections that this small chrome never displays.
        from ..build.exportstatus import deliverables

        row = next((item for item in deliverables(project) if item.kind == "final"), None)
        if row is not None:
            data = row.to_dict()
            state = str(data.get("freshness") or "unknown")
            cls, label = _FINAL_PRESENTATION.get(
                state, ("st-missing", "成片状态未知")
            )
            final = {
                "state": state,
                "class": cls,
                "label": label,
                "basis": str(data.get("basis") or ""),
                "path": data.get("path"),
            }
    except Exception as exc:  # a finishing page itself must still render
        final["basis"] = "成片状态读取失败:" + " ".join(str(exc).split())[:160]

    picture_lock: dict[str, Any] = {
        "eligible": False,
        "class": "st-missing",
        "label": "锁片资格未知",
        "counts": {},
        "reasons": [],
        "summary": "暂时无法读取锁片资格。",
    }
    try:
        # ``media_eligibility`` is the direct, read-only owner for selected
        # media and Picture Lock eligibility.  Avoid the broader narrative paid
        # readiness pipeline: this chrome never decides whether generation may run.
        from ..build.readiness import media_eligibility

        eligibility = media_eligibility(project)
        cls, label, summary = _picture_lock_presentation(eligibility)
        picture_lock = {
            "eligible": bool(eligibility.get("picture_lock_eligible")),
            "class": cls,
            "label": label,
            "counts": dict(eligibility.get("counts") or {}),
            "reasons": [str(x) for x in (eligibility.get("picture_lock_reasons") or [])[:5]],
            "summary": summary,
        }
    except Exception as exc:  # independent degradation from final status
        picture_lock["reasons"] = [
            "锁片资格读取失败:" + " ".join(str(exc).split())[:160]
        ]

    return {
        "ok": True,
        "final": final,
        "picture_lock": picture_lock,
    }


def finishing_journey_html(active: str) -> str:
    """Render the shared five-stage journey with lazy status placeholders."""

    paths = [path for path, _label, _desc in FINISHING_STAGES]
    try:
        current = paths.index(active)
    except ValueError:
        current = 0

    links: list[str] = []
    for idx, (path, label, desc) in enumerate(FINISHING_STAGES):
        cls = "mj-finish-step active" if path == active else "mj-finish-step"
        current_attr = ' aria-current="step"' if path == active else ""
        links.append(
            f'<a class="{cls}" href="{_e(path)}"{current_attr} '
            f'title="{_e(desc)}">'
            f'<span class="mj-finish-num">{idx + 1}</span>'
            f'<span class="mj-finish-label">{_e(label)}</span>'
            "</a>"
        )

    previous = FINISHING_STAGES[current - 1] if current > 0 else None
    following = FINISHING_STAGES[current + 1] if current + 1 < len(FINISHING_STAGES) else None
    nav_bits: list[str] = []
    if previous is not None:
        nav_bits.append(
            f'<a class="btn ghost mini" href="{_e(previous[0])}">← {_e(previous[1])}</a>'
        )
    if following is not None:
        nav_bits.append(
            f'<a class="btn ghost mini mj-finish-next" href="{_e(following[0])}">'
            f'继续到：{_e(following[1])} →</a>'
        )
    else:
        nav_bits.append(
            '<a class="btn ghost mini mj-finish-next" href="/">返回首页</a>'
        )

    current_label = FINISHING_STAGES[current][1]
    current_desc = FINISHING_STAGES[current][2]
    return (
        f'<section class="mj-finish-journey" data-finishing-journey '
        f'data-active="{_e(active)}" aria-label="成片流程">'
        '<div class="mj-finish-head">'
        '<div><span class="mj-finish-eyebrow">成片流程</span>'
        f'<strong>当前：{_e(current_label)}</strong>'
        f'<span class="muted">{_e(current_desc)}</span></div>'
        '<div class="mj-finish-status" aria-live="polite">'
        '<span class="badge st-missing" data-finish-final '
        'title="页面显示后检查">正在检查成片…</span>'
        '<span class="badge st-missing" data-finish-lock '
        'title="页面显示后检查">正在检查锁片资格…</span>'
        '</div></div>'
        f'<nav class="mj-finish-track" aria-label="成片阶段">{"".join(links)}</nav>'
        '<div class="mj-finish-foot">'
        '<p class="mj-finish-note muted" data-finish-note>'
        '状态来自当前项目与媒体证据；这里只展示，不会替你锁片或改项目。</p>'
        f'<div class="mj-finish-actions">{"".join(nav_bits)}</div>'
        '</div>'
        '</section>'
    )
