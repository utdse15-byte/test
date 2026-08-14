"""Plain-language glossary — engineering term → 中文用户词 (round U, item 18).

The single source of truth for the workbench's product copy: the §10 table of
REPORTS/ROUND-U-REFERENCES.md adopted VERBATIM. Every engineering concept the
GUI surfaces (provider, take, stale, fallback, …) maps to a 剪映-style plain
Chinese word plus a one-line, 望文生义 explanation. The GUI shows the 中文用户词
as the label and hangs the English original + the explanation off an accessible
``?`` tooltip at each term's first appearance on a page.

This module owns three things:

  * :data:`GLOSSARY` — ``engineering term -> (中文用户词, 一句话说明)``, the §10
    table verbatim (the naming SSOT; the CLI wording is deliberately NOT touched);
  * :func:`term` / :func:`tooltip_html` — render a plain word, or the word with
    an accessible ``?`` help button (hover, focus, click and Escape; CSP-safe;
    all text ``html.escape``-d);
  * the shared chrome assets (:func:`render_glossary_css` / :func:`render_glossary_js`)
    that also drive the 新手/专业 mode switch and the §10 "显示专业术语" toggle —
    both served same-origin so a strict ``script-src 'self'; style-src 'self'``
    CSP permits them, neither carrying an inline handler or an inline ``style=``.

The English original is greyed BESIDE the Chinese word only when the reader has
turned "显示专业术语" on (a ``mj-show-terms`` body class); beginners just see the
Chinese, pros can still map every word back to the docs.
"""

from __future__ import annotations

import html

__all__ = [
    "GLOSSARY",
    "term",
    "tooltip_html",
    "render_glossary_css",
    "render_glossary_js",
]

# The §10 table of REPORTS/ROUND-U-REFERENCES.md, VERBATIM. Order preserved from
# the report so this reads as its Python mirror. Engineering term -> (中文用户词,
# 一句话说明). The 中文用户词 keeps the report's slash-separated alternatives; the
# label helper (:func:`term`) takes the first as the primary word.
GLOSSARY: dict[str, tuple[str, str]] = {
    "provider": ("生成来源", "这条画面/配音是用哪个 AI 模型或服务做出来的。"),
    "take": ("版本 / 这一条", "同一个镜头反复生成的不同版本，可并排对比、挑一条留用。"),
    "stale": ("待更新 / 需重做", "上游改过之后这一条还是旧的，得重新生成才跟得上。"),
    "fallback": ("备用方案 / 兜底", "首选来源失败时自动改用的替代方案，保证片子出得来。"),
    "routing": ("智能派单 / 线路选择", "系统自动决定每条任务交给哪个模型来做，你不用手动指定。"),
    "ledger": ("制作台账 / 制作记录", "完整记下每一步生成了什么、花了多少，随时可回查。"),
    "sidecar": ("配套信息", "跟素材一起存的说明文件（参数、来源、时间），不占画面。"),
    "manifest": ("成片清单 / 配方单", "记录整片由哪些镜头、素材、参数拼成，照它能一模一样再做一遍。"),
    "QC": ("质量检查 / 质检", "自动帮你查画面、字幕、音量有没有明显问题。"),
    "repair plan": ("修复方案", "针对质检查出的问题，系统给出的一键修补步骤。"),
    "lock": ("锁定", "锁住这一条，避免被重新生成或不小心改动。"),
    "snapshot": ("存档点", "把当前状态存下来，随时可以回到这一刻。"),
    "rollback": ("还原 / 回到上一版", "放弃这次改动，退回之前的存档点。"),
    "proxy": ("预览版 / 低清代理", "用小体积低清文件流畅预览，导出时自动换回高清。"),
    "render": ("合成导出 / 渲染", "把时间线上所有内容合成为最终成片。"),
    "timeline": ("时间线", "按时间先后排素材的编辑区（沿用剪映同名词）。"),
    "shot": ("镜头", "一段连续画面，分镜表里的一格。"),
    "storyboard": ("分镜 / 分镜脚本", "把整片拆成一个个镜头的计划表，先定好再生成。"),
    "budget breaker": ("花费护栏 / 每次构建预算上限", "本次构建花费到上限就自动暂停，避免不知不觉超支。"),
    "dry-run": ("试跑 / 预演", "只算不真正生成，先看计划和预估花费再决定要不要开工。"),
}


