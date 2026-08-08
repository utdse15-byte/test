"""Pure, offline MiniMax H3 authoring projection and lint.

H3 is one strict dialect over the provider-neutral VideoAuthoringPlan.  It is
not an executable Provider and has no network, credentials, inference, or
media-registration path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from ..core.hashing import hash_value
from .prompt_profiles import MINIMAX_H3_PROFILE
from .video_authoring import (
    VideoAuthoringPlan,
    build_video_authoring_plan_from_resolved,
)

H3_HANDOFF_SCHEMA = "manju.provider-handoff/v1"
H3_MODES = ("T2VA", "I2VA", "L2VA", "FL2VA", "REF2VA")


def _legacy_keyframes(plan: VideoAuthoringPlan) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for frame in plan.keyframes:
        row: dict[str, Any] = {
            "index": frame.index,
            "position": frame.authored_position,
            "at_ms": frame.at_ms,
            "image": frame.image,
            "prompt": frame.prompt,
        }
        if frame.sha256:
            row["sha256"] = frame.sha256
        if frame.safe_remote_identity:
            row["remote_not_downloaded"] = True
        rows.append(row)
    return rows


def _legacy_ref_rows(plan: VideoAuthoringPlan) -> list[dict[str, Any]]:
    physical = {row.id: row for row in plan.reference_graph.physical}
    rows = []
    for binding in plan.reference_graph.bindings:
        asset = physical[binding.physical_id]
        rows.append({
            "index": binding.index,
            "ref": binding.ref,
            "tier": binding.tier,
            "kind": binding.kind,
            "path": asset.local_asset,
            "is_url": binding.is_url,
            "exists": binding.exists,
            "sha256": asset.sha256,
            "controls": list(binding.controls),
            "ignore": list(binding.ignore),
            "subject_ref": binding.subject_scope,
            "blocked_reason": binding.blocked_reason,
        })
    return rows


def reference_plan_digest(rows: list[dict[str, Any]]) -> str:
    """Compatibility digest for a frozen logical reference plan."""
    return hash_value([
        {
            key: row.get(key)
            for key in (
                "ref", "tier", "kind", "path", "is_url", "exists", "sha256",
                "controls", "ignore", "subject_ref",
            )
        }
        for row in rows
    ])


def _reference_labels(plan: VideoAuthoringPlan) -> list[dict[str, Any]]:
    picture_labels: dict[str, str] = {}
    counts = {"image": 0, "video": 0, "audio": 0}
    physical = {row.id: row for row in plan.reference_graph.physical}
    for row in plan.reference_graph.physical:
        counts[row.kind] = counts.get(row.kind, 0) + 1
        prefix = {"image": "Image", "video": "Video", "audio": "Audio"}.get(
            row.kind, "Asset"
        )
        picture_labels[row.id] = f"{prefix}{counts[row.kind]}"
    return [
        {
            "label": picture_labels[binding.physical_id],
            "binding_index": binding.index,
            "kind": physical[binding.physical_id].kind,
            "subject_ref": binding.subject_scope,
            "controls": list(binding.controls),
            "ignore": list(binding.ignore),
        }
        for binding in plan.reference_graph.bindings
    ]


def _mode(plan: VideoAuthoringPlan) -> tuple[str, list[dict[str, Any]]]:
    has_start = bool(plan.conditioning.start_frames)
    has_end = bool(plan.conditioning.end_frames)
    if has_start and has_end:
        mode = "FL2VA"
    elif has_start:
        mode = "I2VA"
    elif has_end:
        mode = "L2VA"
    else:
        mode = "T2VA"
    ordinary = bool(plan.conditioning.reference_images or plan.conditioning.reference_videos)
    if mode != "T2VA" and ordinary:
        return mode, [{
            "code": "h3_mode_conflict",
            "level": "blocker",
            "message": "start/end keyframes cannot be combined with ordinary references for H3",
        }]
    return ("REF2VA" if mode == "T2VA" and ordinary else mode), []


def project_h3_mode(
    shot: Any, ref_rows: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Compatibility helper retained for callers of the original H3 module."""
    roles: set[str] = set()
    duration_ms = (
        int(float(shot.duration) * 1000)
        if isinstance(shot.duration, (int, float)) else None
    )
    for frame in getattr(shot, "keyframes", ()) or ():
        role = getattr(frame, "position", None)
        at_ms = getattr(frame, "at_ms", None)
        if role in {"start", "end"}:
            roles.add(role)
        elif at_ms is not None and int(at_ms) <= 0:
            roles.add("start")
        elif duration_ms is not None and at_ms is not None and int(at_ms) >= duration_ms:
            roles.add("end")
    mode = "FL2VA" if roles == {"start", "end"} else (
        "I2VA" if "start" in roles else "L2VA" if "end" in roles else "T2VA"
    )
    ordinary = any(row.get("kind") in {"image", "video"} for row in ref_rows)
    if mode != "T2VA" and ordinary:
        return mode, [{
            "code": "h3_mode_conflict",
            "level": "blocker",
            "message": "start/end keyframes cannot be combined with ordinary references for H3",
        }]
    return ("REF2VA" if mode == "T2VA" and ordinary else mode), []


