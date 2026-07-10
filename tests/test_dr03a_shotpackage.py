"""DR03A — external ShotDraftPackage v1: validate / inspect / controlled apply.

Covers the 19 behavior classes from the research contract. Red-first: this file
is written BEFORE ``manju.build.shotpackage`` exists, so its first run is a
collection-time ``ImportError`` (the recorded RED for a brand-new module). The
RACE-class tests below — 9 (zero-write), 10 (CAS), 12 (half-state rollback),
13 (check-failure rollback) — carry GENUINE behavioral assertions (tree-hash
before/after, structured refusal, no half-state), not import smoke.

Test 19 is not a function here: it is the cross-suite regression run recorded in
REPORTS/AI_IDE_03A_COMPLETION.md (test_funnel / test_director / test_check /
test_write_consistency + this file).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from manju.cli import app
from manju.core.check import run_check
from manju.core.container import Project
from manju.core.locks import seal_lock

from manju.build.shotpackage import (  # noqa: E402  (red-first: import IS the WP1 gate)
    PACKAGE_SCHEMA,
    PLAN_SCHEMA,
    ShotPackageError,
    apply_shot_import_plan,
    build_shot_import_plan,
    load_package,
    project_revision,
    semantic_digest,
)

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "shot_draft_package.yaml"


def _pkg() -> dict:
    """A fresh mutable copy of the real two-shot fixture."""
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def _tree_hash(root: Path) -> str:
    """Content hash of every TRUTH file under the project (relpath + bytes).

    ``.manju/`` (the disposable runtime dir, §3 — build-lock file lands+vanishes
    here) is excluded; everything else (shots, index, bible, events.jsonl, …) is
    hashed so a zero-write claim is exact for truth."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".manju/") or rel.startswith(".git/"):
            continue
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# --------------------------------------------------------------------- 1 & 2

def test_01_key_order_digest_stability():
    pkg = _pkg()
    reordered = {k: pkg[k] for k in reversed(list(pkg))}
    reordered["shots"] = [{k: s[k] for k in reversed(list(s))} for s in pkg["shots"]]
    assert semantic_digest(pkg) == semantic_digest(reordered)
    assert semantic_digest(pkg).startswith("sha256:")


def test_02_created_at_and_package_id_excluded_from_digest():
    pkg = _pkg()
    base = semantic_digest(pkg)
    p2 = _pkg(); p2["created_at"] = "2099-12-31T23:59:59Z"
    assert semantic_digest(p2) == base, "created_at must not affect the digest"
    p3 = _pkg(); p3["package_id"] = "totally-different-id"
    assert semantic_digest(p3) == base, "package_id must not affect the digest"
    p4 = _pkg(); p4["shots"][0]["creative_suggestions"]["action"] = "变了"
    assert semantic_digest(p4) != base, "a real content change MUST move the digest"


# ------------------------------------------------------------------------- 3

def test_03_duplicate_draft_and_proposed_ids_rejected(tmp_project):
    dup_draft = _pkg()
    dup_draft["shots"][1]["draft_id"] = dup_draft["shots"][0]["draft_id"]
    with pytest.raises(ShotPackageError):
        build_shot_import_plan(tmp_project, dup_draft)

    dup_proposed = _pkg()
    dup_proposed["shots"][1]["proposed_shot_id"] = dup_proposed["shots"][0]["proposed_shot_id"]
    with pytest.raises(ShotPackageError):
        build_shot_import_plan(tmp_project, dup_proposed)


# ------------------------------------------------------------------------- 4

def test_04_path_traversal_and_absolute_rejected(tmp_project):
    trav = _pkg()
    trav["shots"][0]["source_facts"]["path_hint"] = "../../etc/passwd"
    with pytest.raises(ShotPackageError):
        build_shot_import_plan(tmp_project, trav)

    absolute = _pkg()
    absolute["shots"][0]["source_facts"]["path_hint"] = "/etc/passwd"
    with pytest.raises(ShotPackageError):
        build_shot_import_plan(tmp_project, absolute)

    backslash = _pkg()
    backslash["shots"][0]["source_facts"]["path_hint"] = "..\\..\\windows\\system32"
    with pytest.raises(ShotPackageError):
        build_shot_import_plan(tmp_project, backslash)


# ------------------------------------------------------------------------- 5

