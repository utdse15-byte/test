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
from pathlib import Path
from typing import Any, Callable

from ..core.check import run_check
from ..core.container import Project
from ..core.events import append_event
from ..core.hashing import get_by_path
from ..timeline.compiler import CompileError, build_timeline
from .stale import ShotBuildStatus, ShotState, evaluate_all


class BuildError(RuntimeError):
    pass


class BuildCanceled(BuildError):
    """Raised when a ``should_cancel()`` checkpoint trips mid-build (goal:
    honest cooperative cancellation — gui/jobs.py ``JobRunner.cancel``).

    Always raised from INSIDE ``run_build``'s ``with build_lock(...)`` block,
    so it unwinds through the lock's own ``finally: lock.release()`` exactly
    like any other exception — a canceled build never leaves the project
    locked. Whatever was already generated stays on disk as ordinary
    content-addressed cache (§3): nothing is rolled back, and a later build
    reuses it instead of re-spending. Carries what was already done/spent so
    the caller (CLI/GUI/MCP) reports it honestly instead of a bare "canceled"."""

    def __init__(self, message: str, *, generated: list[str], spent: float,
                 currency: str | None, run_id: str | None = None):
        super().__init__(message)
        self.generated = generated
        self.spent = spent
        self.currency = currency
        # DR03C: carry the run id so run_build's handler can set it on the
        # (freshly minted) canceled BuildResult — the CANCELED run attempt is
        # emitted inside _run_build_phases, where the RunEvidence lives.
        self.run_id = run_id


# Round W (issue #3): the single source of truth for --gen's three legal
# values. CLI/MCP/GUI/director all validate against THIS tuple (import it —
# don't repeat the literal) so a typo like "--gen offf" can never drift
# between call sites into "not off, so proceed" (the review's concrete bug).
GEN_MODES = ("missing", "auto", "off")


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


def _keyframe_gated_video_shots(project: Project, *, gen: str,
                                rules=None) -> list[str]:
    """AI_IDE_16 §10 — shots pending PAID video whose keyframe candidates are
    NOT adopted (Fable ruling 2). A shot qualifies iff it (a) has keyframe
    (image) candidates but no adopted one, (b) has no final VIDEO take yet, and
    (c) its video generation is PRICED > 0 (a free local fallback is not a §10
    paid-video request; a local-only project is never gated).

    Deliberately SHOT-level, not plan-level: an unadopted keyframe leaves the
    shot NEEDS_SELECTION (not MISSING), so it is not in the auto-generate plan —
    but under the unattended profile an unadopted keyframe must still block the
    paid build (the ladder discipline). A shot with no keyframe candidates never
    appears here (opt-in per shot → old projects byte-identical). Never raises
    into the build."""
    if gen == "off":
        return []
    from ..qc.production import keyframe_gate, video_takes

    if rules is None:
        try:
            rules = project.load_rules()
        except Exception:
            return []
    out: list[str] = []
    for sid in project.shot_ids():
        try:
            if not keyframe_gate(project, sid)["gated"]:
                continue
            if video_takes(project, sid):
                continue  # already has a video take → past the gate
            shot = project.load_shot(sid)
            cost, _cur = _estimate_shot_cost(
                shot, _target_duration_ms(project, shot, rules))
        except Exception:
            continue
        if (cost or 0) > 0:
            out.append(sid)
    return out


# AI_IDE_16: image extensions that mark a take as a KEYFRAME candidate (never a
# video deliverable). Kept local so graph does not import qc at module load.
_LADDER_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg"})


def _animatic_shot_still(project: Project, shot_id: str) -> "Path | None":
    """The best keyframe still for a shot's animatic panel (§6): the ADOPTED
    keyframe (selected image take or promoted ref) > first candidate image take
    > an authored keyframe image ref > None (caller falls back to a slate)."""
    from ..qc.production import keyframe_adoption, keyframe_candidates

    cands = keyframe_candidates(project, shot_id)
    ad = keyframe_adoption(project, shot_id)
    if ad.get("adopted") and ad.get("take"):
        t = next((c for c in cands if c.name == ad["take"]), None)
        if t is not None and t.media_path is not None and t.media_path.exists():
            return t.media_path
    for c in cands:
        if c.media_path is not None and c.media_path.exists():
            return c.media_path
    try:
        from ..providers.refs import resolve_local_ref

        for kf in getattr(project.load_shot(shot_id), "keyframes", []) or []:
            img = getattr(kf, "image", None)
            if not img:
                continue
            p, _ = resolve_local_ref(project, str(img))
            if p is not None and p.exists():
                return p
    except Exception:
        pass
    return None


def _render_animatic(project: Project, timeline, *, ass_file=None, force=False):
    """AI_IDE_16 §6 — a DETERMINISTIC animatic assembled from each shot's best
    keyframe still via ffmpeg_kenburns pan/hold, over the EXISTING timeline
    audio/captions. Output is a DERIVED artifact in ``renders/animatic/`` — never
    a video take, never selected; content-keyed so a repeat run is idempotent and
    a delete is rebuildable. REUSES the kenburns + final-render helpers (Fable
    ruling 4); it never plans or submits a paid video request."""
    from pathlib import Path

    from ..core.hashing import cache_key, hash_file, short_hash
    from ..core.models import Timeline
    from ..media.audition import ensure_slate
    from ..media.ffmpeg import default_log
    from ..media.kenburns import kenburns
    from ..media.render import (
        _audio_input_hashes, _enc_params, _read_key_sidecar, _toolchain_key_component,
        _write_key_sidecar, render_timeline,
    )

    log = default_log(project.root, "animatic")
    config = project.load_config()
    clips_dir = project.root / ".manju" / "animatic" / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    data = timeline.model_dump()
    # R2: a rational (1001-family) project folds its exact rate into the per-still
    # preview key so its animatic clips are content-distinct from an int-nominal
    # project's; None (int) contributes nothing → byte-identical key. Read via the
    # config's frame_rate resolver (never the raw rational field — R1 surface
    # pin). The animatic is a de-scoped preview surface: its clips still ENCODE at
    # the int nominal fps below (kenburns/slate are untouched), so a rational
    # preview is nominal-rate — the OUTER animatic key still differs via the
    # timeline's rational-rate echo folded through tl.model_dump() below.
    _rate = config.frame_rate
    _rate_key = None if _rate.exact_int is not None else str(_rate)
    # S4: the strictly-opt-in toolchain component (sorted token→fact map from
    # media/render's helper, process-cached facts). None for every project that
    # never set cache_toolchain_keys → both animatic key sites below append
    # nothing, byte-identical keys — exactly the _rate_key drop-when-absent
    # pattern. Opted in, the kenburns clip keys AND the outer animatic key
    # shift when a declared fact shifts (a new ffmpeg honestly re-renders the
    # preview instead of reusing stale-toolchain clip bytes).
    _tc_key = _toolchain_key_component(project, config)
    seg_keys: list[str] = []
    for clip in data.get("tracks", {}).get("video", []) or []:
        shot = str(clip.get("shot") or "")
        dur = int(clip.get("duration_ms") or 1000)
        still = (_animatic_shot_still(project, shot)
                 if shot and not shot.startswith("__") else None)
        if still is not None:
            kb_key: dict[str, Any] = {
                "still": hash_file(still), "dur": dur, "w": config.width,
                "h": config.height, "fps": config.fps, "kind": "animatic_kenburns",
            }
            if _rate_key is not None:
                kb_key["rate"] = _rate_key
            if _tc_key is not None:
                kb_key["toolchain"] = _tc_key
            key = short_hash(cache_key(kb_key), 12)
            dest = clips_dir / f"{shot}_{key}.mp4"
            if force or not (dest.exists() and dest.stat().st_size > 0):
                kenburns(still, dest, width=config.width, height=config.height,
                         fps=config.fps, duration_ms=dur, zoom_from=1.0,
                         zoom_to=1.08, log=log)
            clip["source"] = project.relpath(dest)
            clip["take"] = "__animatic__"
            seg_keys.append(f"kb:{hash_file(still)}:{dur}")
        else:
            slate = ensure_slate(project, shot or "shot", duration_ms=dur,
                                 width=config.width, height=config.height,
                                 fps=config.fps)
            clip["source"] = project.relpath(slate)
            clip["take"] = "__slate__"
            seg_keys.append(f"slate:{shot}:{dur}")
    tl = Timeline.model_validate(data)

    animatic_payload: dict[str, Any] = {
        "segments": seg_keys,
        "timeline": tl.model_dump(exclude={"meta"}),
        "ass": hash_file(ass_file) if ass_file and Path(ass_file).exists() else None,
        "audio": _audio_input_hashes(project, tl),
        "encoding": _enc_params("final"),
        "target": "animatic",
    }
    if _tc_key is not None:  # S4 opt-in only — absent appends nothing (byte pin)
        animatic_payload["toolchain"] = _tc_key
    key = cache_key(animatic_payload)

    out_dir = project.root / "renders" / "animatic"
    out_dir.mkdir(parents=True, exist_ok=True)
    versions = []
    for p in out_dir.glob("animatic_v*.mp4"):
        try:
            versions.append((int(p.stem.split("_v", 1)[1]), p))
        except (IndexError, ValueError):
            pass
    if not force and versions:
        newest = max(versions, key=lambda t: t[0])[1]
        if _read_key_sidecar(newest) == key:
            log(f"animatic up-to-date (content key match): reusing {newest.name}")
            return newest
    n = max((v for v, _ in versions), default=0) + 1
    out = out_dir / f"animatic_v{n}.mp4"
    rendered = render_timeline(project, tl, target="final", out_path=out,
                               ass_file=ass_file, force=True, log=log)
    _write_key_sidecar(rendered, key, "animatic")
    log(f"animatic written: {project.relpath(rendered)}")
    return rendered


def _record_local_runs(project: Project, takes: list, params: dict,
                       estimates: dict[str, float] | None = None,
                       evidence: Any = None) -> None:
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
                    # DR03C test 19: the ledger row carries the SAME attempt_id
                    # the events.jsonl SUCCEEDED record carries for this take.
                    attempt_id=(evidence.attempt_for_take(take.shot_id, take.name)
                                if evidence is not None else None),
                )
    except (OSError, sqlite3.Error, Exception):  # disposable state, never fatal
        pass


def _emit_render_attempt(evidence, project: Project, out, target: str, *,
                         reused: bool, handle=None) -> None:
    """DR03C: emit the render attempt (SUCCEEDED fresh / SKIPPED_CACHE_HIT
    reused). The final's ``.key.json`` sidecar already carries ``output_sha256``
    (DR01) and ``final_key`` — read them (a cheap JSON read), never re-hash the
    mp4. Best-effort: any read hiccup degrades to an attempt without the sha.

    P0 WP4: ``handle`` is the attempt handle PRE-MINTED before the render ran
    (its id was announced by an attempt_started event); when given, the terminal
    lands on that SAME attempt_id so started/terminal correlate. ``None`` keeps
    the old post-hoc mint (any legacy/direct caller)."""
    import json as _json

    from .attempts import output_ref

    content_key = None
    sha = None
    try:
        sidecar = out.with_suffix(".key.json")
        if sidecar.exists():
            data = _json.loads(sidecar.read_text(encoding="utf-8"))
            content_key = data.get("final_key")
            sha = data.get("output_sha256")
    except Exception:
        pass
    ref_kwargs: dict[str, Any] = {"path": project.relpath(out)}
    if sha:
        ref_kwargs["sha256"] = sha
    else:  # sidecar lacked it (older final) — hash once so the output is verifiable
        try:
            from ..core.hashing import hash_file
            ref_kwargs["sha256"] = hash_file(out)
        except Exception:
            ref_kwargs["sha256"] = ""
    if content_key:
        ref_kwargs["content_key"] = content_key
    try:
        ref_kwargs["bytes"] = out.stat().st_size
    except OSError:
        pass
    out_ref = output_ref("final", **ref_kwargs)
    if handle is None:
        handle = evidence.attempt("render", {"kind": "render", "target": target},
                                  "render")
    if reused:
        handle.skipped_cache_hit(outputs=[out_ref])
    else:
        handle.succeeded(outputs=[out_ref])


def _resolve_head_provider(project: Project, shot, else_bias: str | None) -> str | None:
    """Best-effort: the provider a shot's fallback attempt will try FIRST —
    used only to pick which manifest's ``max_concurrent`` (goal 8) gates the
    worker slot. Mirrors ``providers.registry.generate_with_fallback``'s own
    resolution (routing when a routing.yaml/mode bias is active, else the
    shot's explicit provider, else the §8.4 fallback chain's head). Advisory:
    an unresolved head returns ``None`` (unlimited), never raises."""
    if shot.generation.provider:
        return shot.generation.provider
    try:
        from ..providers import routing

        config = routing.load_routing(project)
        if config is not None or else_bias is not None:
            order = routing.resolve(project, shot, config, else_bias=else_bias).order
            if order:
                return order[0]
    except Exception:
        pass
    try:
        from ..providers.registry import fallback_chain

        chain = fallback_chain(shot)
        return chain[0] if chain else None
    except Exception:
        return None


def _provider_semaphore(name: str | None, cache: dict):
    """The shared :class:`threading.Semaphore` gating concurrent calls to
    provider ``name`` (goal 8), sized by its manifest's
    ``limits.max_concurrent``. ``None`` (unlimited — today's behaviour) when
    the name is unresolved or the provider has no manifest at all (every
    built-in local provider: kenburns/caption_card/manual_import), since only
    a config-declared manifest carries a real concurrency policy. Cached per
    ``name`` so every worker in one build shares the SAME semaphore object."""
    if name is None:
        return None
    if name not in cache:
        import threading

        try:
            from ..providers.registry import get_manifest

            manifest = get_manifest(name)
        except Exception:
            manifest = None
        limit = manifest.limits.max_concurrent if manifest is not None else None
        cache[name] = threading.Semaphore(limit) if limit and limit > 0 else None
    return cache[name]


