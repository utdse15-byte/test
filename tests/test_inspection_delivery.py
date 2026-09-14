from pathlib import Path
import importlib.util
from hashlib import sha256
import json
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('inspection_delivery',ROOT/'tools/user_ready/build_inspection_delivery.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

@pytest.fixture
def evidence(tmp_path):
    repo=tmp_path/'repo';(repo/'tools').mkdir(parents=True)
    (repo/'tools/model_workbench.html').write_text('fixture-only',encoding='utf-8')
    ev=tmp_path/'evidence';ev.mkdir()
    report={k:True for k in ('ok','actual_pixel_view_checked','modal_close_focus_and_scroll_restored',
        'view_does_not_dirty_verified_backup','playing_video_paused_not_restarted','mobile_selected_tab_visible',
        'field_error_focused_without_losing_other_inputs','actual_browser_download','fresh_reopen_identical','legacy_r16_6_roundtrip_identical')}
    report.update(unchanged_media_files=5,actual_original_resolution=[1920,1080],javascript_errors=[],network_requests=[],
        checkout_sha256='fixture-hash',html_sha256=sha256(b'fixture-only').hexdigest(),media_sha256={'fixture':'same'})
    for name in ['source-inspection','installed-inspection']:
        (ev/name).mkdir();(ev/name/'RESULT.json').write_text(json.dumps(report),encoding='utf-8')
    return repo,ev


def test_inspection_gate_accepts_matching_complete_evidence(evidence):
    assert M.gate(*evidence)['media_files']==5


@pytest.mark.parametrize('key,value',[
 ('ok',False),('actual_pixel_view_checked',False),('modal_close_focus_and_scroll_restored',False),
 ('view_does_not_dirty_verified_backup',False),('actual_browser_download',False),('legacy_r16_6_roundtrip_identical',False),
 ('checkout_sha256','changed'),('html_sha256','changed'),('media_sha256',{'fixture':'changed'}),
 ('unchanged_media_files',0),('actual_original_resolution',[640,360]),('javascript_errors',['boom']),
 ('network_requests',['https://example.invalid'])])
def test_inspection_gate_rejects_incomplete_or_divergent_evidence(evidence,key,value):
    repo,ev=evidence;p=ev/'installed-inspection/RESULT.json';r=json.loads(p.read_text(encoding='utf-8'));r[key]=value;p.write_text(json.dumps(r), encoding='utf-8')
    with pytest.raises(ValueError):M.gate(repo,ev)


def test_inspection_gate_rejects_changed_page(evidence):
    repo,ev=evidence;(repo/'tools/model_workbench.html').write_text('modified',encoding='utf-8')
    with pytest.raises(ValueError):M.gate(repo,ev)


def test_inspection_gate_missing_report_not_accepted(evidence):
    repo,ev=evidence;(ev/'installed-inspection/RESULT.json').unlink()
    with pytest.raises(FileNotFoundError):M.gate(repo,ev)
