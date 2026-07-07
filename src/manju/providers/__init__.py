"""Providers layer (§8): manual import, local fallbacks, and the cloud skeleton.

Media rendering is imported lazily inside provider methods so this package
imports cleanly even before ``manju.media`` has landed.
"""

from __future__ import annotations

from .base import (
    CloudProvider,
    FailureKind,
    GenerationRequest,
    NeedsHumanInput,
    Provider,
    ProviderFailure,
)
from .caption_card import CaptionCardProvider
from .kenburns import KenburnsProvider
from .manual import ManualImportProvider, register_manual_take
from .registry import (
    available_providers,
    fallback_chain,
    generate_with_fallback,
    get_provider,
    register_provider,
)
from .routing import (
    BUILTIN_STRATEGIES,
    RoutingError,
    explain as route_explain,
    list_strategies as route_list,
    load_routing,
    resolve as route_resolve,
)

__all__ = [
    # base protocol
    "FailureKind",
    "ProviderFailure",
    "NeedsHumanInput",
    "GenerationRequest",
    "Provider",
    "CloudProvider",
    # registry / routing
    "get_provider",
    "available_providers",
    "register_provider",
    "fallback_chain",
    "generate_with_fallback",
    # routing strategies (goal 9)
    "route_resolve",
    "route_explain",
    "route_list",
    "load_routing",
    "BUILTIN_STRATEGIES",
    "RoutingError",
    # built-in providers
    "ManualImportProvider",
    "register_manual_take",
    "KenburnsProvider",
    "CaptionCardProvider",
]
