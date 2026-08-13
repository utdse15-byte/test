"""Provider registry and fallback routing (§8.4).

Built-in, always-available providers are the network-independent locals:
``manual_import``, ``ffmpeg_kenburns``, ``caption_card``. Cloud adapters (M3)
register themselves on top. The fallback chain always ends at a provider that
does not depend on the network, so a single failure never blocks the whole cut.
"""

from __future__ import annotations

from typing import Callable

from ..core.container import TakeInfo
from ..core.models import ShotSpec
from .base import (
    FailureKind,
    GenerationRequest,
    NeedsHumanInput,
    Provider,
    ProviderFailure,
)

_REGISTRY: dict[str, Provider] = {}

# fallback step (§4.1 FALLBACK_STEPS) -> provider available today.
# Steps with no local/registered adapter yet are skipped.
_FALLBACK_MAP: dict[str, str] = {
    "still_frame_motion": "ffmpeg_kenburns",
    "caption_card": "caption_card",
    # image_to_video / first_last_frame / comic_panel: no adapter yet -> skip
}

_FINAL_PROVIDER = "caption_card"  # §8.4: chain must end network-independent


def _ensure_builtins() -> None:
    if _REGISTRY:
        return
    from .caption_card import CaptionCardProvider
    from .kenburns import KenburnsProvider
    from .manual import ManualImportProvider

    for provider in (ManualImportProvider(), KenburnsProvider(), CaptionCardProvider()):
        _REGISTRY[provider.id] = provider


def register_provider(provider: Provider) -> None:
    """Register (or replace) a provider by its id — used by cloud adapters."""
    _ensure_builtins()
    _REGISTRY[provider.id] = provider


# --------------------------------------------------------------- manifests
# Config-declared providers (§8.2/§8.6): each provider.yaml becomes an
# instance — generic_cloud manifests get the built-in GenericCloudProvider,
# anything else resolves the "module:Class" escape hatch. Instances are
# cached per manifest-file mtime so edits (and test overrides via
# MANJU_PROVIDERS_DIR) are picked up without a process restart.

_manifest_cache: tuple[tuple, dict[str, Provider], list[str]] | None = None


def _manifest_state() -> tuple[dict[str, Provider], list[str]]:
    global _manifest_cache
    from .manifest import GENERIC_ADAPTER, load_manifests, providers_dir

    root = providers_dir()
    key_parts = [str(root)]
    if root.is_dir():
        for p in sorted(root.glob("*/provider.yaml")):
            key_parts.append(f"{p}:{p.stat().st_mtime_ns}")
    key = tuple(key_parts)
    if _manifest_cache is not None and _manifest_cache[0] == key:
        return _manifest_cache[1], _manifest_cache[2]

    manifests, errors = load_manifests()
    providers: dict[str, Provider] = {}
    for pid, manifest in manifests.items():
        if manifest.type in ("asr", "tts"):
            continue  # ASR/TTS live on their own surfaces, never a shot fallback
        if pid in _FALLBACK_MAP.values() or pid == "manual_import":
            errors.append(f"{pid}: manifest id shadows a built-in provider — skipped")
            continue
        try:
            from .zero_cost import require_manifest_allowed

            require_manifest_allowed(manifest)
            if manifest.adapter == GENERIC_ADAPTER:
                from .generic_cloud import GenericCloudProvider

                providers[pid] = GenericCloudProvider(manifest)
            else:  # escape hatch: "package.module:ClassName"
                module_name, _, class_name = manifest.adapter.partition(":")
                if not class_name:
                    raise ValueError(f"adapter {manifest.adapter!r} is not 'module:Class'")
                import importlib

                cls = getattr(importlib.import_module(module_name), class_name)
                providers[pid] = cls(manifest)
        except Exception as exc:  # a broken adapter never breaks the registry
            errors.append(f"{pid}: {exc}")
    _manifest_cache = (key, providers, errors)
    return providers, errors


def get_manifest(name: str):
    """The ProviderManifest for a config-declared provider, or None."""
    from .manifest import load_manifests

    return load_manifests()[0].get(name)


def manifest_errors() -> list[str]:
    """Config problems collected while building the registry (for doctor)."""
    _, errors = _manifest_state()
    return errors


