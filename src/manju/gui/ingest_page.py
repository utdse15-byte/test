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


def _shell(title: str, token: str, body: str, project: Any) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    nav, bcls = chrome(PAGE_PATH, project)

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
        + GLOSSARY_HEAD
        + '<script src="/common.js" defer></script>\n'
        + '<script src="/ingest.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{PAGE_PATH}" class="{bcls}">\n'
        + nav
        + "\n<main>\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


def render(
    project: Any,
    token: str,
    query: dict[str, list[str]] | None = None,
) -> str:
    from .shot_journey import shot_journey_html

    query = query or {}
    requested_role = (query.get("role") or ["auto"])[0]
    role = requested_role if requested_role in {"auto", "take", "voice", "ref"} else "auto"
    requested_shot = (query.get("shot") or [""])[0]
    try:
        shot = requested_shot if requested_shot in set(project.shot_ids()) else ""
    except Exception:
        shot = ""

    head = (
        '<div class="page-h"><h1>批量入库<span class="mj-en" aria-hidden="true"> (Batch ingest)</span></h1>'
        '<span class="muted">把外部生成、拍摄或配音素材安全加入项目；先预演匹配，确认后只追加、不覆盖。</span></div>'
    )
    journey = shot_journey_html(PAGE_PATH, project, shot_id=shot or None)
    role_options = "".join(
        f'<option value="{_e(value)}"' + (' selected' if value == role else '') + f'>{_e(label)}</option>'
        for value, label in (
            ("auto", "自动识别"),
            ("take", "视频候选"),
            ("voice", "配音"),
            ("ref", "参考图"),
        )
    )
    preset_note = (
        f'<p class="ing-preset muted">已预设为 <b>{_e(shot)}</b> 的'
        f'{"视频候选" if role == "take" else "素材"}；入库前仍会显示完整匹配计划。</p>'
        if shot else ""
    )
    body = (
        head
        + journey
        + '<div class="ing-panel panel">'
        '<div class="ing-primary-row">'
        '<label class="btn ing-file-label">选择文件<span class="mj-en" aria-hidden="true"> (Multiple)</span>'
        '<input type="file" id="ing-files" multiple hidden></label>'
        '<button type="button" class="btn" id="ing-plan-btn" disabled>生成入库计划</button>'
        '<button type="button" class="btn ghost" id="ing-reset-btn">开始新一批</button>'
        '</div>'
        '<details class="ing-match-settings"' + (' open' if shot or role != "auto" else '') + '>'
        '<summary>匹配设置</summary>'
        '<div class="ing-controls">'
        '<label class="ing-field">素材类型 <select id="ing-role">'
        + role_options
        + '</select></label>'
        '<label class="ing-field">目标镜头 '
        f'<input type="text" id="ing-shot" value="{_e(shot)}" placeholder="如 S001；留空则按文件名识别"></label>'
        '</div></details>'
        + preset_note
        + '<div id="ing-filelist" class="ing-filelist muted"></div>'
        '</div>'
        '<div id="ing-table-wrap"></div>'
        '<details class="ing-legend panel">'
        '<summary>文件命名和入库规则</summary>'
        '<div class="muted">'
        '<b>视频候选</b> 会追加为镜头的新版本，不覆盖旧版本；'
        '<b>配音</b> 会追加为人工音频；<b>参考图</b> 会复制进参考素材；'
        '<b>普通导入</b> 用于未匹配到镜头或设定的文件；重复内容会安全跳过。'
        '<span class="mj-en" aria-hidden="true"> Take, voice, reference and plain-import classifications remain available in the technical plan.</span>'
        '</div></details>'
        + _review_section_html()
    )
    return _shell("批量入库", token, body, project)


