"""Project open/create next_action UI (classic script globals).

Served as ``/project-action.js`` after ``/webclient.js``. Handles the unified
API contract (reload_current vs open_in_new_window) with a modal dialog —
never toast/alert for normal "open in new window" workflows.
"""

from __future__ import annotations

__all__ = ["render_project_action_js", "render_project_action_css",
           "project_public_dict", "project_next_action", "gui_launch_command"]

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.container import Project

PROJECT_ACTION_CSS = r"""
.mj-pa-ov {
  position: fixed; inset: 0; z-index: 10000;
  background: rgba(15, 18, 24, 0.72);
  display: flex; align-items: center; justify-content: center;
  padding: 1.5rem;
}
.mj-pa-dlg {
  background: var(--panel, #181b21); color: var(--fg, #eceef2);
  border: 1px solid var(--line, #2e3440);
  border-radius: 10px; max-width: 32rem; width: 100%;
  padding: 1.25rem 1.4rem; box-shadow: 0 12px 40px rgba(0,0,0,.45);
  font: 14px/1.45 system-ui, sans-serif;
}
.mj-pa-dlg h3 { margin: 0 0 .6rem; font-size: 1.1rem; font-weight: 600; }
.mj-pa-name { font-weight: 600; margin: .4rem 0 .15rem; }
.mj-pa-path {
  color: #9aa0a6; font-size: .85rem; word-break: break-all;
  margin: 0 0 .75rem;
}
.mj-pa-note { color: #c4c7ce; font-size: .9rem; margin: 0 0 1rem; }
.mj-pa-cmd {
  background: var(--bg, #0f1115); border: 1px solid var(--line, #2e3440);
  border-radius: 6px;
  padding: .5rem .65rem; font: 12px/1.4 ui-monospace, Consolas, monospace;
  color: #b8d4ff; word-break: break-all; margin: 0 0 1rem;
  user-select: all;
}
.mj-pa-row { display: flex; flex-wrap: wrap; gap: .5rem; justify-content: flex-end; }
.mj-pa-row button {
  font: inherit; cursor: pointer; border-radius: 6px; padding: .4rem .85rem;
  border: 1px solid #4a5160; background: #2a2f3a; color: #e8eaed;
}
.mj-pa-row button.primary {
  background: #3b6ea8; border-color: #4a82c4; color: #fff;
}
.mj-pa-row button:disabled { opacity: .55; cursor: wait; }
.mj-pa-status { font-size: .85rem; color: #9aa0a6; min-height: 1.2em; margin: 0 0 .5rem; }
"""

