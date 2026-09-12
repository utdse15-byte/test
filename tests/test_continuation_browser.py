"""Isolated Chromium document + genuine loopback HTTP (not native navigation).

The sandbox rejects native file:// and localhost navigation. A test-only fetch
adapter transports requests into the unchanged service with its same-origin
headers; server origin defenses are independently exercised in HTTP tests.
"""
import base64
import hashlib
import http.client
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import expect
from tests.test_rebuilt_r5_workbench import browser
from tests.test_local_continuation import package, M
from tests.test_rebuilt_r16_user_desk import write_desk, load_buffers
from manju.authoring.desk import verify_desk

ROOT=Path(__file__).resolve().parents[1]
BRIDGE="""<script>
window.fetch=async function(url,options={}){
 const body=options.body?new Uint8Array(await new Blob([options.body]).arrayBuffer()):null;
 let encoded=null;if(body){let chunks=[];for(let i=0;i<body.length;i+=16384)chunks.push(String.fromCharCode(...body.subarray(i,i+16384)));encoded=btoa(chunks.join(''));}
 const answer=await window.__local_http_test({path:String(url),method:options.method||'GET',headers:options.headers||{},body:encoded});
 return new Response(Uint8Array.from(atob(answer.body),c=>c.charCodeAt(0)),{status:answer.status,headers:answer.headers});
};</script>"""


class Live:
    def __init__(self, package, root):
        self.package,self.root=package,root;self.start()
    def start(self):
        self.server,self.url=M.create_server(self.package,self.root)
        self.parsed=urlsplit(self.url)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def call(self,request):
        assert request['path'].startswith(self.parsed.path)
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=20)
        headers={**request['headers'],'Origin':'http://'+self.parsed.netloc}
        body=base64.b64decode(request['body']) if request['body'] is not None else None
        conn.request(request['method'],request['path'],body=body,headers=headers)
        r=conn.getresponse();answer={'status':r.status,'headers':dict(r.getheaders()),'body':base64.b64encode(r.read()).decode()}
        conn.close();return answer
    def page(self,browser,*,width=1100):
        ctx=browser.new_context(accept_downloads=True,viewport={'width':width,'height':900})
        p=ctx.new_page();p.expose_function('__local_http_test',self.call)
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        conn.request('GET',self.parsed.path);r=conn.getresponse();assert r.status==200;html=r.read().decode();conn.close()
        html=html.replace('<head>','<head>'+BRIDGE,1)
        p.set_content(html,wait_until='load');p.wait_for_function('!!window.ManjuContinuation')
        return ctx,p


@pytest.fixture
def live(package,tmp_path):
    result=Live(package,tmp_path/'browser-recovery')
    yield result
    result.close()


def fill_prompt(page,text):
    page.locator('#prompt').fill(text)


def save_point(page):
    page.locator('#continue-save').click()
    expect(page.locator('#continue-status')).to_contain_text('已写入并核对本机恢复点',timeout=30000)
    return page.evaluate('ManjuContinuation.state.lastPoint')


def test_no_initial_write_and_manual_save_keeps_backup_reminder(live,browser):
    ctx,page=live.page(browser)
    try:
        assert not page.locator('#continue-auto').is_checked()
        assert not live.root.exists()
        fill_prompt(page,'  当前镜头草稿\n不准备批准  ')
        assert page.evaluate('ManjuDesk.needsSave()')
        point=save_point(page)
        assert page.evaluate('ManjuDesk.needsSave()')
        assert verify_desk(live.root/point['id']/'DESK.zip')['ok']
        assert not page.locator('#human-confirmed').is_checked()
    finally:ctx.close()


def test_restart_preview_apply_does_not_claim_independent_backup(live,browser,tmp_path):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'必须跨重启保留的最新文字')
        pending,template=load_buffers(page)
        point=save_point(page)
    finally:ctx.close()
    live.close();live.start()
    ctx,page=live.page(browser,width=390)
    try:
        assert page.locator('#prompt').input_value()!='必须跨重启保留的最新文字'
        page.locator('#continue-list').click()
        page.locator('#continue-choice').select_option(point['id'])
        page.locator('#continue-preview').click()
        expect(page.locator('#desk-restore-preview')).to_be_visible(timeout=30000)
        assert page.locator('#prompt').input_value()!='必须跨重启保留的最新文字'
        page.locator('#desk-restore-confirmed').check();page.locator('#desk-restore-apply').click()
        expect(page.locator('#prompt')).to_have_value('必须跨重启保留的最新文字')
        expect(page.locator('#desk-save-status')).to_contain_text('没有标记独立备份')
        assert page.evaluate('ManjuDesk.needsSave()')
        assert page.evaluate('ManjuExchange.state.edit')==pending
        assert page.evaluate('ManjuFlex.state.template')==template
        assert page.locator('#quality-only').is_checked()
        assert not page.locator('#human-confirmed').is_checked()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.locator('#continue-panel').scroll_into_view_if_needed()
        # Actual browser download of the stored bytes, not a fabricated link.
        with page.expect_download() as download:page.locator('#continue-download').click()
        saved=tmp_path/'actual-download.zip';download.value.save_as(saved)
        assert saved.read_bytes()==(live.root/point['id']/'DESK.zip').read_bytes()
        assert verify_desk(saved)['ok']
    finally:ctx.close()


