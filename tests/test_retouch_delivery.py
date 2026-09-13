"""No fabricated release when test evidence or source identity is absent."""
from pathlib import Path
import hashlib
import importlib.util
import json
import pytest

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('retouch_delivery', REPO/'tools/user_ready/build_retouch_delivery.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

@pytest.fixture
def gate(tmp_path):
    repo=tmp_path/'repo';repo.mkdir();(repo/'test.py').write_text('print(1)',encoding='utf-8')
    e=tmp_path/'evidence';e.mkdir()
    (e/'regression.rc').write_text('0',encoding='utf-8')
    (e/'regression.xml').write_text('<testsuites><testsuite><testcase name="x"/></testsuite></testsuites>',encoding='utf-8')
    (e/'tested-source-hashes.json').write_text(json.dumps({'test.py':hashlib.sha256((repo/'test.py').read_bytes()).hexdigest()}),encoding='utf-8')
    value={'ok':True,'kit_sha256':'1','director_after_sha256':'2','checkout_after_sha256':'3','runtime_files':265}
    for name in ['source-retouch','installed-retouch']:
        (e/name).mkdir();(e/name/'RESULT.json').write_text(json.dumps(value),encoding='utf-8')
    (e/'installed-parity.json').write_text(json.dumps(value),encoding='utf-8')
    return repo,e

def test_gate_reads_completed_reports(gate):
    assert M.evidence_gate(*gate)['tests']==1

@pytest.mark.parametrize('change',['rc','xml','source','parity','missing','empty-fingerprints'])
def test_gate_refuses_unverified_or_changed_inputs(gate,change):
    repo,e=gate
    if change=='rc':(e/'regression.rc').write_text('1',encoding='utf-8')
    elif change=='xml':(e/'regression.xml').write_text('<testsuites><testcase><failure/></testcase></testsuites>',encoding='utf-8')
    elif change=='source':(repo/'test.py').write_text('changed',encoding='utf-8')
    elif change=='parity':
        p=e/'installed-retouch/RESULT.json';v=json.loads(p.read_text(encoding='utf-8'));v['kit_sha256']='different';p.write_text(json.dumps(v),encoding='utf-8')
    elif change=='missing':(e/'source-retouch/RESULT.json').unlink()
    else:(e/'tested-source-hashes.json').write_text('{}',encoding='utf-8')
    with pytest.raises((ValueError,OSError)):M.evidence_gate(repo,e)

def test_existing_output_is_preserved(gate,tmp_path):
    repo,e=gate;dest=tmp_path/'release';dest.mkdir();p=dest/'important';p.write_bytes(b'old')
    with pytest.raises(ValueError):M.build(repo,dest,e,tmp_path/'unused')
    assert p.read_bytes()==b'old'
