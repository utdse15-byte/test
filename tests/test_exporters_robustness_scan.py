"""Exporter robustness & correctness — regressions found by the hourly deep
scan of ``exporters/`` (adversarially reproduced before fixing).

Seven genuine defects, each pinned red-first here:

1. ``edl_import.parse_edl`` crashed on a Unicode digit-like leading token
   (``str.isdigit()`` admits ``²`` that ``int()`` rejects) — the whole document
   was lost instead of the one row counted in ``unknown_rows``.
2. ``srt_ass.compile_vtt`` emitted WebVTT cue text WITHOUT escaping ``<``/``&``
   — a bare ``<`` opens a cue-span tag and eats the rest of the line (text loss).
3. ``fcpxml`` emitted ``<!-- … -->`` notes containing user names verbatim; a
   name with ``--`` (or a trailing ``-``) produced a not-well-formed document.
4. ``fcpxml_import.parse_fcpxml`` leaked a raw ``UnicodeDecodeError`` on a
   non-UTF-8 file instead of the documented structured ``FcpxmlImportError``.
5. ``otio._audio_clip`` emitted a ``source_range`` NOT contained by its
   ``available_range`` when the bed had a source in-point (the video path is
   careful to widen; the audio path was not).
6. ``jianying`` audio material ``duration`` was not widened to cover the source
   in-point, so a bed with a non-zero in-point read past the declared length
   (the exact inconsistency the video path was fixed for in Round-W #10).
7. ``jianying.lint_draft`` crashed on a hand-edited draft with a non-numeric
   timerange value or non-string path instead of returning a problem list.

These are behavioral pins (they call the functions and assert on outputs), never
source-text greps.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from manju.core.models import (
    AudioClip,
    CaptionLine,
    Timeline,
    TimelineTracks,
)


# --------------------------------------------------------------------------- #
# (1) edl_import: a Unicode digit-like row is counted, never a crash            #
# --------------------------------------------------------------------------- #


def test_parse_edl_unicode_digit_row_is_unknown_not_a_crash():
    from manju.exporters.edl_import import parse_edl

    # '²'.isdigit() is True but int('²') raises; the row must fall through to
    # unknown_rows while the valid event and the MANJU note both survive.
    text = (
        "TITLE: X\n"
        "FCM: NON-DROP FRAME\n"
        "²  REEL1 V C 00:00:00:00 00:00:01:00 01:00:00:00 01:00:01:00\n"
        "* MANJU: keep me\n"
        "001 REEL1 V C 00:00:00:00 00:00:01:00 01:00:00:00 01:00:01:00\n"
    )
    parsed = parse_edl(text)  # must not raise
    assert len(parsed.events) == 1
    assert len(parsed.manju_notes) == 1
    assert any("²" in u for u in parsed.unknown_rows)


def test_parse_edl_unicode_digit_transition_token_is_not_a_crash():
    from manju.exporters.edl_import import parse_edl

    # A superscript in the transition-frames slot (middle[0]) took the same
    # isdigit()->int() crash path.
    text = (
        "TITLE: X\nFCM: NON-DROP FRAME\n"
        "001 REEL1 V D ² 00:00:00:00 00:00:01:00 01:00:00:00 01:00:01:00\n"
    )
    parsed = parse_edl(text)  # must not raise (pre-fix: int('²') ValueError)
    # post-fix the row parses leniently — the un-decimal transition token is
    # simply not read as a frame count (transition_frames stays None); the
    # crucial guarantee is that the parse SURVIVES rather than aborting.
    assert len(parsed.events) == 1
    assert parsed.events[0].transition_frames is None


# --------------------------------------------------------------------------- #
# (2) compile_vtt escapes the WebVTT grammar characters                         #
# --------------------------------------------------------------------------- #


def _vtt(text: str) -> str:
    from manju.exporters.srt_ass import compile_vtt

    tl = Timeline(tracks=TimelineTracks(
        captions=[CaptionLine(start_ms=0, end_ms=1000, text=text)]))
    return compile_vtt(tl)


def test_compile_vtt_escapes_lt_and_amp():
    out = _vtt("价格 < 100 & 更多")
    assert "&lt;" in out and "&amp;" in out
    # the bare, line-eating '<' must be gone; CJK rides through untouched
    assert "价格" in out and "更多" in out
    assert "< 100" not in out


def test_compile_vtt_plain_cjk_has_no_new_entities():
    # a caption with neither '<' nor '&' is byte-identical to before (no escapes)
    out = _vtt("你好世界")
    assert "你好世界" in out
    assert "&" not in out


# --------------------------------------------------------------------------- #
# (3) fcpxml comment factory always emits well-formed XML                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("note", [
    "audio a--b clamped ",
    "shot name ends-",
    "a---b---c",
    " MANJU: transition at scene--2 ",
])
def test_fcpxml_comment_is_always_well_formed(note):
    from manju.exporters.fcpxml import _comment

    root = ET.Element("x")
    root.append(_comment(note))
    xml = ET.tostring(root)          # serialize
    ET.fromstring(xml)               # must re-parse — no ValueError/ParseError


def test_fcpxml_comment_without_dashes_is_unchanged():
    from manju.exporters.fcpxml import _comment

    plain = " MANJU: a clean informational note "
    # byte-identical to a bare ET.Comment for any note free of '--' / trailing '-'
    assert ET.tostring(_comment(plain)) == ET.tostring(ET.Comment(plain))


# --------------------------------------------------------------------------- #
# (4) fcpxml_import: non-UTF-8 file -> structured error, not a raw crash         #
# --------------------------------------------------------------------------- #


def test_parse_fcpxml_non_utf8_raises_structured_error(tmp_path):
    from manju.exporters.fcpxml_import import FcpxmlImportError, parse_fcpxml

    bad = tmp_path / "legacy.fcpxml"
    bad.write_bytes("<fcpxml>价</fcpxml>".encode("gb2312"))  # not UTF-8
    with pytest.raises(FcpxmlImportError):
        parse_fcpxml(bad)


# --------------------------------------------------------------------------- #
# (5) otio audio available_range CONTAINS the source_range                       #
# --------------------------------------------------------------------------- #


class _StubProject:
    """Minimal project: resolve stays inside a synthetic root (containment ok),
    and load_config returns an object with a name."""

    class _Cfg:
        name = "proj"

    def load_config(self) -> "_StubProject._Cfg":
        return self._Cfg()

    def resolve(self, source: str) -> Path:
        return Path("/proj") / source


def _range_end(tr: dict) -> float:
    return tr["start_time"]["value"] + tr["duration"]["value"]


@pytest.mark.parametrize("offset_ms", [0, 2000])
def test_otio_audio_available_range_contains_source_range(offset_ms):
    from manju.exporters.otio import _Times, _audio_clip

    clip = AudioClip(source="bgm.mp3", start_ms=0, duration_ms=5000,
                     start_offset_ms=offset_ms)
    d = _audio_clip(_StubProject(), clip, _Times(24.0, None), "music")
    src = d["source_range"]
    avail = d["media_reference"]["available_range"]
    # the media's available range must cover exactly up to the source_range end
    assert _range_end(avail) == _range_end(src)
    assert avail["start_time"]["value"] == 0
    if offset_ms == 0:
        # default in-point stays byte-stable: available == window
        assert avail["duration"]["value"] == src["duration"]["value"]


# --------------------------------------------------------------------------- #
# (6) jianying audio material duration covers in-point + window                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("offset_ms", [0, 2000])
def test_jianying_audio_material_duration_covers_source_window(offset_ms):
    from manju.exporters.jianying import _build_draft

    clip = AudioClip(source="bgm.mp3", start_ms=0, duration_ms=5000,
                     start_offset_ms=offset_ms)
    tl = Timeline(width=1920, height=1080,
                  tracks=TimelineTracks(music=[clip]))
    draft = _build_draft(_StubProject(), tl)

    mats = {m["id"]: m for m in draft["materials"]["audios"]}
    assert len(mats) == 1
    # the single music clip lands on ONE of the (multiple) audio tracks — find
    # the segment by material id rather than assuming which track holds it.
    segs = [s for t in draft["tracks"] if t.get("type") == "audio"
            for s in (t.get("segments") or [])]
    seg = next(s for s in segs if s.get("material_id") in mats)
    st = seg["source_timerange"]
    mat = mats[seg["material_id"]]
    # material length must reach at least the segment's source in-point + window
    assert mat["duration"] >= st["start"] + st["duration"]
    if offset_ms == 0:
        assert mat["duration"] == st["duration"]  # byte-stable default


# --------------------------------------------------------------------------- #
# (7) lint_draft never crashes on a malformed hand-edited draft                 #
# --------------------------------------------------------------------------- #


def test_lint_draft_survives_malformed_scalars(tmp_path):
    from manju.exporters.jianying import lint_draft

    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({
        "duration": "oops",                       # non-numeric
        "materials": {"videos": [{"id": "v", "path": 123}]},   # non-str path
        "tracks": [{
            "type": "video",
            "segments": [{
                "id": "s",
                "target_timerange": {"start": "x", "duration": ["nope"]},
            }],
        }],
    }), encoding="utf-8")

    problems = lint_draft(draft, _StubProject())  # must not raise
    assert isinstance(problems, list)
    assert any("missing 'path'" in p for p in problems)
