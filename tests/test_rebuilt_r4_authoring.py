from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner
from manju.authoring.core import *
from manju.authoring.cli import app

NOW=datetime(2026,9,6,12,tzinfo=timezone.utc)

def req(**kw):
    return Request(shot_id='镜头01',prompt='单个连续镜头，雨逐渐停下。',task=kw.pop('task','create'),
                   duration_s=kw.pop('duration_s',8),resolution=kw.pop('resolution','720p'),
                   aspect_ratio=kw.pop('aspect_ratio','16:9'),**kw)

def asset(role='first_frame',name='首帧.png',content=b'fixture',**kw):
    return Asset(id=kw.pop('id','A1'),role=role,path=name,sha256=sha256(content).hexdigest(),bytes=len(content),**kw)

def options(r,c=None,day=date(2026,9,6)):
    return plan(r,c,today=day)['options']

def opt(r,profile,mode,c=None):
    return next(o for o in options(r,c) if o['profile_id']==profile and o['mode_id']==mode)

def approved(r,c=None,profile='veo-3.1-generate-preview',mode='text'):
    return approve(r,c or load_catalog(),profile,mode,reviewer='离线测试者',human_confirmed=True,acknowledge_warnings=True,now=NOW)

def test_catalog_current_specificity():
    c=load_catalog()
    # Catalogs are extensible; pin inherited identities rather than a count.
    assert {
        'minimax-h3','minimax-h3-max','gemini-omni-1.1-flash',
        'veo-3.1-generate-preview','veo-3.1-fast-generate-preview',
        'runway-keyframes-app','runway-edit-studio','luma-ray3-modify'
    }.issubset({p.id for p in c.profiles})
    assert all(p.status=='authoring_only' for p in c.profiles)
    fast=next(p for p in c.profiles if p.id=='minimax-h3-max')
    assert all(m.task!='reference' for m in fast.modes)
    assert next(p for p in c.profiles if p.id=='runway-edit-studio').entry=='web_app_only'
    assert next(p for p in c.profiles if p.id=='gemini-omni-1.1-flash').model_id=='gemini-omni-1.1-flash'

def test_no_autoselect():
    p=plan(req(),today=NOW.date());assert p['selected_profile'] is None and not p['ranked_by_quality']

def test_minimax_modes_and_ratio():
    r=req(task='bridge',assets=[asset(),asset('last_frame','尾帧.png',id='A2')],resolution='768P',aspect_ratio='adaptive')
    assert opt(r,'minimax-h3','first-last')['compatible']
    r.assets.append(asset('subject_reference','人物.png',id='A3'))
    assert not opt(r,'minimax-h3','first-last')['compatible']

def test_minimax_model_limits():
    r=req(duration_s=4,resolution='2K')
    assert opt(r,'minimax-h3','text')['compatible']
    assert not opt(r,'minimax-h3-max','text')['compatible']

@pytest.mark.parametrize('res,dur,valid',[('720p',4,True),('1080p',4,False),('4k',8,True),('2K',8,False)])
def test_veo_coupled_limits(res,dur,valid):
    assert opt(req(resolution=res,duration_s=dur),'veo-3.1-generate-preview','text')['compatible']==valid

def test_veo_extension_not_arbitrary_upload():
    a=asset('source_video','源.mp4')
    r=req(task='extend',assets=[a])
    assert not opt(r,'veo-3.1-generate-preview','veo-extend')['compatible']
    a.origin_model='veo-3.1'
    assert opt(r,'veo-3.1-generate-preview','veo-extend')['compatible']

def test_omni_unknown_is_warning_audio_unsupported():
    o=opt(req(),'gemini-omni-1.1-flash','text')
    assert o['compatible'] and any('duration_limit_not_verified' in w for w in o['warnings'])
    r=req(task='edit',assets=[asset('source_video','源.mp4',duration_ms=11000)],preserve=['衣服'],change=['背景'])
    assert not opt(r,'gemini-omni-1.1-flash','uploaded-edit')['compatible']

def test_asset_duration_unknown_requires_measurement():
    r=req(task='edit',assets=[asset('source_video','源.mp4')],preserve=['衣服'],change=['背景'])
    assert any('asset_duration_required' in e for e in opt(r,'gemini-omni-1.1-flash','uploaded-edit')['errors'])

def test_total_reference_duration():
    r=req(task='reference',resolution='768P',assets=[asset('reference_video','a.mp4',duration_ms=8000),asset('reference_video','b.mp4',duration_ms=8000,id='B')])
    assert any('total_asset_duration' in e for e in opt(r,'minimax-h3','references')['errors'])

@pytest.mark.parametrize('params',[{'preserve':['服装'],'change':['服装']},{'task':'edit'},{'stage':'final'}, {'duration_s':True}])
def test_request_rejects_invalid(params):
    with pytest.raises(ValidationError):req(**params)

@pytest.mark.parametrize('name',['../a.png','/a.png','x\\a.png','CON.png','a./b.png','x:y.png','AUX/x.png'])
def test_unsafe_paths(name):
    with pytest.raises((AuthoringError,ValidationError)):asset(name=name)

