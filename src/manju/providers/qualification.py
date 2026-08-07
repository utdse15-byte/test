"""AI_IDE_14 — real-provider qualification & low-cost canary (contract §3–§10).

DERIVED-ONLY. This module adds qualification EVIDENCE and canary orchestration
on TOP of the existing provider / submission / run-evidence machinery — it
builds NO second provider registry, task ledger or spend system (contract §1,
§11). Everything here is either a pure function of already-authoritative facts
or a thin driver over the STANDARD ``GenerationRequest`` / admission / evidence
path.

Two layers:

* **Pure derivation** — :func:`qualification_state` implements the 8-rung ladder
  ``UNTESTED → CONFIG_VALID → DRY_RUN_VALID → CANARY_SUBMIT_PASSED →
  CANARY_ARTIFACT_PASSED → RECOVERY_PASSED → PRODUCTION_READY`` with ``STALE`` /
  ``BLOCKED`` modelled as OVERLAYS ``(level, stale, blocked_reason)`` (addendum
  ruling 1). A qualification's semantic identity (its bindings, §3) is the
  provider profile digest (REUSED from DR04 ``providers.catalog``), the adapter
  semantic digest (source hash of the adapter class), the canary fixture
  version, the request digest, evidence refs, artifact probe facts and cost. A
  change to any of the four staleness anchors invalidates recorded canary
  evidence (drops to the live config floor and flags STALE). The check DATE is
  audit metadata ONLY — it never enters the identity.

* **Canary orchestration** — :func:`qualify` runs a fixed, low-cost, private-
  material-free canary (contract §5, §8: NO private/restricted input) through
  the STANDARD admission/evidence path (zero new ledger). Real network is
  operator-gated: ``--dry-run`` never touches a transport; ``--run`` is refused
  without an explicit max-cost it fits under, goes through the existing
  ``ask_before`` spend gate, and HARD-disables any fallback to a second
  provider — the canary is a chain of ONE (single ``submission_id``, never
  ``generate_with_fallback``).

Environment honesty (addendum): with NO real provider account here, the whole
real-canary path lands tested against SCRIPTED transports; the matrix tops real
cloud providers out at ``CONFIG_VALID`` / ``DRY_RUN_VALID`` — the correct
outcome, not a gap. ``PRODUCTION_READY`` is reachable ONLY by a real operator
running a real canary later (evidence must record ``transport == "real"``); this
module refuses to mint it from a scripted run.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Callable

from ..core.hashing import hash_file, hash_text, hash_value

SCHEMA = "manju.provider-qualification/v1"
FIXTURE_SCHEMA = "manju.canary-fixture/v1"

# ---------------------------------------------------------------- the ladder

UNTESTED = "UNTESTED"
CONFIG_VALID = "CONFIG_VALID"
DRY_RUN_VALID = "DRY_RUN_VALID"
CANARY_SUBMIT_PASSED = "CANARY_SUBMIT_PASSED"
CANARY_ARTIFACT_PASSED = "CANARY_ARTIFACT_PASSED"
RECOVERY_PASSED = "RECOVERY_PASSED"
PRODUCTION_READY = "PRODUCTION_READY"
# overlays (addendum ruling 1) — reported IN PLACE of the level string, but
# modelled separately so a caller always sees the underlying rung too.
STALE = "STALE"
BLOCKED = "BLOCKED"

LEVELS = (UNTESTED, CONFIG_VALID, DRY_RUN_VALID, CANARY_SUBMIT_PASSED,
          CANARY_ARTIFACT_PASSED, RECOVERY_PASSED, PRODUCTION_READY)
_ORDER = {name: i for i, name in enumerate(LEVELS)}

# The semantic identity of a qualification (§3). A change to ANY anchor makes
# recorded canary evidence STALE. The check DATE is deliberately NOT here.
STALENESS_ANCHORS = (
    "provider_profile_digest",   # DR04 — moves on a provider profile change
    "adapter_semantic_digest",   # moves on a real adapter-code edit
    "fixture_version",           # moves on a canary fixture / input-media edit
    "request_digest",            # moves on a key request-semantics change
    "response_schema_digest",    # moves on a submit/poll response-schema drift
)


# ============================================================ pure derivation


def _max_level(a: str, b: str) -> str:
    return a if _ORDER.get(a, 0) >= _ORDER.get(b, 0) else b


def staleness_drift(evidence: dict | None, declared: dict | None) -> list[str]:
    """The staleness anchors whose evidence-recorded value differs from the
    current declared value (§3). The VALUE compare needs both sides; a MISSING
    mandatory anchor is handled separately by
    :func:`missing_staleness_anchors` (closeout §2 item 5 — a missing anchor is
    never read as "no drift")."""
    ev, cur = evidence or {}, declared or {}
    return [k for k in STALENESS_ANCHORS
            if ev.get(k) is not None and cur.get(k) is not None
            and ev.get(k) != cur.get(k)]


def missing_staleness_anchors(evidence: dict | None, declared: dict | None,
                              capability: str | None = None) -> list[str]:
    """The MANDATORY staleness anchors a recorded rung cannot be verified
    without (closeout contract §2 item 5: 缺失不是"无漂移" — a missing anchor is
    STALE, never silently current).

    * any recorded rung >= DRY_RUN_VALID must carry the profile + adapter
      digests (and the fixture version when the capability has a canary
      fixture);
    * a canary rung (>= CANARY_SUBMIT_PASSED) must additionally carry the
      request digest and the response-schema digest it was earned under;
    * a live-derivable anchor recorded on the evidence but absent from the
      CURRENT declared facts (e.g. the canary fixture was deleted) can no
      longer be verified as un-drifted — also missing.

    Pure: the only lookup is the static capability→fixture map."""
    ev, cur = evidence or {}, declared or {}
    level = ev.get("level")
    if level not in _ORDER or _ORDER[level] < _ORDER[DRY_RUN_VALID]:
        return []
    required = {"provider_profile_digest", "adapter_semantic_digest"}
    if capability and capability_fixture_id(capability) is not None:
        required.add("fixture_version")
    if _ORDER[level] >= _ORDER[CANARY_SUBMIT_PASSED]:
        required.update({"request_digest", "response_schema_digest"})
    missing = sorted(k for k in required if not ev.get(k))
    for k in ("provider_profile_digest", "adapter_semantic_digest", "fixture_version"):
        if ev.get(k) is not None and cur.get(k) is None and k not in missing:
            missing.append(k)
    return missing


def _bindings(provider_id: str, capability: str,
              declared: dict, evidence: dict | None) -> dict:
    """The qualification's bindings (§3). Where canary evidence exists its
    recorded digests are the identity (what it was qualified UNDER); absent
    evidence, the current declared profile/adapter digests stand in. The check
    date is intentionally excluded — audit metadata, never identity."""
    ev = evidence or {}
    return {
        "provider_id": provider_id,
        "capability": capability,
        "provider_profile_digest": (ev.get("provider_profile_digest")
                                    or declared.get("provider_profile_digest")),
        "adapter_semantic_digest": (ev.get("adapter_semantic_digest")
                                    or declared.get("adapter_semantic_digest")),
        "fixture_version": ev.get("fixture_version"),
        "request_digest": ev.get("request_digest"),
        "response_schema_digest": ev.get("response_schema_digest"),
        "evidence_refs": ev.get("evidence_refs") or [],
        "artifact": ev.get("artifact"),
        "cost": ev.get("cost"),
    }


def qualification_state(provider_id: str, capability: str, *,
                        evidence: dict | None, declared: dict | None) -> dict:
    """Derive one provider capability's qualification as ``(state, level, stale,
    blocked_reason, bindings, reasons)`` — a PURE function of the current
    ``declared`` facts and the recorded ``evidence`` (contract §3, addendum
    ruling 1). No I/O, no clock: two calls with the same inputs are identical.

    * ``level`` — the highest rung the evidence + live-derivable facts support.
      ``CONFIG_VALID`` is the live floor when the manifest exists and offline
      doctor passes; higher rungs come only from non-stale recorded evidence.
    * ``stale`` — a recorded rung is invalidated because a staleness anchor
      moved; the level falls back to the config floor and the drift is named.
    * ``blocked_reason`` — a structural condition that prevents qualification
      (provider absent / broken manifest); reported as ``BLOCKED``.
    """
    declared = declared or {}
    reasons: list[str] = []

    exists = declared.get("exists", True)
    blocked_reason: str | None = None
    if not exists:
        blocked_reason = "provider_absent"
    elif declared.get("manifest_error"):
        blocked_reason = "manifest_error"
    elif declared.get("enabled") is False:
        # closeout §2 item 3: enabled == false ⇒ BLOCKED(provider_disabled) —
        # regardless of any recorded evidence.
        blocked_reason = "provider_disabled"
    elif declared.get("capability_declared") is False:
        # closeout §2 item 4: a capability the provider never declared cannot
        # be admitted, whatever evidence claims.
        blocked_reason = "capability_not_declared"

    config_ok = bool(declared.get("config_ok"))
    floor = CONFIG_VALID if (exists and config_ok) else UNTESTED
    if exists and not config_ok:
        probs = declared.get("config_problems") or []
        reasons.append("config_invalid" + (": " + "; ".join(probs) if probs else ""))

    stale = False
    ev_level = (evidence or {}).get("level")
    if evidence and ev_level in _ORDER:
        drift = staleness_drift(evidence, declared)
        missing = missing_staleness_anchors(evidence, declared, capability)
        if drift:
            reasons.append("stale:" + ",".join(drift))
        if missing:
            # closeout §2 item 5: a missing mandatory anchor is STALE — the
            # recorded rung cannot be verified, so it falls to the live floor.
            reasons.append("stale_missing_anchors:" + ",".join(missing))
        if drift or missing:
            stale = True
            level = floor
        else:
            level = _max_level(floor, ev_level)
    else:
        level = floor

    # PRODUCTION_READY is a REAL-canary-only rung (addendum): a scripted run can
    # prove the machinery but never production-readiness. Enforce it here too so
    # the invariant holds regardless of who wrote the evidence.
    if level == PRODUCTION_READY and (evidence or {}).get("transport") != "real":
        level = RECOVERY_PASSED
        reasons.append("production_ready_requires_real_canary")

    state = BLOCKED if blocked_reason else (STALE if stale else level)
    return {
        "state": state,
        "level": level,
        "stale": stale,
        "blocked_reason": blocked_reason,
        "bindings": _bindings(provider_id, capability, declared, evidence),
        "reasons": reasons,
        "provider_id": provider_id,
        "capability": capability,
    }


# ============================================================ adapter digest


def _adapter_class(manifest: Any):
    from importlib import import_module

    from .manifest import (
        COMFYUI_ADAPTER,
        GENERIC_ADAPTER,
        GENERIC_ASR_ADAPTER,
        GENERIC_TTS_ADAPTER,
        LOCAL_CMD_ADAPTER,
    )

    adapter = getattr(manifest, "adapter", "") or ""
    if adapter == GENERIC_ADAPTER:
        from .generic_cloud import GenericCloudProvider
        return GenericCloudProvider
    if adapter == GENERIC_TTS_ADAPTER:
        from .tts import GenericTtsProvider
        return GenericTtsProvider
    if adapter == GENERIC_ASR_ADAPTER:
        from .asr import GenericAsrProvider
        return GenericAsrProvider
    if adapter == COMFYUI_ADAPTER:
        from .comfyui import ComfyUIProvider
        return ComfyUIProvider
    if adapter == LOCAL_CMD_ADAPTER:
        from .local_cmd import LocalCommandProvider
        return LocalCommandProvider
    if ":" in adapter:  # module:Class escape hatch
        mod_name, _, cls_name = adapter.partition(":")
        try:
            return getattr(import_module(mod_name), cls_name, None)
        except Exception:
            return None
    return None


def adapter_semantic_digest(manifest: Any) -> str:
    """A stable ``sha256:`` over the adapter CLASS source (§3 / addendum ruling
    1): it moves only when the adapter's real behaviour is edited, and is
    independent of paths, mtime and credentials. Falls back to hashing the
    adapter string when the class source is unavailable (built-in without a
    manifest, or an unresolvable escape hatch)."""
    import inspect

    cls = _adapter_class(manifest)
    if cls is None:
        return hash_text("adapter:" + (getattr(manifest, "adapter", "") or "unknown"))
    try:
        return hash_text(inspect.getsource(cls))
    except (OSError, TypeError):
        return hash_text("adapter-class:" + cls.__qualname__)


# ============================================================ canary fixtures


# capability string -> the canary fixture id that qualifies it (contract §5).
_CAPABILITY_FIXTURE: dict[str, str] = {
    "image_to_video": "canary.video.i2v.v1",
    "text_to_video": "canary.video.i2v.v1",
    "first_last_frame": "canary.video.i2v.v1",
    "text_to_image": "canary.image.t2i.v1",
    "image_to_image": "canary.image.t2i.v1",
    "image": "canary.image.t2i.v1",
    "video": "canary.video.i2v.v1",
    "tts": "canary.tts.v1",
    "asr": "canary.asr.v1",
    "vision": "canary.vlm.review.v1",
    "visual_review": "canary.vlm.review.v1",
}

# adapters whose full submit/poll/download canary this batch drives end-to-end.
# Others (tts/asr/comfyui/local_cmd/vision) qualify to DRY_RUN_VALID here and a
# real submit canary for them is SKIPPED_WITH_EVIDENCE (their request/response
# shape differs — a real canary extends the runner; the machinery is identical).
_RUN_CANARY_ADAPTERS = frozenset({"generic_cloud"})


def canary_fixtures_dir() -> Path:
    """Where the committed canary fixtures live (addendum ruling 3:
    ``tests/fixtures/canary/``). Overridable with ``MANJU_CANARY_FIXTURES_DIR``
    (tests pin their own copy)."""
    env = os.environ.get("MANJU_CANARY_FIXTURES_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "canary"


def capability_fixture_id(capability: str) -> str | None:
    return _CAPABILITY_FIXTURE.get(capability)


def fixture_version(fixture_path: Path) -> str:
    """The fixture VERSION binding (§3, addendum ruling 3): a hash over the
    fixture YAML bytes AND any bundled input/sample media, so editing the
    contract OR the deterministic input moves it (staleness anchor)."""
    from ..core.yamlio import read_yaml

    parts = [hash_file(fixture_path)]
    data = read_yaml(fixture_path) or {}
    for key in ("input_media", "sample_artifact"):
        media = data.get(key)
        if media:
            mp = fixture_path.parent / media
            if mp.is_file():
                parts.append(hash_file(mp))
    return hash_value(parts)


def load_fixture(capability: str, *, fixtures_dir: Path | None = None) -> dict:
    """Load + validate the canary fixture contract for ``capability`` (contract
    §5). Returns the fixture dict with a computed ``fixture_version`` and the
    resolved fixture directory. Raises ``ValueError`` for an unknown capability
    or a malformed/absent fixture (never a silent pass)."""
    from ..core.yamlio import read_yaml

    fid = capability_fixture_id(capability)
    if fid is None:
        raise ValueError(
            f"no canary fixture for capability {capability!r} "
            f"(known: {sorted(_CAPABILITY_FIXTURE)})")
    fdir = fixtures_dir or canary_fixtures_dir()
    fpath = Path(fdir) / f"{fid}.yaml"
    if not fpath.is_file():
        raise ValueError(f"canary fixture missing: {fpath}")
    data = read_yaml(fpath) or {}
    if data.get("schema") != FIXTURE_SCHEMA:
        raise ValueError(f"{fpath}: not a {FIXTURE_SCHEMA} fixture")
    data["fixture_version"] = fixture_version(fpath)
    data["_dir"] = str(fdir)
    data["_path"] = str(fpath)
    return data


# ============================================================ declared facts


def declared_facts(provider_id: str, capability: str, *,
                   fixtures_dir: Path | None = None) -> dict:
    """The CURRENT declared facts for one provider capability, derived live from
    the authoritative catalog + manifest (never a stored copy). Feeds the pure
    :func:`qualification_state` and the staleness compare."""
    from .catalog import descriptor_for_manifest, provider_profile_digest
    from .manifest import load_manifests

    manifests, errors = load_manifests()
    manifest = manifests.get(provider_id)
    facts: dict[str, Any] = {"provider_id": provider_id, "capability": capability}

    if manifest is not None:
        descriptor = descriptor_for_manifest(manifest)
        problems = manifest.validate_for_generic()
        facts.update({
            "exists": True,
            "enabled": not manifest.disabled,
            # closeout §2 item 4: admission is capability-exact — a capability
            # the manifest never declared is structurally blocked.
            "capability_declared": capability in (manifest.capabilities or []),
            "kind": descriptor.kind,
            "adapter": manifest.adapter,
            "adapter_short": _adapter_short(manifest.adapter),
            "provider_profile_digest": provider_profile_digest(
                provider_id, capability, descriptor=descriptor),
            "adapter_semantic_digest": adapter_semantic_digest(manifest),
            "config_ok": not problems,
            "config_problems": problems,
        })
    else:
        # a built-in provider (no manifest) OR an absent/broken one.
        descriptor = _builtin_descriptor(provider_id)
        if descriptor is None:
            facts.update({
                "exists": False,
                "manifest_error": any(provider_id in e for e in errors),
                "config_ok": False,
                "config_problems": ["provider not found"],
            })
            return facts
        facts.update({
            "exists": True,
            "enabled": descriptor.enabled,
            # a built-in declares its capabilities on the descriptor; its
            # provider TYPE stands in when the capability list is empty (the
            # matrix iterates exactly those rows).
            "capability_declared": (
                capability in (descriptor.capabilities or ())
                or capability == getattr(descriptor, "provider_type", None)),
            "kind": descriptor.kind,
            "adapter": "builtin",
            "adapter_short": "builtin",
            "provider_profile_digest": provider_profile_digest(
                provider_id, capability, descriptor=descriptor),
            "adapter_semantic_digest": hash_text("builtin:" + provider_id),
            # a built-in local provider needs no manifest fill — config is
            # trivially valid, but it is LOCAL, never a paid-canary target.
            "config_ok": True,
            "config_problems": [],
        })

    try:
        facts["fixture_version"] = load_fixture(
            capability, fixtures_dir=fixtures_dir)["fixture_version"]
    except ValueError:
        pass  # no fixture for this capability — staleness simply skips that anchor
    return facts


def _builtin_descriptor(provider_id: str):
    from .catalog import iter_provider_descriptors

    descriptors, _ = iter_provider_descriptors()
    return next((d for d in descriptors if d.provider_id == provider_id), None)


def _adapter_short(adapter: str) -> str:
    from .manifest import ADAPTER_ALIASES

    for alias, full in ADAPTER_ALIASES.items():
        if full == adapter:
            return alias
    return adapter.split(":")[-1] if ":" in adapter else adapter


# ============================================================ report projection
# The qualification report is a DELETABLE derived JSON — a DISPLAY PROJECTION
# ONLY (closeout §2 items 1/9): reports/providers/qualification/
# <provider>__<capability>.json. It is NEVER a build/resume/cache/ADMISSION
# input — forging, editing or deleting it changes no real admission authority
# (the durable evidence stream below is the truth admission re-materializes
# from). A test pins that no build path reads this directory (contract test 15).


def qualification_dir(project: Any) -> Path:
    return Path(project.root) / "reports" / "providers" / "qualification"


def report_path(project: Any, provider_id: str, capability: str) -> Path:
    from ..core.idents import validate_safe_segment

    validate_safe_segment(provider_id, label="provider_id")
    validate_safe_segment(capability, label="capability")
    return qualification_dir(project) / f"{provider_id}__{capability}.json"


def write_report(project: Any, report: dict) -> Path:
    import json

    from ..core.yamlio import atomic_write_text

    path = report_path(project, report["provider_id"], report["capability"])
    atomic_write_text(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return path


def read_report(project: Any, provider_id: str, capability: str) -> dict | None:
    import json

    path = report_path(project, provider_id, capability)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ====================================================== durable evidence store
# CLOSEOUT C1 (contract §2 items 1-2): network admission is re-materialized
# from durable APPEND-ONLY qualification evidence riding the project's existing
# events.jsonl stream — the same flock-serialized coordinator the canary's
# submission events already use (core.events.append_jsonl_line: no new ledger,
# no new lock, no new file, no new schema). Each (provider, capability) chain
# is hash-linked exactly like the DR06 submission chains, so tampering is
# detectable; a torn/corrupt stream FAILS CLOSED (admission blocked,
# transport 0), never "no evidence, carry on".

QUALIFICATION_EVIDENCE_ACTION = "qualification_evidence"
RISK_ACCEPTANCE_ACTION = "bridge_risk_acceptance"
RISK_CONSUMED_ACTION = "bridge_risk_acceptance_consumed"
# the structured fail-closed blocked_reason for unreadable/tampered evidence.
EVIDENCE_CORRUPT = "evidence_corrupt"

_EVIDENCE_ACTIONS = frozenset(
    {QUALIFICATION_EVIDENCE_ACTION, RISK_ACCEPTANCE_ACTION, RISK_CONSUMED_ACTION})


def _evidence_event_digest(detail: dict) -> str:
    """The per-(provider, capability) chain digest — over the controlled fields
    only, mirroring ``providers.submission.submission_event_digest``."""
    return hash_value({
        "provider_id": detail.get("provider_id"),
        "capability": detail.get("capability"),
        "evidence": detail.get("evidence"),
        "event_id": detail.get("event_id"),
        "prev_event_digest": detail.get("prev_event_digest"),
    })


def _read_evidence_stream(project: Any) -> tuple[list[dict], int]:
    """Project the qualification-evidence / risk-acceptance events out of
    ``events.jsonl`` in FILE order (emission order under the append flock).
    Returns ``(records, malformed)`` — ``malformed`` counts torn lines in the
    WHOLE stream (F1 discipline: a stream that provably lost a line cannot
    vouch for any chain read from it)."""
    import json as _json

    path = Path(project.root) / "events.jsonl"
    records: list[dict] = []
    malformed = 0
    if not path.exists():
        return records, malformed
    # errors="replace": a torn multibyte tail must count as ONE malformed line
    # (the F1 discipline below), not raise UnicodeDecodeError mid-iteration and
    # crash every qualification reader.
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = _json.loads(raw)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(rec, dict) or rec.get("action") not in _EVIDENCE_ACTIONS:
                continue
            detail = rec.get("detail")
            if isinstance(detail, dict):
                records.append({**detail, "_action": rec.get("action")})
            else:
                malformed += 1
    return records, malformed


def record_qualification_evidence(project: Any, provider_id: str, capability: str,
                                  evidence: dict, *, actor: str = "engine") -> dict:
    """Append ONE durable qualification-evidence event — the admission-truth
    write. Hash-chained per (provider, capability); the report JSON written
    alongside is only a projection of this. Returns the written event detail
    (``{}`` when the best-effort durable append could not land)."""
    from uuid import uuid4

    from ..core import events as _events_core
    from ..core.idents import validate_safe_segment

    validate_safe_segment(provider_id, label="provider_id")
    validate_safe_segment(capability, label="capability")
    records, _malformed = _read_evidence_stream(project)
    chain = [r for r in records
             if r.get("_action") == QUALIFICATION_EVIDENCE_ACTION
             and r.get("provider_id") == provider_id
             and r.get("capability") == capability]
    prev = _evidence_event_digest(chain[-1]) if chain else None
    detail = {
        "provider_id": provider_id,
        "capability": capability,
        "evidence": evidence,
        "event_id": "qev_" + uuid4().hex[:12],
        "prev_event_digest": prev,
    }
    record = {"ts": _now_iso(), "actor": actor,
              "action": QUALIFICATION_EVIDENCE_ACTION, "detail": detail}
    ok = _events_core.append_jsonl_line(Path(project.root), record,
                                        durable=True, required=False)
    return detail if ok else {}


def read_qualification_evidence(project: Any, provider_id: str,
                                capability: str) -> tuple[dict | None, str | None]:
    """The durable evidence for one (provider, capability): ``(evidence,
    error)``. ``error == EVIDENCE_CORRUPT`` means the stream is torn or the
    chain broke — the caller MUST fail closed (blocked admission, transport 0),
    never treat it as UNTESTED-and-fine (closeout §2 item 10)."""
    try:
        records, malformed = _read_evidence_stream(project)
    except OSError:
        return None, EVIDENCE_CORRUPT
    if malformed:
        return None, EVIDENCE_CORRUPT
    chain = [r for r in records
             if r.get("_action") == QUALIFICATION_EVIDENCE_ACTION
             and r.get("provider_id") == provider_id
             and r.get("capability") == capability]
    prev: str | None = None
    for rec in chain:
        if rec.get("prev_event_digest") != prev:
            return None, EVIDENCE_CORRUPT
        prev = _evidence_event_digest(rec)
    if not chain:
        return None, None
    evidence = chain[-1].get("evidence")
    if not isinstance(evidence, dict) or evidence.get("level") not in _ORDER:
        return None, EVIDENCE_CORRUPT
    return evidence, None


def _stored_evidence(project: Any, provider_id: str,
                     capability: str) -> tuple[dict | None, str | None]:
    """The recorded evidence that feeds the pure derivation — read from the
    DURABLE append-only store, never from the deletable report projection
    (closeout §2 item 1). Never trusted as truth: only a claim re-derived
    against the CURRENT declared facts (so stale evidence re-reads as STALE).
    Returns ``(evidence, error)`` — see :func:`read_qualification_evidence`."""
    return read_qualification_evidence(project, provider_id, capability)


# ------------------------------------------------- one-time risk acceptance
# closeout §2 item 8 / addendum C1 ruling 6: a LOW-RUNG manual bridge
# experiment may be admitted only by a ONE-TIME operator risk acceptance —
# an append-only evidence event bound to the EXACT (provider_id, capability,
# request_digest), consumed on first use, never a config flag, never covering
# a different digest. It can never override an explicit provider disable or an
# undeclared capability on a real manifest.


def record_bridge_risk_acceptance(project: Any, provider_id: str, capability: str,
                                  request_digest: str, *,
                                  accepted_by: str = "operator") -> dict:
    """Record the one-time operator risk acceptance (durable, append-only).
    Raises ``EvidenceWriteError`` when the durable write cannot land — an
    acceptance that is not durably recorded must not exist."""
    from uuid import uuid4

    from ..core import events as _events_core

    detail = {
        "provider_id": provider_id,
        "capability": capability,
        "request_digest": request_digest,
        "accepted_by": accepted_by,
        "acceptance_id": "acc_" + uuid4().hex[:12],
        "single_use": True,
    }
    record = {"ts": _now_iso(), "actor": "human",
              "action": RISK_ACCEPTANCE_ACTION, "detail": detail}
    _events_core.append_jsonl_line(Path(project.root), record,
                                   durable=True, required=True)
    return detail


def _consume_risk_acceptance(project: Any, provider_id: str, capability: str,
                             request_digest: str) -> dict | None:
    """Find an UNCONSUMED acceptance for the exact triple and durably consume
    it (single-use). Returns the acceptance, or ``None`` (none matches, the
    stream is corrupt, or the consumption event could not be durably written —
    all fail closed)."""
    from ..core import events as _events_core

    if not request_digest:
        return None
    try:
        records, malformed = _read_evidence_stream(project)
    except OSError:
        return None
    if malformed:
        return None  # a torn stream cannot vouch for acceptances either
    consumed = {r.get("acceptance_id") for r in records
                if r.get("_action") == RISK_CONSUMED_ACTION}
    match = next(
        (r for r in records
         if r.get("_action") == RISK_ACCEPTANCE_ACTION
         and r.get("provider_id") == provider_id
         and r.get("capability") == capability
         and r.get("request_digest") == request_digest
         and r.get("acceptance_id") not in consumed),
        None)
    if match is None:
        return None
    record = {"ts": _now_iso(), "actor": "engine", "action": RISK_CONSUMED_ACTION,
              "detail": {"acceptance_id": match.get("acceptance_id"),
                         "provider_id": provider_id, "capability": capability,
                         "request_digest": request_digest}}
    ok = _events_core.append_jsonl_line(Path(project.root), record,
                                        durable=True, required=False)
    if not ok:
        return None  # single-use is only provable when the consumption is durable
    return {k: v for k, v in match.items() if k != "_action"}


# =============================================== AI_IDE_15 reviewer admission gate
#
# The cloud-visual reviewer (AI_IDE_15 §2, addendum ruling 1) may dispatch a real
# VLM review ONLY when the vision provider is qualified. This gate lives in the
# PROVIDER layer (it consumes the ladder above); the qc package must not couple to
# the qualification report (the build-boundary guard §15). Core imports NO vendor
# SDK: this is a pure qualification check, never a transport. The offline fake
# reviewer double drives record_verdicts directly and never reaches this gate.

# closeout §2 item 7: an UNATTENDED or PAID review dispatch needs a fully
# proven provider; only an explicitly HUMAN-INTERACTIVE session may run from a
# passed canary artifact. DRY_RUN_VALID launches nothing real (Q10).
REVIEWER_MIN_QUALIFICATION = PRODUCTION_READY
REVIEWER_INTERACTIVE_MIN_QUALIFICATION = CANARY_ARTIFACT_PASSED
REVIEWER_REFUSAL = "REVIEWER_NOT_QUALIFIED"


def _evidence_corrupt_refusal(refusal: str, min_required: str,
                              provider_id: str, capability: str) -> dict:
    """The structured fail-closed decision for corrupt/unreadable durable
    evidence (closeout §2 item 10): admission blocked, transport 0, never an
    exception escaping to the caller, never UNTESTED-and-fine."""
    return {"admitted": False, "refusal": refusal, "level": None,
            "state": BLOCKED, "blocked_reason": EVIDENCE_CORRUPT,
            "evidence_corrupt": True, "min_required": min_required,
            "reason": ("durable qualification evidence is corrupt/unreadable — "
                       "failing closed (no dispatch, transport 0); repair or "
                       "re-run qualification"),
            "provider_id": provider_id, "capability": capability}


def reviewer_admission_from_state(qualification: dict, *,
                                  interactive: bool = False) -> dict:
    """Pure admission predicate over a :func:`qualification_state` dict. A cloud
    VLM review may be dispatched ONLY when the provider is qualified to at least
    the applicable floor — :data:`REVIEWER_MIN_QUALIFICATION` (unattended/paid
    default) or :data:`REVIEWER_INTERACTIVE_MIN_QUALIFICATION` (an explicitly
    human-interactive session) — and is not BLOCKED and not STALE; anything
    else is a structured ``REVIEWER_NOT_QUALIFIED`` refusal — never a silent
    proceed. No I/O, so both branches are testable without a real provider."""
    min_required = (REVIEWER_INTERACTIVE_MIN_QUALIFICATION if interactive
                    else REVIEWER_MIN_QUALIFICATION)
    level = qualification.get("level")
    stale = bool(qualification.get("stale"))
    blocked = qualification.get("blocked_reason")
    min_rung = _ORDER.get(min_required, 0)
    qualified = _ORDER.get(level, -1) >= min_rung and not blocked and not stale
    if qualified:
        return {"admitted": True, "refusal": None, "level": level,
                "state": qualification.get("state"),
                "min_required": min_required,
                "reason": f"qualified to {level} (>= {min_required})"}
    if blocked:
        why = f"provider blocked: {blocked}"
    elif stale:
        why = f"qualification stale (rung fell back to {level})"
    else:
        why = (f"qualified only to {level}; a cloud VLM review needs "
               f">= {min_required} (run provider qualification first)")
    return {"admitted": False, "refusal": REVIEWER_REFUSAL, "level": level,
            "state": qualification.get("state"),
            "min_required": min_required, "reason": why}


def reviewer_admission(provider_id: str, capability: str = "vision", *,
                       project: Any | None = None,
                       evidence: dict | None = None,
                       fixtures_dir: Path | None = None,
                       interactive: bool = False) -> dict:
    """Whether a cloud VLM review may be dispatched through ``provider_id`` for
    ``capability`` (default ``vision``). Reuses the ladder end to end: live
    :func:`declared_facts` + any recorded (deletable) evidence → the pure
    :func:`qualification_state` → :func:`reviewer_admission_from_state`.

    This is the SAME plumbing behind the real-reviewer path (AI_IDE_15 addendum
    ruling 1): a pure qualification check that opens NO network transport and
    imports NO vendor SDK — a broken / absent / unqualified vision provider is
    refused with ``REVIEWER_NOT_QUALIFIED`` rather than dispatched. In this
    environment no real vision provider reaches ``DRY_RUN_VALID``, so the real
    review round-trip is SKIPPED_WITH_EVIDENCE and this refusal is its stand-in."""
    try:
        declared = declared_facts(provider_id, capability, fixtures_dir=fixtures_dir)
    except Exception as exc:  # a broken manifest is not qualified — never a pass
        declared = {"exists": False, "manifest_error": str(exc)}
    ev = evidence
    if ev is None and project is not None:
        try:
            ev, err = _stored_evidence(project, provider_id, capability)
        except Exception:
            ev, err = None, EVIDENCE_CORRUPT
        if err == EVIDENCE_CORRUPT:
            return _evidence_corrupt_refusal(
                REVIEWER_REFUSAL,
                REVIEWER_INTERACTIVE_MIN_QUALIFICATION if interactive
                else REVIEWER_MIN_QUALIFICATION, provider_id, capability)
    q = qualification_state(provider_id, capability, evidence=ev, declared=declared)
    decision = reviewer_admission_from_state(q, interactive=interactive)
    decision.update({"provider_id": provider_id, "capability": capability})
    return decision


# ============================================ AI_IDE_19 analyzer admission gate
#
# A cloud video-understanding / media-analysis provider (AI_IDE_19 WP1, addendum
# ruling 1) is one more capability rung on the SAME ladder: real cloud analysis
# lands ONLY behind qualification, and fixture analysis runs first (contract §2).
# This mirrors the AI_IDE_15 reviewer gate exactly — a pure qualification check
# that opens NO transport and imports NO vendor SDK; a broken / absent /
# unqualified analyzer is refused with ``ANALYZER_NOT_QUALIFIED`` rather than
# dispatched. The offline fixture analyzer never reaches this gate.

# closeout §2 item 7: same floors as the reviewer — unattended/paid analysis
# needs PRODUCTION_READY; only an explicitly human-interactive session may run
# from a passed canary artifact. DRY_RUN_VALID launches nothing real (Q11).
ANALYZER_MIN_QUALIFICATION = PRODUCTION_READY
ANALYZER_INTERACTIVE_MIN_QUALIFICATION = CANARY_ARTIFACT_PASSED
ANALYZER_REFUSAL = "ANALYZER_NOT_QUALIFIED"


def analyzer_admission_from_state(qualification: dict, *,
                                  interactive: bool = False) -> dict:
    """Pure admission predicate for a cloud analyzer over a
    :func:`qualification_state` dict. Dispatch is allowed ONLY when the provider
    is qualified to at least the applicable floor (unattended/paid default
    :data:`ANALYZER_MIN_QUALIFICATION`; human-interactive
    :data:`ANALYZER_INTERACTIVE_MIN_QUALIFICATION`), not BLOCKED and not STALE;
    anything else is a structured ``ANALYZER_NOT_QUALIFIED`` refusal — never a
    silent proceed. No I/O, so both branches are testable offline."""
    min_required = (ANALYZER_INTERACTIVE_MIN_QUALIFICATION if interactive
                    else ANALYZER_MIN_QUALIFICATION)
    level = qualification.get("level")
    stale = bool(qualification.get("stale"))
    blocked = qualification.get("blocked_reason")
    min_rung = _ORDER.get(min_required, 0)
    qualified = _ORDER.get(level, -1) >= min_rung and not blocked and not stale
    if qualified:
        return {"admitted": True, "refusal": None, "level": level,
                "state": qualification.get("state"),
                "min_required": min_required,
                "reason": f"qualified to {level} (>= {min_required})"}
    if blocked:
        why = f"provider blocked: {blocked}"
    elif stale:
        why = f"qualification stale (rung fell back to {level})"
    else:
        why = (f"qualified only to {level}; a cloud analysis needs "
               f">= {min_required} (run provider qualification first)")
    return {"admitted": False, "refusal": ANALYZER_REFUSAL, "level": level,
            "state": qualification.get("state"),
            "min_required": min_required, "reason": why}


def analyzer_admission(provider_id: str, capability: str = "media_analysis", *,
                       project: Any | None = None,
                       evidence: dict | None = None,
                       fixtures_dir: Path | None = None,
                       interactive: bool = False) -> dict:
    """Whether a cloud media-analysis run may be dispatched through
    ``provider_id`` for ``capability``. Reuses the ladder end to end (live
    :func:`declared_facts` + any recorded deletable evidence → the pure
    :func:`qualification_state` → :func:`analyzer_admission_from_state`), exactly
    like :func:`reviewer_admission`. In this environment no real analyzer reaches
    ``DRY_RUN_VALID``, so the real analysis round-trip is SKIPPED_WITH_EVIDENCE
    and this refusal is its stand-in (contract §2: fixture analysis first)."""
    try:
        declared = declared_facts(provider_id, capability, fixtures_dir=fixtures_dir)
    except Exception as exc:  # a broken manifest is not qualified — never a pass
        declared = {"exists": False, "manifest_error": str(exc)}
    ev = evidence
    if ev is None and project is not None:
        try:
            ev, err = _stored_evidence(project, provider_id, capability)
        except Exception:
            ev, err = None, EVIDENCE_CORRUPT
        if err == EVIDENCE_CORRUPT:
            return _evidence_corrupt_refusal(
                ANALYZER_REFUSAL,
                ANALYZER_INTERACTIVE_MIN_QUALIFICATION if interactive
                else ANALYZER_MIN_QUALIFICATION, provider_id, capability)
    q = qualification_state(provider_id, capability, evidence=ev, declared=declared)
    decision = analyzer_admission_from_state(q, interactive=interactive)
    decision.update({"provider_id": provider_id, "capability": capability})
    return decision


# ============================================ AI_IDE_19 bridge admission gate
#
# A REAL generative transition bridge (WP5b) executes through the STANDARD paid
# provider path — closeout §2 item 8: the DEFAULT floor is PRODUCTION_READY (a
# paid unattended generation), and the ONLY way below it is a one-time operator
# risk acceptance bound to the exact (provider, capability, request_digest) —
# single-use, durable, never a config flag. Same ladder, same shape as the
# reviewer/analyzer gates; a structured ``BRIDGE_NOT_QUALIFIED`` refusal rather
# than a silent paid submit. The gate lives in the PROVIDER layer (build/ must
# not import it — the build-boundary guard); the ONE dispatch seam every bridge
# surface funnels through (``providers.base.dispatch_bridge``) consults it.

BRIDGE_MIN_QUALIFICATION = PRODUCTION_READY
BRIDGE_REFUSAL = "BRIDGE_NOT_QUALIFIED"

# Blocked reasons a risk acceptance can NEVER override: an explicit operator
# disable, an undeclared capability and a broken manifest outrank a one-time
# experiment acceptance (Q01/Q02: blocked regardless of any evidence). Only an
# unblocked provider or one ABSENT from the catalog entirely (a directly-passed
# scripted experiment object the acceptance names exactly) is coverable.
_RISK_COVERABLE_BLOCKS = (None, "provider_absent")


def bridge_admission_from_state(qualification: dict) -> dict:
    """Pure admission predicate for a generative bridge over a
    :func:`qualification_state` dict — qualified only at
    ``>= PRODUCTION_READY``, not BLOCKED, not STALE; else a
    ``BRIDGE_NOT_QUALIFIED`` refusal. No I/O. (The one-time risk-acceptance
    path lives in :func:`bridge_admission`, which owns the durable store.)"""
    level = qualification.get("level")
    stale = bool(qualification.get("stale"))
    blocked = qualification.get("blocked_reason")
    min_rung = _ORDER.get(BRIDGE_MIN_QUALIFICATION, 0)
    qualified = _ORDER.get(level, -1) >= min_rung and not blocked and not stale
    if qualified:
        return {"admitted": True, "refusal": None, "level": level,
                "state": qualification.get("state"),
                "min_required": BRIDGE_MIN_QUALIFICATION,
                "reason": f"qualified to {level} (>= {BRIDGE_MIN_QUALIFICATION})"}
    if blocked:
        why = f"provider blocked: {blocked}"
    elif stale:
        why = f"qualification stale (rung fell back to {level})"
    else:
        why = (f"qualified only to {level}; a real generative bridge needs "
               f">= {BRIDGE_MIN_QUALIFICATION} (run provider qualification "
               f"first, or record a one-time operator risk acceptance for this "
               f"exact request digest)")
    return {"admitted": False, "refusal": BRIDGE_REFUSAL, "level": level,
            "state": qualification.get("state"),
            "blocked_reason": blocked,
            "min_required": BRIDGE_MIN_QUALIFICATION, "reason": why}


def bridge_admission(provider_id: str, capability: str = "generative_bridge", *,
                     project: Any | None = None, evidence: dict | None = None,
                     fixtures_dir: Path | None = None,
                     request_digest: str | None = None) -> dict:
    """Whether a REAL generative bridge may be dispatched through ``provider_id``.
    Reuses the ladder end to end (like the reviewer / analyzer gates), floored
    at PRODUCTION_READY. Below the floor, a low-rung MANUAL experiment may be
    admitted exactly once by an operator risk acceptance bound to this precise
    ``request_digest`` (recorded + consumed durably — closeout §2 item 8). No
    real video provider reaches the floor here, so the real bridge stays
    SKIPPED_WITH_EVIDENCE and this refusal is its stand-in."""
    try:
        declared = declared_facts(provider_id, capability, fixtures_dir=fixtures_dir)
    except Exception as exc:
        declared = {"exists": False, "manifest_error": str(exc)}
    ev = evidence
    if ev is None and project is not None:
        try:
            ev, err = _stored_evidence(project, provider_id, capability)
        except Exception:
            ev, err = None, EVIDENCE_CORRUPT
        if err == EVIDENCE_CORRUPT:
            # a corrupt store also invalidates the risk-acceptance path — it
            # rides the same stream, so nothing here can vouch for anything.
            return _evidence_corrupt_refusal(
                BRIDGE_REFUSAL, BRIDGE_MIN_QUALIFICATION, provider_id, capability)
    q = qualification_state(provider_id, capability, evidence=ev, declared=declared)
    decision = bridge_admission_from_state(q)
    decision.update({"provider_id": provider_id, "capability": capability})
    if (not decision["admitted"] and project is not None and request_digest
            and q.get("blocked_reason") in _RISK_COVERABLE_BLOCKS):
        acceptance = _consume_risk_acceptance(project, provider_id, capability,
                                              request_digest)
        if acceptance is not None:
            decision = {
                "admitted": True, "refusal": None, "level": q.get("level"),
                "state": q.get("state"), "min_required": BRIDGE_MIN_QUALIFICATION,
                "risk_accepted": True,
                "acceptance_id": acceptance.get("acceptance_id"),
                "reason": ("one-time operator risk acceptance consumed for this "
                           "exact provider/capability/request digest (single-use "
                           "manual experiment path — closeout §2 item 8)"),
                "provider_id": provider_id, "capability": capability,
            }
    return decision


# ============================================================ WP3 declared-vs-observed


def _declared_vs_observed(manifest: Any, fixture: dict,
                          artifact: dict | None) -> list[dict]:
    """Render the contract §7 Declared / Observed / Not-tested lines — a paid
    fact is NEVER inferred beyond what the ONE canary actually exercised, and
    the manifest is NEVER written back (WP3)."""
    rows: list[dict] = []
    lim = getattr(manifest, "limits", None)
    probe = (artifact or {}).get("probe") or {}

    declared_dur = getattr(lim, "max_duration_ms", None) if lim else None
    obs_dur = probe.get("duration_ms")
    rows.append({
        "attribute": "duration",
        "declared": f"max {declared_dur} ms" if declared_dur else "unset",
        "observed": (f"canary {obs_dur} ms passed" if obs_dur is not None
                     else "not probed (scripted/fake media)"),
        "not_tested": (f"the {declared_dur} ms boundary"
                       if declared_dur else "any longer duration"),
    })

    exp_size = fixture.get("expected_frame_size")
    obs_size = ([probe.get("width"), probe.get("height")]
                if probe.get("width") else None)
    rows.append({
        "attribute": "frame_size",
        "declared": f"opaque max_resolution {getattr(lim, 'max_resolution', None)!r}"
                    if lim and getattr(lim, "max_resolution", None) else "unset",
        "observed": (f"canary {obs_size[0]}x{obs_size[1]} passed"
                     if obs_size else "not probed"),
        "not_tested": "every other resolution / aspect ratio",
    })

    rows.append({
        "attribute": "reference_inputs",
        "declared": f"image_mode={getattr(getattr(manifest, 'refs', None), 'image_mode', 'none')}",
        "observed": "single fixed fixture input" if fixture.get("input_media")
                    else "none delivered",
        "not_tested": "multi-ref / first+last-frame / all ref counts",
    })
    return rows


# ============================================================ artifact checks


def _scan_forbidden_keys(obj: Any, forbidden_names: list[str]) -> list[str]:
    """Every key path in ``obj`` whose NAME matches a forbidden secret field
    (contract §5 forbidden_secret_fields, test 12). Value-agnostic — it is the
    presence of a secret-NAMED field in a receipt/evidence that must never
    happen."""
    forbidden = {n.lower() for n in (forbidden_names or [])}
    leaked: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k).lower() in forbidden:
                    leaked.append((path + "." + str(k)).lstrip("."))
                walk(v, path + "." + str(k))
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(obj, "")
    return leaked


def _validate_artifact(fixture: dict, receipt: dict, artifact: dict) -> dict:
    """Check the downloaded canary artifact + its receipt against the fixture
    contract (contract §6 download semantics, test 8/12). Returns
    ``{checks: [...], ok: bool}``. A missing probe (fake media) degrades the
    frame-size / duration checks to SKIPPED_WITH_EVIDENCE rather than a false
    pass — honest for the scripted path."""
    checks: list[dict] = []

    def add(name: str, ok: bool | None, detail: Any) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    for field in fixture.get("required_receipt_fields", []):
        add(f"receipt.{field}", receipt.get(field) not in (None, ""),
            receipt.get(field))

    leaked = _scan_forbidden_keys({"receipt": receipt, "artifact": artifact},
                                  fixture.get("forbidden_secret_fields", []))
    add("no_forbidden_secret_fields", not leaked, leaked)

    add("artifact_nonempty", (artifact.get("byte_size") or 0) > 0,
        artifact.get("byte_size"))
    add("artifact_hashed", bool(artifact.get("content_sha256")),
        artifact.get("content_sha256"))

    probe = artifact.get("probe") or {}
    exp_size = fixture.get("expected_frame_size")
    if exp_size:
        if probe.get("width") and probe.get("height"):
            add("frame_size", [probe["width"], probe["height"]] == list(exp_size),
                [probe.get("width"), probe.get("height")])
        else:
            add("frame_size", None, "SKIPPED_WITH_EVIDENCE: no media probe")
    exp_dur = fixture.get("expected_duration_range_ms")
    if exp_dur:
        if probe.get("duration_ms") is not None:
            lo, hi = exp_dur
            add("duration_ms", lo <= probe["duration_ms"] <= hi,
                probe.get("duration_ms"))
        else:
            add("duration_ms", None, "SKIPPED_WITH_EVIDENCE: no media probe")

    ok = all(c["ok"] for c in checks if c["ok"] is not None)
    return {"checks": checks, "ok": ok}


# ============================================================ orchestration


class CanaryError(RuntimeError):
    """A canary refusal that is NOT a spend (config/argument/gate) — carries a
    stable ``code`` so the CLI can branch and a test can assert transport 0."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def qualify(
    project: Any,
    provider_id: str,
    capability: str,
    *,
    mode: str = "dry_run",
    max_cost: float | None = None,
    assume_yes: bool = False,
    transport: Callable | None = None,
    recovery_drill: bool = False,
    health_probe: bool = False,
    probe_opener: Callable | None = None,
    fixtures_dir: Path | None = None,
    now: str | None = None,
    write: bool = True,
) -> dict:
    """Run a canary qualification and return (and, by default, persist) the
    DERIVED qualification report.

    ``mode='dry_run'`` validates config + fixture + estimate with NO network and
    records ``DRY_RUN_VALID``. ``mode='run'`` is the operator-gated real canary:
    it is REFUSED unless ``max_cost`` is given and the estimate fits under it,
    passes through the existing ``ask_before`` spend gate (unless ``assume_yes``),
    then drives ONE submission (no fallback) through the standard admission path.

    ``transport`` is the injection seam: ``None`` uses the real stdlib transport
    (a genuine paid call — operator-gated); tests pass a scripted transport so
    the whole path lands offline. ``recovery_drill`` additionally re-runs the
    EXISTING recovery drill (delete SQLite → rebuild → poll-only resume) against
    the canary project to earn ``RECOVERY_PASSED`` (scripted path only)."""
    from .manifest import estimate_cost, load_manifests

    manifests, _ = load_manifests()
    manifest = manifests.get(provider_id)
    declared = declared_facts(provider_id, capability, fixtures_dir=fixtures_dir)
    now = now or _now_iso()

    # BLOCKED short-circuit — nothing to qualify.
    if not declared.get("exists", False):
        return _finish(project, provider_id, capability, declared, evidence=None,
                       mode=mode, transport_kind="none", now=now,
                       estimate=None, currency=None, max_cost=max_cost,
                       manifest=manifest, fixture=None, extra={}, write=write)

    fixture = load_fixture(capability, fixtures_dir=fixtures_dir)
    currency = getattr(getattr(manifest, "cost", None), "currency", None)
    # SAME default as _run_canary's actual request (`or 1000`): estimating a
    # missing fixture duration as 0 priced a per-second provider's canary at
    # 0, waving it past --max-cost and the ask_before gate while the real
    # submission carried 1000ms.
    duration_ms = int(fixture.get("duration_ms") or 1000)
    estimate = (estimate_cost(manifest, duration_ms, candidates=1)
                if manifest is not None else 0.0)

    extra: dict[str, Any] = {
        "estimated_cost": estimate,
        "currency": currency,
        "max_cost": max_cost,
        "fixture_id": fixture.get("fixture_id"),
        "fixture_version": fixture.get("fixture_version"),
        "data_handling": _data_handling(manifest),
        "declared_vs_observed": _declared_vs_observed(manifest, fixture, None),
    }

    # WP4 free health/quota probe (test 17): GET-only, and it NEVER triggers a
    # submit. A failing probe is recorded, not fatal, and can never substitute
    # for a real canary (§8) — it leaves the level at the config/dry-run floor.
    if health_probe and manifest is not None:
        extra["health_probe"] = _run_health_probe(manifest, opener=probe_opener)

    if mode == "dry_run":
        evidence = _dry_run_evidence(declared, fixture, estimate, currency, now)
        return _finish(project, provider_id, capability, declared, evidence=evidence,
                       mode=mode, transport_kind="none", now=now, estimate=estimate,
                       currency=currency, max_cost=max_cost, manifest=manifest,
                       fixture=fixture, extra=extra, write=write)

    if mode != "run":
        raise CanaryError(f"unknown canary mode {mode!r}", code="bad_mode")

    # ---- operator-gated real-canary refusals (all pre-transport, spend 0) ----
    if manifest is None or manifest.adapter not in _RUN_CANARY_ADAPTERS:
        raise CanaryError(
            f"{provider_id}: the submit/artifact canary currently drives "
            f"generic_cloud providers only; {declared.get('adapter')!r} qualifies "
            f"to DRY_RUN_VALID here (real submit canary SKIPPED_WITH_EVIDENCE)",
            code="adapter_not_runnable")
    if not declared.get("config_ok"):
        raise CanaryError(
            f"{provider_id}: offline config invalid — fix it before a paid canary "
            f"({'; '.join(declared.get('config_problems') or [])})",
            code="config_invalid")
    if max_cost is None:
        raise CanaryError(
            f"{provider_id}: --run requires an explicit --max-cost (§5, stop "
            f"condition: a real canary must be cost-bounded)", code="max_cost_required")
    if estimate > float(max_cost):
        raise CanaryError(
            f"{provider_id}: canary estimate {estimate} {currency or ''} exceeds "
            f"--max-cost {max_cost} — refusing (§5)", code="estimate_over_max_cost")
    fixture_cap = fixture.get("max_estimated_cost")
    if fixture_cap is not None and estimate > float(fixture_cap):
        raise CanaryError(
            f"{provider_id}: canary estimate {estimate} exceeds the fixture's own "
            f"max_estimated_cost {fixture_cap} — refusing (§5)",
            code="estimate_over_fixture_cap")

    # existing ask_before spend gate (§8.3) — the human yes for real spend.
    _spend_gate(project, estimate, currency, assume_yes=assume_yes)

    transport_kind = "real" if transport is None else "scripted"
    run = _run_canary(project, manifest, capability, fixture,
                      transport=transport, recovery_drill=recovery_drill,
                      fixtures_dir=fixtures_dir)
    extra.update({
        "canary_project": run["canary_project_rel"],
        "receipt": run["receipt"],
        "artifact_checks": run["artifact_checks"],
        "recovery": run.get("recovery"),
        "declared_vs_observed": _declared_vs_observed(manifest, fixture,
                                                      run["artifact"]),
    })

    evidence = _run_evidence(declared, fixture, run, estimate, currency,
                             transport_kind, now)
    return _finish(project, provider_id, capability, declared, evidence=evidence,
                   mode=mode, transport_kind=transport_kind, now=now,
                   estimate=estimate, currency=currency, max_cost=max_cost,
                   manifest=manifest, fixture=fixture, extra=extra, write=write)


