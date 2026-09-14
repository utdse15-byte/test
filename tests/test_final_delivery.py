import importlib.util
import json
from pathlib import Path
import pytest
from tests.test_ide_delivery import data,put
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('final_delivery',ROOT/'tools/user_ready/build_final_delivery.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
KEYS=('three_way_report_parity','local_later_fields_preserved','only_selected_story_fields_changed','ending_field_unchanged','inner_studio_unchanged','undo_tested','stale_preview_refused','already_applied_not_repeated')

@pytest.fixture
def ready(data):
    r,e=data
    for old,new in [('source-ide-complete','source-final'),('installed-ide','installed-final')]:
        obj=json.loads((e/old/'RESULT.json').read_text(encoding='utf-8'));obj.update({k:True for k in KEYS});obj['packet_sha256']='same';put(e/new/'RESULT.json',obj)
    return r,e


def test_final_gate_accepts_matching_evidence(ready):assert M.evidence_gate(*ready)['passed']==1

@pytest.mark.parametrize('key',KEYS)
def test_missing_selective_proof_never_publishes(ready,key):
    r,e=ready;p=e/'installed-final/RESULT.json';v=json.loads(p.read_text(encoding='utf-8'));v[key]=False;put(p,v)
    with pytest.raises(ValueError):M.evidence_gate(r,e)


def test_inconsistent_packet_bytes_rejected(ready):
    r,e=ready;p=e/'installed-final/RESULT.json';v=json.loads(p.read_text(encoding='utf-8'));v['packet_sha256']='changed';put(p,v)
    with pytest.raises(ValueError):M.evidence_gate(r,e)


def test_existing_publication_directory_not_overwritten(tmp_path):
    p=tmp_path/'keep.zip';p.write_bytes(b'old')
    with pytest.raises(ValueError):M.build(tmp_path,tmp_path,tmp_path,tmp_path/'example.zip')
    assert p.read_bytes()==b'old'
