"""QC checks (§9) — three layers, by increasing depth.

    existence  referenced files are present and ffprobe-readable
    technical  durations vs timeline (tolerance), resolution, caption sanity,
               final-render checks, optional deep black/silence detection
    content    "eyes for the agent": extract one mid-point frame per shot so an
               agent can judge character/scene consistency; that judgment stays
               OUTSIDE the engine (§9).

QC never crashes on a broken external tool: probing and detectors are wrapped so
a failed detector degrades to "no finding", never to a failed report.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..build.stale import ShotBuildStatus, ShotState, evaluate_all
from ..core.container import Project
from ..core.models import ProbeInfo, ProjectConfig, Timeline

# tolerances (§9)
_DURATION_TOL_MS = 150      # per-clip source vs timeline duration
_FINAL_DURATION_TOL_MS = 500  # final render vs timeline total
_BLACKDETECT_D = 0.5        # minimum black-span seconds
_SILENCE_DB = -60.0         # mean_volume below this = near silent


@dataclass
class QCItem:
    level: str    # "error" | "warn" | "info"
    area: str     # "existence" | "technical" | "content"
    subject: str  # shot id / "timeline" / "final"
    message: str
    suggestion: str = ""
    auto_safe: bool = False

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "area": self.area,
            "subject": self.subject,
            "message": self.message,
            "suggestion": self.suggestion,
            "auto_safe": self.auto_safe,
        }


@dataclass
class QCReport:
    items: list[QCItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(it.level == "error" for it in self.items)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "items": [it.to_dict() for it in self.items]}

    def add(self, *args, **kwargs) -> None:
        self.items.append(QCItem(*args, **kwargs))


# --------------------------------------------------------------------- probe


def _probe_info(path: Path) -> ProbeInfo | None:
    """Prefer ``manju.media.probe``; fall back to a direct ffprobe call so QC
    works even before the media package lands."""
    try:
        from ..media.probe import probe as media_probe  # lazy

        info = media_probe(path)
        if info is not None:
            return info
    except Exception:
        pass
    return _ffprobe(path)


def _ffprobe(path: Path) -> ProbeInfo | None:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except Exception:
        return None
    streams = data.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    duration_ms = None
    dur = fmt.get("duration") or (vstream or {}).get("duration")
    if dur is not None:
        try:
            duration_ms = int(round(float(dur) * 1000))
        except (TypeError, ValueError):
            duration_ms = None

    width = height = None
    fps = None
    if vstream:
        width = vstream.get("width")
        height = vstream.get("height")
        rate = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate")
        if rate and "/" in rate:
            num, den = rate.split("/", 1)
            try:
                den_f = float(den)
                fps = float(num) / den_f if den_f else None
            except (TypeError, ValueError):
                fps = None
    return ProbeInfo(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=fps,
        has_audio=astream is not None,
    )


# ----------------------------------------------------------------- run_qc


def run_qc(
    project: Project,
    timeline: Timeline | None = None,
    *,
    deep: bool = False,
    extract_frames: bool = True,
) -> QCReport:
    report = QCReport()
    config = project.load_config()
    if timeline is None:
        timeline = project.load_timeline()

    statuses = evaluate_all(project)
    probe_cache: dict[Path, ProbeInfo | None] = {}

    def probe(path: Path) -> ProbeInfo | None:
        if path not in probe_cache:
            probe_cache[path] = _probe_info(path)
        return probe_cache[path]

    selected: dict[str, tuple[Path, ProbeInfo | None]] = {}

    _existence_shots(project, report, statuses, probe, selected)
    _staleness_info(report, statuses)
    _voice_info(project, report)
    if timeline is not None:
        _existence_timeline(project, report, timeline, probe)
        _existence_audio_policy(project, report, timeline)
        _technical_clips(project, report, timeline, probe)
        _technical_captions(project, report, timeline)
    _technical_take_resolution(project, report, config, selected)
    _final_render(project, report, config, timeline, deep)
    if extract_frames:
        _content_frames(project, report, selected)
    _content_checkers(project, report, statuses, deep)

    return report


def _content_checkers(project, report, statuses, deep: bool) -> None:
    """§9 third-round stance: the content tier is a machine checker chain the
    engine runs itself — no agent required. must_show assertions always run
    for shots that declare them; black/freeze/mcp-video probes join on deep."""
    from .content import content_checks

    for st in statuses:
        if st.take is None or st.take.media_path is None:
            continue
        try:
            shot = project.load_shot(st.shot_id)
            report.items.extend(content_checks(project, shot, st.take, deep=deep))
        except Exception as exc:  # a broken checker never breaks QC itself
            report.add("warn", "content", st.shot_id,
                       f"content checker chain errored: {exc}",
                       suggestion="agent 终审;或查看 manju doctor")


# --------------------------------------------------------- existence layer


def _existence_shots(project, report, statuses, probe, selected) -> None:
    for st in statuses:
        if st.selected_take is None:
            continue
        take = st.take
        if take is None or take.media_path is None or not take.media_path.exists():
            report.add(
                "error", "existence", st.shot_id,
                f"selected take {st.selected_take!r} has no media file on disk (missing)",
                suggestion="regenerate the shot or re-import media",
            )
            continue
        info = probe(take.media_path)
        if info is None:
            report.add(
                "error", "existence", st.shot_id,
                f"selected take media not readable by ffprobe: "
                f"{project.relpath(take.media_path)}",
                suggestion="the file may be corrupt — regenerate or re-import",
            )
            continue
        selected[st.shot_id] = (take.media_path, info)


def _staleness_info(report, statuses: list[ShotBuildStatus]) -> None:
    """Surface build/staleness state as info items (§4.3, §9)."""
    notes = {
        ShotState.STALE: "shot spec changed after the selected take was generated (stale)",
        ShotState.MISSING: "no takes yet — build will generate",
        ShotState.NEEDS_SELECTION: "takes exist but none is selected",
    }
    for st in statuses:
        msg = notes.get(st.state)
        if msg:
            report.add("info", "existence", st.shot_id, msg)


def _voice_info(project, report) -> None:
    """Voice staleness as QC info (M3): the film still builds with a stale
    voice — the selected line simply predates the current text (§4.3)."""
    try:
        from ..build.voice import VoiceState, evaluate_all_voices

        for vs in evaluate_all_voices(project):
            if vs.state == VoiceState.STALE:
                report.add(
                    "info", "existence", vs.shot_id,
                    "voice take predates the current dialogue/voice reference (stale)",
                    suggestion=f"manju voice {vs.shot_id} 重配音(§4.3:默认不自动重做)",
                )
            elif vs.state == VoiceState.MISSING:
                report.add(
                    "info", "existence", vs.shot_id,
                    "dialogue has no voice take yet",
                    suggestion="配置 tts manifest 后 build 自动补齐,或手放 voice_take_NN.wav",
                )
    except Exception:
        pass  # advisory only


def _existence_timeline(project, report, timeline: Timeline, probe) -> None:
    def check(source: str, label: str, *, readable: bool) -> None:
        try:
            path = project.resolve(source)
        except Exception:
            report.add("error", "existence", "timeline",
                       f"{label} source path is invalid: {source}")
            return
        if not path.exists():
            report.add("error", "existence", "timeline",
                       f"{label} source missing: {source}")
            return
        if readable and probe(path) is None:
            report.add("error", "existence", "timeline",
                       f"{label} source not readable by ffprobe: {source}")

    for clip in timeline.tracks.video:
        check(clip.source, f"video {clip.shot}/{clip.take}", readable=True)
    for clip in timeline.tracks.voice:
        check(clip.source, "voice", readable=False)
    for clip in timeline.tracks.music:
        check(clip.source, "music", readable=False)


def _existence_audio_policy(project, report, timeline: Timeline) -> None:
    """Audio policy checks (§9), read from rules against the compiled video
    track so both halves of a skip are surfaced:

      - a missing SFX / ambient / transition-sound source file → error;
      - an SFX anchor that names a shot NOT on the timeline → warn (the
        compiler deterministically drops that SFX, so without this the clip
        would silently vanish).
    """
    from ..timeline.anchors import resolve_anchor

    audio = project.load_rules().audio
    video_clips = list(timeline.tracks.video)

    def check_file(source: str, label: str) -> None:
        try:
            path = project.resolve(source)
        except Exception:
            report.add("error", "existence", "timeline",
                       f"{label} source path is invalid: {source}",
                       suggestion="use a project-relative path under the project root")
            return
        if not path.exists():
            report.add("error", "existence", "timeline",
                       f"{label} source missing: {source}",
                       suggestion="drop the audio file in media/imports/ and point "
                                  "the audio policy at it (rules.yaml → audio)")

    for i, spec in enumerate(audio.sfx):
        check_file(spec.source, f"sfx #{i}")
        if resolve_anchor(spec.at, spec.offset_ms, video_clips) is None:
            report.add(
                "warn", "technical", "timeline",
                f"sfx #{i} anchor {spec.at!r} does not resolve — no shot on the "
                "timeline matches it, so this SFX is skipped",
                suggestion='anchor grammar: "" (absolute), "shot:<id>", '
                           '"shot:<id>:start" or "shot:<id>:end"; check the shot id',
            )
    if audio.ambient.source:
        check_file(audio.ambient.source, "ambient")
    if audio.transition_sound:
        check_file(audio.transition_sound, "transition sound")


# --------------------------------------------------------- technical layer


def _technical_clips(project, report, timeline: Timeline, probe) -> None:
    # Frame-grid hint (review open issue #2): the compiler always emits
    # frame-aligned durations (FIX-B), so an off-grid clip means a
    # hand-authored/edited timeline — name the cause instead of letting the
    # final fps/duration checks fail mysteriously later.
    from ..timeline.compiler import snap_to_frame_grid

    for clip in timeline.tracks.video:
        snapped = snap_to_frame_grid(clip.duration_ms, timeline.fps)
        if snapped != clip.duration_ms:
            report.add(
                "warn", "technical", clip.shot,
                f"clip duration {clip.duration_ms}ms is not frame-aligned at "
                f"{timeline.fps}fps ({clip.duration_ms * timeline.fps / 1000:.2f} frames) "
                "— hand-authored timeline? nearest grid value is "
                f"{snapped}ms",
                suggestion="snap durations to the FIX-B rule: "
                           "round(round(ms*fps/1000)*1000/fps); see README 'Frame-grid rule'",
            )

    for clip in timeline.tracks.video:
        try:
            path = project.resolve(clip.source)
        except Exception:
            continue
        if not path.exists():
            continue
        info = probe(path)
        if info is None or info.duration_ms is None:
            continue
        # A source shorter than its clip is only a warn: normalize pads it (§9).
        if info.duration_ms + _DURATION_TOL_MS < clip.duration_ms:
            report.add(
                "warn", "technical", clip.shot,
                f"source is shorter than the clip: source "
                f"{info.duration_ms}ms vs clip {clip.duration_ms}ms "
                f"(normalize will pad, but check the intent)",
                suggestion="regenerate at the clip duration if padding is undesirable",
            )


def _technical_captions(project, report, timeline: Timeline) -> None:
    rules = project.load_rules().captions
    budget = max(1, rules.max_chars_per_line * rules.max_lines)
    lines = timeline.tracks.captions

    for i, cap in enumerate(lines):
        text = (cap.text or "").strip()
        if not text:
            report.add("error", "technical", "timeline",
                       f"caption #{i} has empty text",
                       suggestion="remove the caption or supply text")
        if cap.end_ms <= cap.start_ms:
            report.add("error", "technical", "timeline",
                       f"caption #{i} has non-positive duration "
                       f"(start={cap.start_ms}ms end={cap.end_ms}ms)",
                       suggestion="fix the caption interval so end > start")
        if len(text) > budget:
            report.add("warn", "technical", "timeline",
                       f"caption #{i} is {len(text)} chars, over the "
                       f"{budget}-char budget ({rules.max_chars_per_line}"
                       f"×{rules.max_lines})",
                       suggestion="shorten the line or split the caption")

    # overlapping intervals (compare in chronological order)
    ordered = sorted(enumerate(lines), key=lambda t: (t[1].start_ms, t[1].end_ms))
    for (pi, prev), (ci, cur) in zip(ordered, ordered[1:]):
        if cur.start_ms < prev.end_ms:
            report.add("warn", "technical", "timeline",
                       f"captions #{pi} and #{ci} overlap "
                       f"(#{pi} ends {prev.end_ms}ms, #{ci} starts {cur.start_ms}ms)",
                       suggestion="adjust caption timings so they do not overlap")


def _technical_take_resolution(project, report, config: ProjectConfig, selected) -> None:
    for shot_id, (_, info) in selected.items():
        if info is None or info.width is None or info.height is None:
            continue
        if (info.width, info.height) != (config.width, config.height):
            report.add(
                "info", "technical", shot_id,
                f"take resolution {info.width}x{info.height} differs from project "
                f"{config.width}x{config.height} (normalize handles scaling/padding)",
            )


def _final_render(project, report, config: ProjectConfig, timeline, deep: bool) -> None:
    final = _newest_final(project)
    if final is None:
        return
    info = _probe_info(final)
    if info is None:
        report.add("error", "technical", "final",
                   f"final render not readable by ffprobe: {final.name}")
        return

    if info.width is not None and info.height is not None:
        if (info.width, info.height) != (config.width, config.height):
            report.add(
                "error", "technical", "final",
                f"final render resolution {info.width}x{info.height} != project "
                f"{config.width}x{config.height}",
                suggestion="re-render at the project resolution",
            )

    # Frame-rate contract (FIX-B): the final's r_frame_rate must equal the
    # project fps exactly — a drifted rate (e.g. 143/6 from concat of
    # non-frame-aligned segments) desyncs everything downstream.
    if info.fps is not None and config.fps and abs(info.fps - config.fps) > 1e-6:
        report.add(
            "error", "technical", "final",
            f"final r_frame_rate {info.fps} != project fps {config.fps}",
            suggestion="re-render (the pipeline forces fps=<project fps> in the "
                       "final chain; a mismatch means an old or foreign final)",
        )

    # Duration contract (FIX-B): |final - timeline| ≤ 1 frame.
    if timeline is not None and info.duration_ms is not None and config.fps:
        frame_ms = 1000.0 / config.fps
        drift = abs(info.duration_ms - timeline.duration_ms)
        if drift > frame_ms + 0.5:  # +0.5ms for probe rounding
            report.add(
                "error", "technical", "final",
                f"final duration {info.duration_ms}ms differs from timeline "
                f"{timeline.duration_ms}ms by {drift:.1f}ms (> 1 frame = {frame_ms:.1f}ms)",
                suggestion="re-render; the timeline and final are out of sync",
            )

    if deep:
        _deep_detectors(report, final)


def _deep_detectors(report, final: Path) -> None:
    """Optional deep pass: blackdetect + volumedetect (§9). Never fails QC."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(final),
             "-vf", f"blackdetect=d={_BLACKDETECT_D}:pix_th=0.10",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
        if "black_start" in proc.stderr:
            spans = re.findall(r"black_start:(\S+)\s+black_end:(\S+)", proc.stderr)
            report.add(
                "warn", "technical", "final",
                f"blackdetect found {len(spans) or 'some'} all-black span(s) "
                f"(>= {_BLACKDETECT_D}s)",
                suggestion="inspect the render for dropped/black clips",
            )
    except Exception:
        pass  # a broken detector never fails QC

    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(final),
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
        m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", proc.stderr)
        if m and float(m.group(1)) < _SILENCE_DB:
            report.add(
                "warn", "technical", "final",
                f"mean_volume {m.group(1)}dB is below {_SILENCE_DB}dB — near silent",
                suggestion="check the audio mix / voice track",
            )
    except Exception:
        pass


