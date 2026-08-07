"""Opt-in Creative Charter truth for the v5 dramatic/screen authoring layer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from .hashing import hash_file, hash_value
from .idents import is_safe_segment
from .model_base import ManjuModel

CREATIVE_SCHEMA = "manju.creative-charter/v1"
_SHA_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_FORBIDDEN = frozenset({
    "brief", "ending", "script", "budget", "width", "height", "fps",
    "edit" + "_rate", "frame", "frames", "shots", "prompt", "provider",
})


class TruthRef(ManjuModel):
    path: str
    sha256: str

    @field_validator("path")
    @classmethod
    def _relative_safe_path(cls, value: str) -> str:
        value = str(value or "").replace("\\", "/").strip()
        p = Path(value)
        if not value or p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
            raise ValueError("truth reference path must be a non-empty project-relative path")
        if not all(is_safe_segment(part) or "." in part for part in p.parts):
            raise ValueError(f"unsafe truth reference path: {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _exact_hash(cls, value: str) -> str:
        if not _SHA_RE.fullmatch(str(value or "")):
            raise ValueError("truth reference sha256 must be a full 64-hex SHA-256 digest")
        return str(value).lower()


class AudiovisualCharter(ManjuModel):
    experience_promise: str = ""
    viewing_contract: dict[str, Any] = Field(default_factory=dict)
    first_last_image_relation: dict[str, Any] = Field(default_factory=dict)
    sensory_anchors: dict[str, list[str]] = Field(default_factory=dict)
    motifs: list[dict[str, Any]] = Field(default_factory=list)
    spatial_dramaturgy: list[str] = Field(default_factory=list)
    temporal_attitude: list[str] = Field(default_factory=list)
    channel_policy: dict[str, Any] = Field(default_factory=dict)
    offscreen_strategy: list[str] = Field(default_factory=list)
    ambiguity_to_preserve: list[str] = Field(default_factory=list)
    ai_use_thesis: dict[str, Any] = Field(default_factory=dict)


class CreativeCharter(ManjuModel):
    format: str = CREATIVE_SCHEMA
    revision: int = 1
    brief_ref: TruthRef
    ending_ref: TruthRef
    workflow_depth: str = "standard"
    format_profile: str = "narrative_film"
    commitments: dict[str, Any] = Field(default_factory=dict)
    engine: dict[str, Any] = Field(default_factory=dict)
    audiovisual: AudiovisualCharter = Field(default_factory=AudiovisualCharter)
    production: dict[str, Any] = Field(default_factory=dict)
    rights: dict[str, Any] = Field(default_factory=dict)
    authority: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _no_duplicate_production_truth(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            duplicate = sorted(_FORBIDDEN.intersection(value))
            if duplicate:
                raise ValueError(
                    "Creative Charter cannot duplicate production/source truth: "
                    + ", ".join(duplicate)
                )
        return value

    @field_validator("format")
    @classmethod
    def _known_format(cls, value: str) -> str:
        if value != CREATIVE_SCHEMA:
            raise ValueError(f"format must be {CREATIVE_SCHEMA}")
        return value


def creative_path(project: Any) -> Path:
    return Path(project.root) / "story" / "creative.yaml"


def _ref_status(project: Any, ref: TruthRef) -> dict[str, Any]:
    root = Path(project.root).resolve()
    target = (root / ref.path).resolve()
    status: dict[str, Any] = {"path": ref.path, "expected": ref.sha256, "current": None}
    if not target.is_relative_to(root):
        status["state"] = "unsafe"
        return status
    if not target.is_file():
        status["state"] = "missing"
        return status
    try:
        actual = hash_file(target)
    except OSError:
        status["state"] = "unreadable"
        return status
    status["current"] = actual
    status["state"] = "current" if actual.removeprefix("sha256:") == ref.sha256.removeprefix("sha256:") else "drift"
    return status


def load_creative(project: Any) -> CreativeCharter | None:
    """Load the opt-in charter. Missing file is the legacy no-op."""
    path = creative_path(project)
    if not path.exists():
        return None
    from .yamlio import read_yaml
    data = read_yaml(path) or {}
    if not isinstance(data, Mapping):
        raise ValueError("story/creative.yaml must contain a mapping")
    return CreativeCharter.model_validate(data)


def creative_status(project: Any) -> dict[str, Any]:
    """Pure, explainable status; never writes or migrates a project."""
    charter = load_creative(project)
    if charter is None:
        return {"opted_in": False, "state": "legacy", "schema": CREATIVE_SCHEMA}
    refs = {
        "brief": _ref_status(project, charter.brief_ref),
        "ending": _ref_status(project, charter.ending_ref),
    }
    current = all(item["state"] == "current" for item in refs.values())
    return {
        "opted_in": True,
        "state": "current" if current else "drifted",
        "schema": CREATIVE_SCHEMA,
        "revision": charter.revision,
        "refs": refs,
        "digest": hash_value(charter.model_dump(mode="json")),
    }


__all__ = [
    "CREATIVE_SCHEMA", "TruthRef", "AudiovisualCharter", "CreativeCharter",
    "creative_path", "load_creative", "creative_status",
]
