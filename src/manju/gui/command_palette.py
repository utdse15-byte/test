"""Global, read-only command palette for the local Manju workbench.

The palette is a presentation/discovery layer over existing owners:

* page destinations come from :func:`manju.gui.pages.navigation_items`;
* shot ids and bounded authored summaries come from canonical shot files;
* recent projects come from :mod:`manju.core.recents`;
* opening the task center and workspace reuses their existing UI/actions.

It never executes a build, Provider request, selection, review, approval or
Picture Lock action.  It stores no project state, performs no media probes and
never resolves Provider credentials.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = [
    "palette_payload",
    "render_command_button",
    "render_command_palette_css",
    "render_command_palette_js",
]


def _project_name(project: Any) -> str:
    if project is None:
        return ""
    try:
        return str(project.load_config().name or project.root.name)
    except Exception:
        try:
            return str(project.root.name)
        except Exception:
            return ""


def _compact_text(value: Any, *, limit: int = 120) -> str:
    """Return one bounded, whitespace-normalised search/display string."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _shot_index_row(project: Any, shot_id: str) -> dict[str, Any]:
    """Build one cheap, secret-free shot discovery row.

    The command palette benefits far more from searching ``推开卷帘门`` than
    from knowing only ``S003``.  Reading the already-authored shot YAML is still
    a local deterministic lookup (roughly ten milliseconds for 300 shots in the
    Wave 13 benchmark); it performs no media probe, timeline compilation,
    Provider resolution or credential access.  A malformed shot stays
    discoverable by id and is marked unavailable instead of breaking the whole
    palette.
    """
    row: dict[str, Any] = {
        "id": str(shot_id),
        "scene": "",
        "action": "",
        "dialogue": "",
        "characters": [],
        "available": True,
    }
    try:
        raw = project.load_shot_raw(shot_id)
    except Exception:
        row["available"] = False
        return row

    action = raw.get("action")
    if isinstance(action, dict):
        action = action.get("main") or action.get("description") or ""
    dialogue = raw.get("dialogue")
    if isinstance(dialogue, dict):
        dialogue = dialogue.get("text") or ""
    characters = raw.get("characters")
    if not isinstance(characters, list):
        characters = []

    row.update({
        "scene": _compact_text(raw.get("scene") or raw.get("scene_id")),
        "action": _compact_text(action),
        "dialogue": _compact_text(dialogue, limit=96),
        "characters": [_compact_text(item, limit=48) for item in characters[:8]],
    })
    return row


def render_command_button(*, title: str = "搜索页面、镜头或项目（Ctrl+K）") -> str:
    """Render the single shared command-palette trigger."""
    import html

    return (
        '<button type="button" id="mj-command-btn" class="mj-command-button" '
        'aria-haspopup="dialog" aria-controls="mj-command-palette" '
        'aria-expanded="false" aria-keyshortcuts="Control+K Meta+K" '
        f'title="{html.escape(title, quote=True)}">'
        '<span class="mj-command-icon" aria-hidden="true"></span>'
        '<span class="mj-command-label">搜索</span>'
        '<kbd class="mj-command-kbd" aria-hidden="true">Ctrl K</kbd></button>'
    )


