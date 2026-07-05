"""Timeline compiler (§6) — a pure function.

input  = shot order + each shot's selected take + voice durations + rules
output = timeline.json (same input, same output, always)

Times are integer milliseconds throughout; fps only exists at render time —
EXCEPT for the frame-grid snap (FIX-B): every clip duration is rounded to a
whole number of frames so segment boundaries land exactly on the encoder's
frame grid (rule: ms → nearest whole frame count, minimum 1 frame → back to
nearest integer ms; e.g. 1200ms @ 24fps = 28.8 frames → 29 frames → 1208ms).
Audio drives picture duration: for `duration: auto` shots the voice take's
length plus padding decides the clip length, clamped by rules, then snapped.

Manual takeover (§6): when rules.mode == "manual", timeline.json is human
truth — the compiler refuses to overwrite it and writes
timeline.generated.json for comparison instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..core.container import Project
from ..core.hashing import hash_value
from ..core.models import (
    AudioClip,
    CaptionLine,
    OverlayClip,
    ProjectConfig,
    ShotSpec,
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)

# probe_fn: absolute media path -> duration in ms (None if unreadable)
ProbeFn = Callable[[Path], int | None]


class CompileError(RuntimeError):
    pass


@dataclass
class ShotInput:
    shot: ShotSpec
    take_name: str
    take_source: str  # project-relative
    take_duration_ms: int | None
    voice_source: str | None = None  # project-relative
    voice_duration_ms: int | None = None


@dataclass
class CompileInput:
    config: ProjectConfig
    rules: TimelineRules
    shots: list[ShotInput] = field(default_factory=list)

    def fingerprint(self) -> str:
        """Hash of everything that shaped this compile -> meta.compiled_from."""
        payload: dict[str, Any] = {
            "fps": self.config.fps,
            "width": self.config.width,
            "height": self.config.height,
            "rules": self.rules.model_dump(),
            "shots": [
                {
                    "id": s.shot.id,
                    "duration": s.shot.duration,
                    "dialogue": s.shot.dialogue.model_dump(),
                    "take": s.take_name,
                    "take_source": s.take_source,
                    "take_duration_ms": s.take_duration_ms,
                    "voice_source": s.voice_source,
                    "voice_duration_ms": s.voice_duration_ms,
                }
                for s in self.shots
            ],
        }
        return hash_value(payload)


def snap_to_frame_grid(duration_ms: int, fps: int) -> int:
    """FIX-B frame-grid rounding rule (also documented in the README):

        frames = max(1, round(duration_ms * fps / 1000))
        snapped = max(1, round(frames * 1000 / fps))

    A duration that is not a whole number of frames cannot be rendered
    faithfully — the encoder rounds every segment up/down independently and
    the drift accumulates across the concat (pre-fix: 1200ms @ 24fps became
    29-frame/1216ms segments and a 143/6 final frame rate). Snapping to the
    nearest whole frame count (never below one frame) keeps timeline math,
    captions, audio offsets and the encoder all on the same grid.
    """
    frames = max(1, round(duration_ms * fps / 1000))
    return max(1, round(frames * 1000 / fps))


def _resolve_duration_ms(inp: ShotInput, rules: TimelineRules, fps: int) -> int:
    """Audio drives picture (§6). Explicit numeric duration always wins; the
    result is snapped to the frame grid (FIX-B) as the final step."""
    timing = rules.timing
    if inp.shot.duration != "auto":
        return snap_to_frame_grid(max(1, int(round(float(inp.shot.duration) * 1000))), fps)
    if inp.voice_duration_ms:
        raw = inp.voice_duration_ms + timing.padding_before_ms + timing.padding_after_ms
    elif inp.take_duration_ms:
        raw = inp.take_duration_ms
    else:
        raw = timing.default_shot_ms
    return snap_to_frame_grid(max(timing.min_shot_ms, min(timing.max_shot_ms, raw)), fps)


def _split_caption(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Split dialogue into caption-sized chunks. CJK text has no spaces, so
    the splitter is width-based with a preference for punctuation breaks."""
    text = text.strip()
    if not text:
        return []
    budget = max_chars * max_lines
    if len(text) <= budget:
        return [text]
    chunks: list[str] = []
    rest = text
    breakers = "。!?!?;;,,、 "
    while rest:
        if len(rest) <= budget:
            chunks.append(rest)
            break
        window = rest[:budget]
        cut = max((window.rfind(ch) for ch in breakers), default=-1)
        cut = cut + 1 if cut > budget // 2 else budget
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [c for c in chunks if c]


