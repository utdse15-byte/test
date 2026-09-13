"""Real Chromium tests for inspection, actionable errors and narrow navigation.

The exact standalone document is executed in isolation; no native file navigation
or Windows desktop acceptance is implied.
"""
from pathlib import Path
import json
import pytest
from playwright.sync_api import expect
from tests.test_experience_ui import page, nav
from tests.test_rebuilt_r5_workbench import browser


def test_failed_plan_identifies_prompt_without_erasing_draft(page):
    nav(page, 'shot')
    page.locator('#shot-id').fill('镜头保留')
    before = page.evaluate('ManjuDesk.fingerprint()')
    page.locator('#check-plan').click()
    expect(page.locator('#prompt')).to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#prompt')).to_be_focused()
    expect(page.locator('#ux-error-prompt')).to_contain_text('画面')
    assert page.evaluate('ManjuDesk.fingerprint()') == before
    page.locator('#prompt').fill('雨停后，一辆电车缓缓驶过。')
    expect(page.locator('#prompt')).not_to_have_attribute('aria-invalid', 'true')
    expect(page.locator('#ux-error-prompt')).not_to_be_visible()


def test_viewer_uses_actual_pixels_and_preserves_work(page):
    nav(page, 'director')
    # Actual decoded PNG generated locally; native media cards are used by the
    # integration rehearsal. This fixture isolates the presentation behaviour.
    page.evaluate('''() => {
      const root=document.getElementById('director-anchors');root.replaceChildren();
      const card=document.createElement('div');card.dataset.anchor='A01';
      for(const [w,h,title] of [[640,360,'原帧参考'],[1920,1080,'精修目标图']]) {
        const canvas=document.createElement('canvas');canvas.width=w;canvas.height=h;
        const c=canvas.getContext('2d');c.fillStyle='#657266';c.fillRect(0,0,w,h);
        c.fillStyle='#fff';c.fillText(title,20,30);
        const image=new Image();image.alt=title;image.src=canvas.toDataURL('image/png');card.append(image);
      }root.append(card);
    }''')
    before = page.evaluate('ManjuDesk.fingerprint()')
    page.get_by_role('button', name='查看 A01 精修目标图大图', exact=True).click()
    expect(page.locator('#ux-image-dialog')).to_be_visible()
    expect(page.locator('#ux-image-meta')).to_contain_text('1920 × 1080')
    page.locator('#ux-image-actual').click()
    assert page.locator('#ux-image-original').evaluate('e=>e.getBoundingClientRect().width') == 1920
    page.keyboard.press('ArrowLeft')
    expect(page.locator('#ux-image-meta')).to_contain_text('640 × 360')
    page.keyboard.press('Escape')
    expect(page.locator('#ux-image-dialog')).not_to_be_visible()
    assert page.evaluate('ManjuDesk.fingerprint()') == before


def test_mobile_navigation_follows_search_without_scrolling_page_twice(page):
    page.set_viewport_size({'width':390,'height':844})
    page.keyboard.press('Control+k');page.locator('#ux-search-input').fill('组合与模板')
    page.keyboard.press('Enter')
    link=page.locator('#ux-navigation [data-view="flex"]')
    expect(link).to_have_attribute('aria-current','page')
    box=link.bounding_box()
    assert box['x'] >= 0 and box['x']+box['width'] <= 390

@pytest.fixture
def gallery(page):
    nav(page, 'director')
    page.evaluate('''() => {
      const root=document.getElementById('director-anchors');root.replaceChildren();
      for(const id of ['A01','A02']) {
       const card=document.createElement('div');card.dataset.anchor=id;
       for(const [w,h,title] of [[640,360,'原帧参考'],[1920,1080,'精修目标图']]) {
        const c=document.createElement('canvas');c.width=w;c.height=h;
        const g=c.getContext('2d');g.fillStyle=id==='A01'?'#7a8270':'#76878d';g.fillRect(0,0,w,h);
        g.fillStyle='#fff';g.font='28px sans-serif';g.fillText(title,20,50);
        const img=new Image();img.alt=title;img.src=c.toDataURL('image/png');card.append(img);
       }root.append(card);
      }
    }''')
    expect(page.locator('.ux-image-trigger')).to_have_count(4)
    return page


