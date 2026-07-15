"""`manju gui` — S8a GUI CORE INTERACTIONS (round S §4).

Covers the six S8a features as HTTP behaviour over the same strict-client
server the CLI shares: first-run onboarding (live done-detection, per-user
persistence), the pre-generation plan endpoint (dry-run shape + routing
awareness), structured failure cards in the state poll, the batch redo/voice
endpoints (skip/fail reasons), reorder, the packaging YAML editor's
validate-on-save, the §5 lock guard, and the new-project dialog. Each asserts
the same truth files / events the CLI would have written, and re-checks that
the dangerous surface stays absent (§5).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.core.events import tail_events
from manju.gui.server import create_server

# ---------------------------------------------------------------- fixtures


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path, monkeypatch):
    """Never touch the real ~/.manju in tests: the per-user gui_state store and
    any user routing file are redirected to hermetic tmp paths."""
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "gui_state.json"))
    monkeypatch.setenv("MANJU_ROUTING", str(tmp_path / "no_user_routing.yaml"))


def _request(server, path, *, method="GET", body=None, headers=None, host=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _post(server, path, body, token=None, **kw):
    headers = {"X-Manju-Token": token if token is not None else server.token}
    return _request(server, path, method="POST", body=body, headers=headers, **kw)


def _wait_job(server, job_id, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, _, data = _request(server, "/api/jobs")
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# --------------------------------------------------------------- onboarding


def test_onboarding_live_done_detection(gui, tmp_project, add_shot, make_take):
    status, _, data = _request(gui, "/api/onboarding")
    assert status == 200
    steps = {s["key"]: s for s in data["steps"]}
    assert set(steps) == {"project", "story", "shots", "generate", "review", "export"}
    # a fresh project: only 建项目 is done; each step carries a hint + CLI one-liner
    assert steps["project"]["done"] is True
    assert steps["shots"]["done"] is False and steps["generate"]["done"] is False
    assert steps["shots"]["hint"] and steps["shots"]["cli"]
    # empty-ish (no takes) and not dismissed -> auto-show
    assert data["empty_ish"] is True and data["dismissed"] is False
    assert data["should_show"] is True

    # writing real story prose flips 写剧本 (heading+comment scaffold does not)
    (tmp_project.root / "story" / "script.md").write_text(
        "# 剧本\n\n林夏推开便利店的门,雨点砸在玻璃上。\n", encoding="utf-8")
    _, _, data = _request(gui, "/api/onboarding")
    assert {s["key"]: s["done"] for s in data["steps"]}["story"] is True

    # a shot flips 建镜头; a take flips 生成 and clears empty-ish -> no auto-show
    add_shot(tmp_project, "S001")
    _, _, data = _request(gui, "/api/onboarding")
    assert {s["key"]: s["done"] for s in data["steps"]}["shots"] is True
    assert data["should_show"] is True  # still no takes
    make_take(tmp_project, "S001", "h")
    _, _, data = _request(gui, "/api/onboarding")
    done = {s["key"]: s["done"] for s in data["steps"]}
    assert done["generate"] is True
    assert data["empty_ish"] is False and data["should_show"] is False


def test_onboarding_dismiss_persists_per_user(gui, tmp_project, tmp_path):
    store = tmp_path / "gui_state.json"
    _, _, data = _request(gui, "/api/onboarding")
    assert data["should_show"] is True
    status, _, data = _post(gui, "/api/onboarding/dismiss", {"dismissed": True})
    assert status == 200 and data["dismissed"] is True
    # persisted to the per-USER store, keyed by project root — NOT in the project
    assert store.exists()
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert str(tmp_project.root.resolve()) in saved["onboarding_dismissed"]
    assert not (tmp_project.root / "gui_state.json").exists()
    # now suppressed
    _, _, data = _request(gui, "/api/onboarding")
    assert data["dismissed"] is True and data["should_show"] is False
    # re-open (header 帮助) clears the dismissal
    status, _, data = _post(gui, "/api/onboarding/dismiss", {"dismissed": False})
    assert status == 200 and data["dismissed"] is False
    _, _, data = _request(gui, "/api/onboarding")
    assert data["dismissed"] is False


# ---------------------------------------------------- plan modal endpoint


def test_plan_build_shape(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # MISSING -> appears in the plan
    status, _, data = _post(gui, "/api/plan", {"action": "build"})
    assert status == 200 and data["action"] == "build"
    row = next(r for r in data["rows"] if r["shot"] == "S001")
    assert row["provider"] and isinstance(row["estimated_cost"], (int, float))
    for key in ("estimated_cost", "saved_cost", "currency", "zero_cost", "routing", "skipped"):
        assert key in data
    assert data["routing"] is False  # no routing.yaml present


def test_plan_provider_from_routing_when_present(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # a project routing.yaml flips the resolver on: providers come from routing
    (tmp_project.root / "timeline" / "routing.yaml").write_text(
        "strategy: local_only\n", encoding="utf-8")
    status, _, data = _post(gui, "/api/plan", {"action": "build"})
    assert status == 200 and data["routing"] is True
    assert data["rows"] and all(r["provider"] for r in data["rows"])


def test_plan_redo_prices_routed_provider_not_static_head(gui, tmp_project, add_shot, monkeypatch):
    """Goal 7: gui/plan.py's redo row already SHOWED the routing-resolved
    provider label; the estimated_cost must price that SAME provider, not the
    untouched shot's static fallback head."""
    import manju.providers.registry as registry_mod
    from manju.core.yamlio import write_yaml

    def _manifest(pid, per_second):
        return {
            "id": pid, "type": "video", "adapter": "generic_cloud",
            "capabilities": ["text_to_video"],
            "auth": {"key_env": f"{pid.upper()}_KEY"},
            "submit": {"url": "https://x/v", "body_template": {}, "job_id_path": "$.id"},
            "poll": {"url": "https://x/v/{job_id}", "status_path": "$.s",
                     "status_map": {"ok": "succeeded"}},
            "cost": {"per_second": per_second, "currency": "CNY"},
        }

    providers_dir = tmp_project.root.parent / "manju_providers_gui_plan"
    write_yaml(providers_dir / "aaa_cheap" / "provider.yaml", _manifest("aaa_cheap", 0.01))
    write_yaml(providers_dir / "zzz_pricey" / "provider.yaml", _manifest("zzz_pricey", 0.20))
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(providers_dir))
    monkeypatch.setenv("AAA_CHEAP_KEY", "k")
    monkeypatch.setenv("ZZZ_PRICEY_KEY", "k")
    registry_mod._manifest_cache = None

    add_shot(tmp_project, "S001", duration=4.0)
    # routing sends this shot to zzz_pricey (a literal else selector) even
    # though the static fallback chain would pick aaa_cheap first.
    (tmp_project.root / "timeline" / "routing.yaml").write_text(
        "strategy: default\nstrategies:\n  default:\n    rules: []\n"
        "    else: zzz_pricey\n", encoding="utf-8")
    registry_mod._manifest_cache = None

    _, _, redo = _post(gui, "/api/plan", {"action": "redo", "shot": "S001"})
    assert redo["rows"][0]["provider"] == "zzz_pricey"
    # 4s × 0.20/s — NOT 4s × 0.01/s (aaa_cheap, the static fallback head)
    assert redo["rows"][0]["estimated_cost"] == pytest.approx(0.80)

    _, _, batch = _post(gui, "/api/plan",
                        {"action": "batch-redo", "shots": ["S001"]})
    assert batch["rows"][0]["provider"] == "zzz_pricey"
    assert batch["rows"][0]["estimated_cost"] == pytest.approx(0.80)
    registry_mod._manifest_cache = None


