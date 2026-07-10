"""Shared provider capability projection (DR04).

The ONE place provider FACTS are gathered — built-ins + user manifests — so
routing, the CLI and the spend-free preflight all read the same descriptors
instead of re-deriving them. Historically routing owned this in a private
``_catalog()``/``_ProviderView`` (routing.py); that fact-gathering now lives
here and routing is a thin consumer (its behaviour stays byte-identical, pinned
by ``test_providers_routing`` + ``test_dr04_characterization``).

Two public shapes:

* :func:`iter_provider_descriptors` — the flat, typed view every consumer reads
  (``ProviderDescriptor``), plus the structured ``errors`` from broken
  manifests (a broken declaration NEVER hides a healthy provider, §8.2).
* :func:`project_provider_capabilities` — the DERIVED
  ``manju.provider-capability-projection/v1`` document (contract §7.2):
  deterministic, digestible, and free of secrets / absolute paths / credential
  presence. Delete/rebuild safe; never a build/resume/cache input.

The per-profile identity :func:`provider_profile_digest` is a deliverable the
next batch (AI_IDE_06) derives submission identity from — it must stay stable
and is documented here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.hashing import hash_value

SCHEMA = "manju.provider-capability-projection/v1"

# Built-in providers carry no manifest ``type`` — a small honest map (DATA in
# this module, never a guess baked into the provider classes). Falls back to the
# live instance's ``kind`` for any future built-in.
_BUILTIN_TYPES: dict[str, str] = {
    "manual_import": "manual",
    "ffmpeg_kenburns": "video",
    "caption_card": "video",
}


@dataclass(frozen=True)
class ProviderDescriptor:
    """Every fact a consumer needs about one provider, gathered once.

    The routing-facing subset (``kind``/``capabilities``/``per_second``/
    ``per_call``/``disabled``/``exists``/``adapter``/``max_duration_ms``) mirrors
    what the old ``_ProviderView`` carried; the rest feeds the projection and the
    preflight. ``manifest`` is the live ``ProviderManifest`` (``None`` for a
    built-in) — kept for downstream reads (e.g. body-template placeholders); it
    is NEVER serialised into the projection.
    """

    provider_id: str
    provider_type: str
    adapter: str
    kind: str                    # "local" | "cloud"
    enabled: bool
    exists: bool
    source_kind: str             # "builtin" | "manifest"
    capabilities: tuple[str, ...]
    per_second: float
    per_call: float
    currency: str | None
    max_duration_ms: int | None
    max_resolution: str | None   # opaque string, surfaced VERBATIM (never guessed)
    max_concurrent: int | None
    rate_limit_per_min: int | None
    max_ref_images: int | None   # manifest-level budget cap
    max_ref_videos: int | None
    refs_image_mode: str
    refs_video_mode: str
    refs_max_images: int         # per-field API-shape cap
    refs_max_videos: int
    first_last_supported: bool
    credential_env: str | None   # auth.key_env NAME only (never a value/presence)
    free_probe_available: bool
    manifest: Any = field(default=None, compare=False, repr=False)


def _cloud_adapters() -> set[str]:
    from .manifest import GENERIC_ADAPTER, GENERIC_ASR_ADAPTER, GENERIC_TTS_ADAPTER

    return {GENERIC_ADAPTER, GENERIC_TTS_ADAPTER, GENERIC_ASR_ADAPTER}


def descriptor_for_manifest(manifest: Any) -> ProviderDescriptor:
    """Build a :class:`ProviderDescriptor` from ONE manifest object, without
    scanning disk — the single-manifest view preflight/tests use. ``kind`` is
    inferred from the adapter exactly as the disk scan's manifest loop does."""
    from .manifest import COMFYUI_ADAPTER

    lim = manifest.limits
    refs = manifest.refs
    caps = tuple(manifest.capabilities)
    from .manifest import FIRST_LAST_CAPABILITY

    first_last = (refs.first_last_mode != "none"
                  and FIRST_LAST_CAPABILITY in manifest.capabilities)
    free_probe = (manifest.adapter == COMFYUI_ADAPTER) or bool(manifest.ping_url)
    return ProviderDescriptor(
        provider_id=manifest.id,
        provider_type=manifest.type,
        adapter=manifest.adapter,
        kind="cloud" if manifest.adapter in _cloud_adapters() else "local",
        enabled=not bool(getattr(manifest, "disabled", False)),
        exists=True,
        source_kind="manifest",
        capabilities=caps,
        per_second=float(manifest.cost.per_second),
        per_call=float(manifest.cost.per_call),
        currency=manifest.cost.currency,
        max_duration_ms=lim.max_duration_ms,
        max_resolution=lim.max_resolution,
        max_concurrent=lim.max_concurrent,
        rate_limit_per_min=lim.rate_limit_per_min,
        max_ref_images=lim.max_ref_images,
        max_ref_videos=lim.max_ref_videos,
        refs_image_mode=refs.image_mode,
        refs_video_mode=refs.video_mode,
        refs_max_images=refs.max_images,
        refs_max_videos=refs.max_videos,
        first_last_supported=first_last,
        credential_env=manifest.auth.key_env,
        free_probe_available=free_probe,
        manifest=manifest,
    )


