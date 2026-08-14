from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from manju.core.container import Project
from manju.gui import (
    glossary,
    page as app_page,
    pages,
    project_action,
    task_center,
    webclient,
)
from manju.gui.jobs import Job
from manju.gui.state import project_identity
from manju.gui.userstate import set_mode, set_show_pro_terms
from manju.providers.zero_cost import execution_policy_snapshot


class Runner:
    def __init__(self, jobs):
        self.jobs = list(jobs)

    def list(self):
        return list(self.jobs)

    def interrupted(self):
        return []


def clean_html(html: str, css: str, project_token: str) -> str:
    html = re.sub(r'<link rel="stylesheet" href="[^"]+">\n?', '', html)
    html = re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>\n?', '', html)
    if '<meta name="manju-project"' not in html:
        html = html.replace(
            '<meta name="manju-token"',
            f'<meta name="manju-project" content="{project_token}">\n<meta name="manju-token"',
            1,
        )
    return html.replace('</head>', '<style>' + css + '</style></head>')


def make_project(root: Path) -> Project:
    for candidate in (root, Path(str(root) + ".manju")):
        if candidate.exists():
            shutil.rmtree(candidate)
    return Project.create(root, name="雨夜末班车", git_init=False)


def job_data(*, standard: bool = False):
    now = datetime.now(timezone.utc).replace(microsecond=0)

    waiting = Job(
        id="job-waiting",
        kind="redo",
        params={"shot": "S004"},
        state="done",
        created=(now - timedelta(minutes=4)).isoformat(),
        started=(now - timedelta(minutes=4)).isoformat(),
        finished=(now - timedelta(minutes=3, seconds=40)).isoformat(),
    )
    waiting.result = {
        "waiting_user": True,
        "estimated_cost": 2.4,
        "currency": "CNY",
        "shot": "S004",
    }

    uncertain = Job(
        id="job-uncertain",
        kind="build",
        params={},
        state="canceled",
        created=(now - timedelta(minutes=9)).isoformat(),
        started=(now - timedelta(minutes=8)).isoformat(),
        finished=(now - timedelta(minutes=1)).isoformat(),
    )
    uncertain.billing = {
        "may_have_billed": True,
        "provider_job_id": "remote-42",
    }

    running = Job(
        id="job-running",
        kind="build",
        params={"target": "final"},
        state="running",
        progress="gen:S003 (3/12)",
        created=(now - timedelta(minutes=2)).isoformat(),
        started=(now - timedelta(seconds=86)).isoformat(),
    )

    queued = Job(
        id="job-queued",
        kind="export",
        params={},
        state="queued",
        created=(now - timedelta(seconds=34)).isoformat(),
    )

    recent = Job(
        id="job-recent",
        kind="qc",
        params={"lang": "zh-CN"},
        state="done",
        progress="qc",
        created=(now - timedelta(minutes=12)).isoformat(),
        started=(now - timedelta(minutes=12)).isoformat(),
        finished=(now - timedelta(minutes=10)).isoformat(),
    )

    policy = {
        "status": "standard" if standard else "strict",
        "mode": "legacy_default" if standard else "strict_zero_cost",
    }
    app = {
        "app_mode": True,
        "shutdown_state": "open",
        "quit_mode": None,
        "running_job": {
            "id": running.id,
            "kind": running.kind,
            "state": running.state,
            "display_name": "构建",
            "phase_label": "生成 S003",
            "phase_detail": "第 3 / 12 个",
            "context": "",
            "cancelable": True,
            "paid": True,
            "billing_risk": standard,
        },
        "queued_count": 1,
        "execution_policy": policy,
    }
    snapshot = task_center.task_center_snapshot(
        Runner([waiting, uncertain, running, queued, recent]),
        app_status=app,
    )
    return snapshot, app


