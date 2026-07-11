"""Deterministic BAD-MEDIA generator (AI_IDE_20A, contract §3 "Technical media"
+ §8 Harness: 完全离线的 deterministic fake path / 故障注入点明确).

Every clip is synthesized by ffmpeg from lavfi sources with FIXED arguments and
NO wall-clock — the same call reproduces the same technical facts on every run.
Output is validated by PROBE FACTS (ffprobe duration / fps / streams / size),
NEVER by output byte hash: ffmpeg's encoded bytes are not stable across ffmpeg/
codec versions, so pinning a sha256 on generated media would be a false
regression. That honesty is recorded in manifest.json's ``design_notes``.

Because the bytes are not pinned, generated media is NOT committed — a
session-scoped pytest fixture builds it into a tmp dir (the committed *generator*
plus generated media keeps the repo tiny, per the addendum). Covers the §3 fault
family: good / short / black-head+tail / freeze / bad-aspect / bad-fps /
corrupt-container / silent / clipped / audio-drift / subtitle-overlap /
text-mutation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Small + short so generation is fast and the tmp footprint is trivial.
W, H, FPS = 320, 240, 24

# bitexact everywhere we can: strip encoder version/timestamps from the
# container so runs differ as little as possible (facts are what we assert).
_BITEXACT = ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-map_metadata", "-1"]


class BadMediaError(RuntimeError):
    pass


def _run(args: list[str]) -> None:
    """Run one ffmpeg command, fully non-interactive. Raises on failure with the
    stderr tail so a generation break is legible."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-12:])
        raise BadMediaError(f"ffmpeg failed ({args}):\n{tail}")


def _x264(extra_out: list[str]) -> list[str]:
    return ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", *extra_out]


# --------------------------------------------------------------- generators
# Each generator writes ONE file into ``d`` and returns its Path. Signatures are
# uniform so build_all can iterate.


def gen_good(d: Path) -> Path:
    """A clean 2s clip: video + audio, correct 4:3 aspect, 24fps."""
    out = d / "good.mp4"
    _run([
        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        *_x264([]), "-c:a", "aac", "-shortest", *_BITEXACT, str(out),
    ])
    return out


def gen_short(d: Path) -> Path:
    """Far-too-short clip (0.25s) — trips minimum-duration gates."""
    out = d / "short.mp4"
    _run([
        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration=0.25",
        *_x264([]), *_BITEXACT, str(out),
    ])
    return out


def gen_black_head_tail(d: Path) -> Path:
    """1s black + 1s content + 1s black (3s). The >=1s black runs are caught by
    the production blackdetect gate (qc.content.black_detected, d=1.0)."""
    out = d / "black_head_tail.mp4"
    fc = (
        f"color=c=black:s={W}x{H}:r={FPS}:d=1[b1];"
        f"testsrc2=s={W}x{H}:r={FPS}:d=1[c];"
        f"color=c=black:s={W}x{H}:r={FPS}:d=1[b2];"
        f"[b1][c][b2]concat=n=3:v=1:a=0[v]"
    )
    _run(["-filter_complex", fc, "-map", "[v]", *_x264([]), *_BITEXACT, str(out)])
    return out


def gen_freeze(d: Path) -> Path:
    """A fully static 3s clip — the frozen interval clearly exceeds the
    production freezedetect gate's d=2 window (qc.content.freeze_detected)."""
    out = d / "freeze.mp4"
    _run([
        "-f", "lavfi", "-i", f"color=c=green:s={W}x{H}:r={FPS}:d=3",
        *_x264([]), *_BITEXACT, str(out),
    ])
    return out


def gen_bad_aspect(d: Path) -> Path:
    """A 4:1 letterbox-wrong aspect (400x100) clip."""
    out = d / "bad_aspect.mp4"
    _run([
        "-f", "lavfi", "-i", f"testsrc2=size=400x100:rate={FPS}:duration=1.5",
        *_x264([]), *_BITEXACT, str(out),
    ])
    return out


def gen_bad_fps(d: Path) -> Path:
    """An unusually low 5fps clip."""
    out = d / "bad_fps.mp4"
    _run([
        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=5:duration=2",
        "-r", "5", *_x264([]), *_BITEXACT, str(out),
    ])
    return out


