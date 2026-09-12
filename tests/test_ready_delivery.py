"""User-ready maintenance: version identity and read-only offline startup."""
import ast
import importlib.util
import json
from pathlib import Path
import tomllib

REPO = Path(__file__).resolve().parents[1]


def test_declared_runtime_and_delivery_versions_are_identical():
    project = tomllib.loads((REPO / 'pyproject.toml').read_text(encoding='utf-8'))
    tree = ast.parse((REPO / 'src/manju/__init__.py').read_text(encoding='utf-8'))
    runtime = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)]
    delivery = json.loads((REPO / 'DELIVERY_VERSION.json').read_text(encoding='utf-8'))
    assert runtime == [project['project']['version']] == [delivery['version']]

import hashlib
import http.client
import threading
from urllib.parse import urlsplit
import pytest


def load_tool(name):
    spec = importlib.util.spec_from_file_location('ready_' + name, REPO / 'tools/delivery' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LOCAL = load_tool('LOCAL_WORKBENCH')


@pytest.fixture
def local_root(tmp_path):
    root = tmp_path / '新目录 with spaces'
    root.mkdir()
    data = {'START_HERE.html': b'<!doctype html><title>start</title>',
            'APP/OPEN_MODEL_WORKBENCH.html': b'<!doctype html><title>workspace</title>',
            'EXAMPLES/CHECKOUT.zip': b'zip fixture, not decoded by the static server'}
    for name, value in data.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    (root / 'LOCAL_WORKBENCH_FILES.json').write_text(
        json.dumps({k: hashlib.sha256(v).hexdigest() for k, v in data.items()}), encoding='utf-8')
    return root


@pytest.fixture
def local_http(local_root):
    server, url = LOCAL.create_server(local_root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield local_root, server, urlsplit(url)
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()


def request(server, path, *, method='GET', headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    conn.request(method, path, headers=headers or {})
    res = conn.getresponse()
    result = res.status, dict(res.getheaders()), res.read()
    conn.close()
    return result


def test_server_serves_only_verified_immutable_snapshot(local_http):
    root, server, url = local_http
    assert server.server_address[0] == '127.0.0.1'
    status, headers, content = request(server, url.path)
    assert status == 200 and content == (root / 'START_HERE.html').read_bytes()
    assert headers['Cache-Control'] == 'no-store'
    assert "connect-src 'none'" in headers['Content-Security-Policy']
    assert 'Access-Control-Allow-Origin' not in headers
    page = root / 'APP/OPEN_MODEL_WORKBENCH.html'
    old = page.read_bytes()
    page.write_bytes(b'changed after startup')
    assert request(server, url.path + 'APP/OPEN_MODEL_WORKBENCH.html')[2] == old


@pytest.mark.parametrize('suffix', ['../START_HERE.html', '%2e%2e/START_HERE.html',
                                 'APP/../START_HERE.html', 'APP/', 'LOCAL_WORKBENCH_FILES.json',
                                 'APP/OPEN_MODEL_WORKBENCH.html?x=1', 'START_HERE.html/',
                                 'EXAMPLES/../secret', 'APP\\OPEN_MODEL_WORKBENCH.html'])
def test_server_never_normalizes_or_lists_paths(local_http, suffix):
    _, server, url = local_http
    assert request(server, url.path + suffix)[0] == 404


def test_token_required_and_head_has_no_body(local_http):
    root, server, url = local_http
    assert request(server, '/')[0] == 404
    code, headers, data = request(server, url.path, method='HEAD')
    assert code == 200 and data == b''
    assert int(headers['Content-Length']) == (root / 'START_HERE.html').stat().st_size
    code, headers, _ = request(server, url.path + 'EXAMPLES/CHECKOUT.zip')
    assert code == 200 and 'attachment' in headers['Content-Disposition']


@pytest.mark.parametrize('headers', [{'Host': 'example.com'}, {'Origin': 'https://evil.example'},
                                    {'Origin': 'null'}, {'Sec-Fetch-Site': 'cross-site'}])
def test_foreign_requests_rejected(local_http, headers):
    _, server, url = local_http
    assert request(server, url.path, headers=headers)[0] == 403


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
def test_no_writing_routes(local_http, method):
    root, server, url = local_http
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert request(server, url.path, method=method)[0] == 405
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('attack', ['tamper', 'missing', 'extra-key', 'duplicate-key', 'oversize', 'bad-hash', 'symlink'])
def test_bad_package_refuses_before_binding(local_root, attack, monkeypatch):
    manifest = local_root / 'LOCAL_WORKBENCH_FILES.json'
    page = local_root / 'START_HERE.html'
    if attack == 'tamper':
        page.write_bytes(b'changed')
    elif attack == 'missing':
        page.unlink()
    elif attack == 'extra-key':
        data = json.loads(manifest.read_text(encoding='utf-8'));data['../other'] = '0' * 64
        manifest.write_text(json.dumps(data), encoding='utf-8')
    elif attack == 'duplicate-key':
        manifest.write_text('{"x":1,"x":2}', encoding='utf-8')
    elif attack == 'oversize':
        page.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
    elif attack == 'bad-hash':
        data = json.loads(manifest.read_text(encoding='utf-8'));data['START_HERE.html'] = 'wrong'
        manifest.write_text(json.dumps(data), encoding='utf-8')
    elif attack == 'symlink':
        page.unlink();page.symlink_to(local_root / 'APP/OPEN_MODEL_WORKBENCH.html')
    monkeypatch.setattr(LOCAL, 'LocalServer', lambda *a, **k: pytest.fail('must validate before bind'))
    with pytest.raises((ValueError, OSError)):
        LOCAL.create_server(local_root)


def test_each_server_has_independent_token(local_root):
    a, url_a = LOCAL.create_server(local_root)
    b, url_b = LOCAL.create_server(local_root)
    try:
        assert urlsplit(url_a).path != urlsplit(url_b).path
    finally:
        a.server_close(); b.server_close()
