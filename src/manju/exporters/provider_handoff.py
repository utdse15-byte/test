"""Deterministic, offline external-provider handoff bundles.

The bundle is a derived human handoff, never project/build truth. It contains
no executable Provider, network transport, credential lookup, generated media,
or automatic take selection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..core.hashing import hash_file, hash_value, short_hash
from ..providers.minimax_h3_prompt import build_h3_projection
from ..providers.refs import (
    RefItem,
    ReferenceControlConflict,
    physical_ref_key,
    read_ref_bytes,
    resolve_local_ref,
    resolve_refs,
    validate_control_ownership,
)

SCHEMA = "manju.provider-handoff/v1"
MANIFEST_SCHEMA = "manju.provider-handoff-manifest/v1"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ProviderHandoffError(RuntimeError):
    pass


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _text_bytes(value: str) -> bytes:
    return value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_basename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", Path(name).name).strip("._")
    return cleaned[:80] or "asset"


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _animatic_context(project: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    try:
        from ..build.readiness import current_animatic, current_animatic_approval

        current = current_animatic(project)
        approval = current_animatic_approval(project, current)
        context = {
            "current": bool(current.get("current")),
            "path": current.get("path"),
            "sha256": current.get("sha256"),
            "content_key": current.get("content_key"),
            "approval": {
                "approved": bool(approval.get("approved")),
                "event_id": approval.get("event_id"),
            },
        }
    except Exception as exc:
        context = {
            "current": False,
            "path": None,
            "sha256": None,
            "content_key": None,
            "approval": {"approved": False, "event_id": None},
            "unavailable_reason": f"animatic_context_unavailable:{exc.__class__.__name__}",
        }
    if not context["approval"]["approved"]:
        warnings.append({
            "code": "h3_handoff_before_animatic_approval",
            "level": "warning",
            "message": "handoff exported before the current animatic was human-approved",
        })
    return context, warnings


def _keyframe_sources(project: Any, shot: Any, bible: dict[str, dict]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, frame in enumerate(getattr(shot, "keyframes", []) or []):
        raw = str(getattr(frame, "image", None) or "")
        row: dict[str, Any] = {
            "index": index,
            "position": getattr(frame, "position", None),
            "at_ms": getattr(frame, "at_ms", None),
            "image": None,
            "asset": None,
            "sha256": None,
        }
        if not raw:
            rows.append(row)
            continue
        if raw.startswith(("http://", "https://")):
            row["image"] = _safe_url(raw)
            row["remote_not_downloaded"] = True
            rows.append(row)
            continue
        entry = bible.get(raw)
        if isinstance(entry, dict):
            for key in ("ref_image", "ref_images"):
                value = entry.get(key)
                if isinstance(value, (list, tuple)):
                    value = value[0] if value else None
                if value:
                    raw = str(value)
                    break
        if raw.startswith(("http://", "https://")):
            row["image"] = _safe_url(raw)
            row["remote_not_downloaded"] = True
            rows.append(row)
            continue
        path, reason = resolve_local_ref(project, raw)
        if reason:
            row["blocked_reason"] = reason
        elif path is None or not path.is_file():
            row["missing"] = True
            row["image"] = raw
        else:
            row["image"] = project.relpath(path)
            row["source_path"] = path
            row["sha256"] = hash_file(path)
        rows.append(row)
    return rows


def _asset_members(
    project: Any,
    refset: Any,
    keyframes: list[dict[str, Any]],
    ref_hashes: dict[int, str | None],
    keyframe_hashes: dict[int, str | None],
) -> tuple[dict[str, bytes], dict[int, str], dict[int, str]]:
    members: dict[str, bytes] = {}
    physical_names: dict[tuple[str, str], str] = {}
    ref_assets: dict[int, str] = {}
    keyframe_assets: dict[int, str] = {}

    def add(
        path: Path,
        physical: tuple[str, str],
        prefix: str,
        data: bytes,
        expected_sha: str | None,
    ) -> str:
        existing = physical_names.get(physical)
        if existing is not None:
            return existing
        digest = _sha256(data)
        actual_sha = f"sha256:{digest}"
        if expected_sha is not None and expected_sha != actual_sha:
            raise ProviderHandoffError(
                f"asset changed while freezing handoff inputs: {path.name}"
            )
        name = f"assets/{prefix}_{len(physical_names) + 1:02d}_{digest[:12]}_{_safe_basename(path.name)}"
        folded = {candidate.casefold() for candidate in members}
        if name.casefold() in folded:
            raise ProviderHandoffError(f"case-insensitive asset path collision: {name}")
        members[name] = data
        physical_names[physical] = name
        return name

    for index, item in enumerate(getattr(refset, "items", []) or []):
        if item.is_url or item.path is None or not item.exists:
            continue
        try:
            data = read_ref_bytes(item)
        except OSError as exc:
            raise ProviderHandoffError(
                f"reference became unreadable while freezing handoff: {item.ref}"
            ) from exc
        name = add(
            Path(item.path),
            physical_ref_key(item),
            "ref",
            data,
            ref_hashes.get(index),
        )
        ref_assets[index] = name

    for row in keyframes:
        path = row.pop("source_path", None)
        if path is None:
            continue
        path = Path(path)
        physical = ("image", str(path.resolve()))
        keyframe_item = RefItem(
            ref=str(row.get("image") or ""),
            tier="keyframe",
            kind="image",
            path=path,
            is_url=False,
            exists=True,
            root=project.root,
        )
        try:
            data = read_ref_bytes(keyframe_item)
        except OSError as exc:
            raise ProviderHandoffError(
                f"keyframe became unreadable while freezing handoff: {path.name}"
            ) from exc
        name = add(
            path,
            physical,
            "keyframe",
            data,
            keyframe_hashes.get(int(row["index"])),
        )
        keyframe_assets[int(row["index"])] = name
        row["asset"] = name
        row["sha256"] = f"sha256:{_sha256(data)}"
    return members, ref_assets, keyframe_assets


def build_handoff(project: Any, shot_id: str, *, target: str = "minimax_h3") -> dict[str, Any]:
    """Build one in-memory handoff from a single frozen reference resolution."""
    if target != "minimax_h3":
        raise ProviderHandoffError(f"unsupported authoring target: {target}")
    shot = project.load_shot(shot_id)
    bible = project.load_bible()
    refset = resolve_refs(project, shot, bible)
    try:
        validate_control_ownership(refset)
    except ReferenceControlConflict as exc:
        raise ProviderHandoffError(str(exc)) from exc

    projection = build_h3_projection(
        shot, bible, refset, project_root=project.root
    )
    blockers = [row for row in projection["findings"] if row["level"] == "blocker"]
    keyframes = _keyframe_sources(project, shot, bible)
    for row in keyframes:
        if row.get("blocked_reason"):
            blockers.append({
                "code": "h3_keyframe_blocked",
                "level": "blocker",
                "message": row["blocked_reason"],
            })
        elif row.get("missing"):
            blockers.append({
                "code": "h3_keyframe_missing",
                "level": "blocker",
                "message": f"keyframe image is missing: {row.get('image')}",
            })
    if blockers:
        codes = ", ".join(str(row["code"]) for row in blockers)
        raise ProviderHandoffError(f"handoff blocked: {codes}")

    asset_sources, ref_assets, _keyframe_assets = _asset_members(
        project,
        refset,
        keyframes,
        {int(row["index"]): row.get("sha256") for row in projection["references"]},
        {int(row["index"]): row.get("sha256") for row in projection["keyframes"]},
    )
    labels = {
        int(row["binding_index"]): row["label"]
        for row in projection["reference_labels"]
    }
    refs = []
    for row in projection["references"]:
        clean = {key: value for key, value in row.items() if key != "blocked_reason"}
        clean["asset"] = ref_assets.get(int(row["index"]))
        clean["label"] = labels.get(int(row["index"]))
        clean["remote_not_downloaded"] = bool(row["is_url"])
        refs.append(clean)
    ref_digest = projection["reference_plan_digest"]
    refs_doc = {
        "schema": SCHEMA,
        "shot": shot_id,
        "reference_plan_digest": ref_digest,
        "logical_bindings": refs,
        "physical_assets": [
            {"path": name, "sha256": f"sha256:{_sha256(data)}"}
            for name, data in sorted(asset_sources.items())
        ],
    }
    animatic, animatic_warnings = _animatic_context(project)
    animatic["shot_timing_digest"] = hash_value({
        "shot": shot.id,
        "duration": shot.duration,
    })
    animatic["keyframe_digest"] = hash_value(projection["keyframes"])
    warnings = list(projection["findings"]) + animatic_warnings
    identity_payload = {
        "target": target,
        "shot": shot_id,
        "mode": projection["mode"],
        "prompt": projection["prompt"],
        "prompt_origin": projection["prompt_origin"],
        "quality_status": projection["quality_status"],
        "profile": projection["profile"],
        "reference_plan_digest": ref_digest,
        "reference_labels": projection["reference_labels"],
        "keyframes": keyframes,
        "assets": refs_doc["physical_assets"],
        "animatic": animatic,
        "warnings": warnings,
        "eligibility": projection["eligibility"],
    }
    bundle_digest = hash_value(identity_payload)
    handoff_id = f"{target}:{shot_id}:{short_hash(bundle_digest, 12)}"
    handoff = {
        "schema": SCHEMA,
        "handoff_id": handoff_id,
        "bundle_digest": bundle_digest,
        "target": target,
        "shot": shot_id,
        "mode": projection["mode"],
        "prompt_origin": projection["prompt_origin"],
        "quality_status": projection["quality_status"],
        "reference_plan_digest": ref_digest,
        "reference_labels": projection["reference_labels"],
        "profile": projection["profile"],
        "keyframes": keyframes,
        "animatic": animatic,
        "warnings": warnings,
        "eligibility": projection["eligibility"],
        "claims": {
            "official_minimax_certification": False,
            "h3_output_quality_validated": False,
            "generated_media_included": False,
        },
        "return": {
            "source": "external_manual_roundtrip",
            "claimed_generator": "unverified",
            "auto_select": False,
        },
    }
    return {
        "projection": projection,
        "handoff": handoff,
        "refs": refs_doc,
        "asset_sources": asset_sources,
    }


def _readme(handoff: dict[str, Any]) -> str:
    return f"""# External provider handoff

