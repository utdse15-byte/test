"""Staleness determination (§4.3) — deliberately conservative.

The system fills gaps automatically but never overturns a decision either
party has already made:

    no takes at all                  -> MISSING     (build generates)
    selected take, spec_hash equal   -> FRESH       (cache hit, skip)
    selected take, spec changed      -> STALE       (flag only; --regen-stale
                                                     or `manju redo` to remake)
    selected take is a manual import -> MANUAL      (never auto-invalidated)
    takes exist, none selected       -> NEEDS_SELECTION
    selected take file missing       -> BROKEN      (check error)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..core.container import Project, TakeInfo
from ..core.hashing import MANUAL_HASH
from ..core.models import ShotSpec
from ..core.spec import compute_spec_hash


class ShotState(str, Enum):
    MISSING = "missing"
    FRESH = "fresh"
    STALE = "stale"
    MANUAL = "manual"
    NEEDS_SELECTION = "needs_selection"
    BROKEN = "broken"


@dataclass
class ShotBuildStatus:
    shot_id: str
    state: ShotState
    spec_hash: str
    selected_take: str | None
    take: TakeInfo | None
    note: str = ""

    @property
    def usable(self) -> bool:
        """Can this shot go on the timeline as-is? Stale is still usable —
        the human's selection stands until they explicitly regenerate."""
        return self.state in (ShotState.FRESH, ShotState.STALE, ShotState.MANUAL)


def evaluate_shot(project: Project, shot: ShotSpec,
                  bible: dict[str, dict[str, Any]] | None = None) -> ShotBuildStatus:
    current_hash = compute_spec_hash(shot, bible if bible is not None else project.load_bible())
    takes = project.takes(shot.id)
    selected = shot.status.selected_take

    if not takes and not selected:
        return ShotBuildStatus(shot.id, ShotState.MISSING, current_hash, None, None)

    if not selected:
        return ShotBuildStatus(
            shot.id, ShotState.NEEDS_SELECTION, current_hash, None, None,
            note=f"{len(takes)} take(s) available, none selected",
        )

    take = next((t for t in takes if t.name == selected), None)
    if take is None or take.media_path is None:
        return ShotBuildStatus(
            shot.id, ShotState.BROKEN, current_hash, selected, take,
            note=f"selected take '{selected}' has no media on disk",
        )

    if take.sidecar.spec_hash == MANUAL_HASH:
        return ShotBuildStatus(shot.id, ShotState.MANUAL, current_hash, selected, take)

    if take.sidecar.spec_hash == current_hash:
        return ShotBuildStatus(shot.id, ShotState.FRESH, current_hash, selected, take)

    return ShotBuildStatus(
        shot.id, ShotState.STALE, current_hash, selected, take,
        note="shot spec changed after this take was generated",
    )


def evaluate_all(project: Project) -> list[ShotBuildStatus]:
    bible = project.load_bible()
    return [evaluate_shot(project, project.load_shot(sid), bible) for sid in project.shot_ids()]
