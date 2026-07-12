"""Locale-aware build helpers (WP4 completion).

Picture pipeline (video takes, segment cache) is always shared. Only voice
audio + captions + final content-key inputs differ per language.

Duration policy (guide §WP4 non-goals): video clip durations come from the
BASE timeline (or a base compile). Locale voice is swapped onto those
slots; a longer locale voice surfaces via existing sync-hint machinery,
not by restaging video.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.locale import load_lines, overlay_shot_for_voice
from ..core.models import CaptionLine, Timeline
from ..core.yamlio import atomic_write_text


def locale_captions_dir(project: Project, lang: str) -> Path:
    d = project.captions_dir / "locales" / lang
    d.mkdir(parents=True, exist_ok=True)
    return d


def locale_final_dir(project: Project, lang: str) -> Path:
    d = project.final_dir / "locales" / lang
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan_locale_voice(project: Project, lang: str, *, gen: str = "missing") -> list[dict[str, Any]]:
    """Voice plan for a locale: shots with overlay text but no locale take."""
    if gen == "off":
        return []
    try:
        from ..providers.tts import tts_providers

        providers = tts_providers()
        if not providers:
            return []
        provider_id = sorted(providers)[0]
        manifest = providers[provider_id]
    except Exception:
        return []
    lines = load_lines(project, lang)
    plan: list[dict[str, Any]] = []
    for sid in project.shot_ids():
        entry = lines.get(sid) or {}
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        if project.voice_takes(sid, lang=lang):
            continue  # has a locale take
        plan.append({
            "shot": sid,
            "kind": "voice",
            "reason": "missing",
            "provider": provider_id,
            "estimated_cost": float(manifest.cost.per_call or 0),
            "currency": manifest.cost.currency,
            "lang": lang,
        })
    return plan


def synthesize_locale_voices(
    project: Project,
    plan: list[dict[str, Any]],
    *,
    lang: str,
    actor: str = "engine",
) -> list[str]:
    """Run TTS for locale plan rows; register under locales/<lang>/."""
    from ..core.events import append_event
    from ..providers.tts import get_tts_provider

    generated: list[str] = []
    bible = project.load_bible()
    for item in plan:
        sid = item["shot"]
        shot = overlay_shot_for_voice(project, project.load_shot(sid), lang)
        if not shot.dialogue.text:
            continue
        tts = get_tts_provider(item.get("provider"))
        orig = project.register_voice_take

        def _reg(shot_id, media_file, sidecar, **kw):
            kw.setdefault("lang", lang)
            return orig(shot_id, media_file, sidecar, **kw)

        project.register_voice_take = _reg  # type: ignore[method-assign]
        try:
            media = tts.synthesize(project, shot, bible)
        finally:
            project.register_voice_take = orig  # type: ignore[method-assign]
        generated.append(f"{sid}/locales/{lang}/{media.stem}")
        append_event(project.root, actor, "voice", {
            "shot": sid, "take": media.stem, "lang": lang,
            "provider": getattr(tts, "id", item.get("provider")),
        })
    return generated


def apply_locale_overlay(
    project: Project,
    timeline: Timeline,
    lang: str,
) -> Timeline:
    """Return a NEW timeline: base picture windows kept; voice sources swapped
    to locale takes when present; caption text rewritten from locale lines
    (timing windows preserved from base cues for that shot).
    """
    from ..timeline.compiler import _find_voice
    from ..timeline.cuemap import cues_for_shot

    data = timeline.model_dump()
    # Swap voice clips
    for clip in (data.get("tracks") or {}).get("voice") or []:
        shot = clip.get("shot") or ""
        # Voice clips may only have source; recover shot from path if needed
        if not shot and clip.get("source"):
            # media/gen/S001/voice_take_01.mp3 → S001
            parts = str(clip["source"]).replace("\\", "/").split("/")
            if "gen" in parts:
                i = parts.index("gen")
                if i + 1 < len(parts):
                    shot = parts[i + 1]
                    clip["shot"] = shot
        if not shot:
            continue
        voices = project.voice_takes(shot, lang=lang)
        if voices:
            clip["source"] = project.relpath(voices[-1][0])

    # Rebuild captions: keep base cue windows per shot, replace text from locale
    lines = load_lines(project, lang)
    base_tl = timeline
    new_caps: list[dict[str, Any]] = []
    # Group existing cues by shot
    for sid in project.shot_ids():
        pairs = cues_for_shot(base_tl, sid)
        entry = lines.get(sid) or {}
        loc_text = str(entry.get("text") or "").strip()
        if not pairs:
            continue
        if not loc_text:
            # keep original cue texts for this shot
            for i, c in pairs:
                new_caps.append({
                    "start_ms": c.start_ms, "end_ms": c.end_ms,
                    "text": c.text, "speaker": c.speaker, "shot": sid,
                })
            continue
        # One-shot: put full locale text into the first cue window spanning all
        # of this shot's cues (merged span) if multiple cues
        start = pairs[0][1].start_ms
        end = pairs[-1][1].end_ms
        speaker = pairs[0][1].speaker
        # Prefer locale voice timing if present
        voices = project.voice_takes(sid, lang=lang)
        if voices:
            import json
            timing_path = voices[-1][0].with_suffix(".timing.json")
            if timing_path.exists():
                try:
                    words = json.loads(timing_path.read_text(encoding="utf-8"))
                    if isinstance(words, list) and words:
                        # place relative to shot caption start
                        for w in words:
                            new_caps.append({
                                "start_ms": start + int(w.get("start_ms", 0)),
                                "end_ms": start + int(w.get("end_ms", 0) or 1),
                                "text": str(w.get("text") or ""),
                                "speaker": speaker,
                                "shot": sid,
                            })
                        continue
                except (json.JSONDecodeError, OSError):
                    pass
        new_caps.append({
            "start_ms": start, "end_ms": max(end, start + 1),
            "text": loc_text, "speaker": speaker, "shot": sid,
        })
    # Keep non-shot captions (none typically)
    if new_caps:
        data["tracks"]["captions"] = new_caps
    return Timeline.model_validate(data)


def export_locale_captions(
    project: Project,
    timeline: Timeline,
    lang: str,
) -> dict[str, Path]:
    """Write captions/locales/<lang>/captions.srt|.ass|.ttml from the (locale)
    timeline.

    The TTML sibling is the ONE caption exit that carries a declared language:
    ``lang`` (the validated locale id) lands on ``xml:lang`` — the base
    non-locale export honestly records none (``exporters/ttml.py``). An
    OPTIONAL ``locales/<lang>/meta.yaml`` may declare a layout ``direction``
    (``rtl``/``ltr``, human-declared, never inferred); absent → ``None`` →
    the writer emits a byte-identical bare ``<div>``. Same determinism +
    line-budget discipline as the srt/ass siblings.
    """
    from ..core.locale import load_locale_meta
    from ..exporters.srt_ass import compile_ass, compile_srt, _caption_style
    from ..exporters.ttml import compile_ttml

    style = _caption_style(project)
    d = locale_captions_dir(project, lang)
    srt_path = d / "captions.srt"
    ass_path = d / "captions.ass"
    ttml_path = d / "captions.ttml"
    max_chars = style.get("max_chars_per_line")
    atomic_write_text(srt_path, compile_srt(timeline, max_chars_per_line=max_chars))
    atomic_write_text(
        ass_path,
        compile_ass(timeline, width=timeline.width, height=timeline.height, style=style),
    )
    direction = load_locale_meta(project, lang).get("direction")
    atomic_write_text(
        ttml_path,
        compile_ttml(
            timeline, lang=lang, max_chars_per_line=max_chars, direction=direction,
        ),
    )
    # Hash-based freshness for locale status (not mere presence)
    try:
        from ..core.locale import write_locale_captions_key
        write_locale_captions_key(project, lang)
    except Exception:
        pass
    return {"srt": srt_path, "ass": ass_path, "ttml": ttml_path}


def next_locale_final_path(project: Project, lang: str) -> Path:
    d = locale_final_dir(project, lang)
    import re
    nums = [
        int(m.group(1))
        for p in d.glob("final_v*.mp4")
        if (m := re.match(r"final_v(\d+)$", p.stem))
    ]
    return d / f"final_v{max(nums, default=0) + 1}.mp4"


def newest_locale_final(project: Project, lang: str) -> Path | None:
    d = locale_final_dir(project, lang)
    import re
    versions = [
        (int(m.group(1)), p)
        for p in d.glob("final_v*.mp4")
        if (m := re.match(r"final_v(\d+)$", p.stem))
    ]
    return max(versions, key=lambda t: t[0])[1] if versions else None
