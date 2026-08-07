"""AI_IDE_14 — real-provider qualification & low-cost canary.

Audit-first / red-first / derived-only / fake-transport-first. This env has NO
real provider account, so the WHOLE real-canary path is driven against SCRIPTED
transports (the DR06 admission-sandbox pattern) and the matrix honestly tops
real cloud providers out at CONFIG_VALID / DRY_RUN_VALID. PRODUCTION_READY is
reachable only by a real operator running a real canary later — a scripted run
provably reaches at most RECOVERY_PASSED (transport != "real").

Covers the contract §10 items 1-19. Runtime behaviours the P0/FA batches already
proved (fail-closed recovery, disposition, poll-only resume) are REUSED — the
canary drives the SAME admission path; only the qualification BINDINGS get their
own proofs here.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import manju.providers.registry as registry_mod
from manju.core.container import Project
from manju.providers import qualification as Q
from manju.providers.base import ProviderFailure
from manju.providers.generic_cloud import HttpResponse
from manju.providers import submission as S

FIXTURES = Path(__file__).parent / "fixtures" / "canary"


# ------------------------------------------------------------------ scaffolding


@pytest.fixture(autouse=True)
def _pin_fixtures(monkeypatch):
    monkeypatch.setenv("MANJU_CANARY_FIXTURES_DIR", str(FIXTURES))


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    registry_mod._manifest_cache = None
    yield root
    registry_mod._manifest_cache = None


@pytest.fixture
def user(tmp_path):
    return Project.create(tmp_path / "user", git_init=False)


API_KEY_VALUE = "sk-super-secret-key-value-1234"


def write_manifest(providers_dir, monkeypatch, pid="canary_cloud", *, extra_yaml="",
                   caps="image_to_video", key_set=True):
    monkeypatch.setenv(f"{pid.upper()}_KEY", API_KEY_VALUE) if key_set else None
    (providers_dir / pid).mkdir()
    (providers_dir / pid / "provider.yaml").write_text(
        f"""
