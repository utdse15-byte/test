from __future__ import annotations

from manju.build.director import confirm, propose, reject
from manju.gui import authoring_journey, create_page, director_page, storyboard


BRIEF = (
    "# 故事与结尾\n\n"
    "雨夜的末班车停在空站，准备离开的检票员发现一个孩子一直等着不会回来的父亲。"
    "她本来只想按时关门，却最终陪孩子走完整条回家路，也因此放下了自己多年没有面对的离别和逃避。"
)
SYNOPSIS = (
    "# 场次梗概\n\n"
    "第一场从空站和催促关门开始，检票员发现孩子后被迫停下。第二场两人在雨里寻找线索，"
    "孩子承认父亲已经失约很多次。最后检票员把自己的伞交给孩子，并决定亲自送他回家，"
    "她也第一次拨通了多年未联系的母亲电话。"
)
BEATS = (
    "# 变化节拍\n\n"
    "- 车站广播催促关门，检票员准备结束一天。\n"
    "- 她发现孩子仍然等在空站，选择先询问而不是赶走。\n"
    "- 孩子承认父亲可能不会来，检票员的态度发生变化。\n"
    "- 两人共撑一把伞离开，检票员也拨出自己的和解电话。\n"
)
SCRIPT = (
    "# 剧本与声音\n\n"
    "夜。空站。广播最后一次提示关门。检票员把钥匙插进卷帘门，忽然听见长椅下传来鞋底摩擦声。"
    "她回头，看见孩子抱着湿透的书包。孩子说：爸爸答应来。她停了很久，把门重新推开。广播声被她亲手关掉，空站第一次真正安静下来。"
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


def test_authoring_journey_fresh_project_points_to_current_story_stage(tmp_project):
    data = authoring_journey.authoring_journey_payload(tmp_project)
    assert data["writing"]["done"] < data["writing"]["total"]
    assert data["next"]["href"] == "/create"
    assert data["next"]["kind"] == "writing"
    assert data["proposal"]["pending"] == 0
    assert data["storyboard"]["ready"] is False


def test_authoring_journey_proposals_are_optional_but_pending_decisions_take_priority(tmp_project):
    _fill_story(tmp_project)
    no_proposal = authoring_journey.authoring_journey_payload(tmp_project)
    assert no_proposal["writing"]["done"] == 4
    assert no_proposal["next"]["href"] == "/storyboard"
    assert no_proposal["proposal"]["optional"] is True

    proposal = propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{
            "path": "story/script.md",
            "content": SCRIPT + "\n# 修改建议\n让最后一个电话先响一声再切黑。\n",
        }],
    }], why="让结尾的声音先于画面结束", actor="ai")

    pending = authoring_journey.authoring_journey_payload(tmp_project)
    assert pending["proposal"]["pending"] == 1
    assert pending["proposal"]["truth_pending"] == 1
    assert pending["next"]["href"] == "/director"
    assert pending["next"]["kind"] == "proposal"

    reject(tmp_project, proposal.id, actor="human")
    resolved = authoring_journey.authoring_journey_payload(tmp_project)
    assert resolved["proposal"]["pending"] == 0
    assert resolved["next"]["href"] == "/storyboard"


def test_authoring_journey_marks_stale_proposal_as_attention_not_current_truth(tmp_project):
    _fill_story(tmp_project)
    proposal = propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{"path": "story/script.md", "content": SCRIPT + "\n提案版本。\n"}],
    }], actor="ai")
    _write(tmp_project, "story/script.md", SCRIPT + "\n项目已由用户继续修改。\n")

    data = authoring_journey.authoring_journey_payload(tmp_project)
    assert data["proposal"]["stale"] == 1
    assert data["proposal"]["pending"] == 1
    assert data["next"]["href"] == "/director"
    assert proposal.id in data["proposal"]["ids"]



def test_pending_truth_patch_precedes_more_writing_to_avoid_stale_review(tmp_project):
    _write(tmp_project, "story/brief.md", BRIEF)
    proposal = propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{"path": "story/brief.md", "content": BRIEF + "\n让结尾先响起电话。\n"}],
    }], actor="ai")

    data = authoring_journey.authoring_journey_payload(tmp_project)
    assert data["writing"]["complete"] is False
    assert data["proposal"]["truth_pending"] == 1
    assert data["next"]["href"] == "/director"
    assert proposal.id in data["proposal"]["ids"]


