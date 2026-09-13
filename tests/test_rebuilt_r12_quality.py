from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import subprocess

import pytest
from typer.testing import CliRunner

from manju.authoring.core import Request, Catalog, load_catalog, digest, plan
from manju.authoring.quality import load_shortlist, quality_plan, Shortlist
from manju.authoring.cli import app
from tests.test_rebuilt_r5_workbench import browser, page

DAY=date(2026,9,8)
REQ=Request(shot_id='质量验证',task='create',prompt='固定镜头，主体抬头。',duration_s=5,resolution='1080p',aspect_ratio='16:9')


def test_original_ten_profiles_unchanged():
    root=Path(__file__).resolve().parents[1]
    old=json.loads(subprocess.check_output(['git','show','636d010:src/manju/authoring/data/catalog.json'],cwd=root))
    now=load_catalog().model_dump(mode='json')
    assert now['profiles'][:10]==old['profiles']
    assert len(now['profiles'])==12


def test_three_specific_identities_not_brand_names():
    p=quality_plan(REQ,today=DAY)
    assert {v['profile_id'] for v in p['options']}=={'gemini-omni-1.1-flash','wan3-standard','fal-h3-max'}
    assert p['selected_profile'] is None and not p['automatic_fallback'] and not p['cost_ranked']
    assert all(v['compatible'] for v in p['options'])
    assert any(x['profile_id']=='minimax-h3-max' for x in p['excluded'])
    assert {e.profile_id:e.profile_sha256 for e in load_shortlist().entries}=={
        p.id:digest(p) for p in load_catalog().profiles if p.id in {e.profile_id for e in load_shortlist().entries}}


def test_same_name_changed_contents_cannot_inherit_quality_evidence():
    c=load_catalog().model_dump(mode='json')
    next(p for p in c['profiles'] if p['id']=='wan3-standard')['notes'].append('changed')
    report=quality_plan(REQ,Catalog.model_validate(c),today=DAY)
    assert not any(x['profile_id']=='wan3-standard' for x in report['options'])
    assert any(x['profile_id']=='wan3-standard' and x['reason']=='profile_changed_requires_quality_recheck' for x in report['excluded'])


def test_empty_current_task_is_not_replaced_by_old_provider():
    report=quality_plan(Request(shot_id='表演',task='perform',prompt='保留表演'),today=DAY)
    assert report['options']==[] and report['selected_profile'] is None
    assert report['automatic_fallback'] is False


@pytest.mark.parametrize('day',[date(2026,9,8),date(2026,10,1),date(2026,9,7)])
def test_browser_python_quality_parity(page,day):
    expected=quality_plan(REQ,today=day)
    actual=page.evaluate('(v)=>ManjuQuality.qualityPlan(v.r,ManjuWorkbench.catalog(),v.day)',{'r':REQ.model_dump(mode='json'),'day':day.isoformat()})
    assert actual==expected


def test_ui_quality_default_and_no_auto_selection(page):
    assert page.locator('#quality-only').is_checked()
    page.locator('#prompt').fill('高质量，不自动降级')
    page.locator('#check-plan').click()
    page.wait_for_function('()=>ManjuWorkbench.state.plan!==null')
    assert page.locator('input[type=radio]').count() >= 3
    assert page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).count()==0
    assert page.evaluate('ManjuWorkbench.state.selected') is None
    page.locator('#quality-only').uncheck()
    assert page.evaluate('ManjuWorkbench.state.plan') is None
    page.locator('#check-plan').click()
    page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).wait_for()
    page.locator('#quality-only').check()
    assert page.locator('input[type=radio]').count()==0


def test_fal_unknown_duration_and_non_native_resolution_are_explicit():
    report=quality_plan(REQ,today=DAY)
    o=next(o for o in report['options'] if o['profile_id']=='fal-h3-max')
    assert 'duration_limit_not_verified:confirm_with_vendor' in o['warnings']
    assert any('768' in w and '1080' in w for w in o['warnings'])
    q=next(o for o in report['options'] if o['profile_id']=='gemini-omni-1.1-flash')
    assert 'leaderboard_score_not_verified_for_exact_successor' in q['warnings']


def test_cli_evidence_does_not_modify_request(tmp_path):
    p=tmp_path/'q.json';p.write_text(REQ.model_dump_json(), encoding='utf-8')
    before=p.read_bytes()
    result=CliRunner().invoke(app,['frontier-plan',str(p),'--on','2026-09-08'])
    assert result.exit_code==0,result.stdout
    assert json.loads(result.stdout)['automatic_fallback'] is False
    assert p.read_bytes()==before


