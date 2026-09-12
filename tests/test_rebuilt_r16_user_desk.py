"""R16 user paths: identify, stage, check out, recover; never imply approval."""
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import struct
import zipfile

import pytest
from playwright.sync_api import expect
from typer.testing import CliRunner

from manju.authoring.cli import app
from manju.authoring.core import canonical, digest
from manju.authoring.desk import Desk, SCRATCH_FORM, extract_studio, verify_desk
from manju.authoring.exchange import create_edit
from manju.authoring.studio import verify_studio
from tests.test_rebuilt_r13_studio import empty_document, rich_document, write_archive
from tests.test_rebuilt_r14_flexibility import sample_template
from tests.test_rebuilt_r5_workbench import browser, page


def metadata(inner, *, pending=True):
    doc=empty_document()
    edit=create_edit(doc).model_dump(mode='json')
    edit['values']['shot/prompt']='  外部还有一段没决定要不要用\n'
    return {'schema_id':'manju.desk-session/v1','studio_archive_sha256':sha256(inner).hexdigest(),
            'external_edit':edit if pending else None,'personal_template':sample_template() if pending else None,
            'scratch_form':dict(zip(sorted(SCRATCH_FORM),['B方案_未完成','  新模板名  ','  还在写的说明\n'])),
            'confirmations_restored':False,'automatic_execution':False,'project_modified':False}


def write_desk(tmp_path, name='desk.zip', *, rich=False, edit=None, inner_edit=None, zip_edit=None):
    d,media=rich_document() if rich else (empty_document(),{})
    inner_path=write_archive(tmp_path/(name+'.studio.zip'),d,media,inner_edit)
    inner=inner_path.read_bytes();meta=metadata(inner)
    if edit:edit(meta)
    members={'DESK.json':canonical(meta),'STUDIO.zip':inner}
    members['MANIFEST.json']=canonical({'schema_id':'manju.desk-manifest/v1','files':{n:sha256(data).hexdigest() for n,data in members.items()}})
    if zip_edit:zip_edit(members)
    out=tmp_path/name
    with zipfile.ZipFile(out,'w',zipfile.ZIP_STORED) as z:
        for n,data in members.items():z.writestr(n,data)
    return out,meta,inner


def input_json(page,selector,value,name='材料.json'):
    page.locator(selector).set_input_files({'name':name,'mimeType':'application/json','buffer':canonical(value)})


def save_desk(page,path):
    with page.expect_download(timeout=20000) as d:page.locator('#desk-save').click()
    d.value.save_as(path);return path


def current_edit(page):
    return page.evaluate('async()=>ManjuExchange.make(ManjuStudio.view(),["shot","director"])')


def open_template_controls(page):
    if not page.locator('#flex-template-box').get_attribute('open') == '':
        page.locator('#flex-template-box > summary').click()


def load_buffers(page):
    open_template_controls(page)
    e=current_edit(page);e['values']['shot/prompt']='外部意见还没有应用'
    input_json(page,'#exchange-import-json',e);expect(page.locator('#exchange-preview')).to_be_enabled()
    t=sample_template();input_json(page,'#flex-template-file',t);expect(page.locator('#flex-preview-template')).to_be_enabled()
    page.locator('#flex-template-name').fill('  我还在写的模板名  ')
    page.locator('#flex-template-notes').fill('  未完成\n第二行 ')
    page.locator('#flex-branch-name').fill('B方案_未完成')
    return e,t


@pytest.mark.parametrize('rich',[False,True])
def test_python_check_and_exact_inner_extraction(tmp_path,rich):
    path,meta,inner=write_desk(tmp_path,rich=rich);before=path.read_bytes();receipt=verify_desk(path)
    assert receipt['ok'] and receipt['external_edit_present'] and receipt['personal_template_present']
    assert receipt['desk_sha256']==digest(meta) and not receipt['video_decode_verified']
    assert receipt['studio']['media_files']==(3 if rich else 0)
    output=tmp_path/'旧版可以打开.zip';result=extract_studio(path,output)
    assert output.read_bytes()==inner and path.read_bytes()==before and verify_studio(output)['ok']
    assert result['original_studio_bytes_preserved'] and not result['unapplied_buffers_included']


