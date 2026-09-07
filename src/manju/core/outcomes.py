"""The shared operation-outcome model (Priority 1 item 5).

Business logic across the engine signals four semantic outcomes — success, "I
need the owner to confirm a paid action", "the operation was canceled", and
"it failed". Historically each surface encoded those four its own way:

* CLI:  ``_fail(msg, code="waiting_user")`` + an exit code;
* GUI:  result dicts like ``{"waiting_user": True}`` / ``{"canceled": True}``;
:class:`OperationOutcome` is the ONE semantic result the business layer returns.
CLI and GUI only *adapt* it into their own shape — they do not each re-derive
what "canceled" means. :func:`classify_exception` is the single place
that maps the engine's exceptions (``WaitingUser``, ``ProviderCanceled``,
``BuildCanceled``, ``ProviderFailure``) onto :class:`OutcomeCode`, so a
``ProviderCanceled`` becomes the SAME outcome in all three surfaces.

Pure model: the dataclass/enum/adapters import nothing. The exception
classifier uses deferred imports (the established core pattern — see
core/locale.py, core/series.py) so the pure model never hard-depends on the
higher layers that define those exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class OutcomeCode(StrEnum):
    OK = "ok"
    WAITING_USER = "waiting_user"
    CANCELED = "canceled"
    FAILED = "failed"


# Billing disposition tokens (Priority 1 item 8) — a paid operation that is
# canceled or fails cannot always prove the provider stopped BEFORE billing.
class BillingState(StrEnum):
    NOT_BILLED = "not_billed"                       # provably no charge
    BILLED = "billed"                               # provably charged
    CONFIRMED_CANCELED = "confirmed_canceled"       # provider confirmed stop, no charge
    CANCEL_REQUESTED_UNKNOWN = "cancel_requested_unknown"  # may have billed
    COMPLETED_BEFORE_CANCEL = "completed_before_cancel"    # finished (and billed)
    NOT_SUPPORTED = "not_supported"                 # provider cannot cancel


# The four explicit cancellation dispositions the task calls out (item 8). Only
# CONFIRMED_CANCELED proves no charge; the rest may have billed.
_CANCEL_DISPOSITIONS = frozenset({
    BillingState.CONFIRMED_CANCELED,
    BillingState.CANCEL_REQUESTED_UNKNOWN,
    BillingState.NOT_SUPPORTED,
    BillingState.COMPLETED_BEFORE_CANCEL,
})


def may_have_billed(disposition: str | None) -> bool:
    """True unless the disposition PROVES no charge. A local cancel request does
    not prove the remote provider stopped before billing, so anything other than
    CONFIRMED_CANCELED / NOT_BILLED is treated as possibly-billed."""
    return disposition not in (
        BillingState.CONFIRMED_CANCELED, BillingState.NOT_BILLED, None)


@dataclass(frozen=True, slots=True)
class CancelRecord:
    """Persistable record of a cancellation's billing disposition (item 8).

    Serializes to exactly the shape the task specifies. ``automatic_resubmit``
    is ALWAYS False for any cancellation — a possibly-billed or deliberately
    stopped request must never be silently re-submitted; retrying it requires an
    explicit owner confirmation.
    """

    provider_job_id: str | None
    cancel_requested_at: str | None
    cancel_disposition: str
    may_have_billed: bool
    automatic_resubmit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_job_id": self.provider_job_id,
            "cancel_requested_at": self.cancel_requested_at,
            "cancel_disposition": self.cancel_disposition,
            "may_have_billed": self.may_have_billed,
            "automatic_resubmit": self.automatic_resubmit,
        }

    @classmethod
    def for_disposition(cls, disposition: str, *, provider_job_id: str | None = None,
                        cancel_requested_at: str | None = None) -> CancelRecord:
        return cls(
            provider_job_id=provider_job_id,
            cancel_requested_at=cancel_requested_at,
            cancel_disposition=disposition,
            may_have_billed=may_have_billed(disposition),
            automatic_resubmit=False,  # never auto-resubmit a canceled request
        )

    @classmethod
    def from_provider_canceled(cls, exc: BaseException, *,
                               cancel_requested_at: str | None = None) -> CancelRecord:
        """Build the record for a ProviderCanceled: the remote may still bill
        (uncertain), so may_have_billed=True and automatic_resubmit=False."""
        return cls.for_disposition(
            BillingState.CANCEL_REQUESTED_UNKNOWN,
            provider_job_id=getattr(exc, "job_id", None),
            cancel_requested_at=cancel_requested_at,
        )


@dataclass(frozen=True, slots=True)
class OperationOutcome:
    """One semantic result, shared by the business layer.

    ``retryable`` means "safe/meaningful to run again". A paid operation whose
    billing is uncertain (``billing_state == cancel_requested_unknown``) is
    NOT retryable without explicit user confirmation (item 8): auto-retry could
    double-charge. :meth:`auto_resubmit_allowed` encodes that safety rule.
    """

    code: OutcomeCode
    message: str = ""
    retryable: bool = False
    billing_state: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    # -- constructors -------------------------------------------------------
    @classmethod
    def ok(cls, message: str = "", **data: Any) -> OperationOutcome:
        return cls(OutcomeCode.OK, message=message, data=dict(data))

    @classmethod
    def waiting_user(cls, message: str = "", **data: Any) -> OperationOutcome:
        # Waiting for confirmation is inherently re-runnable (with assume_yes).
        return cls(OutcomeCode.WAITING_USER, message=message, retryable=True,
                   data=dict(data))

    @classmethod
    def canceled(cls, message: str = "", *, billing_state: str | None = None,
                 **data: Any) -> OperationOutcome:
        return cls(OutcomeCode.CANCELED, message=message,
                   billing_state=billing_state, data=dict(data))

    @classmethod
    def failed(cls, message: str = "", *, retryable: bool = False,
               billing_state: str | None = None, **data: Any) -> OperationOutcome:
        return cls(OutcomeCode.FAILED, message=message, retryable=retryable,
                   billing_state=billing_state, data=dict(data))

    # -- semantics ----------------------------------------------------------
    @property
    def is_ok(self) -> bool:
        return self.code is OutcomeCode.OK

    def auto_resubmit_allowed(self) -> bool:
        """May the system re-submit this WITHOUT asking the owner first?

        Never for an uncertain-billing cancellation (item 8: a possibly-billed
        paid request must not be silently retried), and never for a plain
        cancel — cancellation is a deliberate stop, not a transient error.
        """
        if self.code is OutcomeCode.CANCELED:
            return False
        if self.billing_state in (
            BillingState.CANCEL_REQUESTED_UNKNOWN,
            BillingState.COMPLETED_BEFORE_CANCEL,
        ):
            return False
        return self.retryable

    # -- adapters (CLI / GUI only adapt, never re-derive) -------------------
    def to_gui_dict(self) -> dict[str, Any]:
        """The GUI job-result shape."""
        out: dict[str, Any] = dict(self.data)
        if self.code is OutcomeCode.WAITING_USER:
            out["waiting_user"] = True
            if self.message and "errors" not in out:
                out["errors"] = [self.message]
        elif self.code is OutcomeCode.CANCELED:
            out["canceled"] = True
            if self.message and "errors" not in out:
                out["errors"] = [self.message]
        elif self.code is OutcomeCode.FAILED:
            out.setdefault("error", self.message)
        if self.billing_state is not None:
            out["billing_state"] = self.billing_state
        return out

    def to_cli(self) -> dict[str, Any]:
        """CLI adaptation: a stable exit code + the branchable machine code.

        0 = ok; 2 = waiting_user (a soft, actionable stop — re-run with --yes);
        3 = canceled; 1 = failed. The ``code`` string matches what ``_fail``
        already emits, so agents keep string-matching the same tokens.
        """
        exit_code = {
            OutcomeCode.OK: 0,
            OutcomeCode.WAITING_USER: 2,
            OutcomeCode.CANCELED: 3,
            OutcomeCode.FAILED: 1,
        }[self.code]
        return {"exit_code": exit_code, "code": str(self.code),
                "message": self.message, "billing_state": self.billing_state}


def classify_exception(exc: BaseException) -> OperationOutcome:
    """Map an engine exception onto the shared outcome — the ONE place that
    knows ``ProviderCanceled``/``BuildCanceled`` → canceled, ``WaitingUser`` →
    waiting_user, everything else → failed. Deferred imports keep the pure
    model free of upward dependencies."""
    msg = " ".join(str(exc).split())[:500]

    try:
        from ..build.graph import WaitingUser
        if isinstance(exc, WaitingUser):
            return OperationOutcome.waiting_user(msg)
    except ImportError:
        pass

    # ProviderCanceled means "we stopped WAITING for a remote job here" — the
    # remote may still complete and bill (its id was persisted at submit, so a
    # later poll resumes rather than resubmits; see ProviderCanceled's own
    # docstring). That is exactly the uncertain-billing disposition (item 8):
    # a possibly-billed cancel must not be silently auto-resubmitted.
    try:
        from ..providers.base import ProviderCanceled
        if isinstance(exc, ProviderCanceled):
            return OperationOutcome.canceled(
                msg,
                billing_state=BillingState.CANCEL_REQUESTED_UNKNOWN,
                provider_job_id=getattr(exc, "job_id", None),
                provider_id=getattr(exc, "provider_id", None),
            )
    except ImportError:
        pass

    try:
        from ..build.graph import BuildCanceled
        if isinstance(exc, BuildCanceled):
            return OperationOutcome.canceled(msg)
    except ImportError:
        pass

    from ..media.ffmpeg import MediaCanceled
    if isinstance(exc, MediaCanceled):
        return OperationOutcome.canceled(msg)

    return OperationOutcome.failed(msg)