def _concurrent_generate(items, gen_one, *, max_workers: int,
                         budget_limit: float | None, head_provider=None,
                         should_cancel: "Callable[[], bool] | None" = None):
    """Drive ``gen_one`` over ``items`` in a bounded thread pool (goal 14).

    Returns ``(results_by_shot, tripped, canceled, running_cost,
    in_flight_at_trip)``. Provider calls run concurrently (``max_workers`` at
    once), but this function performs NO order-dependent commit — the caller
    applies that strictly in plan order from the returned dict, so the
    outcome is deterministic no matter who finished first.

    ``should_cancel`` (goal: honest job cancellation) is checked before EVERY
    new submission — the same "between per-shot generation submissions"
    checkpoint the serial path uses — so a trip stops handing out new work
    while whatever is already in flight finishes normally (mirrors the
    budget-trip honesty below: ``canceled=True`` tells the caller which
    already-committed results it can keep). ``None`` (no GUI job wired a
    cancel flag) never checks anything — byte-identical to before.

    Per-provider concurrency (goal 8): ``head_provider(item) -> str | None``
    resolves the provider whose manifest ``limits.max_concurrent`` should gate
    THIS item's worker slot — the same head :func:`providers.routing.resolve`
    (or the §8.4 fallback chain) would try first. A manifest declaring
    ``max_concurrent: 1`` therefore never receives more than one concurrent
    call, even though the global pool runs ``max_workers`` at once: the
    semaphore acquire sits UNDER (nested inside) the global pool, so a worker
    thread blocks on the provider's own slot rather than the fallback attempt
    running unbounded. No ``head_provider`` (or an unresolved head) is
    unlimited — byte-identical to before goal 8.

    The other live governor is the budget: as each shot's ACTUAL cost lands,
    it is summed, and the instant the running total exceeds ``budget_limit``
    no NEW shot is submitted (in-flight ones finish — goal 80 honesty:
    ``in_flight_at_trip`` is a snapshot of the shots still running at the exact
    moment the trip fired, so the caller can say exactly which N will still
    complete and bill). With the default free/local providers every actual
    cost is 0, so the trip never fires and the result set equals ``items`` —
    nothing observable changes but the order of execution."""
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
    from concurrent.futures import wait as _wait

    items = list(items)
    results: dict[str, dict] = {}
    running = 0.0
    tripped = False
    canceled = False
    next_i = 0
    sem_cache: dict = {}
    in_flight_at_trip: list[str] = []

    def _gated(item):
        sem = _provider_semaphore(head_provider(item), sem_cache) if head_provider else None
        if sem is None:
            return gen_one(item)
        with sem:
            return gen_one(item)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        in_flight: dict = {}

        def _fill() -> None:
            nonlocal next_i, canceled
            while (next_i < len(items) and len(in_flight) < max_workers
                   and not tripped and not canceled):
                if should_cancel is not None and should_cancel():
                    canceled = True
                    break
                it = items[next_i]
                next_i += 1
                in_flight[ex.submit(_gated, it)] = it["shot"]

        _fill()
        while in_flight:
            finished, _ = _wait(list(in_flight), return_when=FIRST_COMPLETED)
            for fut in finished:
                sid = in_flight.pop(fut)
                res = fut.result()
                results[sid] = res
                # A per-shot ProviderCanceled (goal: honest job cancellation —
                # this item's own poll loop observed should_cancel and stopped
                # waiting, §8.1) also counts as canceled, even if _fill() above
                # never itself saw the flag between submissions.
                if res.get("canceled") and not canceled:
                    canceled = True
                running += float(res.get("actual_cost", 0.0) or 0.0)
                if (budget_limit is not None and running > budget_limit
                        and not tripped):
                    tripped = True
                    # snapshot: shots still running RIGHT NOW will complete and
                    # bill regardless of the trip (goal 80) — _fill() below
                    # submits nothing new once tripped, so this list only
                    # shrinks as the while loop drains it.
                    in_flight_at_trip = list(in_flight.values())
            _fill()
    return results, tripped, canceled, running, in_flight_at_trip


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
    # Round W (issue #81): the render `target in (proxy, final, qc)` actually
    # QC'd. ``target=qc`` deliberately does NOT render first (see
    # _run_build_phases §6 below) — it QCs the newest EXISTING final on disk
    # against the freshly recompiled timeline, so a stale render is a real
    # possibility. This names exactly which artifact that was (or None when
    # there is no final yet), so the CLI/MCP/GUI can say so instead of leaving
    # the user to assume QC ran against a fresh render.
    qc_final: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    estimated_cost: float = 0.0
    saved_cost: float = 0.0  # cache savings: what regenerating FRESH shots would cost
    waiting_user: bool = False  # §8.3 ask_before gate: needs an explicit yes
    # goal: honest job cancellation — a should_cancel() checkpoint tripped
    # mid-build (gui/jobs.py JobRunner.cancel). ``generated``/``errors`` still
    # carry what was actually produced and the honest cancellation message;
    # this flag lets a caller distinguish "canceled" from an ordinary ok=False
    # failure without string-matching the error text.
    canceled: bool = False
    # goal 10: structured failure summaries recorded during THIS build (errors +
    # info-level degradations), newest first — the AI path reads these ids and
    # cross-references reports/failures.jsonl for the full evidence/hint.
    failures: list[dict[str, Any]] = field(default_factory=list)
    # DR03C: the run-evidence id for THIS build (the same id on the final's key
    # sidecar and the events.jsonl attempt stream). Additive, default None so
    # dry-run (a plan, emits no evidence) and any legacy construction stay
    # unchanged; `build --json`/MCP surface it for free via to_dict().
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _record(project: Project, step: str, subject: str, cause: str, *,
            evidence: str = "", hint: str = "", log_path: str | None = None,
            level: str = "error", actor: str = "engine",
            detail: dict | None = None) -> None:
    """Record one build-phase failure/degradation (goal 10). Best-effort: the
    build never breaks because a failure could not be logged."""
    try:
        from ..core.failures import Failure, record_failure

        record_failure(
            project,
            Failure(step=step, subject=subject, cause=cause, evidence=evidence,
                    hint=hint, log_path=log_path, level=level, actor=actor,
                    detail=detail or {}),
        )
    except Exception:
        pass


def _target_duration_ms(project: Project, shot, rules) -> int:
    """Duration a provider should aim for — mirrors the compiler's rules
    minus the (not yet existing) voice."""
    if shot.duration != "auto":
        return max(1, int(round(float(shot.duration) * 1000)))
    return rules.timing.default_shot_ms


def _routed_shot_for_pricing(project: Project | None, shot, *, else_bias: str | None = None):
    """An in-memory copy of ``shot`` with ``generation.provider`` pinned to the
    head :func:`providers.routing.resolve` would pick (#7) — so
    :func:`_estimate_shot_cost` prices the SAME provider execution will
    actually try first, not the static fallback-chain head. The review's exact
    failure mode: gui/plan.py already showed routing's resolved provider as a
    label while the cost number still priced the untouched shot.

    Returns ``shot`` unchanged (no copy, no routing lookup) when there is
    nothing to resolve: no ``project``, an explicit ``generation.provider``
    already pinned (it always wins over routing anyway), or — the common,
    byte-identical default — no routing.yaml active and no build-mode bias.
    Advisory only: any resolution failure degrades to the untouched shot,
    never raises."""
    if project is None or getattr(shot.generation, "provider", None):
        return shot
    try:
        from ..providers import routing

        config = routing.load_routing(project)
        if config is None and else_bias is None:
            return shot  # byte-identical default: no routing file, no mode bias
        chosen = routing.resolve(project, shot, config, else_bias=else_bias).chosen
        if not chosen:
            return shot
        priced = shot.model_copy(deep=True)
        priced.generation.provider = chosen
        return priced
    except Exception:
        return shot  # pricing is advisory; never blocks planning


def _candidates_clamp_note(shot) -> str | None:
    """§29: generic_cloud's submit/poll/download path always returns exactly
    ONE result per job (one ``result_url``, one downloaded file) — a
    ``candidates > 1`` shot routed there is priced (and will be produced) as
    1, never silently oversold. Returns the 中文 note to surface on the plan
    row, or ``None`` when there is nothing to clamp (candidates<=1, or the
    provider that would price this shot first is not generic_cloud)."""
    if int(getattr(shot.generation, "candidates", 1) or 1) <= 1:
        return None
    try:
        from ..providers.manifest import GENERIC_ADAPTER
        from ..providers.registry import fallback_chain, get_manifest

        names = ([shot.generation.provider] if shot.generation.provider else []) \
            + fallback_chain(shot)
        for name in names:
            manifest = get_manifest(name)
            if manifest is not None:
                if manifest.adapter == GENERIC_ADAPTER:
                    return (f"{name}: 该 provider 单次只产一个候选,已按 1 计价 "
                            f"(generation.candidates={shot.generation.candidates})")
                return None  # the provider that would price/run first isn't generic_cloud
    except Exception:
        pass
    return None


def _estimate_shot_cost(shot, duration_ms: int) -> tuple[float, str | None]:
    """§8.3 dry-run pricing from provider manifests (§8.6). The price is taken
    from the provider that would actually run first: the shot's explicit
    provider, else the first cloud provider on its fallback chain. Local
    providers are free; a missing/broken manifest prices as 0 (doctor flags it).

    Callers that want the estimate to follow ``providers.routing`` (#7) pass a
    shot already resolved through :func:`_routed_shot_for_pricing` — this
    function stays routing-agnostic (and its 2-arg signature stable) so the
    many call sites that monkeypatch it in tests are unaffected."""
    try:
        from ..providers.manifest import GENERIC_ADAPTER, estimate_cost
        from ..providers.registry import fallback_chain, get_manifest

        names = ([shot.generation.provider] if shot.generation.provider else []) \
            + fallback_chain(shot)
        for name in names:
            manifest = get_manifest(name)
            if manifest is not None:
                # §29: generic_cloud never produces more than one take per job —
                # price exactly what will be produced, not candidates × price.
                candidates = (1 if manifest.adapter == GENERIC_ADAPTER
                             else shot.generation.candidates)
                return (
                    estimate_cost(manifest, duration_ms, candidates),
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
                     gen: str, regen_stale: bool,
                     else_bias: str | None = None) -> list[dict[str, Any]]:
    rules = project.load_rules()
    plan: list[dict[str, Any]] = []
    # ``auto`` = missing + stale (P1-6): operators/agents who pick auto expect
    # the engine to refresh outdated takes, not silently behave like missing.
    # Explicit ``--regen-stale`` still forces stale regen under gen=missing.
    want_stale = bool(regen_stale) or gen == "auto"
    for st in statuses:
        needs = st.state == ShotState.MISSING or (want_stale and st.state == ShotState.STALE)
        if not needs or gen == "off":
            continue
        shot = project.load_shot(st.shot_id)
        if shot.generation.strategy == "manual":
            continue  # this shot is explicitly waiting for a human import
        duration_ms = _target_duration_ms(project, shot, rules)
        # #7: price the SAME provider routing (incl. build-mode bias) would
        # actually try first, not the static fallback-chain head.
        priced_shot = _routed_shot_for_pricing(project, shot, else_bias=else_bias)
        cost, currency = _estimate_shot_cost(priced_shot, duration_ms)
        plan.append(
            {
                "shot": st.shot_id,
                "reason": st.state.value,
                "candidates": shot.generation.candidates,
                "duration_ms": duration_ms,
                "provider": shot.generation.provider or "auto(fallback chain)",
                "estimated_cost": cost,
                "currency": currency,
                # #29: non-None only when a candidates>1 shot prices/generates
                # through generic_cloud (which always produces exactly one take).
                "candidates_note": _candidates_clamp_note(shot),
            }
        )
    return plan


