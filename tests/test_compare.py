"""VERSION-COMPARE engine (goal 11, round-S): `manju compare` + build/compare.py.

Two layers:

- fast, ffmpeg-free tests that fabricate two finals (fake mp4 + hand-written
  ``final_vN.key.json`` / ``final_vN.timeline.json`` snapshots) and drive the
  full diff logic — per-shot take changes with providers, caption cue diff,
  audio + packaging diff, root-cause correlation from events, and the honest
  degradation path for pre-S finals with no snapshot;
- one ffmpeg-gated end-to-end test that actually builds a tiny project twice
  across a real take change, proving render-time snapshot persistence and that
  build idempotency (build twice = one final) is untouched.
"""

from __future__ import annotations

import json
import shutil

import pytest
from typer.testing import CliRunner

from manju.build.compare import CompareError, compare_finals, resolve_final
from manju.cli import app
from manju.core.models import (
    AudioClip,
    CaptionLine,
    OverlayClip,
    TakeSidecar,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
)

runner = CliRunner()

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

TS_A = "2026-07-06T10:00:00+00:00"
TS_MID = "2026-07-07T09:00:00+00:00"
TS_B = "2026-07-07T10:00:00+00:00"


# ---------------------------------------------------------------- fabricators


def _vclip(shot, take, source, start, dur):
    return VideoClip(shot=shot, take=take, source=source, start_ms=start, duration_ms=dur)


def _timeline(video, *, captions=None, music=None, overlay=None, duration_ms=None,
              width=1080, height=1920, fps=24) -> Timeline:
    return Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="compiled"),
        fps=fps, width=width, height=height,
        duration_ms=duration_ms if duration_ms is not None else sum(c.duration_ms for c in video),
        tracks=TimelineTracks(
            video=video, captions=captions or [], music=music or [], overlay=overlay or [],
        ),
    )


def _write_final(project, version, *, timeline, key, created_at):
    """Fabricate final_vN.mp4 + its key.json (+ timeline.json when given)."""
    project.final_dir.mkdir(parents=True, exist_ok=True)
    (project.final_dir / f"final_v{version}.mp4").write_bytes(b"fake-final-" + str(version).encode())
    (project.final_dir / f"final_v{version}.key.json").write_text(
        json.dumps({"final_key": key, "target": "final", "created_at": created_at}),
        encoding="utf-8",
    )
    if timeline is not None:
        (project.final_dir / f"final_v{version}.timeline.json").write_text(
            json.dumps(timeline.model_dump(), ensure_ascii=False), encoding="utf-8"
        )


