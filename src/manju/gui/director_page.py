"""导演助手 /director — the six-step AI-director loop as a workbench page.

The GUI twin of ``manju director …`` (goal item 17): the same
:mod:`manju.build.director` core the CLI and CLI/GUI drive, rendered as one page so
a human can run the whole contract without a terminal:

    propose → (impact + est cost, 试跑 numbers — the §9 step-2 emphasis) →
    确认 → 执行 → diff summary + failures + 下一步建议 (one-click 转为新提案).

Built to the same stance as the round-T pages (:mod:`manju.gui.pages_t`):

  * server-rendered — a plain GET bakes the real proposals + suggestions into the
    DOM; ``/director.js`` layers on the confirm/execute round-trips and refresh;
  * CSP-safe — CSS/JS in external files, no inline handlers or ``style=``, every
    mutating request carries the ``X-Manju-Token`` from the ``manju-token`` meta;
  * XSS-safe — all server text is ``html.escape``-d here and the script only ever
    writes ``textContent`` / element properties, never ``innerHTML``;
  * one core — every button calls the SAME director functions the CLI does;
    **确认 and 执行 are separate clicks, never one** (§8.3 approve-before-execute).
"""

from __future__ import annotations

import difflib
import html
from typing import Any

__all__ = [
    "PAGE_PATHS_DIRECTOR",
    "render",
    "render_director_css",
    "render_director_js",
    "proposals_payload",
]

PAGE_PATHS_DIRECTOR = frozenset({"/director"})

