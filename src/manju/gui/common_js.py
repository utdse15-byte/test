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

UX-WAVE-3 adds the global task bar: jobs belong to the APP window, not to the
page that submitted them, but every server-rendered page only polled the job it
had itself just started — navigate away and a running build became invisible
until you found its page again. The bar (bottom-right, absent when idle) polls
``/api/jobs``, shows active jobs with 取消, and when a job it observed active
finishes it leaves a short-lived chip with the kind's follow-up link (审片/导出
中心/…). The SPA home does NOT load common.js — its queue panel + hidden-tab
notifications (UX-WAVE-2) already cover the home surface, so no double bar.

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
  /* Prefer shared requestJson (preserves full error JSON); keep {status,data}
   * return shape for existing server-rendered pages.
   * R2-P0-3: must preserve real HTTP status (202 Accepted for jobs) — never
   * hardcode 200 or lab/export/ingest/edit all treat success as failure. */
  if (typeof requestJson === "function") {
    return requestJson("POST", url, body || {}, {
      token: TOKEN, projectId: PROJECT || "",
      returnStatus: true
    }).then(function (r) {
      /* requestJson with returnStatus → {status, data}; plain → body only. */
      if (r && typeof r === "object" && "data" in r && "status" in r) {
        return { status: r.status, data: r.data };
      }
      return { status: 200, data: r };
    }).catch(function (err) {
      var d = (err && err.data) || {};
      var status = (err && err.status) || 0;
      if (status === 409 && d && d.code === "project_switched") {
        projectSwitchedOverlay(d.project);
      }
      return { status: status, data: d };
    });
  }
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
    var p = (typeof requestJson === "function")
      ? requestJson("GET", "/api/project-id", undefined, { token: TOKEN || "" })
      : fetch("/api/project-id").then(function (r) { return r.json(); });
    p.then(function (d) {
        if (d && typeof d.token === "string" && d.token !== PROJECT) {
          projectSwitchedOverlay(d.name);
        }
      })
      .catch(function () {});
  }, 15000);
}

/* Direction program (#50): remember where work happened, per project — the
 * workbench home renders its 继续上次工作 chip from this. Server pages only
 * (the home page itself must never clobber yesterday's trail). */
if (PROJECT && location.pathname !== "/") {
  try {
    window.localStorage.setItem("manju-last-" + PROJECT, JSON.stringify({
      page: location.pathname,
      title: (document.title || "").split(" ·")[0],
      ts: Date.now()
    }));
  } catch (e) { /* best-effort */ }
}

/* Product Polish R1 Wave 4: the five finishing pages share one journey bar.
 * The page shell paints immediately with honest "正在检查" placeholders; this
 * single read then fills them from /api/finishing/status.  No polling and no
 * client inference: exportstatus/readiness remain the owners. */
(function initFinishingJourney() {
  var root = document.querySelector("[data-finishing-journey]");
  if (!root) return;
  var finalChip = root.querySelector("[data-finish-final]");
  var lockChip = root.querySelector("[data-finish-lock]");
  var note = root.querySelector("[data-finish-note]");
  var statusClasses = ["st-fresh", "st-stale", "st-missing", "st-broken", "st-needs", "st-manual"];

  function setChip(node, data, fallback) {
    if (!node) return;
    statusClasses.forEach(function (cls) { node.classList.remove(cls); });
    var cls = data && data["class"];
    node.classList.add(statusClasses.indexOf(cls) >= 0 ? cls : "st-missing");
    node.textContent = (data && data.label) || fallback;
    var title = (data && (data.basis || (data.reasons || []).join("；"))) || "";
    node.title = title;
  }

  var opts = (typeof manjuApiOptions === "function") ? manjuApiOptions() : {};
  var req = (typeof requestJson === "function")
    ? requestJson("GET", "/api/finishing/status", undefined, opts)
    : fetch("/api/finishing/status").then(function (r) { return r.json(); });
  req.then(function (data) {
    var finalData = (data || {}).final || {};
    var lockData = (data || {}).picture_lock || {};
    setChip(finalChip, finalData, "成片状态未知");
    setChip(lockChip, lockData, "锁片资格未知");
    if (!note) return;
    note.classList.remove("warn", "bad");
    var message = "状态来自当前项目与媒体证据；这里只展示，不会替你锁片或改项目。";
    if (finalData.state === "problematic") {
      note.classList.add("bad");
      message = finalData.basis || "当前成片有问题，请先查看导出中心的证据。";
    } else if (finalData.state === "stale") {
      note.classList.add("warn");
      message = finalData.basis || "上游内容已变化，当前成片需要重新构建。";
    } else if (finalData.state === "missing") {
      message = "尚无成片；完成当前阶段后可在导出页或工作台进行构建。";
    } else if (!lockData.eligible && lockData.summary) {
      message = lockData.summary;
    }
    note.textContent = message;
  }).catch(function () {
    setChip(finalChip, null, "成片状态暂不可用");
    setChip(lockChip, null, "锁片资格暂不可用");
    if (note) note.textContent = "状态检查失败，不影响当前页面编辑；稍后刷新即可重试。";
  });
})();

