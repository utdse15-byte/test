"""qc/ integrity — regressions found by the hourly qc/ scan (adversarially
reproduced before fixing). Behavioral pins, never source-text greps.

1. checks._extract_frame ran ffmpeg WITHOUT checking its return code and reported
   success purely from dest.exists(), so a failed extraction (non-zero exit, no
   exception) left the stale per-shot frame from a PRIOR take on disk and was
   surfaced as a fresh "mid-point frame for visual review" — the content tier's
   eyes judging the current take on the wrong take's frame (UNKNOWN->PASS).
2. report._derive_action matched the substring "duration" anywhere, so QC errors
   whose subject is NOT a shot ("final" render drift, "voice"/"music"/"sfx"/
   "ambient" negative clip duration) were mis-derived as an auto-safe shot
   redo_new_seed — `manju repair --auto` then redo_shot's a non-existent shot,
   masking the real re-render / timeline-edit fix.
3. captions_access.accessibility_for_project read a manual captions.srt with
   encoding="utf-8" and NO errors="replace", so a non-UTF-8 human-authored SRT
   raised UnicodeDecodeError and crashed the report generator — violating the
   documented "never a crash" contract and CLAUDE.md's errors="replace" mandate.
4. runperf._latest_run_id opened events.jsonl in strict text mode and iterated
   `for raw in f`, so a torn multibyte tail raised an uncaught UnicodeDecodeError
   out of the default `manju perf` path (the build.attempts torn-tail pattern).
5. agent_review.read_v2_records / _read_records read qc_agent.jsonl with strict
   UTF-8 and caught only OSError, so an invalid-UTF-8 byte raised an uncaught
   UnicodeDecodeError — breaking their documented "Never raises" contract.
"""

from __future__ import annotations

import json

import pytest


# --------------------------------------------------------------------------- #
# (1) _extract_frame honours ffmpeg's return code (UNKNOWN never -> PASS)        #
# --------------------------------------------------------------------------- #


def test_extract_frame_failure_not_reported_as_success(tmp_path, monkeypatch):
    from manju.qc import checks

    dest = tmp_path / "frames" / "S001.jpg"
    dest.parent.mkdir(parents=True)

    class _Proc:
        returncode = 1  # ffmpeg could not decode the current take

    def _failing_run(*a, **k):
        # ffmpeg's -y truncates/creates the output before the decode fails,
        # leaving a partial file on disk even though extraction failed
        dest.write_bytes(b"")  # 0-byte partial — dest.exists() is now True
        return _Proc()

    monkeypatch.setattr(checks.subprocess, "run", _failing_run)
    # a failed extraction must report False, not surface the partial/stale file
    assert checks._extract_frame(tmp_path / "take_02.mp4", dest, 1.5) is False


def test_extract_frame_success_on_zero_returncode(tmp_path, monkeypatch):
    from manju.qc import checks

    dest = tmp_path / "frames" / "S001.jpg"

    class _Proc:
        returncode = 0

    def _ok_run(*a, **k):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"FRESH-JPEG")
        return _Proc()

    monkeypatch.setattr(checks.subprocess, "run", _ok_run)
    assert checks._extract_frame(tmp_path / "take.mp4", dest, 1.0) is True


# --------------------------------------------------------------------------- #
# (2) _derive_action: a non-shot "duration" item is not an auto-safe shot redo   #
# --------------------------------------------------------------------------- #


def test_derive_action_final_duration_drift_is_human_review(tmp_project):
    from manju.qc import report
    from manju.qc.checks import QCItem

    item = QCItem(
        level="error", area="technical", subject="final",
        message=("final duration 12345ms differs from timeline 12000ms "
                 "by 345ms (> 1 frame = 40.0ms)"))
    action = report._derive_action(tmp_project, item, {})
    # a final-render drift routes to human_review (re-render), never an
    # auto-safe re-seed of a non-existent shot named 'final'
    assert action["action"] == "human_review"
    assert action["auto_safe"] is False


@pytest.mark.parametrize("track", ["voice", "music", "sfx", "ambient"])
def test_derive_action_audio_negative_duration_not_auto_safe(tmp_project, track):
    from manju.qc import report
    from manju.qc.checks import QCItem

    item = QCItem(
        level="error", area="technical", subject=track,
        message=f"{track} clip 'x.wav' has a negative duration (-5ms)")
    action = report._derive_action(tmp_project, item, {})
    # a track name is not a shot — must not become an auto-safe redo_shot target
    assert action["auto_safe"] is False


def test_derive_action_real_shot_shorter_still_redos(tmp_project, add_shot):
    from manju.qc import report
    from manju.qc.checks import QCItem

    add_shot(tmp_project, "S001")
    item = QCItem(
        level="warn", area="technical", subject="S001",
        message=("source is shorter than the clip: source 1000ms vs clip "
                 "3000ms (normalize will pad, but check the intent)"))
    action = report._derive_action(tmp_project, item, {})
    # a genuine per-shot deficit is still the auto-safe re-seed (unchanged)
    assert action["action"] == "redo_new_seed"
    assert action["auto_safe"] is True


# --------------------------------------------------------------------------- #
# (3) accessibility_for_project survives a non-UTF-8 manual SRT                  #
# --------------------------------------------------------------------------- #


def test_accessibility_manual_srt_non_utf8_does_not_crash(tmp_project):
    from manju.qc.captions_access import accessibility_for_project

    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    srt = tmp_project.captions_dir / "captions.srt"
    # a human-authored SRT saved as GBK (non-UTF-8 bytes) — real on a Windows box
    srt.write_bytes(
        "1\r\n00:00:00,000 --> 00:00:01,000\r\n你好世界\r\n".encode("gbk"))
    rules = tmp_project.load_rules()
    rules.captions.mode = "manual"
    tmp_project.save_rules(rules)

    doc = accessibility_for_project(tmp_project)  # must not raise
    assert isinstance(doc, dict)


# --------------------------------------------------------------------------- #
# (4) runperf._latest_run_id survives a torn multibyte tail                      #
# --------------------------------------------------------------------------- #


def test_latest_run_id_survives_torn_multibyte(tmp_project):
    from manju.qc import runperf

    ev = tmp_project.root / "events.jsonl"
    valid = {"action": "stage_attempt",
             "detail": {"run_id": "run_x", "state": "SUCCEEDED"}}
    ev.write_bytes(json.dumps(valid, ensure_ascii=False).encode("utf-8") + b"\n")
    with open(ev, "ab") as f:  # torn: first 2 bytes of the 3-byte char 中
        f.write('{"detail":{"run_id":"run_y","p":"中'
                .encode("utf-8")[:-1])
    # must not raise; the torn tail is never the authority for "latest"
    assert runperf._latest_run_id(tmp_project) == "run_x"


# --------------------------------------------------------------------------- #
# (5) agent_review jsonl readers never raise on invalid UTF-8                    #
# --------------------------------------------------------------------------- #


def test_agent_review_readers_survive_invalid_utf8(tmp_project):
    from manju.qc import agent_review

    path = agent_review.agent_log_path(tmp_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    valid = {"schema": "manju.qc.v2", "shot": "S001", "verdict": "pass"}
    path.write_bytes(
        json.dumps(valid).encode("utf-8") + b"\n"
        + b"\xff\xfe not valid utf-8\n")

    # both readers document "Never raises" — the bad line is counted malformed
    _recs_v2, malformed_v2 = agent_review.read_v2_records(tmp_project)
    _recs_legacy, malformed_legacy = agent_review._read_records(tmp_project)
    assert malformed_v2 >= 1
    assert malformed_legacy >= 1
