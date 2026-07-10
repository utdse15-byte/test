"""DR04 — spend-free request preflight + routing fail-earlier (§11, 19-38).

``check_request_compatibility`` (``manju.request-compatibility/v1``) validates
ONLY explicit executable facts (§8.3): provider exists/enabled, capability
match, duration vs ``max_duration_ms`` (the SAME rule generic_cloud enforces at
submit), ref counts vs caps/budget, first/last support, and body-template
placeholder completeness. It NEVER infers from story/prompt/shot-size, NEVER
touches the network, and NEVER guesses ``max_resolution``. Preflight failure ⇒
zero transport submits (the generic_cloud guard is the final defense). Routing
skips incompatible candidates with a visible reason; an explicit pin that is
incompatible fails before submit.
"""

from __future__ import annotations

import json

import pytest

import manju.providers.registry as registry_mod
from manju.core.yamlio import write_yaml
from manju.providers import preflight, routing
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.catalog import descriptor_for_manifest, iter_provider_descriptors
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import ProviderManifest
from manju.providers.preflight import (
    SCHEMA,
    STATUS_COMPATIBLE,
    STATUS_INCOMPATIBLE,
    STATUS_UNKNOWN_LEGACY,
    STATUS_WITH_OMISSIONS,
    check_request_compatibility,
    duration_exceeds_limit,
)
from manju.providers.registry import fallback_chain, generate_with_fallback


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    monkeypatch.delenv("MANJU_ROUTING", raising=False)
    registry_mod._manifest_cache = None
    yield root
    registry_mod._manifest_cache = None


def _reset_cache():
    registry_mod._manifest_cache = None


