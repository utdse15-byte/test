"""DR03A — external ShotDraftPackage v1: validate / inspect / controlled apply.

An external creative tool (an "AI IDE") hands Manju a *proposal*: a structured,
multi-shot ``manju.shot-draft-package/v1`` YAML file describing shots it would
like created, each with the source facts it is grounded in and SOFT creative
suggestions. This module turns that proposal into truth — but only on Manju's
terms:

- **The package is never a source of truth.** ``creative_suggestions`` are SOFT.
  They never auto-promote to ``quality.must_show`` / ``quality.avoid``, a
  continuity lock, routing, or a locked duration. ``visual_prompt`` /
  ``negative_prompt`` / ``style_tags`` / ``continuity_notes`` / ``confidence`` /
  ``duration_budget_ms`` / ``provenance`` / ``review_notes`` / ``source_spans``
  are ALWAYS omitted from a created shot (the forbidden-mapping table — each
  omission is listed in the plan with a reason and where the value lives
  instead).
- **fragment / generation-unit ids never mint Shot identity.** ``draft_id`` is
  provenance; an empty ``proposed_shot_id`` is allocated the next free ``S###``.
- **Default capability = CREATE new shots + append index.** An op that targets
  an EXISTING shot id is a CONFLICT, never an overwrite — existing-shot changes
  go through ``manju propose``.
- **Inspect is ZERO-WRITE.** :func:`build_shot_import_plan` reads only.
  :func:`apply_shot_import_plan` is the only writer, and it reuses ONLY the
  existing controlled-write machinery — ``Project.save_shot`` / index save
  (atomic, §3), ``core.check.run_check`` (§4/§5), ``core.events.append_event``,
  ``core.hashing`` — under one ``build_lock``, CAS-guarded, with a compensating
  rollback and a post-apply check. No model calls, no ``selected_take`` change,
  no media writes, no new merge engine, no new DB.

The CLI entry is ``manju shot-package FILE [--apply] [--json]`` (build/cli.py),
following the ``roundtrip`` precedent. There is no MCP tool: the external-file
apply command class (``ingest`` / ``roundtrip`` / ``import``) is not exposed on
MCP today, so this one is not either (parity).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from ..core.check import SECRET_PATTERNS, run_check
from ..core.events import append_event
from ..core.hashing import canonical_json, hash_text, hash_value
from ..core.idents import UnsafeIdentifierError, validate_safe_segment, windows_relpath_problems
from ..core.models import SHOT_SIZES, Camera, ShotSpec
from ..core.yamlio import atomic_write_text, dump_yaml

if TYPE_CHECKING:
    from ..core.container import Project

__all__ = [
    "PACKAGE_SCHEMA",
    "PLAN_SCHEMA",
    "MAX_PACKAGE_BYTES",
    "ShotPackageError",
    "load_package",
    "semantic_digest",
    "project_revision",
    "build_shot_import_plan",
    "apply_shot_import_plan",
]

PACKAGE_SCHEMA = "manju.shot-draft-package/v1"
PACKAGE_SCHEMA_PREFIX = "manju.shot-draft-package/"
PACKAGE_SCHEMA_MAJOR = 1
PLAN_SCHEMA = "manju.shot-import-plan/v1"
MAX_PACKAGE_BYTES = 2 * 1024 * 1024  # 2 MiB — enforced BEFORE parse

_KNOWN_TOP_KEYS = {"schema", "package_id", "created_at", "producer", "source", "shots"}
_KNOWN_SHOT_KEYS = {
    "draft_id", "proposed_shot_id", "source_facts", "creative_suggestions",
    "execution_constraints", "review_notes", "provenance",
}
# Camera model fields a suggestion may map into (verified against core/models.py).
_CAMERA_FIELDS = set(Camera.model_fields)

# The forbidden-mapping table: fields ALWAYS omitted from a created shot, each
# with the reason AND where the value goes instead (plan/events, or a manual
# author step). Dotted keys are looked up under the package shot.
_FORBIDDEN_REASONS: dict[str, str] = {
    "creative_suggestions.visual_prompt":
        "would shadow prompt compilation — NOT written; the author may hand-copy it "
        "into generation.prompt_override later",
    "creative_suggestions.negative_prompt":
        "would shadow prompt compilation — NOT written; the author may hand-copy it "
        "into generation.prompt_override later",
    "creative_suggestions.style_tags":
        "soft styling — never an auto continuity/quality lock and never a bible write",
    "creative_suggestions.continuity_notes":
        "soft note — never an auto continuity lock and never a bible write",
    "creative_suggestions.confidence":
        "advisory score — never auto-approves or auto-rejects a shot",
    "execution_constraints.duration_budget_ms":
        "no matching spec field — execution budgeting is routing's job, not a shot field",
    "provenance":
        "recorded in the plan + apply event, never in the shot YAML",
    "review_notes":
        "recorded in the plan + apply event, never in the shot YAML",
    "source_facts.source_spans":
        "provenance only — kept in the plan/events, never in the shot YAML (schema unchanged)",
}


class ShotPackageError(ValueError):
    """A ShotDraftPackage is structurally invalid, unsafe, or unsupported.

    Raised (never a silent skip) for: over the size cap, a secret-shaped token
    anywhere in the raw text, an absolute/traversal path, an unknown schema
    MAJOR, a missing required field, an unsafe id, or duplicate draft/proposed
    ids. Inspect and apply both raise it BEFORE producing a plan or writing
    anything, so a rejected package never reaches the shot tree or the ledger.
    """


# --------------------------------------------------------------------- loading


def load_package(path) -> dict[str, Any]:
    """Read + security-screen a package FILE, returning the parsed mapping.

    Enforces the size cap BEFORE parse, scans the RAW text for secrets
    (``core.check.SECRET_PATTERNS`` — a package carrying an Authorization/API
    key is rejected), safe-loads the YAML, and checks the schema MAJOR. Raises
    :class:`ShotPackageError` on any failure. The heavier structural validation
    (required fields, ids, paths, duplicates) runs in
    :func:`build_shot_import_plan`, so a direct-dict caller is screened too.
    """
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        raise ShotPackageError(f"package file not found: {p}")
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise ShotPackageError(f"cannot stat package file: {exc}") from exc
    if size > MAX_PACKAGE_BYTES:
        raise ShotPackageError(
            f"package is {size} bytes — over the {MAX_PACKAGE_BYTES}-byte cap; refusing to parse")
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ShotPackageError(f"cannot read package file as UTF-8: {exc}") from exc

    _scan_secrets(text)  # over the RAW text — before we trust anything in it

    import yaml
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ShotPackageError(f"package YAML did not parse: {' '.join(str(exc).split())}") from exc
    if not isinstance(data, dict):
        raise ShotPackageError("package must be a YAML mapping at the top level")
    _require_schema_major(data)
    return data


# ------------------------------------------------------------------ validation


def _scan_secrets(text: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise ShotPackageError(
                "package rejected: it contains a token shaped like an API key/secret — "
                "keys must live in env vars, never enter the project (§8.2). Strip it and re-export")


def _require_schema_major(package: dict[str, Any]) -> None:
    schema = package.get("schema")
    if not isinstance(schema, str) or not schema.startswith(PACKAGE_SCHEMA_PREFIX):
        raise ShotPackageError(
            f"unknown package schema {schema!r} — expected {PACKAGE_SCHEMA_PREFIX}vN")
    match = re.match(r"^v(\d+)", schema[len(PACKAGE_SCHEMA_PREFIX):])
    if not match:
        raise ShotPackageError(f"unparseable schema version in {schema!r}")
    if int(match.group(1)) != PACKAGE_SCHEMA_MAJOR:
        raise ShotPackageError(
            f"unsupported package schema major: {schema!r} — this build imports "
            f"{PACKAGE_SCHEMA} (major {PACKAGE_SCHEMA_MAJOR}) only")


def _is_unsafe_path(value: Any) -> bool:
    """True for an absolute path, a Windows drive path, a NUL byte, any ``..``
    traversal segment — or (W1 §3.2) any Windows-lexical hazard: reserved
    device names (``CON.wav``), NTFS ADS colons (``x.wav:stream``), trailing
    dot/space segments. A path_hint is NEVER resolved or followed — an unsafe
    one is rejected outright, so a symlink can never be used to escape, and a
    downloaded package can never smuggle an unopenable-on-Windows name into a
    clean project. Delegates to the ONE lexical owner, ``core.idents``."""
    if not isinstance(value, str) or not value:
        return False
    return bool(windows_relpath_problems(value))


def _reject_unsafe_paths(node: Any) -> None:
    """Recursively reject any path-like field (a key containing ``path``) whose
    value is absolute or contains a traversal segment."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and "path" in key.lower() and _is_unsafe_path(value):
                raise ShotPackageError(
                    f"package rejected: field {key!r}={value!r} is an absolute/traversal path — "
                    "path fields must be project-relative (no '..', no leading '/')")
            _reject_unsafe_paths(value)
    elif isinstance(node, list):
        for item in node:
            _reject_unsafe_paths(item)


