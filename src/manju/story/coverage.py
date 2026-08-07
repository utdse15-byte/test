"""Source and Screen Experience Coverage, derived from authored contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.authoring import SceneContractV2
from ..core.hashing import hash_value
from ..core.source_spans import load_source_spans
from ..core.yamlio import dump_yaml, atomic_write_text

COVERAGE_SCHEMA = "manju.story-coverage/v1"


def _span_id(ref: str) -> str:
    return str(ref).removeprefix("script:")


def _experience_beats(project: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for scene_id in project.scene_contract_ids():
        scene = project.load_scene_contract(scene_id)
        direction = getattr(scene, "direction", None)
        experience = getattr(direction, "experience", None) if direction else None
        for beat in (experience.beats if experience else []):
            out[beat.id] = beat
    return out


def derive_coverage(project: Any, *, mode: str = "quick", persist: bool = True) -> dict[str, Any]:
    spans = {span.span_id: span for span in load_source_spans(project)}
    experience_beats = _experience_beats(project)
    exceptions: dict[str, dict[str, Any]] = {}
    for scene_id in project.scene_contract_ids():
        scene = project.load_scene_contract(scene_id)
        for item in getattr(scene, "coverage_exceptions", []):
            if not isinstance(item, dict):
                continue
            ref = str(item.get("source_span_ref") or "").removeprefix("script:")
            if ref:
                exceptions[ref] = {
                    "disposition": str(item.get("disposition") or "excepted"),
                    "reason": str(item.get("reason") or ""),
                    "scene": scene_id,
                }
    source: dict[str, dict[str, Any]] = {
        span_id: {"status": "uncovered", "shots": [], "channels": []}
        for span_id in spans
    }
    experience = {
        beat_id: {"status": "uncovered", "shots": []}
        for beat_id in experience_beats
    }
    unanchored: list[str] = []
    for shot_id in project.shot_ids(indexed_only=True):
        shot = project.load_shot(shot_id)
        refs = list(shot.source_span_refs or [])
        screen = shot.contract.screen if shot.contract else None
        beat_refs = list(screen.experience_beat_refs if screen else [])
        anchored = bool(refs or beat_refs or (screen and screen.role and screen.justification.strip()))
        if not anchored:
            unanchored.append(shot_id)
        for ref in refs:
            sid = _span_id(ref)
            row = source.setdefault(sid, {"status": "unresolved", "shots": [], "channels": []})
            if shot_id not in row["shots"]:
                row["shots"].append(shot_id)
            row["status"] = "covered" if sid in spans else "unresolved"
            channels = row["channels"]
            if shot.dialogue.text and "dialogue" not in channels:
                channels.append("dialogue")
            if shot.contract and shot.contract.sound.cue and "sound" not in channels:
                channels.append("sound")
            if screen and screen.primary_carrier and screen.primary_carrier not in channels:
                channels.append(screen.primary_carrier)
        for beat_id in beat_refs:
            row = experience.setdefault(beat_id, {"status": "unresolved", "shots": []})
            if shot_id not in row["shots"]:
                row["shots"].append(shot_id)
            row["status"] = "covered" if beat_id in experience_beats else "unresolved"

    for span_id, exception in exceptions.items():
        if span_id in source and source[span_id]["status"] != "covered":
            source[span_id]["status"] = "excepted"
            source[span_id]["exception"] = exception

    uncovered_source = [
        sid for sid, row in source.items()
        if row["status"] not in ("covered", "excepted")
    ]
    proof_scenes = {
        scene_id for scene_id in project.scene_contract_ids()
        if project.load_scene_contract(scene_id).proof_scene
    }
    uncovered_proof_source = [
        span_id for span_id in uncovered_source
        if span_id in spans and spans[span_id].scene_id in proof_scenes
    ]
    uncovered_required = [
        bid for bid, row in experience.items()
        if bid in experience_beats
        and row["status"] != "covered"
        and experience_beats[bid].required
    ]
    result = {
        "schema": COVERAGE_SCHEMA,
        "mode": mode,
        "source_coverage": source,
        "screen_experience": experience,
        "unanchored_shots": sorted(unanchored),
        "uncovered_source_spans": sorted(uncovered_source),
        "uncovered_proof_scene_source_spans": sorted(uncovered_proof_source),
        "uncovered_required_experience_beats": sorted(uncovered_required),
    }
    result["digest"] = hash_value(result)
    if persist:
        target = project.reports_dir / "derived" / "story" / "coverage.json"
        atomic_write_text(target, dump_yaml(result))
    return result


def rebuild_coverage(project: Any, *, mode: str = "quick") -> dict[str, Any]:
    return derive_coverage(project, mode=mode, persist=True)


__all__ = ["COVERAGE_SCHEMA", "derive_coverage", "rebuild_coverage"]
