"""Derived production-readiness gates for narrative projects.

No readiness state is persisted. Human approvals are narrow, self-verifying
events appended to the existing ``reports/verifications.jsonl`` log.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.hashing import hash_file, hash_text, hash_value
from ..core.yamlio import read_yaml
from ..qc.assurance import compute_assurance
from ..qc.production import accepted_observed_state, video_takes
from .exportstatus import VERIFICATIONS_FILE, VERIFICATIONS_LOCK
from .shotpackage import project_revision
from .stale import ShotState, evaluate_shot

SCHEMA = "manju.production-readiness/v1"
STAGES = (
    "LEGACY",
    "AUTHORING",
    "PROOF_SHOT_READY",
    "PROOF_SCENE_READY",
    "BULK_READY",
)
GATE_IDS = (
    "SCENE_CONTRACTS",
    "SHOT_CONTRACTS",
    "ANIMATIC_CURRENT",
    "ANIMATIC_APPROVED",
    "PROOF_SHOTS",
    "PROOF_SCENES",
    "BULK_READY",
)

ANIMATIC_APPROVAL_KIND = "animatic_approved"
PROOF_SCENE_APPROVAL_KIND = "proof_scene_approved"
PROXY_ONLY_PROVIDERS = frozenset({"caption_card", "comic_panel"})


class ReadinessError(RuntimeError):
    pass


def _detail(code: str, message: str, *, subject: str | None = None,
            severity: str = "error") -> dict[str, Any]:
    row: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if subject:
        row["subject"] = subject
    return row


def _gate(gate_id: str, details: list[dict[str, Any]]) -> dict[str, Any]:
    failed = any(item.get("severity", "error") == "error" for item in details)
    return {
        "id": gate_id,
        "state": "fail" if failed else "pass",
        "blocking": True,
        "details": details,
    }


def _bible_ids(project: Project, name: str) -> set[str]:
    path = project.root / "bible" / f"{name}.yaml"
    try:
        data = read_yaml(path) if path.exists() else {}
    except Exception:
        return set()
    return set(data) if isinstance(data, dict) else set()


def _scene_contract_gate(project: Project) -> tuple[dict[str, Any], dict[str, Any]]:
    details: list[dict[str, Any]] = []
    scenes: dict[str, Any] = {}
    character_ids = _bible_ids(project, "characters")
    prop_ids = _bible_ids(project, "props")
    location_ids = _bible_ids(project, "scenes")

    for scene_id in project.scene_contract_ids():
        subject = f"story/scenes/{scene_id}.yaml"
        try:
            scene = project.load_scene_contract(scene_id)
        except Exception as exc:
            details.append(_detail(
                "SCENE_CONTRACT_INVALID",
                f"cannot parse scene contract: {' '.join(str(exc).split())[:240]}",
                subject=subject,
            ))
            continue
        if scene.id != scene_id:
            details.append(_detail(
                "SCENE_ID_MISMATCH",
                f"scene id {scene.id!r} does not match filename {scene_id!r}",
                subject=subject,
            ))
            continue
        scenes[scene_id] = scene
        for field_name, value in (
            ("purpose", scene.purpose),
            ("entry_state", scene.entry_state),
            ("irreversible_change", scene.irreversible_change),
            ("exit_state", scene.exit_state),
        ):
            if not value:
                details.append(_detail(
                    "SCENE_CONTRACT_INCOMPLETE",
                    f"SceneContract.{field_name} is empty",
                    subject=subject,
                ))
        if scene.location_ref and scene.location_ref not in location_ids:
            details.append(_detail(
                "SCENE_LOCATION_UNRESOLVED",
                f"location_ref {scene.location_ref!r} is not in bible/scenes.yaml",
                subject=subject,
            ))
        for state_name, states in (
            ("entry_state", scene.entry_state), ("exit_state", scene.exit_state)
        ):
            for character_id, state in states.items():
                if character_id not in character_ids:
                    details.append(_detail(
                        "SCENE_CHARACTER_UNRESOLVED",
                        f"{state_name} character {character_id!r} is unresolved",
                        subject=subject,
                    ))
                for prop_id in state.props:
                    if prop_id not in prop_ids:
                        details.append(_detail(
                            "SCENE_PROP_UNRESOLVED",
                            f"{state_name} prop {prop_id!r} is unresolved",
                            subject=subject,
                        ))

    for shot_id in project.shot_ids(indexed_only=True):
        subject = f"shots/{shot_id}.yaml"
        try:
            shot = project.load_shot(shot_id)
        except Exception as exc:
            details.append(_detail(
                "INDEXED_SHOT_INVALID",
                f"indexed shot cannot be loaded: {' '.join(str(exc).split())[:240]}",
                subject=subject,
            ))
            continue
        if not shot.scene_id:
            details.append(_detail(
                "SHOT_SCENE_ID_MISSING", "indexed narrative shot has no scene_id",
                subject=subject,
            ))
        elif shot.scene_id not in scenes:
            details.append(_detail(
                "SHOT_SCENE_UNRESOLVED",
                f"scene_id {shot.scene_id!r} has no valid SceneContract",
                subject=subject,
            ))
    return _gate("SCENE_CONTRACTS", details), scenes


def _shot_contract_gate(project: Project) -> dict[str, Any]:
    from ..providers.refs import resolve_refs, unreadable_ref_message

    details: list[dict[str, Any]] = []
    prop_ids = _bible_ids(project, "props")
    bible = project.load_bible()
    for shot_id in project.shot_ids(indexed_only=True):
        subject = f"shots/{shot_id}.yaml"
        try:
            shot = project.load_shot(shot_id)
        except Exception as exc:
            details.append(_detail(
                "SHOT_CONTRACT_INVALID",
                f"shot cannot be loaded: {' '.join(str(exc).split())[:240]}",
                subject=subject,
            ))
            continue
        contract = shot.contract
        if contract is None:
            details.append(_detail(
                "SHOT_CONTRACT_MISSING", "paid narrative shot has no ShotContract",
                subject=subject,
            ))
            continue
        for field_name, value in (
            ("purpose", contract.purpose),
            ("action.main", shot.action.main.strip()),
            ("endpoint", contract.endpoint),
        ):
            if not value:
                details.append(_detail(
                    "SHOT_CONTRACT_INCOMPLETE", f"{field_name} is empty", subject=subject,
                ))
        if not contract.control.primary_uncertainty.strip():
            details.append(_detail(
                "PRIMARY_UNCERTAINTY_UNDECLARED",
                "primary uncertainty is not declared; continue only with explicit review",
                subject=subject,
                severity="warning",
            ))
        for prop_id in shot.props or []:
            if prop_id not in prop_ids:
                details.append(_detail(
                    "SHOT_PROP_UNRESOLVED", f"prop {prop_id!r} is unresolved",
                    subject=subject,
                ))
        try:
            refs = resolve_refs(project, shot, bible)
            ref_error = unreadable_ref_message(refs.items)
        except Exception as exc:
            ref_error = "reference resolution failed: " + " ".join(str(exc).split())[:200]
        if ref_error:
            details.append(_detail(
                "SHOT_REFERENCE_UNRESOLVED", ref_error, subject=subject,
            ))
    return _gate("SHOT_CONTRACTS", details)


def _compile_current_animatic(project: Project) -> tuple[Any | None, str | None, str | None]:
    """(timeline, content_key, error), with no derived writes."""
    try:
        from ..exporters.srt_ass import compile_project_ass
        from ..media.probe import probe_duration_ms
        from ..timeline.compiler import compile_timeline, gather_compile_input
        from .graph import animatic_content_key

        inputs = gather_compile_input(
            project, probe_duration_ms, include_unindexed=False,
            allow_missing_takes=True,
        )
        timeline = compile_timeline(inputs)
        rules = project.load_rules()
        ass_hash = None
        if rules.captions.enabled and timeline.tracks.captions:
            ass_hash = hash_text(compile_project_ass(project, timeline))
        key = animatic_content_key(project, timeline, ass_hash=ass_hash)
        return timeline, key, None
    except Exception as exc:
        return None, None, f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"


def _animatic_version(path: Path) -> int:
    try:
        return int(path.stem.split("_v", 1)[1])
    except (IndexError, ValueError):
        return -1


def _key_sidecar(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.with_suffix(".key.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def current_animatic(project: Project) -> dict[str, Any]:
    """Resolve the artifact whose sidecar matches today's one shared key."""
    _timeline, content_key, error = _compile_current_animatic(project)
    result: dict[str, Any] = {
        "current": False,
        "content_key": content_key,
        "path": None,
        "sha256": None,
        "bytes": None,
        "error": error,
    }
    if content_key is None:
        return result
    out_dir = project.root / "renders" / "animatic"
    matches: list[Path] = []
    for path in out_dir.glob("animatic_v*.mp4") if out_dir.is_dir() else []:
        sidecar = _key_sidecar(path)
        if (
            sidecar is not None
            and sidecar.get("final_key") == content_key
            and sidecar.get("target", "animatic") == "animatic"
            and path.is_file()
        ):
            try:
                if path.stat().st_size > 0:
                    matches.append(path)
            except OSError:
                pass
    if not matches:
        return result
    path = max(matches, key=lambda item: (_animatic_version(item), item.as_posix()))
    try:
        sha = hash_file(path)
        size = path.stat().st_size
    except OSError as exc:
        result["error"] = f"animatic unreadable: {exc}"
        return result
    result.update(
        current=True,
        path=project.relpath(path),
        sha256=sha,
        bytes=size,
        error=None,
    )
    return result


