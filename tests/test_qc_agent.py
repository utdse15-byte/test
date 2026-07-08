"""Agent visual-QC pipe (round V, goal item 6).

Manju runs NO vision model; professional visual judgment is the driving agent's
own eyes + the visual-qc-review skill's A–J standards. These tests exercise the
deterministic PIPE Manju owns: brief (出题) → verdict (回填, bound to take bytes)
→ merge into run_qc as [AI判读] items, with staleness when the take changes.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.models import TakeSidecar
from manju.core.spec import compute_spec_hash
from manju.qc.agent_review import (
    VerdictError,
    agent_log_path,
    agent_verdict_items,
    qc_brief,
    record_verdicts,
)
from manju.qc.checks import run_qc

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg + ffprobe required for real frame extraction",
)


# --------------------------------------------------------------- helpers


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _fake_take(project, shot_id, spec_hash):
    """A few-byte fake take (enough for hashing / merge; no real frames)."""
    tmp = project.root / f"_fake_{shot_id}_{spec_hash[-6:]}.mp4"
    tmp.write_bytes(b"fakevid-" + spec_hash.encode("ascii")[-8:])
    take = project.register_take(shot_id, tmp,
                                 TakeSidecar(provider="test", spec_hash=spec_hash))
    tmp.unlink()
    return take


def _real_take(project, shot_id, spec_hash, *, color="red"):
    tmp = project.root / f"_src_{shot_id}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=160x120:d=1:r=24", "-pix_fmt", "yuv420p", str(tmp)],
        check=True, capture_output=True,
    )
    take = project.register_take(shot_id, tmp,
                                 TakeSidecar(provider="test", spec_hash=spec_hash))
    tmp.unlink()
    return take


# ------------------------------------------------------------------- brief


@needs_ffmpeg
def test_qc_brief_structure(tmp_project, add_shot):
    """The brief carries real extracted frames, shot context, and the criteria
    pointer — but no judgment (that lives in the visual-qc-review skill)."""
    from manju.core.yamlio import write_yaml

    write_yaml(tmp_project.root / "bible" / "characters.yaml", {
        "linxia": {"name": "林夏", "appearance": "短发,黑色风衣",
                   "ref_images": ["media/refs/linxia_front.png"]},
    })
    shot = add_shot(tmp_project, "S001",
                    quality={"must_show": ["硬币年份 2036 清晰可读"],
                             "avoid": ["穿帮的现代物品"]},
                    continuity={"locks": ["林夏戴旧手表"]})
    take = _real_take(tmp_project, "S001",
                      compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take.name)

    brief = qc_brief(tmp_project)

    # criteria pointer names the skill and the 中文 how-to (no criteria inline)
    assert brief["criteria"]["skill"] == "visual-qc-review"
    assert "visual-qc-review" in brief["criteria"]["note"]
    assert "verdicts" in brief["verdict_contract"]["shape"]

    assert len(brief["shots"]) == 1
    entry = brief["shots"][0]
    assert entry["shot"] == "S001"
    assert entry["take"] == take.name
    assert entry["take_hash"] and entry["take_hash"].startswith("sha256:")

    # real frames extracted at first / mid / last and present on disk
    frames = entry["frames"]
    assert set(frames) == {"first", "mid", "last"}
    for rel in frames.values():
        assert rel is not None
        assert (tmp_project.root / rel).exists()

    ctx = entry["context"]
    assert ctx["scene"]["id"] == "convenience_store"
    assert ctx["characters"][0]["id"] == "linxia"
    assert ctx["characters"][0]["refs"] == ["media/refs/linxia_front.png"]
    assert ctx["must_show"] == ["硬币年份 2036 清晰可读"]
    assert ctx["avoid"] == ["穿帮的现代物品"]
    assert ctx["continuity_locks"] == ["林夏戴旧手表"]
    assert ctx["dialogue"]["text"] == "这不可能。"


def test_qc_brief_skips_shots_without_a_take(tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # no take selected
    brief = qc_brief(tmp_project)
    assert brief["shots"] == []
    assert brief["skipped"] and brief["skipped"][0]["shot"] == "S001"

    # an explicitly requested unknown shot is reported, not silently dropped
    brief2 = qc_brief(tmp_project, ["ZZZ"])
    assert brief2["skipped"][0] == {"shot": "ZZZ", "reason": "不是本项目镜头"}


# ------------------------------------------------------- verdict round-trip


def test_verdict_roundtrip_surfaces_ai_items(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")
    take = _fake_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take.name)

    result = record_verdicts(tmp_project, {"verdicts": [
        {"shot": "S001", "take": take.name, "criterion": "A1", "level": "blocker",
         "message": "林夏中途变脸", "evidence": "frames/S001_mid.jpg", "frame_ms": 480},
        {"shot": "S001", "take": take.name, "criterion": "B2", "level": "issue",
         "message": "袖口纽扣数量在剪辑间变化"},
        {"shot": "S001", "take": take.name, "criterion": "E5", "level": "fyi",
         "message": "皮肤略有塑料感(弱信号)"},
    ]}, actor="ai")
    assert result["written"] == 3
    assert result["levels"] == {"blocker": 1, "issue": 1, "fyi": 1}

    report = run_qc(tmp_project, None, extract_frames=False)
    ai = [i for i in report.items if i.area == "content" and "[AI判读]" in i.message]
    by_level = {i.level: i for i in ai}
    # blocker→error / issue→warn / fyi→info
    assert by_level["error"].subject == "S001" and "A1" in by_level["error"].message
    assert "林夏中途变脸" in by_level["error"].message
    assert "480ms" in by_level["error"].suggestion  # frame_ms surfaced
    assert "B2" in by_level["warn"].message
    assert "E5" in by_level["info"].message
    # the blocker means the whole report is not ok
    assert not report.ok


def test_verdict_goes_stale_when_take_regenerated(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")
    take1 = _fake_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take1.name)
    record_verdicts(tmp_project, {"verdicts": [
        {"shot": "S001", "take": take1.name, "criterion": "A1", "level": "blocker",
         "message": "旧版本的问题"},
    ]})

    # regenerate: a NEW take with different bytes becomes the selection
    take2 = _fake_take(tmp_project, "S001", "sha256:regenerated")
    assert take2.name != take1.name
    _select(tmp_project, "S001", take2.name)

    items = agent_verdict_items(tmp_project)
    # the old verdict no longer matches the current bytes → NOT surfaced as [AI判读]
    assert not any("[AI判读]" in i.message for i in items)
    stale = [i for i in items if i.level == "info" and "已过期" in i.message]
    assert stale and stale[0].subject == "S001"
    assert "manju qc brief" in stale[0].suggestion


def test_malformed_jsonl_line_skipped(tmp_project, add_shot):
    shot = add_shot(tmp_project, "S001")
    take = _fake_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take.name)
    record_verdicts(tmp_project, {"verdicts": [
        {"shot": "S001", "take": take.name, "criterion": "C1", "level": "issue",
         "message": "背景物体形变"},
    ]})
    # a torn / garbage line lands in the append-only log
    with open(agent_log_path(tmp_project), "a", encoding="utf-8") as f:
        f.write("{not valid json,,,\n")

    items = agent_verdict_items(tmp_project)  # must not raise
    assert any("[AI判读]" in i.message and "C1" in i.message for i in items)
    assert any("无法解析" in i.message for i in items)  # skip + count surfaced


def test_unknown_shot_rejected(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, {"verdicts": [
            {"shot": "S999", "level": "blocker", "message": "不存在的镜头"},
        ]})
    assert "S999" in str(exc.value)
    # nothing was persisted — validation is all-or-nothing
    assert not agent_log_path(tmp_project).exists()


def test_bad_level_rejected(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    with pytest.raises(VerdictError):
        record_verdicts(tmp_project, {"verdicts": [
            {"shot": "S001", "level": "critical", "message": "级别不合法"},
        ]})


# --------------------------------------------------- round-W #33: intake + agg


def test_empty_criterion_rejected(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, {"verdicts": [
            {"shot": "S001", "level": "issue", "criterion": "  ", "message": "有结论没标准"},
        ]})
    assert "criterion" in str(exc.value)
    assert not agent_log_path(tmp_project).exists()  # all-or-nothing


def test_empty_message_rejected(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, {"verdicts": [
            {"shot": "S001", "level": "issue", "criterion": "A1", "message": ""},
        ]})
    assert "message" in str(exc.value)


def test_legacy_empty_criterion_findings_do_not_overwrite_each_other(tmp_project, add_shot):
    """round-W #33: record_verdicts now refuses an empty criterion/message, so
    this collision is impossible for anything written going forward — but a
    jsonl file from BEFORE this fix could still hold several distinct findings
    that all share an empty criterion string. The merge must surface every one
    of them, not just the last line for that shot."""
    shot = add_shot(tmp_project, "S001")
    take = _fake_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take.name)

    # simulate a pre-fix jsonl: two DISTINCT findings, both criterion="".
    legacy_lines = [
        {"ts": "2024-01-01T00:00:00", "actor": "ai", "shot": "S001", "take": take.name,
         "take_hash": None, "criterion": "", "level": "blocker",
         "message": "手部穿模", "evidence": ""},
        {"ts": "2024-01-01T00:00:01", "actor": "ai", "shot": "S001", "take": take.name,
         "take_hash": None, "criterion": "", "level": "issue",
         "message": "背景灯光跳变", "evidence": ""},
    ]
    # bind take_hash to the REAL current take bytes so both records are live.
    from manju.core.hashing import hash_file

    real_hash = hash_file(take.media_path)
    for line in legacy_lines:
        line["take_hash"] = real_hash

    path = agent_log_path(tmp_project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for line in legacy_lines:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    items = agent_verdict_items(tmp_project)
    ai_items = [i for i in items if "[AI判读]" in i.message]
    bodies = " | ".join(i.message for i in ai_items)
    # BOTH legacy findings survive the merge — neither overwrote the other.
    assert "手部穿模" in bodies
    assert "背景灯光跳变" in bodies
    assert len(ai_items) == 2


# ----------------------------------------------------------------- CLI


def test_cli_brief_json(tmp_project, add_shot, make_take, monkeypatch):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take.name)
    monkeypatch.chdir(tmp_project.root)

    result = CliRunner().invoke(app, ["qc", "brief", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["criteria"]["skill"] == "visual-qc-review"
    assert data["shots"][0]["shot"] == "S001"
    assert data["shots"][0]["take"] == take.name


def test_cli_verdict_from_stdin(tmp_project, add_shot, make_take, monkeypatch):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take.name)
    monkeypatch.chdir(tmp_project.root)

    payload = json.dumps({"verdicts": [
        {"shot": "S001", "take": take.name, "criterion": "F1", "level": "issue",
         "message": "画面文字为乱码"},
    ]})
    result = CliRunner().invoke(app, ["qc", "verdict", "--from-file", "-"], input=payload)
    assert result.exit_code == 0, result.output
    assert "已回填 1 条" in result.output
    assert agent_log_path(tmp_project).exists()

    # and it merges into a subsequent bare `manju qc` run as an [AI判读] item
    qc_run = CliRunner().invoke(app, ["qc", "--json"])
    report = json.loads(qc_run.output)
    assert any("[AI判读]" in i["message"] and "F1" in i["message"]
               for i in report["items"])


def test_cli_bare_qc_still_runs(tmp_project, add_shot, make_take, monkeypatch):
    """The refactor to a sub-app must keep `manju qc` (no subcommand) working."""
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take.name)
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["qc"])
    assert "reports/qc.md" in result.output


# ----------------------------------------------------------------- MCP


def test_mcp_qc_brief_and_verdict(tmp_project, add_shot, make_take):
    from manju.mcp.tools import call_tool, list_tools

    names = {t["name"] for t in list_tools()}
    assert {"qc_brief", "qc_verdict"} <= names

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S001", take.name)

    brief = call_tool(tmp_project, "qc_brief", {})
    assert brief["criteria"]["skill"] == "visual-qc-review"
    assert brief["shots"][0]["shot"] == "S001"

    out = call_tool(tmp_project, "qc_verdict", {"verdicts": [
        {"shot": "S001", "take": take.name, "criterion": "H4", "level": "blocker",
         "message": "叉子没送到嘴里"},
    ]})
    assert out["written"] == 1

    items = agent_verdict_items(tmp_project)
    assert any("[AI判读]" in i.message and "H4" in i.message and i.level == "error"
               for i in items)


def test_mcp_qc_verdict_unknown_shot_is_tool_error(tmp_project, add_shot):
    from manju.mcp.tools import ToolError, call_tool

    add_shot(tmp_project, "S001")
    with pytest.raises(ToolError):
        call_tool(tmp_project, "qc_verdict", {"verdicts": [
            {"shot": "NOPE", "level": "fyi", "message": "x"},
        ]})
