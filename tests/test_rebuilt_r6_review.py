from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import subprocess
import zipfile
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner
from manju.authoring.core import (Request, AuthoringError, digest, load_catalog, approve,
                                 write_bundle, verify_bundle, file_digest, load_json, write_new_json)
from manju.review.core import (Candidate, ReviewDocument, create_session, record_decision,
                              candidate_from_file, promote, validate_promotion, draft_member, report)
from manju.review.cli import app
import importlib.util
import os

@pytest.fixture(scope='module')
def browser():
    if not importlib.util.find_spec('playwright') or not shutil.which('chromium'):
        pytest.skip('real Chromium/Playwright unavailable')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'],timeout=15000)
        yield b
        b.close()

@pytest.fixture
def page(browser):
    context=browser.new_context(accept_downloads=True,viewport={'width':1280,'height':960})
    page=context.new_page()
    html=Path(__file__).resolve().parents[1]/'tools/model_workbench.html'
    if os.environ.get('MANJU_BROWSER_TEST_TRANSPORT')=='isolated_document':
        page.set_content(html.read_text(),wait_until='load')
    else:
        page.goto(html.as_uri(),wait_until='load',timeout=15000)
    yield page
    context.close()


NOW=datetime(2026,9,6,12,tzinfo=timezone.utc)

def request():
    return Request(shot_id='雨后镜头',task='create',prompt='一个连续镜头，光线缓慢变化。',duration_s=8,resolution='720p',aspect_ratio='16:9')

@pytest.fixture
def prepared(tmp_path):
    a=tmp_path/'候选甲.mp4';a.write_bytes(b'fake-video-byte-fixture-not-measured')
    b=tmp_path/'候选乙.mp4';b.write_bytes(b'another-video-byte-fixture-not-measured')
    candidates=[candidate_from_file(f,probe=False) for f in (a,b)]
    return create_session(request(),candidates,nonce='a'*32),a,b


def decided(doc,path,verdict='approve_draft'):
    return record_decision(doc,file_digest(path),path,verdict=verdict,reviewer='合成测试声明',notes='用于验证记录链，不是用户真实审片。',human_confirmed=True,criteria={'identity':4,'audio':None},declared_at='2026-09-06T12:00:00Z')


def test_no_auto_winner_and_immutable_decisions(prepared):
    doc,a,b=prepared;out=decided(doc,a)
    assert not doc.decisions and len(out.decisions)==1
    assert out.session.no_automatic_selection
    assert not out.decisions[0].picture_lock_authorized
    assert out.decisions[0].previous_decision_sha256 is None
    assert '候选甲.mp4' not in report(out)
    assert '候选甲.mp4' in report(out,reveal=True)


def test_duplicate_bytes_not_two_candidates(prepared):
    doc,a,b=prepared
    with pytest.raises(ValidationError):create_session(request(),[doc.session.candidates[0]]*2)


@pytest.mark.parametrize('field,value', [('prompt','新的剧情'),('duration_s',6),('aspect_ratio','9:16'),('shot_id','另一个镜头'),('preserve',['人物'])])
def test_creative_changes_require_new_draft(prepared,field,value):
    doc,a,b=prepared;doc=decided(doc,a);final=promote(doc,file_digest(a),a,human_confirmed=True)
    body=final.model_dump(mode='json');body[field]=value
    with pytest.raises(AuthoringError):validate_promotion(Request.model_validate(body),doc,a)


def test_promotion_resolution_only_and_actual_bytes(prepared):
    doc,a,b=prepared;doc=decided(doc,a);h=file_digest(a)
    final=promote(doc,h,a,human_confirmed=True,resolution='1080p')
    assert final.stage=='final' and final.approved_draft_sha256==h and final.resolution=='1080p'
    with pytest.raises(AuthoringError):promote(doc,h,a,human_confirmed=False)
    a.write_bytes(b'changed')
    with pytest.raises(AuthoringError):promote(doc,h,a,human_confirmed=True)


