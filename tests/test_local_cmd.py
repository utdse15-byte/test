"""Local-command adapter (round-q) — exercised with REAL subprocesses driving
tiny sh scripts (fast and honest, no mocking). Covers success + lineage, the
{out}/placeholder substitution (prompt as ONE argv element), env passthrough,
timeout, missing output, nonzero-exit stderr surfacing."""

from __future__ import annotations

import os
import signal
import stat
import time

import pytest

from manju.core.yamlio import write_yaml
from manju.providers.base import FailureKind, GenerationRequest, ProviderFailure
from manju.providers.local_cmd import LocalCommandProvider
from manju.providers.manifest import LOCAL_CMD_ADAPTER, ProviderManifest


def _script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _manifest(command, **overrides):
    lc = {"command": command, "timeout_s": 10.0, "output_ext": ".mp4"}
    lc.update(overrides)
    return ProviderManifest.model_validate({
        "id": "local_cmd", "type": "video", "adapter": LOCAL_CMD_ADAPTER,
        "capabilities": ["image_to_video"], "cost": {"per_call": 0.0, "currency": "CNY"},
        "local_cmd": lc,
    })


@pytest.fixture
def request_for(tmp_project, add_shot):
    def _make(shot_id="S001", duration_ms=4000, **gen):
        shot = add_shot(tmp_project, shot_id, generation={"candidates": 1, **gen})
        return GenerationRequest(
            project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
            spec_hash="sha256:test", duration_ms=duration_ms, candidates=1,
            params={"seed": 7},
        )
    return _make


def test_runs_registers_with_lineage_and_env(tmp_path, request_for):
    # writes the shot id (from MANJU_* env passthrough) into the {out} file
    script = _script(tmp_path, "gen.sh", 'printf "%s" "$MANJU_SHOT_ID" > "$2"\n')
    provider = LocalCommandProvider(_manifest(f"sh {script} --out {{out}}"))
    takes = provider.generate(request_for("S001"))
    assert len(takes) == 1
    sc = takes[0].sidecar
    # env passthrough reached the tool
    assert takes[0].media_path.read_text() == "S001"
    # lineage (§4.2): argv, exit code, duration
    assert sc.params["exit_code"] == 0
    assert sc.params["argv"][0] == "sh" and sc.params["argv"][-1].endswith("out.mp4")
    assert "--out" in sc.params["argv"]
    assert "duration_s" in sc.params
    assert sc.remote and sc.remote.cost == 0.0


def test_prompt_is_one_argv_element(tmp_path, request_for):
    script = _script(tmp_path, "gen.sh", 'echo ok > "$4"\n')  # write file to {out}
    provider = LocalCommandProvider(
        _manifest(f"sh {script} --prompt {{prompt}} --out {{out}}")
    )
    # prompt_override with spaces is passed through verbatim by compile_prompt
    takes = provider.generate(request_for("S002", prompt_override="alpha beta gamma"))
    argv = takes[0].sidecar.params["argv"]
    # the multi-word prompt is exactly ONE argv element (never word-split)
    assert "alpha beta gamma" in argv
    assert argv.index("--prompt") + 1 == argv.index("alpha beta gamma")


def test_placeholder_substitution_types(tmp_path, request_for):
    # {width}x{height} embeds into one word; {seed} is its own word
    script = _script(tmp_path, "gen.sh", 'echo ok > "$6"\n')
    provider = LocalCommandProvider(
        _manifest(f"sh {script} --size {{width}}x{{height}} --seed {{seed}} --out {{out}}")
    )
    argv = provider.generate(request_for("S003")).pop().sidecar.params["argv"]
    assert "1080x1920" in argv  # embedded placeholders -> one element
    assert argv[argv.index("--seed") + 1] == "7"


def test_placeholder_substitution_is_single_pass(tmp_path, request_for):
    """round-W #58: a compiled prompt that happens to CONTAIN literal
    "{seed}"/"{out}" text must never be re-scanned by a later placeholder's
    substitution — the template is scanned ONCE, left to right; only real
    placeholders from the ORIGINAL template are replaced."""
    script = _script(tmp_path, "gen.sh", 'echo ok > "$6"\n')
    provider = LocalCommandProvider(
        _manifest(f"sh {script} --prompt {{prompt}} --seed {{seed}} --out {{out}}")
    )
    # the prompt LITERALLY contains "{seed}" and "{out}" as text — these must
    # survive verbatim in the prompt argv element, never turn into the real
    # seed value or output path.
    prompt_text = "一个装着{seed}和{out}字样的场景描述"
    takes = provider.generate(request_for("S005", prompt_override=prompt_text))
    argv = takes[0].sidecar.params["argv"]
    assert prompt_text in argv  # the whole compiled prompt survives untouched
    # the REAL --seed argv element (a separate word) is still substituted
    assert argv[argv.index("--seed") + 1] == "7"