def palette_payload(
    project: Any | None,
    *,
    project_token: str = "",
    mode: str | None = None,
) -> dict[str, Any]:
    """Return a bounded, secret-free discovery index.

    The index reads canonical shot text so a person can search by action, scene
    or dialogue instead of remembering an id.  It still performs no media
    probe, timeline compilation, Provider resolution, credential access or
    write.  The Wave 13 benchmark keeps this lazy path measurable at 12/100/300
    shots.
    """
    from ..core.recents import load_recents
    from .pages import navigation_items
    from .userstate import resolve_mode

    resolved_mode = mode or resolve_mode()
    shots: list[dict[str, Any]] = []
    current_root = ""
    if project is not None:
        try:
            shot_ids = [str(sid) for sid in project.shot_ids()]
        except Exception:
            shot_ids = []
        shots = [_shot_index_row(project, sid) for sid in shot_ids]
        try:
            current_root = str(Path(project.root).resolve())
        except Exception:
            current_root = str(getattr(project, "root", "") or "")

    recents: list[dict[str, Any]] = []
    try:
        entries = load_recents().entries
    except Exception:
        entries = []
    for entry in entries[:12]:
        raw_path = str(entry.get("path") or "").strip()
        if not raw_path:
            continue
        try:
            current = bool(current_root and str(Path(raw_path).resolve()) == current_root)
        except Exception:
            current = bool(current_root and raw_path == current_root)
        recents.append({
            "name": str(entry.get("name") or Path(raw_path).name),
            "path": raw_path,
            "current": current,
            "pinned": bool(entry.get("pinned")),
            "last_opened": entry.get("last_opened"),
        })

    return {
        "version": 2,
        "mode": resolved_mode,
        "project": {
            "bound": project is not None,
            "name": _project_name(project),
            "token": project_token,
            "shot_count": len(shots),
        },
        "navigation": navigation_items(resolved_mode),
        "shots": shots,
        "recents": recents,
    }


