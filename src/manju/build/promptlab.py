"""Prompt Workbench data layer (goal item 7).

``shot_prompt_bundle(project, shot_id)`` gathers everything a director needs to
UNDERSTAND (and trust) what a paid generate call will send for one shot, in one
read-only, JSON-serialisable dict — the exact prompts the build assembles, the
references it resolved, the provider it will route to and why, the pre-flight
cost, and the single-action prompt checks. A later GUI page renders this bundle;
``manju prompt`` prints it.

Every piece is READ from the SAME code paths the build uses, never a fork:

* prompts — :mod:`manju.providers.prompt` (``video_prompt`` is byte-identically
  the ``compile_prompt`` string a cloud/comfyui/local_cmd provider archives on
  its take sidecar as ``compiled_prompt``);
* references — :func:`manju.providers.refs.resolve_refs` (the single resolver, so
  tier lineage matches what the build delivers);
* provider resolution — :mod:`manju.providers.routing` (``explain``/``resolve``),
  the honest label from :mod:`manju.gui.plan`, plus the §8.4 fallback chain;
* cost — ``build.graph._estimate_shot_cost`` / ``_target_duration_ms`` (the same
  estimators ``build --dry-run``, ``gui.plan`` and ``spend`` price from);
* checks — :func:`manju.qc.prompt_checks.check_shot`.

The bundle NEVER mutates the project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.container import Project

__all__ = ["shot_prompt_bundle"]


def _relpath(project: Project, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return project.relpath(path)
    except Exception:
        return str(path)


def _references(project: Project, shot, bible: dict) -> dict[str, Any]:
    """RefSet summary with per-ref tier lineage (goal item 7). Reuses the single
    resolver so paths/kinds/tiers match exactly what the build would deliver."""
    from ..providers.refs import resolve_refs

    refset = resolve_refs(project, shot, bible)
    return {
        "images": [_relpath(project, p) for p in refset.images],
        "videos": [_relpath(project, p) for p in refset.videos],
        "primary_image": _relpath(project, refset.primary_image),
        "primary_image_tier": refset.primary_image_source,
        "has_declared_refs": refset.has_declared_refs(),
        # per-ref lineage: authored value, tier that supplied it, kind, existence
        "items": [
            {
                "ref": it.ref,
                "tier": it.tier,
                "kind": it.kind,
                "path": _relpath(project, it.path),
                "is_url": it.is_url,
                "exists": it.exists,
            }
            for it in refset.items
        ],
        # the resolver's own serialisable lineage block (image/video/primary)
        "lineage": refset.lineage,
    }


def _provider_resolution(project: Project, shot) -> dict[str, Any]:
    """Which provider runs first and WHY (explicit / rule / strategy / fallback),
    the full ordered list, and the §8.4 fallback chain — reusing the routing
    resolver and gui.plan's honest provider label."""
    from ..providers import routing
    from ..providers.registry import fallback_chain

    try:
        chain = fallback_chain(shot)
    except Exception:
        chain = []

    try:
        info = routing.explain(project, shot)
    except routing.RoutingError as exc:
        # A broken routing.yaml is `route explain`'s to surface — here it is an
        # honest, non-fatal note; the §8.4 chain is still the truthful safety net.
        return {
            "chosen": chain[0] if chain else None,
            "why": "fallback",
            "label": (shot.generation.provider or "auto(fallback chain)"),
            "order": list(chain),
            "fallback_chain": chain,
            "routing_error": " ".join(str(exc).split()),
        }

    if info["explicit_provider"]:
        why = "explicit"
    elif info["fired_rule"] is not None:
        why = "rule"
    elif info["routing_file"] and info["else"] != "fallback":
        why = f"strategy:{info['else']}"
    else:
        why = "fallback"

    try:  # reuse gui/plan.py's honest provider label (contract: reuse its helpers)
        from ..gui.plan import _provider_label

        label = _provider_label(project, shot, info["routing_file"],
                                info["explicit_provider"])
    except Exception:
        label = info["chosen"] or (shot.generation.provider or "auto(fallback chain)")

    return {
        "chosen": info["chosen"],
        "why": why,
        "label": label,
        "order": info["order"],
        "fallback_chain": chain,
        "strategy": info["strategy"],
        "sources": info["sources"],
        "explicit_provider": info["explicit_provider"],
        "fired_rule": info["fired_rule"],
        "else": info["else"],
        "skipped": info["skipped"],
    }


def _cost(project: Project, shot) -> dict[str, Any]:
    """Pre-flight per-shot estimate — the SAME estimators build/dry-run, gui.plan
    and spend price from (build.graph._estimate_shot_cost / _target_duration_ms)."""
    from .graph import _estimate_shot_cost, _target_duration_ms

    duration_ms = _target_duration_ms(project, shot, project.load_rules())
    cost, currency = _estimate_shot_cost(shot, duration_ms)
    return {
        "estimated_cost": float(cost),
        "currency": currency,
        "duration_ms": int(duration_ms),
    }


def shot_prompt_bundle(project: Project, shot_id: str) -> dict[str, Any]:
    """Everything the prompt workbench needs for one shot — READ-ONLY.

    Raises :class:`~manju.core.container.ProjectError` when the shot does not
    exist (the caller turns that into a clean CLI failure). The returned dict is
    JSON-serialisable (paths are strings, findings are plain dicts)."""
    from ..core.spec import compute_spec_hash, spec_payload
    from ..providers.prompt import (
        compile_director_prompt,
        compile_image_prompt,
        compile_negative_prompt,
        compile_prompt,
    )
    from ..qc.prompt_checks import check_shot

    shot = project.load_shot(shot_id)  # ProjectError if missing
    bible = project.load_bible()

    cost = _cost(project, shot)

    return {
        "shot": shot_id,
        # (1) ShotSpec snapshot — the canonical spec_payload dict (the exact
        # snapshot a generated take archives), plus its staleness anchor.
        "shot_spec": spec_payload(shot, bible),
        "spec_hash": compute_spec_hash(shot, bible),
        # (2) the prompts, assembled by the build's own code path (prompt.py).
        "image_prompt": compile_image_prompt(shot, bible),
        "video_prompt": compile_prompt(shot, bible),  # == the build's compiled_prompt
        "director_prompt": compile_director_prompt(shot, bible),
        "negative_prompt": compile_negative_prompt(shot),
        # (3) references with per-ref tier lineage.
        "references": _references(project, shot, bible),
        # (4) provider resolution: chosen + why + full fallback order.
        "provider": _provider_resolution(project, shot),
        # (5) estimated cost for this shot.
        "cost": cost,
        # (6) single-action checks (incl. split-shot suggestions).
        "checks": check_shot(project, shot, duration_ms=cost["duration_ms"]),
    }