def test_non_authoring_proposal_does_not_interrupt_incomplete_story(tmp_project):
    _write(tmp_project, "story/brief.md", BRIEF)
    propose(tmp_project, [{"type": "snapshot", "label": "before-writing"}], actor="human")

    data = authoring_journey.authoring_journey_payload(tmp_project)
    assert data["proposal"]["truth_pending"] == 0
    assert data["proposal"]["other_pending"] == 1
    assert data["next"]["href"] == "/create"
    assert authoring_journey.render_authoring_attention(tmp_project) == ""


def test_optional_proposal_store_failure_does_not_take_down_create_page(tmp_project, monkeypatch):
    _write(tmp_project, "story/brief.md", BRIEF)

    def broken(_project):
        raise OSError("proposal ledger unavailable")

    monkeypatch.setattr("manju.build.director.list_proposals", broken)
    data = authoring_journey.authoring_journey_payload(tmp_project)
    html = create_page.render_create(tmp_project, "tok")

    assert data["proposal"]["error"]
    assert data["next"]["href"] == "/create"
    assert "提案状态暂不可用" in html
    assert "保存并检查进度" in html


def test_storyboard_only_warns_when_authoring_truth_needs_attention(tmp_project, add_shot):
    _fill_story(tmp_project)
    add_shot(tmp_project, "S001")
    healthy = storyboard.render(tmp_project, "tok")
    assert "创作提案尚未决定" not in healthy

    proposal = propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{"path": "story/script.md", "content": SCRIPT + "\n新结尾。\n"}],
    }], actor="ai")
    pending = storyboard.render(tmp_project, "tok")
    assert proposal.id not in pending  # compact hand-off never leaks technical ids
    assert "创作提案尚未决定" in pending
    assert "当前分镜仍然基于已经落盘的项目真相" in pending
    assert 'href="/director"' in pending


def _project_bytes(project):
    return {
        path.relative_to(project.root).as_posix(): path.read_bytes()
        for path in project.root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }



def test_authoring_optional_step_does_not_share_the_badge_class(tmp_project):
    html = authoring_journey.render_authoring_journey(tmp_project, "/create")
    assert 'class="aj-step aj-state-optional"' in html
    assert html.count('<a class="aj-step') == 3
    assert 'class="aj-optional"' in html

def test_authoring_projection_is_read_only(tmp_project):
    _fill_story(tmp_project)
    propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{"path": "story/script.md", "content": SCRIPT + "\n提案。\n"}],
    }], actor="ai")
    before = _project_bytes(tmp_project)

    authoring_journey.authoring_journey_payload(tmp_project)
    authoring_journey.render_authoring_journey(tmp_project, "/create")
    authoring_journey.render_authoring_attention(tmp_project)

    assert _project_bytes(tmp_project) == before

def test_create_page_focuses_story_materials_and_hides_production_technical_detail(tmp_project):
    _write(tmp_project, "story/brief.md", BRIEF)
    html = create_page.render_create(tmp_project, "tok")

    assert 'data-authoring-journey="1"' in html
    assert 'class="cw-materials' in html
    assert html.count('data-source-stage="') == 4
    assert "保存并检查进度" in html
    assert "让 IDE 助手起草" in html
    assert "生产资格与技术状态" in html
    assert '<details class="cw-technical panel"' in html
    assert "cw-railwrap" not in html


def test_director_truth_patch_renders_human_review_and_real_diff(tmp_project):
    _fill_story(tmp_project)
    proposal = propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{
            "path": "story/script.md",
            "content": SCRIPT.replace(
                "她停了很久，把门重新推开",
                "她先关掉广播，随后重新抬起卷帘门",
            ),
        }],
    }], why="让人物的选择通过动作而不是解释成立", actor="ai")

    html = director_page.render_director(tmp_project, "tok")
    assert 'data-authoring-journey="1"' in html
    assert "待我决定" in html
    assert "创作变更提案" in html
    assert "story/script.md" in html
    assert "重新抬起卷帘门" in html
    assert 'class="dg-inline-del"' in html
    assert 'class="dg-inline-add"' in html
    assert "当前内容" in html and "提案内容" in html
    assert "现在没有需要自动转换的建议" not in html
    assert html.index("待我决定") < html.index(proposal.id)
    assert "确认这份提案" in html
    assert "执行已确认提案" not in html
    assert proposal.id in html
    assert "技术详情" in html

    confirm(tmp_project, proposal.id, actor="human")
    confirmed = director_page.render_director(tmp_project, "tok")
    assert "执行已确认提案" in confirmed
    assert "确认只冻结了这份内容，还没有写入项目" in confirmed


