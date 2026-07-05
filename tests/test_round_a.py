"""Round A: closure of the review's newly-discovered issues #1-#3.

#1 captions-manual mode: build idempotency holds (deterministic ASS recompile
   keeps the content key stable) — audit turned into a pinned test.
#2 hand-authored timelines: QC names the frame-grid as the cause.
#3 proxy renders get the same content-key skip as finals.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg required"
)


@pytest.fixture
def two_shot_project(tmp_project, add_shot):
    from manju.providers.manual import register_manual_take

    for i in (1, 2):
        sid = f"S00{i}"
        clip = tmp_project.runtime_dir / f"c{i}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
             "-f", "lavfi", "-i", f"sine=frequency={300 * i}:duration=1",
             "-shortest", "-pix_fmt", "yuv420p", str(clip)],
            check=True,
        )
        add_shot(tmp_project, sid, dialogue={"speaker": "linxia", "text": f"台词{i}。"})
        take = register_manual_take(tmp_project, sid, clip)
        tmp_project.update_shot_raw(
            sid, lambda d, t=take.name: d.setdefault("status", {}).__setitem__("selected_take", t)
        )
    return tmp_project


# ------------------------------------------------------------------ issue 3


def test_proxy_build_is_idempotent(two_shot_project):
    from manju.build.graph import run_build

    p = two_shot_project
    assert run_build(p, target="proxy").ok
    proxy = p.proxy_dir / "proxy.mp4"
    assert proxy.exists()
    assert (p.proxy_dir / "proxy.key.json").exists()
    first_mtime = proxy.stat().st_mtime_ns

    assert run_build(p, target="proxy").ok
    assert proxy.stat().st_mtime_ns == first_mtime, "proxy was re-encoded needlessly"

    # --force re-encodes
    assert run_build(p, target="proxy", force=True).ok
    assert proxy.stat().st_mtime_ns != first_mtime


def test_proxy_reencodes_when_content_changes(two_shot_project):
    from manju.build.graph import run_build
    from manju.providers.manual import register_manual_take

    p = two_shot_project
    assert run_build(p, target="proxy").ok
    proxy = p.proxy_dir / "proxy.mp4"
    first_mtime = proxy.stat().st_mtime_ns

    swap = p.runtime_dir / "swap.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(swap)],
        check=True,
    )
    take = register_manual_take(p, "S001", swap)
    p.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    assert run_build(p, target="proxy").ok
    assert proxy.stat().st_mtime_ns != first_mtime, "changed content must re-encode"


# ------------------------------------------------------------------ issue 2


def test_qc_names_frame_grid_for_off_grid_timeline(two_shot_project):
    """A hand-edited timeline with a 1000ms clip... wait — 1000ms@24 = 24
    frames exactly; use 1015ms (24.36 frames) to be off-grid."""
    from manju.build.graph import run_build
    from manju.core.yamlio import read_json, write_json
    from manju.qc.checks import run_qc

    p = two_shot_project
    assert run_build(p, target="qc").ok

    data = read_json(p.timeline_path)
    data["tracks"]["video"][0]["duration_ms"] = 1015  # off-grid @24fps
    write_json(p.timeline_path, data)

    report = run_qc(p, p.load_timeline(), extract_frames=False)
    hints = [i for i in report.items if "not frame-aligned" in i.message]
    assert hints, "off-grid clip must be named by QC"
    assert "1015ms" in hints[0].message
    assert "snap" in hints[0].suggestion


def test_qc_no_grid_hint_for_compiled_timeline(two_shot_project):
    from manju.build.graph import run_build
    from manju.qc.checks import run_qc

    p = two_shot_project
    assert run_build(p, target="qc").ok
    report = run_qc(p, p.load_timeline(), extract_frames=False)
    assert not [i for i in report.items if "not frame-aligned" in i.message]


# ------------------------------------------------------------------ issue 1


def test_captions_manual_mode_build_stays_idempotent(two_shot_project):
    """Audit closure: the ASS recompiled from an unchanged human SRT is
    byte-deterministic, so the final content key stays stable across builds
    even though export_captions rewrites the file each time."""
    from manju.build.graph import run_build

    p = two_shot_project
    assert run_build(p, target="final").ok
    srt = p.captions_dir / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,500\n人工字幕,保持不变\n",
                   encoding="utf-8")
    rules = p.load_rules()
    rules.captions.mode = "manual"
    p.save_rules(rules)

    # first manual-mode build renders (key changed: captions changed)
    assert run_build(p, target="final").ok
    finals_after_first = sorted(p.final_dir.glob("final_v*.mp4"))

    # second build with the SAME human SRT must be a key match -> no new final
    assert run_build(p, target="final").ok
    assert sorted(p.final_dir.glob("final_v*.mp4")) == finals_after_first

    # editing the human SRT changes the key -> exactly one new final
    srt.write_text("1\n00:00:00,000 --> 00:00:01,500\n人工字幕,改了\n",
                   encoding="utf-8")
    assert run_build(p, target="final").ok
    assert len(sorted(p.final_dir.glob("final_v*.mp4"))) == len(finals_after_first) + 1