def available_providers() -> dict[str, Provider]:
    _ensure_builtins()
    merged = dict(_REGISTRY)
    manifest_providers, _ = _manifest_state()
    for pid, provider in manifest_providers.items():
        merged.setdefault(pid, provider)
    return merged


def get_provider(name: str) -> Provider:
    providers = available_providers()
    try:
        provider = providers[name]
    except KeyError:
        raise KeyError(
            f"unknown provider {name!r}; available: {sorted(providers)}"
        ) from None
    manifest = getattr(provider, "manifest", None)
    if manifest is not None:
        from .zero_cost import require_manifest_allowed

        require_manifest_allowed(manifest)
    return provider


def fallback_chain(shot: ShotSpec) -> list[str]:
    """Map ``shot.generation.fallback`` to providers available today (§8.4).

    Unknown/unavailable steps are skipped; the chain is guaranteed to end at the
    network-independent ``caption_card`` provider so it can never dead-end.

    A step with no local mapping (e.g. ``image_to_video``) resolves to the
    first config-declared provider advertising that capability (§8.6), so a
    filled-in manifest slots straight into existing shots' fallback chains.

    Disabled providers (``disabled: true`` in the manifest, goal item 1) are
    never selected here — they are skipped exactly like an unavailable step."""
    manifest_providers, _ = _manifest_state()
    disabled = {
        pid for pid, p in manifest_providers.items()
        if getattr(getattr(p, "manifest", None), "disabled", False)
    }
    chain: list[str] = []
    for step in shot.generation.fallback:
        name = _FALLBACK_MAP.get(step)
        if name is None:
            name = next(
                (pid for pid, p in sorted(manifest_providers.items())
                 if pid not in disabled
                 and step in getattr(getattr(p, "manifest", None), "capabilities", [])),
                None,
            )
        if name and name in disabled:  # a disabled provider named via the map: skip it
            continue
        if name and name not in chain:
            chain.append(name)
    # ensure caption_card is the final element
    if _FINAL_PROVIDER in chain:
        chain.remove(_FINAL_PROVIDER)
    chain.append(_FINAL_PROVIDER)
    return chain


