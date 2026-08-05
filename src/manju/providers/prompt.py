"""Prompt compilation (§8.5).

The template layer fills a shot's picture-bearing fields — plus excerpts of the
Bible entries it references — into a prompt. Where an agent has hand-written a
prompt for a specific model, it is the best prompt compiler there is, so
``generation.prompt_override`` is passed through **verbatim** and this module
does nothing to it (§8.5).

Everything else is deterministic: the same shot + Bible always compile to the
same prompt regardless of dict key order (excerpt keys are sorted; the
``characters`` list keeps its authored order because order is meaningful).
Compiled prompts are archived on each take sidecar / run log so every yuan spent
is reproducible (§8.5).
"""

from __future__ import annotations

from typing import Any

from ..core.intent import director_contract_view, prompt_contract_sections
from ..core.models import Camera, ShotSpec

# -- camera enum -> natural phrase (small fixed mapping; unknowns degrade to the
#    raw value with underscores turned to spaces) --------------------------------
_SHOT_SIZE = {
    "extreme_wide": "extreme wide shot",
    "wide": "wide shot",
    "medium": "medium shot",
    "close_up": "close-up shot",
    "extreme_close_up": "extreme close-up shot",
}
_MOVEMENT = {
    "static": "static camera",
    "slow_push_in": "slow push-in",
    "push_in": "push in",
    "pull_out": "pull out",
    "pan_left": "pan left",
    "pan_right": "pan right",
    "tilt_up": "tilt up",
    "tilt_down": "tilt down",
    "handheld": "handheld camera",
    "tracking": "tracking shot",
    "orbit": "orbiting camera",
    "zoom_in": "zoom in",
    "zoom_out": "zoom out",
}
_ANGLE = {
    "eye_level": "eye-level angle",
    "low_angle": "low angle",
    "high_angle": "high angle",
    "birds_eye": "bird's-eye view",
    "worms_eye": "worm's-eye view",
    "dutch": "dutch angle",
    "over_shoulder": "over-the-shoulder angle",
}

# placeholder names available to a custom template (§8.5)
PLACEHOLDERS = (
    "scene",
    "characters",
    "props",
    "camera",
    "opening",
    "action",
    "emotion",
    "endpoint",
    "performance",
    "physics",
    "must_show",
    "avoid",
    "dialogue",
    "timing",
)


class _Blank(dict):
    """Mapping for ``str.format_map`` where any missing placeholder renders as
    an empty string instead of raising ``KeyError`` (§8.5)."""

    def __missing__(self, key: str) -> str:
        return ""


def _sorted_keys(d: dict) -> list:
    """Deterministic key order for hand-written YAML mappings whose keys may
    MIX types (a bible entry with a stray ``1:`` int key beside strings made
    ``sorted(dict)`` raise TypeError, aborting the whole compile). Numbers
    sort first (numerically), everything else after (by string form); pure
    str-keyed dicts keep exactly the old order."""
    def key(k: Any):
        if isinstance(k, bool):
            return (1, str(k))
        if isinstance(k, (int, float)):
            return (0, k)
        return (1, str(k))

    return sorted(d, key=key)


