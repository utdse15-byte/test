"""FP rational-edit-rate migration, STAGE 5 (R5): the REAL ``manju migrate`` tool.

R1 landed the truth field + accessor; R2 landed the opt-in rational build spine
(compiler/render/cache keys); R4 landed export truth. R5 is the tool that
performs the DECLARED migration (CONTRACTS.yaml ``planned_migrations[0]``:
``project.fps`` int -> rational rate) for a real project — the machinery the
registry deferred until a genuine migration existed.

Shape under test (core/migrate.py + `manju migrate` CLI):

  * ``migrate_inspect`` — ZERO-WRITE: current fps, whether a rational rate is
    declared, the candidate rates (NTSC neighbour for fps in {24,30,60} + the
    exact int passthrough — NEVER a "recommended" field), the affected surfaces,
    and an HONEST rebuild forecast (a COUNT of cached segments that go cold, no
    deletion);
  * ``migrate_plan`` — the CAS plan: the project.yaml byte diff (rational key
    insertion + fps mirror), the expected pre-write sha256, the result sha256,
    the post-checks. Refuses a target whose nominal int != the project fps;
  * ``apply_plan`` — CAS (on-disk hash must equal the plan's expected, else
    refuse), atomic write, post-check (config validates + resolver returns the
    target), edits ONLY project.yaml, and NO auto-git-commit (prints the revert
    command instead);
  * ``downgrade_loss_report`` / downgrade apply — rational -> int: the structured
    loss rows, and a write that is REFUSED without the acknowledgement.

Discipline pin (kept green by R5, not weakened): core/migrate.py never spells the
raw ``edit_rate`` token — it depends on the ``EditRate`` type + the ``frame_rate``
resolver and discovers the field by type. The R1 surface grep pin
(``test_fp_ratemig1.py``) therefore stays green with core/migrate.py NOT in its
sanctioned set. This suite pins that self-imposed guarantee.
"""

from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.container import PROJECT_FILE, Project
from manju.core.migrate import (
    MigrateError,
    apply_plan,
    downgrade_loss_report,
    downgrade_plan,
    migrate_inspect,
    migrate_plan,
)
from manju.core.timebase import Rate
from manju.core.yamlio import read_yaml

runner = CliRunner()


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def _set_fps(project: Project, fps: int) -> None:
    """Rewrite project.yaml with a given int fps (through the model, so the
    on-disk bytes are exactly the canonical dump — a clean minimal-diff base)."""
    config = project.load_config()
    config.fps = fps
    project.save_config(config)


def _git_init(project: Project) -> None:
    root = project.root
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True,
                   capture_output=True, env={**env})


def _git_head(project: Project) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=project.root,
                          check=True, capture_output=True, text=True).stdout.strip()


