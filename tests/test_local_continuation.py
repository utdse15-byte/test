"""Optional recovery: real files, real HTTP, failure safety, unchanged Desk/v1."""
from __future__ import annotations
import hashlib
import http.client as http_client_module
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import threading
from urllib.parse import urlsplit
import zipfile

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('continuation_test', REPO / 'tools/delivery/LOCAL_CONTINUE.py')
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
from tests.test_rebuilt_r16_user_desk import write_desk
from manju.authoring.desk import verify_desk


@pytest.fixture
def archive(tmp_path):
    path, _, _ = write_desk(tmp_path, rich=True)
    return path


def put(store, file, *, window='a'*32, sequence=1):
    with file.open('rb') as stream:
        return store.save(stream, file.stat().st_size, window, sequence, M.digest(file))


def contents(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_read_does_not_create_recovery_directory(tmp_path):
    root=tmp_path/'uncreated'
    s=M.RecoveryStore(root)
    assert s.list()['points']==[] and not root.exists()


def test_exact_legacy_desk_bytes_and_real_restart(tmp_path, archive):
    root=tmp_path/'保存 work'
    original=archive.read_bytes();s=M.RecoveryStore(root)
    receipt=put(s,archive)
    assert not receipt['independent_backup']
    restarted=M.RecoveryStore(root)
    record, stream=restarted.load(receipt['point']['id'])
    with stream:assert stream.read()==original
    assert restarted.list()['points']==[record]
    result=verify_desk(root/record['id']/'DESK.zip')
    assert result['ok'] and not result['video_decode_verified']
    assert archive.read_bytes()==original


@pytest.mark.parametrize('failure',['truncated','wrong-hash','invalid-zip','unsafe-flags','quota','disk-full','stale','fsync','rename'])
def test_failed_save_never_removes_previous_point(tmp_path,archive,monkeypatch,failure):
    s=M.RecoveryStore(tmp_path/'recover',keep=1)
    first=put(s,archive);before=contents(s.root)
    data=archive.read_bytes();value=M.digest(archive);length=len(data);sequence=2
    if failure=='truncated': data=data[:-10]
    if failure=='wrong-hash': value='0'*64
    if failure=='invalid-zip': data=b'bad';length=len(data);value=hashlib.sha256(data).hexdigest()
    if failure=='unsafe-flags':
        with zipfile.ZipFile(io.BytesIO(data)) as z:members={n:z.read(n) for n in z.namelist()}
        d=json.loads(members['DESK.json']);d['automatic_execution']=True
        members['DESK.json']=json.dumps(d).encode()
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_STORED) as z:
            for n,b in members.items():z.writestr(n,b)
        data=out.getvalue();length=len(data);value=hashlib.sha256(data).hexdigest()
    if failure=='quota':s.maximum=len(data)
    if failure=='disk-full':monkeypatch.setattr(M.shutil,'disk_usage',lambda _:shutil._ntuple_diskusage(10,10,0))
    if failure=='stale':sequence=1
    if failure=='fsync':monkeypatch.setattr(M.os,'fsync',lambda _:(_ for _ in ()).throw(OSError('disk fault')))
    if failure=='rename':monkeypatch.setattr(M.os,'rename',lambda *a:(_ for _ in ()).throw(OSError('rename fault')))
    with pytest.raises((M.RecoveryError,OSError)):
        s.save(io.BytesIO(data),length,'a'*32,sequence,value)
    assert contents(s.root)==before
    assert first['point']['id'] in [r['id'] for r in s.list()['points']]


def test_prune_only_own_window_after_success(tmp_path,archive):
    s=M.RecoveryStore(tmp_path/'recover',keep=3)
    other=put(s,archive,window='b'*32)
    own=[put(s,archive,sequence=i) for i in range(1,6)]
    ids={r['id'] for r in s.list()['points']}
    assert ids=={other['point']['id'],*(p['point']['id'] for p in own[-3:])}
    assert own[-1]['pruned']==[own[1]['point']['id']]