@pytest.mark.parametrize('change',[
 lambda d:d.update(schema_id='manju.desk-session/v2'),lambda d:d.update(confirmations_restored=True),
 lambda d:d.update(confirmations_restored=0),lambda d:d.update(automatic_execution=True),
 lambda d:d.update(project_modified=True),lambda d:d.update(extra='ignored?'),
 lambda d:d.pop('external_edit'),lambda d:d.update(studio_archive_sha256='x'*64),
 lambda d:d['scratch_form'].update(extra='x'),lambda d:d['scratch_form'].pop('flex-template-name'),
 lambda d:d['scratch_form'].update({'flex-template-name':12}),
 lambda d:d['scratch_form'].update({'flex-template-notes':'x'*30001}),
 lambda d:d['external_edit'].update(transfers_approval=True),
 lambda d:d['external_edit']['base'].update({'shot/prompt':'篡改导出基线'}),
 lambda d:d['personal_template'].update(automatic_execution=True),
 lambda d:d['personal_template']['fields'].update(script='run this'),
])
def test_schema_rejects_without_coercing_or_creating_approval(tmp_path,change):
    path,_,_=write_desk(tmp_path,edit=change)
    with pytest.raises((ValueError,TypeError)):verify_desk(path)


@pytest.mark.parametrize('change',[
 lambda m:m.pop('STUDIO.zip'),lambda m:m.update({'../STUDIO.zip':b'x'}),
 lambda m:m.update({'extra.txt':b'x'}),lambda m:m.update({'DESK.json':b'{"schema_id":"x","schema_id":"y"}'}),
 lambda m:m.update({'DESK.json':b'x'*(2*1024*1024+1)}),
 lambda m:m.update({'STUDIO.zip':b'not a real zip'}),
 lambda m:m.update({'MANIFEST.json':canonical({'schema_id':'manju.desk-manifest/v1','files':{}})}),
 lambda m:m.update({'MANIFEST.json':canonical({'schema_id':'manju.desk-manifest/v1','files':{'DESK.json':'a'*64,'STUDIO.zip':'b'*64}})}),
])
def test_closed_inventory_and_invalid_bytes_rejected(tmp_path,change):
    path,_,_=write_desk(tmp_path,zip_edit=change)
    with pytest.raises(ValueError):verify_desk(path)


def test_inner_archive_still_has_its_own_semantic_validation(tmp_path):
    def corrupt(entries):
        d=json.loads(entries['STUDIO.json']);d['automatic_execution']=True;entries['STUDIO.json']=canonical(d)
    path,_,_=write_desk(tmp_path,inner_edit=corrupt)
    with pytest.raises(ValueError):verify_desk(path)


@pytest.mark.parametrize('kind',['duplicate','compressed','symlink','prefix','suffix','local_name','comment'])
def test_rejects_unsafe_zip_encodings(tmp_path,kind):
    source,_,_=write_desk(tmp_path);dest=tmp_path/'bad.zip'
    with zipfile.ZipFile(source) as z:entries={n:z.read(n) for n in z.namelist()}
    if kind in ['prefix','suffix','local_name']:
        data=source.read_bytes()
        if kind=='prefix':data=b'prefix'+data
        elif kind=='suffix':data+=b'suffix'
        else:data=data[:30]+b'X'+data[31:]
        dest.write_bytes(data)
    else:
        with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED if kind=='compressed' else zipfile.ZIP_STORED) as z:
            if kind=='comment':z.comment=b'tail'
            for name,data in entries.items():
                if kind=='symlink' and name=='DESK.json':
                    info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=0o120777<<16;z.writestr(info,data)
                else:z.writestr(name,data)
            if kind=='duplicate':z.writestr('DESK.json',entries['DESK.json'])
    with pytest.raises(ValueError):verify_desk(dest)


def test_readonly_input_and_exclusive_output(tmp_path):
    path,_,_=write_desk(tmp_path);out=tmp_path/'keep.zip';out.write_bytes(b'old work')
    with pytest.raises(ValueError):extract_studio(path,out)
    assert out.read_bytes()==b'old work'


