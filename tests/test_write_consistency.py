"""Round AA item 5 — cross-entry write consistency.

Three precise fixes, one test module:

1. **CAS** (optimistic concurrency) on read-modify-write entrances —
   ``core.writes.checked_shot_write``'s ``expected_text_hash`` / the
   ``shot_text_hash`` helper, threaded into MCP ``update_shot``'s
   ``expected_rev`` and the GUI's shot-editor / storyboard-cell / take-note
   forms (each of which holds a rendered snapshot a human may sit on before
   saving — the textbook case this closes).
2. **Residual A** (DECISIONS §9) — per-episode ``BuildLock`` coverage inside
   ``core.series.sync_bible``/``new_episode`` (previously NO cross-process
   guard on writes into multiple episode projects).
3. **Residual B** (DECISIONS §9) — per-action-type ``BuildLock`` coverage at
   ``build.director``'s dispatch site (previously a mix of already-locked
   and unlocked action types with zero coverage on the unlocked half).

No ffmpeg anywhere: every action exercised is text/git only. Chinese names/
messages throughout (§14), matching the rest of the suite.
"""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from manju.build import director as d
from manju.core.container import Project
from manju.core.hashing import hash_text
from manju.core.series import Series, new_episode, sync_bible
from manju.core.writes import WriteRejected, checked_shot_write, shot_text_hash
from manju.core.yamlio import read_yaml, write_yaml
from manju.runtime.buildlock import BuildLock

# ======================================================================
# 1a. core.writes: checked_shot_write's CAS kwarg + shot_text_hash helper
# ======================================================================


def test_shot_text_hash_empty_for_missing_shot(tmp_project: Project):
    assert shot_text_hash(tmp_project, "NOPE") == ""


def test_shot_text_hash_matches_canonical_hash_text(tmp_project: Project, add_shot):
    add_shot(tmp_project, "S001")
    text = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    assert shot_text_hash(tmp_project, "S001") == hash_text(text)


def test_checked_shot_write_cas_happy_path(tmp_project: Project, add_shot):
    """The correct hash (what the caller actually loaded) writes normally."""
    add_shot(tmp_project, "S001")
    rev = shot_text_hash(tmp_project, "S001")

    result = checked_shot_write(
        tmp_project, "S001",
        lambda d: d.setdefault("dialogue", {}).__setitem__("text", "改动一。"),
        expected_text_hash=rev,
    )
    assert result["ok"] is True
    assert tmp_project.load_shot("S001").dialogue.text == "改动一。"


def test_checked_shot_write_stale_hash_rejected_file_untouched(tmp_project: Project, add_shot):
    """A hash from BEFORE someone else's edit is refused — WriteRejected,
    中文, and the file is left byte-for-byte as the other entrance left it
    (never even reverted, because nothing was ever written)."""
    add_shot(tmp_project, "S001")
    stale_rev = shot_text_hash(tmp_project, "S001")

    # a different entrance writes first
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "别人先改了。"))
    on_disk = tmp_project.shot_path("S001").read_text(encoding="utf-8")

    with pytest.raises(WriteRejected, match="乐观锁"):
        checked_shot_write(
            tmp_project, "S001",
            lambda d: d.setdefault("dialogue", {}).__setitem__("text", "我也想改。"),
            expected_text_hash=stale_rev,
        )
    assert tmp_project.shot_path("S001").read_text(encoding="utf-8") == on_disk


def test_checked_shot_write_no_cas_when_hash_omitted(tmp_project: Project, add_shot):
    """`expected_text_hash=None` (the default) skips the check entirely —
    byte-for-byte the pre-round-AA behavior for every existing call site."""
    add_shot(tmp_project, "S001")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "别人先改了。"))

    result = checked_shot_write(
        tmp_project, "S001",
        lambda d: d.setdefault("dialogue", {}).__setitem__("text", "覆盖也没关系。"),
    )
    assert result["ok"] is True
    assert tmp_project.load_shot("S001").dialogue.text == "覆盖也没关系。"


# ======================================================================
# 1b. MCP update_shot's expected_rev / get_shot's rev
# ======================================================================


