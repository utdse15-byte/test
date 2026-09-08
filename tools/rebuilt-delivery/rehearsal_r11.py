"""Real synthetic media, scoped-repair download/reopen and explicit raster checks.

Loads the imported package's HTML by default, so an isolated installed wheel can
exercise the same flow without source-tree imports. Original HTML is transported
via set_content because this sandbox forbids file navigation, not as a claim of
Windows file:// or double-click acceptance. Never calls a generation provider.
"""
from pathlib import Path
from importlib.resources import files
from hashlib import sha256
import argparse
import json
import shutil
import subprocess
import zipfile

import manju
from playwright.sync_api import sync_playwright, expect
from manju.authoring.core import Request, file_digest
from manju.authoring.repair import verify_repair_bundle
from manju.authoring.returns import inspect_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--html', type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    html = args.html or Path(str(files('manju.authoring').joinpath('data/workbench.html')))
    source = root / '原片_宽银幕.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=1584x672:r=24', '-f', 'lavfi', '-i', 'sine=f=440:r=48000',
                    '-t', '6', '-c:v', 'libx264', '-threads', '1', '-preset', 'ultrafast',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)],
                   check=True, capture_output=True, timeout=25)
    original_hash = file_digest(source)
    errors, requests, contexts = [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=shutil.which('chromium'), headless=True,
                                     args=['--no-sandbox'], timeout=15000)
        def fresh_page():
            context = browser.new_context(accept_downloads=True, viewport={'width':1365, 'height':1000})
            contexts.append(context)
            page = context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('request', lambda r: requests.append(r.url))
            page.set_content(html.read_text(encoding='utf-8'), wait_until='load')
            page.set_default_timeout(10000)
            return page
        page = fresh_page()
        page.locator('#shot-id').fill('R11_局部返工合成验收')
        page.locator('#prompt').fill('合成图案测试，保持连续镜头。不是用户的真实作品或批准。')
        page.locator('#duration').fill('6')
        page.locator('#resolution').select_option('720p')
        page.locator('#ratio').select_option('21:9')
        main_request = page.evaluate('ManjuWorkbench.getRequest()')
        page.locator('#repair-source').set_input_files(source)
        expect(page.locator('#repair-status')).to_contain_text('原片已绑定')
        values={'start':'1.201', 'end':'2.444', 'before':'0.3', 'after':'0.6',
                'preserve':'图案位置\n范围外画面\n原声音', 'change':'此处只作为修改要求的合成测试',
                'human':'合成验收，不是用户审片'}
        for key, value in values.items():
            page.locator('#repair-'+key).fill(value)
        page.locator('#repair-confirmed').check()
        page.locator('#repair-preview').click()
        page.wait_for_function('()=>document.getElementById("repair-video").currentTime>0.95')
        page.evaluate('()=>document.getElementById("repair-video").pause()')
        with page.expect_download() as download:
            page.locator('#repair-export').click()
        bundle = root/'REPAIR.zip'
        download.value.save_as(bundle)
        repair_result = verify_repair_bundle(bundle)
        assert repair_result['source_sha256'] == original_hash
        with zipfile.ZipFile(bundle) as archive:
            plan = json.loads(archive.read('PLAN.json'))
        assert plan['start_ms']==1201 and plan['end_ms']==2444
        assert repair_result['context_window']['start_ms']==901
        page.locator('#repair-section summary').click()
        page.locator('#repair-section').screenshot(path=str(root/'repair-desktop.png'))
        page.set_viewport_size({'width':390, 'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#repair-section').screenshot(path=str(root/'repair-narrow.png'))
        page.close()
        page = fresh_page()
        page.locator('#prompt').fill('重新打开返工不可覆盖这个不同的新任务')
        untouched = page.evaluate('ManjuWorkbench.getRequest()')
        page.on('dialog', lambda d: d.accept())
        page.locator('#repair-import').set_input_files(bundle)
        expect(page.locator('#repair-status')).to_contain_text('已恢复返工原片')
        assert page.evaluate('ManjuWorkbench.getRequest()') == untouched
        assert page.locator('#repair-start').input_value() == '1.201'
        assert not page.locator('#repair-confirmed').is_checked()
        page.locator('#repair-preview').click()
        page.wait_for_function('()=>document.getElementById("repair-video").currentTime>0.95')
        page.evaluate('()=>document.getElementById("repair-video").pause()')
        # Restore the synthetic request explicitly for comparing the two check policies.
        page.evaluate('q=>ManjuWorkbench.fillRequest(q)', main_request)
        page.locator('#review-files').set_input_files(source)
        page.wait_for_function('()=>ManjuReview.state.pending.length===1&&!ManjuReview.state.busy')
        assert not page.evaluate('ManjuReview.state.document')
        page.locator('#return-section > summary').click()
        with page.expect_download() as download:
            page.locator('#export-returns').click()
        legacy_path = root/'RETURN_V1.json'
        download.value.save_as(legacy_path)
        legacy = json.loads(legacy_path.read_text())
        assert legacy['schema_id']=='manju.return-preflight/v1'
        assert legacy['items'][0]['status']=='needs_attention'
        page.locator('#return-runway-raster').click()
        assert page.locator('#return-width').input_value()=='1584'
        assert page.locator('#return-height').input_value()=='672'
        with page.expect_download() as download:
            page.locator('#export-returns').click()
        exact_path=root/'RETURN_V2.json'
        download.value.save_as(exact_path)
        exact=json.loads(exact_path.read_text())
        assert exact['schema_id']=='manju.return-preflight/v2'
        assert exact['items'][0]['status']=='metadata_matches_requested_checks'
        assert exact['request_sha256']==legacy['request_sha256']
        assert not exact['automatic_approval'] and not exact['automatic_selection']
        req=Request.model_validate(main_request)
        decoded=inspect_files(req,[source],decode=True,expected_pixels=(1584,672))
        assert decoded['items'][0]['full_video_decode']=='passed'
        assert decoded['items'][0]['status']=='metadata_matches_requested_checks'
        (root/'FULL_DECODE.json').write_text(json.dumps(decoded,ensure_ascii=False,indent=2)+'\n')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#return-section').screenshot(path=str(root/'return-narrow.png'))
        assert page.evaluate('ManjuWorkbench.getRequest()')==main_request
        assert not page.evaluate('ManjuReview.state.document')
        assert not errors, errors
        assert not [u for u in requests if u.startswith(('http:', 'https:'))], requests
        assert file_digest(source)==original_hash
        report={'ok':True, 'package_import':str(Path(manju.__file__).resolve()),
                'package_version':manju.__version__, 'html':str(html), 'html_sha256':file_digest(html),
                'source_sha256':original_hash, 'source_unchanged':True, 'source_pixels':[1584,672],
                'repair':repair_result, 'repair_zip_sha256':file_digest(bundle),
                'repair_restored_and_played_without_rebinding':True,
                'separate_main_request_unchanged_on_repair_import':True,
                'current_repair_confirmation_cleared':True, 'request_v1_hash_preserved':True,
                'legacy_v1_kept':True, 'explicit_raster_v2_matches':True, 'full_video_decode_passed':True,
                'review_decisions_created':0, 'commercial_calls':0, 'actual_browser_downloads_saved':3,
                'page_errors':errors, 'http_requests':0, 'browser_version':browser.version,
                'transport':'isolated_document', 'file_navigation_certified':False,
                'frame_accurate_editing_executed':False, 'ai_video_editing_executed':False}
        (root/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        for context in contexts:
            context.close()
        browser.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
