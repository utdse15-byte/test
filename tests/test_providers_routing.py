"""Provider-management commands (goal 1) + routing strategies (goal 9).

Manifest scaffolding round-trips, the ``disabled`` flag honoured everywhere
(list / fallback chain / explicit-provider build error), secret masking, every
built-in strategy resolving on a fixture shot-set, the resolution-order pins
(explicit > rule > fallback; absent routing.yaml ⇒ byte-identical §8.4),
unknown-match-key errors, live probes, and user-vs-project file precedence.

Hermetic: ``MANJU_PROVIDERS_DIR`` points at a temp dir (so the user routing
file resolves to ``<tmp>/routing.yaml``, never a developer's real ~/.manju),
and the registry manifest cache is reset per test.
"""

from __future__ import annotations

import urllib.error

import pytest
import yaml
from typer.testing import CliRunner

import manju.providers.registry as registry_mod
from manju.cli import app
from manju.core.yamlio import write_yaml
from manju.providers import routing
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.manifest import (
    ADAPTER_ALIASES,
    ProviderManifest,
    load_manifests,
    reachability_probe,
    scaffold_template,
)
from manju.providers.registry import fallback_chain, generate_with_fallback

runner = CliRunner()


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    """A hermetic ~/.manju/providers replacement; the user routing.yaml then
    lives at ``<tmp_path>/routing.yaml``. Resets the registry manifest cache."""
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    monkeypatch.delenv("MANJU_ROUTING", raising=False)
    registry_mod._manifest_cache = None
    yield root
    registry_mod._manifest_cache = None


def _reset_cache():
    registry_mod._manifest_cache = None


