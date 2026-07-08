"""Editor-draft exporters must carry the WHOLE audible mix (round-N finding).

The audio policy (rules.audio) puts SFX, per-cut transition sounds and a looped
ambient bed onto ``timeline.tracks.sfx`` / ``timeline.tracks.ambient``; render.py
mixes them into final.mp4. The OTIO and JianYing-skeleton exporters used to walk
only voice + music, silently dropping the entire SFX/ambient layer for anyone who
kept editing in JianYing/CapCut or via OTIO. These tests pin that both tracks now
export, and that a project WITHOUT them still exports byte-identically (no
regression). The pyJianYingDraft/pycapcut native path is covered in
test_native_draft.py.
"""

from __future__ import annotations

import json

from manju.core.models import (
    AudioClip,
    Timeline,
    TimelineTracks,
    VideoClip,
)
from manju.exporters.jianying import export_jianying
from manju.exporters.otio import export_otio


def _timeline_with_sfx_ambient() -> Timeline:
    """Two shots, one voice clip, BGM, two SFX (an explicit hit + a transition
    whoosh on the interior cut) and a looped ambient bed under the whole film.
    Mirrors what timeline/compiler.py emits from a populated rules.audio."""
    return Timeline(
        duration_ms=5000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="take_01",
                          source="media/gen/S001/take_01.mp4", start_ms=0, duration_ms=2000),
                VideoClip(shot="S002", take="take_01",
                          source="media/gen/S002/take_01.mp4", start_ms=2000, duration_ms=3000),
            ],
            voice=[AudioClip(source="media/gen/S001/v.wav", start_ms=200,
                             duration_ms=1000, gain_db=-2.0)],
            music=[AudioClip(source="media/imports/bgm.wav", start_ms=0,
                             duration_ms=5000, gain_db=-18.0)],
            # compiler builds SFX with no duration_ms (source used as-is)
            sfx=[
                AudioClip(source="media/imports/hit.wav", start_ms=1500, gain_db=-6.0),
                AudioClip(source="media/imports/whoosh.wav", start_ms=2000, gain_db=-12.0),
            ],
            ambient=[AudioClip(source="media/imports/room.wav", start_ms=0,
                               duration_ms=5000, gain_db=-24.0, loop=True)],
        ),
    )


def _voice_music_only_timeline() -> Timeline:
    """The pre-round-N shape: only voice + music on the audio side."""
    return Timeline(
        duration_ms=5000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="take_01",
                          source="media/gen/S001/take_01.mp4", start_ms=0, duration_ms=2000),
                VideoClip(shot="S002", take="take_01",
                          source="media/gen/S002/take_01.mp4", start_ms=2000, duration_ms=3000),
            ],
            voice=[AudioClip(source="media/gen/S001/v.wav", start_ms=200,
                             duration_ms=1000, gain_db=-2.0)],
            music=[AudioClip(source="media/imports/bgm.wav", start_ms=0,
                             duration_ms=5000, gain_db=-18.0)],
        ),
    )


# --------------------------------------------------------------------- OTIO


def _otio_audio_children(doc: dict) -> list[dict]:
    audio_track = next(t for t in doc["tracks"]["children"] if t["kind"] == "Audio")
    return audio_track["children"]


