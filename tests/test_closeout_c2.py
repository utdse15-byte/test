"""AI_IDE_14_21_CLOSEOUT §3 — C2 generative bridge closed loop.

Red tests B01–B12 (contract §3). Target behaviour:

* the bridge plan/request carries REAL endpoint frame file refs + content
  hashes, duration, direction, description, params — and its request digest
  covers every body-affecting field (B01/B02);
* the provider receives the actual endpoint media through the standard
  GenerationRequest refs surface (B03); hash-only-no-media is invalid;
* endpoint bytes are re-hashed at execute time — mismatch => transport 0 (B04);
* qualification admission is enforced in the ONE provider-layer dispatch seam
  execute_bridge funnels through — direct Python, CLI and MCP cannot bypass it
  (B05/B06); low-rung manual experiments ride a single-use operator risk
  acceptance bound to the exact (provider, capability, request_digest);
* spec_hash derives from the current Shot + bridge plan; the literal "bridge"
  is refused (B07);
* adoption consumes only current-bound accepted Assurance evidence — arbitrary
  dicts and stale packets/verdicts are rejected (B08/B09); a current accepted
  Assurance + exact bytes yields a zero-write adoption Proposal (B10);
* an unadopted bridge is caught in a final by ACTUAL source path/content hash,
  not just its take name (B11);
* a restart resumes the SAME submission poll-only and never resubmits (B12).

No network: scripted transports + synthetic durable evidence only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import manju.providers.registry as registry_mod
from manju.build import bridge
from manju.core.container import Project
from manju.core.hashing import hash_file
from manju.providers import qualification as Q
from manju.providers.base import GenerationRequest, ProviderFailure

FIXTURES = Path(__file__).parent / "fixtures" / "canary"
SAMPLE_MP4 = (FIXTURES / "canary.clip.mp4").read_bytes()
API_KEY_VALUE = "sk-super-secret-key-value-1234"


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


def write_manifest(providers_dir, monkeypatch, pid, *, caps, extra_yaml=""):
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


class Canned:
    """A scripted transport (same shape as the c14 canary transport)."""

    def __init__(self, *, poll_running=0):
        from manju.providers.generic_cloud import HttpResponse

        self._resp = HttpResponse
        self.poll_running = poll_running
        self.posts = 0
        self.polls = 0

    def __call__(self, method, url, headers, body):
        if method == "POST":
            self.posts += 1
            return self._resp(200, {"Content-Type": "application/json"},
                              json.dumps({"data": {"job_id": "job_bridge_1"}}).encode())
        if method == "GET" and url.endswith(".mp4"):
            return self._resp(200, {"Content-Type": "video/mp4"}, SAMPLE_MP4)
        if method == "GET":
            self.polls += 1
            status = "RUN" if self.polls <= self.poll_running else "DONE"
            return self._resp(200, {"Content-Type": "application/json"},
                              json.dumps({"data": {"status": status,
                                                   "url": "https://cdn.example.com/r.mp4",
                                                   "cost": 0.1}}).encode())
        raise AssertionError(f"unexpected {method} {url}")


class FakeBridgeProvider:
    """Scripted stand-in for a real video provider — registers a take through
    the standard path and records the request it received."""

    id = "fake_bridge"
    kind = "cloud"

    def __init__(self):
        self.requests = []

    def generate(self, req: GenerationRequest):
        self.requests.append(req)
        from manju.core.models import TakeSidecar
        media = req.project.gen_dir / "_bridge_src.mp4"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"bridge-media-" + req.spec_hash.encode())
        take = req.project.register_take(
            req.shot.id, media,
            TakeSidecar(provider=self.id, spec_hash=req.spec_hash), move=False)
        return [take]


def frames(project, a=b"PREV-FRAME-BYTES-1", b=b"NEXT-FRAME-BYTES-2"):
    d = Path(project.root) / "bridge_frames"
    d.mkdir(exist_ok=True)
    prev, nxt = d / "prev.png", d / "next.png"
    prev.write_bytes(a)
    nxt.write_bytes(b)
    return prev, nxt


def make_plan(project, prev, nxt, **kw):
    kw.setdefault("duration_ms", 600)
    kw.setdefault("direction", "left_to_right")
    kw.setdefault("description", "dolly through the door")
    return bridge.plan_bridge(prev_end_frame=prev, next_start_frame=nxt,
                              project_root=project.root, **kw)


def accept_and_execute(project, shot, plan, provider, **kw):
    """The operator flow for a low-rung manual experiment: a ONE-TIME risk
    acceptance bound to the exact provider/capability/request digest."""
    Q.record_bridge_risk_acceptance(project, provider.id, bridge.BRIDGE_CAPABILITY,
                                    plan["request_digest"])
    return bridge.execute_bridge(project, shot, plan, provider=provider, **kw)


# ============================== B01 description change changes request digest


def test_b01_description_change_changes_request_digest(tmp_project):
    prev, nxt = frames(tmp_project)
    a = make_plan(tmp_project, prev, nxt, description="dolly through the door")
    b = make_plan(tmp_project, prev, nxt, description="whip pan to the window")
    assert a["request_digest"] != b["request_digest"]
    assert a["description"] == "dolly through the door"


# ============================== B02 direction/params/endpoint bytes change digest


def test_b02_direction_params_and_endpoint_bytes_change_digest(tmp_project):
    prev, nxt = frames(tmp_project)
    base = make_plan(tmp_project, prev, nxt)
    # direction
    d = make_plan(tmp_project, prev, nxt, direction="right_to_left")
    assert d["request_digest"] != base["request_digest"]
    # params
    p = make_plan(tmp_project, prev, nxt, params={"motion_strength": 0.9})
    assert p["request_digest"] != base["request_digest"]
    # endpoint BYTES: same file paths, different content
    prev.write_bytes(b"PREV-FRAME-BYTES-CHANGED")
    e = make_plan(tmp_project, prev, nxt)
    assert e["request_digest"] != base["request_digest"]
    assert e["prev_end_frame_hash"] != base["prev_end_frame_hash"]
    # and identical inputs are deterministic
    prev.write_bytes(b"PREV-FRAME-BYTES-1")
    again = make_plan(tmp_project, prev, nxt)
    assert again["request_digest"] == base["request_digest"]


# ============================== B03 provider receives actual endpoint refs


def test_b03_provider_receives_actual_endpoint_media(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    prev, nxt = frames(tmp_project)
    plan = make_plan(tmp_project, prev, nxt)
    provider = FakeBridgeProvider()
    take = accept_and_execute(tmp_project, "S001", plan, provider)
    assert take is not None and len(provider.requests) == 1
    req = provider.requests[0]
    # the ACTUAL endpoint frame files ride the standard request refs surface
    delivered = {hash_file(p) for p in req.refset().images}
    assert plan["prev_end_frame_hash"] in delivered
    assert plan["next_start_frame_hash"] in delivered
    # the effective request itself names the refs (unified effective request)
    assert plan["prev_end_frame"] in json.dumps(req.params)
    # hash-only with NO deliverable media is refused before any dispatch
    hash_only = bridge.plan_bridge(
        prev_end_frame_hash="sha256:p", next_start_frame_hash="sha256:n",
        duration_ms=500)
    p2 = FakeBridgeProvider()
    Q.record_bridge_risk_acceptance(tmp_project, p2.id, bridge.BRIDGE_CAPABILITY,
                                    hash_only["request_digest"])
    with pytest.raises(bridge.BridgeError):
        bridge.execute_bridge(tmp_project, "S001", hash_only, provider=p2)
    assert p2.requests == []


# ============================== B04 endpoint hash mismatch => transport 0


def test_b04_endpoint_hash_mismatch_is_transport_zero(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    prev, nxt = frames(tmp_project)
    plan = make_plan(tmp_project, prev, nxt)
    # the frame file changes AFTER planning — the plan's hash no longer matches
    prev.write_bytes(b"TAMPERED-AFTER-PLAN")
    provider = FakeBridgeProvider()
    Q.record_bridge_risk_acceptance(tmp_project, provider.id,
                                    bridge.BRIDGE_CAPABILITY,
                                    plan["request_digest"])
    with pytest.raises(bridge.BridgeError):
        bridge.execute_bridge(tmp_project, "S001", plan, provider=provider)
    assert provider.requests == []          # transport 0 — dispatch never reached


# ============================== B05 direct execute on unqualified provider => 0


def test_b05_direct_execute_unqualified_is_transport_zero(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    prev, nxt = frames(tmp_project)
    plan = make_plan(tmp_project, prev, nxt)
    provider = FakeBridgeProvider()
    # no qualification, no risk acceptance — a DIRECT python call is refused
    with pytest.raises(ProviderFailure) as exc:
        bridge.execute_bridge(tmp_project, "S001", plan, provider=provider)
    assert provider.requests == []          # transport 0
    assert "BRIDGE" in str(exc.value.detail.get("code", "")).upper()
    # durable refusal evidence landed (reports/failures.jsonl)
    failures = (Path(tmp_project.root) / "reports" / "failures.jsonl")
    assert failures.is_file()
    assert "fake_bridge" in failures.read_text(encoding="utf-8")

    # a risk acceptance is SINGLE-USE and digest-exact:
    other = make_plan(tmp_project, prev, nxt, description="a different bridge")
    Q.record_bridge_risk_acceptance(tmp_project, provider.id,
                                    bridge.BRIDGE_CAPABILITY,
                                    plan["request_digest"])
    # bound to plan's digest — it never covers a DIFFERENT digest
    with pytest.raises(ProviderFailure):
        bridge.execute_bridge(tmp_project, "S001", other, provider=provider)
    assert provider.requests == []
    # first use consumes it …
    bridge.execute_bridge(tmp_project, "S001", plan, provider=provider)
    assert len(provider.requests) == 1
    # … and a second use of the SAME acceptance is refused
    with pytest.raises(ProviderFailure):
        bridge.execute_bridge(tmp_project, "S001", plan, provider=provider)
    assert len(provider.requests) == 1


# ============================== B06 CLI / MCP / direct share the same gate


def test_b06_cli_mcp_direct_share_the_same_gate(tmp_project, add_shot,
                                                providers_dir, monkeypatch,
                                                tmp_path):
    import manju.providers.base as PB

    add_shot(tmp_project, "S001")
    prev, nxt = frames(tmp_project)
    plan = make_plan(tmp_project, prev, nxt)
    pid = write_manifest(providers_dir, monkeypatch, "b06_cloud",
                         caps="image_to_video, generative_bridge")

    seen = []
    real_dispatch = PB.dispatch_bridge

    class _Take:
        name = "take_stub"
        media_path = None
        sidecar = None

    def spy_dispatch(project, provider, req, **kw):
        seen.append((getattr(provider, "id", None), req.params["bridge"]["request_digest"]))
        return [_Take()]

    monkeypatch.setattr(PB, "dispatch_bridge", spy_dispatch)

    # (1) DIRECT python execute_bridge funnels through the ONE provider-layer seam
    bridge.execute_bridge(tmp_project, "S001", plan, provider=FakeBridgeProvider())
    assert len(seen) == 1 and seen[0][1] == plan["request_digest"]

    # (2) the CLI bridge command is a thin wrapper over the SAME code object
    from typer.testing import CliRunner

    import manju.cli as cli
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(cli, "_RECENTS_TOUCHED", False)
    monkeypatch.chdir(tmp_project.root)
    out = CliRunner().invoke(cli.app, ["bridge", "run", "S001",
                                       "--plan", str(plan_file),
                                       "--provider", pid, "--json"])
    assert out.exit_code == 0, out.output
    assert len(seen) == 2 and seen[1][0] == pid

    # (3) MCP exposes NO alternate ungated bridge path at all
    import manju.mcp as mcp_pkg
    import manju.mcp.tools as MT
    mcp_src = "".join(p.read_text(encoding="utf-8")
                      for p in Path(mcp_pkg.__file__).parent.glob("*.py"))
    assert "execute_bridge" not in mcp_src
    assert not any("bridge" in name for name in MT.TOOLS)

    # (4) the gate the real dispatch seam consults IS the provider-layer
    #     qualification admission — one function, not parallel copies
    monkeypatch.setattr(PB, "dispatch_bridge", real_dispatch)
    gate_calls = []
    real_admission = Q.bridge_admission

    def spy_admission(provider_id, capability="generative_bridge", **kw):
        gate_calls.append((provider_id, capability))
        return real_admission(provider_id, capability, **kw)

    monkeypatch.setattr(Q, "bridge_admission", spy_admission)
    with pytest.raises(ProviderFailure):
        bridge.execute_bridge(tmp_project, "S001", plan,
                              provider=FakeBridgeProvider())
    assert gate_calls == [("fake_bridge", bridge.BRIDGE_CAPABILITY)]


# ============================== B07 fixed "bridge" spec_hash forbidden


def test_b07_fixed_bridge_spec_hash_forbidden(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    prev, nxt = frames(tmp_project)
    plan = make_plan(tmp_project, prev, nxt)
    provider = FakeBridgeProvider()
    take = accept_and_execute(tmp_project, "S001", plan, provider)
    # the spec hash derives from the CURRENT shot + bridge plan
    got = provider.requests[0].spec_hash
    assert got != "bridge" and got.startswith("sha256:")
    assert take.sidecar.spec_hash == got
    # a different plan derives a different spec identity
    plan2 = make_plan(tmp_project, prev, nxt, description="another transition")
    p2 = FakeBridgeProvider()
    accept_and_execute(tmp_project, "S001", plan2, p2)
    assert p2.requests[0].spec_hash != got
    # the literal "bridge" is refused outright
    with pytest.raises(bridge.BridgeError):
        bridge.execute_bridge(tmp_project, "S001", plan, provider=provider,
                              spec_hash="bridge")


# ============================== B08 arbitrary review dict cannot adopt


def _executed_lineage(tmp_project, add_shot, shot="S001"):
    add_shot(tmp_project, shot)
    prev, nxt = frames(tmp_project)
    plan = make_plan(tmp_project, prev, nxt)
    provider = FakeBridgeProvider()
    take = accept_and_execute(tmp_project, shot, plan, provider)
    lineage = bridge.bridge_lineage(plan, take, shot=shot)
    return plan, take, lineage


def _accepted_assurance(lineage, **over):
    from manju.qc.assurance import SCHEMA as ASSURANCE_SCHEMA

    a = {
        "schema": ASSURANCE_SCHEMA,
        "subject": {"kind": "shot", "id": lineage.get("shot") or "S001"},
        "assurance_state": "accepted",
        "spec_hash": lineage.get("spec_hash"),
        "expectation_digest": "sha256:exp1",
        "evidence": {"packet_id": "pkt_abcdef123456",
                     "media_sha256": lineage.get("output_media_hash"),
                     "reviewer": "human"},
    }
    a.update(over)
    return a


def test_b08_arbitrary_review_dict_cannot_adopt(tmp_project, add_shot):
    plan, take, lineage = _executed_lineage(tmp_project, add_shot)
    # the classic forgery: any {passed: true, bound_hash: ...} dict
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review={
            "passed": True, "bound_hash": lineage["output_media_hash"]})
    # a dict that merely CLAIMS the schema but is not an accepted assurance
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review={
            "schema": "manju.qc.assurance/v1", "passed": True,
            "bound_hash": lineage["output_media_hash"]})
    # a non-dict is refused too
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review="LGTM")
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review=None)


# ============================== B09 stale packet/verdict cannot adopt


def test_b09_stale_packet_or_verdict_cannot_adopt(tmp_project, add_shot):
    plan, take, lineage = _executed_lineage(tmp_project, add_shot)
    # a STALE assurance (evidence no longer current-bound) is refused
    stale = _accepted_assurance(lineage, assurance_state="stale")
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(dict(lineage), review=stale)
    # an accepted assurance bound to DIFFERENT (older) bytes is refused
    old_bytes = _accepted_assurance(lineage)
    old_bytes["evidence"]["media_sha256"] = "sha256:OLD-BYTES"
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review=old_bytes)
    # an accepted assurance bound to a DIFFERENT spec is refused (stale spec)
    wrong_spec = _accepted_assurance(lineage, spec_hash="sha256:some-other-spec")
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review=wrong_spec)
    # a bare v2 VERDICT record is observation evidence, never acceptance authority
    verdict_like = {"schema": "manju.qc.verdict/v2", "binding": "stale",
                    "media_sha256": lineage["output_media_hash"]}
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review=verdict_like)


# ============================== B10 current Assurance + exact bytes may propose


def test_b10_current_assurance_and_exact_bytes_propose_adoption(
        tmp_project, add_shot):
    plan, take, lineage = _executed_lineage(tmp_project, add_shot)
    shot_before = (Path(tmp_project.root) / "shots" / "S001.yaml").read_bytes() \
        if (Path(tmp_project.root) / "shots" / "S001.yaml").exists() else None
    review = _accepted_assurance(lineage)
    adopted = bridge.adopt_bridge(lineage, review=review,
                                  media_path=take.media_path)
    assert adopted["adopted"] is True
    proposal = adopted["proposal"]
    assert proposal["do_not_execute_automatically"] is True
    assert proposal["take"] == take.name
    assert proposal["output_media_hash"] == lineage["output_media_hash"]
    # adoption NEVER auto-writes the shot / selected_take / Bible
    spec = tmp_project.load_shot("S001")
    assert spec.status.selected_take != take.name
    if shot_before is not None:
        assert (Path(tmp_project.root) / "shots" / "S001.yaml").read_bytes() \
            == shot_before
    # "exact bytes": tamper with the media AFTER review — adoption refuses
    take.media_path.write_bytes(b"tampered-after-review")
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review=review, media_path=take.media_path)


# ============================== B11 unadopted actual source path/hash never final


def test_b11_unadopted_actual_source_path_hash_cannot_enter_final(
        tmp_project, add_shot):
    plan, take, lineage = _executed_lineage(tmp_project, add_shot)
    # the bridge media sneaks into the final under a DIFFERENT name/path
    renamed = Path(tmp_project.root) / "exports" / "innocent_looking_clip.mp4"
    renamed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(take.media_path, renamed)
    diags = bridge.assert_not_in_final(
        lineage, ["S001/take_09", "exports/innocent_looking_clip.mp4"],
        adopted=False, source_paths=[renamed])
    assert any(d["code"] == "UNAPPROVED_BRIDGE_IN_FINAL" for d in diags)
    # by-name detection still works
    diags2 = bridge.assert_not_in_final(lineage, [take.name], adopted=False)
    assert any(d["code"] == "UNAPPROVED_BRIDGE_IN_FINAL" for d in diags2)
    # an ADOPTED bridge is legitimate, and an unrelated source is clean
    assert bridge.assert_not_in_final(lineage, [take.name], adopted=True,
                                      source_paths=[renamed]) == []
    other = Path(tmp_project.root) / "exports" / "unrelated.mp4"
    other.write_bytes(b"a completely different clip")
    assert bridge.assert_not_in_final(lineage, ["S001/take_09"], adopted=False,
                                      source_paths=[other]) == []


# ============================== B12 restart resumes, never resubmits


def test_b12_restart_resumes_same_submission_never_resubmits(
        tmp_project, add_shot, providers_dir, monkeypatch):
    from manju.build import attempts as A
    from manju.providers import submission as S
    from manju.providers.generic_cloud import GenericCloudProvider
    from manju.providers.manifest import load_manifests

    add_shot(tmp_project, "S001")
    prev, nxt = frames(tmp_project)
    plan = make_plan(tmp_project, prev, nxt)
    pid = write_manifest(providers_dir, monkeypatch, "b12_cloud",
                         caps="image_to_video, generative_bridge")
    # a REAL-transport PRODUCTION_READY qualification recorded durably
    declared = Q.declared_facts(pid, "generative_bridge")
    Q.record_qualification_evidence(tmp_project, pid, "generative_bridge", {
        "level": Q.PRODUCTION_READY, "transport": "real",
        "provider_profile_digest": declared["provider_profile_digest"],
        "adapter_semantic_digest": declared["adapter_semantic_digest"],
        "fixture_version": declared.get("fixture_version"),
        "request_digest": "sha256:req-b12", "response_schema_digest": "sha256:resp-b12",
        "evidence_refs": [], "artifact": None,
        "cost": {"estimated": 0.1, "currency": "CNY", "actual": 0.1},
        "checked_at": "2026-07-12T00:00:00Z",
    })
    manifest = load_manifests()[0][pid]

    # run 1: the submit is ADMITTED, then the poll times out (interrupted run)
    t1 = Canned(poll_running=10_000)
    p1 = GenericCloudProvider(manifest, transport=t1, sleep_fn=lambda _s: None,
                              timeout_s=0.05, max_retries=0)
    with pytest.raises(ProviderFailure):
        bridge.execute_bridge(tmp_project, "S001", plan, provider=p1)
    assert t1.posts == 1
    events, _ = A.read_submission_events(tmp_project)
    sids = {e["submission_id"] for e in events}
    assert len(sids) == 1
    sid = sids.pop()
    assert events[-1]["to"] == S.ADMITTED

    # "restart": the disposable runtime state is deleted wholesale
    shutil.rmtree(Path(tmp_project.root) / ".manju", ignore_errors=True)

    # run 2: the SAME plan resumes the SAME submission poll-only — no resubmit
    t2 = Canned()
    p2 = GenericCloudProvider(manifest, transport=t2, sleep_fn=lambda _s: None)
    takes = bridge.execute_bridge(tmp_project, "S001", plan, provider=p2)
    assert takes is not None
    assert t2.posts == 0                       # NEVER resubmitted
    events2, _ = A.read_submission_events(tmp_project)
    sids2 = {e["submission_id"] for e in events2}
    assert sids2 == {sid}                      # the SAME submission identity
    assert events2[-1]["to"] == S.TERMINAL_SUCCESS
