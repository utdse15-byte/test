"""The config-driven generic cloud adapter (§8.6).

Most cloud generation APIs share one REST shape: POST a task → get a job id,
GET to poll status, download the result by URL. This adapter compresses the
differences into a manifest; onboarding a new API = filling the ★ fields.
Signed requests, multipart uploads, websockets etc. are out of scope by
design — those get a dedicated adapter class via ``manifest.adapter``.

HTTP goes through an injectable ``transport`` so every behavior is testable
offline; the default transport is stdlib urllib (no new dependencies).
"""

from __future__ import annotations

import base64
import json
import hashlib
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

from ..core.models import KeyframeSpec, ShotSpec
from ..core.hashing import hash_value
from .base import (
    CloudProvider,
    FailureKind,
    GenerationRequest,
    ProviderFailure,
    status_to_kind,
)
from .jsonpath import JsonPathError, assign, extract
from .manifest import FIRST_LAST_CAPABILITY, GENERIC_ADAPTER, JOB_STATES, ProviderManifest
from .submission import ProviderExecutionProfile
from .refs import (
    RefItem,
    encode_multipart,
    read_ref_bytes,
    resolve_local_ref,
    unreadable_ref_message,
    physical_ref_key,
)

# Tier label recorded on a keyframe-sourced ref (its own lineage bucket, distinct
# from the resolve_refs tiers — a first/last-frame image is authored on the shot's
# keyframe, not resolved through the ref tiering).
TIER_KEYFRAME = "keyframe"


class HttpResponse(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))


@dataclass(frozen=True)
class PreparedFile:
    """One immutable multipart file captured before submission admission."""

    field: str
    filename: str
    payload: bytes
    content_sha256: str


@dataclass(frozen=True)
class PreparedProviderPayload:
    """The exact Generic Cloud semantics bound by request identity.

    JSON bytes, multipart fields, and file bytes are sealed once. Transport may
    choose a fresh multipart boundary on a retry, but it never re-renders the
    manifest or re-reads a project file.
    """

    provider_id: str
    method: str
    url: str
    rendered_body_json: bytes
    multipart_fields: tuple[tuple[str, str], ...]
    files: tuple[PreparedFile, ...]
    semantic_digest: str
    ref_content_sha256: tuple[tuple[str, str, str], ...]
    delivery_evidence_json: bytes
    extra_headers: tuple[tuple[str, str], ...]
    auth_key_env: str | None
    auth_header: str
    idempotency_field: str | None
    execution_profile: ProviderExecutionProfile

    @property
    def rendered_body(self) -> dict[str, object]:
        """Return a detached view; callers cannot mutate the sealed bytes."""
        value = json.loads(self.rendered_body_json)
        assert isinstance(value, dict)
        return value

    @property
    def delivery_evidence(self) -> dict:
        value = json.loads(self.delivery_evidence_json)
        assert isinstance(value, dict)
        return value

    def semantic_facts(self) -> dict:
        return {
            "method": self.method,
            "body": self.rendered_body,
            "files": [
                {
                    "field": item.field,
                    "filename": item.filename,
                    "content_sha256": item.content_sha256,
                }
                for item in self.files
            ],
        }


# --------------------------------------------------------------- shared guards
# One home for the parse/validate steps every generic adapter (video / tts /
# asr) must run on an UNTRUSTED provider response, so a malformed 2xx can never
# leak a raw UnicodeDecodeError / TypeError / fabricated id out of a paid path.

def parse_json_response(resp: "HttpResponse", provider_id: str, *,
                        post_submit: bool = False):
    """``resp.json()`` with provider-level error conversion. A 2xx whose body is
    not valid UTF-8 / not valid JSON raises a structured :class:`ProviderFailure`
    instead of a raw ``UnicodeDecodeError``/``JSONDecodeError``. ``post_submit``
    marks the disposition ``OUTCOME_UNKNOWN`` — the remote side may have accepted
    the (paid) request even though we cannot read its reply, so it must never be
    treated as a safe not-happened failure."""
    try:
        return json.loads(resp.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        detail = {"status": resp.status, "body": resp.text()[:2000]}
        disposition = None
        if post_submit:
            from .submission import OUTCOME_UNKNOWN_DISPOSITION
            disposition = OUTCOME_UNKNOWN_DISPOSITION
            detail["disposition"] = OUTCOME_UNKNOWN_DISPOSITION
        raise ProviderFailure(
            FailureKind.provider_error,
            f"{provider_id}: response was not readable JSON ({exc})",
            detail=detail, disposition=disposition,
        ) from exc


def require_remote_id(value, provider_id: str, *, field: str = "job id",
                      post_submit: bool = True) -> str:
    """Validate a value extracted as a remote job id (or any identifier that
    becomes a URL path segment). ``str(extract(...))`` used to coerce JSON
    ``null``/objects/arrays/booleans into ``"None"``/``"{...}"``/``"['x']"``/
    ``"True"`` and then poll ``/jobs/None`` — orphaning the real accepted job.
    Only a non-empty, non-whitespace string (or a plain int) is a real id;
    everything else means the response was malformed AFTER a paid submit →
    ``OUTCOME_UNKNOWN``."""
    ok = (isinstance(value, str) and value.strip()) or (
        isinstance(value, int) and not isinstance(value, bool))
    if ok:
        return str(value).strip() if isinstance(value, str) else str(value)
    disposition = None
    detail = {"raw": repr(value)[:200]}
    if post_submit:
        from .submission import OUTCOME_UNKNOWN_DISPOSITION
        disposition = OUTCOME_UNKNOWN_DISPOSITION
        detail["disposition"] = OUTCOME_UNKNOWN_DISPOSITION
    raise ProviderFailure(
        FailureKind.provider_error,
        f"{provider_id}: response carried no usable {field} "
        f"(got {type(value).__name__}) — the request may have been accepted remotely",
        detail=detail, disposition=disposition,
    )


def journal_remote_submit(project_root, provider_id: str, job_id: str,
                          detail: dict) -> None:
    """DURABLY record an accepted async remote job id BEFORE polling it — the
    fail-closed submission journal (audit findings 8/9). A best-effort
    ``append_event`` could silently drop (lock busy) or raise (open failure) the
    id AFTER the paid request was accepted, orphaning the spend so a retry
    double-charges. This fsyncs the id and, if the durable write cannot be made,
    raises a ``ProviderFailure`` that STILL surfaces the id and marks the outcome
    ``OUTCOME_UNKNOWN`` — never silently continue, never lose the id."""
    from datetime import datetime, timezone

    from ..core.events import EvidenceWriteError, append_jsonl_line
    from .submission import OUTCOME_UNKNOWN_DISPOSITION

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": "engine",
        "action": "provider_remote_submit",
        "detail": {"provider": provider_id, "remote_job_id": job_id, **detail},
    }
    try:
        append_jsonl_line(project_root, record, durable=True, required=True)
    except EvidenceWriteError as exc:
        raise ProviderFailure(
            FailureKind.provider_error,
            f"{provider_id}: paid remote job {job_id} was accepted but its id "
            f"could not be durably journaled ({exc.reason}) — the remote job MAY "
            f"be running/billing; reconcile job_id={job_id} before retrying",
            detail={"remote_job_id": job_id, "job_id": job_id,
                    "disposition": OUTCOME_UNKNOWN_DISPOSITION,
                    "journal_error": exc.reason},
            disposition=OUTCOME_UNKNOWN_DISPOSITION,
        ) from exc


def is_transient_status(status: int) -> bool:
    """A polling response worth retrying against the SAME accepted job: rate
    limiting (429) or a server-side transient (5xx). A non-retryable 4xx (404
    job-not-found, 401/403 auth) is terminal."""
    return status == 429 or 500 <= status < 600


