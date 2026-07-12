"""FP Loop G — missing-media report + hash-verified relink (roadmap §6.3).

Red-first tests for :mod:`manju.media.relink`. The through-line is honesty:

- the DETECTION seam already exists (``core/container.py`` — ``TakeInfo.
  media_path`` is ``None`` when a sidecar exists but its media file is gone);
  this loop adds the REPORT over it plus the safe relink workflow;
- ``missing_media_report`` / ``relink_plan`` are ZERO-WRITE (pinned by byte
  snapshots of the whole project);
- a recorded content hash (attempt-evidence ``outputs[].sha256`` — the one
  durable media-bytes lineage, ``build/attempts.py``) is the ONLY basis for a
  verified relink; where no hash was ever recorded the report says so and
  candidates are name-only ADVISORY;
- ``apply_relink`` is CAS: every candidate is re-hashed AT APPLY TIME, and the
  restore is a byte-copy back to the ORIGINAL recorded project-relative path —
  truth files (sidecars/shots/timeline/bible) are NEVER rewritten;
- restore targets are confined to ``media/gen/**`` / ``media/refs/**``;
  ``media/imports/`` (ingest-only) and anything escaping the project refuse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.hashing import hash_file
from manju.core.yamlio import read_yaml, write_yaml

from manju.media.relink import (
    RELINK_PLAN_SCHEMA,
    RelinkError,
    apply_relink,
    missing_media_report,
    relink_plan,
)


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #


def _append_take_output_evidence(project: Project, shot_id: str, take) -> str:
    """Append ONE attempt-evidence line to events.jsonl recording the take's
    media output (path + sha256 + bytes) — the exact shape
    ``providers/registry.py`` emits and ``build/attempts.read_attempts``
    projects back out. Returns the recorded sha256."""
    sha = hash_file(take.media_path)
    rel = project.relpath(take.media_path)
    line = {
        "action": "stage_attempt",
        "actor": "test",
        "detail": {
            "state": "SUCCEEDED",
            "sequence": 1,
            "attempt_id": f"att-{shot_id}-{take.name}",
            "run_id": "run-test",
            "unit": {"kind": "shot", "shot": shot_id},
            "outputs": [{
                "role": "take", "path": rel, "sha256": sha,
                "bytes": take.media_path.stat().st_size, "take": take.name,
            }],
        },
    }
    with open(project.root / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return sha


def _project_byte_state(project: Project) -> dict[str, str]:
    """relpath -> sha256 for EVERY file under the project (the zero-write pin)."""
    return {
        project.relpath(p): hash_file(p)
        for p in sorted(project.root.rglob("*"))
        if p.is_file()
    }


def _row(report: dict, kind: str, **match) -> dict:
    rows = [r for r in report["rows"] if r["kind"] == kind
            and all(r.get(k) == v for k, v in match.items())]
    assert rows, f"no {kind} row matching {match} in {report['rows']}"
    return rows[0]


# --------------------------------------------------------------------------- #
# missing_media_report — detection                                            #
# --------------------------------------------------------------------------- #


def test_missing_take_detected_without_recorded_hash(tmp_project, add_shot, make_take):
    """A sidecar whose media file is gone (container.py media_path=None seam)
    lands in the report; with NO recorded hash the row says so honestly."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:spec1")
    take.media_path.unlink()  # media gone, sidecar (truth) stays

    report = missing_media_report(tmp_project)
    row = _row(report, "take", shot="S001", take=take.name)
    assert row["expected_hash"] is None
    assert row["last_known_relpath"] is None  # never fabricated
    assert row["hash_relink_available"] is False
    assert "advisory" in row["note"].lower() or "ADVISORY" in row["note"]
    assert report["summary"]["by_kind"]["take"] == 1


