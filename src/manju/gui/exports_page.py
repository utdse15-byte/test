"""导出中心 Export center — the finished-output status page (round-U, goal 4).

A sibling of the round-S/T server-rendered pages (:mod:`manju.gui.pages` /
:mod:`manju.gui.pages_t`), built to the exact same stance:

  * server-rendered — a plain GET carries the real deliverable rows baked into
    the DOM; ``/exports.js`` only layers on the generate/update + 标记已人工确认
    actions and the job polling;
  * CSP-safe — CSS/JS in external files, no inline handlers / ``style=``, every
    mutating POST carries the ``X-Manju-Token`` from the ``manju-token`` meta tag;
  * XSS-safe — ALL server text (paths, basis, verifier/notes) is ``html.escape``-d
    and the script only ever writes ``textContent``;
  * one core — the table is exactly :func:`manju.build.exportstatus.deliverables`,
    the SAME rows ``manju exports`` prints, so the two surfaces cannot disagree.

Row actions honor the money contract (§8.3): the priced deliverables (成片/预览版)
do NOT get a spend button — they LINK to the workbench, where the build runs
through the plan modal (dry-run → confirm). The free/local exporters
(SRT/ASS/OTIO/剪映草稿/CapCut 草稿/封面/预告) get a generate/update button routed
through the existing exporter + packaging engine; the two desktop drafts also get
a 标记已人工确认 button (with a note) that appends to reports/verifications.jsonl.
"""

from __future__ import annotations

import html
from typing import Any

__all__ = ["PAGE_PATH", "render", "render_exports_css", "render_exports_js"]

PAGE_PATH = "/exports"

# freshness slug → the app.css badge class that already carries the right colour
_BADGE_CLASS = {
    "up_to_date": "st-fresh",
    "stale": "st-stale",
    "missing": "st-missing",
    "problematic": "st-broken",
    "needs_manual": "st-needs",
    "verified": "st-manual",
}

# The priced deliverables that must NOT spend from this page — they link to the
# workbench build (plan modal → confirm), never a silent generate here.
_BUILD_ONLY = {"final", "proxy"}


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
        '<link rel="stylesheet" href="/exports.css">\n'
        '<script src="/webclient.js" defer></script>\n'
        '<script src="/common.js" defer></script>\n'
        '<script src="/exports.js" defer></script>\n'
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


def _chip(row: dict[str, Any]) -> str:
    cls = _BADGE_CLASS.get(row["freshness"], "st-missing")
    return f'<span class="badge {cls}">{_e(row["freshness_zh"])}</span>'


def _actions(row: dict[str, Any]) -> str:
    kind = row["kind"]
    if kind in _BUILD_ONLY:
        # priced: link to the workbench build (plan modal → confirm), never spend here
        return ('<a class="btn ghost mini" href="/">去工作台构建</a>'
                '<span class="muted xc-hint">计划弹窗确认花费(不在此处直接消费)</span>')
    out = [f'<button class="btn mini" data-act="gen" data-kind="{_e(kind)}">生成 / 更新</button>']
    if row["verifiable"]:
        # a desktop draft: the 标记已人工确认 note + button (§14 honesty)
        out.append(
            '<span class="xc-verify">'
            f'<input class="xc-note" type="text" maxlength="200" '
            f'data-kind="{_e(kind)}" placeholder="确认备注(可选)">'
            f'<button class="btn ghost mini" data-act="verify" data-kind="{_e(kind)}">'
            '标记已人工确认</button></span>'
        )
    return "".join(out)


def _card(row: dict[str, Any]) -> str:
    ver = f' <span class="chip xc-ver">{_e(row["version"])}</span>' if row["version"] else ""
    path = (f'<div class="xc-path muted">{_e(row["open_hint"] or row["path"])}</div>'
            if row["openable"] and (row["open_hint"] or row["path"]) else
            (f'<div class="xc-path muted">{_e(row["open_hint"])}</div>' if row["open_hint"] else ""))
    verified = ""
    if row["freshness"] == "verified" and row["verified_by"]:
        note = f" · {_e(row['verified_note'])}" if row["verified_note"] else ""
        verified = (f'<div class="xc-verified muted">✓ {_e(row["verified_by"])} '
                    f'@ {_e(str(row["verified_at"] or "")[:19])}{note}</div>')
    return (
        f'<div class="xc-card panel" data-kind="{_e(row["kind"])}">'
        f'<div class="xc-head"><h2>{_e(row["label"])}{ver}</h2>{_chip(row)}</div>'
        f'<div class="xc-basis">{_e(row["basis"])}</div>'
        f'{path}{verified}'
        f'<div class="xc-actions">{_actions(row)}</div>'
        '</div>'
    )


