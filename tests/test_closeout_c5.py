"""AI_IDE_14_21_CLOSEOUT Track C — C5 Audio Masters red tests (A01–A06).

Contract §6 + CLOSEOUT_C_ADDENDUM C5 rulings 1–4:

- every bus records expected/resolved/dropped clips; ANY unreadable expected
  source blocks that bus's masters AND every master summing it (digital silence
  can never satisfy verification), and dropped clips surface with reasons;
- roles are renamed honestly (RAW_*_STEM / RAW_STEM_SUM /
  M_AND_E_BUS_EXCLUSION_MASTER); the raw sum is never mislabeled a program
  master; PROGRAM_MASTER is emitted ONLY if the final program-mixer chain can be
  reused + hash-verified — otherwise it is absent with a recorded reason;
- sums carry documented headroom/limiter, true peak is measured, and clipping is
  a BLOCKING diagnostic (not a note);
- the M&E claim is bus-exclusion (excludes_voice_bus), never content-level proof.

Real ffmpeg renders over synthetic sines (deterministic, no network). Red-first:
each test fails on the pre-CLOSEOUT tree (silent source drop, FULL_MIX/
M_AND_E_MASTER naming, no level-safety/blocked/roles_absent fields).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.models import AudioClip, Timeline, TimelineRules, TimelineTracks
from manju.media import masters as M

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required for masters")

_FREQ = {"voice": 300, "music": 800, "sfx": 1500, "amb": 120}


def _sine(path: Path, freq: int, dur: float = 1.5, *, amplitude: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={dur}", "-af", f"volume={amplitude}",
         "-ac", "2", "-ar", "48000", str(path)], check=True)


def _audio_project(tmp_path: Path, *, drop: tuple[str, ...] = ()) -> tuple[Project, Timeline]:
    """The healthy four-bus project; names in ``drop`` have their SOURCE FILE
    withheld (the timeline still references it) so the bus has an expected but
    unresolvable clip — the missing-source path."""
    project = Project.create(tmp_path / "P", git_init=False)
    media = project.root / "media"
    for name, freq in _FREQ.items():
        if name not in drop:
            _sine(media / f"{name}.wav", freq)
    tracks = TimelineTracks(
        voice=[AudioClip(source="media/voice.wav", start_ms=0, duration_ms=1500)],
        music=[AudioClip(source="media/music.wav", start_ms=0, duration_ms=1500, gain_db=-6.0)],
        sfx=[AudioClip(source="media/sfx.wav", start_ms=300, duration_ms=400)],
        ambient=[AudioClip(source="media/amb.wav", start_ms=0, duration_ms=1500, gain_db=-20.0)],
    )
    tl = Timeline(duration_ms=1500, tracks=tracks)
    project.save_rules(TimelineRules(mode="manual"))
    project.save_timeline(tl)
    return project, tl


def _hot_project(tmp_path: Path) -> tuple[Project, Timeline]:
    """Two full-scale buses (voice + music, no attenuation) so their raw sum
    would clip without headroom/limiter."""
    project = Project.create(tmp_path / "H", git_init=False)
    media = project.root / "media"
    _sine(media / "voice.wav", 300, amplitude=1.0)
    _sine(media / "music.wav", 800, amplitude=1.0)
    tracks = TimelineTracks(
        voice=[AudioClip(source="media/voice.wav", start_ms=0, duration_ms=1500)],
        music=[AudioClip(source="media/music.wav", start_ms=0, duration_ms=1500)],
    )
    tl = Timeline(duration_ms=1500, tracks=tracks)
    project.save_rules(TimelineRules(mode="manual"))
    project.save_timeline(tl)
    return project, tl


def _by_role(index: dict) -> dict[str, dict]:
    return {a["role"]: a for a in index["artifacts"]}


# ==================================================== A01 missing source blocks


def test_a01_missing_voice_source_blocks_dialogue_full_not_mne(tmp_path):
    """A01: an unreadable voice source blocks the dialogue stem AND the raw stem
    sum (both include the voice bus); the bus-exclusion M&E — which excludes the
    voice bus — is NOT blocked by it ('as appropriate'). Never 'verified'."""
    project, tl = _audio_project(tmp_path, drop=("voice",))
    index = M.render_masters(project, tl)
    arts = _by_role(index)

    assert arts["RAW_DIALOGUE_STEM"]["blocked"] is True
    assert arts["RAW_DIALOGUE_STEM"]["status"] != "COMPLETE"
    assert arts["RAW_STEM_SUM"]["blocked"] is True            # sums the voice bus
    # M&E excludes the voice bus, so a missing voice source does not block it
    assert arts["M_AND_E_BUS_EXCLUSION_MASTER"]["blocked"] is False
    # digital silence substituted for the dropped voice never counts as verified
    assert arts["RAW_DIALOGUE_STEM"]["status"] in ("BLOCKED", "INCOMPLETE")


# ==================================================== A02 dropped clip in index


def test_a02_dropped_clip_appears_in_index_with_reason(tmp_path):
    """A02: a dropped (unresolvable) clip surfaces per-bus (expected/resolved/
    dropped counts + a reason) and in the index-level dropped summary."""
    project, tl = _audio_project(tmp_path, drop=("music",))
    index = M.render_masters(project, tl)
    mus = _by_role(index)["RAW_MUSIC_STEM"]

    assert mus["expected_clips"] == 1
    assert mus["resolved_clips"] == 0
    assert len(mus["dropped_clips"]) == 1
    drop = mus["dropped_clips"][0]
    assert drop["source"] == "media/music.wav"
    assert drop["reason"]                                     # a non-empty reason
    # a healthy bus records its clips resolved with zero drops
    voice = _by_role(index)["RAW_DIALOGUE_STEM"]
    assert voice["expected_clips"] == 1 and voice["resolved_clips"] == 1
    assert voice["dropped_clips"] == []
    # index-level roll-up names the drop too
    assert any(d.get("source") == "media/music.wav" for d in index.get("dropped_clips", []))


# ==================================================== A03 level safety / clipping


def test_a03_two_hot_buses_do_not_clip_silently(tmp_path):
    """A03: the raw sum carries documented headroom/limiter and a measured true
    peak; if two hot buses still push it over the ceiling, that is a BLOCKING
    diagnostic (never silent)."""
    project, tl = _hot_project(tmp_path)
    index = M.render_masters(project, tl)
    s = _by_role(index)["RAW_STEM_SUM"]

    assert s["level_safety"]["method"] in ("limiter", "headroom")
    assert s["level_safety"]["ceiling_dbtp"] <= 0.0
    tp = s["loudness"]["true_peak_dbtp"]
    assert tp is not None                                     # true peak measured
    clip_diags = [d for d in index.get("diagnostics", [])
                  if d.get("code") == "AUDIO_CLIPPING"]
    if tp > s["level_safety"]["ceiling_dbtp"]:
        # over the ceiling => it is FLAGGED, blocking, not a silent note
        assert any(d.get("severity") == "blocking" for d in clip_diags)

    # the clipping check is a real blocking mechanism, exercised deterministically
    diag = M._level_safety_diagnostics([
        {"role": "RAW_STEM_SUM", "loudness": {"true_peak_dbtp": 0.9},
         "level_safety": {"ceiling_dbtp": -1.0}}])
    assert diag and diag[0]["code"] == "AUDIO_CLIPPING"
    assert diag[0]["severity"] == "blocking"


# ==================================================== A04 honest role naming


def test_a04_raw_stem_sum_not_mislabeled_program_master(tmp_path):
    """A04: honest RAW_* role names; the raw four-bus sum is RAW_STEM_SUM, never a
    PROGRAM_MASTER (or the misleading 'FULL_MIX'); ambient is its own stem; the
    loudnorm'd sum stays a distinct kind, never conflated with the raw sum."""
    project, tl = _audio_project(tmp_path)
    index = M.render_masters(project, tl, loudness_target_lufs=-14.0)
    roles = {a["role"] for a in index["artifacts"]}

    assert roles == {"RAW_DIALOGUE_STEM", "RAW_MUSIC_STEM", "RAW_SFX_STEM",
                     "RAW_AMBIENT_STEM", "RAW_STEM_SUM",
                     "M_AND_E_BUS_EXCLUSION_MASTER"}
    assert "PROGRAM_MASTER" not in roles
    assert "FULL_MIX" not in roles                            # misleading name retired

    sumart = next(a for a in index["artifacts"] if a["role"] == "RAW_STEM_SUM")
    assert sumart.get("is_program_master") in (False, None)
    assert sumart["kind"] == "stem_sum"
    # loudnorm'd sum is a DISTINCT kind, never the raw sum's kind
    ln = index["loudnorm_master"]
    assert ln is not None and ln["kind"] != "stem_sum"
    assert "loudnorm" in ln["kind"]


