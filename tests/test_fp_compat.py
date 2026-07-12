"""F0 Contract Governance — old-project fixture corpus (roadmap §4.5).

Small, hand-written, synthetic OLD-SHAPE documents (never copied media) under
tests/fixtures/compat/. Every release must be able to:

  - READ each old document via its CURRENT real loader, without an exception;
  - NOT silently rewrite it (sha256 before == after — a loader that normalized
    bytes on read would break lock hashes / content keys);
  - REJECT a torn/half-written file structurally (a clean error, never a crash
    and never a silent partial accept);
  - REFUSE an unknown FUTURE major (manju.qc.verdict/v99) at intake, and never
    surface it as valid evidence on read.

Where a doc type has no read-back loader (the delivery manifest is a pure
derivation — reading a materialized manifest as input was removed as a
violation), the test pins what the consuming code actually does: the manifest is
self-describing and provably inert.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from manju.build import delivery
from manju.core.container import Project
from manju.qc import agent_review

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compat"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def old_project(tmp_path: Path) -> tuple[Project, dict[str, Path]]:
    """Assemble one coherent old-shape project from the compat corpus and return
    (project, {name: on-disk path the loader will read})."""
    root = tmp_path / "old.manju"
    for sub in ("shots", "timeline", "media/gen/S001", "reports"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    paths = {
        "project": root / "project.yaml",
        "shot": root / "shots" / "S001.yaml",
        "timeline": root / "timeline" / "timeline.json",
        "take": root / "media" / "gen" / "S001" / "take_01.yaml",
        "verdict": root / "reports" / "qc_agent.jsonl",
        "delivery": root / "reports" / "old.delivery-manifest.json",
    }
    shutil.copy(FIXTURES / "project.yaml", paths["project"])
    shutil.copy(FIXTURES / "shot.yaml", paths["shot"])
    shutil.copy(FIXTURES / "timeline.json", paths["timeline"])
    shutil.copy(FIXTURES / "take_sidecar.yaml", paths["take"])
    shutil.copy(FIXTURES / "verdict_v1.jsonl", paths["verdict"])
    shutil.copy(FIXTURES / "delivery_manifest.json", paths["delivery"])

    return Project(root), paths


# ---------------------------------------------- read via real loader, no rewrite


def test_project_yaml_loads_without_rewrite(old_project):
    project, paths = old_project
    before = _sha256(paths["project"])
    config = project.load_config()
    assert config.name == "旧项目雨夜"
    assert config.fps == 24  # the pre-migration int shape still loads
    assert _sha256(paths["project"]) == before, "load_config silently rewrote project.yaml"


def test_shot_yaml_loads_without_rewrite(old_project):
    project, paths = old_project
    before = _sha256(paths["shot"])
    shot = project.load_shot("S001")
    assert shot.id == "S001"                   # id defaulted from filename
    assert shot.scene == "convenience_store"
    assert _sha256(paths["shot"]) == before, "load_shot silently rewrote the shot file"


def test_timeline_json_loads_without_rewrite(old_project):
    project, paths = old_project
    before = _sha256(paths["timeline"])
    timeline = project.load_timeline()
    assert timeline is not None
    assert timeline.fps == 24
    assert len(timeline.tracks.video) == 1
    assert timeline.tracks.video[0].shot == "S001"
    assert _sha256(paths["timeline"]) == before, "load_timeline silently rewrote timeline.json"


def test_take_sidecar_loads_without_rewrite(old_project):
    project, paths = old_project
    before = _sha256(paths["take"])
    takes = project.takes("S001")
    assert len(takes) == 1
    sidecar = takes[0].sidecar
    assert sidecar.provider == "manual"
    assert sidecar.spec_version is None      # old take: read as v1 forever (§4.3)
    assert takes[0].error is None
    assert _sha256(paths["take"]) == before, "Project.takes silently rewrote the sidecar"


def test_verdict_v1_reads_as_legacy_without_rewrite(old_project):
    project, paths = old_project
    before = _sha256(paths["verdict"])

    records, malformed = agent_review._read_records(project)
    assert malformed == 0
    assert len(records) == 1
    assert records[0]["shot"] == "S001"
    assert "schema" not in records[0]        # genuinely legacy (schema-less)

    # the public reader folds it into QC items without raising.
    assert isinstance(agent_review.agent_verdict_items(project), list)
    assert _sha256(paths["verdict"]) == before, "the verdict reader silently rewrote the log"


def test_delivery_manifest_is_readable_recognized_and_inert(old_project):
    """No read-back loader exists (a materialized manifest is provably inert), so
    the honest pin: it parses, its schema is the CURRENT recognized major, and it
    is never rewritten (there is no loader that could)."""
    _, paths = old_project
    before = _sha256(paths["delivery"])
    manifest = json.loads(paths["delivery"].read_text(encoding="utf-8"))
    assert manifest["schema"] == delivery.SCHEMA == "manju.delivery-manifest/v1"
    assert _sha256(paths["delivery"]) == before


def test_no_fixture_bytes_changed_after_all_loads(old_project):
    """Belt-and-braces: loading the whole corpus perturbs nothing on disk."""
    project, paths = old_project
    before = {k: _sha256(p) for k, p in paths.items()}
    project.load_config()
    project.load_shot("S001")
    project.load_timeline()
    project.takes("S001")
    agent_review._read_records(project)
    after = {k: _sha256(p) for k, p in paths.items()}
    assert before == after


# --------------------------------------------------- torn / half-written reject


def test_torn_json_is_rejected_structurally():
    """A half-written JSON is a clean parse error — never a silent partial
    accept, never a harness crash."""
    torn = (FIXTURES / "torn.json").read_text(encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        json.loads(torn)


def test_torn_line_in_log_is_skipped_not_crashed(old_project, tmp_path):
    """A torn line appended to an append-only log (interrupted write, §15.8) is
    skipped + counted by the reader, which still returns the valid records and
    never raises."""
    project, paths = old_project
    torn = (FIXTURES / "torn.json").read_text(encoding="utf-8").strip()
    # one good legacy line already present; append the torn fragment as a line.
    with open(paths["verdict"], "a", encoding="utf-8") as f:
        f.write(torn + "\n")
    records, malformed = agent_review._read_records(project)
    assert malformed == 1              # the torn line was rejected + counted
    assert len(records) == 1           # the valid legacy record still surfaced
    assert records[0]["shot"] == "S001"


# ------------------------------------------------- unknown future major refused


def test_unknown_future_major_verdict_is_refused_at_intake(old_project):
    """manju.qc.verdict/v99 is REFUSED by the intake — never silently accepted
    (§4.5 未知 major 拒绝). record_verdicts routes any manju.qc.verdict/* payload
    to the v2 intake, which rejects a non-v2 major."""
    project, _ = old_project
    payload = json.loads((FIXTURES / "verdict_future_v99.json").read_text(encoding="utf-8"))
    with pytest.raises(agent_review.VerdictError) as excinfo:
        agent_review.record_verdicts(project, payload)
    assert "manju.qc.verdict/v99" in str(excinfo.value)


def test_unknown_future_major_is_never_surfaced_as_evidence(old_project):
    """Even if a v99 record sits on disk, NEITHER reader accepts it: the v2
    reader keeps only exact-v2, and the legacy reader skips any manju.qc.* line.
    It is never silently promoted to valid evidence."""
    project, paths = old_project
    v99 = json.dumps(
        json.loads((FIXTURES / "verdict_future_v99.json").read_text(encoding="utf-8")),
        ensure_ascii=False,
    )
    with open(paths["verdict"], "a", encoding="utf-8") as f:
        f.write(v99 + "\n")

    v2_records, _ = agent_review.read_v2_records(project)
    assert all(r.get("schema") != "manju.qc.verdict/v99" for r in v2_records)

    legacy, _ = agent_review._read_records(project)
    assert all(r.get("schema", "") != "manju.qc.verdict/v99" for r in legacy)
    # the genuinely-legacy v1 record is still the only accepted one.
    assert [r["shot"] for r in legacy] == ["S001"]
