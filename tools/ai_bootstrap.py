"""Read-only cold-start inventory. No installs, credentials, project mutation or network."""
from __future__ import annotations
import argparse
import ast
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def inspect(root: Path = ROOT) -> dict:
    required = ['AGENTS.md', 'CLAUDE.md', 'STATE.md', 'docs/ai/OPERATIONS.md',
                'docs/ai/DEVELOPMENT_GUARDS.md', 'skills/manju/SKILL.md',
                'src/manju/authoring/ide.py', 'DELIVERY_VERSION.json']
    missing = [name for name in required if not (root/name).is_file()]
    states = sorted(root.glob('PROJECT_STATE_*.md'), key=lambda p:p.name)
    declared = json.loads((root/'DELIVERY_VERSION.json').read_text(encoding='utf-8'))['version']
    runtime = None
    for node in ast.parse((root/'src/manju/__init__.py').read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in node.targets):
            runtime = ast.literal_eval(node.value)
    dependencies = {}
    for package in ('manju', 'pydantic', 'typer', 'PyYAML', 'pytest', 'playwright'):
        try: dependencies[package] = metadata.version(package)
        except metadata.PackageNotFoundError: dependencies[package] = None
    return {'ok': not missing and bool(states) and declared == runtime,
            'read_only': True, 'network_calls': 0, 'credentials_read': False,
            'git_worktree_present': (root/'.git').exists(),
            'adjacent_history_bundle_present': (root.parent/'repository.bundle').is_file(),
            'ok_scope': 'source_entrypoints_and_version_only_not_runtime_or_ide_certification',
            'declared_version': declared, 'source_runtime_version': runtime,
            'python_supported': sys.version_info >= (3,11),
            'installed_packages': dependencies,
            'installed_manju_matches_source': dependencies['manju'] == declared,
            'media_tools_on_path': {name: bool(shutil.which(name)) for name in ('ffmpeg','ffprobe')},
            'missing_files': missing, 'latest_project_state': states[-1].name if states else None,
            'read_next': ([states[-1].name] if states else [])+['AGENTS.md','STATE.md','docs/ai/OPERATIONS.md'],
            'available_skills': sorted(p.parent.name for p in (root/'skills').glob('*/SKILL.md')),
            'first_commands': ['python -m manju --version','python -m manju models ide-open --help',
                               'python -m manju models ide-preview --help','python -m manju models ide-return --help'],
            'important': ['browser_unsaved_state_is_not_accessible', 'full_return_requires_explicit_restore',
                          'instructions_are_context_not_security_enforcement', 'no_commercial_execution_verified']}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--json',action='store_true')
    args=parser.parse_args()
    try: result=inspect()
    except (OSError,ValueError,KeyError,SyntaxError) as exc:
        result={'ok':False,'error':str(exc),'read_only':True}
    if args.json: print(json.dumps(result,ensure_ascii=False,indent=2))
    else:
        print('Manju AI 接管自检（只读）')
        print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['ok'] else 2


if __name__=='__main__': raise SystemExit(main())
