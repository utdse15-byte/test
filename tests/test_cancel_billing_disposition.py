"""Behavioral tests for cancellation billing disposition in the GUI job runner
(P1 item 8). Covers item-2 bullet #6 (a canceled/uncertain paid request is not
auto-resubmitted) at the runtime job level.
"""

from __future__ import annotations

import time

import pytest

from manju.gui.jobs import Job, JobRunner, _cancel_billing
from manju.providers.base import ProviderCanceled


def _wait_state(runner, job_id, states, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job.state in states:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach {states}")


@pytest.fixture
def runner():
    r = JobRunner()
    yield r
    r.shutdown(timeout=2.0)


# --------------------------------------------------------------------------- #
# _cancel_billing: the disposition rule
# --------------------------------------------------------------------------- #

def test_paid_kind_self_reported_cancel_is_uncertain() -> None:
    job = Job(id="j", kind="voice", params={}, project_id="p")
    billing = _cancel_billing(job, {"canceled": True})
    assert billing["cancel_disposition"] == "cancel_requested_unknown"
    assert billing["may_have_billed"] is True
    assert billing["automatic_resubmit"] is False


def test_provider_canceled_records_remote_job_id() -> None:
    job = Job(id="j", kind="redo", params={}, project_id="p")
    billing = _cancel_billing(job, None, exc=ProviderCanceled("cloudx", "job-77"))
    assert billing["provider_job_id"] == "job-77"
    assert billing["may_have_billed"] is True
    assert billing["automatic_resubmit"] is False


def test_non_paid_kind_cancel_has_no_billing_uncertainty() -> None:
    job = Job(id="j", kind="export", params={}, project_id="p")
    assert _cancel_billing(job, {"canceled": True}) is None


def test_explicit_confirmed_canceled_disposition_is_preserved() -> None:
    job = Job(id="j", kind="voice", params={}, project_id="p")
    billing = _cancel_billing(
        job, {"canceled": True, "billing_state": "confirmed_canceled"})
    assert billing["cancel_disposition"] == "confirmed_canceled"
    assert billing["may_have_billed"] is False


def test_job_to_dict_exposes_billing() -> None:
    job = Job(id="j", kind="voice", params={}, project_id="p")
    assert job.to_dict()["billing"] is None
    job.billing = {"may_have_billed": True}
    assert job.to_dict()["billing"] == {"may_have_billed": True}


# --------------------------------------------------------------------------- #
# End-to-end through the runner worker
# --------------------------------------------------------------------------- #

def test_runner_records_uncertain_billing_on_paid_cancel(runner) -> None:
    job = runner.submit("voice", {}, lambda job: {"canceled": True, "errors": ["x"]})
    done = _wait_state(runner, job.id, ("canceled",))
    assert done.billing is not None
    assert done.billing["may_have_billed"] is True
    assert done.billing["automatic_resubmit"] is False


def test_runner_no_billing_on_non_paid_cancel(runner) -> None:
    job = runner.submit("export", {}, lambda job: {"canceled": True})
    done = _wait_state(runner, job.id, ("canceled",))
    assert done.billing is None


def test_runner_records_provider_job_id_on_provider_canceled(runner) -> None:
    def fn(job):
        job.cancel_event.set()  # so the raised cancel lands as "canceled"
        raise ProviderCanceled("cloudx", "job-abc")

    job = runner.submit("redo", {}, fn)
    done = _wait_state(runner, job.id, ("canceled",))
    assert done.state == "canceled"
    assert done.billing["provider_job_id"] == "job-abc"
    assert done.billing["may_have_billed"] is True
