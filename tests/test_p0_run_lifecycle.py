"""P0 WP4 — run lifecycle events + honest RunManifest (contract §6, §9 9-11).

RED at HEAD 76be954 (WP0-G, recorded before the fix): the manifest claimed
COMPLETED for (a) an interrupted run (attempt handle opened, no terminal —
attempt_count 0), (b) a run with a finished generate attempt but NO run-end
attempt (crash later in the build), and (c) a run_id with NO EVENTS AT ALL.
An interrupted or unknown run could never be told apart from a clean success.

Now: run_started/run_terminal/attempt_started ride the same events.jsonl
(best-effort, schema manju.run-lifecycle/v1); the manifest derives the exact
vocabulary COMPLETED / COMPLETED_WITH_WARNINGS / FAILED / CANCELED /
WAITING_USER / INCOMPLETE / NOT_FOUND, with dangling_attempts on INCOMPLETE
and legacy_terminal_only on pre-WP4 streams.
"""

from __future__ import annotations

import json

from manju.build import attempts as A
from manju.build.graph import run_build


def _lifecycle_lines(root, action):
    out = []
    path = root / "events.jsonl"
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln)
        if rec.get("action") == action:
            out.append(rec)
    return out


# ==================== 6 (WP0-G). interrupted run -> INCOMPLETE + dangling


def test_interrupted_run_is_incomplete_with_dangling_attempt(tmp_path):
    """§9.9: run_started + attempt_started then a simulated crash (no terminals
    at all) → INCOMPLETE, with that attempt listed in dangling_attempts.
    RED at HEAD: this exact stream shape derived COMPLETED."""
    A.append_run_started(tmp_path, "run_int", target="final", gen="missing")
    A.append_attempt_started(tmp_path, "run_int", "att_dangling",
                             stage="generate", provider="video_x")
    # (simulated hard crash: no stage_attempt terminal, no run_terminal)

    m = A.build_run_manifest(tmp_path, "run_int")
    assert m["terminal_status"] == A.MANIFEST_INCOMPLETE
    assert m["dangling_attempts"] == [
        {"attempt_id": "att_dangling", "stage": "generate", "provider": "video_x"}
    ]


def test_incomplete_only_lists_attempts_without_terminals(tmp_path):
    """A started attempt whose terminal DID land is not dangling — only the
    started-but-never-terminated one is listed."""
    A.append_run_started(tmp_path, "run_mix", target="final", gen="missing")
    A.append_attempt_started(tmp_path, "run_mix", "att_done", stage="generate",
                             provider="p")
    A.append_attempt_started(tmp_path, "run_mix", "att_lost", stage="render")
    A.append_attempt(tmp_path, {"run_id": "run_mix", "attempt_id": "att_done",
                                "stage": "generate", "action": "generate",
                                "state": A.SUCCEEDED, "sequence": 1})
    m = A.build_run_manifest(tmp_path, "run_mix")
    assert m["terminal_status"] == A.MANIFEST_INCOMPLETE  # run never terminated
    assert m["dangling_attempts"] == [{"attempt_id": "att_lost", "stage": "render"}]


# ========================= 7. clean lifecycle -> COMPLETED, no dangling


def test_complete_lifecycle_run_is_completed_with_no_dangling(tmp_path):
    """§9.10: attempt_started + its stage_attempt terminal + run_terminal
    (completed) → COMPLETED, and no dangling_attempts key at all."""
    A.append_run_started(tmp_path, "run_ok", target="final", gen="missing")
    A.append_attempt_started(tmp_path, "run_ok", "att_1", stage="generate",
                             provider="p")
    A.append_attempt(tmp_path, {"run_id": "run_ok", "attempt_id": "att_1",
                                "stage": "generate", "action": "generate",
                                "state": A.SUCCEEDED, "sequence": 1})
    A.append_run_terminal(tmp_path, "run_ok", status="completed",
                          counts={"generated": 1})
    m = A.build_run_manifest(tmp_path, "run_ok")
    assert m["terminal_status"] == A.COMPLETED
    assert "dangling_attempts" not in m
    assert "legacy_terminal_only" not in m


