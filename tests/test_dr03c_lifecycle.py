"""DR03C — lifecycle wiring (the emission points in graph.py + registry.py).

These pin that the run-evidence stream is produced by REAL flows: the build's
run/cache-hit/render attempts, the provider fallback attempts (with A/B/C
parentage, failed attempts preserved), REJECTED_PRECHECK, the cancel and
spend-gate run terminals, the ledger cross-reference, and the hard invariants —
the evidence context is default-inert (None → byte-identical), the RunManifest
is never read by rebuild-index, and an evidence-append failure after a media
commit warns without deleting media.

Provider-level tests drive ``generate_with_fallback`` directly with fake
in-process providers (no ffmpeg); full-build tests are ffmpeg-gated.
"""

from __future__ import annotations

import json
import shutil
import threading

import pytest

from manju.build import attempts as A
from manju.build.graph import run_build
from manju.core.hashing import hash_file
from manju.providers.base import (
    FailureKind,
    GenerationRequest,
    Provider,
    ProviderFailure,
)
from manju.providers.registry import generate_with_fallback
from manju.runtime.state import RuntimeState

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required")


# ---------------------------------------------------------------- fake providers


@pytest.fixture
def registry_sandbox():
    """Register fake providers for one test, restore the global registry after."""
    import manju.providers.registry as reg

    reg._ensure_builtins()
    saved = dict(reg._REGISTRY)
    yield reg
    reg._REGISTRY.clear()
    reg._REGISTRY.update(saved)


class _FailProvider(Provider):
    kind = "local"

    def __init__(self, pid: str, fail_kind: FailureKind):
        self.id = pid
        self._fail_kind = fail_kind

    def generate(self, req):
        raise ProviderFailure(self._fail_kind, f"{self.id} refused", detail={})


class _OkProvider(Provider):
    kind = "local"
    id = "ok_c"

    def generate(self, req):
        media = req.project.root / f"_fake_{req.shot.id}.mp4"
        media.write_bytes(b"okvideo-" + req.shot.id.encode())
        return [self._register(req, media, params={"seed": req.params.get("seed")})]


def _req(project, shot, evidence, *, params=None):
    return GenerationRequest(
        project=project, shot=shot, bible=project.load_bible(),
        spec_hash="sha256:test", duration_ms=3000, candidates=1,
        params=params or {"seed": 1}, estimated_cost=0.0, evidence=evidence)


# ------------------------------------------------ (7/20) parentage + preserved


def test_fallback_chain_records_abc_parentage_and_preserves_failures(
    tmp_project, add_shot, registry_sandbox
):
    """5.3 A/B/C: fallback_root = first try's id, parent = previous try's id,
    fallback_index = position. A fallback SUCCESS never overwrites the earlier
    FAILED/REJECTED attempts — all three attempts are on the stream, in order."""
    registry_sandbox.register_provider(_FailProvider("fail_a", FailureKind.invalid))
    registry_sandbox.register_provider(_FailProvider("fail_b", FailureKind.provider_error))
    registry_sandbox.register_provider(_OkProvider())
    shot = add_shot(tmp_project, "S001")
    ev = A.RunEvidence(tmp_project, "run_abc")

    takes = generate_with_fallback(_req(tmp_project, shot, ev),
                                   ["fail_a", "fail_b", "ok_c"])
    assert takes and takes[0].sidecar.provider == "ok_c"

    recs, _ = A.read_attempts(tmp_project, "run_abc")
    gen = [r for r in recs if r["stage"] == "generate" and r["action"] == "generate"]
    assert len(gen) == 3  # every try preserved — success never overwrote failures
    a, b, c = gen
    # states: invalid -> REJECTED_PRECHECK, provider_error -> FAILED, then SUCCEEDED
    assert (a["state"], b["state"], c["state"]) == (
        A.REJECTED_PRECHECK, A.FAILED, A.SUCCEEDED)
    assert [r["fallback_index"] for r in gen] == [0, 1, 2]
    root = a["attempt_id"]
    assert a.get("parent_attempt_id") is None
    assert a["fallback_root_attempt_id"] == root
    assert b["parent_attempt_id"] == root and b["fallback_root_attempt_id"] == root
    assert c["parent_attempt_id"] == b["attempt_id"] and c["fallback_root_attempt_id"] == root
    # the FAILED one is retryable-classified and carries executor identity
    assert b["failure"]["category"] == "provider_error"
    assert c["executor"]["provider_id"] == "ok_c"
    assert c["outputs"][0]["sha256"].startswith("sha256:")


