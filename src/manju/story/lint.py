"""Deterministic structural Story/Screen lint; artistic findings stay review evidence."""

from __future__ import annotations

from typing import Any

from ..core.authoring import SceneContractV2
from ..core.hashing import hash_value
from ..core.source_spans import load_source_spans
from .coverage import derive_coverage

KNOWN_DURATIONS = {"read", "fixed", "auto", "beat", "hold"}


def lint_story(project: Any, *, mode: str = "quick") -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    source_valid = True
    try:
        spans = {span.span_id for span in load_source_spans(project)}
    except Exception as exc:
        source_valid = False
        spans = set()
        errors.append({"code": "SOURCE_SPANS_INVALID", "message": str(exc)})
    thread_state: dict[str, str] = {}
    for scene_id in project.scene_contract_ids():
        try:
            scene = project.load_scene_contract(scene_id)
        except Exception as exc:
            errors.append({"code": "SCENE_INVALID", "scene": scene_id, "message": str(exc)})
            continue
        if isinstance(scene, SceneContractV2):
            refs: list[str] = []
            for item in (scene.change.choice, scene.change.cost):
                if item and item.source_span_ref:
                    refs.append(item.source_span_ref)
            for change in scene.change.state_changes:
                if change.source_span_ref:
                    refs.append(change.source_span_ref)
            for thread in scene.change.thread_changes:
                if thread.source_span_ref:
                    refs.append(thread.source_span_ref)
                prior = thread_state.get(thread.thread_id)
                if prior is not None and thread.operation == "create":
                    errors.append({"code": "THREAD_DUPLICATE", "thread": thread.thread_id})
                elif prior is None and thread.operation not in ("create", "open"):
                    errors.append({"code": "THREAD_ORDER", "thread": thread.thread_id,
                                   "operation": thread.operation, "scene": scene_id})
                thread_state[thread.thread_id] = thread.operation
            for beat in (scene.direction.experience.beats
                         if scene.direction and scene.direction.experience else []):
                refs.extend(beat.source_span_refs)
            for exception in scene.coverage_exceptions:
                if isinstance(exception, dict) and exception.get("source_span_ref"):
                    refs.append(str(exception["source_span_ref"]))
            for ref in refs:
                if ref.removeprefix("script:") not in spans:
                    errors.append({"code": "SOURCE_REF_UNRESOLVED", "scene": scene_id,
                                   "ref": ref})
    try:
        coverage = derive_coverage(project, mode=mode, persist=False) if source_valid else {
            "uncovered_source_spans": [], "uncovered_required_experience_beats": [],
            "digest": hash_value({"source_valid": False, "mode": mode}),
        }
    except Exception as exc:
        errors.append({"code": "COVERAGE_INVALID", "message": str(exc)})
        coverage = {
            "uncovered_source_spans": [], "uncovered_required_experience_beats": [],
            "digest": hash_value({"coverage_valid": False, "mode": mode}),
        }
    if coverage["uncovered_source_spans"]:
        warnings.append({"code": "SOURCE_UNCOVERED", "items": coverage["uncovered_source_spans"]})
    if mode in ("standard", "series") and coverage.get("uncovered_proof_scene_source_spans"):
        errors.append({"code": "PROOF_SOURCE_UNCOVERED",
                       "items": coverage["uncovered_proof_scene_source_spans"]})
    if coverage["uncovered_required_experience_beats"]:
        severity = "error" if mode in ("standard", "series") else "warning"
        (errors if severity == "error" else warnings).append({
            "code": "EXPERIENCE_UNCOVERED",
            "items": coverage["uncovered_required_experience_beats"],
        })
    return {"schema": "manju.story-lint/v1", "mode": mode,
            "ok": not errors, "errors": errors, "warnings": warnings,
            "coverage_digest": coverage["digest"]}