def run_build(
    project: Project,
    *,
    target: str = "final",  # proxy | final | exports | qc | audition
    gen: str = "missing",  # missing | auto | off
    regen_stale: bool = False,
    dry_run: bool = False,
    force: bool = False,  # FIX-A: re-render even when the content key matches
    actor: str = "engine",
    assume_yes: bool = False,  # explicit approval for ask_before-gated spend (§8.3)
    mode: str | None = None,  # goal 14: quality|balanced|speed (flag) — None = default
    on_phase=None,  # Callable[[str], None] — coarse progress ("check"/"render"…)
    include_unindexed: bool = False,  # review #5: opt back into pre-round-W behaviour
    should_cancel: "Callable[[], bool] | None" = None,  # goal: honest job cancellation
    lang: str | None = None,  # WP4: locale overlay (None = base, byte-identical)
    agent_profile: str = "collaborative",  # AI_IDE_16 §10 keyframe spend gate
) -> BuildResult:
    """One mutating build per project at a time (§3, §5, R2). Value locks guard
    *content*; this process lock guards the dual-actor scenario — a human
    terminal, an AI session over MCP and the board's job runner can otherwise
    race on timeline.json / renders/ / the ledger with nobody at fault. The
    whole mutating build runs under ``.manju/build.lock``; contention is a
    one-line, ``ok=False`` result (BuildLocked → errors), never a traceback.

    Dry-run is READ-ONLY and deliberately runs WITHOUT the lock, so a cost
    estimate stays available even while another actor is mid-build.

    ``include_unindexed`` (review #5, default False): a shot that exists on
    disk but is not in ``shots/index.yaml`` order is excluded from the plan
    AND the compiled timeline — index order is the order authority. Pass
    True (``manju build --include-unindexed``) to include it anyway.

    ``should_cancel`` (goal: honest job cancellation, default ``None`` = the
    byte-identical original behaviour): a zero-arg predicate checked between
    phases, between per-shot generation submissions, and around the render
    step — gui/jobs.py's ``JobRunner`` wires a job's cancel flag here so a
    GUI cancel click actually stops a running build. A trip raises
    :class:`BuildCanceled` internally, which this function turns into a
    clean, ``ok=False, canceled=True`` result (lock released, nothing rolled
    back — see :class:`BuildCanceled`'s docstring) instead of an exception
    reaching the caller. Dry-run never checks it (read-only and instant —
    nothing to cancel)."""
    from datetime import datetime, timezone

    from ..runtime.buildlock import BuildLocked, build_lock

    # Round W (issue #3): validate --gen HERE — the one entry point every
    # caller (CLI, MCP tool, GUI plan/server, director) ultimately funnels
    # through — so a typo like "offf" can never be treated as "not off" and
    # silently proceed to spend money on generation. Some callers already
    # pre-validate (defense in depth, harmless); this closes the gap for the
    # ones that did not (plain CLI `manju build`, the MCP `build` tool).
    if gen not in GEN_MODES:
        result = BuildResult()
        result.ok = False
        result.errors.append(
            f"--gen 必须是 {'/'.join(GEN_MODES)} 之一(实际 {gen!r})— 未知值不会被当成 "
            "off,为避免在没打算生成时误触发付费生成,构建已直接停止;检查拼写"
        )
        return result

    if lang:
        from ..core.locale import validate_lang
        from ..core.container import ProjectError as _PE

        try:
            lang = validate_lang(lang)
        except _PE as exc:
            result = BuildResult()
            result.ok = False
            result.errors.append(str(exc))
            return result
        # C1: refuse targets that still write into shared base trees.
        if target in ("proxy", "exports"):
            result = BuildResult()
            result.ok = False
            result.errors.append(
                f"--lang 与 --target {target} 会污染 base 树,已拒绝;"
                "请用 manju build --lang <lang> --target final|qc"
            )
            return result

    # Boundary for "failures recorded during THIS build": every structured
    # record carries a UTC iso-seconds ts in the same format, so a string >= is a
    # clean time filter (goal 10). Captured before any phase runs.
    build_start = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if dry_run:
        # Dry-run is read-only and never spends, so it never records a real
        # failure; return as-is (failures stays []). should_cancel is
        # deliberately NOT threaded in here: dry-run is synchronous and free
        # (§8.3) — there is nothing to cancel, and this early-return branch
        # has no try/except to unwind a BuildCanceled cleanly.
        return _run_build_phases(
            project, target=target, gen=gen, regen_stale=regen_stale,
            dry_run=True, force=force, actor=actor,
            assume_yes=assume_yes, mode=mode, on_phase=on_phase,
            include_unindexed=include_unindexed, lang=lang,
            agent_profile=agent_profile,
        )
    # P0 WP4: _run_build_phases stamps its run_id + terminal-emitted flag onto
    # this carrier, so an EXCEPTION escaping the phases still emits
    # run_terminal(failed) below (try/finally semantics). A hard KILL emits
    # nothing — which is exactly what the manifest's INCOMPLETE detects.
    _run_info: dict = {}
    try:
        with build_lock(project.root, actor=actor):
            result = _run_build_phases(
                project, target=target, gen=gen, regen_stale=regen_stale,
                dry_run=False, force=force, actor=actor,
                assume_yes=assume_yes, mode=mode, on_phase=on_phase,
                include_unindexed=include_unindexed, should_cancel=should_cancel,
                lang=lang, agent_profile=agent_profile, _run_info=_run_info,
            )
    except BuildLocked as exc:
        # Contention is a gate, not a debuggable failure — keep R2 semantics
        # (one-line ok=False), do not record it as a failure.
        result = BuildResult()
        result.ok = False
        result.errors.append(str(exc))
        return result
    except BuildCanceled as exc:
        # goal: honest job cancellation. build_lock's own context manager
        # already released the lock (its finally: runs before this except,
        # since the raise happened INSIDE the with-block); nothing here needs
        # to unlock anything. Report exactly what the checkpoint knew: what
        # was generated and what was actually spent — never a bare "canceled".
        result = BuildResult()
        result.ok = False
        result.canceled = True
        result.generated = list(exc.generated)
        result.run_id = exc.run_id  # DR03C: correlate the canceled result to its run
        result.errors.append(str(exc))
        # P0 WP4 (review): _cancel_check emits the canceled run_terminal before
        # raising, so this is a no-op on the normal path — but a BuildCanceled
        # from the documented-unreachable direct-raise (a flapping cancel flag
        # at graph.py's MediaCanceled edge) must still terminate the run, or a
        # deliberately canceled run would derive INCOMPLETE.
        rid = _run_info.get("run_id")
        if rid and not _run_info.get("terminal_emitted"):
            _run_info["terminal_emitted"] = True
            try:
                from .attempts import append_run_terminal

                append_run_terminal(project, rid, status="canceled", actor=actor)
            except Exception:
                pass
        append_event(project.root, actor, "build",
                     {"target": target, "ok": False, "canceled": True,
                      "generated": exc.generated, "spent": exc.spent,
                      "currency": exc.currency, "run_id": exc.run_id})
        return result
    except BaseException:
        # P0 WP4: any other exception escaping the phases still terminates the
        # run honestly (run_terminal failed) before propagating. Best-effort;
        # idempotent via the carrier flag (_finish_run/_cancel_check may have
        # already emitted).
        rid = _run_info.get("run_id")
        if rid and not _run_info.get("terminal_emitted"):
            try:
                from .attempts import append_run_terminal

                append_run_terminal(project, rid, status="failed", actor=actor)
            except Exception:
                pass
        raise

    _attach_failures(project, result, build_start)
    return result


def _attach_failures(project: Project, result: BuildResult, since_ts: str) -> None:
    """Fill ``result.failures`` from reports/failures.jsonl for the AI path.

    Reads back everything recorded at or after this build's start — including the
    provider-level records dropped deep in the generation stack (base/comfyui/
    local_cmd), which the build phases never see directly — and hands the AI a
    compact newest-first list of ids + one-liners. Best-effort (§3)."""
    try:
        from ..core.failures import read_failures

        result.failures = [
            {"id": r.get("id"), "level": r.get("level"), "step": r.get("step"),
             "subject": r.get("subject"), "cause": r.get("cause")}
            for r in read_failures(project, n=500)
            if (r.get("ts") or "") >= since_ts
        ]
    except Exception:
        pass


