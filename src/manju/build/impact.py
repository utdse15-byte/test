"""`manju impact` — interconnection spine (WP1).

Answers: "if I change this line of dialogue (or any shot field), what
happens?" One pure report covering video/voice staleness, caption cues,
timeline recompile, final re-render, export deliverables that would flip,
and catch-up cost. Never mutates, never spends.

Two modes:
- *Hypothetical* (``field`` + ``new_value``): deep-copy the shot, apply the
  edit, recompute hashes, compare against takes. Nothing is written.
- *Current* (no field): report what is already stale and what it touches.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..core.container import Project, ProjectError
from ..core.hashing import MANUAL_HASH
from ..core.models import CaptionLine, ShotSpec
from ..core.spec import (
    SPEC_VERSION,
    VOICE_VERSION,
    compute_spec_hash,
    compute_voice_hash,
    diff_spec_fields,
    spec_payload,
)
from .stale import ShotState, evaluate_shot
from .voice import VoiceState, evaluate_voice


# Deliverables that flip when timeline recompiles / final re-renders.
# Deterministic mapping — do NOT recompute exportstatus on hypothetical state.
_EXPORTS_ON_TIMELINE = ("srt", "ass", "otio", "jianying", "capcut")
_EXPORTS_ON_FINAL = ("final", "proxy", "cover", "teaser")

_VOICE_COST_NOTE = "voice per_call only — speech duration unknown"


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    """Mutate ``data`` so the dotted path equals ``value`` (creates intermediate
    dicts when needed for write-through on model_dump trees)."""
    parts = dotted.split(".")
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict):
            raise KeyError(f"path '{dotted}' hits non-dict at '{part}'")
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    if not isinstance(node, dict):
        raise KeyError(f"path '{dotted}' hits non-dict before leaf")
    # Coerce common leaf types from string CLI values when the existing
    # value has a typed shape (int/float/bool). Leave strings alone.
    leaf = parts[-1]
    if leaf in node and value is not None and not isinstance(value, type(node[leaf])):
        cur = node[leaf]
        if isinstance(cur, bool) and isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(cur, int) and not isinstance(cur, bool) and isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                pass
        elif isinstance(cur, float) and isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                pass
    node[leaf] = value


def apply_shot_field(shot: ShotSpec, field: str, new_value: Any) -> ShotSpec:
    """Return a deep-copied shot with ``field`` set to ``new_value``.

    Reuses the model_dump → mutate → model_validate path so nested pydantic
    models stay consistent. Raises ``KeyError`` / ``ValidationError`` on bad
    paths or values — callers surface them as one-line findings.
    """
    data = shot.model_dump()
    _set_dotted(data, field, new_value)
    return ShotSpec.model_validate(data)


def _video_impact(
    project: Project,
    shot: ShotSpec,
    *,
    hypo: ShotSpec | None,
    bible: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Current video state + would-become under a hypothetical shot (or
    current when hypo is None)."""
    current = evaluate_shot(project, shot, bible)
    out: dict[str, Any] = {
        "state": current.state.value,
        "would_become": current.state.value,
        "changed_fields": [],
        "selected_take": current.selected_take,
    }
    if hypo is None:
        if current.state == ShotState.STALE and current.take and current.take.sidecar.spec_snapshot:
            payload = spec_payload(
                shot, bible,
                version=current.take.sidecar.spec_version or 1,
                project_root=project.root,
            )
            out["changed_fields"] = diff_spec_fields(
                current.take.sidecar.spec_snapshot, payload
            )
        return out

    # Hypothetical: compare hypo hash against the take's recorded hash
    # (same §4.3 versioned-formula judgment as evaluate_shot).
    if current.state == ShotState.MANUAL:
        out["would_become"] = "manual"
        # still name fields that would move the current-version hash
        old_p = spec_payload(shot, bible, version=SPEC_VERSION, project_root=project.root)
        new_p = spec_payload(hypo, bible, version=SPEC_VERSION, project_root=project.root)
        out["changed_fields"] = diff_spec_fields(old_p, new_p)
        return out

    if current.state in (ShotState.MISSING, ShotState.NEEDS_SELECTION, ShotState.BROKEN):
        old_p = spec_payload(shot, bible, version=SPEC_VERSION, project_root=project.root)
        new_p = spec_payload(hypo, bible, version=SPEC_VERSION, project_root=project.root)
        out["changed_fields"] = diff_spec_fields(old_p, new_p)
        # still missing/etc. after the edit
        out["would_become"] = current.state.value
        return out

    take = current.take
    assert take is not None
    take_version = take.sidecar.spec_version or 1
    old_hash = compute_spec_hash(
        shot, bible, version=take_version, project_root=project.root
    )
    new_hash = compute_spec_hash(
        hypo, bible, version=take_version, project_root=project.root
    )
    old_p = spec_payload(shot, bible, version=take_version, project_root=project.root)
    new_p = spec_payload(hypo, bible, version=take_version, project_root=project.root)
    out["changed_fields"] = diff_spec_fields(old_p, new_p)
    if take.sidecar.spec_hash == MANUAL_HASH:
        out["would_become"] = "manual"
    elif new_hash == take.sidecar.spec_hash or (
        take.sidecar.spec_hash == old_hash and new_hash == old_hash
    ):
        # hash still matches the take → stays fresh (or stays stale if it
        # was already stale for other reasons — re-evaluate hypo)
        hypo_status = evaluate_shot(project, hypo, bible)
        # evaluate_shot uses on-disk shot for nothing about hash — it uses
        # the shot object we pass, so this is correct for hypo.
        out["would_become"] = hypo_status.state.value
    else:
        out["would_become"] = "stale"
    # Refine: if take matched old and new differs → stale; if take already
    # matched neither, still stale; if new matches take → fresh.
    if take.sidecar.spec_hash == new_hash:
        out["would_become"] = "fresh"
    elif take.sidecar.spec_hash != MANUAL_HASH:
        out["would_become"] = "stale"
    return out


