"""Actual saved-file/CLI/browser roundtrip. No real commercial IDE or model call.

Original shipped HTML in isolated Chromium, not native file/Windows navigation.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from playwright.sync_api import sync_playwright, expect
import manju
from manju.authoring.desk import verify_desk


def sha(path):return sha256(path.read_bytes()).hexdigest()
def media(path):
    with zipfile.ZipFile(path) as outer,zipfile.ZipFile(BytesIO(outer.read('STUDIO.zip'))) as z:
        return {n:sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('media/')}

def run(output:Path,example:Path,legacy_html:Path):
    output.mkdir(parents=True,exist_ok=False)
    html=files('manju.authoring').joinpath('data/workbench.html').read_text(encoding='utf-8')
    original=media(example);errors=[];requests=[];commands=[]
    def cli(*args):
        command=[sys.executable,'-m','manju','models',*map(str,args)]
        r=subprocess.run(command,cwd=output,capture_output=True,text=True,encoding='utf-8',timeout=60)
        commands.append({'args':list(map(str,args)),'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        assert r.returncode==0,r.stdout+r.stderr
        return json.loads(r.stdout)
    with sync_playwright() as pw:
        b=pw.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        c=b.new_context(accept_downloads=True,viewport={'width':1440,'height':1050})
        def fresh(text=html):
            p=c.new_page();p.set_default_timeout(20000);p.on('pageerror',lambda e:errors.append(str(e)))
            p.on('request',lambda r:requests.append(r.url) if r.url.startswith(('http:','https:')) else None)
            p.set_content(text,wait_until='load');return p
        def restore(p,path):
            p.locator('#intake-files').set_input_files(path);expect(p.locator('#intake-preview')).to_be_visible()
            p.locator('#intake-open').click();p.locator('#desk-restore-confirmed').check()
            p.locator('#desk-restore-apply').click();expect(p.locator('#desk-save-status')).to_contain_text('收工包已恢复')
        def nav(p,name):p.locator('#ux-navigation [data-view='+name+']').click()
        def save(p,path):
            with p.expect_download() as dl:p.locator('#ux-save').click()
            dl.value.save_as(path);verify_desk(path)
        def quiet(p):
            if p.locator('#ux-notification').is_visible():p.locator('#ux-notification button').click()
        p=fresh();restore(p,example);nav(p,'story')
        expect(p.locator('#story-action')).to_be_visible();expect(p.locator('#prompt')).not_to_be_visible()
        p.locator('#story-action').fill('林岚把箱子稳稳放在桌上，抬眼观察周泊。')
        data=p.evaluate('ManjuStory.view()');finger=p.evaluate('ManjuDesk.fingerprint()')
        p.locator('#scene-step-refs').click();p.locator('#scene-step-brief').click();p.locator('#scene-step-write').click()
        assert p.evaluate('ManjuDesk.fingerprint()')==finger
        p.locator('#story-scene-editor').scroll_into_view_if_needed();quiet(p)
        p.screenshot(path=str(output/'writing-desktop.png'))
        p.set_viewport_size({'width':390,'height':920});p.locator('#story-scene-editor').scroll_into_view_if_needed()
        assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
        p.screenshot(path=str(output/'writing-narrow.png'))
        p.set_viewport_size({'width':1440,'height':1050});p.locator('[data-story-tab=sources]').click()
        p.evaluate('ManjuExperience.reveal("story-section")');p.screenshot(path=str(output/'characters-desktop.png'))
        nav(p,'shot');expect(p.locator('#prompt')).to_be_visible();expect(p.locator('#story-section')).not_to_be_visible()
        quiet(p);p.screenshot(path=str(output/'shot-desktop.png'))
        exported=output/'CHECKOUT_FOR_AI.zip';save(p,exported);assert media(exported)==original
        # These are deterministic script edits simulating an IDE agent, not a paid agent invocation.
        w=output/'AI_WORK';opened=cli('ide-open',exported,'--output',w)
        assert opened['source_modified'] is False
        before=sha(w/'BASE_CHECKOUT.zip');ref_before={str(x.relative_to(w)):sha(x) for x in (w/'REFERENCES').rglob('*') if x.is_file()}
        story=json.loads((w/'STORY.json').read_text(encoding='utf-8'))
        old_briefs=json.loads(json.dumps(story['briefs']))
        story['scenes'][0]['action']='林岚主动把箱子交给周泊，然后松开双手。'
        story['sources'][0]['text']+='\n本次修改：她更在乎保住信任，而不是箱子本身。'
        (w/'STORY.json').write_text(json.dumps(story,ensure_ascii=False,indent=2),encoding='utf-8')
        edit=json.loads((w/'EDIT.json').read_text(encoding='utf-8'))
        prompt_key=next(key for key in edit['values'] if key=='shot/prompt')
        edit['values'][prompt_key]='中景。林岚把箱子递出，手指逐渐离开箱盖。镜头保持观察，不自动换人物。'
        (w/'EDIT.json').write_text(json.dumps(edit,ensure_ascii=False,indent=2),encoding='utf-8')
        preview=cli('ide-preview',w);(output/'IDE_PREVIEW.json').write_text(json.dumps(preview,ensure_ascii=False,indent=2),encoding='utf-8')
        assert preview['story_changed'] and preview['changed_fields']
        returned=output/'RETURNED_CHECKOUT.zip'
        result=cli('ide-return',w,'--expected-preview',preview['preview_sha256'],'--output',returned)
        assert result['source_modified'] is False and result['requires_explicit_browser_restore']
        assert sha(w/'BASE_CHECKOUT.zip')==before and media(returned)==original
        assert ref_before=={str(x.relative_to(w)):sha(x) for x in (w/'REFERENCES').rglob('*') if x.is_file()}
        after=fresh();restore(after,returned)
        assert after.evaluate('ManjuStory.view().scenes[0].action')==story['scenes'][0]['action']
        assert after.evaluate('ManjuStory.view().briefs')==old_briefs
        assert after.locator('#prompt').input_value()==edit['values'][prompt_key]
        nav(after,'story');after.locator('[data-story-tab=briefs]').click()
        assert after.locator('#story-briefs article[data-stale=true]').count()>=1
        nav(after,'director');after.locator('#director-video').evaluate('v=>v.play()')
        after.wait_for_function('()=>document.querySelector("#director-video").currentTime>.1')
        after.get_by_role('button',name='查看 A01 精修目标图大图',exact=True).click()
        expect(after.locator('#ux-image-meta')).to_contain_text('1920 × 1080');after.keyboard.press('Escape')
        browser_saved=output/'BROWSER_RETURNED.zip';save(after,browser_saved);assert media(browser_saved)==original
        # Python and browser ZIP headers differ, so compare canonical content; browser cycles must be exact.
        re=fresh();restore(re,browser_saved);reexport=output/'BROWSER_REOPENED.zip';save(re,reexport)
        assert reexport.read_bytes()==browser_saved.read_bytes()
        old=fresh(legacy_html.read_text(encoding='utf-8'));restore(old,browser_saved)
        oldexport=output/'LEGACY_REEXPORTED.zip';save(old,oldexport)
        assert oldexport.read_bytes()==browser_saved.read_bytes()
        nav(old,'director');old.locator('#director-video').evaluate('v=>v.play()')
        old.wait_for_function('()=>document.querySelector("#director-video").currentTime>.1')
        c.close();b.close()
    assert not errors,errors;assert not requests,requests
    report={'ok':True,'version':manju.__version__,'runtime':str(Path(manju.__file__).resolve()),
            'html_sha256':sha256(html.encode()).hexdigest(),'input_sha256':sha(example),
            'actual_browser_download':True,'actual_cli_roundtrip':True,
            'new_story_and_text_restored':True,'old_briefs_immutable':True,'old_briefs_show_stale':True,
            'source_backup_unchanged':True,'media_sha256':original,'unchanged_media_files':len(original),
            'fresh_checkout_reopen_identical':True,'legacy_checkout_reexport_identical':True,
            'browser_checkout_sha256':sha(browser_saved),'high_resolution_png_preserved':[1920,1080],
            'javascript_errors':errors,'network_requests':requests,'transport':'isolated_document',
            'native_windows_acceptance':False,'commercial_generation':False,'commercial_ide_agent_tested':False,
            'client_download_confirmed':False}
    (output/'CLI_RESULTS.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--example',type=Path,required=True);p.add_argument('--legacy-html',type=Path,required=True)
    a=p.parse_args();run(a.output.resolve(),a.example.resolve(),a.legacy_html.resolve())
