"""Actual local file/CLI/browser/media selective-return rehearsal.

Inputs are pre-existing synthetic media and fiction, not a commercial AI output.
Run identically against source, independently installed wheel and final archive.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from playwright.sync_api import sync_playwright, expect
import manju
from manju.authoring.core import canonical
from manju.authoring.desk import verify_desk
from manju.authoring.story_return import preview_return


def sha(path):
    import hashlib
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def media(path):
    with zipfile.ZipFile(path) as outer, zipfile.ZipFile(BytesIO(outer.read('STUDIO.zip'))) as inner:
        return {n:sha256(inner.read(n)).hexdigest() for n in inner.namelist() if n.startswith('media/')}


def run(output:Path,example:Path,legacy_html:Path):
    output.mkdir(parents=True,exist_ok=False)
    html=files('manju.authoring').joinpath('data/workbench.html').read_text(encoding='utf-8')
    original=media(example);initial_hash=sha(example);errors=[];requests=[];commands=[]
    def cli(*args):
        r=subprocess.run([sys.executable,'-m','manju','models',*map(str,args)],cwd=output,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
        commands.append({'args':list(map(str,args)),'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        assert r.returncode==0,r.stdout+r.stderr
        return json.loads(r.stdout)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        ctx=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1080})
        def fresh(text=html):
            p=ctx.new_page();p.set_default_timeout(20000)
            p.on('pageerror',lambda e:errors.append(str(e)))
            p.on('request',lambda r:requests.append(r.url) if r.url.startswith(('http:','https:')) else None)
            p.set_content(text,wait_until='load');return p
        def nav(p,view):p.locator('#ux-navigation [data-view='+view+']').click()
        def restore(p,path):
            nav(p,'home');p.locator('#intake-files').set_input_files(path)
            expect(p.locator('#intake-preview')).to_be_visible();p.locator('#intake-open').click()
            expect(p.locator('#desk-restore-preview')).to_be_visible()
            p.locator('#desk-restore-confirmed').check();p.locator('#desk-restore-apply').click()
            expect(p.locator('#desk-save-status')).to_contain_text('收工包已恢复')
        def save(p,path):
            with p.expect_download() as d:p.locator('#ux-save').click()
            d.value.save_as(path);assert verify_desk(path)['ok']
        def pose(p):
            if p.locator('#ux-notification').is_visible():p.locator('#ux-notification button').click()
            p.locator('#story-return-section').evaluate('e=>e.scrollIntoView({block:"start"})')
            p.evaluate('()=>scrollBy(0,-108)')
        p=fresh();restore(p,example);base=output/'CHECKOUT_FOR_AI.zip';save(p,base)
        work=output/'AI_WORK';cli('ide-open',base,'--output',work)
        old=json.loads((work/'STORY.json').read_text(encoding='utf-8'));candidate=deepcopy(old);sid=candidate['scenes'][1]['id']
        candidate['scenes'][1]['action']='周泊先把修好的怀表推到林岚面前，才展开湿透的账页。她认出自己的笔迹，却先问他从哪里找到。他没有收回怀表，只把原本朝向她的椅子挪开了一点。'
        candidate['scenes'][1]['dialogue']='周泊：你可以晚一点告诉我。\n林岚：我不是故意让你等。\n周泊：那就别再替我决定该知道什么。'
        (work/'STORY.json').write_bytes(canonical(candidate))
        preview=cli('ide-preview',work,'--html',output/'READ_CHANGES.html')
        packet_file=output/'STORY_RETURN.json';cli('ide-story-return',work,'--expected-preview',preview['preview_sha256'],'--output',packet_file)
        packet=json.loads(packet_file.read_text(encoding='utf-8'))
        nav(p,'story');p.locator('#story-scenes button').nth(0).click();p.locator('#story-action').fill('这是我在网页后来补写的第一场，必须保留。')
        p.locator('#story-scenes button').nth(1).click();p.locator('#story-dialogue').fill('林岚：这句是我刚写的，先不要替换。')
        local=p.evaluate('ManjuStory.view()');localzip=output/'LOCAL_LATER_CHECKOUT.zip';save(p,localzip)
        nav(p,'home');p.locator('#intake-files').set_input_files(packet_file);expect(p.locator('#intake-preview')).to_be_visible();p.locator('#intake-open').click()
        expect(p.locator('#story-return-results')).to_be_visible()
        report=p.evaluate('ManjuStoryReturn.state.pending.report');assert report==preview_return(local,packet)
        expect(p.locator('[data-return-key="scene/'+sid+'/action"]')).to_be_checked()
        expect(p.locator('[data-return-key="scene/'+sid+'/dialogue"]')).to_be_disabled()
        assert p.evaluate('ManjuStory.view()')==local
        pose(p);p.screenshot(path=str(output/'SELECTIVE_DESKTOP.png'))
        p.set_viewport_size({'width':390,'height':1080});pose(p);assert p.evaluate('document.documentElement.scrollWidth<=innerWidth');p.screenshot(path=str(output/'SELECTIVE_MOBILE.png'))
        p.set_viewport_size({'width':1440,'height':1080});p.locator('#story-return-apply').click()
        accepted=p.evaluate('ManjuStory.view()')
        assert accepted['scenes'][1]['action']==candidate['scenes'][1]['action']
        assert accepted['scenes'][1]['dialogue']==local['scenes'][1]['dialogue'] and accepted['scenes'][0]==local['scenes'][0]
        assert accepted['scenes'][2]==old['scenes'][2] and accepted['ending']==old['ending'] and accepted['briefs']==old['briefs']
        expect(p.locator('#story-return-apply')).to_be_disabled()
        p.locator('#story-return-undo').click();assert p.evaluate('ManjuStory.view()')==local
        p.locator('#story-return-apply').click();assert p.evaluate('ManjuStory.view()')==accepted
        saved=output/'SELECTED_CHECKOUT.zip';save(p,saved);assert media(saved)==original
        with zipfile.ZipFile(localzip) as za,zipfile.ZipFile(saved) as zb:assert za.read('STUDIO.zip')==zb.read('STUDIO.zip')
        nav(p,'home');p.locator('#desk-verify-file').set_input_files(saved);expect(p.locator('#desk-save-status')).to_contain_text('核验')
        again=fresh();restore(again,saved);nav(again,'director')
        again.locator('#director-video').evaluate('v=>v.play()');again.wait_for_function('()=>document.querySelector("#director-video").currentTime>0.1')
        again.get_by_role('button',name='查看 A01 精修目标图大图',exact=True).click();expect(again.locator('#ux-image-meta')).to_contain_text('1920 × 1080');again.keyboard.press('Escape')
        repeat=output/'REOPENED_CHECKOUT.zip';save(again,repeat);assert repeat.read_bytes()==saved.read_bytes()
        legacy=fresh(legacy_html.read_text(encoding='utf-8'));restore(legacy,saved);oldreturn=output/'LEGACY_REEXPORTED.zip';save(legacy,oldreturn);assert oldreturn.read_bytes()==saved.read_bytes()
        nav(again,'story');again.locator('#story-return-section>summary').click();again.locator('#story-return-file').set_input_files(packet_file)
        expect(again.locator('#story-return-summary')).to_contain_text('1 项已一致');expect(again.locator('#story-return-apply')).to_be_disabled()
        again.locator('#story-scenes button').nth(0).click();again.locator('#story-action').fill('比较之后又写的内容')
        expect(again.locator('#story-return-summary')).to_contain_text('已变化')
        assert again.evaluate('ManjuStory.view().scenes[0].action')=='比较之后又写的内容'
        (output/'BROWSER_PREVIEW.json').write_bytes(canonical(report))
        ctx.close();browser.close()
    assert not errors and not requests,(errors,requests)
    assert sha(example)==initial_hash and sha(work/'BASE_CHECKOUT.zip')==sha(base)
    result={'ok':True,'version':manju.__version__,'runtime':str(Path(manju.__file__).resolve()),
        'html_sha256':sha256(html.encode()).hexdigest(),'input_sha256':initial_hash,
        'actual_browser_download':True,'actual_cli_roundtrip':True,'three_way_report_parity':True,
        'local_later_fields_preserved':True,'only_selected_story_fields_changed':True,'ending_field_unchanged':True,
        'old_briefs_immutable':True,'inner_studio_unchanged':True,'source_backup_unchanged':True,
        'undo_tested':True,'stale_preview_refused':True,'already_applied_not_repeated':True,
        'fresh_checkout_reopen_identical':True,'legacy_checkout_reexport_identical':True,
        'media_sha256':original,'unchanged_media_files':len(original),'high_resolution_png_preserved':[1920,1080],
        'browser_checkout_sha256':sha(saved),'packet_sha256':sha(packet_file),
        'javascript_errors':errors,'network_requests':requests,'transport':'isolated_document',
        'native_windows_acceptance':False,'commercial_generation':False,'commercial_ide_agent_tested':False,'client_download_confirmed':False}
    (output/'CLI_RESULTS.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--example',type=Path,required=True);p.add_argument('--legacy-html',type=Path,required=True)
    a=p.parse_args();run(a.output.resolve(),a.example.resolve(),a.legacy_html.resolve())
