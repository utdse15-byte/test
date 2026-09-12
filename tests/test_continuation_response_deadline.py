"""A request includes the entire body. No perpetual busy/success after timeout."""
from playwright.sync_api import expect
from tests.test_continuation_browser import live, browser, package, fill_prompt, save_point


def test_stalled_json_after_headers_releases_busy_without_false_success(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'正文卡住仍然保留的新稿')
        page.evaluate('''()=>{MANJU_CONTINUE_CONFIG.request_timeout_ms=80;const original=window.fetch;
        window.fetch=async(url,options)=>{if(options?.method==='POST')return {ok:true,json:()=>new Promise(r=>window.releaseStalledJSON=r)};return original(url,options);};}''')
        page.locator('#continue-auto').check();page.locator('#continue-save').click()
        expect(page.locator('#continue-status')).to_contain_text('超时',timeout=5000)
        assert not page.evaluate('ManjuContinuation.state.busy')
        assert not page.evaluate('ManjuDesk.state.busy')
        assert page.evaluate('ManjuContinuation.state.last') is None
        assert not page.locator('#continue-auto').is_checked()
        assert not live.root.exists()
        page.evaluate("window.releaseStalledJSON({point:{sha256:'f'.repeat(64),bytes:123}})")
        assert page.evaluate('ManjuContinuation.state.last') is None
        assert page.locator('#prompt').input_value()=='正文卡住仍然保留的新稿'
    finally:ctx.close()


def test_stalled_blob_download_has_deadline_preserves_snapshot(live,browser):
    ctx,page=live.page(browser)
    try:
        fill_prompt(page,'已保存的稿件');point=save_point(page)
        page.locator('#continue-list').click();page.locator('#continue-choice').select_option(point['id'])
        page.evaluate('''()=>{MANJU_CONTINUE_CONFIG.request_timeout_ms=80;const original=window.fetch;
        window.fetch=async(url,options)=>{if(String(url).includes('points/'))return {ok:true,blob:()=>new Promise(()=>{})};return original(url,options);};}''')
        page.locator('#continue-preview').click()
        expect(page.locator('#continue-status')).to_contain_text('超时',timeout=5000)
        assert not page.evaluate('ManjuContinuation.state.busy')
        assert page.evaluate('ManjuDesk.state.pending') is None
        assert page.locator('#prompt').input_value()=='已保存的稿件'
        assert live.server.store.list()['points'][0]['id']==point['id']
    finally:ctx.close()