# state → (chinese chip label, css class)
_STATE_CHIP = {
    "proposed": ("已提案", "d-proposed"),
    "confirmed": ("已确认", "d-confirmed"),
    "executing": ("执行中", "d-executing"),
    "done": ("已完成", "d-done"),
    "failed": ("失败", "d-failed"),
    "rejected": ("已否决", "d-rejected"),
    "expired": ("待更新", "d-expired"),
}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _shell(title: str, token: str, active: str, body: str, project: Any) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    nav, bcls = chrome(active, project)

    return (
        "<!doctype html>\n"
        '<html lang="zh">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/director.css">\n'
        + GLOSSARY_HEAD
        + '<script src="/common.js" defer></script>\n'
        + '<script src="/director.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{_e(active)}" class="{bcls}">\n'
        + nav
        + "\n<main>\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


# ------------------------------------------------------------------ payload


def proposals_payload(project: Any) -> dict[str, Any]:
    """The JSON the page (and its refresh) render from: every proposal with a
    live ``current`` flag, plus the standalone next-step suggestions."""
    from ..build.director import _is_current, list_proposals, suggest_next

    proposals = []
    for pr in list_proposals(project):
        current = _is_current(project, pr)
        data = pr.model_dump()
        data["current"] = current
        proposals.append(data)
    suggestions = [s.to_dict() for s in suggest_next(project)]
    return {"proposals": proposals, "suggestions": suggestions}


def render(path: str, project: Any, token: str, query: dict[str, list[str]]) -> str:
    if path == "/director":
        return render_director(project, token)
    raise KeyError(path)


# ------------------------------------------------------------------ render


_ACTION_LABELS = {
    "build": "生成候选或成片",
    "redo": "重做镜头",
    "voice": "生成配音",
    "repair": "修复素材",
    "mixer": "调整混音",
    "captions": "编辑字幕",
    "packaging": "调整包装",
    "snapshot": "建立存档点",
    "rollback": "还原内容",
    "truth_patch_set": "更新创作真相",
}


def _action_brief(a: dict[str, Any]) -> str:
    t = a.get("type")
    if t == "build":
        return f"{a.get('target') or 'final'} · {a.get('gen') or 'missing'}"
    if t in ("redo", "voice"):
        return str(a.get("shot") or "") + (f" · {a['provider']}" if a.get("provider") else "")
    if t == "repair":
        return f"{a.get('shot') or ''} · {a.get('op') or ''}".strip(" ·")
    if t in ("captions", "packaging"):
        return str(a.get("op") or "")
    if t == "rollback":
        return f"{a.get('shot') or a.get('path') or ''} · {a.get('op') or ''}".strip(" ·")
    if t == "snapshot":
        return str(a.get("label") or "当前项目")
    if t == "mixer":
        return "、".join((a.get("changes") or {}).keys())
    if t == "truth_patch_set":
        return f"{len(a.get('patches') or [])} 个文件"
    return ""


def _proposal_kind(pr: dict[str, Any]) -> tuple[str, str, str]:
    actions = [entry.get("action") or {} for entry in (pr.get("actions") or [])]
    types = {str(action.get("type") or "") for action in actions}
    if "truth_patch_set" in types:
        return "authoring", "创作变更提案", "会修改故事、镜头、Bible 或系列真相；只允许人类确认和执行。"
    if types.intersection({"build", "redo", "voice"}) or float(pr.get("estimated_cost") or 0) > 0:
        return "priced", "制作提案", "可能调用 Provider；执行前仍受当前模式、预算和费用确认保护。"
    return "local", "本地操作提案", "只运行本地或文本操作，不会调用云端 Provider。"


def _is_priced(pr: dict[str, Any]) -> bool:
    kind, _label, _detail = _proposal_kind(pr)
    return kind == "priced"


def _impact_summary(impact: dict[str, Any]) -> str:
    shots = [str(item) for item in (impact.get("shots") or []) if item]
    outputs = [str(item) for item in (impact.get("outputs") or []) if item]
    parts: list[str] = []
    if shots:
        parts.append("镜头 " + "、".join(shots))
    if outputs:
        parts.append("影响 " + "、".join(outputs))
    if impact.get("note"):
        parts.append(str(impact["note"]))
    return " · ".join(parts) or "不会改变媒体或项目真相"


def _action_card(index: int, pa: dict[str, Any]) -> str:
    action = pa.get("action") or {}
    atype = str(action.get("type") or "")
    label = _ACTION_LABELS.get(atype, atype or "未知操作")
    brief = _action_brief(action)
    impact = _impact_summary(pa.get("impact") or {})
    cost = float(pa.get("estimated_cost") or 0)
    currency = str(pa.get("currency") or "")
    cost_text = f"最高预估 {cost:g} {currency}" if cost else "本地操作 · 0 费用"
    result = pa.get("result")
    result_html = ""
    if result is not None:
        ok = bool(result.get("ok"))
        result_html = (
            f'<span class="dg-action-result {"ok" if ok else "bad"}">'
            f'{"已完成" if ok else _e(result.get("error") or "失败")}</span>'
        )
    brief_html = f'<span>{_e(brief)}</span>' if brief else ""
    return (
        '<div class="dg-action">'
        f'<span class="dg-action-index">{index + 1}</span>'
        '<div class="dg-action-copy">'
        f'<strong>{_e(label)}</strong>'
        f'{brief_html}'
        f'<small>{_e(impact)}</small>'
        '</div>'
        f'<div class="dg-action-meta"><span>{_e(cost_text)}</span>{result_html}</div>'
        '</div>'
    )


def _read_text(project: Any, relpath: str) -> tuple[str, str | None]:
    path = project.root / relpath
    if not path.exists():
        return "", None
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return "", "当前文件不是 UTF-8，无法在这里安全预览差异。"
    except OSError as exc:
        return "", "无法读取当前文件：" + " ".join(str(exc).split())


def _unified_diff(before: str, after: str, relpath: str) -> tuple[list[str], bool]:
    """Return a bounded technical diff without materialising an unbounded file."""
    stream = difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"当前/{relpath}", tofile=f"提案/{relpath}",
        lineterm="", n=3,
    )
    lines: list[str] = []
    truncated = False
    for index, line in enumerate(stream):
        if index >= 240:
            truncated = True
            break
        lines.append(line)
    return lines, truncated


def _diff_line_html(line: str) -> str:
    # One prose paragraph can be tens of thousands of characters.  The complete
    # proposal remains on disk; the browser preview must stay bounded.
    if len(line) > 600:
        line = line[:420] + " …（本行已截断）… " + line[-140:]
    if line.startswith("@@"):
        cls = "hunk"
    elif line.startswith("+++") or line.startswith("---"):
        cls = "meta"
    elif line.startswith("+"):
        cls = "add"
    elif line.startswith("-"):
        cls = "del"
    else:
        cls = "context"
    return f'<span class="dg-diff-line dg-diff-{cls}">{_e(line)}</span>'


def _technical_diff(before: str, after: str, relpath: str) -> str:
    lines, truncated = _unified_diff(before, after, relpath)
    if not lines:
        return '<p class="muted">没有文本变化。</p>'
    tail = (
        '<span class="dg-diff-line dg-diff-meta">… 差异预览已截断；原始提案仍在技术详情中完整保留。</span>'
        if truncated else ""
    )
    return '<pre class="dg-patch-diff">' + "".join(_diff_line_html(line) for line in lines) + tail + '</pre>'


