"""Cloud ASR plugin slot (M4) + manual transcribe on-ramps.

The cloud path is verified offline with a scripted transport (no ASR account
exists here); the manual paths (--from-srt / --text) are fully exercised —
they are usable today without any provider.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from manju.core.yamlio import write_yaml
from manju.providers.asr import (
    AsrUnavailable,
    GenericAsrProvider,
    distribute_text,
    get_asr_provider,
    parse_srt,
    segments_to_srt,
)
from manju.providers.base import FailureKind, ProviderFailure
from manju.providers.generic_cloud import HttpResponse
from manju.providers.manifest import ProviderManifest

# ------------------------------------------------------------- manual: srt

SRT_SAMPLE = """\
1
00:00:00,000 --> 00:00:02,500
这不可能。

2
00:00:03,000 --> 00:00:05,000
硬币的年份,
是2036。
"""


def test_parse_srt_roundtrip():
    segments = parse_srt(SRT_SAMPLE)
    assert len(segments) == 2
    assert segments[0].start_ms == 0 and segments[0].end_ms == 2500
    assert segments[1].text == "硬币的年份,\n是2036。"
    # normalize -> emit -> parse again is stable
    assert parse_srt(segments_to_srt(segments)) == segments


def test_parse_srt_tolerates_mess():
    messy = "﻿" + SRT_SAMPLE.replace("\n", "\r\n").replace("1\r\n00:", "00:")
    segments = parse_srt(messy)
    assert len(segments) == 2 and segments[0].text == "这不可能。"


# ------------------------------------------------------------ manual: text


def test_distribute_text_weights_and_order():
    segments = distribute_text("第一句很长很长很长很长。短句!最后一句中等长度。", 10000)
    assert len(segments) == 3
    assert segments[0].start_ms == 0
    starts = [s.start_ms for s in segments]
    assert starts == sorted(starts)
    assert all(s.end_ms > s.start_ms for s in segments)
    # longest sentence gets the longest span
    spans = [s.end_ms - s.start_ms for s in segments]
    assert spans[0] == max(spans)


def test_distribute_text_empty():
    assert distribute_text("", 5000) == []
    assert distribute_text("你好", 0) == []


# ------------------------------------------------------------- cloud slot


def _asr_manifest(**overrides):
    base = {
        "id": "asr_x",
        "type": "asr",
        "adapter": "generic_asr",
        "auth": {"key_env": "ASR_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/asr",
            "body_template": {"audio": "{audio_b64}", "lang": "{language}"},
            "job_id_path": "$.data.task_id",
        },
        "asr": {"segments_path": "$.data.segments", "text_key": "text",
                "start_key": "begin", "end_key": "end", "time_unit": "s"},
    }
    base.update(overrides)
    return base


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return self.script.pop(0)


def _resp(payload):
    return HttpResponse(200, {}, json.dumps(payload).encode())


def test_sync_asr_submit_is_result(monkeypatch, tmp_path):
    """§8.4 degenerate form: no poll section -> the submit response carries
    the transcript; seconds are converted to ms per asr.time_unit."""
    monkeypatch.setenv("ASR_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_asr_manifest())
    transport = ScriptedTransport([_resp({
        "data": {"segments": [
            {"begin": 0.0, "end": 2.5, "text": "这不可能。"},
            {"begin": 3.0, "end": 5.0, "text": "是2036。"},
        ]}
    })])
    provider = GenericAsrProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    media = tmp_path / "a.wav"
    media.write_bytes(b"RIFFfake")
    segments = provider.transcribe(media)
    assert [s.text for s in segments] == ["这不可能。", "是2036。"]
    assert segments[0].end_ms == 2500  # s -> ms
    sent = json.loads(transport.requests[0][3])
    assert sent["lang"] == "zh" and sent["audio"]  # b64 payload present


def test_transcribe_refuses_oversized_local_file(monkeypatch, tmp_path):
    """Goal 54: a local media file over the (shared, generic_cloud) size cap
    is refused before being loaded whole into memory for base64 upload."""
    monkeypatch.setenv("ASR_X_KEY", "k")
    monkeypatch.setenv("MANJU_MAX_DOWNLOAD_BYTES", "1024")
    manifest = ProviderManifest.model_validate(_asr_manifest())
    provider = GenericAsrProvider(manifest, transport=ScriptedTransport([]),
                                  sleep_fn=lambda s: None)
    media = tmp_path / "huge.wav"
    media.write_bytes(b"x" * 2048)
    with pytest.raises(ProviderFailure) as exc:
        provider.transcribe(media)
    assert "cap" in str(exc.value).lower() or "1024" in str(exc.value)


def test_async_asr_polls_to_completion(monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_asr_manifest(
        poll={"url": "https://api.example.com/v1/asr/{job_id}",
              "status_path": "$.data.status",
              "status_map": {"DONE": "succeeded", "RUNNING": "running"}},
    ))
    transport = ScriptedTransport([
        _resp({"data": {"task_id": "t1"}}),
        _resp({"data": {"status": "RUNNING"}}),
        _resp({"data": {"status": "DONE",
                        "segments": [{"begin": 0, "end": 1.5, "text": "你好"}]}}),
    ])
    provider = GenericAsrProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")
    segments = provider.transcribe(media)
    assert len(segments) == 1 and segments[0].end_ms == 1500
    assert len(transport.requests) == 3


def test_asr_submit_429_is_rate_limited(monkeypatch, tmp_path):
    """F4: a 429 on ASR submit is retryable rate_limited (like its tts sibling),
    NOT a generic provider_error. RED at HEAD: asr mapped every >=400 to
    provider_error, discarding the retryable signal + throttle hint."""
    monkeypatch.setenv("ASR_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_asr_manifest())
    transport = ScriptedTransport([HttpResponse(429, {}, b'{"error":"slow down"}')])
    provider = GenericAsrProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    media = tmp_path / "a.wav"
    media.write_bytes(b"RIFFfake")
    with pytest.raises(ProviderFailure) as exc:
        provider.transcribe(media)
    assert exc.value.kind is FailureKind.rate_limited


def test_asr_poll_429_retries_the_same_job_never_resubmits(monkeypatch, tmp_path):
    """Audit finding 10: a 429 while POLLING an ACCEPTED job must keep polling
    the SAME job id, NOT abandon it — the old code raised rate_limited, and a
    higher-level retry then submitted (and paid for) a whole new job. The poll
    loop now retries the transient and there is only ever ONE submit POST."""
    monkeypatch.setenv("ASR_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_asr_manifest(
        poll={"url": "https://api.example.com/v1/asr/{job_id}",
              "status_path": "$.data.status",
              "status_map": {"DONE": "succeeded", "RUNNING": "running"}},
    ))
    transport = ScriptedTransport([
        _resp({"data": {"task_id": "t1"}}),                 # submit ok (async)
        HttpResponse(429, {}, b'{"error":"slow down"}'),    # poll 429 → retried
        _resp({"data": {"status": "DONE", "segments": [
            {"begin": 0.0, "end": 1.0, "text": "hi"}]}}),   # poll DONE
    ])
    provider = GenericAsrProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")
    segs = provider.transcribe(media)
    assert [s.text for s in segs] == ["hi"]
    posts = [r for r in transport.requests if r[0] == "POST"]
    assert len(posts) == 1  # the paid submit happened exactly once


def test_asr_non_429_error_stays_provider_error(monkeypatch, tmp_path):
    """F4 boundary: a non-429 error status still classifies as provider_error."""
    monkeypatch.setenv("ASR_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_asr_manifest())
    transport = ScriptedTransport([HttpResponse(500, {}, b'{"error":"boom"}')])
    provider = GenericAsrProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")
    with pytest.raises(ProviderFailure) as exc:
        provider.transcribe(media)
    assert exc.value.kind is FailureKind.provider_error


def test_malformed_segment_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("ASR_X_KEY", "k")
    manifest = ProviderManifest.model_validate(_asr_manifest())
    transport = ScriptedTransport([_resp({"data": {"segments": [{"oops": 1}]}})])
    provider = GenericAsrProvider(manifest, transport=transport, sleep_fn=lambda s: None)
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")
    with pytest.raises(ProviderFailure) as exc:
        provider.transcribe(media)
    assert "asr.text_key" in str(exc.value)


def test_asr_slot_discovery(monkeypatch, tmp_path):
    """ASR manifests are found by get_asr_provider but NEVER enter the shot
    generation registry (an ASR engine is not a fallback for a missing shot)."""
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    monkeypatch.setenv("ASR_X_KEY", "k")
    write_yaml(tmp_path / "asr_x" / "provider.yaml", _asr_manifest())

    from manju.providers.registry import available_providers, manifest_errors

    assert get_asr_provider().id == "asr_x"
    assert get_asr_provider("asr_x").id == "asr_x"
    assert "asr_x" not in available_providers()
    assert manifest_errors() == []


def test_no_asr_configured_lists_onramps(monkeypatch, tmp_path):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "empty"))
    with pytest.raises(AsrUnavailable) as exc:
        get_asr_provider()
    message = str(exc.value)
    assert "--from-srt" in message and "--text" in message and "generic_asr" in message


# ----------------------------------------------------------- CLI on-ramps


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_cli_transcribe_manual_paths(tmp_project, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from manju.cli import app

    clip = tmp_project.imports_dir / "interview.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-shortest", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()

    srt_file = tmp_path / "mine.srt"
    srt_file.write_text(SRT_SAMPLE, encoding="utf-8")
    result = runner.invoke(app, ["transcribe", "media/imports/interview.mp4",
                                 "--from-srt", str(srt_file), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["segments"] == 2 and payload["source"] == "manual_srt"
    assert (tmp_project.captions_dir / "transcripts" / "interview.srt").exists()

    result = runner.invoke(app, ["transcribe", "media/imports/interview.mp4",
                                 "--text", "第一句话。第二句话!", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["source"] == "manual_text"

    # no provider configured and no manual input -> actionable error
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "none"))
    result = runner.invoke(app, ["transcribe", "media/imports/interview.mp4"])
    assert result.exit_code == 1


def test_cli_transcribe_refuses_media_outside_project(tmp_project, monkeypatch, tmp_path):
    """Goal 52: an absolute (or ../-escaping) media path is refused — cloud ASR
    would otherwise upload arbitrary local files to an external provider."""
    from typer.testing import CliRunner

    from manju.cli import app

    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"RIFFfake")
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()

    result = runner.invoke(app, ["transcribe", str(outside), "--text", "hello"])
    assert result.exit_code == 1
    assert "manju import" in result.output and "containment" in result.output.lower()

    # a ../ escape from inside the project is refused the same way
    result2 = runner.invoke(app, ["transcribe", f"../{outside.name}", "--text", "hello"])
    assert result2.exit_code == 1


def test_cli_transcribe_cloud_asr_spend_gated(tmp_project, monkeypatch, tmp_path):
    """Goal 52: the cloud ASR on-ramp goes through the SAME §8.3 ask_before
    gate build/redo/voice enforce — a priced call without --yes stops as
    waiting_user and never reaches the provider."""
    from typer.testing import CliRunner

    from manju.cli import app
    from manju.core.yamlio import write_yaml

    media = tmp_project.imports_dir / "clip.wav"
    media.write_bytes(b"RIFFfake")

    write_yaml(tmp_path / "asr_x" / "provider.yaml", {
        "id": "asr_x", "type": "asr", "adapter": "generic_asr",
        "auth": {"key_env": "ASR_X_KEY"},
        "submit": {"url": "https://api.example.com/v1/asr",
                  "body_template": {"audio": "{audio_b64}"}, "job_id_path": "$.id"},
        "asr": {"segments_path": "$.data.segments"},
        "cost": {"per_call": 3.0, "currency": "CNY"},
    })
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    monkeypatch.setenv("ASR_X_KEY", "k")
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()

    result = runner.invoke(app, ["transcribe", "media/imports/clip.wav"])
    assert result.exit_code == 1
    assert "waiting_user" in result.output
    # nothing was written — the gate stopped it before any network call
    assert not (tmp_project.captions_dir / "transcripts" / "clip.srt").exists()
