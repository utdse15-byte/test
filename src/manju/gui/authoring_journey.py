"""Read-only product projection for the authoring journey.

The journey connects the existing creation funnel, Director Proposal store and
storyboard readiness without creating a second authoring plan.  It is only a
navigation/explanation layer: direct text edits and human-confirmed
``truth_patch_set`` proposals both land in the same canonical project files.
"""

from __future__ import annotations

import html
from typing import Any

__all__ = [
    "AUTHORING_JOURNEY_CSS",
    "authoring_journey_payload",
    "render_authoring_attention",
    "render_authoring_journey",
    "story_material_status",
]


_ATTENTION_STATES = frozenset({"proposed", "confirmed", "executing", "expired"})
_TERMINAL_STATES = frozenset({"done", "failed", "rejected"})


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _proposal_has_truth_patch(proposal: Any) -> bool:
    return any(
        getattr(entry, "action", {}).get("type") == "truth_patch_set"
        for entry in getattr(proposal, "actions", [])
    )


def story_material_status(row: dict[str, Any]) -> tuple[str, str]:
    """Return calm, user-facing status for one canonical story material.

    The funnel's exact evidence remains available in technical disclosure; this
    function keeps paths, thresholds and engine vocabulary out of the primary
    writing surface without inventing a second completion rule.
    """
    state = str(row.get("state") or "todo")
    evidence = str(row.get("evidence") or "")
    satisfied = bool(row.get("satisfied"))
    if satisfied and state in {"done", "current"}:
        return "已完成", "内容已达到当前阶段要求。"
    if satisfied:
        return "内容已存在", "前面的材料完成后，系统会自动接到这里。"
    if "读取失败" in evidence or "无法" in evidence:
        return "需要检查", "当前文件暂时无法可靠读取，请展开技术状态查看原因。"
    if "还不存在" in evidence:
        return "尚未开始", "还没有内容，可以从模板或空白开始。"
    if row.get("id") == "beats" and ("只有" in evidence or "需 ≥" in evidence):
        return "正在写", "至少写出 3 个真正改变人物处境的节拍。"
    if "过短" in evidence or "模板" in evidence:
        return "正在写", "还需要补充一些内容，保存后会重新检查。"
    if state == "todo":
        return "稍后", "完成前一份材料后再继续。"
    return "正在写", "继续补充内容，保存后会重新检查。"


