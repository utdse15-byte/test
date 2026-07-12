"""FP Loop M — the run performance report (§8.6), a pure DERIVED view.

These pin ``manju.qc.runperf.run_performance`` directly against synthetic
attempt-evidence fixtures (the DR03C ``events.jsonl`` stream — no ffmpeg, no
build): exact durations/counts, the honest ``unavailable[]`` list, the
cache-hit-rate derivation from the recorded ``SKIPPED_CACHE_HIT`` state, the
provider-vs-local split, determinism, the structured-empty (never-crash) path,
and the §8.7 pin that nothing under ``build/`` imports the module (a
performance hint is never a scheduling fact).

Durations are pinned by writing evidence with EXPLICIT ``duration_ms`` /
``started_at`` / ``ended_at`` via :func:`append_attempt` (which preserves them
verbatim — it never re-times), so the report's arithmetic is exact and
independent of wall clock. A run built through ``RunEvidence`` would carry a
monotonic ``duration_ms`` we could not pin.
"""

from __future__ import annotations

from pathlib import Path

from manju.build import attempts as A
from manju.qc import runperf as RP


# --------------------------------------------------------------------- helpers


def _att(root: Path, run_id: str, seq: int, **fields) -> None:
    """Append ONE synthetic attempt-evidence line with pinned timing."""
    payload = {"run_id": run_id, "sequence": seq, "schema": A.SCHEMA}
    payload.update(fields)
    A.append_attempt(root, payload)


def _seed_full_run(root: Path, run_id: str = "run_1") -> None:
    """A canonical multi-stage run: a fresh provider gen (with cost), a failed
    provider gen, a per-shot cache skip, a local render, and the run envelope.
    Every duration is pinned so the report arithmetic is exact."""
    # provider generate SUCCEEDED, 3000ms, actual cost 0.5 (estimate 9.0 ignored)
    _att(root, run_id, 1, stage="generate", action="generate", state=A.SUCCEEDED,
         attempt_id="att_gen0001", unit={"kind": "shot", "shot": "S001"},
         executor={"kind": "provider", "provider_id": "runway"},
         duration_ms=3000, started_at="2026-07-12T10:00:00+00:00",
         ended_at="2026-07-12T10:00:03+00:00",
         cost={"actual": 0.5, "estimated": 9.0, "currency": "CNY"},
         outputs=[A.output_ref("take", path="shots/S001/t1.mp4",
                               sha256="sha256:1", bytes=1000)])
    # provider generate FAILED, 500ms, rate_limited
    _att(root, run_id, 2, stage="generate", action="generate", state=A.FAILED,
         attempt_id="att_gen0002", unit={"kind": "shot", "shot": "S002"},
         executor={"kind": "provider", "provider_id": "runway"},
         duration_ms=500, started_at="2026-07-12T10:00:03+00:00",
         ended_at="2026-07-12T10:00:03+00:00",
         failure={"category": "rate_limited", "code": "rate_limited", "message": "429"})
    # per-shot cache hit (no provider executor), 1ms
    _att(root, run_id, 3, stage="generate", action="cache_hit",
         state=A.SKIPPED_CACHE_HIT, attempt_id="att_gen0003",
         unit={"kind": "shot", "shot": "S003"}, duration_ms=1,
         started_at="2026-07-12T10:00:03+00:00", ended_at="2026-07-12T10:00:03+00:00")
    # local render SUCCEEDED, 8000ms
    _att(root, run_id, 4, stage="render", action="render", state=A.SUCCEEDED,
         attempt_id="att_ren0004", unit={"kind": "render", "target": "final"},
         duration_ms=8000, started_at="2026-07-12T10:00:03+00:00",
         ended_at="2026-07-12T10:00:11+00:00",
         outputs=[A.output_ref("final", path="renders/final/v1.mp4",
                               sha256="sha256:2", bytes=50000)])
    # run-level envelope (wall span; excluded from per-stage aggregation)
    _att(root, run_id, 5, stage="build", action="run", state=A.SUCCEEDED,
         attempt_id="att_run0005", unit={"kind": "project"}, duration_ms=11000,
         started_at="2026-07-12T10:00:00+00:00", ended_at="2026-07-12T10:00:11+00:00",
         run_context={"command": "build", "target": "final", "mode": None})


