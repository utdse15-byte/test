"""P0 ledger-audit fixes for the paid_safety group.

Covers: SPEND-P0-001 (non-finite/negative cost bypassing the budget breaker),
DIRECTOR-P0-001 (internal proposal id path traversal), DIRECTOR-P0-002
(forged/unbound human confirmation), DIRECTOR-P0-003 (concurrent double
execution of one confirmed proposal), and WINCLI-P0-003 (zero-cost cloud TTS
network egress with no privacy gate).

Every action exercised is a text/local op or a monkeypatched engine call — no
ffmpeg, no network.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from manju.build import director as d
from manju.build import spend
from manju.build import voice
from manju.core.container import Project
from manju.core.models import ShotSpec
from manju.core.yamlio import write_yaml

PROJECT_NAME = "雨夜便利店"


# --------------------------------------------------------------- fixtures


def _bible(project: Project) -> None:
    write_yaml(project.root / "bible" / "scenes.yaml",
               {"store": {"name": "便利店", "description": "雨夜街角的便利店。"}})
    write_yaml(project.root / "bible" / "characters.yaml",
               {"lin": {"name": "林夏", "voice": "冷静、克制", "appearance": "短发"}})


def _shot(project: Project, shot_id: str = "S001", **over) -> ShotSpec:
    data = {"id": shot_id, "scene": "store", "characters": ["lin"],
            "dialogue": {"speaker": "lin", "text": "这不可能。"}, "duration": "auto"}
    data.update(over)
    shot = ShotSpec.model_validate(data)
    project.save_shot(shot)
    idx = project.load_index()
    if shot_id not in idx.order:
        idx.order.append(shot_id)
        project.save_index(idx)
    return shot


@pytest.fixture
def project(tmp_path: Path) -> Project:
    p = Project.create(tmp_path / PROJECT_NAME, name=PROJECT_NAME, git_init=False)
    _bible(p)
    _shot(p, "S001")
    return p


# ============================================================ SPEND-P0-001


def test_plain_sum_would_bypass_the_breaker():
    """The exact IEEE-754 foot-gun the fix removes: a NaN/negative folded into a
    running total defeats every ``running > budget`` compare, so the old
    ``running += float(cost or 0.0)`` kept submitting paid work."""
    poisoned = 0.0 + float("nan")
    assert not (poisoned > 1.0)  # NaN compare is always False → breaker never trips
    negative = 0.0 + (-100.0) + 60.0
    assert not (negative > 50.0)  # a negative cancels later real spend


def test_checked_cost_rejects_nan_inf_negative_and_nonnumeric():
    for bad in (float("nan"), float("inf"), float("-inf"), -0.01, "abc", None):
        with pytest.raises(spend.PaidResultInvalid):
            spend.checked_cost(bad)


def test_checked_cost_accepts_finite_nonnegative():
    assert spend.checked_cost(0.0) == 0.0
    assert spend.checked_cost(12.5) == 12.5
    assert spend.checked_cost("3.5") == 3.5


def test_checked_cost_currency_mismatch_fails_closed():
    with pytest.raises(spend.PaidResultInvalid) as ei:
        spend.checked_cost(1.0, currency="USD", budget_currency="CNY")
    assert ei.value.reason == "paid_currency_mismatch"
    # matching / absent budget currency is fine
    assert spend.checked_cost(1.0, currency="CNY", budget_currency="CNY") == 1.0
    assert spend.checked_cost(1.0, currency="USD") == 1.0


def test_book_actual_cost_fails_closed_and_breaker_then_works():
    budget = 1.0
    running = 0.0
    # a NaN result never enters the total — it fails closed instead of poisoning.
    with pytest.raises(spend.PaidResultInvalid):
        running = spend.book_actual_cost(running, float("nan"))
    assert running == 0.0  # unchanged; caller stops submitting new paid work
    # valid costs accumulate and the breaker fires deterministically.
    running = spend.book_actual_cost(running, 10.0)
    assert running == 10.0 and running > budget

    # a negative can't sneak the total back under budget either.
    with pytest.raises(spend.PaidResultInvalid):
        spend.book_actual_cost(running, -100.0)


# ========================================================= DIRECTOR-P0-001


def _write_raw_proposal(project: Project, filename: str, data: dict) -> Path:
    d.proposals_dir(project).mkdir(parents=True, exist_ok=True)
    path = d.proposals_dir(project) / filename
    write_yaml(path, data)
    return path


def test_load_proposal_refuses_internal_id_mismatch(project):
    # a file prop_0001.yaml whose internal id points elsewhere (traversal/spoof).
    _write_raw_proposal(project, "prop_0001.yaml",
                        {"id": "../../project", "state": "proposed", "actions": []})
    before = (project.root / "project.yaml").read_bytes()
    with pytest.raises(d.DirectorError):
        d.load_proposal(project, "prop_0001")
    # reject() loads first, so the traversal never reaches a save.
    with pytest.raises(d.DirectorError):
        d.reject(project, "prop_0001")
    assert (project.root / "project.yaml").read_bytes() == before  # truth intact


def test_save_refuses_traversal_id(project):
    before = (project.root / "project.yaml").read_bytes()
    evil = d.Proposal(id="../../project", state="rejected", actions=[])
    with pytest.raises(d.DirectorError):
        d._save(project, evil)
    assert (project.root / "project.yaml").read_bytes() == before
    # an absolute id is refused the same way.
    evil2 = d.Proposal(id="/etc/passwd", state="rejected", actions=[])
    with pytest.raises(d.DirectorError):
        d._save(project, evil2)


def test_list_skips_id_mismatched_files(project):
    good = d.propose(project, [{"type": "snapshot"}])
    _write_raw_proposal(project, "prop_0002.yaml",
                        {"id": "../../project", "state": "proposed", "actions": []})
    ids = [p.id for p in d.list_proposals(project)]
    assert good.id in ids
    assert "../../project" not in ids


# ========================================================= DIRECTOR-P0-002


def test_forged_confirmed_state_without_credential_is_refused(project):
    prop = d.propose(project, [{"type": "snapshot", "label": "cp"}])
    # forge the confirmation directly in the (editable) truth file — no confirm().
    data = d.read_yaml(d.proposals_dir(project) / f"{prop.id}.yaml")
    data["state"] = "confirmed"
    data["confirmed_by"] = "human"
    write_yaml(d.proposals_dir(project) / f"{prop.id}.yaml", data)
    with pytest.raises(d.DirectorError, match="确认凭据"):
        d.execute(project, prop.id)


def test_post_confirm_action_swap_is_refused(project):
    prop = d.propose(project, [{"type": "snapshot", "label": "safe"}])
    d.confirm(project, prop.id, actor="human")  # credential bound to the "safe" snapshot
    # swap the action content AFTER confirmation (state stays confirmed).
    data = d.read_yaml(d.proposals_dir(project) / f"{prop.id}.yaml")
    data["actions"][0]["action"]["label"] = "swapped"
    write_yaml(d.proposals_dir(project) / f"{prop.id}.yaml", data)
    with pytest.raises(d.DirectorError, match="确认凭据"):
        d.execute(project, prop.id)


def test_legit_confirm_then_execute_is_allowed(project):
    prop = d.propose(project, [{"type": "captions", "op": "revert"}])
    d.confirm(project, prop.id, actor="human")
    out = d.execute(project, prop.id)  # must NOT be refused
    assert out.proposal_id == prop.id and out.ok
    assert out.state == "done"


# ========================================================= DIRECTOR-P0-003


def test_concurrent_execute_claims_and_runs_action_once(project, monkeypatch):
    prop = d.propose(project, [{"type": "snapshot", "label": "cp"}])
    d.confirm(project, prop.id, actor="human")

    calls: list[str] = []

    def _fake_run_action(project, action, actor, on_phase, *,
                         agent_profile="collaborative"):
        calls.append(action["type"])
        # widen the window between claim and completion so a second thread races.
        threading.Event().wait(0.05)
        return {"op": "snapshot", "sha": "deadbeef"}

    monkeypatch.setattr(d, "_run_action", _fake_run_action)

    barrier = threading.Barrier(2)
    results: list = []
    errors: list = []

    def _worker():
        barrier.wait()
        try:
            results.append(d.execute(project, prop.id))
        except d.DirectorError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls == ["snapshot"]  # the action ran EXACTLY once (no double-submit)
    assert len(results) == 1 and len(errors) == 1


def test_second_execute_after_done_is_refused(project):
    prop = d.propose(project, [{"type": "captions", "op": "revert"}])
    d.confirm(project, prop.id, actor="human")
    out = d.execute(project, prop.id)
    assert out.state == "done"
    with pytest.raises(d.DirectorError, match="only a confirmed"):
        d.execute(project, prop.id)  # done → cannot re-run (idempotent, no re-spend)


# =========================================================== WINCLI-P0-003


def _fake_project_with_ask_before(tokens):
    return SimpleNamespace(load_config=lambda: SimpleNamespace(ask_before=list(tokens)))


def _fake_edge_provider():
    from manju.providers.edge_tts import EdgeTtsProvider

    return SimpleNamespace(
        id="edge", manifest=None,
        NETWORK_EGRESS=EdgeTtsProvider.NETWORK_EGRESS,
        NETWORK_EGRESS_ENDPOINT=EdgeTtsProvider.NETWORK_EGRESS_ENDPOINT)


def test_edge_adapter_is_marked_network_egress():
    from manju.providers.edge_tts import EdgeTtsProvider

    assert EdgeTtsProvider.NETWORK_EGRESS is True
    assert "Microsoft" in EdgeTtsProvider.NETWORK_EGRESS_ENDPOINT


def test_egress_target_describes_provider_and_destination():
    edge = _fake_edge_provider()
    tgt = voice.tts_egress_target(edge)
    assert tgt["provider"] == "edge"
    assert "Microsoft" in tgt["destination"]
    assert "dialogue" in tgt["data"]
    # a generic manifest exposes its submit URL host.
    generic = SimpleNamespace(
        id="tts_x",
        manifest=SimpleNamespace(submit=SimpleNamespace(url="https://api.tts.example/v1/say")))
    assert voice.tts_egress_target(generic)["destination"] == "api.tts.example"


def test_egress_gate_noop_without_token():
    proj = _fake_project_with_ask_before(["expensive_generation"])
    # zero-cost cloud provider, but the token is absent → additive no-op.
    voice.network_egress_gate(proj, [_fake_edge_provider()], allow_network=False)


def test_egress_gate_refuses_when_token_present():
    proj = _fake_project_with_ask_before(["expensive_generation", "external_data_transfer"])
    with pytest.raises(voice.VoiceEgressWaiting) as ei:
        voice.network_egress_gate(proj, [_fake_edge_provider()], allow_network=False)
    assert ei.value.reason == "external_data_transfer"
    assert "edge" in [str(p) for p in ei.value.providers]


def test_egress_gate_allow_network_bypasses_even_with_token():
    proj = _fake_project_with_ask_before(["external_data_transfer"])
    # explicit network consent lets it through (spend gate stays separate).
    voice.network_egress_gate(proj, [_fake_edge_provider()], allow_network=True)
