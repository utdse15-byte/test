"""Round K: the Edge TTS dedicated adapter — a real, keyless cloud TTS wired
through the §8.6 module:Class escape hatch. Unit tests run offline; the live
synthesis test exercises the actual service and skips cleanly when the
network/trust store makes it unreachable."""

from __future__ import annotations

import importlib.util
import os
import shutil

import pytest

from manju.core.yamlio import write_yaml

has_edge = importlib.util.find_spec("edge_tts") is not None


def _edge_manifest(**overrides):
    base = {
        "id": "edge",
        "type": "tts",
        "adapter": "manju.providers.edge_tts:EdgeTtsProvider",
        "tts": {"default_voice": "zh-CN-YunxiNeural", "audio_format": "mp3"},
        "cost": {"per_call": 0.0, "currency": "CNY"},
    }
    base.update(overrides)
    return base


@pytest.fixture
def edge_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    write_yaml(tmp_path / "prov" / "edge" / "provider.yaml", _edge_manifest())


# ------------------------------------------------------------ escape hatch


def test_module_class_escape_hatch_resolves(edge_env):
    from manju.providers.edge_tts import EdgeTtsProvider
    from manju.providers.tts import get_tts_provider

    provider = get_tts_provider("edge")
    assert isinstance(provider, EdgeTtsProvider)
    assert provider.id == "edge"


def test_unloadable_adapter_is_actionable(tmp_path, monkeypatch):
    from manju.providers.tts import TtsUnavailable, get_tts_provider

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "p"))
    write_yaml(tmp_path / "p" / "bad" / "provider.yaml",
               _edge_manifest(id="bad", adapter="no.such.module:Nope"))
    with pytest.raises(TtsUnavailable) as exc:
        get_tts_provider("bad")
    assert "cannot load adapter" in str(exc.value)


def test_voice_resolution_bible_wins(edge_env, tmp_project, add_shot):
    from manju.providers.tts import get_tts_provider

    provider = get_tts_provider("edge")
    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "台词"})
    bible = dict(tmp_project.load_bible())
    assert provider._voice_for(shot, bible) == "zh-CN-YunxiNeural"  # manifest default
    bible["linxia"] = {**(bible.get("linxia") or {}), "voice_id": "zh-CN-XiaoxiaoNeural"}
    assert provider._voice_for(shot, bible) == "zh-CN-XiaoxiaoNeural"  # bible wins


# --------------------------------------------------------------- live wire


@pytest.mark.skipif(not has_edge, reason="edge-tts not installed")
@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe required")
def test_live_synthesis_registers_real_voice_take(edge_env, tmp_project, add_shot,
                                                  monkeypatch):
    """Hits the real Edge TTS service. Skips (never fails) when the service
    is unreachable — offline CI stays green, online CI proves the wire."""
    from manju.providers.base import ProviderFailure
    from manju.providers.tts import get_tts_provider

    ca = "/root/.ccr/ca-bundle.crt"
    if os.path.exists(ca) and not os.environ.get("SSL_CERT_FILE"):
        monkeypatch.setenv("SSL_CERT_FILE", ca)  # trust the egress proxy CA

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "凌晨三点,还有人醒着吗?"})
    provider = get_tts_provider("edge")
    try:
        media = provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    except ProviderFailure as exc:
        pytest.skip(f"Edge TTS unreachable here: {exc}")

    assert media.suffix == ".mp3" and media.stat().st_size > 1000
    from manju.media.probe import probe_duration_ms

    duration = probe_duration_ms(media)
    assert duration and duration > 1000  # a real spoken sentence, not a stub

    _, sidecar = tmp_project.voice_takes("S001")[-1]
    assert sidecar is not None and sidecar.provider == "edge"
    assert sidecar.params["voice"] == "zh-CN-YunxiNeural"

    # and the compiler picks it up: audio drives picture (§6)
    from manju.build.voice import VoiceState, evaluate_voice

    assert evaluate_voice(tmp_project, shot).state is VoiceState.FRESH
