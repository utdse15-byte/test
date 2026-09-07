from __future__ import annotations
from copy import deepcopy
from pathlib import Path
import importlib.util
import os
import json
import shutil
import zipfile
import pytest
from typer.testing import CliRunner
from manju.authoring.core import Request, load_catalog, plan, digest, verify_bundle, file_digest
from manju.authoring.project_bridge import from_shot
from manju.authoring.cli import app

REPO=Path(__file__).resolve().parents[1]
PAGE=REPO/'tools/model_workbench.html'


def test_project_bridge_read_only(tmp_project,add_shot):
    shot=add_shot(tmp_project,'S01')
    before={p.relative_to(tmp_project.root).as_posix():file_digest(p) for p in tmp_project.root.rglob('*') if p.is_file()}
    result=from_shot(tmp_project.root,'S01')
    after={p.relative_to(tmp_project.root).as_posix():file_digest(p) for p in tmp_project.root.rglob('*') if p.is_file()}
    assert before==after
    assert result['schema_id']=='manju.project-authoring-import/v1'
    assert result['source_plan_sha256']==digest(result['source_plan'])
    assert result['request']['shot_id']=='S01' and result['project_modified'] is False
    assert Request.model_validate(result['request']).prompt


def test_cli_exports_standalone_and_refuses_overwrite(tmp_path):
    runner=CliRunner();out=tmp_path/'本地工作台.html'
    result=runner.invoke(app,['workbench','--output',str(out)])
    assert result.exit_code==0,result.stdout
    assert out.read_bytes()==PAGE.read_bytes()
    assert runner.invoke(app,['workbench','--output',str(out)]).exit_code==2


def test_standalone_is_reproducible():
    assert PAGE.read_bytes()==(REPO/'src/manju/authoring/data/workbench.html').read_bytes()
    text=PAGE.read_text()
    assert "connect-src 'none'" in text and '__SCRIPT__' not in text
    assert '<script src=' not in text and '@import' not in text


@pytest.fixture(scope='module')
def browser():
    if not importlib.util.find_spec('playwright') or not shutil.which('chromium'):
        pytest.skip('real Chromium/Playwright not installed')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'],timeout=15000)
        yield browser
        browser.close()


@pytest.fixture
def page(browser):
    context=browser.new_context(accept_downloads=True,viewport={'width':1280,'height':960})
    page=context.new_page()
    if os.environ.get('MANJU_BROWSER_TEST_TRANSPORT') == 'isolated_document':
        # Sandbox policy blocks every navigation before route interception.
        # This executes the exact file bytes but is NOT file:// navigation acceptance.
        page.set_content(PAGE.read_text(),wait_until='load')
    else:
        page.goto(PAGE.as_uri(),wait_until='load',timeout=15000)
    yield page
    context.close()


def test_real_file_page_launch_no_external_requests(page):
    failures=[];page.on('pageerror',lambda e:failures.append(str(e)))
    assert page.title()=='Manju · 离线镜头工作台'
    assert page.evaluate('typeof ManjuWorkbench')=='object'
    assert page.evaluate('typeof ManjuWorkbench.SHA256')=='function'
    assert not failures


@pytest.mark.parametrize('changes',[
    {},{'resolution':'1080p','duration_s':4}, {'duration_s':8,'resolution':'2K'},
    {'task':'edit','assets':[{'id':'V1','role':'source_video','path':'v.mp4','sha256':'a'*64,'bytes':100,'duration_ms':11000,'origin_model':None}],'preserve':['服装'],'change':['光线']},
    {'task':'bridge','resolution':'768P','aspect_ratio':'adaptive','assets':[{'id':'I1','role':'first_frame','path':'首.png','sha256':'a'*64,'bytes':100,'duration_ms':None,'origin_model':None},{'id':'I2','role':'last_frame','path':'尾.png','sha256':'b'*64,'bytes':100,'duration_ms':None,'origin_model':None}]},
    {'task':'extend','assets':[{'id':'V1','role':'source_video','path':'v.mp4','sha256':'a'*64,'bytes':100,'duration_ms':8000,'origin_model':'veo-3.1'}]},
    {'task':'reference','resolution':'768P','assets':[{'id':'V1','role':'reference_video','path':'v.mp4','sha256':'a'*64,'bytes':100,'duration_ms':1000,'origin_model':None}]},
    {'task':'perform'}])
