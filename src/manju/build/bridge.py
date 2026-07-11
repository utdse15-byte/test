"""AI_IDE_19 WP5b — generative transition bridge (executes through the paid path).

AI_IDE_16 pinned the generative bridge as *proposal-only, transport-0*. THIS batch
executes it — but only through the STANDARD provider path and behind the same
guards every paid generation obeys (contract §8, addendum ruling 6):

* the bridge INPUT is explicitly bound — the previous shot's END frame hash, the
  next shot's START frame hash, a duration and a direction/motion constraint;
  a missing endpoint is REFUSED (never a floating bridge);
* execution rides the ordinary ``GenerationRequest`` → ``Provider.generate`` →
  ``register_take`` path, so it inherits AI_IDE_14's admission, budget and
  paid-recovery machinery (delete SQLite → poll-only resume → resubmit 0). With
  no real account here, a scripted provider stands in and the real path is
  qualification-gated (``BRIDGE_NOT_QUALIFIED``);
* the result is only a TRANSITION CANDIDATE (append-only, with input+output hash
  lineage). It requires a CURRENT-bound review (AI_IDE_15) bound to the exact
  bridge output bytes before it may be adopted — a bridge may never be used to
  paper over a real continuity failure (§8);
* an UNAPPROVED bridge never reaches a final (§11 row 16) — :func:`assert_not_in_final`
  is the manifest-side guard, complementing the 16 gate.
"""

from __future__ import annotations

from typing import Any

from ..core.hashing import hash_file, hash_value

BRIDGE_CAPABILITY = "generative_bridge"

# The qualification gate (``BRIDGE_NOT_QUALIFIED``) lives in the PROVIDER layer —
# ``providers.qualification.bridge_admission`` — and is called from cli.py, NEVER
# imported here (the build-boundary guard: build/ must not read the qualification
# report). This module is the deterministic execution + lineage half.


class BridgeError(RuntimeError):
    """A bridge could not be planned, executed or adopted. Carries no secret."""


# ------------------------------------------------------------------ plan (inputs)


def plan_bridge(*, prev_end_frame_hash: str, next_start_frame_hash: str,
                duration_ms: int, direction: str | None = None,
                description: str | None = None,
                params: dict | None = None) -> dict[str, Any]:
    """Bind the bridge inputs (contract §8). BOTH endpoint frame hashes are
    required — a bridge with no anchored start or end frame is refused. Returns a
    plan with a deterministic ``request_digest`` (the idempotency key the standard
    poll-only resume keys on, so a restart never resubmits/double-charges)."""
    if not prev_end_frame_hash or not next_start_frame_hash:
        raise BridgeError("a generative bridge must bind BOTH the previous shot's "
                          "end-frame hash and the next shot's start-frame hash")
    if not duration_ms or int(duration_ms) <= 0:
        raise BridgeError("a bridge needs a positive duration_ms")
    plan = {
        "prev_end_frame_hash": prev_end_frame_hash,
        "next_start_frame_hash": next_start_frame_hash,
        "duration_ms": int(duration_ms),
        "direction": direction,
        "description": description,
        "params": params or {},
    }
    plan["request_digest"] = hash_value({
        "p": prev_end_frame_hash, "n": next_start_frame_hash,
        "d": int(duration_ms), "dir": direction, "params": params or {}})
    return plan


# ------------------------------------------------------------------ execute


def execute_bridge(project: Any, shot: str, plan: dict[str, Any], *, provider,
                   spec_hash: str = "bridge", parent_take: str | None = None):
    """Execute the bridge through the STANDARD provider path — a
    ``GenerationRequest`` handed to ``provider.generate``, which registers the
    result as an append-only take. Returns the produced :class:`TakeInfo`. The
    ``bridge`` plan rides on ``params`` (lineage); ``redo_of`` records the parent."""
    from ..providers.base import GenerationRequest

    shot_spec = project.load_shot(shot)
    req = GenerationRequest(
        project=project, shot=shot_spec, bible=project.load_bible(),
        spec_hash=spec_hash, duration_ms=int(plan.get("duration_ms") or 0),
        candidates=1, params={"bridge": plan}, redo_of=parent_take)
    takes = provider.generate(req)
    if not takes:
        raise BridgeError("bridge provider returned no take")
    return takes[0]


def bridge_lineage(plan: dict[str, Any], take, *, parent_take: str | None = None) -> dict[str, Any]:
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
        "take": getattr(take, "name", None),
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


def adopt_bridge(lineage: dict[str, Any], *, review: dict | None) -> dict[str, Any]:
    """Adopt a reviewed bridge into source (the explicit human step, §8). Refuses
    unless ``review`` PASSED and is bound to the CURRENT bridge output bytes — a
    stale review (bound to older bytes) is rejected, so continuity is judged on
    what will actually ship."""
    if not review or not review.get("passed"):
        raise BridgeError("cannot adopt a bridge without a passing current-bound "
                          "review (§8: a bridge must not hide a continuity failure)")
    if review.get("bound_hash") != lineage.get("output_media_hash"):
        raise BridgeError("bridge review is bound to different bytes than the "
                          "current bridge output — re-review the current take")
    return {**lineage, "adopted": True, "review": review}


# ------------------------------------------------------------------ never-in-final


def assert_not_in_final(lineage: dict[str, Any], compiled_sources: list[str], *,
                        adopted: bool) -> list[dict[str, Any]]:
    """Manifest-side guard (§11 row 16): an UN-adopted bridge take must not appear
    among the compiled final's sources. Returns a blocking diagnostic if it does,
    complementing the AI_IDE_16 build gate. An adopted bridge is legitimate."""
    take = lineage.get("take")
    if adopted or take is None:
        return []
    if take in (compiled_sources or []):
        return [{
            "code": "UNAPPROVED_BRIDGE_IN_FINAL", "severity": "blocking",
            "detail": f"generative bridge take {take!r} is in the final but was "
                      f"never adopted through a current-bound review",
            "take": take,
        }]
    return []
