"""AI_IDE_08_10_12C WP3 — candidate family & provenance.

Candidate families are DERIVED views (contract §8.1/8.3): grouped from the
existing take sidecars (creative source revision = spec_hash, explicit-redo
lineage = redo_of) joined with the existing attempt evidence (request_digest +
output hashes). No CandidateSet store, no persistence, never a build input;
never grouped by filename / shot_id / mtime.

Red-first:
  - `manju.qc.production` did not exist (ImportError);
  - an explicit `redo --from-take` recorded NO lineage anywhere (sidecar had
    no redo_of, the redo event names only shot+takes) — proven by
    test_redo_records_lineage_on_sidecar failing pre-fix.
"""

from __future__ import annotations

import hashlib

from manju.core.hashing import hash_file
from manju.core.models import TakeSidecar


def _tree_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _take(project, shot_id, data: bytes, *, spec_hash="h1", params=None,
          redo_of=None, provider="test"):
    tmp = project.root / f"_src_{shot_id}_{hashlib.sha256(data).hexdigest()[:6]}.mp4"
    tmp.write_bytes(data)
    kwargs = {"provider": provider, "spec_hash": spec_hash, "params": params or {}}
    if redo_of is not None:
        kwargs["redo_of"] = redo_of
    take = project.register_take(shot_id, tmp, TakeSidecar(**kwargs))
    tmp.unlink()
    return take


# --------------------------------------------------- redo lineage (red fixture 4)


def test_redo_records_lineage_on_sidecar(tmp_project, add_shot):
    """RED pre-fix: an explicit `redo --from-take take_01` registered a new
    take whose sidecar carried NO lineage — the creative family was
    unreconstructable (redo emits no attempt evidence: registry.py evidence is
    None outside a run). Contract 4.2/8.1: additive sidecar `redo_of`."""
    from manju.build.graph import redo_shot

    add_shot(tmp_project, "S001")
    first = redo_shot(tmp_project, "S001", actor="test", assume_yes=True)
    assert first, "offline fallback chain should mint a take"
    src_name = first[0]

    again = redo_shot(tmp_project, "S001", from_take=src_name,
                      actor="test", assume_yes=True)
    assert again
    new = tmp_project.get_take("S001", again[0])
    assert new.sidecar.redo_of == src_name, (
        "an explicit from-take redo must record its creative parent on the "
        "sidecar (redo_of) — nothing else ties the new take to its family"
    )
    # plain redo (no --from-take) has no single parent take: no invented lineage.
    src = tmp_project.get_take("S001", src_name)
    assert getattr(src.sidecar, "redo_of", None) in (None, "")


# --------------------------------------------------- derived family view


def test_families_group_by_source_revision_and_lineage(tmp_project, add_shot):
    from manju.qc.production import candidate_families

    add_shot(tmp_project, "S001")
    a = _take(tmp_project, "S001", b"take-A", spec_hash="sha256:rev1")
    b = _take(tmp_project, "S001", b"take-B", spec_hash="sha256:rev1")
    # source was edited (rev2) then explicitly redone FROM b -> joins b's family
    c = _take(tmp_project, "S001", b"take-C", spec_hash="sha256:rev2",
              redo_of=b.name)
    # an unrelated new revision take -> its own family
    d = _take(tmp_project, "S001", b"take-D", spec_hash="sha256:rev3")

    view = candidate_families(tmp_project, "S001")
    fams = view["families"]
    by_take = {m["take"]: f for f in fams for m in f["takes"]}
    assert by_take[a.name]["family_id"] == by_take[b.name]["family_id"]
    assert by_take[c.name]["family_id"] == by_take[b.name]["family_id"], \
        "explicit redo joins the parent's creative family across a source edit"
    assert by_take[d.name]["family_id"] != by_take[a.name]["family_id"]
    fam = by_take[a.name]
    assert fam["family_id"].startswith("derived:")
    assert fam["source_spec_hash"] == "sha256:rev1"
    member = next(m for m in fam["takes"] if m["take"] == a.name)
    assert member["media_sha256"] == hash_file(a.media_path)


