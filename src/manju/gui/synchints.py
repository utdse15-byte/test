"""Caption/voice sync hints (round U): does the 字幕 match the 语音?

A pure, unit-testable analyzer that compares the compiled timeline's caption
cue windows against the actual speech energy on the voice track (via
:func:`manju.media.waveform.rms_levels`). It never mutates and never renders —
it returns a list of :class:`Hint` records the /edit page overlays on the 字幕
lane and lists in a panel, each pointing at a concrete fix (字幕页 / ``manju
repair --op voice``).

Three hint kinds (REPORTS §1 native-cut depth, item 2 audio light-editing):

  1. ``cue_no_speech`` — a caption cue over near-silence (window mean level
     below :data:`_SILENCE_LEVEL`): "字幕出现但没有对应语音".
  2. ``speech_no_cue``  — sustained speech energy outside every cue window,
     ONLY where dialogue exists (i.e. inside a voice clip): "有语音但没有字幕
     覆盖".
  3. ``cue_early``      — a cue that starts more than :data:`_LEAD_MS` before
     its speech actually begins: "字幕比语音提前出现".

Honesty about media (§3): the analysis needs ffmpeg (rms_levels shells out).
When no voice clip can be analyzed — no ffmpeg, an audio-less source, or no
voice track at all — the analyzer degrades to an empty result plus a note; it
never raises. The rms sampler is injectable (``levels_fn``) so the three hint
kinds and the degrade path are all unit-tested WITHOUT ffmpeg.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["Hint", "sync_hints", "sync_hints_data", "HINT_KINDS"]

# --- tuning constants (normalized RMS: rms_levels maps -60dBFS→0.0, 0dBFS→1.0)
_SILENCE_LEVEL = 0.10   # a cue window whose MEAN level is below this reads silent
_SPEECH_LEVEL = 0.18    # a bucket at/above this counts as "speech is present here"
_LEAD_MS = 300          # a cue earlier than its speech onset by MORE than this = 提前
_MIN_UNCOVERED_MS = 400 # a speech-with-no-cue run shorter than this is not worth a hint
_BUCKETS = 200          # rms sampling resolution across each voice clip

HINT_KINDS = ("cue_no_speech", "speech_no_cue", "cue_early")

# levels_fn(source_relpath) -> list[float] | None  (None = un-analyzable, e.g. no
# audio / no ffmpeg). Injected in tests; the default wraps media.waveform.rms_levels.
LevelsFn = Callable[[str], "list[float] | None"]


@dataclass
class Hint:
    """One caption/voice mismatch. ``text`` is the cue text verbatim (escaped
    only at render / in :meth:`to_dict`); ``start_ms``/``end_ms`` is the window
    the hint marks on the 字幕 lane; ``fix`` is the concrete next step."""

    kind: str
    shot: str
    text: str
    start_ms: int
    end_ms: int
    message: str
    fix: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "shot": self.shot,
            "text": html.escape(self.text or ""),
            "start_ms": int(self.start_ms),
            "end_ms": int(self.end_ms),
            "message": self.message,
            "fix": self.fix,
        }


# ----------------------------------------------------------------- default rms


def _default_levels_fn(project: Any, buckets: int) -> LevelsFn:
    def fn(source: str) -> "list[float] | None":
        try:
            from ..media.waveform import rms_levels

            return rms_levels(project, source, buckets=buckets)
        except Exception:  # MediaError (no audio / no ffmpeg) or anything else
            return None

    return fn


# --------------------------------------------------------------- tiny geometry


def _shot_at(video: list[Any], ms: float) -> str:
    """The shot id whose video clip covers ``ms`` (packaging cards keep their
    ``__intro__``/``__outro__`` id); "" when nothing covers it."""
    for vc in video:
        if vc.start_ms <= ms < vc.start_ms + vc.duration_ms:
            return vc.shot
    return ""


def _bucket_ms(vc: Any, lv: list[float]) -> float:
    return vc.duration_ms / max(1, len(lv))


def _window_levels(covering: list[tuple[Any, list[float]]],
                   start: float, end: float) -> list[float]:
    """Every rms sample whose bucket overlaps ``[start, end)`` across the voice
    clips that overlap the window."""
    vals: list[float] = []
    for vc, lv in covering:
        bms = _bucket_ms(vc, lv)
        for i, v in enumerate(lv):
            b0 = vc.start_ms + i * bms
            if b0 + bms > start and b0 < end:
                vals.append(v)
    return vals


def _speech_onset(covering: list[tuple[Any, list[float]]],
                  cue_start: float, cue_end: float) -> float | None:
    """First timestamp at/after ``cue_start`` (and before ``cue_end``) where a
    covering voice clip crosses :data:`_SPEECH_LEVEL` — i.e. speech begins."""
    onset: float | None = None
    for vc, lv in covering:
        bms = _bucket_ms(vc, lv)
        for i, v in enumerate(lv):
            b0 = vc.start_ms + i * bms
            if b0 < cue_start:
                continue
            if b0 >= cue_end:
                break
            if v >= _SPEECH_LEVEL:
                if onset is None or b0 < onset:
                    onset = b0
                break
    return onset


# ------------------------------------------------------------------- analysis


def _analyze(project: Any, timeline: Any, levels_fn: LevelsFn | None,
             buckets: int) -> tuple[list[Hint], str | None, int]:
    if timeline is None:
        return [], "还没有时间线,构建后才能做字幕/语音同步检查", 0
    video = list(timeline.tracks.video)
    voice = list(timeline.tracks.voice)
    cues = list(timeline.tracks.captions)
    if not voice:
        return [], "时间线没有配音轨(语音),无法比对字幕与语音", 0
    if levels_fn is None:
        levels_fn = _default_levels_fn(project, buckets)

    clips: list[tuple[Any, list[float]]] = []
    for vc in voice:
        try:
            lv = levels_fn(vc.source)
        except Exception:
            lv = None
        if lv:
            clips.append((vc, lv))
    if not clips:
        return ([], "无法读取语音波形(需要 ffmpeg / 该来源无音频),同步检查已跳过", 0)

    hints: list[Hint] = []
    cue_windows = [(c.start_ms, c.end_ms) for c in cues]

    # (1) + (3): each cue against the covering speech energy.
    for c in cues:
        covering = [
            (vc, lv) for vc, lv in clips
            if vc.start_ms < c.end_ms and c.start_ms < vc.start_ms + vc.duration_ms
        ]
        samples = _window_levels(covering, c.start_ms, c.end_ms)
        mean = (sum(samples) / len(samples)) if samples else 0.0
        shot = _shot_at(video, c.start_ms) or _shot_at(video, (c.start_ms + c.end_ms) / 2)
        if mean < _SILENCE_LEVEL:
            hints.append(Hint(
                "cue_no_speech", shot, c.text, c.start_ms, c.end_ms,
                "字幕出现但没有对应语音(该字幕窗口内几乎无声)",
                "到字幕页调整/删除此字幕,或用 `manju repair --op voice` 补配音",
            ))
            continue
        onset = _speech_onset(covering, c.start_ms, c.end_ms)
        if onset is not None and (onset - c.start_ms) > _LEAD_MS:
            lead = int(onset - c.start_ms)
            hints.append(Hint(
                "cue_early", shot, c.text, c.start_ms, c.end_ms,
                f"字幕比语音提前 {lead}ms 出现",
                "到字幕页把此字幕起点对齐到语音起点",
            ))

    # (2): sustained speech energy with NO cue over it (only inside voice clips).
    def _covered(ms: float) -> bool:
        return any(s <= ms < e for s, e in cue_windows)

    for vc, lv in clips:
        bms = _bucket_ms(vc, lv)
        run_start: float | None = None
        n = len(lv)
        for i in range(n + 1):
            mid = vc.start_ms + i * bms + bms / 2.0
            active = i < n and lv[i] >= _SPEECH_LEVEL and not _covered(mid)
            if active and run_start is None:
                run_start = vc.start_ms + i * bms
            elif not active and run_start is not None:
                run_end = vc.start_ms + i * bms
                if run_end - run_start >= _MIN_UNCOVERED_MS:
                    hints.append(Hint(
                        "speech_no_cue", _shot_at(video, run_start),
                        "", int(run_start), int(run_end),
                        "有语音但没有字幕覆盖(这段说话没有字幕)",
                        "到字幕页为这段语音补一条字幕",
                    ))
                run_start = None

    hints.sort(key=lambda h: (h.start_ms, h.kind))
    return hints, None, len(clips)


def sync_hints(project: Any, timeline: Any, *, levels_fn: LevelsFn | None = None,
               buckets: int = _BUCKETS) -> list[Hint]:
    """The caption/voice mismatches for a compiled timeline (may be empty).

    Pure and side-effect free apart from the (cached) rms reads the default
    ``levels_fn`` performs. Degrades to ``[]`` when there is nothing to analyze
    (no timeline / no voice / no ffmpeg). ``levels_fn`` is injectable for tests."""
    return _analyze(project, timeline, levels_fn, buckets)[0]


def sync_hints_data(project: Any, timeline: Any, *, levels_fn: LevelsFn | None = None,
                    buckets: int = _BUCKETS) -> dict[str, Any]:
    """The endpoint/render envelope: hints + an honest degrade ``note`` +
    how many voice clips were analyzed."""
    hints, note, analyzed = _analyze(project, timeline, levels_fn, buckets)
    return {
        "hints": [h.to_dict() for h in hints],
        "note": note,
        "analyzed": analyzed,
        "available": analyzed > 0,
    }
