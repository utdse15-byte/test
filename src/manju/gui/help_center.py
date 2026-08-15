"""Global Help & Support surface for the local Manju workbench.

The help center is deliberately a *read-only presentation layer*.  It exposes
product identity, the current execution policy, local-data guarantees, global
shortcuts and the existing diagnostic commands.  It does not run ``doctor``,
create a support bundle, inspect Provider credentials, read media, or mutate a
project.  Expensive or sensitive work remains an explicit CLI/user action.
"""

from __future__ import annotations

import html
import platform
from typing import Any

from manju import __version__

__all__ = [
    "about_payload",
    "render_help_button",
    "render_help_center_css",
    "render_help_center_js",
]


def _project_name(project: Any | None) -> str:
    if project is None:
        return ""
    try:
        return str(project.load_config().name or project.root.name)
    except Exception:
        try:
            return str(project.root.name)
        except Exception:
            return ""


def _execution_product_copy(snapshot: dict[str, Any]) -> tuple[str, str]:
    status = str(snapshot.get("status") or "invalid")
    if status == "strict":
        return "本地安全", "不会连接外部 Provider，也不会读取云端密钥。"
    if status == "standard":
        return "标准执行", "外部 Provider 仍受预算、确认与请求身份保护。"
    return "模式错误", "执行配置无效；Provider 操作会在传输前被拒绝。"


def about_payload(
    project: Any | None,
    *,
    readonly: bool = False,
    app_mode: bool = False,
) -> dict[str, Any]:
    """Return a bounded, secret-free product/support snapshot.

    The payload intentionally omits absolute project paths, executable paths,
    host names, environment variables, Provider manifests and credential
    presence.  The existing redacted ``manju support-bundle`` command remains
    the explicit path for deeper diagnostics.
    """

    try:
        from ..providers.zero_cost import execution_policy_snapshot

        execution = dict(execution_policy_snapshot())
    except Exception:
        execution = {"status": "invalid", "mode": "unknown"}

    label, summary = _execution_product_copy(execution)
    system = platform.system() or "Unknown"
    doctor = "manju doctor --windows" if system == "Windows" else "manju doctor"
    project_name = _project_name(project)

    return {
        "schema": "manju.help-center/v1",
        "product": {
            "name": "Manju One",
            "version": __version__,
        },
        "runtime": {
            "python": platform.python_version(),
            "system": system,
            "machine": platform.machine() or "unknown",
        },
        "execution": {
            "status": str(execution.get("status") or "invalid"),
            "mode": str(execution.get("mode") or "unknown"),
            "label": label,
            "summary": summary,
        },
        "project": {
            "bound": project is not None,
            "name": project_name or None,
            "readonly": bool(readonly),
        },
        "application": {
            "app_mode": bool(app_mode),
            "local_server": True,
            "telemetry": "none",
        },
        "commands": {
            "doctor": doctor,
            "support_bundle": "manju support-bundle",
            "windows_logs": r"%LOCALAPPDATA%\Manju\Logs",
        },
        "links": {
            "workspace": "/?workspace=1",
            "doctor": "/doctor" if project is not None else None,
        },
        "shortcuts": [
            {"keys": "Ctrl/Cmd+K", "action": "快速前往页面、镜头或项目"},
            {"keys": "F1", "action": "打开帮助与支持"},
            {"keys": "?", "action": "在审片和剪辑页打开当前页面快捷键"},
        ],
        "guarantees": [
            {
                "title": "本地工作台",
                "detail": "界面通过本机 loopback 服务运行；Manju 本身不上传产品使用遥测。",
            },
            {
                "title": "项目真相可见",
                "detail": "项目文本仍是唯一真相，媒体保持只追加；帮助面板不会修改项目。",
            },
            {
                "title": "诊断由你决定",
                "detail": "环境检查与脱敏支持包都需要你显式运行，不会在后台自动收集或发送。",
            },
        ],
    }