@pytest.mark.parametrize('verdict',['revise','reject'])
def test_latest_decision_revokes_in_this_snapshot(prepared,verdict):
    doc,a,b=prepared;approved=decided(doc,a);newer=decided(approved,a,verdict)
    assert len(approved.decisions)==1 and len(newer.decisions)==2
    assert newer.decisions[-1].previous_decision_sha256==approved.decisions[-1].decision_sha256
    with pytest.raises(AuthoringError):promote(newer,file_digest(a),a,human_confirmed=True)
    # A sealed older record is still historical evidence, not an online revocation lookup.
    assert promote(approved,file_digest(a),a,human_confirmed=True).stage=='final'


def test_pending_not_approved_and_human_required(prepared):
    doc,a,b=prepared
    with pytest.raises(AuthoringError):promote(doc,file_digest(a),a,human_confirmed=True)
    with pytest.raises(AuthoringError):record_decision(doc,file_digest(a),a,verdict='approve_draft',reviewer='我',notes='看过',human_confirmed=False)


@pytest.mark.parametrize('attack',['session','request','decision','order','previous','lock','score','nonce'])
def test_review_corruption_refused(prepared,attack):
    doc,a,b=prepared;value=decided(decided(doc,a),b).model_dump(mode='json')
    if attack=='session':value['session']['candidates'][0]['bytes']+=1
    if attack=='request':value['session']['request']['prompt']='已篡改'
    if attack=='decision':value['decisions'][0]['notes']='已篡改'
    if attack=='order':value['decisions'].reverse()
    if attack=='previous':value['decisions'][1]['previous_decision_sha256']=None
    if attack=='lock':value['decisions'][0]['picture_lock_authorized']=True
    if attack=='score':value['decisions'][0]['criteria']['identity']=True
    if attack=='nonce':value['session']['nonce']='b'*32
    with pytest.raises((ValidationError,AuthoringError)):ReviewDocument.model_validate(value)


def test_mutated_nested_model_revalidated(prepared):
    doc,a,b=prepared;approved=decided(doc,a);approved.decisions[0].criteria['identity']=1
    with pytest.raises(ValidationError):promote(approved,file_digest(a),a,human_confirmed=True)


def make_final(tmp_path,prepared):
    doc,a,b=prepared;doc=decided(doc,a);final=promote(doc,file_digest(a),a,human_confirmed=True,resolution='1080p')
    cat=load_catalog();approval=approve(final,cat,'veo-3.1-generate-preview','text',reviewer='测试',human_confirmed=True,acknowledge_warnings=True,now=NOW)
    out=tmp_path/'定稿交接'
    return final,cat,approval,doc,a,out


def test_final_bundle_requires_real_proof(tmp_path,prepared):
    final,cat,approval,doc,a,out=make_final(tmp_path,prepared)
    with pytest.raises(AuthoringError):write_bundle(final,cat,approval,tmp_path,out)
    assert not out.exists()
    write_bundle(final,cat,approval,tmp_path,out,draft_review=doc,draft_file=a)
    result=verify_bundle(out,require_promotion=True)
    assert result['promotion_verified'] and result['schema_id']=='manju.model-bundle/v2'
    assert (out/draft_member(doc.session.candidates[0])).read_bytes()==a.read_bytes()
    assert load_json(out/'DRAFT_REVIEW.json')['decisions'][0]['picture_lock_authorized'] is False
    assert not result['external_execution_authorized']
    with pytest.raises(AuthoringError):write_bundle(final,cat,approval,tmp_path,out,draft_review=doc,draft_file=a)


@pytest.mark.parametrize('attack',['video_rehash','record_rehash','extra_rehash','strip_proof'])
def test_final_bundle_tamper_rejected_even_manifest_rehashed(tmp_path,prepared,attack):
    final,cat,approval,doc,a,out=make_final(tmp_path,prepared)
    write_bundle(final,cat,approval,tmp_path,out,draft_review=doc,draft_file=a)
    if attack=='video_rehash':(out/draft_member(doc.session.candidates[0])).write_bytes(b'new')
    if attack=='record_rehash':
        value=load_json(out/'DRAFT_REVIEW.json');value['decisions'][0]['notes']='new';(out/'DRAFT_REVIEW.json').write_text(json.dumps(value))
    if attack=='extra_rehash':(out/'unapproved.txt').write_text('x')
    if attack=='strip_proof':(out/'DRAFT_REVIEW.json').unlink()
    manifest=load_json(out/'MANIFEST.json');manifest['files']={p.relative_to(out).as_posix():file_digest(p) for p in out.rglob('*') if p.is_file() and p.name!='MANIFEST.json'}
    (out/'MANIFEST.json').write_text(json.dumps(manifest))
    with pytest.raises((AuthoringError,ValidationError,OSError)):verify_bundle(out)