def lint_screen(project: Any, *, mode: str = "quick") -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    source_valid = True
    try:
        source_spans = {span.span_id for span in load_source_spans(project)}
    except Exception as exc:
        source_valid = False
        source_spans = set()
        errors.append({"code": "SOURCE_SPANS_INVALID", "message": str(exc)})
    known_beats: set[str] = set()
    motifs: set[str] = set()
    for scene_id in project.scene_contract_ids():
        scene = project.load_scene_contract(scene_id)
        direction = getattr(scene, "direction", None)
        experience = getattr(direction, "experience", None) if direction else None
        for beat in (experience.beats if experience else []):
            known_beats.add(beat.id)
        for change in getattr(scene, "change", None).state_changes if getattr(scene, "change", None) else []:
            if change.source_span_ref is not None and not change.source_span_ref.strip():
                errors.append({"code": "SOURCE_REF_EMPTY", "scene": scene_id})
    try:
        charter = project.load_creative()
    except Exception as exc:
        errors.append({"code": "CREATIVE_CHARTER_INVALID", "message": str(exc)})
        charter = None
    if charter:
        motifs = {str(row.get("id")) for row in charter.audiovisual.motifs if row.get("id")}
    first = True
    for shot_id in project.shot_ids(indexed_only=True):
        shot = project.load_shot(shot_id)
        unresolved = sorted(
            ref for ref in (shot.source_span_refs or [])
            if ref.removeprefix("script:") not in source_spans
        )
        for ref in unresolved:
            errors.append({"code": "SOURCE_REF_UNRESOLVED", "shot": shot_id, "ref": ref})
        screen = shot.contract.screen if shot.contract else None
        if screen is None:
            if mode in ("standard", "series"):
                warnings.append({"code": "SCREEN_ROLE_MISSING", "shot": shot_id})
            if not shot.source_span_refs:
                row = {"code": "SCREEN-UNANCHORED-SHOT", "shot": shot_id}
                (errors if mode in ("standard", "series") else warnings).append(row)
            first = False
            continue
        if screen.experience_beat_refs:
            missing = sorted(set(screen.experience_beat_refs) - known_beats)
            if missing:
                errors.append({"code": "EXPERIENCE_REF_UNKNOWN", "shot": shot_id, "items": missing})
        if not shot.source_span_refs and not screen.experience_beat_refs and not screen.justification.strip():
            row = {"code": "SCREEN-UNANCHORED-SHOT", "shot": shot_id}
            (errors if mode in ("standard", "series") else warnings).append(row)
        if screen.motif_ref and screen.motif_ref not in motifs:
            errors.append({"code": "MOTIF_UNKNOWN", "shot": shot_id, "motif": screen.motif_ref})
        if first and screen.relation_from_previous:
            errors.append({"code": "RELATION_FROM_PREVIOUS_FIRST_SHOT", "shot": shot_id})
        first = False
        if screen.duration:
            duration_mode = screen.duration.get("mode", "")
            if duration_mode not in KNOWN_DURATIONS:
                warnings.append({"code": "DURATION_MODE_UNKNOWN", "shot": shot_id})
            if duration_mode == "read" and not screen.duration.get("reason", "").strip():
                errors.append({"code": "DURATION_REASON_MISSING", "shot": shot_id})
        if any(not str(value).strip() for value in screen.attention.values()):
            errors.append({"code": "ATTENTION_EMPTY", "shot": shot_id})
    try:
        coverage = derive_coverage(project, mode=mode, persist=False) if source_valid else {
            "uncovered_required_experience_beats": [],
            "digest": hash_value({"source_valid": False, "mode": mode}),
        }
    except Exception as exc:
        errors.append({"code": "COVERAGE_INVALID", "message": str(exc)})
        coverage = {
            "uncovered_required_experience_beats": [],
            "digest": hash_value({"coverage_valid": False, "mode": mode}),
        }
    if coverage["uncovered_required_experience_beats"]:
        row = {"code": "REQUIRED_EXPERIENCE_UNCOVERED",
               "items": coverage["uncovered_required_experience_beats"]}
        (errors if mode in ("standard", "series") else warnings).append(row)
    return {"schema": "manju.screen-lint/v1", "mode": mode,
            "ok": not errors, "errors": errors, "warnings": warnings,
            "coverage_digest": coverage["digest"]}


__all__ = ["lint_story", "lint_screen"]
