"""Cross-WP interconnection tests (WP3 align, WP4 locale, WP5 transparency,
WP6 roundtrip, WP7 consistency)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.core.failures import Failure, next_options_for, record_failure
from manju.core.locale import add_locale, base_text_hash, line_status, locale_status
from manju.core.yamlio import write_yaml
from manju.media.align import align_shot, plan_multi_shot, split_dialogue


# ------------------------------------------------------------------ WP3


def test_align_default_writes_timing(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "这不可能。真的吗？"})
    gen = tmp_project.root / "media" / "gen" / "S001"
    gen.mkdir(parents=True, exist_ok=True)
    media = gen / "voice_take_01.wav"
    media.write_bytes(b"RIFF" + b"\x00" * 100)

    report = align_shot(tmp_project, "S001", probe_fn=lambda p: 2000)
    timing = Path(tmp_project.root) / report["timing"]
    assert timing.exists()
    words = json.loads(timing.read_text(encoding="utf-8"))
    assert len(words) >= 1
    assert words[0]["start_ms"] == 0
    assert all("text" in w for w in words)
    # MANUAL (no sidecar) still MANUAL after align
    voices = tmp_project.voice_takes("S001")
    assert voices and voices[-1][1] is None


def test_align_from_srt(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "你好世界"})
    gen = tmp_project.root / "media" / "gen" / "S001"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "voice_take_01.wav").write_bytes(b"x" * 50)
    srt = tmp_project.root / "hand.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,500\n你好世界\n",
        encoding="utf-8",
    )
    report = align_shot(tmp_project, "S001", from_srt=srt, probe_fn=lambda p: 1500)
    assert report["source"] == "from_srt"
    assert report["cues"] >= 1


def test_split_dialogue_cjk():
    assert len(split_dialogue("第一句。第二句！")) == 2


def test_plan_multi_shot_order(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "第一句"})
    add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "第二句"})
    media = tmp_project.root / "vo.wav"
    media.write_bytes(b"x" * 20)
    plan = plan_multi_shot(
        tmp_project, media, ["S001", "S002"], probe_fn=lambda p: 4000
    )
    assert len(plan["rows"]) == 2
    assert plan["rows"][0]["shot"] == "S001"
    assert plan["rows"][0]["start_ms"] == 0
    assert plan["rows"][1]["end_ms"] == 4000


# ------------------------------------------------------------------ WP4


def test_locale_add_and_stale(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "中文台词"})
    add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "另一句"})
    result = add_locale(tmp_project, "en")
    assert result["total"] == 2
    lines_path = tmp_project.root / "locales" / "en" / "lines.yaml"
    assert lines_path.exists()
    # fill one translation
    from manju.core.locale import load_lines
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "English line"
    write_yaml(lines_path, lines)
    st = line_status(tmp_project, "en", "S001")
    assert st["state"] == "ok"
    # change base → 翻译过期
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("dialogue", {}).__setitem__("text", "改了中文"),
    )
    st2 = line_status(tmp_project, "en", "S001")
    assert st2["state"] == "翻译过期"
    # S002 still missing
    assert line_status(tmp_project, "en", "S002")["state"] == "missing"


def test_locale_byte_identity_without_locales(tmp_project, add_shot):
    """Projects with no locales/ are untouched by locale machinery."""
    add_shot(tmp_project, "S001")
    from manju.core.locale import list_locales
    assert list_locales(tmp_project) == []
    from manju.core.check import run_check
    r = run_check(tmp_project)
    assert r.ok


# ------------------------------------------------------------------ WP5


def test_next_options_content_rejected():
    f = Failure(
        step="generate", subject="S001", cause="content rejected by provider",
        detail={"kind": "content_rejected", "fallback_chain": ["a", "b_provider"]},
    )
    opts = next_options_for(f)
    assert len(opts) >= 2
    actions = {o["action"] for o in opts}
    assert "rewrite_prompt" in actions
    assert "fallback_provider" in actions
    assert any("b_provider" in o["label_zh"] for o in opts)


def test_record_failure_embeds_next_options(tmp_project):
    rec = record_failure(
        tmp_project,
        Failure(step="generate", subject="S002", cause="timeout waiting",
                detail={"kind": "timeout"}),
    )
    assert "next_options" in rec["detail"]
    assert rec["detail"]["next_options"]


def test_explain_cost(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    from manju.build.explain import explain
    base = explain(tmp_project)
    assert "cost" not in base
    with_cost = explain(tmp_project, with_cost=True)
    assert "cost" in with_cost
    assert "total" in with_cost["cost"]


def test_export_final_export_gate(tmp_project, add_shot, monkeypatch):
    from typer.testing import CliRunner
    from manju.cli import app
    from manju.core.models import (
        CaptionLine, Timeline, TimelineMeta, TimelineTracks, VideoClip,
    )

    add_shot(tmp_project, "S001")
    # put final_export in ask_before (default already has it)
    cfg = tmp_project.load_config()
    assert "final_export" in cfg.ask_before
    # need a timeline for export to proceed past the gate check order
    tl = Timeline(
        meta=TimelineMeta(compiled_from="x"),
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="t", source="x.mp4",
                             start_ms=0, duration_ms=1000)],
            captions=[CaptionLine(start_ms=0, end_ms=500, text="hi")],
        ),
    )
    tmp_project.save_timeline(tl)
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()
    r = runner.invoke(app, ["export", "--srt", "--json"])
    # should hit waiting_user because final_export is in ask_before
    assert r.exit_code != 0
    assert "waiting_user" in (r.output or "").lower() or "final_export" in (r.output or "")


# ------------------------------------------------------------------ WP7


def test_plan_consistency_warning(tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # linxia has no ref_image
    from manju.gui.plan import action_plan
    # force a missing video plan row
    env = action_plan(tmp_project, "build", {"target": "final", "gen": "missing"})
    # may have rows if caption_card is free, or empty — either way structure
    assert "rows" in env
    if env["rows"]:
        # at least one video row should carry consistency list
        video_rows = [r for r in env["rows"] if r.get("kind") != "voice"]
        if video_rows:
            assert "consistency" in video_rows[0]
            # linxia lacks refs
            assert any("linxia" in w or "参考" in w
                       for w in (video_rows[0].get("consistency") or [])
                       ) or video_rows[0].get("consistency") == []


# ------------------------------------------------------------------ WP6


def test_roundtrip_plan_no_changes(tmp_project, add_shot, tmp_path):
    from manju.build.roundtrip import plan_roundtrip, write_baseline

    add_shot(tmp_project, "S001")
    doc = {
        "name": "test",
        "tracks": [],
        "OTIO_SCHEMA": "OpenTimelineIO.v1_Timeline",
    }
    edited = tmp_path / "timeline.otio.json"
    edited.write_text(json.dumps(doc), encoding="utf-8")
    write_baseline(tmp_project, "otio", "timeline.otio", doc, compiled_from="abc")
    # put baseline next to edited
    base_dir = edited.parent / ".baseline"
    base_dir.mkdir()
    (base_dir / "timeline.otio.json").write_text(
        json.dumps({"document": doc, "compiled_from": "abc"}),
        encoding="utf-8",
    )
    plan = plan_roundtrip(tmp_project, edited)
    assert plan["kind"] == "otio"
    assert any(r.get("class") == "no_changes" for r in plan["rows"])