def test_mcp_get_shot_exposes_rev(tmp_project: Project, add_shot):
    from manju.mcp.tools import call_tool

    add_shot(tmp_project, "S001")
    payload = call_tool(tmp_project, "get_shot", {"shot_id": "S001"})
    assert payload["rev"] == shot_text_hash(tmp_project, "S001")
    assert payload["rev"]  # non-empty for an existing shot


def test_mcp_update_shot_stale_expected_rev_errors_fresh_rev_works(
    tmp_project: Project, add_shot
):
    import copy

    import yaml

    from manju.mcp.tools import ToolError, call_tool

    add_shot(tmp_project, "S001")
    before = call_tool(tmp_project, "get_shot", {"shot_id": "S001"})

    # a concurrent entrance (CLI / a second MCP client / the GUI) edits the
    # SAME shot after we loaded it
    concurrent = copy.deepcopy(before["data"])
    concurrent["dialogue"]["text"] = "别的入口先改了。"
    tmp_project.shot_path("S001").write_text(
        yaml.safe_dump(concurrent, allow_unicode=True, sort_keys=False), encoding="utf-8")
    on_disk_after_race = tmp_project.shot_path("S001").read_text(encoding="utf-8")

    # our own edit, built from the ORIGINAL (now-stale) load
    mine = copy.deepcopy(before["data"])
    mine["dialogue"]["text"] = "我们的改动。"
    my_yaml = yaml.safe_dump(mine, allow_unicode=True, sort_keys=False)

    with pytest.raises(ToolError, match="乐观锁"):
        call_tool(tmp_project, "update_shot", {
            "shot_id": "S001", "yaml_content": my_yaml, "expected_rev": before["rev"],
        })
    # refused BEFORE anything touched disk — the concurrent edit stands
    assert tmp_project.shot_path("S001").read_text(encoding="utf-8") == on_disk_after_race

    # re-fetch the CURRENT rev and retry — now it lands
    fresh = call_tool(tmp_project, "get_shot", {"shot_id": "S001"})
    assert fresh["rev"] != before["rev"]
    result = call_tool(tmp_project, "update_shot", {
        "shot_id": "S001", "yaml_content": my_yaml, "expected_rev": fresh["rev"],
    })
    assert result["ok"] is True
    reloaded = yaml.safe_load(tmp_project.shot_path("S001").read_text(encoding="utf-8"))
    assert reloaded["dialogue"]["text"] == "我们的改动。"


def test_mcp_update_shot_omitting_expected_rev_unaffected(tmp_project: Project, add_shot):
    """Backward compatible: an agent that never calls get_shot (or doesn't
    pass expected_rev back) sees the exact pre-round-AA behavior."""
    import yaml

    from manju.mcp.tools import call_tool

    add_shot(tmp_project, "S001")
    data = call_tool(tmp_project, "get_shot", {"shot_id": "S001"})["data"]
    data["dialogue"]["text"] = "没有 rev 也能存。"
    result = call_tool(tmp_project, "update_shot", {
        "shot_id": "S001",
        "yaml_content": yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    })
    assert result["ok"] is True


# ======================================================================
# 1c. GUI 409 — the shot editor (full raw-YAML form) is CAS-protected
# ======================================================================

from manju.gui.server import create_server  # noqa: E402


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = {}
        return exc.code, parsed


def _post(server, path, body):
    return _req(server, path, method="POST", body=body,
               headers={"X-Manju-Token": server.token})


def test_gui_shot_editor_stale_rev_refused_409(gui, tmp_project: Project, add_shot):
    """The shot editor's client holds the FULL raw YAML text loaded via GET
    (a textarea a human may sit on before saving) — round AA's own litmus
    test for CAS. A stale `expected_rev` is refused 409 + 中文, the file is
    untouched, and a fresh rev (re-GET) succeeds normally."""
    add_shot(tmp_project, "S001")
    status, loaded = _req(gui, "/api/shot/S001")
    assert status == 200
    rev = loaded["rev"]
    assert rev

    # a concurrent entrance (CLI / MCP / another browser tab) edits the shot
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "别的入口先改了。"))
    on_disk = tmp_project.shot_path("S001").read_text(encoding="utf-8")

    stale_yaml = loaded["yaml"].replace("这不可能", "我方的改动")
    status, resp = _post(gui, "/api/shot/S001", {"yaml": stale_yaml, "expected_rev": rev})
    assert status == 409
    assert "乐观锁" in resp.get("error", "")
    assert tmp_project.shot_path("S001").read_text(encoding="utf-8") == on_disk  # untouched

    # re-GET for the current rev -> save now succeeds
    status, fresh = _req(gui, "/api/shot/S001")
    assert status == 200 and fresh["rev"] != rev
    fresh_yaml = fresh["yaml"].replace("别的入口先改了", "我方的改动")
    status, resp = _post(gui, "/api/shot/S001",
                         {"yaml": fresh_yaml, "expected_rev": fresh["rev"]})
    assert status == 200 and resp["ok"] is True
    assert "我方的改动" in tmp_project.shot_path("S001").read_text(encoding="utf-8")