def test_file_changed_while_verifying_cannot_get_receipt(tmp_path,monkeypatch):
    import manju.authoring.desk as desk
    path,_,_=write_desk(tmp_path);real=desk.file_digest
    def change(p):
        value=real(p)
        if p==path:path.write_bytes(path.read_bytes()+b'x')
        return value
    monkeypatch.setattr(desk,'file_digest',change)
    with pytest.raises(ValueError,match='changed during'):verify_desk(path)


def test_cli_readonly_and_extract_paths(tmp_path):
    source,_,inner=write_desk(tmp_path);runner=CliRunner()
    result=runner.invoke(app,['desk-verify',str(source)]);assert result.exit_code==0,result.stdout
    receipt=json.loads(result.stdout);assert receipt['ok'] and not receipt['project_modified']
    out=tmp_path/'for-r15.zip'
    result=runner.invoke(app,['desk-extract-studio',str(source),'--output',str(out)])
    assert result.exit_code==0,result.stdout
    assert out.read_bytes()==inner
    assert runner.invoke(app,['desk-extract-studio',str(source),'--output',str(out)]).exit_code!=0


def test_browser_python_schema_parity(page,tmp_path):
    _,doc,_=write_desk(tmp_path)
    assert page.evaluate('d=>ManjuDesk.normalize(d)',doc)==Desk.model_validate(doc).model_dump(mode='json')


def test_save_download_is_not_claimed_verified(page,tmp_path):
    page.locator('#prompt').fill('未保存的镜头')
    out=save_desk(page,tmp_path/'desk.zip')
    assert verify_desk(out)['ok'] and page.evaluate('ManjuDesk.needsSave()')
    expect(page.locator('#desk-save-status')).to_contain_text('尚不能声称已落盘')
    page.locator('#desk-verify-file').set_input_files(out)
    expect(page.locator('#desk-save-status')).to_contain_text('均与当前一致')
    assert not page.evaluate('ManjuDesk.needsSave()')
    open_template_controls(page)
    page.locator('#flex-template-notes').fill('又想到了一条')
    assert page.evaluate('ManjuDesk.needsSave()')
    expect(page.locator('#desk-save-status')).to_contain_text('新修改')


def test_roundtrip_restores_unapplied_materials_not_decisions(page,tmp_path):
    page.locator('#prompt').fill('本地当前任务');edit,template=load_buffers(page)
    before=page.evaluate('ManjuDesk.fingerprint()');out=save_desk(page,tmp_path/'return.zip')
    page.locator('#prompt').fill('不要误覆盖这句')
    page.locator('#intake-files').set_input_files(out)
    expect(page.locator('#intake-summary')).to_contain_text('收工包')
    page.locator('#intake-open').click()
    expect(page.locator('#desk-restore-preview')).to_be_visible()
    assert page.locator('#prompt').input_value()=='不要误覆盖这句'
    page.locator('#desk-restore-confirmed').check();page.locator('#desk-restore-apply').click()
    expect(page.locator('#prompt')).to_have_value('本地当前任务')
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    assert page.evaluate('ManjuExchange.state.edit')==edit
    assert page.evaluate('ManjuFlex.state.template')==template
    assert not page.locator('#exchange-confirmed').is_checked()
    assert not page.locator('#human-confirmed').is_checked()
    assert page.locator('#quality-only').is_checked()
    assert page.evaluate('ManjuWorkbench.state.selected') is None
    again=save_desk(page,tmp_path/'again.zip');assert out.read_bytes()==again.read_bytes()


def test_pending_import_failure_preserves_previous_external_draft(page):
    e=current_edit(page);input_json(page,'#exchange-import-json',e)
    expect(page.locator('#exchange-preview')).to_be_enabled()
    before=page.evaluate('ManjuExchange.state.edit')
    input_json(page,'#exchange-import-json',{'schema_id':'garbage'})
    expect(page.locator('#status')).to_have_class('status error')
    assert page.evaluate('ManjuExchange.state.edit')==before
    expect(page.locator('#exchange-preview')).to_be_enabled()


def test_invalid_template_keeps_existing_template(page):
    t=sample_template();input_json(page,'#flex-template-file',t)
    expect(page.locator('#flex-preview-template')).to_be_enabled()
    input_json(page,'#flex-template-file',{'schema_id':'no'})
    expect(page.locator('#flex-template-status')).to_contain_text('保持不变')
    assert page.evaluate('ManjuFlex.state.template')==t
    expect(page.locator('#flex-preview-template')).to_be_enabled()


