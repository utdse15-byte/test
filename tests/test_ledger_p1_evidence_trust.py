"""Ledger P1 — evidence trust: three tools that failed OPEN on their own
normal path (missing/corrupt evidence silently downgraded into "通过").

Each of the three refusals below is a README discipline, not a new feature:
"UNKNOWN is never guessed into PASS", and a partial write is never left behind.

- DELIVERY-P1-006: an artifact whose bytes cannot be proven (no sha256/size, or
  not a regular file) may not sit in a byte-proven state, and may not satisfy a
  required role.
- ROUNDTRIP-P1-002: a baseline that EXISTS but does not parse is a different
  state from "no baseline" — it refuses instead of degrading into no-baseline
  mode (which silently reads current truth as the pre-export order).
- SERIES-P1-002: ``Series.create`` is transactional — a blocked scaffold leaves
  ZERO residue and raises the structured error the CLI already renders as JSON.

Fake media only; no ffmpeg.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from manju.build import delivery as D
from manju.build import roundtrip as R
from manju.build.exportstatus import DeliverableRow, Freshness
from manju.cli import app
from manju.core.container import Project, ProjectError
from manju.core.series import SERIES_DIRS, SERIES_FILE, Series, SeriesError
from manju.core.yamlio import read_yaml, write_yaml

runner = CliRunner()


# ------------------------------------------------------------- shared helpers


def _set_profiles(project: Project, profiles: dict) -> None:
    data = read_yaml(project.root / "project.yaml") or {}
    data["delivery_profiles"] = profiles
    write_yaml(project.root / "project.yaml", data)


def _manual_captions(project: Project) -> str:
    """A captions.srt that ``exportstatus`` judges 上新 (manual mode: the human
    SRT is definitionally the truth that renders), so the manifest row reaches
    TECHNICALLY_VERIFIED and the fail-open shape is reachable at all."""
    rules = project.load_rules()
    rules.captions.mode = "manual"
    project.save_rules(rules)
    project.captions_dir.mkdir(parents=True, exist_ok=True)
    srt = project.captions_dir / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n这不可能。\n", encoding="utf-8")
    return project.relpath(srt)


def _srt_artifact(manifest: dict) -> dict:
    return next(a for a in manifest["artifacts"] if a["role"] == "CAPTIONS_SRT")


# ====================================================== DELIVERY-P1-006


def test_delivery_unhashable_artifact_is_not_technically_verified(
        tmp_project, add_shot, monkeypatch):
    """An 上新 SRT whose bytes cannot be hashed used to keep
    state=TECHNICALLY_VERIFIED + technical=VERIFIED with sha256/bytes null."""
    add_shot(tmp_project, "S001")
    _manual_captions(tmp_project)

    healthy = _srt_artifact(D.build_manifest(tmp_project, "master"))
    assert healthy["state"] == D.TECHNICALLY_VERIFIED
    assert healthy["sha256"] and healthy["bytes"]

    real_hash_file = D.hash_file

    def _unreadable(path, *args, **kwargs):
        if str(path).endswith("captions.srt"):
            raise OSError("simulated unreadable artifact")
        return real_hash_file(path, *args, **kwargs)

    monkeypatch.setattr(D, "hash_file", _unreadable)
    man = D.build_manifest(tmp_project, "master")
    srt = _srt_artifact(man)
    assert srt["sha256"] is None and srt["bytes"] is None
    assert srt["state"] == D.INVALID
    assert srt["verification"]["technical"] == "FAILED"
    assert "ARTIFACT_UNVERIFIABLE" in {d["code"] for d in man["diagnostics"]}
    assert any(d["code"] == "ARTIFACT_UNVERIFIABLE" and d["severity"] == "blocking"
               for d in man["diagnostics"])


def test_delivery_unhashable_required_role_is_not_satisfied(
        tmp_project, add_shot, monkeypatch):
    """The same row named in required_roles must fail readiness — required-role
    satisfaction used to read only the freshness-derived state."""
    add_shot(tmp_project, "S001")
    _manual_captions(tmp_project)
    _set_profiles(tmp_project, {"yt": {"variant_kind": "master",
                                       "required_roles": ["CAPTIONS_SRT"]}})
    ready = D.build_manifest(tmp_project, "yt")
    assert ready["release"]["required_roles_satisfied"] is True

    real_hash_file = D.hash_file

    def _unreadable(path, *args, **kwargs):
        if str(path).endswith("captions.srt"):
            raise OSError("simulated unreadable artifact")
        return real_hash_file(path, *args, **kwargs)

    monkeypatch.setattr(D, "hash_file", _unreadable)
    man = D.build_manifest(tmp_project, "yt")
    assert man["release"]["required_roles_satisfied"] is False
    assert man["release"]["delivery_state"]["technical_ready"] is False


def test_delivery_required_role_needs_byte_proof_not_only_state(tmp_project):
    """Service-layer unit: a hand-shaped artifact row in a byte-proven state but
    WITHOUT sha256/bytes never satisfies a required role."""
    proven = {"artifact_id": "captions:srt", "role": "CAPTIONS_SRT",
              "path": "captions/captions.srt", "state": D.TECHNICALLY_VERIFIED,
              "sha256": "sha256:" + "0" * 64, "bytes": 48}
    unproven = {**proven, "sha256": None, "bytes": None}
    ok = D._release_section(tmp_project, [], [proven], ["CAPTIONS_SRT"],
                            D.MASTER, False)
    bad = D._release_section(tmp_project, [], [unproven], ["CAPTIONS_SRT"],
                             D.MASTER, False)
    assert ok["required_roles_satisfied"] is True
    assert bad["required_roles_satisfied"] is False
    assert bad["delivery_state"]["technical_ready"] is False


def test_delivery_non_regular_file_artifact_is_unverifiable(tmp_project):
    """A declared artifact path that is a DIRECTORY (or any non-regular file)
    carries no byte identity — it downgrades instead of riding as verified."""
    d = tmp_project.captions_dir / "captions.srt"
    d.parent.mkdir(parents=True, exist_ok=True)
    d.mkdir()
    row = DeliverableRow("srt", "SRT 字幕 外挂", tmp_project.relpath(d),
                         Freshness.UP_TO_DATE, "test row")
    art, diags = D._artifact_from_row(tmp_project, row)
    assert art["state"] == D.INVALID
    assert art["sha256"] is None and art["bytes"] is None
    assert [d_["code"] for d_ in diags] == ["ARTIFACT_UNVERIFIABLE"]


def test_delivery_healthy_master_manifest_keeps_no_new_diagnostics(
        tmp_project, add_shot):
    """Green-path guard: a project whose artifacts all hash cleanly gains no
    ARTIFACT_UNVERIFIABLE row (the refusal never fires on the normal path)."""
    add_shot(tmp_project, "S001")
    _manual_captions(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    assert "ARTIFACT_UNVERIFIABLE" not in {d["code"] for d in man["diagnostics"]}


# ====================================================== ROUNDTRIP-P1-002


def _jianying_doc(order: list[str]) -> dict:
    return {
        "fps": 24,
        "canvas_config": {"width": 1080, "height": 1920},
        "materials": {"videos": []},
        "tracks": [{"type": "video", "segments": [
            {"manju": {"shot": s, "take": "take_01", "kind": "video"}}
            for s in order
        ]}],
    }


@pytest.fixture()
def roundtrip_project(tmp_project, add_shot):
    """A project with a JianYing baseline on disk + an edited skeleton whose
    shot order was swapped in the NLE."""
    for shot in ("S001", "S002"):
        add_shot(tmp_project, shot)
    R.write_baseline(tmp_project, "jianying", "draft",
                     _jianying_doc(["S001", "S002"]), compiled_from="fp")
    draft_dir = tmp_project.exports_dir / "jianying" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    edited = draft_dir / "draft_content.json"
    edited.write_text(json.dumps(_jianying_doc(["S002", "S001"]), ensure_ascii=False),
                      encoding="utf-8")
    baseline = tmp_project.exports_dir / "jianying" / ".baseline" / "draft.json"
    assert baseline.exists()
    return tmp_project, edited, baseline


def test_roundtrip_plans_normally_against_a_readable_baseline(roundtrip_project):
    project, edited, _baseline = roundtrip_project
    plan = R.plan_roundtrip(project, edited)
    assert [r["class"] for r in plan["rows"]] == ["reorder"]
    assert plan["rows"][0]["state"] == "ok"


def test_roundtrip_corrupt_baseline_refuses_instead_of_planning(roundtrip_project):
    """A baseline that exists but does not parse must NOT degrade into
    no-baseline mode (which reads current truth as the pre-export order and
    hands back an appliable write-back)."""
    project, edited, baseline = roundtrip_project
    baseline.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(ProjectError) as exc:
        R.plan_roundtrip(project, edited)
    assert getattr(exc.value, "reason", None) == "roundtrip_baseline_corrupt"
    assert "roundtrip_baseline_corrupt" in str(exc.value)


def test_roundtrip_truncated_baseline_refuses(roundtrip_project):
    """The disk-truncation shape (a half-written JSON payload) refuses too."""
    project, edited, baseline = roundtrip_project
    text = baseline.read_text(encoding="utf-8")
    baseline.write_text(text[: len(text) // 2], encoding="utf-8")
    with pytest.raises(ProjectError) as exc:
        R.plan_roundtrip(project, edited)
    assert getattr(exc.value, "reason", None) == "roundtrip_baseline_corrupt"


def test_roundtrip_baseline_payload_of_wrong_shape_refuses(roundtrip_project):
    """Valid JSON that is not a baseline payload (a list, a scalar) is just as
    unusable as unparsable bytes — it refuses rather than diffing against {}."""
    project, edited, baseline = roundtrip_project
    baseline.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ProjectError) as exc:
        R.plan_roundtrip(project, edited)
    assert getattr(exc.value, "reason", None) == "roundtrip_baseline_corrupt"


def test_roundtrip_absent_baseline_keeps_the_legal_no_baseline_mode(
        roundtrip_project):
    """"File is not there" stays the EXISTING legal behaviour — only "there but
    unreadable" is the new refusal."""
    project, edited, baseline = roundtrip_project
    baseline.unlink()
    plan = R.plan_roundtrip(project, edited)
    assert plan["baseline"] is None
    assert plan["rows"]


