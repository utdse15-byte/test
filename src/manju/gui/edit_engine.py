"""Native-cut engine helpers for the /edit page (round U).

Engine-side logic behind the two /edit surfaces that need more than a text edit:

  * **Per-boundary transition validity** — the ½-clip cap (REPORTS §1: a
    transition may be at most half the SHORTER adjacent clip). The server's
    override-save gate calls :func:`max_override_duration_ms` so an impossible
    cross-dissolve never reaches the compiler.

  * **Generative handle rebuild (补拍手柄)** — when a boundary's xfade degraded
    for lack of real media handles, re-generate the shot LONGER (clip + 2×half,
    the render's own ``_plan_transitions`` math) through the EXISTING redo path,
    then virtual-trim (``set_inout_take mode=virtual``) back to the original
    content span, leaving the spare head/tail a cross-dissolve consumes. When
    the routed provider cannot be told a duration (its manifest has no duration
    placeholder), there is nothing to extend — the caller renders the honest
    advisory instead of the button.

Purely engine-side: NO GUI code, no new generation machinery — the rebuild
composes the existing ``_plan_redo`` / ``_run_redo`` (append-only, same ledger
+ spend gate as ``manju redo``) with ``set_inout_take``.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "HandleRebuildError",
    "NO_DURATION_ADVISORY",
    "provider_supports_duration",
    "routed_provider",
    "neighbour_durations",
    "max_override_duration_ms",
    "handle_rebuild_proposal",
    "run_handle_rebuild",
]

# The honest advisory shown in the seam popover when 补拍手柄 is impossible.
NO_DURATION_ADVISORY = "该生成来源不支持指定时长,无法补拍手柄"


class HandleRebuildError(ValueError):
    """A 补拍手柄 request could not proceed (no duration control / no take
    produced). Carries a human-readable 中文 reason; nothing was written."""


# =========================================================== duration control


def _template_texts(manifest: Any):
    """Every template string a manifest sends to its backend — the place a
    ``{duration_s}``/``{duration_ms}`` placeholder would live."""

    def walk(v: Any):
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for x in v.values():
                yield from walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from walk(x)

    submit = getattr(manifest, "submit", None)
    if submit is not None:
        yield from walk(getattr(submit, "body_template", {}) or {})
    comfy = getattr(manifest, "comfyui", None)
    if comfy is not None:
        yield from walk(getattr(comfy, "input_map", {}) or {})
    local = getattr(manifest, "local_cmd", None)
    cmd = getattr(local, "command", None) if local is not None else None
    if cmd:
        yield cmd


def provider_supports_duration(name: str | None) -> bool:
    """Can this provider be told what duration to generate?

    A LOCAL / code provider receives ``req.duration_ms`` as a Python argument and
    honours it (kenburns, caption_card and every registered code provider do), so
    a provider with no manifest always can. A config-declared CLOUD provider only
    can when one of its templates references a ``{duration…}`` placeholder
    (``{duration_s}`` / ``{duration_ms}``) — otherwise the backend picks its own
    length and there is nothing to extend, so 补拍手柄 is impossible."""
    if not name:
        return False
    try:
        from ..providers.registry import get_manifest
    except Exception:
        return True
    manifest = get_manifest(name)
    if manifest is None:
        return True  # local/code provider: honours req.duration_ms directly
    return any("{duration" in t for t in _template_texts(manifest))


def routed_provider(project: Any, shot: Any) -> str:
    """The provider that would actually run FIRST for this shot: an explicit
    ``generation.provider`` wins; else routing's resolved head; else the first
    provider on the §8.4 fallback chain."""
    explicit = getattr(shot.generation, "provider", None)
    if explicit:
        return explicit
    try:
        from ..providers.routing import resolve

        chosen = resolve(project, shot).chosen
        if chosen:
            return chosen
    except Exception:
        pass
    try:
        from ..providers.registry import fallback_chain

        chain = fallback_chain(shot)
        if chain:
            return chain[0]
    except Exception:
        pass
    return "caption_card"


# ============================================================ ½-clip validity


def _shot_target_ms(project: Any, shot_id: str, rules: Any) -> int:
    from ..build.graph import _target_duration_ms

    return _target_duration_ms(project, project.load_shot(shot_id), rules)


def neighbour_durations(project: Any, timeline: Any,
                        out_edge_id: str) -> tuple[int, int] | None:
    """``(a_ms, b_ms)`` for the boundary on ``out_edge_id``'s out-edge — the
    outgoing clip and the incoming one. Prefers the COMPILED timeline (real
    durations, packaging cards included); falls back to the shot order + the
    same duration resolver the compiler uses. ``None`` when there is no interior
    boundary here (the true last segment) or it can't be located."""
    if timeline is not None:
        vids = list(timeline.tracks.video)
        for i, vc in enumerate(vids):
            if vc.shot == out_edge_id:
                if i + 1 < len(vids):
                    return int(vc.duration_ms), int(vids[i + 1].duration_ms)
                return None  # last segment → no boundary
    ids = project.shot_ids()
    if out_edge_id in ids:
        j = ids.index(out_edge_id)
        if j + 1 < len(ids):
            rules = project.load_rules()
            return (_shot_target_ms(project, ids[j], rules),
                    _shot_target_ms(project, ids[j + 1], rules))
    return None


