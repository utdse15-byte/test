"""New catalog declarations, revision control, race refusal and real browser parity."""
from copy import deepcopy
from datetime import date
from pathlib import Path
import importlib.util
import json
import pytest
from typer.testing import CliRunner
from playwright.sync_api import expect
from manju.authoring.core import Catalog, Request, load_catalog, plan, digest, approve, write_bundle, verify_bundle
from manju.authoring.catalog_review import review_catalog
from manju.authoring.cli import app
from tests.test_rebuilt_r5_workbench import browser, page, REPO

DAY=date(2026,9,6)
OLD=REPO/'src/manju/authoring/data/catalog_r7.json'

def request(**kw):
    return Request.model_validate(dict(shot_id='S001',task='create',prompt='保持一个连续镜头',duration_s=8,resolution='720p',aspect_ratio='16:9',**kw))


def test_new_profiles_do_not_silently_rewrite_legacy():
    old,new=load_catalog(OLD),load_catalog()
    lookup={p.id:p for p in new.profiles}
    assert all(digest(p)==digest(lookup[p.id]) for p in old.profiles)
    assert set(lookup)-{p.id for p in old.profiles}=={'runway-gen-4-5-web'}
    report=review_catalog(old,new,request(),today=DAY)
    assert len(report['field_changes'])==1 and len(report['task_impact'])==1
    assert not any(report[k] for k in ('remote_sources_verified','automatic_apply','automatic_approval','automatic_model_selection'))


def test_legacy_bundle_verifies_with_embedded_old_catalog(tmp_path):
    r=request();c=load_catalog(OLD)
    a=approve(r,c,'veo-3.1-generate-preview','text',reviewer='旧档验证',human_confirmed=True,acknowledge_warnings=True)
    folder=write_bundle(r,c,a,tmp_path,tmp_path/'legacy')
    assert verify_bundle(folder)['ok']


@pytest.mark.parametrize('when,state',[('2026-09-05','future_dated'),('2026-09-06','within_review_window'),('2026-10-06','within_review_window'),('2026-10-07','review_overdue')])
def test_evidence_dates_are_not_verified_sources(when,state):
    c=load_catalog();r=review_catalog(c,c,today=date.fromisoformat(when))
    assert next(x for x in r['evidence_freshness'] if x['profile_id']=='gemini-omni-1.1-flash')['state']==state
    assert r['task_impact'] is None and r['field_changes']==[]
    assert not r['remote_sources_verified']


def test_field_and_mode_changes_describe_lost_compatibility():
    old=load_catalog();v=old.model_dump(mode='json');p=next(p for p in v['profiles'] if p['id']=='runway-gen-4-5-web')
    p['modes'][0]['duration_range']=[2,4];p['modes'].pop();v['revision']='test-change'
    report=review_catalog(old,Catalog.model_validate(v),request(),today=DAY)
    assert any(x['path'].endswith('duration_range') for x in report['field_changes'])
    item=next(x for x in report['task_impact'] if x['profile_id']==p['id'])
    assert item['before']['compatible'] and not item['after']['compatible']
    assert 'duration_out_of_range' in item['after']['errors']


def test_omni_does_not_invent_duration_or_audio_reference():
    rows=plan(request(),today=DAY)['options'];omni=next(r for r in rows if r['profile_id']=='gemini-omni-1.1-flash')
    assert omni['compatible'] and 'duration_limit_not_verified:confirm_with_vendor' in omni['warnings']
    c=next(p for p in load_catalog().profiles if p.id=='gemini-omni-1.1-flash')
    assert all('reference_audio' not in m.roles for m in c.modes)
    assert all(m.duration_range is None and m.durations is None for m in c.modes)
    assert any('上采样' in n for n in c.notes)


