"""FIX-C (secret scanning), FIX-D (clean error presentation), FIX-E (pack
preserves the project name) + make_sample argparse. Red-first."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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


# ---------------------------------------------------------------- goal item 21


def test_secret_scan_no_longer_skips_media_directory(tmp_project):
    """media/ used to be skipped WHOLESALE — a secret in a text sidecar/notes
    file under media/refs or media/imports slipped past `manju check`
    entirely. Only .git/.manju/renders/ stay skipped now; binary media is
    excluded by SUFFIX, not by living under media/."""
    notes = tmp_project.root / "media" / "refs" / "notes.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("api_key=sk-proj-AbCdEf1234567890GhIjKl\n", encoding="utf-8")
    report = run_check(tmp_project)
    assert any("API key/secret" in e for e in report.errors)


def test_secret_scan_still_skips_binary_media_by_suffix(tmp_project):
    """Pulling media/ back into scope must not start reading actual media
    bytes as text — only the already-covered text suffixes get scanned."""
    media = tmp_project.root / "media" / "imports" / "clip.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"sk-proj-AbCdEf1234567890GhIjKl" * 3)  # secret-shaped, wrong suffix
    report = run_check(tmp_project)
    assert not any("clip.mp4" in e for e in report.errors)


def test_secret_scan_bounds_large_text_file_size(tmp_project):
    """A per-file size bound keeps the now-larger scan surface (media/ back
    in scope) fast — an oversized text file is skipped rather than read in
    full; `check` must simply not hang or crash on it."""
    from manju.core.check import MAX_SCAN_BYTES

    big = tmp_project.root / "media" / "imports" / "huge_notes.txt"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_text("x" * (MAX_SCAN_BYTES + 1000), encoding="utf-8")
    report = run_check(tmp_project)  # must simply not hang/crash
    assert isinstance(report.ok, bool)


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


def test_unpack_default_dest_follows_archive_filename_not_comment(
    tmp_project, monkeypatch, tmp_path
):
    """Round W (goal item 20, supersedes the old FIX-E "preserves original
    name" contract): the zip comment's embedded project name is
    archive-controlled content and is no longer trusted for path purposes —
    a malicious .manjupkg could carry a comment name like ``../../etc`` or an
    absolute path to steer the default restore location. The default restore
    directory now comes from the ARCHIVE'S OWN FILENAME (sanitized), so
    renaming the .manjupkg changes the default restore name too."""
    monkeypatch.chdir(tmp_project.root)
    archive = tmp_path / "arbitrary-name.manjupkg"
    result = CliRunner().invoke(app, ["pack", "--out", str(archive)])
    assert result.exit_code == 0, result.output

    out_dir = tmp_path / "restore"
    out_dir.mkdir()
    monkeypatch.chdir(out_dir)
    result = CliRunner().invoke(app, ["unpack", str(archive)])
    assert result.exit_code == 0, result.output
    restored = out_dir / "arbitrary-name.manju"  # from the ARCHIVE filename now
    assert restored.is_dir(), sorted(p.name for p in out_dir.iterdir())
    assert (restored / "project.yaml").exists()
    # the comment-embedded original name must NOT be used for the path
    assert not (out_dir / tmp_project.root.name).exists()


def test_unpack_ignores_malicious_comment_name_for_dest(tmp_project, monkeypatch, tmp_path):
    """The concrete attack (goal item 20): a hand-crafted zip comment naming a
    path-like project name must NOT steer where an unpack (without --dest)
    lands — only the archive's own filename does."""
    import json
    import zipfile

    monkeypatch.chdir(tmp_project.root)
    archive = tmp_path / "evil.manjupkg"
    result = CliRunner().invoke(app, ["pack", "--out", str(archive)])
    assert result.exit_code == 0, result.output

    # tamper the comment the same way `pack` would have written it, but with
    # a path-traversal-shaped "name"
    with zipfile.ZipFile(archive, "a") as zf:
        zf.comment = json.dumps(
            {"manjupkg": 1, "name": "../../../../tmp/manju_pwned"}, ensure_ascii=False
        ).encode("utf-8")

    out_dir = tmp_path / "restore2"
    out_dir.mkdir()
    monkeypatch.chdir(out_dir)
    result = CliRunner().invoke(app, ["unpack", str(archive)])
    assert result.exit_code == 0, result.output
    # lands next to the archive-derived name, INSIDE out_dir — never escapes
    restored = out_dir / "evil.manju"
    assert restored.is_dir()
    assert (restored / "project.yaml").exists()
    # nothing was ever written at the attacker-chosen path
    assert not Path("/tmp/manju_pwned").exists()


def test_unpack_dest_overrides_name(tmp_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_project.root)
    archive = tmp_path / "x.manjupkg"
    assert CliRunner().invoke(app, ["pack", "--out", str(archive)]).exit_code == 0
    dest = tmp_path / "自定义名字.manju"
    result = CliRunner().invoke(app, ["unpack", str(archive), "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert (dest / "project.yaml").exists()


# ------------------------------------------------------- make_sample CLI ----


# The repo root derived from THIS file — never hardcode the checkout path
# (a baked-in dev path made these two tests CI-only failures).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_MAKE_SAMPLE = _REPO_ROOT / "tests" / "fixtures" / "make_sample.py"


def test_make_sample_help_has_no_side_effects(tmp_path, monkeypatch):
    """--help must print usage and create nothing (argparse conversion)."""
    monkeypatch.chdir(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(_MAKE_SAMPLE), "--help"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0
    assert "usage" in proc.stdout.lower()
    assert list(tmp_path.iterdir()) == []


def test_make_sample_argparse_options(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_MAKE_SAMPLE), str(tmp_path / "mini"),
         "--shots", "2", "--clip-seconds", "0.5", "--no-bgm"],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    from manju.core.container import Project

    root = next((tmp_path / "mini").glob("*.manju"))
    project = Project(root)
    assert len(project.shot_ids()) == 2
    assert project.load_rules().music.source is None
