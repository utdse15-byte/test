"""Portable, timestamp-bounded revision instructions, not generation or approval.

The complete original video is retained. Milliseconds address presentation time
from the start of that video; neither frame accuracy nor unchanged surroundings
are certified. Request/v1 remains immutable context, not a new executable task.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil
import stat
import struct
import tempfile
import unicodedata
import zipfile
from typing import Literal

from pydantic import Field, model_validator

from .core import (AuthoringError, Digest, Request, StrictModel, canonical,
                   digest, file_digest, safe_relative)
from .workspace import MAX_FILE, MAX_JSON, json_value
from ..review.core import Candidate


class RepairPlan(StrictModel):
    schema_id: Literal['manju.repair-plan/v1'] = 'manju.repair-plan/v1'
    request: Request
    request_sha256: Digest
    source: Candidate
    start_ms: int = Field(ge=0, le=86_400_000, strict=True)
    end_ms: int = Field(ge=1, le=86_400_000, strict=True)
    context_before_ms: int = Field(default=500, ge=0, le=10_000, strict=True)
    context_after_ms: int = Field(default=500, ge=0, le=10_000, strict=True)
    preserve: list[str] = Field(min_length=1, max_length=30)
    change: list[str] = Field(min_length=1, max_length=30)
    audio_policy: Literal['preserve_original', 'needs_manual_review'] = 'preserve_original'
    requested_by: str = Field(min_length=1, max_length=120)
    scope_confirmed: Literal[True] = True
    execution_authorized: Literal[False] = False
    automatic_selection: Literal[False] = False
    outside_interval_unchanged_verified: Literal[False] = False
    plan_sha256: Digest

    @model_validator(mode='wrap')
    @classmethod
    def complete_canonical_document(cls, value, handler):
        result = handler(value)
        if isinstance(value, dict) and canonical(value) != canonical(result):
            raise ValueError('repair document must contain complete canonical fields without coercion')
        return result

    @model_validator(mode='after')
    def validate_content(self):
        if self.source.media_check == 'hash_only' or self.source.duration_ms is None:
            raise ValueError('repair needs measured source video duration and dimensions')
        if self.source.bytes > MAX_FILE:
            raise ValueError('repair source exceeds local portable limit of 128 MiB')
        if not self.start_ms < self.end_ms <= self.source.duration_ms:
            raise ValueError('repair interval must satisfy 0 <= start < end <= source duration')
        for values in (self.preserve, self.change):
            if any(not isinstance(s, str) or not s.strip() or len(s) > 2000 for s in values):
                raise ValueError('each preserve/change instruction must have 1 to 2000 characters')
            if any(s != s.strip() for s in values):
                raise ValueError('repair instructions must be trimmed before hashing')
        norm = lambda s: unicodedata.normalize('NFKC', s).casefold()
        if set(map(norm, self.preserve)) & set(map(norm, self.change)):
            raise ValueError('the same instruction cannot be both preserved and changed')
        if digest(self.request) != self.request_sha256:
            raise ValueError('original request context changed')
        body = self.model_dump(mode='json')
        body.pop('plan_sha256')
        if digest(body) != self.plan_sha256:
            raise ValueError('repair plan content changed')
        return self


def create_plan(request: Request, source: Candidate, *, start_ms: int, end_ms: int,
                preserve: list[str], change: list[str], requested_by: str,
                human_confirmed: bool, context_before_ms: int = 500,
                context_after_ms: int = 500, audio_policy: str = 'preserve_original') -> RepairPlan:
    if not human_confirmed:
        raise AuthoringError('explicit confirmation of the revision scope is required')
    body = dict(schema_id='manju.repair-plan/v1', request=request.model_dump(mode='json'),
                request_sha256=digest(request), source=source.model_dump(mode='json'),
                start_ms=start_ms, end_ms=end_ms, context_before_ms=context_before_ms,
                context_after_ms=context_after_ms, preserve=[x.strip() for x in preserve],
                change=[x.strip() for x in change], requested_by=requested_by.strip(),
                audio_policy=audio_policy, scope_confirmed=True, execution_authorized=False,
                automatic_selection=False, outside_interval_unchanged_verified=False)
    body['plan_sha256'] = digest(body)
    return RepairPlan.model_validate(body)


def context_window(plan: RepairPlan) -> dict:
    start = max(0, plan.start_ms - plan.context_before_ms)
    end = min(plan.source.duration_ms, plan.end_ms + plan.context_after_ms)
    return {'start_ms': start, 'end_ms': end,
            'repair_start_relative_ms': plan.start_ms - start,
            'repair_end_relative_ms': plan.end_ms - start,
            'interval_convention': 'half_open_presentation_milliseconds'}


def source_member(plan: RepairPlan) -> str:
    return 'source/' + plan.source.sha256 + Path(plan.source.filename).suffix.lower()


def repair_brief(plan: RepairPlan) -> str:
    w = context_window(plan)
    lines = ['# 局部返工交接 / Scoped revision', '',
             f'镜头：{plan.request.shot_id}', f'原任务 SHA-256：{plan.request_sha256}',
             f'原片：{source_member(plan)}', f'原片 SHA-256：{plan.source.sha256}',
             f'请求人：{plan.requested_by}', '',
             f'返工范围（毫秒）：[{plan.start_ms}, {plan.end_ms})',
             f'供参考的上下文（毫秒）：[{w["start_ms"]}, {w["end_ms"]})',
             '计时从所附原片开始，使用半开区间。这不是帧精确剪辑或供应商参数。', '',
             '## 保留', *['- ' + s for s in plan.preserve], '',
             '## 修改', *['- ' + s for s in plan.change], '',
             f'声音策略：{plan.audio_policy}', '',
             '本包保留完整原片，没有剪切、上传、生成、自动拼接或批准返回视频。',
             '原任务仅作上下文，引用的其他素材未随此返工包携带。',
             '保留项是创作要求，不是模型一定保持不变的保证。',
             '外部返回后必须核对接缝、范围外画面和声音，并重新人工审片。',
             'SHA-256 只证明内容一致，不证明发布者身份或版权许可。', '']
    return '\n'.join(lines)


def _payload(plan: RepairPlan) -> dict[str, bytes]:
    return {'PLAN.json': (canonical(plan) + b'\n'),
            'BRIEF.md': repair_brief(plan).encode('utf-8')}


def _verify_stored_layout(path: Path, archive: zipfile.ZipFile) -> None:
    """Accept the same no-prefix/no-trailer stored ZIP layout as the browser.

    zipfile intentionally accepts self-extracting prefixes and trailing data;
    these are unnecessary for this four-member document protocol.
    """
    length = path.stat().st_size
    with path.open('rb') as stream:
        if length < 22:
            raise AuthoringError('truncated repair ZIP')
        stream.seek(length - 22)
        sig, disk, cd_disk, count_disk, count, size, offset, comment = struct.unpack('<4s4H2LH', stream.read(22))
        if (sig != b'PK\x05\x06' or disk or cd_disk or comment or count != 4
            or count_disk != count or offset + size != length - 22 or archive.start_dir != offset):
            raise AuthoringError('repair ZIP must have a closed single-disk layout')
        cursor = 0
        for info in archive.infolist():
            if info.header_offset != cursor:
                raise AuthoringError('repair ZIP prefix, gaps or reordered records are unsupported')
            stream.seek(cursor)
            raw = stream.read(30)
            if len(raw) != 30:
                raise AuthoringError('truncated repair local header')
            header = struct.unpack('<4s5H3L2H', raw)
            signature, _, flags, method, _, _, crc, compressed, unpacked, name_len, extra_len = header
            name = stream.read(name_len).decode('utf-8' if flags & 0x800 else 'cp437')
            if (signature != b'PK\x03\x04' or flags != info.flag_bits or method != 0 or extra_len
                or name != info.filename or crc != info.CRC or compressed != info.compress_size
                or unpacked != info.file_size):
                raise AuthoringError('repair local and central headers disagree')
            cursor += 30 + name_len + compressed
        if cursor != offset:
            raise AuthoringError('repair ZIP has unlisted local data')


def verify_repair_bundle(path: Path) -> dict:
    """Validate a closed stored-ZIP inventory without extracting or probing it."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE + 3 * MAX_JSON:
        raise AuthoringError('repair archive missing, linked or oversized')
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) != 4 or archive.comment:
                raise AuthoringError('repair archive requires exactly four members and no comment')
            _verify_stored_layout(path, archive)
            names = set()
            for info in infos:
                safe_relative(info.filename)
                if (info.filename in names or info.is_dir() or info.compress_type != zipfile.ZIP_STORED
                    or info.flag_bits & ~0x800 or info.extra or info.comment
                    or info.file_size != info.compress_size or stat.S_ISLNK(info.external_attr >> 16)):
                    raise AuthoringError('unsupported or duplicate repair archive member')
                limit = MAX_FILE if info.filename.startswith('source/') else MAX_JSON
                if not 0 < info.file_size <= limit:
                    raise AuthoringError('repair member size exceeds limit')
                names.add(info.filename)
            if not {'PLAN.json', 'BRIEF.md', 'MANIFEST.json'} <= names:
                raise AuthoringError('repair metadata missing')
            plan = RepairPlan.model_validate(json_value(archive.read('PLAN.json')))
            expected = {'PLAN.json', 'BRIEF.md', source_member(plan)}
            manifest = json_value(archive.read('MANIFEST.json'))
            if (not isinstance(manifest, dict) or set(manifest) != {'schema_id', 'files'}
                or manifest['schema_id'] != 'manju.repair-manifest/v1'
                or not isinstance(manifest['files'], dict) or set(manifest['files']) != expected
                or names != expected | {'MANIFEST.json'}):
                raise AuthoringError('repair inventory is not a closed set')
            for name in expected:
                h = sha256()
                with archive.open(name) as stream:
                    while chunk := stream.read(1024 * 1024):
                        h.update(chunk)
                if h.hexdigest() != manifest['files'][name]:
                    raise AuthoringError(f'repair file hash mismatch: {name}')
            source = source_member(plan)
            if (manifest['files'][source] != plan.source.sha256
                or archive.getinfo(source).file_size != plan.source.bytes):
                raise AuthoringError('repair source differs from the recorded video')
            if archive.read('BRIEF.md') != repair_brief(plan).encode('utf-8'):
                raise AuthoringError('repair brief differs from its plan')
            return {'ok': True, 'schema_id': 'manju.repair-verification/v1',
                    'plan_sha256': plan.plan_sha256, 'source_sha256': plan.source.sha256,
                    'context_window': context_window(plan), 'source_video_included': True,
                    'metadata_independently_reprobed': False, 'execution_authorized': False,
                    'automatic_selection': False, 'outside_interval_unchanged_verified': False}
    except (zipfile.BadZipFile, UnicodeError, RecursionError) as exc:
        raise AuthoringError(f'invalid repair archive: {exc}') from exc


