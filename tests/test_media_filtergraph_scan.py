"""media/ filtergraph escaping — two HIGH bugs found by the media/ scan, each
adversarially reproduced END-TO-END against real ffmpeg before fixing.

1. render: the subtitle burn emitted the POSITIONAL `ass=<escaped>` form, but a
   compiled .ass path carrying '=' (a project under D:\\剪辑\\项目=2026\\...)
   makes the ass filter's own option parser split the positional value at the
   first '=' -> "Option not found" -> EVERY subtitled render for that project
   fails. Fixed to the KEYED form `ass=filename=<escaped>` (like textfile=/
   fontfile=), which lands the '=' inside the value.
2. reframe: ffmpeg_crop_expr embedded time-varying x/y expressions that carry
   commas (if(lt(t,T),A,B)) into a comma-joined -vf, so the graph parser split
   the crop filter at those commas -> "Filter not found". Fixed by escaping the
   expression commas as "\\,".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.models import (
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
)

FFMPEG = shutil.which("ffmpeg")
has_ffmpeg = FFMPEG is not None

_ASS = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 192\nPlayResY: 108\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
    "Alignment, MarginL, MarginR, MarginV, Encoding\n"
    "Style: Default,Arial,16,&H00FFFFFF,2,10,10,10,1\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Text\n"
    "Dialogue: 0,0:00:00.00,0:00:01.00,Default,hi\n"
)


def _tiny_mp4(path: Path, duration_s: float = 0.5) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=blue:s=320x240:d={duration_s}:r=24",
         "-f", "lavfi", "-i", f"sine=f=440:d={duration_s}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


# --------------------------------------------------------------------------- #
# (1) the ass= subtitle burn survives a path containing '='                      #
# --------------------------------------------------------------------------- #


def test_ass_filter_uses_keyed_filename_form():
    """Behavioral pin: the burn emits the KEYED `ass=filename=` form (a '=' in
    the path only survives inside a keyed value, never a positional one)."""
    from manju.media.render import _ass_filter

    assert _ass_filter("/tmp/a/c.ass").startswith("ass=filename=")


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg required")
def test_render_timeline_ass_path_with_equals_burns(
        tmp_project, add_shot, make_take, tmp_path):
    """End-to-end, the real call site: a compiled .ass under a directory carrying
    '=' must burn (exit 0). Pre-fix the positional ass= emission failed with the
    exact 'Option not found' the ass filter's option parser raises on the '='."""
    from manju.core.spec import compute_spec_hash
    from manju.media.render import render_timeline

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "中文"})
    h = compute_spec_hash(shot, tmp_project.load_bible())
    take = make_take(tmp_project, "S001", h)
    media = tmp_project.root / "media" / "gen" / "S001" / f"{take.name}.mp4"
    _tiny_mp4(media, 0.5)
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )

    tl = Timeline(
        meta=TimelineMeta(compiled_from="test"),
        fps=24, width=320, height=240, duration_ms=500,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take=take.name,
                             source=f"media/gen/S001/{take.name}.mp4",
                             start_ms=0, duration_ms=500)],
        ),
    )
    tmp_project.save_timeline(tl)

    assdir = tmp_path / "proj=2026"
    assdir.mkdir()
    ass = assdir / "captions.ass"
    ass.write_text(_ASS, encoding="utf-8")

    out = tmp_project.final_dir / "out.mp4"
    rendered = render_timeline(
        tmp_project, tl, target="final", out_path=out, ass_file=ass, force=True)
    assert rendered.exists() and rendered.stat().st_size > 0


# --------------------------------------------------------------------------- #
# (2) the reframe crop expression survives its own commas in a -vf graph         #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg required")
def test_reframe_crop_expr_multi_keyframe_burns(tmp_path):
    """End-to-end: a multi-keyframe crop expression (which contains if(lt(t,..))
    commas) joined into a -vf graph must parse and run (exit 0). Pre-fix the bare
    commas split the filtergraph -> 'Filter not found'."""
    from manju.media.reframe import OK, ffmpeg_crop_expr

    compiled = {"status": OK, "keyframes": [
        {"t_ms": 0, "w": 200, "h": 200, "x": 10, "y": 5},
        {"t_ms": 500, "w": 200, "h": 200, "x": 40, "y": 20},
        {"t_ms": 1000, "w": 200, "h": 200, "x": 80, "y": 40},
    ]}
    crop = ffmpeg_crop_expr(compiled, fps=24)
    vf = f"{crop},scale=320:320,setsar=1"
    out = tmp_path / "reframe_out.mp4"
    proc = subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-t", "0.3", "-i", "color=c=black:s=400x400:r=24",
         "-vf", vf, "-frames:v", "3", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and out.stat().st_size > 0