def _validate_package(package: dict[str, Any]) -> None:
    """Full structural + safety validation. Raises :class:`ShotPackageError`.

    Runs on every entry (inspect and apply), so a direct-dict caller is screened
    identically to the CLI's file path."""
    if not isinstance(package, dict):
        raise ShotPackageError("package must be a mapping")
    _require_schema_major(package)
    _scan_secrets(canonical_json(package))  # defense-in-depth over the serialized body
    _reject_unsafe_paths(package)

    if not isinstance(package.get("package_id"), str) or not package["package_id"].strip():
        raise ShotPackageError("package.package_id is required (a non-empty string)")
    producer = package.get("producer")
    if not isinstance(producer, dict) or not producer.get("kind") or not producer.get("name"):
        raise ShotPackageError("package.producer.kind and package.producer.name are required")
    source = package.get("source")
    if not isinstance(source, dict) or not source.get("source_revision"):
        raise ShotPackageError("package.source.source_revision is required")
    shots = package.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ShotPackageError("package.shots must be a non-empty list")

    draft_ids: list[str] = []
    proposed_ids: list[str] = []
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise ShotPackageError(f"package.shots[{i}] must be a mapping")
        draft_id = shot.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            raise ShotPackageError(f"package.shots[{i}].draft_id is required (a non-empty string)")
        _safe_id(draft_id, "draft_id")
        draft_ids.append(draft_id)
        proposed = shot.get("proposed_shot_id")
        if proposed is not None:
            if not isinstance(proposed, str) or not proposed:
                raise ShotPackageError(
                    f"package.shots[{i}].proposed_shot_id must be a non-empty string when set")
            _safe_id(proposed, "proposed_shot_id")
            proposed_ids.append(proposed)
    if len(set(draft_ids)) != len(draft_ids):
        raise ShotPackageError("duplicate draft_id(s) in package — draft ids must be unique")
    if len(set(proposed_ids)) != len(proposed_ids):
        raise ShotPackageError("duplicate proposed_shot_id(s) in package — proposed ids must be unique")