def _event_id(event: dict[str, Any]) -> str:
    return hash_value({key: value for key, value in event.items() if key != "event_id"})


def _verification_events(project: Project, kind: str) -> tuple[list[dict], str | None]:
    path = project.reports_dir / VERIFICATIONS_FILE
    if not path.exists():
        return [], None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [], f"verification log unreadable: {exc}"
    events: list[dict] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("kind") == kind:
            events.append(record)
    return events, None


def _valid_event(event: dict[str, Any]) -> bool:
    actor = event.get("actor") or {}
    event_id = event.get("event_id")
    return (
        actor.get("kind") == "human"
        and isinstance(event_id, str)
        and bool(event_id)
        and _event_id(event) == event_id
    )


def _append_verification(project: Project, event: dict[str, Any]) -> None:
    from ..core.events import EvidenceWriteError, append_jsonl_line

    project.reports_dir.mkdir(parents=True, exist_ok=True)
    try:
        append_jsonl_line(
            project.reports_dir,
            event,
            durable=True,
            required=True,
            file_name=VERIFICATIONS_FILE,
            lock_name=VERIFICATIONS_LOCK,
        )
    except EvidenceWriteError as exc:
        raise ReadinessError(
            f"durable verification append failed ({exc.reason}); approval not recorded"
        ) from exc


