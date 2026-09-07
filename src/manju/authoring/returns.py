"""Read-only technical inspection of returned candidates against a fixed request.

These local tolerances are triage policy, not vendor promises or creative QC.
A matching report never selects a take or authorizes a final promotion.
"""
from __future__ import annotations
from pathlib import Path
import re

from .core import AuthoringError, Request, digest, file_digest
from ..review.core import Candidate, candidate_from_file

DURATION_TOLERANCE_MS = 100
ASPECT_TOLERANCE_PERCENT = 2


def candidate_checks(request: Request, candidate: Candidate) -> list[dict]:
    request = Request.model_validate(request.model_dump(mode='json'))
    candidate = Candidate.model_validate(candidate.model_dump(mode='json'))
    checks = []
    expected = request.duration_s * 1000 if request.duration_s is not None else None
    actual = candidate.duration_ms
    state = ('not_requested' if expected is None else 'unknown' if actual is None
             else 'match' if abs(actual-expected) <= DURATION_TOLERANCE_MS else 'mismatch')
    checks.append({'id': 'duration_ms', 'state': state, 'expected': expected, 'actual': actual})
    ratio = re.fullmatch(r'([1-9][0-9]{0,3}):([1-9][0-9]{0,3})', request.aspect_ratio or '')
    w, h = candidate.width, candidate.height
    actual_ratio = f'{w}:{h}' if w and h else None
    if request.aspect_ratio in {None, '', 'adaptive'}:
        state = 'not_requested'
    elif not ratio or not w or not h:
        state = 'unknown'
    else:
        a, b = map(int, ratio.groups())
        state = 'match' if abs(w*b-h*a)*100 <= h*a*ASPECT_TOLERANCE_PERCENT else 'mismatch'
    checks.append({'id': 'dimension_ratio', 'state': state,
                   'expected': request.aspect_ratio, 'actual': actual_ratio})
    target = re.fullmatch(r'(360|480|540|720|768|1080|1440|2160)[pP]', request.resolution or '')
    expected_edge = int(target.group(1)) if target else None
    actual_edge = min(w,h) if w and h else None
    state = ('not_requested' if not request.resolution else
             'unknown' if expected_edge is None or actual_edge is None else
             'match' if actual_edge >= expected_edge else 'mismatch')
    checks.append({'id': 'minimum_short_edge_px', 'state': state,
                   'expected': expected_edge, 'actual': actual_edge})
    return checks


def return_report(request: Request, candidates: list[Candidate], *,
                  decoded: set[str] | None = None) -> dict:
    request = Request.model_validate(request.model_dump(mode='json'))
    candidates = [Candidate.model_validate(c.model_dump(mode='json')) for c in candidates]
    hashes = {c.sha256 for c in candidates}
    if not 1 <= len(candidates) <= 12 or len(hashes) != len(candidates):
        raise AuthoringError('inspect 1..12 distinct returned candidates')
    decoded = decoded or set()
    if not decoded <= hashes:
        raise AuthoringError('decode result is not bound to a returned candidate')
    items = []
    for candidate in candidates:
        checks = candidate_checks(request,candidate)
        states = {c['state'] for c in checks}
        status = ('needs_attention' if states & {'mismatch','unknown'} else
                  'not_requested' if states == {'not_requested'} else 'metadata_matches_requested_checks')
        items.append({'candidate':candidate.model_dump(mode='json'),'checks':checks,'status':status,
                      'full_video_decode':'passed' if candidate.sha256 in decoded else 'not_checked'})
    body = {'schema_id':'manju.return-preflight/v1','request':request.model_dump(mode='json'),
            'request_sha256':digest(request),
            'policy':{'duration_tolerance_ms':DURATION_TOLERANCE_MS,
                      'aspect_tolerance_percent':ASPECT_TOLERANCE_PERCENT,
                      'resolution_policy':'known_p_labels_minimum_short_edge'},
            'items':items,'automatic_approval':False,'automatic_selection':False,
            'picture_lock_authorized':False,'provider_origin_verified':False,
            'creative_quality_evaluated':False,'audio_evaluated':False,
            'geometry_note':'Dimension checks use reported pixel dimensions; SAR, rotation and crop are not certified.'}
    return {**body,'report_sha256':digest(body)}


def inspect_files(request: Request, paths: list[Path], *, decode: bool = False) -> dict:
    """Local protocols only; full decode is explicit, bounded and writes no video."""
    if not 1 <= len(paths) <= 12:
        raise AuthoringError('select 1..12 local candidate videos')
    candidates,decoded=[],set()
    for original in paths:
        path=original.resolve()
        candidate=candidate_from_file(path,local_only=True)
        if decode:
            from ..media.ffmpeg import run_ffmpeg, MediaError
            try:
                run_ffmpeg(['-nostdin','-xerror','-protocol_whitelist','file,pipe',
                            '-threads','1','-i',str(path),'-map','0:v:0','-an','-sn','-dn',
                            '-f','null','-'],timeout=120)
            except MediaError as exc:
                raise AuthoringError('returned video failed bounded full decoding; no report or media was changed') from exc
            decoded.add(candidate.sha256)
        if file_digest(path)!=candidate.sha256 or path.stat().st_size!=candidate.bytes:
            raise AuthoringError('returned candidate changed during inspection')
        candidates.append(candidate)
    return return_report(request,candidates,decoded=decoded)
