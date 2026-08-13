"""DR03C — the single attempt-evidence stream + derived RunManifest (module).

These pin the ``manju.build.attempts`` contract directly (no ffmpeg needed): the
schema/states, the systematic redaction, the locked concurrent append, the
torn-tail-tolerant projection reader, the UNKNOWN_LEGACY handling, and the
RunManifest derivation (determinism, honest costs, never-guess-from-files, the
verify_outputs verifier). The lifecycle WIRING lives in
``tests/test_dr03c_lifecycle.py``.

The stream IS events.jsonl (Variant A): an attempt is one ``action="stage_attempt"``
event whose ``detail`` is a ``manju.stage-attempt-evidence/v1`` document.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from manju.build import attempts as A
from manju.core.hashing import hash_file, hash_value


# --------------------------------------------------------------------- helpers


def _events_lines(root: Path) -> list[dict]:
    path = root / "events.jsonl"
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _one_attempt(root: Path, run_id: str, *, stage="generate", action="generate",
                 state=A.SUCCEEDED, **fields) -> dict:
    ev = A.RunEvidence(root, run_id)
    h = ev.attempt(stage, {"kind": "shot", "shot": "S001"}, action)
    return h._emit(state, **fields)


# ------------------------------------------------------------------- (1) identity


def test_attempt_has_stable_identity_and_schema(tmp_path):
    rec = _one_attempt(tmp_path, "run_x")
    assert rec["action"] == A.ACTION  # envelope: stage_attempt
    d = rec["detail"]
    assert d["schema"] == A.SCHEMA == "manju.stage-attempt-evidence/v1"
    assert d["run_id"] == "run_x"
    assert d["attempt_id"].startswith("att_") and len(d["attempt_id"]) == 4 + 12
    assert isinstance(d["sequence"], int) and d["sequence"] == 1
    assert d["stage"] == "generate" and d["action"] == "generate"
    assert d["semantic_digest"].startswith("sha256:")


# --------------------------------------------------------------------- (2) states


def test_states_are_the_eleven_and_never_a_done_boolean(tmp_path):
    assert A.STATES == (
        "PLANNED", "SUBMITTED", "RUNNING", "SUCCEEDED", "SKIPPED_CACHE_HIT",
        "FAILED", "CANCELED", "WAITING_USER", "REJECTED_PRECHECK", "ABANDONED",
        "UNKNOWN_LEGACY",
    )
    rec = _one_attempt(tmp_path, "run_x", state=A.SUCCEEDED,
                       outputs=[A.output_ref("take", path="a", sha256="sha256:1")])
    assert "done" not in rec["detail"]
    assert rec["detail"]["state"] in A.STATES


def test_unknown_state_normalises_to_unknown_legacy(tmp_path):
    rec = A.append_attempt(tmp_path, {"run_id": "r", "stage": "x", "action": "y",
                                      "state": "NOT_A_REAL_STATE", "sequence": 1})
    assert rec["detail"]["state"] == A.UNKNOWN_LEGACY


# --------------------------------------------------------------- (12) single event


def test_one_event_per_attempt_no_begin_end_pairs(tmp_path):
    ev = A.RunEvidence(tmp_path, "run_x")
    h = ev.attempt("generate", {"kind": "shot", "shot": "S001"}, "generate")
    h.succeeded(outputs=[A.output_ref("take", path="a", sha256="sha256:1")])
    # a second terminal call is a no-op (idempotent guard) — never two events
    assert h.failed(failure={"category": "x", "code": "x", "message": "late"}) == {}
    attempts = [e for e in _events_lines(tmp_path) if e["action"] == A.ACTION]
    assert len(attempts) == 1  # exactly one terminal record for the attempt
    d = attempts[0]["detail"]
    # the single record carries its own timing, not a paired begin
    assert "started_at" in d and "ended_at" in d and "duration_ms" in d


# ---------------------------------------------------------------- (8/9) redaction


def test_redaction_drops_credentials_prompts_and_signed_urls(tmp_path):
    ev = A.RunEvidence(tmp_path, "run_x")
    secret = "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz012345"
    h = ev.attempt("generate", {"kind": "shot", "shot": "S001"}, "generate",
                   request={"Authorization": f"Bearer {secret}",
                            "api_key": secret,
                            "prompt": "一段很长的中文提示词" * 5,
                            "image": "https://cdn.example.com/x.png?X-Amz-Signature=deadbeef&exp=9"})
    h.succeeded(outputs=[A.output_ref("take", path="shots/S001/take_01.mp4",
                                      sha256="sha256:1")])
    blob = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert secret not in blob                       # (8) no credential leaks
    assert "X-Amz-Signature=deadbeef" not in blob   # (8) signed-url query stripped
    d = [e for e in _events_lines(tmp_path) if e["action"] == A.ACTION][0]["detail"]
    req = d["request"]
    assert req["Authorization"] == "<redacted>"
    assert req["api_key"] == "<redacted>"
    # (9) the prompt survives ONLY as a digest, never full text
    assert set(req["prompt"]) == {"digest", "len"}
    assert req["prompt"]["digest"].startswith("sha256:")
    assert req["image"].endswith("?<redacted>")
    assert d["redactions"]  # what was applied is recorded


def test_provider_request_evidence_digests_over_redacted_params(tmp_path):
    req = A.provider_request_evidence(
        {"seed": 7, "api_key": "sk-proj-SECRETSECRETSECRET12345", "steps": 20},
        spec_hash="sha256:s", duration_ms=3000, candidate_index=0, seed=7)
    assert req["seed"] == 7 and req["candidate_index"] == 0
    assert "sk-proj-SECRET" not in json.dumps(req)  # never in the digest input
    assert req["request_digest"].startswith("sha256:")
    assert "api_key" in req["redacted_summary"]  # key name is fine; value gone


def test_paths_are_project_relative_never_absolute(tmp_path):
    # output_ref/input_ref carry exactly what the caller passes; the wiring
    # always passes project.relpath(...) — assert the helper does not absolutise.
    o = A.output_ref("final", path="renders/final/final_v1.mp4", sha256="sha256:1",
                     bytes=10, content_key="sha256:k")
    assert o["path"] == "renders/final/final_v1.mp4"
    assert not Path(o["path"]).is_absolute()
    i = A.input_ref("prompt", digest="sha256:d")
    assert i == {"role": "prompt", "digest": "sha256:d"}  # ref = digest only


# -------------------------------------------------------------- (10) concurrency


def _writer(root: Path, run_id: str, i: int) -> None:
    A.append_attempt(root, {"run_id": run_id, "stage": "generate", "action": "gen",
                            "state": A.SUCCEEDED, "sequence": i,
                            "unit": {"kind": "shot", "shot": f"S{i:03d}"},
                            "outputs": [A.output_ref("take", path=f"x{i}",
                                                     sha256="sha256:%d" % i,
                                                     note="padding " * 40)]})


def test_concurrent_appends_never_lose_or_tear_a_line(tmp_path):
    n = 40
    threads = [threading.Thread(target=_writer, args=(tmp_path, "run_c", i))
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    # every line parses (no torn/interleaved line) and none is lost
    lines = _events_lines(tmp_path)  # raises on any torn JSON
    attempts = [e for e in lines if e["action"] == A.ACTION]
    assert len(attempts) == n
    records, malformed = A.read_attempts(tmp_path, "run_c")
    assert malformed == 0 and len(records) == n


# ---------------------------------------------------------------- (11) torn tail


def test_torn_tail_line_reports_malformed_but_projection_survives(tmp_path):
    ev = A.RunEvidence(tmp_path, "run_t")  # one context → monotonic sequence
    ev.attempt("generate", {"kind": "shot", "shot": "S001"}, "gen")._emit(A.SUCCEEDED)
    ev.attempt("generate", {"kind": "shot", "shot": "S002"}, "gen")._emit(
        A.FAILED, failure={"category": "x", "code": "x", "message": "boom"})
    # simulate a torn trailing write (a half-flushed line, no newline)
    with open(tmp_path / "events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"ts": "2026", "action": "stage_attempt", "detail": {"run_id')
    records, malformed = A.read_attempts(tmp_path, "run_t")
    assert malformed == 1              # the torn tail is counted
    assert len(records) == 2           # the two prior attempts are unaffected
    assert [r["state"] for r in records] == [A.SUCCEEDED, A.FAILED]


def test_reader_ignores_non_attempt_and_foreign_lines(tmp_path):
    from manju.core.events import append_event

    append_event(tmp_path, "engine", "build", {"ok": True})  # a plain event
    _one_attempt(tmp_path, "run_z", state=A.SUCCEEDED)
    append_event(tmp_path, "engine", "render", {"target": "final"})
    records, malformed = A.read_attempts(tmp_path, "run_z")
    assert malformed == 0
    assert len(records) == 1  # only the one stage_attempt line, others ignored


# ------------------------------------------------------------ (14/15) manifest


def _seed_run(root: Path, run_id: str) -> None:
    ev = A.RunEvidence(root, run_id)
    ev.attempt("generate", {"kind": "shot", "shot": "S001"}, "cache_hit").skipped_cache_hit(
        outputs=[{"role": "take", "take": "take_01", "spec_hash": "sha256:s"}])
    ev.attempt("render", {"kind": "render", "target": "final"}, "render").succeeded(
        outputs=[A.output_ref("final", path="renders/final/final_v1.mp4",
                              sha256="sha256:deadbeef", content_key="sha256:k")])
    ev.add_evidence_ref("reports/qc.json")
    ev.run_succeeded()


def test_manifest_is_deterministic_for_the_same_event_set(tmp_path):
    _seed_run(tmp_path, "run_m")
    p1 = A.materialize_run_manifest(tmp_path, "run_m")
    m1 = json.loads(p1.read_text(encoding="utf-8"))
    p2 = A.materialize_run_manifest(tmp_path, "run_m")
    m2 = json.loads(p2.read_text(encoding="utf-8"))
    # generated_at is a wall-clock incidental (NOT part of evidence_digest);
    # everything semantic is byte-stable across re-materialization.
    assert m1["evidence_digest"] == m2["evidence_digest"]
    del m1["generated_at"], m2["generated_at"]
    assert m1 == m2


def test_manifest_is_derived_atomic_and_deletable(tmp_path):
    _seed_run(tmp_path, "run_m")
    path = A.materialize_run_manifest(tmp_path, "run_m")
    assert path == A.run_manifest_path(tmp_path, "run_m")
    assert path.exists() and path.name == "run.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    assert m["schema"] == A.MANIFEST_SCHEMA
    assert m["terminal_status"] == A.COMPLETED
    assert m["qc_report_refs"] == ["reports/qc.json"]
    assert [o["path"] for o in m["final_output_refs"]] == ["renders/final/final_v1.mp4"]
    # deletable: removing it and re-deriving reproduces the same evidence_digest
    digest = m["evidence_digest"]
    path.unlink()
    assert not path.exists()
    m2 = json.loads(A.materialize_run_manifest(tmp_path, "run_m").read_text(encoding="utf-8"))
    assert m2["evidence_digest"] == digest


# ------------------------------------------------ (13) never guess from files


def test_manifest_never_claims_a_file_without_an_attempt(tmp_path):
    # a stray final on disk with NO render attempt event for this run
    (tmp_path / "renders" / "final").mkdir(parents=True)
    (tmp_path / "renders" / "final" / "final_v9.mp4").write_bytes(b"stray")
    ev = A.RunEvidence(tmp_path, "run_g")
    ev.run_succeeded()  # a run with no render attempt at all
    m = json.loads(A.materialize_run_manifest(tmp_path, "run_g").read_text(encoding="utf-8"))
    assert m["final_output_refs"] == []  # never invented from file existence


# ----------------------------------------------------------------- (16) costs


def test_manifest_costs_prefer_actual_and_never_double_count(tmp_path):
    ev = A.RunEvidence(tmp_path, "run_$")
    # attempt A: actual known -> count actual, ignore its estimate
    ev.attempt("generate", {"kind": "shot", "shot": "S001"}, "gen").succeeded(
        cost={"actual": 0.5, "estimated": 9.0, "currency": "CNY"})
    # attempt B: only an estimate -> count the estimate
    ev.attempt("generate", {"kind": "shot", "shot": "S002"}, "gen").succeeded(
        cost={"estimated": 0.25, "currency": "CNY"})
    ev.run_succeeded()
    m = json.loads(A.materialize_run_manifest(tmp_path, "run_$").read_text(encoding="utf-8"))
    cny = next(c for c in m["costs"] if c["currency"] == "CNY")
    assert cny["amount"] == 0.75  # 0.5 (actual) + 0.25 (estimate), NOT 9.x


# ------------------------------------------------------ (21) verify_outputs


def test_verify_outputs_flags_mutated_bytes(tmp_path):
    final = tmp_path / "renders" / "final" / "final_v1.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"the-real-final-bytes")
    sha = hash_file(final)
    ev = A.RunEvidence(tmp_path, "run_v")
    ev.attempt("render", {"kind": "render", "target": "final"}, "render").succeeded(
        outputs=[A.output_ref("final", path="renders/final/final_v1.mp4", sha256=sha)])
    ev.run_succeeded()
    assert A.verify_outputs(tmp_path, "run_v") == []  # matches at first
    # mutate the bytes under the evidence
    final.write_bytes(b"tampered!")
    mism = A.verify_outputs(tmp_path, "run_v")
    assert len(mism) == 1
    assert mism[0]["path"] == "renders/final/final_v1.mp4"
    assert mism[0]["expected_sha256"] == sha
    assert mism[0]["actual_sha256"] == hash_file(final) != sha
    # a missing file is a mismatch too, reported honestly
    final.unlink()
    assert A.verify_outputs(tmp_path, "run_v")[0]["actual_sha256"] == "<missing>"


def test_semantic_digest_excludes_incidentals(tmp_path):
    base = {"schema": A.SCHEMA, "run_id": "r", "attempt_id": "att_a", "sequence": 1,
            "stage": "generate", "action": "gen", "state": A.SUCCEEDED,
            "outputs": [{"role": "take", "sha256": "sha256:1"}]}
    d1 = A.semantic_digest({**base, "ts": "2026-01-01T00:00:00+00:00",
                            "duration_ms": 10, "redactions": ["x"]})
    d2 = A.semantic_digest({**base, "ts": "2027-09-09T09:09:09+00:00",
                            "duration_ms": 9999, "redactions": ["y", "z"]})
    assert d1 == d2  # ts / duration_ms / redactions never move the digest
    # but a semantic change (an output sha) DOES move it
    d3 = A.semantic_digest({**base, "outputs": [{"role": "take", "sha256": "sha256:2"}]})
    assert d3 != d1
    assert d1 == hash_value(A._semantic_payload({**base, "ts": "z"}))