def _by_track(children: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in children:
        out.setdefault(c["metadata"]["manju"]["track"], []).append(c)
    return out


def test_otio_exports_sfx_and_ambient(tmp_project):
    fps = float(tmp_project.load_config().fps)
    out = export_otio(tmp_project, _timeline_with_sfx_ambient())
    doc = json.loads(out.read_text(encoding="utf-8"))

    children = _otio_audio_children(doc)
    by_track = _by_track(children)
    # all four audio kinds are present on the single Audio track
    assert set(by_track) == {"voice", "music", "sfx", "ambient"}
    assert len(by_track["sfx"]) == 2       # explicit hit + transition whoosh
    assert len(by_track["ambient"]) == 1

    # SFX clip: name = source stem, source-as-is reference, real start in metadata
    hit = next(c for c in by_track["sfx"] if c["name"] == "hit")
    assert hit["media_reference"]["target_url"] == "media/imports/hit.wav"
    assert hit["metadata"]["manju"]["start_ms"] == 1500
    whoosh = next(c for c in by_track["sfx"] if c["name"] == "whoosh")
    assert whoosh["metadata"]["manju"]["start_ms"] == 2000

    # ambient bed: at t=0, spanning the film; loop noted in metadata (OTIO has no
    # loop primitive) and duration expressed in frames at the timeline fps
    amb = by_track["ambient"][0]
    assert amb["name"] == "room"
    assert amb["media_reference"]["target_url"] == "media/imports/room.wav"
    assert amb["metadata"]["manju"] == {"track": "ambient", "start_ms": 0, "loop": True}
    assert amb["source_range"]["duration"]["value"] == round(5000 * fps / 1000.0, 6)

    # voice/music are untouched: NO loop key leaks onto them
    for c in by_track["voice"] + by_track["music"]:
        assert "loop" not in c["metadata"]["manju"]


def test_otio_without_sfx_ambient_is_unchanged(tmp_project):
    """No regression: a voice+music-only timeline never grows sfx/ambient clips
    or a loop key, and the export is byte-stable across runs."""
    out = export_otio(tmp_project, _voice_music_only_timeline())
    raw = out.read_text(encoding="utf-8")
    doc = json.loads(raw)

    tracks = {c["metadata"]["manju"]["track"] for c in _otio_audio_children(doc)}
    assert tracks == {"voice", "music"}
    assert '"sfx"' not in raw and '"ambient"' not in raw and '"loop"' not in raw

    # deterministic re-export → byte-identical file
    assert export_otio(tmp_project, _voice_music_only_timeline()).read_text(encoding="utf-8") == raw


# ----------------------------------------------------------- JianYing skeleton


def _tracks_by_type(draft: dict) -> list[dict]:
    return draft["tracks"]


def test_jianying_skeleton_exports_sfx_and_ambient(tmp_project):
    draft_path = export_jianying(tmp_project, _timeline_with_sfx_ambient())
    draft = json.loads(draft_path.read_text(encoding="utf-8"))

    tracks = {t["id"]: t for t in draft["tracks"]}
    from manju.exporters.jianying import _uid

    sfx_track = tracks[_uid("track", "audio", "sfx")]
    amb_track = tracks[_uid("track", "audio", "ambient")]
    assert sfx_track["type"] == "audio" and amb_track["type"] == "audio"

    # each SFX becomes its own segment on the sfx track at its real µs start.
    # FIDELITY CAVEAT: compiler SFX carry no duration_ms, so the skeleton (which
    # never probes media) writes duration 0µs — the same convention _add_audio
    # uses for any duration-less clip; the segment still surfaces the source so
    # the SFX layer is no longer silently dropped on a JianYing/CapCut handoff.
    starts = sorted(s["target_timerange"]["start"] for s in sfx_track["segments"])
    assert starts == [1500 * 1000, 2000 * 1000]
    assert all(s["target_timerange"]["duration"] == 0 for s in sfx_track["segments"])

    # ambient bed references its source over the film's span (start 0, 5000ms),
    # mirroring how BGM is trimmed/represented — no loop semantics in a draft.
    amb_seg = amb_track["segments"][0]
    assert amb_seg["target_timerange"] == {"start": 0, "duration": 5000 * 1000}

    # materials for every new source are declared and point at real paths
    names = {m["material_name"] for m in draft["materials"]["audios"]}
    assert {"hit.wav", "whoosh.wav", "room.wav"} <= names


def test_jianying_skeleton_without_sfx_ambient_is_unchanged(tmp_project):
    """No regression: with neither track populated the draft keeps exactly the
    old video/voice/music/text track layout and stays byte-stable."""
    from manju.exporters.jianying import _uid

    draft_path = export_jianying(tmp_project, _voice_music_only_timeline())
    raw = draft_path.read_text(encoding="utf-8")
    draft = json.loads(raw)

    ids = [t["id"] for t in draft["tracks"]]
    assert _uid("track", "audio", "sfx") not in ids
    assert _uid("track", "audio", "ambient") not in ids
    # exactly the four pre-round-N tracks, text still last
    assert [t["type"] for t in draft["tracks"]] == ["video", "audio", "audio", "text"]

    # deterministic uuid5 ids → byte-identical re-export
    assert export_jianying(tmp_project, _voice_music_only_timeline()).read_text(encoding="utf-8") == raw


# --------------------------------------------------- round-W #10: source_in_ms


def _trimmed_video_timeline(source_in_ms: int) -> Timeline:
    """A single virtually-trimmed video clip — the internal render (media/
    render.py) seeks to ``source_in_ms``; every exporter must honour the same
    seek or the NLE opens a different picture than the one Manju rendered."""
    return Timeline(
        duration_ms=1000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="take_01",
                      source="media/gen/S001/take_01.mp4", start_ms=0,
                      duration_ms=1000, source_in_ms=source_in_ms),
        ]),
    )


def test_otio_source_range_honors_source_in_ms(tmp_project):
    fps = float(tmp_project.load_config().fps)
    out = export_otio(tmp_project, _trimmed_video_timeline(400))
    doc = json.loads(out.read_text(encoding="utf-8"))
    clip = doc["tracks"]["children"][0]["children"][0]
    assert clip["source_range"]["start_time"]["value"] == round(400 * fps / 1000.0, 6)
    assert clip["source_range"]["duration"]["value"] == round(1000 * fps / 1000.0, 6)
    # available_range widens to cover in-point + window (self-consistency, per
    # the module's own contract: "available_range covers the used source_range")
    avail = clip["media_reference"]["available_range"]["duration"]["value"]
    assert avail == round((400 + 1000) * fps / 1000.0, 6)


def test_otio_zero_source_in_ms_is_byte_identical(tmp_project):
    """No regression: source_in_ms=0 (the default, untrimmed) exports exactly
    as before this fix — source_range starts at 0, available_range == duration."""
    out = export_otio(tmp_project, _trimmed_video_timeline(0))
    doc = json.loads(out.read_text(encoding="utf-8"))
    clip = doc["tracks"]["children"][0]["children"][0]
    assert clip["source_range"]["start_time"]["value"] == 0.0
    avail = clip["media_reference"]["available_range"]["duration"]["value"]
    assert avail == clip["source_range"]["duration"]["value"]


def test_jianying_skeleton_source_timerange_honors_source_in_ms(tmp_project):
    draft_path = export_jianying(tmp_project, _trimmed_video_timeline(400))
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    video_track = next(t for t in draft["tracks"] if t["type"] == "video")
    seg = video_track["segments"][0]
    assert seg["source_timerange"] == {"start": 400_000, "duration": 1_000_000}  # µs
    # the material's own declared duration widens to cover in-point + window
    mat = draft["materials"]["videos"][0]
    assert mat["duration"] == 400_000 + 1_000_000


def test_jianying_skeleton_zero_source_in_ms_is_byte_identical(tmp_project):
    draft_path = export_jianying(tmp_project, _trimmed_video_timeline(0))
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    video_track = next(t for t in draft["tracks"] if t["type"] == "video")
    seg = video_track["segments"][0]
    assert seg["source_timerange"] == {"start": 0, "duration": 1_000_000}
    mat = draft["materials"]["videos"][0]
    assert mat["duration"] == 1_000_000