def _voice_impact(
    project: Project,
    shot: ShotSpec,
    *,
    hypo: ShotSpec | None,
    bible: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    current = evaluate_voice(project, shot, bible)
    voices = project.voice_takes(shot.id)
    newest_name = voices[-1][0].stem if voices else None
    out: dict[str, Any] = {
        "state": current.state.value,
        "would_become": current.state.value,
        "newest_take": newest_name,
        "manual": current.state == VoiceState.MANUAL,
    }
    if current.state == VoiceState.NOT_NEEDED and (
        hypo is None or not hypo.dialogue.text
    ):
        return out
    if hypo is None:
        return out

    if current.state == VoiceState.MANUAL:
        out["would_become"] = "manual"
        out["manual"] = True
        return out

    if current.state == VoiceState.NOT_NEEDED and hypo.dialogue.text:
        out["would_become"] = "missing"
        return out
    if current.state == VoiceState.MISSING:
        # still missing after edit (no takes appear magically)
        out["would_become"] = (
            "not_needed" if not hypo.dialogue.text else "missing"
        )
        return out

    # FRESH or STALE with a sidecar: recompute voice_hash under take version
    media, sidecar = voices[-1]
    if sidecar is None:
        out["would_become"] = "manual"
        out["manual"] = True
        return out
    from .voice import _current_voice_descriptor

    descriptor = _current_voice_descriptor()
    take_version = sidecar.voice_hash_version or 1
    take_provider = descriptor if take_version >= 2 else None
    new_hash = compute_voice_hash(
        hypo, bible, version=take_version, provider=take_provider
    )
    if not hypo.dialogue.text:
        out["would_become"] = "not_needed"
    elif sidecar.voice_hash == new_hash:
        out["would_become"] = "fresh"
    else:
        out["would_become"] = "stale"
    return out


def _caption_impact(
    project: Project,
    shot_id: str,
    *,
    hypo_text: str | None = None,
) -> dict[str, Any]:
    """List cues attributed to this shot (stamped or temporal fallback).

    In hypothetical mode the cue *list* is still the current one — impact
    tells the user which existing cues are about this shot (and would need
    recompile/retime). ``manual_note`` matches voicefix's advisory tone.
    """
    from ..timeline.cuemap import cues_for_shot

    rules = project.load_rules()
    timeline = project.load_timeline()
    manual = rules.captions.mode == "manual"
    mode = "manual" if manual else "compiled"
    manual_note = None

    cue_list: list[CaptionLine] | list[dict[str, Any]] | None = None
    if manual:
        srt = project.captions_dir / "captions.srt"
        if srt.exists():
            from ..providers.asr import parse_srt

            try:
                segs = parse_srt(srt.read_text(encoding="utf-8"))
                cue_list = [
                    {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
                    for s in segs
                ]
            except OSError:
                cue_list = []
        manual_note = (
            "人工接管字幕不会自动移动;修改台词后需人工校准这些字幕的时间轴"
            "(引擎不会替你移动人工字幕)。"
        )

    pairs = cues_for_shot(timeline, shot_id, cues=cue_list)
    cues_out = [
        {
            "index": i,  # 0-based into the caption track / SRT
            "start_ms": c.start_ms,
            "end_ms": c.end_ms,
            "text": c.text,
        }
        for i, c in pairs
    ]
    # Hypothetical dialogue change with no timeline yet: still surface that
    # captions will be (re)derived from the new text on next compile.
    if not cues_out and hypo_text is not None and str(hypo_text).strip():
        # no existing cues to list; report empty + compiled mode is fine
        pass
    return {
        "cues": cues_out,
        "mode": mode,
        "manual_note": manual_note,
    }


def _timeline_renders_verdicts(project: Project) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reuse explain helpers so impact and explain never disagree."""
    from .explain import _render_explanation, _timeline_explanation

    tl_info, effective = _timeline_explanation(project)
    renders = _render_explanation(project, effective)
    return tl_info, renders


def _exports_stale_after(timeline_verdict: str, render_verdicts: dict[str, Any]) -> list[str]:
    """Deterministic export flip mapping from timeline/render verdicts."""
    stale: list[str] = []
    tl_moves = (
        "recompile" in timeline_verdict
        or "compile (no timeline" in timeline_verdict
        or timeline_verdict.startswith("compile")
    )
    # "unchanged" / "manual mode" → no timeline-driven export flip from a
    # pure advisory; but a dialogue edit that changes voice duration WILL
    # recompile — impact always treats a voice/spec-moving edit as timeline
    # recompile when would-become voice/video is stale/missing.
    if tl_moves:
        stale.extend(_EXPORTS_ON_TIMELINE)
    final_v = ""
    if isinstance(render_verdicts, dict):
        final_info = render_verdicts.get("final") or {}
        if isinstance(final_info, dict):
            final_v = str(final_info.get("verdict") or "")
        elif "verdict" in render_verdicts:
            final_v = str(render_verdicts.get("verdict") or "")
    if "re-render" in final_v or "render (no previous" in final_v or tl_moves:
        for name in _EXPORTS_ON_FINAL:
            if name not in stale:
                stale.append(name)
    return stale


def _estimate_costs(project: Project, shot: ShotSpec, *,
                    need_video: bool, need_voice: bool) -> dict[str, Any]:
    from .graph import _estimate_shot_cost, _routed_shot_for_pricing, _target_duration_ms

    currency = "CNY"
    regen_video = 0.0
    regen_voice = 0.0
    if need_video:
        rules = project.load_rules()
        try:
            dur_ms = _target_duration_ms(project, shot, rules)
        except Exception:
            dur_ms = int(getattr(rules.timing, "default_shot_ms", 3000) or 3000)
        try:
            priced = _routed_shot_for_pricing(project, shot)
        except Exception:
            priced = shot
        regen_video, cur = _estimate_shot_cost(priced, dur_ms)
        if cur:
            currency = cur
    if need_voice:
        try:
            from ..providers.tts import tts_providers

            providers = tts_providers()
            if providers:
                mid = sorted(providers)[0]
                m = providers[mid]
                regen_voice = float(m.cost.per_call or 0.0)
                if m.cost.currency:
                    currency = m.cost.currency
        except Exception:
            pass
    return {
        "regen_video": round(float(regen_video), 4),
        "regen_voice": round(float(regen_voice), 4),
        "currency": currency,
        "note": _VOICE_COST_NOTE if need_voice else None,
    }


def impact_report(
    project: Project,
    shot_id: str,
    field: str | None = None,
    new_value: str | None = None,
) -> dict[str, Any]:
    """Build the full impact report for ``shot_id``.

    All keys always present so ``--json`` consumers can rely on the shape.
    """
    try:
        shot = project.load_shot(shot_id)
    except ProjectError as exc:
        raise ProjectError(str(exc)) from exc

    bible = project.load_bible()
    hypo: ShotSpec | None = None
    if field is not None:
        if new_value is None:
            raise ValueError(
                f"impact: --field 需要 --value (field={field!r} 没有给出新值)"
            )
        hypo = apply_shot_field(shot, field, new_value)

    video = _video_impact(project, shot, hypo=hypo, bible=bible)
    voice = _voice_impact(project, shot, hypo=hypo, bible=bible)
    hypo_text = hypo.dialogue.text if hypo is not None else None
    captions = _caption_impact(project, shot_id, hypo_text=hypo_text)

    # Timeline / renders: for hypothetical edits that would stale voice or
    # video, the next build recompiles and re-renders even if the on-disk
    # fingerprint still matches today.
    tl_info, renders = _timeline_renders_verdicts(project)
    timeline_verdict = str(tl_info.get("verdict") or "")
    would_move = (
        video.get("would_become") in ("stale", "missing")
        or voice.get("would_become") in ("stale", "missing")
        or (field is not None and field.startswith("dialogue."))
    )
    if would_move and field is not None:
        # Hypothetical: inputs WILL change once the edit is saved.
        timeline_out = {
            "verdict": "recompile (inputs changed)",
            "mode": tl_info.get("mode"),
            "captions_mode": tl_info.get("captions_mode"),
        }
        renders_out = {
            "final": "re-render (content key differs)",
            "proxy": "re-render (content key differs)",
        }
        # If final already missing, say render not re-render
        final_info = renders.get("final") if isinstance(renders, dict) else None
        if isinstance(final_info, dict) and final_info.get("latest") is None:
            renders_out["final"] = "render (no previous key)"
            renders_out["proxy"] = "render (no previous key)"
        export_stale = list(_EXPORTS_ON_TIMELINE) + [
            x for x in _EXPORTS_ON_FINAL if x not in _EXPORTS_ON_TIMELINE
        ]
    else:
        timeline_out = {
            "verdict": timeline_verdict,
            "mode": tl_info.get("mode"),
            "captions_mode": tl_info.get("captions_mode"),
        }
        if isinstance(renders, dict) and "final" in renders:
            renders_out = {
                "final": (renders.get("final") or {}).get("verdict", ""),
                "proxy": (renders.get("proxy") or {}).get("verdict", ""),
            }
        else:
            renders_out = {
                "final": str(renders.get("verdict", "")) if isinstance(renders, dict) else "",
                "proxy": "",
            }
        export_stale = _exports_stale_after(timeline_verdict, renders)

    need_video = video.get("would_become") in ("stale", "missing")
    need_voice = voice.get("would_become") in ("stale", "missing")
    # Cost for catch-up: regen only when would_become is stale/missing
    # (manual never auto-regens; fresh needs nothing).
    cost_shot = hypo if hypo is not None else shot
    cost = _estimate_costs(
        project, cost_shot, need_video=need_video, need_voice=need_voice
    )

    # WP4: which locale lines would flip to 翻译过期 if base dialogue moves
    locales_section: dict[str, Any] = {"affected": []}
    if field is not None and str(field).startswith("dialogue."):
        try:
            from ..core.locale import line_status, list_locales

            for lg in list_locales(project):
                st = line_status(project, lg, shot_id)
                # After base dialogue change, any non-missing line becomes 翻译过期
                if st.get("state") in ("ok", "翻译过期") and st.get("text"):
                    locales_section["affected"].append({
                        "lang": lg, "would_become": "翻译过期",
                        "text": st.get("text"),
                    })
                elif st.get("state") == "missing":
                    locales_section["affected"].append({
                        "lang": lg, "would_become": "missing",
                    })
        except Exception:
            pass

    return {
        "shot": shot_id,
        "field": field,
        "video": video,
        "voice": voice,
        "captions": captions,
        "timeline": timeline_out,
        "renders": renders_out,
        "exports": {"stale_after": export_stale},
        "cost": cost,
        "locales": locales_section,
    }


def impact_summary_zh(report: dict[str, Any]) -> str:
    """One-line 中文 strip for the GUI: 「此修改将影响:…」."""
    parts: list[str] = []
    voice = report.get("voice") or {}
    if voice.get("would_become") == "stale":
        parts.append("配音 1 条将过期")
    elif voice.get("would_become") == "missing":
        parts.append("配音将需合成")
    elif voice.get("manual"):
        parts.append("配音为人工(不过期)")

    cues = (report.get("captions") or {}).get("cues") or []
    if cues:
        parts.append(f"字幕 {len(cues)} 条")
    elif (report.get("captions") or {}).get("mode") == "compiled":
        if report.get("field") and str(report.get("field")).startswith("dialogue."):
            parts.append("字幕将重编")

    tl = (report.get("timeline") or {}).get("verdict") or ""
    if "recompile" in tl or "compile" in tl:
        parts.append("时间线将重编")

    final_v = (report.get("renders") or {}).get("final") or ""
    if "re-render" in final_v or final_v.startswith("render"):
        parts.append("成片将重渲")

    cost = report.get("cost") or {}
    total = float(cost.get("regen_video") or 0) + float(cost.get("regen_voice") or 0)
    cur = cost.get("currency") or "CNY"
    if total > 0 or (cost.get("regen_video") is not None):
        parts.append(f"追赶成本 ≈{total:.2f} {cur}")

    if not parts:
        return "此修改无明显连锁影响"
    return "此修改将影响:" + " · ".join(parts)
