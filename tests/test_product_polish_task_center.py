"""Product Polish R1 Wave 10: one honest task center across every GUI page.

The tests pin presentation and boundaries, not a second task state machine:
JobRunner/jobkinds remain authoritative, the task-center projection is read-only,
and all actions continue to use the existing cancel/retry/quit endpoints.
"""

from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection

from manju.build.graph import WaitingUser
from manju.gui.jobs import Job
from manju.gui.pages import GLOSSARY_HEAD, nav_html
from manju.gui.project_action import render_project_action_js
from manju.gui.server import _waiting_user_result, create_server
from manju.gui.task_center import (
    present_job,
    render_task_center_js,
    task_center_snapshot,
)
from manju.gui.webclient import render_webclient_js
from manju.providers.zero_cost import execution_policy_snapshot


class _Runner:
    def __init__(self, jobs):
        self._jobs = list(jobs)

    def list(self):
        return list(self._jobs)

    def interrupted(self):
        return []


def _get_json(server, path: str):
    host, port = server.server_address[:2]
    conn = HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read().decode("utf-8")
    conn.close()
    return response.status, json.loads(body)


def _get_text(server, path: str):
    host, port = server.server_address[:2]
    conn = HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read().decode("utf-8")
    conn.close()
    return response.status, body


def test_build_progress_is_human_language_but_raw_phase_remains_auditable():
    view = present_job(Job(
        id="job-1",
        kind="build",
        params={"target": "final"},
        state="running",
        progress="gen:S003 (3/12)",
    ))

    assert view["label"] == "构建"
    assert view["group"] == "active"
    assert view["phase"] == {
        "key": "generate_item",
        "label": "生成 S003",
        "detail": "第 3 / 12 个",
        "raw": "gen:S003 (3/12)",
    }
    assert view["cancel"]["confirm"] is True  # paid remote work: honest warning
    assert view["next_action"] is None


def test_waiting_user_is_attention_and_never_rendered_as_plain_done():
    job = Job(id="job-2", kind="redo", params={"shot": "S001"}, state="done")
    job.result = {"waiting_user": True, "estimated_cost": 2.5, "currency": "CNY"}
    view = present_job(job)

    assert view["state"] == "done"  # underlying JobRunner fact is unchanged
    assert view["state_key"] == "waiting_user"
    assert view["state_label"] == "等待确认"
    assert view["group"] == "attention"
    assert view["phase"]["label"] == "等待你确认费用"
    assert view["next_action"] == {"href": "/", "label": "查看费用计划"}
    assert view["cost"] == {
        "estimated": 2.5,
        "currency": "CNY",
        "label": "预估费用 2.5 CNY",
    }
    assert "预估费用 2.5 CNY" in view["notification"]["body"]
    assert "尚未执行" in view["notification"]["body"]


def test_uncertain_remote_cancel_stays_attention_not_clean_recent_history():
    job = Job(id="job-3", kind="build", params={}, state="canceled")
    job.billing = {
        "may_have_billed": True,
        "provider_job_id": "remote-42",
    }
    view = present_job(job)

    assert view["state_key"] == "cancel_unknown"
    assert view["group"] == "attention"
    assert view["state_label"] == "待核对"
    assert "可能仍在运行并计费" in view["billing_message"]
    assert "remote-42" in view["billing_message"]
    assert view["retryable"] is False
    assert view["next_action"] == {"href": "/providers", "label": "核对 Provider 状态"}


def test_waiting_user_adapter_preserves_structured_cost_without_parsing_text():
    payload = _waiting_user_result(
        WaitingUser("waiting_user: human message", 3.75, "CNY"),
        shot="S002",
    )

    assert payload == {
        "waiting_user": True,
        "errors": ["waiting_user: human message"],
        "estimated_cost": 3.75,
        "currency": "CNY",
        "shot": "S002",
    }


def test_non_monetary_confirmation_does_not_claim_a_fee():
    job = Job(id="job-confirm", kind="export", params={}, state="done")
    job.result = {"waiting_user": True, "estimated_cost": 0.0}

    view = present_job(job)

    assert view["phase"]["label"] == "等待你确认"
    assert view["phase"]["detail"] == "操作尚未执行"
    assert view["next_action"] == {
        "href": "/exports",
        "label": "查看待确认操作",
    }
    assert "费用" not in view["notification"]["body"]


def test_strict_zero_cost_snapshot_removes_remote_cancel_alarm(monkeypatch):
    monkeypatch.setenv("MANJU_EXECUTION_MODE", "strict_zero_cost")
    running = Job(id="strict-build", kind="build", params={}, state="running")

    view = task_center_snapshot(
        _Runner([running]),
        app_status={"shutdown_state": "open", "execution_policy": execution_policy_snapshot()},
    )["items"][0]

    assert view["paid"] is True  # kind capability remains auditable
    assert view["billing_risk"] is False
    assert view["cancel"]["confirm"] is False
    assert "已阻止外部 Provider" in view["cancel"]["message"]


def test_completed_job_keeps_the_completed_operation_visible():
    job = Job(id="done-qc", kind="qc", params={"lang": "zh-CN"}, state="done")
    job.progress = "qc"

    view = present_job(job)

    assert view["state_label"] == "已完成"
    assert view["phase"] == {
        "key": "qc",
        "label": "检查质量",
        "detail": "已完成",
        "raw": "qc",
    }