def compile_timeline(inp: CompileInput) -> Timeline:
    if not inp.shots:
        raise CompileError("nothing to compile: no shots with a usable selected take")

    rules, config = inp.rules, inp.config
    tracks = TimelineTracks()
    cursor = 0

    for i, s in enumerate(inp.shots):
        duration_ms = _resolve_duration_ms(s, rules, config.fps)
        is_last = i == len(inp.shots) - 1
        tracks.video.append(
            VideoClip(
                shot=s.shot.id,
                take=s.take_name,
                source=s.take_source,
                start_ms=cursor,
                duration_ms=duration_ms,
                transition_out=None if is_last else rules.transition_default,
            )
        )

        if s.voice_source and s.voice_duration_ms:
            tracks.voice.append(
                AudioClip(
                    source=s.voice_source,
                    start_ms=cursor + rules.timing.padding_before_ms,
                    duration_ms=s.voice_duration_ms,
                )
            )

        text = s.shot.dialogue.text
        if rules.captions.enabled and text:
            # captions follow the voice when there is one, else span the shot
            if s.voice_duration_ms:
                cap_start = cursor + rules.timing.padding_before_ms
                cap_total = s.voice_duration_ms
            else:
                cap_start = cursor
                cap_total = duration_ms
            pieces = _split_caption(
                text, rules.captions.max_chars_per_line, rules.captions.max_lines
            )
            if pieces:
                weights = [len(p) for p in pieces]
                total_w = sum(weights)
                t = cap_start
                for piece, w in zip(pieces, weights):
                    span = max(300, int(round(cap_total * w / total_w)))
                    end = min(t + span, cap_start + cap_total)
                    tracks.captions.append(
                        CaptionLine(
                            start_ms=t,
                            end_ms=max(end, t + 1),
                            text=piece,
                            speaker=s.shot.dialogue.speaker,
                        )
                    )
                    t = end

        cursor += duration_ms

    total_ms = cursor
    # Title card is an overlay layer (§7 step ④): emitted after the video track
    # is assembled so its duration can be clamped to the film's length.
    if rules.title_card.enabled and rules.title_card.text:
        tracks.overlay.append(
            OverlayClip(
                kind="title_card",
                template=rules.title_card.template,
                text=rules.title_card.text,
                start_ms=0,
                duration_ms=min(rules.title_card.duration_ms, total_ms),
            )
        )

    if rules.music.source:
        tracks.music.append(
            AudioClip(
                source=rules.music.source,
                start_ms=0,
                duration_ms=total_ms,  # BGM is trimmed to picture length
                gain_db=rules.music.gain_db,
                ducking=rules.music.ducking,
                fade_out_ms=rules.music.fade_out_ms,
            )
        )

    return Timeline(
        meta=TimelineMeta(compiled_from=inp.fingerprint(), mode="compiled"),
        fps=config.fps,
        width=config.width,
        height=config.height,
        duration_ms=total_ms,
        tracks=tracks,
    )


# --------------------------------------------------------- project plumbing


def _find_voice(project: Project, shot_id: str) -> Path | None:
    tdir = project.takes_dir(shot_id)
    if not tdir.exists():
        return None
    candidates = sorted(tdir.glob("voice_take_*.*")) + sorted(tdir.glob("voice.*"))
    return next((c for c in candidates if c.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac")), None)


def gather_compile_input(project: Project, probe_fn: ProbeFn) -> CompileInput:
    """Assemble the compiler's input from the project. Raises CompileError
    listing every shot that cannot go on the timeline (missing/unselected)."""
    from ..build.stale import evaluate_all  # local import: build depends on timeline too

    config = project.load_config()
    rules = project.load_rules()
    shots: list[ShotInput] = []
    problems: list[str] = []

    for status in evaluate_all(project):
        if not status.usable:
            problems.append(f"{status.shot_id}: {status.state.value}"
                            + (f" ({status.note})" if status.note else ""))
            continue
        take = status.take
        assert take is not None and take.media_path is not None
        voice = _find_voice(project, status.shot_id)
        take_dur = take.sidecar.probe.duration_ms if take.sidecar.probe else None
        if take_dur is None:
            take_dur = probe_fn(take.media_path)
        shots.append(
            ShotInput(
                shot=project.load_shot(status.shot_id),
                take_name=take.name,
                take_source=project.relpath(take.media_path),
                take_duration_ms=take_dur,
                voice_source=project.relpath(voice) if voice else None,
                voice_duration_ms=probe_fn(voice) if voice else None,
            )
        )

    if problems:
        raise CompileError(
            "cannot compile timeline, unresolved shots:\n  " + "\n  ".join(problems)
        )
    return CompileInput(config=config, rules=rules, shots=shots)


def build_timeline(project: Project, probe_fn: ProbeFn) -> tuple[Timeline, Path, bool]:
    """Compile and write. Returns (timeline, written_path, overwrote_truth).

    mode=manual makes timeline.json human truth: we only ever write
    timeline.generated.json next to it (§6).
    """
    inp = gather_compile_input(project, probe_fn)
    timeline = compile_timeline(inp)
    manual = project.load_rules().mode == "manual"
    path = project.save_timeline(timeline, generated_only=manual)
    return timeline, path, not manual
