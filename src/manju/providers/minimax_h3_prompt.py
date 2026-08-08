"""Pure, offline MiniMax H3 authoring projection and lint.

This is intentionally not a Provider. It reads a shot's existing intent and
reference resolution, producing deterministic text and warnings that a human
can hand to an external tool. No network, credentials, inference, or media
registration is reachable from this module.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..core.hashing import hash_file, hash_value
from ..core.intent import prompt_contract_sections
from ..core.models import ShotSpec
from .prompt import compile_prompt
from .prompt_profiles import MINIMAX_H3_PROFILE

H3_HANDOFF_SCHEMA = "manju.provider-handoff/v1"
H3_MODES = ("T2VA", "I2VA", "L2VA", "FL2VA", "REF2VA")


def _keyframe_rows(shot: ShotSpec) -> list[dict[str, Any]]:
    rows = []
    for index, frame in enumerate(getattr(shot, "keyframes", []) or []):
        rows.append({
            "index": index,
            "position": getattr(frame, "position", None),
            "at_ms": getattr(frame, "at_ms", None),
            "image": getattr(frame, "image", None),
            "prompt": getattr(frame, "prompt", None),
        })
    return rows


def _keyframe_mode(shot: ShotSpec, rows: list[dict[str, Any]]) -> str:
    roles: set[str] = set()
    duration_ms = (
        int(float(shot.duration) * 1000)
        if isinstance(shot.duration, (int, float))
        else None
    )
    for row in rows:
        if row.get("position") in {"start", "end"}:
            roles.add(str(row["position"]))
        elif row.get("at_ms") is not None and int(row["at_ms"]) <= 0:
            roles.add("start")
        elif (
            duration_ms is not None
            and row.get("at_ms") is not None
            and int(row["at_ms"]) >= duration_ms
        ):
            roles.add("end")
    if "start" in roles and "end" in roles:
        return "FL2VA"
    if "start" in roles:
        return "I2VA"
    if "end" in roles:
        return "L2VA"
    return "T2VA"


def _ref_rows(refset: Any, project_root: Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(getattr(refset, "items", []) or []):
        path = getattr(item, "path", None)
        digest = None
        if path is not None and getattr(item, "exists", False):
            try:
                digest = hash_file(Path(path))
            except OSError:
                digest = None
        rel = None
        if path is not None and project_root is not None:
            try:
                rel = (
                    Path(path).resolve()
                    .relative_to(Path(project_root).resolve())
                    .as_posix()
                )
            except ValueError:
                rel = None
        raw_ref = str(getattr(item, "ref", ""))
        safe_ref = raw_ref
        if getattr(item, "is_url", False):
            parsed = urlsplit(raw_ref)
            safe_ref = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        rows.append({
            "index": index,
            "ref": safe_ref,
            "tier": getattr(item, "tier", "none"),
            "kind": getattr(item, "kind", "image"),
            "path": rel,
            "is_url": bool(getattr(item, "is_url", False)),
            "exists": bool(getattr(item, "exists", False)),
            "sha256": digest,
            "controls": list(getattr(item, "controls", ()) or ()),
            "ignore": list(getattr(item, "ignore", ()) or ()),
            "subject_ref": getattr(item, "subject_ref", None),
            "blocked_reason": getattr(item, "blocked_reason", None),
        })
    return rows


def reference_plan_digest(rows: list[dict[str, Any]]) -> str:
    """Digest the frozen logical reference plan (stable across all outputs)."""
    return hash_value([
        {
            key: row.get(key)
            for key in (
                "ref",
                "tier",
                "kind",
                "path",
                "is_url",
                "exists",
                "sha256",
                "controls",
                "ignore",
                "subject_ref",
            )
        }
        for row in rows
    ])


def _reference_labels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable H3 labels while preserving every logical binding."""
    physical: dict[tuple[Any, ...], str] = {}
    counts = {"image": 0, "video": 0}
    labels: list[dict[str, Any]] = []
    for row in rows:
        kind = str(row["kind"])
        key = (kind, row.get("path") or row.get("ref"), row.get("sha256"))
        label = physical.get(key)
        if label is None:
            counts[kind] += 1
            label = f"{'Image' if kind == 'image' else 'Video'}{counts[kind]}"
            physical[key] = label
        labels.append({
            "label": label,
            "binding_index": row["index"],
            "kind": kind,
            "subject_ref": row.get("subject_ref"),
            "controls": list(row.get("controls") or []),
            "ignore": list(row.get("ignore") or []),
        })
    return labels


def _duration(shot: ShotSpec) -> float | None:
    value = getattr(shot, "duration", "auto")
    return float(value) if isinstance(value, (int, float)) else None


