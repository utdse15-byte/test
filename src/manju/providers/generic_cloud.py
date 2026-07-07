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
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, NamedTuple

from ..core.models import ShotSpec
from .base import CloudProvider, FailureKind, GenerationRequest, ProviderFailure
from .jsonpath import JsonPathError, assign, extract
from .manifest import GENERIC_ADAPTER, JOB_STATES, ProviderManifest
from .refs import RefItem, base64_ref, encode_multipart, unreadable_ref_message


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


def default_transport(method: str, url: str, headers: dict[str, str],
                      body: bytes | None) -> HttpResponse:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return HttpResponse(resp.status, dict(resp.headers), resp.read())
    except urllib.error.HTTPError as exc:  # still a response — classify upstream
        return HttpResponse(exc.code, dict(exc.headers or {}), exc.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderFailure(
            FailureKind.timeout, f"network error calling {url}: {exc}",
            detail={"url": url},
        ) from exc


def _placeholder_map(req: GenerationRequest) -> dict[str, object]:
    """Values available to body_template placeholders. Everything the design
    names (§8.6) plus the shot's generation params verbatim."""
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

    def _throttle(self) -> None:
        """Engine-side rate limiting (§8.2): we do not rely on remote 429s."""
        per_min = self.manifest.limits.rate_limit_per_min
        if per_min <= 0:
            return
        min_interval = 60.0 / per_min
        if self._last_submit is not None:
            wait = min_interval - (self._clock() - self._last_submit)
            if wait > 0:
                self._sleep(wait)
        self._last_submit = self._clock()

    # ---------------------------------------------------- CloudProvider API

    def submit(self, req: GenerationRequest) -> str:
        cfg = self.manifest.submit
        assert cfg is not None  # validated at construction
        self._enforce_limits(req.shot, req.duration_ms)
        rendered = render_body(cfg.body_template, _placeholder_map(req))
        # Reference inputs (goal item 7): inject per the manifest `refs:` section,
        # validating every local ref is readable BEFORE this paid POST. Returns
        # any multipart file parts and records the delivery lineage on req.params
        # so it lands on the take (auditable per take).
        files = self._deliver_refs(rendered, req)
        if files:
            content_type, body = encode_multipart(_body_as_fields(rendered), files)
            headers = self._headers({**cfg.extra_headers, "Content-Type": content_type})
        else:
            body = json.dumps(rendered, ensure_ascii=False).encode("utf-8")
            headers = self._headers(cfg.extra_headers)
        self._throttle()
        resp = self._transport(cfg.method, cfg.url, headers, body)
        if resp.status == 429:
            raise ProviderFailure(
                FailureKind.rate_limited, f"{self.id}: remote rate limit (429)",
                detail={"body": resp.text()[:500]},
            )
        if resp.status >= 400:
            kind = self._classify_body(resp.text())
            raise ProviderFailure(
                kind, f"{self.id}: submit failed with HTTP {resp.status}",
                detail={"status": resp.status, "body": resp.text()[:2000]},
            )
        try:
            job_id = extract(resp.json(), cfg.job_id_path)
        except (JsonPathError, json.JSONDecodeError) as exc:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: cannot read job id ({exc})",
                detail={"body": resp.text()[:2000]},
            ) from exc
        return str(job_id)

    def poll(self, job_id: str) -> tuple[str, dict]:
        cfg = self.manifest.poll
        assert cfg is not None
        url = cfg.url.format(job_id=job_id)
        resp = self._transport("GET", url, self._headers(), None)
        text = resp.text()
        if resp.status == 429:
            return "running", {}  # remote asked us to back off; keep polling
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
            if "cost" not in info:  # fall back to the manifest's price sheet
                info["cost"] = self.manifest.cost.per_call
                info["currency"] = self.manifest.cost.currency
            self._results[job_id] = info
        return status, info

    def download(self, job_id: str, dest_dir: Path) -> list[Path]:
        info = self._results.get(job_id) or {}
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
        suffix = Path(url.split("?", 1)[0]).suffix or ".mp4"
        dest = Path(dest_dir) / f"result{suffix}"
        dest.write_bytes(resp.body)
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
        the manifest field) — all BEFORE the paid POST (reliability #3)."""
        rc = self.manifest.refs
        refset = req.refset()
        delivery: dict = {"image_mode": rc.image_mode, "video_mode": rc.video_mode,
                          "images": [], "videos": []}
        files: list[tuple[str, str, bytes]] = []

        if rc.image_mode != "none" and rc.max_images > 0:
            items = refset.image_items()[: rc.max_images]
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
            vitems = refset.video_items()[: rc.max_videos]
            if vitems:
                urls = [self._require_url(it, rc.video_field) for it in vitems]
                assign(body, rc.video_field, _one_or_list(urls, rc.max_videos))
                delivery["videos"] = [
                    {"ref": it.ref, "tier": it.tier, "delivered_as": "url_field"}
                    for it in vitems
                ]

        req.params["ref_delivery"] = delivery
        return files

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
        limit = self.manifest.limits.max_duration_ms
        if limit and duration_ms > limit:
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: shot {shot.id} needs {duration_ms}ms but the provider "
                f"caps at {limit}ms — split the shot or pick another provider",
            )
