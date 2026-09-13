"""Do not publish the new UI based only on old protocol-test evidence."""
from pathlib import Path
import importlib.util
import json
import pytest
from tests.test_retouch_delivery import gate
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('experience_delivery',ROOT/'tools/user_ready/build_experience_delivery.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

@pytest.fixture
def ui_gate(gate):
    repo,e=gate
    result={k:True for k in ('ok','actual_browser_download','navigation_preserves_all_fields','hidden_video_paused_without_seek','fresh_page_roundtrip_identical','legacy_r16_5_roundtrip_identical')}
    result['checkout_sha256']='same-output'
    for name in ('source-experience','installed-experience'):
        (e/name).mkdir();(e/name/'RESULT.json').write_text(json.dumps(result),encoding='utf-8')
    return repo,e


def test_ui_gate_requires_actual_focused_rehearsals(ui_gate):
    assert M.evidence_gate(*ui_gate)['tests']==1

@pytest.mark.parametrize('field',['ok','actual_browser_download','navigation_preserves_all_fields','hidden_video_paused_without_seek','fresh_page_roundtrip_identical','legacy_r16_5_roundtrip_identical','checkout_sha256'])
def test_ui_gate_rejects_bad_or_divergent_results(ui_gate,field):
    repo,e=ui_gate;p=e/'installed-experience/RESULT.json';d=json.loads(p.read_text());d[field]=False if field!='checkout_sha256' else 'different';p.write_text(json.dumps(d))
    with pytest.raises(ValueError):M.evidence_gate(repo,e)


def test_ui_gate_rejects_missing_default_view_evidence(ui_gate):
    repo,e=ui_gate;(e/'source-experience/RESULT.json').unlink()
    with pytest.raises(OSError):M.evidence_gate(repo,e)
