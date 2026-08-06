"""Phase 4 - media-bound creative Experiment Memory.

Experiment Memory is an additive block inside the existing verdict v2
``decision``.  It inherits the verdict's exact media/spec/expectation binding,
remains append-only history after drift, and never becomes assurance or run
attempt evidence.
"""

from __future__ import annotations

import json

import pytest

from manju.core.hashing import hash_file
from manju.core.models import ProbeInfo, TakeSidecar
from manju.core.spec import SPEC_VERSION, compute_spec_hash
from manju.qc.agent_review import (
    VerdictError,
    qc_brief,
    read_v2_records,
    record_verdicts,
)
from manju.qc.assurance import compute_assurance
from manju.qc.production import candidate_families

V2 = "manju.qc.verdict/v2"


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _take(project, shot_id, data=b"experiment-media", *, duration_ms=None):
    source = project.root / f"_{shot_id}_experiment.mp4"
    source.write_bytes(data)
    probe = ProbeInfo(duration_ms=duration_ms) if duration_ms is not None else None
    take = project.register_take(
        shot_id,
        source,
        TakeSidecar(provider="test", spec_hash="sha256:experiment", probe=probe),
    )
    source.unlink()
    _select(project, shot_id, take.name)
    return take


def _row(project, shot_id="S001"):
    return next(r for r in qc_brief(project, [shot_id])["shots"] if r["shot"] == shot_id)


def _experiment(**overrides):
    value = {
        "hypothesis": "smaller hand motion preserves the coin geometry",
        "tested_variable": "motion",
        "held_constant": ["camera", "character_reference", "lighting", "duration"],
        "expected_result": "the coin remains distinct from the fingers",
        "observed_result": "usable until the final pocket-contact beat",
        "usable_ranges_ms": [{"start_ms": 0, "end_ms": 800}],
        "next_test": "cut on the occlusion instead of generating the full insert",
    }
    value.update(overrides)
    return value


def _verdict(row, *, experiment=None, observed="present", disposition="REROLL"):
    decision = {
        "disposition": disposition,
        "diagnostic_isolation": True,
    }
    if disposition != "KEEP":
        decision["primary_repair_variable"] = "motion"
    if experiment is not None:
        decision["experiment"] = experiment
    return {
        "schema": V2,
        "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [
            {"expectation_id": e["id"], "observed": observed}
            for e in row["expectations"]
        ],
        "findings": [],
        "reviewer": {"kind": "model_visual", "name": "experiment-test"},
        "decision": decision,
    }


def _shot(project, add_shot, *, duration_ms=1000):
    add_shot(project, "S001", quality={"must_show": ["red umbrella appears"]})
    take = _take(project, "S001", duration_ms=duration_ms)
    return take, _row(project)


def _member(view, take_name):
    return next(
        member
        for family in view["families"]
        for member in family["takes"]
        if member["take"] == take_name
    )


def test_legacy_verdict_without_experiment_loads(tmp_project, add_shot):
    _take_info, row = _shot(tmp_project, add_shot)
    record_verdicts(tmp_project, _verdict(row, experiment=None))

    [record], malformed = read_v2_records(tmp_project)
    assert malformed == 0
    assert "experiment" not in record["decision"]


def test_experiment_binds_to_exact_media(tmp_project, add_shot):
    take, row = _shot(tmp_project, add_shot)
    experiment = _experiment()
    record_verdicts(tmp_project, _verdict(row, experiment=experiment))

    [record], _ = read_v2_records(tmp_project)
    assert record["media_sha256"] == hash_file(take.media_path)
    assert record["media_take"] == take.name
    assert record["spec_hash"] == row["spec_hash"]
    assert record["expectation_digest"] == row["expectation_digest"]
    assert record["decision"]["experiment"] == experiment


def test_invalid_tested_variable_rejected(tmp_project, add_shot):
    _take_info, row = _shot(tmp_project, add_shot)
    with pytest.raises(VerdictError, match="tested_variable"):
        record_verdicts(
            tmp_project,
            _verdict(row, experiment=_experiment(tested_variable="vibes")),
        )
    assert read_v2_records(tmp_project)[0] == []