def _dry_run_evidence(declared: dict, fixture: dict, estimate: float,
                      currency: str | None, now: str) -> dict:
    """DRY_RUN_VALID evidence — config passed + estimate computed, NO network.

    An INVALID config earns UNTESTED, not CONFIG_VALID: recorded evidence
    claiming a rung the dry run just disproved would prop the derived level at
    CONFIG_VALID (``_max_level(floor, ev_level)``) even while the live doctor
    keeps failing."""
    return {
        "level": DRY_RUN_VALID if declared.get("config_ok") else UNTESTED,
        "transport": "none",
        "provider_profile_digest": declared.get("provider_profile_digest"),
        "adapter_semantic_digest": declared.get("adapter_semantic_digest"),
        "fixture_version": fixture.get("fixture_version"),
        "request_digest": None,
        "evidence_refs": [],
        "artifact": None,
        "cost": {"estimated": estimate, "currency": currency, "actual": None},
        "checked_at": now,   # AUDIT ONLY
    }


def _run_evidence(declared: dict, fixture: dict, run: dict, estimate: float,
                  currency: str | None, transport_kind: str, now: str) -> dict:
    level = CANARY_SUBMIT_PASSED
    if run["artifact_checks"]["ok"]:
        level = CANARY_ARTIFACT_PASSED
        # the ladder is MONOTONIC: recovery can only promote a run whose
        # artifact checks passed — a failed artifact must never reach
        # RECOVERY_PASSED (nor, via a real transport, PRODUCTION_READY)
        if run.get("recovery", {}).get("passed"):
            level = RECOVERY_PASSED
    # PRODUCTION_READY is minted ONLY from a genuinely real transport that
    # cleared every rung — never from the scripted path (addendum honesty).
    if transport_kind == "real" and level == RECOVERY_PASSED:
        level = PRODUCTION_READY
    return {
        "level": level,
        "transport": transport_kind,
        "provider_profile_digest": declared.get("provider_profile_digest"),
        "adapter_semantic_digest": declared.get("adapter_semantic_digest"),
        "fixture_version": fixture.get("fixture_version"),
        "request_digest": run["request_digest"],
        "response_schema_digest": run.get("response_schema_digest"),
        "evidence_refs": run["evidence_refs"],
        "artifact": run["artifact"],
        "cost": {"estimated": estimate, "currency": currency,
                 "actual": run["receipt"].get("cost")},
        "checked_at": now,   # AUDIT ONLY
    }