def test_old_total_verification_does_not_mark_external_buffer_saved(page,tmp_path):
    with page.expect_download() as d:page.locator('#export-studio').click()
    out=tmp_path/'studio.zip';d.value.save_as(out)
    page.locator('#verify-studio-download').set_input_files(out)
    expect(page.locator('#studio-save-status')).to_contain_text('内容与当前三个工作区一致')
    e=current_edit(page);input_json(page,'#exchange-import-json',e)
    expect(page.locator('#exchange-preview')).to_be_enabled()
    assert page.evaluate('ManjuDesk.needsSave()')


def test_restore_preview_invalidated_by_new_scratch_text(page,tmp_path):
    out=save_desk(page,tmp_path/'old.zip');page.locator('#intake-files').set_input_files(out)
    expect(page.locator('#intake-open')).to_be_enabled();page.locator('#intake-open').click()
    expect(page.locator('#desk-restore-preview')).to_be_visible()
    open_template_controls(page)
    page.locator('#flex-template-name').fill('恢复预览之后写的内容')
    expect(page.locator('#desk-restore-summary')).to_contain_text('失效')
    expect(page.locator('#desk-restore-apply')).to_be_disabled()
    assert page.locator('#flex-template-name').input_value()=='恢复预览之后写的内容'


@pytest.mark.parametrize('kind',['request','catalog','template','external'])
def test_intake_content_identity_ignores_filename(page,kind):
    values={
      'request':{'schema_id':'manju.model-request/v1','shot_id':'S-X','task':'create','prompt':'带回任务'},
      'catalog':page.evaluate('ManjuWorkbench.catalog()'),
      'template':sample_template(),'external':current_edit(page)}
    current=page.locator('#prompt').input_value()
    input_json(page,'#intake-files',values[kind],name='看不出是什么的文件.bin')
    expect(page.locator('#intake-preview')).to_be_visible()
    assert page.evaluate('ManjuDesk.state.intake.kind')==kind
    assert page.locator('#prompt').input_value()==current
    if kind=='request':
        expect(page.locator('#intake-open')).to_be_disabled();page.locator('#intake-confirmed').check()
    page.locator('#intake-open').click()
    if kind=='request':expect(page.locator('#prompt')).to_have_value('带回任务')
    elif kind=='catalog':expect(page.locator('#catalog-preview')).to_be_visible()
    elif kind=='template':expect(page.locator('#flex-preview-template')).to_be_enabled()
    else:expect(page.locator('#exchange-preview')).to_be_enabled()


def test_intake_old_studio_has_full_and_partial_choices(page,tmp_path):
    path=write_archive(tmp_path/'旧备份.zip')
    page.locator('#intake-files').set_input_files(path)
    expect(page.locator('#intake-preview')).to_be_visible()
    assert page.locator('#intake-destination option').count()==2
    page.locator('#intake-destination').select_option('1');page.locator('#intake-open').click()
    expect(page.locator('#flex-preview-compose')).to_be_enabled()


@pytest.mark.parametrize('value',[{'schema_id':'unknown/v1'},['not','object'],{'x':1}])
def test_unknown_json_never_changes_current_work(page,value):
    page.locator('#prompt').fill('保留本地')
    input_json(page,'#intake-files',value)
    expect(page.locator('#intake-status')).to_contain_text('未打开')
    assert page.locator('#prompt').input_value()=='保留本地'
    assert page.evaluate('ManjuDesk.state.intake') is None


def test_duplicate_json_keys_rejected_before_routing(page):
    page.locator('#intake-files').set_input_files({'name':'edit.json','mimeType':'application/json','buffer':b'{"schema_id":"manju.external-edit/v1","schema_id":"evil"}'})
    expect(page.locator('#intake-status')).to_contain_text('重复字段')
    assert page.evaluate('ManjuDesk.state.intake') is None


def test_cancel_intake_preserves_everything(page):
    e=current_edit(page);before=page.evaluate('ManjuDesk.fingerprint()')
    input_json(page,'#intake-files',e);expect(page.locator('#intake-preview')).to_be_visible()
    page.locator('#intake-cancel').click();expect(page.locator('#intake-preview')).to_be_hidden()
    assert page.evaluate('ManjuDesk.fingerprint()')==before


