"""Installed/read-only GUI task export plus actual browser import/download.

Browser policy prevents direct localhost/file navigation in this environment;
HTTP responses are fetched normally and the exact HTML is mounted as an isolated
document. This is not Windows/native-navigation acceptance.
"""
from pathlib import Path
import json,sys,threading,urllib.request
from playwright.sync_api import sync_playwright
import manju
from manju.core.container import Project
from manju.gui.server import create_server
from manju.authoring.core import file_digest,Request,digest

root=Path(sys.argv[1]);root.mkdir(exist_ok=False)
project=Project(Path(sys.argv[2]))
server=create_server(project,host='127.0.0.1',port=0,actor='human',readonly=True)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
def hashes():return {f.relative_to(project.root).as_posix():file_digest(f) for f in project.root.rglob('*') if f.is_file()}
def get(path):
 with urllib.request.urlopen(f'http://127.0.0.1:{server.port}'+path,timeout=10) as r:return r.read(),dict(r.headers)
before=hashes()
try:
 html,headers=get('/model-workbench');data,download_headers=get('/api/model-authoring/shot?shot=S001')
 task=root/'read-only-shot.json';task.write_bytes(data);doc=json.loads(data)
 original=Request.model_validate(doc['request']);assert doc['project_modified'] is False
 assert "connect-src 'none'" in headers['Content-Security-Policy']
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  page=browser.new_page(accept_downloads=True,viewport={'width':1365,'height':1000});errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
  page.set_content(html.decode(),wait_until='load')
  assert page.locator('#project-model-tools').is_visible()
  page.locator('#import-request').set_input_files(task)
  page.wait_for_function('()=>document.getElementById("shot-id").value==="S001"')
  with page.expect_download() as event:page.locator('#export-request').click()
  exported=root/'browser-exported-request.json';event.value.save_as(exported)
  value=json.loads(exported.read_text());request=Request.model_validate(value.get('request',value))
  assert digest(request)==digest(original)
  page.screenshot(path=str(root/'project-tools-desktop.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(root/'project-tools-mobile.png'),full_page=True)
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  assert not errors,errors
  report={'ok':True,'runtime_version':manju.__version__,'runtime_from_isolated_install':'r7-install-check/site' in manju.__file__,
    'read_only_real_http_export':True,'http_content_disposition':download_headers['Content-Disposition'],
    'project_files_unchanged':hashes()==before,'project_files_compared':len(before),
    'browser_task_import_and_download_passed':True,'request_sha256':digest(request),
    'export_file_sha256':file_digest(exported),'browser':browser.version,'page_errors':errors,
    'native_navigation_tested':False,'transport':'real local HTTP followed by isolated browser document',
    'commercial_api_calls':0,'automatic_selection_or_approval':False}
  assert report['runtime_from_isolated_install'] and report['project_files_unchanged']
  (root/'RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False,indent=2));browser.close()
finally:
 server.shutdown();server.close();thread.join(timeout=5)
