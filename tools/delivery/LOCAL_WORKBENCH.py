"""Read-only loopback fallback for the extracted, zero-install HTML workbench.

No package installation, project writes, directory serving, uploads or persistence.
Only a bounded, hash-checked in-memory snapshot of three named files is served.
Closing this console stops the server; it does not save unsaved browser work.
"""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import stat
import sys
import webbrowser

ROUTES = {
    'START_HERE.html': ('text/html; charset=utf-8', 2 * 1024 * 1024),
    'APP/OPEN_MODEL_WORKBENCH.html': ('text/html; charset=utf-8', 4 * 1024 * 1024),
    'EXAMPLES/CHECKOUT.zip': ('application/zip', 4 * 1024 * 1024),
}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key in local file inventory')
        result[key] = value
    return result


def regular_file(root: Path, relative: str) -> Path:
    current = root
    for part in relative.split('/'):
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked files or reparse points are not served')
    if not current.is_file():
        raise ValueError('Extract the complete package into a regular directory first')
    return current


def load_snapshot(root: Path) -> dict[str, tuple[bytes, str]]:
    manifest = regular_file(root, 'LOCAL_WORKBENCH_FILES.json')
    if manifest.stat().st_size > 8192:
        raise ValueError('Local inventory exceeds its size limit')
    expected = json.loads(manifest.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(expected, dict) or set(expected) != set(ROUTES):
        raise ValueError('Local inventory must declare exactly the three allowed files')
    result = {}
    for name, (mime, maximum) in ROUTES.items():
        if not isinstance(expected[name], str) or not re.fullmatch(r'[a-f0-9]{64}', expected[name]):
            raise ValueError('Invalid local inventory digest')
        path = regular_file(root, name)
        # Bound reads even if another process changes a file after stat().
        with path.open('rb') as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum or hashlib.sha256(data).hexdigest() != expected[name]:
            raise ValueError('Local file changed or was incompletely extracted: ' + name)
        result[name] = (data, mime)
    return result


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    request_queue_size = 8

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address


def create_server(root: Path) -> tuple[LocalServer, str]:
    snapshot = load_snapshot(root.resolve())
    token = secrets.token_urlsafe(24)
    prefix = '/' + token + '/'

    class Handler(BaseHTTPRequestHandler):
        server_version = 'ManjuLocal'
        sys_version = ''

        def log_message(self, *_):
            # Do not print tokens, file names, headers or request paths.
            pass

        def reply(self, code, data=b'', mime='text/plain; charset=utf-8', *, head=False, attachment=False):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src blob: data:; media-src blob:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.send_header('Connection', 'close')
            if attachment:
                self.send_header('Content-Disposition', 'attachment; filename="CHECKOUT.zip"')
            self.end_headers()
            if not head:
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
            self.close_connection = True

        def serve(self, *, head=False):
            host = '127.0.0.1:' + str(self.server.server_port)
            if self.headers.get_all('Host', []) != [host]:
                return self.reply(403, b'Host rejected', head=head)
            origins = self.headers.get_all('Origin', [])
            if (origins and origins != ['http://' + host]) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.reply(403, b'Origin rejected', head=head)
            if not self.path.startswith(prefix):
                return self.reply(404, b'Not found', head=head)
            name = self.path[len(prefix):] or 'START_HERE.html'
            # Exact matching only: no URL unquoting, normalization or filesystem traversal.
            if name not in snapshot:
                return self.reply(404, b'Not found', head=head)
            data, mime = snapshot[name]
            return self.reply(200, data, mime, head=head, attachment=name.endswith('.zip'))

        def do_GET(self):
            self.serve()

        def do_HEAD(self):
            self.serve(head=True)

        def do_POST(self):
            self.reply(405, b'Read-only: uploads and writes are not supported')

        do_PUT = do_POST
        do_DELETE = do_POST
        do_PATCH = do_POST
        do_OPTIONS = do_POST

    server = LocalServer(('127.0.0.1', 0), Handler)
    return server, f'http://127.0.0.1:{server.server_port}{prefix}'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--no-open', action='store_true', help='Print the local URL without opening a browser')
    args = parser.parse_args(argv)
    try:
        server, url = create_server(args.root)
    except (OSError, ValueError) as exc:
        print('LOCAL WORKBENCH NOT STARTED: ' + str(exc), file=sys.stderr)
        return 2
    with server:
        print(json.dumps({'status': 'serving', 'url': url, 'read_only': True,
                          'writes_projects': False, 'autosave': False}, ensure_ascii=False), flush=True)
        print('仅本机只读入口。请在退出前下载收工包；Ctrl+C 停止服务。', flush=True)
        if not args.no_open:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                print('浏览器未自动打开，请复制上方本机地址。', flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            print('本机服务已停止；没有自动保存浏览器内容。', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