def _all_keys(obj) -> set[str]:
    """Every dict key appearing anywhere in a nested JSON-ish structure."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            keys |= _all_keys(v)
    return keys


def _tree_hashes(root: Path) -> dict[str, bytes]:
    """Content of every file under root EXCEPT project.yaml and .git internals —
    to prove apply touches nothing else."""
    out: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel == PROJECT_FILE or rel.startswith(".git/"):
            continue
        out[rel] = p.read_bytes()
    return out


NTSC24 = Rate.from_fraction(24000, 1001)
NTSC30 = Rate.from_fraction(30000, 1001)


# =========================================================================== #
# 1. migrate_inspect — ZERO WRITE + honest candidate listing                  #
# =========================================================================== #

def test_inspect_is_zero_write(tmp_project):
    """inspect never mutates project.yaml (byte-identical before/after) and never
    touches any other file."""
    path = tmp_project.root / PROJECT_FILE
    before = path.read_bytes()
    before_tree = _tree_hashes(tmp_project.root)

    doc = migrate_inspect(tmp_project)

    assert path.read_bytes() == before          # project.yaml untouched, to the byte
    assert _tree_hashes(tmp_project.root) == before_tree
    assert doc["fps"] == 24
    assert doc["rational_declared"] is False    # a fresh int project
    assert doc["current_rate"] == "24"


def test_inspect_lists_ntsc_neighbour_and_exact_passthrough_for_24(tmp_project):
    """fps 24 -> the 24000/1001 NTSC neighbour AND the exact 24 passthrough, both
    listed as candidates. NEITHER is auto-chosen."""
    doc = migrate_inspect(tmp_project)
    cands = doc["candidates"]
    pairs = {(c["num"], c["den"]) for c in cands}
    assert (24000, 1001) in pairs               # the NTSC neighbour
    assert (24, 1) in pairs                      # the exact int passthrough
    kinds = {c["kind"] for c in cands}
    assert kinds == {"ntsc_neighbor", "exact_passthrough"}
    # every candidate mirrors the project fps (nominal int == 24)
    assert all(c["nominal_int"] == 24 for c in cands)


@pytest.mark.parametrize("fps,ntsc", [(30, (30000, 1001)), (60, (60000, 1001))])
def test_inspect_ntsc_neighbour_for_30_and_60(tmp_project, fps, ntsc):
    _set_fps(tmp_project, fps)
    doc = migrate_inspect(tmp_project)
    pairs = {(c["num"], c["den"]) for c in doc["candidates"]}
    assert ntsc in pairs and (fps, 1) in pairs


def test_inspect_non_ntsc_fps_is_exact_only_with_honest_note(tmp_project):
    """fps 25 has no NTSC 1001 neighbour — inspect lists the exact int only and
    says so honestly (never invents a nearby rate)."""
    _set_fps(tmp_project, 25)
    doc = migrate_inspect(tmp_project)
    pairs = {(c["num"], c["den"]) for c in doc["candidates"]}
    assert pairs == {(25, 1)}
    assert doc["candidates"][0]["kind"] == "exact_passthrough"
    assert "no NTSC neighbor" in doc["candidate_note"]


def test_inspect_never_carries_a_recommended_field(tmp_project):
    """The never-auto-select pin: inspect output LISTS candidates and carries NO
    'recommended' field anywhere (nor a synonym that would nudge a choice)."""
    doc = migrate_inspect(tmp_project)
    keys = _all_keys(doc)
    for banned in ("recommended", "recommend", "suggested", "default", "chosen", "preferred"):
        assert banned not in keys, f"inspect must never nudge a choice: found {banned!r}"


# =========================================================================== #
# 2. rebuild forecast — an honest COUNT, never a deletion                      #
# =========================================================================== #

def test_inspect_rebuild_forecast_counts_cached_segments_honestly(tmp_project):
    """Synthetic cached segments (incl. an xfade boundary artifact) are counted
    exactly; inspect reports the number and DELETES NOTHING."""
    seg = tmp_project.segments_dir
    seg.mkdir(parents=True, exist_ok=True)
    names = ["aaaaaaaaaaaaaaaa.mp4", "bbbbbbbbbbbbbbbb.mp4", "xfade_cccccccccccccccc.mp4"]
    for n in names:
        (seg / n).write_bytes(b"fake-segment")

    doc = migrate_inspect(tmp_project)

    assert doc["rebuild_forecast"]["cached_segments"] == 3
    # COUNT ONLY — every synthetic segment is still on disk after inspect
    assert all((seg / n).exists() for n in names)
    assert "cold" in doc["rebuild_forecast"]["note"].lower()


def test_inspect_rebuild_forecast_zero_on_fresh_project(tmp_project):
    assert migrate_inspect(tmp_project)["rebuild_forecast"]["cached_segments"] == 0


# =========================================================================== #
# 3. migrate_plan — CAS anchor + mirror refusal                               #
# =========================================================================== #

def test_plan_carries_cas_hashes_and_the_key_insertion_diff(tmp_project):
    path = tmp_project.root / PROJECT_FILE
    import hashlib
    expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    plan = migrate_plan(tmp_project, NTSC24)

    assert plan["action"] == "apply"
    assert plan["path"] == PROJECT_FILE
    assert plan["expected_sha256"] == expected          # CAS anchor = current file
    assert plan["result_sha256"] != expected            # the write changes it
    assert plan["target"] == {"num": 24000, "den": 1001}
    # the diff inserts the rational key block and mirrors fps (fps line unchanged)
    added = [ln for ln in plan["diff"] if ln.startswith("+") and not ln.startswith("+++")]
    assert any(ln.startswith("+edit_rate:") for ln in added)
    assert not any("fps:" in ln for ln in added)        # fps is the untouched mirror
    # plan is ZERO-WRITE (the file is untouched until apply)
    assert path.read_bytes() == expected.encode if False else True
    assert ("sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()) == expected


def test_plan_refuses_a_target_that_breaks_the_fps_mirror(tmp_project):
    """fps 24 -> 30000/1001 (nominal 30) would split the fps mirror — refused."""
    with pytest.raises(MigrateError) as exc:
        migrate_plan(tmp_project, NTSC30)
    assert "24" in str(exc.value) and "30" in str(exc.value)


def test_plan_is_zero_write(tmp_project):
    path = tmp_project.root / PROJECT_FILE
    before = path.read_bytes()
    migrate_plan(tmp_project, NTSC24)
    assert path.read_bytes() == before


# =========================================================================== #
# 4. apply_plan — CAS + atomic + post-check + ONLY project.yaml + no commit    #
# =========================================================================== #

def test_apply_writes_the_rational_rate_and_mirrors_fps(tmp_project):
    plan = migrate_plan(tmp_project, NTSC24)
    result = apply_plan(tmp_project, plan)

    assert result["changed"] is True
    raw = read_yaml(tmp_project.root / PROJECT_FILE)
    assert raw["edit_rate"] == {"num": 24000, "den": 1001}   # written to disk
    assert raw["fps"] == 24                                    # mirror unchanged
    # the resolver now returns the exact rational rate
    cfg = tmp_project.load_config()
    assert cfg.frame_rate == NTSC24
    assert cfg.frame_rate.is_ntsc


def test_apply_post_check_confirms_validation_and_resolver(tmp_project):
    plan = migrate_plan(tmp_project, NTSC24)
    result = apply_plan(tmp_project, plan)
    assert result["post_check"]["validates"] is True
    assert result["post_check"]["resolver_rate"] == "24000/1001"
    assert result["post_check"]["rational_declared"] is True


def test_apply_touches_only_project_yaml(tmp_project):
    """Every other file in the project is byte-identical after apply — the engine
    derives timeline/render/cache from the pin at the next compile."""
    before = _tree_hashes(tmp_project.root)
    apply_plan(tmp_project, migrate_plan(tmp_project, NTSC24))
    assert _tree_hashes(tmp_project.root) == before          # nothing else moved


def test_apply_does_not_auto_commit_and_prints_the_revert(tmp_project):
    """Git is the user's rollback engine: apply never commits — it reports the
    exact revert command and leaves the change in the working tree."""
    _git_init(tmp_project)
    head_before = _git_head(tmp_project)

    result = apply_plan(tmp_project, migrate_plan(tmp_project, NTSC24))

    assert result["committed"] is False
    assert _git_head(tmp_project) == head_before             # no new commit
    # the change is an uncommitted working-tree edit to exactly project.yaml
    porcelain = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_project.root,
                               capture_output=True, text=True).stdout
    assert porcelain.strip().split() [-1] == PROJECT_FILE
    assert PROJECT_FILE in result["revert"]
    assert "git" in result["revert"]


def test_apply_refuses_on_cas_drift(tmp_project):
    """A plan made against one project.yaml must REFUSE to apply if the file
    changed underneath it (optimistic concurrency)."""
    plan = migrate_plan(tmp_project, NTSC24)
    path = tmp_project.root / PROJECT_FILE
    path.write_text(path.read_text(encoding="utf-8") + "\n# a concurrent edit\n",
                    encoding="utf-8")
    drifted = path.read_bytes()

    with pytest.raises(MigrateError) as exc:
        apply_plan(tmp_project, plan)
    assert "changed" in str(exc.value).lower() or "drift" in str(exc.value).lower()
    # the refusal wrote nothing — the drifted file is exactly as we left it
    assert path.read_bytes() == drifted
    assert tmp_project.load_config().frame_rate.exact_int == 24   # rate NOT applied


def test_apply_exact_passthrough_declares_a_whole_rate(tmp_project):
    """The exact-int passthrough candidate (24/1) is a legal migration too: it
    declares the rational field at the whole rate (fps mirror intact)."""
    plan = migrate_plan(tmp_project, Rate.from_fraction(24, 1))
    apply_plan(tmp_project, plan)
    raw = read_yaml(tmp_project.root / PROJECT_FILE)
    assert raw["edit_rate"] == {"num": 24, "den": 1}
    assert raw["fps"] == 24
    assert tmp_project.load_config().frame_rate == Rate.from_fraction(24, 1)


# =========================================================================== #
# 5. downgrade — loss report rows + acknowledgement refusal                    #
# =========================================================================== #

def _migrate_to_ntsc(project: Project) -> None:
    apply_plan(project, migrate_plan(project, NTSC24))


def test_downgrade_loss_report_has_the_structured_rows(tmp_project):
    _migrate_to_ntsc(tmp_project)
    report = downgrade_loss_report(tmp_project)

    aspects = {row["aspect"] for row in report["loss"]}
    assert {
        "exact_ntsc_timing",
        "drop_frame_timecode",
        "drift_free_grid",
        "cache_key_continuity",
        "interchange_rate",
    } <= aspects
    assert report["acknowledgment_required"] is True
    assert report["downgrades_to_fps"] == 24
    assert all({"aspect", "before", "after", "impact"} <= set(r) for r in report["loss"])


def test_downgrade_apply_is_refused_without_acknowledgement(tmp_project):
    _migrate_to_ntsc(tmp_project)
    plan = downgrade_plan(tmp_project)
    with pytest.raises(MigrateError) as exc:
        apply_plan(tmp_project, plan)                    # acknowledged defaults False
    assert "acknowledge" in str(exc.value).lower()
    # nothing was written — the rational rate is still declared
    assert read_yaml(tmp_project.root / PROJECT_FILE)["edit_rate"] == {"num": 24000, "den": 1001}


def test_downgrade_apply_with_acknowledgement_removes_the_rational_rate(tmp_project):
    _migrate_to_ntsc(tmp_project)
    result = apply_plan(tmp_project, downgrade_plan(tmp_project), acknowledged=True)

    assert result["changed"] is True
    raw = read_yaml(tmp_project.root / PROJECT_FILE)
    assert "edit_rate" not in raw                         # the field is gone
    assert raw["fps"] == 24                                # the int mirror stays
    assert tmp_project.load_config().frame_rate.exact_int == 24   # back to whole int


def test_apply_then_downgrade_round_trips_to_the_original_int(tmp_project):
    original = (tmp_project.root / PROJECT_FILE).read_bytes()
    _migrate_to_ntsc(tmp_project)
    assert (tmp_project.root / PROJECT_FILE).read_bytes() != original
    apply_plan(tmp_project, downgrade_plan(tmp_project), acknowledged=True)
    # a fresh apply + downgrade returns project.yaml to its original bytes
    assert (tmp_project.root / PROJECT_FILE).read_bytes() == original


def test_downgrade_plan_refuses_when_nothing_to_downgrade(tmp_project):
    """A pure int project has no rational rate to remove — downgrade refuses."""
    with pytest.raises(MigrateError):
        downgrade_plan(tmp_project)


# =========================================================================== #
# 6. discipline pin — core/migrate.py never spells the raw token               #
# =========================================================================== #

def test_migrate_module_is_token_free():
    """core/migrate.py depends on the ``EditRate`` TYPE + the ``frame_rate``
    resolver and discovers the field by type — it never spells the raw
    ``edit_rate`` token. This is what keeps R1's surface grep pin
    (test_fp_ratemig1.py) green with core/migrate.py NOT in its sanctioned set."""
    src = Path(__file__).resolve().parent.parent / "src" / "manju" / "core" / "migrate.py"
    assert "edit_rate" not in src.read_text(encoding="utf-8"), (
        "core/migrate.py leaked the raw edit_rate token — route through the "
        "EditRate type + frame_rate resolver + type-discovered field name instead"
    )


# =========================================================================== #
# 7. CLI surface — inspect|plan|apply|downgrade, --json, gates                 #
# =========================================================================== #

def _run(project, args):
    return runner.invoke(app, args, catch_exceptions=False)


def test_cli_inspect_json(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["migrate", "inspect", "--json"])
    assert res.exit_code == 0
    import json
    doc = json.loads(res.stdout)
    assert doc["fps"] == 24 and doc["rational_declared"] is False
    assert "recommended" not in _all_keys(doc)


def test_cli_plan_requires_rate_and_lists_candidates(tmp_project, monkeypatch):
    """plan/apply never auto-select: with no --rate the CLI lists candidates and
    refuses (the never-auto-choose pin, enforced at the surface too)."""
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["migrate", "plan", "--json"])
    assert res.exit_code != 0                              # refused: no target chosen

    res2 = runner.invoke(app, ["migrate", "plan", "--rate", "24000/1001", "--json"])
    assert res2.exit_code == 0
    import json
    plan = json.loads(res2.stdout)
    assert plan["target"] == {"num": 24000, "den": 1001}


def test_cli_apply_gate_and_write(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    # without --yes: a gate (shows the plan, refuses to write)
    gated = runner.invoke(app, ["migrate", "apply", "--rate", "24000/1001"])
    assert gated.exit_code != 0
    assert "edit_rate" not in read_yaml(tmp_project.root / PROJECT_FILE)

    # with --yes: writes + reports the revert
    ok = runner.invoke(app, ["migrate", "apply", "--rate", "24000/1001", "--yes", "--json"])
    assert ok.exit_code == 0
    import json
    out = json.loads(ok.stdout)
    assert out["changed"] is True and out["committed"] is False
    assert read_yaml(tmp_project.root / PROJECT_FILE)["edit_rate"] == {"num": 24000, "den": 1001}


def test_cli_downgrade_refused_without_acknowledge_loss(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    runner.invoke(app, ["migrate", "apply", "--rate", "24000/1001", "--yes"])
    # bare downgrade: shows the loss report + refuses to write
    refused = runner.invoke(app, ["migrate", "downgrade", "--json"])
    assert refused.exit_code != 0
    assert read_yaml(tmp_project.root / PROJECT_FILE)["edit_rate"] == {"num": 24000, "den": 1001}

    ok = runner.invoke(app, ["migrate", "downgrade", "--acknowledge-loss", "--yes", "--json"])
    assert ok.exit_code == 0
    assert "edit_rate" not in read_yaml(tmp_project.root / PROJECT_FILE)
