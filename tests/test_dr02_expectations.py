"""DR02 WP1 — ExpectationSet compilation (qc/expectations.py).

Red-first note: this whole module (and thus every import below) did not exist
before WP1; the pre-implementation run is a bare ModuleNotFoundError on
``manju.qc.expectations``. These tests then pin the compile rules, the
reorder/key-order/unicode identity stability, the empty-set validity, and the
spec_hash-vs-digest binding asymmetry (ruling #6).
"""

from __future__ import annotations

import unicodedata

from manju.core.spec import SPEC_VERSION, compute_spec_hash
from manju.core.yamlio import write_yaml
from manju.qc.expectations import SCHEMA, compile_expectations


def _current_spec_hash(project, shot_id):
    shot = project.load_shot(shot_id)
    return compute_spec_hash(shot, project.load_bible(),
                             version=SPEC_VERSION, project_root=project.root)


# --------------------------------------------------------------- compile rules


def test_must_show_compiles_present_external_visual(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞必须出现"]})
    es = compile_expectations(tmp_project, "S001")

    assert es["schema"] == SCHEMA
    assert es["subject"] == {"kind": "shot", "id": "S001"}
    assert es["spec_hash"] == _current_spec_hash(tmp_project, "S001")
    assert len(es["expectations"]) == 1
    exp = es["expectations"][0]
    assert exp["polarity"] == "present"
    assert exp["check"] == "external_visual"
    assert exp["statement"] == "红色雨伞必须出现"  # verbatim
    assert exp["required"] is True
    assert exp["source_path"] == "shots/S001.yaml#/quality/must_show/0"
    assert exp["id"].startswith("exp:S001:must_show:")
    assert len(exp["id"].rsplit(":", 1)[1]) == 8  # 8 hex
    assert es["digest"].startswith("sha256:")


def test_avoid_compiles_absent_external_visual(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"avoid": ["穿帮的现代物品"]})
    es = compile_expectations(tmp_project, "S001")
    [exp] = es["expectations"]
    assert exp["polarity"] == "absent"
    assert exp["check"] == "external_visual"
    assert exp["id"].startswith("exp:S001:avoid:")
    assert exp["source_path"] == "shots/S001.yaml#/quality/avoid/0"


def test_lock_compiles_present_external_consistency(tmp_project, add_shot):
    add_shot(tmp_project, "S001", continuity={"locks": ["prop:coin", "character:linxia"]})
    es = compile_expectations(tmp_project, "S001")
    kinds = {e["id"].split(":")[2] for e in es["expectations"]}
    assert kinds == {"lock"}
    for exp in es["expectations"]:
        assert exp["polarity"] == "present"
        assert exp["check"] == "external_consistency"
    # lock strings are compiled verbatim (structured refs by convention, but
    # unvalidated free strings in the schema — no parsing).
    statements = {e["statement"] for e in es["expectations"]}
    assert statements == {"prop:coin", "character:linxia"}
    paths = {e["source_path"] for e in es["expectations"]}
    assert paths == {"shots/S001.yaml#/continuity/locks/0",
                     "shots/S001.yaml#/continuity/locks/1"}


def test_all_three_sources_coexist(tmp_project, add_shot):
    add_shot(tmp_project, "S001",
             quality={"must_show": ["A出现"], "avoid": ["B出现"]},
             continuity={"locks": ["character:linxia"]})
    es = compile_expectations(tmp_project, "S001")
    assert len(es["expectations"]) == 3
    kinds = sorted(e["id"].split(":")[2] for e in es["expectations"])
    assert kinds == ["avoid", "lock", "must_show"]


# --------------------------------------------------------------- duplicates


def test_exact_duplicate_collapses_to_one_id(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞", "红色雨伞"]})
    es = compile_expectations(tmp_project, "S001")
    assert len(es["expectations"]) == 1  # collapsed, never two ids
    exp = es["expectations"][0]
    assert exp["duplicates"] == ["shots/S001.yaml#/quality/must_show/1"]
    assert exp["source_path"] == "shots/S001.yaml#/quality/must_show/0"  # first kept


def test_whitespace_and_nfc_variants_are_the_same_expectation(tmp_project, add_shot):
    # normalization (NFC + strip + collapse whitespace) drives dedup identity.
    add_shot(tmp_project, "S001",
             quality={"must_show": ["红色  雨伞", " 红色 雨伞 "]})
    es = compile_expectations(tmp_project, "S001")
    assert len(es["expectations"]) == 1


# --------------------------------------------------------------- empty set


def test_no_explicit_constraints_yields_empty_set(tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # default quality/continuity are empty
    es = compile_expectations(tmp_project, "S001")
    assert es["expectations"] == []
    assert es["digest"].startswith("sha256:")  # empty set still has a stable digest


def test_legacy_shot_without_quality_or_continuity_section(tmp_project):
    # a bare shot file that predates the quality/continuity sections entirely
    write_yaml(tmp_project.root / "shots" / "S900.yaml", {
        "id": "S900", "scene": "convenience_store", "characters": ["linxia"],
    })
    es = compile_expectations(tmp_project, "S900")
    assert es["expectations"] == []


# ------------------------------------------------------- identity stability


def test_reorder_must_show_keeps_ids_and_digest(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["第一项", "第二项", "第三项"]})
    first = compile_expectations(tmp_project, "S001")
    ids_first = {e["id"] for e in first["expectations"]}

    # reorder the list — ids are content-derived, so they must not move, and the
    # digest reflects the SET not the order.
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("quality", {}).__setitem__(
            "must_show", ["第三项", "第一项", "第二项"]),
    )
    second = compile_expectations(tmp_project, "S001")
    ids_second = {e["id"] for e in second["expectations"]}

    assert ids_first == ids_second
    assert first["digest"] == second["digest"]
    # provenance DOES follow the new authored index (identity vs provenance)
    by_stmt = {e["statement"]: e["source_path"] for e in second["expectations"]}
    assert by_stmt["第三项"] == "shots/S001.yaml#/quality/must_show/0"