def _inline_comparison(before: str, after: str) -> str:
    """Human-readable before/after with exact changed spans highlighted."""
    if before == after:
        return '<p class="muted">提案内容与当前文件相同。</p>'
    if max(len(before), len(after)) > 20_000:
        return (
            '<p class="dg-long-copy">内容较长，主界面不展开全文。'
            '请查看下方统一差异和技术详情后再确认。</p>'
        )

    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    before_parts: list[str] = []
    after_parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old = _e(before[i1:i2])
        new = _e(after[j1:j2])
        if tag == "equal":
            before_parts.append(old)
            after_parts.append(new)
        elif tag == "delete":
            before_parts.append(f'<mark class="dg-inline-del">{old}</mark>')
        elif tag == "insert":
            after_parts.append(f'<mark class="dg-inline-add">{new}</mark>')
        else:  # replace
            before_parts.append(f'<mark class="dg-inline-del">{old}</mark>')
            after_parts.append(f'<mark class="dg-inline-add">{new}</mark>')

    old_html = "".join(before_parts) or '<em class="muted">文件尚不存在</em>'
    new_html = "".join(after_parts) or '<em class="muted">提案将清空这个文件</em>'
    return (
        '<div class="dg-compare-grid">'
        '<section><h4>当前内容</h4>'
        f'<div class="dg-inline-copy">{old_html}</div></section>'
        '<section><h4>提案内容</h4>'
        f'<div class="dg-inline-copy">{new_html}</div></section>'
        '</div>'
    )


def _truth_patch_review(project: Any, action: dict[str, Any]) -> str:
    patches = list(action.get("patches") or [])
    if not patches:
        return '<p class="err">这份创作提案没有可审阅文件。</p>'
    cards: list[str] = []
    for index, patch in enumerate(patches):
        relpath = str(patch.get("path") or "")
        before, error = _read_text(project, relpath)
        after = str(patch.get("content") or "")
        if error:
            body = f'<p class="err">{_e(error)}</p>'
            state = "无法比较"
        else:
            state = "新建" if not before and after else ("无变化" if before == after else "修改")
            body = (
                _inline_comparison(before, after)
                + '<details class="dg-unified"><summary>查看统一差异</summary>'
                + _technical_diff(before, after, relpath)
                + '</details>'
            )
        cards.append(
            f'<details class="dg-patch"{" open" if index == 0 else ""}>'
            f'<summary><span>{_e(relpath)}</span><small>{_e(state)} · 查看修改前后</small></summary>'
            f'{body}</details>'
        )
    return (
        '<div class="dg-patch-review">'
        f'<p>这份提案会修改 <b>{len(patches)}</b> 个文件。下面直接展示当前内容和提案内容；'
        '确认只冻结这份提案，执行后才会原子写入。</p>'
        + "".join(cards)
        + '</div>'
    )


def _outcome_view(outcome: dict[str, Any]) -> str:
    diff = outcome.get("diff") or {}
    outputs = diff.get("outputs") or {}
    parts = ['<div class="dg-outcome"><h4>执行结果</h4>']
    new = ", ".join(outputs.get("new_finals") or [])
    rerendered = ", ".join(outputs.get("rerendered_finals") or [])
    if new or rerendered:
        parts.append(
            '<p>'
            + (f'新增成片：{_e(new)}。' if new else "")
            + (f'重新渲染：{_e(rerendered)}。' if rerendered else "")
            + '</p>'
        )
    spec = diff.get("spec") or {}
    if spec.get("available") and spec.get("text"):
        parts.append(
            '<details class="dg-specdiff"><summary>查看项目文本差异</summary>'
            f'<pre>{_e(spec.get("text"))}</pre></details>'
        )
    failure = outcome.get("failure")
    if failure:
        parts.append(
            '<div class="dg-failure"><b>执行在第一处失败后已停止</b>'
            f'<p>{_e(failure.get("cause"))}</p>'
            '<small>项目保留了失败证据；请在技术详情中查看 failure id。</small></div>'
        )
    if len(parts) == 1:
        parts.append('<p class="muted">操作已经结束；没有新的媒体差异需要展示。</p>')
    return "".join(parts) + "</div>"


