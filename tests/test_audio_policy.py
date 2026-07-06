"""Round N — the audio policy (rules.audio) wired through compile → render →
staleness → QC.

Two halves, like test_overlay.py:
  (a) the compiler is a pure function — no ffmpeg. The audio policy is INERT by
      default (zero behaviour change); when set it populates the sfx/ambient
      tracks and voice gain deterministically, anchors resolve against the
      compiled video track, unknown-shot SFX are dropped, and a policy change
      re-fingerprints the compile.
  (b) end-to-end (ffmpeg required) — a tiny project renders a final with the
      full mix; the second build is a content-key skip (idempotent); editing an
      SFX file's bytes re-renders. Plus filter-graph string assertions on
      _build_audio_graph (volume / adelay / -stream_loop / sidechaincompress).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.models import (
    AmbientRules,
    AudioClip,
    AudioMixRules,
    Dialogue,
    ProjectConfig,
    SfxClipSpec,
    ShotSpec,
    Timeline,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.timeline.compiler import CompileInput, ShotInput, compile_timeline

# a real (tiny) wav so path-existence checks pass without ffmpeg
WAV_HEADER = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
              b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")


# --------------------------------------------------------- (a) compiler-only


def _shot_input(shot_id="S001", *, duration=2.0, text="", take_source=None,
                voice_source=None, voice_duration_ms=None) -> ShotInput:
    return ShotInput(
        shot=ShotSpec(id=shot_id, duration=duration,
                      dialogue=Dialogue(speaker="linxia", text=text)),
        take_name="take_01",
        take_source=take_source or f"gen/{shot_id}/take_01.mp4",
        take_duration_ms=4000,
        voice_source=voice_source,
        voice_duration_ms=voice_duration_ms,
    )


def _input(*shots, audio=None, config=None) -> CompileInput:
    rules = TimelineRules(audio=audio) if audio is not None else TimelineRules()
    return CompileInput(
        config=config or ProjectConfig(name="t"),
        rules=rules,
        shots=list(shots),
    )


def test_default_audio_rules_are_inert():
    """Default AudioMixRules (empty sfx, no ambient/transition, gain 0) → the
    sfx/ambient tracks stay empty and voice gain is 0: zero behaviour change
    for every existing project (the compile is also byte-stable)."""
    inp = _input(
        _shot_input("S001", text="一", voice_source="gen/S001/v.wav", voice_duration_ms=1000),
        _shot_input("S002", text="二"),
    )
    tl = compile_timeline(inp)
    assert tl.tracks.sfx == []
    assert tl.tracks.ambient == []
    assert all(c.gain_db == 0.0 for c in tl.tracks.voice)
    assert tl.tracks.voice  # a voice clip WAS emitted (unchanged shape)
    # determinism holds with the audio machinery in the path
    assert compile_timeline(inp).model_dump_json() == tl.model_dump_json()


def test_voice_gain_applied():
    audio = AudioMixRules(voice_gain_db=-4.5)
    inp = _input(
        _shot_input("S001", text="x", voice_source="gen/S001/v.wav", voice_duration_ms=1000),
        audio=audio,
    )
    tl = compile_timeline(inp)
    assert tl.tracks.voice[0].gain_db == -4.5


def test_sfx_absolute_anchor():
    audio = AudioMixRules(sfx=[SfxClipSpec(source="media/imports/hit.wav", at="", offset_ms=750, gain_db=-6.0)])
    tl = compile_timeline(_input(_shot_input("S001"), _shot_input("S002"), audio=audio))
    assert len(tl.tracks.sfx) == 1
    clip = tl.tracks.sfx[0]
    assert clip.source == "media/imports/hit.wav"
    assert clip.start_ms == 750  # absolute offset from t=0
    assert clip.gain_db == -6.0


def test_sfx_shot_start_anchor():
    audio = AudioMixRules(sfx=[SfxClipSpec(source="s.wav", at="shot:S002")])
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0),
                                 _shot_input("S002", duration=3.0), audio=audio))
    # S002 starts where S001 ends
    assert tl.tracks.sfx[0].start_ms == tl.tracks.video[1].start_ms == 2000


def test_sfx_shot_end_anchor():
    audio = AudioMixRules(sfx=[SfxClipSpec(source="s.wav", at="shot:S001:end", offset_ms=100)])
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0),
                                 _shot_input("S002", duration=3.0), audio=audio))
    v0 = tl.tracks.video[0]
    assert tl.tracks.sfx[0].start_ms == v0.start_ms + v0.duration_ms + 100 == 2100


def test_sfx_unknown_shot_is_skipped_deterministically():
    audio = AudioMixRules(sfx=[
        SfxClipSpec(source="ok.wav", at="shot:S001"),
        SfxClipSpec(source="ghost.wav", at="shot:S999"),  # no such shot
    ])
    tl = compile_timeline(_input(_shot_input("S001"), _shot_input("S002"), audio=audio))
    sources = [c.source for c in tl.tracks.sfx]
    assert sources == ["ok.wav"]  # the unresolvable one is dropped


def test_transition_sound_count_is_clips_minus_one():
    audio = AudioMixRules(transition_sound="media/imports/whoosh.wav", transition_gain_db=-10.0)
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0),
                                 _shot_input("S002", duration=2.0),
                                 _shot_input("S003", duration=2.0), audio=audio))
    trans = [c for c in tl.tracks.sfx if c.source == "media/imports/whoosh.wav"]
    assert len(trans) == 2  # 3 clips → 2 interior boundaries
    assert [c.start_ms for c in trans] == [2000, 4000]  # on the interior cuts
    assert all(c.gain_db == -10.0 for c in trans)


def test_transition_sound_none_for_single_clip():
    audio = AudioMixRules(transition_sound="whoosh.wav")
    tl = compile_timeline(_input(_shot_input("S001"), audio=audio))
    assert tl.tracks.sfx == []  # one clip → no interior boundary


def test_ambient_clip_shape():
    audio = AudioMixRules(
        ambient=AmbientRules(source="media/imports/room.wav", gain_db=-24.0,
                             ducking=True, fade_out_ms=800)
    )
    tl = compile_timeline(_input(_shot_input("S001", duration=2.0),
                                 _shot_input("S002", duration=3.0), audio=audio))
    assert len(tl.tracks.ambient) == 1
    amb = tl.tracks.ambient[0]
    assert amb.source == "media/imports/room.wav"
    assert amb.start_ms == 0
    assert amb.duration_ms == tl.duration_ms == 5000  # spans the whole film
    assert amb.loop is True
    assert amb.gain_db == -24.0 and amb.ducking is True and amb.fade_out_ms == 800


def test_no_ambient_when_source_unset():
    tl = compile_timeline(_input(_shot_input("S001"), audio=AudioMixRules()))
    assert tl.tracks.ambient == []


# ----------------------------------------------------- staleness / fingerprint


def test_sfx_gain_change_refingerprints():
    shots = [_shot_input("S001"), _shot_input("S002")]
    config = ProjectConfig(name="t")

    def inp_with(gain: float) -> CompileInput:
        return CompileInput(
            config=config,
            rules=TimelineRules(audio=AudioMixRules(
                sfx=[SfxClipSpec(source="s.wav", at="shot:S001", gain_db=gain)])),
            shots=list(shots),
        )

    base = inp_with(-6.0)
    changed = inp_with(-3.0)
    # rules.audio participates in the fingerprint (via the rules dump)
    assert base.fingerprint() != changed.fingerprint()
    assert compile_timeline(base).meta.compiled_from != compile_timeline(changed).meta.compiled_from
    # unchanged rules → unchanged fingerprint (deterministic)
    assert base.fingerprint() == inp_with(-6.0).fingerprint()


# --------------------------------------------------------------- (b) QC


def _tiny_video_timeline() -> Timeline:
    return Timeline(
        duration_ms=4000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                      start_ms=0, duration_ms=2000),
            VideoClip(shot="S002", take="take_01", source="media/gen/S002/take_01.mp4",
                      start_ms=2000, duration_ms=2000),
        ]),
    )


def test_qc_missing_audio_sources(tmp_project, add_shot):
    from manju.qc.checks import run_qc

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    rules = tmp_project.load_rules()
    rules.audio = AudioMixRules(
        sfx=[SfxClipSpec(source="media/imports/nope_sfx.wav", at="shot:S001")],
        ambient=AmbientRules(source="media/imports/nope_amb.wav"),
        transition_sound="media/imports/nope_trans.wav",
    )
    tmp_project.save_rules(rules)

    report = run_qc(tmp_project, _tiny_video_timeline(), extract_frames=False)
    audio_errs = [i for i in report.items if i.level == "error" and "missing" in i.message
                  and any(k in i.message for k in ("sfx", "ambient", "transition sound"))]
    joined = " ".join(i.message for i in audio_errs)
    assert "sfx #0" in joined
    assert "ambient" in joined
    assert "transition sound" in joined
    assert all(i.suggestion for i in audio_errs)  # each carries a fix suggestion


def test_qc_unresolvable_sfx_anchor_warns(tmp_project, add_shot):
    from manju.qc.checks import run_qc

    add_shot(tmp_project, "S001")
    sfx = tmp_project.imports_dir / "hit.wav"
    sfx.parent.mkdir(parents=True, exist_ok=True)
    sfx.write_bytes(WAV_HEADER)  # file EXISTS, only the anchor is bad

    rules = tmp_project.load_rules()
    rules.audio = AudioMixRules(
        sfx=[SfxClipSpec(source="media/imports/hit.wav", at="shot:S999")])
    tmp_project.save_rules(rules)

    report = run_qc(tmp_project, _tiny_video_timeline(), extract_frames=False)
    warns = [i for i in report.items
             if i.level == "warn" and "anchor" in i.message and "S999" in i.message]
    assert warns, [i.message for i in report.items]
    # names the anchor grammar in the suggestion
    assert "shot:<id>" in warns[0].suggestion
    # and the good-file case raises NO missing-file error for this source
    assert not [i for i in report.items if i.level == "error" and "hit.wav" in i.message]


# ------------------------------------------------- (b) filter-graph strings


def test_build_audio_graph_statements(tmp_project):
    """_build_audio_graph must place voice/sfx gain (volume), sfx delay
    (adelay), the looped ambient input (-stream_loop -1) and generalized
    ducking (one sidechaincompress per ducked music + ambient clip)."""
    from manju.media.render import _build_audio_graph

    timeline = Timeline(
        duration_ms=2000,
        tracks=TimelineTracks(
            voice=[AudioClip(source="media/gen/S001/v.wav", start_ms=200,
                             duration_ms=1000, gain_db=-3.0)],
            sfx=[AudioClip(source="media/imports/s.wav", start_ms=500, gain_db=-6.0)],
            music=[AudioClip(source="media/imports/m.wav", start_ms=0, duration_ms=2000,
                             gain_db=-18.0, ducking=True, fade_out_ms=1000)],
            ambient=[AudioClip(source="media/imports/a.wav", start_ms=0, duration_ms=2000,
                               gain_db=-24.0, ducking=True, fade_out_ms=800, loop=True)],
        ),
    )
    inputs, stmts, aout = _build_audio_graph(timeline, tmp_project, target="proxy", total_s=2.0)
    joined = ";".join(stmts)

    assert "volume=-3.0dB" in joined          # voice gain
    assert "volume=-6.0dB" in joined          # sfx gain
    assert "adelay=500:all=1" in joined       # sfx placed at its start
    assert "-stream_loop" in inputs and "-1" in inputs  # ambient loops to fill
    # ducking generalized over music + ambient: exactly two sidechain keys
    assert joined.count("sidechaincompress") == 2
    assert "asplit=3" in joined               # voice bus → mix + 2 duck keys
    assert "[sfx0]" in joined                  # sfx joins the final mix
    assert aout  # a real output label


def test_build_audio_graph_sfx_not_ducked_without_voice(tmp_project):
    """No voice → nothing to duck against: no sidechaincompress at all, and the
    sfx/ambient still render (they are not gated on ducking)."""
    from manju.media.render import _build_audio_graph

    timeline = Timeline(
        duration_ms=2000,
        tracks=TimelineTracks(
            sfx=[AudioClip(source="media/imports/s.wav", start_ms=0, gain_db=-6.0)],
            ambient=[AudioClip(source="media/imports/a.wav", start_ms=0, duration_ms=2000,
                               ducking=True, loop=True)],  # asks to duck, but no voice
        ),
    )
    _inputs, stmts, _aout = _build_audio_graph(timeline, tmp_project, target="proxy", total_s=2.0)
    joined = ";".join(stmts)
    assert "sidechaincompress" not in joined
    assert "[sfx0]" in joined and "[amb0pre]" in joined


def test_audio_input_hashes_cover_sfx_and_ambient(tmp_project):
    """The final content key must move when any sfx/ambient file's bytes change
    (FIX-A: _audio_input_hashes covers the sfx + ambient tracks)."""
    from manju.media.render import _audio_input_hashes

    sfx = tmp_project.imports_dir / "s.wav"
    amb = tmp_project.imports_dir / "a.wav"
    sfx.parent.mkdir(parents=True, exist_ok=True)
    sfx.write_bytes(WAV_HEADER)
    amb.write_bytes(WAV_HEADER + b"\x01")

    timeline = Timeline(tracks=TimelineTracks(
        sfx=[AudioClip(source="media/imports/s.wav", start_ms=0)],
        ambient=[AudioClip(source="media/imports/a.wav", start_ms=0, duration_ms=1000, loop=True)],
    ))
    before = _audio_input_hashes(tmp_project, timeline)
    assert len(before) == 2  # both tracks are hashed
    sfx.write_bytes(WAV_HEADER + b"\x02\x03")  # edit the sfx bytes
    after = _audio_input_hashes(tmp_project, timeline)
    assert before != after


# --------------------------------------------------- (b) real build (ffmpeg)


pytestmark_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)


def _sine_wav(dest: Path, freq: int, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", str(dest)],
        check=True,
    )


@pytestmark_ffmpeg
def test_real_build_with_audio_policy_is_idempotent_and_key_sensitive(tmp_path):
    """A tiny real build with the full audio mix: renders once, the second
    build is a content-key skip, and editing the SFX bytes re-renders."""
    from manju.build.graph import run_build
    from manju.core.container import Project
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path / "样片", shots=2)
    project = Project(root)

    sfx = project.imports_dir / "sfx.wav"
    amb = project.imports_dir / "ambient.wav"
    trans = project.imports_dir / "whoosh.wav"
    _sine_wav(sfx, 880, 0.3)
    _sine_wav(amb, 90, 1.0)   # short bed: proves it LOOPS to fill the film
    _sine_wav(trans, 1200, 0.2)

    rules = project.load_rules()
    rules.audio = AudioMixRules(
        voice_gain_db=-2.0,
        sfx=[SfxClipSpec(source="media/imports/sfx.wav", at="shot:S001:end", gain_db=-6.0)],
        ambient=AmbientRules(source="media/imports/ambient.wav", gain_db=-24.0,
                             ducking=True, fade_out_ms=500),
        transition_sound="media/imports/whoosh.wav",
        transition_gain_db=-12.0,
    )
    project.save_rules(rules)

    def finals() -> list[Path]:
        return sorted(project.final_dir.glob("final_v*.mp4"))

    r1 = run_build(project, target="final")
    assert r1.ok, r1.errors
    assert len(finals()) == 1

    # the compiled timeline carries the mix
    tl = project.load_timeline()
    assert tl.tracks.ambient and tl.tracks.ambient[0].loop is True
    assert any(c.source.endswith("sfx.wav") for c in tl.tracks.sfx)
    assert any(c.source.endswith("whoosh.wav") for c in tl.tracks.sfx)

    # second build: identical content key → NO new final
    assert run_build(project, target="final").ok
    assert len(finals()) == 1

    # editing the SFX bytes changes the audio input hash → exactly one new final
    _sine_wav(sfx, 440, 0.3)
    assert run_build(project, target="final").ok
    assert len(finals()) == 2
