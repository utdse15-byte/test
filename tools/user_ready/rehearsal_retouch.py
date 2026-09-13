"""Real-media offline image roundtrip on exact source or installed HTML.

Native navigation is separately probed. This sandbox run uses set_content on
unaltered application bytes, not a Windows double-click certification.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import shutil
import struct
import subprocess
import zlib
import zipfile

import manju
from manju.authoring.retouch import verify_kit, normalize_task
from manju.authoring.director import verify_director_bundle
from manju.authoring.desk import verify_desk
from playwright.sync_api import sync_playwright, expect


def png(w,h,value):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes([value,190,220])*w)*h))+chunk(b'IEND',b'')


def digest(p):return sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--legacy-html',type=Path,required=True)
    args=parser.parse_args();root=args.output.absolute();root.mkdir(parents=True,exist_ok=False)
    video=root/'运动底片.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                    'testsrc2=size=640x360:rate=12:duration=2','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,timeout=30)
    before_video=digest(video);errors=[]
    html=files('manju.authoring').joinpath('data/workbench.html').read_text(encoding='utf-8')
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        ctx=browser.new_context(accept_downloads=True,viewport={'width':1240,'height':960})
        def fresh(text=html):
            page=ctx.new_page();page.set_default_timeout(20000);page.on('pageerror',lambda e:errors.append(str(e)));page.set_content(text,wait_until='load');page.evaluate('window.ManjuExperience?.showAll({scroll:false})');return page
        page=fresh();page.locator('#prompt').fill('上方独立任务：保持原稿，不因图片精修而改写。')
        request_before=page.evaluate('ManjuWorkbench.getRequest()')
        page.locator('#director-shot').fill('精修示例_双时刻')
        page.locator('#director-preserve').fill('原构图与表演动作\n画面中文字内容')
        page.locator('#director-change').fill('背景光线')
        page.locator('#director-source').set_input_files(video)
        page.wait_for_function('()=>ManjuDirector.state.entry!==null&&!ManjuDirector.state.busy')
        page.wait_for_function('()=>document.querySelector("#director-video").readyState>=2')
        for index,seconds in enumerate((0.25,1.25),1):
            page.evaluate('s=>{let v=document.querySelector("#director-video");v.pause();v.currentTime=s;}',seconds)
            page.wait_for_function('()=>!document.querySelector("#director-video").seeking&&document.querySelector("#director-video").readyState>=2')
            page.locator('#director-capture').click();page.wait_for_function('()=>!ManjuDirector.state.busy')
            page.get_by_label(f'A{index:02d} 目标外观',exact=True).fill('背景呈现柔和蓝色光线；不修改构图、动作和文字。这是合成演练要求。')
        with page.expect_download() as e:page.locator('#director-export').click()
        original=root/'DIRECTOR_BEFORE.zip';e.value.save_as(original);verify_director_bundle(original)
        page.locator('#retouch-section').evaluate('e=>e.open=true');page.locator('#retouch-route').select_option('openai-sunburst-20260908')
        with page.expect_download() as e:page.locator('#retouch-export').click()
        kit=root/'RETOUCH_KIT.zip';e.value.save_as(kit);report=verify_kit(kit)
        with zipfile.ZipFile(kit) as z:
            task_path=root/'TASK.json';task_path.write_bytes(z.read('TASK.json'))
            task=normalize_task(json.loads(z.read('TASK.json')))
            assert not any(n.lower().endswith(('.mp4','.mov')) for n in z.namelist())
        outputs=[]
        for index,job in enumerate(task['jobs']):
            out=root/job['output_name'];out.write_bytes(png(1920,1080,40+index*30));outputs.append(out)
        # Route through the real home intake, not direct assignment.
        page.locator('#intake-files').set_input_files(task_path);expect(page.locator('#intake-preview')).to_be_visible()
        page.locator('#intake-open').click();expect(page.locator('#retouch-status')).to_contain_text('已载入')
        for out in outputs:
            page.locator('#retouch-outputs').set_input_files(out);page.locator('#retouch-preview').click()
            expect(page.locator('#retouch-return-preview')).to_be_visible()
            if out==outputs[0]:
                assert all(a['guide'] is None for a in page.evaluate('ManjuDirector.state.anchors'))
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.locator('#retouch-section').screenshot(path=str(root/'retouch-preview-narrow.png'))
                page.set_viewport_size({'width':1240,'height':960})
            page.locator('#retouch-confirm').check();page.locator('#retouch-apply').click()
            expect(page.locator('#retouch-status')).to_contain_text('已接回 1')
        # Repeated same result is a no-op, not a duplicate new candidate.
        page.locator('#retouch-outputs').set_input_files(outputs[0]);page.locator('#retouch-preview').click()
        expect(page.locator('#retouch-return-preview')).to_contain_text('不重复替换')
        page.locator('#retouch-confirm').check();page.locator('#retouch-apply').click()
        expect(page.locator('#retouch-status')).to_contain_text('已接回 0')
        assert page.evaluate('ManjuWorkbench.getRequest()')==request_before
        assert digest(video)==before_video
        with page.expect_download() as e:page.locator('#director-export').click()
        final=root/'DIRECTOR_AFTER.zip';e.value.save_as(final);final_report=verify_director_bundle(final)
        with zipfile.ZipFile(final) as z:
            for out in outputs:assert z.read('guides/'+digest(out)+'.png')==out.read_bytes()
        with page.expect_download() as e:page.locator('#desk-save').click()
        checkout=root/'CHECKOUT_AFTER.zip';e.value.save_as(checkout);desk_report=verify_desk(checkout)
        page.locator('#desk-verify-file').set_input_files(checkout)
        # Legacy package reads exact unchanged Director/v1, then exports identical bytes.
        legacy=fresh(args.legacy_html.read_text(encoding='utf-8'))
        legacy.on('dialog',lambda d:d.accept())
        legacy.locator('#director-import').set_input_files(final)
        expect(legacy.locator('#director-status')).to_contain_text('已恢复')
        legacy.evaluate('()=>document.querySelector("#director-video").play()')
        legacy.wait_for_function('()=>document.querySelector("#director-video").currentTime>0.05')
        with legacy.expect_download() as e:legacy.locator('#director-export').click()
        legacy_saved=root/'LEGACY_DIRECTOR.zip';e.value.save_as(legacy_saved);assert legacy_saved.read_bytes()==final.read_bytes()
        ctx.close();browser.close()
    assert not errors,errors
    result={'ok':True,'version':manju.__version__,'runtime':str(Path(manju.__file__).resolve()),
            'source_video_sha256':before_video,'task_sha256':task['task_sha256'],
            'kit_sha256':digest(kit),'director_after_sha256':digest(final),'checkout_after_sha256':digest(checkout),
            'original_video_unchanged':True,'two_partial_returns':True,'idempotent_repeat':True,
            'native_target_size':[1920,1080],'target_bytes_unchanged':True,'unrelated_request_unchanged':True,
            'legacy_director_roundtrip_identical':True,'legacy_html_sha256':digest(args.legacy_html),'actual_browser_downloads':True,
            'isolated_document':True,'native_navigation':False,'windows_verified':False,
            'commercial_generation_executed':False,'kit_verification':report,'director_verification':final_report,'desk_verification':desk_report,
            'javascript_errors':errors}
    (root/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
