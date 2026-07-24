"""DR03C — the single run-evidence stream + the derived RunManifest.

Architecture ruling (Variant A): ``events.jsonl`` IS the attempt stream. Every
generation/build attempt is ONE versioned event line
(``action="stage_attempt"``, whose ``detail`` carries a
``manju.stage-attempt-evidence/v1`` document). The SQLite ``runs`` ledger, the
``tasks``/``failures`` views and the RunManifest are all PROJECTIONS or
cross-references of this one stream — there is no ``trace.json``, no receipts
directory, no second database, no parallel file store.

This module OWNS everything for that stream: the schema constants + states, the
locked emission helper (:func:`append_attempt`), the systematic redaction, the
per-run context object (:class:`RunEvidence` + :class:`AttemptHandle`), the
events projection reader (:func:`read_attempts`) and the RunManifest
materializer (:func:`materialize_run_manifest`). Everything else in the codebase
only CALLS into here.

Design decisions recorded in code (see the contract):

* **One event per attempt (v1).** A terminal call emits exactly ONE event via a
  single locked ``write()``; there are no begin/end pairs. ``started_at`` /
  ``ended_at`` / ``duration_ms`` are fields of that single record. This keeps the
  stream simple and loss-proof; the ``PLANNED`` / ``RUNNING`` states are reserved
  for a future streaming emission and are never written in v1.
* **Locked write.** Attempt payloads (with inputs/outputs/request) routinely
  exceed ``PIPE_BUF``, so a bare append-mode ``write()`` is no longer atomic. We
  serialize every attempt append behind an ``events.lock`` flock — the exact
  rotate-then-append pattern ``core/failures.py`` uses for ``failures.lock`` — so
  concurrent writers (the generation ThreadPoolExecutor, a second process) never
  interleave or tear a line. WP1: that flock + the durable write are now the ONE
  coordinator ``core.events.append_jsonl_line``, and EVERY writer — the attempt
  stream, the submission stream, AND plain ``append_event`` — routes through it
  under the same lock (a lock-timeout / no-fcntl platform DROPS a best-effort
  record rather than ever writing it unlocked).
* **Sequence bound.** ``sequence`` is a per-run monotonic int from a process-local
  ``itertools.count`` guarded by a ``threading.Lock``. This is sufficient because
  a build is single-process (``build_lock`` serializes builds) and its generation
  concurrency is an in-process ``ThreadPoolExecutor`` — every worker shares the
  one :class:`RunEvidence`. It is NOT a cross-process sequence; two independent
  processes writing attempts for the SAME run id (never produced by the build)
  could collide on ``sequence`` — attempt_id (a uuid) stays globally unique
  regardless, and the projection sorts by ``(sequence, attempt_id)``.
"""

from __future__ import annotations

import itertools
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core import events as _events_core
from ..core.events import EvidenceWriteError
from ..core.hashing import hash_file, hash_value

# --------------------------------------------------------------- schema/states

SCHEMA = "manju.stage-attempt-evidence/v1"
MANIFEST_SCHEMA = "manju.run-manifest/v1"

# The event action every attempt line carries at the envelope level (the
# collaboration-log tail reads it like any other action).
ACTION = "stage_attempt"

EVENTS_FILE = "events.jsonl"
EVENTS_LOCK = "events.lock"

# The 11 attempt states (contract §… ). NEVER a `done` boolean.
PLANNED = "PLANNED"
SUBMITTED = "SUBMITTED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
SKIPPED_CACHE_HIT = "SKIPPED_CACHE_HIT"
FAILED = "FAILED"
CANCELED = "CANCELED"
WAITING_USER = "WAITING_USER"
REJECTED_PRECHECK = "REJECTED_PRECHECK"
ABANDONED = "ABANDONED"
UNKNOWN_LEGACY = "UNKNOWN_LEGACY"

STATES = (
    PLANNED, SUBMITTED, RUNNING, SUCCEEDED, SKIPPED_CACHE_HIT, FAILED,
    CANCELED, WAITING_USER, REJECTED_PRECHECK, ABANDONED, UNKNOWN_LEGACY,
)
_STATES = frozenset(STATES)

# States that a terminal (single-event) emission may carry in v1.
TERMINAL_STATES = frozenset(
    {SUCCEEDED, SKIPPED_CACHE_HIT, FAILED, CANCELED, WAITING_USER,
     REJECTED_PRECHECK, ABANDONED}
)

# RunManifest terminal_status values.
COMPLETED = "COMPLETED"
COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
MANIFEST_FAILED = "FAILED"
MANIFEST_CANCELED = "CANCELED"
MANIFEST_WAITING_USER = "WAITING_USER"
# P0 WP4: the two honest non-terminal statuses. INCOMPLETE = run_started with
# no run_terminal (an interrupted/killed run is never claimed COMPLETED);
# NOT_FOUND = no events at all for the run_id.
MANIFEST_INCOMPLETE = "INCOMPLETE"
MANIFEST_NOT_FOUND = "NOT_FOUND"

