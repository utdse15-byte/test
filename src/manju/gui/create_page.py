"""创作 /create — the creation funnel as a creative-assistant product surface.

Round V, goal item 2's GUI half (§2 of REPORTS/ROUND-V-REFERENCES-1.md). The
CLI half is ``manju create`` + :mod:`manju.build.funnel`; this is its human
face: the staged funnel (story→contracts→proof→candidate build) rendered as a
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

from .a11y import HTML_LANG, NOSCRIPT_HTML, SKIP_LINK_HTML, main_open

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
    from ..build.readiness import production_readiness

    payload = funnel_status(project)
    readiness = production_readiness(project)
    payload["production"] = readiness
    payload["eligibility"] = readiness.get("eligibility") or {}
    if project.creative_opted_in:
        from ..story.coverage import derive_coverage
        from ..story.lint import lint_screen, lint_story
        try:
            coverage = derive_coverage(project, persist=False)
        except Exception as exc:
            coverage = {"error": " ".join(str(exc).split())}
        payload["story_screen"] = {
            "creative": project.creative_status(),
            "story_lint": lint_story(project),
            "screen_lint": lint_screen(project),
            "coverage": coverage,
        }
    return payload


def skill_payload(project: Any, skill_id: str) -> dict[str, Any]:
    """The skill-modal body: a skill's full ``SKILL.md`` text (the human-facing
    twin of ``manju skills show``). Raises ``KeyError`` for an unknown id."""
    from ..core.skills import load_skill, skill_text

    info = load_skill(project, skill_id)  # KeyError with available ids when unknown
    return {"id": info.id, "name": info.name, "source": info.source,
            "text": skill_text(project, skill_id)}


# Sentinel: the file EXISTS but its bytes could not be read faithfully. This
# must be distinguishable from "absent" — the editor renders absent as an empty
# "保存即创建" textarea whose save then OVERWRITES the real file. With
# errors="ignore" a GBK-saved story file also used to lose every non-UTF-8 byte
# (all its Chinese) on the way INTO the textarea, so a save destroyed the truth.
_UNREADABLE = "__manju_unreadable__"


def _read(project: Any, relpath: str) -> str | None:
    path = project.root / relpath
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _UNREADABLE
    except OSError:
        return _UNREADABLE


# ------------------------------------------------------------------ shell


def _shell(title: str, token: str, active: str, body: str, project: Any) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    nav, bcls = chrome(active, project)
    return (
        "<!doctype html>\n"
        f'<html lang="{HTML_LANG}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/create.css">\n'
        + GLOSSARY_HEAD
        # GLOSSARY_HEAD ALREADY carries /webclient.js (see pages.py) — a second
        # include threw "Identifier 'ManjuApiError' has already been declared"
        # on every /create load (the exact bug pages_t.py documents for its
        # three pages; TRISURFACE F-04 caught this fourth copy).
        + '<script src="/common.js" defer></script>\n'
        + '<script src="/create.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{_e(active)}" class="{bcls}">\n'
        + SKIP_LINK_HTML + nav
        + "\n" + main_open() + "\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        + NOSCRIPT_HTML + "\n"
        + "</body>\n</html>\n"
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


def _material_card(s: dict[str, Any], files: dict[str, str], *, active: bool) -> str:
    """One of the four canonical story materials in the focused writer rail.

    The full seven-stage funnel remains available under progressive disclosure;
    this compact list is the daily writing surface and therefore only contains
    the files the page can edit directly.
    """
    sid = s["id"]
    state = s["state"]
    reachable = sid in files and state in ("done", "current")
    from .authoring_journey import story_material_status

    label, detail = story_material_status(s)
    attrs = (
        f' data-act="edit" data-stage="{_e(sid)}"'
        if reachable else ' aria-disabled="true"'
    )
    tag = "button" if reachable else "div"
    current = ' aria-current="true"' if active else ""
    return (
        f'<{tag} class="cw-material cw-material-{_e(state)}" '
        f'data-source-stage="{_e(sid)}"{attrs}{current}>'
        f'<span class="cw-material-mark">{_MARK.get(state, "·")}</span>'
        '<span class="cw-material-copy">'
        f'<strong>{_e(s["cn"])}</strong>'
        f'<span class="cw-material-state">{_e(label)}</span>'
        f'<small>{_e(detail)}</small>'
        f'</span></{tag}>'
    )


def _materials_panel(status: dict[str, Any], files: dict[str, str], current: str | None) -> str:
    rows = [s for s in status["stages"] if s["id"] in files]
    done = sum(1 for row in rows if row.get("satisfied"))
    active = current if current in files else None
    cards = "".join(_material_card(row, files, active=row["id"] == active) for row in rows)
    return (
        '<aside class="cw-materials panel">'
        '<div class="cw-materials-head"><div><h2>故事材料</h2>'
        '<p>这些文件就是项目真相；保存后系统重新判断进度。</p></div>'
        f'<span>{done}/{len(rows)}</span></div>'
        f'<div class="cw-material-list">{cards}</div>'
        '</aside>'
    )


def _full_funnel(status: dict[str, Any], files: dict[str, str]) -> str:
    return (
        '<details class="cw-full-progress panel">'
        '<summary>查看完整创作进度</summary>'
        '<p class="muted">故事材料之后还会进入分镜、生成计划和候选构建。这里保留完整证据，'
        '但不会抢占当前写作空间。'
        '<span class="mj-en" aria-hidden="true"> Contracts: SceneContract, ShotContract.</span></p>'
        '<div class="cw-rail">'
        + "".join(_stage_card(s, files) for s in status["stages"])
        + '</div></details>'
    )


# ------------------------------------------------------- workbench: text (B/C)


def _editor(project: Any, s: dict[str, Any], files: dict[str, str], *,
            active: bool) -> str:
    """The textarea workspace over a pre-storyboard story file. Loads the file's
    current content; save (POST) writes it verbatim; when the file is absent and
    scaffoldable, a 生成模板 button calls the SAME template the CLI create uses.
    Carries the AI-proposes-human-decides hint (part C)."""
    from ..build.funnel import SCAFFOLDS

    from .authoring_journey import story_material_status
    from .server import _text_rev

    sid, cn = s["id"], s["cn"]
    _label, product_detail = story_material_status(s)
    relpath = files[sid]
    text = _read(project, relpath)
    if text == _UNREADABLE:
        # refuse the editor outright: rendering an empty/mangled textarea over
        # an existing file invites a save that destroys it
        return (
            f'<section class="cw-editor{"" if active else " hidden"}" '
            f'data-stage="{_e(sid)}">'
            f'<div class="cw-eh"><h2>{_e(cn)} '
            f'<span class="muted">· {_e(relpath)}</span></h2></div>'
            f'<p class="err">{_e(relpath)} 存在但无法按 UTF-8 读取'
            '(可能是 GBK/ANSI 编码,或被其他程序占用)。'
            '为避免覆盖丢失内容,本页不提供编辑;请把文件另存为 UTF-8 '
            '(VS Code/记事本另存为 → UTF-8)后刷新。</p>'
            '</section>'
        )
    exists = text is not None
    scaffoldable = sid in SCAFFOLDS

    # The external IDE remains optional.  Keep the exact command available, but
    # do not let a technical shell snippet compete with the writer by default.
    cmd = f'manju auto "起草{cn}"'
    ai = (
        '<details class="cw-ai-help">'
        '<summary>让 IDE 助手起草<span class="mj-en" aria-hidden="true">（让 AI 起草）</span></summary>'
        '<div class="cw-ai">'
        '<span class="cw-ai-lead">复制给外部 IDE 助手</span>'
        f'<code class="cw-cmd">{_e(cmd)}</code>'
        f'<button type="button" class="cw-copy" data-act="copy" '
        f'data-copy="{_e(cmd)}">复制</button>'
        '<span class="cw-ai-or muted">Manju 不会在内部调用 AI；草案仍需你检查并保存。</span>'
        '</div></details>'
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
        f'<div class="cw-eh"><div><h2>{_e(cn)}</h2>'
        f'<p class="cw-evidence">{_e(product_detail)}</p></div>{scaffold_btn}</div>'
        f'{empty}'
        f'<textarea class="cw-text" data-stage="{_e(sid)}" spellcheck="false" '
        f'data-rev="{_e(_text_rev(text))}" '
        f'aria-label="{_e(cn)} 正文">{_e(text or "")}</textarea>'
        '<div class="cw-saverow">'
        f'<button type="button" class="btn" data-act="save" data-stage="{_e(sid)}">'
        '保存并检查进度</button>'
        '<span class="cw-save-state" aria-live="polite">已保存</span>'
        '<span class="muted cw-savehint">Ctrl+S 保存；离开未保存内容前会提醒。</span>'
        '</div>'
        f'{ai}'
        '<details class="cw-editor-meta"><summary>文件与技术说明</summary>'
        f'<p><code>{_e(relpath)}</code> · {_e(s.get("evidence") or product_detail)}。'
        '保存会写入项目真相并记录事件。</p></details>'
        '</section>'
    )


# ------------------------------------------------ workbench: non-text (B)


def _plan_workbench(project: Any, entry: dict[str, Any]) -> str:
    """The Animatic/Proof plan stage: read proposals and link to /director.
    Approve (确认/执行) happens on the director page — one approval surface, so
    this panel never renders a confirm/execute button (§2d)."""
    try:
        from ..build.director import list_proposals

        props = list_proposals(project)
    except Exception as exc:  # a broken proposal file never bricks the page
        inner = f'<p class="err">{_e(exc)}</p>'
    else:
        if not props:
            inner = ('<p class="muted">还没有生成计划提案。你可以直接回工作台试算，'
                     '也可以让外部 IDE 助手先提交提案，再由你审阅。</p>')
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
        '<section class="cw-special panel"><h2>审阅生成计划<span class="mj-en" aria-hidden="true"> (Animatic / Proof Plan)</span></h2>'
        f'<p class="muted">{_e(entry["evidence"])}</p>'
        + inner
        + '<p class="cw-planhint muted">这里保持只读。确认和执行只在导演助手中分别进行，'
        '不会因为打开本页而生成或产生费用。</p>'
        '<a class="btn" href="/director">审阅提案 →</a></section>'
    )


def _special_workbench(project: Any, current: str, status: dict[str, Any]) -> str:
    """The non-text current-stage workspace: storyboard / plan / produce."""
    entry = next(s for s in status["stages"] if s["id"] == current)
    if current == "storyboard":
        n = len(project.shot_ids())
        return (
            '<section class="cw-special panel"><h2>建立分镜合同</h2>'
            f'<p class="muted">{_e(entry["evidence"])}</p>'
            f'<p>故事材料已经就绪。当前 <b>{n}</b> 个镜头，下一步把剧本拆成可执行的'
            ' SceneContract / ShotContract，并通过项目校验。</p>'
            '<div class="cw-btnrow"><a class="btn" href="/storyboard">去分镜工作台 →</a>'
            '<a class="btn ghost" href="/director">审阅外部提案</a></div></section>'
        )
    if current == "plan":
        return _plan_workbench(project, entry)
    # produce
    final = project.newest_final_path()
    link = '<a class="btn" href="/">回工作台构建 build →</a>'
    if final is not None:
        rel = project.relpath(final)
        link += (f'<a class="btn ghost" href="/media/{quote(rel, safe="/")}">'
                 '看最新构建产物 →</a>')
    return (
        '<section class="cw-special panel"><h2>生成可审片候选</h2>'
        f'<p class="muted">{_e(entry["evidence"])}</p>'
        '<p>合同与生成计划已就绪。回工作台先试算，再生成候选。'
        '生成成功只得到候选，不会自动选用或锁片。</p>'
        f'<div class="cw-btnrow">{link}</div></section>'
    )


def _complete_workbench(project: Any) -> str:
    final = project.newest_final_path()
    link = '<a class="btn" href="/">回工作台 →</a>'
    if final is not None:
        rel = project.relpath(final)
        link += (f'<a class="btn ghost" href="/media/{quote(rel, safe="/")}">'
                 '看最新构建产物 →</a>')
    return (
        '<section class="cw-special panel"><h2>候选构建阶段完成</h2>'
        '<p>创作源与构建产物已落地。候选素材仍需媒体评审、人工批准和 Picture Lock，'
        '可回工作台继续打磨或再出一版。</p>'
        f'<div class="cw-btnrow">{link}</div></section>'
    )


def _eligibility_panel(status: dict[str, Any]) -> str:
    """Render the readiness vocabulary once for the create workbench."""
    production = status.get("production") or {}
    eligibility = status.get("eligibility") or {}
    counts = eligibility.get("counts") or {}
    stage = production.get("stage", "UNAVAILABLE")
    lock = "eligible" if eligibility.get("picture_lock_eligible") else "not eligible"
    return (
        '<section class="cw-eligibility">'
        '<div class="cw-eligibility-head"><h2>生产资格 / media eligibility</h2>'
        f'<span class="muted">stage {_e(stage)} · Picture Lock {_e(lock)}</span></div>'
        '<div class="cw-eligibility-grid">'
        + "".join(
            f'<div class="cw-eligibility-item"><b>{_e(state)}</b>'
            f'<span>{int(counts.get(state, 0))}</span></div>'
            for state in ("proxy-only", "candidate", "final-eligible")
        )
        + '</div><p class="muted">build ok 只证明执行链成功；proxy-only 是系统体检代理，'
          'candidate 仍待评审，只有 final-eligible 才能进入 Picture Lock。</p>'
        '</section>'
    )


def _technical_panel(status: dict[str, Any], files: dict[str, str]) -> str:
    return (
        '<details class="cw-technical panel">'
        '<summary>生产资格与技术状态</summary>'
        '<p class="muted">这些信息用于审计生成和锁片资格，不影响你继续写作。</p>'
        + _eligibility_panel(status)
        + _full_funnel(status, files)
        + '</details>'
    )


# ------------------------------------------------------------------ render


def render_create(project: Any, token: str) -> str:
    head = ('<div class="page-h"><h1>创作<span class="mj-en" aria-hidden="true"> (Create)</span></h1>'
            '<span class="muted">先写清故事与结尾，再把它变成可执行分镜。可以自己写，也可以让外部 IDE 助手起草；最终都由你保存和确认。</span></div>')

    try:
        status = create_payload(project)
    except Exception as exc:
        return _shell(
            "创作",
            token,
            "/create",
            head + f'<p class="err panel">{_e(exc)}</p>',
            project,
        )

    files = stage_files()
    current = status.get("current")

    # part D: a fresh project leads with the 立意 invitation — say what this
    # place is, then the ONE action (NN/g empty-state discipline).
    intro = ""
    if status.get("done") == 0 and current == "brief":
        intro = (
            '<div class="cw-intro panel"><h2>先写清故事最终改变了什么</h2>'
            '<p>从人物现在的处境、无法回头的变化和最后离开的状态开始。这里保存的是项目真相；'
            '任何外部助手写出的内容都要由你检查并保存后才会生效。</p></div>'
        )

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

    from .authoring_journey import render_authoring_journey

    body = (
        head
        + render_authoring_journey(project, "/create")
        + intro
        + '<div class="cw-layout">'
        + _materials_panel(status, files, current)
        + '<div id="cw-workbench">' + "".join(editors) + special + '</div>'
        + '</div>'
        + _technical_panel(status, files)
        + modal
    )
    return _shell("创作", token, "/create", body, project)


# ============================================================ assets (css/js)


def render_create_css() -> str:
    from .authoring_journey import AUTHORING_JOURNEY_CSS

    return AUTHORING_JOURNEY_CSS + _CREATE_CSS


def render_create_js() -> str:
    return _CREATE_JS


_CREATE_CSS = """
/* manju gui — 创作 /create workspace (round V, goal item 2). Loaded AFTER
   /app.css + /pages.css; reuses their :root palette + .panel/.btn/.muted. */

