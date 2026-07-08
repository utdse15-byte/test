"""分镜工作台 Storyboard workspace — the complete table-style shot view (round-U,
goal item 3).

A sibling of the round-S/T/U server-rendered pages (:mod:`manju.gui.pages` /
:mod:`manju.gui.pages_t` / :mod:`manju.gui.exports_page`), built to the exact
same stance:

  * server-rendered — a plain GET carries every shot row baked into the DOM;
    ``/storyboard.js`` only layers on inline edit, the approval-chip cycle, the
    per-field lock action, batch approve/lock and the row detail drawer;
  * CSP-safe — CSS/JS in external files, no inline handlers / ``style=``, every
    mutating POST carries the ``X-Manju-Token`` from the ``manju-token`` meta tag;
  * XSS-safe — ALL server text (actions, dialogue, must_show/avoid, provider,
    lock paths) is ``html.escape``-d and the script only ever writes textContent;
  * one core — the columns are exactly the REPORTS §2 打法建议 table: Shot# · 场景
    · 角色 · 动作/描述 · 台词 · 镜头 · 必须出现 · 避免 · 来源 · 状态 · 锁 · 审批.

Two orthogonal chips per the reference (Frame.io keeps freshness separate from
approval): 状态 is the take-state from :mod:`manju.build.stale`
(无版本/待更新/最新…), 审批 is the three-state review (待审/进行中/已通过 — the
Frame.io Needs Review / In Progress / Approved words). 角色 chips resolve through
the round-U asset matrix (alias-aware); an unregistered but resolvable @mention
renders as a HOLLOW chip pointing at ``manju mentions --apply``.

Editing is lock-respecting: a locked path is NEVER written (the server refuses
with 409 naming the lock in 中文). LOCK is offered (the same seal
``manju lock`` uses); UNLOCK is deliberately absent — an existing lock renders
with a 🔒 and the tooltip "解锁请用命令行 manju unlock(防误触)" per
docs/WORKBENCH.md containment.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

__all__ = [
    "PAGE_PATH",
    "render",
    "render_storyboard_css",
    "render_storyboard_js",
    "EDITABLE_FIELDS",
    "LIST_FIELDS",
    "lock_conflict",
]

PAGE_PATH = "/storyboard"

# The shot free-text fields the storyboard edits inline. Kept small and explicit
# so an unexpected dotted path can never be written through this surface.
EDITABLE_FIELDS = ("action.main", "dialogue.text", "quality.must_show", "quality.avoid")
# The two of those that are LISTS (one entry per line in the editor).
LIST_FIELDS = ("quality.must_show", "quality.avoid")

# take-state (build/stale ShotState) → 中文 chip word + the app.css badge class.
# Uses the REPORTS §10 glossary vocabulary (待更新 = stale, 版本 = take).
_STATE_ZH = {
    "missing": "无版本",
    "fresh": "最新",
    "stale": "待更新",
    "manual": "手动置入",
    "needs_selection": "待挑选",
    "broken": "缺媒体",
}
_STATE_BADGE = {
    "missing": "st-missing",
    "fresh": "st-fresh",
    "stale": "st-stale",
    "manual": "st-manual",
    "needs_selection": "st-needs",
    "broken": "st-broken",
}

# three-state review (Frame.io Needs Review / In Progress / Approved) → 中文 chip.
_REVIEW_ZH = {"needs_review": "待审", "in_progress": "进行中", "approved": "已通过"}
_REVIEW_BADGE = {"needs_review": "st-needs", "in_progress": "st-stale",
                 "approved": "st-fresh"}
# the cycle 待审 → 进行中 → 已通过 → 待审 (also mirrored in the JS)
_REVIEW_NEXT = {"needs_review": "in_progress", "in_progress": "approved",
                "approved": "needs_review"}

_UNLOCK_TIP = "解锁请用命令行 manju unlock(防误触)"


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


# ---------------------------------------------------------------- lock helper


def lock_conflict(locked: dict[str, Any], field: str) -> str | None:
    """The locked dotted path that would be overwritten by editing ``field``, or
    ``None`` when no lock overlaps. A lock on ``P`` seals the value at ``P``; a
    write to ``F`` collides when ``P == F`` (same field), ``F`` is inside ``P``
    (``F`` startswith ``P.``) or ``P`` is inside ``F`` (``P`` startswith ``F.``).
    Shared by the server edit endpoint so the refusal names the exact lock."""
    for p in (locked or {}):
        if p == field or field.startswith(p + ".") or p.startswith(field + "."):
            return p
    return None


# ---------------------------------------------------------------- HTML shell


def _shell(title: str, token: str, body: str) -> str:
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
        '<link rel="stylesheet" href="/storyboard.css">\n'
        '<script src="/storyboard.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{PAGE_PATH}">\n'
        + nav_html(PAGE_PATH)
        + "\n<main>\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


# ---------------------------------------------------------------- cell builders


def _char_chips(shot: Any, matrix: dict[str, Any], lookup: Any) -> str:
    """角色 chips: registered characters resolved through the asset matrix
    (alias-aware, solid chips) plus any resolvable-but-UNregistered @mention as a
    hollow chip pointing at ``manju mentions --apply``."""
    names = {r["id"]: (r.get("name") or r["id"])
             for r in matrix.get("kinds", {}).get("character", [])}

    def canon(token: str) -> tuple[str | None, str]:
        for kind, aid, via in lookup.candidates(token):
            if kind == "character":
                return aid, via
        return None, "id"

    chips: list[str] = []
    registered_canon: set[str] = set()
    for c in (shot.characters or []):
        aid, via = canon(c)
        if aid is not None:
            registered_canon.add(aid)
            label = names.get(aid, aid)
            tip = f"角色 {aid}" + (f"(经别名 {c} 解析)" if via == "alias" else "")
            chips.append(f'<span class="chip sb-char" title="{_e(tip)}">{_e(label)}</span>')
        else:
            registered_canon.add(c)
            chips.append(
                f'<span class="chip sb-char sb-dangling" '
                f'title="{_e(f"{c} 未在 characters 设定集中登记")}">{_e(c)}</span>')

    # resolvable @mentions in the shot's free text that are NOT yet registered
    try:
        from ..core.mentions import resolve_mentions, shot_mention_text

        resolved, _ = resolve_mentions(shot_mention_text(shot), matrix)
    except Exception:
        resolved = []
    seen: set[str] = set()
    for m, kind, aid in resolved:
        if kind != "character" or aid in registered_canon or aid in seen:
            continue
        seen.add(aid)
        label = names.get(aid, aid)
        tip = f"@{m.raw} 已识别但未登记到 characters — 运行 manju mentions --apply 登记"
        chips.append(f'<span class="chip sb-char sb-hollow" title="{_e(tip)}">'
                     f'@{_e(label)}</span>')

    if not chips:
        return '<span class="muted">—</span>'
    return '<span class="chips">' + "".join(chips) + "</span>"


def _lock_chips(locked: dict[str, Any]) -> str:
    """锁 column: one 🔒 chip per locked field. NO unlock affordance — the tooltip
    routes to the CLI (docs/WORKBENCH.md containment)."""
    if not locked:
        return '<span class="muted">—</span>'
    out: list[str] = []
    for path, digest in locked.items():
        unsealed = "" if digest else " · 未封印(manju lock)"
        tip = f"{path} 已锁定 · {_UNLOCK_TIP}{unsealed}"
        out.append(f'<span class="chip lock" title="{_e(tip)}">🔒 {_e(path)}</span>')
    return '<span class="chips">' + "".join(out) + "</span>"


def _editable(shot_id: str, field: str, value: str) -> str:
    """A double-click / ✎ inline-editable cell. The raw value rides in a data
    attribute so the JS editor seeds itself without re-escaping surprises."""
    shown = value if value else "—"
    cls = "sb-edit" + (" muted" if not value else "")
    return (f'<td class="{cls}" data-field="{_e(field)}" data-shot="{_e(shot_id)}" '
            f'title="双击编辑 (double-click to edit)">'
            f'<span class="cell-text">{_e(shown)}</span>'
            f'<button class="edit-btn" type="button" title="编辑">✎</button></td>')


def _list_cell(shot_id: str, field: str, items: list[str]) -> str:
    """A must_show / avoid cell: chips for display, newline-joined for editing."""
    raw = "\n".join(str(x) for x in (items or []))
    inner = ("".join(f'<span class="chip mini">{_e(x)}</span>' for x in items)
             if items else '<span class="muted">—</span>')
    cls = "sb-edit sb-list"
    return (f'<td class="{cls}" data-field="{_e(field)}" data-shot="{_e(shot_id)}" '
            f'data-raw="{_e(raw)}" title="双击编辑 (double-click to edit)">'
            f'<span class="cell-text chips">{inner}</span>'
            f'<button class="edit-btn" type="button" title="编辑">✎</button></td>')


def _approval_chip(shot_id: str, review_state: str) -> str:
    zh = _REVIEW_ZH.get(review_state, review_state)
    badge = _REVIEW_BADGE.get(review_state, "st-needs")
    return (f'<button class="chip sb-approve badge {badge}" type="button" '
            f'data-shot="{_e(shot_id)}" data-review="{_e(review_state)}" '
            f'title="点击流转 待审→进行中→已通过 (approval)">{_e(zh)}</button>')


def _state_chip(state: str, note: str) -> str:
    zh = _STATE_ZH.get(state, state)
    badge = _STATE_BADGE.get(state, "st-missing")
    tip = f' title="{_e(note)}"' if note else ""
    return f'<span class="badge {badge}"{tip}>{_e(zh)}</span>'


def _take_preview_html(project: Any, take: Any, selected: str | None) -> str:
    """One take's small, LAZY preview card (round X agent XF, pain #8: fine-
    grained clip previews). ``src``/``poster`` are NOT set server-side — only
    ``data-src``/``data-poster`` are — so opening the page never triggers a
    single ffmpeg transcode; ``/storyboard.js`` assigns the real attributes
    only when this drawer is actually opened by a human (never block page
    GET on ffmpeg). Reuses the EXISTING ``/preview`` and ``/thumb`` lazy
    endpoints (``gui.state.playable_url`` — the same helper the review page's
    big player uses) rather than inventing a second transcode path."""
    if take.media_path is None:
        return ""
    from .state import playable_url

    sel_badge = ' <span class="badge st-fresh">已选用</span>' if take.name == selected else ""
    is_audio = take.media_path.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac")
    if is_audio:
        url, _ext = playable_url(project, take.media_path)
        return (
            '<div class="sb-take-prev">'
            f'<div class="sb-take-prev-name">{_e(take.name)}{sel_badge}</div>'
            f'<audio class="sb-take-audio" preload="none" controls '
            f'data-src="{_e(url)}"></audio>'
            '</div>'
        )
    rel = project.relpath(take.media_path)
    url, _ext = playable_url(project, take.media_path)
    thumb_url = "/thumb/" + quote(rel, safe="/")
    return (
        '<div class="sb-take-prev">'
        f'<div class="sb-take-prev-name">{_e(take.name)}{sel_badge}</div>'
        '<div class="sb-take-video-wrap" data-pending="1">'
        f'<video class="sb-take-video" preload="none" muted controls playsinline '
        f'data-src="{_e(url)}" data-poster="{_e(thumb_url)}"></video>'
        '<span class="sb-take-prev-pending muted">点击展开后懒加载预览</span>'
        '</div></div>'
    )


def _detail_row(project: Any, shot_id: str, shot: Any, note: str) -> str:
    """The hidden drawer under a row: the full spec (raw YAML truth) + take notes
    (§3) + the why-stale evidence + per-take lazy previews. colspan spans
    every column."""
    try:
        raw_text = project.shot_path(shot_id).read_text(encoding="utf-8")
    except Exception:
        raw_text = ""
    take_notes = dict(getattr(shot.status, "take_notes", {}) or {})
    notes_html = ""
    if take_notes:
        rows = "".join(
            f'<li><b>{_e(t)}</b>: {_e(txt)}</li>' for t, txt in take_notes.items())
        notes_html = f'<div class="sb-notes"><h4>版本笔记 (take notes)</h4><ul>{rows}</ul></div>'
    why = f'<div class="sb-why hint">状态说明:{_e(note)}</div>' if note else ""
    lock_ctl = _lock_controls(shot_id, shot)

    try:
        takes = project.takes(shot_id, skip_ghosts=True)
    except Exception:
        takes = []
    takes_html = ""
    if takes:
        selected = shot.status.selected_take
        cards = "".join(_take_preview_html(project, t, selected) for t in takes)
        takes_html = (f'<div class="sb-takes"><h4>版本预览 (take previews)</h4>'
                      f'<div class="sb-takes-row">{cards}</div></div>')

    return (
        f'<tr class="sb-detail hidden" data-detail="{_e(shot_id)}"><td colspan="13">'
        f'<div class="sb-drawer">'
        f'<div class="sb-drawer-head"><h3>{_e(shot_id)} 详情 (detail)</h3>'
        f'<button class="btn ghost mini sb-close" type="button">收起 (close)</button></div>'
        f'{why}{lock_ctl}'
        f'{takes_html}'
        f'<h4>完整规格 (full spec)</h4>'
        f'<pre class="sb-spec">{_e(raw_text)}</pre>'
        f'{notes_html}'
        f'</div></td></tr>'
    )


# lockable creative fields offered in the drawer + batch bar (dotted paths the
# `manju lock` seal understands). Kept a small curated set.
_LOCKABLE = (
    ("action.main", "动作"),
    ("dialogue.text", "台词"),
    ("camera", "镜头"),
    ("quality.must_show", "必须出现"),
    ("quality.avoid", "避免"),
    ("scene", "场景"),
    ("characters", "角色"),
)


def _lock_controls(shot_id: str, shot: Any) -> str:
    """Per-field LOCK buttons (seal via the same machinery `manju lock` uses).
    Already-locked fields show the 🔒 state (no unlock — CLI only)."""
    locked = set((shot.locked or {}).keys())
    btns: list[str] = []
    for path, label in _LOCKABLE:
        if path in locked:
            btns.append(f'<span class="chip lock" title="{_e(_UNLOCK_TIP)}">'
                        f'🔒 {_e(label)}</span>')
        else:
            btns.append(f'<button class="btn ghost mini sb-lock" type="button" '
                        f'data-shot="{_e(shot_id)}" data-field="{_e(path)}">'
                        f'锁定 {_e(label)}</button>')
    return ('<div class="sb-lockctl"><h4>锁定 (lock — 解锁仅命令行)</h4>'
            '<div class="chips">' + "".join(btns) + "</div></div>")


# ---------------------------------------------------------------- page render


def render(project: Any, token: str) -> str:
    head = ('<div class="page-h"><h1>分镜工作台 Storyboard</h1>'
            '<span class="muted">每个镜头一行 · 状态(版本新鲜度)与审批(Frame.io 三态)'
            '正交 · 行内可改动作/台词/必须出现/避免 · 锁定即封印(解锁仅命令行)</span></div>')
    try:
        shot_ids = project.shot_ids()
    except Exception as exc:
        return _shell("分镜工作台", token, head + f'<p class="err panel">{_e(exc)}</p>')

    if not shot_ids:
        empty = ('<div class="panel sb-empty"><p>还没有镜头。用 '
                 '<code>manju new --shots N</code> 或在工作台里新建。</p></div>')
        return _shell("分镜工作台", token, head + empty)

    # one asset-matrix + one staleness pass for the whole table (never per-cell).
    try:
        from ..core.assets import asset_matrix, build_lookup

        matrix = asset_matrix(project)
        lookup = build_lookup(matrix)
    except Exception:
        matrix, lookup = {"kinds": {}}, _EmptyLookup()
    scene_names = {r["id"]: (r.get("name") or r["id"])
                   for r in matrix.get("kinds", {}).get("scene", [])}

    try:
        from ..build.stale import evaluate_all

        states = {st.shot_id: st for st in evaluate_all(project)}
    except Exception:
        states = {}

    try:
        from .plan import _routing_on

        routing_on = _routing_on(project)
    except Exception:
        routing_on = False

    rows: list[str] = []
    for sid in shot_ids:
        try:
            shot = project.load_shot(sid)
        except Exception as exc:
            rows.append(f'<tr class="sb-row broken"><td>{_e(sid)}</td>'
                        f'<td colspan="12" class="err">{_e(exc)}</td></tr>')
            continue
        rows.append(_shot_row(project, sid, shot, matrix, lookup, scene_names,
                              states.get(sid), routing_on))
        st = states.get(sid)
        rows.append(_detail_row(project, sid, shot, st.note if st else ""))

    header = (
        '<thead><tr>'
        '<th class="sb-sel"><input type="checkbox" id="sb-all" title="全选"></th>'
        '<th>Shot#</th><th>场景</th><th>角色</th><th>动作/描述</th><th>台词</th>'
        '<th>镜头</th><th>必须出现</th><th>避免</th><th>来源</th>'
        '<th>状态</th><th>锁</th><th>审批</th>'
        '</tr></thead>')
    table = (f'<div class="sb-scroll"><table class="sb-table">{header}'
             f'<tbody>{"".join(rows)}</tbody></table></div>')

    batchbar = (
        '<div id="sb-batchbar" class="panel sb-batchbar hidden">'
        '<span class="sb-count"><b id="sb-n">0</b> 已选</span>'
        '<button class="btn mini" type="button" id="sb-approve-all">批量通过 (approve)</button>'
        '<span class="sb-lockgroup">批量锁定:'
        '<select id="sb-lock-field">'
        + "".join(f'<option value="{_e(p)}">{_e(lbl)}</option>' for p, lbl in _LOCKABLE)
        + '</select>'
        '<button class="btn ghost mini" type="button" id="sb-lock-all">锁定</button>'
        '</span>'
        '<button class="btn ghost mini" type="button" id="sb-clear">清除选择</button>'
        '</div>')

    legend = (
        '<div class="sb-legend muted panel">'
        '词汇(§10):<b>状态</b> 版本新鲜度 — 无版本/待更新/最新/手动置入/待挑选/缺媒体 · '
        '<b>审批</b> Frame.io 三态 — 待审/进行中/已通过(点击流转) · '
        '<b>角色</b> 实心=已登记(别名可解析)，空心 @=未登记提及(manju mentions --apply) · '
        '<b>锁</b> 🔒 已封印，解锁仅命令行 manju unlock'
        '</div>')

    body = head + batchbar + table + legend
    return _shell("分镜工作台", token, body)


class _EmptyLookup:
    def candidates(self, token: str) -> list:  # noqa: D401 - degenerate fallback
        return []


def _shot_row(project: Any, sid: str, shot: Any, matrix: dict[str, Any], lookup: Any,
              scene_names: dict[str, str], st: Any, routing_on: bool) -> str:
    from ..core.writes import shot_text_hash

    scene = shot.scene or ""
    scene_label = scene_names.get(scene, scene) if scene else "—"
    cam = f"{shot.camera.shot_size} · {shot.camera.movement}"
    provider = _provider(project, shot, routing_on)
    state = st.state.value if st is not None else "missing"
    note = st.note if st is not None else ""
    review_state = shot.status.review_state
    # round AA item 5 (#1): the CAS token this row's rendered field values were
    # read at — /storyboard.js echoes it back as `expected_rev` on a cell save
    # (_act_sb_edit) so a save against a row a human sat on for a while cannot
    # silently clobber an edit that landed from elsewhere in the meantime.
    rev = shot_text_hash(project, sid)

    return (
        f'<tr class="sb-row" data-shot="{_e(sid)}" data-rev="{_e(rev)}">'
        f'<td class="sb-sel"><input type="checkbox" class="sb-check" '
        f'data-shot="{_e(sid)}"></td>'
        f'<td class="sb-id"><button class="sb-toggle" type="button" '
        f'title="展开详情">▸</button> {_e(sid)}</td>'
        f'<td class="sb-scene" title="{_e(scene)}">{_e(scene_label)}</td>'
        f'<td class="sb-chars">{_char_chips(shot, matrix, lookup)}</td>'
        + _editable(sid, "action.main", shot.action.main or "")
        + _editable(sid, "dialogue.text", shot.dialogue.text or "")
        + f'<td class="sb-cam">{_e(cam)}</td>'
        + _list_cell(sid, "quality.must_show", list(shot.quality.must_show))
        + _list_cell(sid, "quality.avoid", list(shot.quality.avoid))
        + f'<td class="sb-src" title="生成来源 (provider)">{_e(provider)}</td>'
        + f'<td class="sb-state">{_state_chip(state, note)}</td>'
        + f'<td class="sb-lock">{_lock_chips(shot.locked or {})}</td>'
        + f'<td class="sb-approval">{_approval_chip(sid, review_state)}</td>'
        + '</tr>'
    )


def _provider(project: Any, shot: Any, routing_on: bool) -> str:
    """来源 column — the routing-resolved head provider, via the SAME helper the
    plan modal uses so the two never disagree."""
    try:
        from .plan import _provider_label

        return _provider_label(project, shot, routing_on,
                               getattr(shot.generation, "provider", None) or None)
    except Exception:
        return getattr(shot.generation, "provider", None) or "auto(兜底链)"


# ============================================================ assets (css/js)


def render_storyboard_css() -> str:
    return _CSS


def render_storyboard_js() -> str:
    return _JS


_CSS = """
/* 分镜工作台 storyboard (round-U). Loaded AFTER /app.css + /pages.css; reuses
   their palette (--panel/--line/--muted…) and chips (.chip/.badge.st-*). */