def test_browser_python_parity(page,changes):
    values={'shot_id':'镜头01','task':'create','prompt':'一个连续镜头。','duration_s':8,'resolution':'720p','aspect_ratio':'16:9'};values.update(changes)
    r=Request.model_validate(values);expected=plan(r,load_catalog(),today=__import__('datetime').date(2026,9,6))
    actual=page.evaluate('r => {const q=ManjuWorkbench.normalizeRequest(r); return {request:q, options:ManjuWorkbench.options(q,ManjuWorkbench.catalog(),"2026-09-06")};}',r.model_dump(mode='json'))
    assert actual['request']==r.model_dump(mode='json')
    assert actual['options']==expected['options']
    assert page.evaluate('r=>ManjuWorkbench.hash(r)',actual['request'])==digest(r)


def test_ui_real_download_and_python_verification(page,tmp_path):
    page.locator('#prompt').fill('一个连续镜头，雨后灯光映在水面。')
    page.locator('#check-plan').click()
    page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).check()
    page.locator('#reviewer').fill('浏览器实测')
    page.locator('#human-confirmed').check();page.locator('#ack-warnings').check()
    with page.expect_download(timeout=20000) as info:page.locator('#export-bundle').click()
    path=tmp_path/info.value.suggested_filename;info.value.save_as(path)
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None;z.extractall(tmp_path/'bundle')
    assert verify_bundle(tmp_path/'bundle')['ok']


def test_missing_files_refuse_and_rebind_by_hash(page,tmp_path):
    # A real PNG from the standard library, not an image extension on arbitrary bytes.
    import struct,zlib
    def chunk(kind,payload):return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\xff\0\0'))+chunk(b'IEND',b'')
    image=tmp_path/'新文件名.png';image.write_bytes(data)
    from hashlib import sha256
    req=Request(shot_id='首帧',task='animate',prompt='保持主体，缓慢推镜',duration_s=8,resolution='720p',aspect_ratio='16:9',assets=[dict(id='A1',role='first_frame',path='旧名字.png',sha256=sha256(data).hexdigest(),bytes=len(data))])
    source=tmp_path/'request.json';source.write_text(json.dumps(req.model_dump(mode='json'),ensure_ascii=False))
    page.locator('#import-request').set_input_files(source)
    page.wait_for_function('ManjuWorkbench.state.assets.length===1')
    page.locator('#check-plan').click();page.get_by_role('radio',name='Veo 3.1 Preview first',exact=True).check()
    page.locator('#reviewer').fill('测试');page.locator('#human-confirmed').check();page.locator('#ack-warnings').check();page.locator('#export-bundle').click()
    page.wait_for_function('document.getElementById("status").textContent.includes("缺少实际素材")')
    page.locator('#asset-files').set_input_files(image)
    page.wait_for_function('ManjuWorkbench.state.files.size===1 && !ManjuWorkbench.state.busy')
    assert page.evaluate('ManjuWorkbench.state.assets.length')==1
    assert not page.locator('#human-confirmed').is_checked()
    page.locator('#check-plan').click();page.get_by_role('radio',name='Veo 3.1 Preview first',exact=True).check()
    page.locator('#human-confirmed').check();page.locator('#ack-warnings').check()
    with page.expect_download(timeout=20000) as info:page.locator('#export-bundle').click()
    path=tmp_path/'with-image.zip';info.value.save_as(path)
    with zipfile.ZipFile(path) as z:z.extractall(tmp_path/'with-image')
    assert verify_bundle(tmp_path/'with-image')['ok']
    assert (tmp_path/'with-image/assets/旧名字.png').read_bytes()==data