def _structured_prompt(
    plan: VideoAuthoringPlan,
    mode: str,
    reference_labels: list[dict[str, Any]],
) -> tuple[str, str]:
    if plan.exact_prompt_override is not None:
        return plan.exact_prompt_override, "prompt_override"
    fields = plan.intent_sections
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
            f"detailed_description:\n{plan.compiled_prompt or 'N/A'}",
            "overall_soundscape:\nN/A",
            "non_diegetic_music:\nN/A",
        ]
    else:
        parts = [
            f"integrated_multimodal_description:\n{plan.compiled_prompt or 'N/A'}",
            "overall_soundscape:\nN/A",
            "non_diegetic_music:\nN/A",
        ]
    return "\n\n".join(parts), "deterministic_fallback"


def _lint_plan(
    plan: VideoAuthoringPlan,
    mode: str,
    mode_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings = list(mode_findings)
    limits = MINIMAX_H3_PROFILE.advisory_limits
    duration = plan.duration_ms / 1000 if plan.duration_ms is not None else None
    duration_limit = limits["duration_seconds"]
    if duration is not None and not (
        duration_limit["min"] <= duration <= duration_limit["max"]
    ):
        findings.append({
            "code": "h3_duration_advisory",
            "level": "warning",
            "message": "advisory external duration range is 4-15 seconds",
            "value": duration,
        })
    images = len(plan.conditioning.reference_images)
    videos = len(plan.conditioning.reference_videos)
    if mode == "FL2VA" and len(plan.keyframes) > limits["fl2va_images"]["max"]:
        findings.append({
            "code": "h3_fl2va_image_advisory",
            "level": "warning",
            "message": "FL2VA advisory limit is two keyframe images",
        })
    if mode == "REF2VA" and images > limits["ref2va_images"]["max"]:
        findings.append({
            "code": "h3_ref2va_image_advisory", "level": "warning",
            "message": "REF2VA advisory limit is nine images", "value": images,
        })
    if mode == "REF2VA" and videos > limits["ref2va_videos"]["max"]:
        findings.append({
            "code": "h3_ref2va_video_advisory", "level": "warning",
            "message": "REF2VA advisory limit is three reference videos", "value": videos,
        })
    if mode == "REF2VA" and images + videos > limits["mixed_reference_files"]["max"]:
        findings.append({
            "code": "h3_mixed_reference_advisory", "level": "warning",
            "message": "advisory mixed reference file limit is twelve",
            "value": images + videos,
        })
    for binding in plan.reference_graph.bindings:
        if binding.blocked_reason:
            findings.append({
                "code": "h3_reference_blocked", "level": "blocker",
                "message": binding.blocked_reason, "ref": binding.ref,
            })
        elif not binding.is_url and not binding.exists:
            findings.append({
                "code": "h3_reference_missing", "level": "blocker",
                "message": f"local reference is missing: {binding.ref}", "ref": binding.ref,
            })
    for frame in plan.keyframes:
        if frame.blocked_reason:
            findings.append({
                "code": "h3_keyframe_blocked", "level": "blocker",
                "message": "keyframe path is outside the project boundary",
            })
        elif frame.missing or (frame.role in {"start", "end"} and not frame.image):
            findings.append({
                "code": "h3_keyframe_image_missing", "level": "blocker",
                "message": f"{frame.role or 'authored'} keyframe image is missing",
            })
    return findings


def lint_h3(
    shot: Any,
    ref_rows: list[dict[str, Any]],
    mode: str,
    mode_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compatibility lint using unique physical reference counts."""
    findings = list(mode_findings)
    duration = float(shot.duration) if isinstance(shot.duration, (int, float)) else None
    if duration is not None and not (4 <= duration <= 15):
        findings.append({
            "code": "h3_duration_advisory", "level": "warning",
            "message": "advisory external duration range is 4-15 seconds", "value": duration,
        })
    unique = {
        (row.get("kind"), row.get("path") or row.get("ref"), row.get("sha256"))
        for row in ref_rows
    }
    images = sum(kind == "image" for kind, _identity, _sha in unique)
    videos = sum(kind == "video" for kind, _identity, _sha in unique)
    if mode == "REF2VA" and images > 9:
        findings.append({"code": "h3_ref2va_image_advisory", "level": "warning",
                         "message": "REF2VA advisory limit is nine images", "value": images})
    if mode == "REF2VA" and videos > 3:
        findings.append({"code": "h3_ref2va_video_advisory", "level": "warning",
                         "message": "REF2VA advisory limit is three reference videos", "value": videos})
    if mode == "REF2VA" and images + videos > 12:
        findings.append({"code": "h3_mixed_reference_advisory", "level": "warning",
                         "message": "advisory mixed reference file limit is twelve",
                         "value": images + videos})
    for row in ref_rows:
        if row.get("blocked_reason"):
            findings.append({"code": "h3_reference_blocked", "level": "blocker",
                             "message": row["blocked_reason"], "ref": row.get("ref")})
        elif not row.get("is_url") and not row.get("exists"):
            findings.append({"code": "h3_reference_missing", "level": "blocker",
                             "message": f"local reference is missing: {row.get('ref')}",
                             "ref": row.get("ref")})
    return findings


def project_h3_plan(plan: VideoAuthoringPlan) -> dict[str, Any]:
    mode, mode_findings = _mode(plan)
    labels = _reference_labels(plan)
    prompt, origin = _structured_prompt(plan, mode, labels)
    findings = _lint_plan(plan, mode, mode_findings)
    ref_rows = _legacy_ref_rows(plan)
    return {
        "schema": H3_HANDOFF_SCHEMA,
        "profile": MINIMAX_H3_PROFILE.to_dict(),
        "shot": plan.shot_id,
        "mode": mode,
        "dialect_mode": mode,
        "prompt": prompt,
        "prompt_origin": origin,
        "quality_status": (
            "needs_director_review" if origin == "deterministic_fallback"
            else "author_override_unverified"
        ),
        "keyframes": _legacy_keyframes(plan),
        "references": ref_rows,
        "reference_labels": labels,
        "reference_plan_digest": reference_plan_digest(ref_rows),
        "findings": findings,
        "eligibility": {
            "bundle": "handoff-only",
            "generated_media": "absent",
            "picture_lock": "not_eligible",
        },
    }


class _ProjectView:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def relpath(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def resolve(self, value: str) -> Path:
        resolved = (self.root / value).resolve()
        resolved.relative_to(self.root)
        return resolved

    def load_config(self) -> Any:
        return SimpleNamespace(width=0, height=0)


def build_h3_projection(
    shot: Any,
    bible: dict[str, dict],
    refset: Any,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    root = Path(project_root or Path.cwd())
    plan = build_video_authoring_plan_from_resolved(_ProjectView(root), shot, bible, refset)
    return project_h3_plan(plan)


class MiniMaxH3Profile:
    descriptor = MINIMAX_H3_PROFILE

    def project(self, plan: VideoAuthoringPlan) -> dict[str, Any]:
        return project_h3_plan(plan)

    def lint(
        self, plan: VideoAuthoringPlan, projection: Mapping[str, Any]
    ) -> tuple[dict[str, Any], ...]:
        return tuple(projection.get("findings", ()))

    def render_readme(self, handoff: Mapping[str, Any]) -> str:
        return f"""# External provider handoff

Shot: {handoff['shot']}
Target profile: minimax_h3 (unofficial Manju authoring profile)
Mode: {handoff['mode']}
Handoff ID: {handoff['handoff_id']}

`prompt.txt` is the exact authored/fallback prompt. References and copied
assets are frozen in `refs.json`. This package contains no generated media and
does not claim MiniMax certification, endorsement, or validated H3 output
quality. Follow `RETURN_FILES.md` for the manual ingest/select/QC loop.
"""

    def render_bundle_files(
        self,
        plan: VideoAuthoringPlan,
        projection: Mapping[str, Any],
        handoff: Mapping[str, Any],
    ) -> Mapping[str, str]:
        shot = plan.shot_id
        return {"RETURN_FILES.md": f"""# Returned files

Name returned videos with the shot id first, for example `{shot}_h3_v1.mp4`.
Do not claim the generator from the filename alone.

1. Preview: `manju ingest <returned-file> --shot {shot}`
2. Apply without selecting: `manju ingest <returned-file> --shot {shot} --apply --no-auto-select --handoff <this-bundle>`
3. Run normal QC and inspect the registered take.
4. Select only after human review: `manju select {shot} <take>`
"""}

    def return_filename_examples(self, shot_id: str) -> tuple[str, ...]:
        return (f"{shot_id}_h3_v1.mp4",)

    def animatic_warning(self) -> Mapping[str, Any]:
        return {
            "code": "h3_handoff_before_animatic_approval",
            "level": "warning",
            "message": "handoff exported before the current animatic was human-approved",
        }

    def profile_claims(self) -> Mapping[str, Any]:
        return {
            "minimax_h3": {
                "official_minimax_certification": False,
                "h3_output_quality_validated": False,
            }
        }


__all__ = [
    "H3_HANDOFF_SCHEMA",
    "H3_MODES",
    "MiniMaxH3Profile",
    "build_h3_projection",
    "lint_h3",
    "project_h3_mode",
    "project_h3_plan",
    "reference_plan_digest",
]
