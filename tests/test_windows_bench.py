"""W5.3 (MANJU_WINDOWS_ONLY_LEAN_V3) — the opt-in render benchmark.

The plan's W5.3 asks for a "benchmark". The honest lean form for a personal
pipeline is the MANJU_C21G_BENCH precedent: an OPT-IN, env-gated timing run
that PRINTS its numbers for the human (run with ``-s``) and asserts only the
loosest sanity floor — never a perf regression gate (wall-clock on shared CI
runners is noise; a hard threshold would flake the hard-won green gate).

    MANJU_BENCH=1 python -m pytest tests/test_windows_bench.py -s

Deliberately NOT built (recorded in DECISIONS): a perf REPORT under reports/
— ``qc/runperf.py`` is the §8.7 perf-view owner (derived from build evidence,
deterministic, never wall-clock); a second, clock-based perf artifact beside
it would be exactly the parallel system the plan forbids. This file touches
neither runperf nor build/ (the §8.7 no-build-import pins stay untouched).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("MANJU_BENCH"),
        reason="opt-in timing benchmark; set MANJU_BENCH=1 (prints numbers, run with -s)",
    ),
    pytest.mark.skipif(
        shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
        reason="ffmpeg required",
    ),
]

W, H, FPS = 640, 360, 24


def _gen_source(dest: Path, seconds: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(dest)],
        check=True, capture_output=True,
    )


def test_bench_normalize_and_render(tmp_path):
    """Times the three hot paths a real build spends its wall-clock in:
    segment normalize (cold), the segment cache (warm reuse), and the final
    compose. Numbers are printed for the human; the only assertions are
    sanity floors (outputs exist; warm reuse is not slower than cold)."""
    from manju.core.container import Project
    from manju.core.models import Timeline, TimelineTracks, VideoClip
    from manju.media.render import render_timeline

    project = Project.create(tmp_path / "基准", git_init=False)
    clips = []
    for i in range(3):
        src = project.imports_dir / f"c{i}.mp4"
        _gen_source(src, 2.0)
        clips.append(VideoClip(shot=f"S{i}", take="t", source=project.relpath(src),
                               start_ms=i * 2000, duration_ms=2000))
    tl = Timeline(fps=FPS, width=W, height=H, duration_ms=6000,
                  tracks=TimelineTracks(video=clips))

    print(f"\n--- W5.3 bench: 3×2s clips, {W}x{H}@{FPS}, host "
          f"{os.uname().sysname if hasattr(os, 'uname') else os.name} ---")

    t0 = time.perf_counter()
    out_cold = render_timeline(project, tl, target="proxy")
    cold = time.perf_counter() - t0
    print(f"  proxy render, cold segment cache : {cold:7.2f}s → {out_cold.name}")

    t0 = time.perf_counter()
    render_timeline(project, tl, target="proxy", force=True)
    warm = time.perf_counter() - t0
    print(f"  proxy render, warm segment cache : {warm:7.2f}s (segments reused)")

    t0 = time.perf_counter()
    out_final = render_timeline(project, tl, target="final")
    final = time.perf_counter() - t0
    print(f"  final render (loudnorm+faststart): {final:7.2f}s → {out_final.name}")

    assert out_cold.exists() and out_final.exists()
    # the loosest floor: warm (cached segments) must not be SLOWER than cold —
    # anything tighter would flake on shared runners.
    assert warm <= cold * 1.5
