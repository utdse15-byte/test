"""AI_IDE_18 WP4 / WP5 / WP6 — duration adaptation advice, lip-sync request
lineage, and MEASURED lip-sync drift. All pure computation over evidence: no
local model, no LLM, and — the load-bearing pin — never a provider's own
self-reported "lip-sync score" (contract §8, §9).

- WP4 :func:`duration_proposals` — given a voice take duration, a slot duration
  and DECLARED thresholds, emit the §7 options in order as PROPOSALS with
  reasons. It NEVER mutates dialogue/translation text (the dialogue text digest
  is unchanged by any path — a proposal is advice, not an edit).

- WP5 :func:`plan_lipsync` / :func:`lipsync_lineage` — a lip-sync capability
  request binds the EXACT video + audio hashes and an explicit subject selector;
  more than one detectable face without a selector is REFUSED. The output is a
  NEW take whose lineage points back at the parent (``redo_of``), the original
  is never touched (append-only). An unrecognisable / occluded subject or a
  provider timeout yields UNKNOWN — never a fabricated success.

- WP6 :func:`measure_drift` — combine audio word/onset boundaries with (optional)
  mouth-motion observations into overall offset / local drift / max deviation +
  alignability, then PASS / FAIL / UNKNOWN with a route onto the existing
  7-route repair vocabulary.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from ..core.hashing import hash_value

# ---------------------------------------------------------------- WP5 lip-sync

LIP_SYNC_CAPABILITY = "lip_sync"

# verdicts (mirror the qc UNKNOWN discipline).
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


class LipSyncError(ValueError):
    """A lip-sync request is not admissible as stated (e.g. an ambiguous subject
    with no selector). Carries a human-readable reason."""


def plan_lipsync(*, video_hash: str, audio_hash: str,
                 subject_selector: Any = None, faces_detected: int = 1,
                 params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate + describe a lip-sync request. Binds the exact video/audio bytes
    by hash. RAISES :class:`LipSyncError` when more than one face is detectable
    and no explicit ``subject_selector`` disambiguates which one to drive
    (contract §8: 多角色必须显式 selector). Zero detectable faces ⇒ the request is
    admissible but flagged ``alignable=False`` so the result can only be UNKNOWN."""
    if not video_hash or not audio_hash:
        raise LipSyncError("lip-sync requires BOTH an exact video hash and an "
                           "exact audio hash — neither may be blank")
    if faces_detected > 1 and subject_selector in (None, "", []):
        raise LipSyncError(
            f"{faces_detected} faces detected but no subject_selector — a "
            "multi-face shot must name which subject to lip-sync (§8)")
    return {
        "capability": LIP_SYNC_CAPABILITY,
        "video_hash": video_hash,
        "audio_hash": audio_hash,
        "subject_selector": subject_selector,
        "faces_detected": int(faces_detected),
        "alignable": faces_detected >= 1,
        "params": dict(params or {}),
        "request_digest": hash_value({
            "v": video_hash, "a": audio_hash,
            "s": subject_selector, "p": params or {}}),
    }


def lipsync_lineage(parent_take: str, plan: dict[str, Any], *,
                    provider: str) -> dict[str, Any]:
    """The sidecar-lineage kwargs for the NEW lip-sync take. The output is
    append-only: it records ``redo_of=parent_take`` so the derived candidate
    family joins it to the source take, WITHOUT touching the original.
    ``value_note`` carries the exact input hashes as provenance."""
    return {
        "redo_of": parent_take,
        "provider": provider,
        "lineage": {
            "kind": "lip_sync",
            "parent_take": parent_take,
            "video_hash": plan.get("video_hash"),
            "audio_hash": plan.get("audio_hash"),
            "subject_selector": plan.get("subject_selector"),
        },
    }


def lipsync_result(plan: dict[str, Any], *, recognised: bool,
                   timed_out: bool = False) -> dict[str, Any]:
    """Turn a provider outcome into an admission verdict WITHOUT trusting any
    self-reported score. Unrecognised/occluded subject or a timeout ⇒ UNKNOWN
    (reconcile later), never a pretended PASS."""
    if timed_out:
        return {"verdict": UNKNOWN, "reason": "lip-sync provider timed out — "
                "hold as UNKNOWN, do not re-prompt automatically (§8)"}
    if not plan.get("alignable") or not recognised:
        return {"verdict": UNKNOWN, "reason": "subject not recognisable / "
                "occluded — cannot confirm lip-sync (§8)"}
    return {"verdict": "ADMITTED", "reason": "new lip-sync take admitted; "
            "measured drift QC still required (a model score is NOT alignment)"}


# ---------------------------------------------------------------- WP6 drift

# route mapping for a drift failure onto the existing 7-route vocabulary
# (qc/production.SEVEN_ROUTES) — addendum ruling 7.
ROUTE_OFFSET_FIX = "FIX_IN_POST"          # shift the audio/caption offset
ROUTE_TIME_STRETCH = "EDIT_DONT_REGENERATE"  # stretch within declared threshold
ROUTE_RE_TTS = "REROLL"                   # regenerate the voice take
ROUTE_RE_LIPSYNC = "REROLL"               # regenerate the lip-sync take
ROUTE_RESHOOT = "RESHOOT"                 # change the shot / camera
ROUTE_ACCEPT = "ACCEPT_DEVIATION"         # human accepts the deviation


