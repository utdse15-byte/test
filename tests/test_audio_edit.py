"""Round T — the AUDIO-EDIT ENGINE: finish a normal video's audio WITHOUT
opening JianYing. Three capabilities plus a GUI-facing mixer API:

  1. BGM (+ ambient) in-point (``start_offset_ms``) and fade-in (``fade_in_ms``)
     — carried rules → clip → filtergraph, byte-identical when at defaults.
  2. Per-shot footage-audio control (``ShotSpec.source_audio`` gain/mute) folded
     into the SEGMENT normalize cache key, so exactly one segment re-encodes.
  3. ``build/mixer.py`` read/write API the GUI mixer drives.
  4. Exporters (JianYing skeleton / OTIO) carry the new fields, defaults stable.

Structure mirrors test_audio_policy: (a) pure compiler/model/key/filtergraph
assertions with NO ffmpeg, then (b) real ffmpeg end-to-end proofs.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.hashing import cache_key, hash_file, short_hash
from manju.core.models import (
    AmbientRules,
    AudioClip,
    AudioMixRules,
    Dialogue,
    MusicRules,
    ProjectConfig,
    ShotSpec,
    SourceAudio,
    Timeline,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.spec import compute_spec_hash
from manju.timeline.compiler import CompileInput, ShotInput, compile_timeline

WAV_HEADER = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
              b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")


def _shot_input(shot_id="S001", *, duration=2.0, text="", source_audio=None,
                voice_source=None, voice_duration_ms=None) -> ShotInput:
    shot = ShotSpec(id=shot_id, duration=duration,
                    dialogue=Dialogue(speaker="linxia", text=text))
    if source_audio is not None:
        shot.source_audio = source_audio
    return ShotInput(
        shot=shot, take_name="take_01",
        take_source=f"gen/{shot_id}/take_01.mp4", take_duration_ms=4000,
        voice_source=voice_source, voice_duration_ms=voice_duration_ms,
    )


def _input(*shots, rules=None) -> CompileInput:
    return CompileInput(config=ProjectConfig(name="t"),
                        rules=rules or TimelineRules(), shots=list(shots))


# ============================================================ (a) defaults pin


def test_new_fields_default_to_zero_and_off():
    """Every additive knob defaults to a no-op: 0 in-point/fade, 0dB/unmuted
    footage audio — the whole point of 'byte-identical at defaults'."""
    assert (MusicRules().start_offset_ms, MusicRules().fade_in_ms) == (0, 0)
    assert (AmbientRules().start_offset_ms, AmbientRules().fade_in_ms) == (0, 0)
    assert (AudioClip(source="x", start_ms=0).start_offset_ms,
            AudioClip(source="x", start_ms=0).fade_in_ms) == (0, 0)
    v = VideoClip(shot="S", take="t", source="s.mp4", start_ms=0, duration_ms=1000)
    assert (v.source_gain_db, v.source_mute) == (0.0, False)
    assert (SourceAudio().gain_db, SourceAudio().mute) == (0.0, False)


def test_default_compile_is_deterministic_with_new_machinery():
    inp = _input(_shot_input("S001", text="一"), _shot_input("S002", text="二"))
    a = compile_timeline(inp)
    assert compile_timeline(inp).model_dump_json() == a.model_dump_json()
    # default VideoClips carry the source-audio defaults
    assert all(c.source_gain_db == 0.0 and c.source_mute is False for c in a.tracks.video)


def test_default_source_audio_does_not_change_the_fingerprint():
    """A shot with EXPLICIT default source_audio fingerprints identically to one
    that never set it — the fingerprint omits the key unless it is non-default."""
    plain = _input(_shot_input("S001"))
    explicit = _input(_shot_input("S001", source_audio=SourceAudio()))
    assert plain.fingerprint() == explicit.fingerprint()


def test_source_audio_change_refingerprints():
    base = _input(_shot_input("S001"))
    louder = _input(_shot_input("S001", source_audio=SourceAudio(gain_db=-6.0)))
    muted = _input(_shot_input("S001", source_audio=SourceAudio(mute=True)))
    assert base.fingerprint() != louder.fingerprint()
    assert base.fingerprint() != muted.fingerprint()
    assert louder.fingerprint() != muted.fingerprint()


def test_source_audio_is_not_in_spec_payload():
    """Footage own-audio never restages the PICTURE: the video spec_hash is
    invariant to source_audio (it lives outside spec_payload)."""
    plain = ShotSpec(id="S001", dialogue=Dialogue(speaker="linxia", text="x"))
    loud = plain.model_copy(update={"source_audio": SourceAudio(gain_db=6.0, mute=True)})
    assert compute_spec_hash(plain) == compute_spec_hash(loud)


def test_bgm_offset_fade_change_refingerprints():
    """BGM/ambient in-point + fade-in live in the hashed rules dump."""
    base = _input(_shot_input("S001"),
                  rules=TimelineRules(music=MusicRules(source="media/imports/bgm.wav")))
    moved = _input(_shot_input("S001"),
                   rules=TimelineRules(music=MusicRules(source="media/imports/bgm.wav",
                                                        start_offset_ms=4000, fade_in_ms=800)))
    assert base.fingerprint() != moved.fingerprint()


# ------------------------------------------------- (a) compiler carriage


def test_music_carries_in_point_and_fade_in():
    rules = TimelineRules(music=MusicRules(source="media/imports/bgm.wav",
                                           start_offset_ms=4000, fade_in_ms=800))
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0), rules=rules))
    m = tl.tracks.music[0]
    assert m.start_offset_ms == 4000 and m.fade_in_ms == 800


def test_ambient_carries_in_point_and_fade_in():
    rules = TimelineRules(audio=AudioMixRules(
        ambient=AmbientRules(source="media/imports/room.wav",
                             start_offset_ms=1500, fade_in_ms=600)))
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0), rules=rules))
    a = tl.tracks.ambient[0]
    assert a.start_offset_ms == 1500 and a.fade_in_ms == 600


def test_per_shot_source_audio_carried_onto_video_clip():
    tl = compile_timeline(_input(
        _shot_input("S001", source_audio=SourceAudio(gain_db=-4.0)),
        _shot_input("S002", source_audio=SourceAudio(mute=True)),
        _shot_input("S003"),
    ))
    v = tl.tracks.video
    assert (v[0].source_gain_db, v[0].source_mute) == (-4.0, False)
    assert (v[1].source_gain_db, v[1].source_mute) == (0.0, True)
    assert (v[2].source_gain_db, v[2].source_mute) == (0.0, False)


# ------------------------------------------------- (a) segment cache key pin


def _fake_project(tmp_path: Path):
    from manju.core.container import Project

    root = Project.create(tmp_path / "样片", git_init=False).root
    return Project(root)


def _seg_key(project, clip, target="proxy", fade_in_ms=0, fade_out_ms=0):
    from manju.media.render import _segment_cache_key

    return _segment_cache_key(project, clip, width=1080, height=1920, fps=24,
                              target=target, fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms)


def test_segment_key_byte_identical_at_default_source_audio(tmp_project):
    """PIN: a clip at source-audio defaults hashes to EXACTLY the historical
    8-part key — its cached segment is reused, nothing re-encodes on upgrade."""
    src = tmp_project.gen_dir / "S001" / "take_01.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fakevideo-0")
    clip = VideoClip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                     start_ms=0, duration_ms=2000)
    historical = cache_key(hash_file(src), 1080, 1920, 24, 2000, "proxy", 0, 0)
    assert _seg_key(tmp_project, clip) == historical


def test_segment_key_changes_only_for_non_default_source_audio(tmp_project):
    src = tmp_project.gen_dir / "S001" / "take_01.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fakevideo-0")

    def clip(**kw):
        return VideoClip(shot="S001", take="take_01",
                         source="media/gen/S001/take_01.mp4",
                         start_ms=0, duration_ms=2000, **kw)

    base = _seg_key(tmp_project, clip())
    gained = _seg_key(tmp_project, clip(source_gain_db=-6.0))
    muted = _seg_key(tmp_project, clip(source_mute=True))
    assert base != gained != muted and base != muted
    # explicit-default == default (folded in only when non-default)
    assert _seg_key(tmp_project, clip(source_gain_db=0.0, source_mute=False)) == base


def test_final_content_key_byte_stable_at_default_source_audio(tmp_project):
    """PIN: the final content key is INSENSITIVE to default source-audio (carried
    only through the segment keys, and excluded from the timeline dump) and
    SENSITIVE when a shot's footage audio actually changes."""
    from manju.media.render import final_content_key

    src = tmp_project.gen_dir / "S001" / "take_01.mp4"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fakevideo-0")

    def tl(**kw):
        return Timeline(duration_ms=2000, tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                      start_ms=0, duration_ms=2000, **kw)]))

    base = final_content_key(tmp_project, tl(), ass_file=None, target="proxy")
    explicit_default = final_content_key(
        tmp_project, tl(source_gain_db=0.0, source_mute=False), ass_file=None, target="proxy")
    changed = final_content_key(tmp_project, tl(source_gain_db=-6.0), ass_file=None, target="proxy")
    muted = final_content_key(tmp_project, tl(source_mute=True), ass_file=None, target="proxy")
    assert base == explicit_default        # defaults never perturb the key
    assert base != changed and base != muted


