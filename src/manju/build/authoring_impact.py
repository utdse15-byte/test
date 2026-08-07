"""Derived authoring impact, separate from picture staleness."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..core.hashing import hash_file, hash_value

AUTHORING_IMPACT_SCHEMA = "manju.authoring-impact/v1"
AUTHORING_ROOTS = ("story/", "shots/", "bible/", "series/")


def _digest(path: Path) -> str:
    try:
        return hash_file(path) if path.is_file() else ""
    except OSError:
        return ""


def authoring_basis(project: Any, paths: Iterable[str] | None = None) -> dict[str, str]:
    """Hash only content truth relevant to an authoring proposal."""
    if paths is None:
        found: list[str] = []
        for root in AUTHORING_ROOTS:
            base = Path(project.root) / root
            if base.exists():
                found.extend(
                    p.relative_to(project.root).as_posix()
                    for p in base.rglob("*") if p.is_file()
                )
        paths = sorted(found)
    return {str(path).replace("\\", "/"): _digest(Path(project.root) / path) for path in paths}


def authoring_basis_digest(basis: dict[str, str]) -> str:
    return hash_value({"schema": AUTHORING_IMPACT_SCHEMA, "basis": basis})


def current_authoring_basis(project: Any, basis: dict[str, str]) -> bool:
    return authoring_basis_digest(authoring_basis(project, basis)) == authoring_basis_digest(basis)


def classify_authoring_impact(changed_paths: Iterable[str]) -> dict[str, Any]:
    paths = [str(p).replace("\\", "/") for p in changed_paths]
    out = {
        "schema": AUTHORING_IMPACT_SCHEMA,
        "paths": sorted(set(paths)),
        "text_review": False,
        "story_lint": False,
        "screen_coverage": False,
        "animatic_experience_review": False,
        "authoring_review_stale": False,
        "picture_stale": False,
    }
    for path in paths:
        if path.startswith("story/"):
            out["text_review"] = True
            out["story_lint"] = True
            out["authoring_review_stale"] = True
            out["screen_coverage"] = True
            out["animatic_experience_review"] = True
        if path.startswith("shots/"):
            out["authoring_review_stale"] = True
            out["screen_coverage"] = True
            out["animatic_experience_review"] = True
            # Existing picture-bearing fields retain the historical spec gate;
            # callers must pass an actual SPEC diff to mark picture stale.
            if path.endswith(".yaml"):
                out["picture_stale"] = False
        if path.startswith("bible/") or path.startswith("series/"):
            out["authoring_review_stale"] = True
            out["text_review"] = True
    return out


def authoring_impact(project: Any, before: dict[str, str]) -> dict[str, Any]:
    after = authoring_basis(project, before)
    changed = [path for path in before if before.get(path, "") != after.get(path, "")]
    result = classify_authoring_impact(changed)
    result["basis_before"] = before
    result["basis_after"] = after
    result["basis_digest"] = authoring_basis_digest(after)
    return result


__all__ = [
    "AUTHORING_IMPACT_SCHEMA", "authoring_basis", "authoring_basis_digest",
    "current_authoring_basis", "classify_authoring_impact", "authoring_impact",
]