def _run_build_phases(
    project: Project,
    *,
    target: str = "final",  # proxy | final | exports | qc | audition
    gen: str = "missing",  # missing | auto | off
    regen_stale: bool = False,
    dry_run: bool = False,
    force: bool = False,  # FIX-A: re-render even when the content key matches
    actor: str = "engine",
    assume_yes: bool = False,  # §8.3 ask_before gate (spend cluster)
    mode: str | None = None,  # goal 14: build mode name/flag (None = engine default)
    on_phase=None,  # advisory coarse-progress callback (spend cluster)
    include_unindexed: bool = False,  # review #5: opt back into pre-round-W behaviour
    should_cancel: "Callable[[], bool] | None" = None,  # goal: honest job cancellation
    lang: str | None = None,  # WP4 locale
    agent_profile: str = "collaborative",  # AI_IDE_16 §10 keyframe spend gate
    _run_info: dict | None = None,  # P0 WP4: run_build's lifecycle carrier (internal)
) -> BuildResult:
    result = BuildResult()
    # WP3 run-evidence: one run id per build invocation, minted before any
    # phase runs. Evidence-only — it must NEVER enter a content key/hash; it is
    # threaded to the render's key sidecar (correlating a produced final to its
    # run) and stamped on this build's events.jsonl "build" record.
    from datetime import datetime as _dt
    from datetime import timezone as _tz
    from uuid import uuid4 as _uuid4
    run_id = f"run_{_dt.now(_tz.utc):%Y%m%d_%H%M%S}_" + _uuid4().hex[:6]
    # DR03C run-evidence: one context per build invocation, created next to the
    # run_id mint. Evidence-only and default-inert — a DRY RUN is a plan, so it
    # creates NO context and emits NO attempts (evidence stays None); every
    # emission point below is guarded on `evidence is not None`. Wraps existing
    # flow only; when None, behaviour is byte-identical to before DR03C.
    from .attempts import (
        RunEvidence,
        append_run_started,
        append_run_terminal,
        materialize_run_manifest,
    )
    evidence = None if dry_run else RunEvidence(
        project, run_id, actor=actor,
        # what invocation this run IS — lands on the run-level terminal and
        # top-level in the manifest, so a run answers "which command/target/
        # mode produced me" (contract §6.1 command/target/mode).
        run_context={"command": "build", "target": target, "gen": gen,
                     "mode": mode or "balanced"})
    # P0 WP4 run lifecycle: run_started lands at the run's REAL start (the
    # run_id mint, after the build lock); run_terminal lands on EVERY exit
    # (see _finish_run / _cancel_check / run_build's exception guard). All
    # best-effort — telemetry never blocks a build. ``lifecycle`` doubles as
    # run_build's carrier so a hard exception can still emit run_terminal
    # (a hard KILL emits nothing — exactly what INCOMPLETE detects).
    lifecycle = _run_info if _run_info is not None else {}
    lifecycle["run_id"] = run_id
    lifecycle["terminal_emitted"] = evidence is None  # dry-run: no lifecycle
    if evidence is not None:
        append_run_started(project, run_id, target=target, gen=gen, actor=actor)

    def _emit_run_terminal(status: str) -> None:
        """Emit the ONE run_terminal for this run (idempotent, best-effort)."""
        if lifecycle.get("terminal_emitted"):
            return
        lifecycle["terminal_emitted"] = True
        append_run_terminal(
            project, run_id, status=status,
            counts={"generated": len(result.generated),
                    "errors": len(result.errors),
                    "warnings": len(result.warnings)},
            actor=actor)
    # goal: honest job cancellation — running total of what THIS build has
    # actually spent so far, updated as each shot's takes commit (see
    # _commit_one below). Used only to word the cancellation message
    # honestly (已花费 X); advisory, like every other spend figure in this
    # module — a bookkeeping miss must never fail the build.
    spent_so_far: dict[str, Any] = {"total": 0.0, "currency": None}

    def _materialize() -> None:
        """Derive + write reports/runs/<run_id>/run.json. Best-effort: a
        materialization failure NEVER breaks the build — it degrades to a
        warning (the manifest is a derived, deletable projection, §3)."""
        if evidence is None:
            return
        try:
            materialize_run_manifest(project, run_id)
        except Exception as exc:
            result.warnings.append(f"run manifest not materialized: {exc}")

    def _finish_run(res: "BuildResult") -> "BuildResult":
        """Emit the SINGLE run-level terminal attempt (state derived from the
        result), materialize the manifest, and stamp result.run_id — then return
        the result unchanged. Called at every non-dry-run return so exactly one
        run attempt lands per build; idempotent (RunEvidence guards a second
        emission), so a canceled build that already emitted CANCELED is not
        double-counted."""
        if evidence is None:
            return res  # dry-run: a plan, no evidence
        res.run_id = run_id
        # surface any evidence-append warnings (e.g. test 18: append failed after
        # a media commit → warn, never delete media)
        res.warnings.extend(evidence.warnings)
        evidence.warnings.clear()
        if res.waiting_user:
            evidence.run_waiting_user()
            _emit_run_terminal("waiting_user")
        elif not res.ok:
            evidence.run_failed()
            _emit_run_terminal("failed")
        else:
            evidence.run_succeeded()
            # "completed" verbatim: the manifest derivation promotes to
            # COMPLETED_WITH_WARNINGS when failure attempts are on record.
            _emit_run_terminal("completed")
        res.warnings.extend(evidence.warnings)
        evidence.warnings.clear()
        _materialize()
        return res

    def _cancel_check(where: str, *, remote_pending: bool = False,
                      force: bool = False) -> None:
        """Raise :class:`BuildCanceled` iff ``should_cancel()`` trips. The one
        choke point every checkpoint below calls, so the honest message shape
        (what was generated, what was spent, whether a remote job may still
        be billing) is built in exactly one place.

        ``force=True`` (P1-7): raise from an *already observed* cancel
        (e.g. ``gen_canceled`` / ``ProviderCanceled``) without re-sampling
        the predicate — a non-sticky flag must not let the build continue
        with generation holes.
        """
        if not force and (should_cancel is None or not should_cancel()):
            return
        total = spent_so_far["total"]
        currency = spent_so_far["currency"]
        cost_str = f"{total:g}" + (f" {currency}" if currency else "")
        parts = [
            f"已取消({where})",
            f"{len(result.generated)} 个产物已完成并缓存(内容寻址,可复用)",
            f"已花费 {cost_str}",
        ]
        if remote_pending:
            parts.append(
                "该镜头的远程任务可能仍在进行并计费,job id 已记录(§8.1),"
                "后续构建会恢复轮询而不是重新提交"
            )
        # DR03C: the CANCELED run attempt is emitted HERE (the RunEvidence lives
        # in this scope), then the manifest is materialized, before the exception
        # unwinds to run_build. run_id rides the exception so the freshly minted
        # canceled BuildResult there can carry it.
        if evidence is not None:
            evidence.run_canceled(outputs=[{"role": "generated_count",
                                            "count": len(result.generated)}],
                                  decision={"remote_may_continue": bool(remote_pending)})
            _emit_run_terminal("canceled")  # P0 WP4: canceled exits terminate too
            result.warnings.extend(evidence.warnings)
            evidence.warnings.clear()
            _materialize()
        raise BuildCanceled("; ".join(parts), generated=list(result.generated),
                            spent=total, currency=currency, run_id=run_id)

    def _phase(name: str) -> None:
        """Coarse progress for watchers, AND the "between phases" cancel
        checkpoint (goal: honest job cancellation) — checked first so a
        cancellation trip is reported before any (possibly misleading)
        progress update for the phase that will never run."""
        _cancel_check(f"阶段 phase={name}")
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
        _record(project, "check", "project", "check 未通过,构建被拦下(§5 安全网)",
                evidence="\n".join(str(e) for e in report.errors[:8]),
                hint="逐条修复上面的 check 错误后重跑 manju build", actor=actor,
                detail={"errors": list(report.errors)})
        return _finish_run(result)
    result.warnings.extend(report.warnings)

    # review #5: index order is the order authority — a shot on disk but not
    # in shots/index.yaml never enters the plan or the compiled timeline
    # unless include_unindexed opts back in (check still WARNS about it).
    statuses = evaluate_all(project, indexed_only=not include_unindexed)
    result.stale = [s.shot_id for s in statuses if s.state == ShotState.STALE]
    if not statuses:
        result.ok = False
        result.errors.append(
            "no shots found — the creative stage (brief → script → shots) is agent/human "
            "work; the engine never invents it (§2)"
        )
        return _finish_run(result)

    config = project.load_config()

    # ---- 1a. build mode knobs (goal 14): --mode flag > project.yaml build.mode >
    # None. A None mode is the byte-identical default path (serial generation, no
    # routing bias, today's retry budget). A bad mode value is a clean, early
    # build error — never a traceback and never a partial spend. Resolved BEFORE
    # the plan/savings pricing below (moved up, #7) so both price the SAME
    # routing-biased provider execution will actually pick, not a bias-blind
    # fallback head.
    from .modes import BuildModeError, knobs_for, resolve_mode
    try:
        mode_knobs = knobs_for(resolve_mode(mode, config))
    except BuildModeError as exc:
        result.ok = False
        result.errors.append(f"build --mode: {exc}")
        return _finish_run(result)
    else_bias = mode_knobs.else_bias if mode_knobs else None

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
            priced_shot = _routed_shot_for_pricing(project, shot, else_bias=else_bias)
            cost, _currency = _estimate_shot_cost(
                priced_shot, _target_duration_ms(project, shot, rules))
            result.saved_cost += cost
    except Exception:  # savings are advisory; the skipped list still stands
        pass

    # ---- 1. generation plan (video takes + voice takes, both priced §8.3):
    # priced via the SAME routing.resolve() (incl. tier/mode bias) the generate
    # phase below will use, so the estimate and the ask_before/budget gates see
    # the provider that will actually run first (#7).
    # WP2 audition: voice only — never plan/spend video generation.
    # WP4 lang: video plan empty (shared segments); locale voice only.
    if target in ("audition", "animatic"):
        # WP2/WP6: a preview render — voice only, never plan/spend paid video
        # (the animatic uses EXISTING keyframe stills + kenburns pan/hold).
        result.plan = _plan_voice(project, gen=gen)
    elif lang:
        from .locale_build import plan_locale_voice
        result.plan = plan_locale_voice(project, lang, gen=gen)
        result.warnings.append(
            f"locale build --lang {lang}: picture generation skipped "
            "(shared segment cache); only locale voice planned"
        )
    else:
        result.plan = _plan_generation(project, statuses, gen=gen, regen_stale=regen_stale,
                                       else_bias=else_bias)
        result.plan += _plan_voice(project, gen=gen)
    result.estimated_cost = sum(p["estimated_cost"] for p in result.plan)

    # ---- AI_IDE_16 §10 keyframe spend gate (the batch's teeth). A shot about
    # to submit a PAID video whose keyframe candidates are not adopted is
    # flagged. COLLABORATIVE (default): an advisory warning, surfaced in the
    # dry-run plan too — never a hard block (a human may skip the ladder).
    # UNATTENDED: a structured refusal below (transport 0). Empty for any
    # project with no keyframe candidates → byte-identical.
    gated_kf_shots = _keyframe_gated_video_shots(project, gen=gen)
    if gated_kf_shots:
        gated_set = set(gated_kf_shots)
        for p in result.plan:
            if p.get("shot") in gated_set and p.get("kind") != "voice":
                p["keyframe_not_adopted"] = True
        result.warnings.append(
            "keyframe ladder(§10): 未采纳关键帧即将付费生成视频 — "
            f"{', '.join(gated_kf_shots)};建议先采纳一个关键帧"
            "(select 该 take 或提升为 first-frame ref)。unattended 档会拒绝。"
        )

    budget = config.budget.limit
    if budget is not None and result.estimated_cost > budget:
        result.ok = False
        result.errors.append(
            f"estimated cost {result.estimated_cost} exceeds budget {budget} — "
            "预算熔断 budget breaker (§8.3): raise project.yaml budget.limit or shrink the plan"
        )
        return _finish_run(result)

    if dry_run:
        # WP3 p7: keep the machine output project-relative, mirroring the
        # real-build branch's `project.relpath(tl_path)` below — a dry-run's
        # to_dict() (and the MCP build tool that returns it) must not leak an
        # absolute filesystem path.
        result.timeline_path = project.relpath(project.timeline_path)
        return _finish_run(result)

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
        return _finish_run(result)

    # ---- 1c. AI_IDE_16 §10 the teeth: under the UNATTENDED profile a paid
    # video for an UNADOPTED keyframe is a STRUCTURED REFUSAL, before any
    # submission (transport 0). It rides beside the ask_before gate but is
    # INDEPENDENT of assume_yes — a confirmed director proposal (assume_yes=True)
    # still cannot let unattended automation skip the ladder; a human confirms
    # the SPEND, the ladder is a separate discipline. Collaborative keeps only
    # the advisory warning above (never blocks a human).
    if agent_profile == "unattended" and gated_kf_shots:
        result.ok = False
        result.errors.append(
            "KEYFRAME_NOT_ADOPTED: unattended 档拒绝为未采纳关键帧的镜头提交付费视频 — "
            f"{', '.join(gated_kf_shots)};先采纳一个关键帧(select 该 take,或把它提升为 "
            "first-frame ref 绑定),或改由 collaborative 人工路径确认(§10)。"
            "未发生任何 transport / 花费(transport=0)。"
        )
        return _finish_run(result)

    _phase("generate")
    # ---- 2c. cache hits (DR03C): a shot whose selected take is already usable
    # (FRESH cache hit, or a MANUAL human import) skips generation entirely —
    # record one SKIPPED_CACHE_HIT attempt per such shot so the evidence stream
    # accounts for EVERY shot, not only the (re)generated ones. This is exactly
    # the plan's ``result.skipped`` set. Identity is bound by take NAME +
    # spec_hash only — deliberately NO media re-hash (hashing every fresh take on
    # every build is unacceptable I/O; the render attempt below carries the
    # content-key binding for the final). Dry-run / no-evidence → skipped.
    if evidence is not None:
        for st in statuses:
            if st.state not in (ShotState.FRESH, ShotState.MANUAL) or not st.selected_take:
                continue
            out = {"role": "take", "take": st.selected_take, "spec_hash": st.spec_hash}
            if st.take is not None and st.take.media_path is not None:
                out["path"] = project.relpath(st.take.media_path)
            evidence.attempt(
                "generate", {"kind": "shot", "shot": st.shot_id}, "cache_hit",
            ).skipped_cache_hit(outputs=[out])
    # ---- 2. generate (fill gaps only; stale/manual are respected, §4.3)
    #
    # Concurrency (goal 14): with NO build mode ``mode_knobs`` is None → the loop
    # runs SERIALLY, generate-then-commit per shot, byte-identically to the
    # pre-modes engine. A mode raises max_workers > 1, and the provider calls run
    # in a bounded thread pool while the ORDER-DEPENDENT commit (events, ledger,
    # result mutation) is still applied strictly in plan order after a gather —
    # so results are deterministic regardless of who finished first. The three
    # spend invariants are preserved: the ask_before gate above already ran
    # BEFORE any submission; a running-actual-cost budget trip stops NEW
    # submissions (below); and the build_lock/WaitingUser semantics are untouched
    # (this whole body already runs under the caller's lock). Each shot writes to
    # its OWN take directory, so parallel register_take never collides; the only
    # shared write left inside a worker is a cloud provider's best-effort ledger
    # self-record (already swallow-guarded, §3).
    video_plan = [p for p in result.plan if p.get("kind") != "voice"]
    voice_plan = [p for p in result.plan if p.get("kind") == "voice"]
    # shot_id -> pre-flight estimate, persisted with each recorded run so
    # `spend` can show estimate-vs-actual (§8.3 事前 vs 事后).
    estimates = {p["shot"]: p["estimated_cost"] for p in video_plan}
    if video_plan:
        from ..providers.base import (
            GenerationRequest,
            NeedsHumanInput,
            ProviderCanceled,
            ProviderFailure,
        )
        from ..providers.refs import ref_reliability_notes, resolve_refs
        from ..providers.registry import fallback_chain, generate_with_fallback

        bible = project.load_bible()
        gen_bias = mode_knobs.else_bias if mode_knobs else None
        gen_retries = mode_knobs.retries if mode_knobs else None
        max_workers = mode_knobs.max_workers if mode_knobs else 1

        def _gen_one(item: dict) -> dict:
            """Worker: build the request and run the provider fallback for ONE
            shot. Pure w.r.t. the shared BuildResult — it only reads project truth
            and writes this shot's own take dir; all order-dependent bookkeeping
            is deferred to ``_commit_one``. Never raises: a provider failure is
            captured and committed like the serial path did."""
            shot = project.load_shot(item["shot"])
            st = next(s for s in statuses if s.shot_id == shot.id)
            params = dict(shot.generation.params)
            req = GenerationRequest(
                project=project,
                shot=shot,
                bible=bible,
                spec_hash=st.spec_hash,
                duration_ms=item["duration_ms"],
                candidates=item["candidates"],
                params=params,
                # §8.3 事前: thread the plan's per-shot estimate onto the request
                # so a CLOUD provider records it on its own ledger row (local
                # runs get it via _record_local_runs below). Same figure the
                # `estimates` map holds — one source, both provider kinds.
                estimated_cost=item["estimated_cost"],
                # goal 14: mode routing bias + retry budget for this call (both
                # None when no mode → byte-identical request).
                routing_bias=gen_bias,
                max_retries=gen_retries,
                # Reference inputs (goal item 7): resolve ONCE here so tiering +
                # lineage are shared by whichever provider the fallback picks.
                refs=resolve_refs(project, shot, bible, params=params),
                # goal: honest job cancellation — a cloud provider's poll loop
                # (CloudProvider._poll_to_completion) observes this between
                # sleeps and stops WAITING (never mid-HTTP-call) via
                # ProviderCanceled, caught below like any other terminal
                # outcome so this function still never raises.
                should_cancel=should_cancel,
                # DR03C: the run-evidence context (None on dry-run) so the
                # registry emits ONE stage_attempt per provider try. Absent →
                # registry emits nothing (byte-identical).
                evidence=evidence,
            )
            chain = fallback_chain(shot)
            canceled = False
            try:
                takes = generate_with_fallback(req, chain)
                exc: BaseException | None = None
            except (ProviderFailure, NeedsHumanInput) as e:
                takes, exc = None, e
            except ProviderCanceled as e:
                # Not a failure (§8.1: the remote job may still complete and
                # bill; its id is already persisted for resume) — the caller
                # (the loop below / _concurrent_generate) treats this shot as
                # "stopped, not spent-on-nothing" and raises BuildCanceled at
                # the next checkpoint rather than recording a failure.
                takes, exc = None, e
                canceled = True
            actual = 0.0
            for t in (takes or []):
                remote = t.sidecar.remote
                if remote and remote.cost:
                    actual += remote.cost
            return {"item": item, "shot": shot, "req": req, "chain": chain,
                    "takes": takes, "exc": exc, "actual_cost": actual,
                    "canceled": canceled}

        def _commit_one(res: dict) -> None:
            """Serial, order-dependent commit for one generated shot — the exact
            side effects the pre-modes loop applied inline, unchanged."""
            item, shot, req, chain = res["item"], res["shot"], res["req"], res["chain"]
            takes, exc = res["takes"], res["exc"]
            if res.get("canceled"):
                # goal: honest job cancellation — a ProviderCanceled stopped
                # this shot's poll loop from waiting; it produced NO take, so
                # there is nothing to commit, and it is NOT a failure (§8.1:
                # the remote job may still complete/bill; its id already
                # persisted for resume). The next _cancel_check call raises.
                return
            if exc is not None:
                result.warnings.append(f"{shot.id}: generation failed — {exc}")
                # Every provider (incl. the caption_card terminal) failed: this
                # shot has NO usable take. The per-provider attempts were already
                # recorded (base/comfyui/local_cmd); this records the shot-level
                # outcome so the AI/human sees "S00x produced nothing" directly.
                attempts = getattr(exc, "detail", {}).get("attempts") if isinstance(
                    getattr(exc, "detail", None), dict) else None
                ev = "\n".join(f"[{a['provider']}] {a['error']}" for a in attempts) \
                    if attempts else " ".join(str(exc).split())
                _record(project, "generate", shot.id,
                        "所有供应商都失败,该镜头没有可用镜头",
                        evidence=ev,
                        hint="看每个供应商的失败原因(manju failures);"
                             "或改 generation.provider / 用 caption_card 兜底",
                        actor=actor)
                return
            # Degradation (goal 10): the take came from a FALLBACK, not the shot's
            # first choice — record it in the SAME shape at level=info so
            # "为什么这个镜头变成了字幕卡?" is answerable from the record alone.
            effective = takes[0].sidecar.provider if takes else None
            first_choice = shot.generation.provider or (chain[0] if chain else None)
            if effective and first_choice and effective != first_choice:
                _record(project, "generate", shot.id,
                        f"降级:首选 {first_choice} 未产出,改用 {effective}",
                        hint="同镜头的 generate 失败记录说明了首选为何失败(manju failures)",
                        level="info", actor=actor,
                        detail={"first_choice": first_choice, "effective": effective})
            result.generated.extend(f"{shot.id}/{t.name}" for t in takes)
            # reliability (goal item 7): a declared ref dropped by the chosen
            # provider (image_mode: none) or delivered as zero (tier mismatch)
            # surfaces here so silent ref-dropping is impossible.
            result.warnings.extend(ref_reliability_notes(shot.id, req.refset(), takes))
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
            _record_local_runs(project, takes, {"reason": item["reason"]}, estimates,
                               evidence=evidence)
            # goal: honest job cancellation — running spend for THIS build, so
            # a checkpoint that trips right after this shot can say exactly
            # what has already been spent (§8.3 honesty, not just §80's).
            spent_so_far["total"] += float(res.get("actual_cost", 0.0) or 0.0)
            if spent_so_far["currency"] is None:
                spent_so_far["currency"] = next(
                    (t.sidecar.remote.currency for t in takes
                     if t.sidecar.remote and t.sidecar.remote.currency), None)

        if max_workers <= 1:
            # serial — generate then commit per shot, in plan order. The
            # cancel checkpoint sits BEFORE each new submission (goal: honest
            # job cancellation, "between per-shot generation submissions") —
            # should_cancel=None (the default) makes _cancel_check a no-op.
            # R2-P1-1: mid-run budget.limit trip (parity with concurrent path)
            # — check BEFORE each new submission against actual spent_so_far.
            serial_budget_skipped: list[str] = []
            for item in video_plan:
                if budget is not None and spent_so_far["total"] > float(budget):
                    serial_budget_skipped.append(item["shot"])
                    continue
                _cancel_check(f"生成:{item['shot']}(尚未开始)")
                res = _gen_one(item)
                _commit_one(res)
                if res.get("canceled"):
                    # This shot's own poll loop stopped waiting — raise now
                    # rather than waiting for the next iteration's pre-check.
                    # force=True: do not re-sample should_cancel() (P1-7).
                    _cancel_check(f"生成:{item['shot']}", remote_pending=True,
                                  force=True)
            if serial_budget_skipped:
                result.warnings.append(
                    f"预算已到上限:不再提交新任务(串行生成:实际花费 "
                    f"{spent_so_far['total']} 已超 budget.limit={budget},§8.3)。"
                    f"不再提交的镜头 {', '.join(serial_budget_skipped)} "
                    "— 已生成的镜头不受影响;提高 budget.limit 或缩减计划后重跑"
                )
                _record(project, "generate", "budget",
                        "串行生成触发预算熔断,未提交的镜头被跳过",
                        evidence=(
                            f"running_cost={spent_so_far['total']} > budget={budget}; "
                            f"skipped={serial_budget_skipped}"
                        ),
                        hint="提高 project.yaml budget.limit 或缩减计划",
                        level="info", actor=actor)
        else:
            # #8: which provider's manifest max_concurrent should gate each
            # shot's worker slot — resolved the SAME way generate_with_fallback
            # will pick a head (routing when active, else the explicit
            # provider, else the §8.4 chain's first entry). Precomputed
            # serially (cheap: no network) before the pool starts.
            head_provider_by_shot: dict[str, str | None] = {}
            for item in video_plan:
                try:
                    hshot = project.load_shot(item["shot"])
                    head_provider_by_shot[item["shot"]] = _resolve_head_provider(
                        project, hshot, gen_bias)
                except Exception:
                    head_provider_by_shot[item["shot"]] = None

            # bounded parallel generation; commit deterministically in plan order.
            done, tripped, gen_canceled, running, in_flight_at_trip = _concurrent_generate(
                video_plan, _gen_one, max_workers=max_workers, budget_limit=budget,
                head_provider=lambda it: head_provider_by_shot.get(it["shot"]),
                should_cancel=should_cancel)
            for item in video_plan:
                res = done.get(item["shot"])
                if res is not None:
                    _commit_one(res)
            if gen_canceled:
                # goal: honest job cancellation — cancel already observed
                # (either between submissions or inside one shot's poll loop);
                # every already-committed result above is real (generated +
                # cached), everything else was never submitted or never billed.
                # force=True: do not re-sample should_cancel() (P1-7).
                remote_pending = any(bool(r.get("canceled")) for r in done.values())
                _cancel_check("生成(并发提交已停止)", remote_pending=remote_pending,
                              force=True)
            if tripped:
                unsub = [it["shot"] for it in video_plan if it["shot"] not in done]
                # §80 honesty: the trip stops NEW submissions only — shots already
                # in flight at that moment keep running and WILL be billed. Say so
                # in the gate's own output, not just in code comments.
                inflight_note = (
                    f";进行中的 {len(in_flight_at_trip)} 个镜头仍会完成并计费"
                    f"({', '.join(in_flight_at_trip)})"
                ) if in_flight_at_trip else "(没有仍在进行中的镜头)"
                result.warnings.append(
                    f"预算已到上限:不再提交新任务(并发生成:实际花费 {running} "
                    f"已超 budget.limit={budget},§8.3)。不再提交的镜头 "
                    + ", ".join(unsub or ["(无)"]) + inflight_note +
                    " — 已生成的镜头不受影响;提高 budget.limit 或缩减计划后重跑")
                _record(project, "generate", "budget",
                        "并发生成触发预算熔断,未提交的镜头被跳过",
                        evidence=f"running_cost={running} > budget={budget}; skipped={unsub}; "
                                 f"in_flight_at_trip={in_flight_at_trip}",
                        hint="提高 project.yaml budget.limit 或缩减计划;并发上限由 --mode 决定",
                        level="info", actor=actor)

    # ---- 2v. voice synthesis (M3): fill MISSING voices via the configured
    # TTS manifest; stale voices are flagged, never redone (§4.3). Synthesis
    # happens BEFORE the timeline compile so fresh voices drive durations (§6)
    # within the same build. WP4: locale plan rows register under locales/<lang>/.
    if voice_plan:
        _phase("voice")
        if lang:
            from .locale_build import synthesize_locale_voices
            # R2-P0-1: run_build already holds build_lock — do not re-acquire.
            try:
                gen_paths = synthesize_locale_voices(
                    project, voice_plan, lang=lang, actor=actor,
                    hold_lock=False,
                )
                result.generated.extend(gen_paths)
            except Exception as exc:
                result.ok = False
                result.errors.append(
                    f"locale voice batch failed ({lang}): "
                    + " ".join(str(exc).split())[:500]
                )
                _record(project, "voice", lang or "locale",
                        f"locale 配音失败({lang})",
                        evidence=" ".join(str(exc).split())[:800],
                        hint=f"manju voice <shot> --lang {lang} --yes",
                        actor=actor)
                return _finish_run(result)
            # R2-P1-4: plan non-empty → every planned shot must have a locale take.
            missing_loc = [
                p["shot"] for p in voice_plan
                if not project.voice_takes(p["shot"], lang=lang)
            ]
            if missing_loc:
                result.ok = False
                result.errors.append(
                    f"locale {lang}: 配音计划未全部落地 — 缺失 "
                    f"{', '.join(missing_loc)} (拒绝带着母语音频出外语成片)"
                )
                _record(project, "voice", lang or "locale",
                        "locale 配音计划未全部落地",
                        evidence=f"missing={missing_loc}",
                        hint=f"manju voice --missing --lang {lang} --yes",
                        actor=actor)
                return _finish_run(result)
        else:
            from ..providers.base import ProviderFailure as _PF
            from ..providers.tts import get_tts_provider

            bible = project.load_bible()
            for item in voice_plan:
                # goal: honest job cancellation — same "between per-shot
                # submissions" checkpoint as the video generation loop above;
                # should_cancel=None (default) makes this a no-op.
                _cancel_check(f"配音:{item['shot']}(尚未开始)")
                shot = project.load_shot(item["shot"])
                try:
                    tts = get_tts_provider(item["provider"])
                    media = tts.synthesize(project, shot, bible)
                except (_PF, Exception) as exc:
                    result.warnings.append(f"{shot.id}: voice synthesis failed — {exc}")
                    _record(project, "voice", shot.id,
                            f"配音合成失败({item['provider']})",
                            evidence=" ".join(str(exc).split())[:800],
                            hint="确认 TTS manifest/密钥;或用 manju voice 手动补配音",
                            actor=actor, detail={"provider": item["provider"]})
                    continue
                result.generated.append(f"{shot.id}/{media.stem}")
                append_event(project.root, actor, "voice",
                             {"shot": shot.id, "take": media.stem,
                              "provider": item["provider"]})
    # C1: locale text without locale voice must not silently compile to 母语成片
    # (empty voice_plan when TTS absent previously skipped R2-P1-4 hard-fail).
    if lang and gen != "off" and target in ("final", "exports", "qc", "proxy"):
        from ..core.locale import load_lines

        lines = load_lines(project, lang)
        need_voice = [
            sid for sid in project.shot_ids()
            if str((lines.get(sid) or {}).get("text") or "").strip()
            and not project.voice_takes(sid, lang=lang)
        ]
        if need_voice:
            result.ok = False
            result.errors.append(
                f"locale {lang}: 有译文无配音 — {', '.join(need_voice)} "
                "(拒绝母语音频+外语字幕假本地化;配置 TTS 后 manju voice --missing "
                f"--lang {lang} --yes,或填齐 locale takes)"
            )
            _record(project, "voice", lang or "locale",
                    "locale 有译文无配音(计划为空或 TTS 不可用)",
                    evidence=f"need_voice={need_voice}",
                    hint=f"配置 TTS 后 manju voice --missing --lang {lang} --yes",
                    actor=actor)
            return _finish_run(result)
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
    # is allowed; overturning a selection never is). Round W (#28/#39): goes
    # through the SAME checked write every other select entrance uses — a
    # value-hash lock on status/selected_take (sealed empty, before any take
    # existed) must skip auto-select too, loudly, not just a silent pass.
    # No build_lock here on purpose: this whole phase already runs inside
    # run_build's build_lock (not reentrant) — see core/writes.py.
    from ..core.writes import WriteRejected, select_take_checked

    for st in evaluate_all(project, indexed_only=not include_unindexed):
        if st.state == ShotState.NEEDS_SELECTION:
            takes = project.takes(st.shot_id)
            # AI_IDE_16 §5: a keyframe (IMAGE) candidate is NEVER auto-adopted as
            # selected_take — the contract forbids auto-setting a candidate as
            # selected, and an image is not a video deliverable for the timeline.
            # A human/agent adopts a keyframe explicitly (select / promote-to-ref).
            usable = [t for t in takes if t.media_path is not None
                      and t.media_path.suffix.lower() not in _LADDER_IMAGE_EXTS]
            if usable:
                choice = usable[-1].name
                try:
                    select_take_checked(
                        project, st.shot_id, choice, actor=actor,
                        via="build_auto_select", action="auto_select",
                    )
                except WriteRejected as exc:
                    result.warnings.append(
                        f"{st.shot_id}: auto-select SKIPPED (locked/rejected) — {exc}"
                    )
                    _record(project, "check", st.shot_id,
                            "auto-select 跳过(selected_take 已锁定或写后校验失败)",
                            evidence=str(exc), hint=f"人工确认后 `manju select {st.shot_id} "
                            f"{choice}`,或 `manju unlock {st.shot_id} status.selected_take`",
                            level="info", actor=actor)
                    continue
                result.warnings.append(
                    f"{st.shot_id}: auto-selected {choice} (no human selection existed)"
                )

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
            _record(project, "package", "packaging_card",
                    "片头/片尾卡渲染被跳过(降级)",
                    evidence=" ".join(str(exc).split())[:600],
                    hint="QC 会提示缺失资产;检查 packaging.yaml 与卡片模板",
                    level="info", actor=actor)

    _phase("compile")
    # ---- 3. compile timeline (pure function, §6)
    from ..media.probe import probe_duration_ms

    # WP2 audition: compile in-memory with slate placeholders for missing
    # takes; NEVER write timeline.json (the real timeline stays gated).
    # WP4 lang: in-memory locale overlay on base structure; never write base
    # timeline.json.
    if target in ("audition", "animatic"):
        from ..timeline.compiler import compile_timeline, gather_compile_input

        try:
            cinp = gather_compile_input(
                project, probe_duration_ms,
                include_unindexed=include_unindexed,
                allow_missing_takes=True,
                lang=lang,
            )
            timeline = compile_timeline(cinp)
        except CompileError as exc:
            result.ok = False
            result.errors.append(str(exc))
            _record(project, "compile", "timeline", f"{target} 时间线编译失败",
                    evidence=" ".join(str(exc).split())[:800],
                    hint="先 manju voice --missing 合成配音;预览不需要画面视频 takes",
                    actor=actor)
            return _finish_run(result)
        result.timeline_path = None  # not written
        result.warnings.append(
            f"{target}: in-memory timeline (timeline.json untouched); "
            "missing picture takes use slate/keyframe placeholders"
        )
    elif lang:
        from ..timeline.compiler import compile_timeline, gather_compile_input
        from .locale_build import apply_locale_overlay

        try:
            # Prefer existing base timeline (picture windows fixed); else compile
            base_tl = project.load_timeline()
            if base_tl is None:
                cinp = gather_compile_input(
                    project, probe_duration_ms,
                    include_unindexed=include_unindexed, lang=None,
                )
                base_tl = compile_timeline(cinp)
            timeline = apply_locale_overlay(project, base_tl, lang)
            # Also recompile with lang for caption text if base had no captions
            if not timeline.tracks.captions:
                cinp = gather_compile_input(
                    project, probe_duration_ms,
                    include_unindexed=include_unindexed, lang=lang,
                )
                timeline = compile_timeline(cinp)
        except CompileError as exc:
            result.ok = False
            result.errors.append(str(exc))
            _record(project, "compile", "timeline", f"locale({lang}) 编译失败",
                    evidence=" ".join(str(exc).split())[:800],
                    hint=f"确认 locales/{lang}/lines.yaml 与 locale 配音",
                    actor=actor)
            return _finish_run(result)
        result.timeline_path = None
        result.warnings.append(
            f"locale --lang {lang}: in-memory overlay (base timeline.json untouched); "
            "video segments reused from shared cache"
        )
    else:
        try:
            timeline, tl_path, overwrote = build_timeline(
                project, probe_duration_ms, include_unindexed=include_unindexed
            )
        except CompileError as exc:
            result.ok = False
            result.errors.append(str(exc))
            _record(project, "compile", "timeline", "时间线编译失败",
                    evidence=" ".join(str(exc).split())[:800],
                    hint="按报错定位镜头/时长/字幕规则(§6);见 timeline/rules.yaml",
                    actor=actor)
            return _finish_run(result)
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
                _record(project, "compile", "timeline",
                        "手工 timeline.json 无法解析",
                        evidence=" ".join(str(exc).split())[:600],
                        hint="修复该 JSON,或把 rules.yaml mode 改回 compiled;"
                             "对照 timeline.generated.json",
                        log_path="timeline/timeline.json", actor=actor)
                return _finish_run(result)

    # ---- 4. captions
    rules = project.load_rules()
    ass_path = None
    if rules.captions.enabled and timeline.tracks.captions:
        _phase("captions")
        if lang:
            from .locale_build import export_locale_captions

            paths = export_locale_captions(project, timeline, lang)
        else:
            from ..exporters.srt_ass import export_captions

            paths = export_captions(project, timeline)
        result.captions = {k: project.relpath(v) for k, v in paths.items()}
        ass_path = paths.get("ass")

    # ---- 5. render
    if target == "audition":
        _phase("render:audition")
        from ..media.audition import render_audition
        from ..media.ffmpeg import MediaError as _MediaError

        try:
            out = render_audition(
                project, timeline, ass_file=ass_path, force=force,
            )
        except _MediaError as exc:
            result.ok = False
            result.errors.append(
                f"audition: {' '.join(str(exc).split())} "
                "(建议:查看 .manju/logs/audition.log / render.log)"
            )
            _record(project, "render", "audition", "试听片渲染失败",
                    evidence=str(exc)[-1200:],
                    hint="查看 .manju/logs/audition.log;确认 ffmpeg 可用",
                    log_path=".manju/logs/audition.log", actor=actor)
            return _finish_run(result)
        result.render_path = project.relpath(out)
        append_event(project.root, actor, "render",
                     {"target": "audition", "output": result.render_path})
    elif target == "animatic":
        _phase("render:animatic")
        from ..media.ffmpeg import MediaError as _MediaError

        try:
            out = _render_animatic(project, timeline, ass_file=ass_path, force=force)
        except _MediaError as exc:
            result.ok = False
            result.errors.append(
                f"animatic: {' '.join(str(exc).split())} "
                "(建议:查看 .manju/logs/animatic.log / render.log)"
            )
            _record(project, "render", "animatic", "动态分镜(animatic)渲染失败",
                    evidence=str(exc)[-1200:],
                    hint="查看 .manju/logs/animatic.log;确认 ffmpeg 可用",
                    log_path=".manju/logs/animatic.log", actor=actor)
            return _finish_run(result)
        result.render_path = project.relpath(out)
        # a DERIVED artifact — never a video take, never selected (pin).
        result.warnings.append(
            "animatic: 派生预演产物(renders/animatic/),不是视频 take、不会被 "
            "selected,可删除后由关键帧重建(§6)"
        )
        append_event(project.root, actor, "render",
                     {"target": "animatic", "output": result.render_path})
    elif target in ("proxy", "final") and lang:
        # R2-P1-2: only final is namespaced under locales/<lang>/; refuse
        # proxy (would poison base proxy cache with locale audio/captions).
        if target != "final":
            result.ok = False
            result.errors.append(
                f"--lang 仅支持 --target final|qc 的字幕/成片路径;"
                f"当前 target={target!r} 会污染 base 树,已拒绝。"
                "请用 manju build --lang <lang> --target final"
            )
            return _finish_run(result)
        _phase("render:final:locale")
        from ..media.ffmpeg import MediaCanceled, MediaError as _MediaError, cancel_scope
        from ..media.render import (
            _read_key_sidecar, _write_key_sidecar, final_content_key, render_timeline,
        )
        from .locale_build import newest_locale_final, next_locale_final_path

        try:
            key = final_content_key(
                project, timeline, ass_file=ass_path, target="final",
            )
            # Fold lang into key so base/locale finals never collide
            from ..core.hashing import cache_key
            key = cache_key({"locale": lang, "base_key": key})
            newest = newest_locale_final(project, lang)
            if not force and newest is not None and _read_key_sidecar(newest) == key:
                out = newest
                result.warnings.append(
                    f"locale final up-to-date (content key match) — reused {out.name}; "
                    "video segments shared with base"
                )
            else:
                out_path = next_locale_final_path(project, lang)
                # R2-P1-3: same cancel_scope as base final render.
                try:
                    with cancel_scope(should_cancel):
                        out = render_timeline(
                            project, timeline, target="final", out_path=out_path,
                            ass_file=ass_path, force=True,
                        )
                except MediaCanceled:
                    _cancel_check(f"渲染:final:{lang}(ffmpeg 已终止)", force=True)
                    raise BuildCanceled(
                        f"已取消(渲染:final:{lang})",
                        generated=list(result.generated),
                        spent=spent_so_far["total"],
                        currency=spent_so_far["currency"],
                        run_id=run_id,
                    )
                _write_key_sidecar(out, key, f"final:{lang}")
                result.warnings.append(
                    f"locale --lang {lang}: rendered {project.relpath(out)}; "
                    "video segment cache shared with base (only ASS+audio differ)"
                )
        except BuildCanceled:
            raise
        except _MediaError as exc:
            result.ok = False
            result.errors.append(f"locale render: {' '.join(str(exc).split())}")
            _record(project, "render", f"final:{lang}", "locale 成片渲染失败",
                    evidence=str(exc)[-1200:],
                    hint="查看 .manju/logs/render.log",
                    log_path=".manju/logs/render.log", actor=actor)
            return _finish_run(result)
        result.render_path = project.relpath(out)
        append_event(project.root, actor, "render",
                     {"target": "final", "lang": lang, "output": result.render_path})
    elif target in ("proxy", "final"):
        _phase(f"render:{target}")
        from ..media.render import render_timeline
        # goal: honest job cancellation — media/render.py itself is a
        # read-only surface this round (its segment/boundary/final pipeline
        # is not touched); cancel_scope makes every run_ffmpeg call IT makes
        # transitively cancel-aware instead, so a trip kills whichever ffmpeg
        # subprocess is actually running rather than waiting for the current
        # encode to finish. should_cancel is None on the default path, so
        # this is a genuine no-op then.
        from ..media.ffmpeg import MediaCanceled, cancel_scope

        finals_before = set(project.final_dir.glob("final_v*.mp4"))
        # P0 WP4: the final render is one of the two expensive attempt families
        # — pre-mint its handle and announce attempt_started BEFORE the work, so
        # an interrupted render is a dangling attempt on an INCOMPLETE run. The
        # terminal (SUCCEEDED / SKIPPED_CACHE_HIT via _emit_render_attempt)
        # lands on the SAME attempt_id. Best-effort, one append.
        render_handle = None
        if evidence is not None:
            from .attempts import append_attempt_started
            render_handle = evidence.attempt(
                "render", {"kind": "render", "target": target}, "render")
            append_attempt_started(project, run_id, render_handle.attempt_id,
                                   stage="render", actor=actor)
        try:
            with cancel_scope(should_cancel):
                out = render_timeline(project, timeline, target=target, ass_file=ass_path,
                                      force=force, run_id=run_id)
        except MediaCanceled as exc:
            _cancel_check(f"渲染:{target}(ffmpeg 已终止)")
            # Defensive: should_cancel() is true (that is what raised
            # MediaCanceled in the first place), so _cancel_check above
            # always raises — this is unreachable in practice, but never
            # silently swallow a canceled render as a generic failure.
            raise BuildCanceled(
                str(exc), generated=list(result.generated),
                spent=spent_so_far["total"], currency=spent_so_far["currency"],
            ) from exc
        except Exception as exc:  # FIX-D: MediaError etc. -> one-line diagnostic
            from ..media.ffmpeg import MediaError

            if not isinstance(exc, MediaError):
                raise  # unknown exceptions keep their traceback
            result.ok = False
            result.errors.append(
                f"render: {' '.join(str(exc).split())} "
                "(建议:查看 .manju/logs/render.log 复现单条 ffmpeg 命令)"
            )
            # The MediaError already carries the ffmpeg stderr tail + the exact
            # command as its message; record it verbatim as evidence, pointing at
            # the fuller render log (goal 10).
            _record(project, "render", target, f"{target} 渲染失败(ffmpeg)",
                    evidence=str(exc)[-1200:],
                    hint="查看 .manju/logs/render.log 复现单条 ffmpeg 命令;核对滤镜/输入",
                    log_path=".manju/logs/render.log", actor=actor)
            return _finish_run(result)
        result.render_path = project.relpath(out)
        reused = target == "final" and out in finals_before
        if reused:
            result.warnings.append(
                f"final up-to-date (content key match) — reused {out.name}; "
                "use --force to re-render (FIX-A)"
            )
        append_event(project.root, actor, "render", {"target": target, "output": result.render_path})
        # DR03C render attempt: SUCCEEDED for a fresh render, SKIPPED_CACHE_HIT
        # when the content key matched and the existing final was reused. The
        # output sha256 + content_key are READ from the DR01 .key.json sidecar
        # (already computed at render completion) — never re-hashed here.
        if evidence is not None:
            _emit_render_attempt(evidence, project, out, target, reused=reused,
                                 handle=render_handle)

    # ---- 6. QC (§9)
    if target in ("proxy", "final", "qc"):
        _phase("qc")
        from ..qc.checks import run_qc
        from ..qc.report import write_reports

        # Round W (issue #81): target=qc deliberately skips step 5 above (no
        # render) — it is a cheap "check what's already there" pass, exactly
        # like the standalone `manju qc` command, NOT "render then check".
        # Making it imply a render would contradict that (and `target=exports`
        # skips render for the same reason: its exporters read the timeline,
        # not the composited video) — see the CLI help text for the option
        # this round took instead: name the artifact QC'd, honestly.
        if target in ("proxy", "final"):
            qc_final = result.render_path  # the render this exact call just made
        elif lang:
            # R2-P0-2: target=qc --lang must probe locale final, not base.
            from .locale_build import newest_locale_final

            final_path = newest_locale_final(project, lang)
            qc_final = project.relpath(final_path) if final_path else None
        else:
            final_path = project.newest_final_path()
            qc_final = project.relpath(final_path) if final_path else None
        result.qc_final = qc_final
        # R2-P0-2: pass explicit final so duration/res checks hit the right file.
        qc_path = None
        if qc_final:
            qc_path = project.root / qc_final if not Path(qc_final).is_absolute() else Path(qc_final)
        qc = run_qc(project, timeline, final_path=qc_path)
        result.qc_ok = qc.ok
        result.qc_reports = {k: project.relpath(v) for k, v in write_reports(project, qc).items()}
        # DR03C: the run ran QC — attach the qc report refs to the run-level
        # evidence so they surface as the manifest's qc_report_refs.
        if evidence is not None:
            for _rel in result.qc_reports.values():
                evidence.add_evidence_ref(_rel)
        # QC gate (goal 10): a non-passing QC does not abort the build (§9), but
        # the reason should be one click away. Record it in the same shape, level
        # info, pointing at the qc report the human/AI reads next.
        if qc.ok is False:
            issues = [i for i in getattr(qc, "items", []) if getattr(i, "level", None) == "error"]
            _record(project, "qc", "final",
                    f"QC 未通过:{len(issues)} 处错误",
                    evidence="\n".join(str(getattr(i, "message", i)) for i in issues[:8]),
                    hint="见 reports/qc.md 逐条处理;必要时 manju repair",
                    log_path=result.qc_reports.get("md") or "reports/qc.md",
                    level="info", actor=actor)

    # ---- 7. draft exports
    if target in ("final", "exports"):
        # C1: locale timeline must not clobber base NLE drafts under exports/.
        if lang:
            if target == "exports":
                result.ok = False
                result.errors.append(
                    f"--lang 与 --target exports 尚未命名空间化(会覆盖 base "
                    f"exports/ 草稿),已拒绝;请用 --target final 出 locales/"
                    f"<lang>/ 成片,或无 --lang 导 base 草稿"
                )
                return _finish_run(result)
            result.warnings.append(
                f"locale --lang {lang}: 跳过 base NLE exports(otio/jianying/capcut)"
                " 以免污染共享 exports/ 树;成片已在 renders/final/locales/"
            )
        else:
            _phase("exports")
            profiles = set(config.export_profiles)
            if "otio" in profiles:
                from ..exporters.otio import export_otio

                result.exports["otio"] = project.relpath(export_otio(project, timeline))
            if "jianying" in profiles:
                from ..exporters.jianying import export_jianying

                result.exports["jianying"] = project.relpath(
                    export_jianying(project, timeline)
                )
                # dual-path (decision 8): native pyJianYingDraft draft is primary;
                # its absence is a note, never a build failure (§14 fallback exits)
                try:
                    from ..exporters.native_draft import export_jianying_native

                    result.exports["jianying_native"] = project.relpath(
                        export_jianying_native(project, timeline)
                    )
                except Exception as exc:
                    result.warnings.append(f"jianying native draft skipped: {exc}")
                    _record(project, "export", "jianying_native",
                            "剪映原生草稿导出被跳过(降级到骨架草稿)",
                            evidence=" ".join(str(exc).split())[:600],
                            hint="安装 pyJianYingDraft 可启用原生草稿;final.mp4/SRT 仍是兜底出口(§14)",
                            level="info", actor=actor)
            if "capcut" in profiles:
                try:
                    from ..exporters.native_draft import export_capcut_native

                    result.exports["capcut"] = project.relpath(
                        export_capcut_native(project, timeline)
                    )
                except Exception as exc:
                    result.warnings.append(f"capcut draft skipped: {exc}")
                    _record(project, "export", "capcut",
                            "CapCut 原生草稿导出被跳过(降级)",
                            evidence=" ".join(str(exc).split())[:600],
                            hint="安装 pycapcut 可启用;final.mp4/SRT 仍是兜底出口(§14)",
                            level="info", actor=actor)

    build_detail: dict[str, Any] = {
        "target": target, "ok": result.ok, "render": result.render_path,
        "run_id": run_id,  # WP3: ties this build to the final's key sidecar (e3)
    }
    if target == "final" and result.render_path:
        # basename of the produced final (render_path is a project-relative
        # posix string, e.g. "renders/final/final_v1.mp4")
        build_detail["final"] = result.render_path.rsplit("/", 1)[-1]
    append_event(project.root, actor, "build", build_detail)
    return _finish_run(result)