def test_missing_take_with_recorded_hash(tmp_project, add_shot, make_take):
    """Attempt evidence recorded the media's sha256 + relpath: the row carries
    both, and hash-verified relink is available."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:spec1")
    rel = tmp_project.relpath(take.media_path)
    sha = _append_take_output_evidence(tmp_project, "S001", take)
    take.media_path.unlink()

    report = missing_media_report(tmp_project)
    row = _row(report, "take", shot="S001", take=take.name)
    assert row["expected_hash"] == sha
    assert row["last_known_relpath"] == rel
    assert row["hash_relink_available"] is True


def test_present_take_not_reported(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:spec1")
    report = missing_media_report(tmp_project)
    assert report["summary"]["by_kind"]["take"] == 0


def test_timeline_missing_source_detected(tmp_project):
    """Timeline clip sources that do not resolve are reported (video + music),
    while ``__slate__`` virtual sources are placeholders, never 'missing'."""
    timeline = {
        "tracks": {
            "video": [
                {"shot": "S001", "take": "take_01",
                 "source": "media/gen/S001/take_01.mp4",
                 "start_ms": 0, "duration_ms": 1000},
                {"shot": "S002", "take": "__slate__",
                 "source": "__slate__/S002", "start_ms": 1000, "duration_ms": 1000},
            ],
            "music": [{"source": "media/refs/bgm.wav", "start_ms": 0}],
        },
    }
    from manju.core.models import Timeline
    tmp_project.save_timeline(Timeline.model_validate(timeline))

    report = missing_media_report(tmp_project)
    vrow = _row(report, "timeline_source", source="media/gen/S001/take_01.mp4")
    assert "video[0]" in vrow["referenced_by"]
    _row(report, "timeline_source", source="media/refs/bgm.wav")
    slate = [r for r in report["rows"] if "__slate__" in str(r.get("source", ""))]
    assert slate == [], "__slate__ virtual sources must never be reported missing"
    assert report["skipped_virtual_sources"] == 1
    # no recorded hash for either source → advisory only
    assert vrow["expected_hash"] is None and vrow["hash_relink_available"] is False


def test_ref_pin_missing_detected(tmp_project):
    """A bible ref_image pin naming a file that no longer exists is a `ref` row
    (the reference media the model actually tracks — core/refs.py bible pins)."""
    data = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    data["linxia"]["ref_image"] = "media/refs/linxia_ref.png"
    write_yaml(tmp_project.root / "bible" / "characters.yaml", data)

    report = missing_media_report(tmp_project)
    row = _row(report, "ref", asset_id="linxia")
    assert row["last_known_relpath"] == "media/refs/linxia_ref.png"
    assert row["expected_hash"] is None  # refs never get a recorded hash today
    assert row["hash_relink_available"] is False


# --------------------------------------------------------------------------- #
# relink_plan — bounded, hash-first, deterministic, zero-write                #
# --------------------------------------------------------------------------- #


def _missing_take_with_candidate(tmp_project, add_shot, make_take, tmp_path,
                                 candidate_name: str = "renamed_elsewhere.bin"):
    """A missing take WITH recorded hash + a byte-identical candidate under an
    external search root, under a DIFFERENT name (hash-first must find it)."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:spec1")
    rel = tmp_project.relpath(take.media_path)
    original = take.media_path.read_bytes()
    sha = _append_take_output_evidence(tmp_project, "S001", take)
    take.media_path.unlink()

    root = tmp_path / "backup_disk"
    root.mkdir()
    (root / candidate_name).write_bytes(original)
    return take, rel, sha, original, root


def test_plan_finds_hash_verified_candidate(tmp_project, add_shot, make_take, tmp_path):
    take, rel, sha, _, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)

    plan = relink_plan(tmp_project, [root])
    assert plan["schema"] == RELINK_PLAN_SCHEMA
    row = next(r for r in plan["rows"] if r["missing"]["kind"] == "take")
    assert row["verified"] is True
    assert row["method"] == "content_hash"
    assert row["action"] == "relink"
    assert row["candidate"]["hash"] == sha
    assert row["candidate"]["path"].endswith("renamed_elsewhere.bin")
    assert row["missing"]["last_known_relpath"] == rel


def test_plan_hashless_item_is_name_only_advisory(tmp_project, tmp_path):
    """A hashless missing ref: candidates match by name only and are marked
    ADVISORY — the plan never pretends verification it does not have."""
    data = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    data["linxia"]["ref_image"] = "media/refs/linxia_ref.png"
    write_yaml(tmp_project.root / "bible" / "characters.yaml", data)
    root = tmp_path / "usb"
    root.mkdir()
    (root / "linxia_ref.png").write_bytes(b"png-bytes")

    plan = relink_plan(tmp_project, [root])
    row = next(r for r in plan["rows"] if r["missing"]["kind"] == "ref")
    assert row["verified"] is False
    assert row["method"] == "name_only"
    assert row["verification"] == "name_only_advisory"
    assert row["action"] == "relink"  # proposed; apply gates it behind opt-in
    assert row["candidate"]["path"].endswith("linxia_ref.png")
    assert row["candidate"]["hash"] is None  # never hashed → never claimed


