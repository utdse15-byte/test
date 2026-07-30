"""Cloud TTS provider (M3, decision 5: cloud-only, zero local models).

Voice generation for scripted drama: the shot's dialogue line + the speaker's
voice reference (bible) become a ``voice_take_NN`` under ``media/gen/<shot>/``
with a sidecar carrying the **voice_hash** (``core.spec.compute_voice_hash``)
— the staleness anchor pinned in FIX-F, now wired.

Config shape = the same §8.6 manifest family as video/ASR: type ``tts``,
``adapter: generic_tts``. Most TTS APIs are synchronous (§8.4 degenerate
form): the submit response carries the audio (URL or inline base64); async
APIs fill ``poll`` as usual. Anything more exotic writes a dedicated adapter
class via the ``module:Class`` escape hatch.

Staleness semantics mirror §4.3 exactly:

    shot has no dialogue          -> voice not needed
    no voice take                 -> MISSING (build synthesizes when a tts
                                     manifest is configured)
    newest sidecar voice_hash ==  -> FRESH (skip)
    voice_hash differs            -> STALE (flag only; `manju voice` redoes)
    newest take has NO sidecar    -> MANUAL (hand-dropped, never invalidated)
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from ..core.container import Project
from ..core.hashing import hash_value
from ..core.models import RemoteJobInfo, ShotSpec, VoiceTakeSidecar
from ..core.spec import VOICE_VERSION, compute_voice_hash, voice_payload
from .base import (
    FailureKind,
    ProviderCanceled,
    ProviderFailure,
    probe_media,
    status_to_kind,
)
from .jsonpath import JsonPathError, extract
from .manifest import GENERIC_TTS_ADAPTER, ProviderManifest, load_manifests


class TtsUnavailable(RuntimeError):
    pass


# TRISURFACE F-16: ONE message for "no TTS configured", shared by every raiser
# (this resolver, voicefix, the build voice plan). The old text pointed at
# §8.6 — a design document that is not in the repo — and named a Python class
# with no command to type. Every clause below is a door the owner can walk
# through today.
TTS_UNCONFIGURED_MESSAGE = (
    "TTS 未配置 no TTS provider configured — 免费无 key 的 Edge TTS 一条命令即可:"
    "manju providers add edge --type tts --adapter edge(装完 manju providers "
    "check edge 验证);带 key 的云 TTS 用 --adapter generic_tts 模板;"
    "或自己把配音文件丢进 media/gen/<shot>/ 当 manual voice take"
)


def manifest_fingerprint(manifest: ProviderManifest | None) -> str | None:
    """A stable fingerprint of WHAT the manifest would ask the provider to do
    (round W, review #60): the submit endpoint + body template — the two
    things that, if edited, change the real request even when the manifest id
    stays the same (e.g. someone swaps the underlying model inside the same
    ``tts_x`` manifest). ``None`` manifest (no submit section, e.g. the Edge
    TTS module:Class adapter) still yields a stable constant fingerprint."""
    submit = getattr(manifest, "submit", None) if manifest is not None else None
    return hash_value({
        "url": getattr(submit, "url", None),
        "body_template": getattr(submit, "body_template", None),
    })


def voice_provider_descriptor(provider: Any) -> dict[str, Any]:
    """``{id, fingerprint, language, format}`` for VOICE_VERSION=2 hashing — the
    resolved provider's identity + manifest shape + language/format, so a
    provider/model/language/format swap is visible to voice staleness
    (review #60). Works uniformly for :class:`GenericTtsProvider` and
    :class:`~manju.providers.edge_tts.EdgeTtsProvider` — both expose
    ``.id``/``.manifest``."""
    manifest = getattr(provider, "manifest", None)
    tts_cfg = getattr(manifest, "tts", None) if manifest is not None else None
    return {
        "id": getattr(provider, "id", None),
        "fingerprint": manifest_fingerprint(manifest),
        "language": getattr(tts_cfg, "language", None),
        "format": getattr(tts_cfg, "audio_format", None),
    }


class GenericTtsProvider:
    """TTS over the §8.6 manifest shape. Body placeholders: {text} {speaker}
    {language} plus every voice-shaping bible field of the speaker
    ({voice} {voice_ref} {voice_sample} {voice_id} {tone}, empty when absent)."""

    def __init__(self, manifest: ProviderManifest, *,
                 transport=None, sleep_fn: Callable[[float], None] = time.sleep):
        if manifest.adapter != GENERIC_TTS_ADAPTER:
            raise ValueError(f"manifest {manifest.id} is not a generic_tts manifest")
        hard = [p for p in manifest.validate_for_generic() if "key_env" not in p]
        if hard:
            raise ValueError(f"manifest {manifest.id} invalid: {'; '.join(hard)}")
        from .generic_cloud import default_transport

        self.manifest = manifest
        self.id = manifest.id
        self._transport = transport or default_transport
        self._sleep = sleep_fn

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        from .generic_cloud import GenericCloudProvider

        return GenericCloudProvider._headers(self, extra)  # same auth semantics (§8.2)

    # ------------------------------------------------------------ synthesis

    def _placeholders(self, shot: ShotSpec, bible: dict) -> dict[str, object]:
        payload = voice_payload(shot, bible)
        values: dict[str, object] = {
            "text": payload["text"],
            "speaker": payload["speaker"],
            "language": self.manifest.tts.language,
        }
        for key in ("voice", "voice_ref", "voice_sample", "voice_id", "tone"):
            values[key] = payload["voice_ref"].get(key, "")
        return values

    def _audio_from(self, data, *, dest_dir: Path) -> Path:
        from .generic_cloud import (
            _write_bytes_atomic,
            is_transient_status,
            reject_html_error_page,
            safe_audio_ext,
            safe_url,
        )

        cfg = self.manifest.tts
        # audio_format is an EXTENSION, never a path (§path-traversal guard):
        # `x/../../escaped.bin` must not write outside the staging dir.
        dest = dest_dir / f"voice.{safe_audio_ext(cfg.audio_format, self.id)}"
        if cfg.audio_b64_path:
            try:
                raw_b64 = str(extract(data, cfg.audio_b64_path))
                # validate=True: a corrupt payload ("AAAA!!!!") must RAISE, not
                # silently drop the invalid chars and register 3 junk bytes as a
                # voice take. Strip whitespace/newlines a provider may wrap it in.
                blob = base64.b64decode("".join(raw_b64.split()), validate=True)
            except (JsonPathError, binascii.Error, ValueError) as exc:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: cannot read inline audio ({exc}) — check tts.audio_b64_path",
                ) from exc
            if not blob:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: inline audio decoded to zero bytes — provider returned no audio",
                )
            _write_bytes_atomic(dest, blob)
            return dest
        try:
            url = str(extract(data, cfg.audio_url_path))
        except JsonPathError as exc:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: cannot read audio URL ({exc}) — check tts.audio_url_path",
            ) from exc
        # #54: the SAME scheme allowlist + size cap generic_cloud's
        # default_transport enforces apply here too when no transport was
        # injected (this provider defaults to it, like generic_cloud/asr).
        resp = self._transport("GET", url, {}, None)
        if resp.status >= 400 or not resp.body:
            # PROVIDER-PRIV-001: the audio URL is routinely a PRESIGNED one
            # (``?X-Amz-Signature=…``); it must never reach failures.jsonl /
            # reports intact. safe_url is the provider-side front door to the
            # one scrubber (providers.submission.strip_url_query).
            # PROVIDER-DOWNLOAD-001 sibling: a 429/5xx here is transient, and
            # the synthesis has ALREADY been paid for — classify it retryable
            # instead of burning the take as a terminal provider_error.
            kind = status_to_kind(resp.status)
            if kind is FailureKind.provider_error and is_transient_status(resp.status):
                kind = FailureKind.timeout
            raise ProviderFailure(
                kind,
                f"{self.id}: audio download failed with HTTP {resp.status}",
                detail={"url": safe_url(url), "status": resp.status},
            )
        # #43/#44/#54: a 200 that is actually an HTML/error page must never
        # be written to disk as if it were the promised audio.
        reject_html_error_page(resp, url, self.id)
        _write_bytes_atomic(dest, resp.body)
        return dest

    def synthesize(self, project: Project, shot: ShotSpec, bible: dict,
                   *, should_cancel=None) -> Path:
        """Synthesize the shot's line and register it as the next voice take.
        Returns the registered media path.

        ``should_cancel`` (C21): optional cooperative cancel predicate threaded
        into async ``_poll`` so GUI cancel can stop mid-wait.
        """
        from .base import CapabilitySubmission
        from .generic_cloud import (
            journal_remote_submit,
            parse_json_response,
            render_body,
            require_remote_id,
        )
        from .submission import disposition_for_status

        if not shot.dialogue.text:
            raise ProviderFailure(
                FailureKind.invalid, f"{self.id}: shot {shot.id} has no dialogue to voice"
            )
        cfg = self.manifest.submit
        assert cfg is not None  # validated at construction
        body = json.dumps(
            render_body(cfg.body_template, self._placeholders(shot, bible)),
            ensure_ascii=False,
        ).encode("utf-8")
        # The voice hash is the staleness anchor AND the content-addressed
        # request identity: it already folds the line, the speaker's bible voice
        # ref and the resolved provider descriptor (id/fingerprint/language/
        # format), so two runs that would produce the same voice share a
        # request_digest and a re-run of an ambiguous one is recognizable.
        voice_hash = compute_voice_hash(
            shot, bible, version=VOICE_VERSION,
            provider=voice_provider_descriptor(self),
        )
        # PROVIDER-SUBMISSION-001: the paid POST is no longer the first side
        # effect. PREPARED + the DISPATCHING claim must be durable BEFORE the
        # transport is entered, and a blip afterwards leaves an OUTCOME_UNKNOWN
        # submission that fail-closes the next run instead of paying again.
        with CapabilitySubmission(
                self.id, capability="tts", project=project, scope=shot.id,
                spec_hash=voice_hash, manifest=self.manifest,
                identity_params={"language": self.manifest.tts.language,
                                 "format": self.manifest.tts.audio_format},
                estimated_cost=self.manifest.cost.per_call or None) as guard:
            # submit carries the manifest-declared extra_headers, mirroring
            # generic_cloud.submit (they were silently dropped before — a declared
            # version/tenant header never reached the API)
            resp = self._transport(cfg.method, cfg.url,
                                   self._headers(cfg.extra_headers), body)
            if resp.status >= 400:
                # F4: shared status→kind so a 429 stays retryable rate_limited and
                # every sibling adapter classifies identically (no drift).
                declared = frozenset(cfg.definite_rejection_statuses or ())
                raise ProviderFailure(
                    status_to_kind(resp.status),
                    f"{self.id}: synthesis failed with HTTP {resp.status}",
                    detail={"body": resp.text()[:2000]},
                    disposition=disposition_for_status(resp.status, declared),
                )
            # a 2xx whose body is not readable JSON is OUTCOME_UNKNOWN post-submit
            # (the paid request may have been accepted): structured, never raw.
            data = parse_json_response(resp, self.id, post_submit=True)

            job_id: str | None = None
            if self.manifest.poll is not None:  # async form
                # only a real non-empty id — null/array/object never become "/None".
                job_id = require_remote_id(extract(data, cfg.job_id_path), self.id)
            # provable receipt: ADMITTED (evidence first, then the projection)
            # the instant the provider answered — this is the record that turns a
            # later crash into a fail-closed re-run instead of a second charge.
            guard.admitted(job_id)
            if job_id is not None:
                # The PAID remote job id must hit DURABLE ground BEFORE we poll —
                # fail-closed: if it cannot be journaled, surface the id + mark the
                # outcome unknown instead of silently orphaning the spend.
                journal_remote_submit(project.root, self.id, job_id, {
                    "capability": "tts", "shot": shot.id,
                    "cost": self.manifest.cost.per_call or None,
                    "currency": self.manifest.cost.currency,
                })
                data = self._poll(job_id, should_cancel=should_cancel)

            with tempfile.TemporaryDirectory(prefix=f"tts_{shot.id}_") as tmp:
                audio = self._audio_from(data, dest_dir=Path(tmp))
                sidecar = VoiceTakeSidecar(
                    provider=self.id,
                    voice_hash=voice_hash,
                    voice_hash_version=VOICE_VERSION,
                    params={"text": shot.dialogue.text,
                            "speaker": shot.dialogue.speaker},
                    remote=RemoteJobInfo(
                        job_id=job_id,
                        cost=self.manifest.cost.per_call or None,
                        currency=self.manifest.cost.currency,
                    ),
                    # Cache the voice duration at synthesis (audit FP-L2): the same
                    # best-effort probe the video path uses (providers.base.probe_media,
                    # None on failure), so the timeline compiler reads it back instead
                    # of live-ffprobing this file on every compile.
                    probe=probe_media(audio),
                )
                take = project.register_voice_take(shot.id, audio, sidecar)
            # only now, with the take on durable ground, is the submission resolved
            guard.succeeded()
            return take

    def _poll(self, job_id: str, *, should_cancel=None) -> dict:
        from .generic_cloud import (
            is_transient_status,
            parse_json_response,
            poll_backoff,
            retry_after_seconds,
        )

        poll_cfg = self.manifest.poll
        assert poll_cfg is not None
        # A monotonic deadline bounds REAL wall time (finding 11): the old
        # `elapsed += delay` counted only our sleeps, so a slow/long transport
        # could run far past the advertised 600s budget.
        deadline = time.monotonic() + 600.0
        i = 0
        while True:
            # C12/C27: cooperative cancel between poll rounds (locale/GUI cancel).
            # ProviderCanceled (not ProviderFailure) so run_build locale path and
            # generate_with_fallback treat this as stop-waiting, not a failed spend.
            if should_cancel is not None and should_cancel():
                raise ProviderCanceled(self.id, job_id)
            resp = self._transport(
                "GET", poll_cfg.url.format(job_id=job_id), self._headers(), None
            )
            if resp.status >= 400:
                if is_transient_status(resp.status) and time.monotonic() < deadline:
                    # 429 / 5xx while polling an ACCEPTED job: keep polling the
                    # SAME id (finding 10) — raising here abandoned the remote
                    # job and a higher-level retry would submit (and pay for) a
                    # NEW one. Honor Retry-After, bounded backoff.
                    self._sleep(retry_after_seconds(resp.headers, poll_backoff(i)))
                    i += 1
                    continue
                raise ProviderFailure(
                    status_to_kind(resp.status),
                    f"{self.id}: poll failed with HTTP {resp.status}",
                    detail={"job_id": job_id},
                )
            data = parse_json_response(resp, self.id, post_submit=False)
            raw_status = str(extract(data, poll_cfg.status_path))
            status = poll_cfg.status_map.get(raw_status, raw_status.lower())
            if status == "succeeded":
                return data
            if status == "failed" or status not in ("queued", "running"):
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: job {job_id} ended with status {raw_status!r}",
                    detail={"job_id": job_id, "body": resp.text()[:2000]},
                )
            delay = poll_backoff(i)
            if time.monotonic() + delay > deadline:
                raise ProviderFailure(
                    FailureKind.timeout,
                    f"{self.id}: job {job_id} did not finish within 600s",
                )
            self._sleep(delay)
            i += 1


def tts_providers() -> dict[str, ProviderManifest]:
    """TTS manifests live on the voice surface, not in the shot-generation
    registry — a voice engine is never a fallback for a missing picture."""
    manifests, _ = load_manifests()
    return {pid: m for pid, m in manifests.items() if m.type == "tts"}


def get_tts_provider(name: str | None = None, **kwargs):
    """Resolve a TTS provider by manifest id. ``adapter: generic_tts`` gets
    the config-driven provider; anything else is the §8.6 ``module:Class``
    escape hatch (e.g. ``manju.providers.edge_tts:EdgeTtsProvider`` — Edge TTS
    speaks WebSocket, which the generic REST adapter deliberately does not)."""
    providers = tts_providers()
    if name is None:
        if not providers:
            raise TtsUnavailable(TTS_UNCONFIGURED_MESSAGE)
        name = sorted(providers)[0]
    if name not in providers:
        raise TtsUnavailable(f"unknown TTS provider {name!r}; configured: {sorted(providers)}")
    manifest = providers[name]
    if manifest.adapter == GENERIC_TTS_ADAPTER:
        return GenericTtsProvider(manifest, **kwargs)
    module_name, _, class_name = manifest.adapter.partition(":")
    if not class_name:
        raise TtsUnavailable(
            f"{name}: adapter {manifest.adapter!r} is neither generic_tts nor 'module:Class'"
        )
    try:
        import importlib

        cls = getattr(importlib.import_module(module_name), class_name)
    except (ImportError, AttributeError) as exc:
        raise TtsUnavailable(f"{name}: cannot load adapter {manifest.adapter!r}: {exc}") from exc
    return cls(manifest, **kwargs)