def test_changed_work_invalidates_restoration_preview(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'旧版');point=save_point(page)
        fill_prompt(page,'新的当前稿')
        page.locator('#continue-list').click();page.locator('#continue-choice').select_option(point['id'])
        page.locator('#continue-preview').click();expect(page.locator('#desk-restore-preview')).to_be_visible()
        fill_prompt(page,'预览后继续写，不得覆盖')
        expect(page.locator('#desk-restore-apply')).to_be_disabled()
        assert page.locator('#prompt').input_value()=='预览后继续写，不得覆盖'
    finally:ctx.close()


def test_two_windows_never_share_writer_identity(live,browser):
    c1,p1=live.page(browser);c2,p2=live.page(browser)
    try:
        assert p1.evaluate('ManjuContinuation.windowId')!=p2.evaluate('ManjuContinuation.windowId')
        fill_prompt(p1,'窗口 A');a=save_point(p1)
        fill_prompt(p2,'窗口 B');b=save_point(p2)
        assert a['id']!=b['id']
        for i in range(4):
            fill_prompt(p1,'窗口 A 第'+str(i)+'次');save_point(p1)
        ids={p['id'] for p in live.server.store.list()['points']}
        assert b['id'] in ids and a['id'] not in ids
        assert p2.locator('#prompt').input_value()=='窗口 B'
    finally:c1.close();c2.close()


def test_network_failure_pauses_autosave_and_keeps_current_work(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'先保存');point=save_point(page)
        before=(live.root/point['id']/'DESK.zip').read_bytes()
        page.evaluate("() => { window.fetch=async()=>{throw Error('connection stopped')}; }")
        fill_prompt(page,'网络失败之后的新文字')
        page.locator('#continue-auto').check();page.locator('#continue-save').click()
        expect(page.locator('#continue-status')).to_contain_text('续作未完成')
        assert not page.locator('#continue-auto').is_checked()
        assert page.locator('#prompt').input_value()=='网络失败之后的新文字'
        assert (live.root/point['id']/'DESK.zip').read_bytes()==before
    finally:ctx.close()


def test_autosave_is_opt_in_and_runs_while_page_alive(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'启用后自动保存，不依赖关闭事件')
        assert not live.root.exists()
        page.locator('#continue-auto').check()
        expect(page.locator('#continue-status')).to_contain_text('已写入并核对本机恢复点',timeout=18000)
        assert len(live.server.store.list()['points'])==1
        assert page.evaluate('ManjuDesk.needsSave()')
    finally:ctx.close()


def test_typing_while_save_response_pending_is_not_marked_saved(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'请求开始时的旧稿')
        page.evaluate('''()=>{const original=window.fetch;window.fetch=async(...args)=>{
          const response=await original(...args);if(args[1]?.method==='POST'){
          window.saveResponseArrived=true;await new Promise(resolve=>window.releaseSaveResponse=resolve);}return response;};}''')
        page.locator('#continue-save').click();page.wait_for_function('() => window.saveResponseArrived === true')
        fill_prompt(page,'保存响应回来前继续写的新稿')
        page.evaluate('window.releaseSaveResponse()')
        expect(page.locator('#continue-status')).to_contain_text('新内容尚未保存')
        assert page.locator('#prompt').input_value()=='保存响应回来前继续写的新稿'
        assert page.evaluate('ManjuContinuation.state.last!==ManjuDesk.fingerprint()')
        assert page.evaluate('ManjuDesk.needsSave()')
    finally:ctx.close()


def test_deleting_this_windows_latest_point_pauses_auto(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'不要在删除后马上自动写回来')
        point=save_point(page)
        page.locator('#continue-auto').check();page.locator('#continue-list').click()
        page.locator('#continue-choice').select_option(point['id'])
        page.once('dialog',lambda d:d.accept())
        page.locator('#continue-delete').click()
        expect(page.locator('#continue-status')).to_contain_text('自动续作已暂停')
        assert not page.locator('#continue-auto').is_checked()
        assert not live.server.store.list()['points']
        assert page.locator('#prompt').input_value()=='不要在删除后马上自动写回来'
    finally:ctx.close()


def test_observation_after_success_does_not_invent_unsaved_changes(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'一次快速手动保存的原稿')
        save_point(page)
        # Real periodic observation, not a rerun of the saving routine.
        page.wait_for_timeout(1200)
        expect(page.locator('#continue-status')).to_contain_text('已写入并核对本机恢复点')
        assert page.evaluate('ManjuContinuation.state.last===ManjuDesk.fingerprint()')
        assert page.evaluate('ManjuDesk.needsSave()')
    finally:ctx.close()


def test_page_lifecycle_restarts_observer_without_writing(live,browser):
    ctx,page=live.page(browser)
    try:
        assert page.evaluate('ManjuContinuation.isWatching()')
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}))")
        assert not page.evaluate('ManjuContinuation.isWatching()')
        page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
        assert page.evaluate('ManjuContinuation.isWatching()')
        assert not live.root.exists()
    finally:ctx.close()


def test_revoked_auto_consent_before_upload_never_writes(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'撤销自动保存，不应再新建恢复点')
        page.evaluate('''()=>{const build=ManjuDesk.build;ManjuDesk.build=async()=>{
          const result=await build();window.buildHeld=true;await new Promise(r=>window.releaseBuild=r);return result;};}''')
        page.locator('#continue-auto').check()
        page.evaluate('()=>{window.attempt=ManjuContinuation.save({automatic:true});}')
        page.wait_for_function('() => window.buildHeld === true')
        page.locator('#continue-auto').uncheck()
        page.evaluate('window.releaseBuild()')
        expect(page.locator('#continue-status')).to_contain_text('本次尚未上传的现场没有写入')
        assert not live.root.exists()
        assert page.locator('#prompt').input_value()=='撤销自动保存，不应再新建恢复点'
    finally:ctx.close()
