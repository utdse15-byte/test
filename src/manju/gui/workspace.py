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
from .a11y import HTML_LANG, NOSCRIPT_HTML, SKIP_LINK_HTML, main_open

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
        return '<span class="ws-chip ws-chip-warn">路径失效</span>'
    parts = ['<span class="ws-chip">可打开</span>']
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
        + (" · 当前项目" if item["current"] else "") + "</span>"
        f'<span class="ws-row-path">{_e(item["path"])}</span>'
        "</span>"
        f'<span class="ws-row-chips">{_status_chips(item["status"])}</span>'
        "</button>"
    )


def _recents_section(payload: dict[str, Any]) -> str:
    items = payload["recents"]
    out = ['<section class="ws-section"><h2>最近项目</h2>']
    if payload["dropped"]:
        out.append(
            '<p class="ws-note">'
            + _e(f"已从列表移除 {len(payload['dropped'])} 个已经失效的路径")
            + "</p>"
        )
    if not items:
        out.append('<p class="muted">还没有最近项目。创建或打开一次后，会出现在这里。</p>')
        out.append("</section>")
        return "".join(out)
    for series, members in _group_by_series(items):
        if series is None:
            for it in members:
                out.append(_row_html(it))
        else:
            out.append(
                f'<fieldset class="ws-series"><legend>剧集：{_e(series["name"])}'
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
    from .command_palette import render_command_button
    from .help_center import render_help_button

    payload = recents_payload(bound.root if bound is not None else None)
    presets = presets or []

    back = ""
    intro = (
        "<p>选择最近项目，或从磁盘打开、创建一个项目。</p>"
    )
    if bound is not None:
        try:
            bound_name = bound.load_config().name
        except Exception:
            bound_name = bound.root.name
        intro = (
            '<section class="ws-current" aria-label="当前项目">'
            '<span class="ws-current-label">当前项目</span>'
            f'<strong>{_e(bound_name)}</strong>'
            f'<span class="ws-current-path">{_e(bound.root)}</span>'
            '<span class="ws-current-note">打开或新建另一个项目后，工作台会安全切换过去。</span>'
            '</section>'
        )
        back = '<p class="ws-back"><a href="/">← 返回当前项目</a></p>'

    preset_options = ['<option value="">不使用预设</option>']
    for p in presets:
        preset_options.append(
            f'<option value="{_e(p["name"])}">{_e(p["title"])} ({_e(p["aspect"])})</option>'
        )

    body = (
        main_open("workspace-page")
        + '<header class="ws-brand"><span class="ws-brand-mark" aria-hidden="true">M</span>'
        '<span class="ws-brand-copy"><strong>Manju</strong><small>本地电影工作台 · 项目工作区</small></span>'
        '<span class="ws-brand-actions">'
        + render_command_button(title="搜索项目或页面（Ctrl+K）")
        + render_help_button(compact=True)
        + '</span></header>'
        '<h1>打开或新建项目</h1>'
        '<p class="ws-lede">选择项目后，Manju 会恢复上次的页面和工作位置。</p>'
        + intro + back
        + _recents_section(payload)
        + '<div class="ws-action-grid"><section class="ws-section"><h2>打开现有项目</h2>'
        '<form id="ws-open-form" class="ws-form">'
        '<label class="ctl">项目文件夹'
        '<input type="text" id="ws-open-path" name="path" '
        'placeholder="例如 D:\\Films\\my_film.manju" required></label>'
        '<button type="submit" class="btn">打开项目</button>'
        "</form></section>"
        '<section class="ws-section"><h2>新建项目</h2>'
        '<form id="ws-new-form" class="ws-form">'
        '<label class="ctl">项目名称'
        '<input type="text" id="ws-new-name" name="name" maxlength="80" '
        'placeholder="例如 雨夜末班车" required></label>'
        '<label class="ctl">画幅'
        '<select id="ws-new-orient" name="orient">'
        '<option value="vertical">竖屏 9:16</option>'
        '<option value="horizontal">横屏 16:9</option>'
        "</select></label>"
        '<label class="ctl">预设'
        f'<select id="ws-new-preset" name="preset">{"".join(preset_options)}</select></label>'
        '<label class="ctl">保存位置（可选）'
        '<input type="text" id="ws-new-path" name="path" '
        'placeholder="留空则使用当前目录"></label>'
        '<button type="submit" class="btn">创建项目</button>'
        "</form></section></div>"
        '<section id="ws-feedback" class="ws-feedback" role="alert" aria-live="assertive" tabindex="-1" hidden><div class="ws-feedback-mark" aria-hidden="true">!</div><div><h2 id="ws-feedback-title">操作没有完成</h2><p id="ws-feedback-copy"></p><p class="ws-feedback-protect">项目没有被修改，刚才填写的内容仍然保留。</p><details id="ws-feedback-tech" hidden><summary>技术详情</summary><pre id="ws-feedback-detail"></pre></details></div></section>'
        "</main>"
    )

    return (
        "<!doctype html>\n"
        f'<html lang="{HTML_LANG}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="theme-color" content="#11151c">\n'
        '<link rel="icon" href="/favicon.ico" type="image/svg+xml">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        "<title>打开或新建项目 · Manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/workspace.css">\n'
        '<link rel="stylesheet" href="/project-action.css">\n'
        '<link rel="stylesheet" href="/command-palette.css">\n'
        '<link rel="stylesheet" href="/help-center.css">\n'
        '<script src="/webclient.js" defer></script>\n'
        '<script src="/project-action.js" defer></script>\n'
        '<script src="/command-palette.js" defer></script>\n'
        '<script src="/help-center.js" defer></script>\n'
        '<script src="/workspace.js" defer></script>\n'
        "</head>\n"
        '<body data-page="/workspace">\n'
        + SKIP_LINK_HTML + body
        + "\n" + NOSCRIPT_HTML + "\n"
        + "</body>\n</html>\n"
    )


def render_workspace_css() -> str:
    return _WORKSPACE_CSS


def render_workspace_js() -> str:
    return _WORKSPACE_JS


_WORKSPACE_CSS = """
/* manju gui — workspace picker (round X agent XE) */
.workspace-page { max-width: 960px; margin: 0 auto; padding: 1.2rem 1.4rem 3rem; }
.ws-brand { display: flex; align-items: center; gap: .65rem; margin: .1rem 0 1.5rem; }
.ws-brand-mark {
  display: inline-grid; place-items: center; width: 2.35rem; height: 2.35rem;
  border-radius: 10px; background: var(--accent); color: #0b1220;
  font-weight: 900; box-shadow: inset 0 0 0 1px rgba(255,255,255,.24);
}
.ws-brand-copy { display: flex; flex-direction: column; line-height: 1.2; min-width: 0; }
.ws-brand-copy strong { font-size: 1.05rem; }
.ws-brand-copy small { color: var(--muted); margin-top: .18rem; }
.ws-brand-actions { margin-left: auto; display: inline-flex; align-items: center; gap: .4rem; }
.workspace-page h1 { font-size: 1.55rem; margin: .2rem 0 .25rem; }
.ws-lede { color: var(--muted); margin: 0 0 1.2rem; max-width: 44rem; }
.ws-current {
  display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .2rem .65rem;
  align-items: baseline; margin: .9rem 0 .4rem; padding: .72rem .85rem;
  max-width: 46rem; border: 1px solid var(--line); border-radius: 9px;
  background: color-mix(in srgb, var(--panel) 78%, transparent);
}
.ws-current-label { color: var(--muted); font-size: .75rem; grid-column: 1 / -1; }
.ws-current strong { min-width: 0; }
.ws-current-path {
  min-width: 0; color: var(--muted); font-size: .75rem; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.ws-current-note { grid-column: 1 / -1; color: var(--muted); font-size: .78rem; }
.ws-back { margin: .5rem 0 1rem; }
.ws-section { margin: 1.4rem 0; padding: 1rem; background: var(--panel);
  border: 1px solid var(--line); border-radius: 10px; box-shadow: var(--shadow-1); }
.ws-action-grid { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr); gap: 1rem; }
.ws-action-grid .ws-section { margin-top: 0; }
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
.ws-form .ctl { display: flex; flex-direction: column; align-items: stretch;
  gap: .25rem; font-size: .85rem; color: var(--muted); }
.ws-form input, .ws-form select { width: 100%; box-sizing: border-box;
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .5rem .6rem; font: inherit; }
.ws-feedback {
  display: grid; grid-template-columns: 2rem minmax(0, 1fr); gap: .75rem;
  margin: 1.1rem 0 0; padding: .85rem 1rem; border: 1px solid #8a4247;
  border-radius: 10px; background: #30191c; color: var(--fg);
}
.ws-feedback-mark {
  display: grid; place-items: center; width: 1.75rem; height: 1.75rem;
  border-radius: 999px; background: #57272c; color: #ffd7da; font-weight: 850;
}
.ws-feedback h2 { margin: 0 0 .25rem; border: 0; padding: 0; color: #ffd7da; }
.ws-feedback p { margin: .2rem 0; }
.ws-feedback-protect { color: #c8cbd2; font-size: .82rem; }
.ws-feedback details { margin-top: .45rem; }
.ws-feedback summary { color: #ffc2c6; cursor: pointer; font-size: .78rem; }
.ws-feedback pre {
  margin: .45rem 0 0; padding: .55rem .65rem; max-height: 12rem; overflow: auto;
  border-radius: 7px; background: #15171c; color: #d7d9de; white-space: pre-wrap;
  overflow-wrap: anywhere; font-size: .75rem;
}
@media (max-width: 620px) {
  .workspace-page { padding: .85rem .65rem 2rem; }
  .ws-brand { flex-wrap: wrap; }
  .ws-brand-actions { margin-left: auto; }
  .ws-section { margin: .85rem 0; padding: .8rem; }
  .ws-action-grid { grid-template-columns: 1fr; gap: 0; }
  .ws-row { align-items: flex-start; flex-direction: column; }
  .ws-row-chips { justify-content: flex-start; }
  .ws-row-path { max-width: 100%; white-space: normal; overflow-wrap: anywhere; }
  .ws-current { grid-template-columns: 1fr; }
  .ws-current-label, .ws-current-note { grid-column: auto; }
  .ws-current-path { white-space: normal; overflow-wrap: anywhere; }
  .ws-form { max-width: none; }
}
"""

_WORKSPACE_JS = r"""
"use strict";
/* manju gui — workspace picker actions. Depends on /webclient.js +
 * /project-action.js. Only reload_current navigates to `/`. */
(function () {
  var meta = document.querySelector('meta[name="manju-token"]');
  var TOKEN = meta ? (meta.getAttribute("content") || "") : "";
  var feedback = document.getElementById("ws-feedback");
  var feedbackTitle = document.getElementById("ws-feedback-title");
  var feedbackCopy = document.getElementById("ws-feedback-copy");
  var feedbackTech = document.getElementById("ws-feedback-tech");
  var feedbackDetail = document.getElementById("ws-feedback-detail");

  function clearFeedback() {
    if (!feedback) return;
    feedback.hidden = true;
    if (feedbackCopy) feedbackCopy.textContent = "";
    if (feedbackDetail) feedbackDetail.textContent = "";
    if (feedbackTech) feedbackTech.hidden = true;
  }

  function errorText(err) {
    if (err && err.data && err.data.error) return String(err.data.error);
    if (err && err.message) return String(err.message);
    return err ? String(err) : "未知错误";
  }

  function showError(title, err, next) {
    if (!feedback) return;
    feedback.hidden = false;
    if (feedbackTitle) feedbackTitle.textContent = title || "操作没有完成";
    if (feedbackCopy) feedbackCopy.textContent = next || "请检查项目路径后重试。";
    var detail = errorText(err);
    if (feedbackDetail) feedbackDetail.textContent = detail;
    if (feedbackTech) feedbackTech.hidden = !detail;
    feedback.scrollIntoView({ block: "nearest" });
    try { feedback.focus({ preventScroll: true }); }
    catch (focusError) { feedback.focus(); }
  }

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
    clearFeedback();
    post("/api/workspace/open", { path: path })
      .then(onAction)
      .catch(function (err) {
        if (err && err.data && err.data.next_action) {
          onAction(err.data);
          return;
        }
        showError("无法打开这个项目", err,
          "请确认这是一个可访问的 .manju 项目文件夹，然后重试。");
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
      clearFeedback();
      var name = (document.getElementById("ws-new-name") || {}).value || "";
      if (!name.trim()) {
        showError("还缺少项目名称", null, "填写一个名称后即可创建项目。");
        var nameInput = document.getElementById("ws-new-name");
        if (nameInput) nameInput.focus();
        return;
      }
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
          showError("项目没有创建", err,
            "请检查名称和保存位置；现有项目与刚才填写的内容都没有被修改。");
        });
    });
  }
})();
"""