def test_nonzero_exit_surfaces_stderr_tail(tmp_path, request_for):
    script = _script(tmp_path, "fail.sh", 'echo "kaboom detail" >&2\nexit 3\n')
    provider = LocalCommandProvider(_manifest(f"sh {script} {{out}}"))
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S004"))
    assert exc.value.kind is FailureKind.provider_error
    assert "exited 3" in str(exc.value) and "kaboom detail" in str(exc.value)


def test_missing_output_is_provider_error(tmp_path, request_for):
    script = _script(tmp_path, "noop.sh", 'echo hi\nexit 0\n')  # writes nothing to {out}
    provider = LocalCommandProvider(_manifest(f"sh {script} {{out}}"))
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S005"))
    assert exc.value.kind is FailureKind.provider_error
    assert "no output" in str(exc.value)


def test_timeout_path(tmp_path, request_for):
    script = _script(tmp_path, "slow.sh", 'sleep 10\n: > "$1"\n')
    provider = LocalCommandProvider(_manifest(f"sh {script} {{out}}", timeout_s=0.5))
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S006"))
    assert exc.value.kind is FailureKind.timeout
    assert "timed out" in str(exc.value)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:  # exists but not ours (should not happen here)
        return True


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="POSIX process groups required")
def test_timeout_reaps_orphaned_grandchild(tmp_path, request_for):
    """F1: on timeout the WHOLE process group dies. A wrapper command that forks
    a grandchild (a local SVD/Wan worker holding VRAM) must leave that grandchild
    DEAD, never orphaned. RED at HEAD: subprocess.run kills only the DIRECT child
    (the wrapper sh), so the backgrounded `sleep` grandchild survives, reparented
    to init and still running past timeout_s."""
    pidfile = tmp_path / "grandchild.pid"
    # a wrapper that forks a long-lived grandchild in the SAME session/group
    # (non-interactive sh has no job control, so `&` stays in the shell's group)
    # and records its pid, then waits — the whole tree outlives the 0.5s budget.
    script = _script(tmp_path, "wrapper.sh",
                     f'sleep 30 &\n'
                     f'echo $! > "{pidfile}"\n'
                     f'wait\n')
    provider = LocalCommandProvider(_manifest(f"sh {script} {{out}}", timeout_s=0.5))
    gpid = None
    try:
        with pytest.raises(ProviderFailure) as exc:
            provider.generate(request_for("S001"))
        # EXACT ProviderFailure(timeout) surface is preserved
        assert exc.value.kind is FailureKind.timeout
        assert "timed out" in str(exc.value)

        assert pidfile.exists(), "wrapper never recorded the grandchild pid"
        gpid = int(pidfile.read_text().strip())
        # killpg reaped the whole group, not just the wrapper — give the OS a
        # beat to finish reaping the reparented grandchild.
        deadline = time.time() + 5.0
        while _pid_alive(gpid) and time.time() < deadline:
            time.sleep(0.05)
        assert not _pid_alive(gpid), (
            f"grandchild {gpid} survived the timeout — it was orphaned (F1 leak)")
    finally:
        # hygiene: never leak a 30s straggler if the fix ever regresses.
        if gpid is not None and _pid_alive(gpid):
            try:
                os.kill(gpid, signal.SIGKILL)
            except OSError:
                pass


def test_missing_binary_is_invalid(request_for):
    provider = LocalCommandProvider(_manifest("definitely_not_a_binary_xyz {out}"))
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(request_for("S007"))
    assert exc.value.kind is FailureKind.invalid


def test_construction_requires_out_placeholder():
    with pytest.raises(ValueError, match="{out}"):
        LocalCommandProvider(_manifest("echo hello"))


# --------------------------------------------------- registry integration

def test_fallback_chain_routes_capability(tmp_path, monkeypatch, tmp_project, add_shot):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path))
    write_yaml(tmp_path / "svd" / "provider.yaml", {
        "id": "svd_local", "type": "video", "adapter": LOCAL_CMD_ADAPTER,
        "capabilities": ["image_to_video"], "cost": {"per_call": 0.0},
        "local_cmd": {"command": "sh gen.sh --out {out}"},
    })
    from manju.providers.registry import fallback_chain, get_provider, manifest_errors

    assert manifest_errors() == []
    shot = add_shot(tmp_project, "S010",
                    generation={"fallback": ["image_to_video", "caption_card"]})
    chain = fallback_chain(shot)
    assert chain[0] == "svd_local" and chain[-1] == "caption_card"
    assert isinstance(get_provider("svd_local"), LocalCommandProvider)


def test_doctor_probe_flags_missing_binary_and_bad_command():
    # validate_for_generic is what `manju doctor` runs per manifest (§8.6)
    missing = _manifest("definitely_not_a_binary_xyz {out}")
    assert any("not found on PATH" in p for p in missing.validate_for_generic())
    no_out = ProviderManifest.model_validate({
        "id": "x", "adapter": LOCAL_CMD_ADAPTER, "local_cmd": {"command": "echo hi"},
    })
    assert any("{out}" in p for p in no_out.validate_for_generic())
