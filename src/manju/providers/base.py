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
import sqlite3
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..core.container import Project, TakeInfo
from ..core.models import ProbeInfo, RemoteJobInfo, ShotSpec, TakeSidecar

if TYPE_CHECKING:
    from .refs import RefSet


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


class ProviderCanceled(RuntimeError):
    """The caller's ``should_cancel()`` tripped while THIS call was polling a
    remote job (goal: honest job cancellation, gui/jobs.py JobRunner.cancel).

    Deliberately NOT a :class:`ProviderFailure` / :class:`NeedsHumanInput` /
    ``MediaError`` — it is not a failure at all, so
    :func:`providers.registry.generate_with_fallback`'s per-provider ``except``
    clauses never catch it and silently "try the next provider"; it propagates
    straight out and the build unwinds as a cancellation, not a degradation.

    The remote job may still complete and bill: its id was already persisted
    the moment ``submit()`` returned (§8.1 "提交成功即写入" — see
    :meth:`CloudProvider._on_submit`), so a later build/poll resumes it rather
    than resubmitting (never double-charges). This exception only means "we
    stopped WAITING for it here"."""

    def __init__(self, provider_id: str, job_id: str):
        super().__init__(
            f"{provider_id}: 已取消等待任务 {job_id} 完成 — 远程任务可能仍在进行并计费,"
            f"job id 已记录(§8.1),后续构建/轮询会恢复轮询而不是重新提交"
        )
        self.provider_id = provider_id
        self.job_id = job_id


# Per-kind default hint (goal 10): one actionable line when the provider did not
# supply a more specific ``detail["hint"]``. content_rejected is first-class and
# never auto-retried, so its hint points at the action that CAN unblock it.
_KIND_HINT: dict[str, str] = {
    "content_rejected": "审核拒绝(§8.1,不自动重试):改写提示词(§8.5)或换供应商后重试",
    "rate_limited": "触发限流:调低 manifest 的 limits.rate_limit_per_min,或稍后重试",
    "timeout": "网络/任务超时:检查网络与供应商状态后可重试(此类可安全重试)",
    "invalid": "输入无效:核对 manifest 字段与 API key(密钥从不入库,§8.2)",
    "provider_error": "看 evidence 里的 HTTP 状态/响应体,核对 manifest 的 url 与路径映射",
}


def record_provider_failure(project, shot_id: str, provider_id: str,
                            exc: "ProviderFailure", *, actor: str = "engine") -> str | None:
    """Record a generation failure as a structured :class:`Failure` (goal 10).

    The single provider-side sink: cloud adapters route every terminal failure
    here via :meth:`CloudProvider._on_failure`, and the in-process adapters
    (ComfyUI / local_cmd) call it from their own ``generate`` error path — so a
    submit/poll/download failure or a 审核拒绝 all land in ``reports/failures.jsonl``
    in the SAME shape, keyed to the shot. Evidence is the HTTP status + body head
    / job or node id / the reason the poll surface returned; the hint names the
    manifest field or the "is ComfyUI running?" check when the caller supplied one
    in ``detail``. Best-effort — recording never blocks or masks generation.

    Returns the record id (so a ledger row can cross-reference it), or ``None``.
    """
    try:
        from ..core.failures import Failure, record_failure

        detail = dict(exc.detail or {})
        kind = getattr(exc, "kind", None)
        kind_val = kind.value if kind is not None else "provider_error"
        status = detail.get("status")
        job_id = detail.get("job_id") or detail.get("prompt_id")
        node_id = detail.get("node_id")
        body = detail.get("body") or detail.get("reason")

        head_parts: list[str] = []
        if status is not None:
            head_parts.append(f"HTTP {status}")
        if job_id:
            head_parts.append(f"job {job_id}")
        if node_id is not None:
            head_parts.append(f"node {node_id}")
        head = "  ".join(head_parts)
        body_head = str(body)[:800] if body else ""
        evidence = "\n".join(p for p in (head, body_head) if p).strip() or exc.message

        rec = record_failure(
            project,
            Failure(
                step="generate",
                subject=shot_id,
                cause=f"{kind_val}: {' '.join(str(exc.message).split())}"[:400],
                evidence=evidence,
                hint=detail.get("hint") or _KIND_HINT.get(kind_val, ""),
                actor=actor,
                detail={"provider": provider_id, "failure_kind": kind_val},
            ),
        )
        return rec.get("id")
    except Exception:
        return None


