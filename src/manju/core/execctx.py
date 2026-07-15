"""Shared execution context for operation execution (Priority 1 item 6).

Engine operations thread the same handful of parameters through many layers —
locale, confirmation (assume_yes), run id, target, a cancellation signal, and
project identity. :class:`ExecutionContext` bundles them into one object so a
call site passes context, not six loosely-related positional/keyword args.

Cancellation has historically had two shapes: a ``should_cancel: Callable[[],
bool]`` (build/graph, providers) and a ``threading.Event`` (``Job.cancel_event``).
:class:`CancelToken` unifies them: build it from either, hand its
``.should_cancel`` callable to the existing APIs unchanged.

This is an ADDITIVE seam. Public APIs still accept their current params;
migrate call sites to accept an ``ExecutionContext`` incrementally. Pure model:
strictly typed, no engine imports.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CancelToken:
    """A cooperative cancellation signal.

    Wraps a zero-arg predicate that returns True once cancellation is
    requested. Construct from a ``threading.Event`` (:meth:`from_event`), a
    plain callable (:meth:`from_callable`), or :meth:`never` for a token that
    is never canceled. Hand :attr:`should_cancel` to any API that already
    accepts a ``should_cancel=`` callable.
    """

    _predicate: Callable[[], bool]

    @classmethod
    def never(cls) -> CancelToken:
        return cls(lambda: False)

    @classmethod
    def from_callable(cls, fn: Callable[[], bool] | None) -> CancelToken:
        if fn is None:
            return cls.never()
        return cls(fn)

    @classmethod
    def from_event(cls, event: threading.Event) -> CancelToken:
        return cls(event.is_set)

    def is_canceled(self) -> bool:
        return bool(self._predicate())

    @property
    def should_cancel(self) -> Callable[[], bool]:
        """The zero-arg predicate, for APIs that take ``should_cancel=``."""
        return self._predicate


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """The context one operation executes in.

    Replaces loosely-propagated locale / confirmation / run_id / target /
    cancel token / project identity. ``cancel_token`` defaults to a never-cancel
    token so context is cheap to build in non-cancelable paths.
    """

    project_id: str
    run_id: str
    locale: str | None = None
    target: str | None = None
    assume_yes: bool = False
    cancel_token: CancelToken = CancelToken.never()

    @property
    def should_cancel(self) -> Callable[[], bool]:
        """Convenience: the cancel predicate for existing ``should_cancel=`` APIs."""
        return self.cancel_token.should_cancel

    def is_canceled(self) -> bool:
        return self.cancel_token.is_canceled()

    @classmethod
    def from_job(cls, job: Any, *, project_id: str, run_id: str,
                 locale: str | None = None, target: str | None = None,
                 assume_yes: bool = False) -> ExecutionContext:
        """Migration seam: build a context from a GUI ``Job`` (its
        ``cancel_event`` becomes the token). Uses duck typing so ``core`` does
        not import the GUI layer."""
        event = getattr(job, "cancel_event", None)
        token = (CancelToken.from_event(event)
                 if isinstance(event, threading.Event) else CancelToken.never())
        return cls(project_id=project_id, run_id=run_id, locale=locale,
                   target=target, assume_yes=assume_yes, cancel_token=token)

    def with_locale(self, locale: str | None) -> ExecutionContext:
        """A copy scoped to one locale (per-locale build/QC passes, item 7)."""
        return ExecutionContext(
            project_id=self.project_id, run_id=self.run_id, locale=locale,
            target=self.target, assume_yes=self.assume_yes,
            cancel_token=self.cancel_token)
