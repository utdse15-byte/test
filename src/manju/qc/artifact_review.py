"""Text and screen-translation review evidence, bound to current source bytes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.hashing import hash_file, hash_value
from ..core.verifications import append_verification, latest_verification

ARTIFACT_REVIEW_SCHEMA = "manju.artifact-review/v1"
TEXT_LENSES = (
    "story_engine", "structure", "logic", "character_growth", "continuity",
    "dialogue_subtext", "production_feasibility", "ending", "anti_template",
    "format_profile",
)
SCREEN_LENSES = (
    "audiovisual_translation", "attention_design", "duration_and_rhythm",
    "spatial_storytelling", "transition_and_ellipsis", "sound_image_relation",
    "motif_and_repetition", "performance_and_presence", "channel_allocation",
    "over_explanation", "ambiguity_preservation", "screen_breathing",
)


def _target_hash(project: Any, target: str | None) -> str | None:
    if not target:
        return None
    path = (Path(project.root) / target).resolve()
    root = Path(project.root).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return hash_file(path)


def build_artifact_review(*, target: str, lenses: list[str], findings: list[dict[str, Any]],
                          verdict: str, actor_kind: str = "fresh_agent",
                          source_sha256: str | None = None,
                          screen: bool = False) -> dict[str, Any]:
    allowed = set(SCREEN_LENSES if screen else TEXT_LENSES)
    unknown = sorted(set(lenses) - allowed)
    if unknown:
        raise ValueError(f"unknown review lens: {unknown}")
    if actor_kind == "self_check" and verdict == "approved":
        raise ValueError("self_check cannot approve an artifact")
    if screen and actor_kind != "human" and verdict == "approved":
        verdict = "provisional"
    for finding in findings:
        for key in ("evidence", "audience_impact", "production_impact", "requested_change", "fix_owner"):
            finding.setdefault(key, "")
    return {
        "schema": ARTIFACT_REVIEW_SCHEMA,
        "kind": "artifact_review",
        "target": target,
        "source_sha256": source_sha256,
        "lenses": list(lenses),
        "findings": findings,
        "verdict": verdict,
        "actor": {"kind": actor_kind},
        "screen_review": screen,
    }


def record_artifact_review(project: Any, review: dict[str, Any]) -> dict[str, Any]:
    row = dict(review)
    if row.get("kind") != "artifact_review" or row.get("schema") != ARTIFACT_REVIEW_SCHEMA:
        raise ValueError("invalid artifact review schema or kind")
    actor_kind = str((row.get("actor") or {}).get("kind") or "unattested")
    if actor_kind == "self_check" and row.get("verdict") == "approved":
        raise ValueError("self_check cannot approve an artifact")
    if row.get("screen_review") and actor_kind != "human" and row.get("verdict") == "approved":
        row["verdict"] = "provisional"
    findings = row.get("findings")
    if not isinstance(findings, list) or any(not isinstance(item, dict) for item in findings):
        raise ValueError("artifact review findings must be a list of objects")
    required = ("evidence", "audience_impact", "production_impact", "requested_change", "fix_owner")
    missing = [key for index, item in enumerate(findings) for key in required if key not in item]
    if missing:
        raise ValueError("artifact review findings are missing required fields: "
                         + ", ".join(sorted(set(missing))))
    target = row.get("target")
    current = _target_hash(project, target)
    if current is None:
        raise ValueError("artifact review target must be an existing project file")
    if row.get("source_sha256") and row["source_sha256"] != current:
        raise ValueError("artifact review source hash does not match current bytes")
    row["source_sha256"] = current
    return append_verification(project, row)


def current_artifact_review(project: Any, target: str, *, screen: bool | None = None) -> dict[str, Any]:
    current = _target_hash(project, target)
    def match(row: dict[str, Any]) -> bool:
        return row.get("target") == target and (screen is None or row.get("screen_review") is screen)
    row = latest_verification(project, kind="artifact_review", predicate=match)
    if row is None:
        return {"current": False, "reason": "no review evidence"}
    return {"current": bool(current and row.get("source_sha256") == current),
            "review": row, "reason": None if current and row.get("source_sha256") == current else "source changed"}


__all__ = ["ARTIFACT_REVIEW_SCHEMA", "TEXT_LENSES", "SCREEN_LENSES",
           "build_artifact_review", "record_artifact_review", "current_artifact_review"]
