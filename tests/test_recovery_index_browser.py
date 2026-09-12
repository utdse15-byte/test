"""Real browser + unchanged same-origin protected loopback service, isolated transport."""
import json
from pathlib import Path

from playwright.sync_api import expect
from tests.test_continuation_browser import live, package, browser, save_point
from tests.test_local_continuation import contents


def scan(page):
    page.locator('#continue-list').click()
    page.locator('#continue-index').click()
    expect(page.locator('#continue-index-status')).to_contain_text('摘要读取完成',timeout=30000)


def test_no_automatic_index_or_directory_write(live,browser):
    ctx,page=live.page(browser)
    try:
        assert not live.root.exists()
        assert page.evaluate('ManjuContinuation.state.summaries.size')==0
        assert not page.locator('#continue-auto').is_checked()
        expect(page.locator('#continue-index-status')).to_contain_text('主动点击')
    finally:ctx.close()


def test_search_selected_summary_is_read_only_and_preserves_new_typing(live,browser):
    ctx,page=live.page(browser)
    try:
        page.locator('#shot-id').fill('镜头A_夜雨');page.locator('#prompt').fill('霓虹雨夜的背影');a=save_point(page)
        page.locator('#shot-id').fill('镜头B_山海');page.locator('#prompt').fill('青山与雾');b=save_point(page)
        page.locator('#prompt').fill('我正在写的新句子，不能恢复成旧的。')
        current=page.evaluate('ManjuDesk.fingerprint()');files=contents(live.root)
        assert page.evaluate('ManjuDesk.needsSave()')
        scan(page)
        assert page.evaluate('ManjuContinuation.state.summaries.size')==2
        page.locator('#continue-search').fill('霓虹')
        assert page.locator('#continue-choice').input_value()==a['id']
        expect(page.locator('#continue-summary')).to_contain_text('镜头A_夜雨')
        expect(page.locator('#continue-filter-status')).to_contain_text('显示 1 / 2')
        assert page.evaluate('ManjuDesk.fingerprint()')==current and contents(live.root)==files
        assert page.evaluate('ManjuDesk.needsSave()') and not page.locator('#continue-auto').is_checked()
        page.locator('#continue-search').fill('不存在')
        expect(page.locator('#continue-preview')).to_be_disabled()
        expect(page.locator('#continue-download')).to_be_disabled()
        page.locator('#continue-search').fill('')
        page.locator('#continue-choice').select_option(b['id'])
        expect(page.locator('#continue-summary')).to_contain_text('镜头B_山海')
    finally:ctx.close()


def test_titles_and_snippets_are_text_not_html(live,browser):
    ctx,page=live.page(browser,width=390)
    try:
        bad='<img src=x onerror="window.__oops=true">'
        page.locator('#shot-id').fill(bad+'中文'*100);page.locator('#prompt').fill(bad)
        save_point(page);scan(page)
        expect(page.locator('#continue-summary')).to_contain_text(bad)
        assert page.evaluate('window.__oops===undefined')
        assert page.locator('#continue-summary img').count()==0
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    finally:ctx.close()


def test_bad_point_does_not_hide_other_results_or_delete_bytes(live,browser):
    ctx,page=live.page(browser)
    try:
        page.locator('#prompt').fill('older');a=save_point(page)
        page.locator('#prompt').fill('newer');b=save_point(page)
        path=live.root/b['id']/'DESK.zip';path.write_bytes(path.read_bytes()[:-3]);before=contents(live.root)
        scan(page)
        expect(page.locator('#continue-index-status')).to_contain_text('1 份未能核验')
        assert page.evaluate('ManjuContinuation.state.summaries.size')==1
        page.locator('#continue-choice').select_option(b['id'])
        expect(page.locator('#continue-summary')).to_contain_text('未通过核验')
        page.locator('#continue-choice').select_option(a['id'])
        expect(page.locator('#continue-summary')).to_contain_text('older')
        assert contents(live.root)==before
    finally:ctx.close()


def test_reread_verifies_bytes_again_and_discards_old_cached_summary(live,browser):
    ctx,page=live.page(browser)
    try:
        page.locator('#prompt').fill('previously readable');a=save_point(page);scan(page)
        assert page.evaluate('ManjuContinuation.state.summaries.size')==1
        path=live.root/a['id']/'DESK.zip';path.write_bytes(path.read_bytes()[:-1]);scan(page)
        assert page.evaluate('ManjuContinuation.state.summaries.size')==0
        expect(page.locator('#continue-summary')).to_contain_text('未通过核验')
    finally:ctx.close()