def generate_with_fallback(
    req: GenerationRequest,
    chain: list[str] | None = None,
    *,
    log: Callable[[str], None] | None = None,
) -> list[TakeInfo]:
    """Try the shot's preferred provider, then walk the fallback chain (§8.4).

    A provider that fails (``ProviderFailure`` / ``NeedsHumanInput`` /
    ``MediaError``) is recorded and the next is tried. If every provider fails,
    a single ``ProviderFailure(provider_error)`` is raised whose message lists
    every attempt and its error.

    Provider ORDER (goal item 9): naming a disabled provider explicitly is a
    hard build error; when a routing.yaml exists the active strategy decides the
    order; with NO routing file the order is byte-identical to §8.4 (explicit
    provider first, then the fallback chain)."""
    # Closed roles have exactly one explicit reference owner. Validate before
    # routing so providers that do not consume prompt text cannot bypass the
    # pre-transport ownership gate.
    try:
        req.validate_recipe()
        req.authored_refset()
    except ProviderFailure:
        raise
    except Exception as exc:
        from .refs import ReferenceControlConflict
        from .submission import NOT_DISPATCHED

        if isinstance(exc, ReferenceControlConflict):
            detail = {
                "code": "reference_control_conflict",
                "conflicts": list(exc.conflicts),
            }
            message = f"shot {req.shot.id} has conflicting reference ownership: {exc}"
            kind = FailureKind.invalid
        else:
            detail = {"code": "reference_resolution_unavailable"}
            message = (
                f"shot {req.shot.id} reference resolution failed before provider "
                f"routing ({type(exc).__name__}: {exc})"
            )
            kind = FailureKind.provider_error
        raise ProviderFailure(
            kind, message, detail=detail, disposition=NOT_DISPATCHED
        ) from exc
    from . import routing  # lazy: routing imports the registry back

    preferred = req.shot.generation.provider
    if preferred and routing.provider_disabled(preferred):
        raise ProviderFailure(
            FailureKind.invalid,
            f"shot {req.shot.id} names provider {preferred!r} but it is disabled "
            f"(disabled: true) — run `manju providers enable {preferred}` or pick "
            f"another provider",
        )
    # DR04 fail-earlier: an explicit pin that is structurally incompatible with
    # THIS request (duration over the provider's max_duration_ms — the SAME fact
    # generic_cloud enforces at submit) FAILS here, before any submit, instead of
    # being silently degraded down the fallback chain. The submit-time guard
    # remains the final defense for any path that reaches it.
    if preferred:
        incompat = routing.explicit_pin_incompatibility(preferred, req)
        if incompat:
            raise ProviderFailure(
                FailureKind.invalid,
                f"shot {req.shot.id} names provider {preferred!r} but it {incompat}",
            )

    # A routing.yaml (project or user) lets the active strategy decide the
    # order — this is the one wiring point, so it applies even when a caller
    # (build/graph) passes an explicit chain. A build mode's routing bias
    # (req.routing_bias, goal 14) engages the same resolver even with NO routing
    # file so `--mode speed/quality` still biases the else branch. With no
    # routing file AND no mode bias the passed chain (or the freshly computed
    # one) drives the order EXACTLY as §8.4 did — byte-identical (pinned).
    try:
        routed = routing.load_routing(req.project)
        bias = getattr(req, "routing_bias", None)
        if routed is not None or bias is not None:
            config = routed or routing._default_config()
            order = routing.resolve(req.project, req.shot, config, else_bias=bias).order
    except routing.RoutingError as exc:
        # a hand-edited routing.yaml typo (bad YAML, unknown strategy) is a
        # PRE-SPEND config error: surface it as the structured per-shot
        # failure every caller already handles (graph catches ProviderFailure)
        # instead of crashing the whole build with a raw traceback.
        from .submission import NOT_DISPATCHED

        raise ProviderFailure(
            FailureKind.invalid,
            f"routing.yaml 配置错误(修好后重跑;`manju route explain` 可诊断): "
            f"{' '.join(str(exc).split())}",
            disposition=NOT_DISPATCHED,  # nothing was ever sent
        ) from exc
    if routed is None and bias is None:
        if chain is None:
            chain = fallback_chain(req.shot)
        order = ([preferred] if preferred else [])
        order += [name for name in chain if name not in order]

    media_errors = _media_error_types()
    attempts: list[tuple[str, str]] = []

    # DR03C run-evidence (evidence-only; None for direct provider tests / redo
    # outside a run → the whole block below is inert and behaviour is
    # byte-identical). ONE stage_attempt event per provider TRY in the chain;
    # parentage per the contract A/B/C example: fallback_root = the first try's
    # id, parent = the immediately-previous try's id, fallback_index = position.
    # Imported lazily to keep providers/ free of a build/ import cycle.
    ev = getattr(req, "evidence", None)
    _emit_ctx = _EvidenceChain(ev, req) if ev is not None else None

    for idx, name in enumerate(order):
        handle = _emit_ctx.start(name, idx) if _emit_ctx is not None else None
        # Stamp the pre-minted attempt id on the request for THIS try: cloud
        # providers self-record their ledger row inside generate() (base.py
        # _on_success/_on_failure) — before the chain's terminal event lands —
        # so this is the only way the runs row and the stage_attempt event
        # share one attempt_id on the cloud path too (tasks --json parity).
        try:
            provider = get_provider(name)
        except KeyError as exc:
            attempts.append((name, f"not registered ({exc})"))
            if handle is not None:
                _emit_ctx.rejected(handle, "invalid", "not_registered", str(exc))
            continue
        _emit(log, f"provider {name}: generating shot {req.shot.id}")
        try:
            attempt_req = req.for_provider(provider)
            attempt_req.evidence_attempt_id = (
                handle.attempt_id if handle is not None else None
            )
            takes = provider.generate(attempt_req)
        except NeedsHumanInput as exc:
            attempts.append((name, f"needs human input: {exc}"))
            if handle is not None:
                _emit_ctx.rejected(handle, "needs_human_input", "needs_human_input", str(exc))
            continue
        except ProviderFailure as exc:
            attempts.append((name, f"{exc.kind.value}: {exc.message}"))
            if handle is not None:
                _emit_ctx.provider_failure(handle, exc)
            # DR06 (ruling 7): an OUTCOME_UNKNOWN disposition STOPS the fallback
            # chain — falling back would be NEW spend on an unresolved outcome
            # (the remote side of THIS provider may already be running/billing).
            # Re-raise immediately, carrying the disposition + structured detail
            # so the caller/tasks surface the honest "attach or abandon" choice.
            from .submission import OUTCOME_UNKNOWN_DISPOSITION

            if getattr(exc, "disposition", None) == OUTCOME_UNKNOWN_DISPOSITION:
                raise
            continue
        except media_errors as exc:  # media package's MediaError, if importable
            attempts.append((name, f"media error: {exc}"))
            if handle is not None:
                _emit_ctx.failed(handle, "media_error", "media_error", str(exc))
            continue
        if takes:
            _emit(log, f"provider {name}: produced {len(takes)} take(s)")
            if handle is not None:
                # SUCCEEDED only AFTER register_take already committed media +
                # sidecar (provider.generate did that) — the outputs re-hash the
                # bytes now on disk, never before they landed.
                _emit_ctx.succeeded(handle, name, takes)
            return takes
        attempts.append((name, "produced no takes"))
        if handle is not None:
            _emit_ctx.failed(handle, "provider_error", "no_takes", "produced no takes")

    summary = "; ".join(f"[{n}] {e}" for n, e in attempts) or "no providers attempted"
    raise ProviderFailure(
        FailureKind.provider_error,
        f"all providers failed for shot {req.shot.id}: {summary}",
        detail={"attempts": [{"provider": n, "error": e} for n, e in attempts]},
    )


