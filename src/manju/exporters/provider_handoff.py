"""Versioned, deterministic, offline external-authoring handoff bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import yaml

from ..build.readiness import ReadinessError
from ..core.container import ProjectError
from ..core.hashing import hash_value, short_hash
from ..providers.prompt_profiles import (
    get_video_authoring_profile,
    video_authoring_profile_descriptor_history,
)
from ..providers.handoff_lineage import VerifiedHandoffLineage
from ..providers.refs import RefItem, read_ref_bytes
from ..providers.video_authoring import VideoAuthoringPlan, build_video_authoring_plan

LEGACY_SCHEMA = "manju.provider-handoff/v1"
LEGACY_MANIFEST_SCHEMA = "manju.provider-handoff-manifest/v1"
SCHEMA = "manju.provider-handoff/v2"
MANIFEST_SCHEMA = "manju.provider-handoff-manifest/v2"
BUNDLE_FORMAT_REVISION = 2
_RENDERER_REVISION_R1 = "2026-08-08.r1"
_RENDERER_REVISION_R2 = "2026-08-08.r2"
RENDERER_REVISION = _RENDERER_REVISION_R2
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_PROFILE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ProviderHandoffError(RuntimeError):
    def __init__(self, message: str, *, code: str = "handoff_invalid"):
        self.code = code
        self.detail = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class VerifiedHandoff:
    path: Path
    handoff: Mapping[str, Any]
    manifest: Mapping[str, Any]
    members: tuple[str, ...]

    @property
    def metadata(self) -> Mapping[str, Any]:
        return self.handoff

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "handoff": _thaw(self.handoff),
            "manifest": _thaw(self.manifest),
            "members": list(self.members),
        }

    def lineage(self) -> VerifiedHandoffLineage:
        profile = self.handoff.get("profile")
        profile_id = profile.get("id") if isinstance(profile, Mapping) else None
        profile_revision = (
            profile.get("revision") if isinstance(profile, Mapping) else None
        )
        return VerifiedHandoffLineage(
            schema=str(self.handoff["schema"]),
            shot=str(self.handoff["shot"]),
            handoff_id=str(self.handoff["handoff_id"]),
            profile_id=str(profile_id or self.handoff.get("target") or ""),
            profile_revision=(
                str(profile_revision) if profile_revision is not None else None
            ),
            semantic_digest=str(
                self.handoff.get("semantic_digest")
                or self.handoff.get("bundle_digest")
                or ""
            ),
            manifest_digest=(
                str(self.manifest["manifest_digest"])
                if self.manifest.get("manifest_digest") is not None
                else None
            ),
            reference_plan_digest=str(self.handoff["reference_plan_digest"]),
            bundle_format_revision=(
                int(self.handoff["bundle_format_revision"])
                if self.handoff.get("bundle_format_revision") is not None
                else None
            ),
            renderer_revision=(
                str(self.handoff["renderer_revision"])
                if self.handoff.get("renderer_revision") is not None
                else None
            ),
        )


def _fail(code: str, message: str) -> None:
    raise ProviderHandoffError(message, code=code)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _text_bytes(value: str) -> bytes:
    return value.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _prompt_bytes(value: str) -> bytes:
    # Authored prompt overrides are a character-for-character contract.
    return value.encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_basename(name: str) -> str:
    cleaned = _SAFE_NAME.sub("_", Path(name).name).strip("._")
    return cleaned[:80] or "asset"


def _is_reparse_or_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        _fail("handoff_member_missing", f"cannot stat bundle member {path.name}: {exc}")
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _safe_member_path(name: Any) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        _fail("handoff_unsafe_member", f"unsafe bundle member path: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part or part.endswith((" ", ".")) for part in path.parts)
    ):
        _fail("handoff_unsafe_member", f"unsafe bundle member path: {name!r}")
    return path


def _load_json(path: Path, *, code: str, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")

        def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError(f"duplicate key {key!r}")
                out[key] = value
            return out

        value = json.loads(text, object_pairs_hook=no_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        _fail(code, f"invalid {label}: {exc}")
    if not isinstance(value, dict):
        _fail(code, f"{label} must be a JSON object")
    return value, raw


def _inventory(root: Path) -> tuple[dict[str, Path], set[str]]:
    if _is_reparse_or_link(root):
        _fail("handoff_unsafe_member", "bundle root may not be a symlink or reparse point")
    files: dict[str, Path] = {}
    directories: set[str] = set()
    folded: dict[str, str] = {}
    stack = [(root, "")]
    while stack:
        current, prefix = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            _fail("handoff_manifest_invalid", f"cannot enumerate bundle: {exc}")
        for entry in entries:
            name = f"{prefix}/{entry.name}" if prefix else entry.name
            _safe_member_path(name)
            folded_name = name.casefold()
            previous = folded.get(folded_name)
            if previous is not None and previous != name:
                _fail("handoff_case_collision", f"casefold collision: {previous!r} and {name!r}")
            folded[folded_name] = name
            path = Path(entry.path)
            if _is_reparse_or_link(path):
                _fail("handoff_unsafe_member", f"linked bundle member refused: {name}")
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                _fail("handoff_member_missing", f"cannot stat {name}: {exc}")
            if stat.S_ISDIR(info.st_mode):
                directories.add(name)
                stack.append((path, name))
            elif stat.S_ISREG(info.st_mode):
                files[name] = path
            else:
                _fail("handoff_unsafe_member", f"non-regular bundle member refused: {name}")
    return files, directories


def _profile_id(handoff: Mapping[str, Any]) -> str:
    if handoff.get("schema") == LEGACY_SCHEMA:
        target = handoff.get("target")
        if target != "minimax_h3":
            _fail("handoff_profile_unknown", f"unsupported legacy target: {target!r}")
        return "minimax_h3"
    profile = handoff.get("profile")
    profile_id = profile.get("id") if isinstance(profile, dict) else None
    if not isinstance(profile_id, str) or not _SAFE_PROFILE.fullmatch(profile_id):
        _fail("handoff_profile_unknown", f"invalid handoff profile: {profile_id!r}")
    history = video_authoring_profile_descriptor_history()
    if profile_id not in {key[0] for key in history}:
        _fail("handoff_profile_unknown", f"unknown handoff profile: {profile_id}")
    if handoff.get("target") != profile_id:
        _fail("handoff_manifest_invalid", "handoff target/profile identity mismatch")
    revision = profile.get("revision")
    if not isinstance(revision, str) or not revision:
        _fail(
            "handoff_profile_revision_unknown",
            f"invalid handoff profile revision: {revision!r}",
        )
    descriptor = history.get((profile_id, revision))
    if descriptor is None:
        _fail(
            "handoff_profile_revision_unknown",
            f"unknown handoff profile revision: {profile_id}@{revision}",
        )
    if hash_value(profile) != hash_value(descriptor.to_dict()):
        _fail(
            "handoff_profile_descriptor_mismatch",
            f"profile metadata does not match descriptor history: {profile_id}@{revision}",
        )
    return profile_id


def _semantic_asset_rows(
    manifest_members: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for row in manifest_members:
        path = row.get("path")
        if isinstance(path, str) and path.startswith("assets/"):
            rows.append({
                "path": path,
                "sha256": row.get("sha256"),
                "bytes": row.get("bytes"),
            })
    return sorted(rows, key=lambda row: row["path"])


def semantic_document_v2(
    *,
    handoff: Mapping[str, Any],
    refs: Mapping[str, Any],
    prompt_text: str,
    manifest_members: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconstruct the versioned semantic identity from actual bundle content."""
    renderer_revision = handoff.get("renderer_revision")
    if renderer_revision not in {_RENDERER_REVISION_R1, _RENDERER_REVISION_R2}:
        _fail(
            "handoff_renderer_revision_unknown",
            f"unknown renderer revision: {renderer_revision!r}",
        )
    canonical = handoff.get("canonical")
    if not isinstance(canonical, Mapping):
        _fail("handoff_manifest_invalid", "handoff canonical block must be an object")
    document = {
        "bundle_format_revision": handoff.get("bundle_format_revision"),
        "renderer_revision": renderer_revision,
        "profile": _thaw(handoff.get("profile")),
        "source_digest": canonical.get("source_digest"),
        "shot": handoff.get("shot"),
        "projection": {
            "mode": handoff.get("mode"),
            "prompt": prompt_text,
            "prompt_origin": handoff.get("prompt_origin"),
            "quality_status": handoff.get("quality_status"),
        },
        "reference_plan_digest": handoff.get("reference_plan_digest"),
        "refs": _thaw(refs),
        "keyframes": _thaw(handoff.get("keyframes")),
        "assets": _semantic_asset_rows(manifest_members),
        "animatic": _thaw(handoff.get("animatic")),
        "warnings": _thaw(handoff.get("warnings")),
        "eligibility": _thaw(handoff.get("eligibility")),
    }
    if renderer_revision == _RENDERER_REVISION_R2:
        document["canonical"] = _thaw(canonical)
    return document