def _require_human(*, actor_kind: str, unattended: bool) -> None:
    if actor_kind != "human" or unattended:
        raise ReadinessError(
            "production approval is an explicit human action; unattended/AI callers "
            "and --yes may not approve"
        )


def current_animatic_approval(
    project: Project, current: dict[str, Any] | None = None
) -> dict[str, Any]:
    current = current or current_animatic(project)
    result = {"approved": False, "event_id": None, "reason": None}
    if not current.get("current"):
        result["reason"] = "no current animatic"
        return result
    events, error = _verification_events(project, ANIMATIC_APPROVAL_KIND)
    if error:
        result["reason"] = error
        return result
    for event in reversed(events):
        artifact = event.get("artifact") or {}
        if not _valid_event(event):
            continue
        if (
            artifact.get("path") == current.get("path")
            and artifact.get("sha256") == current.get("sha256")
            and artifact.get("bytes") == current.get("bytes")
            and event.get("animatic_content_key") == current.get("content_key")
        ):
            return {"approved": True, "event_id": event["event_id"], "reason": None}
    result["reason"] = "no human approval matches current path, bytes, and content key"
    return result


def approve_animatic(
    project: Project,
    animatic_ref: str,
    *,
    reason: str = "",
    actor_kind: str = "human",
    unattended: bool = False,
) -> dict[str, Any]:
    _require_human(actor_kind=actor_kind, unattended=unattended)
    current = current_animatic(project)
    if not current.get("current"):
        raise ReadinessError(
            "no current animatic exists; run `manju build --target animatic` first"
        )
    try:
        requested = project.resolve(animatic_ref)
        current_path = project.resolve(current["path"])
    except Exception as exc:
        raise ReadinessError(str(exc)) from exc
    if requested != current_path:
        raise ReadinessError(
            f"only the current animatic may be approved: {current['path']}"
        )
    event: dict[str, Any] = {
        "kind": ANIMATIC_APPROVAL_KIND,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact": {
            "path": current["path"],
            "sha256": current["sha256"],
            "bytes": current["bytes"],
        },
        "animatic_content_key": current["content_key"],
        "source_revision": project_revision(project),
        "actor": {"kind": "human"},
        "reason": reason or "",
    }
    event["event_id"] = _event_id(event)
    _append_verification(project, event)
    return {"approved": True, "event": event}