def _technical_details(pr: dict[str, Any]) -> str:
    cost = float(pr.get("estimated_cost") or 0)
    currency = str(pr.get("currency") or "")
    rows = [
        ("提案 ID", pr.get("id") or "—"),
        ("发起者", pr.get("actor") or "—"),
        ("创建时间", pr.get("created_at") or "—"),
        ("费用预估", f"{cost:g} {currency}" if cost else "0 · 本地"),
    ]
    body = "".join(f'<dt>{_e(k)}</dt><dd>{_e(v)}</dd>' for k, v in rows)
    action_types = "、".join(
        str((entry.get("action") or {}).get("type") or "")
        for entry in (pr.get("actions") or [])
    ) or "—"
    body += f'<dt>原始动作</dt><dd><code>{_e(action_types)}</code></dd>'
    if pr.get("basis_digest"):
        body += f'<dt>创作基线</dt><dd><code>{_e(pr.get("basis_digest"))}</code></dd>'
    return (
        '<details class="dg-technical"><summary>技术详情</summary>'
        f'<dl>{body}</dl></details>'
    )


def _proposal_card(project: Any, pr: dict[str, Any], policy_status: str, *, compact: bool = False) -> str:
    pid = str(pr.get("id") or "")
    state = str(pr.get("state") or "proposed")
    label, cls = _STATE_CHIP.get(state, (state, "d-proposed"))
    current = pr.get("current")
    stale = state == "expired" or (current is False and state in ("proposed", "confirmed"))
    kind, kind_label, kind_detail = _proposal_kind(pr)
    title = str(pr.get("why") or kind_label)
    actions = list(pr.get("actions") or [])

    head = (
        '<div class="dg-head">'
        '<div class="dg-title">'
        f'<div><span class="dg-kind-badge dg-kind-{_e(kind)}">{_e(kind_label)}</span>'
        f'<span class="dg-chip {cls}">{_e(label)}</span></div>'
        f'<h3>{_e(title)}</h3><p>{_e(kind_detail)}</p>'
        '</div></div>'
    )
    stale_html = ""
    if stale:
        stale_html = (
            '<div class="dg-stale"><b>项目内容已经变化，这份提案不能再确认或执行。</b>'
            '<span>请按当前项目重新起草；旧提案会继续保留为历史证据。</span></div>'
        )

    if kind == "authoring" and actions:
        main = _truth_patch_review(project, actions[0].get("action") or {})
    else:
        main = '<div class="dg-action-list">' + "".join(
            _action_card(index, entry) for index, entry in enumerate(actions)
        ) + '</div>'

    buttons: list[str] = ['<div class="dg-btns">']
    if state == "proposed" and not stale:
        buttons.append(
            f'<button class="btn" data-act="confirm" data-id="{_e(pid)}">确认这份提案</button>'
        )
        buttons.append(
            '<span class="dg-boundary">确认只冻结这份内容；还需要下一次明确执行才会改变项目。</span>'
        )
    elif state == "confirmed" and not stale:
        if _is_priced(pr) and policy_status in {"strict", "invalid"}:
            buttons.append('<button class="btn" disabled>执行已确认提案</button>')
            buttons.append(
                '<span class="dg-boundary warn">当前是本地安全模式，不会执行可能调用 Provider 的操作。'
                '提案仍会保留，未来切换到受控云端后再重新检查费用。</span>'
            )
        else:
            buttons.append(
                f'<button class="btn" data-act="run" data-id="{_e(pid)}">执行已确认提案</button>'
            )
            buttons.append(
                '<span class="dg-boundary">确认只冻结了这份内容，还没有写入项目或运行操作。</span>'
            )
    if state in ("proposed", "confirmed", "expired"):
        buttons.append(
            f'<button class="btn ghost" data-act="reject" data-id="{_e(pid)}">否决并保留记录</button>'
        )
    buttons.append('</div>')

    outcome = _outcome_view(pr.get("outcome") or {}) if pr.get("outcome") else ""
    compact_cls = " dg-card-compact" if compact else ""
    return (
        f'<article class="dg-card dg-kind-{_e(kind)}{compact_cls}" '
        f'data-id="{_e(pid)}" data-state="{_e(state)}">'
        + head + stale_html + main + "".join(buttons) + outcome + _technical_details(pr)
        + '</article>'
    )


def _suggestion_card(s: dict[str, Any], *, primary: bool = False) -> str:
    import json as _json

    action = s.get("action")
    button = ""
    if action:
        payload = _e(_json.dumps({"actions": [action], "why": s.get("text", "")}, ensure_ascii=False))
        button = (
            f'<button class="btn{" ghost" if not primary else ""}" data-act="suggest-propose" '
            f'data-payload="{payload}">生成一份可审阅提案</button>'
        )
    else:
        button = '<span class="muted">这一步需要你或外部 IDE 助手直接处理。</span>'
    return (
        f'<div class="dg-sugg{" dg-sugg-primary" if primary else ""}">'
        f'<span class="dg-kind">{_e(s.get("kind"))}</span>'
        f'<div><strong>{_e(s.get("text"))}</strong>{button}</div></div>'
    )


