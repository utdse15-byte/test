"""FCPXML is the third round-trip carrier — a Resolve edit can come back.

Resolve is free and reads FCPXML, so it is the realistic zero-cost NLE for this
project. The exporter had written FCPXML since FP loop T2, but nothing wrote a
BASELINE for it, and `plan_roundtrip` loaded every carrier as JSON. An edited
.fcpxml therefore died on "无法解析 JSON" before any diff could run: the export
existed, the return path did not.

What these tests pin, in the order the failures actually bite:

  (a) exporting writes the baseline sidecar at all;
  (b) an UNEDITED export plans clean — a round-trip that invents changes is
      worse than none, because every row would need manual triage;
  (c) a real in-point trim is detected, with the take resolved from the asset
      path (FCPXML has nowhere to stamp a take, so `src` is the only carrier —
      without it the row is dropped at apply for a missing take);
  (d) a reorder is detected;
  (e) 1001/24000 survives the trip. FCPXML holds time as exact rational
      seconds, so this is the one carrier where a float conversion would be
      silent: 1001/24000 is not representable, and the drift would look like a
      one-frame trim on a clip nobody touched.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.build.roundtrip import apply_roundtrip, plan_roundtrip
from manju.core.container import Project
from manju.core.models import (
    EditRate,
    TakeSidecar,
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import write_yaml
from manju.exporters.fcpxml import export_fcpxml

SHOTS = ("S001", "S002")

ffmpeg_only = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)

FF = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]


def _stage_media(path: Path, *, real: bool) -> Path:
    """The source a take is registered from.

    Planning only reads the exported document, so a stub is enough and keeps
    those tests in the fast loop. Applying a trim probes the media, so those
    tests need a real (tiny) clip.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not real:
        path.write_bytes(b"\x00" * 64)
        return path
    # Longer than the 2000ms clip window on purpose: a source with material
    # past the out-point is the normal case (those are the handles), and it is
    # what lets a trim push the in-point later without running off the end.
    subprocess.run(
        [*FF, "-f", "lavfi", "-i", "testsrc2=size=128x128:rate=24:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path)],
        check=True,
    )
    return path


def _fabricate(project: Project, *, ntsc: bool = False,
               real: bool = False) -> Timeline:
    """A manual-mode two-shot timeline with media — ffmpeg-free unless ``real``.

    Media is staged OUTSIDE the take dir and then registered: `register_take`
    allocates the next free take name, so pre-writing `take_01.mp4` into the
    take dir would make it hand back `take_02` and leave the fixture's clip
    metadata disagreeing with its own asset path.
    """
    write_yaml(project.rules_path, TimelineRules(mode="manual").model_dump())
    clips = []
    for i, sid in enumerate(SHOTS):
        idx = project.load_index()
        if sid not in idx.order:
            idx.order.append(sid)
            project.save_index(idx)
        staged = _stage_media(project.root.parent / f"{sid}_src.mp4", real=real)
        info = project.register_take(
            sid, staged, TakeSidecar(provider="test", spec_hash="h"))
        clips.append(VideoClip(
            shot=sid, take=info.name, source=project.relpath(info.media_path),
            start_ms=i * 2000, duration_ms=2000, source_in_ms=0))
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        # The rational echo is the rate truth; `fps` stays its nominal int
        # mirror (24000/1001 -> 24), exactly as a R2-compiled project carries it.
        rate_echo=EditRate(num=24000, den=1001) if ntsc else None,
        tracks=TimelineTracks(video=clips))
    project.save_timeline(tl)
    return tl


@pytest.fixture
def exported(tmp_project: Project) -> Path:
    return export_fcpxml(tmp_project, _fabricate(tmp_project))


def _baseline(project: Project, exported: Path) -> Path:
    return project.root / "exports" / "fcpxml" / ".baseline" / f"{exported.stem}.json"


def _rows(project: Project, exported: Path) -> list[dict]:
    return plan_roundtrip(project, exported).get("rows") or []


def _classes(rows: list[dict]) -> set[str]:
    return {str(r.get("class")) for r in rows}