def test_recent_jobs_sort_by_finish_time_not_old_start_time():
    older_start_newer_finish = Job(id="newer", kind="qc", params={}, state="done")
    older_start_newer_finish.started = "2026-08-14T08:00:00+00:00"
    older_start_newer_finish.finished = "2026-08-14T10:00:00+00:00"
    newer_start_older_finish = Job(id="older", kind="qc", params={}, state="done")
    newer_start_older_finish.started = "2026-08-14T09:00:00+00:00"
    newer_start_older_finish.finished = "2026-08-14T09:30:00+00:00"

    view = task_center_snapshot(_Runner([newer_start_older_finish, older_start_newer_finish]))

    assert [item["id"] for item in view["items"]] == ["newer", "older"]


def test_snapshot_groups_current_runner_facts_without_mutating_them():
    running = Job(id="a", kind="qc", params={"lang": "zh-CN"}, state="running")
    running.progress = "qc"
    queued = Job(id="b", kind="export", params={}, state="queued")
    waiting = Job(id="c", kind="build", params={}, state="done")
    waiting.result = {"waiting_user": True}
    runner = _Runner([running, queued, waiting])

    before = [(j.id, j.state, j.progress, j.result) for j in runner.list()]
    first = task_center_snapshot(runner, app_status={"shutdown_state": "open"})
    second = task_center_snapshot(runner, app_status={"shutdown_state": "open"})
    after = [(j.id, j.state, j.progress, j.result) for j in runner.list()]

    assert before == after
    assert first == second
    assert first["summary"] == {
        "attention": 1,
        "active": 1,
        "queued": 1,
        "recent": 0,
        "total": 3,
        "in_progress": 2,
    }
    assert [item["group"] for item in first["items"]] == [
        "attention", "active", "queued"
    ]


def test_shared_chrome_loads_one_task_center_and_permanent_safe_exit():
    body = nav_html("/review", mode="beginner", project_name="我的项目")

    assert body.count('id="mj-task-center-btn"') == 1
    assert body.count('id="mj-task-center-count"') == 1
    assert body.count('id="mj-quit-btn"') == 1
    assert 'aria-haspopup="dialog"' in body
    assert 'aria-controls="mj-task-center"' in body
    assert GLOSSARY_HEAD.count('/task-center.css') == 1
    assert GLOSSARY_HEAD.count('/task-center.js') == 1


def test_shared_request_owner_signals_accepted_background_work():
    js = render_webclient_js()

    assert 'response.status === 202' in js
    assert 'data.job || data.jobs' in js
    assert 'new CustomEvent("manju:jobs-changed"' in js


def test_task_center_refreshes_immediately_and_deduplicates_overlapping_reads():
    js = render_task_center_js()

    assert 'window.addEventListener("manju:jobs-changed"' in js
    assert "if (refreshPromise) return refreshPromise" in js
    assert '"显示其余 " + rest.length + " 项"' in js


def test_task_center_endpoint_and_assets_are_available_on_real_server(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, payload = _get_json(server, "/api/task-center")
        assert status == 200
        assert payload["version"] == 1
        assert payload["summary"]["total"] == 0
        assert payload["app"]["shutdown_state"] == "open"

        status, js = _get_text(server, "/task-center.js")
        assert status == 200
        assert "window.ManjuTaskCenter" in js
        assert "/api/task-center" in js
        assert "/api/jobs/cancel" in js
        assert "/api/jobs/retry" in js
        assert "Notification.requestPermission" in js

        status, css = _get_text(server, "/task-center.css")
        assert status == 200
        assert ".mj-task-drawer" in css
        assert "prefers-reduced-motion" in css
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_app_status_uses_same_human_phase_owner(tmp_project, monkeypatch):
    monkeypatch.delenv("MANJU_EXECUTION_MODE", raising=False)
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    started = threading.Event()
    release = threading.Event()

    def work(job):
        job.progress = "render:final"
        started.set()
        release.wait(timeout=5)
        return {"ok": True}

    try:
        server.runner.submit("build", {}, work)
        assert started.wait(timeout=3)
        status = server.app_status()
        running = status["running_job"]
        assert running["display_name"] == "构建"
        assert running["phase_label"] == "渲染成片"
        assert running["summary"] == "渲染成片"
        assert running["cancelable"] is True
        assert running["paid"] is True
        assert running["billing_risk"] is True
    finally:
        release.set()
        deadline = time.monotonic() + 3
        while server.runner.busy() and time.monotonic() < deadline:
            time.sleep(0.01)
        server.close()


def test_quit_dialog_explains_queued_work_and_paid_cancel_uncertainty():
    js = render_project_action_js()

    assert "无论选择哪种退出方式，它们都会移出队列" in js
    assert "请求取消不等于远端已经停止或停止计费" in js
    assert "running.display_name" in js
    assert "running.phase_label" in js
    assert "running.billing_risk" in js
    assert "当前阶段不支持中途取消" in js
    assert "移出队列并退出" in js
    assert 'b2.className = "danger"' in js
    assert 'event.key === "Escape"' in js


def test_task_center_owns_cross_page_notifications_and_old_surfaces_are_fallbacks():
    js = render_task_center_js()

    assert "sessionStorage" in js  # survives ordinary page navigation in one tab
    assert 'storageStem + ":notified"' in js
    assert "notificationEnabled()" in js
    assert "Notification.requestPermission" in js  # only the explicit settings action
    assert "window.confirm" not in js
    assert 'dialog.setAttribute("role", "alertdialog")' in js
    assert "liveCount = attention + progress" in js
    assert '"最近结束"' in js

    from manju.gui.common_js import render_common_js
    from manju.gui.page import render_js

    assert "if (window.ManjuTaskCenter) return;" in render_common_js()
    home = render_js()
    assert "if (!window.ManjuTaskCenter) ensureNotifyPermission();" in home
    assert "if (window.ManjuTaskCenter) return;" in home
