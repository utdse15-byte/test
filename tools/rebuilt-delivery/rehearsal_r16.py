"""Real browser check-out roundtrip using byte-preserved synthetic R15 media.

Works from source or an independently installed wheel. No commercial API or
real user film is used. Native file navigation remains a separate platform gate.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from importlib.resources import files
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import manju
from manju.authoring.core import canonical, file_digest
from manju.authoring.desk import verify_desk, extract_studio
from playwright.sync_api import sync_playwright, expect


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--legacy-html',type=Path)
    parser.add_argument('--legacy-pythonpath',type=Path)
    args=parser.parse_args();fixture=args.fixture.resolve();root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    source=fixture/'BASE_STUDIO.zip';returned=fixture/'RETURNED_EDIT.json'
    originals={p.name:file_digest(p) for p in fixture.iterdir() if p.is_file()}
    html=Path(str(files('manju.authoring').joinpath('data/workbench.html')))
    errors=[];requests=[];contexts=[];downloads=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        def fresh(content=None):
            ctx=browser.new_context(accept_downloads=True,viewport={'width':1365,'height':960});contexts.append(ctx)
            page=ctx.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:requests.append(r.url))
            page.set_content(content or html.read_text(encoding='utf-8'),wait_until='load')
            return page
        def save(page,button,name):
            with page.expect_download() as d:page.locator(button).click()
            out=root/name;d.value.save_as(out);downloads.append({'name':name,'suggested_filename':d.value.suggested_filename,'sha256':file_digest(out)})
            return out
        def intake(page,path):
            page.locator('#intake-files').set_input_files(path)
            expect(page.locator('#intake-preview')).to_be_visible()
            page.locator('#intake-open').click()
        page=fresh();before=page.evaluate('ManjuDesk.fingerprint()');intake(page,source)
        expect(page.locator('#studio-restore-preview')).to_be_visible()
        assert page.evaluate('ManjuDesk.fingerprint()')==before
        page.locator('#studio-restore-confirmed').check();page.locator('#apply-studio').click()
        expect(page.locator('#studio-save-status')).to_contain_text('三个工作区已从实际核验')
        intake(page,returned);expect(page.locator('#exchange-preview')).to_be_enabled()
        template={'schema_id':'manju.creative-template/v1','name':'运动不动，外观另做','notes':'合成演练模板，不是用户批准',
                  'fields':{'director':{'director-preserve':'保留运动和镜头路径','director-change':'目标图决定灯光和衣服'}},
                  'quality_only_on_apply':True,'contains_media':False,'contains_approvals':False,'automatic_execution':False}
        template_path=root/'TEMPLATE.json';template_path.write_bytes(canonical(template));intake(page,template_path)
        expect(page.locator('#flex-preview-template')).to_be_enabled()
        page.locator('#flex-template-name').fill('  还未定名的个人方法  ')
        page.locator('#flex-template-notes').fill('  这段说明还没写完\n明天继续 ')
        page.locator('#flex-branch-name').fill('B方案_收工前')
        page.locator('#prompt').fill('本地正在继续写的新稿。外部意见还未决定，不要覆盖。')
        expected=page.evaluate('ManjuDesk.fingerprint()')
        expected_buffers=page.evaluate('ManjuDesk.view()')
        archive=save(page,'#desk-save','DESK.zip');receipt=verify_desk(archive)
        assert receipt['external_edit_present'] and receipt['personal_template_present']
        assert receipt['studio']['media_files']==5
        assert page.evaluate('ManjuDesk.needsSave()')
        page.locator('#desk-verify-file').set_input_files(archive)
        expect(page.locator('#desk-save-status')).to_contain_text('均与当前一致')
        assert not page.evaluate('ManjuDesk.needsSave()')
        page.set_viewport_size({'width':390,'height':844});page.locator('#studio-home').scroll_into_view_if_needed()
        page.screenshot(path=str(root/'home-narrow.png'),full_page=False)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        reopened=fresh();intake(reopened,archive)
        expect(reopened.locator('#desk-restore-preview')).to_be_visible()
        assert reopened.locator('#prompt').input_value()!='本地正在继续写的新稿。外部意见还未决定，不要覆盖。'
        reopened.set_viewport_size({'width':390,'height':844});reopened.locator('#desk-restore-preview').scroll_into_view_if_needed()
        reopened.screenshot(path=str(root/'restore-narrow.png'),full_page=False)
        assert reopened.evaluate('document.documentElement.scrollWidth<=innerWidth')
        reopened.locator('#desk-restore-confirmed').check();reopened.locator('#desk-restore-apply').click()
        expect(reopened.locator('#desk-save-status')).to_contain_text('收工包已恢复')
        assert reopened.evaluate('ManjuDesk.fingerprint()')==expected
        assert reopened.evaluate('ManjuDesk.view()')==expected_buffers
        for key in ('human-confirmed','exchange-confirmed','flex-confirmed','promotion-confirmed'):
            assert not reopened.locator('#'+key).is_checked()
        assert reopened.locator('#quality-only').is_checked()
        video_count=reopened.evaluate('''async()=>{let count=0;for(const v of document.querySelectorAll('video')){if(v.src&&v.src.startsWith('blob:')){await v.play();await new Promise(r=>setTimeout(r,120));if(v.currentTime<=0)throw new Error('video did not progress');v.pause();count++;}}return count;}''')
        assert video_count>=2
        again=save(reopened,'#desk-save','DESK_AGAIN.zip');assert archive.read_bytes()==again.read_bytes()
        reopened.locator('#exchange-preview').click();expect(reopened.locator('#exchange-preview-box')).to_be_visible()
        conflict=reopened.locator('#exchange-rows input[data-key="shot/prompt"]')
        assert conflict.count()==1 and not conflict.is_checked()
        assert reopened.locator('#prompt').input_value()=='本地正在继续写的新稿。外部意见还未决定，不要覆盖。'
        # A corrupt new edit cannot delete the valid staged edit.
        reopened.locator('#exchange-import-json').set_input_files({'name':'broken.json','mimeType':'application/json','buffer':b'{broken'})
        expect(reopened.locator('#status')).to_have_class('status error')
        assert reopened.evaluate('ManjuExchange.state.edit')==expected_buffers['external_edit']
        # Old total-backup readers only receive the unchanged inner ZIP.
        inner=root/'STUDIO_FOR_R15.zip';extract_studio(archive,inner)
        if args.legacy_html:
            legacy=fresh(args.legacy_html.read_text(encoding='utf-8'))
            legacy.locator('#import-studio').set_input_files(inner);expect(legacy.locator('#studio-restore-preview')).to_be_visible()
            legacy.locator('#studio-restore-confirmed').check();legacy.locator('#apply-studio').click()
            expect(legacy.locator('#studio-save-status')).to_contain_text('三个工作区已从实际核验')
            old_again=save(legacy,'#export-studio','LEGACY_STUDIO_AGAIN.zip');assert old_again.read_bytes()==inner.read_bytes()
        for ctx in contexts:ctx.close()
        browser.close()
    # Independent complete-video decode, not only browser metadata inspection.
    decoded=[];target_dimensions=[]
    with zipfile.ZipFile(inner) as z:
        doc=json.loads(z.read('STUDIO.json'))
        with zipfile.ZipFile(source) as original:
            original_doc=json.loads(original.read('STUDIO.json'))
            assert doc['media']==original_doc['media']
            for m in doc['media']:
                data=z.read('media/'+m['sha256']);assert data==original.read('media/'+m['sha256'])
                assert sha256(data).hexdigest()==m['sha256']
                if m['filename'].endswith('.mp4'):
                    file=root/m['filename'];file.write_bytes(data)
                    subprocess.run(['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-i',str(file),'-map','0:v:0','-f','null','-'],check=True,capture_output=True,timeout=30)
                    decoded.append(m['filename'])
            for anchor in doc['director']['anchors']:
                if anchor['guide']:target_dimensions.append([anchor['guide']['width'],anchor['guide']['height']])
    assert [1920,1080] in target_dimensions and len(decoded)==2
    if args.legacy_pythonpath:
        env=dict(os.environ,PYTHONPATH=str(args.legacy_pythonpath.resolve()))
        process=subprocess.run([sys.executable,'-c','from pathlib import Path; from manju.authoring.studio import verify_studio; import sys,json; print(json.dumps(verify_studio(Path(sys.argv[1]))))',str(inner)],cwd=root,env=env,check=True,capture_output=True,text=True,timeout=30)
        legacy_receipt=json.loads(process.stdout);assert legacy_receipt['ok']
        (root/'LEGACY_RECEIPT.json').write_text(json.dumps(legacy_receipt,indent=2),encoding='utf-8')
    assert all(file_digest(fixture/name)==h for name,h in originals.items())
    assert not errors,errors
    assert not [u for u in requests if u.startswith(('http://','https://'))],requests
    report={'ok':True,'manju_import_path':str(Path(manju.__file__).resolve()),'html_sha256':file_digest(html),
            'desk_receipt':receipt,'original_media_bytes_preserved':5,'videos_played_after_restore':video_count,
            'videos_fully_decoded':decoded,'guide_dimensions_preserved':target_dimensions,
            'browser_roundtrip_identical':archive.read_bytes()==again.read_bytes(),
            'unapplied_edit_and_template_restored':True,'local_external_conflict_left_unselected':True,
            'invalid_edit_did_not_erase_previous_buffer':True,'legacy_html_roundtrip':bool(args.legacy_html),
            'legacy_python_verified':bool(args.legacy_pythonpath),'page_errors':errors,'remote_requests':[],
            'browser_transport':'isolated_document, not native file://','commercial_api_calls':0,'downloads':downloads}
    (root/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
