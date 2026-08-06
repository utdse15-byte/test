"""Pre-generation PLAN computation for the GUI (goal item 5, invariant §4.4).

Every priced or generative action — build / redo / voice, and their batch
forms — is EXPLAINED before it runs: the same estimators the CLI's dry-run uses
produce a per-shot table (shot, why, the provider that will run, est cost), a
total, and (for build) the cache savings. The page renders this as the plan
modal and takes an explicit confirm; nothing here mutates.

The provider column is honest about what will actually run: when a routing.yaml
is present it shows ``routing.resolve(...).chosen`` — the exact resolution the
generation path performs — instead of the static fallback label. This is a
read-only sibling of ``build --dry-run`` extended to the redo/voice/batch
actions the GUI also offers, so the two never disagree on the numbers.
"""

from __future__ import annotations

from typing import Any

__all__ = ["action_plan", "routing_explain"]


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or exc.__class__.__name__


def _routing_on(project: Any) -> bool:
    try:
        from ..providers.routing import load_routing

        return load_routing(project) is not None
    except Exception:
        return False


def _provider_label(project: Any, shot: Any, routing_on: bool,
                    explicit: str | None = None, *, else_bias: str | None = None) -> str:
    """The provider the shot will actually try first. An explicit override wins;
    else routing's resolved head when a routing.yaml is present OR a build mode
    biases the else selector (``else_bias``, goal 14 — the plan stays honest
    about which provider a mode would pick); else the static fallback label."""
    if explicit:
        return explicit
    if routing_on or else_bias:
        try:
            from ..providers.routing import resolve

            chosen = resolve(project, shot, else_bias=else_bias).chosen
            if chosen:
                return chosen
        except Exception:
            pass
    return getattr(shot.generation, "provider", None) or "auto(fallback chain)"


def _voice_pricing(explicit: Any) -> tuple[str, float, str | None]:
    """(provider_id, per_call, currency) for a voice synth — the same manifest
    price ``_plan_voice`` gates on. No TTS configured → a free local/manual row."""
    try:
        from ..providers.tts import tts_providers

        provs = tts_providers()
    except Exception:
        provs = {}
    if not provs:
        return (str(explicit) if explicit else "local/manual", 0.0, None)
    pid = str(explicit) if (explicit and str(explicit) in provs) else sorted(provs)[0]
    manifest = provs[pid]
    return (pid, float(manifest.cost.per_call or 0.0), manifest.cost.currency)


def _envelope(action: str, rows: list[dict[str, Any]], skipped: list[dict[str, Any]],
              *, saved_cost: float = 0.0, routing: bool = False) -> dict[str, Any]:
    est = round(sum(float(r.get("estimated_cost") or 0) for r in rows), 6)
    currency = next((r.get("currency") for r in rows if r.get("currency")), None)
    return {
        "action": action,
        "rows": rows,
        "skipped": skipped,
        "estimated_cost": est,
        "saved_cost": round(float(saved_cost or 0), 6),
        "currency": currency,
        "zero_cost": est <= 0,
        "routing": routing,
    }


# --------------------------------------------------------------------- actions


