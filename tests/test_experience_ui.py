"""The default focused UI, tested separately from legacy complete-page regressions."""
from pathlib import Path
import json
from html.parser import HTMLParser
import pytest
from playwright.sync_api import expect
from tests.test_rebuilt_r5_workbench import browser
from manju.authoring.desk import verify_desk

REPO=Path(__file__).resolve().parents[1]
PAGE=REPO/'tools/model_workbench.html'
VIEWS={'home':'studio-home','shot':'ux-shot','review':'review-section','director':'director-section','repair':'repair-section','exchange':'exchange-section','flex':'flex-section'}

@pytest.fixture
def page(browser):
    context=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':960})
    page=context.new_page();page.set_default_timeout(7000)
    page.set_content(PAGE.read_text(encoding='utf-8'),wait_until='load')
    yield page
    context.close()


def nav(page,view):
    page.locator(f'#ux-navigation [data-view="{view}"]').click()


def test_default_is_focused_and_original_controls_remain(page):
    expect(page.locator('#studio-home')).to_be_visible()
    expect(page.locator('#prompt')).not_to_be_visible()
    assert page.evaluate('ManjuExperience.state()')=={'view':'home','all':False,'large':False}
    original=json.loads((REPO/'tests/fixtures/experience_legacy_controls.json').read_text())
    actual=page.evaluate("() => [...document.querySelectorAll('input[id],textarea[id],select[id],button[id]')].map(e=>[e.id,e.tagName.toLowerCase()])")
    assert all(actual.count(entry)==1 for entry in original)
    assert page.locator('#quality-only').is_checked()


@pytest.mark.parametrize('width',[360,390,768,1440])
@pytest.mark.parametrize('view',list(VIEWS))
def test_each_default_view_is_reachable_without_overflow(page,width,view):
    page.set_viewport_size({'width':width,'height':900});nav(page,view)
    expect(page.locator('#'+VIEWS[view])).to_be_visible()
    assert page.locator(f'[data-view={view}]').get_attribute('aria-current')=='page'
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    expect(page.locator('#ux-save')).to_be_visible()
    for other,section in VIEWS.items():
        if other!=view:expect(page.locator('#'+section)).not_to_be_visible()


def test_switching_does_not_mutate_drafts_confirmations_or_undo(page):
    nav(page,'shot');page.locator('#prompt').fill('  中文草稿\n还有未写完的内容  ')
    page.locator('#reviewer').fill('个人代号');page.locator('#human-confirmed').check()
    page.locator('#ack-warnings').check()
    before=page.evaluate('ManjuDesk.fingerprint()')
    for view in VIEWS:nav(page,view)
    page.locator('#ux-text-size').click();page.locator('#ux-show-all').click()
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    assert page.locator('#human-confirmed').is_checked()
    assert page.locator('#ack-warnings').is_checked()
    assert page.locator('#prompt').input_value()=='  中文草稿\n还有未写完的内容  '


def test_complete_view_is_user_accessible_and_reversible(page):
    page.locator('#ux-show-all').click()
    for target in VIEWS.values():expect(page.locator('#'+target)).to_be_visible()
    expect(page.locator('#export-studio')).to_be_visible()
    expect(page.locator('#import-request')).to_be_attached()
    page.locator('#ux-show-all').click();expect(page.locator('#prompt')).not_to_be_visible()
    assert page.evaluate('ManjuExperience.state().all') is False


def test_keyword_search_navigates_without_running_actions(page):
    before=page.evaluate('ManjuDesk.fingerprint()')
    page.keyboard.press('Control+k');expect(page.locator('#ux-search-dialog')).to_be_visible()
    page.locator('#ux-search-input').fill('精修')
    page.get_by_role('button',name='带出关键帧精修').click()
    expect(page.locator('#retouch-section')).to_be_visible()
    assert page.locator('#retouch-section').get_attribute('open') is not None
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    assert page.evaluate('ManjuRetouch.state.task') is None


def test_search_no_match_esc_and_focus_return(page):
    page.locator('#ux-search-open').click();page.locator('#ux-search-input').fill('<img onerror=alert(1)>')
    expect(page.locator('#ux-search-count')).to_contain_text('没有匹配')
    assert page.locator('#ux-search-results button').count()==0
    page.keyboard.press('Escape');expect(page.locator('#ux-search-dialog')).not_to_be_visible()
    expect(page.locator('#ux-search-open')).to_be_focused()


