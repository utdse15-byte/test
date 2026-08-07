"""Shared append-only verification evidence helpers.

All new review evidence rides the existing ``reports/verifications.jsonl``;
this module does not create a parallel review store.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from .events import append_jsonl_line

VERIFICATION_SCHEMA = "manju.verification.evidence/v1"
VERIFICATIONS_FILE = "verifications.jsonl"
VERIFICATIONS_LOCK = "verifications.lock"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_verification(project: Any, record: dict[str, Any], *, durable: bool = True) -> dict[str, Any]:
    row = {"schema": VERIFICATION_SCHEMA, "ts": _now(), **record}
    if not append_jsonl_line(project.reports_dir, row, durable=durable, required=True,
                             file_name=VERIFICATIONS_FILE, lock_name=VERIFICATIONS_LOCK):
        raise OSError("verification evidence was not written")
    return row


def read_verifications(project: Any, *, kind: str | None = None) -> list[dict[str, Any]]:
    path = project.reports_dir / VERIFICATIONS_FILE
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and (kind is None or row.get("kind") == kind):
                rows.append(row)
    except OSError:
        return []
    return rows


def latest_verification(project: Any, *, kind: str, predicate=None) -> dict[str, Any] | None:
    rows = read_verifications(project, kind=kind)
    if predicate is not None:
        rows = [row for row in rows if predicate(row)]
    return rows[-1] if rows else None


__all__ = ["VERIFICATION_SCHEMA", "append_verification", "read_verifications", "latest_verification"]
