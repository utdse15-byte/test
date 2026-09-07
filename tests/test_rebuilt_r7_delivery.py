"""Adversarial download/package validation and explicit, non-destructive launch gates."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
import pytest

REPO=Path(__file__).resolve().parents[1]
def module(name):
    path=REPO/'tools/delivery'/f'{name}.py'
    spec=importlib.util.spec_from_file_location('r7_'+name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
V=module('VERIFY_PACKAGE');Z=module('ZIP_CHECK');L=module('LAUNCH')


def seal(root):
    sums={p.relative_to(root).as_posix():V.digest(p) for p in root.rglob('*') if p.is_file() and p.name!='SHA256SUMS.json'}
    (root/'SHA256SUMS.json').write_text(json.dumps(sums,ensure_ascii=False),encoding='utf-8')


@pytest.fixture
def package(tmp_path):
    root=tmp_path/'包';root.mkdir();(root/'source/src/manju').mkdir(parents=True);(root/'wheels').mkdir()
    runtime=b'__version__="0.2.0+r7"\n';(root/'source/src/manju/__init__.py').write_bytes(runtime)
    meta={'stage':'R7','version':'0.2.0+r7','baseline':'a'*40,'git_head':'b'*40,'git_tree':'c'*40,
          'inherits':['R2','R3','R4','R5','R6'],'features':{},'original_r3_r4_bytes_recovered':False,
          'formal_windows_release':False,'wheel':'wheels/manju.whl'}
    (root/'PACKAGE.json').write_text(json.dumps(meta));(root/'source/DELIVERY_VERSION.json').write_text(json.dumps(meta))
    with zipfile.ZipFile(root/meta['wheel'],'w') as z:z.writestr('manju/__init__.py',runtime)
    for name in ['repository.bundle','CHANGES_FROM_R2.patch','START_HERE.md','VERIFY_PACKAGE.py']:(root/name).write_text('fixture')
    (root/'OPEN_MODEL_WORKBENCH.html').write_text('<!doctype html><title>local</title>')
    seal(root);return root


def test_package_integrity_and_explicit_local_runtime_directories(package):
    before=V.verify(package);assert before['stage']=='R7' and before['wheel_runtime_files']==1
    assert not before['git_restore_checked']
    for directory in ['.venv','workspace','local-logs','__pycache__']:
        (package/directory).mkdir();(package/directory/'local.txt').write_text('mutable local state, not release payload')
    assert V.verify(package)['checked_files']==before['checked_files']


@pytest.mark.parametrize('name',['../outside','/abs','C:/path','x\\y','a/./b','a//b','NUL.txt','aux/x','x.','x /a','a\n.txt','a:b','a?b',''])
def test_portable_paths_rejected(name):
    with pytest.raises(ValueError):V.safe_name(name)
    with pytest.raises(ValueError):Z.checked_name(name)


@pytest.mark.parametrize('attack',['tamper','extra','missing','symlink','dir_symlink','local_symlink','version','inherits','wheel_path','hash_format','duplicate_json','inventory_collision'])
def test_package_fail_closed(package,tmp_path,attack):
    if attack=='tamper':(package/'START_HERE.md').write_text('changed')
    if attack=='extra':(package/'unexpected.txt').write_text('unlisted')
    if attack=='missing':(package/'START_HERE.md').unlink()
    if attack=='symlink':
        outside=tmp_path/'outside.txt';outside.write_text('fixture');(package/'START_HERE.md').unlink();(package/'START_HERE.md').symlink_to(outside)
    if attack=='dir_symlink':
        outside=tmp_path/'outside';outside.mkdir();(package/'newdir').symlink_to(outside,target_is_directory=True)
    if attack=='local_symlink':
        (package/'local-logs').symlink_to(tmp_path,target_is_directory=True)
    if attack in {'version','inherits','wheel_path'}:
        value=json.loads((package/'PACKAGE.json').read_text())
        if attack=='version':value['version']='false-upgrade'
        if attack=='inherits':value['inherits']=['R2','R4']
        if attack=='wheel_path':value['wheel']='../other.whl'
        (package/'PACKAGE.json').write_text(json.dumps(value));seal(package)
    if attack=='hash_format':
        sums=json.loads((package/'SHA256SUMS.json').read_text());sums['START_HERE.md']='oops';(package/'SHA256SUMS.json').write_text(json.dumps(sums))
    if attack=='duplicate_json':(package/'SHA256SUMS.json').write_text('{"x":"a","x":"b"}')
    if attack=='inventory_collision':
        (package/'Case').mkdir();(package/'case').mkdir();(package/'Case/a').write_text('a');(package/'case/b').write_text('b');seal(package)
    with pytest.raises((ValueError,OSError)):V.verify(package)


@pytest.mark.parametrize('attack',['byte_change','duplicate','traversal','symlink'])
def test_wheel_fail_closed_even_package_resealed(package,attack):
    wheel=package/'wheels/manju.whl';runtime=(package/'source/src/manju/__init__.py').read_bytes()
    with zipfile.ZipFile(wheel,'w') as z:
        z.writestr('manju/__init__.py',b'changed' if attack=='byte_change' else runtime)
        if attack=='duplicate':
            with pytest.warns(UserWarning):z.writestr('manju/__init__.py',runtime)
        if attack=='traversal':z.writestr('../outside','bad')
        if attack=='symlink':
            info=zipfile.ZipInfo('manju/link');info.create_system=3;info.external_attr=0o120777<<16;z.writestr(info,'/outside')
    seal(package)
    with pytest.raises(ValueError):V.verify(package)


def write_zip(path,entries):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in entries:z.writestr(name,data)
    return path


def test_safe_zip_actual_extract_and_existing_output_untouched(tmp_path):
    z=write_zip(tmp_path/'pack.zip',[('Manju/说明.md','保留字节'),('Manju/a.bin',b'abc')]);out=tmp_path/'restored'
    result=Z.inspect_archive(z,expected_sha256=V.digest(z),extract_to=out)
    assert result['zip_crc_passed'] and result['saved_receipt_matched'] and not result['code_executed']
    assert (out/'Manju/说明.md').read_text()=='保留字节'
    with pytest.raises(FileExistsError):Z.inspect_archive(z,extract_to=out)
    assert (out/'Manju/a.bin').read_bytes()==b'abc'


@pytest.mark.parametrize('paths',[['../x'],['/abs'],['C:/x'],['x\\y'],['A/x','a/y'],['a','a/x'],['é/x','e\u0301/y'],['CON.txt'],['a.txt','A.txt']])
def test_dangerous_zip_never_extracted(tmp_path,paths):
    z=write_zip(tmp_path/'bad.zip',[(p,b'fixture') for p in paths]);out=tmp_path/'out'
    with pytest.raises(ValueError):Z.inspect_archive(z,extract_to=out)
    assert not out.exists()


def test_zip_duplicate_and_symlink(tmp_path):
    z=tmp_path/'bad.zip'
    with pytest.warns(UserWarning):write_zip(z,[('a',b'1'),('a',b'2')])
    with pytest.raises(ValueError):Z.inspect_archive(z)
    with zipfile.ZipFile(z,'w') as archive:
        info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=0o120777<<16;archive.writestr(info,'/outside')
    with pytest.raises(ValueError):Z.inspect_archive(z)


def test_wrong_download_hash_no_write(tmp_path):
    z=write_zip(tmp_path/'ok.zip',[('a',b'1')]);out=tmp_path/'out'
    with pytest.raises(ValueError):Z.inspect_archive(z,expected_sha256='0'*64,extract_to=out)
    assert not out.exists()


def test_launcher_diagnostics_no_install_no_personal_dump(package,monkeypatch,capsys):
    monkeypatch.setenv('SECRET_TEST_TOKEN','never-in-diagnostics')
    assert L.main(['--root',str(package),'diagnostics','--save'])==0
    data=json.loads(capsys.readouterr().out)
    assert not data['installed_or_started'] and not data['environment_variables_included']
    assert 'never-in-diagnostics' not in json.dumps(data)
    assert str(package) not in json.dumps(data)
    assert not (package/'.venv').exists()
    assert V.verify(package)['ok']


def test_launcher_missing_runtime_refuses_start(package,monkeypatch,capsys):
    def forbidden(*args,**kwargs):raise AssertionError('Must not start or install a subprocess')
    monkeypatch.setattr(L.subprocess,'call',forbidden)
    assert L.main(['--root',str(package),'start'])==2
    assert 'missing' in capsys.readouterr().err


def test_unconfirmed_install_no_environment(package,monkeypatch):
    monkeypatch.setattr('builtins.input',lambda _: 'NO')
    with pytest.raises(ValueError):L.install(package,V,allow_network=False,confirmed=False)
    assert not (package/'.venv').exists()


def test_existing_environment_never_touched(package):
    (package/'.venv').mkdir();marker=package/'.venv/preserve.txt';marker.write_text('mine')
    with pytest.raises(ValueError):L.install(package,V,allow_network=False,confirmed=True)
    assert marker.read_text()=='mine'


def test_failed_new_install_cleanup_only_owned_directory(package,monkeypatch):
    def fail(self,path):
        (path/'partial.txt').write_text('incomplete');raise OSError('simulated failure')
    monkeypatch.setattr(L.venv.EnvBuilder,'create',fail)
    with pytest.raises(OSError):L.install(package,V,allow_network=False,confirmed=True)
    assert not (package/'.venv').exists() and (package/'START_HERE.md').exists()
    assert V.verify(package)['ok']


def test_launch_command_is_local_and_explicit_zero_cost(package,tmp_path,monkeypatch):
    film=tmp_path/'真实作品';film.mkdir();monkeypatch.setenv('PYTHONPATH','untrusted');monkeypatch.setenv('MANJU_EXECUTION_MODE','allow_paid')
    cmd,env=L.launch_command(package,project=film,no_open=True,readonly=True)
    assert '--no-open' in cmd and '--readonly' in cmd and '--app' not in cmd
    assert '127.0.0.1' in cmd and cmd[1]=='-I' and str(film) in cmd
    assert env['MANJU_EXECUTION_MODE']=='strict_zero_cost' and 'PYTHONPATH' not in env
    with pytest.raises(ValueError):L.launch_command(package,project=package,no_open=True,readonly=True)


def test_launcher_can_print_verified_workbench_uri(package,capsys):
    assert L.main(['--root',str(package),'workbench','--print-only'])==0
    assert json.loads(capsys.readouterr().out)['opened'] is False


def test_windows_wrappers_quote_paths_and_do_not_overwrite():
    for name in ['START_WINDOWS.cmd','INSTALL_WINDOWS.cmd','VERIFY_WINDOWS.cmd']:
        text=(REPO/'tools/delivery'/name).read_bytes()
        assert b'\r\n' in text and b'"%~dp0LAUNCH.py"' in text and b'PYTHONUTF8=1' in text
        assert b'pause' in text and b'rmdir' not in text and b'--yes' not in text


def test_r6_proof_fixture_still_verifies():
    from manju.authoring.core import verify_bundle
    result=verify_bundle(REPO/'tests/fixtures/authoring_r6_final',require_promotion=True)
    assert result['promotion_verified']


def test_missing_external_wheelhouse_refused_before_environment(package,tmp_path):
    with pytest.raises(ValueError):
        L.install(package,V,allow_network=False,confirmed=True,wheelhouse=tmp_path/'missing')
    assert not (package/'.venv').exists()


def test_explicit_external_wheelhouse_does_not_modify_release(package,tmp_path,monkeypatch):
    wheelhouse=tmp_path/'offline-dependencies';wheelhouse.mkdir();calls=[]
    def create(self,path):
        executable=L.python_in(path);executable.parent.mkdir(parents=True,exist_ok=True);executable.write_text('fixture')
    def run(cmd,**kwargs):
        calls.append(cmd);return subprocess.CompletedProcess(cmd,0)
    monkeypatch.setattr(L.venv.EnvBuilder,'create',create)
    monkeypatch.setattr(L.subprocess,'run',run)
    monkeypatch.setattr(L,'runtime_status',lambda root:{'installed':True,'version':'0.2.0+r7'})
    before=V.verify(package)['checked_files']
    result=L.install(package,V,allow_network=False,confirmed=True,wheelhouse=wheelhouse)
    assert '--no-index' in calls[0] and str(wheelhouse.resolve()) in calls[0]
    assert '--index-url' not in calls[0] and not result['network_explicitly_allowed']
    assert V.verify(package)['checked_files']==before
