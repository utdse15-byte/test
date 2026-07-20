"""Regression tests for the 2026-07-19 deep bug-hunt wave.

One behavioral test per fixed defect (tests/CONVENTIONS.md: assert on runtime
behavior — return values, state written, structured errors — never on source
text). Grouped by area; each test names the defect it pins RED-first.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from manju.core.container import ProjectError
from manju.core.models import TakeSidecar, VoiceTakeSidecar
from manju.core.yamlio import write_yaml


# ================================================================= core


def test_library_add_interrupted_copy_leaves_no_torn_blob(tmp_path, monkeypatch):
    """An interrupted copy must never leave a partial file AT the
    content-addressed blob name (it would be trusted + propagated forever)."""
    import shutil as _shutil

    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "lib"))
    from manju.core.library import Library

    src = tmp_path / "asset.png"
    src.write_bytes(b"real-bytes-of-the-asset")

    real_copy2 = _shutil.copy2

    def torn_copy2(s, d, **kw):
        Path(d).write_bytes(b"real-")  # half the bytes...
        raise OSError("simulated interruption mid-copy")

    monkeypatch.setattr("shutil.copy2", torn_copy2)
    lib = Library()
    with pytest.raises(OSError):
        lib.add(src, tags=["t"])

    blobs = [p for p in Path(lib.root).iterdir()
             if p.is_file() and p.suffix == ".png"]
    assert blobs == []  # complete-or-absent: no torn blob at ANY name
    assert lib.assets() == []  # and no index row pointing at nothing

    # retry with a working copy heals fully
    monkeypatch.setattr("shutil.copy2", real_copy2)
    entry = lib.add(src, tags=["t"])["entry"]
    blob = Path(lib.root) / entry["blob"]
    assert blob.read_bytes() == b"real-bytes-of-the-asset"


def test_suggest_from_library_matches_the_shots_own_id(tmp_path, monkeypatch,
                                                       tmp_project):
    """The docstring's documented match key set includes the shot's own id."""
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "lib"))
    from manju.core.library import Library, suggest_from_library
    from manju.core.models import ShotSpec

    still = tmp_path / "board.png"
    still.write_bytes(b"a-storyboard-frame")
    Library().add(still, tags=["S001"])

    rows = suggest_from_library(tmp_project, ShotSpec(id="S001"))
    assert len(rows) == 1 and "s001" in rows[0]["matched"]  # case-folded tags


def test_select_take_survives_bare_status_key(tmp_project, add_shot, make_take):
    """A hand-edited shot file with a bare ``status:`` (YAML None) must not
    crash the checked selected_take write."""
    from manju.core.writes import select_take_checked

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:x")
    raw = tmp_project.load_shot_raw("S001")
    raw["status"] = None                      # the hand-edit: a bare `status:`
    write_yaml(tmp_project.shot_path("S001"), raw)
    assert tmp_project.load_shot_raw("S001")["status"] is None

    result = select_take_checked(tmp_project, "S001", take.name,
                                 actor="human", via="cli")
    assert result["ok"] is True
    assert tmp_project.load_shot("S001").status.selected_take == take.name


def test_history_orders_mixed_utc_offsets_chronologically():
    """events.jsonl stamps +00:00, git %cI a local offset — the merged feed
    must sort by INSTANT, not by string."""
    from manju.core.history import HistoryRow, _ts_sort_key

    older_local = HistoryRow(ts="2026-07-19T18:00:00+08:00",  # 10:00 UTC
                             source="git", actor="git", text="older", detail={})
    newer_utc = HistoryRow(ts="2026-07-19T11:00:00+00:00",    # 11:00 UTC
                           source="event", actor="human", text="newer", detail={})
    rows = sorted([newer_utc, older_local], key=_ts_sort_key)
    assert [r.text for r in rows] == ["older", "newer"]


def test_takes_degrades_on_hand_broken_sidecars(tmp_project, add_shot, make_take):
    """Empty, non-mapping, missing-required-fields and unparseable sidecars
    each degrade to a visible TakeInfo carrying an error — never a crash of
    takes()/get_take() (check/status/GUI polling all ride on it)."""
    add_shot(tmp_project, "S001")
    good = make_take(tmp_project, "S001", "sha256:ok")
    tdir = tmp_project.takes_dir("S001")

    (tdir / "take_02.mp4").write_bytes(b"v2")
    (tdir / "take_02.yaml").write_text("", encoding="utf-8")            # empty
    (tdir / "take_03.mp4").write_bytes(b"v3")
    (tdir / "take_03.yaml").write_text("- just\n- a list\n", encoding="utf-8")
    (tdir / "take_04.mp4").write_bytes(b"v4")
    (tdir / "take_04.yaml").write_text("params: {seed: 1}\n", encoding="utf-8")
    (tdir / "take_05.mp4").write_bytes(b"v5")
    (tdir / "take_05.yaml").write_text("a: [unclosed\n", encoding="utf-8")  # bad YAML

    infos = tmp_project.takes("S001")
    by_name = {t.name: t for t in infos}
    assert set(by_name) == {good.name, "take_02", "take_03", "take_04", "take_05"}
    for broken in ("take_02", "take_03", "take_04", "take_05"):
        assert by_name[broken].error, broken  # surfaced, not silently tolerated
    assert by_name[good.name].error is None
    assert tmp_project.get_take("S001", "take_04") is not None


def test_voice_takes_newest_wins_past_take_99(tmp_project, add_shot):
    """voice_take_100 must sort AFTER voice_take_99 — 'newest wins' consumers
    read [-1] (lexicographic glob order broke at three digits)."""
    add_shot(tmp_project, "S001")
    tdir = tmp_project.takes_dir("S001")
    tdir.mkdir(parents=True, exist_ok=True)
    for n in (99, 100, 2):
        (tdir / f"voice_take_{n:02d}.wav").write_bytes(b"v%d" % n)
    voices = tmp_project.voice_takes("S001")
    assert [m.stem for m, _ in voices] == \
        ["voice_take_02", "voice_take_99", "voice_take_100"]
    assert tmp_project.next_voice_take_name("S001") == "voice_take_101"


# ================================================================ build


def _jy_draft_layout(project, base_doc, edited_doc, name="片名"):
    """The REAL exporter layout: exports/jianying/<name>/draft_content.json
    with the baseline at exports/jianying/.baseline/<name>.json."""
    draft_dir = project.root / "exports" / "jianying" / name
    draft_dir.mkdir(parents=True, exist_ok=True)
    edited = draft_dir / "draft_content.json"
    edited.write_text(json.dumps(edited_doc), encoding="utf-8")
    bl_dir = project.root / "exports" / "jianying" / ".baseline"
    bl_dir.mkdir(parents=True, exist_ok=True)
    (bl_dir / f"{name}.json").write_text(
        json.dumps({"document": base_doc, "compiled_from": ""}), encoding="utf-8")
    return edited


def _jy_doc(segments):
    return {
        "materials": {"videos": [], "texts": []},
        "tracks": [{"type": "video", "segments": segments}],
        "canvas_config": {}, "duration": 1_000_000, "fps": 24,
    }


def test_find_baseline_locates_the_jianying_draft_dir_baseline(tmp_project):
    """draft_content.json inside <name>/ maps to .baseline/<name>.json one
    level up — the old stem-only walk returned None for every JianYing export
    (silently disabling caption diff + conflict refusal)."""
    from manju.build.roundtrip import find_baseline

    base = _jy_doc([])
    edited = _jy_draft_layout(tmp_project, base, base)
    found = find_baseline(tmp_project, edited)
    assert found is not None
    assert found.name == "片名.json" and found.parent.name == ".baseline"


def _jy_caption_doc(texts: list[str]) -> dict:
    """Skeleton doc whose caption cues live as materials.texts + a text track
    (the real extractor's shape — _captions_from_jianying)."""
    mats = [{"id": f"t{i}", "content": txt} for i, txt in enumerate(texts)]
    segs = [{"material_id": f"t{i}",
             "target_timerange": {"start": i * 1_000_000, "duration": 900_000}}
            for i in range(len(texts))]
    doc = _jy_doc([])
    doc["materials"]["texts"] = mats
    doc["tracks"].append({"type": "text", "segments": segs})
    return doc


def test_roundtrip_caption_count_change_refuses_positional_diff(tmp_project):
    """Deleting a cue in the NLE must yield a NON-appliable conflict row, not
    shifted positional 'edits' that corrupt captions.srt on apply."""
    from manju.build.roundtrip import apply_roundtrip, plan_roundtrip

    base = _jy_caption_doc(["一", "二", "三"])
    edited = _jy_caption_doc(["一", "三"])          # a cue deleted in the NLE
    path = _jy_draft_layout(tmp_project, base, edited)
    plan = plan_roundtrip(tmp_project, path)

    cap_rows = [r for r in plan["rows"] if r["class"] == "caption_edit"]
    assert cap_rows, plan["rows"]
    assert all(r["state"] == "conflict" and not r["action"] for r in cap_rows)

    result = apply_roundtrip(tmp_project, plan, rows=list(range(len(plan["rows"]))),
                             actor="test")
    assert not any(a.get("class") == "caption_edit" for a in result["applied"])


def test_roundtrip_live_volume_and_unmute_beat_stale_stamps(tmp_project, add_shot):
    """A clip exported with a non-default gain/mute stamp: the user's LATER
    volume / un-mute edit lives in the JianYing fields — the frozen stamp
    must not shadow it into a no-diff."""
    from manju.build.roundtrip import plan_roundtrip

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    seg1 = {"manju": {"shot": "S001", "kind": "video", "source_gain_db": -3.0},
            "volume": 0.7079457843841379}
    seg2 = {"manju": {"shot": "S002", "kind": "video", "source_mute": True},
            "volume": 0.0, "muted": True}
    base = _jy_doc([json.loads(json.dumps(seg1)), json.loads(json.dumps(seg2))])

    e1 = json.loads(json.dumps(seg1))
    e1["volume"] = 1.5                     # user turned the clip UP
    e2 = json.loads(json.dumps(seg2))
    e2["muted"] = False                    # user un-muted
    e2["volume"] = 1.0
    edited = _jy_doc([e1, e2])

    path = _jy_draft_layout(tmp_project, base, edited)
    rows = plan_roundtrip(tmp_project, path)["rows"]
    vol = {r["evidence"]["shot"]: r for r in rows if r["class"] == "volume"}
    assert "S001" in vol, rows              # the gain edit is DETECTED
    assert vol["S001"]["evidence"]["to"]["gain_db"] == pytest.approx(3.52, abs=0.01)
    assert "S002" in vol, rows              # the un-mute is DETECTED
    assert vol["S002"]["evidence"]["to"]["mute"] is False


def test_roundtrip_untouched_stamped_export_plans_no_changes(tmp_project, add_shot):
    """Byte-stability guard for the fix above: re-planning an UNTOUCHED
    stamped export must not invent volume rows."""
    from manju.build.roundtrip import plan_roundtrip

    add_shot(tmp_project, "S001")
    seg = {"manju": {"shot": "S001", "kind": "video", "source_gain_db": -2.345},
           "volume": 0.7634}  # a human-ish approximation of -2.345dB
    base = _jy_doc([json.loads(json.dumps(seg))])
    edited = _jy_doc([json.loads(json.dumps(seg))])
    path = _jy_draft_layout(tmp_project, base, edited)
    rows = plan_roundtrip(tmp_project, path)["rows"]
    assert not [r for r in rows if r["class"] == "volume"], rows


def test_latest_verification_tolerates_torn_multibyte_tail(tmp_project):
    from manju.build.exportstatus import VERIFICATIONS_FILE, _latest_verification

    path = tmp_project.reports_dir / VERIFICATIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"kind": "jianying", "path_hash": "sha256:h", "ts": "t"})
    with open(path, "wb") as f:
        f.write(good.encode("utf-8") + b"\n")
        f.write('{"kind": "jianying", "note": "确认'.encode("utf-8")[:-1])  # torn 多字节
    rec = _latest_verification(tmp_project, "jianying")
    assert rec is not None and rec["path_hash"] == "sha256:h"