def test_r4_golden_preserved():
    root=Path(__file__).parent/'fixtures/authoring_r4'
    result=verify_bundle(root)
    assert result['request_sha256']=='2ff03c70513c83cf9bb03940c02cd4a3c86d6d4d9662776a15a0493a76dddd59'
    assert not result['promotion_verified']
    with pytest.raises(AuthoringError):verify_bundle(root,require_promotion=True)


def test_json_too_large_never_published(tmp_path):
    p=tmp_path/'too-large.json'
    with pytest.raises(AuthoringError):write_new_json(p,{'text':'中'*(1024*1024)})
    assert not p.exists()


def test_cli_review_pipeline(prepared,tmp_path):
    doc,a,b=prepared;r=tmp_path/'request.json';write_new_json(r,request())
    runner=CliRunner();s=tmp_path/'session.json';d=tmp_path/'decision.json';f=tmp_path/'final.json'
    out=runner.invoke(app,['create',str(r),'--candidate',str(a),'--candidate',str(b),'--hash-only','--output',str(s)])
    assert out.exit_code==0,out.stdout
    before=s.read_bytes()
    args=['decide',str(s),'--candidate',file_digest(a),'--file',str(a),'--verdict','approve_draft','--reviewer','我','--notes','合成声明','--human-confirmed','--output',str(d)]
    assert runner.invoke(app,args).exit_code==0
    assert s.read_bytes()==before
    assert runner.invoke(app,args).exit_code==2
    assert runner.invoke(app,['promote',str(d),'--candidate',file_digest(a),'--file',str(a),'--human-confirmed','--resolution','1080p','--output',str(f)]).exit_code==0
    assert load_json(f)['stage']=='final'


@pytest.fixture(scope='module')
def videos(tmp_path_factory):
    root=tmp_path_factory.mktemp('real-review-video')
    if not shutil.which('ffmpeg'):pytest.skip('real ffmpeg unavailable')
    paths=[]
    for i,color in enumerate(['navy','maroon']):
        path=root/f'合成候选{i+1}.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',f'color=c={color}:s=320x180:r=24','-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True,timeout=20)
        paths.append(path)
    return paths


def test_real_ffprobe_candidate(videos):
    c=candidate_from_file(videos[0]);assert c.media_check=='ffprobe' and c.width==320 and c.duration_ms==1000


def test_browser_python_review_parity(page,prepared):
    doc,a,b=prepared;doc=decided(decided(doc,a),b,'revise')
    output=page.evaluate('d=>ManjuReview.normalizeReview(d)',doc.model_dump(mode='json'))
    assert output==doc.model_dump(mode='json')
    new=page.evaluate('r=>ManjuReview.newReview(r.request,r.candidates,r.nonce)',{'request':request().model_dump(mode='json'),'candidates':[c.model_dump(mode='json') for c in doc.session.candidates],'nonce':'a'*32})
    assert ReviewDocument.model_validate(new).session==doc.session


@pytest.mark.parametrize('key',['notes','picture_lock_authorized','previous_decision_sha256'])
def test_browser_rejects_corrupt_review(page,prepared,key):
    doc,a,b=prepared;value=decided(doc,a).model_dump(mode='json')
    value['decisions'][0][key]='forged'
    result=page.evaluate('async d=>{try{await ManjuReview.normalizeReview(d);return "accepted";}catch(e){return e.message;}}',value)
    assert result!='accepted'