def _media_error_types() -> tuple[type[BaseException], ...]:
    try:
        from ..media.ffmpeg import MediaError
    except ImportError:
        return ()
    return (MediaError,)


def _emit(log: Callable[[str], None] | None, msg: str) -> None:
    if log is not None:
        log(msg)


# ------------------------------------------------------- DR03C attempt evidence

# Retryable failure kinds (safe to retry with backoff, §8.1) — surfaced on the
# attempt's failure block so the projection can say whether a retry was legal.
_RETRYABLE_KINDS = frozenset({"rate_limited", "timeout"})
# invalid-before-submit refusals map to REJECTED_PRECHECK, not FAILED.
_PRECHECK_KINDS = frozenset({"invalid"})


class _EvidenceChain:
    """Wraps one ``generate_with_fallback`` call's per-provider attempt emission
    (DR03C). Holds the fallback parentage (root = first try, parent = previous
    try) so each ``stage_attempt`` event carries the A/B/C lineage. All emission
    is best-effort via ``build.attempts`` (imported lazily to avoid a build/ ↔
    providers/ import cycle); a hiccup here never affects generation."""

    def __init__(self, evidence: object, req: object):
        from ..build import attempts as _attempts  # lazy: avoid import cycle

        self._A = _attempts
        self.ev = evidence
        self.req = req
        self.shot_id = getattr(getattr(req, "shot", None), "id", None)
        self._first_id: str | None = None
        self._prev_id: str | None = None

    def _executor(self, name: str) -> dict:
        executor = {"kind": "provider", "provider_id": name}
        try:  # model_id/manifest_digest ONLY if cheaply available from a manifest
            manifest = get_manifest(name)
            if manifest is not None:
                executor["provider_type"] = getattr(manifest, "type", None)
                model = None
                submit = getattr(manifest, "submit", None)
                body = getattr(submit, "body_template", None) if submit else None
                if isinstance(body, dict):
                    model = body.get("model") or body.get("model_id")
                if model:
                    executor["model_id"] = model
                from ..core.hashing import hash_value
                executor["provider_manifest_digest"] = hash_value(
                    manifest.model_dump(mode="json", exclude_none=True))
        except Exception:
            pass
        return executor

    def start(self, name: str, idx: int):
        """Open a handle for one provider try (before the work runs)."""
        try:
            req = self.req
            params = dict(getattr(req, "params", {}) or {})
            request = self._A.provider_request_evidence(
                params, spec_hash=getattr(req, "spec_hash", None),
                duration_ms=getattr(req, "duration_ms", None),
                candidate_index=getattr(req, "candidates", None),
                seed=params.get("seed"))
            handle = self.ev.attempt(
                "generate", {"kind": "shot", "shot": self.shot_id}, "generate",
                parent_attempt_id=self._prev_id, fallback_index=idx,
                executor=self._executor(name), request=request)
            handle.fallback_root_attempt_id = self._first_id or handle.attempt_id
            if self._first_id is None:
                self._first_id = handle.attempt_id
            # P0 WP4: provider generation is one of the two expensive attempt
            # families — announce attempt_started BEFORE provider.generate runs,
            # on the SAME pre-minted attempt_id the terminal stage_attempt will
            # carry. Best-effort (returns False on failure, never raises).
            self._A.append_attempt_started(
                getattr(self.ev, "project", None) or req.project,
                getattr(self.ev, "run_id", None) or "?",
                handle.attempt_id, stage="generate", provider=name,
                actor=getattr(self.ev, "actor", "engine"))
            return handle
        except Exception:
            return None

    def _decision(self) -> dict:
        # v1: cloud-internal retries stay internal — the registry loop itself
        # does not retry a provider, so retry_index is honestly 0 here.
        return {"retry_index": 0}

    def succeeded(self, handle, name: str, takes: list) -> None:
        try:
            from ..core.hashing import hash_file

            outputs = []
            actual = 0.0
            currency = None
            for t in takes:
                media = getattr(t, "media_path", None)
                if media is not None and media.exists():
                    outputs.append(self._A.output_ref(
                        "take", path=self.req.project.relpath(media),
                        sha256=hash_file(media), bytes=media.stat().st_size,
                        take=t.name))
                remote = getattr(t.sidecar, "remote", None)
                if remote is not None and getattr(remote, "cost", None):
                    actual += float(remote.cost)
                    currency = currency or remote.currency
            est = getattr(self.req, "estimated_cost", None)
            cost = {"actual": actual, "estimated": est, "currency": currency}
            rec = handle.succeeded(outputs=outputs, cost=cost, decision=self._decision())
            # ledger cross-ref (test 19): note which attempt produced each take so
            # graph's _record_local_runs threads the SAME id onto the runs row.
            aid = (rec.get("detail") or {}).get("attempt_id") if rec else handle.attempt_id
            for t in takes:
                self.ev.note_take_attempt(getattr(t, "shot_id", self.shot_id), t.name, aid)
            self._prev_id = handle.attempt_id
        except Exception:
            pass

    def failed(self, handle, category: str, code: str, message: str,
               *, retryable: bool | None = None) -> None:
        try:
            failure = {"category": category, "code": code,
                       "message": _one_line(message)}
            if retryable is not None:
                failure["retryable"] = retryable
            handle.failed(failure=failure, decision=self._decision())
            self._prev_id = handle.attempt_id
        except Exception:
            pass

    def rejected(self, handle, category: str, code: str, message: str) -> None:
        try:
            handle.rejected_precheck(
                failure={"category": category, "code": code, "message": _one_line(message)})
            self._prev_id = handle.attempt_id
        except Exception:
            pass

    def provider_failure(self, handle, exc) -> None:
        """Classify a ProviderFailure onto the attempt: invalid → precheck,
        everything else → FAILED with retryable + any provider job id."""
        try:
            kind = getattr(getattr(exc, "kind", None), "value", "provider_error")
            detail = getattr(exc, "detail", None) or {}
            message = getattr(exc, "message", str(exc))
            if kind in _PRECHECK_KINDS:
                self.rejected(handle, kind, kind, message)
                return
            failure = {
                "category": kind, "code": kind, "message": _one_line(message),
                "retryable": kind in _RETRYABLE_KINDS,
            }
            # DR06: the submit-outcome disposition rides the attempt's failure
            # payload (never a new attempt state — the attempt stays FAILED). An
            # OUTCOME_UNKNOWN attempt is flagged as possibly-still-billing so the
            # projection can surface the honest recovery choice.
            disposition = getattr(exc, "disposition", None)
            if disposition is not None:
                failure["disposition"] = disposition
                from .submission import OUTCOME_UNKNOWN_DISPOSITION

                if disposition == OUTCOME_UNKNOWN_DISPOSITION:
                    failure["remote_may_continue"] = True
                    failure["automatic_resubmit"] = False
                    sid = detail.get("submission_id")
                    if sid:
                        failure["submission_id"] = sid
            job_id = detail.get("job_id") or detail.get("prompt_id")
            if job_id:
                failure["provider_job_id"] = job_id
                # a cloud job id present on a terminal failure may still be
                # billing remotely — honest signal for the manifest.
                failure["remote_may_continue"] = True
            handle.failed(failure=failure, decision=self._decision())
            self._prev_id = handle.attempt_id
        except Exception:
            pass


def _one_line(text: object, limit: int = 400) -> str:
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[:limit] + "…"
