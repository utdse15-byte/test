"""Actual external-editor roundtrip using real media and the installed HTML.

All files are synthetic. No model call, real-film modification or external upload.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import zipfile

import manju
from manju.authoring.core import canonical, digest, file_digest
from manju.authoring.exchange import read_edit, text_members, preview_edit, apply_edit, apply_archive
from manju.authoring.flexibility import read_verified
from manju.authoring.studio import verify_studio
from playwright.sync_api import sync_playwright, expect


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('fixture',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args();fixture=args.fixture.resolve();root=args.output.resolve()
    root.mkdir(parents=True,exist_ok=False)
    source=fixture/'STUDIO.zip';doc,_=read_verified(source)
    originals={x.name:file_digest(x) for x in fixture.iterdir() if x.is_file()}
    html=Path(str(files('manju.authoring').joinpath('data/workbench.html')))
    errors=[];requests=[];contexts=[];downloads=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        def fresh():
            ctx=browser.new_context(accept_downloads=True,viewport={'width':1365,'height':960});contexts.append(ctx)
            page=ctx.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:requests.append(r.url))
            page.set_content(html.read_text(encoding='utf-8'),wait_until='load')
            return page
        def save(page,button,name):
            with page.expect_download() as item:page.locator(button).click()
            path=root/name;item.value.save_as(path)
            downloads.append({'path':name,'name':item.value.suggested_filename,'sha256':file_digest(path)})
            return path
        def restore(page,path):
            before=page.evaluate('ManjuStudio.view()')
            page.locator('#import-studio').set_input_files(path)
            expect(page.locator('#studio-restore-preview')).to_be_visible()
            assert page.evaluate('ManjuStudio.view()')==before
            page.locator('#studio-restore-confirmed').check();page.locator('#apply-studio').click()
            expect(page.locator('#studio-save-status')).to_contain_text('三个工作区已从实际核验')
        page=fresh();restore(page,source)
        before_export=page.evaluate('ManjuStudio.view()')
        for area in ('shot','review','repair','director','settings'):
            page.locator('#exchange-'+area).check()
        kit=save(page,'#exchange-export-kit','EXTERNAL_FILES.zip')
        assert page.evaluate('ManjuStudio.view()')==before_export
        with zipfile.ZipFile(kit) as z:
            assert z.testzip() is None
            assert len(z.namelist())==len(set(z.namelist()))
            editpath=root/'EDIT.json';editpath.write_bytes(z.read('EDIT.json'))
            edit=read_edit(editpath);index=json.loads(z.read('MEDIA_MAP.json'))
            assert len(index['files'])==5 and all(r['included'] for r in index['files'])
            with zipfile.ZipFile(source) as original:
                for record in index['files']:
                    data=z.read(record['path'])
                    assert data==original.read('media/'+record['sha256'])
                    assert sha256(data).hexdigest()==record['sha256']
            # Actual editor writes a UTF-8 BOM/CRLF TXT rather than mutating backup hashes.
            name={v:k for k,v in text_members(edit).items()}['shot/prompt']
            txt=root/name
            txt.write_bytes(b'\xef\xbb\xbf'+ '  外部精修镜头：只改变冷光。\r\n保留声音与运动。 '.encode('utf-8'))
            edited=edit.model_dump(mode='json')
            edited['values']['director/director-change']='  外部改好的服装与布光\n原画幅保持 '
            edited['values']['director/anchors/A01/target']='  高像素母图仅作外观目标\n不缩小 '
            edited['values']['review/notes']='外部整理的意见，仍需人工确认；这不是批准。'
            edited['values']['repair/repair-start']='1.201'
            edited['values']['repair/repair-end']='2.444'
            edited['values']['settings/return-width']='640'
            returned=root/'RETURNED_JSON.json';returned.write_bytes(canonical(edited))
        page.locator('#exchange-import-json').set_input_files(returned)
        expect(page.locator('#exchange-preview')).to_be_enabled()
        page.locator('#exchange-import-texts').set_input_files(txt)
        expect(page.locator('#exchange-import-status')).to_contain_text('已载入 1 个外部 TXT')
        returned=save(page,'#exchange-save-returned','RETURNED_EDIT.json');external=read_edit(returned)
        assert external.values['shot/prompt']=='  外部精修镜头：只改变冷光。\n保留声音与运动。 '
        # Concurrent local edits are preserved unless this exact conflict is chosen.
        page.locator('#prompt').fill('本地正在写的新版本，先保留')
        page.locator('#preserve').fill('本地新增要求：不要改变衣服轮廓')
        local=page.evaluate('ManjuStudio.view()')
        local_zip=save(page,'#export-studio','LOCAL_BEFORE_APPLY.zip')
        page.locator('#exchange-preview').click()
        expect(page.locator('#exchange-preview-box')).to_be_visible()
        report=page.evaluate('ManjuExchange.state.pending.report')
        assert report==preview_edit(local,external)
        assert next(r for r in report['rows'] if r['key']=='shot/prompt')['status']=='conflict'
        expect(page.get_by_label('带回 shot/prompt',exact=True)).not_to_be_checked()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.locator('#exchange-section').screenshot(path=str(root/'external-preview-narrow.png'))
        take=page.evaluate('[...document.querySelectorAll("#exchange-rows input:checked")].map(x=>x.dataset.key)')
        expected,_=apply_edit(local,external,take)
        page.locator('#exchange-confirmed').check();page.locator('#exchange-apply').click()
        expect(page.locator('#exchange-status')).to_contain_text('已带回')
        assert page.evaluate('ManjuStudio.view()')==expected
        assert page.locator('#prompt').input_value()=='本地正在写的新版本，先保留'
        assert page.locator('#preserve').input_value()=='本地新增要求：不要改变衣服轮廓'
        assert page.locator('#quality-only').is_checked()
        assert page.evaluate('ManjuWorkbench.state.selected') is None
        assert page.evaluate('ManjuReview.state.document')==doc.workspace.review.model_dump(mode='json')
        applied=save(page,'#export-studio','APPLIED_STUDIO.zip')
        applied_doc,receipt=read_verified(applied)
        cli=root/'PYTHON_APPLIED_STUDIO.zip'
        apply_archive(local_zip,external,take,cli,expected_preview=digest(report))
        cli_doc,_=read_verified(cli)
        assert canonical(cli_doc)==canonical(applied_doc)
        assert applied_doc.media==doc.media and applied_doc.workspace.catalog==doc.workspace.catalog
        with zipfile.ZipFile(applied) as z,zipfile.ZipFile(source) as old:
            for record in applied_doc.media:
                name='media/'+record.sha256
                assert z.read(name)==old.read(name)
        comparison=save(page,'#exchange-report','COMPARISON.json')
        assert json.loads(comparison.read_bytes())['not_applied']==['shot/prompt']
        # Revisit the still-pending conflict explicitly; keep the original export baseline.
        page.locator('#exchange-preview').click()
        expect(page.locator('#exchange-preview-box')).to_be_visible()
        assert page.locator('#exchange-rows input:checked').count()==0
        page.get_by_label('带回 shot/prompt',exact=True).check()
        page.locator('#exchange-confirmed').check();page.locator('#exchange-apply').click()
        expect(page.locator('#prompt')).to_have_value(external.values['shot/prompt'])
        assert page.evaluate('ManjuWorkbench.state.stage')=='draft'
        page.locator('#exchange-undo-confirmed').check();page.locator('#exchange-undo').click()
        expect(page.locator('#prompt')).to_have_value('本地正在写的新版本，先保留')
        assert page.evaluate('ManjuStudio.view()')==expected
        page.close();page=fresh();restore(page,applied)
        assert page.evaluate('ManjuStudio.view()')==expected
        assert page.evaluate('ManjuDirector.state.anchors[0].guide.width')==1920
        for selector in ('#candidate-grid video','#repair-video','#director-video'):
            page.locator(selector).first.evaluate('v=>v.play()')
            page.wait_for_function('s=>document.querySelector(s).currentTime>0.2',arg=selector)
            page.locator(selector).first.evaluate('v=>v.pause()')
        reopened=save(page,'#export-studio','REOPENED_STUDIO.zip')
        assert reopened.read_bytes()==applied.read_bytes()
        # Verify return protection after replacing the motion source.
        page.once('dialog',lambda dialog:dialog.accept())
        page.locator('#director-source').set_input_files(fixture/'另一候选_合成.mp4')
        page.wait_for_function('()=>!ManjuDirector.state.busy&&ManjuDirector.state.source?.filename==="另一候选_合成.mp4"')
        page.locator('#exchange-import-json').set_input_files(returned)
        page.locator('#exchange-preview').click()
        expect(page.locator('#exchange-preview-box')).to_be_visible()
        latest=page.evaluate('ManjuExchange.state.pending.report')
        assert next(r for r in latest['rows'] if r['key']=='director/director-change')['status']=='context_changed'
        expect(page.get_by_label('带回 director/director-change',exact=True)).to_be_disabled()
        assert originals=={x.name:file_digest(x) for x in fixture.iterdir() if x.is_file()}
        external_network=[u for u in requests if u.startswith(('http:','https:'))]
        assert not external_network and not errors,(external_network,errors)
        # Fully decode the exported videos, not just container metadata.
        with zipfile.ZipFile(kit) as z:
            for record in index['files']:
                if record['path'].endswith('.mp4'):
                    target=root/Path(record['path']).name;target.write_bytes(z.read(record['path']))
                    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(target),'-f','null','-'],
                                   check=True,capture_output=True,timeout=25)
        result={'ok':True,'version':manju.__version__,'package_import':str(Path(manju.__file__).resolve()),
            'html_sha256':file_digest(html),'actual_browser_downloads':downloads,'edit_sha256':digest(external),
            'source_fixture_unchanged':True,'raw_media_unchanged':True,'unique_exported_media':len(index['files']),
            'actual_utf8_bom_crlf_txt_roundtrip':True,'browser_python_preview_and_apply_equal':True,
            'default_conflict_keeps_local_text':True,'explicit_conflict_and_immediate_undo_tested':True,
            'changed_motion_source_refused':True,'review_history_unchanged':True,
            'native_1920x1080_png_preserved':True,'reopened_videos_play_without_rebinding':True,
            'restored_reexport_byte_identical':True,'full_video_decode_passed':True,
            'source_zip_sha256':file_digest(source),'applied_zip_sha256':file_digest(applied),
            'kit_sha256':file_digest(kit),'studio_verification':receipt,
            'browser_version':browser.version,'page_errors':errors,'http_requests':len(external_network),
            'commercial_generation_calls':0,'transport':'isolated_document','native_file_navigation_certified':False}
        (root/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        for context in contexts:context.close()
        browser.close()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
