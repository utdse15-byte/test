"""TRISURFACE R2-1 — the v2 visual-QC verdict loop must be visible end to end.

Red-first evidence (TRISURFACE_TEST_2026-07-29.md §R2-1): submitting the exact
``manju.qc.verdict/v2`` shape the brief instructs produced, before this fix:

- a CLI crash AFTER writing (``KeyError: 'levels'`` — the human branch read the
  legacy return shape off the v2 intake result);
- ``qc coverage`` reporting the just-reviewed shot as ``never`` (the coverage
  reader was legacy-only, and DR02 ruling #1 makes the legacy reader SKIP v2
  lines);
- v2 ``findings`` never folding into ``manju qc`` as ``[AI判读]`` items (same
  legacy-only reader in the fold).

The DR02 architecture stands: observations still feed assurance ONLY, binding
fields still come from the packet (bytes A), and the legacy reader still skips
v2 lines. What this wave adds is the missing CONSUMER wiring for coverage and
the findings fold, plus a CLI human branch that tolerates both return shapes.
"""

from __future__ import annotations

from manju.core.models import TakeSidecar
from manju.qc.agent_review import (
    agent_verdict_items,
    qc_brief,
    qc_coverage,
    record_verdicts,
)

V2 = "manju.qc.verdict/v2"


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _take_with_bytes(project, shot_id, data: bytes, *, select=True):
    tmp = project.root / f"_src_{shot_id}.mp4"
    tmp.write_bytes(data)
    take = project.register_take(shot_id, tmp, TakeSidecar(provider="test", spec_hash="h"))
    tmp.unlink()
    if select:
        _select(project, shot_id, take.name)
    return take


def _brief_row(project, shot_id):
    return next(r for r in qc_brief(project, [shot_id])["shots"] if r["shot"] == shot_id)


def _v2_payload(row, findings):
    return {
        "schema": V2,
        "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [],
        "findings": findings,
        "reviewer": {"kind": "model_visual", "name": "test"},
    }


def _bound_v2_intake(project, add_shot, *, findings):
    add_shot(project, "S001")
    _take_with_bytes(project, "S001", b"AAAA-bytes-A")
    row = _brief_row(project, "S001")
    result = record_verdicts(project, _v2_payload(row, findings), actor="ai")
    assert result["written"] == 1
    return result


# ------------------------------------------------- intake result shape (CLI)


def test_v2_intake_result_supports_the_cli_human_branch(tmp_project, add_shot):
    """The CLI human branch prints written/path and a per-level summary. The v2
    return must carry every key that branch reads — the crash was
    ``result["levels"]`` raising ``KeyError`` AFTER the write had landed."""
    result = _bound_v2_intake(
        tmp_project, add_shot,
        findings=[{"level": "fyi", "message": "测试判读"}])
    assert result["written"] == 1
    assert result["path"]
    # the fix: the v2 result carries a levels summary too (fyi=1 here), so the
    # human branch prints one line instead of stacktracing.
    assert result["levels"] == {"fyi": 1}
    # the v2-specific bindings summary stays — agents key on it.
    assert result["bindings"] == {"bound": 1}


def test_cli_qc_verdict_human_output_never_stacktraces(tmp_project, add_shot, monkeypatch):
    """End-to-end through the Typer command: rc=0 and a human line, no
    Traceback. Uses --from-file - (stdin) against the real project."""
    import json as _json

    from typer.testing import CliRunner

    from manju.cli import app

    add_shot(tmp_project, "S001")
    _take_with_bytes(tmp_project, "S001", b"AAAA-bytes-A")
    row = _brief_row(tmp_project, "S001")
    payload = _v2_payload(row, [{"level": "fyi", "message": "CLI 路测试"}])

    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()
    res = runner.invoke(app, ["qc", "verdict", "--from-file", "-"],
                        input=_json.dumps(payload, ensure_ascii=False))
    assert res.exit_code == 0, res.output
    assert "Traceback" not in res.output
    assert "已回填 1 条" in res.output


# --------------------------------------------------------------- coverage


def test_v2_reviewed_shot_counts_in_coverage(tmp_project, add_shot):
    """A bound v2 verdict whose packet bytes still match the current selected
    take is COVERAGE — the shot reads ``reviewed``, not ``never``."""
    _bound_v2_intake(tmp_project, add_shot,
                     findings=[{"level": "fyi", "message": "覆盖率测试"}])
    cov = qc_coverage(tmp_project)
    assert cov["shots"]["S001"] == "reviewed"


