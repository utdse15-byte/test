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
        # Our render pipeline pads short takes to the timeline duration (§7 ①,
        # tpad clone); drafts express the same intent as a mild slow-down:
        # source shorter than target -> speed = source/target automatically.
        source_us = min(material.duration, target_us)
        segment = lib.VideoSegment(
            material,
            trange(clip.start_ms * ms, target_us),
            source_timerange=trange(0, source_us),
        )
        script.add_segment(segment, "main")

    def _add_audio(track_name: str, clips) -> None:
        script.add_track(lib.TrackType.audio, track_name)
        for a in clips:
            material = lib.AudioMaterial(str(project.resolve(a.source)))
            target_us = (a.duration_ms or timeline.duration_ms) * ms
            # audio must never be time-stretched: trim to material length
            target_us = min(target_us, material.duration)
            if target_us <= 0:
                continue
            script.add_segment(
                lib.AudioSegment(material, trange(a.start_ms * ms, target_us),
                                 volume=_volume_from_db(a.gain_db)),
                track_name,
            )

    if timeline.tracks.voice:
        _add_audio("voice", timeline.tracks.voice)
    if timeline.tracks.music:
        _add_audio("music", timeline.tracks.music)

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
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"capcut-cli errored: {exc}"]
    if proc.returncode == 0:
        return []
    output = (proc.stdout + proc.stderr).strip()
    return [line for line in output.splitlines() if line.strip()][:50] or [
        f"capcut-cli lint failed with exit {proc.returncode}"
    ]