def _manifest(pid="video_x", caps=("text_to_video",), *, per_second=0.05,
              disabled=False, limits=None, refs=None, body=None):
    data = {
        "id": pid, "type": "video", "adapter": "generic_cloud",
        "capabilities": list(caps), "disabled": disabled,
        "auth": {"key_env": f"{pid.upper()}_KEY"},
        "submit": {"url": "https://api.x/v",
                   "body_template": body or {"prompt": "{prompt}"},
                   "job_id_path": "$.id"},
        "poll": {"url": "https://api.x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "cost": {"per_second": per_second, "currency": "CNY"},
    }
    if limits:
        data["limits"] = limits
    if refs:
        data["refs"] = refs
    return ProviderManifest.model_validate(data)


def _desc(**kw):
    return descriptor_for_manifest(_manifest(**kw))


def _write(providers_dir, pid, data):
    write_yaml(providers_dir / pid / "provider.yaml", data)
    _reset_cache()


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        if not self.script:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.script.pop(0)


def _resp(status, payload):
    return HttpResponse(status, {}, json.dumps(payload).encode())


# ================================================ request compatibility (19-32)


def test_19_compatible_happy_path():
    out = check_request_compatibility(
        _desc(caps=("text_to_video",), limits={"max_duration_ms": 6000}),
        capability="text_to_video", duration_ms=4000)
    assert out["schema"] == SCHEMA
    assert out["status"] == STATUS_COMPATIBLE
    assert out["reasons"] == [] and out["omissions"] == []
    assert out["effective"]["duration_ms"] == 4000


def test_20_not_registered_is_incompatible():
    # a descriptor for an absent provider (exists False)
    out = check_request_compatibility(
        {"provider_id": "ghost", "exists": False, "enabled": True,
         "capabilities": [], "source": {"kind": "manifest"}},
        capability="text_to_video", duration_ms=1000)
    assert out["status"] == STATUS_INCOMPATIBLE
    assert any(r["code"] == preflight.R_NOT_REGISTERED for r in out["reasons"])


def test_21_disabled_is_incompatible():
    out = check_request_compatibility(
        _desc(disabled=True), capability="text_to_video", duration_ms=1000)
    assert out["status"] == STATUS_INCOMPATIBLE
    assert any(r["code"] == preflight.R_DISABLED for r in out["reasons"])


def test_22_capability_mismatch_is_incompatible():
    out = check_request_compatibility(
        _desc(caps=("text_to_video",)), capability="first_last_frame",
        duration_ms=1000)
    assert out["status"] == STATUS_INCOMPATIBLE
    assert any(r["code"] == preflight.R_CAPABILITY for r in out["reasons"])


def test_23_duration_over_max_is_incompatible():
    d = _desc(limits={"max_duration_ms": 6000})
    out = check_request_compatibility(d, capability="text_to_video", duration_ms=8000)
    assert out["status"] == STATUS_INCOMPATIBLE
    assert any(r["code"] == preflight.R_DURATION for r in out["reasons"])
    # the SAME rule generic_cloud enforces at submit
    assert duration_exceeds_limit(6000, 8000) is True
    assert duration_exceeds_limit(6000, 6000) is False   # strict >, boundary ok
    assert duration_exceeds_limit(None, 10 ** 9) is False  # unset = no cap


def test_24_preflight_failure_means_zero_transport_submits(tmp_project, add_shot,
                                                            monkeypatch):
    """Preflight INCOMPATIBLE ⇒ the caller never submits; and even if it did,
    the generic_cloud submit-time guard is the final defense (zero POSTs)."""
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    manifest = _manifest(limits={"max_duration_ms": 6000})
    transport = ScriptedTransport([])          # any POST would raise
    provider = GenericCloudProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    shot = add_shot(tmp_project, "S1")
    req = GenerationRequest(project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
                            spec_hash="x", duration_ms=8000, params={"seed": 7})

    compat = check_request_compatibility(
        descriptor_for_manifest(manifest), capability="text_to_video",
        duration_ms=req.duration_ms)
    submitted = 0
    if compat["status"] != STATUS_INCOMPATIBLE:
        provider.generate(req)                 # not reached
        submitted += 1
    assert compat["status"] == STATUS_INCOMPATIBLE
    assert submitted == 0
    assert transport.requests == []            # nothing left the process

    # final defense: calling generate anyway still POSTs zero times
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert exc.value.kind is FailureKind.invalid
    assert transport.requests == []


def test_25_unknown_body_placeholder_is_incompatible():
    d = _desc(body={"prompt": "{prompt}", "style": "{mystyle}"})
    out = check_request_compatibility(
        d, capability="text_to_video", duration_ms=1000,
        params={}, body_placeholders={"prompt", "mystyle"})
    assert out["status"] == STATUS_INCOMPATIBLE
    assert any(r["code"] == preflight.R_PLACEHOLDER for r in out["reasons"])


def test_26_known_placeholders_incl_params_are_compatible():
    d = _desc(body={"prompt": "{prompt}", "style": "{mystyle}"})
    out = check_request_compatibility(
        d, capability="text_to_video", duration_ms=1000,
        params={"mystyle": "anime"}, body_placeholders={"prompt", "mystyle"})
    assert out["status"] == STATUS_COMPATIBLE   # params supply {mystyle}


def test_27_refs_over_capacity_is_omission_not_incompatible():
    d = _desc(caps=("image_to_video",),
              refs={"image_mode": "base64_field", "field": "$.image", "max_images": 1})
    out = check_request_compatibility(
        d, capability="image_to_video", duration_ms=1000, ref_image_count=3)
    assert out["status"] == STATUS_WITH_OMISSIONS
    assert out["effective"]["ref_images"] == 1
    om = next(o for o in out["omissions"] if o["code"] == preflight.O_REF_IMAGES)
    assert om["requested"] == 3 and om["delivered"] == 1


def test_28_same_effective_refs_preflight_vs_submit(tmp_project, add_shot, monkeypatch):
    """The effective ref count preflight reports equals what the fake adapter
    actually delivers — because both go through the ONE refs resolver +
    refbudget allocator, never a second one."""
    from manju.providers.refbudget import allocate

    monkeypatch.setenv("VIDEO_X_KEY", "k")
    refs_dir = tmp_project.root / "media" / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    (refs_dir / "a.png").write_bytes(b"\x89PNG\r\n\x1a\naaa")
    (refs_dir / "b.png").write_bytes(b"\x89PNG\r\n\x1a\nbbb")

    manifest = _manifest(
        caps=("image_to_video",),
        refs={"image_mode": "base64_field", "field": "$.image", "max_images": 2})
    provider = GenericCloudProvider(
        manifest, transport=ScriptedTransport([_resp(200, {"id": "job"})]),
        sleep_fn=lambda s: None)

    shot = add_shot(tmp_project, "S1")
    req = GenerationRequest(
        project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
        spec_hash="x", duration_ms=4000,
        params={"seed": 7, "images": ["media/refs/a.png", "media/refs/b.png"]})

    refset = req.refset()
    declared = len(refset.image_items())
    budget = allocate(refset, manifest.limits, req.shot, bible=req.bible)
    expected = budget.selected_images[: manifest.refs.max_images]

    compat = check_request_compatibility(
        descriptor_for_manifest(manifest), capability="image_to_video",
        duration_ms=req.duration_ms, ref_image_count=declared)

    provider.submit(req)  # runs the ONE POST, records ref_delivery on req.params
    delivered = req.params["ref_delivery"]["images"]

    assert declared == 2
    assert compat["effective"]["ref_images"] == len(expected) == len(delivered) == 2
    assert compat["status"] == STATUS_COMPATIBLE


def test_29_builtin_no_facts_is_unknown_legacy_never_skipped():
    # caption_card is the safety net: preflight must NOT declare it INCOMPATIBLE
    descriptors, _ = iter_provider_descriptors()
    cc = next(d for d in descriptors if d.provider_id == "caption_card")
    out = check_request_compatibility(cc, capability="caption_card", duration_ms=999999)
    assert out["status"] == STATUS_UNKNOWN_LEGACY
    assert out["reasons"] == []   # no executable facts, so never a hard skip


def test_30_max_resolution_warns_never_blocks():
    d = _desc(limits={"max_resolution": "1080x1920"})
    out = check_request_compatibility(
        d, capability="text_to_video", duration_ms=1000, width=4096, height=4096)
    assert out["status"] == STATUS_COMPATIBLE     # opaque limit never blocks
    assert any(w["code"] == preflight.WARN_LEGACY_RESOLUTION for w in out["warnings"])


def test_31_first_last_unsupported_is_omission():
    d = _desc(caps=("text_to_video",))   # no first_last_frame capability
    out = check_request_compatibility(
        d, capability="text_to_video", duration_ms=1000, first_last=True)
    assert out["status"] == STATUS_WITH_OMISSIONS
    assert any(o["code"] == preflight.O_FIRST_LAST for o in out["omissions"])
    assert out["effective"]["first_last"] is False


def test_32_never_infers_from_prompt_or_shot_size():
    d = _desc(caps=("text_to_video",), limits={"max_duration_ms": 6000})
    base = check_request_compatibility(d, capability="text_to_video", duration_ms=4000)
    # a giant prompt / shot-size-ish params must not change the verdict — only
    # explicit executable facts are consulted.
    noisy = check_request_compatibility(
        d, capability="text_to_video", duration_ms=4000,
        params={"prompt": "x" * 5000, "shot_size": "close_up", "story": "..."})
    assert base["status"] == noisy["status"] == STATUS_COMPATIBLE
    assert base["reasons"] == noisy["reasons"] == []


# ================================================ routing fail-earlier (33-35, 38)


def test_33_routing_skips_duration_incompatible_with_visible_reason(
        providers_dir, tmp_project, add_shot):
    _write(providers_dir, "capped",
           _cloud_yaml("capped", ["image_to_video"], per_second=0.01,
                       limits={"max_duration_ms": 3000}))
    _write(providers_dir, "roomy",
           _cloud_yaml("roomy", ["image_to_video"], per_second=0.09))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "cheapest"})
    _reset_cache()
    shot = add_shot(tmp_project, "S1", duration=5.0)  # 5000ms > capped's 3000
    res = routing.resolve(tmp_project, shot)
    # the cheapest STRATEGY does not prefer the incompatible (cheaper) provider…
    assert res.order[0] == "roomy"
    # …and the skip is recorded with a visible reason (debuggability).
    assert any(s.get("provider") == "capped" and "max_duration_ms" in s["reason"]
               for s in res.skipped)
    # the §8.4 safety net stays exhaustive (fallback ORDER unchanged) — capped
    # may still ride the tail, where generic_cloud's submit guard is the backstop.
    assert res.order[-1] == "caption_card"