def retry_after_seconds(headers: dict, default: float) -> float:
    """Parse a ``Retry-After`` header (delta-seconds form) into a BOUNDED sleep.

    PROVIDER-POLL-002: the old ``max(0.0, min(float(ra), 60.0))`` turned
    ``Retry-After: 0``, a negative value, or ``nan`` into a **0.0 second sleep**,
    so the poll loop of an already-paid job busy-spun the remote endpoint (and
    our own CPU) flat out until the 600s budget expired — a proxy or a
    misconfigured gateway emitting ``Retry-After: 0`` is enough, no adversary
    needed. The value is now clamped to ``[default, 60]``: ``default`` is the
    caller's own backoff floor, so a header can only ever ask us to wait
    LONGER than we already intended, never to spin. Non-finite (nan/inf) and
    unparseable values fall back to ``default``."""
    import math

    floor = default if default > 0 else 0.0
    ra = (headers or {}).get("Retry-After") or (headers or {}).get("retry-after")
    if ra:
        try:
            value = float(str(ra).strip())
        except (TypeError, ValueError):
            return default
        if math.isfinite(value):
            return max(floor, min(value, 60.0))
    return default


#: Highest exponent any poll backoff is allowed to raise 2 to. ``2 ** 60`` is
#: already far above every cap we use, so clamping here changes no real delay —
#: it only removes the overflow cliff (PROVIDER-POLL-002).
_MAX_BACKOFF_EXPONENT = 60