def _review_section_html() -> str:
    """批次评审 batch review (round AA6, goal item 4): ONE view over every
    item a landed batch added — match/staged/review state, note, per-row
    confirm/flag/discard/查看, a "全部确认已匹配" batch action — so reviewing
    a batch never means opening every shot it touched one at a time. Own
    section on the SAME /ingest page (not a new page framework): the batch
    selector defaults to nothing selected until `/api/ingest/batches`
    answers (see ingest.js's `loadBatches`), same "nothing to show before
    the fetch" stance the plan/apply table above already takes."""
    return (
        '<div class="ing-review panel" id="ing-review">'
        '<div class="ing-rv-head"><div><h2>批次评审<span class="mj-en" aria-hidden="true"> (Batch review)</span></h2>'
        '<p class="muted">检查刚刚加入的素材；确认匹配不会自动选择镜头候选。</p></div>'
        '<label class="ing-field">批次 <select id="ing-rv-batch" disabled></select></label>'
        '<button type="button" class="btn ghost" id="ing-rv-refresh">刷新批次列表</button>'
        '<button type="button" class="btn" id="ing-rv-confirm-all" disabled>全部确认已匹配</button>'
        "</div>"
        '<div id="ing-rv-filters" class="ing-rv-filters"></div>'
        '<div id="ing-rv-table-wrap"><div class="ing-review-empty muted">正在读取已有批次…</div></div>'
        "</div>"
    )


# ============================================================ assets (css/js)


def render_ingest_css() -> str:
    return _INGEST_CSS


def render_ingest_js() -> str:
    return _INGEST_JS


_INGEST_CSS = """
/* 批量入库 batch ingest (round X). Loaded AFTER /app.css + /pages.css; reuses
   their palette (--panel/--panel2/--line/--fg/--mono/--ok/--err…). */
.ing-panel { display: flex; flex-direction: column; gap: .65rem; }
.ing-primary-row, .ing-controls { display: flex; flex-wrap: wrap; gap: .55rem; align-items: center; }
.ing-primary-row { padding-bottom: .1rem; }
.ing-match-settings { border-top: 1px solid var(--line); padding-top: .5rem; }
.ing-match-settings > summary { cursor: pointer; color: var(--fg); font-size: .82rem; font-weight: 650; }
.ing-match-settings[open] > summary { margin-bottom: .5rem; }
.ing-preset { margin: 0; font-size: .78rem; }
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
.ing-legend > summary { cursor: pointer; color: var(--fg); font-weight: 650; }
.ing-legend > div { margin-top: .45rem; }
.ing-after-actions { display: flex; justify-content: flex-end; gap: .45rem; flex-wrap: wrap; margin-top: .65rem; }

@media (max-width: 760px) {
  .ing-primary-row > .btn, .ing-file-label { flex: 1 1 auto; text-align: center; }
  .ing-controls { align-items: stretch; }
  .ing-field { width: 100%; justify-content: space-between; }
  .ing-field input[type="text"], .ing-field select { flex: 1 1 auto; min-width: 0; }
  .ing-after-actions > .btn { flex: 1 1 auto; text-align: center; }
}

/* 批次评审 batch review (round AA6) — .badge/.st-*/.filter-chip/.btn.mini all
   reused as-is from /app.css + /pages.css (loaded before this sheet), never
   redefined here; only this section's own layout is new. */
.ing-review { margin-top: 1.2rem; display: flex; flex-direction: column; gap: .6rem; }
.ing-review-empty { padding: .65rem 0; }
.ing-rv-head > div:first-child { min-width: min(28rem, 100%); }
.ing-rv-head > div:first-child p { margin: .15rem 0 0; font-size: .78rem; }
.ing-rv-head { display: flex; flex-wrap: wrap; gap: .7rem; align-items: center; }
.ing-rv-head h2 { margin: 0; font-size: 1rem; }
.ing-rv-head select {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .2rem .5rem; font: inherit; font-size: .84rem; min-width: 260px;
}
.ing-rv-filters { display: flex; flex-direction: column; gap: .3rem; }
.ing-rv-frow { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; font-size: .8rem; }
.ing-rv-table { width: 100%; border-collapse: collapse; font-size: .84rem; }
.ing-rv-table th, .ing-rv-table td {
  padding: .35rem .5rem; border-bottom: 1px solid var(--line); text-align: left; vertical-align: middle;
}
.ing-rv-thumb img {
  width: 64px; height: 40px; object-fit: cover; border-radius: 4px; display: block; background: var(--panel2);
}
.ing-rv-note { max-width: 220px; word-break: break-word; }
.ing-rv-actions { display: flex; gap: .3rem; flex-wrap: wrap; }
#ing-view-batch-link { margin-left: .6rem; }
"""