def measure_drift(audio_onsets_ms: list[int],
                  mouth_events_ms: list[int] | None = None, *,
                  face_visible: bool | None = None,
                  pass_within_ms: int = 80,
                  fail_beyond_ms: int = 200) -> dict[str, Any]:
    """Measured drift between audio word/onset boundaries and observed mouth
    motion. Thresholds are DECLARED (params), not a hardcoded universal
    standard.

    Returns overall_offset_ms (median audio→mouth offset), max_deviation_ms,
    local_drift_ms (spread of the per-event offsets), alignability and a
    verdict + route. When there is nothing to measure against (no mouth
    observations, or the face is not visible) the verdict is UNKNOWN — drift is
    never invented from the audio alone."""
    onsets = sorted(int(x) for x in (audio_onsets_ms or []))
    mouths = sorted(int(x) for x in (mouth_events_ms or []))
    alignable = bool(mouths) and face_visible is not False

    if not alignable:
        reason = ("no mouth-motion observation to measure against"
                  if not mouths else "face not visible / occluded")
        return {
            "verdict": UNKNOWN, "alignable": False, "reason": reason,
            "overall_offset_ms": None, "max_deviation_ms": None,
            "local_drift_ms": None,
            "route": ROUTE_RESHOOT if face_visible is False else None,
        }

    # nearest-mouth-event offset per audio onset — the honest per-word drift.
    offsets: list[int] = []
    for o in onsets:
        nearest = min(mouths, key=lambda m: abs(m - o))
        offsets.append(nearest - o)
    if not offsets:
        return {"verdict": UNKNOWN, "alignable": True,
                "reason": "no audio onsets to compare",
                "overall_offset_ms": None, "max_deviation_ms": None,
                "local_drift_ms": None, "route": None}

    overall = int(round(median(offsets)))
    # local drift: how much each offset deviates from the overall shift (a pure
    # constant offset is a fixable sync error; scattered drift is not).
    deviations = [abs(x - overall) for x in offsets]
    max_dev = max(abs(x) for x in offsets)
    local_drift = max(deviations) if deviations else 0

    if max_dev <= pass_within_ms:
        verdict, route = PASS, None
    elif max_dev >= fail_beyond_ms:
        verdict = FAIL
        # a near-constant offset is a post offset fix; real drift needs a
        # stronger route (re-TTS / time-stretch / reshoot).
        route = ROUTE_OFFSET_FIX if local_drift <= pass_within_ms else ROUTE_RE_LIPSYNC
    else:
        verdict = FAIL
        route = ROUTE_TIME_STRETCH

    return {
        "verdict": verdict, "alignable": True, "reason": None,
        "overall_offset_ms": overall,
        "max_deviation_ms": max_dev,
        "local_drift_ms": local_drift,
        "route": route,
        "thresholds": {"pass_within_ms": pass_within_ms,
                       "fail_beyond_ms": fail_beyond_ms},
    }


# ---------------------------------------------------------------- WP4 advisor


def duration_proposals(voice_ms: int, slot_ms: int, *,
                       tolerance_ms: int = 150,
                       max_stretch_pct: float = 8.0) -> dict[str, Any]:
    """The §7 duration-adaptation options, IN ORDER, as proposals — never an
    edit. ``tolerance_ms`` and ``max_stretch_pct`` are declared thresholds.

    The dialogue text is never a variable here: this returns advice, and a test
    pins that the text digest is identical before and after consulting it."""
    delta = int(voice_ms) - int(slot_ms)
    within = abs(delta) <= tolerance_ms
    stretch_pct = (abs(delta) / slot_ms * 100.0) if slot_ms else float("inf")
    proposals: list[dict[str, Any]] = []
    if within:
        proposals.append({"action": "ACCEPT", "route": ROUTE_ACCEPT,
                          "reason": f"within tolerance ({delta:+d} ms ≤ "
                          f"{tolerance_ms} ms) — no adaptation needed"})
        return {"delta_ms": delta, "within_tolerance": True, "proposals": proposals,
                "text_mutation": False}

    # §7 order: re-TTS speed/style → source proposal → time-stretch (bounded)
    # → picture edit → human accept. Text rewrite is an AGENT source PROPOSAL,
    # never a core auto-edit.
    proposals.append({"action": "RETTS_SPEED", "route": ROUTE_RE_TTS,
                      "reason": "re-synthesize the voice with an explicit "
                      f"speed/style to absorb {delta:+d} ms"})
    proposals.append({"action": "SOURCE_PROPOSAL", "route": "REWRITE_SOURCE",
                      "reason": "an AGENT may PROPOSE a shorter/longer line — "
                      "a human accepts it as source; core never rewrites text"})
    if stretch_pct <= max_stretch_pct:
        proposals.append({"action": "TIME_STRETCH", "route": ROUTE_TIME_STRETCH,
                          "reason": f"time-stretch {stretch_pct:.1f}% (≤ "
                          f"{max_stretch_pct}% declared cap) via the existing "
                          "repair-op/ffmpeg path"})
    else:
        proposals.append({"action": "TIME_STRETCH_BLOCKED", "route": None,
                          "reason": f"time-stretch {stretch_pct:.1f}% exceeds the "
                          f"{max_stretch_pct}% cap — not offered"})
    proposals.append({"action": "PICTURE_EDIT", "route": "EDIT_DONT_REGENERATE",
                      "reason": "adjust the cut / hold to fit the audio"})
    proposals.append({"action": "ACCEPT_DEVIATION", "route": ROUTE_ACCEPT,
                      "reason": "a human accepts the timing deviation as-is"})
    return {"delta_ms": delta, "within_tolerance": False,
            "stretch_pct": round(stretch_pct, 2), "proposals": proposals,
            "text_mutation": False}