def proof_shot_status(project: Project, shot_id: str) -> dict[str, Any]:
    """Current selected-take eligibility used by both proof gates."""
    reasons: list[str] = []
    try:
        shot = project.load_shot(shot_id)
    except Exception as exc:
        return {"shot": shot_id, "ready": False, "reasons": [str(exc)]}

    selected = shot.status.selected_take
    take = project.get_take(shot_id, selected) if selected else None
    is_video = False
    if not selected or take is None or take.media_path is None:
        reasons.append("selected video take is missing")
    else:
        is_video = take.name in {item.name for item in video_takes(project, shot_id)}
        if not is_video:
            reasons.append("selected take is not video media")

    state = None
    media_sha = None
    media_path = None
    provider = None
    source_in_ms = None
    source_out_ms = None
    if take is not None:
        provider = take.sidecar.provider
        source_in_ms = take.sidecar.source_in_ms
        source_out_ms = take.sidecar.source_out_ms
        if provider in PROXY_ONLY_PROVIDERS:
            reasons.append(f"selected take provider {provider!r} is proxy-only")
        if take.error:
            reasons.append(f"selected take sidecar is invalid: {take.error}")
        if take.media_path is not None:
            media_path = project.relpath(take.media_path)
            try:
                media_sha = hash_file(take.media_path)
            except OSError:
                reasons.append("selected media bytes are unreadable")
    try:
        evaluated = evaluate_shot(project, shot)
        state = evaluated.state.value
        if evaluated.state not in (ShotState.FRESH, ShotState.MANUAL):
            reasons.append(f"selected take is not current ({evaluated.state.value})")
    except Exception as exc:
        reasons.append(f"current-take evaluation failed: {exc}")

    if shot.status.review_state != "approved":
        reasons.append("status.review_state is not human-approved")
    try:
        assurance = compute_assurance(project, shot_id)
    except Exception as exc:
        assurance = {"assurance_state": "unavailable", "expectation_digest": None}
        reasons.append(f"assurance unavailable: {exc}")
    assurance_state = assurance.get("assurance_state")
    if assurance_state != "accepted":
        reasons.append(f"assurance is {assurance_state!r}, not accepted")
    expectation_digest = assurance.get("expectation_digest")
    if not expectation_digest:
        reasons.append("current expectation digest is unavailable")
    # Eligibility is a derived view over the same evidence above. A registered
    # take that is not yet current/approved/assured remains a candidate; a
    # proxy provider can never qualify for picture lock even when its build
    # succeeds. Missing media is kept distinct so the three public labels do
    # not turn an absent take into a misleading candidate.
    if provider in PROXY_ONLY_PROVIDERS:
        eligibility = "proxy-only"
    elif is_video:
        eligibility = "final-eligible" if not reasons else "candidate"
    else:
        eligibility = "none"
    return {
        "shot": shot_id,
        "ready": not reasons,
        "state": eligibility,
        "picture_lock_eligible": eligibility == "final-eligible",
        "reasons": reasons,
        "selected_take": selected,
        "provider": provider,
        "media_path": media_path,
        "media_sha256": media_sha,
        "source_in_ms": source_in_ms,
        "source_out_ms": source_out_ms,
        "take_state": state,
        "review_state": shot.status.review_state,
        "assurance_state": assurance_state,
        "expectation_digest": expectation_digest,
    }


