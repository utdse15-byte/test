"""Batch operations (goal item 3) — ``redo_batch`` / ``voice_batch`` in
manju.build.graph, plus the ``manju redo``/``manju voice`` batch CLI selectors.

The invariants under test (task §Scope): one selector → many shots, but the
three single-redo invariants enforced ONCE for the set —

    * one process build lock for the whole batch (never a per-shot re-acquire),
    * one aggregated §8.3 spend gate on the TOTAL (never a partial re-ask),
    * §4.3 — manual/locked content is never silently overturned; every
      exclusion is reported with its reason.

Generation is faked (``fake_gen`` patches ``generate_with_fallback``) so the
selector/lock/gate/isolation logic is exercised without ffmpeg.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from manju.build import graph
from manju.build.graph import BatchResult, BuildError, WaitingUser, redo_batch, voice_batch
from manju.cli import app
from manju.core.events import tail_events
from manju.core.locks import seal_lock
from manju.core.spec import compute_spec_hash, compute_voice_hash

runner = CliRunner()

# a real (tiny) wav so register_voice_take copies something audio-shaped
WAV_HEADER = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
              b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")


# --------------------------------------------------------------------- fakes


@pytest.fixture
def fake_gen(monkeypatch, tmp_path):
    """Patch the registry's ``generate_with_fallback`` so a redo mints a cheap
    fake take (no ffmpeg). Records the shots it was called for and can be told
    to raise for specific ids (per-shot failure isolation)."""
    import manju.providers.registry as registry
    from manju.core.models import TakeSidecar
    from manju.providers.base import FailureKind, ProviderFailure

    state = {"calls": [], "fail_for": set()}

    def _fake(req, chain=None, *, log=None):
        sid = req.shot.id
        state["calls"].append(sid)
        if sid in state["fail_for"]:
            raise ProviderFailure(FailureKind.provider_error, f"boom for {sid}")
        media = tmp_path / f"_gen_{sid}_{len(state['calls'])}.mp4"
        media.write_bytes(b"vid-" + sid.encode())
        take = req.project.register_take(
            sid, media, TakeSidecar(provider="test", spec_hash=req.spec_hash))
        return [take]

    monkeypatch.setattr(registry, "generate_with_fallback", _fake)
    return state


@pytest.fixture
def priced(monkeypatch):
    """Every planned shot prices at 5 CNY (as a real manifest would, §8.3)."""
    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (5.0, "CNY"))


def _select(project, sid, take_name):
    project.update_shot_raw(
        sid, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name))


@pytest.fixture
def mixed(tmp_project, add_shot, make_take):
    """A project with one shot in each state the selectors must tell apart:

        S001 MISSING · S002 STALE · S003 FRESH · S004 MANUAL
        S005 FRESH + generation.provider sealed (§5 lock)
    """
    p = tmp_project
    bible = p.load_bible()

    add_shot(p, "S001")  # no take -> MISSING

    add_shot(p, "S002")
    _select(p, "S002", make_take(p, "S002", "sha256:oldhash").name)  # STALE

    add_shot(p, "S003")
    fresh_h = compute_spec_hash(p.load_shot("S003"), bible)
    _select(p, "S003", make_take(p, "S003", fresh_h).name)  # FRESH

    add_shot(p, "S004")
    _select(p, "S004", make_take(p, "S004", "manual").name)  # MANUAL

    add_shot(p, "S005", generation={"provider": "caption_card"})
    fresh5 = compute_spec_hash(p.load_shot("S005"), bible)
    _select(p, "S005", make_take(p, "S005", fresh5).name)  # FRESH
    sealed = seal_lock(p.load_shot_raw("S005"), "generation.provider")
    p.update_shot_raw(
        "S005", lambda d: d.setdefault("locked", {}).__setitem__("generation.provider", sealed))
    return p


def _skip_reason(result: BatchResult, sid: str) -> str:
    return next(i["reason"] for i in result.skipped if i["shot"] == sid)


def _failed_reason(result: BatchResult, sid: str) -> str:
    return next(i["reason"] for i in result.failed if i["shot"] == sid)


# ---------------------------------------------------------- selector correctness


def test_all_selector_runs_everything_but_manual(mixed, fake_gen):
    r = redo_batch(mixed, all_shots=True, actor="ai", assume_yes=True)
    assert set(r.ran) == {"S001", "S002", "S003", "S005"}
    assert [i["shot"] for i in r.skipped] == ["S004"]
    assert "manual import" in _skip_reason(r, "S004")
    assert not r.failed
    # every run shot got a take back
    assert all(r.takes[s] for s in r.ran)


def test_all_stale_targets_only_stale(mixed, fake_gen):
    r = redo_batch(mixed, all_stale=True, actor="ai", assume_yes=True)
    assert r.ran == ["S002"]
    # the fresh shots are reported skipped with a reason (never silent)
    assert "state is fresh" in _skip_reason(r, "S003")
    assert "state is missing" in _skip_reason(r, "S001")
    assert set(fake_gen["calls"]) == {"S002"}


def test_all_missing_targets_only_missing(mixed, fake_gen):
    r = redo_batch(mixed, all_missing=True, actor="ai", assume_yes=True)
    assert r.ran == ["S001"]
    assert "state is stale" in _skip_reason(r, "S002")
    assert "--all-missing only redoes missing" in _skip_reason(r, "S002")


def test_explicit_shots_redo_regardless_of_state(mixed, fake_gen):
    # S003 is FRESH — an explicit --shots still redoes it (append-only force)
    r = redo_batch(mixed, shots=["S003", "S001"], actor="ai", assume_yes=True)
    assert set(r.ran) == {"S003", "S001"}
    assert not r.skipped and not r.failed


def test_explicit_shots_still_excludes_manual(mixed, fake_gen):
    r = redo_batch(mixed, shots=["S001", "S004"], actor="ai", assume_yes=True)
    assert r.ran == ["S001"]
    assert "manual import" in _skip_reason(r, "S004")


def test_unknown_shot_lands_in_failed(mixed, fake_gen):
    r = redo_batch(mixed, shots=["S001", "S404"], actor="ai", assume_yes=True)
    assert r.ran == ["S001"]
    assert "no such shot" in _failed_reason(r, "S404")


# --------------------------------------------------------------- lock exclusion


def test_override_on_sealed_field_is_excluded(mixed, fake_gen):
    # S005 has generation.provider sealed; a --provider override would overturn
    # it -> skipped with a §5 reason (mirrors how single redo respects locks).
    r = redo_batch(mixed, all_shots=True, provider="some_cloud",
                   actor="ai", assume_yes=True)
    assert "S005" not in r.ran
    reason = _skip_reason(r, "S005")
    assert "locked" in reason and "generation.provider" in reason
    # the other shots still run under the same override
    assert {"S001", "S002", "S003"} <= set(r.ran)


def test_no_override_does_not_collide_with_lock(mixed, fake_gen):
    # without an override nothing touches the sealed field -> S005 runs
    r = redo_batch(mixed, all_shots=True, actor="ai", assume_yes=True)
    assert "S005" in r.ran


def test_override_equal_to_sealed_value_is_not_a_collision(mixed, fake_gen):
    # provider override == the sealed value is a no-op, not a violation
    r = redo_batch(mixed, shots=["S005"], provider="caption_card",
                   actor="ai", assume_yes=True)
    assert r.ran == ["S005"] and not r.skipped


# --------------------------------------------------------- one lock for the batch


def test_single_lock_hold_no_per_shot_reacquire(mixed, fake_gen, monkeypatch):
    import manju.runtime.buildlock as bl

    count = {"n": 0}
    orig = bl.BuildLock.acquire

    def _counting(self):
        count["n"] += 1
        return orig(self)

    monkeypatch.setattr(bl.BuildLock, "acquire", _counting)
    r = redo_batch(mixed, all_shots=True, actor="ai", assume_yes=True)
    assert len(r.ran) >= 3  # a real multi-shot batch
    assert count["n"] == 1  # exactly one acquire for the whole batch


def test_batch_fails_cleanly_when_already_locked(mixed, fake_gen):
    from manju.runtime.buildlock import BuildLock, BuildLocked

    lock = BuildLock(mixed.root, actor="human").acquire()
    try:
        with pytest.raises(BuildLocked):
            redo_batch(mixed, all_shots=True, actor="ai", assume_yes=True)
    finally:
        lock.release()


# ------------------------------------------------------ one aggregated spend gate


def test_aggregate_gate_fires_once_with_total(mixed, fake_gen, priced):
    with pytest.raises(WaitingUser) as exc:
        redo_batch(mixed, shots=["S001", "S002"], actor="ai")
    # ONE gate on the TOTAL (2 shots × 5.0), with a per-shot breakdown
    assert exc.value.estimated_cost == 10.0
    assert "S001=5.0" in str(exc.value) and "S002=5.0" in str(exc.value)
    # nothing was generated — no partial spend mid-batch
    assert not mixed.takes("S001")
    assert fake_gen["calls"] == []


def test_aggregate_gate_yes_bypass(mixed, fake_gen, priced):
    r = redo_batch(mixed, shots=["S001", "S002"], actor="ai", assume_yes=True)
    assert set(r.ran) == {"S001", "S002"}
    assert r.estimated_cost == 10.0 and r.currency == "CNY"


def test_zero_cost_batch_is_not_gated(mixed, fake_gen):
    # default estimator prices at 0 -> no gate even without assume_yes
    r = redo_batch(mixed, shots=["S001"], actor="ai")
    assert r.ran == ["S001"]


# ---------------------------------------------------------- per-shot isolation


def test_one_provider_failure_does_not_abort_the_rest(mixed, fake_gen):
    fake_gen["fail_for"] = {"S002"}
    r = redo_batch(mixed, all_shots=True, actor="ai", assume_yes=True)
    assert "S002" not in r.ran
    assert "boom for S002" in _failed_reason(r, "S002")
    # the others still produced takes
    assert {"S001", "S003", "S005"} <= set(r.ran)
    assert r.takes["S001"]


# --------------------------------------------------------------------- events


def test_batch_emits_one_batch_event_plus_per_shot_events(mixed, fake_gen):
    redo_batch(mixed, shots=["S001", "S003"], actor="ai", assume_yes=True)
    events = tail_events(mixed.root, 50)
    kinds = [e["action"] for e in events]
    assert kinds.count("redo_batch") == 1
    redo_shots = [e["detail"]["shot"] for e in events if e["action"] == "redo"]
    assert set(redo_shots) == {"S001", "S003"}
    batch = next(e for e in events if e["action"] == "redo_batch")
    assert set(batch["detail"]["ran"]) == {"S001", "S003"}


# ------------------------------------------------------------- BatchResult shape


def test_batchresult_to_dict_shape(mixed, fake_gen):
    r = redo_batch(mixed, all_shots=True, actor="ai", assume_yes=True)
    d = r.to_dict()
    assert set(d) == {"requested", "ran", "skipped", "failed", "takes",
                      "estimated_cost", "actual_cost", "currency",
                      # round AA4: honest job cancellation — mirrors
                      # BuildResult's canceled/errors shape (build/graph.py)
                      "canceled", "errors"}
    assert set(d["requested"]) >= {"S001", "S002", "S003", "S004", "S005"}
    assert all(set(s) == {"shot", "reason"} for s in d["skipped"])


# ------------------------------------------------------------------ CLI wiring


@pytest.fixture
def in_mixed(mixed, monkeypatch):
    monkeypatch.chdir(mixed.root)
    return mixed


def test_cli_redo_all_json(in_mixed, fake_gen):
    res = runner.invoke(app, ["redo", "--all", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert set(data) == {"requested", "ran", "skipped", "failed", "takes",
                         "estimated_cost", "actual_cost", "currency",
                         "canceled", "errors"}
    assert "S004" in [s["shot"] for s in data["skipped"]]


def test_cli_positional_and_batch_are_mutually_exclusive(in_mixed, fake_gen):
    res = runner.invoke(app, ["redo", "S001", "--all"])
    assert res.exit_code == 1
    assert "mutually exclusive" in res.output


def test_cli_two_batch_selectors_rejected(in_mixed, fake_gen):
    res = runner.invoke(app, ["redo", "--all", "--all-stale"])
    assert res.exit_code == 1
    assert "mutually exclusive" in res.output


def test_cli_no_selector_and_no_shot_errors(in_mixed):
    res = runner.invoke(app, ["redo"])
    assert res.exit_code == 1


def test_cli_from_take_rejected_with_batch(in_mixed, fake_gen):
    res = runner.invoke(app, ["redo", "--all", "--from-take", "take_01"])
    assert res.exit_code == 1
    assert "from-take" in res.output


def test_cli_redo_shots_list(in_mixed, fake_gen):
    res = runner.invoke(app, ["redo", "--shots", "S001,S003", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert set(data["ran"]) == {"S001", "S003"}


# ==================================================================== voice batch


class _FakeTts:
    """Registers a voice take without hitting a network TTS."""

    def __init__(self, tmp_path, fail_for=()):
        self.id = "tts_x"
        self._tmp = tmp_path
        self._fail_for = set(fail_for)
        self.calls = []

    def synthesize(self, project, shot, bible):
        from manju.core.models import RemoteJobInfo, VoiceTakeSidecar

        self.calls.append(shot.id)
        if shot.id in self._fail_for:
            raise RuntimeError(f"tts boom for {shot.id}")
        media = self._tmp / f"v_{shot.id}.wav"
        media.write_bytes(WAV_HEADER)
        sidecar = VoiceTakeSidecar(
            provider="tts_x",
            voice_hash=compute_voice_hash(shot, bible),
            remote=RemoteJobInfo(job_id=None, cost=0.02, currency="CNY"),
        )
        return project.register_voice_take(shot.id, media, sidecar)


@pytest.fixture
def tts_env(tmp_path, monkeypatch):
    """A configured tts manifest (per_call 0.02) so voice_batch can price."""
    from manju.core.yamlio import write_yaml

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "prov" / "tts_x" / "provider.yaml", {
        "id": "tts_x",
        "type": "tts",
        "adapter": "generic_tts",
        "auth": {"key_env": "TTS_X_KEY"},
        "submit": {"url": "https://api.example.com/v1/tts",
                   "body_template": {"text": "{text}"},
                   "job_id_path": "$.data.task_id"},
        "tts": {"audio_url_path": "$.data.audio_url", "audio_format": "wav"},
        "cost": {"per_call": 0.02, "currency": "CNY"},
    })


@pytest.fixture
def patch_tts(monkeypatch, tmp_path):
    fake = _FakeTts(tmp_path)
    monkeypatch.setattr("manju.providers.tts.get_tts_provider", lambda name=None, **kw: fake)
    return fake


def test_voice_missing_synthesizes_gap_shots(tmp_project, add_shot, tts_env, patch_tts):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词一"})
    add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "台词二"})
    add_shot(tmp_project, "S003", dialogue={"speaker": "", "text": ""})  # NOT_NEEDED
    r = voice_batch(tmp_project, missing=True, actor="ai", assume_yes=True)
    assert set(r.ran) == {"S001", "S002"}
    assert "no dialogue" in _skip_reason(r, "S003")
    assert len(tmp_project.voice_takes("S001")) == 1


def test_voice_all_skips_stale_advisory_only(tmp_project, add_shot, tts_env, patch_tts):
    # S001 voiced then its line changes -> STALE; S002 still MISSING
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "第一版"})
    patch_tts.synthesize(tmp_project, shot, tmp_project.load_bible())
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "改过的"))
    add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "第二版"})

    r = voice_batch(tmp_project, all_shots=True, actor="ai", assume_yes=True)
    assert r.ran == ["S002"]  # only the gap; stale never batch-regenerated (§4.3)
    assert "advisory-only" in _skip_reason(r, "S001")
    assert len(tmp_project.voice_takes("S001")) == 1  # unchanged


def test_voice_explicit_shots_revoices_stale(tmp_project, add_shot, tts_env, patch_tts):
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "第一版"})
    patch_tts.synthesize(tmp_project, shot, tmp_project.load_bible())
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "改过的"))
    # explicit --shots is the escape hatch to re-voice a stale line
    r = voice_batch(tmp_project, shots=["S001"], actor="ai", assume_yes=True)
    assert r.ran == ["S001"]
    assert len(tmp_project.voice_takes("S001")) == 2


def test_voice_aggregate_gate_once(tmp_project, add_shot, tts_env, patch_tts):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "a"})
    add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "b"})
    with pytest.raises(WaitingUser) as exc:
        voice_batch(tmp_project, missing=True, actor="ai")
    assert exc.value.estimated_cost == pytest.approx(0.04)  # 2 × per_call
    assert patch_tts.calls == []  # nothing synthesized
    r = voice_batch(tmp_project, missing=True, actor="ai", assume_yes=True)
    assert set(r.ran) == {"S001", "S002"}


def test_voice_single_lock_hold(tmp_project, add_shot, tts_env, patch_tts, monkeypatch):
    import manju.runtime.buildlock as bl

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "a"})
    add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "b"})
    count = {"n": 0}
    orig = bl.BuildLock.acquire
    monkeypatch.setattr(bl.BuildLock, "acquire",
                        lambda self: (count.__setitem__("n", count["n"] + 1), orig(self))[1])
    r = voice_batch(tmp_project, missing=True, actor="ai", assume_yes=True)
    assert len(r.ran) == 2 and count["n"] == 1


def test_voice_failure_isolation(tmp_project, add_shot, tts_env, monkeypatch, tmp_path):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "a"})
    add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "b"})
    fake = _FakeTts(tmp_path, fail_for={"S001"})
    monkeypatch.setattr("manju.providers.tts.get_tts_provider", lambda name=None, **kw: fake)
    r = voice_batch(tmp_project, missing=True, actor="ai", assume_yes=True)
    assert r.ran == ["S002"]
    assert "tts boom for S001" in _failed_reason(r, "S001")


def test_voice_batch_event(tmp_project, add_shot, tts_env, patch_tts):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "a"})
    voice_batch(tmp_project, missing=True, actor="ai", assume_yes=True)
    events = tail_events(tmp_project.root, 20)
    assert [e["action"] for e in events].count("voice_batch") == 1
    assert any(e["action"] == "voice" and e["detail"]["shot"] == "S001" for e in events)


def test_voice_no_provider_configured_reports_failed(tmp_project, add_shot, monkeypatch, tmp_path):
    # no MANJU_PROVIDERS_DIR manifest -> every run candidate fails, shape intact
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "empty"))
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "a"})
    r = voice_batch(tmp_project, missing=True, actor="ai", assume_yes=True)
    assert not r.ran
    assert "no TTS provider configured" in _failed_reason(r, "S001")


def test_cli_voice_missing_json(tmp_project, add_shot, tts_env, patch_tts, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "a"})
    res = runner.invoke(app, ["voice", "--missing", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["ran"] == ["S001"]


def test_cli_voice_selector_mutual_exclusion(tmp_project, add_shot, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["voice", "S001", "--missing"])
    assert res.exit_code == 1
    assert "mutually exclusive" in res.output