COMMAND_PALETTE_CSS = r"""
/* Product Polish R1 Wave 13 — one global discovery surface, no new truth. */
.mj-command-button {
  min-width: 32px; min-height: 32px; display: inline-flex; align-items: center; gap: .35rem;
  padding: .15rem .5rem; border: 1px solid var(--line); border-radius: 8px;
  background: var(--panel2); color: var(--muted); font: inherit; cursor: pointer;
  white-space: nowrap;
}
.mj-command-button:hover, .mj-command-button:focus-visible {
  color: var(--fg); border-color: color-mix(in srgb, var(--accent) 58%, var(--line));
  background: color-mix(in srgb, var(--panel2) 78%, var(--accent-bg));
}
.mj-command-icon, .mj-command-search-mark {
  position: relative; flex: 0 0 auto; width: .9rem; height: .9rem; display: inline-block;
  color: currentColor;
}
.mj-command-icon::before, .mj-command-search-mark::before {
  content: ""; position: absolute; left: .08rem; top: .06rem; width: .52rem; height: .52rem;
  border: 1.5px solid currentColor; border-radius: 50%; box-sizing: border-box;
}
.mj-command-icon::after, .mj-command-search-mark::after {
  content: ""; position: absolute; width: .38rem; height: 1.5px;
  left: .52rem; top: .61rem; border-radius: 999px; background: currentColor;
  transform: rotate(45deg); transform-origin: left center;
}
.mj-command-label { font-size: .76rem; font-weight: 650; }
.mj-command-kbd {
  padding: .05rem .28rem; border: 1px solid var(--line); border-bottom-width: 2px;
  border-radius: 5px; background: var(--panel); color: var(--muted);
  font: 650 .62rem/1.35 var(--mono);
}

.mj-command-dialog {
  width: min(42rem, calc(100vw - 2rem)); max-width: none; margin: 9vh auto auto;
  padding: 0; border: 1px solid color-mix(in srgb, var(--accent) 36%, var(--line));
  border-radius: 14px; background: var(--panel); color: var(--fg);
  box-shadow: 0 26px 80px rgba(0,0,0,.56); overflow: hidden;
}
.mj-command-dialog::backdrop { background: rgba(4,6,10,.72); backdrop-filter: blur(2px); }
.mj-command-shell { min-width: 0; }
.mj-command-head {
  display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: center;
  gap: .55rem; padding: .72rem; border-bottom: 1px solid var(--line);
  background: color-mix(in srgb, var(--panel2) 76%, transparent);
}
.mj-command-input-wrap {
  min-width: 0; display: flex; align-items: center; gap: .5rem;
  padding: .15rem .65rem; border: 1px solid var(--line); border-radius: 10px;
  background: var(--bg);
}
.mj-command-input-wrap:focus-within {
  border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 20%, transparent);
}
.mj-command-search-mark { color: var(--muted); }
.mj-command-input {
  min-width: 0; width: 100%; min-height: 2.45rem; padding: 0; border: 0;
  outline: 0; background: transparent; color: var(--fg); font: inherit;
}
.mj-command-input::placeholder { color: var(--muted); }
.mj-command-close {
  min-width: 2.25rem; min-height: 2.25rem; border: 1px solid transparent;
  border-radius: 8px; background: transparent; color: var(--muted);
  font: 700 1rem/1 sans-serif; cursor: pointer;
}
.mj-command-close:hover, .mj-command-close:focus-visible {
  color: var(--fg); border-color: var(--line); background: var(--panel2);
}
.mj-command-context {
  display: flex; align-items: center; justify-content: space-between; gap: .8rem;
  min-height: 2rem; padding: .35rem .85rem; color: var(--muted);
  border-bottom: 1px solid var(--line); font-size: .72rem;
}
.mj-command-context .is-warning { color: var(--warning); }
.mj-command-results {
  max-height: min(58vh, 31rem); overflow: auto; padding: .45rem;
  scroll-padding-block: .45rem;
}
.mj-command-option {
  width: 100%; min-width: 0; display: grid;
  grid-template-columns: minmax(0,1fr) auto; gap: .2rem .75rem;
  padding: .62rem .68rem; border: 1px solid transparent; border-radius: 9px;
  background: transparent; color: var(--fg); text-align: left; font: inherit;
  cursor: pointer;
}
.mj-command-option + .mj-command-option { margin-top: .1rem; }
.mj-command-option:hover, .mj-command-option[aria-selected="true"] {
  border-color: color-mix(in srgb, var(--accent) 42%, var(--line));
  background: var(--accent-bg);
}
.mj-command-option-title {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-size: .88rem; font-weight: 700;
}
.mj-command-option-detail {
  min-width: 0; grid-column: 1; color: var(--muted); font-size: .72rem;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.mj-command-option-kind {
  grid-row: 1 / span 2; grid-column: 2; align-self: center;
  padding: .08rem .38rem; border: 1px solid var(--line); border-radius: 999px;
  color: var(--muted); font-size: .64rem; white-space: nowrap;
}
.mj-command-empty {
  padding: 2.2rem 1rem; text-align: center; color: var(--muted);
}
.mj-command-empty strong { display: block; color: var(--fg); margin-bottom: .3rem; }
.mj-command-foot {
  display: flex; align-items: center; justify-content: space-between; gap: .8rem;
  padding: .5rem .85rem; border-top: 1px solid var(--line); color: var(--muted);
  font-size: .68rem; background: color-mix(in srgb, var(--panel2) 72%, transparent);
}
.mj-command-keys { display: flex; gap: .65rem; flex-wrap: wrap; }
.mj-command-keys kbd {
  padding: .04rem .25rem; border: 1px solid var(--line); border-radius: 4px;
  background: var(--panel); font: 650 .62rem/1.3 var(--mono);
}
.ws-brand .mj-command-button { margin-left: auto; align-self: center; }

@media (max-width: 760px) {
  .mj-command-label, .mj-command-kbd { display: none; }
  .mj-command-button { min-width: 32px; justify-content: center; padding: .15rem .38rem; }
}
@media (max-width: 480px) {
  .mj-command-dialog {
    width: calc(100vw - 1rem); margin-top: .5rem; border-radius: 12px;
  }
  .mj-command-head { padding: .55rem; }
  .mj-command-context { align-items: flex-start; flex-direction: column; gap: .15rem; }
  .mj-command-results { max-height: calc(100vh - 11.5rem); }
  .mj-command-foot { align-items: flex-start; flex-direction: column; gap: .35rem; }
  .mj-command-option { padding: .58rem; }
}
@media (prefers-reduced-motion: reduce) {
  .mj-command-dialog, .mj-command-option, .mj-command-button { transition: none !important; }
}
"""