def test_invalid_identity_before_first_write(tmp_path,archive):
    s=M.RecoveryStore(tmp_path/'recover')
    for identity in ['../bad','UPPER'*7,'','a'*31]:
        with pytest.raises(M.RecoveryError):put(s,archive,window=identity)
    assert not s.root.exists()


def test_corruption_blocks_download_without_modifying_original(tmp_path,archive):
    s=M.RecoveryStore(tmp_path/'recover');p=put(s,archive)['point']
    f=s.root/p['id']/'DESK.zip';f.write_bytes(f.read_bytes()[:-1])
    before=contents(s.root)
    with pytest.raises(M.RecoveryError):s.load(p['id'])
    assert contents(s.root)==before


def test_delete_requires_exact_identity_and_leaves_other_points(tmp_path,archive):
    s=M.RecoveryStore(tmp_path/'recover');a=put(s,archive)['point'];b=put(s,archive,window='b'*32)['point']
    with pytest.raises(M.RecoveryError):s.remove(a['id'],'0'*64)
    assert len(s.list()['points'])==2
    s.remove(a['id'],a['sha256'])
    assert [p['id'] for p in s.list()['points']]==[b['id']]


def test_symlinks_and_foreign_existing_directories_refused(tmp_path,archive):
    foreign=tmp_path/'foreign';foreign.mkdir();(foreign/'keep').write_text('keep')
    with pytest.raises((M.RecoveryError,OSError)):put(M.RecoveryStore(foreign),archive)
    assert contents(foreign)=={'keep':b'keep'}
    root=tmp_path/'link';root.symlink_to(foreign,target_is_directory=True)
    with pytest.raises(M.RecoveryError):put(M.RecoveryStore(root),archive)
    assert contents(foreign)=={'keep':b'keep'}


def test_interrupted_point_only_cleaned_by_explicit_action(tmp_path,archive):
    s=M.RecoveryStore(tmp_path/'recover');good=put(s,archive)['point']
    pending=s.root/('pending-'+'c'*32);pending.mkdir();(pending/'DESK.zip').write_bytes(b'partial')
    assert s.list()['unfinished']==1
    assert pending.exists()
    assert s.clean_unfinished()['removed']==1
    assert s.list()['points']==[good]


def test_cross_process_lock_refuses_other_writer(tmp_path,archive):
    s=M.RecoveryStore(tmp_path/'recover');put(s,archive)
    b=M.RecoveryStore(s.root)
    with s.locked():
        with pytest.raises(M.RecoveryError,match='Another'):put(b,archive,sequence=2)
    assert len(s.list()['points'])==1


@pytest.fixture
def package(tmp_path):
    root=tmp_path/'package';root.mkdir()
    files={'START_HERE.html':b'<!doctype html><title>start</title>',
           'APP/OPEN_MODEL_WORKBENCH.html':(REPO/'tools/model_workbench.html').read_bytes(),
           'EXAMPLES/CHECKOUT.zip':b'fixture only, not served by continuation'}
    for n,b in files.items():
        p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
    (root/'LOCAL_WORKBENCH_FILES.json').write_text(json.dumps({n:hashlib.sha256(b).hexdigest() for n,b in files.items()}),encoding='utf-8')
    js=(REPO/'tools/delivery/CONTINUE_CLIENT.js').read_bytes();(root/M.CLIENT).write_bytes(js)
    (root/M.FILES).write_text(json.dumps({M.CLIENT:hashlib.sha256(js).hexdigest()}),encoding='utf-8')
    return root


@pytest.fixture
def http(package,tmp_path):
    s,url=M.create_server(package,tmp_path/'recover-http');t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
    yield s,urlsplit(url)
    s.shutdown();s.server_close();t.join(3);assert not t.is_alive()