def test_mark_verified_raises_when_the_append_is_dropped(tmp_project, monkeypatch):
    """The record IS the deliverable: a silently dropped durable append must
    surface as a structured error, not a fake success."""
    from manju.build import exportstatus as ES

    name = tmp_project.load_config().name
    draft = tmp_project.root / "exports" / "jianying" / name / "draft_content.json"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("{}", encoding="utf-8")

    import manju.core.events as EV

    monkeypatch.setattr(EV, "append_jsonl_line",
                        lambda *a, **kw: False)  # lock busy / disk full
    with pytest.raises(ES.ExportStatusError):
        ES.mark_verified(tmp_project, "jianying", actor="human")


def test_plan_ingest_degrades_an_unreadable_file_to_a_row(tmp_project, tmp_path,
                                                          monkeypatch):
    from manju.build import ingest as ING

    ok_file = tmp_path / "ok.png"
    ok_file.write_bytes(b"fine")
    locked = tmp_path / "locked.mp4"
    locked.write_bytes(b"held-by-another-app")

    real_hash = ING.hash_file

    def flaky_hash(p):
        if Path(p).name == "locked.mp4":
            raise PermissionError(13, "sharing violation", str(p))
        return real_hash(p)

    monkeypatch.setattr(ING, "hash_file", flaky_hash)
    plan = ING.plan_ingest(tmp_project, [str(ok_file), str(locked)])
    actions = {Path(r.file).name: r.action for r in plan.rows}
    assert actions["locked.mp4"] == "skip_unreadable"
    assert actions["ok.png"] != "skip_unreadable"  # the batch survived


