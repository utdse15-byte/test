"""`manju auto` drives ANY one-shot agent CLI (§10) — the resolver, the
template grammar, and the subprocess contract (playbook prepended, actor=ai,
exit code propagated), proven with a fake agent script so no real agent CLI
is needed.
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from typer.testing import CliRunner

from manju.agents import (
    KNOWN_AGENTS,
    AgentResolutionError,
    build_command,
    resolve_agent,
)
from manju.cli import app

runner = CliRunner()


# ---------------------------------------------------------------- resolver


def test_flag_wins_over_everything(monkeypatch):
    monkeypatch.setenv("MANJU_AGENT", "gemini")
    assert resolve_agent("codex", "aider") == KNOWN_AGENTS["aider"]


def test_env_beats_project(monkeypatch):
    monkeypatch.setenv("MANJU_AGENT", "qwen")
    assert resolve_agent("codex", None) == KNOWN_AGENTS["qwen"]


def test_project_field_used(monkeypatch):
    monkeypatch.delenv("MANJU_AGENT", raising=False)
    assert resolve_agent("codex", None) == KNOWN_AGENTS["codex"]


def test_custom_template_passes_through(monkeypatch):
    monkeypatch.delenv("MANJU_AGENT", raising=False)
    assert resolve_agent(None, "myagent --task {prompt} --yolo") \
        == "myagent --task {prompt} --yolo"


def test_path_probe_first_known(monkeypatch):
    monkeypatch.delenv("MANJU_AGENT", raising=False)
    monkeypatch.setattr("manju.agents.shutil.which",
                        lambda name: "/bin/x" if name == "gemini" else None)
    assert resolve_agent(None, None) == KNOWN_AGENTS["gemini"]


def test_nothing_found_lists_the_options(monkeypatch):
    monkeypatch.delenv("MANJU_AGENT", raising=False)
    monkeypatch.setattr("manju.agents.shutil.which", lambda name: None)
    with pytest.raises(AgentResolutionError) as exc:
        resolve_agent(None, None)
    msg = str(exc.value)
    for name in KNOWN_AGENTS:
        assert name in msg
    assert "serve-mcp" in msg  # the structured path is named


# ----------------------------------------------------------- template → argv


def test_prompt_is_one_argument_never_split():
    argv = build_command("claude -p {prompt}", "多词 提示 '带引号'")
    assert argv == ["claude", "-p", "多词 提示 '带引号'"]


def test_prompt_appended_when_no_placeholder():
    assert build_command("aider --message", "任务") == ["aider", "--message", "任务"]


def test_quoted_template_words_survive():
    argv = build_command('run --flag "two words" {prompt}', "p")
    assert argv == ["run", "--flag", "two words", "p"]


# -------------------------------------------------- subprocess contract (e2e)


def test_auto_runs_fake_agent_with_playbook_and_ai_actor(tmp_project, tmp_path,
                                                         monkeypatch):
    """A fake agent script records its argv + env; auto must hand it the
    composed playbook prompt, MANJU_ACTOR=ai, and propagate its exit code."""
    record = tmp_path / "record.json"
    # Windows gate round 3: a bare #!/bin/sh file is not executable there
    # ('%1 is not a valid Win32 application') — the fake agent is a .cmd on
    # nt so the PRIMARY platform gets real coverage, not a skip.
    if os.name == "nt":
        fake = tmp_path / "fakeagent.cmd"
        fake.write_text(
            "@echo off\r\n"
            f"<nul set /p=\"%~1\" > \"{record}.prompt\"\r\n"
            f"<nul set /p=\"%MANJU_ACTOR%\" > \"{record}.actor\"\r\n"
            "exit /b 7\r\n",
            encoding="utf-8",
        )
    else:
        fake = tmp_path / "fakeagent"
        fake.write_text(
            "#!/bin/sh\n"
            f"printf '%s' \"$1\" > {record}.prompt\n"
            f"printf '%s' \"$MANJU_ACTOR\" > {record}.actor\n"
            "exit 7\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    monkeypatch.chdir(tmp_project.root)
    monkeypatch.delenv("MANJU_AGENT", raising=False)
    result = runner.invoke(app, ["auto", "把第一镜重做一遍",
                                 "--agent", f"{fake} {{prompt}}"])
    assert result.exit_code == 7  # the agent's exit code propagates

    prompt = (tmp_path / "record.json.prompt").read_text(encoding="utf-8")
    assert "把第一镜重做一遍" in prompt
    assert "manju" in prompt  # some playbook text was prepended
    assert (tmp_path / "record.json.actor").read_text(encoding="utf-8") == "ai"

    events = json.loads(
        (tmp_project.root / "events.jsonl").read_text(encoding="utf-8")
        .strip().splitlines()[-1]
    )
    assert events["action"] == "auto"
    assert events["detail"]["agent"] == str(fake)


def test_auto_event_never_carries_the_full_prompt(tmp_project, tmp_path, monkeypatch):
    """goal item 49: events.jsonl (project truth, git-snapshotted) must never
    carry the FULL prompt — only a sha256 (still provable) + a short preview.
    A prompt with something secret-shaped in it must not leak into the log."""
    import hashlib

    if os.name == "nt":
        fake = tmp_path / "fakeagent2.cmd"
        fake.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    else:
        fake = tmp_path / "fakeagent2"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    monkeypatch.chdir(tmp_project.root)
    monkeypatch.delenv("MANJU_AGENT", raising=False)
    # filler padded well past the 80-char preview boundary, THEN a
    # secret-shaped token — proves truncation, not just hashing (a secret
    # sitting in the first 80 chars is, by the review's own spec, still a
    # preview char like any other; what must never happen is the FULL text,
    # including anything past char 80, riding into the log).
    filler = "客户内部信息" * 20  # well over 80 chars
    secret_tail = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz"
    prompt = filler + " " + secret_tail
    assert prompt[:80].find(secret_tail) == -1  # sanity: secret is PAST char 80
    result = runner.invoke(app, ["auto", prompt, "--agent", f"{fake} {{prompt}}"])
    assert result.exit_code == 0

    raw_log = (tmp_project.root / "events.jsonl").read_text(encoding="utf-8")
    assert secret_tail not in raw_log

    events = json.loads(raw_log.strip().splitlines()[-1])
    detail = events["detail"]
    assert "prompt" not in detail  # the old full-text key is gone
    assert detail["prompt_sha256"] == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert detail["prompt_preview"] == prompt[:80]
    assert detail["prompt_len"] == len(prompt)


def test_auto_fails_cleanly_when_agent_missing(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    monkeypatch.delenv("MANJU_AGENT", raising=False)
    result = runner.invoke(app, ["auto", "x", "--agent",
                                 "definitely_not_installed_9x --go {prompt}"])
    assert result.exit_code == 1
    combined = result.output + (result.stderr or "")
    assert "definitely_not_installed_9x" in combined