def _safe_id(value: str, label: str) -> str:
    try:
        return validate_safe_segment(value, label=label)
    except UnsafeIdentifierError as exc:
        raise ShotPackageError(str(exc)) from exc


# ----------------------------------------------------------------- digest/state


def semantic_digest(package: dict[str, Any]) -> str:
    """``sha256:`` over a canonical payload EXCLUDING ``created_at`` and
    ``package_id`` (reuses ``core.hashing.hash_value`` → recursively sorted
    keys), so the digest is independent of YAML key order and of those two
    envelope fields. Two packages with the same creative content share a
    digest — the basis of re-apply idempotency (an already-applied digest is a
    conflict)."""
    payload = {k: v for k, v in package.items() if k not in ("created_at", "package_id")}
    return hash_value(payload)


def project_revision(project: "Project") -> str:
    """A ``sha256:`` current-state token: ``hash_value`` over the visible shot-id
    list plus the exact ``shots/index.yaml`` text. Cheap, order-sensitive to the
    index (the order authority), and stable across reads. The plan echoes it;
    a package whose ``source.source_revision`` does not match it is warned as
    possibly stale."""
    index_path = project.shots_dir / "index.yaml"
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    payload: dict[str, Any] = {"shot_ids": project.shot_ids(), "index": index_text}
    scene_sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(project.scene_contracts_dir.glob("*.yaml"))
        if path.is_file()
    }
    if scene_sources:
        payload["scene_contracts"] = scene_sources
    return hash_value(payload)