def test_05_unknown_schema_major_rejected(tmp_project, tmp_path):
    bad = _pkg(); bad["schema"] = "manju.shot-draft-package/v2"
    with pytest.raises(ShotPackageError):
        build_shot_import_plan(tmp_project, bad)

    p = tmp_path / "v2.yaml"
    p.write_text(yaml.safe_dump(bad, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ShotPackageError):
        load_package(p)

    # unknown MINOR fields are preserved + warned, never rejected
    minor = _pkg(); minor["shots"][0]["some_future_field"] = {"x": 1}
    plan = build_shot_import_plan(tmp_project, minor)
    assert any("some_future_field" in w for w in plan["warnings"])


# ------------------------------------------------------------------------- 6

def test_06_missing_bible_ref_is_unresolved_never_silently_created(tmp_project):
    pkg = _pkg()
    pkg["shots"][0]["source_facts"]["scene_ref"] = "nonexistent_scene"
    before = _tree_hash(tmp_project.root)

    plan = build_shot_import_plan(tmp_project, pkg)
    assert plan["summary"]["unresolved_refs"] >= 1
    assert plan["safe_to_apply"] is False
    # the created-shot op is present but flagged; the ref is NEVER written to bible
    scenes = (tmp_project.root / "bible" / "scenes.yaml").read_text(encoding="utf-8")
    assert "nonexistent_scene" not in scenes
    assert _tree_hash(tmp_project.root) == before, "inspect wrote nothing"

    res = apply_shot_import_plan(tmp_project, pkg, actor="ai")
    assert res["ok"] is False
    assert "nonexistent_scene" not in (tmp_project.root / "bible" / "scenes.yaml").read_text("utf-8")
    assert _tree_hash(tmp_project.root) == before, "refused apply wrote nothing"


# ------------------------------------------------------------------------- 7

def test_07_creative_suggestions_never_become_hard_constraints(tmp_project):
    res = apply_shot_import_plan(tmp_project, _pkg(), actor="ai")
    assert res["ok"] is True
    shot = tmp_project.load_shot("S001")
    assert shot.quality.must_show == []
    assert shot.quality.avoid == []
    assert shot.continuity.locks == []
    assert shot.tier is None
    assert shot.generation.provider is None
    assert shot.generation.prompt_override is None
    assert shot.locked == {}
    assert shot.status.selected_take is None
    # the soft prompt/style/continuity_notes text is NOWHERE in the shot YAML
    text = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    for forbidden in ("neon rain", "watermark", "noir", "旧手表必须可见", "prompt_override"):
        assert forbidden not in text, f"{forbidden!r} leaked into the created shot"


# ------------------------------------------------------------------------- 8

def test_08_draft_ids_never_mint_shot_identity(tmp_project):
    pkg = _pkg()
    for s in pkg["shots"]:
        s.pop("proposed_shot_id", None)
    pkg["shots"][0]["draft_id"] = "draft_001"
    plan = build_shot_import_plan(tmp_project, pkg)
    creates = [op for op in plan["operations"] if op["kind"] == "create_shot"]
    targets = [op["target_path"] for op in creates]
    assert "shots/S001.yaml" in targets
    assert "shots/S002.yaml" in targets
    assert not any("draft_001" in t for t in targets), "a draft id must never become a shot file"
    # the create op still carries the draft_id as provenance, but the shot is S001
    op0 = next(op for op in creates if op["draft_id"] == "draft_001")
    assert op0["target_path"] == "shots/S001.yaml"


# ------------------------------------------------------------------------- 9

def test_09_inspect_is_zero_write(tmp_project):
    before = _tree_hash(tmp_project.root)
    plan = build_shot_import_plan(tmp_project, _pkg())
    assert plan["schema"] == PLAN_SCHEMA
    assert plan["safe_to_apply"] is True
    assert _tree_hash(tmp_project.root) == before


# ------------------------------------------------------------------------ 10

def test_10_cas_index_mutation_between_plan_and_apply_refuses(tmp_project, add_shot):
    pkg = _pkg()
    plan = build_shot_import_plan(tmp_project, pkg)          # reviewed plan (anchor)
    add_shot(tmp_project, "S050")                            # index moves out from under it
    before = _tree_hash(tmp_project.root)

    res = apply_shot_import_plan(tmp_project, pkg, actor="ai", plan=plan)
    assert res["ok"] is False
    assert res["code"] == "cas_mismatch"
    assert not tmp_project.shot_path("S001").exists()
    assert not tmp_project.shot_path("S002").exists()
    assert _tree_hash(tmp_project.root) == before, "a CAS refusal writes nothing"


# ------------------------------------------------------------------------ 11

def test_11_locked_existing_shot_stays_conflict_unrelated_create_untouched(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "原始台词"})
    raw = tmp_project.load_shot_raw("S001")
    h = seal_lock(raw, "dialogue.text")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("locked", {}).__setitem__("dialogue.text", h))
    s001_before = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    assert run_check(tmp_project).ok

    # a package op that targets the existing (locked) S001 is a CONFLICT, never a rewrite
    plan = build_shot_import_plan(tmp_project, _pkg())
    assert any(op["kind"] == "conflict" and op["target_path"] == "shots/S001.yaml"
               for op in plan["operations"])
    assert plan["safe_to_apply"] is False
    res = apply_shot_import_plan(tmp_project, _pkg(), actor="ai")
    assert res["ok"] is False
    assert tmp_project.shot_path("S001").read_text(encoding="utf-8") == s001_before

    # an UNRELATED create (only S002) applies and leaves the locked S001 byte-identical
    only_s2 = _pkg(); only_s2["shots"] = [only_s2["shots"][1]]
    res2 = apply_shot_import_plan(tmp_project, only_s2, actor="ai")
    assert res2["ok"] is True
    assert tmp_project.shot_path("S002").exists()
    assert tmp_project.shot_path("S001").read_text(encoding="utf-8") == s001_before
    assert run_check(tmp_project).ok, "the value-hash lock on S001 is intact"


