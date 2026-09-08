"""Motion-first director reference packs, not video execution or approval.

Browser snapshots are SDR PNG references at the displayed video raster. They
are not HDR masters and their timestamps are not certified decoded frame IDs.
The original video and optional, same-aspect edited guides travel together.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil
import stat
import struct
import tempfile
from typing import Literal
import unicodedata
import zipfile
import zlib

from pydantic import Field, model_validator

from .core import AuthoringError, Digest, StrictModel, canonical, digest, file_digest, safe_relative
from .workspace import MAX_FILE, MAX_JSON, MAX_TOTAL, json_value
from ..review.core import Candidate

MAX_RASTER = 32 * 1024 * 1024
MAX_PIXELS = 33_554_432


class Raster(StrictModel):
    sha256: Digest
    bytes: int = Field(gt=0, le=MAX_RASTER, strict=True)
    width: int = Field(ge=1, le=8192, strict=True)
    height: int = Field(ge=1, le=8192, strict=True)
    filename: str = Field(min_length=1, max_length=240)

    @model_validator(mode='after')
    def dimensions(self):
        safe_relative(self.filename)
        if '/' in self.filename or not self.filename.lower().endswith('.png'):
            raise ValueError('director rasters must have a flat PNG filename')
        if self.width * self.height > MAX_PIXELS:
            raise ValueError('director raster exceeds local pixel limit; no automatic resizing')
        return self


class Anchor(StrictModel):
    id: str = Field(pattern=r'^A[0-9]{2}$')
    source_time_ms: int = Field(ge=0, le=86_400_000, strict=True)
    frame: Raster
    guide: Raster | None
    target: str = Field(max_length=4000)
    capture_method: Literal['browser_canvas_sdr_reference'] = 'browser_canvas_sdr_reference'
    time_basis: Literal['presentation_time_not_certified_frame_index'] = 'presentation_time_not_certified_frame_index'

    @model_validator(mode='after')
    def same_aspect(self):
        if self.guide and self.guide.width * self.frame.height != self.frame.width * self.guide.height:
            raise ValueError('edited guide must match its anchor aspect ratio; native pixels are retained')
        return self


class DirectorPlan(StrictModel):
    schema_id: Literal['manju.director-plan/v1'] = 'manju.director-plan/v1'
    shot_id: str = Field(min_length=1, max_length=120)
    source: Candidate
    source_role: Literal['motion_timing_camera_reference_not_final_look']
    anchors: list[Anchor] = Field(min_length=1, max_length=16)
    preserve: list[str] = Field(max_length=30)
    change: list[str] = Field(max_length=30)
    audio_policy: Literal['preserve_original', 'needs_manual_review']
    delivery_status: Literal['draft_reference_only']
    execution_authorized: Literal[False]
    automatic_selection: Literal[False]
    plan_sha256: Digest

    @model_validator(mode='wrap')
    @classmethod
    def complete_document(cls, value, handler):
        result = handler(value)
        if isinstance(value, dict) and canonical(value) != canonical(result):
            raise ValueError('director document requires all canonical fields, without coercion')
        return result

    @model_validator(mode='after')
    def closed_identity(self):
        if self.shot_id != self.shot_id.strip():
            raise ValueError('shot id must be trimmed')
        if self.source.media_check == 'hash_only' or self.source.duration_ms is None:
            raise ValueError('source must have measured browser or ffprobe metadata')
        if self.source.bytes > MAX_FILE:
            raise ValueError('source exceeds local 128 MiB limit')
        times = [a.source_time_ms for a in self.anchors]
        if times != sorted(set(times)) or any(t >= self.source.duration_ms for t in times):
            raise ValueError('anchors require unique ascending times within the original video')
        if len({a.id for a in self.anchors}) != len(self.anchors):
            raise ValueError('duplicate anchor id')
        for a in self.anchors:
            if (a.frame.width, a.frame.height) != (self.source.width, self.source.height):
                raise ValueError('captured raster differs from measured source display raster')
        for items in (self.preserve, self.change):
            if any(not s or s != s.strip() or len(s) > 2000 for s in items):
                raise ValueError('instructions require trimmed text of 1 to 2000 characters')
        norm = lambda s: unicodedata.normalize('NFKC', s).casefold()
        if set(map(norm, self.preserve)) & set(map(norm, self.change)):
            raise ValueError('the same instruction cannot be preserved and changed')
        body = self.model_dump(mode='json'); body.pop('plan_sha256')
        if digest(body) != self.plan_sha256:
            raise ValueError('director plan hash mismatch')
        if sum(r.bytes for r in media_records(self).values()) > MAX_TOTAL:
            raise ValueError('director media exceeds local 512 MiB limit')
        return self


def source_member(plan: DirectorPlan) -> str:
    return 'source/' + plan.source.sha256 + Path(plan.source.filename).suffix.lower()


def raster_member(raster: Raster, kind: str) -> str:
    return kind + '/' + raster.sha256 + '.png'


def media_records(plan: DirectorPlan) -> dict[str, Candidate | Raster]:
    records = {source_member(plan): plan.source}
    for anchor in plan.anchors:
        for kind, raster in [('frames', anchor.frame), ('guides', anchor.guide)]:
            if raster:
                name = raster_member(raster, kind)
                if name in records and any(getattr(records[name], k) != getattr(raster, k)
                                           for k in ('sha256', 'bytes', 'width', 'height')):
                    raise ValueError('same media identity has conflicting metadata')
                records[name] = raster
    return records


def director_brief(plan: DirectorPlan) -> str:
    lines = ['# 导演锚点 / Motion-first reference pack', '', f'镜头：{plan.shot_id}',
             f'原片：{source_member(plan)}', f'原片 SHA-256：{plan.source.sha256}',
             '原片用途：运动、表演时序与镜头参考，不等于最终画面外观。', '', '## 保留',
             *['- ' + s for s in plan.preserve], '', '## 改变', *['- ' + s for s in plan.change], '',
             f'声音策略：{plan.audio_policy}', '', '## 时间锚点']
    for a in plan.anchors:
        lines += [f'### {a.id} · {a.source_time_ms} ms', f'原帧参考：{raster_member(a.frame, "frames")}',
                  f'实际像素：{a.frame.width}×{a.frame.height}',
                  f'目标图：{raster_member(a.guide, "guides") if a.guide else "未绑定，仍需外部制作"}',
                  '目标外观：' + (a.target if a.target else '尚未填写'), '']
    lines += ['## 使用边界',
              '完整原视频字节保留。PNG 是浏览器 SDR 参考，不是 HDR、EXR 或高位深母版。',
              '时间是相对原片的播放毫秒，不是已认证的逐帧编号；提交前在目标工具重新核对位置。',
              '每张目标图说明该时刻应呈现的外观，不把提示词写成从原样到新样的变形剧情。',
              '缺少目标图或文字的锚点仍会保存，不能据此认定已准备好生成。',
              '本地最多 16 个锚点，不代表任何模型支持 16 个输入。能力与实际入口须另行检查。',
              '本包是未批准的参考材料，不剪切、不上传、不生成、不授权付款或自动选片。',
              '哈希只验证内容一致，不证明身份、版权或生成质量。', '']
    return '\n'.join(lines)


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Check a bounded PNG chunk stream, not a full raster decompression."""
    if not 33 <= len(data) <= MAX_RASTER or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise AuthoringError('invalid or oversized PNG reference')
    offset, dimensions, has_data, ended = 8, None, False, False
    while offset + 12 <= len(data):
        n = struct.unpack('>I', data[offset:offset + 4])[0]
        end = offset + 12 + n
        if end > len(data):
            raise AuthoringError('truncated PNG chunk')
        kind, payload = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + n]
        crc = struct.unpack('>I', data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise AuthoringError('PNG chunk CRC mismatch')
        if dimensions is None:
            if kind != b'IHDR' or n != 13:
                raise AuthoringError('PNG must start with IHDR')
            dimensions = struct.unpack('>II', payload[:8])
            if not all(1 <= x <= 8192 for x in dimensions) or dimensions[0] * dimensions[1] > MAX_PIXELS:
                raise AuthoringError('PNG raster exceeds local bounds')
        elif kind == b'IHDR':
            raise AuthoringError('duplicate PNG header')
        if kind == b'IDAT':
            has_data = True
        if kind == b'IEND':
            if n != 0 or end != len(data):
                raise AuthoringError('PNG has an invalid ending or trailing content')
            ended = True
        offset = end
    if not ended or not has_data or offset != len(data):
        raise AuthoringError('PNG missing pixel data or ending')
    return dimensions


def _directory(path: Path, archive: zipfile.ZipFile) -> set[str]:
    """A closed stored ZIP, matching browser restrictions; never extract it."""
    infos = archive.infolist(); size = path.stat().st_size
    if not 5 <= len(infos) <= 36 or archive.comment:
        raise AuthoringError('director package requires 5 to 36 entries without comments')
    with path.open('rb') as stream:
        stream.seek(size - 22)
        end = struct.unpack('<4s4H2LH', stream.read(22))
        sig, disk, cd_disk, local_count, count, cd_size, cd_start, comment = end
        if (sig != b'PK\x05\x06' or disk or cd_disk or comment or local_count != count
                or count != len(infos) or cd_start + cd_size != size - 22 or cd_start != archive.start_dir):
            raise AuthoringError('director ZIP layout is not closed')
        names, cursor, total, cd_cursor = set(), 0, 0, cd_start
        for info in infos:
            safe_relative(info.filename)
            if (info.filename in names or info.is_dir() or info.compress_type != 0 or info.extra
                or info.comment or info.flag_bits & ~0x800 or info.file_size != info.compress_size
                or stat.S_ISLNK(info.external_attr >> 16) or info.header_offset != cursor):
                raise AuthoringError('unsafe, duplicate, compressed, prefixed or reordered ZIP member')
            if len(info.filename.encode('utf-8')) > 240:
                raise AuthoringError('director path too long')
            limit = MAX_JSON if info.filename in {'PLAN.json', 'BRIEF.md', 'MANIFEST.json'} else MAX_FILE
            if not 0 < info.file_size <= limit:
                raise AuthoringError('director member size out of range')
            stream.seek(cursor); raw = stream.read(30)
            if len(raw) != 30:
                raise AuthoringError('truncated local record')
            header = struct.unpack('<4s5H3L2H', raw)
            signature, _, flags, method, _, _, crc, packed, unpacked, n, extra = header
            name = stream.read(n).decode('utf-8' if flags & 0x800 else 'cp437')
            if (signature != b'PK\x03\x04' or flags != info.flag_bits or method or extra
                or name != info.filename or crc != info.CRC or packed != info.file_size or unpacked != packed):
                raise AuthoringError('director local and central records disagree')
            stream.seek(cd_cursor); central = stream.read(46)
            if len(central) != 46 or central[:4] != b'PK\x01\x02':
                raise AuthoringError('invalid central directory')
            n2, extra2, comment2, disk2 = struct.unpack_from('<4H', central, 28)
            if extra2 or comment2 or disk2:
                raise AuthoringError('unsupported central fields')
            cd_cursor += 46 + n2
            cursor += 30 + n + packed; total += unpacked; names.add(name)
        if cursor != cd_start or cd_cursor != size - 22 or total > MAX_TOTAL + 3 * MAX_JSON:
            raise AuthoringError('ZIP gaps, unknown records or excess total size')
        return names


def verify_director_bundle(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not 22 <= path.stat().st_size <= MAX_TOTAL + 3 * MAX_JSON:
        raise AuthoringError('director ZIP missing, linked or oversized')
    try:
        with zipfile.ZipFile(path) as archive:
            names = _directory(path, archive)
            if not {'PLAN.json', 'BRIEF.md', 'MANIFEST.json'} <= names:
                raise AuthoringError('director metadata missing')
            plan = DirectorPlan.model_validate(json_value(archive.read('PLAN.json')))
            media = media_records(plan)
            expected = set(media) | {'PLAN.json', 'BRIEF.md'}
            manifest = json_value(archive.read('MANIFEST.json'))
            if (not isinstance(manifest, dict) or set(manifest) != {'schema_id', 'files'}
                or manifest['schema_id'] != 'manju.director-manifest/v1'
                or not isinstance(manifest['files'], dict) or set(manifest['files']) != expected
                or names != expected | {'MANIFEST.json'}):
                raise AuthoringError('director inventory must be the exact referenced set')
            for name in expected:
                h = sha256()
                with archive.open(name) as stream:
                    while chunk := stream.read(1024 * 1024):
                        h.update(chunk)
                if h.hexdigest() != manifest['files'][name]:
                    raise AuthoringError('director member hash mismatch: ' + name)
                if name in media:
                    rec = media[name]
                    if h.hexdigest() != rec.sha256 or archive.getinfo(name).file_size != rec.bytes:
                        raise AuthoringError('media differs from the director plan: ' + name)
                    if isinstance(rec, Raster) and png_dimensions(archive.read(name)) != (rec.width, rec.height):
                        raise AuthoringError('actual PNG dimensions differ from the plan')
            if archive.read('BRIEF.md') != director_brief(plan).encode('utf-8'):
                raise AuthoringError('director brief is not derived from the plan')
            return {'ok': True, 'schema_id': 'manju.director-verification/v1',
                    'plan_sha256': plan.plan_sha256, 'source_sha256': plan.source.sha256,
                    'anchors': len(plan.anchors), 'guides': sum(a.guide is not None for a in plan.anchors),
                    'files_checked': len(expected), 'original_source_included': True,
                    'png_structure_checked': True, 'png_pixels_fully_decoded': False,
                    'source_metadata_independently_reprobed': False,
                    'capture_time_frame_accuracy_verified': False,
                    'execution_authorized': False, 'automatic_selection': False}
    except (zipfile.BadZipFile, UnicodeError, RecursionError, struct.error) as exc:
        raise AuthoringError(f'invalid director package: {exc}') from exc


def write_director_bundle(plan: DirectorPlan, media: dict[str, Path], output: Path) -> dict:
    """Package existing reference files without clobbering any old output."""
    plan = DirectorPlan.model_validate(plan.model_dump(mode='json'))
    records = media_records(plan)
    if set(records) != set(media):
        raise AuthoringError('provide exactly the referenced media paths')
    if output.exists() or output.is_symlink():
        raise FileExistsError('never overwrite a director package')
    for name, path in media.items():
        if path.is_symlink() or not path.is_file() or path.stat().st_size != records[name].bytes or file_digest(path) != records[name].sha256:
            raise AuthoringError('source file does not match the plan: ' + name)
    with tempfile.TemporaryDirectory(prefix='manju-director-', dir=output.parent) as tmp:
        staged = Path(tmp) / 'director.zip'
        payload = {'PLAN.json': canonical(plan) + b'\n', 'BRIEF.md': director_brief(plan).encode('utf-8')}
        manifest = {k: sha256(v).hexdigest() for k, v in payload.items()}
        manifest.update({k: v.sha256 for k, v in records.items()})
        with zipfile.ZipFile(staged, 'w', zipfile.ZIP_STORED) as archive:
            for name, value in payload.items():archive.writestr(name, value)
            for name in sorted(media):archive.write(media[name], name)
            archive.writestr('MANIFEST.json', canonical({'schema_id':'manju.director-manifest/v1','files':manifest}) + b'\n')
        result = verify_director_bundle(staged)
        owned = False
        try:
            with output.open('xb') as target:
                owned = True
                with staged.open('rb') as stream:shutil.copyfileobj(stream, target)
        except BaseException:
            if owned:output.unlink(missing_ok=True)
            raise
        return result