def test_stop_finishes_current_read_without_requesting_next_or_restoring(live,browser):
    ctx,page=live.page(browser)
    try:
        for i in range(3):page.locator('#prompt').fill('草稿'+str(i));save_point(page)
        page.locator('#continue-list').click()
        page.evaluate('''()=>{const old=window.fetch;window.__summaryCalls=0;window.fetch=async(url,options)=>{
          if(String(url).endsWith('/summary')){window.__summaryCalls++;await new Promise(r=>setTimeout(r,500));}
          return old(url,options);
        };}''')
        page.locator('#continue-index').click()
        page.wait_for_function('()=>window.__summaryCalls===1')
        page.locator('#prompt').fill('读取期间的新内容')
        page.locator('#continue-index-stop').click()
        expect(page.locator('#continue-index-status')).to_contain_text('已停止读取后续摘要',timeout=15000)
        assert page.evaluate('window.__summaryCalls')==1
        assert page.locator('#prompt').input_value()=='读取期间的新内容'
        assert page.evaluate('ManjuDesk.needsSave()')
        expect(page.locator('#continue-save')).to_be_enabled()
    finally:ctx.close()


def test_changed_identity_response_not_cached_or_applied(live,browser):
    ctx,page=live.page(browser)
    try:
        page.locator('#prompt').fill('current safe draft');save_point(page)
        before=page.evaluate('ManjuDesk.fingerprint()')
        page.evaluate('''()=>{const old=window.fetch;window.fetch=async(url,options)=>{
          const r=await old(url,options);if(!String(url).endsWith('/summary'))return r;
          const d=await r.json();d.point.sha256='0'.repeat(64);return new Response(JSON.stringify(d),{status:200});
        };}''')
        scan(page)
        assert page.evaluate('ManjuContinuation.state.summaries.size')==0
        assert page.evaluate('ManjuDesk.fingerprint()')==before
    finally:ctx.close()


def test_search_branch_and_refresh_deletion_drops_stale_cache(live,browser):
    ctx,page=live.page(browser)
    try:
        page.locator('#prompt').fill('draft')
        page.evaluate('()=>{document.getElementById("flex-branch-name").value="B方案_冷光";}')
        a=save_point(page);scan(page)
        page.locator('#continue-search').fill('冷光')
        assert page.locator('#continue-choice').input_value()==a['id']
        live.server.store.remove(a['id'],a['sha256'])
        page.locator('#continue-list').click()
        expect(page.locator('#continue-filter-status')).to_contain_text('显示 0 / 0')
        assert page.evaluate('ManjuContinuation.state.summaries.size')==0
    finally:ctx.close()


def test_saved_points_reopen_in_wide_view_without_native_disclosure(live,browser):
    ctx,page=live.page(browser,width=1260)
    page.locator('#prompt').fill('重启后也应该看得到列表');save_point(page);ctx.close()
    live.close();live.start()
    ctx,page=live.page(browser,width=1260)
    try:
        page.locator('#continue-list').click()
        expect(page.locator('#continue-points')).to_be_visible()
        expect(page.locator('#continue-index')).to_be_visible()
        expect(page.locator('#continue-list')).to_have_attribute('aria-expanded','true')
        page.locator('#continue-hide').click()
        expect(page.locator('#continue-points')).to_be_hidden()
        expect(page.locator('#continue-list')).to_be_focused()
        page.locator('#continue-list').press('Enter')
        expect(page.locator('#continue-index')).to_be_visible()
        page.locator('#continue-index').click()
        expect(page.locator('#continue-index-status')).to_contain_text('摘要读取完成')
    finally:ctx.close()


def test_typing_observed_during_index_read_never_keeps_old_saved_message(live,browser):
    ctx,page=live.page(browser)
    try:
        page.locator('#prompt').fill('先存一份');save_point(page)
        page.locator('#continue-list').click()
        page.evaluate('''()=>{const old=window.fetch;window.fetch=async(url,options)=>{
          if(String(url).endsWith('/summary'))await new Promise(r=>window.__releaseSummary=r);
          return old(url,options);
        };}''')
        page.locator('#continue-index').click()
        page.wait_for_function('()=>typeof window.__releaseSummary==="function"')
        page.locator('#prompt').fill('读取摘要期间的新文字还没有保存')
        page.wait_for_function('()=>ManjuContinuation.state.observed===ManjuDesk.fingerprint()')
        page.evaluate('()=>window.__releaseSummary()')
        expect(page.locator('#continue-index-status')).to_contain_text('摘要读取完成')
        expect(page.locator('#continue-status')).to_contain_text('新修改尚未存入恢复点')
        assert page.evaluate('ManjuDesk.needsSave()')
    finally:ctx.close()
