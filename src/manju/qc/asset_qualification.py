"""Exact-media asset qualification evidence; Bible remains identity truth."""

from __future__ import annotations

import re
from typing import Any

from ..core.verifications import append_verification, latest_verification

ASSET_QUALIFICATION_SCHEMA = "manju.asset-qualification/v1"
QUALIFICATION_STATES = frozenset({"qualified", "qualified_with_limits", "not_qualified"})
_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")


def build_asset_qualification(*, asset_ref: str, asset_kind: str, media_sha256: str,
                              capabilities: dict[str, str], state: str,
                              limits: list[str] | None = None,
                              actor_kind: str = "human") -> dict[str, Any]:
    if state not in QUALIFICATION_STATES:
        raise ValueError(f"unknown asset qualification state: {state!r}")
    if not asset_ref.strip() or not asset_kind.strip():
        raise ValueError("asset_ref and asset_kind are required")
    if not _SHA256_RE.fullmatch(str(media_sha256 or "")):
        raise ValueError("media_sha256 must be a full SHA-256 digest")
    if actor_kind != "human" and state in ("qualified", "qualified_with_limits"):
        state = "not_qualified"
    return {
        "schema": ASSET_QUALIFICATION_SCHEMA,
        "kind": "asset_qualification",
        "asset_ref": asset_ref,
        "asset_kind": asset_kind,
        "media_sha256": media_sha256,
        "capabilities": capabilities,
        "state": state,
        "limits": list(limits or []),
        "actor": {"kind": actor_kind},
    }


def record_asset_qualification(project: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    row = dict(evidence)
    if row.get("schema") != ASSET_QUALIFICATION_SCHEMA or row.get("kind") != "asset_qualification":
        raise ValueError("invalid asset qualification schema or kind")
    if row.get("state") not in QUALIFICATION_STATES:
        raise ValueError(f"unknown asset qualification state: {row.get('state')!r}")
    if not str(row.get("asset_ref") or "").strip() or not str(row.get("asset_kind") or "").strip():
        raise ValueError("asset_ref and asset_kind are required")
    if not _SHA256_RE.fullmatch(str(row.get("media_sha256") or "")):
        raise ValueError("media_sha256 must be a full SHA-256 digest")
    actor_kind = str((row.get("actor") or {}).get("kind") or "unattested")
    if actor_kind != "human" and row["state"] in ("qualified", "qualified_with_limits"):
        row["state"] = "not_qualified"
    return append_verification(project, row)


def asset_qualification_status(project: Any, asset_ref: str,
                               media_sha256: str | None = None) -> dict[str, Any]:
    row = latest_verification(
        project, kind="asset_qualification",
        predicate=lambda item: item.get("asset_ref") == asset_ref,
    )
    if row is None:
        return {"asset_ref": asset_ref, "state": "not_qualified", "current": False}
    current = media_sha256 is None or row.get("media_sha256") == media_sha256
    return {"asset_ref": asset_ref, "state": row.get("state"), "current": current,
            "evidence": row if current else None,
            "reason": None if current else "qualified media changed"}


__all__ = ["ASSET_QUALIFICATION_SCHEMA", "QUALIFICATION_STATES",
           "build_asset_qualification", "record_asset_qualification",
           "asset_qualification_status"]
