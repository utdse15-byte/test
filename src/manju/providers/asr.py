"""Cloud ASR plugin slot (M4, decision 5: cloud-only, zero local models).

Transcription for imported real footage (导入真人素材转录, Niren-CASR-style).
The scripted-drama main line never needs ASR — captions come from dialogue +
TTS alignment — so this is an on-demand slot with three equal on-ramps:

    cloud   an ``asr``-type manifest on the §8.6 config shape
            (``adapter: generic_asr``); sync APIs use the submit-is-result
            degenerate form (§8.4), async ones fill ``poll`` as usual
    srt     a human-provided SRT is parsed and normalized (manual input)
    text    a human-provided transcript is distributed over the media
            duration, weighted by line length (manual input)

All three produce the same ``TranscriptSegment`` list, so downstream use
(captions, dialogue back-fill) never cares where the words came from.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .base import FailureKind, ProviderCanceled, ProviderFailure, status_to_kind
from .jsonpath import JsonPathError, extract
from .manifest import (
    GENERIC_ASR_ADAPTER,
    ProviderManifest,
    load_manifests,
)


class AsrUnavailable(RuntimeError):
    pass


def _find_project_root(media: Path) -> "Path | None":
    """Walk up from a media path to the containing project (the dir with
    project.yaml). ASR media is always project-internal, so this normally
    resolves; ``None`` when it cannot, in which case the durable remote-job
    journal is skipped rather than written to a wrong place."""
    from ..core.container import PROJECT_FILE

    try:
        p = Path(media).resolve()
    except OSError:
        return None
    for cand in (p, *p.parents):
        if (cand / PROJECT_FILE).exists():
            return cand
    return None


@dataclass
class TranscriptSegment:
    start_ms: int
    end_ms: int
    text: str


# ------------------------------------------------------------ manual: SRT

_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def parse_srt(text: str) -> list[TranscriptSegment]:
    """Parse human-provided SRT into segments. Tolerant of BOM, CRLF and
    missing cue numbers; cues out of order are sorted by start time."""
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", text.lstrip("﻿").replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        match = next((m for line in lines if (m := _SRT_TIME.search(line))), None)
        if match is None:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in match.groups())
        start = ((h1 * 60 + m1) * 60 + s1) * 1000 + ms1
        end = ((h2 * 60 + m2) * 60 + s2) * 1000 + ms2
        time_index = next(i for i, line in enumerate(lines) if _SRT_TIME.search(line))
        cue_text = "\n".join(lines[time_index + 1:]).strip()
        if cue_text and end > start:
            segments.append(TranscriptSegment(start, end, cue_text))
    return sorted(segments, key=lambda s: s.start_ms)


# ----------------------------------------------------------- manual: text


def distribute_text(text: str, duration_ms: int, *, gap_ms: int = 120,
                    min_segment_ms: int = 600) -> list[TranscriptSegment]:
    """Turn a plain transcript into timed segments: split on newlines and CJK
    sentence enders, then spread over the media duration weighted by length.
    Deliberately simple — it gives a human 90% of the alignment for free and
    stays editable as SRT afterwards."""
    pieces = [p.strip() for p in re.split(r"[\n。!?!?…]+", text) if p.strip()]
    if not pieces or duration_ms <= 0:
        return []
    n = len(pieces)
    gap = max(0, gap_ms)
    min_seg = max(1, min_segment_ms)
    # If the minimum spans + gaps cannot fit the media, scale them down so the
    # whole distribution stays inside [0, duration_ms]. Finding 18: the old
    # `max(n*min_segment_ms, duration - gaps)` forced `usable` ABOVE the media
    # duration for a short recording, so the last cue started/ended AFTER the
    # media had finished (invalid SRT timing).
    need = n * min_seg + (n - 1) * gap
    if need > duration_ms:
        scale = duration_ms / need
        min_seg = max(1, int(min_seg * scale))
        gap = int(gap * scale)
    total_gap = (n - 1) * gap
    usable = max(n, duration_ms - total_gap)
    weights = [len(p) for p in pieces]
    total_weight = sum(weights) or n
    segments: list[TranscriptSegment] = []
    cursor = 0
    for idx, (piece, weight) in enumerate(zip(pieces, weights)):
        remaining_after = n - idx - 1  # pieces still to place after this one
        span = max(min_seg, int(round(usable * weight / total_weight)))
        # never let a cue (or the room it must leave for later cues) exceed the
        # media: clamp to duration minus 1ms + gap per remaining piece.
        latest_end = duration_ms - remaining_after * (gap + 1)
        end = min(cursor + span, latest_end, duration_ms)
        if end <= cursor:
            end = min(cursor + 1, duration_ms)
        segments.append(TranscriptSegment(cursor, end, piece))
        cursor = min(end + gap, duration_ms)
    return segments


# ------------------------------------------------------------- cloud slot


class GenericAsrProvider:
    """ASR over the same manifest shape as generic_cloud (§8.6). The audio
    file rides in the JSON body as base64 via the ``{audio_b64}`` placeholder;
    anything needing multipart/presigned upload writes a dedicated adapter."""

    def __init__(self, manifest: ProviderManifest, *,
                 transport=None, sleep_fn: Callable[[float], None] = time.sleep):
        if manifest.adapter != GENERIC_ASR_ADAPTER:
            raise ValueError(f"manifest {manifest.id} is not a generic_asr manifest")
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

    def _segments_from(self, data) -> list[TranscriptSegment]:
        import math

        cfg = self.manifest.asr
        try:
            raw_segments = extract(data, cfg.segments_path)
        except JsonPathError as exc:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: cannot read transcript segments ({exc})",
            ) from exc
        # The container must be a LIST of mappings. `{"segments": null}` used to
        # reach `for seg in None` → raw TypeError; a scalar/str is equally wrong.
        if not isinstance(raw_segments, list):
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: transcript segments is {type(raw_segments).__name__}, "
                "expected a list — check asr.segments_path",
            )
        scale = 1000.0 if cfg.time_unit == "s" else 1.0
        segments = []
        for seg in raw_segments:
            if not isinstance(seg, dict):
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: transcript segment is {type(seg).__name__}, "
                    "expected an object — check asr.segments_path",
                )
            try:
                start = float(seg[cfg.start_key]) * scale
                end = float(seg[cfg.end_key]) * scale
                # non-finite (inf/nan) → int(round(...)) raises OverflowError/
                # ValueError; catch both here so a bad number fails structured.
                if not (math.isfinite(start) and math.isfinite(end)):
                    raise ValueError("non-finite timestamp")
                segments.append(TranscriptSegment(
                    start_ms=max(0, int(round(start))),   # clamp negatives to 0
                    end_ms=max(0, int(round(end))),
                    text=str(seg[cfg.text_key]).strip(),
                ))
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: malformed segment {seg!r} ({exc}) — check the "
                    "asr.text_key/start_key/end_key/time_unit config",
                ) from exc
        # sorted + only real spans: an out-of-order provider must not yield an
        # out-of-order transcript (invalid SRT downstream).
        good = [s for s in segments if s.text and s.end_ms > s.start_ms]
        good.sort(key=lambda s: (s.start_ms, s.end_ms))
        return good

    def transcribe(self, media: Path, *,
                   should_cancel: Callable[[], bool] | None = None,
                   project_root: "Path | None" = None,
                   ) -> list[TranscriptSegment]:
        """Transcribe ``media`` via the configured ASR endpoint.

        ``should_cancel`` (C23): optional cooperative cancel predicate checked
        between async poll rounds so GUI/CLI cancel can stop mid-wait without
        sitting out the full 600s budget.

        ``project_root`` (audit finding 9): where to DURABLY journal an accepted
        async remote job id before polling. Defaults to walking up from ``media``
        to the containing project (ASR media is always project-internal).
        """
        from .generic_cloud import _max_response_bytes, render_body

        cfg = self.manifest.submit
        assert cfg is not None  # validated at construction
        from .zero_cost import require_transport_allowed

        require_transport_allowed(cfg.url, credential_ref=self.manifest.auth.key_env)
        # #54: the SAME size cap generic_cloud's downloads enforce, applied to
        # the local read before base64-encoding it into memory — a huge local
        # file must not be silently loaded whole (OOM guard, goal W).
        max_bytes = _max_response_bytes()
        media_path = Path(media)
        size = media_path.stat().st_size
        if max_bytes is not None and size > max_bytes:
            raise ProviderFailure(
                FailureKind.invalid,
                f"{self.id}: {media_path} is {size} bytes, over the "
                f"{max_bytes}-byte cap (configurable via MANJU_MAX_DOWNLOAD_BYTES) "
                "— refusing to load it whole for base64 upload",
                detail={"path": str(media_path), "size": size, "max_bytes": max_bytes},
            )
        audio_b64 = base64.b64encode(media_path.read_bytes()).decode("ascii")
        values = {
            "audio_b64": audio_b64,
            "filename": Path(media).name,
            "language": self.manifest.asr.language,
        }
        body = json.dumps(render_body(cfg.body_template, values),
                          ensure_ascii=False).encode("utf-8")
        # submit carries the manifest-declared extra_headers (mirrors
        # generic_cloud.submit / the tts sibling — they were silently dropped)
        from .base import CapabilitySubmission
        from .generic_cloud import (
            journal_remote_submit,
            parse_json_response,
            require_remote_id,
        )
        from .submission import disposition_for_status

        root = project_root or _find_project_root(media_path)
        # PROVIDER-SUBMISSION-001: durable PREPARED + DISPATCHING evidence
        # BEFORE the paid POST, so a blip afterwards fail-closes the next run
        # instead of silently paying for the same transcription twice. Media
        # outside any project has nowhere durable to write (the pre-existing
        # `journal_remote_submit` skip) — the guard degrades to a no-op there
        # rather than refusing work that was never journaled before either.
        with CapabilitySubmission(
                self.id, capability="asr",
                project=self._project_for(root), scope=self._scope_id(media_path, root),
                spec_hash=self._media_digest(media_path), manifest=self.manifest,
                identity_params={"language": self.manifest.asr.language,
                                 "filename": media_path.name},
                estimated_cost=self.manifest.cost.per_call or None) as guard:
            resp = self._transport(cfg.method, cfg.url,
                                   self._headers(cfg.extra_headers), body)
            if resp.status >= 400:
                # F4: shared status→kind so ASR classifies a 429 as retryable
                # rate_limited exactly like its tts sibling (was always provider_error).
                declared = frozenset(cfg.definite_rejection_statuses or ())
                raise ProviderFailure(
                    status_to_kind(resp.status),
                    f"{self.id}: transcribe failed with HTTP {resp.status}",
                    detail={"body": resp.text()[:2000]},
                    disposition=disposition_for_status(resp.status, declared),
                )
            data = parse_json_response(resp, self.id, post_submit=True)

            poll_cfg = self.manifest.poll
            job_id = None
            if poll_cfg is not None:
                # async form: a real non-empty id (never "/None"), DURABLY
                # journaled before polling (fail-closed).
                job_id = require_remote_id(extract(data, cfg.job_id_path), self.id)
            guard.admitted(job_id)  # provable receipt: the spend is real
            if poll_cfg is None:  # sync API: submit IS the result (§8.4)
                segments = self._segments_from(data)
            else:
                if root is not None:
                    journal_remote_submit(root, self.id, job_id,
                                          {"capability": "asr", "media": str(media)})
                segments = self._poll_segments(job_id, should_cancel=should_cancel)
            guard.succeeded()
            return segments

    # -- submission-identity helpers (PROVIDER-SUBMISSION-001) -------------

    @staticmethod
    def _project_for(root: "Path | None"):
        """The Project the admission evidence lands in, or ``None`` when the
        media does not live in one (degraded, no-op guard)."""
        if root is None:
            return None
        try:
            from ..core.container import Project

            return Project(root)
        except Exception:
            return None

    @staticmethod
    def _scope_id(media_path: Path, root: "Path | None") -> str:
        """The correlation key the intent/event rows carry in the ``shot``
        column. ASR has no shot, so it is the project-relative media path —
        stable across runs, and ``as_posix`` so Windows and POSIX agree."""
        try:
            if root is not None:
                return "asr:" + media_path.resolve().relative_to(
                    Path(root).resolve()).as_posix()
        except (OSError, ValueError):
            pass
        return "asr:" + media_path.name

    @staticmethod
    def _media_digest(media_path: Path) -> "str | None":
        """The audio's content hash — the one fact that decides whether two ASR
        submissions are the same request. Through the ONE content hasher."""
        try:
            from ..core.hashing import hash_file

            return hash_file(media_path)
        except OSError:
            return None

    def _poll_segments(self, job_id: str, *,
                       should_cancel: Callable[[], bool] | None = None,
                       ) -> list[TranscriptSegment]:
        """Async ASR poll loop. ``should_cancel`` (C23) checked between rounds."""
        from .generic_cloud import (
            is_transient_status,
            parse_json_response,
            poll_backoff,
            retry_after_seconds,
        )

        poll_cfg = self.manifest.poll
        assert poll_cfg is not None
        deadline = time.monotonic() + 600.0  # real wall-clock budget (finding 11)
        i = 0
        while True:
            # C23/C30: cooperative cancel BETWEEN poll sleeps (never mid-HTTP).
            # ProviderCanceled (not ProviderFailure) matches TTS/cloud/ComfyUI.
            if should_cancel is not None and should_cancel():
                raise ProviderCanceled(self.id, job_id)
            url = poll_cfg.url.format(job_id=job_id)
            from .zero_cost import require_transport_allowed

            require_transport_allowed(url, credential_ref=self.manifest.auth.key_env)
            resp = self._transport(
                "GET", url, self._headers(), None
            )
            if resp.status >= 400:
                if is_transient_status(resp.status) and time.monotonic() < deadline:
                    # 429 / 5xx polling an ACCEPTED job: keep the SAME id
                    # (finding 10) — abandoning it lets a retry pay for a new one.
                    self._sleep(retry_after_seconds(resp.headers, poll_backoff(i)))
                    i += 1
                    continue
                raise ProviderFailure(
                    status_to_kind(resp.status),
                    f"{self.id}: poll failed with HTTP {resp.status}",
                    detail={"job_id": job_id, "body": resp.text()[:2000]},
                )
            data = parse_json_response(resp, self.id, post_submit=False)
            raw_status = str(extract(data, poll_cfg.status_path))
            status = poll_cfg.status_map.get(raw_status, raw_status.lower())
            if status == "succeeded":
                return self._segments_from(data)
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
                    detail={"job_id": job_id},
                )
            self._sleep(delay)
            i += 1


def asr_providers() -> dict[str, ProviderManifest]:
    """ASR manifests live on the transcribe surface, not in the generation
    registry — an ASR engine can never be a fallback for a missing shot."""
    manifests, _ = load_manifests()
    return {pid: m for pid, m in manifests.items() if m.type == "asr"}


def get_asr_provider(name: str | None = None, **kwargs) -> GenericAsrProvider:
    providers = asr_providers()
    if name is None:
        if not providers:
            raise AsrUnavailable(
                "no ASR provider configured — three on-ramps: "
                "(a) fill an asr manifest (§8.6, type: asr, adapter: generic_asr) "
                "under ~/.manju/providers/; (b) `manju transcribe --from-srt` with "
                "your own subtitles; (c) `manju transcribe --text` with the transcript"
            )
        name = sorted(providers)[0]
    if name not in providers:
        raise AsrUnavailable(f"unknown ASR provider {name!r}; configured: {sorted(providers)}")
    manifest = providers[name]
    from .zero_cost import require_manifest_allowed

    require_manifest_allowed(manifest)
    return GenericAsrProvider(manifest, **kwargs)


def segments_to_srt(segments: list[TranscriptSegment]) -> str:
    from ..exporters.srt_ass import ms_to_srt

    blocks = [
        f"{i}\n{ms_to_srt(s.start_ms)} --> {ms_to_srt(s.end_ms)}\n{s.text}\n"
        for i, s in enumerate(segments, start=1)
    ]
    return "\n".join(blocks)