# ------------------------------------------------- (a) filtergraph string pins

_HISTORICAL_MUSIC_PRE = (
    "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
    "volume=-18.0dB,apad,atrim=0:2.000[mus0pre]"
)


def _music_timeline(**music_kw) -> Timeline:
    return Timeline(duration_ms=2000, tracks=TimelineTracks(
        music=[AudioClip(source="media/imports/bgm.wav", start_ms=0, duration_ms=2000,
                         gain_db=-18.0, **music_kw)]))


def test_default_bgm_filtergraph_is_byte_identical(tmp_project):
    """No in-point / no fade-in → the music chain is byte-for-byte the pre-round
    string: no atrim=start, no asetpts, no afade=t=in leaked in."""
    from manju.media.render import _build_audio_graph

    _inp, stmts, _out = _build_audio_graph(_music_timeline(), tmp_project,
                                           target="proxy", total_s=2.0)
    joined = ";".join(stmts)
    assert _HISTORICAL_MUSIC_PRE in joined
    assert "atrim=start" not in joined
    assert "asetpts" not in joined
    assert "afade=t=in" not in joined


def test_bgm_in_point_and_fade_in_render_in_the_filtergraph(tmp_project):
    from manju.media.render import _build_audio_graph

    tl = _music_timeline(start_offset_ms=2000, fade_in_ms=750)
    _inp, stmts, _out = _build_audio_graph(tl, tmp_project, target="proxy", total_s=2.0)
    joined = ";".join(stmts)
    assert "atrim=start=2.000" in joined          # seek 2s into the source
    assert "asetpts=PTS-STARTPTS" in joined        # rebase timestamps to 0
    assert "afade=t=in:st=0:d=0.750" in joined     # ramp up from silence