def media_eligibility(project: Project) -> dict[str, Any]:
    """Return the current selected-media eligibility for every indexed shot.

    This is deliberately a pure read model. It does not add approval state or
    write a readiness file; ``production_readiness`` and the takeover status
    both consume this same projection. ``picture_lock_eligible`` is true only
    when every indexed shot is ``final-eligible``.
    """
    rows: list[dict[str, Any]] = []
    for shot_id in project.shot_ids(indexed_only=True):
        try:
            rows.append(proof_shot_status(project, shot_id))
        except Exception as exc:
            rows.append({
                "shot": shot_id,
                "state": "none",
                "picture_lock_eligible": False,
                "ready": False,
                "reasons": [f"eligibility evaluation failed: {exc}"],
            })
    counts = {state: 0 for state in ("proxy-only", "candidate", "final-eligible", "none")}
    for row in rows:
        state = row.get("state", "none")
        counts[state if state in counts else "none"] += 1
    lock_reasons = [
        f"{row['shot']}: {row.get('state', 'none')}"
        for row in rows if not row.get("picture_lock_eligible")
    ]
    return {
        "shots": rows,
        "counts": counts,
        "picture_lock_eligible": bool(rows) and not lock_reasons,
        "picture_lock_reasons": lock_reasons,
    }


def _voice_binding(project: Project, shot_id: str) -> dict[str, Any] | None:
    voices = project.voice_takes(shot_id)
    if not voices:
        return None
    media, sidecar = voices[-1]
    timing = media.with_suffix(".timing.json")
    return {
        "path": project.relpath(media),
        "sha256": hash_file(media),
        "sidecar": sidecar.model_dump(exclude_none=True) if sidecar else None,
        "timing_sha256": hash_file(timing) if timing.exists() else None,
    }


def _scene_contract_payload(scene: Any) -> dict[str, Any]:
    return {
        "scene_id": scene.id,
        "location_ref": scene.location_ref,
        "time": scene.time,
        "entry_state": {
            key: value.model_dump() for key, value in scene.entry_state.items()
        },
        "irreversible_change": list(scene.irreversible_change),
        "exit_state": {
            key: value.model_dump() for key, value in scene.exit_state.items()
        },
        "carry_forward": list(scene.carry_forward),
    }


