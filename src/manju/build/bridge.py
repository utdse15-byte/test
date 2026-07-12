"""AI_IDE_19 WP5b + 14_21 CLOSEOUT C2 — generative transition bridge.

AI_IDE_16 pinned the generative bridge as *proposal-only, transport-0*; AI_IDE_19
executed it through the STANDARD provider path; the 14_21 closeout (contract §3)
closes the loop:

* the bridge REQUEST explicitly carries: prev/next endpoint frame REAL file
  refs + content hashes, duration, direction, description, all effective
  params, shot/source/spec identity and the provider profile/adapter identity
  (the last two through the standard submission-identity machinery). Hash-only
  input with no deliverable media is refused;
* ``request_digest`` covers the description and EVERY body-affecting field;
* ``spec_hash`` derives from the CURRENT Shot + bridge plan — the literal
  ``"bridge"`` default is gone and is rejected outright;
* endpoint files are re-hashed at execute time; a mismatch vs the plan refuses
  BEFORE dispatch (transport 0);
* execution funnels through the ONE provider-layer admission seam
  (``providers.base.dispatch_bridge`` → ``providers.qualification.bridge_admission``,
  floor PRODUCTION_READY + the single-use operator risk-acceptance path) and
  then rides the ordinary ``GenerationRequest`` → ``Provider.generate`` →
  ``register_take`` path, so it inherits AI_IDE_14's submission admission,
  budget and paid-recovery machinery (delete SQLite → poll-only resume →
  resubmit 0) with NO bridge-specific ledger;
* adoption consumes ONLY a CURRENT-bound accepted Assurance from the existing
  QC machinery (arbitrary ``{passed: true}`` dicts and stale packets/verdicts
  are refused) and yields a zero-write adoption PROPOSAL — it never writes
  accepted / selected_take / Shot / Bible;
* an UNAPPROVED bridge never reaches a final — :func:`assert_not_in_final`
  compares the ACTUAL source path/content hash, so renaming or copying the
  bridge media does not launder it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.hashing import hash_file, hash_value

BRIDGE_CAPABILITY = "generative_bridge"

# The qualification gate (``BRIDGE_NOT_QUALIFIED``) lives in the PROVIDER layer —
# ``providers.qualification.bridge_admission`` — consulted by the ONE dispatch
# seam ``providers.base.dispatch_bridge`` that :func:`execute_bridge` funnels
# through. It is NEVER imported here (the build-boundary guard: build/ must not
# read the qualification evidence). This module is the deterministic input
# binding + lineage half; CLI/MCP/GUI are thin wrappers over these functions.

# the ONE review type adoption consumes: the existing DR02 derived assurance.
ASSURANCE_SCHEMA = "manju.qc.assurance/v1"


class BridgeError(RuntimeError):
    """A bridge could not be planned, executed or adopted. Carries no secret."""


# ------------------------------------------------------------------ plan (inputs)


def _bind_endpoint(label: str, frame, declared_hash: str | None,
                   project_root) -> tuple[str | None, str | None]:
    """Resolve one endpoint frame: a REAL file ref (project-relative when
    possible) + its content hash. A declared hash that disagrees with the file
    bytes is refused at plan time — the plan must never be born inconsistent."""
    if frame is None:
        return None, declared_hash or None
    path = Path(frame)
    if not path.is_file():
        raise BridgeError(f"{label}: endpoint frame file not found: {path.name}")
    got = hash_file(path)
    if declared_hash and declared_hash != got:
        raise BridgeError(f"{label}: the declared hash does not match the frame "
                          f"file's actual bytes — refusing an inconsistent plan")
    ref = str(frame)
    if project_root is not None:
        try:
            ref = path.resolve().relative_to(Path(project_root).resolve()).as_posix()
        except (ValueError, OSError):
            ref = str(frame)
    return ref, got


def plan_bridge(*, duration_ms: int,
                prev_end_frame=None, next_start_frame=None,
                direction: str | None = None,
                description: str | None = None,
                params: dict | None = None,
                prev_end_frame_hash: str | None = None,
                next_start_frame_hash: str | None = None,
                project_root=None) -> dict[str, Any]:
    """Bind the bridge inputs (contract §3). BOTH endpoints are required — as
    REAL frame files (hashed here; ref stored project-relative) or, for a
    plan-only draft, as bare content hashes (which :func:`execute_bridge` will
    refuse to dispatch: hash-only is not deliverable input media). Returns a
    plan whose deterministic ``request_digest`` covers the description and
    EVERY body-affecting field (the idempotency key the standard poll-only
    resume keys on, so a restart never resubmits/double-charges)."""
    prev_ref, prev_hash = _bind_endpoint("prev_end_frame", prev_end_frame,
                                         prev_end_frame_hash, project_root)
    next_ref, next_hash = _bind_endpoint("next_start_frame", next_start_frame,
                                         next_start_frame_hash, project_root)
    if not prev_hash or not next_hash:
        raise BridgeError("a generative bridge must bind BOTH the previous shot's "
                          "end-frame and the next shot's start-frame (real file "
                          "or content hash)")
    if not duration_ms or int(duration_ms) <= 0:
        raise BridgeError("a bridge needs a positive duration_ms")
    plan = {
        "prev_end_frame": prev_ref,
        "next_start_frame": next_ref,
        "prev_end_frame_hash": prev_hash,
        "next_start_frame_hash": next_hash,
        "duration_ms": int(duration_ms),
        "direction": direction,
        "description": description,
        "params": params or {},
    }
    # closeout B01/B02: the digest covers EVERY body-affecting field — the
    # endpoint refs + bytes (hashes), duration, direction, DESCRIPTION and all
    # effective params. Reuses the standard hashing machinery (hash_value).
    plan["request_digest"] = hash_value({
        "prev_ref": prev_ref, "next_ref": next_ref,
        "p": prev_hash, "n": next_hash,
        "d": int(duration_ms), "dir": direction,
        "description": description, "params": params or {}})
    return plan


# ------------------------------------------------------------------ execute


def _resolve_frame(project: Any, ref: str) -> Path:
    p = Path(ref)
    return p if p.is_absolute() else Path(project.root) / p


def execute_bridge(project: Any, shot: str, plan: dict[str, Any], *, provider,
                   spec_hash: str | None = None, parent_take: str | None = None):
    """Execute the bridge through the STANDARD provider path — a
    ``GenerationRequest`` dispatched via the ONE provider-layer admission seam
    (``providers.base.dispatch_bridge``), which registers the result as an
    append-only take. Returns the produced :class:`TakeInfo`.

    Pre-dispatch refusals (all transport 0):
    * ``spec_hash="bridge"`` — the fixed placeholder is forbidden (B07); left
      ``None``, the spec identity derives from the CURRENT Shot + bridge plan;
    * a plan with no deliverable endpoint frame FILES (hash-only input);
    * endpoint files whose bytes no longer hash to the plan's recorded values
      (B04 — the plan and the delivered media must be the same thing).

    The endpoint frames ride the request's standard params→refs surface, so
    the provider receives the ACTUAL media and their content hashes enter the
    submission identity (B03)."""
    from ..core.spec import SPEC_VERSION, compute_spec_hash
    from ..providers.base import GenerationRequest, dispatch_bridge

    if spec_hash == "bridge":
        raise BridgeError('spec_hash="bridge" is forbidden (closeout B07): the '
                          "spec identity must derive from the current Shot + "
                          "bridge plan, never a fixed placeholder")

    prev_ref = plan.get("prev_end_frame")
    next_ref = plan.get("next_start_frame")
    if not prev_ref or not next_ref:
        raise BridgeError("bridge plan carries no deliverable endpoint frame "
                          "files — hash-only input media cannot be delivered "
                          "to a provider (contract §3)")
    for label, ref, want in (("prev_end_frame", prev_ref,
                              plan.get("prev_end_frame_hash")),
                             ("next_start_frame", next_ref,
                              plan.get("next_start_frame_hash"))):
        path = _resolve_frame(project, ref)
        if not path.is_file():
            raise BridgeError(f"{label}: endpoint frame missing on disk: {ref}")
        got = hash_file(path)
        if want and got != want:
            raise BridgeError(
                f"{label}: endpoint file bytes no longer match the plan's "
                f"recorded hash — refusing before dispatch (transport 0); "
                f"re-plan against the current frames")

    shot_spec = project.load_shot(shot)
    bible = project.load_bible()
    if spec_hash is None:
        # closeout B07: derived from the CURRENT Shot + the bridge plan content.
        spec_hash = hash_value({
            "shot_spec_hash": compute_spec_hash(shot_spec, bible,
                                                version=SPEC_VERSION,
                                                project_root=project.root),
            "bridge_plan_digest": plan.get("request_digest")})

    # the endpoint frames enter the UNIFIED effective request: the plan rides
    # params (lineage) and the frame refs ride the standard params→refs tier,
    # so provider delivery + the submission identity both see the real media.
    params: dict[str, Any] = {"bridge": dict(plan),
                              "images": [str(prev_ref), str(next_ref)]}
    req = GenerationRequest(
        project=project, shot=shot_spec, bible=bible,
        spec_hash=spec_hash, duration_ms=int(plan.get("duration_ms") or 0),
        candidates=1, params=params, redo_of=parent_take)
    takes = dispatch_bridge(project, provider, req)
    if not takes:
        raise BridgeError("bridge provider returned no take")
    return takes[0]


def bridge_lineage(plan: dict[str, Any], take, *, parent_take: str | None = None,
                   shot: str | None = None) -> dict[str, Any]:
    """The append-only lineage record binding the bridge INPUT frame hashes to its
    OUTPUT media hash, and flagging it a transition CANDIDATE (adopted=False until
    a human explicitly adopts a reviewed bridge)."""
    output_hash = None
    media = getattr(take, "media_path", None)
    if media is not None and media.exists():
        output_hash = hash_file(media)
    return {
        "prev_end_frame_hash": plan.get("prev_end_frame_hash"),
        "next_start_frame_hash": plan.get("next_start_frame_hash"),
        "duration_ms": plan.get("duration_ms"),
        "direction": plan.get("direction"),
        "request_digest": plan.get("request_digest"),
        "shot": shot,
        "take": getattr(take, "name", None),
        "spec_hash": getattr(getattr(take, "sidecar", None), "spec_hash", None),
        "output_media_hash": output_hash,
        "redo_of": parent_take,
        "is_transition_candidate": True,
        "adopted": False,
    }


# ------------------------------------------------------------------ review + adopt


def bridge_review_requirement(lineage: dict[str, Any]) -> dict[str, Any]:
    """A bridge candidate must pass a CURRENT-bound review (AI_IDE_15) tied to the
    exact bridge output bytes before adoption — the continuity check that stops a
    bridge from papering over a real failure (§8)."""
    return {
        "required": True,
        "status": "PENDING_REVIEW",
        "bound_hash": lineage.get("output_media_hash"),
        "reason": "a generative bridge is a candidate — it needs a current-bound "
                  "continuity review before it can enter the timeline",
    }


def adopt_bridge(lineage: dict[str, Any], *, review: dict | None,
                 media_path=None) -> dict[str, Any]:
    """Adopt a reviewed bridge (the explicit human step, §8) — closeout B08–B10.

    ``review`` must be a CURRENT-bound **accepted Assurance** from the existing
    QC machinery (schema ``manju.qc.assurance/v1``): type-checked, state
    ``accepted``, its evidence bound to the EXACT bridge output bytes, and its
    spec binding matching this bridge's spec identity. Anything else refuses:
    arbitrary ``{passed: true, bound_hash: ...}`` dicts, bare verdict records
    (observation evidence is never acceptance authority — acceptance is the
    pure assurance function's output), stale assurances, and reviews bound to
    older bytes or another spec. When ``media_path`` is given the bytes on disk
    are RE-hashed now — adoption binds to exact current bytes, not a memory.

    Returns the adopted lineage carrying a zero-write adoption ``proposal``
    (source patch via the existing checked-write path) — adoption itself never
    writes accepted / selected_take / Shot / Bible."""
    if not isinstance(review, dict) or review.get("schema") != ASSURANCE_SCHEMA:
        raise BridgeError(
            "bridge adoption consumes a current-bound accepted Assurance "
            f"(schema {ASSURANCE_SCHEMA}) from the existing QC machinery — "
            "arbitrary review dicts / bare verdict records are refused")
    state = review.get("assurance_state")
    if state != "accepted":
        raise BridgeError(
            f"assurance_state={state!r} cannot adopt a bridge — only a CURRENT "
            f"accepted assurance may (stale/rejected/unknown all refuse)")
    bound = (review.get("evidence") or {}).get("media_sha256")
    if not bound or bound != lineage.get("output_media_hash"):
        raise BridgeError("the assurance is bound to different bytes than the "
                          "current bridge output — re-review the current take")
    lineage_spec = lineage.get("spec_hash")
    review_spec = review.get("spec_hash")
    if lineage_spec and review_spec and lineage_spec != review_spec:
        raise BridgeError("the assurance is bound to a different spec than this "
                          "bridge — a stale spec binding cannot adopt")
    if media_path is not None:
        p = Path(media_path)
        if not p.is_file() or hash_file(p) != lineage.get("output_media_hash"):
            raise BridgeError("the bridge output bytes on disk no longer match "
                              "the reviewed hash — adoption requires the exact "
                              "current bytes")
    proposal = {
        "kind": "bridge_adoption_proposal",
        "shot": lineage.get("shot"),
        "take": lineage.get("take"),
        "output_media_hash": lineage.get("output_media_hash"),
        "source_patch": {
            "field": "status.selected_take",
            "proposed_value": lineage.get("take"),
            "apply_via": "manju select (the existing checked write path)",
        },
        "assurance_evidence": {
            "packet_id": (review.get("evidence") or {}).get("packet_id"),
            "media_sha256": bound,
        },
        "do_not_execute_automatically": True,
    }
    return {**lineage, "adopted": True, "review": review, "proposal": proposal}


# ------------------------------------------------------------------ never-in-final


def assert_not_in_final(lineage: dict[str, Any], compiled_sources: list[str], *,
                        adopted: bool, source_paths: list | None = None
                        ) -> list[dict[str, Any]]:
    """Manifest-side guard (§11 row 16, closeout B11): an UN-adopted bridge must
    not appear among the compiled final's ACTUAL inputs. Checks the take name in
    ``compiled_sources`` AND — decisively — the content hash of every real input
    file in ``source_paths`` against the bridge output hash, so renaming the
    take or copying its media elsewhere is still caught. Returns blocking
    diagnostics; an adopted bridge is legitimate."""
    if adopted:
        return []
    take = lineage.get("take")
    out: list[dict[str, Any]] = []
    if take is not None and take in (compiled_sources or []):
        out.append({
            "code": "UNAPPROVED_BRIDGE_IN_FINAL", "severity": "blocking",
            "detail": f"generative bridge take {take!r} is in the final but was "
                      f"never adopted through a current-bound review",
            "take": take,
        })
    want = lineage.get("output_media_hash")
    for entry in (source_paths or []):
        try:
            path = Path(entry)
            if want and path.is_file() and hash_file(path) == want:
                out.append({
                    "code": "UNAPPROVED_BRIDGE_IN_FINAL", "severity": "blocking",
                    "detail": f"final input {path.name!r} carries the exact "
                              f"content hash of un-adopted generative bridge "
                              f"take {take!r} — renaming/copying does not "
                              f"launder an unapproved bridge",
                    "take": take,
                    "source": path.name,
                })
        except OSError:
            continue
    return out
