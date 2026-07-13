"""Concurrency quality modes (goal 14) + human-readable cost-tiered routing
(goal 15) + `routing explain`.

Covers, per the round-U contract item D:
- tier resolution (a ``tiers:`` mapping fires; a rule may ``match: {tier: ...}``)
  AND its absence is byte-identical to §8.4;
- build-mode strategy bias biases only the neutral ``else`` — never an explicit
  per-shot provider, a fired rule or an opinionated strategy;
- the retry + concurrency knobs are plumbed (mode → knobs → request fields →
  CloudProvider retry budget);
- the ask_before spend gate fires BEFORE any provider submission under
  concurrency, and a budget trip stops NEW submissions;
- generation results commit in deterministic plan order regardless of who
  finished first;
- the `routing explain` output structure (chosen / why / fallback order / cost).

Hermetic like test_providers_routing: ``MANJU_PROVIDERS_DIR`` isolates the user
routing.yaml under tmp, and the registry manifest cache is reset per test.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
from typer.testing import CliRunner

import manju.providers.registry as registry_mod
from manju.build import graph as graph_mod
from manju.build import modes as modes_mod
from manju.build.graph import _concurrent_generate, run_build
from manju.build.modes import BUILD_MODES, knobs_for, mode_else_bias, resolve_mode
from manju.cli import app
from manju.core.models import BuildConfig, ProjectConfig, ShotSpec, TakeSidecar
from manju.core.yamlio import write_yaml
from manju.gui.plan import routing_explain
from manju.providers import routing

runner = CliRunner()


# --------------------------------------------------------------------- fixtures


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


def _write(providers_dir, pid, data):
    write_yaml(providers_dir / pid / "provider.yaml", data)
    _reset_cache()


def _routing(tmp_project, data):
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", data)
    _reset_cache()


# ============================================================ MODE KNOB MAPPING


def test_resolve_mode_flag_beats_config_beats_none():
    cfg = ProjectConfig(name="x", build=BuildConfig(mode="quality"))
    assert resolve_mode("speed", cfg) == "speed"       # flag wins
    assert resolve_mode(None, cfg) == "quality"        # project.yaml default
    assert resolve_mode(None, ProjectConfig(name="x")) is None  # unspecified
    assert resolve_mode(None, None) is None


def test_unknown_mode_raises_clean():
    with pytest.raises(modes_mod.BuildModeError):
        resolve_mode("turbo", None)


def test_knobs_shape_matches_contract():
    # quality 1..2 workers, balanced moderate, speed higher; retries decreasing.
    q, b, s = BUILD_MODES["quality"], BUILD_MODES["balanced"], BUILD_MODES["speed"]
    assert 1 <= q.max_workers <= 2 < b.max_workers < s.max_workers
    assert q.retries > b.retries > s.retries
    assert q.else_bias == "quality" and b.else_bias is None and s.else_bias == "cheapest"
    assert knobs_for(None) is None                     # no mode = default path
    assert mode_else_bias(None) is None
    assert mode_else_bias("speed") == "cheapest"


# ================================================================ TIERS (goal 15)


def test_tier_section_fires_as_priority(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "p1", _cloud_manifest("p1", ["image_to_video"]))
    _write(providers_dir, "p2", _cloud_manifest("p2", ["image_to_video"]))
    _routing(tmp_project, {"tiers": {
        "key_shot": {"description": "关键镜头:优先质量", "use": ["p2", "p1"]}}})
    shot = add_shot(tmp_project, "S001", tier="key_shot")
    res = routing.resolve(tmp_project, shot)
    assert res.order[0] == "p2" and res.order.index("p2") < res.order.index("p1")
    assert res.order[-1] == "caption_card"        # safety net still terminal
    assert res.fired_tier is not None and res.fired_tier["name"] == "key_shot"
    assert res.fired_tier["description"] == "关键镜头:优先质量"


def test_rule_can_match_tier(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "ruleprov", _cloud_manifest("ruleprov", ["image_to_video"]))
    _routing(tmp_project, {"strategy": "mine", "strategies": {"mine": {
        "rules": [{"match": {"tier": "key_shot"}, "use": "ruleprov"}], "else": "fallback"}}})
    hit = add_shot(tmp_project, "S001", tier="key_shot")
    miss = add_shot(tmp_project, "S002", tier="draft")
    assert routing.resolve(tmp_project, hit).order[0] == "ruleprov"
    assert routing.resolve(tmp_project, hit).fired_rule["match"] == {"tier": "key_shot"}
    # a different tier does not fire the rule
    assert routing.resolve(tmp_project, miss).fired_rule is None


def test_explicit_rule_beats_tier_section(providers_dir, tmp_project, add_shot):
    """A hand-written rule matching the tier is more specific than the tiers-
    section default and fires first (step 2 before the tier step 2t)."""
    _write(providers_dir, "ruleprov", _cloud_manifest("ruleprov", ["image_to_video"]))
    _write(providers_dir, "tierprov", _cloud_manifest("tierprov", ["image_to_video"]))
    _routing(tmp_project, {
        "strategy": "mine",
        "strategies": {"mine": {"rules": [
            {"match": {"tier": "key_shot"}, "use": "ruleprov"}], "else": "fallback"}},
        "tiers": {"key_shot": {"use": ["tierprov"]}}})
    shot = add_shot(tmp_project, "S001", tier="key_shot")
    res = routing.resolve(tmp_project, shot)
    assert res.order[0] == "ruleprov"
    assert res.fired_rule is not None and res.fired_tier is None


def test_malformed_tier_is_clean_error(providers_dir, tmp_project, add_shot):
    _routing(tmp_project, {"tiers": {"bad": {"use": "not-a-list"}}})
    shot = add_shot(tmp_project, "S001", tier="bad")
    with pytest.raises(routing.RoutingError):
        routing.resolve(tmp_project, shot)


def test_tier_absent_is_byte_identical(providers_dir, tmp_project, add_shot):
    """A shot tagged with a tier resolves EXACTLY like §8.4 when no tiers
    section defines it — the tier tag alone is a pure no-op (byte-identity)."""
    _write(providers_dir, "cc", _cloud_manifest("cc", ["image_to_video"]))
    from manju.providers.registry import fallback_chain

    tagged = add_shot(tmp_project, "S001", tier="key_shot")
    plain = add_shot(tmp_project, "S002")
    baseline = fallback_chain(tagged)
    # default config (no routing file, no tiers) → head order == the fallback chain
    res_tagged = routing.resolve(tmp_project, tagged, routing.RoutingConfig())
    res_plain = routing.resolve(tmp_project, plain, routing.RoutingConfig())
    assert res_tagged.order == baseline == res_plain.order
    assert res_tagged.fired_tier is None
    # and the shot's tier never enters the picture hash (never restages)
    from manju.core.spec import spec_payload

    assert "tier" not in spec_payload(tagged)


def test_tier_tagged_shot_no_routing_file_bypasses_module(providers_dir, tmp_project, add_shot):
    add_shot(tmp_project, "S001", tier="key_shot")
    assert routing.load_routing(tmp_project) is None  # registry keeps §8.4 path


# =============================================== MODE STRATEGY BIAS (goal 14)


def test_mode_bias_never_clobbers_explicit_or_rules(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "cheapcloud", _cloud_manifest("cheapcloud", ["image_to_video"], 0.01))
    _write(providers_dir, "dearcloud", _cloud_manifest("dearcloud", ["image_to_video"], 0.09))
    _write(providers_dir, "ruleprov", _cloud_manifest("ruleprov", ["image_to_video"]))
    _write(providers_dir, "explprov", _cloud_manifest("explprov", ["image_to_video"]))
    _routing(tmp_project, {"strategy": "mine", "strategies": {"mine": {
        "rules": [{"match": {"shot_size": "close_up"}, "use": "ruleprov"}],
        "else": "fallback"}}})
    config = routing.load_routing(tmp_project)

    explicit = add_shot(tmp_project, "S001", camera={"shot_size": "close_up"},
                        generation={"provider": "explprov"})
    ruled = add_shot(tmp_project, "S002", camera={"shot_size": "close_up"})
    free = add_shot(tmp_project, "S003", camera={"shot_size": "medium"})

    # speed bias = cheapest else; explicit + rule heads are untouched
    assert routing.resolve(tmp_project, explicit, config, else_bias="cheapest").order[0] == "explprov"
    assert routing.resolve(tmp_project, ruled, config, else_bias="cheapest").order[0] == "ruleprov"
    # the unclaimed shot's else IS biased: cheapest expands to ALL capable
    # providers, cost-ordered (cheapcloud before dearcloud)
    free_order = routing.resolve(tmp_project, free, config, else_bias="cheapest").order
    assert free_order.index("cheapcloud") < free_order.index("dearcloud")
    # without the bias the plain fallback chain enumerates only ONE capable
    # cloud (the §8.4 single pick) — dearcloud never appears
    plain = routing.resolve(tmp_project, free, config).order
    assert "dearcloud" not in plain


def test_else_bias_only_overrides_neutral_fallback(providers_dir, tmp_project, add_shot):
    """An opinionated strategy (local_only, else=local) is NOT overridden by a
    mode's else_bias — the user's explicit choice wins over the mode default."""
    _write(providers_dir, "cloud", _cloud_manifest("cloud", ["image_to_video"], 0.01))
    _write(providers_dir, "comfyui",
           {"id": "comfyui", "type": "video",
            "adapter": "manju.providers.comfyui:ComfyUIProvider",
            "capabilities": ["image_to_video"],
            "comfyui": {"workflow_file": "w.json", "base_url": "http://127.0.0.1:8188"},
            "cost": {"per_call": 0.0, "currency": "CNY"}})
    _routing(tmp_project, {"strategy": "local_only"})
    config = routing.load_routing(tmp_project)
    shot = add_shot(tmp_project, "S001")
    order = routing.resolve(tmp_project, shot, config, else_bias="cheapest").order
    assert "cloud" not in order            # local_only still strips cloud
    assert "comfyui" in order


# =============================================== RETRY KNOB PLUMBED (goal 14)


class _FlakyCloud:
    """Minimal CloudProvider that always fails retryable — for counting the
    retry budget honored per request."""

    def __init__(self):
        from manju.providers.base import CloudProvider

        calls = {"poll": 0}
        self.calls = calls

        class _P(CloudProvider):
            id = "flaky"

            def submit(self, req):
                return "job-1"

            def poll(self, job_id):
                calls["poll"] += 1
                from manju.providers.base import FailureKind
                return "failed", {"failure_kind": FailureKind.rate_limited.value,
                                  "reason": "限流"}

            def download(self, job_id, dest):
                return []

        self.provider = _P(sleep_fn=lambda _s: None, max_retries=3)


def _fake_request(tmp_project, add_shot, **kw):
    from manju.providers.base import GenerationRequest

    shot = add_shot(tmp_project, "S001")
    return GenerationRequest(project=tmp_project, shot=shot, bible={},
                             spec_hash="x", duration_ms=1000, **kw)


def test_request_max_retries_overrides_provider_default(tmp_project, add_shot):
    from manju.providers.base import ProviderFailure

    flaky = _FlakyCloud()
    req = _fake_request(tmp_project, add_shot, max_retries=1)
    with pytest.raises(ProviderFailure):
        flaky.provider.generate(req)
    assert flaky.calls["poll"] == 2  # initial + exactly 1 retry


def test_request_none_retries_keeps_provider_default(tmp_project, add_shot):
    from manju.providers.base import ProviderFailure

    flaky = _FlakyCloud()
    req = _fake_request(tmp_project, add_shot, max_retries=None)
    with pytest.raises(ProviderFailure):
        flaky.provider.generate(req)
    assert flaky.calls["poll"] == 4  # initial + the provider's own 3 retries (byte-identical)


# =============================================== CONCURRENCY DRIVER (goal 14)


def test_concurrent_generate_deterministic_and_complete():
    items = [{"shot": f"S{i:03d}"} for i in range(1, 8)]

    def gen_one(item):
        # earlier shots sleep longer → finish LAST, proving order independence
        idx = int(item["shot"][1:])
        time.sleep((10 - idx) * 0.005)
        return {"shot": item["shot"], "actual_cost": 0.0}

    results, tripped, canceled, running, in_flight_at_trip = _concurrent_generate(
        items, gen_one, max_workers=4, budget_limit=None)
    assert not tripped
    assert not canceled  # goal: honest job cancellation — no should_cancel passed
    assert in_flight_at_trip == []  # trip never fired -> nothing to snapshot
    assert set(results) == {it["shot"] for it in items}  # all ran


def test_concurrent_generate_budget_trip_stops_new_submissions():
    items = [{"shot": f"S{i:03d}"} for i in range(1, 9)]
    submitted: list[str] = []
    lock = threading.Lock()

    def gen_one(item):
        with lock:
            submitted.append(item["shot"])
        time.sleep(0.01)
        return {"shot": item["shot"], "actual_cost": 10.0}

    # workers=2 so submission is staged; budget 25 trips after ~3 completions
    results, tripped, canceled, running, in_flight_at_trip = _concurrent_generate(
        items, gen_one, max_workers=2, budget_limit=25.0)
    assert tripped
    assert not canceled  # a budget trip is not a cancellation
    assert running > 25.0
    assert len(submitted) < len(items)          # NEW submissions were stopped
    assert set(results) == set(submitted)       # only submitted shots have results
    # goal 80: the shot(s) still running the instant the trip fired are named —
    # they finish and bill regardless of the trip, and did land in `results`.
    assert set(in_flight_at_trip) <= set(results)


def test_concurrent_generate_honors_provider_max_concurrent(monkeypatch):
    """Goal 8: a manifest declaring max_concurrent=1 never sees more than one
    IN-FLIGHT call at a time, even though the global pool runs several workers —
    the per-provider semaphore sits UNDER the global pool."""
    from manju.providers.manifest import ProviderManifest

    manifest = ProviderManifest.model_validate({
        "id": "capped", "type": "video", "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "submit": {"url": "https://x/v", "body_template": {}, "job_id_path": "$.id"},
        "poll": {"url": "https://x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "limits": {"max_concurrent": 1},
    })
    import manju.providers.registry as registry_mod
    monkeypatch.setattr(registry_mod, "get_manifest",
                        lambda name: manifest if name == "capped" else None)

    items = [{"shot": f"S{i:03d}"} for i in range(1, 6)]
    concurrent_now = 0
    max_concurrent_seen = 0
    lock = threading.Lock()

    def gen_one(item):
        nonlocal concurrent_now, max_concurrent_seen
        with lock:
            concurrent_now += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_now)
        time.sleep(0.02)
        with lock:
            concurrent_now -= 1
        return {"shot": item["shot"], "actual_cost": 0.0}

    results, tripped, canceled, running, _ = _concurrent_generate(
        items, gen_one, max_workers=4, budget_limit=None,
        head_provider=lambda it: "capped")
    assert not canceled
    assert not tripped
    assert set(results) == {it["shot"] for it in items}
    assert max_concurrent_seen == 1  # never more than the manifest's cap


# =========================================== SPEND-GATE ORDER UNDER CONCURRENCY


def _install_fake_gwf(monkeypatch, log):
    """Replace registry.generate_with_fallback with a sleeping fake that records
    the submission order + the request knobs, and returns one real take."""
    import manju.providers.registry as reg

    lock = threading.Lock()

    def fake_gwf(req, chain=None, **kw):
        with lock:
            log.append({"shot": req.shot.id, "bias": req.routing_bias,
                        "retries": req.max_retries, "t": time.time()})
        # earlier shots sleep longer so completion order != plan order
        idx = int("".join(c for c in req.shot.id if c.isdigit()) or "0")
        time.sleep(max(0, (9 - idx)) * 0.01)
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkstemp(suffix=".mp4")[1])
        tmp.write_bytes(b"fake-" + req.shot.id.encode())
        take = req.project.register_take(
            req.shot.id, tmp, TakeSidecar(provider="fakegen", spec_hash=req.spec_hash))
        return [take]

    monkeypatch.setattr(reg, "generate_with_fallback", fake_gwf)


def _paid_project(providers_dir, tmp_project, add_shot, n=4):
    # a paid manifest makes the plan cost > 0 so the ask_before gate engages
    _write(providers_dir, "paidcloud", _cloud_manifest("paidcloud", ["image_to_video"], 0.05))
    for i in range(1, n + 1):
        add_shot(tmp_project, f"S{i:03d}")


def test_spend_gate_fires_before_any_submission(providers_dir, tmp_project, add_shot, monkeypatch):
    _paid_project(providers_dir, tmp_project, add_shot)
    log: list = []
    _install_fake_gwf(monkeypatch, log)
    # no --yes: the priced plan must stop as waiting_user BEFORE any submission
    res = run_build(tmp_project, target="qc", mode="speed", assume_yes=False)
    assert res.ok is False and res.waiting_user is True
    assert log == []                            # nothing was ever submitted


def test_spend_gate_yes_lets_concurrent_submissions_run(providers_dir, tmp_project, add_shot, monkeypatch):
    _paid_project(providers_dir, tmp_project, add_shot)
    log: list = []
    _install_fake_gwf(monkeypatch, log)
    res = run_build(tmp_project, target="qc", mode="speed", assume_yes=True)
    assert {e["shot"] for e in log} == {"S001", "S002", "S003", "S004"}
    # the mode's knobs were plumbed onto every request (speed → cheapest, 1 retry)
    assert all(e["bias"] == "cheapest" and e["retries"] == 1 for e in log)


def test_concurrent_results_commit_in_plan_order(providers_dir, tmp_project, add_shot, monkeypatch):
    # zero-cost plan (no paid manifest) → no gate; mode=speed → parallel branch
    for i in range(1, 6):
        add_shot(tmp_project, f"S{i:03d}")
    log: list = []
    _install_fake_gwf(monkeypatch, log)
    res = run_build(tmp_project, target="qc", mode="speed", assume_yes=True)
    committed = [g.split("/")[0] for g in res.generated]
    assert committed == ["S001", "S002", "S003", "S004", "S005"]  # deterministic


def test_no_mode_uses_serial_path(providers_dir, tmp_project, add_shot, monkeypatch):
    """With no mode the generate path is serial and byte-identical: requests
    carry no routing bias and no retry override, and everything still builds."""
    for i in range(1, 4):
        add_shot(tmp_project, f"S{i:03d}")
    log: list = []
    _install_fake_gwf(monkeypatch, log)
    res = run_build(tmp_project, target="qc", assume_yes=True)  # mode=None
    assert [g.split("/")[0] for g in res.generated] == ["S001", "S002", "S003"]
    assert all(e["bias"] is None and e["retries"] is None for e in log)


# ================================================= ROUTING EXPLAIN (goal 15/C)


def test_routing_explain_structure_and_why(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "explprov", _cloud_manifest("explprov", ["image_to_video"]))
    add_shot(tmp_project, "S001", generation={"provider": "explprov"})
    add_shot(tmp_project, "S002")  # no explicit, no routing file → fallback
    info = routing_explain(tmp_project)
    assert [r["shot"] for r in info["rows"]] == ["S001", "S002"]
    for r in info["rows"]:
        assert set(r) >= {"shot", "chosen", "why", "why_detail",
                          "fallback_order", "estimated_cost", "currency", "tier"}
        assert r["why"] in ("explicit", "rule", "tier", "fallback")
    assert info["rows"][0]["why"] == "explicit" and info["rows"][0]["chosen"] == "explprov"
    assert info["rows"][1]["why"] == "fallback"
    # the fallback order after the head is the tail (never includes the head)
    assert info["rows"][1]["chosen"] not in info["rows"][1]["fallback_order"]


def test_routing_explain_tier_why(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "p1", _cloud_manifest("p1", ["image_to_video"]))
    _routing(tmp_project, {"tiers": {"key_shot": {"description": "关键", "use": ["p1"]}}})
    add_shot(tmp_project, "S001", tier="key_shot")
    row = routing_explain(tmp_project)["rows"][0]
    assert row["why"] == "tier" and row["chosen"] == "p1"
    assert "key_shot" in row["why_detail"]


def test_routing_explain_mode_preview_biases_head(providers_dir, tmp_project, add_shot):
    # names chosen so alphabetical (fallback pick) != cheapest: acloud is dear,
    # zcloud is cheap. Plain resolution takes the alphabetically-first capable
    # provider (acloud); the speed bias takes the cheapest (zcloud).
    _write(providers_dir, "acloud", _cloud_manifest("acloud", ["image_to_video"], 0.09))
    _write(providers_dir, "zcloud", _cloud_manifest("zcloud", ["image_to_video"], 0.01))
    _routing(tmp_project, {"strategy": "default"})  # neutral else=fallback
    add_shot(tmp_project, "S001", duration=4.0)
    base_row = routing_explain(tmp_project)["rows"][0]
    speed_row = routing_explain(tmp_project, mode="speed")["rows"][0]
    assert base_row["chosen"] == "acloud"   # plain §8.4 single-capability pick
    assert speed_row["chosen"] == "zcloud"  # cheapest bias reorders the head
    # goal 7: the COST follows the chosen head too — acloud's estimate must not
    # leak into the speed-biased row (and vice versa).
    assert base_row["estimated_cost"] == pytest.approx(4.0 * 0.09)
    assert speed_row["estimated_cost"] == pytest.approx(4.0 * 0.01)
    assert base_row["estimated_cost"] != speed_row["estimated_cost"]


def test_routing_explain_cli_json(providers_dir, tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["routing", "explain", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert len(payload["rows"]) == 2
    # single-shot form + human table
    res2 = runner.invoke(app, ["routing", "explain", "S001"])
    assert res2.exit_code == 0, res2.output
    assert "S001" in res2.output


def test_routing_explain_cli_bad_mode(providers_dir, tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["routing", "explain", "--mode", "turbo"])
    assert res.exit_code == 1
    assert "--mode" in res.output


# ============================================================ BUILD --mode CLI


def test_build_mode_flag_validated(providers_dir, tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["build", "--mode", "hyper", "--dry-run"])
    assert res.exit_code == 1
    assert "--mode" in res.output


def test_project_yaml_build_mode_roundtrips(tmp_project):
    cfg = tmp_project.load_config()
    cfg.build = BuildConfig(mode="balanced")
    tmp_project.save_config(cfg)
    assert tmp_project.load_config().build.mode == "balanced"


def test_build_mode_none_absent_from_saved_config(tmp_project):
    # additive + default-absent: a project without a build block re-saves without
    # one (no `build:` key leaks in → existing project.yaml stays byte-stable).
    cfg = tmp_project.load_config()
    assert cfg.build is None
    dumped = cfg.model_dump(exclude_none=True)
    assert "build" not in dumped
