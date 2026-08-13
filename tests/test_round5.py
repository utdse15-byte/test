"""Round 5: the last v2.2 gaps — html_render (P1), import previews (§11),
captions manual takeover (§3), schema export, and the round's bug fixes."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.media.html_card import html_available

has_ffmpeg = shutil.which("ffmpeg") is not None
has_chromium = html_available()

# --------------------------------------------------------------- html_render


@pytest.mark.skipif(not (has_ffmpeg and has_chromium), reason="chromium + ffmpeg required")
def test_html_card_video(tmp_path):
    from manju.media.html_card import html_card_video
    from manju.media.probe import probe

    out = html_card_video("硬币年份 2036", tmp_path / "card.mp4",
                          width=540, height=960, fps=24, duration_ms=1500)
    info = probe(out)
    assert (info.width, info.height) == (540, 960)
    assert info.has_audio
    assert abs(info.duration_ms - 1500) < 200


@pytest.mark.skipif(not (has_ffmpeg and has_chromium), reason="chromium + ffmpeg required")
def test_caption_card_prefers_html(tmp_project, add_shot):
    """M3: caption_card 优先用 HTML 渲染实现;lineage records the renderer."""
    from manju.build.graph import run_build

    add_shot(tmp_project, "S001", duration=1.5,
             dialogue={"speaker": "linxia", "text": "这不可能。"})
    assert run_build(tmp_project, target="qc").ok
    take = tmp_project.takes("S001")[0]
    assert take.sidecar.params.get("renderer") == "html"


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg required")
def test_caption_card_drawtext_fallback(tmp_project, add_shot):
    """Adapter wall: renderer=drawtext forced (and used when no Chromium)."""
    from manju.providers.base import GenerationRequest
    from manju.providers.registry import get_provider

    shot = add_shot(tmp_project, "S001", duration=1.5,
                    generation={"params": {"renderer": "drawtext"}})
    req = GenerationRequest(
        project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
        spec_hash="sha256:x", duration_ms=1200, candidates=1,
        params={"renderer": "drawtext"},
    )
    takes = get_provider("caption_card").generate(req)
    assert takes[0].sidecar.params.get("renderer") == "drawtext"
    assert takes[0].media_path and takes[0].media_path.exists()


# ------------------------------------------------------------ import previews


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg required")
def test_import_generates_previews(tmp_project, monkeypatch, tmp_path):
    clip = tmp_path / "素材.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=1",
         "-pix_fmt", "yuv420p", str(clip)], check=True)
    wav = tmp_path / "bgm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=220:duration=1", str(wav)], check=True)

    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["import", str(clip), str(wav), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["imported"]) == 2
    previews = payload["previews"]
    assert any(v.endswith(".jpg") for v in previews.values())      # video poster
    assert any(v.endswith("_wave.png") for v in previews.values())  # waveform
    for v in previews.values():
        assert (tmp_project.root / v).exists()
        assert v.startswith(".manju/")  # derived -> disposable runtime dir (§3)


# ------------------------------------------------- captions manual takeover


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg required")
def test_captions_manual_mode(tmp_project, add_shot):
    """§3: captions.srt 可手改并标记 manual — human SRT survives a rebuild and
    the burned ASS is recompiled from the human cues."""
    from manju.build.graph import run_build

    add_shot(tmp_project, "S001", duration=2.0,
             dialogue={"speaker": "linxia", "text": "原始台词。"})
    assert run_build(tmp_project, target="qc").ok
    srt = tmp_project.captions_dir / "captions.srt"
    assert "原始台词" in srt.read_text(encoding="utf-8")

    # human takes over the captions
    srt.write_text("1\n00:00:00,000 --> 00:00:01,500\n人工改过的字幕\n",
                   encoding="utf-8")
    rules = tmp_project.load_rules()
    rules.captions.mode = "manual"
    tmp_project.save_rules(rules)

    assert run_build(tmp_project, target="qc").ok
    assert "人工改过的字幕" in srt.read_text(encoding="utf-8")  # untouched
    ass = (tmp_project.captions_dir / "captions.ass").read_text(encoding="utf-8")
    assert "人工改过的字幕" in ass and "原始台词" not in ass  # ASS follows the human
    generated = tmp_project.captions_dir / "captions.generated.srt"
    assert "原始台词" in generated.read_text(encoding="utf-8")  # diff target


# --------------------------------------------------------- schema + bugfixes


def test_schema_command(tmp_path, monkeypatch, tmp_project):
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["schema", "--out", str(tmp_path / "schemas")])
    assert result.exit_code == 0
    files = sorted(p.name for p in (tmp_path / "schemas").glob("*.schema.json"))
    assert "shot.schema.json" in files and "timeline.schema.json" in files
    shot_schema = json.loads((tmp_path / "schemas" / "shot.schema.json").read_text(encoding="utf-8"))
    assert "properties" in shot_schema


def test_qc_command_prints_report_path(tmp_project, add_shot, make_take, monkeypatch):
    """Regression: the qc command used paths['md'] against qc_md keys and the
    report path never printed."""
    from manju.core.spec import compute_spec_hash

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["qc"])
    assert "reports/qc.md" in result.output


def test_unlock_normalizes_list_locks(tmp_project, add_shot, monkeypatch):
    """Regression: unlock on a hand-written list-form lock silently did nothing."""
    from manju.core.yamlio import read_yaml, write_yaml

    add_shot(tmp_project, "S001")
    path = tmp_project.shot_path("S001")
    data = read_yaml(path)
    data["locked"] = ["dialogue.text", "duration"]  # unsealed hand-written form
    write_yaml(path, data)

    monkeypatch.chdir(tmp_project.root)
    monkeypatch.setattr("manju.cli._interactive", lambda: True)
    result = CliRunner().invoke(app, ["unlock", "S001", "dialogue.text"], input="y\n")
    assert result.exit_code == 0, result.output
    locked = tmp_project.load_shot_raw("S001")["locked"]
    assert "dialogue.text" not in locked and "duration" in locked


def test_status_surfaces_pending_jobs(tmp_project):
    from manju.build.status import project_status
    from manju.runtime.state import RuntimeState

    with RuntimeState(tmp_project.root) as state:
        state.open_job("job_9", provider="video_x", shot="S001")
    info = project_status(tmp_project)
    assert info["run_log"]["pending_jobs"] == 1


def test_status_never_sums_across_currencies(tmp_project, add_shot, monkeypatch):
    """Goal 79: mixing 10 CNY + 2 USD must never render as a single
    currency-mislabeled number (e.g. "12 USD" just because the last take
    processed happened to be USD) — status groups per currency and only
    collapses to a single total_cost/currency when there is exactly one."""
    from manju.core.models import RemoteJobInfo, TakeSidecar
    from manju.build.status import project_status

    # force the sidecar fallback path (no ledger rows) so this exercises the
    # sidecar-derived currency grouping specifically.
    monkeypatch.setattr(
        "manju.runtime.state.RuntimeState.count_runs", lambda self: 0)

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    media1 = tmp_project.root / "m1.mp4"
    media1.write_bytes(b"x")
    media2 = tmp_project.root / "m2.mp4"
    media2.write_bytes(b"y")
    tmp_project.register_take(
        "S001", media1,
        TakeSidecar(provider="cloud_a", spec_hash="h",
                   remote=RemoteJobInfo(job_id="j1", cost=10.0, currency="CNY")))
    tmp_project.register_take(
        "S002", media2,
        TakeSidecar(provider="cloud_b", spec_hash="h",
                   remote=RemoteJobInfo(job_id="j2", cost=2.0, currency="USD")))

    info = project_status(tmp_project)
    assert info["currency"] is None  # never a specific (wrong) currency when mixed
    by_currency = {c["currency"]: c["cost"] for c in info["spend_by_currency"]}
    assert by_currency == {"CNY": 10.0, "USD": 2.0}