PROJECT_ACTION_JS = r"""
"use strict";
/* Depends on /webclient.js (ManjuApiError, requestJson, manjuApiOptions). */

function _paEscape(s) {
  return String(s == null ? "" : s);
}

/**
 * Unified handler for project open/create responses (2xx or ManjuApiError.data).
 * @param {object} data
 * @param {{onReload?: function, onError?: function}} hooks
 */
function handleProjectAction(data, hooks) {
  hooks = hooks || {};
  if (!data) return;
  var next = data.next_action || {};
  var kind = next.kind || "";

  if (kind === "already_open") {
    /* Same project already bound — no navigation, no dialog. */
    return;
  }

  if (kind === "reload_current") {
    if (typeof hooks.onReload === "function") {
      hooks.onReload(data);
    } else {
      window.location.href = "/";
    }
    return;
  }

  if (kind === "open_in_new_window" || data.open_in_new_window) {
    showProjectActionDialog(data);
    return;
  }

  /* Fallback: success without next_action — treat as reload if ok */
  if (data.ok && !data.error) {
    if (typeof hooks.onReload === "function") hooks.onReload(data);
    else window.location.href = "/";
  }
}

function showProjectActionDialog(data) {
  var existing = document.getElementById("mj-pa-ov");
  if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

  var proj = data.project || {};
  var next = data.next_action || {};
  var created = (data.code === "project_created" || data.code === "project_created_and_bound");
  var title = created ? "项目创建成功" : "项目需要在独立窗口打开";
  var name = proj.name || data.name || "";
  var root = proj.root || data.root || "";
  var cmd = next.display_command || data.cli ||
    (root ? ('manju gui "' + root + '" --app --port 0') : "manju gui --app --port 0");
  var launchOk = next.launch_supported !== false;

  var ov = document.createElement("div");
  ov.id = "mj-pa-ov";
  ov.className = "mj-pa-ov";
  ov.setAttribute("role", "dialog");
  ov.setAttribute("aria-modal", "true");

  var dlg = document.createElement("div");
  dlg.className = "mj-pa-dlg";

  var h = document.createElement("h3");
  h.textContent = title;
  dlg.appendChild(h);

  if (name) {
    var n = document.createElement("div");
    n.className = "mj-pa-name";
    n.textContent = name;
    dlg.appendChild(n);
  }
  if (root) {
    var p = document.createElement("div");
    p.className = "mj-pa-path";
    p.textContent = root;
    dlg.appendChild(p);
  }

  var note = document.createElement("p");
  note.className = "mj-pa-note";
  note.textContent = created
    ? "新项目已创建。当前窗口继续保留现有项目与任务，可在新窗口打开它。"
    : "当前窗口会继续保留现有项目，任务和审片状态不会受到影响。";
  dlg.appendChild(note);

  var cmdEl = document.createElement("pre");
  cmdEl.className = "mj-pa-cmd";
  cmdEl.textContent = cmd;
  dlg.appendChild(cmdEl);

  var status = document.createElement("div");
  status.className = "mj-pa-status";
  status.id = "mj-pa-status";
  dlg.appendChild(status);

  var row = document.createElement("div");
  row.className = "mj-pa-row";

  function close() {
    if (ov.parentNode) ov.parentNode.removeChild(ov);
  }

  var btnLaunch = document.createElement("button");
  btnLaunch.type = "button";
  btnLaunch.className = "primary";
  btnLaunch.textContent = "打开新窗口";
  btnLaunch.disabled = !launchOk || !root;
  btnLaunch.addEventListener("click", function () {
    btnLaunch.disabled = true;
    status.textContent = "正在启动…";
    requestJson("POST", "/api/workspace/launch",
      { project_id: proj.id || "", path: root },
      manjuApiOptions())
      .then(function (resp) {
        status.textContent = (resp && resp.message) || "已启动新窗口";
        setTimeout(close, 900);
      })
      .catch(function (err) {
        btnLaunch.disabled = false;
        status.textContent = "启动失败：" + ((err && err.message) || String(err));
      });
  });
  row.appendChild(btnLaunch);

  var btnCopy = document.createElement("button");
  btnCopy.type = "button";
  btnCopy.textContent = "复制启动命令";
  btnCopy.addEventListener("click", function () {
    function ok() { status.textContent = "命令已复制"; }
    function fail() {
      try {
        var ta = document.createElement("textarea");
        ta.value = cmd;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        ok();
      } catch (e) {
        status.textContent = "复制失败，请手动选择命令";
      }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(cmd).then(ok).catch(fail);
    } else {
      fail();
    }
  });
  row.appendChild(btnCopy);

  var btnCancel = document.createElement("button");
  btnCancel.type = "button";
  btnCancel.textContent = "取消";
  btnCancel.addEventListener("click", close);
  row.appendChild(btnCancel);

  dlg.appendChild(row);
  ov.appendChild(dlg);
  ov.addEventListener("click", function (ev) {
    if (ev.target === ov) close();
  });
  document.body.appendChild(ov);
  btnCancel.focus();
}

/**
 * Safe quit dialog when jobs are still running.
 * @param {{running_job?: object, queued_count?: number}} status
 * @param {function(string): void} onQuit  mode: after_current | cancel_running
 */
function showQuitDialog(status, onQuit) {
  var existing = document.getElementById("mj-quit-ov");
  if (existing && existing.parentNode) existing.parentNode.removeChild(existing);

  var running = status && status.running_job;
  var q = (status && status.queued_count) || 0;

  var ov = document.createElement("div");
  ov.id = "mj-quit-ov";
  ov.className = "mj-pa-ov";
  ov.setAttribute("role", "dialog");
  ov.setAttribute("aria-modal", "true");

  var dlg = document.createElement("div");
  dlg.className = "mj-pa-dlg";

  var h = document.createElement("h3");
  h.textContent = "当前仍有任务运行";
  dlg.appendChild(h);

  var note = document.createElement("p");
  note.className = "mj-pa-note";
  var lines = [];
  if (running) {
    lines.push("正在执行：" + (running.kind || "任务") +
      (running.id ? (" (#" + running.id + ")") : ""));
  }
  if (q > 0) {
    lines.push("另有 " + q + " 个任务等待执行");
  }
  if (!lines.length) {
    lines.push("任务状态已变化，可直接退出。");
  }
  note.textContent = lines.join("\n");
  note.style.whiteSpace = "pre-line";
  dlg.appendChild(note);

  var row = document.createElement("div");
  row.className = "mj-pa-row";

  function close() {
    if (ov.parentNode) ov.parentNode.removeChild(ov);
  }

  function quit(mode) {
    close();
    if (typeof onQuit === "function") onQuit(mode);
  }

  var b1 = document.createElement("button");
  b1.type = "button";
  b1.className = "primary";
  b1.textContent = "完成当前任务后退出";
  b1.addEventListener("click", function () { quit("after_current"); });
  row.appendChild(b1);

  var b2 = document.createElement("button");
  b2.type = "button";
  b2.textContent = "取消任务并退出";
  b2.addEventListener("click", function () { quit("cancel_running"); });
  row.appendChild(b2);

  var b3 = document.createElement("button");
  b3.type = "button";
  b3.textContent = "继续使用";
  b3.addEventListener("click", close);
  row.appendChild(b3);

  dlg.appendChild(row);
  ov.appendChild(dlg);
  document.body.appendChild(ov);
  b3.focus();
}
"""


def render_project_action_js() -> str:
    return PROJECT_ACTION_JS


def render_project_action_css() -> str:
    return PROJECT_ACTION_CSS


def gui_launch_command(project_root: "Path | str") -> str:
    """Display string for the user (copyable); not executed by the browser."""
    root = str(project_root)
    return f'manju gui "{root}" --app --port 0'


def project_public_dict(project: "Project") -> dict[str, Any]:
    """``{id, name, root}`` for project-action API responses."""
    from .state import project_identity

    try:
        name = project.load_config().name
    except Exception:
        name = project.root.name
    return {
        "id": project_identity(project),
        "name": name,
        "root": str(project.root),
    }


def project_next_action(
    project: "Project",
    *,
    kind: str,
    launch_supported: bool = True,
) -> dict[str, Any]:
    """``next_action`` block for bind / create / open_in_new_window responses."""
    if kind == "reload_current":
        return {"kind": "reload_current"}
    cmd = gui_launch_command(project.root)
    return {
        "kind": "open_in_new_window",
        "display_command": cmd,
        "launch_supported": bool(launch_supported),
    }