def _cloud_manifest(pid, caps, per_second=0.05, disabled=False):
    return {
        "id": pid, "type": "video", "adapter": "generic_cloud",
        "capabilities": caps, "disabled": disabled,
        "auth": {"key_env": f"{pid.upper()}_KEY"},
        "submit": {"url": "https://api.x/v", "body_template": {}, "job_id_path": "$.id"},
        "poll": {"url": "https://api.x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "cost": {"per_second": per_second, "currency": "CNY"},
    }


def _comfy_manifest(pid, caps, disabled=False):
    return {
        "id": pid, "type": "video",
        "adapter": "manju.providers.comfyui:ComfyUIProvider",
        "capabilities": caps, "disabled": disabled,
        "comfyui": {"workflow_file": "w.json", "base_url": "http://127.0.0.1:8188"},
        "cost": {"per_call": 0.0, "currency": "CNY"},
    }


def _write(providers_dir, pid, data):
    write_yaml(providers_dir / pid / "provider.yaml", data)
    _reset_cache()


# ======================================================= manifest scaffolding


@pytest.mark.parametrize("adapter", sorted(ADAPTER_ALIASES))
def test_scaffold_round_trips_and_marks_fields(adapter):
    text = scaffold_template("newprov", "video", adapter)
    assert "★" in text  # fields to fill are marked
    data = yaml.safe_load(text)  # valid YAML
    m = ProviderManifest.model_validate(data)  # valid manifest
    assert m.id == "newprov"
    assert m.adapter == ADAPTER_ALIASES[adapter]
    assert m.disabled is False
    # keys are NEVER inlined: the template names an env var, not a value
    assert "API_KEY" not in text or "key_env" in text


def test_scaffold_unknown_adapter_raises():
    with pytest.raises(ValueError):
        scaffold_template("x", "video", "not_an_adapter")


def test_add_scaffolds_and_refuses_overwrite(providers_dir):
    res = runner.invoke(app, ["providers", "add", "wan", "--type", "video", "--json"])
    assert res.exit_code == 0, res.output
    import json
    payload = json.loads(res.output)
    assert payload["key_env"] == "WAN_API_KEY"
    assert (providers_dir / "wan" / "provider.yaml").exists()
    manifests, _ = load_manifests()
    assert "wan" in manifests  # round-trips straight into the registry

    again = runner.invoke(app, ["providers", "add", "wan", "--type", "video"])
    assert again.exit_code == 1
    assert "refusing to overwrite" in again.output


def test_add_rejects_bad_type(providers_dir):
    res = runner.invoke(app, ["providers", "add", "x", "--type", "hologram"])
    assert res.exit_code == 1
    assert "--type must be one of" in res.output


# --------------------------------------------------- goal item 72: provider_id
# path-segment hardening — add/enable/disable/show all reject an id shaped
# like a traversal/absolute path instead of building a path from it.


@pytest.mark.parametrize("cmd", [
    ["providers", "add", "../../evil", "--type", "video"],
    ["providers", "enable", "../../evil"],
    ["providers", "disable", "../../evil"],
    ["providers", "show", "../../evil"],
])
def test_provider_commands_refuse_traversal_id(providers_dir, tmp_path, cmd):
    res = runner.invoke(app, cmd)
    assert res.exit_code != 0
    # nothing named "evil" was ever created anywhere near the hermetic tmp
    # providers dir — the validator refuses BEFORE any path is even built
    assert not any(p.name == "evil" for p in tmp_path.parent.rglob("evil"))


# ================================================================ providers list


def test_list_reports_key_state_and_enabled_never_value(providers_dir, monkeypatch):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    monkeypatch.setenv("CC_KEY", "super-secret-value")
    import json
    res = runner.invoke(app, ["providers", "list", "--json"])
    assert res.exit_code == 0, res.output
    row = json.loads(res.output)["providers"][0]
    assert row["id"] == "cc"
    assert row["key_env"] == "CC_KEY"
    assert row["key_set"] is True
    assert row["enabled"] is True
    # the secret value is NEVER anywhere in the output
    assert "super-secret-value" not in res.output


def test_list_shows_disabled(providers_dir):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"], disabled=True))
    import json
    res = runner.invoke(app, ["providers", "list", "--json"])
    assert json.loads(res.output)["providers"][0]["enabled"] is False


# ==================================================================== masking


def test_show_masks_secret_values_keeps_env_name(providers_dir):
    data = _cloud_manifest("cc", ["image_to_video"])
    data["auth"]["secret"] = "topsecret-abc"      # secret-ish key
    data["api_key"] = "inline-key-should-hide"    # secret-ish key
    _write(providers_dir, "cc", data)
    res = runner.invoke(app, ["providers", "show", "cc"])
    assert res.exit_code == 0, res.output
    assert "topsecret-abc" not in res.output
    assert "inline-key-should-hide" not in res.output
    assert "***" in res.output
    assert "CC_KEY" in res.output  # key_env is a NAME, not a secret — stays visible


def test_show_masks_common_header_names_case_insensitively(providers_dir):
    """round-W #73: Authorization/Cookie/Signature/Bearer key names must mask
    their value too, not just secret/token/password/api_key — and the match
    is case-insensitive (a manifest may spell a header any which way)."""
    data = _cloud_manifest("cc", ["image_to_video"])
    data["headers"] = {
        "Authorization": "Bearer abc.def.ghi",
        "Cookie": "session=super-secret-session-value",
        "X-Signature": "deadbeefcafef00d",
        "bearer_token": "another-real-secret",
    }
    _write(providers_dir, "cc", data)
    res = runner.invoke(app, ["providers", "show", "cc"])
    assert res.exit_code == 0, res.output
    assert "Bearer abc.def.ghi" not in res.output
    assert "super-secret-session-value" not in res.output
    assert "deadbeefcafef00d" not in res.output
    assert "another-real-secret" not in res.output
    assert res.output.count("***") >= 4


def test_show_scrubs_secret_query_params_inside_url_values(providers_dir):
    """round-W #73: a URL VALUE under an innocuous-looking key (submit.url)
    that embeds a signed URL's token/signature query param must have that
    param's VALUE masked too — key-name masking alone misses this."""
    data = _cloud_manifest("cc", ["image_to_video"])
    data["submit"]["url"] = (
        "https://api.example.com/v1/generate?token=leak-me-12345&region=cn"
    )
    _write(providers_dir, "cc", data)
    res = runner.invoke(app, ["providers", "show", "cc"])
    assert res.exit_code == 0, res.output
    assert "leak-me-12345" not in res.output
    assert "token=***" in res.output
    # the rest of the URL (host, path, other params) stays legible
    assert "api.example.com" in res.output
    assert "region=cn" in res.output


# ============================================================= enable / disable


def test_enable_disable_preserves_comments(providers_dir):
    runner.invoke(app, ["providers", "add", "wan", "--type", "video"])
    path = providers_dir / "wan" / "provider.yaml"
    assert "★" in path.read_text(encoding="utf-8")

    assert runner.invoke(app, ["providers", "disable", "wan"]).exit_code == 0
    text = path.read_text(encoding="utf-8")
    assert "★" in text  # comments survive the toggle
    assert "disabled: true" in text
    _reset_cache()
    assert load_manifests()[0]["wan"].disabled is True

    assert runner.invoke(app, ["providers", "enable", "wan"]).exit_code == 0
    text = path.read_text(encoding="utf-8")
    assert "★" in text and "disabled: false" in text
    _reset_cache()
    assert load_manifests()[0]["wan"].disabled is False


def test_toggle_unknown_provider_errors(providers_dir):
    res = runner.invoke(app, ["providers", "disable", "ghost"])
    assert res.exit_code == 1
    assert "no such provider" in res.output


# ============================================== disabled honored in fallback chain


def test_disabled_excluded_from_fallback_chain(providers_dir, tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")  # default fallback includes image_to_video
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    assert "cc" in fallback_chain(shot)  # capability match slots it in

    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video"], disabled=True))
    chain = fallback_chain(shot)
    assert "cc" not in chain
    assert chain[-1] == "caption_card"  # still ends network-independent (§8.4)


def test_disabled_explicit_provider_is_build_error(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video"], disabled=True))
    shot = add_shot(tmp_project, "S001", generation={"provider": "cc"})
    req = GenerationRequest(project=tmp_project, shot=shot, bible={},
                            spec_hash="x", duration_ms=1000)
    with pytest.raises(ProviderFailure) as exc:
        generate_with_fallback(req)
    assert exc.value.kind is FailureKind.invalid
    assert "disabled" in exc.value.message
    assert "cc" in exc.value.message


# =================================================== built-in strategies resolve


@pytest.mark.parametrize("strategy", ["default", "local_only", "cheapest", "quality_first"])
def test_every_builtin_strategy_resolves(providers_dir, tmp_project, add_shot, strategy):
    _write(providers_dir, "cheapcloud", _cloud_manifest("cheapcloud", ["image_to_video"], 0.01))
    _write(providers_dir, "dearcloud", _cloud_manifest("dearcloud", ["image_to_video"], 0.09))
    _write(providers_dir, "comfyui", _comfy_manifest("comfyui", ["image_to_video"]))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": strategy})
    _reset_cache()

    for sid in ("A", "B"):
        shot = add_shot(tmp_project, sid)
        res = routing.resolve(tmp_project, shot)
        assert res.order, f"{strategy} produced no order"
        assert res.order[-1] == "caption_card"  # safety net always terminal
        if strategy == "local_only":
            # NEVER cloud: neither cloud manifest may appear
            assert "cheapcloud" not in res.order and "dearcloud" not in res.order
            assert "comfyui" in res.order  # the capable local one is preferred


def test_cheapest_orders_by_per_second(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "dearcloud", _cloud_manifest("dearcloud", ["image_to_video"], 0.09))
    _write(providers_dir, "cheapcloud", _cloud_manifest("cheapcloud", ["image_to_video"], 0.01))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "cheapest"})
    _reset_cache()
    shot = add_shot(tmp_project, "S001")
    order = routing.resolve(tmp_project, shot).order
    assert order.index("cheapcloud") < order.index("dearcloud")


def test_quality_first_priority_list(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "a", _cloud_manifest("a", ["image_to_video"]))
    _write(providers_dir, "b", _cloud_manifest("b", ["image_to_video"]))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "quality_first",
        "strategies": {"quality_first": {"priority": ["b", "a"]}}})
    _reset_cache()
    shot = add_shot(tmp_project, "S001")
    order = routing.resolve(tmp_project, shot).order
    assert order[0] == "b" and order.index("b") < order.index("a")


