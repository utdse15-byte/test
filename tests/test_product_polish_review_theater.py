"""Product Polish R1 Wave 6 — the review surface behaves like a focused theater.

The tests pin presentation and interaction boundaries only.  Existing mutation
owners (/api/take-note, /api/select, /api/storyboard/approve, repair/redo) remain
unchanged and are covered by the long-standing review tests.
"""

from __future__ import annotations

from manju.gui import pages


def _select(project, shot_id: str, take_name: str) -> None:
    project.update_shot_raw(
        shot_id,
        lambda data: data.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def test_review_theater_has_focused_stage_and_navigable_queue(
    tmp_project, add_shot, make_take
):
    for index in range(1, 4):
        sid = f"S{index:03d}"
        add_shot(tmp_project, sid, action={"main": f"镜头动作 {index}"})
        take = make_take(tmp_project, sid, f"h{index}")
        _select(tmp_project, sid, take.name)

    html = pages.render_review(tmp_project, "tok")

    assert 'class="rv-decision-guide panel"' in html
    assert 'class="rv-theater"' in html
    assert 'class="rv-stage"' in html
    assert 'class="rv-rail panel"' in html
    assert 'class="rv-rail-details"' in html
    assert html.count('class="rv-rail-item') == 3
    assert 'data-rv-target="S001"' in html
    assert 'aria-label="审片队列"' in html
    assert 'id="rv-shortcuts"' in html


def test_review_theater_explains_verdict_selection_and_lock_are_distinct(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    selected = make_take(tmp_project, "S001", "h")
    make_take(tmp_project, "S001", "h2")
    _select(tmp_project, "S001", selected.name)

    html = pages.render_review(tmp_project, "tok")

    assert "系统不会自动改变当前选择、镜头审批或锁片" in html
    assert "只记录本次评价，不会换用候选" in html
    assert "只有显式选用其它候选" in html
    assert "确认镜头可以继续，但不会自动锁片" in html
    assert "当前选择" in html and selected.name in html
    assert "镜头审批" in html
    assert "推荐并下一条" in html
    assert "不推荐，查看其它候选" in html
    assert "稍后处理" in html
    assert "确认镜头通过" in html
    assert "其它候选" in html
    assert "选用 " in html


def test_review_advanced_actions_are_progressively_disclosed(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    selected = make_take(tmp_project, "S001", "h")
    make_take(tmp_project, "S001", "h2")
    _select(tmp_project, "S001", selected.name)

    html = pages.render_review(tmp_project, "tok")

    assert '<details class="rv-more">' in html
    assert "更多操作" in html
    assert 'data-act="redo"' in html
    assert 'data-act="repair"' in html
    assert 'data-act="route"' in html
    assert 'data-act="ai-ctx"' in html
    assert 'data-act="clear-verdict"' in html
    assert 'class="btn ghost rv-clear-verdict"' in html
    assert 'rv-clear-verdict" data-act="clear-verdict" hidden' in html
    assert html.index('class="rv-actions') < html.index('class="rv-body')


def test_review_frame_is_evidence_not_a_second_default_player(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    selected = make_take(tmp_project, "S001", "h")
    _select(tmp_project, "S001", selected.name)
    frame = tmp_project.reports_dir / "frames" / "S001.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(b"fake-jpeg")

    html = pages.render_review(tmp_project, "tok")

    assert '<details class="rv-frame">' in html
    assert "查看质检抽帧" in html
    frame_details = html.split('<details class="rv-frame">', 1)[1].split("</details>", 1)[0]
    assert "查看质检抽帧" in frame_details
    assert '/media/reports/frames/S001.jpg' in frame_details


def test_review_progress_only_counts_the_current_selected_take(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    old_take = make_take(tmp_project, "S001", "old")
    selected = make_take(tmp_project, "S001", "current")
    tmp_project.update_shot_raw(
        "S001",
        lambda data: data.setdefault("status", {}).update(
            {
                "selected_take": selected.name,
                "take_notes": {old_take.name: "好 · 旧候选备注"},
            }
        ),
    )

    html = pages.render_review(tmp_project, "tok")

    assert 'data-take="%s" data-reviewed="0"' % selected.name in html
    assert "已评价 0 / 1" in html
    assert 'data-verdict="pending"' in html
    assert "未评价" in html


def test_review_note_is_presented_as_verdict_plus_rationale(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    selected = make_take(tmp_project, "S001", "h")
    tmp_project.update_shot_raw(
        "S001",
        lambda data: data.setdefault("status", {}).update(
            {
                "selected_take": selected.name,
                "take_notes": {selected.name: "弃 · 手部动作不自然"},
            }
        ),
    )

    html = pages.render_review(tmp_project, "tok")

    assert 'data-verdict="reject"' in html
    assert 'data-note-raw="弃 · 手部动作不自然"' in html
    assert "不推荐" in html
    assert 'value="手部动作不自然"' in html
    assert "已评价 1 / 1" in html
    assert 'rv-clear-verdict" data-act="clear-verdict">' in html


def test_approved_shot_does_not_offer_a_redundant_approval_action(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    selected = make_take(tmp_project, "S001", "h")
    tmp_project.update_shot_raw(
        "S001",
        lambda data: data.setdefault("status", {}).update(
            {
                "selected_take": selected.name,
                "review": "approved",
                "approved": True,
            }
        ),
    )

    html = pages.render_review(tmp_project, "tok")

    assert "镜头已通过" in html
    assert 'data-act="qapprove" title="确认镜头层面的人工审批，不会锁片" disabled' in html


def test_review_js_owns_queue_rail_offsets_and_shortcut_help():
    js = pages.render_pages_js()

    assert "syncReviewOffsets" in js
    assert 'setProperty("--rv-nav-h"' in js
    assert '--rv-workbar-h' not in js
    assert 'closest(".rv-rail-item")' in js
    assert "syncRailItem" in js
    assert "noteTextFor" in js and "syncVerdictFact" in js
    assert 'act === "clear-verdict"' in js
    assert 'e.key === "?"' in js
    assert 'queueMode ? "浏览全部镜头" : "只看当前镜头"' in js


def test_review_css_has_desktop_rail_and_narrow_horizontal_queue():
    css = pages.render_pages_css()

    assert ".rv-theater" in css
    assert "grid-template-columns: minmax(0, 1fr) 280px" in css
    assert ".rv-rail" in css
    assert "grid-auto-flow: column" in css
    assert "--rv-nav-h" in css and "--rv-workbar-h" not in css
    assert ".rv-primary-action" in css
    assert ".rv-decision-state" in css and ".rv-decision-cell" in css
    assert ".rv-decision-guide" in css
    assert ".rv-more" in css
    assert '.rv-shot.reviewed .rv-head h2::after' not in css


def test_review_queue_skip_advances_the_priority_queue_not_file_order():
    js = pages.render_pages_js()

    branch = js.split('else if (act === "skip")', 1)[1].split('else if (act === "note")', 1)[0]
    assert "if (queueMode)" in branch
    assert "qIndex++" in branch and "updateQueueUI()" in branch
    assert "else setActive(active + 1)" in branch


def test_review_verdict_sync_owns_clear_action_and_external_handoff_copy():
    js = pages.render_pages_js()

    sync = js.split("function syncVerdictFact", 1)[1].split("function syncReviewed", 1)[0]
    assert 'querySelector(".rv-clear-verdict")' in sync
    assert 'clearButton.hidden = !(raw || "").trim()' in sync
    handoff = js.split('act === "ai-ctx"', 1)[1].split('act === "route"', 1)[0]
    assert '"当前评价: " + copiedVerdict.label' in handoff
    assert "Claude" not in handoff
    assert "IDE 助手" in handoff



def test_review_live_state_labels_match_server_and_approval_disables_itself():
    js = pages.render_pages_js()

    verdict = js.split("function verdictState", 1)[1].split("function noteRationale", 1)[0]
    assert 'label: "推荐"' in verdict
    assert 'label: "不推荐"' in verdict
    assert 'label: "未评价"' in verdict
    approval = js.split('act === "qapprove"', 1)[1].split('act === "redo"', 1)[0]
    assert 'approveButton.disabled = true' in approval
    assert 'approveButton.textContent = "镜头已通过"' in approval


def test_review_queue_controls_and_progress_expose_accessible_state(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    selected = make_take(tmp_project, "S001", "h")
    _select(tmp_project, "S001", selected.name)

    html = pages.render_review(tmp_project, "tok")
    assert 'role="progressbar"' in html
    assert 'aria-label="当前候选审片进度"' in html
    assert 'aria-valuenow="0"' in html
    assert 'style="width:0%"' in html
    assert 'id="rv-queue-toggle" aria-pressed="false"' in html
    assert 'data-filter="all" aria-pressed="true"' in html
    assert 'data-filter="needs_review" aria-pressed="false"' in html

    js = pages.render_pages_js()
    assert 'tg.setAttribute("aria-pressed"' in js
    assert 'c.setAttribute("aria-pressed"' in js
    assert 'progress.setAttribute("aria-valuenow"' in js


def test_review_narrow_header_and_queue_keep_the_current_item_in_view():
    css = pages.render_pages_css()
    js = pages.render_pages_js()

    assert "scroll-snap-type: x proximity" in css
    assert ".rv-rail-item { scroll-snap-align: start; }" in css
    narrow = css.split("@media (max-width: 760px)", 1)[1]
    assert ".rv-head { grid-template-columns: 1fr; }" in narrow
    assert ".rv-meta, .rv-next" in narrow
    assert ".rv-decision-guide { grid-template-columns: 1fr; }" in narrow
    assert "function scrollRailToActive" in js
    assert 'inline: "nearest"' in js
    set_active = js.split("function setActive", 1)[1].split("function currentVideo", 1)[0]
    assert "scrollRailToActive()" in set_active


def test_review_progress_excludes_shots_without_a_current_selection(
    tmp_project, add_shot, make_take
):
    add_shot(tmp_project, "S001")
    selected = make_take(tmp_project, "S001", "h")
    _select(tmp_project, "S001", selected.name)
    add_shot(tmp_project, "S002")
    make_take(tmp_project, "S002", "h2")
    add_shot(tmp_project, "S003")

    html = pages.render_review(tmp_project, "tok")

    assert "当前候选已评价 0 / 1" in html
    assert "另有 2 个镜头尚未选择候选" in html
    assert 'aria-valuemax="1"' in html
    assert 'style="width:0%"' in html


def test_review_progress_has_correct_server_rendered_width(
    tmp_project, add_shot, make_take
):
    for index in range(1, 5):
        sid = f"S{index:03d}"
        add_shot(tmp_project, sid)
        selected = make_take(tmp_project, sid, f"h{index}")
        _select(tmp_project, sid, selected.name)
        if index == 1:
            tmp_project.update_shot_raw(
                sid,
                lambda data, selected=selected: data.setdefault("status", {}).setdefault(
                    "take_notes", {}
                ).__setitem__(selected.name, "好"),
            )

    html = pages.render_review(tmp_project, "tok")

    assert "当前候选已评价 1 / 4" in html
    assert 'aria-valuemax="4"' in html
    assert 'aria-valuenow="1"' in html
    assert 'data-pct="25" style="width:25%"' in html


def test_review_queue_prioritizes_only_current_media_blockers(
    tmp_project, add_shot, make_take
):
    from manju.board.server import API_ACTIONS

    from manju.core.spec import compute_spec_hash

    add_shot(tmp_project, "S001", action={"main": "普通待审"})
    normal_hash = compute_spec_hash(
        tmp_project.load_shot("S001"),
        tmp_project.load_bible(),
        version=1,
        project_root=tmp_project.root,
    )
    normal = make_take(tmp_project, "S001", normal_hash)
    _select(tmp_project, "S001", normal.name)

    add_shot(tmp_project, "S002", action={"main": "当前媒体有阻塞"})
    blocked_hash = compute_spec_hash(
        tmp_project.load_shot("S002"),
        tmp_project.load_bible(),
        version=1,
        project_root=tmp_project.root,
    )
    blocked = make_take(tmp_project, "S002", blocked_hash)
    _select(tmp_project, "S002", blocked.name)
    API_ACTIONS["annotate"](
        tmp_project,
        {
            "shot": "S002",
            "take": blocked.name,
            "text": "右手穿帮",
            "severity": "blocker",
            "frame": 8,
        },
    )

    html = pages.render_review(tmp_project, "tok")
    rail = html.split('<div class="rv-rail-list">', 1)[1].split("</div></aside>", 1)[0]
    assert rail.index('data-rv-target="S002"') < rail.index('data-rv-target="S001"')
    assert 'data-rv-target="S002"' in rail and 'data-blocker="1"' in rail
    assert "有阻塞" in rail

    # Replacing the bytes makes the exact-media annotation stale.  It remains
    # visible as evidence, but must stop claiming the new bytes are blocked.
    info = tmp_project.get_take("S002", blocked.name)
    info.media_path.write_bytes(b"replacement-bytes")
    stale_html = pages.render_review(tmp_project, "tok")
    stale_card = stale_html.split('id="rv-S002"', 1)[1].split("</section>", 1)[0]
    assert 'data-blocker="0"' in stale_card
    assert "已过期" in stale_card
