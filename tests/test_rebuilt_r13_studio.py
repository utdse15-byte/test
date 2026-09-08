"""Portable whole-studio backup: raw drafts, media closure, explicit recovery."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import struct
import subprocess
import zipfile

import pytest
from playwright.sync_api import expect
from typer.testing import CliRunner

from manju.authoring.core import AuthoringError, canonical, digest
from manju.authoring.cli import app
from manju.authoring.studio import Studio, verify_studio, REPAIR_FORM, DIRECTOR_FORM, AUXILIARY_FORM
from tests.test_rebuilt_r8_workspace import workspace
from tests.test_rebuilt_r12_director import fake_plan
from tests.test_rebuilt_r5_workbench import browser, page


def empty_document():
    w=workspace();w['pending']=[];w['media']=[]
    return {'schema_id':'manju.studio-session/v1','workspace':w,
            'repair':{'form':dict.fromkeys(sorted(REPAIR_FORM),''),'source':None,'request':None},
            'director':{'form':dict.fromkeys(sorted(DIRECTOR_FORM),''),'source':None,'anchors':[]},
            'auxiliary_form':dict.fromkeys(sorted(AUXILIARY_FORM),''),'media':[],
            'quality_only_on_restore':True,'confirmations_restored':False,
            'automatic_execution':False,'project_modified':False}


def rich_document():
    doc=empty_document();plan,video,frame,guide=fake_plan()
    doc['workspace']=workspace(video)
    doc['repair']['source']=plan.source.model_dump(mode='json')
    doc['director']['source']=plan.source.model_dump(mode='json')
    doc['director']['anchors']=[a.model_dump(mode='json') for a in plan.anchors]
    files={sha256(data).hexdigest():data for data in (video,frame,guide)}
    doc['media']=[dict(sha256=h,bytes=len(data),filename=('source.mp4' if data==video else 'image.png'),mime_type=('video/mp4' if data==video else 'image/png')) for h,data in files.items()]
    return doc,files


def write_archive(path,doc=None,media=None,edit=None):
    doc=doc if doc is not None else empty_document()
    entries={'STUDIO.json':canonical(doc),**{'media/'+h:data for h,data in (media or {}).items()}}
    if edit:edit(entries)
    entries['MANIFEST.json']=canonical({'schema_id':'manju.studio-manifest/v1','files':{n:sha256(data).hexdigest() for n,data in entries.items()}})
    with zipfile.ZipFile(path,'w',zipfile.ZIP_STORED) as z:
        for name,data in entries.items():z.writestr(name,data)
    return path


def download_studio(page,path):
    with page.expect_download(timeout=15000) as item:page.locator('#export-studio').click()
    item.value.save_as(path)
    return path


def test_whitespace_and_invalid_business_inputs_still_backup(tmp_path):
    d=empty_document();d['workspace']['draft']['form']['prompt']='  \n  '
    d['repair']['form']['repair-end']='0.000'
    d['repair']['form']['repair-start']='10.123'
    d['director']['form']['director-preserve']='same\nsame\n'
    restored=Studio.model_validate(d)
    assert restored.model_dump(mode='json')==d
    result=verify_studio(write_archive(tmp_path/'中文.zip',d))
    assert result['ok'] and result['media_files']==0 and not result['automatic_execution']


def test_media_deduplicated_across_all_three_areas(tmp_path):
    d,files=rich_document();out=write_archive(tmp_path/'three.zip',d,files)
    result=verify_studio(out)
    assert result['media_files']==3
    assert result['media_bytes']==sum(map(len,files.values()))
    assert not result['video_decode_verified'] and not result['png_pixels_fully_decoded']


def test_raw_anchor_target_spaces_preserved():
    d,_=rich_document();d['director']['anchors'][0]['target']='  蓝衣服\n 未写完 '
    assert Studio.model_validate(d).director.anchors[0].target=='  蓝衣服\n 未写完 '


@pytest.mark.parametrize('mutation',[
    lambda d:d.update(quality_only_on_restore=False),
    lambda d:d.update(confirmations_restored=True),
    lambda d:d.update(automatic_execution=True),
    lambda d:d.update(project_modified=True),
    lambda d:d.update(project_modified=0),
    lambda d:d.update(unknown='ignored?'),
    lambda d:d['auxiliary_form'].update(unknown='x'),
    lambda d:d['auxiliary_form'].update({'return-width':7}),
    lambda d:d['repair']['form'].pop('repair-end'),
    lambda d:d['director']['form'].update({'director-shot':'x'*30001}),
    lambda d:d['media'].append(deepcopy(d['media'][0])),
    lambda d:d['media'].pop(),
    lambda d:d['director']['source'].update(width=999),
    lambda d:d['repair']['source'].update(media_check='hash_only'),
    lambda d:d['director']['anchors'][0].update(source_time_ms=99999),
    lambda d:d['director']['anchors'].append(deepcopy(d['director']['anchors'][0])),
    lambda d:d['director']['anchors'][0].update(capture_method='HDR'),
    lambda d:d['director']['anchors'][0]['guide'].update(width=321),
    lambda d:d['director']['anchors'][0].update(target='x'*4001),
    lambda d:d['director'].update(source=None),
])
def test_semantic_and_authorization_corruption_rejected(mutation):
    d,_=rich_document();mutation(d)
    with pytest.raises(ValueError):Studio.model_validate(d)


@pytest.mark.parametrize('mutation',[
    lambda e:e.update({'../escape.py':b'no'}),
    lambda e:e.update({'unused.txt':b'no'}),
    lambda e:e.update({'media/'+'f'*64:b'not-referenced'}),
    lambda e:e.pop('STUDIO.json'),
    lambda e:e.update({'STUDIO.json':b'{"schema_id":"x","schema_id":"y"}'}),
    lambda e:e.update({'STUDIO.json':b'['*70+b'0'+b']'*70}),
])
def test_closed_archive_and_strict_json(tmp_path,mutation):
    out=write_archive(tmp_path/'bad.zip',edit=mutation)
    with pytest.raises(ValueError):verify_studio(out)


@pytest.mark.parametrize('variant',['prefix','trailer','duplicate','compressed','symlink','local_name','local_crc','zip_comment'])
def test_zip_layout_attacks(tmp_path,variant):
    src=write_archive(tmp_path/'base.zip');out=tmp_path/'bad.zip'
    data=bytearray(src.read_bytes())
    if variant=='prefix':out.write_bytes(b'prefix'+data)
    elif variant=='trailer':out.write_bytes(data+b'trailing')
    elif variant=='local_name':data[30]=ord('X');out.write_bytes(data)
    elif variant=='local_crc':data[14]^=1;out.write_bytes(data)
    else:
        with zipfile.ZipFile(src) as z:entries={n:z.read(n) for n in z.namelist()}
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED if variant=='compressed' else zipfile.ZIP_STORED) as z:
            for name,value in entries.items():
                if variant=='symlink' and name=='STUDIO.json':
                    info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=0o120777<<16;z.writestr(info,value)
                else:z.writestr(name,value)
            if variant=='duplicate':
                with pytest.warns(UserWarning):z.writestr('STUDIO.json',entries['STUDIO.json'])
            if variant=='zip_comment':z.comment=b'not allowed'
    with pytest.raises(ValueError):verify_studio(out)


def test_payload_tamper_rejected_even_with_reforged_manifest(tmp_path):
    d,files=rich_document();first=next(iter(files));files[first]=files[first]+b'changed'
    with pytest.raises(ValueError):verify_studio(write_archive(tmp_path/'bad.zip',d,files))


def test_cli_read_only_and_nonzero_errors(tmp_path):
    out=write_archive(tmp_path/'one.zip');before=out.read_bytes()
    result=CliRunner().invoke(app,['studio-verify',str(out)])
    assert result.exit_code==0,result.stdout
    assert json.loads(result.stdout)['ok']
    assert out.read_bytes()==before and len(list(tmp_path.iterdir()))==1
    result=CliRunner().invoke(app,['studio-verify',str(tmp_path/'absent.zip')])
    assert result.exit_code==2 and '模型交接未完成' in result.stderr


def test_python_and_browser_normalization_match(page):
    doc,files=rich_document()
    actual=page.evaluate('d=>ManjuStudio.normalize(d)',doc)
    assert actual==Studio.model_validate(doc).model_dump(mode='json')
    assert page.evaluate('d=>ManjuWorkbench.hash(d)',actual)==digest(Studio.model_validate(doc))


def test_actual_download_verify_does_not_apply_and_full_restore(page,tmp_path):
    page.locator('#prompt').fill('  半写的镜头\n ')
    page.locator('#repair-change').fill('  只改衣服\n 未完成')
    page.locator('#director-shot').fill('')
    page.locator('#director-change').fill('  临时外观\n')
    page.locator('#return-section > summary').click()
    page.locator('#return-width').fill('1584')
    out=download_studio(page,tmp_path/'studio.zip')
    assert verify_studio(out)['ok']
    assert page.evaluate('ManjuStudio.needsBackup()')
    before=page.evaluate('ManjuStudio.view()')
    page.locator('#verify-studio-download').set_input_files(out)
    expect(page.locator('#studio-save-status')).to_contain_text('与当前三个工作区一致')
    assert not page.evaluate('ManjuStudio.needsBackup()')
    assert page.evaluate('ManjuStudio.view()')==before
    page.locator('#prompt').fill('新文字')
    assert page.evaluate('ManjuStudio.needsBackup()')
    page.locator('#import-studio').set_input_files(out)
    expect(page.locator('#studio-restore-preview')).to_be_visible()
    assert page.locator('#prompt').input_value()=='新文字'
    page.locator('#studio-restore-confirmed').check();page.locator('#apply-studio').click()
    expect(page.locator('#studio-save-status')).to_contain_text('三个工作区已从实际核验')
    assert page.evaluate('ManjuStudio.view()')==before
    assert not page.evaluate('ManjuStudio.needsBackup()')
    assert page.locator('#quality-only').is_checked()
    assert not page.locator('#human-confirmed').is_checked()
    again=download_studio(page,tmp_path/'again.zip')
    assert again.read_bytes()==out.read_bytes()


def test_partial_json_export_does_not_clear_total_backup_warning(page,tmp_path):
    page.locator('#prompt').fill('有效镜头任务')
    with page.expect_download() as item:page.locator('#export-request').click()
    item.value.save_as(tmp_path/'request.json')
    assert page.evaluate('ManjuStudio.needsBackup()')
    event=page.evaluate("()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}")
    assert event is True


@pytest.mark.parametrize('field',['prompt','repair-change','director-change','return-width'])
def test_preview_invalidated_by_edits_in_any_area(page,tmp_path,field):
    out=download_studio(page,tmp_path/'baseline.zip')
    page.locator('#import-studio').set_input_files(out)
    expect(page.locator('#studio-restore-preview')).to_be_visible()
    if field=='return-width':page.locator('#return-section > summary').click()
    page.locator('#'+field).fill('777' if field=='return-width' else '不要覆盖我')
    expect(page.locator('#apply-studio')).to_be_disabled()
    assert page.evaluate('ManjuStudio.state.pending') is None
    assert page.locator('#'+field).input_value()==('777' if field=='return-width' else '不要覆盖我')


def test_wrong_saved_snapshot_does_not_clear_dirty_or_replace(page,tmp_path):
    out=download_studio(page,tmp_path/'earlier.zip')
    page.locator('#director-change').fill('新编辑')
    page.locator('#verify-studio-download').set_input_files(out)
    expect(page.locator('#studio-save-status')).to_contain_text('与当前现场不同')
    assert page.locator('#director-change').input_value()=='新编辑'
    assert page.evaluate('ManjuStudio.needsBackup()')


def test_missing_binding_never_downloads_empty_backup_and_releases_busy(page):
    page.evaluate("()=>{ManjuWorkbench.state.assets=[{id:'A1',role:'first_frame',path:'lost.png',sha256:'a'.repeat(64),bytes:4,duration_ms:null,origin_model:null}];}")
    page.locator('#export-studio').click()
    expect(page.locator('#status')).to_have_class('status error')
    assert '缺少原文件' in page.locator('#status').inner_text()
    assert not page.evaluate('ManjuStudio.state.busy')


def test_legacy_compatibility_mode_not_restored(page,tmp_path):
    page.locator('#quality-only').uncheck()
    out=download_studio(page,tmp_path/'compat.zip')
    page.locator('#human-confirmed').check();page.locator('#repair-confirmed').check()
    page.locator('#import-studio').set_input_files(out)
    expect(page.locator('#studio-restore-preview')).to_be_visible()
    page.locator('#studio-restore-confirmed').check();page.locator('#apply-studio').click()
    assert page.locator('#quality-only').is_checked()
    assert not page.locator('#human-confirmed').is_checked()
    assert not page.locator('#repair-confirmed').is_checked()


def test_narrow_personal_home_no_horizontal_overflow(page):
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_unsubmitted_promotion_resolution_and_upload_role_are_raw_drafts(page,tmp_path):
    page.locator('#promotion-resolution').fill('  4K  ')
    page.locator('#asset-role').select_option('source_video')
    out=download_studio(page,tmp_path/'pending-settings.zip')
    page.locator('#promotion-resolution').fill('1080p')
    page.locator('#asset-role').select_option('first_frame')
    page.locator('#import-studio').set_input_files(out)
    expect(page.locator('#studio-restore-preview')).to_be_visible()
    page.locator('#studio-restore-confirmed').check();page.locator('#apply-studio').click()
    assert page.locator('#promotion-resolution').input_value()=='  4K  '
    assert page.locator('#asset-role').input_value()=='source_video'
    assert not page.locator('#promotion-confirmed').is_checked()
    assert page.evaluate('ManjuWorkbench.state.stage')=='draft'


def test_async_legacy_import_cannot_race_a_total_backup(page,tmp_path):
    request={'schema_id':'manju.model-request/v1','shot_id':'ASYNC','task':'create','prompt':'后来读入的任务'}
    file=tmp_path/'request.json';file.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
    page.evaluate('''()=>{const text=File.prototype.text;File.prototype.text=function(){
        if(this.name==='request.json')return new Promise(resolve=>{window.finishLegacyImport=()=>text.call(this).then(resolve);});
        return text.call(this);};}''')
    page.locator('#import-request').set_input_files(file)
    page.wait_for_function('typeof window.finishLegacyImport === "function"')
    page.locator('#export-studio').click()
    expect(page.locator('#status')).to_contain_text('其他文件操作仍在进行')
    assert not page.evaluate('ManjuStudio.state.busy')
    page.evaluate('window.finishLegacyImport()')
    expect(page.locator('#prompt')).to_have_value('后来读入的任务')
    out=download_studio(page,tmp_path/'after-import.zip')
    assert verify_studio(out)['ok']


def test_read_in_progress_refuses_changed_work_without_partial_apply(page,tmp_path):
    out=download_studio(page,tmp_path/'old.zip')
    page.evaluate('''()=>{const read=Blob.prototype.arrayBuffer;let once=true;
        Blob.prototype.arrayBuffer=function(){if(once){once=false;return new Promise(resolve=>{
            window.finishStudioRead=()=>read.call(this).then(resolve);});}return read.call(this);};}''')
    page.locator('#import-studio').set_input_files(out)
    page.wait_for_function('typeof window.finishStudioRead === "function"')
    page.locator('#director-change').fill('核验中仍在写的新文字')
    page.evaluate('window.finishStudioRead()')
    expect(page.locator('#status')).to_contain_text('核验期间工作现场变化')
    assert page.locator('#director-change').input_value()=='核验中仍在写的新文字'
    assert page.evaluate('ManjuStudio.state.pending') is None
    assert not page.evaluate('ManjuStudio.state.busy')


@pytest.mark.parametrize('selector',['import-studio','verify-studio-download'])
@pytest.mark.parametrize('corruption',['truncated','duplicate','authority'])
def test_bad_import_and_readback_never_replace_live_drafts(page,tmp_path,selector,corruption):
    d=empty_document()
    if corruption=='authority':d['automatic_execution']=True
    out=write_archive(tmp_path/'untrusted.zip',d)
    if corruption=='truncated':out.write_bytes(out.read_bytes()[:-4])
    if corruption=='duplicate':
        with zipfile.ZipFile(out,'a') as z:
            with pytest.warns(UserWarning):z.writestr('STUDIO.json',b'{}')
    page.locator('#prompt').fill('不能覆盖')
    before=page.evaluate('ManjuStudio.view()')
    page.locator('#'+selector).set_input_files(out)
    expect(page.locator('#status')).to_have_class('status error')
    assert page.evaluate('ManjuStudio.view()')==before
    assert page.evaluate('ManjuStudio.state.pending') is None
    assert not page.evaluate('ManjuStudio.state.busy')
    assert page.evaluate('ManjuStudio.needsBackup()')


def test_verify_refuses_file_changed_during_final_hash(tmp_path,monkeypatch):
    import manju.authoring.studio as module
    out=write_archive(tmp_path/'changing.zip')
    original=module.file_digest
    def replace_after_hash(path):
        result=original(path)
        with path.open('ab') as stream:stream.write(b'changed while checking')
        return result
    monkeypatch.setattr(module,'file_digest',replace_after_hash)
    with pytest.raises(AuthoringError,match='changed during verification'):
        verify_studio(out)


def test_failed_save_also_explains_error_at_the_personal_home(page):
    page.evaluate("()=>{ManjuWorkbench.state.assets=[{id:'A1',role:'first_frame',path:'lost.png',sha256:'a'.repeat(64),bytes:4,duration_ms:null,origin_model:null}];}")
    page.locator('#export-studio').click()
    expect(page.locator('#studio-save-status')).to_contain_text('缺少原文件')
    assert 'reason' in page.locator('#studio-save-status').get_attribute('class')
