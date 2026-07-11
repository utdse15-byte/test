"""AI_IDE_19 WP6 — precise tool orchestration (whitelist intent → deterministic).

The FireRed/Inline idea (contract §9), held to Manju's discipline: a natural-
language intent becomes an EXPLICIT edit operation drawn ONLY from a fixed
whitelist, and each operation dispatches onto an EXISTING deterministic executor.
There is NO LLM planner in core (addendum ruling 7) — this module is pure DATA
(the whitelist) plus dispatch; it imports no model and plans nothing.

Every whitelisted op maps 1:1 to a tool Manju already ships (WP0 audit):

    trim/split   → media.repair_ops.set_inout_take   (frame-exact IN/OUT)
    crop         → media.repair_ops.crop_pad_take     (center-crop / pad)
    speed        → media.repair_ops.retime_take       (setpts + atempo)
    gain/ducking → build.mixer.apply_mixer            (bus/shot audio)
    caption      → gui.captions_edit                  (caption source + rules)
    transition   → core.models.TimelineRules          (transition_overrides)
    overlay      → core.models.OverlayClip            (overlay track)
    reorder      → core.container.Project.save_index   (shot order source)

Each op is dry-runnable through the existing impact/estimators; every op is a
LOCAL deterministic edit, so it prices 0 (a repair is local ffmpeg, a mixer edit
is one line of YAML, a transition is a rule). Nothing outside the whitelist can
be resolved — an unknown intent is refused, never improvised.
"""

from __future__ import annotations

from typing import Any

# The FIXED whitelist (contract §9). op → the EXISTING executor it dispatches to.
# ``kind`` groups ops by the executor family the WP0 audit already owns.
TOOL_WHITELIST: dict[str, dict[str, str]] = {
    "trim":       {"executor": "manju.media.repair_ops.set_inout_take", "kind": "repair"},
    "split":      {"executor": "manju.media.repair_ops.set_inout_take", "kind": "repair"},
    "crop":       {"executor": "manju.media.repair_ops.crop_pad_take",  "kind": "repair"},
    "speed":      {"executor": "manju.media.repair_ops.retime_take",    "kind": "repair"},
    "gain":       {"executor": "manju.build.mixer.apply_mixer",         "kind": "mixer"},
    "ducking":    {"executor": "manju.build.mixer.apply_mixer",         "kind": "mixer"},
    "caption":    {"executor": "manju.gui.captions_edit",               "kind": "captions"},
    "transition": {"executor": "manju.core.models.TimelineRules",       "kind": "rules"},
    "overlay":    {"executor": "manju.core.models.OverlayClip",         "kind": "rules"},
    "reorder":    {"executor": "manju.core.container.Project.save_index", "kind": "source"},
}

# a repair op needs a shot; these carry the extra required fields.
_REQUIRED: dict[str, tuple[str, ...]] = {
    "trim": ("shot", "take", "in_ms", "out_ms"),
    "split": ("shot", "take", "at_ms"),
    "crop": ("shot", "take"),
    "speed": ("shot", "take", "factor"),
    "gain": ("shot",),
    "ducking": ("shot",),
    "caption": ("shot",),
    "transition": ("boundary",),
    "overlay": ("kind",),
    "reorder": ("order",),
}


class ToolError(RuntimeError):
    """An intent op is not on the whitelist, or its args are malformed."""


def whitelist_ops() -> tuple[str, ...]:
    return tuple(TOOL_WHITELIST)


def resolve_tool(op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve one whitelisted intent op to its EXISTING executor + a validated
    payload. Refuses (never improvises) anything off the whitelist. This shapes
    the call; the caller runs it through the existing executor (a Director
    proposal action / repair op / rules edit), so no new engine is introduced."""
    args = dict(args or {})
    spec = TOOL_WHITELIST.get(op)
    if spec is None:
        raise ToolError(f"op {op!r} is not on the tool whitelist "
                        f"{sorted(TOOL_WHITELIST)} — refused, never improvised")
    missing = [f for f in _REQUIRED.get(op, ()) if args.get(f) in (None, "")]
    if missing:
        raise ToolError(f"op {op!r} needs {missing} (got {sorted(args)})")
    if op == "speed":
        try:
            if float(args["factor"]) <= 0:
                raise ToolError("speed.factor must be > 0")
        except (TypeError, ValueError):
            raise ToolError("speed.factor must be a number")
    return {"op": op, "executor": spec["executor"], "kind": spec["kind"],
            "payload": args, "deterministic": True}


def dry_run_tool(op: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Impact of one op WITHOUT executing it. Every whitelisted op is a local,
    deterministic edit → priced 0 (honest). Reuses :func:`resolve_tool` for
    validation so a dry-run refuses exactly what a real run would."""
    resolved = resolve_tool(op, args)
    return {
        "op": op,
        "dry_run": True,
        "priced": 0.0,
        "executor": resolved["executor"],
        "kind": resolved["kind"],
        "impact": {"family": resolved["kind"], "local": True,
                   "note": f"{op} dispatches to {resolved['executor']} (local)"},
    }
