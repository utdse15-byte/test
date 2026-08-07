"""Provider-neutral shot Control View derived from adopted project facts."""

from __future__ import annotations

from typing import Any

from ..core.authoring import state_fact_statement
from ..core.hashing import hash_value

CONTROL_VIEW_SCHEMA = "manju.control-view/v1"


def derive_control_view(project: Any, shot_id: str) -> dict[str, Any]:
    shot = project.load_shot(shot_id)
    contract = shot.contract
    screen = contract.screen if contract else None
    opening = [state_fact_statement(item) for item in (contract.opening if contract else [])]
    performance = contract.performance.model_dump() if contract else {"required": [], "avoid": []}
    physics = contract.physics.model_dump() if contract else {"required": [], "avoid": []}
    sound = contract.sound.model_dump() if contract else {"cue": "", "diegetic": [], "music": ""}
    endpoint = list(contract.endpoint) if contract else []
    control = contract.control.model_dump(exclude_none=True) if contract else {}
    view: dict[str, Any] = {
        "schema": CONTROL_VIEW_SCHEMA,
        "shot": shot_id,
        "screen_context": {
            "role": screen.role if screen else None,
            "viewer_task": screen.justification if screen else "",
            "primary_carrier": screen.primary_carrier if screen else "",
            "experience_beat_refs": list(screen.experience_beat_refs if screen else []),
        },
        "start_anchor": opening,
        "semantic_action": [item for item in (shot.action.main, shot.action.emotion) if item],
        "performance": performance,
        "camera": shot.camera.model_dump(),
        "physics": physics,
        "sound": sound,
        "endpoint": endpoint,
        "control": control,
        "control_owners": {},
        "unknown": [],
        "source_span_refs": list(shot.source_span_refs or []),
    }
    params = shot.generation.params or {}
    for key in ("identity", "motion", "background", "prop", "framing"):
        if key in params:
            view["control_owners"][key] = params[key]
    if not control.get("production_method"):
        view["unknown"].append("production method not adopted")
    if sound.get("cue") and not control.get("production_method"):
        view["unknown"].append("Provider support for exact audio cue")
    if screen and screen.duration.get("mode") == "fixed":
        view["unknown"].append("fixed duration is authoring-only until Animatic adoption")
    view["digest"] = hash_value(view)
    return view


def control_view(project: Any, shot_id: str) -> dict[str, Any]:
    return derive_control_view(project, shot_id)


__all__ = ["CONTROL_VIEW_SCHEMA", "derive_control_view", "control_view"]