def verify_handoff_bundle(path: Path | str) -> VerifiedHandoff:
    """Fully verify one directory bundle before its metadata may be trusted."""
    root = Path(path)
    if not root.is_dir():
        _fail("handoff_directory_required", "handoff verification currently requires a directory")
    files, directories = _inventory(root)
    if "handoff.json" not in files:
        _fail("handoff_manifest_missing", "handoff.json is missing")
    if "MANIFEST.json" not in files:
        _fail("handoff_manifest_missing", "MANIFEST.json is missing")
    if "SHA256SUMS" not in files:
        _fail("handoff_manifest_missing", "SHA256SUMS is missing")

    handoff, _handoff_raw = _load_json(
        files["handoff.json"], code="handoff_manifest_invalid", label="handoff metadata"
    )
    manifest, manifest_raw = _load_json(
        files["MANIFEST.json"], code="handoff_manifest_invalid", label="handoff manifest"
    )
    schema = handoff.get("schema")
    if schema not in {LEGACY_SCHEMA, SCHEMA}:
        _fail("handoff_manifest_invalid", f"unsupported handoff schema: {schema!r}")
    expected_manifest_schema = (
        LEGACY_MANIFEST_SCHEMA if schema == LEGACY_SCHEMA else MANIFEST_SCHEMA
    )
    if manifest.get("schema") != expected_manifest_schema:
        _fail(
            "handoff_manifest_invalid",
            f"unsupported manifest schema: {manifest.get('schema')!r}",
        )
    profile_id = _profile_id(handoff)
    members = manifest.get("members")
    if not isinstance(members, list) or not members:
        _fail("handoff_manifest_invalid", "manifest members must be a non-empty list")

    declared: dict[str, dict[str, Any]] = {}
    folded: dict[str, str] = {}
    for row in members:
        if not isinstance(row, dict):
            _fail("handoff_manifest_invalid", "manifest member rows must be objects")
        name = row.get("path")
        _safe_member_path(name)
        if name in {"MANIFEST.json", "SHA256SUMS"}:
            _fail("handoff_manifest_invalid", f"self-referential manifest member: {name}")
        if name in declared:
            _fail("handoff_manifest_invalid", f"duplicate manifest member: {name}")
        previous = folded.get(name.casefold())
        if previous is not None and previous != name:
            _fail("handoff_case_collision", f"casefold collision: {previous!r} and {name!r}")
        folded[name.casefold()] = name
        digest = row.get("sha256")
        size = row.get("bytes")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            _fail("handoff_manifest_invalid", f"invalid manifest row for {name}")
        declared[name] = row
    if "handoff.json" not in declared:
        _fail("handoff_manifest_invalid", "manifest does not declare handoff.json")

    expected_files = set(declared) | {"MANIFEST.json", "SHA256SUMS"}
    extra_files = sorted(set(files) - expected_files)
    if extra_files:
        _fail("handoff_unexpected_member", f"unexpected bundle member: {extra_files[0]}")
    allowed_dirs = {
        PurePosixPath(name).parent.as_posix()
        for name in expected_files
        if PurePosixPath(name).parent.as_posix() != "."
    }
    allowed_dirs |= {
        parent.as_posix()
        for name in tuple(allowed_dirs)
        for parent in PurePosixPath(name).parents
        if parent.as_posix() != "."
    }
    extra_dirs = sorted(directories - allowed_dirs)
    if extra_dirs:
        _fail("handoff_unexpected_member", f"unexpected bundle directory: {extra_dirs[0]}")

    member_bytes: dict[str, bytes] = {}
    for name, row in declared.items():
        member = files.get(name)
        if member is None:
            _fail("handoff_member_missing", f"declared bundle member is missing: {name}")
        try:
            data = member.read_bytes()
        except OSError as exc:
            _fail("handoff_member_missing", f"cannot read bundle member {name}: {exc}")
        if len(data) != row["bytes"]:
            _fail("handoff_member_size_mismatch", f"byte length mismatch for {name}")
        if f"sha256:{_sha256(data)}" != row["sha256"]:
            _fail("handoff_member_hash_mismatch", f"SHA-256 mismatch for {name}")
        member_bytes[name] = data

    checksum_rows = dict(declared)
    checksum_rows["MANIFEST.json"] = {
        "sha256": f"sha256:{_sha256(manifest_raw)}",
        "bytes": len(manifest_raw),
    }
    expected_checksums = "".join(
        f"{row['sha256'].removeprefix('sha256:')}  {name}\n"
        for name, row in sorted(checksum_rows.items())
    ).encode("ascii")
    try:
        actual_checksums = files["SHA256SUMS"].read_bytes()
    except OSError as exc:
        _fail("handoff_checksum_mismatch", f"cannot read SHA256SUMS: {exc}")
    if actual_checksums != expected_checksums:
        _fail("handoff_checksum_mismatch", "SHA256SUMS does not exactly match the manifest")

    if schema == LEGACY_SCHEMA:
        identity_keys = ("handoff_id", "bundle_digest", "reference_plan_digest")
    else:
        identity_keys = (
            "handoff_id",
            "semantic_digest",
            "bundle_digest",
            "shot",
            "reference_plan_digest",
            "renderer_revision",
        )
        if handoff.get("bundle_format_revision") != BUNDLE_FORMAT_REVISION:
            _fail("handoff_manifest_invalid", "unsupported bundle_format_revision")
        if manifest.get("bundle_format_revision") != BUNDLE_FORMAT_REVISION:
            _fail("handoff_manifest_invalid", "manifest bundle_format_revision mismatch")
        if handoff.get("semantic_digest") != handoff.get("bundle_digest"):
            _fail("handoff_manifest_invalid", "bundle_digest must alias semantic_digest")
        for key in ("semantic_digest", "bundle_digest", "reference_plan_digest"):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(handoff.get(key) or "")):
                _fail("handoff_manifest_invalid", f"invalid {key}")
        claims = handoff.get("claims")
        if not isinstance(claims, dict) or any(
            claims.get(key) is not False
            for key in (
                "generated_media_included",
                "execution_verified",
                "generator_identity_verified",
                "output_quality_validated",
                "official_certification",
            )
        ):
            _fail("handoff_manifest_invalid", "handoff claims must remain explicitly false")
        return_row = handoff.get("return")
        if (
            not isinstance(return_row, dict)
            or return_row.get("source") != "external_manual_roundtrip"
            or return_row.get("claimed_generator") != "unverified"
            or return_row.get("auto_select") is not False
        ):
            _fail("handoff_manifest_invalid", "invalid manual-return contract")
        projection_row = handoff.get("projection")
        if (
            not isinstance(projection_row, dict)
            or projection_row.get("prompt_file") != "prompt.txt"
            or "prompt.txt" not in declared
        ):
            _fail("handoff_manifest_invalid", "invalid prompt projection contract")
        digest = manifest.get("manifest_digest")
        expected_digest = hash_value(members)
        if digest != expected_digest:
            _fail("handoff_manifest_invalid", "manifest_digest does not match member rows")
        manifest_profile = manifest.get("profile")
        handoff_profile = handoff["profile"]
        if (
            not isinstance(manifest_profile, dict)
            or manifest_profile.get("id") != profile_id
            or manifest_profile.get("revision") != handoff_profile.get("revision")
        ):
            _fail("handoff_manifest_invalid", "handoff/manifest profile identity mismatch")
        if "refs.json" not in declared:
            _fail("handoff_manifest_invalid", "refs.json is not declared")
        try:
            prompt_text = member_bytes["prompt.txt"].decode("utf-8")
        except UnicodeDecodeError as exc:
            _fail("handoff_manifest_invalid", f"prompt.txt must be exact UTF-8: {exc}")
        refs_doc, _refs_raw = _load_json(
            files["refs.json"], code="handoff_manifest_invalid", label="reference graph"
        )
        canonical_row = handoff.get("canonical")
        if not isinstance(canonical_row, dict):
            _fail("handoff_manifest_invalid", "handoff canonical block must be an object")
        renderer_revision = handoff.get("renderer_revision")
        if renderer_revision not in {_RENDERER_REVISION_R1, _RENDERER_REVISION_R2}:
            _fail(
                "handoff_renderer_revision_unknown",
                f"unknown renderer revision: {renderer_revision!r}",
            )
        shot = handoff.get("shot")
        if not isinstance(shot, str) or not shot:
            _fail("handoff_manifest_invalid", "handoff metadata missing shot")
        cross_document_mismatch = (
            refs_doc.get("shot") != shot
            or refs_doc.get("reference_plan_digest")
            != handoff.get("reference_plan_digest")
            or refs_doc.get("reference_graph_digest")
            != canonical_row.get("reference_graph_digest")
            or handoff.get("mode") != projection_row.get("dialect_mode")
            or handoff.get("quality_status") != projection_row.get("quality_status")
            or (
                renderer_revision != _RENDERER_REVISION_R1
                and handoff.get("prompt_origin")
                != projection_row.get("prompt_origin")
            )
        )
        if cross_document_mismatch:
            _fail(
                "handoff_cross_document_mismatch",
                "handoff, projection, and refs identity fields must agree",
            )
        actual_semantic_digest = hash_value(semantic_document_v2(
            handoff=handoff,
            refs=refs_doc,
            prompt_text=prompt_text,
            manifest_members=members,
        ))
        if handoff.get("semantic_digest") != actual_semantic_digest:
            _fail(
                "handoff_semantic_digest_mismatch",
                "semantic_digest does not match actual bundle content",
            )
        expected_handoff_id = (
            f"{profile_id}:{shot}:{short_hash(actual_semantic_digest, 12)}"
        )
        if handoff.get("handoff_id") != expected_handoff_id:
            _fail(
                "handoff_id_mismatch",
                "handoff_id does not match the recomputed semantic digest",
            )
    for key in identity_keys:
        value = handoff.get(key)
        if not isinstance(value, str) or not value:
            _fail("handoff_manifest_invalid", f"handoff metadata missing {key}")
        if manifest.get(key) != value:
            _fail("handoff_manifest_invalid", f"handoff/manifest identity mismatch for {key}")
    return VerifiedHandoff(
        path=root.resolve(),
        handoff=_freeze(handoff),
        manifest=_freeze(manifest),
        members=tuple(sorted(expected_files)),
    )