# --------------------------------------------------------- (5) REJECTED_PRECHECK


def test_invalid_before_submit_is_rejected_precheck(tmp_project, add_shot, registry_sandbox):
    """kenburns without a reference image raises ProviderFailure(invalid) — a
    pre-submit refusal → REJECTED_PRECHECK (never FAILED)."""
    registry_sandbox.register_provider(_OkProvider())
    shot = add_shot(tmp_project, "S001")  # no ref image anywhere
    ev = A.RunEvidence(tmp_project, "run_rej")
    generate_with_fallback(_req(tmp_project, shot, ev), ["ffmpeg_kenburns", "ok_c"])
    recs, _ = A.read_attempts(tmp_project, "run_rej")
    kb = next(r for r in recs if r.get("executor", {}).get("provider_id") == "ffmpeg_kenburns")
    assert kb["state"] == A.REJECTED_PRECHECK
    assert kb["failure"]["category"] == "invalid"


# ---------------------------------------------------- evidence=None is inert


def test_evidence_none_emits_nothing(tmp_project, add_shot, registry_sandbox):
    """A direct provider call / redo outside a run (evidence=None) emits ZERO
    stage_attempt events — byte-identical to before DR03C."""
    registry_sandbox.register_provider(_OkProvider())
    shot = add_shot(tmp_project, "S001")
    req = GenerationRequest(project=tmp_project, shot=shot, bible=tmp_project.load_bible(),
                            spec_hash="sha256:test", duration_ms=3000, candidates=1,
                            params={"seed": 1})  # evidence defaults to None
    takes = generate_with_fallback(req, ["ok_c"])
    assert takes  # generation still works
    recs, _ = A.read_attempts(tmp_project)
    assert recs == []  # nothing on the attempt stream


# ------------------------------------------------------- (3) success ordering


def test_succeeded_record_lands_only_after_media_is_committed_and_hashed(
    tmp_project, add_shot, registry_sandbox, monkeypatch
):
    """The SUCCEEDED record hits events.jsonl only AFTER the take is atomically
    on disk and its recorded sha matches the bytes — proven by intercepting the
    append and re-hashing at that instant."""
    registry_sandbox.register_provider(_OkProvider())
    shot = add_shot(tmp_project, "S001")
    ev = A.RunEvidence(tmp_project, "run_ord")

    seen = {"checked": False}
    real_append = A.append_attempt

    def spy(project, payload, *, actor="engine"):
        if payload.get("state") == A.SUCCEEDED and payload.get("stage") == "generate":
            for out in payload.get("outputs", []):
                p = tmp_project.root / out["path"]
                assert p.exists(), "SUCCEEDED emitted before media was committed"
                assert hash_file(p) == out["sha256"], "recorded sha does not match on-disk bytes"
                seen["checked"] = True
        return real_append(project, payload, actor=actor)

    monkeypatch.setattr(A, "append_attempt", spy)
    generate_with_fallback(_req(tmp_project, shot, ev), ["ok_c"])
    assert seen["checked"], "no SUCCEEDED generate attempt was observed"


# ---------------------------------------------------- (18) append-fail warns


def test_evidence_append_failure_after_commit_warns_never_deletes_media(
    tmp_project, add_shot, registry_sandbox, monkeypatch
):
    """If the evidence append fails AFTER register_take committed the media, the
    take stays on disk (never deleted) and the run collects a warning."""
    registry_sandbox.register_provider(_OkProvider())
    shot = add_shot(tmp_project, "S001")
    ev = A.RunEvidence(tmp_project, "run_warn")

    monkeypatch.setattr(A, "append_attempt", lambda *a, **k: {})  # every append "fails"
    takes = generate_with_fallback(_req(tmp_project, shot, ev), ["ok_c"])

    assert takes and takes[0].media_path.exists()  # media committed + NOT deleted
    assert ev.warnings  # the run collected an honest warning to surface
    assert any("evidence append failed" in w for w in ev.warnings)


# ------------------------------------------------------- (6) cancel run terminal