def term(key: str) -> str:
    """The primary 中文用户词 for an engineering term (first slash-alternative).

    ``term("take") == "版本"``. Raises :class:`KeyError` on an unknown term —
    callers pass known GLOSSARY keys, so a miss is a programming error, not a
    silent fallback that could ship an English word to a user.
    """
    cn = GLOSSARY[key][0]
    return cn.split("/")[0].strip()


def tooltip_html(key: str, *, label: str | None = None) -> str:
    """Render a Chinese-first term with an accessible ``?`` help button.

    ``label`` overrides the shown Chinese word (default: :func:`term`). All text
    is escaped, so labels and explanations render verbatim even when they contain
    markup characters. The native button is keyboard-focusable without a manual
    ``tabindex``; its explanation is present in the ``aria-label`` and in a
    visible tooltip that opens on hover/focus/click and closes with Escape.
    """
    _, tip = GLOSSARY[key]
    shown = term(key) if label is None else label
    shown_e = html.escape(shown)
    en_e = html.escape(key)
    tip_e = html.escape(tip)
    return (
        '<span class="mj-term">'
        f'<span class="mj-cn">{shown_e}</span>'
        f'<span class="mj-en" aria-hidden="true"> ({en_e})</span>'
        '<span class="mj-help-wrap">'
        f'<button type="button" class="mj-help" aria-expanded="false" '
        f'aria-label="术语说明：{shown_e}。{tip_e}">?</button>'
        f'<span class="mj-tip" role="tooltip">{tip_e}</span>'
        "</span></span>"
    )


# ------------------------------------------------------------------- chrome --
# Loaded on EVERY workbench surface (the SPA + all server-rendered pages) so the
# tooltip look, the 新手/专业 mode switch and the "显示专业术语" toggle behave the
# same everywhere. Extends the board/app palette (:mod:`manju.gui.page`); adds
# nothing to :root.


def render_glossary_css() -> str:
    return _GLOSSARY_CSS


def render_glossary_js() -> str:
    return _GLOSSARY_JS


