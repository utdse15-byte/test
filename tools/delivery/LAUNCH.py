"""Verified local entry point for a Manju delivery. No implicit installations."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import venv
import webbrowser


def verifier():
    # Use the verifier shipped alongside this launcher, not code from an
    # arbitrary --root target. It can check older extracted deliveries too.
    path = Path(__file__).resolve().with_name('VERIFY_PACKAGE.py')
    if not path.is_file():
        raise ValueError('Extract the complete ZIP first. VERIFY_PACKAGE.py is missing.')
    spec = importlib.util.spec_from_file_location('manju_delivery_verifier', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def python_in(environment: Path) -> Path:
    return environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def runtime_status(root: Path) -> dict:
    executable = python_in(root / '.venv')
    if not executable.is_file():
        return {'installed': False, 'reason': 'No private .venv runtime. The HTML workbench needs no Python environment.'}
    command = [str(executable), '-I', '-c',
               'import json,manju,importlib.metadata; print(json.dumps({"version":manju.__version__,"pydantic":importlib.metadata.version("pydantic"),"typer":importlib.metadata.version("typer"),"PyYAML":importlib.metadata.version("PyYAML")}))']
    try:
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=20, check=False)
        if completed.returncode:
            return {'installed': False, 'reason': 'Private environment import failed. Reinstall in a new extracted directory.'}
        info = json.loads(completed.stdout)
        return {'installed': True, **info}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'installed': False, 'reason': 'Private environment could not be inspected within 20 seconds.'}


def install(root: Path, checks, *, allow_network: bool, confirmed: bool, wheelhouse: Path | None = None) -> dict:
    if wheelhouse is not None:
        wheelhouse = wheelhouse.expanduser().resolve()
        if not wheelhouse.is_dir():
            raise ValueError('The explicitly selected offline wheel directory does not exist.')
    if not confirmed:
        mode = 'download dependencies from PyPI' if allow_network else 'use only wheels already in wheels/ (offline)'
        answer = input(f'Create a NEW private .venv here and {mode}? Type INSTALL to confirm: ')
        if answer.strip() != 'INSTALL':
            raise ValueError('Installation not confirmed. No environment was created.')
    target = root / '.venv'
    if target.exists() or target.is_symlink():
        raise ValueError('A .venv already exists. It will NOT be overwritten. Extract a fresh copy for a separate installation.')
    meta = checks.read_json(root / 'PACKAGE.json')
    wheel = checks.regular_member(root, meta['wheel'])
    # Reserve ownership before creation. Cleanup only removes the environment
    # created by this invocation, never a pre-existing installation or project.
    target.mkdir(exist_ok=False)
    try:
        venv.EnvBuilder(with_pip=True, clear=False, symlinks=False).create(target)
        executable = python_in(target)
        command = [str(executable), '-I', '-m', 'pip', '--isolated', '--disable-pip-version-check',
                   'install', '--only-binary=:all:']
        if allow_network:
            command += ['--index-url', 'https://pypi.org/simple']
        else:
            command += ['--no-index', '--find-links', str(wheelhouse or (root / 'wheels'))]
        if allow_network and wheelhouse is not None:
            command += ['--find-links', str(wheelhouse)]
        command.append(str(wheel))
        result = subprocess.run(command, cwd=root, check=False, timeout=600)
        if result.returncode:
            raise ValueError('Dependency installation failed. No existing installation was changed. For offline installation, supply compatible dependency wheels in a separate directory via --wheelhouse; do not add files to this immutable release.')
        runtime = runtime_status(root)
        if not runtime.get('installed') or runtime.get('version') != meta['version']:
            raise ValueError('Installed version/import verification failed.')
        receipt = {'version': meta['version'], 'package_git_head': meta['git_head'],
                   'wheel_sha256': checks.digest(wheel), 'network_explicitly_allowed': allow_network,
                   'installed_at': datetime.now(timezone.utc).isoformat(), 'runtime': runtime,
                   'commercial_api_called': False, 'formal_windows_release': False}
        (target / 'MANJU_INSTALLATION.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        return receipt
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise


def launch_command(root: Path, *, project: Path | None, no_open: bool, readonly: bool) -> tuple[list[str], dict]:
    if project is not None:
        project = project.expanduser().resolve()
        if not project.is_dir():
            raise ValueError('The explicitly selected project directory does not exist.')
        if project == root or root in project.parents:
            raise ValueError('Keep real film projects outside the immutable delivery directory.')
    command = [str(python_in(root / '.venv')), '-I', '-m', 'manju', 'gui']
    if project is not None:
        command.append(str(project))
    command += ['--host', '127.0.0.1', '--port', '0']
    command += ['--no-open'] if no_open else ['--app']
    if readonly:
        command += ['--readonly']
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['MANJU_EXECUTION_MODE'] = 'strict_zero_cost'
    env['PYTHONUTF8'] = '1'
    return command, env


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    sub = parser.add_subparsers(dest='action',required=True)
    verify_parser = sub.add_parser('verify',help='Check all release files, wheel, and optionally Git history.')
    verify_parser.add_argument('--git',action='store_true')
    diagnostics = sub.add_parser('diagnostics',help='Inspect without starting or installing anything. No env/secrets dump.')
    diagnostics.add_argument('--save',action='store_true',help='Write a new JSON in local-logs/.')
    workbench = sub.add_parser('workbench',help='Open the zero-install HTML workbench only.')
    workbench.add_argument('--print-only',action='store_true')
    installer = sub.add_parser('install',help='Explicitly create a NEW private environment; never overwrite one.')
    installer.add_argument('--allow-network',action='store_true',help='Explicitly allow downloading Python dependencies from PyPI.')
    installer.add_argument('--wheelhouse',type=Path,help='Explicit external directory of offline dependency wheels; does not modify the release.')
    installer.add_argument('--yes',action='store_true',help='Explicitly confirm installation without a prompt.')
    start = sub.add_parser('start',help='Run only the previously installed, version-matched private runtime.')
    start.add_argument('--project',type=Path)
    start.add_argument('--no-open',action='store_true')
    start.add_argument('--readonly',action='store_true')
    args = parser.parse_args(argv)
    try:
        if sys.version_info < (3,11):
            raise ValueError('Python 3.11 or newer is required for the full application. The HTML workbench needs no Python.')
        root = args.root.resolve()
        checks = verifier()
        verified = checks.verify(root,git=getattr(args,'git',False))
        meta = checks.read_json(root / 'PACKAGE.json')
        if args.action == 'verify':
            emit(verified)
        elif args.action == 'diagnostics':
            runtime = runtime_status(root)
            result = {'schema_id':'manju.delivery-diagnostics/v1','package_stage':meta['stage'],
                      'package_version':meta['version'],'git_head':meta['git_head'],'package_integrity':verified['ok'],
                      'os':platform.system(),'python_version':platform.python_version(),
                      'private_runtime':runtime,'version_matches':runtime.get('version')==meta['version'],
                      'ffmpeg_on_path':bool(shutil.which('ffmpeg')),'ffprobe_on_path':bool(shutil.which('ffprobe')),
                      'git_on_path':bool(shutil.which('git')),
                      'environment_variables_included':False,'personal_paths_included':False,
                      'installed_or_started':False,'windows_acceptance_verified':False,
                      'next_step':'Open OPEN_MODEL_WORKBENCH.html without installation, or explicitly install the full app.'}
            if args.save:
                directory = root / 'local-logs'
                directory.mkdir(exist_ok=True)
                # Root validation already rejected a symlink/reparse local-logs.
                name = 'diagnostics-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'
                with (directory/name).open('x',encoding='utf-8') as stream:
                    json.dump(result,stream,ensure_ascii=False,indent=2)
                result['saved_relative_path'] = 'local-logs/' + name
            emit(result)
        elif args.action == 'workbench':
            html = checks.regular_member(root,'OPEN_MODEL_WORKBENCH.html')
            if args.print_only:
                emit({'local_uri':html.as_uri(),'verified':True,'opened':False})
            else:
                opened = webbrowser.open(html.as_uri(),new=2)
                emit({'browser_open_requested':opened,'navigation_or_client_save_confirmed':False,
                      'fallback':'Double-click OPEN_MODEL_WORKBENCH.html in this extracted directory.'})
                if not opened:
                    return 2
        elif args.action == 'install':
            emit(install(root,checks,allow_network=args.allow_network,confirmed=args.yes,wheelhouse=args.wheelhouse))
        else:
            runtime = runtime_status(root)
            if not runtime.get('installed') or runtime.get('version') != meta['version']:
                raise ValueError('A version-matched private runtime is missing. Run INSTALL_WINDOWS.cmd explicitly, or use the HTML workbench without installation.')
            command, env = launch_command(root,project=args.project,no_open=args.no_open,readonly=args.readonly)
            print('Starting the verified local application in strict_zero_cost mode. Ctrl+C stops this server.',flush=True)
            return subprocess.call(command,cwd=root,env=env)
        return 0
    except KeyboardInterrupt:
        print('Stopped by the user. Existing projects and previous release directories are not deleted.',file=sys.stderr)
        return 130
    except Exception as exc:
        print('MANJU DID NOT START/INSTALL: ' + str(exc),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
