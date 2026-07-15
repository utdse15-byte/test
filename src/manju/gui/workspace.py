"""The WORKSPACE PICKER (round X agent XE, user pain #6 + the recents half of
#8): what ``manju gui`` serves when it is NOT bound to a project.

Design decision (rebind vs. print-a-command): :class:`~manju.gui.server.GuiServer`
already carries an in-process ``workspace: dict[str, Project]`` + an active
``project`` that ``switch_project`` swaps — a live single-process, single-active-
project server was ALREADY the shape of this codebase (the ``--workspace <dir>``
scan mode). Extending that same server to start with ``project=None`` and REBIND
it once a project is opened (:meth:`GuiServer.bind_project`) reuses that shape
exactly and needed no new process/session model — the honest "print the command
instead" fallback the round brief allows for was NOT needed here. The trade-off
this accepts: a picker-mode server serves exactly ONE browser session's notion
of "current project" at a time (same single-actor assumption every other GUI
surface already makes, §1-⑦) — opening a different project retargets the whole
server, not just one tab.

This module owns the picker PAGE (recents + 按路径打开 + 新建项目, server-rendered,
same CSP-safe/XSS-safe/token-gated discipline as :mod:`manju.gui.pages`) and the
data assembly (:func:`recents_payload`) the picker page AND the in-chrome
project-switcher dropdown (:mod:`manju.gui.glossary`'s shared chrome script) both
read via ``GET /api/workspace/recents``. :mod:`manju.gui.server` wires the routes
(``_workspace_get`` / ``_workspace_post``) and owns the actual rebind/create
mutations, mirroring exactly how ``_act_switch``/``_act_new_project`` already work
for the ``--workspace`` scan mode.

Status chips are DELIBERATELY cheap (round brief: "exists, shot count, newest
final mtime — degrade per-row") — never the full ``build.status.project_status``
walk (which evaluates every shot's staleness against its spec hash and is not
something you want to pay N times for a recents list of up to 20 projects).
Each field degrades independently so one unreadable project never blanks a row.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from ..core.container import PROJECT_FILE, Project, ProjectError
from ..core.recents import load_recents
from ..core.series import Series, SeriesError

__all__ = [
    "recents_payload",
    "render_picker_page",
    "render_workspace_css",
    "render_workspace_js",
]


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


# --------------------------------------------------------------------- data


def _cheap_status(path: Path) -> dict[str, Any]:
    """exists / shots / newest_final_mtime — each field degrades on its own;
    a project this fails to even construct just reports ``exists``."""
    out: dict[str, Any] = {"exists": False, "shots": None, "newest_final_mtime": None}
    if not (path / PROJECT_FILE).exists():
        return out
    out["exists"] = True
    try:
        project = Project(path)
    except ProjectError:
        return out
    try:
        out["shots"] = len(project.shot_ids())
    except Exception:
        pass
    try:
        final = project.newest_final_path()
        out["newest_final_mtime"] = final.stat().st_mtime if final else None
    except Exception:
        pass
    return out


def _series_of(path: Path) -> dict[str, str] | None:
    """Best-effort: is ``path`` a series episode (``<series>/episodes/<eid>.manju``)?
    Used to group episodes of the same series in the recents list/switcher."""
    if path.parent.name != "episodes":
        return None
    try:
        series = Series(path.parent.parent)
        return {"root": str(series.root), "name": series.load_config().name}
    except (SeriesError, OSError):
        return None


def recents_payload(current_root: Path | None) -> dict[str, Any]:
    """The shape both the picker page and ``GET /api/workspace/recents`` render
    from: recents (most-recent-first) with a cheap per-row status chip and
    series-grouping hint, plus any entries dropped this read (report-once)."""
    result = load_recents()
    items: list[dict[str, Any]] = []
    for e in result.entries:
        p = Path(e["path"])
        items.append({
            "path": e["path"],
            "name": e.get("name") or p.name,
            "last_opened": e.get("last_opened"),
            "pinned": bool(e.get("pinned")),
            "current": current_root is not None and p == current_root,
            "status": _cheap_status(p),
            "series": _series_of(p),
        })
    return {
        "recents": items,
        "dropped": result.dropped,
        "current": str(current_root) if current_root is not None else None,
    }


def _group_by_series(items: list[dict[str, Any]]) -> list[tuple[dict[str, str] | None, list[dict[str, Any]]]]:
    """Stable group-by: series-grouped runs stay where their FIRST member would
    have sorted (recency order is never reshuffled across groups), ungrouped
    entries pass through as singleton groups of their own."""
    groups: list[tuple[dict[str, str] | None, list[dict[str, Any]]]] = []
    index: dict[str, int] = {}
    for it in items:
        series = it.get("series")
        if series is None:
            groups.append((None, [it]))
            continue
        key = series["root"]
        if key in index:
            groups[index[key]][1].append(it)
        else:
            index[key] = len(groups)
            groups.append((series, [it]))
    return groups


# ------------------------------------------------------------------- render


def _status_chips(status: dict[str, Any]) -> str:
    if not status.get("exists"):
        return '<span class="ws-chip ws-chip-warn">路径不存在 (missing)</span>'
    parts = ['<span class="ws-chip">存在 (ok)</span>']
    shots = status.get("shots")
    if shots is not None:
        parts.append(f'<span class="ws-chip">{shots} 镜头</span>')
    mtime = status.get("newest_final_mtime")
    if mtime:
        import datetime as _dt

        stamp = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        parts.append(f'<span class="ws-chip">成片 {_e(stamp)}</span>')
    return "".join(parts)


def _row_html(item: dict[str, Any]) -> str:
    cur = " ws-row-current" if item["current"] else ""
    disabled = " disabled" if item["current"] else ""
    return (
        f'<button type="button" class="ws-row{cur}" data-path="{_e(item["path"])}"{disabled}>'
        f'<span class="ws-row-main">'
        f'<span class="ws-row-name">{_e(item["name"])}'
        + (" · 当前 (current)" if item["current"] else "") + "</span>"
        f'<span class="ws-row-path">{_e(item["path"])}</span>'
        "</span>"
        f'<span class="ws-row-chips">{_status_chips(item["status"])}</span>'
        "</button>"
    )


def _recents_section(payload: dict[str, Any]) -> str:
    items = payload["recents"]
    out = ['<section class="ws-section"><h2>最近项目 (recent projects)</h2>']
    if payload["dropped"]:
        out.append(
            '<p class="ws-note">'
            + _e(f"已从列表移除 {len(payload['dropped'])} 个不存在的路径 (missing, removed)")
            + "</p>"
        )
    if not items:
        out.append('<p class="muted">还没有最近项目 (no recent projects yet)。</p>')
        out.append("</section>")
        return "".join(out)
    for series, members in _group_by_series(items):
        if series is None:
            for it in members:
                out.append(_row_html(it))
        else:
            out.append(
                f'<fieldset class="ws-series"><legend>剧集 (series): {_e(series["name"])}'
                "</legend>"
            )
            for it in members:
                out.append(_row_html(it))
            out.append("</fieldset>")
    out.append("</section>")
    return "".join(out)


def render_picker_page(token: str, *, bound: "Project | None" = None, presets: list[dict[str, Any]] | None = None) -> str:
    """The full workspace-picker HTML document.

    ``bound`` is the CURRENTLY bound project when this is reached via the
    in-chrome switcher's "管理工作区" link (``/?workspace=1``) rather than the
    outside-a-project fallback — the page then also offers a way back."""
    payload = recents_payload(bound.root if bound is not None else None)
    presets = presets or []

    back = ""
    intro = (
        "<p>当前不在任何项目目录下 (not inside a project)。"
        "从下面选择最近项目、按路径打开,或新建一个。</p>"
    )
    if bound is not None:
        try:
            bound_name = bound.load_config().name
        except Exception:
            bound_name = bound.root.name
        intro = (
            f"<p>当前项目 (current project):<b>{_e(bound_name)}</b> "
            f'<span class="ws-row-path">{_e(bound.root)}</span>'
            "。在下面打开或新建另一个项目会切换到它 (opening one switches this "
            "server to it)。</p>"
        )
        back = '<p><a href="/">← 返回当前项目 (back to current project)</a></p>'

    preset_options = ['<option value="">无 / generic (none)</option>']
    for p in presets:
        preset_options.append(
            f'<option value="{_e(p["name"])}">{_e(p["title"])} ({_e(p["aspect"])})</option>'
        )

    body = (
        '<div class="ws-wrap">'
        "<h1>Manju 工作区 (workspace)</h1>"
        + intro + back
        + _recents_section(payload)
        + '<section class="ws-section"><h2>按路径打开 (open by path)</h2>'
        '<form id="ws-open-form" class="ws-form">'
        '<label class="ctl">项目路径 (path)'
        '<input type="text" id="ws-open-path" name="path" '
        'placeholder="/path/to/my_film.manju" required></label>'
        '<button type="submit" class="btn">打开 (Open)</button>'
        "</form></section>"
        '<section class="ws-section"><h2>新建项目 (new project)</h2>'
        '<form id="ws-new-form" class="ws-form">'
        '<label class="ctl">名称 (name)'
        '<input type="text" id="ws-new-name" name="name" maxlength="80" '
        'placeholder="my_film" required></label>'
        '<label class="ctl">画幅 (orientation)'
        '<select id="ws-new-orient" name="orient">'
        '<option value="vertical">竖屏 9:16 (vertical)</option>'
        '<option value="horizontal">横屏 16:9 (horizontal)</option>'
        "</select></label>"
        '<label class="ctl">预设 (preset)'
        f'<select id="ws-new-preset" name="preset">{"".join(preset_options)}</select></label>'
        '<label class="ctl">所在目录 (parent dir, optional)'
        '<input type="text" id="ws-new-path" name="path" '
        'placeholder="留空则用当前工作目录 (default: cwd)"></label>'
        '<button type="submit" class="btn">创建 (Create)</button>'
        "</form></section>"
        '<div id="ws-err" class="ws-err" role="alert"></div>'
        "</div>"
    )

    return (
        "<!doctype html>\n"
        '<html lang="zh">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        "<title>Manju 工作区 · workspace</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/workspace.css">\n'
        '<link rel="stylesheet" href="/project-action.css">\n'
        '<script src="/webclient.js" defer></script>\n'
        '<script src="/project-action.js" defer></script>\n'
        '<script src="/workspace.js" defer></script>\n'
        "</head>\n"
        '<body data-page="/workspace">\n'
        + body
        + "\n<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


def render_workspace_css() -> str:
    return _WORKSPACE_CSS


def render_workspace_js() -> str:
    return _WORKSPACE_JS


_WORKSPACE_CSS = """
/* manju gui — workspace picker (round X agent XE) */
.ws-wrap { max-width: 860px; margin: 0 auto; padding: 1.2rem 1.4rem 3rem; }
.ws-wrap h1 { font-size: 1.3rem; margin: .2rem 0 .8rem; }
.ws-section { margin: 1.4rem 0; padding: 1rem; background: var(--panel);
  border: 1px solid var(--line); border-radius: 10px; }
