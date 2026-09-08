"""Real synthetic media across all three workspaces, with actual ZIP downloads.

No model generation or user approval. Isolated-document browser transport is
explicit, and is not a Windows native file-navigation certification.
"""
from __future__ import annotations
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import zipfile

import manju
from manju.authoring.core import file_digest
from manju.authoring.studio import verify_studio
from playwright.sync_api import sync_playwright, expect
from rehearsal_r12 import synthetic_png


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    root=parser.parse_args().output.resolve();root.mkdir(parents=True,exist_ok=False)
    source=root/'运动原片_合成.mp4';other=root/'另一候选_合成.mp4';guide=root/'高像素目标_1920x1080.png'
    for dest,filtergraph in [(source,'testsrc2=s=640x360:r=24'),(other,'testsrc=s=640x360:r=24')]:
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',filtergraph,
            '-f','lavfi','-i','sine=f=440:r=48000','-t','3','-c:v','libx264','-threads','1',
            '-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac',str(dest)],
            check=True,capture_output=True,timeout=25)
    guide.write_bytes(synthetic_png(1920,1080));originals={p.name:file_digest(p) for p in (source,other,guide)}
    html=Path(str(files('manju.authoring').joinpath('data/workbench.html')))
    errors,network,contexts=[],[],[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        def fresh():
            ctx=browser.new_context(accept_downloads=True,viewport={'width':1365,'height':960});contexts.append(ctx)
            page=ctx.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:network.append(r.url))
            page.set_content(html.read_text(encoding='utf-8'),wait_until='load')
            # Deterministic declarations are synthetic fixtures, never user approval.
            page.evaluate("""()=>{const D=Date;window.Date=class extends D{constructor(...a){super(...(a.length?a:['2026-09-08T12:00:00Z']));}static now(){return new D('2026-09-08T12:00:00Z').getTime();}};crypto.getRandomValues=a=>{a.fill(7);return a;};}""")
            return page
        page=fresh()
        page.locator('#shot-id').fill('R13_合成全场恢复')
        page.locator('#task').select_option('edit');page.locator('#prompt').fill('  保留运动与镜头。\n 未写完的说明 ')
        page.locator('#preserve').fill('原镜头运动');page.locator('#change').fill('衣服颜色');page.locator('#duration').fill('3')
        page.locator('#asset-role').select_option('source_video');page.locator('#asset-files').set_input_files(source)
        page.wait_for_function('()=>ManjuWorkbench.state.assets.length===1&&ManjuWorkbench.state.files.size===1')
        page.locator('#review-files').set_input_files([source,other])
        page.wait_for_function('()=>ManjuReview.state.pending.length===2&&!ManjuReview.state.busy')
        page.locator('#create-review').click();page.wait_for_function('()=>ManjuReview.state.document!==null')
        page.locator('#play-review').click();page.wait_for_function('()=>[...document.querySelectorAll("#candidate-grid video")].every(v=>v.currentTime>0.2)')
        page.locator('#pause-review').click()
        page.locator('#review-candidate').select_option(index=1);page.locator('#review-verdict').select_option('revise')
        page.locator('#review-human').fill('合成验收，非用户批准');page.locator('#review-notes').fill('仅验证本地保存，不对真实作品做决定。')
        page.locator('#review-confirmed').check();page.locator('#record-review').click()
        page.wait_for_function('()=>ManjuReview.state.document.decisions.length===1&&!ManjuReview.state.busy')
        page.locator('#review-notes').fill('  下一条意见，尚未提交\n ')
        page.locator('#repair-source').set_input_files(source);page.wait_for_function('()=>ManjuRepair.state.source!==null&&!ManjuRepair.state.busy')
        page.locator('#repair-current-context').click();page.locator('#repair-start').fill('2.444');page.locator('#repair-end').fill('1.201')
        page.locator('#repair-change').fill('  还没修正开始结束时间\n ');page.locator('#repair-human').fill('')
        page.locator('#director-source').set_input_files(source);page.wait_for_function('()=>ManjuDirector.state.source!==null&&!ManjuDirector.state.busy')
        page.wait_for_function('()=>document.getElementById("director-video").readyState>=2')
        for n,t in enumerate((0.5,1.5),1):
            page.evaluate('t=>{const v=document.getElementById("director-video");v.pause();v.currentTime=t;}',t)
            page.wait_for_function('()=>!document.getElementById("director-video").seeking&&document.getElementById("director-video").readyState>=2')
            page.locator('#director-capture').click();page.wait_for_function('n=>ManjuDirector.state.anchors.length===n&&!ManjuDirector.state.busy',arg=n)
        page.get_by_label('A01 目标 PNG',exact=True).set_input_files(guide)
        page.wait_for_function('()=>ManjuDirector.state.anchors[0].guide!==null&&!ManjuDirector.state.busy')
        page.get_by_label('A01 目标外观',exact=True).fill('  保留高像素母图\n 尚未完成 ')
        page.get_by_label('A02 目标外观',exact=True).fill('  第二个目标图尚未制作 ')
        page.locator('#director-shot').fill('');page.locator('#director-change').fill('  未定稿文字\n ')
        page.locator('#return-section > summary').click();page.locator('#return-width').fill('640');page.locator('#return-height').fill('360')
        page.locator('#promotion-resolution').fill('  4K，待确认  ')
        before=page.evaluate('ManjuStudio.view()')
        with page.expect_download() as item:page.locator('#export-studio').click()
        checkpoint=root/'STUDIO.zip';item.value.save_as(checkpoint)
        result=verify_studio(checkpoint);assert result['media_files']==5 and result['director_anchors']==2
        assert result['review_decisions']==1
        assert page.evaluate('ManjuStudio.needsBackup()')
        page.locator('#verify-studio-download').set_input_files(checkpoint)
        expect(page.locator('#studio-save-status')).to_contain_text('与当前三个工作区一致')
        assert not page.evaluate('ManjuStudio.needsBackup()')
        assert before==page.evaluate('ManjuStudio.view()')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#studio-home').screenshot(path=str(root/'studio-home-narrow.png'))
        page.close();page=fresh()
        page.locator('#prompt').fill('另一个窗口的新工作');current=page.evaluate('ManjuStudio.view()')
        page.locator('#import-studio').set_input_files(checkpoint)
        expect(page.locator('#studio-restore-preview')).to_be_visible()
        assert current==page.evaluate('ManjuStudio.view()')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#studio-home').screenshot(path=str(root/'restore-preview-narrow.png'))
        page.locator('#studio-restore-confirmed').check();page.locator('#apply-studio').click()
        expect(page.locator('#studio-save-status')).to_contain_text('三个工作区已从实际核验')
        assert page.evaluate('ManjuStudio.view()')==before
        assert not page.evaluate('ManjuStudio.needsBackup()')
        assert page.locator('#quality-only').is_checked()
        assert page.evaluate('ManjuWorkbench.state.selected') is None
        for selector in ('#candidate-grid video','#repair-video','#director-video'):
            page.locator(selector).first.evaluate('v=>v.play()')
            page.wait_for_function('s=>document.querySelector(s).currentTime>0.2',arg=selector)
            page.locator(selector).first.evaluate('v=>v.pause()')
        assert page.evaluate('ManjuDirector.state.anchors[0].guide.width')==1920
        assert page.locator('#repair-start').input_value()=='2.444'
        assert page.locator('#repair-end').input_value()=='1.201'
        assert not page.locator('#repair-confirmed').is_checked()
        with page.expect_download() as item:page.locator('#export-studio').click()
        again=root/'STUDIO_RESTORED.zip';item.value.save_as(again)
        assert verify_studio(again)['studio_sha256']==result['studio_sha256']
        assert again.read_bytes()==checkpoint.read_bytes()
        with zipfile.ZipFile(checkpoint) as z:
            for path in (source,other,guide):assert sha256(z.read('media/'+originals[path.name])).hexdigest()==originals[path.name]
            assert len([n for n in z.namelist() if n=='media/'+originals[source.name]])==1
        for path in (source,other,guide):assert file_digest(path)==originals[path.name]
        for video in (source,other):
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(video),'-f','null','-'],check=True,capture_output=True,timeout=20)
        external=[u for u in network if u.startswith(('http:','https:'))];assert not external,external
        assert not errors,errors
        report={'ok':True,'package_import':str(Path(manju.__file__).resolve()),'package_version':manju.__version__,
            'html_sha256':file_digest(html),'studio_zip_sha256':file_digest(checkpoint),'studio_verification':result,
            'actual_browser_downloads_saved':2,'raw_forms_whitespace_and_incomplete_inputs_preserved':True,
            'one_source_deduplicated_across_three_areas':True,'reference_media_unique_count':5,
            'native_1920x1080_guide_unchanged':True,'browser_restored_videos_played_without_rebinding':True,
            'preview_did_not_replace_current_work':True,'restored_reexport_byte_identical':True,
            'download_reselection_verified_current_snapshot':True,'quality_filter_restored_true':True,
            'current_confirmations_restored':False,'original_files_unchanged':True,'source_video_full_decode_passed':True,
            'synthetic_review_decision_not_user_approval':True,'commercial_generation_calls':0,
            'http_requests':len(external),'page_errors':errors,'browser_version':browser.version,
            'transport':'isolated_document','native_file_navigation_certified':False}
        (root/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        for context in contexts:context.close()
        browser.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
