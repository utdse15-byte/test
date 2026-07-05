"""Manual import provider (§6, §8.4) — the human on-ramp.

Human-supplied media is registered as a take exactly like generated media, so
it is fully equal downstream. Two invariants (§3, §4.3):

- imports are sacred: registration always COPIES (never moves/consumes) files.
- manual takes carry ``spec_hash = MANUAL_HASH`` and are NEVER auto-invalidated
  when the shot spec changes — the human's choice stands until they redo it.
"""

from __future__ import annotations

from pathlib import Path

from ..core.container import Project, TakeInfo
from ..core.hashing import MANUAL_HASH
from ..core.models import TakeSidecar
from .base import (
    FailureKind,
    GenerationRequest,
    NeedsHumanInput,
    Provider,
    ProviderFailure,
    probe_media,
)


class ManualImportProvider(Provider):
    id = "manual_import"
    kind = "local"

    def generate(self, req: GenerationRequest) -> list[TakeInfo]:
        file = req.params.get("file")
        if not file:
            raise NeedsHumanInput(
                f"waiting for human-imported media for shot {req.shot.id}"
            )
        return [register_manual_take(req.project, req.shot.id, Path(file))]


def register_manual_take(project: Project, shot_id: str, file: Path) -> TakeInfo:
    """Register human-supplied media as a manual take.

    The sidecar records ``provider="manual_import"``, ``spec_hash=MANUAL_HASH``
    (never auto-invalidated, §4.3) and the original ``source`` path. The file is
    copied via ``register_take`` — imports are never consumed (§3)."""
    file = Path(file)
    if not file.exists():
        raise ProviderFailure(
            FailureKind.invalid, f"manual import file not found: {file}"
        )
    sidecar = TakeSidecar(
        provider="manual_import",
        spec_hash=MANUAL_HASH,
        params={},
        source=str(file),
        probe=probe_media(file),
    )
    return project.register_take(shot_id, file, sidecar)  # copies; imports sacred
