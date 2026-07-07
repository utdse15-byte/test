"""`build/mixer.py` — the GUI mixer's read/write API (round-T).

A GUI audio mixer must be able to finish a normal video's sound WITHOUT opening
JianYing: voice level, BGM choice/trim/fades, SFX, the ambient bed, and each
shot's own footage audio. It should never parse rules.yaml or the shot files
itself — it reads the whole audio picture with :func:`read_mixer` and commits an
edit with :func:`apply_mixer`, which

  * validates every change THROUGH the models (a bad value is rejected, nothing
    is written),
  * writes rules.yaml / the touched shot files via the project's existing atomic
    save paths (``save_rules`` / ``update_shot_raw``),
  * records exactly ONE ``mixer`` event (detail = the changed keys), and
  * returns an explain-style verdict of what the next build will re-do — which
    segments re-normalize, whether the timeline recompiles and the final
    re-renders — so the GUI can show "rebuild will touch N shots" before running.

Purely engine-side: NO GUI code lives here.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..core.container import Project, ProjectError
from ..core.events import append_event
from ..core.models import (
    AmbientRules,
    AudioMixRules,
    MusicRules,
    SfxClipSpec,
    SourceAudio,
)


class MixerError(ValueError):
    """A mixer change failed model validation (bad level/flag/anchor). Carries a
    human-readable reason; nothing was written when this is raised."""


# ------------------------------------------------------------------ read side


def _duck_dict(bed: MusicRules | AmbientRules) -> dict[str, Any]:
    return {
        "threshold": bed.duck_threshold,
        "ratio": bed.duck_ratio,
        "attack_ms": bed.duck_attack_ms,
        "release_ms": bed.duck_release_ms,
    }


def _bed_dict(bed: MusicRules | AmbientRules) -> dict[str, Any]:
    """The GUI's view of a music/ambient bed: source, level, ducking, in-point
    and fades. Music and ambient share this shape (both are beds)."""
    return {
        "source": bed.source,
        "gain_db": bed.gain_db,
        "ducking": bed.ducking,
        "start_offset_ms": bed.start_offset_ms,
        "fade_in_ms": bed.fade_in_ms,
        "fade_out_ms": bed.fade_out_ms,
        "duck": _duck_dict(bed),
    }


def _shot_source_audio(project: Project) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sid in project.shot_ids():
        try:
            sa = project.load_shot(sid).source_audio
        except Exception:  # a broken shot file reads as its default (mixer is a view)
            sa = SourceAudio()
        out.append({"shot": sid, "gain_db": sa.gain_db, "mute": sa.mute})
    return out


def read_mixer(project: Project) -> dict[str, Any]:
    """The whole audio picture the GUI mixer drives, in one dict.

    Symmetric with :func:`apply_mixer`: every sub-dict here is a shape
    ``apply_mixer`` accepts back (a read → edit → write round-trip)."""
    rules = project.load_rules()
    audio = rules.audio
    return {
        "voice_gain_db": audio.voice_gain_db,
        "music": _bed_dict(rules.music),
        "ambient": _bed_dict(audio.ambient),
        "sfx": [
            {"source": s.source, "at": s.at, "offset_ms": s.offset_ms, "gain_db": s.gain_db}
            for s in audio.sfx
        ],
        "transition": {"source": audio.transition_sound, "gain_db": audio.transition_gain_db},
        "shots": _shot_source_audio(project),
    }


# ----------------------------------------------------------------- write side

# friendly mixer key -> model field, for a bed (music/ambient)
_BED_FIELDS = ("source", "gain_db", "ducking", "start_offset_ms", "fade_in_ms", "fade_out_ms")
_DUCK_FIELDS = {
    "threshold": "duck_threshold",
    "ratio": "duck_ratio",
    "attack_ms": "duck_attack_ms",
    "release_ms": "duck_release_ms",
}


def _merge_bed(dump: dict[str, Any], change: dict[str, Any]) -> None:
    """Fold a mixer bed-change dict into a MusicRules/AmbientRules model dump
    (in place). Unknown keys are ignored here; the model validation that follows
    is the real gate."""
    for k in _BED_FIELDS:
        if k in change:
            dump[k] = change[k]
    duck = change.get("duck")
    if isinstance(duck, dict):
        for fk, mk in _DUCK_FIELDS.items():
            if fk in duck:
                dump[mk] = duck[fk]


def apply_mixer(project: Project, changes: dict[str, Any], *, actor: str = "human") -> dict[str, Any]:
    """Validate, write, log ONE event, and report the rebuild fallout.

    ``changes`` is any subset of the :func:`read_mixer` shape: ``voice_gain_db``,
    ``music``/``ambient`` (bed dicts, incl. a ``duck`` sub-dict), ``sfx`` (the
    full replacement list), ``transition`` (``{source, gain_db}``) and ``shots``
    (a list of ``{shot, gain_db?, mute?}`` per-shot footage-audio edits).

    All validation happens BEFORE any write: an invalid value (or an unknown
    shot id) raises :class:`MixerError` and the project is left untouched.
    """
    changes = changes or {}
    rules = project.load_rules()
    music_dump = rules.music.model_dump()
    audio_dump = rules.audio.model_dump()  # holds ambient + sfx + voice_gain + transition
    rule_changes: list[str] = []

    if "voice_gain_db" in changes:
        audio_dump["voice_gain_db"] = changes["voice_gain_db"]
        rule_changes.append("voice_gain_db")
    if "music" in changes:
        _merge_bed(music_dump, changes["music"] or {})
        rule_changes.append("music")
    if "ambient" in changes:
        _merge_bed(audio_dump["ambient"], changes["ambient"] or {})
        rule_changes.append("ambient")
    if "sfx" in changes:
        audio_dump["sfx"] = list(changes["sfx"] or [])
        rule_changes.append("sfx")
    if "transition" in changes:
        t = changes["transition"] or {}
        if "source" in t:
            audio_dump["transition_sound"] = t["source"]
        if "gain_db" in t:
            audio_dump["transition_gain_db"] = t["gain_db"]
        rule_changes.append("transition")

    # Validate the rules side by (re)constructing the models — the single gate.
    try:
        new_music = MusicRules.model_validate(music_dump)
        # sfx entries validated individually so the error names the bad clip
        audio_dump["sfx"] = [SfxClipSpec.model_validate(s).model_dump() for s in audio_dump["sfx"]]
        new_audio = AudioMixRules.model_validate(audio_dump)
    except ValidationError as exc:
        raise MixerError(f"invalid mixer change: {_one_line(exc)}") from exc

    # Validate the per-shot side and confirm each shot exists — still no writes.
    shot_updates: dict[str, dict[str, Any]] = {}
    for sc in changes.get("shots", []) or []:
        if not isinstance(sc, dict) or "shot" not in sc:
            raise MixerError("each shots[] entry needs a 'shot' id")
        sid = sc["shot"]
        try:
            sa = SourceAudio.model_validate({k: sc[k] for k in ("gain_db", "mute") if k in sc})
        except ValidationError as exc:
            raise MixerError(f"invalid source_audio for {sid}: {_one_line(exc)}") from exc
        try:
            project.load_shot_raw(sid)  # confirm the shot exists — still no writes
        except ProjectError as exc:
            raise MixerError(f"no such shot to mix: {sid}") from exc
        shot_updates[sid] = sa.model_dump()

    # ---- validation passed: commit atomically through the existing save paths.
    if rule_changes:
        rules.music = new_music
        rules.audio = new_audio
        project.save_rules(rules)
    for sid, sa_dump in shot_updates.items():
        project.update_shot_raw(sid, lambda d, v=sa_dump: d.__setitem__("source_audio", v))

    changed = list(rule_changes) + (["shots"] if shot_updates else [])
    # ONE event for the whole mix edit (§3, §10 handover surface).
    append_event(project.root, actor, "mixer",
                 {"changed": changed, "shots": sorted(shot_updates)})

    return {
        "changed": changed,
        "shots": sorted(shot_updates),
        "rebuild": _rebuild_verdicts(rule_changes, list(shot_updates)),
    }


def _one_line(exc: ValidationError) -> str:
    return " ".join(str(exc).split())


def _rebuild_verdicts(rule_changes: list[str], changed_shots: list[str]) -> dict[str, Any]:
    """Explain-style ``manju build`` fallout for this mix edit (build/explain.py
    verdict shape). The architectural truth the GUI needs: a MIX change
    (voice/music/sfx/ambient/transition) re-renders the final but touches NO
    cached segment — the audio graph lives in the final pass. Only a per-shot
    footage-audio change re-normalizes a segment (exactly the shots listed)."""
    touched = bool(rule_changes) or bool(changed_shots)
    return {
        "timeline": "recompile (mixer inputs changed)" if touched else "unchanged",
        "final": "re-render" if touched else "unchanged",
        # segments are content-addressed: only these shots' segments re-encode.
        "segments_restale": sorted(changed_shots),
    }
