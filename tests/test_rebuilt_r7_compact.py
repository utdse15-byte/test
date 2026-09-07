"""Small downloadable toolkit: commit binding, exact bytes and exclusive publication."""
from pathlib import Path
from hashlib import sha256
import importlib.util,json,subprocess,zipfile
import pytest
from manju.authoring.core import Request

path=Path(__file__).resolve().parents[1]/'tools/rebuilt-delivery/package_offline_tools.py'
spec=importlib.util.spec_from_file_location('manju_compact_packager',path)
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)

@pytest.fixture
def repo(tmp_path):
    r=tmp_path/'source';r.mkdir();(r/'tools').mkdir();(r/'src/manju/authoring/data').mkdir(parents=True)
    (r/'DELIVERY_VERSION.json').write_text(json.dumps({'stage':'R7','version':'0.2.0+r7','features':{}}))
    html=b'<!doctype html><title>offline fixture</title>'
    (r/'tools/model_workbench.html').write_bytes(html);(r/'src/manju/authoring/data/workbench.html').write_bytes(html)
    (r/'src/manju/authoring/data/catalog.json').write_text('{}')
    for args in [['init','--quiet'],['config','user.name','Manju Test'],['config','user.email','test@example.invalid'],['add','.'],['commit','--quiet','-m','Synthetic compact package fixture']]:
        subprocess.run(['git',*args],cwd=r,check=True,capture_output=True)
    return r


def commit(repo):
    subprocess.run(['git','add','.'],cwd=repo,check=True,capture_output=True)
    subprocess.run(['git','commit','--quiet','-m','Synthetic fixture revision'],cwd=repo,check=True,capture_output=True)


def test_compact_zip_fresh_bytes_and_valid_example(repo,tmp_path):
    target=tmp_path/'actual.zip';result=P.build(repo,target)
    assert result['zip_crc_passed'] and result['fresh_extraction_checked'] and not result['client_download_confirmed']
    assert result['archive_sha256']==sha256(target.read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as z:
        prefix='MANJU_R7_OFFLINE_TOOLS/';sums=json.loads(z.read(prefix+'SHA256SUMS.json'))
        assert set(z.namelist())=={prefix+n for n in sums}|{prefix+'SHA256SUMS.json'}
        for name,digest in sums.items():assert sha256(z.read(prefix+name)).hexdigest()==digest
        Request.model_validate_json(z.read(prefix+'EXAMPLE_REQUEST.json'))
        assert z.read(prefix+'OPEN_MODEL_WORKBENCH.html')==(repo/'tools/model_workbench.html').read_bytes()
        version=json.loads(z.read(prefix+'VERSION.json'));assert version['git_head']==result['git_head']
        assert not version['full_native_application_included']


def test_compact_never_overwrites_old_zip(repo,tmp_path):
    target=tmp_path/'keep.zip';target.write_bytes(b'old-user-file')
    with pytest.raises(FileExistsError):P.build(repo,target)
    assert target.read_bytes()==b'old-user-file'


def test_compact_refuses_dirty_source(repo,tmp_path):
    (repo/'extra').write_text('not committed')
    with pytest.raises(ValueError):P.build(repo,tmp_path/'new.zip')
    assert not (tmp_path/'new.zip').exists()


def test_compact_refuses_wrong_stage(repo,tmp_path):
    p=repo/'DELIVERY_VERSION.json';v=json.loads(p.read_text());v['stage']='R6';p.write_text(json.dumps(v));commit(repo)
    with pytest.raises(ValueError):P.build(repo,tmp_path/'new.zip')
    assert not (tmp_path/'new.zip').exists()


def test_compact_refuses_divergent_installed_page(repo,tmp_path):
    (repo/'src/manju/authoring/data/workbench.html').write_text('different');commit(repo)
    with pytest.raises(ValueError):P.build(repo,tmp_path/'new.zip')
    assert not (tmp_path/'new.zip').exists()
