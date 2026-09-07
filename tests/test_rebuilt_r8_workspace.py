"""Full checkpoint closure, malicious archives and actual browser save/restore."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import zipfile

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner
from playwright.sync_api import expect

from manju.authoring.core import AuthoringError, load_catalog, Request, digest
from manju.authoring.workspace import Workspace, verify_workspace, FORM_KEYS
from manju.authoring.cli import app
from manju.review.core import Candidate, create_session
from tests.test_rebuilt_r5_workbench import browser, page


def workspace(payload=b'original candidate bytes'):
    h=sha256(payload).hexdigest()
    draft={'version':1,'form':dict.fromkeys(sorted(FORM_KEYS),''),'assets':[],
           'stage':'draft','draftHash':None,'sourceContext':None}
    draft['form'].update({'shot-id':'S001','task':'create','prompt':'  未完成的描述\n '})
    candidate=Candidate(sha256=h,bytes=len(payload),filename='候选.mp4')
    return {'schema_id':'manju.authoring-workspace/v1','draft':draft,
            'catalog':load_catalog().model_dump(mode='json'),'review':None,
            'pending':[candidate.model_dump(mode='json')],
            'review_form':{'candidate':'','verdict':'','human':'','notes':' 没写完\n','score':''},
            'reveal':False,'media':[{'sha256':h,'bytes':len(payload),'filename':'候选.mp4','mime_type':'video/mp4'}],
            'confirmations_restored':False,'project_modified':False}


def archive(path,doc=None,payload=b'original candidate bytes',changes=None,compression=zipfile.ZIP_STORED):
    doc=doc or workspace(payload)
    entries={'WORKSPACE.json':json.dumps(doc,ensure_ascii=False).encode(),
             'media/'+sha256(payload).hexdigest():payload}
    if changes: changes(entries)
    manifest={'schema_id':'manju.workspace-manifest/v1','files':{k:sha256(v).hexdigest() for k,v in entries.items()}}
    entries['MANIFEST.json']=json.dumps(manifest).encode()
    with zipfile.ZipFile(path,'w',compression) as z:
        for k,v in entries.items():z.writestr(k,v)
    return path


def test_raw_whitespace_and_incomplete_form_preserved(tmp_path):
    doc=workspace();doc['draft']['form']['prompt']='  \n '
    restored=Workspace.model_validate(doc)
    assert restored.draft.form['prompt']=='  \n '
    result=verify_workspace(archive(tmp_path/'backup.zip',doc))
    assert result['media_files']==1 and result['raw_draft_restorable']
    assert not result['project_modified'] and not result['confirmations_restored']
    assert not result['media_decode_verified']


def test_review_history_can_be_independent_of_incomplete_current_draft(tmp_path):
    doc=workspace();c=Candidate.model_validate(doc['pending'][0])
    review=create_session(Request(shot_id='S001',task='create',prompt='已审片任务'),[c],nonce='a'*32)
    doc['review']=review.model_dump(mode='json');doc['pending']=[]
    assert verify_workspace(archive(tmp_path/'checkpoint.zip',doc))['review_decisions']==0


@pytest.mark.parametrize('change',[
    lambda d:d.update(confirmations_restored=True),
    lambda d:d.update(project_modified=True),
    lambda d:d.update(unknown='unexpected'),
    lambda d:d['media'].clear(),
    lambda d:d['media'].append(deepcopy(d['media'][0])),
    lambda d:d['media'][0].update(bytes=1),
    lambda d:d['media'][0].update(filename='../escape.mp4'),
    lambda d:d['media'][0].update(filename='CON.mp4'),
    lambda d:d['draft']['form'].update(secret='not allowed'),
    lambda d:d['draft'].update(stage='final',draftHash=None),
    lambda d:d['pending'].append(deepcopy(d['pending'][0])),
    lambda d:d['draft'].update(sourceContext={'source_plan':{},'source_plan_sha256':'a'*64,'warnings':[]}),
    lambda d:d['review_form'].update(notes='x'*6001),
])
def test_invalid_workspace_rejected(change):
    doc=workspace();change(doc)
    with pytest.raises((ValidationError,AuthoringError)):Workspace.model_validate(doc)


@pytest.mark.parametrize('name',['../escape','media/other','extra.json','MEDIA/a','a\\b'])
def test_extra_or_dangerous_members_rejected(tmp_path,name):
    out=archive(tmp_path/'bad.zip',changes=lambda e:e.update({name:b'x'}))
    with pytest.raises(AuthoringError):verify_workspace(out)


def test_corrupt_member_refused_even_with_valid_zip_crc(tmp_path):
    out=archive(tmp_path/'bad.zip')
    with zipfile.ZipFile(out) as z:entries={n:z.read(n) for n in z.namelist()}
    media=next(k for k in entries if k.startswith('media/'));entries[media]=b'tampered'
    with zipfile.ZipFile(out,'w') as z:
        for k,v in entries.items():z.writestr(k,v)
    with pytest.raises(AuthoringError,match='hash mismatch'):verify_workspace(out)


def test_compressed_workspace_refused(tmp_path):
    with pytest.raises(AuthoringError):verify_workspace(archive(tmp_path/'zip.zip',compression=zipfile.ZIP_DEFLATED))


def test_duplicate_entries_refused(tmp_path):
    out=archive(tmp_path/'duplicate.zip')
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(out,'a') as z:z.writestr('WORKSPACE.json',b'{}')
    with pytest.raises(AuthoringError):verify_workspace(out)


def test_cli_is_read_only(tmp_path):
    out=archive(tmp_path/'现场.zip');before=out.read_bytes()
    r=CliRunner().invoke(app,['workspace-verify',str(out)])
    assert r.exit_code==0,r.stdout
    assert json.loads(r.stdout)['ok']
    assert out.read_bytes()==before and len(list(tmp_path.iterdir()))==1


def test_browser_python_workspace_parity(page):
    doc=workspace()
    actual=page.evaluate('d=>ManjuWorkspace.normalizeWorkspace(d)',doc)
    assert actual==Workspace.model_validate(doc).model_dump(mode='json')
    assert page.evaluate('d=>ManjuWorkbench.hash(d)',actual)==digest(Workspace.model_validate(doc))


def test_actual_browser_export_restore_all_media_and_unsaved_text(page,tmp_path):
    page.locator('#prompt').fill('  原始未完成草稿\n ')
    page.locator('#review-notes').fill(' 尚未提交的审片意见\n')
    page.evaluate('''async()=>{
      const file=new File([new Uint8Array([1,2,3,4])],'首帧.png',{type:'image/png'});
      const entry=await ManjuWorkbench.inspectFile(file);
      ManjuWorkbench.state.files.set(entry.sha256,entry);
      ManjuWorkbench.state.assets=[{id:'A1',role:'first_frame',path:'首帧.png',sha256:entry.sha256,bytes:4,duration_ms:null,origin_model:null}];
      ManjuWorkbench.renderAssets();
    }''')
    with page.expect_download() as event:page.locator('#export-workspace').click()
    out=tmp_path/'download.zip';event.value.save_as(out)
    result=verify_workspace(out);assert result['media_files']==1
    page.locator('#prompt').fill('新的暂存文字');page.locator('#human-confirmed').check()
    page.on('dialog',lambda d:d.accept())
    page.locator('#import-workspace').set_input_files(out)
    expect(page.locator('#workspace-status')).to_contain_text('已恢复')
    assert page.locator('#prompt').input_value()=='  原始未完成草稿\n '
    assert page.locator('#review-notes').input_value()==' 尚未提交的审片意见\n'
    assert not page.locator('#human-confirmed').is_checked()
    assert page.evaluate('ManjuWorkbench.state.files.size')==1
    assert page.evaluate('ManjuWorkbench.state.selected') is None
    with page.expect_download() as event:page.locator('#export-workspace').click()
    again=tmp_path/'second.zip';event.value.save_as(again)
    assert verify_workspace(again)['workspace_sha256']==result['workspace_sha256']


def test_browser_rejects_tamper_without_replacing_current_draft(page,tmp_path):
    out=archive(tmp_path/'bad.zip',changes=lambda e:e.update({'other.json':b'{}'}))
    page.locator('#prompt').fill('保留的工作');page.locator('#import-workspace').set_input_files(out)
    expect(page.locator('#status')).to_have_class('status error')
    assert page.locator('#prompt').input_value()=='保留的工作'


def test_python_archive_restores_in_browser(page,tmp_path):
    out=archive(tmp_path/'python.zip')
    page.on('dialog',lambda d:d.accept());page.locator('#import-workspace').set_input_files(out)
    expect(page.locator('#workspace-status')).to_contain_text('已恢复')
    assert page.evaluate('ManjuReview.state.pending.length')==1
    assert page.evaluate('ManjuReview.state.files.size')==1
    assert page.locator('#review-notes').input_value()==' 没写完\n'
