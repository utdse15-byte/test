"""`manju explain` — why will the next build do what it will do?

A build system earns trust by being able to justify itself: for every shot,
the picture and voice states WITH the hash evidence; for the timeline, whether
a recompile would change it (fingerprint comparison); for the final/proxy,
whether the content key matches (render skipped) or not (re-render, and
because of what). Everything here is read-only — explain never mutates the
project and never spends money.
"""

from __future__ import annotations

from typing import Any

from ..core.container import Project
from ..core.hashing import short_hash
from .stale import evaluate_all
from .voice import VoiceState, evaluate_all_voices


def _shot_explanations(project: Project) -> list[dict[str, Any]]:
    voices = {v.shot_id: v for v in evaluate_all_voices(project)}
    shots: list[dict[str, Any]] = []
    for st in evaluate_all(project):
        entry: dict[str, Any] = {
            "shot": st.shot_id,
            "video": {
                "state": st.state.value,
                "selected_take": st.selected_take,
                "spec_hash": short_hash(st.spec_hash, 12),
            },
        }
        if st.take is not None:
            entry["video"]["take_spec_hash"] = short_hash(st.take.sidecar.spec_hash, 12)
        if st.note:
            entry["video"]["why"] = st.note
        voice = voices.get(st.shot_id)
        if voice is not None and voice.state != VoiceState.NOT_NEEDED:
            entry["voice"] = {
                "state": voice.state.value,
                "voice_hash": short_hash(voice.voice_hash, 12),
            }
            if voice.note:
                entry["voice"]["why"] = voice.note
        shots.append(entry)
    return shots


def _timeline_explanation(project: Project) -> dict[str, Any]:
    rules = project.load_rules()
    current = project.load_timeline()
    info: dict[str, Any] = {
        "mode": rules.mode,
        "exists": current is not None,
        "captions_mode": rules.captions.mode,
    }
    if current is not None:
        info["compiled_from"] = short_hash(current.meta.compiled_from, 12)
    try:
        from ..media.probe import probe_duration_ms
        from ..timeline.compiler import compile_timeline, gather_compile_input

        would_be = compile_timeline(gather_compile_input(project, probe_duration_ms))
        info["would_compile_to"] = short_hash(would_be.meta.compiled_from, 12)
        if current is None:
            info["verdict"] = "compile (no timeline yet)"
        elif rules.mode == "manual":
            info["verdict"] = ("manual mode: timeline.json is human truth; build only "
                               "refreshes timeline.generated.json (§6)")
        elif would_be.meta.compiled_from == current.meta.compiled_from:
            info["verdict"] = "unchanged (fingerprint match)"
        else:
            info["verdict"] = "recompile (inputs changed since last compile)"
        return info, would_be if rules.mode != "manual" else (current or would_be)
    except Exception as exc:
        info["verdict"] = f"cannot compile yet: {' '.join(str(exc).split())}"
        return info, current


def _render_explanation(project: Project, timeline) -> dict[str, Any]:
    if timeline is None:
        return {"verdict": "no timeline to render"}
    try:
        from ..media.render import _read_key_sidecar, final_content_key

        ass = project.captions_dir / "captions.ass"
        renders: dict[str, Any] = {}
        for target in ("final", "proxy"):
            key = final_content_key(
                project, timeline, ass_file=ass if ass.exists() else None, target=target
            )
            if target == "final":
                newest = project.newest_final_path()  # numeric: v10 beats v9
                existing = _read_key_sidecar(newest) if newest else None
                latest = newest.name if newest else None
            else:
                proxy = project.proxy_dir / "proxy.mp4"
                existing = _read_key_sidecar(proxy) if proxy.exists() else None
                latest = proxy.name if proxy.exists() else None
            renders[target] = {
                "latest": latest,
                "content_key": short_hash(key, 12),
                "verdict": (
                    "skip (content key matches)" if existing == key
                    else "render (no previous key)" if existing is None
                    else "re-render (content key differs)"
                ),
            }
        renders["note"] = ("keys computed against the CURRENT captions.ass; a build "
                          "recompiles captions first, so a pending caption change "
                          "shows up as a key difference only after that step")
        return renders
    except Exception as exc:
        return {"verdict": f"cannot compute keys: {' '.join(str(exc).split())}"}


def explain(project: Project) -> dict[str, Any]:
    timeline_info, effective_timeline = _timeline_explanation(project)
    return {
        "shots": _shot_explanations(project),
        "timeline": timeline_info,
        "renders": _render_explanation(project, effective_timeline),
    }
