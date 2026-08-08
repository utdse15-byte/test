"""Provider-neutral, offline video-authoring prompt dialect."""

from __future__ import annotations

from typing import Any, Mapping

from .prompt_profiles import PORTABLE_VIDEO_PROFILE
from .video_authoring import VideoAuthoringPlan


def _value(value: Any) -> str:
    if value is None:
        return "Not specified"
    if isinstance(value, str):
        return value if value else "Not specified"
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item) for item in value) if value else "Not specified"
    return str(value)


def _frame_lines(plan: VideoAuthoringPlan, role: str) -> str:
    rows = [row for row in plan.keyframes if row.role == role]
    if not rows:
        return "Not specified"
    return "\n".join(
        f"{row.id}: {row.image or 'Not specified'}"
        + (f"; intent={row.prompt}" if row.prompt else "")
        for row in rows
    )


def _reference_lines(plan: VideoAuthoringPlan) -> str:
    graph = plan.reference_graph
    if not graph.physical:
        return "Not specified"
    physical = {row.id: row for row in graph.physical}
    lines = []
    for binding in graph.bindings:
        asset = physical[binding.physical_id]
        controls = ", ".join(binding.controls) or "Not specified"
        ignored = ", ".join(binding.ignore) or "Not specified"
        subject = binding.subject_scope or "Not specified"
        lines.append(
            f"{asset.id}: source={asset.source_identity}; subject={subject}; "
            f"controls={controls}; do_not_inherit={ignored}"
        )
    return "\n".join(lines)


def _dialogue_and_text(plan: VideoAuthoringPlan) -> str:
    rows = []
    if plan.dialogue.text:
        prefix = f"{plan.dialogue.speaker}: " if plan.dialogue.speaker else ""
        rows.append(prefix + plan.dialogue.text)
    rows.extend(plan.visible_text)
    return "\n".join(rows) if rows else "Not specified"


def _audio(plan: VideoAuthoringPlan) -> str:
    rows = [*plan.audio.cues, *plan.audio.music, *plan.audio.lyrics]
    return "\n".join(rows) if rows else "Not specified"


def _technical(plan: VideoAuthoringPlan) -> str:
    duration = f"{plan.duration_ms} ms" if plan.duration_ms is not None else "Not specified"
    aspect = plan.aspect_ratio or "Not specified"
    return (
        f"Duration: {duration}\nAspect ratio: {aspect}\n"
        "External execution capability: unverified_at_execution"
    )


def build_portable_projection(plan: VideoAuthoringPlan) -> dict[str, Any]:
    if plan.exact_prompt_override is not None:
        prompt = plan.exact_prompt_override
        origin = "prompt_override"
    else:
        fields = plan.intent_sections
        sections = (
            ("SHOT GOAL", "; ".join(v for v in (
                fields.get("action", ""), fields.get("emotion", ""),
                fields.get("must_show", "")) if v)),
            ("VISUAL START", "\n".join(v for v in (
                fields.get("opening", ""), _frame_lines(plan, "start"))
                if v and v != "Not specified")),
            ("VISUAL CHANGE", "; ".join(v for v in (
                fields.get("action", ""), fields.get("performance", ""),
                fields.get("physics", "")) if v)),
            ("VISUAL END", "\n".join(v for v in (
                fields.get("endpoint", ""), _frame_lines(plan, "end"))
                if v and v != "Not specified")),
            ("SUBJECTS AND REFERENCES", _reference_lines(plan)),
            ("CAMERA", fields.get("camera", "")),
            ("ENVIRONMENT AND LIGHT", fields.get("scene", "")),
            ("DIALOGUE / VISIBLE TEXT", _dialogue_and_text(plan)),
            ("AUDIO INTENT", _audio(plan)),
            ("NEGATIVE CONSTRAINTS", fields.get("avoid", "")),
            ("TECHNICAL TARGET", _technical(plan)),
        )
        prompt = "\n\n".join(f"{title}\n{_value(value)}" for title, value in sections)
        origin = "deterministic_fallback"
    if plan.conditioning.start_frames and plan.conditioning.end_frames:
        mode = "start_and_end_frame"
    elif plan.conditioning.start_frames:
        mode = "start_frame"
    elif plan.conditioning.end_frames:
        mode = "end_frame"
    elif plan.conditioning.ordered_references:
        mode = "references"
    else:
        mode = "text"
    return {
        "schema": "manju.video-authoring-projection/v1",
        "profile": PORTABLE_VIDEO_PROFILE.to_dict(),
        "shot": plan.shot_id,
        "mode": mode,
        "dialect_mode": mode,
        "prompt": prompt,
        "prompt_origin": origin,
        "quality_status": (
            "author_override_unverified" if origin == "prompt_override"
            else "needs_director_review"
        ),
        "keyframes": [row.to_dict() for row in plan.keyframes],
        "references": [row.to_dict() for row in plan.reference_graph.bindings],
        "reference_labels": [],
        "reference_plan_digest": plan.reference_graph.digest,
        "findings": [],
        "eligibility": {
            "bundle": "handoff-only",
            "generated_media": "absent",
            "picture_lock": "not_eligible",
        },
    }


