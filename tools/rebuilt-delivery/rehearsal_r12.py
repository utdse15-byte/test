"""Exercise the imported package with real synthetic media and browser downloads.

No paid generation, native file-navigation certification or user approval.
The source and isolated-wheel executions import their own resource HTML. The
synthetic high-pixel guide is not an AI output or a restoration-quality claim.
"""
from __future__ import annotations
from datetime import date
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
import argparse
import json
import shutil
import struct
import subprocess
import zipfile
import zlib

import manju
from manju.authoring.core import Request, file_digest, verify_bundle
from manju.authoring.quality import quality_plan
from manju.authoring.director import verify_director_bundle, png_dimensions
from playwright.sync_api import sync_playwright, expect


def synthetic_png(w: int, h: int) -> bytes:
    def chunk(kind, data):
        return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data)&0xffffffff)
    pixels = b''.join(b'\0' + bytes([y%256, 140, 210])*w for y in range(h))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB', w,h,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b''))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args(); root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    html=Path(str(files('manju.authoring').joinpath('data/workbench.html')))
    source=root/'运动底片_合成.mp4'; guide=root/'目标图_原生1920x1080.png'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
        'testsrc2=s=960x540:r=24','-f','lavfi','-i','sine=f=440:r=48000','-t','3',
        '-c:v','libx264','-threads','1','-preset','ultrafast','-pix_fmt','yuv420p',
        '-c:a','aac',str(source)],check=True,capture_output=True,timeout=25)
    guide.write_bytes(synthetic_png(1920,1080))
    original=file_digest(source);guide_hash=file_digest(guide)
    errors, network, contexts=[],[],[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,
                                  args=['--no-sandbox'],timeout=15000)
        def fresh():
            context=browser.new_context(accept_downloads=True,viewport={'width':1365,'height':1000})
            contexts.append(context);page=context.new_page();page.set_default_timeout(15000)
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:network.append(r.url))
            page.set_content(html.read_text(encoding='utf-8'),wait_until='load')
            return page
        page=fresh(); assert page.locator('#quality-only').is_checked()
        page.locator('#shot-id').fill('R12_顶尖模型交接_合成')
        page.locator('#prompt').fill('固定镜头，保持空间连续性。合成验收，不是实际商业生成。')
        page.locator('#duration').fill('5'); page.locator('#resolution').select_option('1080p')
        page.locator('#ratio').select_option('16:9')
        request=Request.model_validate(page.evaluate('ManjuWorkbench.getRequest()'))
        page.locator('#check-plan').click()
        expect(page.locator('input[type=radio][value="wan3-standard/text"]')).to_be_visible()
        assert page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).count()==0
        assert page.evaluate('ManjuWorkbench.state.selected') is None
        with page.expect_download() as item:page.locator('#export-quality').click()
        evidence=root/'QUALITY_EVIDENCE.json';item.value.save_as(evidence)
        recorded=json.loads(evidence.read_text(encoding='utf-8'))
        assert recorded['report']==quality_plan(request,today=date.fromisoformat(recorded['report']['evaluated_on']))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#quality-summary').locator('..').screenshot(path=str(root/'quality-narrow.png'))
        page.set_viewport_size({'width':1365,'height':1000})
        # This confirmation is performed on synthetic test fixtures only.
        page.locator('input[type=radio][value="wan3-standard/text"]').check()
        page.locator('#reviewer').fill('合成验收，不是用户批准')
        page.locator('#human-confirmed').check();page.locator('#ack-warnings').check()
        with page.expect_download() as item:page.locator('#export-bundle').click()
        model_bundle=root/'MODEL_HANDOFF.zip';item.value.save_as(model_bundle)
        with zipfile.ZipFile(model_bundle) as archive:
            assert archive.testzip() is None
            # Generated closed inventory verified again by Python below.
            archive.extractall(root/'model-handoff')
        model_result=verify_bundle(root/'model-handoff');assert model_result['ok']
        untouched=page.evaluate('ManjuWorkbench.getRequest()')
        page.locator('#director-shot').fill('R12_运动先行_合成示例')
        page.locator('#director-source').set_input_files(source)
        expect(page.locator('#director-status')).to_contain_text('底片已在本机绑定')
        page.wait_for_function('()=>document.getElementById("director-video").readyState>=2')
        for n,seconds in enumerate((0.5,1.5),1):
            page.evaluate('s=>{const v=document.getElementById("director-video");v.pause();v.currentTime=s;}',seconds)
            page.wait_for_function('()=>!document.getElementById("director-video").seeking&&document.getElementById("director-video").readyState>=2')
            page.locator('#director-capture').click()
            page.wait_for_function('n=>ManjuDirector.state.anchors.length===n&&!ManjuDirector.state.busy',arg=n)
        page.get_by_label('A01 目标外观',exact=True).fill('该时刻呈现蓝色服装，保留原来的表演与镜头。这里只是合成演练。')
        page.get_by_label('A01 目标 PNG',exact=True).set_input_files(guide)
        page.wait_for_function('()=>ManjuDirector.state.anchors[0].guide!==null&&!ManjuDirector.state.busy')
        page.get_by_label('A02 目标外观',exact=True).fill('此处保留未完成目标文字，演示保存草稿；没有绑定目标图。')
        page.locator('#director-preserve').fill('运动时序\n镜头节奏')
        page.locator('#director-change').fill('服装外观')
        with page.expect_download() as item:page.locator('#director-export').click()
        director=root/'DIRECTOR.zip';item.value.save_as(director)
        result=verify_director_bundle(director);assert result['ok']
        assert result['anchors']==2 and result['guides']==1
        with zipfile.ZipFile(director) as archive:
            plan=json.loads(archive.read('PLAN.json'))
            assert [a['source_time_ms'] for a in plan['anchors']]==[500,1500]
            assert not plan['execution_authorized'] and not plan['automatic_selection']
            for name in archive.namelist():
                if name.startswith('source/'):assert sha256(archive.read(name)).hexdigest()==original
                if name.startswith('guides/'):
                    assert sha256(archive.read(name)).hexdigest()==guide_hash
                    assert png_dimensions(archive.read(name))==(1920,1080)
                if name.startswith('frames/'):
                    assert png_dimensions(archive.read(name))==(960,540)
                    # A full PNG decode is test evidence, not the verifier's contract.
                    from PIL import Image
                    from io import BytesIO
                    with Image.open(BytesIO(archive.read(name))) as image:image.load();assert image.size==(960,540)
        assert page.evaluate('ManjuWorkbench.getRequest()')==untouched
        page.locator('#director-section').screenshot(path=str(root/'director-desktop.png'))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#director-section').screenshot(path=str(root/'director-narrow.png'))
        page.close();page=fresh();page.locator('#prompt').fill('导演恢复不应覆盖这个新任务。')
        other_request=page.evaluate('ManjuWorkbench.getRequest()')
        page.on('dialog',lambda d:d.accept())
        page.locator('#director-import').set_input_files(director)
        expect(page.locator('#director-status')).to_contain_text('原片、锚点和目标图已恢复')
        assert page.evaluate('ManjuWorkbench.getRequest()')==other_request
        assert page.get_by_label('A02 目标外观',exact=True).input_value().startswith('此处保留未完成')
        assert page.evaluate('ManjuDirector.state.anchors[0].guide.width')==1920
        page.evaluate('()=>document.getElementById("director-video").play()')
        page.wait_for_function('()=>document.getElementById("director-video").currentTime>0.2')
        page.evaluate('()=>document.getElementById("director-video").pause()')
        with page.expect_download() as item:page.locator('#director-export').click()
        restored=root/'DIRECTOR_RESTORED.zip';item.value.save_as(restored)
        assert restored.read_bytes()==director.read_bytes()
        assert not errors,errors
        external=[u for u in network if u.startswith(('http:','https:'))];assert not external,external
        assert file_digest(source)==original and file_digest(guide)==guide_hash
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-i',str(source),'-f','null','-'],check=True,capture_output=True,timeout=20)
        report={'ok':True,'package_import':str(Path(manju.__file__).resolve()),'package_version':manju.__version__,
            'html_sha256':file_digest(html),'source_sha256':original,'source_pixels':[960,540],
            'guide_sha256':guide_hash,'guide_pixels':[1920,1080],'source_and_guide_unchanged':True,
            'native_frame_dimensions_preserved':True,'higher_pixel_guide_not_downsampled':True,
            'director':result,'director_zip_sha256':file_digest(director),'director_reexport_byte_identical':True,
            'director_restored_played_without_rebinding':True,'browser_restore_reprobed_source_metadata':True,
            'separate_main_request_unchanged':True,'incomplete_anchor_preserved_as_draft':True,
            'source_full_video_decode_passed':True,'captured_png_full_decode_passed':True,
            'quality_evidence_browser_python_identical':True,'quality_default_enabled':True,
            'default_profile_selection':None,'legacy_veo_option_hidden_by_default':True,
            'model_handoff':model_result,'commercial_generation_calls':0,
            'actual_browser_downloads_saved':4,'browser_version':browser.version,'page_errors':errors,'http_requests':0,
            'transport':'isolated_document','native_file_navigation_certified':False,
            'motion_video_editing_executed':False,'image_model_generation_executed':False,
            'native_hdr_or_frame_index_claimed':False,'synthetic_fixture_not_user_approval':True}
        (root/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
        for context in contexts:context.close()
        browser.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
