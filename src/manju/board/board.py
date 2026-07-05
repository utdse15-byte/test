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

__all__ = ["generate_board"]

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
  width: 100%; aspect-ratio: 9/16; display: flex; align-items: center; justify-content: center;
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


def _render_take(project: "Project", shot_id: str, take: Any, selected: bool) -> str:
    cls = "take selected" if selected else "take"
    if take.media_path is not None:
        rel = _esc(project.relpath(take.media_path))
        media = f'<video controls preload="metadata" width="180" src="{rel}"></video>'
    else:
        media = '<div class="nomedia">no media on disk</div>'
    star = ' <span class="star">★</span>' if selected else ""
    name = f'<div class="tname">{_esc(take.name)}{star}</div>'
    meta = f'<div class="tmeta">{_take_meta(project, take)}</div>'
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


def _render_shot(project: "Project", shot_id: str, status: Any) -> str:
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

    head = (
        '<div class="shot-head">'
        f'<span class="sid">{_esc(shot_id)}</span>'
        f'<span class="badge {badge_cls}">{_esc(state_val)}</span>'
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
            _render_take(project, shot_id, t, selected=(t.name == selected)) for t in takes
        )
        takes_html = f'<div class="takes">{cards}</div>'
    else:
        takes_html = '<div class="dialogue">no takes yet</div>'

    return f'<section class="shot">{head}{dialogue}{note_html}{takes_html}</section>'


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


def _render_header(project: "Project") -> str:
    try:
        config = project.load_config()
        name, width, height = config.name, config.width, config.height
        fps, mode = config.fps, config.mode
    except Exception:
        name, width, height, fps, mode = project.root.name, "?", "?", "?", "?"

    shot_count = len(project.shot_ids())
    timeline = project.load_timeline()
    dur = _fmt_duration(timeline.duration_ms) if timeline else "—"

    return (
        '<header class="board">'
        f"<h1>{_esc(name)}</h1>"
        '<div class="meta">'
        f"<span>{_esc(width)}×{_esc(height)}</span>"
        f"<span>{_esc(fps)} fps</span>"
        f"<span>mode: {_esc(mode)}</span>"
        f"<span>shots: {shot_count}</span>"
        f"<span>duration: {_esc(dur)}</span>"
        "</div></header>"
    )


def generate_board(project: "Project") -> Path:
    """Render ``<project root>/board.html`` and return its path."""
    statuses = {s.shot_id: s for s in evaluate_all(project)}

    shots_html = "".join(
        _render_shot(project, sid, statuses.get(sid)) for sid in project.shot_ids()
    )
    if not shots_html:
        shots_html = '<p style="color:#9aa0aa">No shots yet.</p>'

    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    footer = f'<footer class="board">Generated by manju board · {_esc(generated)}</footer>'

    doc = (
        "<!doctype html>\n"
        '<html lang="zh"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_esc(project.load_config().name if (project.root / 'project.yaml').exists() else project.root.name)} · manju board</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head><body>\n"
        f"{_render_header(project)}\n"
        f"<main>{shots_html}{_render_qc(project)}</main>\n"
        f"{footer}\n"
        f"<script>{_JS}</script>\n"
        "</body></html>\n"
    )

    out = project.root / "board.html"
    atomic_write_text(out, doc)
    return out