def test_ambient_in_point_and_fade_in_render_in_the_filtergraph(tmp_project):
    from manju.media.render import _build_audio_graph

    tl = Timeline(duration_ms=2000, tracks=TimelineTracks(
        ambient=[AudioClip(source="media/imports/room.wav", start_ms=0, duration_ms=2000,
                           gain_db=-24.0, loop=True, start_offset_ms=1000, fade_in_ms=500)]))
    inputs, stmts, _out = _build_audio_graph(tl, tmp_project, target="proxy", total_s=2.0)
    joined = ";".join(stmts)
    assert "atrim=start=1.000" in joined and "asetpts=PTS-STARTPTS" in joined
    assert "afade=t=in:st=0:d=0.500" in joined
    assert "-stream_loop" in inputs   # still loops


# ============================================================ (a) mixer API


def test_read_mixer_shape(tmp_project, add_shot):
    from manju.build.mixer import read_mixer

    add_shot(tmp_project, "S001")
    rules = tmp_project.load_rules()
    rules.music = MusicRules(source="media/imports/bgm.wav", gain_db=-16.0,
                             start_offset_ms=3000, fade_in_ms=500)
    rules.audio = AudioMixRules(voice_gain_db=-2.0)
    tmp_project.save_rules(rules)

    mix = read_mixer(tmp_project)
    assert mix["voice_gain_db"] == -2.0
    assert mix["music"]["source"] == "media/imports/bgm.wav"
    assert mix["music"]["gain_db"] == -16.0
    assert mix["music"]["start_offset_ms"] == 3000
    assert mix["music"]["fade_in_ms"] == 500
    assert set(mix["music"]["duck"]) == {"threshold", "ratio", "attack_ms", "release_ms"}
    assert mix["ambient"]["source"] is None
    assert mix["sfx"] == []
    assert mix["transition"] == {"source": None, "gain_db": -12.0}
    assert mix["shots"] == [{"shot": "S001", "gain_db": 0.0, "mute": False}]