def test_policy_rejects_duplicate_and_backwards_dates():
    obj=load_shortlist().model_dump(mode='json');obj['entries'].append(obj['entries'][0])
    with pytest.raises(ValueError):Shortlist.model_validate(obj)
    obj=load_shortlist().model_dump(mode='json');obj['review_after']='2020-01-01'
    with pytest.raises(ValueError):Shortlist.model_validate(obj)


def test_quality_deadline_warns_without_substituting_a_cheaper_model():
    report=quality_plan(REQ,today=date(2026,10,1))
    assert {x['profile_id'] for x in report['options']}=={x['profile_id'] for x in quality_plan(REQ,today=DAY)['options']}
    assert all('quality_evidence_review_overdue' in x['warnings'] for x in report['options'])
    assert not report['automatic_fallback']
    assert quality_plan(REQ,today=date(2026,9,7))['options']==[]


def test_wan_video_total_and_role_exclusivity():
    def asset(id,role,seconds=None):return dict(id=id,role=role,path=id+('.mp4' if seconds else '.png'),sha256=id[-1].lower()*64,bytes=100,duration_ms=seconds*1000 if seconds else None)
    request=Request(shot_id='参考',task='reference',prompt='保持镜头节奏',duration_s=5,resolution='1080p',aspect_ratio='16:9',assets=[asset('AA','reference_video',8),asset('AB','reference_video',8)])
    option=next(o for o in quality_plan(request,today=DAY)['options'] if o['profile_id']=='wan3-standard' and o['mode_id']=='reference-motion')
    assert not option['compatible'] and 'total_asset_duration_exceeded:reference_video' in option['errors']
    request=Request(shot_id='桥接',task='bridge',prompt='连接画面',assets=[asset('AA','first_frame'),asset('AB','last_frame'),asset('AC','subject_reference')])
    assert all(not o['compatible'] for o in quality_plan(request,today=DAY)['options'] if o['profile_id']=='wan3-standard')


def test_invalid_request_releases_export_lock(page):
    page.locator('#export-bundle').click()
    page.wait_for_function('()=>document.getElementById("status").classList.contains("error")')
    assert not page.evaluate('ManjuWorkbench.state.busy')
    assert not page.locator('#export-bundle').is_disabled()


def test_quality_evidence_export_matches_python(page,tmp_path):
    page.locator('#prompt').fill('记录证据')
    request=Request.model_validate(page.evaluate('ManjuWorkbench.getRequest()'))
    with page.expect_download() as info:page.locator('#export-quality').click()
    path=tmp_path/'quality.json';info.value.save_as(path)
    data=json.loads(path.read_text(encoding='utf-8'))
    day=date.fromisoformat(data['report']['evaluated_on'])
    assert data['report']==quality_plan(request,today=day)
    assert data['shortlist']==load_shortlist().model_dump(mode='json')
    assert page.evaluate('ManjuWorkbench.state.selected') is None


def test_generated_page_tracks_delivery_identity_and_external_scope():
    import json
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    version=json.loads((root/'DELIVERY_VERSION.json').read_text(encoding='utf-8'))['version']
    html=(root/'tools/model_workbench.html').read_text(encoding='utf-8')
    assert f'<span class="release">{version} ·' in html
    assert '本地占位预演' in html
    assert html==(root/'src/manju/authoring/data/workbench.html').read_text(encoding='utf-8')


def test_r12_delivery_verifier_accepts_contiguous_history_without_changing_gate(tmp_path):
    import zipfile
    from tests.test_rebuilt_r7_delivery import package as old_fixture, V, seal
    root=old_fixture.__wrapped__(tmp_path)
    meta=json.loads((root/'PACKAGE.json').read_text(encoding='utf-8'))
    meta.update(stage='R12',version='0.2.0+r12',inherits=[f'R{i}' for i in range(2,12)])
    for name in ('PACKAGE.json','source/DELIVERY_VERSION.json'):
        (root/name).write_text(json.dumps(meta), encoding='utf-8')
    data=b'__version__="0.2.0+r12"\n'
    (root/'source/src/manju/__init__.py').write_bytes(data)
    with zipfile.ZipFile(root/meta['wheel'],'w') as archive:archive.writestr('manju/__init__.py',data)
    seal(root)
    assert V.verify(root)['stage']=='R12'
    meta['inherits'].remove('R11')
    for name in ('PACKAGE.json','source/DELIVERY_VERSION.json'):
        (root/name).write_text(json.dumps(meta), encoding='utf-8')
    seal(root)
    with pytest.raises(ValueError,match='contiguous'):V.verify(root)
