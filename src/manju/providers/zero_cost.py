"""Machine-enforced provider egress policy for strict zero-cost execution."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from ..core.hashing import hash_value
from .base import FailureKind, ProviderFailure

STRICT_ZERO_COST = "strict_zero_cost"
POLICY_SCHEMA = "manju.provider-execution-policy/v1"
LOCAL_PROOF_SCHEMA = "manju.zero-cost-local-proof/v1"
R_EGRESS_BLOCKED = "STRICT_ZERO_COST_EGRESS_BLOCKED"
R_CREDENTIAL_FORBIDDEN = "STRICT_ZERO_COST_CREDENTIAL_FORBIDDEN"
R_ADAPTER_FORBIDDEN = "STRICT_ZERO_COST_ADAPTER_FORBIDDEN"


def execution_mode() -> str:
    return os.environ.get("MANJU_EXECUTION_MODE", "").strip().lower()


def strict_zero_cost_active() -> bool:
    return execution_mode() == STRICT_ZERO_COST


def credential_presence(credential_ref: str | None) -> bool | None:
    """Report legacy presence without touching credential vars in strict mode."""
    if not credential_ref or strict_zero_cost_active():
        return None
    return bool(os.environ.get(credential_ref))


def is_loopback_url(url: str) -> bool:
    """Accept an HTTP(S) loopback literal or exact ``localhost`` only."""
    try:
        parsed = urlsplit(str(url))
        host = (parsed.hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return parsed.scheme.lower() in {"http", "https"} and host in {
        "localhost", "127.0.0.1", "::1"
    }


def execution_policy_snapshot() -> dict[str, object]:
    facts: dict[str, object] = {
        "schema": POLICY_SCHEMA,
        "mode": execution_mode() or "legacy_default",
        "allowed_transports": ["local_files", "stdio", "loopback_http"],
        "allowed_http_hosts": ["localhost", "127.0.0.1", "::1"],
        "allowed_provider_adapters": (
            [
                "generic_cloud",
                "generic_asr",
                "generic_tts",
                "manju.providers.comfyui:ComfyUIProvider",
            ]
            if strict_zero_cost_active()
            else "legacy"
        ),
        "arbitrary_subprocess": (
            "forbidden" if strict_zero_cost_active() else "legacy"
        ),
        "external_transport": "forbidden" if strict_zero_cost_active() else "legacy",
        "credential_resolution": "forbidden" if strict_zero_cost_active() else "legacy",
        "cloud_fallback": "forbidden" if strict_zero_cost_active() else "legacy",
    }
    return {**facts, "digest": hash_value(facts)}


def require_transport_allowed(url: str, *, credential_ref: str | None = None) -> None:
    """Fail before credential resolution and before any provider transport."""
    if not strict_zero_cost_active():
        return
    if not is_loopback_url(url):
        raise ProviderFailure(
            FailureKind.invalid,
            "strict zero-cost mode permits provider HTTP only on loopback",
            detail={
                "reason_code": R_EGRESS_BLOCKED,
                "transport_count": 0,
            },
        )
    if credential_ref:
        raise ProviderFailure(
            FailureKind.invalid,
            "strict zero-cost mode forbids provider credential resolution",
            detail={
                "reason_code": R_CREDENTIAL_FORBIDDEN,
                "transport_count": 0,
                "credential_configured": False,
            },
        )


def require_manifest_allowed(manifest) -> None:
    """Reject unaudited provider adapters before they can execute custom code."""
    if not strict_zero_cost_active():
        return
    from .manifest import (
        COMFYUI_ADAPTER,
        GENERIC_ADAPTER,
        GENERIC_ASR_ADAPTER,
        GENERIC_TTS_ADAPTER,
    )

    adapter = str(getattr(manifest, "adapter", ""))
    allowed = {
        GENERIC_ADAPTER,
        GENERIC_ASR_ADAPTER,
        GENERIC_TTS_ADAPTER,
        COMFYUI_ADAPTER,
    }
    if adapter not in allowed:
        raise ProviderFailure(
            FailureKind.invalid,
            "strict zero-cost mode forbids unaudited provider adapters",
            detail={
                "reason_code": R_ADAPTER_FORBIDDEN,
                "transport_count": 0,
                "adapter": adapter,
            },
        )
    if getattr(getattr(manifest, "auth", None), "key_env", None):
        require_transport_allowed(
            _manifest_primary_url(manifest),
            credential_ref=manifest.auth.key_env,
        )
    if adapter in {GENERIC_ADAPTER, GENERIC_ASR_ADAPTER, GENERIC_TTS_ADAPTER}:
        for config in (getattr(manifest, "submit", None), getattr(manifest, "poll", None)):
            if config is not None:
                require_transport_allowed(config.url)
    elif adapter == COMFYUI_ADAPTER:
        require_transport_allowed(manifest.comfyui.base_url)


def _manifest_primary_url(manifest) -> str:
    submit = getattr(manifest, "submit", None)
    if submit is not None:
        return str(submit.url)
    comfyui = getattr(manifest, "comfyui", None)
    if comfyui is not None:
        return str(comfyui.base_url)
    return ""