def test_search_keyboard_and_mobile_all_mode(page):
    page.set_viewport_size({'width':390,'height':844})
    page.locator('#ux-search-open').click();page.locator('#ux-search-input').fill('完整长页')
    page.keyboard.press('ArrowDown');page.keyboard.press('Enter')
    assert page.evaluate('ManjuExperience.state().all') is True
    expect(page.locator('#prompt')).to_be_visible()


def test_ime_keyboard_does_not_save_or_open_dialog(page):
    page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,isComposing:true,bubbles:true}))")
    expect(page.locator('#ux-search-dialog')).not_to_be_visible()


def test_save_shortcut_uses_same_desk_protocol_and_truthful_status(page,tmp_path):
    nav(page,'shot');page.locator('#prompt').fill('写着写着，直接收工保存。')
    before=page.evaluate('ManjuDesk.fingerprint()')
    with page.expect_download() as download:page.keyboard.press('Control+s')
    output=tmp_path/'CHECKOUT.zip';download.value.save_as(output)
    assert verify_desk(output)['ok']
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    assert page.evaluate('ManjuDesk.state.verified') is None
    expect(page.locator('#ux-save-strip')).not_to_have_attribute('data-state','verified')
    page.locator('#ux-save-detail').click();page.locator('#desk-verify-file').set_input_files(output)
    expect(page.locator('#desk-save-status')).to_contain_text('已核验你选回')
    expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','verified')
    nav(page,'shot');page.locator('#prompt').fill('保存之后又写了新内容')
    expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','dirty')


def test_unified_intake_reveals_target_not_hidden_controls(page,tmp_path):
    nav(page,'shot');page.locator('#prompt').fill('用于导入的外部任务')
    request=page.evaluate('ManjuWorkbench.getRequest()');request['shot_id']='外来镜头02'
    path=tmp_path/'任务.json';path.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
    nav(page,'home');page.locator('#intake-files').set_input_files(path)
    expect(page.locator('#intake-preview')).to_be_visible()
    page.locator('#intake-confirmed').check();page.locator('#intake-open').click()
    expect(page.locator('#prompt')).to_be_visible()
    expect(page.locator('#shot-id')).to_have_value('外来镜头02')
    assert page.evaluate('ManjuExperience.state().view')=='shot'


def test_global_error_visible_from_focused_view(page):
    nav(page,'shot');page.locator('#check-plan').click()
    expect(page.locator('#status')).to_have_class('status error')
    expect(page.locator('#ux-notification')).to_be_visible()
    assert page.locator('#ux-notification p').inner_text()==page.locator('#status').inner_text()
    page.locator('#ux-notification button').click();expect(page.locator('#ux-notification')).not_to_be_visible()


def test_scroll_position_preserved_per_view(page):
    nav(page,'shot');page.evaluate('window.scrollTo(0,500)');before=page.evaluate('scrollY')
    nav(page,'director');nav(page,'shot');assert abs(page.evaluate('scrollY')-before)<3


def test_search_does_not_invalidate_unapplied_input(page):
    nav(page,'flex');page.locator('#flex-section details').filter(has=page.locator('#flex-template-name')).locator('summary').click();page.locator('#flex-template-name').fill('  未完成模板名称 ')
    before=page.evaluate('ManjuDesk.fingerprint()')
    page.keyboard.press('Control+k');page.locator('#ux-search-input').fill('这个还没完成');page.keyboard.press('Escape')
    assert page.evaluate('ManjuDesk.fingerprint()')==before


def test_reduced_motion(page):
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('#ux-save').evaluate('e=>getComputedStyle(e).transitionDuration')=='0s'


def test_no_network_on_navigation_and_safe_external_rendering(page):
    urls=[];errors=[];page.on('request',lambda r:urls.append(r.url));page.on('pageerror',lambda e:errors.append(str(e)))
    for view in VIEWS:nav(page,view)
    page.keyboard.press('Control+k');page.locator('#ux-search-input').fill('保存');page.keyboard.press('Escape')
    assert urls==[];assert errors==[]


def test_ui_assets_compiled_identically_and_no_fonts_or_remote_code():
    assert PAGE.read_bytes()==(REPO/'src/manju/authoring/data/workbench.html').read_bytes()
    text=PAGE.read_text(encoding='utf-8')
    assert '__EXPERIENCE_SCRIPT__' not in text and '<script src=' not in text
    assert 'EXPERIENCE_SHELL' not in text and "connect-src 'none'" in text
    assert 'fonts.googleapis' not in text and '@font-face' not in text
