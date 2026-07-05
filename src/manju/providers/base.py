"""Provider protocol (§8) — cloud-first, with a thin async task base class.

A provider turns a shot's intent (a :class:`GenerationRequest`) into one or
more registered takes. Local providers (manual import, kenburns, caption card)
run in-process; cloud providers follow the universal shape of Chinese video/
image APIs: submit a job, poll for status, download the result.

Design intent (§8.1):
- Failure kinds are first-class. ``content_rejected`` (审核拒绝) is NOT a
  corner case — suspense/thriller prompts will hit content moderation, so it
  is a first-class failure that is NEVER auto-retried; only ``rate_limited``
  and ``timeout`` are safely retryable.
- The remote job id is persisted by the *caller* the moment ``submit`` returns.
  On a restart the caller resumes polling the same job instead of resubmitting,
  so an interrupted long job never double-charges (§8.1, §14).

No real HTTP lives here. M3 adapters subclass :class:`CloudProvider` and fill
in ``submit``/``poll``/``download`` against a specific API; this module is only
the deterministic skeleton around them.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..core.container import Project, TakeInfo
from ..core.models import ProbeInfo, RemoteJobInfo, ShotSpec, TakeSidecar


class FailureKind(str, Enum):
    """How a generation attempt failed — the response differs per kind (§8.1)."""

    rate_limited = "rate_limited"       # safe to retry with backoff
    timeout = "timeout"                 # safe to retry with backoff
    content_rejected = "content_rejected"  # 审核拒绝 — first-class, NEVER auto-retried
    provider_error = "provider_error"   # record, switch provider / degrade
    invalid = "invalid"                 # bad input (e.g. no reference image)


class ProviderFailure(RuntimeError):
    """A generation attempt failed with a classified :class:`FailureKind`.

    ``detail`` carries structured context — for ``content_rejected`` it holds
    the full rejection reason so an agent can rewrite the prompt (§8.1, §8.5).
    """

    def __init__(self, kind: FailureKind, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail or {}


class NeedsHumanInput(RuntimeError):
    """The manual provider is waiting for a human to supply media (§6, §8.4)."""


@dataclass
class GenerationRequest:
    """Everything a provider needs to produce takes for one shot.

    ``spec_hash`` is the shot's staleness anchor (§4.3); generated takes carry
    it in their sidecar so ``manju build`` can tell fresh from stale. Manual
    imports override it with :data:`manju.core.hashing.MANUAL_HASH`.
    """

    project: Project
    shot: ShotSpec
    bible: dict[str, dict]
    spec_hash: str
    duration_ms: int
    candidates: int = 1
    params: dict = field(default_factory=dict)


def probe_media(path: Path) -> ProbeInfo | None:
    """Fill a take sidecar's probe via ``manju.media.probe`` if importable.

    Degrades to ``None`` when the media package has not landed yet or the probe
    itself fails, so provider modules import and run cleanly regardless (§8).
    """
    try:
        from ..media.probe import probe as _probe  # lazy: media may not exist yet
    except ImportError:
        return None
    try:
        return _probe(path)
    except Exception:
        return None


class Provider(ABC):
    """Base class for every provider. ``manual_import`` is a provider too (§8.4):
    human media and cloud media are fully equal downstream."""

    id: str = ""
    kind: str = "local"  # "local" | "cloud"

    @abstractmethod
    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        """Produce take(s) for ``req.shot`` and register them on the project.

        Implementations MUST persist produced media via
        ``req.project.register_take(shot_id, file, TakeSidecar(...))`` so the
        take becomes append-only project truth (§3, §4.2)."""

    # -- shared helper: register one produced media file as a take ----------

    def _register(
        self,
        req: GenerationRequest,
        media_file: Path,
        *,
        params: dict | None = None,
        compiled_prompt: str | None = None,
        remote: RemoteJobInfo | None = None,
        source: str | None = None,
        spec_hash: str | None = None,
    ) -> TakeInfo:
        sidecar = TakeSidecar(
            provider=self.id,
            spec_hash=spec_hash or req.spec_hash,
            params=dict(params or {}),
            compiled_prompt=compiled_prompt,
            remote=remote,
            probe=probe_media(media_file),
            source=source,
        )
        return req.project.register_take(req.shot.id, media_file, sidecar)


class CloudProvider(Provider):
    """Async task skeleton for cloud video/image/TTS APIs (§8.1).

    Concrete adapters implement three steps only::

        submit(req)        -> remote job id (persist it immediately)
        poll(job_id)       -> (status, info)   status in queued/running/succeeded/failed
        download(job, dir) -> list[Path]

    This class provides a concrete :meth:`generate` that ties them together:

    * exponential backoff polling (``base_delay`` 0.5s, doubling, capped at
      ``max_delay`` 8s) up to an overall ``timeout_s`` budget (default 600s).
      ``sleep_fn`` is injectable so tests run instantly and deterministically —
      the timeout budget is measured against the *accumulated intended* sleep,
      not wall-clock, so a stubbed ``sleep_fn`` still drives the timeout path.
    * retries ONLY ``rate_limited`` / ``timeout`` failures, at most
      ``max_retries`` (default 3) times.
    * NEVER retries ``content_rejected`` — it is re-raised immediately with the
      rejection reason in ``detail`` for the agent to act on (§8.1).

    Resume-polling contract (§8.1, §14): the caller persists the id returned by
    ``submit`` before the job could possibly complete. If a restart calls
    :meth:`generate` again with that id supplied via ``req.params["remote_job_id"]``,
    ``submit`` is skipped and polling resumes on the existing job — an
    interrupted long task never resubmits and never double-charges.
    """

    kind = "cloud"

    def __init__(
        self,
        *,
        timeout_s: float = 600.0,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        max_retries: int = 3,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self._timeout_s = timeout_s
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._max_retries = max_retries
        self._sleep = sleep_fn

    # -- abstract remote steps (M3 adapters implement these) ---------------

    @abstractmethod
    def submit(self, req: GenerationRequest) -> str:
        """Submit the job; return the remote job id. The caller persists it
        at once so a crash resumes polling instead of resubmitting (§8.1)."""

    @abstractmethod
    def poll(self, job_id: str) -> tuple[str, dict]:
        """Return ``(status, info)`` where status is one of
        ``queued``/``running``/``succeeded``/``failed``. On ``failed`` the info
        should carry ``failure_kind`` and a ``reason`` (§8.1)."""

    @abstractmethod
    def download(self, job_id: str, dest_dir: Path) -> list[Path]:
        """Download the finished job's media into ``dest_dir``; return paths."""

    # -- concrete orchestration --------------------------------------------

    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        # Resume contract: a persisted job id means skip submit and re-poll.
        job_id: str | None = req.params.get("remote_job_id")
        attempt = 0
        while True:
            try:
                if not job_id:
                    job_id = self.submit(req)
                info = self._poll_to_completion(job_id)
                return self._download_and_register(req, job_id, info)
            except ProviderFailure as exc:
                if exc.kind is FailureKind.content_rejected:
                    raise  # §8.1: first-class, never auto-retried
                retryable = exc.kind in (FailureKind.rate_limited, FailureKind.timeout)
                if not retryable or attempt >= self._max_retries:
                    raise
                attempt += 1
                self._sleep(self._backoff(attempt - 1))

    def _backoff(self, i: int) -> float:
        return min(self._base_delay * (2 ** i), self._max_delay)

    def _poll_to_completion(self, job_id: str) -> dict:
        elapsed = 0.0
        i = 0
        while True:
            status, info = self.poll(job_id)
            info = info or {}
            if status == "succeeded":
                return info
            if status == "failed":
                raise self._failure_from_info(job_id, info)
            if status not in ("queued", "running"):
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id} job {job_id}: unknown status {status!r}",
                    detail={"job_id": job_id, "status": status, **info},
                )
            delay = self._backoff(i)
            if elapsed + delay > self._timeout_s:
                raise ProviderFailure(
                    FailureKind.timeout,
                    f"{self.id} job {job_id} did not finish within {self._timeout_s}s",
                    detail={"job_id": job_id},
                )
            self._sleep(delay)
            elapsed += delay
            i += 1

    def _failure_from_info(self, job_id: str, info: dict) -> ProviderFailure:
        raw = info.get("failure_kind", FailureKind.provider_error)
        try:
            kind = raw if isinstance(raw, FailureKind) else FailureKind(str(raw))
        except ValueError:
            kind = FailureKind.provider_error
        reason = info.get("reason") or info.get("message") or "remote job failed"
        return ProviderFailure(
            kind,
            f"{self.id} job {job_id} failed ({kind.value}): {reason}",
            detail={"job_id": job_id, "reason": reason, **info},
        )

    def _download_and_register(
        self, req: GenerationRequest, job_id: str, info: dict
    ) -> list[TakeInfo]:
        tmp = Path(tempfile.mkdtemp(prefix=f"{self.id}_{req.shot.id}_"))
        try:
            files = self.download(job_id, tmp)
            if not files:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id} job {job_id} produced no downloadable files",
                    detail={"job_id": job_id},
                )
            currency = info.get("currency", "CNY")
            cost = info.get("cost")
            takes: list[TakeInfo] = []
            for f in files:
                remote = RemoteJobInfo(job_id=job_id, cost=cost, currency=currency)
                takes.append(
                    self._register(
                        req,
                        Path(f),
                        params=dict(req.params),
                        compiled_prompt=info.get("compiled_prompt"),
                        remote=remote,
                    )
                )
            return takes
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
