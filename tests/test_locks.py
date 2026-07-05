"""Tests for manju.core.locks — value-hash locks (§5).

A lock stores the hash of a field's value; ``verify_locks`` re-hashes and
compares. Four outcomes matter: pass, changed, missing, unsealed.
"""

from __future__ import annotations

from manju.core.hashing import hash_value
from manju.core.locks import seal_lock, verify_locks
from manju.core.models import ShotSpec


def _shot_data() -> dict:
    return {
        "id": "S002",
        "dialogue": {"speaker": "linxia", "text": "这不可能。"},
        "duration": 4,
    }


def test_seal_lock_returns_hash_of_current_value():
    data = _shot_data()
    sealed = seal_lock(data, "dialogue.text")
    assert sealed == hash_value("这不可能。")


def test_verify_locks_passes_on_untouched_data():
    data = _shot_data()
    locked = {"dialogue.text": seal_lock(data, "dialogue.text")}
    assert verify_locks(data, locked, "shots/S002.yaml") == []


def test_verify_locks_reports_changed_after_mutation():
    data = _shot_data()
    locked = {"dialogue.text": seal_lock(data, "dialogue.text")}
    data["dialogue"]["text"] = "改成别的台词"
    violations = verify_locks(data, locked, "shots/S002.yaml")
    assert len(violations) == 1
    assert violations[0].reason == "changed"
    assert violations[0].path == "dialogue.text"
    assert "changed" in str(violations[0])


def test_verify_locks_reports_missing_after_deletion():
    data = _shot_data()
    locked = {"dialogue.text": seal_lock(data, "dialogue.text")}
    del data["dialogue"]["text"]
    violations = verify_locks(data, locked, "shots/S002.yaml")
    assert len(violations) == 1
    assert violations[0].reason == "missing"


def test_verify_locks_reports_unsealed_for_empty_hash():
    # A hand-written `locked: [dialogue.text]` list coerces to an empty hash.
    data = _shot_data()
    violations = verify_locks(data, {"dialogue.text": ""}, "shots/S002.yaml")
    assert len(violations) == 1
    assert violations[0].reason.startswith("unsealed")


def test_shotspec_coerces_bare_list_into_empty_hash_records():
    # ShotSpec._coerce_locked: `locked: [a.b]` -> {"a.b": ""} (unsealed).
    shot = ShotSpec(id="S001", locked=["dialogue.text", "duration"])
    assert shot.locked == {"dialogue.text": "", "duration": ""}
    # such a shot is flagged unsealed until `manju lock` seals it.
    violations = verify_locks(
        {"dialogue": {"text": "x"}, "duration": 3}, shot.locked, "shots/S001.yaml"
    )
    assert {v.reason.split()[0] for v in violations} == {"unsealed"}
