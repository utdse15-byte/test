"""Exact-media-bound six-pass Animatic Experience Review."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.hashing import hash_file, hash_value
from ..core.intent import screen_intent_digest
from ..core.verifications import append_verification, latest_verification

ANIMATIC_EXPERIENCE_SCHEMA = "manju.animatic-experience-review/v1"
PASSES = ("causal", "silent_visual", "audio_only", "thumbnail", "duration", "transitions")
RESULTS = frozenset({"pass", "pass_with_notes", "fail", "changes_required"})


def build_animatic_experience_review(*, path: str, media_sha256: str,
                                     timeline_digest: str, screen_intent_digest_value: str,
                                     script_sha256: str, temp_audio_digest: str,
                                     passes: dict[str, dict[str, Any]], verdict: str,
                                     actor_kind: str = "human") -> dict[str, Any]:
    missing = [name for name in PASSES if name not in passes]
    if missing:
        raise ValueError(f"animatic review missing passes: {missing}")
    for name in PASSES:
        result = passes[name].get("result")
        if result not in RESULTS:
            raise ValueError(f"invalid {name} pass result: {result!r}")
    if verdict == "approved" and any(
        passes[name].get("result") in ("fail", "changes_required") for name in PASSES
    ):
        raise ValueError("an approved Animatic review cannot contain a failed pass")
    if verdict == "approved" and actor_kind != "human":
        verdict = "provisional"
    return {
        "schema": ANIMATIC_EXPERIENCE_SCHEMA,
        "kind": "animatic_experience_review",
        "target": {"path": path, "sha256": media_sha256, "timeline_digest": timeline_digest,
                    "screen_intent_digest": screen_intent_digest_value,
                    "script_sha256": script_sha256, "temp_audio_digest": temp_audio_digest},
        "passes": passes,
        "verdict": verdict,
        "actor": {"kind": actor_kind},
    }


def record_animatic_experience_review(project: Any, review: dict[str, Any]) -> dict[str, Any]:
    if review.get("schema") != ANIMATIC_EXPERIENCE_SCHEMA or review.get("kind") != "animatic_experience_review":
        raise ValueError("invalid Animatic experience review schema or kind")
    actor = (review.get("actor") or {}).get("kind")
    if review.get("verdict") == "approved" and actor != "human":
        raise ValueError("only a human exact-media review can approve Animatic experience")
    passes = review.get("passes")
    if not isinstance(passes, dict):
        raise ValueError("Animatic experience review passes must be an object")
    missing = [name for name in PASSES if name not in passes]
    if missing:
        raise ValueError(f"animatic review missing passes: {missing}")
    for name in PASSES:
        row = passes.get(name)
        if not isinstance(row, dict) or row.get("result") not in RESULTS:
            raise ValueError(f"invalid {name} pass result")
    if review.get("verdict") == "approved" and any(
        passes[name].get("result") in ("fail", "changes_required") for name in PASSES
    ):
        raise ValueError("an approved Animatic review cannot contain a failed pass")
    target = review.get("target") or {}
    for key in ("path", "sha256", "timeline_digest", "screen_intent_digest",
                "script_sha256", "temp_audio_digest"):
        if not str(target.get(key) or "").strip():
            raise ValueError(f"Animatic experience review target.{key} is required")
    root = Path(project.root).resolve()
    path = (root / str(target.get("path") or "")).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Animatic experience review must bind an existing project media file")
    if hash_file(path) != target.get("sha256"):
        raise ValueError("Animatic experience review media hash does not match exact bytes")
    return append_verification(project, review)


def current_animatic_experience_review(project: Any, *, path: str,
                                       media_sha256: str, timeline_digest: str,
                                       script_sha256: str, temp_audio_digest: str) -> dict[str, Any]:
    expected_screen = screen_intent_digest(project)
    def match(row: dict[str, Any]) -> bool:
        target = row.get("target") or {}
        return target.get("path") == path
    row = latest_verification(project, kind="animatic_experience_review", predicate=match)
    if row is None:
        return {"current": False, "reason": "no Animatic experience review"}
    target = row.get("target") or {}
    fields = {
        "sha256": media_sha256, "timeline_digest": timeline_digest,
        "screen_intent_digest": expected_screen, "script_sha256": script_sha256,
        "temp_audio_digest": temp_audio_digest,
    }
    current = all(target.get(key) == value for key, value in fields.items())
    return {"current": current, "review": row,
            "reason": None if current else "animatic media, intent, script or audio changed"}


__all__ = ["ANIMATIC_EXPERIENCE_SCHEMA", "PASSES", "build_animatic_experience_review",
           "record_animatic_experience_review", "current_animatic_experience_review"]
