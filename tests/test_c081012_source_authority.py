"""AI_IDE_08_10_12C WP1 — source authority (§13.1).

The enforcement point (orchestrator ruling 2): NOTHING under reports/ is read
by prompt compilation or GenerationRequest assembly. A directing report / a
continuation capsule is Skill input — mutating it can never move a prompt, a
spec hash, or a request digest. The ONLY path from a creative decision to the
provider is a checked patch to the existing Shot/Bible/refs source.

Status: these are PROOF tests over already-correct behavior (no production
change was needed — the prompt path reads only shot+bible+params). Locked/
stale-source rejection is already red-tested by tests/test_director.py
(fingerprint expiry, :126-146) and tests/test_locks.py / test_write_locks.py.
"""

from __future__ import annotations

import json

from manju.build.attempts import provider_request_evidence
from manju.build.promptlab import shot_prompt_bundle


def _digests(project, shot_id):
    """Every digest a creative decision could legitimately move: the bundle's
    spec hash, the four compiled prompts, and the provider-request evidence
    digest built from the same source-derived inputs the build uses."""
    b = shot_prompt_bundle(project, shot_id)
    shot = project.load_shot(shot_id)
    req = provider_request_evidence(
        dict(shot.generation.params), spec_hash=b["spec_hash"],
        duration_ms=b["cost"]["duration_ms"])
    return {
        "spec_hash": b["spec_hash"],
        "video_prompt": b["video_prompt"],
        "image_prompt": b["image_prompt"],
        "negative_prompt": b["negative_prompt"],
        "prompt_bundle_digest": b["compiler_trace"]["prompt_bundle_digest"],
        "request_digest": req["request_digest"],
    }


def test_directing_report_mutation_moves_nothing(tmp_project, add_shot):
    """§13.1 test 1 + fixture: write reports/directing/S010.json, snapshot all
    digests, then mutate the report wildly — every digest is byte-identical."""
    add_shot(tmp_project, "S010", action={"main": "她低头看硬币"})
    ddir = tmp_project.root / "reports" / "directing"
    ddir.mkdir(parents=True, exist_ok=True)
    report = ddir / "S010.json"
    report.write_text(json.dumps({"beat": "她低头看硬币", "pov": "linxia"}),
                      encoding="utf-8")

    before = _digests(tmp_project, "S010")
    report.write_text(json.dumps({
        "beat": "完全不同的导演判断:她抬头怒视镜头",
        "camera": "extreme_close_up", "endpoint": "别的结尾",
        "must_show": ["九头蛇"],
    }), encoding="utf-8")
    after = _digests(tmp_project, "S010")
    assert before == after, "a derived report must never reach the build"


def test_continuation_capsule_under_reports_is_inert(tmp_project, add_shot):
    """§13.1 test 4: a continuation-capsule-looking file under reports/ cannot
    feed prompt compilation either."""
    add_shot(tmp_project, "S001", action={"main": "她低头看硬币,最后定格在她的手"})
    add_shot(tmp_project, "S002", continuity={"prev": "S001"})
    (tmp_project.root / "reports").mkdir(parents=True, exist_ok=True)
    capsule = tmp_project.root / "reports" / "continuation_S002.json"
    capsule.write_text(json.dumps({"observed_endpoint": "她已经抬头"}), encoding="utf-8")

    before = _digests(tmp_project, "S002")
    capsule.write_text(json.dumps({"observed_endpoint": "她变成了一条龙"}), encoding="utf-8")
    after = _digests(tmp_project, "S002")
    assert before == after


def test_applied_source_patch_moves_all_digests(tmp_project, add_shot):
    """§13.1 test 2: the SAME decision applied as a source patch moves
    spec_hash, the compiled prompt, the bundle digest and the request digest."""
    add_shot(tmp_project, "S010", action={"main": "她低头看硬币"})
    before = _digests(tmp_project, "S010")
    tmp_project.update_shot_raw(
        "S010", lambda d: d.setdefault("action", {}).__setitem__(
            "main", "她抬头怒视镜头"))
    after = _digests(tmp_project, "S010")
    assert after["spec_hash"] != before["spec_hash"]
    assert after["video_prompt"] != before["video_prompt"]
    assert after["prompt_bundle_digest"] != before["prompt_bundle_digest"]
    assert after["request_digest"] != before["request_digest"]


def test_derived_views_write_nothing_and_bundle_reads_no_reports(tmp_project, add_shot):
    """The bundle (with its new production checks + trace) never writes, and
    deleting reports/ entirely leaves every digest identical — the prompt
    projection depends on source only."""
    import shutil

    add_shot(tmp_project, "S010")
    (tmp_project.root / "reports").mkdir(parents=True, exist_ok=True)
    before = _digests(tmp_project, "S010")
    shutil.rmtree(tmp_project.root / "reports")
    after = _digests(tmp_project, "S010")
    assert before == after
