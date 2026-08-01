"""返工自由度波(2026-07-31):店主要「实际使用时不断调整返工」的操作空间。

实地演练 A–E 组后,绝大多数返工动作都很自由(take 来回切、rollback、手动
接管再换回、删/插/改序镜头、改帧率画幅、compare 逐项报差异)。两处例外,
本文件钉住:

1. **可行动的提示在任何面上都看不见。** 把项目从 1080x1920 改成 1920x1080
   之后,QC 逐镜记下「take resolution 1080x1920 differs from project …」
   并给出确切修复命令(`manju repair --op croppad …`)—— 但它是 `info` 级,
   而 `manju qc` 的汇总只数 errors/warnings,`status` 的 QC 行同样只有两个
   数,`build` 只印「QC: 通过」。实测:13 条分辨率建议 + 3 条音频建议全部
   静默,店主看到的是「完成 ✅」。返工的前提是知道自己有哪些选项。
   区分器是诚实的:**info 且带 suggestion = 可行动**;不带 suggestion 的
   info(如「mid-point frame for visual review」逐镜记账)不计入。

2. **Ctrl-C 之后一片沉默。** 中断长构建实测 rc=130、无栈、状态干净(check
   过、锁已释放)——但**输出零行**:店主不知道已产出的 take 是否保留、要不要
   从头再来。引擎自己有很好的取消话术(「已取消:N/M …已产出的 take 不受
   影响」),只是 SIGINT 这条路没接。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest

from manju.core.yamlio import write_yaml


# ---------------------------------------------------- 可行动提示的可见性


def _report_with(levels_and_suggestions):
    from manju.qc.checks import QCReport

    report = QCReport()
    for level, suggestion in levels_and_suggestions:
        report.add(level, "technical", "S001", "msg", suggestion=suggestion)
    return report


def test_actionable_notes_counts_info_with_a_suggestion_only():
    from manju.qc.checks import actionable_notes

    report = _report_with([
        ("info", "manju repair --op croppad …"),   # actionable
        ("info", ""),                              # bookkeeping (frame line)
        ("warn", "already counted as a warning"),
        ("error", "already counted as an error"),
    ])
    assert actionable_notes(report.items) == 1


def test_actionable_notes_accepts_dicts_too():
    """status reads qc.json (dicts), the CLI holds QCItem objects — ONE
    counter serves both so the two surfaces can never disagree."""
    from manju.qc.checks import actionable_notes

    rows = [
        {"level": "info", "suggestion": "do this"},
        {"level": "info", "suggestion": ""},
        {"level": "info"},
        {"level": "warn", "suggestion": "x"},
    ]
    assert actionable_notes(rows) == 1


def test_qc_summary_line_names_actionable_notes(tmp_project, monkeypatch, capsys):
    from typer.testing import CliRunner

    from manju.cli import app

    runner = CliRunner()
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["qc"])
    # the line must be able to carry the third number; on an empty project it
    # is zero, so pin the SHAPE here and the count in the field test below
    assert "errors" in result.output and "warnings" in result.output
    assert "提示" in result.output, result.output


def test_status_qc_block_carries_the_note_count(tmp_project):
    from manju.build.status import project_status

    qc_json = tmp_project.reports_dir / "qc.json"
    qc_json.parent.mkdir(parents=True, exist_ok=True)
    qc_json.write_text(json.dumps({"ok": True, "items": [
        {"level": "info", "area": "technical", "subject": "S001",
         "message": "take resolution 1080x1920 differs…",
         "suggestion": "manju repair --op croppad --shot S001 --mode center_crop"},
        {"level": "info", "area": "content", "subject": "S001",
         "message": "mid-point frame for visual review: …", "suggestion": ""},
        {"level": "warn", "area": "technical", "subject": "S002",
         "message": "w", "suggestion": ""},
    ]}), encoding="utf-8")
    info = project_status(tmp_project)
    assert info["qc"]["errors"] == 0
    assert info["qc"]["warnings"] == 1
    assert info["qc"]["notes"] == 1, "actionable notes missing from status"


@pytest.mark.ffmpeg
def test_changing_the_aspect_surfaces_the_reauthor_advice(tmp_project, add_shot):
    """The field scenario end to end: a project whose takes were authored at
    the old geometry must SAY so somewhere the owner actually looks."""
    import yaml

    add_shot(tmp_project, "S001")
    subprocess.run([sys.executable, "-m", "manju.cli", "build", "--yes"],
                   cwd=tmp_project.root, capture_output=True, text=True,
                   env={**os.environ, "PYTHONUTF8": "1"}, timeout=900)

    cfg_path = tmp_project.root / "project.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["width"], cfg["height"] = cfg["height"], cfg["width"]  # portrait ↔ landscape
    write_yaml(cfg_path, cfg)

    proc = subprocess.run([sys.executable, "-m", "manju.cli", "qc"],
                          cwd=tmp_project.root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONUTF8": "1"}, timeout=900)
    assert "提示" in proc.stdout
    # …and the count is not zero: the resolution advisory is exactly the kind
    # of note this wave exists to surface
    line = next(ln for ln in proc.stdout.splitlines() if "提示" in ln)
    assert "0 " not in line.split("提示")[0].split(",")[-1], line


# ------------------------------------------------------------ Ctrl-C 话术


def _interrupt_build(project_root, after_s: float):
    proc = subprocess.Popen(
        [sys.executable, "-m", "manju.cli", "build", "--force", "--yes"],
        cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    time.sleep(after_s)
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
    out, _ = proc.communicate(timeout=300)
    return proc.returncode, out


@pytest.mark.parametrize("verb", ["build", "redo", "voice"])
def test_ctrl_c_is_answered_on_every_platform(tmp_project, monkeypatch, verb):
    """The proposition — Ctrl-C says what survived and exits 130 — tested by
    raising the interrupt where the CLI actually catches it. Portable on
    purpose: the subprocess-signal version below cannot run on Windows, which
    is the owner's PRIMARY platform, so the behaviour they will really hit
    must be covered by something that runs there."""
    import manju.build.graph as graph
    from typer.testing import CliRunner

    from manju.cli import app

    def _boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(graph, {"build": "run_build", "redo": "redo_batch",
                                "voice": "voice_batch"}[verb], _boom)
    monkeypatch.chdir(tmp_project.root)
    args = {"build": ["build", "--yes"],
            "redo": ["redo", "--shots", "S001", "--yes"],
            "voice": ["voice", "--shots", "S001", "--yes"]}[verb]
    result = CliRunner().invoke(app, args)

    assert result.exit_code == 130, result.output
    out = result.output
    assert "已取消" in out, f"Ctrl-C stayed silent — the owner learns nothing: {out!r}"
    assert f"manju {verb}" in out         # …and how to resume
    assert "只增" in out or "保留" in out   # …and that finished work survived


@pytest.mark.ffmpeg
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows consoles deliver Ctrl-C as CTRL_C_EVENT to a process "
           "GROUP; Popen.send_signal(SIGINT) raises ValueError there, and "
           "CTRL_BREAK_EVENT would land as SIGBREAK — a different signal from "
           "the one the owner's Ctrl-C actually produces, so simulating it "
           "would test a different proposition. The behaviour itself is "
           "covered on every platform by "
           "test_ctrl_c_is_answered_on_every_platform above.")
def test_ctrl_c_on_a_real_build_process(tmp_project, add_shot):
    """The end-to-end evidence where the OS supports the simulation: a REAL
    build subprocess, a REAL SIGINT, and the state left behind."""
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    rc, out = _interrupt_build(tmp_project.root, 2.5)
    if rc == 0:
        pytest.skip("build finished before the interrupt landed")
    assert rc == 130, f"SIGINT must exit 130, got {rc}: {out}"
    assert "Traceback" not in out
    assert "已取消" in out, f"Ctrl-C stayed silent — the owner learns nothing: {out!r}"
    assert "manju build" in out          # …and how to resume
    assert "只增" in out or "保留" in out  # …and that finished work survived


def test_the_cancel_line_is_one_owner():
    """build / redo / voice must not each invent their own wording."""
    import inspect

    from manju import cli

    assert callable(cli._interrupted_message)
    text = cli._interrupted_message("build")
    assert "已取消" in text and "manju build" in text
    for verb in ("build", "redo", "voice"):
        src = inspect.getsource(getattr(cli, verb))
        assert "KeyboardInterrupt" in src, f"{verb} does not catch Ctrl-C"
        assert "_interrupted_message" in src, f"{verb} invents its own cancel text"
