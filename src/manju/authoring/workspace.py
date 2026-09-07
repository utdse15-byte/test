"""Portable offline workspaces: raw draft, catalog, reviews and actual media.

A workspace is a checkpoint, never a production input or a new approval.
Only our uncompressed ZIP profile is accepted, with a closed hash inventory.
No archive paths are extracted and no user project is modified.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import stat
import unicodedata
import zipfile

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Any, Literal

from .core import (Asset, AuthoringError, Catalog, Digest, StrictModel, MAX_JSON,
                   digest, file_digest, safe_relative)
from ..review.core import Candidate, ReviewDocument

MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_ARCHIVE = MAX_TOTAL + 4 * 1024 * 1024
MAX_ENTRIES = 128
FORM_KEYS = {'shot-id', 'task', 'prompt', 'duration', 'resolution', 'ratio',
             'preserve', 'change', 'reviewer'}
REVIEW_FORM_KEYS = {'candidate', 'verdict', 'human', 'notes', 'score'}


def json_value(data: bytes) -> Any:
    if len(data) > MAX_JSON:
        raise AuthoringError('workspace JSON exceeds 2 MiB')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AuthoringError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              AuthoringError('invalid JSON number')))
    except (ValueError, UnicodeError) as exc:
        raise AuthoringError(f'invalid workspace JSON: {exc}') from exc


class RawDraft(BaseModel):
    # Raw form strings must retain whitespace and need not be valid Requests.
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1] = 1
    form: dict[str, str]
    assets: list[Asset] = Field(default_factory=list, max_length=32)
    stage: Literal['draft', 'final'] = 'draft'
    draftHash: Digest | None = None
    sourceContext: dict[str, Any] | None = None

    @model_validator(mode='after')
    def complete(self):
        if set(self.form) != FORM_KEYS or any(len(v) > 30000 for v in self.form.values()):
            raise ValueError('raw draft form fields are incomplete or oversized')
        if self.stage == 'final' and self.draftHash is None:
            raise ValueError('final draft must identify its source video')
        if self.stage == 'draft' and self.draftHash is not None:
            raise ValueError('draft cannot claim promotion')
        if len({a.id for a in self.assets}) != len(self.assets):
            raise ValueError('duplicate asset IDs')
        paths = {}
        for a in self.assets:
            key = unicodedata.normalize('NFC', a.path).casefold()
            identity = (a.path, a.sha256, a.bytes)
            if key in paths and paths[key] != identity:
                raise ValueError('conflicting portable asset paths')
            paths[key] = identity
        ctx = self.sourceContext
        if ctx is not None:
            if set(ctx) != {'source_plan', 'source_plan_sha256', 'warnings'}:
                raise ValueError('invalid source context keys')
            if not isinstance(ctx['source_plan'], dict) or digest(ctx['source_plan']) != ctx['source_plan_sha256']:
                raise ValueError('source context hash mismatch')
            if not isinstance(ctx['warnings'], list) or any(not isinstance(x, str) for x in ctx['warnings']):
                raise ValueError('invalid source warnings')
        return self


class MediaBinding(StrictModel):
    sha256: Digest
    bytes: int = Field(ge=1, le=MAX_FILE, strict=True)
    filename: str = Field(min_length=1, max_length=240)
    mime_type: str = Field(default='', max_length=100)

    @model_validator(mode='after')
    def basename(self):
        safe_relative(self.filename)
        if '/' in self.filename or any(ord(c) < 32 for c in self.mime_type):
            raise ValueError('invalid local media metadata')
        return self


class Workspace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_id: Literal['manju.authoring-workspace/v1'] = 'manju.authoring-workspace/v1'
    draft: RawDraft
    catalog: Catalog
    review: ReviewDocument | None = None
    pending: list[Candidate] = Field(default_factory=list, max_length=12)
    review_form: dict[str, str]
    reveal: bool = Field(default=False, strict=True)
    media: list[MediaBinding] = Field(default_factory=list, max_length=100)
    confirmations_restored: Literal[False] = False
    project_modified: Literal[False] = False

    @model_validator(mode='after')
    def closure(self):
        if set(self.review_form) != REVIEW_FORM_KEYS or any(len(v) > 6000 for v in self.review_form.values()):
            raise ValueError('invalid review form fields')
        if self.review and self.pending:
            raise ValueError('pending candidates cannot coexist with a fixed session')
        if len({c.sha256 for c in self.pending}) != len(self.pending):
            raise ValueError('duplicate pending candidate')
        required = required_media(self)
        bound = {m.sha256: m.bytes for m in self.media}
        if len(bound) != len(self.media) or bound != required:
            raise ValueError('workspace must include exactly all referenced media')
        if sum(bound.values()) > MAX_TOTAL:
            raise ValueError('workspace exceeds the local 512 MiB limit')
        return self


def required_media(workspace: Workspace) -> dict[str, int]:
    refs = list(workspace.draft.assets) + list(workspace.pending)
    if workspace.review:
        refs += list(workspace.review.session.request.assets) + list(workspace.review.session.candidates)
    required = {}
    for item in refs:
        if item.sha256 in required and required[item.sha256] != item.bytes:
            raise AuthoringError('one content hash claims conflicting sizes')
        required[item.sha256] = item.bytes
    return required


def verify_workspace(path: Path) -> dict:
    """Read, hash and validate a portable workspace, without extraction."""
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE:
        raise AuthoringError('workspace archive missing or oversized')
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if not 2 <= len(infos) <= MAX_ENTRIES or z.comment:
                raise AuthoringError('invalid workspace member count or ZIP comment')
            names, folded, total = {}, set(), 0
            for info in infos:
                safe_relative(info.filename)
                key = unicodedata.normalize('NFC', info.filename).casefold()
                if info.filename in names or key in folded:
                    raise AuthoringError('duplicate or colliding ZIP member')
                if (info.is_dir() or info.compress_type != zipfile.ZIP_STORED
                    or info.flag_bits & ~0x800 or info.file_size != info.compress_size
                    or stat.S_ISLNK(info.external_attr >> 16) or info.extra or info.comment):
                    raise AuthoringError('unsupported ZIP member; use a Manju workspace export')
                limit = MAX_JSON if info.filename.endswith('.json') else MAX_FILE
                if not 0 < info.file_size <= limit:
                    raise AuthoringError('workspace member exceeds its size limit')
                names[info.filename] = info
                folded.add(key)
                total += info.file_size
            if total > MAX_TOTAL + 2 * MAX_JSON:
                raise AuthoringError('workspace content exceeds size limit')
            if not {'WORKSPACE.json', 'MANIFEST.json'} <= names.keys():
                raise AuthoringError('workspace metadata missing')
            manifest = json_value(z.read('MANIFEST.json'))
            if (not isinstance(manifest, dict) or set(manifest) != {'schema_id', 'files'}
                or manifest['schema_id'] != 'manju.workspace-manifest/v1'
                or not isinstance(manifest['files'], dict)
                or set(manifest['files']) != set(names) - {'MANIFEST.json'}):
                raise AuthoringError('workspace hash inventory mismatch')
            for name, expected in manifest['files'].items():
                h = sha256()
                with z.open(name) as stream:
                    while block := stream.read(1024 * 1024):
                        h.update(block)
                if h.hexdigest() != expected:
                    raise AuthoringError(f'workspace member hash mismatch: {name}')
            workspace = Workspace.model_validate(json_value(z.read('WORKSPACE.json')))
            expected_names = {'WORKSPACE.json', 'MANIFEST.json'} | {'media/' + m.sha256 for m in workspace.media}
            if set(names) != expected_names:
                raise AuthoringError('unreferenced or missing workspace media')
            for m in workspace.media:
                name = 'media/' + m.sha256
                if names[name].file_size != m.bytes or manifest['files'][name] != m.sha256:
                    raise AuthoringError('workspace media identity mismatch')
            return {'ok': True, 'schema_id': workspace.schema_id,
                    'workspace_sha256': digest(workspace), 'archive_sha256': file_digest(path),
                    'media_files': len(workspace.media),
                    'review_decisions': len(workspace.review.decisions) if workspace.review else 0,
                    'raw_draft_restorable': True, 'confirmations_restored': False,
                    'project_modified': False, 'media_decode_verified': False}
    except (zipfile.BadZipFile, KeyError, UnicodeError) as exc:
        raise AuthoringError(f'invalid workspace ZIP: {exc}') from exc