def test_34_explicit_incompatible_pin_fails_before_submit(
        providers_dir, tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("CAPPED_KEY", "k")
    _write(providers_dir, "capped",
           _cloud_yaml("capped", ["text_to_video"], limits={"max_duration_ms": 3000}))
    shot = add_shot(tmp_project, "S1", duration=5.0,
                    generation={"provider": "capped",
                                "fallback": ["still_frame_motion", "caption_card"]})
    req = GenerationRequest(project=tmp_project, shot=shot, bible={},
                            spec_hash="x", duration_ms=5000)
    with pytest.raises(ProviderFailure) as exc:
        generate_with_fallback(req)
    assert exc.value.kind is FailureKind.invalid
    assert "capped" in exc.value.message
    # never silently replaced by the fallback chain
    assert "max_duration" in exc.value.message or "3000" in exc.value.message


def test_35_cheapest_compares_only_compatible_candidates(
        providers_dir, tmp_project, add_shot):
    _write(providers_dir, "capped_cheap",
           _cloud_yaml("capped_cheap", ["image_to_video"], per_second=0.01,
                       limits={"max_duration_ms": 2000}))
    _write(providers_dir, "roomy_dear",
           _cloud_yaml("roomy_dear", ["image_to_video"], per_second=0.09))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "cheapest"})
    _reset_cache()
    shot = add_shot(tmp_project, "S1", duration=5.0)
    order = routing.resolve(tmp_project, shot).order
    assert order[0] == "roomy_dear"   # the cheaper one is incompatible → excluded


