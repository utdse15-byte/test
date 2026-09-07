"""Actual local video review, human test declarations, browser ZIP, Python verification."""
from pathlib import Path
import json,sys,subprocess,zipfile
from playwright.sync_api import sync_playwright
from manju.authoring.core import file_digest,verify_bundle,load_json
from manju.review.core import ReviewDocument
root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
repo=Path(__file__).resolve().parents[2];html=repo/'tools/model_workbench.html'
clips=[]
for i,filter_ in enumerate(['testsrc2=s=640x360:r=24','smptebars=s=640x360:r=24']):
 clip=root/f'合成候选{i+1}.mp4'
 subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',filter_,'-t','2','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(clip)],check=True,capture_output=True,timeout=20)
 clips.append(clip)
before=[file_digest(c) for c in clips]
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 context=b.new_context(accept_downloads=True,viewport={'width':1365,'height':1000});page=context.new_page();errors=[];requests=[]
 page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
 page.set_content(html.read_text(),wait_until='load')
 page.locator('#shot-id').fill('试镜：保持连续运动')
 page.locator('#prompt').fill('合成流程演练：比较运动连续性，固定镜头，不改变主体。')
 page.locator('#review-files').set_input_files(clips)
 page.wait_for_function('()=>ManjuReview.state.pending.length===2&&!ManjuReview.state.busy')
 page.locator('#create-review').click();page.wait_for_function('()=>!!ManjuReview.state.document')
 page.locator('#play-review').click();page.wait_for_function('()=>Array.from(document.querySelectorAll("#candidate-grid video")).every(v=>v.currentTime>0)')
 page.locator('#pause-review').click()
 chosen=page.evaluate('ManjuReview.state.document.session.blind_order[0]')
 page.locator('#review-candidate').select_option(chosen);page.locator('#review-verdict').select_option('approve_draft')
 page.locator('#review-human').fill('合成演练，不是用户实际批准')
 page.locator('#review-notes').fill('测试图案可播放且连续；本次只验收审片记录和定稿封包，不评价商业模型画质。')
 page.locator('#review-score').select_option('4');page.locator('#review-confirmed').check();page.locator('#record-review').click()
 page.wait_for_function('()=>ManjuReview.state.document.decisions.length===1')
 with page.expect_download() as info:page.locator('#export-review').click()
 review=root/'review.json';info.value.save_as(review)
 doc=ReviewDocument.model_validate(load_json(review));assert not doc.decisions[0].picture_lock_authorized
 page.locator('#review-section').screenshot(path=str(root/'review-desktop.png'))
 page.set_viewport_size({'width':390,'height':844});page.locator('#review-section').screenshot(path=str(root/'review-mobile.png'))
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.set_viewport_size({'width':1365,'height':1000})
 page.locator('#promotion-resolution').fill('1080p');page.locator('#promotion-confirmed').check();page.locator('#promote-review').click()
 page.wait_for_function('()=>ManjuWorkbench.state.stage==="final"')
 page.locator('#check-plan').click();page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).check()
 page.locator('#reviewer').fill('离线合成验收');page.locator('#human-confirmed').check();page.locator('#ack-warnings').check()
 with page.expect_download(timeout=20000) as info:page.locator('#export-bundle').click()
 archive=root/'verified-final-handoff.zip';info.value.save_as(archive)
 with zipfile.ZipFile(archive) as z:
  assert z.testzip() is None;z.extractall(root/'verified-final-handoff')
 result=verify_bundle(root/'verified-final-handoff',require_promotion=True)
 assert [file_digest(c) for c in clips]==before
 assert not errors,errors
 assert not [u for u in requests if u.startswith(('http:','https:'))],requests
 result.update(browser=b.version,html_sha256=file_digest(html),source_hashes_unchanged=True,
               archive_sha256=file_digest(archive),actual_browser_download_saved=True,commercial_api_calls=0,
               page_errors=errors,transport='isolated_document; native file navigation blocked by sandbox policy',
               native_windows_double_click_verified=False,review_document_sha256=file_digest(review))
 (root/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False));b.close()