def proof_scene_digest(project: Project, scene_id: str) -> str:
    """Exact ordered media/trim/contract/audio state a human approves."""
    from ..qc.expectations import compile_expectations

    scene = project.load_scene_contract(scene_id)
    shot_ids = project.scene_shot_ids(scene_id)
    if not shot_ids:
        raise ReadinessError(f"proof scene {scene_id} has no indexed shots")
    rules = project.load_rules()
    transitions = {}
    for shot_id in shot_ids:
        transition = rules.transition_overrides.get(shot_id, rules.transition_default)
        transitions[shot_id] = transition.model_dump() if transition is not None else None

    shots: list[dict[str, Any]] = []
    for shot_id in shot_ids:
        shot = project.load_shot(shot_id)
        selected = shot.status.selected_take
        take = project.get_take(shot_id, selected) if selected else None
        media_sha = None
        media_path = None
        source_in_ms = None
        source_out_ms = None
        if take is not None:
            source_in_ms = take.sidecar.source_in_ms
            source_out_ms = take.sidecar.source_out_ms
            if take.media_path is not None:
                media_path = project.relpath(take.media_path)
                media_sha = hash_file(take.media_path)
        try:
            expectation_digest = compile_expectations(project, shot_id)["digest"]
        except Exception as exc:
            expectation_digest = f"unavailable:{type(exc).__name__}:{exc}"
        shots.append({
            "shot": shot_id,
            "selected_take": selected,
            "media_path": media_path,
            "media_sha256": media_sha,
            "source_in_ms": source_in_ms,
            "source_out_ms": source_out_ms,
            "contract_digest": (
                hash_value(shot.contract.model_dump()) if shot.contract is not None else None
            ),
            "expectation_digest": expectation_digest,
            "duration": shot.duration,
            "transition_out": transitions[shot_id],
            "dialogue": shot.dialogue.model_dump(),
            "sound": shot.contract.sound.model_dump() if shot.contract else None,
            "source_audio": shot.source_audio.model_dump(),
            "voice": _voice_binding(project, shot_id),
        })
    payload = {
        "scene": _scene_contract_payload(scene),
        "ordered_shots": shot_ids,
        "shots": shots,
        "timing_rules": rules.timing.model_dump(),
        "music": rules.music.model_dump(),
        "audio": rules.audio.model_dump(),
    }
    return hash_value(payload)


def current_proof_scene_approval(
    project: Project, scene_id: str, digest: str | None = None
) -> dict[str, Any]:
    try:
        digest = digest or proof_scene_digest(project, scene_id)
    except Exception as exc:
        return {"approved": False, "event_id": None, "reason": str(exc)}
    events, error = _verification_events(project, PROOF_SCENE_APPROVAL_KIND)
    if error:
        return {"approved": False, "event_id": None, "reason": error}
    for event in reversed(events):
        if (
            _valid_event(event)
            and event.get("scene_id") == scene_id
            and event.get("proof_scene_digest") == digest
        ):
            return {"approved": True, "event_id": event["event_id"], "reason": None}
    return {
        "approved": False,
        "event_id": None,
        "reason": "no human approval matches the current proof-scene digest",
    }


def _proof_scene_status(project: Project, scene_id: str, scene: Any) -> dict[str, Any]:
    reasons: list[str] = []
    if not scene.proof_scene:
        reasons.append("SceneContract is not marked proof_scene")
    shot_ids = project.scene_shot_ids(scene_id)
    if not shot_ids:
        reasons.append("proof scene has no indexed shots")
    shot_rows: list[dict[str, Any]] = []
    for shot_id in shot_ids:
        row = proof_shot_status(project, shot_id)
        shot_rows.append(row)
        if not row["ready"]:
            reasons.append(f"{shot_id}: " + "; ".join(row["reasons"]))
        try:
            observed = accepted_observed_state(project, shot_id)
        except Exception as exc:
            observed = {"status": "unavailable", "observed_endpoint": []}
            reasons.append(f"{shot_id}: observed endpoint unavailable: {exc}")
        if observed.get("status") != "current" or not observed.get("observed_endpoint"):
            reasons.append(f"{shot_id}: current observed endpoint is unavailable")
    digest = None
    try:
        digest = proof_scene_digest(project, scene_id) if shot_ids else None
    except Exception as exc:
        reasons.append(f"proof-scene digest unavailable: {exc}")
    approval = current_proof_scene_approval(project, scene_id, digest) if digest else {
        "approved": False, "event_id": None, "reason": "digest unavailable"
    }
    if not approval["approved"]:
        reasons.append(approval["reason"] or "proof scene is not approved")
    return {
        "scene": scene_id,
        "ready": not reasons,
        "reasons": reasons,
        "shots": shot_rows,
        "proof_scene_digest": digest,
        "approval": approval,
    }


