"""Value-hash locks (§5).

`manju lock S002 dialogue.text` stores hash(current value) in the shot file.
`manju check` (and every build) re-hashes the current value and compares.
A mismatch is a hard error — even if an agent edits the file directly, the
engine refuses to build. Unlock exists only on the interactive CLI; it is
never exposed over CLI/GUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .hashing import get_by_path, hash_value


@dataclass
class LockViolation:
    file: str
    path: str
    reason: str  # "changed" | "missing" | "unsealed"

    def __str__(self) -> str:
        return f"{self.file}: locked field '{self.path}' {self.reason}"


def seal_lock(data: dict[str, Any], dotted_path: str) -> str:
    """Return the value hash for a field about to be locked.

    Raises KeyError if the path does not exist — you cannot lock nothing.
    """
    return hash_value(get_by_path(data, dotted_path))


def verify_locks(data: dict[str, Any], locked: dict[str, str], file_label: str) -> list[LockViolation]:
    """Compare each lock record against the current value. Empty hash means the
    lock was hand-written as a bare list and never sealed via `manju lock`."""
    violations: list[LockViolation] = []
    for path, recorded in locked.items():
        if not recorded:
            violations.append(
                LockViolation(file_label, path, "unsealed (run `manju lock` to seal it)")
            )
            continue
        try:
            current = hash_value(get_by_path(data, path))
        except (KeyError, IndexError, ValueError):
            violations.append(LockViolation(file_label, path, "missing"))
            continue
        if current != recorded:
            violations.append(LockViolation(file_label, path, "changed"))
    return violations
