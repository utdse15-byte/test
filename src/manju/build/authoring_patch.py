"""Human-only, atomic truth patch application through the existing Proposal."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..core.authoring import parse_scene_contract
from ..core.creative import CreativeCharter
from ..core.events import append_jsonl_line
from ..core.hashing import hash_file, hash_text, hash_value
from ..core.models import ShotSpec
from ..core.safeio import SafeOutError, checked_out_path
from ..core.yamlio import atomic_write_text, read_yaml

TRUTH_PATCH_SCHEMA = "manju.truth-patch-set/v1"
_ALLOWED_PREFIXES = ("story/", "shots/", "bible/", "series/")
_ALLOWED_ROOTS = ("story", "shots", "bible", "series")


class TruthPatchError(ValueError):
    pass


def _normalise_path(project: Any, raw: Any) -> str:
    path = str(raw or "").replace("\\", "/").lstrip("/")
    if not path or ".." in Path(path).parts or not path.startswith(_ALLOWED_PREFIXES):
        raise TruthPatchError(f"unsafe or unsupported truth patch path: {raw!r}")
    checked_out_path(project.root / path, project_root=project.root,
                    inside_roots=_ALLOWED_ROOTS, kind="truth patch")
    return path


def _bytes_hash(path: Path) -> str:
    return hash_file(path) if path.is_file() else ""


def normalize_patch_set(project: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    patches = payload.get("patches", payload.get("files"))
    if not isinstance(patches, list) or not patches:
        raise TruthPatchError("truth_patch_set requires a non-empty patches list")
    normalized: list[dict[str, Any]] = []
    paths: set[str] = set()
    for item in patches:
        if not isinstance(item, Mapping):
            raise TruthPatchError("each truth patch must be an object")
        path = _normalise_path(project, item.get("path"))
        if path in paths:
            raise TruthPatchError(f"duplicate truth patch path: {path}")
        paths.add(path)
        content = item.get("content", item.get("text"))
        if not isinstance(content, str):
            raise TruthPatchError(f"truth patch {path} content must be text")
        payload_hash = str(item.get("payload_sha256") or hash_text(content)).lower()
        if payload_hash.removeprefix("sha256:") != hash_text(content).removeprefix("sha256:"):
            raise TruthPatchError(f"truth patch {path} payload hash mismatch")
        current = _bytes_hash(project.root / path)
        base = str(item.get("base_sha256", current) or "").lower()
        if base.removeprefix("sha256:") != current.removeprefix("sha256:"):
            raise TruthPatchError(f"truth patch {path} base hash is stale")
        normalized.append({
            "path": path,
            "base_sha256": base,
            "payload_sha256": payload_hash,
            "content": content,
        })
    return {"schema": TRUTH_PATCH_SCHEMA, "patches": normalized}


def patch_basis(project: Any, payload: Mapping[str, Any]) -> dict[str, str]:
    patches = payload.get("patches", payload.get("files")) or []
    return {
        _normalise_path(project, item.get("path")): _bytes_hash(project.root / _normalise_path(project, item.get("path")))
        for item in patches if isinstance(item, Mapping)
    }


def validate_patch_documents(project: Any, patches: list[dict[str, Any]]) -> None:
    for item in patches:
        path = item["path"]
        if not path.endswith((".yaml", ".yml")):
            continue
        from yaml import safe_load
        data = safe_load(item["content"]) or {}
        if path == "story/creative.yaml":
            CreativeCharter.model_validate(data)
        elif path.startswith("story/scenes/"):
            parse_scene_contract(data)
        elif path.startswith("shots/") and path != "shots/index.yaml":
            ShotSpec.model_validate(data)
            current_path = project.root / path
            old = read_yaml(current_path) if current_path.is_file() else {}
            old_locked = old.get("locked", {}) if isinstance(old, dict) else {}
            new_locked = data.get("locked", {}) if isinstance(data, dict) else {}
            if isinstance(old_locked, list):
                old_locked = {str(item): "" for item in old_locked}
            if isinstance(new_locked, list):
                new_locked = {str(item): "" for item in new_locked}
            if old_locked != new_locked:
                raise TruthPatchError(f"{path}: truth patch cannot add/remove/change locks")
            from ..core.locks import verify_locks
            violations = verify_locks(data, old_locked or {}, path)
            changed = [item for item in violations if item.reason == "changed"]
            if changed:
                raise TruthPatchError(f"{path}: locked field change is not allowed")
        elif path.startswith("shots/") and path.endswith("index.yaml"):
            from ..core.models import ShotIndex
            ShotIndex.model_validate(data)
        elif path.startswith("bible/") and not isinstance(data, dict):
            raise TruthPatchError(f"{path} must contain a mapping")


def apply_truth_patch_set(project: Any, payload: Mapping[str, Any], *, actor: str = "human") -> dict[str, Any]:
    if actor != "human":
        raise TruthPatchError("truth_patch_set requires human actor")
    normalized = normalize_patch_set(project, payload)
    patches = normalized["patches"]
    validate_patch_documents(project, patches)
    from ..core.check import run_check
    before_errors = set(run_check(project).errors)
    snapshots: dict[str, bytes | None] = {}
    written: list[str] = []
    try:
        for item in patches:
            path = project.root / item["path"]
            snapshots[item["path"]] = path.read_bytes() if path.exists() else None
            atomic_write_text(path, item["content"])
            written.append(item["path"])
        introduced = [err for err in run_check(project).errors if err not in before_errors]
        if introduced:
            raise TruthPatchError("truth patch introduced check errors: " + " | ".join(introduced[:8]))
    except Exception:
        for rel, old in snapshots.items():
            path = project.root / rel
            if old is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(old)
        raise
    payload_sha256 = hash_value(normalized)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
        "action": "truth_patch_adopted",
        "detail": {
            "schema": TRUTH_PATCH_SCHEMA,
            "paths": written,
            "payload_sha256": payload_sha256,
        },
    }
    try:
        if not append_jsonl_line(project.root, event, durable=True, required=True):
            raise TruthPatchError("adoption event was not written")
    except Exception:
        for rel, old in snapshots.items():
            path = project.root / rel
            if old is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(old)
        raise
    return {"schema": TRUTH_PATCH_SCHEMA, "paths": written, "payload_sha256": payload_sha256}


__all__ = [
    "TRUTH_PATCH_SCHEMA", "TruthPatchError", "normalize_patch_set", "patch_basis",
    "validate_patch_documents", "apply_truth_patch_set",
]
