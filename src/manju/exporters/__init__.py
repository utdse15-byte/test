"""Exporters (§8 出口层, §13 M1): captions (SRT/ASS/VTT/TTML), OTIO, CMX3600 EDL, FCPXML, JianYing draft.

Everything here is a dependency-free, deterministic, atomic writer. Each
exporter is one optional exit; ``final.mp4`` + SRT + OTIO always suffice to
ship (§14). CapCut-family exporters are deferred (§13 M1) and added later as
pure adapter-boundary increments.
"""

from __future__ import annotations

from .edl import compile_edl, export_edl
from .fcpxml import compile_fcpxml, export_fcpxml
from .jianying import export_jianying, lint_draft
from .openclap import export_openclap
from .otio import export_otio
from .srt_ass import (
    compile_ass,
    compile_srt,
    export_captions,
    ms_to_ass,
    ms_to_srt,
)
from .ttml import compile_ttml, export_ttml
from .xmeml import compile_xmeml, export_xmeml

__all__ = [
    "ms_to_srt",
    "ms_to_ass",
    "compile_srt",
    "compile_ass",
    "compile_ttml",
    "compile_edl",
    "compile_fcpxml",
    "export_captions",
    "export_ttml",
    "export_edl",
    "export_fcpxml",
    "compile_xmeml",
    "export_xmeml",
    "export_otio",
    "export_jianying",
    "export_openclap",
    "lint_draft",
]
