"""AI_IDE_19 WP5b — generative transition bridge (EXECUTES this batch).

AI_IDE_16 pinned the generative bridge as proposal-only / transport-0; THIS batch
executes it through the STANDARD paid provider path. §11 rows 15-16: the bridge
binds prev-end + next-start frame hashes and its output hash · it rides the paid
submission/recovery path · it is a CANDIDATE requiring a current-bound review ·
an unapproved bridge NEVER reaches a final.
"""

from __future__ import annotations

import pytest

from manju.build import bridge
from manju.providers.base import GenerationRequest


# ------------------------------------------------------------- a scripted provider

class _FakeBridgeProvider:
    """A scripted stand-in for a real video provider (no account here). Registers
    an append-only take through the STANDARD path and records the request it saw."""
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
            req.shot.id, media, TakeSidecar(provider="fake_bridge", spec_hash=req.spec_hash),
            move=False)
        return [take]


# --------------------------------------------------------------- row 15: inputs

def test_plan_binds_both_frame_hashes_and_refuses_empty():
    plan = bridge.plan_bridge(prev_end_frame_hash="sha256:prev",
                              next_start_frame_hash="sha256:next",
                              duration_ms=600, direction="left_to_right",
                              description="dolly through the door")
    assert plan["prev_end_frame_hash"] == "sha256:prev"
    assert plan["next_start_frame_hash"] == "sha256:next"
    assert plan["duration_ms"] == 600 and plan["request_digest"]
    # a bridge with a missing endpoint frame is REFUSED (never a floating bridge)
    with pytest.raises(bridge.BridgeError):
        bridge.plan_bridge(prev_end_frame_hash="", next_start_frame_hash="sha256:n",
                           duration_ms=600)
    with pytest.raises(bridge.BridgeError):
        bridge.plan_bridge(prev_end_frame_hash="sha256:p", next_start_frame_hash="sha256:n",
                           duration_ms=0)


def test_request_digest_is_deterministic_for_paid_recovery():
    # the idempotency key the standard poll-only resume keys on (no resubmit).
    a = bridge.plan_bridge(prev_end_frame_hash="p", next_start_frame_hash="n", duration_ms=500)
    b = bridge.plan_bridge(prev_end_frame_hash="p", next_start_frame_hash="n", duration_ms=500)
    assert a["request_digest"] == b["request_digest"]


# --------------------------------------------------------------- row 15: gate

def test_real_bridge_is_qualification_gated(tmp_project):
    # the bridge gate lives in the PROVIDER layer (build-boundary guard): build/
    # bridge.py must not import qualification; cli.py calls the provider-side gate.
    from manju.providers.qualification import bridge_admission
    # with no real account, a real bridge provider never reaches DRY_RUN_VALID →
    # refusal (never a silent paid submit). Scripted transport is the stand-in.
    decision = bridge_admission("no_such_video_provider", project=tmp_project)
    assert decision["admitted"] is False
    assert decision["refusal"] == "BRIDGE_NOT_QUALIFIED"


# --------------------------------------------------- row 15: standard path + hashes
# 14_21 closeout update: a plan must bind REAL endpoint frame files (hash-only
# is not deliverable input, contract §3) and an unqualified scripted stand-in
# executes only under a one-time operator risk acceptance bound to the exact
# request digest (the low-rung manual-experiment path) — so these tests now
# create real frames and record that acceptance before executing.


def _frames(project):
    d = project.root / "bridge_frames"
    d.mkdir(exist_ok=True)
    prev, nxt = d / "prev.png", d / "next.png"
    prev.write_bytes(b"c19-prev-frame")
    nxt.write_bytes(b"c19-next-frame")
    return prev, nxt


def _plan(project, **kw):
    prev, nxt = _frames(project)
    kw.setdefault("duration_ms", 600)
    return bridge.plan_bridge(prev_end_frame=prev, next_start_frame=nxt,
                              project_root=project.root, **kw)


