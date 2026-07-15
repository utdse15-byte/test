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
    its CSS-only ``?`` tooltip (hover AND keyboard-focus, CSP-safe, all text
    ``html.escape``-d);
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
    "budget breaker": ("花费护栏 / 预算上限", "花费到上限就自动暂停，避免不知不觉超支。"),
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
    """The 中文用户词 with its accessible ``?`` tooltip, as one inline HTML span.

    ``label`` overrides the shown Chinese word (default: :func:`term`). ALL text
    is ``html.escape``-d — the tooltip and label render verbatim even if a caller
    passes a word containing ``<>&``. The ``?`` badge is keyboard-focusable
    (``tabindex="0"``, ``role="button"``) and carries the explanation in its
    ``aria-label``; the popup shows on ``:hover`` and ``:focus`` via CSS alone,
    so this is CSP-safe (no inline handler, no inline style).
    """
    cn, tip = GLOSSARY[key]
    shown = term(key) if label is None else label
    shown_e = html.escape(shown)
    en_e = html.escape(key)
    tip_e = html.escape(tip)
    return (
        '<span class="mj-term">'
        f'<span class="mj-cn">{shown_e}</span>'
        f'<span class="mj-en" aria-hidden="true"> ({en_e})</span>'
        f'<span class="mj-help" tabindex="0" role="button" '
        f'aria-label="{shown_e}：{tip_e}">?'
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

/* ---- term + ? tooltip (CSS-only hover/focus; CSP-safe) ---------------- */
.mj-term { display: inline; white-space: nowrap; }
.mj-cn { white-space: normal; }
/* the English original is hidden until 显示专业术语 is on (a body class) */
.mj-en { display: none; color: var(--muted); font-weight: 400; font-style: normal; }
body.mj-show-terms .mj-en { display: inline; }
.mj-help {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1.15em; height: 1.15em; margin-left: .2em; border-radius: 999px;
  border: 1px solid var(--line); background: var(--panel2); color: var(--muted);
  font-size: .7em; font-weight: 700; line-height: 1; cursor: help;
  position: relative; vertical-align: middle; user-select: none;
}
.mj-help:hover, .mj-help:focus { color: var(--fg); border-color: var(--accent); outline: none; }
.mj-help:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.mj-tip {
  display: none; position: absolute; bottom: calc(100% + 6px); left: 50%;
  transform: translateX(-50%); z-index: 90; width: max-content; max-width: 260px;
  white-space: normal; background: var(--panel2); color: var(--fg);
  border: 1px solid var(--line); border-radius: 8px; padding: .45rem .65rem;
  font-size: .8rem; font-weight: 400; line-height: 1.55;
  box-shadow: 0 8px 24px rgba(0, 0, 0, .55); text-align: left; pointer-events: none;
}
.mj-help:hover .mj-tip, .mj-help:focus .mj-tip,
.mj-help:focus-within .mj-tip { display: block; }

/* ---- nav-level controls: mode switch + 显示专业术语 toggle ------------- */
.mj-nav-ctl {
  display: inline-flex; align-items: center; gap: .7rem;
  margin-left: auto; flex-wrap: wrap;
}
.mj-modesw {
  display: inline-flex; border: 1px solid var(--line); border-radius: 999px;
  overflow: hidden;
}
.mj-mode-btn {
  background: var(--panel2); color: var(--muted); border: 0;
  padding: .18rem .75rem; font: inherit; font-size: .8rem; cursor: pointer;
}
.mj-mode-btn.on { background: var(--accent); color: #0b1220; font-weight: 700; }
.mj-mode-btn:hover:not(.on) { color: var(--fg); }
.mj-mode-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.mj-terms-toggle {
  display: inline-flex; align-items: center; gap: .35rem; color: var(--muted);
  font-size: .8rem; cursor: pointer; user-select: none; white-space: nowrap;
}
.mj-terms-toggle input { accent-color: var(--accent); }

/* ---- project switcher (round X agent XE) ------------------------------ */
.mj-ws-wrap { position: relative; display: inline-flex; }
.mj-ws-btn {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .18rem .7rem; font: inherit; font-size: .8rem;
  cursor: pointer;
}
.mj-ws-btn:hover { border-color: var(--accent); }
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
  display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
  padding: .4rem 1.2rem; background: var(--accent-bg); color: #cfe3ff;
  border-bottom: 1px solid var(--line); font-size: .82rem;
}
.mj-mode-hint button {
  margin-left: auto; background: transparent; border: 1px solid var(--line);
  color: inherit; border-radius: 6px; cursor: pointer; padding: .05rem .6rem;
  font: inherit; font-size: .8rem;
}
.mj-mode-hint button:hover { filter: brightness(1.25); }

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

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
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
      btn.title = "当前项目 (current project)";
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
      empty.textContent = "还没有最近项目 (no recent projects)";
      wsMenu.appendChild(empty);
    } else {
      var seen = {};
      recents.forEach(function (row) {
        var series = row.series;
        if (series && !seen[series.root]) {
          seen[series.root] = true;
          var title = document.createElement("div");
          title.className = "mj-ws-group-title";
          title.textContent = "剧集 (series): " + series.name;
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
    manage.textContent = "管理工作区 / 新建项目 (manage workspace) →";
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
      loading.textContent = "加载中 (loading)…";
      wsMenu.appendChild(loading);
      wsMenu.classList.remove("hidden");
      wsBtn.setAttribute("aria-expanded", "true");
      fetch("/api/workspace/recents")
        .then(function (r) { return r.json(); })
        .then(wsRender)
        .catch(function () {
          wsClear(wsMenu);
          var err = document.createElement("div");
          err.className = "mj-ws-group-title";
          err.textContent = "最近项目不可用 (recents unavailable)";
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
