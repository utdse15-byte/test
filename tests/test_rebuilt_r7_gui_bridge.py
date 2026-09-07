"""The original GUI discovers offline model tools through read-only HTTP routes."""
from pathlib import Path
import json
import threading
import urllib.request
import urllib.error
import pytest
from manju.gui.server import create_server
from manju.gui.pages import nav_html
from manju.authoring.core import file_digest,Request
from manju.authoring.server_page import render

@pytest.fixture
def gui(tmp_project):
    server=create_server(tmp_project,host='127.0.0.1',port=0,actor='human',readonly=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield server
    server.shutdown();server.close();thread.join(timeout=5)

def get(server,path,headers=None):
    req=urllib.request.Request(f'http://127.0.0.1:{server.port}'+path,headers=headers or {})
    try:r=urllib.request.urlopen(req,timeout=5)
    except urllib.error.HTTPError as e:r=e
    with r:return r.status,dict(r.headers),r.read()

def hashes(project):return {p.relative_to(project.root).as_posix():file_digest(p) for p in project.root.rglob('*') if p.is_file()}

def test_readonly_gui_serves_tools_and_project_snapshot(gui,tmp_project,add_shot):
    add_shot(tmp_project,'S01');before=hashes(tmp_project)
    status,headers,html=get(gui,'/model-workbench')
    assert status==200 and b'ManjuReview' in html
    assert "connect-src 'none'" in headers['Content-Security-Policy']
    assert headers['X-Frame-Options']=='DENY'
    assert '/api/model-authoring/shot?shot=S01' in html.decode()
    status,headers,data=get(gui,'/api/model-authoring/shot?shot=S01')
    assert status==200 and headers['Content-Disposition'].startswith('attachment;')
    value=json.loads(data);assert value['project_modified'] is False
    assert Request.model_validate(value['request']).shot_id=='S01'
    assert hashes(tmp_project)==before

@pytest.mark.parametrize('query',['','?shot=../x','?shot=S01&shot=S02','?shot=S01&other=1'])
def test_ambiguous_or_unsafe_shot_refused(gui,query):
    assert get(gui,'/api/model-authoring/shot'+query)[0]==400

def test_missing_shot_refused(gui):assert get(gui,'/api/model-authoring/shot?shot=UNKNOWN')[0]==404

def test_host_guard_preserved(gui):
    assert get(gui,'/model-workbench',headers={'Host':'attacker.invalid'})[0]==403

def test_nav_discovers_tools_in_new_tab():
    for mode in ['beginner','pro']:
        html=nav_html('/',mode=mode)
        assert 'href="/model-workbench"' in html
        assert 'noopener noreferrer' in html

def test_unbound_model_tools_still_available():
    html=render(None);assert '尚未绑定影片工程' in html and 'ManjuReview' in html
