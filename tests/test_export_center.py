"""导出中心 Export center — the deliverable status engine + GUI page (round-U).

Three layers, ffmpeg-free (every artifact is fabricated: fake mp4 bytes,
hand-written ``.key.json`` sidecars, hand-written drafts, and a manual-mode
``timeline.json`` so the content-key recompute is deterministic):

- ENGINE (:mod:`manju.build.exportstatus`): each freshness verdict reachable —
  上新 / 待更新 / 缺失 / 有问题 / 待人工确认 / 已人工确认 — plus the human
  verification round-trip (mark → 已确认 → regenerate → 待人工确认);
- CLI (``manju exports`` / ``--json``): renders the SAME rows the engine returns;
- GUI (``/exports``): the page + chips over real HTTP, the token/readonly POST
  guards, generate-over-the-engine, verify round-trip, and a CLI==GUI==engine
  same-data assertion so the surfaces can never disagree.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

import pytest
from typer.testing import CliRunner

from manju.build.exportstatus import (
    ExportStatusError,
    Freshness,
    deliverables,
    deliverables_data,
    mark_verified,
)
from manju.cli import app
from manju.core.events import tail_events
from manju.core.models import (
    ShotSpec,
    TakeSidecar,
    Timeline,
    TimelineMeta,
    TimelineRules,
    TimelineTracks,
    VideoClip,
)
from manju.core.spec import compute_spec_hash
from manju.core.yamlio import write_yaml
from manju.gui.server import create_server

runner = CliRunner()

KINDS_IN_ORDER = ["final", "proxy", "srt", "ass", "otio",
                  "jianying", "capcut", "cover", "teaser"]


# ------------------------------------------------------------- fabricators


def _manual_timeline(project) -> Timeline:
    """A tiny hand-built timeline saved as human truth (manual mode) so the
    content-key recompute is exactly what we key against — no compiler, no
    ffprobe."""
    write_yaml(project.rules_path, TimelineRules(mode="manual").model_dump())
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp", mode="manual"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="take_01",
                      source="media/gen/S001/take_01.mp4", start_ms=0, duration_ms=2000)]),
    )
    project.save_timeline(tl)
    return tl


def _final_key(project, tl: Timeline) -> str:
    from manju.media.render import final_content_key

    ass = project.captions_dir / "captions.ass"
    return final_content_key(project, tl, ass_file=ass if ass.exists() else None,
                             target="final")


def _fab_final(project, key: str | None, *, version: int = 1, data: bytes = b"final") -> None:
    project.final_dir.mkdir(parents=True, exist_ok=True)
    (project.final_dir / f"final_v{version}.mp4").write_bytes(data)
    if key is not None:
        (project.final_dir / f"final_v{version}.key.json").write_text(
            json.dumps({"final_key": key, "target": "final"}), encoding="utf-8")


def _compiled_project(project) -> Timeline:
    """A real auto-compiled project (shot + selected take), captions exportable,
    timeline.json saved — enough for the auto-mode caption compare and the HTTP
    generate flow. Uses fake media + an explicit duration so no ffprobe runs."""
    from manju.media.probe import probe_duration_ms
    from manju.timeline.compiler import compile_timeline, gather_compile_input

    shot = ShotSpec.model_validate({
        "id": "S001", "scene": "convenience_store", "characters": ["linxia"],
        "dialogue": {"speaker": "linxia", "text": "这不可能。"}, "duration": 3})
    project.save_shot(shot)
    idx = project.load_index()
    if "S001" not in idx.order:
        idx.order.append("S001")
        project.save_index(idx)
    h = compute_spec_hash(shot, project.load_bible())
    src = project.root / "_src.mp4"
    src.write_bytes(b"fakevideo")
    info = project.register_take("S001", src, TakeSidecar(provider="test", spec_hash=h))
    project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", info.name))
    tl = compile_timeline(gather_compile_input(project, probe_duration_ms))
    project.save_timeline(tl)
    return tl


def _rows(project) -> dict[str, object]:
    return {r.kind: r for r in deliverables(project)}


# =============================================================== ENGINE


def test_empty_project_all_missing(tmp_project):
    rows = deliverables(tmp_project)
    assert [r.kind for r in rows] == KINDS_IN_ORDER
    assert all(r.freshness is Freshness.MISSING for r in rows)
    assert all(r.path is None and not r.openable for r in rows)
    # teaser row explains it is simply disabled, not broken
    teaser = {r.kind: r for r in rows}["teaser"]
    assert "未启用" in teaser.basis


def test_deliverables_data_shape_and_counts(tmp_project):
    data = deliverables_data(tmp_project)
    assert [d["kind"] for d in data["deliverables"]] == KINDS_IN_ORDER
    assert data["counts"] == {"missing": 9}
    row = data["deliverables"][0]
    # the JSON row carries both the machine slug and the 中文 chip word (§3)
    assert row["freshness"] == "missing" and row["freshness_zh"] == "缺失"
    for key in ("label", "basis", "openable", "version", "verifiable"):
        assert key in row


def test_final_up_to_date(tmp_project):
    tl = _manual_timeline(tmp_project)
    _fab_final(tmp_project, _final_key(tmp_project, tl))
    final = _rows(tmp_project)["final"]
    assert final.freshness is Freshness.UP_TO_DATE
    assert final.version == "v1" and final.openable
    assert "内容键匹配" in final.basis


def test_final_stale_on_wrong_key(tmp_project):
    _manual_timeline(tmp_project)
    _fab_final(tmp_project, "sha256:deadbeef")  # a key that cannot match the recompute
    final = _rows(tmp_project)["final"]
    assert final.freshness is Freshness.STALE
    assert "内容键不一致" in final.basis


def test_final_problematic_without_sidecar(tmp_project):
    _manual_timeline(tmp_project)
    _fab_final(tmp_project, None)  # a final with no .key.json → crashed render honesty
    final = _rows(tmp_project)["final"]
    assert final.freshness is Freshness.PROBLEMATIC
    assert "缺内容键" in final.basis


def test_final_problematic_zero_bytes(tmp_project):
    tl = _manual_timeline(tmp_project)
    _fab_final(tmp_project, _final_key(tmp_project, tl), data=b"")  # 0-byte final
    final = _rows(tmp_project)["final"]
    assert final.freshness is Freshness.PROBLEMATIC
    assert "0 字节" in final.basis


def test_final_higher_version_wins(tmp_project):
    """v10 beats v9 — the numeric resolver, surfaced as the version badge."""
    tl = _manual_timeline(tmp_project)
    key = _final_key(tmp_project, tl)
    _fab_final(tmp_project, key, version=9)
    _fab_final(tmp_project, key, version=10)
    final = _rows(tmp_project)["final"]
    assert final.version == "v10"


def test_caption_freshness_auto_mode(tmp_project):
    from manju.exporters.srt_ass import export_captions

    tl = _compiled_project(tmp_project)
    export_captions(tmp_project, tl)
    rows = _rows(tmp_project)
    assert rows["srt"].freshness is Freshness.UP_TO_DATE
    assert rows["ass"].freshness is Freshness.UP_TO_DATE
    assert "逐字一致" in rows["srt"].basis
    # editing the dialogue moves the compiled captions; the on-disk files lag
    tmp_project.update_shot_raw(
        "S001", lambda d: d["dialogue"].__setitem__("text", "完全不同的一句台词"))
    rows = _rows(tmp_project)
    assert rows["srt"].freshness is Freshness.STALE
    assert rows["ass"].freshness is Freshness.STALE


def test_caption_manual_mode_is_truth(tmp_project):
    # rules.captions.mode == "manual" makes captions.srt human truth (§3): it is
    # what renders, so it can never read as "stale" against the auto-compiler.
    rules = TimelineRules()
    rules.captions.mode = "manual"
    write_yaml(tmp_project.rules_path, rules.model_dump())
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.captions_dir / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n人工字幕\n\n", encoding="utf-8")
    srt = _rows(tmp_project)["srt"]
    assert srt.freshness is Freshness.UP_TO_DATE
    assert "真相" in srt.basis


def test_manual_ass_up_to_date_when_it_matches_current_srt_and_style(tmp_project):
    """round-W #62: manual-mode ASS reads up_to_date ONLY when it matches a
    recompile of the CURRENT captions.srt + CURRENT style — this is the
    honest-positive case: a real `export_captions` run just happened, so it
    IS what re-burning the current SRT/style produces (byte for byte)."""
    from manju.exporters.srt_ass import export_captions

    rules = TimelineRules()
    rules.captions.mode = "manual"
    write_yaml(tmp_project.rules_path, rules.model_dump())
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.captions_dir / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n人工字幕\n\n", encoding="utf-8")

    config = tmp_project.load_config()
    export_captions(tmp_project, Timeline(width=config.width, height=config.height))

    ass = _rows(tmp_project)["ass"]
    assert ass.freshness is Freshness.UP_TO_DATE
    assert "重烧" in ass.basis


def test_manual_ass_stale_when_srt_edited_without_reexport(tmp_project):
    """round-W #62: this is the bug the fix closes — manual mode used to mark
    ASS unconditionally up_to_date. Editing captions.srt WITHOUT a re-export
    must move the ASS row to 待更新, not leave it showing 上新."""
    rules = TimelineRules()
    rules.captions.mode = "manual"
    write_yaml(tmp_project.rules_path, rules.model_dump())
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.captions_dir / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n旧字幕\n\n", encoding="utf-8")
    # a stand-in ASS that does NOT match a recompile of the (edited) SRT below
    (tmp_project.captions_dir / "captions.ass").write_text(
        "[Script Info]\nold burn, never matches\n", encoding="utf-8")

    ass = _rows(tmp_project)["ass"]
    assert ass.freshness is Freshness.STALE
    assert "不一致" in ass.basis

    # SRT row is unaffected — it's still human truth, trivially up to date.
    srt = _rows(tmp_project)["srt"]
    assert srt.freshness is Freshness.UP_TO_DATE


def test_manual_ass_needs_manual_when_srt_missing(tmp_project):
    """No captions.srt to compare against (e.g. the ASS survived a manual
    SRT deletion) — honestly needs_manual, never a blind pass."""
    rules = TimelineRules()
    rules.captions.mode = "manual"
    write_yaml(tmp_project.rules_path, rules.model_dump())
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.captions_dir / "captions.ass").write_text("[Script Info]\n", encoding="utf-8")

    ass = _rows(tmp_project)["ass"]
    assert ass.freshness is Freshness.NEEDS_MANUAL


def test_otio_mtime_freshness(tmp_project):
    from manju.exporters.otio import export_otio

    tl = _compiled_project(tmp_project)
    otio = export_otio(tmp_project, tl)
    tl_path = tmp_project.timeline_path
    # OTIO newer than timeline.json → up-to-date
    os.utime(tl_path, (1000, 1000))
    os.utime(otio, (2000, 2000))
    assert _rows(tmp_project)["otio"].freshness is Freshness.UP_TO_DATE
    # timeline.json newer than OTIO → stale (mtime comparison)
    os.utime(tl_path, (3000, 3000))
    row = _rows(tmp_project)["otio"]
    assert row.freshness is Freshness.STALE
    assert "mtime" in row.basis


def _fab_cover(project, tl, *, key_ok: bool, sidecar: bool = True) -> str:
    """Fabricate a final + cover.png (+ its key.json). Returns the correct key."""
    from manju.core.hashing import hash_file
    from manju.media.packaging import cover_cache_key

    _fab_final(project, _final_key(project, tl))
    final = project.newest_final_path()
    correct = cover_cache_key(project.load_config(), hash_file(final), project.load_packaging().cover)
    dest = project.exports_dir / "packaging" / "cover.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"coverpng")
    if sidecar:
        dest.with_suffix(".key.json").write_text(
            json.dumps({"key": correct if key_ok else "sha256:wrong", "kind": "cover"}),
            encoding="utf-8")
    return correct


def test_cover_up_to_date_and_stale(tmp_project):
    tl = _manual_timeline(tmp_project)
    _fab_cover(tmp_project, tl, key_ok=True)
    assert _rows(tmp_project)["cover"].freshness is Freshness.UP_TO_DATE
    _fab_cover(tmp_project, tl, key_ok=False)
    row = _rows(tmp_project)["cover"]
    assert row.freshness is Freshness.STALE
    assert "cover key 不一致" in row.basis


def test_cover_problematic_without_sidecar(tmp_project):
    tl = _manual_timeline(tmp_project)
    _fab_cover(tmp_project, tl, key_ok=True, sidecar=False)
    row = _rows(tmp_project)["cover"]
    assert row.freshness is Freshness.PROBLEMATIC
    assert "sidecar" in row.basis


def _write_jianying_skeleton(project, *, missing_media: bool) -> object:
    """Hand-write a jianying skeleton draft. missing_media → lint flags it."""
    name = project.load_config().name
    draft = project.exports_dir / "jianying" / name / "draft_content.json"
    draft.parent.mkdir(parents=True, exist_ok=True)
    videos = [{"id": "v0", "path": "/nowhere/missing.mp4"}] if missing_media else []
    draft.write_text(json.dumps(
        {"materials": {"videos": videos, "audios": []}, "tracks": [], "duration": 0}),
        encoding="utf-8")
    return draft


def test_draft_needs_manual_then_verified_roundtrip(tmp_project):
    draft = _write_jianying_skeleton(tmp_project, missing_media=False)
    row = _rows(tmp_project)["jianying"]
    assert row.freshness is Freshness.NEEDS_MANUAL
    assert row.verifiable and "无法自证" in row.basis
    assert "在剪映" in row.open_hint

    rec = mark_verified(tmp_project, "jianying", "human", "在剪映里打开正常")
    assert rec["kind"] == "jianying" and rec["actor"] == "human"
    row = _rows(tmp_project)["jianying"]
    assert row.freshness is Freshness.VERIFIED
    assert row.verified_by == "human" and row.verified_note == "在剪映里打开正常"

    # regenerate the draft (bytes change) → the verified hash no longer matches
    draft.write_text(draft.read_text(encoding="utf-8") + "\n ", encoding="utf-8")
    row = _rows(tmp_project)["jianying"]
    assert row.freshness is Freshness.NEEDS_MANUAL
    assert "重生成" in row.basis and row.verifiable


def test_verified_flips_back_when_referenced_media_replaced(tmp_project, tmp_path):
    """round-W #65: the verification hash covers the draft JSON AND the
    referenced media files' fingerprint — a human's "yes, this opens
    correctly" must not survive a referenced take/proxy file being replaced
    IN PLACE while the draft JSON text stays byte-identical (the old bug: the
    verified hash was `hash_file(draft_json)` only)."""
    name = tmp_project.load_config().name
    media = tmp_path / "take_01.mp4"
    media.write_bytes(b"original-take-bytes")

    draft = tmp_project.exports_dir / "jianying" / name / "draft_content.json"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(json.dumps({
        "materials": {"videos": [{"id": "v0", "path": str(media)}], "audios": []},
        "tracks": [], "duration": 0,
    }), encoding="utf-8")

    row = _rows(tmp_project)["jianying"]
    assert row.freshness is Freshness.NEEDS_MANUAL

    mark_verified(tmp_project, "jianying", "human", "在剪映里打开正常,画面对得上")
    row = _rows(tmp_project)["jianying"]
    assert row.freshness is Freshness.VERIFIED

    # the draft JSON text is UNTOUCHED — only the referenced media's bytes
    # (and therefore its size/mtime) change, exactly the round-W #65 scenario.
    import time
    time.sleep(0.01)
    media.write_bytes(b"REPLACED take bytes - a completely different clip now")

    row = _rows(tmp_project)["jianying"]
    assert row.freshness is Freshness.NEEDS_MANUAL
    assert "引用媒体已变" in row.basis


def test_draft_problematic_on_missing_media(tmp_project):
    _write_jianying_skeleton(tmp_project, missing_media=True)
    row = _rows(tmp_project)["jianying"]
    assert row.freshness is Freshness.PROBLEMATIC
    assert "问题" in row.basis


def test_mark_verified_rejects_non_draft(tmp_project):
    with pytest.raises(ExportStatusError):
        mark_verified(tmp_project, "final", "human", "")


def test_mark_verified_rejects_missing_draft(tmp_project):
    with pytest.raises(ExportStatusError):
        mark_verified(tmp_project, "capcut", "human", "")


def test_verifications_jsonl_is_append_only(tmp_project):
    _write_jianying_skeleton(tmp_project, missing_media=False)
    mark_verified(tmp_project, "jianying", "human", "one")
    mark_verified(tmp_project, "jianying", "ai", "two")
    path = tmp_project.reports_dir / "verifications.jsonl"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2
    assert [x["actor"] for x in lines] == ["human", "ai"]
    for rec in lines:
        assert set(rec) == {"ts", "kind", "path_hash", "actor", "note"}
        assert rec["path_hash"].startswith("sha256:")


# =============================================================== CLI


def _run_in(project, *args):
    cwd = os.getcwd()
    os.chdir(project.root)
    try:
        return runner.invoke(app, list(args))
    finally:
        os.chdir(cwd)


def test_cli_exports_human_and_json(tmp_project):
    tl = _manual_timeline(tmp_project)
    _fab_final(tmp_project, _final_key(tmp_project, tl))

    res = _run_in(tmp_project, "exports")
    assert res.exit_code == 0, res.output
    assert "导出中心" in res.output
    assert "上新" in res.output and "缺失" in res.output

    res_json = _run_in(tmp_project, "exports", "--json")
    assert res_json.exit_code == 0
    data = json.loads(res_json.output)
    assert [d["kind"] for d in data["deliverables"]] == KINDS_IN_ORDER
    assert data["counts"].get("up_to_date") == 1


def test_cli_and_engine_agree(tmp_project):
    _manual_timeline(tmp_project)
    _fab_final(tmp_project, "sha256:deadbeef")
    res = _run_in(tmp_project, "exports", "--json")
    assert json.loads(res.output) == deliverables_data(tmp_project)


# =============================================================== HTTP


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _post(server, path, body, token="__use__"):
    tok = server.token if token == "__use__" else token
    headers = {} if tok is None else {"X-Manju-Token": tok}
    return _req(server, path, method="POST", body=body, headers=headers)


def _wait_job(server, job_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, _, data = _req(server, "/api/jobs")
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def test_page_renders_with_chips(gui):
    status, headers, html = _req(gui, "/exports", raw=True)
    assert status == 200
    assert "Content-Security-Policy" in headers
    text = html.decode("utf-8")
    assert "导出中心" in text
    assert 'href="/exports"' in text  # nav link present + active
    assert "st-missing" in text  # freshness chips rendered
    assert "词汇(§3)" in text  # the vocabulary legend
    # static assets serve
    assert _req(gui, "/exports.css", raw=True)[0] == 200
    assert _req(gui, "/exports.js", raw=True)[0] == 200


def test_api_exports_matches_engine(gui):
    _manual_timeline(gui.project)
    _fab_final(gui.project, "sha256:deadbeef")
    status, _, data = _req(gui, "/api/exports")
    assert status == 200
    assert data == deliverables_data(gui.project)


def test_post_requires_token(gui):
    for path, body in (("/api/exports/generate", {"kind": "srt"}),
                       ("/api/exports/verify", {"kind": "jianying"})):
        status, _, data = _post(gui, path, body, token=None)
        assert status == 403
        assert "Token" in data["error"]


def test_post_readonly_forbidden(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0,
                           actor="human", readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, data = _post(server, "/api/exports/generate", {"kind": "srt"})
        assert status == 403 and "readonly" in data["error"]
    finally:
        server.shutdown()
        server.close()


def test_generate_final_is_refused(gui):
    status, _, data = _post(gui, "/api/exports/generate", {"kind": "final"})
    assert status == 400
    assert "工作台" in data["error"]  # links to the plan-modal build instead


def test_generate_srt_over_http(gui):
    _compiled_project(gui.project)  # saves timeline.json the exporter reads
    status, _, data = _post(gui, "/api/exports/generate", {"kind": "srt"})
    assert status == 202
    job = _wait_job(gui, data["job"]["id"])
    assert job["state"] == "done", job.get("error")
    assert (gui.project.captions_dir / "captions.srt").exists()
    # the mutation landed an event with the gui actor + via marker
    ev = [e for e in tail_events(gui.project.root, 20) if e["action"] == "export"]
    assert ev and ev[-1]["actor"] == "human" and ev[-1]["detail"].get("via") == "gui"
    # and the row is now up-to-date over the same engine
    status, _, api = _req(gui, "/api/exports")
    srt = next(d for d in api["deliverables"] if d["kind"] == "srt")
    assert srt["freshness"] == "up_to_date"


def test_verify_draft_over_http_roundtrip(gui):
    _write_jianying_skeleton(gui.project, missing_media=False)
    status, _, data = _post(gui, "/api/exports/verify",
                            {"kind": "jianying", "note": "opens fine"})
    assert status == 200 and data["ok"]
    row = next(d for d in _req(gui, "/api/exports")[2]["deliverables"]
               if d["kind"] == "jianying")
    assert row["freshness"] == "verified" and row["verified_by"] == "human"
    ev = [e for e in tail_events(gui.project.root, 20) if e["action"] == "verify_draft"]
    assert ev and ev[-1]["detail"].get("via") == "gui"


def test_verify_missing_draft_over_http(gui):
    status, _, data = _post(gui, "/api/exports/verify", {"kind": "capcut"})
    assert status == 400 and "draft" in data["error"]