def test_media_route_demands_explicit_purpose(page):
    page.locator('#intake-files').set_input_files({'name':'return.mp4','mimeType':'video/mp4','buffer':b'not yet decoded'})
    expect(page.locator('#intake-preview')).to_be_visible()
    assert page.locator('#intake-destination option').count()==6
    assert page.evaluate('ManjuWorkbench.state.assets')==[]
    page.locator('#intake-destination').select_option('1')
    expect(page.locator('#intake-replace-note')).to_be_visible()
    expect(page.locator('#intake-open')).to_be_disabled()


def test_batch_mixed_documents_refused_atomically(page):
    one=canonical(current_edit(page));before=page.evaluate('ManjuDesk.fingerprint()')
    page.locator('#intake-files').set_input_files([{'name':'a.json','mimeType':'application/json','buffer':one},{'name':'b.json','mimeType':'application/json','buffer':one}])
    expect(page.locator('#intake-status')).to_contain_text('分批')
    assert page.evaluate('ManjuDesk.fingerprint()')==before


def test_pair_txt_requires_base_edit(page):
    page.locator('#intake-files').set_input_files({'name':'other.txt','mimeType':'text/plain','buffer':b'notes'})
    expect(page.locator('#intake-preview')).to_be_visible();page.locator('#intake-open').click()
    expect(page.locator('#intake-status')).to_contain_text('先打开本次配套')
    assert page.evaluate('ManjuExchange.state.edit') is None


def test_filename_is_only_text_not_html(page):
    e=current_edit(page);input_json(page,'#intake-files',e,name='<img src=x onerror=alert(1)>.json')
    expect(page.locator('#intake-status')).to_contain_text('<img')
    assert page.locator('#intake-status img').count()==0


def test_new_input_invalidates_intake_route(page):
    input_json(page,'#intake-files',sample_template());expect(page.locator('#intake-preview')).to_be_visible()
    page.locator('#prompt').fill('识别后继续写')
    expect(page.locator('#intake-open')).to_be_disabled()
    expect(page.locator('#intake-status')).to_contain_text('请重新选择')


def test_drop_route_and_outside_file_navigation_prevention(page):
    e=current_edit(page)
    page.evaluate('''doc=>{const d=new DataTransfer();d.items.add(new File([JSON.stringify(doc)],'e.json',{type:'application/json'}));document.getElementById('intake-drop').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:d}));}''',e)
    expect(page.locator('#intake-preview')).to_be_visible()
    assert page.evaluate('ManjuDesk.state.intake.kind')=='external'
    assert page.evaluate('''()=>{const d=new DataTransfer();d.items.add(new File(['a'],'a.txt'));return !document.body.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:d}));}''')


def test_original_studio_bad_number_cannot_silently_erase_field(page,tmp_path):
    d=empty_document();d['repair']['form']['repair-start']='not a number'
    source=write_archive(tmp_path/'bad-number.zip',d)
    page.locator('#prompt').fill('全部保留');before=page.evaluate('ManjuStudio.view()')
    page.locator('#import-studio').set_input_files(source);expect(page.locator('#studio-restore-preview')).to_be_visible()
    page.locator('#studio-restore-confirmed').check();page.locator('#apply-studio').click()
    expect(page.locator('#status')).to_contain_text('无法原样写入')
    assert page.evaluate('ManjuStudio.view()')==before


