"""P1 paid-safety ledger wave — the cloud/TTS/ASR submission + download path.

Six red-first findings, all on the owner's OWN ordinary path (no adversary):

* PROVIDER-SUBMISSION-001 — tts/asr fired the paid POST as the FIRST side
  effect, with no durable PREPARED record, so a network blip after the
  provider accepted the job let the next run silently re-submit and PAY AGAIN.
* PROVIDER-DOWNLOAD-001 — a 429/503 on the DOWNLOAD of an already-paid job was
  hardcoded terminal, reopening a submission (and a second charge) next build.
* PROVIDER-POLL-002 — ``Retry-After: 0``/negative/NaN collapsed to a 0.0 sleep
  (busy-spin to the 600s cap); the tts/asr backoff could raise OverflowError.
* PROVIDER-NET-001 — no redirect handler, so stdlib urllib copied the
  ``Authorization`` header to a redirect target and rewrote a paid POST to GET.
* PROVIDER-DOWNLOAD-002 — a 200 ``application/json`` error body was written to
  disk and registered as a SUCCESSFUL paid take.
* PROVIDER-PRIV-001 — full signed URLs (``X-Amz-Signature=…``) landed
  unscrubbed in failure ``detail`` / messages, i.e. in reports/failures.jsonl.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from manju.core.yamlio import write_yaml
from manju.providers.base import FailureKind, ProviderFailure
from manju.providers.generic_cloud import HttpResponse

WAV_HEADER = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
              b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

SIGNED = ("https://cdn.example.com/out.mp4?X-Amz-Signature=deadbeefcafe"
          "&X-Amz-Credential=AKIAsecret")


class ScriptedTransport:
    """Replays a scripted list of responses; records every request made."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[tuple] = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


# --------------------------------------------------------------- manifests


def _cloud_manifest(**overrides):
    base = {
        "id": "cloud_x",
        "type": "video",
        "adapter": "generic_cloud",
        "auth": {"key_env": "CLOUD_X_KEY", "header": "Authorization: Bearer {key}"},
        "submit": {"url": "https://api.example.com/v1/jobs",
                   "body_template": {"prompt": "{prompt}"},
                   "job_id_path": "$.id"},
        "poll": {"url": "https://api.example.com/v1/jobs/{job_id}",
                 "status_path": "$.state",
                 "status_map": {"done": "succeeded", "run": "running"},
                 "result_url_path": "$.url"},
        "cost": {"per_call": 0.5, "currency": "CNY"},
    }
    base.update(overrides)
    from manju.providers.manifest import ProviderManifest

    return ProviderManifest.model_validate(base)


def _tts_manifest(**overrides):
    base = {
        "id": "tts_x",
        "type": "tts",
        "adapter": "generic_tts",
        "auth": {"key_env": "TTS_X_KEY"},
        "submit": {"url": "https://api.example.com/v1/tts",
                   "body_template": {"text": "{text}"},
                   "job_id_path": "$.data.task_id"},
        "tts": {"audio_url_path": "$.data.audio_url", "audio_format": "wav"},
        "cost": {"per_call": 0.02, "currency": "CNY"},
    }
    base.update(overrides)
    return base


@pytest.fixture
def tts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "prov" / "tts_x" / "provider.yaml", _tts_manifest())


def _asr_manifest(**overrides):
    base = {
        "id": "asr_x",
        "type": "asr",
        "adapter": "generic_asr",
        "auth": {"key_env": "ASR_X_KEY"},
        "submit": {"url": "https://api.example.com/v1/asr",
                   "body_template": {"audio": "{audio_b64}"},
                   "job_id_path": "$.id"},
        "poll": {"url": "https://api.example.com/v1/asr/{job_id}",
                 "status_path": "$.state",
                 "status_map": {"done": "succeeded"}},
        "asr": {"segments_path": "$.segments", "time_unit": "s"},
        "cost": {"per_call": 0.01, "currency": "CNY"},
    }
    base.update(overrides)
    from manju.providers.manifest import ProviderManifest

    return ProviderManifest.model_validate(base)


# ============================================ PROVIDER-SUBMISSION-001 (tts)