# -------------------------------------------------------------------- mapping


def _known_capabilities() -> set[str]:
    """The capabilities a suggestion may map into generation.fallback — the keys
    of providers/registry.py's fallback map (the steps with a real adapter)."""
    try:
        from ..providers.registry import _FALLBACK_MAP
        return set(_FALLBACK_MAP.keys())
    except Exception:  # pragma: no cover — registry import must never break inspect
        from ..core.models import FALLBACK_STEPS
        return set(FALLBACK_STEPS)


def _snap(duration_ms: int, fps: int) -> int:
    from ..timeline.compiler import snap_to_frame_grid
    return snap_to_frame_grid(duration_ms, fps)


def _present(shot: dict[str, Any], dotted: str) -> bool:
    node: Any = shot
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return node is not None


def _map_one(shot: dict[str, Any], target_id: str, bible: set[str], fps: int) -> dict[str, Any]:
    """Map ONE package shot into a would-be :class:`ShotSpec` plus the plan
    bookkeeping (mapped ``fields``, ``omitted`` suggestions, ``unresolved``
    bible refs, ``warnings``). Pure — same inputs give the same spec, so the
    plan's ``proposed_text_hash`` equals exactly what apply will write."""
    facts = shot.get("source_facts") or {}
    sugg = shot.get("creative_suggestions") or {}
    execc = shot.get("execution_constraints") or {}

    spec_data: dict[str, Any] = {"id": target_id}
    fields: dict[str, Any] = {}
    omitted: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    warnings: list[str] = []

    # scene_ref -> shot.scene (bible check; a miss is UNRESOLVED, never created)
    scene = facts.get("scene_ref")
    if isinstance(scene, str) and scene:
        spec_data["scene"] = scene
        fields["scene"] = scene
        if scene not in bible:
            unresolved.append({"field": "scene", "ref": scene})

    # character_refs -> shot.characters (bible check each)
    chars = facts.get("character_refs")
    if isinstance(chars, list):
        clean = [c for c in chars if isinstance(c, str) and c]
        if clean:
            spec_data["characters"] = clean
            fields["characters"] = clean
            for c in clean:
                if c not in bible:
                    unresolved.append({"field": "characters", "ref": c})

    # action -> shot.action.main
    action = sugg.get("action")
    if isinstance(action, str) and action.strip():
        spec_data["action"] = {"main": action}
        fields["action"] = {"main": action}

    # camera{shot_size,movement,angle} -> shot.camera (known model fields only)
    cam = sugg.get("camera")
    if isinstance(cam, dict):
        cam_map: dict[str, str] = {}
        for key, val in cam.items():
            if key not in _CAMERA_FIELDS:
                omitted.append({"field": f"creative_suggestions.camera.{key}",
                                "reason": f"unknown camera field {key!r} (not in core/models.py "
                                          "Camera) — dropped"})
                continue
            if key == "shot_size" and val not in SHOT_SIZES:
                omitted.append({"field": "creative_suggestions.camera.shot_size",
                                "reason": f"shot_size {val!r} not in {SHOT_SIZES} — dropped "
                                          "(the author can set a valid one)"})
                continue
            if isinstance(val, str) and val:
                cam_map[key] = val
        if cam_map:
            spec_data["camera"] = cam_map
            fields["camera"] = cam_map

    # duration_ms -> shot.duration (PLAIN editable seconds; the compiler
    # frame-snaps at build — this is NOT a lock).
    dms = sugg.get("duration_ms")
    if isinstance(dms, (int, float)) and not isinstance(dms, bool) and dms > 0:
        seconds = round(float(dms) / 1000.0, 6)
        spec_data["duration"] = seconds
        fields["duration"] = seconds
        ms = int(round(dms))
        snapped = _snap(ms, fps)
        if snapped != ms:
            warnings.append(
                f"duration_ms {ms} would frame-snap to {snapped}ms at {fps}fps at build — the "
                f"shot stores {seconds}s unchanged (a plain editable duration, never a lock)")
    elif dms is not None:
        omitted.append({"field": "creative_suggestions.duration_ms",
                        "reason": f"duration_ms {dms!r} is not a positive number — dropped"})

    # execution_constraints.capability -> generation.fallback (only if KNOWN)
    cap = execc.get("capability")
    if isinstance(cap, str) and cap:
        if cap in _known_capabilities():
            spec_data["generation"] = {"fallback": [cap]}
            fields["generation"] = {"fallback": [cap]}
        else:
            omitted.append({"field": "execution_constraints.capability",
                            "reason": f"capability {cap!r} is not a known fallback capability in "
                                      "providers/registry.py — dropped (routing owns capability choice)"})

    # ALWAYS-omitted forbidden fields that are present in THIS shot
    for field_path, reason in _FORBIDDEN_REASONS.items():
        if _present(shot, field_path):
            omitted.append({"field": field_path, "reason": reason})

    spec = ShotSpec.model_validate(spec_data)
    return {"spec": spec, "fields": fields, "omitted": omitted,
            "unresolved": unresolved, "warnings": warnings}


