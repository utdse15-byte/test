"""`manju gui` — the local web workbench (§1-⑦ revisited).

The server is a strict client of the engine core, so these tests assert two
things at once: (a) HTTP behaviour — security gates (Host allowlist, CSRF
token), media serving with Range, JSON envelopes — and (b) that actions land
in the SAME truth files and events the CLI would have written.
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


def _request(server, path, *, method="GET", body=None, headers=None, host=None,
             raw=False):
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
            payload = resp.read()
            return resp.status, dict(resp.headers), (payload if raw else
                                                     json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _post(server, path, body, token=None, **kw):
    headers = {"X-Manju-Token": token if token is not None else server.token}
    return _request(server, path, method="POST", body=body, headers=headers, **kw)


def _wait_job(server, job_id, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _, data = _request(server, "/api/jobs")
        assert status == 200
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# ------------------------------------------------------------------- page


def test_page_serves_token_and_csp(gui):
    status, headers, body = _request(gui, "/", raw=True)
    assert status == 200
    html = body.decode("utf-8")
    assert "manju-token" in html and gui.token in html
    assert "script-src 'self'" in headers.get("Content-Security-Policy", "")
    assert headers.get("X-Frame-Options") == "DENY"
    # static assets exist and are non-trivial
    for path, ctype in (("/app.css", "text/css"), ("/app.js", "application/javascript")):
        s, h, b = _request(gui, path, raw=True)
        assert s == 200 and ctype in h["Content-Type"] and len(b) > 500


def test_foreign_host_rejected(gui):
    status, _, data = _request(gui, "/api/state", host="evil.example.com")
    assert status == 403
    assert "host" in data["error"].lower()
    # the bound host and plain localhost both pass
    assert _request(gui, "/api/state", host=f"127.0.0.1:{gui.port}")[0] == 200
    assert _request(gui, "/api/state", host="localhost")[0] == 200


# ------------------------------------------------------------------- state


def test_state_shape(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "hash-x")
    status, _, state = _request(gui, "/api/state")
    assert status == 200
    assert state["project"]["name"] == tmp_project.load_config().name
    assert state["next_step"]
    shot = next(s for s in state["shots"] if s["id"] == "S001")
    assert shot["state"] == "needs_selection"
    assert shot["dialogue"] == "这不可能。"
    assert shot["takes"][0]["name"] == take.name
    assert shot["takes"][0]["url"].startswith("/media/media/gen/S001/")
    assert state["jobs"] == []
    assert isinstance(state["events"], list)


# ------------------------------------------------------------------ select


def test_select_requires_token(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    status, _, data = _request(gui, "/api/select", method="POST",
                               body={"shot": "S001", "take": take.name})
    assert status == 403 and "token" in data["error"].lower()
    status, _, data = _post(gui, "/api/select", {"shot": "S001", "take": take.name},
                            token="wrong")
    assert status == 403


def test_select_writes_truth_and_event(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    status, _, data = _post(gui, "/api/select", {"shot": "S001", "take": take.name})
    assert status == 200 and data["ok"] is True
    assert tmp_project.load_shot("S001").status.selected_take == take.name
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "select" and event["actor"] == "human"
    assert event["detail"]["via"] == "gui"
    # unknown take -> 404 error envelope
    status, _, data = _post(gui, "/api/select", {"shot": "S001", "take": "take_99"})
    assert status == 404 and "take_99" in data["error"]


def test_select_refuses_when_selected_take_locked(gui, tmp_project, add_shot, make_take):
    """Round W (#39): GUI select now goes through the SAME checked write
    (select_take_checked) as CLI/MCP/board — a value-hash lock on
    status.selected_take must refuse it too, not just get bypassed here."""
    from manju.core.locks import seal_lock

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")

    def ensure_field(d):
        status = d.get("status")
        if not isinstance(status, dict):
            status = {}
        status.setdefault("selected_take", None)
        d["status"] = status

    tmp_project.update_shot_raw("S001", ensure_field)
    raw = tmp_project.load_shot_raw("S001")
    digest = seal_lock(raw, "status.selected_take")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("locked", {}).__setitem__(
            "status.selected_take", digest)
    )

    status, _, data = _post(gui, "/api/select", {"shot": "S001", "take": take.name})
    assert status == 409
    assert "error" in data
    assert tmp_project.load_shot("S001").status.selected_take is None


def test_select_refuses_when_build_locked(gui, tmp_project, add_shot, make_take):
    """Round W (#9): GUI select takes the process build lock too — a held
    lock (another CLI/MCP process mutating the same project) refuses."""
    from manju.runtime.buildlock import BuildLock

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    lock = BuildLock(tmp_project.root, actor="human").acquire()
    try:
        status, _, data = _post(gui, "/api/select", {"shot": "S001", "take": take.name})
        assert status == 409
        assert "error" in data
    finally:
        lock.release()
    assert tmp_project.load_shot("S001").status.selected_take is None


def test_lock_via_api(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/lock", {"shot": "S001", "field": "dialogue.text"})
    assert status == 200 and data["hash"]
    assert "dialogue.text" in tmp_project.load_shot_raw("S001")["locked"]
    # unlock is NOT on this surface, mirroring MCP (§5)
    status, _, _ = _post(gui, "/api/unlock", {"shot": "S001", "field": "dialogue.text"})
    assert status == 404


# ------------------------------------------------------------------- media


def test_media_range_and_traversal(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    rel = tmp_project.relpath(take.media_path)
    full = take.media_path.read_bytes()

    status, headers, body = _request(gui, f"/media/{rel}", raw=True)
    assert status == 200 and body == full
    assert headers["Accept-Ranges"] == "bytes"

    status, headers, body = _request(gui, f"/media/{rel}", raw=True,
                                     headers={"Range": "bytes=2-5"})
    assert status == 206 and body == full[2:6]
    assert headers["Content-Range"] == f"bytes 2-5/{len(full)}"

    etag = headers["ETag"]
    status, _, _ = _request(gui, f"/media/{rel}", raw=True,
                            headers={"If-None-Match": etag})
    assert status == 304

    # unsatisfiable range
    status, _, _ = _request(gui, f"/media/{rel}", raw=True,
                            headers={"Range": f"bytes={len(full) + 10}-"})
    assert status == 416

    # traversal + non-whitelisted trees are refused
    for bad in ("../outside.txt", ".manju/state.sqlite", "shots/S001.yaml",
                "events.jsonl", "media/../project.yaml"):
        status, _, _ = _request(gui, f"/media/{bad}")
        assert status in (403, 404), bad


# -------------------------------------------------------------------- jobs


def test_build_dry_run_is_synchronous(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # missing -> appears in the plan
    status, _, data = _post(gui, "/api/build", {"dry_run": True})
    assert status == 200 and data["dry_run"] is True
    plan = data["result"]["plan"]
    assert any(p["shot"] == "S001" for p in plan)
    status, _, data = _request(gui, "/api/jobs")
    assert data["jobs"] == []  # dry-run never becomes a job


def test_build_validation(gui):
    status, _, data = _post(gui, "/api/build", {"target": "nonsense"})
    assert status == 400 and "target" in data["error"]


def test_qc_job_lifecycle(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    _post(gui, "/api/select", {"shot": "S001", "take": take.name})
    status, _, data = _post(gui, "/api/qc", {})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "done"
    assert (tmp_project.reports_dir / "qc.json").exists()
    # fake media -> QC finds problems, but the job itself succeeded
    assert isinstance(job["result"]["errors"], int)


def test_voice_job_fails_cleanly_without_tts(gui, tmp_project, add_shot, monkeypatch):
    monkeypatch.delenv("MANJU_TTS_PROVIDER", raising=False)
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/voice", {"shot": "S001", "provider": "no_such_tts"})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "failed" and job["error"]


def _write_tts_manifest(tmp_path, monkeypatch):
    from manju.core.yamlio import write_yaml
    import manju.providers.registry as registry_mod

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "_providers" / "tts_x" / "provider.yaml", {
        "id": "tts_x", "type": "tts", "adapter": "generic_tts",
        "auth": {"key_env": "TTS_X_KEY"},
        "submit": {"url": "https://api.example.com/v1/tts",
                  "body_template": {"text": "{text}"}, "job_id_path": "$.data.task_id"},
        "tts": {"audio_url_path": "$.data.audio_url", "audio_format": "wav"},
        "cost": {"per_call": 5.0, "currency": "CNY"},
    })
    registry_mod._manifest_cache = None


def test_voice_endpoint_gated_like_cli(gui, tmp_project, add_shot, tmp_path, monkeypatch):
    """Goal 61: the GUI's single-shot voice action routes through the SAME
    §8.3 ask_before gate the CLI ``manju voice`` uses — a priced synthesis
    without an explicit ``assume_yes`` fails the job as waiting_user and
    spends nothing (previously this endpoint called the TTS provider
    directly with no gate at all)."""
    _write_tts_manifest(tmp_path, monkeypatch)
    add_shot(tmp_project, "S001")

    # no assume_yes -> waiting_user, nothing spent, no voice take written
    status, _, data = _post(gui, "/api/voice", {"shot": "S001"})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "failed"
    assert "waiting_user" in (job["error"] or "")
    assert tmp_project.voice_takes("S001") == []

    # assume_yes -> proceeds through the (offline, no real network) provider
    # call and fails cleanly for lack of a live transport — but the important
    # assertion is it got PAST the gate, not that TTS network mocking works.
    status2, _, data2 = _post(gui, "/api/voice", {"shot": "S001", "assume_yes": True})
    assert status2 == 202
    job2 = _wait_job(gui, data2["job"]["id"])
    # a real network call will fail in this offline test env; the gate itself
    # is proven by the FIRST call's waiting_user + the fact this one is not
    # rejected as waiting_user.
    assert "waiting_user" not in (job2.get("error") or "")


def test_voice_validation(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S002", dialogue={"speaker": "", "text": ""})
    status, _, data = _post(gui, "/api/voice", {"shot": "S002"})
    assert status == 400 and "dialogue" in data["error"]
    status, _, data = _post(gui, "/api/voice", {"shot": "NOPE"})
    assert status == 404


def test_jobs_are_serialized(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    _post(gui, "/api/select", {"shot": "S001", "take": take.name})
    ids = []
    for _ in range(2):
        _, _, data = _post(gui, "/api/qc", {})
        ids.append(data["job"]["id"])
    first, second = (_wait_job(gui, i) for i in ids)
    assert first["state"] == "done" and second["state"] == "done"
    # FIFO: the second job started at/after the first finished
    assert second["started"] >= first["finished"]


# ------------------------------------------------------------ shot editor


def test_shot_get_raw_yaml(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _request(gui, "/api/shot/S001")
    assert status == 200 and data["exists"] is True and data["in_index"] is True
    assert "dialogue" in data["yaml"]
    status, _, data = _request(gui, "/api/shot/S404")
    assert status == 200 and data["exists"] is False
    assert _request(gui, "/api/shot/..%2Fproject")[0] == 400


def test_shot_save_verbatim_and_event(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    text = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    edited = text.replace("这不可能。", "这怎么可能。") + "# 手写注释保持原样\n"
    status, _, data = _post(gui, "/api/shot/S001", {"yaml": edited})
    assert status == 200 and data["ok"] is True
    on_disk = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    assert "这怎么可能。" in on_disk and "# 手写注释保持原样" in on_disk
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "edit_shot" and event["detail"]["via"] == "gui"


def test_shot_save_bad_yaml_rejected(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/shot/S001", {"yaml": "id: [unclosed"})
    assert status == 400 and "YAML" in data["error"]
    status, _, data = _post(gui, "/api/shot/S001", {"yaml": "- just\n- a list\n"})
    assert status == 400


def test_shot_save_check_failure_reverts(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    before = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    broken = before.replace("convenience_store", "no_such_scene")
    status, _, data = _post(gui, "/api/shot/S001", {"yaml": broken})
    assert status == 409 and data["errors"]
    assert tmp_project.shot_path("S001").read_text(encoding="utf-8") == before


def test_shot_save_cannot_bypass_lock(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/lock", {"shot": "S001", "field": "dialogue.text"})[0] == 200
    before = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    tampered = before.replace("这不可能。", "改掉锁定的台词")
    status, _, data = _post(gui, "/api/shot/S001", {"yaml": tampered})
    assert status == 409
    assert tmp_project.shot_path("S001").read_text(encoding="utf-8") == before


def test_shot_create_new(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # gives the bible references a baseline
    new_yaml = (
        "id: S099\nscene: convenience_store\ncharacters: [linxia]\n"
        "dialogue: {speaker: linxia, text: 新镜头。}\nduration: auto\n"
    )
    status, _, data = _post(gui, "/api/shot/S099", {"yaml": new_yaml})
    assert status == 200 and data["created"] is True
    assert tmp_project.shot_path("S099").exists()
    # a GUI-created shot is indexed automatically (user intent: in the cut)
    assert "S099" in tmp_project.load_index().order
    assert not any("index.yaml" in w for w in data["warnings"])
    _, _, state = _request(gui, "/api/state")
    assert any(s["id"] == "S099" for s in state["shots"])


# ---------------------------------------------------------------- uploads


def _upload(server, name, payload, token=None):
    url = f"http://127.0.0.1:{server.port}/api/upload?name={name}"
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("X-Manju-Token", token if token is not None else server.token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_upload_roundtrip_and_collision(gui, tmp_project):
    payload = b"fake-upload-bytes" * 100
    status, data = _upload(gui, "clip.mp4", payload)
    assert status == 200 and data["imported"] == "media/imports/clip.mp4"
    assert (tmp_project.imports_dir / "clip.mp4").read_bytes() == payload
    # imports are never overwritten: same name -> _2
    status, data = _upload(gui, "clip.mp4", b"other")
    assert status == 200 and data["imported"] == "media/imports/clip_2.mp4"
    assert (tmp_project.imports_dir / "clip.mp4").read_bytes() == payload
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "import" and event["detail"]["via"] == "gui"


def test_upload_validation(gui):
    assert _upload(gui, "", b"x")[0] == 400
    assert _upload(gui, ".hidden", b"x")[0] == 400
    assert _upload(gui, "a%2F..%2Fevil.bin", b"x")[1]["imported"].startswith("media/imports/")
    status, data = _upload(gui, "clip.mp4", b"x", token="wrong")
    assert status == 403


def test_timeline_endpoint(gui, tmp_project):
    status, _, data = _request(gui, "/api/timeline")
    assert status == 200 and data["timeline"] is None


# ------------------------------------------------------- watch/lists/edit


def test_watch_longpoll(gui, tmp_project, add_shot):
    # first call with no fp returns immediately with changed=true
    status, _, data = _request(gui, "/api/watch?timeout=1")
    assert status == 200 and data["changed"] is True and data["fp"]
    fp = data["fp"]
    # nothing changed -> holds until timeout, comes back changed=false
    status, _, data = _request(gui, f"/api/watch?fp={fp}&timeout=1")
    assert status == 200 and data["changed"] is False and data["fp"] == fp
    # a truth edit flips the fingerprint
    add_shot(tmp_project, "S001")
    status, _, data = _request(gui, f"/api/watch?fp={fp}&timeout=5")
    assert data["changed"] is True and data["fp"] != fp


def test_proposals_listing(gui, tmp_project):
    status, _, data = _request(gui, "/api/proposals")
    assert status == 200 and data["proposals"] == []
    tmp_project.proposals_dir.mkdir(exist_ok=True)
    (tmp_project.proposals_dir / "0001_change_ending.md").write_text(
        "# 改结尾\n\n建议把 S006 台词换成留白。\n", encoding="utf-8")
    status, _, data = _request(gui, "/api/proposals")
    assert len(data["proposals"]) == 1
    assert "改结尾" in data["proposals"][0]["text"]


def test_index_reorder(gui, tmp_project, add_shot):
    for sid in ("S001", "S002", "S003"):
        add_shot(tmp_project, sid)
    status, _, data = _post(gui, "/api/index", {"order": ["S003", "S001", "S002"]})
    assert status == 200 and data["ok"] is True
    assert tmp_project.shot_ids() == ["S003", "S001", "S002"]
    # not a permutation -> rejected
    status, _, data = _post(gui, "/api/index", {"order": ["S001", "S002"]})
    assert status == 400 and "permutation" in data["error"]
    event = tail_events(tmp_project.root, 2)[0]
    assert event["action"] == "reorder"


def test_bible_editor_gated(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # S001 references scene convenience_store
    status, _, data = _request(gui, "/api/bible/scenes")
    assert status == 200 and "convenience_store" in data["yaml"]
    # removing a referenced scene must be reverted by the check gate
    status, _, data = _post(gui, "/api/bible/scenes", {"yaml": "other_scene: {name: x}\n"})
    assert status == 409
    assert "convenience_store" in (tmp_project.root / "bible" / "scenes.yaml").read_text(
        encoding="utf-8")
    # additive edit passes
    current = (tmp_project.root / "bible" / "scenes.yaml").read_text(encoding="utf-8")
    status, _, data = _post(gui, "/api/bible/scenes",
                            {"yaml": current + "alley: {name: 后巷}\n"})
    assert status == 200 and data["ok"] is True
    assert _request(gui, "/api/bible/nope")[0] == 404


def test_rules_editor(gui, tmp_project):
    status, _, current = _request(gui, "/api/rules")
    assert status == 200 and current["exists"] is True
    status, _, data = _post(gui, "/api/rules", {"yaml": "mode: [broken"})
    assert status == 400
    status, _, data = _post(gui, "/api/rules", {"yaml": current["yaml"]})
    assert status == 200 and data["ok"] is True


# ------------------------------------------------------------- doctor/git


def test_doctor_endpoint(gui):
    status, _, data = _request(gui, "/api/doctor")
    assert status == 200
    assert any(c["name"] == "ffmpeg" for c in data["checks"])
    assert isinstance(data["ok"], bool)


def test_git_endpoints_no_repo(gui):
    status, _, data = _request(gui, "/api/git/status")
    assert status == 200 and data["git"] is None  # conftest: git_init=False
    status, _, data = _post(gui, "/api/git/commit", {"message": ""})
    assert status == 400
    status, _, data = _post(gui, "/api/git/commit", {"message": "x"})
    assert status == 409  # not a repo -> commit fails with the git error


def test_git_commit_via_api(gui, tmp_project, add_shot):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_project.root, check=True)
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/git/commit", {"message": "首次提交 via gui"})
    assert status == 200 and data["ok"] is True and data["hash"]
    status, _, data = _request(gui, "/api/git/status")
    assert data["git"]["dirty"] is False
    status, _, data = _request(gui, "/api/git/log?n=5")
    assert any("首次提交" in e["subject"] for e in data["log"])
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "git_commit"


# ------------------------------------------------------------------ misc


def test_events_filtering(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    _post(gui, "/api/select", {"shot": "S001", "take": take.name})
    _post(gui, "/api/lock", {"shot": "S001", "field": "dialogue.text"})
    status, _, data = _request(gui, "/api/events?action=select")
    assert status == 200
    assert data["events"] and all(e["action"] == "select" for e in data["events"])
    status, _, data = _request(gui, "/api/events?actor=nobody")
    assert data["events"] == []


def test_check_and_explain_endpoints(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _request(gui, "/api/check")
    assert status == 200 and data["ok"] is True
    status, _, data = _request(gui, "/api/explain")
    assert status == 200 and any(s["shot"] == "S001" for s in data["shots"])


def test_unknown_routes(gui):
    assert _request(gui, "/api/nope")[0] == 404
    status, _, _ = _post(gui, "/api/nope", {})
    assert status == 404


def test_state_cache_by_fingerprint(gui, tmp_project, add_shot):
    _, _, first = _request(gui, "/api/state")
    assert first["fp"] and first["readonly"] is False
    _, _, second = _request(gui, "/api/state")
    assert second["fp"] == first["fp"]  # cache hit, same fingerprint
    add_shot(tmp_project, "S001")
    _, _, third = _request(gui, "/api/state")
    assert third["fp"] != first["fp"]
    assert any(s["id"] == "S001" for s in third["shots"])  # fresh payload


def test_workspace_mode(tmp_path):
    from manju.core.container import Project
    from manju.gui.server import discover_workspace

    ws = tmp_path / "studio"
    ws.mkdir()
    a = Project.create(ws / "甲", git_init=False)
    b = Project.create(ws / "乙", git_init=False)
    projects = discover_workspace(ws)
    assert set(projects) == {"甲", "乙"}

    server = create_server(a, host="127.0.0.1", port=0, workspace=projects)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, data = _request(server, "/api/projects")
        assert status == 200 and data["workspace"] is True
        active = [p for p in data["projects"] if p["active"]]
        assert len(active) == 1 and active[0]["slug"] == "甲"
        _, _, state = _request(server, "/api/state")
        assert state["workspace"]["active"] == "甲"
        # switch and confirm state follows
        status, _, data = _post(server, "/api/switch", {"slug": "乙"})
        assert status == 200 and data["ok"] is True
        _, _, state = _request(server, "/api/state")
        assert state["project"]["name"] == "乙"
        assert _post(server, "/api/switch", {"slug": "nope"})[0] == 404
    finally:
        server.shutdown()
        server.close()


def test_switch_outside_workspace_rejected(gui):
    status, _, data = _post(gui, "/api/switch", {"slug": "x"})
    assert status == 400 and "workspace" in data["error"]


def test_readonly_mode(tmp_project, add_shot, make_take):
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        add_shot(tmp_project, "S001")
        take = make_take(tmp_project, "S001", "h")
        status, _, data = _request(server, "/api/state")
        assert status == 200 and data["readonly"] is True
        status, _, data = _post(server, "/api/select",
                                {"shot": "S001", "take": take.name})
        assert status == 403 and "readonly" in data["error"]
        assert tmp_project.load_shot("S001").status.selected_take is None
    finally:
        server.shutdown()
        server.close()


# ----------------------------------------------------- review-round gaps


def test_build_forwards_assume_yes_into_job(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    _post(gui, "/api/select", {"shot": "S001", "take": take.name})
    status, _, data = _post(gui, "/api/build",
                            {"target": "qc", "gen": "off", "assume_yes": True})
    assert status == 202
    assert data["job"]["params"]["assume_yes"] is True
    _wait_job(gui, data["job"]["id"])


def test_readonly_blocks_upload_and_commit(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, data = _upload(server, "x.bin", b"abc")
        assert status == 403 and "readonly" in data["error"]
        status, _, data = _post(server, "/api/git/commit", {"message": "no"})
        assert status == 403
    finally:
        server.shutdown()
        server.close()


def test_upload_size_cap(gui, monkeypatch):
    from manju.gui import server as server_mod

    monkeypatch.setattr(server_mod._Handler, "_UPLOAD_MAX", 10)
    status, data = _upload(gui, "big.bin", b"x" * 64)
    assert status == 413 and "large" in data["error"]


def test_take_notes_roundtrip(gui, tmp_project, add_shot, make_take):
    """Director review notes (Frame.io-inspired): one YAML line per note."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    status, _, data = _post(gui, "/api/take-note",
                            {"shot": "S001", "take": take.name, "text": "太暗,重打光"})
    assert status == 200 and data["ok"] is True
    assert tmp_project.load_shot("S001").status.take_notes[take.name] == "太暗,重打光"
    _, _, state = _request(gui, "/api/state")
    card = next(s for s in state["shots"] if s["id"] == "S001")["takes"][0]
    assert card["note"] == "太暗,重打光"
    # empty text deletes; unknown take 404
    status, _, _ = _post(gui, "/api/take-note",
                         {"shot": "S001", "take": take.name, "text": ""})
    assert status == 200
    assert tmp_project.load_shot("S001").status.take_notes == {}
    assert _post(gui, "/api/take-note",
                 {"shot": "S001", "take": "take_99", "text": "x"})[0] == 404


def test_finals_version_stack_in_state(gui, tmp_project):
    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.final_dir / "final_v1.mp4").write_bytes(b"a" * 100)
    (tmp_project.final_dir / "final_v1.key.json").write_text("{}", encoding="utf-8")
    (tmp_project.final_dir / "final_v2.mp4").write_bytes(b"b" * 200)  # no key: crashed?
    _, _, state = _request(gui, "/api/state")
    finals = state["finals"]
    assert [f["name"] for f in finals] == ["final_v2.mp4", "final_v1.mp4"]
    assert finals[0]["has_key"] is False and finals[1]["has_key"] is True
    assert finals[0]["url"].startswith("/media/renders/final/")


