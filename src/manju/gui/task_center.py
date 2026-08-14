"""Shared, read-only task-center presentation for every GUI surface.

The GUI already has one authoritative task state machine: :mod:`manju.gui.jobs`.
This module does **not** create another queue, database, progress file or retry
protocol.  It only translates current Job/JobRunner facts into stable,
human-facing groups and phases, and serves one shared CSS/JavaScript surface so
home and every server-rendered page stop interpreting the same task differently.

Product-polish constraints:

* job state remains owned by ``JobRunner``;
* kind metadata remains owned by ``core.jobkinds``;
* paid confirmation remains owned by the existing waiting-user result;
* cancel/retry still use the existing HTTP endpoints;
* notification preferences are local browser state only;
* nothing here is a project/build/provider input.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..core import jobkinds

if TYPE_CHECKING:  # pragma: no cover - imports only for type checkers
    from .jobs import JobRunner

__all__ = [
    "present_job",
    "render_task_center_css",
    "render_task_center_js",
    "task_center_snapshot",
]

_GROUP_ORDER = {"attention": 0, "active": 1, "queued": 2, "recent": 3}

_NEXT_ACTIONS: dict[str, tuple[str, str]] = {
    "build": ("/exports", "查看成片"),
    "redo": ("/review", "去审片"),
    "redo_batch": ("/review", "去审片"),
    "voice": ("/review", "去审片"),
    "voice_batch": ("/review", "去审片"),
    "qc": ("/review", "查看质检"),
    "repair": ("/review", "去审片"),
    "export": ("/exports", "打开导出中心"),
    "ingest_plan": ("/ingest", "查看导入计划"),
    "ingest": ("/ingest#ing-review", "检查本批次"),
    "handle_rebuild": ("/edit", "回到剪辑"),
    "roundtrip": ("/edit", "查看外部修改"),
    "edit_preview": ("/edit", "回到剪辑"),
    "edit_preview_batch": ("/edit", "回到剪辑"),
    "series_sync_bible": ("/series", "查看剧集设定"),
}

_RECOVERY_ACTIONS: dict[str, tuple[str, str]] = {
    "build": ("/", "检查构建计划"),
    "redo": ("/lab", "回到镜头实验室"),
    "redo_batch": ("/lab", "回到镜头实验室"),
    "voice": ("/review", "回到审片"),
    "voice_batch": ("/review", "回到审片"),
    "qc": ("/review", "回到审片"),
    "repair": ("/review", "检查当前媒体"),
    "export": ("/exports", "回到导出中心"),
    "ingest_plan": ("/ingest", "回到批量入库"),
    "ingest": ("/ingest#ing-review", "检查本批次"),
    "handle_rebuild": ("/edit", "回到剪辑"),
    "roundtrip": ("/edit", "检查外部修改"),
    "edit_preview": ("/edit", "回到剪辑"),
    "edit_preview_batch": ("/edit", "回到剪辑"),
    "series_sync_bible": ("/series", "检查剧集设定"),
}

_RUNNING_DEFAULTS = {
    "build": "正在构建项目",
    "redo": "正在生成新候选",
    "redo_batch": "正在批量生成候选",
    "voice": "正在生成配音",
    "voice_batch": "正在批量生成配音",
    "qc": "正在检查质量",
    "repair": "正在修复媒体",
    "export": "正在写出文件",
    "ingest_plan": "正在分析导入文件",
    "ingest": "正在登记素材",
    "series_sync_bible": "正在同步剧集设定",
    "edit_preview_batch": "正在生成批量预览",
    "voice_preview": "正在生成试听",
    "handle_rebuild": "正在重建补拍手柄",
    "roundtrip": "正在分析外部修改",
    "series_new_episode": "正在创建新一集",
    "edit_preview": "正在生成剪辑预览",
}

_EXACT_PHASES = {
    "check": "检查项目",
    "generate": "生成候选",
    "voice": "生成配音",
    "compile": "编排时间线",
    "captions": "生成字幕",
    "render:audition": "渲染试听版",
    "render:animatic": "渲染动态分镜",
    "render:final:locale": "渲染本地化成片",
    "render:final": "渲染成片",
    "render:proxy": "渲染预览",
    "qc": "检查质量",
    "exports": "生成交付文件",
}

_ACTION_PHASES = {
    "build": "构建项目",
    "redo": "生成新候选",
    "voice": "生成配音",
    "repair": "修复媒体",
    "mixer": "更新混音",
    "captions": "更新字幕",
    "packaging": "更新包装",
    "snapshot": "创建版本快照",
    "rollback": "恢复版本",
    "truth_patch_set": "写入已确认的创作修改",
}

_GEN_RE = re.compile(r"^gen:([^\s]+)\s*\((\d+)\s*/\s*(\d+)\)$")
_ACTION_RE = re.compile(r"^action\s+(\d+)\s*/\s*(\d+)\s*:\s*(.+)$")


def _as_dict(job: Any) -> dict[str, Any]:
    if isinstance(job, Mapping):
        return dict(job)
    fn = getattr(job, "to_dict", None)
    if callable(fn):
        raw = fn()
        if isinstance(raw, Mapping):
            return dict(raw)
    return {}


def _one_line(value: Any, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def _timestamp(value: Any) -> str:
    text = str(value or "")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return text


def _context(rec: Mapping[str, Any]) -> str:
    params = rec.get("params")
    result = rec.get("result")
    params = params if isinstance(params, Mapping) else {}
    result = result if isinstance(result, Mapping) else {}
    parts: list[str] = []
    for key in ("shot", "shot_id"):
        value = params.get(key) or result.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
            break
    lang = params.get("lang") or result.get("lang")
    if isinstance(lang, str) and lang.strip():
        parts.append(lang.strip())
    return " · ".join(parts)


def _phase_view(
    *,
    kind: str,
    state: str,
    progress: str,
    waiting_user: bool,
    waiting_cost: bool,
    billing_unknown: bool,
) -> dict[str, str]:
    raw = progress.strip()
    if waiting_user:
        return {
            "key": "waiting_user",
            "label": "等待你确认费用" if waiting_cost else "等待你确认",
            "detail": "付费步骤尚未执行" if waiting_cost else "操作尚未执行",
            "raw": progress,
        }
    if state == "queued":
        return {"key": "queued", "label": "等待前面的任务", "detail": "", "raw": progress}
    if state == "canceling":
        return {
            "key": "canceling",
            "label": "正在请求安全停止",
            "detail": "任务会在支持的检查点停止",
            "raw": progress,
        }
    if state == "interrupted":
        return {
            "key": "interrupted",
            "label": "上次退出时中断",
            "detail": "实际结果未知，请先核对产物",
            "raw": progress,
        }
    if state == "failed":
        return {"key": "failed", "label": "任务失败", "detail": "需要你处理", "raw": progress}
    if state == "canceled":
        if billing_unknown:
            return {
                "key": "cancel_unknown",
                "label": "取消结果待核对",
                "detail": "需要核对远端状态",
                "raw": progress,
            }
        return {"key": "canceled", "label": "已取消", "detail": "", "raw": progress}
    if state == "done":
        if raw in _EXACT_PHASES:
            return {"key": raw, "label": _EXACT_PHASES[raw], "detail": "已完成", "raw": raw}
        match = _GEN_RE.match(raw)
        if match:
            shot, index, total = match.groups()
            return {
                "key": "generate_item",
                "label": f"生成 {shot}",
                "detail": f"已完成 · 第 {index} / {total} 个",
                "raw": raw,
            }
        match = _ACTION_RE.match(raw)
        if match:
            index, total, action = match.groups()
            label = _ACTION_PHASES.get(action, "执行已确认提案")
            return {
                "key": "proposal_action",
                "label": label,
                "detail": f"已完成 · 第 {index} / {total} 项",
                "raw": raw,
            }
        if raw.startswith("render:"):
            target = raw.split(":", 1)[1]
            return {
                "key": "render",
                "label": "渲染成片",
                "detail": f"已完成 · {target}",
                "raw": raw,
            }
        fallback = _RUNNING_DEFAULTS.get(kind, "处理任务")
        if fallback.startswith("正在"):
            fallback = fallback[2:] or "处理任务"
        return {"key": "done", "label": fallback, "detail": "已完成", "raw": raw}

    if raw in _EXACT_PHASES:
        return {"key": raw, "label": _EXACT_PHASES[raw], "detail": "", "raw": raw}
    match = _GEN_RE.match(raw)
    if match:
        shot, index, total = match.groups()
        return {
            "key": "generate_item",
            "label": f"生成 {shot}",
            "detail": f"第 {index} / {total} 个",
            "raw": raw,
        }
    match = _ACTION_RE.match(raw)
    if match:
        index, total, action = match.groups()
        label = _ACTION_PHASES.get(action, "执行已确认提案")
        return {
            "key": "proposal_action",
            "label": label,
            "detail": f"第 {index} / {total} 项",
            "raw": raw,
        }
    if raw.startswith("render:"):
        target = raw.split(":", 1)[1]
        return {
            "key": "render",
            "label": "正在渲染",
            "detail": target,
            "raw": raw,
        }
    if raw:
        return {"key": "running", "label": _RUNNING_DEFAULTS.get(kind, "正在处理"),
                "detail": raw, "raw": raw}
    return {"key": "running", "label": _RUNNING_DEFAULTS.get(kind, "正在处理"),
            "detail": "", "raw": ""}


def _next_action(
    kind: str,
    *,
    waiting_user: bool,
    waiting_cost: bool,
    state: str,
    context: str,
    billing_unknown: bool,
) -> dict[str, str] | None:
    if waiting_user:
        if waiting_cost:
            return {"href": "/", "label": "查看费用计划"}
        target = _RECOVERY_ACTIONS.get(kind)
        return {
            "href": target[0] if target else "/",
            "label": "查看待确认操作",
        }
    if billing_unknown:
        return {"href": "/providers", "label": "核对 Provider 状态"}
    if state == "done":
        target = _NEXT_ACTIONS.get(kind)
    elif state in {"failed", "interrupted", "canceled"}:
        target = _RECOVERY_ACTIONS.get(kind)
    else:
        target = None
    if target is None:
        return None
    href = target[0]
    # Shot-level recovery should take the operator back to the same shot when
    # that context is available.  The context is presentation-only and is
    # quoted before it enters a URL; no project truth is inferred here.
    if context and kind in {"redo", "voice", "qc", "repair"}:
        shot = context.split(" · ", 1)[0]
        if shot:
            href += ("&" if "?" in href else "?") + "shot=" + quote(shot, safe="")
    return {"href": href, "label": target[1]}


def _cost_view(result: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = result.get("estimated_cost")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    currency = _one_line(result.get("currency"), 24)
    number = f"{value:.2f}".rstrip("0").rstrip(".")
    label = f"预估费用 {number}" + (f" {currency}" if currency else "")
    return {"estimated": value, "currency": currency, "label": label}


def present_job(job: Any) -> dict[str, Any]:
    """Translate one Job/Job dict into stable, presentation-only facts."""
    rec = _as_dict(job)
    kind = str(rec.get("kind") or "")
    spec = jobkinds.spec(kind)
    label = spec.display_name_zh if spec is not None else (kind or "任务")
    state = str(rec.get("state") or "")
    result = rec.get("result") if isinstance(rec.get("result"), Mapping) else {}
    billing = rec.get("billing") if isinstance(rec.get("billing"), Mapping) else {}
    waiting_user = bool(result.get("waiting_user") is True)
    cost = _cost_view(result)
    waiting_cost = bool(cost and cost["estimated"] > 0)
    billing_unknown = bool(billing.get("may_have_billed") is True)
    progress = _one_line(rec.get("progress"), 180)
    phase = _phase_view(
        kind=kind,
        state=state,
        progress=progress,
        waiting_user=waiting_user,
        waiting_cost=waiting_cost,
        billing_unknown=billing_unknown,
    )

    if waiting_user:
        state_key, state_label, group, tone = "waiting_user", "等待确认", "attention", "warning"
    elif state == "failed":
        state_key, state_label, group, tone = state, "失败", "attention", "danger"
    elif state == "interrupted":
        state_key, state_label, group, tone = state, "已中断", "attention", "warning"
    elif state == "canceled" and billing_unknown:
        state_key, state_label, group, tone = "cancel_unknown", "待核对", "attention", "warning"
    elif state in ("running", "canceling"):
        state_key = state
        state_label = "运行中" if state == "running" else "取消中"
        group, tone = "active", "active"
    elif state == "queued":
        state_key, state_label, group, tone = state, "排队中", "queued", "neutral"
    elif state == "canceled":
        state_key, state_label, group, tone = state, "已取消", "recent", "neutral"
    else:
        state_key, state_label, group, tone = state or "unknown", "已完成" if state == "done" else "状态未知", "recent", "success" if state == "done" else "neutral"

    context = _context(rec)
    title = label + (f" · {context}" if context else "")
    cancelable = bool(rec.get("cancelable"))
    paid = bool(spec.paid) if spec is not None else False
    cancel: dict[str, Any] | None = None
    if cancelable:
        if state == "queued":
            cancel = {
                "label": "移出队列",
                "confirm": False,
                "message": "该任务尚未运行，移出队列不会产生新的费用。",
            }
        elif state == "running":
            cancel = {
                "label": "请求取消",
                "confirm": paid,
                "message": (
                    "会在安全检查点请求停止；远端任务可能仍继续并计费，"
                    "系统会保留任务身份并避免重复提交。"
                    if paid else
                    "会在任务支持的安全检查点停止；已经完成的本地产物会保留。"
                ),
            }

    error = _one_line(rec.get("error"), 360)
    note = _one_line(rec.get("note"), 360)
    billing_message = ""
    if billing_unknown:
        billing_message = "远端取消尚未确认，任务可能仍在运行并计费。"
        remote_id = billing.get("provider_job_id")
        if remote_id:
            billing_message += f" 远程任务：{remote_id}。"

    notification_state = state_key
    notification_body = phase["label"]
    if waiting_user:
        notification_body = (
            ((cost["label"] + "；费用尚未确认，付费步骤尚未执行。")
             if waiting_cost else "操作尚未确认，因此没有执行。")
        )
    elif state == "failed" and error:
        notification_body = error
    elif state == "interrupted":
        notification_body = "上次退出时任务仍在运行，请核对产物后从原页面重新发起。"
    elif billing_unknown:
        notification_body = billing_message

    return {
        "id": str(rec.get("id") or ""),
        "kind": kind,
        "label": label,
        "context": context,
        "title": title,
        "state": state,
        "state_key": state_key,
        "state_label": state_label,
        "group": group,
        "tone": tone,
        "phase": phase,
        "created": _timestamp(rec.get("created")),
        "started": _timestamp(rec.get("started")),
        "finished": _timestamp(rec.get("finished")),
        "cancelable": cancelable,
        "cancel": cancel,
        # A canceled paid task whose remote outcome is uncertain must never
        # advertise an immediate retry: that is exactly the state in which a
        # duplicate paid submission is most dangerous.
        "retryable": bool(rec.get("retryable")) and not billing_unknown,
        "retry_of": str(rec.get("retry_of") or ""),
        "paid": paid,
        "waiting_user": waiting_user,
        "billing_unknown": billing_unknown,
        "billing_message": billing_message,
        "cost": cost,
        "error": error,
        "note": note,
        "next_action": _next_action(
            kind,
            waiting_user=waiting_user,
            waiting_cost=waiting_cost,
            state=state,
            context=context,
            billing_unknown=billing_unknown,
        ),
        "notification": {
            "key": f"{rec.get('id') or ''}:{notification_state}",
            "title": f"Manju · {label} · {state_label}",
            "body": notification_body,
        },
    }


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: list[dict[str, Any]] = []
    for group in ("attention", "active", "queued", "recent"):
        rows = [item for item in items if item.get("group") == group]
        def stamp(item: dict[str, Any]) -> str:
            if group == "recent":
                return str(item.get("finished") or item.get("started") or item.get("created") or "")
            if group == "queued":
                return str(item.get("created") or "")
            return str(item.get("started") or item.get("created") or item.get("finished") or "")
        rows.sort(
            key=lambda item: (stamp(item), str(item.get("id") or "")),
            reverse=True,
        )
        grouped.extend(rows)
    grouped.extend(sorted(
        [item for item in items if item.get("group") not in _GROUP_ORDER],
        key=lambda item: (
            _GROUP_ORDER.get(str(item.get("group")), 9),
            str(item.get("finished") or item.get("started") or item.get("created") or ""),
            str(item.get("id") or ""),
        ),
    ))
    return grouped


def task_center_snapshot(
    runner: "JobRunner",
    *,
    app_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Current read-only task-center projection for one bound GUI session."""
    raw = [j.to_dict() for j in runner.list()] + runner.interrupted()
    items = _sort_items([present_job(job) for job in raw])
    app = dict(app_status or {})
    policy = app.get("execution_policy")
    policy = policy if isinstance(policy, Mapping) else {}
    provider_blocked = policy.get("status") in {"strict", "invalid"}
    for item in items:
        item["billing_risk"] = bool(item.get("paid")) and not provider_blocked
        cancel = item.get("cancel")
        if (
            provider_blocked
            and isinstance(cancel, dict)
            and item.get("state") == "running"
            and item.get("paid")
        ):
            cancel["confirm"] = False
            cancel["message"] = (
                "当前执行模式已阻止外部 Provider 和凭据读取；"
                "会在任务支持的安全检查点停止，已完成的本地产物会保留。"
            )
    counts = {"attention": 0, "active": 0, "queued": 0, "recent": 0}
    for item in items:
        group = str(item.get("group") or "")
        if group in counts:
            counts[group] += 1
    counts["total"] = len(items)
    counts["in_progress"] = counts["active"] + counts["queued"]
    digest_payload = [
        {
            "id": item["id"],
            "state": item["state_key"],
            "phase": item["phase"].get("raw") or item["phase"].get("key"),
            "finished": item["finished"],
        }
        for item in items
    ]
    revision = hashlib.sha256(
        json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "version": 1,
        "revision": revision,
        "summary": counts,
        "items": items,
        "app": app,
        "poll_after_ms": 1800 if counts["in_progress"] else (5000 if counts["attention"] else 10000),
    }


