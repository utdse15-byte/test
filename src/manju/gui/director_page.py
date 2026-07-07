"""导演助手 /director — the six-step AI-director loop as a workbench page.

The GUI twin of ``manju director …`` (goal item 17): the same
:mod:`manju.build.director` core the CLI and MCP drive, rendered as one page so
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


def _shell(title: str, token: str, active: str, body: str) -> str:
    from .pages import nav_html

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
        '<script src="/director.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{_e(active)}">\n'
        + nav_html(active)
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


def _action_brief(a: dict[str, Any]) -> str:
    t = a.get("type")
    if t == "build":
        return f"target={a.get('target')} gen={a.get('gen')}"
    if t in ("redo", "voice"):
        return str(a.get("shot") or "") + (f" · {a['provider']}" if a.get("provider") else "")
    if t == "repair":
        return f"{a.get('op')} {a.get('shot')}"
    if t in ("captions", "packaging"):
        return str(a.get("op") or "")
    if t == "rollback":
        return f"{a.get('op')} {a.get('shot') or a.get('path') or ''}".strip()
    if t == "snapshot":
        return str(a.get("label") or "")
    if t == "mixer":
        return ", ".join((a.get("changes") or {}).keys())
    return ""


def _suggestion_card(s: dict[str, Any]) -> str:
    import json as _json

    action = s.get("action")
    btn = ""
    if action:
        payload = _e(_json.dumps({"actions": [action], "why": s.get("text", "")},
                                 ensure_ascii=False))
        btn = (f'<button class="btn mini" data-act="suggest-propose" '
               f'data-payload="{payload}">转为新提案</button>')
    else:
        btn = '<span class="muted mini">需人工处理（无自动动作）</span>'
    return (
        f'<div class="dg-sugg"><span class="dg-kind">{_e(s.get("kind"))}</span>'
        f'<span class="dg-sugg-text">{_e(s.get("text"))}</span>{btn}</div>'
    )


def _impact_cell(impact: dict[str, Any]) -> str:
    shots = ", ".join(str(x) for x in (impact.get("shots") or [])) or "—"
    outs = ", ".join(str(x) for x in (impact.get("outputs") or [])) or "—"
    note = impact.get("note") or ""
    note_html = f'<div class="muted mini">{_e(note)}</div>' if note else ""
    return (f'<div>影响镜头 shots: {_e(shots)}</div>'
            f'<div>产物 outputs: {_e(outs)}</div>{note_html}')


def _action_row(i: int, pa: dict[str, Any]) -> str:
    a = pa.get("action") or {}
    cost = pa.get("estimated_cost") or 0
    cur = pa.get("currency") or ""
    cost_html = (f'<span class="dg-cost">≈{cost:g} {_e(cur)}</span>'
                 if cost else '<span class="muted mini">免费 free</span>')
    result = pa.get("result")
    res_html = ""
    if result is not None:
        mark = "✓" if result.get("ok") else "✗"
        cls = "ok" if result.get("ok") else "bad"
        res_html = (f'<div class="dg-res {cls}">{mark} '
                    f'{_e(result.get("error") or "完成")}</div>')
    return (
        '<tr class="dg-act">'
        f'<td class="dg-i">#{i}</td>'
        f'<td class="dg-type">{_e(a.get("type"))}<div class="muted mini">'
        f'{_e(_action_brief(a))}</div></td>'
        f'<td class="dg-impact">{_impact_cell(pa.get("impact") or {})}</td>'
        f'<td class="dg-actcost">{cost_html}{res_html}</td>'
        '</tr>'
    )


def _outcome_view(outcome: dict[str, Any]) -> str:
    diff = outcome.get("diff") or {}
    out = diff.get("outputs") or {}
    parts = ['<div class="dg-outcome"><h3>结果 result · 差异 diff</h3>']

    new = ", ".join(out.get("new_finals") or []) or "—"
    rer = ", ".join(out.get("rerendered_finals") or []) or "—"
    parts.append(f'<div class="dg-diff">新成片 new: {_e(new)} · '
                 f'重渲染 re-rendered: {_e(rer)}</div>')
    spec = diff.get("spec") or {}
    if spec.get("available") and spec.get("text"):
        parts.append('<details class="dg-specdiff"><summary class="muted">'
                     '真相文本差异 spec diff</summary>'
                     f'<pre>{_e(spec.get("text"))}</pre></details>')

    failure = outcome.get("failure")
    if failure:
        parts.append(
            '<div class="dg-failure"><b>失败 failure</b> '
            f'#{_e(failure.get("id"))} · {_e(failure.get("step"))} · '
            f'{_e(failure.get("subject"))}<div>{_e(failure.get("cause"))}</div>'
            '<div class="muted mini">见 manju failures / reports/failures.jsonl</div></div>')

    return "".join(parts) + "</div>"


def _proposal_card(pr: dict[str, Any]) -> str:
    pid = pr.get("id")
    state = pr.get("state") or "proposed"
    label, cls = _STATE_CHIP.get(state, (state, "d-proposed"))
    current = pr.get("current")
    stale_flag = ""
    if current is False and state in ("proposed", "confirmed"):
        stale_flag = '<span class="dg-chip d-expired">待更新 project moved</span>'

    cur = pr.get("currency") or ""
    cost = pr.get("estimated_cost") or 0
    head = (
        f'<div class="dg-head"><span class="dg-id">{_e(pid)}</span>'
        f'<span class="dg-chip {cls}">{_e(label)}</span>{stale_flag}'
        f'<span class="dg-total">试跑预估 est: ≈{cost:g} {_e(cur)}</span>'
        f'<span class="muted mini">[{_e(pr.get("actor"))}]</span></div>'
    )
    why = f'<div class="dg-why">{_e(pr.get("why"))}</div>' if pr.get("why") else ""

    rows = "".join(_action_row(i, pa) for i, pa in enumerate(pr.get("actions") or []))
    table = (
        '<table class="dg-acts"><thead><tr><th>#</th><th>动作 action</th>'
        '<th>影响 impact</th><th>花费 cost</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )

    # 确认 and 执行 are SEPARATE buttons — never one (§8.3).
    buttons = ['<div class="dg-btns">']
    can_confirm = state == "proposed" and current is not False
    can_run = state == "confirmed" and current is not False
    if can_confirm:
        buttons.append(f'<button class="btn" data-act="confirm" data-id="{_e(pid)}">'
                       '确认 confirm</button>')
    if can_run:
        buttons.append(f'<button class="btn" data-act="run" data-id="{_e(pid)}">'
                       '执行 execute</button>')
    if state in ("proposed", "confirmed", "expired"):
        buttons.append(f'<button class="btn ghost" data-act="reject" data-id="{_e(pid)}">'
                       '否决 reject</button>')
    if state in ("proposed", "confirmed") and current is False:
        buttons.append('<span class="muted mini">项目已变动:确认/执行会被拒为待更新,'
                       '请按当前状态重新提案</span>')
    buttons.append("</div>")

    outcome = pr.get("outcome")
    outcome_html = _outcome_view(outcome) if outcome else ""
    # next-step suggestions live on the outcome AND are recomputed live; render
    # the live top-level suggestions section instead of the frozen outcome ones.

    return (f'<div class="dg-card" data-id="{_e(pid)}" data-state="{_e(state)}">'
            + head + why + table + "".join(buttons) + outcome_html + '</div>')


def render_director(project: Any, token: str) -> str:
    head = ('<div class="page-h"><h1>导演助手 Director</h1>'
            '<span class="muted">提案 → 影响/花费(试跑) → 确认 → 执行 → 差异 → 下一步。'
            '确认与执行是两次独立点击(§8.3 先确认再花费)。</span></div>')

    try:
        payload = proposals_payload(project)
    except Exception as exc:
        return _shell("导演助手", token, "/director",
                      head + f'<p class="err panel">{_e(exc)}</p>')

    suggestions = payload["suggestions"]
    sugg_body = "".join(_suggestion_card(s) for s in suggestions) or \
        '<p class="muted">暂无建议 — 一切就绪,或先 build。</p>'
    sugg_panel = (
        '<div class="dg-suggpanel panel"><h2>下一步建议 suggest next</h2>'
        '<p class="muted">来自现有信号(待更新→重做 · 质检→修复方案 · 缺失→生成 · '
        '花费护栏→提醒);点“转为新提案”一步生成提案。</p>'
        f'<div id="dg-suggs">{sugg_body}</div></div>'
    )

    # power-user / agent-parity: a raw actions composer (JSON), collapsed.
    composer = (
        '<details class="dg-composer panel"><summary>自定义提案 custom proposal '
        '(JSON actions)</summary>'
        '<p class="muted mini">动作白名单:build · redo · voice · repair · mixer · '
        'captions · packaging · snapshot · rollback。示例:'
        '[{"type":"redo","shot":"S002"}]</p>'
        '<textarea id="dg-actions" rows="4" '
        'placeholder=\'[{"type":"snapshot","label":"cp"}]\'></textarea>'
        '<input id="dg-why" type="text" placeholder="为什么 why(可选)">'
        '<button class="btn" id="dg-propose">提案 propose</button></details>'
    )

    cards = "".join(_proposal_card(pr) for pr in payload["proposals"])
    proposals_panel = (
        '<div class="dg-proposals"><h2>提案 proposals</h2>'
        + (cards or '<p class="muted panel">还没有提案 — 上面“转为新提案”或自定义一个。</p>')
        + '</div>'
    )

    body = head + sugg_panel + composer + proposals_panel
    return _shell("导演助手", token, "/director", body)


# ============================================================ assets (css/js)


def render_director_css() -> str:
    return _DIRECTOR_CSS


def render_director_js() -> str:
    return _DIRECTOR_JS


_DIRECTOR_CSS = """
.dg-suggpanel, .dg-composer { margin-bottom:16px; }
.dg-sugg { display:flex; align-items:center; gap:10px; padding:6px 0;
  border-bottom:1px solid #1a1f29; flex-wrap:wrap; }
.dg-sugg:last-child { border-bottom:none; }
.dg-kind { font-size:11px; color:#6ea8fe; text-transform:uppercase;
  min-width:70px; }
.dg-sugg-text { flex:1; min-width:200px; }
.mini { font-size:12px; }
.dg-composer textarea { width:100%; font-family:monospace; margin:6px 0; }
.dg-composer input { width:100%; margin:6px 0; }
.dg-card { background:#12161f; border:1px solid #232a36; border-radius:8px;
  padding:12px; margin-bottom:14px; }
.dg-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.dg-id { font-weight:600; color:#c3c9d5; font-variant-numeric:tabular-nums; }
.dg-total { color:#e0af68; margin-left:auto; }
.dg-why { color:#9aa4b5; margin:6px 0; }
.dg-chip { font-size:11px; padding:2px 8px; border-radius:10px; font-weight:600; }
.d-proposed { background:#1c2a44; color:#6ea8fe; }
.d-confirmed { background:#183a2a; color:#7ee787; }
.d-executing { background:#3a3418; color:#e0af68; }
.d-done { background:#173a1f; color:#7ee787; }
.d-failed { background:#3a1c1c; color:#ff7b72; }
.d-rejected { background:#26262c; color:#8b93a3; }
.d-expired { background:#3a2c18; color:#e0af68; }
.dg-acts { width:100%; border-collapse:collapse; margin:8px 0; }
.dg-acts th { text-align:left; font-size:12px; color:#8b93a3; padding:5px 8px;
  border-bottom:1px solid #262c38; }
.dg-acts td { padding:6px 8px; vertical-align:top; border-bottom:1px solid #1a1f29; }
.dg-i { color:#6ea8fe; font-variant-numeric:tabular-nums; }
.dg-type { font-weight:600; }
.dg-cost { color:#e0af68; }
.dg-res.ok { color:#7ee787; } .dg-res.bad { color:#ff7b72; }
.dg-btns { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:6px; }
.dg-outcome { margin-top:10px; border-top:1px solid #232a36; padding-top:8px; }
.dg-diff { color:#c3c9d5; }
.dg-specdiff pre { background:#0b0e14; padding:8px; border-radius:6px;
  overflow-x:auto; font-size:12px; max-height:280px; }
.dg-failure { background:#2a1616; border:1px solid #3a1c1c; border-radius:6px;
  padding:8px; margin-top:8px; color:#ffb4ab; }
.btn.mini { padding:2px 8px; font-size:12px; }
"""


_DIRECTOR_JS = r"""
"use strict";
(function () {
  var META = document.querySelector('meta[name="manju-token"]');
  var TOKEN = META ? META.getAttribute("content") : "";

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Manju-Token": TOKEN },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        return { status: r.status, data: d };
      });
    });
  }
  function toast(msg, ok) {
    var t = document.getElementById("toast");
    if (!t) return;
    var el = document.createElement("div");
    el.className = "toast-item " + (ok === false ? "bad" : "good");
    el.textContent = msg;
    t.appendChild(el);
    setTimeout(function () { el.remove(); }, 4000);
  }
  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }

  if (document.body.getAttribute("data-page") !== "/director") return;

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-act]");
    if (btn) {
      var act = btn.getAttribute("data-act");
      if (act === "confirm") return doConfirm(btn.getAttribute("data-id"));
      if (act === "run") return doRun(btn.getAttribute("data-id"), btn);
      if (act === "reject") return doReject(btn.getAttribute("data-id"));
      if (act === "suggest-propose") return doSuggestPropose(btn);
      return;
    }
    if (ev.target.id === "dg-propose") return doCompose();
  });

  function doConfirm(id) {
    post("/api/director/confirm", { id: id }).then(function (res) {
      if (res.status === 200) { toast("已确认 " + id + " — 现在可执行", true); reloadSoon(); }
      else toast((res.data && res.data.error) || "确认失败", false);
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
        if (btn) { btn.disabled = false; btn.textContent = "执行 execute"; }
        toast((res.data && res.data.error) || "执行失败", false);
      }
    });
  }
  function doReject(id) {
    if (!confirm("否决提案 " + id + " ?")) return;
    post("/api/director/reject", { id: id }).then(function (res) {
      if (res.status === 200) { toast("已否决 " + id, true); reloadSoon(); }
      else toast((res.data && res.data.error) || "失败", false);
    });
  }
  function doSuggestPropose(btn) {
    var payload;
    try { payload = JSON.parse(btn.getAttribute("data-payload")); }
    catch (e) { toast("建议数据损坏", false); return; }
    post("/api/director/propose", payload).then(function (res) {
      if (res.status === 200) { toast("已转为提案 " + (res.data.id || ""), true); reloadSoon(); }
      else toast((res.data && res.data.error) || "提案失败", false);
    });
  }
  function doCompose() {
    var ta = document.getElementById("dg-actions");
    var why = document.getElementById("dg-why");
    var actions;
    try { actions = JSON.parse(ta.value); }
    catch (e) { toast("actions 不是合法 JSON", false); return; }
    if (!Array.isArray(actions) || !actions.length) { toast("actions 需为非空数组", false); return; }
    post("/api/director/propose", { actions: actions, why: why ? why.value : "" })
      .then(function (res) {
        if (res.status === 200) { toast("已提案 " + (res.data.id || ""), true); reloadSoon(); }
        else toast((res.data && res.data.error) || "提案失败", false);
      });
  }
})();
"""