def redo_shot(project: Project, shot_id: str, *, candidates: int | None = None,
              provider: str | None = None, seed: int | None = None,
              from_take: str | None = None,
              actor: str = "engine", assume_yes: bool = False) -> list[str]:
    """`manju redo S002` — force new takes (append-only), regardless of
    staleness. Selection is only touched when the shot had none.

    Recipe-reuse (R12, Runway pattern): ``from_take`` replays a prior take's
    recorded recipe — its provider and generation params (seed included) travel
    with the output, so the ⟳ "same recipe again" gesture is an append-only
    redo, not a re-authoring. Explicit ``provider``/``seed`` still override the
    reused recipe; the shot's own selection is left untouched.

    A priced redo raises :class:`WaitingUser` unless ``assume_yes`` — the same
    §8.3 ask_before gate as ``build`` (the R7 spend-gate hole closure). Holds
    the process build lock like a full build (a redo mutates takes and the
    ledger); contention raises :class:`~manju.runtime.buildlock.BuildLocked`."""
    from ..runtime.buildlock import build_lock

    with build_lock(project.root, actor=actor):
        return _redo_shot_locked(project, shot_id, candidates=candidates,
                                 provider=provider, seed=seed,
                                 from_take=from_take, actor=actor,
                                 assume_yes=assume_yes)