def test_cancel_emits_canceled_run_attempt_and_manifest(tmp_project, add_shot):
    """A should_cancel() already tripped at the first checkpoint: the run-level
    CANCELED attempt lands on the stream, the manifest is materialized with
    terminal_status CANCELED, and the result carries the run_id."""
    add_shot(tmp_project, "S001")
    tripped = threading.Event()
    tripped.set()
    result = run_build(tmp_project, target="qc", gen="missing", actor="ai",
                       should_cancel=tripped.is_set)
    assert result.ok is False and result.canceled is True
    assert result.run_id, "canceled result must carry its run_id"

    recs, _ = A.read_attempts(tmp_project, result.run_id)
    run_attempts = [r for r in recs if r["stage"] == "build" and r["action"] == "run"]
    assert len(run_attempts) == 1 and run_attempts[0]["state"] == A.CANCELED

    m = json.loads(A.run_manifest_path(tmp_project, result.run_id).read_text())
    assert m["terminal_status"] == A.MANIFEST_CANCELED


# ------------------------------------------------- waiting_user (spend gate)


def test_spend_gate_emits_waiting_user_with_no_provider_submission(
    tmp_project, add_shot, monkeypatch
):
    """The ask_before gate fires BEFORE any provider submission: the run-level
    WAITING_USER attempt is on the stream and there is NOT a single generate
    attempt (no money was ever committed to a provider)."""
    import manju.build.graph as graph

    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (5.0, "CNY"))
    add_shot(tmp_project, "S001")
    result = run_build(tmp_project, target="qc", actor="ai")  # no assume_yes
    assert result.waiting_user is True and result.run_id

    recs, _ = A.read_attempts(tmp_project, result.run_id)
    run_attempts = [r for r in recs if r["stage"] == "build" and r["action"] == "run"]
    assert len(run_attempts) == 1 and run_attempts[0]["state"] == A.WAITING_USER
    assert not [r for r in recs if r["action"] == "generate"]  # no submission happened


def test_dry_run_emits_no_attempts(tmp_project, add_shot):
    """Dry-run is a PLAN — it emits no attempts and materializes no manifest."""
    add_shot(tmp_project, "S001")
    result = run_build(tmp_project, target="final", dry_run=True)
    assert result.ok and result.run_id is None
    recs, _ = A.read_attempts(tmp_project)
    assert recs == []
    assert not (tmp_project.root / "reports" / "runs").exists()


# =================================================== full build (ffmpeg-gated)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One tiny real project built to final (module-scoped, reused read-only)."""
    from manju.core.container import Project
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path_factory.mktemp("dr03c") / "样片", shots=2,
                               clip_seconds=1.0)
    project = Project(root)
    result = run_build(project, target="final", assume_yes=True)
    assert result.ok, result.errors
    return project, result


@needs_ffmpeg
def test_build_run_id_on_result_and_to_dict(built):
    project, result = built
    assert result.run_id
    assert result.to_dict()["run_id"] == result.run_id  # build --json / MCP free


@needs_ffmpeg
def test_build_emits_cache_hit_render_and_run_attempts(built):
    project, result = built
    recs, malformed = A.read_attempts(project, result.run_id)
    assert malformed == 0
    by_stage = {}
    for r in recs:
        by_stage.setdefault((r["stage"], r["action"]), []).append(r)
    # every shot skipped as a cache hit (the sample's takes are manual imports)
    cache = by_stage.get(("generate", "cache_hit"), [])
    assert len(cache) == 2 and all(c["state"] == A.SKIPPED_CACHE_HIT for c in cache)
    # NO media re-hash on a cache hit: the take output binds name+spec_hash only
    assert all("sha256" not in (c["outputs"][0]) for c in cache)
    assert all(c["outputs"][0]["spec_hash"] for c in cache)
    # exactly one render SUCCEEDED and one run SUCCEEDED
    render = by_stage[("render", "render")]
    assert len(render) == 1 and render[0]["state"] == A.SUCCEEDED
    assert render[0]["outputs"][0]["path"].startswith("renders/final/")
    run = by_stage[("build", "run")]
    assert len(run) == 1 and run[0]["state"] == A.SUCCEEDED