# ------------------------------------------------------- (1) exact durations


def test_pins_exact_stage_durations_and_counts(tmp_path):
    _seed_full_run(tmp_path)
    rep = RP.run_performance(tmp_path, "run_1")

    assert rep["found"] is True and rep["run_id"] == "run_1"
    assert rep["attempt_count"] == 5 and rep["malformed_lines"] == 0
    # run_context projected from the envelope
    assert (rep["command"], rep["target"], rep["mode"]) == ("build", "final", None)

    # wall = the envelope's own span, 11s
    assert rep["wall"] == {"ms": 11000, "started_at": "2026-07-12T10:00:00+00:00",
                           "ended_at": "2026-07-12T10:00:11+00:00", "source": "run_envelope"}

    stages = {s["stage"]: s for s in rep["stages"]}
    # the run-level build/run envelope is NEVER a stage row (no double count)
    assert set(stages) == {"generate", "render"}
    assert stages["generate"]["total_ms"] == 3501   # 3000 + 500 + 1
    assert stages["generate"]["attempts"] == 3
    assert stages["generate"]["status_counts"] == {"FAILED": 1, "SKIPPED_CACHE_HIT": 1,
                                                   "SUCCEEDED": 1}
    assert stages["render"]["total_ms"] == 8000 and stages["render"]["max_ms"] == 8000


def test_slowest_stages_and_attempts_are_ordered_by_recorded_duration(tmp_path):
    _seed_full_run(tmp_path)
    rep = RP.run_performance(tmp_path, "run_1")
    assert rep["slowest_stages"] == [{"stage": "render", "total_ms": 8000},
                                     {"stage": "generate", "total_ms": 3501}]
    # slowest ATTEMPTS: render(8000) > S001(3000) > S002(500) > S003(1)
    ids = [(a["stage"], a["duration_ms"]) for a in rep["slowest_attempts"]]
    assert ids == [("render", 8000), ("generate", 3000), ("generate", 500),
                   ("generate", 1)]


def test_duration_is_read_from_recorded_evidence_never_recomputed(tmp_path):
    # a single attempt with an absurd pinned duration — the report echoes the
    # RECORDED number, proving it never re-times against the wall clock.
    _att(tmp_path, "run_z", 1, stage="render", action="render", state=A.SUCCEEDED,
         unit={"kind": "render", "target": "final"}, duration_ms=987654,
         started_at="2026-07-12T10:00:00+00:00", ended_at="2026-07-12T10:16:27+00:00")
    _att(tmp_path, "run_z", 2, stage="build", action="run", state=A.SUCCEEDED,
         unit={"kind": "project"}, duration_ms=987654,
         started_at="2026-07-12T10:00:00+00:00", ended_at="2026-07-12T10:16:27+00:00")
    rep = RP.run_performance(tmp_path, "run_z")
    assert next(s for s in rep["stages"] if s["stage"] == "render")["total_ms"] == 987654


# ------------------------------------------------- (2) provider vs local split


def test_provider_vs_local_time_split_where_rows_distinguish(tmp_path):
    _seed_full_run(tmp_path)
    ts = RP.run_performance(tmp_path, "run_1")["time_split"]
    # provider = executor.kind=='provider' (the two gen attempts): 3000 + 500
    assert ts["provider_ms"] == 3500 and ts["provider_attempts"] == 2
    # local = every other non-envelope attempt (render 8000 + cache skip 1)
    assert ts["local_ms"] == 8001 and ts["local_attempts"] == 2
    assert ts["render_ms"] == 8000
    assert ts["by_provider"] == [{"provider_id": "runway", "attempts": 2,
                                  "total_ms": 3500}]


