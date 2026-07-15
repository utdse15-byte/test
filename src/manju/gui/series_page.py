"""剧集工作台 /series — the GUI half of the series umbrella (round X, agent XD,
user pain #4). core/series.py (``Series.find`` / ``series_status`` /
``new_episode`` / ``sync_bible`` / ``series_characters`` / ``split_script``) is
CLI-first; this page is a strict, read-mostly client of that SAME core, mirroring
the round-U/V pages' stance (:mod:`manju.gui.create_page`, :mod:`manju.gui.director_page`):

  * server-rendered — a plain GET bakes the live series aggregation into the
    DOM; ``/series.js`` layers on the 新建集 form, the safe sync-bible apply
    button and the copy-to-clipboard affordances (no page state of its own);
  * CSP-safe — CSS/JS in external files, no inline handlers or ``style=``,
    every mutating POST carries ``X-Manju-Token``;
  * XSS-safe — all server text (series/episode names, bible entry values,
    error strings) is ``html.escape``-d here; the script only ever writes
    ``textContent``, never ``innerHTML``;
  * one core — the episode table is ``series_status`` verbatim, the sync view
    is ``sync_bible(apply=False)`` verbatim (enriched here with the RAW bible
    entry values for the side-by-side diff — sync_bible's report itself only
    carries ``kind:id`` keys), the characters view is ``series_characters``
    verbatim, and the split-script panel is ``split_script(apply=False)``.

Containment stance (mirrors the GUI's `unlock`/`gc --hard` absence, §5/§11):
this page's ONE mutating action beyond 新建集 is the sync-bible **apply**
button, and it is hard-wired to ``sync_bible(apply=True)`` with **no** `force`
— the safe missing-only subset. A diverged entry is NEVER writable from the
GUI; the page only ever offers the equivalent ``manju series sync-bible
--force kind:id`` / ``manju series split-script <file> --force <eid>``
commands as copyable text, exactly like the workbench's ``unlock`` stays
CLI-only. The GUI server is bound to exactly ONE project (§ server.py), so
"open" for a DIFFERENT episode never hot-swaps ``self.server.project`` — it
shows the absolute path + the ``manju gui`` command to run there, honestly.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

__all__ = [
    "PAGE_PATHS_SERIES",
    "render",
    "render_series_css",
    "render_series_js",
]

PAGE_PATHS_SERIES = frozenset({"/series"})

# shots_by_state key -> the .badge st-* suffix already styled by /pages.css
# (mirrors gui.cockpit's STATE_ORDER/STATE_ZH, whose labels we reuse for 中文).
_STATE_BADGE = {
    "fresh": "fresh", "manual": "fresh", "stale": "stale",
    "needs_selection": "needs", "missing": "needs", "broken": "needs",
}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


# ------------------------------------------------------------------ shell


def _shell(title: str, token: str, body: str) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    active = "/series"
    nav, bcls = chrome(active)  # no project: this IS the series workbench already
    return (
        "<!doctype html>\n"
        '<html lang="zh">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/series.css">\n'
        + GLOSSARY_HEAD
        + '<script src="/webclient.js" defer></script>\n'
        + '<script src="/common.js" defer></script>\n'
        + '<script src="/series.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{active}" class="{bcls}">\n'
        + nav
        + "\n<main>\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


def render(project: Any, token: str) -> str:
    from ..core.series import Series

    head = (
        '<div class="page-h"><h1>剧集工作台 Series</h1>'
        '<span class="muted">跨集聚合(series_status/series_characters/series_continuity)'
        "与保守同步(sync_bible)的只读+安全写视图 —— 强制覆盖(--force)与长稿拆分落地只留在"
        "命令行</span></div>"
    )

    try:
        series = Series.find_or_none(project.root)
    except Exception:
        series = None

    if series is None:
        body = (
            head
            + '<p class="muted panel">当前项目不属于任何剧集(未在其上级目录中找到 '
            "series.yaml)—— 它是一个独立项目。用命令行新建一个剧集伞状目录,把项目"
            "登记为其分集:"
            '<pre class="sr-cmd">manju series new &lt;dir&gt; --name &lt;剧集名&gt;\n'
            "manju series new-episode E01 --title &lt;标题&gt;</pre></p>"
        )
        return _shell("剧集工作台", token, body)

    try:
        cfg = series.load_config()
    except Exception as exc:
        return _shell("剧集工作台", token,
                      head + f'<p class="err panel">series.yaml 读取失败:{_e(exc)}</p>')

    body = (
        head
        + _episodes_section(series, project)
        + _continuity_section(series)
        + _sync_bible_section(series)
        + _characters_section(series)
        + _split_script_section(series)
    )
    return _shell(f"剧集工作台 · {cfg.name}", token, body)


# ================================================================ A. episodes


def _episodes_section(series: Any, project: Any) -> str:
    from ..core.series import series_status

    try:
        info = series_status(series)
    except Exception as exc:
        return (
            '<section class="panel sr-episodes"><h2>分集 Episodes</h2>'
            f'<p class="err">读取失败:{_e(exc)}</p></section>'
        )

    rows: list[str] = []
    for e in info["episodes"]:
        eid = e["id"]
        if e.get("error"):
            rows.append(
                f'<tr class="sr-ep-row"><td>{_e(eid)}</td><td>{_e(e.get("title"))}</td>'
                f'<td colspan="4" class="err">✗ {_e(e["error"])}</td></tr>'
            )
            continue
        ep_dir = series.episode_project_dir(eid)
        by = e.get("shots_by_state") or {}
        chip_items = [
            f'<span class="badge st-{_STATE_BADGE.get(s, "needs")}">{_e(s)} {n}</span>'
            for s, n in by.items() if n
        ]
        chips = "".join(chip_items) or '<span class="muted">无镜头</span>'
        final = e.get("latest_final")
        final_html = (f'<span class="badge st-fresh">{_e(final)}</span>' if final
                      else '<span class="muted">无 final</span>')
        cost = e.get("cost") or 0.0
        cur = e.get("currency") or ""
        spend_html = _e(f"{cost:g} {cur}".strip())

        if ep_dir == project.root:
            open_html = '<span class="badge st-fresh">当前项目 current</span>'
        else:
            cmd = f'cd "{ep_dir}" && manju gui'
            open_html = (
                '<div class="sr-open">'
                f'<div class="sr-path muted">{_e(ep_dir)}</div>'
                f'<code class="sr-cmd">{_e(cmd)}</code>'
                f'<button type="button" class="btn ghost mini" data-act="copy" '
                f'data-copy="{_e(cmd)}">复制命令</button></div>'
            )
        rows.append(
            f'<tr class="sr-ep-row" data-eid="{_e(eid)}">'
            f'<td>{_e(eid)}</td><td>{_e(e.get("title"))}</td>'
            f'<td class="sr-chips">{chips}</td><td>{final_html}</td>'
            f'<td>{spend_html}</td><td>{open_html}</td></tr>'
        )

    t = info["totals"]
    totals_html = (
        f'<p class="muted">合计 {t["ok"]}/{t["episodes"]} 集可读'
        + (f' · {t["errors"]} 集出错' if t.get("errors") else "")
        + f' · 镜头 {t["shots"]} · 成片 {t["finals"]} · 花费 '
        + _e(f'{t["cost"]:g} {t.get("currency") or ""}'.strip())
        + "</p>"
    )

    table = (
        '<div class="tablewrap"><table class="sr-table"><thead><tr>'
        "<th>集</th><th>标题</th><th>镜头状态</th><th>最新成片</th><th>花费</th><th>打开</th>"
        "</tr></thead><tbody>"
        + ("".join(rows) or '<tr><td colspan="6" class="muted">还没有分集</td></tr>')
        + "</tbody></table></div>"
    )

    new_form = (
        '<div class="sr-newep panel"><h3>新建集 New episode</h3>'
        '<div class="sr-newep-row">'
        '<label class="sr-field">集号 eid<input type="text" id="sr-new-eid" '
        'placeholder="E03 或 slug"></label>'
        '<label class="sr-field">标题 title<input type="text" id="sr-new-title" '
        'placeholder="可选"></label>'
        '<button type="button" class="btn" id="sr-new-btn">新建 create</button>'
        "</div><div id=\"sr-new-result\" class=\"muted\"></div></div>"
    )

    return (
        f'<section class="panel sr-episodes"><h2>分集 Episodes'
        f' <span class="muted">· {_e(info["series"])}</span></h2>'
        + table + totals_html + new_form + "</section>"
    )


# ============================================================ A2. continuity


# verdict (series_continuity) -> the .badge st-* suffix (severity order mirrors
# _STATE_BADGE above: fresh=ok, stale=needs-attention-soon, needs=incomplete,
# broken=worst).
_VERDICT_BADGE = {
    "完整": "st-fresh", "待同步": "st-stale", "缺素材": "st-needs", "有问题": "st-broken",
}


def _asset_grid(title: str, rows: list[dict[str, Any]], eps: list[str]) -> str:
    """One present/diverged/appearances grid — the SAME rendering shape
    `_characters_section` already uses, generalized so scenes/props reuse it
    (mirrors core/series.py's `_asset_continuity_matrix` generalizing the
    engine those grids read from)."""
    if not rows:
        return ""
    trows: list[str] = []
    for c in rows:
        cid, name = c["id"], c.get("name")
        cells: list[str] = []
        for cell in c["episodes"]:
            if cell.get("error"):
                cells.append(f'<td class="err">✗ {_e(cell["error"])}</td>')
                continue
            if not cell.get("present"):
                cells.append('<td class="muted">不在场</td>')
                continue
            n = len(cell.get("appearances") or [])
            if cell.get("diverged"):
                label = "语音分歧 voice" if cell.get("voice_diverged") else "分歧 diverged"
                badge = f'<span class="badge st-needs">{_e(label)}</span>'
            else:
                badge = '<span class="badge st-fresh">在场</span>'
            cells.append(f'<td>{badge}<span class="muted"> 出场×{n}</span></td>')
        name_html = f' <span class="muted">{_e(name)}</span>' if name else ""
        trows.append(f'<tr><td><b>{_e(cid)}</b>{name_html}</td>' + "".join(cells) + "</tr>")
    header_cells = "".join(f"<th>{_e(eid)}</th>" for eid in eps)
    return (
        f'<h4>{_e(title)}</h4>'
        '<div class="tablewrap"><table class="sr-table"><thead><tr>'
        f"<th>id</th>{header_cells}</tr></thead><tbody>" + "".join(trows)
        + "</tbody></table></div>"
    )


def _continuity_section(series: Any) -> str:
    """全局连续性 Continuity (round AA7, goal item 7) — a strict render of
    `series_continuity`: the episode-verdict matrix (镜头/检查/同步/引用 + a
    verdict chip), the characters/scenes/props continuity grids (same pattern
    as `_characters_section`, generalized), and the voice/packaging outlier
    callouts. Read-only — no job (see core.series.series_continuity's
    docstring for why); 待同步 rows link straight into the sync-bible section
    below instead of duplicating its diff view."""
    from ..core.series import series_continuity

    try:
        view = series_continuity(series)
    except Exception as exc:
        return (
            '<section class="panel sr-continuity"><h2>全局连续性 Continuity</h2>'
            f'<p class="err">读取失败:{_e(exc)}</p></section>'
        )

    rows: list[str] = []
    for e in view["episodes"]:
        eid = e["id"]
        if e.get("error"):
            rows.append(
                f'<tr class="sr-ct-row"><td>{_e(eid)}</td><td>{_e(e.get("title"))}</td>'
                f'<td colspan="5" class="err">✗ {_e(e["error"])}</td></tr>'
            )
            continue
        by = e.get("shots_by_state") or {}
        shots_html = " ".join(f"{_e(s)}={n}" for s, n in by.items() if n) or "—"
        bible = e.get("bible_sync") or {}
        refs = e.get("refs") or {}
        sync_text = f"新增{bible.get('added', 0)}/分歧{bible.get('diverged', 0)}"
        sync_html = (
            f'<a href="#sr-sync-ep-{_e(eid)}">{_e(sync_text)}</a>'
            if (bible.get("added") or bible.get("diverged")) else _e(sync_text)
        )
        verdict = e.get("verdict", "")
        badge_cls = _VERDICT_BADGE.get(verdict, "st-needs")
        rows.append(
            f'<tr class="sr-ct-row" data-eid="{_e(eid)}">'
            f'<td>{_e(eid)}</td><td>{_e(e.get("title"))}</td>'
            f'<td>{_e(shots_html)}</td>'
            f'<td>错误{e.get("check_errors", 0)}/警告{e.get("check_warnings", 0)}</td>'
            f'<td>{sync_html}</td>'
            f'<td>孤儿{refs.get("orphan", 0)}/缺失{refs.get("missing", 0)}</td>'
            f'<td><span class="badge {badge_cls}">{_e(verdict)}</span></td></tr>'
        )

    matrix = (
        '<div class="tablewrap"><table class="sr-table"><thead><tr>'
        "<th>集</th><th>标题</th><th>镜头</th><th>检查</th><th>同步</th><th>引用</th><th>结论</th>"
        "</tr></thead><tbody>"
        + ("".join(rows) or '<tr><td colspan="7" class="muted">还没有分集</td></tr>')
        + "</tbody></table></div>"
    )

    t = view["totals"]
    totals_html = (
        f'<p class="muted">合计 完整 {t.get("complete", 0)} · 缺素材 {t.get("missing_assets", 0)}'
        f' · 有问题 {t.get("problem", 0)} · 待同步 {t.get("needs_sync", 0)}'
        + (f" · {t['errors']} 集出错" if t.get("errors") else "")
        + "</p>"
    )

    eps = [e["id"] for e in view["episodes"] if not e.get("error")]
    grids_html = "".join(
        g for g in (
            _asset_grid("角色 characters", view.get("characters") or [], eps),
            _asset_grid("场景 scenes", view.get("scenes") or [], eps),
            _asset_grid("道具 props", view.get("props") or [], eps),
        ) if g
    )

    outliers = view.get("packaging_outliers") or []
    outlier_html = ""
    if outliers:
        items = "".join(
            f'<li><b>{_e(o["field"])}</b>:多数 {_e(o["majority"])} · 例外 '
            + _e("、".join(f"{x['episode']}={x['value']}" for x in o["outliers"]))
            + "</li>"
            for o in outliers
        )
        outlier_html = (
            '<div class="sr-ct-outliers"><h4>片头/片尾/封面偏差 packaging '
            '<span class="muted">(参考性提示,不代表错误)</span></h4>'
            f"<ul>{items}</ul></div>"
        )

    return (
        '<section class="panel sr-continuity"><h2>全局连续性 Continuity'
        f' <span class="muted">· {_e(view["series"])}</span></h2>'
        f'<p class="muted">{_e(view.get("note", ""))}</p>'
        + matrix + totals_html + grids_html + outlier_html + "</section>"
    )


# ============================================================= B. sync-bible


def _bible_entry(base_dir: Path, kind: str, entry_id: str) -> Any:
    from ..core.yamlio import read_yaml

    path = Path(base_dir) / "bible" / f"{kind}.yaml"
    if not path.exists():
        return None
    data = read_yaml(path) or {}
    if not isinstance(data, dict):
        return None
    v = data.get(entry_id)
    return v if isinstance(v, dict) else None


def _diff_row(series: Any, ep_dir: Path, key: str, *, diverged: bool) -> str:
    from ..core.yamlio import dump_yaml

    kind, _, entry_id = key.partition(":")
    s_val = _bible_entry(series.root, kind, entry_id)
    e_val = _bible_entry(ep_dir, kind, entry_id)
    s_text = dump_yaml(s_val) if s_val is not None else "(无 none)"
    e_text = dump_yaml(e_val) if e_val is not None else "(缺失 missing)"

    force_html = ""
    if diverged:
        cmd = f"manju series sync-bible --force {key}"
        force_html = (
            f'<code class="sr-cmd">{_e(cmd)}</code>'
            f'<button type="button" class="btn ghost mini" data-act="copy" '
            f'data-copy="{_e(cmd)}">复制</button>'
        )
    return (
        f'<div class="sr-diff" data-key="{_e(key)}">'
        f'<div class="sr-diff-head"><b>{_e(key)}</b>{force_html}</div>'
        '<div class="sr-diff-cols">'
        f'<div class="sr-diff-col"><span class="muted">剧集 series</span>'
        f'<pre>{_e(s_text)}</pre></div>'
        f'<div class="sr-diff-col"><span class="muted">分集 episode</span>'
        f'<pre>{_e(e_text)}</pre></div>'
        "</div></div>"
    )


def _sync_bible_section(series: Any) -> str:
    from ..core.series import sync_bible

    try:
        report = sync_bible(series, apply=False)
    except Exception as exc:
        return (
            '<section class="panel sr-sync"><h2>Bible 同步 Sync-bible</h2>'
            f'<p class="err">读取失败:{_e(exc)}</p></section>'
        )

    ep_blocks: list[str] = []
    for ep in report["episodes"]:
        eid = ep["id"]
        # round AA7: the continuity dashboard's 待同步 episodes link straight
        # here (`#sr-sync-ep-<eid>`) instead of duplicating the diff view.
        anchor_id = f'sr-sync-ep-{_e(eid)}'
        if ep.get("error"):
            ep_blocks.append(
                f'<div class="sr-sync-ep panel" id="{anchor_id}"><h4>{_e(eid)}</h4>'
                f'<p class="err">✗ {_e(ep["error"])}</p></div>'
            )
            continue
        ep_dir = series.episode_project_dir(eid)
        missing_html = "".join(
            _diff_row(series, ep_dir, k, diverged=False) for k in ep["added"])
        diverged_html = "".join(
            _diff_row(series, ep_dir, k, diverged=True) for k in ep["diverged"])
        parts = []
        if missing_html:
            parts.append(
                '<div class="sr-sync-group"><h5>缺失 missing · 可安全新增</h5>'
                + missing_html + "</div>")
        if diverged_html:
            parts.append(
                '<div class="sr-sync-group"><h5>分歧 diverged · 只读,需命令行 --force</h5>'
                + diverged_html + "</div>")
        parts.append(f'<p class="muted">已同步 in-sync:{ep.get("in_sync", 0)} 条</p>')
        ep_blocks.append(
            f'<div class="sr-sync-ep panel" id="{anchor_id}"><h4>{_e(eid)}</h4>'
            + "".join(parts) + "</div>")

    t = report["totals"]
    totals_html = (
        f'<p class="muted">合计 新增 {t["added"]} · 分歧 {t["diverged"]} · '
        f'覆盖 {t["overwritten"]} · 拒绝 {t["refused"]} · 已同步 {t["in_sync"]}</p>'
    )

    apply_btn = (
        '<div class="sr-sync-apply btnrow">'
        '<button type="button" class="btn" id="sr-sync-apply-btn">'
        "应用安全同步(仅新增)apply safe subset</button>"
        '<span class="sr-hint muted" '
        'title="强制覆盖分歧条目(--force)只能在命令行执行,和 unlock 的收口策略一致 —— '
        'GUI 从不做隐式的危险覆盖。">ⓘ 为什么分歧条目不能在这里覆盖?</span>'
        '<div id="sr-sync-result" class="muted"></div></div>'
    )

    return (
        '<section class="panel sr-sync"><h2>Bible 同步 Sync-bible</h2>'
        f'<p class="muted">{_e(report["note"])}</p>'
        + totals_html + apply_btn + "".join(ep_blocks) + "</section>"
    )


# ============================================================ C. characters


def _characters_section(series: Any) -> str:
    from ..core.series import series_characters

    try:
        view = series_characters(series)
    except Exception as exc:
        return (
            '<section class="panel sr-chars"><h2>全局角色 Characters</h2>'
            f'<p class="err">读取失败:{_e(exc)}</p></section>'
        )

    eps = view["episodes"]
    rows: list[str] = []
    for c in view["characters"]:
        cid, name = c["id"], c.get("name")
        cells: list[str] = []
        for cell in c["episodes"]:
            if cell.get("error"):
                cells.append(f'<td class="err">✗ {_e(cell["error"])}</td>')
                continue
            if not cell.get("present"):
                cells.append('<td class="muted">不在场</td>')
                continue
            n = len(cell.get("appearances") or [])
            badge = ('<span class="badge st-needs">分歧 diverged</span>'
                     if cell.get("diverged") else
                     '<span class="badge st-fresh">在场</span>')
            cells.append(f'<td>{badge}<span class="muted"> 出场×{n}</span></td>')
        name_html = f' <span class="muted">{_e(name)}</span>' if name else ""
        rows.append(
            f'<tr><td><b>{_e(cid)}</b>{name_html}</td>' + "".join(cells) + "</tr>"
        )

    header_cells = "".join(f"<th>{_e(eid)}</th>" for eid in eps)
    table = (
        '<div class="tablewrap"><table class="sr-table"><thead><tr>'
        f"<th>角色</th>{header_cells}</tr></thead><tbody>"
        + ("".join(rows)
           or f'<tr><td colspan="{1 + len(eps)}" class="muted">剧集 bible 还没有角色</td></tr>')
        + "</tbody></table></div>"
    )

    eo = view.get("episode_only") or []
    eo_html = ""
    if eo:
        items = "".join(
            f'<li>{_e(x["id"])} → {_e("、".join(x["episodes"]))}</li>' for x in eo
        )
        eo_html = (
            f'<details class="sr-eo"><summary class="muted">仅存在于分集 bible '
            f"episode-only({len(eo)})</summary><ul>{items}</ul></details>"
        )

    return (
        '<section class="panel sr-chars"><h2>全局角色 Characters</h2>'
        f'<p class="muted">{_e(view.get("note", ""))}</p>' + table + eo_html + "</section>"
    )


# ============================================================ D. split-script


def _split_script_section(series: Any) -> str:
    from ..core.series import SeriesError, split_script

    script_dir = series.script_dir
    files = sorted(script_dir.glob("*.md")) if script_dir.exists() else []
    if not files:
        return (
            '<section class="panel sr-split"><h2>长稿拆分 Split-script</h2>'
            f'<p class="muted">{_e(series.relpath(script_dir))}/ 目录下还没有 .md 长稿文件 —— '
            "放入按 <code># E01 标题</code> / <code>## E01</code> 标记分集的长稿,本页会显示"
            "拆分预演;创建/写入只在命令行执行:"
            '<pre class="sr-cmd">manju series split-script &lt;file&gt; --apply</pre></p>'
            "</section>"
        )

    blocks: list[str] = []
    for f in files:
        try:
            report = split_script(series, f, apply=False)
        except SeriesError as exc:
            blocks.append(
                f'<div class="sr-split-file panel"><h4>{_e(f.name)}</h4>'
                f'<p class="err">{_e(exc)}</p></div>'
            )
            continue
        rows = []
        for e in report["episodes"]:
            state = "已存在 exists" if e["exists"] else "将新建 would-create"
            rows.append(
                f'<tr><td>{_e(e["eid"])}</td><td>{_e(e.get("title"))}</td>'
                f'<td>{_e(state)}</td><td>{_e(e["script_path"])}</td>'
                f'<td>{e["chars"]}</td></tr>'
            )
        cmd = f'manju series split-script "{f}" --apply'
        blocks.append(
            f'<div class="sr-split-file panel"><h4>{_e(f.name)}</h4>'
            '<div class="tablewrap"><table class="sr-table"><thead><tr>'
            "<th>集</th><th>标题</th><th>状态</th><th>脚本路径</th><th>字数</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
            '<div class="sr-split-cmd">'
            f'<code class="sr-cmd">{_e(cmd)}</code>'
            f'<button type="button" class="btn ghost mini" data-act="copy" '
            f'data-copy="{_e(cmd)}">复制</button>'
            '<span class="muted"> — 只读预演;命令行加 --apply 才会创建分集/写入脚本'
            "</span></div></div>"
        )

    return (
        '<section class="panel sr-split"><h2>长稿拆分 Split-script</h2>'
        + "".join(blocks) + "</section>"
    )


# ============================================================ assets (css/js)


def render_series_css() -> str:
    return _SERIES_CSS


def render_series_js() -> str:
    return _SERIES_JS


_SERIES_CSS = """
/* 剧集工作台 /series (round X, agent XD). Loaded AFTER /app.css + /pages.css;
   reuses their :root palette + .panel/.btn/.badge/.muted/.tablewrap. */