def _retime_spine_clip(path: Path, shot: str, *, start: str,
                       duration: str | None = None) -> None:
    """Rewrite one SPINE clip's in-point (and optionally its length).

    Anchored to `<asset-clip`: the `<asset>` RESOURCE for the same shot also
    carries `name=` and `start="0s"` and appears earlier in the document, so a
    looser pattern edits the resource — which changes no in-point and makes the
    test silently vacuous.

    Pass ``duration`` to model a RIPPLE trim, where dragging the head later
    also shortens the clip. Moving ``start`` alone pushes the out-point further
    into the source, which is a real edit an NLE can express but only if the
    media reaches that far.
    """
    text = path.read_text(encoding="utf-8")
    edited, n = re.subn(
        rf'(<asset-clip\b[^>]*?name="{shot}"[^>]*?)start="[^"]*"',
        rf'\1start="{start}"', text, count=1)
    assert n == 1, f"no spine asset-clip for {shot} in {path.name}"
    if duration is not None:
        edited, n = re.subn(
            rf'(<asset-clip\b[^>]*?name="{shot}"[^>]*?)duration="[^"]*"',
            rf'\1duration="{duration}"', edited, count=1)
        assert n == 1, f"no duration on the {shot} spine clip in {path.name}"
    path.write_text(edited, encoding="utf-8")


def _swap_spine_clips(path: Path) -> None:
    """Reverse the two spine clips, leaving the resource section untouched."""
    text = path.read_text(encoding="utf-8")
    clips = re.findall(r'[ \t]*<asset-clip\b[^>]*/>\n', text)
    assert len(clips) == 2, f"expected 2 spine clips, found {len(clips)}"
    path.write_text(
        text.replace(clips[0] + clips[1], clips[1] + clips[0]), encoding="utf-8")


# ------------------------------------------------------------------ (a) + (b)


def test_exporting_fcpxml_writes_a_roundtrip_baseline(
    tmp_project: Project, exported: Path
) -> None:
    assert _baseline(tmp_project, exported).exists()


def test_an_untouched_export_plans_clean(
    tmp_project: Project, exported: Path
) -> None:
    """No edit, no change rows — the plan must not invent work."""
    assert _classes(_rows(tmp_project, exported)) <= {"no_changes"}


def test_an_edited_fcpxml_is_not_reported_as_unparsable(
    tmp_project: Project, exported: Path
) -> None:
    """The original bug: XML reached the JSON loader and died as bad JSON."""
    _retime_spine_clip(exported, "S001", start="12/24s")
    plan = plan_roundtrip(tmp_project, exported)
    assert plan.get("kind") == "fcpxml"


# ------------------------------------------------------------------------ (c)


def test_a_trimmed_in_point_is_detected(
    tmp_project: Project, exported: Path
) -> None:
    _retime_spine_clip(exported, "S001", start="12/24s")  # 0s -> 500ms
    trims = [r for r in _rows(tmp_project, exported) if r.get("class") == "trim"]
    assert len(trims) == 1, trims
    ev = trims[0]["evidence"]
    assert ev["shot"] == "S001"
    assert ev["from"]["in_ms"] == 0
    assert ev["to"]["in_ms"] == 500


def test_the_trim_row_carries_the_take_it_will_apply_to(
    tmp_project: Project, exported: Path
) -> None:
    """FCPXML cannot stamp a take, so identity comes from the asset path."""
    _retime_spine_clip(exported, "S001", start="12/24s")
    trim = next(r for r in _rows(tmp_project, exported) if r["class"] == "trim")
    assert trim["evidence"]["take"] == "take_01"
    assert trim["target"] == "media/gen/S001/take_01"


def test_only_the_edited_clip_is_reported(
    tmp_project: Project, exported: Path
) -> None:
    """The untouched second shot must not ride along as a phantom trim."""
    _retime_spine_clip(exported, "S001", start="12/24s")
    trims = [r for r in _rows(tmp_project, exported) if r.get("class") == "trim"]
    assert [r["evidence"]["shot"] for r in trims] == ["S001"]


# ------------------------------------------------------------------------ (d)


def test_a_reordered_spine_is_detected(
    tmp_project: Project, exported: Path
) -> None:
    _swap_spine_clips(exported)
    assert "reorder" in _classes(_rows(tmp_project, exported))


