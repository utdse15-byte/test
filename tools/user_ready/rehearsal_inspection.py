"""Exercise actual bound images, keyboard UX and downloadable backups.

Uses unmodified packaged HTML in an isolated Chromium document. It does not
certify native file:// navigation, Windows, commercial generation or colour.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
import json
from pathlib import Path
import shutil
import zipfile
from playwright.sync_api import sync_playwright, expect
import manju
from manju.authoring.desk import verify_desk


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def media(path):
    with zipfile.ZipFile(path) as desk, zipfile.ZipFile(BytesIO(desk.read('STUDIO.zip'))) as studio:
        return {n:sha256(studio.read(n)).hexdigest() for n in studio.namelist() if n.startswith('media/')}


def run(output: Path, example: Path, legacy_html: Path):
    output.mkdir(parents=True,exist_ok=False)
    html=files('manju.authoring').joinpath('data/workbench.html').read_text(encoding='utf-8')
    errors=[]; remote=[]; original_media=media(example); result={}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        context=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':960})
        def fresh(text=html):
            p=context.new_page();p.set_default_timeout(20000)
            p.on('pageerror',lambda e:errors.append(str(e)))
            p.on('request',lambda r:remote.append(r.url) if r.url.startswith(('http:','https:')) else None)
            p.set_content(text,wait_until='load');return p
        def restore(p,path):
            with p.expect_file_chooser() as chooser:p.locator('#ux-open').click()
            chooser.value.set_files(path)
            expect(p.locator('#intake-preview')).to_be_visible();p.locator('#intake-open').click()
            expect(p.locator('#desk-restore-preview')).to_be_visible()
            p.locator('#desk-restore-confirmed').check();p.locator('#desk-restore-apply').click()
            expect(p.locator('#desk-save-status')).to_contain_text('收工包已恢复')
        def nav(p,key):p.locator('#ux-navigation [data-view='+key+']').click()
        def quiet(p):
            if p.locator('#ux-notification').is_visible():p.locator('#ux-notification button').click()
        page=fresh();restore(page,example);before=page.evaluate('ManjuDesk.fingerprint()')
        quiet(page);page.screenshot(path=str(output/'home-desktop.png'))
        nav(page,'director');expect(page.locator('#director-video')).to_be_visible()
        page.evaluate('document.querySelector("#director-video").play()')
        page.wait_for_function('() => document.querySelector("#director-video").currentTime>0.15')
        target=page.get_by_role('button',name='查看 A01 精修目标图大图',exact=True)
        target.scroll_into_view_if_needed();quiet(page);page.screenshot(path=str(output/'director-desktop.png'))
        target.click();expect(page.locator('#ux-image-meta')).to_contain_text('1920 × 1080')
        assert page.locator('#director-video').evaluate('e=>e.paused')
        paused_at=page.locator('#director-video').evaluate('e=>e.currentTime')
        page.screenshot(path=str(output/'image-fit-desktop.png'))
        page.locator('#ux-image-actual').click()
        assert page.locator('#ux-image-original').evaluate('e=>e.getBoundingClientRect().width')==1920
        page.screenshot(path=str(output/'image-actual-desktop.png'))
        page.keyboard.press('ArrowLeft');expect(page.locator('#ux-image-title')).to_have_text('A01 · 原帧参考')
        page.keyboard.press('ArrowRight');expect(page.locator('#ux-image-meta')).to_contain_text('1920 × 1080')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#ux-image-fit').click();page.screenshot(path=str(output/'image-narrow.png'))
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.keyboard.press('Escape');expect(target).to_be_focused()
        assert page.locator('#director-video').evaluate('e=>e.paused')
        assert abs(page.locator('#director-video').evaluate('e=>e.currentTime')-paused_at)<.05
        assert page.evaluate('ManjuDesk.fingerprint()')==before
        # Search can enter a far-right mobile tab, which must now be visible.
        page.keyboard.press('Control+k');page.locator('#ux-search-input').fill('组合与模板');page.keyboard.press('Enter')
        active=page.locator('#ux-navigation [data-view=flex]').bounding_box()
        assert active['x']>=0 and active['x']+active['width']<=390
        page.screenshot(path=str(output/'mobile-active-tab.png'))
        # Explicit invalid action locates the field; corrective writing is ours.
        nav(page,'shot');page.locator('#prompt').fill('');page.locator('#check-plan').click()
        expect(page.locator('#prompt')).to_be_focused();expect(page.locator('#prompt')).to_have_attribute('aria-invalid','true')
        quiet(page);page.screenshot(path=str(output/'field-help-narrow.png'))
        page.locator('#prompt').fill('视觉实测：保留五份原素材，只调整这句镜头文字；精修目标图仍需人工审片。')
        expect(page.locator('#ux-error-prompt')).not_to_be_visible()
        ready=page.evaluate('ManjuDesk.fingerprint()');nav(page,'director')
        with page.expect_download() as dl:page.keyboard.press('Control+s')
        saved=output/'CHECKOUT_AFTER.zip';dl.value.save_as(saved)
        assert verify_desk(saved)['ok'];assert media(saved)==original_media
        page.locator('#ux-save-detail').click();page.locator('#desk-verify-file').set_input_files(saved)
        expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','verified')
        # Viewing does not mark a verified backup dirty.
        nav(page,'director');page.get_by_role('button',name='查看 A01 精修目标图大图',exact=True).click()
        expect(page.locator('#ux-image-meta')).to_contain_text('1920 × 1080')
        page.keyboard.press('Escape');expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','verified')
        assert page.evaluate('ManjuDesk.fingerprint()')==ready
        again=fresh();restore(again,saved);assert again.evaluate('ManjuDesk.fingerprint()')==ready
        nav(again,'director');again.get_by_role('button',name='查看 A01 精修目标图大图',exact=True).click()
        expect(again.locator('#ux-image-meta')).to_contain_text('1920 × 1080');again.keyboard.press('Escape')
        with again.expect_download() as dl:again.locator('#ux-save').click()
        reopened=output/'REOPENED.zip';dl.value.save_as(reopened);assert reopened.read_bytes()==saved.read_bytes()
        old=fresh(legacy_html.read_text(encoding='utf-8'));restore(old,saved)
        nav(old,'director');old.evaluate('document.querySelector("#director-video").play()')
        old.wait_for_function('() => document.querySelector("#director-video").currentTime>0.1')
        with old.expect_download() as dl:old.locator('#ux-save').click()
        legacy=output/'LEGACY_R16_6.zip';dl.value.save_as(legacy);assert legacy.read_bytes()==saved.read_bytes()
        context.close();browser.close()
    assert not errors,errors
    assert not remote,remote
    result.update({'ok':True,'version':manju.__version__,'runtime':str(Path(manju.__file__).resolve()),
        'html_sha256':sha256(html.encode()).hexdigest(),'input_sha256':digest(example),
        'checkout_sha256':digest(saved),'unchanged_media_files':len(original_media),'media_sha256':original_media,
        'actual_original_resolution': [1920,1080],'actual_pixel_view_checked':True,
        'modal_close_focus_and_scroll_restored':True,'view_does_not_dirty_verified_backup':True,
        'playing_video_paused_not_restarted':True,'mobile_selected_tab_visible':True,
        'field_error_focused_without_losing_other_inputs':True,'actual_browser_download':True,
        'fresh_reopen_identical':True,'legacy_r16_6_roundtrip_identical':True,
        'javascript_errors':errors,'network_requests':remote,'transport':'isolated_document',
        'native_windows_acceptance':False,'commercial_generation':False,'user_computer_download_confirmed':False})
    (output/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2));return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--example',type=Path,required=True);parser.add_argument('--legacy-html',type=Path,required=True)
    a=parser.parse_args();run(a.output.absolute(),a.example.absolute(),a.legacy_html.absolute())
