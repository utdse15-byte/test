"""镜头实验室 Shot lab — the single-shot experiment page (round U, goal 13).

The page is a Pro-mode workbench over ONE shot with three panels (参考 / 提示词 /
候选). These tests drive it over real HTTP and assert:

* a plain GET renders all three panels from a fixture shot — the resolved refs
  with tier lineage, the budget allocation (selected vs 被省略), the honest
  需要视觉模型 cleanliness rows, the promptlab bundle + single-action checks, and
  the candidate cards;
* ``generation.prompt_override`` edits write + record an event + honour locks
  (409 + 中文), the same for the ref pick/reorder slot;
* the 按关键帧拆分 scaffold writes only behind the explicit apply POST, never on a
  page render, and refuses a sealed ``keyframes`` (409);
* the 草稿/成片 quality-toggle cost delta equals the plan estimators' numbers;
* 生成候选 goes through the jobs runner and the confirm-before-spend gate (a
  priced run without ``assume_yes`` fails as ``waiting_user`` — never silent);
* 存为参考 lands a frame in the shot's refs AND in the user library;
* the token / readonly guards protect every mutating action.

Hermetic: the user-level shelves (library / providers / routing) point at tmp
dirs so nothing touches the real ~/.manju, and the manifest cache is reset per
test. The ffmpeg-gated save-as-ref test is skipped where ffmpeg is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import manju.providers.registry as registry_mod
from manju.build.graph import _estimate_shot_cost, _target_duration_ms
from manju.core.events import tail_events
from manju.core.locks import seal_lock
from manju.core.models import TakeSidecar
from manju.core.yamlio import write_yaml
from manju.gui.server import create_server

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "_library"))
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers"))
    monkeypatch.setenv("MANJU_ROUTING", str(tmp_path / "_user_routing.yaml"))
    monkeypatch.delenv("REFCAP_KEY", raising=False)
    monkeypatch.delenv("CHEAP_KEY", raising=False)
    monkeypatch.delenv("PRICEY_KEY", raising=False)
    registry_mod._manifest_cache = None
    yield
    registry_mod._manifest_cache = None


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _cloud_manifest(pid, *, per_second=0.05, max_ref_images=None, max_duration_ms=None):
    data = {
        "id": pid, "type": "video", "adapter": "generic_cloud",
        "capabilities": ["image_to_video"], "disabled": False,
        "auth": {"key_env": f"{pid.upper()}_KEY"},
        "submit": {"url": "https://api.x/v", "body_template": {}, "job_id_path": "$.id"},
        "poll": {"url": "https://api.x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "cost": {"per_second": per_second, "currency": "CNY"},
    }
    limits = {}
    if max_ref_images is not None:
        limits["max_ref_images"] = max_ref_images
    if max_duration_ms is not None:
        limits["max_duration_ms"] = max_duration_ms
    if limits:
        data["limits"] = limits
    return data


def _write_provider(pid, data):
    root = Path(os.environ["MANJU_PROVIDERS_DIR"])
    write_yaml(root / pid / "provider.yaml", data)
    registry_mod._manifest_cache = None


def _fake_image(project, rel):
    p = project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake" * 8)
    return rel


def _req(server, path, *, method="GET", body=None, headers=None, host=None,
         raw=False, data_bytes=None):
    url = f"http://127.0.0.1:{server.port}{path}"
    if data_bytes is not None:
        data = data_bytes
    else:
        data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data_bytes is None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
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


def _html(server, path, **kw):
    status, headers, body = _req(server, path, raw=True, **kw)
    return status, headers, (body.decode("utf-8") if isinstance(body, bytes) else body)


def _post(server, path, body, token="__default__", **kw):
    headers = dict(kw.pop("headers", {}) or {})
    if token == "__default__":
        headers["X-Manju-Token"] = server.token
    elif token is not None:
        headers["X-Manju-Token"] = token
    return _req(server, path, method="POST", body=body, headers=headers, **kw)


def _wait_job(server, job_id, timeout=90.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _, data = _req(server, "/api/jobs")
        assert status == 200
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _real_mp4(tmp_path, name="clip.mp4", seconds=1):
    out = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc=duration={seconds}:size=128x72:rate=24",
         "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True)
    return out


# ============================================================ panels render


def test_page_renders_three_panels(gui, tmp_project, add_shot, make_take):
    """A plain GET carries all three panels: refs lineage + budget + cleanliness
    + prompt bundle + checks + candidate cards — every piece from the real core."""
    # a routed provider that caps refs at 1 → the budget view has an omission
    _write_provider("refcap", _cloud_manifest("refcap", max_ref_images=1))
    # two declared image refs (character primary + scene) so one is omitted
    write_yaml(tmp_project.root / "bible" / "characters.yaml", {
        "linxia": {"name": "林夏", "appearance": "短发黑色风衣",
                   "ref_image": _fake_image(tmp_project, "media/imports/linxia.png")}})
    write_yaml(tmp_project.root / "bible" / "scenes.yaml", {
        "convenience_store": {"name": "便利店", "description": "雨夜街角",
                              "ref_image": _fake_image(tmp_project, "media/imports/scene.png")}})
    add_shot(tmp_project, "S001", duration=6.0,
             generation={"provider": "refcap"},
             action={"main": "她走进房间然后转身再坐下"})  # >2 actions → a check
    take = make_take(tmp_project, "S001", "sha256:abc")

    status, _, html = _html(gui, "/lab?shot=S001")
    assert status == 200

    # three panels present
    assert "参考 References" in html
    assert "提示词 Prompts" in html
    assert "候选 Candidates" in html
    # (A) refs lineage: the tier + the ref path
    assert "media/imports/linxia.png" in html
    assert "bible" in html
    # (A) budget view: an omission with its 中文 impact line
    assert "被省略" in html
    assert "场景一致性将只依赖提示词描述" in html
    # (A) cleanliness: the honest needs-vision row for the scene ref
    assert "需要视觉模型" in html
    # (B) the promptlab bundle — the four prompt blocks + the picture content
    assert "视频提示词" in html and "反向提示词" in html
    assert "便利店" in html
    # (B) the single-action check + its split suggestion
    assert "拆分建议" in html
    # (C) the candidate card for the take
    assert take.name in html
    assert "生成候选" in html


def test_lab_data_endpoint_is_json(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _req(gui, "/api/lab/data?shot=S001")
    assert status == 200
    json.dumps(data)  # serialisable
    assert data["shot"] == "S001"
    assert "bundle" in data and "budget" in data and "cleanliness" in data
    assert data["bundle"]["video_prompt"]  # the build-path prompt


def test_lab_page_defaults_to_first_shot(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    status, _, html = _html(gui, "/lab")  # no ?shot=
    assert status == 200
    assert 'data-shot="S001"' in html


def test_lab_page_empty_project(gui, tmp_project):
    status, _, html = _html(gui, "/lab")
    assert status == 200
    assert "还没有镜头" in html


# ================================================== prompt_override edit + lock


def test_prompt_override_writes_and_events(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/lab/prompt-override",
                            {"shot": "S001", "text": "一个人在雨中奔跑"})
    assert status == 200 and data["ok"]
    assert tmp_project.load_shot("S001").generation.prompt_override == "一个人在雨中奔跑"
    ev = tail_events(tmp_project.root, 5)
    assert any(e["action"] == "edit_shot"
               and e["detail"].get("field") == "generation.prompt_override"
               for e in ev)
    # empty text clears the override
    _post(gui, "/api/lab/prompt-override", {"shot": "S001", "text": ""})
    assert tmp_project.load_shot("S001").generation.prompt_override is None


def test_prompt_override_respects_lock_409(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001", generation={"prompt_override": "原始提示词"})
    # seal a lock on generation.prompt_override (§5)
    raw = tmp_project.load_shot_raw("S001")
    digest = seal_lock(raw, "generation.prompt_override")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.__setitem__("locked", {"generation.prompt_override": digest}))

    status, _, data = _post(gui, "/api/lab/prompt-override",
                            {"shot": "S001", "text": "改写想突破锁定"})
    assert status == 409
    assert "已锁定" in data["error"]
    # truth is untouched
    assert tmp_project.load_shot("S001").generation.prompt_override == "原始提示词"


def test_prompt_override_unknown_shot_404(gui):
    status, _, _ = _post(gui, "/api/lab/prompt-override", {"shot": "S999", "text": "x"})
    assert status == 404


# ============================================================ refs pick/reorder


def test_refs_write_and_lock(gui, tmp_project, add_shot):
    _fake_image(tmp_project, "media/imports/a.png")
    _fake_image(tmp_project, "media/imports/b.png")
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/lab/refs",
                            {"shot": "S001",
                             "refs": ["media/imports/b.png", "media/imports/a.png"]})
    assert status == 200 and data["ok"]
    refs = tmp_project.load_shot("S001").generation.params.get("refs")
    assert refs == ["media/imports/b.png", "media/imports/a.png"]  # order preserved

    # lock the refs slot → a further write is refused with 中文
    raw = tmp_project.load_shot_raw("S001")
    digest = seal_lock(raw, "generation.params.refs")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.__setitem__("locked", {"generation.params.refs": digest}))
    status, _, data = _post(gui, "/api/lab/refs",
                            {"shot": "S001", "refs": ["media/imports/a.png"]})
    assert status == 409 and "已锁定" in data["error"]
    assert tmp_project.load_shot("S001").generation.params["refs"] == [
        "media/imports/b.png", "media/imports/a.png"]


def test_refs_bad_body_400(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, _ = _post(gui, "/api/lab/refs", {"shot": "S001", "refs": "notalist"})
    assert status == 400


# ==================================================== scaffold gated behind POST


def test_scaffold_only_writes_on_explicit_post(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "她走进房间然后转身再坐下"})
    # a page render must NOT scaffold keyframes (the field starts empty)
    _html(gui, "/lab?shot=S001")
    assert not tmp_project.load_shot_raw("S001").get("keyframes")

    status, _, data = _post(gui, "/api/lab/scaffold", {"shot": "S001", "n": 3})
    assert status == 200 and data["ok"]
    kfs = tmp_project.load_shot_raw("S001").get("keyframes")
    assert isinstance(kfs, list) and len(kfs) == 3
    assert kfs[0]["position"] == "start" and kfs[-1]["position"] == "end"
    assert any(e["action"] == "board_keyframes" for e in tail_events(tmp_project.root, 5))


def test_scaffold_refuses_sealed_keyframes(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "她走进房间然后坐下"})
    tmp_project.update_shot_raw(
        "S001", lambda d: d.__setitem__("locked", {"keyframes": "somehash"}))
    status, _, data = _post(gui, "/api/lab/scaffold", {"shot": "S001", "n": 4})
    assert status == 409 and "keyframes" in data["error"]
    assert not tmp_project.load_shot_raw("S001").get("keyframes")  # unchanged (empty)


def test_scaffold_n_bounds(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/lab/scaffold", {"shot": "S001", "n": 1})[0] == 400
    assert _post(gui, "/api/lab/scaffold", {"shot": "S001", "n": 99})[0] == 400


# ================================================ quality toggle cost delta


def test_quality_delta_equals_plan_estimators(gui, tmp_project, add_shot):
    """Draft (speed→cheapest) and Final (quality→priority) resolve to different
    providers; the endpoint's numbers equal the plan estimators used everywhere."""
    _write_provider("cheap", _cloud_manifest("cheap", per_second=0.01))
    _write_provider("pricey", _cloud_manifest("pricey", per_second=0.10))
    # override the default strategy: quality prefers pricey, cheapest picks cheap
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "default",
        "strategies": {"default": {"rules": [], "else": "fallback",
                                   "priority": ["pricey", "cheap"]}}})
    registry_mod._manifest_cache = None
    add_shot(tmp_project, "S001", duration=4.0)

    status, _, plan = _req(gui, "/api/lab/generate-plan?shot=S001")
    assert status == 200

    # independently price via the SAME estimators (graph._estimate_shot_cost)
    from manju.providers.routing import resolve

    shot = tmp_project.load_shot("S001")
    rules = tmp_project.load_rules()
    dur = _target_duration_ms(tmp_project, shot, rules)

    def priced(bias):
        prov = resolve(tmp_project, shot, else_bias=bias).chosen
        sc = shot.model_copy(deep=True)
        sc.generation.provider = prov
        cost, _cur = _estimate_shot_cost(sc, dur)
        return prov, float(cost)

    dprov, dcost = priced("cheapest")
    fprov, fcost = priced("quality")

    assert plan["draft"]["provider"] == dprov == "cheap"
    assert plan["final"]["provider"] == fprov == "pricey"
    assert plan["draft"]["estimated_cost"] == dcost
    assert plan["final"]["estimated_cost"] == fcost
    assert plan["delta"] == round(fcost - dcost, 6)
    assert fcost > dcost > 0  # final costs more than draft (a real tradeoff)


