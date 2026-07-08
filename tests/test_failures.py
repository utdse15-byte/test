"""Make EVERY failure debuggable (goal 10).

The contract under test: when any step fails, a structured record lands in
``reports/failures.jsonl`` — what step, on what subject, the one-line cause, a
short verbatim evidence tail, one actionable hint, where the fuller log is — and
the same record is mirrored to the collaboration log. Degradations record in the
SAME shape at ``level="info"``. Every wired path (ffmpeg / cloud provider incl.
moderation / ComfyUI / local_cmd / a build fallback) is exercised end to end,
plus the surfaces: ``manju failures``, the ``status`` line, ``build --json``.
"""

from __future__ import annotations

import json
import shutil

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.events import tail_events
from manju.core.failures import (
    Failure,
    failures_since_last_build,
    read_failures,
    record_failure,
)
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.generic_cloud import GenericCloudProvider, HttpResponse
from manju.providers.manifest import COMFYUI_ADAPTER, ProviderManifest

runner = CliRunner()
_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

FAILURES_REL = "reports/failures.jsonl"
# The frozen record schema the wave-2 GUI renders from — asserted verbatim so a
# field rename here is a loud, deliberate break, never a silent one.
SCHEMA_KEYS = {
    "id", "ts", "level", "step", "subject", "cause",
    "evidence", "hint", "log_path", "actor", "detail",
}


def _read_lines(project):
    path = project.root / FAILURES_REL
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ------------------------------------------------------------------ core record


def test_record_failure_is_wellformed_and_mirrored_to_events(tmp_project):
    rec = record_failure(tmp_project, Failure(
        step="render", subject="final", cause="ffmpeg exited 1",
        evidence="$ ffmpeg -i a.mp4 …\nInvalid argument", hint="核对滤镜",
        log_path=".manju/logs/render.log",
    ))
    # the returned record carries the full, frozen schema
    assert set(rec) == SCHEMA_KEYS
    assert rec["step"] == "render" and rec["subject"] == "final"
    assert rec["level"] == "error"
    assert rec["id"].startswith("F-") and rec["ts"]  # assigned on record

    # it is the same object persisted to reports/failures.jsonl
    on_disk = _read_lines(tmp_project)
    assert len(on_disk) == 1 and on_disk[0] == rec

    # and mirrored to the collaboration log as action="failure" (§3, §10)
    evs = [e for e in tail_events(tmp_project.root, 20) if e.get("action") == "failure"]
    assert len(evs) == 1
    assert evs[0]["detail"]["step"] == "render"
    assert evs[0]["detail"]["subject"] == "final"
    assert evs[0]["detail"]["cause"] == "ffmpeg exited 1"


def test_unknown_step_normalises_and_info_level_kept(tmp_project):
    rec = record_failure(tmp_project, Failure(step="bogus", subject="x",
                                              cause="c", level="info"))
    # round W (#50): an unrecognized step reads as "unknown" — never silently
    # coerced to "generate" (a typo in a caller must not misattribute).
    assert rec["step"] == "unknown"
    assert rec["level"] == "info"


def test_unknown_step_is_a_real_step_not_a_crash(tmp_project):
    """A caller typo ("reneder", "qcc", ...) — or a bare empty string — must
    normalize to "unknown", stay queryable (read_failures/by-step filters),
    and never be attributed to a step that did not actually run (#50)."""
    for bad in ("reneder", "qcc", "", "  ", "GENERATE_TYPO"):
        rec = record_failure(tmp_project, Failure(step=bad, subject="x", cause="c"))
        assert rec["step"] == "unknown"
    recent = read_failures(tmp_project, n=10)
    assert all(r["step"] == "unknown" for r in recent)
    assert len(recent) == 5


def test_record_failure_rotates_at_threshold(tmp_project, monkeypatch):
    # tiny threshold so a couple of records force a rotation
    monkeypatch.setattr("manju.core.failures.ROTATE_BYTES", 200)
    for i in range(6):
        record_failure(tmp_project, Failure(step="generate", subject=f"S{i:03d}",
                                            cause="x" * 80))
    reports = tmp_project.root / "reports"
    rotated = list(reports.glob("failures.*.jsonl"))
    assert rotated, "expected at least one rotated slice"
    # the active file still exists and holds only the most recent record(s)
    active = _read_lines(tmp_project)
    assert active and active[-1]["subject"] == "S005"
    # nothing was lost: rotated + active together cover all six subjects
    seen = {r["subject"] for r in active}
    for rf in rotated:
        seen |= {json.loads(ln)["subject"]
                 for ln in rf.read_text(encoding="utf-8").splitlines() if ln.strip()}
    assert seen == {f"S{i:03d}" for i in range(6)}


