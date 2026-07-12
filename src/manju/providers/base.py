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
from typing import TYPE_CHECKING, Any, Callable

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

    def __init__(self, kind: FailureKind, message: str, *, detail: dict | None = None,
                 disposition: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail or {}
        # DR06 (ruling 6): the ORTHOGONAL submit-outcome classification
        # (providers.submission NOT_DISPATCHED / DEFINITELY_REJECTED / ADMITTED /
        # OUTCOME_UNKNOWN). ``None`` = legacy/conservative — a failure that never
        # classified its disposition keeps the pre-DR06 retry-by-kind behavior;
        # only an explicit OUTCOME_UNKNOWN triggers the fail-closed path (never
        # retry, never fallback, never resubmit). ``FailureKind`` is untouched.
        self.disposition = disposition


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
                detail={"provider": provider_id, "failure_kind": kind_val,
                        # DR06: an UNKNOWN submission's failure record carries its
                        # submission_id (existing free-form detail dict, no
                        # signature change) so `manju failures --json` links to it.
                        **({"submission_id": detail["submission_id"]}
                           if detail.get("submission_id") else {}),
                        **({"disposition": detail["disposition"]}
                           if detail.get("disposition") else {})},
            ),
        )
        return rec.get("id")
    except Exception:
        return None


# --------------------------------------------------------- bridge dispatch seam
# CLOSEOUT C2 (contract §3 执行边界): qualification/admission for the generative
# bridge lives HERE — the ONE provider-layer service every bridge surface
# funnels through (build.bridge.execute_bridge → dispatch_bridge →
# provider.generate). CLI/MCP/GUI are thin wrappers over execute_bridge, and a
# direct Python call cannot bypass it either: an unqualified provider yields
# transport 0 with durable refusal evidence. The admission itself is
# providers.qualification.bridge_admission (floor PRODUCTION_READY, plus the
# single-use operator risk-acceptance path) — consulted, never re-implemented,
# so CLI / MCP / direct share the SAME gate function, not three copies.

BRIDGE_PARAMS_KEY = "bridge"
BRIDGE_DISPATCH_CAPABILITY = "generative_bridge"


def dispatch_bridge(project, provider, req: "GenerationRequest", *,
                    capability: str = BRIDGE_DISPATCH_CAPABILITY) -> list:
    """Admission-gate + dispatch ONE generative-bridge request through the
    standard provider path. Refusal is a structured
    ``ProviderFailure(code=BRIDGE_NOT_QUALIFIED)`` raised BEFORE any transport
    (spend 0) and recorded durably (``reports/failures.jsonl``); admission
    proceeds to the ordinary ``provider.generate`` → ``register_take`` path,
    inheriting the AI_IDE_14 submission admission / budget / paid-recovery
    machinery unchanged."""
    from .qualification import BRIDGE_REFUSAL, bridge_admission

    plan = (req.params or {}).get(BRIDGE_PARAMS_KEY) or {}
    provider_id = getattr(provider, "id", "") or "<unknown>"
    decision = bridge_admission(provider_id, capability, project=project,
                                request_digest=plan.get("request_digest"))
    if not decision.get("admitted"):
        exc = ProviderFailure(
            FailureKind.invalid,
            f"{provider_id}: generative bridge refused before dispatch "
            f"(transport 0) — {decision.get('reason')}",
            detail={"code": decision.get("refusal") or BRIDGE_REFUSAL,
                    "capability": capability,
                    "request_digest": plan.get("request_digest"),
                    "min_required": decision.get("min_required"),
                    "level": decision.get("level"),
                    "evidence_corrupt": decision.get("evidence_corrupt", False),
                    "hint": "运行 provider qualification(真实 canary)或由操作者"
                            "登记一次性 risk acceptance(绑定本次 request digest)"})
        record_provider_failure(project, req.shot.id, provider_id, exc)
        raise exc
    # provenance of the gate decision on the request params (deterministic
    # content only — replays of the same plan produce the identical identity).
    req.params.setdefault("bridge_admission", {
        "admitted": True,
        "risk_accepted": bool(decision.get("risk_accepted", False)),
        "min_required": decision.get("min_required")})
    return provider.generate(req)


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
    # DR03C run-evidence (additive, evidence-only, default-absent): the per-run
    # attempt context ``providers.registry.generate_with_fallback`` emits ONE
    # ``stage_attempt`` event per provider try into when it is present. Set ONLY
    # by graph's build call sites (``_gen_one``); ``None`` for every direct
    # provider test / redo-outside-a-run, so the registry emits nothing and the
    # existing callers/tests are byte-identical. Typed loosely to avoid importing
    # build/ from providers/ (layering) — it is a ``build.attempts.RunEvidence``.
    evidence: Any = None
    # The CURRENT provider try's attempt id, stamped by the registry's evidence
    # chain just before each ``provider.generate`` call (the handle is pre-minted,
    # its terminal event lands after). Cloud providers self-record their ledger
    # row INSIDE generate() — before the registry can note anything — so this is
    # how ``_on_success``/``_on_failure`` thread the SAME attempt_id onto the
    # runs row (tasks --json parity for the expensive path, not just local).
    evidence_attempt_id: str | None = None
    # 08_10_12C WP3 (additive, default-absent): the parent take name when this
    # request is an EXPLICIT recipe-replay redo (`manju redo --from-take X`).
    # ``Provider._register`` stamps it onto every produced take's sidecar as
    # ``redo_of`` so the derived candidate-family view can join the redo to its
    # creative family. Provenance only — providers must not read it.
    redo_of: str | None = None

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
            # 08_10_12C WP3: explicit-redo creative lineage (None otherwise —
            # dropped on write, so non-redo takes stay byte-identical).
            redo_of=req.redo_of,
        )
        return req.project.register_take(req.shot.id, media_file, sidecar)