.sb-scroll { overflow-x: auto; margin: .8rem 0; border: 1px solid var(--line); border-radius: 8px; }
.sb-table { border-collapse: collapse; width: 100%; min-width: 1100px; font-size: .82rem; }
.sb-table th, .sb-table td {
  border-bottom: 1px solid var(--line); padding: .4rem .5rem; text-align: left;
  vertical-align: top;
}
.sb-table thead th {
  position: sticky; top: 0; background: var(--panel2); z-index: 1;
  white-space: nowrap; font-weight: 700;
}
.sb-row:hover { background: var(--panel2); }
.sb-row.broken .err { color: var(--err); }
.sb-sel { width: 1.6rem; text-align: center !important; }
.sb-id { white-space: nowrap; font-family: var(--mono); }
.sb-toggle {
  background: none; border: none; color: var(--muted); cursor: pointer;
  font: inherit; padding: 0 .2rem;
}
.sb-toggle.open { transform: rotate(90deg); display: inline-block; }
.sb-cam { white-space: nowrap; font-family: var(--mono); font-size: .78rem; }
.sb-src { font-family: var(--mono); font-size: .78rem; }
.sb-char.sb-hollow { background: transparent; border-style: dashed; color: var(--warn); }
.sb-char.sb-dangling { color: var(--muted); border-style: dotted; }
.chip.mini { font-size: .72rem; padding: .06rem .4rem; }
.sb-edit { position: relative; max-width: 22ch; cursor: text; }
.sb-edit .cell-text { display: inline-block; }
.sb-edit .edit-btn {
  opacity: 0; background: none; border: none; color: var(--muted); cursor: pointer;
  font: inherit; position: absolute; top: .2rem; right: .2rem;
}
.sb-edit:hover .edit-btn { opacity: 1; }
.sb-edit.editing .cell-text, .sb-edit.editing .edit-btn { display: none; }
.sb-editor { display: flex; flex-direction: column; gap: .3rem; }
.sb-editor textarea, .sb-editor input {
  background: var(--bg); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .25rem .4rem; font: inherit; font-size: .8rem;
  width: 22ch; min-width: 12ch;
}
.sb-editor .sb-editor-btns { display: flex; gap: .3rem; }
.sb-approve { cursor: pointer; }
.sb-batchbar { display: flex; flex-wrap: wrap; gap: .6rem; align-items: center; margin: .6rem 0; }
.sb-lockgroup { display: inline-flex; gap: .35rem; align-items: center; }
.sb-batchbar select {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .2rem .4rem; font: inherit; font-size: .8rem;
}
.sb-drawer { padding: .4rem .2rem .6rem; }
.sb-drawer-head { display: flex; justify-content: space-between; align-items: center; }
.sb-drawer h3 { font-size: .95rem; margin: 0; }
.sb-drawer h4 { font-size: .82rem; margin: .6rem 0 .3rem; color: var(--muted); }
.sb-spec {
  background: var(--bg); border: 1px solid var(--line); border-radius: 6px;
  padding: .5rem .6rem; font-family: var(--mono); font-size: .78rem;
  white-space: pre-wrap; word-break: break-word; max-height: 340px; overflow: auto;
}
.sb-notes ul { margin: .2rem 0 0 1.1rem; }
.sb-lockctl { margin: .4rem 0; }
.sb-takes { margin: .5rem 0; }
.sb-takes-row { display: flex; flex-wrap: wrap; gap: .6rem; }
.sb-take-prev { width: 11rem; }
.sb-take-prev-name { font-size: .76rem; margin-bottom: .2rem; }
.sb-take-video-wrap { position: relative; }
.sb-take-video, .sb-take-audio { width: 100%; display: block; border-radius: 6px; background: var(--bg); }
.sb-take-video { max-height: 110px; }
.sb-take-prev-pending {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  font-size: .72rem; text-align: center; padding: .3rem; pointer-events: none;
}
.sb-take-video-wrap[data-pending="0"] .sb-take-prev-pending { display: none; }
.sb-legend { font-size: .8rem; line-height: 1.8; margin-top: 1rem; }
.sb-empty { margin-top: 1rem; }
.btn.mini, .chip.mini { font-size: .74rem; }
"""


_JS = r"""
"use strict";
(function () {
  var META = document.querySelector('meta[name="manju-token"]');
  var TOKEN = META ? META.getAttribute("content") : "";
  var REVIEW_NEXT = { needs_review: "in_progress", in_progress: "approved",
                      approved: "needs_review" };
  var LIST_FIELDS = { "quality.must_show": 1, "quality.avoid": 1 };

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
    setTimeout(function () { el.remove(); }, 3600);
  }
  function reloadSoon() { setTimeout(function () { location.reload(); }, 400); }

  // ---- row detail drawer ----------------------------------------------------
  // round X agent XF: per-take previews inside the drawer are NEVER wired to
  // src/poster server-side (only data-src/data-poster) — this is the ONLY
  // place that assigns them, and only on the FIRST open, so a page with many
  // shots never triggers a single ffmpeg transcode just from being loaded.
  function loadTakePreviews(det) {
    det.querySelectorAll(".sb-take-video[data-src]").forEach(function (v) {
      var wrap = v.closest(".sb-take-video-wrap");
      v.poster = v.getAttribute("data-poster") || "";
      v.src = v.getAttribute("data-src");
      v.removeAttribute("data-src");
      v.addEventListener("loadeddata", function () {
        if (wrap) wrap.setAttribute("data-pending", "0");
      });
      v.addEventListener("error", function () {
        if (wrap) wrap.setAttribute("data-pending", "0");
      });
    });
    det.querySelectorAll("audio.sb-take-audio[data-src]").forEach(function (a) {
      a.src = a.getAttribute("data-src");
      a.removeAttribute("data-src");
    });
  }
  function toggleDetail(shot, force) {
    var det = document.querySelector('tr.sb-detail[data-detail="' + CSS.escape(shot) + '"]');
    if (!det) return;
    var open = (force === undefined) ? det.classList.contains("hidden") : force;
    det.classList.toggle("hidden", !open);
    if (open) loadTakePreviews(det);
    var tog = document.querySelector('tr.sb-row[data-shot="' + CSS.escape(shot) + '"] .sb-toggle');
    if (tog) { tog.classList.toggle("open", open); tog.textContent = open ? "▾" : "▸"; }
  }

  // ---- inline edit ----------------------------------------------------------
  function startEdit(cell) {
    if (cell.classList.contains("editing")) return;
    var field = cell.getAttribute("data-field");
    var shot = cell.getAttribute("data-shot");
    var isList = !!LIST_FIELDS[field];
    var current;
    if (isList) current = cell.getAttribute("data-raw") || "";
    else {
      var txt = cell.querySelector(".cell-text");
      current = (txt && txt.textContent === "—") ? "" : (txt ? txt.textContent : "");
    }
    cell.classList.add("editing");
    var box = document.createElement("div");
    box.className = "sb-editor";
    var input = document.createElement(isList ? "textarea" : "textarea");
    if (isList) input.rows = 3; else input.rows = 2;
    input.value = current;
    var btns = document.createElement("div");
    btns.className = "sb-editor-btns";
    var save = document.createElement("button");
    save.type = "button"; save.className = "btn mini"; save.textContent = "保存";
    var cancel = document.createElement("button");
    cancel.type = "button"; cancel.className = "btn ghost mini"; cancel.textContent = "取消";
    btns.appendChild(save); btns.appendChild(cancel);
    box.appendChild(input); box.appendChild(btns);
    cell.appendChild(box);
    input.focus();
    function done() { box.remove(); cell.classList.remove("editing"); }
    cancel.addEventListener("click", function (ev) { ev.stopPropagation(); done(); });
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { ev.stopPropagation(); done(); }
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); commit(); }
    });
    save.addEventListener("click", function (ev) { ev.stopPropagation(); commit(); });
    function commit() {
      save.disabled = true;
      /* round AA item 5 (#1): the row's data-rev is the CAS token this cell's
       * value was rendered at — echoed back as expected_rev so a save against
       * a row that went stale (edited elsewhere while this cell sat open) is
       * refused (409) instead of silently overwriting. */
      var row = cell.closest("tr.sb-row");
      var editBody = { shot: shot, field: field, value: input.value };
      var rev = row ? row.getAttribute("data-rev") : null;
      if (rev) editBody.expected_rev = rev;
      post("/api/storyboard/edit", editBody).then(function (res) {
        if (res.status === 200 && res.data.ok) { toast(shot + " " + field + " 已保存", true); reloadSoon(); }
        else if (res.status === 409) { toast(res.data.error || "字段被锁定", false); save.disabled = false; }
        else { toast(res.data.error || "保存失败", false); save.disabled = false; }
      }).catch(function () { toast("网络错误", false); save.disabled = false; });
    }
  }

  // ---- approval cycle -------------------------------------------------------
  function cycleApproval(btn) {
    var shot = btn.getAttribute("data-shot");
    var cur = btn.getAttribute("data-review");
    var next = REVIEW_NEXT[cur] || "needs_review";
    btn.disabled = true;
    post("/api/storyboard/approve", { shot: shot, review: next }).then(function (res) {
      if (res.status === 200 && res.data.ok) { reloadSoon(); }
      else { toast(res.data.error || "审批失败", false); btn.disabled = false; }
    }).catch(function () { toast("网络错误", false); btn.disabled = false; });
  }

  // ---- lock a field ---------------------------------------------------------
  function lockField(btn) {
    var shot = btn.getAttribute("data-shot");
    var field = btn.getAttribute("data-field");
    btn.disabled = true;
    post("/api/lock", { shot: shot, field: field }).then(function (res) {
      if (res.status === 200 && res.data.ok) { toast(shot + " " + field + " 已锁定", true); reloadSoon(); }
      else { toast(res.data.error || "锁定失败", false); btn.disabled = false; }
    }).catch(function () { toast("网络错误", false); btn.disabled = false; });
  }

  // ---- batch selection ------------------------------------------------------
  function selected() {
    return Array.prototype.slice
      .call(document.querySelectorAll(".sb-check:checked"))
      .map(function (c) { return c.getAttribute("data-shot"); });
  }
  function refreshBatchbar() {
    var sel = selected();
    var bar = document.getElementById("sb-batchbar");
    var n = document.getElementById("sb-n");
    if (n) n.textContent = String(sel.length);
    if (bar) bar.classList.toggle("hidden", sel.length === 0);
  }

  document.addEventListener("change", function (ev) {
    if (ev.target.id === "sb-all") {
      var on = ev.target.checked;
      document.querySelectorAll(".sb-check").forEach(function (c) { c.checked = on; });
      refreshBatchbar();
    } else if (ev.target.classList.contains("sb-check")) {
      refreshBatchbar();
    }
  });

  document.addEventListener("click", function (ev) {
    var t = ev.target;
    if (t.closest(".sb-editor")) return;  // clicks inside the editor never bubble to row

    var editBtn = t.closest(".edit-btn");
    if (editBtn) { ev.stopPropagation(); startEdit(editBtn.closest(".sb-edit")); return; }

    var approve = t.closest(".sb-approve");
    if (approve) { ev.stopPropagation(); cycleApproval(approve); return; }

    var lockBtn = t.closest(".sb-lock");
    if (lockBtn) { ev.stopPropagation(); lockField(lockBtn); return; }

    var close = t.closest(".sb-close");
    if (close) { var dr = close.closest("tr.sb-detail"); if (dr) toggleDetail(dr.getAttribute("data-detail"), false); return; }

    if (t.closest(".sb-sel") || t.tagName === "INPUT" || t.closest("a")) return;
    if (t.closest(".sb-edit")) return;  // editable cell: dblclick / ✎ edits, no drawer toggle

    var toggle = t.closest(".sb-toggle");
    if (toggle) { ev.stopPropagation(); toggleDetail(toggle.closest(".sb-row").getAttribute("data-shot")); return; }

    var row = t.closest("tr.sb-row");
    if (row && !row.classList.contains("broken")) { toggleDetail(row.getAttribute("data-shot")); }
  });

  document.addEventListener("dblclick", function (ev) {
    var cell = ev.target.closest(".sb-edit");
    if (cell) { ev.preventDefault(); startEdit(cell); }
  });

  // batch bar buttons
  document.addEventListener("click", function (ev) {
    if (ev.target.id === "sb-clear") {
      document.querySelectorAll(".sb-check, #sb-all").forEach(function (c) { c.checked = false; });
      refreshBatchbar();
      return;
    }
    if (ev.target.id === "sb-approve-all") {
      var sel = selected();
      if (!sel.length) return;
      ev.target.disabled = true;
      post("/api/storyboard/approve", { shots: sel, review: "approved" }).then(function (res) {
        if (res.status === 200 && res.data.ok) { toast("已批量通过 " + (res.data.changed || sel.length), true); reloadSoon(); }
        else { toast(res.data.error || "批量通过失败", false); ev.target.disabled = false; }
      }).catch(function () { toast("网络错误", false); ev.target.disabled = false; });
      return;
    }
    if (ev.target.id === "sb-lock-all") {
      var sel2 = selected();
      if (!sel2.length) return;
      var field = document.getElementById("sb-lock-field").value;
      ev.target.disabled = true;
      post("/api/storyboard/lock-batch", { shots: sel2, field: field }).then(function (res) {
        if (res.status === 200 && res.data.ok) { toast("已批量锁定 " + field + " ×" + (res.data.locked || 0), true); reloadSoon(); }
        else { toast(res.data.error || "批量锁定失败", false); ev.target.disabled = false; }
      }).catch(function () { toast("网络错误", false); ev.target.disabled = false; });
      return;
    }
  });
})();
"""
