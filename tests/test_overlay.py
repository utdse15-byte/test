"""Tests for the title-card overlay layer (§7 step ④, §13 M4).

Two halves:
  (a) the compiler is a pure function — no ffmpeg needed. Overlay is OFF by
      default (zero behaviour change); when enabled it emits exactly one
      title-card clip, clamped to the film length, and a title-card text change
      re-fingerprints the compile.
  (b) end-to-end (ffmpeg required) — a tiny 2-shot project renders a proxy with
      a Chinese title card burned in, and the overlay track is deterministic.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.models import (
    Dialogue,
    ProjectConfig,
    ShotSpec,
    TimelineRules,
    TitleCardRules,
)
from manju.timeline.compiler import (
    CompileInput,
    ShotInput,
    compile_timeline,
    gather_compile_input,
)


# --------------------------------------------------------- (a) compiler-only


def _shot_input(shot_id="S001", *, duration="auto",
                take_source="gen/S001/take_01.mp4", take_duration_ms=4000) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=shot_id, duration=duration, dialogue=Dialogue(speaker="linxia", text="")),
        take_name="take_01",
        take_source=take_source,
        take_duration_ms=take_duration_ms,
    )


def _input(*shots, rules=None) -> CompileInput:
    return CompileInput(
        config=ProjectConfig(name="t"),
        rules=rules or TimelineRules(),
        shots=list(shots),
    )


def test_no_overlay_by_default():
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0)))
    assert tl.tracks.overlay == []


def test_no_overlay_when_disabled_or_text_empty():
    # enabled but empty text → nothing
    r1 = TimelineRules(title_card=TitleCardRules(enabled=True, text=""))
    assert compile_timeline(_input(_shot_input("S001", duration=2.0), rules=r1)).tracks.overlay == []
    # text set but disabled → nothing
    r2 = TimelineRules(title_card=TitleCardRules(enabled=False, text="片头"))
    assert compile_timeline(_input(_shot_input("S001", duration=2.0), rules=r2)).tracks.overlay == []


def test_one_overlay_with_correct_fields_and_duration_clamped():
    rules = TimelineRules(
        title_card=TitleCardRules(enabled=True, text="雨夜便利店", duration_ms=1500, template="chapter")
    )
    # single 1.0s shot → total 1000ms, shorter than the 1500ms card duration.
    tl = compile_timeline(_input(_shot_input("S001", duration=1.0), rules=rules))
    assert tl.duration_ms == 1000
    assert len(tl.tracks.overlay) == 1
    ov = tl.tracks.overlay[0]
    assert ov.kind == "title_card"
    assert ov.template == "chapter"
    assert ov.text == "雨夜便利店"
    assert ov.start_ms == 0
    assert ov.duration_ms == 1000  # clamped to the (shorter) film length


def test_overlay_duration_not_clamped_when_film_is_longer():
    rules = TimelineRules(
        title_card=TitleCardRules(enabled=True, text="雨夜便利店", duration_ms=1500)
    )
    tl = compile_timeline(_input(_shot_input("S001", duration=5.0), rules=rules))
    assert tl.tracks.overlay[0].duration_ms == 1500  # under the total → unchanged


def test_fingerprint_changes_when_title_card_text_changes():
    base = _input(
        _shot_input("S001"),
        rules=TimelineRules(title_card=TitleCardRules(enabled=True, text="雨夜便利店")),
    )
    changed = _input(
        _shot_input("S001"),
        rules=TimelineRules(title_card=TitleCardRules(enabled=True, text="晴日咖啡馆")),
    )
    assert base.fingerprint() != changed.fingerprint()
    assert (
        compile_timeline(base).meta.compiled_from
        != compile_timeline(changed).meta.compiled_from
    )


# ------------------------------------------------------- (b) end-to-end (ffmpeg)

pytestmark_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required for the title-card render",
)


def _real_clip(dest: Path, freq: int) -> None:
    """A distinct 1s testsrc2 clip with a sine tone (h264 + aac), like make_sample."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


@pytestmark_ffmpeg
def test_title_card_proxy_render_and_determinism(tmp_project, add_shot, tmp_path):
    from manju.build.graph import run_build
    from manju.core.models import ProbeInfo, TakeSidecar

    project = tmp_project
    for i, sid in enumerate(("S001", "S002"), start=1):
        clip = tmp_path / f"{sid}.mp4"
        _real_clip(clip, 220 + i * 60)
        take = project.register_take(
            sid,
            clip,
            TakeSidecar(
                provider="manual_import",
                spec_hash="manual",
                probe=ProbeInfo(duration_ms=1000, width=540, height=960, fps=24, has_audio=True),
            ),
        )
        add_shot(project, sid, status={"selected_take": take.name})

    rules = project.load_rules()
    rules.title_card.enabled = True
    rules.title_card.text = "雨夜便利店"
    project.save_rules(rules)

    result = run_build(project, target="proxy")
    assert result.ok, f"errors={result.errors} warnings={result.warnings}"
    assert result.render_path, "no proxy produced"
    proxy = project.resolve(result.render_path)
    assert proxy.exists() and proxy.stat().st_size > 0

    timeline = project.load_timeline()
    assert len(timeline.tracks.overlay) == 1
    ov = timeline.tracks.overlay[0]
    assert ov.kind == "title_card" and ov.text == "雨夜便利店"
    assert ov.duration_ms == 1500  # 2×1s film is longer than the card → not clamped

    # the overlay track (and the whole compile) is deterministic across compiles.
    probe = lambda p: 1000  # noqa: E731 — probe stub; sidecar probes are used anyway
    a = compile_timeline(gather_compile_input(project, probe))
    b = compile_timeline(gather_compile_input(project, probe))
    assert a.tracks.overlay == b.tracks.overlay
    assert a.model_dump_json() == b.model_dump_json()
