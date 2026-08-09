"""Static registry for offline video-authoring profiles.

Profiles are deliberately separate from executable Providers.  They may
project text and render a manual handoff, but cannot submit, poll, download,
price, qualify, or register media.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .video_authoring import VideoAuthoringPlan


@dataclass(frozen=True)
class VideoAuthoringProfileDescriptor:
    id: str
    revision: str
    display_name: str
    kind: str
    dialect: str
    network: str
    execution: str
    requires_api_key: bool
    capabilities: Mapping[str, Any]
    advisory_limits: Mapping[str, Any]
    source: str
    verified_on: str
    cost: str = "not_applicable"

    @property
    def profile_revision(self) -> str:
        return self.revision

    @property
    def advisory_external_limit(self) -> Mapping[str, Any]:
        return self.advisory_limits

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "revision": self.revision,
            "display_name": self.display_name,
            "kind": self.kind,
            "dialect": self.dialect,
            "network": self.network,
            "execution": self.execution,
            "requires_api_key": self.requires_api_key,
            "capabilities": dict(self.capabilities),
            "advisory_limits": dict(self.advisory_limits),
            "source": self.source,
            "verified_on": self.verified_on,
            "cost": self.cost,
        }


# Compatibility name retained for callers of the H3-only v1 surface.
PromptAuthoringProfile = VideoAuthoringProfileDescriptor


class VideoAuthoringProfile(Protocol):
    descriptor: VideoAuthoringProfileDescriptor

    def project(self, plan: "VideoAuthoringPlan") -> dict[str, Any]: ...

    def lint(
        self, plan: "VideoAuthoringPlan", projection: Mapping[str, Any]
    ) -> tuple[dict[str, Any], ...]: ...

    def render_readme(self, handoff: Mapping[str, Any]) -> str: ...

    def render_bundle_files(
        self,
        plan: "VideoAuthoringPlan",
        projection: Mapping[str, Any],
        handoff: Mapping[str, Any],
    ) -> Mapping[str, str]: ...

    def return_filename_examples(self, shot_id: str) -> tuple[str, ...]: ...

    def animatic_warning(self) -> Mapping[str, Any]: ...

    def profile_claims(self) -> Mapping[str, Any]: ...


MINIMAX_H3_PROFILE = VideoAuthoringProfileDescriptor(
    id="minimax_h3",
    revision="2026-08-08.r1",
    display_name="MiniMax H3 Offline Authoring",
    kind="authoring_only",
    dialect="minimax-h3-authoring-v1",
    network="forbidden",
    execution="unavailable",
    requires_api_key=False,
    capabilities={
        "execution_capability": "unverified_at_execution",
        "dialect_modes": ("T2VA", "I2VA", "L2VA", "FL2VA", "REF2VA"),
    },
    advisory_limits={
        "duration_seconds": {"min": 4, "max": 15},
        "fl2va_images": {"max": 2},
        "ref2va_images": {"max": 9},
        "ref2va_videos": {"max": 3},
        "reference_video_seconds": {"min": 2, "max": 15, "total_max": 15},
        "mixed_reference_files": {"max": 12},
    },
    source="unofficial Manju authoring profile; external limits are advisory",
    verified_on="2026-08-08",
)


PORTABLE_VIDEO_PROFILE = VideoAuthoringProfileDescriptor(
    id="portable_video",
    revision="2026-08-08.r1",
    display_name="Portable Video Handoff",
    kind="authoring_only",
    dialect="portable-natural-language-v1",
    network="forbidden",
    execution="unavailable",
    requires_api_key=False,
    capabilities={"execution_capability": "unverified_at_execution"},
    advisory_limits={},
    source="provider-neutral manual video authoring handoff",
    verified_on="2026-08-08",
)


def _h3_factory() -> VideoAuthoringProfile:
    from .minimax_h3_prompt import MiniMaxH3Profile

    return MiniMaxH3Profile()


def _portable_factory() -> VideoAuthoringProfile:
    from .portable_video_prompt import PortableVideoProfile

    return PortableVideoProfile()


_PROFILE_FACTORIES: dict[str, Callable[[], VideoAuthoringProfile]] = {
    "portable_video": _portable_factory,
    "minimax_h3": _h3_factory,
}
_DESCRIPTORS = {
    PORTABLE_VIDEO_PROFILE.id: PORTABLE_VIDEO_PROFILE,
    MINIMAX_H3_PROFILE.id: MINIMAX_H3_PROFILE,
}
_PROFILE_DESCRIPTOR_HISTORY = {
    (PORTABLE_VIDEO_PROFILE.id, PORTABLE_VIDEO_PROFILE.revision): PORTABLE_VIDEO_PROFILE,
    (MINIMAX_H3_PROFILE.id, MINIMAX_H3_PROFILE.revision): MINIMAX_H3_PROFILE,
}


def get_video_authoring_profile(profile_id: str) -> VideoAuthoringProfile:
    factory = _PROFILE_FACTORIES.get(profile_id)
    if factory is None:
        raise KeyError(f"unknown video authoring profile: {profile_id}")
    return factory()


def video_authoring_profile_registry() -> dict[str, VideoAuthoringProfileDescriptor]:
    return {profile_id: _DESCRIPTORS[profile_id] for profile_id in _PROFILE_FACTORIES}


def get_video_authoring_profile_descriptor(
    profile_id: str, revision: str
) -> VideoAuthoringProfileDescriptor:
    """Return a code-owned historical descriptor without executable profile code."""
    try:
        return _PROFILE_DESCRIPTOR_HISTORY[(profile_id, revision)]
    except KeyError as exc:
        raise KeyError(f"unknown video authoring profile revision: {profile_id}@{revision}") from exc


def video_authoring_profile_descriptor_history(
) -> dict[tuple[str, str], VideoAuthoringProfileDescriptor]:
    return dict(_PROFILE_DESCRIPTOR_HISTORY)


def get_prompt_profile(profile_id: str) -> VideoAuthoringProfileDescriptor:
    try:
        return _DESCRIPTORS[profile_id]
    except KeyError as exc:
        raise KeyError(f"unknown prompt authoring profile: {profile_id}") from exc


def prompt_profile_registry() -> dict[str, VideoAuthoringProfileDescriptor]:
    return video_authoring_profile_registry()


__all__ = [
    "MINIMAX_H3_PROFILE",
    "PORTABLE_VIDEO_PROFILE",
    "PromptAuthoringProfile",
    "VideoAuthoringProfile",
    "VideoAuthoringProfileDescriptor",
    "get_prompt_profile",
    "get_video_authoring_profile",
    "get_video_authoring_profile_descriptor",
    "prompt_profile_registry",
    "video_authoring_profile_descriptor_history",
    "video_authoring_profile_registry",
]