# ================================================= resolution order + precedence


def test_resolution_explicit_wins_over_rule(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "ruleprov", _cloud_manifest("ruleprov", ["image_to_video"]))
    _write(providers_dir, "explprov", _cloud_manifest("explprov", ["image_to_video"]))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "mine",
        "strategies": {"mine": {"rules": [
            {"match": {"shot_size": "close_up"}, "use": "ruleprov"}], "else": "fallback"}}})
    _reset_cache()
    shot = add_shot(tmp_project, "S001",
                    camera={"shot_size": "close_up"},
                    generation={"provider": "explprov"})
    res = routing.resolve(tmp_project, shot)
    assert res.order[0] == "explprov"  # explicit always wins
    assert "ruleprov" in res.order      # rule still contributes lower


def test_rule_fires_over_else_and_names_fired_rule(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "ruleprov", _cloud_manifest("ruleprov", ["image_to_video"]))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "mine",
        "strategies": {"mine": {"rules": [
            {"match": {"shot_size": "wide"}, "use": "SKIP"},
            {"match": {"has_dialogue": True}, "use": "ruleprov"}], "else": "fallback"}}})
    _reset_cache()
    shot = add_shot(tmp_project, "S001", camera={"shot_size": "close_up"})
    info = routing.explain(tmp_project, shot)
    assert info["fired_rule"]["index"] == 1
    assert info["fired_rule"]["use"] == "ruleprov"
    assert info["order"][0] == "ruleprov"
    # the non-matching rule #0 is recorded as skipped with a reason
    assert any(s.get("rule") == 0 for s in info["skipped"])
    # fallback chain remains the safety net at the tail
    assert info["order"][-1] == "caption_card"


