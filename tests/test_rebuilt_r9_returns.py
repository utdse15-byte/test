from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json
import subprocess
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect
from typer.testing import CliRunner

from manju.authoring.core import Request, AuthoringError, digest, file_digest
from manju.authoring.returns import candidate_checks, return_report, inspect_files
from manju.authoring.cli import app
from manju.review.core import Candidate
from tests.test_rebuilt_r5_workbench import browser,page


def req(**kw):
    return Request(shot_id='S001',task='create',prompt='测试返回规格，不批准画质',
                   **({'duration_s':8,'aspect_ratio':'16:9','resolution':'720p'}|kw))


def candidate(**kw):
    return Candidate(**({'sha256':'a'*64,'bytes':100,'filename':'返回.mp4',
                         'duration_ms':8000,'width':1280,'height':720,'media_check':'browser_metadata'}|kw))


@pytest.mark.parametrize('value,state',[(7900,'match'),(8100,'match'),(7899,'mismatch'),(8101,'mismatch'),(None,'unknown')])
def test_duration_exact_boundary(value,state):
    c=candidate(duration_ms=value,media_check='hash_only')
    assert candidate_checks(req(),c)[0]['state']==state


@pytest.mark.parametrize('w,h,state',[(1600,900,'match'),(1632,900,'match'),(1634,900,'mismatch'),(720,1280,'mismatch'),(None,None,'unknown')])
def test_ratio_exact_rational_boundary(w,h,state):
    assert candidate_checks(req(),candidate(width=w,height=h,media_check='hash_only'))[1]['state']==state


@pytest.mark.parametrize('resolution,state', [('720p','match'),('720P','match'),('1080p','mismatch'),('2K','unknown'),('4K','unknown'),(None,'not_requested')])
def test_only_explicit_p_labels_interpreted(resolution,state):
    assert candidate_checks(req(resolution=resolution),candidate())[2]['state']==state


def test_uncertain_does_not_become_pass_or_approval():
    r=return_report(req(resolution='2K'),[candidate()])
    assert r['items'][0]['status']=='needs_attention'
    assert r['items'][0]['full_video_decode']=='not_checked'
    assert not any(r[k] for k in ['automatic_approval','automatic_selection','picture_lock_authorized','provider_origin_verified','creative_quality_evaluated','audio_evaluated'])
    assert digest({k:v for k,v in r.items() if k!='report_sha256'})==r['report_sha256']


def test_no_requested_checks_not_falsely_passed():
    r=return_report(req(duration_s=None,aspect_ratio=None,resolution=None),[candidate()])
    assert r['items'][0]['status']=='not_requested'


@pytest.mark.parametrize('cs',[[],[candidate(),candidate()],[candidate(sha256=f'{i:064x}') for i in range(13)]])
def test_candidate_set_rejected(cs):
    with pytest.raises(AuthoringError):return_report(req(),cs)


def test_unbound_decode_claim_rejected():
    with pytest.raises(AuthoringError):return_report(req(),[candidate()],decoded={'b'*64})


@pytest.mark.parametrize('r,c',[
    (req(),candidate()),(req(resolution='2K'),candidate()),
    (req(aspect_ratio='adaptive'),candidate()),
    (req(duration_s=None,aspect_ratio=None,resolution=None),candidate()),
    (req(),candidate(width=720,height=1280)),
    (req(),candidate(duration_ms=None,width=None,height=None,media_check='hash_only')),
    (req(),candidate(duration_ms=8101)),
])
def test_python_browser_report_parity(page,r,c):
    actual=page.evaluate('([r,c])=>ManjuReturns.returnReport(r,[c])',[r.model_dump(mode='json'),c.model_dump(mode='json')])
    assert actual==return_report(r,[c])


@pytest.fixture
def clip(tmp_path):
    path=tmp_path/'实际返回.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                    'testsrc2=s=320x180:r=24','-t','1','-c:v','libx264','-threads','1',
                    '-pix_fmt','yuv420p',str(path)],capture_output=True,check=True,timeout=20)
    return path


def test_real_full_decode_does_not_modify_media(clip):
    before=file_digest(clip)
    r=inspect_files(req(duration_s=1,resolution='720p'),[clip],decode=True)
    assert r['items'][0]['full_video_decode']=='passed'
    assert [c['state'] for c in r['items'][0]['checks']]==['match','match','mismatch']
    assert before==file_digest(clip)
    assert not r['automatic_approval']


def test_corrupt_local_candidate_does_not_create_output(tmp_path):
    file=tmp_path/'bad.mp4';file.write_bytes(b'not a video');out=tmp_path/'report.json'
    request=tmp_path/'request.json';request.write_text(req().model_dump_json(),encoding='utf-8')
    result=CliRunner().invoke(app,['inspect-return',str(request),'--candidate',str(file),'--output',str(out)])
    assert result.exit_code==2 and not out.exists()


def test_cli_exclusive_report(clip,tmp_path):
    request=tmp_path/'request.json';request.write_text(req(duration_s=1).model_dump_json(),encoding='utf-8')
    out=tmp_path/'report.json';runner=CliRunner()
    args=['inspect-return',str(request),'--candidate',str(clip),'--decode','--output',str(out)]
    assert runner.invoke(app,args).exit_code==0
    before=out.read_bytes();assert runner.invoke(app,args).exit_code==2
    assert out.read_bytes()==before
    assert json.loads(before)['items'][0]['candidate']['sha256']==file_digest(clip)


def test_local_probe_uses_protocol_allowlist(monkeypatch,tmp_path):
    from importlib import import_module
    module = import_module("manju.media.probe")
    seen=[]
    def fake_run(cmd,**kw):
        seen.append(cmd);return SimpleNamespace(returncode=0,stdout=json.dumps({'streams':[{'codec_type':'video','width':320,'height':180}],'format':{'duration':'1'}}),stderr='')
    monkeypatch.setattr(module.subprocess,'run',fake_run)
    module.probe(tmp_path/'fake.mp4',local_only=True)
    assert seen[0][-3:]==['-protocol_whitelist','file,pipe',str(tmp_path/'fake.mp4')]
    module.probe(tmp_path/'fake.mp4')
    assert '-protocol_whitelist' not in seen[1]


def test_actual_playlist_cannot_fetch_http(tmp_path):
    file=tmp_path/'playlist.m3u8'
    file.write_text('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://127.0.0.1:9/never-request.ts\n#EXT-X-ENDLIST\n',encoding='utf-8')
    from manju.media.probe import probe
    from manju.media.ffmpeg import MediaError
    with pytest.raises(MediaError,match='whitelist'):
        probe(file,local_only=True,timeout=5)


def test_browser_return_report_reads_real_candidate_without_approving(page,clip,tmp_path):
    page.locator('#prompt').fill('返回检查');page.locator('#duration').fill('1')
    page.locator('#review-files').set_input_files(clip)
    expect(page.locator('#review-status')).to_contain_text('候选文件已核对')
    page.locator('#return-section summary').click()
    with page.expect_download() as d:page.locator('#export-returns').click()
    out=tmp_path/'browser-return.json';d.value.save_as(out);r=json.loads(out.read_text())
    assert r['items'][0]['candidate']['sha256']==file_digest(clip)
    assert r['items'][0]['full_video_decode']=='not_checked'
    assert r['items'][0]['status']=='needs_attention'
    assert not r['automatic_approval']
    assert page.evaluate('ManjuReview.state.document') is None
    assert not page.locator('#review-confirmed').is_checked()