def test_quality_delta_no_providers_is_zero(gui, tmp_project, add_shot):
    # no cloud providers → both quality routes fall to free local → delta 0
    add_shot(tmp_project, "S001")
    _, _, plan = _req(gui, "/api/lab/generate-plan?shot=S001")
    assert plan["draft"]["estimated_cost"] == 0.0
    assert plan["delta"] == 0.0


# ============================================ generate: jobs runner + spend gate


@pytest.fixture
def priced(monkeypatch):
    """Every planned shot pretends to cost 5 CNY (as a real manifest would) —
    reaches the in-process server's redo path (same module)."""
    from manju.build import graph

    monkeypatch.setattr(graph, "_estimate_shot_cost", lambda shot, dur: (5.0, "CNY"))


def test_generate_confirm_gate_blocks_silent_spend(gui, tmp_project, add_shot, priced):
    """A priced generate WITHOUT assume_yes fails the job as waiting_user and
    spends nothing (§8.3) — the confirm-before-spend boundary."""
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/lab/generate",
                            {"shot": "S001", "quality": "draft", "assume_yes": False})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "failed"
    assert "waiting_user" in (job["error"] or "")
    assert not tmp_project.takes("S001")  # nothing spent or written


def test_generate_runs_through_jobs_runner(gui, tmp_project, add_shot, priced):
    """With an explicit yes the generate proceeds through the offline fallback
    chain on the jobs runner and mints a take (append-only)."""
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/lab/generate",
                            {"shot": "S001", "quality": "draft", "assume_yes": True})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "done", job.get("error")
    assert tmp_project.takes("S001")  # a candidate was generated
    assert job["result"]["quality"] == "draft"


