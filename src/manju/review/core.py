"""Immutable, hash-bound reviews and draft-to-final provenance.

Blind labels hide filenames in the presentation. They do not constitute a
scientific double-blind study. Records are local declarations, not signatures.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import secrets
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from ..authoring.core import (StrictModel, Request, Digest, AuthoringError, digest,
                               file_digest, safe_relative, MAX_ASSET, VIDEO_EXTS)

Criterion = Literal['identity', 'motion', 'camera', 'continuity', 'audio', 'artifacts', 'prompt_adherence']
Verdict = Literal['approve_draft', 'revise', 'reject']
Score = Annotated[int, Field(ge=1, le=5, strict=True)]


def timestamp() -> str:
    # Whole UTC seconds form one stable representation shared with JavaScript.
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Candidate(StrictModel):
    sha256: Digest
    bytes: int = Field(ge=1, le=MAX_ASSET, strict=True)
    filename: str = Field(min_length=1, max_length=240)
    duration_ms: int | None = Field(default=None, ge=1, le=86_400_000, strict=True)
    width: int | None = Field(default=None, ge=1, le=32768, strict=True)
    height: int | None = Field(default=None, ge=1, le=32768, strict=True)
    media_check: Literal['hash_only', 'browser_metadata', 'ffprobe'] = 'hash_only'

    @field_validator('filename')
    @classmethod
    def portable_name(cls, value):
        safe_relative(value)
        if '/' in value or Path(value).suffix.lower() not in VIDEO_EXTS:
            raise ValueError('a candidate must have a portable video basename')
        return value

    @model_validator(mode='after')
    def measured_fields(self):
        if self.media_check != 'hash_only' and (self.duration_ms is None or self.width is None or self.height is None):
            raise ValueError('a measured video requires duration, width and height')
        return self


def _without_hash(model: StrictModel, field: str) -> dict:
    data = model.model_dump(mode='json')
    data.pop(field)
    return data


class Session(StrictModel):
    schema_id: Literal['manju.review-session/v1'] = 'manju.review-session/v1'
    request: Request
    request_sha256: Digest
    candidates: list[Candidate] = Field(min_length=1, max_length=12)
    nonce: str = Field(pattern=r'^[a-f0-9]{32}$')
    blind_order: list[Digest] = Field(min_length=1, max_length=12)
    session_sha256: Digest
    no_automatic_selection: Literal[True] = True

    @model_validator(mode='after')
    def identities(self):
        if self.request.stage != 'draft':
            raise ValueError('review sessions require a draft request')
        if digest(self.request) != self.request_sha256:
            raise ValueError('review request hash mismatch')
        hashes = [c.sha256 for c in self.candidates]
        if len(set(hashes)) != len(hashes):
            raise ValueError('identical video bytes are not separate candidates')
        expected = sorted(hashes, key=lambda h: (digest({'nonce': self.nonce, 'sha256': h}), h))
        if expected != self.blind_order:
            raise ValueError('blind order does not match the session seed')
        if digest(_without_hash(self, 'session_sha256')) != self.session_sha256:
            raise ValueError('review session content changed')
        return self


class Decision(StrictModel):
    schema_id: Literal['manju.review-decision/v1'] = 'manju.review-decision/v1'
    session_sha256: Digest
    request_sha256: Digest
    candidate_sha256: Digest
    sequence: int = Field(ge=1, le=1000, strict=True)
    previous_decision_sha256: Digest | None
    verdict: Verdict
    reviewer: str = Field(min_length=1, max_length=120)
    notes: str = Field(min_length=1, max_length=6000)
    criteria: dict[Criterion, Score | None] = Field(default_factory=dict)
    declared_at: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
    human_declared: Literal[True] = True
    picture_lock_authorized: Literal[False] = False
    decision_sha256: Digest

    @model_validator(mode='after')
    def checksum(self):
        datetime.strptime(self.declared_at, '%Y-%m-%dT%H:%M:%SZ')
        if digest(_without_hash(self, 'decision_sha256')) != self.decision_sha256:
            raise ValueError('decision content changed')
        return self


class ReviewDocument(StrictModel):
    schema_id: Literal['manju.review-document/v1'] = 'manju.review-document/v1'
    session: Session
    decisions: list[Decision] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def chain(self):
        candidates = {c.sha256 for c in self.session.candidates}
        previous = None
        for i, decision in enumerate(self.decisions, 1):
            if (decision.sequence != i or decision.previous_decision_sha256 != previous
                or decision.session_sha256 != self.session.session_sha256
                or decision.request_sha256 != self.session.request_sha256
                or decision.candidate_sha256 not in candidates):
                raise ValueError('decision sequence, predecessor or session binding is invalid')
            previous = decision.decision_sha256
        return self


def create_session(request: Request, candidates: list[Candidate], *, nonce: str | None = None) -> ReviewDocument:
    seed = nonce if nonce is not None else secrets.token_hex(16)
    order = sorted((c.sha256 for c in candidates), key=lambda h: (digest({'nonce': seed, 'sha256': h}), h))
    body = {'schema_id': 'manju.review-session/v1', 'request': request.model_dump(mode='json'),
            'request_sha256': digest(request), 'candidates': [c.model_dump(mode='json') for c in candidates],
            'nonce': seed, 'blind_order': order, 'no_automatic_selection': True}
    body['session_sha256'] = digest(body)
    return ReviewDocument(session=Session.model_validate(body))


def candidate_from_file(path: Path, *, probe: bool = True, local_only: bool = False) -> Candidate:
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTS:
        raise AuthoringError('select an existing local video file')
    before = path.stat()
    if not 0 < before.st_size <= MAX_ASSET:
        raise AuthoringError('candidate is empty or exceeds the local 2 GiB limit')
    kwargs = {}
    if probe:
        from ..media.probe import probe as read_probe
        from ..media.ffmpeg import MediaError
        try:
            info = read_probe(path, timeout=15.0, **({"local_only": True} if local_only else {}))
        except MediaError as exc:
            raise AuthoringError(f'candidate metadata probe failed: {exc}') from exc
        if not info.width or not info.height or not info.duration_ms or info.duration_ms < 1:
            raise AuthoringError('candidate has no measurable video stream')
        kwargs = {'duration_ms': info.duration_ms, 'width': info.width, 'height': info.height,
                  'media_check': 'ffprobe'}
    content_hash = file_digest(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise AuthoringError('candidate changed while reading')
    return Candidate(sha256=content_hash, bytes=before.st_size, filename=path.name, **kwargs)


def verify_candidate(candidate: Candidate, path: Path) -> None:
    if not path.is_file() or path.stat().st_size != candidate.bytes or file_digest(path) != candidate.sha256:
        raise AuthoringError('candidate bytes differ from the reviewed content')


def record_decision(document: ReviewDocument, candidate_sha256: str, candidate_file: Path, *,
                    verdict: str, reviewer: str, notes: str, human_confirmed: bool,
                    criteria: dict | None = None, declared_at: str | None = None) -> ReviewDocument:
    # Revalidate mutable Pydantic children so callers cannot bypass invariants by
    # mutating a nested list/dict after the original construction.
    document = ReviewDocument.model_validate(document.model_dump(mode='json'))
    if not human_confirmed:
        raise AuthoringError('explicit human review confirmation is required')
    candidate = next((c for c in document.session.candidates if c.sha256 == candidate_sha256), None)
    if candidate is None:
        raise AuthoringError('candidate is not part of this review session')
    verify_candidate(candidate, candidate_file)
    body = {'schema_id': 'manju.review-decision/v1', 'session_sha256': document.session.session_sha256,
            'request_sha256': document.session.request_sha256, 'candidate_sha256': candidate_sha256,
            'sequence': len(document.decisions) + 1,
            'previous_decision_sha256': document.decisions[-1].decision_sha256 if document.decisions else None,
            'verdict': verdict, 'reviewer': reviewer.strip(), 'notes': notes.strip(),
            'criteria': criteria or {}, 'declared_at': declared_at or timestamp(),
            'human_declared': True, 'picture_lock_authorized': False}
    body['decision_sha256'] = digest(body)
    updated = document.model_dump(mode='json')
    updated['decisions'].append(Decision.model_validate(body).model_dump(mode='json'))
    return ReviewDocument.model_validate(updated)


def latest_decision(document: ReviewDocument, candidate_sha256: str) -> Decision | None:
    return next((d for d in reversed(document.decisions) if d.candidate_sha256 == candidate_sha256), None)


def validate_promotion(request: Request, document: ReviewDocument, draft_file: Path) -> Candidate:
    document = ReviewDocument.model_validate(document.model_dump(mode='json'))
    if request.stage != 'final' or not request.approved_draft_sha256:
        raise AuthoringError('not a final promotion request')
    candidate = next((c for c in document.session.candidates if c.sha256 == request.approved_draft_sha256), None)
    decision = latest_decision(document, request.approved_draft_sha256)
    if not candidate or not decision or decision.verdict != 'approve_draft':
        raise AuthoringError('the latest review decision does not approve this exact draft')
    allowed = {'stage', 'approved_draft_sha256', 'resolution'}
    actual = request.model_dump(mode='json')
    original = document.session.request.model_dump(mode='json')
    if {k: v for k, v in actual.items() if k not in allowed} != {k: v for k, v in original.items() if k not in allowed}:
        raise AuthoringError('creative intent, timing, aspect ratio or input assets changed; review a new draft')
    verify_candidate(candidate, draft_file)
    return candidate


def promote(document: ReviewDocument, candidate_sha256: str, draft_file: Path, *,
            human_confirmed: bool, resolution: str | None = None) -> Request:
    if not human_confirmed:
        raise AuthoringError('explicit final-promotion confirmation is required')
    body = document.session.request.model_dump(mode='json')
    body.update(stage='final', approved_draft_sha256=candidate_sha256)
    if resolution is not None:
        body['resolution'] = resolution
    request = Request.model_validate(body)
    validate_promotion(request, document, draft_file)
    return request


def draft_member(candidate: Candidate) -> str:
    return 'approved-draft/' + candidate.sha256 + Path(candidate.filename).suffix.lower()


def report(document: ReviewDocument, *, reveal: bool = False) -> str:
    document = ReviewDocument.model_validate(document.model_dump(mode='json'))
    labels = {h: chr(65 + i) for i, h in enumerate(document.session.blind_order)}
    names = {c.sha256: c.filename for c in document.session.candidates}
    rows = ['# Manju 候选审片记录', '', f'镜头：{document.session.request.shot_id}',
            f'场次内容哈希：{document.session.session_sha256}', '',
            '本记录不自动选择候选、不批准 Picture Lock。隐藏文件名辅助比较，不构成科学双盲试验。', '']
    for h in document.session.blind_order:
        decision = latest_decision(document, h)
        rows += [f'## 候选 {labels[h]}', f'内容 SHA-256：{h}',
                 f'文件：{names[h] if reveal else "未揭示来源"}',
                 f'最新决定：{decision.verdict if decision else "尚未评审"}']
        if decision:
            rows += [f'确认人：{decision.reviewer}；时间：{decision.declared_at}', decision.notes,
                     '人工评分（不自动汇总排名）：' + str(decision.criteria)]
        rows.append('')
    rows += ['## 历史', f'保留 {len(document.decisions)} 条按顺序追加的决定。旧声明不会被新的决定抹去。', '']
    return '\n'.join(rows)
