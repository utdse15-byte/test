"""Cold package entry and release gate: real execution is not replaced by claims."""
import importlib.util
import json
from pathlib import Path
import shutil
from hashlib import sha256
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('ide_delivery',ROOT/'tools/user_ready/build_ide_delivery.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
def put(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d),encoding='utf-8')
def sha(p):return sha256(p.read_bytes()).hexdigest()

@pytest.fixture
def data(tmp_path):
    repo=tmp_path/'repo';ev=tmp_path/'evidence';ev.mkdir();(repo/'tools/user_ready').mkdir(parents=True);(repo/'src/manju').mkdir(parents=True)
    shutil.copy2(ROOT/'tools/user_ready/build_experience_delivery.py',repo/'tools/user_ready/build_experience_delivery.py')
    (repo/'tools/model_workbench.html').write_text('test page', encoding='utf-8');(repo/'src/manju/ide.py').write_text('source', encoding='utf-8')
    (ev/'regression.rc').write_text('0', encoding='utf-8');(ev/'regression.xml').write_text('<testsuite><testcase classname="test" name="real"/></testsuite>', encoding='utf-8')
    put(ev/'tested-source-hashes.json',{'tools/model_workbench.html':sha(repo/'tools/model_workbench.html'),'src/manju/ide.py':sha(repo/'src/manju/ide.py')})
    r={k:True for k in ('ok','actual_browser_download','actual_cli_roundtrip','new_story_and_text_restored','old_briefs_immutable','old_briefs_show_stale','source_backup_unchanged','fresh_checkout_reopen_identical','legacy_checkout_reexport_identical')}
    r.update(javascript_errors=[],network_requests=[],html_sha256=sha(repo/'tools/model_workbench.html'),version='test',input_sha256='input',media_sha256={'media':'hash'},unchanged_media_files=1,browser_checkout_sha256='return',high_resolution_png_preserved=[1920,1080])
    put(ev/'source-ide-complete/RESULT.json',{**r,'runtime':'source'});put(ev/'installed-ide/RESULT.json',{**r,'runtime':'installed'})
    put(ev/'installed-parity.json',{'ok':True,'files_checked':1,'runtime_hashes':{'manju/ide.py':sha(repo/'src/manju/ide.py')}})
    return repo,ev

def test_gate_accepts_actual_matching_evidence(data):assert M.evidence_gate(*data)['passed']==1

@pytest.mark.parametrize('key',['ok','actual_browser_download','actual_cli_roundtrip','new_story_and_text_restored','old_briefs_immutable','old_briefs_show_stale','source_backup_unchanged','fresh_checkout_reopen_identical','legacy_checkout_reexport_identical'])
def test_missing_actual_step_blocks_publish(data,key):
    repo,ev=data;p=ev/'installed-ide/RESULT.json';r=json.loads(p.read_text(encoding='utf-8'));r[key]=False;put(p,r)
    with pytest.raises(ValueError):M.evidence_gate(repo,ev)

@pytest.mark.parametrize('case',['unfinished','empty','failed','duplicate','changed-source','same-runtime','wrong-result','js-error','no-parity','changed-runtime'])
def test_gate_rejects_stale_or_unfinished(data,case):
    repo,ev=data;p=ev/'installed-ide/RESULT.json';r=json.loads(p.read_text(encoding='utf-8'))
    if case=='unfinished':(ev/'regression.rc').write_text('1', encoding='utf-8')
    elif case=='empty':(ev/'regression.xml').write_text('<testsuite/>', encoding='utf-8')
    elif case=='failed':(ev/'regression.xml').write_text('<testsuite><testcase><failure/></testcase></testsuite>', encoding='utf-8')
    elif case=='duplicate':(ev/'regression.xml').write_text('<testsuite><testcase name="x"/><testcase name="x"/></testsuite>', encoding='utf-8')
    elif case=='changed-source':(repo/'tools/model_workbench.html').write_text('changed', encoding='utf-8')
    elif case=='same-runtime':r['runtime']='source';put(p,r)
    elif case=='wrong-result':r['browser_checkout_sha256']='different';put(p,r)
    elif case=='js-error':r['javascript_errors']=['error'];put(p,r)
    elif case=='no-parity':put(ev/'installed-parity.json',{'ok':False})
    elif case=='changed-runtime':put(ev/'installed-parity.json',{'ok':True,'files_checked':1,'runtime_hashes':{'manju/ide.py':'bad'}})
    with pytest.raises(ValueError):M.evidence_gate(repo,ev)

def test_root_ide_entry_has_no_autorun_or_install(tmp_path):
    M.ide_entry(tmp_path)
    assert (tmp_path/'CLAUDE.md').read_text(encoding='utf-8').startswith('@AGENTS.md')
    j=json.loads((tmp_path/'MANJU.code-workspace').read_text(encoding='utf-8'))
    assert j['folders'][0]['path']=='APP/source'
    t=j['tasks']['tasks'][0];assert t['args']==['tools/ai_bootstrap.py','--json'] and t.get('runOptions') is None
    assert 'extensions' not in j and 'APP/repository.bundle' in (tmp_path/'AGENTS.md').read_text(encoding='utf-8')