# The semantic_digest is a fingerprint of an attempt's MEANING. It deliberately
# EXCLUDES incidentals that vary run-to-run without changing what happened: the
# wall-clock stamps, the duration, the digest field itself, the redaction log,
# and the human-facing report refs. Absolute paths never appear (every path we
# store is already project-relative) and UI text (a redacted human message) is
# dropped from the nested `failure`/`request` blocks below.
_DIGEST_EXCLUDE_TOP = frozenset(
    {"ts", "started_at", "ended_at", "duration_ms", "semantic_digest",
     "redactions", "evidence_refs"}
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _root(project: Any) -> Path:
    """Accept a Project (has ``.root``) or a raw path (mirrors failures._root)."""
    if isinstance(project, (str, Path)):
        return Path(project)
    return Path(getattr(project, "root", project))


# ------------------------------------------------------------------ the lock
# WP1: the ONE append coordinator (the single flock + the durable write) now
# lives in ``core.events`` — the core layer must not import build/*, and build/*
# imports core, so the primitive belongs there. Every writer in THIS module
# routes through ``core.events.append_jsonl_line``; ``_events_lock`` stays as a
# thin, best-effort delegate for any out-of-tree caller.


def _events_lock(project_root: Path, *, timeout_s: float | None = None):
    """Deprecated thin delegate to :func:`manju.core.events.events_lock` (WP1).

    Best-effort context manager: yields ``True`` when the events lock is held
    (safe to write) and ``False`` when it could not be acquired — in which case
    the caller MUST skip the write (WP1 never writes unlocked). Retained only so
    an external caller of the old name keeps working; internal writers use
    :func:`manju.core.events.append_jsonl_line` directly."""
    return _events_core.events_lock(project_root, timeout_s=timeout_s, required=False)


# ------------------------------------------------------------------ redaction

# Param/header keys whose VALUE is a credential — dropped wholesale (never a
# digest, the key is enough to know it was there).
_SECRET_KEY_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|apikey|secret|token|access[_-]?token|"
    r"auth[_-]?token|password|passwd|x-api-key|bearer|credential)"
)
# Prompt/bundle text keys — replaced by a digest (referenced, never carried).
_PROMPT_KEYS = frozenset({"prompt", "compiled_prompt", "negative_prompt", "text"})
_URL_QUERY_RE = re.compile(r"^(https?://[^?\s]+)\?\S*$")


def _import_secret_patterns() -> list:
    """The final secret backstop = ``core.check.SECRET_PATTERNS`` (one source of
    truth for what a leaked key looks like). Imported lazily/defensively so a
    refactor there can never break the evidence path."""
    try:
        from ..core.check import SECRET_PATTERNS

        return list(SECRET_PATTERNS)
    except Exception:
        return []


def _redact_scalar(value: str, applied: list[str]) -> str:
    m = _URL_QUERY_RE.match(value)
    if m:
        applied.append("stripped query string from signed url")
        return m.group(1) + "?<redacted>"
    return value