def test_plan_no_candidate_row_is_a_skip(tmp_project, add_shot, make_take, tmp_path):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:spec1")
    _append_take_output_evidence(tmp_project, "S001", take)
    take.media_path.unlink()
    empty = tmp_path / "empty_root"
    empty.mkdir()

    plan = relink_plan(tmp_project, [empty])
    row = next(r for r in plan["rows"] if r["missing"]["kind"] == "take")
    assert row["candidate"] is None
    assert row["action"] == "skip"
    assert row["reason"] == "no_candidate"


def test_plan_deterministic_ordering_and_duplicate_candidates(
        tmp_project, add_shot, make_take, tmp_path):
    """Two byte-identical candidates: the sorted-first is THE candidate, the
    other is listed; two runs emit byte-identical plans."""
    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path, candidate_name="b_copy.bin")
    (root / "a_copy.bin").write_bytes(original)

    plan1 = relink_plan(tmp_project, [root])
    plan2 = relink_plan(tmp_project, [root])
    assert json.dumps(plan1, sort_keys=True) == json.dumps(plan2, sort_keys=True)
    row = next(r for r in plan1["rows"] if r["missing"]["kind"] == "take")
    assert row["candidate"]["path"].endswith("a_copy.bin")
    assert any(p.endswith("b_copy.bin") for p in row["other_candidates"])


def test_report_and_plan_are_zero_write(tmp_project, add_shot, make_take, tmp_path):
    """The zero-write pin: project bytes are hash-identical (and the file SET
    unchanged) across report + plan."""
    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)
    before = _project_byte_state(tmp_project)
    missing_media_report(tmp_project)
    relink_plan(tmp_project, [root])
    assert _project_byte_state(tmp_project) == before


def test_scan_caps_honored_structured_partial_scan(
        tmp_project, add_shot, make_take, tmp_path):
    """A root with more files than the cap ⇒ structured partial-scan note,
    bounded work, never a hang; an oversize candidate is size-skipped, not
    hashed."""
    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)
    for i in range(10):
        (root / f"filler_{i:02d}.bin").write_bytes(b"x" * (i + 1))

    plan = relink_plan(tmp_project, [root], max_files=5)
    assert plan["scan"]["complete"] is False
    assert plan["scan"]["files_scanned"] == 5
    assert plan["scan"]["cap_files"] == 5
    assert any("partial" in n for n in plan["scan"]["notes"])

    # oversize: candidate bigger than the per-file hash cap is never hashed
    plan2 = relink_plan(tmp_project, [root], max_bytes_per_file=4)
    assert plan2["scan"]["skipped_oversize"] > 0
    row = next(r for r in plan2["rows"] if r["missing"]["kind"] == "take")
    assert row["action"] == "skip" and row["reason"] == "no_candidate"


# --------------------------------------------------------------------------- #
# apply_relink — CAS, per-row atomic, truth untouched                          #
# --------------------------------------------------------------------------- #


def test_apply_restores_bytes_to_recorded_relpath(
        tmp_project, add_shot, make_take, tmp_path):
    """The verified restore: bytes come back to the ORIGINAL project-relative
    path, hash-verified after write; sidecar/shot/timeline truth is untouched."""
    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)
    truth_before = {
        p: hash_file(p) for p in [
            take.sidecar_path, tmp_project.shot_path("S001"),
            tmp_project.shots_dir / "index.yaml",
        ]
    }

    plan = relink_plan(tmp_project, [root])
    result = apply_relink(tmp_project, plan)

    restored = tmp_project.root / rel
    assert restored.exists() and restored.read_bytes() == original
    assert hash_file(restored) == sha
    row = next(r for r in result["rows"] if r["status"] == "restored")
    assert row["target"] == rel
    assert row["written_hash"] == sha
    assert row["verified"] is True
    assert result["summary"]["restored"] == 1
    # truth files byte-identical — relink restores MEDIA, never rewrites truth
    assert {p: hash_file(p) for p in truth_before} == truth_before
    # the take resolves again through the ONE container seam
    assert tmp_project.get_take("S001", take.name).media_path is not None