def test_gui_shot_editor_save_without_expected_rev_unaffected(gui, tmp_project: Project, add_shot):
    """Omitting `expected_rev` (an older client, or a fresh create) skips the
    CAS check — pre-round-AA behavior, unchanged."""
    add_shot(tmp_project, "S001")
    status, loaded = _req(gui, "/api/shot/S001")
    new_yaml = loaded["yaml"].replace("这不可能", "无 rev 也能存")
    status, resp = _post(gui, "/api/shot/S001", {"yaml": new_yaml})
    assert status == 200 and resp["ok"] is True


# ======================================================================
# 2. core.series: per-episode BuildLock in sync_bible / new_episode
# ======================================================================


def _make_series(tmp_path):
    series = Series.create(tmp_path / "深夜信号剧集", name="深夜信号剧集", git_init=False)
    write_yaml(series.bible_dir / "characters.yaml", {"linxia": {"name": "林夏"}})
    return series


def test_sync_bible_stops_at_busy_episode_names_it_earlier_ones_stay_synced(tmp_path):
    series = _make_series(tmp_path)
    e1 = new_episode(series, "E01")
    e2 = new_episode(series, "E02")
    e3 = new_episode(series, "E03")

    # a new character on the series bible only — every episode gets an ADD candidate
    write_yaml(series.bible_dir / "characters.yaml",
              {"linxia": {"name": "林夏"}, "gou": {"name": "阿狗"}})

    lock = BuildLock(e2.root, actor="human").acquire()
    try:
        report = sync_bible(series, apply=True)
    finally:
        lock.release()

    assert report["stopped_at"] == "E02"
    rows = {r["id"]: r for r in report["episodes"]}
    # E01 (processed BEFORE the busy one) landed and stays synced
    assert rows["E01"]["added"] == ["characters:gou"]
    assert "gou" in read_yaml(e1.root / "bible" / "characters.yaml")
    # E02 (the busy one) is named, 中文, and untouched
    assert rows["E02"]["error"] and "E02" in rows["E02"]["error"]
    assert any(c in rows["E02"]["error"] for c in "占用锁定被")
    assert "gou" not in read_yaml(e2.root / "bible" / "characters.yaml")
    # E03 was never even attempted
    assert "E03" not in rows
    assert "gou" not in read_yaml(e3.root / "bible" / "characters.yaml")

    # lock released -> re-running sync-bible picks up where it left off
    report2 = sync_bible(series, apply=True)
    assert report2["stopped_at"] is None
    assert "gou" in read_yaml(e2.root / "bible" / "characters.yaml")
    assert "gou" in read_yaml(e3.root / "bible" / "characters.yaml")


def test_sync_bible_report_only_never_takes_a_lock(tmp_path):
    """apply=False writes nothing, so a busy episode does not block the
    report at all (there is nothing there to protect)."""
    series = _make_series(tmp_path)
    e1 = new_episode(series, "E01")
    write_yaml(series.bible_dir / "characters.yaml",
              {"linxia": {"name": "林夏"}, "gou": {"name": "阿狗"}})

    lock = BuildLock(e1.root, actor="human").acquire()
    try:
        report = sync_bible(series, apply=False)
    finally:
        lock.release()

    assert report["stopped_at"] is None
    assert report["episodes"][0]["added"] == ["characters:gou"]