def test_narrow_home_has_no_overflow(page):
    page.set_viewport_size({'width':390,'height':844})
    load_buffers(page)
    input_json(page,'#intake-files',current_edit(page),name='中文特别长文件名'*10+'.json')
    expect(page.locator('#intake-preview')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')


def test_async_external_read_does_not_replace_newer_work_or_lose_previous(page):
    e=current_edit(page);input_json(page,'#exchange-import-json',e);expect(page.locator('#exchange-preview')).to_be_enabled()
    before=page.evaluate('ManjuExchange.state.edit')
    page.evaluate('''()=>{const original=File.prototype.arrayBuffer;File.prototype.arrayBuffer=function(){if(this.name==='slow.json')return new Promise(resolve=>window.finishSlow=()=>original.call(this).then(resolve));return original.call(this);};}''')
    fresh=deepcopy(e);fresh['values']['shot/prompt']='过时文字'
    input_json(page,'#exchange-import-json',fresh,name='slow.json')
    page.wait_for_function('typeof window.finishSlow === "function"')
    page.locator('#prompt').fill('读取期间的新稿');page.evaluate('window.finishSlow()')
    expect(page.locator('#status')).to_contain_text('保留原改稿')
    assert page.evaluate('ManjuExchange.state.edit')==before
    assert page.locator('#prompt').input_value()=='读取期间的新稿'


def test_keyboard_can_open_unified_file_picker(page):
    page.locator('#intake-drop').focus()
    with page.expect_file_chooser() as picker:page.keyboard.press('Enter')
    picker.value.set_files({'name':'template.json','mimeType':'application/json','buffer':canonical(sample_template())})
    expect(page.locator('#intake-summary')).to_contain_text('个人模板')


def test_no_pending_buffers_legacy_verified_backup_still_satisfies_save_warning(page,tmp_path):
    page.locator('#prompt').fill('旧备份兼容现场')
    with page.expect_download() as d:page.locator('#export-studio').click()
    out=tmp_path/'old.zip';d.value.save_as(out)
    page.locator('#verify-studio-download').set_input_files(out)
    expect(page.locator('#studio-save-status')).to_contain_text('内容与当前三个工作区一致')
    assert not page.evaluate('ManjuDesk.needsSave()')


def test_cancel_desk_restore_preserves_buffers_and_text(page,tmp_path):
    old=save_desk(page,tmp_path/'old.zip');page.locator('#prompt').fill('当前必须保留');load_buffers(page)
    before=page.evaluate('ManjuDesk.fingerprint()')
    page.locator('#intake-files').set_input_files(old);expect(page.locator('#intake-preview')).to_be_visible()
    page.locator('#intake-open').click();expect(page.locator('#desk-restore-preview')).to_be_visible()
    page.locator('#desk-restore-cancel').click()
    assert page.evaluate('ManjuDesk.fingerprint()')==before


def test_different_valid_checkout_does_not_clear_save_reminder(page,tmp_path):
    old=save_desk(page,tmp_path/'old.zip');load_buffers(page)
    before=page.evaluate('ManjuDesk.fingerprint()')
    page.locator('#desk-verify-file').set_input_files(old)
    expect(page.locator('#desk-save-status')).to_contain_text('不同')
    assert page.evaluate('ManjuDesk.fingerprint()')==before and page.evaluate('ManjuDesk.needsSave()')


def test_corrupt_desk_does_not_replace_work(page,tmp_path):
    bad,_,_=write_desk(tmp_path,zip_edit=lambda d:d.update({'STUDIO.zip':b'bad'}))
    page.locator('#prompt').fill('保留');before=page.evaluate('ManjuDesk.fingerprint()')
    page.locator('#intake-files').set_input_files(bad);expect(page.locator('#intake-preview')).to_be_visible()
    page.locator('#intake-open').click();expect(page.locator('#desk-save-status')).to_contain_text('未完成')
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    expect(page.locator('#desk-restore-apply')).to_be_disabled()


def test_pending_edit_changed_during_desk_verify_invalidates_result(page,tmp_path):
    old=save_desk(page,tmp_path/'old.zip')
    page.evaluate('''()=>{const original=Blob.prototype.arrayBuffer;let blocked=false;Blob.prototype.arrayBuffer=function(){if(!blocked&&this.size===22){blocked=true;return new Promise(resolve=>window.finishDeskRead=()=>original.call(this).then(resolve));}return original.call(this);};}''')
    page.locator('#desk-verify-file').set_input_files(old)
    page.wait_for_function('typeof window.finishDeskRead === "function"')
    page.locator('#flex-branch-name').fill('读取期间的新名称')
    page.evaluate('window.finishDeskRead()')
    expect(page.locator('#desk-save-status')).to_contain_text('核验期间内容改变')
    assert page.locator('#flex-branch-name').input_value()=='读取期间的新名称'
    assert page.evaluate('ManjuDesk.needsSave()')