# ------------------------------------------- (3) cache hit rate derivation


def test_cache_hit_rate_is_derived_from_the_recorded_skipped_cache_hit_state(tmp_path):
    _seed_full_run(tmp_path)
    cache = RP.run_performance(tmp_path, "run_1")["cache"]
    # eligible = SUCCEEDED + SKIPPED_CACHE_HIT (a FAILED gen is not cache-decidable)
    # overall: 1 hit / 3 eligible (S001 succ, S003 skip, render succ)
    assert cache["hits"] == 1 and cache["eligible"] == 3 and cache["rate"] == 0.3333
    by_stage = {s["stage"]: s for s in cache["by_stage"]}
    assert by_stage["generate"] == {"stage": "generate", "hits": 1, "eligible": 2,
                                    "rate": 0.5}
    assert by_stage["render"]["hits"] == 0 and by_stage["render"]["rate"] == 0.0
    assert "SKIPPED_CACHE_HIT" in cache["source"]


# --------------------------------------------------------- (4) cost + errors


def test_cost_totals_prefer_actual_and_split_by_provider_and_shot(tmp_path):
    _seed_full_run(tmp_path)
    cost = RP.run_performance(tmp_path, "run_1")["cost"]
    # actual 0.5 counted; the 9.0 estimate on the same attempt is NEVER added
    assert cost["totals"] == [{"currency": "CNY", "amount": 0.5}]
    assert cost["by_provider"] == [{"provider_id": "runway", "currency": "CNY",
                                    "amount": 0.5}]
    assert cost["by_shot"] == [{"shot": "S001", "currency": "CNY", "amount": 0.5}]


def test_error_categories_group_by_recorded_failure_code(tmp_path):
    _seed_full_run(tmp_path)
    errors = RP.run_performance(tmp_path, "run_1")["errors"]
    assert errors["count"] == 1
    assert errors["by_category"] == [{"category": "rate_limited", "count": 1,
                                      "codes": [{"code": "rate_limited", "count": 1}]}]


def test_recorded_output_bytes_is_the_honest_proxy_not_disk_usage(tmp_path):
    _seed_full_run(tmp_path)
    rep = RP.run_performance(tmp_path, "run_1")
    assert rep["recorded_output_bytes"] == 51000  # 1000 (take) + 50000 (final)
    # and disk_usage is honestly listed unavailable, NOT conflated with the proxy
    assert "disk_usage" in {u["metric"] for u in rep["unavailable"]}


def test_per_shot_rollup_pins_duration_and_cost(tmp_path):
    _seed_full_run(tmp_path)
    by_shot = {s["shot"]: s for s in RP.run_performance(tmp_path, "run_1")["by_shot"]}
    assert by_shot["S001"]["total_ms"] == 3000 and by_shot["S001"]["cost_amount"] == 0.5
    assert by_shot["S002"]["states"] == {"FAILED": 1}
    assert by_shot["S003"]["states"] == {"SKIPPED_CACHE_HIT": 1}


# ------------------------------------------------ (5) unavailable[] honesty


def test_unavailable_lists_qc_time_and_disk_usage_with_missing_source(tmp_path):
    """§8.6 metrics with NO recorded source are listed honestly (never
    estimated): QC is never timed on the stream, disk usage is never measured."""
    _seed_full_run(tmp_path)
    rep = RP.run_performance(tmp_path, "run_1")
    unavailable = {u["metric"]: u["missing_source"] for u in rep["unavailable"]}
    assert set(unavailable) == {"qc_time", "disk_usage"}
    # every entry names WHY it is unavailable (the missing source)
    assert "qc" in unavailable["qc_time"] and "evidence_refs" in unavailable["qc_time"]
    assert "disk" in unavailable["disk_usage"]