def test_apply_mixer_round_trip_writes_and_reads_back(tmp_project, add_shot):
    from manju.build.mixer import apply_mixer, read_mixer
    from manju.core.models import SfxClipSpec

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")

    changes = {
        "voice_gain_db": -3.0,
        "music": {"source": "media/imports/bgm.wav", "gain_db": -14.0,
                  "ducking": True, "start_offset_ms": 2500, "fade_in_ms": 600,
                  "fade_out_ms": 900, "duck": {"ratio": 6.0, "release_ms": 300}},
        "ambient": {"source": "media/imports/room.wav", "gain_db": -22.0},
        "sfx": [{"source": "media/imports/hit.wav", "at": "shot:S001", "gain_db": -5.0}],
        "transition": {"source": "media/imports/whoosh.wav", "gain_db": -9.0},
        "shots": [{"shot": "S001", "gain_db": -6.0}, {"shot": "S002", "mute": True}],
    }
    apply_mixer(tmp_project, changes)

    mix = read_mixer(tmp_project)
    assert mix["voice_gain_db"] == -3.0
    assert mix["music"]["start_offset_ms"] == 2500 and mix["music"]["fade_in_ms"] == 600
    assert mix["music"]["fade_out_ms"] == 900 and mix["music"]["ducking"] is True
    assert mix["music"]["duck"]["ratio"] == 6.0 and mix["music"]["duck"]["release_ms"] == 300
    assert mix["ambient"]["source"] == "media/imports/room.wav"
    assert mix["sfx"] == [{"source": "media/imports/hit.wav", "at": "shot:S001",
                           "offset_ms": 0, "gain_db": -5.0}]
    assert mix["transition"] == {"source": "media/imports/whoosh.wav", "gain_db": -9.0}
    assert {"shot": "S001", "gain_db": -6.0, "mute": False} in mix["shots"]
    assert {"shot": "S002", "gain_db": 0.0, "mute": True} in mix["shots"]

    # the write reached the real truth files (not just an in-memory view)
    assert tmp_project.load_rules().music.start_offset_ms == 2500
    assert tmp_project.load_shot("S002").source_audio.mute is True


def test_apply_mixer_records_exactly_one_event(tmp_project, add_shot):
    from manju.build.mixer import apply_mixer
    from manju.core.events import tail_events

    add_shot(tmp_project, "S001")
    before = len(tail_events(tmp_project.root, n=1000))
    apply_mixer(tmp_project, {"voice_gain_db": -1.0,
                              "shots": [{"shot": "S001", "gain_db": -2.0}]})
    after = tail_events(tmp_project.root, n=1000)
    mixer_events = [e for e in after if e["action"] == "mixer"]
    assert len(after) == before + 1               # exactly ONE event appended
    assert len(mixer_events) == 1
    assert set(mixer_events[0]["detail"]["changed"]) == {"voice_gain_db", "shots"}
    assert mixer_events[0]["detail"]["shots"] == ["S001"]


def test_apply_mixer_rejects_invalid_and_writes_nothing(tmp_project, add_shot):
    from manju.build.mixer import MixerError, apply_mixer
    from manju.core.events import tail_events

    add_shot(tmp_project, "S001")
    before_rules = tmp_project.rules_path.read_text(encoding="utf-8")
    before_events = len(tail_events(tmp_project.root, n=1000))

    # a non-numeric gain cannot validate as MusicRules.gain_db (float)
    with pytest.raises(MixerError):
        apply_mixer(tmp_project, {"music": {"gain_db": "loud"}})
    # an unknown shot id is rejected before any write
    with pytest.raises(MixerError):
        apply_mixer(tmp_project, {"voice_gain_db": -3.0,
                                  "shots": [{"shot": "S404", "gain_db": -2.0}]})

    # nothing was written: rules byte-identical, no event, S001 untouched
    assert tmp_project.rules_path.read_text(encoding="utf-8") == before_rules
    assert len(tail_events(tmp_project.root, n=1000)) == before_events
    assert tmp_project.load_shot("S001").source_audio == SourceAudio()