def max_override_duration_ms(project: Any, timeline: Any,
                             out_edge_id: str) -> int | None:
    """The largest legal transition duration on this boundary = half the shorter
    neighbour clip (剪映's rule, REPORTS §1). ``None`` when the neighbours can't
    be determined (an inert key — the compiler keeps it, QC advises)."""
    nd = neighbour_durations(project, timeline, out_edge_id)
    if nd is None:
        return None
    return min(nd) // 2


# ============================================================== handle rebuild


def handle_rebuild_proposal(project: Any, shot_id: str, *,
                            transition_ms: int | None = None) -> dict[str, Any]:
    """The pre-generation proposal for 补拍手柄 on ``shot_id``'s out-edge.

    Computes the extended generate duration (clip + 2×half, sized to the
    boundary's transition) and the virtual-trim window that re-centres the
    original content span, plus whether the routed provider can be told a
    duration (else an advisory). No side effects — safe to call on every render
    of the seam popover. The window math is deterministic (does not depend on a
    probe), so it is unit-testable without ffmpeg."""
    shot = project.load_shot(shot_id)  # raises ProjectError on unknown shot
    rules = project.load_rules()
    config = project.load_config()
    fps = int(config.fps or 24)

    from ..build.graph import _target_duration_ms

    clip_ms = _target_duration_ms(project, shot, rules)
    if transition_ms is None:
        ov = rules.transition_overrides.get(shot_id) if rules.transition_overrides else None
        spec = ov if ov is not None else rules.transition_default
        transition_ms = int(getattr(spec, "duration_ms", 300) or 0) if spec else 300
    transition_ms = int(transition_ms)

    from ..media.render import _xfade_half_ms

    half_ms = _xfade_half_ms(transition_ms, fps)
    extended_ms = clip_ms + 2 * half_ms
    in_ms = half_ms
    out_ms = half_ms + clip_ms

    provider = routed_provider(project, shot)
    supported = provider_supports_duration(provider)
    prop: dict[str, Any] = {
        "shot": shot_id,
        "provider": provider,
        "supported": supported,
        "fps": fps,
        "clip_ms": clip_ms,
        "half_ms": half_ms,
        "extended_ms": extended_ms,
        "in_ms": in_ms,
        "out_ms": out_ms,
        "transition_ms": transition_ms,
    }
    if not supported:
        prop["advisory"] = NO_DURATION_ADVISORY
        prop["estimated_cost"] = 0.0
        prop["currency"] = None
        return prop
    from ..build.graph import _estimate_shot_cost

    # #7: price the SAME provider `provider` (above) already resolved and
    # displays — not the untouched shot, which would still price the static
    # fallback head when routing sent this shot elsewhere.
    priced_shot = shot
    if provider and provider != getattr(shot.generation, "provider", None):
        priced_shot = shot.model_copy(deep=True)
        priced_shot.generation.provider = provider
    cost, currency = _estimate_shot_cost(priced_shot, extended_ms)
    prop["estimated_cost"] = float(cost)
    prop["currency"] = currency
    return prop