def test_apply_refuses_tampered_candidate_cas(
        tmp_project, add_shot, make_take, tmp_path):
    """CAS: candidate bytes changed between plan and apply ⇒ that row refuses,
    nothing is written."""
    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)
    plan = relink_plan(tmp_project, [root])
    (root / "renamed_elsewhere.bin").write_bytes(b"TAMPERED")

    result = apply_relink(tmp_project, plan)
    row = result["rows"][0]
    assert row["status"] == "refused"
    assert row["reason"] == "candidate_hash_mismatch"
    assert not (tmp_project.root / rel).exists()
    assert result["summary"]["refused"] == 1


def test_apply_refuses_vanished_candidate(tmp_project, add_shot, make_take, tmp_path):
    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)
    plan = relink_plan(tmp_project, [root])
    (root / "renamed_elsewhere.bin").unlink()

    result = apply_relink(tmp_project, plan)
    assert result["rows"][0]["status"] == "refused"
    assert result["rows"][0]["reason"] == "candidate_vanished"
    assert not (tmp_project.root / rel).exists()


def test_apply_is_per_row_atomic(tmp_project, add_shot, make_take, tmp_path):
    """One good row + one tampered row in the same plan: the good row restores,
    the bad one refuses — no partial surprises, the report says exactly what
    happened."""
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    t1 = make_take(tmp_project, "S001", "sha256:spec1")
    t2 = make_take(tmp_project, "S002", "sha256:spec2")
    rel1, rel2 = (tmp_project.relpath(t.media_path) for t in (t1, t2))
    bytes1, bytes2 = t1.media_path.read_bytes(), t2.media_path.read_bytes()
    _append_take_output_evidence(tmp_project, "S001", t1)
    _append_take_output_evidence(tmp_project, "S002", t2)
    t1.media_path.unlink()
    t2.media_path.unlink()
    root = tmp_path / "root"
    root.mkdir()
    (root / "one.bin").write_bytes(bytes1)
    (root / "two.bin").write_bytes(bytes2)

    plan = relink_plan(tmp_project, [root])
    (root / "two.bin").write_bytes(b"TAMPERED-2")
    result = apply_relink(tmp_project, plan)

    assert result["summary"]["restored"] == 1
    assert result["summary"]["refused"] == 1
    assert (tmp_project.root / rel1).read_bytes() == bytes1
    assert not (tmp_project.root / rel2).exists()


def test_apply_unverified_requires_opt_in(tmp_project, tmp_path):
    """A name-only advisory row refuses without allow_unverified=True; with it,
    the restore happens AND is recorded as unverified (re-hashed, no silent
    lies)."""
    data = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    data["linxia"]["ref_image"] = "media/refs/linxia_ref.png"
    write_yaml(tmp_project.root / "bible" / "characters.yaml", data)
    root = tmp_path / "usb"
    root.mkdir()
    (root / "linxia_ref.png").write_bytes(b"png-bytes")

    plan = relink_plan(tmp_project, [root])

    refused = apply_relink(tmp_project, plan)
    assert refused["rows"][0]["status"] == "refused"
    assert refused["rows"][0]["reason"] == "unverified_requires_opt_in"
    assert not (tmp_project.root / "media/refs/linxia_ref.png").exists()

    result = apply_relink(tmp_project, plan, allow_unverified=True)
    row = result["rows"][0]
    assert row["status"] == "restored_unverified"
    assert row["verified"] is False
    assert row["written_hash"] == hash_file(root / "linxia_ref.png")
    assert (tmp_project.root / "media/refs/linxia_ref.png").read_bytes() == b"png-bytes"
    assert result["summary"]["restored_unverified"] == 1


def _handcrafted_plan(target: str, candidate: Path, *, verified=True,
                      expected_hash=None) -> dict:
    """A plan an adversary (or a stale file) could hand to apply — apply must
    re-guard every row, never trust the plan."""
    return {
        "schema": RELINK_PLAN_SCHEMA,
        "rows": [{
            "missing": {"kind": "take", "id": "S001/take_01",
                        "last_known_relpath": target,
                        "expected_hash": expected_hash or hash_file(candidate)},
            "candidate": {"path": str(candidate), "size": candidate.stat().st_size,
                          "hash": expected_hash or hash_file(candidate)},
            "verified": verified,
            "method": "content_hash" if verified else "name_only",
            "action": "relink",
        }],
    }


def test_apply_refuses_imports_target(tmp_project, tmp_path):
    """media/imports is ingest-only: NEVER a restore target."""
    cand = tmp_path / "cand.bin"
    cand.write_bytes(b"bytes")
    plan = _handcrafted_plan("media/imports/x.mp4", cand)
    result = apply_relink(tmp_project, plan)
    assert result["rows"][0]["status"] == "refused"
    assert result["rows"][0]["reason"] == "target_in_imports"
    assert not (tmp_project.root / "media/imports/x.mp4").exists()