def test_take_card_carries_seed(gui, tmp_project, add_shot, tmp_path):
    from manju.core.models import TakeSidecar

    add_shot(tmp_project, "S001")
    media = tmp_path / "s.mp4"
    media.write_bytes(b"x")
    tmp_project.register_take(
        "S001", media, TakeSidecar(provider="kenburns", spec_hash="h",
                                   params={"seed": 42}))
    _, _, state = _request(gui, "/api/state")
    card = next(s for s in state["shots"] if s["id"] == "S001")["takes"][0]
    assert card["seed"] == 42 and card["provider"] == "kenburns"


def test_conflict_409_carries_current_truth(gui, tmp_project, add_shot):
    """Conflict-banner contract: the 409 returns the reverted-to disk text so
    the editor can render buffer-vs-truth and offer re-apply (no refetch)."""
    add_shot(tmp_project, "S001")
    before = tmp_project.shot_path("S001").read_text(encoding="utf-8")
    broken = before.replace("convenience_store", "no_such_scene")
    status, _, data = _post(gui, "/api/shot/S001", {"yaml": broken})
    assert status == 409
    assert data["current"] == before


def test_schema_endpoint(gui):
    """Truth-file schemas over HTTP (editor validation groundwork, §12)."""
    status, _, data = _request(gui, "/api/schema")
    assert status == 200
    assert "ShotSpec" in data["schemas"] or any("hot" in k for k in data["schemas"])