def render_help_button(*, compact: bool = False) -> str:
    """Render the one shared Help & Support trigger."""

    classes = "mj-help-center-button"
    if compact:
        classes += " compact"
    return (
        f'<button type="button" id="mj-help-center-btn" class="{classes}" '
        'aria-haspopup="dialog" aria-controls="mj-help-center" '
        'aria-expanded="false" aria-keyshortcuts="F1" '
        'title="帮助与支持（F1）">'
        '<span class="mj-help-center-icon" aria-hidden="true">?</span>'
        '<span class="mj-help-center-label">帮助</span></button>'
    )


HELP_CENTER_CSS = r"""
/* Product Polish R1 final wave — global, local-only Help & Support. */
.mj-help-center-button {
  min-width: 32px; min-height: 32px; display: inline-flex; align-items: center;
  justify-content: center; gap: .34rem; padding: .15rem .48rem;
  border: 1px solid var(--line); border-radius: 8px; background: var(--panel2);
  color: var(--muted); font: inherit; cursor: pointer; white-space: nowrap;
}
.mj-help-center-button:hover, .mj-help-center-button:focus-visible,
.mj-help-center-button[aria-expanded="true"] {
  color: var(--fg); border-color: color-mix(in srgb, var(--accent) 58%, var(--line));
  background: color-mix(in srgb, var(--panel2) 78%, var(--accent-bg));
}
.mj-help-center-icon {
  display: inline-grid; place-items: center; width: 1rem; height: 1rem;
  border: 1px solid currentColor; border-radius: 999px; font: 750 .68rem/1 sans-serif;
}
.mj-help-center-label { font-size: .76rem; font-weight: 650; }
.mj-help-center-button.compact .mj-help-center-label { display: none; }

.mj-help-center-dialog {
  width: min(47rem, calc(100vw - 2rem)); max-width: none; max-height: min(84vh, 48rem);
  margin: 7vh auto auto; padding: 0; overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--accent) 34%, var(--line));
  border-radius: 14px; background: var(--panel); color: var(--fg);
  box-shadow: 0 28px 90px rgba(0,0,0,.58);
}
.mj-help-center-dialog::backdrop {
  background: rgba(4,6,10,.72); backdrop-filter: blur(2px);
}
.mj-help-center-shell { display: flex; flex-direction: column; max-height: inherit; min-width: 0; }
.mj-help-center-head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 1rem;
  padding: 1rem 1.05rem .85rem; border-bottom: 1px solid var(--line);
  background: color-mix(in srgb, var(--panel2) 76%, transparent);
}
.mj-help-center-head h2 { margin: 0; font-size: 1.1rem; }
.mj-help-center-head p { margin: .2rem 0 0; color: var(--muted); font-size: .8rem; }
.mj-help-center-close {
  flex: 0 0 auto; min-width: 2.25rem; min-height: 2.25rem; border: 1px solid transparent;
  border-radius: 8px; background: transparent; color: var(--muted);
  font: 700 1rem/1 sans-serif; cursor: pointer;
}
.mj-help-center-close:hover, .mj-help-center-close:focus-visible {
  color: var(--fg); border-color: var(--line); background: var(--panel2);
}
.mj-help-center-body { overflow: auto; padding: 1rem 1.05rem 1.1rem; }
.mj-help-center-loading, .mj-help-center-error {
  padding: .8rem .9rem; border: 1px solid var(--line); border-radius: 9px;
  background: var(--panel2); color: var(--muted);
}
.mj-help-center-error { border-color: color-mix(in srgb, var(--danger, #ff8a90) 52%, var(--line)); }
.mj-help-product {
  display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .45rem 1rem;
  align-items: center; padding: .85rem .9rem; border: 1px solid var(--line);
  border-radius: 10px; background: var(--panel2);
}
.mj-help-product strong { font-size: 1rem; }
.mj-help-version { color: var(--muted); font: 650 .74rem/1.4 var(--mono); }
.mj-help-policy { grid-column: 1 / -1; display: flex; align-items: flex-start; gap: .55rem; }
.mj-help-policy-dot {
  flex: 0 0 auto; width: .58rem; height: .58rem; margin-top: .28rem;
  border-radius: 999px; background: var(--success, #6fdca0);
}
.mj-help-policy.invalid .mj-help-policy-dot { background: var(--danger, #ff8a90); }
.mj-help-policy.standard .mj-help-policy-dot { background: var(--warning, #ffc94d); }
.mj-help-policy-copy { min-width: 0; }
.mj-help-policy-copy b { display: block; }
.mj-help-policy-copy span { color: var(--muted); font-size: .78rem; }
.mj-help-section { margin-top: 1rem; }
.mj-help-section h3 { margin: 0 0 .55rem; font-size: .9rem; }
.mj-help-guarantees, .mj-help-shortcuts {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .55rem;
  margin: 0; padding: 0; list-style: none;
}
.mj-help-guarantees li, .mj-help-shortcuts li {
  min-width: 0; padding: .68rem .72rem; border: 1px solid var(--line);
  border-radius: 9px; background: color-mix(in srgb, var(--panel2) 76%, transparent);
}
.mj-help-guarantees b, .mj-help-shortcuts b { display: block; margin-bottom: .2rem; font-size: .8rem; }
.mj-help-guarantees span, .mj-help-shortcuts span { color: var(--muted); font-size: .74rem; line-height: 1.45; }
.mj-help-shortcuts kbd {
  display: inline-block; margin-bottom: .3rem; padding: .08rem .34rem;
  border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 5px;
  background: var(--bg); color: var(--fg); font: 650 .68rem/1.4 var(--mono);
}
.mj-help-actions { display: flex; flex-wrap: wrap; gap: .48rem; }
.mj-help-actions .btn, .mj-help-actions button, .mj-help-actions a {
  min-height: 34px; display: inline-flex; align-items: center; justify-content: center;
  box-sizing: border-box;
}
.mj-help-actions a { text-decoration: none; }
.mj-help-copy-status { min-height: 1.2rem; margin: .45rem 0 0; color: var(--muted); font-size: .75rem; }
.mj-help-copy-buffer {
  position: fixed; left: -10000px; top: 0; width: 1px; height: 1px;
  opacity: 0; pointer-events: none;
}
.mj-help-technical { margin-top: .9rem; border-top: 1px solid var(--line); padding-top: .75rem; }
.mj-help-technical summary { cursor: pointer; color: var(--muted); font-size: .78rem; font-weight: 700; }
.mj-help-tech-grid {
  display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: .35rem .75rem;
  margin-top: .6rem; font-size: .75rem;
}
.mj-help-tech-grid dt { color: var(--muted); }
.mj-help-tech-grid dd { margin: 0; min-width: 0; overflow-wrap: anywhere; font-family: var(--mono); }

@media (max-width: 760px) {
  .mj-help-center-label { display: none; }
  .mj-help-center-button { padding-inline: .38rem; }
  .mj-help-guarantees, .mj-help-shortcuts { grid-template-columns: 1fr; }
}
@media (max-width: 440px) {
  .mj-help-center-dialog {
    width: calc(100vw - 1rem); max-height: calc(100vh - 1rem); margin: .5rem auto auto;
    border-radius: 12px;
  }
  .mj-help-center-head { padding: .82rem .78rem .68rem; }
  .mj-help-center-body { padding: .78rem; }
  .mj-help-product { grid-template-columns: 1fr; }
  .mj-help-version { justify-self: start; }
  .mj-help-policy { grid-column: auto; }
  .mj-help-actions { display: grid; grid-template-columns: 1fr; }
  .mj-help-actions .btn, .mj-help-actions button, .mj-help-actions a { width: 100%; }
  .mj-help-tech-grid { grid-template-columns: 1fr; gap: .12rem; }
  .mj-help-tech-grid dd { margin-bottom: .4rem; }
}
@media (prefers-reduced-motion: reduce) {
  .mj-help-center-dialog::backdrop { backdrop-filter: none; }
}
"""