def test_roundtrip_cli_corrupt_baseline_is_a_json_envelope(
        roundtrip_project, monkeypatch):
    """The CLI refusal is a stable --json error object, never a traceback."""
    project, edited, baseline = roundtrip_project
    baseline.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["roundtrip", str(edited), "--json"])
    assert res.exit_code == 1
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert "roundtrip_baseline_corrupt" in json.dumps(payload, ensure_ascii=False)


# ======================================================== SERIES-P1-002


def test_series_create_blocked_by_a_plain_file_leaves_zero_residue(tmp_path):
    """A plain file where a scaffold directory belongs used to raise a raw
    FileExistsError AFTER bible/ and episodes/ were already created."""
    root = tmp_path / "剧集"
    root.mkdir()
    (root / "script").write_text("这是一个普通文件,不是目录", encoding="utf-8")

    with pytest.raises(SeriesError) as exc:
        Series.create(root, git_init=False)
    assert "script" in str(exc.value)

    # zero residue: nothing but the pre-existing blocker survives
    assert sorted(p.name for p in root.iterdir()) == ["script"]
    assert not (root / SERIES_FILE).exists()
    for sub in SERIES_DIRS:
        if sub != "script":
            assert not (root / sub).exists()


def test_series_create_blocked_by_a_plain_events_log_dir(tmp_path):
    """The precheck covers every planned path, not only the first blocker."""
    root = tmp_path / "剧集2"
    root.mkdir()
    (root / "events.jsonl").mkdir()
    with pytest.raises(SeriesError):
        Series.create(root, git_init=False)
    assert sorted(p.name for p in root.iterdir()) == ["events.jsonl"]


def test_series_new_json_refuses_with_a_structured_envelope(tmp_path, monkeypatch):
    """`manju series new <dir> --json` must print the JSON error envelope, not a
    Rich traceback (the CLI already catches SeriesError — the service layer just
    has to raise one)."""
    root = tmp_path / "剧集3"
    root.mkdir()
    (root / "script").write_text("blocker", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["series", "new", str(root), "--json"])
    assert res.exit_code == 1
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert payload["error"] and payload["code"]
    assert sorted(p.name for p in root.iterdir()) == ["script"]


def test_series_create_still_scaffolds_the_full_umbrella(tmp_path):
    """Green-path guard: the normal scaffold is unchanged."""
    root = tmp_path / "深夜信号"
    series = Series.create(root, name="深夜信号", git_init=False)
    assert (series.root / SERIES_FILE).exists()
    assert (series.root / "events.jsonl").exists()
    for sub in SERIES_DIRS:
        assert (series.root / sub).is_dir()
    assert series.load_config().name == "深夜信号"


def test_series_create_over_an_existing_series_still_refuses(tmp_path):
    root = tmp_path / "重复"
    Series.create(root, git_init=False)
    with pytest.raises(SeriesError):
        Series.create(root, git_init=False)
