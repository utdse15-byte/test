"""`manju gui` — 剧集工作台 /series (round X, agent XD, user pain #4).

The GUI half of core/series.py (CLI-first): the /series page + its supporting
POST actions, plus the 剧集 membership banner core/series.py's Series.find
makes possible in the shared chrome (:mod:`manju.gui.pages`).

These tests pin, against the SAME engine core the CLI drives:
  * the episodes table renders series_status verbatim over a real 2-episode
    fixture series, served over real HTTP;
  * the 剧集 banner appears in the shared chrome when the bound project is an
    EPISODE of a series, and is absent for a plain (non-series) project;
  * 新建集 round-trips through core.series.new_episode (registers in
    series.yaml, seeds the bible, lands an event) and shows up on reload;
  * the sync-bible report groups missing/diverged/in-sync per episode, and
    the SAFE-subset apply button writes ONLY the missing entry (+ an event)
    while a diverged entry is left completely untouched;
  * the global characters view renders series_characters verbatim (presence/
    diverged chips + appearance counts);
  * every mutating POST honours the X-Manju-Token + readonly-403 gates.

Fake media only; no ffmpeg. Chinese names throughout (§14).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from manju.core.events import tail_events
from manju.core.series import Series, new_episode
from manju.core.yamlio import read_yaml, write_yaml
from manju.gui import pages
from manju.gui.server import create_server

SERIES_NAME = "深夜信号剧集"


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("MANJU_SKILLS_DIR", str(tmp_path / "_skills"))


def _seed_series_bible(series: Series) -> None:
    write_yaml(series.bible_dir / "characters.yaml",
               {"linxia": {"name": "林夏", "voice": "冷静、克制、略带沙哑"}})
    write_yaml(series.bible_dir / "scenes.yaml",
               {"store": {"name": "便利店", "lighting": "冷白灯管"}})


@pytest.fixture
def tmp_series(tmp_path: Path) -> Series:
    series = Series.create(tmp_path / SERIES_NAME, name=SERIES_NAME, git_init=False)
    _seed_series_bible(series)
    return series


@pytest.fixture
def two_episodes(tmp_series: Series):
    e1 = new_episode(tmp_series, "E01", title="第一集")
    e2 = new_episode(tmp_series, "E02", title="第二集")
    return tmp_series, e1, e2


def _start(project):
    server = create_server(project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def gui(two_episodes):
    """The GUI server bound to E01 (the first episode) — the ordinary shape:
    `manju gui` always binds to ONE project."""
    series, e1, e2 = two_episodes
    server = _start(e1)
    yield server
    server.shutdown()
    server.close()


@pytest.fixture
def gui_plain(tmp_project):
    """A GUI server bound to a PLAIN project (no series.yaml anywhere above
    it) — the banner-absence control."""
    server = _start(tmp_project)
    yield server
    server.shutdown()
    server.close()


@pytest.fixture
def gui_readonly(two_episodes):
    series, e1, e2 = two_episodes
    server = create_server(e1, host="127.0.0.1", port=0, actor="human", readonly=True)
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
            return resp.status, dict(resp.headers), (
                payload if raw else json.loads(payload or b"{}"))
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


def _poll_job(server, job_id, tries=200):
    """round AA4: new-episode / sync-bible-apply now run on the jobs runner
    (Project.create scaffolding / per-episode bible writes are genuinely
    multi-second) — same submit+poll shape every other GUI test suite uses."""
    import time

    for _ in range(tries):
        status, _, data = _req(server, "/api/jobs")
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise TimeoutError("job did not finish")


# ================================================================== A. episodes


def test_series_page_renders_episodes_table(gui, two_episodes):
    """The episodes table renders series_status verbatim over real HTTP."""
    series, e1, e2 = two_episodes
    status, _, html = _html(gui, "/series")
    assert status == 200
    assert "分集 Episodes" in html
    assert SERIES_NAME in html
    assert "E01" in html and "第一集" in html
    assert "E02" in html and "第二集" in html
    # the currently-bound episode (E01) is marked current, never a hot-swap link
    assert "当前项目" in html
    # a DIFFERENT episode shows its path + the honest `manju gui` command,
    # never an in-place switch action
    assert str(e2.root) in html
    assert "manju gui" in html
    assert 'data-act="switch"' not in html


def test_series_page_404s_never_for_plain_project(gui_plain):
    """A plain project still serves /series (200) with an honest explanation
    instead of erroring — the page never assumes series membership."""
    status, _, html = _html(gui_plain, "/series")
    assert status == 200
    assert "不属于任何剧集" in html
    assert "分集 Episodes" not in html


# =============================================================== A2. continuity


def test_continuity_endpoint_matches_core(gui, two_episodes):
    """GET /api/series/continuity is a strict, read-only render of
    core.series.series_continuity — no job, no lock (round AA7)."""
    from manju.core.series import series_continuity

    series, e1, e2 = two_episodes
    status, headers, data = _req(gui, "/api/series/continuity")
    assert status == 200
    expected = series_continuity(series)
    assert data["series"] == expected["series"]
    assert [e["id"] for e in data["episodes"]] == [e["id"] for e in expected["episodes"]]
    assert data["totals"] == expected["totals"]
    assert data["needs_sync"] == expected["needs_sync"]


def test_continuity_endpoint_404_when_not_in_series(gui_plain):
    status, _, data = _req(gui_plain, "/api/series/continuity")
    assert status == 400
    assert "剧集" in data["error"]


def test_continuity_section_renders_verdict_matrix(gui, two_episodes):
    """Two freshly-seeded, shot-less episodes are both 完整 (in sync, no
    check errors, nothing missing) — the matrix table + verdict chips render."""
    series, e1, e2 = two_episodes
    status, _, html = _html(gui, "/series")
    assert status == 200
    assert "全局连续性 Continuity" in html
    assert "E01" in html and "E02" in html
    assert "完整" in html


def test_continuity_section_links_diverged_episode_into_sync_bible(gui, two_episodes):
    """A 待同步 episode's row links straight into the existing sync-bible
    section instead of duplicating the diff view."""
    series, e1, e2 = two_episodes
    write_yaml(e1.root / "bible" / "characters.yaml", {"linxia": {"name": "林夏(分集改)"}})

    status, _, html = _html(gui, "/series")
    assert status == 200
    assert "待同步" in html
    assert 'href="#sr-sync-ep-E01"' in html
    assert 'id="sr-sync-ep-E01"' in html


def test_continuity_section_is_csp_safe(gui, two_episodes):
    status, headers, html = _html(gui, "/series")
    assert status == 200
    assert "<script>" not in html
    assert "onclick=" not in html and "style=" not in html
    csp = headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp


# ------------------------------------------------------------------ B. banner


def test_banner_appears_for_episode_project(two_episodes):
    """The 剧集 banner renders in the shared chrome (pages.chrome) when the
    bound project is an episode — Series.find walks past its project.yaml."""
    series, e1, e2 = two_episodes
    nav, _ = pages.chrome("/review", e1)
    assert "mj-series-banner" in nav
    assert SERIES_NAME in nav
    assert 'href="/series"' in nav
    assert "剧集工作台" in nav


def test_banner_absent_for_plain_project(tmp_project):
    nav, _ = pages.chrome("/review", tmp_project)
    assert "mj-series-banner" not in nav


def test_banner_absent_when_project_not_passed(two_episodes):
    """Every pre-existing chrome() call site (page.py/edit.py/pages_t.py/
    create_page.py) omits project — behaviour must stay byte-identical."""
    series, e1, e2 = two_episodes
    nav, _ = pages.chrome("/review")
    assert "mj-series-banner" not in nav


def test_banner_appears_over_real_http_on_a_pages_route(gui):
    status, _, html = _html(gui, "/review")
    assert status == 200
    assert "mj-series-banner" in html
    assert 'href="/series"' in html


def test_banner_absent_over_real_http_for_plain_project(gui_plain):
    status, _, html = _html(gui_plain, "/review")
    assert status == 200
    assert "mj-series-banner" not in html


# -------------------------------------------------------------- new-episode


def test_new_episode_round_trip(gui, two_episodes):
    series, e1, e2 = two_episodes
    status, _, resp = _post(gui, "/api/series/new-episode",
                            {"eid": "E03", "title": "第三集"})
    assert status == 202, resp  # round AA4: scaffolding now runs on the jobs runner
    job = _poll_job(gui, resp["job"]["id"])
    assert job["state"] == "done", job
    data = job["result"]
    assert data["eid"] == "E03"

    # registered in series.yaml (the SAME core.series.new_episode the CLI uses)
    cfg = series.load_config()
    assert [(e.id, e.title) for e in cfg.episodes][-1] == ("E03", "第三集")
    ep_dir = series.episode_project_dir("E03")
    assert (ep_dir / "project.yaml").exists()
    # bible seeded from the series bible
    assert read_yaml(ep_dir / "bible" / "characters.yaml")["linxia"]["name"] == "林夏"
    # a series-level event landed
    actions = [e.get("action") for e in tail_events(series.root, 20)]
    assert "series_new_episode" in actions

    # shows up on reload
    status, _, html = _html(gui, "/series")
    assert "E03" in html and "第三集" in html


def test_new_episode_missing_eid_400(gui):
    status, _, data = _post(gui, "/api/series/new-episode", {"eid": "", "title": "x"})
    assert status == 400


def test_new_episode_duplicate_400(gui, two_episodes):
    status, _, data = _post(gui, "/api/series/new-episode", {"eid": "E01"})
    assert status == 400
    assert "error" in data


def test_new_episode_404_when_not_in_series(gui_plain):
    status, _, data = _post(gui_plain, "/api/series/new-episode", {"eid": "E01"})
    assert status == 400
    assert "剧集" in data["error"]


# ------------------------------------------------------------ C. sync-bible


def test_sync_report_groups_missing_and_diverged(gui, two_episodes):
    series, e1, e2 = two_episodes
    # E01 diverges linxia; series bible gains a brand-new character (akun,
    # missing from BOTH episodes).
    write_yaml(e1.root / "bible" / "characters.yaml", {"linxia": {"name": "林夏(分集改)"}})
    write_yaml(series.bible_dir / "characters.yaml",
               {"linxia": {"name": "林夏"}, "akun": {"name": "阿坤"}})

    status, _, html = _html(gui, "/series")
    assert status == 200
    assert "characters:akun" in html          # missing → candidate add
    assert "characters:linxia" in html        # diverged → reported
    # per-entry diff view: series value vs episode value, both escaped
    assert "阿坤" in html
    assert "林夏(分集改)" in html
    assert "林夏" in html
    # the CLI --force command is offered as copyable text for the divergence
    assert "manju series sync-bible --force characters:linxia" in html
    # never an in-page force-overwrite control
    assert 'data-act="force"' not in html


def test_sync_safe_apply_writes_missing_leaves_diverged_untouched(gui, two_episodes):
    series, e1, e2 = two_episodes
    write_yaml(e1.root / "bible" / "characters.yaml", {"linxia": {"name": "林夏(分集改)"}})
    write_yaml(series.bible_dir / "characters.yaml",
               {"linxia": {"name": "林夏"}, "akun": {"name": "阿坤"}})

    status, _, resp = _post(gui, "/api/series/sync-bible/apply", {})
    assert status == 202, resp  # round AA4: sync now runs on the jobs runner
    job = _poll_job(gui, resp["job"]["id"])
    assert job["state"] == "done", job
    data = job["result"]
    assert data["totals"]["added"] >= 1
    assert data["totals"]["overwritten"] == 0

    # missing entry landed in BOTH episodes (series bible entries sync to every
    # episode missing them)
    e1_chars = read_yaml(e1.root / "bible" / "characters.yaml")
    e2_chars = read_yaml(e2.root / "bible" / "characters.yaml")
    assert e1_chars["akun"]["name"] == "阿坤"
    assert e2_chars["akun"]["name"] == "阿坤"
    # the divergence is COMPLETELY untouched — never auto-overwritten
    assert e1_chars["linxia"]["name"] == "林夏(分集改)"

    # an event landed for the add
    events = tail_events(series.root, 40)
    assert any(e["action"] == "series_sync_bible" and e["detail"]["action"] == "add"
               and e["detail"]["entry"] == "characters:akun" for e in events)
    # and never one for an overwrite (the GUI can't send --force at all)
    assert not any(e["action"] == "series_sync_bible" and e["detail"]["action"] == "overwrite"
                   for e in events)


def test_sync_apply_endpoint_never_accepts_force(gui, two_episodes):
    """The GUI's sync-apply endpoint has no `force` parameter at all — passing
    one in the body is simply ignored, proving overwrite stays CLI-only."""
    series, e1, e2 = two_episodes
    write_yaml(e1.root / "bible" / "characters.yaml", {"linxia": {"name": "旧"}})
    write_yaml(series.bible_dir / "characters.yaml", {"linxia": {"name": "新"}})

    status, _, resp = _post(gui, "/api/series/sync-bible/apply",
                            {"force": ["characters:linxia"]})
    assert status == 202, resp  # round AA4: sync now runs on the jobs runner
    job = _poll_job(gui, resp["job"]["id"])
    assert job["state"] == "done", job
    data = job["result"]
    assert data["totals"]["overwritten"] == 0
    assert read_yaml(e1.root / "bible" / "characters.yaml")["linxia"]["name"] == "旧"


def test_sync_report_404_when_not_in_series(gui_plain):
    status, _, data = _post(gui_plain, "/api/series/sync-bible/apply", {})
    assert status == 400


# ------------------------------------------------------------- D. characters


def test_characters_view_renders_presence_and_divergence(gui, two_episodes):
    from manju.core.models import ShotSpec

    series, e1, e2 = two_episodes

    def add_shot(project, shot_id):
        shot = ShotSpec.model_validate({
            "id": shot_id, "scene": "store", "characters": ["linxia"],
            "dialogue": {"speaker": "linxia", "text": "这不可能。"},
            "duration": "auto",
        })
        project.save_shot(shot)
        index = project.load_index()
        index.order.append(shot_id)
        project.save_index(index)

    add_shot(e1, "S001")  # linxia appears in E01
    write_yaml(e2.root / "bible" / "characters.yaml", {"linxia": {"name": "林夏(E2 改)"}})

    status, _, html = _html(gui, "/series")
    assert status == 200
    assert "全局角色 Characters" in html
    assert "linxia" in html
    assert "林夏" in html
    assert "出场×1" in html            # E01 appearance count
    assert "分歧 diverged" in html      # E02 diverged chip


def test_characters_endpoint_shape_matches_core(gui, two_episodes):
    """Sanity: the page's character section is a strict render of
    series_characters (no parallel computation of presence/divergence)."""
    from manju.core.series import series_characters

    series, e1, e2 = two_episodes
    view = series_characters(series)
    status, _, html = _html(gui, "/series")
    for c in view["characters"]:
        assert c["id"] in html


# --------------------------------------------------------------- split-script


def test_split_script_panel_empty_when_no_script_files(gui):
    status, _, html = _html(gui, "/series")
    assert status == 200
    assert "长稿拆分 Split-script" in html
    assert "还没有" in html


def test_split_script_panel_previews_without_writing(gui, two_episodes):
    series, e1, e2 = two_episodes
    script = series.script_dir / "long.md"
    script.write_text("# E01 开场\n林夏走进便利店。\n## E03 追逐\n雨中追逐。\n",
                      encoding="utf-8")

    status, _, html = _html(gui, "/series")
    assert status == 200
    assert "long.md" in html
    assert "已存在 exists" in html   # E01 already exists
    assert "将新建 would-create" in html  # E03 does not yet exist
    assert "manju series split-script" in html
    # read-only: nothing gets created just by rendering the page
    assert not (series.episode_project_dir("E03") / "project.yaml").exists()


# --------------------------------------------------------------- E. guards


def test_new_episode_readonly_403(gui_readonly):
    status, _, data = _post(gui_readonly, "/api/series/new-episode",
                            {"eid": "E03", "title": "x"})
    assert status == 403
    assert "readonly" in data["error"]


def test_new_episode_token_guard(gui):
    status, _, data = _post(gui, "/api/series/new-episode",
                            {"eid": "E03"}, token="bad-token")
    assert status == 403
    assert "token" in data["error"].lower() or "Token" in data["error"]


def test_sync_apply_readonly_403(gui_readonly):
    status, _, data = _post(gui_readonly, "/api/series/sync-bible/apply", {})
    assert status == 403


def test_sync_apply_token_guard(gui):
    status, _, data = _post(gui, "/api/series/sync-bible/apply", {}, token="bad-token")
    assert status == 403


def test_series_page_is_csp_safe_no_inline_js(gui):
    status, headers, html = _html(gui, "/series")
    assert status == 200
    assert 'src="/series.js"' in html
    assert 'href="/series.css"' in html
    assert "<script>" not in html
    assert "onclick=" not in html and "onload=" not in html
    assert "style=" not in html
    csp = headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    from manju.gui import series_page

    js = series_page.render_series_js()
    assert "innerHTML" not in js
    assert "textContent" in js