def run_handle_rebuild(project: Any, shot_id: str, *, transition_ms: int | None = None,
                       provider: str | None = None,
                       actor: str = "human", assume_yes: bool = False,
                       should_cancel=None) -> dict[str, Any]:
    """Execute 补拍手柄: regenerate ``shot_id`` at the extended duration through
    the existing redo path, then virtual-trim back to the centred content span.

    Append-only throughout: the redo mints fresh takes without overturning the
    shot's selection, and the trim mints one more take (the handle-bearing one)
    that the caller can offer to select. Holds the process build lock and passes
    the SAME §8.3 spend gate as ``manju redo`` (``assume_yes`` to proceed).
    Raises :class:`HandleRebuildError` when the provider has no duration control
    or the generation produced nothing to trim.

    ``provider`` (goal 63) is the provider the CALLER's proposal priced — the
    GUI passes back what :func:`handle_rebuild_proposal` showed the human when
    the popover was rendered. The redo is pinned to EXACTLY that provider
    (routing is never silently re-resolved between propose and execute); if
    the freshly-resolved head no longer matches (routing.yaml edited, a
    manifest disabled, …) this refuses with a 已重新报价 message instead of
    running a provider the human never confirmed. ``provider=None`` (an older
    caller) falls back to resolving fresh at execute time, as before."""
    prop = handle_rebuild_proposal(project, shot_id, transition_ms=transition_ms)
    if not prop["supported"]:
        raise HandleRebuildError(NO_DURATION_ADVISORY)

    extended_ms = int(prop["extended_ms"])
    clip_ms = int(prop["clip_ms"])
    half_ms = int(prop["half_ms"])
    pinned_provider = provider or prop["provider"]

    from ..build.graph import _estimate_shot_cost, _plan_redo, _run_redo, spend_gate
    from ..core.events import append_event
    from ..media.repair_ops import set_inout_take
    from ..runtime.buildlock import build_lock

    with build_lock(project.root, actor=actor):
        rules = project.load_rules()
        bible = project.load_bible()
        if provider is not None:
            # #63: refuse rather than silently run a provider the human never
            # saw/confirmed — re-resolve fresh, right before spending, and
            # compare against what the proposal the caller is honoring priced.
            current = routed_provider(project, project.load_shot(shot_id))
            if current != provider:
                raise HandleRebuildError(
                    f"已重新报价:供应商从提案的 {provider} 变为 {current}"
                    "(routing 配置发生变化)— 请重新查看补拍手柄提案后再确认"
                )
        plan = _plan_redo(project, shot_id, candidates=None, provider=pinned_provider,
                          seed=None, from_take=None, rules=rules, bible=bible)
        # the "duration param": drive the provider LONGER by overriding the
        # in-memory shot's duration (never written back — a redo is append-only).
        plan.shot.duration = extended_ms / 1000.0
        plan.cost, plan.currency = _estimate_shot_cost(plan.shot, extended_ms)
        spend_gate(project, plan.cost, plan.currency, assume_yes=assume_yes,
                   hint=f"确认后补拍手柄:{shot_id}(带 assume_yes / --yes)")
        # C51: thread cancel into redo generate (cloud/ComfyUI poll).
        gen_takes = _run_redo(
            project, plan, bible=bible, rules=rules, actor=actor,
            should_cancel=should_cancel)
        if not gen_takes:
            raise HandleRebuildError(f"{shot_id}: 生成失败,没有可裁剪的加长素材")
        gen_take = gen_takes[-1]

        # centre the original content span inside whatever length was produced,
        # so BOTH edges keep spare handle material (the real cross-dissolve fuel).
        real_ms = extended_ms
        try:
            from ..media.probe import probe

            info = project.get_take(shot_id, gen_take)
            probed = probe(info.media_path).duration_ms
            if probed:
                real_ms = int(probed)
        except Exception:
            pass
        keep = max(1, min(clip_ms, real_ms - 2))
        in_ms = max(0, (real_ms - keep) // 2)
        out_ms = in_ms + keep
        from ..media.ffmpeg import MediaCanceled, cancel_scope
        with cancel_scope(should_cancel):
            try:
                trim = set_inout_take(
                    project, shot_id, gen_take, in_ms, out_ms, mode="virtual")
            except MediaCanceled as exc:
                raise HandleRebuildError(f"已取消补拍手柄: {exc}") from exc

        append_event(project.root, actor, "handle_rebuild",
                     {"shot": shot_id, "gen_take": gen_take, "trim_take": trim.name,
                      "extended_ms": extended_ms, "in_ms": in_ms, "out_ms": out_ms,
                      "half_ms": half_ms, "via": "gui"})

    return {"shot": shot_id, "gen_take": gen_take, "trim_take": trim.name,
            "extended_ms": extended_ms, "in_ms": in_ms, "out_ms": out_ms,
            "half_ms": half_ms}