@dataclass
class _RedoPlan:
    """A priced, override-applied redo, ready to run — the unit shared by the
    single redo and the batch so both price and generate off exactly one
    code path (one estimator, one selection rule, one ledger write)."""

    shot: Any  # ShotSpec, with overrides applied in-memory (never written back)
    params: dict[str, Any]
    cost: float
    currency: str | None
    candidates: int | None  # explicit --candidates override, else None
    # 08_10_12C WP3: the replayed parent take name for an explicit
    # `--from-take` redo — stamped onto the new take sidecars as ``redo_of``
    # (creative-family lineage). None for a plain redo (no single parent).
    redo_of: str | None = None


def _plan_redo(project: Project, shot_id: str, *, candidates: int | None,
               provider: str | None, seed: int | None, from_take: str | None,
               rules, bible) -> _RedoPlan:
    """Apply the redo overrides to an in-memory shot and price it (§8.3 事前).

    The overrides only ever live on the in-memory ``ShotSpec`` — a redo is
    append-only and never writes generation.* back (§4.3), so the shot file's
    sealed fields stay byte-identical. ``from_take`` replays a prior take's
    recorded recipe (R12); it is single-redo only (a per-take gesture, never a
    batch one)."""
    shot = project.load_shot(shot_id)
    params = dict(shot.generation.params)
    if from_take is not None:
        prior = project.get_take(shot_id, from_take)
        if prior is None:
            raise BuildError(f"{shot_id}: no such take to reuse: {from_take}")
        # the recipe travels with the output: replay the take's recorded params
        # (seed among them) and, unless overridden, its provider.
        params.update(prior.sidecar.params)
        if provider is None and prior.sidecar.provider not in ("", "manual_import"):
            shot.generation.provider = prior.sidecar.provider
    if seed is not None:
        params["seed"] = seed
    if provider:
        shot.generation.provider = provider
    if candidates:
        shot.generation.candidates = int(candidates)  # in-memory, for pricing
    # #7: when no explicit provider pinned the shot above, price the SAME head
    # providers.routing.resolve() would pick — a redo run with routing active
    # would otherwise be estimated off the static fallback-chain head.
    priced_shot = _routed_shot_for_pricing(project, shot)
    cost, currency = _estimate_shot_cost(priced_shot, _target_duration_ms(project, shot, rules))
    return _RedoPlan(shot, params, cost, currency, candidates, redo_of=from_take)