# --------------------------------------------------------------------- planning


def _prior_apply(project: "Project", digest: str) -> dict[str, Any] | None:
    """The most-recent ``shot_package_apply`` event for this exact package
    digest, or ``None`` — the basis of re-apply idempotency (reads only)."""
    path = project.root / "events.jsonl"
    if not path.exists():
        return None
    found: dict[str, Any] | None = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            detail = rec.get("detail") or {}
            if rec.get("action") == "shot_package_apply" and detail.get("package_digest") == digest:
                found = {"ts": rec.get("ts"), "created": detail.get("created")}
    except OSError:
        return None
    return found


def _max_s_number(ids: set[str]) -> int:
    best = 0
    for sid in ids:
        m = re.match(r"^S(\d+)$", sid)
        if m:
            best = max(best, int(m.group(1)))
    return best


def _conflict_op(op_id: int, draft_id: str, target_id: str, mapped: dict[str, Any],
                 *, reason: str) -> dict[str, Any]:
    return {
        "op_id": op_id,
        "kind": "conflict",
        "draft_id": draft_id,
        "target_path": f"shots/{target_id}.yaml",
        "expected_current_hash": None,
        "proposed_text_hash": None,
        "fields": mapped["fields"],
        "omitted_suggestions": mapped["omitted"],
        "unresolved_refs": mapped["unresolved"],
        "reason": reason,
        "warnings": mapped["warnings"],
    }