/* ---- 全局任务条 UX-WAVE-3 ------------------------------------------------
 * Read-only presentation over the existing runner — no new task system. The
 * only client state is "which job ids were seen active on THIS page". Kind
 * labels come lazily from /api/meta/job-kinds (the one registry — never a
 * hard-coded kind→label copy); until it resolves the raw kind shows. Node
 * styling is per-element cssText (CSSOM) because the pages' CSP carries
 * style-src 'self' — a JS-injected <style> would be blocked. The 中文 state
 * words mirror the SPA's local STATE_ZH (a fixed runner enum, not registry
 * metadata). Polling pauses while the tab is hidden; the SPA owns the
 * hidden-tab Web Notification channel (UX-WAVE-2), not this bar. */
var MJ_TASKBAR_STATE_ZH = {
  queued: "排队中", running: "运行中", canceling: "取消中",
  canceled: "已取消", done: "完成", failed: "失败"
};
/* kind → [href, 中文] follow-up for "任务完成后直接给出下一步" — a
 * presentation choice, so the map lives here in the GUI layer. */
var MJ_TASKBAR_NEXT = {
  build: ["/exports", "查看成片"],
  redo: ["/review", "去审片"],
  redo_batch: ["/review", "去审片"],
  voice: ["/review", "去审片"],
  voice_batch: ["/review", "去审片"],
  qc: ["/review", "查看质检"],
  repair: ["/review", "去审片"],
  export: ["/exports", "打开导出中心"],
  ingest_plan: ["/ingest", "查看导入计划"],
  ingest: ["/storyboard", "查看分镜"],
  handle_rebuild: ["/edit", "回剪辑"],
  roundtrip: ["/edit", "回剪辑"],
  edit_preview: ["/edit", "回剪辑"],
  edit_preview_batch: ["/edit", "回剪辑"]
};