def approve_proof_scene(
    project: Project,
    scene_id: str,
    *,
    reason: str = "",
    actor_kind: str = "human",
    unattended: bool = False,
) -> dict[str, Any]:
    _require_human(actor_kind=actor_kind, unattended=unattended)
    try:
        scene = project.load_scene_contract(scene_id)
    except Exception as exc:
        raise ReadinessError(str(exc)) from exc
    if not scene.proof_scene:
        raise ReadinessError(f"SceneContract {scene_id} is not marked proof_scene")
    shot_ids = project.scene_shot_ids(scene_id)
    if not shot_ids:
        raise ReadinessError(f"proof scene {scene_id} has no indexed shots")
    blockers: list[str] = []
    for shot_id in shot_ids:
        row = proof_shot_status(project, shot_id)
        if not row["ready"]:
            blockers.append(f"{shot_id}: " + "; ".join(row["reasons"]))
        try:
            observed = accepted_observed_state(project, shot_id)
        except Exception as exc:
            blockers.append(f"{shot_id}: observed endpoint unavailable: {exc}")
            continue
        if observed.get("status") != "current" or not observed.get("observed_endpoint"):
            blockers.append(f"{shot_id}: current observed endpoint is unavailable")
    if blockers:
        raise ReadinessError("proof-scene approval blocked: " + " | ".join(blockers))
    digest = proof_scene_digest(project, scene_id)
    event: dict[str, Any] = {
        "kind": PROOF_SCENE_APPROVAL_KIND,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scene_id": scene_id,
        "proof_scene_digest": digest,
        "source_revision": project_revision(project),
        "actor": {"kind": "human"},
        "reason": reason or "",
    }
    event["event_id"] = _event_id(event)
    _append_verification(project, event)
    return {"approved": True, "event": event}


def _release_findings(project: Project) -> list[dict[str, Any]]:
    rows = []
    for shot_id in project.shot_ids(indexed_only=True):
        try:
            shot = project.load_shot(shot_id)
            take = project.get_take(shot_id, shot.status.selected_take) \
                if shot.status.selected_take else None
        except Exception:
            continue
        if take is not None and take.sidecar.provider in PROXY_ONLY_PROVIDERS:
            rows.append({
                "shot": shot_id,
                "source_kind": take.sidecar.provider,
                "blocking_release": False,
            })
    if not rows:
        return []
    return [{
        "code": "PROXY_ONLY_MEDIA_IN_FINAL",
        "shots": rows,
        "blocking_release": False,
    }]


