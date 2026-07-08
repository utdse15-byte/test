"""`manju gui` — the six server-rendered workbench pages (round S, S8b).

Companions to :mod:`tests.test_gui`: these assert the round-S pages complete the
capability matrix (docs/WORKBENCH.md) — 审片 review, 对比 compare, 素材库 library,
服务商 providers, 路由 routing, 体检 doctor — and that every mutation they offer
lands in the SAME truth files + events the CLI would write, never leaking a
secret and never bypassing the token/readonly gates.

Each page is server-rendered, so a plain GET carries the real fixture content;
the tests assert on that HTML directly (no browser). The ffmpeg-gated repair
test is skipped where ffmpeg is absent.
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

import pytest

from manju.core.events import tail_events
from manju.core.models import (
    TakeSidecar,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
)
from manju.gui.server import create_server

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

TS_A = "2026-07-06T10:00:00+00:00"
TS_MID = "2026-07-07T09:00:00+00:00"
TS_B = "2026-07-07T10:00:00+00:00"


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    """Point the user-level shelves (library / providers / routing) at tmp dirs
    so the pages never touch the real ~/.manju. Read at call time by the core,
    and the server runs in-process, so setting them here reaches the server."""
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "_library"))
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers"))
    monkeypatch.setenv("MANJU_ROUTING", str(tmp_path / "_user_routing.yaml"))
    monkeypatch.delenv("VIDEO_X_KEY", raising=False)


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, host=None, raw=False):
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


def _html(server, path, **kw):
    status, headers, body = _req(server, path, raw=True, **kw)
    return status, headers, (body.decode("utf-8") if isinstance(body, bytes) else body)


def _post(server, path, body, token=None, **kw):
    headers = {"X-Manju-Token": token if token is not None else server.token}
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


# ------------------------------------------------------------- helpers


def _select_take(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _write_qc(project, subject, message="太暗,重打光", suggestion="重打光"):
    project.reports_dir.mkdir(parents=True, exist_ok=True)
    (project.reports_dir / "qc.json").write_text(
        json.dumps({"ok": False, "items": [
            {"level": "warn", "area": "technical", "subject": subject,
             "message": message, "suggestion": suggestion}]}),
        encoding="utf-8")


def _write_frame(project, shot_id):
    frames = project.reports_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / f"{shot_id}.jpg").write_bytes(b"\xff\xd8\xff\xd9")  # tiny jpeg-ish


def _register(project, shot, provider):
    tmp = project.root / f"_src_{shot}_{provider}.mp4"
    tmp.write_bytes(b"take-bytes-" + f"{shot}{provider}".encode())
    info = project.register_take(shot, tmp, TakeSidecar(provider=provider, spec_hash="manual"))
    return info.name, project.relpath(info.media_path)


def _timeline(video):
    return Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="compiled"),
        fps=24, width=1080, height=1920,
        duration_ms=sum(c.duration_ms for c in video),
        tracks=TimelineTracks(video=video, captions=[], music=[], overlay=[]),
    )


def _write_final(project, version, *, timeline, key, created_at):
    project.final_dir.mkdir(parents=True, exist_ok=True)
    (project.final_dir / f"final_v{version}.mp4").write_bytes(b"final-" + str(version).encode())
    (project.final_dir / f"final_v{version}.key.json").write_text(
        json.dumps({"final_key": key, "target": "final", "created_at": created_at}),
        encoding="utf-8")
    if timeline is not None:
        (project.final_dir / f"final_v{version}.timeline.json").write_text(
            json.dumps(timeline.model_dump(), ensure_ascii=False), encoding="utf-8")


def _real_take(project, shot_id, seconds=1):
    src = project.root / f"_realsrc_{shot_id}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=duration={seconds}:size=64x48:rate=24",
         "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True)
    return project.register_take(shot_id, src,
                                 TakeSidecar(provider="test", spec_hash="h"))


_VIDEO_MANIFEST = {
    "id": "video_x",
    "type": "video",
    "adapter": "generic_cloud",
    "disabled": False,
    "capabilities": ["text_to_video"],
    "auth": {"key_env": "VIDEO_X_KEY"},
    "submit": {
        "url": "https://api.example.com/v1/videos",
        "body_template": {"prompt": "{prompt}", "api_key": "sk-super-secret-xyz-999"},
        "job_id_path": "$.data.task_id",
        "extra_headers": {"X-Api-Token": "bearer-super-secret-xyz-999"},
    },
    "poll": {
        "url": "https://api.example.com/v1/videos/{job_id}",
        "status_path": "$.data.status",
        "status_map": {"SUCCEEDED": "succeeded", "FAILED": "failed"},
        "result_url_path": "$.data.video_url",
    },
}
_SECRET = "super-secret-xyz-999"


def _write_provider(providers_dir, manifest=None):
    from manju.core.yamlio import write_yaml

    m = dict(manifest or _VIDEO_MANIFEST)
    write_yaml(providers_dir / m["id"] / "provider.yaml", m)


# ================================================================ 审片 review


def test_review_page_renders_with_content(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    alt = make_take(tmp_project, "S001", "h2")
    _select_take(tmp_project, "S001", take.name)
    _write_qc(tmp_project, "S001")
    _write_frame(tmp_project, "S001")

    status, _, body = _html(gui, "/review")
    assert status == 200
    assert "审片 Review" in body
    assert "S001" in body
    assert "太暗,重打光" in body and "重打光" in body   # QC finding + suggestion
    assert "/media/reports/frames/S001.jpg" in body       # QC mid-frame
    assert f"换用 {alt.name}" in body                     # prior take as alternate
    assert "好" in body and "弃" in body                  # verdict controls
    assert "已审 0 / 1" in body                            # progress meter, none noted yet
    assert "data-page=\"/review\"" in body


def test_review_verdict_writes_take_notes_and_event(gui, tmp_project, add_shot, make_take):
    """The review verdict/note buttons persist through the existing take_notes
    path (human truth) — one YAML line + one event, and the page then counts
    the shot as reviewed."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    _select_take(tmp_project, "S001", take.name)

    status, _, data = _post(gui, "/api/take-note",
                            {"shot": "S001", "take": take.name, "text": "好 · 通过"})
    assert status == 200 and data["ok"] is True
    assert tmp_project.load_shot("S001").status.take_notes[take.name] == "好 · 通过"
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "take_note" and event["detail"]["via"] == "gui"

    # the page now reflects the review as done
    _, _, body = _html(gui, "/review")
    assert "已审 1 / 1" in body