def _run_redo(project: Project, plan: _RedoPlan, *, bible, rules,
              actor: str) -> list[str]:
    """Generate a redo from a priced plan — no lock, no spend gate (the caller
    owns both). Append-only: mints fresh takes and only fills an *empty*
    selection, never overturning an existing one (§4.3)."""
    from ..core.spec import SPEC_VERSION, compute_spec_hash
    from ..providers.base import GenerationRequest
    from ..providers.refs import resolve_refs
    from ..providers.registry import fallback_chain, generate_with_fallback

    shot = plan.shot
    req = GenerationRequest(
        project=project,
        shot=shot,
        bible=bible,
        # round W: every FRESHLY generated take is hashed/snapshotted at the
        # current SPEC_VERSION (Provider._register stamps spec_version to
        # match) — a redo must agree with the normal build path, or the
        # sidecar's spec_hash and spec_version would describe two different
        # payload shapes.
        spec_hash=compute_spec_hash(shot, bible, version=SPEC_VERSION, project_root=project.root),
        duration_ms=_target_duration_ms(project, shot, rules),
        candidates=plan.candidates or shot.generation.candidates,
        params=plan.params,
        # reuse the estimate computed for the gate (don't recompute) so a CLOUD
        # redo records it on its ledger row too.
        estimated_cost=plan.cost,
        # refs use the request params (which on a redo carry the reused take's
        # image), matching the resolver's params-tier read (goal item 7).
        refs=resolve_refs(project, shot, bible, params=plan.params),
        # 08_10_12C WP3: explicit-redo lineage travels to the take sidecar.
        redo_of=plan.redo_of,
    )
    takes = generate_with_fallback(req, fallback_chain(shot))
    if not shot.status.selected_take and takes:
        choice = takes[-1].name
        # Round W (#28/#39): a value-hash lock can seal status/selected_take
        # even while it is still empty (locked BEFORE a first take existed);
        # this auto-fill must not silently overturn that. A lightweight
        # guard (not the full select_take_checked pipeline — this already
        # runs inside redo_shot's own build_lock, and one extra `manju
        # check` pass per redo is not free) — loud skip, never silent.
        from ..core.writes import selected_take_lock_block

        blocking = selected_take_lock_block(project, shot.id)
        if blocking is not None:
            _record(project, "check", shot.id,
                    "redo 自动填选 selected_take 被跳过(已锁定)",
                    evidence=f"locked path: {blocking}",
                    hint=f"人工确认后 `manju select {shot.id} {choice}`,或 "
                    f"`manju unlock {shot.id} {blocking}`",
                    level="info", actor=actor)
        else:
            project.update_shot_raw(
                shot.id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", choice)
            )
    _record_local_runs(project, takes, {"redo": True}, {shot.id: plan.cost})
    append_event(project.root, actor, "redo", {"shot": shot.id, "takes": [t.name for t in takes]})
    return [t.name for t in takes]


def _redo_shot_locked(project: Project, shot_id: str, *, candidates: int | None = None,
                      provider: str | None = None, seed: int | None = None,
                      from_take: str | None = None,
                      actor: str = "engine", assume_yes: bool = False) -> list[str]:
    rules = project.load_rules()
    bible = project.load_bible()
    plan = _plan_redo(project, shot_id, candidates=candidates, provider=provider,
                      seed=seed, from_take=from_take, rules=rules, bible=bible)
    # §8.3 事前: price this redo and gate it just like build before any spend.
    spend_gate(project, plan.cost, plan.currency, assume_yes=assume_yes,
               hint=f"确认后重试:manju redo {shot_id} --yes(或 MCP/GUI 带 assume_yes)")
    return _run_redo(project, plan, bible=bible, rules=rules, actor=actor)


# ============================================================ batch operations
# One selector → many shots, but the same three invariants as the single redo,
# only enforced ONCE for the whole set (R2 §5): one process build lock held for
# the entire batch (never per-shot churn), one aggregated §8.3 spend gate on the
# TOTAL (never a partial re-ask mid-batch), and — §4.3 — manual/locked content
# is never silently overturned; every exclusion is reported with its reason.
#
# Deliberately NOT built: `select --batch`. Regeneration is mechanical and the
# engine will do it in bulk; *selection* is a per-take human judgment (§3) and
# is never a bulk operation — the engine regenerates in bulk but never DECIDES
# in bulk. That is a design stance, not an omission.


@dataclass
class BatchResult:
    """The envelope every batch command returns (redo/voice). ``requested`` is
    the universe the selector considered; ``ran`` the shots that actually
    generated; ``skipped``/``failed`` each carry a per-shot ``reason`` so no
    exclusion is ever silent (§4.3). ``estimated_cost`` is the aggregate the
    spend gate fired on; ``actual_cost`` sums what the produced takes recorded."""

    requested: list[str] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)   # {shot, reason}
    failed: list[dict[str, str]] = field(default_factory=list)    # {shot, reason}
    takes: dict[str, list[str]] = field(default_factory=dict)     # shot -> take names
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    currency: str | None = None
    # goal: honest job cancellation — a should_cancel() checkpoint tripped
    # BETWEEN two shots of the batch (see _redo_batch_locked/_voice_batch_locked
    # below). Mirrors BuildResult's canceled/errors shape (build/graph.py) so
    # gui/jobs.py's JobRunner can generically recognize a dict result as
    # "canceled" (it only ever checks these two keys, kind-agnostically). Every
    # shot already in ``ran`` before the trip keeps its take(s) — a batch redo/
    # voice is append-only, so a partial run is never a partial CORRUPTION,
    # just fewer shots than requested.
    canceled: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _failure_reason(exc: BaseException) -> str:
    """One-line reason for a per-shot failure (FIX-D collapse). Composition
    point for round-s/failures: when a richer failure classifier lands in the
    tree it can wrap this; absent it (current tree) a plain, collapsed line."""
    try:  # round-s/failures, if present, turns a raw exc into a structured reason
        from .failures import describe_failure  # type: ignore
        return describe_failure(exc)
    except Exception:
        pass
    return " ".join(str(exc).split()) or exc.__class__.__name__


# override → the shot field a batch-wide override would overturn if sealed (§5)
_OVERRIDE_LOCK_PATHS = {
    "provider": "generation.provider",
    "seed": "generation.params.seed",
    "candidates": "generation.candidates",
}


def _lock_collisions(project: Project, shot_id: str, *, provider: str | None,
                     seed: int | None, candidates: int | None) -> list[str]:
    """Sealed fields (§5) a batch override would overturn — mirrors how the
    single redo respects locks: an override that would change a locked
    generation.* field is refused rather than silently applied (§4.3). No
    override, or an override equal to the sealed value, collides with nothing."""
    try:
        raw = project.load_shot_raw(shot_id)
    except Exception:
        return []
    locked = raw.get("locked") or {}
    if isinstance(locked, list):  # bare list form: `locked: [a.b]`
        locked = {str(p): "" for p in locked}
    if not isinstance(locked, dict):
        return []
    overrides = {"provider": provider, "seed": seed, "candidates": candidates}
    collisions: list[str] = []
    for key, value in overrides.items():
        if value is None:
            continue
        path = _OVERRIDE_LOCK_PATHS[key]
        if path not in locked:
            continue
        try:
            current = get_by_path(raw, path)
        except (KeyError, IndexError, ValueError):
            current = None
        new = int(value) if key == "candidates" else value
        if current != new:  # a real change to a sealed field -> refuse
            collisions.append(path)
    return collisions


def _selector_mode(shots, all_stale: bool, all_missing: bool, all_shots: bool) -> str:
    """Resolve exactly one batch selector; the four are mutually exclusive and
    at least one is required (the CLI mirrors this, plus the positional-id XOR)."""
    active = [name for name, on in (
        ("shots", bool(shots)),
        ("all-stale", all_stale),
        ("all-missing", all_missing),
        ("all", all_shots),
    ) if on]
    if not active:
        raise BuildError(
            "redo_batch: no selector — pass shots=[…] or all_stale/all_missing/all_shots")
    if len(active) > 1:
        raise BuildError(f"redo_batch: selectors are mutually exclusive, got {active}")
    return active[0]


