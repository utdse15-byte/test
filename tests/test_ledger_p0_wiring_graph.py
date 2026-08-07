"""P0 ledger — build/graph paid-safety wiring.

Two invariants the build graph consumes from its owners:

* SPEND-P0-001 — EVERY provider-reported actual cost enters a running total
  through ``build.spend.book_actual_cost``/``checked_cost``. A ``NaN`` fails
  every ``running > budget_limit`` compare (IEEE-754) and a negative cancels
  out later real spend, so an unusable figure must fail CLOSED (the build
  stops) rather than be booked as 0 while paid work keeps being submitted.
* WINCLI-P0-003 — cloud TTS ships private dialogue off-box even at zero cost,
  so ``build.voice.network_egress_gate`` runs before any synthesis. It is
  opt-in: absent the ``ask_before`` token nothing changes, and spend consent
  (``assume_yes``) never doubles as egress consent.

Red-first: before the wiring, ``_concurrent_generate``/``_commit_one`` folded
``float(res.get("actual_cost", 0.0) or 0.0)`` straight into the total, the voice
attribution's tolerant ``except Exception`` swallowed any refusal, and
``run_build`` never called the egress gate at all.
"""

from __future__ import annotations

import pytest

from manju.build import graph
from manju.build.spend import PaidResultInvalid
from manju.build.voice import NETWORK_EGRESS_TOKEN

# a real (tiny) wav so a registered voice take is a plausible audio file
WAV = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
       b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

# figures a provider must never be able to push into a running total.
UNTRUSTWORTHY = [float("nan"), float("inf"), float("-inf"), -5.0, "abc", object()]


# ------------------------------------------------- 1. the running-total folds


def _drive(costs, budget, max_workers=2):
    """Run ``_concurrent_generate`` over ``[(shot, actual_cost), ...]``."""
    by_shot = dict(costs)
    items = [{"shot": sid} for sid, _ in costs]
    return graph._concurrent_generate(
        items, lambda it: {"shot": it["shot"], "actual_cost": by_shot[it["shot"]]},
        max_workers=max_workers, budget_limit=budget)


def test_nan_really_does_defeat_the_budget_breaker():
    """Why this must fail closed rather than degrade: once NaN is in the total,
    the trip condition is False forever, so every further paid task ships."""
    poisoned = float("nan") + 10.0
    assert not (poisoned > 1.0)
    assert not (poisoned <= 1.0)


@pytest.mark.parametrize("bad", UNTRUSTWORTHY)
def test_concurrent_generate_refuses_untrustworthy_actual_cost(bad):
    with pytest.raises(PaidResultInvalid):
        _drive([("S001", bad)], budget=1.0)


def test_concurrent_generate_still_sums_valid_costs_and_trips_budget():
    results, tripped, canceled, running, _ = _drive(
        [("S001", 3.0), ("S002", 4.0)], budget=5.0)
    assert running == 7.0
    assert tripped is True and canceled is False
    assert set(results) == {"S001", "S002"}


def test_zero_cost_plans_are_untouched():
    """The default free/local providers report 0 — no trip, no refusal."""
    results, tripped, canceled, running, in_flight = _drive(
        [("S001", 0.0), ("S002", None)], budget=1.0)
    assert running == 0.0 and tripped is False and canceled is False
    assert in_flight == [] and set(results) == {"S001", "S002"}


def test_book_result_cost_reraises_a_worker_refusal():
    """``_gen_one`` never raises (it may run in a pool), so a per-take figure it
    already refused rides back as ``cost_invalid`` — booking re-raises it."""
    bad = PaidResultInvalid("refused", cost=float("nan"))
    with pytest.raises(PaidResultInvalid):
        graph._book_result_cost(0.0, {"actual_cost": 0.0, "cost_invalid": bad},
                                label="t")


def test_book_result_cost_accepts_clean_figures():
    assert graph._book_result_cost(1.0, {"actual_cost": 2.5}, label="t") == 3.5
    assert graph._book_result_cost(1.0, {}, label="t") == 1.0
    assert graph._book_result_cost(1.0, {"actual_cost": None}, label="t") == 1.0


# --------------------------------------- 2. a build stops on an unusable cost


@pytest.fixture
def fake_video_provider(tmp_path, monkeypatch):
    """Replace the provider fallback with one that produces a REAL take whose
    in-memory sidecar carries whatever ``remote.cost`` the test wants (nothing
    invalid is ever persisted)."""
    from manju.core.models import RemoteJobInfo, TakeSidecar
    from manju.providers import registry

    box = {"cost": 0.0}

    def _generate(req, chain):
        src = tmp_path / f"_fake_{req.shot.id}.mp4"
        src.write_bytes(b"fakevideo-" + req.shot.id.encode("ascii"))
        take = req.project.register_take(
            req.shot.id, src,
            TakeSidecar(provider="test", spec_hash=req.spec_hash))
        take.sidecar.remote = RemoteJobInfo(job_id="j1", cost=box["cost"],
                                            currency="CNY")
        return [take]

    monkeypatch.setattr(registry, "generate_with_fallback", _generate)
    return box


