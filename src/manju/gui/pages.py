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

from .a11y import HTML_LANG, NOSCRIPT_HTML, SKIP_LINK_HTML, main_open
from .glossary import tooltip_html

__all__ = [
    "PAGE_PATHS",
    "render",
    "render_pages_css",
    "render_pages_js",
    "nav_html",
    "chrome",
    "series_banner_html",
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
    ("/packaging", "包装"),
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

# Direction program (#50): the daily nav groups by USE-FREQUENCY, not by
# module — 17 flat pills asked for a navigation decision on every glance.
# PRESENTATION-layer only: every page keeps its route, every link stays in
# the DOM (CSS dropdowns), _NAV above stays the one label owner, and the
# PRO_ONLY/beginner behaviour is unchanged. A group whose pages are all
# hidden in 新手 mode disappears with them.
_NAV_GROUPS = (
    ("工作台", ("/",)),
    ("创作", ("/create", "/director")),
    ("镜头", ("/storyboard", "/lab", "/ingest")),
    ("审片", ("/review", "/compare")),
    ("成片", ("/edit", "/subtitles", "/mixer", "/packaging", "/exports")),
    ("工具", ("/library", "/providers", "/routing", "/doctor")),
)

# The finishing surfaces already render a richer in-page journey with the same
# five destinations.  Repeating those links in the global sub-navigation would
# add visual noise and two competing current-page indicators, so the chrome
# only marks the 成片 stage and lets :mod:`manju.gui.finishing_journey` own the
# page-level sequence.
_INLINE_STAGE_NAV = frozenset(
    {"/edit", "/subtitles", "/mixer", "/packaging", "/exports"}
)

# Head stanza every workbench surface shares so the glossary tooltip look + the
# mode/terms chrome behave identically on the SPA and all server-rendered pages.
GLOSSARY_HEAD = (
    '<link rel="stylesheet" href="/glossary.css">\n'
    '<link rel="stylesheet" href="/project-action.css">\n'
    '<link rel="stylesheet" href="/task-center.css">\n'
    '<script src="/webclient.js" defer></script>\n'
    '<script src="/project-action.js" defer></script>\n'
    '<script src="/task-center.js" defer></script>\n'
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


def series_banner_html(project: Any) -> str:
    """Round X (agent XD, user pain #4): the 剧集 membership banner — rendered
    into the chrome when the GUI's bound project is an EPISODE of a series
    (``Series.find`` walks up past the episode's ``project.yaml`` to the
    umbrella's ``series.yaml`` — see core/series.py). Absent for a plain
    project (no series.yaml anywhere above it) or when the series.yaml can't
    be read — a broken umbrella must never break the episode's own chrome."""
    try:
        from ..core.series import Series

        series = Series.find_or_none(project.root)
    except Exception:
        series = None
    if series is None:
        return ""
    try:
        name = series.load_config().name
    except Exception:
        name = series.root.name
    return (
        '<div class="mj-series-banner">本片属于剧集《' + _e(name) + "》"
        '<a href="/series">→ 剧集工作台</a></div>'
    )


def _project_display_name(project: Any) -> str:
    """Best-effort display name for shared chrome only.

    This is deliberately presentation-only: failure to read project metadata
    falls back to the directory name and never blocks the page or becomes a
    build input.
    """
    if project is None:
        return ""
    try:
        return str(project.load_config().name or project.root.name)
    except Exception:
        try:
            return str(project.root.name)
        except Exception:
            return ""


def chrome(
    active: str,
    project: Any = None,
    *,
    project_name: str | None = None,
) -> tuple[str, str]:
    """Resolve the per-user view mode + glossary toggle (server-side) and return
    ``(nav_html, body_class)`` for a page shell. Central so the SPA and every
    server-rendered page share one mode-aware nav and one body class.

    ``project`` is optional (default ``None`` — unchanged nav, no banner) so
    every existing call site stays byte-identical; pass the bound project to
    also render the 剧集 membership banner (:func:`series_banner_html`) right
    after the nav."""
    from .userstate import is_mode_hint_dismissed, is_show_pro_terms, resolve_mode

    mode = resolve_mode()
    show = is_show_pro_terms()
    label = project_name if project_name is not None else _project_display_name(project)
    nav = nav_html(
        active,
        mode=mode,
        show_terms=show,
        hint_dismissed=is_mode_hint_dismissed(),
        project_name=label,
    )
    if project is not None:
        nav += series_banner_html(project)
    return nav, body_class(mode, show)


def _mode_controls(mode: str, show_terms: bool) -> str:
    """Progressively-disclosed 新手/专业 and terminology controls.

    The controls and their existing ``data-mode`` / checkbox hooks remain the
    same; only their presentation moves behind a native ``<details>`` menu so
    the production stages keep visual priority.
    """

    def btn(m: str, label: str) -> str:
        on = mode == m
        return (f'<button type="button" class="mj-mode-btn{" on" if on else ""}" '
                f'data-mode="{m}" aria-pressed="{"true" if on else "false"}">'
                f"{_e(label)}</button>")

    checked = " checked" if show_terms else ""
    summary = "新手视图" if mode == "beginner" else "专业视图"
    if show_terms:
        summary += " · 术语"
    return (
        '<details class="mj-view-menu">'
        f'<summary aria-label="界面视图设置">{_e(summary)}'
        '<span class="mj-view-caret" aria-hidden="true">▾</span></summary>'
        '<div class="mj-view-popover">'
        '<div class="mj-view-title">界面视图</div>'
        '<span class="mj-nav-ctl">'
        '<span class="mj-modesw" role="group" aria-label="界面视图">'
        + btn("beginner", "新手") + btn("pro", "专业")
        + "</span>"
        '<label class="mj-terms-toggle" '
        'title="在中文标签旁显示对应的专业术语">'
        f'<input type="checkbox" id="mj-terms-toggle"{checked}> 显示专业术语</label>'
        "</span></div></details>"
    )


def _workspace_switcher(project_name: str = "") -> str:
    """The compact project-switcher trigger (round X agent XE, user pain #6):
    a recents-backed dropdown, always present in the shared nav so it works on
    the SPA AND every server-rendered page alike. Population + the open/rebind
    action are lazy (`GET /api/workspace/recents` on first open, `POST
    /api/workspace/open` on pick) and live in the shared chrome script
    (:mod:`manju.gui.glossary`'s ``render_glossary_js``) so this stays a pure,
    CSP-safe static shell — no inline handlers, nothing baked in server-side
    besides the two empty containers JS fills."""
    label = project_name.strip() or "项目"
    title = f"切换项目，当前：{label}" if project_name.strip() else "切换/打开项目"
    return (
        '<span class="mj-ws-wrap">'
        '<button type="button" id="mj-ws-btn" class="mj-ws-btn" '
        f'aria-haspopup="true" aria-expanded="false" title="{_e(title)}">'
        f'<span class="mj-ws-label">{_e(label)}</span>'
        '<span class="mj-ws-caret" aria-hidden="true">▾</span></button>'
        '<div id="mj-ws-menu" class="mj-ws-menu hidden" role="menu" '
        'aria-label="切换项目"></div>'
        "</span>"
    )


def _execution_status_html() -> str:
    """Secret-free execution policy indicator for every workbench surface."""
    try:
        from ..providers.zero_cost import execution_policy_snapshot

        snap = execution_policy_snapshot()
    except Exception:
        snap = {"status": "invalid", "mode": "unknown"}

    status = str(snap.get("status") or "invalid")
    mode = str(snap.get("mode") or "unknown")
    if status == "strict":
        label = "本地安全"
        title = "不会连接外部 Provider，也不会读取云端密钥"
    elif status == "standard":
        label = "标准执行"
        title = "外部 Provider 仍受预算、确认和请求身份保护"
    else:
        label = "模式错误"
        title = f"MANJU_EXECUTION_MODE={mode}；Provider 执行会被拒绝"
    return (
        f'<span class="mj-execution-state {status}" '
        f'data-execution-status="{_e(status)}" title="{_e(title)}">'
        '<span class="mj-execution-dot" aria-hidden="true"></span>'
        f'<span class="mj-execution-label">{_e(label)}</span>'
        f'<span class="mj-execution-mode mj-tech">{_e(mode)}</span>'
        "</span>"
    )


def _mode_hint() -> str:
    """Compact, dismissable home-only guidance for a fresh beginner."""
    return (
        '<div id="mj-mode-hint" class="mj-mode-hint" role="status">'
        '<span class="mj-mode-hint-copy"><b>新手视图已开启</b>'
        '<span>高级工具已收起，项目内容不会改变。</span></span>'
        '<span class="mj-mode-hint-actions">'
        '<button type="button" class="mj-mode-btn mj-mode-hint-pro" '
        'data-mode="pro">查看全部功能</button>'
        '<button type="button" id="mj-mode-hint-x" aria-label="关闭新手提示">'
        "知道了</button></span></div>"
    )


def nav_html(
    active: str,
    mode: str = "pro",
    show_terms: bool = False,
    hint_dismissed: bool = True,
    project_name: str = "",
) -> str:
    """Shared application chrome with stable stages and progressive detail.

    The six production stages are always visible.  Only the active stage's
    page-level links are shown, avoiding hover-only discovery and reducing the
    old 17-link pill wall.  Beginner mode still omits advanced destinations;
    directly-opened advanced pages explain their current location without
    re-advertising the hidden link.
    """
    beginner = mode == "beginner"
    label_of = dict(_NAV)

    active_group: tuple[str, tuple[str, ...]] | None = None
    for group in _NAV_GROUPS:
        if active in group[1]:
            active_group = group
            break

    out = [
        '<nav class="pnav" aria-label="Manju 应用导航">',
        '<div class="pnav-appbar">',
        '<a class="pnav-brand" href="/" aria-label="Manju 工作台">'
        '<span class="pnav-brand-mark" aria-hidden="true">M</span>'
        '<span class="pnav-brand-name">Manju</span></a>',
        '<div class="pnav-tools">',
        _execution_status_html(),
        '<button type="button" id="mj-task-center-btn" class="mj-task-button" '
        'aria-haspopup="dialog" aria-controls="mj-task-center" '
        'aria-expanded="false" title="打开任务中心">'
        '<span class="mj-task-label">任务</span>'
        '<span id="mj-task-center-count" class="mj-task-count" hidden></span></button>',
        _mode_controls(mode, show_terms),
        _workspace_switcher(project_name),
        '<button type="button" id="mj-quit-btn" class="mj-quit-button" '
        'title="安全退出 Manju">退出</button>',
        "</div></div>",
        '<div class="pnav-stagebar" aria-label="制作阶段">',
    ]

    for gname, hrefs in _NAV_GROUPS:
        visible = [h for h in hrefs if not (beginner and h in PRO_ONLY_PAGES)]
        if not visible:
            continue
        is_active = active in hrefs
        cls = "pnav-stage active" if is_active else "pnav-stage"
        # A single-page stage is the current page; multi-page stages are the
        # current production step while a page link (or in-page journey) owns
        # aria-current=page.
        current = ""
        if is_active:
            current = ' aria-current="page"' if len(hrefs) == 1 else ' aria-current="step"'
        out.append(
            f'<a class="{cls}"{current} href="{visible[0]}">{_e(gname)}</a>'
        )
    out.append("</div>")

    if active_group is not None and active not in _INLINE_STAGE_NAV:
        gname, hrefs = active_group
        visible = [h for h in hrefs if not (beginner and h in PRO_ONLY_PAGES)]
        hidden_current = active not in visible
        # A lone visible destination needs no second row unless it must also
        # explain that the current deep-linked page is a hidden pro surface.
        if len(visible) > 1 or hidden_current:
            out.append(
                f'<div class="pnav-subbar" aria-label="{_e(gname)}阶段页面">'
                f'<span class="pnav-subtitle">{_e(gname)}</span>'
            )
            for href in visible:
                cls = "active" if href == active else ""
                cur = ' aria-current="page"' if href == active else ""
                out.append(
                    f'<a class="{cls}"{cur} href="{href}">{_e(label_of[href])}</a>'
                )
            if hidden_current:
                out.append(
                    '<span class="pnav-hidden-current" aria-current="page">'
                    f'{_e(label_of.get(active, active))}<small>专业功能</small></span>'
                )
            out.append("</div>")

    out.append("</nav>")
    # The old banner followed users across every page until dismissal.  One
    # compact home hint is enough; the view menu remains visible everywhere.
    if beginner and not hint_dismissed and active == "/":
        out.append(_mode_hint())
    return "".join(out)


def _shell(title: str, token: str, active: str, body: str, project: Any = None) -> str:
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
        + GLOSSARY_HEAD
        + '<script src="/common.js" defer></script>\n<script src="/pages.js" defer></script>\n'
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
    """Dispatch a page path to its renderer. ``query`` is the parsed query dict."""

    def one(key: str) -> str | None:
        v = query.get(key)
        return v[0] if v else None

    if path == "/review":
        return render_review(project, token)
    if path == "/compare":
        return render_compare(project, token, one("a"), one("b"))
    if path == "/library":
        return render_library(project, token, one("tag"), one("kind"), one("shot"),
                              one("orphans"))
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
            cls: str = "", *, lazy: bool = False) -> str:
    """A <video> or <img> element for a media url, degrading to a muted note."""
    if not url:
        return '<p class="muted">没有可播放的媒体</p>'
    if ext in ("png", "jpg", "jpeg", "webp", "gif"):
        return f'<img class="{cls}" src="{_e(url)}" alt="">'
    p = f' poster="{_e(poster)}"' if poster else ""
    if lazy:
        return (f'<video class="{cls}" data-src="{_e(url)}"{p} '
                'controls preload="none"></video>')
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


# build/stale ShotState -> 中文 word + badge class (mirrors gui.storyboard's
# _STATE_ZH/_STATE_BADGE, kept as a small local copy — round X agent XF,
# queue-mode filter chips AND the per-card badge need it, and pages.py stays
# independent of storyboard.py's private vocabulary).
_RV_BUILDSTATE_ZH = {
    "missing": "无版本", "fresh": "最新", "stale": "待更新", "manual": "手动置入",
    "needs_selection": "待挑选", "broken": "缺媒体",
}
_RV_BUILDSTATE_BADGE = {
    "missing": "st-missing", "fresh": "st-fresh", "stale": "st-stale",
    "manual": "st-manual", "needs_selection": "st-needs", "broken": "st-broken",
}
_RV_REVIEWSTATE_ZH = {
    "needs_review": "待审",
    "approved": "已通过",
    "rejected": "已否决",
}
_RV_REVIEWSTATE_BADGE = {
    "needs_review": "st-needs",
    "approved": "st-fresh",
    "rejected": "st-broken",
}
_RV_QC_LEVEL_ZH = {
    "error": "错误",
    "warn": "警告",
    "warning": "警告",
    "info": "信息",
}
_RV_ANNOTATION_ZH = {
    "note": "备注",
    "issue": "问题",
    "blocker": "阻塞",
}
_RV_NEXT_ZH = {
    "broken": "当前媒体不可用，需要修复或换选",
    "missing": "还没有可审候选",
    "select": "请选择一个候选",
    "blocker": "有阻塞批注需要处理",
    "qc": "质检发现错误，需要复核",
    "newtake": "有新候选等待决定",
    "voice": "配音需要处理",
    "stale": "当前候选已过期，需要重做或确认保留",
    "review": "等待人工审片",
}


def _review_note_state(note: str | None) -> tuple[str, str, str, str]:
    """Return ``(kind, label, badge, rationale)`` for one take note.

    The persisted owner remains ``status.take_notes``.  The review page only
    separates the leading human verdict from its rationale for presentation;
    it never creates another review store.
    """

    raw = str(note or "").strip()
    if raw == "好":
        return "good", "推荐", "st-fresh", ""
    if raw.startswith("好 ·"):
        return "good", "推荐", "st-fresh", raw[3:].strip()
    if raw == "弃":
        return "reject", "不推荐", "st-broken", ""
    if raw.startswith("弃 ·"):
        return "reject", "不推荐", "st-broken", raw[3:].strip()
    if raw:
        return "note", "有备注", "st-manual", raw
    return "pending", "未评价", "st-needs", ""


def render_review(project: Any, token: str) -> str:
    shots = project.shot_ids()
    qc_by_shot, qc_err = _qc_by_shot(project)
    reviewed = 0
    reviewable = 0
    cards: list[str] = []
    rail_items: list[tuple[int, int, str]] = []

    # one staleness pass for the whole page (round X agent XF QUEUE filter
    # chips: 待选/待审/已通过/待更新 combine build/stale states + review_state).
    try:
        from ..build.stale import evaluate_all

        build_states = {st.shot_id: st.state.value for st in evaluate_all(project)}
    except Exception:
        build_states = {}
    # Intuitiveness wave: the ONE per-shot answer (build/status.py owner) —
    # computed with the same facts this page already renders; QC errors read
    # once for the whole page.
    try:
        from ..build.status import _qc_error_shots, shot_next_action

        _qc_errs = _qc_error_shots(project)
    except Exception:  # advice must never break the review page
        shot_next_action = None  # type: ignore[assignment]
        _qc_errs = frozenset()

    # round AA item 5 (#1): CAS token per shot — the note input below is
    # pre-filled with the CURRENT note text at render time (a human may sit on
    # this page before saving), so /pages.js echoes this back as expected_rev
    # on /api/take-note; a stale card refuses instead of clobbering.
    from ..core.writes import shot_text_hash

    for idx, sid in enumerate(shots):
        try:
            shot = project.load_shot(sid)
        except Exception:
            shot = None
        selected = shot.status.selected_take if shot else None
        take_notes = dict(shot.status.take_notes) if shot else {}
        current_note_raw = str(take_notes.get(selected) or "").strip() if selected else ""
        if selected:
            reviewable += 1
        if current_note_raw:
            reviewed += 1
        try:
            takes = project.takes(sid)
        except Exception:
            takes = []
        sel = next((t for t in takes if t.name == selected), None)
        alts = [t for t in takes if t.name != selected]
        build_state = build_states.get(sid, "missing")
        review_state = shot.status.review_state if shot else "needs_review"

        # large player of the selected take, poster = QC mid-frame when present
        frame = project.reports_dir / "frames" / f"{sid}.jpg"
        frame_url = None
        if frame.exists():
            frame_url = "/media/" + quote(project.relpath(frame), safe="/")
        if sel is not None:
            url, ext, thumb = _take_media(project, sel)
            # Every review card is a cheap shell. The JS activates exactly one
            # focal player, so a long queue never opens all media streams at
            # first paint.
            player = _player(url, ext, frame_url, cls="rv-video", lazy=True)
        else:
            player = '<p class="muted">尚未选择候选。</p>'

        # QC findings for this shot
        findings = qc_by_shot.get(sid, [])
        if findings:
            rows = []
            for f in findings:
                lvl = str(f.get("level") or "info")
                msg = _e(f.get("message"))
                sug = f.get("suggestion")
                sug_html = f'<div class="rv-qc-sug muted">↳ {_e(sug)}</div>' if sug else ""
                level_label = _RV_QC_LEVEL_ZH.get(lvl, lvl)
                rows.append(
                    f'<li class="rv-qc-item lvl-{_e(lvl)}"><span class="badge st-{_qc_badge(lvl)}">'
                    f'<span title="{_e(lvl)}">{_e(level_label)}</span></span> '
                    f'{msg}{sug_html}</li>'
                )
            qc_html = '<ul class="rv-qc">' + "".join(rows) + "</ul>"
        else:
            qc_html = '<p class="muted rv-qc">当前选择未发现质检问题。</p>'

        # UX audit F17: the board's media-bound annotations were INVISIBLE on
        # this richer review page — a blocker pinned to an exact frame
        # vanished when the owner came here to act on it. Read-only mirror of
        # the board's list: the same severity vocabulary, the ONE staleness
        # rule (Annotation.matches_media — the stored media_sha256 vs the
        # take file's CURRENT hash), and a frame chip that seeks the player.
        ann_html = ""
        has_live_blocker = False
        shot_status = getattr(shot, "status", None) if shot else None
        anns = [a for a in (getattr(shot_status, "annotations", None) or [])
                if selected and a.take == selected]
        if anns:
            from ..core.hashing import hash_file as _hash_file

            current = None
            info = project.get_take(sid, selected)
            if (info is not None and info.media_path is not None
                    and info.media_path.is_file()):
                try:
                    current = _hash_file(info.media_path)
                except OSError:
                    current = None
            ann_rows = []
            for ann in anns:
                sev = ann.severity if ann.severity in ("note", "issue", "blocker") else "note"
                sev_label = _RV_ANNOTATION_ZH.get(sev, sev)
                chips = [f'<span class="badge rv-ann-{sev}" title="{_e(sev)}">'
                         f'{_e(sev_label)}</span>']
                if ann.frame is not None and ann.frame_rate:
                    m = re.fullmatch(r"(\d+)(?:/(\d+))?", str(ann.frame_rate))
                    if m:
                        num, den = int(m.group(1)), int(m.group(2) or 1)
                        sec = ann.frame * den / num if num else 0.0
                        chips.append(
                            f'<span class="rv-ann-seek" data-seek="{sec:.3f}" '
                            f'title="点击跳到对应画面">第 {ann.frame} 帧 · {sec:.3f}s</span>')
                matches_current = ann.matches_media(current)
                if sev == "blocker" and matches_current:
                    has_live_blocker = True
                if not matches_current:
                    chips.append('<span class="badge rv-ann-stale" title="绑定的媒体'
                                 '哈希不再匹配 — 该候选媒体已被替换或重做，批注指向'
                                 '旧画面">⚠ 已过期</span>')
                subject = (f"[{_e(ann.subject)}] " if ann.subject else "")
                ann_rows.append(
                    '<li class="rv-ann-item">'
                    f'{"".join(chips)} {subject}{_e(ann.text)}'
                    f'<div class="muted rv-ann-meta">{_e(ann.actor)} · {_e(ann.created_at)}</div>'
                    "</li>")
            ann_html = ('<div class="rv-anns"><span class="muted">看板批注</span>'
                        f'<ul>{"".join(ann_rows)}</ul></div>')

        frame_html = (
            '<details class="rv-frame">'
            '<summary>查看质检抽帧</summary>'
            f'<img src="{_e(frame_url)}" alt="{_e(sid)} 的质检抽帧">'
            '</details>'
            if frame_url else ""
        )

        # prior takes as small alternates — round X agent XF (pain #8): each
        # alt now carries a LAZY preview (data-src only, never src=) behind a
        # ▶ button, so the review page's initial GET never triggers ffmpeg
        # for takes that are not even the focal player (never block page GET
        # on ffmpeg). Clicking ▶ reveals the small <video> and assigns src.
        alt_html = ""
        if alts:
            items = []
            for t in alts:
                url, _x, thumb = _take_media(project, t)
                note = take_notes.get(t.name)
                note_html = f'<span class="rv-alt-note" title="{_e(note)}">📝</span>' if note else ""
                thumb_img = (f'<img src="{_e(thumb)}" alt="">' if thumb
                             else '<div class="rv-alt-noimg"></div>')
                play_btn = (f'<button type="button" class="rv-alt-play" data-act="preview" '
                            f'title="预览(懒加载)">▶</button>' if url else "")
                video_el = (f'<video class="rv-alt-video hidden" muted controls preload="none" '
                           f'data-src="{_e(url)}"></video>' if url else "")
                items.append(
                    f'<div class="rv-alt" data-take="{_e(t.name)}">'
                    f'<div class="rv-alt-media">{thumb_img}{play_btn}{video_el}</div>'
                    f'<button class="btn ghost mini" data-act="select" '
                    f'data-take="{_e(t.name)}">选用 {_e(t.name)}</button>{note_html}</div>'
                )
            alt_html = (
                '<section class="rv-inspector-block rv-alts">'
                '<div class="rv-inspector-title"><b>其它候选</b>'
                '<span class="muted">更换当前选择，不等于锁片</span></div>'
                '<div class="rv-alts-row">' + "".join(items) + "</div></section>"
            )

        repair_btns = "".join(
            f'<button class="btn ghost mini" data-act="repair" '
            + " ".join(f'{k}="{_e(v)}"' for k, v in attrs.items())
            + f">{_e(label)}</button>"
            for _op, label, attrs in _REPAIR_OPS
        )

        verdict_kind, verdict_label, verdict_badge, note_rationale = _review_note_state(
            current_note_raw
        )
        note_val = _e(note_rationale)
        state_badge = (f'<span class="badge st-{_state_badge(shot.status)}">'
                       f'{_e(_state_word(shot))}</span>' if shot else "")
        build_badge = (f'<span class="badge {_RV_BUILDSTATE_BADGE.get(build_state, "st-missing")}" '
                       f'title="状态(版本新鲜度)">{_e(_RV_BUILDSTATE_ZH.get(build_state, build_state))}'
                       f'</span>')
        action = _e(shot.action.main) if shot else ""
        dialogue = _e(shot.dialogue.text) if shot else ""
        reviewed_attr = "1" if current_note_raw else "0"
        has_qc_error = any(str(f.get("level")) == "error" for f in findings)
        review_label = _RV_REVIEWSTATE_ZH.get(review_state, review_state)
        review_badge = _RV_REVIEWSTATE_BADGE.get(review_state, "st-missing")
        selected_label = selected or "尚未选择"

        if not selected and build_state == "needs_selection":
            rail_label, rail_cls = "待选", "needs-selection"
        elif not selected:
            rail_label, rail_cls = "无候选", "missing"
        elif build_state == "stale":
            rail_label, rail_cls = "待更新", "stale"
        elif has_live_blocker:
            rail_label, rail_cls = "有阻塞", "blocker"
        elif has_qc_error:
            rail_label, rail_cls = "需复核", "qc-error"
        elif review_state == "approved":
            rail_label, rail_cls = "已通过", "approved"
        elif reviewed_attr == "1":
            rail_label, rail_cls = "已评价", "reviewed"
        else:
            rail_label, rail_cls = "待审", "needs-review"
        if build_state == "needs_selection":
            rail_priority = 0
        elif build_state == "stale":
            rail_priority = 1
        elif has_live_blocker:
            rail_priority = 2
        elif has_qc_error:
            rail_priority = 2
        elif selected and reviewed_attr != "1":
            rail_priority = 3
        elif selected and review_state != "approved":
            rail_priority = 4
        elif not selected:
            rail_priority = 5
        else:
            rail_priority = 6
        rail_html = (
            '<button type="button" class="rv-rail-item'
            + (' reviewed' if reviewed_attr == "1" else '')
            + f'" aria-label="{_e(sid)}，{action or "未填写镜头动作"}，{_e(rail_label)}" '
            f'data-rv-target="{_e(sid)}" data-reviewed="{reviewed_attr}" '
            f'data-buildstate="{_e(build_state)}" data-review="{_e(review_state)}" '
            f'data-take="{_e(selected or "")}" data-qc="{"1" if has_qc_error else "0"}" '
            f'data-blocker="{"1" if has_live_blocker else "0"}" '
            f'data-verdict="{_e(verdict_kind)}">'
            f'<span class="rv-rail-index">{idx + 1:02d}</span>'
            f'<span class="rv-rail-copy"><strong>{_e(sid)}</strong>'
            f'<small>{action or "未填写镜头动作"}</small></span>'
            f'<span class="rv-rail-status {rail_cls}">{_e(rail_label)}</span>'
            '</button>'
        )
        rail_items.append((rail_priority, idx, rail_html))

        # the ONE per-shot answer, only when there IS something to do
        next_html = ""
        next_detail_html = ""
        if shot_next_action is not None:
            try:
                act = shot_next_action(project, sid, state=build_state,
                                       selected_take=selected,
                                       qc_error_shots=_qc_errs)
                if act["key"] != "ok":
                    next_label = _RV_NEXT_ZH.get(act["key"], "这个镜头需要处理")
                    next_html = (
                        f'<div class="rv-next" title="{_e(act["action"])}">'
                        f'<b>需要处理</b><span>{_e(next_label)}</span></div>'
                    )
                    next_detail_html = (
                        '<div class="rv-next-detail"><b>系统建议</b>'
                        f'<code>{_e(act["action"])}</code></div>'
                    )
            except Exception:
                next_html = ""

        rev = shot_text_hash(project, sid) if shot else ""
        decision_disabled = ' disabled aria-disabled="true"' if not selected else ""
        primary_label = "先选择候选" if not selected else "推荐并下一条"
        approval_disabled = (
            ' disabled aria-disabled="true"'
            if not selected or review_state == "approved"
            else ""
        )
        approval_label = "镜头已通过" if review_state == "approved" else "确认镜头通过"
        clear_verdict_html = (
            '<button class="btn ghost rv-clear-verdict" data-act="clear-verdict"'
            + (' hidden' if not current_note_raw else '')
            + '>清除当前评价</button>'
            if selected else ""
        )

        cards.append(
            f'<section class="rv-shot panel{" reviewed" if reviewed_attr == "1" else ""}" '
            f'id="rv-{_e(sid)}" data-shot="{_e(sid)}" '
            f'data-take="{_e(selected or "")}" data-reviewed="{reviewed_attr}" '
            f'data-buildstate="{_e(build_state)}" data-review="{_e(review_state)}" '
            f'data-qc="{"1" if has_qc_error else "0"}" '
            f'data-blocker="{"1" if has_live_blocker else "0"}" '
            f'data-verdict="{_e(verdict_kind)}" data-note-raw="{_e(current_note_raw)}" '
            f'data-rev="{_e(rev)}">\n'
            f'  <div class="rv-head"><h2><a class="rv-lablink" '
            f'href="/lab?shot={_e(sid)}" title="打开镜头实验室 (lab)">{_e(sid)}</a> '
            f'{state_badge}{build_badge}'
            f'<span class="rv-idx muted">#{idx + 1}</span></h2>'
            f'<div class="rv-meta muted">{action}{" · 台词:" + dialogue if dialogue else ""}</div>'
            f"{next_html}"
            f'</div>\n'
            f'  <div class="rv-decision-state" aria-label="当前审片决定状态">'
            f'    <div class="rv-decision-cell selection" data-role="selection">'
            f'      <span>当前选择</span><strong title="{_e(selected_label)}">{_e(selected_label)}</strong>'
            f'    </div>'
            f'    <div class="rv-decision-cell evaluation verdict-{_e(verdict_kind)}" data-role="evaluation">'
            f'      <span>本次评价</span>'
            f'      <strong class="rv-verdict-state {verdict_badge}">{_e(verdict_label)}</strong>'
            f'    </div>'
            f'    <div class="rv-decision-cell approval review-{_e(review_state)}" data-role="approval">'
            f'      <span>镜头审批</span>'
            f'      <strong class="rv-review-state {review_badge}">{_e(review_label)}</strong>'
            f'    </div>'
            f'  </div>\n'
            f'  <div class="rv-actions rv-decision-bar">\n'
            f'    <div class="rv-decision-main">'
            f'      <button class="btn rv-primary-action" data-act="good" '
            f'title="快捷键 G；只记录推荐评价，不会自动选择、审批或锁片"{decision_disabled}>'
            f'{_e(primary_label)}</button>'
            f'      <button class="btn ghost rv-reject-action" data-act="reject" '
            f'title="快捷键 X；只记录不推荐评价，不会取消当前选择"{decision_disabled}>'
            f'不推荐，查看其它候选</button>'
            f'      <button class="btn ghost" data-act="skip" title="快捷键 j">稍后处理</button>'
            f'    </div>\n'
            f'    <div class="rv-decision-secondary">'
            f'      <button class="btn ghost" data-act="qapprove" '
            f'title="确认镜头层面的人工审批，不会锁片"{approval_disabled}>'
            f'{_e(approval_label)}</button>'
            + (f'      <a class="btn ghost" href="/?compare={_e(sid)}" '
               f'title="工作台 A/B 浮层：两个候选同步播放对比">对比候选</a>'
               if sel and alts else "")
            + f'    </div>\n'
            f"  </div>\n"
            f'  <div class="rv-body">\n'
            f'    <div class="rv-player-column">'
            f'      <div class="rv-player">{player}</div>'
            f'      <p class="rv-player-note muted">推荐评价只记录判断，不会自动更换当前选择、审批镜头或锁片。</p>'
            f'    </div>\n'
            f'    <div class="rv-side">'
            f'      <section class="rv-inspector-block">'
            f'        <div class="rv-inspector-title"><b>质检与批注</b>'
            f'        <span class="muted">证据只针对当前选择</span></div>'
            f'        {qc_html}{ann_html}{frame_html}'
            f'      </section>'
            f'      {alt_html}'
            f'    </div>\n'
            f"  </div>\n"
            f'  <div class="rv-noterow">'
            f'<label for="rv-note-{_e(sid)}">审片备注</label>'
            f'<input id="rv-note-{_e(sid)}" class="rv-note-input" '
            f'placeholder="记录判断依据或后续处理" value="{note_val}">'
            f'<button class="btn ghost mini" data-act="note"{decision_disabled}>'
            f'保存备注</button></div>\n'
            f'  <details class="rv-more">'
            f'    <summary>更多操作</summary>'
            f'    <div class="rv-more-actions">'
            f'      <button class="btn ghost" data-act="redo">重新生成</button>'
            f'      <span class="rv-repair">本地修复：{repair_btns}</span>'
            f'      <button class="btn ghost" data-act="route">查看路由</button>'
            f'      {clear_verdict_html}'
            f'      <button class="btn ghost" data-act="ai-ctx" '
            f'title="复制该镜头的结构化上下文，交给外部 IDE 或 agent">复制给 IDE 助手</button>'
            f'    </div>'
            f'    {next_detail_html}'
            f'    <div class="rv-explain muted" hidden></div>'
            f'  </details>\n'
            f"</section>"
        )

    total = len(shots)
    err_line = f'<p class="err">{_e(qc_err)}</p>' if qc_err else ""
    if not shots:
        cards_html = '<p class="muted panel">项目还没有镜头 (no shots to review)。</p>'
        rail_html = ""
    else:
        cards_html = "\n".join(cards)
    pct = (reviewed * 100 // reviewable) if reviewable else 0
    waiting_selection = total - reviewable
    if shots:
        rail_html = _queue_bar_html(
            reviewed,
            reviewable,
            waiting_selection,
            pct,
            "".join(item for _priority, _idx, item in sorted(rail_items)),
        )

    body = (
        '<div class="page-h rv-page-head"><div><h1>审片 <span class="mj-en">Review</span></h1>'
        '<p class="muted">逐条判断当前' + tooltip_html("take", label="候选")
        + '；系统不会自动改变当前选择、镜头审批或锁片。</p>'
        '</div>'
        '<details class="rv-shortcuts" id="rv-shortcuts">'
        '<summary>快捷键 <kbd>?</kbd></summary>'
        '<div class="rv-shortcut-grid">'
        '<span><kbd>J</kbd> 下一条</span><span><kbd>K</kbd> 上一条</span>'
        '<span><kbd>G</kbd> 推荐</span><span><kbd>X</kbd> 不推荐</span>'
        '<span><kbd>A</kbd> 确认镜头通过</span>'
        '<span><kbd>U</kbd> 撤回评价</span>'
        '<span><kbd>Space</kbd> 播放 / 暂停</span></div></details>'
        '</div>\n'
        + err_line
        + '<div class="rv-decision-guide panel">'
        '<div><b>推荐 / 不推荐</b><span>只记录本次评价，不会换用候选。</span></div>'
        '<div><b>当前选择</b><span>只有显式选用其它候选，当前版本才会改变。</span></div>'
        '<div><b>镜头审批</b><span>确认镜头可以继续，但不会自动锁片。</span></div>'
        '<a class="rv-lock-guide" href="/edit">锁片在成片阶段完成 →</a>'
        '</div>'
        + '<div class="rv-theater"><div class="rv-stage">'
        + cards_html + '</div>' + rail_html + '</div>'
        + _consistency_section(project)
    )
    return _shell("审片", token, "/review", body, project)


# ------------------------------------------------ QUEUE mode (round X agent XF)
# Batch review by shot status (pain #7/#8): filter chips over the SAME state
# vocabulary the storyboard page's 状态/审批 columns already use (build/stale
# ShotState + the three-state review), plus a one-at-a-time flow layered on
# top of the existing per-card actions (never a second mutation path).

_RV_QUEUE_FILTERS = (
    ("all", "全部"),
    ("needs_selection", "待选"),
    ("needs_review", "待审"),
    ("approved", "已通过"),
    ("stale", "待更新"),
)


def _queue_bar_html(
    reviewed: int,
    reviewable: int,
    waiting_selection: int,
    pct: int,
    rail_items: str,
) -> str:
    """Render the persistent, read-only priority queue projection."""

    chips = "".join(
        f'<button type="button" class="filter-chip rv-qfilter{" active" if key == "all" else ""}" '
        f'data-filter="{_e(key)}" aria-pressed="{"true" if key == "all" else "false"}">'
        f'{_e(label)}</button>'
        for key, label in _RV_QUEUE_FILTERS
    )
    waiting_html = (
        f'<span class="rv-progress-context muted">另有 {waiting_selection} 个镜头尚未选择候选</span>'
        if waiting_selection
        else ""
    )
    return (
        '<aside class="rv-rail panel" aria-label="审片队列">'
        '<div class="rv-rail-details">'
        '<div class="rv-rail-heading"><span>审片队列</span>'
        f'<b id="rv-progress" aria-live="polite">当前候选已评价 {reviewed} / {reviewable}</b></div>'
        '<div class="rv-rail-body">'
        f'<span class="bar rv-bar" role="progressbar" aria-label="当前候选审片进度" '
        f'aria-valuemin="0" aria-valuemax="{reviewable}" aria-valuenow="{reviewed}">'
        f'<span class="bar-fill" id="rv-progress-fill" data-pct="{pct}" style="width:{pct}%"></span></span>'
        + waiting_html
        + f'<div class="rv-qfilters" aria-label="队列筛选">{chips}</div>'
        '<div class="rv-queue-ctl">'
        '<button type="button" class="btn ghost mini" id="rv-queue-toggle" aria-pressed="false">只看当前镜头</button>'
        '<span id="rv-queue-pos" class="rv-queue-pos muted"></span>'
        '<button type="button" class="btn ghost mini" id="rv-q-prev" title="上一条 (K)">‹</button>'
        '<button type="button" class="btn ghost mini" id="rv-q-next" title="下一条 (J)">›</button>'
        '</div>'
        f'<div class="rv-rail-list">{rail_items}</div>'
        '<details class="rv-rail-advanced"><summary>批量操作</summary>'
        '<button type="button" class="btn ghost mini" id="rv-redo-stale" '
        'title="把所有待更新镜头一次性重做；仍需明确确认可能产生的费用">'
        '批量重做待更新</button></details>'
        '</div></div></aside>'
    )


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
    matrix/board never breaks the review page.

    Audit G1 (OPT-GUI): the unit STRUCTURE renders inline and CHEAPLY here
    (``compose_boards=False`` — no ffprobe/ffmpeg on the request thread), so the
    verdict flow is usable at first paint; each unit's contact-sheet BOARD (the
    ffprobe+ffmpeg cost that made /review 16.6s at 40 shots) is a LAZY slot the
    page JS fills from ``POST /api/review/consistency`` after first paint (the
    SAME render-structure-inline / lazy-the-media discipline the alt-take
    previews above already use)."""
    try:
        from ..qc.agent_review import qc_brief as _qc_brief

        brief = _qc_brief(project, mode="consistency", compose_boards=False)
    except Exception as exc:
        return (
            '<div class="page-h"><h2>跨镜一致性 Consistency</h2></div>\n'
            f'<p class="err panel">一致性组合加载失败:{_e(exc)}</p>'
        )

    units = brief.get("units") or []
    coverage = ((brief.get("coverage") or {}).get("units")) or {}
    skipped = brief.get("skipped") or []

    # The criteria text is per unit KIND, not per unit — a handful of distinct
    # paragraphs. Printing the full ~200-character note on every card meant a
    # 12-pair film rendered the same wall of text a dozen times and the page
    # became mostly repetition, which is how a reader stops reading it at all.
    # State it ONCE above the cards; each card keeps only its short section
    # label (C/D…), which the legend explains.
    legend: dict[str, str] = {}
    for u in units:
        crit = u.get("criteria") or {}
        key = str(crit.get("sections") or "").strip()
        if key and key not in legend:
            legend[key] = str(crit.get("note") or "").strip()
    legend_html = ""
    if legend:
        rows = "".join(f"<li><b>{_e(k)}</b> — {_e(v)}</li>"
                       for k, v in legend.items())
        legend_html = (
            f'<details class="panel cs-legend"><summary>判据说明 · {len(legend)} 组'
            f'(适用于下方全部组合,展开看细则)</summary><ul>{rows}</ul></details>\n')

    if not units:
        body = ('<p class="muted panel">暂无可判读的一致性组合(需要至少两个共享角色/'
                '场景、且已选候选的镜头)。</p>')
    else:
        body = legend_html + "\n".join(
            _consistency_card(u, coverage.get(u["unit"], {})) for u in units)

    skip_html = ""
    if skipped:
        items = "".join(
            f'<li>{_e(s.get("unit", ""))}:{_e(s.get("reason", ""))}</li>' for s in skipped
        )
        skip_html = (f'<details class="muted cs-skipped"><summary>跳过 {len(skipped)} '
                    f'个组合</summary><ul>{items}</ul></details>')

    content = (
        '<div class="page-h"><h2>跨镜一致性 Consistency</h2>'
        '<span class="muted">角色出场对照表 · 相邻镜头场景对比 · 场景整体看板 — '
        '一致性是跨镜属性,不逐镜孤立判读;人工可在此现场提交裁决</span></div>\n'
        + body + skip_html
    )
    return (
        '<details class="panel rv-pro-section">'
        '<summary>专业：跨镜一致性 · 展开后加载看板</summary>'
        + content + '</details>'
    )


def _consistency_card(unit: dict, cov: dict) -> str:
    # G1: the contact-sheet board is LAZY — a placeholder slot the page JS fills
    # from POST /api/review/consistency after first paint (see _consistency_section
    # and /pages.js initReviewConsistencyBoards). Never composed on the /review
    # GET, so the request thread spawns no ffprobe/ffmpeg.
    img_html = ('<div class="cs-board-slot" data-cs-board="1">'
                '<span class="muted">正在加载看板…</span></div>')
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
        # Short label only — the full note lives once in the section legend
        # (see _consistency_section), instead of on all N cards.
        f'  <div class="muted">判据 {_e(criteria.get("sections"))}'
        f'  <span class="cs-crit-hint">(细则见上方「判据说明」)</span></div>'
        f'  {img_html}'
        f'  <div class="cs-form btnrow">'
        f'    <select class="cs-criterion">{crit_opts}</select>'
        f'    <select class="cs-level">'
        f'      <option value="fyi">提示</option>'
        f'      <option value="issue">问题</option>'
        f'      <option value="blocker">阻塞</option>'
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
        return _shell("对比", token, "/compare", body, project)

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
        return _shell("对比", token, "/compare", body, project)

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
            '<div class="cmp-degraded panel"><span class="badge st-stale">降级模式</span> '
            + _e(diff.get("note") or "per-shot detail unavailable (pre-S final)")
            + "</div>"
        )
        body = head + picker + note + summary + players
        return _shell("对比", token, "/compare", body, project)

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
    return _shell("对比", token, "/compare", body, project)


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


def _lib_card_html(a: dict[str, Any], hash8: str, *, matched: list[str] | None = None) -> str:
    """One library asset card. Shared by the main grid and the 素材库建议 strip
    (round X agent XF, ``?shot=``) — ``matched`` (present only on a
    suggestion) adds a "命中" line naming which tag(s) matched, so a
    reviewer sees exactly why the asset was suggested."""
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
    matched_html = (f'<div class="lib-matched">命中: {_e("、".join(matched))}</div>'
                    if matched else "")
    return (
        f'<div class="lib-card panel" data-hash="{_e(hash8)}">'
        f"{thumb_html}"
        f'<div class="lib-name" title="{_e(a.get("name"))}">{_e(a.get("name"))}</div>'
        f'<div class="lib-meta muted">{_e(a.get("kind"))} · {size_mb:.2f}MB · {_e(hash8)}</div>'
        f"{matched_html}"
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


def _library_shot_suggestions_html(project: Any, shot_id: str) -> str:
    """素材库建议 strip for one shot (round X agent XF, pain #7/#8, ``?shot=``
    on /library): the SAME deterministic tag/kind matches the shot lab's 参考
    panel offers, reusing the identical card + 用到项目(refs) adopt action —
    just reachable from the library's own browse page too. Degrades to a
    single muted line (never an error) on a broken shot/library."""
    from ..core.library import _hex

    try:
        shot = project.load_shot(shot_id)
    except Exception:
        return (f'<div class="lib-suggest panel"><span class="err">'
                f'未找到镜头 {_e(shot_id)}</span></div>')
    try:
        from ..core.library import suggest_from_library

        rows = suggest_from_library(project, shot)
    except Exception:
        rows = []
    if not rows:
        body = '<p class="muted">没有匹配该镜头角色/场景标签的素材库项。</p>'
    else:
        cards = "".join(
            _lib_card_html(r, r["hash8"], matched=r.get("matched"))
            for r in rows
        )
        body = f'<div class="lib-grid">{cards}</div>'
    return (
        '<div class="lib-suggest panel">'
        f'<h2>为镜头 {_e(shot_id)} 推荐 <a class="filter-chip" href="/library">清除</a></h2>'
        f"{body}</div>"
    )


def render_library(project: Any, token: str, tag: str | None, kind: str | None,
                   shot: str | None = None, refs_orphans: str | None = None) -> str:
    from ..core.library import Library, LibraryError, _hex

    head = ('<div class="page-h"><h1>素材库 Library</h1>'
            '<span class="muted">个人素材(~/.manju/library)· 内容寻址去重</span></div>')
    lib = Library()
    try:
        all_assets = lib.assets()
    except LibraryError as exc:
        return _shell("素材库", token, "/library",
                      head + f'<p class="err panel">{_e(exc)}</p>', project)

    suggest_html = _library_shot_suggestions_html(project, shot) if shot else ""

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
        cards = [_lib_card_html(a, _hex(a["hash"])[:8]) for a in assets]
        grid = '<div class="lib-grid">' + "".join(cards) + "</div>"

    body = (head + f'<p class="muted">{_e(str(lib.root))}</p>' + suggest_html
            + filters + upload + grid
            + _refs_section_html(project, orphans_only=bool(refs_orphans)))
    return _shell("素材库", token, "/library", body, project)


# ================================================ media/refs ownership (§3)
# Round AA goal item 3 (GUI half): core/refs.py's refs_report/assign_ref, one
# read-only section appended to the SAME /library page (not a new route, not
# a rebuild of the personal-library grid above — the task's own "do not
# rebuild the library page" constraint). Every row here is, by definition,
# REFERENCE material (media/refs) — never a project's generated/imported
# PRODUCTION media (renders/, media/imports/), which this page's personal
# library grid above already mixes with no source metadata of its own to
# label; the distinction is made visible here instead (§ the section header).


def _refs_file_options_html(rows: list[dict[str, Any]]) -> str:
    opts = ['<option value="">选择参考文件… (choose a file)</option>']
    for r in rows:
        label = r["file"] + ("(孤儿)" if r["orphan"] else "")
        opts.append(f'<option value="{_e(r["file"])}">{_e(label)}</option>')
    return "".join(opts)


def _refs_owner_options_html(project: Any) -> str:
    """Known-id picker options for 关联 (assign): every shot id, grouped
    separately from every bible character/scene/prop id — a plain
    ``<select>``/``<optgroup>``, no fancy autocomplete (goal item 3's own
    scope note). Each option value is ``"<kind>:<id>"``; the page JS splits
    on the first ``:`` and POSTs the right ``shot=``/``character=``/
    ``scene=``/``prop=`` field to ``/api/refs/assign``."""
    from ..core.refs import bible_owner_map

    shot_opts = "".join(
        f'<option value="shot:{_e(s)}">{_e(s)}</option>' for s in sorted(project.shot_ids())
    )
    groups = []
    if shot_opts:
        groups.append(f'<optgroup label="镜头 (shots)">{shot_opts}</optgroup>')

    owner_map = bible_owner_map(project)
    by_kind: dict[str, list[str]] = {"characters": [], "scenes": [], "props": []}
    for asset_id, bible_file in owner_map.items():
        if bible_file in by_kind:
            by_kind[bible_file].append(asset_id)
    kind_label = {"characters": "角色 (characters)", "scenes": "场景 (scenes)",
                  "props": "道具 (props)"}
    kind_param = {"characters": "character", "scenes": "scene", "props": "prop"}
    for bible_file in ("characters", "scenes", "props"):
        ids = sorted(by_kind[bible_file])
        if not ids:
            continue
        opts = "".join(
            f'<option value="{kind_param[bible_file]}:{_e(aid)}">{_e(aid)}</option>'
            for aid in ids
        )
        groups.append(f'<optgroup label="{kind_label[bible_file]}">{opts}</optgroup>')
    return "".join(groups)


def _refs_section_html(project: Any, orphans_only: bool) -> str:
    from ..core.refs import refs_report

    try:
        report = refs_report(project)
    except Exception as exc:
        return (
            '<div class="refs-section">'
            '<div class="page-h"><h2>参考素材归属 (media/refs ownership)</h2></div>'
            f'<p class="err panel">{_e(exc)}</p></div>'
        )

    rows = report["files"]
    if orphans_only:
        rows = [r for r in rows if r["orphan"]]

    filters = (
        '<div class="lib-filters panel"><div class="lib-frow">'
        f'<a class="filter-chip{"" if orphans_only else " active"}" href="/library">全部</a>'
        f'<a class="filter-chip{" active" if orphans_only else ""}" href="/library?orphans=1">'
        f'只看孤儿 (orphans only) · {report["orphan_count"]}</a>'
        '</div></div>'
    )

    if not rows:
        table_html = ('<p class="muted">没有孤儿参考文件 (no orphan refs)。</p>' if orphans_only
                      else '<p class="muted">参考素材目录为空。</p>')
    else:
        trs = []
        for r in rows:
            rel = r["file"]
            thumb = ""
            if r["kind"] in ("image", "video"):
                thumb = (f'<img class="refs-thumb" src="/thumb/{quote(rel, safe="/")}" '
                         'alt="" loading="lazy">')
            owners = "、".join(r["owners"]) if r["owners"] else "—"
            trs.append(
                "<tr>"
                f'<td>{thumb}</td>'
                f'<td class="refs-file" title="{_e(rel)}">{_e(rel)}</td>'
                f'<td>{_e(r["kind"])}</td>'
                f'<td>{_e(r["role"])}</td>'
                f'<td>{_e(owners)}</td>'
                f'<td>{"是" if r["bible_pinned"] else "否"}</td>'
                f'<td>{"孤儿" if r["orphan"] else "—"}</td>'
                "</tr>"
            )
        table_html = (
            '<div class="tablewrap"><table class="cmp-table"><thead><tr>'
            "<th>预览</th><th>文件</th><th>类型</th><th>角色</th><th>归属</th>"
            "<th>bible 已固定</th><th>孤儿</th>"
            "</tr></thead><tbody>" + "".join(trs) + "</tbody></table></div>"
        )

    missing = report["missing"]
    if missing:
        items = "".join(
            f'<li>{_e(m["bible_file"])}:{_e(m["asset_id"])} · {_e(m["field"])} → '
            f'{_e(m["value"])} （文件不存在）</li>'
            for m in missing
        )
        missing_html = (
            '<div class="refs-missing"><h3>失效的素材指向</h3>'
            f'<ul>{items}</ul></div>'
        )
    else:
        missing_html = '<p class="muted">没有失效的角色、场景或道具指向。</p>'

    owner_options = _refs_owner_options_html(project)
    assign_form = (
        '<div class="refs-assign panel">'
        '<h3>关联 (assign)</h3>'
        '<p class="muted">把一个 media/refs 文件关联到镜头或 bible 资产'
        '(重命名为 {id}_ref 约定,并按需回写 bible ref_image;已有的 bible 指向'
        '会自动跟着改名)。</p>'
        '<div class="refs-assign-row">'
        f'<select id="refs-assign-file">{_refs_file_options_html(report["files"])}</select>'
        f'<select id="refs-assign-target">{owner_options}</select>'
        '<button type="button" class="btn mini" id="refs-assign-btn">关联</button>'
        '</div><div id="refs-assign-out" class="muted"></div>'
        '</div>'
    ) if report["files"] else ""

    return (
        '<div class="refs-section">'
        '<div class="page-h"><h2>参考素材归属 (media/refs ownership) '
        '<span class="muted">参考素材 (reference) — 与生成/导入的成品素材 '
        '(production media, renders/imports) 分开管理</span></h2></div>'
        f'<p class="muted">{_e(report["note"])}</p>'
        f"{filters}"
        f'<div class="panel">{table_html}</div>'
        f'<div class="panel">{missing_html}</div>'
        + assign_form +
        "</div>"
    )


# ============================================================ 服务商 providers


def _adapter_short(adapter: str) -> str:
    from ..providers.manifest import ADAPTER_ALIASES

    for alias, full in ADAPTER_ALIASES.items():
        if full == adapter:
            return alias
    return adapter.split(":")[-1] if ":" in adapter else adapter


def _provider_rows() -> tuple[list[dict], list[str]]:
    from ..providers.manifest import fix_hint, load_manifests
    from ..providers.zero_cost import credential_presence

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
            "key_set": credential_presence(key_env),
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
                      head + f'<p class="err panel">{_e(exc)}</p>', project)

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
        status_badge = ('<span class="badge st-fresh">已启用</span>' if enabled
                        else '<span class="badge st-missing">未启用</span>')
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
    return _shell("服务商", token, "/providers", body, project)


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
                      head + f'<p class="err panel">{_e(exc)}</p>', project)

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
        badge = '<span class="badge st-fresh">当前生效</span>' if act else ""
        tag = "内置" if s["builtin"] else "自定义"
        pr = f' · priority={_e(s["priority"])}' if s.get("priority") else ""
        cards.append(
            f'<div class="rt-card panel{" rt-active" if act else ""}">'
            f'<h3>{_e(s["name"])} {badge} <span class="muted">[{tag}]</span></h3>'
            f'<div class="muted">rules={_e(s["rules"])} · else={_e(s["else"])}{pr}</div></div>'
        )

    body = (head + f'<p class="muted">{src_line}</p>' + picker + explain_panel
            + '<div class="rt-cards">' + "".join(cards) + "</div>")
    return _shell("路由", token, "/routing", body, project)


# ============================================================ 体检 doctor


def render_doctor(project: Any, token: str) -> str:
    from ..build.doctor import run_doctor

    head = ('<div class="page-h"><h1>体检 Doctor</h1>'
            '<button class="btn ghost" id="dr-refresh">刷新</button></div>')
    try:
        report = run_doctor(project)
    except Exception as exc:
        return _shell("体检", token, "/doctor",
                      head + f'<p class="err panel">{_e(exc)}</p>', project)

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
    return _shell("体检", token, "/doctor", body, project)


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
  --pnav-app-h: 44px;
  --pnav-stage-h: 42px;
  background: color-mix(in srgb, var(--panel) 97%, #000);
  border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 60;
  box-shadow: 0 6px 18px rgba(0, 0, 0, .22);
}
.pnav a { color: var(--fg); text-decoration: none; }
.pnav-appbar {
  min-height: var(--pnav-app-h); display: flex; align-items: center;
  gap: .75rem; padding: .35rem 1rem .25rem;
}
.pnav-brand {
  display: inline-flex; align-items: center; gap: .5rem; flex: 0 0 auto;
  color: var(--fg); font-size: .88rem; font-weight: 750; letter-spacing: .01em;
}
.pnav-brand:hover { color: #fff; }
.pnav-brand-mark {
  display: inline-grid; place-items: center; width: 1.65rem; height: 1.65rem;
  border-radius: 8px; background: var(--accent); color: #0b1220;
  font-size: .78rem; font-weight: 900; box-shadow: inset 0 0 0 1px rgba(255,255,255,.22);
}
.pnav-tools {
  min-width: 0; margin-left: auto; display: flex; align-items: center;
  justify-content: flex-end; gap: .4rem;
}
.mj-execution-state {
  display: inline-flex; align-items: center; gap: .38rem; min-height: 30px;
  padding: .15rem .58rem; border: 1px solid var(--line); border-radius: 999px;
  background: var(--panel2); color: var(--muted); font-size: .75rem;
  white-space: nowrap;
}
.mj-execution-dot { width: .5rem; height: .5rem; border-radius: 999px; background: var(--muted); }
.mj-execution-state.strict { color: var(--ok); border-color: #2f5a43; background: #14261d; }
.mj-execution-state.strict .mj-execution-dot { background: var(--ok); }
.mj-execution-state.standard { color: var(--warn); border-color: #5e4c20; background: #2b2414; }
.mj-execution-state.standard .mj-execution-dot { background: var(--warn); }
.mj-execution-state.invalid { color: var(--err); border-color: #5a2b2e; background: #2a1618; }
.mj-execution-state.invalid .mj-execution-dot { background: var(--err); }
.mj-execution-mode { display: none; }
.pnav-stagebar {
  min-height: var(--pnav-stage-h); display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr)); gap: .22rem;
  padding: .18rem 1rem .42rem;
}
.pnav-stage {
  display: flex; align-items: center; justify-content: center; min-width: 0;
  padding: .38rem .5rem; border: 1px solid transparent; border-radius: 8px;
  color: var(--muted); font-size: .82rem; font-weight: 650;
  transition: background-color .12s ease, border-color .12s ease, color .12s ease;
}
.pnav-stage:hover { color: var(--fg); background: var(--panel2); }
.pnav-stage.active {
  color: var(--fg); border-color: color-mix(in srgb, var(--accent) 65%, var(--line));
  background: var(--accent-bg); box-shadow: inset 0 -2px 0 var(--accent);
}
.pnav-subbar {
  min-height: 36px; display: flex; align-items: center; gap: .3rem;
  padding: .28rem 1rem; overflow-x: auto; scrollbar-width: thin;
  background: var(--panel2); border-top: 1px solid var(--line);
}
.pnav-subtitle {
  flex: 0 0 auto; color: var(--muted); font-size: .7rem; font-weight: 750;
  letter-spacing: .08em; margin-right: .15rem;
}
.pnav-subbar a, .pnav-hidden-current {
  flex: 0 0 auto; display: inline-flex; align-items: center; gap: .3rem;
  padding: .22rem .58rem; border-radius: 6px; color: var(--muted);
  font-size: .78rem; white-space: nowrap;
}
.pnav-subbar a:hover { color: var(--fg); background: rgba(255,255,255,.04); }
.pnav-subbar a.active {
  color: var(--fg); background: rgba(116,169,255,.13); font-weight: 700;
}
.pnav-hidden-current { color: var(--warn); border: 1px solid #5e4c20; }
.pnav-hidden-current small {
  padding: .04rem .3rem; border-radius: 999px; background: #4a3a12;
  color: var(--warn); font-size: .62rem; font-weight: 700;
}

@media (max-width: 760px) {
  .pnav-appbar { min-height: 42px; gap: .4rem; padding: .3rem .65rem .2rem; }
  .pnav-brand-name { display: none; }
  .pnav-tools { gap: .28rem; }
  .mj-execution-state { min-height: 28px; padding: .12rem .46rem; font-size: .7rem; }
  .pnav-stagebar {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: .2rem; padding: .22rem .65rem .42rem;
  }
  .pnav-stage { min-height: 36px; padding: .3rem .35rem; }
  .pnav-subbar { min-height: 36px; padding: .25rem .65rem; }
  .pnav-subtitle { display: none; }
}
@media (max-width: 360px) {
  .pnav-appbar { align-items: flex-start; flex-wrap: wrap; }
  .pnav-brand-name { display: inline; }
  .pnav-tools {
    width: 100%; margin-left: 0; flex-wrap: wrap; justify-content: flex-start;
  }
  .mj-ws-wrap { flex: 1 1 8rem; }
  .mj-ws-btn { width: 100%; max-width: none; }
  .pnav-stagebar { padding-left: .45rem; padding-right: .45rem; }
  .pnav-subbar { white-space: normal; flex-wrap: wrap; overflow-x: visible; }
}
@media (prefers-reduced-motion: reduce) {
  .pnav-stage, .toast-item, .mj-toast-repeat { animation: none !important; transition: none !important; }
}

/* round X (agent XD): 剧集 series-membership banner, injected by chrome()
   right after the nav on every page that passes it a project. */
.mj-series-banner {
  display: flex; align-items: center; gap: .5rem; flex-wrap: wrap;
  padding: .4rem 1.2rem; background: var(--panel2); border-bottom: 1px solid var(--line);
  font-size: .84rem; color: var(--fg);
}
.mj-series-banner a {
  color: var(--accent); text-decoration: none; font-weight: 600;
}
.mj-series-banner a:hover { text-decoration: underline; }

.page-h { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; margin: 1.1rem 0 .5rem; }
.page-h h1 { font-size: 1.25rem; }

/* Product Polish R1 Wave 7: the shot-production path is intentionally
   BRANCHING, not a false four-step rail.  The lab and batch-ingest are two
   alternative ways to obtain append-only candidates; both converge on the
   existing human review owner.  This chrome is read-only. */
.mj-shot-journey {
  margin: .7rem 0 1rem; padding: .72rem .82rem;
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  box-shadow: var(--shadow-1);
}
.mj-shot-head, .mj-shot-foot {
  display: flex; align-items: center; justify-content: space-between;
  gap: .75rem; flex-wrap: wrap;
}
.mj-shot-current {
  display: flex; align-items: baseline; gap: .5rem; flex-wrap: wrap; min-width: 0;
}
.mj-shot-eyebrow {
  color: var(--muted); font-size: .72rem; font-weight: 700; letter-spacing: .08em;
}
.mj-shot-context {
  display: inline-flex; align-items: baseline; gap: .35rem; min-width: 0;
  padding-left: .5rem; border-left: 1px solid var(--line); font-size: .78rem;
}
.mj-shot-context span {
  color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: min(30rem, 45vw);
}
.mj-shot-stats { display: flex; gap: .35rem; flex-wrap: wrap; }
.mj-shot-stats span {
  display: inline-flex; align-items: baseline; gap: .2rem;
  padding: .2rem .45rem; border: 1px solid var(--line); border-radius: 999px;
  background: var(--panel2); color: var(--muted); font-size: .72rem;
}
.mj-shot-stats b { color: var(--fg); font-variant-numeric: tabular-nums; }
.mj-shot-track {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: .35rem; margin: .62rem 0 .52rem;
}
.mj-shot-step {
  display: flex; align-items: center; justify-content: center; gap: .42rem;
  min-width: 0; min-height: 2.55rem; padding: .38rem .52rem;
  border: 1px solid var(--line); border-radius: 8px; background: var(--panel2);
  color: var(--muted); text-decoration: none; font-size: .8rem; font-weight: 650;
}
a.mj-shot-step:hover { color: var(--fg); border-color: #4b5566; filter: none; }
.mj-shot-step.active {
  color: var(--fg); border-color: var(--accent); background: var(--accent-bg);
  box-shadow: inset 0 0 0 1px rgba(116,169,255,.18);
}
.mj-shot-num {
  display: inline-grid; place-items: center; flex: 0 0 auto; width: 1.32rem; height: 1.32rem;
  border-radius: 999px; background: rgba(255,255,255,.06);
  font-size: .68rem; font-variant-numeric: tabular-nums;
}
.mj-shot-step.active .mj-shot-num { background: var(--accent); color: #0b1220; }
.mj-shot-step-title { display: inline-flex; align-items: center; gap: .4rem; }
.mj-shot-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mj-shot-candidate { flex-wrap: wrap; row-gap: .18rem; }
.mj-shot-branches {
  display: inline-flex; align-items: center; gap: .28rem; color: var(--muted);
  font-size: .72rem; font-weight: 500;
}
.mj-shot-branches a { color: var(--muted); text-decoration: none; padding: .08rem .25rem; border-radius: 4px; }
.mj-shot-branches a:hover, .mj-shot-branches a.active { color: var(--fg); background: rgba(255,255,255,.07); }
.mj-shot-foot p { margin: 0; font-size: .76rem; min-width: min(34rem, 100%); }
.mj-shot-warning { color: var(--warn); margin-left: .35rem; }
.mj-shot-next { margin-left: auto; text-decoration: none; }
@media (max-width: 760px) {
  .mj-shot-journey { padding: .65rem; }
  .mj-shot-head { align-items: flex-start; }
  .mj-shot-current { align-items: flex-start; flex-direction: column; gap: .18rem; }
  .mj-shot-context { padding-left: 0; border-left: 0; width: 100%; }
  .mj-shot-context span { max-width: calc(100vw - 3.2rem); }
  .mj-shot-stats { width: 100%; }
  .mj-shot-stats span { flex: 1 1 calc(50% - .2rem); justify-content: center; }
  .mj-shot-track { gap: .25rem; }
  .mj-shot-step { min-height: 3.15rem; padding: .32rem .28rem; flex-direction: column; gap: .18rem; }
  .mj-shot-candidate { justify-content: center; }
  .mj-shot-step-title { gap: .25rem; }
  .mj-shot-branches { gap: .12rem; font-size: .67rem; }
  .mj-shot-foot { align-items: flex-start; }
  .mj-shot-next { width: 100%; margin-left: 0; text-align: center; }
}

/* Product Polish R1 Wave 4: one shared finishing journey across 剪辑 → 字幕
   → 混音 → 包装 → 导出.  It is presentation-only; the two status chips are
   filled lazily from existing export/readiness owners after first paint. */
.mj-finish-journey {
  margin: .75rem 0 1rem; padding: .75rem .85rem;
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  box-shadow: var(--shadow-1);
}
.mj-finish-head, .mj-finish-foot {
  display: flex; align-items: center; justify-content: space-between;
  gap: .8rem; flex-wrap: wrap;
}
.mj-finish-head > div:first-child {
  display: flex; align-items: baseline; gap: .55rem; flex-wrap: wrap; min-width: 0;
}
.mj-finish-eyebrow {
  color: var(--muted); font-size: .72rem; font-weight: 700; letter-spacing: .08em;
}
.mj-finish-status, .mj-finish-actions {
  display: flex; align-items: center; gap: .4rem; flex-wrap: wrap;
}
.mj-finish-track {
  display: grid; grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: .35rem; margin: .65rem 0 .55rem;
}
.mj-finish-step {
  display: flex; align-items: center; justify-content: center; gap: .42rem;
  min-width: 0; padding: .42rem .55rem;
  border: 1px solid var(--line); border-radius: 8px;
  background: var(--panel2); color: var(--muted); text-decoration: none;
  font-size: .82rem; font-weight: 600;
}
.mj-finish-step:hover { color: var(--fg); border-color: #4b5566; filter: none; }
.mj-finish-step.active {
  color: var(--fg); border-color: var(--accent); background: var(--accent-bg);
  box-shadow: inset 0 0 0 1px rgba(116,169,255,.18);
}
.mj-finish-num {
  display: inline-grid; place-items: center; flex: 0 0 auto;
  width: 1.35rem; height: 1.35rem; border-radius: 999px;
  background: rgba(255,255,255,.06); font-size: .7rem; font-variant-numeric: tabular-nums;
}
.mj-finish-step.active .mj-finish-num { background: var(--accent); color: #0b1220; }
.mj-finish-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mj-finish-note { margin: 0; font-size: .78rem; min-width: min(32rem, 100%); }
.mj-finish-note.warn { color: var(--warn); }
.mj-finish-note.bad { color: var(--err); }
@media (max-width: 760px) {
  .mj-finish-track { grid-template-columns: repeat(6, minmax(0, 1fr)); }
  .mj-finish-step { grid-column: span 2; }
  .mj-finish-step:nth-child(n + 4) { grid-column: span 3; }
  .mj-finish-head { align-items: flex-start; }
  .mj-finish-status { width: 100%; }
  .mj-finish-foot { align-items: flex-start; }
  .mj-finish-actions { width: 100%; justify-content: flex-end; }
}
.err { color: var(--err); }
.mini { padding: .1rem .5rem !important; font-size: .74rem !important; }

#toast {
  position: fixed; right: 1rem; bottom: 1rem; display: flex; flex-direction: column;
  gap: .45rem; z-index: 200; max-width: min(420px, calc(100vw - 1.25rem));
}
.toast-item {
  display: grid; grid-template-columns: minmax(0, 1fr) 2rem; align-items: start;
  gap: .55rem; background: var(--panel2); border: 1px solid var(--line);
  border-left-width: 4px; color: var(--fg); padding: .58rem .55rem .58rem .8rem;
  border-radius: 9px; font-size: .84rem; box-shadow: 0 6px 20px rgba(0,0,0,.5);
  animation: mj-rise .18s ease-out; overflow-wrap: anywhere;
}
.toast-item.good { border-color: #2c5a3f; border-left-color: var(--ok); }
.toast-item.warn { border-color: #826323; border-left-color: var(--warn); }
.toast-item.bad { border-color: #8a4247; border-left-color: var(--err); }
.mj-toast-copy { min-width: 0; padding-top: .12rem; }
.mj-toast-close {
  display: inline-grid; place-items: center; width: 2rem; height: 2rem; padding: 0;
  border: 1px solid transparent; border-radius: 7px; background: transparent;
  color: var(--muted); font: inherit; font-size: 1.05rem; cursor: pointer;
}
.mj-toast-close:hover { color: var(--fg); background: rgba(255,255,255,.06); }
.mj-toast-repeat { animation: mj-toast-repeat .22s ease-out; }
@keyframes mj-toast-repeat { 50% { transform: translateY(-2px); filter: brightness(1.18); } }
@media (max-width: 520px) {
  #toast { left: .55rem; right: .55rem; bottom: .55rem; max-width: none; }
}

/* ---------------------------------------------------------- review -- */
body[data-page="/review"] {
  --rv-nav-h: 88px;
}
.rv-page-head { align-items: flex-start; }
.rv-page-head h1 { margin-bottom: .15rem; }
.rv-page-head p { margin: .2rem 0 0; max-width: 48rem; }
.rv-shortcuts { margin-left: auto; position: relative; }
.rv-shortcuts > summary, .rv-rail-advanced > summary,
.rv-more > summary, .rv-frame > summary {
  cursor: pointer; color: var(--muted); font-size: .78rem; font-weight: 650;
}
.rv-shortcuts > summary {
  list-style: none; padding: .35rem .55rem; border: 1px solid var(--line); border-radius: 7px;
}
.rv-shortcuts > summary::-webkit-details-marker { display: none; }
.rv-shortcut-grid {
  position: absolute; right: 0; top: calc(100% + .4rem); z-index: 70;
  min-width: 270px; display: grid; grid-template-columns: 1fr 1fr; gap: .45rem .75rem;
  padding: .75rem; background: var(--panel2); border: 1px solid var(--line);
  border-radius: 10px; box-shadow: 0 12px 28px rgba(0,0,0,.42);
  color: var(--muted); font-size: .78rem;
}
.rv-decision-guide {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
  gap: .7rem; align-items: stretch; margin-top: 0; padding: .75rem;
  background: color-mix(in srgb, var(--panel) 88%, var(--accent) 12%);
}
.rv-decision-guide > div { display: flex; flex-direction: column; gap: .15rem; min-width: 0; }
.rv-decision-guide b { font-size: .84rem; }
.rv-decision-guide span { color: var(--muted); font-size: .77rem; line-height: 1.35; }
.rv-lock-guide { align-self: center; white-space: nowrap; font-size: .82rem; }
kbd {
  display: inline-flex; align-items: center; justify-content: center; min-width: 1.45rem;
  padding: .08rem .34rem; border: 1px solid var(--line); border-bottom-width: 2px;
  border-radius: 5px; background: var(--panel2); color: var(--fg);
  font: 650 .7rem var(--mono);
}
.rv-theater {
  display: grid; grid-template-columns: minmax(0, 1fr) 280px;
  gap: 1rem; align-items: start; margin-top: 1rem;
}
.rv-stage { min-width: 0; grid-column: 1; grid-row: 1; }
.rv-rail {
  grid-column: 2; grid-row: 1; display: flex; flex-direction: column; position: sticky;
  top: calc(var(--rv-nav-h) + .75rem);
  height: calc(100vh - var(--rv-nav-h) - 1.5rem);
  max-height: calc(100vh - var(--rv-nav-h) - 1.5rem);
  overflow: hidden; margin: 0; padding: .7rem;
}
.rv-rail-details {
  display: flex; flex: 1; min-height: 0; height: 100%;
  flex-direction: column; overflow: hidden;
}
.rv-rail-heading {
  display: flex; justify-content: space-between; align-items: center; gap: .5rem;
  color: var(--fg); font-size: .9rem; font-weight: 650;
}
.rv-rail-heading b { font-size: .72rem; color: var(--muted); font-weight: 550; }
.rv-rail-body {
  display: flex; flex: 1; min-height: 0; flex-direction: column;
  gap: .6rem; margin-top: .65rem; overflow: hidden;
}
.rv-rail .rv-bar { display: block; width: 100%; margin: 0; }
.rv-progress-context { font-size: .7rem; line-height: 1.35; }
.rv-qfilters { display: flex; flex-wrap: wrap; gap: .3rem; align-items: center; }
.rv-queue-ctl {
  display: grid; grid-template-columns: minmax(0, 1fr) auto auto auto;
  gap: .35rem; align-items: center;
}
.rv-queue-pos { font-size: .76rem; min-width: 2.6rem; text-align: center; }
#rv-queue-toggle.on { background: var(--accent); color: #0b1220; font-weight: 700; }
.rv-rail-advanced { border-top: 1px solid var(--line); padding-top: .45rem; }
.rv-rail-advanced > summary { margin-bottom: .4rem; }
.rv-rail-list {
  display: grid; flex: 1 1 auto; min-height: 9rem; gap: .35rem;
  overflow: auto; padding-right: .12rem; scrollbar-gutter: stable;
}
.rv-rail-item {
  width: 100%; display: grid; grid-template-columns: 2rem minmax(0, 1fr) auto;
  align-items: center; gap: .5rem; padding: .5rem .55rem;
  border: 1px solid transparent; border-radius: 8px;
  background: transparent; color: var(--fg); text-align: left; cursor: pointer;
  font: inherit;
}
.rv-rail-item:hover { background: var(--panel2); border-color: var(--line); }
.rv-rail-item.active {
  background: var(--accent-bg); border-color: var(--accent);
  box-shadow: inset 3px 0 0 var(--accent);
}
.rv-rail-item.rv-filtered-out { display: none; }
.rv-rail-index { color: var(--muted); font: 650 .72rem var(--mono); }
.rv-rail-copy { min-width: 0; display: grid; gap: .1rem; }
.rv-rail-copy strong { font-size: .82rem; }
.rv-rail-copy small {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--muted); font-size: .7rem;
}
.rv-rail-status {
  padding: .12rem .38rem; border-radius: 999px; white-space: nowrap;
  color: var(--muted); background: var(--panel2); font-size: .68rem;
}
.rv-rail-status.approved, .rv-rail-status.reviewed { color: var(--ok); background: #17402a; }
.rv-rail-status.stale { color: var(--warn); background: #4a3a12; }
.rv-rail-status.blocker { color: #ffd0d2; background: #5a1f25; }
.rv-rail-status.qc-error { color: var(--err); background: #4d1f22; }
.rv-rail-status.needs-selection, .rv-rail-status.needs-review { color: #ffb27a; background: #4a2f12; }
.rv-rail-status.missing { color: var(--muted); background: var(--panel2); }
.rv-shot {
  scroll-margin-top: calc(var(--rv-nav-h) + .75rem);
  overflow: clip;
}
.rv-shot.active {
  outline: 2px solid var(--accent); outline-offset: 1px;
  box-shadow: 0 12px 30px rgba(0,0,0,.22);
}
.rv-head {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .45rem 1rem;
  align-items: start; position: static; background: transparent; padding: 0;
}
.rv-head h2 { margin: 0; }
.rv-meta { grid-column: 1 / 2; line-height: 1.5; }
.rv-idx { font-size: .8rem; margin-left: .4rem; }
.rv-next {
  display: inline-flex; align-items: center; gap: .4rem; width: fit-content;
  margin-top: .35rem; padding: .28rem .5rem; border: 1px solid #5d4617;
  border-radius: 7px; background: #2b2516; color: #f2d486; font-size: .78rem;
}
.rv-next b { color: var(--warn); font-size: .68rem; letter-spacing: .02em; }
.rv-next-detail {
  display: grid; gap: .35rem; margin-top: .65rem; padding: .6rem .7rem;
  border: 1px solid var(--line); border-radius: 8px; background: var(--panel2);
  color: var(--muted); font-size: .78rem;
}
.rv-next-detail code { white-space: pre-wrap; overflow-wrap: anywhere; color: var(--fg); }
.rv-decision-state {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .45rem;
  margin-top: .65rem;
}
.rv-decision-cell {
  min-width: 0; display: grid; gap: .18rem; padding: .5rem .6rem;
  border: 1px solid var(--line); border-radius: 9px; background: var(--panel2);
}
.rv-decision-cell > span { color: var(--muted); font-size: .7rem; }
.rv-decision-cell > strong {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--fg); font-size: .82rem; font-weight: 680;
}
.rv-verdict-state.st-fresh, .rv-review-state.st-fresh { color: var(--ok); }
.rv-verdict-state.st-broken, .rv-review-state.st-broken { color: var(--err); }
.rv-verdict-state.st-needs, .rv-review-state.st-needs { color: var(--warn); }
.rv-verdict-state.st-manual, .rv-review-state.st-manual { color: var(--accent); }
.rv-body {
  display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(300px, .75fr);
  gap: 1rem; margin: .8rem 0;
}
.rv-player-column { min-width: 0; }
.rv-player {
  min-height: 260px; display: grid; place-items: center; overflow: hidden;
  border: 1px solid #08090b; border-radius: 12px; background: #050607;
}
.rv-video {
  display: block; width: 100%; max-height: min(62vh, 680px);
  object-fit: contain; border-radius: 0; background: #000;
}
.rv-player-note { margin: .5rem 0 0; font-size: .78rem; }
.rv-side { display: flex; flex-direction: column; gap: .7rem; min-width: 0; }
.rv-inspector-block {
  padding: .75rem; border: 1px solid var(--line); border-radius: 10px;
  background: color-mix(in srgb, var(--panel2) 70%, transparent);
}
.rv-inspector-title {
  display: flex; align-items: baseline; justify-content: space-between; gap: .6rem;
  margin-bottom: .55rem;
}
.rv-inspector-title .muted { font-size: .72rem; }
.rv-qc { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: .4rem; }
.rv-qc-item { font-size: .84rem; }
.rv-qc-sug { font-size: .8rem; margin-left: 1.2rem; }
/* UX audit F17: the board's annotations, mirrored read-only */
.rv-anns { margin-top: .7rem; }
.rv-anns ul { margin: .2rem 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: .4rem; }
.rv-ann-item { font-size: .84rem; }
.rv-ann-meta { font-size: .76rem; }
.rv-ann-note { background: #24313f; color: #9ecbff; }
.rv-ann-issue { background: #4a3a12; color: #ffcf5c; }
.rv-ann-blocker { background: #4d1f22; color: #ff8a90; }
.rv-ann-stale { background: #4d1f22; color: #ff8a90; }
.rv-ann-seek { color: var(--accent); cursor: pointer; text-decoration: underline dotted; font-size: .8rem; }
.rv-frame { margin-top: .65rem; }
.rv-frame img {
  width: 100%; border-radius: 8px; border: 1px solid var(--line);
  display: block; margin-top: .45rem;
}
.rv-alts-row { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .3rem; }
.rv-alt { display: flex; flex-direction: column; gap: .2rem; width: 120px; }
.rv-alt-media { position: relative; width: 120px; height: 68px; }
.rv-alt img, .rv-alt-noimg { width: 120px; height: 68px; object-fit: cover; border-radius: 5px; background: var(--panel2); border: 1px solid var(--line); }
.rv-alt-video { width: 120px; height: 68px; object-fit: cover; border-radius: 5px; background: #000; }
.rv-alt-video.hidden { display: none; }
.rv-alt-play {
  position: absolute; inset: 0; margin: auto; width: 1.8rem; height: 1.8rem; border-radius: 999px;
  background: rgba(0,0,0,.55); color: #fff; border: 1px solid rgba(255,255,255,.5);
  cursor: pointer; font-size: .8rem; line-height: 1;
}
.rv-alt-media.playing img, .rv-alt-media.playing .rv-alt-noimg, .rv-alt-media.playing .rv-alt-play { display: none; }
.rv-actions {
  display: flex; align-items: center; justify-content: space-between; gap: .65rem;
  margin: .75rem 0 .8rem; padding: .55rem .65rem;
  background: color-mix(in srgb, var(--panel) 94%, transparent);
  border: 1px solid var(--line); border-radius: 10px; backdrop-filter: blur(8px);
}
body.rv-queue-on .rv-actions {
  position: sticky; top: calc(var(--rv-nav-h) + .5rem);
  z-index: 39;
}
.rv-decision-main, .rv-decision-secondary {
  display: flex; align-items: center; flex-wrap: wrap; gap: .45rem;
}
.rv-primary-action { background: var(--ok); color: #07130b; border-color: var(--ok); }
.rv-reject-action { border-color: #5a2c2f !important; }
.rv-reject-action:hover:not(:disabled) { color: var(--err); border-color: var(--err) !important; }
.rv-repair { display: inline-flex; align-items: center; gap: .35rem; flex-wrap: wrap; color: var(--muted); font-size: .8rem; }
.rv-noterow {
  display: grid; grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center; gap: .5rem; margin-top: .65rem;
}
.rv-noterow label { color: var(--muted); font-size: .78rem; }
.rv-note-input, .lib-tag-input, .lib-note-input {
  flex: 1; background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .42rem .55rem; font: inherit; font-size: .84rem;
}
.rv-more { margin-top: .6rem; padding-top: .55rem; border-top: 1px solid var(--line); }
.rv-more-actions { display: flex; flex-wrap: wrap; gap: .45rem; align-items: center; margin-top: .55rem; }
.rv-explain { margin-top: .5rem; font-size: .84rem; font-family: var(--mono); }
.rv-shot.rv-filtered-out { display: none; }
body.rv-queue-on .rv-shot:not(.rv-qcurrent) { display: none; }

@media (max-width: 1280px) {
  .rv-body { grid-template-columns: 1fr; }
}
@media (max-width: 980px) {
  .rv-decision-guide { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rv-lock-guide { align-self: start; white-space: normal; }
  .rv-theater { grid-template-columns: 1fr; }
  .rv-stage { grid-column: 1; grid-row: 2; }
  .rv-rail {
    grid-column: 1; grid-row: 1; position: static; min-width: 0;
    width: 100%; max-width: 100%; height: auto; max-height: none;
    overflow: hidden; margin-bottom: 0;
  }
  .rv-rail-details { display: block; height: auto; overflow: visible; }
  .rv-rail-body { display: flex; overflow: visible; }
  .rv-rail-list {
    display: grid; min-width: 0; width: 100%; max-width: 100%;
    min-height: 0; height: auto; max-height: none;
    grid-auto-flow: column; grid-auto-columns: minmax(190px, 44vw);
    overflow-x: auto; overflow-y: hidden; overscroll-behavior-inline: contain;
    padding: 0 0 .25rem; scroll-snap-type: x proximity;
  }
  .rv-rail-item { scroll-snap-align: start; }
}
@media (max-width: 760px) {
  .rv-page-head { gap: .5rem; }
  .rv-shortcuts { margin-left: 0; }
  .rv-shortcut-grid {
    left: 0; right: auto; width: min(310px, calc(100vw - 2rem)); min-width: 0;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .rv-head { grid-template-columns: 1fr; }
  .rv-meta, .rv-next { grid-column: 1 / -1; grid-row: auto; }
  .rv-decision-guide { grid-template-columns: 1fr; }
  body.rv-queue-on .rv-actions { position: static; }
  .rv-progress-context { display: block; }
  .rv-queue-ctl { grid-template-columns: 1fr auto auto; }
  #rv-queue-toggle { grid-column: 1 / -1; justify-self: stretch; }
  .rv-queue-pos { justify-self: start; text-align: left; }
  .rv-actions { align-items: stretch; }
  .rv-decision-main, .rv-decision-secondary { width: 100%; }
  .rv-decision-main .btn { flex: 1 1 auto; }
  .rv-noterow { grid-template-columns: 1fr auto; }
  .rv-noterow label { grid-column: 1 / -1; }
  .rv-rail-list { grid-auto-columns: minmax(170px, 76vw); }
}
@media (max-width: 480px) {
  .rv-decision-state { grid-template-columns: 1fr; }
}

/* ------------------------------------------- consistency (round X, XB) -- */
.cs-unit { margin: .8rem 0; }
.cs-head { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap; }
.cs-board { display: block; max-width: 100%; margin: .5rem 0; border-radius: 8px; border: 1px solid var(--line); }
/* G1: lazy board placeholder — filled by /pages.js after first paint */
.cs-board-slot { display: flex; align-items: center; min-height: 3rem; margin: .5rem 0; font-size: .84rem; }
.cs-board-note { color: var(--muted); }
.cs-form { align-items: center; gap: .4rem; flex-wrap: wrap; margin-top: .4rem; }
.cs-form select { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .25rem .4rem; font: inherit; font-size: .84rem; }
.cs-message { flex: 1; min-width: 220px; background: var(--panel2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .3rem .5rem; font: inherit; font-size: .84rem; }
.cs-skipped { margin-top: .6rem; font-size: .84rem; }
.rv-pro-section { margin-top: 1.4rem; }
.rv-pro-section > summary { cursor: pointer; font-weight: 650; padding: .2rem 0; }

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
.lib-suggest { margin: .8rem 0; }
.lib-suggest h2 { font-size: 1rem; display: flex; align-items: center; gap: .6rem; }
.lib-matched { font-size: .74rem; color: var(--accent); }

/* -------------------------------------------- refs ownership (round AA #3) */
.refs-section { margin-top: 1.4rem; }
.refs-section .page-h h2 { font-size: 1.05rem; display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap; }
.refs-thumb { width: 46px; height: 46px; object-fit: cover; border-radius: 4px; background: var(--panel2); border: 1px solid var(--line); }
.refs-file { font-family: var(--mono); font-size: .8rem; max-width: 22rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.refs-missing ul { margin: .3rem 0 0; padding-left: 1.2rem; font-size: .82rem; }
.refs-missing li { margin: .15rem 0; }
.refs-assign-row { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; }
.refs-assign select { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .25rem .45rem; font: inherit; max-width: 100%; }
.refs-assign #refs-assign-out { min-height: 1.1rem; font-size: .82rem; }

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

  function reloadSoon() { setTimeout(function () { location.reload(); }, 450); }

  /* External-IDE handoff: clipboard with the house fallback (create/series). */
  function copyForAI(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { toast("上下文已复制，可粘贴到 IDE 助手", true); },
        function () { toast("复制失败，请手动选择", false); }
      );
    } else {
      toast("复制失败，请手动选择", false);
    }
  }

  var page = document.body.getAttribute("data-page");
  if (page === "/review") initReview();
  else if (page === "/compare") initCompare();
  else if (page === "/library") initLibrary();
  else if (page === "/providers") initProviders();
  else if (page === "/routing") initRouting();
  else if (page === "/doctor") initDoctor();
  if (page === "/review") initReviewConsistency();
  if (page === "/review") initReviewConsistencyBoards();

  // ------------------------ 跨镜一致性 lazy contact-sheet boards (audit G1)
  // The /review GET renders the consistency unit STRUCTURE inline but leaves
  // each unit's contact-sheet board a placeholder slot (never composes ffmpeg
  // on the request thread). Here we fetch the composed boards once, after first
  // paint, and fill the slots by DOM-building (no innerHTML). A fetch failure
  // leaves a labelled note in every slot — never a blank (degradation contract).
  function initReviewConsistencyBoards() {
    var details = document.querySelector(".rv-pro-section");
    if (!details) return;
    var loaded = false;
    function loadBoards() {
      if (loaded) return;
      loaded = true;
      var slots = [];
      Array.prototype.slice.call(details.querySelectorAll(".cs-unit")).forEach(function (u) {
        var slot = u.querySelector(".cs-board-slot");
        if (slot) slots.push({ unit: u.getAttribute("data-unit"), slot: slot });
      });
      if (!slots.length) return;  // empty state — nothing to load, no fetch
      details.classList.add("loading");
    function note(slot, text) {
      slot.textContent = "";
      var span = document.createElement("span");
      span.className = "muted cs-board-note";
      span.textContent = text;
      slot.appendChild(span);
    }
    function degradeAll() {
      slots.forEach(function (s) {
        note(s.slot, "看板加载失败,请刷新重试 (consistency boards failed to load)");
      });
    }
      post("/api/review/consistency", {}).then(function (res) {
      if (res.status !== 200 || !res.data || !res.data.units) { degradeAll(); return; }
      var byUnit = {};
      res.data.units.forEach(function (u) { byUnit[u.unit] = u; });
      slots.forEach(function (s) {
        var info = byUnit[s.unit];
        if (info && info.image) {
          s.slot.textContent = "";
          var img = document.createElement("img");
          img.className = "cs-board";
          img.alt = "";
          img.src = info.image;
          s.slot.appendChild(img);
        } else {
          note(s.slot, "看板尚未生成(需要 ffmpeg)");
        }
      });
      }).catch(degradeAll);
    }
    details.addEventListener("toggle", function () {
      if (details.open) loadBoards();
    });
    if (details.open) loadBoards();
  }

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
            toast("裁决已提交：" + unit, true);
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
    function syncReviewOffsets() {
      var nav = document.querySelector(".pnav");
      document.body.style.setProperty("--rv-nav-h", ((nav && nav.offsetHeight) || 88) + "px");
    }
    syncReviewOffsets();
    window.addEventListener("resize", syncReviewOffsets);
    var fill = document.getElementById("rv-progress-fill");
    if (fill && fill.dataset.pct) fill.style.width = fill.dataset.pct + "%";
    var shots = Array.prototype.slice.call(document.querySelectorAll(".rv-shot"));
    if (!shots.length) return;
    var active = 0;
    var activeVideo = null;
    function activateMedia(card) {
      if (activeVideo && activeVideo !== card.querySelector("video[data-src], video[src]")) {
        activeVideo.pause();
        activeVideo.removeAttribute("src");
        activeVideo.load();
      }
      var video = card && card.querySelector("video[data-src], video[src]");
      if (!video) { activeVideo = null; return; }
      if (video.hasAttribute("data-src")) {
        video.setAttribute("src", video.getAttribute("data-src"));
        video.setAttribute("preload", "metadata");
      }
      activeVideo = video;
    }
    /* 从上次位置继续 (#49a): the active card survives a reload / a return
     * days later — restored by SHOT ID (indices shift as shots come and go),
     * keyed by the stable project identity. Best-effort only. */
    var posKey = "manju-rv-pos-" + ((typeof PROJECT === "string" && PROJECT) ? PROJECT : "unbound");
    var restoredPos = false;  /* query/current position pins the queue head */
    var requestedShot = "";
    try { requestedShot = new URLSearchParams(location.search).get("shot") || ""; }
    catch (err) { requestedShot = ""; }
    if (requestedShot) {
      for (var qi = 0; qi < shots.length; qi++) {
        if (shots[qi].getAttribute("data-shot") === requestedShot) {
          active = qi;
          restoredPos = true;
          break;
        }
      }
    } else {
      try {
        var savedShot = window.localStorage.getItem(posKey);
        if (savedShot) {
          for (var si = 0; si < shots.length; si++) {
            if (shots[si].getAttribute("data-shot") === savedShot) {
              active = si;
              restoredPos = true;
              break;
            }
          }
        }
      } catch (err) { /* storage disabled — start at the top as before */ }
    }

    // ---- QUEUE mode (round X agent XF, pain #7/#8: batch review by state) --
    var qFilter = "all";
    var queueMode = false;
    var qIndex = 0;
    var qModeKey = "manju-rv-queue-" + ((typeof PROJECT === "string" && PROJECT) ? PROJECT : "unbound");
    /* 播放记忆 (#50a): rate/volume/muted survive reloads, per project —
     * review sessions keep the owner's chosen speed without re-setting it
     * on every card. Applies to every card + alt preview on load; any
     * user change on any <video> becomes the new remembered value. */
    var avKey = "manju-rv-av-" + ((typeof PROJECT === "string" && PROJECT) ? PROJECT : "unbound");
    var av = null;
    try { av = JSON.parse(window.localStorage.getItem(avKey) || "null"); } catch (err) { av = null; }
    function applyAV(v) {
      if (!av || !v) return;
      if (typeof av.rate === "number" && av.rate > 0) v.playbackRate = av.rate;
      if (typeof av.vol === "number") v.volume = Math.min(1, Math.max(0, av.vol));
      if (typeof av.muted === "boolean") v.muted = av.muted;
    }
    function saveAV(e) {
      var v = e.target;
      /* #50c: alt previews are deliberately muted server-side (they must
       * never double the audio) — only the MAIN player reads/writes memory */
      if (!v || v.tagName !== "VIDEO" || !v.closest(".rv-player")) return;
      av = { rate: v.playbackRate, vol: v.volume, muted: v.muted };
      try { window.localStorage.setItem(avKey, JSON.stringify(av)); } catch (err) { /* off */ }
    }
    Array.prototype.slice.call(
      document.querySelectorAll(".rv-shot .rv-player video")).forEach(applyAV);
    /* media events do not bubble — capture phase catches them all */
    document.addEventListener("ratechange", saveAV, true);
    document.addEventListener("volumechange", saveAV, true);
    /* direction program (#50): the queue walks most-blocking first, not shot
     * order. Snapshotted ONCE at load — a live re-sort would make cards jump
     * mid-session; the next reload re-ranks. */
    function qPriority(s) {
      var bs = s.getAttribute("data-buildstate") || "";
      if (bs === "needs_selection") return 0;
      if (bs === "stale") return 1;
      if (s.getAttribute("data-blocker") === "1") return 2;
      if (s.getAttribute("data-qc") === "1") return 2;   /* QC error findings */
      if (s.getAttribute("data-take") && s.getAttribute("data-reviewed") !== "1") return 3;
      /* nothing to JUDGE yet — a take-less shot cannot take a verdict, so it
       * trails everything reviewable regardless of its build state. */
      if (!s.getAttribute("data-take")) return 5;
      if ((s.getAttribute("data-review") || "needs_review") === "approved") return 6;
      return 4;  /* already evaluated, but not yet shot-approved */
    }
    var qOrder = shots.slice().sort(function (a, b) {
      var d = qPriority(a) - qPriority(b);
      return d !== 0 ? d : shots.indexOf(a) - shots.indexOf(b);
    });
    var railItems = Array.prototype.slice.call(document.querySelectorAll(".rv-rail-item"));
    var lastVerdict = null;  /* {shot, take, card, prevText, prevReviewed} — U 撤回 */

    function railItemFor(card) {
      var sid = card && card.getAttribute("data-shot");
      if (!sid) return null;
      for (var ri = 0; ri < railItems.length; ri++) {
        if (railItems[ri].getAttribute("data-rv-target") === sid) return railItems[ri];
      }
      return null;
    }
    var railList = document.querySelector(".rv-rail-list");
    if (railList) {
      qOrder.forEach(function (card) {
        var item = railItemFor(card);
        if (item) railList.appendChild(item);
      });
    }
    function railState(card) {
      var bs = card.getAttribute("data-buildstate") || "missing";
      if (!card.getAttribute("data-take")) {
        return bs === "needs_selection"
          ? { label: "待选", cls: "needs-selection" }
          : { label: "无候选", cls: "missing" };
      }
      if (bs === "stale") return { label: "待更新", cls: "stale" };
      if (card.getAttribute("data-blocker") === "1") {
        return { label: "有阻塞", cls: "blocker" };
      }
      if (card.getAttribute("data-qc") === "1") return { label: "需复核", cls: "qc-error" };
      if ((card.getAttribute("data-review") || "needs_review") === "approved") {
        return { label: "已通过", cls: "approved" };
      }
      if (card.getAttribute("data-reviewed") === "1") return { label: "已评价", cls: "reviewed" };
      return { label: "待审", cls: "needs-review" };
    }
    function syncRailItem(card) {
      var item = railItemFor(card);
      if (!item) return;
      var state = railState(card);
      item.classList.toggle("active", card === shots[active]);
      item.classList.toggle("reviewed", card.getAttribute("data-reviewed") === "1");
      item.classList.toggle("rv-filtered-out", !matchesFilter(card));
      if (card === shots[active]) item.setAttribute("aria-current", "true");
      else item.removeAttribute("aria-current");
      item.setAttribute("data-reviewed", card.getAttribute("data-reviewed") || "0");
      item.setAttribute("data-buildstate", card.getAttribute("data-buildstate") || "missing");
      item.setAttribute("data-review", card.getAttribute("data-review") || "needs_review");
      item.setAttribute("data-take", card.getAttribute("data-take") || "");
      item.setAttribute("data-qc", card.getAttribute("data-qc") || "0");
      item.setAttribute("data-blocker", card.getAttribute("data-blocker") || "0");
      item.setAttribute("data-verdict", card.getAttribute("data-verdict") || "pending");
      var status = item.querySelector(".rv-rail-status");
      if (status) {
        status.className = "rv-rail-status " + state.cls;
        status.textContent = state.label;
      }
    }
    function syncRail() { shots.forEach(syncRailItem); }
    function scrollRailToActive() {
      var item = railItemFor(shots[active]);
      if (item && queueMode) {
        item.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
      }
    }

    function verdictState(raw) {
      var text = (raw || "").trim();
      if (text === "好" || text.indexOf("好 ·") === 0) {
        return { kind: "good", label: "推荐", cls: "st-fresh" };
      }
      if (text === "弃" || text.indexOf("弃 ·") === 0) {
        return { kind: "reject", label: "不推荐", cls: "st-broken" };
      }
      if (text) return { kind: "note", label: "有备注", cls: "st-manual" };
      return { kind: "pending", label: "未评价", cls: "st-needs" };
    }
    function noteRationale(raw) {
      var text = (raw || "").trim();
      var match = text.match(/^(好|弃)(?:\s*·\s*)?([\s\S]*)$/);
      return match ? (match[2] || "").trim() : text;
    }
    function noteTextFor(card, rationale) {
      var kind = card.getAttribute("data-verdict") || "pending";
      var text = (rationale || "").trim();
      if (kind === "good") return "好" + (text ? " · " + text : "");
      if (kind === "reject") return "弃" + (text ? " · " + text : "");
      return text;
    }
    function syncVerdictFact(card, raw) {
      var state = verdictState(raw);
      card.setAttribute("data-note-raw", (raw || "").trim());
      card.setAttribute("data-verdict", state.kind);
      var badge = card.querySelector(".rv-verdict-state");
      if (badge) {
        badge.className = "rv-verdict-state " + state.cls;
        badge.textContent = state.label;
      }
      var clearButton = card.querySelector(".rv-clear-verdict");
      if (clearButton) clearButton.hidden = !(raw || "").trim();
      return state;
    }
    function syncReviewed(card, raw) {
      var isReviewed = (raw || "").trim() ? "1" : "0";
      card.setAttribute("data-reviewed", isReviewed);
      card.classList.toggle("reviewed", isReviewed === "1");
      return isReviewed;
    }

    function updateProgress() {
      var reviewable = shots.filter(function (s) { return !!s.getAttribute("data-take"); }).length;
      var done = document.querySelectorAll('.rv-shot[data-take]:not([data-take=""])[data-reviewed="1"]').length;
      var meter = document.getElementById("rv-progress");
      if (meter) meter.textContent = "当前候选已评价 " + done + " / " + reviewable;
      var progress = document.querySelector(".rv-bar[role=progressbar]");
      if (progress) {
        progress.setAttribute("aria-valuemax", String(reviewable));
        progress.setAttribute("aria-valuenow", String(done));
      }
      if (fill) fill.style.width = (reviewable ? (done * 100 / reviewable) : 0) + "%";
    }
    function setActive(i) {
      if (i < 0) i = 0;
      if (i >= shots.length) i = shots.length - 1;
      shots[active].classList.remove("active");
      active = i;
      shots[active].classList.add("active");
      activateMedia(shots[active]);
      syncRail();
      scrollRailToActive();
      shots[active].scrollIntoView({ behavior: "smooth", block: "start" });
      try {
        window.localStorage.setItem(posKey, shots[active].getAttribute("data-shot") || "");
      } catch (err) { /* best-effort */ }
    }
    function currentVideo() { return shots[active].querySelector("video"); }

    function matchesFilter(s) {
      if (qFilter === "all") return true;
      if (qFilter === "needs_selection" || qFilter === "stale") {
        return (s.getAttribute("data-buildstate") || "missing") === qFilter;
      }
      if (qFilter === "needs_review") {
        return !!s.getAttribute("data-take") && s.getAttribute("data-reviewed") !== "1";
      }
      return (s.getAttribute("data-review") || "needs_review") === qFilter;
    }
    function filteredShots() { return qOrder.filter(matchesFilter); }

    /* one owner for entering/leaving queue mode — the toggle click, the
     * saved preference and the unreviewed-work default all route here. */
    function setQueueMode(on) {
      queueMode = !!on;
      document.body.classList.toggle("rv-queue-on", queueMode);
      var tg = document.getElementById("rv-queue-toggle");
      if (tg) {
        tg.classList.toggle("on", queueMode);
        tg.setAttribute("aria-pressed", queueMode ? "true" : "false");
        tg.textContent = queueMode ? "浏览全部镜头" : "只看当前镜头";
      }
      qIndex = 0;
      updateQueueUI();
      syncReviewOffsets();
    }
    function updateQueueUI() {
      var list = filteredShots();
      var pos = document.getElementById("rv-queue-pos");
      shots.forEach(function (s) {
        s.classList.remove("rv-qcurrent");
        s.classList.toggle("rv-filtered-out", !matchesFilter(s));
      });
      if (!queueMode) { if (pos) pos.textContent = ""; syncRail(); return; }
      if (!list.length) { if (pos) pos.textContent = "0 / 0"; syncRail(); return; }
      if (qIndex >= list.length) qIndex = list.length - 1;
      if (qIndex < 0) qIndex = 0;
      var cur = list[qIndex];
      cur.classList.add("rv-qcurrent");
      if (pos) pos.textContent = (qIndex + 1) + " / " + list.length;
      var i = shots.indexOf(cur);
      if (i >= 0) setActive(i);
      syncRail();
    }

    function verdict(kind) {
      var s = shots[active];
      var shot = s.getAttribute("data-shot");
      var take = s.getAttribute("data-take");
      if (!take) { toast("先选择一个候选", false); return; }
      var noteEl = s.querySelector(".rv-note-input");
      var rationale = noteEl ? noteEl.value.trim() : "";
      var label = kind === "good" ? "好" : "弃";
      var nextRaw = label + (rationale ? " · " + rationale : "");
      /* round AA item 5 (#1): data-rev is the CAS token this card's note was
       * rendered at — echoed back as expected_rev so a save against a card
       * left open past someone else's edit is refused (409), not clobbered. */
      var noteBody = { shot: shot, take: take, text: nextRaw };
      var rev = s.getAttribute("data-rev");
      if (rev) noteBody.expected_rev = rev;
      /* U 撤回 (#50): remember what this verdict overwrote — the input's
       * defaultValue is the server-rendered saved note, untouched by typing. */
      var prevState = {
        shot: shot, take: take, card: s,
        prevText: s.getAttribute("data-note-raw") || "",
        prevReviewed: s.getAttribute("data-reviewed") || "0",
        prevVerdict: s.getAttribute("data-verdict") || "pending",
      };
      post("/api/take-note", noteBody).then(function (res) {
        if (res.status === 200) {
          /* UX audit F14: refresh the CAS token from the response, or the
           * owner's NEXT action on this card is refused by their own save. */
          if (res.data && res.data.rev) s.setAttribute("data-rev", res.data.rev);
          syncVerdictFact(s, nextRaw);
          syncReviewed(s, nextRaw);
          if (noteEl) noteEl.defaultValue = rationale;
          lastVerdict = prevState;
          updateProgress();
          syncRailItem(s);
          toast(shot + (kind === "good" ? " 已标记为推荐" : " 已标记为不推荐"),
                kind === "good");
          if (kind === "reject") {
            var alts = s.querySelector(".rv-alts");
            if (alts) alts.scrollIntoView({ behavior: "smooth", block: "center" });
          } else if (queueMode) {
            /* 判断后自动下一条 — advance only while the card still matches
             * the active filter (same #50c rule as 通过) */
            if (filteredShots().indexOf(s) >= 0) qIndex++;
            updateQueueUI();
          } else {
            setActive(active + 1);
          }
        } else { toast((res.data && res.data.error) || "失败", false); }
      });
    }

    document.addEventListener("click", function (e) {
      /* UX audit F17: the mirrored board-annotation frame chip seeks the
       * card's player — the same affordance the board's own list has. */
      var seek = e.target.closest(".rv-ann-seek");
      if (seek) {
        var card = seek.closest(".rv-shot");
        var vid = card ? card.querySelector(".rv-video") : null;
        if (vid) { vid.currentTime = parseFloat(seek.getAttribute("data-seek") || "0"); }
        return;
      }
      var railItem = e.target.closest(".rv-rail-item");
      if (railItem) {
        var targetShot = railItem.getAttribute("data-rv-target");
        var targetCard = null;
        for (var ri = 0; ri < shots.length; ri++) {
          if (shots[ri].getAttribute("data-shot") === targetShot) {
            targetCard = shots[ri];
            break;
          }
        }
        if (!targetCard) return;
        if (!queueMode) setQueueMode(true);
        var queue = filteredShots();
        var targetIndex = queue.indexOf(targetCard);
        if (targetIndex >= 0) {
          qIndex = targetIndex;
          updateQueueUI();
        }
        return;
      }
      var btn = e.target.closest("[data-act]");
      if (!btn) return;
      var s = btn.closest(".rv-shot");
      if (!s) return;
      var act = btn.getAttribute("data-act");
      var i = shots.indexOf(s);
      /* the alt ▶ (data-act=preview) belongs to its OWN listener — activating
       * here scroll-jumped the page on every lazy preview (bug-hunt #51) */
      if (act !== "preview" && i >= 0 && i !== active) setActive(i);
      var shot = s.getAttribute("data-shot");
      if (act === "good") verdict("good");
      else if (act === "reject") verdict("reject");
      else if (act === "skip") {
        if (queueMode) { qIndex++; updateQueueUI(); }
        else setActive(active + 1);
      }
      else if (act === "note") {
        var take = s.getAttribute("data-take");
        if (!take) { toast("先选择一个候选", false); return; }
        var val = s.querySelector(".rv-note-input").value.trim();
        var nextNoteRaw = noteTextFor(s, val);
        var noteBody2 = { shot: shot, take: take, text: nextNoteRaw };
        var rev2 = s.getAttribute("data-rev");
        if (rev2) noteBody2.expected_rev = rev2;
        post("/api/take-note", noteBody2).then(function (res) {
          if (res.status === 200) {
            if (res.data && res.data.rev) s.setAttribute("data-rev", res.data.rev);
            /* #50c: defaultValue = the last SAVED text — u-undo restores
             * exactly this, so a saved note is never silently discarded. */
            var nEl2 = s.querySelector(".rv-note-input");
            if (nEl2) nEl2.defaultValue = val;
            syncVerdictFact(s, nextNoteRaw);
            syncReviewed(s, nextNoteRaw);
            toast("备注已保存", true);
            updateProgress();
            updateQueueUI();
          } else { toast((res.data && res.data.error) || "失败", false); }
        });
      }
      else if (act === "select") {
        post("/api/select", { shot: shot, take: btn.getAttribute("data-take") }).then(function (res) {
          if (res.status === 200) { toast("已换用 " + btn.getAttribute("data-take"), true); reloadSoon(); }
          else toast((res.data && res.data.error) || "失败", false);
        });
      }
      else if (act === "clear-verdict") {
        var clearTake = s.getAttribute("data-take");
        if (!clearTake) { toast("先选择一个候选", false); return; }
        var beforeRaw = s.getAttribute("data-note-raw") || "";
        var clearBody = { shot: shot, take: clearTake, text: "" };
        var clearRev = s.getAttribute("data-rev");
        if (clearRev) clearBody.expected_rev = clearRev;
        post("/api/take-note", clearBody).then(function (res) {
          if (res.status === 200) {
            if (res.data && res.data.rev) s.setAttribute("data-rev", res.data.rev);
            lastVerdict = {
              shot: shot, take: clearTake, card: s, prevText: beforeRaw,
              prevReviewed: s.getAttribute("data-reviewed") || "0",
              prevVerdict: s.getAttribute("data-verdict") || "pending",
            };
            syncVerdictFact(s, "");
            syncReviewed(s, "");
            var clearInput = s.querySelector(".rv-note-input");
            if (clearInput) { clearInput.value = ""; clearInput.defaultValue = ""; }
            updateProgress();
            updateQueueUI();
            toast("已清除 " + shot + " 的当前评价", true);
          } else toast((res.data && res.data.error) || "清除失败", false);
        });
      }
      else if (act === "qapprove") {
        post("/api/storyboard/approve", { shot: shot, review: "approved" }).then(function (res) {
          if (res.status === 200) {
            /* UX audit F14: approving rewrote the shot file — refresh the
             * card's CAS token so a following 好/弃/备注 still lands. */
            if (res.data && res.data.revs && res.data.revs[shot]) {
              s.setAttribute("data-rev", res.data.revs[shot]);
            }
            s.setAttribute("data-review", "approved");
            var approval = s.querySelector(".rv-review-state");
            if (approval) {
              approval.className = "rv-review-state st-fresh";
              approval.textContent = "已通过";
            }
            var approveButton = s.querySelector('[data-act="qapprove"]');
            if (approveButton) {
              approveButton.disabled = true;
              approveButton.setAttribute("aria-disabled", "true");
              approveButton.textContent = "镜头已通过";
            }
            syncRailItem(s);
            toast(shot + " 已通过", true);
            updateQueueUI();
            /* #50c: only advance when the approved card STILL matches the
             * active filter — under 待审 the card leaves the list and the
             * next one slides into this index; qIndex++ would skip it. */
            if (queueMode) {
              if (filteredShots().indexOf(s) >= 0) qIndex++;
              updateQueueUI();
            }
          } else toast((res.data && res.data.error) || "失败", false);
        });
      }
      else if (act === "redo") {
        // C1: confirm dialog IS the spend gate (personal workbench) — pass
        // assume_yes so the job does not fail as WaitingUser with a green toast.
        if (!window.confirm("重做镜头 " + shot + "？将产生新的生成花费(确认即批准花费)。")) return;
        post("/api/redo", { shot: shot, assume_yes: true }).then(function (res) {
          if (res.status === 202 || (res.data && res.data.job)) toast("重做已加入任务队列", true);
          else toast((res.data && res.data.error) || "失败", false);
        });
      }
      else if (act === "repair") {
        var b = { shot: shot, op: btn.getAttribute("data-op") };
        if (btn.getAttribute("data-factor")) b.factor = parseFloat(btn.getAttribute("data-factor"));
        if (btn.getAttribute("data-ms")) b.ms = parseInt(btn.getAttribute("data-ms"), 10);
        if (btn.getAttribute("data-mode")) b.mode = btn.getAttribute("data-mode");
        post("/api/repair", b).then(function (res) {
          if (res.status === 202 || (res.data && res.data.job)) toast("修复排队: " + b.op, true);
          else toast((res.data && res.data.error) || "失败", false);
        });
      }
      else if (act === "ai-ctx") {
        /* External IDE handoff: a clean, structured task context — the AI lives
         * OUTSIDE the GUI (§0); this hands it exactly what the card knows.
         * NB: read data-take HERE — the hoisted `var take` from the note
         * branch is undefined on this path and always copied "(未选用)". */
        var ctxTake = s.getAttribute("data-take");
        var copiedVerdict = verdictState(s.getAttribute("data-note-raw") || "");
        var cl = [
          "镜头: " + shot,
          "当前选择: " + (ctxTake || "（尚未选择）"),
          "当前评价: " + copiedVerdict.label,
          "镜头审批: " + (s.getAttribute("data-review") || "needs_review"),
          "构建状态: " + (s.getAttribute("data-buildstate") || "?"),
        ];
        var nvEl = s.querySelector(".rv-note-input");
        if (nvEl && nvEl.value.trim()) cl.push("备注: " + nvEl.value.trim());
        cl.push("相关文件:");
        cl.push("- shots/" + shot + ".yaml");
        var vEl = s.querySelector("video");
        var vsrc = vEl ? (vEl.getAttribute("src") || "") : "";
        if (vsrc.indexOf("/media/") === 0) {
          cl.push("- " + decodeURI(vsrc.slice("/media/".length)).split("?")[0]);
        }
        if (s.querySelector(".rv-anns")) cl.push("- reports/annotations.jsonl(含本镜批注)");
        cl.push("目标:（写下希望 IDE 助手完成的事）");
        copyForAI(cl.join("\n"));
      }
      else if (act === "route") {
        var box = s.querySelector(".rv-explain");
        (typeof requestJson==="function"?requestJson("GET","/api/route-explain?shot="+encodeURIComponent(shot),undefined,typeof manjuApiOptions==="function"?manjuApiOptions():{}):fetch("/api/route-explain?shot="+encodeURIComponent(shot)).then(function(r){return r.json();}))
          .then(function (d) {
            box.hidden = false;
            box.textContent = "";
            if (d.error) { box.textContent = d.error; return; }
            box.textContent = "策略 " + d.strategy + " → " + (d.chosen || "(none)")
              + "  顺序: " + ((d.order || []).join(" › ") || "无");
          });
      }
    });

    // ---- queue toolbar: filter chips, mode toggle, prev/next, alt preview --
    document.addEventListener("click", function (e) {
      var play = e.target.closest(".rv-alt-play");
      if (play) {
        var media = play.closest(".rv-alt-media");
        var vid = media && media.querySelector(".rv-alt-video[data-src]");
        if (vid) {
          vid.setAttribute("src", vid.getAttribute("data-src"));
          vid.removeAttribute("data-src");
          vid.classList.remove("hidden");
          media.classList.add("playing");
          vid.play().catch(function () {});
        }
        return;
      }
      var chip = e.target.closest(".rv-qfilter");
      if (chip) {
        document.querySelectorAll(".rv-qfilter").forEach(function (c) {
          var activeChip = c === chip;
          c.classList.toggle("active", activeChip);
          c.setAttribute("aria-pressed", activeChip ? "true" : "false");
        });
        qFilter = chip.getAttribute("data-filter");
        qIndex = 0;
        updateQueueUI();
        return;
      }
      if (e.target.id === "rv-queue-toggle") {
        setQueueMode(!queueMode);
        /* persist EXPLICIT intent only — the unreviewed-work default must
         * stay a default, not silently become a preference. */
        try { window.localStorage.setItem(qModeKey, queueMode ? "1" : "0"); }
        catch (err) { /* best-effort */ }
        return;
      }
      if (e.target.id === "rv-q-prev") { qIndex--; updateQueueUI(); return; }
      if (e.target.id === "rv-q-next") { qIndex++; updateQueueUI(); return; }
      if (e.target.id === "rv-redo-stale") {
        /* C1: confirm dialog is the spend gate — assume_yes after explicit yes
         * (jobs panel has no waiting_user UI for redo_batch). */
        var stale = Array.prototype.map.call(
          document.querySelectorAll('.rv-shot[data-buildstate="stale"]'),
          function (s) { return s.getAttribute("data-shot"); });
        if (!stale.length) { toast("没有待更新的镜头", false); return; }
        if (!window.confirm("批量重做 " + stale.length + " 个待更新镜头?"
                            + "可能产生生成花费;确认即批准花费。")) return;
        e.target.disabled = true;
        post("/api/redo-batch", { shots: stale, assume_yes: true }).then(function (res) {
          e.target.disabled = false;
          if ((res.status === 202 || res.status === 200) && res.data && res.data.job) {
            toast("批量重做已加入任务队列（任务 " + res.data.job.id + "），进度见工作台", true);
          } else toast((res.data && res.data.error) || "失败", false);
        });
        return;
      }
    });

    document.addEventListener("keydown", function (e) {
      var tag = e.target && e.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "?") {
        var help = document.getElementById("rv-shortcuts");
        if (help) {
          help.open = !help.open;
          syncReviewOffsets();
        }
        e.preventDefault();
        return;
      }
      if (queueMode && (e.key === "j" || e.key === "k")) {
        qIndex += (e.key === "j") ? 1 : -1;
        updateQueueUI();
        e.preventDefault();
        return;
      }
      if (e.key === "j") { setActive(active + 1); e.preventDefault(); }
      else if (e.key === "k") { setActive(active - 1); e.preventDefault(); }
      else if (e.key === "g") { verdict("good"); e.preventDefault(); }
      else if (e.key === "x") { verdict("reject"); e.preventDefault(); }
      else if (e.key === "a") {
        /* Convenience wave 3: approve joins the keyboard flow. Fires the
         * card's own 通过 button so the CAS-token refresh and queue
         * advance stay in ONE place. 重做 deliberately has no key —
         * a spend action never hides behind a single keystroke. */
        var ab = shots[active] && shots[active].querySelector('[data-act="qapprove"]');
        if (ab) ab.click();
        e.preventDefault();
      }
      else if (e.key === "u") {
        /* 撤回刚才的好/弃 (#50): restores the note text the verdict
         * overwrote and the card's reviewed state. One step, newest only —
         * deeper history belongs to the truth files, not the browser. */
        if (!lastVerdict) { toast("没有可撤回的评价", false); e.preventDefault(); return; }
        var lv = lastVerdict;
        lastVerdict = null;
        var ubody = { shot: lv.shot, take: lv.take, text: lv.prevText };
        var urev = lv.card.getAttribute("data-rev");
        if (urev) ubody.expected_rev = urev;
        post("/api/take-note", ubody).then(function (res) {
          if (res.status === 200) {
            if (res.data && res.data.rev) lv.card.setAttribute("data-rev", res.data.rev);
            syncVerdictFact(lv.card, lv.prevText);
            syncReviewed(lv.card, lv.prevText);
            var restoredNote = lv.card.querySelector(".rv-note-input");
            if (restoredNote) {
              restoredNote.value = noteRationale(lv.prevText);
              restoredNote.defaultValue = noteRationale(lv.prevText);
            }
            updateProgress();
            updateQueueUI();
            toast("已撤回 " + lv.shot + " 的评价", true);
          } else {
            lastVerdict = lv;  /* #50c: a transient failure keeps u retryable */
            toast((res.data && res.data.error) || "撤回失败", false);
          }
        });
        e.preventDefault();
      }
      else if (e.key === " ") {
        var v = currentVideo();
        if (v) { if (v.paused) v.play(); else v.pause(); }
        e.preventDefault();
      }
    });
    /* honour the restored 从上次位置继续 index (falls back to 0) — the class
     * lands directly so the page does NOT auto-scroll on a fresh open;
     * j/k/setActive scrolls from here on as always. */
    shots[active].classList.add("active");
    activateMedia(shots[active]);
    /* 队列默认 (#50): a saved preference wins; otherwise unreviewed work
     * opens straight into 队列模式 — the daily task IS the queue. */
    var savedQ = null;
    try { savedQ = window.localStorage.getItem(qModeKey); } catch (err) { /* off */ }
    var unreviewed = shots.filter(function (s) {
      return s.getAttribute("data-reviewed") !== "1";
    }).length;
    if (savedQ === "1" || (savedQ === null && unreviewed > 0)) {
      /* capture the restored 上次位置 card BEFORE entering queue mode —
       * setQueueMode's updateQueueUI syncs `active` onto the queue head. */
      var restoredCard = shots[active];
      setQueueMode(true);
      if (restoredPos) {  /* only a REAL saved position re-pins the queue */
        var ai = filteredShots().indexOf(restoredCard);
        if (ai >= 0) { qIndex = ai; updateQueueUI(); }
      }
    }
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
          if (res.status === 200) toast("已用到项目 → " + (res.data.dest || "media/refs"), true);
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
      var upHeaders = { "X-Manju-Token": TOKEN };
      if (PROJECT) upHeaders["X-Manju-Project"] = PROJECT;
      fetch("/api/lib/upload?name=" + encodeURIComponent(f.name), {
        method: "POST", headers: upHeaders, body: f
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          return { status: r.status, data: d };
        });
      }).then(function (res) {
        if (res.status === 200) { toast("已入库 (added)", true); reloadSoon(); }
        else toast((res.data && res.data.error) || "失败", false);
      });
    });

    // -------- media/refs ownership 关联 (assign) — round AA goal item 3 --
    var assignBtn = document.getElementById("refs-assign-btn");
    if (assignBtn) assignBtn.addEventListener("click", function () {
      var fileSel = document.getElementById("refs-assign-file");
      var targetSel = document.getElementById("refs-assign-target");
      var out = document.getElementById("refs-assign-out");
      var relpath = fileSel ? fileSel.value : "";
      var target = targetSel ? targetSel.value : "";
      if (!relpath || !target) {
        if (out) out.textContent = "请先选择文件和归属目标 (pick a file and a target)";
        return;
      }
      var sep = target.indexOf(":");
      var kind = target.slice(0, sep);
      var id = target.slice(sep + 1);
      var body = { relpath: relpath };
      body[kind] = id;
      post("/api/refs/assign", body).then(function (res) {
        if (res.status === 200 && res.data.ok) {
          toast("已关联 (assigned) → " + (res.data.new || "已更新"), true);
          reloadSoon();
        } else {
          var msg = (res.data && res.data.error) || "关联失败 (assign failed)";
          if (out) out.textContent = msg;
          toast(msg, false);
        }
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
      (typeof requestJson==="function"?requestJson("GET","/api/route-explain?shot="+encodeURIComponent(sel.value),undefined,typeof manjuApiOptions==="function"?manjuApiOptions():{}):fetch("/api/route-explain?shot="+encodeURIComponent(sel.value)).then(function(r){return r.json();}))
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