@pytest.mark.parametrize('duration,okay',[(9999,True),(10000,True),(10001,False),(None,False)])
def test_omni_uploaded_edit_input_limit(duration,okay):
    r=Request(shot_id='edit',task='edit',prompt='改变光线',preserve=['服装'],change=['光线'],assets=[dict(id='V1',path='a.mp4',sha256='a'*64,bytes=100,role='source_video',duration_ms=duration)])
    item=next(x for x in plan(r,today=DAY)['options'] if x['profile_id']=='gemini-omni-1.1-flash')
    assert item['compatible'] is okay


@pytest.mark.parametrize('when',['2026-09-05','2026-09-06','2026-10-08'])
def test_whole_report_browser_python_parity(page,when):
    old=load_catalog(OLD);new=load_catalog();r=request()
    expected=review_catalog(old,new,r,today=date.fromisoformat(when))
    actual=page.evaluate('x=>ManjuCatalogReview.review(x.a,x.b,x.r,x.day)',dict(a=old.model_dump(mode='json'),b=new.model_dump(mode='json'),r=r.model_dump(mode='json'),day=when))
    assert actual==expected


def test_cli_new_report_is_read_only_and_exclusive(tmp_path):
    after=tmp_path/'after.json';after.write_text(load_catalog().model_dump_json())
    req=tmp_path/'request.json';req.write_text(request().model_dump_json());out=tmp_path/'report.json'
    runner=CliRunner();args=['catalog-review',str(OLD),str(after),'--request',str(req),'--on','2026-09-06','--output',str(out)]
    result=runner.invoke(app,args);assert result.exit_code==0,result.stdout
    assert json.loads(out.read_text())==review_catalog(load_catalog(OLD),load_catalog(),request(),today=DAY)
    assert runner.invoke(app,args).exit_code==2


def test_import_is_staged_apply_clears_confirmation_and_revert_previews(page,tmp_path):
    page.locator('#prompt').fill('当前镜头文字不能改');page.locator('#check-plan').click()
    page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).check();page.locator('#human-confirmed').check()
    base=page.evaluate('ManjuWorkbench.catalog().revision')
    page.locator('#import-catalog').set_input_files(OLD)
    expect(page.locator('#catalog-summary')).to_contain_text('字段变化')
    assert page.evaluate('ManjuWorkbench.catalog().revision')==base
    assert page.locator('#human-confirmed').is_checked()
    page.locator('#apply-catalog').click();expect(page.locator('#status')).to_contain_text('明确确认')
    page.locator('#catalog-confirmed').check();page.locator('#apply-catalog').click()
    expect(page.locator('#status')).to_contain_text('已替换能力档')
    assert not page.locator('#human-confirmed').is_checked()
    assert page.locator('#prompt').input_value()=='当前镜头文字不能改'
    assert page.evaluate('ManjuWorkbench.state.selected') is None
    oldrev=page.evaluate('ManjuWorkbench.catalog().revision')
    page.locator('#catalog-revert').click();expect(page.locator('#catalog-summary')).to_contain_text(base)
    assert page.evaluate('ManjuWorkbench.catalog().revision')==oldrev
    page.locator('#catalog-confirmed').check();page.locator('#apply-catalog').click()
    expect(page.locator('#status')).to_contain_text('已替换能力档')
    assert page.evaluate('ManjuWorkbench.catalog().revision')==base


def test_changed_task_refuses_stale_preview(page):
    page.locator('#prompt').fill('旧镜头');page.locator('#import-catalog').set_input_files(OLD)
    expect(page.locator('#catalog-summary')).to_contain_text('字段变化')
    base=page.evaluate('ManjuWorkbench.catalog().revision');page.locator('#prompt').fill('新镜头')
    page.locator('#catalog-confirmed').check();page.locator('#apply-catalog').click()
    expect(page.locator('#status')).to_contain_text('工作现场已改变')
    assert page.evaluate('ManjuWorkbench.catalog().revision')==base
    page.locator('#catalog-refresh').click();expect(page.locator('#status')).to_contain_text('已预览')
    assert not page.locator('#catalog-confirmed').is_checked()


