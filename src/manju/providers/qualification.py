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
    current declared value (§3). Only compares anchors present on BOTH sides —
    a missing declared/recorded anchor is never treated as drift."""
    ev, cur = evidence or {}, declared or {}
    return [k for k in STALENESS_ANCHORS
            if ev.get(k) is not None and cur.get(k) is not None
            and ev.get(k) != cur.get(k)]


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

    config_ok = bool(declared.get("config_ok"))
    floor = CONFIG_VALID if (exists and config_ok) else UNTESTED
    if exists and not config_ok:
        probs = declared.get("config_problems") or []
        reasons.append("config_invalid" + (": " + "; ".join(probs) if probs else ""))

    stale = False
    ev_level = (evidence or {}).get("level")
    if evidence and ev_level in _ORDER:
        drift = staleness_drift(evidence, declared)
        if drift:
            stale = True
            reasons.append("stale:" + ",".join(drift))
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
# The qualification report is a DELETABLE derived JSON (addendum ruling 2):
# reports/providers/qualification/<provider>__<capability>.json. It is NEVER a
# build/resume/cache input — deleting it loses qualification HISTORY, never
# truth. A test pins that no build path reads this directory (contract test 15).


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


def _stored_evidence(project: Any, provider_id: str, capability: str) -> dict | None:
    """The recorded evidence (from a prior report) that feeds the pure
    derivation — never trusted as truth, only as a claim to re-derive against
    the CURRENT declared facts (so a stale report re-reads as STALE)."""
    report = read_report(project, provider_id, capability)
    return report.get("evidence") if report else None


# =============================================== AI_IDE_15 reviewer admission gate
#
# The cloud-visual reviewer (AI_IDE_15 §2, addendum ruling 1) may dispatch a real
# VLM review ONLY when the vision provider is qualified. This gate lives in the
# PROVIDER layer (it consumes the ladder above); the qc package must not couple to
# the qualification report (the build-boundary guard §15). Core imports NO vendor
# SDK: this is a pure qualification check, never a transport. The offline fake
# reviewer double drives record_verdicts directly and never reaches this gate.

# a review needs at least this rung; below it (or blocked/stale) → refusal.
REVIEWER_MIN_QUALIFICATION = DRY_RUN_VALID
REVIEWER_REFUSAL = "REVIEWER_NOT_QUALIFIED"


def reviewer_admission_from_state(qualification: dict) -> dict:
    """Pure admission predicate over a :func:`qualification_state` dict. A cloud
    VLM review may be dispatched ONLY when the provider is qualified to at least
    :data:`REVIEWER_MIN_QUALIFICATION`, is not BLOCKED and is not STALE; anything
    else is a structured ``REVIEWER_NOT_QUALIFIED`` refusal — never a silent
    proceed. No I/O, so both branches are testable without a real provider."""
    level = qualification.get("level")
    stale = bool(qualification.get("stale"))
    blocked = qualification.get("blocked_reason")
    min_rung = _ORDER.get(REVIEWER_MIN_QUALIFICATION, 0)
    qualified = _ORDER.get(level, -1) >= min_rung and not blocked and not stale
    if qualified:
        return {"admitted": True, "refusal": None, "level": level,
                "state": qualification.get("state"),
                "min_required": REVIEWER_MIN_QUALIFICATION,
                "reason": f"qualified to {level} (>= {REVIEWER_MIN_QUALIFICATION})"}
    if blocked:
        why = f"provider blocked: {blocked}"
    elif stale:
        why = f"qualification stale (rung fell back to {level})"
    else:
        why = (f"qualified only to {level}; a cloud VLM review needs "
               f">= {REVIEWER_MIN_QUALIFICATION} (run provider qualification first)")
    return {"admitted": False, "refusal": REVIEWER_REFUSAL, "level": level,
            "state": qualification.get("state"),
            "min_required": REVIEWER_MIN_QUALIFICATION, "reason": why}


def reviewer_admission(provider_id: str, capability: str = "vision", *,
                       project: Any | None = None,
                       evidence: dict | None = None,
                       fixtures_dir: Path | None = None) -> dict:
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
            ev = _stored_evidence(project, provider_id, capability)
        except Exception:
            ev = None
    q = qualification_state(provider_id, capability, evidence=ev, declared=declared)
    decision = reviewer_admission_from_state(q)
    decision.update({"provider_id": provider_id, "capability": capability})
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
    duration_ms = int(fixture.get("duration_ms") or 0)
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
    """DRY_RUN_VALID evidence — config passed + estimate computed, NO network."""
    return {
        "level": DRY_RUN_VALID if declared.get("config_ok") else CONFIG_VALID,
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
    prev_digest = None
    prev_state = None
    for to_state, job in ((S.PREPARED, None), (S.DISPATCHING, None),
                          (S.ADMITTED, "job_canary_recover")):
        rec = A.append_submission_event(
            canary_project, submission_id=sid, request_digest=digest,
            from_state=prev_state, to_state=to_state, provider_id=provider_id,
            shot=req.shot.id, remote_job_id=job, prev_event_digest=prev_digest)
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
            evidence = _stored_evidence(project, d.provider_id, capability)
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