def browser_stub(snapshot: dict, app_status: dict) -> str:
    return """
      window.__calls = [];
      window.__notificationPermissionRequests = 0;
      window.__notifications = [];
      function DemoNotification(title, options) {
        window.__notifications.push({title:title, options:options || {}});
      }
      DemoNotification.permission = 'default';
      DemoNotification.requestPermission = async function () {
        window.__notificationPermissionRequests += 1;
        DemoNotification.permission = 'granted';
        return 'granted';
      };
      Object.defineProperty(window, 'Notification', {value: DemoNotification, configurable:true});
      window.requestJson = async function(method, path, body) {
        window.__calls.push({method:String(method), path:String(path), body:body || null});
        const key = String(path).split('?')[0];
        if (method === 'GET' && key === '/api/task-center') return JSON.parse(JSON.stringify(window.__taskSnapshot));
        if (method === 'GET' && key === '/api/app/status') return JSON.parse(JSON.stringify(window.__appStatus));
        if (method === 'POST' && key === '/api/jobs/cancel') return {ok:true};
        if (method === 'POST' && key === '/api/jobs/retry') return {ok:true};
        if (method === 'POST' && key === '/api/app/quit') return {ok:true};
        return {};
      };
      window.__taskSnapshot = %s;
      window.__appStatus = %s;
      window.setTimeout = function () { return 1; };
    """ % (
        json.dumps(snapshot, ensure_ascii=False),
        json.dumps(app_status, ensure_ascii=False),
    )


def prepare(page: Page, html: str, snapshot: dict, app: dict, project_js: str, task_js: str) -> None:
    page.set_content(html, wait_until="domcontentloaded")
    page.add_script_tag(content=browser_stub(snapshot, app))
    page.add_script_tag(content=project_js)
    page.add_script_tag(content=task_js)
    page.wait_for_function("window.ManjuTaskCenter && window.ManjuTaskCenter.current() !== null")


def task_facts(page: Page) -> dict:
    return page.evaluate("""() => ({
      viewport:{w:innerWidth,h:innerHeight},
      document:{w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight},
      horizontalOverflow:document.documentElement.scrollWidth-innerWidth,
      badge:document.querySelector('#mj-task-center-count')?.textContent || '',
      buttonTitle:document.querySelector('#mj-task-center-btn')?.title || '',
      groups:Array.from(document.querySelectorAll('.mj-task-group-title')).map(x=>x.textContent.trim()),
      cards:Array.from(document.querySelectorAll('.mj-task-item')).map(x=>x.textContent.trim()),
      scope:document.querySelector('#mj-task-scope-note')?.textContent || '',
      notificationPermissionRequests:window.__notificationPermissionRequests,
      calls:window.__calls.slice(),
      drawer:{
        hidden:document.querySelector('#mj-task-center')?.hidden,
        width:Math.round(document.querySelector('#mj-task-center')?.getBoundingClientRect().width || 0),
        role:document.querySelector('#mj-task-center')?.getAttribute('role') || '',
        modal:document.querySelector('#mj-task-center')?.getAttribute('aria-modal') || '',
      }
    })""")