def test_tested_variable_cannot_be_held_constant(tmp_project, add_shot):
    _take_info, row = _shot(tmp_project, add_shot)
    with pytest.raises(VerdictError, match="held_constant"):
        record_verdicts(
            tmp_project,
            _verdict(
                row,
                experiment=_experiment(held_constant=["camera", "motion"]),
            ),
        )
    assert read_v2_records(tmp_project)[0] == []


@pytest.mark.parametrize(
    "usable_ranges",
    [
        [{"start_ms": -1, "end_ms": 100}],
        [{"start_ms": 100, "end_ms": 100}],
        [{"start_ms": 200, "end_ms": 100}],
        [{"start_ms": 0, "end_ms": 1001}],
    ],
)
def test_usable_range_must_be_ordered_and_within_known_media(
    tmp_project, add_shot, usable_ranges
):
    _take_info, row = _shot(tmp_project, add_shot, duration_ms=1000)
    with pytest.raises(VerdictError, match="usable_ranges_ms"):
        record_verdicts(
            tmp_project,
            _verdict(
                row,
                experiment=_experiment(usable_ranges_ms=usable_ranges),
            ),
        )
    assert read_v2_records(tmp_project)[0] == []


def test_unknown_media_duration_allows_range_with_warning(tmp_project, add_shot):
    _take_info, row = _shot(tmp_project, add_shot, duration_ms=None)
    result = record_verdicts(tmp_project, _verdict(row, experiment=_experiment()))

    assert result["written"] == 1
    assert any("duration" in warning for warning in result["warnings"])


def test_stale_experiment_remains_history_only(tmp_project, add_shot):
    take, row = _shot(tmp_project, add_shot)
    experiment = _experiment()
    record_verdicts(tmp_project, _verdict(row, experiment=experiment))

    current = _member(candidate_families(tmp_project, "S001"), take.name)
    assert current["experiment"] == experiment
    assert current["historical_experiment"] == experiment

    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("action", {}).__setitem__("main", "a changed source beat"),
    )
    stale = _member(candidate_families(tmp_project, "S001"), take.name)
    assert stale["binding_status"] == "stale"
    assert stale["experiment"] is None
    assert stale["historical_experiment"] == experiment
    [record], _ = read_v2_records(tmp_project)
    assert record["decision"]["experiment"] == experiment


def test_experiment_does_not_change_assurance(tmp_project, add_shot):
    _take_info, row = _shot(tmp_project, add_shot)
    record_verdicts(tmp_project, _verdict(row, experiment=None))
    without_experiment = compute_assurance(tmp_project, "S001")

    record_verdicts(tmp_project, _verdict(row, experiment=_experiment()))
    with_experiment = compute_assurance(tmp_project, "S001")

    without_experiment["evidence"].pop("observed_at")
    with_experiment["evidence"].pop("observed_at")
    assert with_experiment == without_experiment


def test_experiment_does_not_change_spec_hash(tmp_project, add_shot):
    _take_info, row = _shot(tmp_project, add_shot)
    shot = tmp_project.load_shot("S001")
    before = compute_spec_hash(
        shot,
        tmp_project.load_bible(),
        version=SPEC_VERSION,
        project_root=tmp_project.root,
    )

    record_verdicts(tmp_project, _verdict(row, experiment=_experiment()))

    after = compute_spec_hash(
        tmp_project.load_shot("S001"),
        tmp_project.load_bible(),
        version=SPEC_VERSION,
        project_root=tmp_project.root,
    )
    assert after == before


def test_experiment_does_not_enter_execution_attempt_log(tmp_project, add_shot):
    from manju.build.attempts import read_attempts

    _take_info, row = _shot(tmp_project, add_shot)
    record_verdicts(
        tmp_project,
        _verdict(row, experiment=_experiment()),
    )

    attempts, malformed = read_attempts(tmp_project)
    assert attempts == []
    assert malformed == 0


def test_manju_check_rejects_tampered_usable_range(tmp_project, add_shot):
    from manju.core.check import run_check

    _take_info, row = _shot(tmp_project, add_shot)
    record_verdicts(tmp_project, _verdict(row, experiment=_experiment()))
    log = tmp_project.reports_dir / "qc_agent.jsonl"
    record = json.loads(log.read_text(encoding="utf-8"))
    record["decision"]["experiment"]["usable_ranges_ms"] = [
        {"start_ms": 900, "end_ms": 100}
    ]
    log.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    report = run_check(tmp_project)
    assert any("usable_ranges_ms" in error for error in report.errors)