# --------------------------------------------------------- #50: cross-process append


def _mp_failure_worker(root: str, subject: str) -> None:
    """Top-level (picklable) worker for the multiprocessing race test below:
    each OS process appends ONE failure record to the SAME project's ledger,
    all fired together (round W, #50)."""
    from manju.core.failures import Failure, record_failure

    record_failure(root, Failure(step="render", subject=subject, cause="race"))


def test_record_failure_concurrent_processes_no_interleaved_or_lost_lines(tmp_project):
    """Real OS processes (not threads) racing `record_failure` on the SAME
    reports/failures.jsonl must not lose a line or interleave two lines into
    one unparseable one (round W, #50). Without the cross-process lock around
    rotate-check + append, two processes' writes can interleave at the OS
    buffer level or race the rotation boundary; with it, every line must be
    present, distinct, and independently valid JSON."""
    import multiprocessing

    n = 12
    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_mp_failure_worker, args=(str(tmp_project.root), f"S{i:03d}"))
        for i in range(n)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, f"worker process failed (exitcode={p.exitcode})"

    lines = _read_lines(tmp_project)  # raises on any torn/interleaved JSON line
    assert len(lines) == n
    assert {r["subject"] for r in lines} == {f"S{i:03d}" for i in range(n)}


def test_read_failures_newest_first_and_level_filter(tmp_project):
    record_failure(tmp_project, Failure(step="generate", subject="A", cause="a"))
    record_failure(tmp_project, Failure(step="qc", subject="B", cause="b", level="info"))
    record_failure(tmp_project, Failure(step="render", subject="C", cause="c"))
    newest = read_failures(tmp_project, 10)
    assert [r["subject"] for r in newest] == ["C", "B", "A"]  # newest first
    errs = read_failures(tmp_project, 10, level="error")
    assert [r["subject"] for r in errs] == ["C", "A"]  # info B filtered out


def test_failures_since_last_build_counts_only_after_green_build(tmp_project):
    from manju.core.events import append_event

    record_failure(tmp_project, Failure(step="render", subject="OLD", cause="old"))
    # a successful build draws the line
    append_event(tmp_project.root, "engine", "build", {"target": "final", "ok": True})
    record_failure(tmp_project, Failure(step="render", subject="NEW", cause="new"))
    record_failure(tmp_project, Failure(step="qc", subject="DEG", cause="d", level="info"))
    since = failures_since_last_build(tmp_project)
    subjects = {r["subject"] for r in since}
    assert "NEW" in subjects        # after the green build → counted
    assert "OLD" not in subjects    # before it → not counted
    assert "DEG" not in subjects    # info degradations never count as failures


# ------------------------------------------------------------------- ffmpeg path


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required")
def test_run_ffmpeg_bad_filter_records_failure(tmp_project):
    from manju.media.ffmpeg import MediaError, run_ffmpeg

    out = tmp_project.root / "renders" / "x.mp4"
    with pytest.raises(MediaError):
        run_ffmpeg(
            ["-f", "lavfi", "-i", "color=c=red:size=64x64:d=1",
             "-vf", "definitelynotafilter", str(out)],
            project=tmp_project.root, subject="final",
        )
    recs = read_failures(tmp_project, 5)
    assert len(recs) == 1
    r = recs[0]
    assert r["step"] == "render" and r["subject"] == "final"
    assert "ffmpeg exited" in r["cause"]
    # evidence carries BOTH the argv head and the ffmpeg stderr tail
    assert "$ ffmpeg" in r["evidence"] and "-vf" in r["evidence"]
    assert r["log_path"] == ".manju/logs/render.log"


def test_run_ffmpeg_without_project_records_nothing(tmp_project, monkeypatch):
    # the recording capability is opt-in: existing callers (no project) are byte
    # identical — same exception, no record written.
    from manju.media.ffmpeg import MediaError, run_ffmpeg

    monkeypatch.setattr("subprocess.run", lambda *a, **k: _FakeProc(1, "boom"))
    with pytest.raises(MediaError):
        run_ffmpeg(["-i", "nope"])
    assert not (tmp_project.root / FAILURES_REL).exists()