def test_one_request_n_candidates_share_family_and_indices(tmp_project, add_shot):
    """Fixture 3: one provider request returned N candidates — the attempt
    evidence (request_digest + ordered output hashes) is the join; members
    carry the shared request_digest and their candidate_index."""
    from manju.build.attempts import append_attempt, provider_request_evidence
    from manju.qc.production import candidate_families

    add_shot(tmp_project, "S001")
    t0 = _take(tmp_project, "S001", b"cand-zero", spec_hash="sha256:revX")
    t1 = _take(tmp_project, "S001", b"cand-one", spec_hash="sha256:revX")

    request = provider_request_evidence({"seed": 7}, spec_hash="sha256:revX",
                                        duration_ms=5000)
    append_attempt(tmp_project, {
        "run_id": "run_test", "attempt_id": "att_1", "sequence": 1,
        "stage": "generate", "unit": {"kind": "shot", "shot": "S001"},
        "action": "generate", "state": "SUCCEEDED", "request": request,
        "outputs": [
            {"role": "take", "path": f"media/gen/S001/{t0.name}.mp4",
             "sha256": hash_file(t0.media_path)},
            {"role": "take", "path": f"media/gen/S001/{t1.name}.mp4",
             "sha256": hash_file(t1.media_path)},
        ],
    })

    view = candidate_families(tmp_project, "S001")
    fam = next(f for f in view["families"]
               if any(m["take"] == t0.name for m in f["takes"]))
    members = {m["take"]: m for m in fam["takes"]}
    assert members[t0.name]["request_digest"] == request["request_digest"]
    assert members[t1.name]["request_digest"] == request["request_digest"]
    assert members[t0.name]["candidate_index"] == 0
    assert members[t1.name]["candidate_index"] == 1


def test_view_is_pure_rebuildable_and_writes_nothing(tmp_project, add_shot):
    from manju.qc.production import candidate_families

    add_shot(tmp_project, "S001")
    _take(tmp_project, "S001", b"pure-A")
    _take(tmp_project, "S001", b"pure-B")
    before = _tree_hashes(tmp_project.root)
    v1 = candidate_families(tmp_project, "S001")
    v2 = candidate_families(tmp_project, "S001")
    assert v1 == v2
    assert _tree_hashes(tmp_project.root) == before  # view, not source


def test_keeper_selected_and_disposition_separation(tmp_project, add_shot):
    """§8.4: keeper (a KEEP review conclusion, bound evidence) and
    selected_take (Shot source) are DIFFERENT states, both visible, neither
    implying the other."""
    from manju.qc.agent_review import qc_brief, record_verdicts
    from manju.qc.production import candidate_families

    add_shot(tmp_project, "S001", quality={"must_show": ["红伞出现"]})
    kept = _take(tmp_project, "S001", b"keeper-bytes", spec_hash="sha256:rev1")
    other = _take(tmp_project, "S001", b"chosen-bytes", spec_hash="sha256:rev1")
    # human selects OTHER; reviewer KEEPs the first (bound to its exact bytes)
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", kept.name))
    row = next(r for r in qc_brief(tmp_project, ["S001"])["shots"])
    record_verdicts(tmp_project, {
        "schema": "manju.qc.verdict/v2", "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": "S001"},
        "observations": [{"expectation_id": e["id"], "observed": "present"}
                         for e in row["expectations"]],
        "findings": [], "reviewer": {"kind": "model_visual", "name": "t"},
        "decision": {"disposition": "KEEP"},
    })
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", other.name))

    view = candidate_families(tmp_project, "S001")
    members = {m["take"]: m for f in view["families"] for m in f["takes"]}
    assert members[kept.name]["review_disposition"] == "KEEP"
    assert members[kept.name]["selected"] is False   # KEEP never auto-selects
    assert members[other.name]["selected"] is True
    assert members[other.name]["review_disposition"] is None