@needs_ffmpeg
def test_render_output_sha_reuses_the_dr01_sidecar(built):
    """The render attempt's output sha256 == the final's .key.json output_sha256
    (read, not re-hashed) == hash_file of the mp4 on disk."""
    project, result = built
    recs, _ = A.read_attempts(project, result.run_id)
    render = next(r for r in recs if r["stage"] == "render")
    out = render["outputs"][0]
    final = project.final_dir / out["path"].rsplit("/", 1)[-1]
    sidecar = json.loads(final.with_suffix(".key.json").read_text())
    assert out["sha256"] == sidecar["output_sha256"] == hash_file(final)
    assert out["content_key"] == sidecar["final_key"]


@needs_ffmpeg
def test_second_build_render_is_skipped_cache_hit(built):
    """(4) A content-key-match rebuild reuses the final → render attempt is
    SKIPPED_CACHE_HIT, still binding the reused final's key + existing sha."""
    project, _first = built
    result2 = run_build(project, target="final", assume_yes=True)
    recs, _ = A.read_attempts(project, result2.run_id)
    render = next(r for r in recs if r["stage"] == "render")
    assert render["state"] == A.SKIPPED_CACHE_HIT
    assert render["outputs"][0]["content_key"]


@needs_ffmpeg
def test_manifest_terminal_status_and_verify_outputs(built):
    project, result = built
    m = json.loads(A.run_manifest_path(project, result.run_id).read_text())
    assert m["terminal_status"] == A.COMPLETED
    assert m["qc_report_refs"]  # the build ran QC → refs attached
    assert A.verify_outputs(project, result.run_id) == []  # bytes match the record


# ------------------------------------------------ (19) ledger attempt_id x-ref


@needs_ffmpeg
def test_tasks_attempt_id_equals_events_attempt_id(tmp_project, add_shot):
    """A generated take's ledger row carries the SAME attempt_id the events.jsonl
    SUCCEEDED record carries (thread through record_run)."""
    # preferred provider missing -> degrades to caption_card (a LOCAL provider,
    # so _record_local_runs writes the ledger row with the attempt_id).
    add_shot(tmp_project, "S001", generation={"provider": "nope", "candidates": 1})
    result = run_build(tmp_project, target="qc", assume_yes=True)
    assert result.ok and result.generated

    recs, _ = A.read_attempts(tmp_project, result.run_id)
    succeeded = next(r for r in recs if r["stage"] == "generate"
                     and r["state"] == A.SUCCEEDED)
    with RuntimeState(tmp_project.root) as state:
        rows = [r for r in state.run_log(10) if r["status"] == "succeeded"]
    assert rows and rows[0]["attempt_id"] == succeeded["attempt_id"]


# --------------------------------- (17) rebuild-index never reads manifests


@needs_ffmpeg
def test_rebuild_index_works_from_sidecars_after_manju_and_manifests_deleted(
    tmp_project, add_shot
):
    """Delete .manju AND all run manifests: rebuild-index re-derives the ledger
    from take sidecars alone (never reads a manifest), and the append-only
    attempt HISTORY is still fully readable from events.jsonl."""
    add_shot(tmp_project, "S001", generation={"provider": "nope", "candidates": 1})
    result = run_build(tmp_project, target="qc", assume_yes=True)
    assert result.ok and result.run_id

    # nuke disposable state + the derived manifests
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)
    shutil.rmtree(tmp_project.root / "reports" / "runs", ignore_errors=True)
    assert not A.run_manifest_path(tmp_project, result.run_id).exists()

    with RuntimeState(tmp_project.root) as state:
        stats = state.rebuild(tmp_project)  # sidecar-derived, no manifest read
    assert stats["runs"] >= 1

    # attempt history survived in events.jsonl — the single source of truth
    recs, _ = A.read_attempts(tmp_project, result.run_id)
    assert any(r["state"] == A.SUCCEEDED and r["stage"] == "generate" for r in recs)
    # and the manifest can be re-derived on demand from that stream
    m = json.loads(A.materialize_run_manifest(tmp_project, result.run_id).read_text())
    assert m["run_id"] == result.run_id


@needs_ffmpeg
def test_build_still_succeeds_after_its_manifest_is_deleted(tmp_project, add_shot):
    """(15) The manifest is derived + deletable and NEVER read by a build: a
    second build after deleting the first's manifest works unchanged."""
    add_shot(tmp_project, "S001", generation={"provider": "nope", "candidates": 1})
    r1 = run_build(tmp_project, target="qc", assume_yes=True)
    A.run_manifest_path(tmp_project, r1.run_id).unlink()
    r2 = run_build(tmp_project, target="qc", assume_yes=True)
    assert r2.ok  # build never depended on the manifest