class _SubmissionContext:
    """DR06 — the per-submission admission bookkeeping for ONE paid cloud
    generate() call. Owns the submission_id/request_digest, the current state,
    and the hash-chain tail; every ``transition`` emits the ``submission_state``
    event FIRST (evidence) then updates the SQLite projection (§8.4 order), and
    threads the previous event's digest forward so the per-submission chain is
    intact. NO network. State can be ``None`` only on a resume of an ADMITTED
    submission whose row is being polled (never on a fresh submit — that path
    fail-closes if the DB is unavailable, ruling 5)."""

    def __init__(self, provider: "CloudProvider", state, req: "GenerationRequest",
                 *, submission_id: str, request_digest: str, current_state: str,
                 prev_event_digest: str | None = None):
        self.provider = provider
        self.state = state
        self.req = req
        self.submission_id = submission_id
        self.request_digest = request_digest
        self.current_state = current_state
        self.remote_job_id: str | None = None
        self._last_digest = prev_event_digest
        self._intent_id: str | None = None
        self.warnings: list[str] = []

    def emit(self, from_state: str | None, to_state: str, *, reason_code: str | None = None,
             remote_job_id: str | None = None, detail: dict | None = None,
             required: bool = False) -> None:
        """Emit ONE submission_state event through the WP1 coordinator.

        ``required=True`` (the PREPARED / DISPATCHING admission gate) turns an
        :class:`EvidenceWriteError` OR an empty append into a
        ``ProviderFailure(provider_error, code=submission_evidence_unavailable,
        disposition=NOT_DISPATCHED)`` — nothing was sent, so NOT_DISPATCHED is
        honest (a later attempt may retry; each provider fails closed at its own
        prepare). ``required=False`` (ADMITTED / terminal / classify, where money
        is already spent) keeps the best-effort warning so the flow is never
        orphaned."""
        from ..build.attempts import append_submission_event
        from ..core.events import EvidenceWriteError
        from . import submission as S
        from .submission import submission_event_digest

        reason: str | None = None
        try:
            rec = append_submission_event(
                self.req.project, submission_id=self.submission_id,
                request_digest=self.request_digest, from_state=from_state, to_state=to_state,
                provider_id=self.provider.id, shot=self.req.shot.id,
                remote_job_id=remote_job_id, reason_code=reason_code,
                prev_event_digest=self._last_digest, detail=detail, required=required)
        except EvidenceWriteError as exc:
            rec, reason = {}, exc.reason
        if rec:
            self._last_digest = submission_event_digest(rec)
            return
        if required:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.provider.id}: durable submission evidence could not be "
                f"persisted before dispatch — refusing to start a paid submit "
                f"(WP1 fail-closed admission gate)",
                detail={"code": "submission_evidence_unavailable",
                        "shot": self.req.shot.id, "reason": reason or "empty_append"},
                disposition=S.NOT_DISPATCHED,
            )
        self.warnings.append(
            f"submission evidence append failed for {self.submission_id} "
            f"({self.current_state}->{to_state})")

    def transition(self, to_state: str, *, reason_code: str | None = None,
                   remote_job_id: str | None = None, detail: dict | None = None,
                   required: bool = False) -> None:
        """Evidence-FIRST transition (§8.4/§8.5 row 5): the event lands before
        the SQLite projection, so a crash between them recovers the state from
        the event stream. Legality is enforced by the pure state machine.
        ``required=True`` applies the WP1 durable-admission gate to the event —
        any DISPATCHING (re-)entry that precedes a paid submit must be as
        durable as the first attempt's (constraint 4 covers EVERY submit)."""
        from . import submission as S

        S.assert_transition(self.current_state, to_state)
        self.emit(self.current_state, to_state, reason_code=reason_code,
                  remote_job_id=remote_job_id, detail=detail, required=required)
        if self.state is not None:
            try:
                self.state.set_submission_state(
                    self.submission_id, to_state, remote_job_id=remote_job_id)
            except Exception:
                pass  # projection is rebuildable from the event just written
        self.current_state = to_state
        if remote_job_id:
            self.remote_job_id = remote_job_id


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
        # Runtime state is disposable (§3) for READS and for post-commit
        # bookkeeping; but DR06 (ruling 5) makes the PRE-submit identity a
        # fail-closed gate: if PREPARED / the DISPATCHING claim cannot be
        # persisted, the paid submit MUST NOT start. The cloud flow that
        # succeeds cleanly is byte-identical bar the additive submission
        # evidence + intent columns.
        from . import submission as S

        state = self._open_state(req)
        try:
            # goal 27: flag any intent left OPEN by an earlier crashed attempt.
            self._flag_dangling_intents(state, req)

            # Resume contract (§8.1, §14): an EXPLICIT job id short-circuits to
            # re-poll (never resubmit) — the forced-resume path, unchanged.
            job_id: str | None = req.params.get("remote_job_id")
            sub: _SubmissionContext | None = None
            if not job_id:
                # DR06 (ruling 8): strict correlation against unresolved
                # submissions for this shot+provider BEFORE minting a new paid
                # submission. May resume an ADMITTED job under its submission_id,
                # re-dispatch an idempotent one, or FAIL CLOSED on an ambiguous
                # (DISPATCHING/OUTCOME_UNKNOWN) outcome — never auto-resubmit.
                action, job_id, sub = self._resolve_resume(state, req)
            max_retries = self._max_retries if req.max_retries is None else req.max_retries
            attempt = 0
            while True:
                intent_id: str | None = None
                try:
                    if not job_id:
                        # §8.4 write order: PREPARED (intent row + event) ->
                        # atomic DISPATCHING claim (+event) -> adapter submit.
                        if sub is None:
                            sub = self._prepare_submission(state, req)
                        else:
                            self._redispatch(sub)  # a retry / idempotent recovery
                        intent_id = sub._intent_id
                        try:
                            job_id = self.submit(req)
                        except ProviderFailure as exc:
                            # WP2 phase-aware default: INSIDE the submit phase, a
                            # ProviderFailure that never classified its disposition
                            # defaults to OUTCOME_UNKNOWN — the request may have
                            # reached the remote side, so it must never be auto-
                            # retried / resubmitted (double-charge risk).
                            # generic_cloud classifies its own transport; this is
                            # the conservative default for any adapter that did not
                            # (was: None -> kind-based retry -> re-submit).
                            if exc.disposition is None:
                                exc.disposition = S.OUTCOME_UNKNOWN_DISPOSITION
                                exc.detail.setdefault("disposition_defaulted",
                                                      "submit_phase")
                            self._resolve_intent(state, intent_id, None)
                            intent_id = None
                            self._classify_submit_failure(sub, exc)
                            raise
                        except Exception as exc:
                            # DR06 contract test 28 — the conservative DEFAULT at
                            # the ONE choke point: a RAW/unclassified adapter
                            # exception past the send boundary (escape-hatch
                            # adapters raising TimeoutError/ConnectionResetError/
                            # anything) is OUTCOME_UNKNOWN, never a crash and
                            # never a "safe to retry". generic_cloud classifies
                            # its own transport; this catches every adapter that
                            # did not.
                            from . import submission as S_

                            self._resolve_intent(state, intent_id, None)
                            intent_id = None
                            failure = ProviderFailure(
                                FailureKind.provider_error,
                                f"{self.id}: unclassified submit exception past "
                                f"the send boundary ({type(exc).__name__}: {exc}) "
                                "— outcome unknown; the remote side may have "
                                "accepted the task",
                                detail={"error_class": type(exc).__name__},
                                disposition=S_.OUTCOME_UNKNOWN_DISPOSITION,
                            )
                            self._classify_submit_failure(sub, failure)
                            raise failure from exc
                        # remote_job_id / provable receipt: ADMITTED event FIRST,
                        # THEN the SQLite projection (resolve_intent/open_job) —
                        # a crash between them recovers from evidence (§8.5 row 5).
                        sub.transition(S.ADMITTED, reason_code="submit_accepted",
                                       remote_job_id=job_id)
                        self._resolve_intent(state, intent_id, job_id)
                        intent_id = None
                        self._on_submit(state, req, job_id)  # 提交成功即写入
                    info = self._poll_to_completion(job_id, should_cancel=req.should_cancel)
                    takes = self._download_and_register(req, job_id, info)
                    self._on_success(state, req, job_id, info, takes)
                    # SUCCEEDED-class transition ONLY after the existing commit
                    # points (register_take + ledger) have run.
                    if sub is not None:
                        sub.transition(S.TERMINAL_SUCCESS, reason_code="downloaded")
                        self._surface_submission_warnings(req, sub)
                    return takes
                except ProviderFailure as exc:
                    if intent_id is not None:
                        self._resolve_intent(state, intent_id, None)
                    # DR06 (ruling 6/7): an OUTCOME_UNKNOWN submit outcome is
                    # fail-closed — never a local retry, never a fallback, never
                    # a resubmit. It propagates carrying its disposition so the
                    # registry stops the chain.
                    if exc.disposition == S.OUTCOME_UNKNOWN_DISPOSITION:
                        if sub is not None:
                            exc.detail.setdefault("submission_id", sub.submission_id)
                        exc.detail.setdefault("disposition", exc.disposition)
                        self._on_failure(state, req, job_id, exc)
                        self._surface_submission_warnings(req, sub)
                        raise
                    if exc.kind is FailureKind.content_rejected:
                        self._on_failure(state, req, job_id, exc)
                        if sub is not None and sub.current_state == S.ADMITTED:
                            sub.transition(S.TERMINAL_FAILURE, reason_code="content_rejected")
                        raise  # §8.1: first-class, never auto-retried
                    retryable = exc.kind in (FailureKind.rate_limited, FailureKind.timeout)
                    if not retryable or attempt >= max_retries:
                        self._on_failure(state, req, job_id, exc)
                        # A DEFINITE post-admission failure (remote job failed /
                        # unknown status / no downloadable files) is TERMINAL_
                        # FAILURE; an EXHAUSTED but retryable poll timeout leaves
                        # the submission ADMITTED (resume-safe — a later build
                        # re-polls the same job, never resubmits).
                        if sub is not None and sub.current_state == S.ADMITTED \
                                and not retryable:
                            sub.transition(S.TERMINAL_FAILURE, reason_code=exc.kind.value)
                        self._finalize_failed_submission(sub, exc)
                        raise
                    attempt += 1
                    self._sleep(self._backoff(attempt - 1))
        finally:
            self._close_state(state)

    # -- DR06 submission identity + admission (ruling 2/4/5/8) --------------

    def _submission_capability(self, req: GenerationRequest) -> str:
        """A deterministic capability string for the profile digest. Prefers the
        provider manifest's capabilities (first sorted), else the provider kind —
        stable, and moves the digest only on a real capability change."""
        manifest = getattr(self, "manifest", None)
        caps = sorted(getattr(manifest, "capabilities", None) or [])
        return caps[0] if caps else (self.kind or "generate")

    def _build_identity(self, req: GenerationRequest) -> tuple[dict, str]:
        """Assemble the submission identity + its request_digest (pure module).
        Hashes the small ref files and the compiled prompt HERE; the pure module
        never touches disk. Memoized per-provider on the request so the fresh
        path (correlation check + PREPARED) does not re-hash the ref files
        twice — keyed by provider id since a fallback provider has a distinct
        identity."""
        from . import submission as S
        from ..core.hashing import hash_file
        from .catalog import descriptor_for_manifest, provider_profile_digest

        cache = getattr(req, "_dr06_identity_cache", None)
        if cache is None:
            cache = req._dr06_identity_cache = {}
        if self.id in cache:
            return cache[self.id]

        manifest = getattr(self, "manifest", None)
        capability = self._submission_capability(req)
        descriptor = descriptor_for_manifest(manifest) if manifest is not None else None
        profile_digest = provider_profile_digest(self.id, capability, descriptor=descriptor)

        # P0 WP3 §5.1: for a PAID cloud submit the request identity is a
        # fail-closed gate — a compiled-prompt / ref-resolution / ref-hash
        # failure must NOT degrade to a None/empty identity and spend anyway
        # (the remote job would be shaped by an identity we cannot vouch for).
        # Local providers (non-"cloud" kind) keep the historic best-effort
        # behaviour — decided by kind here, never a global flag.
        strict = (self.kind == "cloud")

        # compiled prompt -> digest only (never stored). Best-effort compile.
        compiled_prompt = None
        try:
            from .prompt import compile_prompt

            compiled_prompt = compile_prompt(req.shot, req.bible)
        except Exception as exc:
            if strict:
                raise self._identity_unavailable(req, "prompt_compile", exc) from exc
            compiled_prompt = None

        # final selected refs in delivery order: (role, logical_id, sha256).
        ref_refs: list[dict] = []
        try:
            refset = req.refset()
            items = list(refset.image_items()) + list(refset.video_items())
        except Exception as exc:
            if strict:
                raise self._identity_unavailable(req, "ref_resolution", exc) from exc
            items = []
        for it in items:
            sha = None
            if it.path is not None and not it.is_url:
                try:
                    if it.path.is_file():
                        sha = hash_file(it.path)
                    elif strict:
                        # a declared LOCAL ref that should be hashed but is not a
                        # readable file — the identity is incomplete, fail closed.
                        raise OSError(f"local ref not a readable file: {it.ref}")
                except OSError as exc:
                    if strict:
                        raise self._identity_unavailable(req, "ref_hash", exc) from exc
                    sha = None
            ref_refs.append(S.ref_fact(it.kind, it.ref, sha))

        first_frame, last_frame = self._keyframe_flags(req)
        identity = S.build_submission_identity(
            shot_id=req.shot.id, spec_hash=req.spec_hash, provider_id=self.id,
            provider_profile_digest=profile_digest, capability=capability,
            duration_ms=req.duration_ms, candidates=req.candidates,
            seed=req.params.get("seed"), params=req.params,
            compiled_prompt=compiled_prompt, ref_refs=ref_refs,
            first_frame=first_frame, last_frame=last_frame,
            project_root=req.project.root)
        result = (identity, S.request_digest(identity))
        cache[self.id] = result
        return result

    def _keyframe_flags(self, req: GenerationRequest) -> tuple[bool, bool]:
        raw = getattr(req.shot, "keyframes", None) or []
        has_start = has_end = False
        for entry in raw:
            pos = getattr(entry, "position", None)
            if pos is None and isinstance(entry, dict):
                pos = entry.get("position")
            if pos == "start":
                has_start = True
            elif pos == "end":
                has_end = True
        return has_start, has_end

    def _idempotency_field(self):
        """The declared provider-native idempotency header name, or ``None``
        (ruling 10). Only a manifest that opts in with
        ``submission.idempotency.mode == header`` injects a key."""
        manifest = getattr(self, "manifest", None)
        sub = getattr(manifest, "submission", None)
        idem = getattr(sub, "idempotency", None) if sub is not None else None
        if idem is not None and getattr(idem, "mode", None) == "header" \
                and getattr(idem, "field", None):
            return idem.field
        return None

    def _prepare_submission(self, state, req: GenerationRequest) -> "_SubmissionContext":
        """Mint the submission, persist PREPARED (intent row + event), then the
        atomic DISPATCHING claim (+event) — the fail-closed gate (ruling 5): if
        either cannot be persisted, raise BEFORE the network so the paid submit
        never starts."""
        from . import submission as S
        from ..core.hashing import hash_text

        identity, digest = self._build_identity(req)
        submission_id = S.mint_submission_id()
        # the derived idempotency key rides a request ATTRIBUTE (never req.params,
        # which would leak into the take sidecar / ledger AND perturb the identity
        # digest) so submit() can inject it ONLY when the manifest declares an
        # idempotency header (ruling 10). Stable across re-dispatch of this id.
        req.submission_id = submission_id
        req.submission_idempotency_key = (
            S.idempotency_key(self.id, submission_id) if self._idempotency_field() else None)

        if state is None:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: cannot persist the PREPARED submission intent — the "
                f"runtime state is unavailable; refusing to start a paid submit "
                f"(DR06 fail-closed, ruling 5)",
                detail={"code": "submission_prepare_unavailable", "shot": req.shot.id},
            )
        try:
            params_hash = hash_text(str(sorted(req.params.items())))
        except Exception:
            params_hash = None
        try:
            intent_id = state.open_intent(
                provider=self.id, shot=req.shot.id, params_hash=params_hash,
                submission_id=submission_id, request_digest=digest, state=S.PREPARED)
        except (OSError, sqlite3.Error) as exc:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: failed to persist the PREPARED submission intent "
                f"({exc}); refusing to start a paid submit (DR06 fail-closed)",
                detail={"code": "submission_prepare_failed", "shot": req.shot.id},
            ) from exc

        sub = _SubmissionContext(self, state, req, submission_id=submission_id,
                                 request_digest=digest, current_state=S.PREPARED)
        sub._intent_id = intent_id
        # WP1: the PREPARED evidence MUST be durable before the paid submit — an
        # append failure here raises ProviderFailure(NOT_DISPATCHED) and the
        # transport is never reached.
        sub.emit(None, S.PREPARED, reason_code="prepared",
                 detail=self._gate_detail(req), required=True)
        # atomic DISPATCHING claim (SQLite CAS) then its event.
        try:
            claimed = state.claim_dispatching(submission_id)
        except (OSError, sqlite3.Error) as exc:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: failed to claim DISPATCHING ({exc}); refusing to "
                f"start a paid submit (DR06 fail-closed)",
                detail={"code": "submission_claim_failed", "shot": req.shot.id},
            ) from exc
        if not claimed:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: could not claim DISPATCHING for {submission_id} "
                f"(another process owns it, or it is not PREPARED) — refusing to "
                f"start a duplicate paid submit (DR06 fail-closed, SQLite CAS)",
                detail={"code": "submission_claim_lost", "shot": req.shot.id},
            )
        sub.current_state = S.DISPATCHING
        # WP1: the DISPATCHING evidence MUST be durable before the paid submit —
        # same fail-closed gate as PREPARED above.
        sub.emit(S.PREPARED, S.DISPATCHING, reason_code="claimed",
                 detail=self._gate_detail(req), required=True)
        return sub

    def _redispatch(self, sub: "_SubmissionContext") -> None:
        """Re-enter DISPATCHING for a retry (NOT_DISPATCHED/DEFINITELY_REJECTED)
        or an idempotent recovery — the SAME submission_id records a new dispatch
        attempt (ruling 10). Nothing was admitted, so this is not a resubmit of a
        billed job."""
        from . import submission as S

        if sub.current_state != S.DISPATCHING:
            # WP1: this DISPATCHING evidence precedes a paid submit exactly like
            # the first attempt's — same durable-admission gate (a failed append
            # refuses the retry's transport call; a recovered row ALREADY in
            # DISPATCHING re-submits under its earlier durable evidence).
            sub.transition(S.DISPATCHING, reason_code="redispatch", required=True)

    def _gate_detail(self, req: GenerationRequest) -> dict:
        """The ask_before/budget gate correlation recorded on the PREPARED /
        DISPATCHING event (ruling 5) — the existing gate results inline, no new
        SpendAuthorizationRef schema (SKIPPED_WITH_EVIDENCE). The ask_before/
        budget gate lives UPSTREAM in build.graph (run_build); reaching a paid
        submit at all means it cleared, so we record that correlation + the
        per-shot estimate threaded on the request (assume_yes is not re-plumbed
        to the provider layer — that would need a graph.py touch beyond the
        file cap)."""
        return {"estimated_cost": getattr(req, "estimated_cost", None),
                "gate": "cleared_upstream"}

    def _classify_submit_failure(self, sub: "_SubmissionContext",
                                 exc: ProviderFailure) -> None:
        """Drive the submission state off a submit()-phase ProviderFailure's
        disposition (ruling 5/6). An explicit OUTCOME_UNKNOWN -> OUTCOME_UNKNOWN
        (fail-closed). NOT_DISPATCHED -> REJECTED_PRE_DISPATCH (retry allowed).
        DEFINITELY_REJECTED -> REMOTE_REJECTED (fallback allowed). A ``None``
        disposition (a provider that never classified) is the legacy case:
        inferred conservatively from the kind for the RECORD, but it never
        triggers the fail-closed path (the retry gate stays kind-based)."""
        from . import submission as S

        disp = exc.disposition
        if disp is None:
            # WP2 NOTE: on the SUBMIT path this branch is now DEAD — the choke
            # point in generate() defaults an unclassified submit-phase failure to
            # OUTCOME_UNKNOWN before calling here, so ``exc.disposition`` is always
            # set. Kept tolerant for any other (non-submit) caller / future reuse.
            # legacy inference for the projection only (never fail-closed here):
            # a local pre-check refusal is not-dispatched; a definite remote kind
            # is remote-rejected; anything else stays not-dispatched (safe).
            if exc.kind in (FailureKind.content_rejected, FailureKind.rate_limited):
                target = S.REMOTE_REJECTED
            else:
                target = S.REJECTED_PRE_DISPATCH
        else:
            target = S.disposition_to_state(disp)
        try:
            if sub.current_state in S.LEGAL_TRANSITIONS and target in \
                    S.LEGAL_TRANSITIONS.get(sub.current_state, frozenset()):
                sub.transition(target, reason_code=exc.kind.value)
            else:
                # already past DISPATCHING or an illegal edge: record via event
                # only, never crash the paid path on bookkeeping.
                sub.emit(sub.current_state, target, reason_code=exc.kind.value)
                sub.current_state = target
        except Exception:
            pass

    def _finalize_failed_submission(self, sub: "_SubmissionContext | None",
                                    exc: ProviderFailure) -> None:
        if sub is None:
            return
        self._surface_submission_warnings(sub.req, sub)

    def _surface_submission_warnings(self, req: GenerationRequest,
                                     sub: "_SubmissionContext | None") -> None:
        """Fold any submission-evidence-append warnings onto the run evidence so
        they surface (never silent), mirroring the DR03C append-fail handling."""
        if sub is None or not sub.warnings:
            return
        ev = getattr(req, "evidence", None)
        note = getattr(ev, "note_warning", None)
        if callable(note):
            for w in sub.warnings:
                note(w)

    def _resolve_resume(self, state, req: GenerationRequest):
        """Strict resume correlation (ruling 8). Returns ``(action, job_id,
        sub)``: ``("fresh", None, None)`` to mint a new submission;
        ``("resume", job_id, sub)`` to re-poll an ADMITTED job under its
        submission_id; ``("redispatch", None, sub)`` to re-dispatch an idempotent
        submission. Raises a structured fail-closed diagnostic on an ambiguous
        (DISPATCHING/OUTCOME_UNKNOWN) submission that cannot be safely resumed."""
        from . import submission as S

        if state is None:
            # downstream _prepare_submission fail-closes on state None BEFORE any
            # transport (ruling 5) — pinned, so "fresh" here can never spend.
            return ("fresh", None, None)
        # P0 WP3 §5.1: the consult MUST NOT degrade to "fresh" when it cannot
        # actually verify prior submissions — an identity/state-query/evidence
        # failure here used to silently skip the correlation and fresh-submit
        # past an in-flight ADMITTED/DISPATCHING job (double-charge risk).
        try:
            _identity, digest = self._build_identity(req)
        except ProviderFailure:
            # P0 WP3 §5.1: an identity-unavailable failure is ALREADY fail-closed
            # and provably pre-send (NOT_DISPATCHED) — propagate it verbatim,
            # never remask it as recovery_unavailable (OUTCOME_UNKNOWN).
            raise
        except Exception as exc:
            raise self._recovery_unavailable(req, "identity", exc) from exc
        # POST_COMPLETION WP1: fold append-only submission EVIDENCE into the
        # projection BEFORE the query, so a fresh/empty state.sqlite is never
        # read as "nothing in flight" while events.jsonl holds an unresolved
        # chain (the double-charge gap). Same helper the release gate consumes.
        try:
            proj = state.ensure_submission_projection(
                req.project, shot=req.shot.id, provider=self.id)
        except Exception as exc:
            raise self._recovery_unavailable(req, "evidence_projection", exc) from exc
        if proj.get("status") == "recovery_unavailable":
            raise self._recovery_unavailable(
                req, proj.get("stage") or "evidence_projection",
                RuntimeError(proj.get("error") or "submission evidence unavailable"))
        try:
            subs = state.submissions(shot=req.shot.id, provider=self.id,
                                     states=tuple(sorted(S.UNRESOLVED_STATES)))
        except Exception as exc:
            raise self._recovery_unavailable(req, "state_query", exc) from exc

        # a broken per-submission event chain fail-closes ONLY this shot+provider.
        for row in subs:
            self._guard_chain_integrity(req, row)

        # 1) an ADMITTED submission is resume-safe (polling, never resubmit).
        admitted = [r for r in subs if r.get("state") == S.ADMITTED]
        for row in admitted:
            if digest is not None and row.get("request_digest") == digest \
                    and row.get("remote_job_id"):
                sub = self._context_from_row(state, req, row, digest)
                return ("resume", row["remote_job_id"], sub)
        if admitted and digest is not None:
            # an ADMITTED job exists but the spec changed under it (digest
            # mismatch): conflict — never silently poll the old task (test 45/46).
            raise self._conflict_failure(req, admitted[0], digest)

        # 2) a DISPATCHING/OUTCOME_UNKNOWN/RECOVERY_EVIDENCE_CORRUPT submission
        #    is side-effect ambiguous.
        ambiguous = [r for r in subs if r.get("state") in S.SIDE_EFFECT_AMBIGUOUS]
        if ambiguous:
            row = ambiguous[0]
            if row.get("state") != S.RECOVERY_EVIDENCE_CORRUPT \
                    and digest is not None \
                    and row.get("request_digest") == digest \
                    and self._idempotency_field():
                # ruling 10: a DECLARED idempotent provider MAY re-dispatch the
                # SAME submission_id (the derived key dedupes remotely). NEVER
                # off the WP3 sentinel — corrupt evidence cannot prove the
                # prior dispatch used the same key.
                sub = self._context_from_row(state, req, row, digest)
                req.submission_id = row["submission_id"]
                req.submission_idempotency_key = S.idempotency_key(
                    self.id, row["submission_id"])
                return ("redispatch", None, sub)
            raise self._unknown_outcome_failure(req, row)

        # 3) no DR06 submission: consult the legacy pending-job table. A legacy
        #    row is auto-resumed poll-only ONLY when it correlates to THIS
        #    request's digest; an uncorrelated row fail-closes for a human
        #    decision (P0 WP3 §5.2) rather than resuming on shot+provider alone.
        legacy = self._resume_job_id(state, req, digest)
        return ("resume", legacy, None) if legacy else ("fresh", None, None)

    def _context_from_row(self, state, req, row, digest):
        from . import submission as S

        sub = _SubmissionContext(
            self, state, req, submission_id=row["submission_id"],
            request_digest=row.get("request_digest") or digest or "",
            current_state=S.normalize_state(row.get("state")),
            prev_event_digest=self._chain_tail_digest(req, row["submission_id"]))
        sub._intent_id = row.get("id")
        sub.remote_job_id = row.get("remote_job_id")
        return sub

    def _chain_tail_digest(self, req, submission_id):
        try:
            from ..build.attempts import read_submission_events
            from .submission import submission_event_digest

            events, _ = read_submission_events(req.project, submission_id)
            return submission_event_digest(events[-1]) if events else None
        except Exception:
            return None

    def _identity_unavailable(self, req, stage: str, exc: BaseException) -> ProviderFailure:
        """P0 WP3 §5.1 — the structured fail-closed failure for a PAID submit
        whose request identity could not be fully assembled (compiled prompt /
        ref resolution / local-ref hash). ``NOT_DISPATCHED``: nothing was sent
        (the identity is built strictly BEFORE the transport), so this is
        provably pre-send — transport 0. Distinct from a consult that could not
        LOOK (``submission_recovery_unavailable``): here we know we did not
        spend."""
        from . import submission as S

        return ProviderFailure(
            FailureKind.provider_error,
            f"{self.id}: cannot assemble the submission identity for shot "
            f"{req.shot.id} — {stage} unavailable ({type(exc).__name__}); "
            f"refusing to send a paid submit with an incomplete request identity "
            f"(P0 WP3 §5.1 fail-closed)",
            detail={"code": "submission_identity_unavailable", "stage": stage,
                    "shot": req.shot.id, "automatic_resubmit": False},
            disposition=S.NOT_DISPATCHED,
        )

    def _recovery_unavailable(self, req, stage: str, exc: BaseException) -> ProviderFailure:
        """P0 WP3 §5.1 — the structured fail-closed failure for a CONSULT that
        could not run (identity / state query / evidence read / chain verify
        raised). Distinct from a consult that RAN and found an ambiguous or
        conflicting row (submission_outcome_unknown / submission_spec_conflict):
        here we could not even look, so a fresh paid submit must not start."""
        from . import submission as S

        return ProviderFailure(
            FailureKind.provider_error,
            f"{self.id}: cannot verify shot {req.shot.id}'s prior submissions — "
            f"the recovery consult failed at {stage} ({type(exc).__name__}); "
            f"refusing a fresh paid submit while earlier outcomes are "
            f"unverifiable (P0 WP3 fail-closed)",
            detail={"code": "submission_recovery_unavailable", "stage": stage,
                    "shot": req.shot.id, "automatic_resubmit": False,
                    "possible_remote_side_effect": True},
            disposition=S.OUTCOME_UNKNOWN_DISPOSITION,
        )

    def _guard_chain_integrity(self, req, row) -> None:
        """A broken per-submission event chain -> RECOVERY_EVIDENCE_CORRUPT that
        fail-closes ONLY this shot+provider (ruling 4), never unrelated work.
        P0 WP3 §5.1: a FAILURE to read or verify the evidence (as opposed to a
        verified-corrupt result) is submission_recovery_unavailable — it used to
        silently pass the guard."""
        from ..build.attempts import read_submission_events
        from . import submission as S

        sid = row.get("submission_id")
        if not sid:
            return
        try:
            events, malformed = read_submission_events(req.project, sid)
        except Exception as exc:
            raise self._recovery_unavailable(req, "evidence_read", exc) from exc
        # FINAL_ACCEPTANCE F1: the malformed count is STREAM-GLOBAL — a torn
        # line has no parseable submission_id, so the per-sid filter cannot
        # exclude it and it could belong to ANY submission (this one included).
        # A stream that provably lost a line cannot vouch for any chain read
        # from it: fail closed, never resume/poll/submit past it.
        if malformed:
            raise self._recovery_unavailable(
                req, "evidence_malformed",
                RuntimeError(f"{malformed} torn line(s) in the evidence stream"))
        try:
            ok, broken_at = S.verify_chain(events)
        except Exception as exc:
            raise self._recovery_unavailable(req, "chain_verify", exc) from exc
        if not ok:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: submission {sid} for shot {req.shot.id} has a "
                f"corrupt evidence chain (broken at event {broken_at}) — "
                f"fail-closing this shot+provider only (DR06 ruling 4); resolve "
                f"with `manju tasks attach-remote-job`/`abandon`",
                detail={"code": "RECOVERY_EVIDENCE_CORRUPT", "submission_id": sid,
                        "shot": req.shot.id, "broken_at": broken_at,
                        "automatic_resubmit": False},
                disposition=S.OUTCOME_UNKNOWN_DISPOSITION,
            )

    def _unknown_outcome_failure(self, req, row) -> ProviderFailure:
        from . import submission as S

        return ProviderFailure(
            FailureKind.provider_error,
            f"{self.id}: shot {req.shot.id} has an unresolved submission "
            f"{row.get('submission_id')} in state {row.get('state')} — its remote "
            f"outcome is UNKNOWN and must not be auto-resubmitted (DR06 ruling 8). "
            f"Attach the remote job id or abandon it with duplicate-risk accepted.",
            detail={"code": "submission_outcome_unknown",
                    "submission_id": row.get("submission_id"),
                    "shot": req.shot.id, "state": row.get("state"),
                    "automatic_resubmit": False, "possible_remote_side_effect": True,
                    "actions": ["attach_remote_job", "abandon_with_duplicate_risk"]},
            disposition=S.OUTCOME_UNKNOWN_DISPOSITION,
        )

    def _conflict_failure(self, req, row, digest) -> ProviderFailure:
        from . import submission as S

        return ProviderFailure(
            FailureKind.provider_error,
            f"{self.id}: shot {req.shot.id} has an ADMITTED submission "
            f"{row.get('submission_id')} still in flight, but the request spec "
            f"changed under it (digest mismatch) — refusing to silently poll the "
            f"old task (DR06 ruling 8).",
            detail={"code": "submission_spec_conflict",
                    "submission_id": row.get("submission_id"), "shot": req.shot.id,
                    "pending_digest": row.get("request_digest"),
                    "request_digest": digest, "automatic_resubmit": False,
                    "actions": ["attach_remote_job", "abandon_with_duplicate_risk"]},
            disposition=S.OUTCOME_UNKNOWN_DISPOSITION,
        )

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

    def _resume_job_id(self, state, req: GenerationRequest,
                       digest: str | None = None) -> str | None:
        if state is None:
            return None
        try:
            pending = state.pending_jobs(shot=req.shot.id, provider=self.id)
        except (OSError, sqlite3.Error):
            return None
        if not pending:
            return None
        # P0 WP3 §5.2: shot+provider alone can no longer authorize a resume — a
        # pending row may belong to an entirely DIFFERENT request. Auto-resume
        # poll-only ONLY when the row carries a request digest that CORRELATES to
        # the current identity. Pre-DR06 `jobs` rows carry no digest, so they now
        # require a human attach/abandon decision (this deliberately tightens the
        # old DR06 characterization that matched on shot+provider only).
        job = pending[0]
        row_digest = job.get("request_digest")
        if digest is not None and row_digest and row_digest == digest:
            job_id = job.get("remote_job_id")
            if self._logger is not None and job_id:
                self._logger(
                    f"{self.id}: resuming poll on correlated pending job {job_id} "
                    f"for shot {req.shot.id} — no resubmit, no double-charge (§8.1)"
                )
            return job_id
        raise self._legacy_pending_unknown(req, job)

    def _legacy_pending_unknown(self, req, job) -> ProviderFailure:
        """P0 WP3 §5.2 — a legacy pending job that cannot be correlated to the
        current request is side-effect ambiguous: its remote output may be a
        different request's, so it is neither auto-resumed nor auto-resubmitted.
        A human attaches it (poll-only ADMITTED) or abandons it with the
        duplicate-charge risk accepted."""
        from . import submission as S

        return ProviderFailure(
            FailureKind.provider_error,
            f"{self.id}: shot {req.shot.id} has a legacy pending job "
            f"{job.get('remote_job_id')} with no correlatable request identity — "
            f"resuming it on shot+provider alone could poll a DIFFERENT request's "
            f"output; refusing (P0 WP3 §5.2). Attach the remote job id or abandon "
            f"it with duplicate-risk accepted.",
            detail={"code": "LEGACY_PENDING_CORRELATION_UNKNOWN",
                    "remote_job_id": job.get("remote_job_id"),
                    "shot": req.shot.id, "automatic_resubmit": False,
                    "possible_remote_side_effect": True,
                    "actions": ["attach_remote_job", "abandon_with_duplicate_risk"]},
            disposition=S.OUTCOME_UNKNOWN_DISPOSITION,
        )

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
                attempt_id=getattr(req, "evidence_attempt_id", None),
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
                attempt_id=getattr(req, "evidence_attempt_id", None),
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
