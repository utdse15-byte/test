"""Shared GUI JSON client (classic script globals).

Served as ``/webclient.js`` and included before page-specific scripts so every
surface uses one request layer: parse JSON always, throw :class:`ManjuApiError`
with the full server body on non-2xx (never drop ``code`` / ``next_action`` /
``cli`` by converting to a bare ``Error`` string).
"""

from __future__ import annotations

__all__ = ["render_webclient_js"]

# Classic script (no IIFE): globals for glossary / workspace / common / app.
WEBCLIENT_JS = r"""
"use strict";
/* manju gui — shared JSON request layer. CSP-safe external file. */
class ManjuApiError extends Error {
  constructor(status, data, path) {
    var d = data || {};
    super(d.error ? d.error : ("HTTP " + status + (path ? (": " + path) : "")));
    this.name = "ManjuApiError";
    this.status = status;
    this.code = d.code || "";
    this.data = d;
    this.path = path || "";
  }
}

class ManjuProtocolError extends Error {
  constructor(message, path, raw) {
    super(message || "服务器返回了无效 JSON");
    this.name = "ManjuProtocolError";
    this.path = path || "";
    this.raw_response = raw || "";
  }
}

/**
 * @param {string} method
 * @param {string} path
 * @param {object|undefined} body
 * @param {{token?: string, projectId?: string}} options
 * @returns {Promise<object>}
 */
async function requestJson(method, path, body, options) {
  var opts = options || {};
  var headers = {
    "Accept": "application/json",
    "X-Manju-Token": opts.token || ""
  };
  if (opts.projectId) {
    headers["X-Manju-Project"] = opts.projectId;
  }
  var init = {
    method: method,
    headers: headers,
    credentials: "same-origin",
    cache: "no-store"
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  var response = await fetch(path, init);
  var data = {};
  if (response.status === 204) {
    data = {};
  } else {
    var text = "";
    try { text = await response.text(); } catch (e) { text = ""; }
    if (text && text.trim()) {
      try {
        data = JSON.parse(text);
      } catch (e) {
        if (response.ok) {
          throw new ManjuProtocolError(
            "服务器返回了无效 JSON", path, text.slice(0, 1000));
        }
        data = { error: "服务器返回了无效 JSON", raw_response: text.slice(0, 1000) };
      }
    } else {
      data = {};
    }
  }
  if (!response.ok) {
    throw new ManjuApiError(response.status, data, path);
  }
  return data || {};
}

/** Default options from the document's meta tags (token + project identity). */
function manjuApiOptions(extra) {
  var meta = document.querySelector('meta[name="manju-token"]');
  var pmeta = document.querySelector('meta[name="manju-project"]');
  var o = {
    token: meta ? (meta.getAttribute("content") || "") : "",
    projectId: pmeta ? (pmeta.getAttribute("content") || "") : ""
  };
  if (extra) {
    if (extra.token !== undefined) o.token = extra.token;
    if (extra.projectId !== undefined) o.projectId = extra.projectId;
  }
  return o;
}
"""


def render_webclient_js() -> str:
    return WEBCLIENT_JS
