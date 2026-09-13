"""Actual file, playback, focused-navigation and backup rehearsal on unaltered HTML.

Runs isolated Chromium because native file navigation is blocked in this sandbox.
This is not native Windows acceptance or confirmation of the user's download.
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
from playwright.sync_api import sync_playwright,expect
import manju
from manju.authoring.desk import verify_desk


def digest(p):return sha256(p.read_bytes()).hexdigest()

def media(zip_path):
    with zipfile.ZipFile(zip_path) as desk,zipfile.ZipFile(BytesIO(desk.read('STUDIO.zip'))) as studio:
        return {n:sha256(studio.read(n)).hexdigest() for n in studio.namelist() if n.startswith('media/')}


def run(output,example,legacy_html):
    output.mkdir(parents=True,exist_ok=False)
    html=files('manju.authoring').joinpath('data/workbench.html').read_text(encoding='utf-8')
    errors=[];remote=[];baseline_media=media(example);result={}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        ctx=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1000})
        def fresh(text=html):
            p=ctx.new_page();p.on('pageerror',lambda e:errors.append(str(e)));p.on('request',lambda r:remote.append(r.url) if r.url.startswith(('https:','http:')) else None)
            p.set_default_timeout(20000);p.set_content(text,wait_until='load');return p
        def nav(p,key):p.locator('#ux-navigation [data-view='+key+']').click()
        def restore(p,path):
            if p.locator('#ux-open').count():
                with p.expect_file_chooser() as chooser:p.locator('#ux-open').click()
                chooser.value.set_files(path)
            else:p.locator('#intake-files').set_input_files(path)
            expect(p.locator('#intake-preview')).to_be_visible()
            p.locator('#intake-open').click();expect(p.locator('#desk-restore-preview')).to_be_visible()
            p.locator('#desk-restore-confirmed').check();p.locator('#desk-restore-apply').click()
            expect(p.locator('#desk-save-status')).to_contain_text('收工包已恢复')
        def quiet(p):
            if p.locator('#ux-notification').is_visible():p.locator('#ux-notification button').click()
        page=fresh();page.screenshot(path=str(output/'home-desktop.png'))
        result['empty_desktop_height']=page.evaluate('document.documentElement.scrollHeight')
        restore(page,example);original=page.evaluate('ManjuDesk.fingerprint()')
        nav(page,'director');expect(page.locator('#director-video')).to_be_visible()
        page.evaluate('()=>document.querySelector("#director-video").play()')
        page.wait_for_function('()=>document.querySelector("#director-video").currentTime>0.15')
        nav(page,'shot');assert page.locator('#director-video').evaluate('e=>e.paused')
        stopped=page.locator('#director-video').evaluate('e=>e.currentTime')
        nav(page,'director');assert page.locator('#director-video').evaluate('e=>e.paused')
        assert abs(page.locator('#director-video').evaluate('e=>e.currentTime')-stopped)<0.08
        quiet(page);page.screenshot(path=str(output/'director-desktop.png'))
        page.keyboard.press('Control+k');page.locator('#ux-search-input').fill('找回')
        page.get_by_role('button',name='找回改名 / 搬家的素材').click()
        expect(page.locator('#relink-panel')).to_be_visible()
        nav(page,'review');quiet(page);page.screenshot(path=str(output/'review-desktop.png'))
        nav(page,'shot');page.locator('#ux-text-size').click();page.locator('#ux-text-size').click()
        page.locator('#ux-show-all').click();page.locator('#ux-show-all').click()
        assert page.evaluate('ManjuDesk.fingerprint()')==original
        # Make an explicit new edit and save from the current task, without returning home.
        page.locator('#prompt').fill('体验验收：只改变这句镜头文字，原素材与待处理外部改稿完整保留。')
        before=page.evaluate('ManjuDesk.fingerprint()');quiet(page);page.screenshot(path=str(output/'shot-desktop.png'))
        nav(page,'director')
        with page.expect_download() as d:page.keyboard.press('Control+s')
        saved=output/'CHECKOUT_AFTER.zip';d.value.save_as(saved)
        assert verify_desk(saved)['ok'];assert media(saved)==baseline_media
        assert page.evaluate('ManjuDesk.fingerprint()')==before
        assert page.evaluate('ManjuDesk.needsSave()')
        page.locator('#ux-save-detail').click();page.locator('#desk-verify-file').set_input_files(saved)
        expect(page.locator('#desk-save-status')).to_contain_text('已核验你选回')
        expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','verified')
        page.set_viewport_size({'width':390,'height':844});nav(page,'home');quiet(page);page.screenshot(path=str(output/'home-narrow.png'))
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        nav(page,'director');quiet(page);page.screenshot(path=str(output/'director-narrow.png'))
        page.keyboard.press('Control+k');page.locator('#ux-search-input').fill('精修');page.screenshot(path=str(output/'search-narrow.png'));page.keyboard.press('Escape')
        freshpage=fresh();restore(freshpage,saved);assert freshpage.evaluate('ManjuDesk.fingerprint()')==before
        nav(freshpage,'director');freshpage.evaluate('()=>document.querySelector("#director-video").play()');freshpage.wait_for_function('()=>document.querySelector("#director-video").currentTime>0.1')
        with freshpage.expect_download() as d:freshpage.locator('#ux-save').click()
        new_saved=output/'REOPENED_CHECKOUT.zip';d.value.save_as(new_saved);assert new_saved.read_bytes()==saved.read_bytes()
        # Original r16.5 has no UX shell. Its unchanged protocol must still roundtrip.
        old=fresh(legacy_html.read_text(encoding='utf-8'));restore(old,saved)
        old.evaluate('()=>document.querySelector("#director-video").play()');old.wait_for_function('()=>document.querySelector("#director-video").currentTime>0.1')
        with old.expect_download() as d:old.locator('#desk-save').click()
        old_saved=output/'LEGACY_CHECKOUT.zip';d.value.save_as(old_saved);assert old_saved.read_bytes()==saved.read_bytes()
        ctx.close();browser.close()
    assert not errors,errors;assert not remote,remote
    result.update({'ok':True,'version':manju.__version__,'runtime':str(Path(manju.__file__).resolve()),
       'checkout_sha256':digest(saved),'input_sha256':digest(example),'media_files_unchanged':len(baseline_media),
       'media_sha256':baseline_media,'navigation_preserves_all_fields':True,'hidden_video_paused_without_seek':True,
       'return_to_video_never_autoplays':True,'actual_browser_file_chooser':True,'actual_browser_download':True,
       'saved_from_focused_view':True,'shortcut_save':True,'verified_only_after_file_reread':True,
       'fresh_page_roundtrip_identical':True,'legacy_r16_5_roundtrip_identical':True,'narrow_overflow':False,
       'javascript_errors':errors,'network_requests':remote,'transport':'isolated_document',
       'native_navigation':False,'windows_verified':False,'user_computer_download_confirmed':False})
    (output/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--example',type=Path,required=True);p.add_argument('--legacy-html',type=Path,required=True);a=p.parse_args();run(a.output.absolute(),a.example.absolute(),a.legacy_html.absolute())