def test_yaml_key_order_does_not_change_digest(tmp_project):
    write_yaml(tmp_project.root / "shots" / "A.yaml", {
        "id": "A", "scene": "convenience_store",
        "quality": {"must_show": ["X出现"], "avoid": ["Y出现"]},
        "continuity": {"locks": ["character:linxia"]},
    })
    write_yaml(tmp_project.root / "shots" / "B.yaml", {
        "continuity": {"locks": ["character:linxia"]},
        "quality": {"avoid": ["Y出现"], "must_show": ["X出现"]},
        "scene": "convenience_store", "id": "B",
    })
    a = compile_expectations(tmp_project, "A")
    b = compile_expectations(tmp_project, "B")
    # ids embed the shot id, so strip that segment when comparing across shots.
    def _bare(es):
        return sorted((e["id"].split(":")[2], e["id"].split(":")[3],
                       e["polarity"], e["check"], e["statement"])
                      for e in es["expectations"])
    assert _bare(a) == _bare(b)
    # the digest binds the subject id, so it differs by shot; the identity set
    # (kind+hash+polarity+check+statement) is what key order must not touch.


def test_unicode_nfc_and_nfd_yield_the_same_id(tmp_project, add_shot):
    nfc = unicodedata.normalize("NFC", "café 出现")
    nfd = unicodedata.normalize("NFD", "café 出现")
    assert nfc != nfd  # different bytes...

    add_shot(tmp_project, "S001", quality={"must_show": [nfc]})
    add_shot(tmp_project, "S002", quality={"must_show": [nfd]})
    e1 = compile_expectations(tmp_project, "S001")["expectations"][0]
    e2 = compile_expectations(tmp_project, "S002")["expectations"][0]
    # ...but the same id suffix (identity is NFC-normalized)
    assert e1["id"].rsplit(":", 1)[1] == e2["id"].rsplit(":", 1)[1]
    # and the stored statement stays verbatim (each keeps its own bytes)
    assert e1["statement"] == nfc
    assert e2["statement"] == nfd


def test_source_text_change_moves_digest(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞"]})
    before = compile_expectations(tmp_project, "S001")["digest"]
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("quality", {}).__setitem__("must_show", ["蓝色雨伞"]),
    )
    after = compile_expectations(tmp_project, "S001")["digest"]
    assert before != after


# ------------------------------------- ruling #6: spec_hash vs digest asymmetry


def test_must_show_edit_moves_both_spec_hash_and_digest(tmp_project, add_shot):
    """quality IS in spec_payload, so editing must_show moves BOTH the spec_hash
    and the expectation digest (binding steps 5 and 6 both fire)."""
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞"]})
    es0 = compile_expectations(tmp_project, "S001")
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("quality", {}).__setitem__("must_show", ["绿色雨伞"]),
    )
    es1 = compile_expectations(tmp_project, "S001")
    assert es0["spec_hash"] != es1["spec_hash"]
    assert es0["digest"] != es1["digest"]


def test_lock_edit_moves_digest_only_not_spec_hash(tmp_project, add_shot):
    """continuity is NOT in spec_payload, so editing locks moves ONLY the
    expectation digest — binding step 6 must be able to stale evidence with the
    spec_hash (step 5) unchanged. This is the load-bearing asymmetry."""
    add_shot(tmp_project, "S001", continuity={"locks": ["prop:coin"]})
    es0 = compile_expectations(tmp_project, "S001")
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("continuity", {}).__setitem__("locks", ["prop:watch"]),
    )
    es1 = compile_expectations(tmp_project, "S001")
    assert es0["spec_hash"] == es1["spec_hash"]   # spec_hash did NOT move
    assert es0["digest"] != es1["digest"]         # but the digest did


def test_two_calls_deep_equal(tmp_project, add_shot):
    add_shot(tmp_project, "S001",
             quality={"must_show": ["A"], "avoid": ["B"]},
             continuity={"locks": ["character:linxia"]})
    assert compile_expectations(tmp_project, "S001") == compile_expectations(tmp_project, "S001")


def test_read_only_compile_does_not_touch_sources(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"must_show": ["红色雨伞"]})
    shot_file = tmp_project.root / "shots" / "S001.yaml"
    before = shot_file.read_bytes()
    compile_expectations(tmp_project, "S001")
    assert shot_file.read_bytes() == before  # compile never writes back
