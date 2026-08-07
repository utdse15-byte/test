"""Leak-safe scene context packets for directing."""

from __future__ import annotations

from typing import Any

from .trajectory import character_trajectory


def context_packet(project: Any, scene_id: str, *, for_directing: bool = True) -> dict[str, Any]:
    scene = project.load_scene_contract(scene_id)
    characters = sorted(set(getattr(scene, "entry_state", {})) | set(getattr(scene, "exit_state", {})))
    # Future scene state is intentionally omitted; a packet is bounded by the
    # requested scene and can be used without leaking later knowledge.
    return {
        "schema": "manju.story-context/v1",
        "scene": scene_id,
        "purpose": scene.purpose,
        "entry_state": {k: v.model_dump() for k, v in getattr(scene, "entry_state", {}).items()},
        "change": getattr(scene, "change", None).model_dump() if getattr(scene, "change", None) else {
            "irreversible_change": list(getattr(scene, "irreversible_change", []))
        },
        "exit_state": {k: v.model_dump() for k, v in getattr(scene, "exit_state", {}).items()},
        "characters": {
            character: character_trajectory(project, character, through_scene=scene_id)
            for character in characters
        },
        "direction": getattr(scene, "direction", None).model_dump() if getattr(scene, "direction", None) else None,
    }