def test_case_collision():
    with pytest.raises(ValidationError):
        req(assets=[asset(name='a.png'),asset(name='A.png',id='B')])

def test_unknown_catalog_keys_and_claims():
    c=load_catalog().model_dump(mode='json');c['remote_execute']=True
    with pytest.raises(ValidationError):Catalog.model_validate(c)

def test_source_url_no_credentials():
    c=load_catalog().model_dump(mode='json');c['profiles'][0]['source_url']='https://token:secret@example.org/a'
    with pytest.raises(ValidationError):Catalog.model_validate(c)

def test_evidence_staleness_not_silent():
    rows=options(req(),day=date(2027,1,1))
    assert all(any('overdue' in w for w in r['warnings']) for r in rows)
    assert all(not r['compatible'] for r in options(req(),day=date(2026,1,1)))

def test_approval_explicit_and_warning_ack():
    for human,ack in [(False,False),(False,True),(True,False)]:
        with pytest.raises(AuthoringError):approve(req(),load_catalog(),'veo-3.1-generate-preview','text',reviewer='我',human_confirmed=human,acknowledge_warnings=ack,now=NOW)
    assert not approved(req()).external_execution_authorized

def test_changed_request_or_catalog_invalidates_approval():
    r=req();c=load_catalog();a=approved(r,c)
    r.prompt='新镜头'
    with pytest.raises(AuthoringError):verify_approval(r,c,a)
    r=req();c.revision='new'
    with pytest.raises(AuthoringError):verify_approval(r,c,a)

def make_bundle(tmp_path):
    (tmp_path/'首帧.png').write_bytes(b'fixture')
    r=req(task='animate',assets=[asset()]);c=load_catalog();a=approved(r,c,mode='first')
    out=write_bundle(r,c,a,tmp_path,tmp_path/'新交接包')
    return r,c,a,out

def test_bundle_chinese_hash_and_no_overwrite(tmp_path):
    r,c,a,out=make_bundle(tmp_path)
    assert verify_bundle(out)['ok']
    assert (out/'assets/首帧.png').read_bytes()==b'fixture'
    assert (tmp_path/'首帧.png').read_bytes()==b'fixture'
    with pytest.raises(AuthoringError):write_bundle(r,c,a,tmp_path,out)

def test_asset_changed_refuses_output(tmp_path):
    (tmp_path/'首帧.png').write_bytes(b'changed')
    r=req(task='animate',assets=[asset()]);c=load_catalog()
    with pytest.raises(AuthoringError):write_bundle(r,c,approved(r,c,mode='first'),tmp_path,tmp_path/'out')
    assert not (tmp_path/'out').exists()

@pytest.mark.parametrize('attack',['bytes','extra','brief_rehash','asset_rehash','symlink'])
def test_tampering(tmp_path,attack):
    r,c,a,out=make_bundle(tmp_path)
    if attack=='bytes':(out/'assets/首帧.png').write_bytes(b'changed')
    if attack=='extra':(out/'extra.txt').write_text('x')
    if attack.endswith('_rehash'):
        p=out/('BRIEF.md' if attack=='brief_rehash' else 'assets/首帧.png');p.write_text('forged')
        manifest=load_json(out/'MANIFEST.json');manifest['files'][p.relative_to(out).as_posix()]=file_digest(p)
        (out/'MANIFEST.json').write_text(json.dumps(manifest))
    if attack=='symlink':
        (out/'assets/首帧.png').unlink();(out/'assets/首帧.png').symlink_to(tmp_path/'首帧.png')
    with pytest.raises(AuthoringError):verify_bundle(out)

def test_json_duplicate_nonfinite(tmp_path):
    for text in ['{"x":1,"x":2}','{"x":NaN}']:
        p=tmp_path/'a.json';p.write_text(text)
        with pytest.raises(AuthoringError):load_json(p)

def test_cli_real_cycle_and_exclusive_output(tmp_path):
    runner=CliRunner();r=tmp_path/'req.json';a=tmp_path/'approval.json';b=tmp_path/'bundle'
    assert runner.invoke(app,['example','--output',str(r)]).exit_code==0
    assert runner.invoke(app,['example','--output',str(r)]).exit_code==2
    p=runner.invoke(app,['plan',str(r)]); assert p.exit_code==0 and json.loads(p.stdout)['selected_profile'] is None
    assert runner.invoke(app,['approve',str(r),'--profile','veo-3.1-generate-preview','--mode','text','--reviewer','测试','--human-confirmed','--acknowledge-warnings','--output',str(a)]).exit_code==0
    result=runner.invoke(app,['bundle',str(r),str(a),'--output',str(b)])
    assert result.exit_code==0,result.stdout
    assert runner.invoke(app,['verify',str(b)]).exit_code==0

def test_luma_fifth_second_not_falsely_last_frame():
    r=req(task='edit',assets=[asset('source_video','源.mp4',duration_ms=8000),asset('last_frame','尾帧.png',id='B')],preserve=['衣服'],change=['背景'])
    assert 'last_frame_is_not_clip_end_for_this_input_length' in opt(r,'luma-ray3-modify','modify')['errors']
    r.assets[0].duration_ms=4000
    assert opt(r,'luma-ray3-modify','modify')['compatible']
