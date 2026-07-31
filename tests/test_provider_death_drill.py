"""战役② 供应商中途真死(店主核准的真实使用战役,2026-07-31)。

Field method: a REAL local HTTP TTS (generic_tts shape, priced 1.5/call)
that serves one good WAV then dies mid-request (os._exit inside the
handler), plus connection-refused after death. The paid-safety core passed
every probe verbatim — OUTCOME_UNKNOWN fail-closed with two recovery
commands, abandon-with-reason, the unresolved submission SURVIVES
`rebuild-index`, spend counted exactly the successful calls. What failed
is the reporting shell around it, fixed here:

- the generic_tts scaffold taught `{prompt}` — a placeholder the TTS
  adapter REJECTS (its set is text/speaker/tone/voice/...); following the
  scaffold verbatim failed every shot;
- a batch that FAILED shots exited 0 — automation can't see failure;
- real provider failures never reached the failures store ("暂无失败记录
  一切顺利" right after two of them) — though `voice` is a canonical step
  in the store's own schema;
- the single-shot path printed a 143-line traceback wall for BOTH
  branchable conditions (DR06 unknown-submission block, network death)
  that the batch path renders as one clean line;
- `providers check` answered "no such provider" for a manifest that EXISTS
  but fails validation (`providers list` names the real error).
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from manju.build.graph import BatchResult
from manju.core.failures import read_failures
from manju.core.models import ShotSpec

runner = CliRunner()


def _dialogued(project, sid="S001", text="演习台词。"):
    shot = ShotSpec.model_validate({
        "id": sid, "scene": "", "duration": 2.0,
        "dialogue": {"speaker": "老板", "text": text},
    })
    project.save_shot(shot)
    index = project.load_index()
    if sid not in index.order:
        index.order.append(sid)
    project.save_index(index)
    return shot


def _refused_manifest(tmp_path, monkeypatch, pid="deadtts", per_call=0.0):
    d = tmp_path / "providers" / pid
    d.mkdir(parents=True)
    (d / "provider.yaml").write_text(
        f"""\
id: {pid}
type: tts
adapter: generic_tts
disabled: false
capabilities: [tts]
auth:
  key_env: DEADTTS_API_KEY
  header: 'Authorization: Bearer {{key}}'
submit:
  url: http://127.0.0.1:1/tts
  method: POST
  body_template:
    text: '{{text}}'
  job_id_path: $.id
tts:
  audio_b64_path: $.audio_b64
  audio_format: wav
  language: zh
cost:
  per_call: {per_call}
  currency: CNY