def test_fallback_is_always_the_safety_net(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "ruleprov", _cloud_manifest("ruleprov", ["image_to_video"]))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "mine",
        "strategies": {"mine": {"rules": [
            {"match": {}, "use": "ruleprov"}], "else": "fallback"}}})
    _reset_cache()
    shot = add_shot(tmp_project, "S001")
    order = routing.resolve(tmp_project, shot).order
    assert order[0] == "ruleprov"
    assert order[-1] == "caption_card"


# ------------------------------------------- the byte-identical §8.4 pin


def test_pin_absent_routing_file_returns_none(providers_dir, tmp_project):
    assert routing.load_routing(tmp_project) is None


def test_pin_default_strategy_matches_fallback_chain_order(
        providers_dir, tmp_project, add_shot):
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    for sid, gen in (("A", {}), ("B", {"provider": "cc"})):
        shot = add_shot(tmp_project, sid, generation=gen)
        # the §8.4 baseline: explicit-first then fallback chain, deduped
        preferred = shot.generation.provider
        baseline = ([preferred] if preferred else []) + [
            n for n in fallback_chain(shot)
            if n != preferred
        ]
        res = routing.resolve(tmp_project, shot, routing.RoutingConfig())  # default
        assert res.order == baseline


# ==================================================== unknown match key errors


def test_unknown_match_key_is_clean_error_not_silent_false(
        providers_dir, tmp_project, add_shot):
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "bad",
        "strategies": {"bad": {"rules": [{"match": {"mood": "tense"}, "use": "x"}]}}})
    _reset_cache()
    shot = add_shot(tmp_project, "S001")
    with pytest.raises(routing.RoutingError) as exc:
        routing.resolve(tmp_project, shot)
    assert "unknown match key 'mood'" in str(exc.value)
    # and the CLI surfaces it as a clean failure, not a traceback
    import os
    os.chdir(tmp_project.root)
    res = runner.invoke(app, ["route", "explain", "S001"])
    assert res.exit_code == 1
    assert "unknown match key" in res.output


def test_rule_without_use_errors(providers_dir, tmp_project, add_shot):
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "bad", "strategies": {"bad": {"rules": [{"match": {}}]}}})
    _reset_cache()
    shot = add_shot(tmp_project, "S001")
    with pytest.raises(routing.RoutingError):
        routing.resolve(tmp_project, shot)


def test_unknown_strategy_errors(providers_dir, tmp_project, add_shot):
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "nope"})
    _reset_cache()
    shot = add_shot(tmp_project, "S001")
    with pytest.raises(routing.RoutingError):
        routing.resolve(tmp_project, shot)


# ============================================= match field coverage (SHOT-based)


def test_match_fields_evaluate_against_shot(providers_dir, tmp_project, add_shot):
    shot = add_shot(
        tmp_project, "S001",
        scene="convenience_store", characters=["linxia"], duration=4.0,
        camera={"shot_size": "close_up", "movement": "pan"},
        dialogue={"speaker": "linxia", "text": "hi"},
        generation={"params": {"style": "anime"},
                    "fallback": ["image_to_video", "caption_card"]},
    )
    m = routing._match
    assert m({"shot_size": "close_up"}, shot)
    assert m({"camera_motion": "pan"}, shot)
    assert m({"has_dialogue": True}, shot)
    assert not m({"has_dialogue": False}, shot)
    assert m({"scene": "convenience_store"}, shot)
    assert m({"duration_gt": 3}, shot) and m({"duration_lt": 5}, shot)
    assert not m({"duration_gt": 10}, shot)
    assert m({"characters_include": "linxia"}, shot)
    assert m({"characters_include": ["nobody", "linxia"]}, shot)
    assert m({"provider_capability": "image_to_video"}, shot)
    assert not m({"provider_capability": "first_last_frame"}, shot)
    assert m({"params.style": "anime"}, shot)
    assert not m({"params.style": "noir"}, shot)


