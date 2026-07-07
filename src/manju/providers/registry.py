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
        return providers[name]
    except KeyError:
        raise KeyError(
            f"unknown provider {name!r}; available: {sorted(providers)}"
        ) from None


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
    from . import routing  # lazy: routing imports the registry back

    preferred = req.shot.generation.provider
    if preferred and routing.provider_disabled(preferred):
        raise ProviderFailure(
            FailureKind.invalid,
            f"shot {req.shot.id} names provider {preferred!r} but it is disabled "
            f"(disabled: true) — run `manju providers enable {preferred}` or pick "
            f"another provider",
        )

    # A routing.yaml (project or user) lets the active strategy decide the
    # order — this is the one wiring point, so it applies even when a caller
    # (build/graph) passes an explicit chain. With NO routing file the passed
    # chain (or the freshly computed one) drives the order EXACTLY as §8.4 did,
    # keeping the provider choice byte-identical (pinned).
    routed = routing.load_routing(req.project)
    if routed is not None:
        order = routing.resolve(req.project, req.shot, routed).order
    else:
        if chain is None:
            chain = fallback_chain(req.shot)
        order = ([preferred] if preferred else [])
        order += [name for name in chain if name not in order]

    media_errors = _media_error_types()
    attempts: list[tuple[str, str]] = []

    for name in order:
        try:
            provider = get_provider(name)
        except KeyError as exc:
            attempts.append((name, f"not registered ({exc})"))
            continue
        _emit(log, f"provider {name}: generating shot {req.shot.id}")
        try:
            takes = provider.generate(req)
        except NeedsHumanInput as exc:
            attempts.append((name, f"needs human input: {exc}"))
            continue
        except ProviderFailure as exc:
            attempts.append((name, f"{exc.kind.value}: {exc.message}"))
            continue
        except media_errors as exc:  # media package's MediaError, if importable
            attempts.append((name, f"media error: {exc}"))
            continue
        if takes:
            _emit(log, f"provider {name}: produced {len(takes)} take(s)")
            return takes
        attempts.append((name, "produced no takes"))

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