def test_spend_endpoint(gui, tmp_project):
    """/api/spend serves the §8.3 事后 ledger report (empty project shape)."""
    status, _, data = _request(gui, "/api/spend")
    assert status == 200
    assert data["source"] == "empty" and data["total"] == 0


def test_validate_endpoint(gui, tmp_project, add_shot):
    """Keystroke validation: pure parse+model check, no write, no lock scan."""
    ok = "id: S001\nscene: convenience_store\nduration: auto\n"
    status, _, data = _post(gui, "/api/validate", {"kind": "shot", "yaml": ok})
    assert status == 200 and data["ok"] is True
    bad = ok.replace("duration: auto", "duration: not_a_number")
    status, _, data = _post(gui, "/api/validate", {"kind": "shot", "yaml": bad})
    assert status == 200 and data["ok"] is False and data["errors"]
    status, _, data = _post(gui, "/api/validate", {"kind": "rules", "yaml": "mode: compiled\n"})
    assert data["ok"] is True
    status, _, data = _post(gui, "/api/validate",
                            {"kind": "bible/scenes", "yaml": "x: not_a_mapping\n"})
    assert data["ok"] is False
    assert _post(gui, "/api/validate", {"kind": "nope", "yaml": "a: 1"})[0] == 400
    # validation never writes: shot file untouched
    add_shot(tmp_project, "S009")
    before = tmp_project.shot_path("S009").read_text(encoding="utf-8")
    _post(gui, "/api/validate", {"kind": "shot", "id": "S009",
                                 "yaml": "id: S009\nscene: nowhere\n"})
    assert tmp_project.shot_path("S009").read_text(encoding="utf-8") == before