@pytest.mark.skipif(not _HAS_FFMPEG, reason="repair op needs ffmpeg/ffprobe")
def test_review_repair_op_creates_new_take(gui, tmp_project, add_shot):
    """A per-finding repair button calls the same repair_ops the CLI does,
    registering a NEW take (append-only) and recording a repair event."""
    add_shot(tmp_project, "S001")
    src = _real_take(tmp_project, "S001")
    _select_take(tmp_project, "S001", src.name)
    before = len(tmp_project.takes("S001"))

    status, _, data = _post(gui, "/api/repair", {"shot": "S001", "op": "trim", "ms": 100})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "done", job.get("error")
    assert job["result"]["op"] == "trim" and job["result"]["source_take"] == src.name

    takes = tmp_project.takes("S001")
    assert len(takes) == before + 1                    # append-only new take
    new_name = job["result"]["new_take"]
    assert any(t.name == new_name for t in takes)
    assert src.media_path.exists()                     # source untouched
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "repair" and event["detail"]["op"] == "trim"
    assert event["detail"]["via"] == "gui"


def test_review_repair_validates(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/repair", {"shot": "S001", "op": "nonsense"})[0] == 400
    assert _post(gui, "/api/repair", {"shot": "NOPE", "op": "trim"})[0] == 404
    # no selected take and none passed -> a clean 400, not a crash
    status, _, data = _post(gui, "/api/repair", {"shot": "S001", "op": "trim", "ms": 100})
    assert status == 400 and "take" in data["error"]


# =============================================================== 对比 compare


def test_compare_page_full_diff(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    s1, s1_src = _register(tmp_project, "S001", "kenburns")
    s2_old, s2_old_src = _register(tmp_project, "S002", "kenburns")
    s2_new, s2_new_src = _register(tmp_project, "S002", "comfyui")
    base = [VideoClip(shot="S001", take=s1, source=s1_src, start_ms=0, duration_ms=1200)]
    tl_a = _timeline(base + [VideoClip(shot="S002", take=s2_old, source=s2_old_src,
                                       start_ms=1200, duration_ms=1200)])
    tl_b = _timeline(base + [VideoClip(shot="S002", take=s2_new, source=s2_new_src,
                                       start_ms=1200, duration_ms=1200)])
    _write_final(tmp_project, 2, timeline=tl_a, key="sha256:aaa", created_at=TS_A)
    _write_final(tmp_project, 3, timeline=tl_b, key="sha256:bbb", created_at=TS_B)

    status, _, body = _html(gui, "/compare")
    assert status == 200
    assert "对比 Compare" in body
    assert "final_v2" in body and "final_v3" in body          # dropdowns
    assert "cmp-vid-a" in body and "cmp-vid-b" in body        # synced players
    assert "/media/renders/final/final_v3.mp4" in body
    assert "换 take" in body                                   # S002 take_changed row
    assert "未变" in body                                      # S001 unchanged row (dimmed)


def test_compare_degraded_pre_s_final(gui, tmp_project):
    # v2 has a snapshot, v3 does not -> the honest degraded note
    tl = _timeline([VideoClip(shot="S001", take="take_01", source="media/x.mp4",
                              start_ms=0, duration_ms=1000)])
    _write_final(tmp_project, 2, timeline=tl, key="sha256:aaa", created_at=TS_A)
    _write_final(tmp_project, 3, timeline=None, key="sha256:bbb", created_at=TS_B)

    status, _, body = _html(gui, "/compare")
    assert status == 200
    assert "degraded" in body
    assert "pre-S final" in body or "per-shot detail unavailable" in body


def test_compare_needs_two_finals(gui, tmp_project):
    status, _, body = _html(gui, "/compare")
    assert status == 200 and "需要至少两个 final" in body


# =============================================================== 素材库 library


def _seed_library(count=2):
    import os
    from pathlib import Path

    from manju.core.library import Library

    lib = Library()
    made = []
    for i in range(count):
        src = Path(os.environ["MANJU_LIBRARY"]).parent / f"_asset_{i}.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"asset-bytes-" + str(i).encode())
        entry = lib.add(src, tags=[f"tag{i}"], note=f"note {i}")["entry"]
        made.append(entry)
    return lib, made


def test_library_page_lists_assets(gui):
    lib, made = _seed_library(2)
    status, _, body = _html(gui, "/library")
    assert status == 200
    assert "素材库 Library" in body
    for entry in made:
        assert entry["name"] in body
    assert "tag0" in body and "note 1" in body
    assert "用到项目(refs)" in body                    # per-asset use affordance


def test_library_use_into_project_honors_no_overwrite(gui, tmp_project):
    from manju.core.library import _hex

    lib, made = _seed_library(1)
    hash8 = _hex(made[0]["hash"])[:8]

    status, _, data = _post(gui, "/api/lib/use", {"hash": hash8, "as": "refs"})
    assert status == 200 and data["ok"] is True
    first = tmp_project.resolve(data["dest"])
    assert first.exists()
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "lib_use" and event["detail"]["via"] == "gui"

    # a second use never overwrites the first — it lands with a _2 suffix
    original_bytes = first.read_bytes()
    status, _, data2 = _post(gui, "/api/lib/use", {"hash": hash8, "as": "refs"})
    assert status == 200
    assert data2["dest"] != data["dest"] and "_2" in data2["dest"]
    assert first.read_bytes() == original_bytes         # first copy intact


def test_library_page_shot_query_shows_suggestions(gui, tmp_project, add_shot):
    """round X agent XF: /library?shot=S001 surfaces the SAME deterministic
    tag/kind suggestions the shot lab offers, using the shared card markup
    (so 用到项目(refs) works out of the box, no new JS)."""
    from pathlib import Path

    from manju.core.library import Library

    add_shot(tmp_project, "S001")  # default scene/characters include "linxia"
    lib = Library()
    still = Path(os.environ["MANJU_LIBRARY"]).parent / "_linxia.png"
    still.parent.mkdir(parents=True, exist_ok=True)
    still.write_bytes(b"a-portrait")
    lib.add(still, tags=["linxia"])

    status, _, body = _html(gui, "/library?shot=S001")
    assert status == 200
    assert "为镜头 S001 推荐" in body
    assert "_linxia.png" in body
    assert "命中" in body
    assert "用到项目(refs)" in body   # the shared, already-wired adopt button


def test_library_page_shot_query_no_match_is_honest(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, body = _html(gui, "/library?shot=S001")
    assert status == 200
    assert "为镜头 S001 推荐" in body
    assert "没有匹配" in body


def test_library_page_unknown_shot_degrades_cleanly(gui, tmp_project):
    status, _, body = _html(gui, "/library?shot=NOPE")
    assert status == 200
    assert "未找到镜头" in body


def test_library_tag_and_note_roundtrip(gui):
    from manju.core.library import Library, _hex

    lib, made = _seed_library(1)
    hash8 = _hex(made[0]["hash"])[:8]
    assert _post(gui, "/api/lib/tag", {"hash": hash8, "tags": "a, b, a"})[0] == 200
    assert _post(gui, "/api/lib/note", {"hash": hash8, "note": "重配"})[0] == 200
    entry = Library().get(hash8)
    assert entry["tags"] == ["a", "b"] and entry["note"] == "重配"
    assert _post(gui, "/api/lib/note", {"hash": "deadbeef", "note": "x"})[0] == 404


# ============================================================== 服务商 providers


def test_providers_page_masks_secrets(gui, tmp_path):
    _write_provider(tmp_path / "_providers")
    status, _, body = _html(gui, "/providers")
    assert status == 200
    assert "服务商 Providers" in body
    assert "video_x" in body                            # provider id shown
    assert "VIDEO_X_KEY" in body                        # env-var NAME shown
    assert _SECRET not in body                          # value NEVER rendered
    assert "manju providers add" in body                # read-only add panel


def test_providers_toggle_roundtrips_disabled(gui, tmp_project, tmp_path):
    from manju.core.yamlio import read_yaml

    _write_provider(tmp_path / "_providers")
    path = tmp_path / "_providers" / "video_x" / "provider.yaml"
    assert read_yaml(path).get("disabled") in (False, None)

    status, _, data = _post(gui, "/api/providers/toggle", {"id": "video_x", "disabled": True})
    assert status == 200 and data["enabled"] is False
    assert read_yaml(path)["disabled"] is True
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "provider_toggle" and event["detail"]["enabled"] is False

    # and back
    status, _, data = _post(gui, "/api/providers/toggle", {"id": "video_x", "disabled": False})
    assert status == 200 and data["enabled"] is True
    assert read_yaml(path)["disabled"] is False
    # unknown provider -> 404
    assert _post(gui, "/api/providers/toggle", {"id": "nope", "disabled": True})[0] == 404


# =============================================================== 路由 routing


_ROUTING_YAML = (
    "strategy: default\n"
    "strategies:\n"
    "  my_rules:\n"
    "    rules:\n"
    "      - match: {shot_size: close_up}\n"
    "        use: kenburns\n"
    "    else: fallback\n"
)


def test_routing_page_renders(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    (tmp_project.root / "timeline" / "routing.yaml").write_text(_ROUTING_YAML, encoding="utf-8")
    status, _, body = _html(gui, "/routing")
    assert status == 200
    assert "路由 Routing" in body
    assert "my_rules" in body and "default" in body     # custom + built-in strategies
    assert "route-explain" in body or "解释" in body      # per-shot explain affordance


def test_routing_strategy_picker_writes_only_the_key(gui, tmp_project):
    path = tmp_project.root / "timeline" / "routing.yaml"
    path.write_text(_ROUTING_YAML, encoding="utf-8")

    status, _, data = _post(gui, "/api/routing/strategy", {"strategy": "my_rules"})
    assert status == 200 and data["strategy"] == "my_rules"

    text = path.read_text(encoding="utf-8")
    assert "strategy: my_rules" in text                 # only the strategy key flipped
    assert "strategy: default" not in text
    assert "my_rules:" in text and "use: kenburns" in text and "else: fallback" in text
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "route_strategy" and event["detail"]["strategy"] == "my_rules"


def test_routing_strategy_invalid_reverts(gui, tmp_project):
    path = tmp_project.root / "timeline" / "routing.yaml"
    path.write_text(_ROUTING_YAML, encoding="utf-8")
    status, _, data = _post(gui, "/api/routing/strategy", {"strategy": "no_such_strategy"})
    assert status == 400 and "reverted" in data["error"]
    assert path.read_text(encoding="utf-8") == _ROUTING_YAML   # untouched
    # a bad name shape is rejected before any write
    assert _post(gui, "/api/routing/strategy", {"strategy": "bad name!"})[0] == 400


def test_route_explain_endpoint(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _req(gui, "/api/route-explain?shot=S001")
    assert status == 200
    assert data["shot"] == "S001" and data["strategy"] == "default"
    assert "order" in data
    assert _req(gui, "/api/route-explain?shot=NOPE")[0] == 404
    assert _req(gui, "/api/route-explain")[0] == 400


# =============================================================== 体检 doctor


def test_doctor_page_renders(gui):
    status, _, body = _html(gui, "/doctor")
    assert status == 200
    assert "体检 Doctor" in body
    assert "ffmpeg" in body                              # a real probe row
    assert "dr-refresh" in body                          # refresh button


# ========================================================= assets + guards


def test_pages_assets_and_nav(gui):
    for path, ctype in (("/pages.css", "text/css"),
                        ("/pages.js", "application/javascript")):
        status, headers, body = _html(gui, path)
        assert status == 200 and ctype in headers["Content-Type"] and len(body) > 500
    # the SPA now links the pages and carries the nav for discovery
    status, _, spa = _html(gui, "/")
    assert status == 200 and "/pages.css" in spa and 'href="/review"' in spa


def test_pages_carry_csp_and_token(gui):
    status, headers, body = _html(gui, "/doctor")
    assert status == 200
    assert "script-src 'self'" in headers.get("Content-Security-Policy", "")
    assert headers.get("X-Frame-Options") == "DENY"
    assert gui.token in body                             # token embedded for POSTs


def test_dangerous_surface_still_guarded(gui, tmp_project, add_shot, make_take):
    # containment: unlock / gc / pack never exist on this surface (§5)
    assert _post(gui, "/api/unlock", {})[0] == 404
    assert _post(gui, "/api/gc", {})[0] == 404
    assert _req(gui, "/api/pack")[0] == 404
    # DNS-rebinding guard applies to the new pages too
    assert _html(gui, "/review", host="evil.example.com")[0] == 403
    # CSRF: page actions need the token
    add_shot(tmp_project, "S001")
    status, _, data = _req(gui, "/api/repair", method="POST",
                           body={"shot": "S001", "op": "trim", "ms": 100})
    assert status == 403 and "token" in data["error"].lower()
    assert _req(gui, "/api/routing/strategy", method="POST",
                body={"strategy": "default"})[0] == 403


def test_readonly_blocks_page_actions(tmp_project, tmp_path):
    _write_provider(tmp_path / "_providers")
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # pages still render (read-only), but every mutation is refused
        assert _html(server, "/providers")[0] == 200
        status, _, data = _post(server, "/api/providers/toggle",
                                {"id": "video_x", "disabled": True})
        assert status == 403 and "readonly" in data["error"]
        assert _post(server, "/api/routing/strategy", {"strategy": "cheapest"})[0] == 403
    finally:
        server.shutdown()
        server.close()
