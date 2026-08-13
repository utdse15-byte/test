"""AI_IDE_14_21_CLOSEOUT §2 — C1 qualification authority & network admission.

Red tests Q01–Q14 (contract §2). Written FIRST against the target behaviour:

* the reports/providers/qualification/*.json projection has ZERO admission
  effect (forge / edit / delete — Q06/Q07/Q08);
* admission re-materializes from durable append-only qualification evidence
  (events.jsonl), fail-closed on corruption (Q09);
* enabled==false and undeclared capabilities BLOCK regardless of evidence
  (Q01/Q02);
* a missing mandatory staleness anchor is STALE, never "no drift" (Q03–Q05);
* floors: reviewer/analyzer unattended/paid >= PRODUCTION_READY (interactive
  >= CANARY_ARTIFACT_PASSED); bridge default >= PRODUCTION_READY — so
  DRY_RUN_VALID admits none of them (Q10–Q12);
* admission is capability-exact (Q13/Q14) — synthetic real-transport evidence
  is written by test fixtures (no network anywhere in this file).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import manju.providers.registry as registry_mod
from manju.core.container import Project
from manju.providers import qualification as Q

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


def write_manifest(providers_dir, monkeypatch, pid="c1_cloud", *, extra_yaml="",
                   caps="image_to_video", key_set=True):
    if key_set:
        monkeypatch.setenv(f"{pid.upper()}_KEY", API_KEY_VALUE)
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


def dry_run_evidence_for(pid, capability):
    """A DRY_RUN_VALID evidence dict whose anchors match the CURRENT declared
    facts — the honest ceiling a cloud provider reaches with no real account."""
    declared = Q.declared_facts(pid, capability)
    return {
        "level": Q.DRY_RUN_VALID,
        "transport": "none",
        "provider_profile_digest": declared.get("provider_profile_digest"),
        "adapter_semantic_digest": declared.get("adapter_semantic_digest"),
        "fixture_version": declared.get("fixture_version"),
        "request_digest": None,
        "evidence_refs": [],
        "artifact": None,
        "cost": {"estimated": 0.1, "currency": "CNY", "actual": None},
        "checked_at": "2026-07-12T00:00:00Z",
    }


def real_evidence_for(pid, capability, level=None, **over):
    """Synthetic REAL-transport canary evidence bound to the CURRENT anchors —
    the established test pattern for the operator path (no network)."""
    declared = Q.declared_facts(pid, capability)
    ev = {
        "level": level or Q.PRODUCTION_READY,
        "transport": "real",
        "provider_profile_digest": declared.get("provider_profile_digest"),
        "adapter_semantic_digest": declared.get("adapter_semantic_digest"),
        "fixture_version": declared.get("fixture_version"),
        "request_digest": "sha256:req-synthetic-canary",
        "response_schema_digest": "sha256:resp-synthetic-canary",
        "evidence_refs": [{"submission_id": "sub_synth", "to": "TERMINAL_SUCCESS"}],
        "artifact": {"content_sha256": "sha256:artifact", "byte_size": 10},
        "cost": {"estimated": 0.1, "currency": "CNY", "actual": 0.1},
        "checked_at": "2026-07-12T00:00:00Z",
    }
    ev.update(over)
    return ev


def seed_durable(user, pid, capability, evidence):
    """Record evidence in the durable append-only store (the admission truth)."""
    return Q.record_qualification_evidence(user, pid, capability, evidence)


def _declared(**over):
    d = {"exists": True, "config_ok": True, "provider_profile_digest": "sha256:p1",
         "adapter_semantic_digest": "sha256:a1", "fixture_version": "sha256:f1"}
    d.update(over)
    return d


# ============================== Q01 disabled provider cannot admit


def test_q01_disabled_provider_cannot_admit(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q01_cloud",
                         caps="image_to_video, generative_bridge",
                         extra_yaml="disabled: true")
    # even PRODUCTION_READY real evidence must not admit a disabled provider
    ev = real_evidence_for(pid, "generative_bridge")
    decision = Q.bridge_admission(pid, "generative_bridge", project=user, evidence=ev)
    assert decision["admitted"] is False
    state = Q.qualification_state(
        pid, "generative_bridge", evidence=ev,
        declared=Q.declared_facts(pid, "generative_bridge"))
    assert state["state"] == Q.BLOCKED
    assert state["blocked_reason"] == "provider_disabled"
    # a one-time risk acceptance never overrides an explicit disable either
    acc = Q.record_bridge_risk_acceptance(
        user, pid, "generative_bridge", "sha256:whatever")
    assert acc["acceptance_id"]
    decision2 = Q.bridge_admission(pid, "generative_bridge", project=user,
                                   evidence=ev, request_digest="sha256:whatever")
    assert decision2["admitted"] is False


# ============================== Q02 capability not declared cannot admit


def test_q02_undeclared_capability_cannot_admit(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q02_cloud",
                         caps="image_to_video")
    # strong evidence for a capability the manifest never declared
    ev = real_evidence_for(pid, "generative_bridge")
    decision = Q.bridge_admission(pid, "generative_bridge", project=user, evidence=ev)
    assert decision["admitted"] is False
    state = Q.qualification_state(
        pid, "generative_bridge", evidence=ev,
        declared=Q.declared_facts(pid, "generative_bridge"))
    assert state["state"] == Q.BLOCKED
    assert state["blocked_reason"] == "capability_not_declared"
    # risk acceptance does not cover an undeclared capability on a real manifest
    Q.record_bridge_risk_acceptance(user, pid, "generative_bridge", "sha256:x")
    d2 = Q.bridge_admission(pid, "generative_bridge", project=user,
                            evidence=ev, request_digest="sha256:x")
    assert d2["admitted"] is False


# ============================== Q03/Q04/Q05 missing anchors are STALE, not "no drift"


def test_q03_missing_profile_anchor_is_stale_or_blocked():
    ev = {"level": Q.CANARY_ARTIFACT_PASSED, "transport": "real",
          # provider_profile_digest MISSING from the recorded evidence
          "adapter_semantic_digest": "sha256:a1", "fixture_version": "sha256:f1",
          "request_digest": "sha256:r1", "response_schema_digest": "sha256:s1"}
    r = Q.qualification_state("x", "image_to_video", evidence=ev, declared=_declared())
    assert r["state"] in (Q.STALE, Q.BLOCKED)
    assert r["level"] != Q.CANARY_ARTIFACT_PASSED  # rung fell back — never kept
    assert any("provider_profile_digest" in reason for reason in r["reasons"])


def test_q04_missing_adapter_anchor_is_stale_or_blocked():
    ev = {"level": Q.CANARY_ARTIFACT_PASSED, "transport": "real",
          "provider_profile_digest": "sha256:p1",
          # adapter_semantic_digest MISSING
          "fixture_version": "sha256:f1",
          "request_digest": "sha256:r1", "response_schema_digest": "sha256:s1"}
    r = Q.qualification_state("x", "image_to_video", evidence=ev, declared=_declared())
    assert r["state"] in (Q.STALE, Q.BLOCKED)
    assert r["level"] != Q.CANARY_ARTIFACT_PASSED
    assert any("adapter_semantic_digest" in reason for reason in r["reasons"])


def test_q05_missing_fixture_request_response_anchor_after_canary_is_stale():
    base = {"level": Q.CANARY_ARTIFACT_PASSED, "transport": "real",
            "provider_profile_digest": "sha256:p1",
            "adapter_semantic_digest": "sha256:a1", "fixture_version": "sha256:f1",
            "request_digest": "sha256:r1", "response_schema_digest": "sha256:s1"}
    for anchor in ("fixture_version", "request_digest", "response_schema_digest"):
        ev = {k: v for k, v in base.items() if k != anchor}
        r = Q.qualification_state("x", "image_to_video", evidence=ev,
                                  declared=_declared())
        assert r["state"] in (Q.STALE, Q.BLOCKED), anchor
        assert r["level"] != Q.CANARY_ARTIFACT_PASSED, anchor
        assert any(anchor in reason for reason in r["reasons"]), anchor
    # the complete anchor set (all five present + matching) is NOT stale
    ok = Q.qualification_state("x", "image_to_video", evidence=dict(base),
                               declared=_declared())
    assert ok["stale"] is False and ok["level"] == Q.CANARY_ARTIFACT_PASSED


# ============================== Q06 forged valid JSON report cannot admit


def test_q06_forged_report_cannot_admit(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q06_cloud",
                         caps="vision")
    # forge a perfectly VALID report claiming PRODUCTION_READY on current anchors
    forged = {
        "schema": Q.SCHEMA, "provider_id": pid, "capability": "vision",
        "state": Q.PRODUCTION_READY, "level": Q.PRODUCTION_READY,
        "stale": False, "blocked_reason": None,
        "evidence": real_evidence_for(pid, "vision"),
    }
    path = Q.report_path(user, pid, "vision")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(forged), encoding="utf-8")
    # the projection must have ZERO admission effect — no durable evidence exists
    decision = Q.reviewer_admission(pid, "vision", project=user)
    assert decision["admitted"] is False
    row = next(r for r in Q.qualification_matrix(user)["rows"]
               if r["provider_id"] == pid and r["capability"] == "vision")
    assert row["level"] in (Q.UNTESTED, Q.CONFIG_VALID)
    assert row["has_evidence"] is False


# ============================== Q07 editing report level cannot admit


def test_q07_edited_report_level_cannot_admit(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q07_cloud", caps="vision")
    rep = Q.qualify(user, pid, "vision", mode="dry_run")
    assert rep["state"] == Q.DRY_RUN_VALID
    before = Q.reviewer_admission(pid, "vision", project=user)
    # edit the projection: bump the recorded evidence to a real PRODUCTION_READY
    path = Q.report_path(user, pid, "vision")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["evidence"]["level"] = Q.PRODUCTION_READY
    doc["evidence"]["transport"] = "real"
    doc["evidence"]["request_digest"] = "sha256:forged-req"
    doc["evidence"]["response_schema_digest"] = "sha256:forged-resp"
    doc["state"] = doc["level"] = Q.PRODUCTION_READY
    path.write_text(json.dumps(doc), encoding="utf-8")
    after = Q.reviewer_admission(pid, "vision", project=user)
    assert after["admitted"] is False
    assert after["admitted"] == before["admitted"]
    # the durable truth still reads DRY_RUN_VALID
    row = next(r for r in Q.qualification_matrix(user)["rows"]
               if r["provider_id"] == pid and r["capability"] == "vision")
    assert row["level"] == Q.DRY_RUN_VALID


# ============================== Q08 deleting report does not erase authority


def test_q08_deleting_report_does_not_erase_durable_authority(
        providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q08_cloud", caps="vision")
    rep = Q.qualify(user, pid, "vision", mode="dry_run")
    assert rep["state"] == Q.DRY_RUN_VALID
    Q.report_path(user, pid, "vision").unlink()
    # the durable qualification authority survives the projection's deletion
    row = next(r for r in Q.qualification_matrix(user)["rows"]
               if r["provider_id"] == pid and r["capability"] == "vision")
    assert row["level"] == Q.DRY_RUN_VALID
    assert row["has_evidence"] is True


# ============================== Q09 malformed durable evidence blocks transport


def test_q09_malformed_durable_evidence_fails_closed(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q09_cloud", caps="vision")
    Q.qualify(user, pid, "vision", mode="dry_run")
    # corrupt the DURABLE evidence stream (a torn line in events.jsonl)
    events = Path(user.root) / "events.jsonl"
    with events.open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-07-12T00:00:00Z", "action": "qualification_evi\n')
    # fail closed: a structured refusal (never an exception), admission blocked
    decision = Q.reviewer_admission(pid, "vision", project=user)
    assert decision["admitted"] is False
    assert "corrupt" in json.dumps(decision).lower()
    row = next(r for r in Q.qualification_matrix(user)["rows"]
               if r["provider_id"] == pid and r["capability"] == "vision")
    assert row["state"] == Q.BLOCKED
    assert row["blocked_reason"] == Q.EVIDENCE_CORRUPT


# ============================== Q10/Q11/Q12 DRY_RUN_VALID launches nothing real


def test_q10_dry_run_valid_cannot_launch_real_reviewer(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q10_cloud", caps="vision")
    ev = dry_run_evidence_for(pid, "vision")
    # unattended/paid default floor: PRODUCTION_READY
    assert Q.reviewer_admission(pid, "vision", project=user,
                                evidence=ev)["admitted"] is False
    # even the human-interactive floor needs a passed canary artifact
    assert Q.reviewer_admission(pid, "vision", project=user, evidence=ev,
                                interactive=True)["admitted"] is False


def test_q11_dry_run_valid_cannot_launch_real_analyzer(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q11_cloud",
                         caps="media_analysis")
    ev = dry_run_evidence_for(pid, "media_analysis")
    assert Q.analyzer_admission(pid, "media_analysis", project=user,
                                evidence=ev)["admitted"] is False
    assert Q.analyzer_admission(pid, "media_analysis", project=user, evidence=ev,
                                interactive=True)["admitted"] is False


def test_q12_dry_run_valid_cannot_launch_paid_bridge(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q12_cloud",
                         caps="image_to_video, generative_bridge")
    ev = dry_run_evidence_for(pid, "generative_bridge")
    decision = Q.bridge_admission(pid, "generative_bridge", project=user, evidence=ev)
    assert decision["admitted"] is False
    assert decision["min_required"] == Q.PRODUCTION_READY


# ============================== Q13 real canary admits only the qualified capability


def test_q13_real_canary_with_current_anchors_admits_that_capability(
        providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q13_cloud",
                         caps="image_to_video, generative_bridge")
    seed_durable(user, pid, "generative_bridge",
                 real_evidence_for(pid, "generative_bridge"))
    decision = Q.bridge_admission(pid, "generative_bridge", project=user)
    assert decision["admitted"] is True
    assert decision["refusal"] is None
    # and the admission came from the durable store, not any report file
    assert Q.read_report(user, pid, "generative_bridge") is None


def test_q13b_stale_anchor_revokes_the_real_canary_admission(
        providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q13b_cloud",
                         caps="image_to_video, generative_bridge")
    seed_durable(user, pid, "generative_bridge",
                 real_evidence_for(pid, "generative_bridge"))
    assert Q.bridge_admission(pid, "generative_bridge",
                              project=user)["admitted"] is True
    # move a staleness anchor (a manifest cost edit moves the profile digest)
    yaml_path = providers_dir / pid / "provider.yaml"
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8").replace(
        "per_second: 0.1", "per_second: 0.99"), encoding="utf-8")
    registry_mod._manifest_cache = None
    after = Q.bridge_admission(pid, "generative_bridge", project=user)
    assert after["admitted"] is False


# ============================== Q14 capability A never admits capability B


def test_q14_qualification_for_a_never_admits_b(providers_dir, user, monkeypatch):
    pid = write_manifest(providers_dir, monkeypatch, pid="q14_cloud",
                         caps="image_to_video, generative_bridge, vision")
    seed_durable(user, pid, "generative_bridge",
                 real_evidence_for(pid, "generative_bridge"))
    assert Q.bridge_admission(pid, "generative_bridge",
                              project=user)["admitted"] is True
    # the SAME provider's OTHER capabilities stay unadmitted
    assert Q.bridge_admission(pid, "image_to_video",
                              project=user)["admitted"] is False
    assert Q.reviewer_admission(pid, "vision", project=user)["admitted"] is False
    matrix = {(r["provider_id"], r["capability"]): r
              for r in Q.qualification_matrix(user)["rows"]}
    assert matrix[(pid, "generative_bridge")]["has_evidence"] is True
    assert matrix[(pid, "image_to_video")]["has_evidence"] is False
