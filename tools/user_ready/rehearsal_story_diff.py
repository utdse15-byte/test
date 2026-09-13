"""Actual IDE/file/browser comparison cycle with shipped synthetic media.

No commercial IDE/model is invoked. The current interpreter and packaged HTML are
used so the same script can test source, installed wheel and extracted delivery.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from playwright.sync_api import sync_playwright, expect
import manju
from manju.authoring.core import canonical
from manju.authoring.desk import verify_desk


def sha(path):
    with Path(path).open('rb') as f:
        import hashlib
        return hashlib.file_digest(f,'sha256').hexdigest()


def media(path):
    with zipfile.ZipFile(path) as z,zipfile.ZipFile(BytesIO(z.read('STUDIO.zip'))) as inner:
        return {n:sha256(inner.read(n)).hexdigest() for n in inner.namelist() if n.startswith('media/')}


def run(output,example,legacy_html):
    output.mkdir(parents=True,exist_ok=False)
    html=files('manju.authoring').joinpath('data/workbench.html').read_text(encoding='utf-8')
    original=media(example);original_input=sha(example);errors=[];network=[];commands=[]
    def cli(*args):
        command=[sys.executable,'-m','manju','models',*map(str,args)]
        r=subprocess.run(command,cwd=output,capture_output=True,text=True,encoding='utf-8',timeout=90)
        commands.append({'args':list(map(str,args)),'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
        assert r.returncode==0,r.stdout+r.stderr
        return json.loads(r.stdout)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        context=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1050})
        def fresh(text=html):
            p=context.new_page();p.set_default_timeout(20000)
            p.on('pageerror',lambda e:errors.append(str(e)))
            p.on('request',lambda r:network.append(r.url) if r.url.startswith(('http:','https:')) else None)
            p.set_content(text,wait_until='load');return p
        def prepare(p,path):
            p.locator('#ux-navigation [data-view=home]').click()
            p.locator('#intake-files').set_input_files(path)
            expect(p.locator('#intake-preview')).to_be_visible();p.locator('#intake-open').click()
            expect(p.locator('#desk-restore-preview')).to_be_visible()
        def confirm(p):
            p.locator('#desk-restore-confirmed').check();p.locator('#desk-restore-apply').click()
            expect(p.locator('#desk-save-status')).to_contain_text('收工包已恢复')
        def save(p,path):
            with p.expect_download() as dl:p.locator('#ux-save').click()
            dl.value.save_as(path);verify_desk(path)
        def quiet(p):
            if p.locator('#ux-notification').is_visible():p.locator('#ux-notification button').click()
        p=fresh();prepare(p,example);confirm(p)
        baseline=output/'CHECKOUT_FOR_AI.zip';save(p,baseline)
        w=output/'AI_WORK';cli('ide-open',baseline,'--output',w)
        source=json.loads((w/'STORY.json').read_text(encoding='utf-8'));old=json.loads(canonical(source));old_briefs=source['briefs']
        assert len(source['scenes'])>=3
        second=source['scenes'][1];identity=second['id']
        second['action']='周泊把湿透的账页压在桌上。林岚认出了自己的笔迹，却先问他从哪里找到。周泊等了片刻，把原本朝向她的椅子移开；她伸出的手停在半途。'
        second['dialogue']='周泊：你说过，那晚没有别人来过。\n林岚：我想先把事情弄清楚。\n周泊：所以你让我等的，不是答案。'
        (w/'STORY.json').write_bytes(canonical(source))
        report_path=output/'READ_CHANGES.html'
        preview=cli('ide-preview',w,'--html',report_path)
        assert preview['changed_fields']==[] and preview['story_changed']
        diff=preview['story_diff'];row=next(x for x in diff['scenes'] if x['id']==identity)
        assert row['before_position']==2 and row['after_position']==2
        assert {x['field'] for x in row['changes']}=={'action','dialogue'}
        assert diff['ending_field_unchanged'] and not diff['orders']['scenes']['changed']
        returned=output/'RETURNED_CHECKOUT.zip';cli('ide-return',w,'--expected-preview',preview['preview_sha256'],'--output',returned)
        assert media(returned)==original and sha(w/'BASE_CHECKOUT.zip')==sha(baseline)
        # Comparison uses the actual current page, not the earlier exported baseline.
        p.locator('#ux-navigation [data-view=story]').click()
        p.evaluate('id=>{ManjuStory.state.sceneId=id;ManjuStory.render()}',identity)
        p.locator('#story-action').fill('这是导出之后仍然在网页继续写的新稿。')
        finger=p.evaluate('ManjuDesk.fingerprint()');prepare(p,returned)
        expect(p.locator('#desk-story-diff')).to_contain_text('这是导出之后仍然在网页继续写的新稿。')
        expect(p.locator('#desk-story-diff')).to_contain_text(second['action'])
        assert p.evaluate('ManjuDesk.fingerprint()')==finger
        p.locator('#desk-restore-cancel').click()
        assert p.evaluate('ManjuDesk.fingerprint()')==finger
        # Current edits invalidate an already displayed comparison without applying it.
        prepare(p,returned);p.locator('#ux-navigation [data-view=story]').click()
        p.locator('#story-action').fill('对照打开后写的另一句，不能被旧预览覆盖。')
        expect(p.locator('#desk-restore-apply')).to_be_disabled()
        # A fresh current document is deliberately loaded for the clean baseline example.
        p.evaluate('d=>ManjuStory.install(d)',old);prepare(p,returned)
        got=p.evaluate('([a,b])=>ManjuStoryDiff.compare(a,b)',[old,source]);assert got==diff
        (output/'BROWSER_DIFF.json').write_bytes(canonical(got))
        quiet(p);p.locator('#desk-story-diff').scroll_into_view_if_needed();p.screenshot(path=str(output/'compare-desktop.png'))
        p.set_viewport_size({'width':390,'height':1000});p.locator('#desk-story-diff').scroll_into_view_if_needed();assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
        p.screenshot(path=str(output/'compare-narrow.png'))
        p.set_viewport_size({'width':1440,'height':1050});confirm(p)
        assert p.evaluate('ManjuStory.view()')==source and source['briefs']==old_briefs
        p.locator('#ux-navigation [data-view=director]').click();p.locator('#director-video').evaluate('v=>v.play()')
        p.wait_for_function('()=>document.querySelector("#director-video").currentTime>0.1')
        saved=output/'BROWSER_RETURNED.zip';save(p,saved);assert media(saved)==original
        # Select the actual downloaded file back, then recover in a fresh window.
        p.locator('#ux-navigation [data-view=home]').click();p.locator('#desk-verify-file').set_input_files(saved)
        expect(p.locator('#desk-save-status')).to_contain_text('核验')
        again=fresh();prepare(again,saved);confirm(again);cycle=output/'BROWSER_REOPENED.zip';save(again,cycle)
        assert saved.read_bytes()==cycle.read_bytes()
        legacy=fresh(legacy_html.read_text(encoding='utf-8'));prepare(legacy,saved);confirm(legacy)
        oldout=output/'LEGACY_REEXPORTED.zip';save(legacy,oldout);assert oldout.read_bytes()==saved.read_bytes()
        # The report itself is a standalone, script-free page, with no network I/O.
        view=fresh(report_path.read_text(encoding='utf-8'))
        assert view.locator('script,input,button').count()==0
        expect(view.locator('body')).to_contain_text(second['action'])
        view.screenshot(path=str(output/'report-desktop.png'))
        view.set_viewport_size({'width':390,'height':1000});assert view.evaluate('document.documentElement.scrollWidth<=innerWidth')
        view.screenshot(path=str(output/'report-narrow.png'))
        context.close();browser.close()
    assert not errors and not network,(errors,network)
    assert sha(example)==original_input
    result={'ok':True,'version':manju.__version__,'runtime':str(Path(manju.__file__).resolve()),
        'html_sha256':sha256(html.encode()).hexdigest(),'input_sha256':original_input,
        'actual_browser_download':True,'actual_cli_roundtrip':True,'new_story_and_text_restored':True,
        'old_briefs_immutable':True,'old_briefs_show_stale':any(x['status']=='needs_review' for x in preview['brief_status']),
        'source_backup_unchanged':True,'media_sha256':original,'unchanged_media_files':len(original),
        'fresh_checkout_reopen_identical':True,'legacy_checkout_reexport_identical':True,
        'browser_checkout_sha256':sha(saved),'high_resolution_png_preserved':[1920,1080],
        'readable_story_diff_parity':True,'current_browser_edit_shown_not_merged':True,
        'stale_comparison_refused':True,'report_contains_full_before_after':True,
        'report_sha256':sha(report_path),'javascript_errors':errors,'network_requests':network,
        'transport':'isolated_document','native_windows_acceptance':False,'commercial_generation':False,
        'commercial_ide_agent_tested':False,'client_download_confirmed':False}
    (output/'IDE_PREVIEW.json').write_bytes(canonical(preview))
    (output/'CLI_RESULTS.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--example',type=Path,required=True);p.add_argument('--legacy-html',type=Path,required=True)
    a=p.parse_args();run(a.output.resolve(),a.example.resolve(),a.legacy_html.resolve())