def write_repair_bundle(plan: RepairPlan, source: Path, output: Path) -> dict:
    plan = RepairPlan.model_validate(plan.model_dump(mode='json'))
    if source.is_symlink() or not source.is_file():
        raise AuthoringError('select a regular local source video')
    if output.exists() or output.is_symlink():
        raise FileExistsError('never overwrite an existing repair bundle')
    if source.stat().st_size != plan.source.bytes or file_digest(source) != plan.source.sha256:
        raise AuthoringError('source content differs from the repair plan')
    with tempfile.TemporaryDirectory(prefix='manju-repair-', dir=output.parent) as tmp:
        staged = Path(tmp) / 'repair.zip'
        payload = _payload(plan)
        files = {k: sha256(v).hexdigest() for k, v in payload.items()}
        files[source_member(plan)] = plan.source.sha256
        manifest = {'schema_id': 'manju.repair-manifest/v1', 'files': files}
        with zipfile.ZipFile(staged, 'w', zipfile.ZIP_STORED) as archive:
            for name, data in payload.items():
                archive.writestr(name, data)
            archive.write(source, source_member(plan))
            archive.writestr('MANIFEST.json', canonical(manifest) + b'\n')
        result = verify_repair_bundle(staged)
        # Catch a producer updating the source while the ZIP was being written.
        if file_digest(source) != plan.source.sha256:
            raise AuthoringError('source changed during export; no package published')
        owned = False
        try:
            with output.open('xb') as target:
                owned = True
                with staged.open('rb') as stream:
                    shutil.copyfileobj(stream, target)
        except BaseException:
            if owned:
                output.unlink(missing_ok=True)
            raise
        return result
