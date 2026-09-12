"""Read-only recovery descriptions, old format compatibility and clock rollback."""
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest
from tests.test_local_continuation import M, archive, put, contents, http, package, req
from tests.test_rebuilt_r16_user_desk import write_desk
from manju.authoring.desk import verify_desk


def test_summary_reads_rich_point_without_changes_or_extracting(tmp_path, archive):
    store=M.RecoveryStore(tmp_path/'recovery');point=put(store,archive)['point']
    before=contents(store.root)
    result=store.inspect(point['id'],point['sha256'])
    assert result['ok'] and result['point']==point
    d=result['summary']
    assert d['schema_id']=='manju.recovery-summary/v1' and d['media_files']==3
    assert d['pending_external_edit'] and d['pending_template']
    assert not d['video_decode_verified'] and not d['restored'] and not d['independent_backup']
    assert contents(store.root)==before and verify_desk(archive)['ok']
    assert set(json.loads((store.root/point['id']/'POINT.json').read_text()))=={'id','window','sequence','sha256','bytes','created_utc'}


def test_empty_unfinished_draft_can_be_summarized(tmp_path):
    archive,_,_=write_desk(tmp_path)
    s=M.RecoveryStore(tmp_path/'recover');r=put(s,archive)['point'];d=s.inspect(r['id'],r['sha256'])['summary']
    assert d['media_files']==0 and d['media_bytes']==0 and d['candidates']==0
    assert not d['has_director_video'] and not d['has_repair_video']


@pytest.mark.parametrize('bad',['','x'*64,'0'*64,None])
def test_summary_requires_exact_expected_identity(tmp_path,archive,bad):
    s=M.RecoveryStore(tmp_path/'recover');r=put(s,archive)['point'];before=contents(s.root)
    with pytest.raises(M.RecoveryError):s.inspect(r['id'],bad)
    assert contents(s.root)==before


@pytest.mark.parametrize('identity',['../LOCK','a'*31,'/etc/passwd','a'*32+'/summary'])
def test_summary_rejects_paths_before_reading(tmp_path,identity):
    s=M.RecoveryStore(tmp_path/'absent')
    with pytest.raises(M.RecoveryError):s.inspect(identity,'0'*64)
    assert not s.root.exists()


def test_summary_does_not_trust_metadata_when_payload_corrupted(tmp_path,archive):
    s=M.RecoveryStore(tmp_path/'recover');r=put(s,archive)['point']
    target=s.root/r['id']/'DESK.zip';target.write_bytes(target.read_bytes()[:-1]);before=contents(s.root)
    with pytest.raises(M.RecoveryError):s.inspect(r['id'],r['sha256'])
    assert contents(s.root)==before


def test_missing_point_does_not_create_directory(tmp_path):
    s=M.RecoveryStore(tmp_path/'absent')
    with pytest.raises(M.RecoveryError):s.inspect('a'*32,'0'*64)
    assert not s.root.exists()


def test_summary_long_text_does_not_change_stored_draft(tmp_path):
    text='雨后灯光 '*300
    def change(items):
        doc=json.loads(items['STUDIO.json']);doc['workspace']['draft']['form']['prompt']=text
        items['STUDIO.json']=json.dumps(doc,ensure_ascii=False).encode()
    archive,_,_=write_desk(tmp_path,inner_edit=change)
    s=M.RecoveryStore(tmp_path/'recover');r=put(s,archive)['point'];before=archive.read_bytes()
    d=s.inspect(r['id'],r['sha256'])['summary']
    assert d['prompt']==text[:240] and archive.read_bytes()==before
    _,stream=s.load(r['id'])
    with stream:assert stream.read()==before


