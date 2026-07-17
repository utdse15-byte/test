"""Behavioral contract for paid confirmation + cancellation (P1 item 2 bullets).

Drives the REAL engine (redo_shot) with a fake provider — no ffmpeg — to pin:

* #1 a paid operation without assume_yes stops as waiting_user;
* #2 the provider is NOT called while confirmation is pending;
* #3 original operation arguments are preserved through to the provider call;
* #4 cancellation during remote polling yields a canceled result;
* #5 no later phase / sidecar write / selection write / final publication
  happens after cancellation.

(Bullets #6/#9 are covered by test_operation_outcome; #8 by test_multilocale_qc;
#10 by test_jobkinds_registry.)
"""

from __future__ import annotations

import pytest

import manju.build.graph as graph
import manju.providers.registry as registry_mod
from manju.build.graph import WaitingUser, redo_shot
from manju.core.outcomes import OutcomeCode, classify_exception
from manju.providers.base import ProviderCanceled


@pytest.fixture
def priced(monkeypatch):
    """Every planned shot pretends to cost money, arming the §8.3 spend gate."""
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (5.0, "CNY"))


def test_paid_redo_without_assume_yes_waits_and_provider_not_called(
        tmp_project, add_shot, priced, monkeypatch) -> None:
    add_shot(tmp_project, "S001")
    calls = {"n": 0}

    def spy(req, chain):
        calls["n"] += 1
        return []

    monkeypatch.setattr(registry_mod, "generate_with_fallback", spy)

    # #1 paid op without assume_yes -> waiting_user (WaitingUser is the
    # envelope-less redo's waiting_user signal; classify confirms the mapping).
    with pytest.raises(WaitingUser) as ei:
        redo_shot(tmp_project, "S001", actor="ai")
    assert classify_exception(ei.value).code is OutcomeCode.WAITING_USER
    # #2 provider not called while confirmation is pending.
    assert calls["n"] == 0
    # nothing was written while pending.
    assert list(tmp_project.takes("S001")) == []


def test_cancel_during_redo_polling_is_canceled_and_writes_nothing(
        tmp_project, add_shot, priced, monkeypatch) -> None:
    add_shot(tmp_project, "S001")
    takes_before = list(tmp_project.takes("S001"))
    captured: dict[str, object] = {}

    def spy(req, chain):
        captured["req"] = req            # capture the provider-bound request
        raise ProviderCanceled("cloudx", "job-1")  # cancel mid-poll

    monkeypatch.setattr(registry_mod, "generate_with_fallback", spy)

    with pytest.raises(ProviderCanceled) as ei:
        redo_shot(tmp_project, "S001", seed=123, candidates=2, assume_yes=True,
                  should_cancel=lambda: True)

    # #4 cancellation during remote polling -> canceled outcome.
    assert classify_exception(ei.value).code is OutcomeCode.CANCELED

    # #3 the original arguments reached the provider UNCHANGED.
    req = captured["req"]
    assert req.shot.id == "S001"
    assert req.params.get("seed") == 123
    assert req.candidates == 2

    # #5 no later phase happened: no new take sidecar, no selection, no final.
    assert list(tmp_project.takes("S001")) == takes_before
    shot = tmp_project.load_shot("S001")
    assert not shot.status.selected_take
    assert tmp_project.newest_final_path() is None
