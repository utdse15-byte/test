"""Editable external text exchange, separate from immutable studio backups.

A three-way comparison preserves concurrent local edits. A content hash detects
accidental baseline edits, not authorship or trust. Inputs are data, never code,
approval or model capability evidence. Applying creates a NEW Studio/v1 archive.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import math
import shutil
import tempfile
from typing import Literal
import zipfile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .core import AuthoringError, Digest, canonical, digest, file_digest, safe_relative
from .flexibility import read_verified, selected_sections
from .studio import Studio, verify_studio
from .workspace import MAX_FILE, MAX_JSON, MAX_TOTAL, json_value

SECTIONS = ('shot', 'review', 'repair', 'director', 'settings')
FIELDS = {
    'shot': ('shot-id', 'task', 'prompt', 'duration', 'resolution', 'ratio', 'preserve', 'change', 'reviewer'),
    'review': ('notes',),  # Unsubmitted notes only. Verdicts/scores/decisions are not imported.
    'repair': ('repair-start', 'repair-end', 'repair-before', 'repair-after',
               'repair-preserve', 'repair-change', 'repair-audio', 'repair-human'),
    'director': ('director-shot', 'director-preserve', 'director-change', 'director-audio'),
    'settings': ('return-width', 'return-height', 'promotion-resolution'),
}
MAX_FIELDS = 41  # 25 fixed fields and at most 16 target descriptions.
ANCHOR_KEY = re.compile(r'^director/anchors/(A[0-9]{2})/target$')


def text_limit(key: str) -> int:
    area, _, field = key.partition('/')
    if area in FIELDS and field in FIELDS[area]:
        return 6000 if area == 'review' else 30000
    if ANCHOR_KEY.fullmatch(key):
        return 4000
    raise AuthoringError('unknown or read-only exchange field: ' + key)


def _text(key: str, value: str) -> None:
    # Match JavaScript UTF-16 units, including astral Unicode. Do not strip spaces.
    if not isinstance(value, str):
        raise AuthoringError('exchange values must be text')
    try:
        units = len(value.encode('utf-16-le')) // 2
    except UnicodeError as exc:
        raise AuthoringError('unpaired Unicode surrogate in exchange text') from exc
    if units > text_limit(key):
        raise AuthoringError('exchange text exceeds field limit: ' + key)


class ExternalEdit(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_id: Literal['manju.external-edit/v1']
    sections: list[str] = Field(min_length=1, max_length=5)
    base: dict[str, str]
    values: dict[str, str]
    contexts: dict[str, Digest]
    base_sha256: Digest
    transfers_approval: Literal[False]
    automatic_execution: Literal[False]

    @model_validator(mode='after')
    def closed(self):
        if self.sections != selected_sections(self.sections, SECTIONS):
            raise ValueError('exchange sections require canonical order')
        if (not 1 <= len(self.base) <= MAX_FIELDS or set(self.base) != set(self.values)
                or set(self.base) != set(self.contexts)):
            raise ValueError('exchange must preserve exactly its exported field keys')
        for key in self.base:
            if key.split('/')[0] not in self.sections:
                raise ValueError('field outside the selected section')
            _text(key, self.base[key]); _text(key, self.values[key])
        if digest({'sections': self.sections, 'base': self.base, 'contexts': self.contexts}) != self.base_sha256:
            raise ValueError('export baseline changed; edit values only')
        if len(canonical(self)) > MAX_JSON:
            raise ValueError('exchange JSON exceeds 2 MiB')
        return self

    @model_validator(mode='wrap')
    @classmethod
    def exact(cls, value, handler):
        result = handler(value)
        if isinstance(value, dict) and canonical(value) != canonical(result):
            raise ValueError('exchange requires exact fields without coercion')
        return result


def _raw(doc: Studio | dict) -> dict:
    return deepcopy(doc.model_dump(mode='json') if isinstance(doc, BaseModel) else doc)


def _form(raw: dict, area: str) -> dict:
    if area == 'shot':
        return raw['workspace']['draft']['form']
    if area == 'review':
        return raw['workspace']['review_form']
    if area == 'settings':
        return raw['auxiliary_form']
    return raw[area]['form']


def field_state(doc: Studio | dict) -> tuple[dict[str, str], dict[str, str]]:
    """Also accepts a UI draft view with missing media bytes; no fake backup."""
    raw = _raw(doc); w = raw['workspace']; draft = w['draft']
    source = lambda area: (raw[area]['source'] or {}).get('sha256')
    shot_scope = {'shot_id': draft['form']['shot-id'], 'assets': draft['assets'],
                  'origin': (draft['sourceContext'] or {}).get('source_plan_sha256')}
    scopes = {
        'shot': shot_scope,
        'review': {'session': (w['review'] or {}).get('session', {}).get('session_sha256'),
                   'pending': sorted(c['sha256'] for c in w['pending'])},
        'repair': {'request': digest(raw['repair']['request']) if raw['repair']['request'] is not None else None,
                   'source': source('repair')},
        'director': {'shot_id': raw['director']['form']['director-shot'], 'source': source('director')},
        'settings': shot_scope,
    }
    values, contexts = {}, {}
    for area, keys in FIELDS.items():
        for key in keys:
            path = area + '/' + key; value = _form(raw, area)[key]; _text(path, value)
            values[path] = value; contexts[path] = digest(scopes[area])
    for anchor in raw['director']['anchors']:
        path = 'director/anchors/' + anchor['id'] + '/target'; _text(path, anchor['target'])
        if path in values:
            raise AuthoringError('duplicate anchor field')
        values[path] = anchor['target']
        contexts[path] = digest({**scopes['director'], 'anchor': anchor['id'],
                                'time': anchor['source_time_ms'], 'frame': anchor['frame']['sha256'],
                                'guide': (anchor['guide'] or {}).get('sha256')})
    return values, contexts


def create_edit(doc: Studio | dict, sections: list[str] | None = None) -> ExternalEdit:
    sections = selected_sections(list(SECTIONS) if sections is None else sections, SECTIONS)
    values, scopes = field_state(doc)
    base = {k: v for k, v in sorted(values.items()) if k.split('/')[0] in sections}
    contexts = {k: scopes[k] for k in base}
    return ExternalEdit.model_validate({
        'schema_id': 'manju.external-edit/v1', 'sections': sections, 'base': base,
        'values': deepcopy(base), 'contexts': contexts,
        'base_sha256': digest({'sections': sections, 'base': base, 'contexts': contexts}),
        'transfers_approval': False, 'automatic_execution': False,
    })


def normalize_edit(edit: ExternalEdit | dict) -> ExternalEdit:
    return ExternalEdit.model_validate(_raw(edit))


def read_edit(path: Path) -> ExternalEdit:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON:
        raise AuthoringError('edit JSON must be a regular file within 2 MiB')
    # utf-8-sig accepts a Windows UTF-8 BOM. Do not accept invalid/replaced bytes.
    with path.open('rb') as f:
        data = f.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise AuthoringError('edit JSON grew beyond 2 MiB')
    try:
        return ExternalEdit.model_validate(json_value(data.decode('utf-8-sig').encode('utf-8')))
    except (RecursionError, UnicodeError) as exc:
        raise AuthoringError('edit JSON is too deeply nested or not valid UTF-8') from exc


def preview_edit(doc: Studio | dict, edit: ExternalEdit | dict) -> dict:
    edit = normalize_edit(edit); current, contexts = field_state(doc); rows = []
    for key in sorted(edit.base):
        base, value, local = edit.base[key], edit.values[key], current.get(key)
        if value == base:
            status = 'unchanged'
        elif key not in current or contexts[key] != edit.contexts[key]:
            status = 'context_changed'
        elif value == local:
            status = 'already_applied'
        elif local == base:
            status = 'ready'
        else:
            status = 'conflict'
        rows.append({'key': key, 'base': base, 'local': local, 'external': value, 'status': status})
    return {'schema_id': 'manju.external-edit-preview/v1', 'edit_sha256': digest(edit),
            'current_sha256': digest({'values': current, 'contexts': contexts}),
            'rows': rows, 'transfers_approval': False, 'automatic_execution': False}


_NUMBER_FIELDS = {'shot/duration', 'repair/repair-start', 'repair/repair-end',
                  'repair/repair-before', 'repair/repair-after',
                  'settings/return-width', 'settings/return-height'}
_SINGLE_LINE = {'shot/shot-id', 'shot/reviewer', 'repair/repair-human', 'director/director-shot'}


def assert_settable(key: str, text: str) -> None:
    """Do not publish a raw draft the browser would silently sanitize on restore."""
    if '\r' in text or (key in _SINGLE_LINE and '\n' in text):
        raise AuthoringError('field cannot preserve these line endings in the browser: ' + key)
    if key in _NUMBER_FIELDS and text:
        if (not re.fullmatch(r'-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', text)
                or not math.isfinite(float(text))):
            raise AuthoringError('field cannot preserve this number in the browser: ' + key)


def apply_edit(doc: Studio | dict, edit: ExternalEdit | dict, take: list[str]) -> tuple[dict, dict]:
    """Explicit per-field decisions. A conflict is never silently auto-taken."""
    raw = _raw(doc); report = preview_edit(raw, edit)
    eligible = {r['key']: r for r in report['rows'] if r['status'] in ('ready', 'conflict')}
    if not isinstance(take, list) or not take or len(set(take)) != len(take) or set(take) - eligible.keys():
        raise AuthoringError('select nonempty unique ready/conflict fields; changed context cannot be overridden')
    for key in take:
        assert_settable(key, eligible[key]['external'])
    for key in take:
        value = eligible[key]['external']; area, field = key.split('/', 1)
        anchor = ANCHOR_KEY.fullmatch(key)
        if anchor:
            next(a for a in raw['director']['anchors'] if a['id'] == anchor[1])['target'] = value
        else:
            _form(raw, area)[field] = value
    if any(k.startswith('shot/') and k != 'shot/reviewer' for k in take):
        raw['workspace']['draft'].update(stage='draft', draftHash=None, sourceContext=None)
    report.update(applied=sorted(take), not_applied=[r['key'] for r in report['rows']
                   if r['external'] != r['base'] and r['key'] not in take])
    # Old decisions remain historical; no media records or catalog are changed.
    return raw, report


def text_members(edit: ExternalEdit | dict) -> dict[str, str]:
    value = normalize_edit(edit)
    return {f'text-{value.base_sha256[:16]}-{n:03d}.txt': key for n, key in enumerate(sorted(value.base), 1)}


def overlay_texts(edit: ExternalEdit | dict, files: dict[str, bytes]) -> ExternalEdit:
    """Only named field files from this exact EDIT.json; LF is explicit here."""
    raw = normalize_edit(edit).model_dump(mode='json'); names = text_members(raw)
    if not files or set(files) - names.keys() or sum(len(b) for b in files.values()) > MAX_JSON:
        raise AuthoringError('unknown, empty or oversized text file selection')
    for name, data in files.items():
        if not isinstance(data, bytes) or len(data) > 120003:
            raise AuthoringError('text file exceeds its UTF-8 bound')
        try:
            value = data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        except UnicodeError as exc:
            raise AuthoringError('field file must be UTF-8: ' + name) from exc
        raw['values'][names[name]] = value
    return ExternalEdit.model_validate(raw)


def media_uses(doc: Studio | dict, sections: list[str]) -> list[dict]:
    raw = _raw(doc); sections = selected_sections(sections, SECTIONS); rows = []
    def add(record, usage, suggested=None):
        if record:
            rows.append({'sha256': record['sha256'], 'bytes': record['bytes'],
                         'filename': suggested or record.get('filename') or Path(record.get('path', 'media')).name,
                         'usage': usage})
    w = raw['workspace']
    if 'shot' in sections:
        for asset in w['draft']['assets']:
            add(asset, 'shot/' + asset['role'] + '/' + asset['id'])
    if 'review' in sections:
        for c in [*w['pending'], *((w['review'] or {}).get('session', {}).get('candidates', []))]:
            add(c, 'review/candidate')
        for a in (w['review'] or {}).get('session', {}).get('request', {}).get('assets', []):
            add(a, 'review/request/' + a['role'] + '/' + a['id'])
    if 'repair' in sections:
        add(raw['repair']['source'], 'repair/source')
    if 'director' in sections:
        d = raw['director']; add(d['source'], 'director/motion-source')
        for a in d['anchors']:
            for kind in ('frame', 'guide'):
                add(a[kind], 'director/' + a['id'] + '/' + kind)
    return rows


def media_index(doc: Studio | dict, sections: list[str], include_media: bool) -> dict:
    uses = media_uses(doc, sections); grouped = {}
    for row in uses:
        h = row['sha256']
        if h not in grouped:
            filename = row['filename']; safe_relative(filename)
            if '/' in filename:
                raise AuthoringError('external media needs a basename')
            # ASCII hash path is stable on all platforms; original names and every
            # use remain explicit in the map. Retain the original extension.
            suffix = Path(filename).suffix.lower()
            if suffix in {'.ttf', '.otf', '.ttc', '.woff', '.woff2'}:
                raise AuthoringError('font files are not distributed')
            if suffix not in {'.png','.jpg','.jpeg','.webp','.heic','.heif','.mp4','.mov','.webm','.mkv','.wav','.mp3','.m4a','.aac','.flac'}:
                suffix = '.bin'
            grouped[h] = {'sha256': h, 'bytes': row['bytes'], 'path': 'media/' + h + suffix,
                          'original_names': [], 'uses': [], 'included': include_media}
        record = grouped[h]
        if record['bytes'] != row['bytes'] or not 0 < row['bytes'] <= MAX_FILE:
            raise AuthoringError('conflicting or oversized media identity')
        if row['filename'] not in record['original_names']:
            record['original_names'].append(row['filename'])
        if row['usage'] not in record['uses']:
            record['uses'].append(row['usage'])
    if sum(r['bytes'] for r in grouped.values()) > MAX_TOTAL:
        raise AuthoringError('selected media exceeds 512 MiB')
    return {'schema_id': 'manju.external-media-map/v1', 'sections': sections,
            'files': [grouped[h] for h in sorted(grouped)], 'media_modified': False,
            'transfers_approval': False, 'automatic_execution': False}


def _publish(stage: Path, output: Path) -> None:
    owned = False
    try:
        with output.open('xb') as dest:
            owned = True
            with stage.open('rb') as src:
                shutil.copyfileobj(src, dest)
        if file_digest(output) != file_digest(stage):
            raise AuthoringError('published file does not match staged file')
    except BaseException:
        if owned:
            output.unlink(missing_ok=True)
        raise


def _copy_media(src: zipfile.ZipFile, dst: zipfile.ZipFile, source_name: str,
                output_name: str, size: int, expected: str) -> None:
    info = src.getinfo(source_name)
    if info.file_size != size or info.compress_type != zipfile.ZIP_STORED:
        raise AuthoringError('source media changed before copy')
    h = sha256(); remaining = size
    with src.open(source_name) as inp, dst.open(output_name, 'w') as out:
        while remaining:
            chunk = inp.read(min(1024 * 1024, remaining))
            if not chunk:
                raise AuthoringError('source media truncated')
            h.update(chunk); out.write(chunk); remaining -= len(chunk)
        if inp.read(1) or h.hexdigest() != expected:
            raise AuthoringError('source media digest mismatch')


def kit_readme(edit: ExternalEdit | dict, index: dict) -> str:
    rows = ['# Manju 外部编辑包 / External editing kit', '',
            '这不是备份或生成授权。解压后可直接打开 media/ 原素材；没有转码、缩图或上传。',
            '编辑 EDIT.json 的 values，保留 base、contexts、base_sha256 和字段名。',
            '也可编辑 texts/ 内的 UTF-8 文本。回到第09区先加载原 EDIT.json，再选择改过的 text-材料ID-xxx.txt。',
            '两种方式选一种即可；加载文本文件会更新对应 values。CRLF/CR 显式转换为 LF，空格保留。',
            '只导回 EDIT.json 或选中的文本，不重打包 ZIP。说明、媒体映射和历史批准不作为可执行输入。',
            '导入先逐字段三方比较。冲突默认保留本地，换底片/关键帧的旧修改拒绝带入。',
            '外部媒体结果请通过原素材/候选/目标图入口显式绑定；不会根据文件名替换原片。',
            'JSON 的哈希不证明作者身份或版权。分享前自行检查隐私文字、素材权利和文件名。', '',
            '## 文字文件与字段']
    rows += [f'- texts/{name}: {key}' for name, key in text_members(edit).items()]
    rows += ['', '## 素材', f"包含实际字节：{'是' if any(r['included'] for r in index['files']) else '否'}",
             'MEDIA_MAP.json 记录所有用途、原文件名与 SHA-256。同一内容在多个区域使用时只复制一次。', '']
    return '\n'.join(rows)


def export_kit(archive: Path, output: Path, sections: list[str] | None = None, *,
               include_media: bool = True) -> dict:
    if output.exists() or output.is_symlink():
        raise FileExistsError('use a new output name; originals are never overwritten')
    doc, before = read_verified(archive); edit = create_edit(doc, sections)
    index = media_index(doc, edit.sections, include_media)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.manju-exchange-', dir=output.parent) as td:
        stage = Path(td) / 'kit.zip'
        with zipfile.ZipFile(archive) as src, zipfile.ZipFile(stage, 'w', zipfile.ZIP_STORED) as dest:
            dest.writestr('EDIT.json', canonical(edit))
            dest.writestr('MEDIA_MAP.json', canonical(index))
            dest.writestr('README.md', kit_readme(edit, index).encode('utf-8'))
            for name, key in text_members(edit).items():
                dest.writestr('texts/' + name, edit.values[key].encode('utf-8'))
            for record in index['files']:
                if record['included']:
                    _copy_media(src, dest, 'media/' + record['sha256'], record['path'],
                                record['bytes'], record['sha256'])
        with zipfile.ZipFile(stage) as check:
            if check.testzip() is not None:
                raise AuthoringError('external kit ZIP CRC mismatch')
        if file_digest(archive) != before['archive_sha256']:
            raise AuthoringError('source changed during export; no kit published')
        _publish(stage, output)
    return {'ok': True, 'output': str(output), 'archive_sha256': file_digest(output),
            'media_files': sum(r['included'] for r in index['files']),
            'edit_sha256': digest(edit), 'source_unchanged': True,
            'full_backup': False, 'automatic_execution': False}


def apply_archive(archive: Path, edit: ExternalEdit | dict, take: list[str], output: Path, *,
                  expected_preview: str | None = None) -> dict:
    if output.exists() or output.is_symlink():
        raise FileExistsError('use a new output name; original backups are never overwritten')
    doc, before = read_verified(archive)
    if expected_preview is not None and digest(preview_edit(doc, edit)) != expected_preview:
        raise AuthoringError('preview is stale; preview the current archive and edit again')
    raw, report = apply_edit(doc, edit, take); result = Studio.model_validate(raw)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.manju-exchange-', dir=output.parent) as td:
        stage = Path(td) / 'new-studio.zip'
        with zipfile.ZipFile(archive) as src, zipfile.ZipFile(stage, 'w', zipfile.ZIP_STORED) as dest:
            payload = canonical(result); sums = {'STUDIO.json': sha256(payload).hexdigest()}
            dest.writestr('STUDIO.json', payload)
            for record in result.media:
                name = 'media/' + record.sha256
                _copy_media(src, dest, name, name, record.bytes, record.sha256); sums[name] = record.sha256
            dest.writestr('MANIFEST.json', canonical({'schema_id': 'manju.studio-manifest/v1', 'files': sums}))
        receipt = verify_studio(stage)
        if file_digest(archive) != before['archive_sha256']:
            raise AuthoringError('source changed during apply; no output published')
        _publish(stage, output)
    return {**receipt, 'output': str(output), 'source_unchanged': True, 'preview': report,
            'media_bytes_unchanged': True, 'automatic_execution': False}