def _redact_obj(obj: Any, applied: list[str]) -> Any:
    """Recursively drop credential-ish keys, digest prompt text, strip signed-URL
    query strings. Returns a NEW structure; ``applied`` accrues one line per
    distinct redaction kind performed (deduped by the caller)."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = str(k)
            if _SECRET_KEY_RE.search(key):
                out[key] = "<redacted>"
                applied.append(f"dropped credential key: {key}")
                continue
            if key in _PROMPT_KEYS and isinstance(v, str):
                out[key] = {"digest": hash_value(v), "len": len(v)}
                applied.append(f"prompt text -> digest: {key}")
                continue
            out[key] = _redact_obj(v, applied)
        return out
    if isinstance(obj, list):
        return [_redact_obj(v, applied) for v in obj]
    if isinstance(obj, str):
        return _redact_scalar(obj, applied)
    return obj


def _scrub_secret_patterns(doc: dict, applied: list[str]) -> dict:
    """Final systematic scan: serialize the (already key-redacted) doc and, if
    any ``SECRET_PATTERNS`` match a residual token anywhere, blanket-replace and
    re-parse. Belt-and-braces over the explicit key policy above."""
    patterns = _import_secret_patterns()
    if not patterns:
        return doc
    try:
        blob = json.dumps(doc, ensure_ascii=False)
    except (TypeError, ValueError):
        return doc
    hit = False
    for pat in patterns:
        blob, n = pat.subn("<redacted:secret>", blob)
        if n:
            hit = True
    if not hit:
        return doc
    applied.append("secret-pattern backstop scrubbed a residual token")
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return doc


def redact_params(params: dict | None) -> tuple[dict, list[str]]:
    """Public helper for callers building a ``request`` block: return
    ``(redacted_params, applied)``. Credential keys dropped, prompt text
    digested, signed-URL queries stripped."""
    applied: list[str] = []
    red = _redact_obj(dict(params or {}), applied)
    return red, _dedup(applied)


def _dedup(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for it in items:
        seen.setdefault(it, None)
    return list(seen)


# --------------------------------------------------- input/output/request refs


def output_ref(role: str, *, path: str, sha256: str, bytes: int | None = None,
               content_key: str | None = None, asset_id: str | None = None,
               **extra: Any) -> dict:
    """One committed output item. ``path`` MUST already be project-relative."""
    ref: dict[str, Any] = {"role": role, "path": path, "sha256": sha256}
    if bytes is not None:
        ref["bytes"] = int(bytes)
    if content_key is not None:
        ref["content_key"] = content_key
    if asset_id is not None:
        ref["asset_id"] = asset_id
    ref.update(extra)
    return ref


def input_ref(role: str, *, path: str | None = None, sha256: str | None = None,
              bytes: int | None = None, digest: str | None = None,
              content_key: str | None = None, **extra: Any) -> dict:
    """One input item. A concrete file carries ``path``/``sha256``; a
    ref/prompt/bundle carries ``digest`` only (never full text)."""
    ref: dict[str, Any] = {"role": role}
    if digest is not None:
        ref["digest"] = digest
    if path is not None:
        ref["path"] = path
    if sha256 is not None:
        ref["sha256"] = sha256
    if bytes is not None:
        ref["bytes"] = int(bytes)
    if content_key is not None:
        ref["content_key"] = content_key
    ref.update(extra)
    return ref


def provider_request_evidence(params: dict | None, *, spec_hash: str | None,
                              duration_ms: int | None,
                              candidate_index: int | None = None,
                              seed: Any = None) -> dict:
    """Build the ``request`` block for a provider attempt: a ``request_digest``
    over the REDACTED params + spec_hash + duration (stable, secret-free), the
    candidate index, the seed when present, and a short ``redacted_summary`` of
    the surviving param keys. The compiled prompt is NEVER carried — only its
    digest survives (via :func:`redact_params`)."""
    red, applied = redact_params(params)
    digest = hash_value({"params": red, "spec_hash": spec_hash,
                         "duration_ms": duration_ms})
    req: dict[str, Any] = {"request_digest": digest}
    if candidate_index is not None:
        req["candidate_index"] = int(candidate_index)
    if seed is not None:
        req["seed"] = seed
    # a compact, human-scannable list of what survived redaction (keys only)
    req["redacted_summary"] = sorted(str(k) for k in red)
    if applied:
        req["redactions"] = applied
    return req


# --------------------------------------------------------- semantic_digest


def _semantic_payload(doc: dict) -> dict:
    """The subset of an evidence doc that the semantic_digest hashes: everything
    except the incidental top-level keys, and minus the two nested UI-text
    fields (a redacted human message; the request's human summary)."""
    payload = {k: v for k, v in doc.items() if k not in _DIGEST_EXCLUDE_TOP}
    failure = payload.get("failure")
    if isinstance(failure, dict) and "message" in failure:
        failure = {k: v for k, v in failure.items() if k != "message"}
        payload["failure"] = failure
    request = payload.get("request")
    if isinstance(request, dict) and "redacted_summary" in request:
        request = {k: v for k, v in request.items() if k != "redacted_summary"}
        payload["request"] = request
    return payload


def semantic_digest(doc: dict) -> str:
    return hash_value(_semantic_payload(doc))


# --------------------------------------------------------- the emission helper


def append_attempt(project: Any, payload: dict, *, actor: str = "engine",
                   required: bool = False) -> dict:
    """Normalize + redact + stamp + append ONE attempt event; return the written
    record. Default (``required=False``) is best-effort — NEVER raises out
    (log-and-degrade like ``append_event``'s callers): the evidence stream is
    best-effort, and — critically — an evidence-append failure AFTER a media
    commit must never delete media, only surface a warning (the caller turns a
    ``None``/empty return into a build warning). ``required=True`` propagates an
    :class:`EvidenceWriteError` when the durable write cannot be made (WP1) — used
    only where losing the evidence must fail the operation.

    The write is a single locked ``write()`` via the WP1 coordinator
    (``core.events.append_jsonl_line``); ALL redaction stays HERE, before it."""
    try:
        doc = dict(payload)
        doc.setdefault("schema", SCHEMA)
        # normalize state (unknown -> UNKNOWN_LEGACY, never invented)
        state = doc.get("state")
        if state not in _STATES:
            doc["state"] = UNKNOWN_LEGACY
        doc["ts"] = _now_iso()

        # systematic redaction over the whole doc, then the secret backstop.
        applied: list[str] = list(doc.get("redactions") or [])
        doc = _redact_obj(doc, applied)
        doc = _scrub_secret_patterns(doc, applied)
        if applied:
            doc["redactions"] = _dedup(applied)

        # stamp the semantic digest LAST (over the redacted, stamped doc).
        doc["semantic_digest"] = semantic_digest(doc)

        record = {"ts": doc["ts"], "actor": actor, "action": ACTION, "detail": doc}
        # ONE durable, flock-serialized write via the single coordinator so a
        # >PIPE_BUF line never interleaves with another attempt writer.
        ok = _events_core.append_jsonl_line(_root(project), record,
                                            durable=True, required=required)
        return record if ok else {}
    except EvidenceWriteError:
        raise  # required-mode durable failure — never swallowed (WP1)
    except Exception:
        # best-effort: never propagate. The caller (RunEvidence) treats a
        # falsy return as "evidence append failed" and warns — never deletes
        # the media that was already committed (test 18).
        return {}


# -------------------------------------------------- DR06 submission events
# Submission lifecycle transitions ride THIS SAME events.jsonl (ruling 1): one
# event line, action "submission_state", schema
# manju.provider-submission-event/v1, carrying a per-submission hash chain
# (prev_event_digest) for corruption detection. They reuse the SAME flock
# (_events_lock) and the SAME redaction backstop — no new ledger, no new lock,
# no new file. The chain math + field contract live in providers.submission
# (pure); this is only the locked write + the projection read.

SUBMISSION_ACTION = "submission_state"


def append_submission_event(
    project: Any, *, submission_id: str, request_digest: str | None,
    from_state: str | None, to_state: str, provider_id: str,
    shot: str | None = None, remote_job_id: str | None = None,
    reason_code: str | None = None, prev_event_digest: str | None = None,
    detail: dict | None = None, actor: str = "engine", required: bool = False,
) -> dict:
    """Append ONE ``submission_state`` event via the SAME WP1 coordinator
    (one ``events.lock`` flock, one ``events.jsonl``). Mints ``event_id``, sets
    ``prev_event_digest`` (the caller threads it forward — in-process from the
    previous append's returned event, cross-process from the verified chain
    tail), redacts ONLY the free-form ``detail`` (the chain fields are controlled
    and stay intact so the digest is stable), writes, and returns the written
    event document (whose :func:`providers.submission.submission_event_digest` is
    the NEXT event's ``prev_event_digest``).

    Default (``required=False``) is best-effort — NEVER raises out; a caller turns
    an empty return into a warning. ``required=True`` (the PREPARED / DISPATCHING
    admission gate) propagates an :class:`EvidenceWriteError` when the durable
    write cannot be made, so the paid submit refuses to start (WP1)."""
    from ..providers.submission import SCHEMA_EVENT

    try:
        applied: list[str] = []
        red_detail = _redact_obj(dict(detail or {}), applied) if detail else None
        event: dict[str, Any] = {
            "schema": SCHEMA_EVENT,
            "submission_id": submission_id,
            "request_digest": request_digest,
            "from": from_state,
            "to": to_state,
            "provider_id": provider_id,
            "remote_job_id": remote_job_id,   # non-secret job id (§8.6)
            "reason_code": reason_code,
            "event_id": "evt_" + uuid4().hex[:12],
            "prev_event_digest": prev_event_digest,
        }
        if shot is not None:
            event["shot"] = shot           # projection aid (not part of the chain)
        if red_detail:
            event["detail"] = red_detail
        record = {"ts": _now_iso(), "actor": actor, "action": SUBMISSION_ACTION,
                  "detail": event}
        # secret backstop over the whole line (chain fields are controlled and
        # never match, so the digest stays stable) — belt-and-braces.
        record = _scrub_secret_patterns(record, applied)
        # ONE durable, flock-serialized write via the single coordinator.
        ok = _events_core.append_jsonl_line(_root(project), record,
                                            durable=True, required=required)
        return record["detail"] if ok else {}
    except EvidenceWriteError:
        raise  # required-mode durable failure — never swallowed (WP1)
    except Exception as exc:
        if required:
            # a build/redaction failure in required mode is still an evidence
            # failure the admission gate must see — surface it, don't drop it.
            raise EvidenceWriteError("io_error") from exc
        return {}


def _decode_jsonl_line(raw_bytes: bytes) -> str | None:
    """One JSONL ledger line decoded from BYTES, or None when the bytes are not
    valid UTF-8 — a write torn mid-multibyte-char at EOF (a crash during an
    append, the acknowledged torn-tail source; CJK prompts/shots on the primary
    Windows platform make this real). The strict text-mode ``for raw in f`` used
    to decode eagerly and raise UnicodeDecodeError out of the whole reader,
    bricking the projection; the callers count a None as a malformed line
    exactly as they already count a JSONDecodeError, mirroring the hardened
    ``core.events`` line decode."""
    try:
        return raw_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None


def read_submission_events(project: Any, submission_id: str | None = None
                           ) -> tuple[list[dict], int]:
    """Project the submission-state events out of ``events.jsonl`` (ruling 1/4).

    Returns ``(records, malformed)`` in FILE order — which IS emission order (the
    append is serialized under the events flock), so it is the exact order the
    per-submission hash chain assumes and :func:`providers.submission.verify_chain`
    validates. Deliberately NOT re-sorted: a ts/event_id sort would scramble
    same-second events and defeat corruption detection. Optionally filtered to one
    ``submission_id``; ``malformed`` counts torn lines. Mirrors
    :func:`read_attempts` (minus the explicit sequence sort attempts need)."""
    path = _root(project) / EVENTS_FILE
    records: list[dict] = []
    malformed = 0
    if not path.exists():
        return records, malformed
    with open(path, "rb") as f:
        for raw_bytes in f:
            raw = _decode_jsonl_line(raw_bytes)
            if raw is None:
                malformed += 1  # torn mid-multibyte write — count, never crash
                continue
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(rec, dict) or rec.get("action") != SUBMISSION_ACTION:
                continue
            detail = rec.get("detail")
            if not isinstance(detail, dict):
                continue
            if submission_id is not None and detail.get("submission_id") != submission_id:
                continue
            # carry the envelope ts onto the detail so rebuild has a timestamp
            detail = {**detail, "ts": rec.get("ts") or detail.get("ts")}
            records.append(detail)
    return records, malformed


def submission_chains(project: Any) -> dict[str, list[dict]]:
    """Every submission's event list, grouped by ``submission_id`` in chain
    order — the input to a per-submission
    :func:`providers.submission.verify_chain` (recovery / corruption scoping)."""
    records, _ = read_submission_events(project)
    chains: dict[str, list[dict]] = {}
    for rec in records:
        sid = rec.get("submission_id")
        if sid:
            chains.setdefault(sid, []).append(rec)
    return chains


# -------------------------------------------------- P0 WP4 run-lifecycle events
# Additive event kinds on the SAME events.jsonl through the A1 coordinator:
# run_started (run_build's real start), run_terminal (EVERY run_build exit),
# attempt_started (BEFORE the two expensive attempt families only — provider
# generation and the final render). All BEST-EFFORT: run telemetry must never
# block or crash a build (only paid admission evidence is required=True). The
# existing terminal ``stage_attempt`` event IS the attempt terminal — it is not
# renamed and never dual-written.

LIFECYCLE_SCHEMA = "manju.run-lifecycle/v1"
RUN_STARTED_ACTION = "run_started"
RUN_TERMINAL_ACTION = "run_terminal"
ATTEMPT_STARTED_ACTION = "attempt_started"
# run_terminal.status vocabulary (lowercase on the event; the manifest maps it
# onto the MANIFEST_* constants verbatim, plus the warnings promotion).
RUN_TERMINAL_STATUSES = ("completed", "completed_with_warnings", "failed",
                         "canceled", "waiting_user")


def _append_lifecycle(project: Any, action: str, detail: dict, *,
                      actor: str = "engine") -> bool:
    """ONE best-effort, durable lifecycle append via the WP1 coordinator —
    exactly one lock acquisition, never raises (telemetry must never block or
    crash a build)."""
    try:
        record = {"ts": _now_iso(), "actor": actor, "action": action,
                  "detail": {"schema": LIFECYCLE_SCHEMA, **detail}}
        return _events_core.append_jsonl_line(_root(project), record,
                                              durable=True, required=False)
    except Exception:
        return False


def append_run_started(project: Any, run_id: str, *, target: str | None = None,
                       gen: str | None = None, actor: str = "engine") -> bool:
    """run_build's real start (after the build lock, at the run_id mint).
    Carries run_id + target/gen only — never a params dump."""
    detail: dict[str, Any] = {"run_id": run_id}
    if target is not None:
        detail["target"] = target
    if gen is not None:
        detail["gen"] = gen
    return _append_lifecycle(project, RUN_STARTED_ACTION, detail, actor=actor)


def append_run_terminal(project: Any, run_id: str, *, status: str,
                        counts: dict | None = None, actor: str = "engine") -> bool:
    """One run_terminal per run_build exit (success/failure/canceled/refusal).
    An unknown status collapses to ``failed`` (conservative, never invented into
    a success). A hard KILL emits nothing — exactly what INCOMPLETE detects."""
    detail: dict[str, Any] = {
        "run_id": run_id,
        "status": status if status in RUN_TERMINAL_STATUSES else "failed",
    }
    if counts:
        detail["counts"] = dict(counts)
    return _append_lifecycle(project, RUN_TERMINAL_ACTION, detail, actor=actor)


def append_attempt_started(project: Any, run_id: str, attempt_id: str, *,
                           stage: str, provider: str | None = None,
                           actor: str = "engine") -> bool:
    """BEFORE one expensive attempt (provider generation / final render). The
    ``attempt_id`` correlates to the pre-minted handle whose terminal
    ``stage_attempt`` event lands later; a started attempt with no terminal is
    a dangling attempt on an INCOMPLETE run."""
    detail: dict[str, Any] = {"run_id": run_id, "attempt_id": attempt_id,
                              "stage": stage}
    if provider:
        detail["provider"] = provider
    return _append_lifecycle(project, ATTEMPT_STARTED_ACTION, detail, actor=actor)


def read_run_lifecycle(project: Any, run_id: str) -> dict[str, list[dict]]:
    """Project ONE run's lifecycle events out of events.jsonl in file order:
    ``{"started": [...], "terminals": [...], "attempt_started": [...]}``. Torn
    lines are skipped exactly like every other reader on this stream."""
    path = _root(project) / EVENTS_FILE
    out: dict[str, list[dict]] = {"started": [], "terminals": [],
                                  "attempt_started": []}
    if not path.exists():
        return out
    keys = {RUN_STARTED_ACTION: "started", RUN_TERMINAL_ACTION: "terminals",
            ATTEMPT_STARTED_ACTION: "attempt_started"}
    with open(path, "rb") as f:
        for raw_bytes in f:
            raw = _decode_jsonl_line(raw_bytes)
            if not raw:  # None (torn mid-multibyte) or blank — skip, never crash
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            key = keys.get(rec.get("action") or "")
            if key is None:
                continue
            detail = rec.get("detail")
            if not isinstance(detail, dict) or detail.get("run_id") != run_id:
                continue
            out[key].append({**detail, "ts": rec.get("ts")})
    return out


# ---------------------------------------------------------- the run context


class AttemptHandle:
    """One attempt. Created BEFORE the work runs (so its ``attempt_id`` can
    parent a later retry), terminated by exactly one of the terminal methods
    below — which emits the single event. A handle whose work was abandoned
    (e.g. a ProviderCanceled unwinding past it) is simply never terminated and
    emits nothing (honest: an attempt with no terminal event never happened on
    the stream)."""

    def __init__(self, run: "RunEvidence", *, stage: str, unit: dict,
                 action: str, parent_attempt_id: str | None = None,
                 fallback_root_attempt_id: str | None = None,
                 fallback_index: int | None = None,
                 executor: dict | None = None, request: dict | None = None,
                 inputs: list | None = None, started_at: str | None = None):
        self.run = run
        self.project = run.project
        self.stage = stage
        self.unit = dict(unit or {})
        self.action = action
        self.attempt_id = "att_" + uuid4().hex[:12]
        self.parent_attempt_id = parent_attempt_id
        self.fallback_root_attempt_id = fallback_root_attempt_id
        self.fallback_index = fallback_index
        self.executor = executor
        self.request = request
        self.inputs = inputs
        self._started_at = started_at or _now_iso()
        self._t0 = time.monotonic()
        self._emitted = False

    # -- terminal emitters (each emits ONE event) --------------------------

    def succeeded(self, *, outputs: list | None = None, cost: dict | None = None,
                  decision: dict | None = None, request: dict | None = None,
                  executor: dict | None = None,
                  evidence_refs: list | None = None) -> dict:
        return self._emit(SUCCEEDED, outputs=outputs, cost=cost,
                          decision=decision, request=request, executor=executor,
                          evidence_refs=evidence_refs)

    def skipped_cache_hit(self, *, outputs: list | None = None,
                          decision: dict | None = None) -> dict:
        return self._emit(SKIPPED_CACHE_HIT, outputs=outputs, decision=decision)

    def failed(self, *, failure: dict | None = None, decision: dict | None = None,
               cost: dict | None = None) -> dict:
        return self._emit(FAILED, failure=failure, decision=decision, cost=cost)

    def rejected_precheck(self, *, failure: dict | None = None) -> dict:
        return self._emit(REJECTED_PRECHECK, failure=failure)

    def canceled(self, *, failure: dict | None = None,
                 decision: dict | None = None, outputs: list | None = None) -> dict:
        return self._emit(CANCELED, failure=failure, decision=decision,
                          outputs=outputs)

    def waiting_user(self, *, decision: dict | None = None,
                     cost: dict | None = None) -> dict:
        return self._emit(WAITING_USER, decision=decision, cost=cost)

    def abandoned(self, *, failure: dict | None = None) -> dict:
        return self._emit(ABANDONED, failure=failure)

    # -- the single write --------------------------------------------------

    def _emit(self, state: str, **extra: Any) -> dict:
        if self._emitted:
            return {}  # one terminal event per attempt, always
        self._emitted = True
        ended = _now_iso()
        doc: dict[str, Any] = {
            "schema": SCHEMA,
            "run_id": self.run.run_id,
            "attempt_id": self.attempt_id,
            "sequence": self.run._next_sequence(),
            "stage": self.stage,
            "unit": self.unit,
            "action": self.action,
            "state": state,
            "started_at": self._started_at,
            "ended_at": ended,
            "duration_ms": int((time.monotonic() - self._t0) * 1000),
        }
        if self.parent_attempt_id:
            doc["parent_attempt_id"] = self.parent_attempt_id
        if self.fallback_root_attempt_id:
            doc["fallback_root_attempt_id"] = self.fallback_root_attempt_id
        if self.fallback_index is not None:
            doc["fallback_index"] = self.fallback_index
        if self.executor is not None:
            doc["executor"] = self.executor
        if self.inputs is not None:
            doc["inputs"] = self.inputs
        if self.request is not None:
            doc["request"] = self.request
        # per-call fields (a passed value overrides the handle default)
        for key in ("outputs", "cost", "decision", "failure", "executor",
                    "request", "evidence_refs", "run_context"):
            val = extra.get(key)
            if val is not None:
                doc[key] = val
        rec = append_attempt(self.project, doc, actor=self.run.actor)
        if not rec:
            # evidence append failed AFTER any media was committed — never
            # delete media; the run collects a warning it will surface.
            self.run.note_warning(
                f"evidence append failed for attempt {self.attempt_id} "
                f"({self.stage}/{state}); media (if any) is untouched"
            )
        return rec


class RunEvidence:
    """The per-run evidence context — created by ``_run_build_phases`` next to
    the DR01 run_id mint, threaded to the provider registry via
    ``GenerationRequest.evidence`` and used directly by the build phases. Owns
    the monotonic sequence, the run-level terminal emission, the accumulated
    ``evidence_refs`` (qc reports) and the take→attempt_id map the ledger
    cross-reference reads."""

    def __init__(self, project: Any, run_id: str, *, actor: str = "engine",
                 run_context: dict | None = None):
        self.project = project
        self.run_id = run_id
        self.actor = actor
        # what invocation this run IS (command/target/mode …) — stamped on the
        # run-level terminal attempt and projected top-level by the manifest,
        # so a run can answer "which command produced me" (contract §6.1).
        self.run_context = dict(run_context or {})
        self._counter = itertools.count(1)
        self._seq_lock = threading.Lock()
        self.evidence_refs: list[str] = []
        self.warnings: list[str] = []
        self._take_attempts: dict[tuple[str, str], str] = {}
        self._started_at = _now_iso()
        self._run_emitted = False

    # -- sequence / bookkeeping -------------------------------------------

    def _next_sequence(self) -> int:
        with self._seq_lock:
            return next(self._counter)

    def note_warning(self, message: str) -> None:
        self.warnings.append(message)

    def add_evidence_ref(self, ref: str) -> None:
        if ref and ref not in self.evidence_refs:
            self.evidence_refs.append(ref)

    def note_take_attempt(self, shot_id: str, take_name: str, attempt_id: str) -> None:
        """Record which SUCCEEDED provider attempt produced a given take, so the
        ledger row for that take can cross-reference the same attempt_id the
        events stream carries (contract test 19)."""
        self._take_attempts[(str(shot_id), str(take_name))] = attempt_id

    def attempt_for_take(self, shot_id: str, take_name: str) -> str | None:
        return self._take_attempts.get((str(shot_id), str(take_name)))

    # -- attempt factory ---------------------------------------------------

    def attempt(self, stage: str, unit: dict, action: str, **fields: Any) -> AttemptHandle:
        return AttemptHandle(self, stage=stage, unit=unit, action=action, **fields)

    # -- run-level single-event terminals ---------------------------------

    def _run_terminal(self, state: str, **extra: Any) -> dict:
        if self._run_emitted:
            return {}
        self._run_emitted = True
        handle = AttemptHandle(
            self, stage="build", unit={"kind": "project"}, action="run",
            started_at=self._started_at,
        )
        refs = list(self.evidence_refs) or None
        if self.run_context and "run_context" not in extra:
            extra["run_context"] = dict(self.run_context)
        return handle._emit(state, evidence_refs=refs, **extra)

    def run_succeeded(self, **extra: Any) -> dict:
        return self._run_terminal(SUCCEEDED, **extra)

    def run_failed(self, **extra: Any) -> dict:
        return self._run_terminal(FAILED, **extra)

    def run_canceled(self, **extra: Any) -> dict:
        return self._run_terminal(CANCELED, **extra)

    def run_waiting_user(self, **extra: Any) -> dict:
        return self._run_terminal(WAITING_USER, **extra)


# ------------------------------------------------------- projection reader


def read_attempts(project: Any, run_id: str | None = None) -> tuple[list[dict], int]:
    """Project the attempt stream out of ``events.jsonl``.

    Returns ``(records, malformed)`` where ``records`` is the list of attempt
    evidence documents (the event ``detail``), sorted by ``(sequence,
    attempt_id)``, optionally filtered to one ``run_id``; ``malformed`` counts
    lines that failed to parse as JSON (a torn tail write) — those never affect
    the parsed records. Old/foreign event lines (any ``action`` other than
    ``stage_attempt``) are ignored, not counted as malformed. An attempt line
    whose ``state`` is unrecognizable is surfaced with state ``UNKNOWN_LEGACY``
    — never dropped, never invented into a real state."""
    path = _root(project) / EVENTS_FILE
    records: list[dict] = []
    malformed = 0
    if not path.exists():
        return records, malformed
    with open(path, "rb") as f:
        for raw_bytes in f:
            raw = _decode_jsonl_line(raw_bytes)
            if raw is None:
                malformed += 1  # torn mid-multibyte write — count, never crash
                continue
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(rec, dict) or rec.get("action") != ACTION:
                continue  # not an attempt line — ignored, not malformed
            detail = rec.get("detail")
            if not isinstance(detail, dict):
                continue
            if run_id is not None and detail.get("run_id") != run_id:
                continue
            if detail.get("state") not in _STATES:
                detail = {**detail, "state": UNKNOWN_LEGACY}
            records.append(detail)
    records.sort(key=lambda d: (_seq_key(d.get("sequence")), str(d.get("attempt_id") or "")))
    return records, malformed


def _seq_key(seq: Any) -> tuple[int, float]:
    """Sort key tolerant of a missing/nonnumeric sequence (legacy/foreign):
    numbered attempts first in order, unnumbered last."""
    try:
        return (0, float(seq))
    except (TypeError, ValueError):
        return (1, float("inf"))


# ----------------------------------------------------------- RunManifest


def _cost_of(doc: dict) -> tuple[float, str | None]:
    """The single money figure to count for ONE attempt: prefer the actual, else
    the estimate — never both (no double count). Returns ``(amount, currency)``;
    ``(0.0, None)`` when the attempt records no cost."""
    cost = doc.get("cost")
    if not isinstance(cost, dict):
        return 0.0, None
    currency = cost.get("currency")
    for field in ("actual", "estimated"):
        v = cost.get(field)
        if v is not None:
            try:
                return float(v), currency
            except (TypeError, ValueError):
                return 0.0, currency
    return 0.0, currency


def _terminal_status(run_doc: dict | None, has_failure: bool) -> str:
    """LEGACY derivation (pre-WP4 streams only): map the run-end (build/run)
    attempt's state + whether any failure attempt is present to a manifest
    terminal_status. A lifecycle-aware run (run_started/run_terminal present)
    never uses this — see :func:`build_run_manifest`."""
    state = run_doc.get("state") if run_doc else None
    if state == CANCELED:
        return MANIFEST_CANCELED
    if state == WAITING_USER:
        return MANIFEST_WAITING_USER
    if state == FAILED:
        return MANIFEST_FAILED
    if state == SUCCEEDED:
        return COMPLETED_WITH_WARNINGS if has_failure else COMPLETED
    # no run-end attempt on record: derive conservatively from the parts.
    return MANIFEST_FAILED if has_failure else COMPLETED


# run_terminal.status (lowercase, on the event) -> manifest vocabulary.
_RUN_TERMINAL_TO_MANIFEST = {
    "completed": COMPLETED,
    "completed_with_warnings": COMPLETED_WITH_WARNINGS,
    "failed": MANIFEST_FAILED,
    "canceled": MANIFEST_CANCELED,
    "waiting_user": MANIFEST_WAITING_USER,
}


def build_run_manifest(project: Any, run_id: str) -> dict:
    """The ``manju.run-manifest/v1`` document derived from the run's attempt
    events. Pure projection: never guesses from files on disk (test 13), never
    re-claims validity (test 21 uses :func:`verify_outputs` for that).

    P0 WP4 status rules (exact vocabulary COMPLETED / COMPLETED_WITH_WARNINGS /
    FAILED / CANCELED / WAITING_USER / INCOMPLETE / NOT_FOUND):

    - no events at all for the run_id → NOT_FOUND;
    - run_started without run_terminal → INCOMPLETE, with ``dangling_attempts``
      = every attempt_started whose attempt_id has no terminal stage_attempt;
    - run_terminal present → its status verbatim (a ``completed`` with failure
      attempts on record promotes to COMPLETED_WITH_WARNINGS);
    - LEGACY streams (attempt terminals only, no lifecycle events) keep the
      pre-WP4 derivation and are stamped ``legacy_terminal_only: true`` — the
      terminals prove no more than they ever did, and started evidence is never
      fabricated. Success is NEVER inferred from file existence.
    """
    records, malformed = read_attempts(project, run_id)
    lifecycle = read_run_lifecycle(project, run_id)

    run_doc: dict | None = None
    for d in records:
        if d.get("stage") == "build" and d.get("action") == "run":
            run_doc = d  # last one wins (highest sequence)

    failures: list[dict] = []
    final_output_refs: list[dict] = []
    qc_report_refs: list[str] = []
    stages: dict[str, dict[str, int]] = {}
    totals: dict[str, float] = {}
    digests: list[str] = []

    for d in records:
        stage = str(d.get("stage") or "?")
        state = str(d.get("state") or UNKNOWN_LEGACY)
        stages.setdefault(stage, {}).setdefault(state, 0)
        stages[stage][state] += 1

        if d.get("semantic_digest"):
            digests.append(str(d["semantic_digest"]))

        amount, currency = _cost_of(d)
        if amount:
            totals[currency or "?"] = round(totals.get(currency or "?", 0.0) + amount, 6)

        if state in (FAILED, REJECTED_PRECHECK, CANCELED) and d.get("failure"):
            fail = d["failure"]
            entry = {"attempt_id": d.get("attempt_id"), "stage": stage,
                     "category": fail.get("category"), "code": fail.get("code")}
            if fail.get("failure_id"):
                entry["failure_id"] = fail["failure_id"]
            failures.append(entry)

        if stage == "render" and state in (SUCCEEDED, SKIPPED_CACHE_HIT):
            for out in (d.get("outputs") or []):
                if isinstance(out, dict):
                    final_output_refs.append(out)

    if run_doc:
        for ref in (run_doc.get("evidence_refs") or []):
            if ref not in qc_report_refs:
                qc_report_refs.append(str(ref))

    run_context = dict((run_doc or {}).get("run_context") or {})

    # ---- P0 WP4 terminal_status ------------------------------------------
    lifecycle_present = bool(lifecycle["started"] or lifecycle["terminals"]
                             or lifecycle["attempt_started"])
    dangling_attempts: list[dict] | None = None
    legacy_terminal_only = False
    if not records and not lifecycle_present:
        terminal_status = MANIFEST_NOT_FOUND  # no events at all for this run_id
    elif lifecycle_present:
        if lifecycle["terminals"]:
            raw = str(lifecycle["terminals"][-1].get("status") or "")
            terminal_status = _RUN_TERMINAL_TO_MANIFEST.get(raw, MANIFEST_FAILED)
            if terminal_status == COMPLETED and failures:
                terminal_status = COMPLETED_WITH_WARNINGS  # warnings promotion
        else:
            # started (or an attempt started) but never terminated: the run was
            # interrupted/killed. NEVER claimed COMPLETED — the WP0-G fix.
            terminal_status = MANIFEST_INCOMPLETE
            terminal_ids = {str(d.get("attempt_id")) for d in records
                            if d.get("attempt_id")}
            dangling_attempts = [
                {k: v for k, v in (("attempt_id", a.get("attempt_id")),
                                   ("stage", a.get("stage")),
                                   ("provider", a.get("provider")))
                 if v is not None}
                for a in lifecycle["attempt_started"]
                if str(a.get("attempt_id")) not in terminal_ids
            ]
    else:
        # legacy stream (pre-WP4: attempt terminals only): today's derivation,
        # honestly stamped — never fabricate started evidence.
        terminal_status = _terminal_status(run_doc, bool(failures))
        legacy_terminal_only = True

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "run_id": run_id,
        "generated_at": _now_iso(),
        # which invocation produced this run (from the run-level terminal
        # attempt; absent on legacy/foreign streams — never invented).
        "command": run_context.get("command"),
        "target": run_context.get("target"),
        "mode": run_context.get("mode"),
        "terminal_status": terminal_status,
        "attempt_count": len(records),
        "malformed_lines": malformed,
        "stages": stages,
        "costs": [{"currency": c, "amount": a} for c, a in sorted(totals.items())],
        "failures": failures,
        "qc_report_refs": qc_report_refs,
        "final_output_refs": final_output_refs,
        "attempts": records,
        "evidence_digest": hash_value(sorted(digests)),
    }
    if dangling_attempts is not None:
        manifest["dangling_attempts"] = dangling_attempts
    if legacy_terminal_only:
        manifest["legacy_terminal_only"] = True
    return manifest