def test_build_fails_closed_when_a_provider_reports_an_untrustworthy_cost(
        tmp_project, add_shot, fake_video_provider):
    fake_video_provider["cost"] = float("nan")
    add_shot(tmp_project, "S001")

    result = graph.run_build(tmp_project, target="qc", actor="ai", assume_yes=True)

    assert result.ok is False
    assert result.waiting_user is False  # a refusal, not a consent prompt
    assert any("paid_result_invalid" in e for e in result.errors), result.errors
    # fail-closed is a one-line result, never a traceback at the CLI/GUI.
    assert all(isinstance(e, str) for e in result.errors)


def test_fail_closed_build_still_releases_the_build_lock(
        tmp_project, add_shot, fake_video_provider):
    from manju.runtime.buildlock import build_lock

    fake_video_provider["cost"] = -3.0
    add_shot(tmp_project, "S001")
    result = graph.run_build(tmp_project, target="qc", actor="ai", assume_yes=True)
    assert result.ok is False
    with build_lock(tmp_project.root, actor="probe"):
        pass  # BuildLocked here would mean the refusal leaked the lock


def test_valid_costs_still_build_normally(tmp_project, add_shot,
                                          fake_video_provider):
    fake_video_provider["cost"] = 1.25
    add_shot(tmp_project, "S001")
    result = graph.run_build(tmp_project, target="qc", actor="ai", assume_yes=True)
    assert not any("paid_result_invalid" in e for e in result.errors)
    assert result.generated  # the take was committed, the cost booked


# ------------------------------------ 3. voice: attribution vs. the ledger

TTS_MANIFEST = {
    "id": "tts_x",
    "type": "tts",
    "adapter": "generic_tts",
    "auth": {"key_env": "TTS_X_KEY"},
    "submit": {
        "url": "https://api.tts.example/v1/say",
        "body_template": {"text": "{text}"},
        "job_id_path": "$.data.task_id",
    },
    "tts": {"audio_url_path": "$.data.audio_url", "audio_format": "wav"},
    "cost": {"per_call": 0.02, "currency": "CNY"},
}


class _FakeTts:
    """A cloud TTS stand-in: records every call and registers a real take."""

    id = "tts_x"
    manifest = None
    NETWORK_EGRESS_ENDPOINT = "Microsoft Edge TTS 云端"

    def __init__(self, tmp_path, cost=None):
        self._tmp = tmp_path
        self.cost = cost
        self.calls: list[str] = []

    def synthesize(self, project, shot, bible, **kwargs):
        from manju.core.models import RemoteJobInfo, VoiceTakeSidecar

        self.calls.append(shot.id)
        src = self._tmp / f"_voice_{shot.id}.wav"
        src.write_bytes(WAV)
        remote = (RemoteJobInfo(job_id="v1", cost=self.cost, currency="CNY")
                  if self.cost is not None else None)
        return project.register_voice_take(
            shot.id, src,
            VoiceTakeSidecar(provider="tts_x", voice_hash="sha256:fake",
                             remote=remote))


@pytest.fixture
def voice_only(tmp_project, add_shot, make_take, tmp_path, monkeypatch):
    """A project whose only PLANNED work is one cloud voice take.

    The picture take is present but stale, so it never enters the generation
    plan (``regen_stale`` off) — the voice branch is what the build reaches.
    Returns a factory: ``voice_only(cost=...)`` installs the fake provider."""
    from manju.core.yamlio import write_yaml

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "prov" / "tts_x" / "provider.yaml", TTS_MANIFEST)

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "stale-hash")
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )

    def _install(cost=None):
        from manju.providers import tts as tts_mod

        fake = _FakeTts(tmp_path, cost=cost)
        monkeypatch.setattr(tts_mod, "get_tts_provider",
                            lambda name=None, **kw: fake)
        return fake

    return _install


def _set_ask_before(project, tokens):
    config = project.load_config()
    config.ask_before = list(tokens)
    project.save_config(config)


def test_voice_attribution_does_not_swallow_a_refused_figure(tmp_project,
                                                             voice_only):
    """The tolerant ``except Exception`` there exists to survive a failed
    ATTRIBUTION, not a distorted LEDGER: spent_so_far gates the remaining voice
    submissions, so a poisoned total keeps spending."""
    fake = voice_only(cost=-2.5)
    result = graph.run_build(tmp_project, target="qc", actor="ai", assume_yes=True)

    assert fake.calls == ["S001"]
    assert result.ok is False
    assert any("paid_result_invalid" in e for e in result.errors), result.errors
    # honesty: the take was really produced (and billed) — it stays on disk.
    assert tmp_project.voice_takes("S001")


