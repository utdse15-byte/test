"""Static Review Board (§1-⑦, §11 ``manju board``).

The director's workbench replacement: a single self-contained ``board.html`` at
the project root — inline CSS, no external assets, one tiny inline vanilla-JS
copy helper. It covers ~80% of a GUI's value at ~1/20th the cost (§1-⑦):

  * a header with project / resolution / fps / mode / shot count / duration;
  * one card per shot in index order — id, action, dialogue, a colour-coded
    build-state badge (from :func:`manju.build.stale.evaluate_all`), and every
    take as an inline ``<video>`` with a RELATIVE ``media/gen/...`` src;
  * the selected take is highlighted (border + ★); each other take gets a
    click-to-copy ``manju select <shot> <take>`` one-liner;
  * a QC summary if ``reports/qc.json`` exists;
  * a generation-timestamp footer.

ALL user-supplied text (dialogue is arbitrary) is HTML-escaped. The file is
written atomically.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..build.stale import ShotState, evaluate_all
from ..core.hashing import HASH_PREFIX, hash_file
from ..core.yamlio import atomic_write_text, read_json, read_yaml

if TYPE_CHECKING:
    from ..core.container import Project

__all__ = ["generate_board", "render_board"]

def _project_aspect(project) -> str:
    """CSS aspect-ratio for media placeholders — the project's real frame
    (9/16 was hardcoded before round N and looked wrong on 16:9 kits)."""
    try:
        config = project.load_config()
        return f"{config.width}/{config.height}"
    except Exception:
        return "9/16"


# Build-state -> CSS class (colour-coded badge).
_STATE_CLASS = {
    ShotState.FRESH: "st-fresh",
    ShotState.STALE: "st-stale",
    ShotState.MISSING: "st-missing",
    ShotState.MANUAL: "st-manual",
    ShotState.NEEDS_SELECTION: "st-needs",
    ShotState.BROKEN: "st-broken",
}

# UX audit F18: the SAME state vocabulary the GUI already translates
# (gui/pages.py _RV_BUILDSTATE_ZH) — the two review surfaces used to name one
# state differently ('manual' badge here vs 手动置入 there). Badge text is the
# Chinese; the enum rides the title attribute (hover + grep-ability).
_STATE_ZH = {
    "missing": "无版本", "fresh": "最新", "stale": "待更新", "manual": "手动置入",
    "needs_selection": "待挑选", "broken": "缺媒体", "unknown": "未知",
}

# Per-take director verdicts (§R9/R10): a take_notes value of exactly "好"/"弃"
# is a one-key verdict (approve / reject); anything else is a free note. These
# mirror the GUI's 👍/👎 verdict buttons as static, colour-coded chips.
_VERDICT_LABELS = {"好": "👍好", "弃": "👎弃"}
_VERDICT_CLASS = {"好": "tv-ok", "弃": "tv-no"}
_NOTE_MAX = 80  # note-line display is truncated ~here; full text rides in title

_CSS = """
:root {
  --bg: #14161a; --panel: #1d2027; --panel2: #24272f; --line: #333844;
  --fg: #e8eaed; --muted: #9aa0aa; --accent: #6ea8fe; --star: #ffcf5c;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 0 4rem; background: var(--bg); color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC",
    "WenQuanYi Micro Hei", sans-serif;
  line-height: 1.5;
}
header.board {
  padding: 1.4rem 1.6rem; background: var(--panel); border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 5;
}
header.board h1 { margin: 0 0 .3rem; font-size: 1.4rem; }
header.board .meta { color: var(--muted); font-size: .9rem; }
header.board .meta span { margin-right: 1.1rem; white-space: nowrap; }
main { padding: 1.2rem 1.6rem; }
.shot {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 1rem 1.1rem; margin-bottom: 1.1rem;
}
.shot-head { display: flex; align-items: baseline; gap: .8rem; flex-wrap: wrap; }
.shot-head .sid { font-size: 1.1rem; font-weight: 700; }
.shot-head .action { color: var(--fg); }
.shot .dialogue { color: var(--muted); margin: .35rem 0 .7rem; font-size: .95rem; }
.shot .dialogue .speaker { color: var(--accent); }
.shot .sel-reject {
  margin: .5rem 0 .2rem; padding: .3rem .6rem; border-radius: 6px;
  font-size: .82rem; font-weight: 700; color: #ff8a90;
  background: #2a1517; border: 1px solid #4d1f22;
}
.badge {
  font-size: .72rem; font-weight: 700; padding: .12rem .5rem; border-radius: 999px;
  text-transform: uppercase; letter-spacing: .03em; white-space: nowrap;
}
.st-fresh  { background: #17402a; color: #7ee2a8; }
.st-stale  { background: #4a3a12; color: #ffcf5c; }
.st-missing{ background: #3a3d44; color: #c4c9d2; }
.st-manual { background: #23324d; color: #8fb8ff; }
.st-needs  { background: #4a2f12; color: #ffb27a; }
.st-broken { background: #4d1f22; color: #ff8a90; }
.tv-ok { background: #17402a; color: #7ee2a8; }
.tv-no { background: #4d1f22; color: #ff8a90; }
.takes { display: flex; flex-wrap: wrap; gap: .8rem; }
.take {
  background: var(--panel2); border: 1px solid var(--line); border-radius: 8px;
  padding: .55rem; width: 200px;
}
.take.selected { border: 2px solid var(--star); }
.take video { width: 100%; height: auto; border-radius: 5px; background: #000; display: block; }
.take .nomedia {
  /* MANJU_ASPECT is substituted with the project's real w/h at render time */
  width: 100%; aspect-ratio: MANJU_ASPECT; display: flex; align-items: center; justify-content: center;
  color: var(--muted); font-size: .8rem; background: #000; border-radius: 5px; text-align: center;
}
.take .tname { font-weight: 700; margin: .4rem 0 .15rem; font-size: .9rem; }
.take .tname .star { color: var(--star); }
.take .tmeta { color: var(--muted); font-size: .74rem; word-break: break-all; }
.take .tnote-row { margin-top: .4rem; }
.take .tnote {
  margin-top: .4rem; font-size: .76rem; color: #cfe3ff; background: #0f1114;
  border-radius: 4px; padding: .28rem .42rem; overflow-wrap: anywhere;
}
.selcmd { display: flex; align-items: center; gap: .4rem; margin-top: .45rem; }
.selcmd code {
  flex: 1; background: #0f1114; color: #cfe3ff; padding: .25rem .4rem; border-radius: 4px;
  font-size: .74rem; cursor: pointer; overflow-x: auto; white-space: nowrap;
}
.selcmd button {
  background: var(--accent); color: #0b1220; border: 0; border-radius: 4px;
  padding: .25rem .5rem; font-size: .72rem; font-weight: 700; cursor: pointer;
}
.qc { margin-top: 1.4rem; }
.qc h2 { font-size: 1.15rem; border-bottom: 1px solid var(--line); padding-bottom: .3rem; }
.qc .counts span {
  display: inline-block; margin: .2rem .5rem .4rem 0; padding: .15rem .55rem;
  border-radius: 999px; background: var(--panel2); font-size: .8rem;
}
.qc ul { padding-left: 1.1rem; }
.qc li { font-size: .86rem; margin: .18rem 0; }
.qc .lvl { font-weight: 700; margin-right: .4rem; }
.lvl-error, .lvl-fail { color: #ff8a90; }
.lvl-warn, .lvl-warning { color: #ffcf5c; }
.lvl-info, .lvl-pass, .lvl-ok { color: #7ee2a8; }
footer.board { padding: 1.2rem 1.6rem; color: var(--muted); font-size: .8rem; }
""".strip()

_JS = """
function mjCopy(btn){
  var code = btn.previousElementSibling;
  var text = code ? code.textContent : "";
  function done(){ var o = btn.textContent; btn.textContent = "copied"; setTimeout(function(){ btn.textContent = o; }, 1200); }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(function(){});
  } else {
    var r = document.createRange(); r.selectNodeContents(code);
    var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
    try { document.execCommand("copy"); done(); } catch (e) {}
    s.removeAllRanges();
  }
}
""".strip()

# --- serve mode only: an ACTIONABLE veneer over the same core the CLI calls ---
# These are appended after the static CSS/JS so the static board stays
# byte-for-byte identical (see the pin test). Buttons carry data-* attributes;
# one delegated click handler POSTs to /api/<action> and reloads on success.
_SERVE_CSS = """
.btn {
  background: var(--accent); color: #0b1220; border: 0; border-radius: 6px;
  padding: .35rem .7rem; font-size: .8rem; font-weight: 700; cursor: pointer;
}
.btn:hover { filter: brightness(1.08); }
.btn:disabled { background: var(--panel2); color: var(--muted); cursor: default; filter: none; }
.btn-redo { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); }
.btn-roll { background: #4a3a12; color: var(--star); }
.btn-sel  { background: #17402a; color: #7ee2a8; }
.board-actions { margin-top: .75rem; display: flex; gap: .5rem; flex-wrap: wrap; }
.shot-actions { display: flex; gap: .5rem; margin-top: .75rem; flex-wrap: wrap; }
.tact { margin-top: .45rem; }
.tact .btn { width: 100%; }
.mj-banner {
  /* UX audit F15: viewport-fixed — the banner used to sit in normal flow at
   * the very top, so a failed action while scrolled deep into the shot list
   * played out as "nothing happened" (the red line rendered 3000px above). */
  display: none; position: fixed; top: 0; left: 0; right: 0; z-index: 60;
  background: #4d1f22; color: #ff8a90; padding: .7rem 1.6rem;
  font-weight: 700; border-bottom: 1px solid #6b2a2e;
}
.mj-overlay {
  position: fixed; inset: 0; z-index: 50; background: rgba(10,12,16,.82);
  display: none; align-items: center; justify-content: center;
}
.mj-ovbox { text-align: center; color: var(--fg); max-width: 80%; }
.mj-spinner {
  width: 42px; height: 42px; margin: 0 auto .8rem; border-radius: 50%;
  border: 4px solid var(--line); border-top-color: var(--accent);
  animation: mjspin 1s linear infinite;
}
@keyframes mjspin { to { transform: rotate(360deg); } }
.mj-ovtext { font-size: 1rem; }
.mj-banner.ok { background: #17402a; color: #7ee2a8; border-bottom-color: #245c3a; }

/* --- panels (tabbed workspace sections) --- */
.mj-panels {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  margin-bottom: 1.1rem; overflow: hidden;
}
.mj-tabs {
  display: flex; flex-wrap: wrap; gap: .2rem; padding: .5rem .6rem 0;
  border-bottom: 1px solid var(--line); background: var(--panel2);
}
.mj-tab {
  background: transparent; color: var(--muted); border: 0;
  border-bottom: 2px solid transparent; padding: .5rem .8rem; font-size: .85rem;
  font-weight: 700; cursor: pointer;
}
.mj-tab:hover { color: var(--fg); }
.mj-tab.active { color: var(--fg); border-bottom-color: var(--accent); }
.mj-panel { display: none; padding: 1rem 1.1rem; }
.mj-panel.active { display: block; }
.mj-panel h3 { margin: 0 0 .6rem; font-size: 1.02rem; }
.mj-note { color: var(--muted); font-size: .82rem; margin: .2rem 0 .7rem; }
.mj-manual {
  background: #23324d; color: #8fb8ff; font-weight: 700; padding: .35rem .6rem;
  border-radius: 6px; display: inline-block; margin-bottom: .6rem;
}
.mj-dl { display: grid; grid-template-columns: max-content 1fr; gap: .3rem .9rem; font-size: .88rem; }
.mj-dl dt { color: var(--muted); }
.mj-dl dd { margin: 0; word-break: break-word; }
.mj-table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.mj-table th, .mj-table td { text-align: left; padding: .28rem .5rem; border-bottom: 1px solid var(--line); vertical-align: top; }
.mj-table th { color: var(--muted); font-weight: 700; }
.mj-table td.num { color: var(--muted); white-space: nowrap; width: 1%; }
.mj-cue-time { color: var(--accent); font-family: ui-monospace, monospace; white-space: nowrap; font-size: .78rem; }
.mj-bible-entry {
  border: 1px solid var(--line); border-radius: 8px; padding: .6rem .8rem;
  margin-bottom: .7rem; background: var(--panel2);
}
.mj-bible-entry h4 { margin: 0 0 .4rem; font-size: .92rem; }
.mj-lock { color: var(--star); margin-left: .3rem; }
.mj-asset {
  display: flex; align-items: center; gap: .7rem; padding: .35rem 0;
  border-bottom: 1px solid var(--line); font-size: .85rem;
}
.mj-asset img { width: 64px; height: auto; border-radius: 4px; background: #000; }
.mj-asset .sz { color: var(--muted); margin-left: auto; white-space: nowrap; }
.mj-sacred { color: var(--star); font-size: .82rem; margin-top: .6rem; }
.qc-sugg { color: var(--muted); font-size: .8rem; display: block; margin-left: 1.6rem; }

/* --- compare (side-by-side takes, synced playback) --- */
.btn-cmp { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); }
.btn-cmp.active { background: var(--accent); color: #0b1220; border-color: var(--accent); }
.btn-playall { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); }
.compare-wrap {
  display: none; margin-top: .9rem; border-top: 1px dashed var(--line); padding-top: .8rem;
}
.compare-wrap.open { display: block; }
.compare-head { display: flex; align-items: center; gap: .7rem; flex-wrap: wrap; margin-bottom: .7rem; }
.compare-hint { color: var(--muted); font-size: .78rem; }
.compare-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: .8rem; }
.cmp-cell { background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: .6rem; }
.cmp-cell video { width: 100%; height: auto; border-radius: 5px; background: #000; display: block; }
.cmp-name { font-weight: 700; margin-bottom: .35rem; font-size: .92rem; }
.cmp-name .star { color: var(--star); }
.cmp-meta { color: var(--muted); font-size: .76rem; margin: .4rem 0; word-break: break-all; }

/* --- compare pro modes (FP T1): wipe / difference / frame-lock stepping --- */
.btn-mode { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); }
.btn-mode.active { background: var(--accent); color: #0b1220; border-color: var(--accent); }
.cmp-rate {
  color: var(--muted); font-size: .74rem; font-family: ui-monospace, monospace;
  white-space: nowrap;
}
/* FP X1: the live review-transport status label — empty until a key drives it,
   then echoes the current key/rate (K pause · L ×N · J step-back · frame/1s). */
.cmp-transport {
  color: var(--accent); font-size: .74rem; font-family: ui-monospace, monospace;
  white-space: nowrap; min-height: 1em;
}
.cmp-transport:empty { display: none; }
.cmp-durwarn {
  margin: .1rem 0 .5rem; padding: .3rem .6rem; border-radius: 6px;
  font-size: .8rem; font-weight: 700; color: var(--star);
  background: #4a3a12; border: 1px solid #6b5518;
}
.cmp-ab { display: none; }
.compare-wrap[data-mode="wipe"] .cmp-ab,
.compare-wrap[data-mode="diff"] .cmp-ab { display: block; }
.compare-wrap[data-mode="wipe"] .compare-grid,
.compare-wrap[data-mode="diff"] .compare-grid { display: none; }
.ab-stage {
  position: relative; background: #000; border-radius: 6px; overflow: hidden;
  max-width: 720px;
}
.ab-stage video { width: 100%; height: auto; display: block; }
.ab-stage .ab-b { position: absolute; inset: 0; clip-path: inset(0 0 0 50%); }
.ab-stage .ab-canvas { display: none; position: absolute; inset: 0; width: 100%; height: 100%; }
.compare-wrap[data-mode="diff"] .ab-canvas { display: block; }
.compare-wrap[data-mode="diff"] .ab-stage video { opacity: 0; }
.ab-controls {
  display: flex; align-items: center; gap: .8rem; flex-wrap: wrap;
  margin-top: .5rem; font-size: .8rem; color: var(--muted);
}
.ab-controls input[type="range"] { vertical-align: middle; }
.compare-wrap[data-mode="wipe"] .ab-gainctl,
.compare-wrap[data-mode="wipe"] .ab-gainlabel { display: none; }
.compare-wrap[data-mode="diff"] .ab-wipectl { display: none; }
.ab-gainlabel { color: var(--star); font-size: .76rem; }
.cmp-ab-unavail { color: var(--muted); font-size: .8rem; }

/* --- onion skin (FP V2): a fourth A/B mode — B stacked over A at CSS opacity.
   No canvas for the video pair; the wipe clip-path and the onion opacity are
   each an inline override on .ab-b that setCmpMode clears on every switch, so a
   mode always starts from its own CSS baseline (never a leaked half-clip). --- */
.compare-wrap[data-mode="onion"] .cmp-ab { display: block; }
.compare-wrap[data-mode="onion"] .compare-grid { display: none; }
.compare-wrap[data-mode="onion"] .ab-b { clip-path: none; opacity: .5; }
.ab-onionctl { display: none; }
.compare-wrap[data-mode="onion"] .ab-onionctl { display: inline-block; }
.compare-wrap[data-mode="onion"] .ab-wipectl,
.compare-wrap[data-mode="onion"] .ab-gainctl,
.compare-wrap[data-mode="onion"] .ab-gainlabel { display: none; }
.ab-onionlabel { display: none; color: var(--accent); font-size: .76rem; }
.compare-wrap[data-mode="onion"] .ab-onionlabel { display: inline; }

/* --- scopes (FP V2): a collapsed-by-default luma histogram + per-column luma
   waveform of video A's CURRENT PARKED frame. Client-side, browser-decoded RGB,
   VIEW-ONLY — the canvas is never a fact source (permanent label below). --- */
.cmp-scopes-wrap { margin-top: .8rem; }
.cmp-scopes {
  display: none; margin-top: .5rem; border-top: 1px dashed var(--line); padding-top: .6rem;
}
.cmp-scopes.open { display: block; }
.cmp-scopes-label {
  color: var(--muted); font-size: .76rem; line-height: 1.55; margin: 0 0 .55rem;
}
.cmp-scope-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: .8rem;
}
.cmp-scope-cell { min-width: 0; }
.cmp-scope-cap { color: var(--muted); font-size: .74rem; margin-bottom: .25rem; }
.cmp-scope-canvas {
  width: 100%; height: auto; max-width: 320px; background: #000; border-radius: 6px;
  display: block; image-rendering: pixelated;
}
.cmp-scopes-note { color: var(--star); font-size: .78rem; margin: .5rem 0 0; }

/* --- boundary view (FP T1): accepted ending vs next start, per adjacent pair --- */
.boundary h2 { font-size: 1.15rem; border-bottom: 1px solid var(--line); padding-bottom: .3rem; }
.bnd-row {
  border: 1px solid var(--line); border-radius: 8px; background: var(--panel2);
  padding: .7rem .8rem; margin-bottom: .8rem;
}
.bnd-head { display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; margin-bottom: .55rem; }
.bnd-pair { font-weight: 700; font-size: .98rem; }
.bnd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: .8rem; }
.bnd-cell { min-width: 0; }
.bnd-lab { color: var(--muted); font-size: .78rem; margin-bottom: .3rem; }
.bnd-img { width: 100%; height: auto; display: block; border-radius: 5px; background: #000; }
.bnd-missing {
  /* MANJU_ASPECT is substituted with the project's real w/h at render time */
  width: 100%; aspect-ratio: MANJU_ASPECT; display: flex; align-items: center;
  justify-content: center; color: var(--muted); font-size: .78rem; background: #000;
  border-radius: 5px; text-align: center; padding: .5rem;
}
.bnd-canvas {
  display: none; grid-column: 1 / -1; width: 100%; max-width: 560px;
  background: #000; border-radius: 6px;
}
.bnd-row.bnd-diffon .bnd-canvas { display: block; }

/* --- boundary onion skin (FP V2): the next shot's FIRST still stacked over
   this shot's LAST still at CSS opacity — does the cut register? A view stacked
   from the SAME cached stills, distinct from (and independent of) the diff
   canvas above; both toggle on the same row without conflicting. --- */
.bnd-onionctl { color: var(--muted); font-size: .78rem; }
.bnd-onion { display: none; position: relative; max-width: 480px; margin-top: .6rem; }
.bnd-row.bnd-onionon .bnd-onion { display: block; }
.bnd-onion .bnd-onion-img {
  width: 100%; display: block; border-radius: 5px; background: #000;
}
.bnd-onion .bnd-onion-b { position: absolute; inset: 0; opacity: .5; }

/* --- review annotations (Wave 3 §5.5): per-take media-bound notes --- */
.ann-wrap { margin-top: .45rem; }
.ann-list { display: flex; flex-direction: column; gap: .3rem; }
.ann-row {
  background: #0f1114; border: 1px solid var(--line); border-radius: 5px;
  padding: .3rem .45rem; font-size: .76rem;
}
.ann-head { display: flex; align-items: center; gap: .35rem; flex-wrap: wrap; }
.ann-note { background: #23324d; color: #8fb8ff; }
.ann-issue { background: #4a3a12; color: #ffcf5c; }
.ann-blocker { background: #4d1f22; color: #ff8a90; }
.ann-stale { background: #4d1f22; color: #ff8a90; }
.ann-seek {
  color: var(--accent); cursor: pointer; font-family: ui-monospace, monospace;
  font-size: .72rem; white-space: nowrap;
}
.ann-body { margin-top: .2rem; overflow-wrap: anywhere; color: #cfe3ff; }
.ann-subject { color: var(--star); }
.ann-meta { color: var(--muted); font-size: .7rem; margin-top: .15rem; }
.ann-form {
  display: flex; gap: .3rem; margin-top: .4rem; flex-wrap: wrap; align-items: center;
}
.ann-form .ann-text {
  flex: 1; min-width: 110px; background: #0f1114; color: var(--fg);
  border: 1px solid var(--line); border-radius: 4px; padding: .25rem .4rem;
  font-size: .76rem;
}
.ann-form .ann-sev {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 4px; font-size: .74rem; padding: .2rem;
}
.ann-at { color: var(--muted); font-size: .72rem; white-space: nowrap; }
""".strip()

_SERVE_JS = """
(function(){
  var MSG = {
    build: "正在构建成片,可能需要几分钟,请勿关闭页面…",
    qc: "正在质检…",
    package: "正在生成封面/预告…",
    snapshot: "正在保存快照…",
    redo: "正在重做该镜头…",
    select: "正在切换选用…",
    rollback_shot: "正在回滚该镜头…",
    export: "正在导出草稿/字幕…",
    annotate: "正在保存批注…"
  };
  function el(id){ return document.getElementById(id); }
  function overlay(show, msg){
    var o = el("mj-overlay"); if(!o) return;
    o.querySelector(".mj-ovtext").textContent = msg || "处理中…";
    o.style.display = show ? "flex" : "none";
  }
  function banner(msg, ok){
    var b = el("mj-banner"); if(!b) return;
    b.textContent = msg || "";
    b.className = "mj-banner" + (ok ? " ok" : "");
    b.style.display = msg ? "block" : "none";
  }
  function showExport(d){
    overlay(false);
    var parts = [], o = d.outputs || {}, k;
    for (k in o){ if (o.hasOwnProperty(k)) parts.push(k + "=" + o[k]); }
    var notes = (d.notes && d.notes.length) ? " · " + d.notes.join(" · ") : "";
    banner("✓ 导出完成 export: " + (parts.join(" · ") || "无") + notes, true);
  }
  function post(action, body, onOk){
    banner("");
    overlay(true, MSG[action] || "处理中…");
    fetch("/api/" + action, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Manju-Token": (typeof MANJU_TOKEN!=="undefined"?MANJU_TOKEN:"")},
      body: JSON.stringify(body || {})
    }).then(function(r){
      return r.json().catch(function(){
        return {ok:false, error:"服务器返回了无法解析的响应 (HTTP " + r.status + ")"};
      });
    }).then(function(d){
      if (d && d.ok) {
        if (d.outputs) { showExport(d); return; }  // export: show paths, don't reload
        /* UX audit F16: a caller-managed success path — annotate updates the
         * page in place instead of location.reload(), which dropped every
         * parked player / compare mode / active tab. */
        if (onOk) { onOk(d); return; }
        location.reload(); return;
      }
      overlay(false);
      banner("✗ " + ((d && d.error) || "操作失败"));
    }).catch(function(err){
      overlay(false);
      banner("✗ 请求失败:" + err);
    });
  }
  function switchTab(key){
    var tabs = document.querySelectorAll(".mj-tab"), i;
    for (i = 0; i < tabs.length; i++){
      tabs[i].classList.toggle("active", tabs[i].getAttribute("data-tab") === key);
    }
    var panels = document.querySelectorAll(".mj-panel"), j;
    for (j = 0; j < panels.length; j++){
      panels[j].classList.toggle("active", panels[j].getAttribute("data-panel") === key);
    }
    /* UX audit F16: the active tab survives navigation/reload in the URL
     * hash (replaceState — no history spam). */
    try { history.replaceState(null, "", "#mjtab-" + key); } catch (e) {}
  }
  (function restoreTab(){
    if (location.hash && location.hash.indexOf("#mjtab-") === 0){
      var key = location.hash.slice(7);
      if (document.querySelector('.mj-tab[data-tab="' + key + '"]')){
        switchTab(key);
      }
    }
  })();
  function toggleCompare(btn){
    var section = btn.closest("section.shot"); if(!section) return;
    var wrap = section.querySelector(".compare-wrap"); if(!wrap) return;
    var open = wrap.classList.toggle("open");
    btn.classList.toggle("active", open);
    if(!open){ pauseAll(wrap.querySelectorAll("video")); stopDiffLoop(wrap); }
    else if ((wrap.getAttribute("data-mode") || "sbs") === "diff"){ startDiffLoop(wrap); }
  }
  function pauseAll(vids){ for (var i = 0; i < vids.length; i++){ vids[i].pause(); } }
  function anyPlaying(vids){
    for (var i = 0; i < vids.length; i++){ if(!vids[i].paused && !vids[i].ended){ return true; } }
    return false;
  }
  // FP T1: the videos the CURRENT compare mode shows — the grid in
  // side-by-side, the A/B stack in wipe/difference. Sync/step act on these
  // only, so hidden duplicates never double the audio.
  function activeCmpVideos(wrap){
    var mode = wrap.getAttribute("data-mode") || "sbs";
    var vids = wrap.querySelectorAll(mode === "sbs" ? ".compare-grid video" : ".cmp-ab video");
    return vids.length ? vids : wrap.querySelectorAll("video");
  }
  function syncPlay(btn){
    var wrap = btn.closest(".compare-wrap"); if(!wrap) return;
    var vids = activeCmpVideos(wrap);
    if (anyPlaying(vids)){
      pauseAll(wrap.querySelectorAll("video"));
      btn.textContent = "▶ 同步播放 sync play";
    } else {
      for (var i = 0; i < vids.length; i++){
        try { vids[i].currentTime = 0; } catch(e) {}   // align starts for a fair compare
        vids[i].play().catch(function(){});
      }
      btn.textContent = "⏸ 同步暂停 pause all";
    }
  }
  // ---- FP T1: compare pro modes (wipe / difference / frame-lock stepping).
  // All pixel work is CLIENT-SIDE (CSS clip-path; canvas composite) — the
  // canvas is a VIEW, never a fact source, and the difference is honestly
  // labelled "amplified ×N" whenever gain is applied.
  function setCmpMode(btn){
    var wrap = btn.closest(".compare-wrap"); if(!wrap) return;
    var mode = btn.getAttribute("data-cmpmode") || "sbs";
    var btns = wrap.querySelectorAll("[data-cmpmode]");
    for (var i = 0; i < btns.length; i++){ btns[i].classList.toggle("active", btns[i] === btn); }
    pauseAll(wrap.querySelectorAll("video"));
    wrap.setAttribute("data-mode", mode);
    // FP V2: wipe (clip-path) and onion (opacity) each own a different INLINE
    // override on .ab-b; clear both on every switch so the new mode starts from
    // its CSS baseline — never a leaked half-clip or a stale onion opacity that
    // would even survive into diff mode's opacity:0 rule (inline beats sheet).
    var abB = wrap.querySelector(".cmp-ab .ab-b");
    if (abB){ abB.style.clipPath = ""; abB.style.opacity = ""; }
    if (mode === "diff"){ startDiffLoop(wrap); } else { stopDiffLoop(wrap); }
    drawScopes(wrap);  // parked frame may now be visible under a new mode
  }
  // ±1 frame, pause-synced, using the EXACT frame period den/num seconds from
  // data-fps-num/den (the R2 rational timeline echo when the project is
  // rational — 1001/24000 s — never a rounded millisecond count).
  // FP X1: the den/num arithmetic and the pause+seek+scope-redraw path are
  // extracted into framePeriod()/cmpSeek() so the ±1-frame BUTTONS and the
  // keyboard review transport (K/L/J/arrows) share ONE reader and ONE seek path
  // — the arithmetic is never duplicated and there is never a second seek code.
  function framePeriod(wrap){
    var num = parseInt(wrap.getAttribute("data-fps-num") || "0", 10);
    var den = parseInt(wrap.getAttribute("data-fps-den") || "0", 10);
    if (!num || !den) return 0;
    return den / num;  // one exact frame period, seconds (0 ⇒ rate unknown)
  }
  function cmpSeek(wrap, dt){
    pauseAll(wrap.querySelectorAll("video"));
    var vids = activeCmpVideos(wrap);
    for (var i = 0; i < vids.length; i++){
      try { vids[i].currentTime = Math.max(0, vids[i].currentTime + dt); } catch(e) {}
    }
    drawScopes(wrap);  // FP V2: scopes follow the step (also fired on 'seeked')
  }
  function frameStep(btn){
    var wrap = btn.closest(".compare-wrap"); if(!wrap) return;
    var per = framePeriod(wrap); if (!per) return;
    var dir = parseInt(btn.getAttribute("data-framestep") || "0", 10);
    cmpSeek(wrap, dir * per);  // one exact frame period, signed
  }
  function wipeMove(input){
    var stage = input.closest(".cmp-ab"); if(!stage) return;
    var b = stage.querySelector(".ab-b"); if(!b) return;
    b.style.clipPath = "inset(0 0 0 " + input.value + "%)";
  }
  // FP V2: onion-skin opacity on the stacked B video (0-100% → 0-1). The classic
  // "does the next frame register?" overlay — pure CSS opacity, no canvas.
  function onionMove(input){
    var stage = input.closest(".cmp-ab"); if(!stage) return;
    var b = stage.querySelector(".ab-b"); if(!b) return;
    b.style.opacity = (parseInt(input.value, 10) || 0) / 100;
    var ctl = input.closest(".ab-controls");
    var lab = ctl ? ctl.querySelector("[data-onionlabel]") : null;
    if (lab){ lab.textContent = "B 叠加 over A @ " + input.value +
      "% — 洋葱皮 onion-skin(仅视图 view-only)"; }
  }
  function gainHostOf(el){ return el.closest(".compare-wrap") || el.closest(".bnd-row"); }
  function gainMove(input){
    var host = gainHostOf(input); if(!host) return;
    host.setAttribute("data-gain", input.value);
    var label = host.querySelector("[data-gainlabel]");
    if (label){ label.textContent = "差异已放大 amplified ×" + input.value + " — 非原始像素差 not raw pixel deltas"; }
    if (host.classList && host.classList.contains("bnd-diffon")){ drawBoundaryDiff(host); }
  }
  // Draw |A-B| into ctx, then amplify by `gain` in a second pass IF the
  // browser supports canvas filters; otherwise stay at ×1 and SAY so.
  function diffDraw(ctx, a, b, w, h, gain, label){
    ctx.filter = "none";
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, w, h);
    ctx.drawImage(a, 0, 0, w, h);
    ctx.globalCompositeOperation = "difference";
    ctx.drawImage(b, 0, 0, w, h);
    ctx.globalCompositeOperation = "source-over";
    var applied = 1;
    if (gain > 1 && typeof ctx.filter === "string"){
      ctx.globalCompositeOperation = "copy";
      ctx.filter = "brightness(" + gain + ")";
      ctx.drawImage(ctx.canvas, 0, 0);
      ctx.filter = "none";
      ctx.globalCompositeOperation = "source-over";
      applied = gain;
    }
    if (label){
      label.textContent = (applied > 1)
        ? "差异已放大 amplified ×" + applied + " — 非原始像素差 not raw pixel deltas"
        : (gain > 1 ? "增益不可用 gain unavailable — 显示原始差异 raw difference (×1)"
                    : "原始差异 raw difference (×1)");
    }
  }
  function stopDiffLoop(wrap){
    if (wrap._mjRaf){ cancelAnimationFrame(wrap._mjRaf); wrap._mjRaf = null; }
  }
  function startDiffLoop(wrap){
    stopDiffLoop(wrap);
    var a = wrap.querySelector(".cmp-ab .ab-a"), b = wrap.querySelector(".cmp-ab .ab-b");
    var canvas = wrap.querySelector(".cmp-ab .ab-canvas");
    if (!a || !b || !canvas) return;
    var ctx = canvas.getContext("2d");
    function tick(){
      if ((wrap.getAttribute("data-mode") || "sbs") !== "diff" || !wrap.classList.contains("open")){
        wrap._mjRaf = null; return;
      }
      if (a.videoWidth){
        var w = Math.min(a.videoWidth, 640);
        var h = Math.round(w * a.videoHeight / a.videoWidth);
        if (canvas.width !== w || canvas.height !== h){ canvas.width = w; canvas.height = h; }
        if (a.readyState >= 2 && b.readyState >= 2){
          diffDraw(ctx, a, b, w, h,
                   parseInt(wrap.getAttribute("data-gain") || "4", 10),
                   wrap.querySelector("[data-gainlabel]"));
        }
      }
      wrap._mjRaf = requestAnimationFrame(tick);
    }
    wrap._mjRaf = requestAnimationFrame(tick);
  }
  // Duration honesty (client side): once metadata is in, differing A/B
  // durations get labelled — never a silent misalignment. (The server also
  // renders a fact label when the sidecar probes already prove a mismatch.)
  function checkAbDurations(wrap){
    var a = wrap.querySelector(".cmp-ab .ab-a"), b = wrap.querySelector(".cmp-ab .ab-b");
    var warn = wrap.querySelector("[data-durwarn]");
    if (!a || !b || !warn || !isFinite(a.duration) || !isFinite(b.duration)) return;
    var num = parseInt(wrap.getAttribute("data-fps-num") || "24", 10) || 24;
    var den = parseInt(wrap.getAttribute("data-fps-den") || "1", 10) || 1;
    if (Math.abs(a.duration - b.duration) > (den / num) / 2){
      warn.textContent = "⚠ 时长不同 " + a.duration.toFixed(3) + "s vs " +
        b.duration.toFixed(3) + "s — 逐帧步进按各自时钟 frame steps run on each video's own clock";
      warn.hidden = false;
    }
  }
  // Boundary view: client-side difference of the two boundary stills.
  function toggleBoundaryDiff(btn){
    var row = btn.closest(".bnd-row"); if(!row) return;
    var on = row.classList.toggle("bnd-diffon");
    btn.classList.toggle("active", on);
    if (on){ drawBoundaryDiff(row); }
  }
  function drawBoundaryDiff(row){
    var imgs = row.querySelectorAll("img.bnd-img");
    var canvas = row.querySelector("canvas[data-bndcanvas]");
    if (imgs.length < 2 || !canvas) return;
    var a = imgs[0], b = imgs[1];
    if (!a.complete || !b.complete){
      var retry = function(){ drawBoundaryDiff(row); };
      if (!a.complete) a.addEventListener("load", retry, {once: true});
      if (!b.complete) b.addEventListener("load", retry, {once: true});
      return;
    }
    var w = a.naturalWidth || 480, h = a.naturalHeight || 270;
    canvas.width = w; canvas.height = h;
    diffDraw(canvas.getContext("2d"), a, b, w, h,
             parseInt(row.getAttribute("data-gain") || "4", 10),
             row.querySelector("[data-gainlabel]"));
  }
  // FP V2: onion-skin on a boundary pair — the next shot's FIRST still stacked
  // over this shot's LAST still at CSS opacity (does the cut register?). A pure
  // CSS overlay of the SAME cached stills; independent of the diff canvas, both
  // toggle on the same row without conflicting.
  function toggleBoundaryOnion(btn){
    var row = btn.closest(".bnd-row"); if(!row) return;
    var on = row.classList.toggle("bnd-onionon");
    btn.classList.toggle("active", on);
  }
  function boundaryOnionMove(input){
    var row = input.closest(".bnd-row"); if(!row) return;
    var b = row.querySelector(".bnd-onion-b"); if(!b) return;
    b.style.opacity = (parseInt(input.value, 10) || 0) / 100;
  }
  // FP V2: SCOPES — a Rec.709 luma histogram + a per-column luma waveform of
  // video A's CURRENT PARKED frame. VIEW-ONLY: browser-decoded RGB sampled to an
  // offscreen canvas; the canvas is NEVER a fact source (QC colorstats remain the
  // measurement authority — see the permanent label). Redrawn ONLY on discrete
  // parked-frame events (pause / frame-step / seek / slider), NEVER in a loop
  // while playing — scopes are for parked frames.
  function toggleScopes(btn){
    var box = btn.closest(".cmp-scopes-wrap"); if(!box) return;
    var panel = box.querySelector(".cmp-scopes"); if(!panel) return;
    var on = panel.classList.toggle("open");
    btn.classList.toggle("active", on);
    if (on){ drawScopes(btn.closest(".compare-wrap")); }
  }
  function scopesNote(panel, msg){
    var n = panel.querySelector("[data-scope-note]");
    if (n){ n.textContent = msg || ""; n.hidden = !msg; }
  }
  function rec709(d, o){ return 0.2126*d[o] + 0.7152*d[o+1] + 0.0722*d[o+2]; }
  function drawScopes(wrap){
    if (!wrap || !wrap.classList.contains("open")) return;
    var panel = wrap.querySelector(".cmp-scopes");
    if (!panel || !panel.classList.contains("open")) return;  // collapsed → skip
    var a = wrap.querySelector(".cmp-ab .ab-a");
    var hist = panel.querySelector("[data-scope-hist]");
    var wave = panel.querySelector("[data-scope-wave]");
    if (!a || !hist || !wave) return;
    if (!a.videoWidth){ scopesNote(panel, "暂无帧 — 播放后暂停或步进一帧 park a frame first"); return; }
    var hctx = hist.getContext && hist.getContext("2d");
    var wctx = wave.getContext && wave.getContext("2d");
    if (!hctx || !wctx){ scopesNote(panel, "画布 2D 不可用 canvas 2D unavailable — 示波器无法绘制"); return; }
    var sw = Math.min(a.videoWidth, 256);
    var sh = Math.max(1, Math.round(sw * a.videoHeight / a.videoWidth));
    var samp = wrap._mjScopeSamp || (wrap._mjScopeSamp = document.createElement("canvas"));
    samp.width = sw; samp.height = sh;
    var sctx = samp.getContext("2d"), data;
    try {
      sctx.drawImage(a, 0, 0, sw, sh);
      data = sctx.getImageData(0, 0, sw, sh).data;
    } catch (e){
      scopesNote(panel, "无法读取像素 pixel read blocked (" + ((e && e.name) || "error") +
        ") — 示波器仅供查看,非事实来源 view-only, not a fact source");
      return;
    }
    scopesNote(panel, "");
    drawLumaHistogram(hctx, hist, data);
    drawLumaWaveform(wctx, wave, data, sw, sh);
  }
  function drawLumaHistogram(ctx, canvas, data){
    var W = 256, H = 120; canvas.width = W; canvas.height = H;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, W, H);
    var bins = new Array(256), k;
    for (k = 0; k < 256; k++){ bins[k] = 0; }
    for (var i = 0; i < data.length; i += 4){
      var y = rec709(data, i) | 0; if (y < 0){ y = 0; } else if (y > 255){ y = 255; }
      bins[y]++;
    }
    var max = 0, b;
    for (b = 0; b < 256; b++){ if (bins[b] > max){ max = bins[b]; } }
    ctx.fillStyle = "#7ee2a8";
    if (max > 0){
      for (var x = 0; x < 256; x++){
        var bh = Math.round((H - 2) * bins[x] / max);
        if (bh > 0){ ctx.fillRect(x, H - bh, 1, bh); }
      }
    }
  }
  function drawLumaWaveform(ctx, canvas, data, sw, sh){
    var W = sw, H = 120; canvas.width = W; canvas.height = H;
    var img = ctx.createImageData(W, H), o = img.data, p;
    for (p = 3; p < o.length; p += 4){ o[p] = 255; }  // opaque black baseline
    for (var x = 0; x < sw; x++){
      for (var yy = 0; yy < sh; yy++){
        var s = (yy * sw + x) * 4;
        var py = H - 1 - Math.round(rec709(data, s) * (H - 1) / 255);
        if (py < 0){ py = 0; } else if (py >= H){ py = H - 1; }
        var dp = (py * W + x) * 4;
        o[dp] = Math.min(255, o[dp] + 40);
        o[dp + 1] = Math.min(255, o[dp + 1] + 90);
        o[dp + 2] = Math.min(255, o[dp + 2] + 60);
      }
    }
    ctx.putImageData(img, 0, 0);
  }
  function playAll(btn){
    var section = btn.closest("section.shot"); if(!section) return;
    var vids = section.querySelectorAll(".takes video");
    if (anyPlaying(vids)){ pauseAll(vids); }
    else { for (var i = 0; i < vids.length; i++){ vids[i].play().catch(function(){}); } }
  }
  // --- review annotations (Wave 3 §5.5) --- click-to-seek: a data-seek chip
  // parks ITS OWN take's <video> at the annotation's bound time (seconds
  // pre-computed server-side from the annotation's exact frame_rate binding).
  function annSeek(el){
    var tk = el.closest(".take"); if(!tk) return;
    var v = tk.querySelector("video"); if(!v) return;
    var s = parseFloat(el.getAttribute("data-seek"));
    if (!isFinite(s)) return;
    try { v.pause(); v.currentTime = Math.max(0, s); } catch(e) {}
  }
  // Add-form submit: POST /api/annotate through the SAME token-carrying post()
  // every other action uses. "at current frame" captures the take video's
  // parked currentTime as an exact frame number via the page's rational rate
  // (data-fps-num/den); expected_rev (the shot's CAS token rendered into the
  // form) rides along so a stale page can never silently clobber the shot.
  function annSubmit(btn){
    var box = btn.closest(".ann-form"); if(!box) return;
    var input = box.querySelector(".ann-text");
    var sel = box.querySelector(".ann-sev");
    var body = {
      shot: box.getAttribute("data-shot"),
      take: box.getAttribute("data-take"),
      text: input ? input.value : "",
      severity: sel ? sel.value : "note"
    };
    var chk = box.querySelector(".ann-atframe");
    var num = parseInt(box.getAttribute("data-fps-num") || "0", 10);
    var den = parseInt(box.getAttribute("data-fps-den") || "0", 10);
    if (chk && chk.checked && num && den){
      /* UX audit F16: frame-accurate parking happens in the OPEN compare
       * stack (K/L/J and the ±1帧 transport live there) — read the parked
       * time from its active video first; the small take-card player is the
       * fallback. Without this, '当前帧' silently bound to the card video's
       * time (usually f0) while the owner was staring at the bad frame. */
      var v = null;
      var section = box.closest("section.shot");
      var wrap = section ? section.querySelector(".compare-wrap.open") : null;
      if (wrap){
        var cvids = activeCmpVideos(wrap);
        if (cvids.length) v = cvids[0];
      }
      if (!v){
        var tk = box.closest(".take");
        v = tk ? tk.querySelector("video") : null;
      }
      if (v){ body.frame = Math.max(0, Math.round(v.currentTime * num / den)); }
    }
    var rev = box.getAttribute("data-rev");
    if (rev){ body.expected_rev = rev; }
    post("annotate", body, function(d){
      overlay(false);
      banner("✓ 批注已记录(" + (body.severity || "note") + (body.frame != null ? " · f" + body.frame : "") + ")", true);
      if (input) input.value = "";
      /* refresh EVERY form of this shot's CAS token — the write staled them
       * all (the F14 class, board edition). */
      if (d && d.rev){
        var forms = document.querySelectorAll('.ann-form[data-shot="' + body.shot + '"]'), i;
        for (i = 0; i < forms.length; i++){ forms[i].setAttribute("data-rev", d.rev); }
      }
      /* show the new annotation in place — no reload, parked state survives */
      var tk2 = box.closest(".take");
      var list = tk2 ? tk2.querySelector(".ann-list") : null;
      if (list){
        var row = document.createElement("div");
        row.className = "ann-row";
        row.textContent = "[" + (body.severity || "note") + "] "
          + (body.frame != null ? "f" + body.frame + " · " : "") + body.text;
        list.appendChild(row);
      }
    });
  }
  document.addEventListener("click", function(e){
    var t = e.target, hit;
    if ((hit = t.closest && t.closest("[data-tab]"))){ switchTab(hit.getAttribute("data-tab")); return; }
    if ((hit = t.closest && t.closest("[data-compare]"))){ toggleCompare(hit); return; }
    if ((hit = t.closest && t.closest("[data-syncplay]"))){ syncPlay(hit); return; }
    if ((hit = t.closest && t.closest("[data-playall]"))){ playAll(hit); return; }
    if ((hit = t.closest && t.closest("[data-cmpmode]"))){ setCmpMode(hit); return; }
    if ((hit = t.closest && t.closest("[data-framestep]"))){ frameStep(hit); return; }
    if ((hit = t.closest && t.closest("[data-bnddiff]"))){ toggleBoundaryDiff(hit); return; }
    if ((hit = t.closest && t.closest("[data-bndonion]"))){ toggleBoundaryOnion(hit); return; }
    if ((hit = t.closest && t.closest("[data-scopes]"))){ toggleScopes(hit); return; }
    if ((hit = t.closest && t.closest("[data-annsubmit]"))){ annSubmit(hit); return; }
    if ((hit = t.closest && t.closest("[data-seek]"))){ annSeek(hit); return; }
    var btn = t.closest ? t.closest("button[data-act]") : null;
    if (!btn || btn.disabled) return;
    var body = {};
    if (btn.dataset.shot) body.shot = btn.dataset.shot;
    if (btn.dataset.take) body.take = btn.dataset.take;
    if (btn.dataset.target) body.target = btn.dataset.target;
    post(btn.getAttribute("data-act"), body);
  });
  // FP T1: range sliders (wipe position / difference gain) via one delegated
  // input listener, mirroring the delegated click handler above.
  document.addEventListener("input", function(e){
    var t = e.target;
    if (!t || !t.matches) return;
    // FP V2: scopes redraw on slider events too (a no-op unless the panel is
    // open) — one of the sanctioned parked-frame redraw triggers, never a loop.
    if (t.matches("input[data-wipe]")){ wipeMove(t); drawScopes(t.closest(".compare-wrap")); return; }
    if (t.matches("input[data-onion]")){ onionMove(t); drawScopes(t.closest(".compare-wrap")); return; }
    if (t.matches("input[data-diffgain]")){ gainMove(t); return; }
    if (t.matches("input[data-bndonion-op]")){ boundaryOnionMove(t); return; }
  });
  // FP T1: duration honesty for the A/B pair as soon as metadata arrives.
  document.addEventListener("loadedmetadata", function(e){
    var t = e.target;
    if (t && t.tagName === "VIDEO" && t.closest && t.closest(".cmp-ab")){
      var wrap = t.closest(".compare-wrap");
      if (wrap){ checkAbDurations(wrap); }
    }
  }, true);
  // FP V2: scopes redraw on the discrete parked-frame events ONLY — pause and
  // seek (the frame-step lands here) — never a rAF loop while playing (the
  // scopes are for parked frames; the permanent label says so).
  function scopesFromVideoEvent(e){
    var t = e.target;
    if (t && t.tagName === "VIDEO" && t.closest && t.closest(".cmp-ab")){
      var wrap = t.closest(".compare-wrap");
      if (wrap){ drawScopes(wrap); }
    }
  }
  document.addEventListener("pause", scopesFromVideoEvent, true);
  document.addEventListener("seeked", scopesFromVideoEvent, true);
  // FP X1: professional review TRANSPORT for the open compare — K/L/J + arrows,
  // NATIVE browser transport ONLY. It extends the SAME delegated keydown listener
  // below (never a second listener) and shares framePeriod()/cmpSeek() with the
  // ±1-frame buttons, so parked scopes follow via V2's 'seeked' hook.
  // NATIVE playbackRate cycle 1→2→4→1: return the rate to play at THIS press and
  // advance the stored state for the next — the browser's own rate, never a
  // re-timed shuttle.
  function cmpRate(wrap){
    var cur = wrap._mjRate || 1;
    wrap._mjRate = (cur >= 4) ? 1 : cur * 2;  // 1→2→4→1
    return cur;
  }
  // The one small transport status label (data-transport) — the current key/rate
  // shows here so the pro grammar is discoverable, never a hidden chord.
  function setTransport(wrap, msg){
    var lab = wrap.querySelector("[data-transport]");
    if (lab){ lab.textContent = msg; }
  }
  function cmpTransport(e, wrap){
    var vids = activeCmpVideos(wrap), i;
    var k = e.key;
    if (k === "k" || k === "K"){
      e.preventDefault();
      pauseAll(wrap.querySelectorAll("video"));
      setTransport(wrap, "K ⏸ 暂停 pause");
    } else if (k === "l" || k === "L"){
      e.preventDefault();
      var rate = cmpRate(wrap);
      for (i = 0; i < vids.length; i++){
        try { vids[i].playbackRate = rate; } catch(err) {}  // a video refusing rate: swallowed
        vids[i].play().catch(function(){});
      }
      setTransport(wrap, "L ▶ 播放 play ×" + rate);
    } else if (k === "j" || k === "J"){
      // HONEST: <video> has no native reverse, so J is a SINGLE −1-frame step
      // (same exact period as ArrowLeft) — never a fake smooth-reverse interval
      // shuttle. The label says so, plainly.
      e.preventDefault();
      var pj = framePeriod(wrap);
      if (pj){ cmpSeek(wrap, -pj); }
      setTransport(wrap, "J: 逐帧回退 step-back (浏览器不支持倒放 no native reverse)");
    } else if (k === "ArrowLeft" || k === "ArrowRight"){
      e.preventDefault();
      var dir = (k === "ArrowRight") ? 1 : -1;
      if (e.shiftKey){
        cmpSeek(wrap, dir * 1);  // EXACT one second, signed
        setTransport(wrap, (dir > 0 ? "→ +1秒" : "← -1秒") + " 1s");
      } else {
        var pa = framePeriod(wrap);
        if (pa){ cmpSeek(wrap, dir * pa); }  // one exact frame period, shared math
        setTransport(wrap, (dir > 0 ? "→ +1帧" : "← -1帧") + " frame");
      }
    }
  }
  // Keyboard: Space toggles the focused <video> (frame.io/PlayPause convention);
  // FP X1 EXTENDS the SAME delegated listener with the review transport — active
  // ONLY when the event target is inside an OPEN .compare-wrap, and NEVER when the
  // user is typing in a form field (tagName guard). No second keydown listener.
  document.addEventListener("keydown", function(e){
    if (e.code === "Space" || e.key === " "){
      var a = document.activeElement;
      if (a && a.tagName === "VIDEO"){
        e.preventDefault();
        if (a.paused) { a.play().catch(function(){}); } else { a.pause(); }
      }
      return;
    }
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    var wrap = (t && t.closest) ? t.closest(".compare-wrap.open") : null;
    if (wrap){ cmpTransport(e, wrap); }
  });
})();
""".strip()

_SERVE_BODY = (
    '<div id="mj-banner" class="mj-banner"></div>\n'
    '<div id="mj-overlay" class="mj-overlay"><div class="mj-ovbox">'
    '<div class="mj-spinner"></div><div class="mj-ovtext">处理中…</div>'
    "</div></div>\n"
)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


# ---------------------------------------------------------------------------
# AI_IDE_16 §7 WP3 — 2D blocking, DERIVED from Shot source (NO 3D, no canvas
# truth): camera frame + subject box + line of action + movement arrow as a pure
# deterministic SVG string. The canvas is a VIEW; the Shot fields stay the truth.
# ---------------------------------------------------------------------------

_BLOCK_W, _BLOCK_H = 320, 180
# subject footprint (fraction of frame height) by shot size — a close-up fills
# the frame; a wide shot is a small figure.
_SHOT_SIZE_BOX = {
    "extreme_close_up": 0.86, "close_up": 0.68, "medium": 0.46,
    "wide": 0.26, "extreme_wide": 0.14,
}
# horizontal movement arrows (dx sign) and push/pull (scale) hints, keyed by
# common movement tokens; unknown movements draw no arrow (never guessed).
_MOVE_HINT = {
    "static": None,
    "pan_left": ("h", -1), "pan_right": ("h", 1),
    "truck_left": ("h", -1), "truck_right": ("h", 1),
    "tilt_up": ("v", -1), "tilt_down": ("v", 1),
    "dolly_in": ("z", 1), "push_in": ("z", 1), "zoom_in": ("z", 1),
    "dolly_out": ("z", -1), "pull_out": ("z", -1), "zoom_out": ("z", -1),
}


def blocking_svg(shot: Any) -> str:
    """A pure, deterministic 2D blocking diagram for one shot (§7 WP3).

    Derived ONLY from the Shot source camera/action fields — camera frame,
    a subject box sized by ``camera.shot_size`` and shifted by ``camera.angle``,
    a movement arrow from ``camera.movement``, and the action beat as the line
    of action label. No 3D, no persisted canvas coordinates: same shot → same
    bytes. Returns a self-contained ``<svg>`` string (a board asset)."""
    try:
        cam = shot.camera
        size = getattr(cam, "shot_size", "medium")
        movement = getattr(cam, "movement", "static") or "static"
        angle = getattr(cam, "angle", "eye_level") or "eye_level"
        action = (shot.action.main or "").strip()
        sid = shot.id
    except Exception:
        size, movement, angle, action, sid = "medium", "static", "eye_level", "", "?"

    w, h = _BLOCK_W, _BLOCK_H
    frac = _SHOT_SIZE_BOX.get(size, 0.46)
    box_h = h * frac
    box_w = box_h * 0.6
    cx = w / 2.0
    # angle shifts the subject vertically within the frame (low angle → subject
    # sits higher in frame, high angle → lower).
    cy = h / 2.0 + ({"low_angle": h * 0.12, "high_angle": -h * 0.12}.get(angle, 0.0))
    bx, by = cx - box_w / 2.0, cy - box_h / 2.0

    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        'xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="blocking {_esc(sid)}">',
        f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" fill="none" '
        'stroke="#888" stroke-width="1.5"/>',
        # rule-of-thirds guides
        f'<line x1="{w/3:.0f}" y1="0" x2="{w/3:.0f}" y2="{h}" stroke="#8883" />',
        f'<line x1="{2*w/3:.0f}" y1="0" x2="{2*w/3:.0f}" y2="{h}" stroke="#8883" />',
        # subject box + line of action (baseline through the subject)
        f'<line x1="0" y1="{cy:.0f}" x2="{w}" y2="{cy:.0f}" stroke="#4c9aff55" '
        'stroke-dasharray="4 3"/>',
        f'<rect x="{bx:.1f}" y="{by:.1f}" width="{box_w:.1f}" height="{box_h:.1f}" '
        'fill="#4c9aff33" stroke="#4c9aff" stroke-width="2" rx="4"/>',
        f'<circle cx="{cx:.1f}" cy="{by + box_h*0.22:.1f}" r="{box_w*0.22:.1f}" '
        'fill="none" stroke="#4c9aff" stroke-width="2"/>',
    ]

    hint = _MOVE_HINT.get(movement)
    if hint is not None:
        kind, sign = hint
        if kind == "h":
            y = cy
            x1, x2 = (cx - 55, cx + 55) if sign > 0 else (cx + 55, cx - 55)
            parts.append(
                f'<line x1="{x1:.0f}" y1="{y:.0f}" x2="{x2:.0f}" y2="{y:.0f}" '
                'stroke="#ffb020" stroke-width="2.5" marker-end="url(#ar)"/>')
        elif kind == "v":
            x = cx + box_w
            y1, y2 = (cy - 45, cy + 45) if sign > 0 else (cy + 45, cy - 45)
            parts.append(
                f'<line x1="{x:.0f}" y1="{y1:.0f}" x2="{x:.0f}" y2="{y2:.0f}" '
                'stroke="#ffb020" stroke-width="2.5" marker-end="url(#ar)"/>')
        else:  # z: push/pull — nested frame
            k = 26 if sign > 0 else -26
            parts.append(
                f'<rect x="{bx - k:.0f}" y="{by - k*box_h/box_w:.0f}" '
                f'width="{box_w + 2*k:.0f}" height="{box_h + 2*k*box_h/box_w:.0f}" '
                'fill="none" stroke="#ffb020" stroke-width="2" '
                'stroke-dasharray="5 4"/>')
    parts.append(
        '<defs><marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0 0 L10 5 L0 10 z" fill="#ffb020"/></marker></defs>')
    label = f"{size} · {movement} · {angle}"
    parts.append(
        f'<text x="6" y="{h - 8}" font-size="11" fill="#aaa" '
        f'font-family="monospace">{_esc(label)}</text>')
    if action:
        parts.append(
            f'<text x="6" y="15" font-size="11" fill="#ccc">'
            f'{_esc(action[:46])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _ladder_chips(project: "Project", shot_id: str) -> str:
    """AI_IDE_16 §6 read-only board view fields: the shot's DERIVED preview
    ladder stage, its keyframe-approval status, and the predicted next-step
    (video-layer) cost via the existing estimators. Serve-mode only — the static
    board bytes are unchanged."""
    try:
        from ..qc.production import ladder_view

        view = ladder_view(project, shot_id)
    except Exception:
        return ""
    stage = view.get("stage", "SCRIPT")
    kf = view.get("keyframe") or {}
    if not kf.get("has_candidates"):
        appr = "无关键帧 no keyframes"
        appr_cls = "st-manual"
    elif kf.get("adopted"):
        appr = f"关键帧已采纳 adopted ({_esc(kf.get('via'))})"
        appr_cls = "st-fresh"
    else:
        appr = f"关键帧待采纳 {kf.get('candidate_count', 0)} pending"
        appr_cls = "st-needs"
    nxt = view.get("next_step") or {}
    cost = nxt.get("estimated_cost")
    cur = nxt.get("currency") or ""
    cost_chip = (
        f'<span class="badge st-stale">下一步 next→video ≈ {_esc(cost)} {_esc(cur)}</span>'
        if cost else "")
    return (
        '<div class="ladder-chips">'
        f'<span class="badge st-fresh">ladder: {_esc(stage)}</span>'
        f'<span class="badge {appr_cls}">{appr}</span>'
        f"{cost_chip}"
        "</div>"
    )


def _fmt_duration(ms: int | None) -> str:
    if not ms or ms <= 0:
        return "—"
    total_s = ms / 1000.0
    m, s = divmod(total_s, 60)
    return f"{int(m):d}:{s:05.2f}"


def _short_hash(spec_hash: str | None) -> str:
    if not spec_hash:
        return "?"
    if spec_hash == "manual":
        return "manual"
    return spec_hash.removeprefix(HASH_PREFIX)[:12]


def _take_meta(project: "Project", take: Any) -> str:
    sc = take.sidecar
    dur = sc.probe.duration_ms if (sc.probe and sc.probe.duration_ms) else None
    bits = [
        f"provider: {_esc(sc.provider)}",
        f"spec: {_esc(_short_hash(sc.spec_hash))}",
        f"dur: {_esc(_fmt_duration(dur))}" if dur else "dur: —",
    ]
    return " · ".join(bits)


def _take_meta_full(take: Any) -> str:
    """Richer sidecar line for the compare grid: provider · cost · created ·
    spec · dur — everything the board already knows about a take (§4.3)."""
    sc = take.sidecar
    bits = [f"provider: {_esc(sc.provider)}", f"spec: {_esc(_short_hash(sc.spec_hash))}"]
    if sc.remote and sc.remote.cost:
        bits.append(f"cost: {_esc(sc.remote.cost)} {_esc(sc.remote.currency)}")
    if sc.created_at:
        bits.append(f"created: {_esc(sc.created_at)}")
    dur = sc.probe.duration_ms if (sc.probe and sc.probe.duration_ms) else None
    if dur:
        bits.append(f"dur: {_esc(_fmt_duration(dur))}")
    return " · ".join(bits)


def _verdict_of(note: str | None) -> str | None:
    """The one-key verdict a note *is* (exactly ``好``/``弃``), else ``None``."""
    return note if note in _VERDICT_LABELS else None


# --- review annotations (Wave 3 §5.5) — serve mode ONLY (static bytes pinned) --

_ANN_SEVERITY_CLASS = {"note": "ann-note", "issue": "ann-issue", "blocker": "ann-blocker"}
_ANN_STALE_BADGE = (
    '<span class="badge ann-stale" title="绑定的媒体哈希不再匹配 — 该 take 的媒体已被'
    '替换/重做,批注指向旧画面 (stored media_sha256 no longer matches the take '
    'file&#39;s current hash)">⚠ 陈旧 STALE</span>'
)


def _parse_rate_string(rate: str) -> tuple[int, int] | None:
    """``(num, den)`` from an annotation's exact rational rate string ("24" /
    "24000/1001" — what ``core.timebase.Rate.__str__`` emits). Parse-only, the
    inverse of the ONE formatter; ``None`` for anything unparsable (the row
    then honestly renders frames without seconds, never a guessed rate)."""
    try:
        num_s, _, den_s = str(rate).partition("/")
        num, den = int(num_s), int(den_s or "1")
        return (num, den) if num > 0 and den > 0 else None
    except (TypeError, ValueError):
        return None


def _ann_time_chip(ann: Any) -> str:
    """The frame/range chip: frames + seconds via the annotation's OWN bound
    rate, carrying ``data-seek="<seconds>"`` for the click-to-seek handler."""
    nd = _parse_rate_string(ann.frame_rate)
    if ann.range_frames is not None and len(ann.range_frames) == 2:
        start, end = ann.range_frames
        if nd:
            num, den = nd
            sa, sb = start * den / num, end * den / num
            return (f'<span class="ann-seek" data-seek="{sa:.3f}" '
                    f'title="点击定位 click to seek">f{start}–f{end} '
                    f"≈ {sa:.3f}–{sb:.3f}s</span>")
        return f'<span class="ann-seek">f{_esc(start)}–f{_esc(end)}</span>'
    if ann.frame is not None:
        if nd:
            num, den = nd
            sec = ann.frame * den / num
            return (f'<span class="ann-seek" data-seek="{sec:.3f}" '
                    f'title="点击定位 click to seek">f{ann.frame} ≈ {sec:.3f}s</span>')
        return f'<span class="ann-seek">f{_esc(ann.frame)}</span>'
    return ""


def _render_take_annotations(project: "Project", shot_id: str, take: Any,
                             anns: list[Any], rev: str,
                             rate_nd: tuple[int, int] | None) -> str:
    """Per-take annotations block (serve mode only): the list rows + the add
    form. STALE is computed AT RENDER TIME — the take file's CURRENT hash vs
    each annotation's stored ``media_sha256`` binding (takes are append-only,
    so a mismatch means the media was replaced/superseded). All user text
    (text/subject/actor/…) goes through :func:`_esc`."""
    rows = []
    if anns:
        current: str | None = None
        if take.media_path is not None and take.media_path.is_file():
            try:
                current = hash_file(take.media_path)
            except OSError:
                current = None
        for ann in anns:
            sev = ann.severity if ann.severity in _ANN_SEVERITY_CLASS else "note"
            chips = [f'<span class="badge {_ANN_SEVERITY_CLASS[sev]}">{_esc(sev)}</span>']
            time_chip = _ann_time_chip(ann)
            if time_chip:
                chips.append(time_chip)
            if not ann.matches_media(current):
                chips.append(_ANN_STALE_BADGE)
            subject = (f'<span class="ann-subject">[{_esc(ann.subject)}]</span> '
                       if ann.subject else "")
            rows.append(
                '<div class="ann-row">'
                f'<div class="ann-head">{"".join(chips)}</div>'
                f'<div class="ann-body">{subject}{_esc(ann.text)}</div>'
                f'<div class="ann-meta">{_esc(ann.actor)} · {_esc(ann.created_at)}</div>'
                "</div>"
            )
    # Browser-verified (F16 follow-up): the container renders even when EMPTY
    # — the client-side insert after the take's FIRST annotation targets
    # .ann-list, and `if rows else ""` made that first insert a silent no-op
    # (the row only appeared after a manual reload). Headless-Chromium run
    # caught it; HTTP tests could not.
    list_html = f'<div class="ann-list">{"".join(rows)}</div>'
    rate_attrs = (f' data-fps-num="{rate_nd[0]}" data-fps-den="{rate_nd[1]}"'
                  if rate_nd else "")
    form = (
        f'<div class="ann-form" data-shot="{_esc(shot_id)}" data-take="{_esc(take.name)}" '
        f'data-rev="{_esc(rev)}"{rate_attrs}>'
        '<input type="text" class="ann-text" maxlength="2000" '
        'placeholder="批注 annotate…">'
        '<select class="ann-sev">'
        '<option value="note">note 备注</option>'
        '<option value="issue">issue 问题</option>'
        '<option value="blocker">blocker 阻断</option>'
        "</select>"
        '<label class="ann-at"><input type="checkbox" class="ann-atframe" checked>'
        "当前帧 at frame</label>"
        '<button type="button" class="btn" data-annsubmit="1">批注 annotate</button>'
        "</div>"
    )
    return f'<div class="ann-wrap">{list_html}{form}</div>'


def _render_take_note(note: str | None) -> str:
    """Per-take director note (§R9/R10) as static board markup.

    Exactly ``好``/``弃`` renders a colour-coded verdict chip (styled like the
    build-state badges); any other text renders a truncated ``📝`` line with
    the full note in the ``title`` attribute. Note text is arbitrary director
    input, so it is HTML-escaped via :func:`_esc`.
    """
    if not note:
        return ""
    verdict = _verdict_of(note)
    if verdict is not None:
        return (f'<div class="tnote-row"><span class="badge {_VERDICT_CLASS[verdict]}">'
                f"{_VERDICT_LABELS[verdict]}</span></div>")
    text = note.strip()
    if not text:
        return ""
    shown = text if len(text) <= _NOTE_MAX else text[:_NOTE_MAX] + "…"
    return f'<div class="tnote" title="{_esc(text)}">📝 {_esc(shown)}</div>'


def _render_take(project: "Project", shot_id: str, take: Any, selected: bool,
                 serve: bool = False, note: str | None = None,
                 annotations: list[Any] | None = None, rev: str = "",
                 rate_nd: tuple[int, int] | None = None) -> str:
    cls = "take selected" if selected else "take"
    if take.media_path is not None:
        rel = _esc(project.relpath(take.media_path))
        src = f"/media/{rel}" if serve else rel
        # the QC content layer keeps a mid-point frame per shot (§9) — use it
        # as the poster so the card wall reads at a glance without playback
        poster_path = project.reports_dir / "frames" / f"{shot_id}.jpg"
        if selected and poster_path.exists():
            prel = _esc(project.relpath(poster_path))
            poster = f' poster="{("/media/" + prel) if serve else prel}"'
        else:
            poster = ""
        media = (f'<video controls preload="metadata" width="180"{poster} '
                 f'src="{src}"></video>')
    else:
        media = '<div class="nomedia">no media on disk</div>'
    star = ' <span class="star">★</span>' if selected else ""
    name = f'<div class="tname">{_esc(take.name)}{star}</div>'
    meta = f'<div class="tmeta">{_take_meta(project, take)}</div>'
    note_html = _render_take_note(note)
    if serve:
        cmd = _take_action(shot_id, take.name, selected)
        # Wave 3 (§5.5): per-take annotation list + add form — serve mode only,
        # so the static board stays byte-for-byte identical (its pin holds).
        cmd += _render_take_annotations(project, shot_id, take,
                                        annotations or [], rev, rate_nd)
    else:
        cmd = ""
        if not selected:
            line = f"manju select {shot_id} {take.name}"
            cmd = (
                '<div class="selcmd">'
                f"<code>{_esc(line)}</code>"
                '<button type="button" onclick="mjCopy(this)">copy</button>'
                "</div>"
            )
    return f'<div class="{cls}">{media}{name}{meta}{note_html}{cmd}</div>'


def _take_action(shot_id: str, take_name: str, selected: bool) -> str:
    """Serve-mode per-take button: '选用 select' (disabled + ★ on the current
    selection). Data-* attributes drive the delegated click handler in _SERVE_JS."""
    if selected:
        return ('<div class="tact">'
                '<button type="button" class="btn btn-sel" disabled>★ 已选用</button>'
                "</div>")
    return ('<div class="tact">'
            '<button type="button" class="btn" data-act="select" '
            f'data-shot="{_esc(shot_id)}" data-take="{_esc(take_name)}">选用 select</button>'
            "</div>")


def _render_shot(project: "Project", shot_id: str, status: Any,
                 serve: bool = False, rollbackable: bool = False,
                 rate_nd: tuple[int, int] | None = None) -> str:
    ann_by_take: dict[str, list[Any]] = {}
    try:
        shot = project.load_shot(shot_id)
        action = shot.action.main
        speaker = shot.dialogue.speaker
        dtext = shot.dialogue.text
        take_notes = dict(shot.status.take_notes)
        if serve:  # Wave 3 (§5.5): media-bound review annotations, per take
            for ann in shot.status.annotations:
                ann_by_take.setdefault(ann.take, []).append(ann)
    except Exception:
        action = speaker = dtext = ""
        take_notes = {}

    rev = ""
    if serve:
        # the CAS token the per-take annotate form round-trips (expected_rev),
        # so a page rendered before another entrance edited this shot refuses
        # cleanly instead of silently last-writer-winning.
        try:
            from ..core.writes import shot_text_hash

            rev = shot_text_hash(project, shot_id)
        except Exception:
            rev = ""

    badge_cls = _STATE_CLASS.get(status.state, "st-missing") if status else "st-missing"
    state_val = status.state.value if status else "unknown"
    note = status.note if (status and status.note) else ""
    selected = status.selected_take if status else None

    voice_html = ""
    try:  # voice chip (M3): mirror of the picture badge, advisory only
        from ..build.voice import VoiceState, evaluate_voice

        voice = evaluate_voice(project, project.load_shot(shot_id))
        if voice.state != VoiceState.NOT_NEEDED:
            voice_cls = {
                VoiceState.FRESH: "st-fresh", VoiceState.MANUAL: "st-manual",
                VoiceState.STALE: "st-stale", VoiceState.MISSING: "st-missing",
            }.get(voice.state, "st-missing")
            voice_html = (f'<span class="badge {voice_cls}" '
                          f'title="{_esc(voice.state.value)}">'
                          f"配音 {_esc(_STATE_ZH.get(voice.state.value, voice.state.value))}</span>")
    except Exception:
        voice_html = ""

    head = (
        '<div class="shot-head">'
        f'<span class="sid">{_esc(shot_id)}</span>'
        f'<span class="badge {badge_cls}" title="{_esc(state_val)}">'
        f"{_esc(_STATE_ZH.get(state_val, state_val))}</span>"
        f"{voice_html}"
        f'<span class="action">{_esc(action)}</span>'
        "</div>"
    )
    dialogue = ""
    if dtext or speaker:
        sp = f'<span class="speaker">{_esc(speaker)}:</span> ' if speaker else ""
        dialogue = f'<div class="dialogue">{sp}{_esc(dtext)}</div>'
    note_html = f'<div class="dialogue">{_esc(note)}</div>' if note else ""

    # review signal: the take the director committed to is itself marked 弃
    sel_reject_html = ""
    if selected is not None and _verdict_of(take_notes.get(selected)) == "弃":
        sel_reject_html = ('<div class="sel-reject">'
                           "⚠ 选中take已标弃 (selected take rejected)</div>")

    takes = project.takes(shot_id)
    if takes:
        cards = "".join(
            _render_take(project, shot_id, t, selected=(t.name == selected), serve=serve,
                         note=take_notes.get(t.name),
                         annotations=ann_by_take.get(t.name), rev=rev, rate_nd=rate_nd)
            for t in takes
        )
        takes_html = f'<div class="takes">{cards}</div>'
    else:
        takes_html = ('<div class="dialogue">还没有 take(no takes yet)— '
                      "生成或 manju import 后在此挑选</div>")

    # AI_IDE_16 §6/§7: read-only ladder chips + 2D blocking SVG. Serve-mode ONLY,
    # so the static board.html stays byte-for-byte identical (its pin holds).
    ladder_html = ""
    blocking_html = ""
    if serve:
        ladder_html = _ladder_chips(project, shot_id)
        try:
            blocking_html = ('<details class="blocking"><summary>2D blocking</summary>'
                             f"{blocking_svg(shot)}</details>")
        except Exception:
            blocking_html = ""

    actions_html = ""
    compare_html = ""
    if serve:
        btns = [
            '<button type="button" class="btn btn-redo" data-act="redo" '
            f'data-shot="{_esc(shot_id)}">重做 redo</button>'
        ]
        if rollbackable:
            btns.append(
                '<button type="button" class="btn btn-roll" data-act="rollback_shot" '
                f'data-shot="{_esc(shot_id)}">回滚 rollback</button>'
            )
        playable = [t for t in takes if t.media_path is not None]
        if playable:
            btns.append(
                '<button type="button" class="btn btn-playall" data-playall="1">'
                "▶ 播放全部 play all</button>"
            )
        if len(takes) >= 2:
            btns.append(
                '<button type="button" class="btn btn-cmp" data-compare="1">'
                "⧉ 对比 compare</button>"
            )
            compare_html = _render_compare(project, shot_id, takes, selected,
                                           rate_nd=rate_nd)
        actions_html = f'<div class="shot-actions">{"".join(btns)}</div>'

    return (f'<section class="shot">{head}{ladder_html}{sel_reject_html}'
            f"{dialogue}{note_html}{takes_html}{blocking_html}"
            f"{actions_html}{compare_html}</section>")


# FP T1 (user item 6): the default difference-view brightness gain. The canvas
# view multiplies |A-B| by this, so the label must always SAY it is amplified.
_DIFF_GAIN_DEFAULT = 4
_AMPLIFIED_LABEL = (f"差异已放大 amplified ×{_DIFF_GAIN_DEFAULT} — "
                    "非原始像素差 not raw pixel deltas")


def _frame_step_label(num: int, den: int) -> str:
    """Display-honest frame period: the EXACT fraction first (den/num seconds),
    the rounded milliseconds only as an ≈ convenience, and the rate spelled
    ``num/den`` whenever it is rational (never a rounded float posing as fps)."""
    period_ms = 1000.0 * den / num
    if den == 1:
        return f"1帧 frame = 1/{num} s ≈ {period_ms:.3f} ms @ {num} fps"
    return (f"1帧 frame = {den}/{num} s ≈ {period_ms:.3f} ms "
            f"@ {num}/{den} fps (exact rational)")


def _rate_info(project: "Project") -> tuple[int, int] | None:
    """The exact edit rate the compare stepper uses, as ``(num, den)``.

    Truth precedence mirrors what the board already reads, dispatched ONLY
    through the TYPED rate resolvers (the R1 grep pin keeps the raw declared
    field out of display surfaces like this one): the compiled timeline when
    one exists — ``Timeline.frame_rate`` resolves the R2 rational echo when
    present, else the int ``fps`` promoted exactly — otherwise the config's
    ``ProjectConfig.frame_rate`` (same truth precedence, same exactness).
    ``None`` when even that fails (broken project.yaml): the stepper is then
    honestly disabled, never fed a guessed rate."""
    try:
        timeline = project.load_timeline()
        rate = (timeline.frame_rate if timeline is not None
                else project.load_config().frame_rate)
        return rate.numerator, rate.denominator
    except Exception:
        return None


def _sidecar_duration_ms(take: Any) -> int | None:
    sc = take.sidecar
    return sc.probe.duration_ms if (sc.probe and sc.probe.duration_ms) else None


def _render_ab_block(project: "Project", take_a: Any, take_b: Any,
                     selected: str | None, rate_nd: tuple[int, int] | None) -> str:
    """The wipe/difference A/B stack (FP T1). All pixel work is client-side:
    the wipe is a CSS clip-path on the stacked top video, the difference is a
    <canvas> composite honestly labelled "amplified ×N". B is muted so the two
    stacked takes never double the audio."""
    rel_a = _esc(project.relpath(take_a.media_path))
    rel_b = _esc(project.relpath(take_b.media_path))
    star_a = " ★" if take_a.name == selected else ""
    star_b = " ★" if take_b.name == selected else ""

    # Duration honesty from the takes' EXISTING sidecar probe facts: a known
    # mismatch beyond one frame period is a server-rendered FACT label
    # (data-durfact). The client-side check (data-durwarn) covers unprobed
    # takes once the browser knows the real durations. Never silent.
    dur_fact = ""
    ms_a, ms_b = _sidecar_duration_ms(take_a), _sidecar_duration_ms(take_b)
    if ms_a and ms_b:
        num, den = rate_nd if rate_nd else (24, 1)
        if abs(ms_a - ms_b) > 1000.0 * den / num:
            dur_fact = (
                '<div class="cmp-durwarn" data-durfact="1">'
                f"⚠ 时长不同 durations differ: {_esc(take_a.name)} {ms_a / 1000.0:.3f}s"
                f" vs {_esc(take_b.name)} {ms_b / 1000.0:.3f}s — "
                "逐帧步进按各自时钟 frame steps run on each video's own clock</div>"
            )

    return (
        '<div class="cmp-ab">'
        '<div class="ab-stage">'
        f'<video class="ab-a" preload="metadata" src="/media/{rel_a}"></video>'
        f'<video class="ab-b" preload="metadata" muted src="/media/{rel_b}"></video>'
        '<canvas class="ab-canvas" data-diffcanvas="1"></canvas>'
        "</div>"
        '<div class="ab-controls">'
        f"<span>A: {_esc(take_a.name)}{star_a} · B: {_esc(take_b.name)}{star_b}</span>"
        '<label class="ab-wipectl">擦除 wipe '
        '<input type="range" data-wipe="1" min="0" max="100" value="50"></label>'
        '<label class="ab-gainctl">增益 gain '
        f'<input type="range" data-diffgain="1" min="1" max="16" step="1" '
        f'value="{_DIFF_GAIN_DEFAULT}"></label>'
        f'<span class="ab-gainlabel" data-gainlabel="1">{_AMPLIFIED_LABEL}</span>'
        # FP V2: onion-skin opacity for the stacked B video (shown only in
        # onion mode via CSS). Pure CSS opacity — no canvas for the pair.
        '<label class="ab-onionctl">洋葱皮 onion '
        '<input type="range" data-onion="1" min="0" max="100" value="50"></label>'
        '<span class="ab-onionlabel" data-onionlabel="1">B 叠加 over A @ 50% — '
        "洋葱皮 onion-skin(仅视图 view-only)</span>"
        "</div>"
        f"{dur_fact}"
        '<div class="cmp-durwarn" data-durwarn hidden></div>'
        "</div>"
    )


# FP V2 (user item 6, "scopes"): the PERMANENT honesty label. It is BINDING —
# it states that the readings come from browser-decoded RGB, from the current
# paused frame of video A only, that they are view-only, the exact Rec.709 luma
# formula, that they are NOT the engine's color facts (QC colorstats remain the
# measurement authority), and that they redraw only for parked frames (never a
# play-time loop). The canvas is a VIEW, never a fact source; nothing is written.
_SCOPES_LABEL = (
    "示波器 scopes:读数取自浏览器解码的 RGB(browser-decoded RGB),"
    "来自视频 A 的当前暂停帧(current paused frame of video A only)—— 仅供查看 view-only。"
    "亮度用 Rec.709 luma:Y = 0.2126·R + 0.7152·G + 0.0722·B。"
    "这不是引擎的颜色事实(not the engine's color facts):"
    "QC colorstats remain the measurement authority。"
    "仅在暂停 / 逐帧步进 / 滑块时重绘,播放时绝不连续刷新"
    "(redrawn on pause / frame-step / slider only, never looped while playing "
    "— scopes are for parked frames)。"
)


def _render_scopes() -> str:
    """The scopes panel (FP V2): collapsed by default, a Rec.709 luma histogram
    and a per-column luma waveform of video A's CURRENT PARKED frame, drawn
    CLIENT-SIDE from browser-decoded RGB. VIEW-ONLY — the canvas is never a fact
    source (the permanent :data:`_SCOPES_LABEL` says exactly this). Two canvases,
    an honest-degradation note slot, and a toggle wired to the delegated
    click/redraw listeners. Only emitted when the A/B stack (video A) exists."""
    return (
        '<div class="cmp-scopes-wrap">'
        '<button type="button" class="btn btn-mode" data-scopes="1">'
        "📊 示波器 scopes</button>"
        '<div class="cmp-scopes">'
        f'<p class="cmp-scopes-label">{_SCOPES_LABEL}</p>'
        '<div class="cmp-scope-row">'
        '<div class="cmp-scope-cell">'
        '<div class="cmp-scope-cap">亮度直方图 luma histogram</div>'
        '<canvas class="cmp-scope-canvas" data-scope-hist="1"></canvas>'
        "</div>"
        '<div class="cmp-scope-cell">'
        '<div class="cmp-scope-cap">亮度波形 luma waveform(逐列 per-column)</div>'
        '<canvas class="cmp-scope-canvas" data-scope-wave="1"></canvas>'
        "</div>"
        "</div>"
        '<p class="cmp-scopes-note" data-scope-note hidden></p>'
        "</div></div>"
    )


def _render_compare(project: "Project", shot_id: str, takes: list[Any],
                    selected: str | None,
                    rate_nd: tuple[int, int] | None = None) -> str:
    """Serve-mode take comparison (frame.io-style compare view).

    The original synced side-by-side grid (a responsive 2-up/3-up of ALL the
    shot's takes, one synchronized play/pause, per-take metadata + select)
    stays exactly as it was; FP T1 EXTENDS it with a mode toggle — side-by-side
    | wipe (CSS clip-path slider) | difference (client-side canvas, honest
    "amplified ×N" label) — and pause-synced ±1-frame stepping that carries the
    EXACT frame period (``data-fps-num``/``data-fps-den``; the R2 rational
    timeline echo when the project is rational)."""
    poster_path = project.reports_dir / "frames" / f"{shot_id}.jpg"
    poster_rel = _esc(project.relpath(poster_path)) if poster_path.exists() else ""
    cells = []
    for take in takes:
        is_sel = (take.name == selected)
        if take.media_path is not None:
            rel = _esc(project.relpath(take.media_path))
            poster = f' poster="/media/{poster_rel}"' if poster_rel else ""
            video = (f'<video class="cmp-video" controls preload="metadata"{poster} '
                     f'src="/media/{rel}"></video>')
        else:
            video = '<div class="nomedia">no media on disk</div>'
        star = ' <span class="star">★</span>' if is_sel else ""
        cells.append(
            '<div class="cmp-cell">'
            f'<div class="cmp-name">{_esc(take.name)}{star}</div>'
            f"{video}"
            f'<div class="cmp-meta">{_take_meta_full(take)}</div>'
            f"{_take_action(shot_id, take.name, is_sel)}"
            "</div>"
        )

    # A/B pair for wipe/difference: the selected take (when it has media) vs
    # the first OTHER take with media. Fewer than two playable takes ⇒ the two
    # pixel modes are honestly unavailable — a labelled note, never a broken
    # stack or a silent fallback.
    playable = [t for t in takes if t.media_path is not None]
    take_a = next((t for t in playable if t.name == selected), playable[0] if playable else None)
    take_b = next((t for t in playable if take_a is not None and t.name != take_a.name), None)

    mode_btns = ['<button type="button" class="btn btn-mode active" '
                 'data-cmpmode="sbs">并排 side-by-side</button>']
    ab_html = ""
    scopes_html = ""
    if take_a is not None and take_b is not None:
        mode_btns.append('<button type="button" class="btn btn-mode" '
                         'data-cmpmode="wipe">擦除 wipe</button>')
        mode_btns.append('<button type="button" class="btn btn-mode" '
                         'data-cmpmode="diff">差异 difference</button>')
        # FP V2: onion skin — a fourth mode on the SAME A/B stack (B over A at
        # CSS opacity). The scopes panel (video A's parked frame) rides along.
        mode_btns.append('<button type="button" class="btn btn-mode" '
                         'data-cmpmode="onion">洋葱皮 onion</button>')
        ab_html = _render_ab_block(project, take_a, take_b, selected, rate_nd)
        scopes_html = _render_scopes()
    else:
        mode_btns.append('<span class="cmp-ab-unavail">擦除/差异/洋葱皮不可用 — '
                         "need two takes with media</span>")

    # Frame-lock stepping: ±1 frame at the EXACT period. Unknown rate ⇒ the
    # buttons are omitted and the label says so (display honesty, no guess).
    if rate_nd is not None:
        num, den = rate_nd
        step_html = (
            f'<button type="button" class="btn btn-mode" data-framestep="-1">⏮ -1帧</button>'
            f'<button type="button" class="btn btn-mode" data-framestep="1">+1帧 ⏭</button>'
            f'<span class="cmp-rate">{_esc(_frame_step_label(num, den))}</span>'
        )
        rate_attrs = f' data-fps-num="{num}" data-fps-den="{den}"'
    else:
        step_html = ('<span class="cmp-rate">fps 未知 unknown — '
                     "逐帧步进不可用 frame stepping unavailable</span>")
        rate_attrs = ""

    head = (
        '<div class="compare-head">'
        '<button type="button" class="btn" data-syncplay="1">▶ 同步播放 sync play</button>'
        f"{''.join(mode_btns)}"
        f"{step_html}"
        '<span class="compare-hint">同步播放本镜头所有备选 · 空格键播放/暂停聚焦的视频</span>'
        # FP X1: the review-transport discoverability line — every key spelled out,
        # bilingual, so the pro grammar is never a hidden chord. The live status
        # label to its right echoes the current key/rate as the reviewer drives.
        '<span class="compare-hint cmp-transport-hint">评审快捷键 review keys · '
        'K=暂停 pause · L=播放 play(倍速 rate ×1→2→4) · '
        'J=逐帧回退 step-back(无原生倒放 no native reverse) · '
        '←/→=±1帧 frame · Shift+←/→=±1秒 1s · '
        '仅在打开的对比区内 only inside an open compare</span>'
        '<span class="cmp-transport" data-transport aria-live="polite"></span>'
        "</div>"
    )
    return (f'<div class="compare-wrap" data-mode="sbs" '
            f'data-gain="{_DIFF_GAIN_DEFAULT}"{rate_attrs}>{head}'
            f'<div class="compare-grid">{"".join(cells)}</div>'
            f"{ab_html}{scopes_html}</div>")


# ------------------------------------------------------------- boundary view
# FP T1 (user item 6): for consecutive shots in index order, shot N's LAST
# frame vs shot N+1's FIRST frame — the cut the director actually judges. The
# stills are extracted through the EXISTING media/frames.extract_frame service
# (content-addressed cache under .manju/frames — §3 disposable, `manju gc`
# wipes it, NEVER a build input); this module adds no extractor and no fact
# source. Serve-mode only: the static board stays byte-for-byte identical.

# Past-any-clip sentinel for "the last frame": extract_frame clamps the seek
# inside the clip BEFORE deriving its cache key (round-W #36), so one huge
# at_ms resolves to exactly the real last frame with exactly one cache entry.
_BOUNDARY_END_MS = 10**10
_BOUNDARY_STILL_W = 480  # modest preview width; aspect kept by the extractor


def _boundary_still(project: "Project", shot_id: str,
                    selected: str | None, *, last: bool) -> tuple[str | None, str | None]:
    """Resolve one boundary still for ``shot_id``'s SELECTED take.

    Returns ``(project-relative jpg, None)`` on success, else ``(None, honest
    reason)`` — a missing selection, missing media or a failed extraction each
    name themselves; nothing here ever raises into the page render."""
    if not selected:
        return None, "未选用 take (no selected take)"
    take = project.get_take(shot_id, selected)
    if take is None:
        return None, f"选用的 take 不存在 (selected take '{selected}' not found)"
    if take.media_path is None:
        return None, "媒体缺失 (no media on disk)"
    try:
        from ..media.frames import extract_frame

        jpg = extract_frame(project, project.relpath(take.media_path),
                            _BOUNDARY_END_MS if last else 0,
                            width=_BOUNDARY_STILL_W)
        return project.relpath(jpg), None
    except Exception as exc:
        reason = " ".join(str(exc).split())[:160]
        return None, f"抽帧失败 (frame extraction failed): {reason}"


def _boundary_cell(shot_id: str, take_name: str | None, rel: str | None,
                   reason: str | None, side_label: str) -> str:
    cap = f"{shot_id} · {side_label}" + (f" · {take_name}" if take_name else "")
    if rel is not None:
        media = f'<img class="bnd-img" src="/media/{_esc(rel)}" alt="{_esc(cap)}">'
    else:
        media = f'<div class="bnd-missing">{_esc(reason)}</div>'
    return f'<div class="bnd-cell"><div class="bnd-lab">{_esc(cap)}</div>{media}</div>'


def _render_boundary(project: "Project", statuses: dict[str, Any]) -> str:
    """The cut-boundary section (serve mode): one row per adjacent shot pair,
    ending still vs starting still, with a client-side difference toggle
    (canvas view, honest amplified label). Every degraded slot carries its
    reason; a wholly broken section degrades to a note, never a 500."""
    order = project.shot_ids()
    if len(order) < 2:
        return ""
    rows = []
    for left, right in zip(order, order[1:]):
        sel_l = getattr(statuses.get(left), "selected_take", None)
        sel_r = getattr(statuses.get(right), "selected_take", None)
        rel_l, why_l = _boundary_still(project, left, sel_l, last=True)
        rel_r, why_r = _boundary_still(project, right, sel_r, last=False)
        tools = ""
        onion_stage = ""
        if rel_l is not None and rel_r is not None:
            tools = (
                '<button type="button" class="btn btn-mode" data-bnddiff="1">'
                "差异 difference</button>"
                '<label class="ab-gainctl">增益 gain '
                f'<input type="range" data-diffgain="1" min="1" max="16" step="1" '
                f'value="{_DIFF_GAIN_DEFAULT}"></label>'
                f'<span class="ab-gainlabel" data-gainlabel="1">{_AMPLIFIED_LABEL}</span>'
                # FP V2: onion toggle + opacity slider for this boundary pair.
                '<button type="button" class="btn btn-mode" data-bndonion="1">'
                "洋葱皮 onion</button>"
                '<label class="bnd-onionctl">不透明度 opacity '
                '<input type="range" data-bndonion-op="1" min="0" max="100" '
                'value="50"></label>'
            )
            # FP V2: the stacked onion stage — the next shot's FIRST still (B)
            # over this shot's LAST still (A) at CSS opacity. Reuses the SAME
            # cached stills; distinct classes leave the diff canvas untouched.
            onion_stage = (
                '<div class="bnd-onion" aria-hidden="true">'
                f'<img class="bnd-onion-img bnd-onion-a" src="/media/{_esc(rel_l)}" alt="">'
                f'<img class="bnd-onion-img bnd-onion-b" src="/media/{_esc(rel_r)}" alt="">'
                "</div>"
            )
        rows.append(
            f'<div class="bnd-row" data-gain="{_DIFF_GAIN_DEFAULT}">'
            '<div class="bnd-head">'
            f'<span class="bnd-pair">{_esc(left)} → {_esc(right)}</span>{tools}'
            "</div>"
            '<div class="bnd-grid">'
            f"{_boundary_cell(left, sel_l, rel_l, why_l, '末帧 last frame')}"
            f"{_boundary_cell(right, sel_r, rel_r, why_r, '首帧 first frame')}"
            '<canvas class="bnd-canvas" data-bndcanvas="1"></canvas>'
            "</div>"
            f"{onion_stage}"
            "</div>"
        )
    return (
        '<section class="shot boundary">'
        "<h2>剪辑点 Cut boundaries — 已选结尾 vs 下一镜开头 "
        "(accepted ending vs next start)</h2>"
        '<p class="mj-note">帧取自各镜头已选用 take(现有抽帧缓存,可随时删除);'
        "差异/洋葱皮视图为客户端合成/叠加,仅供查看 view-only — 画布不是事实来源。</p>"
        f"{''.join(rows)}</section>"
    )


def _boundary_section(project: "Project", statuses: dict[str, Any]) -> str:
    """Failure containment for the boundary view — same stance as
    :func:`_safe_panel`: a broken section is a note, never a dead board."""
    try:
        return _render_boundary(project, statuses)
    except Exception as exc:
        reason = " ".join(str(exc).split())[:160]
        return ('<section class="shot boundary"><h2>剪辑点 Cut boundaries</h2>'
                f'<p class="mj-note">边界视图暂不可用 (boundary view unavailable): '
                f"{_esc(reason)}</p></section>")


def _rollbackable_shots(project: "Project", statuses: dict[str, Any]) -> set[str]:
    """Shots that `rollback_shot` could act on right now: they have an earlier
    recorded selection (select/rollback_shot event) different from the current
    one — the exact condition manju.core.history.rollback_shot checks (§10)."""
    from ..core.events import tail_events

    picks: dict[str, list[str]] = {}
    for ev in tail_events(project.root, n=10_000):
        if ev.get("action") in ("select", "rollback_shot"):
            detail = ev.get("detail") or {}
            shot, take = detail.get("shot"), detail.get("take")
            if shot and take:
                picks.setdefault(str(shot), []).append(str(take))
    out: set[str] = set()
    for shot, plist in picks.items():
        st = statuses.get(shot)
        current = (getattr(st, "selected_take", None) or "") if st else ""
        if any(t != current for t in plist):
            out.add(shot)
    return out


def _normalize_qc_items(data: Any) -> list[dict[str, Any]]:
    """Coerce a qc.json payload into a flat list of item dicts, defensively."""
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        raw = None
        for key in ("items", "issues", "problems", "checks", "results"):
            if isinstance(data.get(key), list):
                raw = data[key]
                break
        if raw is None:
            raw = []
    else:
        raw = []
    items: list[dict[str, Any]] = []
    for it in raw:
        if isinstance(it, dict):
            items.append(it)
        else:
            items.append({"message": it})
    return items


def _qc_level(item: dict[str, Any]) -> str:
    for key in ("level", "severity", "status"):
        v = item.get(key)
        if v:
            return str(v).lower()
    return "info"


def _qc_message(item: dict[str, Any]) -> str:
    for key in ("message", "msg", "detail", "text", "description", "name"):
        v = item.get(key)
        if v:
            return str(v)
    return str(item)


def _render_qc(project: "Project") -> str:
    qc_path = project.reports_dir / "qc.json"
    if not qc_path.exists():
        return ""
    try:
        data = read_json(qc_path)
    except Exception:
        return '<section class="qc"><h2>QC</h2><p>qc.json is unreadable.</p></section>'
    items = _normalize_qc_items(data)

    counts: dict[str, int] = {}
    for it in items:
        lvl = _qc_level(it)
        counts[lvl] = counts.get(lvl, 0) + 1
    counts_html = "".join(
        f"<span>{_esc(lvl)}: {n}</span>" for lvl, n in sorted(counts.items())
    ) or "<span>no items</span>"

    lis = []
    for it in items[:20]:
        lvl = _qc_level(it)
        lis.append(
            f'<li><span class="lvl lvl-{_esc(lvl)}">{_esc(lvl)}</span>{_esc(_qc_message(it))}</li>'
        )
    more = ""
    if len(items) > 20:
        more = f"<li>… {len(items) - 20} more</li>"
    list_html = f"<ul>{''.join(lis)}{more}</ul>" if lis else "<p>No QC items.</p>"

    return (
        '<section class="qc"><h2>QC report</h2>'
        f'<div class="counts">{counts_html}</div>{list_html}</section>'
    )


def _render_header(project: "Project", serve: bool = False) -> str:
    try:
        config = project.load_config()
        name, width, height = config.name, config.width, config.height
        fps, mode = config.fps, config.mode
    except Exception:
        name, width, height, fps, mode = project.root.name, "?", "?", "?", "?"

    shot_count = len(project.shot_ids())
    timeline = project.load_timeline()
    dur = _fmt_duration(timeline.duration_ms) if timeline else "—"

    # render verdicts from the explainer (read-only): the director sees at a
    # glance whether the next build would re-render or skip
    verdicts = ""
    try:
        from ..build.explain import explain

        renders = explain(project).get("renders", {})
        chips = []
        for target in ("final", "proxy"):
            if isinstance(renders.get(target), dict):
                verdict = renders[target]["verdict"]
                cls = "st-fresh" if verdict.startswith("skip") else "st-stale"
                chips.append(f'<span class="badge {cls}">{_esc(target)}: '
                             f"{_esc(verdict)}</span>")
        verdicts = "".join(chips)
    except Exception:
        verdicts = ""

    toolbar = ""
    if serve:
        toolbar = (
            '<div class="board-actions">'
            '<button type="button" class="btn" data-act="build" data-target="final">'
            "构建 build</button>"
            '<button type="button" class="btn" data-act="qc">质检 qc</button>'
            '<button type="button" class="btn" data-act="package">打包 package</button>'
            '<button type="button" class="btn" data-act="export">导出 export</button>'
            '<button type="button" class="btn" data-act="snapshot">快照 snapshot</button>'
            "</div>"
        )

    return (
        '<header class="board">'
        f"<h1>{_esc(name)}</h1>"
        '<div class="meta">'
        f"<span>{_esc(width)}×{_esc(height)}</span>"
        f"<span>{_esc(fps)} fps</span>"
        f"<span>mode: {_esc(mode)}</span>"
        f"<span>shots: {shot_count}</span>"
        f"<span>duration: {_esc(dur)}</span>"
        f"{verdicts}"
        f"</div>{toolbar}</header>"
    )


# ------------------------------------------------------------- serve panels
# Tabbed, server-rendered inspector sections (§10 handover surfaces). Each is
# regenerated per request like the board — no client-side data fetching. A
# broken/empty section degrades to a friendly note, never taking the board down.

_PANEL_TABS = [
    ("project", "项目 Project"),
    ("subs", "字幕 Subtitles"),
    ("bible", "圣经 Bible"),
    ("log", "日志 Log"),
    ("assets", "资产 Assets"),
    ("qc", "QC"),
]


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _srt_timecode(ms: int) -> str:
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _safe_panel(fn) -> str:
    try:
        return fn()
    except Exception as exc:  # a single broken panel must not 500 the board
        return f'<p class="mj-note">面板暂不可用 (panel unavailable): {_esc(exc)}</p>'


def _render_project_panel(project: "Project") -> str:
    from ..build.status import project_status

    st = project_status(project)
    budget = st.get("budget_limit")
    spend = f'{st.get("total_cost", 0)} {st.get("currency", "")}'
    if budget:
        spend += f' / 预算 budget {budget}'
    by_state = st.get("shots_by_state") or {}
    by_state_txt = ", ".join(f"{k}: {len(v)}" for k, v in by_state.items()) or "—"
    voice = st.get("voice_by_state") or {}
    voice_txt = ", ".join(f"{k}: {len(v)}" for k, v in voice.items()) or "—"
    qc = st.get("qc")
    if qc:
        qc_txt = f'ok={qc.get("ok")} · errors={qc.get("errors", "?")} · warns={qc.get("warnings", "?")}'
    else:
        qc_txt = "尚未质检 (no qc.json)"
    tl = st.get("timeline") or {}
    tl_txt = "有 present" if tl.get("exists") else "无 (先 build)"
    rows = [
        ("下一步 next step", st.get("next_step", "—")),
        ("模式 mode", st.get("mode", "—")),
        ("分辨率 resolution", st.get("resolution", "—")),
        ("预设 preset", st.get("preset", "—")),
        ("镜头 shots", st.get("shots_total", 0)),
        ("按状态 by state", by_state_txt),
        ("配音 voice", voice_txt),
        ("花费 spend", spend),
        ("时间线 timeline", tl_txt),
        ("成片 latest final", st.get("latest_final") or "—"),
        ("QC", qc_txt),
    ]
    dl = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in rows)
    return f'<h3>项目状态 Project status</h3><dl class="mj-dl">{dl}</dl>'


def _srt_table(cues: list[Any]) -> str:
    if not cues:
        return '<p class="mj-note">无字幕行 (no cues).</p>'
    body = "".join(
        f'<tr><td class="num">{i}</td>'
        f'<td class="mj-cue-time">{_srt_timecode(c.start_ms)} → {_srt_timecode(c.end_ms)}</td>'
        f"<td>{_esc(c.text)}</td></tr>"
        for i, c in enumerate(cues, 1)
    )
    return ('<table class="mj-table"><thead><tr><th>#</th><th>时间 timing</th>'
            f"<th>字幕 text</th></tr></thead><tbody>{body}</tbody></table>")


def _render_subs_panel(project: "Project") -> str:
    from ..providers.asr import parse_srt

    srt_path = project.captions_dir / "captions.srt"
    if not srt_path.exists():
        return ('<h3>字幕 Subtitles</h3>'
                '<p class="mj-note">还没有 captions.srt — 先 <code>manju build</code>。</p>')
    manual = False
    try:
        manual = project.load_rules().captions.mode == "manual"
    except Exception:
        manual = False
    out = ["<h3>字幕 Subtitles</h3>"]
    if manual:
        out.append('<div class="mj-manual">⚠ MANUAL 模式:captions.srt 为人工真相,'
                    "编译版仅供对比,不覆盖手改内容 (§3)。</div>")
    cues = parse_srt(srt_path.read_text(encoding="utf-8"))
    out.append('<p class="mj-note">captions.srt (只读 read-only)</p>')
    out.append(_srt_table(cues))
    gen_path = project.captions_dir / "captions.generated.srt"
    if manual and gen_path.exists():
        gen_cues = parse_srt(gen_path.read_text(encoding="utf-8"))
        out.append('<p class="mj-note">captions.generated.srt (编译版对比 compiled, read-only)</p>')
        out.append(_srt_table(gen_cues))
    return "".join(out)


def _render_bible_panel(project: "Project") -> str:
    out = ["<h3>圣经 Bible</h3>",
           '<p class="mj-note">characters / scenes / style (只读 read-only · 🔒 = locked)</p>']
    any_entry = False
    for fname, label in (("characters", "角色 characters"),
                         ("scenes", "场景 scenes"),
                         ("style", "风格 style")):
        path = project.root / "bible" / f"{fname}.yaml"
        if not path.exists():
            continue
        data = read_yaml(path) or {}
        if not isinstance(data, dict) or not data:
            continue
        out.append(f'<h4 style="color:var(--muted);margin:.9rem 0 .4rem">{_esc(label)}</h4>')
        for entry_id, entry in data.items():
            if not isinstance(entry, dict):
                continue
            any_entry = True
            locked = entry.get("locked") or {}
            locked_keys = set(locked.keys()) if isinstance(locked, dict) else set(locked)
            name = entry.get("name")
            title = f"{_esc(entry_id)}" + (f" · {_esc(name)}" if name else "")
            lines = []
            for key, value in entry.items():
                if key in ("locked", "name"):
                    continue
                lock = ' <span class="mj-lock" title="locked">🔒</span>' if key in locked_keys else ""
                lines.append(f"<dt>{_esc(key)}{lock}</dt><dd>{_esc(value)}</dd>")
            body = f'<dl class="mj-dl">{"".join(lines)}</dl>' if lines else ""
            out.append(f'<div class="mj-bible-entry"><h4>{title}</h4>{body}</div>')
    if not any_entry:
        out.append('<p class="mj-note">Bible 为空 (no entries yet).</p>')
    return "".join(out)


def _render_log_panel(project: "Project") -> str:
    from ..core.events import tail_events

    events = tail_events(project.root, 50)
    out = ["<h3>日志 Log</h3>",
           '<p class="mj-note">events.jsonl 最近 50 条 · 每次打开页面自动刷新 '
           "(auto-refresh on load)</p>"]
    if not events:
        out.append('<p class="mj-note">暂无事件 (no events yet).</p>')
        return "".join(out)
    rows = []
    for ev in reversed(events):  # newest first
        detail = ev.get("detail") or {}
        dtxt = ", ".join(f"{k}={v}" for k, v in detail.items()) if isinstance(detail, dict) else str(detail)
        rows.append(
            f'<tr><td class="mj-cue-time">{_esc(ev.get("ts", ""))}</td>'
            f'<td>{_esc(ev.get("actor", ""))}</td>'
            f'<td><b>{_esc(ev.get("action", ""))}</b></td>'
            f"<td>{_esc(dtxt)}</td></tr>"
        )
    out.append('<table class="mj-table"><thead><tr><th>ts</th><th>actor</th>'
               f'<th>action</th><th>detail</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')
    return "".join(out)


def _render_assets_panel(project: "Project") -> str:
    out = ["<h3>资产 Assets · media/imports</h3>"]
    imports_dir = project.imports_dir
    thumbs_dir = project.runtime_dir / "thumbs"
    files = sorted(p for p in imports_dir.glob("*") if p.is_file()) if imports_dir.exists() else []
    if not files:
        out.append('<p class="mj-note">media/imports 为空 (no imports yet).</p>')
    else:
        for f in files:
            preview = ""
            for cand in (thumbs_dir / f"{f.stem}.jpg", thumbs_dir / f"{f.stem}_wave.png"):
                if cand.exists():
                    preview = f'<img src="/media/{_esc(project.relpath(cand))}" alt="preview">'
                    break
            try:
                size = _human_size(f.stat().st_size)
            except OSError:
                size = "—"
            out.append(f'<div class="mj-asset">{preview}<span>{_esc(f.name)}</span>'
                       f'<span class="sz">{_esc(size)}</span></div>')
    out.append('<p class="mj-sacred">🔒 media/imports 是神圣的:引擎从不修改或删除导入文件 '
               "(imports are sacred — never modified or deleted, §3)。</p>")
    return "".join(out)


def _qc_suggestion(item: dict[str, Any]) -> str:
    for key in ("suggestion", "fix", "hint"):
        v = item.get(key)
        if v:
            return str(v)
    return ""


def _render_qc_panel(project: "Project") -> str:
    qc_path = project.reports_dir / "qc.json"
    if not qc_path.exists():
        return ('<h3>QC</h3><p class="mj-note">尚未质检 — 点击顶部 “质检 qc” '
                "或运行 <code>manju build --target qc</code>。</p>")
    try:
        data = read_json(qc_path)
    except Exception:
        return '<h3>QC</h3><p class="mj-note">qc.json 无法读取 (unreadable).</p>'
    items = _normalize_qc_items(data)
    counts: dict[str, int] = {}
    for it in items:
        lvl = _qc_level(it)
        counts[lvl] = counts.get(lvl, 0) + 1
    counts_html = "".join(
        f"<span>{_esc(lvl)}: {n}</span>" for lvl, n in sorted(counts.items())
    ) or "<span>no items</span>"
    lis = []
    for it in items:
        lvl = _qc_level(it)
        subj = it.get("subject") or it.get("shot") or ""
        subj_html = (f' <span style="color:var(--muted)">[{_esc(subj)}]</span>'
                     if subj else "")
        sugg = _qc_suggestion(it)
        sugg_html = (f'<span class="qc-sugg">↳ 建议 suggestion: {_esc(sugg)}</span>'
                     if sugg else "")
        lis.append(
            f'<li><span class="lvl lvl-{_esc(lvl)}">{_esc(lvl)}</span>'
            f"{_esc(_qc_message(it))}{subj_html}{sugg_html}</li>"
        )
    list_html = f"<ul>{''.join(lis)}</ul>" if lis else '<p class="mj-note">No QC items.</p>'
    # wrap in .qc so the existing count-pill / list styling (static _CSS) applies
    return (f'<div class="qc"><h3>QC report</h3>'
            f'<div class="counts">{counts_html}</div>{list_html}</div>')


def _render_panels(project: "Project") -> str:
    renderers = {
        "project": lambda: _render_project_panel(project),
        "subs": lambda: _render_subs_panel(project),
        "bible": lambda: _render_bible_panel(project),
        "log": lambda: _render_log_panel(project),
        "assets": lambda: _render_assets_panel(project),
        "qc": lambda: _render_qc_panel(project),
    }
    tabbar = "".join(
        f'<button type="button" class="mj-tab{" active" if i == 0 else ""}" '
        f'data-tab="{key}">{label}</button>'
        for i, (key, label) in enumerate(_PANEL_TABS)
    )
    panels = "".join(
        f'<div class="mj-panel{" active" if i == 0 else ""}" data-panel="{key}">'
        f"{_safe_panel(renderers[key])}</div>"
        for i, (key, _label) in enumerate(_PANEL_TABS)
    )
    return f'<section class="mj-panels"><div class="mj-tabs">{tabbar}</div>{panels}</section>'


def render_board(project: "Project", serve: bool = False, token: str = "") -> str:
    """Build the board HTML document.

    ``serve=False`` (default) is the static, self-contained board — byte-for-byte
    what ``manju board`` has always written (pinned by a test). ``serve=True`` is
    the live workspace served by :mod:`manju.board.server`: media/poster ``src``
    point at ``/media/<relpath>``, per-take/-shot/header action buttons appear, an
    inline vanilla-JS layer POSTs to ``/api/<action>`` with a busy overlay, and a
    tabbed inspector (project/subtitles/bible/log/assets/QC) rides above the shots.
    FP T1 (user item 6) extends the serve-mode compare with wipe/difference modes
    and frame-lock stepping, and appends the cut-boundary section (ending vs next
    start stills via the existing frames cache) after the shots.

    ``token`` (Round Y, review #12) is the server's per-run token; in serve mode
    it is embedded as ``MANJU_TOKEN`` so the board's own ``post()`` sends it in
    the ``X-Manju-Token`` header. The static board never carries it.
    """
    statuses = {s.shot_id: s for s in evaluate_all(project)}
    rollbackable = _rollbackable_shots(project, statuses) if serve else set()
    # FP T1: the exact edit rate for frame-lock stepping, resolved ONCE per
    # render (timeline echo first — rational-aware — else the project rate).
    rate_nd = _rate_info(project) if serve else None

    shots_html = "".join(
        _render_shot(project, sid, statuses.get(sid), serve=serve,
                     rollbackable=(sid in rollbackable), rate_nd=rate_nd)
        for sid in project.shot_ids()
    )
    if not shots_html:
        shots_html = ('<p style="color:#9aa0aa">还没有镜头(no shots yet)— '
                      "用 manju gui 的创作页或直接编辑 shots/*.yaml,"
                      "然后 manju check 校验。</p>")

    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    footer = f'<footer class="board">Generated by manju board · {_esc(generated)}</footer>'

    css = (_CSS + "\n" + _SERVE_CSS) if serve else _CSS
    css = css.replace("MANJU_ASPECT", _project_aspect(project))
    script = (_JS + "\n" + _SERVE_JS) if serve else _JS
    if serve:
        # Prepend the token as a JS const the serve-mode post() sends. Only in
        # serve mode, so the static board stays byte-for-byte identical.
        script = f"var MANJU_TOKEN={json.dumps(token)};\n" + script
    body_extras = _SERVE_BODY if serve else ""

    # In serve mode the QC section moves into the panel strip (above the shots)
    # and the FP T1 cut-boundary view rides after them; static mode keeps the
    # standalone QC section appended after the shots so the pinned board stays
    # byte-for-byte identical.
    if serve:
        main_inner = (f"{_render_panels(project)}{shots_html}"
                      f"{_boundary_section(project, statuses)}")
    else:
        main_inner = f"{shots_html}{_render_qc(project)}"

    return (
        "<!doctype html>\n"
        '<html lang="zh"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(project.load_config().name if (project.root / 'project.yaml').exists() else project.root.name)} · manju board</title>\n"
        f"<style>{css}</style>\n"
        "</head><body>\n"
        f"{body_extras}"
        f"{_render_header(project, serve=serve)}\n"
        f"<main>{main_inner}</main>\n"
        f"{footer}\n"
        f"<script>{script}</script>\n"
        "</body></html>\n"
    )


def generate_board(project: "Project") -> Path:
    """Render ``<project root>/board.html`` (static mode) and return its path."""
    out = project.root / "board.html"
    atomic_write_text(out, render_board(project, serve=False))
    return out
