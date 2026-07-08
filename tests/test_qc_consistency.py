"""Cross-shot visual-consistency QC (round X, agent XB — user pain #2).

The round-V pipe (qc/agent_review.py: brief/verdict/merge) judges each shot in
ISOLATION. Consistency is a CROSS-shot / shot-vs-reference property, so this
round adds a second brief mode that packages COMPARISON UNITS instead of
per-shot rows: a contact sheet per character appearing in >1 shot (bible ref
image(s) + one take frame per appearance), a side-by-side board per adjacent
shot pair sharing a scene, and a contact sheet per scene. A verdict against a
unit binds to ALL member take hashes at once (any member regenerating stales
it), coverage tracking reports reviewed/stale/never per shot AND per unit, and
a human can file a verdict straight from the /review GUI (not only an agent).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.models import TakeSidecar
from manju.core.yamlio import write_yaml
from manju.gui.server import create_server
from manju.media.boards import grid_dims
from manju.qc.agent_review import (
    VerdictError,
    agent_log_path,
    agent_verdict_items,
    qc_brief,
    qc_coverage,
    record_verdicts,
)
from manju.qc.checks import run_qc

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg + ffprobe required for real frame extraction",
)


# --------------------------------------------------------------- fixtures


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _real_take(project, shot_id, *, color="red", seconds=1):
    tmp = project.root / f"_src_{shot_id}_{color}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=160x120:d={seconds}:r=24", "-pix_fmt", "yuv420p", str(tmp)],
        check=True, capture_output=True,
    )
    take = project.register_take(shot_id, tmp,
                                 TakeSidecar(provider="test", spec_hash="h"))
    tmp.unlink()
    return take


def _ref_image(project, rel, *, color="blue"):
    dest = project.root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=64x64", "-frames:v", "1", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def _build_consistency_project(project, add_shot):
    """A small project exercising all three unit kinds:

    - S001, S002 share scene ``convenience_store`` and both feature 林夏
      (linxia) — a character unit (linxia), a pair unit, a scene unit.
    - S002, S003 also feature 阿凯 (kai), but S003 is in scene ``alley`` — so
      linxia/kai's shared shot S002 does NOT make S002~S003 a pair unit (the
      scene changes), but kai still gets a character unit (S002, S003).
    - S003, S004 share scene ``alley`` — a second pair + scene unit.
    - a lone character ``solo`` appears only in S001 — too few appearances for
      a comparison unit (exercises the ``skipped`` path).
    """
    write_yaml(project.root / "bible" / "scenes.yaml", {
        "convenience_store": {"name": "便利店"},
        "alley": {"name": "小巷"},
    })
    write_yaml(project.root / "bible" / "characters.yaml", {
        "linxia": {"name": "林夏", "ref_images": ["media/refs/linxia_front.png"]},
        "kai": {"name": "阿凯"},
        "solo": {"name": "路人甲"},
    })
    _ref_image(project, "media/refs/linxia_front.png")

    add_shot(project, "S001", scene="convenience_store", characters=["linxia", "solo"])
    add_shot(project, "S002", scene="convenience_store", characters=["linxia", "kai"])
    add_shot(project, "S003", scene="alley", characters=["kai"])
    add_shot(project, "S004", scene="alley", characters=[])

    for sid, color in (("S001", "red"), ("S002", "green"), ("S003", "blue"), ("S004", "yellow")):
        take = _real_take(project, sid, color=color)
        _select(project, sid, take.name)


# ------------------------------------------------------- unit construction


@needs_ffmpeg
def test_consistency_units_character_pair_scene(tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)

    brief = qc_brief(tmp_project, mode="consistency")
    assert brief["mode"] == "consistency"
    assert brief["criteria"]["skill"] == "visual-qc-review"

    ids = {u["unit"]: u for u in brief["units"]}
    assert ids.keys() >= {
        "character:linxia", "character:kai",
        "pair:S001~S002", "pair:S003~S004",
        "scene:convenience_store", "scene:alley",
    }
    # the scene actually changes between S002 and S003 -> no pair unit there
    assert "pair:S002~S003" not in ids

    linxia = ids["character:linxia"]
    assert [m["shot"] for m in linxia["members"]] == ["S001", "S002"]
    assert linxia["criteria"]["sections"] == "A/B"

    kai = ids["character:kai"]
    assert [m["shot"] for m in kai["members"]] == ["S002", "S003"]

    pair = ids["pair:S001~S002"]
    assert [m["shot"] for m in pair["members"]] == ["S001", "S002"]
    assert pair["criteria"]["sections"] == "C/D"

    scene = ids["scene:convenience_store"]
    assert [m["shot"] for m in scene["members"]] == ["S001", "S002"]
    assert scene["criteria"]["sections"] == "C/D"

    # solo only appears in one shot -> not enough for a comparison unit
    assert "character:solo" not in ids
    skip_ids = {s["unit"] for s in brief["skipped"]}
    assert "character:solo" in skip_ids

    # every take_hash is real (bound to the actual registered take bytes)
    for u in brief["units"]:
        for m in u["members"]:
            assert m["take_hash"] and m["take_hash"].startswith("sha256:")

    # a coverage block travels with the brief (round X)
    assert brief["coverage"]["summary"]["units_total"] == len(brief["units"])


@needs_ffmpeg
def test_consistency_shots_filter_scopes_units(tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)

    scoped = qc_brief(tmp_project, ["S003"], mode="consistency")
    ids = {u["unit"] for u in scoped["units"]}
    # every returned unit must include S003 as a member
    for u in scoped["units"]:
        assert any(m["shot"] == "S003" for m in u["members"])
    assert "character:kai" in ids       # kai appears in S002, S003
    assert "character:linxia" not in ids  # linxia never appears in S003


def test_qc_brief_invalid_mode_raises(tmp_project):
    with pytest.raises(ValueError):
        qc_brief(tmp_project, mode="bogus")


def test_grid_dims_supports_pair_board():
    """round X extended GRID_DIMS with a 2 (2x1) entry for the pair board —
    4/9 stay exactly as before (boards.py's own tests cover those)."""
    assert grid_dims(2) == (2, 1)
    assert grid_dims(4) == (2, 2)
    assert grid_dims(9) == (3, 3)


# --------------------------------------------------- contact-sheet composition


@needs_ffmpeg
def test_consistency_board_composed_labeled_and_cached(monkeypatch, tmp_project, add_shot):
    """Real ffmpeg composition end-to-end: the character unit's board is a real
    image (ref image + one frame per appearance, labeled), and a second brief
    call is a cache hit (make_board is NOT invoked again for the same unit)."""
    _build_consistency_project(tmp_project, add_shot)

    from manju.media import boards as boards_mod

    real_make_board = boards_mod.make_board
    calls: list[dict] = []

    def spy(images, grid, out, **kw):
        calls.append({"grid": grid, "labels": list(kw.get("labels") or []),
                      "n_images": len(images), "out": str(out)})
        return real_make_board(images, grid, out, **kw)

    monkeypatch.setattr(boards_mod, "make_board", spy)

    brief = qc_brief(tmp_project, mode="consistency")
    units_by_id = {u["unit"]: u for u in brief["units"]}
    linxia = units_by_id["character:linxia"]
    assert linxia["image"] is not None
    board_path = tmp_project.root / linxia["image"]
    assert board_path.is_file() and board_path.stat().st_size > 0

    # match calls back to units by destination path (content-addressed, salted
    # with the unit id — so the pair and scene units over the SAME two shots
    # never collide on one cache file, even though their cells coincide).
    def call_for(unit: dict) -> dict:
        dest = str(tmp_project.root / unit["image"])
        return next(c for c in calls if c["out"] == dest)

    linxia_call = call_for(linxia)
    # ref image cell first, then one cell per appearance in shot order
    assert linxia_call["labels"] == ["参考图", "S001", "S002"]
    assert linxia_call["grid"] == 4      # 3 cells -> smallest grid that fits is 2x2
    assert linxia_call["n_images"] == 3

    pair_call = call_for(units_by_id["pair:S001~S002"])
    assert pair_call["labels"] == ["S001", "S002"]
    assert pair_call["grid"] == 2        # a pair board is the 2-cell side-by-side grid

    n_calls_first_pass = len(calls)
    assert n_calls_first_pass == len(brief["units"])  # one compose per unit

    # a second brief call must be a cache hit: no new make_board invocations,
    # and it returns the SAME content-addressed path.
    brief2 = qc_brief(tmp_project, mode="consistency")
    assert len(calls) == n_calls_first_pass
    units2 = {u["unit"]: u for u in brief2["units"]}
    assert units2["character:linxia"]["image"] == linxia["image"]


@needs_ffmpeg
def test_consistency_board_degrades_without_crashing_when_compose_fails(
    monkeypatch, tmp_project, add_shot,
):
    """A composition failure (e.g. no ffmpeg) degrades to image=None — the
    unit and its members still show up in the brief (never fatal, module-wide
    degrade-gracefully contract)."""
    _build_consistency_project(tmp_project, add_shot)

    from manju.media import boards as boards_mod

    def boom(*a, **kw):
        raise boards_mod.MediaError("simulated compose failure")

    monkeypatch.setattr(boards_mod, "make_board", boom)

    brief = qc_brief(tmp_project, mode="consistency")
    assert brief["units"]  # still lists the units
    for u in brief["units"]:
        assert u["image"] is None
        assert u["members"]  # member bindings are unaffected


# ------------------------------------------------- unit verdict + staleness


@needs_ffmpeg
def test_unit_verdict_roundtrip_surfaces_ai_item(tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)

    result = record_verdicts(tmp_project, {"verdicts": [
        {"unit": "pair:S001~S002", "criterion": "C1", "level": "issue",
         "message": "背景招牌位置不连续"},
    ]}, actor="ai")
    assert result["written"] == 1

    items = agent_verdict_items(tmp_project)
    hit = [i for i in items if i.subject == "pair:S001~S002" and "[AI判读]" in i.message]
    assert hit, [i.message for i in items]
    assert hit[0].level == "warn"  # issue -> warn
    assert "背景招牌位置不连续" in hit[0].message
    assert "C1" in hit[0].message


@needs_ffmpeg
def test_unit_verdict_stales_when_any_member_regenerates(tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)

    record_verdicts(tmp_project, {"verdicts": [
        {"unit": "character:linxia", "criterion": "A2", "level": "blocker",
         "message": "两镜之间脸型明显不同"},
    ]})
    before = agent_verdict_items(tmp_project)
    assert any(i.subject == "character:linxia" and "[AI判读]" in i.message for i in before)

    # regenerate ONLY S002 (one of linxia's two member shots)
    take2 = _real_take(tmp_project, "S002", color="magenta")
    _select(tmp_project, "S002", take2.name)

    after = agent_verdict_items(tmp_project)
    assert not any(i.subject == "character:linxia" and "[AI判读]" in i.message for i in after)
    stale = [i for i in after if i.subject == "character:linxia" and i.level == "info"
            and "已过期" in i.message]
    assert stale
    assert "consistency" in stale[0].suggestion


def test_unit_verdict_unknown_unit_rejected(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, {"verdicts": [
            {"unit": "character:nope", "criterion": "A1", "level": "fyi", "message": "x"},
        ]})
    assert "character:nope" in str(exc.value)
    assert not agent_log_path(tmp_project).exists()  # all-or-nothing, same as shot path


@needs_ffmpeg
def test_unit_verdict_requires_criterion_and_message(tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)
    with pytest.raises(VerdictError):
        record_verdicts(tmp_project, {"verdicts": [
            {"unit": "pair:S001~S002", "level": "fyi", "criterion": "", "message": "x"},
        ]})
    with pytest.raises(VerdictError):
        record_verdicts(tmp_project, {"verdicts": [
            {"unit": "pair:S001~S002", "level": "fyi", "criterion": "C1", "message": " "},
        ]})


# --------------------------------------------------------------- coverage


@needs_ffmpeg
def test_qc_coverage_states(tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)

    # nothing judged yet -> everything reviewable is "never"
    cov = qc_coverage(tmp_project)
    assert cov["shots"]["S001"] == "never"
    assert cov["units"]["character:linxia"]["state"] == "never"
    assert cov["summary"]["gaps"] > 0

    # judge S001 (shot-scoped) and the linxia unit (unit-scoped)
    record_verdicts(tmp_project, {"verdicts": [
        {"shot": "S001", "criterion": "A1", "level": "fyi", "message": "S001 看起来正常"},
        {"unit": "character:linxia", "criterion": "A2", "level": "fyi", "message": "身份一致"},
    ]})
    cov2 = qc_coverage(tmp_project)
    assert cov2["shots"]["S001"] == "reviewed"
    assert cov2["units"]["character:linxia"]["state"] == "reviewed"
    assert cov2["units"]["character:linxia"]["kind"] == "character"

    # regenerate S001 -> its shot verdict AND the linxia unit verdict go stale
    take2 = _real_take(tmp_project, "S001", color="cyan")
    _select(tmp_project, "S001", take2.name)
    cov3 = qc_coverage(tmp_project)
    assert cov3["shots"]["S001"] == "stale"
    assert cov3["units"]["character:linxia"]["state"] == "stale"
    # untouched shots/units keep their prior (never) state
    assert cov3["shots"]["S002"] == "never"


@needs_ffmpeg
def test_qc_coverage_summary_counts_are_consistent(tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)
    cov = qc_coverage(tmp_project)
    s = cov["summary"]
    assert s["shots_total"] == s["shots_reviewed"] + s["shots_stale"] + s["shots_never"]
    assert s["units_total"] == s["units_reviewed"] + s["units_stale"] + s["units_never"]
    assert s["gaps"] == s["shots_never"] + s["units_never"]
    assert s["shots_total"] == len(cov["shots"])
    assert s["units_total"] == len(cov["units"])


# ----------------------------------------------------------------- run_qc


@needs_ffmpeg
def test_run_qc_surfaces_one_coverage_gap_info_item(tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)

    report = run_qc(tmp_project, None, extract_frames=False)
    gap_items = [i for i in report.items if i.subject == "qc_coverage"]
    assert len(gap_items) == 1
    assert gap_items[0].level == "info"
    assert "未经 AI 判读" in gap_items[0].message

    # judge EVERY reviewable shot and unit -> the gap item disappears
    cov = qc_coverage(tmp_project)
    verdicts = [
        {"shot": sid, "criterion": "A1", "level": "fyi", "message": "ok"}
        for sid in cov["shots"]
    ] + [
        {"unit": uid, "criterion": "A1", "level": "fyi", "message": "ok"}
        for uid in cov["units"]
    ]
    record_verdicts(tmp_project, {"verdicts": verdicts})

    report2 = run_qc(tmp_project, None, extract_frames=False)
    assert not any(i.subject == "qc_coverage" for i in report2.items)


def test_run_qc_no_gap_item_when_nothing_reviewable(tmp_project):
    """A project with no shots has nothing to cover — the coverage-gap info
    item stays silent (never a spurious 0-gap nudge)."""
    report = run_qc(tmp_project, None, extract_frames=False)
    assert not any(i.subject == "qc_coverage" for i in report.items)


# ------------------------------------------------------------------- CLI


@needs_ffmpeg
def test_cli_qc_brief_consistency_mode(tmp_project, add_shot, monkeypatch):
    _build_consistency_project(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)

    result = CliRunner().invoke(app, ["qc", "brief", "--mode", "consistency", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["mode"] == "consistency"
    assert any(u["unit"] == "character:linxia" for u in data["units"])

    human = CliRunner().invoke(app, ["qc", "brief", "--mode", "consistency"])
    assert human.exit_code == 0, human.output
    assert "一致性" in human.output
    assert "character:linxia" in human.output


@needs_ffmpeg
def test_cli_qc_coverage(tmp_project, add_shot, monkeypatch):
    _build_consistency_project(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)

    result = CliRunner().invoke(app, ["qc", "coverage", "--json"])
    assert result.exit_code == 0, result.output
    cov = json.loads(result.output)
    assert "summary" in cov and "gaps" in cov["summary"]

    human = CliRunner().invoke(app, ["qc", "coverage"])
    assert human.exit_code == 0, human.output
    assert "覆盖率" in human.output


def test_cli_qc_brief_bad_mode_fails_cleanly(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, ["qc", "brief", "--mode", "bogus"])
    assert result.exit_code != 0


# ------------------------------------------------------------------- MCP


@needs_ffmpeg
def test_mcp_qc_brief_consistency_and_coverage_tools(tmp_project, add_shot):
    from manju.mcp.tools import call_tool, list_tools

    names = {t["name"] for t in list_tools()}
    assert {"qc_brief", "qc_coverage", "qc_verdict"} <= names

    _build_consistency_project(tmp_project, add_shot)

    brief = call_tool(tmp_project, "qc_brief", {"mode": "consistency"})
    assert brief["mode"] == "consistency"
    unit = next(u for u in brief["units"] if u["unit"] == "pair:S001~S002")

    out = call_tool(tmp_project, "qc_verdict", {"verdicts": [
        {"unit": unit["unit"], "criterion": "D1", "level": "issue", "message": "光源方向不一致"},
    ]})
    assert out["written"] == 1

    cov = call_tool(tmp_project, "qc_coverage", {})
    assert cov["units"]["pair:S001~S002"]["state"] == "reviewed"
    assert "summary" in cov


def test_mcp_qc_brief_bad_mode_is_tool_error(tmp_project, add_shot):
    from manju.mcp.tools import ToolError, call_tool

    add_shot(tmp_project, "S001")
    with pytest.raises(ToolError):
        call_tool(tmp_project, "qc_brief", {"mode": "bogus"})


def test_mcp_qc_verdict_unknown_unit_is_tool_error(tmp_project, add_shot):
    from manju.mcp.tools import ToolError, call_tool

    add_shot(tmp_project, "S001")
    with pytest.raises(ToolError):
        call_tool(tmp_project, "qc_verdict", {"verdicts": [
            {"unit": "character:nope", "criterion": "A1", "level": "fyi", "message": "x"},
        ]})


# --------------------------------------------------------------- GUI review


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "_library"))
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers"))
    monkeypatch.setenv("MANJU_ROUTING", str(tmp_path / "_user_routing.yaml"))


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


def _post(server, path, body, token=None, **kw):
    headers = {"X-Manju-Token": token if token is not None else server.token}
    return _req(server, path, method="POST", body=body, headers=headers, **kw)


@needs_ffmpeg
def test_review_page_renders_consistency_section(gui, tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)

    status, _, body = _html(gui, "/review")
    assert status == 200
    assert "跨镜一致性 Consistency" in body
    assert 'data-unit="character:linxia"' in body
    assert 'data-unit="pair:S001~S002"' in body
    assert "提交裁决" in body
    assert "未判读" in body  # coverage chip, nothing judged yet


def test_review_page_consistency_empty_state(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")  # a single shot -> no comparison unit possible
    status, _, body = _html(gui, "/review")
    assert status == 200
    assert "暂无可判读的一致性组合" in body


@needs_ffmpeg
def test_qc_verdict_endpoint_roundtrip_as_human(gui, tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)

    status, _, data = _post(gui, "/api/qc/verdict", {
        "unit": "character:linxia", "criterion": "A2", "level": "issue",
        "message": "第二镜眼神明显不同",
    })
    assert status == 200 and data["ok"] is True

    items = agent_verdict_items(tmp_project)
    hit = [i for i in items if i.subject == "character:linxia"]
    assert hit and "第二镜眼神明显不同" in hit[0].message

    last_line = agent_log_path(tmp_project).read_text(encoding="utf-8").splitlines()[-1]
    assert json.loads(last_line)["actor"] == "human"   # GUI verdicts are actor=human

    # the page now reflects the unit as judged
    _, _, body = _html(gui, "/review")
    assert "已判读" in body


@needs_ffmpeg
def test_qc_verdict_endpoint_shot_scoped_also_works(gui, tmp_project, add_shot):
    _build_consistency_project(tmp_project, add_shot)
    status, _, data = _post(gui, "/api/qc/verdict", {
        "shot": "S001", "criterion": "A1", "level": "fyi", "message": "S001 没问题",
    })
    assert status == 200 and data["ok"] is True
    items = agent_verdict_items(tmp_project)
    assert any(i.subject == "S001" for i in items)


def test_qc_verdict_endpoint_guards(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # CSRF: wrong/missing token
    status, _, data = _req(gui, "/api/qc/verdict", method="POST",
                           body={"shot": "S001", "criterion": "A1", "level": "fyi",
                                 "message": "x"},
                           headers={"X-Manju-Token": "wrong"})
    assert status == 403 and "token" in data["error"].lower()

    # unknown unit -> a clean 400, not a 500
    status, _, data = _post(gui, "/api/qc/verdict", {
        "unit": "character:nope", "criterion": "A1", "level": "fyi", "message": "x",
    })
    assert status == 400
    assert "character:nope" in data["error"]


def test_qc_verdict_endpoint_readonly_blocked(tmp_project, tmp_path, add_shot):
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, data = _post(server, "/api/qc/verdict", {
            "shot": "S001", "criterion": "A1", "level": "fyi", "message": "x",
        })
        assert status == 403 and "readonly" in data["error"]
    finally:
        server.shutdown()
        server.close()
