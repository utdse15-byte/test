"""剪辑 EDIT — the clip-level finishing page (round T).

The seventh server-rendered workbench page (sibling of :mod:`manju.gui.pages`):
clip-level finishing WITHOUT opening JianYing — order, trim, footage audio,
transitions and looks, every one clickable in ``manju gui``.

Like the round-S pages this is a full HTML document produced by a pure render
function: the timeline order, the applied-transition state (from the last
build's ``final_vN.transitions.json``), the current default transition, the look
preset and the per-shot footage-audio levels are all baked into the DOM
server-side, so a plain GET carries the real content. ``/edit.js`` layers on the
inspector panel, the scrub strip, the plan-modal rebuild flow and the up/down
reorder — every mutation going through the SAME engine core the CLI calls
(``set_inout_take``, ``apply_mixer``, ``rules.transition_default``, the
``bible/style.yaml`` look) and landing the SAME event.

Design stance mirrors :mod:`manju.gui.pages`:

  * CSP-safe — CSS/JS in ``/edit.css`` + ``/edit.js``, no inline handlers, no
    inline ``style=`` (dynamic geometry via the CSSOM); every mutating request
    carries ``X-Manju-Token``.
  * XSS-safe — all server-supplied text is ``html.escape``-d; the script only
    writes ``textContent``.
  * frame previews are LAZY — the cards and scrub strips are ``<img>`` pointing
    at ``/edit/frame`` / ``/edit/strip`` / ``/edit/look`` (served from the
    disposable ``.manju/frames`` cache); the page GET never shells out to
    ffmpeg, so it serves 200 with or without ffmpeg installed.

Per-boundary transition OVERRIDES are not a data path in the compiler today
(transitions come uniformly from ``rules.transition_default``); the page is
honest about that — it offers a GLOBAL default picker and shows each boundary's
last-build applied/degraded state, and says "全局默认;逐切换点覆盖待后续".
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..core.models import LOOK_PRESETS, TRANSITION_TYPES

__all__ = [
    "EDIT_PATHS",
    "render_edit",
    "render_edit_css",
    "render_edit_js",
    "look_preview_frame",
    "transitions_state",
    "set_duration_in_text",
    "STRIP_COUNT",
]

# The routes this module owns (server.py delegates GET here).
EDIT_PATHS = frozenset({"/edit"})

STRIP_COUNT = 8  # scrub-strip thumbnails per take

_TRANSITION_LABELS = {
    "cut": "硬切",
    "fade": "黑场淡变",
    "xfade_fade": "交叉溶解",
    "xfade_slideleft": "左滑",
    "xfade_slideright": "右滑",
    "xfade_wipeleft": "左擦除",
    "xfade_circleopen": "圆形展开",
}
_LOOK_LABELS = {
    "none": "原色",
    "warm": "暖调",
    "cool": "冷调",
    "bw": "黑白",
    "film": "胶片",
    "vivid": "浓郁",
}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def render_edit_css() -> str:
    return _EDIT_CSS


def render_edit_js() -> str:
    return _EDIT_JS


# --------------------------------------------------------------- read helpers


def transitions_state(project: Any) -> dict[str, dict] | None:
    """The last build's per-boundary transition report, keyed ``"A->B"``.

    ``None`` before any final exists (markers show "—"); an empty dict when a
    final exists but wrote no transitions sidecar (an all-fade/cut build — every
    boundary took the applied default). Read-only and best-effort.
    """
    try:
        newest = project.newest_final_path()
    except Exception:
        newest = None
    if newest is None:
        return None
    sidecar = newest.with_suffix(".transitions.json")
    if not sidecar.exists():
        return {}
    try:
        from ..core.yamlio import read_json

        data = read_json(sidecar)
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for e in (data.get("transitions") or []) if isinstance(data, dict) else []:
        if isinstance(e, dict) and e.get("boundary"):
            out[str(e["boundary"])] = e
    return out


def _mixer_by_shot(project: Any) -> dict[str, dict]:
    try:
        from ..build.mixer import read_mixer

        return {str(s["shot"]): s for s in (read_mixer(project).get("shots") or [])
                if isinstance(s, dict) and s.get("shot")}
    except Exception:
        return {}


def _unbuilt(project: Any) -> tuple[bool, str]:
    """Explain-style dirty check: does a rebuild have work to do? Reuses the
    same ``build.explain`` verdicts the SPA build panel reads. Best-effort."""
    try:
        from ..build.explain import explain

        info = explain(project)
        tl = info.get("timeline") or {}
        renders = info.get("renders") or {}
        verdict = str(tl.get("verdict") or "")
        final = renders.get("final") or {} if isinstance(renders, dict) else {}
        fverdict = str(final.get("verdict") or "")
        if verdict.startswith("recompile"):
            return True, "时间线待重编 · " + verdict
        if verdict.startswith("compile"):
            return True, "尚无时间线 · " + verdict
        if fverdict.startswith("re-render"):
            return True, "成片待渲染 · " + fverdict
        if fverdict.startswith("render "):
            return True, "尚无成片 · " + fverdict
    except Exception:
        pass
    return False, ""


def _selected_take(project: Any, sid: str):
    """(TakeInfo|None, selected_name|None) for a shot — the take the scrub strip
    and trim act on. Degrades to (None, None) on any read error."""
    try:
        shot = project.load_shot(sid)
    except Exception:
        return None, None
    name = shot.status.selected_take
    if not name:
        return None, None
    try:
        take = project.get_take(sid, name)
    except Exception:
        take = None
    return take, name


def _take_relpath(project: Any, take: Any) -> str | None:
    if take is None or getattr(take, "media_path", None) is None:
        return None
    try:
        return project.relpath(take.media_path)
    except Exception:
        return None


def set_duration_in_text(text: str, value: str) -> str:
    """Rewrite ONLY the top-level ``duration:`` line (insert if absent), leaving
    every other byte — comments, locked fields — intact. ``value`` is ``auto``
    or a bare number string. Mirrors pages.set_strategy_in_text discipline so a
    duration edit never round-trips (and never re-normalizes) the rest of the
    shot file (§3)."""
    import re as _re

    lines = text.split("\n") if text else []
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        if _re.match(r"^duration\s*:", line):
            lines[i] = f"duration: {value}"
            return "\n".join(lines)
    # insert after id: (or at top) so it reads naturally
    anchor = None
    for i, line in enumerate(lines):
        if _re.match(r"^id\s*:", line):
            anchor = i
    lines.insert((anchor + 1) if anchor is not None else 0, f"duration: {value}")
    return "\n".join(lines)


# ---------------------------------------------------------- look preview frame


def look_preview_frame(project: Any, source_relpath: str, at_ms: int,
                       preset: str, intensity: float,
                       *, width: int | None = None) -> Path:
    """One frame of ``source_relpath`` rendered through a look preset — the
    before/after twin of :func:`media.frames.extract_frame`.

    A no-op look (``none`` / intensity 0) falls straight back to the canonical
    raw extractor (so raw and preview share one cache). Otherwise it splices the
    EXACT filter chain the final render bakes (``render._look_filter``) into a
    single-frame ffmpeg grab and content-addresses the result under
    ``.manju/frames`` (keyed by source hash + ts + preset + intensity), so a
    re-open is free and a preview matches the eventual final look precisely.
    """
    from ..core.models import LookSpec
    from ..media.frames import extract_frame

    look = LookSpec(preset=preset, intensity=float(intensity))  # validates bounds
    from ..media.render import _look_filter

    look_chain = _look_filter(look)
    if not look_chain:  # none / intensity 0 — reuse the raw extractor + its cache
        return extract_frame(project, source_relpath, at_ms, width=width)

    from ..core.hashing import cache_key, hash_file, short_hash
    from ..media.ffmpeg import MediaError, atomic_output, default_log, run_ffmpeg
    from ..media.frames import _resolve_source, frames_cache_dir
    from ..media.probe import probe

    abspath = _resolve_source(project, source_relpath)
    at_ms = max(0, int(at_ms))
    key = short_hash(cache_key(hash_file(abspath), "look", at_ms, width,
                               look.preset, look.intensity))
    cache = frames_cache_dir(project.root)
    dest = cache / f"{key}.jpg"
    if dest.exists():
        return dest

    info = probe(abspath)
    seek_ms = at_ms
    if info.duration_ms:
        rate = int(round(info.fps)) if info.fps else 24
        frame_len = -(-1000 // max(1, rate))
        seek_ms = min(at_ms, max(0, info.duration_ms - frame_len))
    vf = (f"scale={int(width)}:-2," + look_chain) if width else look_chain
    log = default_log(project.root, "frames")
    cache.mkdir(parents=True, exist_ok=True)
    with atomic_output(dest) as tmp:
        run_ffmpeg(
            ["-ss", f"{max(0.0, seek_ms / 1000.0):.3f}", "-i", abspath,
             "-frames:v", "1", "-vf", vf, "-update", "1", "-q:v", "3", str(tmp)],
            log=log)
        if not Path(tmp).is_file() or Path(tmp).stat().st_size == 0:
            raise MediaError(
                f"no look-preview frame from {source_relpath} at {at_ms}ms")
    return dest


# ------------------------------------------------------------------- rendering


def _nav(active: str) -> str:
    from .pages import nav_html

    return nav_html(active)


def _shell(title: str, token: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="zh">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/edit.css">\n'
        '<script src="/edit.js" defer></script>\n'
        "</head>\n"
        '<body data-page="/edit">\n'
        + _nav("/edit")
        + "\n<main>\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        '<div id="ed-modal" class="ed-modal hidden"><div class="ed-modal-card">'
        '<h2 id="ed-modal-title"></h2><div id="ed-modal-body"></div>'
        '<div class="btnrow"><button class="btn ghost" id="ed-modal-cancel">取消</button>'
        '<button class="btn" id="ed-modal-ok">确认</button></div></div></div>\n'
        "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


def render_edit(project: Any, token: str, query: dict[str, list[str]]) -> str:
    """The 剪辑 EDIT page: a horizontal timeline strip + per-clip inspector."""
    shots = project.shot_ids()
    rules = _load_rules(project)
    td = getattr(rules, "transition_default", None)
    td_type = getattr(td, "type", None) or "cut"
    td_dur = getattr(td, "duration_ms", None) or 0
    look = _load_look(project)
    tstate = transitions_state(project)
    mixer = _mixer_by_shot(project)
    unbuilt, unbuilt_why = _unbuilt(project)

    out: list[str] = []
    # ---- header + rebuild affordance
    out.append('<div class="page-h">')
    out.append("<h1>剪辑 Edit</h1>")
    if unbuilt:
        out.append(f'<span class="chip ed-dirty" title="{_e(unbuilt_why)}">有未构建的修改</span>')
    out.append('<button class="btn ghost" id="ed-rebuild">重新构建 (rebuild)</button>')
    out.append("</div>")
    out.append('<p class="muted ed-sub">剪辑级收尾:顺序 · 裁剪 · 素材声 · 转场 · 调色 — '
               "全部无需打开剪映。所有修改走同一引擎核心与事件。</p>")

    # ---- transitions default + look pickers
    out.append(_transitions_panel(td_type, td_dur))
    out.append(_look_panel(look))

    # ---- timeline strip
    out.append('<section class="panel ed-tl-panel">')
    out.append("<h2>时间线 Timeline</h2>")
    if not shots:
        out.append('<p class="muted">还没有分镜 (no shots yet)。</p>')
    else:
        out.append('<div class="ed-strip" id="ed-strip">')
        for idx, sid in enumerate(shots):
            take, sel = _selected_take(project, sid)
            rel = _take_relpath(project, take)
            out.append(_clip_card(project, idx, len(shots), sid, rel, sel))
            if idx < len(shots) - 1:
                out.append(_boundary_marker(shots[idx], shots[idx + 1], tstate,
                                            td_type))
        out.append("</div>")
    out.append("</section>")

    # ---- per-shot inspectors (hidden; JS reveals the clicked one)
    for sid in shots:
        take, sel = _selected_take(project, sid)
        rel = _take_relpath(project, take)
        out.append(_inspector(project, sid, rel, sel, mixer.get(sid, {})))

    return _shell("剪辑 Edit", token, "\n".join(out))


def _clip_card(project: Any, idx: int, n: int, sid: str,
               rel: str | None, sel: str | None) -> str:
    parts = [f'<div class="ed-card" data-shot="{_e(sid)}">']
    # thumb (lazy — first frame of the selected take, or a placeholder)
    if rel:
        src = "/edit/frame?take=" + quote(rel, safe="") + "&ms=0&w=200"
        parts.append(f'<img class="ed-thumb" loading="lazy" alt="" src="{_e(src)}">')
    else:
        parts.append('<div class="ed-thumb ed-thumb-ph">无 take</div>')
    dur = _shot_duration_label(project, sid)
    parts.append(
        '<div class="ed-card-meta">'
        f'<span class="ed-card-id">{idx + 1}. {_e(sid)}</span>'
        f'<span class="ed-card-dur">{_e(dur)}</span>'
        f'<span class="ed-card-take muted">{_e(sel or "未选 take")}</span>'
        "</div>")
    # reorder (up/down) + open inspector
    parts.append('<div class="ed-card-ctl">')
    parts.append(f'<button class="btn mini ed-up" data-shot="{_e(sid)}" '
                 f'{"disabled" if idx == 0 else ""} title="上移">▲</button>')
    parts.append(f'<button class="btn mini ed-down" data-shot="{_e(sid)}" '
                 f'{"disabled" if idx == n - 1 else ""} title="下移">▼</button>')
    parts.append(f'<button class="btn mini ed-open" data-shot="{_e(sid)}">编辑</button>')
    parts.append("</div></div>")
    return "".join(parts)


def _boundary_marker(a: str, b: str, tstate: dict | None, default_type: str) -> str:
    pair = f"{a}->{b}"
    label = _TRANSITION_LABELS.get(default_type, default_type)
    if tstate is None:
        glyph, cls, title = "—", "", "尚未构建 (no build yet)"
    elif pair in tstate:
        e = tstate[pair]
        req = str(e.get("requested") or default_type)
        label = _TRANSITION_LABELS.get(req, req)
        if e.get("applied"):
            glyph, cls = "✓", "ok"
            title = f"已应用 · 手柄 {e.get('half_ms', '?')}ms/侧"
        else:
            glyph, cls = "✗", "bad"
            title = "降级为硬切 · " + str(e.get("reason") or "unknown")
    else:
        # built, but this boundary wasn't an xfade — it took the applied default
        glyph, cls, title = "✓", "ok", "应用默认转场 (applied default)"
    return (f'<div class="ed-bound {cls}" title="{_e(title)}">'
            f'<span class="ed-bound-glyph">{glyph}</span>'
            f'<span class="ed-bound-label">{_e(label)}</span></div>')


def _inspector(project: Any, sid: str, rel: str | None, sel: str | None,
               mix: dict) -> str:
    p = [f'<section class="ed-inspector hidden panel" data-shot="{_e(sid)}">']
    p.append(f'<div class="ed-insp-head"><h2>编辑 {_e(sid)}</h2>'
             '<button class="btn ghost mini ed-close">关闭</button></div>')

    # --- Trim (scrub strip + in/out) ---
    p.append('<div class="ed-block"><h3>裁剪 Trim</h3>')
    if rel:
        p.append('<div class="ed-scrub">')
        for i in range(STRIP_COUNT):
            src = ("/edit/strip?take=" + quote(rel, safe="")
                   + f"&i={i}&n={STRIP_COUNT}&w=140")
            p.append(f'<img class="ed-scrub-f" loading="lazy" alt="" '
                     f'data-i="{i}" src="{_e(src)}">')
        p.append("</div>")
        p.append(
            '<div class="ed-trim-row">'
            f'<label>入点 in (ms) <input class="ed-in ed-num" type="number" '
            'min="0" step="10" value="0"></label>'
            f'<label>出点 out (ms) <input class="ed-out ed-num" type="number" '
            'min="0" step="10" value="1000"></label>'
            f'<button class="btn ed-trim" data-shot="{_e(sid)}">裁剪成新 take</button>'
            "</div>")
        p.append('<p class="muted ed-hint">追加式:裁剪生成新 take,原 take 不动;'
                 '完成后在下方选用。</p>')
        p.append('<div class="ed-trim-out muted" data-shot="' + _e(sid) + '"></div>')
    else:
        p.append('<p class="muted">该分镜还没有选用的 take,无法裁剪。先在审片页选一个。</p>')

    # --- takes list (so a freshly-minted trim take can be selected) ---
    p.append('<div class="ed-takes">')
    try:
        takes = project.takes(sid)
    except Exception:
        takes = []
    if takes:
        for t in takes:
            active = (t.name == sel)
            cls = "ed-take active" if active else "ed-take"
            btn = ("<span class=\"muted\">已选用</span>" if active else
                   f'<button class="btn ghost mini ed-select" data-shot="{_e(sid)}" '
                   f'data-take="{_e(t.name)}">选用</button>')
            p.append(f'<div class="{cls}"><span class="mono">{_e(t.name)}</span>{btn}</div>')
    else:
        p.append('<span class="muted">无 take</span>')
    p.append("</div></div>")

    # --- Footage audio ---
    gain = float(mix.get("gain_db", 0.0) or 0.0)
    muted = bool(mix.get("mute", False))
    p.append('<div class="ed-block"><h3>素材声 Footage audio</h3>')
    p.append(
        '<div class="ed-audio-row">'
        f'<label>增益 gain <input class="ed-gain" type="range" min="-30" max="12" '
        f'step="0.5" value="{gain:g}"> <span class="ed-gain-val">{gain:g} dB</span></label>'
        f'<label class="ed-mute-lbl"><input class="ed-mute" type="checkbox" '
        f'{"checked" if muted else ""}> 静音 mute</label>'
        f'<button class="btn ed-audio-apply" data-shot="{_e(sid)}">应用</button>'
        "</div></div>")

    # --- Duration override ---
    dur = _shot_duration_raw(project, sid)
    p.append('<div class="ed-block"><h3>时长 Duration</h3>')
    p.append(
        '<div class="ed-dur-row">'
        f'<label><input class="ed-dur-auto" type="checkbox" '
        f'{"checked" if dur == "auto" else ""}> 自动 auto</label>'
        f'<label>秒 <input class="ed-dur-val ed-num" type="number" min="0" step="0.1" '
        f'value="{_e("" if dur == "auto" else dur)}"></label>'
        f'<button class="btn ed-dur-apply" data-shot="{_e(sid)}">应用</button>'
        "</div>"
        '<p class="muted ed-hint">经既有分镜编辑路径写入,遵守锁与校验。</p>'
        "</div>")

    p.append("</section>")
    return "".join(p)


def _transitions_panel(td_type: str, td_dur: int) -> str:
    opts = "".join(
        f'<option value="{_e(t)}"{" selected" if t == td_type else ""}>'
        f'{_e(_TRANSITION_LABELS.get(t, t))} ({_e(t)})</option>'
        for t in TRANSITION_TYPES)
    return (
        '<section class="panel ed-trans-panel"><h2>转场 Transitions</h2>'
        '<div class="ed-trans-row">'
        f'<label>默认转场 <select id="ed-trans-type">{opts}</select></label>'
        f'<label>时长 (ms) <input id="ed-trans-dur" class="ed-num" type="number" '
        f'min="0" max="5000" step="50" value="{int(td_dur)}"></label>'
        '<button class="btn" id="ed-trans-apply">保存默认</button>'
        "</div>"
        '<p class="muted ed-hint">全局默认;逐切换点覆盖待后续。'
        '每个切换点的标记显示上次构建的应用/降级状态(悬停看降级原因)。</p>'
        "</section>")


def _look_panel(look: Any) -> str:
    preset = getattr(look, "preset", "none") or "none"
    intensity = float(getattr(look, "intensity", 1.0) or 0.0)
    chips = "".join(
        f'<button class="ed-look-chip{" active" if p == preset else ""}" '
        f'data-preset="{_e(p)}">{_e(_LOOK_LABELS.get(p, p))}</button>'
        for p in LOOK_PRESETS)
    return (
        '<section class="panel ed-look-panel"><h2>调色 Look</h2>'
        f'<div class="ed-look-chips" id="ed-look-chips">{chips}</div>'
        '<div class="ed-look-row">'
        f'<label>强度 intensity <input id="ed-look-int" type="range" min="0" max="1" '
        f'step="0.05" value="{intensity:g}"> <span class="ed-look-int-val">{intensity:g}</span></label>'
        '<button class="btn" id="ed-look-apply">保存调色</button>'
        "</div>"
        '<div class="ed-look-preview" id="ed-look-preview">'
        '<p class="muted ed-hint">在时间线里选一个分镜后,这里显示原色 / 调色前后对比。</p>'
        "</div>"
        "</section>")


# ------------------------------------------------------------------ tiny reads


def _load_rules(project: Any):
    try:
        return project.load_rules()
    except Exception:
        from ..core.models import TimelineRules

        return TimelineRules()


def _load_look(project: Any):
    try:
        from ..media.render import load_look

        return load_look(project)
    except Exception:
        from ..core.models import LookSpec

        return LookSpec()


def _shot_duration_raw(project: Any, sid: str):
    try:
        d = project.load_shot_raw(sid).get("duration", "auto")
    except Exception:
        return "auto"
    if d == "auto" or d is None:
        return "auto"
    try:
        f = float(d)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return "auto"


def _shot_duration_label(project: Any, sid: str) -> str:
    d = _shot_duration_raw(project, sid)
    return "自动" if d == "auto" else f"{d}s"


# ============================================================ assets (css/js)

_EDIT_CSS = """
/* manju gui — 剪辑 EDIT page (round T). Loaded AFTER /app.css + /pages.css;
   reuses their palette + chrome (.pnav, .btn, .chip, #toast) and never
   overrides them. */

