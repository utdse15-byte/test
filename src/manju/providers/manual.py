"""Manual import provider (§6, §8.4) — the human on-ramp.

Human-supplied media is registered as a take exactly like generated media, so
it is fully equal downstream. Two invariants (§3, §4.3):

- imports are sacred: registration always COPIES (never moves/consumes) files.
- manual takes carry ``spec_hash = MANUAL_HASH`` and are NEVER auto-invalidated
  when the shot spec changes — the human's choice stands until they redo it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..core.container import Project, TakeInfo
from ..core.hashing import MANUAL_HASH, hash_file
from ..core.models import TakeSidecar
from .handoff_lineage import VerifiedHandoffLineage, coerce_handoff_lineage
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


def _roundtrip_findings(project: Project, shot_id: str, probe) -> list[dict]:
    """Advisory duration/aspect checks for an external manual return."""
    findings: list[dict] = []
    shot = project.load_shot(shot_id)
    config = project.load_config()
    if probe.width and probe.height and config.width and config.height:
        actual = probe.width / probe.height
        expected = config.width / config.height
        if abs(actual - expected) / expected > 0.01:
            findings.append({
                "code": "external_return_aspect_mismatch",
                "level": "warning",
                "expected": f"{config.width}x{config.height}",
                "actual": f"{probe.width}x{probe.height}",
            })
    if (
        isinstance(shot.duration, (int, float))
        and probe.duration_ms is not None
        and abs(probe.duration_ms - int(float(shot.duration) * 1000)) > 500
    ):
        findings.append({
            "code": "external_return_duration_mismatch",
            "level": "warning",
            "expected_ms": int(float(shot.duration) * 1000),
            "actual_ms": probe.duration_ms,
        })
    return findings


def register_manual_take(
    project: Project,
    shot_id: str,
    file: Path,
    *,
    handoff: VerifiedHandoffLineage | Mapping[str, Any] | None = None,
) -> TakeInfo:
    """Register human-supplied media as a manual take.

    The sidecar records ``provider="manual_import"``, ``spec_hash=MANUAL_HASH``
    (never auto-invalidated, §4.3) and the original ``source`` path. The file is
    copied via ``register_take`` — imports are never consumed (§3)."""
    file = Path(file)
    if not file.exists():
        raise ProviderFailure(
            FailureKind.invalid, f"manual import file not found: {file}"
        )
    probe = probe_media(file)
    params = {}
    qc = {}
    if handoff is not None:
        lineage = coerce_handoff_lineage(handoff)
        if lineage.shot != shot_id:
            raise ProviderFailure(
                FailureKind.invalid,
                f"handoff shot {lineage.shot!r} does not match {shot_id!r}",
            )
        params = lineage.sidecar_params()
        params["returned_media_sha256"] = hash_file(file)
        qc = {"external_manual_roundtrip": _roundtrip_findings(
            project, shot_id, probe
        )}
    sidecar = TakeSidecar(
        provider="manual_import",
        spec_hash=MANUAL_HASH,
        params=params,
        source=str(file),
        probe=probe,
        qc=qc,
    )
    take = project.register_take(shot_id, file, sidecar)  # copies; imports sacred
    # TRISURFACE F-02: select --file and ingest video takes both land here —
    # record the live ledger row the rebuild would otherwise be the first to
    # derive (tasks/spend saw manual takes only after rebuild-index). Mirrors
    # rebuild()'s sidecar derivation; best-effort, the ledger is disposable
    # (§3) and must never fail a registration.
    try:
        from ..runtime.state import RuntimeState

        with RuntimeState(project.root) as state:
            state.record_run(
                shot=shot_id,
                provider="manual_import",
                status="succeeded",
                params=None,
                cost=0.0,
                currency=None,
                take=take.name,
            )
    except Exception:
        pass
    return take