def test_voice_attribution_books_a_valid_figure(tmp_project, voice_only):
    fake = voice_only(cost=1.5)
    result = graph.run_build(tmp_project, target="qc", actor="ai", assume_yes=True)

    assert fake.calls == ["S001"]
    assert not any("paid_result_invalid" in e for e in result.errors)
    assert any(g.startswith("S001/") for g in result.generated)


# --------------------------------------------- 4. the network-egress gate


def test_voice_synthesis_is_ungated_without_the_token(tmp_project, voice_only):
    """Opt-in: a project that never asked for the gate is byte-identical."""
    fake = voice_only(cost=0.0)
    assert NETWORK_EGRESS_TOKEN not in tmp_project.load_config().ask_before
    graph.run_build(tmp_project, target="qc", actor="ai", assume_yes=True)
    assert fake.calls == ["S001"]


def test_spend_consent_is_not_egress_consent(tmp_project, voice_only):
    """``assume_yes`` grants the §8.3 SPEND yes; it must not also ship private
    dialogue to a third-party cloud (WINCLI-P0-003)."""
    fake = voice_only(cost=0.0)
    _set_ask_before(tmp_project, ["expensive_generation", NETWORK_EGRESS_TOKEN])

    result = graph.run_build(tmp_project, target="qc", actor="ai", assume_yes=True)

    assert fake.calls == []  # transport 0 — refused before any byte left
    assert not tmp_project.voice_takes("S001")
    assert result.ok is False and result.waiting_user is True
    assert any(NETWORK_EGRESS_TOKEN in e for e in result.errors), result.errors
    assert any("tts_x" in e for e in result.errors)


def test_zero_cost_tts_is_gated_too(tmp_project, voice_only):
    """The spend gate keys on estimated_cost > 0, so a free cloud TTS would
    never trip it — egress is a separate risk category."""
    fake = voice_only(cost=0.0)
    _set_ask_before(tmp_project, [NETWORK_EGRESS_TOKEN])  # spend gate removed

    result = graph.run_build(tmp_project, target="qc", actor="ai")

    assert fake.calls == []
    assert result.waiting_user is True


def test_allow_network_grants_the_separate_consent(tmp_project, voice_only):
    fake = voice_only(cost=0.0)
    _set_ask_before(tmp_project, ["expensive_generation", NETWORK_EGRESS_TOKEN])

    graph.run_build(tmp_project, target="qc", actor="ai", assume_yes=True,
                    allow_network=True)

    assert fake.calls == ["S001"]


def test_locale_voice_branch_is_gated_too(tmp_project, voice_only):
    """WP4 locale voice ships the SAME dialogue off-box — both voice branches
    of run_build sit behind the gate."""
    from manju.core.yamlio import write_yaml

    fake = voice_only(cost=0.0)
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml",
               {"S001": {"text": "This cannot be."}})
    _set_ask_before(tmp_project, ["expensive_generation", NETWORK_EGRESS_TOKEN])

    result = graph.run_build(tmp_project, target="final", actor="ai",
                             assume_yes=True, lang="en")

    assert fake.calls == []
    assert not tmp_project.voice_takes("S001", lang="en")
    assert result.ok is False and result.waiting_user is True
    assert any(NETWORK_EGRESS_TOKEN in e for e in result.errors), result.errors


def test_egress_check_resolves_plan_providers_once(tmp_project, voice_only,
                                                   monkeypatch):
    """The helper feeds the gate RESOLVED providers, deduped, and an
    unresolvable one is left to the synthesis loop's own error path."""
    from manju.providers import tts as tts_mod

    voice_only(cost=0.0)
    seen: list[list] = []
    monkeypatch.setattr(graph, "_voice_egress_check", graph._voice_egress_check)
    monkeypatch.setattr("manju.build.voice.network_egress_gate",
                        lambda project, providers, *, allow_network: seen.append(
                            [getattr(p, "id", None) for p in providers]))
    plan = [{"provider": "tts_x"}, {"provider": "tts_x"}]
    graph._voice_egress_check(tmp_project, plan, allow_network=False)
    assert seen == [["tts_x"]]

    def _boom(name=None, **kw):
        raise tts_mod.TtsUnavailable("no such provider")

    monkeypatch.setattr(tts_mod, "get_tts_provider", _boom)
    seen.clear()
    graph._voice_egress_check(tmp_project, [{"provider": "nope"}],
                              allow_network=False)
    assert seen == [[]]  # nothing to name → the gate is a no-op
