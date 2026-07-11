"""DR06 — the pure submission-identity / admission-state-machine module.

This module is the single home of the *pure* facts of paid-submission safety —
identity, the state machine, disposition classification, the per-submission
event hash-chain and the idempotency-key derivation. It owns NO persistence and
NO network: :mod:`manju.runtime.state` projects the intents table, and
:func:`manju.build.attempts.append_submission_event` does the flock-locked write
of the events this module shapes. Everything here is a deterministic function of
its inputs — trivially unit-testable, never a build/resume input beyond the
digests it mints.

The core principle (contract): *an ambiguous submit outcome must never be
automatically treated as not-happened.* We never claim exactly-once; we make the
ambiguity explicit, durable and recoverable.

What lives here (contract ruling 2):

* **Identity** — :func:`build_submission_identity` assembles the
  ``manju.submission-identity/v1`` document over ONLY remote-result-affecting
  semantics (§7.3-7.5); :func:`request_digest` hashes it. The
  ``provider_profile_digest`` is reused verbatim from DR04
  (``providers.catalog``) so a provider profile change moves the digest.
* **submission_id** — :func:`mint_submission_id` (``"sub_" + uuid4().hex[:12]``),
  minted BEFORE any network and persisted first. ``submission_id`` is NOT the
  ``request_digest``: an explicit redo mints a NEW id for the same digest; a
  recovery KEEPS the id; a fallback to another provider is a NEW submission.
* **State machine** — :data:`STATES` + :data:`LEGAL_TRANSITIONS` +
  :func:`is_legal_transition` / :func:`assert_transition`. Legacy rows without a
  state read as :data:`UNKNOWN_LEGACY` (never invented).
* **Disposition** — the ORTHOGONAL classification of a submit outcome
  (:data:`NOT_DISPATCHED` / :data:`DEFINITELY_REJECTED` / :data:`ADMITTED` /
  :data:`OUTCOME_UNKNOWN`), plus the conservative classifiers
  :func:`disposition_for_status` / :func:`disposition_for_transport_error`.
* **Event chain** — :func:`submission_event_digest` + :func:`verify_chain`: a
  per-submission sha256 hash chain over the events that enables corruption
  detection scoped to one shot+provider.
* **Idempotency** — :func:`idempotency_key` = sha256 over
  ``("manju-provider-submit/v1", provider_id, submission_id)`` (never a secret).
* **Redaction** — :func:`redact_reason` scrubs a free-form reason string of
  credentials / signed-URL queries before it rides an event.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.hashing import hash_value

SCHEMA_IDENTITY = "manju.submission-identity/v1"
SCHEMA_EVENT = "manju.provider-submission-event/v1"

# The event action every submission-state line carries at the envelope level, so
# the collaboration-log tail / a projection reads it like any other action. It
# rides 03C's SAME events.jsonl (no new ledger, no new lock — ruling 1).
EVENT_ACTION = "submission_state"

# ------------------------------------------------------------------- states

PREPARED = "PREPARED"
DISPATCHING = "DISPATCHING"
ADMITTED = "ADMITTED"
REJECTED_PRE_DISPATCH = "REJECTED_PRE_DISPATCH"
REMOTE_REJECTED = "REMOTE_REJECTED"
OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
TERMINAL_SUCCESS = "TERMINAL_SUCCESS"
TERMINAL_FAILURE = "TERMINAL_FAILURE"
ABANDONED_BY_USER = "ABANDONED_BY_USER"
# a projection of a legacy intent row that predates the state column — never a
# state we WRITE, only one we READ for rows minted before DR06.
UNKNOWN_LEGACY = "UNKNOWN_LEGACY"

STATES = (
    PREPARED, DISPATCHING, ADMITTED, REJECTED_PRE_DISPATCH, REMOTE_REJECTED,
    OUTCOME_UNKNOWN, TERMINAL_SUCCESS, TERMINAL_FAILURE, ABANDONED_BY_USER,
    UNKNOWN_LEGACY,
)
_STATES = frozenset(STATES)

# States that still need resolution — a rebuild restores these from the event
# stream (ruling 4), and a fresh submit for the same shot+provider must consult
# them (ruling 8). ADMITTED-without-a-terminal is unresolved too, but it is
# resume-safe (polling, never a resubmit) and is handled distinctly from the two
# side-effect-ambiguous states below.
UNRESOLVED_STATES = frozenset({DISPATCHING, ADMITTED, OUTCOME_UNKNOWN})
# The states whose remote side effect is genuinely ambiguous: a fresh submit for
# the same shot+provider must fail closed on these (never auto-resubmit).
SIDE_EFFECT_AMBIGUOUS = frozenset({DISPATCHING, OUTCOME_UNKNOWN})
TERMINAL_STATES = frozenset(
    {TERMINAL_SUCCESS, TERMINAL_FAILURE, REJECTED_PRE_DISPATCH, REMOTE_REJECTED,
     ABANDONED_BY_USER})

# Legal transitions. A retry after a provably-not-sent rejection re-enters
# DISPATCHING (same submission, nothing was sent). A recovery (attach) lifts an
# OUTCOME_UNKNOWN/DISPATCHING to ADMITTED under the SAME submission_id; abandon
# closes an unresolved submission with explicit risk acceptance.
LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    PREPARED: frozenset({DISPATCHING, REJECTED_PRE_DISPATCH, ABANDONED_BY_USER}),
    DISPATCHING: frozenset({ADMITTED, REJECTED_PRE_DISPATCH, REMOTE_REJECTED,
                            OUTCOME_UNKNOWN, ABANDONED_BY_USER}),
    ADMITTED: frozenset({TERMINAL_SUCCESS, TERMINAL_FAILURE, OUTCOME_UNKNOWN,
                         ABANDONED_BY_USER}),
    # a NOT_DISPATCHED rejection may be retried (re-dispatch) — nothing was sent.
    REJECTED_PRE_DISPATCH: frozenset({DISPATCHING}),
    # a definite remote rejection (e.g. 429) may be retried under existing policy.
    REMOTE_REJECTED: frozenset({DISPATCHING}),
    # recovery paths off an ambiguous outcome (ruling 9/10/11).
    OUTCOME_UNKNOWN: frozenset({ADMITTED, ABANDONED_BY_USER, DISPATCHING}),
    TERMINAL_SUCCESS: frozenset(),
    TERMINAL_FAILURE: frozenset(),
    ABANDONED_BY_USER: frozenset(),
    UNKNOWN_LEGACY: frozenset(),
}


def normalize_state(state: Any) -> str:
    """Any unknown/absent state reads as :data:`UNKNOWN_LEGACY` — never invented
    into a real state (mirrors ``build.attempts`` state normalization)."""
    return state if state in _STATES else UNKNOWN_LEGACY


def is_legal_transition(from_state: Any, to_state: str) -> bool:
    return to_state in LEGAL_TRANSITIONS.get(normalize_state(from_state), frozenset())


class IllegalTransition(ValueError):
    """A submission-state transition the state machine forbids."""


def assert_transition(from_state: Any, to_state: str) -> None:
    if not is_legal_transition(from_state, to_state):
        raise IllegalTransition(
            f"illegal submission transition {normalize_state(from_state)} -> {to_state}")


# --------------------------------------------------------------- disposition

NOT_DISPATCHED = "NOT_DISPATCHED"          # provably not sent (safe to retry)
DEFINITELY_REJECTED = "DEFINITELY_REJECTED"  # tested remote rejection (fallback ok)
ADMITTED_DISPOSITION = "ADMITTED"          # provable receipt (a job id / receipt)
# everything after the send boundary that is not one of the above: the outcome
# is genuinely unknown and must NEVER be auto-treated as not-happened.
OUTCOME_UNKNOWN_DISPOSITION = "OUTCOME_UNKNOWN"

DISPOSITIONS = (NOT_DISPATCHED, DEFINITELY_REJECTED, ADMITTED_DISPOSITION,
                OUTCOME_UNKNOWN_DISPOSITION)

def disposition_for_status(status: int,
                           definite_statuses: Iterable[int] | None = None) -> str:
    """Classify a submit HTTP status against the provider's DECLARED definite-
    rejection set (P0 WP2). There is NO global "these 4xx are always rejected"
    table anymore (no global 429/tested-4xx assumption): a post-send status is
    DEFINITELY_REJECTED ONLY when the manifest explicitly declares it
    (``submit.definite_rejection_statuses``); otherwise ANY status >= 400 is
    conservatively OUTCOME_UNKNOWN once the send boundary was crossed. < 400 is
    the caller's success concern (the job-id parse decides ADMITTED vs
    OUTCOME_UNKNOWN). ``definite_statuses`` ``None``/empty declares nothing."""
    if status < 400:
        return ADMITTED_DISPOSITION
    if definite_statuses is not None and status in definite_statuses:
        return DEFINITELY_REJECTED
    return OUTCOME_UNKNOWN_DISPOSITION


def disposition_for_transport_error(exc: BaseException) -> str:
    """Classify a transport-layer exception. ONLY a DNS failure
    (``socket.gaierror``) or a ``ConnectionRefusedError`` raised before any byte
    could be written is provably-not-sent (NOT_DISPATCHED). Everything else —
    timeout, connection reset, EOF, an unknown OSError — is OUTCOME_UNKNOWN: once
    the send boundary is crossed we can no longer prove the request did not
    arrive. Unwraps ``urllib.error.URLError`` to inspect its ``.reason``."""
    import socket

    reason = getattr(exc, "reason", None)
    candidates = [exc, reason] if reason is not None else [exc]
    for c in candidates:
        if isinstance(c, socket.gaierror):
            return NOT_DISPATCHED
        if isinstance(c, ConnectionRefusedError):
            return NOT_DISPATCHED
    return OUTCOME_UNKNOWN_DISPOSITION


def disposition_to_state(disposition: str | None) -> str:
    """Map a submit-outcome disposition to the submission state it drives. A
    ``None`` disposition is the legacy/conservative case: at the submit boundary
    an unclassified failure is treated as OUTCOME_UNKNOWN only when the caller
    explicitly opts in — see providers/base.py for the byte-identity guard."""
    return {
        NOT_DISPATCHED: REJECTED_PRE_DISPATCH,
        DEFINITELY_REJECTED: REMOTE_REJECTED,
        ADMITTED_DISPOSITION: ADMITTED,
        OUTCOME_UNKNOWN_DISPOSITION: OUTCOME_UNKNOWN,
    }.get(disposition or "", OUTCOME_UNKNOWN)


# --------------------------------------------------------------- submission id


def mint_submission_id() -> str:
    """A fresh submission identity string, minted BEFORE any network and
    persisted first (ruling 2). Short, collision-free, never a remote id."""
    return "sub_" + uuid4().hex[:12]


# --------------------------------------------------------------- canonical params

_URL_QUERY_RE = re.compile(r"^(https?://[^?\s]+)\?\S*$")
# non-anchored: strips the query string off a URL embedded anywhere in free text
# (redact_reason), where the anchored form above cannot match.
_URL_QUERY_INLINE_RE = re.compile(r"(https?://[^?\s]+)\?\S*")
_SECRET_KEY_RE = re.compile(
    r"(?i)(authorization|api[_-]?key|apikey|secret|token|access[_-]?token|"
    r"auth[_-]?token|password|passwd|x-api-key|bearer|credential|signature|sign)")


def strip_url_query(value: str) -> str:
    """Drop a signed-URL query string (§7.5 excludes signed URLs from identity;
    a signed url's query is the credential-bearing part)."""
    m = _URL_QUERY_RE.match(value)
    return m.group(1) + "?<redacted>" if m else value


def _project_relative(path: Path, project_root: Path | None) -> str:
    """A Path → its project-relative logical id (§7.4). Absolute paths NEVER
    appear (§7.5): a path outside the root collapses to a non-leaking token."""
    p = Path(path)
    if project_root is not None:
        try:
            return p.resolve().relative_to(Path(project_root).resolve()).as_posix()
        except (ValueError, OSError):
            return "<external-path>"
    return "<external-path>"


def canonical_params(params: dict | None, *, project_root: Path | None = None) -> Any:
    """Canonicalize a params dict for the identity digest (§7.4): sorted keys,
    ``bool`` explicitly distinct from ``int``, floats rendered repr-stable, every
    ``Path`` reduced to a project-relative logical id, signed-URL queries
    stripped, and secret-named keys dropped wholesale (their VALUE never rides
    the identity). Returns a typed, JSON-safe structure."""
    return _canon(params or {}, project_root, drop_secret_keys=True)


def _canon(value: Any, project_root: Path | None, *, drop_secret_keys: bool = False) -> Any:
    if isinstance(value, bool):
        return {"__t": "bool", "v": value}          # bool != int, explicitly
    if isinstance(value, int):
        return {"__t": "int", "v": value}
    if isinstance(value, float):
        return {"__t": "float", "v": repr(value)}   # repr-stable across platforms
    if isinstance(value, Path):
        return {"__t": "path", "v": _project_relative(value, project_root)}
    if value is None:
        return {"__t": "null"}
    if isinstance(value, str):
        return {"__t": "str", "v": strip_url_query(value)}
    if isinstance(value, (list, tuple)):
        return [_canon(v, project_root) for v in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k in sorted(value, key=str):
            key = str(k)
            if drop_secret_keys and _SECRET_KEY_RE.search(key):
                # dropped WHOLESALE (not a redacted placeholder): a request with a
                # secret-named param must hash IDENTICALLY to one without it, so
                # the credential never perturbs — nor rides — the identity (§7.5).
                continue
            out[key] = _canon(value[k], project_root, drop_secret_keys=drop_secret_keys)
        return out
    return {"__t": "repr", "v": repr(value)}


# --------------------------------------------------------------- identity


def build_submission_identity(
    *,
    shot_id: str,
    spec_hash: str | None,
    provider_id: str,
    provider_profile_digest: str,
    capability: str,
    duration_ms: int | None,
    candidates: int | None,
    seed: Any,
    params: dict | None,
    compiled_prompt: str | None,
    ref_refs: list[dict] | None = None,
    first_frame: bool = False,
    last_frame: bool = False,
    project_root: Path | None = None,
) -> dict:
    """Assemble the ``manju.submission-identity/v1`` document (§7.2) over ONLY
    remote-result-affecting semantics.

    INCLUDES (§7.3): shot id + spec_hash; provider id + the DR04 profile digest;
    capability; duration; candidates; seed; canonical params; the compiled-prompt
    DIGEST (never the text — ``compiled_prompt`` is hashed here and discarded);
    the final selected refs as ``(role, logical_id, content_sha256)`` in delivery
    order (the caller hashes the small ref files); first/last-frame flags.

    EXCLUDES (§7.5): keys / auth / signed URLs / absolute paths / mtime / now /
    PID / poll intervals / UI names — none of which are accepted as inputs here.
    """
    from ..core.hashing import hash_value as _hv

    return {
        "schema": SCHEMA_IDENTITY,
        "shot_id": shot_id,
        "spec_hash": spec_hash,
        "provider_id": provider_id,
        "provider_profile_digest": provider_profile_digest,
        "capability": capability,
        "duration_ms": duration_ms,
        "candidates": candidates,
        "seed": seed,
        "params": canonical_params(params, project_root=project_root),
        # the compiled prompt is referenced by digest ONLY — the text never lands.
        "compiled_prompt_digest": _hv(compiled_prompt) if compiled_prompt else None,
        "refs": list(ref_refs or []),
        "first_frame": bool(first_frame),
        "last_frame": bool(last_frame),
    }


def request_digest(identity: dict) -> str:
    """The stable ``sha256:`` digest of a submission identity (§7.3). Two
    submissions with the same remote-affecting semantics share a digest; a change
    to any included fact moves it. The ``schema`` key is part of the hashed
    document so a schema bump is itself a semantic change."""
    return hash_value(identity)


def ref_fact(role: str, logical_id: str, content_sha256: str | None) -> dict:
    """One entry of the identity's ``refs`` list — ``(role, logical_id,
    content_sha256)`` in delivery order. ``content_sha256`` is ``None`` for a URL
    ref (no local bytes); the logical_id has already had any signed query
    stripped by the caller / :func:`strip_url_query`."""
    return {"role": role, "logical_id": strip_url_query(str(logical_id)),
            "content_sha256": content_sha256}


# --------------------------------------------------------------- idempotency


IDEMPOTENCY_NAMESPACE = "manju-provider-submit/v1"


def idempotency_key(provider_id: str, submission_id: str) -> str:
    """The derived provider-native idempotency key (ruling 10): a sha256 over
    ``(namespace, provider_id, submission_id)`` — deterministic, stable across
    re-dispatch attempts of the SAME submission, and carrying NO secret. Rendered
    without the ``sha256:`` prefix so it drops straight into a header value."""
    return hash_value([IDEMPOTENCY_NAMESPACE, provider_id, submission_id]).split(":", 1)[-1]


# --------------------------------------------------------------- event chain


def _event_digest_payload(event: dict) -> dict:
    """The stable subset of a submission event that the chain digest hashes —
    everything that identifies the event and its link, and nothing incidental
    (no envelope ts/actor, no human ``detail`` text)."""
    return {
        "submission_id": event.get("submission_id"),
        "request_digest": event.get("request_digest"),
        "from": event.get("from"),
        "to": event.get("to"),
        "provider_id": event.get("provider_id"),
        "remote_job_id": event.get("remote_job_id"),
        "reason_code": event.get("reason_code"),
        "event_id": event.get("event_id"),
        "prev_event_digest": event.get("prev_event_digest"),
    }


def submission_event_digest(event: dict) -> str:
    """The sha256 of one submission event, used as the NEXT event's
    ``prev_event_digest`` — the per-submission hash chain (ruling 1)."""
    return hash_value(_event_digest_payload(event))


def verify_chain(events: list[dict]) -> tuple[bool, int | None]:
    """Verify a per-submission event chain (events in emission order). Returns
    ``(ok, broken_at)``: ``broken_at`` is the index of the first event whose
    ``prev_event_digest`` does not match the recomputed digest of its
    predecessor (or ``None`` when the chain is intact). An empty / single-event
    chain is trivially intact. Corruption is scoped to ONE shot+provider by the
    caller (ruling 4): a broken chain fail-closes only that unit."""
    prev_digest: str | None = None
    for i, ev in enumerate(events):
        if ev.get("prev_event_digest") != prev_digest:
            return False, i
        prev_digest = submission_event_digest(ev)
    return True, None


# --------------------------------------------------------------- redaction


def redact_reason(text: Any, limit: int = 400) -> str:
    """Scrub a free-form reason string before it rides an event: strip a
    signed-URL query, collapse whitespace, bound the length. Credential-looking
    tokens are handled by the events-stream secret backstop
    (``build.attempts._scrub_secret_patterns``) on write; this is the cheap
    first pass so an obvious signed url never lands verbatim."""
    s = " ".join(str(text).split())
    s = _URL_QUERY_INLINE_RE.sub(lambda m: m.group(1) + "?<redacted>", s)
    return s if len(s) <= limit else s[:limit] + "…"
