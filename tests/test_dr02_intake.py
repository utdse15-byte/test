"""DR02 WP2 — packet v2 issuance + verdict v2 intake (qc/agent_review.py).

Red-first note: ``_record_verdicts_v2`` / ``read_v2_records`` / ``packets_dir``
and the packet-issuing brief did not exist before WP2; every v2 assertion here
fails with AttributeError/KeyError against the pre-WP2 module. The centerpiece
is the race: a verdict formed against the bytes a packet recorded (A) is stored
bound to A — NEVER re-stamped with the current file's bytes (B) — and marked
binding-stale when the world moved.
"""

from __future__ import annotations

import json
import threading

import pytest

from manju.core.hashing import hash_file
from manju.core.models import TakeSidecar
from manju.core.yamlio import write_yaml
from manju.qc.agent_review import (
    VerdictError,
    _read_records,
    agent_log_path,
    agent_verdict_items,
    packets_dir,
    qc_brief,
    read_v2_records,
    record_verdicts,
)

V2 = "manju.qc.verdict/v2"


# --------------------------------------------------------------- helpers


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _take_with_bytes(project, shot_id, data: bytes, *, select=True):
    tmp = project.root / f"_src_{shot_id}.mp4"
    tmp.write_bytes(data)
    take = project.register_take(shot_id, tmp, TakeSidecar(provider="test", spec_hash="h"))
    tmp.unlink()
    if select:
        _select(project, shot_id, take.name)
    return take


def _brief_rows(project, shots=None):
    return {r["shot"]: r for r in qc_brief(project, shots)["shots"]}


def _v2_verdict(row, *, observations=None, findings=None, **over):
    if observations is None:
        observations = [
            {"expectation_id": e["id"], "observed": "present", "evidence_refs": ["frame:1"]}
            for e in row["expectations"]
        ]
    v = {
        "schema": V2,
        "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": observations,
        "findings": findings if findings is not None else [],
        "reviewer": {"kind": "model_visual", "name": "test"},
    }
    v.update(over)
    return v


def _one_shot(project, add_shot, *, must_show=None, continuity=None, data=b"AAAAA-media-A"):
    kwargs = {}
    if must_show is not None:
        kwargs["quality"] = {"must_show": must_show}
    if continuity is not None:
        kwargs["continuity"] = continuity
    add_shot(project, "S001", **kwargs)
    take = _take_with_bytes(project, "S001", data)
    return take, _brief_rows(project)["S001"]


# --------------------------------------------------- packet issuance in the brief


def test_brief_issues_a_persisted_content_addressed_packet(tmp_project, add_shot):
    take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    assert row["schema"] == "manju.qc.packet/v2"
    assert row["packet_id"].startswith("pkt_") and len(row["packet_id"]) == 16
    assert row["media"]["sha256"] == hash_file(take.media_path)
    # the packet is persisted under reports/qc_packets/<id>.json
    pfile = packets_dir(tmp_project) / f"{row['packet_id']}.json"
    assert pfile.is_file()
    packet = json.loads(pfile.read_text(encoding="utf-8"))
    assert packet["subject"] == {"kind": "shot", "id": "S001"}
    assert packet["expectation_digest"] == row["expectation_digest"]

    # re-issuing an identical packet is idempotent: same id, same file.
    row2 = _brief_rows(tmp_project)["S001"]
    assert row2["packet_id"] == row["packet_id"]


# ------------------------------------------------------ the race (media replaced)


def test_media_replacement_stores_stale_bound_to_A_not_B(tmp_project, add_shot):
    take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"], data=b"AAAA-bytes-A")
    hash_a = hash_file(take.media_path)
    assert row["media"]["sha256"] == hash_a

    # overwrite the selected take file with different bytes B (same path/name).
    take.media_path.write_bytes(b"BBBB-different-bytes-B-longer!!")
    hash_b = hash_file(take.media_path)
    assert hash_b != hash_a

    result = record_verdicts(tmp_project, _v2_verdict(row))
    assert result["written"] == 1
    assert result["bindings"] == {"stale": 1}

    [rec], malformed = read_v2_records(tmp_project)
    assert malformed == 0
    assert rec["binding"] == "stale"
    assert rec["binding_failures"] == ["media"]
    # THE RACE FIX: the stored evidence carries A's hash (the bytes reviewed),
    # never the current file's hash B.
    assert rec["media_sha256"] == hash_a
    assert rec["media_sha256"] != hash_b


