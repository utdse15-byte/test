"""The actual publish gate must reject incomplete or stale evidence."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import pytest

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('story_delivery',REPO/'tools/user_ready/build_story_delivery.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def write(p,obj):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj),encoding='utf-8')

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@pytest.fixture
def files(tmp_path):
    repo=tmp_path/'repo';ev=tmp_path/'evidence';ev.mkdir();(repo/'tools/user_ready').mkdir(parents=True);(repo/'src/manju').mkdir(parents=True)
    shutil.copy2(REPO/'tools/user_ready/build_experience_delivery.py',repo/'tools/user_ready/build_experience_delivery.py')
    (repo/'tools/model_workbench.html').write_text('test page');(repo/'src/manju/story.py').write_text('test source')
    (ev/'regression.rc').write_text('0');(ev/'regression.xml').write_text('<testsuites><testsuite><testcase name="real-check"/></testsuite></testsuites>')
    write(ev/'tested-source-hashes.json',{'tools/model_workbench.html':sha(repo/'tools/model_workbench.html'),'src/manju/story.py':sha(repo/'src/manju/story.py')})
    common={'ok':True,'actual_browser_download':True,'story_scope_exclusion':True,'stale_scene_blocks_link':True,'old_brief_not_overwritten':True,'fresh_checkout_reopen_identical':True,'legacy_inner_reexport_identical':True,'javascript_errors':[],'network_requests':[],'html_sha256':sha(repo/'tools/model_workbench.html'),'version':'test','input_sha256':'x','media_sha256':{'media':'x'},'brief_content_hashes':['x'],'unchanged_media_files':1,'high_resolution_png_preserved':[1920,1080]}
    write(ev/'source-story/RESULT.json',{**common,'runtime':'source'});write(ev/'installed-story/RESULT.json',{**common,'runtime':'installed'})
    write(ev/'installed-parity.json',{'ok':True,'files_checked':1,'runtime_hashes':{'manju/story.py':sha(repo/'src/manju/story.py')}})
    return repo,ev


def test_accepts_matching_completed_evidence(files):
    r=M.evidence_gate(*files);assert r['tests']==1 and r['passed']==1 and r['failures']==0


@pytest.mark.parametrize('field',['ok','actual_browser_download','story_scope_exclusion','stale_scene_blocks_link','old_brief_not_overwritten','fresh_checkout_reopen_identical','legacy_inner_reexport_identical'])
def test_rejects_unfinished_media_rehearsal(files,field):
    repo,ev=files;p=ev/'installed-story/RESULT.json';r=json.loads(p.read_text());r[field]=False;write(p,r)
    with pytest.raises(ValueError):M.evidence_gate(repo,ev)


@pytest.mark.parametrize('case',['rc','empty','failure','source-changed','same-runtime','media-diff','no-parity','runtime-parity-changed','js-error'])
def test_rejects_invalid_publish_gate(files,case):
    repo,ev=files;p=ev/'installed-story/RESULT.json';r=json.loads(p.read_text())
    if case=='rc':(ev/'regression.rc').write_text('1')
    elif case=='empty':(ev/'regression.xml').write_text('<testsuites/>')
    elif case=='failure':(ev/'regression.xml').write_text('<testsuite><testcase><failure>failed</failure></testcase></testsuite>')
    elif case=='source-changed':(repo/'tools/model_workbench.html').write_text('different')
    elif case=='same-runtime':r['runtime']='source';write(p,r)
    elif case=='media-diff':r['media_sha256']={'other':'y'};write(p,r)
    elif case=='no-parity':write(ev/'installed-parity.json',{'ok':False})
    elif case=='runtime-parity-changed':write(ev/'installed-parity.json',{'ok':True,'files_checked':1,'runtime_hashes':{'manju/story.py':'bad'}})
    elif case=='js-error':r['javascript_errors']=['error'];write(p,r)
    with pytest.raises(ValueError):M.evidence_gate(repo,ev)


def test_original_entry_uses_current_version_and_explains_old_inner():
    text=(REPO/'tools/user_ready/START_HERE.html').read_text(encoding='utf-8')
    assert json.loads((REPO/'DELIVERY_VERSION.json').read_text(encoding='utf-8'))['version'] in text and 'STORY_CHECKOUT.zip' in text and 'STUDIO.zip' in text