def test_apply_mixer_rebuild_verdicts(tmp_project, add_shot):
    """A pure MIX change re-renders the final but restales NO segment; a per-shot
    footage-audio change restales exactly that shot's segment."""
    from manju.build.mixer import apply_mixer

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")

    mix_only = apply_mixer(tmp_project, {"music": {"source": "media/imports/bgm.wav"}})
    assert mix_only["rebuild"]["final"] == "re-render"
    assert mix_only["rebuild"]["segments_restale"] == []   # mix lives in the final pass

    shot_edit = apply_mixer(tmp_project, {"shots": [{"shot": "S001", "gain_db": -6.0}]})
    assert shot_edit["rebuild"]["segments_restale"] == ["S001"]
    assert "recompile" in shot_edit["rebuild"]["timeline"]


# ============================================================ (a) exporters


def _export_timeline() -> Timeline:
    return Timeline(duration_ms=4000, tracks=TimelineTracks(
        video=[
            VideoClip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                      start_ms=0, duration_ms=2000, source_gain_db=-6.0),
            VideoClip(shot="S002", take="take_01", source="media/gen/S002/take_01.mp4",
                      start_ms=2000, duration_ms=2000, source_mute=True),
        ],
        music=[AudioClip(source="media/imports/bgm.wav", start_ms=0, duration_ms=4000,
                         gain_db=-18.0, start_offset_ms=3000, fade_in_ms=500)],
    ))


def test_jianying_export_carries_new_fields(tmp_project):
    from manju.exporters.jianying import export_jianying

    draft = json.loads(export_jianying(tmp_project, _export_timeline()).read_text("utf-8"))
    vsegs = {s["material_id"]: s for s in draft["tracks"][0]["segments"]}
    # footage source-audio -> per-segment volume / mute flag
    vals = list(vsegs.values())
    assert any("volume" in s and s.get("muted") is True for s in vals)   # S002 muted
    assert any(s.get("volume", 1.0) not in (None,) and "muted" not in s
               and abs(s["volume"] - 10 ** (-6.0 / 20)) < 1e-9 for s in vals)  # S001 -6dB
    # BGM in-point -> source_timerange start (µs) + a per-segment volume
    music_seg = next(s for t in draft["tracks"] if t["type"] == "audio"
                     for s in t["segments"] if s["source_timerange"]["start"] == 3000 * 1000)
    assert "volume" in music_seg


def test_otio_export_carries_new_fields(tmp_project):
    from manju.exporters.otio import export_otio

    doc = json.loads(export_otio(tmp_project, _export_timeline()).read_text("utf-8"))
    video = next(t for t in doc["tracks"]["children"] if t["kind"] == "Video")
    metas = [c["metadata"]["manju"] for c in video["children"]]
    assert any(m.get("source_gain_db") == -6.0 for m in metas)
    assert any(m.get("source_mute") is True for m in metas)
    audio = next(t for t in doc["tracks"]["children"] if t["kind"] == "Audio")
    music = next(c for c in audio["children"] if c["metadata"]["manju"]["track"] == "music")
    assert music["metadata"]["manju"]["start_offset_ms"] == 3000
    assert music["metadata"]["manju"]["fade_in_ms"] == 500
    # the in-point is also the OTIO source_range start (frames at fps)
    fps = float(tmp_project.load_config().fps)
    assert music["source_range"]["start_time"]["value"] == round(3000 * fps / 1000.0, 6)


# ============================================================ (b) ffmpeg e2e

ffmpeg_only = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)


def _mean_volume_db(path: Path, start_s: float, dur_s: float) -> float:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start_s:.3f}", "-t", f"{dur_s:.3f}",
         "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8",
    ).stderr
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?|-inf)\s*dB", out)
    if not m:
        raise AssertionError(f"no mean_volume in ffmpeg output:\n{out[-800:]}")
    return -math.inf if m.group(1) == "-inf" else float(m.group(1))