def authoring_journey_payload(project: Any) -> dict[str, Any]:
    """Derive the current authoring path from existing owners only.

    ``proposal`` is explicitly optional.  Pending authoring patches receive
    priority because continuing to edit the same truth files would otherwise
    make the review stale; unrelated production proposals never block direct
    writing.  A broken optional proposal store degrades to an explanation
    instead of taking the writing surface down with it.
    """
    from ..build.funnel import PRE_STORYBOARD, funnel_status

    funnel = funnel_status(project)
    by_id = {row["id"]: row for row in funnel.get("stages", [])}
    writing_rows = [by_id[sid] for sid in PRE_STORYBOARD if sid in by_id]
    writing_done = sum(1 for row in writing_rows if row.get("satisfied"))
    writing_total = len(writing_rows)
    writing_current = next(
        (row for row in writing_rows if not row.get("satisfied")),
        None,
    )

    pending: list[Any] = []
    stale: list[Any] = []
    truth_pending: list[Any] = []
    truth_stale: list[Any] = []
    terminal = 0
    proposal_error = ""
    try:
        from ..build.director import _is_current, list_proposals

        proposals = list_proposals(project)
        for proposal in proposals:
            try:
                current = _is_current(project, proposal)
            except Exception:
                # The proposal still needs attention, but an uncertain current
                # verdict must never be presented as safe/current.
                current = False
            has_truth = _proposal_has_truth_patch(proposal)
            if proposal.state in _TERMINAL_STATES:
                terminal += 1
            if proposal.state in _ATTENTION_STATES:
                pending.append(proposal)
                is_stale = proposal.state == "expired" or (
                    proposal.state in {"proposed", "confirmed"} and not current
                )
                if is_stale:
                    stale.append(proposal)
                if has_truth:
                    truth_pending.append(proposal)
                    if is_stale:
                        truth_stale.append(proposal)
    except Exception as exc:
        proposal_error = " ".join(str(exc).split()) or exc.__class__.__name__

    storyboard = by_id.get("storyboard") or {}
    storyboard_ready = bool(storyboard.get("satisfied"))
    try:
        shot_count = len(project.shot_ids())
    except Exception:
        shot_count = 0

    other_pending = max(0, len(pending) - len(truth_pending))
    if truth_pending:
        if truth_stale:
            label = (
                f"处理 {len(truth_pending)} 份创作提案"
                f"（{len(truth_stale)} 份已过期）"
            )
        else:
            label = f"审阅 {len(truth_pending)} 份创作提案"
        next_item = {
            "kind": "proposal",
            "href": "/director",
            "label": label,
            "detail": "先决定这些变更，再继续编辑同一批故事文件，避免提案失效。",
        }
    elif writing_done < writing_total:
        next_item = {
            "kind": "writing",
            "href": "/create",
            "label": f"继续写「{(writing_current or {}).get('cn', '故事材料')}」",
            "detail": story_material_status(writing_current or {})[1],
        }
    elif proposal_error:
        next_item = {
            "kind": "proposal",
            "href": "/director",
            "label": "检查提案状态",
            "detail": "提案记录暂时无法读取；直接写作仍然安全，但进入分镜前应先检查。",
        }
    elif other_pending:
        next_item = {
            "kind": "proposal",
            "href": "/director",
            "label": f"处理 {other_pending} 份其它操作提案",
            "detail": "这些提案不改变当前故事材料，但仍需要你确认、执行或否决。",
        }
    elif not storyboard_ready:
        next_item = {
            "kind": "storyboard",
            "href": "/storyboard",
            "label": "建立并校验分镜合同",
            "detail": storyboard.get("evidence") or "把剧本拆成可执行的镜头计划。",
        }
    else:
        next_item = {
            "kind": "storyboard",
            "href": "/storyboard",
            "label": "分镜已就绪，进入镜头生产",
            "detail": storyboard.get("evidence") or f"当前已有 {shot_count} 个镜头。",
        }

    return {
        "writing": {
            "done": writing_done,
            "total": writing_total,
            "complete": writing_total > 0 and writing_done == writing_total,
            "current": (writing_current or {}).get("id"),
            "current_cn": (writing_current or {}).get("cn"),
        },
        "proposal": {
            "optional": True,
            "pending": len(pending),
            "stale": len(stale),
            "truth_pending": len(truth_pending),
            "truth_stale": len(truth_stale),
            "other_pending": other_pending,
            "history": terminal,
            "ids": [proposal.id for proposal in pending],
            "error": proposal_error,
        },
        "storyboard": {
            "ready": storyboard_ready,
            "count": shot_count,
            "evidence": storyboard.get("evidence") or "",
        },
        "next": next_item,
    }


def _step(
    *,
    href: str,
    index: str,
    title: str,
    status: str,
    detail: str,
    state: str,
    active: bool,
    optional: bool = False,
) -> str:
    current = ' aria-current="step"' if active else ""
    opt = '<span class="aj-optional">可选</span>' if optional else ""
    return (
        f'<a class="aj-step aj-state-{_e(state)}" href="{_e(href)}"{current}>'
        f'<span class="aj-index">{_e(index)}</span>'
        '<span class="aj-copy">'
        f'<span class="aj-title">{_e(title)}{opt}</span>'
        f'<span class="aj-status">{_e(status)}</span>'
        f'<span class="aj-detail">{_e(detail)}</span>'
        '</span></a>'
    )