def test_director_unexpected_error_lands_failed_never_stuck_executing(
        tmp_project, monkeypatch):
    """A non-ActionError escaping a handler stranded the proposal in
    'executing' forever (execute refuses non-confirmed, reject refuses
    executing)."""
    from manju.build import director as D

    prop = D.propose(tmp_project, [{"type": "snapshot", "label": "cp"}])
    D.confirm(tmp_project, prop.id, actor="human")
    monkeypatch.setitem(D._DISPATCH, "snapshot",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        D.execute(tmp_project, prop.id, actor="human")
    reloaded = D.load_proposal(tmp_project, prop.id)
    assert reloaded.state == "failed"
    assert reloaded.outcome and reloaded.outcome["ok"] is False


def test_director_retime_factor_validation_is_structured(tmp_project, add_shot):
    from manju.build import director as D

    add_shot(tmp_project, "S001")
    with pytest.raises(D.DirectorError):
        D.propose(tmp_project, [{"type": "repair", "op": "retime",
                                 "shot": "S001", "factor": "fast"}])
    with pytest.raises(D.DirectorError):
        D.propose(tmp_project, [{"type": "repair", "op": "retime",
                                 "shot": "S001", "factor": float("nan")}])


def test_template_pack_rename_never_overwrites_prior_import(tmp_project, tmp_path):
    from manju.build.seriespack import export_template_pack, import_template_pack

    pack = tmp_path / "pack.zip"
    export_template_pack(tmp_project, pack, bible_entries=["linxia"])

    first = import_template_pack(tmp_project, pack, on_conflict="rename")
    assert first["imported"]["bible_entries"] == ["linxia_imported"]
    marker = {"note": "第一次导入的本地修改,绝不能被覆盖"}
    chars_path = tmp_project.root / "bible" / "characters.yaml"
    from manju.core.yamlio import read_yaml
    chars = read_yaml(chars_path)
    chars["linxia_imported"].update(marker)
    write_yaml(chars_path, chars)

    second = import_template_pack(tmp_project, pack, on_conflict="rename")
    assert second["imported"]["bible_entries"] == ["linxia_imported_2"]
    chars = read_yaml(chars_path)
    assert chars["linxia_imported"]["note"] == marker["note"]  # untouched
    assert "linxia_imported_2" in chars


def test_template_pack_secret_scan_covers_every_text_member(tmp_project, tmp_path):
    """A key pasted into a BIBLE ENTRY must refuse the export exactly like one
    in the manifest (only manifest.yaml was scanned before)."""
    from manju.build.seriespack import SeriesPackError, export_template_pack

    chars_path = tmp_project.root / "bible" / "characters.yaml"
    from manju.core.yamlio import read_yaml
    chars = read_yaml(chars_path)
    chars["linxia"]["note"] = "sk-" + "a1B2c3D4e5F6g7H8i9J0kLmN"
    write_yaml(chars_path, chars)
    with pytest.raises(SeriesPackError):
        export_template_pack(tmp_project, tmp_path / "leaky.zip",
                             bible_entries=["linxia"])


def test_color_check_with_no_recognized_axes_is_unknown_not_pass():
    from manju.build.conformance import UNKNOWN, _color_check

    row = _color_check(
        {"color": {"space": "bt709"}},                      # wrong key name
        {"color": {"color_known": True, "primaries": "bt709"}},
        "final_v1")
    assert row["status"] == UNKNOWN


def test_pullsheet_md_roundtrips_multiline_dialogue(tmp_project, add_shot):
    """Export → parse of an UNEDITED sheet must read back the exact truth —
    the lossy `\\n → space` flatten made every multi-line dialogue diff dirty
    (and --apply then rewrote it flattened)."""
    from manju.build.pullsheet import compile_pull_sheet_md, parse_sheet

    add_shot(tmp_project, "S001",
             dialogue={"speaker": "linxia", "text": "第一行\n第二行"})
    md = compile_pull_sheet_md(tmp_project)
    rows = parse_sheet(md, fmt="md")
    row = next(r for r in rows if r["shot_id"] == "S001")
    assert row["dialogue"] == "第一行\n第二行"


# ================================================================== gui


def test_gui_state_transient_oserror_never_wipes_the_store(tmp_path, monkeypatch):
    from manju.gui import userstate as US

    store = tmp_path / "gui_state.json"
    monkeypatch.setenv("MANJU_GUI_STATE", str(store))
    US.update_gui_state(lambda s: s.__setitem__("mode", "pro"))
    original = store.read_text(encoding="utf-8")

    def denied(path, tries=3, delay_s=0.0):
        raise PermissionError(13, "held by antivirus", str(path))

    monkeypatch.setattr(US, "_read_with_retry", denied)
    with pytest.raises(PermissionError):
        US.update_gui_state(lambda s: s.__setitem__("mode", "beginner"))
    assert store.read_text(encoding="utf-8") == original  # NOT clobbered

    monkeypatch.undo()
    monkeypatch.setenv("MANJU_GUI_STATE", str(store))
    assert US.load_gui_state()["mode"] == "pro"


def test_gui_state_real_corruption_is_quarantined(tmp_path, monkeypatch):
    from manju.gui import userstate as US

    store = tmp_path / "gui_state.json"
    monkeypatch.setenv("MANJU_GUI_STATE", str(store))
    store.write_text("{not json", encoding="utf-8")
    state = US.load_gui_state()
    assert state.get("onboarding_dismissed") == {}
    backups = list(tmp_path.glob("gui_state.json.corrupt.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"


def test_jobs_log_torn_multibyte_tail_does_not_brick_the_runner(tmp_project):
    from manju.gui.jobs import JobRunner

    log = tmp_project.root / ".manju" / "jobs.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"id": "j1", "kind": "build", "state": "running",
                       "ts": "2026-07-19T00:00:00+00:00"}, ensure_ascii=False)
    with open(log, "wb") as f:
        f.write(good.encode("utf-8") + b"\n")
        f.write('{"id": "j2", "kind": "构建'.encode("utf-8")[:-1])  # torn CJK tail
    runner = JobRunner(tmp_project.runtime_dir)  # must not raise
    try:
        assert any(rec.get("id") == "j1" for rec in runner.interrupted())
    finally:
        runner.shutdown(timeout=1)


def test_create_page_refuses_editor_over_non_utf8_story(tmp_project):
    """A GBK story file renders a read-only refusal — never an empty/mangled
    textarea whose save would destroy the truth (errors="ignore" used to strip
    every Chinese byte on the way into the editor)."""
    from manju.gui import create_page as CP

    files = CP.stage_files()
    sid, rel = next((k, v) for k, v in files.items() if v.endswith("script.md"))
    story = tmp_project.root / rel
    story.parent.mkdir(parents=True, exist_ok=True)
    story.write_bytes("# 大纲\n她走进便利店。".encode("gb2312"))

    html = CP._editor(tmp_project, {"id": sid, "cn": "剧本"}, files, active=True)
    assert "无法按 UTF-8 读取" in html
    assert "<textarea" not in html            # no way to save over the truth
    # a HEALTHY story file still gets its textarea (the refusal is targeted)
    story.write_text("# 大纲\n她走进便利店。", encoding="utf-8")
    ok_html = CP._editor(tmp_project, {"id": sid, "cn": "剧本"}, files, active=True)
    assert "<textarea" in ok_html and "她走进便利店" in ok_html


def test_shutdown_upgrade_to_cancel_actually_cancels(tmp_project):
    from manju.gui.jobs import JobRunner
    from manju.gui.shutdown import AppShutdownCoordinator

    class _Server:
        pass

    import threading

    srv = _Server()
    srv.closing = threading.Event()
    runner = JobRunner(tmp_project.runtime_dir)
    srv.runner = runner
    srv.app_status = lambda: {}
    srv.shutdown = lambda: None
    srv.server_close = lambda: None

    started = threading.Event()
    release = threading.Event()

    def slow(job):
        started.set()
        release.wait(timeout=10)
        return {"ok": True}

    job = runner.submit("build", {}, slow)
    assert started.wait(timeout=5)

    coord = AppShutdownCoordinator(srv)
    coord.request("after_current")            # graceful quit: job keeps running
    assert not runner.get(job.id).cancel_event.is_set()
    coord.request("cancel_running")           # the upgrade must REALLY cancel
    assert runner.get(job.id).cancel_event.is_set()
    release.set()
    runner.shutdown(timeout=5)


# ============================================================== providers


def test_prompt_compiles_with_mixed_type_bible_keys(tmp_project, add_shot):
    from manju.providers.prompt import compile_prompt

    shot = add_shot(tmp_project, "S001")
    bible = tmp_project.load_bible()
    bible["convenience_store"][1] = "一个手滑写进来的数字键"
    bible["convenience_store"][True] = "还有布尔键"
    text = compile_prompt(shot, bible)          # must not raise
    assert "便利店" in text


def test_dry_run_evidence_never_mints_config_valid_for_a_bad_config():
    from manju.providers import qualification as Q

    ev = Q._dry_run_evidence({"config_ok": False}, {}, 0.0, None, "2026-07-19")
    assert ev["level"] == Q.UNTESTED
    ok = Q._dry_run_evidence({"config_ok": True}, {}, 0.0, None, "2026-07-19")
    assert ok["level"] == Q.DRY_RUN_VALID


def test_run_evidence_ladder_is_monotonic_over_failed_artifact():
    """recovery passing must not leapfrog a FAILED artifact check to
    RECOVERY_PASSED (nor PRODUCTION_READY on a real transport)."""
    from manju.providers import qualification as Q

    run = {"artifact_checks": {"ok": False}, "recovery": {"passed": True},
           "request_digest": "sha256:r", "evidence_refs": [], "artifact": None,
           "receipt": {}}
    ev = Q._run_evidence({}, {}, run, 0.0, None, "real", "2026-07-19")
    assert ev["level"] == Q.CANARY_SUBMIT_PASSED
    run_ok = dict(run, artifact_checks={"ok": True})
    ev2 = Q._run_evidence({}, {}, run_ok, 0.0, None, "real", "2026-07-19")
    assert ev2["level"] == Q.PRODUCTION_READY


def test_qualification_evidence_stream_counts_torn_tail_as_malformed(tmp_project):
    from manju.providers.qualification import _read_evidence_stream

    path = tmp_project.root / "events.jsonl"
    with open(path, "wb") as f:
        f.write('{"action": "qualification_evidence", "detail": {"provider_id": "p"}}\n'
                .encode("utf-8"))
        f.write('{"action": "qualification_evidence", "detail": {"note": "确认'
                .encode("utf-8")[:-1])
    records, malformed = _read_evidence_stream(tmp_project)  # must not raise
    assert len(records) == 1 and malformed >= 1


def test_broken_routing_yaml_is_a_structured_provider_failure(tmp_project,
                                                              add_shot):
    from manju.providers.base import GenerationRequest, ProviderFailure
    from manju.providers.registry import generate_with_fallback
    from manju.providers.submission import NOT_DISPATCHED

    shot = add_shot(tmp_project, "S001")
    write_yaml(tmp_project.root / "timeline" / "routing.yaml",
               {"strategy": "no_such_strategy"})
    req = GenerationRequest(project=tmp_project, shot=shot,
                            bible=tmp_project.load_bible(),
                            spec_hash="sha256:t", duration_ms=1000, candidates=1)
    with pytest.raises(ProviderFailure) as exc:
        generate_with_fallback(req, ["local_slate"])
    assert "routing.yaml" in str(exc.value)
    assert exc.value.disposition == NOT_DISPATCHED  # pre-send: never fail-closed


# ================================================================ media


def test_slate_cache_survives_a_failed_encode(tmp_project, monkeypatch):
    """A dead ffmpeg must leave the content-addressed slate name ABSENT —
    a torn cache entry was reused forever by the size>0 gate."""
    from manju.media import audition as A

    def dying_ffmpeg(args, **kw):
        out = Path(args[-1])
        out.write_bytes(b"partial")           # tool died mid-write
        raise A.MediaError("killed")

    monkeypatch.setattr(A, "run_ffmpeg", dying_ffmpeg)
    with pytest.raises(A.MediaError):
        A.ensure_slate(tmp_project, "S001", duration_ms=500,
                       width=64, height=64, fps=12)
    slates = list((tmp_project.root / ".manju" / "audition" / "slates").glob("*.mp4"))
    assert slates == []

    def good_ffmpeg(args, **kw):
        Path(args[-1]).write_bytes(b"full-video")

    monkeypatch.setattr(A, "run_ffmpeg", good_ffmpeg)
    dest = A.ensure_slate(tmp_project, "S001", duration_ms=500,
                          width=64, height=64, fps=12)
    assert dest.read_bytes() == b"full-video"


def test_bed_fade_in_is_applied_before_the_delay():
    """afade st=0 AFTER adelay ramps the inserted silence — a bed with
    start_ms>0 entered at full volume. The fade must precede the delay."""
    from manju.core.models import AudioClip, Timeline, TimelineTracks
    from manju.media.render import _build_audio_graph

    tl = Timeline(duration_ms=4000, tracks=TimelineTracks(
        music=[AudioClip(source="media/imports/bgm.wav", start_ms=2000,
                         duration_ms=2000, fade_in_ms=500)]))

    class _P:  # resolve() only — the graph builder never opens the file
        def resolve(self, rel):
            return Path("/x") / rel

    _inp, stmts, _out = _build_audio_graph(tl, _P(), target="proxy", total_s=4.0)
    chain = next(s for s in stmts if "afade=t=in" in s)
    assert chain.index("afade=t=in") < chain.index("adelay=2000")


# =============================================================== qc / cli


def test_multilocale_merged_report_carries_every_locale(tmp_project, monkeypatch):
    from manju.qc.checks import QCItem, QCReport
    from manju.qc.multilocale import run_multilocale_qc

    (tmp_project.root / "locales" / "en").mkdir(parents=True)
    (tmp_project.root / "locales" / "en" / "lines.yaml").write_text(
        "S001: {text: hello}\n", encoding="utf-8")
    (tmp_project.root / "locales" / "ja").mkdir(parents=True)
    (tmp_project.root / "locales" / "ja" / "lines.yaml").write_text(
        "S001: {text: こんにちは}\n", encoding="utf-8")
    en_final = tmp_project.final_dir / "locales" / "en"
    en_final.mkdir(parents=True)
    (en_final / "final_v1.mp4").write_bytes(b"x")   # en rendered; ja NOT

    def runner(project, timeline, *, deep, final_path):
        rep = QCReport()
        rep.items.append(QCItem("error", "content", "S001", "must_show 违背"))
        return rep

    ml = run_multilocale_qc(tmp_project, qc_runner=runner)
    merged = ml.merged_report()
    messages = [it.message for it in merged.items]
    assert any(m.startswith("[en] ") for m in messages)
    assert any("missing_final" in m and "[ja]" in m for m in messages)
    assert merged.ok is False


def test_must_show_auto_safe_item_maps_to_an_auto_repair(tmp_project):
    """qc.md promises `repair --auto` for an auto_safe item — the derived plan
    must agree instead of mapping it to non-auto human_review."""
    from manju.qc.checks import QCItem, QCReport
    from manju.qc.report import write_reports

    rep = QCReport()
    rep.items.append(QCItem(
        "error", "content", "S001",
        "must_show 违背(机检 OCR 未找到 '霓虹灯'): 霓虹灯",
        suggestion="redo(换 seed/换 provider)或降级;或 agent 看图复核",
        auto_safe=True))
    write_reports(tmp_project, rep)
    from manju.core.yamlio import read_yaml
    plan = read_yaml(tmp_project.reports_dir / "repair_plan.yaml")
    actions = plan["actions"] if isinstance(plan, dict) else plan
    row = next(a for a in actions if a["shot"] == "S001")
    assert row["auto_safe"] is True and row["action"] == "redo_new_seed"


# ============================================================== exporters


def test_fcpxml_refuses_out_of_project_audio_sources(tmp_project, add_shot,
                                                     make_take, tmp_path):
    from manju.core.models import AudioClip, Timeline, TimelineTracks, VideoClip
    from manju.exporters.fcpxml import export_fcpxml

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:x")
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"leak")
    tl = Timeline(duration_ms=1000, tracks=TimelineTracks(
        video=[VideoClip(shot="S001", take=take.name,
                         source=tmp_project.relpath(take.media_path),
                         start_ms=0, duration_ms=1000)],
        ambient=[AudioClip(source=str(outside), start_ms=0, duration_ms=1000)]))
    with pytest.raises(ProjectError):
        export_fcpxml(tmp_project, tl)


