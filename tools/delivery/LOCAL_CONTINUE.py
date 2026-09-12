"""Optional single-user loopback recovery, not a public server or independent backup.

Only explicit browser actions write below the selected ManjuRecovery directory.
Each point is an ordinary Desk/v1 ZIP. No project directory, arbitrary file route,
telemetry, account, executable upload, cloud or dependency installation is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
import sys
import tempfile
import threading
import time
import webbrowser
import zipfile

MAX_POINT = 520 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_POINTS = 64
KEEP_PER_WINDOW = 3
HEX = re.compile(r'[a-f0-9]{64}\Z')
ID = re.compile(r'[a-f0-9]{32}\Z')
MARKER = b'Manju local recovery v1\n'
CLIENT = 'CONTINUE_CLIENT.js'
FILES = 'CONTINUE_FILES.json'


class RecoveryError(ValueError):
    """Safe public error. Never interpolate an absolute path or user content."""


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError('JSON contains duplicate keys')
        result[key] = value
    return result


def read_json(data):
    if len(data) > 2 * 1024 * 1024:
        raise RecoveryError('Metadata too large')
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(RecoveryError('Nonfinite number')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RecoveryError('Invalid metadata') from exc


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path, *, directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise RecoveryError('Linked or nonregular recovery path refused')
    return path


def ancestors(path):
    for part in reversed((path, *path.parents)):
        if part.exists() or part.is_symlink():
            plain(part, directory=True)


def sync_directory(path):
    # fsync directory is unavailable on Windows. File bytes are flushed there;
    # do not claim filesystem/power-loss guarantees beyond the host platform.
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_new(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def validate_envelope(path):
    """Integrity of the stored envelope, NOT video decoding/creative approval.

    The original workbench independently performs the full semantic and media
    validation before restoring. This writer never extracts archive members.
    """
    try:
        with zipfile.ZipFile(path) as z:
            items = z.infolist()
            if (len(items) != 3 or {i.filename for i in items} != {'DESK.json', 'STUDIO.zip', 'MANIFEST.json'}
                    or z.comment or any(i.compress_type != zipfile.ZIP_STORED or i.flag_bits & ~0x800
                    or i.extra or i.comment or i.is_dir() or stat.S_ISLNK(i.external_attr >> 16)
                    for i in items)):
                raise RecoveryError('Not an ordinary closed Desk ZIP')
            for i in items:
                limit = MAX_POINT if i.filename == 'STUDIO.zip' else 2 * 1024 * 1024
                if not 0 < i.file_size <= limit:
                    raise RecoveryError('ZIP entry size refused')
            if path.stat().st_size > MAX_POINT:
                raise RecoveryError('Recovery ZIP too large')
            # Bound metadata before reading. ZipFile verifies each CRC as read.
            desk = read_json(z.read('DESK.json'))
            manifest = read_json(z.read('MANIFEST.json'))
            if (not isinstance(desk, dict) or desk.get('schema_id') != 'manju.desk-session/v1'
                    or any(desk.get(k) is not False for k in ('automatic_execution', 'project_modified', 'confirmations_restored'))
                    or not isinstance(manifest, dict) or set(manifest) != {'schema_id', 'files'}
                    or manifest['schema_id'] != 'manju.desk-manifest/v1'
                    or not isinstance(manifest['files'], dict)
                    or set(manifest['files']) != {'DESK.json', 'STUDIO.zip'}):
                raise RecoveryError('Desk identity or manual approval boundary refused')
            for name in ('DESK.json', 'STUDIO.zip'):
                with z.open(name) as stream:
                    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
                if actual != manifest['files'][name]:
                    raise RecoveryError('Envelope file hash mismatch')
                if name == 'STUDIO.zip' and actual != desk.get('studio_archive_sha256'):
                    raise RecoveryError('Studio identity mismatch')
            # Reject false-success files carrying a fake inner payload. Exact
            # inner semantics are deliberately owned by the existing reader.
            with z.open('STUDIO.zip') as stream:
                if stream.read(4) != b'PK\x03\x04':
                    raise RecoveryError('Inner Studio is not a ZIP')
            first = min(i.header_offset for i in items)
            with path.open('rb') as stream:
                stream.seek(-22, os.SEEK_END)
                end = stream.read(22)
            if first != 0 or end[:4] != b'PK\x05\x06' or end[-2:] != b'\0\0':
                raise RecoveryError('ZIP prefix or trailing data refused')
    except (OSError, zipfile.BadZipFile, KeyError, TypeError, EOFError) as exc:
        raise RecoveryError('Invalid or unreadable recovery ZIP') from exc


def checkpoint_summary(stream):
    """Read small descriptive metadata from an already SHA-verified Desk stream.

    This is a read-only index, not a restore validator or a video decoder. No
    files are extracted and no media payload is decoded or returned. The old
    POINT.json and Desk/Studio formats remain unchanged. Nested ZIP metadata is
    bounded before json parsing; text is shortened only in this display result.
    """
    def document(archive, name):
        items = [i for i in archive.infolist() if i.filename == name]
        if len(items) != 1:
            raise RecoveryError('Summary metadata missing or duplicated')
        item = items[0]
        if (not 0 < item.file_size <= 2 * 1024 * 1024 or item.is_dir()
                or item.compress_type != zipfile.ZIP_STORED or item.flag_bits & ~0x800
                or item.extra or item.comment or stat.S_ISLNK(item.external_attr >> 16)):
            raise RecoveryError('Summary metadata shape refused')
        value = read_json(archive.read(item))
        if not isinstance(value, dict):
            raise RecoveryError('Summary metadata must be an object')
        return value

    def obj(value):
        if not isinstance(value, dict):
            raise RecoveryError('Summary field is not an object')
        return value

    def text(value, limit=240):
        if not isinstance(value, str):
            raise RecoveryError('Summary text field refused')
        # Never change stored drafts; these are display-only snippets.
        return value[:limit]

    def array(value):
        if not isinstance(value, list) or len(value) > 4096:
            raise RecoveryError('Summary list field refused')
        return value

    try:
        with zipfile.ZipFile(stream) as outer:
            if len(outer.infolist()) != 3 or set(outer.namelist()) != {'DESK.json', 'STUDIO.zip', 'MANIFEST.json'}:
                raise RecoveryError('Summary requires a closed Desk ZIP')
            desk = document(outer, 'DESK.json')
            if (desk.get('schema_id') != 'manju.desk-session/v1'
                    or any(desk.get(k) is not False for k in ('automatic_execution', 'project_modified', 'confirmations_restored'))):
                raise RecoveryError('Summary Desk identity refused')
            inner_info = outer.getinfo('STUDIO.zip')
            if (inner_info.compress_type != zipfile.ZIP_STORED or not 0 < inner_info.file_size <= MAX_POINT
                    or inner_info.flag_bits & ~0x800 or stat.S_ISLNK(inner_info.external_attr >> 16)):
                raise RecoveryError('Summary Studio shape refused')
            # seekable ZipExtFile avoids loading a large inner media ZIP in RAM.
            with outer.open(inner_info) as inner_stream:
                inner_stream.seek(-22, os.SEEK_END)
                end = inner_stream.read(22)
                if len(end) != 22:
                    raise RecoveryError('Summary Studio trailer refused')
                signature, disk, central_disk, local_count, count, size, offset, comment = struct.unpack('<4s4H2IH', end)
                if (signature != b'PK\x05\x06' or disk or central_disk or comment
                        or local_count != count or not 1 <= count <= 4098
                        or size > 2 * 1024 * 1024 or offset + size != inner_info.file_size - 22):
                    raise RecoveryError('Summary Studio directory exceeds bounds')
                inner_stream.seek(0)
                with zipfile.ZipFile(inner_stream) as inner:
                    studio = document(inner, 'STUDIO.json')
        if (studio.get('schema_id') != 'manju.studio-session/v1'
                or any(studio.get(k) is not False for k in ('automatic_execution', 'project_modified', 'confirmations_restored'))):
            raise RecoveryError('Summary Studio identity refused')
        workspace = obj(studio.get('workspace'))
        form = obj(obj(workspace.get('draft')).get('form'))
        review = workspace.get('review')
        candidates = array(obj(obj(review).get('session')).get('candidates')) if review is not None else []
        decisions = array(obj(review).get('decisions')) if review is not None else []
        director, repair = obj(studio.get('director')), obj(studio.get('repair'))
        media = array(studio.get('media'))
        media_bytes = 0
        for entry in media:
            size = obj(entry).get('bytes')
            if type(size) is not int or not 0 < size <= 128 * 1024 * 1024:
                raise RecoveryError('Summary media size refused')
            media_bytes += size
        if media_bytes > 512 * 1024 * 1024:
            raise RecoveryError('Summary media total refused')
        scratch = obj(desk.get('scratch_form'))
        template = desk.get('personal_template')
        return {'schema_id': 'manju.recovery-summary/v1',
                'shot_id': text(form.get('shot-id', ''), 160),
                'prompt': text(form.get('prompt', '')),
                'task': text(form.get('task', ''), 64),
                'branch_name': text(scratch.get('flex-branch-name', ''), 160),
                'media_files': len(media), 'media_bytes': media_bytes,
                'candidates': len(candidates), 'review_records': len(decisions),
                'has_repair_video': repair.get('source') is not None,
                'repair_note': text(obj(repair.get('form')).get('repair-change', '')),
                'has_director_video': director.get('source') is not None,
                'director_anchors': len(array(director.get('anchors'))),
                'director_note': text(obj(director.get('form')).get('director-change', '')),
                'pending_external_edit': desk.get('external_edit') is not None,
                'pending_template': template is not None,
                'template_name': text(obj(template).get('name', ''), 160) if template is not None else '',
                'video_decode_verified': False, 'restored': False, 'independent_backup': False}
    except (zipfile.BadZipFile, KeyError, TypeError, ValueError, EOFError, OSError) as exc:
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError('Recovery summary unavailable; original point retained') from exc


class RecoveryStore:
    def __init__(self, root: Path, *, maximum=MAX_TOTAL, count=MAX_POINTS, keep=KEEP_PER_WINDOW):
        self.root = root.absolute()
        self.maximum, self.count, self.keep = maximum, count, keep
        self.lock = threading.RLock()

    @contextmanager
    def locked(self, *, create=False):
        """Thread and cross-process transaction lock; no directory on read-only first use."""
        with self.lock:
            ancestors(self.root)
            if not self.root.exists():
                if not create:
                    yield False
                    return
                self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
                write_new(self.root / 'IDENTITY', MARKER)
                write_new(self.root / 'LOCK', b'\0')
            marker = plain(self.root / 'IDENTITY')
            if marker.stat().st_size != len(MARKER) or marker.read_bytes() != MARKER:
                raise RecoveryError('Not a Manju recovery directory; no files changed')
            lock_path = plain(self.root / 'LOCK')
            with lock_path.open('r+b') as handle:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise RecoveryError('Another local process is using recovery; retry') from exc
                try:
                    yield True
                finally:
                    if os.name == 'nt':
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle, fcntl.LOCK_UN)

    def _record(self, path):
        plain(path, directory=True)
        if {p.name for p in path.iterdir()} != {'POINT.json', 'DESK.zip'}:
            raise RecoveryError('Recovery point contains unexpected files')
        metadata = plain(path / 'POINT.json')
        if metadata.stat().st_size > 8192:
            raise RecoveryError('Point metadata too large')
        record = read_json(metadata.read_bytes())
        expected = {'id', 'window', 'sequence', 'sha256', 'bytes', 'created_utc'}
        if (not isinstance(record, dict) or set(record) != expected or record['id'] != path.name
                or not ID.fullmatch(str(record['window'])) or not HEX.fullmatch(str(record['sha256']))
                or type(record['sequence']) is not int or not 1 <= record['sequence'] <= 1_000_000_000
                or type(record['bytes']) is not int or not 0 < record['bytes'] <= MAX_POINT
                or not isinstance(record['created_utc'], str) or len(record['created_utc']) > 40):
            raise RecoveryError('Point metadata refused')
        plain(path / 'DESK.zip')
        return record

    def _records(self):
        records, damaged, pending, used = [], [], [], 0
        for path in self.root.iterdir():
            if path.name in {'LOCK', 'IDENTITY'}:
                continue
            plain(path, directory=True)
            # Only our exact shallow file shapes may be counted or removed.
            if not (ID.fullmatch(path.name) or re.fullmatch(r'pending-[a-f0-9]{32}', path.name)):
                raise RecoveryError('Unexpected recovery content; automatic writes paused')
            for f in path.iterdir():
                plain(f)
                if f.name not in {'DESK.zip', 'POINT.json'}:
                    raise RecoveryError('Unexpected recovery file; writes paused')
                used += f.stat().st_size
            if path.name.startswith('pending-'):
                pending.append(path.name)
            else:
                try:
                    records.append(self._record(path))
                except RecoveryError:
                    damaged.append(path.name)
        records.sort(key=lambda r: (r['created_utc'], r['id']), reverse=True)
        return records, damaged, pending, used

    def list(self):
        with self.locked() as ready:
            records, damaged, pending, used = self._records() if ready else ([], [], [], 0)
            return {'points': records, 'damaged': damaged, 'unfinished': len(pending),
                    'bytes_used': used, 'maximum_bytes': self.maximum, 'maximum_points': self.count,
                    'keep_per_window': self.keep, 'independent_backup': False}

    def save(self, stream, length, window, sequence, expected_hash):
        if (type(length) is not int or not 0 < length <= MAX_POINT or not ID.fullmatch(window)
                or not HEX.fullmatch(expected_hash) or type(sequence) is not int
                or not 1 <= sequence <= 1_000_000_000):
            raise RecoveryError('Upload identity or size refused')
        with self.locked(create=True):
            records, damaged, pending, used = self._records()
            # Sequence, not wall-clock time, determines this window's newest points.
            own = sorted((r for r in records if r['window'] == window),
                         key=lambda r: r['sequence'], reverse=True)
            if own and sequence <= max(r['sequence'] for r in own):
                raise RecoveryError('Stale save sequence refused; newer point kept')
            # Reserve before reading. Do not remove an old point to make room.
            if used + length + 8192 > self.maximum or len(records) + len(damaged) >= self.count:
                raise RecoveryError('Recovery capacity reached; download/remove points first')
            if shutil.disk_usage(self.root).free < length + 16 * 1024 * 1024:
                raise RecoveryError('Not enough free disk space; old points kept')
            temporary = self.root / ('pending-' + secrets.token_hex(16))
            temporary.mkdir(mode=0o700)
            committed = False
            try:
                destination = temporary / 'DESK.zip'
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                value, remaining = hashlib.sha256(), length
                with os.fdopen(fd, 'wb') as output:
                    while remaining:
                        block = stream.read(min(1024 * 1024, remaining))
                        if not block:
                            raise RecoveryError('Interrupted upload; old points kept')
                        if len(block) > remaining:
                            raise RecoveryError('Upload exceeds declared size')
                        remaining -= len(block)
                        output.write(block)
                        value.update(block)
                    output.flush()
                    os.fsync(output.fileno())
                if value.hexdigest() != expected_hash:
                    raise RecoveryError('Upload checksum mismatch; old points kept')
                validate_envelope(destination)
                if digest(destination) != expected_hash:
                    raise RecoveryError('Written bytes did not verify; old points kept')
                point_id = secrets.token_hex(16)
                record = {'id': point_id, 'window': window, 'sequence': sequence,
                          'sha256': expected_hash, 'bytes': length,
                          'created_utc': datetime.now(timezone.utc).isoformat(timespec='microseconds')}
                write_new(temporary / 'POINT.json', json.dumps(record, ensure_ascii=True).encode('utf-8'))
                sync_directory(temporary)
                final = self.root / point_id
                if final.exists():
                    raise RecoveryError('Point identity collision; no overwrite')
                os.rename(temporary, final)
                committed = True
                sync_directory(self.root)
                # Publication precedes pruning; this window cannot prune another.
                pruned, warnings = [], []
                for old in own[max(0, self.keep - 1):]:
                    try:
                        self._remove(old['id'], old['sha256'])
                        pruned.append(old['id'])
                    except (RecoveryError, OSError):
                        warnings.append('An older point was retained; cleanup failed')
                return {'ok': True, 'point': record, 'pruned': pruned, 'warnings': warnings,
                        'independent_backup': False, 'media_decode_verified': False}
            finally:
                if not committed:
                    shutil.rmtree(temporary, ignore_errors=True)

    def load(self, point_id):
        if not ID.fullmatch(point_id):
            raise RecoveryError('Invalid point identity')
        with self.locked() as ready:
            if not ready:
                raise RecoveryError('Recovery point not found')
            path = self.root / point_id
            record = self._record(path)
            payload = path / 'DESK.zip'
            # Open and verify under the lock. The caller owns this descriptor;
            # an in-flight download is protected from later deletion on POSIX.
            stream = payload.open('rb')
            try:
                if os.fstat(stream.fileno()).st_size != record['bytes'] or hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                    raise RecoveryError('Recovery bytes damaged; current browser work unchanged')
                stream.seek(0)
                return record, stream
            except BaseException:
                stream.close()
                raise

    def inspect(self, point_id, expected_hash):
        if not isinstance(expected_hash, str) or not HEX.fullmatch(expected_hash):
            raise RecoveryError('Summary checksum required')
        record, stream = self.load(point_id)
        with stream:
            if record['sha256'] != expected_hash:
                raise RecoveryError('Summary target changed; refresh the list')
            summary = checkpoint_summary(stream)
        return {'ok': True, 'point': record, 'summary': summary}

    def _remove(self, point_id, expected):
        record = self._record(self.root / point_id)
        if record['sha256'] != expected:
            raise RecoveryError('Delete target changed')
        path = self.root / point_id
        # Exactly two regular files verified by _record. No recursive symlinks.
        (path / 'DESK.zip').unlink()
        (path / 'POINT.json').unlink()
        path.rmdir()

    def remove(self, point_id, expected):
        if not ID.fullmatch(point_id) or not HEX.fullmatch(expected):
            raise RecoveryError('Invalid deletion identity')
        with self.locked() as ready:
            if not ready:
                raise RecoveryError('Recovery point not found')
            self._remove(point_id, expected)
            sync_directory(self.root)
        return {'ok': True, 'removed': point_id, 'browser_work_changed': False}

    def clean_unfinished(self):
        with self.locked() as ready:
            if not ready:
                return {'ok': True, 'removed': 0}
            _, _, pending, _ = self._records()
            for name in pending:
                path = self.root / name
                for file in path.iterdir():
                    plain(file).unlink()
                path.rmdir()
            sync_directory(self.root)
            return {'ok': True, 'removed': len(pending)}


def load_page(root):
    # Import the existing readonly loader without depending on installed Manju.
    spec = importlib.util.spec_from_file_location('manju_local_static', Path(__file__).with_name('LOCAL_WORKBENCH.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    snapshot = module.load_snapshot(root)
    path = module.regular_file(root, FILES)
    if path.stat().st_size > 8192:
        raise RecoveryError('Continuation inventory too large')
    expected = read_json(path.read_bytes())
    if not isinstance(expected, dict) or set(expected) != {CLIENT}:
        raise RecoveryError('Continuation inventory invalid')
    source = module.regular_file(root, CLIENT)
    with source.open('rb') as stream:
        script = stream.read(1024 * 1024 + 1)
    if len(script) > 1024 * 1024 or hashlib.sha256(script).hexdigest() != expected[CLIENT]:
        raise RecoveryError('Continuation client changed or incomplete')
    page = snapshot['APP/OPEN_MODEL_WORKBENCH.html'][0].decode('utf-8')
    if page.count("connect-src 'none'") != 1 or page.count('</body>') != 1 or 'restoreSourceVersion:1' not in page:
        raise RecoveryError('Workbench embedding contract changed')
    return page.replace("connect-src 'none'", "connect-src 'self'").replace(
        '</body>', '<script>' + script.decode('utf-8') + '</script></body>')


class RecoveryServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False
    request_queue_size = 8

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(15)
        return connection, address


def create_server(root, recovery_root):
    page = load_page(root.absolute())
    store = RecoveryStore(recovery_root)
    prefix = '/' + secrets.token_urlsafe(24) + '/'
    csrf = secrets.token_hex(32)
    config = json.dumps({'base': prefix, 'token': csrf, 'auto_delay_ms': 8000, 'minimum_gap_ms': 30000})
    page = page.replace('<script>\n/* MANJU_CONTINUE_CLIENT */', '<script>window.MANJU_CONTINUE_CONFIG=' + config + ';\n/* MANJU_CONTINUE_CLIENT */')
    if 'window.MANJU_CONTINUE_CONFIG=' not in page:
        raise RecoveryError('Missing client injection marker')
    payload = page.encode('utf-8')
    permits = threading.BoundedSemaphore(8)

    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = 'ManjuContinue', ''

        def log_message(self, *_):
            pass

        def headers_out(self, code, size, mime, attachment=None):
            self.send_response(code)
            for k, v in {'Content-Type': mime, 'Content-Length': str(size), 'Cache-Control': 'no-store',
                'Connection': 'close', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                'Cross-Origin-Resource-Policy': 'same-origin',
                'Content-Security-Policy': "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src blob: data:; media-src blob: data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}.items():
                self.send_header(k, v)
            if attachment:
                self.send_header('Content-Disposition', 'attachment; filename="' + attachment + '"')
            self.end_headers()
            self.close_connection = True

        def respond(self, code, data, mime='application/json; charset=utf-8'):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.headers_out(code, len(data), mime)
            if self.command != 'HEAD':
                self.wfile.write(data)

        def authorized(self, *, write=False):
            host = '127.0.0.1:' + str(self.server.server_port)
            origin = 'http://' + host
            origins = self.headers.get_all('Origin', [])
            if (self.headers.get_all('Host', []) != [host] or (origins and origins != [origin])
                    or self.headers.get('Sec-Fetch-Site') == 'cross-site'
                    or not self.path.startswith(prefix)):
                return False
            if self.path != prefix and self.headers.get_all('X-Manju-Token', []) != [csrf]:
                return False
            return not write or origins == [origin]

        def dispatch(self):
            writing = self.command in {'POST', 'DELETE'}
            if not self.authorized(write=writing):
                return self.respond(403, {'error': 'Local origin or token rejected'})
            name = self.path[len(prefix):]
            if self.command in {'GET', 'HEAD'}:
                if not name:
                    return self.respond(200, payload, 'text/html; charset=utf-8')
                if name == 'points':
                    return self.respond(200, store.list())
                if name.startswith('points/') and name.endswith('/summary') and ID.fullmatch(name[7:-8]):
                    hashes = self.headers.get_all('X-Manju-Sha256', [])
                    if len(hashes) != 1:
                        raise RecoveryError('One summary checksum is required')
                    return self.respond(200, store.inspect(name[7:-8], hashes[0]))
                if name.startswith('points/') and ID.fullmatch(name[7:]):
                    record, stream = store.load(name[7:])
                    try:
                        self.send_response(200)
                        self.send_header('X-Manju-Sha256', record['sha256'])
                        self.send_header('Content-Type', 'application/zip')
                        self.send_header('Content-Length', str(record['bytes']))
                        self.send_header('Content-Disposition', 'attachment; filename="MANJU_DESK_' + record['id'] + '.zip"')
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        if self.command != 'HEAD':
                            shutil.copyfileobj(stream, self.wfile, 1024 * 1024)
                    finally:
                        stream.close()
                    return
            elif self.command == 'POST':
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or not re.fullmatch(r'0|[1-9][0-9]{0,9}', lengths[0]) or self.headers.get_all('Transfer-Encoding', []):
                    return self.respond(400, {'error': 'A single bounded Content-Length is required'})
                length = int(lengths[0])
                for header in ('X-Manju-Sequence','X-Manju-Window','X-Manju-Sha256'):
                    if len(self.headers.get_all(header, [])) > 1:
                        raise RecoveryError('Duplicate upload identity header')
                if name == 'cleanup' and length == 0:
                    return self.respond(200, store.clean_unfinished())
                if name == 'points':
                    sequence = self.headers.get('X-Manju-Sequence', '')
                    if not sequence.isascii() or not sequence.isdigit() or len(sequence) > 10:
                        raise RecoveryError('Invalid save sequence')
                    if self.headers.get_all('Content-Type', []) != ['application/zip']:
                        raise RecoveryError('Recovery upload must be a ZIP')
                    result = store.save(self.rfile, length, self.headers.get('X-Manju-Window', ''),
                        int(sequence), self.headers.get('X-Manju-Sha256', ''))
                    return self.respond(201, result)
            elif self.command == 'DELETE' and name.startswith('points/'):
                if self.headers.get_all('Transfer-Encoding', []) or self.headers.get('Content-Length', '0') != '0':
                    raise RecoveryError('Delete must not carry a payload')
                return self.respond(200, store.remove(name[7:], self.headers.get('X-Manju-Sha256', '')))
            return self.respond(404, {'error': 'Unsupported local route'})

        def handle_request(self):
            if not permits.acquire(blocking=False):
                return self.respond(503, {'error': 'Local service busy; retry'})
            try:
                self.dispatch()
            except (RecoveryError, FileNotFoundError) as exc:
                message = str(exc) if isinstance(exc, RecoveryError) else 'Recovery file missing; no browser work changed'
                self.respond(409, {'error': message})
            except (OSError, TimeoutError, zipfile.BadZipFile):
                try:
                    self.respond(503, {'error': 'Local read/write interrupted; previous points retained'})
                except OSError:
                    pass
            finally:
                permits.release()

        do_GET = handle_request
        do_HEAD = handle_request
        do_POST = handle_request
        do_DELETE = handle_request

        def do_OPTIONS(self):
            self.respond(405, {'error': 'No cross-origin access'})

        do_PUT = do_OPTIONS
        do_PATCH = do_OPTIONS

    server = RecoveryServer(('127.0.0.1', 0), Handler)
    server.store = store
    server.csrf = csrf
    return server, f'http://127.0.0.1:{server.server_port}{prefix}'


def main(argv=None):
    if sys.version_info < (3, 11):
        print('This optional mode needs Python 3.11+. Open START_HERE.html for ordinary offline use.', file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).absolute().parent)
    parser.add_argument('--recovery-dir', type=Path, default=Path.home() / 'ManjuRecovery')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args(argv)
    try:
        server, url = create_server(args.root, args.recovery_dir)
    except (ValueError, OSError) as exc:
        print('Unable to start. Extract a fresh package and verify it. ' + str(exc), file=sys.stderr)
        return 2
    print('Manju local continuation. No cloud or automatic generation.\n' + url, flush=True)
    print('Recovery directory: ' + str(args.recovery_dir.absolute()), flush=True)
    print('Saving starts only after a browser action. Keep this console open. Ctrl+C stops service.', flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