def test_generate_bad_quality_400(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, _ = _post(gui, "/api/lab/generate", {"shot": "S001", "quality": "ultra"})
    assert status == 400


# ==================================================== save-as-ref (refs + lib)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="save-as-ref extracts a frame with ffmpeg")
def test_save_as_ref_into_refs_and_library(gui, tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    take = tmp_project.register_take(
        "S001", _real_mp4(tmp_path), TakeSidecar(provider="test", spec_hash="h"))

    # (1) into the shot's refs
    status, _, data = _post(gui, "/api/lab/save-ref",
                            {"shot": "S001", "take": take.name,
                             "at_ms": 100, "dest": "refs"})
    assert status == 200 and data["dest"] == "refs"
    ref_rel = data["ref"]
    assert tmp_project.resolve(ref_rel).is_file()  # durable frame in media/refs
    assert ref_rel in tmp_project.load_shot("S001").generation.params.get("refs", [])
    assert any(e["action"] == "save_ref" for e in tail_events(tmp_project.root, 5))

    # (2) into the user library
    status, _, data = _post(gui, "/api/lab/save-ref",
                            {"shot": "S001", "take": take.name,
                             "at_ms": 200, "dest": "library"})
    assert status == 200 and data["dest"] == "library"
    from manju.core.library import Library

    assert Library().get(data["hash"])  # the frame is now a library asset


def test_save_as_ref_bad_dest_400(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "h")
    status, _, _ = _post(gui, "/api/lab/save-ref",
                         {"shot": "S001", "take": "take_01", "dest": "nowhere"})
    assert status == 400


def test_save_as_ref_unknown_take_404(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, _ = _post(gui, "/api/lab/save-ref",
                         {"shot": "S001", "take": "take_99", "dest": "refs"})
    assert status == 404


# ================================================== token / readonly guards


def test_token_guard_on_mutations(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    for path, body in (
        ("/api/lab/prompt-override", {"shot": "S001", "text": "x"}),
        ("/api/lab/refs", {"shot": "S001", "refs": []}),
        ("/api/lab/scaffold", {"shot": "S001", "n": 3}),
        ("/api/lab/generate", {"shot": "S001", "quality": "draft"}),
        ("/api/lab/save-ref", {"shot": "S001", "take": "take_01", "dest": "refs"}),
    ):
        status, _, _ = _post(gui, path, body, token=None)  # no X-Manju-Token
        assert status == 403, path


def test_readonly_refuses_lab_mutations(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human",
                           readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # the page + read endpoints still work
        assert _html(server, "/lab?shot=S001")[0] == 200
        assert _req(server, "/api/lab/data?shot=S001")[0] == 200
        # every mutation is refused
        status, _, data = _post(server, "/api/lab/prompt-override",
                                {"shot": "S001", "text": "x"})
        assert status == 403 and "readonly" in data["error"]
    finally:
        server.shutdown()
        server.close()