def test_clean_submit_is_bound(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    result = record_verdicts(tmp_project, _v2_verdict(row))
    assert result["bindings"] == {"bound": 1}
    [rec], _ = read_v2_records(tmp_project)
    assert rec["binding"] == "bound"
    assert "binding_failures" not in rec


# ---------------------------------------- spec vs expectations binding asymmetry


def test_spec_change_stales_via_spec_binding_only(tmp_project, add_shot):
    """Edit ``action`` (in spec_payload) -> spec_hash moves, expectation digest
    and media do NOT -> the record stales via step 5 alone."""
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("action", {}).__setitem__("main", "全新的动作描述"))
    record_verdicts(tmp_project, _v2_verdict(row))
    [rec], _ = read_v2_records(tmp_project)
    assert rec["binding"] == "stale"
    assert rec["binding_failures"] == ["spec"]


def test_lock_change_stales_via_expectations_binding_only(tmp_project, add_shot):
    """Edit ``continuity.locks`` (NOT in spec_payload) -> expectation digest
    moves, spec_hash does NOT -> the record stales via step 6 ALONE. This is the
    load-bearing asymmetry: step 6 must be able to stale evidence independently."""
    _take, row = _one_shot(tmp_project, add_shot,
                           must_show=["红伞出现"], continuity={"locks": ["prop:coin"]})
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("continuity", {}).__setitem__("locks", ["prop:watch"]))
    record_verdicts(tmp_project, _v2_verdict(row))
    [rec], _ = read_v2_records(tmp_project)
    assert rec["binding"] == "stale"
    assert rec["binding_failures"] == ["expectations"]


# --------------------------------------------------- packet integrity (steps 1/2)


def test_missing_packet_rejects_whole_batch(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    (packets_dir(tmp_project) / f"{row['packet_id']}.json").unlink()
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, _v2_verdict(row))
    assert "packet" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []  # zero writes


def test_forged_packet_content_id_mismatch_rejected(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    pfile = packets_dir(tmp_project) / f"{row['packet_id']}.json"
    data = json.loads(pfile.read_text(encoding="utf-8"))
    data["spec_hash"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    pfile.write_text(json.dumps(data), encoding="utf-8")  # same filename, tampered body
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, _v2_verdict(row))
    assert "id 不符" in str(exc.value) or "伪造" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


def test_subject_mismatch_rejected(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, _v2_verdict(row, subject={"kind": "shot", "id": "S999"}))
    assert "subject" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


# ------------------------------------------------ payload validity (steps 7/8)


def test_unknown_expectation_id_rejects_whole_batch(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    v = _v2_verdict(row, observations=[
        {"expectation_id": "exp:S001:must_show:deadbeef", "observed": "present"}])
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, v)
    assert "expectation_id" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


def test_bad_observed_enum_rejected(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    v = _v2_verdict(row, observations=[
        {"expectation_id": row["expectations"][0]["id"], "observed": "maybe"}])
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, v)
    assert "observed" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


def test_partially_invalid_batch_writes_nothing(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["A出现"]})
    add_shot(tmp_project, "S002", quality={"must_show": ["B出现"]})
    _take_with_bytes(tmp_project, "S001", b"aaaa-1")
    _take_with_bytes(tmp_project, "S002", b"bbbb-2")
    rows = _brief_rows(tmp_project)

    good = _v2_verdict(rows["S001"])
    bad = _v2_verdict(rows["S002"], observations=[
        {"expectation_id": "exp:bogus", "observed": "present"}])  # unknown id -> reject
    with pytest.raises(VerdictError):
        record_verdicts(tmp_project, {"schema": V2, "verdicts": [good, bad]})
    # neither the good nor the bad verdict was written — all-or-nothing.
    assert read_v2_records(tmp_project)[0] == []


def test_valid_batch_writes_all(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["A出现"]})
    add_shot(tmp_project, "S002", quality={"must_show": ["B出现"]})
    _take_with_bytes(tmp_project, "S001", b"aaaa-1")
    _take_with_bytes(tmp_project, "S002", b"bbbb-2")
    rows = _brief_rows(tmp_project)
    result = record_verdicts(tmp_project, {"schema": V2, "verdicts": [
        _v2_verdict(rows["S001"]), _v2_verdict(rows["S002"])]})
    assert result["written"] == 2
    assert read_v2_records(tmp_project)[1] == 0
    assert len(read_v2_records(tmp_project)[0]) == 2


# --------------------------------------------------------- hardening (step 8)


def test_path_traversal_in_evidence_refs_rejected(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    v = _v2_verdict(row, observations=[
        {"expectation_id": row["expectations"][0]["id"], "observed": "present",
         "evidence_refs": ["../../../etc/passwd"]}])
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, v)
    assert "evidence_ref" in str(exc.value) or "不安全" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


def test_oversized_verdict_rejected(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    v = _v2_verdict(row, findings=[{"level": "fyi", "message": "x" * 300_000}])
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, v)
    assert "过大" in str(exc.value)
    assert read_v2_records(tmp_project)[0] == []