def test_cache_row_is_NOT_unavailable_because_the_audit_found_a_real_source(tmp_path):
    """The honesty pin (addendum): the cache row would belong in unavailable[]
    UNLESS the audit found a real recorded source. It DID —
    ``SKIPPED_CACHE_HIT`` is a first-class recorded state emitted per stage by
    build.graph — so cache hit rate is populated, not listed unavailable."""
    _seed_full_run(tmp_path)
    rep = RP.run_performance(tmp_path, "run_1")
    metrics = {u["metric"] for u in rep["unavailable"]}
    assert "cache_hit_rate" not in metrics and "cache" not in metrics
    # and it is genuinely DERIVED (a real number from real recorded state)
    assert rep["cache"]["rate"] == 0.3333
    # the recorded source really is the SKIPPED_CACHE_HIT state on the stream
    assert A.SKIPPED_CACHE_HIT == "SKIPPED_CACHE_HIT"


# --------------------------------------------- (6) missing evidence / empty


def test_missing_events_file_is_a_structured_empty_report_never_crashes(tmp_path):
    rep = RP.run_performance(tmp_path)  # no events.jsonl at all, latest=None
    assert rep["found"] is False and rep["terminal_status"] == "NOT_FOUND"
    assert rep["run_id"] is None
    # every section present + empty (a consumer never has to guard for a key)
    assert rep["stages"] == [] and rep["slowest_attempts"] == []
    assert rep["cost"]["totals"] == [] and rep["errors"]["count"] == 0
    assert rep["wall"]["ms"] is None and rep["recorded_output_bytes"] == 0
    # the structural unavailable[] is emitted even for an empty report
    assert {u["metric"] for u in rep["unavailable"]} == {"qc_time", "disk_usage"}


def test_unknown_run_id_is_structured_empty_not_found(tmp_path):
    _seed_full_run(tmp_path)
    rep = RP.run_performance(tmp_path, "no_such_run")
    assert rep["found"] is False and rep["terminal_status"] == "NOT_FOUND"
    assert rep["run_id"] == "no_such_run" and rep["stages"] == []