Shot: {handoff['shot']}
Target profile: {handoff['target']} (unofficial Manju authoring profile)
Mode: {handoff['mode']}
Handoff ID: {handoff['handoff_id']}

`prompt.txt` is the exact authored/fallback prompt. Reference labels and copied
assets are frozen in `refs.json`. Remote URLs were not downloaded and query
strings were removed. This bundle contains no generated media and is not
eligible for Picture Lock.

This package does not claim MiniMax certification, endorsement, or validated
H3 output quality. Follow `RETURN_FILES.md` for the manual ingest/select/QC
loop. The current animatic context is read-only and is recorded in
`handoff.json`.
"""


def _return_files(handoff: dict[str, Any]) -> str:
    shot = handoff["shot"]
    return f"""# Returned files

Name returned videos with the shot id first, for example `{shot}_h3_v1.mp4`.
Do not claim the generator from the filename alone.

1. Preview the ingest plan: `manju ingest <returned-file> --shot {shot}`
2. Apply without selecting: `manju ingest <returned-file> --shot {shot} --apply --no-auto-select --handoff <this-bundle>`
3. Run normal QC and inspect the registered take.
4. Select only after human review: `manju select {shot} <take>`

The manual sidecar records `external_manual_roundtrip`, this handoff ID, bundle
digest, returned file SHA-256, and `claimed_generator: unverified`. It is never
Provider qualification evidence and is never automatically finalized.
"""


def write_handoff_bundle(
    project: Any,
    shot_id: str,
    *,
    output: Path | str = Path("exports/provider_handoff"),
    target: str = "minimax_h3",
) -> tuple[Path, dict[str, Any]]:
    """Write deterministic bundle bytes and return ``(directory, handoff)``."""
    built = build_handoff(project, shot_id, target=target)
    handoff = built["handoff"]
    digest12 = short_hash(handoff["bundle_digest"], 12)
    base = Path(output)
    if not base.is_absolute():
        base = project.root / base
    directory = base / target / shot_id / digest12
    directory.mkdir(parents=True, exist_ok=True)

    files: dict[str, bytes] = {
        "handoff.json": _json_bytes(handoff),
        "prompt.txt": _text_bytes(built["projection"]["prompt"]),
        "refs.json": _json_bytes(built["refs"]),
        "README.md": _text_bytes(_readme(handoff)),
        "RETURN_FILES.md": _text_bytes(_return_files(handoff)),
    }
    for name, data in built["asset_sources"].items():
        if ".." in Path(name).parts or Path(name).is_absolute():
            raise ProviderHandoffError(f"unsafe bundle member: {name}")
        files[name] = data

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "handoff_id": handoff["handoff_id"],
        "bundle_digest": handoff["bundle_digest"],
        "reference_plan_digest": handoff["reference_plan_digest"],
        "members": [
            {"path": name, "sha256": f"sha256:{_sha256(data)}", "bytes": len(data)}
            for name, data in sorted(files.items())
        ],
    }
    files["MANIFEST.json"] = _json_bytes(manifest)
    checksums = "".join(
        f"{_sha256(data)}  {name}\n" for name, data in sorted(files.items())
    )
    files["SHA256SUMS"] = checksums.encode("ascii")

    folded: set[str] = set()
    for name in files:
        if ".." in Path(name).parts or Path(name).is_absolute():
            raise ProviderHandoffError(f"unsafe bundle member: {name}")
        if name.casefold() in folded:
            raise ProviderHandoffError(f"case-insensitive bundle collision: {name}")
        folded.add(name.casefold())
    for name, data in sorted(files.items()):
        path = directory / Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

    return directory, handoff


def read_handoff_metadata(path: Path | str) -> dict[str, Any]:
    """Read and minimally validate bundle metadata for manual ingest lineage."""
    path = Path(path)
    source = path / "handoff.json" if path.is_dir() else path
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderHandoffError(f"invalid handoff metadata: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ProviderHandoffError(f"unsupported handoff schema: {data.get('schema') if isinstance(data, dict) else None}")
    for key in ("handoff_id", "bundle_digest", "shot", "target"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ProviderHandoffError(f"handoff metadata missing {key}")
    if data["target"] != "minimax_h3":
        raise ProviderHandoffError(
            f"unsupported handoff target for manual roundtrip: {data['target']}"
        )
    if path.is_dir() and (path / "MANIFEST.json").is_file():
        try:
            manifest = json.loads(
                (path / "MANIFEST.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderHandoffError(f"invalid handoff manifest: {exc}") from exc
        for key in ("handoff_id", "bundle_digest", "reference_plan_digest"):
            if manifest.get(key) != data.get(key):
                raise ProviderHandoffError(
                    f"handoff metadata/manifest mismatch for {key}"
                )
    return data


__all__ = [
    "MANIFEST_SCHEMA",
    "ProviderHandoffError",
    "SCHEMA",
    "build_handoff",
    "read_handoff_metadata",
    "write_handoff_bundle",
]
