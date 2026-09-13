from pathlib import Path
import importlib.util
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_readonly_bootstrap_and_real_paths():
    p=ROOT/'tools/ai_bootstrap.py'
    spec=importlib.util.spec_from_file_location('ai_bootstrap',p);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    before={n:p.read_bytes() for n in ['STATE.md','AGENTS.md','DELIVERY_VERSION.json'] for p in [ROOT/n]}
    report=mod.inspect()
    assert report['ok'] and report['read_only'] and not report['credentials_read']
    assert report['latest_project_state']==max(ROOT.glob('PROJECT_STATE_*.md')).name
    assert all((ROOT/name).is_file() for name in report['read_next'])
    assert all((ROOT/n).read_bytes()==data for n,data in before.items())


def test_supported_entrypoints_share_one_policy():
    assert (ROOT/'CLAUDE.md').read_text(encoding='utf-8').startswith('@AGENTS.md')
    assert 'docs/ai/OPERATIONS.md' in (ROOT/'AGENTS.md').read_text(encoding='utf-8')
    assert '历史' in (ROOT/'IDE_HANDOFF.md').read_text(encoding='utf-8')
    assert 'events --tail' not in (ROOT/'skills/manju/SKILL.md').read_text(encoding='utf-8')


def test_documented_event_command_actually_exists():
    r=subprocess.run([sys.executable,'-m','manju','events','--help'],capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert r.returncode==0 and '-n' in r.stdout and '--json' in r.stdout


def test_bootstrap_from_unrelated_cwd(tmp_path):
    r=subprocess.run([sys.executable,str(ROOT/'tools/ai_bootstrap.py'),'--json'],cwd=tmp_path,capture_output=True,text=True,encoding='utf-8',timeout=20)
    assert r.returncode==0 and json.loads(r.stdout)['ok']


def test_documented_production_groups_exist():
    from manju.cli import app
    from typer.main import get_command
    root=get_command(app)
    for name in ['status','events','production','create','series','assets','refs','appearances',
                 'prompt','director','ingest','import','transcribe','voice','export','check','qc','pack','unpack']:
        assert name in root.commands, name