def redo_batch(project: Project, shots: list[str] | None = None, *,
               all_stale: bool = False, all_missing: bool = False,
               all_shots: bool = False, candidates: int | None = None,
               provider: str | None = None, seed: int | None = None,
               actor: str = "engine", assume_yes: bool = False,
               should_cancel: "Callable[[], bool] | None" = None) -> BatchResult:
    """`manju redo --all-stale` / `--all-missing` / `--all` / `--shots S001,S003`
    — regenerate many shots under ONE build lock and ONE spend gate.

    Selectors (mutually exclusive, and exclusive with a positional shot id at
    the CLI): ``shots`` an explicit list; ``all_stale`` every STALE shot;
    ``all_missing`` every MISSING shot; ``all_shots`` every shot (redo forces
    new takes regardless of staleness — that is what makes ``--all`` different
    from ``--all-stale``).

    Exclusions are BY DEFAULT and never silent — each lands in
    :attr:`BatchResult.skipped` with a reason: a MANUAL shot (a hand-placed take
    is never batch-regenerated, §4.3); a shot whose sealed generation.* field a
    supplied override would overturn (§5); and, under ``--all-stale`` /
    ``--all-missing``, any shot not in that target state.

    The whole batch runs under a single :func:`~manju.runtime.buildlock.build_lock`
    (no per-shot re-acquire), and one aggregated §8.3 gate raises
    :class:`WaitingUser` with the TOTAL and per-shot breakdown unless
    ``assume_yes`` — there is never a partial re-ask mid-batch. Per-shot errors
    are isolated: one provider failure lands that shot in
    :attr:`BatchResult.failed` and the rest still generate.

    ``should_cancel`` (goal: honest job cancellation) is checked BETWEEN two
    shots' generation — the same "between per-item" checkpoint shape as
    :func:`run_build`'s per-shot loop — so a GUI cancel click stops the batch
    from starting its NEXT shot while whatever is already generating finishes
    normally. Every shot already landed in ``ran`` before the trip keeps its
    take (append-only, §3) — the batch just stops early, honestly reported via
    :attr:`BatchResult.canceled`/``errors``. ``None`` (no GUI job wired a
    cancel flag, e.g. every CLI call) never checks anything — byte-identical
    to before this parameter existed.

    (There is deliberately no batch *select* — see this module's batch section.)
    """
    mode = _selector_mode(shots, all_stale, all_missing, all_shots)  # validate first
    from ..runtime.buildlock import build_lock

    with build_lock(project.root, actor=actor):  # ONE hold for the whole batch
        return _redo_batch_locked(
            project, mode, list(shots or []), candidates=candidates,
            provider=provider, seed=seed, actor=actor, assume_yes=assume_yes,
            should_cancel=should_cancel)


def _redo_batch_locked(project: Project, mode: str, shots: list[str], *,
                       candidates: int | None, provider: str | None,
                       seed: int | None, actor: str, assume_yes: bool,
                       should_cancel: "Callable[[], bool] | None" = None) -> BatchResult:
    rules = project.load_rules()
    bible = project.load_bible()
    by_id = {s.shot_id: s for s in evaluate_all(project)}
    considered = shots if mode == "shots" else project.shot_ids()

    result = BatchResult(requested=list(considered))
    to_run: list[str] = []
    for sid in considered:
        st = by_id.get(sid)
        if st is None:
            result.failed.append({"shot": sid, "reason": "no such shot in this project"})
            continue
        state = st.state
        # selector-scoped skips (only --all-stale / --all-missing narrow by state)
        if mode == "all-stale" and state != ShotState.STALE:
            result.skipped.append({"shot": sid,
                "reason": f"state is {state.value}; --all-stale only redoes stale shots"})
            continue
        if mode == "all-missing" and state != ShotState.MISSING:
            result.skipped.append({"shot": sid,
                "reason": f"state is {state.value}; --all-missing only redoes missing shots"})
            continue
        # invariant exclusions (§4.3 / §5) — every selector, explicit list included
        if state == ShotState.MANUAL:
            result.skipped.append({"shot": sid,
                "reason": "manual import (§4.3) — a hand-placed take is never batch-regenerated"})
            continue
        collisions = _lock_collisions(project, sid, provider=provider, seed=seed,
                                      candidates=candidates)
        if collisions:
            result.skipped.append({"shot": sid,
                "reason": f"locked (§5): {', '.join(collisions)} sealed — an override would overturn it"})
            continue
        to_run.append(sid)

    # price the whole batch off the shared estimator, then gate ONCE on the total
    plans: dict[str, _RedoPlan] = {}
    for sid in to_run:
        try:
            plans[sid] = _plan_redo(project, sid, candidates=candidates, provider=provider,
                                    seed=seed, from_take=None, rules=rules, bible=bible)
        except BuildError as exc:
            result.failed.append({"shot": sid, "reason": _failure_reason(exc)})
    run_ids = [sid for sid in to_run if sid in plans]
    result.estimated_cost = sum(plans[sid].cost for sid in run_ids)
    result.currency = next((plans[sid].currency for sid in run_ids
                            if plans[sid].currency), None)

    if (result.estimated_cost > 0 and not assume_yes
            and "expensive_generation" in project.load_config().ask_before):
        breakdown = ", ".join(f"{sid}={plans[sid].cost}" for sid in run_ids)
        raise WaitingUser(
            f"waiting_user: 批量预估花费 {result.estimated_cost} {result.currency or ''} "
            f"({len(run_ids)} 镜头: {breakdown}) 命中 ask_before=expensive_generation — "
            f"确认后重试:manju redo <selector> --yes(先 dry-run 看计划,§8.3)",
            result.estimated_cost, result.currency)

    # generate — per-shot error isolation: one failure never aborts the rest
    for sid in run_ids:
        # goal: honest job cancellation — checked BEFORE this shot starts, so
        # a trip never kills a generation already in flight, only stops the
        # NEXT one from starting (mirrors run_build's per-shot checkpoint).
        if should_cancel is not None and should_cancel():
            result.canceled = True
            remaining = len(run_ids) - len(result.ran) - len(result.failed)
            result.errors.append(
                f"已取消:{len(result.ran)}/{len(run_ids)} 个镜头已生成并保留"
                f"(append-only,已产出的 take 不受影响),{remaining} 个镜头未开始"
            )
            break
        try:
            result.takes[sid] = _run_redo(project, plans[sid], bible=bible,
                                          rules=rules, actor=actor)
            result.ran.append(sid)
        except Exception as exc:  # ProviderFailure/NeedsHumanInput/MediaError/…
            result.failed.append({"shot": sid, "reason": _failure_reason(exc)})

    result.actual_cost = _sum_actual_take_cost(project, result.takes)
    append_event(project.root, actor, "redo_batch", {
        "mode": mode,
        "requested": result.requested,
        "ran": result.ran,
        "skipped": [s["shot"] for s in result.skipped],
        "failed": [f["shot"] for f in result.failed],
        "estimated_cost": result.estimated_cost,
        "canceled": result.canceled,
    })
    return result


def _sum_actual_take_cost(project: Project, takes: dict[str, list[str]]) -> float:
    """Best-effort sum of what the freshly produced takes actually cost — cloud
    takes carry ``remote.cost``; local ones (the offline fallback) record 0."""
    total = 0.0
    for shot_id, names in takes.items():
        try:
            for t in project.takes(shot_id):
                if t.name in names and t.sidecar.remote and t.sidecar.remote.cost:
                    total += t.sidecar.remote.cost
        except Exception:  # pricing is advisory; never fail the result
            pass
    return total


# ------------------------------------------------------------------ voice batch


def _voice_selector_mode(shots, all_shots: bool, missing: bool) -> str:
    active = [name for name, on in (
        ("shots", bool(shots)),
        ("all", all_shots),
        ("missing", missing),
    ) if on]
    if not active:
        raise BuildError("voice_batch: no selector — pass shots=[…] or all_shots/missing")
    if len(active) > 1:
        raise BuildError(f"voice_batch: selectors are mutually exclusive, got {active}")
    return active[0]


def voice_batch(project: Project, shots: list[str] | None = None, *,
                all_shots: bool = False, missing: bool = False,
                provider: str | None = None, actor: str = "engine",
                assume_yes: bool = False,
                should_cancel: "Callable[[], bool] | None" = None) -> BatchResult:
    """`manju voice --all` / `--missing` / `--shots S001,S003` — synthesize many
    voice takes under ONE build lock and ONE spend gate (the redo shape, applied
    to sound).

    Selectors: ``missing`` shots with dialogue but no voice take (VoiceState
    MISSING); ``shots`` an explicit list (the escape hatch that re-voices a
    STALE line — a per-shot human decision); ``all_shots`` every dialogued shot,
    but only the MISSING ones actually run. STALE voices stay advisory-only
    (§4.3): ``--all`` NEVER batch-regenerates them — it skips them with a reason
    and points at ``--shots``. Manual (hand-dropped) voices are likewise skipped
    under ``--all`` / ``--missing``.

    One aggregated §8.3 gate on the per_call total raises :class:`WaitingUser`
    unless ``assume_yes``; per-shot synthesis errors are isolated into
    :attr:`BatchResult.failed`. ``should_cancel`` (goal: honest job
    cancellation) is checked between two shots' synthesis — see
    :func:`redo_batch`'s docstring for the exact same checkpoint shape."""
    mode = _voice_selector_mode(shots, all_shots, missing)
    from ..runtime.buildlock import build_lock

    with build_lock(project.root, actor=actor):
        return _voice_batch_locked(project, mode, list(shots or []),
                                   provider=provider, actor=actor, assume_yes=assume_yes,
                                   should_cancel=should_cancel)


def _voice_batch_locked(project: Project, mode: str, shots: list[str], *,
                        provider: str | None, actor: str,
                        assume_yes: bool,
                        should_cancel: "Callable[[], bool] | None" = None) -> BatchResult:
    from ..providers.tts import TtsUnavailable, get_tts_provider, tts_providers
    from .voice import VoiceState, evaluate_all_voices

    by_id = {v.shot_id: v for v in evaluate_all_voices(project)}
    considered = shots if mode == "shots" else project.shot_ids()

    result = BatchResult(requested=list(considered))
    to_run: list[str] = []
    for sid in considered:
        vs = by_id.get(sid)
        if vs is None:
            result.failed.append({"shot": sid, "reason": "no such shot in this project"})
            continue
        state = vs.state
        if state == VoiceState.NOT_NEEDED:
            result.skipped.append({"shot": sid, "reason": "no dialogue.text to voice"})
            continue
        if mode == "missing" and state != VoiceState.MISSING:
            result.skipped.append({"shot": sid,
                "reason": f"voice state is {state.value}; --missing only voices shots with no take"})
            continue
        if mode == "all" and state != VoiceState.MISSING:
            # §4.3: --all fills gaps, never overturns an existing voice
            reason = {
                VoiceState.FRESH: "voice already fresh",
                VoiceState.STALE: "stale voice is advisory-only (§4.3) — re-voice explicitly with --shots",
                VoiceState.MANUAL: "hand-dropped voice (§4.3) — never batch-regenerated",
            }.get(state, f"voice state is {state.value}")
            result.skipped.append({"shot": sid, "reason": reason})
            continue
        to_run.append(sid)  # 'shots' mode: synth regardless of state (escape hatch)

    if not to_run:
        append_event(project.root, actor, "voice_batch", _voice_event(mode, result))
        return result

    # resolve the TTS provider + price once; a config miss fails every candidate
    # (honest — nothing can run) but still returns the fully-reasoned envelope.
    try:
        providers = tts_providers()
        if not providers:
            raise TtsUnavailable("no TTS provider configured (§8.6, type: tts)")
        provider_id = provider or sorted(providers)[0]
        if provider_id not in providers:
            raise TtsUnavailable(
                f"unknown TTS provider {provider_id!r}; configured: {sorted(providers)}")
        manifest = providers[provider_id]
    except TtsUnavailable as exc:
        for sid in to_run:
            result.failed.append({"shot": sid, "reason": str(exc)})
        append_event(project.root, actor, "voice_batch", _voice_event(mode, result))
        return result

    per_call = manifest.cost.per_call or 0.0
    result.currency = manifest.cost.currency
    result.estimated_cost = per_call * len(to_run)
    if (result.estimated_cost > 0 and not assume_yes
            and "expensive_generation" in project.load_config().ask_before):
        breakdown = ", ".join(f"{sid}={per_call}" for sid in to_run)
        raise WaitingUser(
            f"waiting_user: 批量配音预估 {result.estimated_cost} {result.currency or ''} "
            f"({len(to_run)} 镜头: {breakdown}) 命中 ask_before=expensive_generation — "
            f"确认后重试:manju voice <selector> --yes(§8.3)",
            result.estimated_cost, result.currency)

    bible = project.load_bible()
    tts = get_tts_provider(provider_id)
    for sid in to_run:
        # goal: honest job cancellation — same "before the next item" checkpoint
        # as redo_batch above.
        if should_cancel is not None and should_cancel():
            result.canceled = True
            remaining = len(to_run) - len(result.ran) - len(result.failed)
            result.errors.append(
                f"已取消:{len(result.ran)}/{len(to_run)} 个镜头已配音并保留"
                f"(append-only,已产出的 take 不受影响),{remaining} 个镜头未开始"
            )
            break
        try:
            media = tts.synthesize(project, project.load_shot(sid), bible)
            result.takes[sid] = [media.stem]
            result.ran.append(sid)
            append_event(project.root, actor, "voice",
                         {"shot": sid, "take": media.stem, "provider": provider_id})
        except Exception as exc:
            result.failed.append({"shot": sid, "reason": _failure_reason(exc)})

    result.actual_cost = _sum_actual_voice_cost(project, result.takes)
    append_event(project.root, actor, "voice_batch", _voice_event(mode, result))
    return result


def _voice_event(mode: str, result: BatchResult) -> dict[str, Any]:
    return {
        "mode": mode,
        "requested": result.requested,
        "ran": result.ran,
        "skipped": [s["shot"] for s in result.skipped],
        "failed": [f["shot"] for f in result.failed],
        "estimated_cost": result.estimated_cost,
        "canceled": result.canceled,
    }


def _sum_actual_voice_cost(project: Project, takes: dict[str, list[str]]) -> float:
    total = 0.0
    for shot_id, names in takes.items():
        try:
            for media, sidecar in project.voice_takes(shot_id):
                if (sidecar is not None and media.stem in names
                        and sidecar.remote and sidecar.remote.cost):
                    total += sidecar.remote.cost
        except Exception:
            pass
    return total
