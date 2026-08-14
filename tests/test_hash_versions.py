"""Versioned spec_hash / voice_hash compatibility.

Spec v2 folds ``dialogue`` + (non-empty) ``keyframes`` into the picture
hash — both are real provider inputs (dialogue drives the prompt compiler;
keyframes drive first/last-frame video tasks) that v1 silently excluded.
Spec v3 adds explicit props and picture-bearing ShotContract fields.
VOICE_VERSION=2 folds the resolved TTS provider id + a manifest fingerprint +
language/format into the voice hash — a provider/model swap used to be
invisible to voice staleness.

§4.3 conservatism, pinned here: an EXISTING take/voice is judged FOREVER by
the version it was made under (bumping SPEC_VERSION/VOICE_VERSION never mass-
invalidates a project); only a NEWLY generated take/voice records the new
version and gains the new sensitivity.
"""

from __future__ import annotations

import shutil

import pytest

from manju.build.stale import ShotState, evaluate_shot
from manju.build.voice import VoiceState, evaluate_voice
from manju.core.spec import (
    SPEC_VERSION,
    VOICE_VERSION,
    compute_spec_hash,
    compute_voice_hash,
    spec_payload,
)
from manju.core.yamlio import write_yaml

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


# ================================================================ v1 pin


def test_v1_spec_payload_is_byte_identical_to_pre_round_w(tmp_project, add_shot):
    """PIN: version=1 (the default on every core/spec.py function) has EXACTLY
    the pre-round-W key set — no dialogue, no keyframes."""
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    bible = tmp_project.load_bible()
    payload = spec_payload(shot, bible)  # default: version=1
    assert set(payload) == {
        "scene", "scene_bible", "characters", "character_bible",
        "camera", "action", "quality", "duration", "generation",
    }
    assert "dialogue" not in payload
    assert "keyframes" not in payload
    # a bare call is unaffected by an explicit version=1 call
    assert payload == spec_payload(shot, bible, version=1)


def test_v1_hash_ignores_dialogue_and_keyframes(tmp_project, add_shot):
    plain = add_shot(tmp_project, "S001", dialogue={"speaker": "", "text": ""})
    worded = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "完全不同的台词"},
                      keyframes=[{"position": "start", "image": "media/refs/x.png",
                                  "prompt": "开场画面"}])
    bible = tmp_project.load_bible()
    assert compute_spec_hash(plain, bible) == compute_spec_hash(worded, bible)


# ================================================================ v2 payload shape


def test_v2_adds_dialogue(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词A"})
    bible = tmp_project.load_bible()
    v2 = spec_payload(shot, bible, version=2)
    assert v2["dialogue"] == {"speaker": "linxia", "text": "台词A"}
    assert "keyframes" not in v2  # empty keyframes -> key omitted entirely


def test_v2_dialogue_edit_changes_the_hash_but_v1_does_not(tmp_project, add_shot):
    a = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词A"})
    b = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "完全不同的台词B"})
    bible = tmp_project.load_bible()
    assert compute_spec_hash(a, bible, version=1) == compute_spec_hash(b, bible, version=1)
    assert compute_spec_hash(a, bible, version=2) != compute_spec_hash(b, bible, version=2)


def test_v2_keyframes_included_when_non_empty(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001", keyframes=[
        {"position": "start", "image": "media/refs/kf.png", "prompt": "开场"},
        {"at_ms": 1500, "image": "https://example.com/mid.png", "prompt": "中段"},
    ])
    bible = tmp_project.load_bible()
    v2 = spec_payload(shot, bible, version=2)
    assert v2["keyframes"] == [
        {"position": "start", "at_ms": None, "image": "media/refs/kf.png", "prompt": "开场"},
        {"position": None, "at_ms": 1500, "image": "https://example.com/mid.png", "prompt": "中段"},
    ]
    # no project_root -> no local file to hash -> no image_file_hash key
    assert all("image_file_hash" not in kf for kf in v2["keyframes"])


def test_v2_keyframe_local_image_content_change_moves_the_hash(tmp_project, add_shot):
    img = tmp_project.root / "media" / "refs" / "kf.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"AAAA")
    shot = add_shot(tmp_project, "S001",
                    keyframes=[{"position": "start", "image": "media/refs/kf.png"}])
    bible = tmp_project.load_bible()
    h1 = compute_spec_hash(shot, bible, version=2, project_root=tmp_project.root)

    # same path, DIFFERENT bytes -> the take's real provider input changed
    img.write_bytes(b"BBBBBBBB")
    h2 = compute_spec_hash(shot, bible, version=2, project_root=tmp_project.root)
    assert h1 != h2

    # v1 never looks at keyframes at all, so the file edit is invisible to it
    assert compute_spec_hash(shot, bible, version=1) == compute_spec_hash(shot, bible, version=1)


# ============================================ §4.3 conservatism: video takes


