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
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..build.stale import ShotState, evaluate_all
from ..core.hashing import HASH_PREFIX
from ..core.yamlio import atomic_write_text, read_json

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
    rollback_shot: "正在回滚该镜头…"
  };
  function el(id){ return document.getElementById(id); }
  function overlay(show, msg){
    var o = el("mj-overlay"); if(!o) return;
    o.querySelector(".mj-ovtext").textContent = msg || "处理中…";
    o.style.display = show ? "flex" : "none";
  }
  function banner(msg){
    var b = el("mj-banner"); if(!b) return;
    b.textContent = msg || ""; b.style.display = msg ? "block" : "none";
  }
  function post(action, body){
    banner("");
    overlay(true, MSG[action] || "处理中…");
    fetch("/api/" + action, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body || {})
    }).then(function(r){
      return r.json().catch(function(){
        return {ok:false, error:"服务器返回了无法解析的响应 (HTTP " + r.status + ")"};
      });
    }).then(function(d){
      if (d && d.ok) { location.reload(); return; }
      overlay(false);
      banner("✗ " + ((d && d.error) || "操作失败"));
    }).catch(function(err){
      overlay(false);
      banner("✗ 请求失败:" + err);
    });
  }
  document.addEventListener("click", function(e){
    var btn = e.target.closest ? e.target.closest("button[data-act]") : null;
    if (!btn || btn.disabled) return;
    var body = {};
    if (btn.dataset.shot) body.shot = btn.dataset.shot;
    if (btn.dataset.take) body.take = btn.dataset.take;
    if (btn.dataset.target) body.target = btn.dataset.target;
    post(btn.getAttribute("data-act"), body);
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


def _render_take(project: "Project", shot_id: str, take: Any, selected: bool,
                 serve: bool = False) -> str:
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
    return f'<div class="{cls}">{media}{name}{meta}{cmd}</div>'


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
    except Exception:
        action = speaker = dtext = ""

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

    takes = project.takes(shot_id)
    if takes:
        cards = "".join(
            _render_take(project, shot_id, t, selected=(t.name == selected), serve=serve)
            for t in takes
        )
        takes_html = f'<div class="takes">{cards}</div>'
    else:
        takes_html = '<div class="dialogue">no takes yet</div>'

    actions_html = ""
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
        actions_html = f'<div class="shot-actions">{"".join(btns)}</div>'

    return (f'<section class="shot">{head}{dialogue}{note_html}'
            f"{takes_html}{actions_html}</section>")


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


def render_board(project: "Project", serve: bool = False) -> str:
    """Build the board HTML document.

    ``serve=False`` (default) is the static, self-contained board — byte-for-byte
    what ``manju board`` has always written (pinned by a test). ``serve=True`` is
    the live workspace served by :mod:`manju.board.server`: media/poster ``src``
    point at ``/media/<relpath>``, per-take/-shot/header action buttons appear, and
    an inline vanilla-JS layer POSTs to ``/api/<action>`` with a busy overlay.
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
    body_extras = _SERVE_BODY if serve else ""

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
        f"<main>{shots_html}{_render_qc(project)}</main>\n"
        f"{footer}\n"
        f"<script>{script}</script>\n"
        "</body></html>\n"
    )


def generate_board(project: "Project") -> Path:
    """Render ``<project root>/board.html`` (static mode) and return its path."""
    out = project.root / "board.html"
    atomic_write_text(out, render_board(project, serve=False))
    return out
