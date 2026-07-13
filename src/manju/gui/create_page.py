"""创作 /create — the creation funnel as a creative-assistant product surface.

Round V, goal item 2's GUI half (§2 of REPORTS/ROUND-V-REFERENCES-1.md). The
CLI half is ``manju create`` + :mod:`manju.build.funnel`; this is its human
face: the staged funnel (立意→梗概→节拍→剧本→分镜→生成计划→生成) rendered as a
*workspace*, not a protocol. The market's idea→storyboard funnels (LTX / HeyGen
/ 即创…) all propose STRUCTURE from an idea, then let a human decide at a few
gates (§2c/§2d); Manju's stance (§0) is that the LLM lives in the driving agent,
so this page never calls an LLM — it renders :func:`funnel_status`, edits the
story truth files the CLI already writes, and points at the ONE approval surface
(the director) rather than duplicating buttons.

Built to the same stance as the round-T/U pages (:mod:`director_page`):

  * server-rendered — a plain GET bakes the live funnel + the current stage's
    workspace into the DOM; ``/create.js`` layers on save / scaffold / the skill
    modal and the refresh;
  * CSP-safe — CSS/JS in external files, no inline handlers or ``style=``, every
    mutating request carries the ``X-Manju-Token`` from the ``manju-token`` meta;
  * XSS-safe — all server text (evidence lines, file bodies, skill markdown,
    proposal ids) is ``html.escape``-d here and the script only ever writes
    ``textContent``, never ``innerHTML``;
  * one core — the textarea save writes the SAME story file the CLI/agent edit,
    the 生成模板 button calls the SAME :func:`funnel.scaffold_stage` template the
    CLI ``manju create`` uses, and the plan stage only LINKS to the director
    (approve happens THERE — never a second confirm/execute button here).

The AI-proposes-human-decides framing (§2's key finding) is honest on every
pre-storyboard card: 让 AI 起草 = a copyable ``manju auto "起草…"`` command chip,
自己写 = the textarea. No pixel of this page ever generates for you.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

__all__ = [
    "PAGE_PATHS_CREATE",
    "render",
    "render_create_css",
    "render_create_js",
    "create_payload",
    "skill_payload",
    "stage_files",
]

PAGE_PATHS_CREATE = frozenset({"/create"})

# funnel stage state → rail glyph (mirrors cli.py's _FUNNEL_MARK).
_MARK = {"done": "✓", "current": "▶", "todo": "○"}

# director proposal state → (chinese chip, css class) — read-only echo of the
# director page's own vocabulary (§ we never approve here, only surface state).
_P_STATE = {
    "proposed": ("已提案", "cw-p-proposed"),
    "confirmed": ("已确认", "cw-p-confirmed"),
    "executing": ("执行中", "cw-p-executing"),
    "done": ("已完成", "cw-p-done"),
    "failed": ("失败", "cw-p-failed"),
    "rejected": ("已否决", "cw-p-rejected"),
    "expired": ("待更新", "cw-p-expired"),
}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


# ------------------------------------------------------------------ data


def stage_files() -> dict[str, str]:
    """``{stage id: story relpath}`` for the pre-storyboard WRITING stages —
    the only stages this page edits as text. Derived from the funnel's own
    :data:`STAGES`/:data:`PRE_STORYBOARD`, never a parallel list (§reuse)."""
    from ..build.funnel import PRE_STORYBOARD, STAGES

    return {s.id: s.artifact for s in STAGES if s.id in PRE_STORYBOARD}


def create_payload(project: Any) -> dict[str, Any]:
    """The funnel status the page + its refresh render from (the CLI's
    ``manju create --json`` shape, verbatim from :func:`funnel_status`)."""
    from ..build.funnel import funnel_status

    return funnel_status(project)


def skill_payload(project: Any, skill_id: str) -> dict[str, Any]:
    """The skill-modal body: a skill's full ``SKILL.md`` text (the human-facing
    twin of ``manju skills show``). Raises ``KeyError`` for an unknown id."""
    from ..core.skills import load_skill, skill_text

    info = load_skill(project, skill_id)  # KeyError with available ids when unknown
    return {"id": info.id, "name": info.name, "source": info.source,
            "text": skill_text(project, skill_id)}


def _read(project: Any, relpath: str) -> str | None:
    path = project.root / relpath
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


# ------------------------------------------------------------------ shell


def _shell(title: str, token: str, active: str, body: str) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    nav, bcls = chrome(active)
    return (
        "<!doctype html>\n"
        '<html lang="zh">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/create.css">\n'
        + GLOSSARY_HEAD
        + '<script src="/common.js" defer></script>\n<script src="/create.js" defer></script>\n'
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


def render(path: str, project: Any, token: str, query: dict[str, list[str]]) -> str:
    if path == "/create":
        return render_create(project, token)
    raise KeyError(path)


# ------------------------------------------------------------------ rail (A)


def _stage_card(s: dict[str, Any], files: dict[str, str]) -> str:
    """One stage as a rail card: 中文名, evidence, skill chip, and — for a
    reachable text stage — an ✎编辑 chip that swaps its editor into view."""
    state = s["state"]
    sid = s["id"]
    mark = _MARK.get(state, "·")
    chips = [
        '<button type="button" class="cw-skillchip" data-act="skill" '
        f'data-skill="{_e(s["skill"])}" '
        f'title="查看技能全文 (open skill)">📖 {_e(s["skill"])}</button>'
    ]
    if sid in files and state in ("done", "current"):
        chips.append('<button type="button" class="cw-editchip" data-act="edit" '
                     f'data-stage="{_e(sid)}">✎ 编辑</button>')
    hero = (f'<div class="cw-next">下一步 · {_e(s["next_action"])}</div>'
            if state == "current" else "")
    return (
        f'<div class="cw-stage cw-{_e(state)}" data-stage="{_e(sid)}">'
        f'<div class="cw-mark">{mark}</div>'
        f'<div class="cw-cn">{_e(s["cn"])}<span class="cw-id">{_e(sid)}</span></div>'
        f'<div class="cw-ev muted">{_e(s["evidence"])}</div>'
        f'<div class="cw-chips">{"".join(chips)}</div>'
        f'{hero}</div>'
    )


# ------------------------------------------------------- workbench: text (B/C)


def _editor(project: Any, s: dict[str, Any], files: dict[str, str], *,
            active: bool) -> str:
    """The textarea workspace over a pre-storyboard story file. Loads the file's
    current content; save (POST) writes it verbatim; when the file is absent and
    scaffoldable, a 生成模板 button calls the SAME template the CLI create uses.
    Carries the AI-proposes-human-decides hint (part C)."""
    from ..build.funnel import SCAFFOLDS

    sid, cn = s["id"], s["cn"]
    relpath = files[sid]
    text = _read(project, relpath)
    exists = text is not None
    scaffoldable = sid in SCAFFOLDS

    # part C: 让 AI 起草 (a copyable command chip) beside 自己写 (the textarea).
    cmd = f'manju auto "起草{cn}"'
    ai = (
        '<div class="cw-ai">'
        '<span class="cw-ai-lead">让 AI 起草</span>'
        f'<code class="cw-cmd">{_e(cmd)}</code>'
        f'<button type="button" class="cw-copy" data-act="copy" '
        f'data-copy="{_e(cmd)}">复制</button>'
        '<span class="cw-ai-or muted">— 或在下面<b>自己写</b>。'
        '本页从不替你调用 AI(§0)。</span>'
        '</div>'
    )

    scaffold_btn = ""
    empty = ""
    if not exists:
        if scaffoldable:
            scaffold_btn = ('<button type="button" class="btn ghost" data-act="scaffold" '
                            f'data-stage="{_e(sid)}">生成模板 scaffold</button>')
            empty = (f'<p class="cw-empty muted">{_e(relpath)} 还不存在 —— '
                     '点「生成模板」起个头,或直接在下面写;保存即创建。</p>')
        else:
            empty = (f'<p class="cw-empty muted">{_e(relpath)} 还不存在 —— '
                     '在下面写,保存即创建。</p>')

    return (
        f'<section class="cw-editor{"" if active else " hidden"}" '
        f'data-stage="{_e(sid)}">'
        f'<div class="cw-eh"><h2>{_e(cn)} '
        f'<span class="muted">· {_e(relpath)}</span></h2>{scaffold_btn}</div>'
        f'{ai}{empty}'
        f'<textarea class="cw-text" data-stage="{_e(sid)}" spellcheck="false" '
        f'aria-label="{_e(cn)} 正文">{_e(text or "")}</textarea>'
        '<div class="cw-saverow">'
        f'<button type="button" class="btn" data-act="save" data-stage="{_e(sid)}">'
        '保存 save</button>'
        f'<span class="muted cw-savehint">写入 {_e(relpath)} 并记一条事件;'
        '内容够充实后本阶段自动完成(引擎从不代写,§2)。</span>'
        '</div></section>'
    )


# ------------------------------------------------ workbench: non-text (B)


def _plan_workbench(project: Any, entry: dict[str, Any]) -> str:
    """The 生成计划 stage: READ the director's proposals and link to /director.
    Approve (确认/执行) happens on the director page — one approval surface, so
    this panel never renders a confirm/execute button (§2d)."""
    try:
        from ..build.director import list_proposals

        props = list_proposals(project)
    except Exception as exc:  # a broken proposal file never bricks the page
        inner = f'<p class="err">{_e(exc)}</p>'
    else:
        if not props:
            inner = ('<p class="muted">还没有生成计划提案。到导演助手 propose 起草,'
                     'confirm 过审(approve-before-spend)后即可生成。</p>')
        else:
            rows = []
            for p in props:
                label, cls = _P_STATE.get(p.state, (p.state, "cw-p-proposed"))
                cost = p.estimated_cost or 0
                cur = p.currency or ""
                why = f' · {_e(p.why)}' if p.why else ""
                rows.append(
                    f'<li class="cw-prop"><span class="cw-pchip {cls}">{_e(label)}</span>'
                    f'<span class="cw-pid">{_e(p.id)}</span>'
                    f'<span class="muted">{len(p.actions)} 动作 · 试跑 ≈{cost:g} '
                    f'{_e(cur)}</span>{why}</li>'
                )
            inner = '<ul class="cw-props">' + "".join(rows) + "</ul>"
    return (
        '<section class="cw-special panel"><h2>生成计划 Plan '
        '<span class="muted">· approve-before-spend 闸门</span></h2>'
        f'<p class="muted">{_e(entry["evidence"])}</p>'
        + inner
        + '<p class="cw-planhint muted">审批(确认 / 执行)在导演助手一处进行 —— '
        '这里只读,不重复按钮。</p>'
        '<a class="btn" href="/director">去导演助手审批 →</a></section>'
    )


def _special_workbench(project: Any, current: str, status: dict[str, Any]) -> str:
    """The non-text current-stage workspace: storyboard / plan / produce."""
    entry = next(s for s in status["stages"] if s["id"] == current)
    if current == "storyboard":
        n = len(project.shot_ids())
        return (
            '<section class="cw-special panel"><h2>分镜 Storyboard</h2>'
            f'<p class="muted">{_e(entry["evidence"])}</p>'
            f'<p>当前 <b>{n}</b> 个镜头 —— 到分镜工作台补齐镜头并 '
            '<code>manju check</code> 过校验。</p>'
            '<a class="btn" href="/storyboard">去分镜工作台 →</a></section>'
        )
    if current == "plan":
        return _plan_workbench(project, entry)
    # produce
    final = project.newest_final_path()
    link = '<a class="btn" href="/">回工作台构建 build →</a>'
    if final is not None:
        rel = project.relpath(final)
        link += (f'<a class="btn ghost" href="/media/{quote(rel, safe="/")}">'
                 '看最新成片 →</a>')
    return (
        '<section class="cw-special panel"><h2>生成 Produce</h2>'
        f'<p class="muted">{_e(entry["evidence"])}</p>'
        '<p>分镜已就绪 —— 回工作台运行 build 出片。</p>'
        f'<div class="cw-btnrow">{link}</div></section>'
    )


def _complete_workbench(project: Any) -> str:
    final = project.newest_final_path()
    link = '<a class="btn" href="/">回工作台 →</a>'
    if final is not None:
        rel = project.relpath(final)
        link += (f'<a class="btn ghost" href="/media/{quote(rel, safe="/")}">'
                 '看最新成片 →</a>')
    return (
        '<section class="cw-special panel"><h2>全部完成 ✅</h2>'
        '<p>创作漏斗已走完 —— 立意到成片每一步都落地了。可回工作台继续打磨或再出一版。</p>'
        f'<div class="cw-btnrow">{link}</div></section>'
    )


# ------------------------------------------------------------------ render


def render_create(project: Any, token: str) -> str:
    head = ('<div class="page-h"><h1>创作 Create</h1>'
            '<span class="muted">把一句话想法一步步推到可以按「生成」的分镜 · '
            'AI 起草、你定夺 · 每步一个真相文件(引擎从不代写)</span></div>')

    try:
        status = create_payload(project)
    except Exception as exc:
        return _shell("创作", token, "/create",
                      head + f'<p class="err panel">{_e(exc)}</p>')

    files = stage_files()
    current = status.get("current")

    # part D: a fresh project leads with the 立意 invitation — say what this
    # place is, then the ONE action (NN/g empty-state discipline).
    intro = ""
    if status.get("done") == 0 and current == "brief":
        intro = (
            '<div class="cw-intro panel"><h2>从这里开始 · 立意</h2>'
            '<p>创作台把一个想法一步步推到可以按「生成」的分镜:'
            '<b>立意 → 梗概 → 节拍 → 剧本 → 分镜 → 生成计划 → 生成</b>。'
            '每一步你写一个真相文件,AI 可起草、你来定夺。'
            '先在下面写下你的<b>一句话立意</b>:谁、在哪、发生什么、为什么抓人。</p></div>'
        )

    rail = ('<div class="cw-railwrap panel"><div class="cw-rail">'
            + "".join(_stage_card(s, files) for s in status["stages"])
            + '</div></div>')

    # the workbench: an editor per reachable (done/current) text stage — the
    # current one shown, the rest hidden but swappable via each card's ✎编辑 —
    # plus the non-text workspace when the current stage is storyboard/plan/
    # produce (or the completion panel when the funnel is done).
    editors = [
        _editor(project, s, files, active=(s["id"] == current))
        for s in status["stages"]
        if s["id"] in files and s["state"] in ("done", "current")
    ]
    if current in ("storyboard", "plan", "produce"):
        special = _special_workbench(project, current, status)
    elif current is None:
        special = _complete_workbench(project)
    else:
        special = ""

    modal = (
        '<div id="cw-modal" class="cw-modal" data-act="modal-close" hidden>'
        '<div class="cw-modalbox">'
        '<div class="cw-modalhead"><h3 id="cw-modaltitle">技能 skill</h3>'
        '<button type="button" class="btn ghost mini" data-act="modal-close">'
        '关闭 ✕</button></div>'
        '<pre id="cw-modalbody" class="cw-modalbody"></pre>'
        '<div class="cw-modalfoot muted">同一份内容,agent 用 '
        '<code>manju skills show &lt;id&gt;</code> 取。</div>'
        '</div></div>'
    )

    body = (head + intro + rail
            + '<div id="cw-workbench">' + "".join(editors) + special + '</div>'
            + modal)
    return _shell("创作", token, "/create", body)


# ============================================================ assets (css/js)


def render_create_css() -> str:
    return _CREATE_CSS


def render_create_js() -> str:
    return _CREATE_JS


_CREATE_CSS = """
/* manju gui — 创作 /create workspace (round V, goal item 2). Loaded AFTER
   /app.css + /pages.css; reuses their :root palette + .panel/.btn/.muted. */

