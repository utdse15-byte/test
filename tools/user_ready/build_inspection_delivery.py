"""Publish inspection maintenance only after UI, media and prior flow evidence."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path


def load_builder():
    path=Path(__file__).with_name('build_experience_delivery.py')
    spec=importlib.util.spec_from_file_location('inspection_base_builder',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def gate(repo, evidence):
    reports=[]
    required=('ok','actual_pixel_view_checked','modal_close_focus_and_scroll_restored',
              'view_does_not_dirty_verified_backup','playing_video_paused_not_restarted',
              'mobile_selected_tab_visible','field_error_focused_without_losing_other_inputs',
              'actual_browser_download','fresh_reopen_identical','legacy_r16_6_roundtrip_identical')
    for name in ('source-inspection','installed-inspection'):
        report=json.loads((evidence/name/'RESULT.json').read_text(encoding='utf-8'))
        if not all(report.get(key) is True for key in required):
            raise ValueError('Missing completed inspection acceptance: '+name)
        if report.get('javascript_errors') or report.get('network_requests'):
            raise ValueError('Browser errors or external network requests')
        if report['unchanged_media_files']<1 or report['actual_original_resolution']!=[1920,1080]:
            raise ValueError('Actual media/high-resolution inspection missing')
        reports.append(report)
    for key in ('html_sha256','checkout_sha256','media_sha256'):
        if reports[0][key]!=reports[1][key]:raise ValueError('Source/installed inspection differs: '+key)
    from hashlib import sha256
    if reports[0]['html_sha256']!=sha256((repo/'tools/model_workbench.html').read_bytes()).hexdigest():
        raise ValueError('Inspection page changed after acceptance')
    return {'actual_pixel_view':True,'source_installed_backup_identical':True,
            'legacy_r16_6_roundtrip':True,'media_files':reports[0]['unchanged_media_files']}


def build(repo,output,evidence,example):
    return load_builder().build(repo,output,evidence,example,name_prefix='MANJU_FLOW',
        report_subdir='inspection',task_pattern='TASK_视觉实测_*.md',supplemental_gate=gate)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--output',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--example',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.repo.resolve(),a.output.resolve(),a.evidence.resolve(),a.example.resolve()),ensure_ascii=False,indent=2))
