"""DR02 WP0 — same-name media-replacement race characterization test.

The bound-acceptance-evidence contract turns on ONE hazard: a review verdict is
formed by looking at the bytes a brief showed (media A), but by the time the
verdict is filed the selected take file at that same path may hold DIFFERENT
bytes (media B) — a regeneration that reused the name, or an out-of-band
overwrite. If intake stamps the CURRENT file's hash onto the stored evidence, a
judgment made against A is silently rebound to B. That is the race.

``Project.register_take`` never overwrites a take's media (container.py) so the
replacement here is done OUT OF BAND (writing the file directly) — that is
honest: the defense the intake must provide is exactly the "same path, new
bytes" case, however the bytes got there.

First run (audit RED): this test drives TODAY's ``record_verdicts`` legacy path,
which resolves + hashes the CURRENT file at intake (``_resolve_take``), so the
stored ``take_hash`` is B's hash — the verdict formed against A is bound to B.
The assertion that it must NOT be bound to B therefore FAILS, proving the race.

After the WP2 v2 intake lands, this test is adjusted to submit a v2 verdict
(bound to the packet issued against A); intake compares current bytes to the
packet's recorded media sha, finds they moved, and stores the record marked
binding-stale WITHOUT restamping B's hash — so the same characterization now
passes. The race staying fixed is the point.
"""

from __future__ import annotations

from manju.core.hashing import hash_file
from manju.core.models import TakeSidecar
from manju.qc.agent_review import qc_brief, read_v2_records, record_verdicts

MEDIA_A = b"AAAA-original-selected-take-bytes-that-the-reviewer-actually-saw"
MEDIA_B = b"BBBB-different-bytes-swapped-in-at-the-same-path-after-the-brief!!"


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _take_with_bytes(project, shot_id, data: bytes):
    """Register a take from a temp file carrying exactly ``data`` and select it.
    Returns the registered TakeInfo (its ``media_path`` is the in-project copy)."""
    tmp = project.root / f"_dr02_src_{shot_id}.mp4"
    tmp.write_bytes(data)
    take = project.register_take(
        shot_id, tmp, TakeSidecar(provider="test", spec_hash="h")
    )
    tmp.unlink()
    return take


def test_dr02_wp0_same_name_media_replacement_race(tmp_project, add_shot):
    """FIRST-RUN AUDIT RED (recorded verbatim in the WP report): with this test
    driving the LEGACY ``record_verdicts`` path, intake re-hashed the current
    file, so the stored ``take_hash`` was B's hash and the assertion
    ``stored != hash_b`` FAILED — proving the race. It now drives the v2 intake
    path (below) and passes: the same replacement is caught, and the evidence
    stays bound to A."""
    # 1. a shot with a selected take, media bytes A.
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞必须出现"]})
    take = _take_with_bytes(tmp_project, "S001", MEDIA_A)
    _select(tmp_project, "S001", take.name)
    hash_a = hash_file(take.media_path)

    # 2. brief the shot; it issues a packet binding the reviewer to A's bytes.
    row = qc_brief(tmp_project, ["S001"])["shots"][0]
    assert row["shot"] == "S001"
    assert row["media"]["sha256"] == hash_a  # the packet recorded A

    # 3. overwrite the take media file with DIFFERENT bytes B, same path/name
    #    (out of band — register_take itself never clobbers; see module docstring).
    take.media_path.write_bytes(MEDIA_B)
    hash_b = hash_file(take.media_path)
    assert hash_b != hash_a

    # 4. submit a v2 verdict built against the packet from step 2.
    record_verdicts(tmp_project, {
        "schema": "manju.qc.verdict/v2",
        "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": "S001"},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [
            {"expectation_id": row["expectations"][0]["id"], "observed": "present"}],
        "findings": [],
        "reviewer": {"kind": "model_visual", "name": "eyes"},
    }, actor="ai")

    # 5. the stored evidence must NOT bind this verdict to B's hash as current:
    #    it is marked binding-stale and still carries A's hash, never B's.
    [rec], malformed = read_v2_records(tmp_project)
    assert malformed == 0
    assert rec["binding"] == "stale"
    assert rec["binding_failures"] == ["media"]
    assert rec["media_sha256"] == hash_a, "evidence must stay bound to the bytes reviewed (A)"
    assert rec["media_sha256"] != hash_b, (
        "verdict formed against media A must never be rebound to media B at "
        "intake — the same-name media-replacement race stays fixed"
    )
