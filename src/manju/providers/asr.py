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

from .base import FailureKind, ProviderFailure, status_to_kind
from .jsonpath import JsonPathError, extract
from .manifest import (
    GENERIC_ASR_ADAPTER,
    ProviderManifest,
    load_manifests,
)


class AsrUnavailable(RuntimeError):
    pass


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
    total_gap = gap_ms * (len(pieces) - 1)
    usable = max(len(pieces) * min_segment_ms, duration_ms - total_gap)
    weights = [len(p) for p in pieces]
    total_weight = sum(weights)
    segments: list[TranscriptSegment] = []
    cursor = 0
    for piece, weight in zip(pieces, weights):
        span = max(min_segment_ms, int(round(usable * weight / total_weight)))
        end = min(cursor + span, duration_ms) if duration_ms > cursor else cursor + span
        segments.append(TranscriptSegment(cursor, max(end, cursor + 1), piece))
        cursor = end + gap_ms
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

    def _headers(self) -> dict[str, str]:
        from .generic_cloud import GenericCloudProvider

        return GenericCloudProvider._headers(self)  # same auth semantics (§8.2)

    def _segments_from(self, data) -> list[TranscriptSegment]:
        cfg = self.manifest.asr
        try:
            raw_segments = extract(data, cfg.segments_path)
        except JsonPathError as exc:
            raise ProviderFailure(
                FailureKind.provider_error,
                f"{self.id}: cannot read transcript segments ({exc})",
            ) from exc
        scale = 1000.0 if cfg.time_unit == "s" else 1.0
        segments = []
        for seg in raw_segments:
            try:
                segments.append(TranscriptSegment(
                    start_ms=int(round(float(seg[cfg.start_key]) * scale)),
                    end_ms=int(round(float(seg[cfg.end_key]) * scale)),
                    text=str(seg[cfg.text_key]).strip(),
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: malformed segment {seg!r} ({exc}) — check the "
                    "asr.text_key/start_key/end_key/time_unit config",
                ) from exc
        return [s for s in segments if s.text and s.end_ms > s.start_ms]

    def transcribe(self, media: Path, *,
                   should_cancel: Callable[[], bool] | None = None,
                   ) -> list[TranscriptSegment]:
        """Transcribe ``media`` via the configured ASR endpoint.

        ``should_cancel`` (C23): optional cooperative cancel predicate checked
        between async poll rounds so GUI/CLI cancel can stop mid-wait without
        sitting out the full 600s budget.
        """
        from .generic_cloud import _max_response_bytes, render_body

        cfg = self.manifest.submit
        assert cfg is not None  # validated at construction
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
        resp = self._transport(cfg.method, cfg.url, self._headers(), body)
        if resp.status >= 400:
            # F4: shared status→kind so ASR classifies a 429 as retryable
            # rate_limited exactly like its tts sibling (was always provider_error).
            raise ProviderFailure(
                status_to_kind(resp.status),
                f"{self.id}: transcribe failed with HTTP {resp.status}",
                detail={"body": resp.text()[:2000]},
            )
        data = resp.json()

        poll_cfg = self.manifest.poll
        if poll_cfg is None:  # sync API: submit IS the result (§8.4)
            return self._segments_from(data)

        # async form: same loop shape as generic_cloud, but the transcript
        # lives in the final poll body itself, so we keep the raw JSON
        job_id = str(extract(data, cfg.job_id_path))
        return self._poll_segments(job_id, should_cancel=should_cancel)

    def _poll_segments(self, job_id: str, *,
                       should_cancel: Callable[[], bool] | None = None,
                       ) -> list[TranscriptSegment]:
        """Async ASR poll loop. ``should_cancel`` (C23) checked between rounds."""
        poll_cfg = self.manifest.poll
        assert poll_cfg is not None
        elapsed, i = 0.0, 0
        while True:
            # C23: cooperative cancel BETWEEN poll sleeps (never mid-HTTP).
            if should_cancel is not None and should_cancel():
                raise ProviderFailure(
                    FailureKind.provider_error,
                    f"{self.id}: poll canceled for job {job_id}",
                    detail={"job_id": job_id, "canceled": True},
                )
            resp = self._transport(
                "GET", poll_cfg.url.format(job_id=job_id), self._headers(), None
            )
            if resp.status >= 400:
                # F4: a 429 during poll is retryable rate_limited — shared
                # classification, no sibling drift.
                raise ProviderFailure(
                    status_to_kind(resp.status),
                    f"{self.id}: poll failed with HTTP {resp.status}",
                    detail={"job_id": job_id, "body": resp.text()[:2000]},
                )
            data = resp.json()
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
            delay = min(0.5 * (2 ** i), 8.0)
            if elapsed + delay > 600.0:
                raise ProviderFailure(
                    FailureKind.timeout,
                    f"{self.id}: job {job_id} did not finish within 600s",
                    detail={"job_id": job_id},
                )
            self._sleep(delay)
            elapsed += delay
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
    return GenericAsrProvider(providers[name], **kwargs)


def segments_to_srt(segments: list[TranscriptSegment]) -> str:
    from ..exporters.srt_ass import ms_to_srt

    blocks = [
        f"{i}\n{ms_to_srt(s.start_ms)} --> {ms_to_srt(s.end_ms)}\n{s.text}\n"
        for i, s in enumerate(segments, start=1)
    ]
    return "\n".join(blocks)