def test_rapid_overlapping_previews_only_latest_wins(page):
    old=load_catalog(OLD).model_dump(mode='json');new=load_catalog().model_dump(mode='json')
    result=page.evaluate('async x=>{await Promise.all([ManjuCatalogReview.preview(x.a),ManjuCatalogReview.preview(x.b)]);return ManjuCatalogReview.state.pending.revision;}',dict(a=old,b=new))
    assert result==new['revision']


@pytest.mark.parametrize('value',['{"a":1,"a":2}','{"a":1,"\\u0061":2}','{"x":{"z":1,"z":2}}','[NaN]','[1e500]','{} trailing','{"a":1,}','['*70+'0'+']'*70])
def test_strict_json_rejects_ambiguous_data(page,value):
    result=page.evaluate('s=>{try{ManjuCatalogReview.strictJSON(s);return false;}catch(e){return true;}}',value)
    assert result


@pytest.mark.parametrize('value',[{},[1,False,None,3.25],{'换行':'文字\n"\\','a':[{'b':2}]},{'__proto__':{'polluted':True}},'string',-5e-4])
def test_strict_json_matches_json_data(page,value):
    # JSON round-trip avoids Playwright's special-key transport sanitization.
    result=page.evaluate('s=>JSON.stringify(ManjuCatalogReview.strictJSON(s))',json.dumps(value,ensure_ascii=False))
    assert json.loads(result)==value
    assert not page.evaluate('Boolean({}.polluted)')


def test_duplicate_catalog_does_not_change_state(page,tmp_path):
    value=tmp_path/'duplicate.json';data=load_catalog().model_dump_json();value.write_text(data.replace('"revision":','"revision":"ignored", "revision":',1))
    before=page.evaluate('ManjuWorkbench.catalog().revision')
    page.locator('#import-catalog').set_input_files(value);expect(page.locator('#status')).to_contain_text('重复字段')
    assert page.evaluate('ManjuWorkbench.catalog().revision')==before


def test_catalog_preview_no_mobile_overflow(page):
    page.set_viewport_size({'width':390,'height':844});page.locator('#import-catalog').set_input_files(OLD)
    expect(page.locator('#catalog-summary')).to_contain_text('字段变化')
    page.locator('#catalog-preview summary').click()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')


def _packager():
    spec=importlib.util.spec_from_file_location('r10_packager',REPO/'tools/rebuilt-delivery/build_cumulative.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_publication_never_unlinks_a_racing_existing_file(tmp_path):
    m=_packager();src=tmp_path/'src';dst=tmp_path/'output';src.write_bytes(b'candidate');dst.write_bytes(b'other producer')
    with pytest.raises(FileExistsError):m.publish_new(src,dst)
    assert dst.read_bytes()==b'other producer'


def test_publication_cleans_only_owned_partial_file(tmp_path,monkeypatch):
    m=_packager();src=tmp_path/'src';dst=tmp_path/'output';src.write_bytes(b'candidate')
    def broken(a,b):b.write(b'partial');raise OSError('injected storage fault')
    monkeypatch.setattr(m.shutil,'copyfileobj',broken)
    with pytest.raises(OSError):m.publish_new(src,dst)
    assert not dst.exists() and src.read_bytes()==b'candidate'


def test_return_report_long_hash_wraps_on_narrow_screen(page,tmp_path):
    # Exercise the actual R9 report renderer, not a simplified layout fixture.
    import subprocess
    movie=tmp_path/'候选.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=320x180:r=24','-t','1','-c:v','libx264','-threads','1',str(movie)],check=True,capture_output=True,timeout=20)
    page.locator('#prompt').fill('布局回归');page.locator('#review-files').set_input_files(movie)
    expect(page.locator('#review-status')).to_contain_text('候选文件已核对')
    page.locator('#return-section > summary').click();page.locator('#inspect-returns').click()
    expect(page.locator('#status')).to_contain_text('返回规格检查完成')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