def poll_backoff(round_index: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    """Exponential poll backoff that cannot overflow.

    The sibling poll loops all wrote ``min(base * (2 ** i), cap)`` inline. That
    expression is evaluated EAGERLY (it is also passed as the ``default`` of
    :func:`retry_after_seconds`), so a long-running poll — an instant transport
    against a 600s budget reaches four-digit round counts easily — raised a bare
    ``OverflowError`` out of the middle of a paid, already-accepted job instead
    of a typed :class:`ProviderFailure`. Clamping the exponent keeps the curve
    identical for every round that matters and makes the tail flat."""
    i = max(0, min(int(round_index), _MAX_BACKOFF_EXPONENT))
    return min(base * (2 ** i), cap)


_SAFE_AUDIO_EXTS = frozenset({"wav", "mp3", "m4a", "aac", "flac", "ogg", "opus"})


def safe_audio_ext(fmt: str, provider_id: str) -> str:
    """A manifest's ``tts.audio_format`` is an EXTENSION, never a path. The
    destination used to be ``dest_dir / f"voice.{fmt.lstrip('.')}"`` with no
    separator/`..`/drive checks, so ``audio_format: x/../../escaped.bin`` wrote
    OUTSIDE the staging dir. Accept only a short alnum extension from the
    allowlist; refuse anything with a separator, ``..``, a colon/drive or
    unexpected characters."""
    raw = str(fmt or "").strip().lstrip(".").lower()
    if raw in _SAFE_AUDIO_EXTS:
        return raw
    raise ProviderFailure(
        FailureKind.invalid,
        f"{provider_id}: tts.audio_format {fmt!r} is not a permitted audio "
        f"extension (one of {sorted(_SAFE_AUDIO_EXTS)}) — it names a file "
        "extension, not a path",
    )


# transport(method, url, headers, body) -> HttpResponse; never raises on HTTP
# error statuses — status classification is the provider's job, not urllib's.
Transport = Callable[[str, str, dict[str, str], bytes | None], HttpResponse]

# Network hygiene (goal W, #43/#44/#54): every generic_cloud/tts/asr/comfyui
# call funnels through THIS one transport by default, so a scheme allowlist
# and a size cap live in exactly one place and stay consistent everywhere —
# "unify via one shared helper". Generous default (2 GiB): large video/image
# results are legitimate; the cap exists to stop an OOM from an unbounded or
# misbehaving response, not to constrain normal media sizes. Override with
# MANJU_MAX_DOWNLOAD_BYTES (an int; <=0 disables the cap for the rare
# legitimately-unbounded job).
DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
_ALLOWED_URL_SCHEMES = ("http", "https")
_READ_CHUNK = 65536


def _max_response_bytes() -> int | None:
    override = os.environ.get("MANJU_MAX_DOWNLOAD_BYTES")
    if override:
        try:
            value = int(override)
        except ValueError:
            return DEFAULT_MAX_RESPONSE_BYTES
        return value if value > 0 else None
    return DEFAULT_MAX_RESPONSE_BYTES


def _read_capped(resp, url: str, max_bytes: int | None) -> bytes:
    """Stream ``resp`` up to ``max_bytes``, refusing (never buffering
    unbounded) past the cap — the #43 OOM guard: a runaway or hostile
    response is cut off instead of exhausted into memory."""
    if max_bytes is None:
        return resp.read()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"response from {safe_url(url)} exceeded the {max_bytes}-byte "
                "cap (configurable via MANJU_MAX_DOWNLOAD_BYTES) — refusing to "
                "buffer further (goal 43, OOM guard)",
                detail={"url": safe_url(url), "max_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)


def safe_url(url: object) -> str:
    """A URL rendered for EVIDENCE (a failure message / ``detail``).

    PROVIDER-PRIV-001: a result URL is normally a SIGNED url — its query string
    (``X-Amz-Signature``/``X-Amz-Credential``/``token=``) IS the credential.
    Those messages land in ``reports/failures.jsonl`` and the run log, which are
    read, shared and (now) scanned, so no url reaches evidence verbatim. The
    scrub itself is NOT forked here: :func:`providers.submission.strip_url_query`
    stays the one owner and this is the provider-side front door to it."""
    from .submission import strip_url_query

    return strip_url_query(str(url))


# Headers that may cross an origin boundary on a redirect. Everything else —
# Authorization, Cookie, X-Api-Key, a tenant/account header, a signed-request
# header — is dropped: see :class:`CredentialSafeRedirectHandler`.
_REDIRECT_SAFE_HEADERS = frozenset({
    "accept", "accept-encoding", "accept-language", "user-agent",
    "content-type", "content-length",
})


def _same_origin(a: str, b: str) -> bool:
    """Scheme + host + effective port equality (a redirect that stays on the
    SAME origin may keep its credentials; anything else may not)."""
    pa, pb = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
    default = {"http": 80, "https": 443}
    try:
        port_a = pa.port or default.get(pa.scheme.lower())
        port_b = pb.port or default.get(pb.scheme.lower())
    except ValueError:  # malformed port -> treat as a different origin
        return False
    return (pa.scheme.lower() == pb.scheme.lower()
            and (pa.hostname or "").lower() == (pb.hostname or "").lower()
            and port_a == port_b)


class CredentialSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """PROVIDER-NET-001 — the redirect policy stdlib urllib does not have.

    ``urllib.request.HTTPRedirectHandler.redirect_request`` (CPython 3.11)
    copies EVERY request header onto the redirect target — including
    ``Authorization``/``X-Api-Key``/a tenant header — regardless of whether the
    target is the same origin, and it rewrites a ``POST`` into a ``GET`` with no
    body. On the owner's ordinary path that means (a) the provider API key is
    handed to whatever host a 302 names, and (b) a PAID submit silently becomes a
    credential-less GET whose reply we then try to read a job id out of.

    This handler keeps the useful half — a GET/HEAD redirect is followed, which
    is how signed CDN result URLs work — and fixes both halves:

    * cross-origin: only :data:`_REDIRECT_SAFE_HEADERS` survive; every credential
      / tenant header is dropped before the new request is issued;
    * a non-GET/HEAD method is NEVER auto-followed (``None`` = "not my job"), so
      a paid POST is never re-issued as a GET. The 30x then surfaces to
      :func:`default_transport`, which refuses it with an honest disposition.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.get_method() not in ("GET", "HEAD"):
            return None  # never downgrade a paid POST into a GET
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None or _same_origin(req.full_url, new.full_url):
            return new
        kept = {k: v for k, v in new.header_items()
                if k.lower() in _REDIRECT_SAFE_HEADERS}
        return urllib.request.Request(
            new.full_url, data=new.data, headers=kept, method=new.get_method(),
            origin_req_host=new.origin_req_host, unverifiable=True)


def _is_loopback_host(host: str | None) -> bool:
    """True for localhost / 127.0.0.1 / ::1 — must never go through HTTP_PROXY.

    C35: corporate Windows proxies commonly break ComfyUI and other local
    adapters (502 via 127.0.0.1:proxy) when urlopen inherits env proxies.
    """
    if not host:
        return False
    h = host.strip().lower().strip("[]")
    return h in ("localhost", "127.0.0.1", "::1") or h.startswith("127.")


def default_transport(method: str, url: str, headers: dict[str, str],
                      body: bytes | None) -> HttpResponse:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        # #43: an unexpected scheme (file://, ftp://, data://…) is refused
        # BEFORE urlopen ever touches it — never a surprise local-file read or
        # non-HTTP protocol dial from a manifest/response-supplied URL.
        raise ProviderFailure(
            FailureKind.invalid,
            f"refusing non-http(s) URL scheme {scheme!r}: {safe_url(url)}",
            detail={"url": safe_url(url), "scheme": scheme},
        )
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    max_bytes = _max_response_bytes()
    host = urllib.parse.urlsplit(url).hostname
    # PROVIDER-NET-001: EVERY call goes through an opener carrying the
    # credential-safe redirect policy — urlopen's default handler would copy
    # Authorization cross-origin and rewrite a paid POST into a GET.
    # C35: loopback must ALSO bypass system HTTP(S)_PROXY (personal Windows proxy).
    handlers: list = [CredentialSafeRedirectHandler()]
    if _is_loopback_host(host):
        handlers.append(urllib.request.ProxyHandler({}))
    open_url = urllib.request.build_opener(*handlers).open
    try:
        with open_url(request, timeout=60) as resp:
            status, resp_headers = resp.status, dict(resp.headers)
            payload = _read_capped(resp, url, max_bytes)
    except urllib.error.HTTPError as exc:  # still a response — classify upstream
        status, payload = exc.code, _read_capped(exc, url, max_bytes)
        if 300 <= status < 400:
            _refuse_redirect(method, url, status, dict(exc.headers or {}))
        return HttpResponse(status, dict(exc.headers or {}), payload)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # DR06 (ruling 6): mark the send boundary. ONLY a DNS failure or a
        # connection-refused before any byte was written is provably-not-sent;
        # everything else after submit was attempted (timeout, reset, EOF,
        # unknown OSError) is OUTCOME_UNKNOWN — the request MAY have arrived, so
        # its outcome must never be auto-treated as not-happened. The
        # classification is a pure function in providers.submission; the caller
        # (submit) reads it off the raised ProviderFailure.
        from .submission import disposition_for_transport_error

        raise ProviderFailure(
            FailureKind.timeout, f"network error calling {safe_url(url)}: {exc}",
            detail={"url": safe_url(url)},
            disposition=disposition_for_transport_error(exc),
        ) from exc
    if 300 <= status < 400:
        _refuse_redirect(method, url, status, resp_headers)
    return HttpResponse(status, resp_headers, payload)


def _refuse_redirect(method: str, url: str, status: int, headers: dict) -> None:
    """A 30x that :class:`CredentialSafeRedirectHandler` declined to follow.

    Only reachable for a non-GET/HEAD method (a paid POST) or a redirect chain
    urllib could not complete. For a POST the send boundary is already crossed —
    the remote side saw the request — so the disposition is OUTCOME_UNKNOWN and
    the shot fails CLOSED rather than being re-submitted (and re-charged) on the
    assumption that a redirect means "nothing happened"."""
    from .submission import NOT_DISPATCHED, OUTCOME_UNKNOWN_DISPOSITION

    location = headers.get("Location") or headers.get("location") or ""
    sent = method.upper() not in ("GET", "HEAD")
    raise ProviderFailure(
        FailureKind.provider_error,
        f"refusing to follow the HTTP {status} redirect for {method} "
        f"{safe_url(url)} -> {safe_url(location) or '<no location>'} — a paid "
        "request is never silently re-issued as a credential-less GET "
        "(PROVIDER-NET-001)",
        detail={"url": safe_url(url), "status": status,
                "location": safe_url(location)},
        disposition=OUTCOME_UNKNOWN_DISPOSITION if sent else NOT_DISPATCHED,
    )


def _write_bytes_atomic(dest: Path, data: bytes) -> None:
    """Write ``data`` to ``dest`` via temp-file + durable replace (P2-2).

    Same crash-safety story as ``yamlio.atomic_write_text`` / ffmpeg
    ``atomic_output``: fsync the temp, then ``replace_with_retry`` for Windows
    AV/indexer sharing violations. Shared by generic_cloud/tts/comfyui.
    """
    import uuid

    from ..core.yamlio import replace_with_retry

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(tmp, dest)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# Content types a MEDIA download may legitimately carry. PROVIDER-DOWNLOAD-002:
# this is an ALLOWLIST, not an html blacklist — an expired signed URL that
# answers 200 with `application/json {"error": ...}` (S3/GCS/most job APIs do
# exactly that) used to sail straight past the old `text/html`-only check and be
# written to disk, then registered as a SUCCESSFUL paid take. An unset/blank
# content type stays permitted (many CDNs omit it, and every offline test
# fixture does) — the magic-byte check below is what covers that case.
_MEDIA_CONTENT_TYPES = (
    "video/", "audio/", "image/", "application/octet-stream",
    "application/binary", "application/force-download", "application/download",
    "application/mp4", "application/ogg", "application/zip", "binary/",
)
# Leading bytes that prove the payload is a TEXT document (a JSON/XML/HTML error
# body), never media. Checked whatever the declared content type says, because a
# CDN error page is perfectly capable of claiming `video/mp4`.
_TEXT_BODY_PREFIXES = (b"{", b"[", b"<")


def reject_html_error_page(resp: "HttpResponse", url: str, provider_id: str) -> None:
    """A "successful" (2xx) download that is actually an ERROR DOCUMENT — an
    expired signed URL, a CDN error page, a login wall, a job API's
    ``{"error": ...}`` — must never be written to disk as if it were the
    promised media (#43/#44/#54, PROVIDER-DOWNLOAD-002).

    Two independent gates, either of which refuses:

    * the declared content type, when present, must name a MEDIA type
      (:data:`_MEDIA_CONTENT_TYPES`) — an allowlist, so ``application/json``,
      ``text/plain`` and friends are caught, not only ``text/html``;
    * the first non-whitespace byte must not open a JSON/HTML/XML document
      (:data:`_TEXT_BODY_PREFIXES`) — which catches an error body that lies
      about its content type.

    Raises with a short verbatim snippet so the failure is diagnosable from
    evidence alone. The url is scrubbed (:func:`safe_url`) — a signed result URL
    must not ride into ``reports/failures.jsonl`` (PROVIDER-PRIV-001)."""
    raw = (resp.headers.get("Content-Type") or resp.headers.get("content-type") or "")
    ctype = raw.split(";", 1)[0].strip().lower()
    bad: str | None = None
    if ctype and not ctype.startswith(_MEDIA_CONTENT_TYPES):
        bad = f"content type {ctype!r} is not a media type"
    elif resp.body.lstrip()[:1] in _TEXT_BODY_PREFIXES:
        bad = "the body opens as a JSON/HTML/XML document, not media"
    if bad is None:
        return
    snippet = " ".join(resp.text()[:300].split())
    raise ProviderFailure(
        FailureKind.provider_error,
        f"{provider_id}: download from {safe_url(url)} is not media — {bad} "
        f"(likely an error page) — snippet: {snippet!r}",
        detail={"url": safe_url(url), "content_type": ctype, "reason": bad},
    )


def _placeholder_map(req: GenerationRequest) -> dict[str, object]:
    """Values available to body_template placeholders. Everything the design
    names (§8.6) plus the shot's generation params verbatim.

    DR04: the base key set here IS
    ``providers.preflight.BASE_PLACEHOLDER_KEYS`` — the single vocabulary the
    spend-free preflight checks a body_template's completeness against (their
    equality is pinned by test_dr04_preflight). Keep the two in lock-step."""
    from .prompt import compile_prompt

    req.validate_recipe()
    config = req.project.load_config()
    refset = req.refset()
    values: dict[str, object] = dict(req.params)
    values.update({
        "prompt": compile_prompt(req.shot, req.bible, refset=refset),
        "duration_s": req.duration_ms / 1000.0,
        "duration_ms": req.duration_ms,
        "width": config.width,
        "height": config.height,
        "fps": config.fps,
        "seed": req.params.get("seed", 0),
        "shot_id": req.shot.id,
    })
    return values


def _one_or_list(values: list, max_n: int) -> object:
    """A single-ref field carries the scalar; a multi-ref field carries a list."""
    return values[0] if max_n == 1 and len(values) == 1 else values


def _first_last_keyframes(
    shot: ShotSpec, duration_ms: int
) -> tuple[KeyframeSpec | None, KeyframeSpec | None]:
    """The shot's start + end keyframes (goal item 12), or ``(None, None)``.

    A keyframe is the START when ``position == start`` (or ``at_ms <= 0``) and the
    END when ``position == end`` (or ``at_ms >= duration_ms``). Reads the additive
    ``keyframes`` field tolerantly (typed KeyframeSpec objects, or raw dicts on a
    directly-built request) so a request assembled either way sees the same
    frames. A shot with no keyframes yields ``(None, None)`` → no delivery."""
    raw = getattr(shot, "keyframes", None) or []
    kfs: list[KeyframeSpec] = []
    for entry in raw:
        if isinstance(entry, KeyframeSpec):
            kfs.append(entry)
        else:
            try:
                kfs.append(KeyframeSpec.model_validate(entry))
            except Exception:
                continue
    start = next((k for k in kfs if k.position == "start"), None)
    if start is None:
        start = next((k for k in kfs if k.at_ms is not None and k.at_ms <= 0), None)
    end = next((k for k in kfs if k.position == "end"), None)
    if end is None and duration_ms:
        end = next((k for k in kfs
                    if k.at_ms is not None and k.at_ms >= duration_ms), None)
    return start, end


def _body_as_fields(body: dict) -> dict[str, str]:
    """Flatten a rendered JSON body to multipart text fields — scalars pass
    through as strings, containers are JSON-encoded so the file part rides
    alongside every body value (goal item 7, multipart mode)."""
    fields: dict[str, str] = {}
    for key, value in body.items():
        if isinstance(value, (dict, list)):
            fields[key] = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            fields[key] = "true" if value else "false"
        else:
            fields[key] = str(value)
    return fields


def render_body(template: object, values: dict[str, object]) -> object:
    """Recursively substitute {placeholders} in template strings.

    A string that is exactly one placeholder ("{duration_s}") becomes the
    TYPED value (float/int), so numeric API fields stay numeric; strings with
    embedded placeholders are formatted as text. Unknown placeholders raise —
    a silently empty prompt field must never reach a paid API.
    """
    if isinstance(template, dict):
        return {k: render_body(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [render_body(v, values) for v in template]
    if isinstance(template, str):
        stripped = template.strip()
        if stripped.startswith("{") and stripped.endswith("}") and stripped.count("{") == 1:
            key = stripped[1:-1]
            if key not in values:
                raise ProviderFailure(
                    FailureKind.invalid,
                    f"body_template placeholder {{{key}}} has no value "
                    f"(available: {sorted(map(str, values))})",
                )
            return values[key]
        try:
            return template.format(**values)
        except (KeyError, IndexError) as exc:
            raise ProviderFailure(
                FailureKind.invalid, f"body_template placeholder {exc} has no value"
            ) from None
    return template


class GenericCloudProvider(CloudProvider):
    """CloudProvider driven entirely by a ProviderManifest (§8.6)."""

    kind = "cloud"

    def __init__(self, manifest: ProviderManifest, *, transport: Transport | None = None,
                 clock: Callable[[], float] = time.monotonic, **kwargs):
        if manifest.adapter != GENERIC_ADAPTER:
            raise ValueError(f"manifest {manifest.id} is not a generic_cloud manifest")
        problems = manifest.validate_for_generic()
        # a missing key is a runtime concern (submit-time), not a construction error
        hard = [p for p in problems if "key_env" not in p]
        if hard:
            raise ValueError(f"manifest {manifest.id} invalid: {'; '.join(hard)}")
        super().__init__(**kwargs)
        self.manifest = manifest
        self.id = manifest.id
        self._transport = transport or default_transport
        self._clock = clock
        self._last_submit: float | None = None
        self._results: dict[str, dict] = {}  # job_id -> last successful poll info
        # #53: the duration/candidates THIS job was submitted with, so poll()'s
        # cost fallback (no cost_path in the response) can price per_call +
        # per_second×duration instead of per_call alone. #8: a lock so
        # concurrent generate() calls sharing this cached provider instance
        # can't race _throttle()'s read-sleep-write into a burst that exceeds
        # rate_limit_per_min.
        self._submitted: dict[str, dict] = {}
        self._execution_profiles_by_job: dict[str, ProviderExecutionProfile] = {}
        self._poll_urls_by_job: dict[str, str] = {}
        self._throttle_lock = threading.Lock()

    # ------------------------------------------------------------ plumbing

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        auth = self.manifest.auth
        if auth.key_env:
            key = os.environ.get(auth.key_env)
            if not key:
                raise ProviderFailure(
                    FailureKind.invalid,
                    f"{self.id}: API key env var {auth.key_env} is not set "
                    "(keys never live in the project, §8.2)",
                )
            name, _, value = auth.header.partition(":")
            headers[name.strip()] = value.strip().format(key=key)
        headers.update(extra or {})
        return headers

    @staticmethod
    def _headers_for_payload(payload: PreparedProviderPayload) -> dict[str, str]:
        """Build auth headers from the config snapshot sealed with the payload."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if payload.auth_key_env:
            key = os.environ.get(payload.auth_key_env)
            if not key:
                raise ProviderFailure(
                    FailureKind.invalid,
                    f"{payload.provider_id}: API key env var {payload.auth_key_env} "
                    "is not set (keys never live in the project, §8.2)",
                )
            name, _, value = payload.auth_header.partition(":")
            headers[name.strip()] = value.strip().format(key=key)
        headers.update(dict(payload.extra_headers))
        return headers

    def _classify_body(
        self, text: str, markers: tuple[str, ...] | None = None
    ) -> FailureKind:
        """§8.1: content review rejection is first-class — never retried."""
        if markers is None:
            markers = tuple(self.manifest.failure.content_rejected_when)
        for marker in markers:
            if marker in text:
                return FailureKind.content_rejected
        return FailureKind.provider_error

    def _with_idempotency_header(self, headers: dict[str, str],
                                 req: GenerationRequest) -> dict[str, str]:
        """DR06 ruling 10: inject the derived idempotency key into the declared
        header ONLY when the manifest opts in (``submission.idempotency.mode ==
        header``) AND base has derived a key for this submission. Old manifests
        (no ``submission`` section) and direct provider tests are byte-identical —
        no header is added. The key is a sha256 over (namespace, provider_id,
        submission_id): stable across re-dispatch attempts, carrying no secret."""
        sub = getattr(self.manifest, "submission", None)
        idem = getattr(sub, "idempotency", None) if sub is not None else None
        key = getattr(req, "submission_idempotency_key", None)
        if idem is not None and getattr(idem, "mode", None) == "header" \
                and getattr(idem, "field", None) and key:
            headers = {**headers, idem.field: key}
        return headers

    def _throttle(self) -> None:
        """Engine-side rate limiting (§8.2): we do not rely on remote 429s.

        Goal 8: a lock serializes the read-sleep-write sequence, so concurrent
        ``generate()`` calls sharing this cached provider instance (goal-14
        concurrent build) cannot race two near-simultaneous reads of
        ``_last_submit`` into a burst that exceeds ``rate_limit_per_min`` — a
        thread waiting on the lock is itself correctly spaced from whoever
        holds it.

        SCOPE (honest, review #6): ``rate_limit_per_min`` is a BEST-EFFORT
        PER-PROCESS throttle, NOT a hard cross-process quota. Two separate
        ``manju`` processes each throttle independently, so their combined
        submit rate can exceed the per-minute figure. That is a deliberate
        design choice for a single-user local tool — a true distributed token
        bucket (shared persistent state + a cross-process lock on every submit)
        is not worth the machinery here. The real backstop against exceeding a
        provider's quota is the remote 429, which we catch as
        ``FailureKind.rate_limited`` and retry with backoff (see ``poll`` /
        ``submit`` and providers/base.py). ``max_concurrent`` IS hard-enforced
        (a semaphore); the per-minute rate is the soft one."""
        per_min = self.manifest.limits.rate_limit_per_min
        if per_min <= 0:
            return
        min_interval = 60.0 / per_min
        with self._throttle_lock:
            if self._last_submit is not None:
                wait = min_interval - (self._clock() - self._last_submit)
                if wait > 0:
                    self._sleep(wait)
            self._last_submit = self._clock()

    def _rendered_request_facts(self, req: GenerationRequest) -> dict:
        """Canonical facts from the same payload transport will consume."""
        try:
            payload = self._prepared_provider_payload(req)
        except ProviderFailure as exc:
            if exc.disposition is None:
                from .submission import NOT_DISPATCHED

                exc.disposition = NOT_DISPATCHED
            raise
        return {
            **payload.semantic_facts(),
            "digest": payload.semantic_digest,
            "_ref_content_sha256": {
                (kind, physical): digest
                for kind, physical, digest in payload.ref_content_sha256
            },
        }

    def _execution_profile(
        self, req: GenerationRequest | None = None
    ) -> ProviderExecutionProfile:
        if req is not None:
            return self._prepared_provider_payload(req).execution_profile
        return ProviderExecutionProfile.from_manifest(self.manifest)

    def _activate_recovery_profile(
        self,
        req: GenerationRequest,
        job_id: str,
        profile: ProviderExecutionProfile,
    ) -> None:
        self._execution_profiles_by_job[str(job_id)] = profile

    def _prepared_provider_payload(
        self, req: GenerationRequest
    ) -> PreparedProviderPayload:
        cache = getattr(req, "_prepared_provider_payloads", None)
        if cache is None:
            cache = req._prepared_provider_payloads = {}
        cached = cache.get(self.id)
        if cached is not None:
            return cached

        self._ensure_provider_request(req)
        cfg = self.manifest.submit
        assert cfg is not None
        self._enforce_limits(req.shot, req.duration_ms)
        rendered = render_body(cfg.body_template, _placeholder_map(req))
        blob_cache: dict[tuple[str, str], bytes] = {}
        delivery: dict = {}
        raw_files = self._deliver_refs(
            rendered, req, record=False, blob_cache=blob_cache,
            delivery_out=delivery,
        )
        files = tuple(
            PreparedFile(
                field=field,
                filename=filename,
                payload=bytes(raw),
                content_sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
            )
            for field, filename, raw in raw_files
        )
        facts = {
            "method": cfg.method.upper(),
            "body": rendered,
            "files": [
                {
                    "field": item.field,
                    "filename": item.filename,
                    "content_sha256": item.content_sha256,
                }
                for item in files
            ],
        }
        auth = self.manifest.auth
        sub = getattr(self.manifest, "submission", None)
        idem = getattr(sub, "idempotency", None) if sub is not None else None
        idempotency_field = (
            getattr(idem, "field", None)
            if getattr(idem, "mode", None) == "header" else None
        )
        execution_profile = ProviderExecutionProfile.from_manifest(self.manifest)
        payload = PreparedProviderPayload(
            provider_id=self.id,
            method=cfg.method.upper(),
            url=str(cfg.url),
            rendered_body_json=json.dumps(rendered, ensure_ascii=False).encode("utf-8"),
            multipart_fields=tuple(_body_as_fields(rendered).items()),
            files=files,
            semantic_digest=hash_value(facts),
            ref_content_sha256=tuple(
                (kind, physical, "sha256:" + hashlib.sha256(raw).hexdigest())
                for (kind, physical), raw in blob_cache.items()
            ),
            delivery_evidence_json=json.dumps(
                delivery, ensure_ascii=False, sort_keys=True
            ).encode("utf-8"),
            extra_headers=tuple((str(k), str(v)) for k, v in cfg.extra_headers.items()),
            auth_key_env=auth.key_env,
            auth_header=auth.header,
            idempotency_field=idempotency_field,
            execution_profile=execution_profile,
        )
        cache[self.id] = payload
        return payload

    # ---------------------------------------------------- CloudProvider API

    def submit(self, req: GenerationRequest) -> str:
        from .submission import (
            NOT_DISPATCHED,
            OUTCOME_UNKNOWN_DISPOSITION,
            disposition_for_status,
        )

        cfg = self.manifest.submit
        assert cfg is not None
        # P0 WP2: the provider's DECLARED definite-rejection statuses (else empty
        # = declare nothing). A post-send status is DEFINITELY_REJECTED ONLY if it
        # is in this set; every other >= 400 is conservatively OUTCOME_UNKNOWN.
        profile = self._prepared_provider_payload(req).execution_profile
        declared = frozenset(profile.definite_rejection_statuses)
        # Everything BEFORE the transport call is provably-not-sent: a duration
        # cap, a render/refs error, a missing key, the engine-side throttle. DR06
        # marks these NOT_DISPATCHED so the admission machine records
        # REJECTED_PRE_DISPATCH (retry allowed) — and so the WP2 submit-phase
        # default (OUTCOME_UNKNOWN) never swallows a provably-not-sent failure.
        try:
            payload = self._prepared_provider_payload(req)
            req.params["ref_delivery"] = payload.delivery_evidence
            if payload.files:
                content_type, body = encode_multipart(
                    dict(payload.multipart_fields),
                    [(item.field, item.filename, item.payload) for item in payload.files],
                )
                headers = self._headers_for_payload(payload)
                headers["Content-Type"] = content_type
            else:
                body = payload.rendered_body_json
                headers = self._headers_for_payload(payload)
            # ruling 10: inject the derived idempotency key ONLY when the manifest
            # declares an idempotency header (req.submission_idempotency_key is set
            # by base._prepare_submission for a declared provider, else None).
            key = getattr(req, "submission_idempotency_key", None)
            if payload.idempotency_field and key:
                headers[payload.idempotency_field] = key
            # the engine-side throttle is pre-network too — keep it inside the
            # provably-not-sent boundary so any refusal here is NOT_DISPATCHED.
            self._throttle()
        except ProviderFailure as exc:
            if exc.disposition is None:
                exc.disposition = NOT_DISPATCHED  # pre-transport = provably not sent
            raise
        except Exception as exc:
            # A RAW exception here (a bad auth.header template's KeyError, an
            # unserializable rendered body's TypeError, …) is STILL provably
            # pre-send — letting it escape meant base.py's choke-point default
            # classified it OUTCOME_UNKNOWN and permanently fail-closed the
            # shot ("may be billing remotely") over a local config typo.
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: submit preparation failed before any network "
                f"({type(exc).__name__}: {exc})",
                detail={"error_class": type(exc).__name__},
                disposition=NOT_DISPATCHED,
            ) from exc
        resp = self._transport(payload.method, payload.url, headers, body)
        # From here the send boundary is crossed — a failure's disposition is
        # DEFINITELY_REJECTED only for a MANIFEST-DECLARED status, else UNKNOWN
        # (no global 429/tested-4xx assumption). The FailureKind mapping is
        # unchanged (kind stays honest; disposition is the safety bit).
        if resp.status == 429:
            raise ProviderFailure(
                FailureKind.rate_limited, f"{self.id}: remote rate limit (429)",
                detail={"status": 429, "body": resp.text()[:500]},
                disposition=disposition_for_status(429, declared),
            )
        if resp.status >= 400:
            kind = self._classify_body(resp.text(), profile.failure_content_markers)
            raise ProviderFailure(
                kind, f"{self.id}: submit failed with HTTP {resp.status}",
                detail={"status": resp.status, "body": resp.text()[:2000]},
                disposition=disposition_for_status(resp.status, declared),
            )
        try:
            job_id = extract(resp.json(), profile.job_id_path)
        except (JsonPathError, json.JSONDecodeError) as exc:
            # a 2xx we cannot read a job id out of: the server accepted the
            # request (a job MAY exist remotely) but we cannot track it —
            # genuinely ambiguous, so OUTCOME_UNKNOWN, never a safe retry/fallback.
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: cannot read job id ({exc})",
                detail={"status": resp.status, "body": resp.text()[:2000]},
                disposition=OUTCOME_UNKNOWN_DISPOSITION,
            ) from exc
        # #53: remember this job's duration for poll()'s cost fallback. #29:
        # this adapter's submit/poll/download path always returns exactly ONE
        # result per job (one result_url, one downloaded file) — the fallback
        # cost is priced for 1, never req.candidates, matching what is
        # actually produced/billed.
        job_id = str(job_id)
        self._submitted[job_id] = {"duration_ms": req.duration_ms}
        self._execution_profiles_by_job[job_id] = profile
        if self.manifest.poll is not None:
            self._poll_urls_by_job[job_id] = str(self.manifest.poll.url)
        return job_id

    def poll(self, job_id: str) -> tuple[str, dict]:
        profile = self._execution_profiles_by_job.get(str(job_id)) or self._execution_profile()
        if profile.poll_endpoint_template is None or profile.poll_status_path is None:
            raise ProviderFailure(
                FailureKind.invalid, f"{self.id}: execution profile has no poll contract"
            )
        url_template = self._poll_urls_by_job.get(str(job_id), profile.poll_endpoint_template)
        url = url_template.format(job_id=job_id)
        resp = self._transport("GET", url, self._headers(), None)
        text = resp.text()
        if resp.status == 429:
            return "running", {}  # remote asked us to back off; keep polling
        if resp.status >= 500:
            # F6: a transient 5xx (502/503/504) during ONE poll GET is NOT a
            # terminal job failure — the remote job may still be running/billing.
            # Keep polling within the EXISTING timeout budget
            # (base._poll_to_completion enforces it, so a 5xx that never clears
            # becomes the ordinary poll-timeout, never an infinite loop) instead
            # of killing an otherwise-good PAID job on a single blip. A 4xx below
            # stays terminal (a definite client error, already-handled 429 aside).
            return "running", {}
        if resp.status >= 400:
            return "failed", {
                "failure_kind": self._classify_body(
                    text, profile.failure_content_markers
                ).value,
                "reason": text[:2000],
            }
        try:
            data = resp.json()
            raw_status = str(extract(data, profile.poll_status_path))
        except (JsonPathError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            # UnicodeDecodeError is a ValueError SIBLING of json.JSONDecodeError,
            # not a subclass, so it was NOT caught here — a 2xx poll of an
            # already-billed job whose body is not valid UTF-8 (a corporate proxy
            # interstitial, a truncated multibyte chunk) crashed generate()/the
            # build instead of returning the graceful provider_error below.
            return "failed", {"failure_kind": FailureKind.provider_error.value,
                              "reason": f"cannot read status: {exc}; body={text[:500]}"}
        status = dict(profile.poll_status_map).get(raw_status, raw_status.lower())
        if status not in JOB_STATES:
            return "failed", {
                "failure_kind": FailureKind.provider_error.value,
                "reason": f"unmapped remote status {raw_status!r} "
                          f"(add it to poll.status_map)",
            }
        info: dict = {"raw_status": raw_status}
        if status == "failed":
            reason = text
            if profile.poll_reason_path:
                try:
                    reason = str(extract(data, profile.poll_reason_path))
                except JsonPathError:
                    pass
            # §8.1: rejection reason FULL TEXT goes to the run log via detail
            info.update({"failure_kind": self._classify_body(
                text, profile.failure_content_markers
            ).value,
                         "reason": reason})
        if status == "succeeded":
            if profile.poll_result_url_path:
                try:
                    info["result_url"] = str(extract(data, profile.poll_result_url_path))
                except JsonPathError as exc:
                    return "failed", {"failure_kind": FailureKind.provider_error.value,
                                      "reason": f"job succeeded but {exc}"}
            if profile.poll_cost_path:
                try:
                    info["cost"] = float(extract(data, profile.poll_cost_path))
                    info["currency"] = self.manifest.cost.currency
                except (JsonPathError, TypeError, ValueError):
                    pass
            if "cost" not in info:
                # #53: fall back to the manifest's price sheet using the SAME
                # per_call + per_second×duration shape estimate_cost() uses —
                # per_call alone under-reports a per-second-priced provider.
                # Candidates is always 1 here (#29: this adapter never returns
                # more than one result per job).
                sub = self._submitted.get(job_id) or {}
                duration_s = float(sub.get("duration_ms") or 0) / 1000.0
                info["cost"] = round(
                    self.manifest.cost.per_call
                    + self.manifest.cost.per_second * duration_s, 6)
                info["currency"] = self.manifest.cost.currency
            self._results[job_id] = info
        return status, info

    def download(self, job_id: str, dest_dir: Path) -> list[Path]:
        # PROVIDER-DOWNLOAD-001: the cached poll info of an ALREADY PAID job is
        # read here, NOT popped — the pop is deferred until the bytes are on
        # disk. Popping first meant a transient download failure threw away the
        # only copy of the result URL, so the retry inside this same generate()
        # had nothing to fetch and the job died with the money already spent.
        info = self._results.get(job_id) or {}
        url = info.get("result_url")
        if not url:
            self._results.pop(job_id, None)
            self._submitted.pop(job_id, None)
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: job {job_id} has no result URL "
                "(set poll.result_url_path in the manifest)",
                detail={"job_id": job_id},
            )
        resp = self._transport("GET", url, {}, None)
        if resp.status >= 400 or not resp.body:
            # PROVIDER-DOWNLOAD-001: a 429/503 on the DOWNLOAD of a job we have
            # ALREADY PAID FOR is transient, not terminal. Hardcoding
            # provider_error here made base.generate() give up, mark the
            # submission TERMINAL_FAILURE, and let the NEXT build open a fresh
            # submission — i.e. pay a second time for media that already exists
            # remotely. `status_to_kind` is the one owner of that mapping (F4),
            # so a 429 stays retryable exactly like it does on submit/poll; a
            # transient 5xx is reported as `timeout`, the other retryable kind.
            kind = status_to_kind(resp.status)
            if kind is FailureKind.provider_error and is_transient_status(resp.status):
                kind = FailureKind.timeout
            raise ProviderFailure(
                kind,
                f"{self.id}: download failed with HTTP {resp.status}",
                detail={"url": safe_url(url), "job_id": job_id,
                        "status": resp.status},
            )
        # #43/#44 + PROVIDER-DOWNLOAD-002: a 200 that is actually an error
        # document (expired signed URL, CDN error page, a JSON {"error": …})
        # must never be written to disk as if it were the promised media.
        reject_html_error_page(resp, url, self.id)
        suffix = Path(url.split("?", 1)[0]).suffix or ".mp4"
        dest = Path(dest_dir) / f"result{suffix}"
        _write_bytes_atomic(dest, resp.body)
        # F3: only NOW is this the terminal read of the cached poll info — drop
        # both per-job dicts so a registry-cached provider instance (one per
        # manifest, reused across a build of hundreds of shots or a long-lived
        # MCP/GUI host) does not accumulate them for the process lifetime.
        self._results.pop(job_id, None)
        self._submitted.pop(job_id, None)
        return [dest]

    # --------------------------------------------------------- refs delivery

    def _deliver_refs(
        self, body: dict, req: GenerationRequest, *, record: bool = True,
        blob_cache: dict[tuple[str, str], bytes] | None = None,
        delivery_out: dict | None = None,
    ) -> list[tuple[str, str, bytes]]:
        """Deliver the refs, converting a TOCTOU refusal into a typed failure.

        ``refs.read_ref_bytes`` re-verifies the ref on the OPEN descriptor and
        raises :class:`~manju.core.safeio.SafeOutError` when the file was swapped
        for a link / a non-regular file / an out-of-project target between
        resolution and upload. That is a LOCAL, provably pre-network refusal, so
        it must surface as ``ProviderFailure(invalid)`` — letting a raw
        SafeOutError escape would hit base.py's unclassified-exception default
        and fail the shot CLOSED as OUTCOME_UNKNOWN ("may be billing remotely")
        for something that never left the machine."""
        from ..core.safeio import SafeOutError

        if blob_cache is None:
            blob_cache = {}
        try:
            return self._deliver_refs_verified(
                body, req, record=record, blob_cache=blob_cache,
                delivery_out=delivery_out,
            )
        except SafeOutError as exc:
            raise ProviderFailure(FailureKind.invalid, f"{self.id}: {exc}") from exc

    def _deliver_refs_verified(
        self, body: dict, req: GenerationRequest, *, record: bool = True,
        blob_cache: dict[tuple[str, str], bytes],
        delivery_out: dict | None = None,
    ) -> list[tuple[str, str, bytes]]:
        """Inject reference inputs into the rendered ``body`` per ``manifest.refs``.

        Returns multipart file parts (empty unless ``image_mode: multipart``).
        Records the delivery lineage on ``req.params['ref_delivery']`` so it is
        archived on the take. Raises ``invalid`` for a missing/unreadable local
        ref (naming path + tier) or a local path handed to a URL field (naming
        the manifest field) — all BEFORE the paid POST (reliability #3).

        Reference budget (goal item 9): when ``limits.max_ref_images`` /
        ``max_ref_videos`` are set, the refs are first passed through the
        deterministic, priority-ordered allocator; only the budget-allocated
        subset is delivered and ``ref_delivery.budget`` records both what was
        selected and what was omitted (each with a 中文 reason). With NO budget
        configured the allocator returns the refs in their resolved order with
        zero omissions, so the delivered bytes AND the ``ref_delivery`` block are
        byte-identical to before (pinned)."""
        rc = self.manifest.refs
        self._ensure_provider_request(req)
        refset = req.refset()
        plan = req.delivery_plan
        assert plan is not None
        delivery: dict = plan.evidence_base()
        files: list[tuple[str, str, bytes]] = []

        if rc.image_mode != "none" and rc.max_images > 0:
            logical_items = list(plan.selected("image"))
            seen_blobs: set[tuple[str, str]] = set()
            items = []
            for item in logical_items:
                key = physical_ref_key(item)
                if key not in seen_blobs:
                    seen_blobs.add(key)
                    items.append(item)
            if items:
                self._guard_readable(items)  # local refs must exist (pre-submit)
                if rc.image_mode == "base64_field":
                    values = [
                        self._base64_prepared_ref(
                            self._require_local(it, rc.image_mode), blob_cache,
                            data_uri=rc.data_uri, mime=rc.mime,
                        )
                        for it in items
                    ]
                    assign(body, rc.field, _one_or_list(values, rc.max_images))
                elif rc.image_mode == "url_field":
                    urls = [self._require_url(it, rc.field) for it in items]
                    assign(body, rc.field, _one_or_list(urls, rc.max_images))
                elif rc.image_mode == "multipart":
                    for i, it in enumerate(items):
                        it = self._require_local(it, rc.image_mode)
                        fld = rc.multipart_field if len(items) == 1 \
                            else f"{rc.multipart_field}{i}"
                        files.append((fld, it.path.name,
                                      self._prepared_ref_bytes(it, blob_cache)))
                delivery["images"] = [
                    {
                        "ref": it.ref,
                        "tier": it.tier,
                        "delivered_as": rc.image_mode,
                        **({"controls": list(it.controls),
                            "ignore": list(it.ignore),
                            "subject_ref": it.subject_ref}
                           if it.declared_transfer or it.transfer_errors else {}),
                    }
                    for it in logical_items
                ]

        if rc.video_mode == "url_field" and rc.max_videos > 0:
            logical_vitems = list(plan.selected("video"))
            seen_videos: set[tuple[str, str]] = set()
            vitems = []
            for item in logical_vitems:
                key = physical_ref_key(item)
                if key not in seen_videos:
                    seen_videos.add(key)
                    vitems.append(item)
            if vitems:
                urls = [self._require_url(it, rc.video_field) for it in vitems]
                assign(body, rc.video_field, _one_or_list(urls, rc.max_videos))
                delivery["videos"] = [
                    {
                        "ref": it.ref,
                        "tier": it.tier,
                        "delivered_as": "url_field",
                        **({"controls": list(it.controls),
                            "ignore": list(it.ignore),
                            "subject_ref": it.subject_ref}
                           if it.declared_transfer or it.transfer_errors else {}),
                    }
                    for it in logical_vitems
                ]

        # Only a CONFIGURED budget adds the audit block — otherwise the
        # ref_delivery dict is byte-identical to today (byte-identity contract).
        # First/last-frame task (goal item 12, round U): a shot with keyframes at
        # start AND end whose images resolve to real local files, routed to a
        # provider that advertises `first_last_frame`, delivers both per the
        # `refs.first_last_*` mapping. Folds into the SAME ref_delivery lineage.
        files += self._deliver_first_last(
            body, req, delivery, blob_cache=blob_cache
        )

        if record:
            req.params["ref_delivery"] = delivery
        if delivery_out is not None:
            delivery_out.clear()
            delivery_out.update(delivery)
        return files

    def _deliver_first_last(
        self, body: dict, req: GenerationRequest, delivery: dict, *,
        blob_cache: dict[tuple[str, str], bytes] | None = None,
    ) -> list[tuple[str, str, bytes]]:
        """First/last-frame delivery, with the same TOCTOU→``invalid`` guard
        :meth:`_deliver_refs` applies (this method is also reachable on its own)."""
        from ..core.safeio import SafeOutError

        if blob_cache is None:
            blob_cache = {}
        try:
            return self._deliver_first_last_verified(
                body, req, delivery, blob_cache=blob_cache
            )
        except SafeOutError as exc:
            raise ProviderFailure(FailureKind.invalid, f"{self.id}: {exc}") from exc

    def _deliver_first_last_verified(
        self, body: dict, req: GenerationRequest, delivery: dict, *,
        blob_cache: dict[tuple[str, str], bytes],
    ) -> list[tuple[str, str, bytes]]:
        """Deliver the shot's start+end keyframe images as a first/last-frame task.

        No-op (returns ``[]``, records nothing) unless ALL of: the manifest opts
        in with ``refs.first_last_mode != none``; it advertises the
        ``first_last_frame`` capability; the shot has a start keyframe AND an end
        keyframe; and BOTH images resolve to what the chosen encoding needs
        (existing local files for base64/multipart, URLs for url_field). Any of
        those unmet → a byte-identical request (the round-U absence contract).
        A resolvable-but-unreadable file raises ``invalid`` BEFORE the paid POST,
        exactly like the ref path (reliability #3)."""
        rc = self.manifest.refs
        if rc.first_last_mode == "none":
            return []
        if FIRST_LAST_CAPABILITY not in self.manifest.capabilities:
            return []
        start_kf, end_kf = _first_last_keyframes(req.shot, req.duration_ms)
        if start_kf is None or end_kf is None:
            return []
        start = self._resolve_keyframe(req, start_kf)
        end = self._resolve_keyframe(req, end_kf)
        if start is None or end is None:
            return []
        # goal item 17: an explicit boundary violation (absolute path / path
        # escaping the project root) is a LOUD pre-submit refusal — the same
        # treatment refs.py gives a declared-but-unreadable ref — rather than
        # the silent "feature not configured" no-op below (that stays for a
        # keyframe that is simply absent).
        for it in (start, end):
            if it.blocked_reason:
                raise ProviderFailure(FailureKind.invalid, f"{self.id}: {it.blocked_reason}")

        enc = rc.first_last_encoding
        if enc in ("base64_field", "multipart"):
            # both must be real local files (the round-U "resolve to real local
            # files" condition) — otherwise stay byte-identical
            if not (start.exists and end.exists and not start.is_url
                    and not end.is_url):
                return []
        elif enc == "url_field":
            if not (start.is_url and end.is_url):
                return []

        files: list[tuple[str, str, bytes]] = []
        if enc == "base64_field":
            self._guard_readable([start, end])  # pre-submit, zero-cost
            first_v = self._base64_prepared_ref(
                start, blob_cache, data_uri=rc.data_uri, mime=rc.mime
            )
            last_v = self._base64_prepared_ref(
                end, blob_cache, data_uri=rc.data_uri, mime=rc.mime
            )
            self._assign_first_last(body, rc, first_v, last_v)
        elif enc == "url_field":
            self._assign_first_last(body, rc, start.ref, end.ref)
        elif enc == "multipart":
            self._guard_readable([start, end])
            assert start.path is not None and end.path is not None
            files.append((rc.first_last_multipart_first, start.path.name,
                          self._prepared_ref_bytes(start, blob_cache)))
            files.append((rc.first_last_multipart_last, end.path.name,
                          self._prepared_ref_bytes(end, blob_cache)))

        delivery["first_last"] = {
            "mode": rc.first_last_mode,
            "encoding": enc,
            "start": {"ref": start.ref, "role": "start", "delivered_as": enc},
            "end": {"ref": end.ref, "role": "end", "delivered_as": enc},
        }
        return files

    @staticmethod
    def _prepared_ref_bytes(
        item: RefItem, blob_cache: dict[tuple[str, str], bytes]
    ) -> bytes:
        key = physical_ref_key(item)
        if key not in blob_cache:
            blob_cache[key] = read_ref_bytes(item)
        return blob_cache[key]

    @classmethod
    def _base64_prepared_ref(
        cls, item: RefItem, blob_cache: dict[tuple[str, str], bytes], *,
        data_uri: bool, mime: str | None,
    ) -> str:
        assert item.path is not None
        encoded = base64.b64encode(
            cls._prepared_ref_bytes(item, blob_cache)
        ).decode("ascii")
        if not data_uri:
            return encoded
        guessed = mime or mimetypes.guess_type(item.path.name)[0] or "image/png"
        return f"data:{guessed};base64,{encoded}"

    def _assign_first_last(self, body: dict, rc, first_v, last_v) -> None:
        """Write the two frame values into ``body`` per the configured shape:
        ``fields`` → two JSONPaths (Kling image + image_tail); ``array`` → one
        JSONPath receiving ``[first, last]`` (Runway)."""
        if rc.first_last_mode == "fields":
            assign(body, rc.first_frame_field, first_v)
            assign(body, rc.last_frame_field, last_v)
        elif rc.first_last_mode == "array":
            assign(body, rc.frames_field, [first_v, last_v])

    def _resolve_keyframe(self, req: GenerationRequest,
                          kf: KeyframeSpec) -> RefItem | None:
        """Resolve a keyframe's ``image`` (project path / URL / bible asset
        id) to a :class:`RefItem`, or ``None`` when there is nothing to
        resolve. goal item 17: local-path resolution goes through the SAME
        ``resolve_local_ref`` containment guard refs use — an absolute path
        or one that escapes the project root is REFUSED, never read, even
        though it used to have its own independent (and leakier) resolution
        here."""
        img = (kf.image or "").strip()
        if not img:
            return None
        if img.startswith(("http://", "https://")):
            return RefItem(ref=img, tier=TIER_KEYFRAME, kind="image", path=None,
                           is_url=True, exists=True, root=req.project.root)
        # bible asset id (character/scene) → its ref_image
        entry = req.bible.get(img)
        if isinstance(entry, dict):
            for key in ("ref_image", "ref_images"):
                val = entry.get(key)
                if isinstance(val, (list, tuple)):
                    val = val[0] if val else None
                if val:
                    img = str(val)
                    break
        path, reason = resolve_local_ref(req.project, img)
        exists = bool(path and path.is_file())
        return RefItem(ref=img, tier=TIER_KEYFRAME, kind="image", path=path,
                       is_url=False, exists=exists, blocked_reason=reason,
                       root=req.project.root)

    def _guard_readable(self, items: list[RefItem]) -> None:
        msg = unreadable_ref_message(items)
        if msg is not None:
            raise ProviderFailure(FailureKind.invalid, f"{self.id}: {msg}")

    def _require_url(self, item: RefItem, field: str | None) -> str:
        if not item.is_url:
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: refs field {field!r} is a URL field but reference "
                f"{item.ref!r} (tier: {item.tier}) is a local path — host it and "
                f"reference the URL, or use refs.image_mode base64_field/multipart",
            )
        return item.ref

    def _require_local(self, item: RefItem, mode: str) -> RefItem:
        """The symmetric guard to :meth:`_require_url`: a URL ref routed into a
        bytes-delivery mode (base64_field/multipart) has no local bytes. The
        old bare ``assert item.path`` crashed with AssertionError, which base's
        raw-exception default then fail-closed as OUTCOME_UNKNOWN — a permanent
        block over a pre-send config/content mismatch."""
        if item.is_url or item.path is None:
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: refs.image_mode is {mode!r} (needs local bytes) but "
                f"reference {item.ref!r} (tier: {item.tier}) is a URL — download "
                f"it into the project first, or use refs.image_mode url_field",
            )
        return item

    # ------------------------------------------------------------- helpers

    def _enforce_limits(self, shot: ShotSpec, duration_ms: int) -> None:
        # DR04: the duration rule is now the shared, pure
        # ``preflight.duration_exceeds_limit`` — the SAME predicate the spend-free
        # preflight uses, so submit and preflight can never disagree. This guard
        # REMAINS as the final defense (a request may reach submit without having
        # been preflighted). Behaviour is byte-identical to the old
        # ``if limit and duration_ms > limit``.
        from .preflight import duration_exceeds_limit

        limit = self.manifest.limits.max_duration_ms
        if duration_exceeds_limit(limit, duration_ms):
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: shot {shot.id} needs {duration_ms}ms but the provider "
                f"caps at {limit}ms — split the shot or pick another provider",
            )
