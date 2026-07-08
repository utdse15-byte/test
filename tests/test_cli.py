"""CLI surface tests (§11) — every read command supports ``--json``; the new
``propose`` / ``auto`` / ``serve-mcp`` commands and the run-ledger wiring.

These drive the real Typer app through ``typer.testing.CliRunner`` against a
freshly scaffolded project (``tests/conftest.py`` fixtures), cwd-anchored via
``monkeypatch.chdir``. No ffmpeg is needed except for the single, ffmpeg-gated
``redo`` case (real generation through the fallback chain). Kept well under 15s.
"""

from __future__ import annotations

import json
import shutil

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.events import tail_events

runner = CliRunner()

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """cwd-anchor a fresh project so ``_project()`` discovers it from cwd."""
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


# --------------------------------------------------------------- --json reads


def test_status_json_has_shots_total_and_run_log(in_project):
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "shots_total" in data
    assert data["shots_total"] == 0
    # §6/§8.3: the run-ledger snapshot is always present (best-effort).
    assert "run_log" in data
    assert set(data["run_log"]) >= {"runs", "total_cost", "currency"}
    assert data["run_log"]["runs"] == 0


def test_check_json_ok(in_project):
    result = runner.invoke(app, ["check", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True


def test_select_json_selects_take_and_logs_event(in_project, add_shot, make_take):
    add_shot(in_project, "S001")
    take = make_take(in_project, "S001", "sha256:whatever")

    result = runner.invoke(app, ["select", "S001", take.name, "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == {"shot": "S001", "take": take.name, "ok": True}

    # the collaboration log gained a select entry (§10)
    actions = [(e.get("actor"), e.get("action")) for e in tail_events(in_project.root, 50)]
    assert any(a == "select" for _, a in actions)


def test_select_refuses_traversal_shot_id_cleanly(in_project, tmp_path):
    """goal item 11: `manju select` cleanly refuses a shot id shaped like a
    path traversal — no take is ever written outside shots/media/gen, and the
    failure is a normal `_fail()` (nonzero exit, no Python traceback), not a
    crash — before this fix `shot_path` performed no validation at all."""
    result = runner.invoke(app, ["select", "../../evil", "take_01"])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "shot_id" in result.output
    # nothing escaped the project: no file named after the traversal attempt
    # exists anywhere near the project or its parent
    assert not any(p.name == "evil" for p in in_project.root.parent.rglob("evil*"))


def test_lock_json_returns_hash_and_tamper_fails_check(in_project, add_shot):
    add_shot(in_project, "S001")

    result = runner.invoke(app, ["lock", "S001", "dialogue.text", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["shot"] == "S001" and data["field"] == "dialogue.text"
    assert data["hash"].startswith("sha256:")

    # a healthy locked project passes check …
    assert runner.invoke(app, ["check"]).exit_code == 0

    # … tamper the sealed field on disk → the lock no longer verifies (§5)
    in_project.update_shot_raw(
        "S001", lambda d: d["dialogue"].__setitem__("text", "被篡改的台词。")
    )
    tampered = runner.invoke(app, ["check"])
    assert tampered.exit_code != 0


def test_import_json_copies_and_never_overwrites(in_project, tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fake-media-bytes")

    first = runner.invoke(app, ["import", str(src), "--json"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["imported"] == ["media/imports/clip.mp4"]

    # imports are sacred: a second import of the same name gets a _2 suffix (§3)
    second = runner.invoke(app, ["import", str(src), "--json"])
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["imported"] == ["media/imports/clip_2.mp4"]
    assert (in_project.imports_dir / "clip.mp4").exists()
    assert (in_project.imports_dir / "clip_2.mp4").exists()


def test_gc_json_reports_freed_bytes(in_project):
    result = runner.invoke(app, ["gc", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["freed_bytes"] >= 0
    assert data["hard"] is False


def test_gc_hard_removes_sidecar_too_no_ghost_takes(in_project, add_shot, make_take, monkeypatch):
    """round-W #35: `gc --hard` deleting an unselected take's media must also
    remove its sidecar — otherwise a media-less "ghost take" lingers and keeps
    showing up in listings/numbering/stats."""
    import typer

    from manju.core.spec import compute_spec_hash
    import manju.cli as cli_mod

    shot = add_shot(in_project, "S001")
    spec_hash = compute_spec_hash(shot, in_project.load_bible())
    selected = make_take(in_project, "S001", spec_hash)
    unselected = make_take(in_project, "S001", spec_hash)
    in_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", selected.name)
    )

    monkeypatch.setattr(cli_mod, "_interactive", lambda: True)
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)

    result = runner.invoke(app, ["gc", "--hard", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["hard"] is True
    assert data["sidecars_removed"] == 1

    # the unselected take's media AND sidecar are both gone — no ghost.
    assert not unselected.media_path.exists()
    assert not unselected.sidecar_path.exists()
    # the selected take is completely untouched.
    assert selected.media_path.exists() and selected.sidecar_path.exists()

    # default takes() (accounting callers) still enumerates 1 entry (selected);
    # the ghost sidecar is gone entirely now, not merely filtered.
    remaining = [t.name for t in in_project.takes("S001")]
    assert remaining == [selected.name]


def test_takes_skip_ghosts_filters_media_less_sidecar(in_project, add_shot, make_take):
    """Direct unit coverage of the `skip_ghosts` listing filter (round-W #35),
    independent of gc: a sidecar whose media vanished by any means (manual
    delete, a crash mid-write, a project from before this round's gc fix) is
    excluded from a `skip_ghosts=True` read, but still enumerated by default —
    accounting/staleness callers need to know about it."""
    from manju.core.spec import compute_spec_hash

    shot = add_shot(in_project, "S001")
    spec_hash = compute_spec_hash(shot, in_project.load_bible())
    ghost = make_take(in_project, "S001", spec_hash)
    live = make_take(in_project, "S001", spec_hash)
    ghost.media_path.unlink()  # simulate a pre-existing ghost (no sidecar removal)

    assert {t.name for t in in_project.takes("S001")} == {ghost.name, live.name}
    assert {t.name for t in in_project.takes("S001", skip_ghosts=True)} == {live.name}


def test_doctor_json_has_checks_list(in_project):
    result = runner.invoke(app, ["doctor", "--json"])
    data = json.loads(result.output)
    assert isinstance(data["checks"], list) and data["checks"]
    assert "ok" in data
    ffmpeg = next(c for c in data["checks"] if c["name"] == "ffmpeg")
    # ffmpeg is installed in this environment (see module gate)
    assert ffmpeg["ok"] is _HAS_FFMPEG


def test_rebuild_index_json_reports_runs(in_project):
    result = runner.invoke(app, ["rebuild-index", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["runs"] >= 0
    assert data["pending_jobs"] >= 0
    assert "shots" in data


# --------------------------------------------------------------------- propose


def test_propose_creates_numbered_markdown(in_project):
    first = runner.invoke(app, ["propose", "第一个提案", "--body", "想改台词"])
    assert first.exit_code == 0, first.output
    props = sorted(in_project.proposals_dir.glob("*.md"))
    assert len(props) == 1 and props[0].name.startswith("0001_")
    text = props[0].read_text(encoding="utf-8")
    assert text == "# 第一个提案\n\n想改台词\n"

    second = runner.invoke(app, ["propose", "第二个提案", "--body", "再改一次", "--json"])
    assert second.exit_code == 0, second.output
    rel = json.loads(second.output)["path"]
    assert rel.startswith("proposals/0002_")
    names = sorted(p.name for p in in_project.proposals_dir.glob("*.md"))
    assert names[0].startswith("0001_") and names[1].startswith("0002_")


def test_propose_shares_counter_with_mcp_tool(in_project):
    """The CLI and the MCP `propose` tool draw from ONE numbering counter."""
    from manju.mcp import tools as mcp_tools

    # MCP first -> 0001, then CLI -> 0002 (same _next_proposal_number scheme)
    mcp_tools.call_tool(in_project, "propose", {"title": "MCP 提案", "body": "来自 MCP"})
    cli = runner.invoke(app, ["propose", "CLI 提案", "--body", "来自 CLI"])
    assert cli.exit_code == 0, cli.output

    numbers = sorted(int(p.name[:4]) for p in in_project.proposals_dir.glob("*.md"))
    assert numbers == [1, 2]


# ------------------------------------------------------------------------ auto


def test_auto_without_any_agent_cli_errors(in_project, monkeypatch):
    # §10: autopilot is a thin shell over ANY one-shot agent CLI; with no
    # agent resolvable it must fail loudly, list the options, and never spawn
    # a subprocess.
    monkeypatch.delenv("MANJU_AGENT", raising=False)
    monkeypatch.setattr("manju.agents.shutil.which", lambda name: None)
    result = runner.invoke(app, ["auto", "把片子做出来"])
    assert result.exit_code == 1
    combined = result.stdout + result.stderr
    assert "no agent CLI found" in combined
    assert "claude" in combined and "codex" in combined  # options listed


# ------------------------------------------------------------ redo (ffmpeg e2e)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="redo runs real generation via the fallback chain")
def test_redo_json_generates_local_take(in_project, add_shot):
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["redo", "S001", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["shot"] == "S001"
    assert len(data["takes"]) >= 1  # kenburns has no ref image -> caption_card card

    # the local generation was recorded in the disposable run ledger (§8.3)
    from manju.runtime.state import RuntimeState

    with RuntimeState(in_project.root) as state:
        log = state.run_log()
    assert any(r["shot"] == "S001" and r["status"] == "succeeded" for r in log)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="redo runs real generation via the fallback chain")
def test_redo_from_take_reuses_recipe(in_project, add_shot):
    """R12 recipe-reuse: `manju redo --from-take` replays a prior take's
    recorded recipe (provider + params) as a fresh append-only take, and a
    bad take name is a clean one-line error, not a traceback."""
    add_shot(in_project, "S001")
    first = runner.invoke(app, ["redo", "S001", "--json"])
    assert first.exit_code == 0, first.output
    take0 = json.loads(first.output)["takes"][0]

    again = runner.invoke(app, ["redo", "S001", "--from-take", take0, "--json"])
    assert again.exit_code == 0, again.output
    new_takes = json.loads(again.output)["takes"]
    assert new_takes and new_takes[0] != take0  # append-only, a distinct take

    # the reused recipe travels: same provider recorded on the new take
    prior = in_project.get_take("S001", take0)
    fresh = in_project.get_take("S001", new_takes[0])
    assert fresh.sidecar.provider == prior.sidecar.provider

    bad = runner.invoke(app, ["redo", "S001", "--from-take", "take_99"])
    assert bad.exit_code == 1
    assert "no such take" in (bad.stdout + bad.stderr)


def test_pack_excludes_rebuildable_caches(tmp_project, add_shot, monkeypatch, tmp_path):
    """§3: segment/proxy caches are rebuildable — pack omits them by default,
    keeps them under --full; imports and finals always ride."""
    import zipfile

    add_shot(tmp_project, "S001")
    (tmp_project.segments_dir).mkdir(parents=True, exist_ok=True)
    (tmp_project.segments_dir / "seg.mp4").write_bytes(b"cache" * 100)
    (tmp_project.proxy_dir / "proxy.mp4").write_bytes(b"proxy")
    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.final_dir / "final_v1.mp4").write_bytes(b"final")
    (tmp_project.imports_dir / "clip.mp4").write_bytes(b"import")

    monkeypatch.chdir(tmp_project.root)

    out = tmp_path / "a.manjupkg"
    assert runner.invoke(app, ["pack", "--out", str(out)]).exit_code == 0
    names = set(zipfile.ZipFile(out).namelist())
    assert "renders/final/final_v1.mp4" in names
    assert "media/imports/clip.mp4" in names
    assert not any(n.startswith("renders/segments/") for n in names)
    assert not any(n.startswith("renders/proxy/") for n in names)

    out_full = tmp_path / "b.manjupkg"
    assert runner.invoke(app, ["pack", "--out", str(out_full), "--full"]).exit_code == 0
    full_names = set(zipfile.ZipFile(out_full).namelist())
    assert "renders/segments/seg.mp4" in full_names


def test_pack_never_follows_symlink_to_outside_file(tmp_project, add_shot, monkeypatch, tmp_path):
    """goal item 14: a symlink inside the project pointing at a file OUTSIDE
    the project must never have its target bytes embedded in the .manjupkg
    under the safe-looking in-project name — that would leak an
    outside-project file through a package that is supposed to represent
    only the project directory. The symlink is skipped (with a warning),
    never followed."""
    import zipfile

    add_shot(tmp_project, "S001")
    outside = tmp_path / "outside_secret.txt"
    outside.write_bytes(b"TOP-SECRET-OUTSIDE-PROJECT-BYTES")
    link = tmp_project.root / "media" / "refs" / "sneaky_link.txt"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside)

    monkeypatch.chdir(tmp_project.root)
    out = tmp_path / "symlink.manjupkg"
    result = runner.invoke(app, ["pack", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "符号链接" in result.output  # the skip warning fired

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "media/refs/sneaky_link.txt" not in names  # never stored
        for n in names:
            assert zf.read(n) != b"TOP-SECRET-OUTSIDE-PROJECT-BYTES"