def _suggestions_panel(suggestions: list[dict[str, Any]]) -> str:
    if not suggestions:
        return (
            '<section class="dg-suggpanel panel"><h2>现在没有需要自动转换的建议</h2>'
            '<p class="muted">你可以继续直接编辑创作真相，或前往分镜工作台。</p>'
            '<a class="btn ghost" href="/storyboard">去分镜工作台</a></section>'
        )
    first, rest = suggestions[0], suggestions[1:]
    extra = ""
    if rest:
        extra = (
            f'<details class="dg-more-suggestions"><summary>其它 {len(rest)} 条建议</summary>'
            + "".join(_suggestion_card(item) for item in rest)
            + '</details>'
        )
    return (
        '<section class="dg-suggpanel panel"><h2>现在最值得处理</h2>'
        '<p class="muted">建议只会生成一份提案，不会自动确认、执行或产生费用。</p>'
        + _suggestion_card(first, primary=True) + extra + '</section>'
    )


def _composer() -> str:
    return (
        '<details class="dg-composer panel"><summary>高级：使用 JSON 创建操作提案</summary>'
        '<p class="muted mini">这是给熟悉 Director action 的用户和外部 IDE 助手使用的技术入口。'
        '普通创作变更应由外部助手生成 truth_patch_set，再回到本页审阅差异。</p>'
        '<textarea id="dg-actions" rows="4" '
        'placeholder=\'[{"type":"snapshot","label":"checkpoint"}]\'></textarea>'
        '<input id="dg-why" type="text" placeholder="为什么要做这件事（可选）">'
        '<button class="btn" id="dg-propose">创建提案</button></details>'
    )


def render_director(project: Any, token: str) -> str:
    head = (
        '<div class="page-h"><h1>导演助手<span class="mj-en" aria-hidden="true"> (Director)</span></h1>'
        '<span class="muted">所有外部建议先变成可审阅提案。确认只冻结内容，执行才会写入项目或运行操作。</span></div>'
    )

    try:
        payload = proposals_payload(project)
        from ..providers.zero_cost import execution_policy_snapshot
        policy_status = str(execution_policy_snapshot().get("status") or "standard")
    except Exception as exc:
        return _shell(
            "导演助手", token, "/director", head + f'<p class="err panel">{_e(exc)}</p>', project,
        )

    proposals = list(payload.get("proposals") or [])
    active = [row for row in proposals if row.get("state") in {"proposed", "confirmed", "executing", "expired"}]
    history = [row for row in proposals if row not in active]

    from .authoring_journey import render_authoring_journey

    boundary = (
        '<section class="dg-boundary-card panel"><div><strong>提案不会替你做决定</strong>'
        '<p>确认只是把提案内容固定下来；执行才会真正运行。创作变更必须由人类确认和执行，'
        '任何项目变化都会使旧提案失效。</p></div>'
        '<a class="btn ghost" href="/create">返回创作真相</a></section>'
    )

    if active:
        active_html = (
            '<section class="dg-active"><div class="dg-section-head"><div><h2>待我决定</h2>'
            f'<p>{len(active)} 份提案正在等待确认、执行或重新起草。</p></div></div>'
            + "".join(_proposal_card(project, row, policy_status) for row in active)
            + '</section>'
        )
    else:
        active_html = (
            '<section class="dg-active"><div class="dg-section-head"><div><h2>待我决定</h2>'
            '<p>当前没有等待人工处理的提案。</p></div></div>'
            '<div class="panel dg-empty"><p>你可以直接继续写作和建立分镜；提案是可选路径。</p>'
            '<div><a class="btn" href="/create">继续创作</a>'
            '<a class="btn ghost" href="/storyboard">建立分镜</a></div></div></section>'
        )

    history_html = ""
    if history:
        history_html = (
            '<details class="dg-history panel"><summary>历史提案 '
            f'<span>{len(history)}</span></summary>'
            '<p class="muted">已完成、失败或已否决的提案只作为审计记录保留。</p>'
            + "".join(_proposal_card(project, row, policy_status, compact=True) for row in history)
            + '</details>'
        )

    suggestions = list(payload.get("suggestions") or [])
    suggestions_html = _suggestions_panel(suggestions) if suggestions or not active else ""
    body = (
        head
        + render_authoring_journey(project, "/director")
        + boundary
        + active_html
        + suggestions_html
        + history_html
        + _composer()
    )
    return _shell("导演助手", token, "/director", body, project)

