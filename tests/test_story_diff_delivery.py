"""Publication needs current paired browser/CLI diff evidence, not a screenshot."""
import importlib.util
import json
from pathlib import Path
import pytest
from tests.test_ide_delivery import data,put
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('changes_delivery',ROOT/'tools/user_ready/build_changes_delivery.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
KEYS=('readable_story_diff_parity','current_browser_edit_shown_not_merged','stale_comparison_refused','report_contains_full_before_after')

@pytest.fixture
def ready(data):
    r,e=data
    for name in ['source-ide-complete','installed-ide']:
        p=e/name/'RESULT.json';j=json.loads(p.read_text(encoding='utf-8'));j.update({k:True for k in KEYS});j['report_sha256']='equal';put(p,j)
    return r,e

def test_complete_current_evidence_passes(ready):
    assert M.evidence_gate(*ready)['passed']==1

@pytest.mark.parametrize('key',KEYS)
def test_missing_diff_rehearsal_blocks_publication(ready,key):
    r,e=ready;p=e/'installed-ide/RESULT.json';j=json.loads(p.read_text(encoding='utf-8'));j[key]=False;put(p,j)
    with pytest.raises(ValueError):M.evidence_gate(r,e)

def test_different_html_report_bytes_block_publication(ready):
    r,e=ready;p=e/'installed-ide/RESULT.json';j=json.loads(p.read_text(encoding='utf-8'));j['report_sha256']='different';put(p,j)
    with pytest.raises(ValueError):M.evidence_gate(r,e)

def test_never_writes_to_nonempty_delivery_directory(tmp_path):
    p=tmp_path/'do-not-overwrite';p.mkdir();f=p/'keep.zip';f.write_bytes(b'keep')
    with pytest.raises(ValueError):M.build(tmp_path,p,tmp_path,tmp_path/'dummy.zip')
    assert f.read_bytes()==b'keep'