def _builtin_descriptor(pid: str, prov: Any) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider_id=pid,
        provider_type=_BUILTIN_TYPES.get(pid, getattr(prov, "kind", "local")),
        adapter="",
        kind=prov.kind,
        enabled=True,
        exists=True,
        source_kind="builtin",
        capabilities=(),
        per_second=0.0,
        per_call=0.0,
        currency=None,
        max_duration_ms=None,
        max_resolution=None,
        max_concurrent=None,
        rate_limit_per_min=None,
        max_ref_images=None,
        max_ref_videos=None,
        refs_image_mode="none",
        refs_video_mode="none",
        refs_max_images=0,
        refs_max_videos=0,
        first_last_supported=False,
        credential_env=None,
        free_probe_available=False,
        manifest=None,
    )


def iter_provider_descriptors() -> tuple[list[ProviderDescriptor], list[str]]:
    """Every provider Manju knows about (built-ins + user manifests), disabled
    ones included, plus structured ``errors`` for broken manifests.

    Mirrors the old ``routing._catalog()`` fact-gathering EXACTLY so routing is
    byte-identical: manifests first (kind by adapter), then the live registry
    refines ``kind`` for any provider it constructed and adds the built-ins.
    Broken (unloadable) manifests never enter the descriptor list — they are
    collected as ``errors`` (they must not hide a healthy provider)."""
    from .manifest import load_manifests
    from .registry import available_providers

    by_id: dict[str, ProviderDescriptor] = {}
    manifests, errors = load_manifests()  # includes disabled + broken declarations
    for pid, m in manifests.items():
        by_id[pid] = descriptor_for_manifest(m)
    for pid, prov in available_providers().items():
        existing = by_id.get(pid)
        if existing is not None:
            if existing.kind != prov.kind:  # authoritative kind from the live instance
                by_id[pid] = _replace_kind(existing, prov.kind)
        else:
            by_id[pid] = _builtin_descriptor(pid, prov)
    descriptors = [by_id[pid] for pid in sorted(by_id)]
    return descriptors, errors


def _replace_kind(d: ProviderDescriptor, kind: str) -> ProviderDescriptor:
    from dataclasses import replace

    return replace(d, kind=kind)


# --------------------------------------------------------------- projection


def _limits_dict(d: ProviderDescriptor) -> dict[str, Any]:
    # max_resolution is surfaced VERBATIM (opaque, §8.6 option 2) — NEVER parsed
    # into max_width/max_height; those structured fields do not exist (no fixture
    # proves the need — SKIPPED_WITH_EVIDENCE), so they are never invented here.
    return {
        "max_duration_ms": d.max_duration_ms,
        "max_resolution": d.max_resolution,
        "max_concurrent": d.max_concurrent,
        "rate_limit_per_min": d.rate_limit_per_min,
        "max_ref_images": d.max_ref_images,
        "max_ref_videos": d.max_ref_videos,
    }


def _refs_dict(d: ProviderDescriptor) -> dict[str, Any]:
    return {
        "image_mode": d.refs_image_mode,
        "video_mode": d.refs_video_mode,
        "max_images": d.refs_max_images,
        "max_videos": d.refs_max_videos,
        "first_last": d.first_last_supported,
    }


def _cost_dict(d: ProviderDescriptor) -> dict[str, Any]:
    return {"per_second": d.per_second, "per_call": d.per_call, "currency": d.currency}


def _credential_requirements(d: ProviderDescriptor) -> list[str]:
    # names ONLY — never whether the env var is set (that presence is credential
    # information the projection/digest must exclude).
    return [d.credential_env] if d.credential_env else []