def test_edits_invalidate_and_draft_recovers_without_approval(page):
    if os.environ.get('MANJU_BROWSER_TEST_TRANSPORT') == 'isolated_document':
        pytest.skip('file:// reload and localStorage are blocked by this sandbox, not claimed as verified')
    page.locator('#prompt').fill('尚未导出的重要草稿')
    page.locator('#check-plan').click();page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).check()
    page.locator('#human-confirmed').check();page.locator('#prompt').fill('继续输入，不丢失')
    assert not page.locator('#human-confirmed').is_checked()
    page.reload();assert page.locator('#prompt').input_value()=='继续输入，不丢失'
    assert not page.locator('#human-confirmed').is_checked()
    assert page.evaluate('ManjuWorkbench.state.selected') is None


def test_prompt_xss_is_data_not_script(page,tmp_path):
    r=Request(shot_id='x',task='create',prompt='<img src=x onerror="window.injected=true">')
    path=tmp_path/'x.json';path.write_text(json.dumps(r.model_dump(mode='json')))
    page.locator('#import-request').set_input_files(path)
    page.wait_for_function('document.getElementById("prompt").value.includes("onerror")')
    page.locator('#check-plan').click()
    assert not page.evaluate('Boolean(window.injected)')
    assert page.locator('img').count()==0


def test_mobile_no_horizontal_overflow(page):
    page.set_viewport_size({'width':390,'height':844})
    page.locator('#prompt').fill('窄屏测试');page.locator('#check-plan').click()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_catalog_shape_rejects_extra_execution_fields(page):
    c=load_catalog().model_dump(mode='json');c['profiles'][0]['api_key']='not-a-real-key'
    result=page.evaluate('c=>{try{ManjuWorkbench.normalizeCatalog(c);return false;}catch(e){return true;}}',c)
    assert result


@pytest.mark.parametrize('length',[0,1,55,56,63,64,65,127,128,129,1000,1000000])
def test_incremental_sha256_known_answers_and_chunking(page,length):
    from hashlib import sha256
    expected=sha256(b'a'*length).hexdigest()
    actual=page.evaluate('n=>{const h=new ManjuWorkbench.SHA256();for(let i=0;i<n;i+=37)h.update(new Uint8Array(Math.min(37,n-i)).fill(97));return h.hex();}',length)
    assert actual==expected


def test_inaccessible_storage_degrades_to_export_not_crash(page):
    if os.environ.get('MANJU_BROWSER_TEST_TRANSPORT') != 'isolated_document':
        pytest.skip('this fault injection case requires an isolated document')
    page.locator('#prompt').fill('存储被拒绝时仍可导出')
    assert '不允许保存草稿' in page.locator('#storage-note').inner_text()
    with page.expect_download() as info:page.locator('#export-request').click()
    assert info.value.suggested_filename.endswith('.json')

def test_original_r4_bundle_still_verifies():
    result=verify_bundle(REPO/'tests/fixtures/authoring_r4')
    assert result['request_sha256']=='2ff03c70513c83cf9bb03940c02cd4a3c86d6d4d9662776a15a0493a76dddd59'


def test_catalog_roundtrip_python_browser(page):
    cat=load_catalog().model_dump(mode='json')
    assert page.evaluate('c=>ManjuWorkbench.normalizeCatalog(c)',cat)==cat
    assert page.evaluate('c=>ManjuWorkbench.hash(c)',cat)==digest(load_catalog())


def test_readonly_project_import_in_browser(page,tmp_project,add_shot,tmp_path):
    add_shot(tmp_project,'S01')
    data=from_shot(tmp_project.root,'S01')
    path=tmp_path/'shot.json';path.write_text(json.dumps(data,ensure_ascii=False))
    page.locator('#import-request').set_input_files(path)
    page.wait_for_function('document.getElementById("shot-id").value==="S01"')
    assert page.evaluate('ManjuWorkbench.getRequest()')==data['request']
    assert page.locator('#source-details').is_visible()