def render_authoring_journey(project: Any, active_path: str) -> str:
    data = authoring_journey_payload(project)
    writing = data["writing"]
    proposal = data["proposal"]
    storyboard = data["storyboard"]
    next_item = data["next"]

    writing_state = "done" if writing["complete"] else "current"
    writing_status = (
        "四份故事材料已就绪"
        if writing["complete"]
        else f'{writing["done"]}/{writing["total"]} · 当前写「{writing["current_cn"] or "故事材料"}」'
    )
    writing_detail = "直接编辑项目真相；保存后重新检查进度。"

    if proposal["error"]:
        proposal_state = "attention"
        proposal_status = "提案状态暂不可用"
        proposal_detail = "直接写作仍然安全；进入分镜前请到导演助手检查。"
    elif proposal["pending"]:
        proposal_state = "attention"
        labels: list[str] = []
        if proposal["truth_pending"]:
            labels.append(f'{proposal["truth_pending"]} 份创作提案')
        if proposal["other_pending"]:
            labels.append(f'{proposal["other_pending"]} 份其它提案')
        proposal_status = " · ".join(labels) or f'{proposal["pending"]} 份待决定'
        if proposal["stale"]:
            proposal_status += f' · {proposal["stale"]} 份已过期'
        proposal_detail = "先看清影响与差异，再分别确认、执行或否决。"
    else:
        proposal_state = "optional"
        proposal_status = "没有待决定提案"
        proposal_detail = "也可以不走提案，直接继续编辑和建立分镜。"

    if storyboard["ready"]:
        storyboard_state = "done"
        storyboard_status = f'{storyboard["count"]} 个镜头 · 已通过校验'
        storyboard_detail = "镜头合同已成为后续生产的唯一计划。"
    elif writing["complete"] and not proposal["pending"]:
        storyboard_state = "current"
        storyboard_status = f'{storyboard["count"]} 个镜头 · 继续建立或修正'
        storyboard_detail = storyboard["evidence"] or "把剧本拆成可执行镜头。"
    else:
        storyboard_state = "todo"
        storyboard_status = f'{storyboard["count"]} 个镜头'
        storyboard_detail = "故事材料和待决定提案处理后再进入镜头计划。"

    return (
        '<section class="aj panel" data-authoring-journey="1">'
        '<div class="aj-head"><div><h2>创作路径</h2>'
        '<p>你可以直接写，也可以让外部 IDE 助手提交变更；只有人工确认并执行后的内容才会进入项目真相。</p>'
        '</div></div>'
        '<div class="aj-steps">'
        + _step(
            href="/create", index="1", title="创作真相", status=writing_status,
            detail=writing_detail, state=writing_state, active=active_path == "/create",
        )
        + _step(
            href="/director", index="2", title="审阅提案", status=proposal_status,
            detail=proposal_detail, state=proposal_state, active=active_path == "/director",
            optional=True,
        )
        + _step(
            href="/storyboard", index="3", title="建立分镜", status=storyboard_status,
            detail=storyboard_detail, state=storyboard_state,
            active=active_path == "/storyboard",
        )
        + '</div>'
        '<div class="aj-next"><span>现在最值得做</span>'
        f'<a href="{_e(next_item["href"])}">{_e(next_item["label"])} →</a>'
        f'<small>{_e(next_item["detail"])}</small></div>'
        '</section>'
    )


def render_authoring_attention(project: Any) -> str:
    """Render only an exceptional hand-off warning for the storyboard page.

    The shot-production journey owns the normal storyboard surface.  This
    compact banner appears only when authoring truth still needs attention, so
    the two journeys never compete in the healthy path.
    """
    data = authoring_journey_payload(project)
    writing = data["writing"]
    proposal = data["proposal"]
    storyboard = data["storyboard"]

    if proposal["truth_pending"]:
        stale = proposal["truth_stale"]
        title = "创作提案尚未决定"
        detail = (
            f'有 {proposal["truth_pending"]} 份创作变更仍在等待人工处理。'
            + (f'其中 {stale} 份已经过期，需要按当前项目重新起草。' if stale else '')
            + "当前分镜仍然基于已经落盘的项目真相。"
        )
        return (
            '<aside class="aj-alert panel aj-alert-attention">'
            f'<div><strong>{_e(title)}</strong><p>{_e(detail)}</p></div>'
            '<a class="btn" href="/director">审阅创作提案</a></aside>'
        )
    if proposal["error"]:
        return (
            '<aside class="aj-alert panel aj-alert-attention">'
            '<div><strong>提案状态暂时无法读取</strong>'
            '<p>现有分镜没有被修改；继续编辑前请检查导演助手中的提案记录。</p></div>'
            '<a class="btn" href="/director">检查提案</a></aside>'
        )
    if not writing["complete"] and storyboard["count"]:
        return (
            '<aside class="aj-alert panel">'
            '<div><strong>分镜早于故事材料完成</strong>'
            f'<p>四份故事材料目前完成 {writing["done"]}/{writing["total"]}。'
            '现有镜头仍可检查，但后续补写可能要求回头调整分镜。</p></div>'
            '<a class="btn ghost" href="/create">回到创作</a></aside>'
        )
    return ""


