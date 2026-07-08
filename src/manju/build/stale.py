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
from ..core.spec import SPEC_VERSION, compute_spec_hash


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
    from ..core.spec import diff_spec_fields, spec_payload

    bible = bible if bible is not None else project.load_bible()
    # The hash a FRESHLY generated take would record today (always the latest
    # SPEC_VERSION, round W review #37/#16) — this is what a new/regenerated
    # take's sidecar gets (build/graph.py threads it onto GenerationRequest),
    # and what MISSING/NEEDS_SELECTION/BROKEN report as "the current spec".
    current_hash = compute_spec_hash(shot, bible, version=SPEC_VERSION, project_root=project.root)
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
    # Round W (issue #22): a sidecar that failed TakeSidecar's window
    # validation was degraded to a SAFE stand-in by Project.takes() (never a
    # crash), but a build must not silently proceed as if the (bogus) window
    # were fine — treat it as BROKEN, same as missing media, with the real
    # reason so `manju status`/`manju check` name it precisely.
    if take.error:
        return ShotBuildStatus(
            shot.id, ShotState.BROKEN, current_hash, selected, take,
            note=f"selected take '{selected}' sidecar 非法: {take.error}",
        )

    if take.sidecar.spec_hash == MANUAL_HASH:
        return ShotBuildStatus(shot.id, ShotState.MANUAL, current_hash, selected, take)

    # §4.3 conservatism (round W): judge an EXISTING take by the SPEC_VERSION
    # it was generated under, forever — a pre-round-W take (spec_version is
    # None -> read as 1) is compared against the v1 payload (no dialogue/
    # keyframes sensitivity, no mass restage); only a take recorded at v2+
    # is compared against the v2 payload.
    take_version = take.sidecar.spec_version or 1
    take_current_hash = compute_spec_hash(
        shot, bible, version=take_version, project_root=project.root
    )
    if take.sidecar.spec_hash == take_current_hash:
        return ShotBuildStatus(shot.id, ShotState.FRESH, current_hash, selected, take)

    # why-stale evidence: name the exact fields when the take carries the
    # spec snapshot it was generated from (older takes degrade to the hash)
    note = "shot spec changed after this take was generated"
    if take.sidecar.spec_snapshot:
        payload = spec_payload(shot, bible, version=take_version, project_root=project.root)
        changed = diff_spec_fields(take.sidecar.spec_snapshot, payload)
        if changed:
            shown = ", ".join(changed[:6]) + ("…" if len(changed) > 6 else "")
            note = f"spec changed: {shown}"
    return ShotBuildStatus(
        shot.id, ShotState.STALE, current_hash, selected, take,
        note=note,
    )


def evaluate_all(project: Project, *, indexed_only: bool = False) -> list[ShotBuildStatus]:
    """Every shot's build status, in :meth:`Project.shot_ids` order.

    ``indexed_only=True`` (round W, review #5) restricts the walk to
    ``shots/index.yaml`` order ONLY — a shot that exists on disk but was never
    added to the index is invisible to it. Default ``False`` preserves today's
    behaviour (index order + any on-disk stragglers appended) for the many
    status/report call sites; ``build/graph.py`` and
    ``timeline/compiler.gather_compile_input`` pass ``True`` so an unindexed
    draft shot can never enter a build's spend plan or the compiled timeline."""
    bible = project.load_bible()
    return [
        evaluate_shot(project, project.load_shot(sid), bible)
        for sid in project.shot_ids(indexed_only=indexed_only)
    ]
