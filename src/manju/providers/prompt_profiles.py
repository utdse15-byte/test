"""Offline prompt-authoring profiles.

Profiles in this module are deliberately separate from the executable Provider
registry. They describe a projection and an external handoff format only;
they never submit work, estimate cost, or register media.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptAuthoringProfile:
    id: str
    kind: str
    network: str
    execution: str
    cost: str
    requires_api_key: bool
    profile_revision: str
    verified_on: str
    source: str
    advisory_external_limit: dict[str, Any]


MINIMAX_H3_PROFILE = PromptAuthoringProfile(
    id="minimax_h3",
    kind="authoring_only",
    network="forbidden",
    execution="unavailable",
    cost="not_applicable",
    requires_api_key=False,
    profile_revision="2026-08-08.r1",
    verified_on="2026-08-08",
    source="unofficial Manju authoring profile; external limits are advisory",
    advisory_external_limit={
        "duration_seconds": {"min": 4, "max": 15},
        "fl2va_images": {"max": 2},
        "ref2va_images": {"max": 9},
        "ref2va_videos": {"max": 3},
        "reference_video_seconds": {"min": 2, "max": 15, "total_max": 15},
        "mixed_reference_files": {"max": 12},
    },
)


def get_prompt_profile(profile_id: str) -> PromptAuthoringProfile:
    """Return an authoring profile; never consults Provider manifests."""
    if profile_id != MINIMAX_H3_PROFILE.id:
        raise KeyError(f"unknown prompt authoring profile: {profile_id}")
    return MINIMAX_H3_PROFILE


def prompt_profile_registry() -> dict[str, PromptAuthoringProfile]:
    return {MINIMAX_H3_PROFILE.id: MINIMAX_H3_PROFILE}


__all__ = [
    "PromptAuthoringProfile",
    "MINIMAX_H3_PROFILE",
    "get_prompt_profile",
    "prompt_profile_registry",
]