def _fmt(value: Any) -> str:
    """Render a Bible value to a flat, deterministic string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(s for s in (_fmt(v) for v in value) if s)
    if isinstance(value, dict):
        return ", ".join(
            f"{k}: {_fmt(value[k])}" for k in _sorted_keys(value) if _fmt(value[k])
        )
    return str(value).strip()


def _excerpt(entry: Any, *, skip: tuple[str, ...] = ("locked",)) -> str:
    """Join an entry's ``k: v`` pairs in sorted key order, skipping ``skip``
    keys (``locked`` is lock bookkeeping, not part of the picture, §4.3)."""
    if not isinstance(entry, dict):
        return ""
    parts = []
    for key in _sorted_keys(entry):
        if key in skip:
            continue
        rendered = _fmt(entry[key])
        if rendered:
            parts.append(f"{key}: {rendered}")
    return ", ".join(parts)


def _scene_field(shot: ShotSpec, bible: dict[str, dict]) -> str:
    if not shot.scene:
        return ""
    return _excerpt(bible.get(shot.scene) or {})


def _characters_field(shot: ShotSpec, bible: dict[str, dict]) -> str:
    parts = []
    for cid in shot.characters:  # authored order preserved (meaningful)
        entry = bible.get(cid) or {}
        entry = entry if isinstance(entry, dict) else {}
        name = str(entry.get("name") or cid).strip()
        rest = _excerpt(entry, skip=("locked", "name"))
        parts.append(f"{name} ({rest})" if rest else name)
    return "; ".join(parts)


def _camera_field(camera: Camera) -> str:
    def phrase(mapping: dict[str, str], value: str | None) -> str:
        value = (value or "").strip()
        if not value:
            return ""
        return mapping.get(value, value.replace("_", " "))

    parts = [
        phrase(_SHOT_SIZE, camera.shot_size),
        phrase(_MOVEMENT, camera.movement),
        phrase(_ANGLE, camera.angle),
    ]
    return ", ".join(p for p in parts if p)


def _prefixed(label: str, items: list[Any] | None) -> str:
    vals = [s for s in (_fmt(i) for i in (items or [])) if s]
    return f"{label}: {', '.join(vals)}" if vals else ""


def _fields(shot: ShotSpec, bible: dict[str, dict]) -> dict[str, str]:
    return prompt_contract_sections(shot, bible)


def _default_layout(fields: dict[str, str]) -> str:
    """Clean multi-line layout of the fields, skipping empty sections."""
    lines: list[str] = []
    if fields["scene"]:
        lines.append(f"Scene: {fields['scene']}")
    if fields["characters"]:
        lines.append(f"Characters: {fields['characters']}")
    if fields["props"]:
        lines.append(f"Props: {fields['props']}")
    if fields["opening"]:
        lines.append(f"Opening state: {fields['opening']}")
    if fields["camera"]:
        lines.append(f"Camera: {fields['camera']}")
    if fields["action"]:
        lines.append(f"Action: {fields['action']}")
    if fields["emotion"]:
        lines.append(f"Emotion: {fields['emotion']}")
    if fields["performance"]:
        lines.append(f"Performance: {fields['performance']}")
    if fields["physics"]:
        lines.append(f"Physics: {fields['physics']}")
    if fields["endpoint"]:
        lines.append(f"Endpoint: {fields['endpoint']}")
    if fields["timing"]:
        lines.append(f"Timing: {fields['timing']}")
    if fields["must_show"]:
        lines.append(fields["must_show"])
    if fields["avoid"]:
        lines.append(fields["avoid"])
    if fields["dialogue"]:
        lines.append(f'Dialogue: "{fields["dialogue"]}"')
    return "\n".join(lines)


def compile_prompt(
    shot: ShotSpec, bible: dict[str, dict], template: str | None = None
) -> str:
    """Compile a shot into a generation prompt (§8.5).

    - ``generation.prompt_override`` non-empty → returned VERBATIM (the agent is
      the best prompt compiler; the engine passes it through untouched).
    - ``template`` given → filled via ``str.format_map`` with the placeholders in
      :data:`PLACEHOLDERS`; any missing placeholder renders as an empty string.
    - otherwise → a deterministic multi-line default layout, empty sections
      skipped.
    """
    override = shot.generation.prompt_override
    if isinstance(override, str) and override.strip():
        return override  # verbatim — do not normalize or strip

    fields = _fields(shot, bible)
    if template is not None:
        return template.format_map(_Blank(fields))
    return _default_layout(fields)


# -- additive prompt views (goal 7: prompt workbench) --------------------------
# The build sends exactly ONE prompt per take — :func:`compile_prompt` (the
# "video prompt"), archived on the sidecar as ``compiled_prompt``. The workbench
# also wants the picture-only prompt a still-image model consumes, the negative
# prompt a provider's negative field takes, and a human-facing director's line.
# These are assembled HERE, from the SAME shared field helpers, so the build path
# and the workbench bundle can never disagree — and they are strictly additive:
# nothing above changes, so every existing take/prompt stays byte-identical.


def compile_image_prompt(
    shot: ShotSpec, bible: dict[str, dict], template: str | None = None
) -> str:
    """The still-frame (image) prompt (§8.5).

    The SAME assembly as :func:`compile_prompt` but WITHOUT the temporal fields a
    single frame cannot depict — the timed ``action`` beat and the ``dialogue``.
    What remains is the picture: scene, characters, camera framing, emotion, and
    the must-show / avoid quality rails. An agent's ``prompt_override`` still wins
    verbatim (it is the best prompt compiler, §8.5), exactly as for the video
    prompt; a ``template`` fills the same placeholders.
    """
    override = shot.generation.prompt_override
    if isinstance(override, str) and override.strip():
        return override  # verbatim — do not normalize or strip
    fields = _fields(shot, bible)
    if template is not None:
        return template.format_map(_Blank(fields))
    lines: list[str] = []
    if fields["scene"]:
        lines.append(f"Scene: {fields['scene']}")
    if fields["characters"]:
        lines.append(f"Characters: {fields['characters']}")
    if fields["props"]:
        lines.append(f"Props: {fields['props']}")
    if fields["opening"]:
        lines.append(f"Opening state: {fields['opening']}")
    if fields["camera"]:
        lines.append(f"Camera: {fields['camera']}")
    if fields["emotion"]:
        lines.append(f"Emotion: {fields['emotion']}")
    if fields["must_show"]:
        lines.append(fields["must_show"])
    if fields["avoid"]:
        lines.append(fields["avoid"])
    return "\n".join(lines)


def compile_negative_prompt(shot: ShotSpec) -> str:
    """The negative prompt (§8.5): the shot's ``quality.avoid`` items joined —
    the exact values :func:`compile_prompt` renders under ``avoid:``, label-free
    and ready for a provider's dedicated negative field. Empty string when the
    shot lists nothing to avoid. Deterministic (authored order preserved)."""
    avoid = list(shot.quality.avoid or [])
    if shot.contract is not None:
        avoid.extend(shot.contract.performance.avoid)
        avoid.extend(shot.contract.physics.avoid)
    return ", ".join(s for s in (_fmt(i) for i in avoid) if s)


def compile_director_prompt(shot: ShotSpec, bible: dict[str, dict]) -> str:
    """A concise director's-intent line for the workbench / human read (§8.5).

    The action beat + emotion + camera framing + what the frame must land —
    NOT a model input (so ``prompt_override`` deliberately does not apply here;
    the director's intent is separate from whatever prompt a model receives).
    Reuses the shared field assembly; empty sections are skipped."""
    fields = _fields(shot, bible)
    if shot.contract is not None:
        view = director_contract_view(shot)
        parts: list[str] = []
        for label, value in (
            ("目的 Purpose", view["purpose"]),
            ("观众必须感知 Viewer must perceive", view["viewer_must_perceive"]),
            ("起点 Opening", _fmt(view["opening"])),
            ("动作 Action", fields["action"]),
            ("终点 Endpoint", _fmt(view["endpoint"])),
            ("主要风险 Primary risk", view["risk"]["primary"]),
            ("替代调度 Fallback staging", view["risk"]["fallback_staging"]),
        ):
            if value:
                parts.append(f"{label}: {value}")
        return "\n".join(parts)
    parts: list[str] = []
    if fields["action"]:
        parts.append(f"动作 Action: {fields['action']}")
    if fields["emotion"]:
        parts.append(f"情绪 Emotion: {fields['emotion']}")
    if fields["camera"]:
        parts.append(f"镜头 Camera: {fields['camera']}")
    if fields["must_show"]:
        parts.append(fields["must_show"])
    return "\n".join(parts)