def _finish(project, provider_id, capability, declared, *, evidence, mode,
            transport_kind, now, estimate, currency, max_cost, manifest,
            fixture, extra, write) -> dict:
    derived = qualification_state(provider_id, capability,
                                  evidence=evidence, declared=declared)
    report = {
        "schema": SCHEMA,
        "provider_id": provider_id,
        "capability": capability,
        "state": derived["state"],
        "level": derived["level"],
        "stale": derived["stale"],
        "blocked_reason": derived["blocked_reason"],
        "reasons": derived["reasons"],
        "bindings": derived["bindings"],
        "mode": mode,
        "transport": transport_kind,
        "kind": declared.get("kind"),
        "enabled": declared.get("enabled"),
        "estimated_cost": estimate,
        "currency": currency,
        "max_cost": max_cost,
        "checked_at": now,   # AUDIT metadata ONLY — never in bindings/identity
        "environment_note": (
            "no real provider account in this environment — the real-canary path "
            "is exercised via a scripted transport; PRODUCTION_READY needs a real "
            "operator canary") if transport_kind == "scripted" else None,
        "evidence": evidence,
    }
    report.update(extra)
    if write:
        # DURABLE evidence FIRST (the admission truth — append-only events
        # stream), THEN the deletable display projection (closeout §2 item 1).
        if evidence is not None:
            record_qualification_evidence(project, provider_id, capability, evidence)
        write_report(project, report)
    return report