# ------------------------------------------------------------------------ (e)


def test_a_1001_24000_project_round_trips_without_drift(
    tmp_project: Project
) -> None:
    """The NTSC rate is where a float would quietly invent a change."""
    out = export_fcpxml(tmp_project, _fabricate(tmp_project, ntsc=True))
    assert _classes(_rows(tmp_project, out)) <= {"no_changes"}


def test_the_ntsc_baseline_stores_time_as_an_exact_rational(
    tmp_project: Project
) -> None:
    """The stored baseline keeps the FRACTION, not a decimal of it.

    48 frames at 24000/1001 is exactly 1001/500 s (2.002 s). A pipeline that
    went through a float could not hand back that string, and the clean-plan
    test above would not notice: both sides of that diff run the same
    conversion, so they drift together and still compare equal.
    """
    out = export_fcpxml(tmp_project, _fabricate(tmp_project, ntsc=True))
    baseline = json.loads(
        _baseline(tmp_project, out).read_text(encoding="utf-8"))
    clip = baseline["document"]["clips"][0]
    assert clip["duration_exact"] == "1001/500"
    assert clip["duration_ms"] == 2002


# ------------------------------------------------------------------- apply
# Planning a change nobody can apply is a dead end, and apply is where the take
# identity recovered from `src` is actually spent: `set_inout` reads
# `evidence.take` and skips the row without it.
#
# These need REAL media — a virtual trim still probes the source — so they are
# gated like every other ffmpeg test here (test_round_t, the sibling suite for
# `set_inout`, uses the same lavfi + skipif idiom).


@ffmpeg_only
def test_an_fcpxml_trim_applies_to_truth(
    tmp_project: Project, add_shot
) -> None:
    """The full loop: export, trim in the NLE, plan, apply, truth moved."""
    for sid in SHOTS:
        add_shot(tmp_project, sid)
    exported = export_fcpxml(tmp_project, _fabricate(tmp_project, real=True))
    # Ripple-trim the head: in 0 -> 500ms, length 2000 -> 1500ms (out 2000ms).
    _retime_spine_clip(exported, "S001", start="12/24s", duration="36/24s")

    plan = plan_roundtrip(tmp_project, exported)
    result = apply_roundtrip(tmp_project, plan, rows=[0])

    assert result["applied"], result
    # A virtual trim registers a NEW take rather than mutating the old one —
    # append-only lineage, so the original survives beside it.
    takes = [t.name for t in tmp_project.takes("S001")]
    assert len(takes) == 2, takes
    selected = tmp_project.load_shot("S001").status.selected_take
    assert selected in takes and selected != "take_01"


@ffmpeg_only
def test_the_applied_take_carries_the_edited_in_point(
    tmp_project: Project, add_shot
) -> None:
    """500ms in the NLE becomes a 500ms source window in the new take."""
    for sid in SHOTS:
        add_shot(tmp_project, sid)
    exported = export_fcpxml(tmp_project, _fabricate(tmp_project, real=True))
    _retime_spine_clip(exported, "S001", start="12/24s", duration="36/24s")

    plan = plan_roundtrip(tmp_project, exported)
    apply_roundtrip(tmp_project, plan, rows=[0])

    selected = tmp_project.load_shot("S001").status.selected_take
    info = next(t for t in tmp_project.takes("S001") if t.name == selected)
    assert info.sidecar.source_in_ms == 500


def test_a_1001_24000_trim_reports_exact_milliseconds(
    tmp_project: Project
) -> None:
    """A real trim on the NTSC grid still reports the honest in-point.

    12 frames at 24000/1001 is 12·1001/24000 s = 500.5 ms, which lands on 501
    after half-up rounding — not 500, and not a float's 500.4999999.
    """
    out = export_fcpxml(tmp_project, _fabricate(tmp_project, ntsc=True))
    _retime_spine_clip(out, "S001", start="12012/24000s")
    trim = next(r for r in _rows(tmp_project, out) if r["class"] == "trim")
    assert trim["evidence"]["to"]["in_ms"] == 501
