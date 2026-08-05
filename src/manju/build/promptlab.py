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


def _references(project: Project, refset) -> dict[str, Any]:
    """RefSet summary with per-ref tier lineage (goal item 7). The caller
    resolves the RefSet ONCE (the single resolver) and shares it with the
    production checks, so paths/kinds/tiers/transfer fields match exactly what
    the build would deliver."""
    return {
        "images": [_relpath(project, p) for p in refset.images],
        "videos": [_relpath(project, p) for p in refset.videos],
        "primary_image": _relpath(project, refset.primary_image),
        "primary_image_tier": refset.primary_image_source,
        "has_declared_refs": refset.has_declared_refs(),
        # per-ref lineage: authored value, tier that supplied it, kind,
        # existence — plus (08_10_12C §7.4, additive) the declared transfer.
        "items": [
            {
                "ref": it.ref,
                "tier": it.tier,
                "kind": it.kind,
                "path": _relpath(project, it.path),
                "is_url": it.is_url,
                "exists": it.exists,
                "controls": list(it.controls),
                "ignore": list(it.ignore),
                "subject_ref": it.subject_ref,
                "declared_transfer": it.declared_transfer,
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
    from ..core.hashing import hash_value
    from ..core.spec import SPEC_VERSION, compute_spec_hash, spec_payload
    from ..providers.prompt import (
        compile_director_prompt,
        compile_image_prompt,
        compile_negative_prompt,
        compile_prompt,
    )
    from ..providers.refs import resolve_refs
    from ..qc.prompt_checks import (
        check_shot,
        production_checks,
        surface_profile_digest,
        surface_profile_for,
    )

    shot = project.load_shot(shot_id)  # ProjectError if missing
    bible = project.load_bible()

    cost = _cost(project, shot)
    refset = resolve_refs(project, shot, bible)  # ONE resolve, shared below
    video_prompt = compile_prompt(shot, bible)
    image_prompt = compile_image_prompt(shot, bible)
    director_prompt = compile_director_prompt(shot, bible)
    negative_prompt = compile_negative_prompt(shot)

    # 08_10_12C WP2 — additive: the deterministic production checks and the
    # compiler trace. Everything derives from THIS bundle's own inputs (the
    # same code paths the build uses); nothing under reports/ is ever read.
    prod_checks = production_checks(
        project, shot, duration_ms=cost["duration_ms"],
        video_prompt=video_prompt, refset=refset)
    profile, freshness = surface_profile_for(shot)
    try:
        from ..qc.production import continuation_view

        continuation = continuation_view(project, shot_id)
    except Exception:
        continuation = None

    return {
        "shot": shot_id,
        # (1) ShotSpec snapshot — the canonical spec_payload dict (the exact
        # snapshot a generated take archives), plus its staleness anchor.
        "shot_spec": spec_payload(
            shot, bible, version=SPEC_VERSION, project_root=project.root
        ),
        "spec_hash": compute_spec_hash(
            shot, bible, version=SPEC_VERSION, project_root=project.root
        ),
        # (2) the prompts, assembled by the build's own code path (prompt.py).
        "image_prompt": image_prompt,
        "video_prompt": video_prompt,  # == the build's compiled_prompt
        "director_prompt": director_prompt,
        "negative_prompt": negative_prompt,
        # (3) references with per-ref tier lineage.
        "references": _references(project, refset),
        # (4) provider resolution: chosen + why + full fallback order.
        "provider": _provider_resolution(project, shot),
        # (5) estimated cost for this shot.
        "cost": cost,
        # (6) single-action checks (incl. split-shot suggestions).
        "checks": check_shot(project, shot, duration_ms=cost["duration_ms"]),
        # (7) 08_10_12C production checks — proposal-only, never auto-applied.
        "production_checks": prod_checks,
        # (8) the compiler trace: which source revision + prompt bundle these
        # projections came from, which surface-profile data was assumed (and
        # how fresh it is), and — when the shot declares continuity.prev —
        # the continuation source binding (Skill input, never a build input).
        "compiler_trace": {
            "source_revision": compute_spec_hash(
                shot, bible, version=SPEC_VERSION, project_root=project.root
            ),
            "prompt_bundle_digest": hash_value({
                "image": image_prompt, "video": video_prompt,
                "director": director_prompt, "negative": negative_prompt,
            }),
            "surface_profile": {
                "id": profile["id"],
                "digest": surface_profile_digest(profile),
                "evidence_date": profile["evidence_date"],
                "freshness": freshness,
            },
            "continuation_source": continuation,
        },
    }
