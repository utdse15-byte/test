"""Real browser interaction/download, with an explicit sandbox transport mode."""
from pathlib import Path
import sys,json,zipfile
from playwright.sync_api import sync_playwright
from manju.authoring.core import verify_bundle,file_digest
root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
repo=Path(__file__).resolve().parents[2]
html=repo/'tools/model_workbench.html'
images=repo/'tests/fixtures/authoring_r4/assets'
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
 context=b.new_context(accept_downloads=True,viewport={'width':1365,'height':1000})
 page=context.new_page();requests=[];errors=[]
 page.on('request',lambda r:requests.append(r.url));page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(html.read_text(),wait_until='load')
 page.locator('#shot-id').fill('雨后首尾桥接')
 page.locator('#task').select_option('bridge');page.locator('#prompt').fill('在一个连续镜头中，从首帧自然过渡到尾帧，保留构图和主体，缓慢改变光线。')
 page.locator('#resolution').select_option('768P');page.locator('#ratio').select_option('adaptive')
 page.locator('#preserve').fill('主体身份\n镜头构图');page.locator('#change').fill('光线变化')
 for role,name in [('first_frame','首帧.png'),('last_frame','尾帧.png')]:
  page.locator('#asset-role').select_option(role);page.locator('#asset-files').set_input_files(images/name)
  page.wait_for_function('!ManjuWorkbench.state.busy')
  page.wait_for_function('(n)=>ManjuWorkbench.state.assets.length===n',arg=1 if role=='first_frame' else 2)
 page.locator('#check-plan').click();page.get_by_role('radio',name='MiniMax H3 first-last',exact=True).check()
 page.locator('#reviewer').fill('合成媒体演练，不是用户审批');page.locator('#human-confirmed').check();page.locator('#ack-warnings').check()
 with page.expect_download(timeout=20000) as info:page.locator('#export-bundle').click()
 archive=root/'browser-handoff.zip';info.value.save_as(archive)
 with zipfile.ZipFile(archive) as z:
  assert z.testzip() is None;z.extractall(root/'verified-handoff')
 verified=verify_bundle(root/'verified-handoff')
 page.screenshot(path=str(root/'desktop.png'),full_page=True)
 page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(root/'mobile.png'),full_page=True)
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 assert not errors,errors
 assert not [u for u in requests if u.startswith(('http://','https://'))],requests
 result={**verified,'browser':b.version,'html_sha256':file_digest(html),'download_sha256':file_digest(archive),
         'zip_crc_passed':True,'actual_browser_download_saved':True,'network_requests':0,'page_errors':errors,
         'transport':'isolated_document: exact HTML bytes; file:// navigation blocked by sandbox policy',
         'windows_double_click_verified':False}
 (root/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(result,ensure_ascii=False))
 b.close()
