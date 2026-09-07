"""Read-only, field-level capability revision review; never an execution policy.

The source URLs and dates are declarations carried by an authoring catalog.
They are not fetched or authenticated. Request/v1 hashes remain unchanged.
"""
from __future__ import annotations
from datetime import date, datetime, timezone
from .core import Catalog, Request, digest, plan

SCHEMA = 'manju.catalog-review/v1'


def _fields(before: dict, after: dict, prefix: str) -> list[dict]:
    changes = []
    for key in sorted(before.keys() | after.keys()):
        a, b = before.get(key), after.get(key)
        if a != b:
            changes.append({'path': prefix + key, 'before': a, 'after': b})
    return changes


def review_catalog(before: Catalog, after: Catalog, request: Request | None = None,
                   *, today: date | None = None) -> dict:
    """Compare immutable declarations and their effect on this exact request."""
    day = today or datetime.now(timezone.utc).date()
    old = {p.id: p.model_dump(mode='json') for p in before.profiles}
    new = {p.id: p.model_dump(mode='json') for p in after.profiles}
    changes = []
    for pid in sorted(old.keys() | new.keys()):
        a, b = old.get(pid), new.get(pid)
        prefix = f'profiles[{pid}]'
        if a is None or b is None:
            changes.append({'path': prefix, 'before': a, 'after': b})
            continue
        changes.extend(_fields({k:v for k,v in a.items() if k != 'modes'},
                               {k:v for k,v in b.items() if k != 'modes'}, prefix + '.'))
        am, bm = {m['id']:m for m in a['modes']}, {m['id']:m for m in b['modes']}
        for mid in sorted(am.keys() | bm.keys()):
            x, y = am.get(mid), bm.get(mid)
            key = prefix + f'.modes[{mid}]'
            if x is None or y is None:
                changes.append({'path':key, 'before':x, 'after':y})
            else:
                changes.extend(_fields(x, y, key + '.'))
    impact = None
    if request is not None:
        rows = [{(r['profile_id'],r['mode_id']):r for r in plan(request,c,today=day)['options']}
                for c in (before,after)]
        impact = [{'profile_id':key[0], 'mode_id':key[1],
                   'before':rows[0].get(key), 'after':rows[1].get(key)}
                  for key in sorted(rows[0].keys() | rows[1].keys())
                  if rows[0].get(key) != rows[1].get(key)]
    freshness = [{'profile_id':p.id, 'checked_on':p.checked_on.isoformat(),
                  'review_after':p.review_after.isoformat(),
                  'state':'future_dated' if p.checked_on>day else 'review_overdue' if day>p.review_after else 'within_review_window'}
                 for p in sorted(after.profiles,key=lambda p:p.id)]
    return {'schema_id':SCHEMA, 'evaluated_on':day.isoformat(),
            'before_revision':before.revision, 'after_revision':after.revision,
            'before_sha256':digest(before), 'after_sha256':digest(after),
            'request_sha256':digest(request) if request is not None else None,
            'field_changes':changes, 'task_impact':impact, 'evidence_freshness':freshness,
            'remote_sources_verified':False, 'automatic_apply':False,
            'automatic_approval':False, 'automatic_model_selection':False}