def run_manifest_path(project: Any, run_id: str) -> Path:
    return _root(project) / "reports" / "runs" / str(run_id) / "run.json"


def materialize_run_manifest(project: Any, run_id: str) -> Path:
    """Derive + atomically write ``reports/runs/<run_id>/run.json`` and return
    its path. Deterministic for the same event set (bar ``generated_at``, which
    is excluded from ``evidence_digest``); deletable; NEVER read back by
    build/resume/cache/rebuild-index."""
    from ..core.yamlio import atomic_write_text

    manifest = build_run_manifest(project, run_id)
    path = run_manifest_path(project, run_id)
    atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return path


def verify_outputs(project: Any, run_id: str) -> list[dict]:
    """Re-hash every recorded output file for a run and return the MISMATCHES —
    a minimal, honest verifier (test 21). Re-materializing a manifest never
    re-claims validity; THIS is where "the bytes changed under the evidence" is
    detected. Each mismatch: ``{attempt_id, path, expected_sha256,
    actual_sha256}`` (``actual_sha256`` is ``"<missing>"`` when the file is
    gone)."""
    root = _root(project)
    records, _ = read_attempts(project, run_id)
    mismatches: list[dict] = []
    for d in records:
        for out in (d.get("outputs") or []):
            if not isinstance(out, dict):
                continue
            rel = out.get("path")
            expected = out.get("sha256")
            if not rel:
                continue  # nothing identifiable to verify against
            if not expected:
                # a recorded output with NO recorded hash is unverifiable, and
                # unverifiable must never read as verified (UNKNOWN is never
                # guessed into PASS) — surface it instead of skipping.
                mismatches.append({"attempt_id": d.get("attempt_id"), "path": rel,
                                   "expected_sha256": "<unrecorded>",
                                   "actual_sha256": "<unverifiable>"})
                continue
            fpath = root / rel
            if not fpath.exists():
                mismatches.append({"attempt_id": d.get("attempt_id"), "path": rel,
                                   "expected_sha256": expected,
                                   "actual_sha256": "<missing>"})
                continue
            actual = hash_file(fpath)
            if actual != expected:
                mismatches.append({"attempt_id": d.get("attempt_id"), "path": rel,
                                   "expected_sha256": expected,
                                   "actual_sha256": actual})
    return mismatches