TASK_CENTER_CSS = r"""
.mj-task-button,.mj-quit-button {
  min-height:32px; padding:.32rem .58rem; border:1px solid var(--line,#39404d);
  border-radius:8px; background:transparent; color:var(--fg,#e8eaf0);
  font:inherit; cursor:pointer; display:inline-flex; align-items:center; gap:.38rem;
  white-space:nowrap; flex:0 0 auto;
}
.mj-task-button:hover,.mj-quit-button:hover { background:var(--panel2,#232936); }
.mj-task-button.active { border-color:#416a9e; background:#1d2a3e; }
.mj-task-button.attention { border-color:#9f7930; background:#332815; }
.mj-task-button.complete { border-color:#3e7355; background:#173023; }
.mj-task-button.complete .mj-task-count { background:#3d8b60; }
.mj-task-count {
  min-width:1.25rem; height:1.25rem; padding:0 .28rem; border-radius:999px;
  display:inline-flex; align-items:center; justify-content:center;
  background:#426fa8; color:#fff; font-size:.72rem; font-variant-numeric:tabular-nums;
}
.mj-task-button.attention .mj-task-count { background:#b98225; color:#fff; }
.mj-task-scrim {
  position:fixed; inset:0; z-index:9700; background:rgba(8,10,14,.58);
}
.mj-task-drawer {
  position:fixed; z-index:9701; top:.65rem; right:.65rem; bottom:.65rem;
  width:min(29rem,calc(100vw - 1.3rem)); display:flex; flex-direction:column;
  overflow:hidden; border:1px solid var(--line,#39404d); border-radius:14px;
  background:var(--panel,#181b21); color:var(--fg,#eceef2);
  box-shadow:0 20px 60px rgba(0,0,0,.55);
}
.mj-task-head {
  display:flex; align-items:flex-start; justify-content:space-between; gap:1rem;
  padding:1rem 1rem .8rem; border-bottom:1px solid var(--line,#39404d);
}
.mj-task-head h2 { margin:0; font-size:1.15rem; }
.mj-task-head p { margin:.22rem 0 0; color:var(--muted,#98a1b3); font-size:.82rem; }
.mj-task-scope-note { max-width:25rem; line-height:1.42; }
.mj-task-close {
  width:32px; height:32px; border:1px solid var(--line,#39404d); border-radius:8px;
  background:transparent; color:inherit; cursor:pointer; font-size:1.1rem;
}
.mj-task-app-note,.mj-task-message {
  margin:.75rem 1rem 0; padding:.65rem .75rem; border-radius:9px;
  border:1px solid var(--line,#39404d); background:var(--panel2,#222833);
  font-size:.82rem; line-height:1.45;
}
.mj-task-app-note.warn,.mj-task-message.warn { border-color:#866725; background:#302714; }
.mj-task-app-note.bad,.mj-task-message.bad { border-color:#8a4247; background:#30191c; }
.mj-task-body { overflow:auto; padding:.35rem 1rem 1rem; overscroll-behavior:contain; }
.mj-task-group { margin-top:.85rem; }
.mj-task-more { margin:.45rem 0 0; }
.mj-task-more > summary {
  cursor:pointer; color:var(--muted,#98a1b3); font-size:.76rem;
  padding:.38rem .2rem;
}
.mj-task-group-title {
  display:flex; align-items:center; justify-content:space-between; gap:.5rem;
  color:var(--muted,#98a1b3); font-size:.76rem; font-weight:700;
  letter-spacing:.02em; text-transform:none; margin:0 0 .35rem;
}
.mj-task-item {
  border:1px solid var(--line,#39404d); border-radius:10px;
  background:var(--panel2,#202630); padding:.72rem .78rem; margin:.42rem 0;
}
.mj-task-item.danger { border-color:#844047; }
.mj-task-item.warning { border-color:#826323; }
.mj-task-item.active { border-color:#3b6798; }
.mj-task-row { display:flex; align-items:center; justify-content:space-between; gap:.6rem; }
.mj-task-title { min-width:0; font-weight:700; overflow-wrap:anywhere; }
.mj-task-state {
  flex:0 0 auto; border-radius:999px; padding:.12rem .48rem;
  background:#313845; color:#c9d0dd; font-size:.7rem;
}
.mj-task-item.danger .mj-task-state { background:#4c252a; color:#ffb6ba; }
.mj-task-item.warning .mj-task-state { background:#4b3918; color:#ffd36f; }
.mj-task-item.active .mj-task-state { background:#203b5b; color:#a9ceff; }
.mj-task-phase { margin-top:.3rem; font-size:.84rem; }
.mj-task-phase small { color:var(--muted,#98a1b3); margin-left:.35rem; }
.mj-task-progress {
  height:3px; margin:.58rem 0 .2rem; overflow:hidden; border-radius:999px;
  background:#303744;
}
.mj-task-progress::after {
  content:""; display:block; height:100%; width:34%; border-radius:inherit;
  background:var(--accent,#6ea8fe); animation:mj-task-run 1.35s ease-in-out infinite alternate;
}
@keyframes mj-task-run { from { transform:translateX(-20%); } to { transform:translateX(210%); } }
.mj-task-note { margin:.48rem 0 0; color:var(--muted,#a9b0bc); font-size:.78rem; line-height:1.45; overflow-wrap:anywhere; }
.mj-task-note.bad { color:#ffb2b7; }
.mj-task-cost {
  margin:.5rem 0 0; padding:.5rem .58rem; border-radius:8px;
  border:1px solid #826323; background:#302714; color:#ffd777;
  font-size:.78rem; line-height:1.4;
}
.mj-task-actions { display:flex; flex-wrap:wrap; gap:.42rem; margin-top:.62rem; }
.mj-task-actions button,.mj-task-actions a {
  min-height:30px; padding:.25rem .58rem; border:1px solid var(--line,#46505e);
  border-radius:7px; background:transparent; color:inherit; font:inherit;
  font-size:.78rem; text-decoration:none; cursor:pointer; display:inline-flex;
  align-items:center;
}
.mj-task-actions a.primary { background:#244b78; border-color:#3c6f9f; }
.mj-task-actions button:disabled { opacity:.55; cursor:wait; }
.mj-task-tech { margin-top:.55rem; font-size:.75rem; color:var(--muted,#98a1b3); }
.mj-task-tech summary { cursor:pointer; }
.mj-task-tech dl { display:grid; grid-template-columns:auto 1fr; gap:.22rem .55rem; margin:.45rem 0 0; }
.mj-task-tech dt { color:#858e9e; }
.mj-task-tech dd { margin:0; overflow-wrap:anywhere; }
.mj-task-empty { padding:1.4rem .4rem; color:var(--muted,#98a1b3); text-align:center; }
.mj-task-settings {
  border-top:1px solid var(--line,#39404d); padding:.65rem 1rem .85rem;
  background:var(--panel,#181b21);
}
.mj-task-settings summary { cursor:pointer; color:var(--muted,#98a1b3); font-size:.78rem; }
.mj-task-settings-row { display:flex; flex-wrap:wrap; align-items:center; gap:.5rem; margin-top:.58rem; }
.mj-task-settings button {
  min-height:30px; padding:.25rem .58rem; border:1px solid var(--line,#46505e);
  border-radius:7px; background:transparent; color:inherit; cursor:pointer;
}
.mj-task-confirm-scrim {
  position:fixed; inset:0; z-index:9800; display:grid; place-items:center;
  padding:1rem; background:rgba(6,8,12,.72);
}
.mj-task-confirm {
  width:min(30rem,100%); border:1px solid #8a5558; border-radius:12px;
  background:var(--panel,#181b21); color:var(--fg,#eceef2);
  padding:1rem 1.05rem; box-shadow:0 22px 65px rgba(0,0,0,.58);
}
.mj-task-confirm h3 { margin:0 0 .55rem; font-size:1.05rem; }
.mj-task-confirm p { margin:0; color:#c7cbd3; line-height:1.55; white-space:pre-line; }
.mj-task-confirm-actions { display:flex; justify-content:flex-end; flex-wrap:wrap; gap:.5rem; margin-top:1rem; }
.mj-task-confirm-actions button {
  min-height:34px; padding:.35rem .72rem; border:1px solid var(--line,#46505e);
  border-radius:7px; background:#2a303b; color:inherit; font:inherit; cursor:pointer;
}
.mj-task-confirm-actions button.danger { border-color:#98555b; background:#57272c; color:#ffd7da; }
body.mj-task-center-open { overflow:hidden; }
@media (max-width:600px) {
  .mj-task-drawer { inset:.35rem; width:auto; }
  .mj-task-head { padding:.8rem .8rem .65rem; }
  .mj-task-body { padding:.25rem .75rem .85rem; }
  .mj-task-app-note,.mj-task-message { margin:.6rem .75rem 0; }
  .mj-task-settings { padding:.6rem .75rem .75rem; }
  .mj-task-label { display:none; }
  .mj-task-button,.mj-quit-button { padding:.3rem .48rem; }
}
@media (prefers-reduced-motion:reduce) {
  .mj-task-progress::after { animation:none; width:100%; opacity:.55; }
}
"""


