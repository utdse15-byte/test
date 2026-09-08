"""A whole-studio checkpoint, not an execution or approval document.

The embedded Workspace/v1 is unchanged. Unfinished repair/director forms travel
as raw strings and content-addressed media is stored once across all three areas.
Verification is bounded and read-only; it never extracts archive paths.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import stat
import struct
from typing import Literal
import zipfile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .core import AuthoringError, Request, canonical, digest, file_digest
from .director import Anchor, Raster, png_dimensions
from .workspace import (MAX_ARCHIVE, MAX_FILE, MAX_JSON, MAX_TOTAL, MediaBinding,
                        Workspace, json_value, required_media)
from ..review.core import Candidate

REPAIR_FORM = {'repair-start', 'repair-end', 'repair-before', 'repair-after',
               'repair-preserve', 'repair-change', 'repair-audio', 'repair-human'}
DIRECTOR_FORM = {'director-shot', 'director-audio', 'director-preserve', 'director-change'}
AUXILIARY_FORM = {'return-width', 'return-height', 'promotion-resolution', 'asset-role'}
MAX_MEDIA = 134  # 100 workspace media + two sources + 16 pairs of rasters.
MAX_ENTRIES = MAX_MEDIA + 2


def _raw_form(value: dict[str, str], keys: set[str]) -> None:
    if set(value) != keys or any(len(v) > 30000 for v in value.values()):
        raise ValueError('incomplete or oversized raw studio form')


def _source(value: Candidate | None) -> None:
    if value and (value.media_check == 'hash_only' or value.bytes > MAX_FILE):
        raise ValueError('studio source requires measured metadata within local limits')


class RawRepair(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    form: dict[str, str]
    request: Request | None
    source: Candidate | None

    @model_validator(mode='after')
    def check(self):
        _raw_form(self.form, REPAIR_FORM)
        _source(self.source)
        return self


class RawAnchor(Anchor):
    # A half-written target must not be trimmed while checkpointing.
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=False)


class RawDirector(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    form: dict[str, str]
    source: Candidate | None
    anchors: list[RawAnchor] = Field(max_length=16)

    @model_validator(mode='after')
    def check(self):
        _raw_form(self.form, DIRECTOR_FORM)
        _source(self.source)
        if self.anchors and self.source is None:
            raise ValueError('anchors require their actual source video')
        ids, times = set(), set()
        for a in self.anchors:
            if a.id in ids or a.source_time_ms in times:
                raise ValueError('duplicate director anchor ID or time')
            ids.add(a.id); times.add(a.source_time_ms)
            if (a.source_time_ms >= self.source.duration_ms or
                    (a.frame.width, a.frame.height) != (self.source.width, self.source.height)):
                raise ValueError('anchor time or source raster is inconsistent')
        return self


class Studio(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_id: Literal['manju.studio-session/v1']
    workspace: Workspace
    repair: RawRepair
    director: RawDirector
    auxiliary_form: dict[str, str]
    media: list[MediaBinding] = Field(max_length=MAX_MEDIA)
    quality_only_on_restore: Literal[True]
    confirmations_restored: Literal[False]
    automatic_execution: Literal[False]
    project_modified: Literal[False]

    @model_validator(mode='wrap')
    @classmethod
    def exact_document(cls, value, handler):
        result = handler(value)
        if isinstance(value, dict) and canonical(value) != canonical(result):
            raise ValueError('studio session requires canonical fields without coercion')
        return result

    @model_validator(mode='after')
    def closure(self):
        _raw_form(self.auxiliary_form, AUXILIARY_FORM)
        refs = studio_media(self)
        bound = {m.sha256: m.bytes for m in self.media}
        if len(bound) != len(self.media) or bound != refs:
            raise ValueError('studio must include exactly its referenced media')
        if sum(bound.values()) > MAX_TOTAL:
            raise ValueError('studio exceeds the shared local 512 MiB limit')
        if len(canonical(self)) > MAX_JSON:
            raise ValueError('studio metadata exceeds 2 MiB')
        return self


def studio_rasters(doc: Studio) -> list[Raster]:
    return [r for a in doc.director.anchors for r in (a.frame, a.guide) if r is not None]


def studio_videos(doc: Studio) -> list[Candidate]:
    result = list(doc.workspace.pending)
    if doc.workspace.review:
        result += list(doc.workspace.review.session.candidates)
    return result + [s for s in (doc.repair.source, doc.director.source) if s is not None]


def studio_media(doc: Studio) -> dict[str, int]:
    required = required_media(doc.workspace)
    geometries: dict[str, tuple[int | None, ...]] = {}
    for rec in [*studio_videos(doc), *studio_rasters(doc)]:
        if rec.sha256 in required and required[rec.sha256] != rec.bytes:
            raise ValueError('one media identity claims conflicting sizes')
        required[rec.sha256] = rec.bytes
        geometry = (rec.width, rec.height, getattr(rec, 'duration_ms', None))
        if rec.sha256 in geometries:
            old = geometries[rec.sha256]
            if any(a is not None and b is not None and a != b for a, b in zip(old, geometry)):
                raise ValueError('one media identity claims conflicting geometry or duration')
            geometry = tuple(a if a is not None else b for a, b in zip(old, geometry))
        geometries[rec.sha256] = geometry
    return required


def _directory(path: Path, archive: zipfile.ZipFile) -> set[str]:
    """Validate both ZIP directories before allocating or reading member bodies."""
    infos = archive.infolist(); size = path.stat().st_size
    if not 2 <= len(infos) <= MAX_ENTRIES or archive.comment:
        raise AuthoringError('studio ZIP entry count or comment is invalid')
    with path.open('rb') as stream:
        stream.seek(size - 22)
        end = struct.unpack('<4s4H2LH', stream.read(22))
        sig, disk, cd_disk, local_count, count, cd_size, cd_start, comment = end
        if (sig != b'PK\x05\x06' or disk or cd_disk or comment or local_count != count or
                count != len(infos) or cd_size > MAX_ENTRIES * 286 or
                cd_start + cd_size != size - 22 or cd_start != archive.start_dir):
            raise AuthoringError('studio ZIP directory is not closed')
        names, cursor, total, central_cursor = set(), 0, 0, cd_start
        for info in infos:
            name = info.filename
            if name not in {'STUDIO.json', 'MANIFEST.json'} and not re.fullmatch(r'media/[a-f0-9]{64}', name):
                raise AuthoringError('studio contains an unapproved member path')
            if (name in names or info.is_dir() or info.compress_type != 0 or info.extra or
                    info.comment or info.flag_bits & ~0x800 or info.file_size != info.compress_size or
                    stat.S_ISLNK(info.external_attr >> 16) or info.header_offset != cursor):
                raise AuthoringError('unsafe, duplicate, compressed or prefixed studio member')
            limit = MAX_JSON if name.endswith('.json') else MAX_FILE
            if not 0 < info.file_size <= limit:
                raise AuthoringError('studio member exceeds its size limit')
            stream.seek(cursor)
            raw = stream.read(30)
            if len(raw) != 30:
                raise AuthoringError('truncated studio local record')
            h = struct.unpack('<4s5H3L2H', raw)
            signature, _, flags, method, _, _, crc, packed, unpacked, n, extra = h
            if not 0 < n <= 240:
                raise AuthoringError('studio path length exceeds limit')
            local_name = stream.read(n).decode('utf-8')
            if (signature != b'PK\x03\x04' or flags != info.flag_bits or method or extra or
                    local_name != name or crc != info.CRC or packed != info.file_size or unpacked != packed):
                raise AuthoringError('studio local/central records disagree')
            stream.seek(central_cursor); central = stream.read(46)
            if len(central) != 46 or central[:4] != b'PK\x01\x02':
                raise AuthoringError('invalid studio central record')
            n2, extra2, comment2, disk2 = struct.unpack_from('<4H', central, 28)
            if n2 != n or extra2 or comment2 or disk2 or stream.read(n2).decode('utf-8') != name:
                raise AuthoringError('unsupported studio central fields')
            central_cursor += 46 + n2
            cursor += 30 + n + packed; total += unpacked; names.add(name)
        if cursor != cd_start or central_cursor != size - 22 or total > MAX_TOTAL + 2 * MAX_JSON:
            raise AuthoringError('studio ZIP gaps, trailing data or excess total size')
        return names


def _file_identity(path: Path) -> tuple[int, ...]:
    # Exclude atime: a read may legitimately change it. Replacement, truncation
    # or edits during validation cannot inherit an earlier file's receipt.
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def verify_studio(path: Path) -> dict:
    """Verify identity and PNG structure, not full video decode or approval."""
    if path.is_symlink() or not path.is_file() or not 22 <= path.stat().st_size <= MAX_ARCHIVE:
        raise AuthoringError('studio ZIP missing, linked or oversized')
    before = _file_identity(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = _directory(path, archive)
            if not {'STUDIO.json', 'MANIFEST.json'} <= names:
                raise AuthoringError('studio metadata missing')
            doc = Studio.model_validate(json_value(archive.read('STUDIO.json')))
            manifest = json_value(archive.read('MANIFEST.json'))
            expected = {'STUDIO.json'} | {'media/' + h for h in studio_media(doc)}
            if (not isinstance(manifest, dict) or set(manifest) != {'schema_id', 'files'} or
                    manifest['schema_id'] != 'manju.studio-manifest/v1' or
                    not isinstance(manifest['files'], dict) or set(manifest['files']) != expected or
                    names != expected | {'MANIFEST.json'}):
                raise AuthoringError('studio inventory must be the exact referenced set')
            sizes = studio_media(doc)
            for name in expected:
                h = sha256()
                with archive.open(name) as stream:
                    while block := stream.read(1024 * 1024):
                        h.update(block)
                if h.hexdigest() != manifest['files'][name]:
                    raise AuthoringError('studio member hash mismatch: ' + name)
                if name.startswith('media/'):
                    identity = name[6:]
                    if identity != h.hexdigest() or archive.getinfo(name).file_size != sizes[identity]:
                        raise AuthoringError('studio media identity or size mismatch')
            for raster in studio_rasters(doc):
                if png_dimensions(archive.read('media/' + raster.sha256)) != (raster.width, raster.height):
                    raise AuthoringError('studio PNG dimensions disagree with the draft')
            archive_digest = file_digest(path)
            if path.is_symlink() or _file_identity(path) != before:
                raise AuthoringError('studio file changed during verification; retry with a stable copy')
            return {'ok': True, 'schema_id': doc.schema_id, 'studio_sha256': digest(doc),
                    'archive_sha256': archive_digest, 'media_files': len(doc.media),
                    'media_bytes': sum(m.bytes for m in doc.media),
                    'review_decisions': len(doc.workspace.review.decisions) if doc.workspace.review else 0,
                    'director_anchors': len(doc.director.anchors),
                    'repair_has_source': doc.repair.source is not None,
                    'raw_forms_preserved': True, 'quality_only_on_restore': True,
                    'confirmations_restored': False, 'automatic_execution': False,
                    'project_modified': False, 'video_decode_verified': False,
                    'png_pixels_fully_decoded': False}
    except (zipfile.BadZipFile, KeyError, UnicodeError, struct.error, RecursionError) as exc:
        raise AuthoringError(f'invalid studio ZIP: {exc}') from exc