def _plan_and_specs(project: "Project", package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, ShotSpec]]:
    """The single planning engine behind both public functions. Returns the
    JSON-serializable plan dict AND the ShotSpec objects apply will save (keyed
    by allocated shot id). ZERO writes."""
    _validate_package(package)

    warnings: list[str] = []
    for key in package:
        if key not in _KNOWN_TOP_KEYS:
            warnings.append(
                f"unknown package field {key!r} preserved but not interpreted (schema minor ahead)")

    digest = semantic_digest(package)
    proj_rev = project_revision(project)

    src_rev = (package.get("source") or {}).get("source_revision")
    if isinstance(src_rev, str) and src_rev and src_rev != proj_rev:
        warnings.append(
            f"stale: package source_revision {src_rev!r} does not match the project's current "
            f"revision {proj_rev!r} — the package may have been generated against an older project "
            "state; re-confirm it is current before --apply")

    prior = _prior_apply(project, digest)
    if prior is not None:
        warnings.append(
            f"this exact package (digest {digest[:19]}…) was already applied at {prior.get('ts')} "
            f"(created {prior.get('created')}) — re-apply is a no-op")

    bible = set(project.load_bible().keys())
    fps = int(project.load_config().fps)
    existing = set(project.shot_ids())

    shots = package["shots"]
    explicit = {s["proposed_shot_id"] for s in shots if s.get("proposed_shot_id")}
    taken = set(existing) | explicit
    counter = _max_s_number(taken)

    operations: list[dict[str, Any]] = []
    specs: dict[str, ShotSpec] = {}
    created_order: list[str] = []
    op_id = 0
    n_create = n_conflict = n_unresolved = 0

    for shot in shots:
        op_id += 1
        draft_id = shot["draft_id"]
        for key in shot:
            if key not in _KNOWN_SHOT_KEYS:
                warnings.append(
                    f"shots[{draft_id}]: unknown field {key!r} preserved but not interpreted")

        proposed = shot.get("proposed_shot_id")
        if proposed:
            target_id = proposed
        else:
            counter += 1
            while f"S{counter:03d}" in taken:
                counter += 1
            target_id = f"S{counter:03d}"
            taken.add(target_id)

        mapped = _map_one(shot, target_id, bible, fps)
        for w in mapped["warnings"]:
            warnings.append(f"shots[{draft_id}→{target_id}]: {w}")

        if prior is not None:
            operations.append(_conflict_op(
                op_id, draft_id, target_id, mapped,
                reason=(f"package already applied at {prior.get('ts')} (created "
                        f"{prior.get('created')}) — re-import is a no-op; change existing shots "
                        "via `manju propose`")))
            n_conflict += 1
            continue

        if target_id in existing:
            operations.append(_conflict_op(
                op_id, draft_id, target_id, mapped,
                reason=(f"target shot {target_id!r} already exists — a package never overwrites an "
                        "existing shot; change it via `manju propose`")))
            n_conflict += 1
            continue

        spec = mapped["spec"]
        proposed_text = dump_yaml(spec.model_dump(exclude_none=True))
        operations.append({
            "op_id": op_id,
            "kind": "create_shot",
            "draft_id": draft_id,
            "target_path": f"shots/{target_id}.yaml",
            "expected_current_hash": None,
            "proposed_text_hash": hash_text(proposed_text),
            "fields": mapped["fields"],
            "omitted_suggestions": mapped["omitted"],
            "unresolved_refs": mapped["unresolved"],
            "warnings": mapped["warnings"],
        })
        specs[target_id] = spec
        created_order.append(target_id)
        n_create += 1
        n_unresolved += len(mapped["unresolved"])

    index_path = project.shots_dir / "index.yaml"
    index_existed = index_path.exists()
    cur_index_hash = hash_text(index_path.read_text(encoding="utf-8")) if index_existed else None
    if created_order:
        op_id += 1
        idx = project.load_index()
        new_order = list(idx.order) + [s for s in created_order if s not in idx.order]
        idx.order = new_order
        proposed_index_text = dump_yaml(idx.model_dump())
        operations.append({
            "op_id": op_id,
            "kind": "update_index",
            "draft_id": None,
            "target_path": "shots/index.yaml",
            "expected_current_hash": cur_index_hash,
            "proposed_text_hash": hash_text(proposed_index_text),
            "fields": {"appends": list(created_order)},
            "omitted_suggestions": [],
            "unresolved_refs": [],
            "warnings": [],
        })

    safe = n_conflict == 0 and n_unresolved == 0 and prior is None
    plan = {
        "schema": PLAN_SCHEMA,
        "package_id": package.get("package_id"),
        "package_digest": digest,
        "project_revision": proj_rev,
        "operations": operations,
        "summary": {"create": n_create, "update": 0,
                    "conflicts": n_conflict, "unresolved_refs": n_unresolved},
        "safe_to_apply": bool(safe),
        "warnings": warnings,
    }
    if prior is not None:
        # surfaced as a first-class plan field (not only a warning) so the
        # apply refusal can name the real cause — without this, a package with
        # EMPTY proposed ids would re-allocate fresh S### ids on re-apply and
        # duplicate every shot; the digest match is the only guard there.
        plan["prior_apply"] = prior
    return plan, specs


