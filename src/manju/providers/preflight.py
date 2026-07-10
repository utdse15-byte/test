"""Spend-free request preflight (DR04) — ``manju.request-compatibility/v1``.

Answers "will THIS request run on THIS provider, and what would be silently
dropped?" from EXPLICIT executable facts ONLY (contract §8.3) — never a network
call, never a guess from story / prompt / shot-size, never an interpretation of
the opaque ``max_resolution``. The checks:

* provider exists / enabled,
* capability match,
* duration vs ``max_duration_ms`` — the SAME rule generic_cloud enforces at
  submit (:func:`duration_exceeds_limit`, which its guard now delegates to),
* body-template placeholder completeness (an unknown placeholder is
  INCOMPATIBLE before submit — a silently-empty field must never reach a paid
  API),
* reference counts vs the provider's caps/budget → OMISSIONS (never a hard
  block),
* first/last-frame support → an omission when requested but unsupported.

A preflight verdict is advisory: it enables a caller to skip a submit, but the
provider's own submit-time guard remains the final defense. This module is pure
(no imports of the network adapters) so it stays a leaf both routing and
generic_cloud can depend on.
"""

from __future__ import annotations

import string
from typing import Any

SCHEMA = "manju.request-compatibility/v1"

# status (contract §8.1)
STATUS_COMPATIBLE = "COMPATIBLE"
STATUS_WITH_OMISSIONS = "COMPATIBLE_WITH_OMISSIONS"
STATUS_INCOMPATIBLE = "INCOMPATIBLE"
STATUS_UNKNOWN_LEGACY = "UNKNOWN_LEGACY"

# incompatibility reason codes
R_NOT_REGISTERED = "PROVIDER_NOT_REGISTERED"
R_DISABLED = "PROVIDER_DISABLED"
R_CAPABILITY = "CAPABILITY_UNSUPPORTED"
R_DURATION = "DURATION_OVER_MAX"
R_PLACEHOLDER = "UNKNOWN_BODY_PLACEHOLDER"

# omission codes (delivered, but with a documented loss)
O_REF_IMAGES = "REF_IMAGES_OMITTED"
O_REF_VIDEOS = "REF_VIDEOS_OMITTED"
O_REFS_UNCONSUMED = "REFS_NOT_CONSUMED"
O_FIRST_LAST = "FIRST_LAST_UNSUPPORTED"

# warning codes (never change the status)
WARN_LEGACY_RESOLUTION = "LEGACY_UNINTERPRETABLE_LIMIT"

# The body_template placeholder vocabulary (§8.6) — the base names available to
# EVERY request, before the shot's own ``generation.params`` are added. This is
# the SINGLE source of that vocabulary: ``generic_cloud._placeholder_map`` builds
# its value dict on exactly these keys (pinned by
# ``test_dr04_characterization`` + the cross-check in the preflight suite).
BASE_PLACEHOLDER_KEYS = frozenset({
    "prompt", "duration_s", "duration_ms", "width", "height", "fps", "seed", "shot_id",
})


def duration_exceeds_limit(max_duration_ms: int | None, duration_ms: int | None) -> bool:
    """The ONE duration rule, extracted from ``generic_cloud._enforce_limits``.

    ``True`` iff a real cap is set (``None``/``0`` means "no cap", mirroring the
    old ``if limit and duration_ms > limit``) and the request exceeds it
    STRICTLY (``duration_ms > limit``). Both the submit-time guard and preflight
    call this, so they can never disagree."""
    if not max_duration_ms:
        return False
    if duration_ms is None:
        return False
    return duration_ms > max_duration_ms


def iter_body_placeholders(template: Any) -> set[str]:
    """The set of placeholder root names a ``body_template`` references.

    Walks dicts/lists/strings like ``render_body`` does and collects every
    ``{name}`` field's root (``{a.b}``/``{a[0]}`` → ``a``). Escaped ``{{``/``}}``
    yield nothing. Used to detect a placeholder no value can fill BEFORE submit."""
    out: set[str] = set()
    _collect_placeholders(template, out)
    return out


def _collect_placeholders(t: Any, out: set[str]) -> None:
    if isinstance(t, dict):
        for v in t.values():
            _collect_placeholders(v, out)
    elif isinstance(t, list):
        for v in t:
            _collect_placeholders(v, out)
    elif isinstance(t, str):
        try:
            for _, field_name, _, _ in string.Formatter().parse(t):
                if field_name:
                    root = field_name.split(".")[0].split("[")[0].strip()
                    if root:
                        out.add(root)
        except ValueError:
            # a malformed format string ("{" alone) — render_body would raise on
            # it too; treat as no discoverable placeholder here.
            pass