class _FakeProc:
    def __init__(self, rc, stderr):
        self.returncode = rc
        self.stderr = stderr


# ------------------------------------------------------- cloud provider (§8.1)


def _cloud_manifest(**overrides):
    base = {
        "id": "video_x",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "auth": {"key_env": "VIDEO_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/videos",
            "body_template": {"prompt": "{prompt}"},
            "job_id_path": "$.data.task_id",
        },
        "poll": {
            "url": "https://api.example.com/v1/videos/{job_id}",
            "status_path": "$.data.status",
            "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed",
                           "PROCESSING": "running"},
            "result_url_path": "$.data.video_url",
        },
        "failure": {"content_rejected_when": ["contentPolicy", "risk_control"]},
        "limits": {"rate_limit_per_min": 0},
        "cost": {"per_call": 0.5, "currency": "CNY"},
    }
    base.update(overrides)
    return ProviderManifest.model_validate(base)


class _Scripted:
    def __init__(self, script):
        self.script = list(script)

    def __call__(self, method, url, headers, body):
        if not self.script:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self.script.pop(0)


def _resp(status, payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(status, {}, body)


def _req(tmp_project, add_shot, shot_id):
    shot = add_shot(tmp_project, shot_id, generation={"candidates": 1})
    return GenerationRequest(
        project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
        spec_hash="sha256:test", duration_ms=3000, candidates=1, params={"seed": 1},
    )


def test_moderation_rejection_records_failure_and_links_ledger(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    provider = GenericCloudProvider(
        _cloud_manifest(),
        transport=_Scripted([
            _resp(200, {"data": {"task_id": "job_9"}}),
            _resp(200, {"data": {"status": "FAILED",
                                 "message": "blocked by contentPolicy: 悬疑内容"}}),
        ]),
        sleep_fn=lambda s: None,
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, add_shot, "S002"))
    assert exc.value.kind is FailureKind.content_rejected

    recs = read_failures(tmp_project, 5)
    assert len(recs) == 1
    r = recs[0]
    assert r["step"] == "generate" and r["subject"] == "S002"
    assert r["level"] == "error"
    assert r["detail"]["failure_kind"] == "content_rejected"
    assert r["detail"]["provider"] == "video_x"
    assert "contentPolicy" in r["evidence"]  # the rejection reason is the evidence
    assert r["hint"]  # a first-class 审核拒绝 hint (rewrite / switch provider)

    # the ledger row cross-references the same failure record (reason ↔ record)
    from manju.runtime.state import RuntimeState

    with RuntimeState(tmp_project.root) as state:
        row = state.run_log(1)[0]
    assert row["status"] == "failed" and row["failure_kind"] == "content_rejected"
    assert row["failure_id"] == r["id"]


def test_http_error_records_status_in_evidence(tmp_project, add_shot, monkeypatch):
    monkeypatch.setenv("VIDEO_X_KEY", "k")
    provider = GenericCloudProvider(
        _cloud_manifest(),
        transport=_Scripted([_resp(503, {"error": "upstream exploded"})]),
        sleep_fn=lambda s: None,
    )
    with pytest.raises(ProviderFailure):
        provider.generate(_req(tmp_project, add_shot, "S003"))
    r = read_failures(tmp_project, 5)[0]
    assert r["step"] == "generate" and r["subject"] == "S003"
    assert "HTTP 503" in r["evidence"] and "upstream exploded" in r["evidence"]


def test_missing_key_records_invalid_failure(tmp_project, add_shot, monkeypatch):
    monkeypatch.delenv("VIDEO_X_KEY", raising=False)
    provider = GenericCloudProvider(_cloud_manifest(), transport=_Scripted([]),
                                    sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure):
        provider.generate(_req(tmp_project, add_shot, "S004"))
    r = read_failures(tmp_project, 5)[0]
    assert r["detail"]["failure_kind"] == "invalid"
    assert "VIDEO_X_KEY" in r["cause"]


# ------------------------------------------------------------------- ComfyUI


def test_comfyui_missing_workflow_records_failure(tmp_project, add_shot):
    from manju.providers.comfyui import ComfyUIProvider

    manifest = ProviderManifest.model_validate({
        "id": "comfyui", "type": "video", "adapter": COMFYUI_ADAPTER,
        "capabilities": ["image_to_video"], "cost": {"per_call": 0.0, "currency": "CNY"},
        "comfyui": {"base_url": "http://127.0.0.1:9999",
                    "workflow_file": "comfyui/missing.json", "input_map": {}},
    })
    provider = ComfyUIProvider(manifest, sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure):
        provider.generate(_req(tmp_project, add_shot, "S005"))
    r = read_failures(tmp_project, 5)[0]
    assert r["step"] == "generate" and r["subject"] == "S005"
    assert r["detail"]["provider"] == "comfyui"
    assert "Save (API Format)" in r["hint"]  # actionable, names the ComfyUI action


# ------------------------------------------------------------------ local_cmd


def test_local_cmd_missing_binary_records_failure(tmp_project, add_shot):
    from manju.providers.local_cmd import LocalCommandProvider

    manifest = ProviderManifest.model_validate({
        "id": "svd_local", "type": "video", "adapter": "manju.providers.local_cmd:LocalCommandProvider",
        "capabilities": ["image_to_video"], "cost": {"per_call": 0.0, "currency": "CNY"},
        "local_cmd": {"command": "manju-no-such-binary-xyz --out {out}", "timeout_s": 5.0},
    })
    provider = LocalCommandProvider(manifest)
    with pytest.raises(ProviderFailure):
        provider.generate(_req(tmp_project, add_shot, "S006"))
    r = read_failures(tmp_project, 5)[0]
    assert r["step"] == "generate" and r["subject"] == "S006"
    assert r["detail"]["failure_kind"] == "invalid"
    assert "PATH" in r["hint"] or "local_cmd.command" in r["hint"]


# ------------------------------------------------------------ build degradation


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required (caption_card renders)")
def test_build_records_degradation_when_fallback_fires(tmp_project, add_shot):
    from manju.build.graph import run_build

    # preferred provider does not exist → the chain degrades to caption_card,
    # which always produces something offline (§8.4). That degradation is the
    # "why did this shot become a caption card?" record.
    add_shot(tmp_project, "S001", generation={"provider": "nope", "candidates": 1})
    result = run_build(tmp_project, target="final", assume_yes=True)
    assert result.ok, result.errors

    degradations = [r for r in read_failures(tmp_project, 20) if r["level"] == "info"]
    gen_deg = [r for r in degradations if r["step"] == "generate" and r["subject"] == "S001"]
    assert gen_deg, "expected a generate degradation record for the fallback"
    assert "降级" in gen_deg[0]["cause"]

    # the AI path sees the same summaries on the BuildResult
    assert any(f["subject"] == "S001" and f["level"] == "info" and f["step"] == "generate"
               for f in result.failures)


# ----------------------------------------------------------------- CLI surfaces


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


def test_cli_failures_renders_rustc_style(in_project):
    record_failure(in_project, Failure(
        step="render", subject="final", cause="ffmpeg exited 1",
        evidence="$ ffmpeg -i a.mp4 …\nInvalid argument", hint="核对滤镜/输入路径",
        log_path=".manju/logs/render.log",
    ))
    result = runner.invoke(app, ["failures"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "render" in out and "final" in out          # step · subject header
    assert "ffmpeg exited 1" in out                     # cause
    assert "Invalid argument" in out                    # indented evidence
    assert "help:" in out and "核对滤镜" in out          # actionable hint
    assert ".manju/logs/render.log" in out              # where the log lives


def test_cli_failures_json_shape(in_project):
    record_failure(in_project, Failure(step="generate", subject="S001", cause="c"))
    result = runner.invoke(app, ["failures", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["shown"] == 1
    assert set(data["failures"][0]) == SCHEMA_KEYS


def test_cli_failures_empty(in_project):
    result = runner.invoke(app, ["failures", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output) == {"failures": [], "shown": 0}


def test_status_line_and_json_reflect_recent_failures(in_project):
    from manju.core.events import append_event

    append_event(in_project.root, "engine", "build", {"target": "final", "ok": True})
    record_failure(in_project, Failure(step="render", subject="final", cause="boom"))

    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert data["failures_since_build"] == 1

    text = runner.invoke(app, ["status"]).output
    assert "最近失败" in text and "manju failures" in text