def build_shot_import_plan(project: "Project", package: dict[str, Any]) -> dict[str, Any]:
    """Inspect a package against the project — the ZERO-WRITE plan.

    Validates the whole contract (schema/digest, dup ids, path safety, secret
    scan, bible refs, duration-vs-frame-grid warning, existing-id conflicts,
    current index hash, stale source_revision, structural absence of
    selected_take/media), allocates the next free ``S###`` for empty
    ``proposed_shot_id``s, and returns a ``manju.shot-import-plan/v1`` dict.
    ``safe_to_apply`` is true iff there are no conflicts, no unresolved refs,
    and the package was not already applied. Raises :class:`ShotPackageError`
    on a structural/safety rejection. Writes nothing."""
    return _plan_and_specs(project, package)[0]


# ----------------------------------------------------------------------- apply


class _ApplyError(RuntimeError):
    """Internal — a mid-write abort that triggers the compensating rollback."""


def _find_index_op(plan: dict[str, Any]) -> dict[str, Any] | None:
    return next((op for op in plan["operations"] if op["kind"] == "update_index"), None)


def _create_signature(plan: dict[str, Any]) -> tuple:
    """A stable fingerprint of the WRITE operations (create targets + their
    exact proposed text, and the index op's before/after hashes) — apply refuses
    if the recomputed plan's signature differs from the reviewed plan's."""
    return tuple(sorted(
        (op["target_path"], op.get("expected_current_hash"), op["proposed_text_hash"])
        for op in plan["operations"] if op["kind"] in ("create_shot", "update_index")
    ))