.ws-section h2 { font-size: 1rem; margin: 0 0 .7rem; color: var(--muted); }
.ws-note { color: var(--muted); font-size: .85rem; margin: 0 0 .6rem; }
.ws-series { border: 1px solid var(--line); border-radius: 8px; margin: 0 0 .6rem;
  padding: .5rem .7rem .2rem; }
.ws-series legend { color: var(--muted); font-size: .82rem; padding: 0 .3rem; }
.ws-row { display: flex; align-items: center; justify-content: space-between;
  gap: .8rem; width: 100%; text-align: left; background: var(--panel2);
  border: 1px solid var(--line); border-radius: 8px; padding: .5rem .7rem;
  margin: 0 0 .4rem; font: inherit; color: var(--fg); cursor: pointer; }
.ws-row:hover:not(:disabled) { border-color: var(--accent); }
.ws-row:disabled { opacity: .6; cursor: default; }
.ws-row-main { display: flex; flex-direction: column; gap: .15rem; min-width: 0; }
.ws-row-name { font-weight: 600; }
.ws-row-path { color: var(--muted); font-size: .76rem; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; max-width: 46ch; }
.ws-row-chips { display: flex; gap: .35rem; flex-wrap: wrap; justify-content: flex-end; }
.ws-chip { background: var(--panel); border: 1px solid var(--line); border-radius: 999px;
  padding: .1rem .5rem; font-size: .72rem; color: var(--muted); white-space: nowrap; }