def production_readiness(
    project: Project, paid_shot_ids: list[str] | None = None
) -> dict[str, Any]:
    """Derive all gates and the paid-video allowance for the current stage."""
    eligibility = media_eligibility(project)
    if not project.narrative_opted_in:
        return {
            "schema": SCHEMA,
            "opted_in": False,
            "state": "LEGACY",
            "stage": "LEGACY",
            "gates": [],
            "failed_gates": [],
            "proof_shots": [],
            "proof_scenes": [],
            "allowed_paid_shot_ids": "all",
            "disallowed_paid_shot_ids": [],
            "bulk_paid_generation_allowed": True,
            "next_action": None,
            "release_findings": [],
            "eligibility": eligibility,
        }

    scene_gate, scenes = _scene_contract_gate(project)
    shot_gate = _shot_contract_gate(project)

    current = current_animatic(project)
    current_details = [] if current["current"] else [_detail(
        "ANIMATIC_NOT_CURRENT",
        current.get("error") or "no animatic matches the current content key",
        subject=current.get("path") or "renders/animatic",
    )]
    current_gate = _gate("ANIMATIC_CURRENT", current_details)

    animatic_approval = current_animatic_approval(project, current)
    approval_details = [] if animatic_approval["approved"] else [_detail(
        "ANIMATIC_NOT_APPROVED",
        animatic_approval.get("reason") or "current animatic has no human approval",
        subject=current.get("path") or "renders/animatic",
    )]
    approval_gate = _gate("ANIMATIC_APPROVED", approval_details)

    proof_shots: list[str] = []
    proof_shot_details: list[dict[str, Any]] = []
    for shot_id in project.shot_ids(indexed_only=True):
        try:
            shot = project.load_shot(shot_id)
        except Exception:
            continue
        if shot.contract is None or not shot.contract.proof_shot:
            continue
        proof_shots.append(shot_id)
        row = proof_shot_status(project, shot_id)
        if not row["ready"]:
            proof_shot_details.append(_detail(
                "PROOF_SHOT_NOT_READY", "; ".join(row["reasons"]), subject=shot_id,
            ))
    if not proof_shots:
        proof_shot_details.append(_detail(
            "PROOF_SHOT_UNDECLARED", "narrative project has no proof_shot",
            subject="shots",
        ))
    proof_shot_gate = _gate("PROOF_SHOTS", proof_shot_details)

    proof_scenes = sorted(
        scene_id for scene_id, scene in scenes.items() if scene.proof_scene
    )
    proof_scene_details: list[dict[str, Any]] = []
    proof_scene_rows: list[dict[str, Any]] = []
    for scene_id in proof_scenes:
        row = _proof_scene_status(project, scene_id, scenes[scene_id])
        proof_scene_rows.append(row)
        if not row["ready"]:
            proof_scene_details.append(_detail(
                "PROOF_SCENE_NOT_READY", "; ".join(row["reasons"]), subject=scene_id,
            ))
    if not proof_scenes:
        proof_scene_details.append(_detail(
            "PROOF_SCENE_UNDECLARED", "narrative project has no proof_scene",
            subject="story/scenes",
        ))
    proof_scene_gate = _gate("PROOF_SCENES", proof_scene_details)

    first_six = [
        scene_gate, shot_gate, current_gate, approval_gate,
        proof_shot_gate, proof_scene_gate,
    ]
    bulk_details = []
    for gate in first_six:
        if gate["state"] != "pass":
            bulk_details.append(_detail(
                "UPSTREAM_GATE_FAILED", f"{gate['id']} has not passed",
                subject=gate["id"],
            ))
    bulk_gate = _gate("BULK_READY", bulk_details)
    gates = first_six + [bulk_gate]

    if any(gate["state"] == "fail" for gate in first_six[:4]):
        stage = "AUTHORING"
    elif proof_shot_gate["state"] == "fail":
        stage = "PROOF_SHOT_READY"
    elif proof_scene_gate["state"] == "fail":
        stage = "PROOF_SCENE_READY"
    else:
        stage = "BULK_READY"

    indexed = project.shot_ids(indexed_only=True)
    if stage == "AUTHORING":
        allowed: list[str] = []
        next_action = "build and human-approve the current animatic"
    elif stage == "PROOF_SHOT_READY":
        allowed = list(proof_shots)
        next_action = "generate, review, and accept the declared proof shots"
    elif stage == "PROOF_SCENE_READY":
        allowed = list(dict.fromkeys(
            shot_id for scene_id in proof_scenes
            for shot_id in project.scene_shot_ids(scene_id)
        ))
        next_action = "complete and human-approve each current proof-scene digest"
    else:
        allowed = list(indexed)
        next_action = None

    planned = list(dict.fromkeys(paid_shot_ids if paid_shot_ids is not None else indexed))
    disallowed = [shot_id for shot_id in planned if shot_id not in set(allowed)]
    failed = [gate["id"] for gate in gates if gate["state"] == "fail"]
    return {
        "schema": SCHEMA,
        "opted_in": True,
        "state": "READY" if stage == "BULK_READY" else "BLOCKED",
        "stage": stage,
        "gates": gates,
        "failed_gates": failed,
        "animatic": current,
        "animatic_approval": animatic_approval,
        "proof_shots": proof_shots,
        "proof_scenes": proof_scenes,
        "proof_scene_status": proof_scene_rows,
        "allowed_paid_shot_ids": allowed,
        "disallowed_paid_shot_ids": disallowed,
        "bulk_paid_generation_allowed": stage == "BULK_READY",
        "next_action": next_action,
        "release_findings": _release_findings(project),
        "eligibility": eligibility,
    }