def _newest_final(project: Project) -> Path | None:
    finals: list[tuple[int, Path]] = []
    if not project.final_dir.exists():
        return None
    for p in project.final_dir.glob("final_v*.mp4"):
        m = re.match(r"final_v(\d+)$", p.stem)
        if m:
            finals.append((int(m.group(1)), p))
    if not finals:
        return None
    return max(finals, key=lambda t: t[0])[1]


# ----------------------------------------------------------- content layer


def _content_frames(project, report, selected) -> None:
    """Extract one mid-point frame per selected take — eyes for the agent (§9)."""
    frames_dir = project.reports_dir / "frames"
    for shot_id, (media_path, info) in selected.items():
        dest = frames_dir / f"{shot_id}.jpg"
        mid_s = 0.0
        if info is not None and info.duration_ms:
            mid_s = max(0.0, (info.duration_ms / 2.0) / 1000.0)
        if _extract_frame(media_path, dest, mid_s):
            report.add(
                "info", "content", shot_id,
                f"mid-point frame for visual review: {project.relpath(dest)}",
            )
        else:
            report.add(
                "info", "content", shot_id,
                f"could not extract a review frame from {project.relpath(media_path)}",
            )


def _extract_frame(media_path: Path, dest: Path, mid_s: float) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{mid_s:.3f}", "-i", str(media_path),
             "-frames:v", "1", "-q:v", "3", str(dest)],
            capture_output=True, timeout=60,
        )
    except Exception:
        return False
    return dest.exists()


# --------------------------------------------------------------- staleness


def stale_summary(project: Project) -> list[ShotBuildStatus]:
    """Per-shot build/staleness status (§4.3). QC also surfaces these as info
    items; callers wanting the structured list use this directly."""
    return evaluate_all(project)