def _unsafe_reasons(plan: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    prior = plan.get("prior_apply")
    if prior:
        reasons.append(
            f"this exact package was already applied at {prior.get('ts')} "
            f"(created {prior.get('created')}) — re-apply would duplicate its shots; "
            "change the package content if you really mean a second import")
    for op in plan["operations"]:
        if op["kind"] == "conflict":
            reasons.append(op.get("reason", f"conflict at {op['target_path']}"))
        for u in op.get("unresolved_refs") or []:
            reasons.append(
                f"unresolved {u['field']} ref {u['ref']!r} at {op['target_path']} — add it to the "
                "bible, then re-inspect")
    return reasons or ["plan is not safe to apply"]


def _rollback(project: "Project", created_paths: list, index_path,
              index_existed: bool, original_index_text: str | None) -> None:
    """Compensating rollback: delete every created shot file (reverse order),
    then restore the index bytes verbatim (or delete a freshly-created index).
    Leaves no half-state."""
    for path in reversed(created_paths):
        try:
            path.unlink()
        except OSError:
            pass
    if index_existed and original_index_text is not None:
        atomic_write_text(index_path, original_index_text)
    else:
        try:
            index_path.unlink()
        except OSError:
            pass


def apply_shot_import_plan(project: "Project", package: dict[str, Any], *,
                           actor: str = "human", plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply a package under one ``build_lock`` — the ONLY writer.

    Steps: (1) recompute the plan NOW and refuse if not ``safe_to_apply``;
    (2) CAS re-verify against the reviewed ``plan`` (when given) — the index
    file must be unchanged since it, the write signature identical, and every
    target shot file still absent — any mismatch is a structured refusal with
    zero writes; (3) write the new shot YAMLs via ``Project.save_shot`` then the
    index via ``save_index``, staged with a compensating rollback; (4) run
    ``manju check`` and, on any NEW error attributable to this batch, roll the
    batch back and return the check evidence; (5) ``append_event`` a
    prompt-free, secret-free ``shot_package_apply`` record. Never touches git,
    media, ``selected_take`` or locks.

    ``plan`` (optional) is the reviewed plan whose file hashes anchor the CAS
    check — pass what inspect showed the human, so a project that moved since is
    refused instead of silently applied. Omit it to recompute-and-apply."""
    from ..runtime.buildlock import build_lock

    with build_lock(project.root, actor=actor):
        current, specs = _plan_and_specs(project, package)
        anchor = plan if plan is not None else current

        if not current["safe_to_apply"]:
            return {"ok": False, "code": "not_safe_to_apply",
                    "reasons": _unsafe_reasons(current), "plan": current}

        # ---- (2) CAS re-verify against the reviewed plan
        index_path = project.shots_dir / "index.yaml"
        index_existed = index_path.exists()
        original_index_text = index_path.read_text(encoding="utf-8") if index_existed else None
        cur_index_hash = hash_text(original_index_text) if index_existed else None

        mismatches: list[str] = []
        anchor_index = _find_index_op(anchor)
        if anchor_index is not None and anchor_index.get("expected_current_hash") != cur_index_hash:
            mismatches.append("shots/index.yaml changed since the reviewed plan (CAS)")
        if _create_signature(current) != _create_signature(anchor):
            mismatches.append("the planned operations changed since the reviewed plan (CAS)")
        for op in current["operations"]:
            if op["kind"] == "create_shot":
                sid = _target_id(op)
                if project.shot_path(sid).exists():
                    mismatches.append(f"{op['target_path']} already exists (CAS)")
        if mismatches:
            return {"ok": False, "code": "cas_mismatch", "reasons": mismatches, "plan": current}

        # baseline check BEFORE any write, to isolate errors this batch introduces
        before_errors = set(run_check(project).errors)

        # ---- (3) staged write with compensating rollback
        created_paths: list = []
        created_ids: list[str] = []
        try:
            for op in current["operations"]:
                if op["kind"] != "create_shot":
                    continue
                sid = _target_id(op)
                spec = specs[sid]
                would_write = dump_yaml(spec.model_dump(exclude_none=True))
                if hash_text(would_write) != op["proposed_text_hash"]:
                    raise _ApplyError(f"proposed-text drift for {sid} — refusing")
                project.save_shot(spec)
                created_paths.append(project.shot_path(sid))
                created_ids.append(sid)
            idx = project.load_index()
            for sid in created_ids:
                if sid not in idx.order:
                    idx.order.append(sid)
            project.save_index(idx)
        except Exception as exc:
            _rollback(project, created_paths, index_path, index_existed, original_index_text)
            return {"ok": False, "code": "apply_failed",
                    "error": " ".join(str(exc).split())[:400], "plan": current}

        # ---- (4) post-apply check; roll back on any NEW error
        after = run_check(project)
        new_errors = [e for e in after.errors if e not in before_errors]
        if new_errors:
            _rollback(project, created_paths, index_path, index_existed, original_index_text)
            return {"ok": False, "code": "check_failed", "new_errors": new_errors,
                    "check": after.to_dict(), "plan": current}

        # ---- (5) event — ids + digest ONLY (no prompts, no secrets, no body)
        op_ids = [op["op_id"] for op in current["operations"]]
        append_event(project.root, actor, "shot_package_apply", {
            "package_id": package.get("package_id"),
            "package_digest": current["package_digest"],
            "op_ids": op_ids,
            "created": list(created_ids),
        })
        return {"ok": True, "created": list(created_ids), "op_ids": op_ids,
                "package_digest": current["package_digest"],
                "check": after.to_dict(), "plan": current}


def _target_id(op: dict[str, Any]) -> str:
    return op["target_path"].split("/", 1)[-1].removesuffix(".yaml")