def test_director_stale_proposal_is_explicit_and_not_confirmable(tmp_project):
    _fill_story(tmp_project)
    proposal = propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{"path": "story/script.md", "content": SCRIPT + "\n提案版本。\n"}],
    }], actor="ai")
    _write(tmp_project, "story/script.md", SCRIPT + "\n用户已经继续修改。\n")

    html = director_page.render_director(tmp_project, "tok")
    assert proposal.id in html
    assert "项目内容已经变化" in html
    assert "确认这份提案" not in html
    assert "按当前项目重新起草" in html



def test_director_expired_proposal_is_explained_as_stale(tmp_project, monkeypatch):
    _fill_story(tmp_project)
    fake = {
        "id": "proposal-expired",
        "state": "expired",
        "why": "旧的创作建议",
        "actor": "ai",
        "created_at": "2026-08-14T00:00:00+00:00",
        "current": False,
        "actions": [{
            "action": {
                "type": "truth_patch_set",
                "patches": [{"path": "story/script.md", "content": SCRIPT + "\n旧建议。\n"}],
            },
            "impact": {"outputs": ["story/script.md"]},
            "estimated_cost": 0.0,
            "currency": None,
            "result": None,
        }],
        "estimated_cost": 0.0,
        "currency": None,
        "outcome": None,
    }
    monkeypatch.setattr(
        director_page,
        "proposals_payload",
        lambda project: {"proposals": [fake], "suggestions": []},
    )

    html = director_page.render_director(tmp_project, "tok")
    assert "项目内容已经变化" in html
    assert "确认这份提案" not in html
    assert "执行已确认提案" not in html
    assert "否决并保留记录" in html


def test_director_large_patch_uses_bounded_primary_preview(tmp_project):
    _fill_story(tmp_project)
    long_before = "甲" * 21_000
    _write(tmp_project, "story/script.md", long_before)
    propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{"path": "story/script.md", "content": long_before[:-1] + "乙"}],
    }], actor="ai")

    html = director_page.render_director(tmp_project, "tok")
    assert "内容较长，主界面不展开全文" in html
    assert html.count("甲") < 2_000
    assert "查看统一差异" in html


def test_director_patch_content_is_escaped(tmp_project):
    _fill_story(tmp_project)
    propose(tmp_project, [{
        "type": "truth_patch_set",
        "patches": [{
            "path": "story/script.md",
            "content": SCRIPT + "\n<script>window.evil = 1</script>\n",
        }],
    }], actor="ai")

    html = director_page.render_director(tmp_project, "tok")
    assert "<script>window.evil" not in html
    assert "&lt;script&gt;window.evil" in html

def test_director_history_is_progressively_disclosed(tmp_project):
    _fill_story(tmp_project)
    proposal = propose(tmp_project, [{"type": "snapshot", "label": "discard"}], actor="ai")
    reject(tmp_project, proposal.id, actor="human")

    html = director_page.render_director(tmp_project, "tok")
    assert "历史提案" in html
    assert '<details class="dg-history panel"' in html
    assert "已否决" in html


def test_strict_zero_cost_confirmed_priced_proposal_cannot_look_executable(
    tmp_project, monkeypatch
):
    monkeypatch.setenv("MANJU_EXECUTION_MODE", "strict_zero_cost")
    fake = {
        "id": "proposal-priced",
        "state": "confirmed",
        "why": "生成一条候选",
        "actor": "human",
        "created_at": "2026-08-14T00:00:00+00:00",
        "current": True,
        "actions": [{
            "action": {"type": "build", "target": "final", "gen": "missing"},
            "impact": {"shots": ["S001"], "outputs": ["timeline", "final"]},
            "estimated_cost": 8.0,
            "currency": "CNY",
            "result": None,
        }],
        "estimated_cost": 8.0,
        "currency": "CNY",
        "outcome": None,
    }
    monkeypatch.setattr(
        director_page,
        "proposals_payload",
        lambda project: {"proposals": [fake], "suggestions": []},
    )

    html = director_page.render_director(tmp_project, "tok")
    assert "当前是本地安全模式" in html
    assert "执行已确认提案" in html
    assert 'data-act="run"' not in html
    assert "disabled" in html

def test_create_javascript_guards_duplicate_save_and_scaffold_requests():
    js = create_page.render_create_js()
    assert 'if (btn && btn.disabled) return;' in js
    assert 'btn.textContent = "保存中…"' in js
    assert 'btn.textContent = "生成中…"' in js
    assert '项目没有被修改' in js
