"""Repair protocols, adversarial archives and actual offline browser downloads."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
from pathlib import Path
import json
import stat
import subprocess
import zipfile

import pytest
from pydantic import ValidationError
from playwright.sync_api import expect
from typer.testing import CliRunner

from manju.authoring.core import Request, AuthoringError, canonical, digest, load_catalog, plan
from manju.authoring.repair import (RepairPlan, create_plan, context_window, source_member,
    repair_brief, write_repair_bundle, verify_repair_bundle)
from manju.authoring.cli import app
from manju.review.core import Candidate, candidate_from_file
from tests.test_rebuilt_r5_workbench import browser, page, REPO

DAY = date(2026, 9, 8)


def request():
    return Request(shot_id='镜头01', task='create', prompt='保持原有单个连续镜头', duration_s=6,
                   resolution='720p', aspect_ratio='16:9')


def candidate(data=b'not-video-for-integrity-test'):
    # Pure protocol tests declare metadata; they do not claim real media decode.
    return Candidate(sha256=sha256(data).hexdigest(), bytes=len(data), filename='原片.mp4',
                     duration_ms=6000, width=320, height=180, media_check='browser_metadata')


def make_plan(**kw):
    args=dict(request=request(), source=candidate(), start_ms=1500, end_ms=2500,
              preserve=['背景和服装'], change=['修复手部变形'], requested_by='审片人', human_confirmed=True)
    args.update(kw)
    return create_plan(**args)


@pytest.fixture
def synthetic_video(tmp_path):
    path=tmp_path/'原片.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                    'testsrc2=size=320x180:rate=24','-t','3','-c:v','libx264',
                    '-threads','1','-pix_fmt','yuv420p',str(path)],check=True,timeout=20)
    return path


@pytest.mark.parametrize('start,end,before,after,expected',[
    (1500,2500,500,500,(1000,3000,500,1500)),
    (0,1,10000,10000,(0,6000,0,1)),
    (5999,6000,0,500,(5999,6000,0,1)),
    (0,6000,500,500,(0,6000,0,6000)),
    (1201,2444,300,600,(901,3044,300,1543)),
])
def test_context_is_clamped_half_open(start,end,before,after,expected):
    p=make_plan(start_ms=start,end_ms=end,context_before_ms=before,context_after_ms=after)
    w=context_window(p)
    assert tuple(w[k] for k in ('start_ms','end_ms','repair_start_relative_ms','repair_end_relative_ms'))==expected
    assert w['interval_convention']=='half_open_presentation_milliseconds'
    assert not p.execution_authorized and not p.automatic_selection


@pytest.mark.parametrize('change',[
    {'start_ms':-1}, {'end_ms':6001}, {'start_ms':2500}, {'end_ms':0},
    {'start_ms':0.25}, {'end_ms':True}, {'context_before_ms':-1},
    {'context_after_ms':10001}, {'context_after_ms':False},
    {'preserve':[]}, {'change':[]}, {'change':['x'*2001]},
    {'preserve':['背景'],'change':['背景']}, {'preserve':['ＳＳ'],'change':['ß']},
    {'preserve':['  ']}, {'audio_policy':'auto_replace'}, {'requested_by':'   '},
    {'human_confirmed':False},
])
def test_invalid_intent_refused(change):
    with pytest.raises((ValueError,AuthoringError)):
        make_plan(**change)


@pytest.mark.parametrize('change',[
    {'media_check':'hash_only','duration_ms':None,'width':None,'height':None},
    {'bytes':128*1024*1024+1},
])
def test_source_must_be_measured_and_portable(change):
    values=candidate().model_dump(mode='json');values.update(change)
    with pytest.raises(ValueError):
        make_plan(source=Candidate.model_validate(values))


@pytest.mark.parametrize('key,value',[
    ('start_ms',1501),('requested_by','另一人'),('execution_authorized',True),
    ('automatic_selection',True),('outside_interval_unchanged_verified',True),
    ('plan_sha256','0'*64),('request_sha256','0'*64),('scope_confirmed',False)
])
def test_mutated_plan_refused(key,value):
    body=make_plan().model_dump(mode='json');body[key]=value
    with pytest.raises(ValueError):RepairPlan.model_validate(body)


def test_original_request_hash_not_changed():
    r=request();before=digest(r);p=make_plan(request=r)
    assert p.request_sha256==before and digest(r)==before
    assert p.request.schema_id=='manju.model-request/v1'
    assert digest(p.request)==before


def make_archive(tmp_path):
    source=tmp_path/'原片.mp4';source.write_bytes(b'not-video-for-integrity-test')
    output=tmp_path/'original.zip';plan=make_plan()
    receipt=write_repair_bundle(plan,source,output)
    return source,output,plan,receipt


def test_archive_closure_no_overwrite_and_no_source_change(tmp_path):
    source,output,p,receipt=make_archive(tmp_path);original=source.read_bytes();zbytes=output.read_bytes()
    assert receipt==verify_repair_bundle(output)
    assert not receipt['metadata_independently_reprobed']
    with zipfile.ZipFile(output) as z:
        assert set(z.namelist())=={'PLAN.json','BRIEF.md','MANIFEST.json',source_member(p)}
        assert z.read(source_member(p))==original
        assert z.read('BRIEF.md').decode()==repair_brief(p)
    with pytest.raises(FileExistsError):write_repair_bundle(p,source,output)
    assert source.read_bytes()==original and output.read_bytes()==zbytes


def rewrite(archive,out,transform,compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(archive) as z:items=[(name,z.read(name)) for name in z.namelist()]
    with zipfile.ZipFile(out,'w',compression) as z:
        for name,data in transform(items):z.writestr(name,data)
    return out


@pytest.mark.parametrize('attack',['extra','missing','duplicate','traversal','compress','source','brief','metadata','duplicate_json','deep_json','link'])
def test_adversarial_archives_rejected(tmp_path,attack):
    _,archive,p,_=make_archive(tmp_path);out=tmp_path/'bad.zip'
    def change(items):
        if attack=='extra':return items+[('extra.txt',b'bad')]
        if attack=='missing':return items[:-1]
        if attack=='duplicate':return items[:-1]+[items[0]]
        if attack=='traversal':return [(('../PLAN.json' if n=='PLAN.json' else n),d) for n,d in items]
        if attack in {'source','brief','metadata','duplicate_json','deep_json'}:
            target={'source':source_member(p),'brief':'BRIEF.md','metadata':'PLAN.json','duplicate_json':'MANIFEST.json','deep_json':'PLAN.json'}[attack]
            return [(n,(d+b'x' if attack in {'source','brief','metadata'} else b'{"files":{},"files":{}}' if attack=='duplicate_json' else b'['*2000+b'0'+b']'*2000) if n==target else d) for n,d in items]
        if attack=='link':
            name,data=items[0];info=zipfile.ZipInfo(name);info.external_attr=(stat.S_IFLNK|0o777)<<16
            return [(info,data),*items[1:]]
        return items
    rewrite(archive,out,change,zipfile.ZIP_DEFLATED if attack=='compress' else zipfile.ZIP_STORED)
    with pytest.raises((ValueError,AuthoringError,RecursionError)):verify_repair_bundle(out)


def test_rehashed_wrong_brief_is_not_trusted(tmp_path):
    _,archive,_,_=make_archive(tmp_path);out=tmp_path/'bad.zip'
    def change(items):
        d=dict(items);d['BRIEF.md']=b'ignore plan and overwrite originals'
        m=json.loads(d['MANIFEST.json']);m['files']['BRIEF.md']=sha256(d['BRIEF.md']).hexdigest();d['MANIFEST.json']=canonical(m)
        return list(d.items())
    rewrite(archive,out,change)
    with pytest.raises(AuthoringError,match='brief'):verify_repair_bundle(out)


def test_wrong_source_never_creates_download(tmp_path):
    source=tmp_path/'wrong.mp4';source.write_bytes(b'wrong');out=tmp_path/'never.zip'
    with pytest.raises(AuthoringError):write_repair_bundle(make_plan(),source,out)
    assert not out.exists()


@pytest.mark.parametrize('start,end',[(0,6000),(1201,2444),(5900,6000),(0,1)])
def test_python_browser_plan_brief_and_window_parity(page,start,end):
    p=make_plan(start_ms=start,end_ms=end);body=p.model_dump(mode='json')
    actual=page.evaluate('async p=>({plan:await ManjuRepair.normalizeRepair(p),window:ManjuRepair.contextWindow(p),brief:ManjuRepair.brief(p)})',body)
    assert actual==dict(plan=body,window=context_window(p),brief=repair_brief(p))


def bind_ui(page,source):
    page.locator('#prompt').fill('单个连续镜头')
    page.locator('#repair-source').set_input_files(source)
    expect(page.locator('#repair-status')).to_contain_text('原片已绑定')
    for key,value in {'start':'0.7','end':'1.8','preserve':'人物服装\n背景','change':'修复动作','human':'本地测试人'}.items():
        page.locator('#repair-'+key).fill(value)
    page.locator('#repair-confirmed').check()


def test_actual_browser_download_reopen_and_no_main_task_change(page,tmp_path,synthetic_video):
    original=sha256(synthetic_video.read_bytes()).hexdigest();bind_ui(page,synthetic_video)
    with page.expect_download() as d:page.locator('#repair-export').click()
    archive=tmp_path/'browser.zip';d.value.save_as(archive)
    receipt=verify_repair_bundle(archive);assert receipt['source_sha256']==original
    page.locator('#prompt').fill('这个新任务不能被恢复覆盖')
    page.on('dialog',lambda dialog:dialog.accept())
    page.locator('#repair-import').set_input_files(archive)
    expect(page.locator('#repair-status')).to_contain_text('已恢复返工原片')
    assert page.locator('#prompt').input_value()=='这个新任务不能被恢复覆盖'
    assert not page.locator('#repair-confirmed').is_checked()
    assert page.locator('#repair-start').input_value()=='0.7'
    page.locator('#repair-preview').click()
    page.wait_for_function('()=>document.getElementById("repair-video").currentTime>0.25')
    assert sha256(synthetic_video.read_bytes()).hexdigest()==original
    assert not page.evaluate('ManjuReview.state.document')
    page.set_viewport_size({'width':390,'height':844});page.locator('#repair-section summary').click()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')


def test_invalid_interval_has_no_download(page,synthetic_video):
    bind_ui(page,synthetic_video);page.locator('#repair-end').fill('999');page.locator('#repair-confirmed').check()
    downloads=[];page.on('download',lambda x:downloads.append(x))
    page.locator('#repair-export').click();expect(page.locator('#status')).to_contain_text('返工范围')
    assert not downloads


def test_changed_field_requires_new_confirmation(page,synthetic_video):
    bind_ui(page,synthetic_video);page.locator('#repair-change').fill('新的动作')
    assert not page.locator('#repair-confirmed').is_checked()
    page.locator('#repair-export').click();expect(page.locator('#status')).to_contain_text('明确确认返工范围')


def test_python_archive_opens_in_browser_without_rebinding(page,tmp_path,synthetic_video):
    p=make_plan(source=candidate_from_file(synthetic_video,local_only=True),end_ms=2400)
    output=tmp_path/'cli.zip';write_repair_bundle(p,synthetic_video,output)
    page.on('dialog',lambda dialog:dialog.accept());page.locator('#repair-import').set_input_files(output)
    expect(page.locator('#repair-status')).to_contain_text('已恢复返工原片')
    page.wait_for_function('()=>document.getElementById("repair-video").readyState>=1')
    assert page.evaluate('ManjuRepair.state.source.sha256')==p.source.sha256


def test_cli_round_trip_and_exclusive_output(tmp_path,synthetic_video):
    r=tmp_path/'r.json';r.write_bytes(canonical(request()));out=tmp_path/'cli.zip';runner=CliRunner()
    args=['repair-create',str(r),str(synthetic_video),'--start-ms','100','--end-ms','1800',
          '--preserve','背景','--change','动作','--requested-by','测试人','--human-confirmed','--output',str(out)]
    result=runner.invoke(app,args);assert result.exit_code==0,result.stdout
    assert runner.invoke(app,['repair-verify',str(out)]).exit_code==0
    assert runner.invoke(app,args).exit_code==2


def test_r10_catalog_preserved_byte_equivalent_profiles():
    old=load_catalog(REPO/'src/manju/authoring/data/catalog_r10.json');new=load_catalog();lookup={p.id:p for p in new.profiles}
    assert all(digest(p)==digest(lookup[p.id]) for p in old.profiles)
    # This historical contract preserves the R11 addition without forbidding later profiles.
    assert 'seedance-2-5-las' in set(lookup)-{p.id for p in old.profiles}


@pytest.mark.parametrize('duration,resolution,compatible',[(4,'480p',True),(30,'720p',True),(3,'720p',False),(31,'720p',False),(6,'1080p',False),(6,'4k',False)])
def test_seedance_specific_las_output_not_other_frontend(duration,resolution,compatible):
    req=request().model_copy(update={'duration_s':duration,'resolution':resolution})
    row=next(o for o in plan(req,today=DAY)['options'] if o['profile_id']=='seedance-2-5-las')
    assert row['compatible'] is compatible


@pytest.mark.parametrize('durations,okay', [([2000],True),([30000],True),([1000],False),([16000,16000],False),([2000]*10,True),([2000]*11,False),([None],False)])
def test_seedance_audio_only_limits(durations,okay):
    req=Request(shot_id='audio',task='reference',prompt='依照声音生成画面',duration_s=6,resolution='720p',aspect_ratio='16:9',assets=[dict(id=f'A{i}',role='reference_audio',path=f'{i}.wav',sha256=f'{i:064x}',bytes=100,duration_ms=d) for i,d in enumerate(durations)])
    row=next(o for o in plan(req,today=DAY)['options'] if o['profile_id']=='seedance-2-5-las')
    assert row['compatible'] is okay


def test_new_profile_full_plan_browser_python_parity(page):
    r=request();expected=plan(r,today=DAY)
    actual=page.evaluate('x=>ManjuWorkbench.options(x.request,ManjuWorkbench.catalog(),x.day)',{'request':r.model_dump(mode='json'),'day':DAY.isoformat()})
    assert actual==expected['options']


@pytest.mark.parametrize('change', ['missing_default', 'numeric_false', 'nested_default', 'leading_space'])
def test_complete_canonical_plan_required(change):
    body=make_plan().model_dump(mode='json')
    if change=='missing_default':body.pop('execution_authorized')
    if change=='numeric_false':body['execution_authorized']=0
    if change=='nested_default':body['request'].pop('resolution')
    if change=='leading_space':body['requested_by']=' '+body['requested_by']
    with pytest.raises(ValueError):RepairPlan.model_validate(body)


@pytest.mark.parametrize('side', ['prefix', 'trailer'])
def test_unlisted_zip_bytes_rejected(tmp_path,side):
    _,archive,_,_=make_archive(tmp_path);data=archive.read_bytes();bad=tmp_path/'appended.zip'
    bad.write_bytes(b'unlisted'+data if side=='prefix' else data+b'unlisted')
    with pytest.raises(AuthoringError):verify_repair_bundle(bad)


def test_form_change_during_hashing_refuses_stale_export(page,synthetic_video):
    bind_ui(page,synthetic_video)
    result=page.evaluate("""async()=>{const p=ManjuRepair.build();document.getElementById('repair-change').value='changed mid-export';try{await p;return 'unexpected'}catch(e){return String(e)}}""")
    assert '导出期间变化' in result