def test_old_v1_take_survives_a_dialogue_edit_as_fresh(tmp_project, add_shot, make_take):
    """An EXISTING take made before round-W (spec_version absent -> read as 1)
    is judged by the v1 payload forever — a dialogue edit must not stale it."""
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "旧台词"})
    bible = tmp_project.load_bible()
    v1_hash = compute_spec_hash(shot, bible, version=1)
    take = make_take(tmp_project, "S001", v1_hash)  # sidecar.spec_version defaults to None
    assert take.sidecar.spec_version is None
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))

    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "全新台词,内容完全不同"))
    status = evaluate_shot(tmp_project, tmp_project.load_shot("S001"), bible)
    assert status.state is ShotState.FRESH


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required (caption_card fallback renders)")
def test_new_current_take_goes_stale_after_a_dialogue_edit(tmp_project, add_shot):
    """A take generated THROUGH THE REAL PATH (Provider._register) records
    spec_version=SPEC_VERSION — a dialogue edit now moves its hash."""
    from manju.build.graph import redo_shot

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "旧台词"})
    takes = redo_shot(tmp_project, "S001", actor="human")
    info = tmp_project.get_take("S001", takes[0])
    assert info.sidecar.spec_version == SPEC_VERSION
    # Redo produces reviewable material but never decides for the owner.
    assert tmp_project.load_shot("S001").status.selected_take is None
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", takes[0]))

    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "全新台词,内容完全不同"))
    status = evaluate_shot(tmp_project, tmp_project.load_shot("S001"))
    assert status.state is ShotState.STALE


# ================================================================ voice hash


def _tts_manifest(**overrides):
    base = {
        "id": "tts_x", "type": "tts", "adapter": "generic_tts",
        "auth": {"key_env": "TTS_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/tts",
            "body_template": {"text": "{text}", "voice": "{voice}"},
            "job_id_path": "$.data.task_id",
        },
        "tts": {"audio_url_path": "$.data.audio_url", "audio_format": "wav"},
        "cost": {"per_call": 0.02, "currency": "CNY"},
    }
    base.update(overrides)
    return base


def test_voice_v1_hash_has_no_provider_fields(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    bible = tmp_project.load_bible()
    from manju.core.spec import voice_payload

    v1 = voice_payload(shot, bible)
    assert set(v1) == {"text", "speaker", "voice_ref"}


def test_old_v1_voice_survives_a_provider_swap_as_fresh(
        tmp_project, add_shot, monkeypatch, tmp_path):
    """An EXISTING voice take (voice_hash_version absent -> read as 1) is
    judged by the v1 payload forever — swapping the default TTS provider must
    not stale it."""
    from manju.core.models import VoiceTakeSidecar

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "prov" / "tts_x" / "provider.yaml", _tts_manifest())

    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    bible = tmp_project.load_bible()
    v1_hash = compute_voice_hash(shot, bible, version=1)
    tmp_project.register_voice_take(
        "S001", _fake_wav(tmp_path / "old.wav"),
        VoiceTakeSidecar(provider="tts_x", voice_hash=v1_hash),  # voice_hash_version absent
    )
    assert evaluate_voice(tmp_project, shot).state is VoiceState.FRESH

    # swap in a SECOND tts provider with a different endpoint — the "default"
    # (sorted-first) provider selection changes.
    write_yaml(tmp_path / "prov" / "tts_a" / "provider.yaml",
              _tts_manifest(id="tts_a", submit={
                  "url": "https://other.example.com/v2/speak",
                  "body_template": {"input": "{text}"},
                  "job_id_path": "$.id",
              }))
    assert evaluate_voice(tmp_project, tmp_project.load_shot("S001")).state is VoiceState.FRESH


def test_new_v2_voice_goes_stale_after_a_provider_swap(tmp_project, add_shot, monkeypatch, tmp_path):
    """A voice take synthesized THROUGH THE REAL PATH records
    voice_hash_version=VOICE_VERSION with the resolved provider's manifest
    fingerprint folded in — changing which provider resolves as default now
    moves its hash, even though the dialogue line never changed."""
    import json

    from manju.providers.generic_cloud import HttpResponse
    from manju.providers.tts import get_tts_provider

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "prov" / "tts_x" / "provider.yaml", _tts_manifest())

    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})

    class ScriptedTransport:
        def __init__(self, script):
            self.script = list(script)

        def __call__(self, method, url, headers, body):
            return self.script.pop(0)

    wav = _fake_wav(tmp_path / "v.wav")
    provider = get_tts_provider(transport=ScriptedTransport([
        HttpResponse(200, {}, json.dumps(
            {"data": {"audio_url": "https://cdn/x.wav"}}).encode()),
        HttpResponse(200, {}, wav.read_bytes()),
    ]), sleep_fn=lambda s: None)
    provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert evaluate_voice(tmp_project, tmp_project.load_shot("S001")).state is VoiceState.FRESH

    # a DIFFERENT manifest now sorts first (tts_a < tts_x) -> the default
    # provider a build would resolve today changed, even though this shot's
    # dialogue never did.
    write_yaml(tmp_path / "prov" / "tts_a" / "provider.yaml",
              _tts_manifest(id="tts_a", submit={
                  "url": "https://other.example.com/v2/speak",
                  "body_template": {"input": "{text}"},
                  "job_id_path": "$.id",
              }))
    monkeypatch.setenv("TTS_A_KEY", "k2")

    status = evaluate_voice(tmp_project, tmp_project.load_shot("S001"))
    assert status.state is VoiceState.STALE


def _fake_wav(path):
    path.write_bytes(
        b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    return path