def test_38_regression_default_order_and_explain_surfaces_skip(
        providers_dir, tmp_project, add_shot):
    # (a) default strategy order stays byte-identical (no compatible-cap involved)
    _write(providers_dir, "cc", _cloud_yaml("cc", ["image_to_video"]))
    shot = add_shot(tmp_project, "A")
    baseline = [n for n in fallback_chain(shot)]
    res = routing.resolve(tmp_project, shot, routing.RoutingConfig())
    assert res.order == baseline

    # (b) a rule whose use is duration-incompatible → skip surfaces in explain
    _write(providers_dir, "capped",
           _cloud_yaml("capped", ["image_to_video"], limits={"max_duration_ms": 2000}))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "mine",
        "strategies": {"mine": {"rules": [
            {"match": {}, "use": "capped"}], "else": "fallback"}}})
    _reset_cache()
    shot2 = add_shot(tmp_project, "B", duration=5.0)
    info = routing.explain(tmp_project, shot2)
    # the rule's incompatible provider is recorded as skipped (visible in explain)
    assert any(s.get("provider") == "capped" and "max_duration_ms" in s["reason"]
               for s in info["skipped"])
    assert info["order"][-1] == "caption_card"   # safety net intact + terminal


def test_placeholder_vocabulary_is_single_source(tmp_project, add_shot):
    """The preflight body-placeholder vocabulary IS the one generic_cloud fills
    at submit — they must never drift (single source)."""
    from manju.providers.generic_cloud import _placeholder_map
    from manju.providers.preflight import BASE_PLACEHOLDER_KEYS

    shot = add_shot(tmp_project, "S1")
    # request params EMPTY so the map's keys are exactly the base vocabulary
    req = GenerationRequest(project=tmp_project, shot=shot, bible={},
                            spec_hash="x", duration_ms=4000, params={})
    assert set(_placeholder_map(req)) == set(BASE_PLACEHOLDER_KEYS)


# ------------------------------------------------------------------- helper


def _cloud_yaml(pid, caps, per_second=0.05, disabled=False, limits=None):
    m = {
        "id": pid, "type": "video", "adapter": "generic_cloud",
        "capabilities": caps, "disabled": disabled,
        "auth": {"key_env": f"{pid.upper()}_KEY"},
        "submit": {"url": "https://api.x/v", "body_template": {"prompt": "{prompt}"},
                   "job_id_path": "$.id"},
        "poll": {"url": "https://api.x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "cost": {"per_second": per_second, "currency": "CNY"},
    }
    if limits:
        m["limits"] = limits
    return m