def _accept(project, provider, plan):
    from manju.providers.qualification import record_bridge_risk_acceptance
    record_bridge_risk_acceptance(project, provider.id, bridge.BRIDGE_CAPABILITY,
                                  plan["request_digest"])


def test_execute_rides_standard_path_append_only_binds_hashes(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    plan = _plan(tmp_project, direction="left_to_right")
    provider = _FakeBridgeProvider()
    _accept(tmp_project, provider, plan)
    before = len(tmp_project.takes("S001"))
    take = bridge.execute_bridge(tmp_project, "S001", plan, provider=provider)
    # standard path: a GenerationRequest was handed to provider.generate
    assert len(provider.requests) == 1
    assert isinstance(provider.requests[0], GenerationRequest)
    assert provider.requests[0].params.get("bridge") == plan
    # append-only: a NEW take was registered, nothing overwritten
    assert len(tmp_project.takes("S001")) == before + 1
    lineage = bridge.bridge_lineage(plan, take)
    assert lineage["prev_end_frame_hash"] == plan["prev_end_frame_hash"]
    assert lineage["next_start_frame_hash"] == plan["next_start_frame_hash"]
    assert lineage["output_media_hash"]      # output hash bound
    assert lineage["is_transition_candidate"] is True
    assert lineage["adopted"] is False


# --------------------------------------------------------------- row 15: review

def test_bridge_requires_current_bound_review_before_adoption(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    plan = _plan(tmp_project, duration_ms=500)
    provider = _FakeBridgeProvider()
    _accept(tmp_project, provider, plan)
    take = bridge.execute_bridge(tmp_project, "S001", plan, provider=provider)
    lineage = bridge.bridge_lineage(plan, take)
    req = bridge.bridge_review_requirement(lineage)
    assert req["required"] is True and req["status"] == "PENDING_REVIEW"
    assert req["bound_hash"] == lineage["output_media_hash"]
    # adoption REFUSED without a passing review bound to the CURRENT bytes
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review=None)
    # a review bound to STALE bytes is refused (current-bound, 不得掩盖 continuity)
    # 14_21 closeout B08/B09 update: adoption consumes only an ACCEPTED
    # Assurance from the existing QC machinery — the old raw
    # {passed, bound_hash} dicts are exactly what the closeout forbids.
    stale = {"schema": bridge.ASSURANCE_SCHEMA, "assurance_state": "accepted",
             "spec_hash": lineage.get("spec_hash"),
             "evidence": {"media_sha256": "sha256:OLD"}}
    with pytest.raises(bridge.BridgeError):
        bridge.adopt_bridge(lineage, review=stale)
    good = {"schema": bridge.ASSURANCE_SCHEMA, "assurance_state": "accepted",
            "spec_hash": lineage.get("spec_hash"),
            "evidence": {"media_sha256": lineage["output_media_hash"]}}
    adopted = bridge.adopt_bridge(lineage, review=good)
    assert adopted["adopted"] is True


# --------------------------------------------------------------- row 16: not final

def test_unapproved_bridge_never_reaches_final(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    plan = _plan(tmp_project, duration_ms=500)
    provider = _FakeBridgeProvider()
    _accept(tmp_project, provider, plan)
    take = bridge.execute_bridge(tmp_project, "S001", plan, provider=provider)
    lineage = bridge.bridge_lineage(plan, take)
    # a compiled final that does NOT reference the bridge media
    compiled_sources = ["S001/take_09", "S002/take_03"]
    diags = bridge.assert_not_in_final(lineage, compiled_sources, adopted=False)
    assert diags == []                               # candidate absent → fine
    # if an un-adopted bridge somehow appears in the final → blocking diagnostic
    sneaky = compiled_sources + [take.name]
    diags2 = bridge.assert_not_in_final(lineage, sneaky, adopted=False)
    assert any(d["code"] == "UNAPPROVED_BRIDGE_IN_FINAL" for d in diags2)
    # once explicitly adopted, its presence is legitimate
    diags3 = bridge.assert_not_in_final(lineage, sneaky, adopted=True)
    assert diags3 == []
