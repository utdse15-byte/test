"""One shared definition of the server-rendered pages' JS helpers (audit G5).

Before this, the token-reader + ``post`` + ``toast`` were byte-copied into ten
server-rendered page modules (pages, pages_t, lab, storyboard, create, director,
exports, ingest, series, edit) — ~7 KB of identical JS duplicated per page. They
are now defined ONCE here and served as ``/common.js`` (``defer``, injected
BEFORE each page's own ``<script>`` in that page's shell). Because a classic
deferred script runs in document order, these top-level ``var``/``function``
bindings are GLOBAL and already installed when the page's own IIFE runs, so each
page simply DROPPED its local copies and reads the globals through the scope
chain — no call sites changed.

Not shared here: ``pollJob`` (its body genuinely diverges across lab/exports/
ingest/series and edit uses a different ``(id, done)`` signature) and the
glossary/workspace ``post`` (a different return contract — raw fetch / throw on
error). Those stay in their own modules. The ``toast`` auto-dismiss delay, which
varied cosmetically (3400/3600/4000 ms), is unified to 3600 ms.

The GUI has no byte-golden on any rendered JS (audit negative result), so this is
behaviour-pinned by the full GUI test set, not a golden.
"""

from __future__ import annotations

# NOTE: keep this a CLASSIC script (no wrapping IIFE) so TOKEN/post/toast are
# GLOBAL — the page IIFEs that dropped their local copies rely on that.
COMMON_JS = r"""
"use strict";
// audit G5 — the shared server-rendered-page helpers, defined ONCE. Served as
// /common.js (defer) before each page's own script, so a page's own IIFE reads
// these globals instead of re-defining the byte-identical block.
var META = document.querySelector('meta[name="manju-token"]');
var TOKEN = META ? META.getAttribute("content") : "";
/* GPT-analysis wave (stale-tab guard): the identity of the project this
 * DOCUMENT was rendered for. Echoed back on every mutating POST as
 * X-Manju-Project — the server refuses a mismatch (409 project_switched)
 * so a tab left open across a project switch can never write into the
 * wrong project. Empty (picker / older pages) = guard off, old behaviour. */
var PROJ_META = document.querySelector('meta[name="manju-project"]');
var PROJECT = PROJ_META ? (PROJ_META.getAttribute("content") || "") : "";

/* Full-page block once THIS tab's project is no longer the bound one —
 * reading on is as dangerous as writing (media URLs resolve in the NEW
 * project). Refresh adopts the server's current project. */
function projectSwitchedOverlay(name) {
  if (document.getElementById("mj-proj-switched")) return;
  var ov = document.createElement("div");
  ov.id = "mj-proj-switched";
  ov.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(15,18,24,.92);" +
    "color:#fff;display:flex;flex-direction:column;align-items:center;" +
    "justify-content:center;gap:1rem;text-align:center;padding:2rem";
  var msg = document.createElement("div");
  msg.style.cssText = "font-size:1.05rem;max-width:34em";
  msg.textContent = name
    ? ("服务器已切换到项目「" + name + "」— 本页属于另一个项目,已停止读写。")
    : "服务器已切换/关闭项目 — 本页属于另一个项目,已停止读写。";
  var btn = document.createElement("button");
  btn.textContent = "刷新,跟随当前项目 (reload)";
  btn.style.cssText = "font-size:1rem;padding:.5em 1.2em;cursor:pointer";
  btn.addEventListener("click", function () { location.reload(); });
  ov.appendChild(msg);
  ov.appendChild(btn);
  document.body.appendChild(ov);
}

function post(url, body) {
  var headers = { "Content-Type": "application/json", "X-Manju-Token": TOKEN };
  if (PROJECT) headers["X-Manju-Project"] = PROJECT;
  return fetch(url, {
    method: "POST",
    headers: headers,
    body: JSON.stringify(body || {})
  }).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (d) {
      if (r.status === 409 && d && d.code === "project_switched") {
        projectSwitchedOverlay(d.project);
      }
      return { status: r.status, data: d };
    });
  });
}

/* The read-side watchdog: mutating POSTs are refused server-side, but an
 * already-rendered page keeps SHOWING (and its <video> tags keep fetching)
 * relative media paths that now resolve inside the newly bound project.
 * /api/project-id is a constant-time read; 15 s keeps the window small
 * without adding real load. Errors are ignored — a dead server is the
 * browser's problem to report, not this watchdog's. */
if (PROJECT) {
  setInterval(function () {
    fetch("/api/project-id").then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && typeof d.token === "string" && d.token !== PROJECT) {
          projectSwitchedOverlay(d.name);
        }
      })
      .catch(function () {});
  }, 15000);
}

function toast(msg, ok) {
  var t = document.getElementById("toast");
  if (!t) return;
  var el = document.createElement("div");
  el.className = "toast-item " + (ok === false ? "bad" : "good");
  el.textContent = msg;
  /* UX audit F20: errors are often long engine sentences — 3.6 s was not
   * enough to read one, and nothing durable remained. Errors now stay until
   * clicked (dismiss affordance); successes keep the quick auto-dismiss. */
  if (ok === false) {
    el.textContent = msg + "  ✕";
    el.style.cursor = "pointer";
    el.addEventListener("click", function () { el.remove(); });
  } else {
    setTimeout(function () { el.remove(); }, 3600);
  }
  t.appendChild(el);
}
"""


def render_common_js() -> str:
    return COMMON_JS
