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


def available_providers() -> dict[str, Provider]:
    _ensure_builtins()
    return dict(_REGISTRY)


def get_provider(name: str) -> Provider:
    _ensure_builtins()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown provider {name!r}; available: {sorted(_REGISTRY)}"
        ) from None


def fallback_chain(shot: ShotSpec) -> list[str]:
    """Map ``shot.generation.fallback`` to providers available today (§8.4).

    Unknown/unavailable steps are skipped; the chain is guaranteed to end at the
    network-independent ``caption_card`` provider so it can never dead-end."""
    chain: list[str] = []
    for step in shot.generation.fallback:
        name = _FALLBACK_MAP.get(step)
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
    every attempt and its error."""
    if chain is None:
        chain = fallback_chain(req.shot)

    order: list[str] = []
    preferred = req.shot.generation.provider
    if preferred:
        order.append(preferred)
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
