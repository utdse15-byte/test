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

__all__ = ["action_plan"]


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or exc.__class__.__name__


def _routing_on(project: Any) -> bool:
    try:
        from ..providers.routing import load_routing

        return load_routing(project) is not None
    except Exception:
        return False


def _provider_label(project: Any, shot: Any, routing_on: bool,
                    explicit: str | None = None) -> str:
    """The provider the shot will actually try first. An explicit override wins;
    else routing's resolved head when a routing.yaml is present; else the static
    fallback label the build plan already uses."""
    if explicit:
        return explicit
    if routing_on:
        try:
            from ..providers.routing import resolve

            chosen = resolve(project, shot).chosen
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
    from ..build.graph import run_build

    target = str(params.get("target") or "final")
    gen = str(params.get("gen") or "missing")
    if target not in ("proxy", "final", "exports", "qc"):
        raise ValueError(f"unknown target: {target}")
    if gen not in ("missing", "auto", "off"):
        raise ValueError(f"unknown gen mode: {gen}")
    result = run_build(project, target=target, gen=gen,
                       regen_stale=bool(params.get("regen_stale")),
                       dry_run=True, force=bool(params.get("force")))
    rows: list[dict[str, Any]] = []
    for it in (result.plan or []):
        kind = it.get("kind") or "video"
        provider = it.get("provider")
        if routing_on and kind != "voice":
            try:
                provider = _provider_label(project, project.load_shot(it.get("shot")),
                                           True, None)
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
    return _envelope("build", rows, [], saved_cost=result.saved_cost, routing=routing_on)


def _redo_plan(project: Any, params: dict[str, Any], routing_on: bool) -> dict[str, Any]:
    from ..build.graph import _estimate_shot_cost, _target_duration_ms

    shot_id = str(params.get("shot") or "")
    if not shot_id:
        raise ValueError("shot is required")
    shot = project.load_shot(shot_id)
    rules = project.load_rules()
    dur = _target_duration_ms(project, shot, rules)
    cost, currency = _estimate_shot_cost(shot, dur)
    explicit = params.get("provider") or None
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
            cost, currency = _estimate_shot_cost(shot, dur)
            prov = _provider_label(project, shot, routing_on,
                                   str(provider) if provider else None)
        except Exception as exc:
            skipped.append({"shot": sid, "reason": _one_line(exc)})
            continue
        rows.append({"shot": sid, "kind": "video",
                     "reason": (state.value if state is not None else "redo"),
                     "provider": prov, "estimated_cost": float(cost), "currency": currency})
    return _envelope("batch-redo", rows, skipped, routing=routing_on)


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
