"""Product Polish R1 Wave 7: one honest shot-production path.

The path is intentionally branching: the shot lab and batch ingest are two
alternative ways to obtain append-only candidates, and both converge on the
existing human review surface.  The projection must remain read-only and must
never auto-select, approve, lock, call providers or become a build input.
"""

from __future__ import annotations

import hashlib

from manju.gui.common_js import render_common_js
from manju.gui.ingest_page import render as render_ingest, render_ingest_js
from manju.gui.lab_page import render as render_lab
from manju.gui.pages import render_pages_js
from manju.gui.shot_journey import shot_journey_html, shot_production_summary
from manju.gui.storyboard import render as render_storyboard


def _tree_snapshot(root):
    rows = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows[rel] = ("symlink", path.readlink().as_posix())
        elif path.is_file():
            rows[rel] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            rows[rel] = ("dir", "")
    return rows


def _set_status(project, shot_id, *, selected=None, note=None, review="needs_review"):
    shot = project.load_shot(shot_id)
    shot.status.selected_take = selected
    shot.status.review = review
    shot.status.approved = review == "approved"
    if selected is not None:
        if note is None:
            shot.status.take_notes.pop(selected, None)
        else:
            shot.status.take_notes[selected] = note
    project.save_shot(shot)


def test_journey_is_branching_not_a_false_four_step_pipeline(tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "她推开门"})
    body = shot_journey_html("/lab", tmp_project, shot_id="S001")

    assert body.count('class="mj-shot-num"') == 3
    assert "规划镜头" in body
    assert "获得候选" in body
    assert "人工审片" in body
    assert 'href="/lab?shot=S001"' in body
    assert 'href="/ingest?shot=S001&amp;role=take"' in body
    assert 'href="/review?shot=S001"' in body
    assert "实验室和批量入库是两条替代路径" in body
    assert 'data-active="/lab"' in body
    assert 'class="active" href="/lab?shot=S001"' in body
    assert "S001" in body and "她推开门" in body


def test_empty_summary_points_to_authoring_without_writing(tmp_project):
    before = _tree_snapshot(tmp_project.root)
    summary = shot_production_summary(tmp_project)
    body = shot_journey_html("/storyboard", tmp_project)
    after = _tree_snapshot(tmp_project.root)

    assert summary["total"] == 0
    assert summary["next"] == {
        "href": "/create",
        "label": "先建立第一个镜头",
        "kind": "plan",
        "shot": None,
    }
    assert "0</b> 镜头" in body
    assert after == before


