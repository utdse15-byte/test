"""Native draft exporters via pyJianYingDraft / pycapcut (§13 M1, decision 8).

Dual-path JianYing export: pyJianYingDraft is the PRIMARY path (this module),
the diff-stable skeleton in ``jianying.py`` plus capcut-cli lint is the
secondary/companion; final.mp4 + SRT + OTIO always remain the fallback exits
(§14 — draft-format drift is the top external risk). pycapcut shares the same
family API, so international CapCut costs almost nothing extra (§2.5 P0).

Every third-party library sits behind the adapter wall: absence raises
``ExporterUnavailable`` with a actionable message; it never crashes an export
run — callers degrade to the skeleton path.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.models import Timeline

if TYPE_CHECKING:
    from ..core.container import Project


class ExporterUnavailable(RuntimeError):
    pass


def _volume_from_db(gain_db: float) -> float:
    return max(0.0, min(2.0, 10 ** (gain_db / 20.0)))


def _build_script(lib, project: "Project", timeline: Timeline):
    """Shared builder — pyJianYingDraft and pycapcut are same-family APIs
    (§2.5), parameterized by the imported module. Times are µs in drafts."""
    try:
        script = lib.ScriptFile(timeline.width, timeline.height, timeline.fps, False)
    except TypeError:  # pycapcut: ScriptFile(width, height, fps=30)
        script = lib.ScriptFile(timeline.width, timeline.height, timeline.fps)

    trange = lib.Timerange
    ms = 1000  # ms -> µs

    script.add_track(lib.TrackType.video, "main")
    for clip in timeline.tracks.video:
        material = lib.VideoMaterial(str(project.resolve(clip.source)))
        target_us = clip.duration_ms * ms
        # Round-W (#10): a virtual trim's source_in_ms is where the internal
        # render actually seeks to — the draft's source_timerange must start
        # there too, or the desktop app opens a different picture than the one
        # Manju rendered. Default 0 keeps every untouched clip byte-identical.
        in_us = max(0, clip.source_in_ms * ms)
        # Our render pipeline pads short takes to the timeline duration (§7 ①,
        # tpad clone); drafts express the same intent as a mild slow-down:
        # source (from the in-point) shorter than target -> speed =
        # source/target automatically.
        avail_us = max(0, material.duration - in_us) if material.duration else target_us
        source_us = min(avail_us, target_us)
        vseg_kwargs: dict = {"source_timerange": trange(in_us, source_us)}
        # Round-T: the footage's OWN audio level/mute — carried as the segment
        # volume when non-default (mute -> 0). Guarded: older library builds
        # without a VideoSegment ``volume`` kwarg keep today's behaviour.
        if clip.source_mute:
            vseg_kwargs["volume"] = 0.0
        elif clip.source_gain_db:
            vseg_kwargs["volume"] = _volume_from_db(clip.source_gain_db)
        try:
            segment = lib.VideoSegment(material, trange(clip.start_ms * ms, target_us),
                                       **vseg_kwargs)
        except TypeError:  # library build without a volume kwarg
            segment = lib.VideoSegment(material, trange(clip.start_ms * ms, target_us),
                                       source_timerange=trange(in_us, source_us))
        script.add_segment(segment, "main")

    def _audio_segment(a):
        """Build one AudioSegment: gain -> volume, trimmed to the material length
        so audio is never time-stretched. A looped ambient bed carries no loop
        primitive in a draft — like BGM it is referenced over its span and
        trimmed to the (possibly shorter) source file. Returns (segment,
        start_us, end_us), or None when the clip trims to nothing."""
        material = lib.AudioMaterial(str(project.resolve(a.source)))
        target_us = (a.duration_ms or timeline.duration_ms) * ms
        # Round-T: BGM/ambient in-point — seek into the SOURCE (start_offset_ms)
        # and bound the read to what remains after the seek (default 0 = today).
        in_us = max(0, a.start_offset_ms * ms)
        target_us = min(target_us, max(0, material.duration - in_us))
        if target_us <= 0:
            return None
        start_us = a.start_ms * ms
        vol = _volume_from_db(a.gain_db)
        if in_us > 0:
            # Only reshape the source read for a real in-point; guarded so a
            # library build without a source_timerange kwarg still exports.
            try:
                segment = lib.AudioSegment(material, trange(start_us, target_us),
                                           source_timerange=trange(in_us, target_us),
                                           volume=vol)
            except TypeError:
                segment = lib.AudioSegment(material, trange(start_us, target_us), volume=vol)
        else:  # default in-point: byte-identical to before round-T
            segment = lib.AudioSegment(material, trange(start_us, target_us), volume=vol)
        return segment, start_us, start_us + target_us

    def _add_audio(track_name: str, clips) -> None:
        """Voice/music/ambient: one lane each — they never self-overlap (voice is
        sequential; music and the ambient bed are single spanning clips)."""
        script.add_track(lib.TrackType.audio, track_name)
        for a in clips:
            built = _audio_segment(a)
            if built is not None:
                script.add_segment(built[0], track_name)

    def _add_sfx(clips) -> None:
        """SFX are transient hits + per-cut transition sounds and CAN overlap (a
        hit and a whoosh may land on the same cut). pyJianYingDraft/pycapcut
        forbid overlapping segments within a track, so spread them across as many
        lanes as needed — greedy first-fit by start time, the same way a human
        stacks SFX on extra audio lanes; render.py mixes every lane regardless."""
        lane_names: list[str] = []
        lane_ends: list[int] = []  # last segment end (µs) per lane
        for a in sorted(clips, key=lambda c: c.start_ms):
            built = _audio_segment(a)
            if built is None:
                continue
            segment, start_us, end_us = built
            lane = next((i for i, e in enumerate(lane_ends) if e <= start_us), None)
            if lane is None:  # no free lane at this time → open another
                lane = len(lane_names)
                name = "sfx" if lane == 0 else f"sfx_{lane + 1}"
                script.add_track(lib.TrackType.audio, name)
                lane_names.append(name)
                lane_ends.append(0)
            script.add_segment(segment, lane_names[lane])
            lane_ends[lane] = end_us

    if timeline.tracks.voice:
        _add_audio("voice", timeline.tracks.voice)
    if timeline.tracks.music:
        _add_audio("music", timeline.tracks.music)
    if timeline.tracks.sfx:
        _add_sfx(timeline.tracks.sfx)
    if timeline.tracks.ambient:
        _add_audio("ambient", timeline.tracks.ambient)

    if timeline.tracks.captions:
        script.add_track(lib.TrackType.text, "captions")
        for cap in timeline.tracks.captions:
            segment = lib.TextSegment(
                cap.text,
                trange(cap.start_ms * ms, max(1, cap.end_ms - cap.start_ms) * ms),
                clip_settings=lib.ClipSettings(transform_y=-0.75),  # bottom safe area
            )
            script.add_segment(segment, "captions")
    return script


def _export_native(lib_name: str, out_dir: Path, project: "Project",
                   timeline: Timeline) -> Path:
    try:
        import importlib

        lib = importlib.import_module(lib_name)
    except ImportError as exc:
        raise ExporterUnavailable(
            f"{lib_name} is not installed — pip install {lib_name} "
            f"(the skeleton draft + final.mp4/SRT/OTIO remain available)"
        ) from exc
    script = _build_script(lib, project, timeline)
    out_dir.mkdir(parents=True, exist_ok=True)
    draft_path = out_dir / "draft_content.json"
    script.dump(str(draft_path))
    return draft_path


def export_jianying_native(project: "Project", timeline: Timeline) -> Path:
    """PRIMARY JianYing path (§13 M1): a real pyJianYingDraft draft folder.
    Open it with the pinned JianYing desktop version (auto-update OFF, §14)."""
    name = project.load_config().name
    return _export_native(
        "pyJianYingDraft", project.exports_dir / "jianying" / f"{name}_native",
        project, timeline,
    )


def export_capcut_native(project: "Project", timeline: Timeline) -> Path:
    """International CapCut draft via pycapcut (same family API, §2.5 P0)."""
    name = project.load_config().name
    return _export_native(
        "pycapcut", project.exports_dir / "capcut" / f"{name}_native",
        project, timeline,
    )


# ------------------------------------------------------- capcut-cli wall


def capcut_cli_available() -> bool:
    return shutil.which("capcut-cli") is not None


def capcut_cli_lint(draft_dir: Path) -> list[str] | None:
    """Secondary-path lint using capcut-cli's existing lint/info capability
    (decision 8: 不自研). Returns None when the tool is absent — callers fall
    back to our own ``jianying.lint_draft``; a broken tool reports itself as a
    single problem string instead of raising (adapter wall)."""
    if not capcut_cli_available():
        return None
    try:
        proc = subprocess.run(
            ["capcut-cli", "lint", str(draft_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"capcut-cli errored: {exc}"]
    if proc.returncode == 0:
        return []
    output = (proc.stdout + proc.stderr).strip()
    return [line for line in output.splitlines() if line.strip()][:50] or [
        f"capcut-cli lint failed with exit {proc.returncode}"
    ]