.cw-intro { border-left: 3px solid var(--accent); }
.cw-intro h2 { margin: 0 0 .4rem; font-size: 1.05rem; }
.cw-layout { display:grid; grid-template-columns:minmax(220px, 270px) minmax(0, 1fr); gap:14px; align-items:start; }
.cw-materials { position:sticky; top:132px; padding:12px; }
.cw-materials-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
.cw-materials-head h2 { margin:0; font-size:1rem; }
.cw-materials-head p { margin:.2rem 0 0; color:var(--muted); font-size:.72rem; line-height:1.4; }
.cw-materials-head > span { flex:0 0 auto; color:var(--accent); font:700 .78rem var(--mono); }
.cw-material-list { display:flex; flex-direction:column; gap:6px; margin-top:10px; }
.cw-material { width:100%; box-sizing:border-box; display:flex; gap:9px; text-align:left; padding:9px; color:var(--fg); background:var(--panel2); border:1px solid var(--line); border-radius:9px; font:inherit; }
button.cw-material { cursor:pointer; }
button.cw-material:hover { border-color:var(--accent); }
.cw-material[aria-current="true"] { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset; background:var(--panel); }
.cw-material[aria-disabled="true"] { opacity:.56; }
.cw-material-mark { color:var(--muted); font-weight:700; }
.cw-material-done .cw-material-mark { color:var(--ok); }
.cw-material-current .cw-material-mark { color:var(--accent); }
.cw-material-copy { min-width:0; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:2px 8px; align-items:baseline; flex:1; }
.cw-material-copy strong { font-size:.82rem; }
.cw-material-state { color:var(--muted); font-size:.7rem; }
.cw-material-copy small { grid-column:1 / -1; color:var(--muted); font-size:.68rem; line-height:1.35; }
.cw-technical { margin-top:14px; }
.cw-technical > summary, .cw-full-progress > summary, .cw-ai-help > summary, .cw-editor-meta > summary { cursor:pointer; font-weight:700; }
.cw-technical > p { margin:.4rem 0 .7rem; }
.cw-eligibility { margin-top:.6rem; border-left:3px solid var(--warn, var(--accent)); padding-left:12px; }
.cw-eligibility-head { display: flex; align-items: baseline; justify-content: space-between; gap: .8rem; flex-wrap: wrap; }
.cw-eligibility h2 { margin: 0; font-size: 1rem; }
.cw-eligibility-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .5rem; margin: .6rem 0 .45rem; }
.cw-eligibility-item { display: flex; justify-content: space-between; gap: .5rem; padding: .45rem .6rem; background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; font-family: var(--mono); font-size: .78rem; }
.cw-eligibility-item span { color: var(--accent); font-weight: 700; }
.cw-eligibility p { margin: 0; font-size: .78rem; line-height: 1.4; }
@media (max-width: 820px) {
  .cw-layout { grid-template-columns:1fr; }
  .cw-materials { position:static; }
  .cw-material-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
}
@media (max-width: 520px) {
  .cw-material-list { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .cw-material { padding:8px; }
  .cw-material-copy { grid-template-columns:1fr; gap:2px; }
  .cw-material-copy small { display:none; }
  .cw-material-state { font-size:.66rem; }
  .cw-eligibility-grid { grid-template-columns:1fr; }
}

/* -------------------------------------------------------------- rail (A) */
.cw-full-progress { margin-top:12px; padding:10px; }
.cw-full-progress > p { margin:.45rem 0 .75rem; }
.cw-rail { display:grid; grid-template-columns:repeat(7,minmax(130px,1fr)); gap:8px; overflow-x:auto; padding-bottom:4px; }
.cw-stage {
  min-width: 130px; position: relative;
  padding: .6rem .8rem .7rem; border: 1px solid var(--line); border-radius: 10px;
  background: var(--panel2); display: flex;
  flex-direction: column; gap: .3rem;
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
#cw-workbench { min-width:0; }
.cw-editor { display: block; }
.cw-eh { display:flex; align-items:flex-start; justify-content:space-between; gap:1rem; flex-wrap:wrap; }
.cw-eh h2 { font-size: 1.1rem; margin: 0; }
.cw-evidence { margin:.25rem 0 .55rem; color:var(--muted); font-size:.78rem; line-height:1.4; }
.cw-ai-help { margin-top:.7rem; border-top:1px solid var(--line); padding-top:.65rem; }
.cw-ai-help > summary { font-size:.8rem; }
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
.cw-save-state { color:var(--ok); font-size:.76rem; font-weight:700; }
.cw-save-state.is-dirty { color:#e0af68; }
.cw-editor-meta { margin-top:.55rem; color:var(--muted); font-size:.76rem; }
.cw-editor-meta p { margin:.4rem 0 0; line-height:1.45; }

.cw-special { margin-top: 1rem; }
.cw-special h2 { font-size: 1.1rem; margin: 0 0 .4rem; }
.cw-btnrow { display: flex; gap: .6rem; flex-wrap: wrap; margin-top: .5rem; }
.cw-props { list-style: none; margin: .5rem 0; padding: 0; display: flex; flex-direction: column; gap: .35rem; }
.cw-prop { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; font-size: .84rem; }
.cw-pid { font-family: var(--mono); }
.cw-pchip { font-size: .72rem; padding: .1rem .5rem; border-radius: 999px; font-weight: 600; }
.cw-p-proposed { background: #1c2a44; color: var(--accent); }
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


  function hasUnsavedWork() {
    return Array.prototype.some.call(document.querySelectorAll(".cw-text"), function (ta) {
      return isDirty(ta) || ta.getAttribute("data-saving") === "true";
    });
  }
  function reloadSoon() {
    setTimeout(function () {
      // Check at execution time too: typing can resume during this delay,
      // or another (hidden) story stage may have an unsaved buffer.
      if (!hasUnsavedWork()) location.reload();
    }, 500);
  }

  // -------- one editor visible at a time; ✎编辑 swaps stages in ---------
  function showStage(stage) {
    var current = document.querySelector(".cw-editor:not(.hidden) .cw-text");
    if (current && isDirty(current) && current.getAttribute("data-stage") !== stage) {
      if (!confirm("当前内容尚未保存，仍要切换吗？")) return;
    }
    var editors = document.querySelectorAll(".cw-editor");
    var found = false;
    for (var i = 0; i < editors.length; i++) {
      var match = editors[i].getAttribute("data-stage") === stage;
      /* the server marks inactive editors with the hidden CLASS — the swap
       * must clear that class on the target, not only flip the attribute
       * (the attribute alone never unhid anything; GUI polish wave). */
      editors[i].classList.toggle("hidden", !match);
      editors[i].hidden = !match;
      if (match) found = true;
    }
    var special = document.querySelector(".cw-special");
    if (special) special.hidden = found;
    var materials = document.querySelectorAll("[data-source-stage]");
    for (var j = 0; j < materials.length; j++) {
      var active = materials[j].getAttribute("data-source-stage") === stage;
      if (active) materials[j].setAttribute("aria-current", "true");
      else materials[j].removeAttribute("aria-current");
    }
    if (found) {
      var ta = document.querySelector('.cw-editor[data-stage="' + cssq(stage) + '"] .cw-text');
      if (ta) ta.focus();
    }
  }
  function cssq(s) { return String(s).replace(/["\\]/g, "\\$&"); }

  function isDirty(ta) { return !!ta && ta.value !== ta.defaultValue; }
  function updateDirty(ta) {
    if (!ta) return;
    var section = ta.closest(".cw-editor");
    var state = section && section.querySelector(".cw-save-state");
    var dirty = isDirty(ta);
    if (state) {
      state.textContent = dirty ? "未保存" : "已保存";
      state.classList.toggle("is-dirty", dirty);
    }
  }

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
    }).catch(function () {
      bodyEl.textContent = "无法连接本地服务，技能内容没有加载。";
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
    var btn = document.querySelector('[data-act="save"][data-stage="' + cssq(stage) + '"]');
    if (btn && btn.disabled) return;
    if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
    var submitted = ta.value;
    ta.setAttribute("data-saving", "true");
    function finishSave() {
      ta.removeAttribute("data-saving");
      if (btn) { btn.disabled = false; btn.textContent = "保存并检查进度"; }
    }
    post("/api/create/save", {
      stage: stage, text: submitted, expected_rev: ta.getAttribute("data-rev")
    }).then(function (res) {
      finishSave();
      if (res.status === 200) {
        // Only the submitted snapshot was saved, NOT whatever the user typed
        // while the request was in flight. Keep newer input dirty and visible.
        ta.defaultValue = submitted;
        if (res.data && typeof res.data.rev === "string") ta.setAttribute("data-rev", res.data.rev);
        updateDirty(ta);
        toast(hasUnsavedWork() ? "已保存提交版本；后续草稿仍未保存。" : "已保存并重新检查进度", true);
        reloadSoon();
      } else {
        var conflict = res.status === 409 && res.data && typeof res.data.current === "string";
        toast(conflict ? "文件已在其他入口修改，当前草稿未被覆盖。请先复制草稿，再刷新合并。"
          : (res.data && res.data.error) || "保存失败", false);
      }
    }).catch(function () {
      finishSave();
      toast("无法连接本地服务；内容仍保留在编辑器中。", false);
    });
  }
  function doScaffold(stage) {
    var ta = document.querySelector('.cw-editor[data-stage="' + cssq(stage) + '"] .cw-text');
    if (ta && isDirty(ta) && ta.value.trim()) {
      toast("编辑器里有未保存内容；先保存当前草稿，再决定是否需要模板。", false);
      ta.focus();
      return;
    }
    var btn = document.querySelector('[data-act="scaffold"][data-stage="' + cssq(stage) + '"]');
    if (btn && btn.disabled) return;
    var original = btn ? btn.textContent : "生成模板";
    if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
    post("/api/create/scaffold", { stage: stage }).then(function (res) {
      if (res.status === 200) { toast("已生成模板 " + stage + " — 编辑它填内容", true); reloadSoon(); }
      else {
        if (btn) { btn.disabled = false; btn.textContent = original; }
        toast((res.data && res.data.error) || "生成模板失败", false);
      }
    }).catch(function () {
      if (btn) { btn.disabled = false; btn.textContent = original; }
      toast("无法连接本地服务；项目没有被修改。", false);
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
  document.addEventListener("input", function (ev) {
    if (ev.target && ev.target.classList && ev.target.classList.contains("cw-text")) {
      updateDirty(ev.target);
    }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeModal();
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") {
      var ta = document.querySelector(".cw-editor:not(.hidden) .cw-text");
      if (ta) {
        ev.preventDefault();
        doSave(ta.getAttribute("data-stage"));
      }
    }
  });
  window.addEventListener("beforeunload", function (ev) {
    var textareas = document.querySelectorAll(".cw-text");
    for (var i = 0; i < textareas.length; i++) {
      if (isDirty(textareas[i])) {
        ev.preventDefault();
        ev.returnValue = "";
        return "";
      }
    }
  });
})();
"""