.cw-intro { border-left: 3px solid var(--accent); }
.cw-intro h2 { margin: 0 0 .4rem; font-size: 1.05rem; }

/* -------------------------------------------------------------- rail (A) */
.cw-railwrap { overflow-x: auto; }
.cw-rail { display: flex; gap: 0; align-items: stretch; min-width: min-content; }
.cw-stage {
  flex: 1 1 0; min-width: 150px; position: relative;
  padding: .6rem .8rem .7rem; border: 1px solid var(--line); border-radius: 10px;
  background: var(--panel2); margin-right: 1.1rem; display: flex;
  flex-direction: column; gap: .3rem;
}
.cw-stage:last-child { margin-right: 0; }
/* the connector arrow between cards */
.cw-stage:not(:last-child)::after {
  content: "›"; position: absolute; right: -.85rem; top: 50%;
  transform: translateY(-50%); color: var(--muted); font-size: 1.1rem;
}
.cw-mark { font-size: 1rem; line-height: 1; }
.cw-cn { font-weight: 700; font-size: .95rem; display: flex; align-items: baseline; gap: .35rem; }
.cw-id { font-size: .68rem; font-weight: 400; color: var(--muted); font-family: var(--mono); }
.cw-ev { font-size: .74rem; line-height: 1.3; }
.cw-chips { display: flex; flex-wrap: wrap; gap: .3rem; margin-top: auto; padding-top: .3rem; }
.cw-skillchip, .cw-editchip {
  font: inherit; font-size: .72rem; cursor: pointer; color: var(--fg);
  background: var(--panel); border: 1px solid var(--line); border-radius: 999px;
  padding: .1rem .5rem;
}
.cw-skillchip:hover, .cw-editchip:hover { border-color: var(--accent); }
.cw-next {
  font-size: .74rem; color: var(--accent); border-top: 1px dashed var(--line);
  padding-top: .3rem; line-height: 1.3;
}
/* done / current / todo states */
.cw-done { opacity: .92; }
.cw-done .cw-mark { color: var(--ok); }
.cw-current { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; background: var(--panel); }
.cw-current .cw-mark { color: var(--accent); }
.cw-todo { opacity: .5; }
.cw-todo .cw-mark { color: var(--muted); }

