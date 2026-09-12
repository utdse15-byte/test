"""Packaging boundaries independent of the interactive recovery service."""
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('continue_delivery_under_test', ROOT / 'tools/user_ready/build_continue_delivery.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_reject_existing_output_before_build(tmp_path):
    (tmp_path / 'old.zip').write_bytes(b'unchanged')
    with pytest.raises(ValueError, match='never replaced'):
        build.build(ROOT, tmp_path, tmp_path, tmp_path / 'missing')
    assert (tmp_path / 'old.zip').read_bytes() == b'unchanged'


def test_missing_completion_record_cannot_publish(tmp_path):
    out = tmp_path / 'new'; out.mkdir()
    with pytest.raises(FileNotFoundError):
        build.build(ROOT, out, tmp_path, tmp_path / 'missing')
    assert not list(out.iterdir())


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a\\b', 'font.ttf', 'font.woff2'])
def test_bad_archive_rejected(tmp_path, name):
    p = tmp_path / 'x.zip'
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr(name, b'x')
    with pytest.raises(ValueError):
        build.verify_zip(p)


def test_duplicate_entries_rejected(tmp_path):
    p = tmp_path / 'x.zip'
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('a', b'x'); z.writestr('a', b'x')
    with pytest.raises(ValueError, match='Duplicate'):
        build.verify_zip(p)


def test_common_inventory_and_launchers(tmp_path):
    fixture = tmp_path / 'fixture.zip'
    with zipfile.ZipFile(fixture, 'w') as z:
        z.writestr('placeholder', b'only a static packaging fixture')
    root = tmp_path / 'kit'
    build.common(ROOT, root, fixture)
    for file in ['LOCAL_WORKBENCH.py','LOCAL_CONTINUE.py','CONTINUE_CLIENT.js',
                 'OPEN_CONTINUE_WINDOWS.cmd','OPEN_LOCAL_WINDOWS.cmd','START_HERE.html']:
        assert (root / file).is_file()
    runtime = (ROOT/'src/manju/authoring/data/workbench.html').read_bytes()
    assert (root/'APP/OPEN_MODEL_WORKBENCH.html').read_bytes() == runtime
    for file in ['LOCAL_WORKBENCH_FILES.json', 'CONTINUE_FILES.json']:
        for rel, expected in json.loads((root/file).read_text()).items():
            assert build.sha(root/rel) == expected
    receipt = build.inventory(root, '0.2.0+r16.2', 'fixture-not-a-release')
    assert receipt['ok']
    archive = tmp_path/'kit.zip'; build.zip_tree(root, archive)
    with pytest.raises(FileExistsError):
        build.zip_tree(root, archive)


def test_actual_fragment_api_and_reversed_recovery(tmp_path):
    parts = build.module(ROOT/'tools/user_ready/download_parts.py', 'fragment_under_test')
    source = tmp_path/'whole.zip';source.write_bytes(bytes(range(256))*20)
    manifest = parts.split_archive(source, tmp_path/'parts', block_size=1024)
    files=[]
    for item in manifest['parts']:
        old = tmp_path/'parts'/item['name']; new=old.with_name('RENAMED_'+old.name)
        old.rename(new); item['name']=new.name;files.append(new)
    result = parts.reassemble(files[::-1],tmp_path/'restored.zip',manifest)
    assert result['ok'] and (tmp_path/'restored.zip').read_bytes()==source.read_bytes()