def _raw_event(project, ts, actor, action, detail):
    line = json.dumps({"ts": ts, "actor": actor, "action": action, "detail": detail},
                      ensure_ascii=False)
    with open(project.root / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _register(project, shot, provider):
    """Register one real take (so compare can resolve its provider), returning
    (take_name, project-relative source)."""
    tmp = project.root / f"_src_{shot}_{provider}.mp4"
    tmp.write_bytes(b"take-bytes-" + f"{shot}{provider}".encode())
    info = project.register_take(shot, tmp, TakeSidecar(provider=provider, spec_hash="manual"))
    return info.name, project.relpath(info.media_path)


# ---------------------------------------------------------------- per-shot diff


def test_take_change_names_old_and_new_take_with_providers_and_cause(tmp_project, add_shot):
    for sid in ("S001", "S002", "S003"):
        add_shot(tmp_project, sid)
    s1_take, s1_src = _register(tmp_project, "S001", "kenburns")
    s2_take, s2_src = _register(tmp_project, "S002", "kenburns")
    # S003 has two takes: an old kenburns take, a redone comfyui take
    s3_old, s3_old_src = _register(tmp_project, "S003", "kenburns")
    s3_new, s3_new_src = _register(tmp_project, "S003", "comfyui")

    base = [_vclip("S001", s1_take, s1_src, 0, 1200),
            _vclip("S002", s2_take, s2_src, 1200, 1200)]
    tl_a = _timeline(base + [_vclip("S003", s3_old, s3_old_src, 2400, 1200)])
    tl_b = _timeline(base + [_vclip("S003", s3_new, s3_new_src, 2400, 1200)])

    _write_final(tmp_project, 2, timeline=tl_a, key="sha256:aaa", created_at=TS_A)
    _write_final(tmp_project, 3, timeline=tl_b, key="sha256:bbb", created_at=TS_B)
    _raw_event(tmp_project, TS_MID, "ai", "redo", {"shot": "S003", "takes": [s3_new]})

    diff = compare_finals(tmp_project)  # default: latest two
    assert diff["a"]["name"] == "final_v2" and diff["b"]["name"] == "final_v3"
    assert diff["degraded"] is False and diff["identical"] is False

    by_shot = {c["shot"]: c for c in diff["changes"]}
    s3 = by_shot["S003"]
    assert s3["change"] == "take_changed"
    assert s3["a"]["take"] == s3_old and s3["b"]["take"] == s3_new
    assert s3["a"]["provider"] == "kenburns" and s3["b"]["provider"] == "comfyui"
    assert "redo by ai" in s3["why"] and "2026-07-07" in s3["why"]
    # untouched shots are reported unchanged (Frame.io per-clip strip)
    assert by_shot["S001"]["change"] == "unchanged"
    assert by_shot["S002"]["change"] == "unchanged"
    assert diff["summary"]["shots_changed"] == 1


def test_added_removed_moved_and_duration_changes(tmp_project, add_shot):
    for sid in ("S001", "S002", "S003"):
        add_shot(tmp_project, sid)
    t1, s1 = _register(tmp_project, "S001", "kenburns")
    t2, s2 = _register(tmp_project, "S002", "kenburns")
    t3, s3 = _register(tmp_project, "S003", "kenburns")

    # a: S001,S002 ;  b: S002 (moved+longer), S003 (added); S001 removed
    tl_a = _timeline([_vclip("S001", t1, s1, 0, 1200),
                      _vclip("S002", t2, s2, 1200, 1200)])
    tl_b = _timeline([_vclip("S002", t2, s2, 0, 1500),
                      _vclip("S003", t3, s3, 1500, 1200)])
    _write_final(tmp_project, 1, timeline=tl_a, key="sha256:a", created_at=TS_A)
    _write_final(tmp_project, 2, timeline=tl_b, key="sha256:b", created_at=TS_B)

    diff = compare_finals(tmp_project, "final_v1", "final_v2")
    by_shot = {c["shot"]: c for c in diff["changes"]}
    assert by_shot["S001"]["change"] == "removed"
    assert by_shot["S003"]["change"] == "added"
    # S002 both moved order AND changed duration → duration_changed takes precedence
    assert by_shot["S002"]["change"] == "duration_changed"
    assert by_shot["S002"]["a"]["duration_ms"] == 1200
    assert by_shot["S002"]["b"]["duration_ms"] == 1500
    assert diff["summary"]["duration_delta_ms"] == 2700 - 2400


def test_caption_cue_diff(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    t1, s1 = _register(tmp_project, "S001", "kenburns")
    v = [_vclip("S001", t1, s1, 0, 3000)]
    caps_a = [CaptionLine(start_ms=0, end_ms=1000, text="第一句"),
              CaptionLine(start_ms=1000, end_ms=2000, text="第二句")]
    caps_b = [CaptionLine(start_ms=0, end_ms=1000, text="第一句"),
              CaptionLine(start_ms=1000, end_ms=2000, text="第二句改了"),
              CaptionLine(start_ms=2000, end_ms=3000, text="第三句")]
    _write_final(tmp_project, 1, timeline=_timeline(v, captions=caps_a),
                 key="sha256:a", created_at=TS_A)
    _write_final(tmp_project, 2, timeline=_timeline(v, captions=caps_b),
                 key="sha256:b", created_at=TS_B)

    diff = compare_finals(tmp_project, "final_v1", "final_v2")
    caps = diff["captions"]
    assert caps["changed"] and caps["a_count"] == 2 and caps["b_count"] == 3
    kinds = {c["index"]: c["change"] for c in caps["cues"]}
    assert kinds == {1: "changed", 2: "added"}
    # the flat GUI strip carries a caption summary row too
    row = next(c for c in diff["changes"] if c["shot"] == "captions")
    assert row["change"] == "captions_changed" and row["a"]["count"] == 2


def test_audio_and_packaging_diff(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    t1, s1 = _register(tmp_project, "S001", "kenburns")
    v = [_vclip("S001", t1, s1, 0, 3000)]
    music_a = [AudioClip(source="media/imports/bgm.wav", start_ms=0, duration_ms=3000, gain_db=-18.0)]
    music_b = [AudioClip(source="media/imports/bgm.wav", start_ms=0, duration_ms=3000, gain_db=-12.0)]
    logo = [OverlayClip(kind="logo", source="media/refs/logo.png", corner="tr",
                        start_ms=0, duration_ms=3000, size_pct=12.0)]
    _write_final(tmp_project, 1, timeline=_timeline(v, music=music_a),
                 key="sha256:a", created_at=TS_A)
    _write_final(tmp_project, 2, timeline=_timeline(v, music=music_b, overlay=logo),
                 key="sha256:b", created_at=TS_B)

    diff = compare_finals(tmp_project, "final_v1", "final_v2")
    assert diff["audio"]["changed"]
    bgm = next(t for t in diff["audio"]["tracks"] if t["track"] == "bgm")
    assert bgm["a"]["clips"][0]["gain_db"] == -18.0 and bgm["b"]["clips"][0]["gain_db"] == -12.0
    assert diff["packaging"]["changed"]
    logo_item = next(i for i in diff["packaging"]["items"] if i["item"] == "logo")
    assert logo_item["change"] == "added"
    # both surface on the flat strip for the GUI
    scopes = {c["shot"] for c in diff["changes"]}
    assert "audio/bgm" in scopes and "packaging/logo" in scopes


# ------------------------------------------------------------ degradation path


def test_degrades_when_a_final_has_no_snapshot(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    t1, s1 = _register(tmp_project, "S001", "kenburns")
    tl = _timeline([_vclip("S001", t1, s1, 0, 1200)])
    # v1 is a pre-S final: key sidecar but NO timeline.json
    _write_final(tmp_project, 1, timeline=None, key="sha256:old", created_at=TS_A)
    _write_final(tmp_project, 2, timeline=tl, key="sha256:new", created_at=TS_B)

    diff = compare_finals(tmp_project, "final_v1", "final_v2")
    assert diff["degraded"] is True
    assert diff["a"]["has_snapshot"] is False and diff["b"]["has_snapshot"] is True
    assert "per-shot detail unavailable" in diff["note"]
    assert "final_v1" in diff["note"]
    assert diff["changes"] == []


def test_identical_content_keys_reported(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    t1, s1 = _register(tmp_project, "S001", "kenburns")
    tl = _timeline([_vclip("S001", t1, s1, 0, 1200)])
    _write_final(tmp_project, 1, timeline=tl, key="sha256:same", created_at=TS_A)
    _write_final(tmp_project, 2, timeline=tl, key="sha256:same", created_at=TS_B)
    diff = compare_finals(tmp_project, "final_v1", "final_v2")
    assert diff["identical"] is True
    assert diff["summary"]["shots_changed"] == 0


# ------------------------------------------------------------------- resolution


def test_resolve_and_default_pair_errors(tmp_project):
    with pytest.raises(CompareError, match="at least two"):
        compare_finals(tmp_project)  # no finals at all
    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.final_dir / "final_v5.mp4").write_bytes(b"x")
    assert resolve_final(tmp_project, "final_v5").name == "final_v5.mp4"
    assert resolve_final(tmp_project, "v5").name == "final_v5.mp4"
    assert resolve_final(tmp_project, "5").name == "final_v5.mp4"
    with pytest.raises(CompareError, match="no such final"):
        resolve_final(tmp_project, "final_v9")


def test_cli_compare_json_and_human(tmp_project, add_shot, monkeypatch):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    t1, s1 = _register(tmp_project, "S001", "kenburns")
    t2a, s2a = _register(tmp_project, "S002", "kenburns")
    t2b, s2b = _register(tmp_project, "S002", "comfyui")
    base = [_vclip("S001", t1, s1, 0, 1200)]
    tl_a = _timeline(base + [_vclip("S002", t2a, s2a, 1200, 1200)])
    tl_b = _timeline(base + [_vclip("S002", t2b, s2b, 1200, 1200)])
    _write_final(tmp_project, 1, timeline=tl_a, key="sha256:a", created_at=TS_A)
    _write_final(tmp_project, 2, timeline=tl_b, key="sha256:b", created_at=TS_B)

    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["compare", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert {c["shot"] for c in data["changes"]} >= {"S001", "S002"}

    human = runner.invoke(app, ["compare"])
    assert human.exit_code == 0, human.output
    assert "S002" in human.output and "take_changed" in human.output


# ------------------------------------------------------- ffmpeg end-to-end


@pytest.mark.skipif(not _HAS_FFMPEG, reason="builds real finals through the render pipeline")
def test_e2e_snapshot_persisted_idempotent_and_compare(tmp_path):
    """Build a tiny project to final → a snapshot lands next to it; build again
    → still one final (idempotency untouched); swap a take → a second final +
    snapshot; compare names the changed shot."""
    from manju.build.compare import compare_finals as _cmp
    from manju.build.graph import run_build
    from manju.core.container import Project
    from manju.providers.manual import register_manual_take
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path / "样片", shots=3, with_bgm=False)
    project = Project(root)

    assert run_build(project, target="final").ok
    finals = sorted(project.final_dir.glob("final_v*.mp4"))
    assert len(finals) == 1
    snap = finals[0].with_suffix(".timeline.json")
    assert snap.exists(), "render must persist final_vN.timeline.json"
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert data["tracks"]["video"], "snapshot carries the compiled video track"

    # build again with identical inputs → idempotent, still ONE final + snapshot
    assert run_build(project, target="final").ok
    assert len(list(project.final_dir.glob("final_v*.mp4"))) == 1
    assert snap.exists()

    # swap S002's selected take → a genuinely different final
    import subprocess

    swap = project.runtime_dir / "swap.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(swap)],
        check=True,
    )
    take = register_manual_take(project, "S002", swap)
    project.update_shot_raw(
        "S002", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    assert run_build(project, target="final").ok
    assert len(list(project.final_dir.glob("final_v*.mp4"))) == 2

    diff = _cmp(project)  # latest two
    by_shot = {c["shot"]: c for c in diff["changes"]}
    assert by_shot["S002"]["change"] == "take_changed"
    assert by_shot["S002"]["b"]["take"] == take.name
    assert diff["degraded"] is False