def main() -> None:
    repo = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    work = Path(sys.argv[3]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["MANJU_EXECUTION_MODE"] = "strict_zero_cost"
    os.environ["MANJU_GUI_STATE"] = str(work.parent / "wave10-gui-state.json")
    set_mode("beginner")
    set_show_pro_terms(False)
    project = make_project(work)
    token = project_identity(project)

    body = (
        '<div class="page-h"><h1>服务商</h1><span class="muted">本地与未来云端能力</span></div>'
        '<section class="panel"><h2>本地安全</h2>'
        '<p>当前不会连接外部 Provider，也不会读取云端密钥。</p>'
        '<p class="muted">任务可以在页面之间继续；任务中心负责统一进度、提醒、取消与退出。</p></section>'
    )
    html = pages._shell("服务商", "tok", "/providers", body, project)
    css = "\n".join([
        app_page.render_css(),
        pages.render_pages_css(),
        glossary.render_glossary_css(),
        project_action.render_project_action_css(),
        task_center.render_task_center_css(),
    ])
    html = clean_html(html, css, token)
    task_js = task_center.render_task_center_js()
    project_js = project_action.render_project_action_js()
    strict_snapshot, strict_app = job_data(standard=False)
    standard_snapshot, standard_app = job_data(standard=True)

    evidence = {
        "schema": "manju.product-polish-task-center-evidence/v1",
        "wave": 10,
        "method": "real Manju server shell/CSS + actual task-center/project-action JavaScript via Playwright page.set_content; local deterministic API stubs; no network/provider/credentials",
        "screens": {},
        "interactions": {},
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )

        for name, viewport in {
            "task-center-desktop": {"width": 1440, "height": 900},
            "task-center-narrow": {"width": 390, "height": 844},
        }.items():
            context = browser.new_context(viewport=viewport, device_scale_factor=1)
            page = context.new_page()
            prepare(page, html, strict_snapshot, strict_app, project_js, task_js)
            if name == "task-center-narrow":
                closed = page.evaluate("""() => ({
                  horizontalOverflow:document.documentElement.scrollWidth-innerWidth,
                  appbar:{
                    width:Math.round(document.querySelector('.pnav-appbar')?.getBoundingClientRect().width || 0),
                    height:Math.round(document.querySelector('.pnav-appbar')?.getBoundingClientRect().height || 0),
                    scrollWidth:document.querySelector('.pnav-appbar')?.scrollWidth || 0
                  },
                  taskLabel:document.querySelector('.mj-task-label') ? getComputedStyle(document.querySelector('.mj-task-label')).display : '',
                  badge:document.querySelector('#mj-task-center-count')?.textContent || ''
                })""")
                assert closed["horizontalOverflow"] <= 0
                assert closed["appbar"]["scrollWidth"] <= closed["appbar"]["width"]
                assert closed["taskLabel"] == "none"
                page.screenshot(path=str(out / "task-center-appbar-narrow.png"), full_page=True)
                evidence["screens"]["task-center-appbar-narrow"] = closed
            page.locator("#mj-task-center-btn").click()
            page.wait_for_selector("#mj-task-center:not([hidden])")
            facts = task_facts(page)
            assert facts["horizontalOverflow"] <= 0
            assert facts["badge"] == "4"  # 2 attention + 1 active + 1 queued
            assert facts["buttonTitle"] == "2 项需要处理 · 2 项进行中"
            assert facts["drawer"]["role"] == "dialog"
            assert facts["drawer"]["modal"] == "true"
            assert facts["notificationPermissionRequests"] == 0
            assert "可以切换页面" in facts["scope"]
            assert any("预估费用 2.4 CNY" in card for card in facts["cards"])
            assert any("核对 Provider 状态" in card for card in facts["cards"])
            assert not any("重新发起" in card and "remote-42" in card for card in facts["cards"])
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)
            evidence["screens"][name] = facts
            context.close()

        # Standard-mode UI simulation only: the paid cancel confirmation must
        # be an explicit, focus-safe Manju dialog and must not call cancel until
        # the human chooses the dangerous action.
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        prepare(page, html, standard_snapshot, standard_app, project_js, task_js)
        page.locator("#mj-task-center-btn").click()
        running_card = page.locator(".mj-task-item", has_text="生成 S003")
        running_card.get_by_role("button", name="请求取消").click()
        page.wait_for_selector('.mj-task-confirm[role="alertdialog"]')
        before = page.evaluate("window.__calls.filter(x=>x.path==='/api/jobs/cancel').length")
        assert before == 0
        assert page.get_by_role("button", name="继续等待").evaluate("el => el === document.activeElement")
        page.screenshot(path=str(out / "task-cancel-confirm-desktop.png"), full_page=True)
        page.get_by_role("button", name="继续等待").click()
        assert page.locator(".mj-task-confirm").count() == 0
        running_card.get_by_role("button", name="请求取消").click()
        page.get_by_role("alertdialog").get_by_role("button", name="请求取消").click()
        page.wait_for_function("window.__calls.filter(x=>x.path==='/api/jobs/cancel').length === 1")
        evidence["interactions"]["paid_cancel"] = {
            "calls_before_confirm": before,
            "calls_after_confirm": page.evaluate("window.__calls.filter(x=>x.path==='/api/jobs/cancel').length"),
            "custom_alertdialog": True,
            "default_focus": "继续等待",
        }
        context.close()

        # Cross-page continuity: seed the previous page's running state in
        # sessionStorage, then load the new page with the same project token and
        # a terminal state. The first fetch must surface exactly one unseen item.
        cross_snapshot = json.loads(json.dumps(strict_snapshot))
        cross_snapshot["items"] = [
            {
                **next(x for x in cross_snapshot["items"] if x["id"] == "job-recent"),
                "id": "job-cross",
                "notification": {
                    "key": "job-cross:done",
                    "title": "Manju · 质检 · 已完成",
                    "body": "检查质量",
                },
            }
        ]
        cross_snapshot["summary"] = {
            "attention": 0, "active": 0, "queued": 0, "recent": 1,
            "total": 1, "in_progress": 0,
        }
        context = browser.new_context(viewport={"width": 900, "height": 700})
        page = context.new_page()
        page.set_content(html, wait_until="domcontentloaded")
        page.evaluate("""([key, value]) => {
          const store = {};
          Object.defineProperty(window, 'sessionStorage', {
            configurable:true,
            value:{
              getItem:k=>Object.prototype.hasOwnProperty.call(store,k)?store[k]:null,
              setItem:(k,v)=>{store[k]=String(v);},
              removeItem:k=>{delete store[k];}
            }
          });
          sessionStorage.setItem(key, value);
        }""", [
            "mj-task-center:" + token + ":states",
            json.dumps({"job-cross": "running"}),
        ])
        page.add_script_tag(content=browser_stub(cross_snapshot, strict_app))
        page.add_script_tag(content=project_js)
        page.add_script_tag(content=task_js)
        page.wait_for_function("window.ManjuTaskCenter && window.ManjuTaskCenter.current() !== null")
        cross = page.evaluate("""() => ({
          badge:document.querySelector('#mj-task-center-count')?.textContent || '',
          unseen:sessionStorage.getItem('mj-task-center:%s:unseen'),
          notified:JSON.parse(sessionStorage.getItem('mj-task-center:%s:notified') || '{}'),
          permissionRequests:window.__notificationPermissionRequests,
        })""" % (token, token))
        assert cross["badge"] == "1"
        assert cross["unseen"] == "1"
        assert cross["notified"].get("job-cross:done") is True
        assert cross["permissionRequests"] == 0
        evidence["interactions"]["cross_page_completion"] = cross
        context.close()

        # A successful 202 job submission from the shared request owner must
        # refresh the permanent task center immediately rather than waiting for
        # the idle poll. This uses the actual webclient and task-center scripts.
        context = browser.new_context(viewport={"width": 900, "height": 700})
        page = context.new_page()
        empty_snapshot = json.loads(json.dumps(strict_snapshot))
        empty_snapshot["items"] = []
        empty_snapshot["summary"] = {
            "attention": 0, "active": 0, "queued": 0, "recent": 0,
            "total": 0, "in_progress": 0,
        }
        queued_snapshot = json.loads(json.dumps(empty_snapshot))
        queued_item = next(x for x in strict_snapshot["items"] if x["id"] == "job-queued")
        queued_snapshot["items"] = [queued_item]
        queued_snapshot["summary"] = {
            "attention": 0, "active": 0, "queued": 1, "recent": 0,
            "total": 1, "in_progress": 1,
        }
        page.set_content(html, wait_until="domcontentloaded")
        page.evaluate("""([initial, later]) => {
          window.__fetchCalls = [];
          window.__taskSnapshot = initial;
          window.__laterTaskSnapshot = later;
          window.fetch = async function(path, init) {
            const method = String((init && init.method) || 'GET');
            window.__fetchCalls.push({method:method, path:String(path)});
            if (method === 'GET' && String(path) === '/api/task-center') {
              return new Response(JSON.stringify(window.__taskSnapshot), {
                status:200, headers:{'Content-Type':'application/json'}
              });
            }
            if (method === 'POST' && String(path) === '/api/demo-job') {
              window.__taskSnapshot = window.__laterTaskSnapshot;
              return new Response(JSON.stringify({job:{id:'accepted-job'}}), {
                status:202, headers:{'Content-Type':'application/json'}
              });
            }
            return new Response('{}', {status:200, headers:{'Content-Type':'application/json'}});
          };
        }""", [empty_snapshot, queued_snapshot])
        page.add_script_tag(content=webclient.render_webclient_js())
        page.add_script_tag(content=task_js)
        page.wait_for_function("window.ManjuTaskCenter && window.ManjuTaskCenter.current() !== null")
        initial_gets = page.evaluate("window.__fetchCalls.filter(x=>x.method==='GET' && x.path==='/api/task-center').length")
        page.evaluate("requestJson('POST', '/api/demo-job', {}, manjuApiOptions())")
        page.wait_for_function("document.querySelector('#mj-task-center-count')?.textContent === '1'")
        later_gets = page.evaluate("window.__fetchCalls.filter(x=>x.method==='GET' && x.path==='/api/task-center').length")
        assert later_gets == initial_gets + 1
        evidence["interactions"]["accepted_job_refresh"] = {
            "task_reads_before": initial_gets,
            "task_reads_after": later_gets,
            "badge": page.locator("#mj-task-center-count").inner_text(),
            "used_shared_webclient": True,
        }
        context.close()

        # Critical tasks are never truncated. Large queued/recent groups use a
        # disclosure that preserves the real total and keeps every row reachable.
        overflow_snapshot = json.loads(json.dumps(empty_snapshot))
        overflow_rows = []
        for index in range(11):
            row = json.loads(json.dumps(queued_item))
            row["id"] = f"queued-{index + 1:02d}"
            row["title"] = f"导出任务 {index + 1}"
            overflow_rows.append(row)
        overflow_snapshot["items"] = overflow_rows
        overflow_snapshot["summary"] = {
            "attention": 0, "active": 0, "queued": 11, "recent": 0,
            "total": 11, "in_progress": 11,
        }
        context = browser.new_context(viewport={"width": 900, "height": 700})
        page = context.new_page()
        prepare(page, html, overflow_snapshot, strict_app, project_js, task_js)
        page.locator("#mj-task-center-btn").click()
        page.wait_for_selector("#mj-task-center:not([hidden])")
        assert "接下来11" in page.locator(".mj-task-group-title").inner_text().replace("\n", "")
        assert page.get_by_text("显示其余 3 项", exact=True).count() == 1
        visible_before = page.locator(".mj-task-item:visible").count()
        page.get_by_text("显示其余 3 项", exact=True).click()
        visible_after = page.locator(".mj-task-item:visible").count()
        assert visible_before == 8
        assert visible_after == 11
        evidence["interactions"]["queued_disclosure"] = {
            "total": 11,
            "visible_before": visible_before,
            "visible_after": visible_after,
            "summary": "显示其余 3 项",
        }
        context.close()

        # Exit dialog uses the same current task facts and preserves the safe
        # default. The paid-risk alternative is visibly dangerous.
        context = browser.new_context(viewport={"width": 1100, "height": 760})
        page = context.new_page()
        prepare(page, html, standard_snapshot, standard_app, project_js, task_js)
        page.locator("#mj-quit-btn").click()
        page.wait_for_selector("#mj-quit-ov")
        assert page.get_by_role("button", name="继续使用").evaluate("el => el === document.activeElement")
        assert page.get_by_role("button", name="取消当前任务并退出").get_attribute("class") == "danger"
        exit_text = page.locator("#mj-quit-ov").inner_text()
        assert "请求取消不等于远端已经停止或停止计费" in exit_text
        assert "另有 1 个任务尚未运行" in exit_text
        page.screenshot(path=str(out / "safe-exit-dialog-desktop.png"), full_page=True)
        evidence["interactions"]["safe_exit"] = {
            "default_focus": "继续使用",
            "paid_cancel_tone": "danger",
            "mentions_remote_billing_uncertainty": True,
            "mentions_queued_jobs_never_start": True,
        }
        context.close()

        browser.close()

    (out / "task-center-facts.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