def gen_corrupt_container(d: Path) -> Path:
    """A truncated MP4: encode a good clip, then keep only the first 800 bytes so
    the moov/container is incomplete and ffprobe FAILS. Deterministic (fixed
    prefix length, byte-truncation in Python, not a random flip)."""
    full = d / "_corrupt_src.mp4"
    _run([
        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration=1",
        *_x264([]), *_BITEXACT, str(full),
    ])
    data = full.read_bytes()
    out = d / "corrupt_container.mp4"
    out.write_bytes(data[:800])  # decapitated container
    full.unlink()
    return out


def gen_silent(d: Path) -> Path:
    """Video with NO audio stream (a silent deliverable that should have sound)."""
    out = d / "silent.mp4"
    _run([
        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration=2",
        *_x264([]), "-an", *_BITEXACT, str(out),
    ])
    return out


def gen_clipped(d: Path) -> Path:
    """Audio driven hard into 0dBFS clipping (sine at volume 8 → square-ish,
    peak pinned to full scale). Video is normal."""
    out = d / "clipped.mp4"
    _run([
        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-filter:a", "volume=8.0",
        *_x264([]), "-c:a", "aac", "-shortest", *_BITEXACT, str(out),
    ])
    return out


def gen_audio_drift(d: Path) -> Path:
    """Audio delayed 400ms behind the video (lip-sync drift). The audio stream
    carries a start offset ffprobe can see."""
    out = d / "audio_drift.mp4"
    _run([
        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration=2",
        "-itsoffset", "0.4", "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
        "-map", "0:v", "-map", "1:a",
        *_x264([]), "-c:a", "aac", *_BITEXACT, str(out),
    ])
    return out


def gen_subtitle_overlap(d: Path) -> Path:
    """An .ass with two Dialogue cues whose time ranges OVERLAP (0.0–3.0 and
    2.0–5.0 both cover 2.0–3.0). Not media — a text fixture the self-test parses
    to prove the overlap is real."""
    out = d / "subtitle_overlap.ass"
    out.write_text(
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 384\nPlayResY: 288\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, Alignment\n"
        "Style: Default,DejaVu Sans,24,&H00FFFFFF,2\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,第一行字幕\n"
        "Dialogue: 0,0:00:02.00,0:00:05.00,Default,,0,0,0,,重叠的第二行字幕\n",
        encoding="utf-8",
    )
    return out


def gen_text_mutation(d: Path) -> tuple[Path, Path]:
    """Two single-frame PNGs with DIFFERENT burned-in text ("SCENE 01" vs
    "SCENE 02") — a text/logo mutation across frames. drawtext uses a known
    system font; the two frames differ (distinct sha256), which the self-test
    asserts."""
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    outs = []
    for tag, name in (("SCENE 01", "text_mutation_a.png"),
                      ("SCENE 02", "text_mutation_b.png")):
        out = d / name
        _run([
            "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}",
            "-vf", (f"drawtext=fontfile={font}:text='{tag}':x=40:y=100:"
                    f"fontsize=40:fontcolor=white"),
            "-frames:v", "1", *_BITEXACT, str(out),
        ])
        outs.append(out)
    return outs[0], outs[1]


# case_id -> generator callable (each writes into the dest dir).
GENERATORS = {
    "technical.good": gen_good,
    "technical.short": gen_short,
    "technical.black_head_tail": gen_black_head_tail,
    "technical.freeze": gen_freeze,
    "technical.bad_aspect": gen_bad_aspect,
    "technical.bad_fps": gen_bad_fps,
    "technical.corrupt_container": gen_corrupt_container,
    "technical.silent": gen_silent,
    "technical.clipped": gen_clipped,
    "technical.audio_drift": gen_audio_drift,
    "technical.subtitle_overlap": gen_subtitle_overlap,
    # text_mutation returns TWO paths — handled specially in build_all.
}


def build_all(dest: Path) -> dict[str, Path]:
    """Generate every technical fixture into ``dest``. Returns {case_id: path}.
    ``technical.text_mutation`` yields two ids (``.a`` / ``.b``)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for case_id, gen in GENERATORS.items():
        out[case_id] = gen(dest)
    a, b = gen_text_mutation(dest)
    out["technical.text_mutation.a"] = a
    out["technical.text_mutation.b"] = b
    return out


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        for cid, p in build_all(Path(td)).items():
            print(f"{cid:34s} {p.name:26s} {p.stat().st_size:7d}B")
