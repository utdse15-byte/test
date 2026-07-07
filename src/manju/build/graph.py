"""The build graph (§0, §11): `manju build` = one command from sources to film.

check → generate missing (never overturn choices, §4.3) → compile timeline →
export captions → render → QC → export drafts. Every phase is skippable via
target/gen flags; dry-run prints the plan (with cost estimate) and touches
nothing. Failures degrade instead of aborting the whole film where the design
allows it (§8.4); hard integrity problems (check errors, lock violations)
always abort.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..core.check import run_check
from ..core.container import Project
from ..core.events import append_event
from ..timeline.compiler import CompileError, build_timeline
from .stale import ShotBuildStatus, ShotState, evaluate_all


class BuildError(RuntimeError):
    pass


class WaitingUser(RuntimeError):
    """§8.3 ask_before gate for commands WITHOUT a result envelope
    (redo/voice): raised instead of spending; carries the estimate so the
    caller can relay it to the human."""

    def __init__(self, message: str, estimated_cost: float, currency: str | None):
        super().__init__(message)
        self.estimated_cost = estimated_cost
        self.currency = currency


def spend_gate(project: Project, estimated_cost: float, currency: str | None,
               *, assume_yes: bool, hint: str) -> None:
    """Raise :class:`WaitingUser` when a paid plan lacks an explicit yes (§8.3).

    The engine-side backstop for the envelope-less commands (redo/voice): the
    design made the agent's discipline the first gate (SKILL.md §5); this makes
    the engine default to STOP. Zero-cost plans, ``assume_yes``, and projects
    that removed ``expensive_generation`` from ask_before all pass silently."""
    if estimated_cost <= 0 or assume_yes:
        return
    if "expensive_generation" not in project.load_config().ask_before:
        return
    raise WaitingUser(
        f"waiting_user: 预估花费 {estimated_cost} {currency or ''} "
        f"命中 ask_before=expensive_generation — {hint}",
        estimated_cost, currency,
    )


def _record_local_runs(project: Project, takes: list, params: dict,
                       estimates: dict[str, float] | None = None) -> None:
    """Record generated takes in the run ledger (§8.3).

    The cost/run split: cloud providers self-record at poll time
    (providers/base.py ``CloudProvider._on_success``), so to avoid double
    counting we record here ONLY for LOCAL providers; a provider we cannot
    classify is skipped. All of this is best-effort — the ledger is disposable
    (§3), so bookkeeping must never fail a build.

    ``estimates`` maps ``shot_id -> planned estimated_cost`` (the *事前* dry-run
    figure); each take's own estimate is looked up by its shot and persisted so
    ``manju spend`` can show estimate-vs-actual. A shot absent from the map
    records a ``None`` estimate (honest — no figure available).
    """
    estimates = estimates or {}
    try:
        from ..providers.registry import available_providers
        from ..runtime.state import RuntimeState

        providers = available_providers()
        # the plan estimate is PER SHOT (already ×candidates): attribute it to
        # exactly ONE row per shot or a multi-take run inflates estimated_total.
        remaining = dict(estimates)
        with RuntimeState(project.root) as state:
            for take in takes:
                provider = providers.get(take.sidecar.provider)
                if provider is None or getattr(provider, "kind", None) != "local":
                    continue  # cloud self-records; unknown provider -> skip
                remote = take.sidecar.remote
                state.record_run(
                    shot=take.shot_id,
                    provider=take.sidecar.provider,
                    status="succeeded",
                    take=take.name,
                    cost=remote.cost if remote and remote.cost else 0.0,
                    currency=remote.currency if remote else None,
                    params=params,
                    estimated_cost=remaining.pop(take.shot_id, None),
                )
    except (OSError, sqlite3.Error, Exception):  # disposable state, never fatal
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
    saved_cost: float = 0.0  # cache savings: what regenerating FRESH shots would cost
    waiting_user: bool = False  # §8.3 ask_before gate: needs an explicit yes

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _target_duration_ms(project: Project, shot, rules) -> int:
    """Duration a provider should aim for — mirrors the compiler's rules
    minus the (not yet existing) voice."""
    if shot.duration != "auto":
        return max(1, int(round(float(shot.duration) * 1000)))
    return rules.timing.default_shot_ms


def _estimate_shot_cost(shot, duration_ms: int) -> tuple[float, str | None]:
    """§8.3 dry-run pricing from provider manifests (§8.6). The price is taken
    from the provider that would actually run first: the shot's explicit
    provider, else the first cloud provider on its fallback chain. Local
    providers are free; a missing/broken manifest prices as 0 (doctor flags it)."""
    try:
        from ..providers.manifest import estimate_cost
        from ..providers.registry import fallback_chain, get_manifest

        names = ([shot.generation.provider] if shot.generation.provider else []) \
            + fallback_chain(shot)
        for name in names:
            manifest = get_manifest(name)
            if manifest is not None:
                return (
                    estimate_cost(manifest, duration_ms, shot.generation.candidates),
                    manifest.cost.currency,
                )
    except Exception:  # pricing is advisory; never blocks planning
        pass
    return 0.0, None


def _plan_voice(project: Project, *, gen: str) -> list[dict[str, Any]]:
    """Voice synthesis plan (M3): shots whose dialogue has no voice take yet,
    when a TTS manifest is configured. Pricing is per_call from the manifest
    (speech duration is unknown before synthesis, so per_second cannot be
    estimated honestly — the ledger records the real figure afterwards)."""
    if gen == "off":
        return []
    try:
        from ..providers.tts import tts_providers
        from .voice import VoiceState, evaluate_all_voices

        providers = tts_providers()
        if not providers:
            return []
        provider_id = sorted(providers)[0]
        manifest = providers[provider_id]
        return [
            {
                "shot": vs.shot_id,
                "kind": "voice",
                "reason": vs.state.value,
                "provider": provider_id,
                "estimated_cost": manifest.cost.per_call,
                "currency": manifest.cost.currency,
            }
            for vs in evaluate_all_voices(project)
            if vs.state == VoiceState.MISSING
        ]
    except Exception:  # planning must never break the build
        return []


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
        duration_ms = _target_duration_ms(project, shot, rules)
        cost, currency = _estimate_shot_cost(shot, duration_ms)
        plan.append(
            {
                "shot": st.shot_id,
                "reason": st.state.value,
                "candidates": shot.generation.candidates,
                "duration_ms": duration_ms,
                "provider": shot.generation.provider or "auto(fallback chain)",
                "estimated_cost": cost,
                "currency": currency,
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
    force: bool = False,  # FIX-A: re-render even when the content key matches
    actor: str = "engine",
    assume_yes: bool = False,  # explicit approval for ask_before-gated spend (§8.3)
    on_phase=None,  # Callable[[str], None] — coarse progress ("check"/"render"…)
) -> BuildResult:
    result = BuildResult()

    def _phase(name: str) -> None:
        """Coarse progress for watchers. Advisory: a broken callback must
        never break a build."""
        if on_phase is not None:
            try:
                on_phase(name)
            except Exception:
                pass

    _phase("check")
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

    # ---- 0b. cache hits: FRESH/MANUAL shots skip generation entirely;
    # saved_cost prices what regenerating the FRESH ones would have spent (§8.3
    # manifests). Manual imports were never paid work, so they count 0. Advisory
    # only — pricing must never fail a build.
    result.skipped = [s.shot_id for s in statuses
                      if s.state in (ShotState.FRESH, ShotState.MANUAL)]
    try:
        rules = project.load_rules()
        for st in statuses:
            if st.state != ShotState.FRESH:
                continue
            shot = project.load_shot(st.shot_id)
            cost, _currency = _estimate_shot_cost(
                shot, _target_duration_ms(project, shot, rules))
            result.saved_cost += cost
    except Exception:  # savings are advisory; the skipped list still stands
        pass

    # ---- 1. generation plan (video takes + voice takes, both priced §8.3)
    result.plan = _plan_generation(project, statuses, gen=gen, regen_stale=regen_stale)
    result.plan += _plan_voice(project, gen=gen)
    result.estimated_cost = sum(p["estimated_cost"] for p in result.plan)

    config = project.load_config()
    budget = config.budget.limit
    if budget is not None and result.estimated_cost > budget:
        result.ok = False
        result.errors.append(
            f"estimated cost {result.estimated_cost} exceeds budget {budget} — "
            "预算熔断 budget breaker (§8.3): raise project.yaml budget.limit or shrink the plan"
        )
        return result

    if dry_run:
        result.timeline_path = str(project.timeline_path)
        return result

    # ---- 1b. ask_before gate (§8.3 第一道闸门,现在由引擎兜底,不再只靠 agent
    # 纪律): a plan that spends real money stops for an explicit yes — uniformly
    # for every actor. The human's yes is `--yes` / a GUI confirm; an agent's
    # yes must come from relaying the estimate to the human first (SKILL.md §5
    # unchanged — the engine now defaults to STOP). Dry-run above is never gated.
    if (result.estimated_cost > 0 and not assume_yes
            and "expensive_generation" in config.ask_before):
        result.ok = False
        result.waiting_user = True
        result.errors.append(
            f"waiting_user: 预估花费 {result.estimated_cost} "
            f"{next((p.get('currency') for p in result.plan if p.get('currency')), '')} "
            "命中 ask_before=expensive_generation — 确认后重试:CLI `manju build --yes`,"
            "MCP/GUI 带 assume_yes(先 dry-run 看计划,§8.3)"
        )
        return result

    _phase("generate")
    # ---- 2. generate (fill gaps only; stale/manual are respected, §4.3)
    video_plan = [p for p in result.plan if p.get("kind") != "voice"]
    voice_plan = [p for p in result.plan if p.get("kind") == "voice"]
    # shot_id -> pre-flight estimate, persisted with each recorded run so
    # `spend` can show estimate-vs-actual (§8.3 事前 vs 事后).
    estimates = {p["shot"]: p["estimated_cost"] for p in video_plan}
    if video_plan:
        from ..providers.base import GenerationRequest, NeedsHumanInput, ProviderFailure
        from ..providers.registry import fallback_chain, generate_with_fallback

        bible = project.load_bible()
        for item in video_plan:
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
                # §8.3 事前: thread the plan's per-shot estimate onto the request
                # so a CLOUD provider records it on its own ledger row (local
                # runs get it via _record_local_runs below). Same figure the
                # `estimates` map holds — one source, both provider kinds.
                estimated_cost=item["estimated_cost"],
            )
            try:
                takes = generate_with_fallback(req, fallback_chain(shot))
            except (ProviderFailure, NeedsHumanInput) as exc:
                result.warnings.append(f"{shot.id}: generation failed — {exc}")
                continue
            result.generated.extend(f"{shot.id}/{t.name}" for t in takes)
            # showcase finding: with ANY image under media/refs, the §8.4
            # chain sends every missing shot to kenburns with that same
            # generic image — correct per spec, surprising in practice.
            # Advise, don't block.
            for t in takes:
                if t.sidecar.params.get("image_source") == "refs_dir_fallback":
                    result.warnings.append(
                        f"{shot.id}: kenburns 使用了通用参考图 "
                        f"{t.sidecar.params.get('image')}(media/refs 兜底)— "
                        "如需镜头专属画面,设 bible ref_image 或 "
                        "generation.params.image,或 generation.provider: caption_card"
                    )
                    break
            append_event(project.root, actor, "generate",
                         {"shot": shot.id, "takes": [t.name for t in takes]})
            _record_local_runs(project, takes, {"reason": item["reason"]}, estimates)

    # ---- 2v. voice synthesis (M3): fill MISSING voices via the configured
    # TTS manifest; stale voices are flagged, never redone (§4.3). Synthesis
    # happens BEFORE the timeline compile so fresh voices drive durations (§6)
    # within the same build.
    if voice_plan:
        _phase("voice")
        from ..providers.base import ProviderFailure as _PF
        from ..providers.tts import get_tts_provider

        bible = project.load_bible()
        for item in voice_plan:
            shot = project.load_shot(item["shot"])
            try:
                tts = get_tts_provider(item["provider"])
                media = tts.synthesize(project, shot, bible)
            except (_PF, Exception) as exc:
                result.warnings.append(f"{shot.id}: voice synthesis failed — {exc}")
                continue
            result.generated.append(f"{shot.id}/{media.stem}")
            append_event(project.root, actor, "voice",
                         {"shot": shot.id, "take": media.stem, "provider": item["provider"]})
    try:
        from .voice import VoiceState, evaluate_all_voices

        stale_voices = [v.shot_id for v in evaluate_all_voices(project)
                        if v.state == VoiceState.STALE]
        if stale_voices:
            result.warnings.append(
                "voice stale(台词/音色已变,默认不重做,§4.3): "
                + ", ".join(stale_voices) + " — 用 manju voice <shot> 重配音"
            )
    except Exception:
        pass  # voice bookkeeping never blocks a build

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

    # ---- 2p. packaging card assets (§13-14): render missing intro/outro cards
    # BEFORE the compile/render so the content-addressed segments the compiler
    # points at exist on disk. Content-addressed + append-only: an unchanged
    # spec reuses its file, edited text mints a new one. Best-effort — a card
    # render hiccup degrades to a warning (QC then flags the missing asset).
    packaging = project.load_packaging()
    if packaging.intro.enabled or packaging.outro.enabled:
        try:
            from ..media.packaging import ensure_packaging_cards

            made = ensure_packaging_cards(project, packaging)
            for rel in made:
                append_event(project.root, actor, "package_card", {"asset": rel})
        except Exception as exc:
            result.warnings.append(f"packaging card render skipped: {exc}")

    _phase("compile")
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
        # FIX-D: a hand-edited timeline.json that fails to parse is a clean,
        # actionable build error — never a JSONDecodeError traceback.
        try:
            timeline = project.load_timeline() or timeline  # render what the human made
        except Exception as exc:
            result.ok = False
            result.errors.append(
                f"timeline/timeline.json: 手工时间线无法解析 — {' '.join(str(exc).split())} "
                "(建议:修复该 JSON,或把 rules.yaml 的 mode 改回 compiled;"
                "对照 timeline.generated.json 排查)"
            )
            return result

    # ---- 4. captions
    rules = project.load_rules()
    ass_path = None
    if rules.captions.enabled and timeline.tracks.captions:
        _phase("captions")
        from ..exporters.srt_ass import export_captions

        paths = export_captions(project, timeline)
        result.captions = {k: project.relpath(v) for k, v in paths.items()}
        ass_path = paths.get("ass")

    # ---- 5. render
    if target in ("proxy", "final"):
        _phase(f"render:{target}")
        from ..media.render import render_timeline

        finals_before = set(project.final_dir.glob("final_v*.mp4"))
        try:
            out = render_timeline(project, timeline, target=target, ass_file=ass_path,
                                  force=force)
        except Exception as exc:  # FIX-D: MediaError etc. -> one-line diagnostic
            from ..media.ffmpeg import MediaError

            if not isinstance(exc, MediaError):
                raise  # unknown exceptions keep their traceback
            result.ok = False
            result.errors.append(
                f"render: {' '.join(str(exc).split())} "
                "(建议:查看 .manju/logs/render.log 复现单条 ffmpeg 命令)"
            )
            return result
        result.render_path = project.relpath(out)
        if target == "final" and out in finals_before:
            result.warnings.append(
                f"final up-to-date (content key match) — reused {out.name}; "
                "use --force to re-render (FIX-A)"
            )
        append_event(project.root, actor, "render", {"target": target, "output": result.render_path})

    # ---- 6. QC (§9)
    if target in ("proxy", "final", "qc"):
        _phase("qc")
        from ..qc.checks import run_qc
        from ..qc.report import write_reports

        qc = run_qc(project, timeline)
        result.qc_ok = qc.ok
        result.qc_reports = {k: project.relpath(v) for k, v in write_reports(project, qc).items()}

    # ---- 7. draft exports
    if target in ("final", "exports"):
        _phase("exports")
        profiles = set(config.export_profiles)
        if "otio" in profiles:
            from ..exporters.otio import export_otio

            result.exports["otio"] = project.relpath(export_otio(project, timeline))
        if "jianying" in profiles:
            from ..exporters.jianying import export_jianying

            result.exports["jianying"] = project.relpath(export_jianying(project, timeline))
            # dual-path (decision 8): native pyJianYingDraft draft is primary;
            # its absence is a note, never a build failure (§14 fallback exits)
            try:
                from ..exporters.native_draft import export_jianying_native

                result.exports["jianying_native"] = project.relpath(
                    export_jianying_native(project, timeline)
                )
            except Exception as exc:
                result.warnings.append(f"jianying native draft skipped: {exc}")
        if "capcut" in profiles:
            try:
                from ..exporters.native_draft import export_capcut_native

                result.exports["capcut"] = project.relpath(
                    export_capcut_native(project, timeline)
                )
            except Exception as exc:
                result.warnings.append(f"capcut draft skipped: {exc}")

    append_event(project.root, actor, "build",
                 {"target": target, "ok": result.ok, "render": result.render_path})
    return result


def redo_shot(project: Project, shot_id: str, *, candidates: int | None = None,
              provider: str | None = None, seed: int | None = None,
              actor: str = "engine", assume_yes: bool = False) -> list[str]:
    """`manju redo S002` — force new takes (append-only), regardless of
    staleness. Selection is only touched when the shot had none.

    A priced redo raises :class:`WaitingUser` unless ``assume_yes`` — the same
    §8.3 ask_before gate as ``build`` (the R7 spend-gate hole closure: a priced
    redo used to spend without ever hitting the gate that ``build`` enforces)."""
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
    if candidates:
        shot.generation.candidates = int(candidates)  # in-memory, for pricing
    # §8.3 事前: price this redo and gate it just like build before any spend.
    cost, currency = _estimate_shot_cost(shot, _target_duration_ms(project, shot, rules))
    spend_gate(project, cost, currency, assume_yes=assume_yes,
               hint=f"确认后重试:manju redo {shot_id} --yes(或 MCP/GUI 带 assume_yes)")
    req = GenerationRequest(
        project=project,
        shot=shot,
        bible=bible,
        spec_hash=compute_spec_hash(shot, bible),
        duration_ms=_target_duration_ms(project, shot, rules),
        candidates=candidates or shot.generation.candidates,
        params=params,
        # reuse the estimate computed above for the gate (don't recompute) so a
        # CLOUD redo records it on its ledger row too.
        estimated_cost=cost,
    )
    takes = generate_with_fallback(req, fallback_chain(shot))
    if not shot.status.selected_take and takes:
        choice = takes[-1].name
        project.update_shot_raw(
            shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", choice)
        )
    _record_local_runs(project, takes, {"redo": True}, {shot_id: cost})
    append_event(project.root, actor, "redo", {"shot": shot_id, "takes": [t.name for t in takes]})
    return [t.name for t in takes]