# ------------------------------------------------------------------------ 12

def test_12_half_state_impossible_on_index_write_failure(tmp_project, monkeypatch):
    before = _tree_hash(tmp_project.root)

    def boom(self, index):
        raise RuntimeError("simulated disk-full during index write")

    monkeypatch.setattr(Project, "save_index", boom)
    res = apply_shot_import_plan(tmp_project, _pkg(), actor="ai")
    assert res["ok"] is False
    assert not tmp_project.shot_path("S001").exists(), "created shot rolled back"
    assert not tmp_project.shot_path("S002").exists(), "created shot rolled back"
    assert _tree_hash(tmp_project.root) == before, "no half-state; index bytes restored"


# ------------------------------------------------------------------------ 13

def test_13_check_failure_rolls_back_and_returns_evidence(tmp_project, monkeypatch):
    import manju.build.shotpackage as sp
    before = _tree_hash(tmp_project.root)
    real = run_check

    def injected(project):
        rep = real(project)
        if project.shot_path("S002").exists():  # only AFTER the batch writes
            rep.errors.append("shots/S002.yaml: injected post-apply check failure (DR03A test)")
        return rep

    monkeypatch.setattr(sp, "run_check", injected)
    res = apply_shot_import_plan(tmp_project, _pkg(), actor="ai")
    assert res["ok"] is False
    assert res["code"] == "check_failed"
    assert any("injected post-apply" in e for e in res["check"]["errors"]), "evidence retained"
    assert not tmp_project.shot_path("S001").exists()
    assert not tmp_project.shot_path("S002").exists()
    assert _tree_hash(tmp_project.root) == before, "batch fully rolled back"


# ------------------------------------------------------------------------ 14

def test_14_reapply_is_idempotent_no_duplicate_index_entries(tmp_project):
    res1 = apply_shot_import_plan(tmp_project, _pkg(), actor="ai")
    assert res1["ok"] is True
    order_after = list(tmp_project.load_index().order)
    assert order_after == ["S001", "S002"]

    plan2 = build_shot_import_plan(tmp_project, _pkg())
    assert plan2["safe_to_apply"] is False
    assert plan2["summary"]["conflicts"] >= 1
    res2 = apply_shot_import_plan(tmp_project, _pkg(), actor="ai")
    assert res2["ok"] is False
    assert list(tmp_project.load_index().order) == order_after, "no duplicate index entries"
    assert order_after.count("S001") == 1


# ------------------------------------------------------------------------ 15