def setup_review(page,videos):
    page.locator('#prompt').fill(request().prompt)
    page.locator('#review-files').set_input_files(videos)
    page.wait_for_function('() => (ManjuReview.state.pending.length===2 && !ManjuReview.state.busy)')
    page.locator('#create-review').click();page.wait_for_function('() => (!!ManjuReview.state.document)')
    h=page.evaluate('ManjuReview.state.document.session.blind_order[0]')
    page.locator('#review-candidate').select_option(h)
    return h


def browser_decide(page,verdict='approve_draft'):
    page.locator('#review-verdict').select_option(verdict);page.locator('#review-human').fill('合成浏览器验收')
    page.locator('#review-notes').fill('检查色块候选，用于验证流程，不是用户对实际影片的审批。')
    page.locator('#review-score').select_option('4');page.locator('#review-confirmed').check()
    before=page.evaluate('ManjuReview.state.document.decisions.length')
    page.locator('#record-review').click();page.wait_for_function('(n)=>ManjuReview.state.document.decisions.length===n',arg=before+1)


def test_browser_real_review_promote_download(page,videos,tmp_path):
    hashes=[file_digest(p) for p in videos];h=setup_review(page,videos)
    assert videos[0].name not in page.locator('#candidate-grid').inner_text()
    page.locator('#play-review').click();page.wait_for_function('Array.from(document.querySelectorAll("#candidate-grid video")).every(v=>!v.paused||v.ended)')
    page.locator('#pause-review').click()
    browser_decide(page)
    doc=ReviewDocument.model_validate(page.evaluate('ManjuReview.state.document'))
    assert not doc.decisions[0].picture_lock_authorized
    with page.expect_download() as info:page.locator('#export-review').click()
    review_file=tmp_path/'review.json';info.value.save_as(review_file)
    assert ReviewDocument.model_validate(load_json(review_file))==doc
    page.locator('#promotion-resolution').fill('1080p');page.locator('#promotion-confirmed').check();page.locator('#promote-review').click()
    page.wait_for_function('() => (ManjuWorkbench.state.stage==="final")')
    page.locator('#check-plan').click();page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).check()
    page.locator('#reviewer').fill('合成验收');page.locator('#human-confirmed').check();page.locator('#ack-warnings').check()
    with page.expect_download(timeout=20000) as info:page.locator('#export-bundle').click()
    archive=tmp_path/'final.zip';info.value.save_as(archive)
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None;z.extractall(tmp_path/'final')
    result=verify_bundle(tmp_path/'final',require_promotion=True)
    assert result['promotion_verified'] and not result['external_execution_authorized']
    assert [file_digest(p) for p in videos]==hashes
    assert (tmp_path/'final'/'approved-draft'/f'{h}.mp4').is_file()


def test_browser_changed_intent_and_revocation_refuse(page,videos):
    h=setup_review(page,videos);browser_decide(page)
    page.locator('#prompt').fill('突然更换故事主角')
    page.locator('#promotion-confirmed').check();page.locator('#promote-review').click()
    page.wait_for_function('() => (document.getElementById("status").textContent.includes("当前任务已变更"))')
    assert page.evaluate('ManjuWorkbench.state.stage')=='draft'
    page.locator('#prompt').fill(request().prompt)
    browser_decide(page,'reject')
    page.locator('#promotion-confirmed').check();page.locator('#promote-review').click()
    page.wait_for_function('() => (document.getElementById("status").textContent.includes("最新决定不是批准"))')
    assert page.evaluate('ManjuWorkbench.state.stage')=='draft'


def test_browser_final_hash_without_review_cannot_export(page):
    page.evaluate('r=>{ManjuWorkbench.fillRequest(r);ManjuWorkbench.invalidate();}',{**request().model_dump(mode='json'),'stage':'final','approved_draft_sha256':'a'*64})
    page.locator('#check-plan').click();page.get_by_role('radio',name='Veo 3.1 Preview text',exact=True).check()
    page.locator('#reviewer').fill('测试');page.locator('#human-confirmed').check();page.locator('#ack-warnings').check();page.locator('#export-bundle').click()
    page.wait_for_function('() => (document.getElementById("status").textContent.includes("真实审片记录"))')
    assert not page.evaluate('ManjuWorkbench.state.busy')


def test_browser_review_mobile_no_overflow(page,videos):
    setup_review(page,videos);page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