def action_plan(project: Any, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Compute the normalized plan envelope for one generative action."""
    action = str(action or "")
    routing_on = _routing_on(project)
    if action == "build":
        return _build_plan(project, params, routing_on)
    if action == "redo":
        return _redo_plan(project, params, routing_on)
    if action == "voice":
        return _voice_plan(project, params, routing_on)
    if action == "batch-redo":
        return _batch_redo_plan(project, params, routing_on)
    if action == "batch-voice":
        return _batch_voice_plan(project, params, routing_on)
    raise ValueError(f"unknown plan action: {action}")


def _build_plan(project: Any, params: dict[str, Any], routing_on: bool) -> dict[str, Any]:
    from ..build.graph import GEN_MODES, run_build  # round W (issue #3): shared enum

    target = str(params.get("target") or "final")
    gen = str(params.get("gen") or "missing")
    if target not in ("proxy", "final", "exports", "qc", "audition", "animatic"):
        raise ValueError(f"unknown target: {target}")
    if gen not in GEN_MODES:
        raise ValueError(f"unknown gen mode: {gen}")
    from ..build.modes import mode_else_bias

    mode = params.get("mode") or None
    else_bias = mode_else_bias(mode)  # None when no mode / no bias
    # WP5: thread the same build knobs CLI uses (lang / include_unindexed)
    # so dry-run --json and /api/plan never disagree with run_build.
    lang = params.get("lang") or None
    include_unindexed = bool(params.get("include_unindexed"))
    result = run_build(
        project, target=target, gen=gen,
        regen_stale=bool(params.get("regen_stale")),
        dry_run=True, force=bool(params.get("force")), mode=mode,
        include_unindexed=include_unindexed, lang=lang,
    )
    rows: list[dict[str, Any]] = []
    for it in (result.plan or []):
        kind = it.get("kind") or "video"
        provider = it.get("provider")
        if (routing_on or else_bias) and kind != "voice":
            try:
                provider = _provider_label(project, project.load_shot(it.get("shot")),
                                           routing_on, None, else_bias=else_bias)
            except Exception:
                pass
        rows.append({
            "shot": it.get("shot"),
            "kind": kind,
            "reason": it.get("reason") or "",
            "provider": provider or "",
            "estimated_cost": float(it.get("estimated_cost") or 0),
            "currency": it.get("currency"),
        })
    env = _envelope("build", rows, [], saved_cost=result.saved_cost, routing=routing_on)
    env["readiness"] = result.readiness
    # WP5: cache-reuse visibility — final/proxy verdict from explain
    try:
        from ..build.explain import explain as _explain

        exp = _explain(project)
        env["renders"] = {
            t: (exp.get("renders") or {}).get(t, {}).get("verdict")
            for t in ("final", "proxy")
        }
    except Exception:
        env["renders"] = {}
    if any(r.get("kind") == "voice" for r in rows):
        env["note"] = "voice per_call only — speech duration unknown pre-synthesis"
    # WP7: consistency preflight — advisory warnings per row (never blocks)
    try:
        env = _attach_consistency(project, env)
    except Exception:
        pass
    return env


def _attach_consistency(project: Any, env: dict[str, Any]) -> dict[str, Any]:
    """WP7: per-row consistency warnings (missing refs). Advisory only."""
    from ..providers.refs import resolve_refs

    for row in env.get("rows") or []:
        if row.get("kind") == "voice":
            continue
        sid = row.get("shot")
        if not sid:
            continue
        warnings: list[str] = []
        try:
            shot = project.load_shot(sid)
            bible = project.load_bible()
            refset = resolve_refs(project, shot, bible)
            # Characters declared without any ref_image
            for cid in shot.characters or []:
                entry = bible.get(cid) or {}
                has_ref = bool(
                    entry.get("ref_image") or entry.get("ref_images")
                    or entry.get("ref_video") or entry.get("ref_videos")
                )
                if not has_ref:
                    warnings.append(
                        f"角色 {cid} 无参考图(ref_image) — 生成前建议 manju refs assign"
                    )
            if hasattr(refset, "has_declared_refs") and not refset.has_declared_refs():
                if shot.characters:
                    # already covered per character above
                    pass
        except Exception:
            pass
        row["consistency"] = warnings
    return env


def _priced_shot_for_row(project: Any, shot: Any, explicit: str | None, *,
                         else_bias: str | None = None):
    """An in-memory shot copy with ``generation.provider`` pinned to the SAME
    head that will be shown as the row's provider label (#7): an explicit
    override wins outright; else routing's resolved head when active; else the
    untouched shot (static fallback head — byte-identical default). Returns
    the original ``shot`` when nothing needs to change (no needless copy).

    This is the fix for the review's exact failure mode: ``_redo_plan`` /
    ``_batch_redo_plan`` showed the routed provider via ``_provider_label``
    but priced the UNTOUCHED shot — routing.yaml could send a shot to
    provider B while the estimate still quoted provider A's price."""
    if explicit:
        if str(explicit) == getattr(shot.generation, "provider", None):
            return shot
        priced = shot.model_copy(deep=True)
        priced.generation.provider = str(explicit)
        return priced
    from ..build.graph import _routed_shot_for_pricing

    return _routed_shot_for_pricing(project, shot, else_bias=else_bias)


def _redo_plan(project: Any, params: dict[str, Any], routing_on: bool) -> dict[str, Any]:
    from ..build.graph import _estimate_shot_cost, _target_duration_ms

    shot_id = str(params.get("shot") or "")
    if not shot_id:
        raise ValueError("shot is required")
    shot = project.load_shot(shot_id)
    rules = project.load_rules()
    dur = _target_duration_ms(project, shot, rules)
    explicit = params.get("provider") or None
    priced_shot = _priced_shot_for_row(project, shot, str(explicit) if explicit else None)
    cost, currency = _estimate_shot_cost(priced_shot, dur)
    rows = [{
        "shot": shot_id, "kind": "video", "reason": "redo (regenerate take)",
        "provider": _provider_label(project, shot, routing_on,
                                    str(explicit) if explicit else None),
        "estimated_cost": float(cost), "currency": currency,
    }]
    return _envelope("redo", rows, [], routing=routing_on)


def _voice_plan(project: Any, params: dict[str, Any], routing_on: bool) -> dict[str, Any]:
    shot_id = str(params.get("shot") or "")
    if not shot_id:
        raise ValueError("shot is required")
    shot = project.load_shot(shot_id)
    text = (shot.dialogue.text or "") if shot.dialogue else ""
    if not str(text).strip():
        raise ValueError(f"{shot_id} has no dialogue.text to voice")
    pid, per_call, currency = _voice_pricing(params.get("provider"))
    rows = [{
        "shot": shot_id, "kind": "voice", "reason": "voice synth (配音)",
        "provider": pid, "estimated_cost": float(per_call), "currency": currency,
    }]
    return _envelope("voice", rows, [], routing=routing_on)


def _batch_redo_plan(project: Any, params: dict[str, Any], routing_on: bool) -> dict[str, Any]:
    from ..build.graph import _estimate_shot_cost, _lock_collisions, _target_duration_ms
    from ..build.stale import ShotState, evaluate_all

    shots = [s for s in (params.get("shots") or []) if isinstance(s, str)]
    provider = params.get("provider") or None
    seed = params.get("seed")
    candidates = params.get("candidates")
    try:
        states = {st.shot_id: st.state for st in evaluate_all(project)}
    except Exception:
        states = {}
    rules = project.load_rules()
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for sid in shots:
        state = states.get(sid)
        if state is None and sid not in project.shot_ids():
            skipped.append({"shot": sid, "reason": "no such shot in this project"})
            continue
        if state == ShotState.MANUAL:
            skipped.append({"shot": sid,
                            "reason": "manual import — a hand-placed take is never batch-regenerated (§4.3)"})
            continue
        collisions = _lock_collisions(
            project, sid, provider=(str(provider) if provider else None),
            seed=(int(seed) if seed is not None else None),
            candidates=(int(candidates) if candidates else None))
        if collisions:
            skipped.append({"shot": sid,
                            "reason": f"locked (§5): {', '.join(collisions)} sealed — an override would overturn it"})
            continue
        try:
            shot = project.load_shot(sid)
            dur = _target_duration_ms(project, shot, rules)
            priced_shot = _priced_shot_for_row(
                project, shot, str(provider) if provider else None)
            cost, currency = _estimate_shot_cost(priced_shot, dur)
            prov = _provider_label(project, shot, routing_on,
                                   str(provider) if provider else None)
        except Exception as exc:
            skipped.append({"shot": sid, "reason": _one_line(exc)})
            continue
        rows.append({"shot": sid, "kind": "video",
                     "reason": (state.value if state is not None else "redo"),
                     "provider": prov, "estimated_cost": float(cost), "currency": currency})
    return _envelope("batch-redo", rows, skipped, routing=routing_on)


# -------------------------------------------------- routing explain (goal 15)


def _why(project: Any, shot: Any, res: Any, config: Any) -> tuple[str, str]:
    """(category, 中文 detail) for how a shot got its head provider. Shared by
    the CLI ``routing explain`` and any GUI routing view, so the two can never
    disagree on the reason. Categories: explicit / rule / tier / fallback."""
    explicit = getattr(shot.generation, "provider", None)
    if explicit and res.chosen == explicit:
        return ("explicit", f"钦定供应商 {explicit}(总是优先)")
    if res.fired_rule is not None:
        fr = res.fired_rule
        return ("rule", f"规则 #{fr['index']} 命中 {fr['match']} → {fr['use']}")
    if getattr(res, "fired_tier", None) is not None:
        ft = res.fired_tier
        desc = ft.get("description") or ""
        return ("tier", f"分级 {ft['name']}" + (f":{desc}" if desc else ""))
    return ("fallback", f"兜底链(§8.4);else={res.else_selector}")


def routing_explain(project: Any, shot_ids: list[str] | None = None, *,
                    mode: str | None = None) -> dict[str, Any]:
    """Per-shot routing decision + cost, for ``manju routing explain [<shot>]``
    (goal 15) and the GUI. For each shot: the chosen head provider and WHY
    (explicit / rule / tier / fallback), the full fallback order AFTER the head,
    and the estimated cost via the SAME estimators build's dry-run and gui/plan
    use — so the CLI, the GUI and the plan modal can never disagree.

    ``mode`` (goal 14) previews a build mode's routing bias without running a
    build; ``None`` reflects the plain active strategy."""
    from ..build.graph import _estimate_shot_cost, _target_duration_ms
    from ..build.modes import mode_else_bias
    from ..providers.routing import load_routing, resolve

    config = load_routing(project)  # may be None → §8.4 default (resolve handles it)
    else_bias = mode_else_bias(mode)
    rules = project.load_rules()
    ids = list(shot_ids) if shot_ids is not None else project.shot_ids()
    rows: list[dict[str, Any]] = []
    for sid in ids:
        shot = project.load_shot(sid)
        res = resolve(project, shot, config, else_bias=else_bias)
        why, why_detail = _why(project, shot, res, config)
        dur = _target_duration_ms(project, shot, rules)
        # #7: price the SAME head `res.chosen` reports — reuse this ALREADY
        # resolved Resolution rather than re-resolving through
        # _routed_shot_for_pricing, so "chosen" and "estimated_cost" can never
        # disagree.
        priced_shot = shot
        if res.chosen and res.chosen != getattr(shot.generation, "provider", None):
            priced_shot = shot.model_copy(deep=True)
            priced_shot.generation.provider = res.chosen
        cost, currency = _estimate_shot_cost(priced_shot, dur)
        rows.append({
            "shot": sid,
            "tier": getattr(shot, "tier", None),
            "chosen": res.chosen,
            "why": why,
            "why_detail": why_detail,
            "fallback_order": res.order[1:],
            "estimated_cost": float(cost),
            "currency": currency,
        })
    return {
        "mode": mode,
        "routing": config is not None,
        "sources": list(config.sources) if config is not None else [],
        "rows": rows,
    }


def _batch_voice_plan(project: Any, params: dict[str, Any], routing_on: bool) -> dict[str, Any]:
    shots = [s for s in (params.get("shots") or []) if isinstance(s, str)]
    pid, per_call, currency = _voice_pricing(params.get("provider"))
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for sid in shots:
        try:
            shot = project.load_shot(sid)
        except Exception as exc:
            skipped.append({"shot": sid, "reason": _one_line(exc)})
            continue
        text = (shot.dialogue.text or "") if shot.dialogue else ""
        if not str(text).strip():
            skipped.append({"shot": sid, "reason": "no dialogue.text to voice"})
            continue
        rows.append({"shot": sid, "kind": "voice", "reason": "voice synth (配音)",
                     "provider": pid, "estimated_cost": float(per_call), "currency": currency})
    return _envelope("batch-voice", rows, skipped, routing=routing_on)
