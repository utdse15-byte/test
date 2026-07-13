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
"""


def render_common_js() -> str:
    return COMMON_JS