.ed-sub { margin: -.2rem 0 .8rem; max-width: 60rem; }
.chip.ed-dirty { color: var(--warn); border-color: #6b5518; background: #4a3a12; font-weight: 700; }
.ed-hint { font-size: .8rem; margin: .35rem 0 0; }
.mono { font-family: var(--mono); }

/* -------- transitions + look pickers -------- */
.ed-trans-row, .ed-look-row, .ed-audio-row, .ed-dur-row, .ed-trim-row {
  display: flex; flex-wrap: wrap; gap: .9rem; align-items: center;
}
.ed-trans-row select, .ed-num, .ed-dur-val {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .25rem .45rem; font: inherit;
}
.ed-num { width: 6.5rem; }
.ed-look-chips { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .7rem; }
.ed-look-chip {
  color: var(--fg); font: inherit; font-size: .82rem; cursor: pointer;
  padding: .2rem .8rem; border-radius: 999px; border: 1px solid var(--line);
  background: var(--panel2);
}
.ed-look-chip.active { background: var(--accent); color: #0b1220; font-weight: 700; border-color: var(--accent); }
.ed-look-preview { margin-top: .8rem; }
.ed-look-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; max-width: 640px; }
.ed-look-pair figure { margin: 0; }
.ed-look-pair img { width: 100%; border-radius: 8px; border: 1px solid var(--line); background: #000; display: block; }
.ed-look-pair figcaption { font-size: .8rem; color: var(--muted); margin-top: .2rem; }

/* -------- timeline strip -------- */
.ed-tl-panel { overflow-x: auto; }
.ed-strip { display: flex; align-items: stretch; gap: 0; min-height: 150px; padding-bottom: .3rem; }
.ed-card {
  flex: 0 0 auto; width: 200px; display: flex; flex-direction: column; gap: .35rem;
  padding: .5rem; border: 1px solid var(--line); border-radius: 8px; background: var(--panel2);
}
.ed-card.sel { outline: 2px solid var(--accent); }
.ed-thumb { width: 100%; height: 108px; object-fit: cover; border-radius: 6px; background: #000; border: 1px solid var(--line); }
.ed-thumb-ph { display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: .82rem; }
.ed-card-meta { display: flex; flex-direction: column; gap: .1rem; font-size: .82rem; }
.ed-card-id { font-weight: 700; }
.ed-card-dur { color: var(--accent); }
.ed-card-take { font-size: .74rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ed-card-ctl { display: flex; gap: .3rem; }
.ed-card-ctl .btn.mini { flex: 1; }

.ed-bound {
  flex: 0 0 auto; align-self: center; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: .1rem; width: 66px;
  color: var(--muted); font-size: .72rem; padding: 0 .2rem;
}
.ed-bound-glyph { font-size: 1rem; }
.ed-bound.ok .ed-bound-glyph { color: var(--ok); }
.ed-bound.bad .ed-bound-glyph { color: var(--err); }
.ed-bound-label { text-align: center; line-height: 1.1; }

/* -------- inspector -------- */
.ed-inspector { margin-top: 1rem; }
.ed-insp-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; }
.ed-block { margin: 1rem 0; padding-top: .6rem; border-top: 1px solid var(--line); }
.ed-block h3 { margin: 0 0 .5rem; font-size: 1rem; }
.ed-scrub { display: flex; gap: 2px; overflow-x: auto; margin-bottom: .6rem; }
.ed-scrub-f { height: 80px; width: auto; border-radius: 3px; background: #000; border: 1px solid var(--line); cursor: pointer; }
.ed-scrub-f.mark-in { outline: 2px solid var(--ok); }
.ed-scrub-f.mark-out { outline: 2px solid var(--star); }
.ed-trim-row label, .ed-audio-row label, .ed-dur-row label { display: inline-flex; align-items: center; gap: .35rem; font-size: .84rem; }
.ed-takes { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .5rem; }
.ed-take { display: inline-flex; align-items: center; gap: .4rem; font-size: .8rem; padding: .2rem .5rem; border: 1px solid var(--line); border-radius: 6px; background: var(--panel2); }
.ed-take.active { border-color: var(--accent); }
.ed-gain-val, .ed-look-int-val { color: var(--muted); font-size: .82rem; min-width: 3.5rem; }

/* -------- plan modal -------- */
.ed-modal { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 300; }
.ed-modal.hidden { display: none; }
.ed-modal-card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 1.2rem 1.4rem; max-width: 620px; width: 92%; max-height: 82vh; overflow: auto; }
.ed-modal-card h2 { margin: 0 0 .7rem; }
.ed-plan-table { width: 100%; border-collapse: collapse; font-size: .84rem; margin: .5rem 0; }
.ed-plan-table th, .ed-plan-table td { text-align: left; padding: .25rem .5rem; border-bottom: 1px solid var(--line); }
.ed-plan-total { font-weight: 700; margin-top: .4rem; }

@media (max-width: 700px) { .ed-look-pair { grid-template-columns: 1fr; } }
"""


_EDIT_JS = r"""
"use strict";
(function () {
  var META = document.querySelector('meta[name="manju-token"]');
  var TOKEN = META ? META.getAttribute("content") : "";

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
  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }
  function errText(res) { return (res.data && res.data.error) || "失败"; }

  function pollJob(id, done) {
    var deadline = Date.now() + 180000;
    (function tick() {
      fetch("/api/jobs").then(function (r) { return r.json(); }).then(function (d) {
        var job = (d.jobs || []).filter(function (j) { return j.id === id; })[0];
        if (job && (job.state === "done" || job.state === "failed")) { done(job); return; }
        if (Date.now() > deadline) { done(null); return; }
        setTimeout(tick, 500);
      }).catch(function () { setTimeout(tick, 700); });
    })();
  }

  // ---------------------------------------------------------- inspector open
  var strip = document.getElementById("ed-strip");
  function inspectorFor(sid) {
    var all = document.querySelectorAll(".ed-inspector");
    var found = null;
    for (var i = 0; i < all.length; i++) {
      if (all[i].getAttribute("data-shot") === sid) found = all[i];
      else all[i].classList.add("hidden");
    }
    return found;
  }
  function openInspector(sid) {
    var insp = inspectorFor(sid);
    if (!insp) return;
    insp.classList.remove("hidden");
    var cards = document.querySelectorAll(".ed-card");
    for (var i = 0; i < cards.length; i++)
      cards[i].classList.toggle("sel", cards[i].getAttribute("data-shot") === sid);
    insp.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------ reorder
  function currentOrder() {
    return Array.prototype.map.call(
      document.querySelectorAll(".ed-card"),
      function (c) { return c.getAttribute("data-shot"); });
  }
  function reorder(sid, dir) {
    var order = currentOrder();
    var i = order.indexOf(sid);
    var j = i + dir;
    if (i < 0 || j < 0 || j >= order.length) return;
    var tmp = order[i]; order[i] = order[j]; order[j] = tmp;
    post("/api/index", { order: order }).then(function (res) {
      if (res.status === 200) { toast("顺序已存", true); reloadSoon(); }
      else toast(errText(res), false);
    });
  }

  // ------------------------------------------------------------ strip clicks
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-shot], .ed-scrub-f, .ed-look-chip, .ed-close");
    if (!el) return;

    if (el.classList.contains("ed-open")) { openInspector(el.getAttribute("data-shot")); return; }
    if (el.classList.contains("ed-close")) {
      var box = el.closest(".ed-inspector"); if (box) box.classList.add("hidden"); return;
    }
    if (el.classList.contains("ed-up")) { reorder(el.getAttribute("data-shot"), -1); return; }
    if (el.classList.contains("ed-down")) { reorder(el.getAttribute("data-shot"), 1); return; }

    if (el.classList.contains("ed-card")) {
      // click the card body (not a control) opens it
      if (!e.target.closest("button")) openInspector(el.getAttribute("data-shot"));
      return;
    }

    if (el.classList.contains("ed-scrub-f")) { markScrub(el); return; }

    if (el.classList.contains("ed-select")) {
      post("/api/select", { shot: el.getAttribute("data-shot"), take: el.getAttribute("data-take") })
        .then(function (res) {
          if (res.status === 200) { toast("已选用 " + el.getAttribute("data-take"), true); reloadSoon(); }
          else toast(errText(res), false);
        });
      return;
    }

    if (el.classList.contains("ed-trim")) { doTrim(el); return; }
    if (el.classList.contains("ed-audio-apply")) { doAudio(el); return; }
    if (el.classList.contains("ed-dur-apply")) { doDuration(el); return; }
    if (el.classList.contains("ed-look-chip")) { pickLook(el); return; }
  });

  // scrub: 1st click sets in, 2nd sets out (by frame index → proportional ms)
  function markScrub(img) {
    var insp = img.closest(".ed-inspector");
    var frames = insp.querySelectorAll(".ed-scrub-f");
    var n = frames.length;
    var idx = parseInt(img.getAttribute("data-i"), 10);
    var inEl = insp.querySelector(".ed-in"), outEl = insp.querySelector(".ed-out");
    // proportional guess across the strip; the user refines the exact ms field.
    var stage = insp.getAttribute("data-scrub-stage") || "in";
    if (stage === "in") {
      frames.forEach(function (f) { f.classList.remove("mark-in"); });
      img.classList.add("mark-in");
      inEl.dataset.frac = (idx / n).toFixed(4);
      insp.setAttribute("data-scrub-stage", "out");
      toast("入点标在第 " + (idx + 1) + " 格,再点一格设出点", true);
    } else {
      frames.forEach(function (f) { f.classList.remove("mark-out"); });
      img.classList.add("mark-out");
      outEl.dataset.frac = ((idx + 1) / n).toFixed(4);
      insp.setAttribute("data-scrub-stage", "in");
      toast("出点标在第 " + (idx + 1) + " 格。核对毫秒后点裁剪", true);
    }
  }

  function doTrim(btn) {
    var insp = btn.closest(".ed-inspector");
    var sid = btn.getAttribute("data-shot");
    var inMs = parseInt(insp.querySelector(".ed-in").value, 10);
    var outMs = parseInt(insp.querySelector(".ed-out").value, 10);
    if (isNaN(inMs) || isNaN(outMs)) { toast("入/出点需为毫秒数", false); return; }
    btn.disabled = true;
    post("/api/edit/trim", { shot: sid, in_ms: inMs, out_ms: outMs }).then(function (res) {
      if (res.status !== 202) { btn.disabled = false; toast(errText(res), false); return; }
      toast("裁剪排队…", true);
      pollJob(res.data.job.id, function (job) {
        btn.disabled = false;
        var out = insp.querySelector(".ed-trim-out");
        if (!job) { toast("裁剪超时", false); return; }
        if (job.state === "failed") { toast(job.error || "裁剪失败", false); return; }
        var nm = job.result && job.result.new_take;
        toast("新 take " + nm, true);
        // offer to select the new take (respect human selection judgment)
        out.textContent = "";
        var span = document.createElement("span");
        span.textContent = "新 take " + nm + " 已生成 · ";
        var b = document.createElement("button");
        b.className = "btn ghost mini"; b.textContent = "选用它";
        b.addEventListener("click", function () {
          post("/api/select", { shot: sid, take: nm }).then(function (r) {
            if (r.status === 200) { toast("已选用 " + nm, true); reloadSoon(); }
            else toast(errText(r), false);
          });
        });
        out.appendChild(span); out.appendChild(b);
      });
    });
  }

  function doAudio(btn) {
    var insp = btn.closest(".ed-inspector");
    var sid = btn.getAttribute("data-shot");
    var gain = parseFloat(insp.querySelector(".ed-gain").value);
    var mute = insp.querySelector(".ed-mute").checked;
    post("/api/edit/audio", { shot: sid, gain_db: gain, mute: mute }).then(function (res) {
      if (res.status === 200) toast("素材声已应用", true);
      else toast(errText(res), false);
    });
  }

  function doDuration(btn) {
    var insp = btn.closest(".ed-inspector");
    var sid = btn.getAttribute("data-shot");
    var auto = insp.querySelector(".ed-dur-auto").checked;
    var body = { shot: sid };
    if (auto) { body.duration = "auto"; }
    else {
      var v = parseFloat(insp.querySelector(".ed-dur-val").value);
      if (isNaN(v) || v <= 0) { toast("时长需为正秒数,或勾选自动", false); return; }
      body.duration = v;
    }
    post("/api/edit/duration", body).then(function (res) {
      if (res.status === 200) { toast("时长已应用", true); reloadSoon(); }
      else toast(errText(res), false);
    });
  }

  // gain / intensity live labels
  document.addEventListener("input", function (e) {
    if (e.target.classList.contains("ed-gain")) {
      var v = e.target.closest("label").querySelector(".ed-gain-val");
      if (v) v.textContent = e.target.value + " dB";
    } else if (e.target.id === "ed-look-int") {
      var iv = document.querySelector(".ed-look-int-val");
      if (iv) iv.textContent = e.target.value;
      refreshLookPreview();
    } else if (e.target.classList.contains("ed-dur-auto")) {
      // toggling auto is a change event; handled below
    }
  });
  document.addEventListener("change", function (e) {
    if (e.target.classList.contains("ed-dur-auto")) {
      var val = e.target.closest(".ed-dur-row").querySelector(".ed-dur-val");
      if (val) val.disabled = e.target.checked;
    }
  });

  // ------------------------------------------------------------ transitions
  var tApply = document.getElementById("ed-trans-apply");
  if (tApply) tApply.addEventListener("click", function () {
    var type = document.getElementById("ed-trans-type").value;
    var dur = parseInt(document.getElementById("ed-trans-dur").value, 10);
    post("/api/edit/transition", { type: type, duration_ms: isNaN(dur) ? 0 : dur })
      .then(function (res) {
        if (res.status === 200) toast("默认转场已保存", true);
        else toast(errText(res), false);
      });
  });

  // ------------------------------------------------------------ look
  var lookPreset = document.querySelector(".ed-look-chip.active");
  function currentLook() {
    var chip = document.querySelector(".ed-look-chip.active");
    var intEl = document.getElementById("ed-look-int");
    return { preset: chip ? chip.getAttribute("data-preset") : "none",
             intensity: intEl ? parseFloat(intEl.value) : 1 };
  }
  function pickLook(chip) {
    document.querySelectorAll(".ed-look-chip").forEach(function (c) { c.classList.remove("active"); });
    chip.classList.add("active");
    refreshLookPreview();
  }
  function firstSelectedTakeRel() {
    // reuse a card thumb's src to find a real take relpath for the preview
    var img = document.querySelector(".ed-card .ed-thumb[src]");
    if (!img) return null;
    var m = img.getAttribute("src").match(/take=([^&]+)/);
    return m ? m[1] : null;
  }
  function refreshLookPreview() {
    var box = document.getElementById("ed-look-preview");
    if (!box) return;
    var rel = firstSelectedTakeRel();
    var look = currentLook();
    box.textContent = "";
    if (!rel) {
      var p = document.createElement("p"); p.className = "muted ed-hint";
      p.textContent = "还没有可预览的 take。"; box.appendChild(p); return;
    }
    var pair = document.createElement("div"); pair.className = "ed-look-pair";
    pair.appendChild(figure(
      "/edit/frame?take=" + rel + "&ms=0&w=360", "原色 raw"));
    pair.appendChild(figure(
      "/edit/look?take=" + rel + "&ms=0&w=360&preset=" + encodeURIComponent(look.preset)
      + "&intensity=" + encodeURIComponent(look.intensity),
      "调色 " + look.preset + " · " + look.intensity));
    box.appendChild(pair);
  }
  function figure(src, cap) {
    var f = document.createElement("figure");
    var img = document.createElement("img"); img.loading = "lazy"; img.alt = ""; img.src = src;
    var c = document.createElement("figcaption"); c.textContent = cap;
    f.appendChild(img); f.appendChild(c); return f;
  }
  var lApply = document.getElementById("ed-look-apply");
  if (lApply) lApply.addEventListener("click", function () {
    var look = currentLook();
    post("/api/edit/look", look).then(function (res) {
      if (res.status === 200) toast("调色已保存", true);
      else toast(errText(res), false);
    });
  });

  // ------------------------------------------------------------ rebuild flow
  var modal = document.getElementById("ed-modal");
  var modalOk = document.getElementById("ed-modal-ok");
  var modalCancel = document.getElementById("ed-modal-cancel");
  var pendingBuild = null;
  function closeModal() { modal.classList.add("hidden"); pendingBuild = null; }
  if (modalCancel) modalCancel.addEventListener("click", closeModal);
  if (modalOk) modalOk.addEventListener("click", function () {
    if (!pendingBuild) { closeModal(); return; }
    post("/api/build", { target: "final", gen: "missing", assume_yes: true })
      .then(function (res) {
        closeModal();
        if (res.status === 202) toast("构建已排队 (queued)", true);
        else toast(errText(res), false);
      });
  });
  var rebuild = document.getElementById("ed-rebuild");
  if (rebuild) rebuild.addEventListener("click", function () {
    post("/api/plan", { action: "build", target: "final", gen: "missing" }).then(function (res) {
      if (res.status !== 200) { toast(errText(res), false); return; }
      showPlan(res.data);
    });
  });
  function showPlan(plan) {
    var title = document.getElementById("ed-modal-title");
    var body = document.getElementById("ed-modal-body");
    title.textContent = "构建计划 · final";
    body.textContent = "";
    var rows = plan.rows || [];
    if (!rows.length) {
      var p = document.createElement("p");
      p.textContent = "没有需要重新生成的镜头 — 直接合成成片。";
      body.appendChild(p);
    } else {
      var table = document.createElement("table"); table.className = "ed-plan-table";
      var head = document.createElement("tr");
      ["镜头", "原因", "服务商", "预估"].forEach(function (h) {
        var th = document.createElement("th"); th.textContent = h; head.appendChild(th);
      });
      table.appendChild(head);
      rows.forEach(function (r) {
        var tr = document.createElement("tr");
        [r.shot, r.reason, r.provider,
         (r.estimated_cost ? (r.estimated_cost + " " + (r.currency || "")) : "—")]
          .forEach(function (v) {
            var td = document.createElement("td"); td.textContent = v; tr.appendChild(td);
          });
        table.appendChild(tr);
      });
      body.appendChild(table);
    }
    var total = document.createElement("p"); total.className = "ed-plan-total";
    total.textContent = "预估合计: " + (plan.estimated_cost || 0) + " " + (plan.currency || "");
    body.appendChild(total);
    pendingBuild = true;
    modal.classList.remove("hidden");
  }

  // preview once on load if a look is set beyond none
  if (lookPreset && lookPreset.getAttribute("data-preset") !== "none") refreshLookPreview();
})();
"""