@dataclass
class GenerationRequest:
    """Everything a provider needs to produce takes for one shot.

    ``spec_hash`` is the shot's staleness anchor (§4.3); generated takes carry
    it in their sidecar so ``manju build`` can tell fresh from stale. Manual
    imports override it with :data:`manju.core.hashing.MANUAL_HASH`.

    ``estimated_cost`` is the pre-flight *事前* price (§8.3) the planner computed
    for this shot (already ×candidates). A cloud provider threads it onto its
    ledger row in ``_on_success`` so ``manju spend`` can show estimate-vs-actual
    for cloud money too, not only local runs. ``None`` when no estimate was
    available — honest, never coerced to 0.
    """

    project: Project
    shot: ShotSpec
    bible: dict[str, dict]
    spec_hash: str
    duration_ms: int
    candidates: int = 1
    params: dict = field(default_factory=dict)
    estimated_cost: float | None = None
    # Build-mode knobs (goal item 14), both additive and default-absent so a
    # request built the old way is byte-identical: ``routing_bias`` is the else-
    # selector the active `manju build --mode` biases toward (threaded to
    # routing.resolve in the registry); ``max_retries`` overrides a cloud
    # provider's retry budget for retryable (rate-limit / timeout) failures for
    # THIS call only (None → the provider's own default, i.e. today's behaviour).
    routing_bias: str | None = None
    max_retries: int | None = None
    # Reference inputs (goal item 7) — resolved once at the build call site so
    # the tiering, lineage and reliability signals are shared across providers.
    # Additive and default-absent: a request built the old way (or a test) lazily
    # resolves via :meth:`refset` on first use, so every provider still sees refs.
    refs: "RefSet | None" = None
    # Cooperative cancellation (goal: honest job cancellation): checked between
    # CloudProvider poll sleeps (never mid-HTTP-call). Additive and
    # default-absent — a request built the old way never observes it, so the
    # default path (no GUI job cancel wired) is byte-identical.
    should_cancel: Callable[[], bool] | None = None

    def refset(self) -> "RefSet":
        """The resolved :class:`~manju.providers.refs.RefSet` for this shot.

        Returns the RefSet populated at the call site, or lazily resolves (and
        caches) one from the request's own ``project``/``shot``/``bible``/``params``
        so directly-constructed requests are never ref-blind."""
        if self.refs is None:
            from .refs import resolve_refs

            self.refs = resolve_refs(
                self.project, self.shot, self.bible, params=self.params
            )
        return self.refs


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
        import json as _json

        from ..core.spec import SPEC_VERSION, spec_payload

        # why-stale evidence: the spec as it was NOW, diffable later (§4.3).
        # One enrichment point for every generated take. Guarded against
        # pathological bloat (huge bible excerpts duplicate per take): past 32KB
        # the snapshot is dropped and staleness degrades to the generic note —
        # advisory data must never dominate the sidecar.
        #
        # round W (review #37/#16): every NEWLY generated take is snapshotted
        # (and hashed, via req.spec_hash — build/stale.py always computes that
        # at SPEC_VERSION for a fresh take) at the CURRENT SPEC_VERSION, and the
        # sidecar records which version so staleness compares apples to apples
        # forever after (§4.3 conservatism — old takes keep being judged by the
        # version they were made under; only new ones gain the new fields).
        snapshot: dict | None = spec_payload(req.shot, req.bible, version=SPEC_VERSION,
                                             project_root=req.project.root)
        try:
            if len(_json.dumps(snapshot, ensure_ascii=False)) > 32_768:
                snapshot = None
        except (TypeError, ValueError):
            snapshot = None

        sidecar = TakeSidecar(
            provider=self.id,
            spec_hash=spec_hash or req.spec_hash,
            spec_version=SPEC_VERSION,
            spec_snapshot=snapshot,
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

    Resume-polling contract (§8.1, §14): the id returned by ``submit`` is
    persisted to :class:`~manju.runtime.state.RuntimeState` the instant it comes
    back, before the job could possibly complete ("提交成功即写入"). A restart
    then finds it still pending for this shot+provider and resumes polling
    instead of resubmitting — an interrupted long task never resubmits and never
    double-charges. A caller may also force resume by supplying the id via
    ``req.params["remote_job_id"]``.

    Ledger contract (§8.3): each terminal outcome writes one run row — cost and
    currency on success, the classified :class:`FailureKind` on failure. All
    :class:`RuntimeState` use is best-effort: a broken or absent DB is swallowed
    so bookkeeping never blocks generation (state is disposable, §3).
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
        logger: Callable[[str], None] | None = None,
    ):
        self._timeout_s = timeout_s
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._max_retries = max_retries
        self._sleep = sleep_fn
        self._logger = logger

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
        # Runtime state is disposable (§3): every use of it below is best-effort
        # so a broken/absent DB can never block generation — generation is what
        # costs money, bookkeeping is not.
        state = self._open_state(req)
        try:
            # goal 27: an intent left OPEN by an earlier, unresolved attempt for
            # this shot+provider means that attempt may have reached the remote
            # side and could be billing right now — flag it loudly (never
            # silently) rather than proceeding as if nothing happened. Advisory
            # only: it does not block THIS call, which is presumably the
            # human/agent's deliberate new attempt.
            self._flag_dangling_intents(state, req)

            # Resume contract (§8.1, §14): a persisted job id — passed explicitly
            # or found still-pending for this shot+provider — means skip submit
            # and re-poll, so an interrupted long task never resubmits/re-charges.
            job_id: str | None = req.params.get("remote_job_id")
            if not job_id:
                job_id = self._resume_job_id(state, req)
            # Build-mode retry budget (goal 14): the request may cap retries for
            # THIS call (quality retries hardest, speed least); None keeps the
            # provider's own default — byte-identical to before modes existed.
            max_retries = self._max_retries if req.max_retries is None else req.max_retries
            attempt = 0
            while True:
                intent_id: str | None = None
                try:
                    if not job_id:
                        # goal 27: the PRE-SUBMIT breadcrumb — written before the
                        # paid call, resolved right after it returns (or raises)
                        # in the SAME process. Only a genuine crash between these
                        # two lines leaves it open for the NEXT attempt to flag.
                        intent_id = self._open_intent(state, req)
                        job_id = self.submit(req)
                        self._resolve_intent(state, intent_id, job_id)
                        intent_id = None
                        self._on_submit(state, req, job_id)  # 提交成功即写入
                    info = self._poll_to_completion(job_id, should_cancel=req.should_cancel)
                    takes = self._download_and_register(req, job_id, info)
                    self._on_success(state, req, job_id, info, takes)
                    return takes
                except ProviderFailure as exc:
                    if intent_id is not None:
                        # submit() itself raised: resolved locally, right here —
                        # not a dangling/open intent (goal 27).
                        self._resolve_intent(state, intent_id, None)
                    if exc.kind is FailureKind.content_rejected:
                        self._on_failure(state, req, job_id, exc)
                        raise  # §8.1: first-class, never auto-retried
                    retryable = exc.kind in (FailureKind.rate_limited, FailureKind.timeout)
                    if not retryable or attempt >= max_retries:
                        self._on_failure(state, req, job_id, exc)
                        raise
                    attempt += 1
                    self._sleep(self._backoff(attempt - 1))
        finally:
            self._close_state(state)

    # -- runtime-state bookkeeping: best-effort, never blocks generation ----
    #    (§3 the DB is disposable; §8.1 resume-polling; §8.3 per-call ledger)

    def _open_state(self, req: GenerationRequest):
        try:
            from ..runtime.state import RuntimeState

            return RuntimeState(req.project.root)
        except (OSError, sqlite3.Error):
            return None

    def _close_state(self, state) -> None:
        if state is None:
            return
        try:
            state.close()
        except (OSError, sqlite3.Error):
            pass

    def _resume_job_id(self, state, req: GenerationRequest) -> str | None:
        if state is None:
            return None
        try:
            pending = state.pending_jobs(shot=req.shot.id, provider=self.id)
        except (OSError, sqlite3.Error):
            return None
        if not pending:
            return None
        job_id = pending[0].get("remote_job_id")
        if self._logger is not None and job_id:
            self._logger(
                f"{self.id}: resuming poll on pending job {job_id} for shot "
                f"{req.shot.id} — no resubmit, no double-charge (§8.1)"
            )
        return job_id

    def _flag_dangling_intents(self, state, req: GenerationRequest) -> None:
        """goal 27: an intent left ``open`` by an earlier attempt for this
        shot+provider that never resolved (a process crash between opening
        the intent and getting a job id back) — the remote side may already
        be billing it. Recorded as a LOUD structured Failure (visible in
        ``manju failures``/the build's failure summary) so a resubmit is never
        blind, even though it does not block THIS call. Best-effort: state is
        disposable (§3) — a lookup failure here is silent (nothing to flag,
        not a hidden problem)."""
        if state is None:
            return
        try:
            dangling = state.dangling_intents(shot=req.shot.id, provider=self.id)
        except (OSError, sqlite3.Error):
            return
        if not dangling:
            return
        try:
            from ..core.failures import Failure, record_failure

            record_failure(
                req.project,
                Failure(
                    step="generate", subject=req.shot.id,
                    cause=f"{self.id}: {len(dangling)} 个未确认的提交意图(可能已提交并计费)",
                    evidence="\n".join(
                        f"intent {d['id']} opened {d['ts']}" for d in dangling[:8]),
                    hint="核对该 provider 后台/账单确认是否已提交成功,"
                         "再决定是否重试(goal 27,避免重复提交/重复计费)",
                    level="info", actor="engine",
                    detail={"provider": self.id, "intent_ids": [d["id"] for d in dangling]},
                ),
            )
        except Exception:
            pass

    def _open_intent(self, state, req: GenerationRequest) -> str | None:
        """goal 27: persist the pre-submit intent BEFORE the paid call. A
        failure to persist is NOT silently swallowed (§3 read-only
        disposability does not cover this write) — it is recorded as a loud
        structured Failure, but generation still proceeds: refusing to spend
        because local bookkeeping is unavailable would be a worse regression
        than the (bounded, documented) double-submit risk it exists to shrink."""
        if state is None:
            return None
        try:
            from ..core.hashing import hash_text

            params_hash = hash_text(str(sorted(req.params.items())))
        except Exception:
            params_hash = None
        try:
            return state.open_intent(provider=self.id, shot=req.shot.id,
                                     params_hash=params_hash)
        except (OSError, sqlite3.Error) as exc:
            try:
                from ..core.failures import Failure, record_failure

                record_failure(
                    req.project,
                    Failure(
                        step="generate", subject=req.shot.id,
                        cause=f"{self.id}: 无法写入提交前意图记录(intent),继续生成",
                        evidence=str(exc),
                        hint="本地 .manju/state.sqlite 写入失败;若随后进程崩溃,"
                             "重启后无法自动判断该次提交是否已发生(goal 27)",
                        level="info", actor="engine",
                        detail={"provider": self.id},
                    ),
                )
            except Exception:
                pass
            return None

    def _resolve_intent(self, state, intent_id: str | None,
                        remote_job_id: str | None) -> None:
        """goal 27: fold submit()'s outcome back onto its intent — best-effort
        (a failure here just means the NEXT run's dangling-intent scan sees a
        stale-but-actually-fine row; §3 disposable read)."""
        if state is None or intent_id is None:
            return
        try:
            state.resolve_intent(intent_id, remote_job_id)
        except (OSError, sqlite3.Error):
            pass

    def _on_submit(self, state, req: GenerationRequest, job_id: str) -> None:
        """Persist the confirmed job id (§8.1 "提交成功即写入"). goal 27: a
        failure to persist is surfaced LOUDLY (a structured Failure) instead
        of a bare ``except: pass`` — §3 disposability covers reads, not a
        pending-money write whose loss would silently reopen the resume
        contract's double-submit window. Generation still proceeds (the
        job id is already resolved on its intent above; refusing to continue
        here would not undo the spend that already happened)."""
        if state is None:
            return
        try:
            state.open_job(
                job_id, provider=self.id, shot=req.shot.id, params=dict(req.params)
            )
        except (OSError, sqlite3.Error) as exc:
            try:
                from ..core.failures import Failure, record_failure

                record_failure(
                    req.project,
                    Failure(
                        step="generate", subject=req.shot.id,
                        cause=f"{self.id}: 已提交的远程任务 {job_id} 未能写入本地 jobs 表",
                        evidence=str(exc),
                        hint="若进程随后崩溃,重启后可能重新提交该镜头 "
                             "(goal 27 双重计费窗口);必要时核对 provider 后台",
                        level="info", actor="engine",
                        detail={"provider": self.id, "remote_job_id": job_id},
                    ),
                )
            except Exception:
                pass

    def _on_success(self, state, req: GenerationRequest, job_id: str, info: dict,
                    takes: list[TakeInfo]) -> None:
        if state is None:
            return
        cost = info.get("cost")
        currency = info.get("currency") if cost is not None else None
        # record the params actually archived on the take (incl. compiled_prompt)
        params = dict(takes[0].sidecar.params) if takes else dict(req.params)
        try:
            state.close_job(job_id, "succeeded", provider=self.id)
            # Attribute-once: a cloud generate() self-records exactly ONE run row
            # per call — every produced take is folded into this single row
            # (take=",".join(...)), unlike _record_local_runs which writes one row
            # PER take. So the per-shot pre-flight estimate (already ×candidates)
            # is attributed here exactly once; a multi-candidate cloud run cannot
            # inflate estimated_total. estimated_cost stays None when the request
            # carried no estimate (honest; spend just shows no delta for this row).
            state.record_run(
                shot=req.shot.id,
                provider=self.id,
                status="succeeded",
                params=params,
                cost=float(cost) if cost is not None else 0.0,
                currency=currency,
                remote_job_id=job_id,
                take=",".join(t.name for t in takes) or None,
                estimated_cost=req.estimated_cost,
            )
        except (OSError, sqlite3.Error):
            pass

    def _on_failure(self, state, req: GenerationRequest, job_id: str | None,
                    exc: ProviderFailure) -> None:
        # Structured failure record FIRST (goal 10) — this is disk truth under
        # reports/ and is independent of the disposable ledger below, so it lands
        # even when state is None. It returns the record id so the ledger row can
        # point back at the full evidence (`manju tasks` reason ↔ failures.jsonl).
        failure_id = record_provider_failure(req.project, req.shot.id, self.id, exc)
        if state is None:
            return
        try:
            if job_id:
                state.close_job(job_id, "failed", provider=self.id)
            state.record_run(
                shot=req.shot.id,
                provider=self.id,
                status="failed",
                params=dict(req.params),
                failure_kind=exc.kind.value,
                remote_job_id=job_id,
                error=exc.message,
                failure_id=failure_id,
            )
        except (OSError, sqlite3.Error):
            pass

    def _backoff(self, i: int) -> float:
        return min(self._base_delay * (2 ** i), self._max_delay)

    def _poll_to_completion(self, job_id: str, *,
                            should_cancel: Callable[[], bool] | None = None) -> dict:
        elapsed = 0.0
        i = 0
        while True:
            # Cooperative cancellation (goal: honest job cancellation): observed
            # BETWEEN poll sleeps, never mid-HTTP-call. Trips STOP WAITING —
            # they never mark the remote job failed/retried (§8.1: the job id
            # is already persisted, so a later poll/build resumes it instead
            # of resubmitting). should_cancel is None by default (no GUI job
            # wired a cancel flag), so this is a no-op on the byte-identical
            # default path.
            if should_cancel is not None and should_cancel():
                raise ProviderCanceled(self.id, job_id)
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
            # §8.5: archive the compiled prompt (or the agent's verbatim
            # override) on every take so every yuan spent is reproducible.
            # Prefer a prompt the poll surface already returned; else compile it
            # here. Import lazily to keep the providers package import-light, and
            # never let a prompt hiccup block a paid download.
            compiled_prompt = info.get("compiled_prompt")
            if not compiled_prompt:
                try:
                    from .prompt import compile_prompt

                    compiled_prompt = compile_prompt(req.shot, req.bible)
                except Exception:
                    compiled_prompt = None
            params = dict(req.params)
            if compiled_prompt:
                params.setdefault("compiled_prompt", compiled_prompt)
            takes: list[TakeInfo] = []
            for f in files:
                remote = RemoteJobInfo(job_id=job_id, cost=cost, currency=currency)
                takes.append(
                    self._register(
                        req,
                        Path(f),
                        params=dict(params),
                        compiled_prompt=compiled_prompt,
                        remote=remote,
                    )
                )
            return takes
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
