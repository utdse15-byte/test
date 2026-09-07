"""Real media, browser downloads, whole-workspace restore and R10 model handoff.

All approvals are clearly synthetic fixture actions, not the user's decisions.
Optional HTML permits exercising the separately built/installed artifact.
"""
from pathlib import Path
from hashlib import sha256
import argparse,json,subprocess,zipfile
from playwright.sync_api import sync_playwright,expect
from manju.authoring.core import file_digest,verify_bundle,Request
from manju.authoring.workspace import verify_workspace
from manju.authoring.returns import inspect_files

p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--html',type=Path);a=p.parse_args()
root=a.output;root.mkdir(parents=True,exist_ok=False)
repo=Path(__file__).resolve().parents[2];html=a.html or repo/'tools/model_workbench.html'
clips=[]
for i,filter_ in enumerate(['testsrc2=s=1280x720:r=24','smptebars=s=1280x720:r=24']):
 clip=root/f'合成候选{i+1}.mp4'
 subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',filter_,'-t','2','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(clip)],check=True,capture_output=True,timeout=30)
 clips.append(clip)
before=[file_digest(c) for c in clips]
with sync_playwright() as pw:
 browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 contexts=[];errors=[];requests=[]
 def fresh_page():
  context=browser.new_context(accept_downloads=True,viewport={'width':1365,'height':1000});contexts.append(context);page=context.new_page()
  page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url));page.set_content(html.read_text(),wait_until='load');return page
 page=fresh_page();page.locator('#shot-id').fill('R10实际闭环');page.locator('#prompt').fill('保持固定镜头，连续运动；合成测试，不代表用户作品。')
 page.locator('#duration').fill('2');page.locator('#resolution').select_option('720p');page.locator('#ratio').select_option('16:9')
 page.locator('#review-files').set_input_files(clips);page.wait_for_function('()=>ManjuReview.state.pending.length===2&&!ManjuReview.state.busy')
 page.locator('#create-review').click();page.wait_for_function('()=>!!ManjuReview.state.document')
 page.locator('#play-review').click();page.wait_for_function('()=>[...document.querySelectorAll("#candidate-grid video")].every(v=>v.currentTime>0)');page.locator('#pause-review').click()
 chosen=page.evaluate('ManjuReview.state.document.session.blind_order[0]')
 page.locator('#review-candidate').select_option(chosen);page.locator('#review-verdict').select_option('approve_draft');page.locator('#review-human').fill('合成验收，不是用户批准')
 page.locator('#review-notes').fill('测试图案的人工测试记录，不是商业画质评价。');page.locator('#review-confirmed').check();page.locator('#record-review').click();page.wait_for_function('()=>ManjuReview.state.document.decisions.length===1')
 page.locator('#review-notes').fill(' 尚未提交的审片补充\n ')
 with page.expect_download() as info:page.locator('#export-workspace').click()
 archive=root/'WORKSPACE.zip';info.value.save_as(archive);workspace=verify_workspace(archive)
 assert workspace['media_files']==2 and workspace['review_decisions']==1
 saved=page.evaluate('ManjuWorkspace.workspaceView()')
 page.close();page=fresh_page();page.on('dialog',lambda d:d.accept())
 page.locator('#import-workspace').set_input_files(archive);expect(page.locator('#workspace-status')).to_contain_text('已恢复 2 个实际文件')
 assert page.evaluate('ManjuWorkspace.workspaceView()')==saved
 assert not any(page.locator('#'+i).is_checked() for i in ['human-confirmed','ack-warnings','review-confirmed','promotion-confirmed'])
 assert page.evaluate('ManjuWorkbench.state.selected') is None
 page.locator('#play-review').click();page.wait_for_function('()=>[...document.querySelectorAll("#candidate-grid video")].every(v=>v.currentTime>0)');page.locator('#pause-review').click()
 # The restored actual files, not filenames or stale media metadata, are inspected again.
 page.locator('#return-section > summary').click()
 with page.expect_download() as info:page.locator('#export-returns').click()
 returns=root/'RETURN_PREFLIGHT.json';info.value.save_as(returns);r=json.loads(returns.read_text())
 assert all(x['status']=='metadata_matches_requested_checks' for x in r['items'])
 assert not r['automatic_approval'] and not r['picture_lock_authorized']
 req=Request.model_validate(r['request']);decoded=inspect_files(req,clips,decode=True)
 (root/'RETURN_FULL_DECODE.json').write_text(json.dumps(decoded,ensure_ascii=False,indent=2))
 assert all(x['full_video_decode']=='passed' for x in decoded['items'])
 # Preview an older capability catalog, then deliberately leave it unapplied.
 page.locator('#import-catalog').set_input_files(repo/'src/manju/authoring/data/catalog_r7.json');expect(page.locator('#catalog-summary')).to_contain_text('字段变化')
 assert 'r10' in page.evaluate('ManjuWorkbench.catalog().revision')
 with page.expect_download() as info:page.locator('#catalog-save-review').click()
 catalog_report=root/'CATALOG_REVIEW.json';info.value.save_as(catalog_report)
 page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(root/'workspace-narrow.png'),full_page=True);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.set_viewport_size({'width':1365,'height':1000})
 page.locator('#review-candidate').select_option(chosen);page.locator('#promotion-resolution').fill('720p');page.locator('#promotion-confirmed').check();page.locator('#promote-review').click();page.wait_for_function('()=>ManjuWorkbench.state.stage==="final"')
 page.locator('#check-plan').click();page.get_by_role('radio',name='Runway Gen-4.5 网页工作流 text',exact=True).check()
 page.locator('#reviewer').fill('R10 离线合成验收');page.locator('#human-confirmed').check();page.locator('#ack-warnings').check()
 with page.expect_download(timeout=20000) as info:page.locator('#export-bundle').click()
 finalzip=root/'FINAL_HANDOFF.zip';info.value.save_as(finalzip)
 with zipfile.ZipFile(finalzip) as z:assert z.testzip() is None;z.extractall(root/'FINAL_HANDOFF')
 handoff=verify_bundle(root/'FINAL_HANDOFF',require_promotion=True)
 assert not errors,errors
 assert not [u for u in requests if u.startswith(('http:','https:'))],requests
 assert [file_digest(c) for c in clips]==before
 report={'ok':True,'html_sha256':file_digest(html),'workspace':workspace,'workspace_sha256':file_digest(archive),'whole_working_view_restored_exactly':True,'restored_media_played_without_rebinding':True,'unsent_review_notes_restored':True,'current_confirmations_cleared':True,'return_specifications_match':True,'full_video_decode_passed':True,'source_media_unchanged':True,'catalog_import_not_automatically_applied':True,'final_handoff':handoff,'final_zip_sha256':file_digest(finalzip),'actual_browser_downloads_saved':4,'commercial_calls':0,'browser':browser.version,'transport':'isolated_document','windows_file_double_click_verified':False,'page_errors':errors}
 (root/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2))
 for c in contexts:c.close()
 browser.close()