def test_tts_records_prepared_evidence_before_the_paid_post(
        tmp_project, add_shot, tts_env, monkeypatch):
    """The PAID POST must not be the first side effect: a durable PREPARED /
    DISPATCHING record has to exist by the time the transport is entered."""
    from manju.providers.tts import get_tts_provider

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "这不可能。"})
    seen: dict = {}

    def transport(method, url, headers, body):
        events = tmp_project.root / "events.jsonl"
        seen["evidence"] = events.read_text(encoding="utf-8", errors="replace") \
            if events.exists() else ""
        return HttpResponse(200, {}, json.dumps(
            {"data": {"audio_url": "https://cdn.example.com/v.wav"}}).encode())

    provider = get_tts_provider(transport=ScriptedTransport([]), sleep_fn=lambda s: None)
    provider._transport = transport
    with pytest.raises(Exception):
        # the audio download response is not scripted; we only care that the
        # PREPARED evidence existed BEFORE the first (paid) transport call.
        provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert "submission_state" in seen.get("evidence", ""), (
        "no durable submission evidence existed before the paid TTS POST")
    assert "PREPARED" in seen["evidence"]


def test_tts_second_run_after_an_ambiguous_submit_refuses_to_pay_again(
        tmp_project, add_shot, tts_env):
    """A transport blip AFTER the provider may have accepted the job must leave
    an ambiguous submission that fail-closes the next run — never a silent
    second paid POST."""
    from manju.providers.tts import get_tts_provider

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "这不可能。"})
    blip = ProviderFailure(FailureKind.timeout, "network error calling …",
                           disposition="OUTCOME_UNKNOWN")
    t1 = ScriptedTransport([blip])
    p1 = get_tts_provider(transport=t1, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure):
        p1.synthesize(tmp_project, shot, tmp_project.load_bible())

    t2 = ScriptedTransport([
        HttpResponse(200, {}, json.dumps(
            {"data": {"audio_url": "https://cdn.example.com/v.wav"}}).encode()),
        HttpResponse(200, {}, WAV_HEADER),
    ])
    p2 = get_tts_provider(transport=t2, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as ei:
        p2.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert not t2.requests, "the re-run re-submitted (and paid) after an ambiguous outcome"
    assert "unknown" in str(ei.value).lower() or "submission" in str(ei.value).lower()


# ============================================ PROVIDER-SUBMISSION-001 (asr)


def test_asr_records_prepared_evidence_before_the_paid_post(tmp_project, monkeypatch):
    from manju.providers.asr import GenericAsrProvider

    media = tmp_project.root / "media" / "src" / "clip.wav"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(WAV_HEADER)
    monkeypatch.setenv("ASR_X_KEY", "k")
    seen: dict = {}

    def transport(method, url, headers, body):
        ev = tmp_project.root / "events.jsonl"
        seen["evidence"] = ev.read_text(encoding="utf-8", errors="replace") \
            if ev.exists() else ""
        return HttpResponse(200, {}, json.dumps(
            {"segments": [{"start": 0, "end": 1, "text": "喂"}]}).encode())

    provider = GenericAsrProvider(_asr_manifest(poll=None), sleep_fn=lambda s: None)
    provider._transport = transport
    provider.transcribe(media, project_root=tmp_project.root)
    assert "PREPARED" in seen.get("evidence", ""), (
        "no durable submission evidence existed before the paid ASR POST")


# =============================================== PROVIDER-DOWNLOAD-001


def test_download_429_of_a_paid_job_is_retryable_not_terminal(tmp_path, monkeypatch):
    """A rate-limited/5xx DOWNLOAD of an ALREADY PAID job must classify as
    retryable (rate_limited / timeout), never a terminal provider_error that
    reopens a fresh (second-charge) submission next build."""
    from manju.providers.generic_cloud import GenericCloudProvider

    monkeypatch.setenv("CLOUD_X_KEY", "k")
    t = ScriptedTransport([HttpResponse(429, {}, b"slow down")])
    p = GenericCloudProvider(_cloud_manifest(), transport=t, sleep_fn=lambda s: None)
    p._results["job1"] = {"result_url": "https://cdn.example.com/a.mp4"}
    with pytest.raises(ProviderFailure) as ei:
        p.download("job1", tmp_path)
    assert ei.value.kind is FailureKind.rate_limited, (
        f"download 429 classified as {ei.value.kind} — terminal for a paid job")


def test_download_keeps_its_poll_info_when_the_download_fails(tmp_path, monkeypatch):
    """The cached poll info (the result URL of a PAID job) must not be popped
    until the bytes are on disk — otherwise the retry has nothing to fetch."""
    from manju.providers.generic_cloud import GenericCloudProvider

    monkeypatch.setenv("CLOUD_X_KEY", "k")
    t = ScriptedTransport([HttpResponse(503, {}, b"nope"),
                           HttpResponse(200, {"Content-Type": "video/mp4"}, b"\x00\x00\x00 ftypmp42")])
    p = GenericCloudProvider(_cloud_manifest(), transport=t, sleep_fn=lambda s: None)
    p._results["job1"] = {"result_url": "https://cdn.example.com/a.mp4"}
    with pytest.raises(ProviderFailure):
        p.download("job1", tmp_path)
    assert "job1" in p._results, "poll info popped before the download succeeded"
    out = p.download("job1", tmp_path)
    assert out and out[0].exists()


# =================================================== PROVIDER-POLL-002


@pytest.mark.parametrize("raw", ["0", "-5", "nan", "  ", "0.0"])
def test_retry_after_never_collapses_to_a_zero_sleep(raw):
    from manju.providers.generic_cloud import retry_after_seconds

    got = retry_after_seconds({"Retry-After": raw}, 0.5)
    assert math.isfinite(got), f"Retry-After {raw!r} produced a non-finite sleep {got!r}"
    assert got >= 0.5, f"Retry-After {raw!r} busy-spins (slept {got})"
    assert got <= 60.0


def test_tts_poll_backoff_survives_a_high_round_count(monkeypatch, tmp_path):
    """The eagerly evaluated ``0.5 * (2 ** i)`` default overflows to a bare
    OverflowError at a high poll count — the exponent must be capped."""
    from manju.providers import tts as tts_mod

    # the two backoff expressions must not be able to overflow
    for i in (10, 2000, 100000):
        assert min(0.5 * (2 ** min(i, 64)), 8.0) == 8.0 or i < 5
    src = Path(tts_mod.__file__).read_text(encoding="utf-8")
    assert "2 ** i" not in src, "uncapped exponent still present in tts backoff"
    from manju.providers import asr as asr_mod

    src = Path(asr_mod.__file__).read_text(encoding="utf-8")
    assert "2 ** i" not in src, "uncapped exponent still present in asr backoff"


# ==================================================== PROVIDER-NET-001


def test_default_transport_installs_a_credential_dropping_redirect_handler():
    """stdlib urllib follows 30x and copies Authorization to the new host, and
    turns a paid POST into a GET. A handler must drop credentials cross-origin."""
    from manju.providers import generic_cloud as gc

    assert hasattr(gc, "CredentialSafeRedirectHandler"), \
        "no redirect handler installed — urllib leaks Authorization cross-origin"
    h = gc.CredentialSafeRedirectHandler()
    import urllib.request

    req = urllib.request.Request(
        "https://api.example.com/v1/jobs", method="GET",
        headers={"Authorization": "Bearer secret", "X-Tenant": "t1"})
    new = h.redirect_request(req, None, 302, "Found",
                             {"location": "https://evil.example.net/x"},
                             "https://evil.example.net/x")
    assert new is not None
    lowered = {k.lower(): v for k, v in new.header_items()}
    assert "authorization" not in lowered, "Authorization copied to another origin"
    assert "x-tenant" not in lowered, "tenant header copied to another origin"


def test_a_paid_post_is_never_silently_replayed_as_a_redirected_get():
    """stdlib turns a 302 on a POST into a credential-less GET of the new URL —
    a paid submit must refuse instead of being re-issued blind."""
    from manju.providers import generic_cloud as gc
    import urllib.request

    h = gc.CredentialSafeRedirectHandler()
    req = urllib.request.Request(
        "https://api.example.com/v1/jobs", data=b"{}", method="POST",
        headers={"Authorization": "Bearer secret"})
    new = h.redirect_request(req, None, 302, "Found",
                             {"location": "https://api.example.com/v2/jobs"},
                             "https://api.example.com/v2/jobs")
    assert new is None, "a paid POST was rewritten into a redirected GET"


def test_same_origin_redirect_keeps_credentials():
    from manju.providers import generic_cloud as gc
    import urllib.request

    h = gc.CredentialSafeRedirectHandler()
    req = urllib.request.Request(
        "https://api.example.com/v1/jobs", method="GET",
        headers={"Authorization": "Bearer secret"})
    new = h.redirect_request(req, None, 302, "Found",
                             {"location": "https://api.example.com/v2/jobs"},
                             "https://api.example.com/v2/jobs")
    lowered = {k.lower(): v for k, v in new.header_items()}
    assert lowered.get("authorization") == "Bearer secret"


# =============================================== PROVIDER-DOWNLOAD-002


def test_a_200_json_error_body_is_never_written_as_a_paid_take(tmp_path, monkeypatch):
    from manju.providers.generic_cloud import GenericCloudProvider

    monkeypatch.setenv("CLOUD_X_KEY", "k")
    body = json.dumps({"error": "signature expired"}).encode()
    t = ScriptedTransport([HttpResponse(200, {"Content-Type": "application/json"}, body)])
    p = GenericCloudProvider(_cloud_manifest(), transport=t, sleep_fn=lambda s: None)
    p._results["job1"] = {"result_url": "https://cdn.example.com/a.mp4"}
    with pytest.raises(ProviderFailure):
        p.download("job1", tmp_path)
    assert not list(Path(tmp_path).glob("result*")), \
        "a JSON error body was written to disk as the paid take"


def test_a_json_magic_byte_body_labelled_as_video_is_still_refused(tmp_path, monkeypatch):
    from manju.providers.generic_cloud import GenericCloudProvider

    monkeypatch.setenv("CLOUD_X_KEY", "k")
    t = ScriptedTransport([HttpResponse(
        200, {"Content-Type": "video/mp4"}, b'{"error": "expired"}')])
    p = GenericCloudProvider(_cloud_manifest(), transport=t, sleep_fn=lambda s: None)
    p._results["job1"] = {"result_url": "https://cdn.example.com/a.mp4"}
    with pytest.raises(ProviderFailure):
        p.download("job1", tmp_path)


# ==================================================== PROVIDER-PRIV-001


def test_download_failure_never_leaks_the_signed_url_query(tmp_path, monkeypatch):
    from manju.providers.generic_cloud import GenericCloudProvider

    monkeypatch.setenv("CLOUD_X_KEY", "k")
    t = ScriptedTransport([HttpResponse(404, {}, b"gone")])
    p = GenericCloudProvider(_cloud_manifest(), transport=t, sleep_fn=lambda s: None)
    p._results["job1"] = {"result_url": SIGNED}
    with pytest.raises(ProviderFailure) as ei:
        p.download("job1", tmp_path)
    blob = str(ei.value) + json.dumps(ei.value.detail, ensure_ascii=False)
    assert "X-Amz-Signature" not in blob, f"signed URL leaked into evidence: {blob}"


def test_html_error_page_rejection_never_leaks_the_signed_url(tmp_path, monkeypatch):
    from manju.providers.generic_cloud import GenericCloudProvider

    monkeypatch.setenv("CLOUD_X_KEY", "k")
    t = ScriptedTransport([HttpResponse(200, {"Content-Type": "text/html"},
                                        b"<html>expired</html>")])
    p = GenericCloudProvider(_cloud_manifest(), transport=t, sleep_fn=lambda s: None)
    p._results["job1"] = {"result_url": SIGNED}
    with pytest.raises(ProviderFailure) as ei:
        p.download("job1", tmp_path)
    blob = str(ei.value) + json.dumps(ei.value.detail, ensure_ascii=False)
    assert "X-Amz-Signature" not in blob, f"signed URL leaked into evidence: {blob}"


def test_tts_audio_download_failure_never_leaks_the_signed_url(tmp_path, tts_env):
    from manju.providers.tts import get_tts_provider

    t = ScriptedTransport([HttpResponse(500, {}, b"boom")])
    p = get_tts_provider(transport=t, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as ei:
        p._audio_from({"data": {"audio_url": SIGNED}}, dest_dir=tmp_path)
    blob = str(ei.value) + json.dumps(ei.value.detail, ensure_ascii=False)
    assert "X-Amz-Signature" not in blob, f"signed URL leaked into evidence: {blob}"
