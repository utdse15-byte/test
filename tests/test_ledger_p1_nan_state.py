"""Ledger P1 regressions — group ``nan_state``.

Four fail-open defects on the owner's ORDINARY path (no adversary needed):

- CORE-BUDGET-001 (money) — ``budget.limit`` accepted ``NaN``/``inf``/negative.
  ``NaN`` fails EVERY ``running > budget_limit`` compare (IEEE-754), so the
  §8.3 breaker is silently ABSENT for the whole build while real charging
  continues. Validated at the model, the same fail-closed rule
  ``build/spend.checked_cost`` already applies to the other side of the compare.
- PROVIDER-STATE-P1-001 — ``runtime/state._dumps`` had no ``default=``, so a
  params value that came out of YAML as a ``date``/``datetime`` raised
  ``TypeError``. That is not an ``OSError``, so it escaped every ``except`` on
  ``providers/base.py`` AFTER the paid submission went out: the CLI crashed bare
  and the ``jobs`` row was never written, reopening the duplicate-submission
  window (§8.1). Uses the SAME convention as ``core/hashing._json_default``.
- PROVIDER-JSONPATH-P1-001 — ``providers/jsonpath.assign`` silently replaced an
  existing SCALAR with ``{}`` when it needed to descend, so a ``refs.field``
  authored as ``$.prompt.image`` DELETED the compiled prompt and the paid
  request went out anyway with an empty prompt. Now a JsonPathError.
- SECRET-JSONL — ``check.SCAN_SUFFIXES`` did not include ``.jsonl`` while the
  scaffolded GITIGNORE does not ignore ``reports/``, so a credential written
  into ``reports/failures.jsonl`` walked past the HISTORY-P0-001 pre-check into
  git history. The per-file scan is bounded (head + tail, streamed), so this
  widening is size-independent work.

Red-first: each asserts the FIXED behaviour. Owners: core/models.py,
runtime/state.py, providers/jsonpath.py, core/check.py.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from manju.core.check import SCAN_SUFFIXES, file_has_secret, run_check, should_scan_for_secret
from manju.core.models import BudgetConfig, ProjectConfig
from manju.core.yamlio import read_yaml, write_yaml
from manju.providers.jsonpath import JsonPathError, assign, extract
from manju.runtime.state import RuntimeState

# A syntactically valid fake OpenAI-style key (matches SECRET_PATTERNS' sk- form
# without being a real credential).
_FAKE_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"


# ====================================================== CORE-BUDGET-001 (money)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_budget_limit_rejects_non_finite_and_negative(bad):
    """A limit the breaker cannot compare against is refused at parse time."""
    with pytest.raises(ValidationError):
        BudgetConfig(limit=bad)


def test_budget_limit_nan_from_yaml_is_refused(tmp_project):
    """The ORDINARY path: `budget: {limit: .nan}` in project.yaml. YAML's `.nan`
    parses to a real float NaN, so without the validator the breaker silently
    vanishes for the whole build."""
    raw = read_yaml(tmp_project.root / "project.yaml")
    raw["budget"] = {"limit": float("nan"), "currency": "CNY"}
    write_yaml(tmp_project.root / "project.yaml", raw)
    with pytest.raises(Exception) as exc:  # ProjectError or ValidationError
        tmp_project.load_config()
    assert "budget" in str(exc.value).lower() or "limit" in str(exc.value).lower()


def test_budget_limit_accepts_ordinary_values():
    """Unset, zero and a positive limit all stay legal (0 = 'spend nothing')."""
    assert BudgetConfig().limit is None
    assert BudgetConfig(limit=0).limit == 0.0
    assert BudgetConfig(limit=12.5).limit == 12.5


def test_project_without_budget_serializes_byte_identically(tmp_project):
    """The validator must not perturb the compat corpus: a project that never
    sets an optional field still emits exactly the same keys."""
    config = ProjectConfig.model_validate(read_yaml(tmp_project.root / "project.yaml"))
    data = config.model_dump()
    assert data["budget"] == {"limit": None, "currency": "CNY"}
    assert "edit_rate" not in data  # the one wrap serializer still drops it


# ============================================== PROVIDER-STATE-P1-001 (§8.1)


def test_open_job_survives_a_yaml_date_in_params(tmp_path):
    """An unquoted YAML date in shot params (`aired: 2026-07-18`) reaches the
    jobs write as a `datetime.date`. Before the fix json.dumps raised TypeError
    — NOT an OSError, so it escaped providers/base.py's except AFTER the paid
    submit, and the resume breadcrumb was never written."""
    params = {"aired": dt.date(2026, 7, 18), "at": dt.datetime(2026, 7, 18, 9, 30)}
    with RuntimeState(tmp_path) as state:
        state.open_job("job-1", provider="cloud_x", shot="S001", params=params)
        pending = state.pending_jobs()
    assert [j["remote_job_id"] for j in pending] == ["job-1"]
    assert "2026-07-18" in (pending[0]["params"] or "")


def test_record_run_survives_a_yaml_date_in_params(tmp_path):
    """Same convention on the ledger row (`runs.params`)."""
    with RuntimeState(tmp_path) as state:
        state.record_run(
            shot="S001", provider="cloud_x", status="succeeded",
            params={"aired": dt.date(2026, 7, 18)},
        )
        rows = state.run_log()
    assert "2026-07-18" in (rows[0]["params"] or "")


def test_state_dumps_still_refuses_a_genuinely_unserializable_value(tmp_path):
    """`default=` covers YAML's date/time scalars only — it must not turn into a
    blanket str() that would silently mangle a real programming error."""
    with RuntimeState(tmp_path) as state:
        with pytest.raises(TypeError):
            state.open_job("job-2", provider="cloud_x", shot="S001",
                           params={"fh": object()})


# ================================================ PROVIDER-JSONPATH-P1-001


def test_assign_refuses_to_descend_through_an_existing_scalar():
    """`refs.field: $.prompt.image` against a body whose `prompt` is the
    compiled prompt string used to REPLACE it with {} — deleting the prompt and
    letting the paid request go out empty."""
    body = {"prompt": "雨夜便利店,林夏站在货架前", "model": "v1"}
    with pytest.raises(JsonPathError):
        assign(body, "$.prompt.image", "https://example.invalid/a.png")
    assert body["prompt"] == "雨夜便利店,林夏站在货架前"  # untouched


def test_assign_still_creates_missing_intermediate_dicts():
    """The documented behaviour is unchanged for an ABSENT key."""
    body: dict = {"model": "v1"}
    assign(body, "$.input.image", "u")
    assert body["input"] == {"image": "u"}
    assert extract(body, "$.input.image") == "u"


def test_assign_still_replaces_a_none_placeholder():
    """A body_template that declares the slot as `image: null` is a placeholder,
    not authored content — descending through it stays legal."""
    body: dict = {"input": None}
    assign(body, "$.input.image", "u")
    assert body["input"] == {"image": "u"}


def test_assign_leaf_over_a_scalar_is_still_a_plain_overwrite():
    """Only DESCENDING through a scalar is refused; writing the leaf is the job."""
    body = {"prompt": "old"}
    assign(body, "$.prompt", "new")
    assert body["prompt"] == "new"


# ============================================================= SECRET-JSONL


def test_jsonl_is_scanned_for_secrets():
    assert ".jsonl" in SCAN_SUFFIXES
    assert should_scan_for_secret(__import__("pathlib").Path("reports/failures.jsonl"))


def test_secret_in_reports_failures_jsonl_fails_check(tmp_project):
    """reports/ is NOT gitignored, so a key logged into failures.jsonl would be
    committed by `manju snapshot` into git history (where a later delete does not
    remove it). run_check must see it — the same scanner history.py reuses."""
    path = tmp_project.reports_dir / "failures.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"step":"generate","evidence":"Authorization: Bearer %s"}\n' % _FAKE_KEY,
        encoding="utf-8",
    )
    assert file_has_secret(path) is True
    report = run_check(tmp_project)
    assert any("failures.jsonl" in e for e in report.errors), report.errors


def test_clean_jsonl_is_not_flagged(tmp_project):
    """No false positive on an ordinary append-only log line."""
    path = tmp_project.reports_dir / "failures.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"step":"generate","subject":"S001"}\n', encoding="utf-8")
    assert file_has_secret(path) is False
