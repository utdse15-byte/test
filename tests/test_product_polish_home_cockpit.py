"""Wave 9 product gates for the calm, journey-aware home cockpit.

The home page is a presentation projection over existing owners.  These tests
pin the priority sequence and safety boundaries without making the cockpit a
new workflow engine: story truth, Director Proposal, shot status, take media,
build planning and Picture Lock all remain owned where they already live.
"""

from __future__ import annotations

from manju.build.director import propose
from manju.core.spec import compute_spec_hash
from manju.gui import cockpit as ck
from manju.gui.cockpit import cockpit_data
from manju.gui.page import render_js, render_page


BRIEF = (
    "# 故事与结尾\n\n"
    "雨夜的末班车停在空站，准备离开的检票员发现一个孩子一直等着不会回来的父亲。"
    "她本来只想按时关门，却最终陪孩子走完整条回家路，也因此放下了自己多年没有面对的离别和逃避。"
)
SYNOPSIS = (
    "# 场次梗概\n\n"
    "第一场从空站和催促关门开始，检票员发现孩子后被迫停下。第二场两人在雨里寻找线索，"
    "孩子承认父亲已经失约很多次。最后检票员把自己的伞交给孩子，并决定亲自送他回家，她也第一次拨通了多年未联系的母亲电话。"
)
BEATS = (
    "# 变化节拍\n\n"
    "- 广播催促关门，检票员准备结束一天。\n"
    "- 她发现孩子仍然等在空站，选择先询问而不是赶走。\n"
    "- 孩子承认父亲可能不会来，检票员的态度发生变化。\n"
    "- 两人共撑一把伞离开，检票员也拨出自己的和解电话。\n"
)
SCRIPT = (
    "# 剧本与声音\n\n"
    "夜。空站。广播最后一次提示关门。检票员把钥匙插进卷帘门，忽然听见长椅下传来鞋底摩擦声。"
    "她回头，看见孩子抱着湿透的书包。孩子说：爸爸答应来。她停了很久，把门重新推开。"
    "广播声被她亲手关掉，空站第一次真正安静下来。"
)


def _write(project, relpath: str, text: str) -> None:
    path = project.root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fill_story(project) -> None:
    _write(project, "story/brief.md", BRIEF)
    _write(project, "story/synopsis.md", SYNOPSIS)
    _write(project, "story/beats.md", BEATS)
    _write(project, "story/script.md", SCRIPT)


def _project_bytes(project) -> dict[str, bytes]:
    return {
        path.relative_to(project.root).as_posix(): path.read_bytes()
        for path in project.root.rglob("*")
        if path.is_file() and ".git" not in path.parts and ".manju" not in path.parts
    }


def _register_current_take(project, make_take, shot_id: str):
    shot = project.load_shot(shot_id)
    spec_hash = compute_spec_hash(shot, project.load_bible())
    return make_take(project, shot_id, spec_hash)


def _select_review_approve(project, shot_id: str, take_name: str) -> None:
    shot = project.load_shot(shot_id)
    shot.status.selected_take = take_name
    shot.status.take_notes[take_name] = "推荐：动作和构图符合当前镜头意图。"
    shot.status.review = "approved"
    project.save_shot(shot)


def _js_function(name: str, next_name: str) -> str:
    js = render_js()
    start = js.index(f"function {name}(")
    end = js.index(f"function {next_name}(", start)
    return js[start:end]