# ============================================================ assets (css/js)


def render_director_css() -> str:
    from .authoring_journey import AUTHORING_JOURNEY_CSS

    return AUTHORING_JOURNEY_CSS + _DIRECTOR_CSS


def render_director_js() -> str:
    return _DIRECTOR_JS


_DIRECTOR_CSS = """
.dg-boundary-card { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:16px; border-left:3px solid var(--accent); }
.dg-boundary-card strong { font-size:.95rem; }
.dg-boundary-card p { margin:.25rem 0 0; color:var(--muted); font-size:.78rem; line-height:1.45; }
.dg-suggpanel, .dg-composer, .dg-history { margin-bottom:16px; }
.dg-suggpanel h2, .dg-section-head h2 { margin:0; font-size:1rem; }
.dg-suggpanel > p, .dg-section-head p { margin:.25rem 0 .7rem; color:var(--muted); font-size:.78rem; }
.dg-sugg { display:grid; grid-template-columns:auto minmax(0,1fr); gap:10px; padding:8px 0; border-top:1px solid var(--line); }
.dg-sugg:first-of-type { border-top:0; }
.dg-sugg > div { min-width:0; display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; }
.dg-sugg strong { font-size:.82rem; line-height:1.4; }
.dg-sugg-primary { padding:12px; border:1px solid var(--line); border-radius:10px; background:var(--panel2); }
.dg-kind { align-self:start; padding:2px 7px; border-radius:999px; color:var(--accent); background:var(--panel2); font:700 .66rem var(--mono); text-transform:uppercase; }
.dg-more-suggestions { margin-top:8px; }
.dg-more-suggestions > summary { cursor:pointer; color:var(--muted); font-size:.76rem; font-weight:700; }
.dg-section-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin:18px 0 8px; }
.dg-card { background:#12161f; border:1px solid #232a36; border-radius:12px; padding:14px; margin-bottom:12px; }
.dg-card.dg-kind-authoring { border-left:3px solid var(--accent); }
.dg-card.dg-kind-priced { border-left:3px solid #e0af68; }
.dg-card.dg-kind-local { border-left:3px solid #8b93a3; }
.dg-card-compact { background:var(--panel2); }
.dg-head { display:flex; align-items:flex-start; gap:12px; }
.dg-title { min-width:0; flex:1; }
.dg-title > div { display:flex; align-items:center; gap:7px; flex-wrap:wrap; }
.dg-title h3 { margin:.45rem 0 .15rem; font-size:1rem; line-height:1.35; }
.dg-title p { margin:0; color:var(--muted); font-size:.76rem; line-height:1.4; }
.dg-kind-badge, .dg-chip { font-size:.68rem; padding:2px 7px; border-radius:999px; font-weight:700; }
.dg-kind-badge { color:var(--fg); background:var(--panel2); border:1px solid var(--line); }
.dg-kind-authoring .dg-kind-badge { color:var(--accent); }
.dg-kind-priced .dg-kind-badge { color:#e0af68; }
.d-proposed { background:#1c2a44; color:var(--accent); }
.d-confirmed { background:#183a2a; color:#7ee787; }
.d-executing { background:#3a3418; color:#e0af68; }
.d-done { background:#173a1f; color:#7ee787; }
.d-failed { background:#3a1c1c; color:#ff7b72; }
.d-rejected { background:#26262c; color:#8b93a3; }
.d-expired { background:#3a2c18; color:#e0af68; }
.dg-stale { display:flex; flex-direction:column; gap:3px; margin:12px 0; padding:10px 12px; border:1px solid #5a3d19; border-radius:9px; background:#261d12; color:#f2c26b; }
.dg-stale span { font-size:.75rem; color:#d0b07c; }
.dg-action-list { display:flex; flex-direction:column; gap:7px; margin-top:12px; }
.dg-action { display:grid; grid-template-columns:26px minmax(0,1fr) auto; gap:9px; align-items:start; padding:10px; border:1px solid var(--line); border-radius:9px; background:var(--panel2); }
.dg-action-index { width:24px; height:24px; display:grid; place-items:center; border-radius:999px; background:var(--panel); color:var(--accent); font:700 .7rem var(--mono); }
.dg-action-copy { min-width:0; display:flex; flex-direction:column; gap:2px; }
.dg-action-copy strong { font-size:.82rem; }
.dg-action-copy span { font-size:.75rem; }
.dg-action-copy small { color:var(--muted); line-height:1.35; }
.dg-action-meta { display:flex; flex-direction:column; align-items:flex-end; gap:4px; color:var(--muted); font-size:.72rem; text-align:right; }
.dg-action-result.ok { color:var(--ok); }
.dg-action-result.bad { color:#ff7b72; }
.dg-patch-review { margin-top:12px; }
.dg-patch-review > p { margin:.2rem 0 .65rem; color:var(--muted); font-size:.78rem; line-height:1.45; }
.dg-patch { border:1px solid var(--line); border-radius:9px; overflow:hidden; margin-top:7px; background:var(--panel2); }
.dg-patch > summary { cursor:pointer; display:flex; justify-content:space-between; gap:10px; padding:9px 11px; font-weight:700; font-size:.8rem; }
.dg-patch > summary small { color:var(--muted); font-weight:400; }
.dg-compare-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; padding:9px; }
.dg-compare-grid > section { min-width:0; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:#0f131b; }
.dg-compare-grid h4 { margin:0; padding:7px 9px; border-bottom:1px solid var(--line); color:var(--muted); font-size:.72rem; }
.dg-inline-copy { min-height:72px; max-height:310px; overflow:auto; padding:9px; color:#d6dbe4; font:12px/1.55 var(--mono); white-space:pre-wrap; overflow-wrap:anywhere; }
.dg-inline-copy mark { border-radius:3px; padding:0 1px; color:inherit; }
.dg-inline-del { background:rgba(248,81,73,.24); text-decoration:line-through; text-decoration-color:rgba(255,138,144,.7); }
.dg-inline-add { background:rgba(63,185,80,.25); }
.dg-long-copy { margin:9px; padding:10px; border-radius:7px; background:var(--panel); color:var(--muted); font-size:.76rem; }
.dg-unified { margin:0 9px 9px; }
.dg-unified > summary { cursor:pointer; color:var(--muted); font-size:.72rem; }
.dg-patch-diff, .dg-specdiff pre { margin:7px 0 0; padding:11px; overflow:auto; max-height:360px; background:#0b0e14; color:#d6dbe4; font:12px/1.5 var(--mono); white-space:pre-wrap; overflow-wrap:anywhere; }
.dg-diff-line { display:block; min-height:1.5em; }
.dg-diff-meta { color:#8b93a3; }
.dg-diff-hunk { color:#74a9ff; }
.dg-diff-add { color:#9be9a8; background:rgba(63,185,80,.12); }
.dg-diff-del { color:#ffb4ab; background:rgba(248,81,73,.12); }
.dg-diff-context { color:#d6dbe4; }
.dg-btns { display:flex; gap:9px; align-items:center; flex-wrap:wrap; margin-top:12px; padding-top:12px; border-top:1px solid var(--line); }
.dg-boundary { flex:1 1 260px; color:var(--muted); font-size:.74rem; line-height:1.4; }
.dg-boundary.warn { color:#e0af68; }
.dg-outcome { margin-top:12px; padding:11px; border-radius:9px; background:var(--panel2); }
.dg-outcome h4 { margin:0 0 .35rem; font-size:.88rem; }
.dg-outcome p { margin:.25rem 0; font-size:.78rem; }
.dg-specdiff > summary { cursor:pointer; color:var(--muted); font-size:.76rem; }
.dg-failure { background:#2a1616; border:1px solid #3a1c1c; border-radius:7px; padding:9px; margin-top:8px; color:#ffb4ab; }
.dg-failure p { margin:.35rem 0; }
.dg-technical { margin-top:10px; }
.dg-technical > summary, .dg-history > summary, .dg-composer > summary { cursor:pointer; color:var(--muted); font-size:.76rem; font-weight:700; }
.dg-technical dl { display:grid; grid-template-columns:auto minmax(0,1fr); gap:5px 10px; margin:.7rem 0 0; font-size:.72rem; }
.dg-technical dt { color:var(--muted); }
.dg-technical dd { margin:0; min-width:0; overflow-wrap:anywhere; }
.dg-history > summary { color:var(--fg); font-size:.86rem; }
.dg-history > summary span { color:var(--muted); margin-left:.3rem; }
.dg-history > p { margin:.5rem 0 .8rem; }
.dg-empty p { margin:.1rem 0 .7rem; }
.dg-empty > div { display:flex; gap:8px; flex-wrap:wrap; }
.dg-composer textarea { width:100%; box-sizing:border-box; font-family:var(--mono); margin:8px 0 4px; }
.dg-composer input { width:100%; box-sizing:border-box; margin:4px 0 8px; }
.mini { font-size:12px; }
.btn.mini { padding:2px 8px; font-size:12px; }
@media (max-width:720px) {
  .dg-boundary-card { align-items:flex-start; flex-direction:column; }
  .dg-sugg > div { align-items:flex-start; flex-direction:column; }
  .dg-action { grid-template-columns:24px minmax(0,1fr); }
  .dg-action-meta { grid-column:2; align-items:flex-start; text-align:left; }
  .dg-patch > summary { align-items:flex-start; flex-direction:column; }
  .dg-compare-grid { grid-template-columns:1fr; }
  .dg-btns .btn { width:100%; }
}
"""