(function () {
  if (!PROJECT) return;   /* unbound page (picker) — no runner to poll */
  var POLL_MS = 5000;
  var seenActive = Object.create(null);   /* id → true: observed active HERE */
  var doneChips = [];                     /* {job, until} short-lived closers */
  var kindLabels = null, kindLoading = false;
  var bar = null;
  var lastActive = [];
  var first = true;   /* first poll only SEEDS seenActive — jobs that finished
                         before this page opened must not "complete" now */

  function loadKindLabels() {
    if (kindLabels !== null || kindLoading) return;
    kindLoading = true;
    var p = (typeof requestJson === "function")
      ? requestJson("GET", "/api/meta/job-kinds", undefined, { token: TOKEN || "" })
      : fetch("/api/meta/job-kinds").then(function (r) { return r.json(); });
    p.then(function (d) {
      var map = {};
      (((d || {}).job_kinds) || []).forEach(function (s) {
        if (s && s.kind) map[s.kind] = s.display_name_zh || s.kind;
      });
      kindLabels = map;
    }).catch(function () { kindLabels = {}; });
  }

  function ensureBar() {
    if (bar && document.body.contains(bar)) return bar;
    bar = document.createElement("div");
    bar.id = "mj-taskbar";
    bar.style.cssText = "position:fixed;right:.8rem;bottom:.8rem;z-index:800;" +
      "display:flex;flex-direction:column;gap:.35rem;align-items:flex-end;" +
      "font-size:.82rem;max-width:26rem;pointer-events:none";
    document.body.appendChild(bar);
    return bar;
  }

  function rowShell() {
    var row = document.createElement("div");
    row.style.cssText = "display:flex;align-items:center;gap:.5rem;" +
      "background:var(--panel2, #1d222b);border:1px solid var(--line, #39404d);" +
      "border-radius:8px;padding:.3rem .6rem;box-shadow:0 2px 8px rgba(0,0,0,.35);" +
      "color:var(--fg, #e8eaf0);pointer-events:auto;max-width:100%";
    return row;
  }

  function chipBtn(label, title) {
    var b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    if (title) b.title = title;
    b.style.cssText = "font-size:.78rem;cursor:pointer;background:transparent;" +
      "border:1px solid var(--line, #39404d);border-radius:6px;" +
      "color:inherit;padding:.1rem .5rem";
    return b;
  }

  function mjSpan(text, css) {
    var s = document.createElement("span");
    s.textContent = text;
    if (css) s.style.cssText = css;
    return s;
  }

  function jobLabel(j) {
    var kind = (kindLabels || {})[j.kind] || j.kind || "任务";
    var extra = "";
    if (j.params && typeof j.params.shot === "string") extra = " " + j.params.shot;
    else if (j.params && typeof j.params.lang === "string") extra = " " + j.params.lang;
    return kind + extra;
  }

  function renderTaskbar() {
    var host = ensureBar();
    while (host.firstChild) host.removeChild(host.firstChild);
    var now = Date.now();
    doneChips = doneChips.filter(function (c) { return c.until > now; });
    if (!lastActive.length && !doneChips.length) {
      host.style.display = "none";
      return;
    }
    host.style.display = "flex";

    doneChips.forEach(function (c) {
      var j = c.job;
      var row = rowShell();
      var waiting = !!(j.result && j.result.waiting_user === true);
      var failed = j.state === "failed";
      var mark = failed ? "✗" : (waiting ? "⏸" : (j.state === "canceled" ? "◌" : "✓"));
      row.appendChild(mjSpan(
        mark + " " + jobLabel(j) + " · "
          + (waiting ? "待确认花费" : (MJ_TASKBAR_STATE_ZH[j.state] || j.state)),
        failed ? "color:var(--err, #ff7b72)" : ""));
      if (failed && j.error) {
        var e = mjSpan(String(j.error).slice(0, 90),
          "color:var(--muted, #98a1b3);max-width:14rem;overflow:hidden;" +
          "text-overflow:ellipsis;white-space:nowrap");
        e.title = String(j.error);
        row.appendChild(e);
      }
      /* 完成后的下一步: waiting_user outranks the kind map — the spend plan
       * needs CONFIRMING back on the home panel, not admiring. Failed gets
       * 重试, not a link. A link to the page we are already on is noise. */
      var next = waiting ? ["/", "查看计划,确认"] : (failed ? null : MJ_TASKBAR_NEXT[j.kind]);
      if (next && location.pathname !== next[0]) {
        var a = document.createElement("a");
        a.href = next[0];
        a.textContent = next[1] + " →";
        a.style.cssText = "color:var(--accent, #6ea8fe);text-decoration:none";
        row.appendChild(a);
      }
      if (failed && j.retryable) {
        var rb = chipBtn("重试", "重新提交同一任务 (retry)");
        rb.addEventListener("click", function () {
          rb.disabled = true;
          post("/api/jobs/retry", { job_id: j.id }).then(function (r) {
            if (r.status >= 400) {
              toast("重试失败: " + ((r.data || {}).error || r.status), false);
              rb.disabled = false;
            } else { c.until = 0; mjTaskbarTick(); }
          });
        });
        row.appendChild(rb);
      }
      var x = chipBtn("✕", "关闭 (dismiss)");
      x.addEventListener("click", function () { c.until = 0; renderTaskbar(); });
      row.appendChild(x);
      host.appendChild(row);
    });

    lastActive.forEach(function (j) {
      var row = rowShell();
      var txt = jobLabel(j) + " · " + (MJ_TASKBAR_STATE_ZH[j.state] || j.state)
        + (j.progress ? " · " + j.progress : "");
      row.appendChild(mjSpan((j.state === "running" ? "⏳ " : "⌛ ") + txt));
      if (j.cancelable) {
        var cb = chipBtn("取消", "取消该任务 (cancel)");
        cb.addEventListener("click", function () {
          cb.disabled = true;
          post("/api/jobs/cancel", { job_id: j.id }).then(function (r) {
            if (r.status >= 400) {
              toast("取消失败: " + ((r.data || {}).error || r.status), false);
              cb.disabled = false;
            } else mjTaskbarTick();
          });
        });
        row.appendChild(cb);
      }
      host.appendChild(row);
    });
  }

  function handleJobs(jobs) {
    var active = [];
    jobs.forEach(function (j) {
      if (!j || !j.id) return;
      if (j.state === "queued" || j.state === "running" || j.state === "canceling") {
        active.push(j);
        seenActive[j.id] = true;
        return;
      }
      if (first) return;   /* pre-existing history: never announce */
      if (seenActive[j.id]
          && (j.state === "done" || j.state === "failed" || j.state === "canceled")) {
        delete seenActive[j.id];
        var sticky = j.state === "failed"
          || !!(j.result && j.result.waiting_user === true);
        /* failed / 待确认花费 need ACTION — they stay (10 min or ✕);
         * plain completions self-dismiss after 12 s. */
        doneChips.push({ job: j, until: Date.now() + (sticky ? 600000 : 12000) });
      }
    });
    first = false;
    if (active.length || doneChips.length) loadKindLabels();
    lastActive = active;
    renderTaskbar();
  }

  function mjTaskbarTick() {
    if (document.hidden) return;
    var p = (typeof requestJson === "function")
      ? requestJson("GET", "/api/jobs", undefined, { token: TOKEN || "" })
      : fetch("/api/jobs").then(function (r) { return r.json(); });
    p.then(function (d) { handleJobs((d && d.jobs) || []); })
     .catch(function () { /* dead server is the browser's problem */ });
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) mjTaskbarTick();
  });
  setInterval(mjTaskbarTick, POLL_MS);
  mjTaskbarTick();
})();

function toast(msg, ok) {
  var t = document.getElementById("toast");
  if (!t) return;
  if (!t.hasAttribute("aria-live")) {
    /* announce state changes to assistive tech without stealing focus */
    t.setAttribute("role", "status");
    t.setAttribute("aria-live", "polite");
  }
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
