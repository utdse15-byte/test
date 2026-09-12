"""Reproducible small-ZIP delivery, with checksums and no executable payload.

Generated fragment ZIPs are ordinary, single-file ZIP_STORED archives, not film
project interchange bundles. Reassembly always creates a new output file.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import struct
import zipfile


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def split_archive(source: Path, target_dir: Path, *, block_size=10 * 1024 * 1024) -> dict:
    if not 1024 <= block_size <= 16 * 1024 * 1024:
        raise ValueError('Fragment size outside the delivery range')
    if not source.is_file() or source.is_symlink() or source.stat().st_size == 0:
        raise ValueError('A nonempty regular archive is required')
    target_dir.mkdir(exist_ok=False, parents=True)
    manifest = {'schema': 'manju.download-parts/v1', 'name': source.name,
                'bytes': source.stat().st_size, 'sha256': digest(source), 'parts': []}
    with source.open('rb') as stream:
        index = 0
        while data := stream.read(block_size):
            index += 1
            name = f'MANJU_PART_{index:02d}.zip'
            payload_name = f'MANJU_PAYLOAD_{index:02d}.part'
            path = target_dir / name
            entry = zipfile.ZipInfo(payload_name, date_time=(2026, 9, 12, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_STORED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            with zipfile.ZipFile(path, 'x', compression=zipfile.ZIP_STORED) as z:
                z.writestr(entry, data)
            # A fixed checked slice is consumed in the browser only after the
            # ENTIRE ZIP matches its expected digest. No generic ZIP parser.
            with path.open('rb') as handle:
                header = handle.read(30)
            name_length, extra_length = struct.unpack('<HH', header[26:30])
            offset = 30 + name_length + extra_length
            manifest['parts'].append({'index': index, 'name': name, 'bytes': path.stat().st_size,
                'sha256': digest(path), 'payload_name': payload_name, 'payload_offset': offset,
                'payload_bytes': len(data), 'payload_sha256': hashlib.sha256(data).hexdigest()})
    (target_dir / 'PARTS.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def reassemble(paths: list[Path], output: Path, manifest: dict) -> dict:
    if output.exists() or output.is_symlink():
        raise FileExistsError('Output exists and will not be overwritten')
    expected = {p['sha256']: p for p in manifest['parts']}
    if len(paths) != len(expected):
        raise ValueError('Select exactly one of each fragment')
    verified = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Not a regular fragment file')
        size = path.stat().st_size
        if size not in {p['bytes'] for p in expected.values()}:
            raise ValueError('Incomplete or wrong-sized fragment')
        info = expected.get(digest(path))
        if info is None or info['bytes'] != size:
            raise ValueError('Fragment does not match this release')
        if info['index'] in verified:
            raise ValueError('Duplicate fragment')
        with zipfile.ZipFile(path) as z:
            if z.namelist() != [info['payload_name']] or z.testzip() is not None:
                raise ValueError('Invalid fragment ZIP')
            data = z.read(info['payload_name'])
        if len(data) != info['payload_bytes'] or hashlib.sha256(data).hexdigest() != info['payload_sha256']:
            raise ValueError('Fragment payload mismatch')
        verified[info['index']] = data
    total = hashlib.sha256()
    count = 0
    for index in sorted(verified):
        total.update(verified[index]); count += len(verified[index])
    if total.hexdigest() != manifest['sha256'] or count != manifest['bytes']:
        raise ValueError('Combined archive mismatch')
    owned = False
    try:
        with output.open('xb') as stream:
            owned = True
            for index in sorted(verified):
                stream.write(verified[index])
        if digest(output) != manifest['sha256']:
            raise ValueError('Saved archive mismatch')
    except BaseException:
        if owned:
            output.unlink(missing_ok=True)
        raise
    return {'ok': True, 'bytes': count, 'sha256': manifest['sha256'],
            'parts': len(verified), 'original_files_changed': False}
