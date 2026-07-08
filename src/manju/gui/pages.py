"""Server-rendered workbench pages (round S, S8b — goal items 1/6/8/9/11 GUI).

The `manju gui` SPA (:mod:`manju.gui.page`) is one JS-driven document. These are
its siblings: six *server-rendered* routes that complete the workbench-level
capability matrix (docs/WORKBENCH.md) — the human review flow, the finals diff,
the asset library, the provider board, the routing view and the doctor page.

Each page is a full HTML document produced by a pure render function: the real
content is baked into the DOM server-side (so a plain GET carries the fixture
content, no second round-trip needed), and ``/pages.js`` layers on the keyboard
review loop, the synced compare players, the provider toggles and the strategy
picker. Every mutation goes through the SAME engine core the CLI calls and lands
the SAME event in ``events.jsonl`` — the pages add no state of their own.

Design stance (mirrors :mod:`manju.gui.page`):

  * CSP-safe by construction — CSS/JS in external files (``/app.css`` +
    ``/pages.css``, ``/pages.js``), no inline handlers, no inline ``style=``
    (dynamic geometry goes through the CSSOM); every mutating request carries
    the ``X-Manju-Token`` header read from the ``manju-token`` meta tag;
  * XSS-safe by construction — ALL server-supplied text (dialogue, notes, QC
    messages, provider ids, git-adjacent text) is ``html.escape``-d here; the
    script never assigns ``innerHTML`` and adds dynamic text via ``textContent``;
  * secrets never rendered — the providers page shows the key env-var NAME and
    whether it is set, never a value, and never dumps a manifest body;
  * degrade section-by-section — a failing core call renders one muted line in
    its own block and the rest of the page still serves 200.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

from .glossary import tooltip_html

__all__ = [
    "PAGE_PATHS",
    "render",
    "render_pages_css",
    "render_pages_js",
    "nav_html",
    "chrome",
    "body_class",
    "PRO_ONLY_PAGES",
    "GLOSSARY_HEAD",
    "set_disabled_in_text",
    "set_strategy_in_text",
    "STRATEGY_NAME_RE",
]

# The six routes this module owns. server.py delegates GET here for these.
PAGE_PATHS = frozenset(
    {"/review", "/compare", "/library", "/providers", "/routing", "/doctor"}
)

# Strategy names are simple identifiers (built-ins + user-defined keys); the
# picker only writes one of these into routing.yaml, unquoted, so guard it.
STRATEGY_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

_NAV = (
    ("/", "工作台"),
    ("/create", "创作"),
    ("/storyboard", "分镜"),
    ("/director", "导演"),
    ("/lab", "镜头实验室"),
    ("/ingest", "批量入库"),
    ("/edit", "剪辑"),
    ("/review", "审片"),
    ("/subtitles", "字幕"),
    ("/mixer", "混音"),
    ("/packaging", "打包"),
    ("/exports", "导出中心"),
    ("/compare", "对比"),
    ("/library", "素材库"),
    ("/providers", "服务商"),
    ("/routing", "路由"),
    ("/doctor", "体检"),
)

# round U (§8): the pro-only pages the 新手 nav omits — providers, routing,
# doctor, compare. Every one stays reachable by URL (the render never 403s on
# mode; mode only shapes the nav + panel visibility), so hiding is loss-free.
PRO_ONLY_PAGES = frozenset({"/compare", "/providers", "/routing", "/doctor"})

# Head stanza every workbench surface shares so the glossary tooltip look + the
# mode/terms chrome behave identically on the SPA and all server-rendered pages.
GLOSSARY_HEAD = (
    '<link rel="stylesheet" href="/glossary.css">\n'
    '<script src="/glossary.js" defer></script>\n'
)


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def body_class(mode: str, show_terms: bool) -> str:
    """The ``<body>`` class server-side render stamps so CSS can shape the view:
    ``mj-mode-beginner``/``mj-mode-pro`` gate the pro-only panels, ``mj-show-terms``
    reveals the greyed English originals (§10 显示专业术语)."""
    parts = ["mj-mode-beginner" if mode == "beginner" else "mj-mode-pro"]
    if show_terms:
        parts.append("mj-show-terms")
    return " ".join(parts)


def chrome(active: str) -> tuple[str, str]:
    """Resolve the per-user view mode + glossary toggle (server-side) and return
    ``(nav_html, body_class)`` for a page shell. Central so the SPA and every
    server-rendered page share one mode-aware nav and one body class."""
    from .userstate import is_mode_hint_dismissed, is_show_pro_terms, resolve_mode

    mode = resolve_mode()
    show = is_show_pro_terms()
    nav = nav_html(active, mode=mode, show_terms=show,
                   hint_dismissed=is_mode_hint_dismissed())
    return nav, body_class(mode, show)


def _mode_controls(mode: str, show_terms: bool) -> str:
    """The 新手/专业 switch + 显示专业术语 toggle, pinned to the right of the nav.
    Buttons carry ``data-mode`` for /glossary.js (CSP-safe — no inline handler)."""

    def btn(m: str, label: str) -> str:
        on = mode == m
        return (f'<button type="button" class="mj-mode-btn{" on" if on else ""}" '
                f'data-mode="{m}" aria-pressed="{"true" if on else "false"}">'
                f"{_e(label)}</button>")

    checked = " checked" if show_terms else ""
    return (
        '<span class="mj-nav-ctl">'
        '<span class="mj-modesw" role="group" aria-label="视图模式 (view mode)">'
        + btn("beginner", "新手") + btn("pro", "专业")
        + "</span>"
        '<label class="mj-terms-toggle" '
        'title="在每个中文词旁显示英文原词 (show the engineering term beside it)">'
        f'<input type="checkbox" id="mj-terms-toggle"{checked}> 显示专业术语</label>'
        "</span>"
    )


def _mode_hint() -> str:
    """The fresh-user 新手 one-liner (dismissable via /glossary.js)."""
    return (
        '<div id="mj-mode-hint" class="mj-mode-hint">'
        "<span>当前是<b>新手模式</b>:只留常用面板,进阶功能(服务商/路由/体检/对比、"
        "转场调色等)已收起。随时点右上角<b>专业</b>切回全部功能 — 不会丢任何内容。</span>"
        '<button type="button" id="mj-mode-hint-x" aria-label="知道了 (dismiss)">'
        "知道了 ✕</button></div>"
    )


def nav_html(active: str, mode: str = "pro", show_terms: bool = False,
             hint_dismissed: bool = True) -> str:
    """The shared top nav (also injected into the SPA skeleton for discovery).

    In 新手 mode the pro-only page links (:data:`PRO_ONLY_PAGES`) are omitted and
    a dismissable hint bar follows the nav; the pages themselves stay reachable by
    URL. The mode switch + 显示专业术语 toggle sit at the right on every surface."""
    beginner = mode == "beginner"
    out = ['<nav class="pnav">']
    for href, label in _NAV:
        if beginner and href in PRO_ONLY_PAGES:
            continue
        cls = "active" if href == active else ""
        out.append(f'<a class="{cls}" href="{href}">{_e(label)}</a>')
    out.append(_mode_controls(mode, show_terms))
    out.append("</nav>")
    if beginner and not hint_dismissed:
        out.append(_mode_hint())
    return "".join(out)


def _shell(title: str, token: str, active: str, body: str) -> str:
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
        + GLOSSARY_HEAD
        + '<script src="/pages.js" defer></script>\n'
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
    """Dispatch a page path to its renderer. ``query`` is the parsed query dict."""

    def one(key: str) -> str | None:
        v = query.get(key)
        return v[0] if v else None

    if path == "/review":
        return render_review(project, token)
    if path == "/compare":
        return render_compare(project, token, one("a"), one("b"))
    if path == "/library":
        return render_library(project, token, one("tag"), one("kind"))
    if path == "/providers":
        return render_providers(project, token)
    if path == "/routing":
        return render_routing(project, token)
    if path == "/doctor":
        return render_doctor(project, token)
    raise KeyError(path)


# ============================================================ shared bits


def _take_media(project: Any, take: Any) -> tuple[str | None, str, str | None]:
    """(playable_url, ext, thumb_url) for a take, routed through the same lazy
    preview transcode the SPA uses. thumb is None for audio-only takes."""
    if take.media_path is None:
        return None, "", None
    from .state import playable_url

    url, ext = playable_url(project, take.media_path)
    thumb = None
    if take.media_path.suffix.lower() not in (".wav", ".mp3", ".m4a", ".flac"):
        thumb = "/thumb/" + quote(project.relpath(take.media_path), safe="/")
    return url, ext, thumb


def _player(url: str | None, ext: str, poster: str | None = None,
            cls: str = "") -> str:
    """A <video> or <img> element for a media url, degrading to a muted note."""
    if not url:
        return '<p class="muted">无媒体 (no media)</p>'
    if ext in ("png", "jpg", "jpeg", "webp", "gif"):
        return f'<img class="{cls}" src="{_e(url)}" alt="">'
    p = f' poster="{_e(poster)}"' if poster else ""
    return f'<video class="{cls}" src="{_e(url)}"{p} controls preload="metadata"></video>'


# ============================================================ 审片 review


def _qc_by_shot(project: Any) -> tuple[dict[str, list[dict]], str | None]:
    """qc.json findings grouped by subject (shot id), plus a read error note."""
    from ..core.yamlio import read_json

    path = project.reports_dir / "qc.json"
    if not path.exists():
        return {}, None
    try:
        data = read_json(path)
    except Exception:
        return {}, "reports/qc.json 读取失败 (unreadable)"
    grouped: dict[str, list[dict]] = {}
    for it in (data.get("items") or []) if isinstance(data, dict) else []:
        if not isinstance(it, dict):
            continue
        grouped.setdefault(str(it.get("subject") or ""), []).append(it)
    return grouped, None


_REPAIR_OPS = (
    ("retime", "变速 0.9×", {"data-op": "retime", "data-factor": "0.9"}),
    ("extend", "延长 +500ms", {"data-op": "extend", "data-ms": "500", "data-mode": "freeze"}),
    ("trim", "裁尾 -300ms", {"data-op": "trim", "data-ms": "300"}),
    ("croppad", "改画幅", {"data-op": "croppad", "data-mode": "center_crop"}),
)


def render_review(project: Any, token: str) -> str:
    shots = project.shot_ids()
    qc_by_shot, qc_err = _qc_by_shot(project)
    reviewed = 0
    cards: list[str] = []

    for idx, sid in enumerate(shots):
        try:
            shot = project.load_shot(sid)
        except Exception:
            shot = None
        selected = shot.status.selected_take if shot else None
        take_notes = dict(shot.status.take_notes) if shot else {}
        if take_notes:
            reviewed += 1
        try:
            takes = project.takes(sid)
        except Exception:
            takes = []
        sel = next((t for t in takes if t.name == selected), None)
        alts = [t for t in takes if t.name != selected]

        # large player of the selected take, poster = QC mid-frame when present
        frame = project.reports_dir / "frames" / f"{sid}.jpg"
        frame_url = None
        if frame.exists():
            frame_url = "/media/" + quote(project.relpath(frame), safe="/")
        if sel is not None:
            url, ext, thumb = _take_media(project, sel)
            player = _player(url, ext, frame_url, cls="rv-video")
        else:
            player = '<p class="muted">尚未选用 take (no take selected)</p>'

        # QC findings for this shot
        findings = qc_by_shot.get(sid, [])
        if findings:
            rows = []
            for f in findings:
                lvl = str(f.get("level") or "info")
                msg = _e(f.get("message"))
                sug = f.get("suggestion")
                sug_html = f'<div class="rv-qc-sug muted">↳ {_e(sug)}</div>' if sug else ""
                rows.append(
                    f'<li class="rv-qc-item lvl-{_e(lvl)}"><span class="badge st-{_qc_badge(lvl)}">'
                    f"{_e(lvl)}</span> {msg}{sug_html}</li>"
                )
            qc_html = '<ul class="rv-qc">' + "".join(rows) + "</ul>"
        else:
            qc_html = '<p class="muted rv-qc">QC 无此镜发现 (no findings)</p>'

        frame_html = (
            f'<div class="rv-frame"><span class="muted">QC 抽帧</span>'
            f'<img src="{_e(frame_url)}" alt=""></div>'
            if frame_url else ""
        )

        # prior takes as small alternates
        alt_html = ""
        if alts:
            items = []
            for t in alts:
                _u, _x, thumb = _take_media(project, t)
                note = take_notes.get(t.name)
                note_html = f'<span class="rv-alt-note" title="{_e(note)}">📝</span>' if note else ""
                thumb_img = (f'<img src="{_e(thumb)}" alt="">' if thumb
                             else '<div class="rv-alt-noimg"></div>')
                items.append(
                    f'<div class="rv-alt">{thumb_img}'
                    f'<button class="btn ghost mini" data-act="select" '
                    f'data-take="{_e(t.name)}">换用 {_e(t.name)}</button>{note_html}</div>'
                )
            alt_html = ('<div class="rv-alts"><span class="muted">其它 take</span>'
                        '<div class="rv-alts-row">' + "".join(items) + "</div></div>")

        repair_btns = "".join(
            f'<button class="btn ghost mini" data-act="repair" '
            + " ".join(f'{k}="{_e(v)}"' for k, v in attrs.items())
            + f">{_e(label)}</button>"
            for _op, label, attrs in _REPAIR_OPS
        )

        note_val = _e(take_notes.get(selected)) if selected else ""
        state_badge = (f'<span class="badge st-{_state_badge(shot.status)}">'
                       f'{_e(_state_word(shot))}</span>' if shot else "")
        action = _e(shot.action.main) if shot else ""
        dialogue = _e(shot.dialogue.text) if shot else ""
        reviewed_attr = "1" if take_notes else "0"

        cards.append(
            f'<section class="rv-shot panel" id="rv-{_e(sid)}" data-shot="{_e(sid)}" '
            f'data-take="{_e(selected or "")}" data-reviewed="{reviewed_attr}">\n'
            f'  <div class="rv-head"><h2>{_e(sid)} {state_badge}'
            f'<span class="rv-idx muted">#{idx + 1}</span></h2>'
            f'<div class="rv-meta muted">{action}{" · 台词:" + dialogue if dialogue else ""}</div></div>\n'
            f'  <div class="rv-body">\n'
            f'    <div class="rv-player">{player}</div>\n'
            f'    <div class="rv-side">{qc_html}{frame_html}{alt_html}</div>\n'
            f"  </div>\n"
            f'  <div class="rv-actions btnrow">\n'
            f'    <button class="btn" data-act="good" title="快捷键 g">好</button>\n'
            f'    <button class="btn ghost" data-act="reject" title="快捷键 x">弃</button>\n'
            f'    <button class="btn ghost" data-act="redo">重做</button>\n'
            f'    <span class="rv-repair">修:{repair_btns}</span>\n'
            f'    <button class="btn ghost" data-act="route">路由?</button>\n'
            f'    <button class="btn ghost" data-act="skip" title="快捷键 j">跳过</button>\n'
            f"  </div>\n"
            f'  <div class="rv-noterow">'
            f'<input class="rv-note-input" placeholder="备注 / 判词 (note)" value="{note_val}">'
            f'<button class="btn ghost mini" data-act="note">保存备注</button></div>\n'
            f'  <div class="rv-explain muted" hidden></div>\n'
            f"</section>"
        )

    total = len(shots)
    err_line = f'<p class="err">{_e(qc_err)}</p>' if qc_err else ""
    if not shots:
        cards_html = '<p class="muted panel">项目还没有镜头 (no shots to review)。</p>'
    else:
        cards_html = "\n".join(cards)
    pct = (reviewed * 100 // total) if total else 0

    body = (
        '<div class="page-h"><h1>审片 Review</h1>'
        '<span class="muted">逐条审阅每个' + tooltip_html("shot") + '选用的'
        + tooltip_html("take") + ',看 ' + tooltip_html("QC")
        + ' · 键盘 j/k 上下 · g 通过 · x 退回 · 空格 播放/暂停</span></div>\n'
        + err_line
        + '<div class="rv-progress panel">'
        f'<span id="rv-progress">已审 {reviewed} / {total}</span>'
        '<span class="bar rv-bar"><span class="bar-fill" id="rv-progress-fill" '
        f'data-pct="{pct}"></span></span>'
        "</div>\n"
        + cards_html
        + _consistency_section(project)
    )
    return _shell("审片", token, "/review", body)


# --------------------------------------------- 跨镜一致性 consistency (round X)

# criterion codes worth offering in the verdict form's dropdown — the A–J
# codes visual-qc-review actually asks a consistency judge to apply (identity/
# outfit §A/B, scene/lighting continuity §C/D); the full A–J list stays in the
# skill, this is just a shortcut for the common cases.
_CS_CRITERIA = (
    "A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4",
    "C1", "C2", "C3", "C4", "C5", "D1", "D2", "D3", "D4",
)

_CS_STATE_LABEL = {"reviewed": "已判读", "stale": "已过期", "never": "未判读"}
_CS_STATE_CLASS = {"reviewed": "st-fresh", "stale": "st-stale", "never": "st-missing"}


def _consistency_section(project: Any) -> str:
    """The 跨镜一致性 Consistency section of /review (round X, agent XB — user
    pain #2: visual-consistency QC judged shots in isolation). Renders the
    same comparison units ``qc brief --mode consistency`` computes: a contact
    sheet per unit, its member shots, the criteria hint, coverage chip, and a
    verdict FORM a human can file directly (POSTs into the same intake agents
    use, actor=human). Degrades to one muted line on any failure — a broken
    matrix/board never breaks the review page."""
    try:
        from ..qc.agent_review import qc_brief as _qc_brief

        brief = _qc_brief(project, mode="consistency")
    except Exception as exc:
        return (
            '<div class="page-h"><h2>跨镜一致性 Consistency</h2></div>\n'
            f'<p class="err panel">一致性组合加载失败:{_e(exc)}</p>'
        )

    units = brief.get("units") or []
    coverage = ((brief.get("coverage") or {}).get("units")) or {}
    skipped = brief.get("skipped") or []

    if not units:
        body = ('<p class="muted panel">暂无可判读的一致性组合(需要至少两个共享角色/'
                '场景、且已选 take 的镜头)。</p>')
    else:
        body = "\n".join(_consistency_card(u, coverage.get(u["unit"], {})) for u in units)

    skip_html = ""
    if skipped:
        items = "".join(
            f'<li>{_e(s.get("unit", ""))}:{_e(s.get("reason", ""))}</li>' for s in skipped
        )
        skip_html = (f'<details class="muted cs-skipped"><summary>跳过 {len(skipped)} '
                    f'个组合</summary><ul>{items}</ul></details>')

    return (
        '<div class="page-h"><h2>跨镜一致性 Consistency</h2>'
        '<span class="muted">角色出场对照表 · 相邻镜头场景对比 · 场景整体看板 — '
        '一致性是跨镜属性,不逐镜孤立判读;人工可在此现场提交裁决</span></div>\n'
        + body + skip_html
    )


def _consistency_card(unit: dict, cov: dict) -> str:
    img = unit.get("image")
    img_html = (f'<img class="cs-board" src="/media/{quote(str(img), safe="/")}" alt="">'
               if img else '<p class="muted">看板尚未生成(需要 ffmpeg)</p>')
    members = ", ".join(m["shot"] for m in (unit.get("members") or []))
    state = cov.get("state", "never")
    state_label = _CS_STATE_LABEL.get(state, state)
    state_cls = _CS_STATE_CLASS.get(state, "st-missing")
    criteria = unit.get("criteria") or {}
    crit_opts = "".join(f'<option value="{_e(c)}">{_e(c)}</option>' for c in _CS_CRITERIA)

    return (
        f'<section class="panel cs-unit" data-unit="{_e(unit["unit"])}">'
        f'  <div class="cs-head"><b>[{_e(unit.get("kind"))}] {_e(unit.get("label"))}</b>'
        f'  <span class="badge {state_cls} cs-state">{_e(state_label)}</span></div>'
        f'  <div class="muted">成员镜头:{_e(members)}</div>'
        f'  <div class="muted">判据 {_e(criteria.get("sections"))}:{_e(criteria.get("note"))}</div>'
        f'  {img_html}'
        f'  <div class="cs-form btnrow">'
        f'    <select class="cs-criterion">{crit_opts}</select>'
        f'    <select class="cs-level">'
        f'      <option value="fyi">fyi</option>'
        f'      <option value="issue">issue</option>'
        f'      <option value="blocker">blocker</option>'
        f'    </select>'
        f'    <input class="cs-message" placeholder="中文结论(必填)">'
        f'    <button type="button" class="btn ghost mini" data-act="cs-submit">提交裁决</button>'
        f'  </div>'
        f"</section>"
    )


def _qc_badge(level: str) -> str:
    return {"error": "needs", "warn": "stale"}.get(level, "missing")


def _state_word(shot: Any) -> str:
    return "已选用" if shot.status.selected_take else "待选用"


def _state_badge(status: Any) -> str:
    return "fresh" if status.selected_take else "needs"


# ============================================================ 对比 compare


def _final_versions(project: Any) -> list[str]:
    names = []
    for p in sorted(project.final_dir.glob("final_v*.mp4")):
        m = re.fullmatch(r"final_v(\d+)", p.stem)
        if m:
            names.append((int(m.group(1)), p.stem))
    return [n for _v, n in sorted(names)]


def _final_url(project: Any, name: str) -> str:
    return "/media/" + quote(project.relpath(project.final_dir / f"{name}.mp4"), safe="/")


def _ms(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) / 1000:.2f}s"
    except (TypeError, ValueError):
        return _e(v)


_CHANGE_LABEL = {
    "unchanged": ("未变", "unchanged"),
    "take_changed": ("换 take", "take_changed"),
    "duration_changed": ("时长变", "duration_changed"),
    "moved": ("移位", "moved"),
    "added": ("新增", "added"),
    "removed": ("删除", "removed"),
    "captions_changed": ("字幕变", "captions_changed"),
    "audio_changed": ("音频变", "audio_changed"),
}


def render_compare(project: Any, token: str, a: str | None, b: str | None) -> str:
    from ..build.compare import CompareError, compare_finals

    versions = _final_versions(project)
    head = ('<div class="page-h"><h1>对比 Compare</h1>'
            '<span class="muted">成片版本差异 · 逐' + tooltip_html("shot")
            + '看换了哪条' + tooltip_html("take")
            + ' · GitHub PR files 式</span></div>')

    if len(versions) < 2:
        body = (
            head
            + '<p class="muted panel">需要至少两个 final 才能对比 '
            "(build again to mint another)。当前:"
            + _e(", ".join(versions) or "无")
            + "</p>"
        )
        return _shell("对比", token, "/compare", body)

    # dropdowns default to the latest two
    da, db = versions[-2], versions[-1]
    sel_a = a if a in versions else da
    sel_b = b if b in versions else db

    def opts(selected: str) -> str:
        return "".join(
            f'<option value="{_e(v)}"{" selected" if v == selected else ""}>{_e(v)}</option>'
            for v in versions
        )

    picker = (
        '<div class="cmp-picker panel"><label>A '
        f'<select id="cmp-a-sel">{opts(sel_a)}</select></label>'
        '<label>B '
        f'<select id="cmp-b-sel">{opts(sel_b)}</select></label>'
        '<label class="cmp-synclbl"><input type="checkbox" id="cmp-sync" checked> 同步播放</label></div>'
    )

    try:
        diff = compare_finals(project, sel_a, sel_b)
    except CompareError as exc:
        body = head + picker + f'<p class="err panel">{_e(exc)}</p>'
        return _shell("对比", token, "/compare", body)

    # side-by-side players
    players = (
        '<div class="cmp-players">'
        f'<div class="cmp-side"><h3>A · {_e(sel_a)}</h3>'
        f'<video id="cmp-vid-a" class="cmp-video" src="{_e(_final_url(project, sel_a))}" '
        'controls preload="metadata"></video></div>'
        f'<div class="cmp-side"><h3>B · {_e(sel_b)}</h3>'
        f'<video id="cmp-vid-b" class="cmp-video" src="{_e(_final_url(project, sel_b))}" '
        'controls preload="metadata"></video></div>'
        "</div>"
    )

    # summary strip
    s = diff.get("summary") or {}
    summary = (
        '<div class="cmp-summary panel"><h2>概览 Summary</h2><div class="cmp-strip">'
        + _delta_chip("时长 A", _ms(s.get("duration_a_ms")))
        + _delta_chip("时长 B", _ms(s.get("duration_b_ms")))
        + _delta_chip("时长差", _ms(s.get("duration_delta_ms")),
                      bad=bool(s.get("duration_delta_ms")))
        + _delta_chip("分辨率", f'{_e(s.get("resolution_a"))} → {_e(s.get("resolution_b"))}',
                      bad=bool(s.get("resolution_changed")))
        + _delta_chip("fps", f'{_e(s.get("fps_a"))} → {_e(s.get("fps_b"))}',
                      bad=bool(s.get("fps_changed")))
        + "</div></div>"
    )

    if diff.get("degraded"):
        note = (
            '<div class="cmp-degraded panel"><span class="badge st-stale">degraded</span> '
            + _e(diff.get("note") or "per-shot detail unavailable (pre-S final)")
            + "</div>"
        )
        body = head + picker + note + summary + players
        return _shell("对比", token, "/compare", body)

    # per-shot change strip (Frame.io / PR-files: unchanged dimmed)
    rows = []
    for c in diff.get("changes", []):
        change = str(c.get("change", ""))
        label, klass = _CHANGE_LABEL.get(change, (change.replace("_", " "), change))
        a_side, b_side = c.get("a") or {}, c.get("b") or {}
        why = c.get("why")
        why_html = f'<div class="cmp-why muted">↳ {_e(why)}</div>' if why else ""
        dim = " dim" if change == "unchanged" else ""
        rows.append(
            f'<tr class="cmp-row chg-{_e(klass)}{dim}">'
            f'<td class="cmp-shot">{_e(c.get("shot"))}</td>'
            f'<td><span class="badge chgbadge chg-{_e(klass)}">{_e(label)}</span></td>'
            f"<td>{_side_cell(a_side)}</td><td>{_side_cell(b_side)}</td>"
            f"<td>{why_html}</td></tr>"
        )
    strip = (
        '<div class="cmp-changes panel"><h2>逐镜变化 Per-shot changes</h2>'
        '<div class="tablewrap"><table class="cmp-table"><thead><tr>'
        "<th>镜头</th><th>变化</th><th>A</th><th>B</th><th>原因 why</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div></div>"
    )

    # captions cue diff
    caps = diff.get("captions") or {}
    caps_html = ""
    if caps.get("changed"):
        crows = []
        for cue in caps.get("cues", []):
            ca, cb = cue.get("a") or {}, cue.get("b") or {}
            crows.append(
                f'<tr><td>{_e(cue.get("index"))}</td>'
                f'<td><span class="badge chg-{_e(cue.get("change"))}">{_e(cue.get("change"))}</span></td>'
                f'<td>{_e(ca.get("text"))}</td><td>{_e(cb.get("text"))}</td></tr>'
            )
        caps_html = (
            '<div class="cmp-caps panel"><h2>字幕 Captions '
            f'({_e(caps.get("a_count"))} → {_e(caps.get("b_count"))})</h2>'
            '<div class="tablewrap"><table class="cmp-table"><thead><tr>'
            "<th>#</th><th>变化</th><th>A</th><th>B</th></tr></thead><tbody>"
            + "".join(crows) + "</tbody></table></div></div>"
        )

    # audio + packaging tables
    audio_html = _change_block(
        "音频 Audio", diff.get("audio", {}).get("tracks", []),
        lambda t: (t.get("track"),
                   f'{(t.get("a") or {}).get("count")} clip',
                   f'{(t.get("b") or {}).get("count")} clip'))
    pkg_html = _change_block(
        "封装 Packaging", diff.get("packaging", {}).get("items", []),
        lambda it: (f'{it.get("item")} ({it.get("change")})',
                    _e(it.get("a")), _e(it.get("b"))))

    body = (head + picker + summary + strip + caps_html + audio_html + pkg_html
            + players)
    return _shell("对比", token, "/compare", body)


def _delta_chip(label: str, value: str, *, bad: bool = False) -> str:
    cls = "cmp-chip bad" if bad else "cmp-chip"
    return f'<span class="{cls}"><span class="muted">{_e(label)}</span> {_e(value)}</span>'


def _side_cell(side: dict) -> str:
    if not side:
        return '<span class="muted">—</span>'
    take = side.get("take")
    prov = side.get("provider")
    dur = side.get("duration_ms")
    bits = []
    if take:
        bits.append(_e(take))
    if prov:
        bits.append(f'<span class="muted">{_e(prov)}</span>')
    if dur is not None:
        bits.append(f'<span class="muted">{_ms(dur)}</span>')
    return " ".join(bits) or '<span class="muted">—</span>'


def _change_block(title: str, items: list, fmt) -> str:
    if not items:
        return ""
    rows = []
    for it in items:
        name, a, b = fmt(it)
        rows.append(f"<tr><td>{_e(name)}</td><td>{a}</td><td>{b}</td></tr>")
    return (
        f'<div class="panel"><h2>{_e(title)}</h2>'
        '<div class="tablewrap"><table class="cmp-table"><thead><tr>'
        "<th>项</th><th>A</th><th>B</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div></div>"
    )


# ============================================================ 素材库 library


def _lib_kinds() -> tuple[str, ...]:
    return ("video", "image", "audio", "other")


def render_library(project: Any, token: str, tag: str | None, kind: str | None) -> str:
    from ..core.library import Library, LibraryError, _hex

    head = ('<div class="page-h"><h1>素材库 Library</h1>'
            '<span class="muted">个人素材(~/.manju/library)· 内容寻址去重</span></div>')
    lib = Library()
    try:
        all_assets = lib.assets()
    except LibraryError as exc:
        return _shell("素材库", token, "/library",
                      head + f'<p class="err panel">{_e(exc)}</p>')

    # filter chips (built from ALL assets, not the filtered view)
    all_tags = sorted({t for a in all_assets for t in (a.get("tags") or [])})
    assets = lib.list_assets(tag=tag, kind=kind)

    def chip(label: str, href: str, active: bool) -> str:
        return (f'<a class="filter-chip{" active" if active else ""}" '
                f'href="{_e(href)}">{_e(label)}</a>')

    kind_chips = [chip("全部", "/library", not kind and not tag)]
    for k in _lib_kinds():
        href = f"/library?kind={quote(k)}" + (f"&tag={quote(tag)}" if tag else "")
        kind_chips.append(chip(k, href, kind == k))
    tag_chips = []
    for t in all_tags:
        href = f"/library?tag={quote(t)}" + (f"&kind={quote(kind)}" if kind else "")
        tag_chips.append(chip("#" + t, href, tag == t))
    filters = (
        '<div class="lib-filters panel">'
        '<div class="lib-frow"><span class="muted">类型</span>' + "".join(kind_chips) + "</div>"
        + ('<div class="lib-frow"><span class="muted">标签</span>' + "".join(tag_chips) + "</div>"
           if tag_chips else "")
        + "</div>"
    )

    upload = (
        '<div class="lib-upload panel"><label class="btn ghost" for="lib-upload">＋ 上传入库</label>'
        '<input id="lib-upload" type="file" class="hidden"></div>'
    )

    if not assets:
        grid = '<p class="muted panel">素材库为空 (no assets)。</p>'
    else:
        cards = []
        for a in assets:
            hash8 = _hex(a["hash"])[:8]
            thumb = a.get("thumb")
            if thumb:
                thumb_html = f'<img class="lib-thumb" src="/lib-thumb/{_e(hash8)}" alt="">'
            else:
                thumb_html = (f'<div class="lib-thumb lib-thumb-ph">'
                              f'{_e((a.get("kind") or "?")[:3])}</div>')
            tags = a.get("tags") or []
            tag_html = "".join(f'<span class="lib-tag">#{_e(t)}</span>' for t in tags)
            note = a.get("note")
            size_mb = (a.get("size") or 0) / 1e6
            cards.append(
                f'<div class="lib-card panel" data-hash="{_e(hash8)}">'
                f"{thumb_html}"
                f'<div class="lib-name" title="{_e(a.get("name"))}">{_e(a.get("name"))}</div>'
                f'<div class="lib-meta muted">{_e(a.get("kind"))} · {size_mb:.2f}MB · {_e(hash8)}</div>'
                f'<div class="lib-tags">{tag_html}</div>'
                + (f'<div class="lib-note muted">{_e(note)}</div>' if note else "")
                + '<div class="lib-actions">'
                '<button class="btn mini" data-act="use" data-as="refs">用到项目(refs)</button>'
                '<button class="btn ghost mini" data-act="use" data-as="imports">用到 imports</button>'
                "</div>"
                '<div class="lib-edit">'
                f'<input class="lib-tag-input" placeholder="加标签(逗号分隔)" value="{_e(",".join(tags))}">'
                '<button class="btn ghost mini" data-act="tag">存标签</button></div>'
                '<div class="lib-edit">'
                f'<input class="lib-note-input" placeholder="备注 note" value="{_e(note or "")}">'
                '<button class="btn ghost mini" data-act="note">存备注</button></div>'
                "</div>"
            )
        grid = '<div class="lib-grid">' + "".join(cards) + "</div>"

    body = head + f'<p class="muted">{_e(str(lib.root))}</p>' + filters + upload + grid
    return _shell("素材库", token, "/library", body)


# ============================================================ 服务商 providers


def _adapter_short(adapter: str) -> str:
    from ..providers.manifest import ADAPTER_ALIASES

    for alias, full in ADAPTER_ALIASES.items():
        if full == adapter:
            return alias
    return adapter.split(":")[-1] if ":" in adapter else adapter


def _provider_rows() -> tuple[list[dict], list[str]]:
    import os

    from ..providers.manifest import fix_hint, load_manifests

    manifests, load_errors = load_manifests()
    rows = []
    for pid, m in sorted(manifests.items()):
        problems = m.validate_for_generic()
        key_env = m.auth.key_env
        rows.append({
            "id": pid,
            "type": m.type,
            "adapter_short": _adapter_short(m.adapter),
            "capabilities": list(m.capabilities),
            "key_env": key_env,
            "key_set": (bool(os.environ.get(key_env)) if key_env else None),
            "enabled": not m.disabled,
            "doctor_ok": not problems,
            "findings": [{"problem": p, "fix": fix_hint(p, m)} for p in problems],
        })
    return rows, load_errors


def render_providers(project: Any, token: str) -> str:
    head = ('<div class="page-h"><h1>服务商 Providers</h1>'
            '<span class="muted">每个 provider 是一个' + tooltip_html("provider")
            + ' · 状态板 · 密钥值从不显示</span></div>')
    try:
        rows, load_errors = _provider_rows()
    except Exception as exc:  # never break the page on a probe error
        return _shell("服务商", token, "/providers",
                      head + f'<p class="err panel">{_e(exc)}</p>')

    cards = []
    for r in rows:
        enabled = r["enabled"]
        key = r["key_env"]
        if key:
            key_html = (f'<span class="badge st-{"fresh" if r["key_set"] else "needs"}">'
                        f'{"✓" if r["key_set"] else "✗"} {_e(key)}</span>')
        else:
            key_html = '<span class="muted">无需密钥</span>'
        caps = ", ".join(r["capabilities"]) or "—"
        if r["doctor_ok"]:
            check_html = '<div class="pv-check ok">✓ 离线检查通过 (offline checks pass)</div>'
        else:
            findings = "".join(
                f'<li class="pv-finding"><span class="err">✗ {_e(f["problem"])}</span>'
                f'<div class="muted">→ {_e(f["fix"])}</div></li>'
                for f in r["findings"]
            )
            check_html = f'<ul class="pv-check bad">{findings}</ul>'
        toggle_label = "禁用" if enabled else "启用"
        toggle_cls = "ghost" if enabled else ""
        status_badge = ('<span class="badge st-fresh">enabled</span>' if enabled
                        else '<span class="badge st-missing">disabled</span>')
        cards.append(
            f'<div class="pv-card panel" data-id="{_e(r["id"])}">'
            f'<div class="pv-head"><h2>{_e(r["id"])} {status_badge}</h2>'
            f'<button class="btn {toggle_cls} mini" data-act="toggle" '
            f'data-id="{_e(r["id"])}" data-enabled="{"1" if enabled else "0"}">{toggle_label}</button></div>'
            f'<div class="pv-meta"><span class="chip">{_e(r["type"])}</span>'
            f'<span class="chip">{_e(r["adapter_short"])}</span>{key_html}</div>'
            f'<div class="pv-caps muted">能力: {_e(caps)}</div>'
            f"{check_html}</div>"
        )
    if not cards:
        cards.append('<p class="muted panel">未配置任何 provider manifest '
                     '(~/.manju/providers) — 用下面的模板新增。</p>')
    err_html = "".join(f'<p class="err">{_e(e)}</p>' for e in load_errors)

    # read-only "add a provider" panel (creation stays CLI per the matrix)
    try:
        from ..providers.manifest import scaffold_template

        template = scaffold_template("your_provider", "video", "generic_cloud")
    except Exception:
        template = "# (template unavailable)"
    add_panel = (
        '<div class="pv-add panel"><h2>新增 provider (只读示范)</h2>'
        '<p class="muted">创建走命令行(matrix §8.6):</p>'
        '<pre class="pv-cmd">manju providers add your_provider --type video --adapter generic_cloud</pre>'
        '<details><summary class="muted">模板预览 provider.yaml</summary>'
        f'<pre class="pv-template">{_e(template)}</pre></details></div>'
    )

    body = head + err_html + "".join(cards) + add_panel
    return _shell("服务商", token, "/providers", body)


# ============================================================ 路由 routing


def render_routing(project: Any, token: str) -> str:
    from ..providers.routing import RoutingError, list_strategies

    head = ('<div class="page-h"><h1>路由 Routing</h1>'
            '<span class="muted">' + tooltip_html("routing") + '与'
            + tooltip_html("fallback")
            + '的只读视图 + 策略选择器(写 timeline/routing.yaml)</span></div>')
    try:
        info = list_strategies(project)
    except RoutingError as exc:
        return _shell("路由", token, "/routing",
                      head + f'<p class="err panel">{_e(exc)}</p>')

    active = info["active"]
    sources = info.get("sources") or []
    src_line = (f'routing.yaml 来源: {_e(", ".join(sources))} (项目覆盖用户)'
                if sources else "无 routing.yaml — 默认 §8.4 行为(内置策略仍可用)")

    # strategy picker
    opts = "".join(
        f'<option value="{_e(s["name"])}"{" selected" if s["name"] == active else ""}>'
        f'{_e(s["name"])}{" ✓" if s["name"] == active else ""}</option>'
        for s in info["strategies"]
    )
    picker = (
        '<div class="rt-picker panel"><label>活动策略 '
        f'<select id="rt-strategy">{opts}</select></label>'
        '<button class="btn" id="rt-apply">应用</button>'
        '<span class="muted">写入后即校验;失败自动回滚</span></div>'
    )

    # per-shot route explain
    shot_ids = project.shot_ids()
    if shot_ids:
        shot_opts = "".join(f'<option value="{_e(s)}">{_e(s)}</option>' for s in shot_ids)
        explain_panel = (
            '<div class="rt-explain panel"><h2>逐镜路由解释 Route explain</h2>'
            f'<label>镜头 <select id="rt-explain-shot">{shot_opts}</select></label>'
            '<button class="btn ghost" id="rt-explain-btn">解释</button>'
            '<div id="rt-explain-out" class="rt-explain-out muted"></div></div>'
        )
    else:
        explain_panel = ""

    cards = []
    for s in info["strategies"]:
        act = s["name"] == active
        badge = '<span class="badge st-fresh">active</span>' if act else ""
        tag = "内置" if s["builtin"] else "自定义"
        pr = f' · priority={_e(s["priority"])}' if s.get("priority") else ""
        cards.append(
            f'<div class="rt-card panel{" rt-active" if act else ""}">'
            f'<h3>{_e(s["name"])} {badge} <span class="muted">[{tag}]</span></h3>'
            f'<div class="muted">rules={_e(s["rules"])} · else={_e(s["else"])}{pr}</div></div>'
        )

    body = (head + f'<p class="muted">{src_line}</p>' + picker + explain_panel
            + '<div class="rt-cards">' + "".join(cards) + "</div>")
    return _shell("路由", token, "/routing", body)


# ============================================================ 体检 doctor


def render_doctor(project: Any, token: str) -> str:
    from ..build.doctor import run_doctor

    head = ('<div class="page-h"><h1>体检 Doctor</h1>'
            '<button class="btn ghost" id="dr-refresh">刷新</button></div>')
    try:
        report = run_doctor(project)
    except Exception as exc:
        return _shell("体检", token, "/doctor",
                      head + f'<p class="err panel">{_e(exc)}</p>')

    overall = ('<span class="badge st-fresh">全部通过</span>' if report.get("ok")
               else '<span class="badge st-needs">有问题</span>')
    rows = []
    for c in report.get("checks", []):
        ok = c.get("ok")
        glyph = "✓" if ok else "✗"
        cls = "dr-ok" if ok else "dr-bad"
        detail = c.get("detail") or ""
        hint = "" if ok else f'<div class="dr-hint muted">→ {_e(detail)}</div>'
        rows.append(
            f'<div class="dr-row {cls}"><span class="dr-glyph">{glyph}</span>'
            f'<span class="dr-name">{_e(c.get("name"))}</span>'
            f'<span class="dr-detail muted">{_e(detail)}</span>{hint}</div>'
        )
    body = (head + f'<div class="dr-overall panel">{overall}</div>'
            + '<div class="dr-list panel">' + "".join(rows) + "</div>")
    return _shell("体检", token, "/doctor", body)


# ============================================================ text helpers


def set_disabled_in_text(text: str, disabled: bool) -> str:
    """Flip (or insert) the top-level ``disabled:`` key, preserving comments —
    the same policy-switch edit the CLI's ``providers enable/disable`` makes."""
    val = "true" if disabled else "false"
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        if re.match(r"^disabled\s*:", line):
            lines[i] = f"disabled: {val}"
            return "\n".join(lines)
    anchor = None
    for i, line in enumerate(lines):
        if re.match(r"^(adapter|id)\s*:", line):
            anchor = i
    lines.insert((anchor + 1) if anchor is not None else 0, f"disabled: {val}")
    return "\n".join(lines)


def set_strategy_in_text(text: str, strategy: str) -> str:
    """Rewrite ONLY the top-level ``strategy:`` key (insert at top if absent),
    leaving every other line — custom ``strategies:`` blocks, comments — intact."""
    lines = text.split("\n") if text else []
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        if re.match(r"^strategy\s*:", line):
            lines[i] = f"strategy: {strategy}"
            return "\n".join(lines)
    lines.insert(0, f"strategy: {strategy}")
    return "\n".join(lines)


# ============================================================ assets (css/js)


def render_pages_css() -> str:
    return _PAGES_CSS


def render_pages_js() -> str:
    return _PAGES_JS


_PAGES_CSS = """
/* manju gui — extra server-rendered pages (round S, S8b). Loaded AFTER
   /app.css; reuses its :root palette and never overrides its rules. */

.pnav {
  display: flex; flex-wrap: wrap; gap: .4rem; padding: .55rem 1.2rem;
  background: var(--panel); border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 60;
}
.pnav a {
  color: var(--fg); text-decoration: none; font-size: .84rem;
  padding: .22rem .7rem; border-radius: 999px; border: 1px solid var(--line);
  background: var(--panel2);
}
.pnav a.active { background: var(--accent); color: #0b1220; font-weight: 700; border-color: var(--accent); }
.pnav a:hover { filter: brightness(1.15); }

.page-h { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; margin: 1.1rem 0 .5rem; }
.page-h h1 { font-size: 1.25rem; }
.err { color: var(--err); }
.mini { padding: .1rem .5rem !important; font-size: .74rem !important; }

#toast { position: fixed; right: 1rem; bottom: 1rem; display: flex; flex-direction: column; gap: .4rem; z-index: 200; }
.toast-item {
  background: var(--panel2); border: 1px solid var(--line); color: var(--fg);
  padding: .45rem .8rem; border-radius: 8px; font-size: .84rem; max-width: 340px;
  box-shadow: 0 6px 20px rgba(0,0,0,.5);
}
.toast-item.good { border-color: #2c5a3f; }
.toast-item.bad { border-color: #5a2c2f; color: var(--err); }

/* ---------------------------------------------------------- review -- */
.rv-progress { display: flex; align-items: center; gap: 1rem; }
.rv-bar { flex: 1; max-width: 480px; }
.rv-shot.active { outline: 2px solid var(--accent); }
.rv-shot.reviewed .rv-head h2::after { content: " ✓"; color: var(--ok); }
.rv-head { display: flex; justify-content: space-between; gap: 1rem; flex-wrap: wrap; align-items: baseline; }
.rv-idx { font-size: .8rem; margin-left: .4rem; }
.rv-body { display: grid; grid-template-columns: 1.4fr 1fr; gap: 1rem; margin: .7rem 0; }
.rv-video { width: 100%; max-height: 420px; border-radius: 8px; background: #000; }
.rv-side { display: flex; flex-direction: column; gap: .7rem; }
.rv-qc { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: .4rem; }
.rv-qc-item { font-size: .84rem; }
.rv-qc-sug { font-size: .8rem; margin-left: 1.2rem; }
.rv-frame img { width: 100%; border-radius: 6px; border: 1px solid var(--line); display: block; margin-top: .2rem; }
.rv-alts-row { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .3rem; }
.rv-alt { display: flex; flex-direction: column; gap: .2rem; width: 120px; }
.rv-alt img, .rv-alt-noimg { width: 120px; height: 68px; object-fit: cover; border-radius: 5px; background: var(--panel2); border: 1px solid var(--line); }
.rv-actions { align-items: center; }
.rv-repair { display: inline-flex; align-items: center; gap: .35rem; flex-wrap: wrap; color: var(--muted); font-size: .8rem; }
.rv-noterow { display: flex; gap: .5rem; margin-top: .6rem; }
.rv-note-input, .lib-tag-input, .lib-note-input {
  flex: 1; background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .3rem .5rem; font: inherit; font-size: .84rem;
}
.rv-explain { margin-top: .5rem; font-size: .84rem; font-family: var(--mono); }

@media (max-width: 820px) { .rv-body { grid-template-columns: 1fr; } }

/* ------------------------------------------- consistency (round X, XB) -- */
.cs-unit { margin: .8rem 0; }
.cs-head { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap; }
.cs-board { display: block; max-width: 100%; margin: .5rem 0; border-radius: 8px; border: 1px solid var(--line); }
.cs-form { align-items: center; gap: .4rem; flex-wrap: wrap; margin-top: .4rem; }
.cs-form select { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .25rem .4rem; font: inherit; font-size: .84rem; }
.cs-message { flex: 1; min-width: 220px; background: var(--panel2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .3rem .5rem; font: inherit; font-size: .84rem; }
.cs-skipped { margin-top: .6rem; font-size: .84rem; }

/* --------------------------------------------------------- compare -- */
.cmp-picker { display: flex; gap: 1.2rem; align-items: center; flex-wrap: wrap; }
.cmp-picker select { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .25rem .45rem; font: inherit; }
.cmp-synclbl { color: var(--muted); font-size: .84rem; }
.cmp-strip { display: flex; flex-wrap: wrap; gap: .5rem; }
.cmp-chip { background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: .3rem .7rem; font-size: .86rem; }
.cmp-chip.bad { border-color: #6b5518; color: var(--warn); }
.cmp-players { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.cmp-side h3 { margin: 0 0 .3rem; }
.cmp-video { width: 100%; border-radius: 8px; background: #000; }
.cmp-table { width: 100%; border-collapse: collapse; font-size: .84rem; }
.cmp-table th, .cmp-table td { text-align: left; padding: .3rem .55rem; border-bottom: 1px solid var(--line); vertical-align: top; }
.cmp-table th { color: var(--muted); }
.cmp-row.dim { opacity: .45; }
.cmp-shot { font-family: var(--mono); }
.cmp-why { font-size: .8rem; }
.chgbadge { background: var(--panel2); color: var(--fg); }
.badge.chg-take_changed, .badge.chg-added { background: #17402a; color: #7ee2a8; }
.badge.chg-removed { background: #4a2020; color: #ff8a90; }
.badge.chg-moved, .badge.chg-duration_changed { background: #4a3a12; color: #ffcf5c; }
.cmp-degraded { border-left: 3px solid var(--warn); }
@media (max-width: 820px) { .cmp-players { grid-template-columns: 1fr; } }

/* --------------------------------------------------------- library -- */
.lib-filters { display: flex; flex-direction: column; gap: .5rem; }
.lib-frow { display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; }
.filter-chip { color: var(--fg); text-decoration: none; font-size: .8rem; padding: .15rem .6rem; border-radius: 999px; border: 1px solid var(--line); background: var(--panel2); }
.filter-chip.active { background: var(--accent); color: #0b1220; font-weight: 700; }
.lib-upload label { cursor: pointer; }
.lib-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 1rem; }
.lib-card { margin: 0; display: flex; flex-direction: column; gap: .35rem; }
.lib-thumb { width: 100%; height: 130px; object-fit: cover; border-radius: 6px; background: var(--panel2); border: 1px solid var(--line); }
.lib-thumb-ph { display: flex; align-items: center; justify-content: center; color: var(--muted); text-transform: uppercase; font-size: .9rem; letter-spacing: .05em; }
.lib-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lib-meta { font-size: .78rem; }
.lib-tags { display: flex; flex-wrap: wrap; gap: .25rem; }
.lib-tag { font-size: .72rem; background: var(--panel2); border: 1px solid var(--line); border-radius: 999px; padding: 0 .5rem; color: var(--accent); }
.lib-actions { display: flex; gap: .35rem; flex-wrap: wrap; }
.lib-edit { display: flex; gap: .35rem; }

/* -------------------------------------------------------- providers -- */
.pv-card { margin: .8rem 0; }
.pv-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; }
.pv-meta { display: flex; gap: .4rem; flex-wrap: wrap; align-items: center; margin: .4rem 0; }
.pv-caps { font-size: .82rem; }
.pv-check { margin: .5rem 0 0; }
.pv-check.ok { color: var(--ok); font-size: .84rem; }
.pv-check.bad { list-style: none; padding: 0; display: flex; flex-direction: column; gap: .4rem; }
.pv-finding { font-size: .84rem; }
.pv-cmd, .pv-template { background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: .5rem .7rem; overflow-x: auto; font-family: var(--mono); font-size: .8rem; white-space: pre; }
.pv-template { max-height: 360px; overflow: auto; }

/* --------------------------------------------------------- routing -- */
.rt-picker { display: flex; gap: .8rem; align-items: center; flex-wrap: wrap; }
.rt-picker select { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .25rem .45rem; font: inherit; }
.rt-explain-out { margin-top: .5rem; font-family: var(--mono); font-size: .84rem; }
.rt-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 1rem; }
.rt-card { margin: 0; }
.rt-card.rt-active { outline: 1px solid var(--accent); }

/* --------------------------------------------------------- doctor -- */
.dr-list { display: flex; flex-direction: column; gap: .3rem; }
.dr-row { display: grid; grid-template-columns: 1.5rem 180px 1fr; gap: .5rem; align-items: baseline; padding: .2rem 0; border-bottom: 1px solid var(--line); }
.dr-row.dr-ok .dr-glyph { color: var(--ok); }
.dr-row.dr-bad .dr-glyph { color: var(--err); }
.dr-name { font-family: var(--mono); font-size: .84rem; }
.dr-detail { font-size: .82rem; }
.dr-hint { grid-column: 2 / 4; font-size: .82rem; }
@media (max-width: 640px) { .dr-row { grid-template-columns: 1.5rem 1fr; } .dr-detail, .dr-hint { grid-column: 2 / 3; } }
"""


_PAGES_JS = r"""
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
    setTimeout(function () { el.remove(); }, 3400);
  }
  function reloadSoon() { setTimeout(function () { location.reload(); }, 450); }

  var page = document.body.getAttribute("data-page");
  if (page === "/review") initReview();
  else if (page === "/compare") initCompare();
  else if (page === "/library") initLibrary();
  else if (page === "/providers") initProviders();
  else if (page === "/routing") initRouting();
  else if (page === "/doctor") initDoctor();
  if (page === "/review") initReviewConsistency();

  // ------------------------------- 跨镜一致性 consistency verdict form (round X)
  function initReviewConsistency() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest('[data-act="cs-submit"]');
      if (!btn) return;
      var card = btn.closest(".cs-unit");
      if (!card) return;
      var unit = card.getAttribute("data-unit");
      var crit = card.querySelector(".cs-criterion").value;
      var level = card.querySelector(".cs-level").value;
      var msgEl = card.querySelector(".cs-message");
      var msg = msgEl.value.trim();
      if (!msg) { toast("请填写中文结论", false); return; }
      btn.disabled = true;
      post("/api/qc/verdict", { unit: unit, criterion: crit, level: level, message: msg })
        .then(function (res) {
          btn.disabled = false;
          if (res.status === 200) {
            toast("裁决已提交:" + unit, true);
            msgEl.value = "";
            var chip = card.querySelector(".cs-state");
            if (chip) {
              chip.textContent = "已判读";
              chip.className = "badge st-fresh cs-state";
            }
          } else {
            toast((res.data && res.data.error) || "失败", false);
          }
        });
    });
  }

  // ------------------------------------------------------------- review
  function initReview() {
    var fill = document.getElementById("rv-progress-fill");
    if (fill && fill.dataset.pct) fill.style.width = fill.dataset.pct + "%";
    var shots = Array.prototype.slice.call(document.querySelectorAll(".rv-shot"));
    if (!shots.length) return;
    var active = 0;

    function updateProgress() {
      var done = document.querySelectorAll('.rv-shot[data-reviewed="1"]').length;
      var meter = document.getElementById("rv-progress");
      if (meter) meter.textContent = "已审 " + done + " / " + shots.length;
      if (fill) fill.style.width = (shots.length ? (done * 100 / shots.length) : 0) + "%";
    }
    function setActive(i) {
      if (i < 0) i = 0;
      if (i >= shots.length) i = shots.length - 1;
      shots[active].classList.remove("active");
      active = i;
      shots[active].classList.add("active");
      shots[active].scrollIntoView({ behavior: "smooth", block: "start" });
    }
    function currentVideo() { return shots[active].querySelector("video"); }

    function verdict(kind) {
      var s = shots[active];
      var shot = s.getAttribute("data-shot");
      var take = s.getAttribute("data-take");
      if (!take) { toast("先选用一个 take", false); return; }
      var noteEl = s.querySelector(".rv-note-input");
      var extra = noteEl && noteEl.value.trim() ? " · " + noteEl.value.trim() : "";
      var label = kind === "good" ? "好" : "弃";
      post("/api/take-note", { shot: shot, take: take, text: label + extra }).then(function (res) {
        if (res.status === 200) {
          s.setAttribute("data-reviewed", "1");
          s.classList.add("reviewed");
          updateProgress();
          toast(shot + " " + label, kind === "good");
          if (kind === "reject") {
            var alts = s.querySelector(".rv-alts");
            if (alts) alts.scrollIntoView({ behavior: "smooth", block: "center" });
          } else {
            setActive(active + 1);
          }
        } else { toast((res.data && res.data.error) || "失败", false); }
      });
    }

    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-act]");
      if (!btn) return;
      var s = btn.closest(".rv-shot");
      if (!s) return;
      var i = shots.indexOf(s);
      if (i >= 0 && i !== active) setActive(i);
      var act = btn.getAttribute("data-act");
      var shot = s.getAttribute("data-shot");
      if (act === "good") verdict("good");
      else if (act === "reject") verdict("reject");
      else if (act === "skip") setActive(active + 1);
      else if (act === "note") {
        var take = s.getAttribute("data-take");
        if (!take) { toast("先选用一个 take", false); return; }
        var val = s.querySelector(".rv-note-input").value.trim();
        post("/api/take-note", { shot: shot, take: take, text: val }).then(function (res) {
          if (res.status === 200) {
            toast("备注已存", true);
            if (val) { s.setAttribute("data-reviewed", "1"); updateProgress(); }
          } else { toast((res.data && res.data.error) || "失败", false); }
        });
      }
      else if (act === "select") {
        post("/api/select", { shot: shot, take: btn.getAttribute("data-take") }).then(function (res) {
          if (res.status === 200) { toast("已换用 " + btn.getAttribute("data-take"), true); reloadSoon(); }
          else toast((res.data && res.data.error) || "失败", false);
        });
      }
      else if (act === "redo") {
        post("/api/redo", { shot: shot }).then(function (res) {
          if (res.status === 202) toast("重做已排队 (queued)", true);
          else toast((res.data && res.data.error) || "失败", false);
        });
      }
      else if (act === "repair") {
        var b = { shot: shot, op: btn.getAttribute("data-op") };
        if (btn.getAttribute("data-factor")) b.factor = parseFloat(btn.getAttribute("data-factor"));
        if (btn.getAttribute("data-ms")) b.ms = parseInt(btn.getAttribute("data-ms"), 10);
        if (btn.getAttribute("data-mode")) b.mode = btn.getAttribute("data-mode");
        post("/api/repair", b).then(function (res) {
          if (res.status === 202) toast("修复排队: " + b.op, true);
          else toast((res.data && res.data.error) || "失败", false);
        });
      }
      else if (act === "route") {
        var box = s.querySelector(".rv-explain");
        fetch("/api/route-explain?shot=" + encodeURIComponent(shot))
          .then(function (r) { return r.json(); })
          .then(function (d) {
            box.hidden = false;
            box.textContent = "";
            if (d.error) { box.textContent = d.error; return; }
            box.textContent = "策略 " + d.strategy + " → " + (d.chosen || "(none)")
              + "  顺序: " + ((d.order || []).join(" › ") || "—");
          });
      }
    });

    document.addEventListener("keydown", function (e) {
      var tag = e.target && e.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "j") { setActive(active + 1); e.preventDefault(); }
      else if (e.key === "k") { setActive(active - 1); e.preventDefault(); }
      else if (e.key === "g") { verdict("good"); e.preventDefault(); }
      else if (e.key === "x") { verdict("reject"); e.preventDefault(); }
      else if (e.key === " ") {
        var v = currentVideo();
        if (v) { if (v.paused) v.play(); else v.pause(); }
        e.preventDefault();
      }
    });
    shots[0].classList.add("active");
  }

  // ------------------------------------------------------------ compare
  function initCompare() {
    var selA = document.getElementById("cmp-a-sel");
    var selB = document.getElementById("cmp-b-sel");
    function nav() {
      location.href = "/compare?a=" + encodeURIComponent(selA.value)
        + "&b=" + encodeURIComponent(selB.value);
    }
    if (selA) selA.addEventListener("change", nav);
    if (selB) selB.addEventListener("change", nav);

    var va = document.getElementById("cmp-vid-a");
    var vb = document.getElementById("cmp-vid-b");
    var sync = document.getElementById("cmp-sync");
    var busy = false;
    function on() { return sync && sync.checked && va && vb; }
    function mirrorTime() {
      if (!on() || busy) return;
      busy = true;
      try { if (Math.abs(vb.currentTime - va.currentTime) > 0.3) vb.currentTime = va.currentTime; }
      catch (e) {}
      busy = false;
    }
    if (va && vb) {
      va.addEventListener("play", function () { if (on()) vb.play(); });
      va.addEventListener("pause", function () { if (on()) vb.pause(); });
      va.addEventListener("seeked", mirrorTime);
      va.addEventListener("timeupdate", mirrorTime);
    }
  }

  // ------------------------------------------------------------ library
  function initLibrary() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-act]");
      if (!btn) return;
      var card = btn.closest(".lib-card");
      if (!card) return;
      var hash = card.getAttribute("data-hash");
      var act = btn.getAttribute("data-act");
      if (act === "use") {
        post("/api/lib/use", { hash: hash, as: btn.getAttribute("data-as") || "refs" }).then(function (res) {
          if (res.status === 200) toast("已用到项目 → " + res.data.dest, true);
          else toast((res.data && res.data.error) || "失败", false);
        });
      } else if (act === "tag") {
        var tv = card.querySelector(".lib-tag-input").value;
        post("/api/lib/tag", { hash: hash, tags: tv }).then(function (res) {
          if (res.status === 200) { toast("标签已存", true); reloadSoon(); }
          else toast((res.data && res.data.error) || "失败", false);
        });
      } else if (act === "note") {
        var nv = card.querySelector(".lib-note-input").value;
        post("/api/lib/note", { hash: hash, note: nv }).then(function (res) {
          if (res.status === 200) toast("备注已存", true);
          else toast((res.data && res.data.error) || "失败", false);
        });
      }
    });
    var up = document.getElementById("lib-upload");
    if (up) up.addEventListener("change", function () {
      var f = up.files[0];
      if (!f) return;
      fetch("/api/lib/upload?name=" + encodeURIComponent(f.name), {
        method: "POST", headers: { "X-Manju-Token": TOKEN }, body: f
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          return { status: r.status, data: d };
        });
      }).then(function (res) {
        if (res.status === 200) { toast("已入库 (added)", true); reloadSoon(); }
        else toast((res.data && res.data.error) || "失败", false);
      });
    });
  }

  // ----------------------------------------------------------- providers
  function initProviders() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-act='toggle']");
      if (!btn) return;
      var id = btn.getAttribute("data-id");
      var disable = btn.getAttribute("data-enabled") === "1";
      post("/api/providers/toggle", { id: id, disabled: disable }).then(function (res) {
        if (res.status === 200) { toast(id + (disable ? " 已禁用" : " 已启用"), true); reloadSoon(); }
        else toast((res.data && res.data.error) || "失败", false);
      });
    });
  }

  // ------------------------------------------------------------- routing
  function initRouting() {
    var apply = document.getElementById("rt-apply");
    if (apply) apply.addEventListener("click", function () {
      var sel = document.getElementById("rt-strategy");
      post("/api/routing/strategy", { strategy: sel.value }).then(function (res) {
        if (res.status === 200) { toast("策略 → " + sel.value, true); reloadSoon(); }
        else toast((res.data && res.data.error) || "失败", false);
      });
    });
    var exp = document.getElementById("rt-explain-btn");
    if (exp) exp.addEventListener("click", function () {
      var sel = document.getElementById("rt-explain-shot");
      var box = document.getElementById("rt-explain-out");
      fetch("/api/route-explain?shot=" + encodeURIComponent(sel.value))
        .then(function (r) { return r.json(); })
        .then(function (d) {
          box.textContent = "";
          if (d.error) { box.textContent = d.error; return; }
          box.textContent = d.shot + ": 策略 " + d.strategy + " → " + (d.chosen || "(none)")
            + "  顺序 " + ((d.order || []).join(" › ") || "—");
        });
    });
  }

  // -------------------------------------------------------------- doctor
  function initDoctor() {
    var b = document.getElementById("dr-refresh");
    if (b) b.addEventListener("click", function () { location.reload(); });
  }
})();
"""
