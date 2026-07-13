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

Round U deepens this into a real native-editing layer:

  * a stacked **multi-track lanes view** (主轨道 / 字幕 / 音频) rendered from the
    COMPILED timeline (read-only truth), with a global playhead ruler + zoom;
  * **per-clip waveforms** on the audio lanes (lazy ``/edit/wave`` <img>);
  * **caption/voice sync hints** (:mod:`manju.gui.synchints`) overlaid on the
    字幕 lane + a list panel;
  * a **per-boundary transition picker** on each seam marker writing
    ``rules.transition_overrides`` (½-clip validity enforced, 恢复默认 / 硬切);
  * **generative handle rebuild (补拍手柄)** in the seam popover for boundaries
    whose last build degraded for lack of handles.

Lanes only VISUALIZE — every mutation still goes through the inspector actions
and the seam picker, never the lanes themselves.

Round X (agent XG) closes three of the remaining "still need an external NLE"
gaps (REPORTS/ROUND-V-REFERENCES-2.md §1's deferred Tier-2 plan):

  * **Tier-2 timeline preview** — play the COMPILED timeline (order/trims)
    BEFORE any final exists, by sequencing per-clip webpreviews through a
    small pool of ``<video>`` elements (the Chromium 75-``WebMediaPlayer``-
    per-frame cap, §1d) driven by ``/api/edit/playback-manifest``. Tier-1
    (play the render) stays the default the moment a final/proxy exists; a
    toggle switches to Tier-2. No mixed audio (voice/BGM) — the UI says so;
    honest pending/unavailable rows per clip when a preview cannot be built.
  * **字幕样式 caption style panel** — exposes the ASS burn-in knobs
    ``exporters/srt_ass.py`` already reads (font/size/primary colour/outline/
    margin-v/alignment) as an editor writing ``rules.captions``, with a
    server-side one-cue burn preview (the ``look_preview_frame`` pattern).
  * **卡片预设 card presets** — 4 named style combos for packaging intro/outro
    cards (``media/card.py`` / ``media/html_card.py``), selectable in
    ``/packaging``.

All three are additive: every new model field defaults to a value that
renders byte-identically to before it existed (pinned in
``tests/test_edit_v3.py``).
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
    "playback_source",
    "edit_undo_events",
    "plan_revert",
    "UNDO_PANEL_NOTE",
    "STRIP_COUNT",
    "playback_manifest",
    "caption_style_preview_frame",
    "TIER2_NOTE",
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

# Round X (agent XG): Tier-2 timeline preview — no voice/BGM mix, so the UI
# says so up front rather than letting a silent gap read as a bug.
TIER2_NOTE = "时间线预览:无混音,粗剪画面为准"

# Tier-2 <video> pool size (REPORTS §1d: Chromium caps ~75 WebMediaPlayers per
# frame — never one <video> per clip; a small pool with the current clip
# playing + the next one or two preloading is the whole discipline).
TIER2_POOL_SIZE = 3

# ASS numpad-layout alignment values worth exposing (skip the rarely-used
# middle row 4/5/6 to keep the picker short); 2 (下中) is the historical
# hard-coded default.
_ALIGN_LABELS: tuple[tuple[int, str], ...] = (
    (1, "左下 bottom-left"),
    (2, "下中 bottom-center (默认)"),
    (3, "右下 bottom-right"),
    (7, "左上 top-left"),
    (8, "上中 top-center"),
    (9, "右上 top-right"),
)


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


# --------------------------------------------------- playback source (v2 §D)


def playback_source(project: Any, *, include_stale: bool = True) -> dict[str, Any]:
    """Resolve the newest playable render for the preview player, honestly:
    a fresh **final** wins, else the **proxy**, else an empty state (先构建).

    The URL points at the Range-capable ``/media`` endpoint so the ``<video>``
    can seek (Tier 1: play the render, REPORTS §D). ``stale`` flags a final that
    a rebuild would supersede (成片可能已过期), read from the same ``build.explain``
    verdict the dirty chip uses. Best-effort — any read error degrades to the
    empty state, never raises, so a plain GET always resolves a source.

    ``include_stale`` (G3): the stale check runs ``build.explain`` (a full
    recompile). The ``/edit`` PAGE render passes ``False`` so a GET never
    recompiles inline — the ``stale`` chip is revealed lazily by /edit.js after
    it fetches ``/api/edit/dirty``. The async ``/api/edit/playback-source``
    endpoint keeps the default ``True`` (it is off the render thread)."""
    from urllib.parse import quote as _quote

    try:
        final = project.newest_final_path()
    except Exception:
        final = None
    if final is not None:
        try:
            if final.is_file():
                rel = project.relpath(final)
                stale = bool(_unbuilt(project)[0]) if include_stale else False
                return {"kind": "final", "rel": rel,
                        "url": "/media/" + _quote(rel, safe="/"),
                        "label": final.name, "stale": stale}
        except Exception:
            pass
    try:
        proxy = project.proxy_dir / "proxy.mp4"
        if proxy.is_file():
            rel = project.relpath(proxy)
            return {"kind": "proxy", "rel": rel,
                    "url": "/media/" + _quote(rel, safe="/"),
                    "label": "proxy.mp4", "stale": False}
    except Exception:
        pass
    return {"kind": None, "rel": None, "url": None, "label": None, "stale": False}


# ------------------------------------------------ Tier-2 timeline preview (§A)


def playback_manifest(project: Any) -> dict[str, Any]:
    """The Tier-2 playback manifest: the COMPILED timeline's 主轨道 as an
    ORDERED list of ``{shot, start_ms, duration_ms, source_in_ms, source,
    preview_url, status, reason}`` rows, so the client can sequence a small
    pool of ``<video>`` elements over the UN-rendered edit (REPORTS
    ROUND-V-REFERENCES-2.md §1d Tier 2 — before any final exists, or after an
    edit a stale final no longer reflects).

    Pure/read-only and NEVER shells to ffmpeg itself — readiness is a stat-only
    check (:func:`media.webpreview.preview_ready`); a row whose preview is
    missing is marked ``status: "pending"`` (never a URL that would 404), and
    its source is also listed in the top-level ``pending_sources`` for the
    caller to hand to the jobs runner (server.py's
    ``/api/edit/playback-manifest`` GET does this — the manifest computation
    itself stays free of any job-runner dependency so it is trivially unit-
    testable). ``status: "unavailable"`` (with a ``reason``) covers a clip
    whose source vanished/escaped the project — an honest empty state per
    clip, never a crash.

    Sources that are ALREADY browser-safe (webpreview.needs_preview is False —
    most AI-generated takes already are, being H.264/AAC .mp4) skip the
    /preview indirection entirely and point straight at ``/media/<rel>``, same
    as Tier 1. ``preview_url`` never needs a source_in_ms/scale correction on
    the client: webpreview transcodes only ever rescale the FRAME (width), the
    timing axis is untouched (media/webpreview.py's encode args resize
    ``-vf scale=...`` but never trim/retime), so ``source_in_ms`` maps straight
    onto the preview's ``currentTime`` in seconds.
    """
    from urllib.parse import quote as _quote

    from ..media.webpreview import needs_preview, preview_ready

    timeline = _load_timeline(project)
    if timeline is None or not timeline.tracks.video:
        return {"duration_ms": 0, "clips": [], "pending_sources": [], "note": TIER2_NOTE}

    clips_out: list[dict[str, Any]] = []
    pending: list[str] = []
    for vc in timeline.tracks.video:
        row: dict[str, Any] = {
            "shot": vc.shot,
            "start_ms": int(vc.start_ms),
            "duration_ms": int(vc.duration_ms),
            "source_in_ms": int(getattr(vc, "source_in_ms", 0) or 0),
            "source": None,
            "preview_url": None,
            "status": "unavailable",
            "reason": None,
        }
        source = getattr(vc, "source", None)
        if not source:
            row["reason"] = "片段没有素材路径"
            clips_out.append(row)
            continue
        try:
            abspath = project.resolve(source)
        except Exception:
            row["reason"] = "素材路径无法解析"
            clips_out.append(row)
            continue
        if not abspath.is_file():
            row["reason"] = "素材文件不存在(可能已被移动或删除)"
            clips_out.append(row)
            continue
        row["source"] = source
        try:
            if not needs_preview(abspath):
                row["status"] = "ready"
                row["preview_url"] = "/media/" + _quote(source, safe="/")
            elif preview_ready(project.root, abspath):
                row["status"] = "ready"
                row["preview_url"] = "/preview/" + _quote(source, safe="/")
            else:
                row["status"] = "pending"
                pending.append(source)
        except Exception:
            row["reason"] = "无法读取素材(权限或已损坏)"
        clips_out.append(row)

    return {
        "duration_ms": int(getattr(timeline, "duration_ms", 0) or 0),
        "clips": clips_out,
        "pending_sources": pending,
        "note": TIER2_NOTE,
    }


# ------------------------------------------------- caption style preview (§B)


def caption_style_preview_frame(project: Any, style: dict[str, Any], *,
                                sample_text: str = "示例字幕 Sample caption",
                                preview_width: int = 360) -> Path:
    """One frame preview of a pending 字幕样式 change: burns ONE sample cue
    through the EXACT SAME pipeline the final render uses
    (``exporters.srt_ass.compile_ass`` + ffmpeg's libass ``ass=`` filter,
    ``media/render.py``'s own burn-in mechanism) so the preview matches the
    eventual burned-in look precisely — the :func:`look_preview_frame`
    precedent, applied to captions instead of a colour look.

    Background = the newest final/proxy's first frame when one exists (real
    footage — the most honest preview), else a neutral colour field at the
    project's configured resolution (the ASS ``PlayResX/Y`` still matches the
    real frame size, so the sample cue's absolute pixel geometry — font size,
    margin-v safe area — reads exactly as it would in the final; only the
    OUTPUT raster is downscaled to ``preview_width`` for the panel).

    Content-addressed under ``.manju/frames`` (disposable — free to re-key on
    every keystroke); raises on any ffmpeg failure (mirrors
    :func:`look_preview_frame`'s degrade-to-404 contract for the caller).
    """
    import os
    import tempfile

    from ..core.hashing import cache_key, short_hash
    from ..core.models import CaptionLine, Timeline, TimelineTracks
    from ..exporters.srt_ass import compile_ass
    from ..media.ffmpeg import MediaError, atomic_output, default_log, run_ffmpeg
    from ..media.frames import extract_frame, frames_cache_dir
    from ..media.render import _escape_filter_path

    try:
        config = project.load_config()
        cw, ch = max(2, int(config.width)), max(2, int(config.height))
    except Exception:
        cw, ch = 1080, 1920

    key = short_hash(cache_key("capstyle", sample_text, cw, ch, preview_width,
                               sorted(style.items())))
    cache = frames_cache_dir(project.root)
    dest = cache / f"capstyle_{key}.jpg"
    if dest.exists():
        return dest
    cache.mkdir(parents=True, exist_ok=True)

    tl = Timeline(width=cw, height=ch, duration_ms=4000,
                 tracks=TimelineTracks(captions=[
                     CaptionLine(start_ms=0, end_ms=4000, text=sample_text)]))
    ass_text = compile_ass(tl, width=cw, height=ch, style=style)

    fd, ass_tmp_name = tempfile.mkstemp(dir=str(cache), prefix=".capstyle_", suffix=".ass")
    ass_tmp = Path(ass_tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(ass_text)

        bg_frame: Path | None = None
        src = playback_source(project)
        if src["kind"] is not None and src["rel"]:
            try:
                bg_frame = extract_frame(project, src["rel"], 0, width=cw)
            except Exception:
                bg_frame = None

        vf = (f"scale={cw}:{ch},ass={_escape_filter_path(ass_tmp)},"
              f"scale='min({int(preview_width)},iw)':-2")
        log = default_log(project.root, "frames")
        with atomic_output(dest) as tmp:
            if bg_frame is not None:
                cmd = ["-i", str(bg_frame), "-vf", vf, "-frames:v", "1",
                      "-update", "1", "-q:v", "3", str(tmp)]
            else:
                cmd = ["-f", "lavfi", "-i", f"color=c=0x1c2733:s={cw}x{ch}",
                      "-vf", vf, "-frames:v", "1", "-update", "1", "-q:v", "3",
                      str(tmp)]
            run_ffmpeg(cmd, log=log)
            if not Path(tmp).is_file() or Path(tmp).stat().st_size == 0:
                raise MediaError("no caption-style preview frame produced")
    finally:
        try:
            ass_tmp.unlink()
        except OSError:
            pass
    return dest


# ------------------------------------------------------- honest undo (v2 §C)

# events.jsonl actions the 撤销 surface reads: the edit-surface mutations. Undo
# is honest, git-backed history — NEVER a volatile JS stack (REPORTS §1c/§C).
_UNDO_ACTIONS = frozenset({
    "edit_rules", "edit_bible", "edit_shot", "mixer", "repair",
    "handle_rebuild", "rollback_file", "rollback_shot", "snapshot", "select",
})

# The honest note the panel carries: undo is VISIBLE history, not erased history;
# media/takes are append-only (选择版本, never "undone").
UNDO_PANEL_NOTE = ("撤销是可见的历史(git 回滚),不是易失撤销栈 — 每次撤销本身也是一条改动。"
                   "素材/takes 追加不回滚,只选择版本。")


def _look_label(preset: str, intensity: Any) -> str:
    return f"{_LOOK_LABELS.get(preset, preset)} · 强度 {intensity}"


def _find_prior(prior: list, pred) -> Any:
    """The newest event in ``prior`` (oldest→newest) matching ``pred``, or None —
    the recorded 'previous value' a tier-1 revert re-applies."""
    for ev in reversed(prior):
        if pred(ev):
            return ev
    return None


def _revert_plan_for(ev: dict, prior: list) -> dict[str, Any] | None:
    """The machine revert plan for one edit event, or None when the event is not
    a field-level (tier-1) revert. Tier-1 re-applies the value in effect BEFORE
    this event, recovered from ``prior`` (the earlier events) — durable + honest.

    Returns one of:
      * ``{"tier": 1, "endpoint": ..., "apply": {...}, "label": ...}`` — re-apply
        via the SAME engine endpoint the original edit used;
      * ``{"tier": "file", "file": rel, "label": ...}`` — no recorded prior value:
        fall to a git file rollback (tier 2);
      * ``None`` — append-only / informational (no tier-1 path)."""
    action = ev.get("action")
    d = ev.get("detail") or {}

    if action == "edit_rules" and "transition_default" in d:
        p = _find_prior(prior, lambda e: e.get("action") == "edit_rules"
                        and "transition_default" in (e.get("detail") or {}))
        if p is None:
            return {"tier": "file", "file": "timeline/rules.yaml",
                    "label": "回滚 timeline/rules.yaml 到上一版本"}
        td = (p.get("detail") or {})["transition_default"]
        return {"tier": 1, "endpoint": "transition",
                "apply": {"type": td.get("type", "cut"),
                          "duration_ms": int(td.get("duration_ms", 0) or 0)},
                "label": "默认转场恢复到 "
                         + _TRANSITION_LABELS.get(td.get("type", "cut"), td.get("type", "cut"))}

    if action == "edit_rules" and "transition_override" in d:
        shot = (d["transition_override"] or {}).get("shot")
        if not shot:
            return None
        p = _find_prior(prior, lambda e: e.get("action") == "edit_rules"
                        and ((e.get("detail") or {}).get("transition_override") or {}).get("shot") == shot)
        if p is None:
            # no earlier override for this out-edge → the prior state was 无覆盖.
            return {"tier": 1, "endpoint": "transition-override",
                    "apply": {"shot": shot, "action": "reset"},
                    "label": f"转场覆盖 {shot} 恢复默认"}
        pov = (p.get("detail") or {})["transition_override"]
        if pov.get("reset"):
            apply = {"shot": shot, "action": "reset"}
        elif pov.get("type") == "cut":
            apply = {"shot": shot, "cut": True}
        else:
            apply = {"shot": shot, "type": pov.get("type", "cut"),
                     "duration_ms": int(pov.get("duration_ms", 0) or 0)}
        return {"tier": 1, "endpoint": "transition-override", "apply": apply,
                "label": f"转场覆盖 {shot} 恢复到上一版本"}

    if action == "edit_bible" and "look" in d:
        p = _find_prior(prior, lambda e: e.get("action") == "edit_bible"
                        and "look" in (e.get("detail") or {}))
        if p is None:
            return {"tier": "file", "file": "bible/style.yaml",
                    "label": "回滚 bible/style.yaml 到上一版本"}
        lk = (p.get("detail") or {})["look"]
        return {"tier": 1, "endpoint": "look",
                "apply": {"preset": lk.get("preset", "none"),
                          "intensity": float(lk.get("intensity", 1.0) or 0.0)},
                "label": "调色恢复到 " + _look_label(lk.get("preset", "none"),
                                                   lk.get("intensity", 1.0))}

    if action == "edit_shot" and d.get("field") == "duration":
        shot = d.get("shot")
        p = _find_prior(prior, lambda e: e.get("action") == "edit_shot"
                        and (e.get("detail") or {}).get("field") == "duration"
                        and (e.get("detail") or {}).get("shot") == shot)
        if p is None:
            return {"tier": "file", "file": f"shots/{shot}.yaml",
                    "label": f"回滚 shots/{shot}.yaml 到上一版本"}
        return {"tier": 1, "endpoint": "duration",
                "apply": {"shot": shot, "duration": (p.get("detail") or {}).get("duration", "auto")},
                "label": f"时长 {shot} 恢复到上一版本"}

    if action == "mixer":
        # the mixer event records WHICH shots changed, not the prior gain — so
        # there is no recorded value to re-apply: honest tier-2 (file rollback).
        shots = d.get("shots") or []
        if len(shots) == 1:
            return {"tier": "file", "file": f"shots/{shots[0]}.yaml",
                    "label": f"回滚 shots/{shots[0]}.yaml 到上一版本"}
        return {"tier": "file", "file": "timeline/rules.yaml",
                "label": "回滚 timeline/rules.yaml 到上一版本"}

    return None


def _undo_what(ev: dict) -> str:
    """A one-line 中文 summary of what an edit event changed (the 撤销 list row)."""
    action = ev.get("action")
    d = ev.get("detail") or {}
    if action == "edit_rules" and "transition_default" in d:
        td = d["transition_default"]
        return ("默认转场 → " + _TRANSITION_LABELS.get(td.get("type"), str(td.get("type")))
                + f" {int(td.get('duration_ms', 0) or 0)}ms")
    if action == "edit_rules" and "transition_override" in d:
        ov = d["transition_override"]
        shot = ov.get("shot", "?")
        if ov.get("reset"):
            return f"转场覆盖 {shot} → 恢复默认"
        if ov.get("type") == "cut":
            return f"转场覆盖 {shot} → 硬切"
        return (f"转场覆盖 {shot} → " + _TRANSITION_LABELS.get(ov.get("type"), str(ov.get("type")))
                + f" {int(ov.get('duration_ms', 0) or 0)}ms")
    if action == "edit_bible" and "look" in d:
        lk = d["look"]
        return "调色 → " + _look_label(lk.get("preset", "none"), lk.get("intensity", 1.0))
    if action == "edit_shot" and d.get("field") == "duration":
        return f"时长 {d.get('shot', '?')} → {d.get('duration', 'auto')}"
    if action == "mixer":
        return "素材声 → " + (", ".join(d.get("shots") or []) or ", ".join(d.get("changed") or []))
    if action == "repair":
        return f"裁剪 {d.get('shot', '?')} → 新 take {d.get('new_take', '')}"
    if action == "handle_rebuild":
        return f"补拍手柄 {d.get('shot', '?')} → take {d.get('trim_take', '')}"
    if action == "rollback_file":
        return f"回滚文件 {d.get('path', '?')}"
    if action == "rollback_shot":
        return f"回滚版本 {d.get('shot', '?')} → {d.get('take', '')}"
    if action == "select":
        return f"选用 {d.get('shot', '?')} · {d.get('take', '')}"
    if action == "snapshot":
        return "快照" + (f":{d.get('label')}" if d.get("label") else "")
    return str(action)


def edit_undo_events(project: Any, n: int = 8) -> list[dict[str, Any]]:
    """The last ``n`` edit-surface events with an honest revert affordance each
    (Native Cut v2 §C). Newest first. Read-only: builds the 撤销 panel from the
    events.jsonl tail (git-backed truth), never a volatile stack.

    Each row carries ``index`` (absolute position in the log — the handle the
    revert endpoint reverts by), ``what/ts/actor``, a ``tier`` and, for tier-1,
    the recovered previous-value ``label``; append-only rows (media/takes) carry
    ``revertable=False`` and a 选择版本 note."""
    from ..core.events import tail_events

    allev = tail_events(project.root, 100_000)
    indexed = [(i, ev) for i, ev in enumerate(allev)
               if ev.get("action") in _UNDO_ACTIONS]
    out: list[dict[str, Any]] = []
    for i, ev in indexed[-n:]:
        plan = _revert_plan_for(ev, allev[:i])
        action = ev.get("action")
        file_rel: str | None = None
        if plan is not None and plan.get("tier") == 1:
            tier: Any = 1
            revertable = True
            label = plan["label"]
            note = None
        elif plan is not None and plan.get("tier") == "file":
            tier = "file"
            revertable = True
            label = plan["label"]
            file_rel = plan.get("file")
            note = None
        elif action in ("repair", "handle_rebuild"):
            tier, revertable, label = "append", False, ""
            note = "素材/takes 追加不回滚,只在下方选择版本"
        elif action in ("select", "rollback_shot"):
            tier, revertable, label = "version", False, ""
            note = "版本选择(追加式,可再选回)"
        elif action == "rollback_file":
            tier, revertable, label = "history", False, ""
            note = "这本身就是一次撤销(git 回滚)"
        else:  # snapshot / other
            tier, revertable, label = "info", False, ""
            note = None
        out.append({"index": i, "ts": ev.get("ts", ""), "actor": ev.get("actor", "?"),
                    "action": action, "what": _undo_what(ev), "tier": tier,
                    "revertable": revertable, "label": label, "file": file_rel,
                    "note": note})
    out.reverse()
    return out


def plan_revert(project: Any, index: int) -> dict[str, Any]:
    """The revert plan for the event at absolute log ``index`` — the pure core the
    ``/api/edit/revert`` endpoint dispatches on. Raises ``IndexError`` for an
    out-of-range index and ``ValueError`` for an event with no revert path."""
    from ..core.events import tail_events

    allev = tail_events(project.root, 100_000)
    if index < 0 or index >= len(allev):
        raise IndexError(f"事件索引超出范围: {index}")
    ev = allev[index]
    if ev.get("action") not in _UNDO_ACTIONS:
        raise ValueError("该事件不在撤销范围内")
    plan = _revert_plan_for(ev, allev[:index])
    if plan is None:
        raise ValueError("素材/版本类改动是追加式的,不能撤销(只选择版本)")
    plan = dict(plan)
    plan["index"] = index
    plan["ts"] = ev.get("ts", "")
    plan["action"] = ev.get("action")
    return plan


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


def _shell(title: str, token: str, body: str) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    nav, bcls = chrome("/edit")
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
        + GLOSSARY_HEAD
        + '<script src="/common.js" defer></script>\n<script src="/edit.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="/edit" class="{bcls}">\n'
        + nav
        + "\n<main>\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        '<div id="ed-modal" class="ed-modal hidden"><div class="ed-modal-card">'
        '<h2 id="ed-modal-title"></h2><div id="ed-modal-body"></div>'
        '<div class="btnrow"><button class="btn ghost" id="ed-modal-cancel">取消</button>'
        '<button class="btn" id="ed-modal-ok">确认</button></div></div></div>\n'
        + _seam_modal()
        + _keymap_modal()
        + _footer_hints()
        + "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


# The 6-key universal map (REPORTS Native Cut v2 §A) + the extras, advertised in
# the ? overlay and the footer. Every row is ALSO reachable by click (WCAG 2.5.7):
# the key is only an accelerator over an existing button/control.
_KEYMAP: tuple[tuple[str, str], ...] = (
    ("空格 Space", "播放 / 暂停预览"),
    ("← / →", "逐帧移动播放头(按住 Shift = 1 秒)"),
    ("Home / End", "跳到时间线开头 / 结尾"),
    ("↑ / ↓", "跳到上一个 / 下一个片段切换点"),
    ("I / O", "把入点 / 出点设到播放头(填入裁剪框并聚焦“裁剪”)"),
    ("Z", "在缩放档位之间切换(×1 / ×2 / ×4)"),
    ("N", "开关吸附(磁吸到片段/字幕边界与整秒)"),
    ("[ / ]", "缩小 / 放大时间线"),
    ("Ctrl+Z / Ctrl+Shift+Z", "撤销 / 重做上一次改动(git 回滚,非易失栈)"),
    ("Esc", "关闭弹窗 / 覆盖层"),
    ("?", "打开这张快捷键表"),
)


def _keymap_modal() -> str:
    """The bilingual keyboard-map overlay (`?` / the 快捷键 button). A CSP-safe
    dialog — no inline handlers; the JS toggles ``.hidden`` and Esc closes it."""
    rows = "".join(
        f'<tr><th scope="row"><kbd>{_e(k)}</kbd></th><td>{_e(v)}</td></tr>'
        for k, v in _KEYMAP)
    return (
        '<div id="ed-keymap-modal" class="ed-modal hidden" role="dialog" '
        'aria-modal="true" aria-labelledby="ed-keymap-title">'
        '<div class="ed-modal-card">'
        '<h2 id="ed-keymap-title">键盘快捷键 Keyboard map</h2>'
        '<p class="muted ed-hint">每个快捷键都只是按钮的加速器 —— 所有操作都能点击完成'
        '(无需拖拽,WCAG 2.5.7)。在输入框里打字时快捷键自动让位。</p>'
        f'<table class="ed-keymap-table">{rows}</table>'
        '<div class="btnrow"><button class="btn" id="ed-keymap-close">关闭 (Esc)</button></div>'
        "</div></div>\n")


def _footer_hints() -> str:
    """The always-visible footer hint bar advertising the keymap (Shotcut /
    LosslessCut both train users to reach for ``?``)."""
    return (
        '<div class="ed-footer-hints" id="ed-footer-hints">'
        '<span>空格 播放</span><span>←→ 逐帧</span><span>I/O 入出点</span>'
        '<span>N 吸附</span><span>Z 缩放</span><span>Ctrl+Z 撤销</span>'
        '<button class="ed-foot-key" id="ed-keymap-foot" title="全部快捷键">? 快捷键</button>'
        "</div>\n")


def _seam_modal() -> str:
    """The per-boundary transition picker popover (round U). Hidden; JS fills
    the type/duration fields and the 补拍手柄 row per seam and posts the change
    to ``/api/edit/transition-override`` / ``/api/edit/handle-rebuild``."""
    opts = "".join(
        f'<option value="{_e(t)}">{_e(_TRANSITION_LABELS.get(t, t))} ({_e(t)})</option>'
        for t in TRANSITION_TYPES)
    return (
        '<div id="ed-seam-modal" class="ed-modal hidden"><div class="ed-modal-card">'
        '<h2 id="ed-seam-title">切换点转场</h2>'
        '<p class="muted ed-hint" id="ed-seam-sub"></p>'
        '<div class="ed-seam-row">'
        f'<label>转场 <select id="ed-seam-type">{opts}</select></label>'
        '<label>时长 (ms) <input id="ed-seam-dur" class="ed-num" type="number" '
        'min="0" max="5000" step="50" value="300"></label>'
        '</div>'
        '<p class="muted ed-hint" id="ed-seam-cap"></p>'
        '<div class="ed-seam-hr" id="ed-seam-handle"></div>'
        '<div class="btnrow ed-seam-btns">'
        '<button class="btn ghost" id="ed-seam-reset">恢复默认</button>'
        '<button class="btn ghost" id="ed-seam-cut">硬切</button>'
        '<button class="btn ghost" id="ed-seam-cancel">取消</button>'
        '<button class="btn" id="ed-seam-ok">应用</button>'
        '</div></div></div>\n')


def render_edit(project: Any, token: str, query: dict[str, list[str]]) -> str:
    """The 剪辑 EDIT page: multi-track lanes + a clip strip + per-clip inspector."""
    shots = project.shot_ids()
    rules = _load_rules(project)
    td = getattr(rules, "transition_default", None)
    td_type = getattr(td, "type", None) or "cut"
    td_dur = getattr(td, "duration_ms", None) or 0
    overrides = getattr(rules, "transition_overrides", None) or {}
    look = _load_look(project)
    tstate = transitions_state(project)
    timeline = _load_timeline(project)
    mixer = _mixer_by_shot(project)

    from .userstate import is_snap_enabled

    snap_on = is_snap_enabled()

    out: list[str] = []
    # ---- header + rebuild affordance + undo / keyboard-map toolbar (v2 §A/§C)
    out.append('<div class="page-h">')
    out.append("<h1>剪辑 Edit</h1>")
    # G3: the dirty badge is a LAZY slot — hidden at first paint, revealed by
    # /edit.js after it fetches /api/edit/dirty. Before, render_edit ran a FULL
    # build.explain recompile (+ ~40 probes) inline just for this string; that
    # recompile now happens OFF the request thread (mirrors the review boards).
    out.append('<span class="chip ed-dirty" id="ed-dirty-chip" hidden '
               'title="有未构建的修改">有未构建的修改</span>')
    out.append('<button class="btn ghost" id="ed-undo-btn" aria-expanded="false" '
               'title="最近改动 · 一键撤销(git 回滚)">撤销 Undo</button>')
    out.append('<button class="btn ghost" id="ed-keymap-btn" '
               'title="键盘快捷键(或按 ?)">快捷键 ?</button>')
    out.append('<button class="btn ghost" id="ed-rebuild">重新构建 (rebuild)</button>')
    out.append("</div>")
    out.append('<p class="muted ed-sub">剪辑级收尾:多轨道 · 顺序 · 裁剪 · 素材声 · 转场 · 调色 · '
               "字幕/语音同步 — 全部无需打开剪映。所有修改走同一引擎核心与事件。</p>")

    # ---- honest-undo panel (git-backed; hidden until toggled) — v2 §C
    out.append(_undo_panel())

    # ---- preview player: Tier 1 (plays the render) + Tier 2 (round X, sequences
    # per-clip previews of the COMPILED timeline before any final exists) — v2 §D / §A
    out.append(_playback_section(project, timeline))

    # ---- transitions default + look pickers
    out.append(_transitions_panel(td_type, td_dur))
    out.append(_look_panel(look))

    # ---- 字幕样式 caption style panel (round X §B)
    out.append(_caption_style_panel(project, rules))

    # ---- multi-track lanes view (from the COMPILED timeline, read-only)
    out.append(_lanes_section(project, timeline, snap_on))

    # ---- caption/voice sync-hints panel (advanced; JS fills it lazily)
    out.append(_synchints_panel(timeline))

    # ---- clip strip (主轨道 cards: reorder + inspector + clickable seams)
    out.append('<section class="panel ed-tl-panel">')
    out.append("<h2>时间线 Timeline · 主轨道</h2>")
    if not shots:
        out.append('<p class="muted">还没有分镜 (no shots yet)。</p>')
    else:
        out.append('<div class="ed-strip" id="ed-strip">')
        for idx, sid in enumerate(shots):
            take, sel = _selected_take(project, sid)
            rel = _take_relpath(project, take)
            out.append(_clip_card(project, idx, len(shots), sid, rel, sel))
            if idx < len(shots) - 1:
                a, b = shots[idx], shots[idx + 1]
                out.append(_boundary_marker(
                    project, timeline, a, b, tstate, td_type, td_dur, overrides))
        out.append("</div>")
    out.append("</section>")

    # ---- per-shot inspectors (hidden; JS reveals the clicked one)
    for sid in shots:
        take, sel = _selected_take(project, sid)
        rel = _take_relpath(project, take)
        out.append(_inspector(project, sid, rel, sel, mixer.get(sid, {})))

    return _shell("剪辑 Edit", token, "\n".join(out))


# --------------------------------------------------------------- lanes view


# lane kind -> (中文 label, timeline track attribute)
_AUDIO_LANES = (
    ("voice", "配音 Voice", "ed-lane-voice"),
    ("music", "音乐 Music", "ed-lane-music"),
    ("sfx", "音效 SFX", "ed-lane-sfx"),
    ("ambient", "环境 Ambient", "ed-lane-ambient"),
)
_WAVE_W = 480
_WAVE_H = 40


def _lanes_section(project: Any, timeline: Any, snap_on: bool = True) -> str:
    """The stacked typed lanes (主轨道 / 字幕 / 音频) from the compiled timeline.

    Pure DOM with ms geometry baked into ``data-*`` attributes; ``/edit.js``
    positions every block against the shared timeline width (upper-covers-lower
    is a visual convention only — lanes never mutate). No timeline yet → an
    honest 'build first' note, so the page GET never needs ffmpeg.

    Round V: carries ``data-fps`` (frame-step + frame-grid snap) and ``data-snap``
    (the persisted magnet state) so the keyboard/snapping layer needs no fetch on
    load, and adds a magnet toggle + a snap-tick ruler overlay."""
    p = ['<section class="panel ed-lanes-panel"><div class="ed-lanes-head">',
         "<h2>多轨道 Lanes</h2>"]
    if timeline is None:
        p.append('<p class="muted ed-hint">构建一次后,这里按 CapCut 方式显示主轨道 / 字幕 / '
                 '音频多轨道、波形与全局播放头。</p></div></section>')
        return "".join(p)
    total = max(1, int(getattr(timeline, "duration_ms", 0) or 0))
    tracks = timeline.tracks
    if not total or not tracks.video:
        # a compiled-but-empty timeline: recompute a best-effort total
        total = max([1] + [c.start_ms + c.duration_ms for c in tracks.video])
    fps = int(getattr(timeline, "fps", 0) or 0) or 24
    p.append('<div class="ed-zoom"><label>缩放 <input id="ed-zoom" type="range" '
             'min="1" max="8" step="0.5" value="1"></label>'
             '<span class="muted ed-hint" id="ed-zoom-val">×1</span>'
             '<button class="btn ghost mini ed-magnet" id="ed-snap-toggle" '
             f'aria-pressed="{"true" if snap_on else "false"}" '
             'title="吸附到片段/字幕边界与整秒(N 切换,按住 Alt 临时关闭)">'
             f'吸附 N {"开" if snap_on else "关"}</button>'
             '</div></div>')

    p.append(f'<div class="ed-lanes" id="ed-lanes" data-total="{total}" '
             f'data-fps="{fps}" data-snap="{"1" if snap_on else "0"}">')
    # global playhead ruler (+ a snap-tick overlay the JS fills)
    p.append('<div class="ed-ruler" id="ed-ruler"><div class="ed-lane-track" '
             f'data-total="{total}"><div class="ed-snapticks" id="ed-snapticks"></div>'
             '<div class="ed-playhead" id="ed-playhead"></div>'
             '</div></div>')

    # 主轨道 (video)
    p.append(_lane_row("主轨道 Main", "ed-lane-video", "".join(
        _video_block(project, vc, total) for vc in tracks.video)))

    # 字幕 (captions) + sync-hint strip overlay
    cap_blocks = "".join(_caption_block(c, i, total)
                         for i, c in enumerate(tracks.captions))
    cap_blocks += '<div class="ed-hint-strip" id="ed-synchints-strip"></div>'
    p.append(_lane_row("字幕 Captions", "ed-lane-caption", cap_blocks))

    # 音频 lanes
    for attr, label, cls in _AUDIO_LANES:
        clips = getattr(tracks, attr, []) or []
        if not clips and attr in ("sfx", "ambient", "music"):
            continue  # keep the view tidy: only show audio lanes that carry clips
        blocks = "".join(_audio_block(project, c, total) for c in clips)
        p.append(_lane_row(label, cls, blocks
                           or '<span class="ed-lane-empty muted">—</span>'))

    p.append("</div>")
    p.append('<figure class="ed-lane-preview" id="ed-lane-preview" hidden>'
             '<img alt="" id="ed-lane-preview-img"><figcaption class="muted" '
             'id="ed-lane-preview-cap"></figcaption></figure>')
    p.append("</section>")
    return "".join(p)


def _lane_row(label: str, cls: str, inner: str) -> str:
    return (f'<div class="ed-lane {cls}"><div class="ed-lane-label">{_e(label)}</div>'
            f'<div class="ed-lane-track">{inner}</div></div>')


def _video_block(project: Any, vc: Any, total: int) -> str:
    rel = _clip_source_rel(project, vc.source)
    src_attr = f' data-src="{_e(rel)}"' if rel else ""
    return (
        f'<button class="ed-lane-clip ed-vclip" data-shot="{_e(vc.shot)}" '
        f'data-ms-start="{int(vc.start_ms)}" data-ms-dur="{int(vc.duration_ms)}" '
        f'data-in="{int(getattr(vc, "source_in_ms", 0) or 0)}"{src_attr} '
        f'data-total="{total}" title="{_e(vc.shot)} · {int(vc.duration_ms)}ms">'
        f'<span class="ed-clip-t">{_e(vc.shot)}</span></button>')


def _caption_block(c: Any, i: int, total: int) -> str:
    dur = max(1, int(c.end_ms) - int(c.start_ms))
    return (
        f'<a class="ed-lane-clip ed-cclip" href="/subtitles" '
        f'data-cue="{i}" data-ms-start="{int(c.start_ms)}" data-ms-dur="{dur}" '
        f'data-total="{total}" title="{_e(c.text)}">'
        f'<span class="ed-clip-t">{_e(c.text)}</span></a>')


def _audio_block(project: Any, c: Any, total: int) -> str:
    rel = _clip_source_rel(project, c.source)
    dur = int(c.duration_ms) if getattr(c, "duration_ms", None) else max(1, total - int(c.start_ms))
    inner = ""
    src_attr = ""
    if rel:
        from urllib.parse import quote

        wsrc = ("/edit/wave?take=" + quote(rel, safe="")
                + f"&w={_WAVE_W}&h={_WAVE_H}")
        inner = f'<img class="ed-wave" loading="lazy" alt="" src="{_e(wsrc)}">'
        src_attr = f' data-src="{_e(rel)}"'
    return (
        f'<div class="ed-lane-clip ed-aclip"{src_attr} '
        f'data-ms-start="{int(c.start_ms)}" data-ms-dur="{dur}" data-total="{total}" '
        f'title="{_e(rel or "")}">{inner}</div>')


def _clip_source_rel(project: Any, source: str | None) -> str | None:
    """A compiled clip's ``source`` is already project-relative; keep it only
    when it resolves to a real, served media file (else the block is bare)."""
    if not source:
        return None
    try:
        abspath = project.resolve(source)
    except Exception:
        return None
    return source if abspath.is_file() else None


def _synchints_panel(timeline: Any) -> str:
    """The caption/voice sync-hints list (advanced → mj-pro-only). JS fills it
    from ``/api/edit/synchints`` (which runs ffmpeg lazily — never the page
    GET); before that it shows a 'loading' line, and honestly degrades when
    there is no timeline / no voice / no ffmpeg."""
    body = ('<p class="muted ed-hint">构建一次后可检查字幕与语音是否对齐。</p>'
            if timeline is None else
            '<p class="muted ed-hint" id="ed-sync-note">正在分析字幕与语音…</p>'
            '<div id="ed-synchints-list" class="ed-sync-list"></div>')
    return ('<section class="panel ed-sync-panel mj-pro-only"><h2>字幕/语音同步检查</h2>'
            + body + "</section>")


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


def _boundary_marker(project: Any, timeline: Any, a: str, b: str,
                     tstate: dict | None, default_type: str, default_dur: int,
                     overrides: dict) -> str:
    """A clickable seam marker (CapCut's white-square transition point). Carries
    the effective transition (override on ``a``'s out-edge, else the default),
    the last build's applied/degraded state, and the ½-clip cap so the picker
    popover can validate client-side and offer 补拍手柄 on a degraded seam."""
    pair = f"{a}->{b}"
    # effective transition = override on this out-edge, else global default
    has_ov = a in overrides
    ov = overrides.get(a) if has_ov else None
    if has_ov and ov is None:
        eff_type, eff_dur = "cut", 0
    elif has_ov:
        eff_type = getattr(ov, "type", None) or "cut"
        eff_dur = int(getattr(ov, "duration_ms", 0) or 0)
    else:
        eff_type, eff_dur = default_type, int(default_dur or 0)
    label = _TRANSITION_LABELS.get(eff_type, eff_type)

    degraded = False
    if tstate is None:
        glyph, cls, title = "—", "", "尚未构建 (no build yet)"
    elif pair in tstate:
        e = tstate[pair]
        if e.get("applied"):
            glyph, cls = "✓", "ok"
            title = f"已应用 · 手柄 {e.get('half_ms', '?')}ms/侧"
        else:
            glyph, cls, degraded = "✗", "bad", True
            title = "降级为硬切 · " + str(e.get("reason") or "unknown")
    else:
        glyph, cls, title = "✓", "ok", "应用默认转场 (applied default)"

    try:
        from .edit_engine import max_override_duration_ms

        max_ms = max_override_duration_ms(project, timeline, a)
    except Exception:
        max_ms = None
    ov_chip = '<span class="ed-bound-ov" title="逐切换点覆盖">·覆盖</span>' if has_ov else ""
    return (
        f'<button class="ed-bound {cls}" data-out="{_e(a)}" data-next="{_e(b)}" '
        f'data-eff-type="{_e(eff_type)}" data-eff-dur="{eff_dur}" '
        f'data-default-type="{_e(default_type)}" data-default-dur="{int(default_dur or 0)}" '
        f'data-has-override="{"1" if has_ov else "0"}" '
        f'data-degraded="{"1" if degraded else "0"}" '
        f'data-max="{"" if max_ms is None else int(max_ms)}" title="{_e(title)}">'
        f'<span class="ed-bound-glyph">{glyph}</span>'
        f'<span class="ed-bound-label">{_e(label)}{ov_chip}</span></button>')


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
        '<section class="panel ed-trans-panel mj-pro-only"><h2>转场 Transitions</h2>'
        '<div class="ed-trans-row">'
        f'<label>默认转场 <select id="ed-trans-type">{opts}</select></label>'
        f'<label>时长 (ms) <input id="ed-trans-dur" class="ed-num" type="number" '
        f'min="0" max="5000" step="50" value="{int(td_dur)}"></label>'
        '<button class="btn" id="ed-trans-apply">保存默认</button>'
        "</div>"
        '<p class="muted ed-hint">全局默认;逐切换点(seam)可点开单独覆盖 — 写入 '
        'timeline/rules.yaml 的 transition_overrides(null 或 cut 表示硬切)。'
        '每个切换点的标记显示上次构建的应用/降级状态(悬停看降级原因);'
        '降级的切换点可在弹窗里“补拍手柄”。</p>'
        "</section>")


def _look_panel(look: Any) -> str:
    preset = getattr(look, "preset", "none") or "none"
    intensity = float(getattr(look, "intensity", 1.0) or 0.0)
    chips = "".join(
        f'<button class="ed-look-chip{" active" if p == preset else ""}" '
        f'data-preset="{_e(p)}">{_e(_LOOK_LABELS.get(p, p))}</button>'
        for p in LOOK_PRESETS)
    return (
        '<section class="panel ed-look-panel mj-pro-only"><h2>调色 Look</h2>'
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


def _caption_style_panel(project: Any, rules: Any) -> str:
    """字幕样式 caption style panel (round X §B): the ASS burn-in knobs
    ``exporters/srt_ass.py`` already honors (font/size/primary colour/outline/
    margin-v/alignment), writing ``rules.captions`` additive style fields.
    Every input's VALUE is the current override (empty when unset) and its
    PLACEHOLDER is the computed fallback the ASS writer uses when unset — so
    leaving a field blank visibly means "使用系统默认值", never a silent
    guess. Hints beside the controls are the subtitle-standards skill's
    numbers (CJK ≤16 横屏 / ~9–10 竖屏 per line, 竖屏安全区 顶150/底300px)."""
    from ..exporters.srt_ass import DEFAULT_FONT

    cap = getattr(rules, "captions", None)
    try:
        config = project.load_config()
        cw, ch = int(config.width), int(config.height)
    except Exception:
        cw, ch = 1080, 1920
    default_size = max(36, ch // 22)
    default_margin = max(1, ch // 12)
    font = (getattr(cap, "font", None) or "") if cap else ""
    size = getattr(cap, "size", None) if cap else None
    primary = (getattr(cap, "primary_colour", None) or "") if cap else ""
    margin_v = getattr(cap, "margin_v", None) if cap else None
    outline = getattr(cap, "outline", None) if cap else None
    alignment = (getattr(cap, "alignment", None) if cap else None) or 2
    align_opts = "".join(
        f'<option value="{v}"{" selected" if v == alignment else ""}>{_e(lbl)}</option>'
        for v, lbl in _ALIGN_LABELS)
    portrait = ch >= cw
    line_hint = ("竖屏建议每行 ≤ 9–10 个 CJK 字" if portrait else "横屏建议每行 ≤ 16 个 CJK 字")
    safe_hint = "竖屏安全区:顶部 ~150px、底部 ~300px(字幕带需在其上方)" if portrait else ""
    return (
        '<section class="panel ed-capstyle-panel mj-pro-only"><h2>字幕样式 Caption style</h2>'
        f'<p class="muted ed-hint">{_e(line_hint)}'
        + (f' · {_e(safe_hint)}' if safe_hint else '')
        + ' · 留空 = 使用系统默认值(不改变现有输出)。</p>'
        '<div class="ed-cs-row">'
        f'<label>字体 font <input id="ed-cs-font" type="text" value="{_e(font)}" '
        f'placeholder="{_e(DEFAULT_FONT)}"></label>'
        f'<label>字号 size <input id="ed-cs-size" class="ed-num" type="number" min="12" max="200" '
        f'value="{_e("" if size is None else size)}" placeholder="{default_size}"></label>'
        f'<label>主色 primary <input id="ed-cs-primary" type="text" value="{_e(primary)}" '
        'placeholder="&H00FFFFFF" title="ASS 颜色格式 &amp;HAABBGGRR"></label>'
        "</div>"
        '<div class="ed-cs-row">'
        f'<label>描边 outline <input id="ed-cs-outline" class="ed-num" type="number" min="0" max="10" '
        f'value="{_e("" if outline is None else outline)}" placeholder="3"></label>'
        f'<label>底部安全区 margin-v (px) <input id="ed-cs-marginv" class="ed-num" type="number" '
        f'min="0" max="600" value="{_e("" if margin_v is None else margin_v)}" '
        f'placeholder="{default_margin}"></label>'
        f'<label>对齐 alignment <select id="ed-cs-align">{align_opts}</select></label>'
        "</div>"
        '<div class="btnrow">'
        '<button class="btn" id="ed-cs-apply">保存字幕样式</button>'
        '<button class="btn ghost" id="ed-cs-reset">恢复默认(清空覆盖)</button>'
        "</div>"
        '<figure class="ed-cs-preview" id="ed-cs-preview">'
        '<img alt="" id="ed-cs-preview-img" loading="lazy">'
        '<figcaption class="muted ed-hint">样式预览(服务端用示例字幕真实烧录一帧)</figcaption>'
        "</figure>"
        "</section>")


def _undo_panel() -> str:
    """The honest-undo surface (v2 §C): a collapsible toolbar panel the JS fills
    from ``/api/edit/undo``. Hidden until the 撤销 button toggles it. Server-
    rendered shell only (CSP-safe) — no volatile stack, git-backed truth."""
    return (
        '<section class="panel ed-undo-panel hidden" id="ed-undo-panel" '
        'aria-label="最近改动 · 撤销">'
        '<div class="ed-undo-head"><h2>最近改动 · 撤销</h2>'
        '<button class="btn ghost mini" id="ed-snapshot-btn" '
        'title="给当前工程打一个 git 快照,整段编辑一键可回">开始编辑前先快照</button></div>'
        f'<p class="muted ed-hint">{_e(UNDO_PANEL_NOTE)}</p>'
        '<div class="ed-undo-list" id="ed-undo-list"></div>'
        '<p class="muted ed-hint">需要整项目回滚?到 '
        '<a href="/history">历史 / 回滚</a> 页。</p>'
        "</section>")


def _playback_section(project: Any, timeline: Any) -> str:
    """The preview player above the lanes: Tier 1 (v2 §D — play the render,
    the default the moment a final/proxy exists) and Tier 2 (round X §A —
    sequence per-clip webpreviews of the COMPILED timeline, so an edit can be
    scrubbed BEFORE any final exists, or after further edits a stale final no
    longer reflects). A toggle switches tiers when both are genuinely
    available; when only one is, that one renders directly with no pointless
    toggle-to-nothing. Neither → one honest combined empty state (unchanged
    from before this round). The playhead syncs both ways in ``/edit.js``
    regardless of which tier is active (timeupdate → playhead; ruler
    click/step → seek; Space/←→ dispatch to whichever tier is active)."""
    # G3: include_stale=False so the page GET never recompiles via build.explain
    # — the stale chip below is a lazy slot revealed by /edit.js after paint.
    src = playback_source(project, include_stale=False)
    has_t1 = src["kind"] is not None
    has_t2 = timeline is not None and bool(timeline.tracks.video)
    default_tier = 1 if has_t1 else (2 if has_t2 else 0)

    if not has_t1 and not has_t2:
        return (
            '<section class="panel ed-play-panel" id="ed-play-panel" data-kind="" '
            'data-default-tier="0">'
            '<h2>预览播放 Preview</h2>'
            '<p class="muted ed-hint" id="ed-play-empty">还没有成片或预览版可播放 —— '
            '先构建(点上方“重新构建”),这里就能按空格播放整条时间线,播放头与多轨道联动。'
            '(在此之前,点击标尺仍显示定格预览。)</p></section>')

    parts: list[str] = [
        '<section class="panel ed-play-panel" id="ed-play-panel" '
        f'data-kind="{_e(src["kind"] or "")}" data-src="{_e(src["url"] or "")}" '
        f'data-default-tier="{default_tier}">',
        '<div class="ed-play-head"><h2>预览播放 Preview</h2>',
    ]
    if has_t1 and has_t2:
        parts.append(
            '<button class="btn ghost mini" id="ed-play-tier-toggle" type="button" '
            'aria-pressed="false" title="在“成片/预览版”与“时间线预览(粗剪)”之间切换">'
            '切到时间线预览 Tier 2</button>')
    parts.append("</div>")

    # ---- Tier 1: play the render (v2 §D) ----
    t1_hidden = "" if default_tier == 1 else " hidden"
    parts.append(f'<div class="ed-tier1" id="ed-tier1"{t1_hidden}>')
    if has_t1:
        kind_label = "成片 final" if src["kind"] == "final" else "预览版 proxy"
        # G3: a lazy slot (hidden) — /edit.js reveals it after /api/edit/dirty,
        # so the page GET never recompiles just to know if the final is stale.
        stale = ('<span class="chip ed-dirty" id="ed-play-stale" hidden '
                 'title="有未构建的修改,成片可能已过期">成片可能已过期</span>'
                 if src["kind"] == "final" else "")
        parts.append(
            f'<span class="chip ed-play-kind">{_e(kind_label)} · {_e(src["label"])}</span>'
            f'{stale}'
            '<video class="ed-play-video" id="ed-preview-video" preload="metadata" '
            f'playsinline controls src="{_e(src["url"])}"></video>'
            '<p class="muted ed-hint">空格 播放/暂停 · ←/→ 逐帧(Shift=1 秒) · '
            '点击下方标尺跳转 —— 播放头与多轨道联动。</p>')
    else:
        parts.append('<p class="muted ed-hint">还没有成片或预览版 —— 构建后在此播放最终画面'
                     '(转场/调色/混音全部生效)。</p>')
    parts.append("</div>")

    # ---- Tier 2 (round X §A): sequence per-clip previews of the timeline ----
    t2_hidden = "" if default_tier == 2 else " hidden"
    parts.append(f'<div class="ed-tier2" id="ed-tier2"{t2_hidden}>')
    if has_t2:
        pool = "".join(
            f'<video class="ed-t2-video" data-slot="{i}" playsinline preload="auto"></video>'
            for i in range(TIER2_POOL_SIZE))
        parts.append(
            f'<div class="ed-t2-pool" id="ed-t2-pool" data-pool-size="{TIER2_POOL_SIZE}">'
            f'{pool}</div>'
            '<div class="ed-t2-controls">'
            '<button class="btn ghost mini" id="ed-t2-play" type="button">'
            '▶ 播放时间线预览</button>'
            '<span class="muted ed-hint" id="ed-t2-status"></span></div>'
            f'<p class="muted ed-hint">{_e(TIER2_NOTE)} —— 按片段依次播放各自的 proxy/预览版,'
            '不做转场特效;空格/←→/标尺与 Tier 1 共用。'
            '<a href="#" id="ed-t2-clip-toggle">哪些片段还没生成预览?</a></p>'
            '<div class="ed-t2-clip-status muted ed-hint hidden" id="ed-t2-clip-status"></div>')
    else:
        parts.append('<p class="muted ed-hint">还没有编译的时间线 —— 构建一次后即可在渲染成片前'
                     '按剪辑顺序/裁剪预览(粗剪,无转场无混音)。</p>')
    parts.append("</div>")

    parts.append("</section>")
    return "".join(parts)


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


def _load_timeline(project: Any):
    """The compiled timeline (read-only truth for the lanes), or None before any
    build. Best-effort — a malformed timeline degrades the lanes to None, never
    a page error."""
    try:
        return project.load_timeline()
    except Exception:
        return None


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

/* -------- seam picker popover -------- */
.ed-seam-row { display: flex; flex-wrap: wrap; gap: .9rem; align-items: center; margin: .6rem 0; }
.ed-seam-row select, .ed-seam-row input { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .25rem .45rem; font: inherit; }
.ed-seam-btns { display: flex; flex-wrap: wrap; gap: .5rem; justify-content: flex-end; margin-top: .8rem; }
.ed-seam-hr { margin: .6rem 0; }
.ed-seam-hr:not(:empty) { border-top: 1px dashed var(--line); padding-top: .7rem; }

/* -------- multi-track lanes -------- */
.ed-lanes-panel { overflow-x: hidden; }
.ed-lanes-head { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.ed-zoom { display: flex; align-items: center; gap: .5rem; font-size: .84rem; }
.ed-lanes { overflow-x: auto; padding-bottom: .4rem; }
.ed-lane { display: flex; align-items: stretch; gap: .5rem; margin: .25rem 0; min-height: 34px; }
.ed-lane-label { flex: 0 0 96px; font-size: .78rem; color: var(--muted); display: flex; align-items: center; }
.ed-lane-track { position: relative; flex: 1 1 auto; min-width: 320px; height: 40px; background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; }
.ed-ruler .ed-lane-track { height: 20px; background: transparent; border-style: dashed; cursor: crosshair; }
.ed-lane-clip { position: absolute; top: 2px; bottom: 2px; overflow: hidden; border-radius: 4px; border: 1px solid var(--line); font: inherit; box-sizing: border-box; }
.ed-clip-t { display: block; padding: 0 .3rem; font-size: .72rem; line-height: 34px; white-space: nowrap; text-overflow: ellipsis; overflow: hidden; }
.ed-vclip { background: #22303f; color: var(--fg); cursor: pointer; }
.ed-vclip:hover { border-color: var(--accent); }
.ed-cclip { background: #2a2338; color: var(--fg); text-decoration: none; cursor: pointer; }
.ed-cclip:hover { border-color: var(--accent); }
.ed-aclip { background: #1d2b24; display: flex; align-items: center; }
.ed-wave { width: 100%; height: 100%; object-fit: fill; display: block; opacity: .85; }
.ed-lane-empty { padding: .3rem .5rem; font-size: .74rem; }
.ed-playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--star, #f0c040); left: 0; pointer-events: none; }
.ed-hint-strip { position: absolute; top: 0; bottom: 0; left: 0; right: 0; pointer-events: none; }
.ed-hint-mark { position: absolute; top: 0; bottom: 0; border-radius: 3px; border: 1px solid var(--err); background: rgba(200,60,60,.22); pointer-events: none; }
.ed-hint-mark.cue_early { border-color: var(--warn); background: rgba(200,150,40,.22); }
.ed-hint-mark.speech_no_cue { border-color: var(--accent); background: rgba(60,120,200,.20); }
.ed-lane-preview { margin-top: .8rem; max-width: 360px; }
.ed-lane-preview img { width: 100%; border-radius: 8px; border: 1px solid var(--line); background: #000; display: block; }

/* -------- sync hints list -------- */
.ed-sync-list { display: flex; flex-direction: column; gap: .4rem; }
.ed-sync-item { display: flex; flex-direction: column; gap: .15rem; padding: .4rem .6rem; border: 1px solid var(--line); border-left-width: 3px; border-radius: 6px; background: var(--panel2); font-size: .82rem; }
.ed-sync-item.cue_no_speech { border-left-color: var(--err); }
.ed-sync-item.cue_early { border-left-color: var(--warn); }
.ed-sync-item.speech_no_cue { border-left-color: var(--accent); }
.ed-sync-fix { color: var(--muted); font-size: .76rem; }
.ed-bound-ov { color: var(--accent); font-size: .68rem; margin-left: .15rem; }
button.ed-bound { background: none; cursor: pointer; }
button.ed-bound:hover .ed-bound-label { text-decoration: underline; }

@media (max-width: 700px) { .ed-look-pair { grid-template-columns: 1fr; } }

/* -------- round V: preview player (§D) -------- */
.ed-play-head { display: flex; align-items: baseline; gap: .7rem; flex-wrap: wrap; }
.ed-play-kind { background: var(--panel2); }
.ed-play-video { width: 100%; max-width: 640px; margin-top: .6rem; border-radius: 8px;
  border: 1px solid var(--line); background: #000; display: block; }

/* -------- round V: honest-undo panel (§C) -------- */
.ed-undo-panel.hidden { display: none; }
.ed-undo-head { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.ed-undo-list { display: flex; flex-direction: column; gap: .4rem; margin: .5rem 0; }
.ed-undo-item { display: flex; align-items: center; gap: .6rem; padding: .4rem .6rem;
  border: 1px solid var(--line); border-left-width: 3px; border-radius: 6px;
  background: var(--panel2); font-size: .84rem; }
.ed-undo-item.tier1 { border-left-color: var(--ok); }
.ed-undo-item.file { border-left-color: var(--warn); }
.ed-undo-item.append, .ed-undo-item.version, .ed-undo-item.history, .ed-undo-item.info {
  border-left-color: var(--line); }
.ed-undo-what { flex: 1 1 auto; }
.ed-undo-meta { color: var(--muted); font-size: .74rem; }
.ed-undo-empty { color: var(--muted); font-size: .82rem; }

/* -------- round V: magnet toggle + snap ticks (§B) -------- */
.ed-magnet[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); font-weight: 700; }
.ed-magnet[aria-pressed="false"] { opacity: .7; }
.ed-snapticks { position: absolute; top: 0; bottom: 0; left: 0; right: 0; pointer-events: none; }
.ed-snaptick { position: absolute; top: 40%; bottom: 0; width: 1px; background: var(--line); }
.ed-snaptick.sec { top: 15%; background: var(--muted); }

/* -------- round V: keyboard-map overlay (§A) -------- */
.ed-keymap-table { width: 100%; border-collapse: collapse; font-size: .86rem; margin: .6rem 0; }
.ed-keymap-table th, .ed-keymap-table td { text-align: left; padding: .3rem .5rem;
  border-bottom: 1px solid var(--line); vertical-align: top; }
.ed-keymap-table th { white-space: nowrap; width: 12rem; }
kbd { font-family: var(--mono); font-size: .8rem; background: var(--panel2);
  border: 1px solid var(--line); border-radius: 4px; padding: .05rem .35rem; }

/* -------- round V: footer hint bar (§A) -------- */
.ed-footer-hints { position: sticky; bottom: 0; z-index: 40; display: flex; flex-wrap: wrap;
  align-items: center; gap: .5rem 1rem; margin-top: 1.2rem; padding: .45rem .8rem;
  background: var(--panel); border-top: 1px solid var(--line); font-size: .78rem; color: var(--muted); }
.ed-footer-hints span { white-space: nowrap; }
.ed-foot-key { margin-left: auto; color: var(--accent); font: inherit; cursor: pointer;
  background: none; border: 1px solid var(--line); border-radius: 999px; padding: .1rem .7rem; }

/* -------- round V: visible focus (WCAG 2.4.7) on accelerated controls -------- */
.ed-magnet:focus-visible, .ed-foot-key:focus-visible, #ed-undo-btn:focus-visible,
#ed-keymap-btn:focus-visible, .ed-vclip:focus-visible, .ed-bound:focus-visible,
.ed-undo-item button:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}
.ed-play-video:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

/* -------- round X: Tier-1/Tier-2 toggle + pooled Tier-2 preview (§A) -------- */
.ed-tier1.hidden, .ed-tier2.hidden { display: none; }
.ed-t2-pool { position: relative; width: 100%; max-width: 640px; margin-top: .6rem; }
.ed-t2-video { width: 100%; border-radius: 8px; border: 1px solid var(--line);
  background: #000; display: none; }
.ed-t2-video.active { display: block; }
.ed-t2-controls { display: flex; align-items: center; gap: .7rem; margin-top: .5rem; }
.ed-t2-clip-status.hidden { display: none; }
.ed-t2-clip-status { margin-top: .4rem; }
.ed-t2-clip-row { display: flex; gap: .5rem; align-items: baseline; }
.ed-t2-clip-row .bad { color: var(--err); }
.ed-t2-clip-row .warn { color: var(--warn); }
#ed-play-tier-toggle:focus-visible, #ed-t2-play:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}

/* -------- round X: 字幕样式 caption style panel (§B) -------- */
.ed-cs-row { display: flex; flex-wrap: wrap; gap: .9rem; align-items: center; margin: .5rem 0; }
.ed-cs-row input, .ed-cs-row select {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .25rem .45rem; font: inherit;
}
.ed-cs-row input[type="text"] { width: 11rem; }
.ed-cs-preview { margin-top: .8rem; max-width: 360px; }
.ed-cs-preview img { width: 100%; border-radius: 8px; border: 1px solid var(--line);
  background: #000; display: block; }
"""


_EDIT_JS = r"""
"use strict";
(function () {

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

  // ============================================================ lanes view
  var lanes = document.getElementById("ed-lanes");
  var lanesTotal = lanes ? parseInt(lanes.getAttribute("data-total"), 10) || 1 : 1;
  var zoom = 1;

  function trackPx() {
    if (!lanes) return 0;
    return Math.max(320, (lanes.clientWidth - 110)) * zoom;
  }
  function layoutLanes() {
    if (!lanes) return;
    var w = trackPx();
    var tracks = lanes.querySelectorAll(".ed-lane-track");
    for (var i = 0; i < tracks.length; i++) {
      tracks[i].style.flex = "0 0 auto";
      tracks[i].style.width = w + "px";
    }
    var clips = lanes.querySelectorAll(".ed-lane-clip");
    for (var j = 0; j < clips.length; j++) {
      var s = parseInt(clips[j].getAttribute("data-ms-start"), 10) || 0;
      var d = parseInt(clips[j].getAttribute("data-ms-dur"), 10) || 1;
      clips[j].style.left = (s / lanesTotal * 100) + "%";
      clips[j].style.width = Math.max(0.4, d / lanesTotal * 100) + "%";
    }
    placeHintMarks();
    placeSnapTicks();
  }
  var zoomEl = document.getElementById("ed-zoom");
  if (zoomEl) zoomEl.addEventListener("input", function () {
    zoom = parseFloat(zoomEl.value) || 1;
    var zv = document.getElementById("ed-zoom-val");
    if (zv) zv.textContent = "×" + zoom;
    layoutLanes();
  });
  window.addEventListener("resize", layoutLanes);

  // ---- round V: playhead state + snapping + Tier-1 video playback ----
  var fps = lanes ? (parseInt(lanes.getAttribute("data-fps"), 10) || 24) : 24;
  var frameMs = Math.max(1, Math.round(1000 / fps));
  var snapOn = lanes ? lanes.getAttribute("data-snap") === "1" : true;
  var playheadMs = 0;
  var video = document.getElementById("ed-preview-video");
  // round X §A: which playback tier is currently driving Space/←→/the ruler —
  // 1 = play the render (Tier 1), 2 = pooled per-clip preview (Tier 2). Baked
  // server-side (data-default-tier) so the FIRST keypress is already correct.
  var playPanel = document.getElementById("ed-play-panel");
  var activeTier = playPanel ? (parseInt(playPanel.getAttribute("data-default-tier"), 10) || 0) : 0;

  function snapTargets() {
    // clip boundaries + caption cue edges + whole seconds (Native Cut v2 §B)
    var t = [];
    var clips = lanes ? lanes.querySelectorAll(".ed-vclip, .ed-cclip") : [];
    for (var i = 0; i < clips.length; i++) {
      var s = parseInt(clips[i].getAttribute("data-ms-start"), 10) || 0;
      var d = parseInt(clips[i].getAttribute("data-ms-dur"), 10) || 0;
      t.push(s); t.push(s + d);
    }
    for (var sec = 0; sec <= lanesTotal; sec += 1000) t.push(sec);
    return t;
  }
  function applySnap(ms) {
    if (!snapOn) return ms;
    var best = ms, bestD = Infinity;
    var px = trackPx();
    var msPerPx = px > 0 ? (lanesTotal / px) : 0;
    var thresh = Math.max(frameMs, msPerPx * 8);  // ~8px, but never below a frame
    var tg = snapTargets();
    for (var i = 0; i < tg.length; i++) {
      var dd = Math.abs(tg[i] - ms);
      if (dd < bestD && dd <= thresh) { bestD = dd; best = tg[i]; }
    }
    return Math.round(best);
  }
  function placeSnapTicks() {
    var host = document.getElementById("ed-snapticks");
    if (!host) return;
    host.textContent = "";
    for (var sec = 0; sec <= lanesTotal; sec += 1000) {
      var d = document.createElement("div");
      d.className = "ed-snaptick sec";
      d.style.left = (sec / lanesTotal * 100) + "%";
      host.appendChild(d);
    }
  }
  function stillFrameAt(ms) {
    var vclips = lanes ? lanes.querySelectorAll(".ed-vclip") : [];
    for (var i = 0; i < vclips.length; i++) {
      var s = parseInt(vclips[i].getAttribute("data-ms-start"), 10) || 0;
      var d = parseInt(vclips[i].getAttribute("data-ms-dur"), 10) || 0;
      var src = vclips[i].getAttribute("data-src");
      if (src && ms >= s && ms < s + d) {
        var inMs = parseInt(vclips[i].getAttribute("data-in"), 10) || 0;
        var off = inMs + (ms - s);
        var fig = document.getElementById("ed-lane-preview");
        var img = document.getElementById("ed-lane-preview-img");
        var cap = document.getElementById("ed-lane-preview-cap");
        if (fig && img) {
          img.src = "/edit/frame?take=" + encodeURIComponent(src) + "&ms=" + off + "&w=360";
          if (cap) cap.textContent = vclips[i].getAttribute("data-shot") + " · " + ms + "ms";
          fig.hidden = false;
        }
        return;
      }
    }
  }
  function paintPlayhead(ms) {
    var ph = document.getElementById("ed-playhead");
    if (ph) ph.style.left = (ms / lanesTotal * 100) + "%";
  }
  // seekTo: move the playhead (snapping unless bypassed), seek the <video>, and
  // show a still frame while paused. opts.fromVideo = driven by timeupdate (no
  // re-seek, no still); opts.bypass = Alt held / frame-step (skip snapping).
  // round X §A: when Tier 2 is active, the "video" driven is whichever pool
  // slot is playing the covering clip — t2SeekAndPlay owns that.
  function seekTo(ms, opts) {
    opts = opts || {};
    ms = Math.max(0, Math.min(lanesTotal, Math.round(ms)));
    if (!opts.fromVideo && !opts.bypass) ms = applySnap(ms);
    playheadMs = ms;
    paintPlayhead(ms);
    if (activeTier === 2) { t2SeekAndPlay(ms, t2Playing); return; }
    if (video && !opts.fromVideo) { try { video.currentTime = ms / 1000; } catch (e) {} }
    if (!video || video.paused) stillFrameAt(ms);
  }
  if (video) {
    video.addEventListener("timeupdate", function () {
      if (video.paused) return;
      seekTo(video.currentTime * 1000, { fromVideo: true });
    });
  }
  function togglePlay() {
    if (activeTier === 2) {
      if (t2Playing) { t2Playing = false; t2PauseActive(); }
      else { t2Playing = true; t2SeekAndPlay(playheadMs, true); }
      return;
    }
    if (!video) { toast("先构建成片或预览版再播放", false); return; }
    if (video.paused) { video.play().catch(function () {}); }
    else { video.pause(); }
  }
  function stepFrame(dir, big) { seekTo(playheadMs + dir * (big ? 1000 : frameMs), { bypass: true }); }

  // ======================================================= round X §A: Tier 2
  // pooled per-clip timeline preview (REPORTS ROUND-V-REFERENCES-2.md §1d —
  // the Chromium ~75-WebMediaPlayer-per-frame cap: never one <video> per clip,
  // a small pool with the current clip playing + the next preloading).
  var t2Pool = Array.prototype.slice.call(document.querySelectorAll(".ed-t2-video"));
  var T2_POOL_SIZE = t2Pool.length;  // server-rendered pool size (edit.py TIER2_POOL_SIZE)
  var t2ActiveSlot = 0;
  var t2CurrentClip = -1;
  var t2Playing = false;
  var t2Manifest = null;
  var t2PollTimer = null;

  function t2ShowSlot(slot) {
    for (var i = 0; i < t2Pool.length; i++) t2Pool[i].classList.toggle("active", i === slot);
  }
  function t2StatusNote(text) {
    var el = document.getElementById("ed-t2-status");
    if (el) el.textContent = text || "";
  }
  function t2PauseActive() {
    var v = t2Pool[t2ActiveSlot];
    if (v) v.pause();
  }
  function t2RenderClipStatus() {
    var host = document.getElementById("ed-t2-clip-status");
    if (!host || !t2Manifest) return;
    host.textContent = "";
    var clips = t2Manifest.clips || [];
    var problems = clips.filter(function (c) { return c.status !== "ready"; });
    if (!problems.length) {
      var ok = document.createElement("p");
      ok.textContent = "全部 " + clips.length + " 个片段的预览已就绪。";
      host.appendChild(ok);
      return;
    }
    problems.forEach(function (c) {
      var row = document.createElement("div");
      row.className = "ed-t2-clip-row";
      var tag = document.createElement("span");
      tag.className = c.status === "pending" ? "warn" : "bad";
      tag.textContent = c.shot + "：" + (c.status === "pending" ? "生成中…" : (c.reason || "不可用"));
      row.appendChild(tag);
      host.appendChild(row);
    });
  }
  function t2LoadManifest(cb) {
    fetch("/api/edit/playback-manifest").then(function (r) { return r.json(); })
      .then(function (d) {
        t2Manifest = d;
        t2RenderClipStatus();
        var pendingN = (d.clips || []).filter(function (c) { return c.status === "pending"; }).length;
        if (pendingN) {
          t2StatusNote(pendingN + " 个片段的预览正在生成…");
          if (d.job && d.job.id && d.job.state !== "done" && d.job.state !== "failed") {
            pollJob(d.job.id, function () { t2LoadManifest(cb); });
          }
        } else if (t2Manifest.clips && t2Manifest.clips.length) {
          t2StatusNote("");
        } else {
          t2StatusNote("时间线里还没有主轨道片段");
        }
        if (cb) cb(d);
      }).catch(function () { t2StatusNote("预览清单加载失败"); });
  }
  function t2ClipAt(ms) {
    if (!t2Manifest) return null;
    var clips = t2Manifest.clips || [];
    for (var i = 0; i < clips.length; i++) {
      var c = clips[i];
      if (ms >= c.start_ms && ms < c.start_ms + c.duration_ms) return { clip: c, index: i };
    }
    if (clips.length && ms >= clips[clips.length - 1].start_ms + clips[clips.length - 1].duration_ms) {
      return null;  // past the last clip — nothing covers the playhead
    }
    return clips.length ? { clip: clips[0], index: 0 } : null;
  }
  // Preload the NEXT ready clip into the pool slot that will become active
  // next — the "second hidden <video> pre-seeked to the next clip" the 75-
  // player-cap discipline recommends (REPORTS §1d), without ever exceeding
  // T2_POOL_SIZE live elements.
  function t2PreloadNext(index) {
    if (T2_POOL_SIZE < 2) return;
    var clips = (t2Manifest && t2Manifest.clips) || [];
    var next = clips[index + 1];
    if (!next || next.status !== "ready") return;
    var slot = t2Pool[(t2ActiveSlot + 1) % T2_POOL_SIZE];
    if (slot.getAttribute("data-source") !== next.source) {
      slot.src = next.preview_url;
      slot.setAttribute("data-source", next.source);
      try { slot.currentTime = (next.source_in_ms || 0) / 1000; } catch (e) {}
    }
  }
  function t2ActivateClip(entry, atMs, autoplay) {
    var c = entry.clip;
    if (c.status !== "ready") {
      t2PauseActive();
      t2StatusNote(c.status === "pending"
        ? ("『" + c.shot + "』的预览尚未生成,正在生成…")
        : ("『" + c.shot + "』不可预览:" + (c.reason || "未知原因")));
      return;
    }
    var slot = t2Pool[t2ActiveSlot];
    var withinMs = Math.max(0, atMs - c.start_ms);
    // previews are same-duration scaled copies (only the FRAME is rescaled,
    // never the timing axis — media/webpreview.py never retimes), so
    // source_in_ms maps straight onto currentTime with no scale correction.
    var target = ((c.source_in_ms || 0) + withinMs) / 1000;
    if (slot.getAttribute("data-source") !== c.source) {
      slot.src = c.preview_url;
      slot.setAttribute("data-source", c.source);
    }
    var apply = function () {
      try { slot.currentTime = target; } catch (e) {}
      if (autoplay) slot.play().catch(function () {});
    };
    if (slot.readyState >= 1) apply();
    else slot.addEventListener("loadedmetadata", apply, { once: true });
    t2ShowSlot(t2ActiveSlot);
    t2CurrentClip = entry.index;
    t2StatusNote("");
    t2PreloadNext(entry.index);
  }
  function t2SeekAndPlay(ms, autoplay) {
    if (!t2Manifest) { t2LoadManifest(function () { t2SeekAndPlay(ms, autoplay); }); return; }
    var entry = t2ClipAt(ms);
    if (!entry) { t2PauseActive(); t2StatusNote("播放头不在任何已编译片段上"); return; }
    t2ActivateClip(entry, ms, autoplay !== false && t2Playing);
  }
  // on the ACTIVE slot's timeupdate: advance the global playhead, and swap the
  // active pool slot to the next clip once playback crosses its boundary —
  // "current clip plays, next preloads; on clip end swap to the next pool
  // element" (the contract this whole block implements).
  t2Pool.forEach(function (v, slot) {
    v.addEventListener("timeupdate", function () {
      if (activeTier !== 2 || slot !== t2ActiveSlot || v.paused || !t2Manifest) return;
      var clips = t2Manifest.clips || [];
      var c = clips[t2CurrentClip];
      if (!c) return;
      var globalMs = c.start_ms + Math.max(0, v.currentTime * 1000 - (c.source_in_ms || 0));
      playheadMs = Math.min(lanesTotal, globalMs);
      paintPlayhead(playheadMs);
      if (globalMs >= c.start_ms + c.duration_ms - 20) {
        var next = clips[t2CurrentClip + 1];
        if (next) {
          v.pause();
          t2ActiveSlot = (t2ActiveSlot + 1) % T2_POOL_SIZE;
          t2ActivateClip({ clip: next, index: t2CurrentClip + 1 }, next.start_ms, true);
        } else {
          v.pause();
          t2Playing = false;
          t2StatusNote("时间线预览播放完毕");
        }
      }
    });
  });
  var t2PlayBtn = document.getElementById("ed-t2-play");
  if (t2PlayBtn) t2PlayBtn.addEventListener("click", function () {
    if (activeTier !== 2) setTier(2);
    togglePlay();
  });
  var t2ClipToggle = document.getElementById("ed-t2-clip-toggle");
  if (t2ClipToggle) t2ClipToggle.addEventListener("click", function (e) {
    e.preventDefault();
    var host = document.getElementById("ed-t2-clip-status");
    if (!host) return;
    var willShow = host.classList.contains("hidden");
    host.classList.toggle("hidden", !willShow);
    if (willShow && !t2Manifest) t2LoadManifest();
  });

  // -- Tier-1 / Tier-2 toggle (only rendered when BOTH are genuinely available) --
  function setTier(n) {
    if (n === activeTier) return;
    if (activeTier === 2) { t2Playing = false; t2PauseActive(); }
    else if (video) video.pause();
    activeTier = n;
    var t1 = document.getElementById("ed-tier1"), t2 = document.getElementById("ed-tier2");
    if (t1) t1.classList.toggle("hidden", n !== 1);
    if (t2) t2.classList.toggle("hidden", n !== 2);
    var btn = document.getElementById("ed-play-tier-toggle");
    if (btn) {
      btn.setAttribute("aria-pressed", n === 2 ? "true" : "false");
      btn.textContent = n === 1 ? "切到时间线预览 Tier 2" : "切到成片/预览版 Tier 1";
    }
    if (n === 2 && !t2Manifest) t2LoadManifest();
    seekTo(playheadMs, { bypass: true });
  }
  var tierToggle = document.getElementById("ed-play-tier-toggle");
  if (tierToggle) tierToggle.addEventListener("click", function () {
    setTier(activeTier === 1 ? 2 : 1);
  });
  if (activeTier === 2) t2LoadManifest();

  var ruler = document.getElementById("ed-ruler");
  if (ruler) ruler.addEventListener("click", function (e) {
    var track = ruler.querySelector(".ed-lane-track");
    if (!track) return;
    var r = track.getBoundingClientRect();
    if (r.width <= 0) return;
    seekTo((e.clientX - r.left) / r.width * lanesTotal, { bypass: e.altKey });
  });

  // ---------------------------------------------------------- sync hints
  function placeHintMarks() {
    var strip = document.getElementById("ed-synchints-strip");
    if (!strip || !window.__edHints) return;
    strip.textContent = "";
    window.__edHints.forEach(function (h) {
      var m = document.createElement("div");
      m.className = "ed-hint-mark " + h.kind;
      m.title = h.message;
      m.style.left = (h.start_ms / lanesTotal * 100) + "%";
      m.style.width = Math.max(0.5, (h.end_ms - h.start_ms) / lanesTotal * 100) + "%";
      strip.appendChild(m);
    });
  }
  function loadSyncHints() {
    var note = document.getElementById("ed-sync-note");
    var list = document.getElementById("ed-synchints-list");
    if (!list) return;
    fetch("/api/edit/synchints").then(function (r) { return r.json(); }).then(function (d) {
      window.__edHints = d.hints || [];
      placeHintMarks();
      list.textContent = "";
      if (note) {
        if (d.note) note.textContent = d.note;
        else note.textContent = window.__edHints.length
          ? ("发现 " + window.__edHints.length + " 条字幕/语音不同步") : "字幕与语音看起来对齐 ✓";
      }
      window.__edHints.forEach(function (h) {
        var it = document.createElement("div");
        it.className = "ed-sync-item " + h.kind;
        var head = document.createElement("div");
        head.textContent = (h.shot ? (h.shot + " · ") : "") + h.message
          + " (" + h.start_ms + "–" + h.end_ms + "ms)";
        it.appendChild(head);
        if (h.text) {
          var tx = document.createElement("div");
          tx.className = "ed-sync-fix"; tx.textContent = "字幕:" + h.text;
          it.appendChild(tx);
        }
        var fx = document.createElement("div");
        fx.className = "ed-sync-fix"; fx.textContent = "→ " + h.fix;
        it.appendChild(fx);
        list.appendChild(it);
      });
    }).catch(function () { if (note) note.textContent = "同步检查不可用"; });
  }

  // ---------------------------------------------------------- seam picker
  var seamModal = document.getElementById("ed-seam-modal");
  var seamState = null;
  function openSeam(btn) {
    if (!seamModal) return;
    seamState = {
      out: btn.getAttribute("data-out"),
      next: btn.getAttribute("data-next"),
      max: btn.getAttribute("data-max"),
      degraded: btn.getAttribute("data-degraded") === "1"
    };
    document.getElementById("ed-seam-title").textContent =
      "切换点转场:" + seamState.out + " → " + seamState.next;
    var sub = document.getElementById("ed-seam-sub");
    sub.textContent = btn.getAttribute("data-has-override") === "1"
      ? "当前为逐切换点覆盖" : "当前跟随全局默认";
    document.getElementById("ed-seam-type").value = btn.getAttribute("data-eff-type") || "cut";
    document.getElementById("ed-seam-dur").value = btn.getAttribute("data-eff-dur") || "0";
    var cap = document.getElementById("ed-seam-cap");
    cap.textContent = seamState.max ? ("时长上限 " + seamState.max + "ms(相邻较短片段的一半)") : "";
    var hr = document.getElementById("ed-seam-handle");
    hr.textContent = "";
    if (seamState.degraded) {
      var p = document.createElement("span");
      p.textContent = "上次构建因缺少手柄降级为硬切。";
      var b = document.createElement("button");
      b.className = "btn ghost"; b.textContent = "补拍手柄"; b.id = "ed-seam-handle-btn";
      b.addEventListener("click", handleRebuild);
      hr.appendChild(p); hr.appendChild(document.createTextNode(" ")); hr.appendChild(b);
    }
    seamModal.classList.remove("hidden");
  }
  function closeSeam() { if (seamModal) seamModal.classList.add("hidden"); seamState = null; }
  function seamSave(body) {
    body.shot = seamState.out;
    post("/api/edit/transition-override", body).then(function (res) {
      if (res.status === 200) {
        // issue #69: a write on the true last segment (no out-edge) is
        // accepted but inert — the server says so in res.data.warnings.
        var warn = res.data && res.data.warnings && res.data.warnings[0];
        toast(warn ? ("已更新,但 " + warn) : "切换点转场已更新", true);
        reloadSoon();
      }
      else toast(errText(res), false);
    });
  }
  function handleRebuild() {
    if (!seamState) return;
    post("/api/edit/handle-rebuild-plan", { shot: seamState.out }).then(function (res) {
      if (res.status !== 200) { toast(errText(res), false); return; }
      var pl = res.data;
      if (!pl.supported) { toast(pl.advisory || "无法补拍手柄", false); return; }
      var msg = "补拍手柄:加长到 " + pl.extended_ms + "ms 后回裁,预估 "
        + (pl.estimated_cost || 0) + " " + (pl.currency || "") + "(供应商 "
        + (pl.provider || "?") + ")。继续?";
      if (!window.confirm(msg)) return;
      post("/api/edit/handle-rebuild",
        { shot: seamState.out, assume_yes: true, provider: pl.provider })
        .then(function (r2) {
          if (r2.status !== 202) { toast(errText(r2), false); return; }
          toast("补拍手柄排队…", true);
          var sid = seamState.out; closeSeam();
          pollJob(r2.data.job.id, function (job) {
            if (!job) { toast("补拍手柄超时", false); return; }
            if (job.state === "failed") { toast(job.error || "补拍手柄失败", false); return; }
            var nm = job.result && job.result.trim_take;
            toast("已补拍手柄 · take " + nm + "(重建以应用转场)", true);
            if (nm) post("/api/select", { shot: sid, take: nm }).then(function () { reloadSoon(); });
          });
        });
    });
  }
  var seamOk = document.getElementById("ed-seam-ok");
  if (seamOk) seamOk.addEventListener("click", function () {
    if (!seamState) return;
    var type = document.getElementById("ed-seam-type").value;
    var dur = parseInt(document.getElementById("ed-seam-dur").value, 10);
    if (isNaN(dur)) dur = 0;
    if (seamState.max && dur > parseInt(seamState.max, 10)) {
      toast("时长超过上限 " + seamState.max + "ms", false); return;
    }
    seamSave({ type: type, duration_ms: dur });
  });
  var seamCut = document.getElementById("ed-seam-cut");
  if (seamCut) seamCut.addEventListener("click", function () { seamSave({ cut: true }); });
  var seamReset = document.getElementById("ed-seam-reset");
  if (seamReset) seamReset.addEventListener("click", function () { seamSave({ action: "reset" }); });
  var seamCancel = document.getElementById("ed-seam-cancel");
  if (seamCancel) seamCancel.addEventListener("click", closeSeam);

  // lanes + seams click delegation (separate from the strip handler above)
  document.addEventListener("click", function (e) {
    var seam = e.target.closest(".ed-bound");
    if (seam && seam.tagName === "BUTTON") { openSeam(seam); return; }
    var vc = e.target.closest(".ed-vclip");
    if (vc) { seekTo(parseInt(vc.getAttribute("data-ms-start"), 10) || 0); return; }
  });

  // ============================================ round V: keyboard + snap + undo
  var zoomVal = document.getElementById("ed-zoom-val");
  function setZoom(z) {
    zoom = Math.max(1, Math.min(8, z));
    if (zoomEl) zoomEl.value = String(zoom);
    if (zoomVal) zoomVal.textContent = "×" + zoom;
    layoutLanes();
  }
  function cycleZoom() { setZoom(zoom >= 4 ? 1 : (zoom < 2 ? 2 : 4)); }

  // -- snapping magnet toggle (persisted per-user, §B) --
  function setSnap(on, persist) {
    snapOn = !!on;
    if (lanes) lanes.setAttribute("data-snap", snapOn ? "1" : "0");
    var btn = document.getElementById("ed-snap-toggle");
    if (btn) {
      btn.setAttribute("aria-pressed", snapOn ? "true" : "false");
      btn.textContent = "吸附 N " + (snapOn ? "开" : "关");
    }
    if (persist) post("/api/edit/snap", { enabled: snapOn });
    toast(snapOn ? "吸附已开 (snap on)" : "吸附已关 (snap off)", true);
  }
  var snapBtn = document.getElementById("ed-snap-toggle");
  if (snapBtn) snapBtn.addEventListener("click", function () { setSnap(!snapOn, true); });

  // -- edit-point jump (↑/↓) + the covered clip for I/O --
  function boundaries() {
    var b = [0, lanesTotal];
    var vclips = lanes ? lanes.querySelectorAll(".ed-vclip") : [];
    for (var i = 0; i < vclips.length; i++) {
      var s = parseInt(vclips[i].getAttribute("data-ms-start"), 10) || 0;
      var d = parseInt(vclips[i].getAttribute("data-ms-dur"), 10) || 0;
      b.push(s); b.push(s + d);
    }
    b = b.filter(function (v, i, a) { return a.indexOf(v) === i; });
    b.sort(function (x, y) { return x - y; });
    return b;
  }
  function jumpEdit(dir) {
    var b = boundaries(), i;
    if (dir < 0) {
      for (i = b.length - 1; i >= 0; i--) if (b[i] < playheadMs - 1) { seekTo(b[i], { bypass: true }); return; }
      seekTo(0, { bypass: true });
    } else {
      for (i = 0; i < b.length; i++) if (b[i] > playheadMs + 1) { seekTo(b[i], { bypass: true }); return; }
      seekTo(lanesTotal, { bypass: true });
    }
  }
  function coveringVClip(ms) {
    var vclips = lanes ? lanes.querySelectorAll(".ed-vclip") : [];
    for (var i = 0; i < vclips.length; i++) {
      var s = parseInt(vclips[i].getAttribute("data-ms-start"), 10) || 0;
      var d = parseInt(vclips[i].getAttribute("data-ms-dur"), 10) || 0;
      if (ms >= s && ms < s + d) return vclips[i];
    }
    return null;
  }
  // I / O: set the covered clip's trim in/out at the playhead — PREFILL the
  // existing trim inputs + focus 裁剪 (never a silent re-encode, §A).
  function setInOut(which) {
    var vc = coveringVClip(playheadMs);
    if (!vc) { toast("播放头不在任何主轨道片段上", false); return; }
    var sid = vc.getAttribute("data-shot");
    var s = parseInt(vc.getAttribute("data-ms-start"), 10) || 0;
    var inSrc = parseInt(vc.getAttribute("data-in"), 10) || 0;
    var off = Math.max(0, inSrc + (playheadMs - s));
    openInspector(sid);
    var insp = inspectorFor(sid);
    if (!insp) { toast("该分镜暂无编辑面板", false); return; }
    var field = insp.querySelector(which === "in" ? ".ed-in" : ".ed-out");
    if (!field) { toast("先在审片页给该分镜选一个 take 才能裁剪", false); return; }
    field.value = String(off);
    var applyBtn = insp.querySelector(".ed-trim");
    if (applyBtn) applyBtn.focus();
    toast((which === "in" ? "入点 in" : "出点 out") + " 设为 " + off + "ms — 核对后点“裁剪成新 take”", true);
  }

  // -- keyboard-map overlay (?) --
  var keymapModal = document.getElementById("ed-keymap-modal");
  function keymapToggle() { if (keymapModal) keymapModal.classList.toggle("hidden"); }
  function keymapClose() { if (keymapModal) keymapModal.classList.add("hidden"); }
  ["ed-keymap-btn", "ed-keymap-foot"].forEach(function (id) {
    var b = document.getElementById(id);
    if (b) b.addEventListener("click", keymapToggle);
  });
  var keymapCloseBtn = document.getElementById("ed-keymap-close");
  if (keymapCloseBtn) keymapCloseBtn.addEventListener("click", keymapClose);

  // -- honest-undo panel (§C) --
  var undoPanel = document.getElementById("ed-undo-panel");
  var undoBtn = document.getElementById("ed-undo-btn");
  function undoRender(items) {
    var list = document.getElementById("ed-undo-list");
    if (!list) return;
    list.textContent = "";
    if (!items || !items.length) {
      var e = document.createElement("p");
      e.className = "ed-undo-empty";
      e.textContent = "还没有可撤销的改动。编辑转场 / 调色 / 时长后,这里列出最近改动。";
      list.appendChild(e); return;
    }
    items.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "ed-undo-item " + (it.tier === 1 ? "tier1" : it.tier);
      var what = document.createElement("span");
      what.className = "ed-undo-what"; what.textContent = it.what;
      var meta = document.createElement("span");
      meta.className = "ed-undo-meta"; meta.textContent = it.actor + " · " + (it.ts || "");
      row.appendChild(what); row.appendChild(meta);
      if (it.revertable) {
        var b = document.createElement("button");
        b.className = "btn ghost mini";
        b.textContent = it.tier === "file" ? "回滚文件" : "撤销";
        if (it.label) b.title = it.label;
        b.addEventListener("click", function () { undoRevert(it); });
        row.appendChild(b);
      } else if (it.note) {
        var n = document.createElement("span");
        n.className = "ed-undo-meta"; n.textContent = it.note;
        row.appendChild(n);
      }
      list.appendChild(row);
    });
  }
  function undoLoad() {
    fetch("/api/edit/undo").then(function (r) { return r.json(); }).then(function (d) {
      undoRender(d.events || []);
    }).catch(function () {});
  }
  function undoRevert(it) {
    if (it.tier === "file") {
      if (!window.confirm("将 " + (it.file || it.label || "该文件") +
          " 从 git 回滚到上一版本?这会覆盖当前文本改动。")) return;
      post("/api/git/rollback-file", { path: it.file }).then(function (res) {
        if (res.status === 200) { toast("已回滚 " + it.file, true); reloadSoon(); }
        else toast(errText(res), false);
      });
      return;
    }
    post("/api/edit/revert", { index: it.index, ts: it.ts }).then(function (res) {
      if (res.status === 200) { toast("已撤销:" + (res.data.reverted || it.what), true); reloadSoon(); }
      else toast(errText(res), false);
    });
  }
  function undoToggle(forceOpen) {
    if (!undoPanel) return;
    var willShow = forceOpen === true || undoPanel.classList.contains("hidden");
    undoPanel.classList.toggle("hidden", !willShow);
    if (undoBtn) undoBtn.setAttribute("aria-expanded", willShow ? "true" : "false");
    if (willShow) undoLoad();
  }
  if (undoBtn) undoBtn.addEventListener("click", function () { undoToggle(); });
  var snapshotBtn = document.getElementById("ed-snapshot-btn");
  if (snapshotBtn) snapshotBtn.addEventListener("click", function () {
    post("/api/git/snapshot", { label: "编辑前快照" }).then(function (res) {
      if (res.status === 200) {
        toast(res.data.clean ? "工程已是干净快照" : ("已快照 " + (res.data.sha || "")), true);
      } else toast(errText(res), false);
    });
  });

  // Ctrl+Z / Ctrl+Shift+Z: honest git-backed undo/redo of the last edit (redo is
  // the same op on the newest event — a revert is itself an event, §C).
  function revertNewest() {
    fetch("/api/edit/undo").then(function (r) { return r.json(); }).then(function (d) {
      var items = d.events || [], top = null;
      for (var i = 0; i < items.length; i++) { if (items[i].revertable) { top = items[i]; break; } }
      if (!top) { toast("没有可撤销的改动", false); return; }
      undoToggle(true);
      undoRevert(top);
    }).catch(function () {});
  }

  // ---- the ONE keydown accelerator handler (mirrors page.py discipline) ----
  function typingInField(t) {
    if (!t) return false;
    var tag = (t.tagName || "").toLowerCase();
    return tag === "input" || tag === "select" || tag === "textarea" || t.isContentEditable;
  }
  document.addEventListener("keydown", function (e) {
    // Esc always closes popovers, even from a field.
    if (e.key === "Escape") {
      keymapClose();
      if (typeof closeSeam === "function") closeSeam();
      if (typeof closeModal === "function") closeModal();
      return;
    }
    // typing in a field NEVER triggers an accelerator (input-focus guard).
    if (typingInField(e.target) || typingInField(document.activeElement)) return;
    if (e.altKey) return;  // Alt is the snap-bypass modifier, not an accelerator
    if (e.ctrlKey || e.metaKey) {
      if ((e.key || "").toLowerCase() === "z") { e.preventDefault(); revertNewest(); }
      return;  // ignore every other ctrl/meta chord
    }
    switch (e.key) {
      case " ": case "Spacebar": e.preventDefault(); togglePlay(); break;
      case "ArrowLeft": e.preventDefault(); stepFrame(-1, e.shiftKey); break;
      case "ArrowRight": e.preventDefault(); stepFrame(1, e.shiftKey); break;
      case "ArrowUp": e.preventDefault(); jumpEdit(-1); break;
      case "ArrowDown": e.preventDefault(); jumpEdit(1); break;
      case "Home": e.preventDefault(); seekTo(0, { bypass: true }); break;
      case "End": e.preventDefault(); seekTo(lanesTotal, { bypass: true }); break;
      case "i": case "I": setInOut("in"); break;
      case "o": case "O": setInOut("out"); break;
      case "n": case "N": setSnap(!snapOn, true); break;
      case "z": case "Z": cycleZoom(); break;
      case "[": setZoom(zoom - 1); break;
      case "]": setZoom(zoom + 1); break;
      case "?": e.preventDefault(); keymapToggle(); break;
      default: break;
    }
  });

  if (lanes) { layoutLanes(); loadSyncHints(); }

  // G3: the dirty / stale badges are fetched AFTER first paint from a small
  // endpoint (the page GET no longer recompiles via build.explain for them) —
  // the same lazy idiom the review boards use. A fetch failure leaves the
  // badges hidden (an honest "unknown"), never a blank or an error.
  fetch("/api/edit/dirty").then(function (r) { return r.json(); }).then(function (d) {
    if (!d || !d.unbuilt) return;
    var chip = document.getElementById("ed-dirty-chip");
    if (chip) { chip.hidden = false; if (d.why) chip.title = d.why; }
    var stale = document.getElementById("ed-play-stale");
    if (stale) stale.hidden = false;
  }).catch(function () {});

  // preview once on load if a look is set beyond none
  if (lookPreset && lookPreset.getAttribute("data-preset") !== "none") refreshLookPreview();

  // ==================================================== round X §B: 字幕样式
  var csPanel = document.querySelector(".ed-capstyle-panel");
  if (csPanel) {
    var csFields = { font: "ed-cs-font", size: "ed-cs-size", primary_colour: "ed-cs-primary",
      outline: "ed-cs-outline", margin_v: "ed-cs-marginv", alignment: "ed-cs-align" };
    function csValue(key) {
      var el = document.getElementById(csFields[key]);
      if (!el) return null;
      var v = el.value;
      if (v === "" || v === null) return null;
      if (key === "font" || key === "primary_colour") return v;
      var n = parseInt(v, 10);
      return isNaN(n) ? null : n;
    }
    function csBody() {
      var body = {};
      Object.keys(csFields).forEach(function (k) { body[k] = csValue(k); });
      return body;
    }
    function csPreviewSrc() {
      var q = Object.keys(csFields).map(function (k) {
        var v = csValue(k);
        return v === null ? "" : (k + "=" + encodeURIComponent(v));
      }).filter(Boolean).join("&");
      return "/edit/caption-style-preview" + (q ? ("?" + q) : "");
    }
    var csDebounce = null;
    function csRefreshPreview() {
      if (csDebounce) clearTimeout(csDebounce);
      csDebounce = setTimeout(function () {
        var img = document.getElementById("ed-cs-preview-img");
        if (img) img.src = csPreviewSrc();
      }, 250);
    }
    csPanel.addEventListener("input", csRefreshPreview);
    var csApply = document.getElementById("ed-cs-apply");
    if (csApply) csApply.addEventListener("click", function () {
      post("/api/edit/caption-style", csBody()).then(function (res) {
        if (res.status === 200) toast("字幕样式已保存", true);
        else toast(errText(res), false);
      });
    });
    var csReset = document.getElementById("ed-cs-reset");
    if (csReset) csReset.addEventListener("click", function () {
      Object.keys(csFields).forEach(function (k) {
        var el = document.getElementById(csFields[k]);
        if (el && k !== "alignment") el.value = "";
      });
      var alignEl = document.getElementById(csFields.alignment);
      if (alignEl) alignEl.value = "2";
      post("/api/edit/caption-style",
        { font: null, size: null, primary_colour: null, outline: null,
          margin_v: null, alignment: null }).then(function (res) {
        if (res.status === 200) { toast("字幕样式已恢复默认", true); csRefreshPreview(); }
        else toast(errText(res), false);
      });
    });
    csRefreshPreview();
  }
})();
"""