def test_torn_tail_line_never_crashes_the_report(tmp_path):
    _seed_full_run(tmp_path)
    with open(tmp_path / "events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"ts": "2026", "action": "stage_attempt", "detail": {"run_id')
    rep = RP.run_performance(tmp_path, "run_1")
    assert rep["found"] is True and rep["malformed_lines"] == 1
    assert rep["attempt_count"] == 5  # the torn tail never joins the records


# --------------------------------------------------- (7) latest-run + determinism


def test_latest_run_is_the_last_run_written_on_the_stream(tmp_path):
    _seed_full_run(tmp_path, "run_1")
    # a second, later run — same file, appended after
    _att(tmp_path, "run_2", 1, stage="render", action="render", state=A.SUCCEEDED,
         unit={"kind": "render", "target": "final"}, duration_ms=42,
         started_at="2026-07-12T11:00:00+00:00", ended_at="2026-07-12T11:00:00+00:00")
    _att(tmp_path, "run_2", 2, stage="build", action="run", state=A.SUCCEEDED,
         unit={"kind": "project"}, duration_ms=42,
         started_at="2026-07-12T11:00:00+00:00", ended_at="2026-07-12T11:00:00+00:00")
    assert RP._latest_run_id(tmp_path) == "run_2"
    assert RP.run_performance(tmp_path)["run_id"] == "run_2"  # default = latest
    assert RP.run_performance(tmp_path, "run_1")["run_id"] == "run_1"  # explicit still works


def test_report_is_deterministic_for_the_same_evidence(tmp_path):
    _seed_full_run(tmp_path)
    r1 = RP.run_performance(tmp_path, "run_1")
    r2 = RP.run_performance(tmp_path, "run_1")
    assert r1 == r2  # no wall-clock field, no generated_at — byte-stable
    # and stable JSON (no unserialisable objects, sorted where it matters)
    import json
    assert json.loads(json.dumps(r1, ensure_ascii=False)) == r1


def test_incomplete_run_without_envelope_uses_the_event_span(tmp_path):
    # a run interrupted before the run-level terminal: no build/run envelope, so
    # wall falls back to the min/max of the stage attempts' recorded stamps.
    _att(tmp_path, "run_i", 1, stage="generate", action="generate", state=A.SUCCEEDED,
         unit={"kind": "shot", "shot": "S001"},
         executor={"kind": "provider", "provider_id": "runway"}, duration_ms=2000,
         started_at="2026-07-12T09:00:00+00:00", ended_at="2026-07-12T09:00:02+00:00")
    _att(tmp_path, "run_i", 2, stage="render", action="render", state=A.SUCCEEDED,
         unit={"kind": "render", "target": "final"}, duration_ms=3000,
         started_at="2026-07-12T09:00:02+00:00", ended_at="2026-07-12T09:00:05+00:00")
    rep = RP.run_performance(tmp_path, "run_i")
    assert rep["wall"] == {"ms": 5000, "started_at": "2026-07-12T09:00:00+00:00",
                           "ended_at": "2026-07-12T09:00:05+00:00", "source": "event_span"}


# ----------------------------------------- (8) §8.7 pin: engine never imports


def test_no_build_module_imports_runperf_a_hint_is_never_a_scheduling_fact():
    """§8.7 原则 '性能提示不是调度事实': the performance view is a pure read-side
    derived report. Its enforceable form is that NOTHING under src/manju/build/
    (the engine/scheduler) imports it — only the CLI does, lazily."""
    build_dir = Path(A.__file__).resolve().parent  # src/manju/build/
    offenders = []
    for py in sorted(build_dir.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        if "runperf" in text:
            offenders.append(py.name)
    assert offenders == [], (
        "a build/ module references runperf — a performance hint must never be a "
        f"scheduling input (§8.7): {offenders}")


def test_runperf_module_does_not_read_wall_clock():
    """A derived view must be deterministic given the evidence — it never reads
    the wall clock. We forbid the CALL forms (datetime.now / time.time /
    .monotonic), not the words: the docstring is free to explain WHY it avoids
    them, and ``datetime`` is imported only for ``fromisoformat`` parsing."""
    src = Path(RP.__file__).read_text(encoding="utf-8")
    assert "datetime.now" not in src
    assert "import time" not in src
    assert "time.time(" not in src
    assert ".monotonic(" not in src


# ------------------------------------------------------- (9) the CLI command

import json  # noqa: E402

from typer.testing import CliRunner  # noqa: E402

from manju.cli import app  # noqa: E402

runner = CliRunner()


def test_cli_perf_json_is_the_full_report(tmp_project, monkeypatch):
    _seed_full_run(tmp_project.root, "run_cli")
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["perf", "run_cli", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # the CLI JSON IS the run_performance report verbatim
    assert payload == RP.run_performance(tmp_project.root, "run_cli")
    assert payload["found"] is True and payload["cache"]["rate"] == 0.3333


def test_cli_perf_defaults_to_latest_and_prints_human_summary(tmp_project, monkeypatch):
    _seed_full_run(tmp_project.root, "run_cli")
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["perf"])  # no run id ⇒ latest
    assert result.exit_code == 0, result.output
    out = result.output
    assert "run_cli" in out and "provider" in out
    # the honest gap list is surfaced to humans too
    assert "unavailable" in out and "qc_time" in out


def test_cli_perf_with_no_runs_is_graceful_not_an_error(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)  # a project with no events.jsonl yet
    result = runner.invoke(app, ["perf"])
    assert result.exit_code == 0  # "no run yet" is a state, not a failure
    assert "未找到" in result.output or "any run" in result.output