def _animatic_context(project: Any) -> dict[str, Any]:
    try:
        from ..build.readiness import current_animatic, current_animatic_approval

        current = current_animatic(project)
        approval = current_animatic_approval(project, current)
        return {
            "current": bool(current.get("current")),
            "path": current.get("path"),
            "sha256": current.get("sha256"),
            "content_key": current.get("content_key"),
            "approval": {
                "approved": bool(approval.get("approved")),
                "event_id": approval.get("event_id"),
            },
        }
    except (ProjectError, ReadinessError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        return {
            "current": False,
            "path": None,
            "sha256": None,
            "content_key": None,
            "approval": {"approved": False, "event_id": None},
            "unavailable_reason": f"animatic_context_unavailable:{exc.__class__.__name__}",
        }


def _read_asset(project: Any, path: Path, kind: str, expected_sha: str | None) -> bytes:
    item = RefItem(
        ref=project.relpath(path), tier="handoff", kind=kind, path=path,
        is_url=False, exists=True, root=project.root,
    )
    try:
        data = read_ref_bytes(item)
    except OSError as exc:
        raise ProviderHandoffError(
            f"asset became unreadable while freezing handoff: {path.name}",
            code="handoff_asset_unreadable",
        ) from exc
    actual = f"sha256:{_sha256(data)}"
    if expected_sha is not None and actual != expected_sha:
        _fail("handoff_asset_changed", f"asset changed while freezing handoff: {path.name}")
    return data


def _asset_members(
    project: Any, plan: VideoAuthoringPlan
) -> tuple[dict[str, bytes], dict[str, str], dict[str, str]]:
    members: dict[str, bytes] = {}
    by_source: dict[tuple[str, str], str] = {}
    physical_assets: dict[str, str] = {}
    frame_assets: dict[str, str] = {}

    def add(path: Path, kind: str, expected_sha: str | None, prefix: str) -> str:
        key = (kind, str(path.resolve()))
        existing = by_source.get(key)
        if existing:
            return existing
        data = _read_asset(project, path, kind, expected_sha)
        digest = _sha256(data)
        name = (
            f"assets/{prefix}_{len(by_source) + 1:02d}_{digest[:12]}_"
            f"{_safe_basename(path.name)}"
        )
        if name.casefold() in {value.casefold() for value in members}:
            _fail("handoff_case_collision", f"case-insensitive asset path collision: {name}")
        members[name] = data
        by_source[key] = name
        return name

    for row in plan.reference_graph.physical:
        if row.source_path is None or row.safe_remote_identity is not None:
            continue
        physical_assets[row.id] = add(row.source_path, row.kind, row.sha256, "ref")
    for frame in plan.keyframes:
        if frame.source_path is None:
            continue
        frame_assets[frame.id] = add(frame.source_path, "image", frame.sha256, "keyframe")
    return members, physical_assets, frame_assets


def build_handoff(project: Any, shot_id: str, *, target: str = "minimax_h3") -> dict[str, Any]:
    """Build a generic v2 handoff from one frozen canonical plan."""
    try:
        profile = get_video_authoring_profile(target)
    except KeyError as exc:
        raise ProviderHandoffError(str(exc), code="handoff_profile_unknown") from exc
    plan = build_video_authoring_plan(project, shot_id)
    projection = dict(profile.project(plan))
    findings = list(profile.lint(plan, projection))
    projection["findings"] = findings
    blockers = [row for row in findings if row.get("level") == "blocker"]
    if blockers:
        codes = ", ".join(str(row.get("code")) for row in blockers)
        raise ProviderHandoffError(f"handoff blocked: {codes}", code="handoff_blocked")

    asset_sources, physical_assets, frame_assets = _asset_members(project, plan)
    reference_plan_digest = projection.get(
        "reference_plan_digest", plan.reference_graph.digest
    )
    label_by_index = {
        int(row["binding_index"]): row["label"]
        for row in projection.get("reference_labels", ())
    }
    physical_by_id = {row.id: row for row in plan.reference_graph.physical}
    logical_bindings = []
    for binding in plan.reference_graph.bindings:
        physical = physical_by_id[binding.physical_id]
        row = {
            "index": binding.index,
            "binding_id": binding.id,
            "physical_id": binding.physical_id,
            "ref": binding.ref,
            "tier": binding.tier,
            "kind": binding.kind,
            "path": physical.local_asset,
            "is_url": binding.is_url,
            "exists": binding.exists,
            "sha256": physical.sha256,
            "controls": list(binding.controls),
            "ignore": list(binding.ignore),
            "subject_ref": binding.subject_scope,
            "asset": physical_assets.get(binding.physical_id),
            "label": label_by_index.get(binding.index),
            "remote_not_downloaded": binding.is_url,
        }
        logical_bindings.append(row)
    refs_doc = {
        "schema": "manju.video-reference-graph/v1",
        "shot": shot_id,
        "reference_plan_digest": reference_plan_digest,
        "reference_graph_digest": plan.reference_graph.digest,
        "logical_bindings": logical_bindings,
        "logical_subjects": [row.to_dict() for row in plan.reference_graph.subjects],
        "physical_assets": [
            {
                **row.to_dict(),
                "path": physical_assets.get(row.id),
                "sha256": (
                    f"sha256:{_sha256(asset_sources[physical_assets[row.id]])}"
                    if row.id in physical_assets else row.sha256
                ),
            }
            for row in plan.reference_graph.physical
        ],
    }
    keyframes = []
    for frame in plan.keyframes:
        row = frame.to_dict()
        row["asset"] = frame_assets.get(frame.id)
        keyframes.append(row)
    animatic = _animatic_context(project)
    animatic["shot_timing_digest"] = hash_value({
        "shot": plan.shot_id, "duration_ms": plan.duration_ms,
    })
    animatic["keyframe_digest"] = hash_value([row.to_dict() for row in plan.keyframes])
    warnings = list(findings)
    if not animatic["approval"]["approved"]:
        warnings.append(dict(profile.animatic_warning()))

    handoff = {
        "schema": SCHEMA,
        "bundle_format_revision": BUNDLE_FORMAT_REVISION,
        "renderer_revision": RENDERER_REVISION,
        "target": target,
        "profile": profile.descriptor.to_dict(),
        "shot": shot_id,
        "mode": projection["mode"],
        "prompt_origin": projection["prompt_origin"],
        "quality_status": projection["quality_status"],
        "reference_plan_digest": reference_plan_digest,
        "reference_labels": projection.get("reference_labels", []),
        "keyframes": keyframes,
        "animatic": animatic,
        "warnings": warnings,
        "eligibility": projection["eligibility"],
        "canonical": {
            "duration_ms": plan.duration_ms,
            "aspect_ratio": plan.aspect_ratio,
            "conditioning": plan.conditioning.to_dict(),
            "reference_graph_digest": plan.reference_graph.digest,
            "source_digest": plan.source_digest,
        },
        "projection": {
            "dialect_mode": projection["mode"],
            "prompt_file": "prompt.txt",
            "prompt_origin": projection["prompt_origin"],
            "quality_status": projection["quality_status"],
        },
        "claims": {
            "generated_media_included": False,
            "execution_verified": False,
            "generator_identity_verified": False,
            "output_quality_validated": False,
            "official_certification": False,
        },
        "profile_claims": dict(profile.profile_claims()),
        "return": {
            "source": "external_manual_roundtrip",
            "claimed_generator": "unverified",
            "auto_select": False,
        },
    }
    asset_rows = [
        {"path": name, "sha256": f"sha256:{_sha256(data)}", "bytes": len(data)}
        for name, data in sorted(asset_sources.items())
    ]
    semantic_digest = hash_value(semantic_document_v2(
        handoff=handoff,
        refs=refs_doc,
        prompt_text=projection["prompt"],
        manifest_members=asset_rows,
    ))
    handoff.update({
        "handoff_id": f"{target}:{shot_id}:{short_hash(semantic_digest, 12)}",
        "semantic_digest": semantic_digest,
        "bundle_digest": semantic_digest,
    })
    return {
        "plan": plan,
        "profile_impl": profile,
        "projection": projection,
        "handoff": handoff,
        "refs": refs_doc,
        "asset_sources": asset_sources,
    }


def _bundle_files(built: Mapping[str, Any]) -> dict[str, bytes]:
    plan = built["plan"]
    profile = built["profile_impl"]
    projection = built["projection"]
    handoff = built["handoff"]
    files: dict[str, bytes] = {
        "handoff.json": _json_bytes(handoff),
        "prompt.txt": _prompt_bytes(projection["prompt"]),
        "refs.json": _json_bytes(built["refs"]),
        "README.md": _text_bytes(profile.render_readme(handoff)),
    }
    for name, text in profile.render_bundle_files(plan, projection, handoff).items():
        _safe_member_path(name)
        files[name] = _text_bytes(text)
    files.update(built["asset_sources"])
    folded: dict[str, str] = {}
    for name in files:
        _safe_member_path(name)
        previous = folded.get(name.casefold())
        if previous is not None and previous != name:
            _fail("handoff_case_collision", f"case-insensitive bundle collision: {name}")
        folded[name.casefold()] = name
    handoff = built["handoff"]
    manifest_members = [
        {"path": name, "sha256": f"sha256:{_sha256(data)}", "bytes": len(data)}
        for name, data in sorted(files.items())
    ]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "bundle_format_revision": BUNDLE_FORMAT_REVISION,
        "renderer_revision": RENDERER_REVISION,
        "handoff_id": handoff["handoff_id"],
        "semantic_digest": handoff["semantic_digest"],
        "bundle_digest": handoff["bundle_digest"],
        "shot": handoff["shot"],
        "profile": {
            "id": handoff["profile"]["id"],
            "revision": handoff["profile"]["revision"],
        },
        "reference_plan_digest": handoff["reference_plan_digest"],
        "manifest_digest": hash_value(manifest_members),
        "members": manifest_members,
    }
    files["MANIFEST.json"] = _json_bytes(manifest)
    files["SHA256SUMS"] = "".join(
        f"{_sha256(data)}  {name}\n" for name, data in sorted(files.items())
    ).encode("ascii")
    return files


