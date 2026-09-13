"""Portable end-of-day envelope, including unapplied external text and templates.

The inner STUDIO.zip is byte-for-byte a Studio/v1 backup. No old format changes,
no remote IO, no restore of consent. Verification is not a video decoder, a
signature, or evidence that the user saved the file on their own computer.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil
import stat
import struct
import tempfile
from typing import Literal
import zipfile

from pydantic import BaseModel, ConfigDict, model_validator

from .core import AuthoringError, Digest, canonical, digest, file_digest
from .exchange import ExternalEdit
from .flexibility import CreativeTemplate
from .studio import verify_studio, _file_identity
from .workspace import MAX_JSON, MAX_TOTAL, json_value
from .story import Story

MAX_STUDIO = MAX_TOTAL + 4 * 1024 * 1024
MAX_ARCHIVE = MAX_TOTAL + 8 * 1024 * 1024
SCRATCH_FORM = {'flex-template-name', 'flex-template-notes', 'flex-branch-name'}
NAMES = {'DESK.json', 'STUDIO.zip', 'MANIFEST.json'}


class Desk(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_id: Literal['manju.desk-session/v1']
    studio_archive_sha256: Digest
    external_edit: ExternalEdit | None
    personal_template: CreativeTemplate | None
    scratch_form: dict[str, str]
    confirmations_restored: Literal[False]
    automatic_execution: Literal[False]
    project_modified: Literal[False]

    @model_validator(mode='wrap')
    @classmethod
    def exact(cls, value, handler):
        result = handler(value)
        if isinstance(value, dict) and canonical(value) != canonical(result):
            raise ValueError('desk fields must be exact, without implicit conversion')
        return result

    @model_validator(mode='after')
    def limits(self):
        if set(self.scratch_form) != SCRATCH_FORM:
            raise ValueError('incomplete desk scratch form')
        if any(len(v.encode('utf-16-le')) // 2 > 30000 for v in self.scratch_form.values()):
            raise ValueError('desk scratch form exceeds local limits')
        if len(canonical(self)) > MAX_JSON:
            raise ValueError('desk metadata exceeds 2 MiB; save external drafts separately')
        return self


class StoryDesk(Desk):
    """Versioned optional extension; v1 inputs/output remain byte-compatible."""
    schema_id: Literal['manju.desk-session/v2']
    story: Story


def parse_desk(value: dict) -> Desk | StoryDesk:
    model = StoryDesk if isinstance(value, dict) and value.get('schema_id') == 'manju.desk-session/v2' else Desk
    return model.model_validate(value)


def _directory(path: Path, archive: zipfile.ZipFile) -> None:
    """Check both ZIP directories without loading the nested media archive."""
    infos = archive.infolist()
    if len(infos) != 3 or archive.comment:
        raise AuthoringError('desk requires exactly three uncommented stored members')
    size = path.stat().st_size
    with path.open('rb') as stream:
        stream.seek(size - 22)
        sig, disk, cd_disk, n1, n2, cd_size, cd_start, comment = struct.unpack('<4s4H2LH', stream.read(22))
        if (sig != b'PK\x05\x06' or disk or cd_disk or comment or n1 != 3 or n2 != 3 or
                cd_size > 3 * 286 or cd_start + cd_size != size - 22 or cd_start != archive.start_dir):
            raise AuthoringError('desk ZIP directory is not closed')
        cursor, central, names, total = 0, cd_start, set(), 0
        for info in infos:
            name = info.filename
            if (name not in NAMES or name in names or info.is_dir() or info.extra or info.comment or
                    info.compress_type != 0 or info.flag_bits & ~0x800 or
                    info.compress_size != info.file_size or info.header_offset != cursor or
                    stat.S_ISLNK(info.external_attr >> 16)):
                raise AuthoringError('unsafe, duplicate, compressed or unapproved desk member')
            limit = MAX_STUDIO if name == 'STUDIO.zip' else MAX_JSON
            if not 0 < info.file_size <= limit:
                raise AuthoringError('desk member exceeds bounded size')
            stream.seek(cursor)
            sig, _, flags, method, _, _, crc, packed, unpacked, n, extra = struct.unpack('<4s5H3L2H', stream.read(30))
            if not 0 < n <= 240:
                raise AuthoringError('desk member name length invalid')
            if (sig != b'PK\x03\x04' or flags != info.flag_bits or method or extra or crc != info.CRC or
                    packed != info.file_size or unpacked != packed or stream.read(n).decode('utf-8') != name):
                raise AuthoringError('desk local record disagrees with directory')
            stream.seek(central)
            raw = stream.read(46)
            if len(raw) != 46 or raw[:4] != b'PK\x01\x02':
                raise AuthoringError('invalid desk central record')
            n2, extra2, comment2, disk2 = struct.unpack_from('<4H', raw, 28)
            if n2 != n or extra2 or comment2 or disk2 or stream.read(n2).decode('utf-8') != name:
                raise AuthoringError('unsupported desk central fields')
            cursor += 30 + n + packed
            central += 46 + n
            total += unpacked
            names.add(name)
        if names != NAMES or cursor != cd_start or central != size - 22 or total > MAX_ARCHIVE:
            raise AuthoringError('desk ZIP has gaps, suffixes or unclosed entries')


def verify_desk(path: Path) -> dict:
    """Read only. Stream and validate the original inner archive in private scratch."""
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 22 <= path.stat().st_size <= MAX_ARCHIVE:
        raise AuthoringError('desk archive missing, linked or oversized')
    before = _file_identity(path)
    try:
        with zipfile.ZipFile(path) as archive:
            _directory(path, archive)
            document = parse_desk(json_value(archive.read('DESK.json')))
            manifest = json_value(archive.read('MANIFEST.json'))
            if (not isinstance(manifest, dict) or set(manifest) != {'schema_id', 'files'} or
                    manifest['schema_id'] != 'manju.desk-manifest/v1' or
                    not isinstance(manifest['files'], dict) or set(manifest['files']) != {'DESK.json', 'STUDIO.zip'}):
                raise AuthoringError('invalid desk inventory')
            for name in ('DESK.json', 'STUDIO.zip'):
                h = sha256()
                with archive.open(name) as source:
                    while block := source.read(1024 * 1024):
                        h.update(block)
                if h.hexdigest() != manifest['files'][name]:
                    raise AuthoringError('desk content hash mismatch: ' + name)
            if manifest['files']['STUDIO.zip'] != document.studio_archive_sha256:
                raise AuthoringError('desk/inner studio identity mismatch')
            with tempfile.TemporaryDirectory(prefix='manju-desk-verify-') as td:
                inner = Path(td) / 'STUDIO.zip'
                with archive.open('STUDIO.zip') as source, inner.open('xb') as destination:
                    shutil.copyfileobj(source, destination, 1024 * 1024)
                studio = verify_studio(inner)
                if studio['archive_sha256'] != document.studio_archive_sha256:
                    raise AuthoringError('verified inner studio identity changed')
            archive_hash = file_digest(path)
            if path.is_symlink() or _file_identity(path) != before:
                raise AuthoringError('desk changed during verification; use a stable file')
            return {'ok': True, 'schema_id': document.schema_id, 'desk_sha256': digest(document),
                    'archive_sha256': archive_hash, 'studio_archive_sha256': document.studio_archive_sha256,
                    'studio': studio, 'external_edit_present': document.external_edit is not None,
                    'external_edit_fields': len(document.external_edit.values) if document.external_edit else 0,
                    'personal_template_present': document.personal_template is not None,
                    'confirmations_restored': False, 'automatic_execution': False,
                    'project_modified': False, 'video_decode_verified': False,
                    'client_download_confirmed': False}
    except (zipfile.BadZipFile, KeyError, UnicodeError, struct.error, RecursionError) as exc:
        raise AuthoringError(f'invalid desk archive: {exc}') from exc


def _publish_new(source: Path, output: Path) -> None:
    owned = False
    try:
        with output.open('xb') as dst:
            owned = True
            with source.open('rb') as src:
                shutil.copyfileobj(src, dst, 1024 * 1024)
    except BaseException:
        if owned:
            output.unlink(missing_ok=True)
        raise


def extract_studio(path: Path, output: Path) -> dict:
    """Extract the verified old-format inner ZIP, never overwriting an output."""
    if output.exists() or output.is_symlink():
        raise AuthoringError('output already exists; never overwrite another backup')
    result = verify_desk(path)
    with tempfile.TemporaryDirectory(prefix='manju-desk-extract-', dir=output.parent) as td:
        staged = Path(td) / 'STUDIO.zip'
        with zipfile.ZipFile(path) as archive, archive.open('STUDIO.zip') as source, staged.open('xb') as dst:
            shutil.copyfileobj(source, dst, 1024 * 1024)
        if file_digest(path) != result['archive_sha256'] or file_digest(staged) != result['studio_archive_sha256']:
            raise AuthoringError('source archive changed; no extracted output published')
        verify_studio(staged)
        _publish_new(staged, output)
    return {'ok': True, 'output': str(output), 'sha256': result['studio_archive_sha256'],
            'original_studio_bytes_preserved': True, 'unapplied_buffers_included': False}