_DIRECTOR_JS = r"""
"use strict";
(function () {

  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }

  if (document.body.getAttribute("data-page") !== "/director") return;

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-act]");
    if (btn) {
      var act = btn.getAttribute("data-act");
      if (act === "confirm") return doConfirm(btn.getAttribute("data-id"), btn);
      if (act === "run") return doRun(btn.getAttribute("data-id"), btn);
      if (act === "reject") return doReject(btn.getAttribute("data-id"), btn);
      if (act === "suggest-propose") return doSuggestPropose(btn);
      return;
    }
    if (ev.target.id === "dg-propose") return doCompose();
  });

  function doConfirm(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "确认中…"; }
    post("/api/director/confirm", { id: id }).then(function (res) {
      if (res.status === 200) { toast("已确认提案；还需要单独执行", true); reloadSoon(); }
      else {
        if (btn) { btn.disabled = false; btn.textContent = "确认这份提案"; }
        toast((res.data && res.data.error) || "确认失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = "确认这份提案"; }
      toast("连接本地服务失败", false);
    });
  }
  function doRun(id, btn) {
    if (btn) { btn.disabled = true; btn.textContent = "执行中…"; }
    post("/api/director/run", { id: id }).then(function (res) {
      if (res.status === 200) {
        var ok = res.data && res.data.ok;
        toast((ok ? "执行完成 " : "执行失败(第一处失败即停) ") + id, ok);
        reloadSoon();
      } else {
        if (btn) { btn.disabled = false; btn.textContent = "执行已确认提案"; }
        toast((res.data && res.data.error) || "执行失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = "执行已确认提案"; }
      toast("连接本地服务失败", false);
    });
  }
  function doReject(id, btn) {
    if (!confirm("否决这份提案并保留记录？")) return;
    if (btn) { btn.disabled = true; btn.textContent = "否决中…"; }
    post("/api/director/reject", { id: id }).then(function (res) {
      if (res.status === 200) { toast("已否决并保留记录", true); reloadSoon(); }
      else {
        if (btn) { btn.disabled = false; btn.textContent = "否决并保留记录"; }
        toast((res.data && res.data.error) || "否决失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = "否决并保留记录"; }
      toast("连接本地服务失败", false);
    });
  }
  function doSuggestPropose(btn) {
    var payload;
    try { payload = JSON.parse(btn.getAttribute("data-payload")); }
    catch (e) { toast("建议数据损坏", false); return; }
    btn.disabled = true;
    var original = btn.textContent;
    btn.textContent = "创建中…";
    post("/api/director/propose", payload).then(function (res) {
      if (res.status === 200) { toast("已创建可审阅提案", true); reloadSoon(); }
      else {
        btn.disabled = false; btn.textContent = original;
        toast((res.data && res.data.error) || "提案失败", false);
      }
    }).catch(function () {
      btn.disabled = false; btn.textContent = original;
      toast("连接本地服务失败", false);
    });
  }
  function doCompose() {
    var ta = document.getElementById("dg-actions");
    var why = document.getElementById("dg-why");
    var actions;
    try { actions = JSON.parse(ta.value); }
    catch (e) { toast("actions 不是合法 JSON", false); return; }
    if (!Array.isArray(actions) || !actions.length) { toast("actions 需为非空数组", false); return; }
    var btn = document.getElementById("dg-propose");
    if (btn) { btn.disabled = true; btn.textContent = "创建中…"; }
    post("/api/director/propose", { actions: actions, why: why ? why.value : "" })
      .then(function (res) {
        if (res.status === 200) { toast("已创建提案", true); reloadSoon(); }
        else {
          if (btn) { btn.disabled = false; btn.textContent = "创建提案"; }
          toast((res.data && res.data.error) || "提案失败", false);
        }
      }).catch(function () {
        if (btn) { btn.disabled = false; btn.textContent = "创建提案"; }
        toast("连接本地服务失败", false);
      });
  }
})();
"""
