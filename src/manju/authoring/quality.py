"""Dated editorial shortlist, separate from immutable v1 authoring contracts.

A shortlist is not a measured per-shot quality guarantee. Exact profile hashes
prevent imported revisions or same-name endpoints from inheriting old evidence.
No API calls, provider fallbacks, cost ranking, or automatic selection occur.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from importlib.resources import files
import json
from typing import Literal

from pydantic import Field, model_validator

from .core import (Catalog, Digest, Identifier, Request, StrictModel, Task,
                   digest, load_catalog, plan)


class ShortlistEntry(StrictModel):
    profile_id: Identifier
    profile_sha256: Digest
    tasks: list[Task] = Field(min_length=1, max_length=7)
    basis: Literal['independent_model_snapshot', 'independent_endpoint_snapshot',
                   'official_successor_family_evidence']
    note: str = Field(min_length=1, max_length=2000)


class Shortlist(StrictModel):
    schema_id: Literal['manju.quality-shortlist/v1'] = 'manju.quality-shortlist/v1'
    revision: str = Field(min_length=1, max_length=100)
    checked_on: date
    review_after: date
    selection_policy: Literal['quality_first_no_automatic_fallback']
    entries: list[ShortlistEntry] = Field(min_length=1, max_length=30)
    sources: list[str] = Field(min_length=1, max_length=30)
    limits: list[str] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def coherent(self):
        if self.review_after < self.checked_on:
            raise ValueError('shortlist review date precedes its evidence date')
        if len({e.profile_id for e in self.entries}) != len(self.entries):
            raise ValueError('duplicate shortlist profile')
        if any(len(set(e.tasks)) != len(e.tasks) for e in self.entries):
            raise ValueError('duplicate shortlist task')
        if any(not s.startswith('https://') for s in self.sources):
            raise ValueError('shortlist sources must use HTTPS')
        return self


def load_shortlist() -> Shortlist:
    return Shortlist.model_validate(json.loads(files('manju.authoring').joinpath(
        'data/quality.json').read_text(encoding='utf-8')))


def quality_plan(request: Request, catalog: Catalog | None = None, *,
                 today: date | None = None, shortlist: Shortlist | None = None) -> dict:
    """Return eligible paths and explicit exclusions; never pick a winner.

    An overdue shortlist keeps its recorded identities but adds an acknowledgement
    warning, instead of silently replacing them. Future-dated evidence is blocked.
    Compatibility still comes from the unchanged v1 capability planner.
    """
    cat, policy = catalog or load_catalog(), shortlist or load_shortlist()
    day = today or datetime.now(timezone.utc).date()
    base = plan(request, cat, today=day)
    by_id = {p.id: p for p in cat.profiles}
    entries = {e.profile_id: e for e in policy.entries}
    eligible, excluded = [], []
    for option in base['options']:
        pid = option['profile_id']
        entry = entries.get(pid)
        reason = None
        if entry is None:
            reason = 'not_in_quality_shortlist'
        elif request.task not in entry.tasks:
            reason = 'task_not_in_quality_shortlist'
        elif digest(by_id[pid]) != entry.profile_sha256:
            reason = 'profile_changed_requires_quality_recheck'
        elif day < policy.checked_on:
            reason = 'quality_evidence_date_in_future'
        if reason:
            excluded.append({'profile_id': pid, 'mode_id': option['mode_id'], 'reason': reason})
        else:
            warnings = list(option['warnings'])
            warnings.append('quality_shortlist_is_not_per_shot_proof')
            if day > policy.review_after:
                warnings.append('quality_evidence_review_overdue')
            if entry.basis == 'official_successor_family_evidence':
                warnings.append('leaderboard_score_not_verified_for_exact_successor')
            eligible.append({**option, 'warnings': sorted(set(warnings)),
                             'quality_basis': entry.basis, 'quality_note': entry.note})
    return {'schema_id': 'manju.quality-plan/v1',
            'request_sha256': base['request_sha256'], 'catalog_sha256': base['catalog_sha256'],
            'shortlist_sha256': digest(policy), 'evaluated_on': day.isoformat(),
            'selected_profile': None, 'automatic_fallback': False, 'cost_ranked': False,
            'ranked_by_quality': False, 'options': eligible, 'excluded': excluded,
            'limits': policy.limits}
