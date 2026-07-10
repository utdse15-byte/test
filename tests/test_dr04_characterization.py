"""DR04 characterization tests (§6.4) — the byte-identity pins.

These capture the CURRENT behaviour of the facts DR04 extracts into the shared
``providers/catalog.py`` and the duration rule it lifts out of
``generic_cloud.py``. They are written BEFORE the extraction and must stay green
THROUGH it: routing's ``_catalog``/``resolve`` order, the generic_cloud
duration guard (and its zero-transport-submit property), the body-placeholder
vocabulary and ``render_body`` refusal, and the refbudget byte-identity path.

If any of these move, the extraction changed behaviour — which the contract
forbids (test_providers_routing / test_generic_cloud pin the same surface).

Hermetic: ``MANJU_PROVIDERS_DIR`` → tmp; registry manifest cache reset per test.
"""

from __future__ import annotations

import pytest

import manju.providers.registry as registry_mod
from manju.core.yamlio import write_yaml
from manju.providers import routing
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import (
    GenericCloudProvider,
    _placeholder_map,
    render_body,
)
from manju.providers.manifest import ProviderManifest, load_manifests
from manju.providers.refbudget import allocate
from manju.providers.registry import fallback_chain


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


def _cloud_manifest(pid, caps, per_second=0.05, per_call=0.0, disabled=False, **limits):
    m = {
        "id": pid, "type": "video", "adapter": "generic_cloud",
        "capabilities": caps, "disabled": disabled,
        "auth": {"key_env": f"{pid.upper()}_KEY"},
        "submit": {"url": "https://api.x/v", "body_template": {}, "job_id_path": "$.id"},
        "poll": {"url": "https://api.x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "cost": {"per_second": per_second, "per_call": per_call, "currency": "CNY"},
    }
    if limits:
        m["limits"] = limits
    return m


def _write(providers_dir, pid, data):
    write_yaml(providers_dir / pid / "provider.yaml", data)
    _reset_cache()


# ------------------------------------------------- 1. _catalog manifest facts


def test_char_catalog_projects_manifest_facts(providers_dir):
    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video", "vertical"], per_second=0.05,
                           per_call=0.2, disabled=False))
    cat = routing._catalog()
    v = cat["cc"]
    assert v.kind == "cloud"                       # generic_cloud adapter -> cloud
    assert set(v.capabilities) == {"image_to_video", "vertical"}
    assert v.per_second == 0.05 and v.per_call == 0.2
    assert v.disabled is False and v.exists is True
    assert v.adapter == "generic_cloud"


def test_char_catalog_marks_disabled(providers_dir):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"], disabled=True))
    assert routing._catalog()["cc"].disabled is True


# ------------------------------------------------- 2. built-ins are in the catalog


def test_char_catalog_includes_builtins_as_local(providers_dir):
    cat = routing._catalog()
    for pid in ("caption_card", "ffmpeg_kenburns", "manual_import"):
        assert pid in cat, pid
        assert cat[pid].kind == "local"
        assert cat[pid].exists is True


# ------------------------------------------------- 3. broken manifest never hides healthy


def test_char_broken_manifest_excluded_healthy_retained(providers_dir):
    _write(providers_dir, "good", _cloud_manifest("good", ["image_to_video"]))
    write_yaml(providers_dir / "bad" / "provider.yaml", {"type": "video"})  # no id
    _reset_cache()
    cat = routing._catalog()
    assert "good" in cat                # healthy provider still present
    assert "bad" not in cat             # broken one is simply absent (not hiding good)
    _, errors = load_manifests()
    assert any("bad" in e for e in errors)


# ------------------------------------------------- 4. duplicate id -> load error


def test_char_duplicate_id_is_a_load_error(providers_dir):
    _write(providers_dir, "one", _cloud_manifest("dup", ["image_to_video"]))
    _write(providers_dir, "two", _cloud_manifest("dup", ["image_to_video"]))
    manifests, errors = load_manifests()
    assert "dup" in manifests
    assert any("duplicate provider id" in e for e in errors)


# ------------------------------------------------- 5. §8.4 byte-identical order pin