TASK_CENTER_JS = r"""
"use strict";
(function () {
  var button = document.getElementById("mj-task-center-btn");
  var badge = document.getElementById("mj-task-center-count");
  var quitButton = document.getElementById("mj-quit-btn");
  if (!button) return;

  var meta = document.querySelector('meta[name="manju-token"]');
  var token = meta ? (meta.getAttribute("content") || "") : "";
  var projectMeta = document.querySelector('meta[name="manju-project"]');
  var project = projectMeta ? (projectMeta.getAttribute("content") || "") : "";
  var baseTitle = document.title;
  var snapshot = null;
  var drawer = null;
  var scrim = null;
  var confirmOverlay = null;
  var timer = null;
  var refreshPromise = null;
  var returnFocus = null;
  var storageStem = "mj-task-center:" + (project || location.pathname);
  var firstFetch = true;

  function api(method, path, body) {
    if (typeof requestJson !== "function") {
      return Promise.reject(new Error("共享请求层尚未加载，请刷新页面后重试"));
    }
    var opts = { token: token };
    if (project) opts.projectId = project;
    return requestJson(method, path, body, opts);
  }

  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function clear(n) { while (n && n.firstChild) n.removeChild(n.firstChild); }
  function localGet(key, fallback) {
    try { var v = localStorage.getItem(key); return v === null ? fallback : v; }
    catch (err) { return fallback; }
  }
  function localSet(key, value) { try { localStorage.setItem(key, value); } catch (err) {} }
  function sessionGet(key, fallback) {
    try { var v = sessionStorage.getItem(key); return v === null ? fallback : v; }
    catch (err) { return fallback; }
  }
  function sessionSet(key, value) { try { sessionStorage.setItem(key, value); } catch (err) {} }
  function parseMap(raw) { try { var x = JSON.parse(raw); return x && typeof x === "object" ? x : {}; } catch (err) { return {}; } }
  /* A navigation reloads this script but sessionStorage survives. If the prior
   * page already observed a running job, the first fetch on the new page must
   * still be able to announce its terminal transition exactly once. */
  firstFetch = sessionGet(storageStem + ":states", "") === "";

  function notificationEnabled() { return localGet("mj-task-center-notify", "0") === "1"; }
  function soundEnabled() { return localGet("mj-notify-sound", "0") === "1"; }
  function playSound() {
    if (!soundEnabled()) return;
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.value = 740;
      gain.gain.setValueAtTime(.055, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(.0001, ctx.currentTime + .3);
      osc.addEventListener("ended", function () {
        try { ctx.close(); } catch (closeErr) {}
      });
      osc.start(); osc.stop(ctx.currentTime + .34);
    } catch (err) {}
  }

  function ensureDrawer() {
    if (drawer && document.body.contains(drawer)) return drawer;
    scrim = node("div", "mj-task-scrim");
    scrim.hidden = true;
    scrim.setAttribute("aria-hidden", "true");
    scrim.addEventListener("click", closeDrawer);
    drawer = node("aside", "mj-task-drawer");
    drawer.id = "mj-task-center";
    drawer.hidden = true;
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute("aria-labelledby", "mj-task-title");
    drawer.setAttribute("aria-describedby", "mj-task-summary mj-task-scope-note");

    var head = node("div", "mj-task-head");
    var copy = node("div");
    var title = node("h2", null, "任务中心");
    title.id = "mj-task-title";
    copy.appendChild(title);
    var summary = node("p", null, "当前没有任务");
    summary.id = "mj-task-summary";
    copy.appendChild(summary);
    var scope = node("p", "mj-task-scope-note", "可以切换页面，任务会继续；关闭 Manju 请使用右上角“退出”。");
    scope.id = "mj-task-scope-note";
    copy.appendChild(scope);
    head.appendChild(copy);
    var close = node("button", "mj-task-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "关闭任务中心");
    close.addEventListener("click", closeDrawer);
    head.appendChild(close);
    drawer.appendChild(head);

    var appNote = node("div", "mj-task-app-note");
    appNote.id = "mj-task-app-note";
    appNote.hidden = true;
    drawer.appendChild(appNote);
    var message = node("div", "mj-task-message");
    message.id = "mj-task-message";
    message.hidden = true;
    message.setAttribute("role", "status");
    message.setAttribute("aria-live", "polite");
    drawer.appendChild(message);
    var body = node("div", "mj-task-body");
    body.id = "mj-task-body";
    drawer.appendChild(body);

    var settings = node("details", "mj-task-settings");
    settings.appendChild(node("summary", null, "后台提醒设置"));
    var setRow = node("div", "mj-task-settings-row");
    var notify = node("button");
    notify.type = "button";
    notify.id = "mj-task-notify-toggle";
    notify.addEventListener("click", toggleNotifications);
    setRow.appendChild(notify);
    var sound = node("button");
    sound.type = "button";
    sound.id = "mj-task-sound-toggle";
    sound.addEventListener("click", function () {
      localSet("mj-notify-sound", soundEnabled() ? "0" : "1");
      renderSettings();
      if (soundEnabled()) playSound();
    });
    setRow.appendChild(sound);
    settings.appendChild(setRow);
    settings.appendChild(node("p", "mj-task-note", "提醒偏好只保存在当前浏览器，不会写入项目。"));
    drawer.appendChild(settings);

    document.body.appendChild(scrim);
    document.body.appendChild(drawer);
    return drawer;
  }

  function renderSettings() {
    if (!drawer) return;
    var notify = document.getElementById("mj-task-notify-toggle");
    var sound = document.getElementById("mj-task-sound-toggle");
    if (notify) {
      if (!("Notification" in window)) {
        notify.textContent = "系统提醒不可用";
        notify.disabled = true;
      } else if (Notification.permission === "denied") {
        notify.textContent = "系统提醒已被浏览器阻止";
        notify.disabled = true;
      } else {
        notify.disabled = false;
        notify.textContent = notificationEnabled() ? "关闭系统提醒" : "开启系统提醒";
      }
    }
    if (sound) sound.textContent = soundEnabled() ? "关闭完成提示音" : "开启完成提示音";
  }

  function toggleNotifications() {
    if (!("Notification" in window)) return;
    if (notificationEnabled()) {
      localSet("mj-task-center-notify", "0");
      renderSettings();
      return;
    }
    if (Notification.permission === "granted") {
      localSet("mj-task-center-notify", "1");
      renderSettings();
      showMessage("后台系统提醒已开启。", "");
      return;
    }
    if (Notification.permission === "default") {
      Notification.requestPermission().then(function (permission) {
        localSet("mj-task-center-notify", permission === "granted" ? "1" : "0");
        renderSettings();
        showMessage(permission === "granted" ? "后台系统提醒已开启。" : "没有开启系统提醒。", permission === "granted" ? "" : "warn");
      }).catch(function () { showMessage("无法开启系统提醒。", "warn"); });
    }
  }

  function showMessage(text, tone) {
    ensureDrawer();
    var box = document.getElementById("mj-task-message");
    if (!box) return;
    box.className = "mj-task-message" + (tone ? " " + tone : "");
    box.textContent = text;
    box.hidden = false;
  }

  function clearMessage() {
    var box = document.getElementById("mj-task-message");
    if (box) box.hidden = true;
  }

  function openDrawer() {
    ensureDrawer();
    if (drawer.hidden) returnFocus = document.activeElement;
    drawer.hidden = false;
    scrim.hidden = false;
    document.body.classList.add("mj-task-center-open");
    button.setAttribute("aria-expanded", "true");
    clearUnseen();
    render();
    var close = drawer.querySelector(".mj-task-close");
    if (close) close.focus();
    refreshNow();
  }

  function closeDrawer() {
    if (!drawer || confirmOverlay) return;
    drawer.hidden = true;
    if (scrim) scrim.hidden = true;
    document.body.classList.remove("mj-task-center-open");
    button.setAttribute("aria-expanded", "false");
    if (returnFocus && typeof returnFocus.focus === "function") returnFocus.focus();
    returnFocus = null;
  }

  function clearUnseen() {
    sessionSet(storageStem + ":unseen", "0");
    document.title = baseTitle;
  }

  function setUnseen(n) {
    sessionSet(storageStem + ":unseen", String(Math.max(0, n || 0)));
    document.title = (n > 0 && document.hidden)
      ? ("(" + n + " 条任务消息) " + baseTitle)
      : baseTitle;
  }

  function humanDuration(item) {
    var start = Date.parse(item.started || item.created || "");
    if (!isFinite(start)) return "";
    var end = item.finished ? Date.parse(item.finished) : Date.now();
    if (!isFinite(end) || end < start) return "";
    var seconds = Math.round((end - start) / 1000);
    if (seconds < 60) return seconds + " 秒";
    var minutes = Math.floor(seconds / 60);
    var rest = seconds % 60;
    return minutes + " 分" + (rest ? (" " + rest + " 秒") : "");
  }

  function technical(item) {
    var details = node("details", "mj-task-tech");
    details.appendChild(node("summary", null, "技术详情"));
    var dl = node("dl");
    function pair(name, value) {
      if (!value) return;
      dl.appendChild(node("dt", null, name));
      dl.appendChild(node("dd", null, value));
    }
    pair("任务", item.id);
    pair("类型", item.kind);
    pair("原始阶段", item.phase && item.phase.raw);
    pair("开始", item.started);
    pair("结束", item.finished);
    pair("重试来源", item.retry_of);
    details.appendChild(dl);
    return details;
  }

  function focusable(root) {
    return Array.prototype.slice.call(root.querySelectorAll(
      'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),details>summary,[tabindex]:not([tabindex="-1"])'
    )).filter(function (el) { return !el.hidden && el.offsetParent !== null; });
  }

  function closeConfirm() {
    if (!confirmOverlay) return;
    var target = confirmOverlay.__returnFocus;
    if (confirmOverlay.parentNode) confirmOverlay.parentNode.removeChild(confirmOverlay);
    confirmOverlay = null;
    if (target && typeof target.focus === "function") target.focus();
  }

  function confirmCancel(item, action) {
    if (!item.cancel || !item.cancel.confirm) { action(); return; }
    if (confirmOverlay) return;
    var text = item.cancel.message || "仍要请求取消吗？";
    var returnTarget = document.activeElement;
    var ov = node("div", "mj-task-confirm-scrim");
    ov.setAttribute("role", "presentation");
    var dialog = node("div", "mj-task-confirm");
    dialog.setAttribute("role", "alertdialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "mj-task-confirm-title");
    dialog.setAttribute("aria-describedby", "mj-task-confirm-copy");
    var title = node("h3", null, "取消可能产生费用的任务？");
    title.id = "mj-task-confirm-title";
    dialog.appendChild(title);
    var copy = node("p", null, text + "\n\nManju 会保留任务身份，避免把不确定结果自动当作可以重试。");
    copy.id = "mj-task-confirm-copy";
    dialog.appendChild(copy);
    var row = node("div", "mj-task-confirm-actions");
    var keep = actionButton("继续等待", closeConfirm);
    var proceed = actionButton("请求取消", function () {
      proceed.disabled = true;
      closeConfirm();
      action();
    });
    proceed.className = "danger";
    row.appendChild(keep);
    row.appendChild(proceed);
    dialog.appendChild(row);
    ov.appendChild(dialog);
    ov.addEventListener("click", function (event) {
      if (event.target === ov) closeConfirm();
    });
    ov.__returnFocus = returnTarget;
    confirmOverlay = ov;
    document.body.appendChild(ov);
    keep.focus();
  }

  function actionButton(label, fn) {
    var b = node("button", null, label);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  }

  function renderItem(item) {
    var card = node("article", "mj-task-item " + (item.tone || ""));
    var top = node("div", "mj-task-row");
    top.appendChild(node("div", "mj-task-title", item.title || item.label || "任务"));
    top.appendChild(node("span", "mj-task-state", item.state_label || item.state_key));
    card.appendChild(top);

    var phase = node("div", "mj-task-phase", (item.phase && item.phase.label) || "");
    var duration = humanDuration(item);
    var detail = (item.phase && item.phase.detail) || "";
    if (detail || duration) phase.appendChild(node("small", null, [detail, duration].filter(Boolean).join(" · ")));
    card.appendChild(phase);
    if (item.group === "active") card.appendChild(node("div", "mj-task-progress"));

    var note = item.billing_message || item.error || item.note || "";
    if (note) card.appendChild(node("p", "mj-task-note" + (item.tone === "danger" ? " bad" : ""), note));
    if (item.waiting_user && item.cost && Number(item.cost.estimated || 0) > 0 && item.cost.label) {
      card.appendChild(node("p", "mj-task-cost", item.cost.label + "；尚未执行付费步骤。"));
    }

    var actions = node("div", "mj-task-actions");
    if (item.next_action) {
      var a = node("a", "primary", item.next_action.label + " →");
      a.href = item.next_action.href;
      actions.appendChild(a);
    }
    if (item.cancel) {
      var cancel = actionButton(item.cancel.label, function () {
        confirmCancel(item, function () {
          cancel.disabled = true;
          api("POST", "/api/jobs/cancel", { job_id: item.id }).then(function () {
            showMessage("已请求取消；系统会在安全检查点更新状态。", "");
            refreshNow();
          }).catch(function (err) {
            cancel.disabled = false;
            showMessage("取消失败：" + ((err && err.message) || String(err)), "bad");
          });
        });
      });
      actions.appendChild(cancel);
    }
    if (item.retryable) {
      var retry = actionButton("重新发起", function () {
        retry.disabled = true;
        api("POST", "/api/jobs/retry", { job_id: item.id }).then(function () {
          showMessage("已重新发起；任何付费步骤仍需再次确认。", "");
          refreshNow();
        }).catch(function (err) {
          retry.disabled = false;
          showMessage("重试失败：" + ((err && err.message) || String(err)), "bad");
        });
      });
      actions.appendChild(retry);
    }
    if (actions.childNodes.length) card.appendChild(actions);
    card.appendChild(technical(item));
    return card;
  }

  function renderAppStatus(app) {
    var box = document.getElementById("mj-task-app-note");
    if (!box) return;
    app = app || {};
    var state = app.shutdown_state || "open";
    if (state === "open") { box.hidden = true; return; }
    box.hidden = false;
    box.className = "mj-task-app-note" + (state === "stuck" ? " bad" : " warn");
    if (state === "stuck") {
      box.textContent = app.message || "退出受阻：当前任务仍未结束。写操作已停止接收，状态和日志仍可查看。";
    } else if (app.quit_mode === "cancel_running") {
      box.textContent = "正在请求安全停止并退出。已排队但未运行的任务不会执行。";
    } else {
      box.textContent = "完成当前任务后退出。已排队但未运行的任务不会执行，也不会产生新的费用。";
    }
  }

  function render() {
    ensureDrawer();
    renderSettings();
    var data = snapshot || { summary: {}, items: [], app: {} };
    var summary = data.summary || {};
    var summaryNode = document.getElementById("mj-task-summary");
    if (summaryNode) {
      var bits = [];
      if (summary.attention) bits.push(summary.attention + " 项需要处理");
      if (summary.in_progress) bits.push(summary.in_progress + " 项进行中");
      if (!bits.length) bits.push("当前没有需要处理的任务");
      summaryNode.textContent = bits.join(" · ");
    }
    renderAppStatus(data.app || {});
    var body = document.getElementById("mj-task-body");
    clear(body);
    var items = Array.isArray(data.items) ? data.items : [];
    var groups = [
      ["attention", "需要你处理", 0],
      ["active", "正在进行", 0],
      ["queued", "接下来", 8],
      ["recent", "最近结束", 5]
    ];
    groups.forEach(function (group) {
      var allRows = items.filter(function (item) { return item.group === group[0]; });
      if (!allRows.length) return;
      var limit = Number(group[2] || 0);
      var rows = limit > 0 ? allRows.slice(0, limit) : allRows;
      var rest = limit > 0 ? allRows.slice(limit) : [];
      var section = node("section", "mj-task-group");
      var heading = node("div", "mj-task-group-title");
      heading.appendChild(node("span", null, group[1]));
      heading.appendChild(node("span", null, String(allRows.length)));
      section.appendChild(heading);
      rows.forEach(function (item) { section.appendChild(renderItem(item)); });
      if (rest.length) {
        var more = node("details", "mj-task-more");
        more.appendChild(node("summary", null, "显示其余 " + rest.length + " 项"));
        rest.forEach(function (item) { more.appendChild(renderItem(item)); });
        section.appendChild(more);
      }
      body.appendChild(section);
    });
    if (!body.childNodes.length) {
      body.appendChild(node("div", "mj-task-empty", "当前没有任务。你可以继续创作。"));
    }
  }

  function updateButton() {
    var summary = (snapshot && snapshot.summary) || {};
    var attention = Number(summary.attention || 0);
    var progress = Number(summary.in_progress || 0);
    var unseen = Number(sessionGet(storageStem + ":unseen", "0")) || 0;
    var liveCount = attention + progress;
    var count = liveCount || unseen;
    button.classList.toggle("attention", attention > 0);
    button.classList.toggle("active", attention === 0 && progress > 0);
    button.classList.toggle("complete", attention === 0 && progress === 0 && unseen > 0);
    if (badge) {
      badge.hidden = count <= 0;
      badge.textContent = count > 99 ? "99+" : String(count || "");
    }
    var titleBits = [];
    if (attention > 0) titleBits.push(attention + " 项需要处理");
    if (progress > 0) titleBits.push(progress + " 项进行中");
    if (!titleBits.length && unseen > 0) titleBits.push(unseen + " 条新任务消息");
    button.title = titleBits.length ? titleBits.join(" · ") : "打开任务中心";
    button.setAttribute("aria-label", button.title);

    var app = (snapshot && snapshot.app) || {};
    if (quitButton) {
      if (app.shutdown_state === "stuck") {
        quitButton.textContent = "退出受阻";
        quitButton.disabled = false;
      } else if (app.shutdown_state === "closing") {
        quitButton.textContent = "正在退出";
        quitButton.disabled = true;
      } else {
        quitButton.textContent = "退出";
        quitButton.disabled = false;
      }
    }
  }

  function notifyTransitions(next) {
    var previous = parseMap(sessionGet(storageStem + ":states", "{}"));
    var notified = parseMap(sessionGet(storageStem + ":notified", "{}"));
    var states = {};
    var unseen = Number(sessionGet(storageStem + ":unseen", "0")) || 0;
    var rows = Array.isArray(next.items) ? next.items : [];
    rows.forEach(function (item) {
      states[item.id] = item.state_key;
      var before = previous[item.id];
      var terminal = item.group === "attention" || item.group === "recent";
      var wasActive = before === "queued" || before === "running" || before === "canceling";
      var key = item.notification && item.notification.key;
      if (!firstFetch && terminal && wasActive && key && !notified[key]) {
        notified[key] = true;
        if (!drawer || drawer.hidden) unseen += 1;
        playSound();
        if (document.hidden) {
          try {
            if (notificationEnabled() && window.Notification && Notification.permission === "granted") {
              new Notification(item.notification.title, { body: item.notification.body, tag: key });
            }
          } catch (err) {}
        }
      }
    });
    var liveKeys = {};
    rows.forEach(function (item) {
      if (item.notification && item.notification.key) liveKeys[item.notification.key] = true;
    });
    Object.keys(notified).forEach(function (key) {
      if (!liveKeys[key]) delete notified[key];
    });
    sessionSet(storageStem + ":states", JSON.stringify(states));
    sessionSet(storageStem + ":notified", JSON.stringify(notified));
    setUnseen(unseen);
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    var delay = (snapshot && snapshot.poll_after_ms) || 10000;
    if (document.hidden) delay = ((snapshot && snapshot.summary && snapshot.summary.in_progress) ? 12000 : 30000);
    if (drawer && !drawer.hidden) delay = Math.min(delay, 2000);
    timer = setTimeout(refreshNow, delay);
  }

  function refreshNow() {
    if (timer) clearTimeout(timer);
    if (refreshPromise) return refreshPromise;
    var request = api("GET", "/api/task-center").then(function (data) {
      notifyTransitions(data || {});
      snapshot = data || {};
      firstFetch = false;
      updateButton();
      if (drawer && !drawer.hidden) render();
      schedule();
      return snapshot;
    }).catch(function () {
      if (drawer && !drawer.hidden) showMessage("任务状态暂时无法读取；不影响项目文件。", "warn");
      schedule();
      return null;
    });
    refreshPromise = request.then(function (value) {
      refreshPromise = null;
      return value;
    }, function (error) {
      refreshPromise = null;
      throw error;
    });
    return refreshPromise;
  }

  function requestQuit(mode) {
    return api("POST", "/api/app/quit", { mode: mode || "after_current" }).then(function () {
      showMessage("正在安全退出…", "");
      openDrawer();
      refreshNow();
    }).catch(function (err) {
      showMessage("退出失败：" + ((err && err.message) || String(err)), "bad");
      openDrawer();
    });
  }

  function promptQuit() {
    api("GET", "/api/app/status").then(function (status) {
      if (status && (status.running_job || Number(status.queued_count || 0) > 0)) {
        if (typeof showQuitDialog === "function") {
          showQuitDialog(status, requestQuit);
          return;
        }
      }
      requestQuit("after_current");
    }).catch(function (err) {
      showMessage("无法读取退出状态：" + ((err && err.message) || String(err)), "bad");
      openDrawer();
    });
  }

  button.addEventListener("click", function () {
    if (drawer && !drawer.hidden) closeDrawer(); else openDrawer();
  });
  if (quitButton) quitButton.addEventListener("click", promptQuit);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      if (confirmOverlay) { closeConfirm(); return; }
      if (drawer && !drawer.hidden) closeDrawer();
      return;
    }
    if (event.key !== "Tab") return;
    var root = confirmOverlay ? confirmOverlay.querySelector(".mj-task-confirm")
      : (drawer && !drawer.hidden ? drawer : null);
    if (!root) return;
    var items = focusable(root);
    if (!items.length) return;
    var first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) { document.title = baseTitle; refreshNow(); }
    else { setUnseen(Number(sessionGet(storageStem + ":unseen", "0")) || 0); schedule(); }
  });
  window.addEventListener("manju:jobs-changed", function () { refreshNow(); });
  window.addEventListener("pagehide", function () { if (timer) clearTimeout(timer); });

  window.ManjuTaskCenter = {
    open: openDrawer,
    close: closeDrawer,
    refresh: refreshNow,
    current: function () { return snapshot; }
  };
  updateButton();
  refreshNow();
})();
"""


def render_task_center_css() -> str:
    return TASK_CENTER_CSS


def render_task_center_js() -> str:
    return TASK_CENTER_JS
