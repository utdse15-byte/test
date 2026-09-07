"""Small offline authoring contracts, separate from execution providers.

Compatibility is based on a dated, partial evidence catalog. It is not a
quality ranking or proof a vendor will accept a request. Approval is a local
human declaration, not a cryptographic identity or authorization to spend.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import unicodedata
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Task = Literal['create', 'animate', 'bridge', 'reference', 'edit', 'extend', 'perform']
Role = Literal['first_frame', 'last_frame', 'subject_reference', 'reference_video',
               'reference_audio', 'source_video', 'performance_video']
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Identifier = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$')]
MAX_JSON = 2 * 1024 * 1024
MAX_ASSET = 2 * 1024 * 1024 * 1024
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.heic', '.heif'}
VIDEO_EXTS = {'.mp4', '.mov', '.webm', '.mkv'}
AUDIO_EXTS = {'.wav', '.mp3', '.m4a', '.aac', '.flac'}


class AuthoringError(ValueError):
    """A portable, actionable validation failure; never an execution retry."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True,
                              validate_assignment=True)


def canonical(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value: Any) -> str:
    return sha256(canonical(value)).hexdigest()


def load_json(path: Path) -> Any:
    if path.stat().st_size > MAX_JSON:
        raise AuthoringError('JSON exceeds the 2 MiB local safety limit')
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise AuthoringError(f'duplicate JSON key: {key}')
            out[key] = value
        return out
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda v: (_ for _ in ()).throw(
                          AuthoringError(f'invalid JSON number: {v}')))


