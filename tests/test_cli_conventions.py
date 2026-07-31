"""软能力波三(2026-07-31):三条被实地审计点名的 CLI 惯例。

1. **`-h` 不认**(`manju -h` → `No such option: -h`,退出 2)。几乎所有
   命令行工具都接受它,店主的手指也会先按它。
2. **`NO_COLOR` 被无视**(https://no-color.org)。pty 下实测 `NO_COLOR=1`
   照样吐 ANSI —— 这是最便宜的一分标准分。
3. **多分钟构建全程零反馈**:80 镜首建 11m57s,标准输出一个字都没有;
   引擎其实一直在发 `on_phase`(含 `gen:S003 (3/12)` 这种粒度),CLI
   从来没接线。进度必须走 **stderr 且仅在交互终端**,这样 stdout 的
   字节、`--json` 的信封、管道下游全都分毫不变。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

ANSI = re.compile(r"\x1b\[([0-9;]*)m")

# NO_COLOR (https://no-color.org) governs COLOUR, not text styling: "prevent
# the addition of ANSI color". Bold/dim/reset are not colour and legitimately
# survive — measured here, default vs NO_COLOR=1 on the same help screen:
#   default    → {1, 0, 2, 1;33, 1;32, 1;36}   (bold-yellow/green/cyan)
#   NO_COLOR=1 → {1, 0, 2}                     (every colour gone)
# Asserting "no ANSI at all" would be a WRONGER standard than the standard,
# and would push a future session to strip bold too.
_COLOUR_SGR = set(range(30, 38)) | set(range(40, 48)) | set(range(90, 98)) \
    | set(range(100, 108)) | {38, 48}


def has_colour(text: str) -> bool:
    for params in ANSI.findall(text):
        for part in params.split(";"):
            if part.isdigit() and int(part) in _COLOUR_SGR:
                return True
    return False


def _pty_capture(args: list[str], env_extra: dict[str, str], cwd=None) -> str:
    """`script(1)`-based pty capture — colour decisions only happen when the
    stream looks interactive, so a plain pipe would prove nothing about
    NO_COLOR. (`pty.spawn` cannot carry env, hence script(1).) ``cwd`` is
    passed to the CHILD: mutating this process's cwd would leak across
    xdist workers (pinned by test_fp_xdist.test_no_raw_chdir_in_tests)."""
    env = {**os.environ, "PYTHONUTF8": "1", **env_extra}
    proc = subprocess.run(
        ["script", "-qec", " ".join(["python", "-m", "manju.cli", *args]), "/dev/null"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=120, cwd=cwd,
    )
    return proc.stdout + proc.stderr


# ------------------------------------------------------------------ -h


@pytest.mark.parametrize("args", [["-h"], ["status", "-h"], ["build", "-h"]])
def test_dash_h_works_like_help(args):
    proc = subprocess.run(
        [sys.executable, "-m", "manju.cli", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"}, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No such option" not in (proc.stdout + proc.stderr)
    assert "Usage" in proc.stdout or "用法" in proc.stdout


def test_dash_h_and_help_agree():
    def _run(flag):
        return subprocess.run(
            [sys.executable, "-m", "manju.cli", "status", flag],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}, timeout=120,
        ).stdout
    assert _run("-h") == _run("--help")


# ------------------------------------------------------------- NO_COLOR


@pytest.mark.skipif(not os.path.exists("/usr/bin/script"),
                    reason="needs util-linux script(1) for a pty")
def test_no_color_is_honoured_on_a_real_terminal():
    coloured = _pty_capture(["--help"], {})
    plain = _pty_capture(["--help"], {"NO_COLOR": "1"})
    assert has_colour(coloured), "no colour even WITHOUT NO_COLOR — test is blind"
    assert not has_colour(plain), "NO_COLOR=1 still emitted colour escapes"


@pytest.mark.skipif(not os.path.exists("/usr/bin/script"),
                    reason="needs util-linux script(1) for a pty")
def test_no_color_applies_to_command_output_not_just_help(tmp_project):
    """`manju status` in a real project prints coloured prose through secho —
    the help screen is Rich's business, this is Click's."""
    plain = _pty_capture(["status"], {"NO_COLOR": "1"}, cwd=tmp_project.root)
    assert not has_colour(plain), "NO_COLOR=1 still coloured command output"


# --------------------------------------------------------------- progress


def test_progress_reporter_writes_to_stderr_only(capsys):
    from manju.cli import _build_progress_reporter

    report = _build_progress_reporter(as_json=False, force=True)
    assert report is not None
    report("check")
    report("gen:S003 (3/12)")
    captured = capsys.readouterr()
    assert captured.out == "", "progress leaked into stdout"
    assert "S003" in captured.err and "3/12" in captured.err


def test_progress_is_silent_under_json(capsys):
    from manju.cli import _build_progress_reporter

    assert _build_progress_reporter(as_json=True, force=True) is None


def test_progress_is_silent_when_not_interactive(capsys):
    """A redirected/piped run must stay byte-identical to before this wave."""
    from manju.cli import _build_progress_reporter

    assert _build_progress_reporter(as_json=False) is None  # pytest capture: not a tty


def test_progress_clears_its_line_when_finished(capsys):
    from manju.cli import _build_progress_reporter

    report = _build_progress_reporter(as_json=False, force=True)
    report("render")
    report.done()
    err = capsys.readouterr().err
    assert "render" in err                 # it did show something…
    assert err.endswith("\r")              # …and left the cursor at column 0
    tail = err.rsplit("\r", 2)[-2]         # what the final rewrite painted
    assert tail.strip() == "", f"the progress line was not blanked: {tail!r}"


def test_build_wires_the_reporter_into_the_engine():
    """The engine has emitted on_phase since UX wave 2; only the CLI never
    subscribed. Pin the wiring so it cannot be silently dropped."""
    import inspect

    from manju import cli

    src = inspect.getsource(cli.build)
    assert "on_phase=" in src, "manju build no longer passes on_phase"
    assert "_build_progress_reporter" in src