/* --------------------------------------------------------- workbench (B/C) */
#cw-workbench { margin-top: 1rem; }
.cw-editor { display: block; }
.cw-eh { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.cw-eh h2 { font-size: 1.1rem; margin: 0; }
.cw-ai {
  display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin: .5rem 0;
  padding: .45rem .6rem; background: var(--panel2); border: 1px solid var(--line); border-radius: 8px;
}
.cw-ai-lead { font-weight: 600; font-size: .82rem; }
.cw-cmd { font-family: var(--mono); font-size: .8rem; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: .12rem .45rem; }
.cw-copy { font: inherit; font-size: .74rem; cursor: pointer; color: var(--fg); background: var(--panel); border: 1px solid var(--line); border-radius: 6px; padding: .1rem .5rem; }
.cw-copy:hover { border-color: var(--accent); }
.cw-ai-or { font-size: .8rem; }
.cw-empty { font-size: .82rem; margin: .4rem 0; }
.cw-text {
  width: 100%; box-sizing: border-box; min-height: 320px; resize: vertical;
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 8px; padding: .7rem .8rem; font-family: var(--mono);
  font-size: .86rem; line-height: 1.5;
}
.cw-saverow { display: flex; align-items: center; gap: .7rem; margin-top: .5rem; flex-wrap: wrap; }
.cw-savehint { font-size: .78rem; }

.cw-special { margin-top: 1rem; }
.cw-special h2 { font-size: 1.1rem; margin: 0 0 .4rem; }
.cw-btnrow { display: flex; gap: .6rem; flex-wrap: wrap; margin-top: .5rem; }
.cw-props { list-style: none; margin: .5rem 0; padding: 0; display: flex; flex-direction: column; gap: .35rem; }
.cw-prop { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; font-size: .84rem; }
.cw-pid { font-family: var(--mono); }
.cw-pchip { font-size: .72rem; padding: .1rem .5rem; border-radius: 999px; font-weight: 600; }
.cw-p-proposed { background: #1c2a44; color: #6ea8fe; }
.cw-p-confirmed, .cw-p-done { background: #173a1f; color: #7ee787; }
.cw-p-executing, .cw-p-expired { background: #3a2c18; color: #e0af68; }
.cw-p-failed { background: #3a1c1c; color: #ff7b72; }
.cw-p-rejected { background: #26262c; color: #8b93a3; }
.cw-planhint { font-size: .8rem; margin: .4rem 0; }

/* ------------------------------------------------------------- skill modal */
.cw-modal {
  position: fixed; inset: 0; z-index: 300; display: flex; align-items: center;
  justify-content: center; padding: 1.5rem; background: rgba(0,0,0,.62);
}
.cw-modalbox {
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  width: min(760px, 100%); max-height: 82vh; display: flex; flex-direction: column;
  box-shadow: 0 12px 40px rgba(0,0,0,.55);
}
.cw-modalhead { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .7rem 1rem; border-bottom: 1px solid var(--line); }
.cw-modalhead h3 { margin: 0; font-size: 1rem; }
.cw-modalbody {
  margin: 0; padding: 1rem; overflow: auto; white-space: pre-wrap; word-break: break-word;
  font-family: var(--mono); font-size: .82rem; line-height: 1.5; color: var(--fg);
}
.cw-modalfoot { padding: .5rem 1rem; border-top: 1px solid var(--line); font-size: .76rem; }
"""


_CREATE_JS = r"""
"use strict";
(function () {
  if (document.body.getAttribute("data-page") !== "/create") return;


  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }

  // -------- one editor visible at a time; ✎编辑 swaps stages in ---------
  function showStage(stage) {
    var editors = document.querySelectorAll(".cw-editor");
    var found = false;
    for (var i = 0; i < editors.length; i++) {
      var match = editors[i].getAttribute("data-stage") === stage;
      editors[i].hidden = !match;
      if (match) found = true;
    }
    var special = document.querySelector(".cw-special");
    if (special) special.hidden = found;
    if (found) {
      var ta = document.querySelector('.cw-editor[data-stage="' + cssq(stage) + '"] .cw-text');
      if (ta) ta.focus();
    }
  }
  function cssq(s) { return String(s).replace(/["\\]/g, "\\$&"); }

  // ------------------------------------------ skill modal (escaped body) ---
  function openSkill(id) {
    var modal = document.getElementById("cw-modal");
    var title = document.getElementById("cw-modaltitle");
    var bodyEl = document.getElementById("cw-modalbody");
    if (!modal) return;
    title.textContent = "技能 " + id;
    bodyEl.textContent = "加载中…";
    modal.hidden = false;
    fetch("/api/create/skill?id=" + encodeURIComponent(id), {
      headers: { "X-Manju-Token": TOKEN }
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        return { status: r.status, data: d };
      });
    }).then(function (res) {
      if (res.status === 200 && res.data && typeof res.data.text === "string") {
        title.textContent = res.data.name || id;
        bodyEl.textContent = res.data.text;   // textContent = no HTML injection
      } else {
        bodyEl.textContent = (res.data && res.data.error) || "技能读取失败";
      }
    });
  }
  function closeModal() {
    var modal = document.getElementById("cw-modal");
    if (modal) modal.hidden = true;
  }

  // ---------------------------------------------------------- actions -----
  function doSave(stage) {
    var ta = document.querySelector('.cw-editor[data-stage="' + cssq(stage) + '"] .cw-text');
    if (!ta) return;
    post("/api/create/save", { stage: stage, text: ta.value }).then(function (res) {
      if (res.status === 200) {
        toast("已保存 " + stage + (res.data && res.data.created ? "(已创建)" : ""), true);
        reloadSoon();
      } else {
        toast((res.data && res.data.error) || "保存失败", false);
      }
    });
  }
  function doScaffold(stage) {
    post("/api/create/scaffold", { stage: stage }).then(function (res) {
      if (res.status === 200) { toast("已生成模板 " + stage + " — 编辑它填内容", true); reloadSoon(); }
      else toast((res.data && res.data.error) || "生成模板失败", false);
    });
  }
  function doCopy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { toast("已复制:" + text, true); },
        function () { toast("复制失败,请手动选择", false); }
      );
    } else {
      toast("请手动复制:" + text, true);
    }
  }

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-act]");
    if (!btn) return;
    var act = btn.getAttribute("data-act");
    if (act === "skill") return openSkill(btn.getAttribute("data-skill"));
    if (act === "edit") return showStage(btn.getAttribute("data-stage"));
    if (act === "save") return doSave(btn.getAttribute("data-stage"));
    if (act === "scaffold") return doScaffold(btn.getAttribute("data-stage"));
    if (act === "copy") return doCopy(btn.getAttribute("data-copy"));
    if (act === "modal-close") {
      // close only when the backdrop or an explicit close control is hit
      if (btn.id === "cw-modal" && ev.target !== btn) return;
      return closeModal();
    }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeModal();
  });
})();
"""