def test_v2_coverage_goes_stale_when_bytes_move(tmp_project, add_shot):
    """Regenerating + selecting a new take stales the v2 review — never
    silently 'reviewed' against bytes nobody looked at."""
    _bound_v2_intake(tmp_project, add_shot,
                     findings=[{"level": "fyi", "message": "过期测试"}])
    _take_with_bytes(tmp_project, "S001", b"BBBB-bytes-B")  # selects take_02
    cov = qc_coverage(tmp_project)
    assert cov["shots"]["S001"] == "stale"


# ------------------------------------------------------------ findings fold


def test_v2_findings_fold_into_qc_items_when_bound(tmp_project, add_shot):
    """The brief promises blocker→error / issue→warn / fyi→info. A bound v2
    finding must surface as an ``[AI判读]`` content item at the mapped level."""
    _bound_v2_intake(
        tmp_project, add_shot,
        findings=[{"level": "issue", "message": "左手穿帮了", "evidence": "frame:2"}])
    items = agent_verdict_items(tmp_project)
    hits = [i for i in items if "左手穿帮了" in i.message]
    assert hits, [i.message for i in items]
    assert hits[0].level == "warn"
    assert hits[0].message.startswith("[AI判读]")
    assert hits[0].subject == "S001"


def test_v2_findings_of_moved_bytes_surface_as_expired_not_as_findings(
        tmp_project, add_shot):
    """Once the bytes move, the old v2 finding must NOT keep asserting itself;
    one honest 已过期 info item replaces it (mirrors the legacy fold)."""
    _bound_v2_intake(
        tmp_project, add_shot,
        findings=[{"level": "issue", "message": "左手穿帮了"}])
    _take_with_bytes(tmp_project, "S001", b"BBBB-bytes-B")
    items = agent_verdict_items(tmp_project)
    assert not [i for i in items if "左手穿帮了" in i.message]
    expired = [i for i in items if i.subject == "S001" and "已过期" in i.message]
    assert expired and expired[0].level == "info"


def _consistency_pair(project, add_shot):
    from manju.core.yamlio import write_yaml

    write_yaml(project.root / "bible" / "scenes.yaml", {"cs": {"name": "便利店"}})
    write_yaml(project.root / "bible" / "characters.yaml", {"linxia": {"name": "林夏"}})
    add_shot(project, "S001", scene="cs", characters=["linxia"])
    add_shot(project, "S002", scene="cs", characters=["linxia"])
    _take_with_bytes(project, "S001", b"s1-aaaa")
    _take_with_bytes(project, "S002", b"s2-bbbb")
    brief = qc_brief(project, mode="consistency")
    return next(u for u in brief["units"] if u["unit"] == "character:linxia")


def _v2_unit_payload(unit, findings):
    return {
        "schema": V2, "packet_id": unit["packet_id"],
        "subject": {"kind": "unit", "id": unit["unit"]},
        "observations": [], "findings": findings,
        "reviewer": {"kind": "model_visual", "name": "test"},
    }


def test_v2_unit_verdict_counts_in_coverage_and_folds(tmp_project, add_shot):
    """The unit-scoped twin: a bound v2 unit verdict is coverage AND its
    findings fold; a member regenerating flips both to stale/expired."""
    unit = _consistency_pair(tmp_project, add_shot)
    record_verdicts(tmp_project, _v2_unit_payload(
        unit, [{"level": "issue", "message": "两镜身份不一致"}]), actor="ai")

    cov = qc_coverage(tmp_project)
    assert cov["units"]["character:linxia"]["state"] == "reviewed"
    items = agent_verdict_items(tmp_project)
    assert [i for i in items if "两镜身份不一致" in i.message]

    _take_with_bytes(tmp_project, "S002", b"s2-CCCC")  # member moves
    cov = qc_coverage(tmp_project)
    assert cov["units"]["character:linxia"]["state"] == "stale"
    items = agent_verdict_items(tmp_project)
    assert not [i for i in items if "两镜身份不一致" in i.message]
    assert [i for i in items if i.subject == "character:linxia" and "已过期" in i.message]


def test_legacy_records_still_fold_exactly_as_before(tmp_project, add_shot):
    """Guard the seam: adding v2 consumers must not disturb the legacy path."""
    add_shot(tmp_project, "S001")
    take = _take_with_bytes(tmp_project, "S001", b"AAAA-bytes-A")
    record_verdicts(tmp_project, {"verdicts": [
        {"shot": "S001", "take": take.name, "criterion": "composition",
         "level": "issue", "message": "旧格式判读"},
    ]}, actor="ai")
    items = agent_verdict_items(tmp_project)
    hits = [i for i in items if "旧格式判读" in i.message]
    assert hits and hits[0].level == "warn"
    cov = qc_coverage(tmp_project)
    assert cov["shots"]["S001"] == "reviewed"