def project_h3_mode(
    shot: ShotSpec, ref_rows: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Project existing keyframes/references into H3's external mode."""
    mode = _keyframe_mode(shot, _keyframe_rows(shot))
    ordinary = [row for row in ref_rows if row["kind"] in {"image", "video"}]
    if mode != "T2VA" and ordinary:
        return mode, [{
            "code": "h3_mode_conflict",
            "level": "blocker",
            "message": (
                "start/end keyframes cannot be combined with ordinary "
                "references for H3"
            ),
        }]
    if mode == "T2VA" and ordinary:
        mode = "REF2VA"
    return mode, []


def _structured_prompt(
    shot: ShotSpec,
    bible: dict[str, dict],
    mode: str,
    reference_labels: list[dict[str, Any]],
) -> tuple[str, str]:
    override = getattr(getattr(shot, "generation", None), "prompt_override", None)
    if isinstance(override, str):
        return override, "prompt_override"
    fields = prompt_contract_sections(shot, bible)
    detail = compile_prompt(shot, bible)
    if mode == "REF2VA":
        label_lines = []
        for row in reference_labels:
            scope = row.get("subject_ref") or "unscoped"
            controls = ", ".join(row.get("controls") or []) or "undeclared"
            label_lines.append(f"{row['label']}: subject={scope}; controls={controls}")
        subjects = "\n".join(label_lines) or (
            fields.get("characters") or fields.get("scene") or "N/A"
        )
        parts = [
            f"subject_definitions:\n{subjects}",
            f"summary:\n{fields.get('scene') or 'N/A'}",
            "retention_analysis:\nN/A",
            f"detailed_description:\n{detail or 'N/A'}",
            "overall_soundscape:\nN/A",
            "non_diegetic_music:\nN/A",
        ]
    else:
        parts = [
            f"integrated_multimodal_description:\n{detail or 'N/A'}",
            "overall_soundscape:\nN/A",
            "non_diegetic_music:\nN/A",
        ]
    return "\n\n".join(parts), "deterministic_fallback"


def lint_h3(
    shot: ShotSpec,
    ref_rows: list[dict[str, Any]],
    mode: str,
    mode_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings = list(mode_findings)
    duration = _duration(shot)
    if duration is not None and not (4 <= duration <= 15):
        findings.append({
            "code": "h3_duration_advisory",
            "level": "warning",
            "message": "advisory external duration range is 4-15 seconds",
            "value": duration,
        })
    images = sum(row["kind"] == "image" for row in ref_rows)
    videos = sum(row["kind"] == "video" for row in ref_rows)
    if mode == "FL2VA" and len(_keyframe_rows(shot)) > 2:
        findings.append({
            "code": "h3_fl2va_image_advisory",
            "level": "warning",
            "message": "FL2VA advisory limit is two keyframe images",
        })
    if mode == "REF2VA" and images > 9:
        findings.append({
            "code": "h3_ref2va_image_advisory",
            "level": "warning",
            "message": "REF2VA advisory limit is nine images",
            "value": images,
        })
    if mode == "REF2VA" and videos > 3:
        findings.append({
            "code": "h3_ref2va_video_advisory",
            "level": "warning",
            "message": "REF2VA advisory limit is three reference videos",
            "value": videos,
        })
    if mode == "REF2VA" and images + videos > 12:
        findings.append({
            "code": "h3_mixed_reference_advisory",
            "level": "warning",
            "message": "advisory mixed reference file limit is twelve",
            "value": images + videos,
        })
    for row in ref_rows:
        if row.get("blocked_reason"):
            findings.append({
                "code": "h3_reference_blocked",
                "level": "blocker",
                "message": row["blocked_reason"],
                "ref": row["ref"],
            })
        elif not row.get("is_url") and not row.get("exists"):
            findings.append({
                "code": "h3_reference_missing",
                "level": "blocker",
                "message": f"local reference is missing: {row['ref']}",
                "ref": row["ref"],
            })
    return findings


def _validate_keyframes(
    frames: list[dict[str, Any]],
    bible: dict[str, dict],
    project_root: Path | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for row in frames:
        raw = str(row.get("image") or "")
        role = row.get("position")
        if not raw:
            if role in {"start", "end"}:
                findings.append({
                    "code": "h3_keyframe_image_missing",
                    "level": "blocker",
                    "message": f"{role} keyframe has no image",
                })
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
            parsed = urlsplit(raw)
            row["image"] = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, "", "")
            )
            row["remote_not_downloaded"] = True
            continue
        path = Path(raw)
        if path.is_absolute():
            row["image"] = "<blocked:absolute-path>"
            findings.append({
                "code": "h3_keyframe_blocked",
                "level": "blocker",
                "message": "absolute keyframe paths are outside the project boundary",
            })
            continue
        if project_root is None:
            continue
        root = Path(project_root).resolve()
        resolved = (root / path).resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            row["image"] = "<blocked:path-escape>"
            findings.append({
                "code": "h3_keyframe_blocked",
                "level": "blocker",
                "message": "keyframe path escapes the project boundary",
            })
            continue
        row["image"] = relative
        if not resolved.is_file():
            findings.append({
                "code": "h3_keyframe_image_missing",
                "level": "blocker",
                "message": f"keyframe image is missing: {relative}",
            })
        else:
            row["sha256"] = hash_file(resolved)
    return findings


def build_h3_projection(
    shot: ShotSpec,
    bible: dict[str, dict],
    refset: Any,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    ref_rows = _ref_rows(refset, project_root)
    mode, mode_findings = project_h3_mode(shot, ref_rows)
    labels = _reference_labels(ref_rows)
    prompt, origin = _structured_prompt(shot, bible, mode, labels)
    digest = reference_plan_digest(ref_rows)
    findings = lint_h3(shot, ref_rows, mode, mode_findings)
    keyframes = _keyframe_rows(shot)
    findings.extend(_validate_keyframes(keyframes, bible, project_root))
    return {
        "schema": H3_HANDOFF_SCHEMA,
        "profile": asdict(MINIMAX_H3_PROFILE),
        "shot": shot.id,
        "mode": mode,
        "prompt": prompt,
        "prompt_origin": origin,
        "quality_status": (
            "needs_director_review"
            if origin == "deterministic_fallback"
            else "author_override_unverified"
        ),
        "keyframes": keyframes,
        "references": ref_rows,
        "reference_labels": labels,
        "reference_plan_digest": digest,
        "findings": findings,
        "eligibility": {
            "bundle": "handoff-only",
            "generated_media": "absent",
            "picture_lock": "not_eligible",
        },
    }


__all__ = [
    "H3_HANDOFF_SCHEMA",
    "H3_MODES",
    "build_h3_projection",
    "lint_h3",
    "project_h3_mode",
    "reference_plan_digest",
]
