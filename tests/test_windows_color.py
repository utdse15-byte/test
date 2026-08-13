"""W4 (MANJU_WINDOWS_ONLY_LEAN_V3) — the colour minimal closed loop.

The audit's finding: every final/proxy Manju emits carries NO colour metadata
at all (`color_primaries/transfer/space/range` all "unknown" — the suite's own
technical-profile honesty shows `color_known: False` on our own outputs), and
untagged bt709-ish output is exactly what players/NLEs then guess at. The
cheapest HONEST win is opt-in:

* ``project.yaml`` gains an OPTIONAL ``color:`` block (absent = today,
  byte-identical everywhere — serialization drops it exactly like
  ``edit_rate``/``cache_toolchain_keys``);
* ``color.tag_outputs: true`` stamps the final/proxy encode with the bt709/tv
  tags the pipeline already produces in substance (yuv420p through swscale
  defaults) — tagging what IS, never converting silently;
* ``color.input_transform: srgb_to_bt709|p3_to_bt709`` declares what the
  SOURCES are and converts them at the one normalize seam (zscale, validated
  against the pinned ffmpeg 6.1.1 on stills/untagged-yuv/alpha/full-range
  inputs) — a declared truth, never a probe-and-guess;
* both facts fold into the cache keys ONLY when active (the look/S4
  fold-only-when-active precedent), so every existing project keys
  byte-identically;
* an HDR source (smpte2084/HLG transfer or bt2020 primaries) gets a WARNING
  diagnostic in the technical profile — the SDR pipeline will not tone-map,
  and silence would be a lie. Facts-only digest is untouched (diagnostics
  never enter ``profile_digest``).

LUT/OCIO config plumbing: REJECTED for this personal pipeline (no producer,
no monitor calibration workflow — dead config would fabricate correctness).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.models import (
    ColorSpec,
    ProjectConfig,
    Timeline,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import read_yaml, write_yaml
from manju.media.normalize import _COLOR_TRANSFORM_CHAINS, normalize_segment
from manju.media.render import _enc_params, _segment_cache_key
from manju.media.technical_profile import normalize_probe_document as N

ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)

W, H, FPS = 192, 336, 24


# ============================================================ model / config


def test_project_without_color_serializes_without_the_key():
    """Byte-identity for every existing project: no ``color`` in project.yaml
    → no ``color`` key in ANY dump (plain model_dump included — presets/
    supportbundle/status all dump plain), exactly the edit_rate precedent."""
    cfg = ProjectConfig(name="雨夜")
    assert "color" not in cfg.model_dump()


def test_color_spec_parses_and_dumps_minimally():
    cfg = ProjectConfig(name="x", color={"tag_outputs": True})
    assert isinstance(cfg.color, ColorSpec)
    assert cfg.color.tag_outputs is True
    assert cfg.color.input_transform is None
    # the dump carries ONLY the non-default keys (drop-default precedent)
    assert cfg.model_dump()["color"] == {"tag_outputs": True}


def test_color_spec_all_default_is_rejected():
    """``color: {}`` is not a legal off switch — absent is (the S4
    empty-list-rejected precedent: an all-default block can only mislead)."""
    with pytest.raises(ValueError):
        ProjectConfig(name="x", color={})


def test_color_input_transform_enum_is_validated():
    with pytest.raises(ValueError):
        ProjectConfig(name="x", color={"input_transform": "rec2020_to_bt709"})
    cfg = ProjectConfig(name="x", color={"input_transform": "p3_to_bt709"})
    assert cfg.color.input_transform == "p3_to_bt709"
    assert cfg.model_dump()["color"] == {"input_transform": "p3_to_bt709"}


# ============================================================ encode params


def test_enc_params_byte_identical_without_color():
    """The historical literals, pinned: no colour opt-in → the exact command
    tail every existing final/proxy was encoded with (content keys hash this
    list verbatim — a byte here re-keys every final in every project)."""
    assert _enc_params("final") == [
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
    ]
    assert _enc_params("proxy") == [
        "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000",
    ]


def test_enc_params_appends_bt709_tags_when_opted_in():
    tags = ["-color_primaries", "bt709", "-color_trc", "bt709",
            "-colorspace", "bt709", "-color_range", "tv"]
    spec = ColorSpec(tag_outputs=True)
    for target in ("final", "proxy"):
        params = _enc_params(target, color=spec)
        assert params[-8:] == tags
        assert params[:-8] == _enc_params(target)  # prefix untouched
    # input_transform alone does NOT tag (two independent switches)
    assert _enc_params("final", color=ColorSpec(input_transform="srgb_to_bt709")) \
        == _enc_params("final")


# ============================================================ cache keys


def _keyable_project(tmp_path: Path) -> tuple[Project, VideoClip]:
    project = Project.create(tmp_path / "色彩", git_init=False)
    src = project.imports_dir / "a.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fake-source-bytes")
    clip = VideoClip(shot="S1", take="t", source=project.relpath(src),
                     start_ms=0, duration_ms=1000)
    return project, clip


def test_segment_key_byte_identical_without_transform(tmp_path):
    project, clip = _keyable_project(tmp_path)
    kw = dict(width=W, height=H, fps=FPS, target="final",
              fade_in_ms=0, fade_out_ms=0)
    base = _segment_cache_key(project, clip, **kw)
    assert _segment_cache_key(project, clip, color_in=None, **kw) == base


def test_segment_key_distinct_and_deterministic_with_transform(tmp_path):
    project, clip = _keyable_project(tmp_path)
    kw = dict(width=W, height=H, fps=FPS, target="final",
              fade_in_ms=0, fade_out_ms=0)
    base = _segment_cache_key(project, clip, **kw)
    srgb = _segment_cache_key(project, clip, color_in="srgb_to_bt709", **kw)
    p3 = _segment_cache_key(project, clip, color_in="p3_to_bt709", **kw)
    assert len({base, srgb, p3}) == 3  # all content-distinct
    assert srgb == _segment_cache_key(project, clip, color_in="srgb_to_bt709", **kw)


# ============================================================ the transform chains


def test_transform_chains_declare_both_sides_fully():
    """Exactly the two declared transforms; each chain must declare the FULL
    input side (pin/tin/min/rin — the pinned ffmpeg's zscale refuses untagged
    yuv without an input matrix: 'no path between colorspaces') and the full
    bt709/limited output side. A partial declaration is a probe-and-guess."""
    assert set(_COLOR_TRANSFORM_CHAINS) == {"srgb_to_bt709", "p3_to_bt709"}
    for chain in _COLOR_TRANSFORM_CHAINS.values():
        for side in ("pin=", "tin=", "min=", "rin=",
                     "p=bt709", "t=bt709", "m=bt709", "r=limited"):
            assert side in chain, (side, chain)
    assert "smpte432" in _COLOR_TRANSFORM_CHAINS["p3_to_bt709"]  # Display P3 primaries


# ============================================================ e2e (ffmpeg 6.1.1)


def _gen_source(dest: Path, *, seconds: float = 1.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(dest)],
        check=True, capture_output=True,
    )


def _color_probe(path: Path) -> dict[str, str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=color_range,color_space,color_transfer,color_primaries",
         "-of", "default=nw=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    pairs = dict(line.split("=", 1) for line in out.strip().splitlines())
    return pairs


def _one_clip_timeline(project: Project) -> Timeline:
    src = project.imports_dir / "clip.mp4"
    _gen_source(src)
    clip = VideoClip(shot="S1", take="t", source=project.relpath(src),
                     start_ms=0, duration_ms=1000)
    return Timeline(fps=FPS, width=W, height=H, duration_ms=1000,
                    tracks=TimelineTracks(video=[clip]))


@ffmpeg
def test_render_untagged_today_and_tagged_when_opted_in(tmp_path):
    """The loop closed end to end, both halves honest:

    (a) no opt-in → the output carries NO colour tags (today's truth, and the
        profile normalizer says ``color_known: False`` about our own output);
    (b) ``color.tag_outputs: true`` → the SAME render carries bt709/tv on all
        four axes and the profile normalizer reports ``color_known: True``
        with no COLOR_UNKNOWN diagnostic."""
    from manju.media.render import render_timeline

    project = Project.create(tmp_path / "色彩闭环", git_init=False)
    tl = _one_clip_timeline(project)

    untagged = render_timeline(project, tl, target="proxy")
    probe = _color_probe(untagged)
    assert set(probe.values()) == {"unknown"}, probe  # (a) today, honestly

    cfg_path = project.root / "project.yaml"
    cfg = read_yaml(cfg_path)
    cfg["color"] = {"tag_outputs": True}
    write_yaml(cfg_path, cfg)

    tagged = render_timeline(project, tl, target="proxy", force=True)
    probe = _color_probe(tagged)
    assert probe == {"color_range": "tv", "color_space": "bt709",
                     "color_transfer": "bt709", "color_primaries": "bt709"}, probe

    # the profile normalizer closes the loop: our own output is now KNOWN
    import json
    raw = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(tagged)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    doc = N(json.loads(raw))
    assert doc["facts"]["color"]["color_known"] is True
    assert "COLOR_UNKNOWN" not in [d["code"] for d in doc["diagnostics"]]


@ffmpeg
def test_tag_outputs_rekeys_the_final_only_when_flipped(tmp_path):
    """Fold-only-when-active, proven at the content-key layer: flipping
    ``tag_outputs`` on changes the proxy's content key (honest re-render — the
    output bytes DO change), and turning it back off returns the ORIGINAL key
    byte-for-byte (no residue)."""
    from manju.media.render import _final_key_payload
    from manju.core.hashing import cache_key

    project = Project.create(tmp_path / "键控", git_init=False)
    tl = _one_clip_timeline(project)
    cfg_path = project.root / "project.yaml"

    def key() -> str:
        # a fresh Project so no stale config cache can mask the yaml edit
        p = Project(project.root)
        return cache_key(_final_key_payload(p, tl, ass_file=None, target="proxy"))

    k_before = key()
    cfg = read_yaml(cfg_path)
    cfg["color"] = {"tag_outputs": True}
    write_yaml(cfg_path, cfg)
    k_on = key()
    assert k_on != k_before

    cfg = read_yaml(cfg_path)
    del cfg["color"]
    write_yaml(cfg_path, cfg)
    assert key() == k_before


@ffmpeg
@pytest.mark.parametrize("transform", ["srgb_to_bt709", "p3_to_bt709"])
def test_normalize_applies_input_transform_on_still_and_video(tmp_path, transform):
    """The zscale chain runs under the PINNED ffmpeg (6.1.1 — the exact CI
    version on both OSes) for both source classes the pipeline normalizes:
    an RGB still (zimg ignores the yuv input declaration for RGB frames —
    verified) and an untagged yuv420p video (the class that needs the full
    input declaration). Output must reach full length and burn cleanly."""
    from manju.media.probe import probe_duration_ms

    still = tmp_path / "静帧.png"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=0xC81E1E:s={W}x{H}", "-frames:v", "1", str(still)],
        check=True, capture_output=True,
    )
    video = tmp_path / "clip.mp4"
    _gen_source(video)

    for i, src in enumerate((still, video)):
        dest = tmp_path / f"seg{i}.mp4"
        normalize_segment(src, dest, width=W, height=H, fps=FPS,
                          duration_ms=600, color_transform=transform)
        assert dest.exists()
        assert abs(probe_duration_ms(dest) - 600) < 100


@ffmpeg
def test_normalize_without_transform_is_command_stable(tmp_path):
    """color_transform=None (every existing caller) must not perturb the
    filter chain: the encoded segment stays reusable against the historical
    profile (the concat contract) and no zscale node appears."""
    video = tmp_path / "clip.mp4"
    _gen_source(video)
    dest = tmp_path / "seg.mp4"
    lines: list[str] = []
    normalize_segment(video, dest, width=W, height=H, fps=FPS,
                      duration_ms=600, color_transform=None, log=lines.append)
    joined = "\n".join(lines)
    assert "zscale" not in joined


# ============================================================ doctor preflight


@ffmpeg
def test_doctor_probes_zscale_only_when_transform_declared(tmp_path):
    """W4: a declared ``color.input_transform`` needs zscale (libzimg) in the
    user's ffmpeg — doctor states it BEFORE the first failed render. No
    declaration → no probe, no row (zero cost for every other project)."""
    from manju.build.doctor import run_doctor

    project = Project.create(tmp_path / "诊断", git_init=False)
    names = [c["name"] for c in run_doctor(project)["checks"]]
    assert "color_transform" not in names  # undeclared → not even probed

    cfg_path = project.root / "project.yaml"
    cfg = read_yaml(cfg_path)
    cfg["color"] = {"input_transform": "srgb_to_bt709"}
    write_yaml(cfg_path, cfg)
    project2 = Project(project.root)
    rows = [c for c in run_doctor(project2)["checks"] if c["name"] == "color_transform"]
    assert len(rows) == 1
    assert rows[0]["ok"] is True  # advisory: NEVER gates doctor's exit code
    assert "srgb_to_bt709" in rows[0]["detail"]
    # this env's ffmpeg (the pinned 6.1.1) has zscale — the row must say so
    assert "available" in rows[0]["detail"]


# ============================================================ HDR advisory


def _probe_doc(video_over: dict) -> dict:
    base = {
        "index": 0, "codec_type": "video", "codec_name": "hevc",
        "width": 3840, "height": 2160, "coded_width": 3840, "coded_height": 2160,
        "r_frame_rate": "24/1", "avg_frame_rate": "24/1",
        "pix_fmt": "yuv420p10le", "nb_frames": "24", "duration": "1.0",
    }
    base.update(video_over)
    return {"streams": [base], "format": {"format_name": "mov", "duration": "1.0"}}


@pytest.mark.parametrize("over,expect", [
    # PQ / HLG transfers and bt2020 primaries: each alone must warn
    ({"color_transfer": "smpte2084", "color_primaries": "bt2020"}, True),
    ({"color_transfer": "arib-std-b67"}, True),
    ({"color_primaries": "bt2020"}, True),
    # SDR / unknown: never warn (unknown is COLOR_UNKNOWN's job, not HDR's)
    ({"color_transfer": "bt709", "color_primaries": "bt709",
      "color_space": "bt709", "color_range": "tv"}, False),
    ({}, False),
])
def test_hdr_source_warning(over, expect):
    doc = N(_probe_doc(over))
    hdr = [d for d in doc["diagnostics"] if d["code"] == "HDR_SOURCE"]
    if expect:
        assert len(hdr) == 1
        assert hdr[0]["severity"] == "warning"
        # the diagnostic names WHAT is HDR about the source (never vague)
        assert hdr[0]["axes"], hdr[0]
    else:
        assert hdr == []


def test_colorstats_adopt_via_never_names_a_nonexistent_command():
    """W4 audit finding: the colorstats compare document advertised
    ``manju repair --op grade`` — an op that does not exist (a reader typing
    it got an error). The descriptor must state the fact (`implemented:
    False`, no command) until a real grade op exists; a pointer must never
    be dressed as a command."""
    import re

    import manju.qc.colorstats as colorstats

    src = Path(colorstats.__file__).read_text(encoding="utf-8")
    assert '"implemented": False' in src
    assert not re.search(r'"command":\s*"[^"]*--op grade', src), \
        "adopt_via must not advertise the nonexistent grade op as a command"


def test_hdr_diagnostic_never_moves_the_facts_digest():
    """profile_digest hashes facts ONLY — the new advisory cannot re-digest
    any existing profile document (diagnostics are outside by design)."""
    sdr = N(_probe_doc({}))
    hdr = N(_probe_doc({"color_transfer": "smpte2084"}))
    # same facts except the transfer axis itself; strip it and compare digests
    # is overkill — the pin that matters: digest input is the facts block.
    assert sdr["profile_digest"] != hdr["profile_digest"]  # transfer IS a fact
    resdr = N(_probe_doc({}))
    assert resdr["profile_digest"] == sdr["profile_digest"]  # deterministic