@pytest.mark.parametrize('width,height',[(320,640),(360,740),(390,844),(768,1024),(1440,960),(844,390)])
def test_image_dialog_fits_viewports_and_keyboard_focus(gallery,width,height):
    page=gallery;page.set_viewport_size({'width':width,'height':height})
    opener=page.get_by_role('button',name='查看 A01 精修目标图大图',exact=True)
    opener.click()
    expect(page.locator('#ux-image-meta')).to_contain_text('1920 × 1080')
    rect=page.locator('#ux-image-dialog').bounding_box()
    assert rect['x']>=0 and rect['y']>=0
    assert rect['x']+rect['width']<=width+1 and rect['y']+rect['height']<=height+1
    assert page.locator('#ux-image-stage').evaluate('e=>e.clientHeight')>70
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    # Modal keeps tab focus inside; return does not change the original form.
    for _ in range(16):page.keyboard.press('Tab')
    assert page.evaluate('document.querySelector("#ux-image-dialog").contains(document.activeElement)')
    page.keyboard.press('Escape');expect(opener).to_be_focused()
    assert page.evaluate('document.body.style.overflow')==''


def test_zoom_drag_switch_and_original_src_unchanged(gallery):
    page=gallery
    sources=page.locator('#director-anchors img').evaluate_all('xs=>xs.map(x=>x.src)')
    page.get_by_role('button',name='查看 A01 精修目标图大图',exact=True).click()
    expect(page.locator('#ux-image-meta')).to_contain_text('1920')
    page.locator('#ux-image-actual').click()
    expect(page.locator('#ux-image-zoom')).to_have_text('100%')
    b=page.locator('#ux-image-stage').bounding_box()
    before=page.locator('#ux-image-stage').evaluate('e=>e.scrollLeft')
    page.mouse.move(b['x']+b['width']/2,b['y']+b['height']/2)
    page.mouse.down();page.mouse.move(b['x']+b['width']/2-100,b['y']+b['height']/2);page.mouse.up()
    assert page.locator('#ux-image-stage').evaluate('e=>e.scrollLeft')>before+80
    page.locator('#ux-image-plus').click()
    expect(page.locator('#ux-image-zoom')).to_have_text('125%')
    page.keyboard.press('ArrowRight')
    expect(page.locator('#ux-image-title')).to_have_text('A02 · 原帧参考')
    expect(page.locator('#ux-image-fit')).to_have_attribute('aria-pressed','true')
    page.keyboard.press('Escape')
    assert page.locator('#director-anchors img').evaluate_all('xs=>xs.map(x=>x.src)')==sources


def test_modal_save_shortcut_does_not_export_or_approve(gallery):
    page=gallery;downloads=[];page.on('download',lambda d:downloads.append(d))
    before=page.evaluate('ManjuDesk.fingerprint()')
    page.get_by_role('button',name='查看 A01 原帧参考大图',exact=True).click()
    page.keyboard.press('Control+s');page.keyboard.press('Control+k')
    expect(page.locator('#ux-search-dialog')).not_to_be_visible()
    page.keyboard.press('Escape')
    assert not downloads
    assert page.evaluate('ManjuDesk.fingerprint()')==before


def test_gallery_closes_when_original_workspace_is_replaced(gallery):
    page=gallery;page.get_by_role('button',name='查看 A01 原帧参考大图',exact=True).click()
    page.evaluate("document.querySelector('#director-anchors').replaceChildren()")
    expect(page.locator('#ux-image-dialog')).not_to_be_visible()
    assert page.locator('#ux-image-original').get_attribute('src') is None
    expect(page.locator('#ux-heading')).to_be_focused()


def test_untrusted_image_label_is_literal_not_html(gallery):
    page=gallery
    page.evaluate('''() => {let x=document.querySelector('#director-anchors img');x.alt='<img src=x onerror=alert(1)>';x.closest('.ux-image-trigger').click();}''')
    expect(page.locator('#ux-image-title')).to_contain_text('<img src=x onerror=alert(1)>')
    assert page.locator('#ux-image-title img').count()==0


@pytest.mark.parametrize('field,value,expected',[
    ('shot-id','','编号'),('duration','0','整数秒'),('duration','1.5','整数秒'),('duration','121','整数秒')])
def test_actionable_validation_uses_existing_limits(page,field,value,expected):
    nav(page,'shot');page.locator('#prompt').fill('雨夜的灯光。')
    page.locator('#'+field).fill(value)
    before=page.evaluate('ManjuDesk.fingerprint()')
    page.locator('#check-plan').click()
    expect(page.locator('#'+field)).to_have_attribute('aria-invalid','true')
    expect(page.locator('#ux-error-'+field)).to_contain_text(expected)
    assert page.evaluate('ManjuDesk.fingerprint()')==before


def test_bad_external_request_does_not_mark_current_form(page):
    nav(page,'shot');page.locator('#prompt').fill('当前有效的文字')
    outcome=page.evaluate('''() => {try{ManjuWorkbench.normalizeRequest({shot_id:'bad',task:'create',prompt:''});return false;}catch(e){return true;}}''')
    assert outcome
    assert page.locator('#prompt').get_attribute('aria-invalid') is None