# ---- the standard-path canary (one submission, no fallback) ----------------


def _run_canary(project, manifest, capability, fixture, *, transport,
                recovery_drill, fixtures_dir) -> dict:
    """Drive ONE canary submission through the STANDARD admission/evidence path
    inside a disposable canary project (addendum ruling 2), then read back the
    receipt + artifact facts. NEVER goes through the fallback chain — the canary
    is a chain of one (§5)."""
    from ..build import attempts as A
    from .base import GenerationRequest
    from .generic_cloud import GenericCloudProvider

    canary_project, rel = _canary_project(project, manifest.id, capability)
    recorder = _SchemaRecorder(transport)
    provider = GenericCloudProvider(manifest, transport=recorder,
                                    sleep_fn=lambda _s: None)
    req = _canary_request(canary_project, fixture)

    takes = provider.generate(req)   # PREPARED→DISPATCHING→ADMITTED→SUCCESS
    if not takes:
        raise CanaryError(f"{manifest.id}: canary produced no take", code="no_take")

    events, _ = A.read_submission_events(canary_project)
    sid = events[-1]["submission_id"] if events else None
    request_digest = events[-1]["request_digest"] if events else None
    states = [e.get("to") for e in events]
    evidence_refs = [{"submission_id": e.get("submission_id"),
                      "event_id": e.get("event_id"), "to": e.get("to")}
                     for e in events]

    take = takes[0]
    media = take.media_path
    probe = take.sidecar.probe.model_dump() if take.sidecar.probe else None
    artifact = {
        "media_name": Path(media).name if media else None,
        "content_sha256": hash_file(media) if media and Path(media).is_file() else None,
        "byte_size": Path(media).stat().st_size if media and Path(media).is_file() else 0,
        "probe": probe,
    }
    remote = take.sidecar.remote
    receipt = {
        "job_id": remote.job_id if remote else None,
        "cost": remote.cost if remote else None,
        "currency": remote.currency if remote else None,
        "submission_id": sid,
        "request_digest": request_digest,
        "states": states,
    }
    artifact_checks = _validate_artifact(fixture, receipt, artifact)

    out = {
        "canary_project_rel": rel,
        "request_digest": request_digest,
        "response_schema_digest": recorder.schema_digest(),
        "evidence_refs": evidence_refs,
        "artifact": artifact,
        "receipt": receipt,
        "artifact_checks": artifact_checks,
    }
    if recovery_drill:
        out["recovery"] = _recovery_drill(canary_project, provider, req,
                                          request_digest, manifest.id)
    return out