# ==================================================== A05 program master honesty


def test_a05_program_master_absent_with_recorded_reason(tmp_path):
    """A05: the masters renderer does NOT reuse the final program mixer's
    ducking/loudness/automation chain (stems are pre-duck/pre-loudnorm PCM; the
    final's program audio is lossy AAC in the mp4), so no PROGRAM_MASTER that
    could be hash-verified to the final video exists. It is honestly ABSENT with
    a recorded role_absent reason — never a raw sum masquerading as one."""
    project, tl = _audio_project(tmp_path)
    index = M.render_masters(project, tl)
    roles = {a["role"] for a in index["artifacts"]}
    assert "PROGRAM_MASTER" not in roles

    absent = {r["role"]: r for r in index.get("roles_absent", [])}
    assert "PROGRAM_MASTER" in absent
    reason = absent["PROGRAM_MASTER"]["reason"]
    assert reason and any(k in reason for k in
                          ("program", "ducking", "loudness", "final", "mixer"))


# ==================================================== A06 M&E bus-exclusion claim


def test_a06_mne_claim_is_bus_exclusion_not_content_proof(tmp_path):
    """A06: the M&E claim states bus-exclusion semantics (excludes_voice_bus),
    never a content-level proof, in both the masters index and the delivery
    manifest row."""
    from manju.build import delivery as D

    project, tl = _audio_project(tmp_path)
    index = M.render_masters(project, tl)
    mne = _by_role(index)["M_AND_E_BUS_EXCLUSION_MASTER"]

    assert mne["excludes_voice_bus"] is True
    assert mne["mne_claim"] == "bus_exclusion"
    assert mne.get("content_verified") in (False, None)
    assert "BUS_EXCLUSION" in mne["role"]

    man = D.build_manifest(project, "master")
    row = next(a for a in man["artifacts"]
               if a["role"] == "M_AND_E_BUS_EXCLUSION_MASTER")
    assert row["audio"]["excludes_voice_bus"] is True
    assert row["audio"]["mne_claim"] == "bus_exclusion"
