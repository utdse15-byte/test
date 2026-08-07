"""Derived leak-safe character trajectories from SceneContract v2."""

from __future__ import annotations

from typing import Any


def character_trajectory(project: Any, character_id: str, *, through_scene: str | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for scene_id in project.scene_contract_ids():
        scene = project.load_scene_contract(scene_id)
        if through_scene and scene_id > through_scene:
            continue
        entry = getattr(scene, "entry_state", {}).get(character_id)
        exit_state = getattr(scene, "exit_state", {}).get(character_id)
        changes = [c.model_dump() for c in getattr(getattr(scene, "change", None), "state_changes", [])
                   if c.subject_ref in (character_id, f"character:{character_id}")]
        if entry or exit_state or changes:
            rows.append({"scene": scene_id,
                         "entry": entry.model_dump() if entry else None,
                         "changes": changes,
                         "exit": exit_state.model_dump() if exit_state else None})
    return {"schema": "manju.character-trajectory/v1", "character": character_id,
            "through_scene": through_scene, "rows": rows}


def open_threads(project: Any, *, through_scene: str | None = None) -> dict[str, Any]:
    """Derive thread history without exposing scenes after ``through_scene``."""
    threads: dict[str, dict[str, Any]] = {}
    for scene_id in project.scene_contract_ids():
        if through_scene and scene_id > through_scene:
            continue
        scene = project.load_scene_contract(scene_id)
        for change in getattr(getattr(scene, "change", None), "thread_changes", []):
            row = threads.setdefault(change.thread_id, {
                "thread_id": change.thread_id,
                "state": "unknown",
                "events": [],
            })
            operation = str(change.operation)
            state = {
                "create": "open",
                "advance": "open",
                "reopen": "open",
                "close": "closed",
                "resolve": "resolved",
                "end": "closed",
            }.get(operation, row["state"])
            row["state"] = state
            row["events"].append({
                "scene": scene_id,
                "operation": operation,
                "statement": change.statement,
                "source_span_ref": change.source_span_ref,
            })
    return {
        "schema": "manju.open-threads/v1",
        "through_scene": through_scene,
        "threads": [threads[key] for key in sorted(threads)],
    }
