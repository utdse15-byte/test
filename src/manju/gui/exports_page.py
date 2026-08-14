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

from .a11y import HTML_LANG, NOSCRIPT_HTML, SKIP_LINK_HTML, main_open

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

_FINISHING_BADGE = {
    "ready": ("st-fresh", "可安全回收"),
    "carrier_missing": ("st-missing", "尚未导出"),
    "baseline_missing": ("st-needs", "仅可单向使用"),
    "baseline_corrupt": ("st-broken", "基线有问题"),
    "carrier_problematic": ("st-broken", "文件有问题"),
}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _shell(title: str, token: str, body: str, project: Any = None) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    nav, bcls = chrome(PAGE_PATH, project)

    return (
        "<!doctype html>\n"
        f'<html lang="{HTML_LANG}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/exports.css">\n'
        + GLOSSARY_HEAD
        + '<script src="/common.js" defer></script>\n'
        + '<script src="/exports.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{PAGE_PATH}" class="{bcls}">\n'
        + SKIP_LINK_HTML + nav
        + "\n" + main_open() + "\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        + NOSCRIPT_HTML + "\n"
        + "</body>\n</html>\n"
    )


def _chip(row: dict[str, Any]) -> str:
    cls = _BADGE_CLASS.get(row["freshness"], "st-missing")
    return f'<span class="badge {cls}">{_e(row["freshness_zh"])}</span>'


def _actions(row: dict[str, Any], *, readonly: bool = False) -> str:
    kind = row["kind"]
    if kind in _BUILD_ONLY:
        # priced: link to the workbench build (plan modal → confirm), never spend here
        return ('<a class="btn ghost mini" href="/">去工作台构建</a>'
                '<span class="muted xc-hint">计划弹窗确认花费(不在此处直接消费)</span>')
    # TRISURFACE F-10: the server already 403s every readonly write — but the
    # page still rendered 15 inviting buttons, each a click that could only
    # fail. Disable them at render like the cockpit gates its controls.
    dis = ' disabled title="只读工作台 readonly"' if readonly else ""
    out = [f'<button class="btn mini" data-act="gen" data-kind="{_e(kind)}"{dis}>'
           '生成 / 更新</button>']
    if row["verifiable"]:
        # a desktop draft: the 标记已人工确认 note + button (§14 honesty)
        out.append(
            '<span class="xc-verify">'
            f'<input class="xc-note" type="text" maxlength="200" '
            f'data-kind="{_e(kind)}" placeholder="确认备注(可选)"{dis}>'
            f'<button class="btn ghost mini" data-act="verify" data-kind="{_e(kind)}"{dis}>'
            '标记已人工确认</button></span>'
        )
    return "".join(out)


def _card(row: dict[str, Any], *, readonly: bool = False) -> str:
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
        f'<div class="xc-actions">{_actions(row, readonly=readonly)}</div>'
        '</div>'
    )


def _finishing_card(row: dict[str, Any], *, readonly: bool = False) -> str:
    cls, label = _FINISHING_BADGE.get(
        str(row.get("state") or ""), ("st-needs", "需要确认"))
    path = (
        f'<div class="xc-path muted">文件：{_e(row["path"])}</div>'
        if row.get("path") else ""
    )
    baseline = (
        f'<div class="xc-path muted">回程基线：{_e(row["baseline"])}</div>'
        if row.get("baseline") else ""
    )
    dis = ' disabled title="只读工作台"' if readonly else ""
    action = (
        f'<button class="btn mini" data-act="gen" '
        f'data-kind="{_e(row["kind"])}"{dis}>'
        f'{"重新导出" if row.get("path") else "导出"} {_e(row["label"])}</button>'
    )
    return (
        f'<article class="xc-finish-card" data-kind="{_e(row["kind"])}">'
        '<div class="xc-head">'
        f'<h3>{_e(row["label"])}</h3>'
        f'<span class="badge {cls}">{label}</span>'
        '</div>'
        f'<p class="xc-purpose">{_e(row.get("purpose"))}</p>'
        f'<p class="xc-basis">{_e(row.get("reason"))}</p>'
        f'{path}{baseline}'
        f'<div class="xc-actions">{action}</div>'
        '</article>'
    )