def _skeleton(doc: Any, prefix: str = "") -> list[str]:
    """The nested key skeleton of a JSON document (values dropped) — the shape a
    schema-drift compare is over."""
    if isinstance(doc, dict):
        out: list[str] = []
        for k in sorted(doc, key=str):
            out += _skeleton(doc[k], f"{prefix}.{k}")
        return out or [prefix + "{}"]
    if isinstance(doc, list):
        return [prefix + "[]"]
    return [prefix]


class _SchemaRecorder:
    """Wraps the canary transport to fingerprint the SUBMIT/POLL response
    SHAPES (keys only, never values) so a later provider response-schema drift
    moves ``response_schema_digest`` — the staleness anchor for test 16. Binary
    download responses are skipped. It NEVER alters the response it passes
    through, so the admission path is byte-identical to the raw transport."""

    def __init__(self, inner: Callable | None):
        from .generic_cloud import default_transport

        self._inner = inner or default_transport
        self._schemas: list[list[str]] = []

    def __call__(self, method, url, headers, body):
        resp = self._inner(method, url, headers, body)
        try:
            import json as _json

            doc = _json.loads(resp.body.decode("utf-8"))
            self._schemas.append(_skeleton(doc))
        except Exception:
            pass  # binary (download) / non-JSON — no schema to record
        return resp

    def schema_digest(self) -> str:
        return hash_value(sorted(self._schemas))


