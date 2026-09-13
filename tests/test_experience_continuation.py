"""Focused-navigation behavior around the genuine optional local continuation service."""
from playwright.sync_api import expect
from tests.test_continuation_browser import live,package,browser,save_point


def nav(page,key):page.locator('#ux-navigation [data-view='+key+']').click()


def test_local_panel_is_home_only_and_navigation_writes_nothing(live,browser):
    ctx,page=live.page(browser,focused=True)
    try:
        expect(page.locator('#continue-panel')).to_be_visible()
        assert not live.root.exists()
        nav(page,'shot');expect(page.locator('#continue-panel')).not_to_be_visible()
        page.locator('#prompt').fill('本机续作和界面导航互不冒充备份。')
        before=page.evaluate('ManjuDesk.fingerprint()')
        page.keyboard.press('Control+k');page.locator('#ux-search-input').fill('本机续作')
        page.get_by_role('button',name='本机续作恢复点').click()
        expect(page.locator('#continue-panel')).to_be_visible()
        assert page.evaluate('ManjuDesk.fingerprint()')==before and not live.root.exists()
        save_point(page)
        assert page.evaluate('ManjuDesk.needsSave()')
        expect(page.locator('#ux-save-strip')).not_to_have_attribute('data-state','verified')
    finally:ctx.close()


def test_local_restore_reveals_home_and_remains_independent_backup_unverified(live,browser):
    ctx,page=live.page(browser,focused=True)
    try:
        nav(page,'shot');page.locator('#prompt').fill('必须恢复的原始文字')
        nav(page,'home');point=save_point(page)
        nav(page,'shot');page.locator('#prompt').fill('恢复前的新文字')
        nav(page,'home');page.locator('#continue-list').click();page.locator('#continue-choice').select_option(point['id'])
        page.locator('#continue-preview').click();expect(page.locator('#desk-restore-preview')).to_be_visible()
        page.locator('#desk-restore-confirmed').check();page.locator('#desk-restore-apply').click()
        expect(page.locator('#desk-save-status')).to_contain_text('没有标记独立备份')
        nav(page,'shot');expect(page.locator('#prompt')).to_have_value('必须恢复的原始文字')
        expect(page.locator('#ux-save-strip')).not_to_have_attribute('data-state','verified')
        assert page.evaluate('ManjuDesk.state.verified') is None
    finally:ctx.close()