def test_home_focus_moves_through_existing_story_shot_and_build_owners(
    tmp_project, add_shot, make_take
):
    fresh = cockpit_data(tmp_project)
    assert fresh["focus"]["source"] == "authoring"
    assert fresh["focus"]["href"] == "/create"
    assert fresh["focus"]["verb"] == "link"

    _fill_story(tmp_project)
    no_shots = cockpit_data(tmp_project)
    assert no_shots["focus"]["source"] == "authoring"
    assert no_shots["focus"]["href"] == "/storyboard"

    add_shot(tmp_project, "S001")
    no_candidate = cockpit_data(tmp_project)
    assert no_candidate["focus"] == {
        **no_candidate["focus"],
        "source": "shots",
        "kind": "candidate",
        "href": "/lab?shot=S001",
        "verb": "link",
        "shot": "S001",
    }

    take = _register_current_take(tmp_project, make_take, "S001")
    no_selection = cockpit_data(tmp_project)
    assert no_selection["focus"]["kind"] == "select"
    assert no_selection["focus"]["href"] == "/review?shot=S001"

    shot = tmp_project.load_shot("S001")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)
    no_review = cockpit_data(tmp_project)
    assert no_review["focus"]["kind"] == "review"
    assert "1/1 有候选" in no_review["focus"]["detail"]

    shot = tmp_project.load_shot("S001")
    shot.status.take_notes[take.name] = "推荐"
    tmp_project.save_shot(shot)
    no_approval = cockpit_data(tmp_project)
    assert no_approval["focus"]["kind"] == "approve"

    shot = tmp_project.load_shot("S001")
    shot.status.review = "approved"
    tmp_project.save_shot(shot)
    ready_to_build = cockpit_data(tmp_project)
    assert ready_to_build["focus"]["source"] == "engine"
    assert ready_to_build["focus"]["verb"] == "build"
    assert ready_to_build["focus"]["action"] == {"type": "build", "target": "final"}
    assert "试算" in ready_to_build["focus"]["detail"]


def test_pending_authoring_and_other_proposals_surface_without_auto_execution(
    tmp_project, add_shot, make_take
):
    _fill_story(tmp_project)
    add_shot(tmp_project, "S001")
    take = _register_current_take(tmp_project, make_take, "S001")
    _select_review_approve(tmp_project, "S001", take.name)

    propose(
        tmp_project,
        [{
            "type": "truth_patch_set",
            "patches": [{"path": "story/script.md", "content": SCRIPT + "\n提案结尾。\n"}],
        }],
        why="强化结尾声音",
        actor="ai",
    )
    data = cockpit_data(tmp_project)
    assert data["focus"]["source"] == "authoring"
    assert data["focus"]["href"] == "/director"
    assert data["journeys"]["authoring"]["attention"]["truth_pending"] == 1
    assert data["focus"]["kind"] == "proposal"
    assert "创作提案" in data["journeys"]["authoring"]["summary"]


def test_non_authoring_proposal_is_still_a_human_decision_on_home(
    tmp_project, add_shot, make_take
):
    _fill_story(tmp_project)
    add_shot(tmp_project, "S001")
    take = _register_current_take(tmp_project, make_take, "S001")
    _select_review_approve(tmp_project, "S001", take.name)
    propose(tmp_project, [{"type": "snapshot", "label": "before-finishing"}], actor="human")

    data = cockpit_data(tmp_project)
    assert data["journeys"]["authoring"]["attention"]["other_pending"] == 1
    assert data["focus"]["source"] == "authoring"
    assert data["focus"]["href"] == "/director"
    assert "操作提案" in data["journeys"]["authoring"]["summary"]


def test_cockpit_projection_is_read_only(tmp_project, add_shot, make_take):
    _fill_story(tmp_project)
    add_shot(tmp_project, "S001")
    take = _register_current_take(tmp_project, make_take, "S001")
    _select_review_approve(tmp_project, "S001", take.name)
    before = _project_bytes(tmp_project)

    data = cockpit_data(tmp_project)

    assert _project_bytes(tmp_project) == before
    assert set(data["journeys"]) == {"authoring", "shots", "finishing"}
    assert data["focus"]["action"] == {"type": "build", "target": "final"}


def test_authoring_projection_failure_does_not_hide_shot_or_finishing_journeys(
    tmp_project, add_shot, monkeypatch
):
    add_shot(tmp_project, "S001")

    def broken(_project):
        raise OSError("authoring projection unavailable")

    monkeypatch.setattr(ck, "authoring_journey_payload", broken)
    data = cockpit_data(tmp_project)

    assert data["journeys"]["authoring"]["state"] == "unavailable"
    assert data["journeys"]["shots"]["summary"].startswith("0/1")
    assert data["journeys"]["finishing"]["summary"].startswith("等待镜头")
    assert data["focus"]["source"] == "shots"