def _finishing_panel(data: dict[str, Any], *, readonly: bool = False) -> str:
    carriers = list((data.get("finishing") or {}).get("carriers") or [])
    if not carriers:
        return ""
    cards = "".join(_finishing_card(row, readonly=readonly) for row in carriers)
    return (
        '<section class="xc-finishing panel">'
        '<div class="xc-section-head">'
        '<div><h2>继续精剪</h2>'
        '<p class="muted">导出文件与回程基线分开验证。只有显示“可安全回收”，'
        '外部编辑后的变化才可以可靠带回 Manju。</p></div>'
        '</div>'
        f'<div class="xc-finishing-grid">{cards}</div>'
        '</section>'
    )


def _grouped_cards(rows: list[dict[str, Any]], *, readonly: bool = False) -> str:
    groups = (
        ("直接观看", "成片和本地预览", {"final", "proxy"}),
        ("字幕", "外挂字幕与烧录样式", {"srt", "ass", "vtt", "ttml"}),
        ("平台草稿", "需要在外部桌面应用中人工打开确认", {"jianying", "capcut"}),
        ("包装与声音", "封面、预告和交付母版", {
            "cover", "teaser", "DIALOGUE_STEM", "MUSIC_STEM", "SFX_STEM",
            "FULL_MIX", "M_AND_E_MASTER",
        }),
    )
    used: set[str] = set()
    sections: list[str] = []
    for title, subtitle, kinds in groups:
        selected = [row for row in rows if row.get("kind") in kinds]
        if not selected:
            continue
        used.update(str(row.get("kind")) for row in selected)
        cards = "".join(_card(row, readonly=readonly) for row in selected)
        sections.append(
            '<section class="xc-section">'
            f'<div class="xc-section-head"><div><h2>{_e(title)}</h2>'
            f'<p class="muted">{_e(subtitle)}</p></div></div>'
            f'<div class="xc-grid">{cards}</div>'
            '</section>'
        )
    rest = [row for row in rows if str(row.get("kind")) not in used]
    if rest:
        sections.append(
            '<section class="xc-section">'
            '<div class="xc-section-head"><div><h2>其他交付物</h2>'
            '<p class="muted">按项目需要出现的附加产物</p></div></div>'
            f'<div class="xc-grid">'
            + "".join(_card(row, readonly=readonly) for row in rest)
            + '</div></section>'
        )
    return "".join(sections)


def render(project: Any, token: str, *, readonly: bool = False) -> str:
    from ..build.exportstatus import deliverables_data
    from .finishing_journey import finishing_journey_html

    head = ('<div class="page-h"><h1>导出中心</h1>'
            '<span class="muted">按目标整理成片、精剪交换、字幕和包装产物</span></div>')
    try:
        data = deliverables_data(project)
    except Exception as exc:  # never break the page on a status error
        return _shell(
            "导出中心", token,
            head + finishing_journey_html(PAGE_PATH)
            + f'<p class="err panel">{_e(exc)}</p>',
            project,
        )

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
    if stale_kinds and not readonly:
        summary_chips += (
            f'<button class="btn mini" id="xc-gen-stale" '
            f'data-kinds="{_e(",".join(stale_kinds))}">'
            f'全部生成 / 更新待更新 ({len(stale_kinds)})</button>')
    if readonly:
        summary_chips += '<span class="chip readonly">只读模式 · 操作请回到项目机器</span>'
    summary = f'<div class="xc-summary panel">{summary_chips}</div>'

    finishing = _finishing_panel(data, readonly=readonly)
    cards = _grouped_cards(rows, readonly=readonly)
    legend = (
        '<details class="xc-legend muted panel">'
        '<summary>状态说明</summary>'
        '<p>词汇(§3)：<b>上新</b> 与当前规格一致 · <b>待更新</b> 上游已改需重做 · '
        '<b>缺失</b> 从未产出 · <b>有问题</b> 文件损坏或缺少证据 · '
        '<b>待人工确认</b> Manju 无法自行证明 · <b>已人工确认</b> 人工确认且字节未变。</p>'
        '</details>'
    )
    journey = finishing_journey_html(PAGE_PATH)
    body = head + journey + summary + finishing + cards + legend
    return _shell("导出中心", token, body, project)


