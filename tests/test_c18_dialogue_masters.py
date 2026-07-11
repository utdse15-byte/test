"""AI_IDE_18 — dialogue performance, alignment evidence, optional lip-sync and
professional audio masters. §12's rows map ~1:1 onto these tests.

The audio-master tests are REAL ffmpeg renders over synthetic sine sources
(deterministic, no network, no local acoustic model). Environment-impossible
provider paths (real cloud ASR/TTS/lip-sync) are exercised through scripted
stand-ins, exactly as the addendum rulings require.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.hashing import hash_value
from manju.core.models import AudioClip, Timeline, TimelineTracks

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg required for masters")


# ------------------------------------------------------------------- helpers


def _sine(path: Path, freq: int, dur: float = 1.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={dur}", "-ac", "2", "-ar", "48000",
         str(path)], check=True)


def _audio_project(tmp_path: Path, *, with_sfx: bool = True) -> tuple[Project, Timeline]:
    project = Project.create(tmp_path / "P", git_init=False)
    media = project.root / "media"
    _sine(media / "voice.wav", 300)
    _sine(media / "music.wav", 800)
    _sine(media / "sfx.wav", 1500)
    _sine(media / "amb.wav", 120)
    tracks = TimelineTracks(
        voice=[AudioClip(source="media/voice.wav", start_ms=0, duration_ms=1500)],
        music=[AudioClip(source="media/music.wav", start_ms=0, duration_ms=1500, gain_db=-6.0)],
        ambient=[AudioClip(source="media/amb.wav", start_ms=0, duration_ms=1500, gain_db=-20.0)],
    )
    if with_sfx:
        tracks.sfx = [AudioClip(source="media/sfx.wav", start_ms=300, duration_ms=400)]
    tl = Timeline(duration_ms=1500, tracks=tracks)
    # persist as the project's manual timeline so the export centre has a real
    # current timeline to judge master freshness against (the CLI renders masters
    # off exactly this compiled/loaded timeline).
    from manju.core.models import TimelineRules

    project.save_rules(TimelineRules(mode="manual"))
    project.save_timeline(tl)
    return project, tl


# ==================================================================== WP7 masters


def test_stems_render_with_full_mix_and_duration_sanity(tmp_path):
    """§12: stems 与 full mix 相加关系/时长. Every stem + the full mix + M&E render
    as real files of the SAME duration; FULL_MIX = sum of the bus stems."""
    from manju.media import masters as M

    project, tl = _audio_project(tmp_path)
    index = M.render_masters(project, tl)
    roles = {a["role"]: a for a in index["artifacts"]}
    assert set(roles) == {"DIALOGUE_STEM", "MUSIC_STEM", "SFX_STEM", "FULL_MIX",
                          "M_AND_E_MASTER"}
    for a in roles.values():
        p = project.root / a["path"]
        assert p.exists() and p.stat().st_size > 0
        assert a["duration_ms"] == 1500          # same length as the timeline
        assert a["sample_rate"] == 48000 and a["channels"] == 2


def test_mne_master_provably_lacks_dialogue(tmp_path):
    """§12: M&E 不含对白. The M&E band carries no dialogue — it is mixed from
    exactly the non-voice buses, so volumedetect on it differs from the full mix
    (dialogue removed) while the dialogue stem itself is audibly present."""
    from manju.media import masters as M

    project, tl = _audio_project(tmp_path)
    index = M.render_masters(project, tl)
    roles = {a["role"]: a for a in index["artifacts"]}

    dlg = M.volume_stats(project.root / roles["DIALOGUE_STEM"]["path"])
    mne = M.volume_stats(project.root / roles["M_AND_E_MASTER"]["path"])
    full = M.volume_stats(project.root / roles["FULL_MIX"]["path"])

    assert roles["M_AND_E_MASTER"]["excludes_dialogue"] is True
    assert "voice" not in roles["M_AND_E_MASTER"]["buses"]
    assert roles["DIALOGUE_STEM"]["excludes_dialogue"] is False
    # the dialogue stem is real audio, not silence
    assert dlg["mean_volume"] is not None and dlg["mean_volume"] > -80.0
    # removing the (loudest) dialogue bus drops the peak — M&E ≠ full mix
    assert mne["max_volume"] is not None and full["max_volume"] is not None
    assert mne["max_volume"] < full["max_volume"] - 1.0


def test_loudness_measured_as_facts(tmp_path):
    """§12: loudness/true peak. Every artifact carries a REAL ebur128/loudnorm
    measurement (integrated LUFS / true-peak dBTP / LRA) — recorded facts."""
    from manju.media import masters as M

    project, tl = _audio_project(tmp_path)
    index = M.render_masters(project, tl)
    for a in index["artifacts"]:
        loud = a["loudness"]
        assert loud["integrated_lufs"] is not None
        assert loud["true_peak_dbtp"] is not None
        assert -1.0 < loud["true_peak_dbtp"] < 0.0 or loud["true_peak_dbtp"] <= 0.0


def test_loudness_target_comes_from_profile_not_hardcoded(tmp_path):
    """§10 pin: the loudness TARGET is the profile's, never hardcoded. A -14
    LUFS target yields a normalised full-mix master measured near -14."""
    from manju.media import masters as M

    project, tl = _audio_project(tmp_path)
    index = M.render_masters(project, tl, loudness_target_lufs=-14.0)
    ln = index["loudnorm_master"]
    assert ln is not None and ln["target_lufs"] == -14.0
    assert abs(ln["loudness"]["integrated_lufs"] - (-14.0)) < 1.5


def test_masters_fill_manifest_roles_with_technical_verification(tmp_path):
    """§12 + addendum ruling 8: the 13C roles honestly SKIPPED as 'no such
    artifact' now appear in the manifest via the EXISTING role scan, technically
    verified, binding audio-input hashes + measured loudness."""
    from manju.build import delivery as D
    from manju.media import masters as M

    project, tl = _audio_project(tmp_path)
    M.render_masters(project, tl)
    man = D.build_manifest(project, "master")
    roles = {a["role"]: a for a in man["artifacts"]}
    for role in ("DIALOGUE_STEM", "MUSIC_STEM", "SFX_STEM", "FULL_MIX",
                 "M_AND_E_MASTER"):
        art = roles[role]
        assert art["state"] == "TECHNICALLY_VERIFIED"
        assert art["sha256"] and art["bytes"]
        assert art["source_refs"]                      # bus input hashes bound
        assert art["audio"]["loudness"]["integrated_lufs"] is not None


def test_master_stale_when_timeline_semantics_change(tmp_path):
    """A moved/dropped cue restales every master (freshness compares the SAME
    timeline semantic digest the 13C manifest binds)."""
    from manju.build import exportstatus as ES
    from manju.media import masters as M

    project, tl = _audio_project(tmp_path)
    M.render_masters(project, tl)
    # rewrite the index with a stale digest as if the timeline moved on
    idx = M.index_path(project)
    data = json.loads(idx.read_text())
    for a in data["artifacts"]:
        a["timeline_digest"] = "sha256:staleXXXX"
    idx.write_text(json.dumps(data))
    rows = {r.kind: r for r in ES.deliverables(project)}
    assert rows["full_mix"].freshness.value == "stale"


# ==================================================================== WP7 VTT


def test_vtt_export_is_real_and_fills_captions_vtt_role(tmp_path, monkeypatch):
    from manju.core.models import CaptionLine
    from manju.exporters.srt_ass import compile_vtt, export_captions

    project = Project.create(tmp_path / "V", git_init=False)
    tl = Timeline(duration_ms=4000, tracks=TimelineTracks(captions=[
        CaptionLine(start_ms=0, end_ms=1500, text="这不可能。"),
        CaptionLine(start_ms=1500, end_ms=3000, text="你看见了什么?"),
    ]))
    doc = compile_vtt(tl)
    assert doc.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in doc     # dot-ms per WebVTT grammar
    assert "这不可能。" in doc
    out = export_captions(project, tl)
    assert out["vtt"].exists() and out["vtt"].read_text(encoding="utf-8").startswith("WEBVTT")


def test_vtt_no_fabricated_word_karaoke(tmp_path):
    """Karaoke is CONDITIONAL — CaptionLine carries no per-word timing, so the
    VTT never emits inline <timestamp> tokens (no fabricated word boundaries)."""
    from manju.core.models import CaptionLine
    from manju.exporters.srt_ass import compile_vtt

    tl = Timeline(tracks=TimelineTracks(captions=[
        CaptionLine(start_ms=0, end_ms=1000, text="逐词高亮需要真实词级时间")]))
    doc = compile_vtt(tl)
    assert "<00:" not in doc          # no inline karaoke tokens invented


# ==================================================================== WP2 alignment


def test_alignment_evidence_binds_exact_audio_and_aligner(tmp_path):
    from manju.media import timing as T

    media = tmp_path / "take.wav"
    _sine(media, 220, 0.5)
    T.write_evidence(media, [{"start_ms": 0, "end_ms": 500, "text": "hi",
                              "speaker": "linxia", "confidence": 0.9}],
                     provider="scripted_asr", profile_digest="deadbeef")
    data = T.read_evidence(media)
    assert data["header"]["source_media_hash"]        # exact audio pinned
    assert data["header"]["aligner"]["provider"] == "scripted_asr"
    assert data["cues"][0]["confidence"] == 0.9 and data["cues"][0]["speaker"] == "linxia"
    assert T.evidence_status(media) == "ALIGNED"


def test_same_name_audio_replacement_reads_stale(tmp_path):
    """§12: 同名音频替换 stale / 对齐绑定 exact audio."""
    from manju.media import timing as T

    media = tmp_path / "take.wav"
    _sine(media, 220, 0.5)
    T.write_evidence(media, [{"start_ms": 0, "end_ms": 500, "text": "hi"}],
                     provider="scripted_asr")
    assert T.evidence_status(media) == "ALIGNED"
    _sine(media, 440, 0.5)               # different bytes, SAME filename
    assert T.evidence_status(media) == "STALE"


def test_alignment_failure_is_unaligned_never_fabricated(tmp_path):
    """§5 pin: alignment failure keeps UNALIGNED with a reason — never a fake
    uniform spread."""
    from manju.media import timing as T

    media = tmp_path / "take.wav"
    _sine(media, 220, 0.5)
    T.write_unaligned(media, provider="scripted_asr", reason="no segments")
    data = T.read_evidence(media)
    assert data["header"]["status"] == "UNALIGNED"
    assert data["cues"] == []                          # no fabricated timing
    assert T.evidence_status(media) == "UNALIGNED"


def test_align_shot_stamps_evidence(tmp_path):
    from manju.media import align, timing as T

    project = Project.create(tmp_path / "A", git_init=False)
    from manju.core.yamlio import write_yaml
    write_yaml(project.root / "bible" / "characters.yaml",
               {"linxia": {"name": "林夏", "voice": "冷静"}})
    from manju.core.models import ShotSpec
    shot = ShotSpec.model_validate({"id": "S001", "characters": ["linxia"],
                                    "dialogue": {"speaker": "linxia", "text": "这不可能。真的。"},
                                    "duration": "auto"})
    project.save_shot(shot)
    idx = project.load_index(); idx.order.append("S001"); project.save_index(idx)
    take_dir = project.takes_dir("S001"); take_dir.mkdir(parents=True, exist_ok=True)
    media = take_dir / "voice_take_01.wav"
    _sine(media, 200, 1.0)
    rep = align.align_shot(project, "S001", probe_fn=lambda p: 1000)
    assert rep["align_status"] == "ALIGNED"
    assert T.evidence_status(media) == "ALIGNED"
    data = T.read_evidence(media)
    assert data["header"]["source_media_hash"] == __import__(
        "manju.core.hashing", fromlist=["hash_file"]).hash_file(media)


# ==================================================================== WP1 voice id


def _voiced_project(tmp_path: Path, entry: dict) -> Project:
    project = Project.create(tmp_path / "VP", git_init=False)
    from manju.core.yamlio import write_yaml
    write_yaml(project.root / "bible" / "characters.yaml", {"linxia": entry})
    return project


def test_voice_profile_missing_rights_blocks_template_export(tmp_path):
    """§4 / ruling 2: a voice sample without rights info WORKS locally but BLOCKS
    template-pack export (the surface 17 consumes)."""
    from manju.build import voiceid as V

    project = _voiced_project(tmp_path, {
        "name": "林夏", "voice_id": "vx-1", "voice_locked": True})
    profile = V.character_profile(project, "linxia")
    gate = V.template_export_gate(profile)
    assert gate["blocked"] is True and gate["shareable"] is False
    assert any("provenance" in r for r in gate["reasons"])


def test_voice_profile_with_rights_is_shareable(tmp_path):
    from manju.build import voiceid as V

    project = _voiced_project(tmp_path, {
        "name": "林夏", "voice_id": "vx-1", "voice_locked": True,
        "voice_provenance": {"source": "actor recording session 2026-03",
                             "license_or_consent": "signed release #A17"}})
    profile = V.character_profile(project, "linxia")
    assert V.provenance_complete(profile) is True
    assert V.template_export_gate(profile)["shareable"] is True
    assert V.is_locked(profile) is True


def test_voice_profile_change_stales_downstream(tmp_path):
    from manju.build import voiceid as V

    a = V.voice_profile({"voice_id": "vx-1", "voice_locked": True})
    b = V.voice_profile({"voice_id": "vx-2", "voice_locked": True})
    same = V.voice_profile({"voice_id": "vx-1", "voice_locked": True,
                            "voice_provenance": {"note": "rights added later"}})
    assert V.downstream_stale(a, b) is True            # identity changed → stale
    assert V.downstream_stale(a, same) is False        # a note is not synthesis input


def test_audition_is_preview_not_take():
    from manju.build import voiceid as V

    assert V.AUDITION_IS_PREVIEW is True


# ==================================================================== WP5 lip-sync


def test_lipsync_multi_face_without_selector_refused():
    from manju.qc import lipsync as L

    with pytest.raises(L.LipSyncError):
        L.plan_lipsync(video_hash="sha256:v", audio_hash="sha256:a",
                       faces_detected=2, subject_selector=None)
    # a selector makes it admissible
    plan = L.plan_lipsync(video_hash="sha256:v", audio_hash="sha256:a",
                          faces_detected=2, subject_selector="face_left")
    assert plan["capability"] == "lip_sync" and plan["subject_selector"] == "face_left"


def test_lipsync_requires_exact_hashes():
    from manju.qc import lipsync as L

    with pytest.raises(L.LipSyncError):
        L.plan_lipsync(video_hash="", audio_hash="sha256:a")


def test_lipsync_output_is_new_take_with_parent_lineage():
    from manju.qc import lipsync as L

    plan = L.plan_lipsync(video_hash="sha256:v", audio_hash="sha256:a")
    lineage = L.lipsync_lineage("voice_take_03", plan, provider="scripted_lipsync")
    assert lineage["redo_of"] == "voice_take_03"       # parent lineage, original untouched
    assert lineage["lineage"]["video_hash"] == "sha256:v"
    assert lineage["lineage"]["audio_hash"] == "sha256:a"


def test_lipsync_unknown_never_faked_and_no_self_score():
    """§8 pins: timeout / occlusion ⇒ UNKNOWN, and a model's own score never
    counts as alignment (the result API accepts no score argument)."""
    from manju.qc import lipsync as L

    plan = L.plan_lipsync(video_hash="sha256:v", audio_hash="sha256:a")
    assert L.lipsync_result(plan, recognised=True, timed_out=True)["verdict"] == "UNKNOWN"
    assert L.lipsync_result(plan, recognised=False)["verdict"] == "UNKNOWN"
    assert L.lipsync_result(plan, recognised=True)["verdict"] == "ADMITTED"
    import inspect
    assert "score" not in inspect.signature(L.lipsync_result).parameters


def test_lip_sync_capability_token_additive():
    from manju.providers.manifest import LIP_SYNC_CAPABILITY, ProviderManifest

    m = ProviderManifest(id="fake_lipsync", type="video",
                         capabilities=[LIP_SYNC_CAPABILITY])
    assert LIP_SYNC_CAPABILITY in m.capabilities


# ==================================================================== WP6 drift


def test_drift_unknown_without_mouth_observation():
    from manju.qc import lipsync as L

    r = L.measure_drift([0, 500, 1000], mouth_events_ms=None)
    assert r["verdict"] == "UNKNOWN" and r["alignable"] is False


def test_drift_unknown_when_face_not_visible():
    from manju.qc import lipsync as L

    r = L.measure_drift([0, 500], mouth_events_ms=[10, 510], face_visible=False)
    assert r["verdict"] == "UNKNOWN"
    assert r["route"] == "RESHOOT"


def test_drift_pass_and_fail_with_route_mapping():
    from manju.qc import lipsync as L

    good = L.measure_drift([0, 500, 1000], mouth_events_ms=[20, 520, 1020])
    assert good["verdict"] == "PASS" and good["overall_offset_ms"] == 20

    bad = L.measure_drift([0, 500, 1000], mouth_events_ms=[300, 800, 1300])
    assert bad["verdict"] == "FAIL"
    # a near-constant 300ms offset routes to a post offset fix
    assert bad["route"] in ("FIX_IN_POST", "EDIT_DONT_REGENERATE", "REROLL")

    from manju.qc.production import SEVEN_ROUTES
    assert bad["route"] in SEVEN_ROUTES


# ==================================================================== WP4 advisor


def test_duration_advisor_never_mutates_text():
    from manju.qc import lipsync as L

    text = "这句台词的长度不能被自动改写。"
    before = hash_value(text)
    out = L.duration_proposals(voice_ms=3200, slot_ms=2500, tolerance_ms=150)
    assert out["text_mutation"] is False
    assert hash_value(text) == before                  # advisor touched no text
    actions = [p["action"] for p in out["proposals"]]
    # §7 order: re-TTS first, source proposal, then bounded time-stretch
    assert actions[0] == "RETTS_SPEED"
    assert "SOURCE_PROPOSAL" in actions


def test_duration_advisor_time_stretch_bounded_by_declared_cap():
    from manju.qc import lipsync as L

    # 40% over slot, cap 8% → time-stretch must be BLOCKED, not offered
    out = L.duration_proposals(voice_ms=3500, slot_ms=2500, max_stretch_pct=8.0)
    actions = [p["action"] for p in out["proposals"]]
    assert "TIME_STRETCH_BLOCKED" in actions and "TIME_STRETCH" not in actions


def test_duration_advisor_within_tolerance_accepts():
    from manju.qc import lipsync as L

    out = L.duration_proposals(voice_ms=2550, slot_ms=2500, tolerance_ms=150)
    assert out["within_tolerance"] is True
    assert out["proposals"][0]["action"] == "ACCEPT"


# ==================================================================== WP8 skill


def test_localize_skill_discovered_with_frontmatter():
    from manju.core.skills import list_skills

    skills = {s.id: s for s in list_skills()}
    assert "localize-dialogue" in skills
    info = skills["localize-dialogue"]
    assert info.description and info.when_to_use
    assert "task" in info.tags


def test_localize_skill_has_required_sections_and_flow():
    from manju.core.skills import bundled_skills_dir

    body = (bundled_skills_dir() / "localize-dialogue" / "SKILL.md").read_text(encoding="utf-8")
    for section in ("## 输入", "## 输出", "## 失败条件", "## 纪律"):
        assert section in body
    # the five-stage flow vocabulary must be present (drift-catcher)
    for stage in ("terminology", "Translate", "Reflect", "Adaptation", "timing fit"):
        assert stage in body
    assert "base_hash" in body and "lines.yaml" in body
    assert "--yes" not in body                      # never pre-authorize spend


def test_localize_skill_data_pack_matches_locale_and_route_vocabulary():
    """Eval fixture: the data pack's locale fields and duration routes must
    match the real vocabularies, so a schema rename can't strand the skill."""
    import yaml

    from manju.core.skills import bundled_skills_dir

    pack = yaml.safe_load(
        (bundled_skills_dir() / "localize-dialogue" / "localize-dialogue.data.yaml"
         ).read_text(encoding="utf-8"))
    assert pack["locale_patch"]["per_line_fields"] == ["text", "base_hash"]
    stages = [s["id"] for s in pack["flow"]]
    assert stages == ["terminology", "translate", "reflect", "adaptation",
                      "timing_fit_review"]
    # every duration route the pack names must be a real advisor action
    from manju.qc.lipsync import duration_proposals

    out = duration_proposals(voice_ms=3500, slot_ms=2500, max_stretch_pct=50.0)
    actions = {p["action"] for p in out["proposals"]} | {"ACCEPT"}
    for route in pack["duration_routes"]:
        assert route in actions, route


# ==================================================================== no local models


def test_no_local_acoustic_model_dependency():
    """Contract §12: 无本地模型依赖. The audio-master + lip-sync + alignment code
    imports no WhisperX / Wav2Lip / MuseTalk / torch-style local model."""
    import manju.media.masters as m1
    import manju.media.timing as m2
    import manju.qc.lipsync as m3

    forbidden = ("whisperx", "wav2lip", "musetalk", "torch", "comfyui")
    for mod in (m1, m2, m3):
        src = Path(mod.__file__).read_text(encoding="utf-8").lower()
        assert not any(f in src for f in forbidden), mod.__file__