class _Facts:
    """The executable facts preflight reads, normalized from a
    ``ProviderDescriptor`` or a projection/plain dict."""

    __slots__ = (
        "provider_id", "exists", "enabled", "capabilities", "has_facts",
        "max_duration_ms", "max_resolution", "refs_image_mode", "refs_video_mode",
        "refs_max_images", "refs_max_videos", "max_ref_images", "max_ref_videos",
        "first_last_supported",
    )

    def __init__(self, entry: Any):
        # duck-typed: a ProviderDescriptor exposes attributes; a dict is a
        # projection/plain entry.
        if isinstance(entry, dict):
            self._from_dict(entry)
        else:
            self._from_descriptor(entry)

    def _from_descriptor(self, d: Any) -> None:
        self.provider_id = getattr(d, "provider_id", None)
        self.exists = bool(getattr(d, "exists", True))
        self.enabled = bool(getattr(d, "enabled", True))
        self.capabilities = set(getattr(d, "capabilities", ()) or ())
        self.has_facts = (getattr(d, "source_kind", None) == "manifest"
                          or bool(self.capabilities))
        self.max_duration_ms = getattr(d, "max_duration_ms", None)
        self.max_resolution = getattr(d, "max_resolution", None)
        self.refs_image_mode = getattr(d, "refs_image_mode", "none")
        self.refs_video_mode = getattr(d, "refs_video_mode", "none")
        self.refs_max_images = int(getattr(d, "refs_max_images", 0) or 0)
        self.refs_max_videos = int(getattr(d, "refs_max_videos", 0) or 0)
        self.max_ref_images = getattr(d, "max_ref_images", None)
        self.max_ref_videos = getattr(d, "max_ref_videos", None)
        self.first_last_supported = bool(getattr(d, "first_last_supported", False))

    def _from_dict(self, e: dict) -> None:
        source = e.get("source") if isinstance(e.get("source"), dict) else {}
        source_kind = source.get("kind") or e.get("source_kind")
        limits = e.get("limits") or {}
        refs = e.get("refs") or {}
        self.provider_id = e.get("provider_id")
        self.exists = bool(e.get("exists", True))
        self.enabled = bool(e.get("enabled", True))
        self.capabilities = _caps_from_entry(e)
        self.has_facts = (source_kind == "manifest") or bool(self.capabilities)
        self.max_duration_ms = limits.get("max_duration_ms")
        self.max_resolution = limits.get("max_resolution")
        self.refs_image_mode = refs.get("image_mode", "none")
        self.refs_video_mode = refs.get("video_mode", "none")
        self.refs_max_images = int(refs.get("max_images", 0) or 0)
        self.refs_max_videos = int(refs.get("max_videos", 0) or 0)
        self.max_ref_images = limits.get("max_ref_images")
        self.max_ref_videos = limits.get("max_ref_videos")
        self.first_last_supported = bool(refs.get("first_last", False))


def _caps_from_entry(e: dict) -> set[str]:
    caps: set[str] = set()
    for c in e.get("capabilities") or []:
        if isinstance(c, dict):
            pid = c.get("profile_id", "")
            if "#" in pid:
                caps.add(pid.split("#", 1)[1])
        elif c:
            caps.add(str(c))
    return caps


def _available_placeholders(params: dict | None) -> set[str]:
    return set(BASE_PLACEHOLDER_KEYS) | set((params or {}).keys())