_INGEST_JS = r"""
"use strict";
(function () {
  if (document.body.getAttribute("data-page") !== "/ingest") return;

  var batchId = null;

  // Poll /api/jobs until the given job finishes (plan/apply run on the job
  // runner). Adaptive cadence: 100ms while a quick local job usually lands
  // (~2s), then 500ms — the runner is SERIALIZED, so an ingest queued behind
  // a long build legitimately takes minutes; the old ~60s cap misreported it
  // as a failure. ~10min cap; a transient fetch error retries, never rejects.
  function pollJob(jobId, tries) {
    tries = tries || 0;
    var opts = (typeof manjuApiOptions === "function") ? manjuApiOptions() : {};
    var p = (typeof requestJson === "function")
      ? requestJson("GET", "/api/jobs", undefined, opts)
      : fetch("/api/jobs").then(function (r) { return r.json(); });
    return p.then(function (d) {
      var job = ((d && d.jobs) || []).filter(function (j) { return j.id === jobId; })[0];
      if (job && (job.state === "done" || job.state === "failed" || job.state === "canceled" || job.state === "interrupted")) return job;
      return job || null;
    }).catch(function () { return null; }).then(function (job) {
      if (job && (job.state === "done" || job.state === "failed" || job.state === "canceled" || job.state === "interrupted")) return job;
      if (tries > 1215) return null;  /* alive at the cap = still running, never "failed" */  // 20×100ms + ~1195×500ms ≈ 10min
      return new Promise(function (res) {
        setTimeout(res, tries < 20 ? 100 : 500);
      }).then(function () { return pollJob(jobId, tries + 1); });
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
    var planBtn = document.getElementById("ing-plan-btn");
    if (planBtn) planBtn.disabled = true;
    // round AA4: /api/ingest/plan now hashes the batch on the jobs runner
    // (a large batch of big video files is genuinely multi-second) — submit
    // + poll, same shape as doApply() below.
    post("/api/ingest/plan", { batch: batchId, role: rs.role, shot: rs.shot }).then(function (res) {
      if (!(res.status === 202 || res.status === 200) || !res.data || !res.data.job) {
        if (planBtn) planBtn.disabled = false;
        toast((res.data && res.data.error) || "生成计划失败", false);
        return;
      }
      pollJob(res.data.job.id).then(function (job) {
        if (planBtn) planBtn.disabled = false;
        if (!job) { toast("计划生成任务仍在排队/运行(轮询超时)— 完成后刷新本页可见", false); return; }
        if (job.state === "done") {
          var plan = job.result || {};
          renderTable(plan);
          if (plan.canceled) {
            toast((plan.errors && plan.errors[0]) || "计划已取消", false);
          } else {
            toast("计划已生成 · " + (plan.rows || []).length + " 项", true);
          }
        } else {
          toast(job.error || "生成计划失败", false);
        }
      });
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
      if (!(res.status === 202 || res.status === 200) || !res.data || !res.data.job) {
        if (btn) btn.disabled = false;
        toast((res.data && res.data.error) || "入库失败", false);
        return;
      }
      pollJob(res.data.job.id).then(function (job) {
        if (btn) btn.disabled = false;
        if (!job) { toast("批量入库任务仍在排队/运行(轮询超时)— 完成后刷新本页可见", false); return; }
        var result = job.result || {};
        paintStatus(result.results);
        if (job.state === "done" && (result.stopped_at === null || result.stopped_at === undefined)) {
          toast("批量入库完成 · " + (result.results || []).length + " 项", true);
        } else if (job.state === "done") {
          toast("入库在第 " + (result.stopped_at + 1) + " 行停止,已落地的部分见上方 ✓", false);
        } else {
          toast(job.error || "入库失败", false);
        }
        // round AA6: apply's job result now carries batch_id — surface a
        // direct way into the review view instead of making the reviewer
        // hunt for the batch they just landed in the selector below.
        if (job.state === "done" && result.batch_id) {
          var landedTakes = (result.results || []).filter(function (item) {
            return item && item.ok && item.row && item.row.action === "take";
          });
          var firstShot = landedTakes.length && landedTakes[0].row
            ? landedTakes[0].row.shot_id : "";
          showReviewLink(result.batch_id, landedTakes.length > 0, firstShot || "");
        }
      });
    });
  }

  function showReviewLink(batchIdToView, hasTakes, firstShot) {
    var wrap = document.getElementById("ing-table-wrap");
    if (!wrap) return;
    var existing = document.getElementById("ing-after-actions");
    if (existing) existing.remove();
    var actions = document.createElement("div");
    actions.id = "ing-after-actions";
    actions.className = "ing-after-actions";
    var a = document.createElement("a");
    a.id = "ing-view-batch-link";
    a.href = "#ing-review";
    a.className = "btn ghost";
    a.textContent = "检查本批次";
    a.addEventListener("click", function (e) {
      e.preventDefault();
      loadBatches(batchIdToView);
      var section = document.getElementById("ing-review");
      if (section) section.scrollIntoView({ behavior: "smooth" });
    });
    actions.appendChild(a);
    if (hasTakes) {
      var review = document.createElement("a");
      review.className = "btn";
      review.href = "/review" + (firstShot ? ("?shot=" + encodeURIComponent(firstShot)) : "");
      review.textContent = "去审片";
      actions.appendChild(review);
    }
    wrap.appendChild(actions);
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
        var headers = { "X-Manju-Token": TOKEN };
        if (typeof PROJECT === "string" && PROJECT) headers["X-Manju-Project"] = PROJECT;
        return fetch(url, { method: "POST", headers: headers, body: f })
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

  // ======================================================================
  // 批次评审 BATCH REVIEW (round AA6, goal item 4): ONE view over every item
  // a landed batch added, with per-row confirm/flag/discard/查看 and a
  // "全部确认已匹配" batch action. Lives on the same /ingest page (own
  // section, #ing-review) — no new page framework, see ingest_page.py's
  // _review_section_html().
  // ======================================================================

  var REVIEW_MATCH_STATES = ["matched", "pending", "unmatched", "conflict", "manual"];
  var REVIEW_STATES = ["pending", "confirmed", "flagged", "discarded", "auto"];
  var MATCH_BADGE = {
    matched: "st-fresh", pending: "st-stale", conflict: "st-needs",
    unmatched: "st-missing", manual: "st-manual"
  };
  var MATCH_LABEL = {
    matched: "已匹配", pending: "待核实", conflict: "冲突", unmatched: "未匹配", manual: "人工指定"
  };
  var REVIEW_BADGE = {
    pending: "st-stale", confirmed: "st-fresh", flagged: "st-needs",
    discarded: "st-broken", auto: "st-manual"
  };
  var REVIEW_LABEL = {
    pending: "待评审", confirmed: "已确认", flagged: "已标记", discarded: "已丢弃", auto: "自动(去重)"
  };

  var rvItems = [];
  var rvBatchSelected = false;
  var rvMatchFilter = "all";
  var rvReviewFilter = "all";

  function rvCountsLabel(counts) {
    counts = counts || {};
    var parts = [];
    REVIEW_STATES.forEach(function (s) {
      if (counts[s]) parts.push((REVIEW_LABEL[s] || s) + " " + counts[s]);
    });
    return parts.length ? parts.join(" · ") : "无条目";
  }

  function loadBatches(preferId) {
    var sel = document.getElementById("ing-rv-batch");
    if (!sel) return Promise.resolve();
    return (typeof requestJson==="function"?requestJson("GET","/api/ingest/batches",undefined,typeof manjuApiOptions==="function"?manjuApiOptions():{}):fetch("/api/ingest/batches").then(function(r){return r.json();})).then(function (d) {
      var batches = d.batches || [];
      var confirmAll = document.getElementById("ing-rv-confirm-all");
      sel.innerHTML = "";
      sel.disabled = batches.length === 0;
      if (confirmAll) confirmAll.disabled = batches.length === 0;
      batches.forEach(function (b) {
        var opt = document.createElement("option");
        opt.value = b.batch;
        opt.textContent = b.batch + "(" + b.items + " 项 · " + rvCountsLabel(b.counts) + ")";
        sel.appendChild(opt);
      });
      var have = batches.some(function (b) { return b.batch === preferId; });
      var want = (preferId && have) ? preferId : (batches[0] ? batches[0].batch : null);
      if (want) {
        sel.value = want;
        return loadBatchDetail(want);
      }
      rvBatchSelected = false;
      rvItems = [];
      renderReviewFilters();
      renderReviewTable();
    }).catch(function () {
      var confirmAll = document.getElementById("ing-rv-confirm-all");
      sel.disabled = true;
      if (confirmAll) confirmAll.disabled = true;
      rvBatchSelected = false;
      rvItems = [];
      renderReviewFilters();
      var wrap = document.getElementById("ing-rv-table-wrap");
      if (wrap) {
        wrap.textContent = "暂时无法读取入库批次。请检查本地服务后重试。";
        wrap.classList.add("muted");
      }
    });
  }

  function loadBatchDetail(batchIdToLoad) {
    if (!batchIdToLoad) return Promise.resolve();
    var opts = (typeof manjuApiOptions === "function") ? manjuApiOptions() : {};
    opts = Object.assign({}, opts, { returnStatus: true });
    var p = (typeof requestJson === "function")
      ? requestJson("GET", "/api/ingest/batch?id=" + encodeURIComponent(batchIdToLoad), undefined, opts)
          .then(function (r) {
            if (r && typeof r === "object" && "data" in r && "status" in r) {
              return { status: r.status, data: r.data };
            }
            return { status: 200, data: r };
          })
          .catch(function (err) {
            return { status: (err && err.status) || 500, data: (err && err.data) || {} };
          })
      : fetch("/api/ingest/batch?id=" + encodeURIComponent(batchIdToLoad)).then(function (r) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            return { status: r.status, data: d };
          });
        });
    return p.then(function (res) {
      if (res.status !== 200) {
        toast((res.data && res.data.error) || "加载批次失败", false);
        return;
      }
      rvBatchSelected = true;
      rvItems = res.data.items || [];
      renderReviewFilters();
      renderReviewTable();
    });
  }

  function rvFilterGroup(label, values, active, labelMap, onPick) {
    var row = document.createElement("div");
    row.className = "ing-rv-frow";
    var lbl = document.createElement("span");
    lbl.className = "muted";
    lbl.textContent = label + ":";
    row.appendChild(lbl);
    var all = document.createElement("button");
    all.type = "button";
    all.className = "filter-chip" + (active === "all" ? " active" : "");
    all.textContent = "全部";
    all.addEventListener("click", function () { onPick("all"); });
    row.appendChild(all);
    values.forEach(function (v) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "filter-chip" + (v === active ? " active" : "");
      btn.textContent = labelMap[v] || v;
      btn.addEventListener("click", function () { onPick(v); });
      row.appendChild(btn);
    });
    return row;
  }

  function renderReviewFilters() {
    var wrap = document.getElementById("ing-rv-filters");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!rvItems.length) return;
    wrap.appendChild(rvFilterGroup("匹配状态", REVIEW_MATCH_STATES, rvMatchFilter, MATCH_LABEL,
      function (v) { rvMatchFilter = v; renderReviewFilters(); renderReviewTable(); }));
    wrap.appendChild(rvFilterGroup("评审状态", REVIEW_STATES, rvReviewFilter, REVIEW_LABEL,
      function (v) { rvReviewFilter = v; renderReviewFilters(); renderReviewTable(); }));
  }

  function rvActionBtn(label, act) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn mini";
    btn.setAttribute("data-act", act);
    btn.textContent = label;
    return btn;
  }

  function renderReviewTable() {
    var wrap = document.getElementById("ing-rv-table-wrap");
    if (!wrap) return;
    wrap.innerHTML = "";
    var items = rvItems.filter(function (it) {
      if (rvMatchFilter !== "all" && it.match !== rvMatchFilter) return false;
      if (rvReviewFilter !== "all" && it.review !== rvReviewFilter) return false;
      return true;
    });
    if (!items.length) {
      var empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = rvItems.length ? "没有符合筛选条件的条目"
        : (rvBatchSelected ? "这个批次没有可评审条目。"
          : "还没有可评审的入库批次。完成一次入库后会显示在这里。");
      wrap.appendChild(empty);
      return;
    }
    var table = document.createElement("table");
    table.className = "ing-rv-table";
    var thead = document.createElement("thead");
    var htr = document.createElement("tr");
    ["预览", "文件", "动作", "目标", "匹配", "历史选择", "评审", "备注", "操作"].forEach(function (h) {
      var th = document.createElement("th");
      th.textContent = h;
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    items.forEach(function (it) {
      var tr = document.createElement("tr");
      tr.setAttribute("data-index", String(it.index));

      var tdThumb = document.createElement("td");
      tdThumb.className = "ing-rv-thumb";
      if (it.thumb_url) {
        var img = document.createElement("img");
        img.src = it.thumb_url;
        img.alt = "";
        img.loading = "lazy";
        tdThumb.appendChild(img);
      }
      tr.appendChild(tdThumb);

      var tdName = document.createElement("td");
      tdName.className = "ing-name";
      tdName.textContent = it.name || "";
      tr.appendChild(tdName);

      var tdAction = document.createElement("td");
      tdAction.textContent = ACTION_LABELS[it.action] || it.action || "";
      tr.appendChild(tdAction);

      var tdTarget = document.createElement("td");
      var targetLabel = it.shot_id || it.asset_id || "—";
      if (it.link) {
        var a = document.createElement("a");
        a.href = it.link;
        a.textContent = targetLabel;
        tdTarget.appendChild(a);
      } else {
        tdTarget.textContent = targetLabel;
      }
      tr.appendChild(tdTarget);

      var tdMatch = document.createElement("td");
      var matchBadge = document.createElement("span");
      matchBadge.className = "badge " + (MATCH_BADGE[it.match] || "st-missing");
      matchBadge.textContent = MATCH_LABEL[it.match] || it.match || "";
      tdMatch.appendChild(matchBadge);
      tr.appendChild(tdMatch);

      var tdStaged = document.createElement("td");
      if (it.staged) {
        var stagedBadge = document.createElement("span");
        stagedBadge.className = "badge st-manual";
        stagedBadge.textContent = "历史自动选择";
        stagedBadge.title = "仅用于兼容旧批次记录；当前入库不会自动选择候选";
        tdStaged.appendChild(stagedBadge);
      }
      tr.appendChild(tdStaged);

      var tdReview = document.createElement("td");
      tdReview.className = "ing-rv-review";
      var reviewBadge = document.createElement("span");
      reviewBadge.className = "badge " + (REVIEW_BADGE[it.review] || "st-missing");
      reviewBadge.textContent = REVIEW_LABEL[it.review] || it.review || "";
      tdReview.appendChild(reviewBadge);
      tr.appendChild(tdReview);

      var tdNote = document.createElement("td");
      tdNote.className = "ing-rv-note muted";
      tdNote.textContent = it.note || "";
      tr.appendChild(tdNote);

      var tdActions = document.createElement("td");
      tdActions.className = "ing-rv-actions";
      tdActions.appendChild(rvActionBtn("确认", "confirm"));
      tdActions.appendChild(rvActionBtn("标记问题", "flag"));
      tdActions.appendChild(rvActionBtn("丢弃", "discard"));
      tdActions.appendChild(rvActionBtn("查看", "view"));
      tr.appendChild(tdActions);

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
  }

  function doReview(index, decision, note) {
    var sel = document.getElementById("ing-rv-batch");
    var batchIdInView = sel ? sel.value : null;
    if (!batchIdInView) return;
    post("/api/ingest/batch/review",
      { batch: batchIdInView, index: index, decision: decision, note: note }
    ).then(function (res) {
      if (res.status !== 200) {
        toast((res.data && res.data.error) || "操作失败", false);
        return;
      }
      var updated = res.data.item;
      var i = rvItems.findIndex(function (it) { return it.index === index; });
      if (i >= 0 && updated) {
        // merge: keep the enriched preview_url/thumb_url/link this GET-only
        // response never carries, only review/note (and everything else,
        // unchanged) come from the server's fresh copy.
        var merged = {};
        Object.keys(rvItems[i]).forEach(function (k) { merged[k] = rvItems[i][k]; });
        Object.keys(updated).forEach(function (k) { merged[k] = updated[k]; });
        rvItems[i] = merged;
      }
      var verb = decision === "confirm" ? "已确认" : decision === "flag" ? "已标记" : "已丢弃";
      toast(res.data.undo ? (verb + "(" + res.data.undo + ")") : verb, true);
      renderReviewTable();
      loadBatches(batchIdInView);  // refresh the selector's per-state counts
    });
  }

  function onReviewTableClick(e) {
    var btn = e.target.closest("button[data-act]");
    if (!btn) return;
    var tr = btn.closest("tr[data-index]");
    if (!tr) return;
    var index = parseInt(tr.getAttribute("data-index"), 10);
    var item = rvItems.filter(function (it) { return it.index === index; })[0];
    if (!item) return;
    var act = btn.getAttribute("data-act");
    if (act === "view") {
      if (item.preview_url) window.open(item.preview_url, "_blank", "noopener");
      else toast("该条目没有可预览的素材", false);
      return;
    }
    if (act === "confirm") {
      doReview(index, "confirm", "");
      return;
    }
    if (act === "flag") {
      var flagNote = window.prompt("标记问题 " + (item.name || "") + "(说明原因,可留空):", item.note || "");
      if (flagNote === null) return;
      doReview(index, "flag", flagNote);
      return;
    }
    if (act === "discard") {
      var msg = "确定丢弃「" + (item.name || "") + "」的评审结果?\n\n"
        + "历史批次可能曾自动选择过候选；只有在镜头此后没有被重新选择的情况下，"
        + "系统才会一并撤销那次历史选择。已落地的素材本身不会被删除。";
      if (!window.confirm(msg)) return;
      var discardNote = window.prompt("备注(可留空):", item.note || "");
      if (discardNote === null) return;
      doReview(index, "discard", discardNote);
      return;
    }
  }

  function doConfirmAllMatched() {
    var sel = document.getElementById("ing-rv-batch");
    var batchIdInView = sel ? sel.value : null;
    if (!batchIdInView) { toast("请先选择一个批次", false); return; }
    var btn = document.getElementById("ing-rv-confirm-all");
    if (btn) btn.disabled = true;
    post("/api/ingest/batch/confirm-matched", { batch: batchIdInView }).then(function (res) {
      if (btn) btn.disabled = false;
      if (res.status !== 200) {
        toast((res.data && res.data.error) || "操作失败", false);
        return;
      }
      toast("已确认 " + res.data.confirmed + " 项已匹配条目", true);
      loadBatches(batchIdInView);
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

  var rvBatchSel = document.getElementById("ing-rv-batch");
  if (rvBatchSel) rvBatchSel.addEventListener("change", function () {
    loadBatchDetail(rvBatchSel.value);
  });
  var rvRefreshBtn = document.getElementById("ing-rv-refresh");
  if (rvRefreshBtn) rvRefreshBtn.addEventListener("click", function () {
    loadBatches(rvBatchSel ? rvBatchSel.value : null);
  });
  var rvConfirmAllBtn = document.getElementById("ing-rv-confirm-all");
  if (rvConfirmAllBtn) rvConfirmAllBtn.addEventListener("click", doConfirmAllMatched);
  var rvTableWrap = document.getElementById("ing-rv-table-wrap");
  if (rvTableWrap) rvTableWrap.addEventListener("click", onReviewTableClick);
  loadBatches(null);
})();
"""