.ws-chip-warn { color: #ffb4b4; border-color: #6a2f2f; }
.ws-form { display: flex; flex-direction: column; gap: .6rem; max-width: 420px; }
.ws-form .ctl { display: flex; flex-direction: column; gap: .25rem; font-size: .85rem;
  color: var(--muted); }
.ws-form input, .ws-form select { background: var(--panel2); color: var(--fg);
  border: 1px solid var(--line); border-radius: 6px; padding: .35rem .5rem; font: inherit; }
.ws-err { color: #ffb4b4; font-size: .85rem; min-height: 1.2em; margin-top: .6rem; }
"""

_WORKSPACE_JS = r"""
"use strict";
/* manju gui — workspace picker actions. Depends on /webclient.js +
 * /project-action.js. Only reload_current navigates to `/`. */
(function () {
  var meta = document.querySelector('meta[name="manju-token"]');
  var TOKEN = meta ? (meta.getAttribute("content") || "") : "";
  var errBox = document.getElementById("ws-err");

  function showErr(msg) { if (errBox) errBox.textContent = msg || ""; }

  function apiOpts() {
    return (typeof manjuApiOptions === "function")
      ? manjuApiOptions({ token: TOKEN })
      : { token: TOKEN };
  }

  function post(url, body) {
    if (typeof requestJson === "function") {
      return requestJson("POST", url, body || {}, apiOpts());
    }
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Manju-Token": TOKEN },
      body: JSON.stringify(body || {})
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) {
          var e = new Error((data && data.error) || ("HTTP " + resp.status));
          e.data = data; e.status = resp.status; e.code = data && data.code;
          throw e;
        }
        return data;
      });
    });
  }

  function onAction(data) {
    if (typeof handleProjectAction === "function") {
      handleProjectAction(data, {
        onReload: function () { window.location.href = "/"; }
      });
      return;
    }
    var next = (data && data.next_action) || {};
    if (next.kind === "reload_current") {
      window.location.href = "/";
    }
  }

  function openPath(path) {
    showErr("");
    post("/api/workspace/open", { path: path })
      .then(onAction)
      .catch(function (err) {
        if (err && err.data && err.data.next_action) {
          onAction(err.data);
          return;
        }
        showErr("打开失败 (open failed): " + ((err && err.message) || String(err)));
      });
  }

  document.querySelectorAll(".ws-row[data-path]").forEach(function (btn) {
    if (btn.disabled) return;
    btn.addEventListener("click", function () { openPath(btn.getAttribute("data-path")); });
  });

  var openForm = document.getElementById("ws-open-form");
  if (openForm) {
    openForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var p = (document.getElementById("ws-open-path") || {}).value || "";
      if (p.trim()) openPath(p.trim());
    });
  }

  var newForm = document.getElementById("ws-new-form");
  if (newForm) {
    newForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      showErr("");
      var name = (document.getElementById("ws-new-name") || {}).value || "";
      if (!name.trim()) { showErr("请输入名称 (name required)"); return; }
      var body = {
        name: name.trim(),
        vertical: (document.getElementById("ws-new-orient") || {}).value !== "horizontal"
      };
      var preset = (document.getElementById("ws-new-preset") || {}).value || "";
      if (preset) body.preset = preset;
      var parent = (document.getElementById("ws-new-path") || {}).value || "";
      if (parent.trim()) body.path = parent.trim();
      post("/api/workspace/new", body)
        .then(onAction)
        .catch(function (err) {
          if (err && err.data && err.data.next_action) {
            onAction(err.data);
            return;
          }
          showErr("创建失败 (create failed): " + ((err && err.message) || String(err)));
        });
    });
  }
})();
"""
