"""Concurrency quality modes for `manju build` (goal item 14).

A build mode is a single knob a director turns — 质量 / 均衡 / 快速 — that maps to
three engine settings at once:

1. **provider strategy bias** (routing else-selector): ``quality`` prefers your
   ``quality_first`` priority list, ``speed`` orders capable providers
   cheapest-first (free local providers sort first — no network, fastest),
   ``balanced`` keeps today's default resolution. The bias is applied ONLY to
   the routing *else* branch — a shot with an explicit ``generation.provider`` or
   one a routing rule/tier already claimed is NEVER re-routed by the mode
   (build/graph threads it through as ``GenerationRequest.routing_bias``), and it
   only overrides the *neutral* ``fallback`` else, so an opinionated strategy the
   user configured (e.g. ``local_only``) still wins.
2. **retry budget** for retryable cloud failures (rate-limit / timeout): quality
   retries hardest, speed least (threaded as ``GenerationRequest.max_retries``).
3. **generation concurrency**: how many shots' provider calls run at once
   (``max_workers`` in build/graph's generate phase). ``quality`` stays gentle
   (2), ``balanced`` moderate (4), ``speed`` higher (8).

The single most important property (rule 2, byte-identity): when NO mode is
selected — no ``--mode`` flag and no ``build.mode`` in project.yaml —
:func:`resolve_mode` returns ``None`` and :func:`knobs_for` returns ``None``, so
build/graph takes the exact serial, unbiased, default-retry path it always did.
A mode changes behaviour ONLY when a human explicitly opts in.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.models import BUILD_MODE_NAMES


class BuildModeError(ValueError):
    """An unknown ``--mode`` / ``build.mode`` value — surfaced as a clean CLI
    failure, never a traceback."""


@dataclass(frozen=True)
class ModeKnobs:
    """The three settings a mode name expands to. ``else_bias`` is ``None`` for
    balanced (no routing bias); ``retries`` overrides the cloud retry budget;
    ``max_workers`` bounds generation parallelism (1 = serial = today)."""

    name: str
    else_bias: str | None       # routing else-selector bias (None = keep strategy's)
    retries: int                # per-request cloud retry budget for this mode
    max_workers: int            # generation concurrency (shots submitted at once)


# The knob table. quality=1..2 workers, balanced moderate, speed higher (the
# contract's shape). Retries: quality retries hardest, speed least.
BUILD_MODES: dict[str, ModeKnobs] = {
    "quality":  ModeKnobs("quality",  "quality",  3, 2),
    "balanced": ModeKnobs("balanced", None,       2, 4),
    "speed":    ModeKnobs("speed",    "cheapest", 1, 8),
}

assert set(BUILD_MODES) == set(BUILD_MODE_NAMES)  # the model validator agrees


def _config_mode(config) -> str | None:
    """project.yaml ``build.mode`` (or None). Tolerant of an absent build block."""
    build = getattr(config, "build", None)
    return getattr(build, "mode", None) if build is not None else None


def resolve_mode(flag: str | None, config=None) -> str | None:
    """Resolve the active build mode: ``--mode`` flag > project.yaml
    ``build.mode`` > ``None`` (unspecified = today's behaviour).

    ``None`` is a first-class answer meaning "no mode" — the caller then runs the
    byte-identical default path. A non-None but unknown value raises
    :class:`BuildModeError` (a typo must not silently degrade to default)."""
    name = flag if flag else (_config_mode(config) if config is not None else None)
    if name is None:
        return None
    if name not in BUILD_MODES:
        raise BuildModeError(
            f"unknown build mode {name!r}; choose one of {tuple(BUILD_MODES)}"
        )
    return name


def knobs_for(name: str | None) -> ModeKnobs | None:
    """The :class:`ModeKnobs` for a resolved mode name, or ``None`` when no mode
    is active (the byte-identical default path)."""
    return BUILD_MODES.get(name) if name else None


def mode_else_bias(name: str | None) -> str | None:
    """Just the routing else-selector bias for a mode name — the read-only plan
    surfaces (gui/plan, `routing explain`) use this to show which provider a mode
    would pick without running a build. ``None`` when no mode / no bias."""
    knobs = knobs_for(name)
    return knobs.else_bias if knobs else None
