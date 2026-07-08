"""批量入库 Batch ingest — the GUI twin of `manju ingest` (round X, workflow
smoothness goal #1: "a batch of assets processed externally and imported back
into Manju feels cumbersome").

A human drops a folder of externally-produced files (regenerated takes, TTS
lines, reference stills) onto this page; the server classifies each one by
filename convention (:mod:`manju.build.ingest`) into a target step, the human
reviews/edits the classification per row, and confirms — landing every row
through the SAME registration paths `manju ingest --apply` and the CLI's
single-file `manju import` use. Nothing moves until the confirm step.

Stance (mirrors :mod:`manju.gui.exports_page` / :mod:`manju.gui.lab_page`):
own dispatch region, server-rendered shell (the table itself is populated by
``/ingest.js`` after upload since there is nothing to show before that),
CSP-safe (CSS/JS external, no inline handlers, every mutating POST carries
``X-Manju-Token``), XSS-safe (the script only ever writes textContent /
builds DOM nodes — see below), one core (`build.ingest.plan_ingest` /
`apply_ingest`, the exact functions the CLI calls).

Upload lands in a per-batch DISPOSABLE staging directory under
``.manju/ingest-tmp/<batch>/`` (a browser has no filesystem access to hand
the engine a real directory path the way the trusted-machine CLI door can) —
``plan_ingest`` is then run against that staging directory exactly as it
would be against a directory argument on the CLI. The staging directory is
removed once ``apply_ingest`` has run (success or partial failure): its
bytes are only ever a COPY source, never itself a project truth location.
"""

from __future__ import annotations

import html
from typing import Any

__all__ = ["PAGE_PATH", "render", "render_ingest_css", "render_ingest_js"]