def test_plan_redo_and_voice_shape(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _, _, redo = _post(gui, "/api/plan", {"action": "redo", "shot": "S001"})
    assert redo["action"] == "redo" and len(redo["rows"]) == 1
    assert redo["rows"][0]["shot"] == "S001"
    _, _, voice = _post(gui, "/api/plan", {"action": "voice", "shot": "S001"})
    assert voice["action"] == "voice" and voice["rows"][0]["kind"] == "voice"
    # a voiceless shot cannot be planned for voice -> clean 400, never a spend
    add_shot(tmp_project, "S002", dialogue={"speaker": "", "text": ""})
    assert _post(gui, "/api/plan", {"action": "voice", "shot": "S002"})[0] == 400
    # unknown action -> 400
    assert _post(gui, "/api/plan", {"action": "nope"})[0] == 400


def test_plan_batch_redo_skips_manual(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")  # will run
    add_shot(tmp_project, "S002")
    take = make_take(tmp_project, "S002", "manual")  # MANUAL_HASH sentinel
    _post(gui, "/api/select", {"shot": "S002", "take": take.name})
    _, _, data = _post(gui, "/api/plan",
                       {"action": "batch-redo", "shots": ["S001", "S002"]})
    assert data["action"] == "batch-redo"
    assert any(r["shot"] == "S001" for r in data["rows"])
    skip = next(s for s in data["skipped"] if s["shot"] == "S002")
    assert "manual" in skip["reason"]


# ------------------------------------------------------------- failures


def test_failure_cards_in_state(gui, tmp_project, add_shot):
    from manju.core.failures import Failure, record_failure

    add_shot(tmp_project, "S001")
    record_failure(tmp_project, Failure(
        step="generate", subject="S001", cause="provider timed out",
        evidence="HTTP 504\nbody: upstream timeout while polling",
        hint="retry, or switch generation.provider", log_path="reports/logs/S001.log",
        level="error", detail={"job_id": "job-abc"}))
    record_failure(tmp_project, Failure(
        step="compile", subject="timeline", cause="fell back to a caption card",
        level="info"))
    _, _, state = _request(gui, "/api/state")
    fails = state["failures"]
    assert len(fails) >= 2
    err = next(f for f in fails if f["subject"] == "S001")
    assert err["level"] == "error" and err["cause"] == "provider timed out"
    assert "upstream timeout" in err["evidence"]           # expandable evidence
    assert err["hint"] and err["log_path"] == "reports/logs/S001.log"
    assert err["detail"]["job_id"] == "job-abc"            # correlated job
    assert any(f["level"] == "info" for f in fails)        # degradation, neutral
    # a new failure moves the fingerprint (cards poll with state)
    fp1 = state["fp"]
    record_failure(tmp_project, Failure(step="render", subject="final", cause="x"))
    _, _, state2 = _request(gui, "/api/state")
    assert state2["fp"] != fp1


# ---------------------------------------------------------- batch endpoints


def test_redo_batch_endpoint_reports_skip_and_fail(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S002")
    take = make_take(tmp_project, "S002", "manual")
    _post(gui, "/api/select", {"shot": "S002", "take": take.name})
    # S002 manual (skipped), S404 absent (failed) — neither generates, so the
    # job is deterministic and the reasons come straight from redo_batch
    status, _, data = _post(gui, "/api/redo-batch",
                            {"shots": ["S002", "S404"], "assume_yes": True})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "done"
    result = job["result"]
    skip = next(s for s in result["skipped"] if s["shot"] == "S002")
    assert "manual" in skip["reason"]
    fail = next(f for f in result["failed"] if f["shot"] == "S404")
    assert fail["reason"]
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "redo_batch"


def test_batch_endpoints_validate(gui):
    assert _post(gui, "/api/redo-batch", {"shots": []})[0] == 400
    assert _post(gui, "/api/voice-batch", {"shots": "S001"})[0] == 400


def test_voice_batch_endpoint(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # has dialogue
    status, _, data = _post(gui, "/api/voice-batch",
                            {"shots": ["S001"], "assume_yes": True})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    # no TTS configured -> the shot fails cleanly; the job itself completes
    assert job["state"] == "done"
    assert "ran" in job["result"] and "failed" in job["result"]


# ----------------------------------------------------------------- reorder


def test_reorder_roundtrips_index_and_event(gui, tmp_project, add_shot):
    for sid in ("S001", "S002", "S003"):
        add_shot(tmp_project, sid)
    status, _, data = _post(gui, "/api/index", {"order": ["S002", "S003", "S001"]})
    assert status == 200 and data["ok"] is True
    assert tmp_project.load_index().order == ["S002", "S003", "S001"]
    ev = [e for e in tail_events(tmp_project.root, 5) if e["action"] == "reorder"]
    assert ev and ev[-1]["detail"]["via"] == "gui"


# ------------------------------------------------------- packaging editor


def test_packaging_editor_validate_on_save(gui, tmp_project):
    status, _, data = _request(gui, "/api/packaging")
    assert status == 200 and data["exists"] is True
    before = tmp_project.packaging_path.read_text(encoding="utf-8")
    # model_validate rejects invalid shape with a one-line error, never saves
    status, _, data = _post(gui, "/api/packaging", {"yaml": "info_cards: 5\n"})
    assert status == 400 and "packaging" in data["error"]
    assert tmp_project.packaging_path.read_text(encoding="utf-8") == before
    # malformed YAML -> 400
    assert _post(gui, "/api/packaging", {"yaml": "info_cards: [unclosed"})[0] == 400
    # /api/validate learns the packaging kind (keystroke-time)
    _, _, v = _post(gui, "/api/validate", {"kind": "packaging", "yaml": "info_cards: 5\n"})
    assert v["ok"] is False and v["errors"]
    _, _, v = _post(gui, "/api/validate", {"kind": "packaging", "yaml": "info_cards: []\n"})
    assert v["ok"] is True
    # a valid save round-trips through the check gate
    status, _, data = _post(gui, "/api/packaging", {"yaml": before})
    assert status == 200 and data["ok"] is True


# ----------------------------------------------------------- lock guard


def test_lock_guard_refuses_locked_field_write(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/lock", {"shot": "S001", "field": "dialogue.text"})[0] == 200
    # the GUI surfaces lock awareness (the editor renders 🔒 read-only from this)
    _, _, sd = _request(gui, "/api/shot/S001")
    assert "dialogue.text" in sd["locked"]
    # a guarded edit can NEVER write through the lock (§5): 409 + revert
    before = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    tampered = before.replace("这不可能。", "改掉锁定的台词")
    status, _, data = _post(gui, "/api/shot/S001", {"yaml": tampered})
    assert status == 409
    assert tmp_project.shot_path("S001").read_text(encoding="utf-8") == before


# ------------------------------------------------ git snapshot / rollback


def test_snapshot_and_rollback_file(gui, tmp_project, add_shot):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_project.root, check=True)
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/git/snapshot", {"label": "first"})
    assert status == 200 and data["ok"] is True and data["clean"] is False
    # edit the shot on disk, then roll it back to the snapshot
    path = tmp_project.shot_path("S001")
    original = path.read_text(encoding="utf-8")
    path.write_text(original + "# scratch\n", encoding="utf-8")
    status, _, data = _post(gui, "/api/git/rollback-file",
                            {"path": "shots/S001.yaml"})
    assert status == 200 and data["ok"] is True
    assert path.read_text(encoding="utf-8") == original
    ev = [e for e in tail_events(tmp_project.root, 10) if e["action"] == "rollback_file"]
    assert ev
    # media/derived output is refused (truth text only)
    status, _, data = _post(gui, "/api/git/rollback-file",
                            {"path": "renders/final/final_v1.mp4"})
    assert status == 400


def test_snapshot_no_repo(gui, tmp_project):
    status, _, data = _post(gui, "/api/git/snapshot", {})
    assert status == 409 and "git" in data["error"].lower()


# -------------------------------------------------------------- tasks view


def test_tasks_endpoint_shape(gui):
    status, _, data = _request(gui, "/api/tasks")
    assert status == 200
    assert isinstance(data["tasks"], list) and "spend" in data
    assert "total" in data["spend"]


# -------------------------------------------------------- new-project dialog


def test_new_project_creates_via_preset(tmp_path):
    from manju.core.container import Project
    from manju.gui.server import discover_workspace

    ws = tmp_path / "studio"
    ws.mkdir()
    a = Project.create(ws / "甲", git_init=False)
    projects = discover_workspace(ws)
    server = create_server(a, host="127.0.0.1", port=0, workspace=projects)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, _, pdata = _request(server, "/api/presets")
        assert pdata["presets"]
        pname = pdata["presets"][0]["name"]
        status, _, data = _post(server, "/api/new-project",
                                {"name": "乙", "preset": pname})
        assert status == 201 and data["ok"] is True and data["name"] == "乙"
        # created on disk as a sibling, via the SAME core (preset recorded)
        newp = Project(ws / "乙.manju")
        assert newp.load_config().preset == pname
        # Session immutable: stays on 甲; new project joins catalog for a new window
        assert data.get("open_in_new_window") is True
        _, _, state = _request(server, "/api/state")
        assert state["project"]["name"] == "甲"
        _, _, plist = _request(server, "/api/projects")
        assert any(p["name"] == "乙" for p in plist["projects"])
        # orientation honoured (no preset -> plain 16:9)
        status, _, data = _post(server, "/api/new-project",
                                {"name": "丙", "vertical": False})
        assert status == 201
        assert Project(ws / "丙.manju").load_config().width == 1920
        # 'new' event recorded via the GUI
        ev = [e for e in tail_events((ws / "乙.manju"), 5) if e["action"] == "new"]
        assert ev and ev[-1]["detail"]["via"] == "gui"
    finally:
        server.shutdown()
        server.close()


def test_new_project_outside_workspace_rejected(gui):
    assert _post(gui, "/api/new-project", {"name": "x"})[0] == 400


# ------------------------------------------------ dangerous surface (§5)


def test_dangerous_surface_still_absent(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # unlock / gc / pack / unpack are NOT on this surface (mirrors MCP, §5)
    for path in ("/api/unlock", "/api/gc", "/api/pack", "/api/unpack"):
        assert _post(gui, path, {})[0] == 404, path
    # every new mutating POST is behind the CSRF token, like the rest
    for path in ("/api/plan", "/api/redo-batch", "/api/voice-batch",
                 "/api/git/snapshot", "/api/new-project", "/api/onboarding/dismiss",
                 "/api/packaging"):
        status, _, data = _request(gui, path, method="POST", body={})
        assert status == 403 and "token" in data["error"].lower(), path


def test_new_endpoints_blocked_in_readonly(tmp_project, add_shot):
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        add_shot(tmp_project, "S001")
        for path, body in (("/api/plan", {"action": "build"}),
                           ("/api/redo-batch", {"shots": ["S001"]}),
                           ("/api/git/snapshot", {}),
                           ("/api/packaging", {"yaml": "info_cards: []\n"})):
            status, _, data = _post(server, path, body)
            assert status == 403 and "readonly" in data["error"], path
    finally:
        server.shutdown()
        server.close()