def test_shot_projection_failure_does_not_hide_authoring_journey(tmp_project, monkeypatch):
    _fill_story(tmp_project)

    def broken(_project):
        raise OSError("shot projection unavailable")

    monkeypatch.setattr(ck, "shot_production_summary", broken)
    data = cockpit_data(tmp_project)

    assert data["journeys"]["authoring"]["state"] == "current"
    assert data["journeys"]["shots"]["state"] == "attention"
    assert data["journeys"]["shots"]["error"] == "shot projection unavailable"
    assert data["journeys"]["finishing"]["state"] == "unavailable"
    assert data["focus"]["source"] == "authoring"

def test_home_html_keeps_legacy_workbench_available_but_progressively_disclosed(tmp_project):
    html = render_page(tmp_project.load_config().name, "token")
    assert 'id="workbench-details"' in html
    assert "项目详情与高级操作" in html
    assert html.index('id="workbench-details"') < html.index('id="header"')
    assert html.index('id="workbench-details"') < html.index('id="buildpanel"')
    assert html.index('id="workbench-details"') < html.index('id="shots"')
    assert '<details id="workbench-details"' in html
    assert '<details id="workbench-details" class="home-workbench" open' not in html


def test_home_workbench_defaults_closed_and_remembers_only_user_ui_state():
    block = _js_function("initHomeWorkbench", "openWorkbenchDetails")
    assert "details.open = false" in block
    assert 'loadUI().workbenchOpen' in block
    assert 'saveUI({ workbenchOpen: !!details.open })' in block
    assert 'classList.contains("mj-mode-pro")' not in block


def test_home_primary_action_navigates_or_reuses_existing_plan_modal_only():
    cta = _js_function("renderHeroCTA", "heroAction")
    hero = _js_function("heroAction", "heroBuild")
    build = _js_function("heroBuild", "renderCockGrid")

    assert "a.href = na.href" in cta
    assert 'na.verb === "build"' in cta
    assert "heroBuild(na.action || {})" in hero
    assert 'showPlanModal("build", params' in build
    assert "/api/select" not in cta + hero + build
    assert "picture" not in (cta + hero + build).lower()


def test_collapsed_home_defers_full_workbench_render_and_hidden_estimate():
    workbench = _js_function("renderWorkbench", "render")
    # render() is followed by the cockpit comment rather than a function; slice it directly.
    js = render_js()
    start = js.index("function render(s)")
    end = js.index("/* ========================================================= cockpit", start)
    render = js[start:end]

    assert "if (!s || !homeWorkbenchOpen()) return" in workbench
    assert 'section("shots"' in workbench
    assert "maybeTimeline(s)" in workbench
    assert "maybeProposals(s)" in workbench
    assert "maybeEvaluate(s)" in workbench
    assert "updateEstimate()" in workbench

    assert "renderWorkbench(s)" in render
    assert 'section("shots"' not in render
    assert "maybeTimeline(s)" not in render
    assert "maybeProposals(s)" not in render
    assert "maybeEvaluate(s)" not in render
    assert "updateEstimate()" not in render


def test_app_mode_quit_stays_in_permanent_application_bar():
    js = render_js()
    start = js.index("function ensureQuitBtn()")
    end = js.index("window.__mjEnsureQuitBtn", start)
    block = js[start:end]
    assert 'document.querySelector(".pnav-tools")' in block
    assert '|| $("header")' in block


def test_running_card_does_not_double_count_a_build_lock_and_its_gui_job():
    js = render_js()
    start = js.index("function renderPriorityGrid(")
    end = js.index("function renderStateStrip(", start)
    block = js[start:end]
    assert "const runningCount = activeJobs.length + cloud || (buildActive ? 1 : 0)" in block
    assert "activeJobs.length + cloud + (buildActive ? 1 : 0)" not in block