HELP_CENTER_JS = r'''"use strict";
/* Global Help & Support. Read-only: it never runs doctor/support-bundle itself. */
(function () {
  var state = {
    button: null, dialog: null, body: null, close: null, trigger: null,
    data: null, loadedAt: 0, loading: false, promise: null, restoreFocus: true
  };

  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function clear(n) { while (n && n.firstChild) n.removeChild(n.firstChild); }
  function button(label, cls) {
    var b = node("button", cls || "btn ghost", label); b.type = "button"; return b;
  }
  function apiGet() {
    if (typeof requestJson === "function") {
      var opts = typeof manjuApiOptions === "function" ? manjuApiOptions() : {};
      return requestJson("GET", "/api/app/about", undefined, opts);
    }
    return fetch("/api/app/about", {headers: {"Accept": "application/json"}}).then(function (resp) {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  }
  function copyText(text) {
    var value = String(text || "");
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      return navigator.clipboard.writeText(value);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = value; area.setAttribute("readonly", "");
      area.className = "mj-help-copy-buffer";
      document.body.appendChild(area); area.select();
      try { document.execCommand("copy") ? resolve() : reject(new Error("copy failed")); }
      catch (err) { reject(err); }
      area.remove();
    });
  }
  function setStatus(text, bad) {
    var host = document.getElementById("mj-help-copy-status");
    if (!host) return;
    host.textContent = text || "";
    host.classList.toggle("is-error", !!bad);
  }
  function ensureDialog() {
    if (state.dialog) return state.dialog;
    var dialog = node("dialog", "mj-help-center-dialog");
    dialog.id = "mj-help-center";
    dialog.setAttribute("aria-labelledby", "mj-help-center-title");
    dialog.setAttribute("aria-describedby", "mj-help-center-description");
    dialog.setAttribute("aria-modal", "true");

    var shell = node("div", "mj-help-center-shell");
    var head = node("div", "mj-help-center-head");
    var heading = node("div");
    var title = node("h2", null, "帮助与支持"); title.id = "mj-help-center-title";
    var desc = node("p", null, "查看当前版本、安全模式、快捷键和本地诊断入口。");
    desc.id = "mj-help-center-description";
    heading.appendChild(title); heading.appendChild(desc); head.appendChild(heading);
    var close = node("button", "mj-help-center-close", "×");
    close.type = "button"; close.setAttribute("aria-label", "关闭帮助与支持");
    head.appendChild(close); shell.appendChild(head);
    var body = node("div", "mj-help-center-body"); shell.appendChild(body);
    dialog.appendChild(shell); document.body.appendChild(dialog);

    state.dialog = dialog; state.body = body; state.close = close;
    close.addEventListener("click", closeHelp);
    dialog.addEventListener("click", function (event) { if (event.target === dialog) closeHelp(); });
    dialog.addEventListener("cancel", function (event) { event.preventDefault(); closeHelp(); });
    dialog.addEventListener("close", function () {
      if (dialog.open) return;
      if (state.button) state.button.setAttribute("aria-expanded", "false");
      var target = state.trigger; state.trigger = null;
      if (state.restoreFocus && target && typeof target.focus === "function") {
        try { target.focus({preventScroll: true}); } catch (err) { target.focus(); }
      }
      state.restoreFocus = true;
    });
    dialog.addEventListener("keydown", function (event) {
      if (event.key !== "Tab") return;
      var focusable = Array.prototype.filter.call(
        dialog.querySelectorAll('button:not([disabled]),a[href],summary,[tabindex]:not([tabindex="-1"])'),
        function (item) { return !item.hidden && item.offsetParent !== null; }
      );
      if (!focusable.length) return;
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    return dialog;
  }
  function addAction(host, label, handler, options) {
    options = options || {};
    var control;
    if (options.href) {
      control = node("a", "btn ghost", label); control.href = options.href;
    } else {
      control = button(label, options.primary ? "btn" : "btn ghost");
      control.addEventListener("click", handler);
    }
    if (options.disabled) {
      if (control.tagName === "A") { control.removeAttribute("href"); control.setAttribute("aria-disabled", "true"); }
      else control.disabled = true;
      control.title = options.title || "请先打开项目";
    }
    host.appendChild(control); return control;
  }
  function summaryText(data) {
    var product = data.product || {}, runtime = data.runtime || {}, execution = data.execution || {};
    var project = data.project || {};
    return [
      (product.name || "Manju One") + " " + (product.version || "?"),
      "运行环境：" + (runtime.system || "?") + " / Python " + (runtime.python || "?"),
      "执行模式：" + (execution.label || "?") + " (" + (execution.mode || "?") + ")",
      "项目：" + (project.bound ? (project.name || "已打开") : "尚未打开"),
      "只读：" + (project.readonly ? "是" : "否"),
      "产品遥测：无"
    ].join("\n");
  }
  function renderData(data) {
    clear(state.body);
    var product = data.product || {}, runtime = data.runtime || {}, execution = data.execution || {};
    var project = data.project || {}, commands = data.commands || {}, links = data.links || {};

    var productCard = node("section", "mj-help-product");
    productCard.appendChild(node("strong", null, (product.name || "Manju One")));
    productCard.appendChild(node("span", "mj-help-version", "版本 " + (product.version || "?")));
    var policy = node("div", "mj-help-policy " + (execution.status || "invalid"));
    policy.appendChild(node("span", "mj-help-policy-dot"));
    var policyCopy = node("div", "mj-help-policy-copy");
    policyCopy.appendChild(node("b", null, execution.label || "执行模式不可用"));
    policyCopy.appendChild(node("span", null, execution.summary || "当前模式信息暂不可用。"));
    policy.appendChild(policyCopy); productCard.appendChild(policy);
    state.body.appendChild(productCard);

    var guarantees = node("section", "mj-help-section"); guarantees.appendChild(node("h3", null, "数据与决定权"));
    var gList = node("ul", "mj-help-guarantees");
    (data.guarantees || []).forEach(function (item) {
      var li = node("li"); li.appendChild(node("b", null, item.title || ""));
      li.appendChild(node("span", null, item.detail || "")); gList.appendChild(li);
    });
    guarantees.appendChild(gList); state.body.appendChild(guarantees);

    var shortcuts = node("section", "mj-help-section"); shortcuts.appendChild(node("h3", null, "常用快捷键"));
    var sList = node("ul", "mj-help-shortcuts");
    (data.shortcuts || []).forEach(function (item) {
      var li = node("li"); li.appendChild(node("kbd", null, item.keys || ""));
      li.appendChild(node("span", null, item.action || "")); sList.appendChild(li);
    });
    shortcuts.appendChild(sList); state.body.appendChild(shortcuts);

    var support = node("section", "mj-help-section"); support.appendChild(node("h3", null, "帮助与诊断"));
    var actions = node("div", "mj-help-actions");
    addAction(actions, "快速前往", function () {
      closeHelp(false);
      if (window.ManjuCommandPalette && typeof window.ManjuCommandPalette.open === "function") {
        window.ManjuCommandPalette.open(state.button || document.activeElement);
      } else { var b = document.getElementById("mj-command-btn"); if (b) b.click(); }
    });
    addAction(actions, "任务中心", function () {
      closeHelp(false);
      if (window.ManjuTaskCenter && typeof window.ManjuTaskCenter.open === "function") window.ManjuTaskCenter.open();
      else { var b = document.getElementById("mj-task-center-btn"); if (b) b.click(); }
    }, {disabled: !document.getElementById("mj-task-center-btn") && !window.ManjuTaskCenter});
    addAction(actions, "环境检查", null, {
      href: links.doctor || "", disabled: !links.doctor, title: "请先打开项目"
    });
    addAction(actions, "项目工作区", null, {href: links.workspace || "/?workspace=1"});
    addAction(actions, "复制产品信息", function () {
      copyText(summaryText(data)).then(function () { setStatus("产品信息已复制。", false); })
        .catch(function () { setStatus("无法复制；可以在技术详情中手动选择。", true); });
    });
    addAction(actions, "复制支持包命令", function () {
      copyText(commands.support_bundle || "manju support-bundle")
        .then(function () { setStatus("支持包命令已复制；运行后仍由你决定是否分享。", false); })
        .catch(function () { setStatus("无法复制支持包命令。", true); });
    });
    support.appendChild(actions);
    var status = node("p", "mj-help-copy-status", ""); status.id = "mj-help-copy-status";
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    support.appendChild(status); state.body.appendChild(support);

    var tech = node("details", "mj-help-technical"); tech.appendChild(node("summary", null, "技术详情"));
    var dl = node("dl", "mj-help-tech-grid");
    [
      ["版本", product.version || "?"],
      ["Python", runtime.python || "?"],
      ["系统", (runtime.system || "?") + " / " + (runtime.machine || "?")],
      ["执行模式", execution.mode || "?"],
      ["项目", project.bound ? (project.name || "已打开") : "尚未打开"],
      ["只读", project.readonly ? "true" : "false"],
      ["环境检查", commands.doctor || "manju doctor"],
      ["支持包", commands.support_bundle || "manju support-bundle"],
      ["Windows App 日志", commands.windows_logs || "%LOCALAPPDATA%\\Manju\\Logs"]
    ].forEach(function (row) { dl.appendChild(node("dt", null, row[0])); dl.appendChild(node("dd", null, row[1])); });
    tech.appendChild(dl); state.body.appendChild(tech);
  }
  function renderLoading() {
    clear(state.body); state.body.appendChild(node("div", "mj-help-center-loading", "正在读取本地产品信息…"));
  }
  function renderError() {
    clear(state.body);
    var box = node("div", "mj-help-center-error");
    box.appendChild(node("strong", null, "产品信息暂时无法读取"));
    box.appendChild(node("p", null, "页面和项目没有被修改。你仍可使用快速前往、任务中心和命令行诊断。"));
    var actions = node("div", "mj-help-actions");
    addAction(actions, "重试", function () { load(true).catch(function () {}); }, {primary: true});
    addAction(actions, "复制环境检查命令", function () {
      copyText("manju doctor").then(function () { setStatus("环境检查命令已复制。", false); })
        .catch(function () { setStatus("无法复制环境检查命令。", true); });
    });
    box.appendChild(actions);
    var status = node("p", "mj-help-copy-status", ""); status.id = "mj-help-copy-status";
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    box.appendChild(status); state.body.appendChild(box);
  }
  function load(force) {
    if (state.promise) return state.promise;
    if (state.data && !force && Date.now() - state.loadedAt < 15000) {
      renderData(state.data); return Promise.resolve(state.data);
    }
    state.loading = true; renderLoading();
    state.promise = apiGet().then(function (data) {
      state.data = data && typeof data === "object" ? data : {};
      state.loadedAt = Date.now(); renderData(state.data); return state.data;
    }).catch(function (err) {
      state.data = null; state.loadedAt = 0; renderError(); throw err;
    }).finally(function () { state.loading = false; state.promise = null; });
    return state.promise;
  }
  function openHelp(trigger) {
    var dialog = ensureDialog();
    state.trigger = trigger || document.activeElement; state.restoreFocus = true;
    if (state.button) state.button.setAttribute("aria-expanded", "true");
    if (typeof dialog.showModal === "function") { if (!dialog.open) dialog.showModal(); }
    else dialog.setAttribute("open", "");
    load(false).catch(function () {});
    window.setTimeout(function () { if (state.close) state.close.focus(); }, 0);
  }
  function closeHelp(restoreFocus) {
    if (!state.dialog) return;
    state.restoreFocus = restoreFocus !== false;
    if (state.button) state.button.setAttribute("aria-expanded", "false");
    if (typeof state.dialog.close === "function" && state.dialog.open) state.dialog.close();
    else state.dialog.removeAttribute("open");
  }
  function init() {
    state.button = document.getElementById("mj-help-center-btn");
    if (state.button) state.button.addEventListener("click", function () { openHelp(state.button); });
    document.addEventListener("keydown", function (event) {
      if (event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key === "F1") { event.preventDefault(); openHelp(state.button || document.activeElement); }
    });
    window.ManjuHelpCenter = {
      open: openHelp,
      close: closeHelp,
      refresh: function () { state.loadedAt = 0; return load(true); },
      current: function () { return {data: state.data, loadedAt: state.loadedAt, loading: state.loading}; }
    };
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
'''


def render_help_center_css() -> str:
    return HELP_CENTER_CSS


def render_help_center_js() -> str:
    return HELP_CENTER_JS