def write_new_json(path: Path, value: Any) -> None:
    """Exclusive output; a new name is required for a revision."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if len(encoded) > MAX_JSON:
        raise AuthoringError('JSON exceeds the 2 MiB local safety limit; start a new revision/session')
    with path.open('xb') as stream:
        stream.write(encoded)


def safe_relative(name: str) -> str:
    p = PurePosixPath(name)
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                *(f'LPT{i}' for i in range(1, 10))}
    if (not name or p.is_absolute() or p.as_posix() != name or '\\' in name
        or any(ord(c) < 32 for c in name)
        or any(x in {'', '.', '..'} or x.endswith((' ', '.')) or
               any(c in x for c in ':*?"<>|') or x.split('.')[0].upper() in reserved
               for x in p.parts)):
        raise AuthoringError(f'unsafe portable path: {name!r}')
    return name


def member(root: Path, name: str) -> Path:
    safe_relative(name)
    p = root
    for part in PurePosixPath(name).parts:
        p = p / part
        info = p.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise AuthoringError('links and Windows reparse points are not allowed')
    if not p.is_file():
        raise AuthoringError(f'not a regular file: {name}')
    return p


def file_digest(path: Path) -> str:
    h = sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            h.update(block)
    return h.hexdigest()


class Asset(StrictModel):
    id: Identifier
    role: Role
    path: str = Field(min_length=1, max_length=240)
    sha256: Digest
    bytes: int = Field(ge=1, le=MAX_ASSET, strict=True)
    duration_ms: int | None = Field(default=None, ge=1, le=86_400_000, strict=True)
    origin_model: str | None = Field(default=None, max_length=120)

    @field_validator('path')
    @classmethod
    def portable(cls, value):
        return safe_relative(value)

    @model_validator(mode='after')
    def extension(self):
        suffix = PurePosixPath(self.path).suffix.lower()
        allowed = (IMAGE_EXTS if self.role in {'first_frame', 'last_frame', 'subject_reference'}
                   else AUDIO_EXTS if self.role == 'reference_audio' else VIDEO_EXTS)
        if suffix not in allowed:
            raise ValueError(f'{self.role} requires an appropriate media extension')
        return self


class Request(StrictModel):
    schema_id: Literal['manju.model-request/v1'] = 'manju.model-request/v1'
    shot_id: str = Field(min_length=1, max_length=120)
    task: Task
    prompt: str = Field(min_length=1, max_length=16000)
    duration_s: int | None = Field(default=None, ge=1, le=120, strict=True)
    resolution: str | None = Field(default=None, max_length=20)
    aspect_ratio: str | None = Field(default=None, max_length=20)
    assets: list[Asset] = Field(default_factory=list, max_length=32)
    preserve: list[str] = Field(default_factory=list, max_length=30)
    change: list[str] = Field(default_factory=list, max_length=30)
    stage: Literal['draft', 'final'] = 'draft'
    approved_draft_sha256: Digest | None = None

    @field_validator('preserve', 'change')
    @classmethod
    def lines(cls, values):
        if any(not s.strip() or len(s) > 1000 for s in values):
            raise ValueError('constraint lines must contain 1..1000 characters')
        return [s.strip() for s in values]

    @model_validator(mode='after')
    def coherent(self):
        ids = [a.id for a in self.assets]
        if len(set(ids)) != len(ids):
            raise ValueError('asset IDs must be unique')
        paths = {}
        for a in self.assets:
            key = unicodedata.normalize('NFC', a.path).casefold()
            identity = (a.path, a.sha256, a.bytes)
            if key in paths and paths[key] != identity:
                raise ValueError('conflicting or non-portable duplicate asset paths')
            paths[key] = identity
        norm = lambda s: unicodedata.normalize('NFKC', s).casefold().strip()
        if {norm(s) for s in self.preserve} & {norm(s) for s in self.change}:
            raise ValueError('the same constraint cannot be preserved and changed')
        if self.task == 'edit' and (not self.preserve or not self.change):
            raise ValueError('editing requires explicit preserve and change constraints')
        if self.stage == 'final' and not self.approved_draft_sha256:
            raise ValueError('final authoring requires an approved draft content hash')
        if self.stage == 'draft' and self.approved_draft_sha256:
            raise ValueError('draft requests cannot claim a final promotion')
        return self


class Mode(StrictModel):
    id: Identifier
    task: Task
    roles: dict[Role, tuple[int, int]] = Field(default_factory=dict)
    min_total_assets: int = Field(default=0, ge=0, le=32)
    durations: list[int] | None = None
    duration_range: tuple[int, int] | None = None
    resolutions: list[str] | None = None
    aspect_ratios: list[str] | None = None
    high_resolution_requires_s: int | None = None
    high_resolutions: list[str] = Field(default_factory=list)
    input_duration_max_ms: dict[Role, int] = Field(default_factory=dict)
    input_duration_min_ms: dict[Role, int] = Field(default_factory=dict)
    input_total_duration_max_ms: dict[Role, int] = Field(default_factory=dict)
    input_bytes_max: dict[Role, int] = Field(default_factory=dict)
    input_extensions: dict[Role, list[str]] = Field(default_factory=dict)
    required_origin_model: str | None = None
    last_frame_max_source_ms: int | None = None
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_bounds(self):
        if any(not (0 <= low <= high <= 32) for low, high in self.roles.values()):
            raise ValueError('invalid role count bounds')
        if self.duration_range and not (1 <= self.duration_range[0] <= self.duration_range[1] <= 120):
            raise ValueError('invalid duration range')
        if self.durations and any(type(x) is not int or x < 1 or x > 120 for x in self.durations):
            raise ValueError('invalid durations')
        if self.durations is not None and self.duration_range is not None:
            raise ValueError('choose duration list or range, not both')
        if self.min_total_assets > sum(high for _, high in self.roles.values()):
            raise ValueError('unreachable minimum asset count')
        return self


class Profile(StrictModel):
    id: Identifier
    label: str = Field(min_length=1, max_length=140)
    vendor: str = Field(min_length=1, max_length=80)
    model_id: str | None = Field(default=None, max_length=120)
    entry: Literal['api_documented_not_connected', 'web_app_only', 'product_workflow']
    status: Literal['authoring_only'] = 'authoring_only'
    claim_id: Identifier
    source_url: str
    checked_on: date
    review_after: date
    modes: list[Mode] = Field(min_length=1, max_length=20)
    notes: list[str] = Field(default_factory=list)

    @field_validator('source_url')
    @classmethod
    def safe_source(cls, value):
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query:
            raise ValueError('evidence source must be a credential-free HTTPS URL')
        return value

    @model_validator(mode='after')
    def ordering(self):
        if self.review_after < self.checked_on:
            raise ValueError('review date precedes verification date')
        if len({m.id for m in self.modes}) != len(self.modes):
            raise ValueError('duplicate mode ID')
        return self


class Catalog(StrictModel):
    schema_id: Literal['manju.model-catalog/v1'] = 'manju.model-catalog/v1'
    revision: str = Field(min_length=1, max_length=100)
    profiles: list[Profile] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len({p.id for p in self.profiles}) != len(self.profiles):
            raise ValueError('duplicate profile ID')
        if len({p.claim_id for p in self.profiles}) != len(self.profiles):
            raise ValueError('duplicate evidence claim ID')
        return self


def load_catalog(path: Path | None = None) -> Catalog:
    raw = load_json(path) if path else json.loads(
        files('manju.authoring').joinpath('data/catalog.json').read_text(encoding='utf-8'))
    return Catalog.model_validate(raw)


def mode_report(request: Request, profile: Profile, mode: Mode, *, today: date) -> dict:
    errors, warnings = [], list(profile.notes) + list(mode.notes)
    if request.task != mode.task:
        errors.append('task_not_supported_by_mode')
    counts = Counter(a.role for a in request.assets)
    for role in set(counts) | set(mode.roles):
        low, high = mode.roles.get(role, (0, 0))
        if not low <= counts[role] <= high:
            errors.append(f'role_count:{role}:expected_{low}_to_{high}')
    if len(request.assets) < mode.min_total_assets:
        errors.append('at_least_one_conditioning_asset_required')
    if request.duration_s is not None:
        if mode.durations is not None and request.duration_s not in mode.durations:
            errors.append('duration_not_supported')
        elif mode.duration_range and not mode.duration_range[0] <= request.duration_s <= mode.duration_range[1]:
            errors.append('duration_out_of_range')
        elif mode.durations is None and mode.duration_range is None:
            warnings.append('duration_limit_not_verified:confirm_with_vendor')
    else:
        warnings.append('duration_unspecified:confirm_with_vendor')
    for key in ('resolution', 'aspect_ratio'):
        value = getattr(request, key)
        supported = getattr(mode, 'resolutions' if key == 'resolution' else 'aspect_ratios')
        if value is None:
            warnings.append(f'{key}_unspecified:confirm_with_vendor')
        elif supported is None:
            warnings.append(f'{key}_limit_not_verified:confirm_with_vendor')
        elif value not in supported:
            errors.append(f'{key}_not_supported')
    if request.resolution in mode.high_resolutions and request.duration_s != mode.high_resolution_requires_s:
        errors.append(f'high_resolution_requires_{mode.high_resolution_requires_s}_seconds')
    for a in request.assets:
        if a.role in mode.input_bytes_max and a.bytes > mode.input_bytes_max[a.role]:
            errors.append(f'asset_bytes_exceeded:{a.id}')
        if a.role in mode.input_extensions and PurePosixPath(a.path).suffix.lower() not in mode.input_extensions[a.role]:
            errors.append(f'asset_extension_not_supported:{a.id}')
        if a.role in mode.input_duration_max_ms or a.role in mode.input_duration_min_ms:
            if a.duration_ms is None:
                errors.append(f'asset_duration_required:{a.id}')
            elif a.duration_ms > mode.input_duration_max_ms.get(a.role, 86_400_000) or a.duration_ms < mode.input_duration_min_ms.get(a.role, 1):
                errors.append(f'asset_duration_out_of_range:{a.id}')
        if a.role == 'source_video' and mode.required_origin_model and a.origin_model != mode.required_origin_model:
            errors.append(f'source_video_origin_must_be:{mode.required_origin_model}')
    for role, limit in mode.input_total_duration_max_ms.items():
        if sum(a.duration_ms or 0 for a in request.assets if a.role == role) > limit:
            errors.append(f'total_asset_duration_exceeded:{role}')
    if mode.last_frame_max_source_ms and counts['last_frame']:
        if any(a.role == 'source_video' and (a.duration_ms is None or a.duration_ms > mode.last_frame_max_source_ms) for a in request.assets):
            errors.append('last_frame_is_not_clip_end_for_this_input_length')
    if profile.checked_on > today:
        errors.append('evidence_date_in_future')
    if today > profile.review_after:
        warnings.append('evidence_review_overdue:reverify_before_external_submission')
    warnings.append('authoring_only:no_API_call_no_quality_or_price_guarantee')
    return {'profile_id': profile.id, 'mode_id': mode.id, 'compatible': not errors,
            'errors': sorted(set(errors)), 'warnings': sorted(set(warnings))}


def plan(request: Request, catalog: Catalog | None = None, *, today: date | None = None) -> dict:
    cat = catalog or load_catalog()
    day = today or datetime.now(timezone.utc).date()
    matches = [mode_report(request, p, m, today=day) for p in cat.profiles for m in p.modes
               if m.task == request.task]
    return {'schema_id': 'manju.model-plan/v1', 'request_sha256': digest(request),
            'catalog_sha256': digest(cat), 'evaluated_on': day.isoformat(),
            'selected_profile': None, 'ranked_by_quality': False, 'options': matches}


class Approval(StrictModel):
    schema_id: Literal['manju.model-approval/v1'] = 'manju.model-approval/v1'
    request_sha256: Digest
    catalog_sha256: Digest
    profile_id: Identifier
    mode_id: Identifier
    reviewer: str = Field(min_length=1, max_length=120)
    declared_at: datetime
    acknowledged_warnings: list[str]
    human_declared: Literal[True] = True
    external_execution_authorized: Literal[False] = False


def approve(request: Request, catalog: Catalog, profile_id: str, mode_id: str,
            *, reviewer: str, human_confirmed: bool, acknowledge_warnings: bool = False,
            now: datetime | None = None) -> Approval:
    if not human_confirmed:
        raise AuthoringError('explicit human confirmation is required')
    timestamp = now or datetime.now(timezone.utc)
    report = plan(request, catalog, today=timestamp.date())
    option = next((o for o in report['options'] if o['profile_id'] == profile_id and o['mode_id'] == mode_id), None)
    if option is None or not option['compatible']:
        raise AuthoringError(f'incompatible profile/mode: {option}')
    if option['warnings'] and not acknowledge_warnings:
        raise AuthoringError('review and explicitly acknowledge the displayed warnings')
    return Approval(request_sha256=digest(request), catalog_sha256=digest(catalog),
                    profile_id=profile_id, mode_id=mode_id, reviewer=reviewer,
                    declared_at=timestamp, acknowledged_warnings=option['warnings'])


def verify_approval(request: Request, catalog: Catalog, approval: Approval) -> None:
    if approval.request_sha256 != digest(request) or approval.catalog_sha256 != digest(catalog):
        raise AuthoringError('approval is stale: request, assets or catalog changed')
    expected = approve(request, catalog, approval.profile_id, approval.mode_id,
                       reviewer=approval.reviewer, human_confirmed=True,
                       acknowledge_warnings=True, now=approval.declared_at)
    if expected != approval:
        raise AuthoringError('approval does not match the evaluated constraints and warnings')


def brief(request: Request, profile: Profile, mode: Mode) -> str:
    rows = ['# Manju 模型交接说明', '', f'镜头：{request.shot_id}',
            f'创作任务：{request.task} / {request.stage}', f'作者适配档：{profile.label} / {mode.id}',
            f'使用入口：{profile.entry}；本项目未连接此商业服务。', '', '## 提示词', '', request.prompt,
            '', '## 保留项', *[f'- {s}' for s in request.preserve], '', '## 改变项',
            *[f'- {s}' for s in request.change], '', '## 目标参数',
            f'时长：{request.duration_s} 秒；分辨率：{request.resolution}；画幅：{request.aspect_ratio}',
            '', '## 素材绑定']
    rows += [f'- {a.id} | {a.role} | assets/{a.path} | SHA-256 {a.sha256}' for a in request.assets]
    if request.approved_draft_sha256:
        rows += ['', f'已批准草稿内容：{request.approved_draft_sha256}']
    rows += ['', '## 能力证据', profile.source_url,
             f'核验：{profile.checked_on}；复核期限：{profile.review_after}；声明：{profile.claim_id}',
             '', '未知、账户、地区、价格、可用性和媒体编码限制仍需提交前核验。',
             '人工批准只固定这份创作交接，不授权付费、不自动选片、不批准 Picture Lock。', '']
    return '\n'.join(rows)


def _members(root: Path) -> dict[str, str]:
    result, portable = {}, set()
    for p in sorted(root.rglob('*')):
        name = p.relative_to(root).as_posix()
        safe_relative(name)
        if p.is_symlink() or getattr(p.lstat(), 'st_file_attributes', 0) & 0x400:
            raise AuthoringError('bundle contains a link or reparse point')
        key = unicodedata.normalize('NFC', name).casefold()
        if key in portable:
            raise AuthoringError('bundle paths collide on a case-insensitive filesystem')
        portable.add(key)
        if p.is_file() and name != 'MANIFEST.json':
            result[name] = file_digest(p)
    return result


def write_bundle(request: Request, catalog: Catalog, approval: Approval,
                 asset_root: Path, output: Path, *, draft_review=None, draft_file: Path | None = None) -> Path:
    """Publish a new immutable directory. Sources are copied and rehashed.

    The old provider-handoff format and its verifier are not changed. This is
    an independent companion, never an in-place mutation of an old bundle.
    """
    verify_approval(request, catalog, approval)
    candidate = None
    if request.stage == 'final':
        from ..review.core import ReviewDocument, validate_promotion
        if draft_review is None or draft_file is None:
            raise AuthoringError('final handoff requires a reviewed draft document and its actual video')
        draft_review = ReviewDocument.model_validate(
            draft_review.model_dump(mode='json') if hasattr(draft_review, 'model_dump') else draft_review)
        candidate = validate_promotion(request, draft_review, draft_file)
    elif draft_review is not None or draft_file is not None:
        raise AuthoringError('draft evidence is only valid for final promotion')
    if output.exists():
        raise AuthoringError('output already exists; choose a new revision directory')
    if not output.parent.is_dir():
        raise AuthoringError('output parent must already exist')
    profile = next(p for p in catalog.profiles if p.id == approval.profile_id)
    mode = next(m for m in profile.modes if m.id == approval.mode_id)
    with tempfile.TemporaryDirectory(prefix='.manju-handoff-', dir=output.parent) as td:
        staging = Path(td) / 'bundle'
        staging.mkdir()
        for a in request.assets:
            src = member(asset_root, a.path)
            if src.stat().st_size != a.bytes:
                raise AuthoringError(f'asset size changed: {a.id}')
            dest = staging / 'assets' / a.path
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
            if dest.stat().st_size != a.bytes or file_digest(dest) != a.sha256:
                raise AuthoringError(f'asset changed or hash incorrect: {a.id}')
        write_new_json(staging / 'REQUEST.json', request)
        write_new_json(staging / 'CATALOG.json', catalog)
        write_new_json(staging / 'APPROVAL.json', approval)
        (staging / 'BRIEF.md').write_text(brief(request, profile, mode), encoding='utf-8')
        if candidate is not None:
            from ..review.core import draft_member, validate_promotion
            write_new_json(staging / 'DRAFT_REVIEW.json', draft_review)
            target = staging / draft_member(candidate)
            target.parent.mkdir()
            shutil.copyfile(draft_file, target)
            validate_promotion(request, draft_review, target)
        write_new_json(staging / 'MANIFEST.json', {
            'schema_id': 'manju.model-bundle/v2' if candidate is not None else 'manju.model-bundle/v1',
            'files': _members(staging)})
        verify_bundle(staging)
        # mkdir reserves a revision name even on POSIX, whose rename could
        # otherwise replace an existing empty directory. Roll back only our own reservation.
        output.mkdir()
        try:
            for p in staging.iterdir():
                shutil.move(str(p), output / p.name)
            verify_bundle(output)
        except BaseException:
            shutil.rmtree(output, ignore_errors=True)
            raise
    return output


def verify_bundle(root: Path, *, require_promotion: bool = False) -> dict:
    manifest = load_json(member(root, 'MANIFEST.json'))
    if set(manifest) != {'schema_id', 'files'} or manifest['schema_id'] not in {'manju.model-bundle/v1', 'manju.model-bundle/v2'}:
        raise AuthoringError('unknown manifest schema')
    if manifest['files'] != _members(root):
        raise AuthoringError('bundle files changed, missing or unexpected')
    req = Request.model_validate(load_json(root / 'REQUEST.json'))
    cat = Catalog.model_validate(load_json(root / 'CATALOG.json'))
    app = Approval.model_validate(load_json(root / 'APPROVAL.json'))
    verify_approval(req, cat, app)
    p = next(p for p in cat.profiles if p.id == app.profile_id)
    m = next(m for m in p.modes if m.id == app.mode_id)
    if (root / 'BRIEF.md').read_bytes() != brief(req, p, m).encode('utf-8'):
        raise AuthoringError('brief does not match the approved request')
    expected_files = {'REQUEST.json', 'CATALOG.json', 'APPROVAL.json', 'BRIEF.md'}
    for a in req.assets:
        name = 'assets/' + a.path
        path = member(root, name)
        if path.stat().st_size != a.bytes or file_digest(path) != a.sha256:
            raise AuthoringError(f'asset integrity failure: {a.id}')
        expected_files.add(name)
    promotion_verified = False
    if manifest['schema_id'] == 'manju.model-bundle/v2':
        from ..review.core import ReviewDocument, draft_member, validate_promotion
        if req.stage != 'final':
            raise AuthoringError('v2 proof bundles require a final request')
        review = ReviewDocument.model_validate(load_json(member(root, 'DRAFT_REVIEW.json')))
        candidate = next((c for c in review.session.candidates if c.sha256 == req.approved_draft_sha256), None)
        if candidate is None:
            raise AuthoringError('approved draft is not in the review')
        name = draft_member(candidate)
        validate_promotion(req, review, member(root, name))
        expected_files.update({'DRAFT_REVIEW.json', name})
        promotion_verified = True
    if require_promotion and not promotion_verified:
        raise AuthoringError('this bundle has no verified final-promotion proof')
    if set(manifest['files']) != expected_files:
        raise AuthoringError('unexpected bundle payload, even if rechecksummed')
    return {'ok': True, 'request_sha256': digest(req), 'profile_id': app.profile_id,
            'mode_id': app.mode_id, 'assets': len(req.assets),
            'schema_id': manifest['schema_id'], 'promotion_verified': promotion_verified,
            'review_scope': 'Included historical local declarations, not global revocation or identity verification.',
            'legacy_final_declaration_only': req.stage == 'final' and not promotion_verified,
            'external_execution_authorized': False,
            'note': 'Local consistency, not an external identity signature or vendor acceptance.'}
