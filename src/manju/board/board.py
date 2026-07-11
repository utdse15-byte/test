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
from ..core.hashing import HASH_PREFIX
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
  display: none; background: #4d1f22; color: #ff8a90; padding: .7rem 1.6rem;
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
    export: "正在导出草稿/字幕…"
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
  function post(action, body){
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
  }
  function toggleCompare(btn){
    var section = btn.closest("section.shot"); if(!section) return;
    var wrap = section.querySelector(".compare-wrap"); if(!wrap) return;
    var open = wrap.classList.toggle("open");
    btn.classList.toggle("active", open);
    if(!open){ pauseAll(wrap.querySelectorAll("video")); }
  }
  function pauseAll(vids){ for (var i = 0; i < vids.length; i++){ vids[i].pause(); } }
  function anyPlaying(vids){
    for (var i = 0; i < vids.length; i++){ if(!vids[i].paused && !vids[i].ended){ return true; } }
    return false;
  }
  function syncPlay(btn){
    var wrap = btn.closest(".compare-wrap"); if(!wrap) return;
    var vids = wrap.querySelectorAll("video");
    if (anyPlaying(vids)){
      pauseAll(vids);
      btn.textContent = "▶ 同步播放 sync play";
    } else {
      for (var i = 0; i < vids.length; i++){
        try { vids[i].currentTime = 0; } catch(e) {}   // align starts for a fair compare
        vids[i].play().catch(function(){});
      }
      btn.textContent = "⏸ 同步暂停 pause all";
    }
  }
  function playAll(btn){
    var section = btn.closest("section.shot"); if(!section) return;
    var vids = section.querySelectorAll(".takes video");
    if (anyPlaying(vids)){ pauseAll(vids); }
    else { for (var i = 0; i < vids.length; i++){ vids[i].play().catch(function(){}); } }
  }
  document.addEventListener("click", function(e){
    var t = e.target, hit;
    if ((hit = t.closest && t.closest("[data-tab]"))){ switchTab(hit.getAttribute("data-tab")); return; }
    if ((hit = t.closest && t.closest("[data-compare]"))){ toggleCompare(hit); return; }
    if ((hit = t.closest && t.closest("[data-syncplay]"))){ syncPlay(hit); return; }
    if ((hit = t.closest && t.closest("[data-playall]"))){ playAll(hit); return; }
    var btn = t.closest ? t.closest("button[data-act]") : null;
    if (!btn || btn.disabled) return;
    var body = {};
    if (btn.dataset.shot) body.shot = btn.dataset.shot;
    if (btn.dataset.take) body.take = btn.dataset.take;
    if (btn.dataset.target) body.target = btn.dataset.target;
    post(btn.getAttribute("data-act"), body);
  });
  // Keyboard: space toggles the focused <video> (frame.io/PlayPause convention).
  document.addEventListener("keydown", function(e){
    if (e.code !== "Space" && e.key !== " ") return;
    var a = document.activeElement;
    if (a && a.tagName === "VIDEO"){
      e.preventDefault();
      if (a.paused) { a.play().catch(function(){}); } else { a.pause(); }
    }
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
                 serve: bool = False, note: str | None = None) -> str:
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
                 serve: bool = False, rollbackable: bool = False) -> str:
    try:
        shot = project.load_shot(shot_id)
        action = shot.action.main
        speaker = shot.dialogue.speaker
        dtext = shot.dialogue.text
        take_notes = dict(shot.status.take_notes)
    except Exception:
        action = speaker = dtext = ""
        take_notes = {}

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
            voice_html = (f'<span class="badge {voice_cls}">'
                          f"配音 {_esc(voice.state.value)}</span>")
    except Exception:
        voice_html = ""

    head = (
        '<div class="shot-head">'
        f'<span class="sid">{_esc(shot_id)}</span>'
        f'<span class="badge {badge_cls}">{_esc(state_val)}</span>'
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
                         note=take_notes.get(t.name))
            for t in takes
        )
        takes_html = f'<div class="takes">{cards}</div>'
    else:
        takes_html = '<div class="dialogue">no takes yet</div>'

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
            compare_html = _render_compare(project, shot_id, takes, selected)
        actions_html = f'<div class="shot-actions">{"".join(btns)}</div>'

    return (f'<section class="shot">{head}{ladder_html}{sel_reject_html}'
            f"{dialogue}{note_html}{takes_html}{blocking_html}"
            f"{actions_html}{compare_html}</section>")


def _render_compare(project: "Project", shot_id: str, takes: list[Any],
                    selected: str | None) -> str:
    """Serve-mode side-by-side take comparison (frame.io-style compare view):
    a responsive 2-up/3-up grid of the shot's takes, each large with its
    sidecar metadata + select button, driven by one synchronized play/pause."""
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
    head = (
        '<div class="compare-head">'
        '<button type="button" class="btn" data-syncplay="1">▶ 同步播放 sync play</button>'
        '<span class="compare-hint">同步播放本镜头所有备选 · 空格键播放/暂停聚焦的视频</span>'
        "</div>"
    )
    return (f'<div class="compare-wrap">{head}'
            f'<div class="compare-grid">{"".join(cells)}</div></div>')


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

    ``token`` (Round Y, review #12) is the server's per-run token; in serve mode
    it is embedded as ``MANJU_TOKEN`` so the board's own ``post()`` sends it in
    the ``X-Manju-Token`` header. The static board never carries it.
    """
    statuses = {s.shot_id: s for s in evaluate_all(project)}
    rollbackable = _rollbackable_shots(project, statuses) if serve else set()

    shots_html = "".join(
        _render_shot(project, sid, statuses.get(sid), serve=serve,
                     rollbackable=(sid in rollbackable))
        for sid in project.shot_ids()
    )
    if not shots_html:
        shots_html = '<p style="color:#9aa0aa">No shots yet.</p>'

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

    # In serve mode the QC section moves into the panel strip (above the shots);
    # static mode keeps the standalone QC section appended after the shots so the
    # pinned board stays byte-for-byte identical.
    if serve:
        main_inner = f"{_render_panels(project)}{shots_html}"
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