def test_apply_refuses_target_escape_and_non_media_targets(tmp_project, tmp_path):
    cand = tmp_path / "cand.bin"
    cand.write_bytes(b"bytes")

    escape = apply_relink(tmp_project, _handcrafted_plan("../evil.mp4", cand))
    assert escape["rows"][0]["status"] == "refused"
    assert escape["rows"][0]["reason"] == "target_escapes_project"

    truth = apply_relink(tmp_project, _handcrafted_plan("shots/S001.yaml", cand))
    assert truth["rows"][0]["status"] == "refused"
    assert truth["rows"][0]["reason"] == "target_not_restorable"
    assert not (tmp_project.root.parent / "evil.mp4").exists()


def test_apply_never_overwrites_an_existing_target(tmp_project, tmp_path):
    """Media is append-only: a target that exists again is refused, bytes on
    disk stay exactly as they were."""
    target_rel = "media/gen/S001/take_01.mp4"
    target = tmp_project.root / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"already-here")
    cand = tmp_path / "cand.bin"
    cand.write_bytes(b"other-bytes")

    result = apply_relink(tmp_project, _handcrafted_plan(target_rel, cand))
    assert result["rows"][0]["status"] == "refused"
    assert result["rows"][0]["reason"] == "target_already_present"
    assert target.read_bytes() == b"already-here"


def test_apply_rejects_wrong_schema_plan(tmp_project, tmp_path):
    cand = tmp_path / "cand.bin"
    cand.write_bytes(b"bytes")
    plan = _handcrafted_plan("media/gen/S001/take_01.mp4", cand)
    plan["schema"] = "manju.other/v9"
    with pytest.raises(RelinkError):
        apply_relink(tmp_project, plan)


def test_apply_appends_relink_event(tmp_project, add_shot, make_take, tmp_path):
    """Apply (a mutation) leaves evidence: one relink_apply event line."""
    from manju.core.events import tail_events

    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)
    plan = relink_plan(tmp_project, [root])
    apply_relink(tmp_project, plan)
    actions = [e.get("action") for e in tail_events(tmp_project.root, 10)]
    assert "relink_apply" in actions


# --------------------------------------------------------------------------- #
# CLI wrapper — manju relink report|plan|apply                                 #
# --------------------------------------------------------------------------- #


def test_cli_relink_end_to_end(tmp_project, add_shot, make_take, tmp_path, monkeypatch):
    """report --json lists the missing take; plan --root --out writes the plan;
    apply --plan restores the bytes to the recorded relpath."""
    from typer.testing import CliRunner

    from manju.cli import app

    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()

    res = runner.invoke(app, ["relink", "report", "--json"])
    assert res.exit_code == 0, res.output
    report = json.loads(res.stdout)
    assert report["summary"]["by_kind"]["take"] == 1

    plan_path = tmp_path / "plan.json"
    res = runner.invoke(app, ["relink", "plan", "--root", str(root),
                              "--out", str(plan_path), "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(plan_path.read_text(encoding="utf-8"))["schema"] == RELINK_PLAN_SCHEMA

    res = runner.invoke(app, ["relink", "apply", "--plan", str(plan_path), "--json"])
    assert res.exit_code == 0, res.output
    result = json.loads(res.stdout)
    assert result["summary"]["restored"] == 1
    assert (tmp_project.root / rel).read_bytes() == original


def test_cli_relink_apply_refusal_exits_nonzero(tmp_project, add_shot, make_take,
                                                tmp_path, monkeypatch):
    """A refused row is a real signal: apply exits non-zero with the structured
    result still on stdout (--json error contract)."""
    from typer.testing import CliRunner

    from manju.cli import app

    take, rel, sha, original, root = _missing_take_with_candidate(
        tmp_project, add_shot, make_take, tmp_path)
    plan = relink_plan(tmp_project, [root])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    (root / "renamed_elsewhere.bin").write_bytes(b"TAMPERED")
    monkeypatch.chdir(tmp_project.root)

    res = CliRunner().invoke(
        app, ["relink", "apply", "--plan", str(plan_path), "--json"])
    assert res.exit_code == 1
    result = json.loads(res.stdout)
    assert result["summary"]["refused"] == 1
    assert not (tmp_project.root / rel).exists()
