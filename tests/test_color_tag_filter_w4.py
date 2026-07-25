"""`color.tag_outputs` must tag all FOUR axes on a current ffmpeg.

The option stamped bt709/tv via the `-color_primaries`/`-color_trc`/
`-colorspace`/`-color_range` OUTPUT options. ffmpeg stopped honouring two of
them for libx264 — measured on the same encode:

    7.0.2   space=bt709 range=tv trc=bt709   primaries=bt709
    7.1     space=bt709 range=tv trc=UNKNOWN primaries=UNKNOWN
    master  space=bt709 range=tv trc=UNKNOWN primaries=UNKNOWN

So an owner who opted in shipped a SILENTLY half-tagged master — the exact
defect the option exists to prevent, and a common delivery-rejection cause.
The tags now also ride a `setparams` filter node, which every tested build
honours (and which has existed since ffmpeg 4.3, so the pinned 6.1.1 has it).

Two things must hold beyond "it works": a project that never opted in must
keep a byte-identical filtergraph AND content key, and a project that DID opt
in must re-key — otherwise its old half-tagged final keeps matching its
sidecar and is never re-rendered.
"""

from __future__ import annotations

import pytest

from manju.core.models import ColorSpec
from manju.media.render import _color_tag_node, _COLOR_TAG_ARGS, _enc_params


# ------------------------------------------------------------- the graph node


def test_no_opt_in_adds_nothing_to_the_graph() -> None:
    """A not-opted-in project's rendered bytes must not move at all.

    `None` is the only way to spell "off": ColorSpec REFUSES an explicit
    `tag_outputs: false` on purpose — off means omitting the whole block — so
    there is exactly one off state to check."""
    assert _color_tag_node(None) == ""
    with pytest.raises(Exception):
        ColorSpec(tag_outputs=False)


def test_opting_in_stamps_all_four_axes_on_the_frames() -> None:
    node = _color_tag_node(ColorSpec(tag_outputs=True))
    assert node.startswith(",setparams=")
    for axis in ("color_primaries=bt709", "color_trc=bt709",
                 "colorspace=bt709", "range=tv"):
        assert axis in node, node


def test_the_two_axes_ffmpeg_dropped_are_the_ones_covered() -> None:
    """trc and primaries are precisely what >=7.1 stopped writing."""
    node = _color_tag_node(ColorSpec(tag_outputs=True))
    assert "color_trc=bt709" in node
    assert "color_primaries=bt709" in node


def test_the_output_options_are_kept_as_well() -> None:
    """Belt and braces: older builds honour these, and they cost nothing —
    neither ffmpeg generation should depend on the other's behaviour."""
    enc = _enc_params("final", ColorSpec(tag_outputs=True))
    for token in _COLOR_TAG_ARGS:
        assert token in enc


# ------------------------------------------------------------- the content key


def _key(project, timeline, target="final"):
    from manju.media.render import final_content_key

    return final_content_key(project, timeline, ass_file=None, target=target)


@pytest.fixture
def project_with_timeline(tmp_project, add_shot):
    from manju.core.models import (
        Timeline, TimelineMeta, TimelineTracks, VideoClip,
    )

    add_shot(tmp_project, "S001")
    src = tmp_project.root / "media" / "gen" / "S001" / "take_01.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fakevideo")
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920, duration_ms=1000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="take_01",
                      source="media/gen/S001/take_01.mp4",
                      start_ms=0, duration_ms=1000)]))
    return tmp_project, tl


def _set_tag_outputs(project, on: bool) -> None:
    from manju.core.yamlio import read_yaml, write_yaml

    cfg_path = project.root / "project.yaml"
    cfg = read_yaml(cfg_path)
    if on:
        cfg["color"] = {"tag_outputs": True}
    else:
        cfg.pop("color", None)
    write_yaml(cfg_path, cfg)


def test_a_project_that_never_opted_in_keeps_its_key(project_with_timeline) -> None:
    """The whole point of folding the node in ONLY when active: nobody who
    never touched this option may be forced into a re-render."""
    project, tl = project_with_timeline
    _set_tag_outputs(project, False)
    before = _key(project, tl)
    _set_tag_outputs(project, False)
    assert _key(project, tl) == before


def test_opting_in_re_keys_so_a_half_tagged_final_cannot_survive(
        project_with_timeline) -> None:
    """Bytes changed, so the key must change — otherwise the stale sidecar
    keeps vouching for a master missing two of its four tags."""
    project, tl = project_with_timeline
    _set_tag_outputs(project, False)
    off = _key(project, tl)
    _set_tag_outputs(project, True)
    on = _key(project, tl)
    assert on != off


def test_turning_it_back_off_returns_the_original_key(
        project_with_timeline) -> None:
    """The documented round-trip guarantee still holds after the fix."""
    project, tl = project_with_timeline
    _set_tag_outputs(project, False)
    off = _key(project, tl)
    _set_tag_outputs(project, True)
    _set_tag_outputs(project, False)
    assert _key(project, tl) == off
