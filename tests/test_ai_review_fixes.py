"""Fixes from external analysis (ai.txt): P0–P2 interconnection gaps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.core.locale import add_locale, load_lines, locale_status, overlay_shot_for_voice
from manju.core.models import (
    CaptionLine,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
)
from manju.core.yamlio import write_yaml


# ------------------------------------------------------------------ P0.1 captions


def test_roundtrip_caption_patch_keeps_full_srt(tmp_project, add_shot, tmp_path):
    """Changing one cue must not drop the rest of the SRT."""
    from manju.build.roundtrip import apply_roundtrip, plan_roundtrip

    add_shot(tmp_project, "S001")
    # human captions with 3 cues
    srt = tmp_project.captions_dir / "captions.srt"
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n第一条\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n第二条\n\n"
        "3\n00:00:02,000 --> 00:00:03,000\n第三条\n",
        encoding="utf-8",
    )
    plan = {
        "kind": "otio",
        "edited": str(tmp_path / "x.otio"),
        "rows": [
            {
                "class": "caption_edit",
                "state": "ok",
                "action": "manual_captions",
                "evidence": {
                    "index": 1,
                    "from": {"start_ms": 1000, "end_ms": 2000, "text": "第二条"},
                    "to": {"start_ms": 1000, "end_ms": 2000, "text": "改过的第二"},
                },
            }
        ],
    }
    result = apply_roundtrip(tmp_project, plan, actor="test")
    assert any(a.get("class") == "caption_edit" for a in result["applied"])
    text = srt.read_text(encoding="utf-8")
    assert "第一条" in text
    assert "改过的第二" in text
    assert "第三条" in text
    assert "第二条" not in text or "改过的第二" in text
    # still three cue blocks
    assert text.count("-->") == 3


# ------------------------------------------------------------------ P0.2 WaitingUser code


def test_voice_preview_waiting_user_code(tmp_project, add_shot, monkeypatch):
    from typer.testing import CliRunner
    from manju.cli import app
    from manju.build.graph import WaitingUser

    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)

    def boom(*a, **k):
        raise WaitingUser("waiting_user: spend 1 CNY", 1.0, "CNY")

    monkeypatch.setattr("manju.media.ttspreview.preview_voice", boom)
    runner = CliRunner()
    r = runner.invoke(app, ["voice", "S001", "--preview", "--json"])
    assert r.exit_code != 0
    # structured error when --json
    out = r.output or ""
    assert "waiting_user" in out.lower() or "waiting_user" in out


# ------------------------------------------------------------------ P0.3 webpreview allowlist


def test_media_prefixes_include_webpreview_tts():
    from manju.gui.server import MEDIA_PREFIXES
    assert any(p.startswith(".manju/webpreview") for p in MEDIA_PREFIXES)


# ------------------------------------------------------------------ P0.4 plan lang thread


def test_action_plan_threads_lang(tmp_project, add_shot, monkeypatch):
    from manju.gui.plan import action_plan

    add_shot(tmp_project, "S001")
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "Hello"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    seen = {}

    def fake_run_build(project, **kwargs):
        seen.update(kwargs)
        from manju.build.graph import BuildResult
        r = BuildResult()
        r.plan = [{"shot": "S001", "kind": "voice", "reason": "missing",
                   "provider": "x", "estimated_cost": 0, "currency": "CNY",
                   "lang": kwargs.get("lang")}]
        r.saved_cost = 0
        return r

    monkeypatch.setattr("manju.build.graph.run_build", fake_run_build)
    env = action_plan(tmp_project, "build", {
        "target": "final", "gen": "missing", "lang": "en",
    })
    assert seen.get("lang") == "en"
    assert seen.get("dry_run") is True
    assert env["rows"]


# ------------------------------------------------------------------ P1 locale status


def test_locale_status_has_voice_and_artifacts(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "Hi"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    info = locale_status(tmp_project, "en")
    body = info["locales"]["en"]
    assert "voice" in body
    assert body["voice"]["S001"] == "missing"  # has text, no take
    assert body["voice"]["S002"] == "not_needed"  # no text
    assert "captions" in body and "freshness" in body["captions"]
    assert "final" in body and "freshness" in body["final"]


def test_voices_yaml_overrides_base_bible_voice_id(tmp_project, add_shot):
    """locale voices.yaml MUST beat bible voice_id (effective voice)."""
    # Put Chinese voice_id on the character bible entry
    write_yaml(
        tmp_project.root / "bible" / "characters.yaml",
        {
            "linxia": {
                "name": "林夏",
                "voice_id": "zh-CN-XiaoxiaoNeural",
                "voice": "冷静",
            }
        },
    )
    add_shot(tmp_project, "S001")
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "Hello"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)
    write_yaml(
        tmp_project.root / "locales" / "en" / "voices.yaml",
        {"linxia": {"voice_id": "en-US-JennyNeural"}},
    )
    shot = overlay_shot_for_voice(
        tmp_project, tmp_project.load_shot("S001"), "en",
    )
    assert shot.dialogue.text == "Hello"
    assert shot.generation.params.get("locale_voice_id") == "en-US-JennyNeural"

    from manju.providers.edge_tts import EdgeTtsProvider
    from manju.providers.manifest import ProviderManifest
    from manju.core.spec import VOICE_VERSION, compute_voice_hash, voice_payload

    m = ProviderManifest.model_validate({
        "id": "edge", "type": "tts",
        "adapter": "manju.providers.edge_tts:EdgeTtsProvider",
        "tts": {"default_voice": "zh-CN-YunxiNeural"},
        "cost": {"per_call": 0, "currency": "CNY"},
    })
    p = EdgeTtsProvider(m)
    # MUST be English override, not bible zh-CN-XiaoxiaoNeural
    assert p._voice_for(shot, tmp_project.load_bible()) == "en-US-JennyNeural"
    # Hash must fold the locale voice_id so changing voices.yaml restages
    payload = voice_payload(shot, tmp_project.load_bible(), version=VOICE_VERSION)
    assert payload["voice_ref"].get("voice_id") == "en-US-JennyNeural"


# ------------------------------------------------------------------ P1 JY captions


def test_jianying_text_materials_carry_manju(tmp_project, add_shot, make_take, tmp_path):
    from manju.core.spec import compute_spec_hash
    from manju.exporters.jianying import export_jianying

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001",
                     compute_spec_hash(shot, tmp_project.load_bible()))
    media = tmp_project.root / "media" / "gen" / "S001" / f"{take.name}.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    if not media.exists():
        media.write_bytes(b"x")
    tl = Timeline(
        meta=TimelineMeta(compiled_from="x"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take=take.name,
                             source=f"media/gen/S001/{take.name}.mp4",
                             start_ms=0, duration_ms=2000)],
            captions=[
                CaptionLine(start_ms=100, end_ms=900, text="你好",
                            shot="S001", speaker="linxia"),
            ],
        ),
    )
    path = export_jianying(tmp_project, tl)
    draft = json.loads(path.read_text(encoding="utf-8"))
    texts = (draft.get("materials") or {}).get("texts") or []
    assert texts
    assert texts[0].get("manju", {}).get("kind") == "caption"
    assert texts[0]["manju"].get("shot") == "S001"
    # text track segments too
    text_tracks = [t for t in draft.get("tracks") or [] if t.get("type") == "text"]
    assert text_tracks
    segs = text_tracks[0].get("segments") or []
    assert segs and segs[0].get("manju", {}).get("cue_index") == 0


def test_jianying_caption_edit_in_plan(tmp_project, add_shot, tmp_path):
    from manju.build.roundtrip import plan_roundtrip, write_baseline

    add_shot(tmp_project, "S001")
    base = {
        "materials": {"texts": [
            {"id": "t0", "type": "text", "content": "原句",
             "manju": {"kind": "caption", "shot": "S001", "cue_index": 0}},
        ], "videos": []},
        "tracks": [
            {"type": "text", "segments": [
                {"material_id": "t0",
                 "target_timerange": {"start": 0, "duration": 1_000_000},
                 "manju": {"kind": "caption", "shot": "S001", "cue_index": 0,
                           "text": "原句"}},
            ]},
        ],
        "canvas_config": {}, "duration": 1_000_000, "fps": 24,
    }
    edited = json.loads(json.dumps(base))
    edited["materials"]["texts"][0]["content"] = "改写"
    edited["tracks"][0]["segments"][0]["manju"]["text"] = "改写"
    # also update content via material
    path = tmp_path / "draft_content.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    base_dir = path.parent / ".baseline"
    base_dir.mkdir()
    (base_dir / "draft_content.json").write_text(
        json.dumps({"document": base, "compiled_from": ""}),
        encoding="utf-8",
    )
    plan = plan_roundtrip(tmp_project, path)
    caps = [r for r in plan["rows"] if r.get("class") == "caption_edit"]
    assert caps, f"expected caption_edit, got {plan['rows']}"


# ------------------------------------------------------------------ P2 permute_index


def test_permute_index_shared_writer(tmp_project, add_shot):
    from manju.core.writes import WriteRejected, permute_index

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    order = permute_index(tmp_project, ["S002", "S001"], actor="t", via="test")
    assert order == ["S002", "S001"]
    assert tmp_project.load_index().order == ["S002", "S001"]
    with pytest.raises(WriteRejected):
        permute_index(tmp_project, ["S001"], actor="t", via="test")


# ------------------------------------------------------------------ GUI routes


def test_roundtrip_api_routes_exist():
    src = Path("src/manju/gui/server.py").read_text(encoding="utf-8")
    assert "/api/roundtrip/plan" in src
    assert "/api/roundtrip/apply" in src
    assert "_act_roundtrip_plan" in src


def test_jianying_reorder_prefers_track_segments_not_materials(
    tmp_project, add_shot, make_take, tmp_path,
):
    """WP6 AC: swapping only track segment order must yield reorder row."""
    from manju.build.roundtrip import plan_roundtrip, write_baseline
    from manju.core.spec import compute_spec_hash
    from manju.exporters.jianying import export_jianying
    from manju.core.models import AudioClip

    s1 = add_shot(tmp_project, "S001")
    s2 = add_shot(tmp_project, "S002")
    t1 = make_take(tmp_project, "S001",
                   compute_spec_hash(s1, tmp_project.load_bible()))
    t2 = make_take(tmp_project, "S002",
                   compute_spec_hash(s2, tmp_project.load_bible()))
    for sid, tn in (("S001", t1.name), ("S002", t2.name)):
        p = tmp_project.root / "media" / "gen" / sid / f"{tn}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(b"x")
        tmp_project.update_shot_raw(
            sid, lambda d, n=tn: d.setdefault("status", {}).__setitem__(
                "selected_take", n),
        )
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp-base"),
        fps=24, width=1080, height=1920, duration_ms=4000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take=t1.name,
                          source=f"media/gen/S001/{t1.name}.mp4",
                          start_ms=0, duration_ms=2000),
                VideoClip(shot="S002", take=t2.name,
                          source=f"media/gen/S002/{t2.name}.mp4",
                          start_ms=2000, duration_ms=2000),
            ],
            captions=[
                CaptionLine(start_ms=100, end_ms=900, text="一", shot="S001"),
                CaptionLine(start_ms=2100, end_ms=2900, text="二", shot="S002"),
            ],
        ),
    )
    draft_path = export_jianying(tmp_project, tl)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    # materials stay S001 then S002; only swap video track segments
    vtracks = [t for t in draft["tracks"] if t.get("type") == "video"]
    assert vtracks
    segs = vtracks[0]["segments"]
    assert len(segs) >= 2
    vtracks[0]["segments"] = [segs[1], segs[0]]
    # change one caption
    texts = draft["materials"]["texts"]
    texts[0]["content"] = "改写一"
    if texts[0].get("manju"):
        texts[0]["manju"]["text"] = "改写一"
    for t in draft["tracks"]:
        if t.get("type") == "text" and t.get("segments"):
            m = t["segments"][0].get("manju") or {}
            m["text"] = "改写一"
            t["segments"][0]["manju"] = m
    edited = tmp_path / "draft_content.json"
    edited.write_text(json.dumps(draft), encoding="utf-8")
    base_dir = edited.parent / ".baseline"
    base_dir.mkdir()
    # baseline is original export order
    orig = json.loads(draft_path.read_text(encoding="utf-8"))
    (base_dir / "draft_content.json").write_text(
        json.dumps({"document": orig, "compiled_from": "fp-base",
                    "captions_hash": None}),
        encoding="utf-8",
    )
    plan = plan_roundtrip(tmp_project, edited)
    classes = [r.get("class") for r in plan["rows"] if r.get("class") != "no_changes"]
    assert "reorder" in classes, f"expected reorder from segment swap, got {plan['rows']}"
    assert "caption_edit" in classes, f"expected caption_edit, got {plan['rows']}"


def test_locale_voice_status_uses_provider_descriptor(tmp_project, add_shot, tmp_path):
    """v2 sidecar with provider-folded hash must read fresh when descriptor matches."""
    from manju.core.locale import locale_status, overlay_shot_for_voice
    from manju.core.models import VoiceTakeSidecar
    from manju.core.spec import VOICE_VERSION, compute_voice_hash
    from manju.providers.tts import voice_provider_descriptor

    add_shot(tmp_project, "S001")
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "Hello"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    shot = overlay_shot_for_voice(tmp_project, tmp_project.load_shot("S001"), "en")
    # Fake Edge-like provider descriptor
    class Fake:
        id = "edge"
        manifest = type("M", (), {
            "submit": None,
            "tts": type("T", (), {"language": "en", "audio_format": "mp3"})(),
        })()
    desc = voice_provider_descriptor(Fake())
    h = compute_voice_hash(
        shot, tmp_project.load_bible(), version=VOICE_VERSION, provider=desc,
    )
    media = tmp_path / "v.wav"
    media.write_bytes(b"RIFF" + b"\x00" * 20)
    # register under locales/en with matching hash
    dest = tmp_project.register_voice_take(
        "S001", media,
        VoiceTakeSidecar(provider="edge", voice_hash=h, voice_hash_version=VOICE_VERSION),
        lang="en",
    )
    assert dest.exists()

    # Point status evaluator at same descriptor
    import manju.core.locale as loc_mod
    monkey = pytest.MonkeyPatch()
    monkey.setattr(loc_mod, "_current_tts_descriptor", lambda: desc)
    try:
        info = locale_status(tmp_project, "en")
        assert info["locales"]["en"]["voice"]["S001"] == "fresh"
    finally:
        monkey.undo()


def test_captions_hash_conflict_on_roundtrip(tmp_project, add_shot, tmp_path):
    """If human SRT moved after export, caption_edit rows are conflict."""
    from manju.build.roundtrip import plan_roundtrip

    add_shot(tmp_project, "S001")
    srt = tmp_project.captions_dir / "captions.srt"
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n当前人工字幕\n",
        encoding="utf-8",
    )
    base = {
        "materials": {"texts": [
            {"id": "t0", "type": "text", "content": "原导出",
             "manju": {"kind": "caption", "shot": "S001", "cue_index": 0}},
        ], "videos": []},
        "tracks": [
            {"type": "text", "segments": [
                {"material_id": "t0",
                 "target_timerange": {"start": 0, "duration": 1_000_000},
                 "manju": {"kind": "caption", "cue_index": 0, "text": "原导出"}},
            ]},
        ],
        "canvas_config": {}, "duration": 1_000_000, "fps": 24,
    }
    edited = json.loads(json.dumps(base))
    edited["materials"]["texts"][0]["content"] = "外部改写"
    edited["tracks"][0]["segments"][0]["manju"]["text"] = "外部改写"
    path = tmp_path / "draft_content.json"
    path.write_text(json.dumps(edited), encoding="utf-8")
    base_dir = path.parent / ".baseline"
    base_dir.mkdir()
    from manju.core.hashing import hash_text
    # baseline says captions were empty/different at export
    (base_dir / "draft_content.json").write_text(
        json.dumps({
            "document": base,
            "compiled_from": "",
            "captions_hash": hash_text("old-export-srt"),
        }),
        encoding="utf-8",
    )
    plan = plan_roundtrip(tmp_project, path)
    caps = [r for r in plan["rows"] if r.get("class") == "caption_edit"]
    assert caps
    assert caps[0]["state"] == "conflict"


def test_multi_shot_asr_help_is_honest(tmp_project, add_shot, tmp_path):
    from manju.media.align import plan_multi_shot
    from manju.core.container import ProjectError

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    media = tmp_path / "vo.wav"
    media.write_bytes(b"RIFF" + b"\x00" * 40)
    with pytest.raises(ProjectError) as exc:
        plan_multi_shot(
            tmp_project, media, ["S001", "S002"],
            asr="dummy", probe_fn=lambda p: 2000,
        )
    msg = str(exc.value)
    assert "transcribe" in msg
    assert "--from-srt" in msg or "from-srt" in msg
