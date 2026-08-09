"""Frozen, secret-free lineage carried from verified handoff to manual take."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class VerifiedHandoffLineage:
    schema: str
    shot: str
    handoff_id: str
    profile_id: str
    profile_revision: str | None
    semantic_digest: str
    manifest_digest: str | None
    reference_plan_digest: str
    bundle_format_revision: int | None
    renderer_revision: str | None

    @classmethod
    def from_mapping(cls, handoff: Mapping[str, Any]) -> "VerifiedHandoffLineage":
        """Compatibility adapter for callers that still pass handoff metadata."""
        profile = handoff.get("profile")
        profile_id = profile.get("id") if isinstance(profile, Mapping) else None
        profile_revision = (
            profile.get("revision") if isinstance(profile, Mapping) else None
        )
        return cls(
            schema=str(handoff.get("schema") or ""),
            shot=str(handoff.get("shot") or ""),
            handoff_id=str(handoff.get("handoff_id") or ""),
            profile_id=str(profile_id or handoff.get("target") or ""),
            profile_revision=(
                str(profile_revision) if profile_revision is not None else None
            ),
            semantic_digest=str(
                handoff.get("semantic_digest") or handoff.get("bundle_digest") or ""
            ),
            manifest_digest=(
                str(handoff["manifest_digest"])
                if handoff.get("manifest_digest") is not None
                else None
            ),
            reference_plan_digest=str(handoff.get("reference_plan_digest") or ""),
            bundle_format_revision=(
                int(handoff["bundle_format_revision"])
                if handoff.get("bundle_format_revision") is not None
                else None
            ),
            renderer_revision=(
                str(handoff["renderer_revision"])
                if handoff.get("renderer_revision") is not None
                else None
            ),
        )

    def sidecar_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "source": "external_manual_roundtrip",
            "handoff_id": self.handoff_id,
            "handoff_profile": self.profile_id,
            "handoff_semantic_digest": self.semantic_digest,
            # Compatibility alias retained for existing readers.
            "bundle_digest": self.semantic_digest,
            "handoff_reference_plan_digest": self.reference_plan_digest,
            "claimed_generator": "unverified",
        }
        optional = {
            "handoff_profile_revision": self.profile_revision,
            "handoff_manifest_digest": self.manifest_digest,
            "handoff_bundle_format_revision": self.bundle_format_revision,
            "handoff_renderer_revision": self.renderer_revision,
        }
        params.update({key: value for key, value in optional.items() if value is not None})
        return params


def coerce_handoff_lineage(
    value: VerifiedHandoffLineage | Mapping[str, Any],
) -> VerifiedHandoffLineage:
    if isinstance(value, VerifiedHandoffLineage):
        return value
    return VerifiedHandoffLineage.from_mapping(value)


__all__ = ["VerifiedHandoffLineage", "coerce_handoff_lineage"]