# ----------------------------------------------------------- CLI: tasks manifest


@needs_ffmpeg
def test_cli_tasks_manifest_rematerializes(tmp_project, add_shot, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    add_shot(tmp_project, "S001", generation={"provider": "nope", "candidates": 1})
    result = run_build(tmp_project, target="qc", assume_yes=True)
    A.run_manifest_path(tmp_project, result.run_id).unlink()  # user deleted it

    monkeypatch.chdir(tmp_project.root)
    out = CliRunner().invoke(app, ["tasks", "manifest", result.run_id, "--json"])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert data["run_id"] == result.run_id
    assert data["manifest"]["schema"] == A.MANIFEST_SCHEMA
    assert A.run_manifest_path(tmp_project, result.run_id).exists()  # re-materialized


class _SelfRecordingCloud(Provider):
    """Minimal stand-in for the cloud path's defining trait: it records its OWN
    runs-ledger row INSIDE generate() (like CloudProvider._on_success/_on_failure
    at base.py:594/625), BEFORE the registry's terminal stage_attempt lands —
    the exact ordering that makes attempt_id threading via
    ``req.evidence_attempt_id`` necessary."""
    kind = "cloud"
    id = "cloudy"

    def __init__(self, *, fail: bool = False):
        self._fail = fail

    def generate(self, req):
        state = RuntimeState(req.project.root)
        if self._fail:
            state.record_run(shot=req.shot.id, provider=self.id, status="failed",
                             params={}, error="boom",
                             attempt_id=getattr(req, "evidence_attempt_id", None))
            raise ProviderFailure(FailureKind.provider_error, "boom", detail={})
        media = req.project.root / f"_cloud_{req.shot.id}.mp4"
        media.write_bytes(b"cloudvideo-" + req.shot.id.encode())
        take = self._register(req, media, params={})
        state.record_run(shot=req.shot.id, provider=self.id, status="succeeded",
                         params={}, cost=0.1, currency="USD", take=take.name,
                         attempt_id=getattr(req, "evidence_attempt_id", None))
        return [take]


def test_cloud_self_recorded_ledger_rows_share_the_attempt_id(
        tmp_project, add_shot, registry_sandbox):
    """tasks --json parity on the CLOUD path (the expensive one): the ledger row
    a provider self-records inside generate() must carry the SAME attempt_id as
    the registry's stage_attempt event — for failures AND successes."""
    add_shot(tmp_project, "S001")
    shot = tmp_project.load_shot("S001")
    ev = A.RunEvidence(tmp_project, "run_cloudparity")

    registry_sandbox.register_provider(_SelfRecordingCloud(fail=True))
    with pytest.raises(ProviderFailure):
        generate_with_fallback(_req(tmp_project, shot, ev), ["cloudy"])
    registry_sandbox.register_provider(_SelfRecordingCloud(fail=False))
    takes = generate_with_fallback(_req(tmp_project, shot, ev), ["cloudy"])
    assert takes

    records, _ = A.read_attempts(tmp_project, "run_cloudparity")
    by_status = {r["state"]: r["attempt_id"] for r in records
                 if r.get("stage") == "generate"}
    rows = RuntimeState(tmp_project.root).run_log(10)
    row_by_status = {r["status"]: r.get("attempt_id") for r in rows
                     if r.get("provider") == "cloudy"}
    assert row_by_status["failed"] == by_status[A.FAILED]
    assert row_by_status["succeeded"] == by_status[A.SUCCEEDED]
    assert row_by_status["succeeded"] and row_by_status["failed"]


@needs_ffmpeg
def test_manifest_names_the_invocation_command_target_mode(built):
    """A run answers 'which command/target/mode produced me' (§6.1): the
    run-level terminal carries run_context and the manifest projects it
    top-level. Legacy/foreign streams without it stay null — never invented."""
    project, result = built
    mf = json.loads(
        (project.reports_dir / "runs" / result.run_id / "run.json")
        .read_text(encoding="utf-8"))
    assert mf["command"] == "build"
    assert mf["target"] == "final"
    assert mf["mode"] in ("balanced", "quality", "speed")

    recs, _ = A.read_attempts(project, result.run_id)
    run_doc = next(r for r in recs if r["stage"] == "build" and r["action"] == "run")
    assert run_doc["run_context"]["target"] == "final"
