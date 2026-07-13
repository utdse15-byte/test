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

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

from ..core.models import KeyframeSpec, ShotSpec
from .base import CloudProvider, FailureKind, GenerationRequest, ProviderFailure
from .jsonpath import JsonPathError, assign, extract
from .manifest import FIRST_LAST_CAPABILITY, GENERIC_ADAPTER, JOB_STATES, ProviderManifest
from .refs import RefItem, base64_ref, encode_multipart, resolve_local_ref, unreadable_ref_message

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
                f"response from {url} exceeded the {max_bytes}-byte cap "
                "(configurable via MANJU_MAX_DOWNLOAD_BYTES) — refusing to "
                "buffer further (goal 43, OOM guard)",
                detail={"url": url, "max_bytes": max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)


def default_transport(method: str, url: str, headers: dict[str, str],
                      body: bytes | None) -> HttpResponse:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        # #43: an unexpected scheme (file://, ftp://, data://…) is refused
        # BEFORE urlopen ever touches it — never a surprise local-file read or
        # non-HTTP protocol dial from a manifest/response-supplied URL.
        raise ProviderFailure(
            FailureKind.invalid,
            f"refusing non-http(s) URL scheme {scheme!r}: {url}",
            detail={"url": url, "scheme": scheme},
        )
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    max_bytes = _max_response_bytes()
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return HttpResponse(resp.status, dict(resp.headers),
                                _read_capped(resp, url, max_bytes))
    except urllib.error.HTTPError as exc:  # still a response — classify upstream
        return HttpResponse(exc.code, dict(exc.headers or {}),
                            _read_capped(exc, url, max_bytes))
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
            FailureKind.timeout, f"network error calling {url}: {exc}",
            detail={"url": url},
            disposition=disposition_for_transport_error(exc),
        ) from exc


def _write_bytes_atomic(dest: Path, data: bytes) -> None:
    """Write ``data`` to ``dest`` via temp-file + ``os.replace`` (#43/#44) —
    a killed process mid-write can never leave a half-written result file at
    the path the pipeline trusts. Shared by every provider download path
    (generic_cloud/tts/comfyui) so the durability guarantee is consistent."""
    import uuid

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:6]}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def reject_html_error_page(resp: "HttpResponse", url: str, provider_id: str) -> None:
    """A "successful" (2xx) download that is actually an HTML/error page — an
    expired signed URL, a CDN error page, a login wall — must never be written
    to disk as if it were the promised media (#43/#44/#54). Raises with a
    short verbatim snippet so the failure is diagnosable from evidence alone;
    a no-op when the content type is unset or genuinely not HTML."""
    ctype = (resp.headers.get("Content-Type") or resp.headers.get("content-type") or "").lower()
    if "text/html" in ctype:
        snippet = " ".join(resp.text()[:300].split())
        raise ProviderFailure(
            FailureKind.provider_error,
            f"{provider_id}: download from {url} returned text/html instead of "
            f"media (likely an error page) — snippet: {snippet!r}",
            detail={"url": url, "content_type": ctype},
        )