_BASE_JS = r'''"use strict";
(function () {
  var STATIC_NAV = __STATIC_NAV__;
  var state = {
    dialog: null, input: null, list: null, context: null, count: null,
    data: {navigation: STATIC_NAV, shots: [], recents: [], project: {bound: false}},
    results: [], selected: 0, trigger: null, loading: false, loaded: false,
    loadedAt: 0, loadError: false, fetchPromise: null, button: null, restoreFocus: true
  };

  function node(tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }
  function clear(el) { while (el && el.firstChild) el.removeChild(el.firstChild); }
  function normalise(value) {
    return String(value || "").toLowerCase().replace(/[\s_\-./\\]+/g, " ").trim();
  }
  function bodyMode() {
    return document.body && document.body.classList.contains("mj-mode-beginner")
      ? "beginner" : "pro";
  }
  function visibleStaticNav() {
    return STATIC_NAV.filter(function (item) {
      return !(bodyMode() === "beginner" && item.pro_only);
    });
  }
  function currentPath() {
    return (document.body && document.body.getAttribute("data-page")) || location.pathname || "/";
  }
  function queryIntent(query) {
    var q = normalise(query);
    if (/(审片|评价|选片|review|theater)/.test(q)) return "review";
    if (/(导入|入库|素材|ingest|import)/.test(q)) return "ingest";
    return "lab";
  }
  function score(command, query) {
    var q = normalise(query);
    if (!q) return Number(command.default_rank || 0);
    var tokens = q.split(" ").filter(Boolean);
    var label = normalise(command.label);
    var detail = normalise(command.detail);
    var hay = normalise([command.label, command.detail].concat(command.keywords || []).join(" "));
    var total = 0;
    for (var i = 0; i < tokens.length; i++) {
      var token = tokens[i];
      if (!hay.includes(token)) return -1;
      if (label === token) total += 180;
      else if (label.startsWith(token)) total += 120;
      else if (label.includes(token)) total += 80;
      else if (detail.includes(token)) total += 38;
      else total += 22;
    }
    total += Number(command.priority || 0);
    return total;
  }
  function staticActions() {
    var bound = !!(state.data.project && state.data.project.bound);
    var workspacePage = currentPath() === "/workspace";
    var rows = [];
    if (document.getElementById("mj-task-center-btn") || window.ManjuTaskCenter) {
      rows.push({
        id: "action:tasks", kind: "action", label: "打开任务中心",
        detail: "查看需要处理、正在进行和最近结束的任务",
        keywords: ["任务", "进度", "后台", "job", "task"], action: "tasks", default_rank: 92
      });
    }
    if (document.getElementById("mj-help-center-btn") || window.ManjuHelpCenter) {
      rows.push({
        id: "action:help", kind: "action", label: "帮助与支持",
        detail: "查看版本、本地数据说明、快捷键、日志和诊断入口",
        keywords: ["帮助", "支持", "关于", "版本", "日志", "快捷键", "诊断", "help", "about", "support"],
        action: "help", default_rank: 90
      });
    }
    if (workspacePage) {
      rows.push({
        id: "action:open-project", kind: "action", label: "打开现有项目",
        detail: "定位到项目文件夹输入框", keywords: ["项目", "打开", "open"],
        action: "focus-open", default_rank: 100
      });
      rows.push({
        id: "action:new-project", kind: "action", label: "新建项目",
        detail: "定位到项目名称输入框", keywords: ["项目", "新建", "create", "new"],
        action: "focus-new", default_rank: 96
      });
    } else {
      rows.push({
        id: "action:workspace", kind: "action", label: bound ? "切换或管理项目" : "打开或新建项目",
        detail: "查看最近项目，或从磁盘打开项目",
        keywords: ["项目", "切换", "工作区", "workspace", "recent"],
        href: "/?workspace=1", default_rank: 88
      });
    }
    return rows;
  }
  function navigationCommands() {
    if (currentPath() === "/workspace" && !(state.data.project && state.data.project.bound)) {
      return [];
    }
    var nav = Array.isArray(state.data.navigation) && state.data.navigation.length
      ? state.data.navigation : visibleStaticNav();
    if (bodyMode() === "beginner") {
      nav = nav.filter(function (item) { return !item.pro_only; });
    }
    var current = currentPath();
    return nav.map(function (item) {
      return {
        id: item.id || ("page:" + item.href), kind: "page", label: item.label,
        detail: (item.group || "页面") + (item.href === current ? " · 当前页面" : ""),
        keywords: item.keywords || [], href: item.href,
        priority: item.href === current ? -25 : 0,
        default_rank: ({"/": 90, "/create": 84, "/storyboard": 82, "/review": 86,
          "/edit": 78, "/exports": 76}[item.href] || 45)
      };
    });
  }
  function shotCommands(query) {
    if (!normalise(query)) return [];
    var intent = queryIntent(query);
    var actionLabel = intent === "review" ? "审片" : (intent === "ingest" ? "导入候选" : "打开镜头");
    var destination = intent === "review" ? "审片" :
      (intent === "ingest" ? "批量入库" : "镜头实验室");
    return (state.data.shots || []).map(function (item) {
      var shot = typeof item === "string" ? {id: item} : (item || {});
      var sid = String(shot.id || "");
      if (!sid) return null;
      var href = intent === "review" ? ("/review?shot=" + encodeURIComponent(sid)) :
        (intent === "ingest" ? ("/ingest?shot=" + encodeURIComponent(sid) + "&role=take") :
          ("/lab?shot=" + encodeURIComponent(sid)));
      var facts = [shot.action, shot.scene].filter(Boolean);
      var detail = destination + (facts.length ? " · " + facts.join(" · ") : "");
      if (shot.available === false) detail += " · 镜头文件需要检查";
      return {
        id: "shot:" + intent + ":" + sid, kind: "shot", label: actionLabel + " " + sid,
        detail: detail, href: href,
        keywords: [sid, shot.action, shot.scene, shot.dialogue].concat(shot.characters || [],
          ["镜头", "shot", "实验室", "审片", "review", "导入", "ingest"]),
        priority: 25
      };
    }).filter(Boolean);
  }
  function recentCommands() {
    return (state.data.recents || []).map(function (item, index) {
      return {
        id: "project:" + item.path, kind: "project", label: item.name || "未命名项目",
        detail: item.current ? "当前项目" : item.path,
        keywords: [item.name, item.path, "项目", "最近", "recent", item.pinned ? "置顶" : ""],
        action: "open-project", path: item.path, current: !!item.current,
        priority: item.current ? -20 : (20 - index), default_rank: item.current ? 5 : (72 - index)
      };
    });
  }
  function allCommands(query) {
    var commands = staticActions().concat(navigationCommands(), recentCommands(), shotCommands(query));
    var q = normalise(query);
    var ranked = commands.map(function (command) {
      return {command: command, score: score(command, q)};
    }).filter(function (row) { return row.score >= 0; });
    ranked.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      var order = {action: 0, page: 1, shot: 2, project: 3};
      var ak = Object.prototype.hasOwnProperty.call(order, a.command.kind) ? order[a.command.kind] : 9;
      var bk = Object.prototype.hasOwnProperty.call(order, b.command.kind) ? order[b.command.kind] : 9;
      if (ak !== bk) return ak - bk;
      return String(a.command.label).localeCompare(String(b.command.label), "zh-CN");
    });
    var seen = Object.create(null), out = [];
    for (var i = 0; i < ranked.length && out.length < 12; i++) {
      var id = ranked[i].command.id;
      if (seen[id]) continue;
      seen[id] = true; out.push(ranked[i].command);
    }
    return out;
  }
  function kindLabel(kind) {
    return ({action: "操作", page: "页面", shot: "镜头", project: "项目"})[kind] || "结果";
  }
  function ensureDialog() {
    if (state.dialog) return state.dialog;
    var dialog = node("dialog", "mj-command-dialog");
    dialog.id = "mj-command-palette";
    dialog.setAttribute("aria-labelledby", "mj-command-title");
    dialog.setAttribute("aria-describedby", "mj-command-description");
    dialog.setAttribute("aria-modal", "true");

    var shell = node("div", "mj-command-shell");
    var head = node("div", "mj-command-head");
    var inputWrap = node("label", "mj-command-input-wrap");
    inputWrap.setAttribute("for", "mj-command-input");
    inputWrap.appendChild(node("span", "mj-command-search-mark"));
    var input = node("input", "mj-command-input");
    input.id = "mj-command-input";
    input.type = "text";
    input.setAttribute("role", "combobox");
    input.autocomplete = "off";
    input.spellcheck = false;
    input.placeholder = "搜索页面、镜头或项目";
    input.setAttribute("aria-label", "搜索页面、镜头或项目");
    input.setAttribute("aria-controls", "mj-command-results");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-haspopup", "listbox");
    input.setAttribute("aria-expanded", "false");
    inputWrap.appendChild(input);
    head.appendChild(inputWrap);
    var close = node("button", "mj-command-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "关闭命令搜索");
    head.appendChild(close);
    shell.appendChild(head);

    var context = node("div", "mj-command-context");
    context.setAttribute("aria-live", "polite");
    var title = node("span", null, "快速前往"); title.id = "mj-command-title";
    var count = node("span", null, ""); count.id = "mj-command-description";
    context.appendChild(title); context.appendChild(count); shell.appendChild(context);

    var list = node("div", "mj-command-results");
    list.id = "mj-command-results"; list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "搜索结果"); shell.appendChild(list);

    var foot = node("div", "mj-command-foot");
    var keys = node("span", "mj-command-keys");
    [["↑↓", "选择"], ["Enter", "打开"], ["Esc", "关闭"]].forEach(function (pair) {
      var wrap = node("span"); wrap.appendChild(node("kbd", null, pair[0]));
      wrap.appendChild(document.createTextNode(" " + pair[1])); keys.appendChild(wrap);
    });
    foot.appendChild(keys);
    foot.appendChild(node("span", null, "可打开页面、镜头和项目；不会构建、生成、选片或锁片"));
    shell.appendChild(foot);
    dialog.appendChild(shell);
    document.body.appendChild(dialog);

    state.dialog = dialog; state.input = input; state.list = list;
    state.context = context; state.count = count;
    close.addEventListener("click", closePalette);
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closePalette();
    });
    dialog.addEventListener("close", function () {
      /* Navigation hooks used by the app or acceptance harness may reopen the
       * native dialog before the queued close event is delivered.  Do not let
       * that stale event collapse the new session. */
      if (dialog.open) return;
      if (state.input) {
        state.input.setAttribute("aria-expanded", "false");
        state.input.removeAttribute("aria-activedescendant");
      }
      if (state.button) state.button.setAttribute("aria-expanded", "false");
      var shouldRestore = state.restoreFocus;
      state.restoreFocus = true;
      if (shouldRestore && state.trigger && typeof state.trigger.focus === "function") {
        try { state.trigger.focus({preventScroll: true}); } catch (err) { state.trigger.focus(); }
      }
    });
    input.addEventListener("input", function () { state.selected = 0; render(); });
    input.addEventListener("keydown", onInputKeydown);
    return dialog;
  }
  function render() {
    if (!state.list) return;
    state.results = allCommands(state.input ? state.input.value : "");
    if (state.selected >= state.results.length) state.selected = Math.max(0, state.results.length - 1);
    clear(state.list);
    if (!state.results.length) {
      var empty = node("div", "mj-command-empty");
      empty.appendChild(node("strong", null, "没有找到匹配项"));
      empty.appendChild(node("span", null, state.loadError
        ? "镜头和最近项目暂时无法更新；页面导航仍然可用。"
        : "可以尝试页面名称、镜头动作、镜头编号或项目名称。"));
      state.list.appendChild(empty);
    } else {
      state.results.forEach(function (command, index) {
        var option = node("button", "mj-command-option");
        option.type = "button"; option.id = "mj-command-option-" + index;
        option.setAttribute("role", "option"); option.setAttribute("tabindex", "-1");
        option.setAttribute("aria-selected", index === state.selected ? "true" : "false");
        option.appendChild(node("span", "mj-command-option-title", command.label));
        option.appendChild(node("span", "mj-command-option-detail", command.detail || ""));
        option.appendChild(node("span", "mj-command-option-kind", kindLabel(command.kind)));
        option.addEventListener("mouseenter", function () { select(index, false); });
        option.addEventListener("click", function () { execute(command); });
        state.list.appendChild(option);
      });
    }
    if (state.input) {
      if (state.results.length) state.input.setAttribute("aria-activedescendant", "mj-command-option-" + state.selected);
      else state.input.removeAttribute("aria-activedescendant");
    }
    if (state.count) {
      var suffix = state.loading ? " · 正在更新项目索引" : "";
      if (state.loadError) suffix = " · 项目索引暂时无法更新，页面仍可打开";
      state.count.textContent = state.results.length + " 项" + suffix;
      state.count.classList.toggle("is-warning", state.loadError);
    }
  }
  function select(index, scroll) {
    if (!state.results.length) return;
    state.selected = Math.max(0, Math.min(index, state.results.length - 1));
    Array.prototype.forEach.call(state.list.querySelectorAll('[role="option"]'), function (el, i) {
      el.setAttribute("aria-selected", i === state.selected ? "true" : "false");
    });
    if (state.input) state.input.setAttribute("aria-activedescendant", "mj-command-option-" + state.selected);
    var active = document.getElementById("mj-command-option-" + state.selected);
    if (active && scroll !== false) active.scrollIntoView({block: "nearest"});
  }
  function onInputKeydown(event) {
    if (event.key === "ArrowDown") { event.preventDefault(); select(state.selected + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); select(state.selected - 1); }
    else if (event.key === "Home") { event.preventDefault(); select(0); }
    else if (event.key === "End") { event.preventDefault(); select(state.results.length - 1); }
    else if (event.key === "Enter") {
      event.preventDefault(); if (state.results[state.selected]) execute(state.results[state.selected]);
    } else if (event.key === "Escape") { event.preventDefault(); closePalette(); }
  }
  function navigate(href) {
    closePalette(false);
    if (typeof window.__MJ_COMMAND_NAVIGATE === "function") {
      window.__MJ_COMMAND_NAVIGATE(href); return;
    }
    window.location.assign(href);
  }
  function focusTarget(id) {
    var target = document.getElementById(id);
    closePalette(false);
    if (!target) return;
    /* Native <dialog> finalises its close/focus algorithm after the current
     * handler.  Move focus on the next task so the safe workspace target wins. */
    window.setTimeout(function () {
      try { target.focus({preventScroll: true}); } catch (err) { target.focus(); }
      target.scrollIntoView({block: "center"});
    }, 0);
  }
  function apiPost(url, body) {
    var meta = document.querySelector('meta[name="manju-token"]');
    var token = meta ? (meta.getAttribute("content") || "") : "";
    if (typeof requestJson === "function") {
      var opts = typeof manjuApiOptions === "function" ? manjuApiOptions({token: token}) : {token: token};
      return requestJson("POST", url, body || {}, opts);
    }
    return fetch(url, {method: "POST", headers: {"Content-Type": "application/json", "X-Manju-Token": token}, body: JSON.stringify(body || {})})
      .then(function (resp) { return resp.json().catch(function () { return {}; }).then(function (data) {
        if (!resp.ok) { var err = new Error(data.error || ("HTTP " + resp.status)); err.data = data; throw err; }
        return data;
      }); });
  }
  function openProject(command) {
    if (command.current) { navigate("/"); return; }
    closePalette(false);
    apiPost("/api/workspace/open", {path: command.path}).then(function (data) {
      if (typeof handleProjectAction === "function") handleProjectAction(data, {onReload: function () { window.location.href = "/"; }});
      else window.location.href = "/";
    }).catch(function (err) {
      if (err && err.data && err.data.next_action && typeof handleProjectAction === "function") {
        handleProjectAction(err.data); return;
      }
      if (typeof toast === "function") toast("无法打开项目：" + ((err && err.message) || String(err)), false);
      else navigate("/?workspace=1");
    });
  }
  function execute(command) {
    if (!command) return;
    if (command.action === "tasks") {
      closePalette(false);
      if (window.ManjuTaskCenter && typeof window.ManjuTaskCenter.open === "function") window.ManjuTaskCenter.open();
      else { var btn = document.getElementById("mj-task-center-btn"); if (btn) btn.click(); }
      return;
    }
    if (command.action === "help") {
      closePalette(false);
      if (window.ManjuHelpCenter && typeof window.ManjuHelpCenter.open === "function") {
        window.ManjuHelpCenter.open(document.getElementById("mj-command-btn") || document.activeElement);
      } else {
        var help = document.getElementById("mj-help-center-btn");
        if (help) help.click();
      }
      return;
    }
    if (command.action === "focus-open") { focusTarget("ws-open-path"); return; }
    if (command.action === "focus-new") { focusTarget("ws-new-name"); return; }
    if (command.action === "open-project") { openProject(command); return; }
    if (command.href) navigate(command.href);
  }
  function fetchData(force) {
    if (state.fetchPromise) return state.fetchPromise;
    var fresh = state.loaded && (Date.now() - state.loadedAt) < 15000;
    if (fresh && !force) return Promise.resolve(state.data);
    state.loading = true; state.loadError = false; render();
    var read;
    if (typeof requestJson === "function") {
      var opts = typeof manjuApiOptions === "function" ? manjuApiOptions() : {};
      read = requestJson("GET", "/api/command-palette", undefined, opts);
    } else {
      read = fetch("/api/command-palette", {headers: {"Accept": "application/json"}}).then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status); return resp.json();
      });
    }
    state.fetchPromise = read.then(function (data) {
      if (data && typeof data === "object") state.data = data;
      state.loaded = true; state.loadedAt = Date.now(); state.loadError = false;
      return state.data;
    }).catch(function () {
      state.loaded = false; state.loadError = true;
      /* A failed refresh must never keep showing shots or recent projects from
       * an older project snapshot.  Static page navigation is safe to retain. */
      state.data = {
        navigation: visibleStaticNav(), shots: [], recents: [],
        project: state.data.project || {bound: false}
      };
      return state.data;
    }).finally(function () {
      state.loading = false; state.fetchPromise = null; render();
    });
    return state.fetchPromise;
  }
  function openPalette(trigger) {
    var dialog = ensureDialog();
    state.trigger = trigger || document.activeElement;
    state.restoreFocus = true;
    state.selected = 0; state.input.value = "";
    state.input.setAttribute("aria-expanded", "true");
    if (state.button) state.button.setAttribute("aria-expanded", "true");
    render();
    if (typeof dialog.showModal === "function") {
      if (!dialog.open) dialog.showModal();
    } else dialog.setAttribute("open", "");
    window.setTimeout(function () { state.input.focus(); }, 0);
    fetchData(false);
  }
  function closePalette(restoreFocus) {
    if (!state.dialog) return;
    state.restoreFocus = restoreFocus !== false;
    if (state.input) {
      state.input.setAttribute("aria-expanded", "false");
      state.input.removeAttribute("aria-activedescendant");
    }
    if (state.button) state.button.setAttribute("aria-expanded", "false");
    if (typeof state.dialog.close === "function" && state.dialog.open) state.dialog.close();
    else state.dialog.removeAttribute("open");
  }
  function init() {
    var button = document.getElementById("mj-command-btn");
    state.button = button;
    if (button) button.addEventListener("click", function () { openPalette(button); });
    document.addEventListener("keydown", function (event) {
      if (event.isComposing) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === "k") {
        event.preventDefault();
        if (state.dialog && state.dialog.open) closePalette(); else openPalette(button || document.activeElement);
      }
    });
    window.addEventListener("manju:jobs-changed", function () {
      /* Project facts may have changed (for example an ingest added shots). The
       * next explicit palette open refreshes the cheap discovery index. */
      state.loaded = false;
    });
    window.ManjuCommandPalette = {
      open: openPalette,
      close: closePalette,
      refresh: function () { state.loaded = false; return fetchData(true); },
      current: function () { return {
        data: state.data, results: state.results.slice(), selected: state.selected,
        loading: state.loading, loadError: state.loadError, loadedAt: state.loadedAt
      }; }
    };
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
'''


def render_command_palette_css() -> str:
    return COMMAND_PALETTE_CSS


def render_command_palette_js() -> str:
    # Import lazily to avoid coupling the page shell's module import to this
    # optional client asset.  The JS ships the static navigation catalog for an
    # instant first paint; the cheap endpoint then adds shots and recents.
    from .pages import navigation_items

    static_nav = json.dumps(
        navigation_items("pro"), ensure_ascii=False, separators=(",", ":")
    )
    return _BASE_JS.replace("__STATIC_NAV__", static_nav)
