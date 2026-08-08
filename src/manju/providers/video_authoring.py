"""Derived, provider-neutral video authoring facts.

This module never writes project state and never executes a model.  It turns
the existing Shot/Bible/reference truth into one frozen plan that prompt
profiles can project into their own dialects.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from ..core.hashing import hash_file, hash_value
from ..core.intent import prompt_contract_sections
from .prompt import compile_prompt
from .refs import (
    RefItem,
    ReferenceControlConflict,
    normalize_subject_scope,
    physical_ref_key,
    resolve_local_ref,
    resolve_refs,
    validate_control_ownership,
)

PLAN_SCHEMA = "manju.video-authoring-plan/v1"


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),) if value != "" else ()


@dataclass(frozen=True)
class PhysicalReference:
    id: str
    kind: str
    source_identity: str
    sha256: str | None
    local_asset: str | None
    safe_remote_identity: str | None
    source_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source_identity": self.source_identity,
            "sha256": self.sha256,
            "local_asset": self.local_asset,
            "safe_remote_identity": self.safe_remote_identity,
        }


@dataclass(frozen=True)
class LogicalBinding:
    id: str
    index: int
    physical_id: str
    ref: str
    tier: str
    kind: str
    subject_scope: str | None
    controls: tuple[str, ...]
    ignore: tuple[str, ...]
    is_url: bool
    exists: bool
    blocked_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "physical_id": self.physical_id,
            "ref": self.ref,
            "tier": self.tier,
            "kind": self.kind,
            "subject_scope": self.subject_scope,
            "controls": list(self.controls),
            "ignore": list(self.ignore),
            "is_url": self.is_url,
            "exists": self.exists,
            "blocked_reason": self.blocked_reason,
        }


@dataclass(frozen=True)
class LogicalSubject:
    id: str
    normalized_scope: str
    source_physical_ids: tuple[str, ...]
    binding_ids: tuple[str, ...]
    controls: tuple[str, ...]
    ignore: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "normalized_scope": self.normalized_scope,
            "source_physical_ids": list(self.source_physical_ids),
            "binding_ids": list(self.binding_ids),
            "controls": list(self.controls),
            "ignore": list(self.ignore),
        }


@dataclass(frozen=True)
class ReferenceGraph:
    physical: tuple[PhysicalReference, ...]
    bindings: tuple[LogicalBinding, ...]
    subjects: tuple[LogicalSubject, ...]
    digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "manju.video-reference-graph/v1",
            "digest": self.digest,
            "physical": [row.to_dict() for row in self.physical],
            "bindings": [row.to_dict() for row in self.bindings],
            "subjects": [row.to_dict() for row in self.subjects],
        }


@dataclass(frozen=True)
class KeyframeBinding:
    id: str
    index: int
    authored_position: str | None
    role: str | None
    at_ms: int | None
    image: str | None
    prompt: str | None
    sha256: str | None
    local_asset: str | None
    safe_remote_identity: str | None
    source_path: Path | None = None
    blocked_reason: str | None = None
    missing: bool = False

    def to_dict(self) -> dict[str, Any]:
        row = {
            "id": self.id,
            "index": self.index,
            "position": self.authored_position,
            "role": self.role,
            "at_ms": self.at_ms,
            "image": self.image,
            "prompt": self.prompt,
            "sha256": self.sha256,
            "local_asset": self.local_asset,
            "safe_remote_identity": self.safe_remote_identity,
        }
        if self.blocked_reason:
            row["blocked_reason"] = self.blocked_reason
        if self.missing:
            row["missing"] = True
        return row


@dataclass(frozen=True)
class ConditioningFacts:
    has_text: bool
    start_frames: tuple[str, ...]
    end_frames: tuple[str, ...]
    ordered_references: tuple[str, ...]
    reference_images: tuple[str, ...]
    reference_videos: tuple[str, ...]
    reference_audio: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_text": self.has_text,
            "start_frames": list(self.start_frames),
            "end_frames": list(self.end_frames),
            "ordered_references": list(self.ordered_references),
            "reference_images": list(self.reference_images),
            "reference_videos": list(self.reference_videos),
            "reference_audio": list(self.reference_audio),
        }


@dataclass(frozen=True)
class DialogueFacts:
    speaker: str | None
    text: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"speaker": self.speaker, "text": self.text}


@dataclass(frozen=True)
class AudioFacts:
    cues: tuple[str, ...]
    music: tuple[str, ...]
    lyrics: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cues": list(self.cues),
            "music": list(self.music),
            "lyrics": list(self.lyrics),
        }


@dataclass(frozen=True)
class VideoAuthoringPlan:
    schema: str
    shot_id: str
    duration_ms: int | None
    aspect_ratio: str | None
    intent_sections: Mapping[str, str]
    compiled_prompt: str
    exact_prompt_override: str | None
    keyframes: tuple[KeyframeBinding, ...]
    reference_graph: ReferenceGraph
    conditioning: ConditioningFacts
    dialogue: DialogueFacts
    audio: AudioFacts
    visible_text: tuple[str, ...]
    source_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "shot_id": self.shot_id,
            "duration_ms": self.duration_ms,
            "aspect_ratio": self.aspect_ratio,
            "intent_sections": dict(self.intent_sections),
            "compiled_prompt": self.compiled_prompt,
            "exact_prompt_override": self.exact_prompt_override,
            "keyframes": [row.to_dict() for row in self.keyframes],
            "reference_graph": self.reference_graph.to_dict(),
            "conditioning": self.conditioning.to_dict(),
            "dialogue": self.dialogue.to_dict(),
            "audio": self.audio.to_dict(),
            "visible_text": list(self.visible_text),
            "source_digest": self.source_digest,
        }


def _reference_graph(project: Any, refset: Any) -> ReferenceGraph:
    physical_by_key: dict[tuple[str, str], PhysicalReference] = {}
    kind_counts: dict[str, int] = {}
    bindings: list[LogicalBinding] = []

    for index, item in enumerate(getattr(refset, "items", ()) or ()):
        kind = str(getattr(item, "kind", "image"))
        key = physical_ref_key(item)
        physical = physical_by_key.get(key)
        if physical is None:
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            prefix = {"image": "Picture", "video": "Video", "audio": "Audio"}.get(
                kind, "Asset"
            )
            physical_id = f"{prefix}{kind_counts[kind]}"
            raw_ref = str(getattr(item, "ref", ""))
            is_url = bool(getattr(item, "is_url", False))
            safe_remote = _safe_url(raw_ref) if is_url else None
            source_path = Path(item.path) if getattr(item, "path", None) else None
            local_asset = None
            digest = None
            if source_path is not None:
                try:
                    local_asset = project.relpath(source_path)
                except Exception:
                    local_asset = None
                if bool(getattr(item, "exists", False)):
                    try:
                        digest = hash_file(source_path)
                    except OSError:
                        digest = None
            source_identity = safe_remote or local_asset or raw_ref
            physical = PhysicalReference(
                id=physical_id,
                kind=kind,
                source_identity=source_identity,
                sha256=digest,
                local_asset=local_asset,
                safe_remote_identity=safe_remote,
                source_path=source_path,
            )
            physical_by_key[key] = physical
        raw_ref = str(getattr(item, "ref", ""))
        bindings.append(LogicalBinding(
            id=f"Binding{index + 1}",
            index=index,
            physical_id=physical.id,
            ref=_safe_url(raw_ref) if getattr(item, "is_url", False) else raw_ref,
            tier=str(getattr(item, "tier", "none")),
            kind=kind,
            subject_scope=normalize_subject_scope(getattr(item, "subject_ref", None)),
            controls=tuple(str(v) for v in (getattr(item, "controls", ()) or ())),
            ignore=tuple(str(v) for v in (getattr(item, "ignore", ()) or ())),
            is_url=bool(getattr(item, "is_url", False)),
            exists=bool(getattr(item, "exists", False)),
            blocked_reason=getattr(item, "blocked_reason", None),
        ))

    subject_rows: dict[str, dict[str, list[str]]] = {}
    for binding in bindings:
        if binding.subject_scope is None:
            continue
        row = subject_rows.setdefault(binding.subject_scope, {
            "physical": [], "bindings": [], "controls": [], "ignore": []
        })
        for key, values in (
            ("physical", (binding.physical_id,)),
            ("bindings", (binding.id,)),
            ("controls", binding.controls),
            ("ignore", binding.ignore),
        ):
            for value in values:
                if value not in row[key]:
                    row[key].append(value)
    subjects = tuple(
        LogicalSubject(
            id=f"Subject{index + 1}",
            normalized_scope=scope,
            source_physical_ids=tuple(row["physical"]),
            binding_ids=tuple(row["bindings"]),
            controls=tuple(row["controls"]),
            ignore=tuple(row["ignore"]),
        )
        for index, (scope, row) in enumerate(subject_rows.items())
    )
    physical = tuple(physical_by_key.values())
    payload = {
        "physical": [row.to_dict() for row in physical],
        "bindings": [row.to_dict() for row in bindings],
        "subjects": [row.to_dict() for row in subjects],
    }
    return ReferenceGraph(physical, tuple(bindings), subjects, hash_value(payload))


def _keyframe_bindings(project: Any, shot: Any, bible: Mapping[str, Any]) -> tuple[KeyframeBinding, ...]:
    rows: list[KeyframeBinding] = []
    duration_ms = (
        int(float(shot.duration) * 1000)
        if isinstance(shot.duration, (int, float)) else None
    )
    for index, frame in enumerate(getattr(shot, "keyframes", ()) or ()):
        authored_position = getattr(frame, "position", None)
        role = authored_position
        at_ms = getattr(frame, "at_ms", None)
        if role is None and at_ms is not None:
            if int(at_ms) <= 0:
                role = "start"
            elif duration_ms is not None and int(at_ms) >= duration_ms:
                role = "end"
        raw = str(getattr(frame, "image", None) or "")
        entry = bible.get(raw)
        if isinstance(entry, dict):
            for key in ("ref_image", "ref_images"):
                value = entry.get(key)
                if isinstance(value, (list, tuple)):
                    value = value[0] if value else None
                if value:
                    raw = str(value)
                    break
        image = raw or None
        local_asset = None
        safe_remote = None
        source_path = None
        blocked_reason = None
        missing = False
        digest = None
        if raw.startswith(("http://", "https://")):
            safe_remote = _safe_url(raw)
            image = safe_remote
        elif raw:
            source_path, blocked_reason = resolve_local_ref(project, raw)
            if blocked_reason:
                image = "<blocked:path>"
                source_path = None
            elif source_path is None or not source_path.is_file():
                missing = True
            else:
                local_asset = project.relpath(source_path)
                image = local_asset
                try:
                    digest = hash_file(source_path)
                except OSError:
                    digest = None
        rows.append(KeyframeBinding(
            id=f"Frame{index + 1}",
            index=index,
            authored_position=authored_position,
            role=role,
            at_ms=at_ms,
            image=image,
            prompt=getattr(frame, "prompt", None),
            sha256=digest,
            local_asset=local_asset,
            safe_remote_identity=safe_remote,
            source_path=source_path,
            blocked_reason=blocked_reason,
            missing=missing,
        ))
    return tuple(rows)


def _aspect_ratio(project: Any) -> str | None:
    config = project.load_config()
    width = int(getattr(config, "width", 0) or 0)
    height = int(getattr(config, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return None
    divisor = gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def build_video_authoring_plan_from_resolved(
    project: Any, shot: Any, bible: Mapping[str, Any], refset: Any
) -> VideoAuthoringPlan:
    """Build a pure derived plan from an already resolved reference set."""
    try:
        validate_control_ownership(refset)
    except ReferenceControlConflict:
        raise
    graph = _reference_graph(project, refset)
    keyframes = _keyframe_bindings(project, shot, bible)
    sections = prompt_contract_sections(shot, dict(bible))
    duration_ms = (
        int(float(shot.duration) * 1000)
        if isinstance(shot.duration, (int, float)) else None
    )
    params = dict(getattr(getattr(shot, "generation", None), "params", {}) or {})
    visible_text = _string_tuple(params.get("visible_text"))
    contract = getattr(shot, "contract", None)
    sound = getattr(contract, "sound", None) if contract is not None else None
    cues = _string_tuple(getattr(sound, "cue", None))
    audio = AudioFacts(
        cues=cues + _string_tuple(params.get("audio_intent")),
        music=_string_tuple(params.get("music")),
        lyrics=_string_tuple(params.get("lyrics")),
    )
    dialogue_text = str(getattr(getattr(shot, "dialogue", None), "text", "") or "")
    dialogue_speaker = str(
        getattr(getattr(shot, "dialogue", None), "speaker", "") or ""
    )
    override = getattr(getattr(shot, "generation", None), "prompt_override", None)
    starts = tuple(row.id for row in keyframes if row.role == "start")
    ends = tuple(row.id for row in keyframes if row.role == "end")
    ordered = tuple(row.id for row in graph.physical)
    conditioning = ConditioningFacts(
        has_text=bool(override is not None or any(sections.values())),
        start_frames=starts,
        end_frames=ends,
        ordered_references=ordered,
        reference_images=tuple(row.id for row in graph.physical if row.kind == "image"),
        reference_videos=tuple(row.id for row in graph.physical if row.kind == "video"),
        reference_audio=tuple(row.id for row in graph.physical if row.kind == "audio"),
    )
    source_payload = {
        "schema": PLAN_SCHEMA,
        "shot_id": shot.id,
        "duration_ms": duration_ms,
        "aspect_ratio": _aspect_ratio(project),
        "intent_sections": sections,
        "compiled_prompt": compile_prompt(shot, dict(bible)),
        "exact_prompt_override": override,
        "keyframes": [row.to_dict() for row in keyframes],
        "reference_graph_digest": graph.digest,
        "conditioning": conditioning.to_dict(),
        "dialogue": {"speaker": dialogue_speaker, "text": dialogue_text},
        "audio": audio.to_dict(),
        "visible_text": list(visible_text),
    }
    return VideoAuthoringPlan(
        schema=PLAN_SCHEMA,
        shot_id=shot.id,
        duration_ms=duration_ms,
        aspect_ratio=source_payload["aspect_ratio"],
        intent_sections=_frozen_mapping(sections),
        compiled_prompt=source_payload["compiled_prompt"],
        exact_prompt_override=override,
        keyframes=keyframes,
        reference_graph=graph,
        conditioning=conditioning,
        dialogue=DialogueFacts(dialogue_speaker or None, dialogue_text or None),
        audio=audio,
        visible_text=visible_text,
        source_digest=hash_value(source_payload),
    )


def build_video_authoring_plan(project: Any, shot_id: str) -> VideoAuthoringPlan:
    shot = project.load_shot(shot_id)
    bible = project.load_bible()
    refset = resolve_refs(project, shot, bible)
    return build_video_authoring_plan_from_resolved(project, shot, bible, refset)


__all__ = [
    "AudioFacts",
    "ConditioningFacts",
    "DialogueFacts",
    "KeyframeBinding",
    "LogicalBinding",
    "LogicalSubject",
    "PhysicalReference",
    "PLAN_SCHEMA",
    "ReferenceGraph",
    "VideoAuthoringPlan",
    "build_video_authoring_plan",
    "build_video_authoring_plan_from_resolved",
]