def req(server,url,path='',*,method='GET',data=None,headers=None):
    h={'X-Manju-Token':server.csrf,'Origin':f'http://127.0.0.1:{server.server_port}',**(headers or {})}
    c=http_client(server);c.request(method,url.path+path,body=data,headers=h)
    r=c.getresponse();result=(r.status,dict(r.getheaders()),r.read());c.close();return result


def http_client(server):return http_client_module.HTTPConnection('127.0.0.1',server.server_port,timeout=5)


def test_real_http_save_list_read_delete(http,archive):
    s,url=http
    code,h,p=req(s,url);assert code==200 and b'MANJU_CONTINUE_CONFIG' in p
    assert "connect-src 'self'" in h['Content-Security-Policy']
    assert not s.store.root.exists()
    code,_,body=req(s,url,'points',method='POST',data=archive.read_bytes(),headers={'Content-Type':'application/zip',
         'X-Manju-Window':'a'*32,'X-Manju-Sequence':'1','X-Manju-Sha256':M.digest(archive)})
    assert code==201,body
    point=json.loads(body)['point']
    assert len(json.loads(req(s,url,'points')[2])['points'])==1
    code,headers,body=req(s,url,'points/'+point['id']);assert code==200 and body==archive.read_bytes()
    assert headers['X-Manju-Sha256']==point['sha256']
    assert req(s,url,'points/'+point['id'],method='DELETE',headers={'X-Manju-Sha256':point['sha256']})[0]==200


@pytest.mark.parametrize('headers',[{'Origin':'https://evil.example'}, {'Origin':'null'},
    {'Host':'example.org'}, {'X-Manju-Token':'wrong'}, {'Sec-Fetch-Site':'cross-site'}])
def test_foreign_requests_never_read_or_write(http,headers):
    s,url=http
    assert req(s,url,'points',headers=headers)[0]==403
    assert req(s,url,'points',method='POST',data=b'x',headers=headers)[0]==403
    assert not s.store.root.exists()


@pytest.mark.parametrize('path',['../START_HERE.html','points/../LOCK','points/%2e%2e','points?x=1',
    'points/'+'a'*32+'/DESK.zip','LOCK','IDENTITY','APP/','C:\\Windows','points//'])
def test_exact_routes_no_path_normalization(http,path):
    s,url=http
    assert req(s,url,path)[0] in {404,409}
    assert not s.store.root.exists()


def test_post_requires_origin_and_length(http):
    s,url=http;c=http_client(s)
    c.request('POST',url.path+'points',body=b'x',headers={'X-Manju-Token':s.csrf})
    r=c.getresponse();assert r.status==403;r.read();c.close()
    assert not s.store.root.exists()


def test_client_requires_verified_inventory(package):
    (package/M.CLIENT).write_bytes(b'tampered')
    with pytest.raises(M.RecoveryError,match='changed'):M.load_page(package)


def test_client_uses_origin_aware_original_restore_contract():
    script=(REPO/'tools/delivery/CONTINUE_CLIENT.js').read_text(encoding='utf-8')
    assert 'ManjuDesk.preview(blob,{source:"local_recovery"})' in script
    assert 'localStorage' not in script and 'indexedDB' not in script
    page=(REPO/'tools/model_workbench.html').read_text(encoding='utf-8')
    assert 'deskState.verified=localRecovery?null:deskFingerprint()' in page
    assert (REPO/'tools/model_workbench.html').read_bytes()==(REPO/'src/manju/authoring/data/workbench.html').read_bytes()


def test_old_workbench_without_restore_source_contract_is_refused(package):
    page=package/'APP/OPEN_MODEL_WORKBENCH.html'
    page.write_bytes(page.read_bytes().replace(b'restoreSourceVersion:1,',b''))
    f=package/'LOCAL_WORKBENCH_FILES.json';m=json.loads(f.read_text(encoding='utf-8'))
    m['APP/OPEN_MODEL_WORKBENCH.html']=M.digest(page);f.write_text(json.dumps(m),encoding='utf-8')
    with pytest.raises(M.RecoveryError,match='contract'):M.load_page(package)
