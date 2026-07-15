"""Behavioral tests for the shared ExecutionContext / CancelToken (P1 item 6)."""

from __future__ import annotations

import threading

from manju.core.execctx import CancelToken, ExecutionContext


# --------------------------------------------------------------------------- #
# CancelToken unifies the two historical cancel shapes
# --------------------------------------------------------------------------- #

def test_never_token_is_never_canceled() -> None:
    tok = CancelToken.never()
    assert tok.is_canceled() is False
    assert tok.should_cancel() is False


def test_from_callable_none_is_never() -> None:
    assert CancelToken.from_callable(None).is_canceled() is False


def test_from_callable_reflects_predicate() -> None:
    flag = {"v": False}
    tok = CancelToken.from_callable(lambda: flag["v"])
    assert tok.is_canceled() is False
    flag["v"] = True
    assert tok.is_canceled() is True


def test_from_event_reflects_event_state() -> None:
    ev = threading.Event()
    tok = CancelToken.from_event(ev)
    assert tok.is_canceled() is False
    ev.set()
    assert tok.is_canceled() is True


def test_should_cancel_is_usable_by_existing_apis() -> None:
    # APIs take should_cancel: Callable[[], bool]; the token exposes exactly that.
    ev = threading.Event()
    tok = CancelToken.from_event(ev)
    predicate = tok.should_cancel
    assert callable(predicate)
    assert predicate() is False
    ev.set()
    assert predicate() is True


# --------------------------------------------------------------------------- #
# ExecutionContext bundles the loosely-propagated params
# --------------------------------------------------------------------------- #

def test_context_carries_all_fields() -> None:
    ev = threading.Event()
    ctx = ExecutionContext(
        project_id="p1", run_id="r1", locale="ja", target="final",
        assume_yes=True, cancel_token=CancelToken.from_event(ev))
    assert ctx.project_id == "p1"
    assert ctx.run_id == "r1"
    assert ctx.locale == "ja"
    assert ctx.target == "final"
    assert ctx.assume_yes is True
    assert ctx.is_canceled() is False
    ev.set()
    assert ctx.is_canceled() is True
    assert ctx.should_cancel() is True


def test_context_defaults_are_safe() -> None:
    ctx = ExecutionContext(project_id="p", run_id="r")
    assert ctx.locale is None and ctx.target is None
    assert ctx.assume_yes is False
    assert ctx.is_canceled() is False  # never-cancel default token


def test_from_job_bridges_cancel_event() -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.cancel_event = threading.Event()

    job = FakeJob()
    ctx = ExecutionContext.from_job(job, project_id="p", run_id="r", locale="en")
    assert ctx.locale == "en"
    assert ctx.is_canceled() is False
    job.cancel_event.set()
    assert ctx.is_canceled() is True


def test_from_job_without_event_is_never_canceled() -> None:
    class Bare:
        pass

    ctx = ExecutionContext.from_job(Bare(), project_id="p", run_id="r")
    assert ctx.is_canceled() is False


def test_with_locale_copies_and_preserves_token() -> None:
    ev = threading.Event()
    base = ExecutionContext(project_id="p", run_id="r", locale="en",
                            assume_yes=True, cancel_token=CancelToken.from_event(ev))
    ja = base.with_locale("ja")
    assert ja.locale == "ja"
    assert base.locale == "en"  # original unchanged (frozen)
    assert ja.assume_yes is True
    # same underlying cancel signal
    ev.set()
    assert ja.is_canceled() is True