# ============================================================ assets (css/js)


def render_exports_css() -> str:
    return _EXPORTS_CSS


def render_exports_js() -> str:
    return _EXPORTS_JS


_EXPORTS_CSS = """
/* 导出中心 export center (round-U). Loaded AFTER /app.css + /pages.css; reuses
   their palette (--panel/--line/--muted…) and badge chips (.badge.st-*). */
.xc-summary { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin: .8rem 0; }
.xc-finishing { margin: 1rem 0 1.4rem; padding: 1rem; }
.xc-finishing-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.xc-finish-card { border: 1px solid var(--line); border-radius: 10px; padding: .9rem; background: var(--panel2); }
.xc-finish-card h3 { margin: 0; font-size: 1rem; }
.xc-purpose { margin: .35rem 0; font-size: .82rem; color: var(--muted); }
.xc-section { margin: 1.3rem 0; }
.xc-section-head { display: flex; justify-content: space-between; align-items: end; gap: 1rem; margin: 0 0 .65rem; }
.xc-section-head h2 { margin: 0; font-size: 1.05rem; }
.xc-section-head p { margin: .18rem 0 0; font-size: .8rem; }
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
.xc-legend summary { cursor: pointer; color: var(--fg); }
.xc-legend p { margin: .65rem 0 0; }
.xc-card.busy { opacity: .6; }
@media (max-width: 760px) {
  .xc-finishing-grid { grid-template-columns: 1fr; }
  .xc-grid { grid-template-columns: 1fr; }
  .xc-section-head { align-items: start; }
}
"""


_EXPORTS_JS = r"""
"use strict";
(function () {

  function reloadSoon(delay) { setTimeout(function () { location.reload(); }, delay || 500); }

  function resultWarnings(job) {
    var result = job && job.result;
    var rows = result && Array.isArray(result.warnings) ? result.warnings : [];
    return rows.map(function (x) { return String(x || "").trim(); }).filter(Boolean);
  }

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

  // one kind through submit+poll; resolves {ok,warnings} only on a confirmed
  // terminal result. An interchange carrier may land while its round-trip
  // baseline fails; that is a useful one-way export, but never a silent green.
  function generateOne(kind) {
    setBusy(kind, true);
    return post("/api/exports/generate", { kind: kind }).then(function (res) {
      if ((res.status === 202 || res.status === 200) && res.data && res.data.job) {
        return pollJob(res.data.job.id).then(function (job) {
          setBusy(kind, false);
          if (job && job.state === "done") {
            var warnings = resultWarnings(job);
            warnings.forEach(function (message) { toast(message, false); });
            return { ok: true, warnings: warnings };
          }
          toast(jobFailText(kind, job), false);
          return { ok: false, warnings: [] };
        });
      }
      setBusy(kind, false);
      toast(res.data.error || (kind + " 生成失败"), false);
      return { ok: false, warnings: [] };
    }).catch(function () {
      setBusy(kind, false);
      toast("网络错误", false);
      return { ok: false, warnings: [] };
    });
  }

  function doGenerate(kind, btn) {
    btn.disabled = true;
    generateOne(kind).then(function (outcome) {
      if (outcome.ok) {
        toast(kind + " 已生成 / 更新", true);
        reloadSoon(outcome.warnings.length ? 1700 : 500);
      }
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
        return generateOne(kind).then(function (outcome) {
          if (outcome.ok) okCount++;
        });
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
    /* TRISURFACE F-20 (minimal): this click appends a PERMANENT human-
       verification record whose meaning is "a human opened this draft in the
       desktop app" — one stray click swore that oath by accident (round 1's
       automation did exactly that). One native confirm() before the append-
       only write, same pattern the subtitles takeover uses; retraction
       semantics stay a deferred decision. */
    if (!confirm("标记「已人工确认」= 你已在桌面 App 里打开过该草稿并确认无误。\n" +
                 "该记录写入核验日志后不可撤销。确定标记 " + kind + " 吗?")) return;
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