def _canary_project(project: Any, provider_id: str, capability: str):
    """Create (or reuse) the disposable canary project under the user project's
    ``.manju/`` runtime area (addendum ruling 2). Deleting it loses qualification
    HISTORY only, never truth."""
    from ..core.container import Project

    base = Path(project.root) / ".manju" / "canary"
    name = f"{provider_id}__{capability}"
    target = base / name
    manju_dir = base / f"{name}.manju"
    if not (manju_dir / "project.yaml").exists():
        Project.create(target, git_init=False)
    canary = Project(manju_dir)
    rel = manju_dir.relative_to(Path(project.root)).as_posix()
    return canary, rel


def _canary_request(canary_project: Any, fixture: dict):
    from ..core.models import ShotSpec
    from .base import GenerationRequest

    prompt = str(fixture.get("prompt") or fixture.get("text") or "canary")
    duration_ms = int(fixture.get("duration_ms") or 1000)
    shot = ShotSpec.model_validate({
        "id": "canary",
        "duration": max(duration_ms / 1000.0, 0.1),
        "generation": {"prompt_override": prompt},
        "dialogue": {"text": prompt},
    })
    canary_project.save_shot(shot)
    index = canary_project.load_index()
    if "canary" not in index.order:
        index.order.append("canary")
        canary_project.save_index(index)
    return GenerationRequest(
        project=canary_project, shot=shot, bible=canary_project.load_bible(),
        spec_hash="sha256:canary", duration_ms=duration_ms, candidates=1,
        params={"seed": 7})