def test_srt_and_vtt_never_emit_a_cue_splitting_blank_line():
    from manju.core.models import CaptionLine, Timeline, TimelineTracks
    from manju.exporters.srt_ass import compile_srt, compile_vtt
    from manju.providers.asr import parse_srt

    tl = Timeline(duration_ms=2000, tracks=TimelineTracks(
        captions=[CaptionLine(start_ms=0, end_ms=1500,
                              text="第一行\n\n第二行")]))
    srt = compile_srt(tl)
    cues = parse_srt(srt)
    assert len(cues) == 1
    assert cues[0].text == "第一行\n第二行"
    vtt = compile_vtt(tl)
    body = vtt.split("-->", 1)[1]
    assert "\n\n" not in body.strip("\n")


def test_caption_style_non_numeric_size_fails_as_one_clean_line():
    from manju.exporters.srt_ass import compile_ass

    from manju.core.models import CaptionLine, Timeline, TimelineTracks

    tl = Timeline(duration_ms=2000, tracks=TimelineTracks(
        captions=[CaptionLine(start_ms=0, end_ms=1000, text="你好")]))
    with pytest.raises(ProjectError) as exc:
        compile_ass(tl, width=1920, height=1080, style={"size": "大"})
    assert "size" in str(exc.value)


# ============================================================== buildlock