def test_auto_duration_never_matches_numeric_bounds(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001", duration="auto")
    assert not routing._match({"duration_gt": 1}, shot)
    assert not routing._match({"duration_lt": 100}, shot)


# ============================================== user-vs-project file precedence


def test_project_routing_wins_over_user(providers_dir, tmp_project):
    # user file (derived from MANJU_PROVIDERS_DIR parent) says local_only …
    write_yaml(providers_dir.parent / "routing.yaml", {"strategy": "local_only"})
    # … project file says cheapest — project wins
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "cheapest"})
    cfg = routing.load_routing(tmp_project)
    assert cfg.strategy == "cheapest"
    assert cfg.sources == ["user", "project"]


def test_user_file_used_when_no_project_file(providers_dir, tmp_project):
    write_yaml(providers_dir.parent / "routing.yaml", {"strategy": "local_only"})
    cfg = routing.load_routing(tmp_project)
    assert cfg.strategy == "local_only"
    assert cfg.sources == ["user"]


def test_user_defined_strategy_visible_and_overridable(providers_dir, tmp_project):
    write_yaml(providers_dir.parent / "routing.yaml",
               {"strategies": {"mine": {"else": "fallback"}}})
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "mine"})
    info = routing.list_strategies(tmp_project)
    names = [s["name"] for s in info["strategies"]]
    assert "mine" in names
    assert info["active"] == "mine"


# ==================================================================== live probe


class _FakeResp:
    def __init__(self, status=200):
        self.status = status

    def close(self):
        pass


def test_live_probe_comfyui_uses_system_stats():
    m = ProviderManifest.model_validate(_comfy_manifest("comfyui", ["image_to_video"]))
    seen = {}

    def opener(url):
        seen["url"] = url
        return _FakeResp(200)

    ok, detail = reachability_probe(m, opener=opener)
    assert ok is True
    assert seen["url"].endswith("/system_stats")


def test_live_probe_unreachable_is_false():
    m = ProviderManifest.model_validate(_comfy_manifest("comfyui", ["image_to_video"]))

    def opener(url):
        raise urllib.error.URLError("connection refused")

    ok, detail = reachability_probe(m, opener=opener)
    assert ok is False
    assert "unreachable" in detail


def test_live_probe_generic_skips_without_ping_url():
    m = ProviderManifest.model_validate(_cloud_manifest("cc", ["image_to_video"]))
    ok, detail = reachability_probe(m)  # never a paid call
    assert ok is None
    assert "skipped" in detail


def test_live_probe_generic_uses_ping_url_when_set():
    data = _cloud_manifest("cc", ["image_to_video"])
    data["ping_url"] = "https://api.x/health"
    m = ProviderManifest.model_validate(data)
    seen = {}
    ok, _ = reachability_probe(m, opener=lambda u: seen.setdefault("u", u) or _FakeResp(200))
    assert ok is True and seen["u"] == "https://api.x/health"


# =========================================================== check command hints


def test_check_reports_problem_and_fix_hint(providers_dir):
    _write(providers_dir, "wan", _cloud_manifest("wan", ["image_to_video"]))
    import json
    res = runner.invoke(app, ["providers", "check", "wan", "--json"])
    assert res.exit_code == 1  # key env not set
    payload = json.loads(res.output)
    assert payload["ok"] is False
    assert payload["problems"]
    assert "export" in payload["problems"][0]["fix"]


def test_check_unknown_provider_errors(providers_dir):
    res = runner.invoke(app, ["providers", "check", "ghost"])
    assert res.exit_code == 1
    assert "no such provider" in res.output


# ==================================================================== route list


def test_route_list_marks_active_and_lists_builtins(providers_dir, tmp_project):
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "cheapest"})
    import json
    import os
    os.chdir(tmp_project.root)
    res = runner.invoke(app, ["route", "list", "--json"])
    assert res.exit_code == 0, res.output
    info = json.loads(res.output)
    assert info["active"] == "cheapest"
    builtins = {s["name"] for s in info["strategies"] if s["builtin"]}
    assert builtins == {"default", "local_only", "cheapest", "quality_first"}
    assert next(s for s in info["strategies"] if s["name"] == "cheapest")["active"]