def _recovery_drill(canary_project, provider, req, digest, provider_id) -> dict:
    """RECOVERY_PASSED derives from RE-RUNNING the existing recovery drill
    against the canary project (addendum ruling 5): seed a valid ADMITTED chain
    for the SAME request digest, delete SQLite, rebuild from evidence, and prove
    a fresh generate RESUMES poll-only (submit count 0 — never a resubmit). This
    reuses providers.submission / runtime.state.rebuild verbatim; it does not
    re-implement the P0/FA recovery machinery."""
    from ..build import attempts as A
    from ..providers import submission as S
    from ..runtime.state import RuntimeState

    sid = "sub_canary_recover"
    execution_profile = provider._execution_profile(req)
    execution_profile_digest = (
        execution_profile.digest if execution_profile is not None else None
    )
    execution_profile_snapshot = (
        execution_profile.to_dict() if execution_profile is not None else None
    )
    prev_digest = None
    prev_state = None
    for to_state, job in ((S.PREPARED, None), (S.DISPATCHING, None),
                          (S.ADMITTED, "job_canary_recover")):
        rec = A.append_submission_event(
            canary_project, submission_id=sid, request_digest=digest,
            from_state=prev_state, to_state=to_state, provider_id=provider_id,
            shot=req.shot.id, remote_job_id=job, prev_event_digest=prev_digest,
            execution_profile_digest=execution_profile_digest,
            execution_profile=execution_profile_snapshot)
        prev_digest = S.submission_event_digest(rec)
        prev_state = to_state

    shutil.rmtree(Path(canary_project.root) / ".manju")
    with RuntimeState(canary_project.root) as st:
        stats = st.rebuild(canary_project)
        restored = [r for r in st.unresolved_submissions()
                    if r["submission_id"] == sid]

    calls = {"n": 0}
    orig_submit = provider.submit

    def counting_submit(r):
        calls["n"] += 1
        return orig_submit(r)

    provider.submit = counting_submit
    try:
        takes = provider.generate(req)   # must RESUME poll-only
    finally:
        provider.submit = orig_submit

    final = _states(canary_project, sid)
    passed = (bool(restored) and restored[0]["state"] == S.ADMITTED
              and calls["n"] == 0 and bool(takes)
              and final[-1:] == [S.TERMINAL_SUCCESS])
    return {
        "passed": passed,
        "submissions_restored": stats.get("submissions_restored"),
        "restored_state": restored[0]["state"] if restored else None,
        "resubmit_calls": calls["n"],   # 0 == poll-only resume, no double-charge
        "final_state": final[-1] if final else None,
    }