def test_buildlock_steals_a_dead_holders_lock(tmp_project):
    import subprocess
    import sys

    from manju.runtime.buildlock import BuildLock

    proc = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, encoding="utf-8")
    dead_pid = int(proc.stdout.strip())

    lock_dir = tmp_project.root / ".manju" / "locks"
    lock = BuildLock(tmp_project.root, actor="human")
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    import socket
    lock.path.write_text(json.dumps({
        "pid": dead_pid, "actor": "engine",
        "started": "2026-07-19T00:00:00+00:00",
        "hostname": socket.gethostname(),
    }), encoding="utf-8")

    lock.acquire()          # provably-dead holder on this host → stolen
    try:
        assert json.loads(lock.path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    finally:
        lock.release()


def test_buildlock_respects_a_contenders_steal_mutex(tmp_project):
    import socket

    from manju.runtime.buildlock import BuildLock, BuildLocked

    lock = BuildLock(tmp_project.root, actor="human")
    lock.path.parent.mkdir(parents=True, exist_ok=True)
    lock.path.write_text(json.dumps({
        "pid": 1, "actor": "engine",
        "started": "2026-07-19T00:00:00+00:00",
        "hostname": socket.gethostname() + "-elsewhere",  # cross-host: mtime rule
    }), encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock.path, (old, old))                       # stale by mtime

    steal = lock.path.with_name(lock.path.name + ".steal")
    steal.write_text("mid-steal", encoding="utf-8")       # another contender
    with pytest.raises(BuildLocked):
        lock.acquire()
    steal.unlink()
    lock.acquire()                                        # now OUR steal wins
    lock.release()


# ===================================================== wave-7 (second pass)


def test_torn_voice_timing_json_degrades_never_crashes_compile(tmp_path):
    """A timing.json torn mid-multibyte-char must degrade to the weighted-split
    fallback (None) — UnicodeDecodeError used to escape the JSON/OSError net
    and crash the whole compile."""
    from manju.timeline.compiler import _load_voice_timing

    voice = tmp_path / "voice_take_01.wav"
    voice.write_bytes(b"riff")
    timing = tmp_path / "voice_take_01.timing.json"
    timing.write_bytes('[{"start_ms": 0, "end_ms": 100, "text": "你好'
                       .encode("utf-8")[:-1])  # torn CJK tail
    assert _load_voice_timing(voice) is None


def test_perf_report_timestamps_never_mix_naive_and_aware():
    """A hand-edited/legacy naive stamp must normalize to UTC at the parse
    boundary — naive-vs-aware max()/subtraction raises TypeError and crashed
    the whole derived report."""
    from manju.build.usage_report import _parse_ts as usage_parse
    from manju.qc.runperf import _parse_ts as perf_parse

    for parse in (perf_parse, usage_parse):
        aware = parse("2026-07-19T10:00:00+00:00")
        naive = parse("2026-07-19T09:00:00")  # hand-edited: no offset
        assert aware is not None and naive is not None
        assert (aware - naive).total_seconds() == 3600  # comparable, ordered


def test_roundtrip_batch_records_never_overwrite_within_one_second(tmp_project):
    """Two applies in the same wall-clock second must land TWO audit records."""
    from manju.build import roundtrip as RT

    plan = {"kind": "jianying", "edited": "", "rows": []}
    first = RT.apply_roundtrip(tmp_project, plan, actor="test")
    second = RT.apply_roundtrip(tmp_project, plan, actor="test")
    batch_dir = tmp_project.root / "reports" / "roundtrip_batches"
    files = sorted(p.name for p in batch_dir.glob("*.yaml"))
    assert len(files) == 2, files
    assert first["batch"] != second["batch"]


# ====================================================== wave-8 (third pass)


def test_edl_import_refuses_non_utf8_and_missing_files_structurally(tmp_path):
    """An EDL is FOREIGN intake (another NLE's save): a GBK/legacy-codepage
    file or a typo'd path must surface as the module's structured
    EdlImportError — the CLI wrapper only speaks that — never a raw
    UnicodeDecodeError/FileNotFoundError traceback."""
    from manju.exporters.edl_import import EdlImportError, parse_edl

    gbk = tmp_path / "老工程.edl"
    gbk.write_bytes("TITLE: 雨夜便利店\n001  卷带A V C 00:00:00:00 "
                    .encode("gb2312"))
    with pytest.raises(EdlImportError) as exc:
        parse_edl(gbk)
    assert any(d.get("code") == "bad_encoding" for d in exc.value.diagnostics)

    with pytest.raises(EdlImportError) as exc2:
        parse_edl(tmp_path / "不存在.edl")
    assert any(d.get("code") == "unreadable" for d in exc2.value.diagnostics)


def test_fcpxml_import_refuses_a_missing_file_structurally(tmp_path):
    from manju.exporters.fcpxml_import import FcpxmlImportError, parse_fcpxml

    with pytest.raises(FcpxmlImportError) as exc:
        parse_fcpxml(tmp_path / "不存在.fcpxml")
    assert any(d.get("code") == "unreadable" for d in exc.value.diagnostics)