@ffmpeg_only
def test_bgm_in_point_differs_from_t0(tmp_path):
    """END-TO-END: a BGM whose loud content starts 1.5s in is SILENT under the
    film's opening at in-point 0, but LOUD at in-point 1500ms."""
    from manju.core.container import Project
    from manju.media.render import render_timeline

    project = Project.create(tmp_path / "样片", git_init=False)
    # silent 3s footage (no audio track -> normalize synthesizes silence)
    vid = project.imports_dir / "vid.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=3",
                    "-an", "-pix_fmt", "yuv420p", str(vid)], check=True)
    # BGM = a 3s sine muted for its first 1.5s (so its loud content starts 1.5s
    # into the source): in-point 0 opens on silence, in-point 1500ms on the sine.
    bgm = project.imports_dir / "bgm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3:sample_rate=48000",
         "-af", "volume='if(lt(t,1.5),0.0,1.0)':eval=frame,"
                "aformat=sample_rates=48000:channel_layouts=stereo",
         str(bgm)], check=True)

    def tl(offset_ms: int) -> Timeline:
        return Timeline(width=320, height=240, fps=24, duration_ms=3000,
                        tracks=TimelineTracks(
                            video=[VideoClip(shot="S001", take="take_01",
                                             source="media/imports/vid.mp4",
                                             start_ms=0, duration_ms=3000)],
                            music=[AudioClip(source="media/imports/bgm.wav", start_ms=0,
                                             duration_ms=3000, gain_db=0.0,
                                             start_offset_ms=offset_ms)]))

    a = render_timeline(project, tl(0), target="proxy",
                        out_path=tmp_path / "t0.mp4", force=True)
    b = render_timeline(project, tl(1500), target="proxy",
                        out_path=tmp_path / "late.mp4", force=True)
    vol_t0 = _mean_volume_db(a, 0.0, 0.4)     # opening: BGM still in its silent head
    vol_late = _mean_volume_db(b, 0.0, 0.4)   # opening: BGM already on the loud sine
    assert vol_late > vol_t0 + 20.0, (vol_t0, vol_late)


def _rms_db(path: Path) -> float:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "astats=metadata=1", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8",
    ).stderr
    vals = [(-math.inf if v == "-inf" else float(v))
            for v in re.findall(r"RMS level dB:\s*(-?\d+(?:\.\d+)?|-inf)", out)]
    assert vals, f"no RMS in ffmpeg output:\n{out[-800:]}"
    return max(vals)


@ffmpeg_only
def test_muted_shot_segment_is_silent_and_reencodes_exactly_one(tmp_path):
    """END-TO-END: (1) muting a shot's footage yields a SILENT segment (RMS at
    the noise floor); (2) changing ONE shot's source-audio re-encodes EXACTLY
    that shot's segment — the content-addressed cache leaves the rest untouched."""
    from manju.build.graph import run_build
    from manju.build.mixer import apply_mixer
    from manju.core.container import Project
    from manju.media.render import _fade_params, _segment_cache_key
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path / "样片", shots=2, with_bgm=False)
    project = Project(root)

    def seg_key(tl, shot):
        clips = list(tl.tracks.video)
        i = next(j for j, c in enumerate(clips) if c.shot == shot)
        fin, fout = _fade_params(clips, i)  # real transition fades fold into the key
        return _segment_cache_key(project, clips[i], width=tl.width, height=tl.height,
                                  fps=tl.fps, target="proxy", fade_in_ms=fin, fade_out_ms=fout)

    assert run_build(project, target="proxy").ok
    segs_before = {p: p.stat().st_mtime_ns for p in project.segments_dir.glob("*.mp4")}
    assert len(segs_before) == 2

    key_s2_before = seg_key(project.load_timeline(), "S002")

    # mute S001 + re-render
    apply_mixer(project, {"shots": [{"shot": "S001", "mute": True}]})
    assert run_build(project, target="proxy").ok

    segs_after = list(project.segments_dir.glob("*.mp4"))
    new_segs = [p for p in segs_after if p not in segs_before]
    assert len(new_segs) == 1, [p.name for p in segs_after]          # exactly one re-encode
    # S002's segment file is byte-untouched (same path, same mtime)
    s2_paths = [p for p in segs_after if p in segs_before]
    assert all(segs_before[p] == p.stat().st_mtime_ns for p in s2_paths)

    tl1 = project.load_timeline()
    assert next(c for c in tl1.tracks.video if c.shot == "S001").source_mute is True
    # S002's key never moved; S001's now folds in the mute → the one new file
    assert seg_key(tl1, "S002") == key_s2_before
    s1_key = seg_key(tl1, "S001")
    s1_seg = project.segments_dir / f"{short_hash(s1_key)}.mp4"
    assert s1_seg == new_segs[0]
    # the muted S001 segment is silent (aac noise floor: RMS well below the tone)
    assert _rms_db(s1_seg) <= -80.0
