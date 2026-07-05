"""The build graph (§0, §11): `manju build` = one command from sources to film.

check → generate missing (never overturn choices, §4.3) → compile timeline →
export captions → render → QC → export drafts. Every phase is skippable via
target/gen flags; dry-run prints the plan (with cost estimate) and touches
nothing. Failures degrade instead of aborting the whole film where the design
allows it (§8.4); hard integrity problems (check errors, lock violations)
always abort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.check import run_check
from ..core.container import Project
from ..core.events import append_event
from ..core.hashing import MANUAL_HASH
from ..timeline.compiler import CompileError, build_timeline
from .stale import ShotBuildStatus, ShotState, evaluate_all


class BuildError(RuntimeError):
    pass


@dataclass
class BuildResult:
    ok: bool = True
    plan: list[dict[str, Any]] = field(default_factory=list)
    generated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    timeline_path: str | None = None
    render_path: str | None = None
    captions: dict[str, str] = field(default_factory=dict)
    exports: dict[str, str] = field(default_factory=dict)
    qc_ok: bool | None = None
    qc_reports: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    estimated_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _target_duration_ms(project: Project, shot, rules) -> int:
    """Duration a provider should aim for — mirrors the compiler's rules
    minus the (not yet existing) voice."""
    if shot.duration != "auto":
        return max(1, int(round(float(shot.duration) * 1000)))
    return rules.timing.default_shot_ms


def _plan_generation(project: Project, statuses: list[ShotBuildStatus], *,
                     gen: str, regen_stale: bool) -> list[dict[str, Any]]:
    rules = project.load_rules()
    plan: list[dict[str, Any]] = []
    for st in statuses:
        needs = st.state == ShotState.MISSING or (regen_stale and st.state == ShotState.STALE)
        if not needs or gen == "off":
            continue
        shot = project.load_shot(st.shot_id)
        if shot.generation.strategy == "manual":
            continue  # this shot is explicitly waiting for a human import
        plan.append(
            {
                "shot": st.shot_id,
                "reason": st.state.value,
                "candidates": shot.generation.candidates,
                "duration_ms": _target_duration_ms(project, shot, rules),
                "provider": shot.generation.provider or "auto(fallback chain)",
                "estimated_cost": 0.0,  # local providers are free; cloud adapters (M3) price here
            }
        )
    return plan


def run_build(
    project: Project,
    *,
    target: str = "final",  # proxy | final | exports | qc
    gen: str = "missing",  # missing | auto | off
    regen_stale: bool = False,
    dry_run: bool = False,
    actor: str = "engine",
) -> BuildResult:
    result = BuildResult()

    # ---- 0. check: the hard gate (locks included, §5)
    report = run_check(project)
    if not report.ok:
        result.ok = False
        result.errors = [f"check failed: {e}" for e in report.errors]
        return result
    result.warnings.extend(report.warnings)

    statuses = evaluate_all(project)
    result.stale = [s.shot_id for s in statuses if s.state == ShotState.STALE]
    if not statuses:
        result.ok = False
        result.errors.append(
            "no shots found — the creative stage (brief → script → shots) is agent/human "
            "work; the engine never invents it (§2)"
        )
        return result

    # ---- 1. generation plan
    result.plan = _plan_generation(project, statuses, gen=gen, regen_stale=regen_stale)
    result.estimated_cost = sum(p["estimated_cost"] for p in result.plan)

    config = project.load_config()
    budget = config.budget.limit
    if budget is not None and result.estimated_cost > budget:
        result.ok = False
        result.errors.append(
            f"estimated cost {result.estimated_cost} exceeds budget {budget} — waiting_user (§8.3)"
        )
        return result

    if dry_run:
        result.timeline_path = str(project.timeline_path)
        return result

    # ---- 2. generate (fill gaps only; stale/manual are respected, §4.3)
    if result.plan:
        from ..providers.base import GenerationRequest, NeedsHumanInput, ProviderFailure
        from ..providers.registry import fallback_chain, generate_with_fallback

        bible = project.load_bible()
        for item in result.plan:
            shot = project.load_shot(item["shot"])
            st = next(s for s in statuses if s.shot_id == shot.id)
            req = GenerationRequest(
                project=project,
                shot=shot,
                bible=bible,
                spec_hash=st.spec_hash,
                duration_ms=item["duration_ms"],
                candidates=item["candidates"],
                params=dict(shot.generation.params),
            )
            try:
                takes = generate_with_fallback(req, fallback_chain(shot))
            except (ProviderFailure, NeedsHumanInput) as exc:
                result.warnings.append(f"{shot.id}: generation failed — {exc}")
                continue
            result.generated.extend(f"{shot.id}/{t.name}" for t in takes)
            append_event(project.root, actor, "generate",
                         {"shot": shot.id, "takes": [t.name for t in takes]})

    # ---- 2b. auto-select where no human decision exists yet (filling a gap
    # is allowed; overturning a selection never is)
    for st in evaluate_all(project):
        if st.state == ShotState.NEEDS_SELECTION:
            takes = project.takes(st.shot_id)
            usable = [t for t in takes if t.media_path is not None]
            if usable:
                choice = usable[-1].name
                project.update_shot_raw(
                    st.shot_id,
                    lambda d, c=choice: d.setdefault("status", {}).__setitem__("selected_take", c),
                )
                result.warnings.append(
                    f"{st.shot_id}: auto-selected {choice} (no human selection existed)"
                )
                append_event(project.root, actor, "auto_select",
                             {"shot": st.shot_id, "take": choice})

    # ---- 3. compile timeline (pure function, §6)
    from ..media.probe import probe_duration_ms

    try:
        timeline, tl_path, overwrote = build_timeline(project, probe_duration_ms)
    except CompileError as exc:
        result.ok = False
        result.errors.append(str(exc))
        return result
    result.timeline_path = project.relpath(tl_path)
    if not overwrote:
        result.warnings.append(
            "rules.mode=manual: timeline.json is human truth; wrote timeline.generated.json (§6)"
        )
        timeline = project.load_timeline() or timeline  # render what the human made

    # ---- 4. captions
    rules = project.load_rules()
    ass_path = None
    if rules.captions.enabled and timeline.tracks.captions:
        from ..exporters.srt_ass import export_captions

        paths = export_captions(project, timeline)
        result.captions = {k: project.relpath(v) for k, v in paths.items()}
        ass_path = paths.get("ass")

    # ---- 5. render
    if target in ("proxy", "final"):
        from ..media.render import render_timeline

        out = render_timeline(project, timeline, target=target, ass_file=ass_path)
        result.render_path = project.relpath(out)
        append_event(project.root, actor, "render", {"target": target, "output": result.render_path})

    # ---- 6. QC (§9)
    if target in ("proxy", "final", "qc"):
        from ..qc.checks import run_qc
        from ..qc.report import write_reports

        qc = run_qc(project, timeline)
        result.qc_ok = qc.ok
        result.qc_reports = {k: project.relpath(v) for k, v in write_reports(project, qc).items()}

    # ---- 7. draft exports
    if target in ("final", "exports"):
        profiles = set(config.export_profiles)
        if "otio" in profiles:
            from ..exporters.otio import export_otio

            result.exports["otio"] = project.relpath(export_otio(project, timeline))
        if "jianying" in profiles:
            from ..exporters.jianying import export_jianying

            result.exports["jianying"] = project.relpath(export_jianying(project, timeline))

    append_event(project.root, actor, "build",
                 {"target": target, "ok": result.ok, "render": result.render_path})
    return result


def redo_shot(project: Project, shot_id: str, *, candidates: int | None = None,
              provider: str | None = None, seed: int | None = None,
              actor: str = "engine") -> list[str]:
    """`manju redo S002` — force new takes (append-only), regardless of
    staleness. Selection is only touched when the shot had none."""
    from ..providers.base import GenerationRequest
    from ..providers.registry import fallback_chain, generate_with_fallback

    shot = project.load_shot(shot_id)
    bible = project.load_bible()
    from ..core.spec import compute_spec_hash

    rules = project.load_rules()
    params = dict(shot.generation.params)
    if seed is not None:
        params["seed"] = seed
    if provider:
        shot.generation.provider = provider
    req = GenerationRequest(
        project=project,
        shot=shot,
        bible=bible,
        spec_hash=compute_spec_hash(shot, bible),
        duration_ms=_target_duration_ms(project, shot, rules),
        candidates=candidates or shot.generation.candidates,
        params=params,
    )
    takes = generate_with_fallback(req, fallback_chain(shot))
    if not shot.status.selected_take and takes:
        choice = takes[-1].name
        project.update_shot_raw(
            shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", choice)
        )
    append_event(project.root, actor, "redo", {"shot": shot_id, "takes": [t.name for t in takes]})
    return [t.name for t in takes]