def test_summary_uses_current_selection_note_not_old_candidate_history(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    take1 = make_take(tmp_project, "S001", "sha256:one")
    take2 = make_take(tmp_project, "S001", "sha256:two")

    shot = tmp_project.load_shot("S001")
    shot.status.selected_take = take2.name
    shot.status.take_notes[take1.name] = "好 · 旧候选"
    tmp_project.save_shot(shot)

    summary = shot_production_summary(tmp_project)
    assert summary["with_candidates"] == 1
    assert summary["selected"] == 1
    assert summary["reviewed"] == 0
    assert summary["next"]["kind"] == "review"
    assert summary["next"]["shot"] == "S001"

    _set_status(tmp_project, "S001", selected=take2.name, note="好 · 当前候选")
    summary = shot_production_summary(tmp_project)
    assert summary["reviewed"] == 1
    assert summary["next"]["kind"] == "approve"

    _set_status(
        tmp_project,
        "S001",
        selected=take2.name,
        note="好 · 当前候选",
        review="approved",
    )
    summary = shot_production_summary(tmp_project)
    assert summary["approved"] == 1
    assert summary["next"]["href"] == "/edit"


def test_next_action_prioritises_missing_candidate_then_selection(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    take = make_take(tmp_project, "S002", "sha256:two")

    summary = shot_production_summary(tmp_project)
    assert summary["next"]["href"] == "/lab?shot=S001"
    assert summary["next"]["kind"] == "candidate"

    make_take(tmp_project, "S001", "sha256:one")
    summary = shot_production_summary(tmp_project)
    assert summary["next"]["href"] == "/review?shot=S001"
    assert summary["next"]["kind"] == "select"

    _set_status(tmp_project, "S001", selected=tmp_project.takes("S001")[0].name)
    _set_status(tmp_project, "S002", selected=take.name, note="好", review="approved")
    summary = shot_production_summary(tmp_project)
    assert summary["next"]["href"] == "/review?shot=S001"
    assert summary["next"]["kind"] == "review"


def test_storyboard_lab_and_ingest_share_the_path_and_preserve_shot_context(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001", action={"main": "她推开门"})
    take = make_take(tmp_project, "S001", "sha256:one")
    _set_status(tmp_project, "S001", selected=take.name)

    storyboard = render_storyboard(tmp_project, "token")
    lab = render_lab(tmp_project, "token", {"shot": ["S001"]})
    ingest = render_ingest(
        tmp_project,
        "token",
        {"shot": ["S001"], "role": ["take"]},
    )

    for body, active in (
        (storyboard, "/storyboard"),
        (lab, "/lab"),
        (ingest, "/ingest"),
    ):
        assert body.count("data-shot-journey") == 1
        assert f'data-active="{active}"' in body
        assert 'href="/review?shot=S001"' in body

    assert 'class="sb-shot-link" href="/lab?shot=S001"' in storyboard
    assert 'href="/ingest?shot=S001&amp;role=take">导入本地候选</a>' in storyboard
    assert 'href="/review?shot=S001">去审片</a>' in storyboard

    assert "先试算，再确认生成" in lab
    assert "当前选择" in lab
    assert 'href="/ingest?shot=S001&amp;role=take">导入本地候选</a>' in lab
    assert 'href="/review?shot=S001">去审片</a>' in lab

    assert 'value="take" selected' in ingest
    assert 'id="ing-shot" value="S001"' in ingest
    assert "已预设为 <b>S001</b> 的视频候选" in ingest
    assert "文件命名和入库规则" in ingest


def test_ingest_invalid_query_does_not_force_unknown_shot_or_role(tmp_project):
    body = render_ingest(
        tmp_project,
        "token",
        {"shot": ["../bad"], "role": ["paid_magic"]},
    )
    assert 'id="ing-shot" value=""' in body
    assert '<option value="auto" selected>' in body
    assert "已预设为" not in body


def test_ingest_completion_routes_to_batch_review_then_human_review():
    js = render_ingest_js()
    assert 'showReviewLink(result.batch_id, landedTakes.length > 0' in js
    assert 'a.textContent = "检查本批次"' in js
    assert 'review.textContent = "去审片"' in js
    assert 'review.href = "/review"' in js
    assert '"历史选择"' in js
    assert 'stagedBadge.textContent = "历史自动选择"' in js
    assert "当前入库不会自动选择候选" in js


def test_review_deep_link_overrides_saved_position_and_taskbar_is_honest():
    pages_js = render_pages_js()
    common_js = render_common_js()

    assert 'new URLSearchParams(location.search).get("shot")' in pages_js
    assert "if (requestedShot)" in pages_js
    assert pages_js.index("if (requestedShot)") < pages_js.index("var savedShot")
    assert 'ingest: ["/ingest#ing-review", "检查本批次"]' in common_js


def test_selected_candidate_card_has_no_redundant_select_mutation(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:one")
    _set_status(tmp_project, "S001", selected=take.name)

    body = render_lab(tmp_project, "token", {"shot": ["S001"]})
    start = body.index(f'data-take="{take.name}"')
    card = body[start : body.index('</div></div>', start) + len('</div></div>')]
    assert "当前选择" in card
    assert 'data-lab="pick"' not in card


def test_explicit_shot_context_does_not_jump_to_an_unrelated_global_next(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001", action={"main": "她推开门"})
    add_shot(tmp_project, "S002", action={"main": "他看向窗外"})
    take = make_take(tmp_project, "S001", "sha256:one")
    _set_status(tmp_project, "S001", selected=take.name)

    # S002 has no candidate, so it is the project-wide next action.  While the
    # user explicitly works on S001, however, the call to action must stay on
    # S001 until that shot's current candidate is reviewed.
    body = shot_journey_html("/lab", tmp_project, shot_id="S001")
    assert 'href="/review?shot=S001"' in body
    assert "评价 S001 的当前候选" in body
    assert "为 S002 准备候选" not in body


def test_completed_focused_shot_labels_the_global_fallback_as_project_next(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001", action={"main": "她推开门"})
    add_shot(tmp_project, "S002", action={"main": "他看向窗外"})
    take = make_take(tmp_project, "S001", "sha256:one")
    _set_status(
        tmp_project,
        "S001",
        selected=take.name,
        note="好 · 当前候选",
        review="approved",
    )

    body = shot_journey_html("/lab", tmp_project, shot_id="S001")
    assert "项目下一步：为 S002 准备候选" in body
    assert "下一步：为 S002 准备候选" in body



def test_nonempty_summary_is_read_only(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:one")
    before = _tree_snapshot(tmp_project.root)
    shot_production_summary(tmp_project)
    shot_journey_html("/storyboard", tmp_project)
    assert _tree_snapshot(tmp_project.root) == before


def test_lab_progressively_discloses_engineering_detail(tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "她推开门"})
    body = render_lab(tmp_project, "token", {"shot": ["S001"]})

    assert "当前视频提示词" in body
    assert "其它提示词与路由详情" in body
    assert "镜头结构与高级改写" in body
    assert "参考来源与质量检查" in body
    assert 'class="mj-en" aria-hidden="true"> (generation.prompt_override)' in body
    assert "自动（兜底链）" in body


def test_ingest_starts_with_an_honest_disabled_batch_review(tmp_project):
    body = render_ingest(tmp_project, "token")
    js = render_ingest_js()

    assert 'id="ing-rv-batch" disabled' in body
    assert 'id="ing-rv-confirm-all" disabled' in body
    assert "正在读取已有批次" in body
    assert "确认匹配不会自动选择镜头候选" in body
    assert "sel.disabled = batches.length === 0" in js
    assert "confirmAll.disabled = batches.length === 0" in js
    assert "var rvBatchSelected = false" in js
    assert "这个批次没有可评审条目" in js
    assert "暂时无法读取入库批次" in js