def test_secret_bearing_verdict_rejected_not_stored(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    leaked = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    v = _v2_verdict(row, findings=[{"level": "fyi", "message": f"debug key {leaked}"}])
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, v)
    assert "密钥" in str(exc.value) or "key" in str(exc.value).lower()
    # the secret never reached disk.
    assert read_v2_records(tmp_project)[0] == []
    log = agent_log_path(tmp_project)
    if log.exists():
        assert leaked not in log.read_text(encoding="utf-8")


# --------------------------------------------- concurrency / line-atomicity


def test_concurrent_appends_keep_lines_atomic(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])
    n = 12
    errors: list[Exception] = []

    def submit():
        try:
            record_verdicts(tmp_project, _v2_verdict(row, reviewer={"kind": "model_visual"}))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    # every appended line is intact JSON (no torn/interleaved writes) and all n
    # v2 records are present.
    records, malformed = read_v2_records(tmp_project)
    assert malformed == 0
    assert len(records) == n
    raw = agent_log_path(tmp_project).read_text(encoding="utf-8").splitlines()
    for line in raw:
        if line.strip():
            json.loads(line)  # must not raise


# ---------------------------------------------- consistency unit member staleness


def _consistency_fake(project, add_shot):
    write_yaml(project.root / "bible" / "scenes.yaml", {"cs": {"name": "便利店"}})
    write_yaml(project.root / "bible" / "characters.yaml",
               {"linxia": {"name": "林夏"}})
    add_shot(project, "S001", scene="cs", characters=["linxia"])
    add_shot(project, "S002", scene="cs", characters=["linxia"])
    _take_with_bytes(project, "S001", b"s1-aaaa")
    _take_with_bytes(project, "S002", b"s2-bbbb")


def test_consistency_unit_member_change_stales_unit_evidence(tmp_project, add_shot):
    _consistency_fake(tmp_project, add_shot)
    brief = qc_brief(tmp_project, mode="consistency")
    unit = next(u for u in brief["units"] if u["unit"] == "character:linxia")
    assert unit["packet_id"].startswith("pkt_")

    verdict = {
        "schema": V2, "packet_id": unit["packet_id"],
        "subject": {"kind": "unit", "id": "character:linxia"},
        "observations": [],
        "findings": [{"level": "issue", "message": "两镜身份不一致"}],
        "reviewer": {"kind": "model_visual", "name": "t"},
    }

    # bound while the members are unchanged
    record_verdicts(tmp_project, verdict)
    [rec], _ = read_v2_records(tmp_project)
    assert rec["binding"] == "bound"

    # regenerate ONE member -> re-submitting binds stale (member map moved)
    _take_with_bytes(tmp_project, "S002", b"s2-REGENERATED")
    record_verdicts(tmp_project, verdict)
    records, _ = read_v2_records(tmp_project)
    assert records[-1]["binding"] == "stale"
    assert "media" in records[-1]["binding_failures"]


# ---------------------------------------------------- legacy / v2 coexistence


def test_legacy_v2_and_malformed_lines_coexist_in_one_log(tmp_project, add_shot):
    _take, row = _one_shot(tmp_project, add_shot, must_show=["红伞出现"])

    # a legacy (schema-less) verdict, a v2 verdict, and a torn line, all in one log.
    record_verdicts(tmp_project, {"verdicts": [
        {"shot": "S001", "criterion": "A1", "level": "issue", "message": "legacy 结论"}]})
    record_verdicts(tmp_project, _v2_verdict(row))
    with open(agent_log_path(tmp_project), "a", encoding="utf-8") as f:
        f.write("{not valid json,,,\n")

    # legacy reader: keeps the legacy line, skips the v2 line, counts the torn one.
    legacy, malformed = _read_records(tmp_project)
    assert malformed == 1
    assert all(r.get("schema") is None for r in legacy)
    assert any(r.get("criterion") == "A1" for r in legacy)

    # v2 reader: keeps only the v2 line, also counts the torn one.
    v2_records, v2_malformed = read_v2_records(tmp_project)
    assert v2_malformed == 1
    assert len(v2_records) == 1 and v2_records[0]["binding"] == "bound"

    # the legacy merge surfaces the legacy finding + the malformed nudge, and
    # never surfaces the v2 record as an [AI判读] item.
    items = agent_verdict_items(tmp_project)
    assert any("legacy 结论" in i.message for i in items)
    assert any("无法解析" in i.message for i in items)


def test_echoed_binding_fields_must_match_the_packet(tmp_project, add_shot):
    """A verdict that ECHOES media_sha256/spec_hash/expectation_digest values
    contradicting its own packet is a confused payload → whole-batch reject,
    zero writes (distinct from a world move, which stores binding-stale)."""
    take, row = _one_shot(tmp_project, add_shot, must_show=["红色雨伞出现"])
    bad = _v2_verdict(row, media_sha256="sha256:" + "0" * 64)  # contradicts packet
    with pytest.raises(VerdictError, match="回显"):
        record_verdicts(tmp_project, bad)
    assert read_v2_records(tmp_project)[0] == []  # zero writes
