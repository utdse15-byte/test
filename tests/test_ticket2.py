"""FIX-C (secret scanning), FIX-D (clean error presentation), FIX-E (pack
preserves the project name) + make_sample argparse. Red-first."""

from __future__ import annotations

import subprocess
import sys

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.check import run_check

# ---------------------------------------------------------------- FIX-C ----

# red-team corpus: every entry MUST be caught (the first one is the review's
# concrete miss: unquoted assignment + hyphenated sk-proj token)
CAUGHT = [
    "api_key=sk-proj-AbCdEf1234567890GhIjKl",          # unquoted + sk-proj-
    "sk-abcdefghijklmnopqrst123456",                    # classic sk- (pin)
    "openai_key: 'sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX'",   # quoted sk-proj-
    "token = ghp_AbCdEfGhIjKlMnOpQrStUvWxYz012345",     # GitHub PAT
    "gho_AbCdEfGhIjKlMnOpQrStUvWxYz012345",             # GitHub OAuth
    "slack: xoxb-1234567890-9876543210-AbCdEfGhIjKl",   # Slack bot token
    "AIzaSyA1234567890abcdefghijklmnopqrstuv",          # Google API key (pin)
    "AKIAIOSFODNN7EXAMPLE",                             # AWS (pin)
    "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc",  # long bearer
    'access_token = "0123456789abcdef0123456789abcdef"',  # quoted assignment (pin)
    "secret_key: 0123456789abcdef01234567",             # unquoted yaml assignment
]

# these must NOT trip the scanner (false-positive guards)
CLEAN = [
    "台词:这不可能。密钥永远不进项目目录(§8.2)。",
    "auth: {key_env: VIDEO_X_API_KEY}",                 # env var NAME is fine
    'header: "Authorization: Bearer {key}"',            # template placeholder
    "sk-… 与 AKIA… 等模式会被扫描",                       # prose mention
    "the task_id was job_8f2c91 and cost 0.32 CNY",
]


@pytest.mark.parametrize("sample", CAUGHT)
def test_secret_scan_catches(tmp_project, sample):
    target = tmp_project.root / "story" / "brief.md"
    target.write_text(f"一句话创意\n{sample}\n", encoding="utf-8")
    report = run_check(tmp_project)
    assert any("API key/secret" in e for e in report.errors), f"missed: {sample}"


@pytest.mark.parametrize("sample", CLEAN)
def test_secret_scan_clean_samples_pass(tmp_project, sample):
    target = tmp_project.root / "story" / "brief.md"
    target.write_text(f"一句话创意\n{sample}\n", encoding="utf-8")
    report = run_check(tmp_project)
    assert report.ok, f"false positive on: {sample!r} -> {report.errors}"


# ---------------------------------------------------------------- FIX-D ----


def test_check_fails_cleanly_on_broken_props_yaml(tmp_project, monkeypatch):
    """Review repro 1: an illegal line in props.yaml must produce a one-line
    diagnostic (file + reason), nonzero exit, and NO traceback."""
    props = tmp_project.root / "bible" / "props.yaml"
    props.write_text("future_coin:\n  year: 2036\n\t\tbroken: [unclosed\n",
                     encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["check"])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"uncaught exception leaked: {result.exception!r}"
    )
    assert "props.yaml" in result.output
    assert "Traceback" not in result.output


def test_build_fails_cleanly_on_invalid_manual_timeline(tmp_project, add_shot,
                                                        make_take, monkeypatch):
    """Review repro 2: rules.mode=manual + timeline.json is invalid JSON →
    build must fail with a clean diagnostic, not a JSONDecodeError."""
    from manju.core.spec import compute_spec_hash

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001",
                     compute_spec_hash(shot, tmp_project.load_bible()))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    rules = tmp_project.load_rules()
    rules.mode = "manual"
    tmp_project.save_rules(rules)
    tmp_project.timeline_path.write_text("{not valid json", encoding="utf-8")

    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["build", "--target", "qc"])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"uncaught exception leaked: {result.exception!r}"
    )
    assert "timeline.json" in result.output
    assert "Traceback" not in result.output


# ---------------------------------------------------------------- FIX-E ----


def test_pack_preserves_original_name(tmp_project, monkeypatch, tmp_path):
    """雨夜便利店.manju → arbitrary.manjupkg → unpack restores 雨夜便利店.manju."""
    monkeypatch.chdir(tmp_project.root)
    archive = tmp_path / "arbitrary-name.manjupkg"
    result = CliRunner().invoke(app, ["pack", "--out", str(archive)])
    assert result.exit_code == 0, result.output

    out_dir = tmp_path / "restore"
    out_dir.mkdir()
    monkeypatch.chdir(out_dir)
    result = CliRunner().invoke(app, ["unpack", str(archive)])
    assert result.exit_code == 0, result.output
    restored = out_dir / tmp_project.root.name  # 雨夜便利店.manju
    assert restored.is_dir(), sorted(p.name for p in out_dir.iterdir())
    assert (restored / "project.yaml").exists()


def test_unpack_dest_overrides_name(tmp_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_project.root)
    archive = tmp_path / "x.manjupkg"
    assert CliRunner().invoke(app, ["pack", "--out", str(archive)]).exit_code == 0
    dest = tmp_path / "自定义名字.manju"
    result = CliRunner().invoke(app, ["unpack", str(archive), "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert (dest / "project.yaml").exists()


# ------------------------------------------------------- make_sample CLI ----


def test_make_sample_help_has_no_side_effects(tmp_path, monkeypatch):
    """--help must print usage and create nothing (argparse conversion)."""
    monkeypatch.chdir(tmp_path)
    proc = subprocess.run(
        [sys.executable, "tests/fixtures/make_sample.py", "--help"],
        capture_output=True, text=True, cwd="/home/user/test",
    )
    assert proc.returncode == 0
    assert "usage" in proc.stdout.lower()
    assert list(tmp_path.iterdir()) == []


def test_make_sample_argparse_options(tmp_path):
    proc = subprocess.run(
        [sys.executable, "tests/fixtures/make_sample.py", str(tmp_path / "mini"),
         "--shots", "2", "--clip-seconds", "0.5", "--no-bgm"],
        capture_output=True, text=True, cwd="/home/user/test",
    )
    assert proc.returncode == 0, proc.stderr
    from manju.core.container import Project

    root = next((tmp_path / "mini").glob("*.manju"))
    project = Project(root)
    assert len(project.shot_ids()) == 2
    assert project.load_rules().music.source is None