def render(project: Any, token: str) -> str:
    from ..build.exportstatus import deliverables_data

    head = ('<div class="page-h"><h1>导出中心 Export center</h1>'
            '<span class="muted">每个成片产物的新鲜度一览 · 桌面草稿需人工确认(§14)</span></div>')
    try:
        data = deliverables_data(project)
    except Exception as exc:  # never break the page on a status error
        return _shell("导出中心", token, head + f'<p class="err panel">{_e(exc)}</p>')

    rows = data["deliverables"]
    counts = data["counts"]
    order = ("up_to_date", "stale", "needs_manual", "verified", "problematic", "missing")
    zh = {"up_to_date": "上新", "stale": "待更新", "needs_manual": "待人工确认",
          "verified": "已人工确认", "problematic": "有问题", "missing": "缺失"}
    summary_chips = "".join(
        f'<span class="badge {_BADGE_CLASS[k]}">{zh[k]} {counts[k]}</span>'
        for k in order if counts.get(k))
    if not summary_chips:
        summary_chips = '<span class="muted">暂无产物</span>'
    # 一键更新: every STALE free/local deliverable in one click (#48b deferred
    # item, landed by the GUI polish wave). Priced kinds (final/proxy) stay
    # build-only; missing kinds stay per-card decisions — stale means "was
    # fine, upstream moved", the one state a bulk refresh cannot get wrong.
    stale_kinds = [r["kind"] for r in rows
                   if r["freshness"] == "stale" and r["kind"] not in _BUILD_ONLY]
    if stale_kinds:
        summary_chips += (
            f'<button class="btn mini" id="xc-gen-stale" '
            f'data-kinds="{_e(",".join(stale_kinds))}">'
            f'全部生成 / 更新待更新 ({len(stale_kinds)})</button>')
    summary = f'<div class="xc-summary panel">{summary_chips}</div>'

    cards = "".join(_card(r) for r in rows)
    legend = (
        '<div class="xc-legend muted panel">'
        '词汇(§3):<b>上新</b> 与当前规格一致 · <b>待更新</b> 上游已改需重做 · '
        '<b>缺失</b> 从未产出 · <b>有问题</b> 文件损坏/缺内容键 · '
        '<b>待人工确认</b> 桌面草稿 Manju 无法自证能打开 · <b>已人工确认</b> 人工确认且 hash 未变'
        '</div>'
    )
    body = head + summary + f'<div class="xc-grid">{cards}</div>' + legend
    return _shell("导出中心", token, body)


# ============================================================ assets (css/js)


def render_exports_css() -> str:
    return _EXPORTS_CSS


def render_exports_js() -> str:
    return _EXPORTS_JS


_EXPORTS_CSS = """
/* 导出中心 export center (round-U). Loaded AFTER /app.css + /pages.css; reuses
   their palette (--panel/--line/--muted…) and badge chips (.badge.st-*). */
.xc-summary { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin: .8rem 0; }
.xc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1rem; }
.xc-card { margin: 0; display: flex; flex-direction: column; gap: .5rem; }
.xc-head { display: flex; justify-content: space-between; align-items: baseline; gap: .8rem; }
.xc-head h2 { font-size: 1rem; }
.xc-ver { font-size: .74rem; }
.xc-basis { font-size: .84rem; }
.xc-path { font-size: .78rem; font-family: var(--mono); word-break: break-all; }
.xc-verified { font-size: .8rem; color: var(--ok) !important; }
.xc-actions { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; margin-top: .2rem; }
.xc-hint { font-size: .76rem; }
.xc-verify { display: inline-flex; gap: .35rem; align-items: center; flex-wrap: wrap; }
.xc-note {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .2rem .45rem; font: inherit; font-size: .8rem; width: 150px;
}
.xc-legend { font-size: .8rem; line-height: 1.7; margin-top: 1rem; }
.xc-card.busy { opacity: .6; }
"""


