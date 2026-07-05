"""media/ — FFmpeg wrapper, probing, normalization, the render pipeline, and the
local fallback providers (Ken Burns push-in, caption cards). No LLM calls, no
network: everything here is deterministic local rendering (§7, §8.4)."""

from __future__ import annotations

from .card import caption_card, find_font
from .ffmpeg import MediaError, default_log, run_ffmpeg
from .kenburns import kenburns
from .normalize import normalize_segment
from .probe import probe, probe_duration_ms
from .render import render_timeline

__all__ = [
    "MediaError",
    "run_ffmpeg",
    "default_log",
    "probe",
    "probe_duration_ms",
    "normalize_segment",
    "render_timeline",
    "kenburns",
    "find_font",
    "caption_card",
]