class PortableVideoProfile:
    descriptor = PORTABLE_VIDEO_PROFILE

    def project(self, plan: VideoAuthoringPlan) -> dict[str, Any]:
        return build_portable_projection(plan)

    def lint(
        self, plan: VideoAuthoringPlan, projection: Mapping[str, Any]
    ) -> tuple[dict[str, Any], ...]:
        findings: list[dict[str, Any]] = []
        for binding in plan.reference_graph.bindings:
            if binding.blocked_reason:
                findings.append({
                    "code": "video_reference_blocked",
                    "level": "blocker",
                    "message": binding.blocked_reason,
                    "ref": binding.ref,
                })
            elif not binding.is_url and not binding.exists:
                findings.append({
                    "code": "video_reference_missing",
                    "level": "blocker",
                    "message": f"local reference is missing: {binding.ref}",
                    "ref": binding.ref,
                })
        for frame in plan.keyframes:
            if frame.blocked_reason:
                findings.append({
                    "code": "video_keyframe_blocked",
                    "level": "blocker",
                    "message": frame.blocked_reason,
                })
            elif frame.missing or (frame.role in {"start", "end"} and not frame.image):
                findings.append({
                    "code": "video_keyframe_missing",
                    "level": "blocker",
                    "message": f"{frame.role or 'authored'} keyframe image is missing",
                })
        return tuple(findings)

    def render_readme(self, handoff: Mapping[str, Any]) -> str:
        return f"""# Portable video handoff

Shot: {handoff['shot']}
Profile: portable_video
Handoff ID: {handoff['handoff_id']}

This is a provider-neutral manual authoring package. It contains no generated
media and makes no claim about an external tool's duration, resolution,
reference limits, generator identity, or output quality. Use `UPLOAD_ORDER.md`
and `CONSTRAINTS.md` when transferring the prompt and frozen assets.
"""

    def render_bundle_files(
        self,
        plan: VideoAuthoringPlan,
        projection: Mapping[str, Any],
        handoff: Mapping[str, Any],
    ) -> Mapping[str, str]:
        order = [row.id for row in plan.keyframes if row.role == "start"]
        order += [row.id for row in plan.keyframes if row.role == "end"]
        order += list(plan.conditioning.ordered_references)
        upload = "\n".join(f"{index}. {name}" for index, name in enumerate(order, 1))
        if not upload:
            upload = "No reference assets are specified."
        return {
            "UPLOAD_ORDER.md": (
                "# Upload order\n\n" + upload +
                "\n\nUse the start and end frames in their named roles. The remaining "
                "references follow the listed order.\n"
            ),
            "CONSTRAINTS.md": (
                "# Constraints\n\nDo not infer facts that are absent from `prompt.txt`. "
                "Preserve dialogue, lyrics, and visible text in their authored language. "
                "External execution capability is `unverified_at_execution`.\n"
            ),
            "RETURN_FILES.md": self._return_files(plan.shot_id),
        }

    @staticmethod
    def _return_files(shot_id: str) -> str:
        return f"""# Returned files

Name returned videos with the shot id first, for example `{shot_id}_external_v1.mp4`.
Do not claim the generator from the filename alone.

1. Preview: `manju ingest <returned-file> --shot {shot_id}`
2. Apply without selecting: `manju ingest <returned-file> --shot {shot_id} --apply --no-auto-select --handoff <this-bundle>`
3. Run normal QC and select only after human review.
"""

    def return_filename_examples(self, shot_id: str) -> tuple[str, ...]:
        return (f"{shot_id}_external_v1.mp4",)

    def animatic_warning(self) -> Mapping[str, Any]:
        return {
            "code": "handoff_before_animatic_approval",
            "level": "warning",
            "message": "handoff exported before the current animatic was human-approved",
        }

    def profile_claims(self) -> Mapping[str, Any]:
        return {}


__all__ = ["PortableVideoProfile", "build_portable_projection"]