PAGE_PATH = "/ingest"


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _shell(title: str, token: str, body: str) -> str:
    from .pages import nav_html

    return (
        "<!doctype html>\n"
        '<html lang="zh">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/ingest.css">\n'
        '<script src="/ingest.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{PAGE_PATH}">\n'
        + nav_html(PAGE_PATH)
        + "\n<main>\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


def render(project: Any, token: str) -> str:
    head = (
        '<div class="page-h"><h1>批量入库 Batch ingest</h1>'
        '<span class="muted">把外部产出的一批素材(镜头 take、配音、参考图)按文件名约定'
        "一次性归档 · 先预演,人工确认后再落地(§3 素材只增不改)</span></div>"
    )
    body = (
        head
        + '<div class="ing-panel panel">'
        '<div class="ing-controls">'
        '<label class="btn ing-file-label">选择文件(可多选)'
        '<input type="file" id="ing-files" multiple hidden></label>'
        '<label class="ing-field">角色 '
        '<select id="ing-role">'
        '<option value="auto">自动识别</option>'
        '<option value="take">take(视频)</option>'
        '<option value="voice">配音(音频)</option>'
        '<option value="ref">参考图</option>'
        "</select></label>"
        '<label class="ing-field">强制镜头 '
        '<input type="text" id="ing-shot" placeholder="如 S001,留空则按文件名识别"></label>'
        '<button type="button" class="btn" id="ing-plan-btn" disabled>重新生成计划</button>'
        '<button type="button" class="btn ghost" id="ing-reset-btn">开始新一批</button>'
        "</div>"
        '<div id="ing-filelist" class="ing-filelist muted"></div>'
        "</div>"
        '<div id="ing-table-wrap"></div>'
        '<div class="ing-legend muted panel">'
        "词汇:<b>take</b> 同一镜头的一个候选版本(生成/导入都追加,不覆盖已有 take) · "
        "<b>配音 take</b> 人工配音,像手动拖入的音频一样永不自动失效(§4.3) · "
        "<b>参考图</b> 复制进 media/refs 供后续生成引用 · "
        "<b>普通导入</b> 未匹配到镜头/角色约定,同 `manju import` 落进 media/imports · "
        "<b>跳过(重复)</b> 内容已存在于项目里,素材只增不改(§3)"
        "</div>"
    )
    return _shell("批量入库", token, body)


# ============================================================ assets (css/js)


def render_ingest_css() -> str:
    return _INGEST_CSS


def render_ingest_js() -> str:
    return _INGEST_JS


_INGEST_CSS = """
/* 批量入库 batch ingest (round X). Loaded AFTER /app.css + /pages.css; reuses
   their palette (--panel/--panel2/--line/--fg/--mono/--ok/--err…). */
.ing-panel { display: flex; flex-direction: column; gap: .5rem; }
.ing-controls { display: flex; flex-wrap: wrap; gap: .7rem; align-items: center; }
.ing-file-label { cursor: pointer; }
.ing-field { display: inline-flex; gap: .35rem; align-items: center; font-size: .84rem; }
.ing-field input[type="text"], .ing-field select {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .2rem .45rem; font: inherit; font-size: .84rem;
}
.ing-filelist { min-height: 1.2rem; font-size: .82rem; }
.ing-table { width: 100%; border-collapse: collapse; margin-top: .6rem; font-size: .84rem; }
.ing-table th, .ing-table td { padding: .35rem .5rem; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
.ing-name { font-family: var(--mono); font-size: .8rem; word-break: break-all; }
.ing-reason { max-width: 380px; }
.ing-id, .ing-action {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .2rem .4rem; font: inherit; font-size: .82rem;
}
.ing-id { width: 120px; }
.ing-confirm { margin-top: .8rem; }
.ing-status { font-family: var(--mono); font-size: .82rem; }
.ing-status.ing-ok { color: var(--ok); }
.ing-status.ing-bad { color: var(--err); }
.ing-legend { font-size: .8rem; line-height: 1.7; margin-top: 1rem; }
"""


_INGEST_JS = r"""
"use strict";
(function () {
  if (document.body.getAttribute("data-page") !== "/ingest") return;

  var META = document.querySelector('meta[name="manju-token"]');
  var TOKEN = META ? META.getAttribute("content") : "";
  var batchId = null;

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
  function pollJob(jobId, tries) {
    tries = tries || 0;
    return fetch("/api/jobs").then(function (r) { return r.json(); }).then(function (d) {
      var job = (d.jobs || []).filter(function (j) { return j.id === jobId; })[0];
      if (job && (job.state === "done" || job.state === "failed")) return job;
      if (tries > 600) return job || null;
      return new Promise(function (res) { setTimeout(res, 100); }).then(function () {
        return pollJob(jobId, tries + 1);
      });
    });
  }
  function newBatchId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  var ACTION_LABELS = {
    take: "take(新)", voice: "配音 take(新)", shot_ref: "镜头参考图",
    bible_ref: "角色/场景/道具参考图", import: "普通导入", skip_duplicate: "跳过(重复)"
  };
  var ACTION_ORDER = ["take", "voice", "shot_ref", "bible_ref", "import", "skip_duplicate"];

  function idFor(row) { return row.shot_id || row.asset_id || ""; }

  function renderTable(plan) {
    var wrap = document.getElementById("ing-table-wrap");
    wrap.innerHTML = "";
    if (!plan.rows || !plan.rows.length) return;
    var table = document.createElement("table");
    table.className = "ing-table";
    var thead = document.createElement("thead");
    var htr = document.createElement("tr");
    ["文件", "动作", "目标 id", "原因", "状态"].forEach(function (h) {
      var th = document.createElement("th");
      th.textContent = h;
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    plan.rows.forEach(function (row, i) {
      var tr = document.createElement("tr");
      tr.setAttribute("data-idx", String(i));

      var tdName = document.createElement("td");
      tdName.textContent = row.name;
      tdName.className = "ing-name";
      tr.appendChild(tdName);

      var tdAction = document.createElement("td");
      var sel = document.createElement("select");
      sel.className = "ing-action";
      ACTION_ORDER.forEach(function (a) {
        var opt = document.createElement("option");
        opt.value = a;
        opt.textContent = ACTION_LABELS[a] || a;
        if (a === row.action) opt.selected = true;
        sel.appendChild(opt);
      });
      tdAction.appendChild(sel);
      tr.appendChild(tdAction);

      var tdId = document.createElement("td");
      var idInput = document.createElement("input");
      idInput.type = "text";
      idInput.className = "ing-id";
      idInput.value = idFor(row);
      idInput.placeholder = "镜头/资产 id";
      tdId.appendChild(idInput);
      tr.appendChild(tdId);

      var tdReason = document.createElement("td");
      tdReason.textContent = row.reason;
      tdReason.className = "ing-reason muted";
      tr.appendChild(tdReason);

      var tdStatus = document.createElement("td");
      tdStatus.className = "ing-status";
      tr.appendChild(tdStatus);

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);

    var confirmRow = document.createElement("div");
    confirmRow.className = "ing-confirm";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn";
    btn.id = "ing-apply-btn";
    btn.textContent = "确认导入(" + plan.rows.length + " 项)";
    btn.addEventListener("click", doApply);
    confirmRow.appendChild(btn);
    wrap.appendChild(confirmRow);
  }

  function collectOverrides() {
    var overrides = {};
    var rows = document.querySelectorAll("#ing-table-wrap tbody tr");
    Array.prototype.forEach.call(rows, function (tr) {
      var idx = tr.getAttribute("data-idx");
      var action = tr.querySelector(".ing-action").value;
      var id = tr.querySelector(".ing-id").value.trim();
      overrides[idx] = { action: action, id: id };
    });
    return overrides;
  }

  function currentRoleShot() {
    var role = document.getElementById("ing-role").value;
    var shot = document.getElementById("ing-shot").value.trim();
    return { role: role, shot: shot || null };
  }

  function doPlan() {
    var rs = currentRoleShot();
    post("/api/ingest/plan", { batch: batchId, role: rs.role, shot: rs.shot }).then(function (res) {
      if (res.status === 200) {
        renderTable(res.data);
        toast("计划已生成 · " + (res.data.rows || []).length + " 项", true);
      } else {
        toast(res.data.error || "生成计划失败", false);
      }
    });
  }

  function paintStatus(results) {
    (results || []).forEach(function (r, i) {
      var tr = document.querySelector('#ing-table-wrap tbody tr[data-idx="' + i + '"]');
      if (!tr) return;
      var cell = tr.querySelector(".ing-status");
      if (!cell) return;
      cell.textContent = r.ok ? "✓" : ("✗ " + (r.error || ""));
      cell.className = "ing-status " + (r.ok ? "ing-ok" : "ing-bad");
    });
  }

  function doApply() {
    var rs = currentRoleShot();
    var overrides = collectOverrides();
    var btn = document.getElementById("ing-apply-btn");
    if (btn) btn.disabled = true;
    post("/api/ingest/apply",
      { batch: batchId, role: rs.role, shot: rs.shot, overrides: overrides }
    ).then(function (res) {
      if (res.status !== 202 || !res.data.job) {
        if (btn) btn.disabled = false;
        toast((res.data && res.data.error) || "入库失败", false);
        return;
      }
      pollJob(res.data.job.id).then(function (job) {
        if (btn) btn.disabled = false;
        if (!job) { toast("入库超时", false); return; }
        var result = job.result || {};
        paintStatus(result.results);
        if (job.state === "done" && (result.stopped_at === null || result.stopped_at === undefined)) {
          toast("批量入库完成 · " + (result.results || []).length + " 项", true);
        } else if (job.state === "done") {
          toast("入库在第 " + (result.stopped_at + 1) + " 行停止,已落地的部分见上方 ✓", false);
        } else {
          toast(job.error || "入库失败", false);
        }
      });
    });
  }

  function resetBatch() {
    batchId = newBatchId();
    var list = document.getElementById("ing-filelist");
    if (list) list.textContent = "";
    var wrap = document.getElementById("ing-table-wrap");
    if (wrap) wrap.innerHTML = "";
    var planBtn = document.getElementById("ing-plan-btn");
    if (planBtn) planBtn.disabled = true;
    var input = document.getElementById("ing-files");
    if (input) input.value = "";
  }

  function uploadAll(files) {
    var list = document.getElementById("ing-filelist");
    var uploaded = 0;
    var total = files.length;
    var chain = Promise.resolve();
    Array.prototype.forEach.call(files, function (f) {
      chain = chain.then(function () {
        var url = "/api/ingest/upload?batch=" + encodeURIComponent(batchId)
          + "&name=" + encodeURIComponent(f.name);
        return fetch(url, { method: "POST", headers: { "X-Manju-Token": TOKEN }, body: f })
          .then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (d) {
              return { status: r.status, data: d };
            });
          }).then(function (res) {
            if (res.status === 200) {
              uploaded += 1;
              if (list) list.textContent = "已上传 " + uploaded + " / " + total;
            } else {
              toast((res.data && res.data.error) || (f.name + " 上传失败"), false);
            }
          });
      });
    });
    chain.then(function () {
      var planBtn = document.getElementById("ing-plan-btn");
      if (planBtn) planBtn.disabled = uploaded === 0;
      if (uploaded > 0) doPlan();
    });
  }

  resetBatch();
  var fileInput = document.getElementById("ing-files");
  if (fileInput) fileInput.addEventListener("change", function () {
    if (fileInput.files && fileInput.files.length) uploadAll(fileInput.files);
  });
  var planBtn2 = document.getElementById("ing-plan-btn");
  if (planBtn2) planBtn2.addEventListener("click", doPlan);
  var resetBtn = document.getElementById("ing-reset-btn");
  if (resetBtn) resetBtn.addEventListener("click", resetBatch);
})();
"""