_GLOSSARY_CSS = """
/* manju gui — plain-language glossary + 新手/专业 mode chrome (round U). */

/* ---- term + ? help button (hover/focus/click/Escape; CSP-safe) -------- */
.mj-term { display: inline; white-space: nowrap; }
.mj-cn { white-space: normal; }
/* the English original is hidden until 显示专业术语 is on (a body class) */
.mj-en { display: none; color: var(--muted); font-weight: 400; font-style: normal; }
body.mj-show-terms .mj-en { display: inline; }
.mj-help-wrap {
  display: inline-flex; align-items: center; position: relative; vertical-align: middle;
}
.mj-help {
  display: inline-grid; place-items: center; min-width: 24px; width: 1.5rem;
  min-height: 24px; height: 1.5rem; margin-left: .22em; padding: 0;
  border-radius: 999px; border: 1px solid var(--line); background: var(--panel2);
  color: var(--muted); font: inherit; font-size: .72em; font-weight: 750;
  line-height: 1; cursor: help; user-select: none;
}
.mj-help:hover, .mj-help:focus, .mj-help[aria-expanded="true"] {
  color: var(--fg); border-color: var(--accent); background: var(--accent-bg); outline: none;
}
.mj-help:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.mj-tip {
  display: none; position: absolute; bottom: calc(100% + 7px); left: 50%;
  transform: translateX(-50%); z-index: 90; width: max-content;
  max-width: min(300px, calc(100vw - 1.5rem)); white-space: normal;
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 8px; padding: .5rem .68rem; font-size: .8rem; font-weight: 400;
  line-height: 1.55; box-shadow: 0 8px 24px rgba(0, 0, 0, .55);
  text-align: left; pointer-events: none;
}
.mj-help-wrap:hover .mj-tip, .mj-help-wrap:focus-within .mj-tip,
.mj-help-wrap.is-open .mj-tip { display: block; }

/* ---- nav-level controls: progressively disclosed view menu ------------ */
.mj-view-menu { position: relative; flex: 0 0 auto; }
.mj-view-menu > summary {
  list-style: none; display: inline-flex; align-items: center; gap: .35rem;
  min-height: 30px; padding: .15rem .58rem; border: 1px solid var(--line);
  border-radius: 7px; background: var(--panel2); color: var(--fg);
  font-size: .75rem; cursor: pointer; user-select: none; white-space: nowrap;
}
.mj-view-menu > summary::-webkit-details-marker { display: none; }
.mj-view-menu > summary:hover { border-color: #4b5566; }
.mj-view-menu > summary:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}
.mj-view-menu[open] > summary { border-color: var(--accent); background: var(--accent-bg); }
.mj-view-caret { color: var(--muted); font-size: .68rem; transition: transform .12s ease; }
.mj-view-menu[open] .mj-view-caret { transform: rotate(180deg); }
.mj-view-popover {
  position: absolute; top: calc(100% + 6px); right: 0; z-index: 100;
  min-width: 220px; padding: .7rem; background: var(--panel);
  border: 1px solid var(--line); border-radius: 10px;
  box-shadow: 0 12px 30px rgba(0,0,0,.52);
}
.mj-view-title {
  margin-bottom: .5rem; color: var(--muted); font-size: .7rem;
  font-weight: 750; letter-spacing: .08em;
}
.mj-nav-ctl { display: flex; flex-direction: column; gap: .65rem; }
.mj-modesw {
  display: grid; grid-template-columns: 1fr 1fr;
  border: 1px solid var(--line); border-radius: 8px; overflow: hidden;
}
.mj-mode-btn {
  background: var(--panel2); color: var(--muted); border: 0;
  min-height: 34px; padding: .28rem .75rem; font: inherit;
  font-size: .8rem; cursor: pointer;
}
.mj-mode-btn.on { background: var(--accent); color: #0b1220; font-weight: 700; }
.mj-mode-btn:hover:not(.on) { color: var(--fg); }
.mj-mode-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.mj-terms-toggle {
  display: inline-flex; align-items: center; gap: .35rem; color: var(--muted);
  min-height: 30px; font-size: .8rem; cursor: pointer;
  user-select: none; white-space: nowrap;
}
.mj-terms-toggle input { accent-color: var(--accent); }

/* ---- project switcher (round X agent XE) ------------------------------ */
.mj-ws-wrap { position: relative; display: inline-flex; }
.mj-ws-btn {
  display: inline-flex; align-items: center; gap: .35rem; min-width: 0;
  max-width: 180px; min-height: 30px; background: var(--panel2); color: var(--fg);
  border: 1px solid var(--line); border-radius: 7px; padding: .15rem .58rem;
  font: inherit; font-size: .75rem; cursor: pointer;
}
.mj-ws-btn:hover { border-color: var(--accent); }
.mj-ws-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mj-ws-caret { flex: 0 0 auto; color: var(--muted); font-size: .68rem; }
.mj-ws-menu {
  position: absolute; top: calc(100% + 4px); right: 0; z-index: 95;
  min-width: 260px; max-width: 360px; max-height: 60vh; overflow-y: auto;
  background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  box-shadow: 0 10px 28px rgba(0, 0, 0, .5); padding: .35rem;
}
.mj-ws-menu.hidden { display: none; }
.mj-ws-group-title {
  color: var(--muted); font-size: .72rem; padding: .3rem .5rem .1rem;
  text-transform: none;
}
.mj-ws-item {
  display: flex; flex-direction: column; gap: .1rem; width: 100%; text-align: left;
  background: transparent; border: 0; border-radius: 6px; color: var(--fg);
  padding: .35rem .5rem; font: inherit; font-size: .82rem; cursor: pointer;
}
.mj-ws-item:hover:not(:disabled) { background: var(--panel2); }
.mj-ws-item:disabled { color: var(--muted); cursor: default; }
.mj-ws-item-path { color: var(--muted); font-size: .72rem; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; max-width: 320px; }
.mj-ws-sep { border-top: 1px solid var(--line); margin: .3rem 0; }
.mj-ws-manage {
  display: block; width: 100%; text-align: left; background: transparent;
  border: 0; color: var(--accent); padding: .35rem .5rem; font: inherit;
  font-size: .82rem; cursor: pointer; text-decoration: none;
}
.mj-ws-manage:hover { text-decoration: underline; }

/* ---- fresh-user hint bar (dismissable) -------------------------------- */
.mj-mode-hint {
  display: flex; align-items: center; gap: .75rem; flex-wrap: wrap;
  padding: .42rem 1rem; background: color-mix(in srgb, var(--accent-bg) 72%, var(--panel));
  color: #cfe3ff; border-bottom: 1px solid var(--line); font-size: .78rem;
}
.mj-mode-hint-copy { display: inline-flex; align-items: baseline; gap: .45rem; flex-wrap: wrap; }
.mj-mode-hint-actions { margin-left: auto; display: inline-flex; align-items: center; gap: .35rem; }
.mj-mode-hint .mj-mode-hint-pro,
.mj-mode-hint #mj-mode-hint-x {
  min-height: 28px; background: transparent; border: 1px solid var(--line);
  color: inherit; border-radius: 6px; cursor: pointer; padding: .12rem .55rem;
  font: inherit; font-size: .75rem;
}
.mj-mode-hint .mj-mode-hint-pro { background: rgba(116,169,255,.12); border-color: #49699a; }
.mj-mode-hint button:hover { filter: brightness(1.18); }

@media (max-width: 760px) {
  .mj-view-menu > summary, .mj-ws-btn { min-height: 28px; padding: .12rem .45rem; }
  .mj-ws-btn { max-width: 96px; }
  .mj-view-popover { right: -3.5rem; max-width: min(260px, calc(100vw - 1.25rem)); }
  .mj-mode-hint { gap: .45rem; padding: .38rem .65rem; }
  .mj-mode-hint-copy { gap: .3rem; }
  .mj-mode-hint-actions { width: 100%; justify-content: flex-end; }
}

/* ---- pro-only panels: hidden in 新手 mode (CSS ONLY — never deleted) --- */
/* the panel stays in the DOM and one click on 专业 reveals it; project data
   is never touched, and the page is always reachable by URL. */
body.mj-mode-beginner .mj-pro-only { display: none !important; }
"""