def _states(project, sid):
    from ..build import attempts as A

    recs, _ = A.read_submission_events(project, sid)
    return [e.get("to") for e in recs]


# ---- gates + probes reused wholesale ---------------------------------------


def _spend_gate(project, estimate, currency, *, assume_yes) -> None:
    from ..build.graph import WaitingUser, spend_gate

    try:
        spend_gate(project, float(estimate or 0), currency, assume_yes=assume_yes,
                   hint="确认后再跑真实 canary(单次提交,不兜底,受 --max-cost 约束)")
    except WaitingUser as exc:
        raise CanaryError(str(exc), code="waiting_user") from exc


def _run_health_probe(manifest, *, opener) -> dict:
    """WP4 (addendum ruling 7): the free, GET-only health/quota probe SLOT. It
    NEVER triggers a submit. No real provider declares a documented free
    endpoint in this repo, so a probe with nothing to hit is SKIPPED_WITH_
    EVIDENCE — a health signal can never substitute for a real canary (§8)."""
    from .manifest import reachability_probe

    ok, detail = reachability_probe(manifest, opener=opener)
    if ok is None:
        status = "skipped_with_evidence"
    elif ok:
        status = "healthy"
    else:
        status = "unreachable"
    return {"status": status, "detail": detail,
            "note": "GET-only; never triggers submit; not a substitute for a "
                    "real canary (§8)"}


def _data_handling(manifest) -> dict | None:
    """WP4 (addendum ruling 7): the DECLARED data-handling facts, surfaced
    VERBATIM with their source ref. These are human-decision material — a
    technical canary can NEVER verify them as true (contract §8, test 19), so
    they ride the report tagged ``declared``, never ``observed``."""
    dh = getattr(manifest, "data_handling", None)
    if dh is None:
        return None
    fields = {k: getattr(dh, k, None) for k in
              ("region", "retention", "training_opt_out", "deletion_url", "source_ref")}
    if not any(v is not None for v in fields.values()):
        return None
    return {"status": "declared", "verified": False, **fields,
            "note": "declared by the operator; NOT verified by any canary (§8)"}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================ matrix (WP0/§9)


def qualification_matrix(project: Any, *, fixtures_dir: Path | None = None) -> dict:
    """The capability × qualification-level matrix (WP0 audit + ``manju providers
    qualification`` with no id). Each row re-derives the CURRENT state from live
    declared facts + any recorded (deletable) evidence, so a stale report reads
    as STALE and a changed manifest is reflected immediately."""
    from .catalog import iter_provider_descriptors

    descriptors, errors = iter_provider_descriptors()
    rows: list[dict] = []
    for d in descriptors:
        caps = list(d.capabilities) or [d.provider_type]
        for capability in caps:
            declared = declared_facts(d.provider_id, capability,
                                      fixtures_dir=fixtures_dir)
            evidence, ev_err = _stored_evidence(project, d.provider_id, capability)
            if ev_err == EVIDENCE_CORRUPT:
                # fail closed (closeout §2 item 10): a torn/tampered durable
                # store cannot vouch for any rung — the row is BLOCKED, never
                # silently the live floor.
                rows.append({
                    "provider_id": d.provider_id, "capability": capability,
                    "kind": d.kind, "enabled": d.enabled,
                    "state": BLOCKED, "level": UNTESTED, "stale": False,
                    "blocked_reason": EVIDENCE_CORRUPT, "has_evidence": False,
                    "reasons": ["durable qualification evidence corrupt/"
                                "unreadable — failing closed (transport 0)"],
                })
                continue
            derived = qualification_state(d.provider_id, capability,
                                          evidence=evidence, declared=declared)
            rows.append({
                "provider_id": d.provider_id,
                "capability": capability,
                "kind": d.kind,
                "enabled": d.enabled,
                "state": derived["state"],
                "level": derived["level"],
                "stale": derived["stale"],
                "blocked_reason": derived["blocked_reason"],
                "has_evidence": evidence is not None,
                "reasons": derived["reasons"],
            })
    return {"schema": SCHEMA, "rows": rows, "errors": errors}