def _write_owned_tree(directory: Path, files: Mapping[str, bytes]) -> None:
    directory.mkdir()
    for name, data in sorted(files.items()):
        path = directory.joinpath(*PurePosixPath(name).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def _same_bundle_bytes(directory: Path, expected: Mapping[str, bytes]) -> bool:
    verified = verify_handoff_bundle(directory)
    if set(verified.members) != set(expected):
        return False
    for name, data in expected.items():
        try:
            if directory.joinpath(*PurePosixPath(name).parts).read_bytes() != data:
                return False
        except OSError:
            return False
    return True


def write_handoff_bundle(
    project: Any,
    shot_id: str,
    *,
    output: Path | str = Path("exports/provider_handoff"),
    target: str = "minimax_h3",
) -> tuple[Path, dict[str, Any]]:
    """Atomically publish an immutable v2 bundle directory."""
    built = build_handoff(project, shot_id, target=target)
    handoff = built["handoff"]
    files = _bundle_files(built)
    digest12 = short_hash(handoff["semantic_digest"], 12)
    base = Path(output)
    if not base.is_absolute():
        base = project.root / base
    parent = base / target / shot_id
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / digest12
    if directory.exists():
        if directory.is_dir() and _same_bundle_bytes(directory, files):
            return directory, handoff
        _fail("handoff_immutable_conflict", f"published handoff differs: {directory}")

    temporary = parent / f".{digest12}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        _write_owned_tree(temporary, files)
        verify_handoff_bundle(temporary)
        try:
            os.rename(temporary, directory)
        except OSError as exc:
            if directory.is_dir() and _same_bundle_bytes(directory, files):
                shutil.rmtree(temporary)
                return directory, handoff
            raise ProviderHandoffError(
                f"cannot publish immutable handoff directory: {exc}",
                code="handoff_publish_failed",
            ) from exc
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    verify_handoff_bundle(directory)
    return directory, handoff


def read_handoff_metadata(path: Path | str) -> dict[str, Any]:
    """Compatibility wrapper over the full verifier."""
    return _thaw(verify_handoff_bundle(path).handoff)


__all__ = [
    "BUNDLE_FORMAT_REVISION",
    "LEGACY_MANIFEST_SCHEMA",
    "LEGACY_SCHEMA",
    "MANIFEST_SCHEMA",
    "ProviderHandoffError",
    "RENDERER_REVISION",
    "SCHEMA",
    "VerifiedHandoff",
    "VerifiedHandoffLineage",
    "build_handoff",
    "read_handoff_metadata",
    "semantic_document_v2",
    "verify_handoff_bundle",
    "write_handoff_bundle",
]