AUTHORING_JOURNEY_CSS = r"""
.aj { margin-bottom: 16px; padding: 14px; }
.aj-alert { display:flex; justify-content:space-between; align-items:center; gap:14px; margin-bottom:12px; border-left:3px solid var(--accent); }
.aj-alert-attention { border-left-color:#e0af68; }
.aj-alert strong { font-size:.88rem; }
.aj-alert p { margin:.25rem 0 0; color:var(--muted); font-size:.76rem; line-height:1.45; }
.aj-head { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; }
.aj-head h2 { margin:0; font-size:1rem; }
.aj-head p { margin:.25rem 0 0; color:var(--muted); font-size:.78rem; line-height:1.45; max-width:840px; }
.aj-steps { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; margin-top:12px; }
.aj-step { min-width:0; display:flex; gap:10px; padding:10px; border:1px solid var(--line); border-radius:10px; color:var(--fg); text-decoration:none; background:var(--panel2); }
.aj-step:hover { border-color:var(--accent); }
.aj-step[aria-current="step"] { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; background:var(--panel); }
.aj-index { width:24px; height:24px; flex:0 0 24px; display:grid; place-items:center; border-radius:999px; border:1px solid var(--line); color:var(--muted); font:700 .72rem var(--mono); }
.aj-copy { min-width:0; display:flex; flex-direction:column; gap:3px; }
.aj-title { display:flex; align-items:center; gap:6px; font-weight:700; font-size:.86rem; }
.aj-status { font-size:.78rem; color:var(--fg); }
.aj-detail { font-size:.72rem; line-height:1.35; color:var(--muted); }
.aj-optional { padding:1px 5px; border:1px solid var(--line); border-radius:999px; color:var(--muted); font-size:.66rem; font-weight:500; }
.aj-state-done .aj-index { border-color:var(--ok); color:var(--ok); }
.aj-state-current .aj-index { border-color:var(--accent); color:var(--accent); }
.aj-state-attention .aj-index { border-color:#e0af68; color:#e0af68; }
.aj-state-todo { opacity:.68; }
.aj-next { margin-top:10px; display:grid; grid-template-columns:auto minmax(0,1fr); column-gap:10px; align-items:baseline; padding-top:10px; border-top:1px solid var(--line); }
.aj-next > span { color:var(--muted); font-size:.72rem; }
.aj-next > a { color:var(--accent); font-weight:700; text-decoration:none; }
.aj-next > small { grid-column:2; color:var(--muted); line-height:1.35; }
@media (max-width:760px) {
  .aj-alert { align-items:flex-start; flex-direction:column; }
  .aj-alert .btn { width:100%; box-sizing:border-box; }
  .aj { padding:12px; }
  .aj-head p { font-size:.74rem; }
  .aj-steps { grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
  .aj-step { min-height:74px; padding:8px; flex-direction:column; gap:6px; }
  .aj-index { width:21px; height:21px; flex-basis:21px; font-size:.66rem; }
  .aj-title { font-size:.76rem; line-height:1.25; }
  .aj-status, .aj-detail, .aj-optional { display:none; }
  .aj-next { grid-template-columns:1fr; gap:3px; }
  .aj-next > small { grid-column:1; }
}
"""
