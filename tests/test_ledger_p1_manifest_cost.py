"""Ledger P1 regressions — group ``manifest_cost``.

Two fail-open defects in ``providers/manifest.py`` on the owner's ORDINARY
path (a hand-written ``~/.manju/providers/<id>/provider.yaml``, no adversary):

- PROVIDER-MANIFEST-001 (money) — ``CostConfig._nonneg_cost`` rejected only
  ``v < 0``. Every compare against ``NaN`` is False (IEEE-754), and ``NaN < 0``
  is no exception, so ``cost: {per_call: .nan}`` VALIDATED. ``estimate_cost``
  then returns ``NaN``, which silently disarms BOTH money gates in
  ``build/graph.py``: the §8.3 budget breaker (``estimated_cost > budget``,
  :1350) and — the part the audit itself missed — the ``ask_before``
  SPEND CONFIRMATION (``estimated_cost > 0``, :1371; same shape again at
  :2789 batch and :2966 voice-batch). The paid build then starts with no
  ceiling AND no prompt. ``inf`` fails those two compares CLOSED, but it
  LAUNDERS into ``NaN`` through ``estimate_cost``'s ``per_second * (0/1000)``
  (``inf * 0.0 == nan``) for any zero-duration item, so it reopens the exact
  same hole — both are refused. Same fail-closed rule ``core.models
  .BudgetConfig`` already applies to the OTHER side of the breaker compare.
- PROVIDER-MANIFEST-008 — ``AsrConfig.time_unit`` was a free ``str`` while the
  consumer (``providers/asr.py:185``) is ``1000.0 if time_unit == "s" else
  1.0``: ANY other spelling — ``seconds``, ``sec``, ``S`` — falls into the
  millisecond branch. A manifest saying ``time_unit: seconds`` therefore yields
  a structurally PERFECT caption track with every timestamp 1000× too small,
  out of a transcription the owner PAID for, with zero errors anywhere. The
  value is now a closed set (``ASR_TIME_UNITS``) refused at load time.
  Deliberately NO synonym list: the plausible spellings are not a closed set
  (``sec``/``secs``/``second``/``msec``/``us``/``ns``/``frames``), so any
  partial guess-list still drops the unguessed ones into the same silent
  ``else: 1.0`` branch — that is UNKNOWN guessed into PASS. Refusing costs the
  single user one edit; guessing costs a paid, silently wrong transcript.

Red-first: each asserts the FIXED behaviour. Owner: providers/manifest.py.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from manju.core.yamlio import write_yaml
from manju.providers.manifest import (
    ASR_TIME_UNITS,
    AsrConfig,
    CostConfig,
    ProviderManifest,
    estimate_cost,
    load_manifests,
)


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    return root


def _cloud_manifest(**over):
    base = {
        "id": "cloud_x",
        "type": "video",
        "adapter": "generic_cloud",
        "capabilities": ["text_to_video"],
        "submit": {"url": "https://api.example.invalid/v1/videos",
                   "body_template": {"prompt": "{prompt}"},
                   "job_id_path": "$.data.task_id"},
        "poll": {"url": "https://api.example.invalid/v1/videos/{job_id}",
                 "status_path": "$.data.status",
                 "status_map": {"SUCCEEDED": "succeeded"}},
    }
    base.update(over)
    return base


def _asr_manifest(**over):
    base = {
        "id": "asr_x",
        "type": "asr",
        "adapter": "generic_asr",
        "capabilities": ["asr"],
        "submit": {"url": "https://api.example.invalid/v1/asr",
                   "body_template": {"audio": "{audio_b64}"},
                   "job_id_path": "$.id"},
    }
    base.update(over)
    return base


# ================================================== PROVIDER-MANIFEST-001 (money)


@pytest.mark.parametrize("field", ["per_call", "per_second"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_cost_rejects_non_finite(field, bad):
    """A price the money gates cannot compare against is refused at parse time."""
    with pytest.raises(ValidationError, match=field):
        CostConfig(**{field: bad})


@pytest.mark.parametrize("field", ["per_call", "per_second"])
def test_cost_still_rejects_negative(field):
    """The pre-existing negative rule is unchanged (never weakened)."""
    with pytest.raises(ValidationError, match=field):
        CostConfig(**{field: -0.5})


def test_cost_accepts_ordinary_prices():
    """0 (free/local provider) and a real price stay legal."""
    assert CostConfig().per_call == 0.0
    assert CostConfig(per_call=0.0, per_second=0.0).per_second == 0.0
    assert CostConfig(per_call=0.5, per_second=0.08).per_second == 0.08


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_manifest_with_non_finite_cost_is_refused(bad):
    with pytest.raises(ValidationError):
        ProviderManifest.model_validate(
            _cloud_manifest(cost={"per_call": bad, "currency": "CNY"}))


def test_nan_cost_from_yaml_never_reaches_the_registry(providers_dir):
    """The ORDINARY path: `cost: {per_call: .nan}` hand-typed into
    provider.yaml. YAML's `.nan` is an ordinary scalar that parses to a real
    float NaN, so before the fix this manifest loaded, priced every take as
    NaN and disarmed both money gates. It must now be an error surfaced by
    `manju doctor`, with the provider ABSENT rather than silently free."""
    root = providers_dir / "cloud_x"
    root.mkdir()
    write_yaml(root / "provider.yaml",
               _cloud_manifest(cost={"per_call": float("nan"), "currency": "CNY"}))
    manifests, errors = load_manifests()
    assert "cloud_x" not in manifests
    assert any("cloud_x/provider.yaml" in e for e in errors), errors


def test_inf_per_second_from_yaml_never_reaches_the_registry(providers_dir):
    """`inf` is not merely "absurdly expensive": estimate_cost multiplies it by
    the duration, and `inf * 0.0` is NaN — so an inf-priced provider re-creates
    the exact NaN fail-open for any zero-duration item."""
    root = providers_dir / "cloud_inf"
    root.mkdir()
    write_yaml(root / "provider.yaml",
               _cloud_manifest(id="cloud_inf",
                               cost={"per_second": float("inf"), "currency": "CNY"}))
    manifests, errors = load_manifests()
    assert "cloud_inf" not in manifests
    assert any("cloud_inf/provider.yaml" in e for e in errors), errors


def test_estimate_cost_of_any_loadable_manifest_is_finite_and_decidable():
    """The property the two gates in build/graph.py depend on: for a manifest
    that VALIDATES, `estimated_cost > budget` and `estimated_cost > 0` are
    real decisions, never the silent False that NaN returns for both. The
    zero-duration case is the inf-laundering one."""
    manifest = ProviderManifest.model_validate(
        _cloud_manifest(cost={"per_call": 0.5, "per_second": 0.08, "currency": "CNY"}))
    for duration_ms in (0, 1, 5000):
        cost = estimate_cost(manifest, duration_ms, candidates=2)
        assert math.isfinite(cost)
        # the ask_before compare (graph.py:1371) is decidable in both directions
        assert (cost > 0) is (cost != 0)


def test_free_provider_still_prices_zero_and_skips_the_spend_gate():
    """Byte-identity for the ordinary local/free provider: 0 is still 0, so the
    ask_before gate still (correctly) does not fire."""
    manifest = ProviderManifest.model_validate(
        _cloud_manifest(cost={"per_call": 0.0, "currency": "CNY"}))
    cost = estimate_cost(manifest, 5000, candidates=3)
    assert cost == 0.0 and not cost > 0


# ======================================================== PROVIDER-MANIFEST-008


def test_asr_time_unit_legal_set_is_exactly_ms_and_s():
    """The ONE closed set. The consumer's `== "s"` branch (providers/asr.py) is
    only correct because everything else is refused here."""
    assert ASR_TIME_UNITS == ("ms", "s")


@pytest.mark.parametrize("unit", ["ms", "s"])
def test_asr_time_unit_accepts_the_two_legal_values(unit):
    assert AsrConfig(time_unit=unit).time_unit == unit


def test_asr_time_unit_defaults_to_ms():
    """Byte-identity: a manifest that never mentions time_unit is unchanged."""
    assert AsrConfig().time_unit == "ms"


@pytest.mark.parametrize(
    "bad",
    ["seconds", "sec", "secs", "second", "S", "MS", "msec", "millisecond",
     "us", "ns", "frames", ""],
)
def test_asr_time_unit_refuses_everything_else(bad):
    """Before the fix EVERY one of these silently meant "milliseconds" — a
    `seconds` manifest produced a perfect-looking caption track 1000× too
    short. Deliberately includes the tempting synonyms: they are refused, not
    guessed."""
    with pytest.raises(ValidationError, match="time_unit"):
        AsrConfig(time_unit=bad)


def test_asr_time_unit_refusal_names_both_legal_values():
    """The 中文 refusal must be actionable on its own — the same convention the
    cost/limits validators in this module already follow."""
    with pytest.raises(ValidationError) as exc:
        AsrConfig(time_unit="seconds")
    message = str(exc.value)
    assert "ms" in message and "s" in message
    assert "1000" in message  # says WHAT goes wrong, not just that it is wrong


def test_asr_manifest_with_a_synonym_time_unit_never_reaches_the_registry(providers_dir):
    """The ORDINARY path: `time_unit: seconds` hand-typed into provider.yaml.
    It must fail at LOAD (surfaced by `manju doctor`) rather than after a paid
    transcription has already produced a 1000×-wrong .timing.json / SRT."""
    root = providers_dir / "asr_x"
    root.mkdir()
    write_yaml(root / "provider.yaml",
               _asr_manifest(asr={"segments_path": "$.data.segments",
                                  "time_unit": "seconds"}))
    manifests, errors = load_manifests()
    assert "asr_x" not in manifests
    assert any("asr_x/provider.yaml" in e for e in errors), errors


def test_asr_manifest_with_a_legal_time_unit_still_loads(providers_dir):
    """Non-regression: the seconds-priced ASR manifests the suite already uses
    (`time_unit: s`) keep loading unchanged."""
    root = providers_dir / "asr_ok"
    root.mkdir()
    write_yaml(root / "provider.yaml",
               _asr_manifest(id="asr_ok",
                             asr={"segments_path": "$.data.segments",
                                  "start_key": "begin", "end_key": "end",
                                  "time_unit": "s"}))
    manifests, errors = load_manifests()
    assert errors == []
    assert manifests["asr_ok"].asr.time_unit == "s"


def test_scaffolded_asr_template_still_declares_a_legal_time_unit(providers_dir):
    """`manju providers add --type asr` writes `time_unit: ms`; the template it
    scaffolds must still round-trip through the stricter loader."""
    from manju.providers.manifest import GENERIC_ASR_ADAPTER, scaffold_template

    root = providers_dir / "asr_tpl"
    root.mkdir()
    (root / "provider.yaml").write_text(
        scaffold_template("asr_tpl", "asr", GENERIC_ASR_ADAPTER), encoding="utf-8")
    manifests, errors = load_manifests()
    assert errors == []
    assert manifests["asr_tpl"].asr.time_unit in ASR_TIME_UNITS