def check_request_compatibility(
    entry: Any,
    *,
    capability: str,
    duration_ms: int | None,
    width: int | None = None,
    height: int | None = None,
    ref_image_count: int = 0,
    ref_video_count: int = 0,
    first_last: bool = False,
    params: dict | None = None,
    body_placeholders: Any = None,
) -> dict:
    """Assess one request against one provider from explicit facts (§8.3).

    ``entry`` is a :class:`~manju.providers.catalog.ProviderDescriptor` or a
    projection/plain dict. ``ref_image_count``/``ref_video_count`` are the counts
    the shot RESOLVED to (from the ONE refs resolver — never re-resolved here);
    preflight reports how many of them fit the provider's caps and what is
    omitted. Returns a ``manju.request-compatibility/v1`` document."""
    f = _Facts(entry)
    reasons: list[dict] = []
    omissions: list[dict] = []
    warnings: list[dict] = []

    # No executable facts (a built-in / no manifest) → UNKNOWN_LEGACY, and NEVER
    # a hard skip: the fallback safety net must never be declared incompatible.
    if not f.has_facts:
        return _document(f.provider_id, capability, STATUS_UNKNOWN_LEGACY,
                         _effective(capability, duration_ms, ref_image_count,
                                    ref_video_count, width, height, first_last),
                         [], [], [])

    # 1. provider exists / enabled
    if not f.exists:
        reasons.append({"code": R_NOT_REGISTERED,
                        "detail": f"provider {f.provider_id!r} is not registered / has no manifest"})
    elif not f.enabled:
        reasons.append({"code": R_DISABLED,
                        "detail": f"provider {f.provider_id!r} is disabled"})

    # 2. capability
    if capability not in f.capabilities:
        reasons.append({"code": R_CAPABILITY,
                        "detail": f"provider does not advertise capability {capability!r} "
                                  f"(has: {sorted(f.capabilities)})"})

    # 3. duration — the SAME rule generic_cloud enforces at submit
    if duration_exceeds_limit(f.max_duration_ms, duration_ms):
        reasons.append({"code": R_DURATION,
                        "detail": f"duration {duration_ms}ms exceeds max_duration_ms "
                                  f"{f.max_duration_ms}ms"})

    # 4. body-template placeholder completeness (unknown → INCOMPATIBLE)
    placeholders = (iter_body_placeholders(body_placeholders)
                    if not isinstance(body_placeholders, (set, frozenset, list, tuple, type(None)))
                    else set(body_placeholders or ()))
    if placeholders:
        unknown = sorted(placeholders - _available_placeholders(params))
        if unknown:
            reasons.append({"code": R_PLACEHOLDER,
                            "detail": f"body_template placeholder(s) have no value: {unknown}"})

    # 5. reference capacity (omissions, never incompatible)
    eff_images = _ref_capacity(
        ref_image_count, f.refs_image_mode, f.refs_max_images, f.max_ref_images,
        O_REF_IMAGES, "image", omissions)
    eff_videos = _ref_capacity(
        ref_video_count, f.refs_video_mode, f.refs_max_videos, f.max_ref_videos,
        O_REF_VIDEOS, "video", omissions)

    # 6. first/last-frame — requested but unsupported → omission
    eff_first_last = bool(first_last and f.first_last_supported)
    if first_last and not f.first_last_supported:
        omissions.append({"code": O_FIRST_LAST,
                          "detail": "provider does not support first/last-frame delivery — "
                                    "the start/end frames will not be sent"})

    # 7. max_resolution — opaque, NEVER blocks; only a warning when a w/h is given
    if f.max_resolution and (width is not None or height is not None):
        warnings.append({"code": WARN_LEGACY_RESOLUTION,
                         "detail": f"max_resolution {f.max_resolution!r} is an opaque, "
                                   "uninterpretable limit — width/height are NOT checked "
                                   "against it (never blocked, never guessed)"})

    status = (STATUS_INCOMPATIBLE if reasons
              else STATUS_WITH_OMISSIONS if omissions
              else STATUS_COMPATIBLE)
    effective = _effective(capability, duration_ms, eff_images, eff_videos,
                           width, height, eff_first_last)
    return _document(f.provider_id, capability, status, effective,
                     omissions, reasons, warnings)


def _ref_capacity(requested: int, mode: str, field_cap: int, budget_cap: int | None,
                  code: str, kind: str, omissions: list[dict]) -> int:
    """Effective delivered count = min(requested, field cap, budget cap) — the
    SAME clamp submit applies (``budget.selected[:refs.max_*]``). Any shortfall
    is a recorded omission, never a block."""
    requested = max(0, int(requested or 0))
    if requested == 0:
        return 0
    if mode == "none":
        omissions.append({"code": O_REFS_UNCONSUMED,
                          "detail": f"provider does not consume reference {kind}s "
                                    f"({kind}_mode: none) — {requested} ref(s) will be dropped",
                          "requested": requested, "delivered": 0})
        return 0
    cap = int(field_cap or 0)
    if budget_cap is not None:
        cap = min(cap, int(budget_cap))
    delivered = max(0, min(requested, cap))
    if delivered < requested:
        omissions.append({"code": code,
                          "detail": f"only {delivered} of {requested} reference {kind}(s) "
                                    "fit the provider caps — the rest are omitted",
                          "requested": requested, "delivered": delivered})
    return delivered


def _effective(capability, duration_ms, ref_images, ref_videos, width, height,
               first_last) -> dict:
    return {
        "capability": capability,
        "duration_ms": duration_ms,
        "ref_images": ref_images,
        "ref_videos": ref_videos,
        "width": width,
        "height": height,
        "first_last": first_last,
    }


def _document(provider_id, capability, status, effective, omissions, reasons,
              warnings) -> dict:
    return {
        "schema": SCHEMA,
        "provider_id": provider_id,
        "capability": capability,
        "status": status,
        "effective": effective,
        "omissions": omissions,
        "reasons": reasons,
        "warnings": warnings,
    }
