"""Behavioral tests for the shared OperationOutcome model (P1 item 5).

Covers the model, the single exception classifier, and — item 2's contract
bullet #9 — that ``ProviderCanceled`` maps to the SAME semantic outcome in CLI,
GUI and MCP, plus bullet #6 (a canceled/uncertain paid request is not
auto-resubmitted). No source scanning: everything asserts runtime values.
"""

from __future__ import annotations

from manju.build.graph import BuildCanceled, WaitingUser
from manju.core.outcomes import (
    BillingState,
    OperationOutcome,
    OutcomeCode,
    classify_exception,
)
from manju.providers.base import ProviderCanceled


# --------------------------------------------------------------------------- #
# The single classifier: engine exception -> shared semantic outcome
# --------------------------------------------------------------------------- #

def test_waiting_user_exception_classifies_as_waiting_user() -> None:
    o = classify_exception(WaitingUser("confirm please", 5.0, "CNY"))
    assert o.code is OutcomeCode.WAITING_USER
    assert o.retryable is True  # re-runnable with assume_yes


def test_provider_canceled_classifies_as_canceled_with_uncertain_billing() -> None:
    o = classify_exception(ProviderCanceled("cloudx", "job-123"))
    assert o.code is OutcomeCode.CANCELED
    # The remote may still bill (its id was persisted at submit): honest
    # uncertain-billing disposition, never a clean "canceled".
    assert o.billing_state == BillingState.CANCEL_REQUESTED_UNKNOWN
    assert o.data["provider_job_id"] == "job-123"
    assert o.data["provider_id"] == "cloudx"


def test_build_canceled_classifies_as_canceled() -> None:
    exc = BuildCanceled("stopped", generated=0, spent=0.0, currency=None)
    assert classify_exception(exc).code is OutcomeCode.CANCELED


def test_other_exceptions_classify_as_failed() -> None:
    assert classify_exception(ValueError("boom")).code is OutcomeCode.FAILED
    assert classify_exception(RuntimeError("nope")).code is OutcomeCode.FAILED


# --------------------------------------------------------------------------- #
# Bullet #6: a canceled or uncertain paid request is not auto-resubmitted
# --------------------------------------------------------------------------- #

def test_uncertain_paid_cancel_is_not_auto_resubmitted() -> None:
    o = classify_exception(ProviderCanceled("cloudx", "job-123"))
    assert o.auto_resubmit_allowed() is False


def test_plain_cancel_is_not_auto_resubmitted() -> None:
    assert OperationOutcome.canceled("stopped").auto_resubmit_allowed() is False


def test_completed_before_cancel_is_not_auto_resubmitted() -> None:
    o = OperationOutcome.canceled(
        "already done", billing_state=BillingState.COMPLETED_BEFORE_CANCEL)
    assert o.auto_resubmit_allowed() is False


def test_confirmed_canceled_is_still_not_auto_resubmitted() -> None:
    # Even a provably-not-billed cancel is a deliberate stop, not a retry.
    o = OperationOutcome.canceled(
        "stopped clean", billing_state=BillingState.CONFIRMED_CANCELED)
    assert o.auto_resubmit_allowed() is False


def test_waiting_user_is_re_runnable() -> None:
    assert OperationOutcome.waiting_user("confirm").auto_resubmit_allowed() is True


# --------------------------------------------------------------------------- #
# Bullet #9: ProviderCanceled -> the SAME outcome across CLI, GUI, MCP
# --------------------------------------------------------------------------- #

def test_provider_canceled_same_semantic_outcome_in_cli_gui_mcp() -> None:
    o = classify_exception(ProviderCanceled("cloudx", "job-123"))

    gui = o.to_gui_dict()
    mcp = o.to_mcp()
    cli = o.to_cli()

    # All three encode "canceled" — no surface silently downgrades it to ok.
    assert gui["canceled"] is True
    assert mcp["ok"] is False and mcp["code"] == "canceled"
    assert cli["code"] == "canceled" and cli["exit_code"] == 3

    # And all three carry the uncertain-billing disposition forward.
    assert gui["billing_state"] == BillingState.CANCEL_REQUESTED_UNKNOWN
    assert mcp["billing_state"] == BillingState.CANCEL_REQUESTED_UNKNOWN
    assert cli["billing_state"] == BillingState.CANCEL_REQUESTED_UNKNOWN


def test_waiting_user_same_semantic_outcome_in_cli_gui_mcp() -> None:
    o = OperationOutcome.waiting_user("confirm spend")
    assert o.to_gui_dict()["waiting_user"] is True
    assert o.to_mcp()["code"] == "waiting_user"
    assert o.to_cli()["code"] == "waiting_user" and o.to_cli()["exit_code"] == 2


def test_ok_outcome_across_surfaces() -> None:
    o = OperationOutcome.ok("done", shot="S001")
    assert "waiting_user" not in o.to_gui_dict() and "canceled" not in o.to_gui_dict()
    assert o.to_gui_dict()["shot"] == "S001"
    assert o.to_mcp()["ok"] is True and o.to_mcp()["code"] == "ok"
    assert o.to_cli()["exit_code"] == 0