def test_new_episode_registers_normally_when_series_root_is_free(tmp_path):
    series = _make_series(tmp_path)
    project = new_episode(series, "E01", title="第一集")
    cfg = series.load_config()
    assert [(e.id, e.title) for e in cfg.episodes] == [("E01", "第一集")]
    assert project.root.name == "E01.manju"


def test_new_episode_busy_series_lock_leaves_project_on_disk_but_unregistered(tmp_path):
    """The series.yaml register step (round AA) takes the SERIES root's own
    BuildLock; a busy series root fails the registration honestly — the new
    project directory (created BEFORE the lock, which needs none per the
    round's own design: nothing else can know about it yet) is left on disk,
    never silently dropped, and Series.episode_ids() still surfaces it."""
    from manju.core.series import SeriesError

    series = _make_series(tmp_path)
    lock = BuildLock(series.root, actor="human").acquire()
    try:
        with pytest.raises(SeriesError, match="E01"):
            new_episode(series, "E01")
    finally:
        lock.release()

    # not registered in series.yaml...
    assert series.load_config().episodes == []
    # ...but the directory + seeded bible exist, and episode_ids() folds it in
    ep_dir = series.episode_project_dir("E01")
    assert (ep_dir / "project.yaml").exists()
    assert "E01" in series.episode_ids()

    # released -> a plain new_episode with a DIFFERENT id still works normally
    new_episode(series, "E02")
    assert any(e.id == "E02" for e in series.load_config().episodes)


# ======================================================================
# 3. build.director: per-action-type BuildLock at the dispatch site
# ======================================================================


@pytest.fixture
def director_git_project(tmp_project: Project) -> Project:
    """A git-backed project (director's auto-snapshot + the snapshot action
    both need a repo), mirroring tests/test_director.py's own git_project."""
    tmp_project.save_rules(tmp_project.load_rules())  # materialize rules.yaml
    subprocess.run(["git", "init", "-q"], cwd=tmp_project.root, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_project.root), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_project.root), "-c", "user.email=a@b.c",
                    "-c", "user.name=t", "commit", "-qm", "init"], capture_output=True)
    return tmp_project


def test_execute_formerly_unlocked_action_type_respects_held_build_lock(
    director_git_project: Project,
):
    """`snapshot` is one of the five action types director.py's dispatch
    table did NOT wrap in build_lock before this round (core/history.py
    never takes one). Holding the project's build_lock externally must now
    block it — first-failure-stop, recorded as a clean ActionError, not a
    silent interleave."""
    prop = d.propose(director_git_project, [{"type": "snapshot", "label": "should-block"}])
    d.confirm(director_git_project, prop.id)

    lock = BuildLock(director_git_project.root, actor="human").acquire()
    try:
        out = d.execute(director_git_project, prop.id)
    finally:
        lock.release()

    assert out.ok is False and out.state == "failed"
    assert out.results[0]["ok"] is False
    assert "pid" in out.results[0]["error"]  # the BuildLocked message shape

    # released -> the SAME proposal type works fine standalone
    prop2 = d.propose(director_git_project, [{"type": "snapshot", "label": "now-ok"}])
    d.confirm(director_git_project, prop2.id)
    out2 = d.execute(director_git_project, prop2.id)
    assert out2.ok is True


def test_execute_locked_internally_action_type_no_double_acquire(
    director_git_project: Project, monkeypatch
):
    """No-double-acquire regression guard (mirrors test_write_locks.py): a
    `build`-type action already takes build_lock INSIDE graph.run_build —
    this round's dispatch-site wrap must NOT wrap it a second time (which
    would self-deadlock even with zero external contention, since BuildLock
    is not reentrant)."""
    import manju.build.graph as graph

    def fake_run_build(project, *, target="final", gen="missing", regen_stale=False,
                       dry_run=False, force=False, actor="engine",
                       assume_yes=False, on_phase=None, mode=None):
        r = graph.BuildResult()
        r.ok = True
        r.render_path = None if dry_run else "renders/final/final_v1.mp4"
        return r

    monkeypatch.setattr(graph, "run_build", fake_run_build)

    prop = d.propose(director_git_project, [{"type": "build", "target": "final"}])
    d.confirm(director_git_project, prop.id)
    out = d.execute(director_git_project, prop.id)
    assert out.ok is True
    assert out.results[0]["type"] == "build"
