"""DR04 — shared provider capability projection (contract §11, items 1-18, 36-37).

The projection (``manju.provider-capability-projection/v1``) is DERIVED: a pure
function of the built-ins + user manifests, deterministic, digestible, and free
of secrets / absolute paths / credential presence. These tests pin the schema,
determinism, the profile digests AI_IDE_06 depends on, the opaque
``max_resolution`` handling, and the two CLI surfaces (``providers catalog`` +
the extended ``providers check --json``).
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import manju.providers.registry as registry_mod
from manju.cli import app
from manju.core.yamlio import write_yaml
from manju.providers import catalog
from manju.providers.catalog import (
    SCHEMA,
    iter_provider_descriptors,
    project_provider_capabilities,
    provider_profile_digest,
)

runner = CliRunner()


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


def _cloud_manifest(pid, caps, per_second=0.05, per_call=0.0, disabled=False,
                    **extra):
    m = {
        "id": pid, "type": "video", "adapter": "generic_cloud",
        "capabilities": caps, "disabled": disabled,
        "auth": {"key_env": f"{pid.upper()}_KEY"},
        "submit": {"url": "https://api.x/v",
                   "body_template": {"prompt": "{prompt}"}, "job_id_path": "$.id"},
        "poll": {"url": "https://api.x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "cost": {"per_second": per_second, "per_call": per_call, "currency": "CNY"},
    }
    m.update(extra)
    return m


def _write(providers_dir, pid, data):
    write_yaml(providers_dir / pid / "provider.yaml", data)
    _reset_cache()


def _find(projection, pid):
    return next(p for p in projection["providers"] if p["provider_id"] == pid)


# ============================================= projection determinism (1-10)


def test_01_schema_string(providers_dir):
    proj = project_provider_capabilities()
    assert proj["schema"] == SCHEMA == "manju.provider-capability-projection/v1"


def test_02_digest_deterministic_across_calls(providers_dir):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    a = project_provider_capabilities()
    _reset_cache()
    b = project_provider_capabilities()
    assert a["projection_digest"] == b["projection_digest"]
    assert a == b


def test_03_digest_excludes_credential_presence(providers_dir, monkeypatch):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    monkeypatch.delenv("CC_KEY", raising=False)
    without = project_provider_capabilities()["projection_digest"]
    monkeypatch.setenv("CC_KEY", "super-secret")
    _reset_cache()
    with_key = project_provider_capabilities()["projection_digest"]
    assert without == with_key  # setting the key must not move the digest


def test_04_digest_excludes_mtime(providers_dir):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    d1 = project_provider_capabilities()["projection_digest"]
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))  # rewrite same content
    d2 = project_provider_capabilities()["projection_digest"]
    assert d1 == d2


def test_05_no_secrets_or_absolute_paths(providers_dir, monkeypatch):
    data = _cloud_manifest("cc", ["image_to_video"])
    data["submit"]["url"] = "https://api.x/v?token=leak-me-123"
    _write(providers_dir, "cc", data)
    monkeypatch.setenv("CC_KEY", "super-secret-value")
    blob = json.dumps(project_provider_capabilities(), ensure_ascii=False)
    assert "super-secret-value" not in blob
    assert "leak-me-123" not in blob        # signed-URL param value never surfaces
    assert str(providers_dir) not in blob   # no absolute path
    assert "/tmp" not in blob and "/home" not in blob


def test_06_credential_requirements_names_only(providers_dir, monkeypatch):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    monkeypatch.setenv("CC_KEY", "x")
    entry = _find(project_provider_capabilities(), "cc")
    assert entry["credential_requirements"] == ["CC_KEY"]  # the NAME
    # presence ("set: true") must NOT appear anywhere in the projection entry
    assert "set" not in json.dumps(entry)
    assert "true" not in json.dumps(entry).replace('"enabled": true', "")


def test_07_broken_manifest_surfaced_healthy_retained(providers_dir):
    _write(providers_dir, "good", _cloud_manifest("good", ["image_to_video"]))
    write_yaml(providers_dir / "bad" / "provider.yaml", {"type": "video"})  # no id
    _reset_cache()
    proj = project_provider_capabilities()
    ids = [p["provider_id"] for p in proj["providers"]]
    assert "good" in ids and "bad" not in ids
    assert any("bad" in e for e in proj["errors"])


def test_08_providers_sorted(providers_dir):
    for pid in ("zeta", "alpha", "mid"):
        _write(providers_dir, pid, _cloud_manifest(pid, ["image_to_video"]))
    ids = [p["provider_id"] for p in project_provider_capabilities()["providers"]]
    assert ids == sorted(ids)


def test_09_profile_id_format(providers_dir):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video", "vertical"]))
    entry = _find(project_provider_capabilities(), "cc")
    pids = {c["profile_id"] for c in entry["capabilities"]}
    assert pids == {"provider:cc#image_to_video", "provider:cc#vertical"}


def test_10_projection_is_pure_delete_rebuild_safe(providers_dir):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    once = project_provider_capabilities()
    # a fresh process-equivalent (cache cleared) yields byte-identical output
    _reset_cache()
    twice = project_provider_capabilities()
    assert once == twice


# ============================================ manifest / profile facts (11-18)


def test_11_builtins_present_local_empty_caps(providers_dir):
    proj = project_provider_capabilities()
    cc = _find(proj, "caption_card")
    assert cc["source"]["kind"] == "builtin"
    assert cc["capabilities"] == []
    assert cc["enabled"] is True


def test_12_disabled_reflected(providers_dir):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"], disabled=True))
    assert _find(project_provider_capabilities(), "cc")["enabled"] is False


def test_13_max_resolution_surfaced_verbatim(providers_dir):
    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video"],
                           limits={"max_resolution": "1080x1920"}))
    entry = _find(project_provider_capabilities(), "cc")
    assert entry["limits"]["max_resolution"] == "1080x1920"  # opaque, verbatim
    # each capability echoes the same opaque limit — never a guessed w/h
    cap = entry["capabilities"][0]
    assert cap["limits"]["max_resolution"] == "1080x1920"
    assert "max_width" not in cap["limits"] and "max_height" not in cap["limits"]


def test_14_structured_limits_not_invented(providers_dir):
    """SKIPPED_WITH_EVIDENCE: no manifest field for max_width/max_height/
    allowed_frame_sizes exists — the projection must not invent them."""
    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video"],
                           limits={"max_resolution": "4k"}))
    entry = _find(project_provider_capabilities(), "cc")
    for forbidden in ("max_width", "max_height", "allowed_frame_sizes"):
        assert forbidden not in entry["limits"]


def test_15_capability_profiles_overlay_absent(providers_dir):
    """SKIPPED_WITH_EVIDENCE: no per-capability overlay in the manifest — every
    capability of a provider echoes the SAME provider-level limits."""
    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video", "vertical"],
                           limits={"max_duration_ms": 6000}))
    caps = _find(project_provider_capabilities(), "cc")["capabilities"]
    assert len({json.dumps(c["limits"], sort_keys=True) for c in caps}) == 1


def test_16_profile_digest_stable(providers_dir):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    a = provider_profile_digest("cc", "image_to_video")
    _reset_cache()
    b = provider_profile_digest("cc", "image_to_video")
    assert a == b and a.startswith("sha256:")


def test_17_profile_digest_moves_on_semantic_change(providers_dir):
    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video"], limits={"max_duration_ms": 6000}))
    before = provider_profile_digest("cc", "image_to_video")
    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video"], limits={"max_duration_ms": 9000}))
    after = provider_profile_digest("cc", "image_to_video")
    assert before != after


def test_18_source_digest_and_free_probe(providers_dir):
    _write(providers_dir, "pinged", _cloud_manifest(
        "pinged", ["image_to_video"], ping_url="https://api.x/health"))
    _write(providers_dir, "silent", _cloud_manifest("silent", ["image_to_video"]))
    proj = project_provider_capabilities()
    assert _find(proj, "pinged")["free_probe_available"] is True
    assert _find(proj, "silent")["free_probe_available"] is False
    for pid in ("pinged", "silent"):
        assert _find(proj, pid)["source"]["semantic_digest"].startswith("sha256:")


def test_18b_iter_descriptors_returns_errors(providers_dir):
    _write(providers_dir, "good", _cloud_manifest("good", ["image_to_video"]))
    write_yaml(providers_dir / "bad" / "provider.yaml", {"type": "video"})
    _reset_cache()
    descriptors, errors = iter_provider_descriptors()
    ids = {d.provider_id for d in descriptors}
    assert "good" in ids and "caption_card" in ids
    assert any("bad" in e for e in errors)


# =================================================== CLI surfaces (36-37)


def test_36_providers_catalog_json_and_output(providers_dir, tmp_path, monkeypatch):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    monkeypatch.setenv("CC_KEY", "super-secret")
    res = runner.invoke(app, ["providers", "catalog", "--json"])
    assert res.exit_code == 0, res.output
    proj = json.loads(res.output)
    assert proj["schema"] == SCHEMA
    assert any(p["provider_id"] == "cc" for p in proj["providers"])
    assert "super-secret" not in res.output

    out = tmp_path / "derived" / "catalog.json"
    res2 = runner.invoke(app, ["providers", "catalog", "--json", "--output", str(out)])
    assert res2.exit_code == 0, res2.output
    assert out.exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["projection_digest"] == proj["projection_digest"]


def test_36b_providers_catalog_human_no_secrets(providers_dir, monkeypatch):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    monkeypatch.setenv("CC_KEY", "super-secret-value")
    res = runner.invoke(app, ["providers", "catalog"])
    assert res.exit_code == 0, res.output
    assert "cc" in res.output
    assert "super-secret-value" not in res.output


def test_37_providers_check_structured_sections(providers_dir, monkeypatch):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"],
                                                limits={"max_resolution": "1080x1920"}))
    monkeypatch.setenv("CC_KEY", "x")
    res = runner.invoke(app, ["providers", "check", "cc", "--json"])
    payload = json.loads(res.output)
    for section in ("manifest_errors", "credential_presence",
                    "projection_consistency", "routing_references", "warnings"):
        assert section in payload, section
    # credential presence names the env var; presence is allowed in CHECK output
    assert payload["credential_presence"]["env"] == "CC_KEY"
    assert payload["projection_consistency"]["ok"] is True
    # the LEGACY opaque limit surfaces as a warning
    assert any("LEGACY" in w or "max_resolution" in w for w in payload["warnings"])