""", encoding="utf-8")
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "providers"))
    monkeypatch.setenv("DEADTTS_API_KEY", "drill")


# ------------------------------------------------ scaffold teaches truth


def test_generic_tts_scaffold_teaches_a_placeholder_that_exists():
    from manju.providers.manifest import GENERIC_TTS_ADAPTER, scaffold_template

    text = scaffold_template("cloudtts", "tts", GENERIC_TTS_ADAPTER)
    assert "{prompt}" not in text  # the video vocabulary the TTS adapter rejects
    assert "'{text}'" in text      # the line to speak, in the adapter's own set


def test_generic_tts_scaffold_is_honest_about_job_id_path():
    from manju.providers.manifest import GENERIC_TTS_ADAPTER, scaffold_template

    text = scaffold_template("cloudtts", "tts", GENERIC_TTS_ADAPTER)
    # the field is REQUIRED by the schema even for sync APIs — the comment
    # must not claim it is async-only (following it, a sync-API owner
    # deletes the line and the manifest stops validating)
    assert "job_id_path" in text
    assert "only used by async" not in text
    assert "必填" in text


# ------------------------------------------------- batch exit code honesty


def _batch(failed=(), ran=(), canceled=False):
    r = BatchResult(requested=[*ran, *(f for f in failed)])
    r.ran = list(ran)
    r.failed = [{"shot": s, "reason": "network error calling …"} for s in failed]
    r.takes = {s: [f"voice_take_01"] for s in ran}
    r.canceled = canceled
    return r


@pytest.mark.parametrize("as_json", [False, True])
def test_voice_batch_with_failures_exits_nonzero(tmp_project, monkeypatch, as_json):
    import manju.build.graph as graph
    from manju.cli import app

    monkeypatch.setattr(graph, "voice_batch",
                        lambda *a, **k: _batch(failed=["S002"], ran=["S001"]))
    monkeypatch.chdir(tmp_project.root)
    args = ["voice", "--shots", "S001,S002", "--yes"]
    if as_json:
        args.append("--json")
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output


@pytest.mark.parametrize("as_json", [False, True])
def test_redo_batch_with_failures_exits_nonzero(tmp_project, monkeypatch, as_json):
    import manju.build.graph as graph
    from manju.cli import app

    monkeypatch.setattr(graph, "redo_batch",
                        lambda *a, **k: _batch(failed=["S002"]))
    monkeypatch.chdir(tmp_project.root)
    args = ["redo", "--shots", "S002", "--yes"]
    if as_json:
        args.append("--json")
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output


def test_all_green_batch_still_exits_zero(tmp_project, monkeypatch):
    import manju.build.graph as graph
    from manju.cli import app

    monkeypatch.setattr(graph, "voice_batch", lambda *a, **k: _batch(ran=["S001"]))
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["voice", "--shots", "S001", "--yes"])
    assert result.exit_code == 0, result.output


def test_canceled_batch_without_failures_exits_zero(tmp_project, monkeypatch):
    # cancel is a user decision, not a failure — job control stays rc=0
    import manju.build.graph as graph
    from manju.cli import app

    monkeypatch.setattr(graph, "voice_batch", lambda *a, **k: _batch(canceled=True))
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["voice", "--shots", "S001", "--yes"])
    assert result.exit_code == 0, result.output


# ---------------------------------------- failures store hears about voice


def test_batch_provider_failure_lands_in_the_failures_store(
        tmp_project, tmp_path, monkeypatch):
    from manju.build.graph import voice_batch

    _dialogued(tmp_project)
    _refused_manifest(tmp_path, monkeypatch)
    result = voice_batch(tmp_project, shots=["S001"], assume_yes=True,
                         provider="deadtts")
    assert result.failed and "S001" == result.failed[0]["shot"]
    rows = read_failures(tmp_project, 5)
    assert rows, "a real provider failure left the failures store empty"
    row = rows[-1]
    assert row["step"] == "voice"
    assert row["subject"] == "S001"
    assert "127.0.0.1" in row["cause"] or "network" in row["cause"]
    assert "manju voice" in row["hint"]  # the retry keystroke is named


def test_unresolved_submission_block_is_not_rerecorded_as_failure(
        tmp_project, tmp_path, monkeypatch):
    """The DR06 block is guidance already surfaced by `manju tasks` — the
    failures store records the PROVIDER failing, not the guard doing its
    job. (The original death that minted the submission was recorded.)"""
    from manju.build.graph import voice_batch
    from manju.providers.base import FailureKind, ProviderFailure

    _dialogued(tmp_project)
    _refused_manifest(tmp_path, monkeypatch)

    def _blocked(*a, **k):
        # forged EXACTLY like base._unknown_outcome_failure — the exclusion
        # keys on the structural detail code, not on message text (an早版
        # of this test forged without detail and correctly got recorded)
        raise ProviderFailure(
            FailureKind.provider_error,
            "deadtts: shot S001 has an unresolved submission sub_x in state "
            "OUTCOME_UNKNOWN — must not be auto-resubmitted (DR06 ruling 8).",
            detail={"code": "submission_outcome_unknown", "submission_id": "sub_x",
                    "shot": "S001", "state": "OUTCOME_UNKNOWN",
                    "automatic_resubmit": False})

    import manju.providers.tts as tts_mod
    monkeypatch.setattr(tts_mod.GenericTtsProvider, "synthesize", _blocked)
    result = voice_batch(tmp_project, shots=["S001"], assume_yes=True,
                         provider="deadtts")
    assert result.failed
    assert read_failures(tmp_project, 5) == []


# ------------------------------------ single-shot: one line, never a wall


def test_single_voice_provider_failure_is_one_line_not_a_traceback(
        tmp_project, tmp_path, monkeypatch):
    from manju.cli import app

    _dialogued(tmp_project)
    _refused_manifest(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["voice", "S001", "--yes",
                                 "--provider", "deadtts"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "network error" in result.output
    rows = read_failures(tmp_project, 5)
    assert rows and rows[-1]["step"] == "voice"  # single path records too


def test_single_voice_provider_failure_json_carries_the_code(
        tmp_project, tmp_path, monkeypatch):
    import json

    from manju.cli import app

    _dialogued(tmp_project)
    _refused_manifest(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["voice", "S001", "--yes",
                                 "--provider", "deadtts", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == "provider_error"
    assert "network error" in payload["error"]


# --------------------------------- providers check: invalid ≠ nonexistent


def test_providers_check_names_the_validation_error_not_no_such(
        tmp_path, monkeypatch):
    from manju.cli import app

    d = tmp_path / "providers" / "brokentts"
    d.mkdir(parents=True)
    (d / "provider.yaml").write_text(
        "id: brokentts\ntype: tts\nadapter: generic_tts\n"
        "submit:\n  url: http://x\n  method: POST\n  body_template: {}\n",
        encoding="utf-8")
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "providers"))
    result = runner.invoke(app, ["providers", "check", "brokentts"])
    assert result.exit_code != 0
    assert "no such provider" not in result.output
    assert "validation" in result.output or "job_id_path" in result.output


def test_providers_check_still_says_no_such_for_truly_absent(
        tmp_path, monkeypatch):
    from manju.cli import app

    (tmp_path / "providers").mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "providers"))
    result = runner.invoke(app, ["providers", "check", "ghost"])
    assert result.exit_code != 0
    assert "no such provider" in result.output