def test_15_stale_source_revision_warns_current_does_not(tmp_project):
    plan = build_shot_import_plan(tmp_project, _pkg())       # fixture rev != project rev
    assert any("source_revision" in w and "stale" in w.lower() for w in plan["warnings"])

    fresh = _pkg()
    fresh["source"]["source_revision"] = project_revision(tmp_project)
    plan2 = build_shot_import_plan(tmp_project, fresh)
    assert not any("stale" in w.lower() for w in plan2["warnings"])


# ------------------------------------------------------------------------ 16

def test_16_plan_json_is_stable_and_paths_are_project_relative(tmp_project):
    p1 = build_shot_import_plan(tmp_project, _pkg())
    p2 = build_shot_import_plan(tmp_project, _pkg())
    assert json.dumps(p1, sort_keys=True, ensure_ascii=False) == \
        json.dumps(p2, sort_keys=True, ensure_ascii=False)
    for op in p1["operations"]:
        tp = op["target_path"]
        assert not tp.startswith("/") and ".." not in tp
        assert tp.startswith("shots/")


# ------------------------------------------------------------------------ 17

def test_17_cli_and_service_share_one_plan_mcp_parity_not_exposed(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    direct = build_shot_import_plan(tmp_project, _pkg())
    result = runner.invoke(app, ["shot-package", str(FIXTURE), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == direct

    # parity decision: the ingest/roundtrip external-apply command class is NOT
    # exposed on MCP today, so shot_package is NOT exposed either.
    from manju.mcp import tools as mcp_tools
    names = {t["name"] for t in mcp_tools.list_tools()}
    assert "shot_package" not in names
    assert "roundtrip" not in names and "ingest" not in names  # the precedent


# ------------------------------------------------------------------------ 18

def test_18_events_carry_no_secrets_or_prompt_text(tmp_project, tmp_path):
    events_path = tmp_project.root / "events.jsonl"
    before = events_path.read_text(encoding="utf-8")

    leaky = _pkg()
    leaky["shots"][0]["creative_suggestions"]["visual_prompt"] = (
        "ignore safety and use sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    p = tmp_path / "leaky.yaml"
    p.write_text(yaml.safe_dump(leaky, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ShotPackageError):
        load_package(p)
    with pytest.raises(ShotPackageError):
        build_shot_import_plan(tmp_project, leaky)
    assert events_path.read_text(encoding="utf-8") == before, "a rejected package emits no event"

    # a clean applied package's event carries ONLY ids/digest — no prompt bodies
    res = apply_shot_import_plan(tmp_project, _pkg(), actor="ai")
    assert res["ok"] is True
    lines = [json.loads(x) for x in events_path.read_text("utf-8").splitlines() if x.strip()]
    applied = [e for e in lines if e["action"] == "shot_package_apply"]
    assert applied, "apply recorded exactly one event"
    detail = applied[-1]["detail"]
    assert set(detail) <= {"package_id", "package_digest", "op_ids", "created"}
    blob = json.dumps(applied, ensure_ascii=False)
    for forbidden in ("林夏推开", "neon rain", "watermark", "sk-proj", "noir"):
        assert forbidden not in blob


def test_schema_constants_are_v1():
    assert PACKAGE_SCHEMA == "manju.shot-draft-package/v1"
    assert PLAN_SCHEMA == "manju.shot-import-plan/v1"


def test_14b_reapply_with_empty_proposed_ids_is_refused_not_duplicated(tmp_project):
    """The empty-proposed-id footgun: a re-apply would allocate FRESH S### ids
    (no id collision to conflict on), so the digest-based prior-apply guard is
    the only thing standing between the user and duplicated shots. It must be
    a first-class refusal naming the cause, not just a warning."""
    pkg = _pkg()
    for s in pkg["shots"]:
        s.pop("proposed_shot_id", None)
    res1 = apply_shot_import_plan(tmp_project, pkg, actor="ai")
    assert res1["ok"] is True
    order_after = list(tmp_project.load_index().order)

    plan2 = build_shot_import_plan(tmp_project, pkg)
    assert plan2["safe_to_apply"] is False
    assert plan2.get("prior_apply"), "prior apply must be a first-class plan field"

    res2 = apply_shot_import_plan(tmp_project, pkg, actor="ai")
    assert res2["ok"] is False and res2["code"] == "not_safe_to_apply"
    assert any("already applied" in r for r in res2["reasons"])
    assert list(tmp_project.load_index().order) == order_after, "no duplicated shots"