.sr-table { width: 100%; border-collapse: collapse; font-size: .86rem; }
.sr-table th, .sr-table td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid var(--line); vertical-align: top; }
.sr-table th { color: var(--muted); }
.sr-chips { display: flex; flex-wrap: wrap; gap: .3rem; }
.sr-open { display: flex; flex-direction: column; gap: .25rem; align-items: flex-start; }
.sr-path { font-family: var(--mono); font-size: .76rem; word-break: break-all; }
.sr-cmd {
  font-family: var(--mono); font-size: .8rem; background: var(--panel2);
  border: 1px solid var(--line); border-radius: 6px; padding: .12rem .5rem;
  display: inline-block;
}
.sr-newep, .sr-sync-apply { margin-top: .8rem; }
.sr-newep-row { display: flex; gap: .8rem; flex-wrap: wrap; align-items: flex-end; }
.sr-field { display: flex; flex-direction: column; gap: .2rem; font-size: .82rem; color: var(--muted); }
.sr-field input {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .3rem .5rem; font: inherit; min-width: 160px;
}
.sr-sync-apply { display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; }
.sr-hint { cursor: help; border-bottom: 1px dotted var(--muted); }
.sr-sync-ep { margin: .8rem 0; }
.sr-sync-group { margin: .5rem 0; }
.sr-sync-group h5 { margin: 0 0 .3rem; font-size: .86rem; }
.sr-diff { margin: .5rem 0; padding: .5rem; background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; }
.sr-diff-head { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin-bottom: .4rem; }
.sr-diff-cols { display: grid; grid-template-columns: 1fr 1fr; gap: .6rem; }
.sr-diff-col pre {
  margin: .2rem 0 0; padding: .4rem .55rem; background: var(--panel); border: 1px solid var(--line);
  border-radius: 6px; font-size: .78rem; overflow-x: auto; white-space: pre-wrap; word-break: break-word;
}
@media (max-width: 720px) { .sr-diff-cols { grid-template-columns: 1fr; } }
.sr-eo { margin-top: .6rem; font-size: .84rem; }
.sr-split-file { margin: .8rem 0; }
.sr-split-cmd { display: flex; align-items: center; gap: .5rem; flex-wrap: wrap; margin-top: .5rem; }
.sr-continuity h4 { margin: .9rem 0 .4rem; font-size: .92rem; }
.sr-ct-outliers { margin-top: .8rem; font-size: .86rem; }
.sr-ct-outliers ul { margin: .3rem 0 0; padding-left: 1.2rem; }
.sr-ct-outliers li { margin: .15rem 0; }
"""


_SERIES_JS = r"""
"use strict";
(function () {
  if (document.body.getAttribute("data-page") !== "/series") return;


  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }

  // round AA4: 新建集 / sync-bible apply now run on the jobs runner
  // (Project.create scaffolding / per-episode bible writes are genuinely
  // multi-second) — same submit+poll shape every other page uses.
  // Adaptive cadence: 100ms while a quick local job usually lands (~2s), then
  // 500ms — the runner is SERIALIZED, so a job queued behind a long build
  // legitimately takes minutes; the old ~60s cap misreported it as a failure.
  // ~10min cap; a transient fetch error retries, never rejects.
  function pollJob(jobId, tries) {
    tries = tries || 0;
    var opts = (typeof manjuApiOptions === "function") ? manjuApiOptions() : {};
    var p = (typeof requestJson === "function")
      ? requestJson("GET", "/api/jobs", undefined, opts)
      : fetch("/api/jobs").then(function (r) { return r.json(); });
    return p.then(function (d) {
      var job = ((d && d.jobs) || []).filter(function (j) { return j.id === jobId; })[0];
      if (job && (job.state === "done" || job.state === "failed" || job.state === "canceled" || job.state === "interrupted")) return job;
      return job || null;
    }).catch(function () { return null; }).then(function (job) {
      if (job && (job.state === "done" || job.state === "failed" || job.state === "canceled" || job.state === "interrupted")) return job;
      if (tries > 1215) return null;  /* alive at the cap = still running, never "failed" */  // 20×100ms + ~1195×500ms ≈ 10min
      return new Promise(function (res) {
        setTimeout(res, tries < 20 ? 100 : 500);
      }).then(function () { return pollJob(jobId, tries + 1); });
    });
  }

  function doCopy(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { toast("已复制", true); },
        function () { toast("复制失败,请手动选择", false); }
      );
    } else {
      toast("请手动复制:" + text, true);
    }
  }

  function doNewEpisode() {
    var eidEl = document.getElementById("sr-new-eid");
    var titleEl = document.getElementById("sr-new-title");
    var out = document.getElementById("sr-new-result");
    var btn = document.getElementById("sr-new-btn");
    var eid = (eidEl.value || "").trim();
    var title = (titleEl.value || "").trim();
    if (!eid) { toast("请填写集号 eid", false); return; }
    if (btn) btn.disabled = true;
    if (out) out.textContent = "新建中…";
    post("/api/series/new-episode", { eid: eid, title: title }).then(function (res) {
      if (res.status !== 202 || !res.data.job) {
        if (btn) btn.disabled = false;
        if (out) out.textContent = (res.data && res.data.error) || "失败";
        toast((res.data && res.data.error) || "失败", false);
        return;
      }
      pollJob(res.data.job.id).then(function (job) {
        if (btn) btn.disabled = false;
        if (!job) {
          if (out) out.textContent = "仍在排队/运行(超时)— 刷新本页查看";
          toast("新建集任务仍在排队/运行(轮询超时)— 完成后刷新本页可见", false);
          return;
        }
        if (job.state === "done") {
          var result = job.result || {};
          toast("已新建 " + eid, true);
          if (out) out.textContent = "已新建 → " + (result.dir || eid);
          reloadSoon();
        } else {
          if (out) out.textContent = job.error || "失败";
          toast(job.error || "失败", false);
        }
      });
    });
  }

  function doSyncApply() {
    var out = document.getElementById("sr-sync-result");
    var btn = document.getElementById("sr-sync-apply-btn");
    if (btn) btn.disabled = true;
    if (out) out.textContent = "同步中…";
    post("/api/series/sync-bible/apply", {}).then(function (res) {
      if (res.status !== 202 || !res.data.job) {
        if (btn) btn.disabled = false;
        if (out) out.textContent = (res.data && res.data.error) || "失败";
        toast((res.data && res.data.error) || "失败", false);
        return;
      }
      pollJob(res.data.job.id).then(function (job) {
        if (btn) btn.disabled = false;
        if (!job) {
          if (out) out.textContent = "仍在排队/运行(超时)— 刷新本页查看";
          toast("同步任务仍在排队/运行(轮询超时)— 完成后刷新本页可见", false);
          return;
        }
        if (job.state === "done") {
          var result = job.result || {};
          var t = result.totals || {};
          if (result.canceled) {
            if (out) out.textContent = (result.errors && result.errors[0]) || "同步已取消";
            toast((result.errors && result.errors[0]) || "同步已取消", false);
          } else {
            if (out) out.textContent = "已应用:新增 " + (t.added || 0) + " 条(分歧条目未改动)";
            toast("同步已应用", true);
          }
          reloadSoon();
        } else {
          if (out) out.textContent = job.error || "失败";
          toast(job.error || "失败", false);
        }
      });
    });
  }

  document.addEventListener("click", function (ev) {
    var copyBtn = ev.target.closest('[data-act="copy"]');
    if (copyBtn) { doCopy(copyBtn.getAttribute("data-copy")); return; }
    if (ev.target.id === "sr-new-btn") { doNewEpisode(); return; }
    if (ev.target.id === "sr-sync-apply-btn") { doSyncApply(); return; }
  });
})();
"""