def profile_id(provider_id: str, capability: str) -> str:
    """The stable per-capability profile identity string (contract §7.2)."""
    return f"provider:{provider_id}#{capability}"


def _profile_facts(d: ProviderDescriptor, capability: str) -> dict[str, Any]:
    return {
        "profile_id": profile_id(d.provider_id, capability),
        "provider_id": d.provider_id,
        "provider_type": d.provider_type,
        "adapter": d.adapter,
        "capability": capability,
        "limits": _limits_dict(d),
        "refs": _refs_dict(d),
        "cost": _cost_dict(d),
    }


def provider_profile_digest(provider_id: str, capability: str, *,
                            descriptor: ProviderDescriptor | None = None) -> str:
    """Stable ``sha256:`` identity for one provider capability profile.

    **AI_IDE_06 deliverable.** The next batch derives a submission's identity
    from this digest, so it MUST be a deterministic function of the profile's
    semantic facts (provider id + capability + limits + refs + cost) and move
    only when one of those changes. It excludes credential values/presence,
    paths and mtime. Pass ``descriptor`` to avoid a disk rescan; otherwise it is
    resolved from :func:`iter_provider_descriptors`."""
    if descriptor is None:
        descriptors, _ = iter_provider_descriptors()
        descriptor = next((x for x in descriptors if x.provider_id == provider_id), None)
        if descriptor is None:
            return hash_value({"profile_id": profile_id(provider_id, capability),
                               "provider_id": provider_id, "capability": capability,
                               "missing": True})
    return hash_value(_profile_facts(descriptor, capability))


def _provider_semantic_facts(d: ProviderDescriptor) -> dict[str, Any]:
    """The provider-level identity input for ``source.semantic_digest`` — every
    semantic fact, and NOTHING incidental (no path, no mtime, no credential
    value or presence; credential requirement is the env-var NAME only)."""
    return {
        "provider_id": d.provider_id,
        "provider_type": d.provider_type,
        "adapter": d.adapter,
        "kind": d.kind,
        "enabled": d.enabled,
        "source_kind": d.source_kind,
        "capabilities": sorted(d.capabilities),
        "limits": _limits_dict(d),
        "refs": _refs_dict(d),
        "cost": _cost_dict(d),
        "credential_requirements": sorted(_credential_requirements(d)),
        "free_probe_available": d.free_probe_available,
    }


def provider_source_digest(d: ProviderDescriptor) -> str:
    return hash_value(_provider_semantic_facts(d))


def _provider_entry(d: ProviderDescriptor) -> dict[str, Any]:
    return {
        "provider_id": d.provider_id,
        "provider_type": d.provider_type,
        "adapter": d.adapter,
        "enabled": d.enabled,
        "source": {"kind": d.source_kind, "semantic_digest": provider_source_digest(d)},
        "capabilities": [
            {
                "profile_id": profile_id(d.provider_id, cap),
                "limits": _limits_dict(d),
                "refs": _refs_dict(d),
                "cost": _cost_dict(d),
                "profile_digest": provider_profile_digest(d.provider_id, cap, descriptor=d),
            }
            for cap in sorted(d.capabilities)
        ],
        "credential_requirements": _credential_requirements(d),
        "free_probe_available": d.free_probe_available,
        "limits": _limits_dict(d),
    }


def _scrub_errors(errors: list[str]) -> list[str]:
    """Broken-manifest errors are surfaced (never hidden) but must carry no
    absolute path — replace the absolute providers root with a placeholder."""
    from .manifest import providers_dir

    root = str(providers_dir())
    return [e.replace(root, "<providers>") for e in errors]


def project_provider_capabilities() -> dict[str, Any]:
    """The DERIVED ``manju.provider-capability-projection/v1`` document.

    Deterministic (providers sorted; no timestamps), digestible
    (``projection_digest`` over the provider array via ``hash_value``), and free
    of secrets / absolute paths / credential presence. Pure function of the
    built-ins + on-disk manifests — safe to delete and rebuild, never a
    build/resume/cache input."""
    descriptors, errors = iter_provider_descriptors()
    providers = [_provider_entry(d) for d in descriptors]
    return {
        "schema": SCHEMA,
        "providers": providers,
        "projection_digest": hash_value(providers),
        "errors": _scrub_errors(errors),
    }