def _placeholder_map(req: GenerationRequest) -> dict[str, object]:
    """Values available to body_template placeholders. Everything the design
    names (§8.6) plus the shot's generation params verbatim.

    DR04: the base key set here IS
    ``providers.preflight.BASE_PLACEHOLDER_KEYS`` — the single vocabulary the
    spend-free preflight checks a body_template's completeness against (their
    equality is pinned by test_dr04_preflight). Keep the two in lock-step."""
    from .prompt import compile_prompt

    config = req.project.load_config()
    values: dict[str, object] = {
        "prompt": compile_prompt(req.shot, req.bible),
        "duration_s": req.duration_ms / 1000.0,
        "duration_ms": req.duration_ms,
        "width": config.width,
        "height": config.height,
        "fps": config.fps,
        "seed": req.params.get("seed", 0),
        "shot_id": req.shot.id,
    }
    values.update(req.params)
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

    def _classify_body(self, text: str) -> FailureKind:
        """§8.1: content review rejection is first-class — never retried."""
        for marker in self.manifest.failure.content_rejected_when:
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

    # ---------------------------------------------------- CloudProvider API

    def submit(self, req: GenerationRequest) -> str:
        from .submission import (
            NOT_DISPATCHED,
            OUTCOME_UNKNOWN_DISPOSITION,
            disposition_for_status,
        )

        cfg = self.manifest.submit
        assert cfg is not None  # validated at construction
        # P0 WP2: the provider's DECLARED definite-rejection statuses (else empty
        # = declare nothing). A post-send status is DEFINITELY_REJECTED ONLY if it
        # is in this set; every other >= 400 is conservatively OUTCOME_UNKNOWN.
        declared = frozenset(cfg.definite_rejection_statuses or ())
        # Everything BEFORE the transport call is provably-not-sent: a duration
        # cap, a render/refs error, a missing key, the engine-side throttle. DR06
        # marks these NOT_DISPATCHED so the admission machine records
        # REJECTED_PRE_DISPATCH (retry allowed) — and so the WP2 submit-phase
        # default (OUTCOME_UNKNOWN) never swallows a provably-not-sent failure.
        try:
            self._enforce_limits(req.shot, req.duration_ms)
            rendered = render_body(cfg.body_template, _placeholder_map(req))
            # Reference inputs (goal item 7): inject per the manifest `refs:`
            # section, validating every local ref is readable BEFORE this paid
            # POST. Returns any multipart file parts and records the delivery
            # lineage on req.params so it lands on the take (auditable per take).
            files = self._deliver_refs(rendered, req)
            if files:
                content_type, body = encode_multipart(_body_as_fields(rendered), files)
                headers = self._headers({**cfg.extra_headers, "Content-Type": content_type})
            else:
                body = json.dumps(rendered, ensure_ascii=False).encode("utf-8")
                headers = self._headers(cfg.extra_headers)
            # ruling 10: inject the derived idempotency key ONLY when the manifest
            # declares an idempotency header (req.submission_idempotency_key is set
            # by base._prepare_submission for a declared provider, else None).
            headers = self._with_idempotency_header(headers, req)
            # the engine-side throttle is pre-network too — keep it inside the
            # provably-not-sent boundary so any refusal here is NOT_DISPATCHED.
            self._throttle()
        except ProviderFailure as exc:
            if exc.disposition is None:
                exc.disposition = NOT_DISPATCHED  # pre-transport = provably not sent
            raise
        resp = self._transport(cfg.method, cfg.url, headers, body)
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
            kind = self._classify_body(resp.text())
            raise ProviderFailure(
                kind, f"{self.id}: submit failed with HTTP {resp.status}",
                detail={"status": resp.status, "body": resp.text()[:2000]},
                disposition=disposition_for_status(resp.status, declared),
            )
        try:
            job_id = extract(resp.json(), cfg.job_id_path)
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
        self._submitted[str(job_id)] = {"duration_ms": req.duration_ms}
        return str(job_id)

    def poll(self, job_id: str) -> tuple[str, dict]:
        cfg = self.manifest.poll
        assert cfg is not None
        url = cfg.url.format(job_id=job_id)
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
                "failure_kind": self._classify_body(text).value,
                "reason": text[:2000],
            }
        try:
            data = resp.json()
            raw_status = str(extract(data, cfg.status_path))
        except (JsonPathError, json.JSONDecodeError) as exc:
            return "failed", {"failure_kind": FailureKind.provider_error.value,
                              "reason": f"cannot read status: {exc}; body={text[:500]}"}
        status = cfg.status_map.get(raw_status, raw_status.lower())
        if status not in JOB_STATES:
            return "failed", {
                "failure_kind": FailureKind.provider_error.value,
                "reason": f"unmapped remote status {raw_status!r} "
                          f"(add it to poll.status_map)",
            }
        info: dict = {"raw_status": raw_status}
        if status == "failed":
            reason = text
            if cfg.reason_path:
                try:
                    reason = str(extract(data, cfg.reason_path))
                except JsonPathError:
                    pass
            # §8.1: rejection reason FULL TEXT goes to the run log via detail
            info.update({"failure_kind": self._classify_body(text).value,
                         "reason": reason})
        if status == "succeeded":
            if cfg.result_url_path:
                try:
                    info["result_url"] = str(extract(data, cfg.result_url_path))
                except JsonPathError as exc:
                    return "failed", {"failure_kind": FailureKind.provider_error.value,
                                      "reason": f"job succeeded but {exc}"}
            if cfg.cost_path:
                try:
                    info["cost"] = float(extract(data, cfg.cost_path))
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
        # F3: download() is the TERMINAL read of this job's cached poll info —
        # once here, polling is done and this generate() call will not poll the
        # id again (a later resume re-polls fresh, re-populating). Pop BOTH
        # per-job dicts so a registry-cached provider instance (one per manifest,
        # reused across a build of hundreds of shots or a long-lived MCP/GUI
        # host) does not accumulate them monotonically for the process lifetime.
        info = self._results.pop(job_id, None) or {}
        self._submitted.pop(job_id, None)
        url = info.get("result_url")
        if not url:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: job {job_id} has no result URL "
                "(set poll.result_url_path in the manifest)",
                detail={"job_id": job_id},
            )
        resp = self._transport("GET", url, {}, None)
        if resp.status >= 400 or not resp.body:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: download failed with HTTP {resp.status}",
                detail={"url": url},
            )
        # #43/#44: a 200 that is actually an HTML/error page (expired signed
        # URL, CDN error page…) must never be written to disk as if it were
        # the promised media.
        reject_html_error_page(resp, url, self.id)
        suffix = Path(url.split("?", 1)[0]).suffix or ".mp4"
        dest = Path(dest_dir) / f"result{suffix}"
        _write_bytes_atomic(dest, resp.body)
        return [dest]

    # --------------------------------------------------------- refs delivery

    def _deliver_refs(
        self, body: dict, req: GenerationRequest
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
        from .refbudget import allocate

        rc = self.manifest.refs
        refset = req.refset()
        budget = allocate(refset, self.manifest.limits, req.shot, bible=req.bible)
        delivery: dict = {"image_mode": rc.image_mode, "video_mode": rc.video_mode,
                          "images": [], "videos": []}
        files: list[tuple[str, str, bytes]] = []

        if rc.image_mode != "none" and rc.max_images > 0:
            items = budget.selected_images[: rc.max_images]
            if items:
                self._guard_readable(items)  # local refs must exist (pre-submit)
                if rc.image_mode == "base64_field":
                    values = [base64_ref(it, data_uri=rc.data_uri, mime=rc.mime)
                              for it in items]
                    assign(body, rc.field, _one_or_list(values, rc.max_images))
                elif rc.image_mode == "url_field":
                    urls = [self._require_url(it, rc.field) for it in items]
                    assign(body, rc.field, _one_or_list(urls, rc.max_images))
                elif rc.image_mode == "multipart":
                    for i, it in enumerate(items):
                        assert it.path is not None
                        fld = rc.multipart_field if len(items) == 1 \
                            else f"{rc.multipart_field}{i}"
                        files.append((fld, it.path.name, it.path.read_bytes()))
                delivery["images"] = [
                    {"ref": it.ref, "tier": it.tier, "delivered_as": rc.image_mode}
                    for it in items
                ]

        if rc.video_mode == "url_field" and rc.max_videos > 0:
            vitems = budget.selected_videos[: rc.max_videos]
            if vitems:
                urls = [self._require_url(it, rc.video_field) for it in vitems]
                assign(body, rc.video_field, _one_or_list(urls, rc.max_videos))
                delivery["videos"] = [
                    {"ref": it.ref, "tier": it.tier, "delivered_as": "url_field"}
                    for it in vitems
                ]

        # Only a CONFIGURED budget adds the audit block — otherwise the
        # ref_delivery dict is byte-identical to today (byte-identity contract).
        if budget.active:
            delivery["budget"] = budget.to_lineage()

        # First/last-frame task (goal item 12, round U): a shot with keyframes at
        # start AND end whose images resolve to real local files, routed to a
        # provider that advertises `first_last_frame`, delivers both per the
        # `refs.first_last_*` mapping. Folds into the SAME ref_delivery lineage.
        files += self._deliver_first_last(body, req, delivery)

        req.params["ref_delivery"] = delivery
        return files

    def _deliver_first_last(
        self, body: dict, req: GenerationRequest, delivery: dict
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
            first_v = base64_ref(start, data_uri=rc.data_uri, mime=rc.mime)
            last_v = base64_ref(end, data_uri=rc.data_uri, mime=rc.mime)
            self._assign_first_last(body, rc, first_v, last_v)
        elif enc == "url_field":
            self._assign_first_last(body, rc, start.ref, end.ref)
        elif enc == "multipart":
            self._guard_readable([start, end])
            assert start.path is not None and end.path is not None
            files.append((rc.first_last_multipart_first, start.path.name,
                          start.path.read_bytes()))
            files.append((rc.first_last_multipart_last, end.path.name,
                          end.path.read_bytes()))

        delivery["first_last"] = {
            "mode": rc.first_last_mode,
            "encoding": enc,
            "start": {"ref": start.ref, "role": "start", "delivered_as": enc},
            "end": {"ref": end.ref, "role": "end", "delivered_as": enc},
        }
        return files

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
                           is_url=True, exists=True)
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
                       is_url=False, exists=exists, blocked_reason=reason)

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