id: {pid}
type: video
adapter: generic_cloud
capabilities: [{caps}]
auth: {{key_env: {pid.upper()}_KEY, header: 'Authorization: Bearer {{key}}'}}
submit: {{url: https://api.example.com/v/create, body_template: {{prompt: '{{prompt}}', seed: '{{seed}}'}}, job_id_path: $.data.job_id}}
poll: {{url: 'https://api.example.com/v/{{job_id}}', status_path: $.data.status, status_map: {{DONE: succeeded, RUN: running}}, result_url_path: $.data.url, cost_path: $.data.cost}}
cost: {{per_second: 0.1, currency: CNY}}
{extra_yaml}
""",
        encoding="utf-8",
    )
    registry_mod._manifest_cache = None
    return pid


SAMPLE_MP4 = (FIXTURES / "canary.clip.mp4").read_bytes()


class Canned:
    """A scripted transport: submit → job id, poll → DONE (any job id), download
    → the deterministic fixture clip. Knobs script the failure modes the runtime
    already classifies (reused via the canary path)."""

    def __init__(self, *, submit_status=200, poll_running=0, download_status=200,
                 download_ctype="video/mp4", download_body=None, poll_extra=None,
                 submit_body=None):
        self.submit_status = submit_status
        self.poll_running = poll_running          # N running polls before DONE
        self.download_status = download_status
        self.download_ctype = download_ctype
        self.download_body = SAMPLE_MP4 if download_body is None else download_body
        self.poll_extra = poll_extra or {}
        self.submit_body = submit_body
        self.posts = 0
        self.polls = 0
        self.downloads = 0

    def __call__(self, method, url, headers, body):
        if method == "POST":
            self.posts += 1
            if self.submit_status >= 400:
                return HttpResponse(self.submit_status, {}, b'{"error":"no"}')
            payload = self.submit_body or {"data": {"job_id": "job_canary_1"}}
            return HttpResponse(200, {"Content-Type": "application/json"},
                                json.dumps(payload).encode())
        if method == "GET" and "result" in url or (method == "GET" and url.endswith(".mp4")):
            self.downloads += 1
            return HttpResponse(self.download_status,
                                {"Content-Type": self.download_ctype},
                                self.download_body)
        if method == "GET":
            self.polls += 1
            status = "RUN" if self.polls <= self.poll_running else "DONE"
            data = {"data": {"status": status, "url": "https://cdn.example.com/result.mp4",
                             "cost": 0.1, **self.poll_extra}}
            return HttpResponse(200, {"Content-Type": "application/json"},
                                json.dumps(data).encode())
        raise AssertionError(f"unexpected {method} {url}")


def run_canary(user, pid, cap="image_to_video", *, transport=None, recovery=False,
               max_cost=1.0, assume_yes=True, **kw):
    return Q.qualify(user, pid, cap, mode="run", max_cost=max_cost,
                     assume_yes=assume_yes, transport=transport or Canned(),
                     recovery_drill=recovery, **kw)


# =========================================================== pure derivation


def _declared(**over):
    d = {"exists": True, "config_ok": True, "provider_profile_digest": "sha256:p1",
         "adapter_semantic_digest": "sha256:a1", "fixture_version": "sha256:f1"}
    d.update(over)
    return d


def test_untested_and_config_floor_and_blocked():
    assert Q.qualification_state("x", "c", evidence=None,
                                 declared=_declared(config_ok=False))["state"] == Q.UNTESTED
    assert Q.qualification_state("x", "c", evidence=None,
                                 declared=_declared())["state"] == Q.CONFIG_VALID
    blocked = Q.qualification_state("x", "c", evidence=None,
                                    declared={"exists": False})
    assert blocked["state"] == Q.BLOCKED and blocked["blocked_reason"] == "provider_absent"


def test_production_ready_needs_a_real_transport():
    """A scripted run that recorded PRODUCTION_READY is capped at RECOVERY_PASSED
    — production-readiness is real-canary-only (environment honesty).

    14_21 closeout Q05: a canary-level evidence record must carry ALL mandatory
    anchors (request/response digests included) or it reads STALE — so this
    evidence now carries the full set (the pre-closeout dict omitted them)."""
    ev = {"level": Q.PRODUCTION_READY, "transport": "scripted",
          "provider_profile_digest": "sha256:p1", "adapter_semantic_digest": "sha256:a1",
          "fixture_version": "sha256:f1", "request_digest": "sha256:r1",
          "response_schema_digest": "sha256:s1"}
    r = Q.qualification_state("x", "c", evidence=ev, declared=_declared())
    assert r["level"] == Q.RECOVERY_PASSED
    assert "production_ready_requires_real_canary" in r["reasons"]
    ev_real = dict(ev, transport="real")
    assert Q.qualification_state("x", "c", evidence=ev_real,
                                 declared=_declared())["level"] == Q.PRODUCTION_READY


def test_check_date_is_never_in_the_identity():
    """§3: the check DATE is audit metadata only — two evidences differing ONLY
    in checked_at derive an identical state + bindings."""
    base = {"level": Q.CANARY_ARTIFACT_PASSED, "transport": "scripted",
            "provider_profile_digest": "sha256:p1", "adapter_semantic_digest": "sha256:a1",
            "fixture_version": "sha256:f1", "request_digest": "sha256:r1"}
    a = Q.qualification_state("x", "c", evidence=dict(base, checked_at="2026-01-01T00:00:00Z"),
                              declared=_declared())
    b = Q.qualification_state("x", "c", evidence=dict(base, checked_at="2026-07-11T00:00:00Z"),
                              declared=_declared())
    assert a["state"] == b["state"] and a["bindings"] == b["bindings"]
    assert "checked_at" not in a["bindings"]


# =============================================== 1. profile digest change → stale


def test_1_profile_digest_change_makes_qualification_stale(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)
    rep = run_canary(user, pid)
    assert rep["state"] == Q.CANARY_ARTIFACT_PASSED

    # a cost edit moves the DR04 provider_profile_digest → recorded canary stale
    (providers_dir / pid / "provider.yaml").write_text(
        (providers_dir / pid / "provider.yaml").read_text().replace(
            "per_second: 0.1", "per_second: 0.99"), encoding="utf-8")
    registry_mod._manifest_cache = None

    declared = Q.declared_facts(pid, "image_to_video")
    stored = Q.read_report(user, pid, "image_to_video")["evidence"]
    derived = Q.qualification_state(pid, "image_to_video", evidence=stored, declared=declared)
    assert derived["state"] == Q.STALE
    assert any("provider_profile_digest" in r for r in derived["reasons"])


# =============================================== 2. fixture change → stale


def test_2_fixture_change_makes_qualification_stale(providers_dir, user, monkeypatch, tmp_path):
    pid = write_manifest(providers_dir, monkeypatch)
    # a private fixtures copy so we can mutate it without touching the shared set
    fdir = tmp_path / "fx"
    fdir.mkdir()
    for f in FIXTURES.iterdir():
        (fdir / f.name).write_bytes(f.read_bytes())
    rep = run_canary(user, pid, fixtures_dir=fdir)
    assert rep["state"] == Q.CANARY_ARTIFACT_PASSED
    stored = rep["evidence"]

    # edit the fixture contract → new fixture_version
    fx = fdir / "canary.video.i2v.v1.yaml"
    fx.write_text(fx.read_text().replace("one second", "two seconds"), encoding="utf-8")

    declared = Q.declared_facts(pid, "image_to_video", fixtures_dir=fdir)
    derived = Q.qualification_state(pid, "image_to_video", evidence=stored, declared=declared)
    assert derived["state"] == Q.STALE
    assert any("fixture_version" in r for r in derived["reasons"])


# =============================================== 3. dry-run touches no network


def test_3_dry_run_never_touches_the_transport(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)

    def explode(*a, **k):
        raise AssertionError("dry-run must NOT open a transport")

    monkeypatch.setattr("manju.providers.generic_cloud.default_transport", explode)
    rep = Q.qualify(user, pid, "image_to_video", mode="dry_run")
    assert rep["state"] == Q.DRY_RUN_VALID
    assert rep["transport"] == "none"
    assert rep["receipt"] is None if "receipt" in rep else True


# ================================= 4. budget / confirm failure → transport 0


def test_4_run_refusals_never_reach_the_transport(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)

    # (a) --run with no --max-cost
    t = Canned()
    with pytest.raises(Q.CanaryError) as e:
        Q.qualify(user, pid, "image_to_video", mode="run", max_cost=None,
                  assume_yes=True, transport=t)
    assert e.value.code == "max_cost_required" and t.posts == 0

    # (b) estimate over --max-cost
    t = Canned()
    with pytest.raises(Q.CanaryError) as e:
        Q.qualify(user, pid, "image_to_video", mode="run", max_cost=0.0001,
                  assume_yes=True, transport=t)
    assert e.value.code == "estimate_over_max_cost" and t.posts == 0

    # (c) ask_before spend gate not approved (assume_yes=False)
    cfg = user.load_config()
    if "expensive_generation" not in cfg.ask_before:
        cfg.ask_before.append("expensive_generation")
        user.save_config(cfg)
    t = Canned()
    with pytest.raises(Q.CanaryError) as e:
        Q.qualify(user, pid, "image_to_video", mode="run", max_cost=1.0,
                  assume_yes=False, transport=t)
    assert e.value.code == "waiting_user" and t.posts == 0


# ================================= 5. the canary never auto-falls-back


def test_5_canary_is_a_chain_of_one_no_fallback(providers_dir, user, monkeypatch):
    """A canary submit that fails OUTCOME_UNKNOWN propagates from the ONE provider
    — the canary builds GenericCloudProvider directly, never generate_with_fallback,
    so there is structurally no second provider to spend on (contract test 5)."""
    pid = write_manifest(providers_dir, monkeypatch)
    t = Canned(submit_status=503)  # 5xx after send → OUTCOME_UNKNOWN
    with pytest.raises(ProviderFailure) as exc:
        run_canary(user, pid, transport=t)
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
    assert t.posts == 1  # exactly one submission attempt, no fallback


# ================================= 6. explicit rejection vs OUTCOME_UNKNOWN


def test_6_definite_rejection_and_unknown_are_distinct_on_the_canary(
        providers_dir, user, monkeypatch):
    """Canary-scoped proof (reuses generic_cloud/DR06 classification): a DECLARED
    4xx is DEFINITELY_REJECTED; an undeclared 5xx is OUTCOME_UNKNOWN."""
    monkeypatch.setenv("REJ_CLOUD_KEY", API_KEY_VALUE)
    (providers_dir / "rej_cloud").mkdir()
    (providers_dir / "rej_cloud" / "provider.yaml").write_text(
        """
id: rej_cloud
type: video
adapter: generic_cloud
capabilities: [image_to_video]
auth: {key_env: REJ_CLOUD_KEY, header: 'Authorization: Bearer {key}'}
submit:
  url: https://api.example.com/v/create
  body_template: {prompt: '{prompt}', seed: '{seed}'}
  job_id_path: $.data.job_id
  definite_rejection_statuses: [422]
poll: {url: 'https://api.example.com/v/{job_id}', status_path: $.data.status, status_map: {DONE: succeeded}, result_url_path: $.data.url}
cost: {per_second: 0.1, currency: CNY}
""",
        encoding="utf-8")
    registry_mod._manifest_cache = None

    with pytest.raises(ProviderFailure) as rej:
        run_canary(user, "rej_cloud", transport=Canned(submit_status=422))
    assert rej.value.disposition == S.DEFINITELY_REJECTED

    with pytest.raises(ProviderFailure) as unk:
        run_canary(user, "rej_cloud", transport=Canned(submit_status=500))
    assert unk.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION


# ================================= 7. poll retries never resubmit


def test_7_poll_retries_do_not_resubmit(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)
    t = Canned(poll_running=3)  # RUN, RUN, RUN, then DONE
    rep = run_canary(user, pid, transport=t)
    assert rep["state"] == Q.CANARY_ARTIFACT_PASSED
    assert t.posts == 1          # ONE submit despite …
    assert t.polls >= 4          # … several polls


# ================================= 8. artifact hash + ffprobe binding


def test_8_artifact_is_hashed_and_probed(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)
    rep = run_canary(user, pid)
    art = rep["bindings"]["artifact"]
    # deterministic content hash == the committed fixture clip's hash
    from manju.core.hashing import hash_file
    assert art["content_sha256"] == hash_file(FIXTURES / "canary.clip.mp4")
    assert art["byte_size"] == len(SAMPLE_MP4)
    assert art["probe"]["width"] == 64 and art["probe"]["height"] == 64
    checks = {c["check"]: c for c in rep["artifact_checks"]["checks"]}
    assert checks["frame_size"]["ok"] is True
    assert checks["duration_ms"]["ok"] is True
    assert rep["artifact_checks"]["ok"] is True


# ================================= 9. corrupt download fails closed


def test_9_corrupt_download_fails_closed(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)
    # a 200 that is really an HTML error page (expired signed URL / CDN page)
    t = Canned(download_ctype="text/html",
               download_body=b"<html>403 Forbidden</html>")
    with pytest.raises(ProviderFailure):
        run_canary(user, pid, transport=t)
    # no report claims a passing artifact
    rep = Q.read_report(user, pid, "image_to_video")
    assert rep is None or rep.get("level") != Q.CANARY_ARTIFACT_PASSED


# ================================= 10. delete SQLite → recover from evidence


def test_10_recovery_drill_delete_sqlite_resume_poll_only(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)
    rep = run_canary(user, pid, recovery=True)
    rec = rep["recovery"]
    assert rec["passed"] is True
    assert rec["submissions_restored"] >= 1
    assert rec["restored_state"] == S.ADMITTED
    assert rec["resubmit_calls"] == 0          # poll-only resume, no double-charge
    assert rep["state"] == Q.RECOVERY_PASSED


# ================================= 11. malformed evidence blocks / is ignored


def test_11_malformed_evidence_never_yields_a_false_pass(providers_dir, user, monkeypatch):
    """14_21 closeout Q06–Q09 update: the report JSON is a DISPLAY projection
    with zero admission effect. Corrupting it neither yields a false pass NOR
    erases the durable append-only evidence (the pre-closeout pin read the
    report AS the evidence store; the closeout contract forbids that).
    Corrupting the DURABLE stream itself fails closed (BLOCKED, transport 0 —
    pinned in test_closeout_c1 Q09)."""
    pid = write_manifest(providers_dir, monkeypatch)
    run_canary(user, pid)                        # records durable evidence + report
    # corrupt the derived report JSON → read_report returns None (no crash) …
    Q.report_path(user, pid, "image_to_video").write_text("{ not json", encoding="utf-8")
    assert Q.read_report(user, pid, "image_to_video") is None
    # … but the DURABLE evidence is untouched: the matrix keeps the earned
    # rung — and a forged high-level report could never lift it either.
    row = next(r for r in Q.qualification_matrix(user)["rows"]
               if r["provider_id"] == pid and r["capability"] == "image_to_video")
    assert row["has_evidence"] is True
    assert row["state"] == Q.CANARY_ARTIFACT_PASSED
    Q.report_path(user, pid, "image_to_video").write_text(
        json.dumps({"schema": Q.SCHEMA, "state": Q.PRODUCTION_READY,
                    "level": Q.PRODUCTION_READY,
                    "evidence": {"level": Q.PRODUCTION_READY, "transport": "real"}}),
        encoding="utf-8")
    row2 = next(r for r in Q.qualification_matrix(user)["rows"]
                if r["provider_id"] == pid and r["capability"] == "image_to_video")
    assert row2["state"] == Q.CANARY_ARTIFACT_PASSED  # forged report: zero effect


def test_11b_torn_submission_evidence_fails_closed(providers_dir, user, monkeypatch):
    """Reuses the existing F1 guard on the canary project: a torn line in the
    submission evidence stream fail-closes a resume (transport 0)."""
    from manju.build import attempts as A
    from manju.runtime.state import RuntimeState

    pid = write_manifest(providers_dir, monkeypatch)
    t = Canned()
    rep = run_canary(user, pid, transport=t)
    canary = Project(Path(user.root) / rep["canary_project"])
    # seed an ADMITTED chain + a torn raw line into the canary evidence stream
    A.append_submission_event(canary, submission_id="sub_x", request_digest="sha256:d",
                              from_state=None, to_state=S.PREPARED, provider_id=pid,
                              shot="canary")
    (Path(canary.root) / "events.jsonl").open("a", encoding="utf-8").write("{torn\n")
    with RuntimeState(canary.root) as st:
        st.rebuild(canary)
    from manju.providers.generic_cloud import GenericCloudProvider
    prov = GenericCloudProvider(next(iter(_load(pid))), transport=t, sleep_fn=lambda _s: None)
    calls_before = t.posts
    with pytest.raises(ProviderFailure) as exc:
        prov.generate(Q._canary_request(canary, Q.load_fixture("image_to_video")))
    assert exc.value.disposition == S.OUTCOME_UNKNOWN_DISPOSITION
    assert t.posts == calls_before               # no new submit


def _load(pid):
    from manju.providers.manifest import load_manifests
    m, _ = load_manifests()
    return [m[pid]]


# ================================= 12. secret / absolute-path scan


def test_12_report_leaks_no_secret_or_absolute_path(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)
    rep = run_canary(user, pid)
    blob = json.dumps(rep, ensure_ascii=False)
    assert API_KEY_VALUE not in blob                 # the key value never rides
    assert str(user.root) not in blob                # no absolute project path
    # the on-disk report too
    disk = Q.report_path(user, pid, "image_to_video").read_text(encoding="utf-8")
    assert API_KEY_VALUE not in disk and str(user.root) not in disk
    # unit: a planted forbidden key is caught
    leaked = Q._scan_forbidden_keys({"receipt": {"authorization": "x"}},
                                    ["authorization", "token"])
    assert leaked == ["receipt.authorization"]


# ================================= 13. report binds exact profile/adapter/request


def test_13_report_binds_exact_profile_adapter_fixture_request(providers_dir, user, monkeypatch):
    from manju.providers.catalog import descriptor_for_manifest, provider_profile_digest

    pid = write_manifest(providers_dir, monkeypatch)
    rep = run_canary(user, pid)
    b = rep["bindings"]
    manifest = _load(pid)[0]
    desc = descriptor_for_manifest(manifest)
    assert b["provider_profile_digest"] == provider_profile_digest(
        pid, "image_to_video", descriptor=desc)
    assert b["adapter_semantic_digest"] == Q.adapter_semantic_digest(manifest)
    assert b["fixture_version"] == Q.load_fixture("image_to_video")["fixture_version"]
    assert b["request_digest"] and b["request_digest"].startswith("sha256:")
    assert "checked_at" not in b                     # audit date excluded from identity


# ================================= 14. untested boundary not claimed supported


def test_14_untested_boundary_is_not_claimed_supported(providers_dir, user, monkeypatch):
    # declare a 15s max_duration the 1s canary never exercises
    pid = write_manifest(providers_dir, monkeypatch, pid="dur_cloud",
                         extra_yaml="limits: {max_duration_ms: 15000}")
    rep = run_canary(user, "dur_cloud")
    dvo = {d["attribute"]: d for d in rep["declared_vs_observed"]}
    assert "15000" in dvo["duration"]["declared"]
    assert "1000 ms passed" in dvo["duration"]["observed"]
    assert "boundary" in dvo["duration"]["not_tested"]
    # a 1s pass never implies the 15s boundary works
    assert rep["level"] in (Q.CANARY_SUBMIT_PASSED, Q.CANARY_ARTIFACT_PASSED)


# ================================= 15. report is not a build/cache input


def test_15_qualification_report_is_not_a_build_input():
    """No build/plan/cache module reads the qualification report directory — it
    is a deletable evidence projection (addendum ruling 2). Proven by source
    scan: the report path helpers live ONLY in qualification.py; cli.py imports
    the functions, never the path."""
    src = Path(__file__).resolve().parents[1] / "src" / "manju"
    # the build / resume / cache / render surface — NONE of it may read the
    # qualification report (cli.py is the interactive surface, not a build input;
    # qualification.py is the writer/owner).
    build_pkgs = ("build", "runtime", "timeline", "media", "exporters", "qc")
    offenders = []
    for pkg in build_pkgs:
        for py in (src / pkg).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            tree = ast.parse(text)
            imports_provider_qualification = False
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports_provider_qualification = any(
                        alias.name.endswith("providers.qualification")
                        for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    imports_provider_qualification = (
                        module.endswith("providers.qualification")
                        or (module.endswith("providers") and any(
                            alias.name == "qualification" for alias in node.names))
                    )
                if imports_provider_qualification:
                    break
            if ("providers/qualification" in text or "qualification_dir" in text
                    or "reports/providers/qualification" in text
                    or imports_provider_qualification):
                offenders.append(str(py.relative_to(src)))
    assert offenders == [], f"a build/cache path reads the qualification report: {offenders}"


def test_15b_deleting_the_report_changes_no_projection(providers_dir, user, monkeypatch):
    from manju.providers.catalog import project_provider_capabilities

    pid = write_manifest(providers_dir, monkeypatch)
    before = project_provider_capabilities()["projection_digest"]
    run_canary(user, pid)
    Q.report_path(user, pid, "image_to_video").unlink()
    after = project_provider_capabilities()["projection_digest"]
    assert before == after   # the capability projection never depended on it


# ================================= 16. response-schema drift → stale


def test_16_response_schema_drift_makes_qualification_stale(providers_dir, user, monkeypatch):
    # anchor semantics (pure): a moved response_schema_digest is drift
    d = _declared(response_schema_digest="sha256:schemaB")
    ev = {"level": Q.CANARY_ARTIFACT_PASSED, "transport": "scripted",
          "provider_profile_digest": "sha256:p1", "adapter_semantic_digest": "sha256:a1",
          "fixture_version": "sha256:f1", "response_schema_digest": "sha256:schemaA"}
    r = Q.qualification_state("x", "image_to_video", evidence=ev, declared=d)
    assert r["state"] == Q.STALE and any("response_schema_digest" in x for x in r["reasons"])

    # and the anchor is MEANINGFUL: two different response shapes → different digest
    pid = write_manifest(providers_dir, monkeypatch)
    a = run_canary(user, pid, transport=Canned())
    b = run_canary(user, pid, transport=Canned(
        submit_body={"data": {"job_id": "j", "extra_field": 1, "nested": {"k": 2}}}))
    assert a["evidence"]["response_schema_digest"] != b["evidence"]["response_schema_digest"]


# ================================= 17. free health probe never submits


def test_17_health_probe_failure_never_triggers_a_submit(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="ping_cloud",
                         extra_yaml="ping_url: https://api.example.com/health")

    def failing_opener(url):
        raise OSError("unreachable")

    rep = Q.qualify(user, "ping_cloud", "image_to_video", mode="dry_run",
                    health_probe=True, probe_opener=failing_opener)
    assert rep["health_probe"]["status"] == "unreachable"
    assert rep["transport"] == "none"                     # dry-run: no submit at all
    assert rep["state"] == Q.DRY_RUN_VALID                # a probe never lifts the rung

    # no ping_url declared → skipped_with_evidence (no real free endpoint in-repo)
    pid2 = write_manifest(providers_dir, monkeypatch, pid="noping_cloud")
    rep2 = Q.qualify(user, "noping_cloud", "image_to_video", mode="dry_run",
                     health_probe=True)
    assert rep2["health_probe"]["status"] == "skipped_with_evidence"


# ================================= 18. canary input has no private material


def test_18_canary_inputs_are_deterministic_and_non_private():
    import struct
    import zlib

    for f in FIXTURES.glob("*.yaml"):
        import yaml
        data = yaml.safe_load(f.read_text())
        for key in ("input_media", "sample_artifact"):
            if data.get(key):
                media = FIXTURES / data[key]
                assert media.is_file()
                assert media.stat().st_size < 8192, f"{media} too big to be a fixture"
    # the PNG first frame is a SINGLE solid color (no private imagery)
    png = (FIXTURES / "canary.frame.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    idat = b""
    i = 8
    while i < len(png):
        length = struct.unpack(">I", png[i:i + 4])[0]
        tag = png[i + 4:i + 8]
        if tag == b"IDAT":
            idat += png[i + 8:i + 8 + length]
        i += 12 + length
    raw = zlib.decompress(idat)
    # every row starts with filter byte 0 then repeats one RGB triple
    row_len = 1 + 64 * 3
    px = raw[1:4]
    assert all(raw[r * row_len + 1 + c * 3: r * row_len + 1 + c * 3 + 3] == px
               for r in range(64) for c in range(64))


# ================================= 19. data-handling is declared, never observed


def test_19_data_handling_is_reference_only_not_verified(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="dh_cloud", extra_yaml=(
        "data_handling: {region: cn-shanghai, retention: 30d, training_opt_out: true, "
        "deletion_url: 'https://api.example.com/delete', source_ref: 'vendor docs 2026-07'}"))
    rep = run_canary(user, "dh_cloud")
    dh = rep["data_handling"]
    assert dh["status"] == "declared" and dh["verified"] is False
    assert dh["region"] == "cn-shanghai" and dh["retention"] == "30d"
    assert dh["training_opt_out"] is True
    # it is NEVER an artifact/observed check
    assert not any("data_handling" in c["check"] for c in rep["artifact_checks"]["checks"])
    assert "region" not in json.dumps(rep["artifact_checks"])


# ================================= CLI surface (contract §9)


def _cli(user, monkeypatch, args):
    from typer.testing import CliRunner

    import manju.cli as cli
    monkeypatch.setattr(cli, "_RECENTS_TOUCHED", False)
    monkeypatch.chdir(user.root)
    return CliRunner().invoke(cli.app, args)


def test_cli_dry_run_and_matrix_and_run_refusal(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch)

    out = _cli(user, monkeypatch,
               ["providers", "qualify", pid, "--capability", "image_to_video",
                "--dry-run", "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["state"] == Q.DRY_RUN_VALID

    # --run with no --max-cost is refused (spend 0) with a stable code
    out = _cli(user, monkeypatch,
               ["providers", "qualify", pid, "--capability", "image_to_video",
                "--run", "--json"])
    assert out.exit_code == 1
    assert json.loads(out.output)["code"] == "max_cost_required"

    # the matrix view
    out = _cli(user, monkeypatch, ["providers", "qualification", "--json"])
    assert out.exit_code == 0, out.output
    row = next(r for r in json.loads(out.output)["rows"]
               if r["provider_id"] == pid and r["capability"] == "image_to_video")
    assert row["state"] == Q.DRY_RUN_VALID


# ================================= matrix / dry-run honest ceiling


def test_matrix_cloud_ceiling_is_dry_run_without_a_real_account(providers_dir, user, monkeypatch):
    """The honest environment outcome: a cloud provider with a valid config +
    dry-run reaches DRY_RUN_VALID, never a canary rung, until a real account runs
    a real canary. Local built-ins sit at CONFIG_VALID."""
    pid = write_manifest(providers_dir, monkeypatch)
    Q.qualify(user, pid, "image_to_video", mode="dry_run")
    rows = {(r["provider_id"], r["capability"]): r
            for r in Q.qualification_matrix(user)["rows"]}
    assert rows[(pid, "image_to_video")]["state"] == Q.DRY_RUN_VALID
    # built-in local providers exist at CONFIG_VALID (no paid canary needed)
    local = [r for (p, c), r in rows.items() if r["kind"] == "local"]
    assert local and all(r["state"] in (Q.CONFIG_VALID, Q.UNTESTED) for r in local)