def test_late_validation_does_not_steal_new_input_focus(page):
    nav(page,'shot');page.locator('#shot-id').focus()
    page.evaluate("window.dispatchEvent(new CustomEvent('manju:request-error',{detail:{inputId:'prompt',triggerId:'check-plan'}}))")
    expect(page.locator('#shot-id')).to_be_focused()
    expect(page.locator('#prompt')).to_have_attribute('aria-invalid','true')


def test_describedby_is_preserved_after_fix(page):
    nav(page,'shot');page.locator('#prompt').evaluate("e=>e.setAttribute('aria-describedby','storage-note')")
    page.locator('#check-plan').click()
    assert 'storage-note' in page.locator('#prompt').get_attribute('aria-describedby')
    page.locator('#prompt').fill('已经填写画面')
    expect(page.locator('#prompt')).to_have_attribute('aria-describedby','storage-note')


def test_repeated_error_reopens_dismissed_notification(page):
    nav(page,'shot');page.locator('#check-plan').click()
    page.locator('#ux-notification button').click()
    page.locator('#check-plan').click()
    expect(page.locator('#ux-notification')).to_be_visible()


def test_review_has_correct_link_to_other_workspace(page):
    nav(page,'review')
    page.get_by_role('link',name='镜头与素材填写草稿').click()
    expect(page.locator('#prompt')).to_be_focused()
    assert page.evaluate('ManjuExperience.state().view')=='shot'


def test_inspection_is_reproducibly_embedded():
    root=Path(__file__).resolve().parents[1]
    data=(root/'tools/model_workbench.html').read_bytes()
    assert data==(root/'src/manju/authoring/data/workbench.html').read_bytes()
    text=data.decode('utf-8')
    assert 'id="ux-image-dialog"' in text
    assert '<script src=' not in text and '@font-face' not in text


def test_close_reopen_in_same_task_does_not_destroy_new_viewer(gallery):
    page=gallery
    page.get_by_role('button',name='查看 A01 原帧参考大图',exact=True).click()
    page.evaluate('''() => {
      document.querySelector('#ux-image-close').click();
      if(document.body.style.overflow!=='')throw new Error('scroll lock was not released synchronously');
      document.querySelectorAll('.ux-image-trigger')[1].click();
    }''')
    expect(page.locator('#ux-image-meta')).to_contain_text('1920 × 1080')
    expect(page.locator('#ux-image-dialog')).to_be_visible()
    assert page.evaluate('document.body.style.overflow')=='hidden'
    page.keyboard.press('Escape')
    assert page.evaluate('document.body.style.overflow')==''


def test_inline_error_label_is_not_under_mobile_navigation(page):
    page.set_viewport_size({'width':390,'height':844});nav(page,'shot')
    page.locator('#check-plan').click()
    label=page.locator('label.field').filter(has=page.locator('#prompt')).bounding_box()
    navigation=page.locator('#ux-sidebar').bounding_box()
    assert label['y']>=navigation['y']+navigation['height']


def test_restored_text_clears_stale_field_decoration(page):
    nav(page,'shot');page.locator('#check-plan').click()
    expect(page.locator('#prompt')).to_have_attribute('aria-invalid','true')
    page.evaluate('''() => {document.getElementById('prompt').value='恢复后的有效文字';ManjuWorkbench.status('已恢复新任务');}''')
    expect(page.locator('#prompt')).not_to_have_attribute('aria-invalid','true')


def test_typing_after_backup_uses_strip_without_popup(page,tmp_path):
    nav(page,'shot');page.locator('#prompt').fill('准备备份')
    with page.expect_download() as dl:page.locator('#ux-save').click()
    saved=tmp_path/'CHECKOUT.zip';dl.value.save_as(saved)
    page.locator('#ux-save-detail').click();page.locator('#desk-verify-file').set_input_files(saved)
    expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','verified')
    if page.locator('#ux-notification').is_visible():page.locator('#ux-notification button').click()
    nav(page,'shot');page.locator('#prompt').fill('又写了新句子')
    expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','dirty')
    expect(page.locator('#ux-notification')).not_to_be_visible()


def test_dirty_indicator_updates_before_next_paint_not_after_idle(page,tmp_path):
    nav(page,'shot');page.locator('#prompt').fill('已保存的句子')
    with page.expect_download() as dl:page.locator('#ux-save').click()
    saved=tmp_path/'CHECKOUT.zip';dl.value.save_as(saved)
    page.locator('#ux-save-detail').click();page.locator('#desk-verify-file').set_input_files(saved)
    expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','verified')
    nav(page,'shot')
    state=page.evaluate('''async () => {
      const field=document.getElementById('prompt');field.value+='新文字';
      field.dispatchEvent(new Event('input',{bubbles:true}));
      await Promise.resolve();return document.getElementById('ux-save-strip').dataset.state;
    }''')
    assert state=='dirty'