_EXPORTS_JS = r"""
"use strict";
(function () {

  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }

  // Poll /api/jobs until the given job finishes (generate runs on the job
  // runner). Adaptive cadence: 100ms while a quick local job usually lands
  // (~2s), then 500ms — the runner is SERIALIZED, so a generate queued behind
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
      if (job && (job.state === "done" || job.state === "failed")) return job;
      return job || null;
    }).catch(function () { return null; }).then(function (job) {
      if (job && (job.state === "done" || job.state === "failed")) return job;
      if (tries > 1215) return null;  /* alive at the cap = still running, never "failed" */  // 20×100ms + ~1195×500ms ≈ 10min
      return new Promise(function (res) {
        setTimeout(res, tries < 20 ? 100 : 500);
      }).then(function () { return pollJob(jobId, tries + 1); });
    });
  }

  // null job after the cap = polling gave up, NOT the job failing — say so.
  function jobFailText(kind, job) {
    if (!job) return kind + " 仍在排队/运行(轮询超时)— 完成后刷新本页可见";
    return job.error || (kind + " 生成失败");
  }

  function setBusy(kind, busy) {
    var card = document.querySelector('.xc-card[data-kind="' + CSS.escape(kind) + '"]');
    if (card) card.classList.toggle("busy", !!busy);
  }

  document.addEventListener("click", function (ev) {
    var all = ev.target.closest("#xc-gen-stale");
    if (all) return doGenerateAll(all);
    var btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    var act = btn.getAttribute("data-act");
    var kind = btn.getAttribute("data-kind");
    if (act === "gen") return doGenerate(kind, btn);
    if (act === "verify") return doVerify(kind, btn);
  });

  // one kind through submit+poll; resolves true only on a confirmed done.
  function generateOne(kind) {
    setBusy(kind, true);
    return post("/api/exports/generate", { kind: kind }).then(function (res) {
      if (res.status === 202 && res.data.job) {
        return pollJob(res.data.job.id).then(function (job) {
          setBusy(kind, false);
          if (job && job.state === "done") return true;
          toast(jobFailText(kind, job), false);
          return false;
        });
      }
      setBusy(kind, false);
      toast(res.data.error || (kind + " 生成失败"), false);
      return false;
    }).catch(function () { setBusy(kind, false); toast("网络错误", false); return false; });
  }

  function doGenerate(kind, btn) {
    btn.disabled = true;
    generateOne(kind).then(function (ok) {
      if (ok) { toast(kind + " 已生成 / 更新", true); reloadSoon(); }
      else { btn.disabled = false; }
    });
  }

  // 一键更新待更新: the stale free kinds, SEQUENTIALLY (the runner is
  // serialized anyway; one in flight keeps every toast attributable). Reload
  // only on full success — partial failure keeps the sticky error toasts
  // readable instead of wiping them with a refresh.
  function doGenerateAll(btn) {
    var kinds = (btn.getAttribute("data-kinds") || "").split(",").filter(Boolean);
    if (!kinds.length) return;
    var label = btn.textContent;
    btn.disabled = true;
    var okCount = 0;
    var chain = Promise.resolve();
    kinds.forEach(function (kind, i) {
      chain = chain.then(function () {
        btn.textContent = "生成中 " + (i + 1) + "/" + kinds.length + " — " + kind;
        return generateOne(kind).then(function (ok) { if (ok) okCount++; });
      });
    });
    chain.then(function () {
      if (okCount === kinds.length) {
        toast("待更新产物已全部更新(" + okCount + " 项)", true);
        reloadSoon();
      } else {
        btn.textContent = label;
        btn.disabled = false;
        toast("完成 " + okCount + "/" + kinds.length + " 项 — 失败项见上方提示", false);
      }
    });
  }

  function doVerify(kind, btn) {
    var input = document.querySelector('.xc-note[data-kind="' + CSS.escape(kind) + '"]');
    var note = input ? input.value : "";
    btn.disabled = true;
    post("/api/exports/verify", { kind: kind, note: note }).then(function (res) {
      if (res.status === 200 && res.data.ok) { toast(kind + " 已标记人工确认", true); reloadSoon(); }
      else { btn.disabled = false; toast(res.data.error || "标记失败", false); }
    }).catch(function () { btn.disabled = false; toast("网络错误", false); });
  }
})();
"""
