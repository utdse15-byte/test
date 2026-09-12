"""Byte-level download recovery including the actual browser save/read-back loop."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import random
import shutil
import zipfile
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ready_parts',ROOT/'tools/user_ready/download_parts.py')
PARTS=importlib.util.module_from_spec(spec);spec.loader.exec_module(PARTS)

@pytest.fixture
def fragments(tmp_path):
    original=tmp_path/'MANJU_READY_FULL.zip'
    with zipfile.ZipFile(original,'w',zipfile.ZIP_STORED) as z:
        z.writestr('示例/data.bin',random.Random(47).randbytes(4500))
    target=tmp_path/'fragments'
    manifest=PARTS.split_archive(original,target,block_size=1024)
    assert len(manifest['parts'])==5
    paths=[target/p['name'] for p in manifest['parts']]
    return original,manifest,paths


def test_reversed_renamed_fragments_are_reassembled_by_bytes(fragments,tmp_path):
    original,manifest,paths=fragments
    renamed=[]
    for i,p in enumerate(reversed(paths)):
        q=tmp_path/f'改名_{i}.zip';shutil.copyfile(p,q);renamed.append(q)
    output=tmp_path/'已恢复.zip'
    receipt=PARTS.reassemble(renamed,output,manifest)
    assert receipt['ok'] and output.read_bytes()==original.read_bytes()
    assert zipfile.ZipFile(output).testzip() is None


@pytest.mark.parametrize('kind',['missing','duplicate','tamper','truncated','wrong-length','link','exists'])
def test_fragment_failures_never_replace_or_create_final(fragments,tmp_path,kind):
    original,manifest,paths=fragments
    output=tmp_path/'never-overwrite.zip'
    before=original.read_bytes()
    if kind=='missing':paths=paths[:-1]
    if kind=='duplicate':paths=[paths[0]]+paths[:-1]
    if kind in ['tamper','truncated','wrong-length']:
        data=bytearray(paths[2].read_bytes())
        if kind=='tamper':data[99]^=1
        elif kind=='truncated':data=data[:300]
        else:data+=b'extra'
        paths[2].write_bytes(data)
    if kind=='link':
        target=tmp_path/'link.zip';target.symlink_to(paths[0]);paths[0]=target
    if kind=='exists':output.write_bytes(b'old user file')
    with pytest.raises((ValueError,FileExistsError)):
        PARTS.reassemble(paths,output,manifest)
    assert output.read_bytes()==b'old user file' if kind=='exists' else not output.exists()
    assert original.read_bytes()==before


def test_offsets_match_exact_payload_and_output_stream(fragments):
    original,manifest,paths=fragments
    joined=bytearray()
    for p,info in zip(paths,manifest['parts']):
        raw=p.read_bytes();data=raw[info['payload_offset']:info['payload_offset']+info['payload_bytes']]
        assert sha256(data).hexdigest()==info['payload_sha256']
        assert zipfile.ZipFile(p).namelist()==[info['payload_name']]
        joined+=data
    assert bytes(joined)==original.read_bytes()


def test_refuses_existing_split_directory(fragments):
    original,_,paths=fragments
    with pytest.raises(FileExistsError):PARTS.split_archive(original,paths[0].parent)


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b=pw.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        yield b
        b.close()


def helper_html(manifest):
    root=ROOT/'tools/user_ready'
    release={'parts':manifest,'files':[{k:manifest[k] for k in ('name','bytes','sha256')}]}
    return (root/'DOWNLOAD_HELPER.template.html').read_text(encoding='utf-8').replace('__SHA256__',(root/'sha256.js').read_text(encoding='utf-8')).replace('__RELEASE__',json.dumps(release))


@pytest.fixture
def helper(browser,fragments):
    original,manifest,paths=fragments
    context=browser.new_context(accept_downloads=True,viewport={'width':390,'height':850})
    page=context.new_page();page.set_content(helper_html(manifest),wait_until='load')
    yield page
    context.close()


def test_browser_reverses_renamed_zips_downloads_and_rechecks_exact_bytes(helper,fragments,tmp_path):
    from playwright.sync_api import expect
    original,manifest,paths=fragments
    files=[{'name':f'改名_{i}.zip','mimeType':'application/zip','buffer':p.read_bytes()} for i,p in enumerate(reversed(paths))]
    helper.locator('#parts-files').set_input_files(files)
    helper.locator('#join').click()
    expect(helper.locator('#save')).to_be_visible()
    expect(helper.locator('#parts-status')).to_contain_text('尚未保存')
    with helper.expect_download(timeout=5000) as event:helper.locator('#save').click()
    output=tmp_path/'from-browser.zip';event.value.save_as(output)
    assert output.read_bytes()==original.read_bytes()
    expect(helper.locator('#parts-status')).to_contain_text('尚未确认保存')
    helper.locator('#check-file').set_input_files(output)
    expect(helper.locator('#check-status')).to_contain_text('核验通过')
    assert helper.evaluate('document.documentElement.scrollWidth<=innerWidth')


@pytest.mark.parametrize('kind',['duplicate','tamper','truncated','missing'])
def test_browser_rejects_bad_fragments(helper,fragments,kind):
    from playwright.sync_api import expect
    _,_,paths=fragments
    files=[{'name':p.name,'mimeType':'application/zip','buffer':p.read_bytes()} for p in paths]
    if kind=='duplicate':files[3]=files[0]
    if kind=='tamper':
        bad=bytearray(files[3]['buffer']);bad[90]^=1;files[3]['buffer']=bytes(bad)
    if kind=='truncated':files[3]['buffer']=files[3]['buffer'][:100]
    if kind=='missing':files.pop()
    helper.locator('#parts-files').set_input_files(files)
    if kind=='missing':expect(helper.locator('#join')).to_be_disabled()
    else:
        helper.locator('#join').click()
        expect(helper.locator('#parts-status')).to_contain_text('未完成')
    expect(helper.locator('#save')).not_to_be_visible()


def test_cancel_invalidates_late_result(helper,fragments):
    from playwright.sync_api import expect
    _,_,paths=fragments
    helper.locator('#parts-files').set_input_files(paths)
    helper.evaluate("document.getElementById('join').click();document.getElementById('cancel').click()")
    helper.wait_for_timeout(120)
    expect(helper.locator('#parts-status')).to_contain_text('已取消')
    expect(helper.locator('#save')).not_to_be_visible()


def test_new_selection_clears_previous_success(helper,fragments):
    from playwright.sync_api import expect
    _,_,paths=fragments
    helper.locator('#parts-files').set_input_files(paths);helper.locator('#join').click()
    expect(helper.locator('#save')).to_be_visible()
    helper.locator('#parts-files').set_input_files([])
    expect(helper.locator('#save')).not_to_be_visible()
    expect(helper.locator('#join')).to_be_disabled()


def test_start_page_actual_save_smoke_and_wrong_file(browser,tmp_path):
    from playwright.sync_api import expect
    context=browser.new_context(accept_downloads=True,viewport={'width':390,'height':850})
    page=context.new_page()
    try:
        page.set_content((ROOT/'tools/user_ready/START_HERE.html').read_text(encoding='utf-8'),wait_until='load')
        expect(page.locator('#capability')).to_contain_text('基础文件接口可用')
        with page.expect_download(timeout=5000) as info:page.locator('#smoke-download').click()
        target=tmp_path/'saved.txt';info.value.save_as(target)
        expect(page.locator('#smoke-status')).to_contain_text('尚未确认')
        page.locator('#smoke-file').set_input_files(target)
        expect(page.locator('#smoke-status')).to_contain_text('保存演练通过')
        page.locator('#smoke-file').set_input_files({'name':'not.txt','mimeType':'text/plain','buffer':b'wrong'})
        expect(page.locator('#smoke-status')).to_contain_text('未通过')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    finally:
        context.close()