def test_run_terminal_status_verbatim_with_warnings_promotion(tmp_path):
    """run_terminal's status is taken verbatim; a completed run with failure
    attempts on record promotes to COMPLETED_WITH_WARNINGS (and failed/canceled/
    waiting_user map verbatim)."""
    # completed + a FAILED attempt on record -> promotion
    A.append_run_started(tmp_path, "run_w", target="final", gen="missing")
    A.append_attempt(tmp_path, {"run_id": "run_w", "stage": "generate",
                                "action": "generate", "state": A.FAILED,
                                "sequence": 1,
                                "failure": {"category": "provider",
                                            "code": "provider_error",
                                            "message": "boom"}})
    A.append_run_terminal(tmp_path, "run_w", status="completed")
    assert A.build_run_manifest(tmp_path, "run_w")["terminal_status"] == \
        A.COMPLETED_WITH_WARNINGS
    # the other statuses map verbatim
    for rid, status, expected in (
            ("run_f", "failed", A.MANIFEST_FAILED),
            ("run_c", "canceled", A.MANIFEST_CANCELED),
            ("run_u", "waiting_user", A.MANIFEST_WAITING_USER)):
        A.append_run_started(tmp_path, rid)
        A.append_run_terminal(tmp_path, rid, status=status)
        assert A.build_run_manifest(tmp_path, rid)["terminal_status"] == expected


def test_run_build_emits_started_and_terminal_end_to_end(tmp_project):
    """Integration: a real run_build (here: no shots → a clean failure) emits
    run_started at its start and run_terminal(failed) on the exit path; the
    materialized manifest says FAILED — never COMPLETED, never INCOMPLETE."""
    result = run_build(tmp_project, target="qc", gen="off", actor="engine")
    assert result.ok is False and result.run_id

    started = _lifecycle_lines(tmp_project.root, A.RUN_STARTED_ACTION)
    terms = _lifecycle_lines(tmp_project.root, A.RUN_TERMINAL_ACTION)
    assert [r["detail"]["run_id"] for r in started] == [result.run_id]
    assert [r["detail"]["run_id"] for r in terms] == [result.run_id]
    assert terms[0]["detail"]["status"] == "failed"
    assert terms[0]["detail"]["schema"] == A.LIFECYCLE_SCHEMA

    m = A.build_run_manifest(tmp_project, result.run_id)
    assert m["terminal_status"] == A.MANIFEST_FAILED
    assert "legacy_terminal_only" not in m


# ======================================= 8. unknown run id -> NOT_FOUND


def test_unknown_run_id_is_not_found(tmp_path):
    """§9.11: a run_id with no events at all is NOT_FOUND — never COMPLETED.
    RED at HEAD: it derived COMPLETED with attempt_count 0."""
    m = A.build_run_manifest(tmp_path, "run_never_existed")
    assert m["terminal_status"] == A.MANIFEST_NOT_FOUND
    assert m["attempt_count"] == 0


# ==================== 9. legacy terminal-only stream keeps deriving


def test_legacy_terminal_only_stream_derives_with_stamp(tmp_path):
    """§9: a pre-WP4 fixture (attempt terminals + the run-end attempt, ZERO
    lifecycle events) still derives exactly today's status — stamped
    legacy_terminal_only: true, and no started evidence is fabricated."""
    ev = A.RunEvidence(tmp_path, "run_legacy")
    ev.attempt("generate", {"kind": "shot", "shot": "S001"}, "generate").succeeded(
        outputs=[A.output_ref("take", path="a.mp4", sha256="sha256:1")])
    ev.run_succeeded()  # the OLD-shape run-end stage_attempt

    m = A.build_run_manifest(tmp_path, "run_legacy")
    assert m["terminal_status"] == A.COMPLETED       # unchanged from today
    assert m["legacy_terminal_only"] is True
    assert "dangling_attempts" not in m

    # a legacy run with NO run-end attempt keeps today's conservative parts
    # derivation too (stamped legacy — the terminals prove no more than before)
    ev2 = A.RunEvidence(tmp_path, "run_legacy2")
    ev2.attempt("generate", {"kind": "shot", "shot": "S001"}, "generate")._emit(
        A.FAILED, failure={"category": "x", "code": "x", "message": "boom"})
    m2 = A.build_run_manifest(tmp_path, "run_legacy2")
    assert m2["terminal_status"] == A.MANIFEST_FAILED
    assert m2["legacy_terminal_only"] is True