def test_char_default_strategy_matches_fallback_chain(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    for sid, gen in (("A", {}), ("B", {"provider": "cc"})):
        shot = add_shot(tmp_project, sid, generation=gen)
        preferred = shot.generation.provider
        baseline = ([preferred] if preferred else []) + [
            n for n in fallback_chain(shot) if n != preferred
        ]
        res = routing.resolve(tmp_project, shot, routing.RoutingConfig())
        assert res.order == baseline


# ------------------------------------------------- 6. cheapest ranks by real price


def test_char_cheapest_orders_by_real_price(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "dear", _cloud_manifest("dear", ["image_to_video"], 0.09))
    _write(providers_dir, "cheap", _cloud_manifest("cheap", ["image_to_video"], 0.01))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "cheapest"})
    _reset_cache()
    shot = add_shot(tmp_project, "S1")
    order = routing.resolve(tmp_project, shot).order
    assert order.index("cheap") < order.index("dear")


# ------------------------------------------------- 7. duration guard + zero submits


def _duration_provider(monkeypatch, max_duration_ms):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    m = ProviderManifest.model_validate(
        _cloud_manifest("video_x", ["text_to_video"], max_duration_ms=max_duration_ms))

    class _Transport:
        def __init__(self):
            self.requests = []

        def __call__(self, method, url, headers, body):
            self.requests.append((method, url))
            raise AssertionError("must not reach the network")

    t = _Transport()
    return GenericCloudProvider(m, transport=t, sleep_fn=lambda s: None), t


def test_char_duration_over_limit_rejected_zero_submits(tmp_project, add_shot, monkeypatch):
    provider, transport = _duration_provider(monkeypatch, 6000)
    shot = add_shot(tmp_project, "S1")
    req = GenerationRequest(project=tmp_project, shot=shot, bible={},
                            spec_hash="x", duration_ms=8000)
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert exc.value.kind is FailureKind.invalid
    assert transport.requests == []           # the guard fires BEFORE any POST


def test_char_duration_within_limit_allows_submit(tmp_project, add_shot, monkeypatch):
    # the guard is `duration_ms > limit` (strict) — exactly at the limit passes
    provider, _ = _duration_provider(monkeypatch, 6000)
    shot = add_shot(tmp_project, "S1")
    req = GenerationRequest(project=tmp_project, shot=shot, bible={},
                            spec_hash="x", duration_ms=6000)
    # _enforce_limits must NOT raise at the boundary; submit IS reached and the
    # probe transport trips there. CHARACTERIZATION FLIP (DR06): the probe's
    # raw AssertionError no longer escapes — a raw/unclassified exception past
    # the send boundary is conservatively OUTCOME_UNKNOWN (base.py choke point,
    # DR06 contract test 28). The probe marker inside the failure message still
    # proves submit was reached, which is all this pin ever asserted.
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(req)
    assert "must not reach the network" in str(exc.value)  # submit WAS reached
    from manju.providers.submission import OUTCOME_UNKNOWN_DISPOSITION

    assert exc.value.disposition == OUTCOME_UNKNOWN_DISPOSITION


# ------------------------------------------------- 8. placeholder vocabulary


def test_char_placeholder_map_vocabulary(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S1", generation={"params": {"style": "anime"}})
    req = GenerationRequest(project=tmp_project, shot=shot, bible={},
                            spec_hash="x", duration_ms=4000, params={"seed": 7, "style": "anime"})
    values = _placeholder_map(req)
    for key in ("prompt", "duration_s", "duration_ms", "width", "height", "fps",
                "seed", "shot_id"):
        assert key in values, key
    assert values["style"] == "anime"          # params flow through verbatim


# ------------------------------------------------- 9. render_body refuses unknowns


def test_char_render_body_unknown_placeholder_is_invalid():
    with pytest.raises(ProviderFailure) as exc:
        render_body({"x": "{nope}"}, {"prompt": "p"})
    assert exc.value.kind is FailureKind.invalid


# ------------------------------------------------- 10. refbudget byte-identity


def test_char_refbudget_no_budget_keeps_all_refs():
    from manju.providers.refs import RefItem, RefSet

    items = [
        RefItem(ref="a.png", tier="params", kind="image", path=None, is_url=True, exists=True),
        RefItem(ref="b.png", tier="bible", kind="image", path=None, is_url=True, exists=True),
    ]
    refset = RefSet.from_items(items)

    class _Limits:  # no caps configured
        max_ref_images = None
        max_ref_videos = None

    report = allocate(refset, _Limits(), None, bible=None)
    assert report.active is False                       # no budget block recorded
    assert [it.ref for it in report.selected_images] == ["a.png", "b.png"]
    assert report.omitted == []