_GLOSSARY_JS = r"""
"use strict";
/* manju gui — 新手/专业 mode switch + 显示专业术语 toggle (round U).
 * Loaded on every surface; a strict `script-src 'self'` allows it because it is
 * an external file with no inline handlers. Depends on /webclient.js +
 * /project-action.js (loaded first via GLOSSARY_HEAD). */
(function () {
  var meta = document.querySelector('meta[name="manju-token"]');
  var TOKEN = meta ? (meta.getAttribute("content") || "") : "";
  /* stale-tab guard: echo the document's project identity (absent on the
   * picker) so mode/glossary toggles can't land in a switched project. */
  var pmeta = document.querySelector('meta[name="manju-project"]');
  var PROJECT_ID = pmeta ? (pmeta.getAttribute("content") || "") : "";
  function apiOpts() {
    return (typeof manjuApiOptions === "function")
      ? manjuApiOptions({ token: TOKEN, projectId: PROJECT_ID })
      : { token: TOKEN, projectId: PROJECT_ID };
  }
  function post(url, body) {
    if (typeof requestJson === "function") {
      return requestJson("POST", url, body || {}, apiOpts());
    }
    /* fallback without webclient */
    var headers = { "Content-Type": "application/json", "X-Manju-Token": TOKEN };
    if (PROJECT_ID) headers["X-Manju-Project"] = PROJECT_ID;
    return fetch(url, {
      method: "POST",
      headers: headers,
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          var e = new Error((d && d.error) || ("HTTP " + r.status));
          e.data = d; e.status = r.status; e.code = d && d.code;
          throw e;
        }
        return d;
      });
    });
  }

  function closeHelp(except) {
    document.querySelectorAll(".mj-help-wrap.is-open").forEach(function (wrap) {
      if (wrap === except) return;
      wrap.classList.remove("is-open");
      var button = wrap.querySelector(".mj-help");
      if (button) button.setAttribute("aria-expanded", "false");
    });
  }

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var help = t.closest(".mj-help");
    if (help) {
      var wrap = help.closest(".mj-help-wrap");
      var opening = !wrap.classList.contains("is-open");
      closeHelp(opening ? wrap : null);
      wrap.classList.toggle("is-open", opening);
      help.setAttribute("aria-expanded", opening ? "true" : "false");
      return;
    }
    closeHelp(null);
    var mb = t.closest(".mj-mode-btn");
    if (mb && !mb.classList.contains("on")) {
      /* server-side render decides visibility, so flip then reload */
      post("/api/mode", { mode: mb.getAttribute("data-mode") })
        .then(function () { location.reload(); })
        .catch(function () { location.reload(); });
      return;
    }
    if (t.id === "mj-mode-hint-x") {
      var hint = document.getElementById("mj-mode-hint");
      if (hint && hint.parentNode) hint.parentNode.removeChild(hint);
      post("/api/mode", { dismiss_hint: true });
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var active = document.querySelector(".mj-help-wrap.is-open .mj-help");
    if (!active) return;
    closeHelp(null);
    active.focus();
  });

  var toggle = document.getElementById("mj-terms-toggle");
  if (toggle) {
    toggle.addEventListener("change", function () {
      /* toggle the body class live (instant), then persist the preference */
      document.body.classList.toggle("mj-show-terms", toggle.checked);
      post("/api/pro-terms", { show: toggle.checked });
    });
  }

  /* -------------------------------------------- project switcher (round X) */
  /* Opens other projects via next_action (open_in_new_window dialog) —
   * never rebinds the current session. */
  var wsBtn = document.getElementById("mj-ws-btn");
  var wsMenu = document.getElementById("mj-ws-menu");

  function wsClear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function wsItem(row) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mj-ws-item";
    var name = document.createElement("span");
    name.textContent = (row.current ? "✓ " : "") + (row.name || row.path);
    btn.appendChild(name);
    var path = document.createElement("span");
    path.className = "mj-ws-item-path";
    path.textContent = row.path;
    btn.appendChild(path);
    if (row.current) {
      btn.disabled = true;
      btn.title = "当前项目";
    } else {
      btn.addEventListener("click", function () { wsOpen(row.path); });
    }
    return btn;
  }

  function wsOpen(path) {
    if (wsMenu) wsMenu.classList.add("hidden");
    post("/api/workspace/open", { path: path })
      .then(function (resp) {
        if (typeof handleProjectAction === "function") {
          handleProjectAction(resp);
        } else if (resp && resp.next_action && resp.next_action.kind === "reload_current") {
          window.location.href = "/";
        }
      })
      .catch(function (err) {
        if (err && err.data && err.data.next_action &&
            typeof handleProjectAction === "function") {
          handleProjectAction(err.data);
          return;
        }
        /* Non-action errors only — never alert for normal new-window flows. */
        if (typeof toast === "function") {
          toast("打开项目失败：" + ((err && err.message) || String(err)), false);
        }
      });
  }

  function wsRender(data) {
    wsClear(wsMenu);
    var recents = (data && Array.isArray(data.recents)) ? data.recents : [];
    if (!recents.length) {
      var empty = document.createElement("div");
      empty.className = "mj-ws-group-title";
      empty.textContent = "还没有最近项目";
      wsMenu.appendChild(empty);
    } else {
      var seen = {};
      recents.forEach(function (row) {
        var series = row.series;
        if (series && !seen[series.root]) {
          seen[series.root] = true;
          var title = document.createElement("div");
          title.className = "mj-ws-group-title";
          title.textContent = "剧集：" + series.name;
          wsMenu.appendChild(title);
        }
        wsMenu.appendChild(wsItem(row));
      });
    }
    var sep = document.createElement("div");
    sep.className = "mj-ws-sep";
    wsMenu.appendChild(sep);
    var manage = document.createElement("a");
    manage.className = "mj-ws-manage";
    manage.href = "/?workspace=1";
    manage.textContent = "管理工作区或新建项目 →";
    wsMenu.appendChild(manage);
  }

  if (wsBtn && wsMenu) {
    wsBtn.addEventListener("click", function (ev) {
      ev.stopPropagation();
      var willOpen = wsMenu.classList.contains("hidden");
      wsMenu.classList.add("hidden");
      wsBtn.setAttribute("aria-expanded", "false");
      if (!willOpen) return;
      wsClear(wsMenu);
      var loading = document.createElement("div");
      loading.className = "mj-ws-group-title";
      loading.textContent = "正在加载…";
      wsMenu.appendChild(loading);
      wsMenu.classList.remove("hidden");
      wsBtn.setAttribute("aria-expanded", "true");
      (typeof requestJson === "function"
        ? requestJson("GET", "/api/workspace/recents", undefined, apiOpts())
        : fetch("/api/workspace/recents").then(function (r) { return r.json(); }))
        .then(wsRender)
        .catch(function () {
          wsClear(wsMenu);
          var err = document.createElement("div");
          err.className = "mj-ws-group-title";
          err.textContent = "最近项目暂时不可用";
          wsMenu.appendChild(err);
        });
    });
    document.addEventListener("click", function (ev) {
      if (wsMenu.classList.contains("hidden")) return;
      if (ev.target === wsBtn || wsMenu.contains(ev.target)) return;
      wsMenu.classList.add("hidden");
      wsBtn.setAttribute("aria-expanded", "false");
    });
  }
})();
"""