@pytest.mark.parametrize('change',[
    lambda d:d.update(schema_id='future-v2'),
    lambda d:d.update(automatic_execution=True),
    lambda d:d['workspace']['draft']['form'].update(prompt=123),
    lambda d:d.update(media=[{'bytes':True}]),
    lambda d:d.update(media=[{'bytes':-1}]),
    lambda d:d.update(media=[{'bytes':129*1024*1024}]),
    lambda d:d.update(media=[{'bytes':128*1024*1024}]*5),
    lambda d:d.update(director=[]),
    lambda d:d['director'].update(anchors={}),
    lambda d:d['workspace'].update(review=[]),
])
def test_unsupported_or_unsafe_summary_is_failure_not_deletion(tmp_path,change):
    def mutate(items):
        doc=json.loads(items['STUDIO.json']);change(doc);items['STUDIO.json']=json.dumps(doc).encode()
    archive,_,_=write_desk(tmp_path,inner_edit=mutate)
    s=M.RecoveryStore(tmp_path/'recover');r=put(s,archive)['point'];before=contents(s.root)
    with pytest.raises(M.RecoveryError):s.inspect(r['id'],r['sha256'])
    assert contents(s.root)==before


@pytest.mark.parametrize('bad',[b'{"schema_id":"a","schema_id":"b"}',b'x'*(2*1024*1024+1)])
def test_summary_metadata_bounded_before_parse(tmp_path,bad):
    archive,_,_=write_desk(tmp_path,inner_edit=lambda items:items.update({'STUDIO.json':bad}))
    s=M.RecoveryStore(tmp_path/'recover');r=put(s,archive)['point']
    with pytest.raises(M.RecoveryError):s.inspect(r['id'],r['sha256'])
    assert len(s.list()['points'])==1


def test_real_http_summary_returns_bounded_json_not_media(http,archive):
    s,url=http;r=put(s.store,archive)['point'];before=contents(s.store.root)
    status,headers,body=req(s,url,'points/'+r['id']+'/summary',headers={'X-Manju-Sha256':r['sha256']})
    assert status==200 and 'application/json' in headers['Content-Type']
    assert json.loads(body)['point']==r and len(body)<10000 and b'PK\x03\x04' not in body
    assert headers['Cache-Control']=='no-store' and contents(s.store.root)==before
    assert req(s,url,'points/'+r['id']+'/summary')[0]==409


@pytest.mark.parametrize('headers',[{'Origin':'https://evil.example'},{'Origin':'null'},{'Host':'evil.example'},
                                  {'X-Manju-Token':'wrong'},{'Sec-Fetch-Site':'cross-site'}])
def test_private_summary_origin_and_token_guards(http,archive,headers):
    s,url=http;r=put(s.store,archive)['point']
    assert req(s,url,'points/'+r['id']+'/summary',headers={'X-Manju-Sha256':r['sha256'],**headers})[0]==403


def test_newest_by_sequence_survives_clock_rollback(tmp_path,archive,monkeypatch):
    s=M.RecoveryStore(tmp_path/'recover',keep=3)
    times=iter([datetime(2026,9,12,12,tzinfo=timezone.utc)+timedelta(hours=h) for h in [0,1,-3,-2,-1]])
    class Clock:
        @staticmethod
        def now(tz):return next(times)
    monkeypatch.setattr(M,'datetime',Clock)
    points=[put(s,archive,sequence=i)['point'] for i in range(1,6)]
    assert {p['sequence'] for p in s.list()['points']}=={3,4,5}
    assert {p['id'] for p in s.list()['points']}=={p['id'] for p in points[-3:]}


def test_old_format_record_is_unmodified_after_new_read(tmp_path,archive):
    s=M.RecoveryStore(tmp_path/'recover');r=put(s,archive)['point'];before=contents(s.root)
    s.inspect(r['id'],r['sha256']);assert contents(s.root)==before
    import importlib.util
    source=Path(__file__).resolve().parents[1]
    import subprocess
    oldcode=subprocess.check_output(['git','show','55990e7:tools/delivery/LOCAL_CONTINUE.py'],cwd=source)
    oldfile=tmp_path/'old.py';oldfile.write_bytes(oldcode)
    spec=importlib.util.spec_from_file_location('old_recovery_compatible',oldfile);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    record,stream=old.RecoveryStore(s.root).load(r['id'])
    with stream:assert stream.read()==archive.read_bytes()
    assert record==r and old.RecoveryStore(s.root).list()['points']==[r]
